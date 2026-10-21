"""Gate 6 R3 E2E-D: one GENERATE served by the real mesh over AXI_MESH Garnet.

Runs the agent side (driver + serving frontend with the serving_mesh executor)
and the mesh side (cores, DMA engines, loader, dispatcher) on one Ruby fabric
so the Host observes the same backing the mesh writes.  The dispatcher is
selector-driven per phase and gated by the executor's KV join.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import m5
from m5.objects import (
    AddrRange,
    AgentAxiDriver,
    AxiGarnetBridge,
    AxiTensorDmaEngine,
    Gate3ObservationRecorder,
    MeshDispatcher,
    MeshDummyCore,
    MeshExperimentObserver,
    MeshProgramLoader,
    NpuMemoryEndpoint,
    NpuServingFrontend,
    PeerSramAperture,
    Root,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util import addToPath, fatal

addToPath("../../")

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import Options  # noqa: E402
from ruby import Ruby  # noqa: E402

from mesh_ir.runtime_deployment import ServingDeployment
from mesh_ir.runtime_fabric import core_parameters, fabric_option_defaults, network_parameters, target_parameters
from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.architecture import load_arch  # noqa: E402
from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as mesh_abi  # noqa: E402
from mesh_ir.agent_config import (  # noqa: E402
    build_capacity_plan,
    load_agent_runtime_config,
    release_policy_of,
    validate_runtime_config,
)
from mesh_ir.agent_plan_image import build_agent_plan_image  # noqa: E402
from mesh_ir.agent_planning import (  # noqa: E402
    build_command_identity_plan,
    build_host_arena_object_plan,
    build_host_task_identity_plan,
)
from mesh_ir.agent_surrogate import (  # noqa: E402
    load_surrogate_profiles,
    validate_surrogate_profiles,
)
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.gate4_oracle import arena_regions  # noqa: E402
from mesh_ir.weight_runtime import RuntimeWeightImage
from mesh_ir.host_bindings import build_host_binding_plans  # noqa: E402
from mesh_ir.serving_profiles import CAPACITY_FIELDS, ServingCapacity, serving_capacity_plan
from mesh_ir.effective import (  # noqa: E402
    EffectiveArchitecture,
    apply_cli_dma_overrides,
)
from gate4_runtime import config_hardware, configure_remote_host_target

ARCH_ARGUMENTS = argparse.ArgumentParser(add_help=False)
ARCH_ARGUMENTS.add_argument("--arch", type=Path,
    default=Path(__file__).resolve().parent / "arch/mesh_1x2.yaml")
ARCH_YAML = ARCH_ARGUMENTS.parse_known_args()[0].arch
ARCH_MANIFEST = load_arch(ARCH_YAML)
EFFECTIVE_ARCH = EffectiveArchitecture(ARCH_MANIFEST)

WATCHDOG_CYCLES = 2000000000


def _region(kind):
    for region in ARCH_MANIFEST.regions:
        if region.kind == kind:
            return region
    fatal("architecture has no %s region", kind)


SRAM_BASE = _region("CORE_SRAM_APERTURE").base
SRAM_STRIDE = _region("CORE_SRAM_APERTURE").tile_stride
SRAM_TILE = _region("CORE_SRAM_APERTURE").tile_bytes


def build_arguments():
    parser = argparse.ArgumentParser(parents=[ARCH_ARGUMENTS])
    Options.addNoISAOptions(parser)
    Ruby.define_options(parser)
    parser.set_defaults(
        network="garnet",
        topology="AxiMeshDie",
        routing_algorithm=1,
        mesh_rows=ARCH_MANIFEST.fabric.network.router_rows,
        mem_type="DDR3_1600_8x8",
        axi_mesh_routers=ARCH_MANIFEST.fabric.network.router_rows * ARCH_MANIFEST.fabric.network.router_cols,
        **fabric_option_defaults(ARCH_MANIFEST),
    )
    parser.add_argument("--serving-program", required=True)
    parser.add_argument("--model-invocation", action="append", type=Path, default=[])
    parser.add_argument("--memory-file-image", default="")
    parser.add_argument("--verify-memory-range", nargs=2, action="append", default=[],
                        type=lambda value: int(value, 0), metavar=("ADDRESS", "BYTES"))
    parser.add_argument("--memory-verify-bytes-limit", type=int, default=0)
    parser.add_argument("--compute-bytes-limit", type=int, default=0)
    parser.add_argument("--fabric-snapshots", action="store_true")
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--surrogate-profiles")
    parser.add_argument("--weight-image-digest", required=True)
    parser.add_argument("--model-weight-image")
    parser.add_argument("--program-weight-registry")
    parser.add_argument("--cache-state-replay")
    parser.add_argument("--weight-policy", choices=("streamed", "cached"), default="streamed")
    parser.add_argument("--moe-provider", choices=("uniform_smoke", "route_replay", "histogram_replay"), default="uniform_smoke")
    parser.add_argument("--moe-provider-artifact")
    parser.add_argument("--agent-only", action="store_true")
    parser.add_argument("--executor", default="serving_mesh",
                        choices=("serving_mesh", "full_context_surrogate"))
    parser.add_argument("--sim-tick-limit", type=int, default=20000000)
    parser.add_argument("--watchdog-ticks", type=int, default=0)
    parser.add_argument("--residency-fault", choices=("", "invalidate_compute_input"), default="")
    parser.add_argument("--checkpoint-dir")
    parser.add_argument("--checkpoint-at-tick", type=int)
    parser.add_argument("--restore-checkpoint")
    parser.add_argument("--quota-write-contexts", type=int, default=1)
    parser.add_argument("--quota-write-beats", type=int, default=128)
    parser.add_argument("--quota-read-contexts", type=int, default=1)
    parser.add_argument("--quota-read-beats", type=int, default=128)
    parser.add_argument("--read-outstanding", type=int, default=None)
    parser.add_argument("--write-outstanding", type=int, default=None)
    parser.add_argument("--segment-queue-depth", type=int, default=None)
    parser.add_argument("--dma-descriptor-queue-depth", type=int, default=None)
    parser.add_argument("--sram-write-bytes-per-cycle-per-bank", type=int,
                        default=None)
    parser.add_argument("--serving-data-region-base", type=lambda value: int(
        value, 0), default=None)
    parser.add_argument("--kv-region-base", type=lambda value: int(value, 0),
                        default=None)
    parser.add_argument("--kv-session-slot-bytes", type=int, default=4096)
    parser.add_argument("--kv-max-sessions", type=int, default=64)
    return parser.parse_args()


def published_output_span(program, region_base):
    profile = program.profile_stream_ranges[-1].profile_id
    command_ids = build_execution_views(program).for_profile(profile).command_ids
    stores = [descriptor for descriptor in program.dma_descriptors
              if descriptor.kind == mesh_abi.DMA_KIND.STORE and
              descriptor.command_id in command_ids]
    if not stores:
        fatal("serving program publishes no output store")
    base = region_base + min(descriptor.dst.offset_bytes
                             for descriptor in stores)
    return base, sum(descriptor.useful_bytes for descriptor in stores)


def artifact_directory():
    directory = Path(
        os.environ.get("AI_MESH_ARTIFACT_DIR", m5.options.outdir)
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def main():
    args = build_arguments()
    if args.memory_verify_bytes_limit < 0 or args.compute_bytes_limit < 0:
        fatal("observation byte limits must be nonnegative")
    if args.agent_only and args.verify_memory_range:
        fatal("memory observation requires serving execution")
    apply_cli_dma_overrides(EFFECTIVE_ARCH, args)
    if args.sram_write_bytes_per_cycle_per_bank is not None:
        EFFECTIVE_ARCH.override(
            "sram_write_bytes_per_cycle_per_bank",
            args.sram_write_bytes_per_cycle_per_bank)
    arch_manifest = EFFECTIVE_ARCH
    clock = "%dHz" % arch_manifest.clock_hz
    id_pool = 1 << arch_manifest.axi_id_bits
    core_ids = list(arch_manifest.core_ids)

    program_path = Path(args.serving_program).resolve()
    if not args.agent_only and \
            arch_manifest.axi_data_bytes != args.axi_data_width_bits // 8:
        fatal("arch data bytes != configured AXI width")
    runtime_config_path = Path(args.runtime_config).resolve()
    runtime_config = load_agent_runtime_config(runtime_config_path)
    runtime = runtime_config.document["runtime"]
    windowed = runtime["mode"] == "WINDOWED_TIMING"
    from mesh_ir.serving_timeline import ServingTimeline
    timeline = ServingTimeline(runtime["mode"], runtime.get("windows") if windowed else None)
    if windowed:
        if args.agent_only:
            fatal("WINDOWED_TIMING requires serving execution")
    fast_event = runtime["mode"] == "FAST_EVENT"
    if args.fabric_snapshots and (args.agent_only or fast_event or windowed):
        fatal("fabric snapshots require FULL_TIMING serving execution")
    args.network = "simple" if fast_event else "garnet"
    if fast_event or windowed:
        if args.simple_physical_channels:
            fatal("FAST_EVENT requires bandwidth shared by all AXI virtual networks")
        args.simple_buffer_size = runtime["fast_event"]["queue_entries"]
        args.simple_bytes_per_cycle = args.link_width_bits // 8
    hardware = config_hardware(runtime_config.document)
    npu_control_base = hardware["npu_control_base"]
    agent_proxy_control_base = hardware["agent_proxy_control_base"]
    host_base = hardware["host_base"]

    artifact_dir = artifact_directory()
    (artifact_dir / "simulation_mode.json").write_text(json.dumps({
        "mode": runtime["mode"], "network": args.network,
        "aggregation": "PER_WINDOW" if windowed else "AXI_MESSAGE" if fast_event else "FLIT",
        "queue_entries": args.simple_buffer_size if fast_event or windowed else None,
        "link_bytes_per_cycle": args.link_width_bits // 8,
        "checkpoint_restore_supported": True,
        "drain_boundary": "PROGRAM",
        "live_window_switch": windowed,
    }, sort_keys=True, indent=2) + "\n")
    result_path = artifact_dir / "mesh_result.json"
    facts_path = artifact_dir / "gate6_facts.tsv"

    agent = runtime_config.document["agent"]
    if agent["mode"] != "replay_plan":
        fatal("Gate6 serving runtime only accepts agent.mode=replay_plan")
    workload = load_workload_plan(
        runtime_config_path.parent / agent["workload_plan"])
    control = None
    if agent["control_plan"] is not None:
        control = load_control_plan(
            runtime_config_path.parent / agent["control_plan"], workload)
    serving_program = decode_program(Path(args.serving_program).read_bytes())
    surrogate = load_surrogate_profiles(Path(args.surrogate_profiles)) if args.surrogate_profiles else None
    if surrogate is None and (args.executor != "serving_mesh" or any(row.flags for row in serving_program.agent_request_profiles)):
        fatal("E_REQUEST_PROFILE: generation and surrogate execution require surrogate profiles")
    validate_runtime_config(runtime_config, workload, surrogate)
    if surrogate is not None:
        validate_surrogate_profiles(surrogate, workload)
    policy = release_policy_of(runtime_config)
    regions = arena_regions(runtime_config.document["serving"]["address_map"])
    for region in regions:
        if region.kind == 'WORKSPACE' and not any(
                item.kind == 'HBM' and item.base <= region.base and
                region.base + region.bytes <= item.base + item.bytes for item in arch_manifest.regions):
            fatal('E_ADDRESS_PLAN: workspace arena must be covered by declared HBM')
    identity = build_command_identity_plan(workload, control, policy)
    host_tasks = build_host_task_identity_plan(workload)
    weight_image = None
    cache_replay = None
    if bool(args.model_weight_image) != bool(args.program_weight_registry):
        fatal("E_MOE_CACHE: weight image and registry must be supplied together")
    if args.cache_state_replay and not args.model_weight_image:
        fatal("E_MOE_CACHE: warm cache requires a weight image and registry")
    if args.model_weight_image:
        if args.agent_only or args.weight_policy != "cached":
            fatal("E_MOE_CACHE: weight installation requires the cached serving mesh")
        weight_image = RuntimeWeightImage.load(serving_program, EFFECTIVE_ARCH,
            args.model_weight_image, args.program_weight_registry, args.weight_image_digest, workload)
        if args.cache_state_replay:
            cache_replay = weight_image.load_replay(args.cache_state_replay)
    from mesh_ir.serving_batch_replay import load_batch_replay
    replay_path = runtime_config.document["serving"]["batch_replay_artifact"]
    if replay_path is not None:
        replay_path = runtime_config_path.parent / replay_path
        replay = load_batch_replay(replay_path, workload, serving_program)
        (artifact_dir / "batch_replay_digest.txt").write_text(replay.digest + "\n")
    provider = None
    if (args.moe_provider == "uniform_smoke") != (args.moe_provider_artifact is None):
        fatal("E_MOE_PROVIDER: replay providers require an immutable provider artifact")
    if args.moe_provider != "uniform_smoke":
        from mesh_ir.serving_moe_provider import ServingMoeProvider
        provider = ServingMoeProvider(serving_program, workload.digest, args.moe_provider,
                                      args.moe_provider_artifact, artifact_dir)

    binding_capacity = {name: runtime_config.document["serving"][name]
                        for name in CAPACITY_FIELDS}
    binding_capacity_plan = serving_capacity_plan(
        serving_program, ServingCapacity(**binding_capacity))
    (artifact_dir / "serving_capacity_plan.json").write_text(
        json.dumps(binding_capacity_plan, sort_keys=True, indent=2) + "\n")
    output_arena_base = runtime_config.document["serving"]["address_map"][
        "output_arena"]["base"]
    published = (output_arena_base,
                 published_output_span(serving_program,
                                       arch_manifest.regions[0].base)[1])
    kv_profile = next((row for row in serving_program.agent_request_profiles if row.flags), None)
    from mesh_ir.kv_layout import reference_kv_pool_base
    kv_tensor_base = args.kv_region_base
    if kv_profile is not None and kv_tensor_base is None:
        kv_tensor_base = reference_kv_pool_base(serving_program, [region.base for region in arch_manifest.regions])
    from mesh_ir.runtime_invocation import load_invocation
    model_invocations = tuple(load_invocation(path, serving_program, ARCH_MANIFEST) for path in args.model_invocation)
    if model_invocations and (args.executor != 'serving_mesh' or args.model_weight_image):
        fatal('E_RELOCATION: model invocations require streamed Serving execution')
    binding_plans = (build_host_binding_plans(
        serving_program, [region.base for region in arch_manifest.regions], invocations=model_invocations)
        if args.executor == "serving_mesh" else ())
    binding_counts = {(plan.program_id, plan.profile_id):
                      len(plan.requirements) for plan in binding_plans}
    arena = build_host_arena_object_plan(
        workload, control, policy, regions, binding_counts, binding_plans=binding_plans)

    capacity = build_capacity_plan(workload, control, runtime_config)
    image, _ = build_agent_plan_image(workload, control, policy, regions,
                                      surrogate, binding_plans,
                                      args.kv_session_slot_bytes)
    image_path = artifact_dir / "plan_image.bin"
    image_path.write_bytes(image)
    request_id_capacity = runtime_config.document["serving"]["host_issued_request_id_entries"]
    services = runtime_config.document["agent_axi_driver"][
        "synthetic_host_services"]
    accepted_queue_entries = capacity["counts"]["live_context_bound"]

    serving_deployment = ServingDeployment.from_arch(
        ARCH_MANIFEST, npu_control_base, agent_proxy_control_base,
        host_base, hardware["host_bytes"],
        control_quota=(args.quota_write_contexts, args.quota_write_beats,
                       args.quota_read_contexts, args.quota_read_beats))
    deployment = serving_deployment.fabric
    scenario = serving_deployment.scenario()
    scenario_path = artifact_dir / "gate6_scenario.json"
    scenario_path.write_text(json.dumps(scenario, sort_keys=True))
    args.axi_scenario = str(scenario_path)

    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=clock, voltage_domain=system.voltage_domain
    )
    args.axi_id_width_bits = arch_manifest.axi_id_bits
    args.axi_max_outstanding_reads = EFFECTIVE_ARCH.dma_read_outstanding
    args.axi_max_outstanding_writes = EFFECTIVE_ARCH.dma_write_outstanding
    Ruby.create_system(args, False, system, cpus=[])
    system.ruby.clk_domain = system.clk_domain
    ruby = system.ruby
    for name, value in network_parameters(ARCH_MANIFEST).items():
        setattr(ruby.network, name, value)
    for target in deployment.targets:
        adapter = getattr(ruby, f'axi_target_adapter{deployment.target_index(target.dst_node)}')
        for name, value in target_parameters(target).items():
            setattr(adapter, name, value)
    if windowed:
        ruby.network.setup_aggregate(runtime["fast_event"]["queue_entries"],
                                     runtime["windows"]["initial_mode"] == "FAST_EVENT")
    configure_remote_host_target(
        getattr(ruby, 'axi_target_adapter%d' % serving_deployment.host_target_index),
        runtime_config.document)

    recorder = Gate3ObservationRecorder(output_path=str(facts_path))
    if args.agent_only:
        cores = []
        apertures = []
        loader = None
        endpoints = []
        dispatcher = None
    system.gate3_recorder = recorder

    seed_path = artifact_dir / "hbm_seed.txt"
    seed_path.write_text("")
    if not args.agent_only:
        observed_ranges = deployment.memory_map.partition(args.verify_memory_range)
        endpoints = []
        for target in deployment.memory_targets:
            verify_path = artifact_dir / f"memory_verify_{target.dst_node}.txt"
            verify_path.write_text(''.join(f'{address:#x} {size}\n'
                for address, size in observed_ranges[target.dst_node]))
            endpoint = NpuMemoryEndpoint(
                adapter=getattr(ruby, f'axi_target_adapter{deployment.target_index(target.dst_node)}'),
                seed_json=str(seed_path), verify_json=str(verify_path),
                verify_bytes_limit=args.memory_verify_bytes_limit)
            endpoint.clk_domain = system.clk_domain
            setattr(system, f'mesh_endpoint_{target.dst_node}', endpoint)
            endpoints.append(endpoint)

        apertures = []
        for index, core_id in enumerate(core_ids):
            aperture = PeerSramAperture(
                adapter=getattr(
                    ruby,
                    "axi_target_adapter%d" %
                    deployment.peer_target_index(core_id),
                ),
                core_id=core_id,
                sram_base=SRAM_BASE + core_id * SRAM_STRIDE,
                sram_bytes=arch_manifest.sram_bytes,
            )
            aperture.clk_domain = system.clk_domain
            setattr(system, "mesh_aperture_%d" % index, aperture)
            apertures.append(aperture)

        cores = []
        for index, core_id in enumerate(core_ids):
            bridge = AxiGarnetBridge(
                adapter=getattr(
                    ruby,
                    "axi_initiator_adapter%d" %
                    deployment.core_initiator_index(core_id),
                ),
                data_bus_bytes=arch_manifest.axi_data_bytes,
                aw_queue_depth=arch_manifest.dma_segment_queue_depth,
                ar_queue_depth=arch_manifest.dma_segment_queue_depth,
                w_beats_per_cycle=arch_manifest.fabric.axi.w_beats_per_cycle,
            )
            bridge.clk_domain = system.clk_domain
            setattr(system, "mesh_bridge_%d" % core_id, bridge)
            engine = AxiTensorDmaEngine(
                bridge=bridge,
                data_bus_bytes=arch_manifest.axi_data_bytes,
                max_burst_beats=arch_manifest.axi_max_burst_beats,
                setup_cycles=arch_manifest.dma_setup_cycles,
                descriptor_queue_depth=arch_manifest.dma_descriptor_queue_depth,
                segment_queue_depth=arch_manifest.dma_segment_queue_depth,
                axi_id_count=id_pool,
            )
            engine.clk_domain = system.clk_domain
            core = MeshDummyCore(
                core_id=core_id,
                compute_bytes_limit=args.compute_bytes_limit,
                **core_parameters(arch_manifest),
                dma=engine,
            )
            core.clk_domain = system.clk_domain
            cores.append(core)
            setattr(system, "mesh_core_%d" % core_id, core)
            setattr(system, "mesh_engine_%d" % core_id, engine)

        loader = MeshProgramLoader(
            memory_file_image=args.memory_file_image,
            weight_runtime_image=json.dumps(weight_image.projection) if weight_image else "",
            weight_image_digest=args.weight_image_digest,
            cache_state_replay=json.dumps(cache_replay) if cache_replay else "",
            memory_endpoints=endpoints,
            strict_replay_serial_batches=runtime_config.document["serving"]["strict_replay_serial_batches"],
            moe_master_seed=runtime_config.document["runtime"]["master_seed"],
            **binding_capacity,
            program_file=str(program_path),
            cores=cores,
            apertures=apertures,
            arch_digest=EFFECTIVE_ARCH.base_digest().hex(),
            effective_arch_digest=EFFECTIVE_ARCH.digest().hex(),
            core_ids=core_ids,
            command_rom_entries=arch_manifest.command_rom_entries,
            sram_bytes=arch_manifest.sram_bytes,
            sram_banks=arch_manifest.sram_banks,
            sram_alignment=arch_manifest.sram_base_alignment_bytes,
            axi_data_bytes=arch_manifest.axi_data_bytes,
            axi_max_burst_beats=arch_manifest.axi_max_burst_beats,
            region_ids=list(range(len(arch_manifest.regions))),
            region_bases=[
                args.serving_data_region_base
                if index == 0 and args.serving_data_region_base is not None
                else region.base
                for index, region in enumerate(arch_manifest.regions)],
            region_bytes=[region.bytes for region in arch_manifest.regions],
            region_tile_strides=[
                region.tile_stride if region.tile_stride else 0
                for region in arch_manifest.regions],
            region_kinds=[
                {"HBM": 0, "HOST_SHARED": 1, "CORE_SRAM_APERTURE": 2}[region.kind]
                for region in arch_manifest.regions],
            region_tile_bytes=[
                region.tile_bytes if region.tile_bytes else 0
                for region in arch_manifest.regions],
            overlay_image="",
            weight_policy=args.weight_policy,
            sram_partition_kinds=[
                partition.kind for partition in arch_manifest.sram_partitions],
            sram_partition_bases=[
                partition.base for partition in arch_manifest.sram_partitions],
            sram_partition_bytes=[
                partition.bytes for partition in arch_manifest.sram_partitions],
            sram_partition_alignments=[
                partition.alignment
                for partition in arch_manifest.sram_partitions],
            sram_partition_metadata_entries=[
                partition.metadata_entries
                for partition in arch_manifest.sram_partitions],
            sram_partition_max_pinned=[
                partition.max_pinned_entries
                for partition in arch_manifest.sram_partitions],
            weight_cache_slot_bytes=arch_manifest.sram_weight_cache_slot_bytes,
        )
        system.mesh_loader = loader

        dispatcher = MeshDispatcher(
            batch_failure_record_entries=runtime_config.document["serving"]["batch_failure_record_entries"],
            loader=loader,
            cores=cores,
            apertures=apertures,
            output_span_base=published[0],
            output_span_bytes=published[1],
            network=ruby.network,
            result_json=str(result_path),
            autostart=False,
            watchdog_ticks=args.watchdog_ticks,
            residency_fault=args.residency_fault,
        )
        dispatcher.clk_domain = system.clk_domain
        system.mesh_dispatcher = dispatcher

    driver = AgentAxiDriver(
        network=ruby.network,
        master=getattr(ruby, "axi_initiator_adapter%d" %
                       serving_deployment.driver_index),
        target=getattr(ruby, "axi_target_adapter%d" %
                       serving_deployment.host_target_index),
        recorder=recorder,
        generated_code_span_base=output_arena_base,
        data_bus_bytes=arch_manifest.axi_data_bytes,
        control_bytes=8,
        max_burst_beats=256,
        profile="",
        request_count=1,
        local_visibility_delay=0,
        drain_cycles=8,
        stop_after_completed_tasks=0,
        stop_accepting_enabled=False,
        stop_accepting_at_tick=0,
        host_fault_site="",
        host_fault_task=0,
        host_fault_round=0,
        control_doorbell_error_ordinal=0,
        mutate_cancel_command_ordinal=0,
        mutate_cancel_command_status=0,
        control_doorbell_b_hold_ns=0,
        cq_read_delay_ns=0,
        metadata_read_delay_ns=0,
        request_source="replay_plan",
        plan_image=str(image_path),
        request_id_capacity=request_id_capacity,
        host_compute_tokens=services["compute_tokens"],
        host_release_waiter_entries=services["release_waiter_entries"],
        host_service_queue_depth=services["service_queue_depth"],
        host_available_fraction_q16=hardware["host_available_fraction_q16"],
        host_compile_slots=hardware["host_compile_slots"],
        host_test_slots=hardware["host_test_slots"],
        host_log_parse_slots=hardware["host_log_parse_slots"],
        host_weight_compile=hardware["host_weight_compile"],
        host_weight_test=hardware["host_weight_test"],
        host_weight_log_parse=hardware["host_weight_log_parse"],
        host_local_io_enabled=hardware["host_local_io_enabled"],
        host_local_io_fixed_ns=hardware["host_local_io_fixed_ns"],
        host_local_io_bytes_per_ns=hardware["host_local_io_bytes_per_ns"],
        agent_object_table_entries=hardware["agent_object_table_entries"],
        cancel_join_entries=hardware["cancel_join_entries"],
        sq_depth=hardware["sq_depth"],
        cq_depth=hardware["cq_depth"],
        host_base=host_base,
        sq_ring_base=hardware["sq_ring_base"],
        cq_ring_base=hardware["cq_ring_base"],
        msi_base=hardware["msi_base"],
        npu_control_base=npu_control_base,
        agent_proxy_control_base=agent_proxy_control_base,
        doorbell_axi_id=hardware["doorbell_axi_id"],
        ack_axi_id=hardware["ack_axi_id"],
    )
    agent_clock = SrcClockDomain(
        clock=hardware["clock"], voltage_domain=system.voltage_domain
    )
    driver.clk_domain = agent_clock
    system.gate6_driver = driver

    frontend = NpuServingFrontend(
        master=getattr(ruby, "axi_initiator_adapter%d" %
                       serving_deployment.frontend_index),
        control_target=getattr(ruby, "axi_target_adapter%d" %
                               serving_deployment.npu_control_target_index),
        recorder=recorder,
        driver=driver,
        data_bus_bytes=arch_manifest.axi_data_bytes,
        control_bytes=8,
        max_burst_beats=256,
        profile="",
        request_count=1,
        sq_read_issue_delay=0,
        cq_entry_axi_id=20,
        output_b_error_request=0,
        output_b_error_segment=0,
        control_cq_first=False,
        executor=args.executor,
        plan_image=str(image_path),
        accepted_queue_entries=accepted_queue_entries,
        seen_request_id_entries=runtime_config.document["serving"]["seen_request_id_entries"],
        kv_session_slot_bytes=args.kv_session_slot_bytes,
        kv_max_sessions=args.kv_max_sessions,
        kv_session_record_entries=hardware["kv_session_record_entries"],
        kv_session_tombstone_entries=hardware["kv_session_tombstone_entries"],
        kv_admission_wait_entries=runtime_config.document["serving"]["kv_admission_wait_entries"],
        kv_release_waiter_entries=runtime_config.document["serving"]["session_release_waiter_entries"],
        **({} if args.agent_only else {
            "serving_loader": loader,
            "serving_dispatcher": dispatcher,
            "serving_weight_image_digest": args.weight_image_digest,
        }),
        sq_depth=hardware["sq_depth"],
        cq_depth=hardware["cq_depth"],
        host_base=host_base,
        sq_ring_base=hardware["sq_ring_base"],
        cq_ring_base=hardware["cq_ring_base"],
        msi_base=hardware["msi_base"],
        npu_control_base=npu_control_base,
        agent_proxy_control_base=agent_proxy_control_base,
        doorbell_axi_id=hardware["doorbell_axi_id"],
        sq_head_axi_id=hardware["sq_head_axi_id"],
        cq_tail_axi_id=hardware["cq_tail_axi_id"],
        msi_axi_id=hardware["msi_axi_id"],
        msi_axi_id_count=hardware["msi_axi_id_count"],
    )
    if kv_profile is not None:
        frontend.kv_bytes_per_token = kv_profile.kv_bytes_per_token
    if kv_tensor_base is not None:
        frontend.kv_region_base = kv_tensor_base
    serving = runtime_config.document["serving"]
    frontend.batch_replay_artifact = str(replay_path) if replay_path else ""
    frontend.strict_replay_serial_batches = serving["strict_replay_serial_batches"]
    frontend.scheduler_max_active_sequences = serving["max_active_sequences"]
    frontend.scheduler_max_batch_tokens = serving["max_batch_tokens"]
    frontend.scheduler_max_prefill_tokens_per_batch = \
        serving["max_prefill_tokens_per_batch"]
    frontend.scheduler_max_decode_sequences_per_batch = \
        serving["max_decode_sequences_per_batch"]
    frontend.scheduler_batch_wait_cycles = serving["batch_wait_cycles"]
    frontend.scheduler_prefill_starvation_cycles = \
        serving["prefill_starvation_cycles"]
    frontend.clk_domain = agent_clock
    system.gate6_frontend = frontend

    observer = None
    if args.fabric_snapshots:
        observer = MeshExperimentObserver(cores=cores, network=ruby.network,
            targets=[getattr(ruby, f'axi_target_adapter{index}') for index in range(len(deployment.targets))])
        system.serving_fabric_observer = observer
    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    checkpoint_enabled = bool(args.checkpoint_dir or args.restore_checkpoint)
    if (args.checkpoint_at_tick is None) != (args.checkpoint_dir is None):
        fatal("checkpoint-dir and checkpoint-at-tick must be supplied together")
    if args.checkpoint_at_tick is not None and not 0 <= args.checkpoint_at_tick < args.sim_tick_limit:
        fatal("checkpoint-at-tick must be inside the simulation interval")
    save_bundle = None
    restore_bundle = None
    if checkpoint_enabled:
        m5.ticks.fixGlobalFrequency()
        from mesh_ir.serving_checkpoint import CheckpointBundle, checkpoint_identity
        if args.agent_only:
            fatal("checkpoint requires serving execution")
        for obj in root.descendants():
            obj.unproxyParams()
        checkpoint_inputs = [program_path, image_path, replay_path,
            *args.model_invocation,
            args.memory_file_image or None,
            args.model_weight_image, args.program_weight_registry,
            args.cache_state_replay, args.moe_provider_artifact]
        checkpoint_identity_value = checkpoint_identity(root.get_config_as_dict(),
            artifact_dir, Path('/proc/self/exe'), runtime_config.document, checkpoint_inputs)
        if args.checkpoint_dir:
            save_bundle = CheckpointBundle(args.checkpoint_dir, checkpoint_identity_value)
        if args.restore_checkpoint:
            restore_bundle = CheckpointBundle(args.restore_checkpoint, checkpoint_identity_value)
            restored_tick, restored_runtime = restore_bundle.read()
            if restored_tick >= args.sim_tick_limit:
                fatal("checkpoint tick must precede the simulation limit")
            if args.checkpoint_at_tick is not None and args.checkpoint_at_tick < restored_tick:
                fatal("checkpoint-at-tick precedes the restored tick")
            timeline.restore(restored_runtime["timeline"])
            if provider is not None:
                provider.invocations = restored_runtime["provider_invocations"]
            restore_bundle.restore_artifacts(artifact_dir)
    m5.instantiate(str(restore_bundle.path) if restore_bundle else None)
    if observer is not None:
        observer.dumpSnapshot(str(artifact_dir / 'fabric_initial.json'))
    if provider is not None:
        if loader is None:
            fatal("E_MOE_PROVIDER: a runtime provider requires the serving mesh loader")
        loader.setServingMoeProvider(provider)

    if restore_bundle:
        m5.simulate(0)
        if m5.curTick() != restored_tick or not frontend.programBoundaryReady():
            fatal("E_CHECKPOINT_STATE: restored program boundary is not ready")
        frontend.resumeProgramBoundary()
    event = timeline.run(m5.simulate, m5.curTick, frontend, args.sim_tick_limit,
        max(1, 100 * 1000000000000 // arch_manifest.clock_hz),
        save_at=args.checkpoint_at_tick,
        save=(lambda tick, state: save_bundle.save(m5.checkpoint, tick,
            dict(timeline=state, provider_invocations=provider.invocations if provider else 0),
            artifacts=[path for pattern in ("moe_instance_*_layer_*.bin", "provider_selection_*.json")
                       for path in artifact_dir.glob(pattern)]))
             if save_bundle else None)
    cause = event.getCause()
    if observer is not None:
        observer.dumpSnapshot(str(artifact_dir / 'fabric_final.json'))
    print("Exiting @ tick", m5.curTick(), "because", cause)
    if not (cause.startswith("AI_MESH_GATE3_QUIESCENT_SUCCESS") or
            cause.startswith("MESH_PROGRAM_DONE")):
        fatal("unexpected exit cause: %s", cause)
    if windowed:
        (artifact_dir / "timing_windows.json").write_text(json.dumps(
            timeline.document(m5.curTick(), facts_path), sort_keys=True, indent=2) + "\n")
    if weight_image is not None:
        result = json.loads(result_path.read_text())
        cores = [row["replay_state"] for row in result["moe"]["cache_state"]]
        if all(core is not None for core in cores):
            (artifact_dir / "cache_state_replay.json").write_text(json.dumps(
                weight_image.replay_document(sorted(cores, key=lambda row: row["core_id"])), sort_keys=True, indent=2) + "\n")
    if os.environ.get("AI_MESH_CHILD_REPORT"):
        from gate6_acceptance import write_gate6_artifacts

        write_gate6_artifacts(
            serving_program,
            result_path,
            facts_path,
            artifact_dir,
            os.environ["AI_MESH_CASE_ID"],
            os.environ["AI_MESH_SUBCASE"],
        )


main()

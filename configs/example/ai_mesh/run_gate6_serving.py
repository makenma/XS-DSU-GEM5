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

from gate6_serving_scenario import (  # noqa: E402
    adapter_indices,
    agent_only_scenario,
    combined_scenario,
    node_ids,
)
from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.builder import load_arch  # noqa: E402
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
from mesh_ir.agent_workload import load_workload_plan  # noqa: E402
from mesh_ir.gate4_oracle import arena_regions  # noqa: E402
from mesh_ir.host_bindings import build_host_binding_plans  # noqa: E402
from mesh_ir.effective import (  # noqa: E402
    EffectiveArchitecture,
    apply_cli_dma_overrides,
)
from gate4_runtime import config_hardware  # noqa: E402

ARCH_YAML = Path(__file__).resolve().parent / "arch/mesh_1x2.yaml"
ARCH_MANIFEST = load_arch(ARCH_YAML)
EFFECTIVE_ARCH = EffectiveArchitecture(ARCH_MANIFEST)

MESH_INSTANCES = 4
WATCHDOG_CYCLES = 2000000000


def _region(kind):
    for region in ARCH_MANIFEST.regions:
        if region.kind == kind:
            return region
    fatal("architecture has no %s region", kind)


HBM_BASE = _region("HBM").base
HBM_WINDOW = min(_region("HBM").bytes, 0x100000000)
HOST_SHARED_BASE = _region("HOST_SHARED").base
HOST_SHARED_BYTES = _region("HOST_SHARED").bytes
SRAM_BASE = _region("CORE_SRAM_APERTURE").base
SRAM_STRIDE = _region("CORE_SRAM_APERTURE").tile_stride
SRAM_TILE = _region("CORE_SRAM_APERTURE").tile_bytes


def build_arguments():
    parser = argparse.ArgumentParser()
    Options.addNoISAOptions(parser)
    Ruby.define_options(parser)
    parser.set_defaults(
        network="garnet",
        topology="AxiMeshDie",
        routing_algorithm=1,
        mesh_rows=2,
        mem_type="DDR3_1600_8x8",
        garnet_vnet_classes="ctrl,data,ctrl,ctrl,data",
        garnet_buffers_per_vnet="4,8,4,4,8",
        axi_data_width_bits=ARCH_MANIFEST.axi_data_bytes * 8,
        axi_mesh_routers=16,
    )
    parser.add_argument("--serving-program", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--surrogate-profiles", required=True)
    parser.add_argument("--weight-image-digest", required=True)
    parser.add_argument("--agent-only", action="store_true")
    parser.add_argument("--executor", default="serving_mesh",
                        choices=("serving_mesh", "full_context_surrogate"))
    parser.add_argument("--sim-tick-limit", type=int, default=20000000)
    parser.add_argument("--watchdog-ticks", type=int, default=0)
    parser.add_argument("--quota-write-contexts", type=int, default=8)
    parser.add_argument("--quota-write-beats", type=int, default=128)
    parser.add_argument("--quota-read-contexts", type=int, default=8)
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
    return parser.parse_args()


def published_output_span(program, region_base):
    profile = program.profile_stream_ranges[-1].profile_id
    command_ids = {
        program.commands[index].command_id
        for range_ in program.profile_stream_ranges
        if range_.profile_id == profile
        for index in range(range_.command_begin,
                           range_.command_begin + range_.command_count)
    }
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
    apply_cli_dma_overrides(EFFECTIVE_ARCH, args)
    if args.sram_write_bytes_per_cycle_per_bank is not None:
        EFFECTIVE_ARCH.override(
            "sram_write_bytes_per_cycle_per_bank",
            args.sram_write_bytes_per_cycle_per_bank)
    arch_manifest = EFFECTIVE_ARCH
    clock = "%dHz" % arch_manifest.clock_hz
    id_pool = 1 << arch_manifest.axi_id_bits
    core_ids = list(arch_manifest.core_ids)
    core_count = len(core_ids)
    adapters = adapter_indices(core_count)
    nodes = node_ids(core_count)

    program_path = Path(args.serving_program).resolve()
    if not args.agent_only and \
            arch_manifest.axi_data_bytes != args.axi_data_width_bits // 8:
        fatal("arch data bytes != configured AXI width")
    runtime_config_path = Path(args.runtime_config).resolve()
    runtime_config = load_agent_runtime_config(runtime_config_path)
    hardware = config_hardware(runtime_config.document)
    npu_control_base = hardware["npu_control_base"]
    agent_proxy_control_base = hardware["agent_proxy_control_base"]
    host_base = hardware["host_base"]
    host_window_base = hardware["host_base"]
    host_window_end = host_window_base + hardware["host_bytes"]

    artifact_dir = artifact_directory()
    result_path = artifact_dir / "mesh_result.json"
    facts_path = artifact_dir / "gate6_facts.tsv"

    agent = runtime_config.document["agent"]
    if agent["mode"] != "replay_plan":
        fatal("Gate6 serving runtime only accepts agent.mode=replay_plan")
    workload = load_workload_plan(
        runtime_config_path.parent / agent["workload_plan"])
    surrogate = load_surrogate_profiles(Path(args.surrogate_profiles))
    validate_runtime_config(runtime_config, workload, surrogate)
    validate_surrogate_profiles(surrogate, workload)
    policy = release_policy_of(runtime_config)
    regions = arena_regions(runtime_config.document["serving"]["address_map"])
    identity = build_command_identity_plan(workload, None, policy)
    host_tasks = build_host_task_identity_plan(workload)
    serving_program = decode_program(Path(args.serving_program).read_bytes())
    output_arena_base = runtime_config.document["serving"]["address_map"][
        "output_arena"]["base"]
    published = (output_arena_base,
                 published_output_span(serving_program,
                                       arch_manifest.regions[0].base)[1])
    kv_profile = serving_program.agent_request_profiles[0]
    kv_tensor_base = arch_manifest.regions[0].base
    for relocation in serving_program.relocations:
        if relocation.symbol_sid == kv_profile.primary_kv_symbol_id:
            kv_tensor_base += relocation.offset_bytes
    binding_plans = build_host_binding_plans(
        serving_program, [region.base for region in arch_manifest.regions])
    binding_counts = {(plan.program_id, plan.profile_id):
                      len(plan.requirements) for plan in binding_plans}
    arena = build_host_arena_object_plan(
        workload, None, policy, regions, binding_counts)

    capacity = build_capacity_plan(workload, None, runtime_config)
    image, _ = build_agent_plan_image(workload, None, policy, regions,
                                      surrogate, binding_plans,
                                      args.kv_session_slot_bytes)
    image_path = artifact_dir / "plan_image.bin"
    image_path.write_bytes(image)
    request_id_capacity = max(64, len(identity["records"]))
    services = runtime_config.document["agent_axi_driver"][
        "synthetic_host_services"]
    accepted_queue_entries = capacity["counts"]["live_context_bound"]

    scenario = (
        agent_only_scenario(
            [
                (npu_control_base, npu_control_base + 0x1000),
                (agent_proxy_control_base, agent_proxy_control_base + 0x1000),
                (host_window_base, host_window_end),
            ],
            {
                "write_contexts": args.quota_write_contexts,
                "write_beats": args.quota_write_beats,
                "read_contexts": args.quota_read_contexts,
                "read_beats": args.quota_read_beats,
            },
        ) if args.agent_only else combined_scenario(
        core_count,
        agent_windows=[
            (npu_control_base, npu_control_base + 0x1000),
            (agent_proxy_control_base, agent_proxy_control_base + 0x1000),
            (host_window_base, host_window_end),
        ],
        mesh_windows=(
            {
                "sram": [(SRAM_BASE + index * SRAM_STRIDE,
                          SRAM_BASE + index * SRAM_STRIDE + SRAM_TILE)
                         for index in range(core_count)],
                "hbm": (HBM_BASE, HBM_BASE + HBM_WINDOW),
                "host_shared": None,
            }
            if not args.agent_only
            else {"sram": [], "hbm": (HBM_BASE, HBM_BASE + 0x1000),
                  "host_shared": None}
        ),
        quotas={
            "write_contexts": args.quota_write_contexts,
            "write_beats": args.quota_write_beats,
            "read_contexts": args.quota_read_contexts,
            "read_beats": args.quota_read_beats,
        },
        routers=args.axi_mesh_routers,
    ))
    scenario_path = artifact_dir / "gate6_scenario.json"
    scenario_path.write_text(json.dumps(scenario, sort_keys=True))
    args.axi_scenario = str(scenario_path)

    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=clock, voltage_domain=system.voltage_domain
    )
    args.axi_id_width_bits = arch_manifest.axi_id_bits
    Ruby.create_system(args, False, system, cpus=[])
    system.ruby.clk_domain = system.clk_domain
    ruby = system.ruby

    recorder = Gate3ObservationRecorder(output_path=str(facts_path))
    if args.agent_only:
        cores = []
        apertures = []
        loader = None
        endpoint = None
        dispatcher = None
    system.gate3_recorder = recorder

    seed_path = artifact_dir / "hbm_seed.txt"
    verify_path = artifact_dir / "hbm_verify.txt"
    seed_path.write_text("")
    verify_path.write_text("")
    if not args.agent_only:
        endpoint = NpuMemoryEndpoint(
            adapter=getattr(ruby, "axi_target_adapter%d" % adapters["hbm_target"]),
            seed_json=str(seed_path),
            verify_json=str(verify_path),
        )
        endpoint.clk_domain = system.clk_domain
        system.mesh_endpoint = endpoint

        apertures = []
        for index, core_id in enumerate(core_ids):
            aperture = PeerSramAperture(
                adapter=getattr(
                    ruby,
                    "axi_target_adapter%d" %
                    (adapters["mesh_aperture_target_begin"] + index),
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
                    (adapters["mesh_core_initiator_begin"] + index),
                ),
                data_bus_bytes=arch_manifest.axi_data_bytes,
                aw_queue_depth=arch_manifest.dma_segment_queue_depth,
                ar_queue_depth=arch_manifest.dma_segment_queue_depth,
                axi_id_count=id_pool,
                axi_id_base=0,
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
                decode_width=arch_manifest.decode_width,
                event_visibility_cycles=arch_manifest.event_visibility_cycles,
                admit_window=arch_manifest.admit_window,
                tensor_queue_depth=arch_manifest.tensor_queue_depth,
                tensor_setup_cycles=arch_manifest.tensor_setup_cycles,
                tensor_flush_cycles=arch_manifest.tensor_pipeline_flush_cycles,
                tensor_macs_per_cycle=arch_manifest.tensor_macs_per_cycle["fp16"],
                tensor_macs_by_dtype=EFFECTIVE_ARCH.dtype_vector(
                    arch_manifest.tensor_macs_per_cycle, "tensor macs"),
                vector_queue_depth=arch_manifest.vector_queue_depth,
                vector_elements_per_cycle=(
                    arch_manifest.vector_elements_per_cycle["fp16"]),
                vector_elements_by_dtype=EFFECTIVE_ARCH.dtype_vector(
                    arch_manifest.vector_elements_per_cycle, "vector elements"),
                reduce_queue_depth=arch_manifest.reduce_queue_depth,
                reduce_setup_cycles=arch_manifest.reduce_setup_cycles,
                reduce_flush_cycles=arch_manifest.reduce_flush_cycles,
                reduce_ops_per_cycle=arch_manifest.reduce_ops_per_cycle["fp16"],
                reduce_ops_by_dtype=EFFECTIVE_ARCH.dtype_vector(
                    arch_manifest.reduce_ops_per_cycle, "reduce ops"),
                sram_banks=arch_manifest.sram_banks,
                sram_bytes=arch_manifest.sram_bytes,
                sram_alignment=arch_manifest.sram_base_alignment_bytes,
                sram_line_bytes=(
                    arch_manifest.sram_read_bytes_per_cycle_per_bank),
                sram_read_bytes_per_cycle=(
                    arch_manifest.sram_read_bytes_per_cycle_per_bank),
                sram_write_bytes_per_cycle=(
                    arch_manifest.sram_write_bytes_per_cycle_per_bank),
                sram_bank_queue_depth=arch_manifest.sram_bank_queue_depth,
                sram_read_ports=arch_manifest.sram_read_ports_per_bank,
                sram_write_ports=arch_manifest.sram_write_ports_per_bank,
                dma=engine,
            )
            core.clk_domain = system.clk_domain
            cores.append(core)
            setattr(system, "mesh_core_%d" % core_id, core)
            setattr(system, "mesh_engine_%d" % core_id, engine)

        loader = MeshProgramLoader(
            program_file=str(program_path),
            cores=cores,
            apertures=apertures,
            arch_digest=EFFECTIVE_ARCH.base_digest().hex(),
            effective_arch_digest=EFFECTIVE_ARCH.digest().hex(),
            core_ids=core_ids,
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
            weight_policy="",
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
            loader=loader,
            cores=cores,
            apertures=apertures,
            endpoint=endpoint,
            output_span_base=published[0],
            output_span_bytes=published[1],
            network=ruby.network,
            result_json=str(result_path),
            instances=MESH_INSTANCES,
            autostart=False,
            watchdog_ticks=args.watchdog_ticks,
        )
        dispatcher.clk_domain = system.clk_domain
        system.mesh_dispatcher = dispatcher

    driver = AgentAxiDriver(
        master=getattr(ruby, "axi_initiator_adapter%d" %
                       adapters["driver_initiator"]),
        target=getattr(ruby, "axi_target_adapter%d" %
                       adapters["agent_proxy_target"]),
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
                       adapters["frontend_initiator"]),
        control_target=getattr(ruby, "axi_target_adapter%d" %
                               adapters["npu_control_target"]),
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
        kv_session_slot_bytes=args.kv_session_slot_bytes,
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
    frontend.kv_bytes_per_token = kv_profile.kv_bytes_per_token
    frontend.kv_region_base = (kv_tensor_base if args.kv_region_base is None
                               else args.kv_region_base)
    frontend.clk_domain = agent_clock
    system.gate6_frontend = frontend

    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    m5.instantiate()

    event = m5.simulate(args.sim_tick_limit)
    cause = event.getCause()
    print("Exiting @ tick", m5.curTick(), "because", cause)
    if not (cause.startswith("AI_MESH_GATE3_QUIESCENT_SUCCESS") or
            cause.startswith("MESH_PROGRAM_DONE")):
        fatal("unexpected exit cause: %s", cause)
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

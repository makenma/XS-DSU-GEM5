"""Golden E2E configs for the Dummy Core runtime prototype (mock AXI).

Runs a handwritten .mshb program on the gem5 event loop with the AXI_MESH
null-ISA build, asserts the stable exit cause and reconciles the mock
transport byte accounting against the compiler traffic oracle.

Usage:
  build/AXI_MESH/gem5.opt configs/example/ai_mesh/run_mesh_program.py \
      --program single --mesh-program-dir <dir-with-program.mshb>
"""

import argparse
import json
import os
import sys
from pathlib import Path

import m5
from m5.objects import Root, SrcClockDomain, System, VoltageDomain
from m5.util import fatal

from dummy_core_case_registry import Backend, backend_programs, invariant_registry
from mesh_ir.producer_content import (
    ProducerContentError,
    verify_transfer_producer_content,
)
from mesh_ir.runtime_reconciliation import (
    ReconciliationError,
    payload_digest as _payload_digest,
    reconcile as reconcile_runtime,
)

parser = argparse.ArgumentParser()
parser.add_argument("--arch", default=str(Path(__file__).resolve().parent / "arch/mesh_1x2.yaml"))
parser.add_argument(
    "--program",
    choices=backend_programs(Backend.MOCK),
    required=True,
)
parser.add_argument("--instances", type=int, default=1)
parser.add_argument("--dma-queue-depth", type=int, default=None,
                    help="Override arch dma descriptor_queue_depth (tuning)")
parser.add_argument("--expect-ticks", type=int, default=0)
parser.add_argument("--expect-digest", default="")
parser.add_argument("--expect-digests-differ", action="store_true")
parser.add_argument("--expect-fatal", default="")
parser.add_argument("--digest-stable-across-instances", action="store_true")
parser.add_argument("--assert-conservation", action="store_true")
parser.add_argument("--traffic-multiplier", type=int, default=1,
                    help="Logical executions per descriptor (REPEAT generations)")
parser.add_argument("--admit-window", type=int, default=None,
                    help="Override arch admit_window (tuning)")
parser.add_argument("--expect-gemm-cycles", type=int, default=-1)
parser.add_argument("--expect-command-latency", action="append", default=[])
parser.add_argument("--assert-all-done", action="store_true")
parser.add_argument("--assert-event-visibility", action="store_true")
parser.add_argument("--expect-sram-service-min", type=int, default=0)
parser.add_argument("--watchdog-ticks", type=int, default=0,
                    help="Dispatcher progress watchdog bound; 0 disables")
parser.add_argument("--error-descriptors", default="",
                    help="Comma-separated descriptor ids to complete with an injected AXI error")
parser.add_argument("--drop-descriptors", default="",
                    help="Comma-separated descriptor ids whose completion is dropped")
parser.add_argument("--fault-occurrence", type=int, default=0,
                    help="Which completion of an injected descriptor faults "
                         "(0 = every completion; 2 faults the first REPEAT replay pass)")
parser.add_argument("--sim-tick-limit", type=int, default=0)
parser.add_argument(
    "--receiver-fault",
    default="",
    help="Test-only receiver notification fault: receive_drop, "
         "receive_duplicate, receive_redirect or receive_premature",
)
parser.add_argument(
    "--residency-fault",
    default="",
    help="Test-only residency installation fault: skip_pre_resident withholds "
         "the compiler-declared initial residency",
)
parser.add_argument("--digest-repeat-count", type=int, default=0,
                    help="Assert each REPEAT-window compute digest appears N times")
parser.add_argument("--entrypoint-id", type=int, default=0,
                    help="Selected entrypoint id; 0 selects the unique variant")
parser.add_argument("--profile-id", type=int, default=0,
                    help="Selected profile id; 0 selects the unique variant")
parser.add_argument("--bindings-file", default="",
                    help="JSON dispatch bindings; empty reuses the compiler reference bindings")
parser.add_argument("--expected-traffic", default="",
                    help="Expected traffic oracle override for an alternate dispatch binding")
parser.add_argument(
    "--mesh-program-dir",
    required=True,
    help="Directory holding program.mshb and expected_traffic.json",
)
args = parser.parse_args()

# Architecture facts come from the single source of truth (arch yaml) via
# the same loader the compiler side uses; no duplicated constants here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "util" / "mesh_ir"))
from mesh_ir.architecture import load_arch  # noqa: E402
from mesh_ir.effective import EffectiveArchitecture  # noqa: E402
from mesh_ir.ir.common import Access  # noqa: E402

arch_manifest = load_arch(Path(args.arch))
effective_arch = EffectiveArchitecture(arch_manifest)
if args.dma_queue_depth is not None:
    effective_arch.override("dma_descriptor_queue_depth", args.dma_queue_depth)
if args.admit_window is not None:
    effective_arch.override("admit_window", args.admit_window)
from mesh_ir.model import MeshIrError  # noqa: E402


def _dtype_vector(throughput_map):
    return effective_arch.dtype_vector(throughput_map, "throughput")


arch = {
    "core_ids": list(arch_manifest.core_ids),
    "sram_bytes": arch_manifest.sram_bytes,
    "sram_banks": arch_manifest.sram_banks,
    "sram_alignment": arch_manifest.sram_base_alignment_bytes,
    "axi_data_bytes": arch_manifest.axi_data_bytes,
    "axi_max_burst_beats": arch_manifest.axi_max_burst_beats,
    "regions": [
        {
            "id": index,
            "base": region.base,
            "bytes": region.bytes,
            "tile_stride": region.tile_stride,
            "tile_bytes": region.tile_bytes,
        }
        for index, region in enumerate(arch_manifest.regions)
    ],
}
arch_digest = effective_arch.digest().hex()

program_dir = Path(args.mesh_program_dir)
program_path = program_dir / "program.mshb"
traffic_path = (
    Path(args.expected_traffic)
    if args.expected_traffic
    else program_dir / "expected_traffic.json"
)
artifact_dir = Path(os.environ.get("AI_MESH_ARTIFACT_DIR", program_dir))
result_path = artifact_dir / "actual_result.json"
reconciliation_path = artifact_dir / "reconciliation.json"
if not program_path.exists():
    fatal("missing %s", program_path)

system = System()
system.voltage_domain = VoltageDomain(voltage="1V")
system.clk_domain = SrcClockDomain(
    clock=f"{arch_manifest.clock_hz / 1e9:g}GHz", voltage_domain=system.voltage_domain
)

from m5.objects import (  # noqa: E402
    MeshDispatcher,
    MeshDummyCore,
    MeshProgramLoader,
    MockAxiTransport,
    TensorDmaEngine,
)

transport = MockAxiTransport(
    data_bus_bytes=arch["axi_data_bytes"],
    burst_base_latency=arch_manifest.dma_burst_base_latency,
    sram_region_base=arch["regions"][2]["base"],
    sram_tile_stride=arch["regions"][2]["tile_stride"],
    error_descriptors=[
        int(value) for value in args.error_descriptors.split(",") if value
    ],
    lost_descriptors=[
        int(value) for value in args.drop_descriptors.split(",") if value
    ],
    fault_occurrence=args.fault_occurrence,
)
transport.clk_domain = system.clk_domain

cores = []
for core_id in arch["core_ids"]:
    core = MeshDummyCore(
        core_id=core_id,
        decode_width=arch_manifest.decode_width,
        event_visibility_cycles=arch_manifest.event_visibility_cycles,
        reference_compute=args.expect_fatal == "reference_compute",
        admit_window=effective_arch.admit_window,
        tensor_queue_depth=arch_manifest.tensor_queue_depth,
        tensor_setup_cycles=arch_manifest.tensor_setup_cycles,
        tensor_flush_cycles=arch_manifest.tensor_pipeline_flush_cycles,
        tensor_macs_by_dtype=_dtype_vector(arch_manifest.tensor_macs_per_cycle),
        vector_queue_depth=arch_manifest.vector_queue_depth,
        vector_elements_by_dtype=_dtype_vector(arch_manifest.vector_elements_per_cycle),
        reduce_queue_depth=arch_manifest.reduce_queue_depth,
        reduce_setup_cycles=arch_manifest.reduce_setup_cycles,
        reduce_flush_cycles=arch_manifest.reduce_flush_cycles,
        reduce_ops_by_dtype=_dtype_vector(arch_manifest.reduce_ops_per_cycle),
        sram_banks=arch_manifest.sram_banks,
        sram_bank_queue_depth=effective_arch.sram_bank_queue_depth,
        sram_read_ports=arch_manifest.sram_read_ports_per_bank,
        sram_write_ports=arch_manifest.sram_write_ports_per_bank,
        sram_bytes=arch_manifest.sram_bytes,
        sram_alignment=arch_manifest.sram_base_alignment_bytes,
        sram_line_bytes=arch_manifest.sram_bank_interleave_bytes,
        sram_read_bytes_per_cycle=arch_manifest.sram_read_bytes_per_cycle_per_bank,
        sram_write_bytes_per_cycle=arch_manifest.sram_write_bytes_per_cycle_per_bank,
        dma=TensorDmaEngine(
            core_id=core_id,
            setup_cycles=arch_manifest.dma_setup_cycles,
            descriptor_queue_depth=effective_arch.dma_descriptor_queue_depth,
            max_outstanding=max(
                effective_arch.dma_read_outstanding,
                effective_arch.dma_write_outstanding,
            ),
            transport=transport,
        ),
    )
    core.clk_domain = system.clk_domain
    core.dma.clk_domain = system.clk_domain
    cores.append(core)

bindings = json.loads(Path(args.bindings_file).read_text()) if args.bindings_file else []
fabric_target_names = []
fabric_range_region_ids = []
fabric_range_owner_cores = []
fabric_range_offsets = []
fabric_range_sizes = []
fabric_range_writable = []
for target in arch_manifest.fabric.targets:
    for address_range in target.ranges:
        fabric_target_names.append(target.name)
        fabric_range_region_ids.append(address_range.region_id)
        fabric_range_owner_cores.append(address_range.owner_core)
        fabric_range_offsets.append(address_range.offset_bytes)
        fabric_range_sizes.append(address_range.size_bytes)
        fabric_range_writable.append(
            1 if address_range.access == Access.READ_WRITE else 0
        )

loader = MeshProgramLoader(
    program_file=str(program_path),
    cores=cores,
    transport=transport,
    arch_digest=arch_digest,
    effective_arch_digest=effective_arch.digest().hex(),
    core_ids=arch["core_ids"],
    sram_bytes=arch["sram_bytes"],
    sram_banks=arch["sram_banks"],
    sram_alignment=arch["sram_alignment"],
    axi_data_bytes=arch["axi_data_bytes"],
    axi_max_burst_beats=arch["axi_max_burst_beats"],
    axi_address_bits=arch_manifest.axi_address_bits,
    entrypoint_id=args.entrypoint_id,
    profile_id=args.profile_id,
    region_ids=[region["id"] for region in arch["regions"]],
    region_bases=[region["base"] for region in arch["regions"]],
    region_bytes=[region["bytes"] for region in arch["regions"]],
    region_tile_strides=[region.get("tile_stride", 0) for region in arch["regions"]],
    region_kinds=[
        {"HBM": 0, "HOST_SHARED": 1, "CORE_SRAM_APERTURE": 2}[r.kind]
        for r in arch_manifest.regions
    ],
    region_tile_bytes=[region.get("tile_bytes", 0) for region in arch["regions"]],
    binding_slot_ids=[item["slot_id"] for item in bindings],
    binding_region_ids=[item["region_id"] for item in bindings],
    binding_owner_cores=[item["owner_core"] for item in bindings],
    binding_offsets=[item["allocation_offset_bytes"] for item in bindings],
    binding_sizes=[item["allocation_size_bytes"] for item in bindings],
    binding_alignments=[item["allocation_alignment_bytes"] for item in bindings],
    binding_accesses=[item["access"] for item in bindings],
    fabric_target_names=fabric_target_names,
    fabric_range_region_ids=fabric_range_region_ids,
    fabric_range_owner_cores=fabric_range_owner_cores,
    fabric_range_offsets=fabric_range_offsets,
    fabric_range_sizes=fabric_range_sizes,
    fabric_range_writable=fabric_range_writable,
)

dispatcher = MeshDispatcher(
    loader=loader,
    cores=cores,
    transport=transport,
    result_json=str(result_path),
    instances=args.instances,
    watchdog_ticks=args.watchdog_ticks,
    receiver_fault=args.receiver_fault,
    residency_fault=args.residency_fault,
)
dispatcher.clk_domain = system.clk_domain

system.mesh_loader = loader
system.mesh_dispatcher = dispatcher
system.mesh_transport = transport
for index, core in enumerate(cores):
    setattr(system, f"mesh_core_{index}", core)

dispatcher.instances = args.instances

root = Root(full_system=False, system=system)
m5.instantiate()

exit_event = m5.simulate(args.sim_tick_limit) if args.sim_tick_limit else m5.simulate()
cause = exit_event.getCause()
print("Exiting @ tick", m5.curTick(), "because", cause)
reconcilable = cause.startswith("MESH_PROGRAM_DONE") or cause.startswith(
    "MESH_PROGRAM_ERROR_DRAINED"
)
if not reconcilable:
    fatal("mesh program did not complete: %s", cause)
if args.expect_ticks and m5.curTick() != args.expect_ticks:
    fatal("tick golden mismatch: expected %d got %d", args.expect_ticks, m5.curTick())


schedule = json.loads((program_dir / "schedule.mesh.json").read_text())
attrs_by_index = {i + 1: attr for i, attr in enumerate(schedule["sections"]["OP_ATTRS"])}


def _fill_pattern_of(row):
    for command in schedule["sections"]["COMMANDS"]:
        if command["command_id"] == row["command_id"]:
            value = attrs_by_index.get(command["attr_index"], {}).get("pattern", 0)
            return int(value, 16) if isinstance(value, str) else value
    return 0


result = json.loads(result_path.read_text())

traffic = json.loads(traffic_path.read_text())
descriptor_kinds = {
    row["descriptor_id"]: row["kind"]
    for row in schedule["sections"]["DMA_DESCRIPTORS"]
}
expected_rows = [
    {
        **row,
        **row["identity"],
        "kind": descriptor_kinds[row["identity"]["descriptor_id"]],
    }
    for row in traffic["descriptors"]
]
expected = {row["descriptor_id"]: row for row in expected_rows}
actual = {row["descriptor_id"]: row for row in result["transport"]}
try:
    reconciliation = reconcile_runtime(
        cause=cause,
        result=result,
        schedule=schedule,
        expected_rows=expected_rows,
        error_descriptors=args.error_descriptors.split(","),
        fault_occurrence=args.fault_occurrence,
        instances=args.instances,
        traffic_multiplier=args.traffic_multiplier,
    )
except ReconciliationError as error:
    fatal("runtime reconciliation failed: %s", error)
reconciliation_path.write_text(json.dumps(reconciliation, indent=2) + "\n")

# The agreement keeps the drain's exit status: the reconciliation above is the
# authority for the descriptor accounting, and the healthy-only checks below
# must not reclassify a drained run.
if not cause.startswith("MESH_PROGRAM_DONE"):
    fatal("mesh program did not complete: %s", cause)

if args.program == "dma_shapes":
    # Both identities come from the admitted program: the push descriptor and
    # the LOCAL_FILL descriptor that carries the declared fill pattern.  The
    # transfer expectation follows the admitted source's real last producer.
    fill_pattern = bytes([0xA5]) + bytes(7)
    expected_content = _payload_digest(fill_pattern * 16)
    transfers = [
        row for row in schedule["sections"]["DMA_DESCRIPTORS"] if row["kind"] == 3
    ]
    fills = [
        row
        for row in schedule["sections"]["DMA_DESCRIPTORS"]
        if row["kind"] == 5 and _fill_pattern_of(row) == 0xA5
    ]
    if len(transfers) != 1 or len(fills) != 1:
        fatal("dma_shapes admitted shapes changed: %d transfers, %d pattern fills",
              len(transfers), len(fills))
    try:
        verify_transfer_producer_content(
            program_dir, result, transfers[0]["descriptor_id"]
        )
    except ProducerContentError as error:
        fatal("transfer producer content check failed: %s", error)
    fill_rows = [
        row
        for row in result["transport"]
        if row["descriptor_id"] == fills[0]["descriptor_id"]
    ]
    if len(fill_rows) != 1 or fill_rows[0]["payload_digest"] != expected_content:
        fatal("FILL content digest does not cover the descriptor byte stream")

for core in result["cores"]:
    if core["commands_issued"] != core["commands_completed"] or core["live_commands"] != 0:
        fatal(
            "conservation violated on core %d: issued=%d completed=%d live=%d",
            core["core_id"],
            core["commands_issued"],
            core["commands_completed"],
            core["live_commands"],
        )

digests = result["digests"]
if args.digest_repeat_count:
    from collections import Counter

    counts = Counter((d["core_id"], d["command_id"]) for d in digests)
    for key, count in counts.items():
        if count != args.digest_repeat_count:
            fatal("digest count for command %s is %d, expected %d", key, count,
                  args.digest_repeat_count)
    for key in counts:
        values = {d["digest"] for d in digests if (d["core_id"], d["command_id"]) == key}
        if len(values) != 1:
            fatal("digest differs across generations for %s", key)
if args.expect_digest and not digests[0]["digest"].startswith(args.expect_digest):
    fatal("digest golden mismatch: %s", digests[0]["digest"])
if args.expect_digests_differ and len({d["digest"] for d in digests}) < 2:
    fatal("compute digests are not sensitive to opcode/attrs: %s", digests)
if args.digest_stable_across_instances:
    per_command = {}
    for d in digests:
        per_command.setdefault((d["core_id"], d["command_id"]), []).append(d["digest"])
    for key, values in per_command.items():
        if len(set(values)) != 1:
            fatal("digest changed across instances for %s: %s", key, values)

if args.assert_all_done:
    schedule_commands = {
        (c["core_id"], c["command_id"]) for c in schedule["sections"]["COMMANDS"]
    }
    done = set()
    for core in result["cores"]:
        for command_id in core["completed_command_ids"]:
            done.add((core["core_id"], command_id))
    if done != schedule_commands:
        fatal(
            "not all commands reached DONE: missing %s",
            sorted(schedule_commands - done)[:8],
        )
if args.expect_sram_service_min:
    total_service = sum(core["sram_service_cycles"] for core in result["cores"])
    if total_service < args.expect_sram_service_min:
        fatal("sram service cycles below minimum: %d < %d", total_service,
              args.expect_sram_service_min)
if args.assert_event_visibility:
    visibility_ticks = arch_manifest.event_visibility_cycles * (
        1_000_000_000_000 // arch_manifest.clock_hz
    )
    # Producer of each event: the signaling command (compute/control) or the
    # DMA command owning the descriptor whose completion_event it is.
    event_producer = {}
    for command in schedule["sections"]["COMMANDS"]:
        if command["signal_event"]:
            event_producer[command["signal_event"]] = command["command_id"]
    for row in expected_rows:
        event_producer[row.get("completion_event", 0)] = row["command_id"]
    waits = schedule["sections"]["COMMAND_WAITS"]
    ok_edges = 0
    for command in schedule["sections"]["COMMANDS"]:
        consumer_id = command["command_id"]
        consumer_issue = None
        for core in result["cores"]:
            if core["core_id"] == command["core_id"]:
                consumer_issue = core.get("command_issue_ticks", {}).get(str(consumer_id))
        if consumer_issue is None:
            continue
        for wait_index in range(command["wait_count"]):
            event_id = waits[command["wait_begin"] + wait_index]["event_id"]
            producer_id = event_producer.get(event_id)
            if producer_id is None:
                continue
            producer_done_tick = None
            for core in result["cores"]:
                producer_done_tick = core.get("command_done_ticks", {}).get(str(producer_id))
                if producer_done_tick is not None:
                    break
            if producer_done_tick is None:
                continue
            if consumer_issue < producer_done_tick + visibility_ticks:
                fatal(
                    "event visibility violated: command %d issued at %d before "
                    "producer %d done + visibility (%d)",
                    consumer_id, consumer_issue, producer_id,
                    producer_done_tick + visibility_ticks,
                )
            ok_edges += 1
    if ok_edges == 0:
        fatal("event visibility check found no wait edges")

if args.expect_gemm_cycles >= 0:
    total_gemm = sum(core["gemm_cycles"] for core in result["cores"])
    if total_gemm != args.expect_gemm_cycles:
        fatal("gemm cycle golden mismatch: expected %d got %d", args.expect_gemm_cycles, total_gemm)

tick_period = 1_000_000_000_000 // arch_manifest.clock_hz
for expectation in args.expect_command_latency:
    command_id_text, cycles_text = expectation.split(":", 1)
    command_id = int(command_id_text)
    expected_cycles = int(cycles_text)
    matches = []
    for core in result["cores"]:
        issue = core["command_issue_ticks"].get(str(command_id))
        done = core["command_done_ticks"].get(str(command_id))
        if issue is not None or done is not None:
            matches.append((core["core_id"], issue, done))
    if len(matches) != 1 or matches[0][1] is None or matches[0][2] is None:
        fatal("command %d has no unique issue/done timing: %s", command_id, matches)
    actual_ticks = matches[0][2] - matches[0][1]
    expected_ticks = expected_cycles * tick_period
    if actual_ticks != expected_ticks:
        actual_cycles = (
            str(actual_ticks // tick_period)
            if actual_ticks % tick_period == 0
            else "non-integral"
        )
        fatal(
            "command %d latency mismatch: expected %d cycles (%d ticks), "
            "got %s cycles (%d ticks)",
            command_id,
            expected_cycles,
            expected_ticks,
            actual_cycles,
            actual_ticks,
        )

total_commands = sum(core["commands_completed"] for core in result["cores"])
if os.environ.get("AI_MESH_CHILD_REPORT"):
    from mesh_ir.acceptance import build_dma_traffic, write_success_artifacts

    invariant_names = invariant_registry(f"mock_{args.program}", sys.argv[1:])
    traffic = build_dma_traffic(
        os.environ["AI_MESH_CASE_ID"],
        os.environ["AI_MESH_SUBCASE"],
        expected_rows,
        result["transport"],
        args.instances * args.traffic_multiplier,
    )
    write_success_artifacts(invariant_names, traffic)
print(
    "MESH_E2E_PASS: %s cores_halted=%d descriptors=%d commands_completed=%d"
    % (args.program, len(result["cores"]), len(actual), total_commands)
)

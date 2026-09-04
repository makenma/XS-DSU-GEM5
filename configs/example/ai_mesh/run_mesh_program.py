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
parser.add_argument("--sim-tick-limit", type=int, default=0)
parser.add_argument("--digest-repeat-count", type=int, default=0,
                    help="Assert each REPEAT-window compute digest appears N times")
parser.add_argument(
    "--mesh-program-dir",
    required=True,
    help="Directory holding program.mshb and expected_traffic.json",
)
args = parser.parse_args()

# Architecture facts come from the single source of truth (arch yaml) via
# the same loader the compiler side uses; no duplicated constants here.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "util" / "mesh_ir"))
from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.effective import EffectiveArchitecture  # noqa: E402

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
arch_digest = effective_arch.base_digest().hex()

program_dir = Path(args.mesh_program_dir)
program_path = program_dir / "program.mshb"
traffic_path = program_dir / "expected_traffic.json"
artifact_dir = Path(os.environ.get("AI_MESH_ARTIFACT_DIR", program_dir))
result_path = artifact_dir / "actual_result.json"
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
        sram_line_bytes=arch_manifest.sram_read_bytes_per_cycle_per_bank,
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
    region_ids=[region["id"] for region in arch["regions"]],
    region_bases=[region["base"] for region in arch["regions"]],
    region_bytes=[region["bytes"] for region in arch["regions"]],
    region_tile_strides=[region.get("tile_stride", 0) for region in arch["regions"]],
    region_kinds=[
        {"HBM": 0, "HOST_SHARED": 1, "CORE_SRAM_APERTURE": 2}[r.kind]
        for r in arch_manifest.regions
    ],
    region_tile_bytes=[region.get("tile_bytes", 0) for region in arch["regions"]],
)

dispatcher = MeshDispatcher(
    loader=loader,
    cores=cores,
    transport=transport,
    result_json=str(result_path),
    instances=args.instances,
    watchdog_ticks=args.watchdog_ticks,
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
if not cause.startswith("MESH_PROGRAM_DONE"):
    fatal("mesh program did not complete: %s", cause)
if args.expect_ticks and m5.curTick() != args.expect_ticks:
    fatal("tick golden mismatch: expected %d got %d", args.expect_ticks, m5.curTick())

# Reconcile the mock transport accounting against the compiler oracle and
# prove content flow with an independent digest oracle.
def _payload_digest(data: bytes) -> str:
    h0 = 0xCBF29CE484222325
    h1 = 0x9E3779B97F4A7C15
    for byte in data:
        h0 = ((h0 ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        h1 = ((h1 + ((h0 >> 31) ^ byte)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    return f"{h0:016x}-{h1:016x}"


schedule = json.loads((program_dir / "schedule.mesh.json").read_text())
attrs_by_index = {i + 1: attr for i, attr in enumerate(schedule["sections"]["OP_ATTRS"])}


def _fill_pattern_of(row):
    for command in schedule["sections"]["COMMANDS"]:
        if command["command_id"] == row["command_id"]:
            return attrs_by_index.get(command["attr_index"], {}).get("pattern", 0)
    return 0


result = json.loads(result_path.read_text())
if args.instances > 1:
    for row in result["transport"]:
        pass  # per-descriptor rows accumulate across instances; scale below

expected_rows = json.loads(traffic_path.read_text())
expected = {row["descriptor_id"]: row for row in expected_rows}
actual = {row["descriptor_id"]: row for row in result["transport"]}
if sorted(actual.keys()) != sorted(expected.keys()):
    fatal(
        "traffic descriptor mismatch: expected %s got %s",
        sorted(expected.keys()),
        sorted(actual.keys()),
    )
for descriptor_id, row in expected.items():
    got = actual[descriptor_id]
    useful = row["useful_bytes"] * args.instances * args.traffic_multiplier
    if useful == 0:
        zero_fields = (
            got["read_bytes"], got["write_bytes"], got["fill_bytes"],
            got["p2p_bytes"], got["read_bursts"], got["write_bursts"],
            got["p2p_bursts"],
        )
        if any(zero_fields):
            fatal(
                "zero-byte descriptor %d produced traffic: %s",
                descriptor_id, zero_fields,
            )
        continue
    if not got.get("payload_digest"):
        fatal("descriptor %d has no payload digest", descriptor_id)
    if row["kind"] == 5:  # LOCAL_FILL: analytic pattern digest per instance
        pattern = _fill_pattern_of(row)
        data = bytes((pattern >> (8 * (i % 8))) & 0xFF for i in range(row["useful_bytes"]))
        # bytes repeat per instance; digest is recorded per completion, so the
        # last instance's digest must equal the analytic pattern digest.
        if got["payload_digest"] != _payload_digest(data):
            fatal("fill content mismatch on descriptor %d", descriptor_id)
        if got["fill_bytes"] != row["useful_bytes"] * args.instances * args.traffic_multiplier:
            fatal("fill traffic mismatch on descriptor %d", descriptor_id)
        continue
    if row["kind"] == 3:  # P2P
        if got["p2p_bytes"] != useful or got["p2p_bursts"] != row["bursts"] * args.instances * args.traffic_multiplier:
            fatal("p2p traffic mismatch on descriptor %d", descriptor_id)
    elif row["kind"] == 2:  # STORE
        if got["write_bytes"] != useful or got["write_bursts"] != row["bursts"] * args.instances * args.traffic_multiplier:
            fatal("store traffic mismatch on descriptor %d", descriptor_id)
    else:
        if got["read_bytes"] != useful or got["read_bursts"] != row["bursts"] * args.instances * args.traffic_multiplier:
            fatal("load traffic mismatch on descriptor %d", descriptor_id)
        # Loads read zero-initialized HBM: content digest is analytic.
        if got["payload_digest"] != _payload_digest(bytes(row["useful_bytes"])):
            fatal("load content mismatch on descriptor %d", descriptor_id)
    if row["kind"] in (2, 3):
        # STORE/P2P carry compute results: their digest must differ from the
        # zero-content digest, proving data flowed through SRAM.
        if got["payload_digest"] == _payload_digest(bytes(row["useful_bytes"])):
            fatal("descriptor %d payload shows no content flow", descriptor_id)

if args.program == "dma_shapes":
    fill_pattern = bytes([0xA5]) + bytes(7)
    expected_content = _payload_digest(fill_pattern * 16)
    p2p = next(row for row in result["transport"] if row["p2p_bytes"] > 0)
    if p2p["payload_digest"] != expected_content:
        fatal("multi-row P2P content digest does not cover every row")
    fill = next(row for row in result["transport"] if row["fill_bytes"] == 128)
    if fill["payload_digest"] != expected_content:
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

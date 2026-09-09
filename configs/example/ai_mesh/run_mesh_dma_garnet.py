"""Gate 2 E2E: Dummy Core DMA over the real NPU AXI-over-Garnet network.

Builds the mesh program system (cores + DMA engines + bridges) on top of the
AXI_MESH Ruby/Garnet fabric (driver_mode=mesh_program), runs it and asserts
command/byte conservation, completion-point semantics, error drain and full
network quiescence from the machine-readable result artifact.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import m5
from m5.objects import (
    AddrRange,
    AxiGarnetBridge,
    AxiTensorDmaEngine,
    MeshDispatcher,
    MeshDummyCore,
    MeshProgramLoader,
    NpuMemoryEndpoint,
    PeerSramAperture,
    Root,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util import addToPath, fatal

from dummy_core_case_registry import Backend, backend_cases, invariant_registry


def fatal_if(condition, fmt, *args):
    if condition:
        fatal(fmt, *args)

addToPath("../../")

from common import Options
from ruby import Ruby

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "util" / "mesh_ir"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dma_uid_predict import predict  # noqa: E402
from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.effective import (  # noqa: E402
    EffectiveArchitecture,
    apply_cli_dma_overrides,
)
from mesh_ir.generated import abi as A  # noqa: E402

ARCH_YAML = Path(__file__).resolve().parent / "arch/mesh_1x2.yaml"

ARCH_MANIFEST = load_arch(ARCH_YAML)
EFFECTIVE_ARCH = EffectiveArchitecture(ARCH_MANIFEST)


def _region(kind):
    for region in ARCH_MANIFEST.regions:
        if region.kind == kind:
            return region
    raise RuntimeError(f"arch yaml has no {kind} region")


def _layout_key(manifest):
    """Routing-relevant manifest identity: sweep arches may vary dma/axi
    tuning fields but must keep the scenario's address map and topology."""
    return (
        manifest.clock_hz,
        tuple(manifest.core_ids),
        manifest.sram_bytes,
        manifest.sram_banks,
        manifest.axi_data_bytes,
        manifest.axi_address_bits,
        manifest.axi_id_bits,
        tuple((r.kind, r.base, r.bytes, getattr(r, "tile_stride", 0),
               getattr(r, "tile_bytes", 0)) for r in manifest.regions),
    )


HOST_SHARED_BASE = _region("HOST_SHARED").base
HBM_BASE = _region("HBM").base
# The Garnet scenario routes a bounded backed HBM window; addresses inside
# the 16 GiB arch region but outside this window reach the default error
# target (this is how the DECERR fault programs are constructed).  The
# window is a scenario routing fact, not an architecture capacity.
HBM_WINDOW = min(_region("HBM").bytes, 0x100000000)
SRAM_BASE = _region("CORE_SRAM_APERTURE").base
SRAM_STRIDE = _region("CORE_SRAM_APERTURE").tile_stride
SRAM_TILE = _region("CORE_SRAM_APERTURE").tile_bytes

NODE_SRAM0 = 10
NODE_SRAM1 = 11
NODE_HBM = 12
NODE_ERR = 13




def _dual_fnv(data: bytes) -> str:
    h0 = 0xCBF29CE484222325
    h1 = 0x9E3779B97F4A7C15
    for byte in data:
        h0 = ((h0 ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        h1 = ((h1 + ((h0 >> 31) ^ byte)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    return f"{h0:016x}-{h1:016x}"


SCENARIOS = {}


def case(name):
    def register(fn):
        if name not in backend_cases(Backend.GARNET):
            raise RuntimeError(f"unregistered Garnet case {name}")
        SCENARIOS[name] = fn
        return fn

    return register


# ---------------------------------------------------------------- seeds ----

SINGLE_SEEDS = [
    (HBM_BASE + 0x100000, 8192, 0x5A),
    (HBM_BASE + 0x200000, 8192, 0xC3),
]
SINGLE_VERIFY = [(HBM_BASE + 0x300000, 8192)]
REGION_EDGE_SEEDS = [
    (HBM_BASE + 0x500000, 8192, 0x5A),
    (HBM_BASE + 0x600000, 8192, 0xC3),
]
REGION_EDGE_VERIFY = [(HBM_BASE + 0x700000, 8192)]

DUAL_SEEDS = [
    (HBM_BASE + 0x100000, 8192, 0x5A),
    (HBM_BASE + 0x200000, 8192, 0xC3),
]
DUAL_VERIFY = [(HBM_BASE + 0x300000, 8192)]

# (src offset, dst offset, size, pattern): the destination range is never
# seeded and must equal the source payload byte for byte after the
# LOAD -> STORE round trip through the real network.
EDGE_PAIRS = [
    (0x100000, 0x200000, 1, 0x01),
    (0x100041, 0x200040, 1, 0x02),
    (0x100080, 0x200080, 31, 0x03),
    (0x1000C0, 0x2000C0, 33, 0x04),
    (0x100FF0, 0x201000, 64, 0x05),
    (0x101100, 0x201100, 511, 0x06),
    (0x101400, 0x201400, 513, 0x07),
    (0x101800, 0x201800, 700, 0x08),
]
EDGE_SEEDS = [(HBM_BASE + s, n, p) for s, _, n, p in EDGE_PAIRS]
EDGE_VERIFY = [(HBM_BASE + d, n) for _, d, n, _ in EDGE_PAIRS]

WRITE_ERROR_SEEDS = [(HBM_BASE + 0x100000, 1024, 0x99)]
WRITE_ERROR_VERIFY = [(HBM_BASE + 0x200000, 1024)]

PIN_SEEDS = [
    (HBM_BASE + 0x100000, 128, 0x11),
    (HBM_BASE + 0x100040, 128, 0x22),
]
PIN_VERIFY = [(HBM_BASE + 0x200000, 128)]


def _seed_file(path: Path, rows):
    path.write_text(
        "".join(f"0x{addr:x} {size} {pattern}\n" for addr, size, pattern in rows)
    )
    return str(path)


def _verify_file(path: Path, rows):
    path.write_text("".join(f"0x{addr:x} {size}\n" for addr, size in rows))
    return str(path)


def _expected_digest(rows, addr, size):
    for row_addr, row_size, pattern in rows:
        if row_addr <= addr and addr + size <= row_addr + row_size:
            return _dual_fnv(bytes([pattern]) * size)
    return None


# ------------------------------------------------------------- scenarios ----

def base_scenario(options):
    wc, wb = options.quota_write_contexts, options.quota_write_beats
    rc, rb = options.quota_read_contexts, options.quota_read_beats
    initiators = [
        {"src_node": 0, "src_port": 0, "router_id": 0, "default_target": NODE_ERR},
        {"src_node": 1, "src_port": 0, "router_id": 1, "default_target": NODE_ERR},
    ]
    targets = [
        {"dst_node": NODE_SRAM0, "router_id": 2},
        {"dst_node": NODE_SRAM1, "router_id": 3},
        {"dst_node": NODE_HBM, "router_id": 4},
        {"dst_node": NODE_ERR, "router_id": 5},
    ]
    quotas = [
        {"src_node": s, "src_port": 0, "dst_node": t,
         "write_contexts": wc, "write_beats": wb,
         "read_contexts": rc, "read_beats": rb}
        for s in (0, 1)
        for t in (NODE_SRAM0, NODE_SRAM1, NODE_HBM, NODE_ERR)
    ]
    return {
        "schema_version": 1,
        "name": "mesh_program",
        "driver_mode": "mesh_program",
        "endpoint_to_router": {"initiators": initiators, "targets": targets},
        "default_error_target": NODE_ERR,
        "target_ranges": [
            {"dst_node": NODE_SRAM0, "start": SRAM_BASE,
             "end": SRAM_BASE + SRAM_TILE},
            {"dst_node": NODE_SRAM1, "start": SRAM_BASE + SRAM_STRIDE,
             "end": SRAM_BASE + SRAM_STRIDE + SRAM_TILE},
            {"dst_node": NODE_HBM, "start": HBM_BASE,
             "end": HBM_BASE + HBM_WINDOW},
            {"dst_node": NODE_HBM, "start": HOST_SHARED_BASE,
             "end": HOST_SHARED_BASE + 0x100000},
        ],
        "quotas": quotas,
        "mesh_planned_extra_latency": [],
        "mesh_planned_faults": [],
    }


def write_uids(program_dir, arch, core_id, count):
    src_nodes = {0: 0, 1: 1}
    uids = predict(program_dir, arch, src_nodes)[core_id]["write"]
    fatal_if(len(uids) < count, "predicted %d write uids, need %d", len(uids), count)
    return uids


# ------------------------------------------------------------------ cases --

@case("dma_basic")
def case_dma_basic(ctx):
    """E2E-A: single-core LOAD -> GEMM timer -> DMA_STORE over Garnet."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = ctx.options.instances


@case("p2p_basic")
def case_p2p_basic(ctx):
    """E2E-B: dual-core LOAD -> GEMM -> P2P -> RECV_WAIT -> REDUCE -> STORE."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = ctx.options.instances


@case("dma_edge")
def case_dma_edge(ctx):
    """DC-17: byte/unaligned/width/burst/4KiB sweep with exact byte flow."""
    ctx.program = "dma_edge"
    ctx.seeds = EDGE_SEEDS
    ctx.verify = EDGE_VERIFY
    ctx.instances = 1


@case("region_edge")
def case_region_edge(ctx):
    ctx.program = "region_edge"
    ctx.seeds = REGION_EDGE_SEEDS
    ctx.verify = REGION_EDGE_VERIFY
    ctx.instances = 1


@case("delayed_b")
def case_delayed_b(ctx):
    """DC-21: middle write burst B delayed; descriptor still waits for it."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 8)
    ctx.scenario["mesh_planned_extra_latency"].append(
        {"target": NODE_HBM, "uid": uids[7], "cycles": 600}
    )


@case("p2p_delayed")
def case_p2p_delayed(ctx):
    """DC-22: RECV_WAIT cannot complete before the last P2P byte commits."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 9)
    ctx.scenario["mesh_planned_extra_latency"].append(
        {"target": NODE_SRAM1, "uid": uids[8], "cycles": 600}
    )


@case("read_error")
def case_read_error(ctx):
    """DC-23/DC-30: DECERR read drain, error latch, downstream cancel."""
    ctx.program = "dma_error"
    ctx.seeds = []
    ctx.verify = [(HBM_BASE + 0x200000, 128)]
    ctx.instances = 1
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    fatal_if(len(uids) < 1, "no predicted read uids")
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[0], "resp": "slverr"}
    )


@case("write_error")
def case_write_error(ctx):
    """DC-25/DC-30: SLVERR write drain keeps committed prefix bytes."""
    ctx.program = "dma_write_error"
    ctx.seeds = WRITE_ERROR_SEEDS
    ctx.verify = WRITE_ERROR_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 1)
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[0], "resp": "slverr"}
    )


@case("fence")
def case_fence(ctx):
    """DC-28: fence waits only pre-fence-accepted transactions."""
    ctx.program = "dma_fence"
    ctx.seeds = [(HBM_BASE + 0x100000, 64, 0x42)]
    ctx.verify = [(HBM_BASE + 0x200000, 4096)]
    ctx.instances = 1


@case("pin")
def case_pin(ctx):
    """DC-32: in-flight DMA pins the allocation; second admit waits."""
    ctx.program = "dma_pin"
    ctx.seeds = PIN_SEEDS
    ctx.verify = PIN_VERIFY
    ctx.instances = 1


@case("constrained")
def case_constrained(ctx):
    """Constrained buffers/quotas: real backpressure, forward progress."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = 1
    ctx.options.garnet_buffers_per_vnet = "2,2,2,2,2"
    ctx.options.axi_source_fifo_depths = "2,8,2,4,16"
    ctx.options.quota_write_contexts = 2
    ctx.options.quota_write_beats = 32
    ctx.options.quota_read_contexts = 2
    ctx.options.quota_read_beats = 64
    EFFECTIVE_ARCH.override("dma_segment_queue_depth", 2)


@case("fence_scopes")
def case_fence_scopes(ctx):
    """Five-scope fence matrix: in-scope pre-fence tags block, other scopes
    and post-fence transactions do not, HOST_SHARED writes are
    distinguishable from HBM stores, and ALL_INSTANCE spans cores."""
    ctx.program = "fence_scopes"
    ctx.seeds = [(HBM_BASE + 0x100000, 16, 0x77)]
    ctx.verify = [(HBM_BASE + 0x200000, 16)]


@case("cross_error")
def case_cross_error(ctx):
    """Instance-global error drain: core0's LOAD DECERRs; core1's waiter on
    the failed producer must be cancelled cross-core, not hang."""
    ctx.program = "cross_fault"
    ctx.seeds = [(HBM_BASE + 0x100000, 128, 0x31), (HBM_BASE + 0x100080, 128, 0x32)]
    ctx.verify = []
    ctx.instances = 1
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    fatal_if(len(uids) < 1, "no predicted read uids")
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[0], "resp": "slverr"}
    )


@case("cross_error_first_instance")
def case_cross_error_first_instance(ctx):
    """instances=2: the per-UID fault hits only instance 1; instance 2
    runs to normal completion after the error latch reset."""
    ctx.program = "cross_fault"
    ctx.seeds = [(HBM_BASE + 0x100000, 128, 0x31), (HBM_BASE + 0x100080, 128, 0x32)]
    ctx.verify = []
    ctx.instances = 2
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[0], "resp": "slverr"}
    )


@case("repeat_error")
def case_repeat_error(ctx):
    """REPEAT generation fails mid-flight: gen0 completes, the faulted gen1
    read drains the instance through the REPEAT error terminal path."""
    ctx.program = "repeat_error"
    ctx.seeds = [(HBM_BASE + 0x100000, 128, 0x51), (HBM_BASE + 0x200000, 128, 0x52)]
    ctx.verify = []
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 1)
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[0], "resp": "slverr"}
    )


@case("p2p_reuse")
def case_p2p_reuse(ctx):
    """Sequential P2P transfers reusing one destination range must both
    commit; expectations retire with their transfer."""
    ctx.program = "p2p_reuse"
    ctx.seeds = []
    ctx.verify = []


def _load_saturation_case(ctx, shape):
    """Saturation benchmark: both cores stream their LOAD plan from the
    shared HBM node; optional uniform HBM read latency forms the sweep's
    latency axis."""
    ctx.program = "load_saturation_" + shape
    ctx.instances = 1
    ctx.seeds = []
    ctx.verify = []
    from mesh_ir.golden_programs import (
        LOAD_SATURATION_HBM_BASE,
        LOAD_SATURATION_TENSOR_BYTES,
        load_saturation_layout,
    )
    for core in (0, 1):
        for index, (rows, row_bytes, stride) in enumerate(
                load_saturation_layout(shape)):
            base = HBM_BASE + LOAD_SATURATION_HBM_BASE + (
                core * 8 + index) * LOAD_SATURATION_TENSOR_BYTES
            ctx.seeds.append(
                (base, (rows - 1) * stride + row_bytes, 0x41 + core * 0x10))
    if ctx.options.load_latency_cycles > 0:
        for core in (0, 1):
            for uid in predict(
                    ctx.program_dir, ctx.arch, {0: 0, 1: 1})[core]["read"]:
                ctx.scenario["mesh_planned_extra_latency"].append(
                    {"target": NODE_HBM, "uid": uid,
                     "cycles": ctx.options.load_latency_cycles})


@case("load_saturation_contiguous")
def case_load_saturation_contiguous(ctx):
    _load_saturation_case(ctx, "contiguous")


@case("load_saturation_multi_tensor")
def case_load_saturation_multi_tensor(ctx):
    _load_saturation_case(ctx, "multi_tensor")


@case("load_saturation_strided")
def case_load_saturation_strided(ctx):
    _load_saturation_case(ctx, "strided")


@case("read_outstanding_window")
def case_read_outstanding_window(ctx):
    """Sliding read window: a 6 KiB LOAD splits into 24 8-beat bursts; the
    4-deep read outstanding window refills on RLAST (not on SRAM commit),
    even when the local SRAM write service is slowed."""
    ctx.program = "read_window"
    ctx.seeds = [(HBM_BASE + 0x100000, 6 * 1024, 0x63)]
    ctx.verify = []
    ctx.instances = 1
    EFFECTIVE_ARCH.override("dma_segment_queue_depth", 8)
    EFFECTIVE_ARCH.override("axi_id_bits", 2)
    EFFECTIVE_ARCH.override("sram_write_bytes_per_cycle_per_bank", 1)
    EFFECTIVE_ARCH.override("dma_read_outstanding", 4)
    EFFECTIVE_ARCH.override("dma_write_outstanding", 4)
    ctx.checks = ["conservation", "quiescence", "completion_timing",
                  "read_window_slides"]
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    for uid in uids:
        ctx.scenario["mesh_planned_extra_latency"].append(
            {"target": NODE_HBM, "uid": uid, "cycles": 400}
        )


@case("dma_zero")
def case_dma_zero(ctx):
    """Zero-length DMA: rows==0 and row_bytes==0 LOAD/FILL/P2P complete at
    the normal completion point with zero traffic; zero-byte P2P retires its
    transfer expectation without any aperture range."""
    ctx.program = "zero_dma"
    ctx.seeds = []
    ctx.verify = []


@case("dma_shapes")
def case_dma_shapes(ctx):
    """Gap fix: PREFETCH + FILL + multi-row strided P2P/STORE + one
    cross-core NORMAL event dependency over the real network."""
    ctx.program = "dma_shapes"
    ctx.seeds = [(HBM_BASE + 0x100000, 64, 0x3C)]
    ctx.verify = [(HBM_BASE + 0x300000, 64), (HBM_BASE + 0x300100, 64)]
    ctx.instances = 1


@case("p2p_persist")
def case_p2p_persist(ctx):
    """Gap fix: every instance must re-observe its P2P transfers."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = 2


@case("sram_persist")
def case_sram_persist(ctx):
    """DC-33: second instance reuses persisted SRAM/byte state cleanly."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 2


# ------------------------------------------------------------ assertions --

def _command_of(schedule, core_id, opcode_name):
    value = int(getattr(A.OPCODE, opcode_name))
    for command in schedule["sections"]["COMMANDS"]:
        if command["core_id"] == core_id and command["opcode"] == value:
            return command
    return None


def check_conservation(ctx, result, expected_rows, out):
    instances = ctx.instances
    bridges = result.get("bridges", [])
    fatal_if(not bridges, "result JSON has no bridge section")

    # Error-drained runs may cancel descriptors before they submit; only
    # descriptors with actual traffic rows contribute to the beat oracle.
    ran = {t["descriptor_id"] for t in result["transport"]}
    w_beats = sum(
        r["w_beats"] for r in expected_rows.values()
        if r["descriptor_id"] in ran
    ) * ctx.instances
    r_beats = sum(
        r["r_beats"] for r in expected_rows.values()
        if r["descriptor_id"] in ran
    ) * ctx.instances
    accepted_w = sum(b["w_accepted"] for b in bridges)
    consumed_r = sum(b["r_beats_consumed"] for b in bridges)
    fatal_if(
        accepted_w != w_beats,
        "W beat conservation: accepted %d != oracle %d" % (accepted_w, w_beats),
    )
    fatal_if(
        consumed_r != r_beats,
        "R beat conservation: consumed %d != oracle %d" % (consumed_r, r_beats),
    )
    for bridge in bridges:
        fatal_if(
            bridge["write_bursts_submitted"] != bridge["b_consumed"]
            or bridge["read_bursts_submitted"] != bridge["read_bursts_completed"],
            "AXI burst conservation violated on core %d" % bridge["core_id"],
        )
        fatal_if(
            bridge["aw_accepted"] != bridge["write_bursts_submitted"]
            or bridge["ar_accepted"] != bridge["read_bursts_submitted"],
            "bridge accept/submission mismatch on core %d" % bridge["core_id"],
        )
        fatal_if(bridge["pending_aw"] or bridge["pending_ar"]
                 or bridge["outstanding_writes"] or bridge["outstanding_reads"]
                 or not bridge["idle"],
                 "bridge on core %d not drained" % bridge["core_id"])

    actual = {row["descriptor_id"]: row for row in result["transport"]}
    # Error-drained runs may cancel descriptors before they submit traffic.
    expected_ids = set(
        d for d, row in expected_rows.items() if row["useful_bytes"] > 0)
    zero_ids = set(
        d for d, row in expected_rows.items() if row["useful_bytes"] == 0)
    fatal_if(
        not set(actual.keys()) <= (expected_ids | zero_ids),
        "traffic descriptors outside the oracle: %s"
        % sorted(set(actual.keys()) - (expected_ids | zero_ids)),
    )
    fatal_if(
        not result.get("error_drained")
        and sorted(actual.keys()) != sorted(expected_ids | zero_ids),
        "traffic descriptor set mismatch",
    )
    for descriptor_id, row in expected_rows.items():
        if descriptor_id not in actual:
            continue
        got = actual[descriptor_id]
        useful = row["useful_bytes"] * instances
        if useful == 0:
            zero_fields = (
                got["read_bytes"] + got.get("read_discarded_bytes", 0),
                got["write_bytes"] + got.get("write_drained_uncommitted_bytes", 0),
                got["fill_bytes"],
                got["p2p_bytes"],
                got["read_bursts"],
                got["write_bursts"],
                got["p2p_bursts"],
            )
            fatal_if(
                any(zero_fields),
                "zero-byte descriptor %d produced traffic: %s",
                descriptor_id, zero_fields,
            )
            continue
        bursts = row["bursts"] * instances
        kind = row["kind"]
        if kind == 5:
            if got["fill_bytes"] != useful:
                fatal("fill traffic mismatch on descriptor %d", descriptor_id)
            continue
        if kind == 3:
            if got["p2p_bytes"] != useful or got["p2p_bursts"] != bursts:
                fatal("p2p traffic mismatch on descriptor %d", descriptor_id)
        elif kind == 2:
            committed = got["write_bytes"]
            if got.get("error_code"):
                committed += got["write_drained_uncommitted_bytes"]
            if committed != useful or got["write_bursts"] != bursts:
                fatal("store traffic mismatch on descriptor %d", descriptor_id)
        else:
            if got["read_bytes"] + got["read_discarded_bytes"] != useful \
                    or got["read_bursts"] != bursts:
                fatal("load traffic mismatch on descriptor %d", descriptor_id)

    for core in result["cores"]:
        if result.get("error_drained"):
            completed_ids = set(core["completed_command_ids"])
            cancelled_ids = set(core["cancelled_command_ids"])
            scheduled_ids = {
                c["command_id"] for c in ctx.schedule["sections"]["COMMANDS"]
                if c["core_id"] == core["core_id"]
            }
            fatal_if(
                completed_ids & cancelled_ids,
                "command %s has two terminal outcomes on core %d"
                % (sorted(completed_ids & cancelled_ids)[:4], core["core_id"]),
            )
            fatal_if(
                (completed_ids | cancelled_ids) != scheduled_ids,
                "core %d commands without a terminal outcome: %s"
                % (core["core_id"],
                   sorted(scheduled_ids - completed_ids - cancelled_ids)[:6]),
            )
            fatal_if(
                core["live_commands"],
                "live commands survive the drain on core %d" % core["core_id"],
            )
        else:
            fatal_if(
                core["commands_issued"] != core["commands_completed"]
                or core["live_commands"],
                "command conservation violated on core %d" % core["core_id"],
            )
        fatal_if(not core["dma_idle"], "dma engine not idle on core %d"
                 % core["core_id"])
    out.append("conservation: PASS")


def check_completion_timing(ctx, result, expected_rows, out):
    """DC-18/DC-19: LOAD terminates only after the last R beat's local SRAM
    commit; STORE's local read precedes its W traffic; every descriptor's
    done tick dominates its local commit."""
    by_descriptor = {
        d["descriptor_id"]: d
        for d in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    }
    traffic_error = {
        t["descriptor_id"]: t.get("error_code", 0)
        for t in result["transport"]
    }
    loads = [r for r in expected_rows.values() if r["kind"] == 1]
    stores = [r for r in expected_rows.values() if r["kind"] == 2]
    for row in result.get("dma_timings", []):
        descriptor = by_descriptor.get(row["descriptor_id"])
        if descriptor is None:
            continue
        kind = descriptor["kind"]
        oracle_row = expected_rows.get(row["descriptor_id"])
        bursts = oracle_row["bursts"] if oracle_row else 0
        for field in ("first_ar_tick", "first_aw_tick", "first_w_tick",
                      "first_b_tick"):
            fatal_if(field not in row,
                     "descriptor %d timing row missing %s",
                     row["descriptor_id"], field)
        if kind in (1, 4):  # LOAD/PREFETCH
            if traffic_error.get(row["descriptor_id"]):
                # Errored loads discard every beat: terminal after the
                # drained RLAST, no local commit exists.
                fatal_if(
                    row["local_commit_tick"] != 0
                    or row["last_r_tick"] > row["done_tick"],
                    "errored LOAD drain violation on descriptor %d: "
                    "r=%d commit=%d done=%d" % (row["descriptor_id"],
                                                row["last_r_tick"],
                                                row["local_commit_tick"],
                                                row["done_tick"]),
                )
                continue
            if oracle_row and oracle_row["useful_bytes"] == 0:
                fatal_if(
                    row["done_tick"] == 0 or row["last_r_tick"] != 0
                    or row["first_ar_tick"] != 0
                    or row["first_aw_tick"] != 0
                    or row["first_w_tick"] != 0
                    or row["first_b_tick"] != 0
                    or row["local_commit_tick"] != 0,
                    "zero-byte LOAD descriptor %d must complete with no "
                    "AXI traffic: ar=%d r=%d done=%d"
                    % (row["descriptor_id"], row["first_ar_tick"],
                       row["last_r_tick"], row["done_tick"]),
                )
                continue
            fatal_if(
                row["first_aw_tick"] != 0 or row["first_w_tick"] != 0
                or row["first_b_tick"] != 0,
                "read descriptor %d exported write-direction timing"
                % row["descriptor_id"],
            )
            fatal_if(row["last_r_tick"] == 0,
                     "LOAD descriptor %d missing R tick",
                     row["descriptor_id"])
            fatal_if(
                row["local_commit_tick"] == 0,
                "LOAD descriptor %d missing commit tick",
                row["descriptor_id"],
            )
            fatal_if(
                not (row["first_ar_tick"] <= row["last_r_tick"]
                     <= row["local_commit_tick"] <= row["done_tick"]),
                "LOAD completion-point violation on descriptor %d: "
                "ar=%d r=%d commit=%d done=%d"
                % (row["descriptor_id"], row["first_ar_tick"],
                   row["last_r_tick"], row["local_commit_tick"],
                   row["done_tick"]),
            )
        elif kind in (2, 3):  # STORE/P2P: writes
            if oracle_row and oracle_row["useful_bytes"] == 0:
                fatal_if(
                    row["done_tick"] == 0 or row["first_aw_tick"] != 0
                    or row["first_w_tick"] != 0 or row["first_b_tick"] != 0
                    or row["first_ar_tick"] != 0
                    or row["last_r_tick"] != 0
                    or row["local_commit_tick"] != 0,
                    "zero-byte write descriptor %d must complete with no "
                    "AXI traffic" % row["descriptor_id"],
                )
                continue
            fatal_if(
                row["first_ar_tick"] != 0 or row["last_r_tick"] != 0,
                "write descriptor %d exported read-direction timing"
                % row["descriptor_id"],
            )
            fatal_if(
                row["first_aw_tick"] == 0 or row["first_w_tick"] == 0
                or row["first_b_tick"] == 0,
                "write descriptor %d missing first AW/W/B tick",
                row["descriptor_id"],
            )
            fatal_if(
                row["local_commit_tick"] == 0,
                "write descriptor %d missing local SRAM read tick",
                row["descriptor_id"],
            )
            if bursts == 1:
                fatal_if(
                    row["first_w_tick"] < row["local_commit_tick"],
                    "single-burst W beats must follow the local SRAM read "
                    "on descriptor %d: read=%d first_w=%d"
                    % (row["descriptor_id"], row["local_commit_tick"],
                       row["first_w_tick"]),
                )
            fatal_if(
                row["local_commit_tick"] > row["done_tick"]
                or row["first_b_tick"] > row["done_tick"]
                or row["first_w_tick"] > row["done_tick"],
                "STORE/P2P completion before local read/first W/B on "
                "descriptor %d" % row["descriptor_id"],
            )
        elif kind == 5:  # LOCAL_FILL: SRAM-only, no AXI traffic at all
            fatal_if(
                row["done_tick"] == 0 or row["first_aw_tick"] != 0
                or row["first_w_tick"] != 0 or row["first_b_tick"] != 0
                or row["first_ar_tick"] != 0 or row["last_r_tick"] != 0,
                "FILL descriptor %d must be SRAM-only" % row["descriptor_id"],
            )
            if oracle_row and oracle_row["useful_bytes"] == 0:
                fatal_if(
                    row["local_commit_tick"] != 0,
                    "zero-byte FILL descriptor %d reserved SRAM"
                    % row["descriptor_id"],
                )
            else:
                fatal_if(
                    row["local_commit_tick"] == 0
                    or row["local_commit_tick"] > row["done_tick"],
                    "FILL descriptor %d completion precedes SRAM commit"
                    % row["descriptor_id"],
                )
    if not result.get("error_drained"):
        if loads and not any(
            r for r in result.get("dma_timings", [])
            if by_descriptor.get(r["descriptor_id"], {}).get("kind") == 1
        ):
            fatal("no LOAD timing rows exported")
        if stores and not any(
            r for r in result.get("dma_timings", [])
            if by_descriptor.get(r["descriptor_id"], {}).get("kind") == 2
        ):
            fatal("no STORE timing rows exported")
    out.append("completion timing: PASS")


def check_quiescence(ctx, result, expected_rows, out):
    garnet = result.get("garnet")
    fatal_if(garnet is None, "result JSON has no garnet quiescence snapshot")
    fatal_if(not garnet["quiescent"], "garnet not quiescent: %s" % garnet)
    fatal_if(garnet["credit_deficit"] != 0, "garnet credit deficit != 0")
    for bridge in result.get("bridges", []):
        fatal_if(not bridge["idle"], "bridge not idle")
    out.append("quiescence: PASS")


def check_e2e_a_content(ctx, result, expected_rows, out):
    endpoint = result["memory_endpoint"]
    descriptors = {
        d["descriptor_id"]: d
        for d in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    }
    actual = {t["descriptor_id"]: t for t in result["transport"]}
    for row in ctx.expected.values():
        if row["kind"] != 1:
            continue
        descriptor = descriptors[row["descriptor_id"]]
        src_abs = HBM_BASE + descriptor["src"]["offset_bytes"]
        pattern = None
        for addr, size, pat in ctx.seeds:
            if addr <= src_abs and src_abs + row["useful_bytes"] <= addr + size:
                pattern = pat
        if pattern is None:
            continue
        want = _dual_fnv(bytes([pattern]) * row["useful_bytes"])
        got = actual[row["descriptor_id"]]
        fatal_if(got["payload_digest"] != want,
                 "load content mismatch on descriptor %d: got %s want %s"
                 % (row["descriptor_id"], got["payload_digest"], want))
    verify = endpoint["verifies"]
    fatal_if(not verify, "no verify rows")
    row = verify[0]
    zeros_after = _dual_fnv(bytes(row["size"] - 16))
    fatal_if(row["after16_digest"] == zeros_after,
             "store result did not cover the full result span: %s", row)
    out.append("e2e-a content: PASS")


def check_edge_bytes(ctx, result, expected_rows, out):
    endpoint = result["memory_endpoint"]
    # Each edge pair round-trips one distinct size; the destination (never
    # seeded) must equal the source payload byte for byte.
    by_dest = {HBM_BASE + d: p for _, d, _, p in EDGE_PAIRS}
    for row in endpoint["verifies"]:
        pattern = by_dest.get(row["address"])
        fatal_if(pattern is None,
                 "edge verify row at %#x has no matching pair"
                 % row["address"])
        want = _dual_fnv(bytes([pattern]) * row["size"])
        fatal_if(row["digest"] != want,
                 "edge byte flow mismatch at %#x: got %s want %s"
                 % (row["address"], row["digest"], want))
    out.append("edge bytes: PASS")


def check_p2p_commit(ctx, result, expected_rows, out):
    apertures = result["apertures"]
    fatal_if(len(apertures) < 2, "missing SRAM apertures in result")
    p2p_bytes = sum(
        t["p2p_bytes"] for t in result["transport"] if t["p2p_bytes"])
    committed = sum(a["committed_valid_bytes"] for a in apertures)
    fatal_if(committed != p2p_bytes,
             "aperture committed bytes %d != p2p bytes %d" % (committed, p2p_bytes))
    for aperture in apertures:
        for transfer in aperture["transfers"]:
            fatal_if(transfer["commit_tick"] == 0,
                     "transfer %d never committed" % transfer["transfer_id"])
    out.append("p2p commit: PASS")


def check_e2e_b_order(ctx, result, expected_rows, out):
    schedule = ctx.schedule
    core = next(c for c in result["cores"] if c["core_id"] == 1)
    ticks = {int(k): v for k, v in core["command_done_ticks"].items()}
    recv = _command_of(schedule, 1, "RECV_WAIT")
    reduce_cmd = _command_of(schedule, 1, "LOCAL_REDUCE")
    store = _command_of(schedule, 1, "DMA_STORE")
    fatal_if(not (recv and reduce_cmd and store), "missing E2E-B commands")
    fatal_if(
        not (ticks[recv["command_id"]] <= ticks[reduce_cmd["command_id"]]
             <= ticks[store["command_id"]]),
        "E2E-B ordering violated: recv=%d reduce=%d store=%d",
        ticks[recv["command_id"]], ticks[reduce_cmd["command_id"]],
        ticks[store["command_id"]],
    )
    aperture = next(a for a in result["apertures"] if a["core_id"] == 1)
    for transfer in aperture["transfers"]:
        fatal_if(
            ticks[recv["command_id"]] < transfer["commit_tick"],
            "RECV_WAIT completed before its transfer committed",
        )
    out.append("e2e-b order: PASS")


def check_recv_wait_order(ctx, result, expected_rows, out):
    check_e2e_b_order(ctx, result, expected_rows, out)
    out.append("recv-wait order: PASS")


def check_b_reorder(ctx, result, expected_rows, out):
    bridge = next(b for b in result["bridges"] if b["core_id"] == 0)
    order = bridge["b_retire_order"]
    fatal_if(sorted(order) != list(range(min(order), min(order) + len(order))),
             "b retire order is not a permutation: %s" % order)
    fatal_if(order == sorted(order),
             "delayed middle B did not reorder the retire order: %s" % order)
    fatal_if(bridge["b_consumed"] != bridge["write_bursts_submitted"],
             "not all write bursts retired after the delayed B")
    out.append("b reorder: PASS")


def check_read_error_drain(ctx, result, expected_rows, out):
    fatal_if(not result["error_drained"], "instance did not error-drain")
    core = result["cores"][0]
    fatal_if(core["commands_errored"] != 1,
             "expected exactly one errored command")
    fatal_if(core["commands_cancelled"] < 2,
             "downstream commands were not cancelled")
    load_rows = [r for r in ctx.expected.values() if r["kind"] == 1]
    discarded = sum(t["read_discarded_bytes"] for t in result["transport"])
    fatal_if(discarded != sum(r["useful_bytes"] for r in load_rows),
             "discarded read bytes %d != useful %d"
             % (discarded, sum(r["useful_bytes"] for r in load_rows)))
    endpoint = result["memory_endpoint"]
    zeros = _dual_fnv(bytes(128))
    fatal_if(endpoint["verifies"][0]["digest"] != zeros,
             "error drain touched the untouched output range")
    out.append("read error drain: PASS")


def check_write_error_drain(ctx, result, expected_rows, out):
    fatal_if(not result["error_drained"], "instance did not error-drain")
    core = result["cores"][0]
    fatal_if(core["commands_errored"] != 1,
             "expected exactly one errored command")
    bridge = next(b for b in result["bridges"] if b["core_id"] == 0)
    fatal_if(bridge["b_consumed"] != 2 or bridge["b_error_count"] != 1,
             "write error did not drain exactly one faulted B")
    store = next(t for t in result["transport"] if t["write_bytes"] > 0)
    fatal_if(store["write_drained_uncommitted_bytes"] != 512,
             "drained-uncommitted bytes %d != 512"
             % store["write_drained_uncommitted_bytes"])
    endpoint = result["memory_endpoint"]
    fatal_if(store["write_bytes"] != 512,
             "committed prefix bytes %d != 512" % store["write_bytes"])
    fatal_if(endpoint["committed_valid_bytes"] != 512,
             "endpoint committed bytes %d != committed prefix 512"
             % endpoint["committed_valid_bytes"])
    out.append("write error drain: PASS")


def check_fence_window(ctx, result, expected_rows, out):
    core = result["cores"][0]
    ticks = {int(k): v for k, v in core["command_done_ticks"].items()}
    fence = _command_of(ctx.schedule, 0, "AXI_FENCE")
    load = _command_of(ctx.schedule, 0, "DMA_LOAD")
    store = _command_of(ctx.schedule, 0, "DMA_STORE")
    fatal_if(not (fence and load and store), "missing fence program commands")
    fatal_if(
        ticks[fence["command_id"]] < ticks[load["command_id"]],
        "fence completed before its pre-fence transaction",
    )
    fatal_if(
        ticks[fence["command_id"]] >= ticks[store["command_id"]],
        "fence waited for the post-fence transaction (scope violation)",
    )
    out.append("fence window: PASS")


def check_fence_scopes(ctx, result, expected_rows, out):
    """DC-28 extension: per-scope fence windows across both cores."""
    def ticks_of(core_id):
        for core in result["cores"]:
            if core["core_id"] == core_id:
                return ({int(k): v for k, v in core["command_done_ticks"].items()},
                        {int(k): v for k, v in core["command_issue_ticks"].items()})
        fatal("core %d missing from result", core_id)

    def cmd_of(core_id, opcode, index=0):
        seen = 0
        for command in ctx.schedule["sections"]["COMMANDS"]:
            if command["core_id"] == core_id and command["opcode"] == opcode:
                if seen == index:
                    return command
                seen += 1
        fatal("no %s on core %d", opcode, core_id)

    def scope_of(command):
        attrs = {i + 1: a for i, a in enumerate(ctx.schedule["sections"]["OP_ATTRS"])}
        return attrs[command["attr_index"]]["fence_scope"]

    done0, issue0 = ticks_of(0)
    done1, _ = ticks_of(1)
    load = cmd_of(0, 4)
    store = cmd_of(0, 5)
    p2p = cmd_of(0, 6)
    host_store = cmd_of(1, 5)
    fences = [c for c in ctx.schedule["sections"]["COMMANDS"]
              if c["core_id"] == 0 and c["opcode"] == 9]
    by_scope = {scope_of(f): f for f in fences}
    fatal_if(set(by_scope) != {1, 2, 3, 4, 5},
             "fence_scopes program must exercise all five scopes")
    fatal_if(done0[by_scope[1]["command_id"]] < done0[load["command_id"]],
             "DMA_READ fence did not wait for the pre-fence LOAD")
    fatal_if(done0[by_scope[1]["command_id"]] >= done0[store["command_id"]],
             "DMA_READ fence waited for the out-of-scope STORE")
    fatal_if(done0[by_scope[2]["command_id"]] < done0[store["command_id"]],
             "DMA_WRITE fence did not wait for the pre-fence STORE")
    fatal_if(done0[by_scope[3]["command_id"]] < done0[p2p["command_id"]],
             "P2P fence did not wait for the pre-fence P2P push")
    fatal_if(done0[by_scope[4]["command_id"]] >= done1[host_store["command_id"]],
             "HOST_SHARED_WRITE fence waited for an HBM-store-only prefix")
    all_done = done0[by_scope[5]["command_id"]]
    if issue0[by_scope[5]["command_id"]] <= done1[host_store["command_id"]]:
        fatal_if(all_done < done1[host_store["command_id"]],
                 "ALL_INSTANCE fence did not span the cross-core store")
    out.append("fence scopes: PASS")


def check_read_window_slides(ctx, result, expected_rows, out):
    limit = EFFECTIVE_ARCH.dma_read_outstanding
    bursts = result.get("burst_timings", [])
    fatal_if(not bursts, "missing per-burst evidence")
    for row in bursts:
        length = row["beats"] * row["beat_bytes"]
        fatal_if(row["beats"] > 8 or row["beats"] <= 0 or
                 row["address"] // 4096 != (row["address"] + length - 1) // 4096,
                 "burst violates beat/page bounds")
    reads = sorted((row for row in bursts if row["channel"] == "AR"),
                   key=lambda row: (row["ar_aw_tick"], row["ordinal"]))
    fatal_if(len(reads) <= limit, "missing refill burst")
    fatal_if(len({row["core_id"] for row in reads}) != 1,
             "read-window stimulus must use one initiator")
    first = reads[0]
    release = min(row["response_tick"] for row in reads)
    fatal_if(release <= 0 or first["commit_tick"] <= 0,
             "missing RLAST or first burst commit")
    fatal_if(sum(row["ar_aw_tick"] < release for row in reads) != limit,
             "first RLAST must follow exactly the configured number of ARs")
    refill = reads[limit]
    fatal_if(not (release <= refill["ar_aw_tick"] < first["commit_tick"]),
             "refill must precede first burst SRAM commit")
    fatal_if(refill["axi_id"] != first["axi_id"], "refill did not reuse first ID")
    fatal_if(len({row["axi_id"] for row in reads}) != limit,
             "read-window stimulus did not use a bounded ID pool")
    previous = {}
    for row in reads:
        prior = previous.get(row["axi_id"])
        fatal_if(prior is not None and row["ar_aw_tick"] < prior["response_tick"],
                 "ID reused before RLAST consumption")
        previous[row["axi_id"]] = row
    peaks = [b["peak_read_outstanding"] for b in result["bridges"]]
    fatal_if(not peaks or max(peaks) != limit, "adapter read window peak differs from limit")
    out.append("read window slides: PASS")


def check_p2p_reuse_commits(ctx, result, expected_rows, out):
    p2p_rows = [r for r in result["transport"] if r["p2p_bytes"] > 0]
    fatal_if(len(p2p_rows) != 2,
             "expected both reused-range transfers to commit, got %d"
             % len(p2p_rows))
    total = sum(r["p2p_bytes"] for r in p2p_rows)
    fatal_if(total != 64, "reused-range commits carried %d bytes" % total)
    out.append("p2p reuse commits: PASS")


def check_first_instance_error_only(ctx, result, expected_rows, out):
    ran = {t["descriptor_id"] for t in result["transport"]}
    fatal_if(not ran, "no descriptors ran")
    bridges = result.get("bridges", [])
    consumed = sum(b["r_beats_consumed"] for b in bridges)
    per_instance = sum(
        r["r_beats"] for r in expected_rows.values()
        if r["descriptor_id"] in ran
    )
    fatal_if(consumed <= 0 or consumed > per_instance * ctx.instances,
             "R beat accounting out of range: %d (per instance %d)"
             % (consumed, per_instance))
    fatal_if(result.get("error_drained"),
             "second instance should have completed normally after the "
             "latch reset")
    for record in result.get("instances", []):
        for core_id, ledger in record["cores"].items():
            fatal_if(ledger["completed"] + ledger["errored"] +
                     ledger["cancelled"] == 0,
                     "instance %s core %s has no terminal outcomes"
                     % (record["instance"], core_id))
    for record in result.get("instances", []):
        if record["instance"] == 1:
            for core_id, ledger in record["cores"].items():
                fatal_if(ledger["cancelled"] == 0 and ledger["errored"] == 0,
                         "instance 1 core %s should show the error drain"
                         % core_id)
            break
    for core in result["cores"]:
        fatal_if(core["live_commands"],
                 "live commands survive on core %d" % core["core_id"])
        fatal_if(core["live_commands"], "live commands on core %d"
                 % core["core_id"])
    records = result.get("instances", [])
    fatal_if(len(records) != 2, "expected two instance ledger records")
    scheduled = {}
    for command in ctx.schedule["sections"]["COMMANDS"]:
        scheduled[command["core_id"]] = scheduled.get(command["core_id"], 0) + 1
    second = records[1]["cores"]
    for core_id, count in scheduled.items():
        ledger = second[str(core_id)]
        fatal_if(
            ledger["completed"] != count or ledger["errored"] != 0
            or ledger["cancelled"] != 0,
            "second instance ledger for core %d is not isolated: %s"
            % (core_id, ledger),
        )
    out.append("first-instance error only: PASS")


def check_instance_ledgers(ctx, result, expected_rows, out):
    records = result.get("instances", [])
    fatal_if(len(records) != ctx.instances,
             "instance ledger count %d != %d" % (len(records), ctx.instances))
    scheduled = {}
    for command in ctx.schedule["sections"]["COMMANDS"]:
        scheduled[command["core_id"]] = scheduled.get(command["core_id"], 0) + 1
    for record in records:
        for core_id, count in scheduled.items():
            ledger = record["cores"][str(core_id)]
            fatal_if(
                ledger["completed"] != count or ledger["errored"] != 0
                or ledger["cancelled"] != 0,
                "instance %d core %d ledger is not isolated: %s"
                % (record["instance"], core_id, ledger),
            )
    out.append("instance ledgers: PASS")


def check_cross_error_drain(ctx, result, expected_rows, out):
    fatal_if(not result["error_drained"], "instance did not error-drain")
    core0 = next(c for c in result["cores"] if c["core_id"] == 0)
    core1 = next(c for c in result["cores"] if c["core_id"] == 1)
    fatal_if(core0["commands_errored"] < 1,
             "core0 did not terminal-error the faulted LOAD")
    fatal_if(core1["commands_cancelled"] < 1 or core1["live_commands"],
             "core1 waiter was not cancelled cross-core")
    fatal_if(core0["live_commands"] or core1["live_commands"],
             "live commands survive the instance drain")
    out.append("cross-core error drain: PASS")


def check_repeat_error_drain(ctx, result, expected_rows, out):
    fatal_if(not result["error_drained"], "instance did not error-drain")
    core0 = next(c for c in result["cores"] if c["core_id"] == 0)
    fatal_if(core0["commands_errored"] < 1,
             "faulted generation did not terminal-error")
    fatal_if(core0["live_commands"],
             "repeat gate or live command survived the drain")
    repeat = next(c for c in ctx.schedule["sections"]["COMMANDS"]
                  if c["opcode"] == 20)
    terminal = (set(core0["completed_command_ids"])
                | set(core0["cancelled_command_ids"]))
    fatal_if(repeat["command_id"] not in terminal,
             "REPEAT command never reached a terminal outcome")
    fatal_if(core0["commands_issued"] > core0["commands_completed"]
             + core0["commands_cancelled"],
             "repeat error accounting: issued %d vs completed %d + cancelled %d"
             % (core0["commands_issued"], core0["commands_completed"],
                core0["commands_cancelled"]))
    out.append("repeat error drain: PASS")


def check_pin_serialize(ctx, result, expected_rows, out):
    core = result["cores"][0]
    ticks = {int(k): v for k, v in core["command_done_ticks"].items()}
    loads = [
        c for c in ctx.schedule["sections"]["COMMANDS"]
        if c["core_id"] == 0 and c["opcode"] == int(A.OPCODE.DMA_LOAD)
    ]
    fatal_if(len(loads) != 2, "pin program must have exactly two loads")
    first, second = loads
    fatal_if(
        ticks[second["command_id"]] <= ticks[first["command_id"]],
        "second load did not serialize behind the pinned allocation",
    )
    endpoint = result["memory_endpoint"]
    want = _dual_fnv(bytes([0x22]) * 128)
    fatal_if(endpoint["verifies"][0]["digest"] != want,
             "store did not carry the last-writer payload")
    out.append("pin serialize: PASS")


def check_shapes_content(ctx, result, expected_rows, out):
    endpoint = result["memory_endpoint"]
    rows = {(r["address"], r["size"]): r for r in endpoint["verifies"]}
    # The accumulator buffer was zero-filled before the reduce; the store
    # rows carry zero tail bytes with the digest prefix on row 0 only.
    row0 = rows.get((HBM_BASE + 0x300000, 64))
    fatal_if(row0 is None, "shapes verify row 0 missing")
    fatal_if(row0["after16_digest"] == _dual_fnv(bytes(48)),
             "shapes store row 0 tail not covered by the result stream: %s"
             % row0)
    row1 = rows.get((HBM_BASE + 0x300100, 64))
    fatal_if(row1 is None, "shapes verify row 1 missing")
    fatal_if(row1["digest"] == _dual_fnv(bytes(64)),
             "shapes store row 1 shows no content flow: %s" % row1)
    # The P2P payload itself must carry the peer-bound fill bytes (the
    # 64-bit little-endian pattern 0xA5 expands to a5 followed by zeros).
    fill_pattern = bytes([0xA5]) + bytes(7)
    p2p = next(t for t in result["transport"] if t["p2p_bytes"] > 0)
    fatal_if(p2p["payload_digest"] != _dual_fnv(fill_pattern * 16),
             "p2p content mismatch: %s" % p2p["payload_digest"])
    fill_row_src = next(t for t in result["transport"] if t["fill_bytes"] == 128
                        and t["descriptor_id"] == 2)
    fatal_if(fill_row_src["payload_digest"] != p2p["payload_digest"],
             "fill and p2p payload digests diverged")
    # PREFETCH payload digest equals the seeded source content.
    pf = next(t for t in result["transport"]
              if t["descriptor_id"] == 1)
    fatal_if(pf["payload_digest"] != _dual_fnv(bytes([0x3C]) * 64),
             "prefetch content mismatch: %s" % pf["payload_digest"])
    # FILL accounting present with no AXI beats.
    fill_row = next(t for t in result["transport"] if t["fill_bytes"] > 0)
    fatal_if(fill_row["fill_bytes"] != 128 or fill_row["read_bursts"]
             or fill_row["write_bursts"] or fill_row["p2p_bursts"],
             "fill row must be local-only: %s" % fill_row)
    out.append("shapes content: PASS")


def check_cross_core_order(ctx, result, expected_rows, out):
    schedule = ctx.schedule
    value = int(A.OPCODE.EVENT_WAIT)
    waiter = _command_of(schedule, 1, "EVENT_WAIT")
    fatal_if(waiter is None, "shapes program lost its cross-core waiter")
    core0 = next(c for c in result["cores"] if c["core_id"] == 0)
    core1 = next(c for c in result["cores"] if c["core_id"] == 1)
    t0 = {int(k): v for k, v in core0["command_done_ticks"].items()}
    t1 = {int(k): v for k, v in core1["command_done_ticks"].items()}
    # The fill (cross-core producer) must complete before the waiter runs.
    fill_cmd = _command_of(schedule, 0, "DMA_FILL")
    fatal_if(fill_cmd is None, "shapes program lost its fill producer")
    fatal_if(
        t1[waiter["command_id"]] <= t0[fill_cmd["command_id"]],
        "cross-core event consumed before its producer published",
    )
    out.append("cross-core order: PASS")


CHECKS = {
    "conservation": check_conservation,
    "completion_timing": check_completion_timing,
    "quiescence": check_quiescence,
    "e2e_a_content": check_e2e_a_content,
    "fence_scopes": check_fence_scopes,
    "cross_error_drain": check_cross_error_drain,
    "first_instance_error_only": check_first_instance_error_only,
    "p2p_reuse_commits": check_p2p_reuse_commits,
    "read_window_slides": check_read_window_slides,
    "repeat_error_drain": check_repeat_error_drain,
    "edge_bytes": check_edge_bytes,
    "p2p_commit": check_p2p_commit,
    "e2e_b_order": check_e2e_b_order,
    "recv_wait_order": check_recv_wait_order,
    "b_reorder": check_b_reorder,
    "read_error_drain": check_read_error_drain,
    "write_error_drain": check_write_error_drain,
    "fence_window": check_fence_window,
    "pin_serialize": check_pin_serialize,
    "shapes_content": check_shapes_content,
    "cross_core_order": check_cross_core_order,
    "instance_ledgers": check_instance_ledgers,
}


# ----------------------------------------------------------------- main --

class CaseContext:
    pass


def main():
    global ARCH_MANIFEST, EFFECTIVE_ARCH
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
    )
    parser.add_argument("--case", choices=backend_cases(Backend.GARNET), required=True)
    parser.add_argument("--mesh-program-dir", required=True)
    parser.add_argument("--arch", default=str(ARCH_YAML),
                        help="Architecture manifest; may vary dma/axi tuning "
                             "fields only (regions/clock/topology are pinned)")
    parser.add_argument("--load-latency-cycles", type=int, default=0,
                        help="Extra HBM read latency injected on every "
                             "load_saturation LOAD uid")
    parser.add_argument("--instances", type=int, default=1)
    parser.add_argument("--quota-write-contexts", type=int, default=16)
    parser.add_argument("--quota-write-beats", type=int, default=512)
    parser.add_argument("--quota-read-contexts", type=int, default=16)
    parser.add_argument("--quota-read-beats", type=int, default=512)
    parser.add_argument("--read-outstanding", type=int, default=None,
                        help="Override arch dma.read_outstanding (tuning)")
    parser.add_argument("--write-outstanding", type=int, default=None,
                        help="Override arch dma.write_outstanding (tuning)")
    parser.add_argument("--segment-queue-depth", type=int,
                        default=None)
    parser.add_argument("--dma-descriptor-queue-depth", type=int,
                        default=None)
    parser.add_argument("--watchdog-ticks", type=int, default=4000000)
    parser.add_argument("--only-check", action="append", choices=sorted(CHECKS))
    args = parser.parse_args()

    # Mesh-program topology defaults (AXI options come from
    # AXI_MESH.define_options with tester-oriented defaults).
    args.axi_mesh_routers = 6
    args.axi_data_width_bits = 256
    args.axi_user_width_bits = 0
    args.axi_max_sim_ticks = args.axi_max_sim_ticks if args.axi_max_sim_ticks != 100000 else 20000000
    if args.axi_source_fifo_depths == "16,64,16,32,128":
        args.axi_source_fifo_depths = "8,32,8,16,64"
    if args.axi_message_buffer_depths == "16,64,16,32,128":
        args.axi_message_buffer_depths = "8,32,8,16,64"
    if args.axi_local_delivery_depths == "16,64,16,32,128":
        args.axi_local_delivery_depths = "8,32,8,16,64"

    from m5.defines import buildEnv
    fatal_if(buildEnv["PROTOCOL"] != "AXI_MESH",
             "run_mesh_dma_garnet.py requires the AXI_MESH protocol")

    args.num_cpus = args.axi_mesh_routers
    if Path(args.arch).resolve() != ARCH_YAML.resolve():
        requested = load_arch(args.arch)
        fatal_if(_layout_key(requested) != _layout_key(ARCH_MANIFEST),
                 "--arch may only vary dma/axi tuning fields")
        ARCH_MANIFEST = requested
        EFFECTIVE_ARCH = EffectiveArchitecture(ARCH_MANIFEST)
    arch_manifest = ARCH_MANIFEST
    arch = {
        "core_ids": list(arch_manifest.core_ids),
        "sram_bytes": arch_manifest.sram_bytes,
        "sram_banks": arch_manifest.sram_banks,
        "sram_alignment": arch_manifest.sram_base_alignment_bytes,
        "axi_data_bytes": arch_manifest.axi_data_bytes,
        "axi_max_burst_beats": arch_manifest.axi_max_burst_beats,
        "region_bases": [r.base for r in arch_manifest.regions],
        "region_tile_strides": [
            r.tile_stride if r.tile_stride else 0 for r in arch_manifest.regions
        ],
    }
    fatal_if(arch_manifest.axi_data_bytes != args.axi_data_width_bits // 8,
             "arch data bytes != configured AXI width")

    program_dir = Path(args.mesh_program_dir)
    artifact_dir = Path(os.environ.get("AI_MESH_ARTIFACT_DIR", program_dir))
    result_path = artifact_dir / "actual_result.json"
    fatal_if(not (program_dir / "program.mshb").exists(),
             "missing %s/program.mshb", program_dir)

    ctx = CaseContext()
    ctx.options = args
    ctx.arch = arch
    ctx.program_dir = program_dir
    ctx.schedule = json.loads((program_dir / "schedule.mesh.json").read_text())
    ctx.expected = {
        row["descriptor_id"]: row
        for row in json.loads((program_dir / "expected_traffic.json").read_text())
    }
    ctx.scenario = base_scenario(args)
    ctx.instances = args.instances

    if set(SCENARIOS) != set(backend_cases(Backend.GARNET)):
        fatal("Garnet case registry and scenario implementations differ")
    SCENARIOS[args.case](ctx)
    apply_cli_dma_overrides(EFFECTIVE_ARCH, args)
    arch_manifest = EFFECTIVE_ARCH
    ctx.checks = list(invariant_registry(args.case, sys.argv[1:]))
    if args.only_check:
        ctx.checks = args.only_check

    scenario_path = artifact_dir / ("scenario_%s.json" % args.case)
    scenario_path.write_text(json.dumps(ctx.scenario, indent=1))
    args.axi_scenario = str(scenario_path)

    clock = "%dHz" % arch_manifest.clock_hz
    id_pool = 1 << arch_manifest.axi_id_bits

    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=clock, voltage_domain=system.voltage_domain
    )

    args.axi_id_width_bits = EFFECTIVE_ARCH.axi_id_bits
    args.axi_max_outstanding_reads = EFFECTIVE_ARCH.dma_read_outstanding
    args.axi_max_outstanding_writes = EFFECTIVE_ARCH.dma_write_outstanding

    Ruby.create_system(args, False, system, cpus=[])
    system.ruby.clk_domain = SrcClockDomain(
        clock=clock, voltage_domain=system.voltage_domain
    )

    ruby = system.ruby
    run_dir = artifact_dir
    seed_path = _seed_file(run_dir / "hbm_seed.txt", ctx.seeds)
    verify_path = _verify_file(run_dir / "hbm_verify.txt", ctx.verify)

    endpoint = NpuMemoryEndpoint(
        adapter=ruby.axi_target_adapter2,
        seed_json=seed_path,
        verify_json=verify_path,
    )
    endpoint.clk_domain = system.clk_domain
    system.mesh_endpoint = endpoint

    apertures = []
    for index, core_id in enumerate(arch["core_ids"]):
        aperture = PeerSramAperture(
            adapter=getattr(ruby, "axi_target_adapter%d" % index),
            core_id=core_id,
            sram_base=SRAM_BASE + core_id * SRAM_STRIDE,
            sram_bytes=arch_manifest.sram_bytes,
        )
        aperture.clk_domain = system.clk_domain
        setattr(system, "mesh_aperture_%d" % index, aperture)
        apertures.append(aperture)

    cores = []
    for core_id in arch["core_ids"]:
        bridge = AxiGarnetBridge(
            adapter=getattr(ruby, "axi_initiator_adapter%d" % core_id),
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
            vector_elements_per_cycle=arch_manifest.vector_elements_per_cycle["fp16"],
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
            sram_line_bytes=arch_manifest.sram_read_bytes_per_cycle_per_bank,
            sram_read_bytes_per_cycle=arch_manifest.sram_read_bytes_per_cycle_per_bank,
            sram_write_bytes_per_cycle=arch_manifest.sram_write_bytes_per_cycle_per_bank,
            sram_bank_queue_depth=ARCH_MANIFEST.sram_bank_queue_depth,
        sram_read_ports=arch_manifest.sram_read_ports_per_bank,
            sram_write_ports=arch_manifest.sram_write_ports_per_bank,
            dma=engine,
        )
        core.clk_domain = system.clk_domain
        engine.clk_domain = system.clk_domain
        bridge.clk_domain = system.clk_domain
        cores.append(core)
        setattr(system, "mesh_core_%d" % core_id, core)
        setattr(system, "mesh_engine_%d" % core_id, engine)

    loader = MeshProgramLoader(
        program_file=str(program_dir / "program.mshb"),
        cores=cores,
        apertures=apertures,
        arch_digest=EFFECTIVE_ARCH.base_digest().hex(),
        effective_arch_digest=EFFECTIVE_ARCH.digest().hex(),
        core_ids=arch["core_ids"],
        sram_bytes=arch["sram_bytes"],
        sram_banks=arch["sram_banks"],
        sram_alignment=arch["sram_alignment"],
        axi_data_bytes=arch["axi_data_bytes"],
        axi_max_burst_beats=arch["axi_max_burst_beats"],
        region_ids=list(range(len(arch_manifest.regions))),
        region_bases=[r.base for r in arch_manifest.regions],
        region_bytes=[r.bytes for r in arch_manifest.regions],
        region_tile_strides=[
            r.tile_stride if r.tile_stride else 0 for r in arch_manifest.regions
        ],
        region_kinds=[
            {"HBM": 0, "HOST_SHARED": 1, "CORE_SRAM_APERTURE": 2}[r.kind]
            for r in ARCH_MANIFEST.regions
        ],
        region_tile_bytes=[
            r.tile_bytes if r.tile_bytes else 0 for r in arch_manifest.regions
        ],
    )
    system.mesh_loader = loader

    dispatcher = MeshDispatcher(
        loader=loader,
        cores=cores,
        apertures=apertures,
        endpoint=endpoint,
        network=ruby.network,
        result_json=str(result_path),
        instances=ctx.instances,
        watchdog_ticks=args.watchdog_ticks,
    )
    dispatcher.clk_domain = system.clk_domain
    system.mesh_dispatcher = dispatcher

    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    m5.instantiate()

    exit_event = m5.simulate(args.axi_max_sim_ticks)
    cause = exit_event.getCause()
    print("Exiting @ tick", m5.curTick(), "because", cause)

    expect_error = any(
        c in ("read_error_drain", "write_error_drain",
              "cross_error_drain", "repeat_error_drain") for c in ctx.checks
    )
    want = "MESH_PROGRAM_ERROR_DRAINED" if expect_error else "MESH_PROGRAM_DONE"
    if not cause.startswith(want):
        fatal("unexpected exit cause: %s (wanted %s)" % (cause, want))

    result = json.loads(result_path.read_text())
    passed = []
    for check in ctx.checks:
        CHECKS[check](ctx, result, ctx.expected, passed)

    if os.environ.get("AI_MESH_CHILD_REPORT"):
        from mesh_ir.acceptance import build_dma_traffic, write_success_artifacts

        expected_for_traffic = list(ctx.expected.values())
        if result.get("error_drained"):
            actual_ids = {row["descriptor_id"] for row in result["transport"]}
            expected_for_traffic = [
                row for row in expected_for_traffic
                if row["descriptor_id"] in actual_ids
            ]
        traffic = build_dma_traffic(
            os.environ["AI_MESH_CASE_ID"],
            os.environ["AI_MESH_SUBCASE"],
            expected_for_traffic,
            result["transport"],
            ctx.instances,
        )
        write_success_artifacts(ctx.checks, traffic)

    print("MESH_DMA_GARNET_PASS: %s (%s)" % (
        args.case, "; ".join(passed)))
    return 0


sys.exit(main())

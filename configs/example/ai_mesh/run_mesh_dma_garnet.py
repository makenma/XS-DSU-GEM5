"""Gate 2 E2E: Dummy Core DMA over the real NPU AXI-over-Garnet network.

Builds the mesh program system (cores + DMA engines + bridges) on top of the
AXI_MESH Ruby/Garnet fabric (driver_mode=mesh_program), runs it and asserts
command/byte conservation, completion-point semantics, error drain and full
network quiescence from the machine-readable result artifact.
"""

import argparse
import hashlib
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
from mesh_ir.fault_plan import execution_keys, predict, resolve_fault_plan  # noqa: E402
from mesh_ir.architecture import load_arch  # noqa: E402
from mesh_ir.effective import (  # noqa: E402
    EffectiveArchitecture,
    apply_cli_dma_overrides,
)
from mesh_ir.diagnostics import MeshIrError  # noqa: E402
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

PIN_SEEDS = []
PIN_VERIFY = [
    (HBM_BASE + 0x200000, 128),
    (HBM_BASE + 0x200100, 128),
]


def _seed_file(path: Path, rows):
    path.write_text(
        "".join(f"0x{addr:x} {size} {pattern}\n" for addr, size, pattern in rows)
    )
    return str(path)


def _verify_file(path: Path, rows):
    path.write_text("".join(f"0x{addr:x} {size}\n" for addr, size in rows))
    return str(path)


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
        "planned_write_commit_replays": [],
        "planned_post_commit_faults": [],
        "mesh_planned_b_ejection": [],
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


@case("delayed_b_ejection")
def case_delayed_b_ejection(ctx):
    """G5-R23-02: a middle write burst B is ejected after the target already
    committed it; the descriptor still waits for every B."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 8)
    ctx.scenario["mesh_planned_b_ejection"].append(
        {"target": NODE_HBM, "uid": uids[5], "cycles": 600}
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


@case("p2p_partial_abandon")
def case_p2p_partial_abandon(ctx):
    """G5-13/CPP-09: one burst of a P2P plan fails before landing, so the
    receiving transfer keeps only the bytes its surviving bursts really commit
    and is never released."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 9)
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_SRAM1, "uid": uids[8], "resp": "slverr"}
    )


@case("p2p_prefilled")
def case_p2p_prefilled(ctx):
    """G5-10: a 40-descriptor P2P push onto a destination a core-1 LOCAL_FILL
    already made resident; the resident destination is never this transfer's
    completion."""
    ctx.program = "p2p_prefilled_destination"
    ctx.seeds = []
    ctx.verify = [(HBM_BASE + 0x200000, 160)]
    ctx.instances = 1


@case("p2p_prefilled_incomplete")
def case_p2p_prefilled_incomplete(ctx):
    """G5-10: the same resident destination with one admitted burst failing, so
    the transfer never completes and RECV_WAIT must still block on it."""
    ctx.program = "p2p_prefilled_destination"
    ctx.seeds = []
    ctx.verify = []
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 20)
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_SRAM1, "uid": uids[19], "resp": "slverr"}
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


@case("read_error_middle")
def case_read_error_middle(ctx):
    """G5-12: a real mid-plan R error must keep every burst that already
    committed, refuse the bad burst as a success and drain the response."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = []
    ctx.instances = 1
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    fatal_if(len(uids) < 17,
             "the mid-plan R error carrier needs the whole LOAD plan")
    ctx.scenario["mesh_planned_faults"].append(
        {"target": NODE_HBM, "uid": uids[16], "resp": "slverr"}
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


@case("drain_stalled")
def case_drain_stalled(ctx):
    """G5-16/B: a credit return lost after the last core halted must be reported
    by the drain-phase watchdog with the unrestored link, never as DONE."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 1
    ctx.options.link_latency = 8
    ctx.options.drain_fault = "drop_credit"
    ctx.options.watchdog_ticks = 3000000


@case("drain_deferred")
def case_drain_deferred(ctx):
    """G5-18: a network slow enough that credits are still returning when the
    last core halts, so the exit must wait for the drain to finish."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 1
    # A longer per-link latency makes the credit round trip outlast the local
    # SRAM drain, which is the state the drain boundary must defer on.
    ctx.options.link_latency = 8


@case("read_reorder")
def case_read_reorder(ctx):
    """G5-02: a multi-burst LOAD whose R bursts return out of issue order.

    Every 256-byte burst reads from its own declared pattern, so a burst that
    lands in the wrong position changes the descriptor payload digest."""
    ctx.program = "read_window"
    ctx.seeds = [
        (HBM_BASE + 0x100000 + index * 256, 256, 0x40 + index)
        for index in range(24)
    ]
    ctx.verify = []
    ctx.instances = 1
    EFFECTIVE_ARCH.override("dma_segment_queue_depth", 8)
    EFFECTIVE_ARCH.override("axi_id_bits", 2)
    EFFECTIVE_ARCH.override("sram_write_bytes_per_cycle_per_bank", 1)
    EFFECTIVE_ARCH.override("dma_read_outstanding", 4)
    EFFECTIVE_ARCH.override("dma_write_outstanding", 4)
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    fatal_if(len(uids) < 16, "read_window must predict at least 16 read uids")
    # Delay one middle burst's target service so later bursts return first.
    ctx.scenario["mesh_planned_extra_latency"].append(
        {"target": NODE_HBM, "uid": uids[2], "cycles": 2000}
    )


@case("lost_response")
def case_lost_response(ctx):
    """G5-16: a response that never arrives must be reported as a deadlock with
    the pending identities, never as DONE or ERROR_DRAINED."""
    ctx.program = "dma_error"
    ctx.seeds = []
    ctx.verify = []
    ctx.instances = 1
    uids = predict(ctx.program_dir, ctx.arch, {0: 0, 1: 1})[0]["read"]
    fatal_if(not uids, "the lost-response carrier needs a read transaction")
    # A target service delay far beyond the watchdog budget: the response is
    # never delivered inside the run, so the drain cannot complete.
    ctx.scenario["mesh_planned_extra_latency"].append(
        {"target": NODE_HBM, "uid": uids[0], "cycles": 1000000000}
    )
    ctx.options.watchdog_ticks = 2000000


@case("write_error_post_commit")
def case_write_error_post_commit(ctx):
    """G5-14: the target commits the burst for real and then reports a failing
    B, so the landing and the error must both be recorded truthfully."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = 1
    uids = write_uids(ctx.program_dir, ctx.arch, 0, 16)
    fatal_if(len(uids) < 16, "the post-commit carrier needs the full store plan")
    # A middle burst: earlier and later bursts still commit normally.
    ctx.scenario["planned_post_commit_faults"].append(
        {"target": NODE_HBM, "uid": uids[8], "resp": "slverr"})


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


SHALLOW_DEPTH = 2


def shallow_queues(ctx):
    """The shallow-queue configuration of the E2E carriers: every real queue
    owner is small enough that its bound is provable from the archive."""
    ctx.options.garnet_buffers_per_vnet = "2,2,2,2,2"
    ctx.options.axi_source_fifo_depths = "2,8,2,4,16"
    ctx.options.quota_write_contexts = 2
    ctx.options.quota_write_beats = 32
    ctx.options.quota_read_contexts = 2
    ctx.options.quota_read_beats = 64
    EFFECTIVE_ARCH.override("dma_descriptor_queue_depth", SHALLOW_DEPTH)
    EFFECTIVE_ARCH.override("dma_segment_queue_depth", SHALLOW_DEPTH)
    EFFECTIVE_ARCH.override("dma_read_outstanding", SHALLOW_DEPTH)
    EFFECTIVE_ARCH.override("dma_write_outstanding", SHALLOW_DEPTH)


@case("dma_basic_shallow")
def case_dma_basic_shallow(ctx):
    """G5-01/G5-07: the whole E2E-1 chain under the shallow-queue configuration,
    where every queue bound really bit."""
    ctx.program = "single"
    ctx.seeds = SINGLE_SEEDS
    ctx.verify = SINGLE_VERIFY
    ctx.instances = ctx.options.instances
    shallow_queues(ctx)


@case("p2p_frames_shallow")
def case_p2p_frames_shallow(ctx):
    """G5-08: the whole E2E-2 chain under the shallow-queue configuration, every
    frame checked on its own."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = ctx.options.instances
    shallow_queues(ctx)


@case("p2p_peer_edge")
def case_p2p_peer_edge(ctx):
    """G5-06/CPP-08: a peer push whose admitted destination itself starts
    unaligned on the receiving tile, pads every row by four bytes and crosses a
    4 KiB page inside its last row."""
    ctx.program = "p2p_cancel"
    ctx.seeds = [(HBM_BASE + 0x200000, 8192, 0x2d)]
    ctx.verify = []
    ctx.instances = 1


@case("p2p_frames")
def case_p2p_frames(ctx):
    """G5-08/D4: the whole E2E-2 chain at 1/2/3 instances, every frame checked
    on its own: ordering, peer coverage, chain content and drain."""
    ctx.program = "dual"
    ctx.seeds = DUAL_SEEDS
    ctx.verify = DUAL_VERIFY
    ctx.instances = ctx.options.instances


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


def _terminal_state(ledger, command_id, generation=0):
    states = [
        row["state"] for row in ledger["terminals"]
        if row["command_id"] == command_id and row["generation"] == generation
    ]
    fatal_if(len(states) != 1,
             "command %d generation %d has %d terminals"
             % (command_id, generation, len(states)))
    return states[0]


def admitted_beat_plan(ctx, row):
    """Burst geometry of one admitted descriptor execution, derived from the
    admitted remote anchor with the shared splitter model.  Nothing here reads
    a runtime counter."""
    from mesh_ir.burst_splitter import plan_descriptor

    plan = plan_descriptor(
        row["row_bytes"], row["rows"], row["remote_address"],
        row["remote_stride_bytes"], ctx.arch["axi_data_bytes"],
        row["max_burst_beats"])
    return plan.bursts


def issued_beats(ctx, result, read_direction):
    """Beats really issued in one direction, classified per admitted execution.

    Every recorded execution issued its full burst plan unless none of its
    bursts was admitted at all; a faulted execution issued its failed burst in
    addition to the ones it committed.  Nothing is inferred from a total and no
    expected item is dropped just because the drain stopped early."""
    total = 0
    for instance in result["instances"]:
        for core_id, ledger in instance["cores"].items():
            for row in ledger["observations"]["descriptor_executions"]:
                kind = ctx.expected[row["descriptor_id"]]["kind"]
                if (kind in (1, 4)) != read_direction:
                    continue
                transfer = row["transfer"] or {}
                if kind in (1, 4):
                    committed = transfer.get("read_bursts", 0)
                else:
                    committed = (transfer.get("write_bursts", 0)
                                 + transfer.get("p2p_bursts", 0))
                failed = 0 if row["status"] in (None, "OK") else 1
                if committed + failed == 0:
                    continue
                plan = admitted_beat_plan(ctx, ctx.expected[row["descriptor_id"]])
                total += sum(burst.beats for burst in plan)
    return total


def check_conservation(ctx, result, expected_rows, out):
    instances = ctx.instances
    bridges = result.get("bridges", [])
    fatal_if(not bridges, "result JSON has no bridge section")

    # Error-drained runs may cancel descriptors before they submit; only
    # descriptors with actual traffic rows contribute to the beat oracle.
    ran = {t["descriptor_id"] for t in result["transport"]}
    w_beats = 0
    r_beats = 0
    for descriptor_id, row in expected_rows.items():
        if descriptor_id not in ran:
            continue
        kind = row["kind"]
        if kind not in (1, 2, 3, 4):
            # LOCAL_FILL moves no AXI beat at all; its per-descriptor traffic
            # row is checked below, and it contributes no bridge beat.
            continue
        # The admitted splitter plan is one execution; the published row counts
        # every execution of the descriptor.
        executions = row["execution_count"] or 1
        bursts = admitted_beat_plan(ctx, row)
        beats = sum(burst.beats for burst in bursts)
        fatal_if(
            len(bursts) * executions != row["bursts"],
            "admitted splitter disagrees with the published burst count on "
            "descriptor %d: %d != %d"
            % (descriptor_id, len(bursts) * executions, row["bursts"]),
        )
        if kind in (2, 3):
            w_beats += beats * executions
        else:
            r_beats += beats * executions
    w_beats *= instances
    r_beats *= instances
    accepted_w = sum(b["w_accepted"] for b in bridges)
    consumed_r = sum(b["r_beats_consumed"] for b in bridges)
    if result.get("error_drained"):
        # A drained run issues the bursts of the executions it reached: the
        # classification comes from the archived executions, never from the
        # counters being checked.
        classified_w = issued_beats(ctx, result, False)
        classified_r = issued_beats(ctx, result, True)
        fatal_if(
            accepted_w != classified_w or consumed_r != classified_r,
            "drained run beat classification: W accepted %d vs classified %d, "
            "R consumed %d vs classified %d"
            % (accepted_w, classified_w, consumed_r, classified_r),
        )
        out.append(
            "drained beat classification: PASS (W %d, R %d issued)"
            % (classified_w, classified_r)
        )
    else:
        fatal_if(
            accepted_w != w_beats,
            "W beat conservation: accepted %d != oracle %d"
            % (accepted_w, w_beats),
        )
        fatal_if(
            consumed_r != r_beats,
            "R beat conservation: consumed %d != oracle %d"
            % (consumed_r, r_beats),
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
        if result.get("error_drained"):
            # A drained run reaches only part of the plan; the exact
            # per-execution classification belongs to the shared reconciliation
            # entry, so only the direction and the bound are structural here.
            fatal_if(
                got["read_bytes"] + got["read_discarded_bytes"] > useful
                or got["write_bytes"] + got["write_drained_uncommitted_bytes"]
                > useful
                or got["p2p_bytes"] + got["write_drained_uncommitted_bytes"]
                > useful,
                "descriptor %d moved more bytes than its admitted payload",
                descriptor_id,
            )
            continue
        if kind == 3:
            if got["p2p_bytes"] != useful or got["p2p_bursts"] != bursts:
                fatal("p2p traffic mismatch on descriptor %d", descriptor_id)
        elif kind == 2:
            if got["write_bytes"] != useful or got["write_bursts"] != bursts:
                fatal("store traffic mismatch on descriptor %d", descriptor_id)
        else:
            if got["read_bytes"] != useful or got["read_bursts"] != bursts:
                fatal("load traffic mismatch on descriptor %d", descriptor_id)

    for core in result["cores"]:
        if result.get("error_drained"):
            # The per-(command, generation) terminal partition of a drained run
            # is owned by the shared reconciliation entry; here only the drain
            # itself is structural.
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


def seeded_bytes(seeds, address, size):
    """The declared pre-cycle-0 bytes of [address, address+size), assembled
    from every covering seed range, or None when any byte is undeclared.

    A read payload oracle must be built from the declared backing, so a row
    covered by several adjacent seed ranges is assembled byte by byte instead
    of demanding one range that spans it."""
    payload = bytearray(size)
    covered = bytearray(size)
    for base, length, pattern in seeds:
        start = max(address, base)
        end = min(address + size, base + length)
        for position in range(start, end):
            payload[position - address] = pattern
            covered[position - address] = 1
    if not all(covered):
        return None
    return bytes(payload)


def declared_read_payloads(ctx):
    """Read payload oracle of the real backing: the digest of the declared
    source bytes in admitted row order, which is the order the DMA engine
    folds its per-burst runs.  A faulted read descriptor is omitted: its
    execution is judged by the burst-prefix fault model instead, and a case
    that faults a read does not have to seed the source it never commits."""
    digests = {}
    for descriptor_id, row in ctx.expected.items():
        if row["kind"] not in (1, 4) or row["useful_bytes"] == 0:
            continue
        payload = bytearray()
        for index in range(row["rows"]):
            address = row["remote_address"] + index * row["remote_stride_bytes"]
            span = seeded_bytes(ctx.seeds, address, row["row_bytes"])
            if span is None:
                fatal_if(
                    descriptor_id not in ctx.error_descriptors,
                    "descriptor %d row %d [%#x,%#x) is outside the declared "
                    "seeds", descriptor_id, index, address,
                    address + row["row_bytes"],
                )
                break
            payload += span
        else:
            digests[descriptor_id] = _dual_fnv(bytes(payload))
    return digests


def resolved_faults(ctx):
    """Resolve the scenario fault plan to admitted execution identities."""
    planned = [
        row for row in ctx.scenario.get("mesh_planned_faults", [])
        if str(row.get("resp", "slverr")).lower() == "slverr"
    ]
    # A post-commit fault is a planned error too: the target commits and then
    # reports a failing B, so the same execution identity is faulted.
    planned += list(ctx.scenario.get("planned_post_commit_faults", []))
    if not planned:
        return (), 0, {}
    try:
        resolved = resolve_fault_plan(
            ctx.program_dir, ctx.arch, planned,
            ctx.expected, ctx.instances)
    except MeshIrError as error:
        fatal("planned target fault is not an admitted execution: %s", error)
    if resolved is None:
        return (), 0, {}
    return resolved


def archived_frames(result):
    """(instance_id, per-core peer coverage) per archived instance frame."""
    return [
        (record["instance"], record["apertures"])
        for record in result["instances"]
    ]


def frame_apertures(result, instance_id=None):
    """One frame's per-core peer coverage; the last frame by default."""
    frames = archived_frames(result)
    fatal_if(not frames, "the result archived no instance frame")
    if instance_id is not None:
        frames = [frame for frame in frames if frame[0] == instance_id]
        fatal_if(len(frames) != 1, "instance %s is not archived once" % instance_id)
    return frames[-1][1]


def frame_core(result, instance_id, core_id):
    """One archived frame's core ledger."""
    frames = [
        record for record in result["instances"]
        if record["instance"] == instance_id
    ]
    fatal_if(len(frames) != 1, "instance %s is not archived once" % instance_id)
    ledger = frames[0]["cores"].get(str(core_id))
    fatal_if(ledger is None,
             "instance %s has no core %d ledger" % (instance_id, core_id))
    return ledger


def frame_terminal_tick(ledger, command_id, generation=0):
    """The tick one command's terminal was recorded in its own frame."""
    ticks = [
        row["terminal_tick"] for row in ledger["observations"]["commands"]
        if row["command_id"] == command_id and row["generation"] == generation
        and row["terminal"]
    ]
    fatal_if(len(ticks) != 1,
             "command %d generation %d has %d terminal ticks"
             % (command_id, generation, len(ticks)))
    return ticks[0]


def check_staged_commit_stages(ctx, result, expected_rows, out):
    """G5-09/G5-10: the peer composes a transfer only out of its own real
    commits, never publishes before the last admitted byte lands, and publishes
    exactly once, in every archived instance frame."""
    wanted = admitted_transfer_bursts(ctx)
    fatal_if(not wanted, "no admitted P2P transfer to check")
    seen = 0
    for instance_id, frame in archived_frames(result):
        for aperture in frame:
            for row in aperture["transfers"]:
                stages = row["stages"]
                fatal_if(not stages, "transfer %d has no commit stages"
                         % row["transfer_id"])
                previous = 0
                for index, stage in enumerate(stages):
                    fatal_if(
                        stage["covered_bytes"] <= previous,
                        "transfer %d stage %d did not add coverage: %s"
                        % (row["transfer_id"], index, stage),
                    )
                    fatal_if(
                        stage["covered_bytes"] + stage["uncovered_bytes"]
                        != row["expected_bytes"],
                        "transfer %d stage %d does not partition its "
                        "expectation: %s"
                        % (row["transfer_id"], index, stage),
                    )
                    fatal_if(
                        stage["notified"] == (index + 1 != len(stages)),
                        "transfer %d published at stage %d of %d"
                        % (row["transfer_id"], index, len(stages)),
                    )
                    fatal_if(
                        not stage["notified"] and stage["uncovered_bytes"] == 0,
                        "transfer %d stage %d has no coverage left but did not "
                        "publish" % (row["transfer_id"], index),
                    )
                    previous = stage["covered_bytes"]
                fatal_if(
                    stages[-1]["covered_bytes"] != row["expected_bytes"]
                    or stages[-1]["uncovered_bytes"] != 0,
                    "instance %s transfer %d published with %d of %d bytes"
                    % (instance_id, row["transfer_id"],
                       stages[-1]["covered_bytes"], row["expected_bytes"]),
                )
                fatal_if(
                    len(stages) != wanted[row["transfer_id"]],
                    "instance %s transfer %d composed %d stages over %d admitted "
                    "bursts" % (instance_id, row["transfer_id"], len(stages),
                                wanted[row["transfer_id"]]),
                )
                seen += 1
    frames = len(archived_frames(result))
    fatal_if(seen != len(wanted) * frames,
             "staged evidence covers %d of %d admitted transfers over %d frames"
             % (seen, len(wanted) * frames, frames))
    out.append("staged commit stages: PASS (%d transfers, %d frames)"
               % (len(wanted), frames))


def check_p2p_unique_publish(ctx, result, expected_rows, out):
    """TORCH-CPP-09: a transfer is released exactly once, and only by its own
    distinct accepted transactions covering every admitted byte."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_transfer_publishes,
    )

    wanted = admitted_transfer_bursts(ctx)
    for record in result["instances"]:
        for core_id, ledger in record["cores"].items():
            for row in ledger["observations"]["transfers"]:
                fatal_if(
                    row["sender_notifications"] != 1
                    or not row["sender_published"]
                    or row["committed_descriptors"] != row["expected_descriptors"]
                    or row["committed_bytes"] != row["expected_bytes"],
                    "instance %s core %s transfer %d was not published exactly "
                    "once over its admitted set: %s"
                    % (record["instance"], core_id, row["transfer_id"], row),
                )
    published = []
    for instance_id, frame in archived_frames(result):
        try:
            published.append(
                verified_transfer_publishes(
                    frame, wanted, refused_replays=refused_replays(ctx))
            )
        except ReconciliationError as error:
            fatal("instance %s peer transfer publish failed: %s",
                  instance_id, error)
    fatal_if(len(published) != len(archived_frames(result)),
             "peer publish evidence covers %d of %d frames"
             % (len(published), len(archived_frames(result))))
    out.append("p2p unique publish: PASS (%d transfers over %d frames)"
               % (len(published[-1]), len(published)))


def check_p2p_partial_abandon(ctx, result, expected_rows, out):
    """G5-13/CPP-09: a P2P plan that lost one burst keeps exactly the bytes its
    surviving bursts landed, on both the sender and the receiver, and is never
    released."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_transfer_publishes,
    )

    fatal_if(
        len(ctx.fault_models) != 1,
        "the partial-abandon carrier needs exactly one faulted descriptor",
    )
    descriptor_id = next(iter(ctx.fault_models))
    transfer_id = next(
        transfer_id
        for transfer_id, descriptor_ids in admitted_transfer_descriptors(ctx).items()
        if descriptor_id in descriptor_ids
    )
    fatal_if(not transfer_id,
             "faulted descriptor %d feeds no P2P transfer" % descriptor_id)
    contributions = [
        execution
        for instance in result["instances"]
        for ledger in instance["cores"].values()
        for execution in ledger["observations"]["descriptor_executions"]
        if execution["descriptor_id"] in
        admitted_transfer_descriptors(ctx)[transfer_id]
    ]
    sender_rows = [
        row
        for record in result["instances"]
        for ledger in record["cores"].values()
        for row in ledger["observations"]["transfers"]
        if row["transfer_id"] == transfer_id
    ]
    try:
        published = verified_transfer_publishes(
            frame_apertures(result),
            admitted_transfer_bursts(ctx),
            abandoned={transfer_id: contributions},
            sender_rows=sender_rows,
        )
    except ReconciliationError as error:
        fatal("partial transfer abandon failed: %s", error)
    row = next(
        row
        for aperture in frame_apertures(result)
        for row in aperture["transfers"]
        if row["transfer_id"] == transfer_id
    )
    out.append(
        "p2p partial abandon: PASS (transfer %d kept %d of %d bytes over %d "
        "bursts and abandoned, %d transfers published)"
        % (transfer_id, row["covered_bytes"], row["expected_bytes"],
           row["transactions"], len(published))
    )


def admitted_transfer_descriptors(ctx):
    """transfer_id -> admitted descriptor ids in plan order, for P2P transfers."""
    admitted = {}
    for descriptor in ctx.schedule["sections"]["DMA_DESCRIPTORS"]:
        if descriptor["kind"] != 3:
            continue
        admitted.setdefault(descriptor["transfer_id"], []).append(
            descriptor["descriptor_id"])
    return admitted


def admitted_transfer_bursts(ctx):
    """transfer_id -> admitted transaction count: the bursts of every P2P
    descriptor feeding the transfer, across its executions."""
    totals = {}
    for transfer_id, descriptor_ids in admitted_transfer_descriptors(ctx).items():
        for descriptor_id in descriptor_ids:
            row = ctx.expected[descriptor_id]
            totals[transfer_id] = (
                totals.get(transfer_id, 0)
                + len(admitted_beat_plan(ctx, row)) * (row["execution_count"] or 1)
            )
    return totals


def refused_replays(ctx):
    """transfer_id -> lanes this run's scenario re-delivered as a stale commit."""
    replay = getattr(ctx, "replay_peer_commit", {})
    return {replay["transfer_id"]: replay["lanes"]} if replay else {}


def check_transfer_snapshots(ctx, result, expected_rows, out):
    """G5-09/G5-10: every real descriptor commit of one P2P transfer reports the
    destination facts its own accepted transaction earned, and a destination an
    earlier producer already made resident is never mistaken for this transfer's
    completion."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        admitted_transfer_destination,
        fill_pattern_bytes,
        verified_transfer_snapshots,
    )

    transfers = admitted_transfer_descriptors(ctx)
    fatal_if(len(transfers) != 1,
             "the transfer-snapshot carrier admits %d transfers" % len(transfers))
    transfer_id, descriptor_ids = next(iter(transfers.items()))
    geometry = {
        row["descriptor_id"]: row
        for row in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    }
    command_id = geometry[descriptor_ids[0]]["command_id"]
    source_core = geometry[descriptor_ids[0]]["src"]["owner_core"]
    target_core, allocations = admitted_transfer_destination(
        ctx.schedule["sections"], descriptor_ids)
    commands = ctx.schedule["sections"]["COMMANDS"]
    attrs_by_index = {
        index + 1: attr
        for index, attr in enumerate(ctx.schedule["sections"]["OP_ATTRS"])
    }
    prefill = next(
        descriptor for descriptor in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
        if descriptor["kind"] == 5 and descriptor["dst"]["owner_core"] == target_core
    )

    def initial_bytes(descriptor_id):
        row = geometry[descriptor_id]
        return fill_pattern_bytes(
            attrs_by_index, commands, prefill["command_id"],
            row["row_bytes"] * row["rows"])

    frames = result["instances"]
    frame = frames[0]["cores"]
    sender = frame[str(source_core)]
    receiver = frame[str(target_core)]

    receiver_aperture = next(
        aperture for aperture in frames[0]["apertures"]
        if aperture["core_id"] == target_core
    )
    transfer_row = next(
        row for row in receiver_aperture["transfers"]
        if row["transfer_id"] == transfer_id
    )
    from mesh_ir.runtime_reconciliation import admitted_write_identities
    identities = admitted_write_identities(
        ctx.program_dir, expected_rows, arch=ctx.arch,
        src_nodes={0: 0, 1: 1}, instances=ctx.instances)

    def pending_span(descriptor_id):
        """The cut-short span of one pending descriptor: its read-back layout,
        the destination bytes and the producer bytes of the whole span."""
        images = [
            image for image in frames[0]["destinations"]
            if image["descriptor_id"] == descriptor_id
        ]
        layout = []
        destination = b""
        offset = 0
        for image in images:
            destination += bytes.fromhex(image["bytes_hex"] or "")
            layout.append((offset, image["address"], image["size"]))
            offset += image["size"]
        execution = next(
            (entry for entry in sender["observations"]["descriptor_executions"]
             if entry["descriptor_id"] == descriptor_id), None)
        producer = None
        if execution is not None and execution.get("source_rows"):
            rows = execution["source_rows"]
            if all(item.get("bytes_hex") for item in rows):
                producer = b"".join(
                    bytes.fromhex(item["bytes_hex"]) for item in rows)
        return {"layout": layout, "destination": destination or None,
                "producer": producer}

    pending_executions = [
        entry["descriptor_id"]
        for entry in sender["observations"]["descriptor_executions"]
        if entry["descriptor_id"] in descriptor_ids and not entry["committed"]
    ]
    executions_by_descriptor = {}
    for row in sender["observations"]["descriptor_executions"]:
        executions_by_descriptor.setdefault(row["descriptor_id"], []).append(
            row)
    for rows_of in executions_by_descriptor.values():
        rows_of.sort(key=lambda row: row["generation"])
    landing = {
        "transfer": transfer_row,
        "instance": frames[0]["instance"],
        "identities": identities,
        "bursts": [row for frame in frames for row in frame.get("bursts", ())],
        "spans": {
            descriptor_id: pending_span(descriptor_id)
            for descriptor_id in pending_executions
        },
    }
    rows = [
        row for row in sender["observations"]["transfer_commits"]
        if row["transfer_id"] == transfer_id
    ]
    completed = _terminal_state(sender, command_id) == "completed"
    try:
        verified_transfer_snapshots(
            rows,
            admitted=descriptor_ids,
            geometry=geometry,
            executions=executions_by_descriptor,
            target_core=target_core,
            admitted_allocations=allocations,
            completed=completed,
            initial_bytes=initial_bytes,
            resident=True,
            landing=landing,
        )
    except ReconciliationError as error:
        fatal("transfer snapshot contract failed: %s", error)

    # The resident destination has a real cause: the target's own admitted
    # prefill retired every descriptor before this transfer's first commit.
    prefill_ids = {
        descriptor["descriptor_id"]
        for descriptor in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
        if descriptor["command_id"] == prefill["command_id"]
    }
    prefill_executions = [
        row for row in receiver["observations"]["descriptor_executions"]
        if row["descriptor_id"] in prefill_ids
    ]
    fatal_if(len(prefill_executions) != len(prefill_ids)
             or not all(row["committed"] for row in prefill_executions),
             "the destination prefill did not retire: %s" % prefill_executions)
    fatal_if(
        max(row["commit_tick"] for row in prefill_executions)
        >= rows[0]["commit_tick"],
        "the destination was made resident at or after this transfer's first "
        "commit: %s vs %s"
        % (max(row["commit_tick"] for row in prefill_executions),
           rows[0]["commit_tick"]),
    )

    committed = {row["descriptor_id"] for row in rows}
    pending = sum(
        geometry[descriptor_id]["row_bytes"] * geometry[descriptor_id]["rows"]
        for descriptor_id in descriptor_ids if descriptor_id not in committed
    )
    if completed:
        fatal_if(len(rows) != len(descriptor_ids),
                 "a completed transfer archived %d of %d descriptor commits"
                 % (len(rows), len(descriptor_ids)))
        for row in rows:
            prefill_digest = hashlib.sha256(
                initial_bytes(row["descriptor_id"])).hexdigest()
            fatal_if(
                not row["source_digest"]
                or row["source_digest"] != row["target_digest"],
                "descriptor %d did not land its own source payload on the "
                "destination: %s" % (row["descriptor_id"], row),
            )
            fatal_if(
                row["target_digest"] == prefill_digest,
                "descriptor %d left the resident destination holding only its "
                "prefilled content" % row["descriptor_id"],
            )
    else:
        fatal_if(not pending,
                 "an incomplete transfer reports no pending content: %s" % rows[-1])
        recv = _command_of(ctx.schedule, target_core, "RECV_WAIT")
        fatal_if(recv is None, "the target core plans no RECV_WAIT")
        fatal_if(
            _terminal_state(receiver, recv["command_id"]) != "cancelled",
            "the receiver's RECV_WAIT did not stay blocked on the resident "
            "destination: %s" % _terminal_state(receiver, recv["command_id"]),
        )
    out.append(
        "transfer snapshots: PASS (%d of %d descriptor commits of transfer %d, "
        "%d pending bytes, resident destination)"
        % (len(rows), len(descriptor_ids), transfer_id, pending)
    )


MEMORY_SPACE_HBM = 1
MEMORY_SPACE_HOST_SHARED = 2
MEMORY_SPACE_CORE_SRAM = 3
MEMORY_SPACE_PEER_SRAM = 4

SENTINEL_NEIGHBOURHOOD_BYTES = 8


def admitted_target_spans(ctx):
    """Admitted DMA write spans per sampled target.

    ``{("endpoint",0)|("aperture",core): [(start,end)]}``: every descriptor
    destination that lands in a sampled target memory, because a byte inside one
    admitted payload is not a sentinel for another.  A compute command's result
    is not a DMA destination and its exact runs are only published at runtime, so
    it is handled when the spans are compared, not when they are declared."""
    spans = {}
    for descriptor in ctx.schedule["sections"]["DMA_DESCRIPTORS"]:
        kind = descriptor["kind"]
        if kind not in (2, 3, 5):
            continue
        row = ctx.expected[descriptor["descriptor_id"]]
        space = descriptor["dst"]["memory_space"]
        if space in (MEMORY_SPACE_HBM, MEMORY_SPACE_HOST_SHARED):
            target = ("endpoint", 0)
        elif space in (MEMORY_SPACE_CORE_SRAM, MEMORY_SPACE_PEER_SRAM):
            target = ("aperture", descriptor["dst"]["owner_core"])
        else:
            continue
        base = row["remote_address"]
        stride = row["remote_stride_bytes"]
        for index in range(row["rows"]):
            start = base + index * stride
            spans.setdefault(target, []).append((start, start + row["row_bytes"]))
    for target in spans:
        spans[target].sort()
    return spans


def target_memory_range(target):
    """The admitted address range of one sampled target: an endpoint region or
    one core's SRAM tile."""
    kind, core_id = target
    if kind == "endpoint":
        region = next(r for r in ARCH_MANIFEST.regions if r.kind == "HBM")
        return region.base, region.base + region.bytes
    region = next(
        r for r in ARCH_MANIFEST.regions if r.kind == "CORE_SRAM_APERTURE"
    )
    base = region.base + core_id * region.tile_stride
    return base, base + (region.tile_bytes or region.bytes)


def sentinel_oracle(ctx):
    """Declared sentinel spans: the head, tail and padding bytes around every
    admitted payload that no admitted write covers.

    Only bytes the target really holds can be sentinels, so a neighbourhood that
    falls outside the target's admitted range is not declared."""
    oracle = {}
    for target, spans in admitted_target_spans(ctx).items():
        claimed = spans
        low, high = target_memory_range(target)
        planned = []
        for index, (start, end) in enumerate(claimed):
            if index == 0:
                planned.append((start - SENTINEL_NEIGHBOURHOOD_BYTES, start))
            else:
                planned.append((claimed[index - 1][1], start))
            planned.append((start, end))
            if index + 1 == len(claimed):
                planned.append((end, end + SENTINEL_NEIGHBOURHOOD_BYTES))
        sentinels = []
        for start, end in planned:
            start, end = max(start, low), min(end, high)
            if end <= start:
                continue
            overlaps = any(
                start < other_end and other_start < end
                for other_start, other_end in claimed
            )
            if overlaps:
                continue
            sentinels.append((start, end))
        if sentinels:
            oracle[target] = sentinels
    return oracle


def write_sentinel_file(path, spans):
    path.write_text("".join(f"0x{start:x} {end - start}\n" for start, end in spans))
    return str(path)


def declared_sentinels(oracle):
    """The oracle in the shape the shared predicate consumes."""
    declared = {}
    for target, spans in oracle.items():
        declared[target] = [
            {"address": start, "size": end - start} for start, end in spans
        ]
    return declared


def check_post_commit_landing(ctx, result, expected_rows, out):
    """G5-14: a post-commit B error lands the bytes and still fails the source.

    The landing, the target's committed count and the failed span are owned by
    the shared entry; the wrapper supplies the admitted burst size, which is an
    architecture fact rather than a result fact."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_post_commit_landing,
    )

    admitted = ctx.arch["axi_max_burst_beats"] * ctx.arch["axi_data_bytes"]
    try:
        summary = verified_post_commit_landing(result, admitted_burst_bytes=admitted)
    except ReconciliationError as error:
        fatal("post-commit landing failed: %s", error)
    out.append(
        "post-commit landing: PASS (%d bytes landed, one %d-byte burst reported "
        "failed)" % (summary["landed_bytes"], admitted)
    )


def check_wstrb_sentinels(ctx, result, expected_rows, out):
    """TORCH-CPP-08: the bytes a transfer must not touch keep their initial
    value on both real target kinds, next to unaligned and split payloads."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_sentinels,
    )

    declared = declared_sentinels(ctx.sentinel_oracle)
    fatal_if(not declared, "no admitted sentinel span was declared for this case")
    observed = {}
    endpoint = result.get("memory_endpoint")
    fatal_if(not endpoint, "result JSON has no memory endpoint")
    observed[("endpoint", 0)] = endpoint.get("sentinels", [])
    for aperture in result["apertures"]:
        observed[("aperture", aperture["core_id"])] = aperture.get("sentinels", [])
    # A compute engine publishes its result runs tile-relative; the sentinel
    # spans are absolute, so the runs are lifted into the target's own space.
    computed = {}
    for instance in result["instances"]:
        for core_id, ledger in instance["cores"].items():
            base = target_memory_range(("aperture", int(core_id)))[0]
            computed.setdefault(int(core_id), []).extend(
                {"address": base + row["address"], "size": row["size"]}
                for output in ledger["observations"]["compute_outputs"]
                for row in output["rows"]
            )
    explained = 0
    for target, rows in declared.items():
        try:
            explained += verified_sentinels(
                rows, observed.get(target, []),
                computed.get(target[1], []) if target[0] == "aperture" else [],
            )
        except ReconciliationError as error:
            fatal("sentinel evidence failed for %s: %s", target, error)
    out.append("wstrb sentinels: PASS (%d spans checked)" % explained)


def check_cancelled_commands_issue_nothing(ctx, result, expected_rows, out):
    """G5-15: a command cancelled before it was issued leaves no descriptor
    work, while a command cancelled after submission keeps its real executions.

    The late-success half (every submitted execution must retire with a status)
    is owned by the shared reconciliation entry, not repeated here."""
    before_issue = 0
    after_issue = 0
    for instance in result["instances"]:
        for core_id, ledger in instance["cores"].items():
            states = {}
            for record in ledger["terminals"]:
                key = (record["command_id"], record["generation"])
                states.setdefault(key, set()).add(record["state"])
            issued = {
                (row["command_id"], row["generation"])
                for row in ledger["observations"]["commands"] if row["issued"]
            }
            executed = {
                (row["command_id"], row["generation"])
                for row in ledger["observations"]["descriptor_executions"]
            }
            for key, names in states.items():
                if "cancelled" not in names:
                    continue
                if key in issued:
                    after_issue += 1
                    continue
                before_issue += 1
                fatal_if(
                    key in executed,
                    "command %s on core %s was cancelled before issue but left "
                    "descriptor work" % (key, core_id),
                )
    if result.get("error_drained"):
        fatal_if(
            before_issue + after_issue == 0,
            "an error drain must terminal-cancel the commands behind the fault",
        )
    out.append(
        "cancelled commands issue nothing: PASS (%d cancelled before issue, "
        "%d cancelled after submission)" % (before_issue, after_issue)
    )


def check_global_drain(ctx, result, expected_rows, out):
    """G5-18: HALT opens the drain; the exit waits for every real owner, and
    the drain window reports what was still in flight when it opened."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_drain,
    )

    try:
        window = verified_drain(
            result.get("garnet"), result.get("credit_ledger"),
            result.get("drain"), result["bridges"],
            [aperture for _, frame in archived_frames(result)
             for aperture in frame],
            result.get("memory_endpoint"), ctx.instances)
    except ReconciliationError as error:
        fatal("global drain failed: %s", error)
    fatal_if(result["watchdog_fired"] != 0,
             "the watchdog fired during the drain")
    if ctx.expect_drain_deferral:
        fatal_if(
            window["begin_pending"] == 0,
            "the deferred-drain carrier opened its drain with an empty "
            "network, so the exit was never postponed",
        )
        fatal_if(
            window["end_tick"] <= window["begin_tick"],
            "the exit was not postponed while owner work was in flight: %s"
            % window,
        )
    out.append(
        "global drain: PASS (%d instances gated, %d ledger links restored; "
        "%d in-flight flits or credits at the last drain open, 0 at exit)"
        % (result["drain"]["instances_drained"],
           len(result["credit_ledger"]), window["begin_pending"]))


def check_reconciliation(ctx, result, expected_rows, out):
    """The admitted plan, the per-execution observations and the transport row
    are reconciled by the shared entry the mock runtime also uses."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        reconcile,
    )

    try:
        summary = reconcile(
            cause=ctx.cause,
            result=result,
            schedule=ctx.schedule,
            expected_rows=list(expected_rows.values()),
            error_descriptors=ctx.error_descriptors,
            fault_occurrence=ctx.fault_occurrence,
            instances=ctx.instances,
            traffic_multiplier=1,
            read_payload_digests=ctx.read_payload_digests,
            fault_models=ctx.fault_models,
        )
    except ReconciliationError as error:
        fatal("runtime reconciliation failed: %s", error)
    out.append("reconciliation: PASS (%d descriptors)" % len(summary["descriptors"]))


def check_loader_zero_traffic(ctx, result, expected_rows, out):
    """TORCH-NORM-00A: the control plane installs without moving any packet,
    flit or AXI beat, and the same run's dispatch is the positive control."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        loader_control_plane_delta,
    )

    try:
        moved = loader_control_plane_delta(result.get("loader_traffic"))
    except ReconciliationError as error:
        fatal("loader control-plane window failed: %s", error)
    fatal_if(moved, "loader install moved traffic: %s" % moved)
    bridges = result["bridges"]
    accepted = sum(
        bridge["ar_accepted"] + bridge["aw_accepted"] for bridge in bridges
    )
    fatal_if(accepted == 0,
             "the dispatched program produced no data-plane AXI request")
    out.append("loader zero traffic: PASS (%d AXI requests after install)"
               % accepted)


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
    traffic_read = {
        t["descriptor_id"]: t.get("read_bytes", 0)
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
                # An errored load drains its responses and terminates after the
                # last one.  It commits locally exactly when a burst survived:
                # a mid-plan error keeps the bursts that already landed, while a
                # first-burst error lands nothing.  The byte-level claim itself
                # belongs to the shared reconciliation's fault model.
                drained = traffic_read.get(row["descriptor_id"], 0)
                fatal_if(
                    row["last_r_tick"] > row["done_tick"]
                    or (drained == 0) != (row["local_commit_tick"] == 0),
                    "errored LOAD drain violation on descriptor %d: "
                    "r=%d commit=%d done=%d drained=%d"
                    % (row["descriptor_id"], row["last_r_tick"],
                       row["local_commit_tick"], row["done_tick"], drained),
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
    """G5-01/E2E-1/R23-03: every STORE destination landed its own payload, and
    the shared byte oracle proves it from raw producer and destination bytes in
    every instance frame."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_store_destinations,
    )

    stores = sorted(
        row for row in expected_rows.values()
        if row["kind"] == 2 and row["remote_stride_bytes"] == row["row_bytes"]
    )
    if not stores:
        # A strided destination is owned by the row-level byte oracle.
        out.append("e2e-a content: PASS (no contiguous store destination)")
        return
    try:
        landed = verified_store_destinations(result, stores)
    except ReconciliationError as error:
        fatal("store destination content failed: %s", error)
    out.append(
        "e2e-a content: PASS (%d store destinations landed their own payload)"
        % landed
    )


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
    frames = archived_frames(result)
    fatal_if(len(frames) != ctx.instances,
             "the result archived %d of %d instance frames"
             % (len(frames), ctx.instances))
    for instance_id, frame in frames:
        for aperture in frame:
            for transfer in aperture["transfers"]:
                fatal_if(
                    transfer["commit_tick"] == 0,
                    "instance %s transfer %d never committed"
                    % (instance_id, transfer["transfer_id"]),
                )
    out.append("p2p commit: PASS (%d frames)" % len(frames))


def check_e2e_b_order(ctx, result, expected_rows, out):
    """G5-08: within every instance frame, the receiver's own order is
    RECV_WAIT <= LOCAL_REDUCE <= DMA_STORE and the transfer committed before the
    wait released; no frame may borrow another frame's ordering."""
    schedule = ctx.schedule
    recv = _command_of(schedule, 1, "RECV_WAIT")
    reduce_cmd = _command_of(schedule, 1, "LOCAL_REDUCE")
    store = _command_of(schedule, 1, "DMA_STORE")
    fatal_if(not (recv and reduce_cmd and store), "missing E2E-B commands")
    checked = 0
    for instance_id, frame in archived_frames(result):
        ledger = frame_core(result, instance_id, 1)
        ticks = {
            command["command_id"]: frame_terminal_tick(ledger, command["command_id"])
            for command in (recv, reduce_cmd, store)
        }
        fatal_if(
            not (ticks[recv["command_id"]] <= ticks[reduce_cmd["command_id"]]
                 <= ticks[store["command_id"]]),
            "instance %s E2E-B ordering violated: recv=%d reduce=%d store=%d"
            % (instance_id, ticks[recv["command_id"]],
               ticks[reduce_cmd["command_id"]], ticks[store["command_id"]]),
        )
        aperture = next(
            row for row in frame if row["core_id"] == 1
        )
        for transfer in aperture["transfers"]:
            fatal_if(
                transfer["commit_tick"] == 0
                or ticks[recv["command_id"]] < transfer["commit_tick"],
                "instance %s RECV_WAIT completed at %d before transfer %d "
                "committed at %d"
                % (instance_id, ticks[recv["command_id"]],
                   transfer["transfer_id"], transfer["commit_tick"]),
            )
        checked += 1
    fatal_if(checked != ctx.instances,
             "ordering evidence covers %d of %d instance frames"
             % (checked, ctx.instances))
    out.append("e2e-b order: PASS (%d frames)" % checked)


def check_peer_write_bursts(ctx, result, expected_rows, out):
    """G5-06/CPP-08: an in-fabric write to a peer target really splits into the
    admitted bursts, so its unaligned head and tail, its row padding and its
    4 KiB split are observable byte for byte."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_burst_geometry,
    )

    p2p = [row for row in expected_rows.values() if row["kind"] == 3]
    fatal_if(len(p2p) != 1,
             "the peer-edge carrier admits %d P2P descriptors" % len(p2p))
    row = p2p[0]
    beat_bytes = ctx.arch["axi_data_bytes"]
    plan = admitted_beat_plan(ctx, row)
    admitted = sorted((burst.beat_base, burst.beats) for burst in plan)
    fatal_if(
        not any(burst.useful_bytes < burst.beats * beat_bytes for burst in plan),
        "the admitted peer payload has no unaligned head or tail",
    )
    page_splits = [
        burst for burst in plan
        if burst.beat_base % 4096 == 0
        and burst.beat_base != row["remote_address"]
    ]
    fatal_if(
        not page_splits,
        "the admitted peer payload never splits at a 4 KiB page boundary",
    )
    writes = [b for b in result["burst_timings"] if b["channel"] == "AW"]
    try:
        verified_burst_geometry(writes, max_beats=ctx.arch["axi_max_burst_beats"],
                                beat_bytes=beat_bytes, admitted=admitted)
    except ReconciliationError as error:
        fatal("peer write burst geometry violated: %s", error)
    out.append(
        "peer write bursts: PASS (%d admitted bursts, %d page splits)"
        % (len(admitted), len(page_splits))
    )


def check_compute_timing(ctx, result, expected_rows, out):
    """G5-01/C-1: every admitted compute command ran for exactly the cycles its
    admitted work costs, on the admitted engine, and ended at the tick that model
    predicts.  The model itself is owned by the shared entry."""
    from mesh_ir.compute_timing import (
        ComputeTimingError,
        verify_engine_timing,
    )

    try:
        summary = verify_engine_timing(
            ctx.program_dir, ARCH_MANIFEST, result, ctx.instances
        )
    except ComputeTimingError as error:
        fatal("compute timing failed: %s", error)
    out.append(
        "compute timing: PASS (%d engine plans, %d tensor cycles, %d reduce "
        "cycles)" % (summary["commands"], summary["tensor_cycles"],
                     summary["reduce_cycles"])
    )


def check_queue_bounds(ctx, result, expected_rows, out):
    """G5-01/G5-07/G5-08: the admitted queue depths bound what the run really
    held in flight; the shared entry owns the rules."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_queue_bounds,
    )

    frames = [
        (instance_id, {
            str(core_id): frame_core(result, instance_id, core_id)
            for core_id in ctx.arch["core_ids"]
            if frame_core(result, instance_id, core_id)["observations"][
                "descriptor_executions"
            ]
        })
        for instance_id, _ in archived_frames(result)
    ]
    try:
        summary = verified_queue_bounds(
            frames,
            result["bridges"],
            result.get("network_stalls"),
            descriptor_queue_depth=EFFECTIVE_ARCH.dma_descriptor_queue_depth,
            read_window=EFFECTIVE_ARCH.dma_read_outstanding,
        )
    except ReconciliationError as error:
        fatal("queue bounds failed: %s", error)
    out.append(
        "queue bounds: PASS (descriptor depth %d, read window %d, %d busy cycles)"
        % (summary["descriptor_queue_depth"], summary["read_window"],
           summary["busy_cycles"])
    )


def check_e2e_b_segments(ctx, result, expected_rows, out):
    """G5-08/D4: every segment of the E2E-2 chain is checked on its own, per
    instance frame, from real archived bytes: the admitted producer each
    local-source descriptor really read, and the bytes the peer transfer really
    landed on the receiving tile of that frame."""
    from mesh_ir.producer_content import (
        ProducerContentError,
        verify_transfer_producer_content,
    )
    local_source = sorted(
        row["descriptor_id"] for row in expected_rows.values()
        if row["kind"] in (2, 3)
    )
    fatal_if(not local_source,
             "no admitted local-source descriptor to check the chain on")
    for descriptor_id in local_source:
        try:
            verify_transfer_producer_content(
                ctx.program_dir, result, descriptor_id
            )
        except ProducerContentError as error:
            fatal("descriptor %d content does not follow its producer: %s",
                  descriptor_id, error)

    p2p = sorted(
        row["descriptor_id"] for row in expected_rows.values()
        if row["kind"] == 3
    )
    fatal_if(not p2p, "the E2E-2 carrier admits no P2P descriptor")
    reduce_command = _command_of(ctx.schedule, 1, "LOCAL_REDUCE")
    fatal_if(reduce_command is None, "the E2E-2 receiver plans no LOCAL_REDUCE")
    checked = 0
    for instance_id, _ in archived_frames(result):
        sender = frame_core(result, instance_id, 0)
        rows = [
            row for row in sender["observations"]["transfer_commits"]
            if row["descriptor_id"] in p2p
        ]
        fatal_if(not rows,
                 "instance %s archived no peer commit observation" % instance_id)
        for row in rows:
            fatal_if(
                not row["source_digest"]
                or row["source_digest"] != row["target_digest"],
                "instance %s descriptor %d did not land its own source payload "
                "on the peer tile: %s" % (instance_id, row["descriptor_id"], row),
            )
            fatal_if(
                row["committed_bytes"] != row["expected_bytes"]
                or row["sender_notifications"] != 1
                or not row["sender_published"],
                "instance %s descriptor %d did not complete its own transfer: %s"
                % (instance_id, row["descriptor_id"], row),
            )
        receiver = frame_core(result, instance_id, 1)
        reduces = [
            row for row in receiver["observations"]["compute_outputs"]
            if row["command_id"] == reduce_command["command_id"]
        ]
        fatal_if(
            len(reduces) != 1 or not reduces[0]["rows"],
            "instance %s has %d reduce observations with rows" % (instance_id, len(reduces)),
        )
        checked += 1
    fatal_if(checked != ctx.instances,
             "segment evidence covers %d of %d instance frames"
             % (checked, ctx.instances))
    out.append("e2e-b segments: PASS (%d frames, %d local-source descriptors)"
               % (checked, len(local_source)))


def check_recv_wait_order(ctx, result, expected_rows, out):
    check_e2e_b_order(ctx, result, expected_rows, out)
    out.append("recv-wait order: PASS")


def admitted_source_rows(ctx, row):
    """Admitted absolute source rows of one descriptor's local source, resolved
    from the admitted binding rather than from the run."""
    descriptor = next(
        item for item in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
        if item["descriptor_id"] == row["descriptor_id"]
    )
    return [
        (row["src_address"] + index * descriptor["src_stride_bytes"],
         descriptor["row_bytes"])
        for index in range(descriptor["rows"])
    ]


def check_packet_traffic(ctx, result, expected_rows, out):
    """G5-R23-05: the Garnet packets and flits a run really injected equal the
    admitted packetizer plan, per virtual network.

    The expectation is derived independently: the admitted per-channel
    packetizer projection (wire bytes -> flits) combined with every burst the
    run really accepted, which the burst attribution already proves.  Nothing is
    read from the counters being checked."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_garnet_traffic,
    )

    # AxiWireSlot order: 0=AW, 1=W, 2=B, 3=AR, 4=R.
    projection = {}
    for row in expected_rows.values():
        for channel in row.get("channels") or ():
            if channel.get("messages"):
                projection[channel["channel"]] = (
                    channel["vnet"], channel["flits"] // channel["messages"])
    accepted = {}
    for frame in result["instances"]:
        for row in frame.get("bursts", ()):
            key = (row["command_id"], row["generation"], row["descriptor_id"])
            entry = accepted.setdefault(
                key, {"AR": 0, "AR_beats": 0, "AW": 0, "AW_beats": 0})
            if row["channel"] == "AR":
                entry["AR"] += 1
                entry["AR_beats"] += row["beats"]
            else:
                entry["AW"] += 1
                entry["AW_beats"] += row["beats"]
    # Every legal vnet starts at a zero expectation: a plan without a channel
    # is a legal pure-read, pure-write or zero-DMA configuration, and the vnet
    # that channel would use must then carry exactly zero traffic.
    expected = {
        vnet: {"packets": 0, "flits": 0}
        for vnet in range(ctx.arch["vnets"])
    }
    for entry in accepted.values():
        messages = {
            0: entry["AW"],
            1: entry["AW_beats"],
            2: entry["AW"],
            3: entry["AR"],
            4: entry["AR_beats"],
        }
        for channel, count in messages.items():
            if not count:
                continue
            vnet, per_message = projection[channel]
            totals = expected[vnet]
            totals["packets"] += count
            totals["flits"] += count * per_message
    try:
        summary = verified_garnet_traffic(
            result.get("garnet_traffic"), expected,
            flit_bytes=ctx.arch["flit_bytes"], vnets=ctx.arch["vnets"],
        )
    except ReconciliationError as error:
        fatal("Garnet packet traffic failed: %s", error)
    out.append(
        "packet traffic: PASS (%d vnets, %d packets injected and received)"
        % (summary["vnets"], summary["packets"])
    )


def check_destination_bytes(ctx, result, expected_rows, out):
    """G5-R23-03: every writer execution's destination owner holds exactly the
    bytes that execution moved, per instance frame, from raw bytes on both
    sides."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_destination_bytes,
    )

    try:
        checked = verified_destination_bytes(
            result["instances"], expected_rows,
            sources={
                descriptor_id: admitted_source_rows(ctx, row)
                for descriptor_id, row in expected_rows.items()
                if row["kind"] in (2, 3)
            },
        )
    except ReconciliationError as error:
        fatal("destination bytes failed: %s", error)
    out.append("destination bytes: PASS (%d destination rows)" % checked)


def check_burst_attribution(ctx, result, expected_rows, out):
    """G5-R23-02: every archived burst names its admitted execution and retires
    exactly once before its descriptor's terminal tick."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_burst_attribution,
    )

    try:
        summary = verified_burst_attribution(
            result["instances"], expected_rows,
            max_beats=ctx.arch["axi_max_burst_beats"],
            beat_bytes=ctx.arch["axi_data_bytes"],
            bridges=result["bridges"],
        )
    except ReconciliationError as error:
        fatal("burst attribution failed: %s", error)
    out.append(
        "burst attribution: PASS (%d bursts over %d frames)"
        % (summary["bursts"], summary["frames"])
    )


def check_b_reorder(ctx, result, expected_rows, out):
    """G5-04: a middle B delayed after its target commit reorders the retire
    order without retiring the descriptor early.  The burst facts themselves
    are owned by the shared attribution entry."""
    bridge = next(b for b in result["bridges"] if b["core_id"] == 0)
    order = bridge["b_retire_order"]
    fatal_if(sorted(order) != list(range(min(order), min(order) + len(order))),
             "b retire order is not a permutation: %s" % order)
    fatal_if(order == sorted(order),
             "delayed middle B did not reorder the retire order: %s" % order)
    fatal_if(bridge["b_consumed"] != bridge["write_bursts_submitted"],
             "not all write bursts retired after the delayed B")
    # The delay must be visible in the burst record itself: a later write burst
    # retired before the delayed one, and its own descriptor still terminated
    # after every one of its bursts.
    writes = sorted(
        (row for frame in result["instances"] for row in frame["bursts"]
         if row["channel"] == "AW"),
        key=lambda row: row["retire_tick"],
    )
    fatal_if(len(writes) < 2, "the delayed-B carrier archived %d write bursts"
             % len(writes))
    retired = [row["ordinal"] for row in writes]
    fatal_if(
        retired == sorted(retired),
        "no write burst retired out of submission order: %s"
        % [(row["ordinal"], row["retire_tick"]) for row in writes],
    )
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
    """DC-28 extension: each scoped fence releases exactly at the completion of
    the in-scope producer it must cover, and the ALL_INSTANCE fence dominates
    the cross-core dependency.

    Every expectation is derived from the admitted fence attributes and the
    admitted descriptor kinds; nothing is read from a fixed tick table."""
    commands = ctx.schedule["sections"]["COMMANDS"]
    attrs_by_index = {
        index + 1: attr
        for index, attr in enumerate(ctx.schedule["sections"]["OP_ATTRS"])
    }
    descriptors = {
        descriptor["command_id"]: descriptor
        for descriptor in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    }

    def core_ticks(core_id):
        for core in result["cores"]:
            if core["core_id"] == core_id:
                return (
                    {int(k): v for k, v in core["command_done_ticks"].items()},
                    {int(k): v for k, v in core["command_issue_ticks"].items()},
                )
        fatal("core %d missing from result", core_id)

    done0, _ = core_ticks(0)
    done1, _ = core_ticks(1)
    fences = {
        attrs_by_index[command["attr_index"]]["fence_scope"]: command
        for command in commands
        if command["core_id"] == 0 and command["opcode"] == int(A.OPCODE.AXI_FENCE)
    }
    fatal_if(set(fences) != {1, 2, 3, 4, 5},
             "fence_scopes program must exercise all five scopes: %s"
             % sorted(fences))
    scoped = []
    for scope in (1, 2, 3, 4):
        fence = fences[scope]
        earlier = [
            command for command in commands
            if command["core_id"] == 0
            and command["command_id"] < fence["command_id"]
            and command["command_id"] in descriptors
        ]
        fatal_if(not earlier, "scope %d fence has no in-scope producer" % scope)
        producer = earlier[-1]
        fatal_if(
            done0[fence["command_id"]] != done0[producer["command_id"]],
            "scope %d fence released at %d but its in-scope producer %d "
            "completed at %d"
            % (scope, done0[fence["command_id"]], producer["command_id"],
               done0[producer["command_id"]]),
        )
        scoped.append(producer["command_id"])
    host_scope = fences[4]
    host_producers = [
        command_id for command_id in scoped
        if descriptors[command_id]["dst"]["memory_space"] == 2
    ]
    fatal_if(
        len(host_producers) != 1,
        "the HOST_SHARED_WRITE scope must cover exactly one host-shared store",
    )
    all_fence = fences[5]
    fatal_if(
        done0[all_fence["command_id"]] < max(done0[c] for c in scoped),
        "ALL_INSTANCE fence completed before a pre-fence producer",
    )
    cross_core = [command for command in commands if command["core_id"] == 1]
    fatal_if(not cross_core, "fence_scopes program has no peer command")
    waiter = cross_core[0]
    if all_fence["command_id"] < waiter["command_id"]:
        fatal_if(
            done0[all_fence["command_id"]] < done1[waiter["command_id"]],
            "ALL_INSTANCE fence did not span the cross-core dependency",
        )
    out.append("fence scopes: PASS")


def check_r_completion_order(ctx, result, expected_rows, out):
    """G5-02: real R bursts return out of issue order across different AXI
    IDs, a single ID never reorders its own bursts, and the delivered bytes
    land in their admitted positions.

    Position sensitivity comes from the declared per-burst source patterns and
    the descriptor payload digest the shared reconciliation entry checks."""
    bursts = result.get("burst_timings", [])
    fatal_if(not bursts, "missing per-burst evidence")
    reads = [row for row in bursts if row["channel"] == "AR"]
    fatal_if(len(reads) < 8, "read-reorder stimulus needs several read bursts")
    by_issue = sorted(reads, key=lambda row: (row["ar_aw_tick"], row["ordinal"]))
    addresses = [row["address"] for row in by_issue]
    fatal_if(addresses != sorted(addresses),
             "AR acceptance order is not the admitted ascending address order")
    by_response = sorted(reads, key=lambda row: (row["response_tick"],
                                                 row["ordinal"]))
    fatal_if(
        [row["address"] for row in by_response] == addresses,
        "no R burst completed out of issue order: the stimulus did not invert "
        "the completion order",
    )
    reordered = sum(
        1 for index, row in enumerate(by_response)
        if row["address"] != addresses[index]
    )
    identifiers = {row["axi_id"] for row in reads}
    fatal_if(len(identifiers) < 2, "the stimulus must use more than one AXI ID")
    for identifier in sorted(identifiers):
        same = [row for row in reads if row["axi_id"] == identifier]
        if len(same) < 2:
            continue
        issue_order = [row["ordinal"]
                       for row in sorted(same, key=lambda r: r["ar_aw_tick"])]
        response_order = [row["ordinal"]
                          for row in sorted(same, key=lambda r: r["response_tick"])]
        fatal_if(issue_order != response_order,
                 "AXI ID %d reordered its own bursts" % identifier)
    out.append("R completion order: PASS (%d bursts, %d reordered, %d IDs)"
               % (len(reads), reordered, len(identifiers)))


def check_read_window_slides(ctx, result, expected_rows, out):
    """G5-02/G5-03: the admitted read window slides; the shared entry owns the
    rules and every owner's agreement about them."""
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_read_window,
    )

    try:
        summary = verified_read_window(
            result.get("burst_timings", []), result.get("read_window", {}),
            result["bridges"],
            limit=EFFECTIVE_ARCH.dma_read_outstanding,
            max_beats=8, beat_bytes=ctx.arch["axi_data_bytes"],
        )
    except ReconciliationError as error:
        fatal("read window failed: %s", error)
    out.append(
        "read window slides: PASS (%d bursts, window %d, first credit at %d)"
        % (summary["bursts"], summary["limit"], summary["credit_release_tick"])
    )


def check_p2p_reuse_commits(ctx, result, expected_rows, out):
    """Each reused-range transfer commits its own admitted payload exactly
    once against the same peer range; no commit may satisfy two transfers."""
    admitted = {
        d["descriptor_id"]: d
        for d in ctx.schedule["sections"]["DMA_DESCRIPTORS"] if d["kind"] == 3
    }
    fatal_if(len(admitted) != 2,
             "the reuse scenario must admit two P2P descriptors, got %d"
             % len(admitted))
    ranges = {(d["dst"]["owner_core"], d["dst"]["offset_bytes"], d["rows"],
               d["row_bytes"]) for d in admitted.values()}
    fatal_if(len(ranges) != 1,
             "the reuse scenario must target one destination range: %s" % ranges)
    rows = {
        row["descriptor_id"]: row
        for row in result["transport"] if row["p2p_bytes"] > 0
    }
    fatal_if(sorted(rows) != sorted(admitted),
             "committed descriptors %s differ from the admitted %s"
             % (sorted(rows), sorted(admitted)))
    for descriptor_id, descriptor in admitted.items():
        oracle = expected_rows[descriptor_id]
        got = rows[descriptor_id]
        fatal_if(
            got["p2p_bytes"] != oracle["useful_bytes"]
            or got["p2p_bursts"] != oracle["bursts"],
            "descriptor %d committed %d bytes over %d bursts, admitted %d over "
            "%d" % (descriptor_id, got["p2p_bytes"], got["p2p_bursts"],
                    oracle["useful_bytes"], oracle["bursts"]),
        )
    aperture = next(a for a in frame_apertures(result) if a["core_id"] == 1)
    coverage = {row["transfer_id"]: row for row in aperture["transfers"]}
    for descriptor in admitted.values():
        transfer_id = descriptor["transfer_id"]
        row = coverage.get(transfer_id)
        fatal_if(row is None, "reused-range transfer %d was never armed"
                 % transfer_id)
        fatal_if(
            not row["notified"] or row["covered_bytes"] != row["expected_bytes"]
            or row["uncovered_bytes"] != 0,
            "reused-range transfer %d was not covered by its own bytes: %s"
            % (transfer_id, row),
        )
        fatal_if(
            row["duplicate_notifications"] != 0,
            "reused-range transfer %d absorbed %d foreign bytes as duplicates"
            % (transfer_id, row["duplicate_notifications"]),
        )
    from mesh_ir.runtime_reconciliation import (
        ReconciliationError,
        verified_transfer_publishes,
    )

    try:
        verified_transfer_publishes(
            frame_apertures(result),
            {
                descriptor["transfer_id"]: expected_rows[
                    descriptor["descriptor_id"]
                ]["bursts"]
                for descriptor in admitted.values()
            },
            refused_replays=refused_replays(ctx),
        )
    except ReconciliationError as error:
        fatal("reused-range transfer identity failed: %s", error)
    out.append("p2p reuse commits: PASS")


def check_first_instance_error_only(ctx, result, expected_rows, out):
    """G5-13: the first instance's error is drained, is attributed only to that
    instance, and leaves the next instance's ledger intact.  The admitted burst
    and byte accounting stays with the shared reconciliation entry."""
    fatal_if(not result["error_drained"],
             "a program with an errored instance must drain")
    frames = {record["instance"]: record for record in result["instances"]}
    fatal_if(sorted(frames) != list(range(1, ctx.instances + 1)),
             "result carries instances %s but %d were requested"
             % (sorted(frames), ctx.instances))
    faulted = frames[1]
    healthy = frames[2]
    for core_id, ledger in faulted["cores"].items():
        fatal_if(ledger["errored"] + ledger["cancelled"] == 0,
                 "instance 1 core %s has no error outcome" % core_id)
    for core_id, ledger in healthy["cores"].items():
        fatal_if(
            ledger["errored"] or ledger["cancelled"] or ledger["completed"] == 0,
            "instance 2 core %s must complete every command after the latch "
            "reset: %s" % (core_id, ledger),
        )
        for row in ledger["observations"]["descriptor_executions"]:
            fatal_if(
                row["status"] != "OK" or not row["committed"],
                "instance 1 fault evidence was attributed to instance 2: %s"
                % row,
            )
    for record in frames.values():
        for core_id, ledger in record["cores"].items():
            fatal_if(ledger["completed"] + ledger["errored"] +
                     ledger["cancelled"] == 0,
                     "instance %s core %s has no terminal outcomes"
                     % (record["instance"], core_id))
    for core in result["cores"]:
        fatal_if(core["live_commands"],
                 "live commands survive on core %d" % core["core_id"])
    scheduled = {}
    for command in ctx.schedule["sections"]["COMMANDS"]:
        scheduled[command["core_id"]] = scheduled.get(command["core_id"], 0) + 1
    for core_id, count in scheduled.items():
        ledger = healthy["cores"][str(core_id)]
        fatal_if(
            ledger["completed"] != count or ledger["errored"] or
            ledger["cancelled"],
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
                | set(core0["cancelled_command_ids"])
                | set(core0["errored_command_ids"]))
    fatal_if(repeat["command_id"] not in terminal,
             "REPEAT command never reached a terminal outcome")
    fatal_if(core0["commands_issued"] > core0["commands_completed"]
             + core0["commands_cancelled"] + core0["commands_errored"],
             "repeat error accounting: issued %d vs completed %d + cancelled "
             "%d + errored %d"
             % (core0["commands_issued"], core0["commands_completed"],
                core0["commands_cancelled"], core0["commands_errored"]))
    out.append("repeat error drain: PASS")


def check_pin_serialize(ctx, result, expected_rows, out):
    """The in-flight DMA pins the allocation, the second admit waits, and both
    stores land the admitted producer payload at distinct destinations."""
    from mesh_ir.runtime_reconciliation import fill_pattern_bytes

    core = result["cores"][0]
    issue_ticks = {int(k): v for k, v in core["command_issue_ticks"].items()}
    done_ticks = {int(k): v for k, v in core["command_done_ticks"].items()}
    descriptors = ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    stores = [d for d in descriptors if d["kind"] == 2]
    fatal_if(len(stores) != 2, "pin program must have exactly two stores")
    first, second = stores
    fatal_if(first["src"] != second["src"],
             "pin stores do not share one local source")
    fatal_if(first["dst"] == second["dst"],
             "pin stores do not target distinct destinations")
    fatal_if(
        issue_ticks[second["command_id"]] < done_ticks[first["command_id"]],
        "second store issued before the first released its pinned allocation",
    )
    producers = [d for d in descriptors if d["kind"] == 5]
    fatal_if(len(producers) != 1,
             "pin program must have exactly one local fill producer")
    producer = producers[0]
    attrs_by_index = {
        index + 1: attr
        for index, attr in enumerate(ctx.schedule["sections"]["OP_ATTRS"])
    }
    payload = fill_pattern_bytes(
        attrs_by_index, ctx.schedule["sections"]["COMMANDS"],
        producer["command_id"], producer["row_bytes"] * producer["rows"])
    landed = bytearray()
    for row in range(first["rows"]):
        offset = row * first["src_stride_bytes"]
        landed += payload[offset:offset + first["row_bytes"]]
    want = _dual_fnv(bytes(landed))
    endpoint = result["memory_endpoint"]
    fatal_if(len(endpoint["verifies"]) != 2,
             "pin program did not verify both destinations")
    fatal_if(any(item["digest"] != want for item in endpoint["verifies"]),
             "pin stores did not carry the admitted producer payload: %s"
             % endpoint["verifies"])
    out.append("pin serialize: PASS")


def check_shapes_content(ctx, result, expected_rows, out):
    """PAYLOAD-CONTENT: the multi-row strided P2P and STORE carry the bytes of
    their admitted last producer, and the HBM destination really landed them.

    The content relation is owned by the shared producer-content entry; this
    check only adds the destination-side landing facts."""
    from mesh_ir.producer_content import (
        ProducerContentError,
        verify_transfer_producer_content,
    )

    endpoint = result["memory_endpoint"]
    rows = {(r["address"], r["size"]): r for r in endpoint["verifies"]}
    row0 = rows.get((HBM_BASE + 0x300000, 64))
    fatal_if(row0 is None, "shapes verify row 0 missing")
    fatal_if(row0["after16_digest"] == _dual_fnv(bytes(48)),
             "shapes store row 0 tail not covered by the result stream: %s"
             % row0)
    row1 = rows.get((HBM_BASE + 0x300100, 64))
    fatal_if(row1 is None, "shapes verify row 1 missing")
    fatal_if(row1["digest"] == _dual_fnv(bytes(64)),
             "shapes store row 1 shows no content flow: %s" % row1)

    local_source = [
        row["descriptor_id"] for row in expected_rows.values()
        if row["kind"] in (2, 3)
    ]
    fatal_if(not local_source, "no admitted local-source descriptor to check")
    verified = 0
    for descriptor_id in sorted(local_source):
        try:
            verify_transfer_producer_content(
                ctx.program_dir, result, descriptor_id
            )
        except ProducerContentError as error:
            fatal("descriptor %d content does not follow its producer: %s",
                  descriptor_id, error)
        verified += 1

    fill_rows = [t for t in result["transport"] if t["fill_bytes"] > 0]
    fatal_if(not fill_rows, "no FILL accounting row")
    for fill_row in fill_rows:
        fatal_if(
            fill_row["read_bursts"] or fill_row["write_bursts"]
            or fill_row["p2p_bursts"],
            "fill row must be local-only: %s" % fill_row,
        )
    out.append("shapes content: PASS (%d producers verified)" % verified)


CHECKS = {
    "conservation": check_conservation,
    "reconciliation": check_reconciliation,
    "loader_zero_traffic": check_loader_zero_traffic,
    "completion_timing": check_completion_timing,
    "quiescence": check_quiescence,
    "e2e_a_content": check_e2e_a_content,
    "fence_scopes": check_fence_scopes,
    "cross_error_drain": check_cross_error_drain,
    "first_instance_error_only": check_first_instance_error_only,
    "p2p_reuse_commits": check_p2p_reuse_commits,
    "p2p_unique_publish": check_p2p_unique_publish,
    "p2p_partial_abandon": check_p2p_partial_abandon,
    "transfer_snapshots": check_transfer_snapshots,
    "staged_commit_stages": check_staged_commit_stages,
    "wstrb_sentinels": check_wstrb_sentinels,
    "r_completion_order": check_r_completion_order,
    "global_drain": check_global_drain,
    "post_commit_landing": check_post_commit_landing,
    "cancelled_commands_issue_nothing": check_cancelled_commands_issue_nothing,
    "read_window_slides": check_read_window_slides,
    "burst_attribution": check_burst_attribution,
    "destination_bytes": check_destination_bytes,
    "packet_traffic": check_packet_traffic,
    "repeat_error_drain": check_repeat_error_drain,
    "edge_bytes": check_edge_bytes,
    "p2p_commit": check_p2p_commit,
    "e2e_b_order": check_e2e_b_order,
    "e2e_b_segments": check_e2e_b_segments,
    "queue_bounds": check_queue_bounds,
    "compute_timing": check_compute_timing,
    "peer_write_bursts": check_peer_write_bursts,
    "recv_wait_order": check_recv_wait_order,
    "b_reorder": check_b_reorder,
    "read_error_drain": check_read_error_drain,
    "write_error_drain": check_write_error_drain,
    "fence_window": check_fence_window,
    "pin_serialize": check_pin_serialize,
    "shapes_content": check_shapes_content,
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
    parser.add_argument("--dump-verify-bytes", type=int, default=0,
                        help="Dump raw committed bytes of verify rows up to "
                             "this size; 0 disables the dump")
    parser.add_argument("--dump-source-bytes", type=int, default=0,
                        help="Dump raw local source rows of write descriptors "
                             "up to this size; 0 disables the dump")
    parser.add_argument("--drain-fault", default="",
                        choices=("", "drop_credit"),
                        help="Test-only drain-phase fault: lose one credit "
                             "return at the real delivery boundary once the "
                             "drain is open")
    parser.add_argument("--receiver-fault", default="",
                        choices=("", "receive_drop", "receive_redirect",
                                 "receive_duplicate"),
                        help="Test-only receiver notification fault injected at "
                             "the real delivery boundary")
    parser.add_argument("--post-commit-fault", action="append", default=[],
                        metavar="TARGET:UID",
                        help="Test-only target hook: commit the write for real "
                             "and then report a failing B for this UID")
    parser.add_argument("--replay-peer-commit", default="",
                        metavar="TRANSFER:BURST",
                        help="Test-only receiver hook: re-deliver the write "
                             "commit of this admitted P2P burst through the "
                             "receiving aperture once a later expectation for "
                             "the same range is armed")
    parser.add_argument("--replay-peer-commit-delay", type=int, default=1000,
                        help="Ticks between arming the later expectation and "
                             "the re-delivered stale commit")
    parser.add_argument("--replay-write-commit", action="append", default=[],
                        metavar="TARGET:UID:COUNT",
                        help="Test-only target hook: replay one write-commit "
                             "observer notification COUNT times for the "
                             "transaction with this UID at this target node")
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
        "flit_bytes": arch_manifest.fabric.network.flit_bytes,
        "vnets": len(arch_manifest.fabric.network.vnet_classes),
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
    descriptor_kinds = {
        row["descriptor_id"]: row["kind"]
        for row in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
    }
    traffic = json.loads((program_dir / "expected_traffic.json").read_text())
    ctx.expected = {
        row["identity"]["descriptor_id"]: {
            **row,
            **row["identity"],
            "kind": descriptor_kinds[row["identity"]["descriptor_id"]],
        }
        for row in traffic["descriptors"]
    }
    ctx.scenario = base_scenario(args)
    for entry in args.post_commit_fault:
        try:
            target_text, uid_text = entry.split(":", 1)
            target, uid = int(target_text, 0), int(uid_text, 0)
        except ValueError:
            fatal("--post-commit-fault expects TARGET:UID, got %r", entry)
        ctx.scenario["planned_post_commit_faults"].append(
            {"target": target, "uid": uid, "resp": "slverr"})
    for entry in args.replay_write_commit:
        try:
            target_text, uid_text, count_text = entry.split(":", 2)
            target, uid, count = int(target_text, 0), int(uid_text, 0), int(count_text)
        except ValueError:
            fatal("--replay-write-commit expects TARGET:UID:COUNT, got %r", entry)
        fatal_if(count <= 0, "replay count must be positive")
        ctx.scenario["planned_write_commit_replays"].append(
            {"target": target, "uid": uid, "count": count})
    ctx.instances = args.instances
    ctx.cause = ""
    ctx.error_descriptors = ()
    ctx.fault_occurrence = 0
    ctx.fault_models = {}
    args.replay_peer_commit_uid = (1 << 64) - 1
    ctx.replay_peer_commit = {}
    if args.replay_peer_commit:
        try:
            transfer_text, burst_text = args.replay_peer_commit.split(":", 1)
            transfer_id, burst_index = int(transfer_text, 0), int(burst_text, 0)
        except ValueError:
            fatal("--replay-peer-commit expects TRANSFER:BURST, got %r"
                  % args.replay_peer_commit)
        fatal_if(burst_index < 0, "replay burst index must not be negative")
        p2p = [row for row in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
               if row["kind"] == 3 and row["transfer_id"] == transfer_id]
        fatal_if(len(p2p) != 1,
                 "replay transfer %d admits %d P2P descriptors"
                 % (transfer_id, len(p2p)))
        facts = execution_keys(ctx.program_dir, ctx.arch, {0: 0, 1: 1},
                               instances=args.instances)
        bursts = [fact for core in facts.values() for fact in core
                  if fact["descriptor_id"] == p2p[0]["descriptor_id"]
                  and fact["direction"] == "write"
                  and fact["instance"] == 1]
        fatal_if(burst_index >= len(bursts),
                 "replay burst %d exceeds the %d admitted write bursts of "
                 "transfer %d" % (burst_index, len(bursts), transfer_id))
        replay = bursts[burst_index]
        args.replay_peer_commit_uid = replay["uid"]
        # The stale delivery is refused by the expectation that is live when it
        # lands: the later transfer that reuses the replayed destination.
        def destination(row):
            return (row["dst"]["owner_core"], row["dst"]["offset_bytes"],
                    row["rows"], row["row_bytes"])

        later = [
            row for row in ctx.schedule["sections"]["DMA_DESCRIPTORS"]
            if row["kind"] == 3 and destination(row) == destination(p2p[0])
            and (row["command_id"], row["descriptor_id"])
            > (p2p[0]["command_id"], p2p[0]["descriptor_id"])
        ]
        fatal_if(len(later) != 1,
                 "the replayed transfer must be followed by exactly one "
                 "transfer reusing its destination, got %d" % len(later))
        ctx.replay_peer_commit = {
            "transfer_id": later[0]["transfer_id"],
            "burst_index": burst_index,
            "uid": replay["uid"],
            "lanes": replay["useful_bytes"],
        }

    if set(SCENARIOS) != set(backend_cases(Backend.GARNET)):
        fatal("Garnet case registry and scenario implementations differ")
    SCENARIOS[args.case](ctx)
    apply_cli_dma_overrides(EFFECTIVE_ARCH, args)
    arch_manifest = EFFECTIVE_ARCH
    ctx.checks = list(invariant_registry(args.case, sys.argv[1:]))
    if args.only_check:
        ctx.checks = args.only_check
    ctx.expect_drain_deferral = args.case == "drain_deferred"
    if "post_commit_landing" in ctx.checks:
        # The landing oracle compares raw bytes on both sides.
        args.dump_verify_bytes = args.dump_verify_bytes or 64 * 1024
        args.dump_source_bytes = args.dump_source_bytes or 64 * 1024
    if any(check in ctx.checks for check in (
            "e2e_a_content", "e2e_b_segments", "destination_bytes",
            "transfer_snapshots", "p2p_partial_abandon", "wstrb_sentinels",
            "post_commit_landing", "peer_write_bursts")):
        # The E2E byte oracle needs the producer and destination raw bytes of
        # every admitted row on both sides, so the dump limit must cover the
        # widest admitted row.
        widest = max((row["row_bytes"] for row in ctx.expected.values()),
                     default=0)
        args.dump_verify_bytes = max(args.dump_verify_bytes, widest)
        args.dump_source_bytes = max(args.dump_source_bytes, widest)
    ctx.sentinel_oracle = {}
    if "wstrb_sentinels" in ctx.checks:
        ctx.sentinel_oracle = sentinel_oracle(ctx)
        fatal_if(not ctx.sentinel_oracle,
                 "case %s asked for sentinel evidence but declares no span"
                 % args.case)
        for target, spans in ctx.sentinel_oracle.items():
            name = ("hbm_sentinel.txt" if target[0] == "endpoint"
                    else "peer_sentinel_%d.txt" % target[1])
            write_sentinel_file(artifact_dir / name, spans)
    ctx.read_payload_digests = None
    if "reconciliation" in ctx.checks:
        (ctx.error_descriptors, ctx.fault_occurrence,
         ctx.fault_models) = resolved_faults(ctx)
        ctx.read_payload_digests = declared_read_payloads(ctx)
    if "reconciliation" in ctx.checks or ctx.sentinel_oracle:
        # The oracle of admitted inputs and expectations is archived before the
        # run; nothing in it is copied from a runtime counter.
        (artifact_dir / "gate5_oracle.json").write_text(json.dumps(
            {
                "case": args.case,
                "instances": ctx.instances,
                "expect_drain_deferral": ctx.expect_drain_deferral,
                "read_payload_digests": ctx.read_payload_digests,
                "error_descriptors": list(ctx.error_descriptors),
                "fault_occurrence": ctx.fault_occurrence,
                "fault_models": {
                    str(key): value for key, value in ctx.fault_models.items()
                },
                "sentinels": {
                    ("endpoint" if target[0] == "endpoint"
                     else "aperture_%d" % target[1]): [
                        {"address": start, "size": end - start}
                        for start, end in spans
                    ]
                    for target, spans in ctx.sentinel_oracle.items()
                },
                "beat_plan": {
                    str(descriptor_id): {
                        "bursts": len(admitted_beat_plan(ctx, row)),
                        "beats": sum(burst.beats
                                     for burst in admitted_beat_plan(ctx, row)),
                    }
                    for descriptor_id, row in ctx.expected.items()
                },
            },
            indent=1, sort_keys=True) + "\n")

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
        verify_bytes_limit=args.dump_verify_bytes,
        sentinel_json=(
            str(artifact_dir / "hbm_sentinel.txt")
            if ("endpoint", 0) in getattr(ctx, "sentinel_oracle", {}) else ""
        ),
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
            sentinel_json=(
                str(artifact_dir / ("peer_sentinel_%d.txt" % core_id))
                if ("aperture", core_id) in getattr(ctx, "sentinel_oracle", {})
                else ""
            ),
            replay_commit_uid=args.replay_peer_commit_uid,
            replay_commit_delay=args.replay_peer_commit_delay,
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
            source_bytes_limit=args.dump_source_bytes,
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
            sram_line_bytes=arch_manifest.sram_bank_interleave_bytes,
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
        network=ruby.network,
        bridges=[getattr(system, "mesh_bridge_%d" % core_id)
                 for core_id in arch["core_ids"]],
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
        receiver_fault=args.receiver_fault,
        drain_fault=args.drain_fault,
        destination_bytes_limit=args.dump_verify_bytes,
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

    planned_faults = [
        row for row in ctx.scenario.get("mesh_planned_faults", [])
        if str(row.get("resp", "slverr")).lower() == "slverr"
    ]
    planned_faults += list(ctx.scenario.get("planned_post_commit_faults", []))
    want = ("MESH_PROGRAM_ERROR_DRAINED" if planned_faults
            else "MESH_PROGRAM_DONE")
    if not cause.startswith(want):
        fatal("unexpected exit cause: %s (wanted %s)" % (cause, want))

    result = json.loads(result_path.read_text())
    ctx.cause = cause
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

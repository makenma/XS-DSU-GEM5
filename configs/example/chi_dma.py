"""CPU-executed DMA baseline on the existing CHI 2x2 mesh."""
import argparse
import json
import os
import m5
from m5.objects import *
from m5.util import addToPath

addToPath(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.xiangshan import XiangshanCore


def build_dma_path(system, args):
    # Single factory: future RTL HNI/RNI replacements belong here.
    if args.backend != "software":
        raise NotImplementedError("RTL HNI/RNI requires BFM and CHI proxies")
    if not getattr(args, "directed_test", False) and args.hnf_entries < 8:
        raise ValueError("CPU baseline requires at least 8 HNF entries; smaller pools can stall the existing CPU cache path")
    system.routers = [
        ChiRouterRefModel(local_x=x, local_y=y, node_type="router")
        for y in range(2)
        for x in range(2)
    ]
    r00, r10, r01, r11 = system.routers
    for a, b in [(r00, r10), (r01, r11)]:
        a.internal_ports[0] = b.internal_peer_ports[2]
        b.internal_ports[2] = a.internal_peer_ports[0]
    for a, b in [(r00, r01), (r10, r11)]:
        a.internal_ports[3] = b.internal_peer_ports[1]
        b.internal_ports[1] = a.internal_peer_ports[3]
    system.hnf = HomeNodeFull(
        x=1,
        y=1,
        node_type="hnf",
        sn_node_id=0x80,
        direct_sn_fake_data=False,
        late_write_comp=True,
        num_poc_entries=args.hnf_entries,
    )
    system.hnf.test_reverse_comp_data = getattr(args, "reverse_data", False)
    if getattr(args, "reverse_data", False):
        system.hnf.test_reverse_comp_data_source = 0x10
    r11.local_ports[0] = system.hnf.rxport
    system.snf = Chi2ClassicMemBridge(node_id=0x80, hnf_node_id=0x90)
    r10.local_ports[0] = system.snf.chi_side
    system.snf.mem_side = system.membus.cpu_side_ports
    if not getattr(args, "directed_test", False):
        system.rnf = Cache2ChiBridge(
            node_id=0, home_node_id=0x90, sink_hnf_txreq=False
        )
        r00.device_ports[0] = system.rnf.chi_side
        system.rnf_uncached = RniDmaAdapter(
            node_id=4,
            allow_io=True,
            max_entries=4,
            max_chi=4,
            upstream_ranges=system.mem_ranges
            + [AddrRange(0x40000000, size=4096)],
        )
        system.rnf.mem_side = system.rnf_uncached.dma_side
        system.rnf_uncached.loader_side = system.membus.cpu_side_ports
        r00.device_ports[4] = system.rnf_uncached.chi_side
    system.rni = RniDmaAdapter(
        max_entries=args.rni_entries,
        max_chi=min(args.chi_outstanding, args.rni_entries),
    )
    if getattr(args, "dma_error", False):
        system.rni.memory_range = AddrRange(0x10000000, size=0x100000)
    r01.device_ports[0] = system.rni.chi_side
    system.hni = HniIoAdapter()
    if (
        not getattr(args, "direct_credit_test", False)
        and getattr(args, "cut_path", "") != "hni"
    ):
        r01.local_ports[4] = system.hni.chi_side
    system.dma = SimpleDma(
        chunk_bytes=args.chunk_bytes, parallel_chunks=args.parallel_chunks
    )
    if getattr(args, "stalls", False):
        system.pio_stall = DmaTimingStall(
            reject_responses=False, stall_cycles=30
        )
        system.hni.pio_side = system.pio_stall.upstream
        system.pio_stall.downstream = system.dma.pio
        system.dma_stall = DmaTimingStall(
            reject_requests=False, stall_cycles=50
        )
        system.dma.dma = system.dma_stall.upstream
        system.dma_stall.downstream = system.rni.dma_side
    else:
        system.hni.pio_side = system.dma.pio
        system.dma.dma = system.rni.dma_side
    if getattr(args, "cut_path", ""):
        system.cut_sink = ChiFlitSink()
        if args.cut_path == "hni":
            r01.local_ports[4] = system.cut_sink.chi_side
        else:
            system.rni.home_node_id = 0x94
            r11.local_ports[4] = system.cut_sink.chi_side


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--binary", default="tests/test-progs/simple_dma/simple_dma"
    )
    p.add_argument(
        "--mode",
        default="dma_copy",
        choices=[
            "dma_copy",
            "mmio",
            "mixed",
            "regression",
            "errors",
            "dma_error",
        ],
    )
    p.add_argument("--length", type=int, default=64)
    p.add_argument("--iterations", type=int, default=1)
    p.add_argument("--chunk-bytes", type=int, default=64)
    p.add_argument("--parallel-chunks", type=int, default=1)
    p.add_argument("--rni-entries", type=int, default=8)
    p.add_argument("--chi-outstanding", type=int, default=4)
    p.add_argument("--hnf-entries", type=int, default=16)
    p.add_argument("--max-ticks", type=int, default=1_000_000_000_000)
    p.add_argument(
        "--backend", choices=["software", "rtl"], default="software"
    )
    p.add_argument("--stalls", action="store_true")
    p.add_argument("--reverse-data", action="store_true")
    p.add_argument("--cut-path", choices=["hni", "rni"], default="")
    p.add_argument("--dma-error", action="store_true")
    p.add_argument("--poll-limit", type=int, default=2_000_000)
    p.add_argument("--cpu", choices=["timing", "o3"], default="timing")
    args = p.parse_args()
    s = System(
        mem_mode="timing", mem_ranges=[AddrRange("512MiB")], cache_line_size=64
    )
    s.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=VoltageDomain())
    s.membus = SystemXBar()
    s.memory = SimpleMemory(range=s.mem_ranges[0], latency="30ns")
    s.memory.port = s.membus.mem_side_ports
    s.system_port = s.membus.cpu_side_ports
    build_dma_path(s, args)
    s.cpu = RiscvTimingSimpleCPU() if args.cpu == "timing" else XiangshanCore()
    if args.cpu == "o3":
        s.cpu.branchPred = DecoupledBPUWithBTB(bpDBSwitches=[])
        s.cpu.branchPred.isDumpMisspredPC = True
        s.cpu.store_prefetch_train = False
        s.cpu.enable_storeSet_train = False
    s.cpu.createInterruptController()
    s.icache = Cache(
        size="16KiB",
        assoc=2,
        tag_latency=1,
        data_latency=1,
        response_latency=1,
        mshrs=8,
        tgts_per_mshr=8,
    )
    s.dcache = Cache(
        size="16KiB",
        assoc=2,
        tag_latency=1,
        data_latency=1,
        response_latency=1,
        mshrs=8,
        tgts_per_mshr=8,
    )
    s.cachebus = L2XBar()
    s.cpu.icache_port = s.icache.cpu_side
    s.cpu.dcache_port = s.dcache.cpu_side
    s.icache.mem_side = s.cachebus.cpu_side_ports
    s.dcache.mem_side = s.cachebus.cpu_side_ports
    s.cachebus.mem_side_ports = s.rnf.cache_side
    binary = os.path.abspath(args.binary)
    s.workload = SEWorkload.init_compatible(binary)
    proc = Process(
        executable=binary,
        cmd=[
            binary,
            args.mode,
            str(args.length),
            str(args.iterations),
            str(args.poll_limit),
        ],
    )
    s.cpu.workload = proc
    s.cpu.createThreads()
    root = Root(full_system=False, system=s)
    m5.instantiate()
    # Explicit physical DMA addresses; no arbitrary userspace pointer translation.
    proc.map(0x40000000, 0x40000000, 4096, False)
    proc.map(0x50000000, 0x10000000, 0x400000, False)
    event = m5.simulate(args.max_ticks)
    result = {
        "tick": int(m5.curTick()),
        "cause": event.getCause(),
        "exit_code": event.getCode(),
        "mode": args.mode,
        "length": args.length,
        "chunk_bytes": args.chunk_bytes,
        "parallel_chunks": args.parallel_chunks,
        "classification": "software adapters; not RTL cycle accurate",
        "cacheable_buffers": False,
        "cpu": args.cpu,
    }
    if (
        event.getCode() == 0
        and event.getCause() == "exiting with last active thread context"
    ):
        # This fork's O3 SE global drain cannot converge after thread exit.
        # Complete a bounded network tail and directly check the new nodes.
        m5.simulate(1_000_000)
        result["rni_idle"] = s.rni.idle()
        result["hni_idle"] = s.hni.idle()
        result["rnf_uncached_idle"] = s.rnf_uncached.idle()
        result["dma_idle"] = s.dma.idle()
        result["hnf_idle"] = not s.hnf.hasLinkWork()
    m5.stats.dump()
    with open(os.path.join(m5.options.outdir, "result.json"), "w") as f:
        json.dump(result, f, indent=2)
    print("DMA_RESULT", json.dumps(result))
    if event.getCode() or not all(
        result.get(k, False)
        for k in [
            "rni_idle",
            "hni_idle",
            "rnf_uncached_idle",
            "dma_idle",
            "hnf_idle",
        ]
    ):
        raise SystemExit(1)


if __name__ == "__m5_main__":
    main()

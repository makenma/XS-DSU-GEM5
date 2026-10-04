"""KMH CPU / classic L1 -> native CHI L2 -> real NoC -> HN -> native SN.

No classic L2, Cache2ChiBridge, ChiTestControl, or explicit protocol opcodes.
"""
import argparse
import json
import os
from pathlib import Path
import m5
from _m5.core import seedRandom
from m5.objects import *
from m5.util import addToPath

parser = argparse.ArgumentParser()
parser.add_argument("--elf-dir", required=True)
parser.add_argument("--sets", type=int, default=16)
parser.add_argument("--assoc", type=int, default=4)
parser.add_argument("--mshrs", type=int, default=16)
parser.add_argument("--hn-entries", type=int, default=16)
parser.add_argument("--slc-lines", type=int, default=256)
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--max-ticks", type=int, default=2_000_000_000)
parser.add_argument("--tail-ticks", type=int, default=1_000_000,
                    help="Bounded tail for already-issued CHI handshakes after all CPUs exit")
args = parser.parse_args()
if not 0 <= args.tail_ticks <= 10_000_000:
    parser.error("--tail-ticks must be in 0..10000000")
seedRandom(args.seed)
gem5 = Path(os.environ["CHI_GEM5_DIR"])
addToPath(str(gem5 / "configs"))
from common.xiangshan import XiangshanCore
from common.Caches import L1_ICache, L1_DCache, L1ToL2Bus

elf = Path(args.elf_dir).resolve()
program = json.loads((elf / "manifest.json").read_text())
system = System(mem_mode="timing", cache_line_size=64, mem_ranges=[AddrRange("256MiB")])
system.voltage_domain = VoltageDomain()
system.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=system.voltage_domain)
system.num_cpus = 4
system.xiangshan_system = True
system.enable_difftest = False
system.cpu = [XiangshanCore(cpu_id=i) for i in range(4)]
corners = [(0, 0), (2, 0), (0, 1), (2, 1)]
def nid(x, y, port=0): return (x << 7) | (y << 4) | (port << 2)
rn_ids = [nid(x, y) for x, y in corners]
hn_ids = [nid(x, y, 1) for x, y in corners]
sn_ids = [nid(1, y, 2) for y in range(2)]
system.l2 = [ChiNativeL2(node_id=n, home_node_ids=hn_ids, home_hash_masks=[64, 128],
                       sets=args.sets, assoc=args.assoc, mshrs=args.mshrs,
                       ranges=system.mem_ranges) for n in rn_ids]
system.hn = [ChiExtendedHome(node_id=n, sn_node_ids=sn_ids, sn_hash_masks=[4160],
                             num_entries=args.hn_entries, slc_lines=args.slc_lines) for n in hn_ids]
system.sn = [ChiNativeMemory(node_id=n, max_outstanding=32) for n in sn_ids]
system.routers = [ChiRouterRefModel(local_x=x, local_y=y) for y in range(2) for x in range(3)]
def router(x, y): return system.routers[y * 3 + x]
links = []
for y in range(2):
    for x in range(3):
        if x < 2:
            router(x, y).internal_ports[0] = router(x + 1, y).internal_peer_ports[2]
            router(x + 1, y).internal_ports[2] = router(x, y).internal_peer_ports[0]
            links.append({"from": f"R{x}{y}", "to": f"R{x+1}{y}"})
        if y < 1:
            router(x, y).internal_ports[3] = router(x, y + 1).internal_peer_ports[1]
            router(x, y + 1).internal_ports[1] = router(x, y).internal_peer_ports[3]
            links.append({"from": f"R{x}{y}", "to": f"R{x}{y+1}"})
# The native HN completes operations to PoC. The L1 crossbar is only PoU;
# setting PoC here would consume CPU CBOs before they reached ChiNativeL2.
system.l1bus = [L1ToL2Bus(point_of_coherency=False) for _ in range(4)]
for i, (x, y) in enumerate(corners):
    cpu = system.cpu[i]
    cpu.branchPred = DecoupledBPUWithBTB(bpDBSwitches=[])
    cpu.store_prefetch_train = False
    cpu.enable_storeSet_train = False
    l1d_options = {}
    if program.get("rtlRequestWorkload"):
        l1d_options["do_fast_writeline"] = True
    cpu.addPrivateSplitL1Caches(L1_ICache(size="1KiB", assoc=2, prefetcher=NULL),
                               L1_DCache(size="1KiB", assoc=2, prefetcher=NULL, **l1d_options))
    cpu.createInterruptController()
    cpu.connectAllPorts(system.l1bus[i].cpu_side_ports, system.l1bus[i].cpu_side_ports,
                        system.l1bus[i].mem_side_ports)
    system.l1bus[i].mem_side_ports = system.l2[i].cpu_side
    router(x, y).device_ports[0] = system.l2[i].chi_side
    router(x, y).local_ports[4] = system.hn[i].rxport
for i in range(2):
    router(1, i).local_ports[8] = system.sn[i].chi_side
system.membus = NoncoherentXBar(width=32, frontend_latency=1, forward_latency=1,
                               response_latency=1, header_latency=1)
system.system_port = system.membus.cpu_side_ports
system.memory = [SimpleMemory(range=AddrRange(0, size="256MiB", masks=[4160], intlvMatch=i),
                              latency="20ns") for i in range(2)]
for i in range(2):
    system.sn[i].mem_side = system.membus.cpu_side_ports
    system.membus.mem_side_ports = system.memory[i].port
for l2 in system.l2:
    l2.functional = system.membus.cpu_side_ports
binaries = [str(elf / f"native-l2-{i}.elf") for i in range(4)]
system.workload = SEWorkload.init_compatible(binaries[0])
for i, cpu in enumerate(system.cpu):
    cpu.workload = Process(pid=100+i, executable=binaries[i], cmd=[binaries[i]])
    cpu.createThreads()
root = Root(full_system=False, system=system)

topology = [{"id": f"{prefix}{i}", "label": f"{prefix}{i}", "nodeId": n, "role": role}
            for prefix, ids, role in [("RN", rn_ids, "RN-F"), ("HN", hn_ids, "HN-F"), ("SN", sn_ids, "SN-F")]
            for i, n in enumerate(ids)]
out = Path(m5.options.outdir)
out.mkdir(parents=True, exist_ok=True)
manifest = {
    "type": "manifest", "schemaVersion": "1.1", "title": program.get("title", "KMH CPU · 原生 CHI L2"),
    "description": program.get("description", "真实四核普通 load/store/fence；L2 持有 tag/data/MSHR，直接发送原生 CHI。"),
    "source": {"kind": "gem5_run", "gem5Commit": "65f3241a6cd486dffddaca251536f5c691af9a1f",
               "generator": "native/gem5/native_l2_run.py", "nativeChi": True},
    "protocol": "ARM IHI 0050 Issue H", "profile": "h-native-256", "lineBytes": 64,
    "dataWidth": 256, "topology": topology, "complete": False, "initialState": {},
    "clock": {"tickHz": "1000000000000"},
    "nativeL2": {"sets": args.sets, "assoc": args.assoc, "mshrs": args.mshrs,
                 "classicL2": False, "protocolOpcodeInjection": False},
    "program": program,
    "simulationSeed": args.seed,
    "sam": {"description": "RN→HN: PA[6],PA[7]；HN→SN: PA[6] XOR PA[12]。",
            "homeNodeIds": [f"HN{i}" for i in range(4)], "hashMasks": ["64", "128"]},
    "fabric": {"routers": [{"id": f"R{x}{y}", "x": x, "y": y, "object": f"system.routers{y*3+x}"}
                            for y in range(2) for x in range(3)], "links": links,
               "attachments": [{"node": f"{prefix}{i}", "router": f"R{x}{y}"}
                               for prefix in ["RN", "HN"] for i, (x, y) in enumerate(corners)]
                              + [{"node": f"SN{i}", "router": f"R1{i}"} for i in range(2)]},
    "endpointPorts": {**{f"system.l2{i}.chi_side": n for i, n in enumerate(rn_ids)},
                      **{f"system.hn{i}.rxport": n for i, n in enumerate(hn_ids)},
                      **{f"system.sn{i}.chi_side": n for i, n in enumerate(sn_ids)}},
}
settings = out / "flowgraph-config.json"
settings.write_text(json.dumps({"manifest": manifest}, ensure_ascii=False))
os.environ["CHI_FLOWGRAPH_CONFIG"] = str(settings)
m5.instantiate()
shared = system.workload.allocPhysPages(16, 0)
for cpu in system.cpu:
    cpu.workload[0].map(0x100000000, shared, 65536, cacheable=True)
uncached_mapping = None
if program.get("uncachedBytes"):
    uncached_bytes = int(program["uncachedBytes"])
    uncached_virtual = int(program["uncachedVirtual"], 0)
    if uncached_bytes <= 0 or uncached_bytes % 4096 or uncached_virtual % 4096:
        raise ValueError("uncached mapping must contain positive, page-aligned bytes")
    # Independent physical RAM pages, with no cacheable alias. This is normal
    # noncacheable memory, not a Device/MMIO region; STRICT_ORDER is a CPU
    # execution constraint and does not define CHI MemAttr.Device.
    uncached_physical = system.workload.allocPhysPages(uncached_bytes // 4096, 0)
    for cpu in system.cpu:
        cpu.workload[0].map(uncached_virtual, uncached_physical, uncached_bytes, cacheable=False)
    uncached_mapping = {"virtual": hex(uncached_virtual), "physical": hex(uncached_physical),
                        "bytes": uncached_bytes, "kind": "normal-noncacheable-ram",
                        "cacheableAlias": False}
event = m5.simulate(args.max_ticks)
cause = event.getCause()
code = event.getCode()
cpu_exit_tick = str(m5.curTick())
cpu_pass = code == 0 and "exiting with last active thread context" in cause
# CPU termination can precede delivery of an already queued CompAck or writeback.
# Continue for a bounded window; do not flush dirty cache contents or manufacture
# traffic. Preserve the original CPU exit cause/code as the pass criterion.
tail_event = m5.simulate(args.tail_ticks) if cpu_pass and args.tail_ticks else None
result = {"tick": str(m5.curTick()), "cause": cause, "exitCode": code,
          "cpuExitTick": cpu_exit_tick, "drainEndTick": str(m5.curTick()),
          "tailTicksRequested": args.tail_ticks,
          "tailCause": tail_event.getCause() if tail_event else None,
          "sharedPhysical": hex(shared), "sharedBytes": 65536,
          "uncachedMapping": uncached_mapping,
          "timedCpuPath": "KMH CPU -> classic L1 -> ChiNativeL2 -> CHI NoC -> HN -> ChiNativeMemory",
          "protocolOpcodeInjection": False,
          "simulationSeed": args.seed,
          "pass": cpu_pass}
(out / "native-l2-result.json").write_text(json.dumps(result, indent=2) + "\n")
m5.stats.dump()
print("NATIVE_L2_RESULT", json.dumps(result))
if not result["pass"]:
    raise RuntimeError(f"native L2 CPU run did not complete: {cause}, code={code}")

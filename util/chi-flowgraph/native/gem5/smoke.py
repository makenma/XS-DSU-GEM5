"""Real event-loop smoke: PyTrafficGen -> RN -> router -> HNF -> SN -> memory.

Run with gem5.opt, not system Python. No ISA, image, checkpoint or guest program.
The same configuration can run with or without CHI_FLOWGRAPH_OUT.
"""
import json
import os
from pathlib import Path

import m5
from m5.objects import (AddrRange, Cache2ChiBridge, Chi2ClassicMemBridge,
                        ChiRouterRefModel, HomeNodeFull, NoncoherentXBar, PyTrafficGen, Root,
                        SimpleMemory, SrcClockDomain, System, VoltageDomain)

retry_mode = os.environ.get("CHI_FLOWGRAPH_RUNTIME_SCENARIO") == "retry"
system = System(cache_line_size=64, mem_mode="timing", mem_ranges=[AddrRange("16MiB")])
system.voltage_domain = VoltageDomain()
system.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=system.voltage_domain)
system.rn = Cache2ChiBridge(node_id=0, home_node_id=4, sink_hnf_txreq=False,
                           transaction_latency_trace_file="rnf-latency.csv")
system.hn = HomeNodeFull(x=0, y=0, port=1, device=0, node_type="hnf",
                         sn_node_id=8, direct_sn_fake_data=False,
                         num_poc_entries=1 if retry_mode else 32,
                         slc_num_sets=4, slc_num_ways=2, sf_num_sets=16, sf_num_ways=4)
system.sn = Chi2ClassicMemBridge(node_id=8, hnf_node_id=0)
system.router = ChiRouterRefModel(local_x=0, local_y=0)
system.router.device_ports[0] = system.rn.chi_side
system.router.local_ports[4] = system.hn.rxport
system.router.local_ports[8] = system.sn.chi_side
system.memory = SimpleMemory(range=system.mem_ranges[0], latency="20ns")
system.membus = NoncoherentXBar(width=32, frontend_latency=1, forward_latency=1,
                               response_latency=1, header_latency=1)
system.sn.mem_side = system.membus.cpu_side_ports
system.system_port = system.membus.cpu_side_ports
system.membus.mem_side_ports = system.memory.port
system.traffic = PyTrafficGen()
system.traffic.port = system.rn.cache_side
root = Root(full_system=False, system=system)

manifest = {
    "type": "manifest", "schemaVersion": "1.0", "title": "gem5 实际运行 · miss 后同地址 hit",
    "description": "PyTrafficGen 经 RN / HNF / SN 访问 SimpleMemory；router 仅用于仿真，不在回放中显示。",
    "source": {"kind": "gem5_run", "gem5Commit": "65f3241a6cd486dffddaca251536f5c691af9a1f",
               "chironCommit": "55cd1e1d92142960af2b57c686fc9f35ce7286be", "generator": "native/gem5/smoke.py"},
    "protocol": "ARM IHI 0050 Issue H", "profile": "h-core-256", "lineBytes": 64, "dataWidth": 256,
    "topology": [{"id": name, "label": name, "nodeId": number, "role": role}
                 for name, number, role in [("RN0",0,"RN-F"),("HN0",4,"HN-F"),("SN0",8,"SN-F")]],
    "endpointPorts": {"system.rn.chi_side":0, "system.hn.rxport":4, "system.sn.chi_side":8},
    "clock": {"tickHz":"1000000000000"}, "initialState": {}, "complete": False,
    "sam": {"description":"实际 RN 配置：home_node_id=4；home_node_ids=[]，固定选择 HN0。", "homeNodeIds":["HN0"]},
}
if retry_mode:
    manifest["title"] = "gem5 实际运行 · RN 重试与 P-Credit"
    manifest["description"] = "实际 RN 以 1ns 间隔发出两笔读；HN 只有一个在途名额，第二笔经历 RetryAck、PCrdGrant 与重发。"
outdir = Path(m5.options.outdir)
outdir.mkdir(parents=True, exist_ok=True)
settings = outdir / "flowgraph-config.json"
settings.write_text(json.dumps({"manifest":manifest}, ensure_ascii=False), encoding="utf-8")
os.environ["CHI_FLOWGRAPH_CONFIG"] = str(settings)
m5.instantiate()
system.rn.startTransactionLatencyTrace()

def requests():
    # Two full-line reads, separated sufficiently for the first fill to commit.
    # This pinned LinearGen uses an inclusive end address (nextAddr > endAddr).
    interval = 1000 if retry_mode else 200000
    yield system.traffic.createLinear(600000, 0x4040, 0x407f, 64, interval, interval, 100, 128)
    yield system.traffic.createIdle(200000)
    yield system.traffic.createExit(0)

system.traffic.start(requests())
exit_event = m5.simulate(2000000)
system.rn.stopTransactionLatencyTrace()
m5.stats.dump()
result = {"tick":str(m5.curTick()), "cause":exit_event.getCause()}
(outdir / "flowgraph-result.json").write_text(json.dumps(result), encoding="utf-8")
print("FLOWGRAPH_RUNTIME", json.dumps(result))

"""Timing-only integration stimulus, separate from CPU acceptance workload."""
import argparse
import json
import os
import m5
from m5.objects import *
from m5.util import addToPath

addToPath("../../../configs/example")
from chi_dma import build_dma_path

p = argparse.ArgumentParser()
kind = p.add_mutually_exclusive_group()
kind.add_argument("--io", action="store_true")
kind.add_argument("--credits", action="store_true")
a = p.parse_args()
s = System(
    mem_mode="timing", mem_ranges=[AddrRange("512MiB")], cache_line_size=64
)
s.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=VoltageDomain())
s.membus = SystemXBar()
s.system_port = s.membus.cpu_side_ports
s.memory = SimpleMemory(
    range=s.mem_ranges[0], latency="30ns", latency_var="80ns"
)
s.memory.port = s.membus.mem_side_ports
args = argparse.Namespace(
    backend="software",
    hnf_entries=2,
    rni_entries=2,
    chi_outstanding=2,
    chunk_bytes=33,
    parallel_chunks=4,
    directed_test=True,
    direct_credit_test=a.credits,
    stalls=True,
)
build_dma_path(s, args)
s.hnf.test_reverse_comp_data = True
# Additional requester shares the same existing network/HNF; it exercises
# arbitrarily sized parent Packets independent of DmaPort line splitting.
s.test_adapter = RniDmaAdapter(
    node_id=8,
    allow_io=a.io or a.credits,
    max_entries=256 if a.credits else 2,
    max_chi=256 if a.credits else 2,
)
if a.credits:
    s.test_adapter.test_hold_rx_cycles = 50000
if a.credits:
    # Isolate HNI's finite link credits: the mesh has additional buffering,
    # which otherwise masks this specific response-credit exhaustion test.
    s.hni.chi_side = s.test_adapter.chi_side
else:
    s.routers[0].device_ports[8] = s.test_adapter.chi_side
s.tester = DmaPathTester(io_test=a.io)
if a.credits:
    s.tester.io_burst = True
s.tester.port = s.test_adapter.dma_side
root = Root(full_system=False, system=s)
m5.instantiate()
e = m5.simulate(100_000_000)
if e.getCause() != "DMA directed test passed":
    raise RuntimeError(e.getCause())
m5.drain()
assert s.test_adapter.idle() and s.rni.idle() and s.hni.idle()
m5.stats.dump()
with open(os.path.join(m5.options.outdir, "result.json"), "w") as f:
    json.dump(
        {"pass": True, "io": a.io, "tick": int(m5.curTick()), "idle": True},
        f,
        indent=2,
    )

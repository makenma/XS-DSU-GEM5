"""Port-level HNF regression, with the ordinary CMN700 LAT=2 configuration."""

import argparse

import m5
from m5.objects import (
    ChiHnfTester,
    HomeNodeFull,
    Root,
    SlcSf,
    SrcClockDomain,
    VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", default="cold_read")
parser.add_argument("--max-cycles", type=int, default=4000)
parser.add_argument("--response-delay", type=int, default=3)
parser.add_argument("--requests", type=int, default=20000)
parser.add_argument("--early-schedule", action="store_true")
parser.add_argument("--sf-sets", type=int, default=4096,
                    help="Use a smaller directory for eviction stress tests")
parser.add_argument("--sf-ways", type=int, default=16)
args = parser.parse_args()

root = Root(full_system=False)
root.voltage = VoltageDomain()
root.clock = SrcClockDomain(clock="1GHz", voltage_domain=root.voltage)
root.hnf = HomeNodeFull(
    clk_domain=root.clock,
    node_type="hnf",
    port=1,  # HNF ID 4; RN-F IDs 0..3 and SN ID 0x100 are distinct.
    rnf_num=4,
    pocq_entry_num=16,
    slcsf=SlcSf(
        clk_domain=root.clock,
        rnf_num=4,
        l3_tag_sets=1024,
        l3_tag_ways=16,
        sf_tag_sets=args.sf_sets,
        sf_tag_ways=args.sf_ways,
        cache_line_size=64,
    ),
)
root.tester = ChiHnfTester(
    clk_domain=root.clock,
    hnf=root.hnf,
    scenario=args.scenario,
    max_cycles=args.max_cycles,
    response_delay=args.response_delay,
    throughput_requests=args.requests,
    early_schedule=args.early_schedule,
)
root.tester.chi = root.hnf.rxport

m5.instantiate()
event = m5.simulate()
if event.getCause() != "CHI HNF integration passed":
    raise RuntimeError(event.getCause())

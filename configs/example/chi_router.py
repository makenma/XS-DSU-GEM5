"""CHI router stage, arbitration, owned-data and credit regression."""
import argparse

import m5
from m5.objects import (
    ChiRouterRefModel, ChiRouterTester, Root, SrcClockDomain, VoltageDomain,
)

parser = argparse.ArgumentParser()
parser.add_argument("--scenario", choices=["timing", "alias", "congestion", "idle", "throughput"], default="timing")
parser.add_argument("--dual", action="store_true")
parser.add_argument("--per-output", action="store_true")
parser.add_argument("--repetitions", type=int, default=128)
parser.add_argument("--trace", default="")
parser.add_argument("--two-hop", action="store_true", help="Send eastbound traffic through a second real router")
parser.add_argument("--reverse-event-order", action="store_true")
args = parser.parse_args()
if args.two_hop and args.scenario == "timing":
    parser.error("--two-hop uses congestion, idle, or throughput")

root = Root(full_system=False)
root.voltage = VoltageDomain()
root.clock = SrcClockDomain(clock="1GHz", voltage_domain=root.voltage)
root.router = ChiRouterRefModel(
    clk_domain=root.clock, node_type="router", local_x=1, local_y=1,
    dual_lane_req=args.dual, dual_lane_rsp=args.dual,
    dual_lane_snp=args.dual, dual_lane_dat=args.dual,
    multi_flit_out=args.per_output,
)
if args.two_hop and args.scenario == "congestion":
    # Keep the hot target (1,1,P3,D0), but place it on the relay so that the
    # blocked endpoint exercises a real directional link's credit return.
    root.router.local_x = 0
root.tester = ChiRouterTester(
    clk_domain=root.clock, router=root.router, scenario=args.scenario,
    repetitions=args.repetitions, trace_file=args.trace,
    early_schedule=args.reverse_event_order,
)
if args.two_hop:
    root.relay = ChiRouterRefModel(
        clk_domain=root.clock, node_type="router",
        local_x=1 if args.scenario == "congestion" else 2, local_y=1,
        dual_lane_req=args.dual, dual_lane_rsp=args.dual,
        dual_lane_snp=args.dual, dual_lane_dat=args.dual,
        multi_flit_out=args.per_output,
    )
for port in range(16):
    root.tester.sources = root.router.device_ports[port]
    if args.two_hop and args.scenario == "congestion" and port == 12:
        root.router.local_ports[port] = root.tester.sinks[16]
    else:
        root.router.local_ports[port] = root.tester.sinks[port]
for port in range(8):
    root.tester.sources = root.router.internal_peer_ports[port]
    if args.two_hop and port in (0, 4):
        root.router.internal_ports[port] = root.relay.internal_peer_ports[port + 2]
    else:
        root.router.internal_ports[port] = root.tester.sinks[16 + port]
if args.two_hop:
    if args.scenario == "congestion":
        root.relay.local_ports[12] = root.tester.sinks[12]
    else:
        root.relay.local_ports[0] = root.tester.sinks[16]
    # E0/E1 converge on the same local destination. This otherwise unused
    # endpoint keeps the vector topology complete.
    root.relay.local_ports[1] = root.tester.sinks[20]

m5.instantiate()
event = m5.simulate()
if event.getCause() != "CHI router regression passed":
    raise RuntimeError(event.getCause())

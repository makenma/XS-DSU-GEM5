from m5.objects.ClockedObject import ClockedObject
from m5.params import Param, VectorRequestPort, VectorResponsePort


class ChiRouterTester(ClockedObject):
    type = "ChiRouterTester"
    cxx_class = "gem5::Chi::ChiRouterTester"
    cxx_header = "mem/cache/CHI/ChiRouterTester.hh"
    router = Param.ChiRouterRefModel("Router under test")
    sources = VectorRequestPort("Sixteen local and eight directional sources")
    sinks = VectorResponsePort("Sixteen local and eight directional sinks")
    scenario = Param.String("timing", "timing, congestion, idle, or throughput")
    repetitions = Param.Unsigned(128, "Flits per source/channel in sustained traffic")
    max_cycles = Param.Unsigned(1000000, "Watchdog")
    trace_file = Param.String("", "Optional deterministic acceptance/delivery trace")
    early_schedule = Param.Bool(False, "Schedule the endpoint before router events to test same-edge order independence")

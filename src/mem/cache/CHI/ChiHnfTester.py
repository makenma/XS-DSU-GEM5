from m5.objects.ClockedObject import ClockedObject
from m5.params import Param, RequestPort


class ChiHnfTester(ClockedObject):
    """Deterministic RN-F/SN endpoints for the HNF integration regression."""

    type = "ChiHnfTester"
    cxx_class = "gem5::Chi::ChiHnfTester"
    cxx_header = "mem/cache/CHI/ChiHnfTester.hh"

    hnf = Param.HomeNodeFull("Home node under test")
    chi = RequestPort("CHI stimulus and response endpoint")
    scenario = Param.String("cold_read", "Directed integration scenario")
    max_cycles = Param.Unsigned(4000, "Watchdog bound")
    response_delay = Param.Unsigned(3, "Endpoint response delay in cycles")
    throughput_requests = Param.Unsigned(20000, "Requests in throughput scenario")
    early_schedule = Param.Bool(False, "Insert endpoint events before HNF events")

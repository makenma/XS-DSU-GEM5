from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class DmaTimingStall(ClockedObject):
    """Bounded deterministic port fault injection for directed tests."""

    type = "DmaTimingStall"
    cxx_class = "gem5::Chi::DmaTimingStall"
    cxx_header = "mem/cache/CHI/DmaTimingStall.hh"
    upstream = ResponsePort("Packet ingress")
    downstream = RequestPort("Packet egress")
    stall_cycles = Param.Cycles(20, "Request/response retry delay")
    reject_requests = Param.Bool(True, "Reject each original request once")
    reject_responses = Param.Bool(True, "Reject each original response once")

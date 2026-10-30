from m5.objects.AbstractMemory import AbstractMemory
from m5.params import Param, RequestPort, ResponsePort


class ChiSnfBridge(AbstractMemory):
    """64-byte CHI subordinate memory with native physical-memory backing."""

    type = "ChiSnfBridge"
    cxx_class = "gem5::Chi::ChiSnfBridge"
    cxx_header = "mem/cache/CHI/ChiSnfBridge.hh"

    chi = RequestPort("CHI endpoint connected to the router")
    functional = ResponsePort("Functional image loading only; no timing bypass")
    node_id = Param.Unsigned(0, "CHI SN-F node ID")
    response_latency = Param.Cycles(20, "Read/DBID/write completion latency")
    max_transactions = Param.Unsigned(64, "Outstanding read and write limit")

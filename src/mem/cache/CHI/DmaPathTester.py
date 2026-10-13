from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class DmaPathTester(ClockedObject):
    type = "DmaPathTester"
    cxx_class = "gem5::Chi::DmaPathTester"
    cxx_header = "mem/cache/CHI/DmaPathTester.hh"
    port = RequestPort("Timing-only adapter test port")
    system = Param.System(Parent.any, "Requestor allocator")
    io_test = Param.Bool(False, "Exercise exact HNI MMIO errors and START")
    io_burst = Param.Bool(
        False, "Burst SCRATCH reads to saturate HNI response credits"
    )

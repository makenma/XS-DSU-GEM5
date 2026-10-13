from m5.params import *
from m5.SimObject import PyBindMethod
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class HniIoAdapter(ClockedObject):
    type = "HniIoAdapter"
    cxx_class = "gem5::Chi::HniIoAdapter"
    cxx_header = "mem/cache/CHI/HniIoAdapter.hh"
    cxx_exports = [PyBindMethod("idle")]
    chi_side = ResponsePort("Existing CHI mesh")
    pio_side = RequestPort("Device PIO timing port")
    system = Param.System(Parent.any, "System requestor allocator")
    node_id = Param.UInt32(0x14, "HNI NodeID")
    io_range = Param.AddrRange(
        AddrRange(0x40000000, size=4096), "Exact MMIO window"
    )

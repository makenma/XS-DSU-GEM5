from m5.params import *
from m5.SimObject import PyBindMethod
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class RniDmaAdapter(ClockedObject):
    type = "RniDmaAdapter"
    cxx_class = "gem5::Chi::RniDmaAdapter"
    cxx_header = "mem/cache/CHI/RniDmaAdapter.hh"
    cxx_exports = [PyBindMethod("idle")]
    dma_side = ResponsePort("Device DMA / RNF uncacheable Packet ingress")
    chi_side = RequestPort("Existing CHI mesh port")
    loader_side = RequestPort(
        "Optional unmeasured SE image/syscall functional port"
    )
    node_id = Param.UInt32(0x10, "Requester NodeID")
    home_node_id = Param.UInt32(0x90, "HNF NodeID")
    io_node_id = Param.UInt32(0x14, "HNI NodeID")
    memory_range = Param.AddrRange(
        AddrRange(0x10000000, size=0x400000), "Owned noncacheable aperture"
    )
    io_range = Param.AddrRange(
        AddrRange(0x40000000, size=4096), "Exact PIO window (RNF only)"
    )
    allow_io = Param.Bool(False, "Enable RNF exact MMIO routing")
    upstream_ranges = VectorParam.AddrRange(
        [], "Optional RNF advertised ranges"
    )
    max_entries = Param.Unsigned(8, "Original Packet entries")
    max_chi = Param.Unsigned(4, "Actual CHI transactions")
    test_hold_rx_cycles = Param.Cycles(
        0, "Directed-test hook: temporarily hold incoming CHI responses"
    )

from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class ChiCosimBridge(ClockedObject):
    """SN-F replacement that hands every flit to an external co-simulator.

    Sits where ``Chi2ClassicMemBridge`` would: REQ/DAT arriving on the CHI
    network are forwarded to the peer (a pyuvm/VCS testbench driving the
    C2XM CHI-to-AXI RTL) over a UNIX socket, RSP/DAT flits from the peer
    are injected back, and the AXI side of the RTL reads/writes *this*
    system's memory through ``mem_side`` -- so gem5's DDR stays the single
    source of truth.  RNF bypass traffic is routed through ``bypass_side``
    and can either cross the socket as well or be served locally.
    """

    type = "ChiCosimBridge"
    cxx_header = "mem/cache/CHI/ChiCosimBridge.hh"
    cxx_class = "gem5::Chi::ChiCosimBridge"

    chi_side = ResponsePort("SNF CHI side port (router local port)")
    mem_side = RequestPort("Classic port toward system.membus (DDR/devices)")
    bypass_side = ResponsePort("RNF bypass/uncached traffic sink port")

    system = Param.System(Parent.any, "System this bridge belongs to")
    socket_path = Param.String("/tmp/c2xm_cosim.sock",
                               "UNIX socket path of the co-sim peer")
    quantum_cycles = Param.UInt32(100, "Barrier period in clock cycles")
    barrier_timeout = Param.Float(120.0,
                                  "Seconds to wait for a barrier ack")
    node_id = Param.UInt32(0x80, "SNF node id reported in the hello message")
    hnf_node_id = Param.UInt32(0x90,
                               "HNF node id reported in the hello message")
    bypass_route = Param.String(
        "all",
        "RNF bypass routing: all | dram_only (devices served locally)"
        " | none (everything served locally)")

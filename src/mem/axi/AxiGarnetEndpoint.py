from m5.objects.ClockedObject import ClockedObject
from m5.params import *


class AxiInitiatorAdapter(ClockedObject):
    type = "AxiInitiatorAdapter"
    cxx_header = "mem/axi/axi_garnet_endpoint.hh"
    cxx_class = "gem5::axi::AxiInitiatorAdapter"

    shim = Param.RubyController("Owning initiator SLICC network shim")
    peer = Param.RubyController("Raw-probe target shim")

    aw_out = Param.MessageBuffer("AW to-network queue")
    w_out = Param.MessageBuffer("W to-network queue")
    ar_out = Param.MessageBuffer("AR to-network queue")
    b_local = Param.MessageBuffer("Finite B local-delivery queue")
    r_local = Param.MessageBuffer("Finite R local-delivery queue")

    src_node = Param.UInt32(0, "Logical source node")
    src_port = Param.UInt16(0, "Logical source port")
    dst_node = Param.UInt32(1, "Logical target node")
    wire_header_bytes = VectorParam.UInt32(
        [24, 16, 8, 24, 16], "AW,W,B,AR,R wire header bytes"
    )
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    raw_probe = Param.Bool(False, "Run Commit 1 raw shim probe")
    raw_probe_hold_cycles = Param.Cycles(
        8, "Cycles to hold the independent B-like local delivery"
    )


class AxiTargetAdapter(ClockedObject):
    type = "AxiTargetAdapter"
    cxx_header = "mem/axi/axi_garnet_endpoint.hh"
    cxx_class = "gem5::axi::AxiTargetAdapter"

    shim = Param.RubyController("Owning target SLICC network shim")
    peer = Param.RubyController(NULL, "Raw-probe initiator shim")
    probe_observer = Param.AxiInitiatorAdapter(
        NULL, "Commit 1-only AW drain observer"
    )

    b_out = Param.MessageBuffer("B to-network queue")
    r_out = Param.MessageBuffer("R to-network queue")
    aw_local = Param.MessageBuffer("Finite AW local-delivery queue")
    w_local = Param.MessageBuffer("Finite W local-delivery queue")
    ar_local = Param.MessageBuffer("Finite AR local-delivery queue")

    src_node = Param.UInt32(0, "Raw-probe logical source node")
    src_port = Param.UInt16(0, "Raw-probe logical source port")
    dst_node = Param.UInt32(1, "Raw-probe logical target node")
    wire_header_bytes = VectorParam.UInt32(
        [24, 16, 8, 24, 16], "AW,W,B,AR,R wire header bytes"
    )
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    raw_probe = Param.Bool(False, "Run Commit 1 raw shim probe")
    raw_probe_hold_cycles = Param.Cycles(
        8, "Cycles to hold the first AW in a depth-one local queue"
    )

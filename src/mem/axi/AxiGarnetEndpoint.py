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
    targets = VectorParam.RubyController([], "Functional target shims")
    target_nodes = VectorParam.UInt32([], "Logical node for each target shim")
    range_starts = VectorParam.UInt64([], "Normal target range starts")
    range_ends = VectorParam.UInt64([], "Normal target range ends")
    range_targets = VectorParam.UInt32([], "Normal range target nodes")
    default_error_target = Param.UInt32(1, "Default error target node")
    quota_target_nodes = VectorParam.UInt32([], "Per-target quota nodes")
    quota_write_contexts = VectorParam.UInt32([], "Source write context quota")
    quota_write_beats = VectorParam.UInt32([], "Source write beat quota")
    quota_read_contexts = VectorParam.UInt32([], "Source read context quota")
    quota_read_beats = VectorParam.UInt32([], "Source read beat quota")
    id_width = Param.UInt8(8, "AXI ID width")
    max_outstanding_reads = Param.UInt32(64, "Read outstanding limit")
    max_outstanding_writes = Param.UInt32(32, "Write outstanding limit")
    source_fifo_depths = VectorParam.UInt32(
        [16, 64, 16, 32, 128], "AW,W,B,AR,R source FIFO depths"
    )
    pre_aw_bursts = Param.UInt32(16, "Bounded pre-AW burst slots")
    pre_aw_beats = Param.UInt32(256, "Bounded pre-AW beat slots")
    b_rob_transactions = Param.UInt32(64, "B response transaction slots")
    r_rob_beats = Param.UInt32(1024, "R response beat slots")
    injection_delays = VectorParam.Cycles(
        [0, 0, 0], "Independent AW,W,AR injection delays"
    )
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
    source_nodes = VectorParam.UInt32([], "Quota source nodes")
    source_ports = VectorParam.UInt16([], "Quota source ports")
    quota_write_contexts = VectorParam.UInt32([], "Target write context quota")
    quota_write_beats = VectorParam.UInt32([], "Target write beat quota")
    quota_read_contexts = VectorParam.UInt32([], "Target read context quota")
    quota_read_beats = VectorParam.UInt32([], "Target read beat quota")
    memory_range_starts = VectorParam.UInt64([], "Simple-memory range starts")
    memory_range_ends = VectorParam.UInt64([], "Simple-memory range ends")
    target_write_contexts = Param.UInt32(64, "Shared target write contexts")
    target_write_assembly_beats = Param.UInt32(
        4096, "Target write assembly beat slots"
    )
    target_read_contexts = Param.UInt32(64, "Target read contexts")
    target_read_response_beats = Param.UInt32(
        4096, "Target read response reservations"
    )
    orphan_w_transactions = Param.UInt32(16, "W_ONLY transaction subquota")
    orphan_w_beats = Param.UInt32(256, "W_ONLY beat subquota")
    response_ready_depths = VectorParam.UInt32(
        [16, 128], "B response and R beat ready depths"
    )
    ingress_depths = VectorParam.UInt32(
        [16, 64, 32], "AW,W,AR adapter ingress depths"
    )
    wire_header_bytes = VectorParam.UInt32(
        [24, 16, 8, 24, 16], "AW,W,B,AR,R wire header bytes"
    )
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    raw_probe = Param.Bool(False, "Run Commit 1 raw shim probe")
    raw_probe_hold_cycles = Param.Cycles(
        8, "Cycles to hold the first AW in a depth-one local queue"
    )

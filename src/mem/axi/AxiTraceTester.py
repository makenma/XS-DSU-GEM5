from m5.objects.ClockedObject import ClockedObject
from m5.params import *


class AxiTraceTester(ClockedObject):
    type = "AxiTraceTester"
    cxx_header = "mem/axi/axi_trace_tester.hh"
    cxx_class = "gem5::axi::AxiTraceTester"

    initiators = VectorParam.AxiInitiatorAdapter([], "Driven initiators")
    targets = VectorParam.AxiTargetAdapter([], "Observed target memories")
    target_nodes = VectorParam.UInt32([], "Logical node for each target")
    transaction_specs = VectorParam.String([], "Resolved functional plan")
    case_name = Param.String("", "Stable integration case name")
    result_json = Param.String("", "Optional Commit 4 result summary")
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    drain_cycles = Param.UInt32(20, "Quiet adapter cycles before success")

from m5.objects.ClockedObject import ClockedObject
from m5.params import *


class AxiTraceTester(ClockedObject):
    type = "AxiTraceTester"
    cxx_header = "mem/axi/axi_trace_tester.hh"
    cxx_class = "gem5::axi::AxiTraceTester"

    initiators = VectorParam.AxiInitiatorAdapter([], "Driven initiators")
    targets = VectorParam.AxiTargetAdapter([], "Observed target memories")
    network = Param.GarnetNetwork("Observed protocol-neutral Garnet network")
    target_nodes = VectorParam.UInt32([], "Logical node for each target")
    transaction_specs = VectorParam.String([], "Resolved functional plan")
    case_name = Param.String("", "Stable integration case name")
    result_json = Param.String("", "Machine-readable integration result")
    data_bus_bytes = Param.UInt32(64, "AXI data bus width in bytes")
    drain_cycles = Param.UInt32(2, "Consecutive quiet network cycles")
    concurrent = Param.Bool(False, "Drive AW, W, and AR independently")
    consumer_stall_until = VectorParam.Cycles(
        [0, 0], "Absolute B and R local-handshake hold-off cycles"
    )
    expected_router_vnet = Param.Int32(
        -1, "Vnet whose configured VC depth must be reached, or -1"
    )
    expected_router_depth = Param.UInt32(
        0, "Expected full input VC depth for the targeted vnet"
    )
    progress_watchdog_cycles = Param.UInt32(
        256, "Eligible-response liveness bound"
    )
    issue_stop_cycle = Param.UInt64(
        0, "Scenario-declared last request creation cycle"
    )
    liveness_bound_components = VectorParam.UInt32(
        [0, 0, 0],
        "Target latency, forced stall, and packet/path liveness bounds",
    )
    local_delivery_depths = VectorParam.UInt32(
        [16, 64, 16, 32, 128], "AW,W,B,AR,R local buffer depths"
    )

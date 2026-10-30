from m5.objects.ClockedObject import ClockedObject
from m5.params import AddrRange, Param, RequestPort, VectorParam, VectorResponsePort


class ChiRnfBridge(ClockedObject):
    type = "ChiRnfBridge"
    cxx_class = "gem5::Chi::ChiRnfBridge"
    cxx_header = "mem/cache/CHI/ChiRnfBridge.hh"
    cpu_side = VectorResponsePort("CPU instruction, data and page table walker ports")
    chi = RequestPort("CHI link to the mesh router")
    node_id = Param.Unsigned(0, "Wire RNF node ID")
    hnf_ids = VectorParam.Unsigned([16, 144], "Homes selected by cache line modulo count")
    range = Param.AddrRange(AddrRange(0x80000000, size="128MiB"), "Coherent RAM range")
    cache_lines = Param.Unsigned(256, "Total lines in the finite unified RNF cache")
    cache_ways = Param.Unsigned(4, "Associativity, including separate ways for LR data and instruction fetch")
    hit_latency = Param.Cycles(1, "Minimum CPU request service latency")
    reservation_cycles = Param.Cycles(1024, "Bounded LR protection against ownership-transfer snoops")


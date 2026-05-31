from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject


class Chi2ClassicMemBridge(ClockedObject):
    type = "Chi2ClassicMemBridge"
    cxx_header = "mem/cache/CHI/Chi2ClassicMemBridge.hh"
    cxx_class = "gem5::Chi::Chi2ClassicMemBridge"

    chi_side = ResponsePort("SNF CHI side port")
    mem_side = RequestPort("Classic memory/cache side port")

    system = Param.System(Parent.any, "System this SN bridge belongs to")
    node_id = Param.UInt32(0, "SNF node id used as CHI SrcID")
    hnf_node_id = Param.UInt32(0, "Default HNF node id")
    block_size = Param.Unsigned(Parent.cache_line_size, "Cache line size")
    data_beat_bytes = Param.UInt32(32, "Bytes carried by one CHI DAT beat")
    max_outstanding = Param.UInt32(32, "Maximum outstanding SN reads")

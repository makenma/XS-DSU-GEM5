from m5.params import *
from m5.SimObject import SimObject
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject

class Cache2ChiBridge(ClockedObject):
    type = "Cache2ChiBridge"
    cxx_header = "mem/cache/CHI/Cache2ChiBridge.hh"
    cxx_class = "gem5::Chi::Cache2ChiBridge"

    cache_side = ResponsePort("Cache side (acts like memory for cache)")
    chi_side   = MasterPort("CHI side (sends CHI requests)")
    mem_side = RequestPort("Memory side (receives responses from memory)")

    wakeup_target = Param.SimObject(NULL, "HomeNodeFull to wake up when enqueue")
    block_size = Param.Unsigned(Parent.cache_line_size, "Cache line size in bytes")
    node_id = Param.UInt32(0, "RNF node id used as CHI SrcID/ReturnNID")
    home_node_id = Param.UInt32(0, "Default HNF node id used as CHI TgtID")
    num_txns = Param.UInt32(32, "Maximum number of outstanding RN transactions")
    data_beat_bytes = Param.UInt32(32, "Bytes carried by one CHI DAT beat")
    enable_retry = Param.Bool(True, "Set AllowRetry and handle RetryAck/PCrdGrant")

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
    home_node_ids = VectorParam.UInt32(
        [],
        "Optional CMN SCG HNF target-ID table; a power-of-two number of "
        "entries enables PA XOR hashing instead of home_node_id",
    )
    hnf_hash_pa_bits = Param.UInt8(
        48,
        "Implemented PA width for the CMN SCG HNF XOR hash; upper bits are zero",
    )
    txnid_base = Param.UInt32(
        0,
        "Exclusive lower bound for the initial monotonic TxnID allocation; "
        "it does not define a recycling window",
    )
    txnid_namespace_id = Param.UInt32(
        0,
        "Interleaved TxnID namespace owned by this bridge. Bridges sharing "
        "one CHI SrcID must use distinct IDs with the same namespace count",
    )
    txnid_namespace_count = Param.UInt32(
        1,
        "Number of interleaved TxnID namespaces sharing one CHI SrcID; "
        "namespace i uses IDs satisfying (TxnID - 1) mod count == i. IDs "
        "never wrap; exhausting a namespace terminates the simulation",
    )
    num_txns = Param.UInt32(
        32,
        "Maximum number of simultaneously outstanding RN transactions; "
        "independent of the monotonically allocated TxnID sequence",
    )
    data_beat_bytes = Param.UInt32(32, "Bytes carried by one CHI DAT beat")
    enable_retry = Param.Bool(True, "Set AllowRetry and handle RetryAck/PCrdGrant")
    sink_hnf_txreq = Param.Bool(
        True,
        "Drain HNF downstream TXREQ flits in direct bridge-HNF test topology")

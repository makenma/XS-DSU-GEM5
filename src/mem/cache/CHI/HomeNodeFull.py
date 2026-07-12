from m5.params import *
from m5.proxy import *

# Inherit parameters (x/y/port/device/node_type) from BasicChiComponent.
from m5.objects.BasicChiComponent import BasicChiComponent


class HomeNodeFull(BasicChiComponent):
    """A full home node model built on top of BasicChiComponent."""

    type = "HomeNodeFull"
    cxx_header = "mem/cache/CHI/HomeNodeFull.hh"
    cxx_class = "gem5::Chi::HomeNodeFull"

    rxport = SlavePort("CHI RX port")

    block_size = Param.Unsigned(Parent.cache_line_size, "Cache line size")
    data_beat_bytes = Param.UInt32(32, "Bytes carried by one CHI DAT beat")
    num_poc_entries = Param.UInt32(32, "Minimal HNF transaction entries")
    enable_retry = Param.Bool(True, "Enable RetryAck/PCrdGrant flow")
    sn_node_id = Param.UInt32(0, "Default SN node id used for HNF TXREQ")
    direct_sn_fake_data = Param.Bool(
        True,
        "Generate zero CompData after direct ReadNoSnp when no real SN exists")
    slc_num_sets = Param.UInt32(1024, "Number of modeled SLC sets")
    slc_num_ways = Param.UInt32(16, "Number of modeled SLC ways")
    sf_num_sets = Param.UInt32(1024, "Number of modeled SF sets")
    sf_num_ways = Param.UInt32(16, "Number of modeled SF ways")
    seq_entries = Param.UInt32(8, "SF victim SEQ entries")
    rnf_slices = Param.UInt32(
        1, "Address-interleaved CHI bridges attached to each RNF")

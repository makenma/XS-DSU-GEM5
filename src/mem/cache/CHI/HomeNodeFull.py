from m5.params import *
from m5.proxy import *

# Inherit parameters (x/y/port/device/node_type) from BasicChiComponent.
from m5.objects.BasicChiComponent import BasicChiComponent
from m5.objects.SlcSnoopFilter import SlcSnoopFilter


class HomeNodeFull(BasicChiComponent):
    """A full home node model built on top of BasicChiComponent."""

    type = "HomeNodeFull"
    cxx_header = "mem/cache/CHI/HomeNodeFull.hh"
    cxx_class = "gem5::Chi::HomeNodeFull"

    rxport = SlavePort("CHI RX port")
    slcsf = Param.SlcSnoopFilter(
        SlcSnoopFilter(), "Standalone SLC and snoop-filter child")

    block_size = Param.Unsigned(Parent.cache_line_size, "Cache line size")
    data_beat_bytes = Param.UInt32(32, "Bytes carried by one CHI DAT beat")
    num_poc_entries = Param.UInt32(32, "Minimal HNF transaction entries")
    enable_retry = Param.Bool(True, "Enable RetryAck/PCrdGrant flow")
    sn_node_id = Param.UInt32(0, "Default SN node id used for HNF TXREQ")
    sn_node_ids = VectorParam.UInt32(
        [],
        "SN target table for DDR address-interleaved HNF TXREQ. A "
        "power-of-two number of entries enables 64-byte cache-line "
        "interleave ddr_index=(addr>>log2(block_size))&(count-1); an "
        "empty list falls back to the scalar sn_node_id",
    )
    direct_sn_fake_data = Param.Bool(
        True,
        "Generate zero CompData after direct ReadNoSnp when no real SN exists")
    # Compatibility parameter surface. The SlcSnoopFilter child resolves
    # these through Parent proxies; runtime C++ reads only the child params.
    # An explicitly configured child parameter therefore takes precedence.
    slc_num_sets = Param.UInt32(1024, "Default modeled SLC sets")
    slc_num_ways = Param.UInt32(16, "Default modeled SLC ways")
    slc_replacement_policy = Param.String(
        "lru",
        "Default SLC replacement policy: lru, random, or srrip",
    )
    slc_replacement_seed = Param.UInt64(
        1, "Default deterministic SLC pseudo-random replacement seed")
    slc_restore_allow_policy_override = Param.Bool(
        False,
        "Allow an experiment-generated checkpoint to start a new SLC policy",
    )
    sf_num_sets = Param.UInt32(1024, "Default modeled SF sets")
    sf_num_ways = Param.UInt32(16, "Default modeled SF ways")
    seq_entries = Param.UInt32(8, "Default SF victim SEQ entries")
    slcsf_lookup_latency = Param.Cycles(4, "SLCSF lookup service latency")
    slcsf_fill_latency = Param.Cycles(4, "SLCSF fill service latency")
    slcsf_update_latency = Param.Cycles(3, "SLCSF update service latency")
    slcsf_victim_latency = Param.Cycles(
        3, "Deprecated compatibility knob; dirty victims hand off to PoCQ")
    slcsf_sf_evict_latency = Param.Cycles(2, "SLCSF SF eviction latency")
    slcsf_replay_penalty = Param.Cycles(2, "SLCSF replay delay")
    slcsf_req_queue_entries = Param.UInt32(8, "SLCSF request queue entries")
    slcsf_resp_queue_entries = Param.UInt32(8, "SLCSF response queue entries")
    slcsf_victim_buffer_entries = Param.UInt32(
        2, "Deprecated compatibility knob; no SLC VictimBuffer is modeled")
    slcsf_lookup_issue_width = Param.UInt32(1, "SLCSF lookup issue width")
    slcsf_fill_issue_width = Param.UInt32(1, "SLCSF fill issue width")
    slcsf_update_issue_width = Param.UInt32(1, "SLCSF update issue width")
    slcsf_max_inflight = Param.UInt32(
        1, "Maximum in-flight SLCSF operations")
    slcsf_response_consume_width = Param.UInt32(
        1, "Maximum SLCSF responses consumed by CC per cycle")
    slcsf_enable_set_lock = Param.Bool(
        False, "Enable SLCSF set conflict locking")
    rnf_slices = Param.UInt32(
        1, "Address-interleaved CHI bridges attached to each RNF")

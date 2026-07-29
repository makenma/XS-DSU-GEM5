from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *


class SlcSnoopFilter(ClockedObject):
    """Standalone clocked owner for HNF SLC and snoop-filter state."""

    type = "SlcSnoopFilter"
    cxx_header = "mem/cache/CHI/SlcSnoopFilter.hh"
    cxx_class = "gem5::Chi::SlcSnoopFilter"

    # Parent proxies preserve the existing HomeNodeFull configuration surface
    # during the standalone-object migration.
    block_size = Param.Unsigned(Parent.block_size, "Cache line size")
    slc_num_sets = Param.UInt32(Parent.slc_num_sets, "Modeled SLC sets")
    slc_num_ways = Param.UInt32(Parent.slc_num_ways, "Modeled SLC ways")
    sf_num_sets = Param.UInt32(Parent.sf_num_sets, "Modeled SF sets")
    sf_num_ways = Param.UInt32(Parent.sf_num_ways, "Modeled SF ways")
    seq_entries = Param.UInt32(Parent.seq_entries, "SF victim SEQ entries")

    slcsf_lookup_latency = Param.Cycles(
        Parent.slcsf_lookup_latency, "SLCSF lookup service latency")
    slcsf_fill_latency = Param.Cycles(
        Parent.slcsf_fill_latency, "SLCSF fill service latency")
    slcsf_update_latency = Param.Cycles(
        Parent.slcsf_update_latency, "SLCSF update service latency")
    slcsf_victim_latency = Param.Cycles(
        Parent.slcsf_victim_latency, "SLCSF dirty victim latency")
    slcsf_sf_evict_latency = Param.Cycles(
        Parent.slcsf_sf_evict_latency, "SLCSF SF eviction latency")
    slcsf_replay_penalty = Param.Cycles(
        Parent.slcsf_replay_penalty, "SLCSF replay delay")
    slcsf_req_queue_entries = Param.UInt32(
        Parent.slcsf_req_queue_entries, "SLCSF request queue entries")
    slcsf_resp_queue_entries = Param.UInt32(
        Parent.slcsf_resp_queue_entries, "SLCSF response queue entries")
    slcsf_victim_buffer_entries = Param.UInt32(
        Parent.slcsf_victim_buffer_entries, "SLCSF VictimBuffer entries")
    slcsf_lookup_issue_width = Param.UInt32(
        Parent.slcsf_lookup_issue_width, "SLCSF lookup issue width")
    slcsf_fill_issue_width = Param.UInt32(
        Parent.slcsf_fill_issue_width, "SLCSF fill issue width")
    slcsf_update_issue_width = Param.UInt32(
        Parent.slcsf_update_issue_width, "SLCSF update issue width")
    slcsf_max_inflight = Param.UInt32(
        Parent.slcsf_max_inflight, "Maximum in-flight SLCSF operations")
    slcsf_response_consume_width = Param.UInt32(
        Parent.slcsf_response_consume_width,
        "Maximum SLCSF responses consumed by CC per cycle")
    slcsf_enable_set_lock = Param.Bool(
        Parent.slcsf_enable_set_lock, "Enable SLCSF set conflict locking")

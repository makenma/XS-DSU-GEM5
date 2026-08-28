from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *
from m5.SimObject import *


class SlcSnoopFilter(ClockedObject):
    """Standalone clocked owner for HNF SLC and snoop-filter state."""

    type = "SlcSnoopFilter"
    cxx_header = "mem/cache/CHI/SlcSnoopFilter.hh"
    cxx_class = "gem5::Chi::SlcSnoopFilter"
    cxx_exports = [
        PyBindMethod("slcValidLineCount"),
        PyBindMethod("slcCapacityLineCount"),
        PyBindMethod("maxValidWaysInSet"),
        PyBindMethod("rearmSlcFullExit"),
    ]

    system = Param.System(Parent.any, "System containing the HN-F SLC")
    # Cache2ChiBridge sends functional writes directly to classic memory, so
    # the SLC must commit its potentially stale lower copy before private
    # caches overwrite memory with the newest owner data.
    checkpoint_writeback_priority = Param.Int(
        -1, "Checkpoint writeback priority; lower values run first")
    cache_level = Param.Unsigned(3, "Checkpoint writeback hierarchy level")

    # These are the canonical runtime parameters. Parent proxies preserve the
    # old HomeNodeFull paths as defaults, while explicit child values win by
    # normal SimObject parameter resolution.
    block_size = Param.Unsigned(Parent.block_size, "Cache line size")
    slc_num_sets = Param.UInt32(Parent.slc_num_sets, "Modeled SLC sets")
    slc_num_ways = Param.UInt32(Parent.slc_num_ways, "Modeled SLC ways")
    slc_replacement_policy = Param.String(
        Parent.slc_replacement_policy,
        "SLC replacement policy: lru, random, or srrip",
    )
    slc_replacement_seed = Param.UInt64(
        Parent.slc_replacement_seed,
        "Deterministic SLC pseudo-random replacement seed",
    )
    slc_restore_allow_policy_override = Param.Bool(
        Parent.slc_restore_allow_policy_override,
        "Allow replacement-policy override when restoring common warm-up state",
    )
    exit_on_slc_full = Param.Bool(
        False, "Exit the simulation loop once when the SLC first becomes full")
    sf_num_sets = Param.UInt32(Parent.sf_num_sets, "Modeled SF sets")
    sf_num_ways = Param.UInt32(Parent.sf_num_ways, "Modeled SF ways")
    seq_entries = Param.UInt32(Parent.seq_entries, "SF victim SEQ entries")
    init_latency = Param.Cycles(
        16, "Abstract SLC/SF cold initialization latency")

    slcsf_lookup_latency = Param.Cycles(
        Parent.slcsf_lookup_latency, "SLCSF lookup service latency")
    slcsf_fill_latency = Param.Cycles(
        Parent.slcsf_fill_latency, "SLCSF fill service latency")
    slcsf_update_latency = Param.Cycles(
        Parent.slcsf_update_latency, "SLCSF update service latency")
    slcsf_victim_latency = Param.Cycles(
        Parent.slcsf_victim_latency,
        "Deprecated compatibility knob; dirty victims hand off to PoCQ")
    slcsf_sf_evict_latency = Param.Cycles(
        Parent.slcsf_sf_evict_latency, "SLCSF SF eviction latency")
    slcsf_replay_penalty = Param.Cycles(
        Parent.slcsf_replay_penalty, "SLCSF replay delay")
    slcsf_req_queue_entries = Param.UInt32(
        Parent.slcsf_req_queue_entries, "SLCSF request queue entries")
    slcsf_resp_queue_entries = Param.UInt32(
        Parent.slcsf_resp_queue_entries, "SLCSF response queue entries")
    slcsf_victim_buffer_entries = Param.UInt32(
        Parent.slcsf_victim_buffer_entries,
        "Deprecated compatibility knob; no SLC VictimBuffer is modeled")
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

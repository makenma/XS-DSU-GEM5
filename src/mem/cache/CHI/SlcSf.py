from m5.objects.ClockedObject import ClockedObject
from m5.params import Param


class SlcSf(ClockedObject):
    """CMN ordinary SLC/SF model: LAT2, no ECC and no data slice."""

    type = "SlcSf"
    cxx_header = "mem/cache/CHI/SlcSf.hh"
    cxx_class = "gem5::Chi::SlcSf"

    rnf_num = Param.Unsigned(1, "Number of RN-F nodes")
    home_count = Param.Unsigned(1, "Number of line-interleaved HNF banks")
    home_index = Param.Unsigned(0, "This HNF's physical line modulo home_count")

    l3_tag_sets = Param.Unsigned(1024, "Number of L3 tag RAM sets")
    l3_tag_ways = Param.Unsigned(16, "Number of L3 tag RAM ways")

    sf_tag_sets = Param.Unsigned(2048, "Number of SF tag RAM sets")
    sf_tag_ways = Param.Unsigned(16, "Number of SF tag RAM ways")

    cache_line_size = Param.Unsigned(
        64, "Bytes in each L3 data RAM entry"
    )

    ram_latency = Param.Unsigned(2, "Tag/data RAM latency; only 2 is supported")
    seq_entries = Param.Unsigned(8, "Ordinary snoop eviction queue entries")
    replacement_seed = Param.Unsigned(0, "Deterministic four-bit SF LFSR seed")

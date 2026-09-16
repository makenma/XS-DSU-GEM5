from m5.objects.ClockedObject import ClockedObject
from m5.params import Param


class SlcSf(ClockedObject):
    """Skeleton of the SLC/SF lookup and data pipeline."""

    type = "SlcSf"
    cxx_header = "mem/cache/CHI/SlcSf.hh"
    cxx_class = "gem5::Chi::SlcSf"

    rnf_num = Param.Unsigned(1, "Number of RN-F nodes")

    l3_tag_sets = Param.Unsigned(1024, "Number of L3 tag RAM sets")
    l3_tag_ways = Param.Unsigned(16, "Number of L3 tag RAM ways")

    sf_tag_sets = Param.Unsigned(2048, "Number of SF tag RAM sets")
    sf_tag_ways = Param.Unsigned(16, "Number of SF tag RAM ways")

    cache_line_size = Param.Unsigned(
        64, "Bytes in each L3 data RAM entry"
    )

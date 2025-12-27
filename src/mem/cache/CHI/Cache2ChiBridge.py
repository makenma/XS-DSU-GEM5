from m5.params import *
from m5.SimObject import SimObject
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject

class Cache2ChiBridge(ClockedObject):
    type = "Cache2ChiBridge"
    cxx_header = "mem/cache/CHI/Cache2ChiBridge.hh"
    cxx_class = "gem5::Cache2ChiBridge"

    cache_side = ResponsePort("Cache side (acts like memory for cache)")
    chi_side   = RequestPort("CHI side (sends CHI requests)")


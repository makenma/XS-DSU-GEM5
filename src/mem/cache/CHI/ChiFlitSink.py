from m5.params import *
from m5.proxy import *
from m5.objects.ClockedObject import ClockedObject

class ChiFlitSink(ClockedObject):
    type = "ChiFlitSink"
    cxx_header = "mem/cache/CHI/ChiFlitSink.hh"
    cxx_class = "gem5::ChiFlitSink"

    chi_side = ResponsePort("Sink CHI side port")

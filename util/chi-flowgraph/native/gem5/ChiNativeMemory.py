from m5.params import *
from m5.proxy import Parent
from m5.objects.ClockedObject import ClockedObject


class ChiNativeMemory(ClockedObject):
    type = 'ChiNativeMemory'
    cxx_header = 'mem/cache/CHI/ChiNativeMemory.hh'
    cxx_class = 'gem5::Chi::ChiNativeMemory'
    chi_side = ResponsePort('Native CHI subordinate endpoint')
    mem_side = RequestPort('Timing memory data source')
    node_id = Param.UInt32(0, 'SN NodeID')
    system = Param.System(Parent.any, 'Parent system')
    max_outstanding = Param.UInt32(32, 'Bounded active memory transactions')

from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import Parent
from m5.SimObject import PyBindMethod
class ChiTestControl(ClockedObject):
    type = 'ChiTestControl'
    cxx_header = 'mem/cache/CHI/ChiTestControl.hh'
    cxx_class = 'gem5::Chi::ChiTestControl'
    pio = ResponsePort('Uncached per-hart test descriptor page')
    base = Param.Addr(0, 'Physical control page outside DRAM')
    node_id = Param.UInt32(0, 'RN-F NodeID')
    cache = Param.ChiIntentCache('Private L2 real tags and data')
    bridge = Param.Cache2ChiBridge('Private RN-F')
    system = Param.System(Parent.any, 'System requestor IDs')
    accept_stash = Param.Bool(True, 'Accept one DataPull when target line is absent')
    cxx_exports = [PyBindMethod('resultWord'),PyBindMethod('setSharedBase')]

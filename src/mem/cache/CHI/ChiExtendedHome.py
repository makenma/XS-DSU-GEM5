from m5.params import *
from m5.objects.ClockedObject import ClockedObject

class ChiExtendedHome(ClockedObject):
    type = 'ChiExtendedHome'
    cxx_header = 'mem/cache/CHI/ChiExtendedHome.hh'
    cxx_class = 'gem5::Chi::ChiExtendedHome'
    rxport = ResponsePort('CHI endpoint port')
    node_id = Param.UInt32(0, 'HN-F NodeID')
    sn_node_ids = VectorParam.UInt32([], 'SN target table')
    sn_hash_masks = VectorParam.UInt64([], 'SN index XOR masks')
    num_entries = Param.UInt32(32, 'In-flight requests')
    slc_lines = Param.UInt32(256, 'SLC capacity in cache lines')

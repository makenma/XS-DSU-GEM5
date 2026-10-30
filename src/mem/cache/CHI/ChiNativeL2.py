from m5.objects.ClockedObject import ClockedObject
from m5.params import *


class ChiNativeL2(ClockedObject):
    """Native L2 adapted to ancient_coding's exclusive-only HNF profile.

    Misses use ReadUnique, dirty victims use WriteBackFull and clean victims
    use CleanInvalid. MMIO belongs on a separate platform bus. Warm native
    checkpoints are rejected because this L2 has no state serialization.
    """
    type = 'ChiNativeL2'
    cxx_header = 'mem/cache/CHI/ChiNativeL2.hh'
    cxx_class = 'gem5::Chi::ChiNativeL2'
    cpu_side = ResponsePort('Classic L1 coherence boundary')
    chi_side = RequestPort('Native CHI RN-F boundary, 256-bit DAT')
    functional = RequestPort('Functional initialization/debug memory access')
    node_id = Param.UInt32(0, 'RN-F NodeID')
    home_node_ids = VectorParam.UInt32([], 'Home node table')
    home_hash_masks = VectorParam.UInt64([], 'Parity masks selecting the home')
    sets = Param.UInt32(512, 'Cache sets, 64-byte lines')
    assoc = Param.UInt32(8, 'Ways per set')
    mshrs = Param.UInt32(32, 'Demand plus victim MSHRs')
    request_queue = Param.UInt32(32, 'Accepted L1 requests waiting for an MSHR')
    reservation_cycles = Param.Cycles(256, 'Bounded LR miss protection, including instruction refill after LR')
    write_evict_or_evict = Param.Bool(False, 'Reserved; ancient HNF uses CleanInvalid for clean replacement')
    ranges = VectorParam.AddrRange([AddrRange('256MiB')], 'Coherent and non-cacheable address ranges')

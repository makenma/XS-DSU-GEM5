#ifndef __MEM_CACHE_CHI_NATIVE_REQUEST_HH__
#define __MEM_CACHE_CHI_NATIVE_REQUEST_HH__

#include "mem/packet.hh"

namespace gem5::Chi {

// Match classic MSHR::replaceUpgrade when a preceding invalidation removes
// the retained L1 copy. In particular, an SC must retain its failure marker:
// converting SCUpgradeReq to ReadExReq would turn UpgradeFailResp into a
// successful read-exclusive response and lose the reservation failure.
inline void promoteInvalidatedUpgrade(PacketPtr packet)
{
    if (!packet->isUpgrade())
        return;
    const bool hadData = packet->hasData() || packet->hasRespData();
    packet->cmd = packet->cmd == MemCmd::SCUpgradeReq ?
        MemCmd::SCUpgradeFailReq : MemCmd::ReadExReq;
    if (!hadData && packet->hasRespData())
        packet->allocate();
}

} // namespace gem5::Chi
#endif

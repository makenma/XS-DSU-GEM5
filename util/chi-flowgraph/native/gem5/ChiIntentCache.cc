#include "mem/cache/CHI/ChiIntentCache.hh"
#include "mem/cache/CHI/ChiProtocolIntent.hh"
#include "mem/cache/CHI/FlowGraphTrace.hh"
#include "base/logging.hh"
#include "mem/cache/tags/base.hh"

namespace gem5::Chi {
ChiIntentCache::ChiIntentCache(const ChiIntentCacheParams& p) : Cache(p),
    releaseEvent([this] {
        auto queued = std::move(held); held.clear();
        for (auto* packet : queued) Cache::recvTimingReq(packet);
    }, name()+".intentRelease") {}

void ChiIntentCache::recvTimingReq(PacketPtr pkt) {
    if (lockedLine) { held.push_back(pkt); return; }
    Cache::recvTimingReq(pkt);
}

bool ChiIntentCache::prepareIntent(Addr address, Snapshot& snapshot) {
    if (lockedLine || !mshrQueue.isEmpty() || !writeBuffer.isEmpty()) return false;
    lockedLine = address & ~(Addr(blkSize)-1);
    auto* block = tags->findBlock(*lockedLine, false);
    if (block && block->isValid()) {
        snapshot.valid=true; snapshot.dirty=block->isSet(CacheBlk::DirtyBit); snapshot.writable=block->isSet(CacheBlk::WritableBit);
        snapshot.data.assign(block->data, block->data+blkSize);
    }
    return true;
}

void ChiIntentCache::commitIntent(Addr address, uint8_t opcode,
                                 const std::vector<uint8_t>& data, bool dirty, bool shared) {
    const Addr line = address & ~(Addr(blkSize)-1);
    panic_if(!lockedLine || *lockedLine!=line, "CHI intent commit without line ownership\n");
    auto* block = tags->findBlock(line, false);
    if (chiAllocating(opcode) || opcode==0x0c) {
        panic_if(data.size()!=blkSize, "CHI allocating intent requires complete line\n");
        auto request=std::make_shared<Request>(line,blkSize,0,Request::funcRequestorId);
        Packet packet(request,MemCmd::ReadResp); packet.allocate(); packet.setData(data.data());
        if (shared) packet.setHasSharers();
        if (dirty) packet.setCacheResponding();
        PacketList writebacks;
        block=handleFill(&packet,block,writebacks,true);
        panic_if(!block || block==tempBlock, "CHI intent could not allocate real L2 line\n");
        panic_if(opcode==0x26 && block->isSet(CacheBlk::DirtyBit) && !block->isSet(CacheBlk::WritableBit),"ReadNotSharedDirty installed SD\n");
        doWritebacks(writebacks,clockEdge(Cycles(1)));
    } else if (opcode==0x08) {
        if (block && block->isValid()) block->clearCoherenceBits(CacheBlk::DirtyBit);
    } else if (opcode==0x09 || opcode==0x0a || opcode==0x0d || chiCopyback(opcode)) {
        if (block && block->isValid()) invalidateBlock(block);
    }
    abortIntent();
}

void ChiIntentCache::recordState(Addr address,const FlowGraphIdentity& flow,uint32_t node) {
    auto* block=tags->findBlock(address&~Addr(63),false);
    nlohmann::ordered_json value={{"state","I"},{"dirty",false}};
    if(block&&block->isValid()) {
        const bool dirty=block->isSet(CacheBlk::DirtyBit),writable=block->isSet(CacheBlk::WritableBit);
        value["state"]=writable?(dirty?"UD":"UC"):(dirty?"SD":"SC");value["dirty"]=dirty;
        value["data"]=FlowGraphTrace::bytes(std::vector<uint8_t>(block->data,block->data+64));value["validMask"]=std::string(128,'f');
    }
    FlowGraphTrace::get().event(flow,"state",node,{{"state",value}});
}

void ChiIntentCache::abortIntent() {
    lockedLine.reset();
    if (!held.empty() && !releaseEvent.scheduled()) schedule(releaseEvent,clockEdge(Cycles(1)));
}
}

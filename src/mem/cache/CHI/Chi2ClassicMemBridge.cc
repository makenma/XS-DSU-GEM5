#include "mem/cache/CHI/Chi2ClassicMemBridge.hh"

#include <algorithm>
#include <cstring>
#include <memory>
#include <type_traits>
#include <utility>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/Chi2ClassicMemBridge.hh"
#include "debug/HnfDirtyVictimE2E.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5
{

namespace Chi
{

Chi2ClassicMemBridge::MemSidePort::MemSidePort(
    const std::string& name, Chi2ClassicMemBridge& owner)
    : RequestPort(name, &owner), owner(owner)
{
}

bool
Chi2ClassicMemBridge::MemSidePort::recvTimingResp(PacketPtr pkt)
{
    return owner.recvMemResp(pkt);
}

void
Chi2ClassicMemBridge::MemSidePort::recvReqRetry()
{
    owner.memReqBlocked = false;
    owner.schedulePump();
}

void
Chi2ClassicMemBridge::MemSidePort::recvRangeChange()
{
    DPRINTF(Chi2ClassicMemBridge, "classic memory side range change\n");
}

Chi2ClassicMemBridge::Chi2ClassicMemBridge(
    const Chi2ClassicMemBridgeParams& p)
    : ClockedObject(p),
      ruby::Consumer(this),
      chiPort(csprintf("%s.chi_side", name()),
              static_cast<ruby::Consumer*>(this), 0),
      memPort(csprintf("%s.mem_side", name()), *this),
      nodeId(p.node_id),
      hnfNodeId(p.hnf_node_id),
      blockSize(p.block_size),
      dataBeatBytes(p.data_beat_bytes),
      maxOutstanding(p.max_outstanding),
      requestorId(p.system->getRequestorId(this)),
      pumpEvent([this]{ pump(); }, name() + ".pumpEvent")
{
    fatal_if(blockSize == 0 || !isPowerOf2(blockSize),
             "%s requires a non-zero power-of-two block_size\n", name());
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "%s requires 0 < data_beat_bytes <= block_size\n", name());
    fatal_if(maxOutstanding == 0,
             "%s requires max_outstanding > 0\n", name());
}

Port&
Chi2ClassicMemBridge::getPort(const std::string& if_name, PortID idx)
{
    if (if_name == "chi_side") {
        return chiPort;
    }
    if (if_name == "mem_side") {
        return memPort;
    }
    return ClockedObject::getPort(if_name, idx);
}

void
Chi2ClassicMemBridge::wakeup()
{
    schedulePump();
}

void
Chi2ClassicMemBridge::print(std::ostream& out) const
{
    out << "Chi2ClassicMemBridge(" << name() << ")";
}

void
Chi2ClassicMemBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        schedule(pumpEvent, nextCycle());
    }
}

bool
Chi2ClassicMemBridge::hasPumpWork() const
{
    return pendingReq || !memReqQ.empty() || blockedPkt ||
        !txDatQ.empty() || !txRspQ.empty() || !txns.empty() ||
        chiPort.hasTxFlit(REQ) || chiPort.hasTxFlit(DAT);
}

uint32_t
Chi2ClassicMemBridge::expectedDataBytes(const RawReq& req) const
{
    return Chi2ClassicMemTxnPolicy::expectedDataBytes(req, blockSize);
}

PacketPtr
Chi2ClassicMemBridge::makeReadPacket(const RawReq& req)
{
    const uint32_t bytes = expectedDataBytes(req);
    RequestPtr request =
        std::make_shared<Request>(req.addr, bytes, 0, requestorId);
    auto* pkt = new Packet(request, MemCmd::ReadReq);
    pkt->allocate();
    pkt->pushSenderState(new SnfSenderState(req.txnid));
    return pkt;
}

PacketPtr
Chi2ClassicMemBridge::makeWritePacket(const TxnEntry& txn,
                                      const RawDat& dat)
{
    RequestPtr request = std::make_shared<Request>(
        txn.req.addr, txn.expectedBytes, 0, requestorId);
    auto* pkt = new Packet(request, MemCmd::WriteReq);
    pkt->allocate();
    std::memcpy(pkt->getPtr<uint8_t>(), dat.data.data(), txn.expectedBytes);
    pkt->pushSenderState(new SnfSenderState(txn.req.txnid));
    return pkt;
}

void
Chi2ClassicMemBridge::sendPendingMemReq()
{
    if (memReqQ.empty() || memReqBlocked) {
        return;
    }

    const uint32_t txnid = memReqQ.front();
    auto it = txns.find(txnid);
    panic_if(it == txns.end(),
             "%s has classic request for unknown txnid=%u\n", name(), txnid);
    TxnEntry& txn = it->second;
    panic_if(txn.phase != TxnEntry::Phase::MemReqQueued || !txn.pkt,
             "%s has invalid queued classic request txnid=%u phase=%u\n",
             name(), txnid, static_cast<unsigned>(txn.phase));
    panic_if(blockedPkt && blockedPkt != txn.pkt,
             "%s changed classic packet while retry was pending\n", name());

    PacketPtr pkt = txn.pkt;
    const bool accepted = memPort.sendTimingReq(pkt);
    const auto next = Chi2ClassicMemTxnPolicy::transition(
        txn.phase, Chi2ClassicMemTxnPolicy::Event::ClassicRequestSent,
        accepted);
    panic_if(!next,
             "%s has illegal classic-request transition txnid=%u phase=%u\n",
             name(), txnid, static_cast<unsigned>(txn.phase));
    if (!accepted) {
        blockedPkt = pkt;
        memReqBlocked = true;
        DPRINTF(Chi2ClassicMemBridge,
                "classic %s blocked txn=%u addr=%#llx size=%u\n",
                pkt->cmdString().c_str(), txnid,
                static_cast<unsigned long long>(pkt->getAddr()),
                pkt->getSize());
        return;
    }

    if (blockedPkt == pkt) {
        blockedPkt = nullptr;
    }
    memReqQ.pop_front();
    txn.phase = *next;
    DPRINTF(Chi2ClassicMemBridge,
            "classic %s sent txn=%u addr=%#llx size=%u\n",
            pkt->cmdString().c_str(), txnid,
            static_cast<unsigned long long>(pkt->getAddr()), pkt->getSize());
}

void
Chi2ClassicMemBridge::acceptReadNoSnp(const RawReq& req)
{
    const auto decoded = decodeReq(req.opcode);
    if (decoded.minor != ReqMinor::ReadNoSnp) {
        warn("%s drops unsupported SNF REQ opcode=0x%x src=%u tgt=%u "
             "txn=%u\n",
             name(), req.opcode, req.srcid, req.tgtid, req.txnid);
        return;
    }

    panic_if(txns.count(req.txnid),
             "%s duplicate ReadNoSnp txnid=%u src=%u\n",
             name(), req.txnid, req.srcid);

    TxnEntry entry{};
    entry.req = req;
    entry.kind = TxnEntry::Kind::Read;
    entry.phase = TxnEntry::Phase::MemReqQueued;
    entry.expectedBytes = expectedDataBytes(req);
    entry.pkt = makeReadPacket(req);
    const bool inserted = txns.emplace(req.txnid, std::move(entry)).second;
    panic_if(!inserted, "%s failed to insert ReadNoSnp txnid=%u\n",
             name(), req.txnid);

    DPRINTF(Chi2ClassicMemBridge,
            "accept ReadNoSnp src=%u tgt=%u txn=%u addr=%#llx bytes=%u "
            "to classic\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr), entry.expectedBytes);
    memReqQ.push_back(req.txnid);
}

std::optional<uint8_t>
Chi2ClassicMemBridge::allocateDbid()
{
    for (uint32_t attempts = 0; attempts < UINT8_MAX; ++attempts) {
        uint8_t candidate = nextDbid++;
        if (candidate == 0) {
            candidate = nextDbid++;
        }
        const bool inUse = std::any_of(
            txns.begin(), txns.end(), [candidate](const auto& item) {
                return item.second.kind == TxnEntry::Kind::Write &&
                    item.second.dbid == candidate;
            });
        if (!inUse) {
            return candidate;
        }
    }
    return std::nullopt;
}

bool
Chi2ClassicMemBridge::acceptWriteNoSnpFull(const RawReq& req)
{
    if (!chiPort.peerHasRxCredit(RSP)) {
        return false;
    }

    panic_if(!Chi2ClassicMemTxnPolicy::isWriteNoSnpFull(req, blockSize),
             "%s invalid WriteNoSnpFull txn=%u addr=%#llx bytes=%u; "
             "expected exact opcode and aligned full line of %u bytes\n",
             name(), req.txnid, static_cast<unsigned long long>(req.addr),
             expectedDataBytes(req), blockSize);
    panic_if(txns.count(req.txnid),
             "%s duplicate WriteNoSnpFull txnid=%u src=%u\n",
             name(), req.txnid, req.srcid);

    std::optional<uint8_t> dbid = allocateDbid();
    if (!dbid) {
        return false;
    }

    TxnEntry entry{};
    entry.req = req;
    entry.kind = TxnEntry::Kind::Write;
    entry.phase = TxnEntry::Phase::WriteDataPending;
    entry.expectedBytes = expectedDataBytes(req);
    entry.dbid = *dbid;
    auto [it, inserted] = txns.emplace(req.txnid, std::move(entry));
    panic_if(!inserted, "%s failed to insert WriteNoSnpFull txnid=%u\n",
             name(), req.txnid);

    const RawRsp rsp = Chi2ClassicMemTxnPolicy::makeDbidResp(
        it->second.req, it->second.dbid, nodeId, hnfNodeId);
    const bool accepted = chiPort.enqueueRx(RSP, rsp);
    panic_if(!accepted,
             "%s lost reserved RSP credit for WriteNoSnpFull txnid=%u\n",
             name(), req.txnid);
    DPRINTF(Chi2ClassicMemBridge,
            "accept WriteNoSnpFull src=%u tgt=%u txn=%u addr=%#llx "
            "bytes=%u dbid=%u\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr),
            it->second.expectedBytes, it->second.dbid);
    return true;
}

bool
Chi2ClassicMemBridge::tryAcceptPendingReq()
{
    if (!pendingReq) {
        return true;
    }
    if (txns.size() >= maxOutstanding) {
        return false;
    }

    const RawReq& req = *pendingReq;
    const auto decoded = decodeReq(req.opcode);
    if (decoded.minor == ReqMinor::ReadNoSnp &&
        req.opcode == Chi2ClassicMemTxnPolicy::ReadNoSnpOpcode) {
        acceptReadNoSnp(req);
        pendingReq.reset();
        return true;
    }
    if (decoded.minor == ReqMinor::WriteNoSnp &&
        req.opcode == Chi2ClassicMemTxnPolicy::WriteNoSnpFullOpcode) {
        if (!acceptWriteNoSnpFull(req)) {
            return false;
        }
        pendingReq.reset();
        return true;
    }

    warn("%s drops unsupported SNF REQ opcode=0x%x src=%u tgt=%u "
         "txn=%u\n",
         name(), req.opcode, req.srcid, req.tgtid, req.txnid);
    pendingReq.reset();
    return true;
}

void
Chi2ClassicMemBridge::drainChiReq()
{
    while (true) {
        if (!tryAcceptPendingReq()) {
            return;
        }
        auto flit = chiPort.getTxFlit(REQ);
        if (!flit) {
            return;
        }
        panic_if(!std::holds_alternative<RawReq>(*flit),
                 "%s sampled non-REQ flit from REQ channel\n", name());
        pendingReq = std::get<RawReq>(std::move(*flit));
    }
}

void
Chi2ClassicMemBridge::acceptWriteData(const RawDat& dat)
{
    auto it = txns.find(dat.txnid);
    panic_if(it == txns.end(),
             "%s got write DAT for unknown txnid=%u dbid=%u\n",
             name(), dat.txnid, dat.dbid);
    TxnEntry& txn = it->second;
    panic_if(txn.kind != TxnEntry::Kind::Write ||
                 txn.phase != TxnEntry::Phase::WriteDataPending,
             "%s got duplicate/out-of-phase write DAT txnid=%u dbid=%u "
             "phase=%u\n",
             name(), dat.txnid, dat.dbid,
             static_cast<unsigned>(txn.phase));
    panic_if(!Chi2ClassicMemTxnPolicy::isExactWriteData(
                 dat, txn.req, txn.dbid, txn.expectedBytes, nodeId),
             "%s got invalid NonCopyBackWriteData txnid=%u dbid=%u "
             "src=%u tgt=%u bytes=%u expected=%u\n",
             name(), dat.txnid, dat.dbid,
             dat.srcid, dat.tgtid,
             static_cast<unsigned>(dat.data.size()), txn.expectedBytes);

    if (Chi2ClassicMemTxnPolicy::isHnfDirtyVictimWriteback(
            txn.req, blockSize)) {
        DPRINTF(HnfDirtyVictimE2E,
                "SN_DV_DAT txn=%u addr=%#llx dbid=%u bytes=%u "
                "data_hash=%016llx\n",
                dat.txnid,
                static_cast<unsigned long long>(txn.req.addr), dat.dbid,
                static_cast<unsigned>(dat.data.size()),
                static_cast<unsigned long long>(
                    Chi2ClassicMemTxnPolicy::traceDataHash(dat.data)));
    }

    txn.pkt = makeWritePacket(txn, dat);
    const auto next = Chi2ClassicMemTxnPolicy::transition(
        txn.phase, Chi2ClassicMemTxnPolicy::Event::WriteDataAccepted);
    panic_if(!next,
             "%s has illegal write-data transition txnid=%u phase=%u\n",
             name(), dat.txnid, static_cast<unsigned>(txn.phase));
    txn.phase = *next;
    memReqQ.push_back(dat.txnid);
    DPRINTF(Chi2ClassicMemBridge,
            "accept NonCopyBackWriteData txn=%u dbid=%u bytes=%u\n",
            dat.txnid, dat.dbid, static_cast<unsigned>(dat.data.size()));
}

void
Chi2ClassicMemBridge::drainChiDat()
{
    while (auto flit = chiPort.getTxFlit(DAT)) {
        panic_if(!std::holds_alternative<RawDat>(*flit),
                 "%s sampled non-DAT flit from DAT channel\n", name());
        acceptWriteData(std::get<RawDat>(std::move(*flit)));
    }
}

bool
Chi2ClassicMemBridge::recvMemResp(PacketPtr pkt)
{
    auto* state = pkt->findNextSenderState<SnfSenderState>();
    panic_if(!state, "%s got classic response without SNF sender state\n",
             name());

    auto it = txns.find(state->txnid);
    panic_if(it == txns.end(),
             "%s got classic response for unknown txnid=%u\n",
             name(), state->txnid);

    TxnEntry& txn = it->second;
    panic_if(pkt != txn.pkt,
             "%s got response packet mismatch txnid=%u\n",
             name(), state->txnid);
    panic_if(!pkt->isResponse(),
             "%s got non-response classic packet cmd=%s txnid=%u\n",
             name(), pkt->cmdString().c_str(), state->txnid);

    const auto next = Chi2ClassicMemTxnPolicy::transition(
        txn.phase,
        Chi2ClassicMemTxnPolicy::Event::ClassicResponseReceived);
    panic_if(!next,
             "%s got classic response in invalid phase=%u txnid=%u\n",
             name(), static_cast<unsigned>(txn.phase), state->txnid);

    if (txn.kind == TxnEntry::Kind::Write) {
        if (Chi2ClassicMemTxnPolicy::isHnfDirtyVictimWriteback(
                txn.req, blockSize)) {
            DPRINTF(HnfDirtyVictimE2E,
                    "SN_DV_COMP txn=%u dbid=%u error=%u\n",
                    state->txnid, txn.dbid, pkt->isError() ? 1 : 0);
        }
        DPRINTF(Chi2ClassicMemBridge,
                "classic WriteResp received txn=%u addr=%#llx cmd=%s\n",
                state->txnid,
                static_cast<unsigned long long>(pkt->getAddr()),
                pkt->cmdString().c_str());
        txRspQ.push_back(Chi2ClassicMemTxnPolicy::makeComp(
            txn.req, txn.dbid, nodeId, hnfNodeId, pkt->isError()));
        txn.phase = *next;

        Packet::SenderState* popped = pkt->popSenderState();
        delete popped;
        delete pkt;
        txn.pkt = nullptr;
        schedulePump();
        return true;
    }

    const uint8_t* src = pkt->hasData() ? pkt->getConstPtr<uint8_t>() : nullptr;
    std::vector<uint8_t> zeros;
    if (!src) {
        zeros.assign(txn.expectedBytes, 0);
        src = zeros.data();
    }

    DPRINTF(Chi2ClassicMemBridge,
            "classic ReadResp received txn=%u addr=%#llx bytes=%u cmd=%s\n",
            state->txnid, static_cast<unsigned long long>(pkt->getAddr()),
            pkt->getSize(), pkt->cmdString().c_str());
    queueCompData(txn, src, std::min<uint32_t>(txn.expectedBytes,
                                               pkt->getSize()));
    txn.phase = *next;

    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;
    txn.pkt = nullptr;
    schedulePump();
    return true;
}

void
Chi2ClassicMemBridge::queueCompData(const TxnEntry& txn,
                                    const uint8_t* data,
                                    uint32_t data_bytes)
{
    const uint32_t bytes = txn.expectedBytes;
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        RawDat dat{};
        dat.qos = txn.req.qos;
        dat.srcid = nodeId ? nodeId : txn.req.tgtid;
        dat.tgtid = hnfNodeId ? hnfNodeId : txn.req.srcid;
        dat.txnid = txn.req.txnid;
        dat.opcode = Chi2ClassicMemTxnPolicy::CompDataOpcode;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = dat.tgtid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = Chi2ClassicMemTxnPolicy::RespSC;
        dat.beatOffset = offset;
        dat.data.assign(beatBytes, 0);
        if (offset < data_bytes) {
            const uint32_t copyBytes =
                std::min<uint32_t>(beatBytes, data_bytes - offset);
            std::memcpy(dat.data.data(), data + offset, copyBytes);
        }
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        txDatQ.push_back(std::move(dat));
    }

    DPRINTF(Chi2ClassicMemBridge,
            "queue CompData txn=%u beats=%u bytes=%u sn=%u hnf=%u\n",
            txn.req.txnid, (bytes + dataBeatBytes - 1) / dataBeatBytes,
            bytes, nodeId, hnfNodeId);
}

void
Chi2ClassicMemBridge::sendPendingDat()
{
    while (!txDatQ.empty()) {
        RawDat dat = txDatQ.front();
        auto txn = txns.end();
        if (dat.last) {
            txn = txns.find(dat.txnid);
            panic_if(txn == txns.end() ||
                         txn->second.kind != TxnEntry::Kind::Read,
                     "%s completed unknown/non-read txnid=%u\n",
                     name(), dat.txnid);
        }

        const bool accepted = chiPort.enqueueRx(DAT, dat);
        std::optional<TxnEntry::Phase> next;
        if (dat.last) {
            next = Chi2ClassicMemTxnPolicy::transition(
                txn->second.phase,
                Chi2ClassicMemTxnPolicy::Event::ChiResponseSent,
                accepted);
            panic_if(!next,
                     "%s completed out-of-phase read txnid=%u phase=%u\n",
                     name(), dat.txnid,
                     static_cast<unsigned>(txn->second.phase));
        }
        if (!accepted) {
            DPRINTF(Chi2ClassicMemBridge,
                    "CompData blocked txn=%u dataid=%u bytes=%u\n",
                    dat.txnid, dat.dataid,
                    static_cast<unsigned>(dat.data.size()));
            return;
        }

        DPRINTF(Chi2ClassicMemBridge,
                "send CompData txn=%u dataid=%u bytes=%u last=%u "
                "src=%u tgt=%u\n",
                dat.txnid, dat.dataid,
                static_cast<unsigned>(dat.data.size()), dat.last,
                dat.srcid, dat.tgtid);
        txDatQ.pop_front();
        if (dat.last) {
            panic_if(*next != TxnEntry::Phase::Complete,
                     "%s retained accepted read response txnid=%u\n",
                     name(), dat.txnid);
            txns.erase(txn);
        }
    }
}

void
Chi2ClassicMemBridge::sendPendingRsp()
{
    while (!txRspQ.empty()) {
        const RawRsp& rsp = txRspQ.front();
        auto txn = txns.find(rsp.txnid);
        panic_if(txn == txns.end() ||
                     txn->second.kind != TxnEntry::Kind::Write,
                 "%s completed unknown/non-write txnid=%u\n",
                 name(), rsp.txnid);

        const bool accepted = chiPort.enqueueRx(RSP, rsp);
        const auto next = Chi2ClassicMemTxnPolicy::transition(
            txn->second.phase,
            Chi2ClassicMemTxnPolicy::Event::ChiResponseSent,
            accepted);
        panic_if(!next,
                 "%s completed out-of-phase write txnid=%u phase=%u\n",
                 name(), rsp.txnid,
                 static_cast<unsigned>(txn->second.phase));
        if (!accepted) {
            DPRINTF(Chi2ClassicMemBridge,
                    "Comp blocked txn=%u dbid=%u\n", rsp.txnid, rsp.dbid);
            return;
        }

        DPRINTF(Chi2ClassicMemBridge,
                "send Comp txn=%u dbid=%u src=%u tgt=%u\n",
                rsp.txnid, rsp.dbid, rsp.srcid, rsp.tgtid);
        panic_if(*next != TxnEntry::Phase::Complete,
                 "%s retained accepted write response txnid=%u\n",
                 name(), rsp.txnid);
        txns.erase(txn);
        txRspQ.pop_front();
    }
}

void
Chi2ClassicMemBridge::pump()
{
    sendPendingRsp();
    sendPendingDat();
    sendPendingMemReq();
    drainChiDat();
    drainChiReq();
    sendPendingMemReq();
    sendPendingRsp();
    sendPendingDat();

    if (hasPumpWork()) {
        schedulePump();
    }
}

} // namespace Chi
} // namespace gem5

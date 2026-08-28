#include "mem/cache/CHI/Chi2ClassicMemBridge.hh"

#include <algorithm>
#include <cstring>
#include <memory>
#include <type_traits>
#include <utility>
#include <vector>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/Chi2ClassicMemBridge.hh"
#include "debug/HnfDirtyVictimE2E.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/packet.hh"
#include "sim/serialize.hh"
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
    pkt->pushSenderState(
        new SnfSenderState(Chi2ClassicTxnKey::fromReq(req)));
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
    pkt->pushSenderState(
        new SnfSenderState(Chi2ClassicTxnKey::fromReq(txn.req)));
    return pkt;
}

void
Chi2ClassicMemBridge::sendPendingMemReq()
{
    if (memReqQ.empty() || memReqBlocked) {
        return;
    }

    const Chi2ClassicTxnKey key = memReqQ.front();
    auto it = txns.find(key);
    panic_if(it == txns.end(),
             "%s has classic request for unknown src=%u txnid=%u\n",
             name(), key.srcId, key.txnId);
    TxnEntry& txn = it->second;
    panic_if(txn.phase != TxnEntry::Phase::MemReqQueued || !txn.pkt,
             "%s has invalid queued classic request src=%u txnid=%u "
             "phase=%u\n", name(), key.srcId, key.txnId,
             static_cast<unsigned>(txn.phase));
    panic_if(blockedPkt && blockedPkt != txn.pkt,
             "%s changed classic packet while retry was pending\n", name());

    PacketPtr pkt = txn.pkt;
    const bool accepted = memPort.sendTimingReq(pkt);
    const auto next = Chi2ClassicMemTxnPolicy::transition(
        txn.phase, Chi2ClassicMemTxnPolicy::Event::ClassicRequestSent,
        accepted);
    panic_if(!next,
             "%s has illegal classic-request transition src=%u txnid=%u "
             "phase=%u\n", name(), key.srcId, key.txnId,
             static_cast<unsigned>(txn.phase));
    if (!accepted) {
        blockedPkt = pkt;
        memReqBlocked = true;
        DPRINTF(Chi2ClassicMemBridge,
                "classic %s blocked src=%u txn=%u addr=%#llx size=%u\n",
                pkt->cmdString().c_str(), key.srcId, key.txnId,
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
            "classic %s sent src=%u txn=%u addr=%#llx size=%u\n",
            pkt->cmdString().c_str(), key.srcId, key.txnId,
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

    const Chi2ClassicTxnKey key = Chi2ClassicTxnKey::fromReq(req);
    panic_if(txns.count(key),
             "%s duplicate ReadNoSnp txnid=%u src=%u\n",
             name(), req.txnid, req.srcid);

    TxnEntry entry{};
    entry.req = req;
    entry.kind = TxnEntry::Kind::Read;
    entry.phase = TxnEntry::Phase::MemReqQueued;
    entry.expectedBytes = expectedDataBytes(req);
    entry.pkt = makeReadPacket(req);
    const bool inserted = txns.emplace(key, std::move(entry)).second;
    panic_if(!inserted, "%s failed to insert ReadNoSnp txnid=%u\n",
             name(), req.txnid);

    DPRINTF(Chi2ClassicMemBridge,
            "accept ReadNoSnp src=%u tgt=%u txn=%u addr=%#llx bytes=%u "
            "to classic\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr), entry.expectedBytes);
    memReqQ.push_back(key);
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
    const Chi2ClassicTxnKey key = Chi2ClassicTxnKey::fromReq(req);
    panic_if(txns.count(key),
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
    auto [it, inserted] = txns.emplace(key, std::move(entry));
    panic_if(!inserted, "%s failed to insert WriteNoSnpFull txnid=%u\n",
             name(), req.txnid);

    const RawRsp rsp = Chi2ClassicMemTxnPolicy::makeDbidResp(
        it->second.req, it->second.dbid, nodeId,
        Chi2ClassicTxnKey::responseTarget(it->second.req, hnfNodeId));
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
    const Chi2ClassicTxnKey key = Chi2ClassicTxnKey::fromWriteData(dat);
    auto it = txns.find(key);
    panic_if(it == txns.end(),
             "%s got write DAT for unknown src=%u txnid=%u dbid=%u\n",
             name(), dat.srcid, dat.txnid, dat.dbid);
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
    memReqQ.push_back(key);
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

    auto it = txns.find(state->key);
    panic_if(it == txns.end(),
             "%s got classic response for unknown src=%u txnid=%u\n",
             name(), state->key.srcId, state->key.txnId);

    TxnEntry& txn = it->second;
    panic_if(pkt != txn.pkt,
             "%s got response packet mismatch src=%u txnid=%u\n",
             name(), state->key.srcId, state->key.txnId);
    panic_if(!pkt->isResponse(),
             "%s got non-response classic packet cmd=%s src=%u txnid=%u\n",
             name(), pkt->cmdString().c_str(), state->key.srcId,
             state->key.txnId);

    const auto next = Chi2ClassicMemTxnPolicy::transition(
        txn.phase,
        Chi2ClassicMemTxnPolicy::Event::ClassicResponseReceived);
    panic_if(!next,
             "%s got classic response in invalid phase=%u src=%u "
             "txnid=%u\n", name(), static_cast<unsigned>(txn.phase),
             state->key.srcId, state->key.txnId);

    if (txn.kind == TxnEntry::Kind::Write) {
        if (Chi2ClassicMemTxnPolicy::isHnfDirtyVictimWriteback(
                txn.req, blockSize)) {
            DPRINTF(HnfDirtyVictimE2E,
                    "SN_DV_COMP txn=%u dbid=%u error=%u\n",
                    state->key.txnId, txn.dbid, pkt->isError() ? 1 : 0);
        }
        DPRINTF(Chi2ClassicMemBridge,
                "classic WriteResp received txn=%u addr=%#llx cmd=%s\n",
                state->key.txnId,
                static_cast<unsigned long long>(pkt->getAddr()),
                pkt->cmdString().c_str());
        txRspQ.push_back(
            {state->key,
             Chi2ClassicMemTxnPolicy::makeComp(
                 txn.req, txn.dbid, nodeId,
                 Chi2ClassicTxnKey::responseTarget(txn.req, hnfNodeId),
                 pkt->isError())});
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
            state->key.txnId,
            static_cast<unsigned long long>(pkt->getAddr()),
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
    const uint32_t responseTarget =
        Chi2ClassicTxnKey::responseTarget(txn.req, hnfNodeId);
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        RawDat dat{};
        dat.qos = txn.req.qos;
        dat.srcid = nodeId ? nodeId : txn.req.tgtid;
        dat.tgtid = responseTarget;
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
        txDatQ.push_back(
            {Chi2ClassicTxnKey::fromReq(txn.req), std::move(dat)});
    }

    DPRINTF(Chi2ClassicMemBridge,
            "queue CompData txn=%u beats=%u bytes=%u sn=%u hnf=%u\n",
            txn.req.txnid, (bytes + dataBeatBytes - 1) / dataBeatBytes,
            bytes, nodeId, responseTarget);
}

void
Chi2ClassicMemBridge::sendPendingDat()
{
    while (!txDatQ.empty()) {
        const PendingDat& pending = txDatQ.front();
        const RawDat& dat = pending.flit;
        auto txn = txns.end();
        if (dat.last) {
            txn = txns.find(pending.key);
            panic_if(txn == txns.end() ||
                         txn->second.kind != TxnEntry::Kind::Read,
                     "%s completed unknown/non-read src=%u txnid=%u\n",
                     name(), pending.key.srcId, pending.key.txnId);
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
        const PendingRsp& pending = txRspQ.front();
        const RawRsp& rsp = pending.flit;
        auto txn = txns.find(pending.key);
        panic_if(txn == txns.end() ||
                     txn->second.kind != TxnEntry::Kind::Write,
                 "%s completed unknown/non-write src=%u txnid=%u\n",
                 name(), pending.key.srcId, pending.key.txnId);

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

void
Chi2ClassicMemBridge::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "chi2classicmem");

    // TxnID-keyed outstanding SN transactions (TxnID namespace).
    paramOut(cp, "nextDbid", static_cast<uint32_t>(nextDbid));
    paramOut(cp, "memReqBlocked", static_cast<uint32_t>(memReqBlocked));
    paramOut(cp, "txnsSize", txns.size());
    std::vector<uint32_t> txn_src, txn_id, txn_kind, txn_phase, txn_exp,
        txn_dbid;
    for (const auto &kv : txns) {
        txn_src.push_back(kv.first.srcId);
        txn_id.push_back(kv.first.txnId);
        txn_kind.push_back(static_cast<uint32_t>(kv.second.kind));
        txn_phase.push_back(static_cast<uint32_t>(kv.second.phase));
        txn_exp.push_back(kv.second.expectedBytes);
        txn_dbid.push_back(static_cast<uint32_t>(kv.second.dbid));
    }
    arrayParamOut(cp, "txnSrcId", txn_src);
    arrayParamOut(cp, "txnTxnId", txn_id);
    arrayParamOut(cp, "txnKind", txn_kind);
    arrayParamOut(cp, "txnPhase", txn_phase);
    arrayParamOut(cp, "txnExpectedBytes", txn_exp);
    arrayParamOut(cp, "txnDbid", txn_dbid);

    // Classic-memory request queue (queue + TxnID keys).
    paramOut(cp, "memReqQSize", memReqQ.size());
    std::vector<uint32_t> mrq_src, mrq_txn;
    for (const auto &k : memReqQ) {
        mrq_src.push_back(k.srcId);
        mrq_txn.push_back(k.txnId);
    }
    arrayParamOut(cp, "memReqQSrcId", mrq_src);
    arrayParamOut(cp, "memReqQTxnId", mrq_txn);

    // Response/DAT queue occupancy is serialized as a drain invariant. Flit
    // contents have no stable representation, so the global checkpoint must
    // empty these queues before serialization.
    paramOut(cp, "txDatQSize", txDatQ.size());
    paramOut(cp, "txRspQSize", txRspQ.size());
    paramOut(cp, "pendingReqValid",
             static_cast<uint32_t>(pendingReq.has_value()));
    paramOut(cp, "blockedPktValid",
             static_cast<uint32_t>(blockedPkt != nullptr));
}

void
Chi2ClassicMemBridge::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "chi2classicmem");

    uint32_t next_dbid = 1, mem_req_blocked = 0;
    paramIn(cp, "nextDbid", next_dbid);
    paramIn(cp, "memReqBlocked", mem_req_blocked);
    nextDbid = static_cast<uint8_t>(next_dbid);
    memReqBlocked = mem_req_blocked;

    txns.clear();
    uint32_t txn_size = 0;
    paramIn(cp, "txnsSize", txn_size);
    std::vector<uint32_t> txn_src, txn_id, txn_kind, txn_phase, txn_exp,
        txn_dbid;
    arrayParamIn(cp, "txnSrcId", txn_src);
    arrayParamIn(cp, "txnTxnId", txn_id);
    arrayParamIn(cp, "txnKind", txn_kind);
    arrayParamIn(cp, "txnPhase", txn_phase);
    arrayParamIn(cp, "txnExpectedBytes", txn_exp);
    arrayParamIn(cp, "txnDbid", txn_dbid);
    for (uint32_t i = 0; i < txn_size && i < txn_src.size(); ++i) {
        Chi2ClassicTxnKey key{txn_src[i], txn_id[i]};
        TxnEntry e;
        e.kind = static_cast<TxnEntry::Kind>(txn_kind[i]);
        e.phase = static_cast<TxnEntry::Phase>(txn_phase[i]);
        e.expectedBytes = txn_exp[i];
        e.dbid = static_cast<uint8_t>(txn_dbid[i]);
        e.pkt = nullptr;
        txns.emplace(key, std::move(e));
    }

    memReqQ.clear();
    uint32_t mrq_size = 0;
    paramIn(cp, "memReqQSize", mrq_size);
    std::vector<uint32_t> mrq_src, mrq_txn;
    arrayParamIn(cp, "memReqQSrcId", mrq_src);
    arrayParamIn(cp, "memReqQTxnId", mrq_txn);
    for (uint32_t i = 0; i < mrq_size && i < mrq_src.size(); ++i) {
        memReqQ.push_back(Chi2ClassicTxnKey{mrq_src[i], mrq_txn[i]});
    }

    uint64_t txdat_sz = 0, txrsp_sz = 0;
    paramIn(cp, "txDatQSize", txdat_sz);
    paramIn(cp, "txRspQSize", txrsp_sz);
    // Flit-bearing queues must be empty in a valid drained checkpoint.

    uint32_t pending_req_valid = 0, blocked_pkt_valid = 0;
    paramIn(cp, "pendingReqValid", pending_req_valid);
    paramIn(cp, "blockedPktValid", blocked_pkt_valid);
    if (!pending_req_valid) pendingReq.reset();
    blockedPkt = nullptr;

    fatal_if(txn_size != 0 || mrq_size != 0 || txdat_sz != 0 ||
                 txrsp_sz != 0 || pending_req_valid != 0 ||
                 blocked_pkt_valid != 0 || mem_req_blocked != 0,
             "%s cannot restore non-drained CHI SN state: "
             "txns=%u memReqQ=%u txDat=%llu txRsp=%llu pendingReq=%u "
             "blockedPkt=%u memReqBlocked=%u\n",
             name(), txn_size, mrq_size,
             static_cast<unsigned long long>(txdat_sz),
             static_cast<unsigned long long>(txrsp_sz), pending_req_valid,
             blocked_pkt_valid, mem_req_blocked);
}

} // namespace Chi
} // namespace gem5

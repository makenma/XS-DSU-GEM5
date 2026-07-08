#include "mem/cache/CHI/Cache2ChiBridge.hh"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <type_traits>
#include <utility>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/Cache2ChiBridge.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"
#include "mem/cache/CHI/base/SnoopOpcode.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/eventq.hh"
#include "sim/sim_object.hh"

namespace gem5
{

namespace Chi
{

namespace
{

namespace ReqOp
{
constexpr uint8_t ReadShared = 0x01;
constexpr uint8_t ReadUnique = 0x07;
constexpr uint8_t CleanInvalid = 0x09;
constexpr uint8_t MakeInvalid = 0x0a;
constexpr uint8_t MakeUnique = 0x0c;
constexpr uint8_t Evict = 0x0d;
constexpr uint8_t WriteEvictFull = 0x55;
constexpr uint8_t WriteCleanFull = 0x57;
constexpr uint8_t WriteUniquePtl = 0x58;
constexpr uint8_t WriteUniqueFull = 0x59;
constexpr uint8_t WriteBackFull = 0x5b;
} // namespace ReqOp

namespace RspOp
{
constexpr uint8_t SnpResp = 0x01;
constexpr uint8_t CompAck = 0x02;
} // namespace RspOp

namespace DatOp
{
constexpr uint8_t SnpRespData = 0x01;
constexpr uint8_t CopyBackWriteData = 0x02;
constexpr uint8_t NonCopyBackWriteData = 0x03;
constexpr uint8_t CompData = 0x04;
} // namespace DatOp

bool
isDirtyResp(uint8_t resp)
{
    return resp == 3 || resp == 4;
}

bool
isReadDataOpcode(uint8_t opcode)
{
    const auto decoded = decodeDat(opcode);
    return decoded.minor == DatMinor::CompData ||
           decoded.minor == DatMinor::DataSepResp ||
           decoded.minor == DatMinor::NCBWrDataCompAck;
}

} // anonymous namespace

Cache2ChiBridge::Cache2ChiBridge(const Cache2ChiBridgeParams& p)
    : ClockedObject(p),
      ruby::Consumer(this),
      cachePort(csprintf("%s.cache_side", name()), *this),
      chiPort(csprintf("%s.chi_side", name()), static_cast<ruby::Consumer*>(this),
              /*id*/ 0),
      memPort(p.name + ".mem_side", this),
      nodeId(p.node_id),
      homeNodeId(p.home_node_id),
      maxTxns(p.num_txns),
      blockSize(p.block_size),
      dataBeatBytes(p.data_beat_bytes),
      enableRetry(p.enable_retry),
      sinkHnfTxReq(p.sink_hnf_txreq),
      pumpEvent([this]{ pump(); }, name() + ".pumpEvent")
{
    fatal_if(blockSize == 0 || !isPowerOf2(blockSize),
             "%s requires a non-zero power-of-two block_size\n", name());
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "%s requires 0 < data_beat_bytes <= block_size\n", name());
    fatal_if(maxTxns == 0, "%s requires num_txns > 0\n", name());
}

Cache2ChiBridge::CacheSidePort::CacheSidePort(
    const std::string& pname, Cache2ChiBridge& owner)
    : ResponsePort(pname, &owner), owner(owner)
{
    DPRINTF(Cache2ChiBridge, "Cache2ChiBridge constructed\n");
}

Cache2ChiBridge::MemSidePort::MemSidePort(const std::string& name,
                                          Cache2ChiBridge* owner)
    : RequestPort(name, owner), owner(owner)
{
}

void
Cache2ChiBridge::wakeup()
{
    schedulePump();
}

void
Cache2ChiBridge::print(std::ostream& out) const
{
    out << "Cache2ChiBridge(" << name() << ")";
}

Port&
Cache2ChiBridge::getPort(const std::string& if_name, PortID idx)
{
    DPRINTF(Cache2ChiBridge, "getPort(%s)\n", if_name);
    if (if_name == "cache_side") {
        return cachePort;
    }
    if (if_name == "chi_side") {
        return chiPort;
    }
    if (if_name == "mem_side") {
        return memPort;
    }

    return ClockedObject::getPort(if_name, idx);
}

void
Cache2ChiBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        schedule(pumpEvent, nextCycle());
    }
}

bool
Cache2ChiBridge::hasPumpWork() const
{
    return !pendingReqPkts.empty() || !pendingRespPkts.empty() ||
           !retryTxnIds.empty();
}

std::optional<uint32_t>
Cache2ChiBridge::allocateTxnId()
{
    for (uint32_t i = 0; i < maxTxns; ++i) {
        uint32_t id = nextTxnId;
        nextTxnId = (nextTxnId % maxTxns) + 1;
        if (!txns.count(id)) {
            return id;
        }
    }
    return std::nullopt;
}

void
Cache2ChiBridge::freeTxn(uint32_t txnid)
{
    txns.erase(txnid);
}

uint32_t
Cache2ChiBridge::allocateSnoopTxnId()
{
    const uint32_t id = nextSnoopTxnId++;
    if (nextSnoopTxnId == 0) {
        nextSnoopTxnId = 1;
    }
    return id;
}

Cache2ChiBridge::MemoryIntent
Cache2ChiBridge::classify(PacketPtr pkt) const
{
    const MemCmd cmd = pkt->cmd;
    MemoryIntent intent{};
    intent.needsResponse = pkt->needsResponse();

    if (cmd == MemCmd::SCUpgradeFailReq) {
        panic("%s: SCUpgradeFailReq is a classic internal failure path and "
              "must not be translated into CHI\n", name());
    }
    if (cmd == MemCmd::LockedRMWReadReq ||
        cmd == MemCmd::LockedRMWWriteReq) {
        panic("%s: LockedRMW* requires CHI Atomic/exclusive locking support; "
              "v1 refuses to map it to ReadUnique\n", name());
    }

    if (cmd == MemCmd::ReadReq || cmd == MemCmd::ReadSharedReq) {
        intent.kind = IntentKind::ReadShared;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        return intent;
    }

    if (cmd == MemCmd::ReadCleanReq) {
        intent.kind = IntentKind::ReadShared;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        intent.forceCleanResponse = true;
        return intent;
    }

    if (cmd == MemCmd::ReadExReq) {
        intent.kind = IntentKind::ReadUnique;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        return intent;
    }

    if (cmd == MemCmd::UpgradeReq || cmd == MemCmd::SCUpgradeReq) {
        intent.kind = IntentKind::MakeUnique;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::InvalidateReq) {
        intent.kind = IntentKind::MakeInvalid;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::CleanInvalidReq) {
        intent.kind = IntentKind::CleanInvalid;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::WriteReq || cmd == MemCmd::WriteLineReq) {
        intent.kind = pkt->isWholeLineWrite(blockSize) ?
            IntentKind::WriteUniqueFull : IntentKind::WriteUniquePtl;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        intent.isPartial = intent.kind == IntentKind::WriteUniquePtl;
        return intent;
    }

    if (cmd == MemCmd::WritebackDirty) {
        intent.kind = IntentKind::WriteBackFull;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        return intent;
    }

    if (cmd == MemCmd::WritebackClean) {
        intent.kind = pkt->hasSharers() ?
            IntentKind::Evict : IntentKind::WriteEvictFull;
        intent.txnClass = pkt->hasSharers() ? TxnClass::Evict : TxnClass::Write;
        intent.expectsComp = true;
        intent.expectsDbid = !pkt->hasSharers();
        intent.carriesData = !pkt->hasSharers();
        return intent;
    }

    if (cmd == MemCmd::WriteClean) {
        intent.kind = IntentKind::WriteCleanFull;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        return intent;
    }

    if (cmd == MemCmd::CleanEvict) {
        intent.kind = IntentKind::Evict;
        intent.txnClass = TxnClass::Evict;
        intent.expectsComp = true;
        return intent;
    }

    panic("%s: unsupported classic packet command %s for conservative CHI "
          "translation\n", name(), pkt->cmdString().c_str());
}

RawReq
Cache2ChiBridge::mapToReq(const MemoryIntent& intent, PacketPtr pkt,
                          uint32_t txnid) const
{
    RawReq req{};
    req.addr = pkt->getBlockAddr(blockSize);
    req.size = static_cast<uint8_t>(pkt->getSize());
    req.qos = pkt->qosValue();
    req.srcid = nodeId;
    req.tgtid = homeNodeId;
    req.txnid = txnid;
    req.stage = 0;
    req.AllowRetry = enableRetry ? 1 : 0;
    req.ReturnNid = nodeId;

    switch (intent.kind) {
      case IntentKind::ReadShared:
      case IntentKind::ReadSharedForceClean:
        req.opcode = ReqOp::ReadShared;
        break;
      case IntentKind::ReadUnique:
        req.opcode = ReqOp::ReadUnique;
        break;
      case IntentKind::MakeUnique:
        req.opcode = ReqOp::MakeUnique;
        break;
      case IntentKind::MakeInvalid:
        req.opcode = ReqOp::MakeInvalid;
        break;
      case IntentKind::CleanInvalid:
        req.opcode = ReqOp::CleanInvalid;
        break;
      case IntentKind::WriteUniqueFull:
        req.opcode = ReqOp::WriteUniqueFull;
        break;
      case IntentKind::WriteUniquePtl:
        req.opcode = ReqOp::WriteUniquePtl;
        req.addr = pkt->getAddr();
        break;
      case IntentKind::WriteBackFull:
        req.opcode = ReqOp::WriteBackFull;
        break;
      case IntentKind::WriteCleanFull:
        req.opcode = ReqOp::WriteCleanFull;
        break;
      case IntentKind::WriteEvictFull:
        req.opcode = ReqOp::WriteEvictFull;
        break;
      case IntentKind::Evict:
        req.opcode = ReqOp::Evict;
        break;
    }

    return req;
}

std::vector<RawDat>
Cache2ChiBridge::packDataBeats(const MemoryIntent& intent, PacketPtr pkt,
                               uint32_t txnid) const
{
    std::vector<RawDat> beats;
    if (!intent.carriesData) {
        return beats;
    }

    panic_if(!pkt->hasData(), "%s: %s carries CHI data but Packet has no data\n",
             name(), pkt->cmdString().c_str());

    const uint8_t* src = pkt->getConstPtr<uint8_t>();
    const uint32_t pktSize = pkt->getSize();
    const uint32_t startOffset = pkt->getOffset(blockSize);
    const uint32_t opcode = intent.txnClass == TxnClass::Write &&
        (intent.kind == IntentKind::WriteUniqueFull ||
         intent.kind == IntentKind::WriteUniquePtl) ?
        DatOp::NonCopyBackWriteData : DatOp::CopyBackWriteData;

    for (uint32_t copied = 0, dataid = 0; copied < pktSize;
         copied += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, pktSize - copied);
        RawDat dat{};
        dat.qos = pkt->qosValue();
        dat.srcid = nodeId;
        dat.tgtid = homeNodeId;
        dat.txnid = txnid;
        dat.opcode = static_cast<uint8_t>(opcode);
        dat.stage = 0;
        dat.last = (copied + beatBytes) == pktSize;
        dat.HomeNID = homeNodeId;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = static_cast<uint8_t>(RespState::Unknown);
        dat.beatOffset = intent.isPartial ? startOffset + copied : copied;
        dat.data.assign(src + copied, src + copied + beatBytes);
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        beats.push_back(std::move(dat));
    }

    return beats;
}

bool
Cache2ChiBridge::cacheRecvTimingReq(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge,
        "cacheRecvTimingReq: cmd=%s addr=0x%lx size=%u\n",
        pkt->cmdString().c_str(),
        static_cast<unsigned long>(pkt->getAddr()),
        pkt->getSize());
    pendingReqPkts.push(pkt);
    schedulePump();
    return true;
}

Tick
Cache2ChiBridge::cacheRecvAtomic(PacketPtr pkt)
{
    panic("%s: atomic access is not supported by Cache2ChiBridge RNF v1\n",
          name());
}

void
Cache2ChiBridge::cacheRecvFunctional(PacketPtr pkt)
{
    memPort.sendFunctional(pkt);
}

bool
Cache2ChiBridge::cacheRecvTimingSnoopResp(PacketPtr pkt)
{
    auto* state = pkt->findNextSenderState<SnoopSenderState>();
    panic_if(!state, "%s: got classic snoop response without bridge state\n",
             name());

    auto it = snoops.find(state->txnid);
    panic_if(it == snoops.end(),
             "%s: got classic snoop response for unknown snoop txn %u\n",
             name(), state->txnid);

    SnoopEntry& snoop = it->second;
    sendSnoopData(snoop, pkt);

    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;

    if (snoop.snoopPkt) {
        delete snoop.snoopPkt;
    }
    snoops.erase(it);
    schedulePump();
    return true;
}

void
Cache2ChiBridge::cacheRecvRespRetry()
{
    schedulePump();
}

void
Cache2ChiBridge::pump()
{
    drainChiTx();
    sendPendingResponses();

    while (!retryTxnIds.empty()) {
        const uint32_t txnid = retryTxnIds.front();
        if (!reissueRetriedTxn(txnid)) {
            break;
        }
        retryTxnIds.pop();
    }

    while (!pendingReqPkts.empty()) {
        PacketPtr pkt = pendingReqPkts.front();
        MemoryIntent intent = classify(pkt);
        auto txnid = allocateTxnId();
        if (!txnid) {
            break;
        }

        TxnEntry txn{};
        txn.txnid = *txnid;
        txn.pkt = pkt;
        txn.intent = intent;
        txn.req = mapToReq(intent, pkt, *txnid);
        txn.dataBeats = packDataBeats(intent, pkt, *txnid);

        DPRINTF(Cache2ChiBridge,
                "enqueue CHI REQ opcode=0x%x txnid=%u cmd=%s addr=0x%lx\n",
                txn.req.opcode, txn.txnid, pkt->cmdString().c_str(),
                static_cast<unsigned long>(txn.req.addr));

        if (!chiPort.enqueueRx(REQ, txn.req)) {
            break;
        }

        pendingReqPkts.pop();
        txns.emplace(txn.txnid, std::move(txn));
    }

    drainChiTx();
    sendPendingResponses();

    if (hasPumpWork()) {
        schedulePump();
    }
}

void
Cache2ChiBridge::drainChiTx()
{
    bool madeProgress = true;
    while (madeProgress) {
        madeProgress = false;
        if (auto flit = chiPort.getTxFlit(REQ)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawReq>) {
                    handleTxReq(f);
                } else {
                    panic("%s: non-REQ flit on REQ channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(RSP)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawRsp>) {
                    handleRsp(f);
                } else {
                    panic("%s: non-RSP flit on RSP channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(DAT)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawDat>) {
                    handleDat(f);
                } else {
                    panic("%s: non-DAT flit on DAT channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(SNP)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawSnp>) {
                    handleSnp(f);
                } else {
                    panic("%s: non-SNP flit on SNP channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
    }
}

void
Cache2ChiBridge::handleTxReq(const RawReq& req)
{
    if (!sinkHnfTxReq) {
        panic("%s: received HNF TXREQ opcode=0x%x while sink_hnf_txreq is "
              "disabled\n",
              name(), req.opcode);
    }

    const auto decoded = decodeReq(req.opcode);
    if (decoded.minor != ReqMinor::ReadNoSnp) {
        warn("%s: direct SN sink dropping unsupported HNF TXREQ opcode=0x%x "
             "src=%u tgt=%u txn=%u addr=%#llx\n",
             name(), req.opcode, req.srcid, req.tgtid, req.txnid,
             static_cast<unsigned long long>(req.addr));
        return;
    }

    DPRINTF(Cache2ChiBridge,
            "direct SN sink accepted HNF TXREQ ReadNoSnp src=%u tgt=%u "
            "txn=%u addr=%#llx size=%u\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr), req.size);

    const uint32_t bytes = req.size ? req.size : blockSize;
    RequestPtr request = std::make_shared<Request>(
        req.addr, bytes, 0, Request::funcRequestorId);
    PacketPtr pkt = new Packet(request, MemCmd::ReadReq);
    pkt->allocate();
    memPort.sendFunctional(pkt);

    const uint8_t* data = pkt->getConstPtr<uint8_t>();
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        RawDat dat{};
        dat.qos = req.qos;
        dat.srcid = req.tgtid;
        dat.tgtid = req.srcid;
        dat.txnid = req.txnid;
        dat.opcode = DatOp::CompData;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = req.ReturnNid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = static_cast<uint8_t>(RespState::SC);
        dat.beatOffset = offset;
        dat.data.assign(data + offset, data + offset + beatBytes);
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);

        panic_if(!chiPort.enqueueRx(DAT, dat),
                 "%s: direct SN sink failed to return CompData txn=%u "
                 "dataid=%u\n",
                 name(), req.txnid, dat.dataid);
        DPRINTF(Cache2ChiBridge,
                "direct SN sink returns CompData txn=%u dataid=%u "
                "offset=%u bytes=%u last=%u\n",
                req.txnid, dat.dataid, offset, beatBytes, dat.last);
    }

    delete pkt;
}

void
Cache2ChiBridge::handleRsp(const RawRsp& rsp)
{
    const auto decoded = decodeRsp(rsp.opcode);
    auto it = txns.find(rsp.txnid);
    panic_if(it == txns.end(), "%s: RSP opcode=0x%x for unknown txnid=%u\n",
             name(), rsp.opcode, rsp.txnid);
    TxnEntry& txn = it->second;

    switch (decoded.minor) {
      case RspMinor::RetryAck:
        txn.retryBlocked = true;
        break;
      case RspMinor::PCrdGrant:
        if (txn.retryBlocked) {
            txn.req.AllowRetry = 0;
            txn.req.pcrdtype = rsp.pcrdtype;
            retryTxnIds.push(txn.txnid);
        }
        break;
      case RspMinor::DBIDResp:
      case RspMinor::DBIDRespOrd:
        txn.hasDbid = true;
        txn.dbid = rsp.dbid;
        sendTxnData(txn);
        break;
      case RspMinor::CompDBIDResp:
        txn.hasDbid = true;
        txn.dbid = rsp.dbid;
        txn.gotComp = true;
        sendTxnData(txn);
        maybeComplete(txn);
        break;
      case RspMinor::Comp:
      case RspMinor::RespSepData:
      case RspMinor::CompPersist:
      case RspMinor::CompCMO:
        if (txn.intent.forceCleanResponse && isDirtyResp(rsp.resp)) {
            panic("%s: ReadCleanReq mapped as ReadShared got dirty CHI "
                  "response state %u\n", name(), rsp.resp);
        }
        txn.gotComp = true;
        maybeComplete(txn);
        break;
      case RspMinor::ReadReceipt:
        break;
      default:
        warn("%s: ignoring unsupported RSP opcode=0x%x txnid=%u\n",
             name(), rsp.opcode, rsp.txnid);
        break;
    }
}

void
Cache2ChiBridge::handleDat(const RawDat& dat)
{
    auto it = txns.find(dat.txnid);
    panic_if(it == txns.end(), "%s: DAT opcode=0x%x for unknown txnid=%u\n",
             name(), dat.opcode, dat.txnid);
    TxnEntry& txn = it->second;

    if (!isReadDataOpcode(dat.opcode)) {
        warn("%s: ignoring unsupported incoming DAT opcode=0x%x txnid=%u\n",
             name(), dat.opcode, dat.txnid);
        return;
    }

    if (txn.intent.forceCleanResponse && isDirtyResp(dat.resp)) {
        panic("%s: ReadCleanReq mapped as ReadShared got dirty CHI data "
              "response state %u\n", name(), dat.resp);
    }

    if (txn.readData.size() < dat.beatOffset + dat.data.size()) {
        txn.readData.resize(dat.beatOffset + dat.data.size(), 0);
    }
    std::copy(dat.data.begin(), dat.data.end(),
              txn.readData.begin() + dat.beatOffset);
    txn.gotData = dat.last || txn.readData.size() >= txn.pkt->getSize();
    txn.gotComp = true;
    DPRINTF(Cache2ChiBridge,
            "handle DAT opcode=0x%x txnid=%u dataid=%u offset=%u "
            "bytes=%u last=%u readBytes=%u/%u\n",
            dat.opcode, dat.txnid, dat.dataid, dat.beatOffset,
            static_cast<unsigned>(dat.data.size()), dat.last,
            static_cast<unsigned>(txn.readData.size()), txn.pkt->getSize());
    maybeComplete(txn);
}

void
Cache2ChiBridge::handleSnp(const RawSnp& snp)
{
    const uint32_t snoopTxn = allocateSnoopTxnId();
    auto req = std::make_shared<Request>(
        snp.addr & ~(static_cast<Addr>(blockSize) - 1),
        blockSize, 0, Request::funcRequestorId);
    PacketPtr pkt = new Packet(req, snoopCmdFor(snp), blockSize, snoopTxn);
    pkt->pushSenderState(new SnoopSenderState(snoopTxn));
    pkt->setExpressSnoop();

    SnoopEntry snoop{};
    snoop.txnid = snoopTxn;
    snoop.snp = snp;
    snoop.snoopPkt = pkt;
    snoop.invalidating = snoopInvalidates(snp);
    snoops.emplace(snoopTxn, snoop);

    cachePort.sendTimingSnoopReq(pkt);

    auto it = snoops.find(snoopTxn);
    if (it == snoops.end()) {
        return;
    }
    SnoopEntry& entry = it->second;
    if (pkt->cacheResponding()) {
        entry.pendingData = true;
        return;
    }

    const RespState state = pkt->hasSharers() && !entry.invalidating ?
        RespState::SC : RespState::I;
    sendSnoopRsp(entry, state);

    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;
    snoops.erase(it);
}

bool
Cache2ChiBridge::sendPendingResponses()
{
    while (!pendingRespPkts.empty()) {
        PacketPtr pkt = pendingRespPkts.front();
        if (!cachePort.sendTimingResp(pkt)) {
            return false;
        }
        pendingRespPkts.pop();
    }
    return true;
}

bool
Cache2ChiBridge::sendCompAck(TxnEntry& txn)
{
    if (txn.sentCompAck || !txn.intent.requiresCompAck) {
        return true;
    }

    RawRsp ack{};
    ack.qos = txn.req.qos;
    ack.srcid = nodeId;
    ack.tgtid = homeNodeId;
    ack.txnid = txn.txnid;
    ack.opcode = RspOp::CompAck;
    ack.stage = 0;
    ack.dbid = 0;
    ack.resp = static_cast<uint8_t>(RespState::Unknown);

    if (!chiPort.enqueueRx(RSP, ack)) {
        return false;
    }
    txn.sentCompAck = true;
    DPRINTF(Cache2ChiBridge, "send CompAck txnid=%u\n", txn.txnid);
    return true;
}

bool
Cache2ChiBridge::sendTxnData(TxnEntry& txn)
{
    if (!txn.dataBeats.empty() && !txn.hasDbid) {
        return false;
    }

    while (txn.nextDataBeat < txn.dataBeats.size()) {
        RawDat dat = txn.dataBeats[txn.nextDataBeat];
        dat.dbid = txn.dbid;
        if (!chiPort.enqueueRx(DAT, dat)) {
            return false;
        }
        DPRINTF(Cache2ChiBridge,
                "send write DAT txnid=%u dbid=%u dataid=%u bytes=%u "
                "last=%u\n",
                txn.txnid, dat.dbid, dat.dataid,
                static_cast<unsigned>(dat.data.size()), dat.last);
        ++txn.nextDataBeat;
    }

    return true;
}

bool
Cache2ChiBridge::reissueRetriedTxn(uint32_t txnid)
{
    auto it = txns.find(txnid);
    if (it == txns.end()) {
        return true;
    }
    TxnEntry& txn = it->second;
    if (!txn.retryBlocked) {
        return true;
    }
    if (!chiPort.enqueueRx(REQ, txn.req)) {
        return false;
    }
    txn.retryBlocked = false;
    txn.hasDbid = false;
    txn.gotComp = false;
    txn.gotData = false;
    txn.sentCompAck = false;
    txn.nextDataBeat = 0;
    txn.readData.clear();
    return true;
}

void
Cache2ChiBridge::maybeComplete(TxnEntry& txn)
{
    if (txn.completed || txn.retryBlocked) {
        return;
    }
    if (txn.intent.expectsData && !txn.gotData) {
        return;
    }
    if (txn.intent.carriesData && txn.nextDataBeat < txn.dataBeats.size()) {
        return;
    }
    if (txn.intent.expectsComp && !txn.gotComp) {
        return;
    }
    if (!sendCompAck(txn)) {
        return;
    }

    completeClassicTxn(txn);
}

void
Cache2ChiBridge::completeClassicTxn(TxnEntry& txn)
{
    txn.completed = true;
    PacketPtr pkt = txn.pkt;

    if (txn.intent.needsResponse) {
        const std::string cmd = pkt->cmdString();
        pkt->makeTimingResponse();
        if (pkt->hasData() && txn.intent.expectsData) {
            panic_if(txn.readData.size() < pkt->getSize(),
                     "%s: completing read txn %u with only %zu/%u bytes\n",
                     name(), txn.txnid, txn.readData.size(), pkt->getSize());
            pkt->setData(txn.readData.data());
        }
        const bool sent = cachePort.sendTimingResp(pkt);
        if (!sent) {
            pendingRespPkts.push(pkt);
        }
        DPRINTF(Cache2ChiBridge,
                "complete classic txnid=%u cmd=%s responded=%u\n",
                txn.txnid, cmd.c_str(), sent);
    } else {
        DPRINTF(Cache2ChiBridge, "complete no-response txnid=%u cmd=%s\n",
                txn.txnid, pkt->cmdString().c_str());
        delete pkt;
    }

    const uint32_t txnid = txn.txnid;
    freeTxn(txnid);
}

void
Cache2ChiBridge::sendSnoopRsp(const SnoopEntry& snoop, RespState state)
{
    RawRsp rsp{};
    rsp.qos = snoop.snp.qos;
    rsp.srcid = nodeId;
    rsp.tgtid = snoop.snp.srcid;
    rsp.txnid = snoop.snp.txnid;
    rsp.opcode = RspOp::SnpResp;
    rsp.stage = 0;
    rsp.dbid = 0;
    rsp.resp = static_cast<uint8_t>(state);

    if (!chiPort.enqueueRx(RSP, rsp)) {
        panic("%s: failed to enqueue SnpResp; retrying snoop responses is "
              "not implemented yet\n", name());
    }
}

void
Cache2ChiBridge::sendSnoopData(SnoopEntry& snoop, PacketPtr pkt)
{
    RawDat dat{};
    dat.qos = snoop.snp.qos;
    dat.srcid = nodeId;
    dat.tgtid = snoop.snp.srcid;
    dat.txnid = snoop.snp.txnid;
    dat.opcode = DatOp::SnpRespData;
    dat.stage = 0;
    dat.last = 1;
    dat.HomeNID = homeNodeId;
    dat.dbid = 0;
    dat.dataid = 0;
    dat.resp = static_cast<uint8_t>(
        snoop.invalidating ? RespState::I : RespState::SC);
    dat.beatOffset = 0;
    dat.data.assign(pkt->getConstPtr<uint8_t>(),
                    pkt->getConstPtr<uint8_t>() + pkt->getSize());
    dat.byteEnable.assign(dat.data.size(), 1);
    dat.chunkValid.assign((dat.data.size() + 7) / 8, 1);

    if (!chiPort.enqueueRx(DAT, dat)) {
        panic("%s: failed to enqueue SnpRespData; retrying snoop data is "
              "not implemented yet\n", name());
    }
}

MemCmd
Cache2ChiBridge::snoopCmdFor(const RawSnp& snp) const
{
    const auto decoded = decodeSnp(snp.opcode);
    switch (decoded.minor) {
      case SnpMinor::SnpShared:
      case SnpMinor::SnpOnce:
        return MemCmd::ReadSharedReq;
      case SnpMinor::SnpClean:
      case SnpMinor::SnpNotSharedDirty:
        return MemCmd::ReadCleanReq;
      case SnpMinor::SnpUnique:
      case SnpMinor::SnpCleanInvalid:
      case SnpMinor::SnpMakeInvalid:
        return MemCmd::ReadExReq;
      default:
        panic("%s: unsupported TXSNP opcode=0x%x in RNF v1\n",
              name(), snp.opcode);
    }
}

bool
Cache2ChiBridge::snoopInvalidates(const RawSnp& snp) const
{
    const auto decoded = decodeSnp(snp.opcode);
    return decoded.minor == SnpMinor::SnpUnique ||
           decoded.minor == SnpMinor::SnpCleanInvalid ||
           decoded.minor == SnpMinor::SnpMakeInvalid;
}

bool
Cache2ChiBridge::memSidePortRecvTimingResp(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got resp from memory side for addr: %#x\n",
            pkt->getAddr());
    if (!cachePort.sendTimingResp(pkt)) {
        pendingRespPkts.push(pkt);
    }
    return true;
}

void
Cache2ChiBridge::memSidePortRecvReqRetry()
{
    DPRINTF(Cache2ChiBridge, "Got req retry from memory side\n");
}

void
Cache2ChiBridge::memSidePortRecvTimingSnoopReq(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    cachePort.sendTimingSnoopReq(pkt);
}

void
Cache2ChiBridge::memSidePortRecvRangeChange()
{
    DPRINTF(Cache2ChiBridge, "Got range change from memory side\n");
}

void
Cache2ChiBridge::memSidePortRecvFunctionalSnoop(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got functional snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    cachePort.sendFunctionalSnoop(pkt);
}

Tick
Cache2ChiBridge::memSidePortRecvAtomicSnoop(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got atomic snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    return cachePort.sendAtomicSnoop(pkt);
}

} // namespace Chi
} // namespace gem5

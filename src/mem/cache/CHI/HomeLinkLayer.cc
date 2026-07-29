#include "mem/cache/CHI/HomeLinkLayer.hh"

#include <algorithm>
#include <ostream>
#include <type_traits>
#include <utility>

#include "mem/cache/CHI/HnfCoherencyController.hh"
#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"

namespace gem5::Chi {

namespace {

namespace RspOp {
constexpr uint8_t RetryAck = 0x03;
constexpr uint8_t Comp = 0x04;
constexpr uint8_t CompDBIDResp = 0x05;
constexpr uint8_t PCrdGrant = 0x07;
} // namespace RspOp

namespace DatOp {
constexpr uint8_t CompData = 0x04;
} // namespace DatOp

constexpr uint8_t RespSC = 1;

const char*
channelName(ChannelType ch)
{
    switch (ch) {
      case ChannelType::REQ: return "REQ";
      case ChannelType::RSP: return "RSP";
      case ChannelType::SNP: return "SNP";
      case ChannelType::DAT: return "DAT";
      default: return "?";
    }
}

void
setVariantStage(FlitVariant& flit, uint32_t stage)
{
    std::visit([stage](auto& f) { f.stage = stage; }, flit);
}

} // anonymous namespace

std::size_t
HomeLinkLayer::TxnKeyHash::operator()(const TxnKey& key) const
{
    return (static_cast<std::size_t>(key.srcid) << 32) ^ key.txnid;
}

uint16_t
HomeLinkLayer::PendingRetryTable::totalForSrc(uint32_t srcid) const
{
    uint16_t total = 0;
    for (const auto& prioRow : cnt) {
        auto srcIt = prioRow.second.find(srcid);
        if (srcIt == prioRow.second.end()) {
            continue;
        }
        for (const auto& pcrd : srcIt->second) {
            total += pcrd.second;
        }
    }
    return total;
}

void
HomeLinkLayer::PendingRetryTable::increment(uint8_t prio, uint32_t srcid,
                                            uint8_t pcrdtype)
{
    ++cnt[prio][srcid][pcrdtype];
}

void
HomeLinkLayer::PendingRetryTable::decrement(uint8_t prio, uint32_t srcid,
                                            uint8_t pcrdtype)
{
    auto prioIt = cnt.find(prio);
    panic_if(prioIt == cnt.end(),
             "HNF pending retry decrement missing prio=%u\n", prio);
    auto srcIt = prioIt->second.find(srcid);
    panic_if(srcIt == prioIt->second.end(),
             "HNF pending retry decrement missing src=%u\n", srcid);
    auto pcrdIt = srcIt->second.find(pcrdtype);
    panic_if(pcrdIt == srcIt->second.end() || pcrdIt->second == 0,
             "HNF pending retry decrement missing pcrdtype=%u\n", pcrdtype);

    --pcrdIt->second;
    if (pcrdIt->second == 0) {
        srcIt->second.erase(pcrdIt);
    }
    if (srcIt->second.empty()) {
        prioIt->second.erase(srcIt);
    }
    if (prioIt->second.empty()) {
        cnt.erase(prioIt);
    }
}

bool
HomeLinkLayer::PendingRetryTable::empty() const
{
    return cnt.empty();
}

HomeLinkLayer::HomeLinkLayer(HomeNodeFull* hnf, uint32_t block_size,
                             uint32_t data_beat_bytes,
                             uint32_t num_poc_entries, bool enable_retry)
    : Consumer(hnf),
      m_homenode(hnf),
      blockSize(block_size),
      dataBeatBytes(data_beat_bytes),
      maxEntries(num_poc_entries),
      retryEnabled(enable_retry),
      tokens(num_poc_entries),
      entries(num_poc_entries)
{
    fatal_if(blockSize == 0, "HomeLinkLayer block_size must be non-zero\n");
    fatal_if(dataBeatBytes == 0,
             "HomeLinkLayer data_beat_bytes must be non-zero\n");
    fatal_if(maxEntries == 0,
             "HomeLinkLayer num_poc_entries must be non-zero\n");

    for (uint32_t i = 0; i < maxEntries; ++i) {
        tokens[i].id = i;
        tokens[i].resourceClass =
            static_cast<uint8_t>(i % static_cast<uint32_t>(LlPriority::Num));
    }
}

void
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "LL wakeup cycle=%llu rxport=%p\n",
            static_cast<unsigned long long>(llCycle), rxport);

    doCcResultAndRetire();
    if (cc) {
        cc->serviceInternalWork(curTick());
        while (cc->hasDeferredRetire()) {
            queueRetire(cc->popDeferredRetire());
        }
    }
    doTxReqArb();
    doTxSnpArb();
    doTxRspArb();
    doTxDatArb();
    doCreditEvents();
    doRetryWakeup();
    doPcrdGrantWakeup();
    doRxPipelineWakeup();
    sampleRxPortsToH0();

    ++llCycle;
    if (hasPendingWork()) {
        scheduleNextCycle();
    }
}

void
HomeLinkLayer::print(std::ostream& out) const
{
    out << "HomeLinkLayer(entries=" << maxEntries
        << ", cycle=" << llCycle << ")";
}

bool
HomeLinkLayer::hasWork() const
{
    return hasPendingWork();
}

void
HomeLinkLayer::doTxReqArb()
{
    if (!rxport || !cc || !cc->hasTxReq()) {
        return;
    }

    HnfCcTxReq pending = cc->frontTxReq();
    pending.req.stage = BasicChiComponent::STAGE_H12;
    if (!rxport->enqueueTx(ChannelType::REQ, pending.req)) {
        DPRINTF(HomeLinkLayer,
                "TXREQ H12 blocked opcode=0x%x src=%u tgt=%u txn=%u "
                "addr=%#llx entry=%u\n",
                pending.req.opcode, pending.req.srcid, pending.req.tgtid,
                pending.req.txnid,
                static_cast<unsigned long long>(pending.req.addr),
                pending.entry);
        return;
    }

    DPRINTF(HomeLinkLayer,
            "TXREQ H12 send opcode=0x%x src=%u tgt=%u txn=%u addr=%#llx "
            "entry=%u\n",
            pending.req.opcode, pending.req.srcid, pending.req.tgtid,
            pending.req.txnid,
            static_cast<unsigned long long>(pending.req.addr),
            pending.entry);
    cc->popTxReq();
    cc->notifyTxReqSent(pending);
}

void
HomeLinkLayer::doTxSnpArb()
{
    if (!rxport || !cc || !cc->hasTxSnp()) {
        return;
    }

    HnfCcTxSnp pending = cc->frontTxSnp();
    pending.snp.stage = BasicChiComponent::STAGE_H12;
    if (!rxport->enqueueTx(ChannelType::SNP, pending.snp)) {
        DPRINTF(HomeLinkLayer,
                "TXSNP H12 blocked opcode=0x%x src=%u tgt=%u txn=%u "
                "addr=%#llx entry=%u targetNode=%u\n",
                pending.snp.opcode, pending.snp.srcid, pending.snp.tgtid,
                pending.snp.txnid,
                static_cast<unsigned long long>(pending.snp.addr),
                pending.entry, pending.targetNode);
        return;
    }

    DPRINTF(HomeLinkLayer,
            "TXSNP H12 send opcode=0x%x src=%u tgt=%u txn=%u addr=%#llx "
            "entry=%u targetNode=%u\n",
            pending.snp.opcode, pending.snp.srcid, pending.snp.tgtid,
            pending.snp.txnid,
            static_cast<unsigned long long>(pending.snp.addr), pending.entry,
            pending.targetNode);
    cc->popTxSnp();
}

void
HomeLinkLayer::doTxRspArb()
{
    if (!rxport) {
        return;
    }

    if (enableTxRspShortPath && sendRspQueue(shortPathFifo)) {
        return;
    }
    if (sendRspQueue(retryAckFifo)) {
        return;
    }
    if (sendRspQueue(pcrdGrantFifo)) {
        return;
    }
    if (cc && cc->hasTxRsp()) {
        HnfCcTxRsp pending = cc->frontTxRsp();
        pending.rsp.stage = BasicChiComponent::STAGE_H3;
        if (!rxport->enqueueTx(ChannelType::RSP, pending.rsp)) {
            DPRINTF(HomeLinkLayer,
                    "TXRSP H3 blocked CC opcode=0x%x src=%u txn=%u "
                    "entry=%u\n",
                    pending.rsp.opcode, pending.rsp.srcid,
                    pending.rsp.txnid, pending.entry);
            return;
        }

        DPRINTF(HomeLinkLayer,
                "TXRSP H3 send CC opcode=0x%x src=%u tgt=%u txn=%u "
                "dbid=%u entry=%u retire=%u\n",
                pending.rsp.opcode, pending.rsp.srcid, pending.rsp.tgtid,
                pending.rsp.txnid, pending.rsp.dbid, pending.entry,
                pending.retire.has_value());
        cc->popTxRsp();
        if (pending.retire) {
            queueRetire(*pending.retire);
        }
        return;
    }
    sendRspQueue(mainPathFifo);
}

void
HomeLinkLayer::doTxDatArb()
{
    if (!rxport) {
        return;
    }

    if (!txDatQ.empty()) {
        TxDatPending pending = txDatQ.front();
        pending.dat.stage = BasicChiComponent::STAGE_H12;
        if (!rxport->enqueueTx(ChannelType::DAT, pending.dat)) {
            DPRINTF(HomeLinkLayer,
                    "TXDAT H12 blocked opcode=0x%x src=%u txn=%u "
                    "dataid=%u\n",
                    pending.dat.opcode, pending.dat.srcid, pending.dat.txnid,
                    pending.dat.dataid);
            return;
        }

        DPRINTF(HomeLinkLayer,
                "TXDAT H12 send opcode=0x%x src=%u tgt=%u txn=%u "
                "dataid=%u bytes=%u last=%u\n",
                pending.dat.opcode, pending.dat.srcid, pending.dat.tgtid,
                pending.dat.txnid, pending.dat.dataid,
                static_cast<unsigned>(pending.dat.data.size()),
                pending.dat.last);
        txDatQ.pop_front();
        return;
    }

    if (!cc || !cc->hasTxDat()) {
        return;
    }

    HnfCcTxDat pending = cc->frontTxDat();
    pending.dat.stage = BasicChiComponent::STAGE_H12;
    if (!rxport->enqueueTx(ChannelType::DAT, pending.dat)) {
        DPRINTF(HomeLinkLayer,
                "TXDAT H12 blocked CC opcode=0x%x src=%u txn=%u "
                "dataid=%u entry=%u\n",
                pending.dat.opcode, pending.dat.srcid, pending.dat.txnid,
                pending.dat.dataid, pending.entry);
        return;
    }

    DPRINTF(HomeLinkLayer,
            "TXDAT H12 send CC opcode=0x%x src=%u tgt=%u txn=%u "
            "dataid=%u bytes=%u last=%u entry=%u\n",
            pending.dat.opcode, pending.dat.srcid, pending.dat.tgtid,
            pending.dat.txnid, pending.dat.dataid,
            static_cast<unsigned>(pending.dat.data.size()), pending.dat.last,
            pending.entry);
    cc->popTxDat();
}

void
HomeLinkLayer::doCreditEvents()
{
    std::deque<CreditEvent> deferred;
    while (!creditEvents.empty()) {
        CreditEvent ev = creditEvents.front();
        creditEvents.pop_front();
        if (ev.dueCycle > llCycle) {
            deferred.push_back(ev);
            continue;
        }
        panic_if(!rxport, "HNF credit event without rxport\n");
        rxport->returnRxCredit(ev.ch, ev.amount);
        DPRINTF(HomeLinkLayer,
                "return RX credit ch=%s amount=%u cycle=%llu\n",
                channelName(ev.ch), ev.amount,
                static_cast<unsigned long long>(llCycle));
    }
    creditEvents.swap(deferred);
}

void
HomeLinkLayer::doCcResultAndRetire()
{
    std::deque<CcAdmitResult> admitDeferred;
    while (!ccAdmitQ.empty()) {
        CcAdmitResult result = ccAdmitQ.front();
        ccAdmitQ.pop_front();
        if (result.dueCycle > llCycle) {
            admitDeferred.push_back(result);
            continue;
        }
        processCcAdmitResult(result);
    }
    ccAdmitQ.swap(admitDeferred);

    std::deque<HnfLinkToCcReq> linkDeferred;
    while (!linkToCcQ.empty()) {
        HnfLinkToCcReq req = linkToCcQ.front();
        linkToCcQ.pop_front();
        if (req.dueCycle > llCycle) {
            linkDeferred.push_back(std::move(req));
            continue;
        }

        panic_if(!cc, "HNF LinkToCcReq without CC seq=%llu\n",
                 static_cast<unsigned long long>(req.seq));

        HnfCcAdmitResult ccResult = cc->acceptLinkReq(req, llCycle);

        CcAdmitResult result{};
        result.valid = ccResult.valid;
        result.seq = ccResult.seq;
        result.tokenId = ccResult.tokenId;
        result.accepted = ccResult.accepted;
        result.dueCycle = llCycle + 1;
        result.req = ccResult.req;
        ccAdmitQ.push_back(result);
        DPRINTF(HomeLinkLayer,
                "CC admit result seq=%llu token=%d accepted=%u due=%llu\n",
                static_cast<unsigned long long>(req.seq), req.tokenId,
                ccResult.accepted,
                static_cast<unsigned long long>(result.dueCycle));
    }
    linkToCcQ.swap(linkDeferred);

    while (!ccRetireQ.empty()) {
        CcRetireEvent ev = ccRetireQ.front();
        ccRetireQ.pop_front();
        if (!ev.valid) {
            continue;
        }
        panic_if(ev.tokenId < 0 ||
                 static_cast<size_t>(ev.tokenId) >= tokens.size(),
                 "HNF retire invalid token=%d\n", ev.tokenId);
        ResourceToken& token = tokens[ev.tokenId];
        if (tokenEventIsStale(
                token, ev.allocationSeq, ev.srcid, ev.txnid, "retire")) {
            continue;
        }
        if (token.state == TokenState::RetireHeldForPCrdGrant ||
            token.state == TokenState::StaticReserved) {
            DPRINTF(HomeLinkLayer,
                    "ignore duplicate retire token=%d seq=%llu state=%u\n",
                    ev.tokenId,
                    static_cast<unsigned long long>(ev.allocationSeq),
                    static_cast<unsigned>(token.state));
            continue;
        }
        panic_if(token.state != TokenState::WorkingDynamic &&
                     token.state != TokenState::WorkingStatic &&
                     token.state != TokenState::WorkingFvb,
                 "HNF retire token=%d seq=%llu has invalid state=%u\n",
                 ev.tokenId,
                 static_cast<unsigned long long>(ev.allocationSeq),
                 static_cast<unsigned>(token.state));
        panic_if(token.resourceClass != ev.resourceClass,
                 "HNF retire token=%d resource class changed %u != %u\n",
                 ev.tokenId, token.resourceClass, ev.resourceClass);
        if (token.resourceClass == static_cast<uint8_t>(ResourceClass::Fvb)) {
            releaseToken(token);
            continue;
        }
        if (!hasEligiblePendingRetry(token.resourceClass)) {
            releaseToken(token);
            continue;
        }
        token.state = TokenState::RetireHeldForPCrdGrant;
        token.reqPriority = ev.reqPriority;
        DPRINTF(HomeLinkLayer,
                "token=%d held for PCrdGrant class=%u pendingRetry=%u\n",
                token.id, token.resourceClass, !pendingRetry.empty());
    }
}

void
HomeLinkLayer::doRetryWakeup()
{
    if (retryDecisionQ.empty()) {
        return;
    }
    if (retryAckFifo.size() >= retryAckFifoDepth) {
        DPRINTF(HomeLinkLayer, "RetryAck FIFO full\n");
        return;
    }

    RetryRecord rec = retryDecisionQ.front();
    retryDecisionQ.pop_front();

    RawRsp rsp{};
    rsp.qos = rec.qos;
    rsp.srcid = rec.tgtid;
    rsp.tgtid = rec.srcid;
    rsp.txnid = rec.txnid;
    rsp.opcode = RspOp::RetryAck;
    rsp.stage = BasicChiComponent::STAGE_H2;
    rsp.pcrdtype = rec.pcrdtype;
    rsp.resp = RespSC;
    rsp.rspKind = RspKind::RetryAck;
    rsp.originSeq = rec.originSeq;
    rsp.originCycle = rec.retryCycle;

    TxRspPending pending{};
    pending.rsp = rsp;
    pending.kind = TxRspKind::RetryAck;
    retryAckFifo.push_back(pending);

    pendingRetry.increment(rec.priority, rec.srcid, rec.pcrdtype);
    pendingRetryRecords.push_back(rec);
    panic_if(pendingRetry.totalForSrc(rec.srcid) > 256,
             "HNF pending retry overflow src=%u\n", rec.srcid);

    DPRINTF(HomeLinkLayer,
            "queue RetryAck src=%u txn=%u prio=%u pcrdtype=%u\n",
            rec.srcid, rec.txnid, rec.priority, rec.pcrdtype);
}

void
HomeLinkLayer::doPcrdGrantWakeup()
{
    for (auto& token : tokens) {
        if (token.state != TokenState::RetireHeldForPCrdGrant) {
            continue;
        }
        if (pcrdGrantFifo.size() >= pcrdGrantFifoDepth) {
            DPRINTF(HomeLinkLayer, "PCrdGrant FIFO full\n");
            continue;
        }

        PendingWinner winner =
            selectPendingRetryForResource(token.resourceClass);
        if (!winner.valid) {
            releaseToken(token);
            continue;
        }

        token.state = TokenState::StaticReserved;
        token.staticOwnerSrcid = winner.srcid;
        token.staticPcrdtype = winner.pcrdtype;
        token.staticPriority = winner.priority;

        RawRsp rsp{};
        rsp.qos = winner.qos;
        rsp.srcid = winner.tgtid;
        rsp.tgtid = winner.srcid;
        rsp.txnid = winner.txnid;
        rsp.opcode = RspOp::PCrdGrant;
        rsp.stage = BasicChiComponent::STAGE_H2;
        rsp.pcrdtype = winner.pcrdtype;
        rsp.resp = RespSC;
        rsp.rspKind = RspKind::PCrdGrant;
        rsp.originCycle = llCycle;

        TxRspPending pending{};
        pending.rsp = rsp;
        pending.kind = TxRspKind::PCrdGrant;
        pcrdGrantFifo.push_back(pending);
        DPRINTF(HomeLinkLayer,
                "queue PCrdGrant token=%d tgt=%u txn=%u pcrdtype=%u\n",
                token.id, rsp.tgtid, rsp.txnid, rsp.pcrdtype);
    }
}

void
HomeLinkLayer::doRxPipelineWakeup()
{
    for (size_t ch = 0; ch < NumCh; ++ch) {
        for (int st = static_cast<int>(NumLlStages) - 1; st >= 0; --st) {
            StageQueue& q = rxPipe[ch][st];
            const size_t count = q.size();
            for (size_t i = 0; i < count; ++i) {
                PipeEntry entry = std::move(q.front());
                q.pop_front();

                StageResult result = dispatchStage(entry);
                switch (result.action) {
                  case StageAction::Advance:
                    if (entry.stage + 1 >= NumLlStages) {
                        break;
                    }
                    ++entry.stage;
                    setVariantStage(entry.flit, entry.stage);
                    rxPipe[ch][entry.stage].push_back(std::move(entry));
                    break;
                  case StageAction::Stay:
                    q.push_back(std::move(entry));
                    break;
                  case StageAction::Drop:
                    break;
                  case StageAction::Error:
                    panic("HomeLinkLayer stage error ch=%s stage=%u\n",
                          channelName(static_cast<ChannelType>(ch)), st);
                }
            }
        }
    }
}

void
HomeLinkLayer::sampleRxPortsToH0()
{
    if (!rxport) {
        return;
    }

    for (ChannelType ch : {ChannelType::REQ, ChannelType::RSP,
                           ChannelType::SNP, ChannelType::DAT}) {
        if (ch == ChannelType::REQ && !acceptNewRxReq) {
            continue;
        }
        auto flit = rxport->getRxFlitNoCredit(ch);
        if (!flit) {
            continue;
        }

        PipeEntry entry{};
        entry.flit = std::move(*flit);
        entry.channel = ch;
        entry.stage = BasicChiComponent::STAGE_H0;
        entry.seq = nextSeq++;
        entry.enterCycle = llCycle;
        setVariantStage(entry.flit, BasicChiComponent::STAGE_H0);

        const bool typeOk =
            (ch == ChannelType::REQ &&
             std::holds_alternative<RawReq>(entry.flit)) ||
            (ch == ChannelType::RSP &&
             std::holds_alternative<RawRsp>(entry.flit)) ||
            (ch == ChannelType::SNP &&
             std::holds_alternative<RawSnp>(entry.flit)) ||
            (ch == ChannelType::DAT &&
             std::holds_alternative<RawDat>(entry.flit));
        panic_if(!typeOk, "HNF sampled wrong flit type on RX%s\n",
                 channelName(ch));

        rxPipe[static_cast<size_t>(ch)][0].push_back(std::move(entry));
        if (ch != ChannelType::REQ) {
            scheduleRxCreditReturn(ch, 1, llCycle + 1);
        }
        DPRINTF(HomeLinkLayer,
                "sample RX%s into H0 seq=%llu cycle=%llu\n",
                channelName(ch),
                static_cast<unsigned long long>(nextSeq - 1),
                static_cast<unsigned long long>(llCycle));
    }
}

HomeLinkLayer::StageResult
HomeLinkLayer::dispatchStage(PipeEntry& entry)
{
    return std::visit([this, &entry](auto& flit) -> StageResult {
        using T = std::decay_t<decltype(flit)>;
        if constexpr (std::is_same_v<T, RawReq>) {
            switch (entry.stage) {
              case 0: return doStageH0Req(entry, flit);
              case 1: return doStageH1Req(entry, flit);
              case 2: return doStageH2Req(entry, flit);
              case 3: return doStageH3Req(entry, flit);
              default: return {StageAction::Error};
            }
        } else if constexpr (std::is_same_v<T, RawRsp>) {
            return doStageRsp(entry, flit);
        } else if constexpr (std::is_same_v<T, RawDat>) {
            return doStageDat(entry, flit);
        } else if constexpr (std::is_same_v<T, RawSnp>) {
            return doStageSnp(entry, flit);
        }
        return {StageAction::Error};
    }, entry.flit);
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageH0Req(PipeEntry& entry, RawReq& req)
{
    DPRINTF(HomeLinkLayer,
            "RXREQ H0 seq=%llu opcode=0x%x src=%u txn=%u addr=%#llx\n",
            static_cast<unsigned long long>(entry.seq), req.opcode,
            req.srcid, req.txnid, static_cast<unsigned long long>(req.addr));
    return {StageAction::Advance};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageH1Req(PipeEntry& entry, RawReq& req)
{
    const auto decoded = decodeReq(req.opcode);
    DPRINTF(HomeLinkLayer,
            "RXREQ H1 seq=%llu decode opcode=0x%x major=%u minor=%u "
            "src=%u txn=%u allowRetry=%u pcrdtype=%u\n",
            static_cast<unsigned long long>(entry.seq), req.opcode,
            static_cast<unsigned>(decoded.major),
            static_cast<unsigned>(decoded.minor), req.srcid, req.txnid,
            req.AllowRetry, req.pcrdtype);

    panic_if(decoded.major == ReqMajor::ReservedOrUnsupported,
             "HNF unsupported RXREQ opcode=0x%x src=%u txn=%u\n",
             req.opcode, req.srcid, req.txnid);

    LlPriority prio = mapPriority(req);
    entry.priority = static_cast<uint8_t>(prio);

    if (req.AllowRetry || !retryEnabled) {
        auto tokenId = allocDynamicToken(prio);
        if (!tokenId) {
            if (retryEnabled && req.AllowRetry) {
                RetryRecord rec{};
                rec.originSeq = entry.seq;
                rec.retryCycle = llCycle;
                rec.srcid = req.srcid;
                rec.tgtid = req.tgtid;
                rec.txnid = req.txnid;
                rec.qos = req.qos;
                rec.priority = static_cast<uint8_t>(prio);
                rec.pcrdtype = req.pcrdtype;
                retryDecisionQ.push_back(rec);
                DPRINTF(HomeLinkLayer,
                        "RXREQ H1 retry decision seq=%llu src=%u txn=%u\n",
                        static_cast<unsigned long long>(entry.seq),
                        req.srcid, req.txnid);
                return {StageAction::Drop};
            }
            return {StageAction::Stay};
        }

        reserveTokenPendingCcAck(*tokenId, entry, req, prio, false);
        queueCcAdmit(entry, req, *tokenId, prio, false);
        return {StageAction::Drop};
    }

    auto tokenId = findStaticReservation(req.srcid, req.pcrdtype);
    panic_if(!tokenId,
             "AllowRetry=0 request without static reservation: "
             "srcid=%u pcrdtype=%u txn=%u\n",
             req.srcid, req.pcrdtype, req.txnid);

    reserveTokenPendingCcAck(*tokenId, entry, req, prio, true);
    queueCcAdmit(entry, req, *tokenId, prio, true);
    return {StageAction::Drop};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageH2Req(PipeEntry& entry, RawReq& req)
{
    DPRINTF(HomeLinkLayer,
            "RXREQ H2 empty stage seq=%llu opcode=0x%x src=%u txn=%u\n",
            static_cast<unsigned long long>(entry.seq), req.opcode,
            req.srcid, req.txnid);
    return {StageAction::Advance};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageH3Req(PipeEntry& entry, RawReq& req)
{
    DPRINTF(HomeLinkLayer,
            "RXREQ H3 drop seq=%llu opcode=0x%x src=%u txn=%u\n",
            static_cast<unsigned long long>(entry.seq), req.opcode,
            req.srcid, req.txnid);
    return {StageAction::Drop};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageRsp(PipeEntry& entry, RawRsp& rsp)
{
    if (entry.stage < BasicChiComponent::STAGE_H2) {
        DPRINTF(HomeLinkLayer,
                "RXRSP H%u seq=%llu opcode=0x%x src=%u txn=%u\n",
                entry.stage, static_cast<unsigned long long>(entry.seq),
                rsp.opcode, rsp.srcid, rsp.txnid);
        return {StageAction::Advance};
    }

    const auto decoded = decodeRsp(rsp.opcode);
    panic_if(decoded.minor != RspMinor::CompAck &&
                 decoded.minor != RspMinor::Comp &&
                 decoded.minor != RspMinor::CompDBIDResp &&
                 decoded.minor != RspMinor::DBIDResp &&
                 decoded.minor != RspMinor::DBIDRespOrd &&
                 decoded.minor != RspMinor::RetryAck &&
                 decoded.minor != RspMinor::PCrdGrant &&
                 decoded.minor != RspMinor::SnpResp &&
                 decoded.minor != RspMinor::SnpRespFwded,
             "HNF RXRSP unsupported opcode=0x%x txn=%u\n",
             rsp.opcode, rsp.txnid);
    panic_if(!cc, "HNF RXRSP without CC src=%u txn=%u\n",
             rsp.srcid, rsp.txnid);
    std::optional<HnfCcRetireInfo> retire = cc->acceptRxRsp(rsp);
    if (retire) {
        queueRetire(*retire);
    }
    DPRINTF(HomeLinkLayer,
            "RXRSP H2 opcode=0x%x src=%u txn=%u handed to CC retire=%u\n",
            rsp.opcode,
            rsp.srcid, rsp.txnid, retire.has_value());
    return {StageAction::Drop};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageDat(PipeEntry& entry, RawDat& dat)
{
    if (entry.stage < BasicChiComponent::STAGE_H2) {
        DPRINTF(HomeLinkLayer,
                "RXDAT H%u seq=%llu opcode=0x%x src=%u txn=%u dbid=%u\n",
                entry.stage, static_cast<unsigned long long>(entry.seq),
                dat.opcode, dat.srcid, dat.txnid, dat.dbid);
        return {StageAction::Advance};
    }

    const auto decoded = decodeDat(dat.opcode);
    if (decoded.major == DatMajor::SnpRespData) {
        panic_if(!cc, "HNF RXDAT SnpRespData without CC src=%u txn=%u\n",
                 dat.srcid, dat.txnid);
        std::optional<HnfCcRetireInfo> retire = cc->acceptRxDat(dat);
        panic_if(retire,
                 "HNF RXDAT SnpRespData unexpectedly retired txn=%u\n",
                 dat.txnid);
        DPRINTF(HomeLinkLayer,
                "RXDAT H2 SnpRespData src=%u txn=%u dataid=%u bytes=%u "
                "last=%u resp=%u\n",
                dat.srcid, dat.txnid, dat.dataid,
                static_cast<unsigned>(dat.data.size()), dat.last, dat.resp);
        return {StageAction::Drop};
    }

    if (decoded.major == DatMajor::CompletionData) {
        panic_if(!cc, "HNF RXDAT CompData without CC src=%u txn=%u\n",
                 dat.srcid, dat.txnid);
        std::optional<HnfCcRetireInfo> retire = cc->acceptRxDat(dat);
        if (retire) {
            queueRetire(*retire);
        }
        DPRINTF(HomeLinkLayer,
                "RXDAT H2 CompData src=%u txn=%u dataid=%u bytes=%u "
                "last=%u retire=%u\n",
                dat.srcid, dat.txnid, dat.dataid,
                static_cast<unsigned>(dat.data.size()), dat.last,
                retire.has_value());
        return {StageAction::Drop};
    }

    if (decoded.major == DatMajor::WriteData) {
        panic_if(!cc, "HNF RXDAT write data without CC src=%u txn=%u\n",
                 dat.srcid, dat.txnid);
        std::optional<HnfCcRetireInfo> retire = cc->acceptRxDat(dat);
        if (retire) {
            queueRetire(*retire);
        }
        DPRINTF(HomeLinkLayer,
                "RXDAT H2 write data src=%u txn=%u dbid=%u bytes=%u "
                "last=%u retire=%u\n",
                dat.srcid, dat.txnid, dat.dbid,
                static_cast<unsigned>(dat.data.size()), dat.last,
                retire.has_value());
        return {StageAction::Drop};
    }

    panic("HNF RXDAT unsupported opcode=0x%x txn=%u\n", dat.opcode,
          dat.txnid);
    return {StageAction::Drop};
}

HomeLinkLayer::StageResult
HomeLinkLayer::doStageSnp(PipeEntry& entry, RawSnp& snp)
{
    panic("HNF LinkLayer v1 does not accept RX SNP flits seq=%llu "
          "opcode=0x%x src=%u txn=%u\n",
          static_cast<unsigned long long>(entry.seq), snp.opcode,
          snp.srcid, snp.txnid);
    return {StageAction::Error};
}

bool
HomeLinkLayer::hasPendingWork() const
{
    return portHasRxFlit() || pipelineHasWork() || txQueuesHaveWork() ||
        !linkToCcQ.empty() || !ccAdmitQ.empty() || !ccRetireQ.empty() ||
        !retryDecisionQ.empty() || !creditEvents.empty() ||
        hasHeldRetireToken() || (cc && cc->hasWork());
}

bool
HomeLinkLayer::pipelineHasWork() const
{
    for (const auto& pipe : rxPipe) {
        for (const auto& stageQ : pipe) {
            if (!stageQ.empty()) {
                return true;
            }
        }
    }
    return false;
}

bool
HomeLinkLayer::txQueuesHaveWork() const
{
    return !shortPathFifo.empty() || !retryAckFifo.empty() ||
        !pcrdGrantFifo.empty() || !mainPathFifo.empty() || !txDatQ.empty() ||
        (cc && cc->hasTxWork());
}

bool
HomeLinkLayer::portHasRxFlit() const
{
    if (!rxport) {
        return false;
    }
    for (ChannelType ch : {ChannelType::REQ, ChannelType::RSP,
                           ChannelType::SNP, ChannelType::DAT}) {
        if (ch == ChannelType::REQ && !acceptNewRxReq) {
            continue;
        }
        if (rxport->hasRxFlit(ch)) {
            return true;
        }
    }
    return false;
}

bool
HomeLinkLayer::requestPipelineHasWork() const
{
    const ChannelPipe& request_pipe =
        rxPipe[static_cast<size_t>(ChannelType::REQ)];
    return std::any_of(
        request_pipe.begin(), request_pipe.end(),
        [](const StageQueue& stage) { return !stage.empty(); });
}

bool
HomeLinkLayer::mayGenerateSlcsfIntent() const
{
    return requestPipelineHasWork() || !linkToCcQ.empty() ||
        !ccAdmitQ.empty() || (cc && cc->mayGenerateSlcsfIntent());
}

bool
HomeLinkLayer::hasHeldRetireToken() const
{
    for (const auto& token : tokens) {
        if (token.state == TokenState::RetireHeldForPCrdGrant) {
            return true;
        }
    }
    return false;
}

void
HomeLinkLayer::scheduleNextCycle()
{
    panic_if(!m_homenode, "HomeLinkLayer missing HomeNodeFull owner\n");
    m_homenode->scheduleEvent(Cycles(1));
}

HomeLinkLayer::LlPriority
HomeLinkLayer::mapPriority(const RawReq& req) const
{
    if (req.qos == 15) {
        return LlPriority::HHigh;
    }
    if (req.qos >= 12) {
        return LlPriority::High;
    }
    if (req.qos >= 8) {
        return LlPriority::Medium;
    }
    return LlPriority::Low;
}

std::optional<int>
HomeLinkLayer::allocDynamicToken(LlPriority prio)
{
    for (int cls = static_cast<int>(prio);
         cls >= static_cast<int>(LlPriority::Low); --cls) {
        for (auto& token : tokens) {
            if (token.state == TokenState::Free &&
                token.resourceClass == static_cast<uint8_t>(cls)) {
                return token.id;
            }
        }
    }
    return std::nullopt;
}

std::optional<int>
HomeLinkLayer::findStaticReservation(uint32_t srcid,
                                     uint8_t pcrdtype) const
{
    for (const auto& token : tokens) {
        if (token.state == TokenState::StaticReserved &&
            token.staticOwnerSrcid == srcid &&
            token.staticPcrdtype == pcrdtype) {
            return token.id;
        }
    }
    return std::nullopt;
}

void
HomeLinkLayer::reserveTokenPendingCcAck(int tokenId, const PipeEntry& entry,
                                        const RawReq& req, LlPriority prio,
                                        bool isStatic)
{
    panic_if(tokenId < 0 || static_cast<size_t>(tokenId) >= tokens.size(),
             "HNF invalid token allocation token=%d\n", tokenId);
    ResourceToken& token = tokens[tokenId];
    if (isStatic) {
        panic_if(token.state != TokenState::StaticReserved,
                 "HNF static allocation without reservation token=%d\n",
                 tokenId);
    } else {
        panic_if(token.state != TokenState::Free,
                 "HNF dynamic allocation on non-free token=%d state=%u\n",
                 tokenId, static_cast<unsigned>(token.state));
    }

    token.state = TokenState::AllocPendingCcAck;
    token.reqPriority = static_cast<uint8_t>(prio);
    token.ownerSrcid = req.srcid;
    token.ownerTxnid = req.txnid;
    token.pcrdtype = req.pcrdtype;
    token.allocatedSeq = entry.seq;
    token.allocatedCycle = llCycle;
    token.pendingStatic = isStatic;
    token.pendingFvb = false;
}

void
HomeLinkLayer::releaseToken(ResourceToken& token)
{
    DPRINTF(HomeLinkLayer,
            "release token=%d state=%u owner=%u pcrdtype=%u\n",
            token.id, static_cast<unsigned>(token.state),
            token.ownerSrcid, token.pcrdtype);
    const int id = token.id;
    const uint8_t resourceClass = token.resourceClass;
    token = ResourceToken{};
    token.id = id;
    token.resourceClass = resourceClass;
}

bool
HomeLinkLayer::pendingPrioCanUseResource(uint8_t pendingPrio,
                                         uint8_t resourceClass) const
{
    if (resourceClass == static_cast<uint8_t>(ResourceClass::Fvb)) {
        return false;
    }
    return pendingPrio >= resourceClass;
}

bool
HomeLinkLayer::hasEligiblePendingRetry(uint8_t resourceClass) const
{
    for (const auto& rec : pendingRetryRecords) {
        if (pendingPrioCanUseResource(rec.priority, resourceClass)) {
            return true;
        }
    }
    return false;
}

HomeLinkLayer::PendingWinner
HomeLinkLayer::selectPendingRetryForResource(uint8_t resourceClass)
{
    for (int prio = static_cast<int>(LlPriority::HHigh);
         prio >= static_cast<int>(LlPriority::Low); --prio) {
        for (auto it = pendingRetryRecords.begin();
             it != pendingRetryRecords.end(); ++it) {
            if (it->priority != static_cast<uint8_t>(prio) ||
                !pendingPrioCanUseResource(it->priority, resourceClass)) {
                continue;
            }

            PendingWinner winner{};
            winner.valid = true;
            winner.priority = it->priority;
            winner.srcid = it->srcid;
            winner.tgtid = it->tgtid;
            winner.txnid = it->txnid;
            winner.qos = it->qos;
            winner.pcrdtype = it->pcrdtype;
            pendingRetry.decrement(it->priority, it->srcid, it->pcrdtype);
            pendingRetryRecords.erase(it);
            return winner;
        }
    }
    return {};
}

void
HomeLinkLayer::scheduleRxCreditReturn(ChannelType ch, uint8_t amount,
                                      uint64_t dueCycle)
{
    CreditEvent ev{};
    ev.ch = ch;
    ev.amount = amount;
    ev.dueCycle = dueCycle;
    creditEvents.push_back(ev);
}

void
HomeLinkLayer::queueCcAdmit(const PipeEntry& entry, const RawReq& req,
                            int tokenId, LlPriority prio, bool isStatic)
{
    HnfLinkToCcReq out{};
    out.valid = true;
    out.seq = entry.seq;
    out.req = req;
    out.tokenId = tokenId;
    out.priority = static_cast<uint8_t>(prio);
    out.resourceClass = tokens[tokenId].resourceClass;
    out.isDynamic = !isStatic;
    out.isStatic = isStatic;
    out.dueCycle = llCycle + 1;
    linkToCcQ.push_back(out);

    DPRINTF(HomeLinkLayer,
            "Link->CC seq=%llu token=%d static=%u due=%llu\n",
            static_cast<unsigned long long>(entry.seq), tokenId, isStatic,
            static_cast<unsigned long long>(out.dueCycle));
}

void
HomeLinkLayer::processCcAdmitResult(const CcAdmitResult& result)
{
    if (!result.valid) {
        return;
    }
    panic_if(result.tokenId < 0 ||
             static_cast<size_t>(result.tokenId) >= tokens.size(),
             "HNF CC admit invalid token=%d\n", result.tokenId);

    ResourceToken& token = tokens[result.tokenId];
    if (tokenEventIsStale(
            token, result.seq, result.req.srcid, result.req.txnid,
            "CC admit result")) {
        return;
    }
    if (!result.accepted) {
        panic_if(token.state != TokenState::AllocPendingCcAck,
                 "HNF rejected CC admit token=%d not pending state=%u\n",
                 result.tokenId, static_cast<unsigned>(token.state));
        releaseToken(token);
        return;
    }

    panic_if(token.state != TokenState::AllocPendingCcAck,
             "HNF CC admit token=%d not pending state=%u\n",
             result.tokenId, static_cast<unsigned>(token.state));

    token.state = token.pendingStatic ? TokenState::WorkingStatic :
        TokenState::WorkingDynamic;
    token.pendingStatic = false;

    scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);

    DPRINTF(HomeLinkLayer,
            "CC admit seq=%llu token=%d accepted, RXREQ credit due=%llu\n",
            static_cast<unsigned long long>(result.seq), result.tokenId,
            static_cast<unsigned long long>(llCycle + 1));
}

bool
HomeLinkLayer::tokenEventIsStale(
    const ResourceToken& token, uint64_t allocation_seq, uint32_t srcid,
    uint32_t txnid, const char* event) const
{
    const HnfCcIdentityMatch match = classifyHnfCcIdentity(
        HnfCcAllocationIdentity{
            token.allocatedSeq, token.ownerSrcid, token.ownerTxnid},
        HnfCcAllocationIdentity{allocation_seq, srcid, txnid});
    if (match == HnfCcIdentityMatch::StaleGeneration) {
        DPRINTF(HomeLinkLayer,
                "ignore stale %s token=%d eventSeq=%llu activeSeq=%llu "
                "src=%u txn=%u\n",
                event, token.id,
                static_cast<unsigned long long>(allocation_seq),
                static_cast<unsigned long long>(token.allocatedSeq),
                srcid, txnid);
        return true;
    }
    panic_if(match == HnfCcIdentityMatch::CorruptOwner,
             "HNF %s token=%d seq=%llu identity changed "
             "event=(%u,%u) active=(%u,%u)\n",
             event, token.id,
             static_cast<unsigned long long>(allocation_seq), srcid, txnid,
             token.ownerSrcid, token.ownerTxnid);
    return false;
}

void
HomeLinkLayer::queueRetire(uint32_t entry)
{
    retireEntry(entry);
}

void
HomeLinkLayer::queueRetire(const HnfCcRetireInfo& info)
{
    if (!info.valid) {
        return;
    }
    CcRetireEvent ev{};
    ev.valid = true;
    ev.tokenId = info.tokenId;
    ev.allocationSeq = info.allocationSeq;
    ev.resourceClass = info.resourceClass;
    ev.reqPriority = info.reqPriority;
    ev.srcid = info.srcid;
    ev.txnid = info.txnid;
    ev.pcrdtype = info.pcrdtype;
    ccRetireQ.push_back(ev);
}

bool
HomeLinkLayer::allocateRequest(const RawReq& req, uint32_t entry,
                               int tokenId)
{
    panic_if(entry >= entries.size(), "HNF invalid entry=%u\n", entry);
    panic_if(entries[entry].valid, "HNF entry already valid entry=%u\n",
             entry);

    const TxnKey key{req.srcid, req.txnid};
    panic_if(txnLookup.find(key) != txnLookup.end(),
             "HNF duplicate RXREQ src=%u txn=%u\n", req.srcid, req.txnid);

    MinimalHnfTxn& txn = entries[entry];
    txn = MinimalHnfTxn{};
    txn.valid = true;
    txn.req = req;
    txn.key = key;
    txn.entry = entry;
    txn.dbid = static_cast<uint8_t>(entry + 1);
    txn.expectedDataBytes = expectedDataBytes(req);
    txn.tokenId = tokenId;

    if (isSupportedReadReq(req.opcode)) {
        txn.kind = TxnKind::Read;
        txnLookup.emplace(key, txn.entry);
        dbidLookup.emplace(txn.dbid, txn.entry);
        enqueueReadData(txn);
    } else if (isSupportedMaintenanceReq(req.opcode)) {
        txn.kind = TxnKind::Maintenance;
        txnLookup.emplace(key, txn.entry);
        dbidLookup.emplace(txn.dbid, txn.entry);
        enqueueComp(req, txn.entry);
    } else if (isSupportedWriteReq(req.opcode)) {
        txn.kind = TxnKind::Write;
        txnLookup.emplace(key, txn.entry);
        dbidLookup.emplace(txn.dbid, txn.entry);
        enqueueCompDbid(txn);
    } else {
        panic("HNF unsupported RXREQ opcode=0x%x src=%u txn=%u\n",
              req.opcode, req.srcid, req.txnid);
    }

    DPRINTF(HomeLinkLayer,
            "allocated entry=%u token=%d kind=%u src=%u txn=%u dbid=%u "
            "opcode=0x%x\n",
            txn.entry, tokenId, static_cast<unsigned>(txn.kind),
            txn.key.srcid, txn.key.txnid, txn.dbid, txn.req.opcode);
    return true;
}

void
HomeLinkLayer::retireEntry(uint32_t entry)
{
    panic_if(entry >= entries.size() || !entries[entry].valid,
             "HNF retire invalid entry=%u\n", entry);

    MinimalHnfTxn& txn = entries[entry];
    panic_if(txn.tokenId < 0 ||
             static_cast<size_t>(txn.tokenId) >= tokens.size(),
             "HNF retire entry=%u invalid token=%d\n", entry, txn.tokenId);
    ResourceToken& token = tokens[txn.tokenId];

    CcRetireEvent ev{};
    ev.valid = true;
    ev.tokenId = txn.tokenId;
    ev.allocationSeq = token.allocatedSeq;
    ev.resourceClass = token.resourceClass;
    ev.reqPriority = token.reqPriority;
    ev.srcid = txn.key.srcid;
    ev.txnid = txn.key.txnid;
    ev.pcrdtype = txn.req.pcrdtype;
    ccRetireQ.push_back(ev);

    DPRINTF(HomeLinkLayer,
            "retire entry=%u token=%d kind=%u src=%u txn=%u dbid=%u\n",
            entry, txn.tokenId, static_cast<unsigned>(txn.kind),
            txn.key.srcid, txn.key.txnid, txn.dbid);
    releaseEntry(entry);
}

void
HomeLinkLayer::releaseEntry(uint32_t entry)
{
    MinimalHnfTxn& txn = entries[entry];
    txnLookup.erase(txn.key);
    dbidLookup.erase(txn.dbid);
    txn = MinimalHnfTxn{};
}

void
HomeLinkLayer::enqueueComp(const RawReq& req,
                           std::optional<uint32_t> retireEntry)
{
    TxRspPending comp{};
    comp.rsp = makeRsp(req, RspOp::Comp);
    comp.rsp.rspKind = RspKind::MainPath;
    comp.kind = TxRspKind::Comp;
    comp.retireEntry = retireEntry;
    mainPathFifo.push_back(comp);
}

void
HomeLinkLayer::enqueueCompDbid(MinimalHnfTxn& txn)
{
    if (txn.compDbidQueued) {
        return;
    }
    TxRspPending comp{};
    comp.rsp = makeRsp(txn.req, RspOp::CompDBIDResp);
    comp.rsp.dbid = txn.dbid;
    comp.rsp.rspKind = RspKind::MainPath;
    comp.kind = TxRspKind::CompDBIDResp;
    mainPathFifo.push_back(comp);
    txn.compDbidQueued = true;
}

void
HomeLinkLayer::enqueueReadData(MinimalHnfTxn& txn)
{
    if (txn.readDataQueued) {
        return;
    }

    const uint32_t bytes = expectedDataBytes(txn.req);
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        TxDatPending pending{};
        RawDat& dat = pending.dat;
        dat.qos = txn.req.qos;
        dat.srcid = txn.req.tgtid;
        dat.tgtid = txn.req.srcid;
        dat.txnid = txn.req.txnid;
        dat.opcode = DatOp::CompData;
        dat.stage = BasicChiComponent::STAGE_H11;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = txn.req.tgtid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = RespSC;
        dat.beatOffset = offset;
        dat.data.assign(beatBytes, 0);
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        txDatQ.push_back(std::move(pending));
    }

    txn.readDataQueued = true;
    DPRINTF(HomeLinkLayer,
            "queue zero CompData src=%u txn=%u entry=%u bytes=%u\n",
            txn.key.srcid, txn.key.txnid, txn.entry, bytes);
}

RawRsp
HomeLinkLayer::makeRsp(const RawReq& req, uint8_t opcode) const
{
    RawRsp rsp{};
    rsp.qos = req.qos;
    rsp.srcid = req.tgtid;
    rsp.tgtid = req.srcid;
    rsp.txnid = req.txnid;
    rsp.opcode = opcode;
    rsp.stage = BasicChiComponent::STAGE_H2;
    rsp.dbid = 0;
    rsp.resp = RespSC;
    rsp.pcrdtype = req.pcrdtype;
    rsp.rspKind = RspKind::MainPath;
    return rsp;
}

bool
HomeLinkLayer::sendRspQueue(std::deque<TxRspPending>& q)
{
    if (!rxport || q.empty()) {
        return false;
    }

    TxRspPending pending = q.front();
    pending.rsp.stage = BasicChiComponent::STAGE_H3;
    if (!rxport->enqueueTx(ChannelType::RSP, pending.rsp)) {
        DPRINTF(HomeLinkLayer,
                "TXRSP H3 blocked kind=%u opcode=0x%x src=%u txn=%u\n",
                static_cast<unsigned>(pending.kind), pending.rsp.opcode,
                pending.rsp.srcid, pending.rsp.txnid);
        return false;
    }

    DPRINTF(HomeLinkLayer,
            "TXRSP H3 send kind=%u opcode=0x%x src=%u tgt=%u txn=%u "
            "dbid=%u pcrdtype=%u\n",
            static_cast<unsigned>(pending.kind), pending.rsp.opcode,
            pending.rsp.srcid, pending.rsp.tgtid, pending.rsp.txnid,
            pending.rsp.dbid, pending.rsp.pcrdtype);
    q.pop_front();

    if (pending.kind == TxRspKind::RetryAck) {
        scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);
    }
    if (pending.retireEntry) {
        queueRetire(*pending.retireEntry);
    }
    return true;
}

HomeLinkLayer::MinimalHnfTxn*
HomeLinkLayer::findTxn(const RawRsp& rsp)
{
    const TxnKey key{rsp.srcid, rsp.txnid};
    auto it = txnLookup.find(key);
    if (it == txnLookup.end()) {
        return nullptr;
    }
    return &entries[it->second];
}

HomeLinkLayer::MinimalHnfTxn*
HomeLinkLayer::findTxn(const RawDat& dat)
{
    const TxnKey key{dat.srcid, dat.txnid};
    auto it = txnLookup.find(key);
    if (it != txnLookup.end()) {
        return &entries[it->second];
    }
    auto dbidIt = dbidLookup.find(dat.dbid);
    if (dbidIt != dbidLookup.end()) {
        return &entries[dbidIt->second];
    }
    return nullptr;
}

bool
HomeLinkLayer::isSupportedReadReq(uint8_t opcode) const
{
    const auto decoded = decodeReq(opcode);
    return decoded.minor == ReqMinor::ReadShared ||
        decoded.minor == ReqMinor::ReadUnique ||
        decoded.minor == ReqMinor::ReadClean;
}

bool
HomeLinkLayer::isSupportedMaintenanceReq(uint8_t opcode) const
{
    const auto decoded = decodeReq(opcode);
    return decoded.minor == ReqMinor::MakeUnique ||
        decoded.minor == ReqMinor::MakeInvalid ||
        decoded.minor == ReqMinor::CleanInvalid ||
        decoded.minor == ReqMinor::Evict;
}

bool
HomeLinkLayer::isSupportedWriteReq(uint8_t opcode) const
{
    const auto decoded = decodeReq(opcode);
    return decoded.minor == ReqMinor::WriteUnique ||
        decoded.minor == ReqMinor::WriteBack ||
        decoded.minor == ReqMinor::WriteClean ||
        decoded.minor == ReqMinor::WriteEvict;
}

uint32_t
HomeLinkLayer::expectedDataBytes(const RawReq& req) const
{
    return req.size ? req.size : blockSize;
}

} // namespace gem5::Chi

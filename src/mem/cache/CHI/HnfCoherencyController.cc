#include "mem/cache/CHI/HnfCoherencyController.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HnfCC.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"

namespace gem5::Chi
{

namespace
{

namespace ReqOp
{
constexpr uint8_t ReadNoSnp = 0x04;
} // namespace ReqOp

namespace DatOp
{
constexpr uint8_t CompData = 0x04;
} // namespace DatOp

namespace RspOp
{
constexpr uint8_t Comp = 0x04;
constexpr uint8_t CompDBIDResp = 0x05;
} // namespace RspOp

constexpr uint8_t RespSC = 1;

PocqTxnKind
txnKindForReq(const RawReq& req)
{
    const auto decoded = decodeReq(req.opcode);
    switch (decoded.minor) {
      case ReqMinor::ReadShared:
      case ReqMinor::ReadClean:
        return PocqTxnKind::ReadShared;
      case ReqMinor::ReadUnique:
      case ReqMinor::MakeReadUnique:
        return PocqTxnKind::ReadUnique;
      case ReqMinor::ReadNoSnp:
      case ReqMinor::ReadNoSnpSep:
        return PocqTxnKind::ReadNoSnp;
      case ReqMinor::ReadOnce:
        return PocqTxnKind::ReadOnce;
      case ReqMinor::CleanInvalid:
        return PocqTxnKind::CleanInvalid;
      case ReqMinor::MakeInvalid:
        return PocqTxnKind::MakeInvalid;
      case ReqMinor::MakeUnique:
        return PocqTxnKind::MakeUnique;
      case ReqMinor::Evict:
        return PocqTxnKind::Evict;
      case ReqMinor::WriteBack:
        return PocqTxnKind::WriteBackFull;
      case ReqMinor::WriteClean:
        return PocqTxnKind::WriteCleanFull;
      case ReqMinor::WriteUnique:
        return PocqTxnKind::WriteUnique;
      case ReqMinor::WriteEvict:
        return PocqTxnKind::WriteEvictFull;
      default:
        return PocqTxnKind::Unknown;
    }
}

bool
txnNeedsCompAck(PocqTxnKind txn)
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::ReadOnce;
}

bool
txnExpectsWriteData(PocqTxnKind txn)
{
    return txn == PocqTxnKind::WriteBackFull ||
        txn == PocqTxnKind::WriteCleanFull ||
        txn == PocqTxnKind::WriteUnique ||
        txn == PocqTxnKind::WriteEvictFull;
}

} // anonymous namespace

HnfCoherencyController::HnfCoherencyController(
    uint32_t block_size, uint32_t data_beat_bytes, uint32_t num_entries,
    uint32_t sn_node_id, bool direct_sn_fake_data)
    : blockSize(block_size),
      dataBeatBytes(data_beat_bytes),
      maxEntries(num_entries),
      snNodeId(sn_node_id),
      directSnFakeData(direct_sn_fake_data),
      entries(num_entries)
{
    fatal_if(blockSize == 0, "HnfCC block_size must be non-zero\n");
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "HnfCC data_beat_bytes must satisfy 0 < beat <= block\n");
    fatal_if(maxEntries == 0, "HnfCC entry count must be non-zero\n");
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::stepPocq(uint32_t entryId, const PocqEvent& event)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid POCQ entry=%u\n",
             entryId);

    Entry& entry = entries[entryId];
    PocqStepResult step = pocqGraph.tryStep(entry.pocqState, event);
    panic_if(!step.stepped,
             "HnfCC POCQ entry=%u has no transition state=%s event=%s\n",
             entryId, POCQ_StateGraph::stateName(step.oldState),
             POCQ_StateGraph::eventName(event.kind));

    DPRINTF(HnfCC, "CC entry=%u POCQ %s --%s--> %s actions=%u\n",
            entryId, POCQ_StateGraph::stateName(step.oldState),
            POCQ_StateGraph::eventName(event.kind),
            POCQ_StateGraph::stateName(step.nextState),
            static_cast<unsigned>(step.actions.size()));

    std::optional<HnfCcRetireInfo> retire;
    for (PocqActionKind action : step.actions) {
        DPRINTF(HnfCC, "CC entry=%u POCQ action=%s\n", entryId,
                POCQ_StateGraph::actionName(action));
        std::optional<HnfCcRetireInfo> actionRetire =
            executePocqAction(entryId, action, event);
        if (actionRetire) {
            retire = actionRetire;
        }
    }
    return retire;
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::executePocqAction(uint32_t entryId,
                                          PocqActionKind action,
                                          const PocqEvent& event)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid POCQ action entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];

    switch (action) {
      case PocqActionKind::DoSlcLookup: {
        entry.state = HnfCcEntryState::WaitSlc;

        HnfSlcLookupReq lookup{};
        lookup.entry = entryId;
        lookup.req = entry.req;
        lookup.blockAddr = entry.blockAddr;
        entry.slcLookupResult = slcsfUnit->lookup(lookup);

        if (entry.slcLookupResult.slcHit) {
            entry.data = entry.slcLookupResult.data;
            if (entry.data.size() < blockSize) {
                entry.data.resize(blockSize, 0);
            }
        }

        PocqEvent lookupDone{};
        lookupDone.kind = PocqEventKind::SlcLookupDone;
        lookupDone.txn = entry.txnKind;
        lookupDone.slcHit = entry.slcLookupResult.slcHit;
        lookupDone.sfHit = entry.slcLookupResult.sfHit;
        lookupDone.replay = entry.slcLookupResult.replay;
        lookupDone.needsCompAck = entry.needsCompAck;
        return stepPocq(entryId, lookupDone);
      }

      case PocqActionKind::UpdateSlcSf:
        if (entry.slcUpdatePending) {
            slcsfUnit->fillCleanShared(entry.blockAddr, entry.req.srcid,
                                       entry.data);
            entry.slcUpdatePending = false;
        }
        {
            PocqEvent updateDone{};
            updateDone.kind = PocqEventKind::SlcUpdateDone;
            updateDone.txn = entry.txnKind;
            updateDone.needsCompAck = entry.needsCompAck;
            return stepPocq(entryId, updateDone);
        }

      case PocqActionKind::QueueTxReq:
        queueMcRead(entryId);
        return std::nullopt;

      case PocqActionKind::QueueCompData:
        queueCompData(entryId, entry.data);
        {
            PocqEvent txLinkDone{};
            txLinkDone.kind = PocqEventKind::TxLinkDone;
            txLinkDone.txn = entry.txnKind;
            txLinkDone.needsCompAck = entry.needsCompAck;
            return stepPocq(entryId, txLinkDone);
        }

      case PocqActionKind::QueueComp:
        queueComp(entryId);
        return std::nullopt;

      case PocqActionKind::QueueCompDBIDResp:
        queueCompDBIDResp(entryId);
        return std::nullopt;

      case PocqActionKind::WaitCompAck:
        entry.state = HnfCcEntryState::WaitCompAck;
        return std::nullopt;

      case PocqActionKind::WaitWriteData:
        entry.state = HnfCcEntryState::WaitWriteData;
        return std::nullopt;

      case PocqActionKind::StoreWriteData:
        storeWriteData(entryId);
        return std::nullopt;

      case PocqActionKind::FlushSf:
        slcsfUnit->flushSf(entry.blockAddr);
        return std::nullopt;

      case PocqActionKind::FlushL3:
        slcsfUnit->flushL3(entry.blockAddr);
        return std::nullopt;

      case PocqActionKind::WriteL3FlushSf:
        slcsfUnit->writeL3FlushSf(entry.blockAddr, entry.req.srcid,
                                  entry.data);
        return std::nullopt;

      case PocqActionKind::Retire:
        return retireEntry(entryId);

      case PocqActionKind::SleepForReplay:
        entry.state = HnfCcEntryState::Sleep;
        DPRINTF(HnfCC, "CC entry=%u sleeps for SLCSF replay\n", entryId);
        return std::nullopt;
    }

    panic("HnfCC unknown POCQ action=%u event=%u\n",
          static_cast<unsigned>(action), static_cast<unsigned>(event.kind));
}

uint64_t
HnfCoherencyController::blockAddr(const RawReq& req) const
{
    return req.addr & ~(static_cast<uint64_t>(blockSize) - 1);
}

uint32_t
HnfCoherencyController::expectedDataBytes(const RawReq& req) const
{
    return req.size ? req.size : blockSize;
}

bool
HnfCoherencyController::entryAllocated(const Entry& entry) const
{
    return entry.state != HnfCcEntryState::Idle &&
        entry.state != HnfCcEntryState::Retire;
}

bool
HnfCoherencyController::hasAddressHazard(uint32_t entry, uint64_t addr) const
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        if (i == entry || !entryAllocated(entries[i])) {
            continue;
        }
        if (entries[i].blockAddr == addr) {
            return true;
        }
    }
    return false;
}

std::optional<uint32_t>
HnfCoherencyController::findTxn(uint32_t srcid, uint32_t txnid) const
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        const Entry& entry = entries[i];
        if (!entryAllocated(entry)) {
            continue;
        }
        if (entry.req.srcid == srcid && entry.req.txnid == txnid) {
            return i;
        }
    }
    return std::nullopt;
}

HnfCcAdmitResult
HnfCoherencyController::acceptLinkReq(const HnfLinkToCcReq& in,
                                      uint64_t cycle)
{
    HnfCcAdmitResult result{};
    result.valid = true;
    result.seq = in.seq;
    result.tokenId = in.tokenId;
    result.req = in.req;

    panic_if(!in.valid, "HnfCC got invalid Link request\n");
    panic_if(!slcsfUnit, "HnfCC missing SLCSF unit\n");
    panic_if(in.tokenId < 0 || static_cast<size_t>(in.tokenId) >= entries.size(),
             "HnfCC invalid token/entry id=%d\n", in.tokenId);

    const auto decoded = decodeReq(in.req.opcode);
    const PocqTxnKind txnKind = txnKindForReq(in.req);
    panic_if(txnKind == PocqTxnKind::Unknown,
             "HnfCC unsupported opcode=0x%x major=%u minor=%u "
             "src=%u txn=%u\n",
             in.req.opcode, static_cast<unsigned>(decoded.major),
             static_cast<unsigned>(decoded.minor), in.req.srcid,
             in.req.txnid);

    const uint32_t entryId = static_cast<uint32_t>(in.tokenId);
    Entry& entry = entries[entryId];
    if (entryAllocated(entry)) {
        DPRINTF(HnfCC,
                "CC admit rejected busy entry=%u src=%u txn=%u state=%u\n",
                entryId, in.req.srcid, in.req.txnid,
                static_cast<unsigned>(entry.state));
        return result;
    }

    entry = Entry{};
    entry.state = HnfCcEntryState::Working;
    entry.pocqState = PocqState::Idle;
    entry.txnKind = txnKind;
    entry.req = in.req;
    entry.seq = in.seq;
    entry.blockAddr = blockAddr(in.req);
    entry.tokenId = in.tokenId;
    entry.priority = in.priority;
    entry.resourceClass = in.resourceClass;
    entry.isStatic = in.isStatic;
    entry.needsCompAck = txnNeedsCompAck(txnKind);
    entry.expectsWriteData = txnExpectsWriteData(txnKind);
    entry.data.assign(blockSize, 0);

    DPRINTF(HnfCC,
            "CC alloc entry=%u token=%d seq=%llu src=%u txn=%u "
            "addr=%#llx cycle=%llu\n",
            entryId, in.tokenId, static_cast<unsigned long long>(in.seq),
            in.req.srcid, in.req.txnid,
            static_cast<unsigned long long>(entry.blockAddr),
            static_cast<unsigned long long>(cycle));

    if (hasAddressHazard(entryId, entry.blockAddr)) {
        entry.state = HnfCcEntryState::Sleep;
        entry.pocqState = PocqState::Sleep;
        for (uint32_t i = 0; i < entries.size(); ++i) {
            if (i != entryId && entryAllocated(entries[i]) &&
                entries[i].blockAddr == entry.blockAddr) {
                entry.sleepingOn = i;
                break;
            }
        }
        DPRINTF(HnfCC,
                "CC entry=%u sleeps on address hazard addr=%#llx blocker=%u\n",
                entryId, static_cast<unsigned long long>(entry.blockAddr),
                entry.sleepingOn.value_or(UINT32_MAX));
    } else {
        startReadFlow(entryId);
    }

    result.accepted = true;
    return result;
}

void
HnfCoherencyController::startReadFlow(uint32_t entryId)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid read entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];
    entry.pocqState = PocqState::Idle;

    PocqEvent admit{};
    admit.kind = PocqEventKind::Admit;
    admit.txn = entry.txnKind;
    admit.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, admit);
    panic_if(retire, "HnfCC entry=%u retired during admit path\n", entryId);
}

void
HnfCoherencyController::queueMcRead(uint32_t entryId)
{
    Entry& entry = entries[entryId];

    RawReq req{};
    req.qos = entry.req.qos;
    req.srcid = entry.req.tgtid;
    req.tgtid = snNodeId;
    req.txnid = entryId + 1;
    req.opcode = ReqOp::ReadNoSnp;
    req.AllowRetry = 0;
    req.addr = entry.blockAddr;
    req.size = expectedDataBytes(entry.req);
    req.ReturnNid = entry.req.tgtid;
    req.order = entry.req.order;
    req.memattr = entry.req.memattr;
    req.snpattr = 0;
    req.expCompAck = false;
    req.traceTag = entry.req.traceTag;
    req.srcType = entry.req.srcType;
    req.ldid = entry.req.ldid;

    HnfCcTxReq out{};
    out.entry = entryId;
    out.req = req;
    txReqQ.push_back(out);

    entry.state = HnfCcEntryState::IssueMcRead;
    DPRINTF(HnfCC,
            "CC entry=%u queues TXREQ ReadNoSnp addr=%#llx hnf=%u sn=%u "
            "mcTxn=%u requester=%u rnTxn=%u\n",
            entryId, static_cast<unsigned long long>(req.addr), req.srcid,
            req.tgtid, req.txnid, entry.req.srcid, entry.req.txnid);
}

void
HnfCoherencyController::queueCompData(uint32_t entryId,
                                      const std::vector<uint8_t>& data)
{
    Entry& entry = entries[entryId];
    const uint32_t bytes = expectedDataBytes(entry.req);
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        HnfCcTxDat out{};
        out.entry = entryId;
        RawDat& dat = out.dat;
        dat.qos = entry.req.qos;
        dat.srcid = entry.req.tgtid;
        dat.tgtid = entry.req.srcid;
        dat.txnid = entry.req.txnid;
        dat.opcode = DatOp::CompData;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = entry.req.tgtid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = RespSC;
        dat.beatOffset = offset;
        dat.data.assign(beatBytes, 0);
        if (offset < data.size()) {
            const uint32_t copyBytes =
                std::min<uint32_t>(beatBytes, data.size() - offset);
            std::copy(data.begin() + offset, data.begin() + offset + copyBytes,
                      dat.data.begin());
        }
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        txDatQ.push_back(out);
    }

    DPRINTF(HnfCC,
            "CC entry=%u queues CompData beats bytes=%u requester=%u txn=%u\n",
            entryId, bytes, entry.req.srcid, entry.req.txnid);
}

void
HnfCoherencyController::queueComp(uint32_t entryId)
{
    Entry& entry = entries[entryId];

    HnfCcTxRsp out{};
    out.entry = entryId;
    RawRsp& rsp = out.rsp;
    rsp.qos = entry.req.qos;
    rsp.srcid = entry.req.tgtid;
    rsp.tgtid = entry.req.srcid;
    rsp.txnid = entry.req.txnid;
    rsp.opcode = RspOp::Comp;
    rsp.dbid = 0;
    rsp.resp = RespSC;
    rsp.pcrdtype = entry.req.pcrdtype;
    rsp.rspKind = RspKind::MainPath;
    out.retire = makeRetireInfo(entry);
    txRspQ.push_back(out);

    const uint64_t addr = entry.blockAddr;
    const uint32_t srcid = entry.req.srcid;
    const uint32_t txnid = entry.req.txnid;
    entry = Entry{};
    wakeSleepingEntries(addr);

    DPRINTF(HnfCC,
            "CC entry=%u queues Comp src=%u txn=%u and retires after send\n",
            entryId, srcid, txnid);
}

void
HnfCoherencyController::queueCompDBIDResp(uint32_t entryId)
{
    Entry& entry = entries[entryId];

    HnfCcTxRsp out{};
    out.entry = entryId;
    RawRsp& rsp = out.rsp;
    rsp.qos = entry.req.qos;
    rsp.srcid = entry.req.tgtid;
    rsp.tgtid = entry.req.srcid;
    rsp.txnid = entry.req.txnid;
    rsp.opcode = RspOp::CompDBIDResp;
    rsp.dbid = static_cast<uint8_t>(entryId + 1);
    rsp.resp = RespSC;
    rsp.pcrdtype = entry.req.pcrdtype;
    rsp.rspKind = RspKind::MainPath;
    txRspQ.push_back(out);

    entry.state = HnfCcEntryState::WaitWriteData;
    entry.writeDataBytes = 0;
    DPRINTF(HnfCC,
            "CC entry=%u queues CompDBIDResp src=%u txn=%u dbid=%u\n",
            entryId, entry.req.srcid, entry.req.txnid, rsp.dbid);
}

void
HnfCoherencyController::storeWriteData(uint32_t entryId)
{
    Entry& entry = entries[entryId];
    slcsfUnit->writeLine(entry.blockAddr, entry.req.srcid, entry.data);
    DPRINTF(HnfCC,
            "CC entry=%u stores write data addr=%#llx src=%u txn=%u\n",
            entryId, static_cast<unsigned long long>(entry.blockAddr),
            entry.req.srcid, entry.req.txnid);
}

const HnfCcTxReq&
HnfCoherencyController::frontTxReq() const
{
    panic_if(txReqQ.empty(), "HnfCC frontTxReq on empty queue\n");
    return txReqQ.front();
}

void
HnfCoherencyController::popTxReq()
{
    panic_if(txReqQ.empty(), "HnfCC popTxReq on empty queue\n");
    txReqQ.pop_front();
}

void
HnfCoherencyController::notifyTxReqSent(uint32_t entryId)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid sent TXREQ entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];
    panic_if(!entryAllocated(entry),
             "HnfCC TXREQ sent for unallocated entry=%u\n", entryId);

    entry.mcReadIssued = true;
    DPRINTF(HnfCC,
            "CC entry=%u TXREQ ReadNoSnp accepted by link fakeData=%u\n",
            entryId, directSnFakeData);

    if (!directSnFakeData) {
        entry.state = HnfCcEntryState::IssueMcRead;
        entry.mcDataBytes = 0;
        return;
    }

    entry.data.assign(blockSize, 0);
    entry.slcUpdatePending = true;
    PocqEvent mcDataDone{};
    mcDataDone.kind = PocqEventKind::McDataDone;
    mcDataDone.txn = entry.txnKind;
    mcDataDone.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, mcDataDone);
    panic_if(retire, "HnfCC entry=%u retired during fake SN data path\n",
             entryId);
    DPRINTF(HnfCC,
            "CC entry=%u generated fake SN data addr=%#llx requester=%u "
            "txn=%u\n",
            entryId, static_cast<unsigned long long>(entry.blockAddr),
            entry.req.srcid, entry.req.txnid);
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::acceptRxDat(const RawDat& dat)
{
    const auto decoded = decodeDat(dat.opcode);
    if (decoded.major == DatMajor::WriteData) {
        std::optional<uint32_t> entryId = findTxn(dat.srcid, dat.txnid);
        panic_if(!entryId,
                 "HnfCC write RXDAT for unknown src=%u txn=%u dbid=%u\n",
                 dat.srcid, dat.txnid, dat.dbid);

        Entry& entry = entries[*entryId];
        panic_if(entry.state != HnfCcEntryState::WaitWriteData,
                 "HnfCC write RXDAT entry=%u state=%u not waiting data\n",
                 *entryId, static_cast<unsigned>(entry.state));

        const uint32_t expected = expectedDataBytes(entry.req);
        const uint32_t lineBytes = blockSize;
        if (entry.data.size() < lineBytes) {
            entry.data.resize(lineBytes, 0);
        }

        const uint32_t offset = dat.beatOffset;
        panic_if(offset > lineBytes,
                 "HnfCC write RXDAT entry=%u offset=%u lineBytes=%u\n",
                 *entryId, offset, lineBytes);

        const uint32_t copyBytes =
            std::min<uint32_t>(dat.data.size(), lineBytes - offset);
        std::copy(dat.data.begin(), dat.data.begin() + copyBytes,
                  entry.data.begin() + offset);
        entry.writeDataBytes =
            std::min<uint32_t>(expected, entry.writeDataBytes + copyBytes);

        DPRINTF(HnfCC,
                "CC entry=%u got write data src=%u txn=%u dbid=%u "
                "offset=%u bytes=%u received=%u/%u last=%u\n",
                *entryId, dat.srcid, dat.txnid, dat.dbid, offset, copyBytes,
                entry.writeDataBytes, expected, dat.last);

        if (!dat.last && entry.writeDataBytes < expected) {
            return std::nullopt;
        }

        PocqEvent writeDone{};
        writeDone.kind = PocqEventKind::WriteDataDone;
        writeDone.txn = entry.txnKind;
        return stepPocq(*entryId, writeDone);
    }

    panic_if(decoded.minor != DatMinor::CompData &&
                 decoded.minor != DatMinor::DataSepResp &&
                 decoded.minor != DatMinor::NCBWrDataCompAck,
             "HnfCC real-SN RXDAT unsupported opcode=0x%x src=%u txn=%u\n",
             dat.opcode, dat.srcid, dat.txnid);

    panic_if(dat.txnid == 0 || dat.txnid > entries.size(),
             "HnfCC real-SN RXDAT bad txnid=%u src=%u\n",
             dat.txnid, dat.srcid);

    const uint32_t entryId = dat.txnid - 1;
    Entry& entry = entries[entryId];
    panic_if(!entryAllocated(entry),
             "HnfCC real-SN RXDAT for idle entry=%u src=%u txn=%u\n",
             entryId, dat.srcid, dat.txnid);
    panic_if(entry.state != HnfCcEntryState::IssueMcRead,
             "HnfCC real-SN RXDAT entry=%u state=%u not waiting for SN data\n",
             entryId, static_cast<unsigned>(entry.state));

    const uint32_t expected = expectedDataBytes(entry.req);
    if (entry.data.size() < expected) {
        entry.data.resize(expected, 0);
    }

    const uint32_t offset = dat.beatOffset;
    panic_if(offset > expected,
             "HnfCC real-SN RXDAT entry=%u offset=%u expected=%u\n",
             entryId, offset, expected);

    const uint32_t copyBytes =
        std::min<uint32_t>(dat.data.size(), expected - offset);
    std::copy(dat.data.begin(), dat.data.begin() + copyBytes,
              entry.data.begin() + offset);
    entry.mcDataBytes = std::min<uint32_t>(expected,
                                           entry.mcDataBytes + copyBytes);

    DPRINTF(HnfCC,
            "CC entry=%u got real SN CompData src=%u txn=%u dataid=%u "
            "offset=%u bytes=%u received=%u/%u last=%u\n",
            entryId, dat.srcid, dat.txnid, dat.dataid, offset, copyBytes,
            entry.mcDataBytes, expected, dat.last);

    if (!dat.last && entry.mcDataBytes < expected) {
        return std::nullopt;
    }

    entry.slcUpdatePending = true;
    PocqEvent mcDataDone{};
    mcDataDone.kind = PocqEventKind::McDataDone;
    mcDataDone.txn = entry.txnKind;
    mcDataDone.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, mcDataDone);
    panic_if(retire, "HnfCC entry=%u retired during real SN data path\n",
             entryId);
    DPRINTF(HnfCC,
            "CC entry=%u completed real SN read addr=%#llx requester=%u "
            "txn=%u\n",
            entryId, static_cast<unsigned long long>(entry.blockAddr),
            entry.req.srcid, entry.req.txnid);
    return std::nullopt;
}

const HnfCcTxDat&
HnfCoherencyController::frontTxDat() const
{
    panic_if(txDatQ.empty(), "HnfCC frontTxDat on empty queue\n");
    return txDatQ.front();
}

void
HnfCoherencyController::popTxDat()
{
    panic_if(txDatQ.empty(), "HnfCC popTxDat on empty queue\n");
    txDatQ.pop_front();
}

const HnfCcTxRsp&
HnfCoherencyController::frontTxRsp() const
{
    panic_if(txRspQ.empty(), "HnfCC frontTxRsp on empty queue\n");
    return txRspQ.front();
}

void
HnfCoherencyController::popTxRsp()
{
    panic_if(txRspQ.empty(), "HnfCC popTxRsp on empty queue\n");
    txRspQ.pop_front();
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::acceptRxRsp(const RawRsp& rsp)
{
    const auto decoded = decodeRsp(rsp.opcode);
    panic_if(decoded.minor != RspMinor::CompAck,
             "HnfCC v1 only accepts CompAck on RXRSP, got opcode=0x%x "
             "src=%u txn=%u\n",
             rsp.opcode, rsp.srcid, rsp.txnid);

    std::optional<uint32_t> entryId = findTxn(rsp.srcid, rsp.txnid);
    panic_if(!entryId, "HnfCC CompAck for unknown src=%u txn=%u\n",
             rsp.srcid, rsp.txnid);

    Entry& entry = entries[*entryId];
    panic_if(entry.state != HnfCcEntryState::WaitCompAck,
             "HnfCC CompAck entry=%u state=%u is not waiting CompAck\n",
             *entryId, static_cast<unsigned>(entry.state));

    DPRINTF(HnfCC,
            "CC entry=%u got CompAck src=%u txn=%u retire\n",
            *entryId, rsp.srcid, rsp.txnid);
    PocqEvent compAck{};
    compAck.kind = PocqEventKind::CompAck;
    return stepPocq(*entryId, compAck);
}

HnfCcRetireInfo
HnfCoherencyController::makeRetireInfo(const Entry& entry) const
{
    HnfCcRetireInfo info{};
    info.valid = true;
    info.tokenId = entry.tokenId;
    info.resourceClass = entry.resourceClass;
    info.reqPriority = entry.priority;
    info.srcid = entry.req.srcid;
    info.pcrdtype = entry.req.pcrdtype;
    return info;
}

HnfCcRetireInfo
HnfCoherencyController::retireEntry(uint32_t entryId)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid retire entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];
    HnfCcRetireInfo info = makeRetireInfo(entry);
    const uint64_t addr = entry.blockAddr;
    entry = Entry{};
    wakeSleepingEntries(addr);
    return info;
}

void
HnfCoherencyController::wakeSleepingEntries(uint64_t addr)
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& entry = entries[i];
        if (entry.state != HnfCcEntryState::Sleep || entry.blockAddr != addr) {
            continue;
        }
        if (hasAddressHazard(i, addr)) {
            continue;
        }
        DPRINTF(HnfCC, "CC wakes sleeping entry=%u addr=%#llx\n",
                i, static_cast<unsigned long long>(addr));
        entry.sleepingOn.reset();
        startReadFlow(i);
        return;
    }
}

bool
HnfCoherencyController::hasWork() const
{
    return hasTxWork();
}

} // namespace gem5::Chi

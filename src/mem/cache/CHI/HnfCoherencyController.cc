#include "mem/cache/CHI/HnfCoherencyController.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HnfCC.hh"
#include "debug/HnfDirtyVictimE2E.hh"
#include "mem/cache/CHI/Chi2ClassicMemTxnPolicy.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint32_t McTxnIdLimit = 0x40000000U;
constexpr uint32_t DirtyVictimTxnIdBase = McTxnIdLimit;
constexpr uint32_t DirtyVictimTxnIdLimit = 0x80000000U;
constexpr uint32_t SnoopTxnIdBase = DirtyVictimTxnIdLimit;

namespace ReqOp
{
constexpr uint8_t ReadNoSnp = 0x04;
constexpr uint8_t WriteUniquePtl = 0x58;
constexpr uint8_t WriteNoSnpFull = 0x5c;
} // namespace ReqOp

namespace SnpOp
{
constexpr uint8_t CleanInvalid = 0x09;
} // namespace SnpOp

namespace DatOp
{
constexpr uint8_t SnpRespData = 0x01;
constexpr uint8_t CopyBackWriteData = 0x02;
constexpr uint8_t CompData = 0x04;
constexpr uint8_t NonCopyBackWriteData = 0x03;
} // namespace DatOp

namespace RspOp
{
constexpr uint8_t Comp = 0x04;
constexpr uint8_t CompDBIDResp = 0x05;
} // namespace RspOp

constexpr uint8_t RespSC = 1;
constexpr uint8_t RespUC = 2;
constexpr uint8_t RespUDPD = 3;

bool
isDirtyDataResponse(uint8_t response)
{
    // UD_PD, SD_PD, and I_PD all transfer dirty data ownership.
    return response == 3 || response == 4 || response == 5;
}

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

uint64_t
requesterTraceKey(uint32_t srcid, uint32_t txnid)
{
    return (static_cast<uint64_t>(srcid) << 32) | txnid;
}

} // anonymous namespace

HnfCoherencyController::HnfCoherencyController(
    uint32_t block_size, uint32_t data_beat_bytes, uint32_t num_entries,
    uint32_t sn_node_id, const std::vector<uint32_t>& sn_node_ids,
    bool direct_sn_fake_data, uint32_t rnf_slices, bool enable_retry)
    : blockSize(block_size),
      dataBeatBytes(data_beat_bytes),
      maxEntries(num_entries),
      snNodeId(sn_node_id),
      snNodeIds(sn_node_ids),
      snInterleaveShift(block_size ? __builtin_ctz(block_size) : 6),
      directSnFakeData(direct_sn_fake_data),
      dirtyVictimRetryEnabled(enable_retry),
      rnfSlices(rnf_slices),
      entries(num_entries)
{
    fatal_if(blockSize == 0 || (blockSize & (blockSize - 1)) != 0,
             "HnfCC block_size must be a non-zero power of two\n");
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "HnfCC data_beat_bytes must satisfy 0 < beat <= block\n");
    fatal_if(1 + (blockSize - 1) / dataBeatBytes > UINT8_MAX + 1,
             "HnfCC block requires more than the 8-bit DataID space\n");
    fatal_if(maxEntries == 0, "HnfCC entry count must be non-zero\n");
    fatal_if(maxEntries > UINT8_MAX,
             "HnfCC entry count=%u exceeds nonzero 8-bit DBID space\n",
             maxEntries);
    fatal_if(rnfSlices == 0 || rnfSlices > 4 ||
                 (rnfSlices & (rnfSlices - 1)) != 0,
             "HnfCC rnf_slices must be a power of two in [1, 4]\n");
    fatal_if(!snNodeIds.empty() &&
                 (snNodeIds.size() & (snNodeIds.size() - 1)) != 0,
             "HnfCC sn_node_ids count must be a power of two\n");
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
        panic_if(entry.slcLookupPhase != SlcLookupPhase::None,
                 "HnfCC entry=%u starts lookup with phase=%u\n", entryId,
                 static_cast<unsigned>(entry.slcLookupPhase));

        SlcSfReqHeader header = makeSlcSfReqHeader(
            slcSfReqIds, entryId, entry.blockAddr, entry.req, entry.seq,
            entry.acceptCycle);
        entry.slcLookupReqId = header.reqId;
        entry.pendingSlcLookup = SlcSfRequest(
            makeSlcSfLookupReq(std::move(header), entry.txnKind));
        entry.slcLookupPhase = SlcLookupPhase::IssuePending;
        tryIssueSlcLookup(entryId);
        return std::nullopt;
      }

      case PocqActionKind::QueueSnoops:
        queueSnoops(entryId);
        return std::nullopt;

      case PocqActionKind::CommitRead:
        startSlcUpdate(entryId);
        return std::nullopt;

      case PocqActionKind::CommitMaintenance:
        completeMaintenance(entryId);
        return std::nullopt;

      case PocqActionKind::RemoveSharer:
        removeSharer(entryId);
        return std::nullopt;

      case PocqActionKind::UpdateSlcSf:
        startSlcUpdate(entryId);
        return std::nullopt;

      case PocqActionKind::QueueTxReq:
        queueMcRead(entryId);
        return std::nullopt;

      case PocqActionKind::QueueCompData:
        queueCompData(entryId, entry.readData);
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
      case PocqActionKind::FlushL3:
      case PocqActionKind::WriteL3FlushSf:
        panic("HnfCC flush action=%u has no CHI protocol transition; "
              "use the typed asynchronous SLCSF service request\n",
              static_cast<unsigned>(action));

      case PocqActionKind::Retire:
        return retireEntry(entryId);

      case PocqActionKind::SleepForReplay:
        entry.state = HnfCcEntryState::Sleep;
        entry.slcsfReplay = true;
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

void
HnfCoherencyController::resetDataAssembly(
    DataAssembly& assembly, uint32_t start_offset, uint32_t expected_bytes)
{
    panic_if(expected_bytes == 0 || start_offset > blockSize ||
                 expected_bytes > blockSize - start_offset,
             "HnfCC invalid DAT assembly range offset=%u bytes=%u block=%u\n",
             start_offset, expected_bytes, blockSize);
    assembly = DataAssembly{};
    assembly.active = true;
    assembly.startOffset = start_offset;
    assembly.expectedBytes = expected_bytes;
    assembly.coverage.assign(blockSize, 0);
    assembly.seenDataIds.assign(UINT8_MAX + 1, 0);
}

bool
HnfCoherencyController::acceptDataBeat(
    DataAssembly& assembly, std::vector<uint8_t>& buffer,
    const RawDat& dat, const char* owner, uint64_t owner_id)
{
    panic_if(!assembly.active || assembly.complete,
             "HnfCC %s=%llu receives DAT without an active assembly\n",
             owner, static_cast<unsigned long long>(owner_id));
    panic_if(dat.data.empty(),
             "HnfCC %s=%llu receives an empty DAT beat\n", owner,
             static_cast<unsigned long long>(owner_id));
    const uint32_t beat_count =
        1 + (assembly.expectedBytes - 1) / dataBeatBytes;
    panic_if(beat_count == 0 || beat_count > UINT8_MAX + 1 ||
                 dat.dataid >= beat_count ||
                 assembly.seenDataIds[dat.dataid],
             "HnfCC %s=%llu DAT DataID=%u beats=%u duplicate=%u\n",
             owner, static_cast<unsigned long long>(owner_id), dat.dataid,
             beat_count, assembly.seenDataIds[dat.dataid]);

    const uint32_t relative_offset = dat.dataid * dataBeatBytes;
    const uint32_t expected_offset =
        assembly.startOffset + relative_offset;
    const uint32_t expected_beat_bytes = std::min(
        dataBeatBytes, assembly.expectedBytes - relative_offset);
    panic_if(dat.beatOffset != expected_offset ||
                 dat.beatOffset > blockSize ||
                 dat.data.size() != expected_beat_bytes ||
                 dat.data.size() > blockSize - dat.beatOffset,
             "HnfCC %s=%llu DAT range offset=%u bytes=%u expectedOffset=%u "
             "expectedBeat=%u block=%u\n",
             owner, static_cast<unsigned long long>(owner_id),
             dat.beatOffset, static_cast<unsigned>(dat.data.size()),
             expected_offset, expected_beat_bytes, blockSize);
    panic_if(dat.byteEnable.size() != dat.data.size() ||
                 dat.chunkValid.size() != (dat.data.size() + 7) / 8,
             "HnfCC %s=%llu DAT mask geometry bytes=%u be=%u chunks=%u\n",
             owner, static_cast<unsigned long long>(owner_id),
             static_cast<unsigned>(dat.data.size()),
             static_cast<unsigned>(dat.byteEnable.size()),
             static_cast<unsigned>(dat.chunkValid.size()));
    panic_if(std::any_of(dat.byteEnable.begin(), dat.byteEnable.end(),
                         [](uint8_t byte) { return byte != 1; }) ||
                 std::any_of(dat.chunkValid.begin(), dat.chunkValid.end(),
                             [](uint8_t chunk) { return chunk != 1; }),
             "HnfCC %s=%llu DAT has disabled required bytes/chunks\n", owner,
             static_cast<unsigned long long>(owner_id));
    if (assembly.response) {
        panic_if(*assembly.response != dat.resp,
                 "HnfCC %s=%llu DAT response changed %u->%u\n", owner,
                 static_cast<unsigned long long>(owner_id),
                 *assembly.response, dat.resp);
    }
    for (uint32_t i = 0; i < dat.data.size(); ++i) {
        panic_if(assembly.coverage[dat.beatOffset + i],
                 "HnfCC %s=%llu DAT overlaps byte=%u\n", owner,
                 static_cast<unsigned long long>(owner_id),
                 dat.beatOffset + i);
    }

    const uint32_t covered_after =
        assembly.coveredBytes + dat.data.size();
    const bool terminal_beat = dat.dataid + 1 == beat_count;
    panic_if(static_cast<bool>(dat.last) != terminal_beat,
             "HnfCC %s=%llu DAT last=%u disagrees with DataID=%u, "
             "terminal=%u\n",
             owner, static_cast<unsigned long long>(owner_id), dat.last,
             dat.dataid, beat_count - 1);
    const bool saw_last_after = assembly.sawLast || dat.last;
    const bool complete = saw_last_after &&
        covered_after == assembly.expectedBytes;

    if (buffer.size() < blockSize) {
        buffer.resize(blockSize, 0);
    }
    std::copy(dat.data.begin(), dat.data.end(),
              buffer.begin() + dat.beatOffset);
    std::fill(assembly.coverage.begin() + dat.beatOffset,
              assembly.coverage.begin() + dat.beatOffset + dat.data.size(), 1);
    assembly.coveredBytes = covered_after;
    assembly.seenDataIds[dat.dataid] = 1;
    assembly.sawLast = saw_last_after;
    if (!assembly.response) {
        assembly.response = dat.resp;
    }
    assembly.complete = complete;
    return complete;
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
        if (i == entry || !entryAllocated(entries[i]) ||
            entries[i].state == HnfCcEntryState::Sleep) {
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

    const uint32_t data_bytes = expectedDataBytes(in.req);
    panic_if(data_bytes > blockSize,
             "HnfCC request crosses modeled line src=%u txn=%u addr=%#llx "
             "bytes=%u block=%u\n",
             in.req.srcid, in.req.txnid,
             static_cast<unsigned long long>(in.req.addr), data_bytes,
             blockSize);
    panic_if(in.req.opcode == ReqOp::WriteUniquePtl,
             "HnfCC WriteUniquePtl is disabled until a coherent masked "
             "read-modify-write/no-allocate path is available src=%u txn=%u "
             "addr=%#llx bytes=%u\n",
             in.req.srcid, in.req.txnid,
             static_cast<unsigned long long>(in.req.addr), data_bytes);
    const bool coherent_read = txnKind == PocqTxnKind::ReadShared ||
        txnKind == PocqTxnKind::ReadUnique ||
        txnKind == PocqTxnKind::ReadOnce;
    const bool line_aligned = (in.req.addr & (blockSize - 1)) == 0;
    if (txnExpectsWriteData(txnKind) || coherent_read) {
        panic_if(data_bytes != blockSize || !line_aligned,
                 "HnfCC line transaction requires aligned full data src=%u "
                 "txn=%u opcode=0x%x addr=%#llx bytes=%u block=%u\n",
                 in.req.srcid, in.req.txnid, in.req.opcode,
                 static_cast<unsigned long long>(in.req.addr), data_bytes,
                 blockSize);
    }

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
    entry.acceptCycle = cycle;
    entry.blockAddr = blockAddr(in.req);
    entry.tokenId = in.tokenId;
    entry.priority = in.priority;
    entry.resourceClass = in.resourceClass;
    entry.isStatic = in.isStatic;
    entry.needsCompAck = txnNeedsCompAck(txnKind);
    entry.expectsWriteData = txnExpectsWriteData(txnKind);
    entry.readData.assign(blockSize, 0);
    if (entry.expectsWriteData) {
        entry.writePayload.assign(blockSize, 0);
        const uint32_t start_offset = static_cast<uint32_t>(
            entry.req.addr - entry.blockAddr);
        resetDataAssembly(
            entry.writeDataAssembly, start_offset,
            expectedDataBytes(entry.req));
    }

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
    // A pending SEQ victim for this line has priority over new main-path
    // work. This is a transient address hazard, not a capacity reservation;
    // sleeping here prevents the main entry from blocking the SEQ transaction
    // that must remove the hazard.
    if (slcsfUnit->seqContains(entry.blockAddr)) {
        entry.state = HnfCcEntryState::Sleep;
        entry.pocqState = PocqState::Sleep;
        entry.slcsfReplay = true;
        return;
    }
    entry.state = HnfCcEntryState::Working;
    entry.pocqState = PocqState::Idle;
    entry.slcsfReplay = false;

    PocqEvent admit{};
    admit.kind = PocqEventKind::Admit;
    admit.txn = entry.txnKind;
    admit.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, admit);
    panic_if(retire, "HnfCC entry=%u retired during admit path\n", entryId);
}

uint32_t
HnfCoherencyController::targetRouteId(uint32_t target, uint64_t addr) const
{
    const uint32_t slice =
        static_cast<uint32_t>((addr / blockSize) & (rnfSlices - 1));
    return (target & ~0x3U) | slice;
}

void
HnfCoherencyController::queueSnoops(uint32_t entryId)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid snoop entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];
    panic_if(entry.slcLookupResult.snoopTargets == 0,
             "HnfCC entry=%u queues snoop without targets\n", entryId);
    panic_if(entry.snoopTxnId != 0,
             "HnfCC entry=%u already owns snoop txn=%u\n",
             entryId, entry.snoopTxnId);

    const uint32_t snoopTxn = allocateSnoopTxnId();
    snoopTxnToEntry.emplace(snoopTxn, entryId);

    entry.state = HnfCcEntryState::WaitSnoop;
    entry.snoopTxnId = snoopTxn;
    entry.snoopPendingTargets = entry.slcLookupResult.snoopTargets;
    entry.snoopDataReceived = false;
    resetDataAssembly(entry.snoopDataAssembly, 0, blockSize);

    for (uint32_t target = 0; target < 64; ++target) {
        if ((entry.snoopPendingTargets & (1ULL << target)) == 0) {
            continue;
        }
        const uint32_t target_src = slcsfUnit->srcIdForIndex(target);

        HnfCcTxSnp out{};
        out.entry = entryId;
        out.targetNode = target_src;
        RawSnp& snp = out.snp;
        snp.qos = entry.req.qos;
        snp.srcid = entry.req.tgtid;
        snp.tgtid = targetRouteId(target_src, entry.blockAddr);
        snp.txnid = snoopTxn;
        snp.opcode = entry.slcLookupResult.snoopOpcode;
        snp.addr = entry.blockAddr;
        snp.size = static_cast<uint8_t>(blockSize);
        txSnpQ.push_back(out);

        DPRINTF(HnfCC,
                "CC entry=%u queues TXSNP opcode=0x%x txn=%u addr=%#llx "
                "targetNode=%u targetRoute=%u broadcast=%u directed=%u\n",
                entryId, snp.opcode, snp.txnid,
                static_cast<unsigned long long>(snp.addr), target, snp.tgtid,
                entry.slcLookupResult.snoopBroadcast,
                entry.slcLookupResult.snoopDirected);
    }
}

void
HnfCoherencyController::startSlcUpdate(uint32_t entryId)
{
    Entry& entry = entries[entryId];
    panic_if(entry.readData.size() < blockSize,
             "HnfCC entry=%u starts read update with %u/%u data bytes\n",
             entryId, static_cast<unsigned>(entry.readData.size()), blockSize);
    panic_if(entry.slcUpdatePhase != SlcUpdatePhase::None,
             "HnfCC entry=%u starts update with phase=%u\n", entryId,
             static_cast<unsigned>(entry.slcUpdatePhase));

    SlcSfReqHeader header = makeSlcSfReqHeader(
        slcSfReqIds, entryId, entry.blockAddr, entry.req, entry.seq,
        entry.acceptCycle);
    entry.slcUpdateReqId = header.reqId;
    SlcSfRequest request = makeSlcSfCommitReadReq(
        std::move(header), entry.txnKind, entry.readData,
        entry.responseDataDirty, entry.req.tgtid, {}, entry.slcCommitToken,
        entry.slcLookupReqId);
    entry.expectedSlcUpdateOperation = slcSfResponseOperation(request);
    entry.pendingSlcUpdate = std::move(request);
    entry.slcUpdatePhase = SlcUpdatePhase::IssuePending;
    tryIssueSlcUpdate(entryId);
}

void
HnfCoherencyController::completeMaintenance(uint32_t entryId)
{
    Entry& entry = entries[entryId];
    panic_if(entry.slcUpdatePhase != SlcUpdatePhase::None,
             "HnfCC entry=%u starts maintenance with phase=%u\n", entryId,
             static_cast<unsigned>(entry.slcUpdatePhase));

    SlcSfReqHeader header = makeSlcSfReqHeader(
        slcSfReqIds, entryId, entry.blockAddr, entry.req, entry.seq,
        entry.acceptCycle);
    entry.slcUpdateReqId = header.reqId;
    SlcSfRequest request = makeSlcSfCompleteMaintenanceReq(
        std::move(header), entry.txnKind, entry.req.tgtid,
        entry.slcCommitToken, entry.slcLookupReqId);
    entry.expectedSlcUpdateOperation = slcSfResponseOperation(request);
    entry.pendingSlcUpdate = std::move(request);
    entry.slcUpdatePhase = SlcUpdatePhase::IssuePending;
    tryIssueSlcUpdate(entryId);
}

void
HnfCoherencyController::removeSharer(uint32_t entryId)
{
    Entry& entry = entries[entryId];
    panic_if(entry.txnKind != PocqTxnKind::Evict,
             "HnfCC entry=%u starts remove-sharer for txn=%u\n", entryId,
             static_cast<unsigned>(entry.txnKind));
    panic_if(entry.slcUpdatePhase != SlcUpdatePhase::None,
             "HnfCC entry=%u starts remove-sharer with phase=%u\n", entryId,
             static_cast<unsigned>(entry.slcUpdatePhase));

    SlcSfReqHeader header = makeSlcSfReqHeader(
        slcSfReqIds, entryId, entry.blockAddr, entry.req, entry.seq,
        entry.acceptCycle);
    entry.slcUpdateReqId = header.reqId;
    SlcSfRequest request = makeSlcSfRemoveSharerReq(
        std::move(header), entry.slcCommitToken, entry.slcLookupReqId);
    entry.expectedSlcUpdateOperation = slcSfResponseOperation(request);
    entry.pendingSlcUpdate = std::move(request);
    entry.slcUpdatePhase = SlcUpdatePhase::IssuePending;
    tryIssueSlcUpdate(entryId);
}

void
HnfCoherencyController::completeSnoopTarget(uint32_t entryId,
                                            uint32_t responder,
                                            bool has_data,
                                            bool data_dirty)
{
    panic_if(entryId >= entries.size(),
             "HnfCC invalid snoop completion entry=%u\n", entryId);
    Entry& entry = entries[entryId];
    panic_if(entry.state != HnfCcEntryState::WaitSnoop,
             "HnfCC snoop completion entry=%u state=%u\n", entryId,
             static_cast<unsigned>(entry.state));
    panic_if((entry.snoopPendingTargets &
                  (1ULL << slcsfUnit->sharerIndex(responder))) == 0,
             "HnfCC snoop txn=%u unexpected responder=%u pending=%#llx\n",
             entry.snoopTxnId, responder,
             static_cast<unsigned long long>(entry.snoopPendingTargets));
    panic_if(has_data && entry.snoopDataReceived,
             "HnfCC snoop txn=%u received dirty data from multiple RNFs\n",
             entry.snoopTxnId);
    panic_if(data_dirty && !has_data,
             "HnfCC snoop txn=%u marks a no-data response dirty\n",
             entry.snoopTxnId);

    if (has_data) {
        entry.snoopDataReceived = true;
        entry.responseDataDirty = data_dirty;
    }
    entry.snoopPendingTargets &=
        ~(1ULL << slcsfUnit->sharerIndex(responder));

    DPRINTF(HnfCC,
            "CC entry=%u accepts snoop response txn=%u responder=%u "
            "hasData=%u pending=%#llx\n",
            entryId, entry.snoopTxnId, responder, has_data,
            static_cast<unsigned long long>(entry.snoopPendingTargets));

    if (entry.snoopPendingTargets != 0) {
        return;
    }

    snoopTxnToEntry.erase(entry.snoopTxnId);
    entry.snoopTxnId = 0;
    PocqEvent snoopDone{};
    snoopDone.kind = PocqEventKind::SnoopDone;
    snoopDone.txn = entry.txnKind;
    snoopDone.dataAvailable = entry.slcLookupResult.slcHit ||
        entry.snoopDataReceived;
    snoopDone.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, snoopDone);
    panic_if(retire, "HnfCC entry=%u retired during snoop completion\n",
             entryId);
}

void
HnfCoherencyController::queueMcRead(uint32_t entryId)
{
    Entry& entry = entries[entryId];

    RawReq req{};
    req.qos = entry.req.qos;
    req.srcid = entry.req.tgtid;
    req.tgtid = selectSnNode(entry.blockAddr);
    panic_if(entry.mcTxnId != 0,
             "HnfCC entry=%u allocates a second MC transaction=%u\n",
             entryId, entry.mcTxnId);
    req.txnid = allocateMcTxnId();
    req.opcode = ReqOp::ReadNoSnp;
    req.AllowRetry = 0;
    req.addr = entry.txnKind == PocqTxnKind::ReadNoSnp ?
        entry.req.addr : entry.blockAddr;
    req.size = expectedDataBytes(entry.req);
    req.ReturnNid = entry.req.tgtid;
    req.order = entry.req.order;
    req.memattr = entry.req.memattr;
    req.snpattr = 0;
    req.expCompAck = false;
    req.traceTag = entry.req.traceTag;
    req.srcType = entry.req.srcType;
    req.ldid = entry.req.ldid;

    const auto [mc_it, inserted] = mcTxnToEntry.emplace(req.txnid, entryId);
    panic_if(!inserted,
             "HnfCC failed to bind MC transaction=%u to entry=%u\n",
             req.txnid, entryId);
    entry.mcTxnId = mc_it->first;
    entry.mcReadIssued = false;
    entry.readData.assign(blockSize, 0);
    resetDataAssembly(
        entry.mcDataAssembly, 0, expectedDataBytes(entry.req));

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
        dat.resp = entry.txnKind == PocqTxnKind::ReadUnique ?
            (entry.responseDataDirty ? RespUDPD : RespUC) : RespSC;
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
    out.traceDirtyVictimRequesterDone =
        dirtyVictimRequesterTracked(entry);
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
    panic_if(rsp.dbid == 0, "HnfCC entry=%u generated reserved DBID zero\n",
             entryId);
    rsp.resp = RespSC;
    rsp.pcrdtype = entry.req.pcrdtype;
    rsp.rspKind = RspKind::MainPath;
    txRspQ.push_back(out);

    entry.state = HnfCcEntryState::WaitWriteData;
    entry.writeDbid = rsp.dbid;
    entry.writePayload.assign(blockSize, 0);
    resetDataAssembly(
        entry.writeDataAssembly,
        static_cast<uint32_t>(entry.req.addr - entry.blockAddr),
        expectedDataBytes(entry.req));
    DPRINTF(HnfCC,
            "CC entry=%u queues CompDBIDResp src=%u txn=%u dbid=%u\n",
            entryId, entry.req.srcid, entry.req.txnid, rsp.dbid);
}

void
HnfCoherencyController::storeWriteData(uint32_t entryId)
{
    Entry& entry = entries[entryId];
    panic_if(entry.slcUpdatePhase != SlcUpdatePhase::None,
             "HnfCC entry=%u starts write update with phase=%u\n", entryId,
             static_cast<unsigned>(entry.slcUpdatePhase));
    panic_if(!entry.writeDataAssembly.complete ||
                 entry.writeDataAssembly.coveredBytes != blockSize ||
                 entry.writePayload.size() != blockSize,
             "HnfCC entry=%u commits incomplete write coverage=%u/%u "
             "payload=%u\n",
             entryId, entry.writeDataAssembly.coveredBytes, blockSize,
             static_cast<unsigned>(entry.writePayload.size()));

    SlcSfReqHeader header = makeSlcSfReqHeader(
        slcSfReqIds, entryId, entry.blockAddr, entry.req, entry.seq,
        entry.acceptCycle);
    entry.slcUpdateReqId = header.reqId;
    SlcSfRequest request = makeSlcSfWriteLineReq(
        std::move(header), entry.writePayload, entry.txnKind,
        entry.req.tgtid, {}, entry.slcCommitToken, entry.slcLookupReqId);
    entry.expectedSlcUpdateOperation = slcSfResponseOperation(request);
    entry.pendingSlcUpdate = std::move(request);
    entry.slcUpdatePhase = SlcUpdatePhase::IssuePending;
    tryIssueSlcUpdate(entryId);
    DPRINTF(HnfCC,
            "CC entry=%u queues write data update addr=%#llx src=%u txn=%u\n",
            entryId, static_cast<unsigned long long>(entry.blockAddr),
            entry.req.srcid, entry.req.txnid);
}

void
HnfCoherencyController::deferRetire(
    std::optional<HnfCcRetireInfo> retire)
{
    if (retire) {
        deferredRetireQ.push_back(*retire);
    }
}

HnfCcRetireInfo
HnfCoherencyController::popDeferredRetire()
{
    panic_if(deferredRetireQ.empty(),
             "HnfCC popDeferredRetire on empty queue\n");
    HnfCcRetireInfo retire = deferredRetireQ.front();
    deferredRetireQ.pop_front();
    return retire;
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

const HnfCcTxSnp&
HnfCoherencyController::frontTxSnp() const
{
    panic_if(txSnpQ.empty(), "HnfCC frontTxSnp on empty queue\n");
    return txSnpQ.front();
}

void
HnfCoherencyController::popTxSnp()
{
    panic_if(txSnpQ.empty(), "HnfCC popTxSnp on empty queue\n");
    txSnpQ.pop_front();
}

void
HnfCoherencyController::notifyTxReqSent(const HnfCcTxReq& request)
{
    if (request.dirtyVictimId) {
        auto transaction = dirtyVictimTxns.find(*request.dirtyVictimId);
        panic_if(transaction == dirtyVictimTxns.end() ||
                     transaction->second.downstreamTxnId !=
                         request.req.txnid ||
                     transaction->second.phase !=
                         DirtyVictimPhase::ReqQueued ||
                     !transaction->second.requestQueued ||
                     transaction->second.requestSent ||
                     request.req.srcid != transaction->second.homeNodeId ||
                     request.req.tgtid != selectSnNode(request.req.addr) ||
                     static_cast<bool>(request.req.AllowRetry) !=
                         transaction->second.activeAllowRetry ||
                     request.req.pcrdtype !=
                         transaction->second.activePcrdtype,
                 "HnfCC invalid sent dirty-victim request id=%llu txn=%u\n",
                 static_cast<unsigned long long>(*request.dirtyVictimId),
                 request.req.txnid);
        transaction->second.requestQueued = false;
        transaction->second.requestSent = true;
        transaction->second.phase = DirtyVictimPhase::WaitDbid;
        return;
    }

    const uint32_t entryId = request.entry;
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
        entry.readData.assign(blockSize, 0);
        resetDataAssembly(
            entry.mcDataAssembly, 0, expectedDataBytes(entry.req));
        return;
    }

    entry.readData.assign(blockSize, 0);
    entry.responseDataDirty = false;
    panic_if(entry.mcTxnId == 0 || mcTxnToEntry.erase(entry.mcTxnId) != 1,
             "HnfCC entry=%u fake SN completion lost MC transaction=%u\n",
             entryId, entry.mcTxnId);
    entry.mcTxnId = 0;
    entry.mcDataAssembly = DataAssembly{};
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

void
HnfCoherencyController::notifyTxReqSent(uint32_t entryId)
{
    HnfCcTxReq request{};
    request.entry = entryId;
    notifyTxReqSent(request);
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::acceptRxDat(const RawDat& dat)
{
    const auto decoded = decodeDat(dat.opcode);
    if (decoded.major == DatMajor::SnpRespData) {
        panic_if(dat.opcode != DatOp::SnpRespData,
                 "HnfCC unsupported partial/forwarded snoop DAT opcode=0x%x "
                 "src=%u txn=%u\n",
                 dat.opcode, dat.srcid, dat.txnid);
        if (seqPocqEntry.valid &&
            seqPocqEntry.snoopTxnId == dat.txnid) {
            panic_if(seqPocqEntry.state != SeqPocqState::WaitSnoop ||
                         (seqPocqEntry.pendingTargets &
                          (1ULL << slcsfUnit->sharerIndex(dat.srcid))) == 0 ||
                         (seqPocqEntry.dataAssembly.source &&
                          *seqPocqEntry.dataAssembly.source != dat.srcid) ||
                         seqPocqEntry.dataReceived ||
                         dat.tgtid != seqPocqEntry.homeNodeId ||
                         dat.HomeNID != seqPocqEntry.homeNodeId ||
                         dat.dbid != 0 || dat.qos != 0,
                     "HnfCC SEQ RXDAT identity mismatch id=%llu src=%u "
                     "tgt=%u home=%u txn=%u dbid=%u pending=%#llx\n",
                     static_cast<unsigned long long>(seqPocqEntry.seqId),
                     dat.srcid, dat.tgtid, dat.HomeNID, dat.txnid, dat.dbid,
                     static_cast<unsigned long long>(
                         seqPocqEntry.pendingTargets));
            const bool complete = acceptDataBeat(
                seqPocqEntry.dataAssembly, seqPocqEntry.data, dat,
                "SEQ", seqPocqEntry.seqId);
            if (!seqPocqEntry.dataAssembly.source) {
                seqPocqEntry.dataAssembly.source = dat.srcid;
            }
            DPRINTF(HnfCC,
                    "SEQ POCQ id=%llu got SnpRespData src=%u txn=%u "
                    "offset=%u bytes=%u last=%u\n",
                    static_cast<unsigned long long>(seqPocqEntry.seqId),
                    dat.srcid, dat.txnid, dat.beatOffset,
                    static_cast<unsigned>(dat.data.size()),
                    dat.last);
            if (complete) {
                completeSeqSnoopTarget(
                    dat.srcid, true, isDirtyDataResponse(dat.resp));
            }
            return std::nullopt;
        }

        auto snoopIt = snoopTxnToEntry.find(dat.txnid);
        panic_if(snoopIt == snoopTxnToEntry.end(),
                 "HnfCC snoop RXDAT for unknown src=%u txn=%u\n",
                 dat.srcid, dat.txnid);
        Entry& entry = entries[snoopIt->second];
        panic_if(entry.state != HnfCcEntryState::WaitSnoop ||
                     (entry.snoopPendingTargets &
                      (1ULL << slcsfUnit->sharerIndex(dat.srcid))) == 0 ||
                     (entry.snoopDataAssembly.source &&
                      *entry.snoopDataAssembly.source != dat.srcid) ||
                     entry.snoopDataReceived ||
                     dat.tgtid != entry.req.tgtid ||
                     dat.HomeNID != entry.req.tgtid || dat.dbid != 0 ||
                     dat.qos != entry.req.qos,
                 "HnfCC snoop RXDAT identity mismatch entry=%u src=%u "
                 "tgt=%u home=%u txn=%u dbid=%u pending=%#llx\n",
                 snoopIt->second, dat.srcid, dat.tgtid, dat.HomeNID,
                 dat.txnid, dat.dbid,
                 static_cast<unsigned long long>(entry.snoopPendingTargets));
        const bool complete = acceptDataBeat(
            entry.snoopDataAssembly, entry.readData, dat,
            "snoop entry", snoopIt->second);
        if (!entry.snoopDataAssembly.source) {
            entry.snoopDataAssembly.source = dat.srcid;
        }
        DPRINTF(HnfCC,
                "CC entry=%u got SnpRespData src=%u txn=%u offset=%u "
                "bytes=%u last=%u resp=%u\n",
                snoopIt->second, dat.srcid, dat.txnid, dat.beatOffset,
                static_cast<unsigned>(dat.data.size()), dat.last, dat.resp);
        if (complete) {
            completeSnoopTarget(
                snoopIt->second, dat.srcid, true,
                isDirtyDataResponse(dat.resp));
        }
        return std::nullopt;
    }

    if (decoded.major == DatMajor::WriteData) {
        std::optional<uint32_t> entryId = findTxn(dat.srcid, dat.txnid);
        panic_if(!entryId,
                 "HnfCC write RXDAT for unknown src=%u txn=%u dbid=%u\n",
                 dat.srcid, dat.txnid, dat.dbid);

        Entry& entry = entries[*entryId];
        panic_if(entry.state != HnfCcEntryState::WaitWriteData,
                 "HnfCC write RXDAT entry=%u state=%u not waiting data\n",
                 *entryId, static_cast<unsigned>(entry.state));
        const uint8_t expected_opcode =
            entry.txnKind == PocqTxnKind::WriteUnique ?
                DatOp::NonCopyBackWriteData : DatOp::CopyBackWriteData;
        panic_if(dat.opcode != expected_opcode ||
                     dat.tgtid != entry.req.tgtid ||
                     dat.HomeNID != entry.req.tgtid ||
                     dat.dbid != entry.writeDbid ||
                     dat.qos != entry.req.qos,
                 "HnfCC write RXDAT identity mismatch entry=%u opcode=0x%x/"
                 "0x%x src=%u tgt=%u/%u home=%u dbid=%u/%u txn=%u\n",
                 *entryId, dat.opcode, expected_opcode, dat.srcid,
                 dat.tgtid, entry.req.tgtid, dat.HomeNID, dat.dbid,
                 entry.writeDbid, dat.txnid);

        const bool complete = acceptDataBeat(
            entry.writeDataAssembly, entry.writePayload, dat,
            "write entry", *entryId);

        DPRINTF(HnfCC,
                "CC entry=%u got write data src=%u txn=%u dbid=%u "
                "offset=%u bytes=%u received=%u/%u last=%u\n",
                *entryId, dat.srcid, dat.txnid, dat.dbid, dat.beatOffset,
                static_cast<unsigned>(dat.data.size()),
                entry.writeDataAssembly.coveredBytes,
                entry.writeDataAssembly.expectedBytes, dat.last);

        if (!complete) {
            return std::nullopt;
        }

        PocqEvent writeDone{};
        writeDone.kind = PocqEventKind::WriteDataDone;
        writeDone.txn = entry.txnKind;
        return stepPocq(*entryId, writeDone);
    }

    panic_if(decoded.minor != DatMinor::CompData &&
                 decoded.minor != DatMinor::DataSepResp,
             "HnfCC real-SN RXDAT unsupported opcode=0x%x src=%u txn=%u\n",
             dat.opcode, dat.srcid, dat.txnid);

    const auto mc_txn = mcTxnToEntry.find(dat.txnid);
    panic_if(mc_txn == mcTxnToEntry.end(),
             "HnfCC real-SN RXDAT for unknown/stale txnid=%u src=%u\n",
             dat.txnid, dat.srcid);

    const uint32_t entryId = mc_txn->second;
    panic_if(entryId >= entries.size(),
             "HnfCC MC transaction=%u maps invalid entry=%u\n",
             dat.txnid, entryId);
    Entry& entry = entries[entryId];
    panic_if(!entryAllocated(entry),
             "HnfCC real-SN RXDAT for idle entry=%u src=%u txn=%u\n",
             entryId, dat.srcid, dat.txnid);
    panic_if(entry.state != HnfCcEntryState::IssueMcRead,
             "HnfCC real-SN RXDAT entry=%u state=%u not waiting for SN data\n",
             entryId, static_cast<unsigned>(entry.state));
    const uint32_t expected_sn = selectSnNode(entry.blockAddr);
    panic_if(!entry.mcReadIssued || entry.mcTxnId != dat.txnid ||
                 dat.srcid != expected_sn || dat.tgtid != entry.req.tgtid ||
                 dat.HomeNID != entry.req.tgtid || dat.dbid != 0 ||
                 dat.qos != entry.req.qos,
             "HnfCC real-SN RXDAT identity mismatch entry=%u src=%u/%u "
             "tgt=%u/%u home=%u txn=%u/%u dbid=%u\n",
             entryId, dat.srcid, expected_sn, dat.tgtid, entry.req.tgtid,
             dat.HomeNID, dat.txnid, entry.mcTxnId, dat.dbid);

    const bool complete = acceptDataBeat(
        entry.mcDataAssembly, entry.readData, dat, "MC entry", entryId);

    DPRINTF(HnfCC,
            "CC entry=%u got real SN CompData src=%u txn=%u dataid=%u "
            "offset=%u bytes=%u received=%u/%u last=%u\n",
            entryId, dat.srcid, dat.txnid, dat.dataid, dat.beatOffset,
            static_cast<unsigned>(dat.data.size()),
            entry.mcDataAssembly.coveredBytes,
            entry.mcDataAssembly.expectedBytes, dat.last);

    if (!complete) {
        return std::nullopt;
    }

    panic_if(mcTxnToEntry.erase(dat.txnid) != 1,
             "HnfCC entry=%u lost completed MC transaction=%u\n",
             entryId, dat.txnid);
    entry.mcTxnId = 0;
    entry.mcDataAssembly = DataAssembly{};
    entry.responseDataDirty = false;
    const uint64_t completedAddr = entry.blockAddr;
    const uint32_t requester = entry.req.srcid;
    const uint32_t requesterTxn = entry.req.txnid;
    PocqEvent mcDataDone{};
    mcDataDone.kind = PocqEventKind::McDataDone;
    mcDataDone.txn = entry.txnKind;
    mcDataDone.needsCompAck = entry.needsCompAck;
    std::optional<HnfCcRetireInfo> retire = stepPocq(entryId, mcDataDone);
    DPRINTF(HnfCC,
            "CC entry=%u completed real SN read addr=%#llx requester=%u "
            "txn=%u\n",
            entryId, static_cast<unsigned long long>(completedAddr),
            requester, requesterTxn);
    return retire;
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
    const auto victim_id = txDatQ.front().dirtyVictimId;
    if (victim_id) {
        auto transaction = dirtyVictimTxns.find(*victim_id);
        panic_if(transaction == dirtyVictimTxns.end() ||
                     transaction->second.phase !=
                         DirtyVictimPhase::DataQueued ||
                     !transaction->second.dataQueued ||
                     transaction->second.dataSent ||
                     transaction->second.dbid == 0,
                 "HnfCC sends data for unknown/completed dirty victim=%llu\n",
                 static_cast<unsigned long long>(*victim_id));
        transaction->second.dataQueued = false;
        transaction->second.dataSent = true;
        if (transaction->second.completionSeen) {
            retireDirtyVictimWriteback(transaction->second);
        } else {
            transaction->second.phase = DirtyVictimPhase::WaitComp;
        }
    }
    txDatQ.pop_front();
}

const HnfCcTxRsp&
HnfCoherencyController::frontTxRsp() const
{
    panic_if(txRspQ.empty(), "HnfCC frontTxRsp on empty queue\n");
    return txRspQ.front();
}

std::optional<HnfCcRetireInfo>
HnfCoherencyController::acceptRxRsp(const RawRsp& rsp)
{
    const auto decoded = decodeRsp(rsp.opcode);
    const bool dirtyVictimRsp =
        decoded.minor == RspMinor::RetryAck ||
        decoded.minor == RspMinor::PCrdGrant ||
        decoded.minor == RspMinor::DBIDResp ||
        decoded.minor == RspMinor::DBIDRespOrd ||
        decoded.minor == RspMinor::CompDBIDResp ||
        decoded.minor == RspMinor::Comp;
    if (dirtyVictimRsp) {
        auto txn_id = dirtyVictimTxnIds.find(rsp.txnid);
        if (txn_id == dirtyVictimTxnIds.end()) {
            DPRINTF(HnfCC,
                    "ignore unmatched dirty-victim RSP opcode=0x%x "
                    "src=%u tgt=%u txn=%u\n",
                    rsp.opcode, rsp.srcid, rsp.tgtid, rsp.txnid);
            return std::nullopt;
        }
        auto transaction = dirtyVictimTxns.find(txn_id->second);
        panic_if(transaction == dirtyVictimTxns.end(),
                 "HnfCC dirty-victim txn=%u lost victim=%llu\n", rsp.txnid,
                 static_cast<unsigned long long>(txn_id->second));
        DirtyVictimTxn& victim = transaction->second;
        if (!dirtyVictimResponseMatches(victim, rsp)) {
            DPRINTF(HnfCC,
                    "ignore mismatched dirty-victim RSP victim=%llu "
                    "opcode=0x%x src=%u tgt=%u txn=%u phase=%u\n",
                    static_cast<unsigned long long>(txn_id->second),
                    rsp.opcode, rsp.srcid, rsp.tgtid, rsp.txnid,
                    static_cast<unsigned>(victim.phase));
            return std::nullopt;
        }

        if (decoded.minor == RspMinor::RetryAck) {
            if (victim.phase != DirtyVictimPhase::WaitDbid ||
                !victim.requestSent || !victim.activeAllowRetry ||
                rsp.respErr != 0) {
                return std::nullopt;
            }
            victim.requestSent = false;
            victim.retryPcrdtype = rsp.pcrdtype;
            victim.phase = DirtyVictimPhase::RetryPending;
            DPRINTF(HnfCC,
                    "CC dirty victim=%llu txn=%u got RetryAck "
                    "pcrdtype=%u\n",
                    static_cast<unsigned long long>(txn_id->second),
                    rsp.txnid, rsp.pcrdtype);
            return std::nullopt;
        }

        if (decoded.minor == RspMinor::PCrdGrant) {
            if (victim.phase != DirtyVictimPhase::RetryPending ||
                rsp.pcrdtype != victim.retryPcrdtype || rsp.respErr != 0) {
                return std::nullopt;
            }
            queueDirtyVictimRequest(victim, false, rsp.pcrdtype);
            return std::nullopt;
        }

        if (decoded.minor == RspMinor::DBIDResp ||
            decoded.minor == RspMinor::DBIDRespOrd ||
            decoded.minor == RspMinor::CompDBIDResp) {
            if (victim.phase != DirtyVictimPhase::WaitDbid ||
                !victim.requestSent || rsp.dbid == 0 ||
                rsp.pcrdtype != victim.activePcrdtype) {
                return std::nullopt;
            }
            victim.dbid = rsp.dbid;
            if (rsp.respErr != 0) {
                completeDirtyVictimWriteback(victim, rsp);
                return std::nullopt;
            }
            queueDirtyVictimData(victim);
            if (decoded.minor == RspMinor::CompDBIDResp) {
                completeDirtyVictimWriteback(victim, rsp);
            }
            return std::nullopt;
        }

        if ((victim.phase != DirtyVictimPhase::DataQueued &&
             victim.phase != DirtyVictimPhase::WaitComp) ||
            victim.dbid == 0 || rsp.dbid != victim.dbid ||
            rsp.pcrdtype != victim.activePcrdtype) {
            return std::nullopt;
        }
        completeDirtyVictimWriteback(victim, rsp);
        return std::nullopt;
    }
    panic_if(decoded.minor == RspMinor::SnpRespFwded,
             "HnfCC does not support forwarded SnpResp opcode=0x%x "
             "src=%u txn=%u\n",
             rsp.opcode, rsp.srcid, rsp.txnid);
    if (decoded.minor == RspMinor::SnpResp) {
        if (seqPocqEntry.valid &&
            seqPocqEntry.snoopTxnId == rsp.txnid) {
            panic_if(seqPocqEntry.state != SeqPocqState::WaitSnoop ||
                         (seqPocqEntry.pendingTargets &
                          (1ULL << slcsfUnit->sharerIndex(rsp.srcid))) == 0 ||
                         rsp.tgtid != seqPocqEntry.homeNodeId ||
                         rsp.qos != 0 || rsp.dbid != 0 ||
                         rsp.respErr != 0 || rsp.pcrdtype != 0 ||
                         rsp.resp > RespUC,
                     "HnfCC SEQ SnpResp identity/state mismatch id=%llu "
                     "src=%u tgt=%u txn=%u qos=%u dbid=%u resp=%u "
                     "respErr=%u pcrdtype=%u pending=%#llx\n",
                     static_cast<unsigned long long>(seqPocqEntry.seqId),
                     rsp.srcid, rsp.tgtid, rsp.txnid, rsp.qos, rsp.dbid,
                     rsp.resp, rsp.respErr, rsp.pcrdtype,
                     static_cast<unsigned long long>(
                         seqPocqEntry.pendingTargets));
            completeSeqSnoopTarget(rsp.srcid, false);
            return std::nullopt;
        }

        auto snoopIt = snoopTxnToEntry.find(rsp.txnid);
        panic_if(snoopIt == snoopTxnToEntry.end(),
                 "HnfCC SnpResp for unknown src=%u txn=%u\n",
                 rsp.srcid, rsp.txnid);
        panic_if(snoopIt->second >= entries.size(),
                 "HnfCC SnpResp txn=%u maps to invalid entry=%u\n",
                 rsp.txnid, snoopIt->second);
        const Entry& entry = entries[snoopIt->second];
        panic_if(entry.state != HnfCcEntryState::WaitSnoop ||
                     entry.snoopTxnId != rsp.txnid ||
                     (entry.snoopPendingTargets &
                      (1ULL << slcsfUnit->sharerIndex(rsp.srcid))) == 0 ||
                     rsp.tgtid != entry.req.tgtid ||
                     rsp.qos != entry.req.qos || rsp.dbid != 0 ||
                     rsp.respErr != 0 || rsp.pcrdtype != 0 ||
                     rsp.resp > RespUC,
                 "HnfCC SnpResp identity/state mismatch entry=%u src=%u "
                 "tgt=%u txn=%u qos=%u dbid=%u resp=%u respErr=%u "
                 "pcrdtype=%u pending=%#llx\n",
                 snoopIt->second, rsp.srcid, rsp.tgtid, rsp.txnid,
                 rsp.qos, rsp.dbid, rsp.resp, rsp.respErr, rsp.pcrdtype,
                 static_cast<unsigned long long>(
                     entry.snoopPendingTargets));
        completeSnoopTarget(snoopIt->second, rsp.srcid, false);
        return std::nullopt;
    }

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
    panic_if(rsp.tgtid != entry.req.tgtid ||
                 rsp.qos != entry.req.qos || rsp.dbid != 0 ||
                 rsp.respErr != 0 || rsp.pcrdtype != 0 ||
                 (rsp.resp != 0 && rsp.resp != 0xff),
             "HnfCC CompAck identity/state mismatch entry=%u src=%u "
             "tgt=%u txn=%u qos=%u dbid=%u resp=%u respErr=%u "
             "pcrdtype=%u\n",
             *entryId, rsp.srcid, rsp.tgtid, rsp.txnid, rsp.qos,
             rsp.dbid, rsp.resp, rsp.respErr, rsp.pcrdtype);

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
    info.allocationSeq = entry.seq;
    info.resourceClass = entry.resourceClass;
    info.reqPriority = entry.priority;
    info.srcid = entry.req.srcid;
    info.txnid = entry.req.txnid;
    info.pcrdtype = entry.req.pcrdtype;
    return info;
}

void
HnfCoherencyController::popTxRsp()
{
    panic_if(txRspQ.empty(), "HnfCC popTxRsp on empty queue\n");
    const HnfCcTxRsp& response = txRspQ.front();
    if (response.traceDirtyVictimRequesterDone) {
        panic_if(!traceDirtyVictimRequesterDone(
                     response.rsp.tgtid, response.rsp.txnid),
                 "HnfCC lost dirty-victim requester before TXRSP "
                 "src=%u txn=%u\n",
                 response.rsp.tgtid, response.rsp.txnid);
    }
    txRspQ.pop_front();
}

bool
HnfCoherencyController::dirtyVictimRequesterTracked(
    const Entry& entry) const
{
    const uint64_t key = requesterTraceKey(entry.req.srcid, entry.req.txnid);
    return dirtyVictimRequesters.count(key) != 0;
}

bool
HnfCoherencyController::traceDirtyVictimRequesterDone(
    uint32_t srcid, uint32_t txnid)
{
    const uint64_t key = requesterTraceKey(srcid, txnid);
    const auto requester = dirtyVictimRequesters.find(key);
    if (requester == dirtyVictimRequesters.end()) {
        return false;
    }

    DPRINTF(HnfDirtyVictimE2E,
            "HNF_DV_REQUESTER_DONE src=%u txn=%u\n",
            srcid, txnid);
    dirtyVictimRequesters.erase(requester);
    return true;
}

HnfCcRetireInfo
HnfCoherencyController::retireEntry(uint32_t entryId)
{
    panic_if(entryId >= entries.size(), "HnfCC invalid retire entry=%u\n",
             entryId);
    Entry& entry = entries[entryId];
    if (entry.mcTxnId != 0) {
        panic_if(mcTxnToEntry.erase(entry.mcTxnId) != 1,
                 "HnfCC entry=%u retires with unowned MC transaction=%u\n",
                 entryId, entry.mcTxnId);
    }
    HnfCcRetireInfo info = makeRetireInfo(entry);
    const uint64_t addr = entry.blockAddr;
    traceDirtyVictimRequesterDone(entry.req.srcid, entry.req.txnid);
    entry = Entry{};
    wakeSleepingEntries(addr);
    return info;
}

void
HnfCoherencyController::wakeSleepingEntries(uint64_t addr)
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& entry = entries[i];
        if (entry.state != HnfCcEntryState::Sleep ||
            entry.blockAddr != addr || entry.slcsfReplay) {
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
HnfCoherencyController::hasMainAddressHazard(uint64_t addr) const
{
    return std::any_of(entries.begin(), entries.end(),
                       [this, addr](const Entry& entry) {
                           return entryAllocated(entry) &&
                               entry.state != HnfCcEntryState::Sleep &&
                               entry.blockAddr == addr;
                       });
}

uint32_t
HnfCoherencyController::allocateMcTxnId()
{
    panic_if(nextMcTxnId == 0 || nextMcTxnId >= McTxnIdLimit,
             "HnfCC MC transaction identity space exhausted next=%u\n",
             nextMcTxnId);
    const uint32_t txn_id = nextMcTxnId++;
    panic_if(mcTxnToEntry.count(txn_id) ||
                 dirtyVictimTxnIds.count(txn_id) ||
                 snoopTxnToEntry.count(txn_id) ||
                 (seqPocqEntry.valid &&
                  seqPocqEntry.snoopTxnId == txn_id),
             "HnfCC MC transaction identity collision txn=%u\n", txn_id);
    return txn_id;
}

uint32_t
HnfCoherencyController::allocateSnoopTxnId()
{
    while (true) {
        panic_if(nextSnoopTxnId < SnoopTxnIdBase ||
                     nextSnoopTxnId == UINT32_MAX,
                 "HnfCC snoop transaction identity space exhausted next=%u\n",
                 nextSnoopTxnId);
        const uint32_t snoop_txn = nextSnoopTxnId++;
        if (!mcTxnToEntry.count(snoop_txn) &&
            !snoopTxnToEntry.count(snoop_txn) &&
            !dirtyVictimTxnIds.count(snoop_txn) &&
            (!seqPocqEntry.valid ||
             seqPocqEntry.snoopTxnId != snoop_txn)) {
            return snoop_txn;
        }
    }
}

uint32_t
HnfCoherencyController::allocateDirtyVictimTxnId(uint32_t requester_txnid)
{
    while (true) {
        panic_if(nextDirtyVictimTxnId < DirtyVictimTxnIdBase ||
                     nextDirtyVictimTxnId >= DirtyVictimTxnIdLimit,
                 "HnfCC dirty-victim transaction identity space exhausted "
                 "next=%u\n",
                 nextDirtyVictimTxnId);
        const uint32_t txn_id = nextDirtyVictimTxnId++;
        if (txn_id != requester_txnid && !mcTxnToEntry.count(txn_id) &&
            !dirtyVictimTxnIds.count(txn_id) &&
            !snoopTxnToEntry.count(txn_id) &&
            (!seqPocqEntry.valid ||
             seqPocqEntry.snoopTxnId != txn_id)) {
            return txn_id;
        }
    }
}

std::optional<SlcSfVictimId>
HnfCoherencyController::dirtyVictimForTxn(
    uint32_t downstreamTxnId) const
{
    const auto transaction = dirtyVictimTxnIds.find(downstreamTxnId);
    if (transaction == dirtyVictimTxnIds.end()) {
        return std::nullopt;
    }
    return SlcSfVictimId{transaction->second};
}

HnfCoherencyController::DirtyVictimPhase
HnfCoherencyController::dirtyVictimPhase(SlcSfVictimId id) const
{
    return dirtyVictimTxns.at(id.value).phase;
}

void
HnfCoherencyController::startDirtyVictimWriteback(
    uint32_t entryId, const SlcSfSlcVictim& victim)
{
    panic_if(entryId >= entries.size() || !entryAllocated(entries[entryId]),
             "HnfCC starts dirty-victim writeback without requester entry\n");
    startDirtyVictimWriteback(
        victim, entries[entryId].req.tgtid, entries[entryId].req.srcid,
        entries[entryId].req.txnid, true);
}

void
HnfCoherencyController::startDirtyVictimWriteback(
    const SlcSfSlcVictim& victim, uint32_t homeNodeId,
    uint32_t requesterSrc, uint32_t requesterTxn, bool trackRequester)
{
    panic_if(!victim.victimId.value || !victim.line.dirty ||
                 victim.line.data.size() != blockSize,
             "HnfCC invalid dirty-victim handoff id=%llu bytes=%u dirty=%u\n",
             static_cast<unsigned long long>(victim.victimId.value),
             static_cast<unsigned>(victim.line.data.size()),
             victim.line.dirty);
    panic_if(dirtyVictimTxns.count(victim.victimId.value),
             "HnfCC duplicate dirty-victim handoff id=%llu\n",
             static_cast<unsigned long long>(victim.victimId.value));

    DirtyVictimTxn transaction{};
    transaction.victim = victim;
    transaction.downstreamTxnId =
        allocateDirtyVictimTxnId(requesterTxn);
    transaction.homeNodeId = homeNodeId;
    const uint64_t requester_key =
        requesterTraceKey(requesterSrc, requesterTxn);
    panic_if(trackRequester && dirtyVictimRequesters.count(requester_key),
             "HnfCC requester src=%u txn=%u caused a second live "
             "dirty victim\n",
             requesterSrc, requesterTxn);
    if (trackRequester) {
        dirtyVictimRequesters.insert(requester_key);
    }
    const uint32_t txn_id = transaction.downstreamTxnId;
    dirtyVictimTxnIds.emplace(txn_id, victim.victimId.value);
    auto [it, inserted] = dirtyVictimTxns.emplace(
        victim.victimId.value, std::move(transaction));
    panic_if(!inserted, "HnfCC failed to allocate dirty-victim txn\n");

    queueDirtyVictimRequest(
        it->second, dirtyVictimRetryEnabled, 0);

    DPRINTF(HnfDirtyVictimE2E,
            "HNF_DV_START victim=%llu txn=%u addr=%#llx "
            "requester_src=%u requester_txn=%u data_hash=%016llx\n",
            static_cast<unsigned long long>(victim.victimId.value), txn_id,
            static_cast<unsigned long long>(victim.lineAddress),
            requesterSrc, requesterTxn,
            static_cast<unsigned long long>(
                Chi2ClassicMemTxnPolicy::traceDataHash(victim.line.data)));

    DPRINTF(HnfCC,
            "PoCQ dirty victim=%llu queues WriteNoSnpFull addr=%#llx "
            "downstream txn=%u requester src=%u txn=%u\n",
            static_cast<unsigned long long>(victim.victimId.value),
            static_cast<unsigned long long>(victim.lineAddress), txn_id,
            requesterSrc, requesterTxn);
}

void
HnfCoherencyController::queueDirtyVictimRequest(
    DirtyVictimTxn& transaction, bool allowRetry, uint8_t pcrdtype)
{
    panic_if(transaction.requestQueued || transaction.requestSent ||
                 transaction.dataQueued || transaction.dataSent ||
                 transaction.dbid != 0 ||
                 (transaction.phase != DirtyVictimPhase::ReqQueued &&
                  transaction.phase != DirtyVictimPhase::RetryPending),
             "HnfCC dirty victim=%llu queues request in phase=%u\n",
             static_cast<unsigned long long>(
                 transaction.victim.victimId.value),
             static_cast<unsigned>(transaction.phase));

    HnfCcTxReq out{};
    out.entry = UINT32_MAX;
    out.dirtyVictimId = transaction.victim.victimId.value;
    out.req.srcid = transaction.homeNodeId;
    out.req.tgtid = selectSnNode(transaction.victim.lineAddress);
    out.req.txnid = transaction.downstreamTxnId;
    out.req.opcode = ReqOp::WriteNoSnpFull;
    out.req.AllowRetry = allowRetry ? 1 : 0;
    out.req.pcrdtype = pcrdtype;
    out.req.addr = transaction.victim.lineAddress;
    out.req.size = static_cast<uint8_t>(blockSize);
    out.req.ReturnNid = transaction.homeNodeId;
    out.req.hnfDirtyVictim = true;
    txReqQ.push_back(std::move(out));
    transaction.activeAllowRetry = allowRetry;
    transaction.activePcrdtype = pcrdtype;
    transaction.requestQueued = true;
    transaction.phase = DirtyVictimPhase::ReqQueued;
}

void
HnfCoherencyController::queueDirtyVictimData(DirtyVictimTxn& transaction)
{
    panic_if(transaction.phase != DirtyVictimPhase::WaitDbid ||
                 !transaction.requestSent || transaction.dbid == 0 ||
                 transaction.dataQueued || transaction.dataSent,
             "HnfCC dirty victim=%llu queues data in phase=%u dbid=%u\n",
             static_cast<unsigned long long>(
                 transaction.victim.victimId.value),
             static_cast<unsigned>(transaction.phase), transaction.dbid);
    HnfCcTxDat out{};
    out.entry = UINT32_MAX;
    out.dirtyVictimId = transaction.victim.victimId.value;
    out.dat.srcid = transaction.homeNodeId;
    out.dat.tgtid = selectSnNode(transaction.victim.lineAddress);
    out.dat.txnid = transaction.downstreamTxnId;
    out.dat.opcode = DatOp::NonCopyBackWriteData;
    out.dat.last = true;
    out.dat.HomeNID = transaction.homeNodeId;
    out.dat.dbid = transaction.dbid;
    out.dat.dataid = 0;
    out.dat.beatOffset = 0;
    out.dat.data = transaction.victim.line.data;
    out.dat.byteEnable.assign(blockSize, 1);
    out.dat.chunkValid.assign((blockSize + 7) / 8, 1);
    txDatQ.push_back(std::move(out));
    transaction.dataQueued = true;
    transaction.phase = DirtyVictimPhase::DataQueued;
}

bool
HnfCoherencyController::dirtyVictimResponseMatches(
    const DirtyVictimTxn& transaction, const RawRsp& rsp) const
{
    return rsp.srcid == selectSnNode(transaction.victim.lineAddress) &&
        rsp.tgtid == transaction.homeNodeId &&
        rsp.txnid == transaction.downstreamTxnId;
}

void
HnfCoherencyController::completeDirtyVictimWriteback(
    DirtyVictimTxn& transaction, const RawRsp& rsp)
{
    // There is no architected path which can report this asynchronous failure
    // back to the already-completed requester. Retiring would lose the only
    // dirty copy, while retaining an inert owner schedules the HNF forever and
    // makes drain impossible.  Fail-stop until a real retry/error contract is
    // modeled.
    panic_if(rsp.respErr != 0,
             "HnfCC dirty victim=%llu downstream txn=%u failed respErr=%u; "
             "dirty data cannot be retired safely\n",
             static_cast<unsigned long long>(
                 transaction.victim.victimId.value),
             transaction.downstreamTxnId, rsp.respErr);

    transaction.completionSeen = true;
    if (!transaction.dataSent) {
        panic_if(transaction.phase != DirtyVictimPhase::DataQueued,
                 "HnfCC dirty victim=%llu completion before queued data\n",
                 static_cast<unsigned long long>(
                     transaction.victim.victimId.value));
        return;
    }

    panic_if(transaction.phase != DirtyVictimPhase::WaitComp,
             "HnfCC dirty victim=%llu completion in phase=%u\n",
             static_cast<unsigned long long>(
                 transaction.victim.victimId.value),
             static_cast<unsigned>(transaction.phase));
    DPRINTF(HnfCC,
            "CC dirty victim=%llu downstream txn=%u completed\n",
            static_cast<unsigned long long>(
                transaction.victim.victimId.value),
            transaction.downstreamTxnId);
    retireDirtyVictimWriteback(transaction);
}

void
HnfCoherencyController::retireDirtyVictimWriteback(
    DirtyVictimTxn& transaction)
{
    panic_if(!transaction.requestSent || !transaction.dataSent ||
                 !transaction.completionSeen || transaction.dbid == 0,
             "HnfCC retires incomplete dirty-victim writeback=%llu\n",
             static_cast<unsigned long long>(
                 transaction.victim.victimId.value));
    const uint64_t victim_id = transaction.victim.victimId.value;
    const uint32_t downstream_txn = transaction.downstreamTxnId;
    DPRINTF(HnfDirtyVictimE2E,
            "HNF_DV_DONE victim=%llu txn=%u\n",
            static_cast<unsigned long long>(victim_id), downstream_txn);
    panic_if(dirtyVictimTxnIds.erase(downstream_txn) != 1,
             "HnfCC dirty victim=%llu lost downstream map at completion\n",
             static_cast<unsigned long long>(victim_id));
    panic_if(dirtyVictimTxns.erase(victim_id) != 1,
             "HnfCC dirty victim=%llu lost PoCQ owner at completion\n",
             static_cast<unsigned long long>(victim_id));
}

void
HnfCoherencyController::startSeqPocq()
{
    panic_if(seqPocqEntry.valid,
             "HnfCC starts SEQ POCQ while another entry is active\n");
    panic_if(!slcsfUnit || !slcsfUnit->hasPendingSeq(),
             "HnfCC starts SEQ POCQ without a pending victim\n");

    const HnfSLCSF::SeqVictim victim = slcsfUnit->frontPendingSeq();
    const uint32_t snoop_txn_id = allocateSnoopTxnId();
    slcsfUnit->markSeqIssued(
        victim.id, SnpOp::CleanInvalid, snoop_txn_id);

    seqPocqEntry = SeqPocqEntry{};
    seqPocqEntry.valid = true;
    seqPocqEntry.seqId = victim.id;
    seqPocqEntry.blockAddr = victim.blockAddr;
    seqPocqEntry.homeNodeId = victim.homeNodeId;
    seqPocqEntry.owner = victim.owner;
    seqPocqEntry.sharers = victim.sharers;
    seqPocqEntry.snoopTxnId = snoop_txn_id;
    seqPocqEntry.data.assign(blockSize, 0);
    resetDataAssembly(seqPocqEntry.dataAssembly, 0, blockSize);

    DPRINTF(HnfCC,
            "SEQ POCQ admit id=%llu addr=%#llx owner=%u sharers=%#llx\n",
            static_cast<unsigned long long>(seqPocqEntry.seqId),
            static_cast<unsigned long long>(seqPocqEntry.blockAddr),
            seqPocqEntry.owner,
            static_cast<unsigned long long>(seqPocqEntry.sharers));
    stepSeqPocq({SeqPocqEventKind::Admit});
}

void
HnfCoherencyController::stepSeqPocq(const SeqPocqEvent& event)
{
    panic_if(!seqPocqEntry.valid, "HnfCC steps an idle SEQ POCQ\n");
    SeqPocqStepResult step =
        seqPocqGraph.tryStep(seqPocqEntry.state, event);
    panic_if(!step.stepped,
             "HnfCC SEQ POCQ id=%llu has no transition state=%s event=%s\n",
             static_cast<unsigned long long>(seqPocqEntry.seqId),
             SEQ_POCQ_StateGraph::stateName(step.oldState),
             SEQ_POCQ_StateGraph::eventName(event.kind));

    DPRINTF(HnfCC,
            "SEQ POCQ id=%llu %s --%s--> %s actions=%u\n",
            static_cast<unsigned long long>(seqPocqEntry.seqId),
            SEQ_POCQ_StateGraph::stateName(step.oldState),
            SEQ_POCQ_StateGraph::eventName(event.kind),
            SEQ_POCQ_StateGraph::stateName(step.nextState),
            static_cast<unsigned>(step.actions.size()));

    for (SeqPocqActionKind action : step.actions) {
        DPRINTF(HnfCC, "SEQ POCQ id=%llu action=%s\n",
                static_cast<unsigned long long>(seqPocqEntry.seqId),
                SEQ_POCQ_StateGraph::actionName(action));
        switch (action) {
          case SeqPocqActionKind::CheckHazard:
            stepSeqPocq({hasMainAddressHazard(seqPocqEntry.blockAddr) ?
                         SeqPocqEventKind::HazardBlocked :
                         SeqPocqEventKind::HazardClear});
            break;
          case SeqPocqActionKind::QueueCleanInvalid:
            queueSeqSnoops();
            break;
          case SeqPocqActionKind::IssueCompleteSfEvict:
            tryIssueSeqComplete();
            break;
          case SeqPocqActionKind::Retire:
            DPRINTF(HnfCC, "SEQ POCQ retire id=%llu addr=%#llx\n",
                    static_cast<unsigned long long>(seqPocqEntry.seqId),
                    static_cast<unsigned long long>(
                        seqPocqEntry.blockAddr));
            seqPocqEntry = SeqPocqEntry{};
            break;
        }
    }
}

void
HnfCoherencyController::tryIssueSeqComplete()
{
    panic_if(!seqPocqEntry.valid ||
                 seqPocqEntry.state != SeqPocqState::CompleteIssue,
             "HnfCC issues inactive SEQ completion\n");
    if (!seqPocqEntry.pendingComplete) {
        SlcSfReqHeader header{};
        header.reqId = slcSfReqIds.allocate();
        header.pocEntryId = UINT32_MAX;
        header.lineAddress = seqPocqEntry.blockAddr;
        header.requester = seqPocqEntry.owner;
        header.opcode = SnpOp::CleanInvalid;
        header.trace.linkSequence = seqPocqEntry.seqId;
        header.trace.transactionId = seqPocqEntry.snoopTxnId;
        seqPocqEntry.completeReqId = header.reqId;
        seqPocqEntry.pendingComplete = makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{seqPocqEntry.seqId}, seqPocqEntry.data,
            seqPocqEntry.dataDirty);
    }

    const SlcSfEnqueueResult result =
        slcsfUnit->tryEnqueue(std::move(*seqPocqEntry.pendingComplete));
    if (result == SlcSfEnqueueResult::Accepted) {
        seqPocqEntry.pendingComplete.reset();
        seqPocqEntry.retryNotBeforeTick = 0;
        stepSeqPocq({SeqPocqEventKind::CompleteAccepted});
    } else {
        const auto* request =
            std::get_if<SlcSfUpdateReq>(&*seqPocqEntry.pendingComplete);
        panic_if(!request ||
                     request->header.reqId != seqPocqEntry.completeReqId,
                 "HnfCC rejected SEQ completion changed ownership/id\n");
    }
}

void
HnfCoherencyController::queueSeqSnoops()
{
    panic_if(!seqPocqEntry.valid || seqPocqEntry.sharers == 0,
             "HnfCC queues invalid SEQ snoop id=%llu sharers=%#llx\n",
             static_cast<unsigned long long>(seqPocqEntry.seqId),
             static_cast<unsigned long long>(seqPocqEntry.sharers));

    panic_if(seqPocqEntry.snoopTxnId == 0,
             "HnfCC queues SEQ snoop without transaction identity\n");
    seqPocqEntry.pendingTargets = seqPocqEntry.sharers;
    for (uint32_t target = 0; target < 64; ++target) {
        if ((seqPocqEntry.pendingTargets & (1ULL << target)) == 0) {
            continue;
        }
        const uint32_t target_src = slcsfUnit->srcIdForIndex(target);
        HnfCcTxSnp out{};
        out.entry = UINT32_MAX;
        out.targetNode = target_src;
        out.snp.srcid = seqPocqEntry.homeNodeId;
        out.snp.tgtid = targetRouteId(target_src, seqPocqEntry.blockAddr);
        out.snp.txnid = seqPocqEntry.snoopTxnId;
        out.snp.opcode = SnpOp::CleanInvalid;
        out.snp.addr = seqPocqEntry.blockAddr;
        out.snp.size = static_cast<uint8_t>(blockSize);
        txSnpQ.push_back(out);
        DPRINTF(HnfCC,
                "SEQ POCQ id=%llu queues SnpCleanInvalid txn=%u "
                "addr=%#llx targetNode=%u targetRoute=%u\n",
                static_cast<unsigned long long>(seqPocqEntry.seqId),
                seqPocqEntry.snoopTxnId,
                static_cast<unsigned long long>(seqPocqEntry.blockAddr),
                target, out.snp.tgtid);
    }
}

void
HnfCoherencyController::completeSeqSnoopTarget(uint32_t responder,
                                               bool has_data,
                                               bool data_dirty)
{
    panic_if(!seqPocqEntry.valid ||
             seqPocqEntry.state != SeqPocqState::WaitSnoop,
             "HnfCC completes inactive SEQ snoop responder=%u\n",
             responder);
    panic_if((seqPocqEntry.pendingTargets &
                  (1ULL << slcsfUnit->sharerIndex(responder))) == 0,
             "HnfCC SEQ id=%llu unexpected responder=%u pending=%#llx\n",
             static_cast<unsigned long long>(seqPocqEntry.seqId),
             responder,
             static_cast<unsigned long long>(
                 seqPocqEntry.pendingTargets));
    panic_if(has_data && seqPocqEntry.dataReceived,
             "HnfCC SEQ id=%llu got dirty data from multiple RNFs\n",
             static_cast<unsigned long long>(seqPocqEntry.seqId));
    panic_if(data_dirty && !has_data,
             "HnfCC SEQ id=%llu marks a no-data response dirty\n",
             static_cast<unsigned long long>(seqPocqEntry.seqId));

    seqPocqEntry.dataReceived |= has_data;
    seqPocqEntry.dataDirty |= data_dirty;
    seqPocqEntry.pendingTargets &=
        ~(1ULL << slcsfUnit->sharerIndex(responder));
    DPRINTF(HnfCC,
            "SEQ POCQ id=%llu accepts snoop response txn=%u responder=%u "
            "hasData=%u pending=%#llx\n",
            static_cast<unsigned long long>(seqPocqEntry.seqId),
            seqPocqEntry.snoopTxnId, responder, has_data,
            static_cast<unsigned long long>(
                seqPocqEntry.pendingTargets));

    if (seqPocqEntry.pendingTargets == 0) {
        stepSeqPocq({SeqPocqEventKind::SnoopDone});
    }
}

void
HnfCoherencyController::retrySlcsfReplayEntries(Tick currentTick)
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& entry = entries[i];
        if (entry.state != HnfCcEntryState::Sleep ||
            !entry.slcsfReplay || currentTick < entry.retryNotBeforeTick) {
            continue;
        }
        if (hasAddressHazard(i, entry.blockAddr)) {
            continue;
        }
        DPRINTF(HnfCC,
                "CC retries SLCSF replay entry=%u addr=%#llx\n", i,
                static_cast<unsigned long long>(entry.blockAddr));
        if (entry.slcUpdatePhase == SlcUpdatePhase::ReplayWait) {
            entry.slcUpdatePhase = SlcUpdatePhase::None;
        }
        if (entry.expectsWriteData && entry.writeDataAssembly.complete) {
            entry.state = HnfCcEntryState::Working;
            entry.pocqState = PocqState::SlcLookup;
            entry.slcsfReplay = false;
            panic_if(executePocqAction(
                         i, PocqActionKind::DoSlcLookup, PocqEvent{}),
                     "HnfCC entry=%u retired while retrying write lookup\n",
                     i);
        } else {
            startReadFlow(i);
        }
    }
}

void
HnfCoherencyController::tryIssueSlcUpdate(uint32_t entryId)
{
    Entry& entry = entries.at(entryId);
    panic_if(entry.slcUpdatePhase != SlcUpdatePhase::IssuePending ||
                 !entry.pendingSlcUpdate ||
                 !entry.expectedSlcUpdateOperation,
             "HnfCC entry=%u retries update without pending request\n",
             entryId);
    const SlcSfReqId reqId = entry.slcUpdateReqId;
    panic_if(!reqId.valid(), "HnfCC entry=%u has invalid update reqId\n",
             entryId);
    const SlcSfOperationKind expectedOperation =
        *entry.expectedSlcUpdateOperation;
    panic_if(slcSfResponseOperation(*entry.pendingSlcUpdate) !=
                 expectedOperation,
             "HnfCC entry=%u pending update changed operation\n", entryId);

    const SlcSfEnqueueResult result =
        slcsfUnit->tryEnqueue(std::move(*entry.pendingSlcUpdate));
    if (result == SlcSfEnqueueResult::Accepted) {
        entry.pendingSlcUpdate.reset();
        entry.slcUpdatePhase = SlcUpdatePhase::Waiting;
        PocqEvent accepted{};
        accepted.kind = PocqEventKind::SlcUpdateAccepted;
        accepted.txn = entry.txnKind;
        panic_if(stepPocq(entryId, accepted),
                 "HnfCC entry=%u retired while issuing update\n", entryId);
        DPRINTF(HnfCC, "CC entry=%u issued SLCSF update req=%llu\n",
                entryId, static_cast<unsigned long long>(reqId.value));
    } else {
        const SlcSfReqId retainedId = std::visit(
            [](const auto& request) { return request.header.reqId; },
            *entry.pendingSlcUpdate);
        panic_if(retainedId != reqId ||
                     slcSfResponseOperation(*entry.pendingSlcUpdate) !=
                         expectedOperation ||
                     entry.expectedSlcUpdateOperation != expectedOperation,
                 "HnfCC entry=%u rejected update changed ownership/id/op\n",
                 entryId);
    }
}

void
HnfCoherencyController::tryIssueSlcLookup(uint32_t entryId)
{
    Entry& entry = entries.at(entryId);
    panic_if(entry.slcLookupPhase != SlcLookupPhase::IssuePending ||
                 !entry.pendingSlcLookup,
             "HnfCC entry=%u retries lookup without pending request\n",
             entryId);
    const SlcSfReqId reqId = entry.slcLookupReqId;
    panic_if(!reqId.valid(), "HnfCC entry=%u has invalid lookup reqId\n",
             entryId);

    const SlcSfEnqueueResult result =
        slcsfUnit->tryEnqueue(std::move(*entry.pendingSlcLookup));
    if (result == SlcSfEnqueueResult::Accepted) {
        entry.pendingSlcLookup.reset();
        entry.slcLookupPhase = SlcLookupPhase::Waiting;
        DPRINTF(HnfCC, "CC entry=%u issued SLCSF lookup req=%llu\n",
                entryId, static_cast<unsigned long long>(reqId.value));
    } else {
        const SlcSfLookupReq* request =
            std::get_if<SlcSfLookupReq>(&*entry.pendingSlcLookup);
        panic_if(!request || request->header.reqId != reqId,
                 "HnfCC entry=%u rejected lookup changed ownership/id\n",
                 entryId);
    }
}

void
HnfCoherencyController::retryPendingSlcLookups()
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        if (entries[i].slcLookupPhase == SlcLookupPhase::IssuePending) {
            tryIssueSlcLookup(i);
        }
    }
}

void
HnfCoherencyController::retryPendingSlcUpdates()
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        if (entries[i].slcUpdatePhase == SlcUpdatePhase::IssuePending) {
            tryIssueSlcUpdate(i);
        }
    }
}

void
HnfCoherencyController::continueLatchedSlcLookups()
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& entry = entries[i];
        if (entry.slcLookupPhase != SlcLookupPhase::ResponseLatched) {
            continue;
        }
        panic_if(!entry.latchedSlcResponse,
                 "HnfCC entry=%u has response phase without response\n", i);
        entry.latchedSlcResponse.reset();
        entry.slcLookupPhase = SlcLookupPhase::None;

        PocqEvent lookupDone{};
        lookupDone.kind = PocqEventKind::SlcLookupDone;
        lookupDone.txn = entry.txnKind;
        lookupDone.slcHit = entry.slcLookupResult.slcHit;
        lookupDone.sfHit = entry.slcLookupResult.sfHit;
        lookupDone.replay = entry.slcLookupResult.replay;
        lookupDone.needsSnoop = entry.slcLookupResult.snoopTargets != 0;
        lookupDone.dataAvailable = entry.slcLookupResult.slcHit;
        lookupDone.needsCompAck = entry.needsCompAck;
        deferRetire(stepPocq(i, lookupDone));
    }
}

void
HnfCoherencyController::continueLatchedSlcUpdates()
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& entry = entries[i];
        if (entry.slcUpdatePhase != SlcUpdatePhase::ResponseLatched) {
            continue;
        }
        panic_if(!entry.latchedSlcUpdateResponse,
                 "HnfCC entry=%u has update phase without response\n", i);
        panic_if(!entry.expectedSlcUpdateOperation ||
                     entry.latchedSlcUpdateResponse->operationKind() !=
                         *entry.expectedSlcUpdateOperation,
                 "HnfCC entry=%u latched update changed operation\n", i);
        entry.latchedSlcUpdateResponse.reset();
        entry.slcUpdatePhase = SlcUpdatePhase::None;
        entry.expectedSlcUpdateOperation.reset();

        PocqEvent updateDone{};
        updateDone.kind = PocqEventKind::SlcUpdateDone;
        updateDone.txn = entry.txnKind;
        updateDone.needsCompAck = entry.needsCompAck;
        deferRetire(stepPocq(i, updateDone));
    }
}

void
HnfCoherencyController::handleSlcsfReplay(
    uint32_t entryId, const SlcSfResponse& response)
{
    Entry& entry = entries.at(entryId);
    const bool lookup =
        response.operationKind() == SlcSfOperationKind::Lookup;
    const auto* replay = std::get_if<SlcSfReplay>(&response.payload());
    panic_if(!replay,
             "HnfCC entry=%u Replay has invalid payload\n", entryId);

    if (entry.snoopTxnId != 0) {
        snoopTxnToEntry.erase(entry.snoopTxnId);
    }
    entry.slcLookupResult = HnfSlcLookupResult{};
    entry.slcCommitToken = SlcSfCommitToken{};
    entry.slcLookupReqId = SlcSfReqId{};
    entry.pendingSlcLookup.reset();
    entry.latchedSlcResponse.reset();
    entry.slcLookupPhase = SlcLookupPhase::None;
    entry.pendingSlcUpdate.reset();
    entry.latchedSlcUpdateResponse.reset();
    entry.slcUpdateReqId = SlcSfReqId{};
    entry.expectedSlcUpdateOperation.reset();
    entry.readData.clear();
    if (!entry.expectsWriteData) {
        entry.writePayload.clear();
    }
    entry.responseDataDirty = false;
    entry.snoopTxnId = 0;
    entry.snoopPendingTargets = 0;
    entry.snoopDataReceived = false;
    entry.snoopDataAssembly = DataAssembly{};
    entry.mcReadIssued = false;
    if (entry.mcTxnId != 0) {
        panic_if(mcTxnToEntry.erase(entry.mcTxnId) != 1,
                 "HnfCC entry=%u Replay lost MC transaction=%u\n",
                 entryId, entry.mcTxnId);
    }
    entry.mcTxnId = 0;
    entry.mcDataAssembly = DataAssembly{};
    if (!entry.expectsWriteData) {
        entry.writeDataAssembly = DataAssembly{};
    }
    entry.retryNotBeforeTick = replay->retryNotBeforeTick;
    entry.slcUpdatePhase = lookup ?
        SlcUpdatePhase::None : SlcUpdatePhase::ReplayWait;

    PocqEvent replayDone{};
    replayDone.kind = lookup ?
        PocqEventKind::SlcLookupDone : PocqEventKind::SlcUpdateDone;
    replayDone.txn = entry.txnKind;
    replayDone.replay = true;
    panic_if(stepPocq(entryId, replayDone),
             "HnfCC entry=%u retired while handling Replay\n", entryId);

    DPRINTF(HnfCC,
            "CC entry=%u waits until tick=%llu after SLCSF replay\n",
            entryId,
            static_cast<unsigned long long>(entry.retryNotBeforeTick));
}

void
HnfCoherencyController::consumeSlcsfResponse(SlcSfResponse response)
{
    const uint32_t entryId = response.pocEntryId();
    if (response.operationKind() == SlcSfOperationKind::CompleteSfEvict) {
        panic_if(!seqPocqEntry.valid ||
                     seqPocqEntry.state != SeqPocqState::CompleteWait ||
                     seqPocqEntry.completeReqId != response.reqId(),
                 "HnfCC unexpected SEQ completion response req=%llu\n",
                 static_cast<unsigned long long>(response.reqId().value));
        if (response.status() == SlcSfTerminalStatus::Replay) {
            const auto* replay =
                std::get_if<SlcSfReplay>(&response.payload());
            panic_if(!replay || entryId != UINT32_MAX,
                     "HnfCC SEQ completion Replay has bad payload\n");
            const Tick retry_not_before = replay->retryNotBeforeTick;
            auto popped = slcsfUnit->popVisibleResponse();
            panic_if(!popped || popped->reqId() != response.reqId() ||
                         popped->status() !=
                             SlcSfTerminalStatus::Replay,
                     "HnfCC SEQ completion Replay was not visible/exact\n");
            seqPocqEntry.retryNotBeforeTick = retry_not_before;
            seqPocqEntry.completeReqId = SlcSfReqId{};
            seqPocqEntry.pendingComplete.reset();
            stepSeqPocq({SeqPocqEventKind::CompleteReplay});
            DPRINTF(HnfCC,
                    "SEQ POCQ id=%llu retries completion after tick=%llu\n",
                    static_cast<unsigned long long>(seqPocqEntry.seqId),
                    static_cast<unsigned long long>(
                        seqPocqEntry.retryNotBeforeTick));
            return;
        }
        panic_if(response.status() != SlcSfTerminalStatus::Done,
                 "HnfCC SEQ completion req=%llu failed\n",
                 static_cast<unsigned long long>(response.reqId().value));
        const auto* update =
            std::get_if<SlcSfUpdateResponse>(&response.payload());
        panic_if(!update ||
                     update->updateKind != SlcSfUpdateKind::CompleteSfEvict ||
                     update->sfVictim ||
                     !update->completionLease,
                 "HnfCC SEQ completion Done has bad payload\n");
        const SlcSfCompletionLease& lease = *update->completionLease;
        panic_if(
            lease.kind() != SlcSfCompletionKind::CompleteSfEvict ||
                lease.reqId() != seqPocqEntry.completeReqId ||
                lease.pocEntryId() != UINT32_MAX ||
                lease.objectId() != seqPocqEntry.seqId ||
                lease.lineAddress() != seqPocqEntry.blockAddr ||
                lease.requester() != seqPocqEntry.owner ||
                lease.opcode() != SnpOp::CleanInvalid ||
                lease.linkSequence() != seqPocqEntry.seqId ||
                lease.transactionId() != seqPocqEntry.snoopTxnId,
            "HnfCC SEQ completion lease identity mismatch\n");
        const std::optional<SlcSfSlcVictim> slc_victim =
            update->slcVictim;
        panic_if(
            slcsfUnit->acknowledgeVisibleCompletion(response) !=
                SlcSfCompletionAckResult::Acknowledged,
            "HnfCC SEQ completion ack was not exact/visible\n");
        if (slc_victim) {
            // A dirty SEQ snoop can install data into a full SLC set.  Its
            // displaced dirty SLC line does not need another snoop. Transfer
            // it directly to a PoCQ-owned WriteNoSnpFull transaction.
            startDirtyVictimWriteback(
                *slc_victim, seqPocqEntry.homeNodeId,
                seqPocqEntry.owner, seqPocqEntry.snoopTxnId, false);
        }
        stepSeqPocq({SeqPocqEventKind::CompleteDone});
        return;
    }
    panic_if(entryId >= entries.size(),
             "HnfCC response has invalid POCQ entry=%u\n", entryId);
    Entry& entry = entries[entryId];
    if (response.operationKind() != SlcSfOperationKind::Lookup) {
        panic_if(entry.slcUpdatePhase != SlcUpdatePhase::Waiting ||
                     entry.latchedSlcUpdateResponse ||
                     entry.slcUpdateReqId != response.reqId() ||
                     !entry.expectedSlcUpdateOperation ||
                     *entry.expectedSlcUpdateOperation !=
                         response.operationKind(),
                 "HnfCC unexpected update response entry=%u req=%llu\n",
                 entryId,
                 static_cast<unsigned long long>(response.reqId().value));

        if (response.status() == SlcSfTerminalStatus::Done) {
            const SlcSfOperationKind expected =
                *entry.expectedSlcUpdateOperation;
            if (expected == SlcSfOperationKind::CommitRead ||
                expected == SlcSfOperationKind::WriteLine) {
                const auto* fill =
                    std::get_if<SlcSfFillResponse>(&response.payload());
                const SlcSfUpdateKind expectedKind =
                    expected == SlcSfOperationKind::CommitRead ?
                        SlcSfUpdateKind::CommitRead :
                        SlcSfUpdateKind::WriteLine;
                panic_if(!fill || fill->updateKind != expectedKind,
                         "HnfCC entry=%u fill update Done has bad payload\n",
                         entryId);
                if (fill->slcVictim) {
                    startDirtyVictimWriteback(entryId, *fill->slcVictim);
                }
            } else if (
                expected == SlcSfOperationKind::CompleteMaintenance ||
                expected == SlcSfOperationKind::RemoveSharer) {
                const auto* update =
                    std::get_if<SlcSfUpdateResponse>(&response.payload());
                const SlcSfUpdateKind expectedKind =
                    expected == SlcSfOperationKind::CompleteMaintenance ?
                        SlcSfUpdateKind::CompleteMaintenance :
                        SlcSfUpdateKind::RemoveSharer;
                panic_if(!update || update->updateKind != expectedKind,
                         "HnfCC entry=%u update Done has bad payload\n",
                         entryId);
                panic_if(update->slcVictim,
                         "HnfCC entry=%u non-allocating update returned "
                         "an SLC victim\n",
                         entryId);
            } else {
                panic("HnfCC entry=%u has unsupported pending operation=%u\n",
                      entryId, static_cast<unsigned>(expected));
            }
            entry.latchedSlcUpdateResponse = std::move(response);
            entry.slcUpdatePhase = SlcUpdatePhase::ResponseLatched;
        } else if (response.status() == SlcSfTerminalStatus::Replay) {
            handleSlcsfReplay(entryId, response);
        } else {
            panic("HnfCC entry=%u update req=%llu failed\n", entryId,
                  static_cast<unsigned long long>(
                      entry.slcUpdateReqId.value));
        }
        return;
    }

    panic_if(entry.slcLookupPhase != SlcLookupPhase::Waiting ||
                 entry.latchedSlcResponse ||
                 entry.slcLookupReqId != response.reqId(),
             "HnfCC unexpected lookup response entry=%u req=%llu\n",
             entryId,
             static_cast<unsigned long long>(response.reqId().value));

    if (response.status() == SlcSfTerminalStatus::Done) {
        const auto* lookup =
            std::get_if<SlcSfLookupResponse>(&response.payload());
        panic_if(!lookup, "HnfCC entry=%u lookup Done has bad payload\n",
                 entryId);
        entry.slcLookupResult = lookup->result;
        entry.slcCommitToken = lookup->token;
    } else if (response.status() == SlcSfTerminalStatus::Replay) {
        handleSlcsfReplay(entryId, response);
        return;
    } else {
        panic("HnfCC entry=%u lookup req=%llu failed\n", entryId,
              static_cast<unsigned long long>(entry.slcLookupReqId.value));
    }

    if (entry.slcLookupResult.slcHit) {
        entry.readData = entry.slcLookupResult.data;
        if (entry.readData.size() < blockSize) {
            entry.readData.resize(blockSize, 0);
        }
    }
    entry.responseDataDirty = entry.slcLookupResult.dataDirty;
    entry.latchedSlcResponse = std::move(response);
    entry.slcLookupPhase = SlcLookupPhase::ResponseLatched;
}

void
HnfCoherencyController::latchVisibleSlcResponses()
{
    const size_t budget = slcsfUnit->pipelineConfig().responseConsumeWidth;
    for (size_t consumed = 0; consumed < budget; ++consumed) {
        const SlcSfResponse* visible = slcsfUnit->frontVisibleResponse();
        if (!visible) {
            break;
        }
        const bool durable = visible->operationKind() ==
            SlcSfOperationKind::CompleteSfEvict;
        if (durable) {
            consumeSlcsfResponse(*visible);
            continue;
        }
        auto response = slcsfUnit->popVisibleResponse();
        if (!response) {
            break;
        }
        consumeSlcsfResponse(std::move(*response));
    }
}

void
HnfCoherencyController::serviceInternalWork()
{
    serviceInternalWork(0);
}

void
HnfCoherencyController::serviceInternalWork(Tick currentTick)
{
    continueLatchedSlcUpdates();
    continueLatchedSlcLookups();
    retryPendingSlcUpdates();
    retryPendingSlcLookups();
    if (!seqPocqEntry.valid && slcsfUnit &&
        slcsfUnit->hasPendingSeq()) {
        startSeqPocq();
    } else if (seqPocqEntry.valid &&
               seqPocqEntry.state == SeqPocqState::Sleep &&
               !hasMainAddressHazard(seqPocqEntry.blockAddr)) {
        stepSeqPocq({SeqPocqEventKind::HazardClear});
    } else if (seqPocqEntry.valid &&
               seqPocqEntry.state == SeqPocqState::CompleteIssue &&
               currentTick >= seqPocqEntry.retryNotBeforeTick) {
        tryIssueSeqComplete();
    }
    latchVisibleSlcResponses();
    retrySlcsfReplayEntries(currentTick);
}

HnfCoherencyController::SlcLookupPhase
HnfCoherencyController::slcLookupPhase(uint32_t entry) const
{
    return entries.at(entry).slcLookupPhase;
}

SlcSfReqId
HnfCoherencyController::slcLookupReqId(uint32_t entry) const
{
    return entries.at(entry).slcLookupReqId;
}

const HnfSlcLookupResult&
HnfCoherencyController::slcLookupResult(uint32_t entry) const
{
    return entries.at(entry).slcLookupResult;
}

const SlcSfCommitToken&
HnfCoherencyController::slcCommitToken(uint32_t entry) const
{
    return entries.at(entry).slcCommitToken;
}

HnfCoherencyController::SlcUpdatePhase
HnfCoherencyController::slcUpdatePhase(uint32_t entry) const
{
    return entries.at(entry).slcUpdatePhase;
}

SlcSfReqId
HnfCoherencyController::slcUpdateReqId(uint32_t entry) const
{
    return entries.at(entry).slcUpdateReqId;
}

PocqState
HnfCoherencyController::pocqState(uint32_t entry) const
{
    return entries.at(entry).pocqState;
}

Tick
HnfCoherencyController::slcsfRetryNotBeforeTick(uint32_t entry) const
{
    return entries.at(entry).retryNotBeforeTick;
}

bool
HnfCoherencyController::mayGenerateSlcsfIntent() const
{
    return seqPocqEntry.valid ||
        std::any_of(entries.begin(), entries.end(),
                    [this](const Entry& entry) {
                        return entryAllocated(entry);
                    });
}

void
HnfCoherencyController::serializeSlcsfIdentityState(CheckpointOut& cp) const
{
    panic_if(hasWork() || mayGenerateSlcsfIntent() ||
                 !deferredRetireQ.empty(),
             "HNF CC checkpoint requires no deferred retire or active owner\n");
    paramOut(cp, "nextRequestId", slcSfReqIds.nextValue());
    paramOut(cp, "nextMcTransactionId", nextMcTxnId);
    paramOut(cp, "nextSnoopTransactionId", nextSnoopTxnId);
    paramOut(cp, "nextDirtyVictimTransactionId", nextDirtyVictimTxnId);
}

void
HnfCoherencyController::unserializeSlcsfIdentityState(CheckpointIn& cp)
{
    panic_if(hasWork() || mayGenerateSlcsfIntent() ||
                 !deferredRetireQ.empty(),
             "HNF CC restore requires an idle controller\n");
    uint64_t next_request_id = 0;
    uint32_t next_mc_txn_id = 0;
    uint32_t next_snoop_txn_id = 0;
    uint32_t next_dirty_victim_txn_id = 0;
    paramIn(cp, "nextRequestId", next_request_id);
    paramIn(cp, "nextMcTransactionId", next_mc_txn_id);
    paramIn(cp, "nextSnoopTransactionId", next_snoop_txn_id);
    paramIn(cp, "nextDirtyVictimTransactionId",
            next_dirty_victim_txn_id);
    panic_if(next_request_id == 0 ||
                 next_mc_txn_id == 0 ||
                 next_mc_txn_id > McTxnIdLimit ||
                 next_dirty_victim_txn_id < DirtyVictimTxnIdBase ||
                 next_dirty_victim_txn_id > DirtyVictimTxnIdLimit ||
                 next_snoop_txn_id < SnoopTxnIdBase ||
                 next_request_id < slcSfReqIds.nextValue() ||
                 next_mc_txn_id < nextMcTxnId ||
                 next_dirty_victim_txn_id < nextDirtyVictimTxnId ||
                 next_snoop_txn_id < nextSnoopTxnId,
             "HNF CC restore has invalid/non-monotonic identity state "
             "req=%llu mc=%u snoop=%u dirtyVictim=%u\n",
             static_cast<unsigned long long>(next_request_id),
             next_mc_txn_id, next_snoop_txn_id,
             next_dirty_victim_txn_id);
    slcSfReqIds.restoreNextValue(next_request_id);
    nextMcTxnId = next_mc_txn_id;
    nextSnoopTxnId = next_snoop_txn_id;
    nextDirtyVictimTxnId = next_dirty_victim_txn_id;
}

bool
HnfCoherencyController::hasWork() const
{
    if (hasTxWork() || hasDeferredRetire()) {
        return true;
    }
    return !mcTxnToEntry.empty() || !dirtyVictimTxns.empty() ||
        seqPocqEntry.valid ||
        (slcsfUnit && slcsfUnit->hasPendingSeq()) ||
        std::any_of(entries.begin(), entries.end(),
                       [this](const Entry& entry) {
                           return entryAllocated(entry);
                       });
}

} // namespace gem5::Chi

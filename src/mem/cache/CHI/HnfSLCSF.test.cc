#include <gtest/gtest.h>

#include <cstdint>
#include <set>
#include <stdexcept>
#include <type_traits>
#include <variant>
#include <vector>

#include "mem/cache/CHI/HnfSLCSF.hh"
#include "mem/cache/CHI/HnfSLCSFRequest.hh"
#include "mem/cache/CHI/HnfSLCSFResponse.hh"

namespace gem5::Chi
{

namespace
{

constexpr uint64_t TestAddr = 0x80004000;
constexpr uint8_t CleanInvalidOpcode = 0x09;
constexpr uint8_t WriteNoSnpFullOpcode = 0x5c;

static_assert(!std::is_copy_constructible_v<HnfSLCSF>);
static_assert(!std::is_copy_assignable_v<HnfSLCSF>);
static_assert(!std::is_move_constructible_v<HnfSLCSF>);
static_assert(!std::is_move_assignable_v<HnfSLCSF>);

struct StageAHomeNodeParams
{
    size_t init_latency = 16;
    size_t slcsf_lookup_latency = 4;
    size_t slcsf_fill_latency = 4;
    size_t slcsf_update_latency = 3;
    size_t slcsf_victim_latency = 3;
    size_t slcsf_sf_evict_latency = 2;
    size_t slcsf_replay_penalty = 2;
    size_t slcsf_req_queue_entries = 8;
    size_t slcsf_resp_queue_entries = 8;
    size_t slcsf_victim_buffer_entries = 2;
    size_t slcsf_lookup_issue_width = 1;
    size_t slcsf_fill_issue_width = 1;
    size_t slcsf_update_issue_width = 1;
    size_t slcsf_max_inflight = 1;
    size_t slcsf_response_consume_width = 1;
    bool slcsf_enable_set_lock = false;
};

HnfSlcLookupResult
lookup(HnfSLCSF& model, uint64_t addr, uint32_t requester,
       PocqTxnKind txn)
{
    HnfSlcLookupReq req{};
    req.entry = 3;
    req.blockAddr = addr;
    req.req.srcid = requester;
    req.txn = txn;
    return model.lookup(req);
}

std::vector<uint8_t>
lineData(uint8_t seed)
{
    std::vector<uint8_t> data(64);
    for (size_t i = 0; i < data.size(); ++i) {
        data[i] = seed + i;
    }
    return data;
}

SlcSfRequest
lookupRequest(uint64_t req_id, uint64_t addr = TestAddr,
              uint32_t poc_entry_id = 0,
              PocqTxnKind txn = PocqTxnKind::ReadShared,
              uint32_t requester = 7)
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = poc_entry_id == 0 ? req_id : poc_entry_id;
    header.lineAddress = addr;
    header.requester = requester;
    return makeSlcSfLookupReq(
        std::move(header), txn);
}

SlcSfRequest
fillRequest(uint64_t req_id, uint64_t addr = TestAddr,
            uint32_t poc_entry_id = 0, SlcSfCommitToken token = {},
            SlcSfReqId source_lookup_req_id = {})
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = poc_entry_id == 0 ? req_id : poc_entry_id;
    header.lineAddress = addr;
    header.requester = 7;
    return makeSlcSfFillCleanSharedReq(
        std::move(header), lineData(static_cast<uint8_t>(req_id)), {},
        token, source_lookup_req_id);
}

SlcSfReqHeader
mutationHeader(uint64_t req_id, uint64_t addr = TestAddr,
               uint32_t requester = 7)
{
    SlcSfReqHeader header{};
    header.reqId = SlcSfReqId{req_id};
    header.pocEntryId = req_id;
    header.lineAddress = addr;
    header.requester = requester;
    return header;
}

enum class FlushServiceOperation : uint8_t
{
    FlushSf,
    FlushL3,
    WriteL3FlushSf
};

SlcSfRequest
flushServiceRequest(FlushServiceOperation operation, uint64_t req_id,
                    const SlcSfCommitToken& token)
{
    SlcSfReqHeader header = mutationHeader(req_id, token.lineAddress);
    switch (operation) {
      case FlushServiceOperation::FlushSf:
        return makeSlcSfFlushSfReq(
            std::move(header), token, token.lookupReqId);
      case FlushServiceOperation::FlushL3:
        return makeSlcSfFlushL3Req(
            std::move(header), token, token.lookupReqId);
      case FlushServiceOperation::WriteL3FlushSf:
        return makeSlcSfWriteL3FlushSfReq(
            std::move(header), lineData(0xa0), {}, token,
            token.lookupReqId);
    }
    throw std::logic_error("unknown flush service operation");
}

SlcSfOperationKind
flushServiceOperationKind(FlushServiceOperation operation)
{
    switch (operation) {
      case FlushServiceOperation::FlushSf:
        return SlcSfOperationKind::FlushSf;
      case FlushServiceOperation::FlushL3:
        return SlcSfOperationKind::FlushL3;
      case FlushServiceOperation::WriteL3FlushSf:
        return SlcSfOperationKind::WriteL3FlushSf;
    }
    throw std::logic_error("unknown flush service operation");
}

const SlcSfReqHeader&
requestHeader(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) -> const SlcSfReqHeader& {
            return typed_request.header;
        },
        request);
}

std::optional<SlcSfResponse>
consumeVisibleResponse(HnfSLCSF& model)
{
    const SlcSfResponse* visible = model.frontVisibleResponse();
    if (!visible) {
        return std::nullopt;
    }
    SlcSfResponse response = *visible;
    const auto* update =
        std::get_if<SlcSfUpdateResponse>(&response.payload());
    if (response.status() == SlcSfTerminalStatus::Done && update &&
        update->completionLease) {
        EXPECT_EQ(model.acknowledgeVisibleCompletion(response),
                  SlcSfCompletionAckResult::Acknowledged);
        return response;
    }
    return model.popVisibleResponse();
}

SlcSfResponse
completeLookup(HnfSLCSF& model, SlcSfRequest request)
{
    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0; cycle < 16; ++cycle) {
        model.wakeup();
        if (auto response = consumeVisibleResponse(model)) {
            return std::move(*response);
        }
    }
    throw std::runtime_error(
        "HnfSLCSF lookup did not complete in test budget");
}

SlcSfCommitToken
completeLookupToken(HnfSLCSF& model, uint64_t req_id, uint64_t addr)
{
    SlcSfResponse response = completeLookup(
        model, lookupRequest(req_id, addr));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Done);
    return std::get<SlcSfLookupResponse>(response.payload()).token;
}

SlcSfResponse
completeMutation(HnfSLCSF& model, SlcSfRequest request)
{
    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0; cycle < 32; ++cycle) {
        model.wakeup();
        if (auto response = consumeVisibleResponse(model)) {
            return std::move(*response);
        }
    }
    throw std::runtime_error(
        "HnfSLCSF mutation did not complete in test budget");
}

SlcSfResponse
awaitVisibleResponse(HnfSLCSF& model, SlcSfRequest request)
{
    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0; cycle < 32; ++cycle) {
        model.wakeup();
        if (const SlcSfResponse* response = model.frontVisibleResponse()) {
            return *response;
        }
    }
    throw std::runtime_error(
        "HnfSLCSF response was not visible in test budget");
}

SlcSfReqHeader
seqCompletionHeader(
    uint64_t req_id, const HnfSLCSF::SeqVictim& victim,
    uint32_t transaction_id)
{
    SlcSfReqHeader header =
        mutationHeader(req_id, victim.blockAddr, victim.owner);
    header.pocEntryId = UINT32_MAX;
    header.opcode = CleanInvalidOpcode;
    header.trace.linkSequence = victim.id;
    header.trace.transactionId = transaction_id;
    return header;
}

SlcSfResponse
completeIssuedSfEvict(
    HnfSLCSF& model, const HnfSLCSF::SeqVictim& victim,
    uint32_t transaction_id, uint64_t req_id,
    const std::vector<uint8_t>& data = {}, bool dirty = false)
{
    return completeMutation(
        model, makeSlcSfCompleteSfEvictReq(
            seqCompletionHeader(req_id, victim, transaction_id),
            SlcSfSeqId{victim.id}, data, dirty));
}

SlcSfReqHeader
dirtyVictimReleaseHeader(
    uint64_t req_id, SlcSfVictimId victim_id, uint64_t line_address,
    uint32_t requester, uint32_t transaction_id)
{
    SlcSfReqHeader header =
        mutationHeader(req_id, line_address, requester);
    header.pocEntryId = UINT32_MAX;
    header.opcode = WriteNoSnpFullOpcode;
    header.trace.linkSequence = victim_id.value;
    header.trace.transactionId = transaction_id;
    return header;
}

SlcSfResponse
completeDirtyVictimRelease(
    HnfSLCSF& model, SlcSfVictimId victim_id, uint64_t line_address,
    uint32_t requester, uint32_t transaction_id, uint64_t req_id)
{
    model.markDirtyVictimWritebackIssued(
        victim_id, requester, WriteNoSnpFullOpcode, transaction_id);
    return completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                req_id, victim_id, line_address, requester, transaction_id),
            victim_id));
}

void
expectLookupResultsEqual(const HnfSlcLookupResult& actual,
                         const HnfSlcLookupResult& expected)
{
    EXPECT_EQ(actual.valid, expected.valid);
    EXPECT_EQ(actual.entry, expected.entry);
    EXPECT_EQ(actual.slcHit, expected.slcHit);
    EXPECT_EQ(actual.sfHit, expected.sfHit);
    EXPECT_EQ(actual.replay, expected.replay);
    EXPECT_EQ(actual.mcreqNonspec, expected.mcreqNonspec);
    EXPECT_EQ(actual.snoopBroadcast, expected.snoopBroadcast);
    EXPECT_EQ(actual.snoopDirected, expected.snoopDirected);
    EXPECT_EQ(actual.snoopOpcode, expected.snoopOpcode);
    EXPECT_EQ(actual.snoopTargets, expected.snoopTargets);
    EXPECT_EQ(actual.rnfid, expected.rnfid);
    EXPECT_EQ(actual.rnfvec, expected.rnfvec);
    EXPECT_EQ(actual.slcState, expected.slcState);
    EXPECT_EQ(actual.sfState, expected.sfState);
    EXPECT_EQ(actual.dataDirty, expected.dataDirty);
    EXPECT_EQ(actual.data, expected.data);
}

void
expectArraySnapshotsEqual(const HnfSLCSFBackend::ArraySnapshot& actual,
                          const HnfSLCSFBackend::ArraySnapshot& expected)
{
    EXPECT_EQ(actual.hit, expected.hit);
    EXPECT_EQ(actual.set, expected.set);
    EXPECT_EQ(actual.way, expected.way);
    EXPECT_EQ(actual.generation, expected.generation);
    EXPECT_EQ(actual.replacementStamp, expected.replacementStamp);
}

void
expectSeqVictimsEqual(const HnfSLCSFBackend::SeqVictim& actual,
                      const HnfSLCSFBackend::SeqVictim& expected)
{
    EXPECT_EQ(actual.id, expected.id);
    EXPECT_EQ(actual.blockAddr, expected.blockAddr);
    EXPECT_EQ(actual.homeNodeId, expected.homeNodeId);
    EXPECT_EQ(actual.state, expected.state);
    EXPECT_EQ(actual.owner, expected.owner);
    EXPECT_EQ(actual.sharers, expected.sharers);
    EXPECT_EQ(actual.issued, expected.issued);
}

void
expectConcurrentServiceEmpty(const HnfSLCSF& model)
{
    EXPECT_EQ(model.reqIngressCount(), 0);
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respReservedCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.setLockCount(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.seqOccupancy(), 0);
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_FALSE(model.hasWork());
}

} // anonymous namespace

TEST(HnfSlcSfRequestTest, AllocatesMonotonicIdsIndependentOfLinkSequence)
{
    SlcSfReqIdAllocator ids;
    RawReq raw{};
    raw.srcid = 7;
    raw.txnid = 11;
    raw.opcode = 0x12;
    raw.qos = 3;
    raw.traceTag = true;

    const auto first = makeSlcSfReqHeader(ids, 2, TestAddr, raw, 99, 101);
    const auto second = makeSlcSfReqHeader(ids, 2, TestAddr, raw, 99, 102);

    EXPECT_TRUE(first.reqId.valid());
    EXPECT_EQ(first.reqId.value + 1, second.reqId.value);
    EXPECT_NE(first.reqId, second.reqId);
    EXPECT_EQ(first.trace.linkSequence, second.trace.linkSequence);
    EXPECT_EQ(first.pocEntryId, 2);
    EXPECT_EQ(first.lineAddress, TestAddr);
    EXPECT_EQ(first.requester, raw.srcid);
    EXPECT_EQ(first.opcode, raw.opcode);
    EXPECT_EQ(first.qos, raw.qos);
    EXPECT_EQ(first.trace.transactionId, raw.txnid);
    EXPECT_TRUE(first.trace.traceTag);
}

TEST(HnfSlcSfRequestTest, MapsEverySynchronousOperationToTypedRequest)
{
    static_assert(!std::is_same_v<SlcSfReqId, uint64_t>);
    static_assert(!std::is_same_v<SlcSfSeqId, SlcSfVictimId>);
    static_assert(!std::is_default_constructible_v<
                  SlcSfCompletionLease>);

    const SlcSfReqHeader header{
        SlcSfReqId{31}, 7, TestAddr, 9, 0x12, 3,
        SlcSfTraceContext{41, 43, 47, true}};
    const std::vector<uint8_t> data = {1, 2, 3};
    const std::vector<uint8_t> mask = {1, 0, 1};

    const SlcSfRequest lookup =
        makeSlcSfLookupReq(header, PocqTxnKind::ReadShared);
    const SlcSfRequest commitRead = makeSlcSfCommitReadReq(
        header, PocqTxnKind::ReadUnique, data, true, 0x90, mask);
    const SlcSfRequest fillCleanShared =
        makeSlcSfFillCleanSharedReq(header, data, mask);
    const SlcSfRequest completeMaintenance =
        makeSlcSfCompleteMaintenanceReq(
            header, PocqTxnKind::CleanInvalid, 0x91);
    const SlcSfRequest removeSharer = makeSlcSfRemoveSharerReq(header);
    const SlcSfRequest writeLine = makeSlcSfWriteLineReq(
        header, data, PocqTxnKind::WriteCleanFull, 0x92, mask);
    const SlcSfRequest flushSf = makeSlcSfFlushSfReq(header);
    const SlcSfRequest flushL3 = makeSlcSfFlushL3Req(header);
    const SlcSfRequest writeL3FlushSf =
        makeSlcSfWriteL3FlushSfReq(header, data, mask);
    const SlcSfRequest completeSfEvict = makeSlcSfCompleteSfEvictReq(
        header, SlcSfSeqId{53}, data, true, mask);
    const SlcSfRequest releaseDirtyVictim =
        makeSlcSfReleaseDirtyVictimReq(header, SlcSfVictimId{59});

    const auto& lookupReq = std::get<SlcSfLookupReq>(lookup);
    EXPECT_EQ(lookupReq.header.reqId, header.reqId);
    EXPECT_EQ(lookupReq.txn, PocqTxnKind::ReadShared);

    const auto& commitReq = std::get<SlcSfFillReq>(commitRead);
    EXPECT_EQ(commitReq.kind(), SlcSfUpdateKind::CommitRead);
    const auto& commit = std::get<SlcSfCommitRead>(commitReq.operation);
    EXPECT_EQ(commit.txn, PocqTxnKind::ReadUnique);
    EXPECT_EQ(commit.homeNodeId, 0x90);
    EXPECT_EQ(commit.line.data, data);
    EXPECT_EQ(commit.line.byteMask, mask);
    EXPECT_TRUE(commit.line.dirty);

    const auto& cleanReq = std::get<SlcSfFillReq>(fillCleanShared);
    EXPECT_EQ(cleanReq.kind(), SlcSfUpdateKind::FillCleanShared);
    const auto& clean =
        std::get<SlcSfFillCleanShared>(cleanReq.operation);
    EXPECT_EQ(clean.line.data, data);
    EXPECT_EQ(clean.line.byteMask, mask);
    EXPECT_FALSE(clean.line.dirty);

    const auto& maintenanceReq =
        std::get<SlcSfUpdateReq>(completeMaintenance);
    EXPECT_EQ(maintenanceReq.kind(), SlcSfUpdateKind::CompleteMaintenance);
    const auto& maintenance =
        std::get<SlcSfCompleteMaintenance>(maintenanceReq.operation);
    EXPECT_EQ(maintenance.txn, PocqTxnKind::CleanInvalid);
    EXPECT_EQ(maintenance.homeNodeId, 0x91);

    const auto& removeReq = std::get<SlcSfUpdateReq>(removeSharer);
    EXPECT_EQ(removeReq.kind(), SlcSfUpdateKind::RemoveSharer);
    EXPECT_TRUE(std::holds_alternative<SlcSfRemoveSharer>(
        removeReq.operation));

    const auto& writeReq = std::get<SlcSfFillReq>(writeLine);
    EXPECT_EQ(writeReq.kind(), SlcSfUpdateKind::WriteLine);
    const auto& write = std::get<SlcSfWriteLine>(writeReq.operation);
    EXPECT_EQ(write.txn, PocqTxnKind::WriteCleanFull);
    EXPECT_EQ(write.homeNodeId, 0x92);
    EXPECT_EQ(write.line.data, data);
    EXPECT_EQ(write.line.byteMask, mask);
    EXPECT_FALSE(write.line.dirty);

    EXPECT_EQ(std::get<SlcSfEvictReq>(flushSf).kind(),
              SlcSfUpdateKind::FlushSf);
    EXPECT_EQ(std::get<SlcSfEvictReq>(flushL3).kind(),
              SlcSfUpdateKind::FlushL3);

    const auto& writeFlushReq =
        std::get<SlcSfFillReq>(writeL3FlushSf);
    EXPECT_EQ(writeFlushReq.kind(), SlcSfUpdateKind::WriteL3FlushSf);
    const auto& writeFlush =
        std::get<SlcSfWriteL3FlushSf>(writeFlushReq.operation);
    EXPECT_EQ(writeFlush.line.data, data);
    EXPECT_EQ(writeFlush.line.byteMask, mask);
    EXPECT_TRUE(writeFlush.line.dirty);

    const auto& sfEvictReq = std::get<SlcSfUpdateReq>(completeSfEvict);
    EXPECT_EQ(sfEvictReq.kind(), SlcSfUpdateKind::CompleteSfEvict);
    const auto& sfEvict =
        std::get<SlcSfCompleteSfEvict>(sfEvictReq.operation);
    EXPECT_EQ(sfEvict.seqId.value, 53);
    EXPECT_EQ(sfEvict.snoopData.data, data);
    EXPECT_EQ(sfEvict.snoopData.byteMask, mask);
    EXPECT_TRUE(sfEvict.snoopData.dirty);

    const auto& releaseReq =
        std::get<SlcSfUpdateReq>(releaseDirtyVictim);
    EXPECT_EQ(releaseReq.kind(), SlcSfUpdateKind::ReleaseDirtyVictim);
    const auto& release =
        std::get<SlcSfReleaseDirtyVictim>(releaseReq.operation);
    EXPECT_EQ(release.victimId.value, 59);
}

TEST(HnfSlcSfRequestTest, PayloadsOwnLineMaskAndTypedVictimState)
{
    std::vector<uint8_t> line = {1, 2, 3};
    std::vector<uint8_t> mask = {1, 0, 1};
    SlcSfSlcVictim slcVictim{
        SlcSfVictimId{61}, TestAddr, HnfSlcState::MU, 7,
        SlcSfCacheLine{line, mask, true}};
    SlcSfSfVictim sfVictim{
        SlcSfSeqId{67}, TestAddr + 64, 0x90, HnfSfState::EU, 9,
        1ULL << 9};
    SlcSfVictim ownedSlc = slcVictim;
    SlcSfVictim ownedSf = sfVictim;
    const auto request = makeSlcSfCompleteSfEvictReq(
        SlcSfReqHeader{}, SlcSfSeqId{67}, line, true, mask);

    line[0] = 9;
    mask[0] = 0;
    slcVictim.line.data[0] = 8;

    const auto& slc = std::get<SlcSfSlcVictim>(ownedSlc);
    const auto& sf = std::get<SlcSfSfVictim>(ownedSf);
    EXPECT_EQ(slc.victimId.value, 61);
    EXPECT_EQ(slc.state, HnfSlcState::MU);
    EXPECT_EQ(slc.line.data[0], 1);
    EXPECT_EQ(slc.line.byteMask[0], 1);
    EXPECT_EQ(sf.seqId.value, 67);
    EXPECT_EQ(sf.state, HnfSfState::EU);
    const auto& completion =
        std::get<SlcSfCompleteSfEvict>(request.operation);
    EXPECT_EQ(completion.snoopData.data[0], 1);
    EXPECT_EQ(completion.snoopData.byteMask[0], 1);
    EXPECT_TRUE(completion.snoopData.dirty);
}

TEST(HnfSlcSfResponseTest, MapsEveryTypedRequestToConcreteOperation)
{
    const SlcSfReqHeader header{
        SlcSfReqId{71}, 73, TestAddr, 7, 0x12, 3,
        SlcSfTraceContext{79, 83, 89, true}};
    const auto lookup = makeSlcSfDoneResponse(
        makeSlcSfLookupReq(header, PocqTxnKind::ReadShared), {}, {});
    const auto commitRead = makeSlcSfDoneResponse(makeSlcSfCommitReadReq(
        header, PocqTxnKind::ReadUnique, {1}, true));
    const auto fillCleanShared = makeSlcSfDoneResponse(
        makeSlcSfFillCleanSharedReq(header, {2}));
    const auto completeMaintenance = makeSlcSfDoneResponse(
        makeSlcSfCompleteMaintenanceReq(
            header, PocqTxnKind::CleanInvalid));
    const auto removeSharer =
        makeSlcSfDoneResponse(makeSlcSfRemoveSharerReq(header));
    const auto writeLine = makeSlcSfDoneResponse(
        makeSlcSfWriteLineReq(header, {3}));
    const auto flushSf =
        makeSlcSfDoneResponse(makeSlcSfFlushSfReq(header));
    const auto flushL3 =
        makeSlcSfDoneResponse(makeSlcSfFlushL3Req(header));
    const auto writeL3FlushSf = makeSlcSfDoneResponse(
        makeSlcSfWriteL3FlushSfReq(header, {4}));
    const auto completeSfEvict = makeSlcSfDoneResponse(
        makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{97}, {5}, false));
    const auto releaseDirtyVictim = makeSlcSfDoneResponse(
        makeSlcSfReleaseDirtyVictimReq(header, SlcSfVictimId{101}));

    EXPECT_EQ(lookup.operationKind(), SlcSfOperationKind::Lookup);
    EXPECT_EQ(commitRead.operationKind(), SlcSfOperationKind::CommitRead);
    EXPECT_EQ(fillCleanShared.operationKind(),
              SlcSfOperationKind::FillCleanShared);
    EXPECT_EQ(completeMaintenance.operationKind(),
              SlcSfOperationKind::CompleteMaintenance);
    EXPECT_EQ(removeSharer.operationKind(),
              SlcSfOperationKind::RemoveSharer);
    EXPECT_EQ(writeLine.operationKind(), SlcSfOperationKind::WriteLine);
    EXPECT_EQ(flushSf.operationKind(), SlcSfOperationKind::FlushSf);
    EXPECT_EQ(flushL3.operationKind(), SlcSfOperationKind::FlushL3);
    EXPECT_EQ(writeL3FlushSf.operationKind(),
              SlcSfOperationKind::WriteL3FlushSf);
    EXPECT_EQ(completeSfEvict.operationKind(),
              SlcSfOperationKind::CompleteSfEvict);
    EXPECT_EQ(releaseDirtyVictim.operationKind(),
              SlcSfOperationKind::ReleaseDirtyVictim);

    EXPECT_EQ(lookup.reqId(), header.reqId);
    EXPECT_EQ(lookup.pocEntryId(), header.pocEntryId);
    EXPECT_EQ(lookup.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(std::get<SlcSfFillResponse>(commitRead.payload()).updateKind,
              SlcSfUpdateKind::CommitRead);
    EXPECT_EQ(std::get<SlcSfUpdateResponse>(completeSfEvict.payload())
                  .updateKind,
              SlcSfUpdateKind::CompleteSfEvict);
    EXPECT_EQ(std::get<SlcSfEvictResponse>(flushL3.payload()).updateKind,
              SlcSfUpdateKind::FlushL3);
}

TEST(HnfSlcSfResponseTest, DerivesDoneReplayAndErrorFromTypedRequest)
{
    const SlcSfReqHeader header{
        SlcSfReqId{103}, 107, TestAddr, 7, 0x12, 3, {}};
    const auto lookupReq =
        makeSlcSfLookupReq(header, PocqTxnKind::ReadShared);
    const auto fillReq = makeSlcSfCommitReadReq(
        header, PocqTxnKind::ReadUnique, {1, 2, 3}, true);
    const auto updateReq = makeSlcSfCompleteSfEvictReq(
        header, SlcSfSeqId{109}, {4, 5, 6}, false);

    const auto done = makeSlcSfDoneResponse(lookupReq, {}, {});
    const auto replayRedo = makeSlcSfReplayResponse(
        fillReq,
        SlcSfReplay{SlcSfReplayReason::StaleCommitToken, 113, true});
    const auto replayKeep = makeSlcSfReplayResponse(
        updateReq,
        SlcSfReplay{SlcSfReplayReason::ResourceConflict, 127, false});
    const auto error = makeSlcSfErrorResponse(
        updateReq,
        SlcSfError{SlcSfErrorCode::UnknownCompletion, "unknown victim"});

    EXPECT_EQ(done.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(done.operationKind(), SlcSfOperationKind::Lookup);
    EXPECT_EQ(replayRedo.status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(replayRedo.operationKind(), SlcSfOperationKind::CommitRead);
    const auto& redo = std::get<SlcSfReplay>(replayRedo.payload());
    EXPECT_EQ(redo.reason, SlcSfReplayReason::StaleCommitToken);
    EXPECT_EQ(redo.retryNotBeforeTick, 113);
    EXPECT_TRUE(redo.redoLookup);
    const auto& keep = std::get<SlcSfReplay>(replayKeep.payload());
    EXPECT_EQ(keep.retryNotBeforeTick, 127);
    EXPECT_FALSE(keep.redoLookup);

    EXPECT_EQ(error.status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(error.operationKind(), SlcSfOperationKind::CompleteSfEvict);
    const auto& errorPayload = std::get<SlcSfError>(error.payload());
    EXPECT_EQ(errorPayload.code, SlcSfErrorCode::UnknownCompletion);
    EXPECT_EQ(errorPayload.description, "unknown victim");
}

TEST(HnfSlcSfResponseTest, CommitTokenPreservesEverySnapshotField)
{
    const SlcSfCommitToken token{
        SlcSfReqId{113}, TestAddr, 127,
        SlcSfArraySnapshot{true, 2, 3, 131},
        SlcSfArraySnapshot{false, 5, 7, 137}};

    EXPECT_EQ(token.lookupReqId.value, 113);
    EXPECT_EQ(token.lineAddress, TestAddr);
    EXPECT_EQ(token.lookupEpoch, 127);
    EXPECT_TRUE(token.slc.hit);
    EXPECT_EQ(token.slc.set, 2);
    EXPECT_EQ(token.slc.way, 3);
    EXPECT_EQ(token.slc.generation, 131);
    EXPECT_FALSE(token.sf.hit);
    EXPECT_EQ(token.sf.set, 5);
    EXPECT_EQ(token.sf.way, 7);
    EXPECT_EQ(token.sf.generation, 137);
}

TEST(HnfSlcSfResponseTest, PayloadsOutliveProducingStack)
{
    const SlcSfReqHeader header{
        SlcSfReqId{139}, 149, TestAddr, 7, 0x12, 3, {}};
    const auto lookup = [&header]() {
        HnfSlcLookupResult result{};
        result.data = {1, 2, 3};
        return makeSlcSfDoneResponse(
            makeSlcSfLookupReq(header, PocqTxnKind::ReadUnique),
            std::move(result), SlcSfCommitToken{});
    }();
    const auto fill = [&header]() {
        std::vector<uint8_t> data = {4, 5, 6};
        std::vector<uint8_t> mask = {1, 0, 1};
        SlcSfSlcVictim slcVictim{
            SlcSfVictimId{151}, TestAddr, HnfSlcState::MU, 7,
            SlcSfCacheLine{data, mask, true}};
        SlcSfSfVictim sfVictim{
            SlcSfSeqId{157}, TestAddr, 0x90, HnfSfState::EU, 9,
            1ULL << 9};
        return makeSlcSfDoneResponse(
            makeSlcSfCommitReadReq(
                header, PocqTxnKind::ReadUnique, data, true, 0, mask),
            std::move(slcVictim), std::move(sfVictim));
    }();
    const auto snoopRequest = [&header]() {
        std::vector<uint8_t> data = {7, 8, 9};
        std::vector<uint8_t> mask = {0, 1, 0};
        return makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{163}, std::move(data), true,
            std::move(mask));
    }();

    const auto& lookupPayload =
        std::get<SlcSfLookupResponse>(lookup.payload());
    const auto& fillPayload = std::get<SlcSfFillResponse>(fill.payload());
    ASSERT_TRUE(fillPayload.slcVictim.has_value());
    ASSERT_TRUE(fillPayload.sfVictim.has_value());
    EXPECT_EQ(lookupPayload.result.data[0], 1);
    EXPECT_EQ(fillPayload.slcVictim->line.data[0], 4);
    EXPECT_EQ(fillPayload.slcVictim->line.byteMask[0], 1);
    EXPECT_EQ(fillPayload.sfVictim->seqId.value, 157);
    const auto& snoop =
        std::get<SlcSfCompleteSfEvict>(snoopRequest.operation).snoopData;
    EXPECT_EQ(snoop.data[0], 7);
    EXPECT_EQ(snoop.byteMask[0], 0);
    EXPECT_TRUE(snoop.dirty);
}

TEST(HnfSlcSfQueueTest, RequestNotIssuedInAcceptanceCycle)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    auto request = lookupRequest(1);

    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.reqIngressCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 1);

    model.wakeup();
    EXPECT_EQ(model.reqIngressCount(), 0);
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.reqInflightCount(), 1);
}

TEST(HnfSlcSfQueueTest, ReqQueueFullReturnsNoCreditWithoutMutation)
{
    const HnfSLCSFPipelineConfig config{2, 2, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto data = lineData(0x20);
    model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);

    auto first = lookupRequest(1);
    auto second = lookupRequest(2);
    SlcSfReqHeader rejected_header{};
    rejected_header.reqId = SlcSfReqId{3};
    rejected_header.pocEntryId = 3;
    rejected_header.lineAddress = TestAddr;
    rejected_header.requester = 0;
    SlcSfRequest rejected = makeSlcSfFlushL3Req(rejected_header);

    EXPECT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.registeredReqCredits(), 0);
    EXPECT_EQ(model.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::NoCredit);
    EXPECT_EQ(requestHeader(rejected).reqId, SlcSfReqId{3});
    EXPECT_EQ(model.reqOutstanding(), 2);
    EXPECT_EQ(model.noCreditRejectCount(), 1);
    EXPECT_EQ(model.serviceStallCount(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 0);
    EXPECT_EQ(model.respOccupied(), 0);

    const auto result = lookup(
        model, TestAddr, 4, PocqTxnKind::ReadShared);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.sfHit);
    EXPECT_EQ(result.data, data);
}

TEST(HnfSlcSfQueueTest, SameWakeupBurstConsumesRegisteredCredit)
{
    const HnfSLCSFPipelineConfig config{3, 2, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(1);
    auto second = lookupRequest(2);
    auto third = lookupRequest(3);
    auto rejected = lookupRequest(4);

    EXPECT_EQ(model.registeredReqCredits(), 3);
    EXPECT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.registeredReqCredits(), 2);
    EXPECT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.registeredReqCredits(), 1);
    EXPECT_EQ(model.tryEnqueue(std::move(third)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.registeredReqCredits(), 0);
    EXPECT_EQ(model.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::NoCredit);
    EXPECT_EQ(requestHeader(rejected).reqId, SlcSfReqId{4});

    model.wakeup();
    EXPECT_EQ(model.reqIngressCount(), 0);
    EXPECT_EQ(model.reqReadyCount(), 2);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.reqOutstanding(), 3);
    EXPECT_EQ(model.registeredReqCredits(), 0);
}

TEST(HnfSlcSfQueueTest, LifecycleRejectionsPreserveRequestOwnership)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    auto initializing_request = lookupRequest(11);
    model.beginInitialization();
    EXPECT_EQ(model.tryEnqueue(std::move(initializing_request)),
              SlcSfEnqueueResult::Initializing);
    EXPECT_EQ(requestHeader(initializing_request).reqId, SlcSfReqId{11});
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.initializingRejectCount(), 1);

    model.finishInitialization();
    model.wakeup();
    EXPECT_EQ(model.registeredReqCredits(), model.reqCapacity());
    auto draining_request = lookupRequest(12);
    model.beginDraining();
    EXPECT_EQ(model.tryEnqueue(std::move(draining_request)),
              SlcSfEnqueueResult::Draining);
    EXPECT_EQ(requestHeader(draining_request).reqId, SlcSfReqId{12});
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.drainingRejectCount(), 1);
    EXPECT_EQ(model.noCreditRejectCount(), 0);
    EXPECT_EQ(model.serviceStallCount(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 0);
}

TEST(HnfSlcSfQueueTest, RejectsInvalidQueueAndIssueConfiguration)
{
    const auto construct = [](HnfSLCSFPipelineConfig config) {
        return HnfSLCSF(64, 4, 2, 4, 2, 8, config);
    };

    EXPECT_THROW(construct({0, 1, 1, 1, 1, 1}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 0, 1, 1, 1, 1}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 1, 0, 1, 1, 1}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 1, 1, 0, 1, 1}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 1, 1, 1, 0, 1}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 1, 1, 1, 1, 0}),
                 std::invalid_argument);
    EXPECT_THROW(construct({1, 1, 2, 1, 1, 1}),
                 std::invalid_argument);
}

TEST(HnfSlcSfQueueTest, StageADefaultConfigurationIsSafe)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto& config = model.pipelineConfig();

    EXPECT_EQ(config.lookupLatency, 4);
    EXPECT_EQ(config.fillLatency, 4);
    EXPECT_EQ(config.updateLatency, 3);
    EXPECT_EQ(config.victimLatency, 3);
    EXPECT_EQ(config.sfEvictLatency, 2);
    EXPECT_EQ(config.replayPenalty, 2);
    EXPECT_EQ(config.reqQueueEntries, 8);
    EXPECT_EQ(config.respQueueEntries, 8);
    EXPECT_EQ(config.victimBufferEntries, 2);
    EXPECT_EQ(config.lookupIssueWidth, 1);
    EXPECT_EQ(config.fillIssueWidth, 1);
    EXPECT_EQ(config.updateIssueWidth, 1);
    EXPECT_EQ(config.maxInflight, 1);
    EXPECT_EQ(config.responseConsumeWidth, 1);
    EXPECT_FALSE(config.enableSetLock);
    EXPECT_EQ(config.childClockPeriod, 1);
    EXPECT_EQ(config.initLatency, 16);
}

TEST(HnfSlcSfQueueTest, HomeNodeParametersMapToPipelineConfig)
{
    StageAHomeNodeParams params{};
    auto config = makeEmbeddedSlcsfConfig(params);

    EXPECT_EQ(config.lookupLatency, 4);
    EXPECT_EQ(config.fillLatency, 4);
    EXPECT_EQ(config.updateLatency, 3);
    EXPECT_EQ(config.victimLatency, 3);
    EXPECT_EQ(config.sfEvictLatency, 2);
    EXPECT_EQ(config.replayPenalty, 2);
    EXPECT_EQ(config.reqQueueEntries, 8);
    EXPECT_EQ(config.respQueueEntries, 8);
    EXPECT_EQ(config.victimBufferEntries, 2);
    EXPECT_EQ(config.lookupIssueWidth, 1);
    EXPECT_EQ(config.fillIssueWidth, 1);
    EXPECT_EQ(config.updateIssueWidth, 1);
    EXPECT_EQ(config.maxInflight, 1);
    EXPECT_EQ(config.responseConsumeWidth, 1);
    EXPECT_FALSE(config.enableSetLock);
    EXPECT_EQ(config.initLatency, 16);

    params.init_latency = 25;
    params.slcsf_lookup_latency = 11;
    params.slcsf_fill_latency = 12;
    params.slcsf_update_latency = 13;
    params.slcsf_victim_latency = 14;
    params.slcsf_sf_evict_latency = 15;
    params.slcsf_replay_penalty = 16;
    params.slcsf_req_queue_entries = 17;
    params.slcsf_resp_queue_entries = 18;
    params.slcsf_victim_buffer_entries = 19;
    params.slcsf_lookup_issue_width = 20;
    params.slcsf_fill_issue_width = 21;
    params.slcsf_update_issue_width = 22;
    params.slcsf_max_inflight = 5;
    params.slcsf_response_consume_width = 24;
    params.slcsf_enable_set_lock = true;
    config = makeEmbeddedSlcsfConfig(params, 8);

    EXPECT_EQ(config.lookupLatency, 11);
    EXPECT_EQ(config.fillLatency, 12);
    EXPECT_EQ(config.updateLatency, 13);
    EXPECT_EQ(config.victimLatency, 14);
    EXPECT_EQ(config.sfEvictLatency, 15);
    EXPECT_EQ(config.replayPenalty, 16);
    EXPECT_EQ(config.reqQueueEntries, 17);
    EXPECT_EQ(config.respQueueEntries, 18);
    EXPECT_EQ(config.victimBufferEntries, 19);
    EXPECT_EQ(config.lookupIssueWidth, 20);
    EXPECT_EQ(config.fillIssueWidth, 21);
    EXPECT_EQ(config.updateIssueWidth, 22);
    EXPECT_EQ(config.maxInflight, 5);
    EXPECT_EQ(config.responseConsumeWidth, 24);
    EXPECT_TRUE(config.enableSetLock);
    EXPECT_EQ(config.childClockPeriod, 8);
    EXPECT_EQ(config.initLatency, 25);

    HnfSLCSF overridden(64, 4, 2, 4, 2, 8, config);
    EXPECT_EQ(overridden.pipelineConfig().lookupLatency, 11);
    EXPECT_EQ(overridden.pipelineConfig().maxInflight, 5);
    EXPECT_TRUE(overridden.pipelineConfig().enableSetLock);
}

TEST(HnfSlcSfQueueTest, RejectsInvalidStageAServiceConfiguration)
{
    const auto expectInvalid = [](HnfSLCSFPipelineConfig config) {
        EXPECT_THROW(HnfSLCSF(64, 4, 2, 4, 2, 8, config),
                     std::invalid_argument);
    };
    HnfSLCSFPipelineConfig config{};

    config.fillLatency = 0;
    expectInvalid(config);
    config = {};
    config.updateLatency = 0;
    expectInvalid(config);
    config = {};
    config.victimLatency = 0;
    expectInvalid(config);
    config = {};
    config.sfEvictLatency = 0;
    expectInvalid(config);
    config = {};
    config.replayPenalty = 0;
    expectInvalid(config);
    config = {};
    config.childClockPeriod = 0;
    expectInvalid(config);
    config = {};
    config.victimBufferEntries = 0;
    expectInvalid(config);
    config = {};
    config.responseConsumeWidth = 0;
    expectInvalid(config);
    config = {};
    config.maxInflight = 2;
    expectInvalid(config);
}

TEST(HnfSlcSfSetLockTest, SameSetOperationsSerialize)
{
    HnfSLCSFSetLockManager locks(4, 4);
    ASSERT_TRUE(locks.tryAcquire(
        1, SlcSfSetLockRequest{0, std::nullopt,
                               SlcSfSetLockMode::Read}));
    EXPECT_FALSE(locks.tryAcquire(
        2, SlcSfSetLockRequest{0, std::nullopt,
                               SlcSfSetLockMode::Write}));
    EXPECT_TRUE(locks.tryAcquire(
        2, SlcSfSetLockRequest{1, std::nullopt,
                               SlcSfSetLockMode::Write}));
    EXPECT_EQ(locks.heldLockCount(), 2);
    locks.release(1);
    locks.release(2);
    EXPECT_EQ(locks.heldLockCount(), 0);
}

TEST(HnfSlcSfSetLockTest, CrossedAcquisitionIsAtomicAndDeadlockFree)
{
    HnfSLCSFSetLockManager locks(4, 4);
    ASSERT_TRUE(locks.tryAcquire(
        1, SlcSfSetLockRequest{0, 1, SlcSfSetLockMode::Write}));
    EXPECT_FALSE(locks.tryAcquire(
        2, SlcSfSetLockRequest{2, 1, SlcSfSetLockMode::Write}));
    EXPECT_EQ(locks.heldLockCount(), 2);
    EXPECT_FALSE(locks.holds(2));
    locks.release(1);
    ASSERT_TRUE(locks.tryAcquire(
        2, SlcSfSetLockRequest{2, 1, SlcSfSetLockMode::Write}));
    locks.release(2);
    EXPECT_EQ(locks.heldLockCount(), 0);
}

TEST(HnfSlcSfSetLockTest, LookupAndUpdateSameSetMutuallyExclude)
{
    HnfSLCSFPipelineConfig config{};
    config.maxInflight = 2;
    config.lookupLatency = 4;
    config.updateLatency = 1;
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto token = completeLookupToken(model, 69, TestAddr);
    auto lookup_request = lookupRequest(70, TestAddr);
    auto update_request = makeSlcSfRemoveSharerReq(
        mutationHeader(71, TestAddr), token, token.lookupReqId);

    ASSERT_EQ(model.tryEnqueue(std::move(lookup_request)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(update_request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.setLockCount(), 2);

    for (size_t cycle = 0; cycle < 16 && model.reqOutstanding(); ++cycle) {
        model.wakeup();
        while (model.popVisibleResponse()) {}
    }
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.setLockCount(), 0);
}

TEST(HnfSlcSfSetLockTest, StalledLockAcquisitionLeaksNoLock)
{
    HnfSLCSFPipelineConfig config{};
    config.maxInflight = 2;
    config.lookupLatency = 3;
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(72, TestAddr);
    auto stalled = lookupRequest(73, TestAddr);
    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(stalled)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.setLockCount(), 2);
    model.wakeup();
    EXPECT_EQ(model.setLockCount(), 2);

    EXPECT_EQ(model.cancelRequest(72, SlcSfReqId{72}, 100),
              SlcSfCancelResult::Cancelled);
    EXPECT_EQ(model.setLockCount(), 0);
    model.wakeup(101);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.setLockCount(), 2);
    for (size_t cycle = 0; cycle < 8 && model.reqOutstanding(); ++cycle) {
        model.wakeup(102 + cycle);
    }
    EXPECT_EQ(model.setLockCount(), 0);
}

TEST(HnfSlcSfSetLockTest, EveryTerminalPathReleasesLocksExactlyOnce)
{
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 1;
    config.updateLatency = 1;
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto complete = [&model](SlcSfRequest request) {
        EXPECT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);
        model.wakeup();
        EXPECT_EQ(model.setLockCount(), 2);
        for (size_t cycle = 0; cycle < 8 && model.reqOutstanding(); ++cycle) {
            model.wakeup();
        }
        EXPECT_EQ(model.reqOutstanding(), 0);
        EXPECT_EQ(model.setLockCount(), 0);
        model.wakeup();
        return model.popVisibleResponse();
    };

    auto done = complete(lookupRequest(74, TestAddr));
    ASSERT_TRUE(done.has_value());
    EXPECT_EQ(done->status(), SlcSfTerminalStatus::Done);

    auto replay = complete(fillRequest(75, TestAddr));
    ASSERT_TRUE(replay.has_value());
    EXPECT_EQ(replay->status(), SlcSfTerminalStatus::Replay);

    auto error = complete(makeSlcSfCommitReadReq(
        mutationHeader(76, TestAddr), PocqTxnKind::Unknown,
        lineData(0x76), false));
    ASSERT_TRUE(error.has_value());
    EXPECT_EQ(error->status(), SlcSfTerminalStatus::Error);

    auto cancelled = lookupRequest(77, TestAddr);
    ASSERT_EQ(model.tryEnqueue(std::move(cancelled)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    ASSERT_EQ(model.setLockCount(), 2);
    EXPECT_EQ(model.cancelRequest(77, SlcSfReqId{77}, 200),
              SlcSfCancelResult::Cancelled);
    EXPECT_EQ(model.setLockCount(), 0);
}

TEST(HnfSlcSfConcurrencyTest, DifferentSetsCompleteConcurrently)
{
    HnfSLCSFPipelineConfig config{};
    config.maxInflight = 2;
    config.lookupIssueWidth = 2;
    config.lookupLatency = 4;
    config.enableSetLock = true;
    HnfSLCSF model(64, 8, 2, 8, 2, 8, config);
    auto first = lookupRequest(261, TestAddr, 2601);
    auto second = lookupRequest(262, TestAddr + 64, 2602);

    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    ASSERT_EQ(model.reqInflightCount(), 2);
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.setLockCount(), 4);

    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 2);
    EXPECT_EQ(model.setLockCount(), 4);

    std::set<std::pair<uint32_t, uint64_t>> completions;
    for (size_t cycle = 0; cycle < 16 && completions.size() != 2; ++cycle) {
        model.wakeup();
        while (auto response = consumeVisibleResponse(model)) {
            EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
            completions.emplace(
                response->pocEntryId(), response->reqId().value);
        }
    }
    EXPECT_EQ(completions,
              (std::set<std::pair<uint32_t, uint64_t>>{
                  {2601, 261}, {2602, 262}}));
    expectConcurrentServiceEmpty(model);
}

TEST(HnfSlcSfConcurrencyTest, MaxInflightAndIssueWidthAreEnforced)
{
    const auto drain = [](HnfSLCSF& model, size_t expected) {
        std::set<uint64_t> completions;
        for (size_t cycle = 0;
             cycle < 64 && completions.size() != expected; ++cycle) {
            model.wakeup();
            while (auto response = consumeVisibleResponse(model)) {
                EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
                completions.insert(response->reqId().value);
            }
        }
        EXPECT_EQ(completions.size(), expected);
        expectConcurrentServiceEmpty(model);
    };

    {
        HnfSLCSFPipelineConfig config{};
        config.maxInflight = 4;
        config.lookupIssueWidth = 2;
        config.enableSetLock = true;
        HnfSLCSF model(64, 8, 2, 8, 2, 8, config);
        for (uint64_t i = 0; i < 4; ++i) {
            auto request = lookupRequest(
                270 + i, TestAddr + i * 64, 2700 + i);
            ASSERT_EQ(model.tryEnqueue(std::move(request)),
                      SlcSfEnqueueResult::Accepted);
        }
        model.wakeup();
        EXPECT_EQ(model.reqInflightCount(), 2);
        EXPECT_EQ(model.reqReadyCount(), 2);
        drain(model, 4);
    }

    {
        HnfSLCSFPipelineConfig config{};
        config.maxInflight = 4;
        config.fillIssueWidth = 1;
        config.enableSetLock = true;
        HnfSLCSF model(64, 8, 2, 8, 2, 8, config);
        const auto first_token =
            completeLookupToken(model, 280, TestAddr);
        const auto second_token =
            completeLookupToken(model, 281, TestAddr + 64);
        auto first = fillRequest(
            282, TestAddr, 2802, first_token, first_token.lookupReqId);
        auto second = fillRequest(
            283, TestAddr + 64, 2803, second_token,
            second_token.lookupReqId);
        ASSERT_EQ(model.tryEnqueue(std::move(first)),
                  SlcSfEnqueueResult::Accepted);
        ASSERT_EQ(model.tryEnqueue(std::move(second)),
                  SlcSfEnqueueResult::Accepted);
        model.wakeup();
        EXPECT_EQ(model.reqInflightCount(), 1);
        EXPECT_EQ(model.reqReadyCount(), 1);
        drain(model, 2);
    }

    {
        HnfSLCSFPipelineConfig config{};
        config.maxInflight = 4;
        config.updateIssueWidth = 1;
        config.enableSetLock = true;
        HnfSLCSF model(64, 8, 2, 8, 2, 8, config);
        const auto first_token =
            completeLookupToken(model, 290, TestAddr);
        const auto second_token =
            completeLookupToken(model, 291, TestAddr + 64);
        auto first = makeSlcSfRemoveSharerReq(
            mutationHeader(292, TestAddr), first_token,
            first_token.lookupReqId);
        auto second = makeSlcSfRemoveSharerReq(
            mutationHeader(293, TestAddr + 64), second_token,
            second_token.lookupReqId);
        ASSERT_EQ(model.tryEnqueue(std::move(first)),
                  SlcSfEnqueueResult::Accepted);
        ASSERT_EQ(model.tryEnqueue(std::move(second)),
                  SlcSfEnqueueResult::Accepted);
        model.wakeup();
        EXPECT_EQ(model.reqInflightCount(), 1);
        EXPECT_EQ(model.reqReadyCount(), 1);
        drain(model, 2);
    }

    {
        HnfSLCSFPipelineConfig config{};
        config.maxInflight = 2;
        config.lookupIssueWidth = 4;
        config.enableSetLock = true;
        HnfSLCSF model(64, 8, 2, 8, 2, 8, config);
        for (uint64_t i = 0; i < 4; ++i) {
            auto request = lookupRequest(
                300 + i, TestAddr + i * 64, 3000 + i);
            ASSERT_EQ(model.tryEnqueue(std::move(request)),
                      SlcSfEnqueueResult::Accepted);
        }
        model.wakeup();
        EXPECT_EQ(model.reqInflightCount(), 2);
        EXPECT_EQ(model.reqReadyCount(), 2);
        drain(model, 4);
    }
}

TEST(HnfSlcSfConcurrencyTest,
     CrossedSlcSfSetsMakeProgressWithOutOfOrderResponses)
{
    HnfSLCSFPipelineConfig config{};
    config.reqQueueEntries = 4;
    config.respQueueEntries = 4;
    config.maxInflight = 3;
    config.lookupIssueWidth = 3;
    config.fillIssueWidth = 1;
    config.lookupLatency = 6;
    config.fillLatency = 2;
    config.enableSetLock = true;
    HnfSLCSF model(64, 2, 4, 3, 4, 8, config);

    // With the 2-set SLC and 3-set SF, these acquire crossed pairs:
    // first=(SLC 0, SF 1), second=(SLC 1, SF 0). The third request needs
    // (SLC 0, SF 0), so it must acquire neither resource until both owners
    // have made progress and released their atomic pairs.
    const uint64_t first_addr = TestAddr + 4 * 64;
    const uint64_t second_addr = TestAddr + 3 * 64;
    const uint64_t third_addr = TestAddr;
    const auto second_token =
        completeLookupToken(model, 310, second_addr);
    auto first = lookupRequest(311, first_addr, 3101);
    auto second = fillRequest(
        312, second_addr, 3102, second_token,
        second_token.lookupReqId);
    auto third = lookupRequest(313, third_addr, 3103);

    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(third)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    ASSERT_EQ(model.reqInflightCount(), 2);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.setLockCount(), 4);

    std::vector<std::pair<uint32_t, uint64_t>> completion_order;
    for (size_t cycle = 0;
         cycle < 32 && completion_order.size() != 3; ++cycle) {
        model.wakeup();
        while (auto response = consumeVisibleResponse(model)) {
            EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
            completion_order.emplace_back(
                response->pocEntryId(), response->reqId().value);
        }
    }
    ASSERT_EQ(completion_order.size(), 3);
    EXPECT_EQ(completion_order[0],
              (std::pair<uint32_t, uint64_t>{3102, 312}));
    EXPECT_EQ(completion_order[1],
              (std::pair<uint32_t, uint64_t>{3101, 311}));
    EXPECT_EQ(completion_order[2],
              (std::pair<uint32_t, uint64_t>{3103, 313}));
    expectConcurrentServiceEmpty(model);
}

TEST(HnfSlcSfQueueTest, CompletedResponseNotVisibleUntilNextCycle)
{
    const HnfSLCSFPipelineConfig config{8, 8, 1, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto request = lookupRequest(21);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.respReservedCount(), 1);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_EQ(model.respOccupied(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());

    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.respReservedCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_EQ(model.respOccupied(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());

    model.wakeup();
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_EQ(model.respVisibleCount(), 1);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{21});
    EXPECT_EQ(response->pocEntryId(), 21);
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfQueueTest, AcceptedRequestProducesExactlyOneTerminalResponse)
{
    HnfSLCSFPipelineConfig config{3, 3, 3, 3, 1, 1, 1};
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(31, TestAddr);
    auto second = lookupRequest(32, TestAddr + 64);
    auto third = lookupRequest(33, TestAddr + 128);

    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(third)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    EXPECT_EQ(model.respReservedCount(), 3);
    EXPECT_EQ(model.respOccupied(), model.respCapacity());
    model.wakeup();
    EXPECT_EQ(model.respReservedCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 3);
    EXPECT_EQ(model.reqOutstanding(), 0);
    model.wakeup();
    EXPECT_EQ(model.respVisibleCount(), 3);

    for (uint64_t id = 31; id <= 33; ++id) {
        auto response = model.popVisibleResponse();
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(response->reqId(), SlcSfReqId{id});
        EXPECT_EQ(response->pocEntryId(), id);
        EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    }
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfQueueTest, ResponseBackpressureDoesNotDropOrDuplicate)
{
    HnfSLCSFPipelineConfig config{2, 1, 2, 2, 1, 1, 1};
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(41);
    auto second = lookupRequest(42);

    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.respReservedCount(), 1);

    model.wakeup();
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.reqInflightCount(), 0);
    model.wakeup();
    EXPECT_EQ(model.respVisibleCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);

    auto first_response = model.popVisibleResponse();
    ASSERT_TRUE(first_response.has_value());
    EXPECT_EQ(first_response->reqId(), SlcSfReqId{41});
    EXPECT_EQ(model.respOccupied(), 0);

    model.wakeup();
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.respReservedCount(), 1);
    model.wakeup();
    EXPECT_EQ(model.respPendingCount(), 1);
    model.wakeup();
    auto second_response = model.popVisibleResponse();
    ASSERT_TRUE(second_response.has_value());
    EXPECT_EQ(second_response->reqId(), SlcSfReqId{42});
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfQueueTest, RejectedRequestProducesNoResponse)
{
    const HnfSLCSFPipelineConfig config{1, 1, 1, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto accepted = lookupRequest(51);
    auto rejected = lookupRequest(52);

    ASSERT_EQ(model.tryEnqueue(std::move(accepted)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::NoCredit);
    EXPECT_EQ(requestHeader(rejected).reqId, SlcSfReqId{52});

    model.wakeup();
    model.wakeup();
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{51});
    EXPECT_FALSE(model.popVisibleResponse().has_value());
}

TEST(HnfSlcSfQueueTest,
     QueuedRequestsAreNotCancellableAndStillCompleteExactlyOnce)
{
    const HnfSLCSFPipelineConfig config{2, 2, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first = lookupRequest(53);
    auto second = lookupRequest(54);
    ASSERT_EQ(model.tryEnqueue(std::move(first)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.cancelRequest(53, SlcSfReqId{53}, 1000),
              SlcSfCancelResult::NotCancellable);
    EXPECT_EQ(model.reqIngressCount(), 2);
    EXPECT_EQ(model.respOccupied(), 0);

    model.wakeup(1010);
    ASSERT_EQ(model.reqInflightCount(), 1);
    ASSERT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.cancelRequest(54, SlcSfReqId{54}, 1010),
              SlcSfCancelResult::NotCancellable);
    EXPECT_EQ(model.reqOutstanding(), 2);

    std::set<uint64_t> terminal_ids;
    for (Tick tick = 1020; tick < 1200 && terminal_ids.size() < 2;
         tick += 10) {
        model.wakeup(tick);
        while (auto response = model.popVisibleResponse()) {
            EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
            EXPECT_TRUE(terminal_ids.insert(response->reqId().value).second);
        }
    }
    EXPECT_EQ(terminal_ids, (std::set<uint64_t>{53, 54}));
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.cancelledRequestCount(), 0);
}

TEST(HnfSlcSfQueueTest, TerminalPendingCannotBeCancelledOrDuplicated)
{
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 1;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto request = lookupRequest(55);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(2000);
    model.wakeup(2010);
    ASSERT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.cancelRequest(55, SlcSfReqId{55}, 2010),
              SlcSfCancelResult::TooLate);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.finishedRequestCount(), 1);

    model.wakeup(2020);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{55});
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    EXPECT_EQ(model.cancelledRequestCount(), 0);
}

TEST(HnfSlcSfQueueTest, MixedPipesCompleteOutOfAcceptanceOrder)
{
    HnfSLCSFPipelineConfig config{3, 3, 2, 1, 1, 1, 6};
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first_lookup = lookupRequest(61, TestAddr, 601);
    auto second_lookup = lookupRequest(62, TestAddr + 128, 602);
    const uint64_t fill_addr = TestAddr + 64;
    const auto fill_token = completeLookupToken(model, 600, fill_addr);
    auto fill = fillRequest(
        63, fill_addr, 603, fill_token, fill_token.lookupReqId);

    ASSERT_EQ(model.tryEnqueue(std::move(first_lookup)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(second_lookup)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(fill)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.reqInflightCount(), 2);
    EXPECT_EQ(model.respReservedCount(), 2);

    model.wakeup();
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.reqReadyCount(), 0);
    EXPECT_EQ(model.reqInflightCount(), 2);
    EXPECT_EQ(model.respPendingCount(), 1);

    model.wakeup();
    ASSERT_EQ(model.respVisibleCount(), 1);
    std::set<std::pair<uint64_t, uint64_t>> first_completions;
    while (auto response = model.popVisibleResponse()) {
        first_completions.emplace(
            response->pocEntryId(), response->reqId().value);
    }
    EXPECT_EQ(first_completions,
              (std::set<std::pair<uint64_t, uint64_t>>{
                  {603, 63}}));

    std::optional<SlcSfResponse> first_lookup_response;
    for (size_t cycle = 0; cycle < 8 && !first_lookup_response; ++cycle) {
        model.wakeup();
        first_lookup_response = model.popVisibleResponse();
    }
    ASSERT_TRUE(first_lookup_response.has_value());
    EXPECT_EQ(first_lookup_response->pocEntryId(), 601);
    EXPECT_EQ(first_lookup_response->reqId(), SlcSfReqId{61});
    std::optional<SlcSfResponse> second_lookup_response;
    for (size_t cycle = 0; cycle < 8 && !second_lookup_response; ++cycle) {
        model.wakeup();
        second_lookup_response = model.popVisibleResponse();
    }
    ASSERT_TRUE(second_lookup_response.has_value());
    EXPECT_EQ(second_lookup_response->pocEntryId(), 602);
    EXPECT_EQ(second_lookup_response->reqId(), SlcSfReqId{62});
    EXPECT_FALSE(model.popVisibleResponse().has_value());
}

enum class StagedMutationCase
{
    CommitRead,
    MemoryFill,
    Maintenance,
    RemoveSharer,
    WriteLine
};

class HnfSlcSfMutationServiceTest :
    public testing::TestWithParam<StagedMutationCase>
{};

TEST_P(HnfSlcSfMutationServiceTest, DispatchesThroughU0U1U2U3)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    switch (GetParam()) {
      case StagedMutationCase::Maintenance:
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadUnique,
            lineData(0x33), false, 0x90);
        break;
      case StagedMutationCase::RemoveSharer:
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared,
            lineData(0x44), false, 0x90);
        break;
      default:
        break;
    }

    const auto token = completeLookupToken(model, 1000, TestAddr);
    SlcSfRequest request;
    switch (GetParam()) {
      case StagedMutationCase::CommitRead:
        request = makeSlcSfCommitReadReq(
            mutationHeader(101), PocqTxnKind::ReadShared,
            lineData(0x11), true, 0x90, {}, token, token.lookupReqId);
        break;
      case StagedMutationCase::MemoryFill:
        request = makeSlcSfFillCleanSharedReq(
            mutationHeader(102), lineData(0x22), {}, token,
            token.lookupReqId);
        break;
      case StagedMutationCase::Maintenance:
        request = makeSlcSfCompleteMaintenanceReq(
            mutationHeader(103), PocqTxnKind::MakeInvalid, 0x90, token,
            token.lookupReqId);
        break;
      case StagedMutationCase::RemoveSharer:
        request = makeSlcSfRemoveSharerReq(
            mutationHeader(104), token, token.lookupReqId);
        break;
      case StagedMutationCase::WriteLine:
        request = makeSlcSfWriteLineReq(
            mutationHeader(105), lineData(0x55),
            PocqTxnKind::WriteUnique, 0x90, {}, token,
            token.lookupReqId);
        break;
    }

    const SlcSfReqHeader submitted_header = requestHeader(request);
    const auto before_stages = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const auto expect_no_stage_write = [&model, &before_stages]() {
        const auto current = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        expectArraySnapshotsEqual(
            current.snapshot.slc, before_stages.snapshot.slc);
        expectArraySnapshotsEqual(
            current.snapshot.sf, before_stages.snapshot.sf);
    };

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U0DecodeValidate), 1);
    expect_no_stage_write();
    const bool fill_pipe =
        GetParam() == StagedMutationCase::CommitRead ||
        GetParam() == StagedMutationCase::MemoryFill ||
        GetParam() == StagedMutationCase::WriteLine;
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    expect_no_stage_write();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    expect_no_stage_write();
    const auto before_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    model.wakeup();
    if (fill_pipe) {
        EXPECT_EQ(model.mutationStageCount(
                      HnfSLCSF::MutationStage::U2ArrayWrite), 1);
        EXPECT_TRUE(model.hasSfReservation(submitted_header.pocEntryId));
        expect_no_stage_write();
        model.wakeup();
    }
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.hasSfReservation(submitted_header.pocEntryId));
    const auto after_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_TRUE(
        after_write.snapshot.slc.generation !=
            before_write.snapshot.slc.generation ||
        after_write.snapshot.sf.generation !=
            before_write.snapshot.sf.generation);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(response->reqId(), submitted_header.reqId);
    EXPECT_EQ(response->pocEntryId(), submitted_header.pocEntryId);

    const auto observation = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    switch (GetParam()) {
      case StagedMutationCase::CommitRead:
        EXPECT_TRUE(observation.result.slcHit);
        EXPECT_TRUE(observation.result.sfHit);
        EXPECT_TRUE(observation.result.dataDirty);
        EXPECT_EQ(observation.result.slcState, HnfSlcState::MN);
        EXPECT_EQ(observation.result.sfState, HnfSfState::SN);
        break;
      case StagedMutationCase::MemoryFill:
        EXPECT_TRUE(observation.result.slcHit);
        EXPECT_TRUE(observation.result.sfHit);
        EXPECT_FALSE(observation.result.dataDirty);
        EXPECT_EQ(observation.result.slcState, HnfSlcState::EN);
        EXPECT_EQ(observation.result.sfState, HnfSfState::EN);
        break;
      case StagedMutationCase::Maintenance:
        EXPECT_FALSE(observation.result.slcHit);
        EXPECT_FALSE(observation.result.sfHit);
        break;
      case StagedMutationCase::RemoveSharer:
        EXPECT_TRUE(observation.result.slcHit);
        EXPECT_FALSE(observation.result.sfHit);
        break;
      case StagedMutationCase::WriteLine:
        EXPECT_TRUE(observation.result.slcHit);
        EXPECT_FALSE(observation.result.sfHit);
        EXPECT_EQ(observation.result.data, lineData(0x55));
        break;
    }
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

INSTANTIATE_TEST_SUITE_P(
    AvailableHandlers, HnfSlcSfMutationServiceTest,
    testing::Values(
        StagedMutationCase::CommitRead, StagedMutationCase::MemoryFill,
        StagedMutationCase::Maintenance, StagedMutationCase::RemoveSharer,
        StagedMutationCase::WriteLine));

TEST(HnfSlcSfMutationServiceTest, DecodeFailureHasNoMutationOrReservation)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    SlcSfRequest request = makeSlcSfCommitReadReq(
        mutationHeader(110), PocqTxnKind::Unknown, lineData(0x66), false);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U3CheckLatch), 1);
    EXPECT_EQ(model.seqReservationCount(), 0);
    const auto after_decode = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectArraySnapshotsEqual(after_decode.snapshot.slc,
                              before.snapshot.slc);
    expectArraySnapshotsEqual(after_decode.snapshot.sf,
                              before.snapshot.sf);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(std::get<SlcSfError>(response->payload()).code,
              SlcSfErrorCode::InvalidRequest);
}

TEST(HnfSlcSfMutationServiceTest, StaleTokenReplaysWithoutWrite)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 7;
    HnfSLCSF model(64, 1, 1, 1, 1, 8, config);
    model.commitRead(
        TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x61), false);
    const auto stale_token = completeLookupToken(model, 1300, TestAddr);

    // Intervening storage mutation makes the lookup snapshot stale before U0.
    model.writeLine(
        TestAddr, 7, lineData(0x62), PocqTxnKind::WriteUnique);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const uint64_t epoch_before = model.currentLookupEpoch();
    const uint64_t accesses_before = model.currentLookupAccessCount();
    const size_t seq_before = model.seqOccupancy();
    const size_t seq_reservations_before = model.seqReservationCount();

    SlcSfRequest request = makeSlcSfCommitReadReq(
        mutationHeader(1301), PocqTxnKind::ReadShared,
        lineData(0x63), true, 0x90, {}, stale_token,
        stale_token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    model.wakeup(1010);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.hasSfReservation(1301));

    const auto after_u0 = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after_u0.result, before.result);
    expectArraySnapshotsEqual(after_u0.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after_u0.snapshot.sf, before.snapshot.sf);
    EXPECT_EQ(model.currentLookupEpoch(), epoch_before);
    EXPECT_EQ(model.currentLookupAccessCount(), accesses_before);
    EXPECT_EQ(model.seqOccupancy(), seq_before);
    EXPECT_EQ(model.seqReservationCount(), seq_reservations_before);

    model.wakeup(1020);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(response->reqId(), SlcSfReqId{1301});
    const auto& replay = std::get<SlcSfReplay>(response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::StaleCommitToken);
    EXPECT_EQ(replay.retryNotBeforeTick, 1017);
    EXPECT_TRUE(replay.redoLookup);

    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after.result, before.result);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
    EXPECT_EQ(model.currentLookupEpoch(), epoch_before);
    EXPECT_EQ(model.currentLookupAccessCount(), accesses_before);
    EXPECT_EQ(model.seqOccupancy(), seq_before);
    EXPECT_EQ(model.seqReservationCount(), seq_reservations_before);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest, FreshTokenCommitsThroughU2)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const auto token = completeLookupToken(model, 1310, TestAddr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1311), lineData(0x64), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto observation = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_TRUE(observation.result.slcHit);
    EXPECT_TRUE(observation.result.sfHit);
    EXPECT_EQ(observation.result.data, lineData(0x64));
}

TEST(HnfSlcSfMutationServiceTest,
     FillVictimAddsLatencyAndReturnsOwnedSeqSnapshot)
{
    HnfSLCSFPipelineConfig config{};
    config.fillLatency = 5;
    config.sfEvictLatency = 3;
    HnfSLCSF model(64, 1, 2, 1, 1, 1, config);
    const uint64_t victim_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    model.commitRead(
        victim_addr, 3, PocqTxnKind::ReadUnique, lineData(0x72), false,
        0x90);
    const auto token =
        completeLookupToken(model, 1312, replacement_addr);
    ASSERT_FALSE(token.sf.hit);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1313, replacement_addr, 7), lineData(0x73), {},
        token, token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();

    while (model.respPendingCount() == 0) {
        model.wakeup();
        EXPECT_LE(
            model.seqOccupancy() + model.seqReservationCount(),
            model.seqCapacity());
        ASSERT_LE(
            model.currentCycle(),
            issue_cycle + config.fillLatency + config.sfEvictLatency);
    }
    EXPECT_EQ(
        model.currentCycle(),
        issue_cycle + config.fillLatency + config.sfEvictLatency);
    EXPECT_EQ(model.seqOccupancy(), 1);
    EXPECT_EQ(model.seqReservationCount(), 0);
    const auto backend_victim = model.frontPendingSeq();

    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& fill =
        std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.sfVictim.has_value());
    const auto& victim = *fill.sfVictim;
    EXPECT_EQ(victim.seqId.value, backend_victim.id);
    EXPECT_EQ(victim.lineAddress, victim_addr);
    EXPECT_EQ(victim.homeNodeId, 0x90);
    EXPECT_EQ(victim.state, HnfSfState::EU);
    EXPECT_EQ(victim.owner, 3);
    EXPECT_EQ(victim.sharers, 1ULL << 3);
}

TEST(HnfSlcSfMutationServiceTest,
     UpdateVictimAddsLatencyAndReturnsOwnedSeqSnapshot)
{
    HnfSLCSFPipelineConfig config{};
    config.updateLatency = 4;
    config.sfEvictLatency = 2;
    HnfSLCSF model(64, 1, 2, 1, 1, 1, config);
    const uint64_t victim_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    model.commitRead(
        victim_addr, 5, PocqTxnKind::ReadShared, lineData(0x74), false,
        0x91);
    const auto token =
        completeLookupToken(model, 1314, replacement_addr);
    SlcSfRequest request = makeSlcSfCompleteMaintenanceReq(
        mutationHeader(1315, replacement_addr, 9),
        PocqTxnKind::MakeUnique, 0x92, token, token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();

    while (model.respPendingCount() == 0) {
        model.wakeup();
        EXPECT_LE(
            model.seqOccupancy() + model.seqReservationCount(),
            model.seqCapacity());
        ASSERT_LE(
            model.currentCycle(),
            issue_cycle + config.updateLatency + config.sfEvictLatency);
    }
    EXPECT_EQ(
        model.currentCycle(),
        issue_cycle + config.updateLatency + config.sfEvictLatency);
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& update =
        std::get<SlcSfUpdateResponse>(response->payload());
    ASSERT_TRUE(update.sfVictim.has_value());
    EXPECT_EQ(update.sfVictim->seqId.value, model.frontPendingSeq().id);
    EXPECT_EQ(update.sfVictim->lineAddress, victim_addr);
    EXPECT_EQ(update.sfVictim->homeNodeId, 0x91);
    EXPECT_EQ(update.sfVictim->state, HnfSfState::EN);
    EXPECT_EQ(update.sfVictim->owner, 5);
    EXPECT_EQ(update.sfVictim->sharers, 1ULL << 5);
}

TEST(HnfSlcSfMutationServiceTest,
     SeqCapacityRaceReplaysWithoutOverwritingVictim)
{
    HnfSLCSFPipelineConfig config{};
    config.replayPenalty = 3;
    HnfSLCSF model(64, 2, 2, 2, 1, 1, config);
    const uint64_t victim_addr = TestAddr;
    const uint64_t other_victim_addr = TestAddr + 64;
    const uint64_t replacement_addr = TestAddr + 128;
    const uint64_t other_replacement_addr = TestAddr + 192;
    model.commitRead(
        victim_addr, 1, PocqTxnKind::ReadUnique, lineData(0x75), false,
        0x90);
    model.commitRead(
        other_victim_addr, 2, PocqTxnKind::ReadUnique, lineData(0x76),
        false, 0x91);
    const auto token =
        completeLookupToken(model, 1316, replacement_addr);

    // Consume the only SEQ slot after Lookup but before the mutation reaches
    // U1. The target token remains fresh because the other SF set changed.
    model.commitRead(
        other_replacement_addr, 4, PocqTxnKind::ReadUnique,
        lineData(0x77), false, 0x92);
    ASSERT_EQ(model.seqOccupancy(), 1);
    ASSERT_TRUE(model.validateCommitToken(
        token, token.lookupReqId, replacement_addr));
    const auto original = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, victim_addr});

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1317, replacement_addr), lineData(0x78), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup(1000 + cycle);
        EXPECT_LE(
            model.seqOccupancy() + model.seqReservationCount(),
            model.seqCapacity());
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    const auto& replay = std::get<SlcSfReplay>(response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::SeqConflict);
    EXPECT_TRUE(replay.redoLookup);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, replacement_addr}).result.sfHit);
    const auto preserved = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, victim_addr});
    expectLookupResultsEqual(preserved.result, original.result);
    expectArraySnapshotsEqual(preserved.snapshot.sf, original.snapshot.sf);
    EXPECT_EQ(model.seqOccupancy(), 1);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     SeqReservationRaceReplaysWithoutPartialReservation)
{
    HnfSLCSF model(64, 2, 2, 2, 1, 1);
    const uint64_t victim_addr = TestAddr;
    const uint64_t other_victim_addr = TestAddr + 64;
    const uint64_t replacement_addr = TestAddr + 128;
    const uint64_t other_replacement_addr = TestAddr + 192;
    model.commitRead(
        victim_addr, 1, PocqTxnKind::ReadUnique, lineData(0x79), false);
    model.commitRead(
        other_victim_addr, 2, PocqTxnKind::ReadUnique, lineData(0x7a),
        false);
    const auto token =
        completeLookupToken(model, 1318, replacement_addr);
    ASSERT_TRUE(model.tryReserveSfResources(
        999, other_replacement_addr, PocqTxnKind::ReadShared));
    ASSERT_EQ(model.seqOccupancy(), 0);
    ASSERT_EQ(model.seqReservationCount(), 1);

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1319, replacement_addr), lineData(0x7b), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        EXPECT_LE(
            model.seqOccupancy() + model.seqReservationCount(),
            model.seqCapacity());
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::SeqConflict);
    EXPECT_EQ(model.serviceStallCount(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 1);
    EXPECT_FALSE(model.hasSfReservation(1319));
    EXPECT_TRUE(model.hasSfReservation(999));
    EXPECT_TRUE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, victim_addr}).result.sfHit);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, replacement_addr}).result.sfHit);
    model.releaseSfResources(999);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     TokenLookupIdTamperReplaysWithoutSideEffects)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    const auto token = completeLookupToken(model, 1320, TestAddr);
    auto tampered_token = token;
    tampered_token.lookupReqId = SlcSfReqId{9999};
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const size_t seq_before = model.seqOccupancy();

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1321), lineData(0x65), {}, tampered_token,
        token.lookupReqId);
    const auto& typed_request = std::get<SlcSfFillReq>(request);
    EXPECT_EQ(typed_request.token.lookupReqId, SlcSfReqId{9999});
    EXPECT_EQ(typed_request.sourceLookupReqId, SlcSfReqId{1320});
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::StaleCommitToken);
    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after.result, before.result);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
    EXPECT_EQ(model.seqOccupancy(), seq_before);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     U0ReplayAndErrorReleasePreheldReservation)
{
    const auto run_terminal_case = [](bool decode_error) {
        HnfSLCSF model(64, 1, 2, 1, 2);
        constexpr uint32_t Entry = 1322;
        const auto token = completeLookupToken(model, 1323, TestAddr);
        ASSERT_TRUE(model.tryReserveSfResources(
            Entry, TestAddr, PocqTxnKind::ReadShared));
        SlcSfRequest request = makeSlcSfCommitReadReq(
            mutationHeader(Entry),
            decode_error ? PocqTxnKind::Unknown : PocqTxnKind::ReadShared,
            lineData(0x65), false, 0, {}, token,
            decode_error ? token.lookupReqId : SlcSfReqId{});
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);

        std::optional<SlcSfResponse> response;
        for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
            model.wakeup();
            response = model.popVisibleResponse();
        }
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(response->status(), decode_error ?
                  SlcSfTerminalStatus::Error :
                  SlcSfTerminalStatus::Replay);
        EXPECT_FALSE(model.hasSfReservation(Entry));
        EXPECT_EQ(model.sfReservationCount(), 0);
        EXPECT_EQ(model.seqReservationCount(), 0);
        EXPECT_EQ(model.reqOutstanding(), 0);
        EXPECT_EQ(model.respOccupied(), 0);
    };

    run_terminal_case(false);
    run_terminal_case(true);
}

TEST(HnfSlcSfMutationServiceTest,
     DirtyVictimReservedBeforeOverwrite)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    const uint64_t dirty_addr = TestAddr;
    const uint64_t other_addr = TestAddr + 64;
    const uint64_t replacement_addr = TestAddr + 128;
    model.writeLine(
        dirty_addr, 1, lineData(0x6b), PocqTxnKind::WriteUnique);
    model.commitRead(
        other_addr, 2, PocqTxnKind::ReadShared, lineData(0x6c), false);
    const auto token = completeLookupToken(model, 1350, replacement_addr);
    ASSERT_FALSE(token.slc.hit);
    model.flushL3(other_addr);
    ASSERT_TRUE(model.validateCommitToken(
        token, SlcSfReqId{1350}, replacement_addr));

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1351, replacement_addr), lineData(0x6d), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    const uint64_t issue_cycle = model.currentCycle();
    model.wakeup(1010);
    model.wakeup(1020);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_EQ(model.victimReservationCount(), 1);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    EXPECT_TRUE(model.hasSfReservation(1351));
    EXPECT_TRUE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, dirty_addr}).result.dataDirty);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown,
        replacement_addr}).result.slcHit);

    std::optional<SlcSfResponse> response;
    Tick tick = 1030;
    while (!response && tick < 1120) {
        model.wakeup(tick);
        response = model.popVisibleResponse();
        tick += 10;
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.currentCycle(),
              issue_cycle + model.pipelineConfig().fillLatency +
                  model.pipelineConfig().victimLatency + 1);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    EXPECT_EQ(fill.slcVictim->lineAddress, dirty_addr);
    EXPECT_EQ(fill.slcVictim->line.data, lineData(0x6b));
    EXPECT_EQ(model.dirtyVictimState(fill.slcVictim->victimId),
              HnfSLCSF::VictimState::HandedOff);
    EXPECT_TRUE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown,
        replacement_addr}).result.slcHit);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, fill.slcVictim->victimId, dirty_addr, 1, 3352, 1352);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     CancelAfterInstalledSnapshotCleansDirtyVictimSeal)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const std::vector<uint8_t> victim_data = lineData(0xb1);
    const uint64_t replacement_addr = TestAddr + 64;
    model.writeLine(
        TestAddr, 3, victim_data, PocqTxnKind::WriteUnique);
    const auto token = completeLookupToken(model, 1450, replacement_addr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1451, replacement_addr), lineData(0xb2), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 8 && model.mutationStageCount(
             HnfSLCSF::MutationStage::U2ArrayWrite) == 0;
         ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    ASSERT_EQ(model.victimReservationCount(), 1);
    ASSERT_EQ(model.victimBufferOccupancy(), 1);
    ASSERT_EQ(model.dirtyVictimSealCount(), 1);
    ASSERT_TRUE(model.hasSfReservation(1451));

    const auto victim_before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const auto replacement_before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, replacement_addr});
    EXPECT_EQ(model.cancelRequest(1451, SlcSfReqId{1451}, 5000),
              SlcSfCancelResult::Cancelled);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);

    model.wakeup();
    auto response = consumeVisibleResponse(model);
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::Cancelled);
    EXPECT_FALSE(consumeVisibleResponse(model).has_value());
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);

    const auto victim_after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const auto replacement_after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, replacement_addr});
    expectLookupResultsEqual(victim_after.result, victim_before.result);
    expectArraySnapshotsEqual(
        victim_after.snapshot.slc, victim_before.snapshot.slc);
    expectArraySnapshotsEqual(
        victim_after.snapshot.sf, victim_before.snapshot.sf);
    expectLookupResultsEqual(
        replacement_after.result, replacement_before.result);
    expectArraySnapshotsEqual(
        replacement_after.snapshot.slc, replacement_before.snapshot.slc);
    expectArraySnapshotsEqual(
        replacement_after.snapshot.sf, replacement_before.snapshot.sf);
    EXPECT_TRUE(victim_after.result.slcHit);
    EXPECT_TRUE(victim_after.result.dataDirty);
    EXPECT_EQ(victim_after.result.data, victim_data);
    EXPECT_FALSE(replacement_after.result.slcHit);
}

TEST(HnfSlcSfMutationServiceTest,
     DrainWithInstalledSnapshotConsumesSealAndPreservesHandoff)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const std::vector<uint8_t> victim_data = lineData(0xc1);
    const uint64_t replacement_addr = TestAddr + 64;
    model.writeLine(
        TestAddr, 4, victim_data, PocqTxnKind::WriteUnique);
    const auto token = completeLookupToken(model, 1452, replacement_addr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1453, replacement_addr), lineData(0xc2), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 8 && model.mutationStageCount(
             HnfSLCSF::MutationStage::U2ArrayWrite) == 0;
         ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    ASSERT_EQ(model.victimReservationCount(), 1);
    ASSERT_EQ(model.dirtyVictimSealCount(), 1);

    model.beginDraining();
    SlcSfRequest rejected = lookupRequest(1454, replacement_addr);
    EXPECT_EQ(model.tryEnqueue(std::move(rejected)),
              SlcSfEnqueueResult::Draining);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        response = consumeVisibleResponse(model);
    }
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    const SlcSfSlcVictim victim = *fill.slcVictim;
    EXPECT_EQ(victim.lineAddress, TestAddr);
    EXPECT_EQ(victim.line.data, victim_data);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::HandedOff);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_TRUE(model.isBusy());
    EXPECT_EQ(model.drainingRejectCount(), 1);

    // Drain gates new admission; the explicit downstream owner is retained.
    // Resume admission before delivering its durable release completion.
    model.resumeFromDrain();
    model.wakeup();
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, victim.victimId, victim.lineAddress, victim.owner,
        9454, 1455);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.dirtyVictimSealCount(), 0);
    EXPECT_FALSE(model.isBusy());
}

TEST(HnfSlcSfMutationServiceTest,
     ExactSfTargetUpgradesPreheldReservationWhenAnotherWayIsInvalid)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    constexpr uint32_t Entry = 1361;
    const uint64_t addr_a = TestAddr;
    const uint64_t addr_b = TestAddr + 64;
    const uint64_t addr_c = TestAddr + 128;
    model.commitRead(
        addr_a, 1, PocqTxnKind::ReadShared, lineData(0x6e), false);
    model.commitRead(
        addr_b, 2, PocqTxnKind::ReadShared, lineData(0x6f), false);
    const auto token = completeLookupToken(model, 1360, addr_c);
    model.flushSf(addr_b);
    ASSERT_TRUE(model.validateCommitToken(token, SlcSfReqId{1360}, addr_c));
    ASSERT_TRUE(model.tryReserveSfResources(
        Entry, addr_c, PocqTxnKind::ReadShared));
    ASSERT_EQ(model.seqReservationCount(), 0);

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(Entry, addr_c), lineData(0x70), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_TRUE(model.hasSfReservation(Entry));
    EXPECT_EQ(model.seqReservationCount(), 1);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 6 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    ASSERT_TRUE(model.hasPendingSeq());
    EXPECT_EQ(model.frontPendingSeq().blockAddr, addr_a);
    EXPECT_FALSE(model.hasSfReservation(Entry));
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     TokenStaledBeforeU2WriteReplaysAndReleasesResources)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    const auto token = completeLookupToken(model, 1330, TestAddr);
    ASSERT_TRUE(model.tryReserveSfResources(
        999, TestAddr, PocqTxnKind::ReadShared));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1331), lineData(0x66), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    model.releaseSfResources(999);

    // Acquire the mutation's own reservation while the token is still fresh,
    // then stale it before U2 so the final validation gate owns cleanup.
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_TRUE(model.hasSfReservation(1331));
    model.writeLine(
        TestAddr, 1, lineData(0x67), PocqTxnKind::WriteUnique);
    const auto intervening = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});

    model.wakeup(500);
    EXPECT_EQ(
        model.currentCycle(),
        issue_cycle + model.pipelineConfig().fillLatency);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U3CheckLatch), 1);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_TRUE(model.hasSfReservation(1331));
    const auto after_revalidate = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after_revalidate.result, intervening.result);
    expectArraySnapshotsEqual(
        after_revalidate.snapshot.slc, intervening.snapshot.slc);
    expectArraySnapshotsEqual(
        after_revalidate.snapshot.sf, intervening.snapshot.sf);

    model.wakeup(510);
    EXPECT_EQ(
        model.currentCycle(),
        issue_cycle + model.pipelineConfig().fillLatency + 1);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.hasSfReservation(1331));
    model.wakeup(520);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    const auto& replay = std::get<SlcSfReplay>(response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::StaleCommitToken);
    EXPECT_EQ(
        replay.retryNotBeforeTick,
        510 + model.pipelineConfig().replayPenalty *
                  model.pipelineConfig().childClockPeriod);
    EXPECT_TRUE(replay.redoLookup);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     LruDriftUsesSnapshottedSlcAndSfWays)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    const uint64_t addr_a = TestAddr;
    const uint64_t addr_b = TestAddr + 64;
    const uint64_t addr_c = TestAddr + 128;
    model.commitRead(
        addr_a, 1, PocqTxnKind::ReadShared, lineData(0x68), false);
    model.commitRead(
        addr_b, 2, PocqTxnKind::ReadShared, lineData(0x69), false);
    const auto token = completeLookupToken(model, 1340, addr_c);
    ASSERT_FALSE(token.slc.hit);
    ASSERT_FALSE(token.sf.hit);

    // Move the recorded victim to MRU without changing its generation.
    completeLookup(model, lookupRequest(1341, addr_a));
    ASSERT_TRUE(model.validateCommitToken(token, SlcSfReqId{1340}, addr_c));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1342, addr_c), lineData(0x6a), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto preserved = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, addr_b});
    EXPECT_TRUE(preserved.result.slcHit);
    EXPECT_TRUE(preserved.result.sfHit);
    const auto installed = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, addr_c});
    EXPECT_TRUE(installed.result.slcHit);
    EXPECT_TRUE(installed.result.sfHit);
    EXPECT_EQ(installed.snapshot.slc.way, token.slc.way);
    EXPECT_EQ(installed.snapshot.sf.way, token.sf.way);
    ASSERT_TRUE(model.hasPendingSeq());
    EXPECT_EQ(model.frontPendingSeq().blockAddr, addr_a);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest, ResourcePreparationFailureIsAtomic)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto token = completeLookupToken(model, 1100, TestAddr);
    ASSERT_TRUE(model.tryReserveSfResources(
        999, TestAddr, PocqTxnKind::ReadShared));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(111), lineData(0x77), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_GT(model.serviceStallCount(), 0);
    EXPECT_EQ(model.correctnessReplayCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_FALSE(model.hasSfReservation(111));
    EXPECT_TRUE(model.hasSfReservation(999));
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr}).result.slcHit);

    model.releaseSfResources(999);
    for (size_t cycle = 0; cycle < 8; ++cycle) {
        model.wakeup();
        if (auto response = model.popVisibleResponse()) {
            EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
            EXPECT_TRUE(model.probe(HnfSlcLookupReq{
                0, RawReq{}, PocqTxnKind::Unknown,
                TestAddr}).result.slcHit);
            EXPECT_FALSE(model.hasSfReservation(111));
            EXPECT_EQ(model.seqReservationCount(), 0);
            EXPECT_EQ(model.reqOutstanding(), 0);
            EXPECT_EQ(model.respOccupied(), 0);
            EXPECT_EQ(model.sfReservationCount(), 0);
            return;
        }
    }
    FAIL() << "stalled mutation did not progress after resource release";
}

TEST(HnfSlcSfMutationServiceTest,
     MultiResourceStallLeavesNoPartialReservation)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const uint64_t dirty_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    model.writeLine(
        dirty_addr, 7, lineData(0x73), PocqTxnKind::WriteUnique);
    const auto token = completeLookupToken(model, 1590, replacement_addr);
    ASSERT_TRUE(model.tryReserveSfResources(
        999, replacement_addr, PocqTxnKind::ReadShared));

    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1591, replacement_addr), lineData(0x74), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();

    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_GT(model.serviceStallCount(), 0);
    EXPECT_TRUE(model.hasSfReservation(999));
    EXPECT_FALSE(model.hasSfReservation(1591));
    EXPECT_EQ(model.victimReservationCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_TRUE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, dirty_addr}).result.dataDirty);

    model.releaseSfResources(999);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    ASSERT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    EXPECT_EQ(fill.slcVictim->lineAddress, dirty_addr);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, fill.slcVictim->victimId, dirty_addr, 7, 3592, 1592);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     ResourceLeakAuditCoversTerminalAndDrainCleanup)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto expect_no_transient_resources = [&model]() {
        EXPECT_EQ(model.reqOutstanding(), 0);
        EXPECT_EQ(model.respOccupied(), 0);
        EXPECT_EQ(model.sfReservationCount(), 0);
        EXPECT_EQ(model.seqReservationCount(), 0);
        EXPECT_EQ(model.victimBufferOccupancy(), 0);
    };

    model.commitRead(
        TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x75), false);
    auto token = completeLookupToken(model, 1600, TestAddr);
    uint64_t finished_before = model.finishedRequestCount();
    SlcSfResponse done = completeMutation(
        model, makeSlcSfRemoveSharerReq(
            mutationHeader(1601), token, token.lookupReqId));
    ASSERT_EQ(done.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.finishedRequestCount(), finished_before + 1);
    expect_no_transient_resources();

    model.commitRead(
        TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x76), false);
    token = completeLookupToken(model, 1602, TestAddr);
    model.removeSharer(TestAddr, 7);
    const auto before_replay = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    finished_before = model.finishedRequestCount();
    SlcSfResponse replay = completeMutation(
        model, makeSlcSfRemoveSharerReq(
            mutationHeader(1603), token, token.lookupReqId));
    ASSERT_EQ(replay.status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(model.finishedRequestCount(), finished_before + 1);
    const auto after_replay = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectArraySnapshotsEqual(
        after_replay.snapshot.slc, before_replay.snapshot.slc);
    expectArraySnapshotsEqual(
        after_replay.snapshot.sf, before_replay.snapshot.sf);
    expect_no_transient_resources();

    finished_before = model.finishedRequestCount();
    SlcSfResponse error = completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            mutationHeader(1604), SlcSfVictimId{9999}));
    ASSERT_EQ(error.status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(model.finishedRequestCount(), finished_before + 1);
    expect_no_transient_resources();

    token = completeLookupToken(model, 1605, TestAddr);
    SlcSfRequest cancelled = makeSlcSfRemoveSharerReq(
        mutationHeader(1606), token, token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(cancelled)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    ASSERT_TRUE(model.hasSfReservation(1606));
    finished_before = model.finishedRequestCount();
    constexpr Tick CancelTick = 5000;
    EXPECT_EQ(model.cancelRequest(
                  1606, SlcSfReqId{1606}, CancelTick),
              SlcSfCancelResult::Cancelled);
    EXPECT_EQ(model.finishedRequestCount(), finished_before + 1);
    EXPECT_EQ(model.cancelledRequestCount(), 1);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.respOccupied(), 1);
    EXPECT_EQ(model.cancelRequest(
                  1606, SlcSfReqId{1606}, CancelTick),
              SlcSfCancelResult::TooLate);
    model.wakeup();
    auto cancelled_response = model.popVisibleResponse();
    ASSERT_TRUE(cancelled_response.has_value());
    EXPECT_EQ(cancelled_response->reqId(), SlcSfReqId{1606});
    EXPECT_EQ(cancelled_response->pocEntryId(), 1606);
    EXPECT_EQ(cancelled_response->status(), SlcSfTerminalStatus::Replay);
    const auto& cancelled_replay =
        std::get<SlcSfReplay>(cancelled_response->payload());
    EXPECT_EQ(cancelled_replay.reason, SlcSfReplayReason::Cancelled);
    EXPECT_EQ(
        cancelled_replay.retryNotBeforeTick,
        CancelTick + model.pipelineConfig().replayPenalty *
                         model.pipelineConfig().childClockPeriod);
    EXPECT_TRUE(cancelled_replay.redoLookup);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    EXPECT_EQ(model.cancelRequest(
                  1606, SlcSfReqId{1606}, CancelTick),
              SlcSfCancelResult::NotFound);
    expect_no_transient_resources();
    EXPECT_EQ(model.correctnessReplayCount(), 1);

    HnfSLCSF drain_model(64, 4, 2, 4, 2);
    drain_model.commitRead(
        TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x77), false);
    const auto drain_token = completeLookupToken(
        drain_model, 1607, TestAddr);
    SlcSfRequest draining_request = makeSlcSfRemoveSharerReq(
        mutationHeader(1608), drain_token, drain_token.lookupReqId);
    ASSERT_EQ(drain_model.tryEnqueue(std::move(draining_request)),
              SlcSfEnqueueResult::Accepted);
    drain_model.wakeup();
    drain_model.wakeup();
    drain_model.wakeup();
    ASSERT_TRUE(drain_model.hasSfReservation(1608));
    const uint64_t drain_finished_before =
        drain_model.finishedRequestCount();
    drain_model.beginDraining();
    auto rejected_during_drain = lookupRequest(1609);
    EXPECT_EQ(drain_model.tryEnqueue(std::move(rejected_during_drain)),
              SlcSfEnqueueResult::Draining);
    std::optional<SlcSfResponse> drain_response;
    for (size_t cycle = 0; cycle < 16 && !drain_response; ++cycle) {
        drain_model.wakeup();
        drain_response = drain_model.popVisibleResponse();
    }
    ASSERT_TRUE(drain_response.has_value());
    EXPECT_EQ(drain_response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(drain_response->reqId(), SlcSfReqId{1608});
    EXPECT_EQ(drain_model.finishedRequestCount(),
              drain_finished_before + 1);
    EXPECT_EQ(drain_model.reqOutstanding(), 0);
    EXPECT_EQ(drain_model.respOccupied(), 0);
    EXPECT_EQ(drain_model.sfReservationCount(), 0);
    EXPECT_EQ(drain_model.seqReservationCount(), 0);
    EXPECT_EQ(drain_model.victimBufferOccupancy(), 0);
    EXPECT_EQ(drain_model.cancelledRequestCount(), 0);
    EXPECT_EQ(drain_model.drainingRejectCount(), 1);
}

TEST(HnfSlcSfMutationServiceTest,
     CancelBeforeU0ReleasesAdoptedReservationAndReturnsTerminal)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    constexpr uint32_t Entry = 1610;
    const auto token = completeLookupToken(model, 1611, TestAddr);
    ASSERT_TRUE(model.tryReserveSfResources(
        Entry, TestAddr, PocqTxnKind::ReadShared));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(Entry), lineData(0x78), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    ASSERT_EQ(model.reqInflightCount(), 1);
    ASSERT_TRUE(model.hasSfReservation(Entry));

    EXPECT_EQ(model.cancelRequest(Entry, SlcSfReqId{Entry}, 6000),
              SlcSfCancelResult::Cancelled);
    EXPECT_FALSE(model.hasSfReservation(Entry));
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);

    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{Entry});
    EXPECT_EQ(response->pocEntryId(), Entry);
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(response->payload()).reason,
              SlcSfReplayReason::Cancelled);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     OwnerlessReleaseCannotBeCancelledAndStillCompletes)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    model.writeLine(
        TestAddr, 0, lineData(0x79), PocqTxnKind::WriteUnique);
    const uint64_t replacement_addr = TestAddr + 64;
    const auto token = completeLookupToken(model, 1620, replacement_addr);
    SlcSfResponse fill = completeMutation(
        model, makeSlcSfFillCleanSharedReq(
            mutationHeader(1621, replacement_addr), lineData(0x7a), {},
            token, token.lookupReqId));
    const auto& fill_payload = std::get<SlcSfFillResponse>(fill.payload());
    ASSERT_TRUE(fill_payload.slcVictim.has_value());
    const SlcSfVictimId victim_id = fill_payload.slcVictim->victimId;
    constexpr uint32_t WritebackTxn = 4622;
    model.markDirtyVictimWritebackIssued(
        victim_id, 0, WriteNoSnpFullOpcode, WritebackTxn);

    SlcSfRequest release = makeSlcSfReleaseDirtyVictimReq(
        dirtyVictimReleaseHeader(
            1622, victim_id, TestAddr, 0, WritebackTxn),
        victim_id);
    ASSERT_EQ(model.tryEnqueue(std::move(release)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(7000);
    ASSERT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.cancelRequest(UINT32_MAX, SlcSfReqId{1622}, 7000),
              SlcSfCancelResult::NotCancellable);

    std::optional<SlcSfResponse> response;
    for (Tick tick = 7010; tick < 7200 && !response; tick += 10) {
        model.wakeup(tick);
        response = consumeVisibleResponse(model);
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{1622});
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.dirtyVictimState(victim_id),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(model.cancelledRequestCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     AdoptedReservationIsHeldThroughU3AndReleasedOnce)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    constexpr uint32_t Entry = 120;
    const auto token = completeLookupToken(model, 1200, TestAddr);
    ASSERT_TRUE(model.tryReserveSfResources(
        Entry, TestAddr, PocqTxnKind::ReadShared));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(Entry), lineData(0x79), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_TRUE(model.hasSfReservation(Entry));
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_TRUE(model.hasSfReservation(Entry));
    model.wakeup();
    EXPECT_FALSE(model.hasSfReservation(Entry));
    EXPECT_EQ(model.respPendingCount(), 1);
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     RemoveSharerStallsOnConflictingSfReservation)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    model.commitRead(
        TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x7a), false);
    const auto token = completeLookupToken(model, 1210, TestAddr);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    ASSERT_TRUE(model.tryReserveSfResources(
        999, TestAddr, PocqTxnKind::Evict));
    SlcSfRequest request = makeSlcSfRemoveSharerReq(
        mutationHeader(121), token, token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_FALSE(model.hasSfReservation(121));
    const auto stalled = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectArraySnapshotsEqual(stalled.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(stalled.snapshot.sf, before.snapshot.sf);

    model.releaseSfResources(999);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr}).result.sfHit);
    EXPECT_FALSE(model.hasSfReservation(121));
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     DirtyVictimHandoffPreservesData)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const std::vector<uint8_t> victim_data = lineData(0x7b);
    model.writeLine(
        TestAddr, 0, victim_data, PocqTxnKind::WriteUnique);
    const uint64_t replacement_addr = TestAddr + 64;
    const auto token = completeLookupToken(
        model, 1220, replacement_addr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(122, replacement_addr), lineData(0x7c), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    const SlcSfSlcVictim owned = *fill.slcVictim;
    EXPECT_TRUE(owned.victimId.value != 0);
    EXPECT_EQ(owned.lineAddress, TestAddr);
    EXPECT_EQ(owned.state, HnfSlcState::MU);
    EXPECT_EQ(owned.owner, 0);
    EXPECT_TRUE(owned.line.dirty);
    EXPECT_EQ(owned.line.data, victim_data);
    EXPECT_EQ(owned.line.byteMask, std::vector<uint8_t>(64, 0xff));
    EXPECT_EQ(model.dirtyVictimState(owned.victimId),
              HnfSLCSF::VictimState::HandedOff);
    constexpr uint32_t WritebackTxn = 4222;
    model.markDirtyVictimWritebackIssued(
        owned.victimId, owned.owner, WriteNoSnpFullOpcode, WritebackTxn);
    EXPECT_EQ(model.dirtyVictimState(owned.victimId),
              HnfSLCSF::VictimState::WritebackIssued);
    SlcSfResponse release = completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1221, owned.victimId, owned.lineAddress, owned.owner,
                WritebackTxn),
            owned.victimId));
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.dirtyVictimState(owned.victimId),
              HnfSLCSF::VictimState::Released);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(owned.line.data, victim_data);
    release = completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1222, owned.victimId, owned.lineAddress, owned.owner,
                WritebackTxn),
            owned.victimId));
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Error);
    release = completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1223, SlcSfVictimId{9999}, owned.lineAddress, owned.owner,
                WritebackTxn),
            SlcSfVictimId{9999}));
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Error);
    EXPECT_FALSE(model.hasSfReservation(122));
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     VictimBufferFullReplaysWithoutMutation)
{
    HnfSLCSFPipelineConfig config{};
    config.victimBufferEntries = 1;
    HnfSLCSF model(64, 1, 1, 1, 1, 8, config);
    model.writeLine(
        TestAddr, 0, lineData(0x81), PocqTxnKind::WriteUnique);
    const uint64_t second_addr = TestAddr + 64;
    auto token = completeLookupToken(model, 1400, second_addr);
    SlcSfResponse first = completeMutation(
        model, makeSlcSfFillCleanSharedReq(
            mutationHeader(1401, second_addr), lineData(0x82), {}, token,
            token.lookupReqId));
    ASSERT_EQ(first.status(), SlcSfTerminalStatus::Done);
    const auto& first_fill = std::get<SlcSfFillResponse>(first.payload());
    ASSERT_TRUE(first_fill.slcVictim.has_value());
    EXPECT_EQ(model.victimBufferOccupancy(), 1);

    model.writeLine(
        second_addr, 1, lineData(0x83), PocqTxnKind::WriteUnique);
    const uint64_t third_addr = TestAddr + 128;
    token = completeLookupToken(model, 1402, third_addr);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, second_addr});
    SlcSfRequest second = makeSlcSfFillCleanSharedReq(
        mutationHeader(1403, third_addr), lineData(0x84), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(second)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U3CheckLatch), 1);
    EXPECT_FALSE(model.hasSfReservation(1403));
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    std::optional<SlcSfResponse> replay_response;
    for (size_t cycle = 0; cycle < 16 && !replay_response; ++cycle) {
        model.wakeup();
        replay_response = model.popVisibleResponse();
    }
    ASSERT_TRUE(replay_response.has_value());
    SlcSfResponse replay = std::move(*replay_response);
    ASSERT_EQ(replay.status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(replay.payload()).reason,
              SlcSfReplayReason::VictimBufferFull);
    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, second_addr});
    expectLookupResultsEqual(after.result, before.result);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, third_addr}).result.slcHit);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, first_fill.slcVictim->victimId, TestAddr, 0, 5404, 1404);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     SameLineDirtyVictimConflictReplaysWithoutMutation)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const uint64_t held_addr = TestAddr;
    const uint64_t temporary_addr = TestAddr + 64;
    model.writeLine(
        held_addr, 0, lineData(0x85), PocqTxnKind::WriteUnique);
    auto token = completeLookupToken(model, 1410, temporary_addr);
    SlcSfResponse first = completeMutation(
        model, makeSlcSfFillCleanSharedReq(
            mutationHeader(1411, temporary_addr), lineData(0x86), {}, token,
            token.lookupReqId));
    const auto& first_fill = std::get<SlcSfFillResponse>(first.payload());
    ASSERT_TRUE(first_fill.slcVictim.has_value());

    model.writeLine(
        held_addr, 0, lineData(0x87), PocqTxnKind::WriteUnique);
    const uint64_t replacement_addr = TestAddr + 128;
    token = completeLookupToken(model, 1412, replacement_addr);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, held_addr});
    SlcSfResponse replay = completeMutation(
        model, makeSlcSfFillCleanSharedReq(
            mutationHeader(1413, replacement_addr), lineData(0x88), {},
            token, token.lookupReqId));
    ASSERT_EQ(replay.status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(replay.payload()).reason,
              SlcSfReplayReason::ResourceConflict);
    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, held_addr});
    expectLookupResultsEqual(after.result, before.result);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, first_fill.slcVictim->victimId, held_addr, 0, 5414, 1414);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
}

TEST(HnfSlcSfMutationServiceTest,
     SlcAndSfVictimLatenciesBothApply)
{
    HnfSLCSFPipelineConfig config{};
    config.fillLatency = 4;
    config.victimLatency = 3;
    config.sfEvictLatency = 2;
    HnfSLCSF model(64, 1, 1, 1, 1, 2, config);
    model.writeLine(
        TestAddr, 0, lineData(0x89), PocqTxnKind::WriteUnique);
    model.commitRead(
        TestAddr + 64, 1, PocqTxnKind::ReadUnique, lineData(0x8a), false);
    const uint64_t replacement_addr = TestAddr + 128;
    const auto token = completeLookupToken(model, 1420, replacement_addr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1421, replacement_addr), lineData(0x8b), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    const uint64_t accepted_cycle = model.currentCycle();
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 16 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(model.currentCycle(), accepted_cycle + config.fillLatency +
              config.victimLatency + config.sfEvictLatency + 2);
    const auto& fill = std::get<SlcSfFillResponse>(response->payload());
    ASSERT_TRUE(fill.slcVictim.has_value());
    ASSERT_TRUE(fill.sfVictim.has_value());
    const SlcSfResponse release = completeDirtyVictimRelease(
        model, fill.slcVictim->victimId, TestAddr, 0, 5422, 1422);
    EXPECT_EQ(release.status(), SlcSfTerminalStatus::Done);
}

TEST(HnfSlcSfMutationServiceTest,
     StaleWritebackBypassesDirtyVictimStall)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const uint64_t dirty_addr = TestAddr;
    const uint64_t stale_addr = TestAddr + 64;
    model.writeLine(
        dirty_addr, 0, lineData(0x7e), PocqTxnKind::WriteUnique);
    model.commitRead(
        stale_addr, 1, PocqTxnKind::ReadUnique,
        lineData(0x7f), false);
    const auto token = completeLookupToken(model, 1324, stale_addr);
    const auto dirty_before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, dirty_addr});
    const auto stale_before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, stale_addr});

    uint64_t req_id = 124;
    for (const PocqTxnKind txn : {
             PocqTxnKind::WriteBackFull,
             PocqTxnKind::WriteEvictFull}) {
        SlcSfRequest request = makeSlcSfWriteLineReq(
            mutationHeader(req_id++, stale_addr, 7), lineData(0x80), txn,
            0, {}, token, token.lookupReqId);
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);
        std::optional<SlcSfResponse> response;
        for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
            model.wakeup();
            response = model.popVisibleResponse();
        }
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    }

    const auto dirty_after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, dirty_addr});
    const auto stale_after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, stale_addr});
    expectArraySnapshotsEqual(
        dirty_after.snapshot.slc, dirty_before.snapshot.slc);
    expectArraySnapshotsEqual(
        stale_after.snapshot.sf, stale_before.snapshot.sf);
    EXPECT_EQ(dirty_after.result.data, dirty_before.result.data);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     FlushOperationsUseConfiguredServiceAndOneTerminalResponse)
{
    for (const FlushServiceOperation operation : {
             FlushServiceOperation::FlushSf,
             FlushServiceOperation::FlushL3,
             FlushServiceOperation::WriteL3FlushSf}) {
        HnfSLCSFPipelineConfig config{};
        config.updateLatency = 2;
        config.fillLatency = 7;
        HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x7d), false);
        const SlcSfCommitToken token =
            completeLookupToken(model, 1230, TestAddr);
        SlcSfRequest request = flushServiceRequest(operation, 1231, token);
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);

        const uint64_t accepted_cycle = model.currentCycle();
        std::optional<SlcSfResponse> response;
        for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
            model.wakeup();
            response = model.popVisibleResponse();
        }
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(model.currentCycle(),
                  accepted_cycle + config.updateLatency + 2);
        EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
        EXPECT_EQ(response->operationKind(),
                  flushServiceOperationKind(operation));
        EXPECT_FALSE(model.popVisibleResponse().has_value());

        const auto after = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        if (operation == FlushServiceOperation::FlushL3) {
            EXPECT_FALSE(after.result.slcHit);
        } else {
            EXPECT_TRUE(after.result.slcHit);
        }
        if (operation != FlushServiceOperation::FlushL3) {
            EXPECT_FALSE(after.result.sfHit);
        }
        if (operation == FlushServiceOperation::WriteL3FlushSf) {
            EXPECT_EQ(after.result.data, lineData(0xa0));
        }
        EXPECT_EQ(model.reqOutstanding(), 0);
        EXPECT_EQ(model.respOccupied(), 0);
    }
}

TEST(HnfSlcSfMutationServiceTest, StaleFlushOperationsReplayWithoutMutation)
{
    for (const FlushServiceOperation operation : {
             FlushServiceOperation::FlushSf,
             FlushServiceOperation::FlushL3,
             FlushServiceOperation::WriteL3FlushSf}) {
        HnfSLCSF model(64, 4, 2, 4, 2);
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x7e), false);
        const SlcSfCommitToken token =
            completeLookupToken(model, 1240, TestAddr);
        model.invalidateCommitTokens();
        const auto before = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        SlcSfRequest request = flushServiceRequest(operation, 1241, token);
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);

        std::optional<SlcSfResponse> response;
        for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
            model.wakeup();
            response = model.popVisibleResponse();
        }
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
        EXPECT_EQ(response->operationKind(),
                  flushServiceOperationKind(operation));
        const auto after = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
        expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
        EXPECT_EQ(after.result.data, before.result.data);
    }
}

TEST(HnfSlcSfMutationServiceTest,
     FlushOperationsWaitForTerminalResponseCapacity)
{
    for (const FlushServiceOperation operation : {
             FlushServiceOperation::FlushSf,
             FlushServiceOperation::FlushL3,
             FlushServiceOperation::WriteL3FlushSf}) {
        HnfSLCSFPipelineConfig config{};
        config.lookupLatency = 1;
        config.updateLatency = 1;
        config.respQueueEntries = 1;
        HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x7f), false);
        const SlcSfCommitToken token =
            completeLookupToken(model, 1250, TestAddr);
        const auto before = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        SlcSfRequest blocker = lookupRequest(1251, TestAddr + 64);
        SlcSfRequest request = flushServiceRequest(operation, 1252, token);
        ASSERT_EQ(model.tryEnqueue(std::move(blocker)),
                  SlcSfEnqueueResult::Accepted);
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::Accepted);

        model.wakeup();
        model.wakeup();
        model.wakeup();
        ASSERT_EQ(model.respVisibleCount(), 1);
        ASSERT_EQ(model.reqReadyCount(), 1);
        const auto stalled = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        expectArraySnapshotsEqual(stalled.snapshot.slc, before.snapshot.slc);
        expectArraySnapshotsEqual(stalled.snapshot.sf, before.snapshot.sf);

        ASSERT_TRUE(model.popVisibleResponse().has_value());
        std::optional<SlcSfResponse> response;
        for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
            model.wakeup();
            response = model.popVisibleResponse();
        }
        ASSERT_TRUE(response.has_value());
        EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
        EXPECT_EQ(response->operationKind(),
                  flushServiceOperationKind(operation));
        EXPECT_FALSE(model.popVisibleResponse().has_value());
    }
}

TEST(HnfSlcSfMutationServiceTest, FlushNoCreditPreservesRequestAndState)
{
    for (const FlushServiceOperation operation : {
             FlushServiceOperation::FlushSf,
             FlushServiceOperation::FlushL3,
             FlushServiceOperation::WriteL3FlushSf}) {
        HnfSLCSFPipelineConfig config{};
        config.reqQueueEntries = 1;
        HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x81), false);
        const SlcSfCommitToken token =
            completeLookupToken(model, 1260, TestAddr);
        const auto before = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        SlcSfRequest blocker = lookupRequest(1261, TestAddr + 64);
        SlcSfRequest request = flushServiceRequest(operation, 1262, token);
        ASSERT_EQ(model.tryEnqueue(std::move(blocker)),
                  SlcSfEnqueueResult::Accepted);
        ASSERT_EQ(model.tryEnqueue(std::move(request)),
                  SlcSfEnqueueResult::NoCredit);
        EXPECT_EQ(requestHeader(request).reqId, SlcSfReqId{1262});
        const auto after = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
        expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
    }
}

TEST(HnfSlcSfMutationServiceTest,
     VisibleResponseBackpressurePreventsEntryAndDuplicateWrite)
{
    HnfSLCSFPipelineConfig config{};
    config.respQueueEntries = 1;
    config.lookupLatency = 1;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto token = completeLookupToken(model, 1130, TestAddr);
    auto blocker = lookupRequest(112, TestAddr + 64);
    SlcSfRequest mutation = makeSlcSfWriteLineReq(
        mutationHeader(113), lineData(0x88), PocqTxnKind::WriteUnique,
        0, {}, token, token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(blocker)),
              SlcSfEnqueueResult::Accepted);
    ASSERT_EQ(model.tryEnqueue(std::move(mutation)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    model.wakeup();
    ASSERT_EQ(model.respVisibleCount(), 1);
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U0DecodeValidate), 0);
    for (size_t cycle = 0; cycle < 3; ++cycle) {
        model.wakeup();
    }
    EXPECT_EQ(model.reqReadyCount(), 1);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr}).result.slcHit);
    ASSERT_TRUE(model.popVisibleResponse().has_value());

    model.wakeup();
    for (size_t cycle = 0; cycle < 4; ++cycle) {
        model.wakeup();
    }
    const auto after_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    ASSERT_TRUE(after_write.result.slcHit);
    const uint64_t generation = after_write.snapshot.slc.generation;
    for (size_t cycle = 0; cycle < 3; ++cycle) {
        model.wakeup();
    }
    EXPECT_EQ(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown,
        TestAddr}).snapshot.slc.generation, generation);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{113});
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
}

TEST(HnfSlcSfMutationServiceTest,
     UnknownOrStaleSeqCompletionCannotOverwriteVictim)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 2);
    const uint64_t firstAddr = TestAddr;
    const uint64_t secondAddr = TestAddr + 64;
    const uint64_t thirdAddr = TestAddr + 128;
    const auto data = lineData(0xd8);
    model.commitRead(
        firstAddr, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.commitRead(
        secondAddr, 4, PocqTxnKind::ReadUnique, data, false, 0x90);
    const auto firstVictim = model.frontPendingSeq();
    constexpr uint32_t FirstTxn = 6500;
    model.markSeqIssued(
        firstVictim.id, CleanInvalidOpcode, FirstTxn);

    SlcSfReqHeader header =
        seqCompletionHeader(1498, firstVictim, FirstTxn);
    header.opcode ^= 1;
    auto response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{firstVictim.id}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(model.seqPhase(firstVictim.id),
              HnfSLCSF::SeqPhase::Issued);

    header = seqCompletionHeader(1499, firstVictim, FirstTxn);
    header.trace.transactionId += 1;
    response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{firstVictim.id}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(model.seqPhase(firstVictim.id),
              HnfSLCSF::SeqPhase::Issued);

    header = seqCompletionHeader(1500, firstVictim, FirstTxn);
    response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{firstVictim.id + 100}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Error);
    EXPECT_TRUE(model.seqCompletionMatches(firstVictim.id, firstAddr));

    header.reqId = SlcSfReqId{1501};
    response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{firstVictim.id}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.seqOccupancy(), 0);

    model.commitRead(
        thirdAddr, 8, PocqTxnKind::ReadUnique, data, false, 0x90);
    const auto secondVictim = model.frontPendingSeq();
    constexpr uint32_t SecondTxn = 6501;
    model.markSeqIssued(
        secondVictim.id, CleanInvalidOpcode, SecondTxn);
    ASSERT_NE(secondVictim.id, firstVictim.id);
    ASSERT_EQ(secondVictim.blockAddr, secondAddr);

    header = seqCompletionHeader(1502, secondVictim, SecondTxn);
    response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{firstVictim.id}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Error);
    EXPECT_TRUE(model.seqCompletionMatches(
        secondVictim.id, secondVictim.blockAddr));

    header.reqId = SlcSfReqId{1503};
    response = completeLookup(
        model, makeSlcSfCompleteSfEvictReq(
            header, SlcSfSeqId{secondVictim.id}, {}, false));
    EXPECT_EQ(response.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.seqOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     SeqCompletionRemainsDurableUntilExactVisibleAck)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 1);
    const uint64_t victim_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    const auto old_data = lineData(0xd9);
    const auto new_data = lineData(0xda);
    model.commitRead(
        victim_addr, 3, PocqTxnKind::ReadUnique,
        old_data, false, 0x90);
    model.commitRead(
        replacement_addr, 4, PocqTxnKind::ReadUnique,
        old_data, false, 0x90);
    const HnfSLCSF::SeqVictim victim = model.frontPendingSeq();
    constexpr uint32_t CompletionTxn = 6600;
    model.markSeqIssued(
        victim.id, CleanInvalidOpcode, CompletionTxn);

    SlcSfRequest request = makeSlcSfCompleteSfEvictReq(
        seqCompletionHeader(1600, victim, CompletionTxn),
        SlcSfSeqId{victim.id}, new_data, true);
    const SlcSfUpdateReq typed_request =
        std::get<SlcSfUpdateReq>(request);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 32 && model.respPendingCount() == 0; ++cycle) {
        model.wakeup();
    }

    ASSERT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_EQ(model.seqOccupancy(), 1);
    EXPECT_TRUE(model.seqContains(victim_addr));
    EXPECT_EQ(model.seqPhase(victim.id),
              HnfSLCSF::SeqPhase::CommittedAwaitAck);
    EXPECT_TRUE(lookup(
        model, victim_addr, 8, PocqTxnKind::ReadShared).replay);

    model.wakeup();
    ASSERT_EQ(model.respVisibleCount(), 1);
    ASSERT_NE(model.frontVisibleResponse(), nullptr);
    const SlcSfResponse visible = *model.frontVisibleResponse();
    const auto& update =
        std::get<SlcSfUpdateResponse>(visible.payload());
    ASSERT_TRUE(update.completionLease.has_value());
    const SlcSfCompletionLease& lease = *update.completionLease;
    EXPECT_EQ(lease.kind(), SlcSfCompletionKind::CompleteSfEvict);
    EXPECT_EQ(lease.reqId(), SlcSfReqId{1600});
    EXPECT_EQ(lease.pocEntryId(), UINT32_MAX);
    EXPECT_EQ(lease.objectId(), victim.id);
    EXPECT_EQ(lease.lineAddress(), victim.blockAddr);
    EXPECT_EQ(lease.requester(), victim.owner);
    EXPECT_EQ(lease.opcode(), CleanInvalidOpcode);
    EXPECT_EQ(lease.linkSequence(), victim.id);
    EXPECT_EQ(lease.transactionId(), CompletionTxn);

    EXPECT_ANY_THROW(model.popVisibleResponse());
    const SlcSfResponse lease_less =
        makeSlcSfDoneResponse(typed_request);
    EXPECT_EQ(model.acknowledgeVisibleCompletion(lease_less),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(model.seqOccupancy(), 1);
    EXPECT_EQ(model.respVisibleCount(), 1);

    EXPECT_EQ(model.acknowledgeVisibleCompletion(visible),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(model.seqOccupancy(), 0);
    EXPECT_FALSE(model.seqPhase(victim.id).has_value());
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_EQ(model.acknowledgeVisibleCompletion(visible),
              SlcSfCompletionAckResult::NotVisible);
    const auto after = lookup(
        model, victim_addr, 8, PocqTxnKind::ReadShared);
    EXPECT_TRUE(after.slcHit);
    EXPECT_TRUE(after.dataDirty);
    EXPECT_EQ(after.data, new_data);
}

TEST(HnfSlcSfMutationServiceTest,
     CrossServiceCompletionResponsesReleaseNeitherSeq)
{
    HnfSLCSF first(64, 4, 2, 1, 1, 1);
    HnfSLCSF second(64, 4, 2, 1, 1, 1);
    const auto data = lineData(0xdb);
    first.commitRead(
        TestAddr, 1, PocqTxnKind::ReadUnique, data, false, 0x90);
    first.commitRead(
        TestAddr + 64, 2, PocqTxnKind::ReadUnique,
        data, false, 0x90);
    second.commitRead(
        TestAddr, 1, PocqTxnKind::ReadUnique,
        data, false, 0x90);
    second.commitRead(
        TestAddr + 64, 2, PocqTxnKind::ReadUnique,
        data, false, 0x90);
    const HnfSLCSF::SeqVictim first_victim = first.frontPendingSeq();
    const HnfSLCSF::SeqVictim second_victim = second.frontPendingSeq();
    ASSERT_EQ(first_victim.id, second_victim.id);
    ASSERT_EQ(first_victim.blockAddr, second_victim.blockAddr);
    ASSERT_EQ(first_victim.owner, second_victim.owner);
    constexpr uint32_t CompletionTxn = 6701;
    first.markSeqIssued(
        first_victim.id, CleanInvalidOpcode, CompletionTxn);
    second.markSeqIssued(
        second_victim.id, CleanInvalidOpcode, CompletionTxn);
    const SlcSfResponse first_response = awaitVisibleResponse(
        first, makeSlcSfCompleteSfEvictReq(
            seqCompletionHeader(1701, first_victim, CompletionTxn),
            SlcSfSeqId{first_victim.id}, {}, false));
    const SlcSfResponse second_response = awaitVisibleResponse(
        second, makeSlcSfCompleteSfEvictReq(
            seqCompletionHeader(1701, second_victim, CompletionTxn),
            SlcSfSeqId{second_victim.id}, {}, false));

    EXPECT_EQ(first.acknowledgeVisibleCompletion(second_response),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(second.acknowledgeVisibleCompletion(first_response),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(first.seqOccupancy(), 1);
    EXPECT_EQ(second.seqOccupancy(), 1);
    EXPECT_EQ(first.seqPhase(first_victim.id),
              HnfSLCSF::SeqPhase::CommittedAwaitAck);
    EXPECT_EQ(second.seqPhase(second_victim.id),
              HnfSLCSF::SeqPhase::CommittedAwaitAck);

    EXPECT_EQ(first.acknowledgeVisibleCompletion(first_response),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(second.acknowledgeVisibleCompletion(second_response),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(first.seqOccupancy(), 0);
    EXPECT_EQ(second.seqOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     CrossServiceReleaseResponsesFreeNeitherDirtyVictim)
{
    HnfSLCSFPipelineConfig config{};
    config.victimBufferEntries = 1;
    HnfSLCSF first(64, 1, 1, 1, 1, 8, config);
    HnfSLCSF second(64, 1, 1, 1, 1, 8, config);
    const auto prepare_victim = [](HnfSLCSF& model) {
        model.writeLine(
            TestAddr, 5, lineData(0xdc), PocqTxnKind::WriteUnique);
        const uint64_t replacement_addr = TestAddr + 64;
        const auto token =
            completeLookupToken(model, 1750, replacement_addr);
        const SlcSfResponse fill = completeMutation(
            model, makeSlcSfFillCleanSharedReq(
                mutationHeader(1751, replacement_addr), lineData(0xdd), {},
                token, token.lookupReqId));
        const auto& payload =
            std::get<SlcSfFillResponse>(fill.payload());
        EXPECT_TRUE(payload.slcVictim.has_value());
        return *payload.slcVictim;
    };
    const SlcSfSlcVictim first_victim = prepare_victim(first);
    const SlcSfSlcVictim second_victim = prepare_victim(second);
    ASSERT_EQ(first_victim.victimId.value, second_victim.victimId.value);
    ASSERT_EQ(first_victim.lineAddress, second_victim.lineAddress);
    ASSERT_EQ(first_victim.owner, second_victim.owner);
    constexpr uint32_t WritebackTxn = 6752;
    first.markDirtyVictimWritebackIssued(
        first_victim.victimId, first_victim.owner,
        WriteNoSnpFullOpcode, WritebackTxn);
    second.markDirtyVictimWritebackIssued(
        second_victim.victimId, second_victim.owner,
        WriteNoSnpFullOpcode, WritebackTxn);
    const SlcSfResponse first_response = awaitVisibleResponse(
        first, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1752, first_victim.victimId, first_victim.lineAddress,
                first_victim.owner, WritebackTxn),
            first_victim.victimId));
    const SlcSfResponse second_response = awaitVisibleResponse(
        second, makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1752, second_victim.victimId, second_victim.lineAddress,
                second_victim.owner, WritebackTxn),
            second_victim.victimId));

    EXPECT_EQ(first.acknowledgeVisibleCompletion(second_response),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(second.acknowledgeVisibleCompletion(first_response),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(first.dirtyVictimState(first_victim.victimId),
              HnfSLCSF::VictimState::ReleaseCommittedAwaitAck);
    EXPECT_EQ(second.dirtyVictimState(second_victim.victimId),
              HnfSLCSF::VictimState::ReleaseCommittedAwaitAck);
    EXPECT_EQ(first.victimBufferOccupancy(), 1);
    EXPECT_EQ(second.victimBufferOccupancy(), 1);

    EXPECT_EQ(first.acknowledgeVisibleCompletion(first_response),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(second.acknowledgeVisibleCompletion(second_response),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(first.victimBufferOccupancy(), 0);
    EXPECT_EQ(second.victimBufferOccupancy(), 0);
}

TEST(HnfSlcSfMutationServiceTest,
     DirtyVictimCapacityIsHeldUntilExactReleaseAck)
{
    HnfSLCSFPipelineConfig config{};
    config.victimBufferEntries = 1;
    HnfSLCSF model(64, 1, 1, 1, 1, 8, config);
    const uint64_t victim_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    const uint64_t third_addr = TestAddr + 128;
    model.writeLine(
        victim_addr, 2, lineData(0xdc), PocqTxnKind::WriteUnique);
    const auto replacement_token =
        completeLookupToken(model, 1800, replacement_addr);
    const SlcSfResponse fill = completeMutation(
        model, makeSlcSfFillCleanSharedReq(
            mutationHeader(1801, replacement_addr), lineData(0xdd), {},
            replacement_token, replacement_token.lookupReqId));
    const auto& fill_payload =
        std::get<SlcSfFillResponse>(fill.payload());
    ASSERT_TRUE(fill_payload.slcVictim.has_value());
    const SlcSfSlcVictim victim = *fill_payload.slcVictim;
    model.writeLine(
        replacement_addr, 3, lineData(0xde),
        PocqTxnKind::WriteUnique);
    const auto third_token =
        completeLookupToken(model, 1802, third_addr);

    constexpr uint32_t WritebackTxn = 6803;
    model.markDirtyVictimWritebackIssued(
        victim.victimId, victim.owner, WriteNoSnpFullOpcode,
        WritebackTxn);
    SlcSfReqHeader forged_header = dirtyVictimReleaseHeader(
        1899, victim.victimId, victim.lineAddress, victim.owner,
        WritebackTxn);
    forged_header.trace.transactionId += 1;
    const SlcSfResponse forged_release = completeMutation(
        model, makeSlcSfReleaseDirtyVictimReq(
            std::move(forged_header), victim.victimId));
    EXPECT_EQ(forged_release.status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::WritebackIssued);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);

    SlcSfRequest release_request = makeSlcSfReleaseDirtyVictimReq(
        dirtyVictimReleaseHeader(
            1803, victim.victimId, victim.lineAddress, victim.owner,
            WritebackTxn),
        victim.victimId);
    const SlcSfUpdateReq typed_release =
        std::get<SlcSfUpdateReq>(release_request);
    ASSERT_EQ(model.tryEnqueue(std::move(release_request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 32 && model.respPendingCount() == 0; ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::ReleaseCommittedAwaitAck);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);

    model.wakeup();
    ASSERT_NE(model.frontVisibleResponse(), nullptr);
    const SlcSfResponse visible_release =
        *model.frontVisibleResponse();
    EXPECT_EQ(model.acknowledgeVisibleCompletion(
                  makeSlcSfDoneResponse(typed_release)),
              SlcSfCompletionAckResult::IdentityMismatch);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::ReleaseCommittedAwaitAck);

    SlcSfRequest blocked_fill = makeSlcSfFillCleanSharedReq(
        mutationHeader(1804, third_addr), lineData(0xdf), {},
        third_token, third_token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(blocked_fill)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0;
         cycle < 32 && model.respPendingCount() == 0; ++cycle) {
        model.wakeup();
    }
    ASSERT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.victimBufferOccupancy(), 1);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::ReleaseCommittedAwaitAck);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, third_addr}).result.slcHit);

    EXPECT_EQ(model.acknowledgeVisibleCompletion(visible_release),
              SlcSfCompletionAckResult::Acknowledged);
    EXPECT_EQ(model.victimBufferOccupancy(), 0);
    EXPECT_EQ(model.dirtyVictimState(victim.victimId),
              HnfSLCSF::VictimState::Released);
    model.wakeup();
    auto blocked_response = model.popVisibleResponse();
    ASSERT_TRUE(blocked_response.has_value());
    ASSERT_EQ(blocked_response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(std::get<SlcSfReplay>(blocked_response->payload()).reason,
              SlcSfReplayReason::VictimBufferFull);
}

TEST(HnfSlcSfLookupPipelineTest, LookupHasConfiguredLatency)
{
    const HnfSLCSFPipelineConfig config{2, 2, 1, 1, 1, 1, 3};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto request = lookupRequest(71);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    ASSERT_EQ(model.currentCycle(), 1);
    ASSERT_EQ(model.reqInflightCount(), 1);

    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 3);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.respPendingCount(), 0);

    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 4);
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);
}

enum class BaseServiceLatencyCase
{
    Lookup,
    FillMapped,
    FillOne,
    FillTwo,
    UpdateMapped,
    UpdateOne,
    UpdateTwo,
    ReleaseDirtyVictim,
    OrdinaryEvictRemoveSharer,
    FlushL3,
    EarlyLookupReplay,
    EarlyMutationReplay
};

class HnfSlcSfBaseServiceLatencyTest :
    public testing::TestWithParam<BaseServiceLatencyCase>
{};

TEST_P(HnfSlcSfBaseServiceLatencyTest, CompletesAtMappedServiceDeadline)
{
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 5;
    config.fillLatency = 6;
    config.updateLatency = 4;
    if (GetParam() == BaseServiceLatencyCase::FillOne ||
        GetParam() == BaseServiceLatencyCase::UpdateOne) {
        config.fillLatency = 1;
        config.updateLatency = 1;
    } else if (GetParam() == BaseServiceLatencyCase::FillTwo ||
               GetParam() == BaseServiceLatencyCase::UpdateTwo) {
        config.fillLatency = 2;
        config.updateLatency = 2;
    }
    HnfSLCSF model(64, 4, 2, 1, 1, 8, config);

    SlcSfRequest request;
    std::optional<SlcSfResponse> seq_completion;
    uint64_t expected_latency = 0;
    switch (GetParam()) {
      case BaseServiceLatencyCase::Lookup:
        request = lookupRequest(1400);
        expected_latency = config.lookupLatency;
        break;
      case BaseServiceLatencyCase::FillMapped:
      case BaseServiceLatencyCase::FillOne:
      case BaseServiceLatencyCase::FillTwo: {
        const auto token = completeLookupToken(model, 1401, TestAddr);
        request = makeSlcSfFillCleanSharedReq(
            mutationHeader(1402), lineData(0x91), {}, token,
            token.lookupReqId);
        expected_latency = config.fillLatency;
        break;
      }
      case BaseServiceLatencyCase::UpdateMapped:
      case BaseServiceLatencyCase::UpdateOne:
      case BaseServiceLatencyCase::UpdateTwo: {
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x92), false);
        const auto token = completeLookupToken(model, 1403, TestAddr);
        request = makeSlcSfCompleteMaintenanceReq(
            mutationHeader(1404), PocqTxnKind::MakeInvalid, 0x90, token,
            token.lookupReqId);
        expected_latency = config.updateLatency;
        break;
      }
      case BaseServiceLatencyCase::ReleaseDirtyVictim: {
        model.writeLine(
            TestAddr, 0, lineData(0x98), PocqTxnKind::WriteUnique);
        const uint64_t replacement_addr = TestAddr + 64;
        const auto token = completeLookupToken(
            model, 1414, replacement_addr);
        SlcSfRequest fill = makeSlcSfFillCleanSharedReq(
            mutationHeader(1415, replacement_addr), lineData(0x99), {},
            token, token.lookupReqId);
        ASSERT_EQ(model.tryEnqueue(std::move(fill)),
                  SlcSfEnqueueResult::Accepted);
        std::optional<SlcSfResponse> fill_response;
        for (size_t cycle = 0; cycle < 16 && !fill_response; ++cycle) {
            model.wakeup(900 + cycle);
            fill_response = model.popVisibleResponse();
        }
        ASSERT_TRUE(fill_response.has_value());
        const auto& fill_payload =
            std::get<SlcSfFillResponse>(fill_response->payload());
        ASSERT_TRUE(fill_payload.slcVictim.has_value());
        const SlcSfVictimId victim_id =
            fill_payload.slcVictim->victimId;
        constexpr uint32_t WritebackTxn = 4416;
        model.markDirtyVictimWritebackIssued(
            victim_id, 0, WriteNoSnpFullOpcode, WritebackTxn);
        request = makeSlcSfReleaseDirtyVictimReq(
            dirtyVictimReleaseHeader(
                1416, victim_id, TestAddr, 0, WritebackTxn),
            victim_id);
        expected_latency = config.updateLatency;
        break;
      }
      case BaseServiceLatencyCase::OrdinaryEvictRemoveSharer: {
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x93), false);
        const auto token = completeLookupToken(model, 1405, TestAddr);
        request = makeSlcSfRemoveSharerReq(
            mutationHeader(1406), token, token.lookupReqId);
        expected_latency = config.updateLatency;
        break;
      }
      case BaseServiceLatencyCase::FlushL3: {
        const auto token = completeLookupToken(model, 1412, TestAddr);
        request = makeSlcSfFlushL3Req(
            mutationHeader(1413), token, token.lookupReqId);
        expected_latency = config.updateLatency;
        break;
      }
      case BaseServiceLatencyCase::EarlyLookupReplay: {
        const uint64_t replacement_addr = TestAddr + 64;
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadUnique, lineData(0x93), false);
        model.commitRead(
            replacement_addr, 8, PocqTxnKind::ReadUnique,
            lineData(0x94), false);
        ASSERT_TRUE(model.seqContains(TestAddr));
        const HnfSLCSF::SeqVictim victim = model.frontPendingSeq();
        constexpr uint32_t CompletionTxn = 7407;
        model.markSeqIssued(
            victim.id, CleanInvalidOpcode, CompletionTxn);
        seq_completion = awaitVisibleResponse(
            model, makeSlcSfCompleteSfEvictReq(
                seqCompletionHeader(1417, victim, CompletionTxn),
                SlcSfSeqId{victim.id}, {}, false));
        EXPECT_EQ(model.seqPhase(victim.id),
                  HnfSLCSF::SeqPhase::CommittedAwaitAck);
        request = lookupRequest(1407);
        expected_latency = 1;
        break;
      }
      case BaseServiceLatencyCase::EarlyMutationReplay: {
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x95), false);
        const auto stale_token =
            completeLookupToken(model, 1408, TestAddr);
        model.writeLine(
            TestAddr, 7, lineData(0x96), PocqTxnKind::WriteUnique);
        request = makeSlcSfFillCleanSharedReq(
            mutationHeader(1409), lineData(0x97), {}, stale_token,
            stale_token.lookupReqId);
        expected_latency = 1;
        break;
      }
    }

    const auto before_service = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const bool must_not_write_before_deadline =
        GetParam() != BaseServiceLatencyCase::Lookup &&
        GetParam() != BaseServiceLatencyCase::EarlyLookupReplay &&
        GetParam() != BaseServiceLatencyCase::EarlyMutationReplay;
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    const uint64_t issue_cycle = model.currentCycle();
    ASSERT_EQ(model.reqInflightCount(), 1);
    if (GetParam() == BaseServiceLatencyCase::EarlyLookupReplay) {
        ASSERT_TRUE(seq_completion.has_value());
        EXPECT_EQ(model.acknowledgeVisibleCompletion(*seq_completion),
                  SlcSfCompletionAckResult::Acknowledged);
        ASSERT_FALSE(model.seqContains(TestAddr));
    }
    while (model.respPendingCount() == 0) {
        model.wakeup(1000 + model.currentCycle());
        ASSERT_LE(model.currentCycle(), issue_cycle + expected_latency);
        if (must_not_write_before_deadline &&
            model.currentCycle() < issue_cycle + expected_latency) {
            const auto before_deadline = model.probe(HnfSlcLookupReq{
                0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
            expectArraySnapshotsEqual(
                before_deadline.snapshot.slc, before_service.snapshot.slc);
            expectArraySnapshotsEqual(
                before_deadline.snapshot.sf, before_service.snapshot.sf);
        }
    }
    EXPECT_EQ(model.currentCycle(), issue_cycle + expected_latency);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup(1000 + model.currentCycle());
    auto response = consumeVisibleResponse(model);
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(),
              (GetParam() == BaseServiceLatencyCase::EarlyLookupReplay ||
               GetParam() == BaseServiceLatencyCase::EarlyMutationReplay) ?
                  SlcSfTerminalStatus::Replay :
                  SlcSfTerminalStatus::Done);
    if (GetParam() == BaseServiceLatencyCase::ReleaseDirtyVictim) {
        EXPECT_EQ(response->operationKind(),
                  SlcSfOperationKind::ReleaseDirtyVictim);
        EXPECT_EQ(model.victimBufferOccupancy(), 0);
    }
    if (GetParam() == BaseServiceLatencyCase::OrdinaryEvictRemoveSharer) {
        EXPECT_EQ(response->operationKind(),
                  SlcSfOperationKind::RemoveSharer);
        const auto after = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        EXPECT_TRUE(after.result.slcHit);
        EXPECT_FALSE(after.result.sfHit);
    } else if (GetParam() == BaseServiceLatencyCase::FlushL3) {
        EXPECT_EQ(response->operationKind(), SlcSfOperationKind::FlushL3);
        const auto after = model.probe(HnfSlcLookupReq{
            0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
        EXPECT_FALSE(after.result.slcHit);
        expectArraySnapshotsEqual(
            after.snapshot.sf, before_service.snapshot.sf);
    }
}

INSTANTIATE_TEST_SUITE_P(
    OperationMapping, HnfSlcSfBaseServiceLatencyTest,
    testing::Values(
        BaseServiceLatencyCase::Lookup,
        BaseServiceLatencyCase::FillMapped,
        BaseServiceLatencyCase::FillOne,
        BaseServiceLatencyCase::FillTwo,
        BaseServiceLatencyCase::UpdateMapped,
        BaseServiceLatencyCase::UpdateOne,
        BaseServiceLatencyCase::UpdateTwo,
        BaseServiceLatencyCase::OrdinaryEvictRemoveSharer,
        BaseServiceLatencyCase::FlushL3,
        BaseServiceLatencyCase::EarlyLookupReplay,
        BaseServiceLatencyCase::EarlyMutationReplay));

TEST(HnfSlcSfLookupPipelineTest,
     ShortFillLatencyStopsCatchUpOnResourceStall)
{
    HnfSLCSFPipelineConfig config{};
    config.fillLatency = 1;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto token = completeLookupToken(model, 1410, TestAddr);
    ASSERT_TRUE(model.tryReserveSfResources(
        999, TestAddr, PocqTxnKind::ReadShared));
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(1411), lineData(0x96), {}, token,
        token.lookupReqId);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), issue_cycle + 1);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_TRUE(model.hasSfReservation(999));
    EXPECT_FALSE(model.hasSfReservation(1411));
    EXPECT_EQ(model.sfReservationCount(), 1);
    EXPECT_EQ(model.seqReservationCount(), 0);
    const auto stalled = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(stalled.result, before.result);
    expectArraySnapshotsEqual(stalled.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(stalled.snapshot.sf, before.snapshot.sf);

    model.releaseSfResources(999);
    EXPECT_EQ(model.sfReservationCount(), 0);
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), issue_cycle + 2);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_TRUE(model.hasSfReservation(1411));
    EXPECT_FALSE(model.hasSfReservation(999));
    EXPECT_EQ(model.sfReservationCount(), 1);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 0);
    const auto prepared = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(prepared.result, before.result);
    expectArraySnapshotsEqual(prepared.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(prepared.snapshot.sf, before.snapshot.sf);

    model.wakeup();
    EXPECT_EQ(model.currentCycle(), issue_cycle + 3);
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_EQ(model.respVisibleCount(), 0);
    EXPECT_FALSE(model.hasSfReservation(1411));
    const auto after_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_TRUE(after_write.result.slcHit);
    EXPECT_TRUE(after_write.result.sfHit);
    EXPECT_EQ(after_write.result.data, lineData(0x96));
    EXPECT_NE(after_write.snapshot.slc.generation, token.slc.generation);
    EXPECT_NE(after_write.snapshot.sf.generation, token.sf.generation);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);

    model.wakeup();
    EXPECT_EQ(model.currentCycle(), issue_cycle + 4);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(response->reqId(), SlcSfReqId{1411});
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    model.wakeup();
    model.wakeup();
    const auto after_idle = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after_idle.result, after_write.result);
    expectArraySnapshotsEqual(after_idle.snapshot.slc, after_write.snapshot.slc);
    expectArraySnapshotsEqual(after_idle.snapshot.sf, after_write.snapshot.sf);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
}

TEST(HnfSlcSfLookupPipelineTest, NoZeroCycleLoop)
{
    const HnfSLCSFPipelineConfig config{1, 1, 1, 1, 1, 1, 1};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto request = lookupRequest(72);

    ASSERT_EQ(model.currentCycle(), 0);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    EXPECT_EQ(model.reqIngressCount(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());

    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 1);
    EXPECT_EQ(model.reqInflightCount(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 2);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 3);
    EXPECT_TRUE(model.popVisibleResponse().has_value());
}

TEST(HnfSlcSfLookupPipelineTest, ZeroLatencyConfigurationIsRejected)
{
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 0;
    EXPECT_THROW(HnfSLCSF(64, 4, 2, 4, 2, 8, config),
                 std::invalid_argument);
}

TEST(HnfSlcSfLookupPipelineTest, ExactLookupTimestamps)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    auto request = lookupRequest(73);
    const uint64_t accepted_cycle = model.currentCycle();

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();
    ASSERT_EQ(model.reqInflightCount(), 1);
    for (size_t cycle = 0; cycle < 3; ++cycle) {
        model.wakeup();
        ASSERT_EQ(model.reqInflightCount(), 1);
    }
    model.wakeup();
    const uint64_t complete_cycle = model.currentCycle();
    ASSERT_EQ(model.respPendingCount(), 1);
    model.wakeup();
    const uint64_t visible_cycle = model.currentCycle();
    ASSERT_EQ(model.respVisibleCount(), 1);

    EXPECT_EQ(accepted_cycle, 0);
    EXPECT_EQ(issue_cycle, accepted_cycle + 1);
    EXPECT_EQ(complete_cycle, issue_cycle + 4);
    EXPECT_EQ(visible_cycle, complete_cycle + 1);
}

TEST(HnfSlcSfLookupPipelineTest, ReturnsSeededBackendResultAfterLatency)
{
    const HnfSLCSFPipelineConfig config{2, 2, 1, 1, 1, 1, 2};
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    const auto data = lineData(0x90);
    model.commitRead(
        TestAddr, 4, PocqTxnKind::ReadShared, data, true);
    auto request = lookupRequest(74, TestAddr, 704);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 2);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_FALSE(model.popVisibleResponse().has_value());

    model.wakeup();
    EXPECT_EQ(model.currentCycle(), 3);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup();
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(response->pocEntryId(), 704);
    const auto& payload =
        std::get<SlcSfLookupResponse>(response->payload());
    EXPECT_TRUE(payload.result.valid);
    EXPECT_EQ(payload.result.entry, 704);
    EXPECT_TRUE(payload.result.slcHit);
    EXPECT_TRUE(payload.result.sfHit);
    EXPECT_TRUE(payload.result.dataDirty);
    EXPECT_EQ(payload.result.data, data);
    EXPECT_EQ(payload.result.rnfvec, 1ULL << 4);
    EXPECT_EQ(payload.token.lookupReqId, SlcSfReqId{74});
    EXPECT_EQ(payload.token.lineAddress, TestAddr);
}

TEST(HnfSlcSfLookupPipelineTest, SeqConflictReturnsRegisteredReplay)
{
    HnfSLCSFPipelineConfig config{};
    config.lookupLatency = 9;
    config.replayPenalty = 3;
    config.childClockPeriod = 10;
    HnfSLCSF model(64, 4, 2, 1, 1, 1, config);
    const uint64_t victim_addr = TestAddr;
    const uint64_t replacement_addr = TestAddr + 64;
    const auto data = lineData(0xa0);
    model.commitRead(
        victim_addr, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.commitRead(
        replacement_addr, 4, PocqTxnKind::ReadUnique, data, false, 0x90);
    ASSERT_TRUE(model.seqContains(victim_addr));
    const HnfSLCSF::SeqVictim victim = model.frontPendingSeq();
    constexpr uint32_t CompletionTxn = 7075;
    model.markSeqIssued(
        victim.id, CleanInvalidOpcode, CompletionTxn);
    const SlcSfResponse completion = awaitVisibleResponse(
        model, makeSlcSfCompleteSfEvictReq(
            seqCompletionHeader(7075, victim, CompletionTxn),
            SlcSfSeqId{victim.id}, {}, false));
    auto request = lookupRequest(75, victim_addr, 705);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    EXPECT_EQ(model.acknowledgeVisibleCompletion(completion),
              SlcSfCompletionAckResult::Acknowledged);
    ASSERT_FALSE(model.seqContains(victim_addr));
    model.wakeup(1010);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup(1020);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(response->operationKind(), SlcSfOperationKind::Lookup);
    EXPECT_EQ(response->pocEntryId(), 705);
    const auto& replay = std::get<SlcSfReplay>(response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::SeqConflict);
    EXPECT_TRUE(replay.redoLookup);
    EXPECT_EQ(replay.retryNotBeforeTick, 1040);
}

TEST(HnfSlcSfLookupPipelineTest, PreservesEveryLookupFactAndCommitToken)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0xb0);
    model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(
        TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    SlcSfResponse response = completeLookup(
        model, lookupRequest(
            81, TestAddr, 801, PocqTxnKind::ReadUnique, 8));
    ASSERT_EQ(response.status(), SlcSfTerminalStatus::Done);
    const auto& payload =
        std::get<SlcSfLookupResponse>(response.payload());
    const auto& result = payload.result;
    EXPECT_TRUE(result.valid);
    EXPECT_EQ(result.entry, 801);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.sfHit);
    EXPECT_EQ(result.slcState, HnfSlcState::EN);
    EXPECT_EQ(result.sfState, HnfSfState::EN);
    EXPECT_EQ(result.data, data);
    EXPECT_FALSE(result.dataDirty);
    EXPECT_FALSE(result.mcreqNonspec);
    EXPECT_TRUE(result.snoopBroadcast);
    EXPECT_FALSE(result.snoopDirected);
    EXPECT_EQ(result.snoopOpcode, 0x07);
    EXPECT_EQ(result.snoopTargets, (1ULL << 0) | (1ULL << 4));
    EXPECT_EQ(result.rnfid, 0);
    EXPECT_EQ(result.rnfvec, (1ULL << 0) | (1ULL << 4));

    const auto& token = payload.token;
    EXPECT_EQ(token.lookupReqId, SlcSfReqId{81});
    EXPECT_EQ(token.lineAddress, TestAddr);
    EXPECT_EQ(token.lookupEpoch, 1);
    EXPECT_TRUE(token.slc.hit);
    EXPECT_EQ(token.slc.set, 0);
    EXPECT_LT(token.slc.way, 2);
    EXPECT_NE(token.slc.generation, 0);
    EXPECT_TRUE(token.sf.hit);
    EXPECT_EQ(token.sf.set, 0);
    EXPECT_LT(token.sf.way, 2);
    EXPECT_NE(token.sf.generation, 0);
}

TEST(HnfSlcSfLookupPipelineTest, ReportsFourSlcSfHitMissCombinations)
{
    const auto data = lineData(0xc0);

    HnfSLCSF neither_sync(64, 4, 2, 4, 2);
    HnfSLCSF neither_pipe(64, 4, 2, 4, 2);
    const auto neither_expected = lookup(
        neither_sync, TestAddr, 7, PocqTxnKind::ReadShared);
    auto neither_response = completeLookup(
        neither_pipe, lookupRequest(
            82, TestAddr, 3, PocqTxnKind::ReadShared, 7));
    const auto& neither_payload =
        std::get<SlcSfLookupResponse>(neither_response.payload());
    expectLookupResultsEqual(neither_payload.result, neither_expected);
    EXPECT_FALSE(neither_payload.result.slcHit);
    EXPECT_FALSE(neither_payload.result.sfHit);
    EXPECT_TRUE(neither_payload.result.mcreqNonspec);
    EXPECT_EQ(neither_payload.result.slcState, HnfSlcState::I);
    EXPECT_EQ(neither_payload.result.sfState, HnfSfState::I);
    EXPECT_FALSE(neither_payload.token.slc.hit);
    EXPECT_FALSE(neither_payload.token.sf.hit);

    HnfSLCSF slc_sync(64, 4, 2, 4, 2);
    HnfSLCSF slc_pipe(64, 4, 2, 4, 2);
    for (HnfSLCSF* model : {&slc_sync, &slc_pipe}) {
        model->writeLine(
            TestAddr, 0, data, PocqTxnKind::WriteUnique);
    }
    const auto slc_expected = lookup(
        slc_sync, TestAddr, 7, PocqTxnKind::ReadShared);
    auto slc_response = completeLookup(
        slc_pipe, lookupRequest(
            83, TestAddr, 3, PocqTxnKind::ReadShared, 7));
    const auto& slc_payload =
        std::get<SlcSfLookupResponse>(slc_response.payload());
    expectLookupResultsEqual(slc_payload.result, slc_expected);
    EXPECT_TRUE(slc_payload.result.slcHit);
    EXPECT_FALSE(slc_payload.result.sfHit);
    EXPECT_EQ(slc_payload.result.slcState, HnfSlcState::MU);
    EXPECT_EQ(slc_payload.result.data, data);
    EXPECT_TRUE(slc_payload.result.dataDirty);
    EXPECT_FALSE(slc_payload.result.mcreqNonspec);
    EXPECT_TRUE(slc_payload.token.slc.hit);
    EXPECT_FALSE(slc_payload.token.sf.hit);

    HnfSLCSF sf_sync(64, 4, 2, 4, 2);
    HnfSLCSF sf_pipe(64, 4, 2, 4, 2);
    for (HnfSLCSF* model : {&sf_sync, &sf_pipe}) {
        model->commitRead(
            TestAddr, 0, PocqTxnKind::ReadUnique, data, false);
    }
    const auto sf_expected = lookup(
        sf_sync, TestAddr, 7, PocqTxnKind::ReadUnique);
    auto sf_response = completeLookup(
        sf_pipe, lookupRequest(
            84, TestAddr, 3, PocqTxnKind::ReadUnique, 7));
    const auto& sf_payload =
        std::get<SlcSfLookupResponse>(sf_response.payload());
    expectLookupResultsEqual(sf_payload.result, sf_expected);
    EXPECT_FALSE(sf_payload.result.slcHit);
    EXPECT_TRUE(sf_payload.result.sfHit);
    EXPECT_EQ(sf_payload.result.sfState, HnfSfState::EU);
    EXPECT_TRUE(sf_payload.result.snoopDirected);
    EXPECT_FALSE(sf_payload.result.snoopBroadcast);
    EXPECT_EQ(sf_payload.result.snoopOpcode, 0x07);
    EXPECT_EQ(sf_payload.result.snoopTargets, 1ULL << 0);
    EXPECT_EQ(sf_payload.result.rnfid, 0);
    EXPECT_EQ(sf_payload.result.rnfvec, 1ULL << 0);
    EXPECT_FALSE(sf_payload.token.slc.hit);
    EXPECT_TRUE(sf_payload.token.sf.hit);

    HnfSLCSF both_sync(64, 4, 2, 4, 2);
    HnfSLCSF both_pipe(64, 4, 2, 4, 2);
    for (HnfSLCSF* model : {&both_sync, &both_pipe}) {
        model->commitRead(
            TestAddr, 0, PocqTxnKind::ReadShared, data, true);
    }
    const auto both_expected = lookup(
        both_sync, TestAddr, 7, PocqTxnKind::ReadUnique);
    auto both_response = completeLookup(
        both_pipe, lookupRequest(
            85, TestAddr, 3, PocqTxnKind::ReadUnique, 7));
    const auto& both_payload =
        std::get<SlcSfLookupResponse>(both_response.payload());
    expectLookupResultsEqual(both_payload.result, both_expected);
    EXPECT_TRUE(both_payload.result.slcHit);
    EXPECT_TRUE(both_payload.result.sfHit);
    EXPECT_EQ(both_payload.result.slcState, HnfSlcState::MN);
    EXPECT_EQ(both_payload.result.sfState, HnfSfState::SN);
    EXPECT_EQ(both_payload.result.data, data);
    EXPECT_TRUE(both_payload.result.dataDirty);
    EXPECT_TRUE(both_payload.result.snoopBroadcast);
    EXPECT_EQ(both_payload.result.snoopOpcode, 0x07);
    EXPECT_EQ(both_payload.result.snoopTargets, 1ULL << 0);
    EXPECT_TRUE(both_payload.token.slc.hit);
    EXPECT_TRUE(both_payload.token.sf.hit);
    EXPECT_NE(both_payload.token.slc.generation, 0);
    EXPECT_NE(both_payload.token.sf.generation, 0);
    EXPECT_EQ(both_payload.token.lookupEpoch, 1);
}

TEST(HnfSlcSfLookupPipelineTest, SeqHitReplayHasNoSideEffects)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0xd0);
    model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    HnfSlcLookupReq request{};
    request.entry = 9;
    request.blockAddr = TestAddr;
    request.req.srcid = 4;
    request.txn = PocqTxnKind::ReadUnique;

    const auto before = model.probe(request);
    const auto after = model.probe(request);
    EXPECT_EQ(model.currentLookupEpoch(), 1);
    EXPECT_EQ(model.currentLookupAccessCount(), 0);
    expectLookupResultsEqual(after.result, before.result);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);

    HnfSLCSFBackend::LookupSnapshot committed{};
    EXPECT_FALSE(model.lookup(request, &committed).replay);
    EXPECT_EQ(committed.lookupEpoch, 1);
    EXPECT_EQ(model.currentLookupEpoch(), 1);
    EXPECT_EQ(model.currentLookupAccessCount(), 1);
    const auto after_one_access = model.probe(request);
    EXPECT_EQ(after_one_access.snapshot.slc.generation,
              before.snapshot.slc.generation);
    EXPECT_EQ(after_one_access.snapshot.sf.generation,
              before.snapshot.sf.generation);
    EXPECT_GT(after_one_access.snapshot.slc.replacementStamp,
              before.snapshot.slc.replacementStamp);
    EXPECT_GT(after_one_access.snapshot.sf.replacementStamp,
              before.snapshot.sf.replacementStamp);
    EXPECT_FALSE(model.lookup(request, &committed).replay);
    EXPECT_EQ(committed.lookupEpoch, 1);
    EXPECT_EQ(model.currentLookupEpoch(), 1);
    EXPECT_EQ(model.currentLookupAccessCount(), 2);
    const auto after_two_accesses = model.probe(request);
    EXPECT_EQ(after_two_accesses.snapshot.slc.generation,
              after_one_access.snapshot.slc.generation);
    EXPECT_EQ(after_two_accesses.snapshot.sf.generation,
              after_one_access.snapshot.sf.generation);
    EXPECT_GT(after_two_accesses.snapshot.slc.replacementStamp,
              after_one_access.snapshot.slc.replacementStamp);
    EXPECT_GT(after_two_accesses.snapshot.sf.replacementStamp,
              after_one_access.snapshot.sf.replacementStamp);

    HnfSLCSF replay_model(64, 4, 2, 1, 1, 1);
    const uint64_t replacement_addr = TestAddr + 64;
    replay_model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    replay_model.commitRead(
        replacement_addr, 4, PocqTxnKind::ReadUnique,
        data, false, 0x90);
    ASSERT_TRUE(replay_model.seqContains(TestAddr));
    ASSERT_TRUE(replay_model.hasPendingSeq());
    const auto replay_before = replay_model.probe(request);
    EXPECT_TRUE(replay_before.result.replay);
    const uint64_t epoch_before = replay_model.currentLookupEpoch();
    const uint64_t accesses_before =
        replay_model.currentLookupAccessCount();
    const size_t occupancy_before = replay_model.seqOccupancy();
    const auto victim_before = replay_model.frontPendingSeq();
    EXPECT_TRUE(replay_model.lookup(request, &committed).replay);
    EXPECT_EQ(replay_model.currentLookupEpoch(), epoch_before);
    EXPECT_EQ(replay_model.currentLookupAccessCount(), accesses_before);
    const auto replay_after = replay_model.probe(request);
    expectLookupResultsEqual(replay_after.result, replay_before.result);
    expectArraySnapshotsEqual(
        replay_after.snapshot.slc, replay_before.snapshot.slc);
    expectArraySnapshotsEqual(
        replay_after.snapshot.sf, replay_before.snapshot.sf);
    EXPECT_EQ(replay_model.seqOccupancy(), occupancy_before);
    expectSeqVictimsEqual(
        replay_model.frontPendingSeq(), victim_before);
}

TEST(HnfSlcSfCommitTokenTest, LookupTouchDoesNotInvalidateToken)
{
    HnfSLCSF model(64, 1, 2, 1, 2);
    model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, lineData(0xe1), false);
    model.commitRead(
        TestAddr + 64, 1, PocqTxnKind::ReadShared, lineData(0xe2), false);
    const uint64_t miss_addr = TestAddr + 128;
    const SlcSfReqId lookup_id{91};
    const SlcSfCommitToken token =
        completeLookupToken(model, lookup_id.value, miss_addr);
    ASSERT_FALSE(token.slc.hit);
    ASSERT_FALSE(token.sf.hit);
    const uint64_t accesses_before = model.currentLookupAccessCount();

    EXPECT_TRUE(model.validateCommitToken(token, lookup_id, miss_addr));
    EXPECT_EQ(model.currentLookupAccessCount(), accesses_before);
    lookup(model, TestAddr, 7, PocqTxnKind::ReadShared);
    EXPECT_EQ(model.currentLookupAccessCount(), accesses_before + 1);
    const auto after_touch = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, miss_addr});
    EXPECT_FALSE(after_touch.snapshot.slc.hit);
    EXPECT_FALSE(after_touch.snapshot.sf.hit);
    EXPECT_NE(after_touch.snapshot.slc.way, token.slc.way);
    EXPECT_NE(after_touch.snapshot.sf.way, token.sf.way);
    EXPECT_TRUE(model.validateCommitToken(token, lookup_id, miss_addr));
    EXPECT_EQ(model.currentLookupAccessCount(), accesses_before + 1);

    SlcSfCommitToken changed = token;
    changed.lookupReqId = SlcSfReqId{92};
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    changed.lineAddress += 64;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    changed.slc.hit = !changed.slc.hit;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.slc.set;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.slc.way;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.slc.generation;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    changed.sf.hit = !changed.sf.hit;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.sf.set;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.sf.way;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
    changed = token;
    ++changed.sf.generation;
    EXPECT_FALSE(model.validateCommitToken(changed, lookup_id, miss_addr));
}

TEST(HnfSlcSfCommitTokenTest, NoOpDirectoryUpdatePreservesGeneration)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    model.commitRead(
        TestAddr, 0, PocqTxnKind::ReadShared, lineData(0xe2), false);
    HnfSlcLookupReq request{};
    request.blockAddr = TestAddr;
    const auto before = model.probe(request);

    model.removeSharer(TestAddr, 7);

    const auto after = model.probe(request);
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
}

TEST(HnfSlcSfCommitTokenTest, CommittedHitMissAndGenerationChangesInvalidate)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const SlcSfReqId miss_id{93};
    const SlcSfCommitToken miss =
        completeLookupToken(model, miss_id.value, TestAddr);
    ASSERT_FALSE(miss.slc.hit);
    model.writeLine(
        TestAddr, 0, lineData(0xe2), PocqTxnKind::WriteUnique);
    EXPECT_FALSE(model.validateCommitToken(miss, miss_id, TestAddr));

    const SlcSfReqId hit_id{94};
    const SlcSfCommitToken hit =
        completeLookupToken(model, hit_id.value, TestAddr);
    ASSERT_TRUE(hit.slc.hit);
    model.writeLine(
        TestAddr, 0, lineData(0xe3), PocqTxnKind::WriteUnique);
    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_TRUE(after.snapshot.slc.hit);
    EXPECT_EQ(after.snapshot.slc.way, hit.slc.way);
    EXPECT_NE(after.snapshot.slc.generation, hit.slc.generation);
    EXPECT_FALSE(model.validateCommitToken(hit, hit_id, TestAddr));
}

TEST(HnfSlcSfCommitTokenTest, TagChangeInSnapshottedWayInvalidatesToken)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const SlcSfReqId lookup_id{95};
    const SlcSfCommitToken token =
        completeLookupToken(model, lookup_id.value, TestAddr);
    ASSERT_FALSE(token.slc.hit);

    model.writeLine(
        TestAddr + 64, 0, lineData(0xe4), PocqTxnKind::WriteUnique);
    const auto current = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_FALSE(current.snapshot.slc.hit);
    EXPECT_EQ(current.snapshot.slc.way, token.slc.way);
    EXPECT_NE(current.snapshot.slc.generation, token.slc.generation);
    EXPECT_FALSE(model.validateCommitToken(token, lookup_id, TestAddr));
}

TEST(HnfSlcSfCommitTokenTest, EvictReallocateAbaInvalidatesOldToken)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    model.writeLine(
        TestAddr, 0, lineData(0xe5), PocqTxnKind::WriteUnique);
    const SlcSfReqId old_id{96};
    const SlcSfCommitToken old_token =
        completeLookupToken(model, old_id.value, TestAddr);
    ASSERT_TRUE(old_token.slc.hit);

    model.flushL3(TestAddr);
    model.writeLine(
        TestAddr + 64, 0, lineData(0xe6), PocqTxnKind::WriteUnique);
    model.flushL3(TestAddr + 64);
    model.writeLine(
        TestAddr, 0, lineData(0xe7), PocqTxnKind::WriteUnique);

    const SlcSfReqId fresh_id{97};
    const SlcSfCommitToken fresh_token =
        completeLookupToken(model, fresh_id.value, TestAddr);
    EXPECT_TRUE(fresh_token.slc.hit);
    EXPECT_EQ(fresh_token.slc.set, old_token.slc.set);
    EXPECT_EQ(fresh_token.slc.way, old_token.slc.way);
    EXPECT_NE(fresh_token.slc.generation, old_token.slc.generation);
    EXPECT_FALSE(model.validateCommitToken(old_token, old_id, TestAddr));
    EXPECT_TRUE(model.validateCommitToken(fresh_token, fresh_id, TestAddr));
}

TEST(HnfSlcSfCommitTokenTest, LookupEpochInvalidatesOutstandingTokens)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    const SlcSfReqId lookup_id{98};
    const SlcSfCommitToken token =
        completeLookupToken(model, lookup_id.value, TestAddr);
    ASSERT_TRUE(model.validateCommitToken(token, lookup_id, TestAddr));

    model.invalidateCommitTokens();
    EXPECT_NE(model.currentLookupEpoch(), token.lookupEpoch);
    EXPECT_FALSE(model.validateCommitToken(token, lookup_id, TestAddr));
}

TEST(HnfSlcSfLookupPipelineTest, MatchesSynchronousReplacementOrder)
{
    HnfSLCSF synchronous(64, 4, 2, 1, 2, 4);
    HnfSLCSF pipelined(64, 4, 2, 1, 2, 4);
    const uint64_t addr_a = TestAddr;
    const uint64_t addr_b = TestAddr + 64;
    const uint64_t addr_c = TestAddr + 128;
    const auto data = lineData(0xe0);
    for (HnfSLCSF* model : {&synchronous, &pipelined}) {
        model->commitRead(
            addr_a, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
        model->commitRead(
            addr_b, 4, PocqTxnKind::ReadUnique, data, false, 0x90);
    }

    EXPECT_FALSE(lookup(
        synchronous, addr_a, 8, PocqTxnKind::ReadShared).replay);
    auto response = completeLookup(
        pipelined, lookupRequest(
            86, addr_a, 806, PocqTxnKind::ReadShared, 8));
    ASSERT_EQ(response.status(), SlcSfTerminalStatus::Done);

    synchronous.commitRead(
        addr_c, 8, PocqTxnKind::ReadUnique, data, false, 0x90);
    pipelined.commitRead(
        addr_c, 8, PocqTxnKind::ReadUnique, data, false, 0x90);
    ASSERT_TRUE(synchronous.hasPendingSeq());
    ASSERT_TRUE(pipelined.hasPendingSeq());
    EXPECT_EQ(synchronous.frontPendingSeq().blockAddr, addr_b);
    expectSeqVictimsEqual(
        pipelined.frontPendingSeq(), synchronous.frontPendingSeq());
}

TEST(HnfSlcSfTest, ReadUniqueBroadcastsToOtherVectorSharers)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x10);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    const auto result = lookup(model, TestAddr, 8, PocqTxnKind::ReadUnique);
    ASSERT_TRUE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.sfState, HnfSfState::EN);
    EXPECT_TRUE(result.snoopBroadcast);
    EXPECT_FALSE(result.snoopDirected);
    EXPECT_EQ(result.snoopOpcode, 0x07);
    EXPECT_EQ(result.snoopTargets, (1ULL << 0) | (1ULL << 4));
}

TEST(HnfSlcSfTest, ReadUniqueExcludesRequesterFromBroadcast)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x20);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);

    const auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    EXPECT_TRUE(result.snoopBroadcast);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);
}

TEST(HnfSlcSfTest, ReadUniqueIsDirectedToDifferentUniqueOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x30);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique, data, false);
    auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    ASSERT_FALSE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.sfState, HnfSfState::EU);
    EXPECT_TRUE(result.snoopDirected);
    EXPECT_FALSE(result.snoopBroadcast);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadUnique, data, true);
    result = lookup(model, TestAddr, 4, PocqTxnKind::ReadUnique);
    EXPECT_FALSE(result.slcHit);
    EXPECT_EQ(result.sfState, HnfSfState::EU);
    EXPECT_EQ(result.rnfid, 4);
    EXPECT_EQ(result.rnfvec, 1ULL << 4);
    EXPECT_EQ(result.snoopTargets, 0);
}

TEST(HnfSlcSfTest, ReadSharedObtainsDirtyDataFromUniqueOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x40);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique, data, true);
    auto result = lookup(model, TestAddr, 4, PocqTxnKind::ReadShared);
    EXPECT_TRUE(result.snoopDirected);
    EXPECT_EQ(result.snoopOpcode, 0x01);
    EXPECT_EQ(result.snoopTargets, 1ULL << 0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, true);
    result = lookup(model, TestAddr, 8, PocqTxnKind::ReadShared);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.sfState, HnfSfState::SN);
    EXPECT_EQ(result.rnfvec, (1ULL << 0) | (1ULL << 4));
    EXPECT_EQ(result.snoopTargets, 0);
}

TEST(HnfSlcSfTest, EvictRemovesTheRequestingSharerOnly)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto data = lineData(0x50);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadShared, data, false);
    model.commitRead(TestAddr, 4, PocqTxnKind::ReadShared, data, false);
    model.removeSharer(TestAddr, 0);

    const auto result = lookup(model, TestAddr, 8, PocqTxnKind::ReadUnique);
    EXPECT_TRUE(result.slcHit);
    EXPECT_TRUE(result.sfHit);
    EXPECT_EQ(result.rnfvec, 1ULL << 4);
    EXPECT_EQ(result.snoopTargets, 1ULL << 4);
}

TEST(HnfSlcSfTest, SfVictimEntersSeqAndBlocksConflictingLookups)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 1);
    const uint64_t addrA = TestAddr;
    const uint64_t addrB = TestAddr + 64;
    const uint64_t addrC = TestAddr + 128;
    const auto data = lineData(0x60);

    model.commitRead(addrA, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    EXPECT_FALSE(lookup(model, addrB, 4, PocqTxnKind::ReadUnique).replay);
    model.commitRead(addrB, 4, PocqTxnKind::ReadUnique, data, false, 0x90);

    ASSERT_EQ(model.seqOccupancy(), 1);
    ASSERT_TRUE(model.hasPendingSeq());
    const auto victim = model.frontPendingSeq();
    EXPECT_EQ(victim.blockAddr, addrA);
    EXPECT_EQ(victim.homeNodeId, 0x90);
    EXPECT_EQ(victim.owner, 0);
    EXPECT_EQ(victim.sharers, 1ULL << 0);
    EXPECT_TRUE(lookup(model, addrA, 8, PocqTxnKind::ReadShared).replay);
    EXPECT_TRUE(lookup(model, addrC, 8, PocqTxnKind::ReadUnique).replay);

    constexpr uint32_t CompletionTxn = 7925;
    model.markSeqIssued(
        victim.id, CleanInvalidOpcode, CompletionTxn);
    const auto completion = completeIssuedSfEvict(
        model, victim, CompletionTxn, 3925);
    EXPECT_EQ(completion.status(), SlcSfTerminalStatus::Done);
    EXPECT_EQ(model.seqOccupancy(), 0);
    EXPECT_FALSE(lookup(model, addrC, 8, PocqTxnKind::ReadUnique).replay);
}

TEST(HnfSlcSfTest, DirtySeqSnoopDataIsPreservedInSlc)
{
    HnfSLCSF model(64, 4, 2, 1, 1, 1);
    const uint64_t addrA = TestAddr;
    const uint64_t addrB = TestAddr + 64;
    const auto oldData = lineData(0x70);
    const auto newData = lineData(0x80);

    model.commitRead(addrA, 0, PocqTxnKind::ReadUnique, oldData, false, 0x90);
    model.commitRead(addrB, 4, PocqTxnKind::ReadUnique, oldData, false, 0x90);

    const auto victim = model.frontPendingSeq();
    constexpr uint32_t CompletionTxn = 7943;
    model.markSeqIssued(
        victim.id, CleanInvalidOpcode, CompletionTxn);
    const auto completion = completeIssuedSfEvict(
        model, victim, CompletionTxn, 3943, newData, true);
    EXPECT_EQ(completion.status(), SlcSfTerminalStatus::Done);

    const auto result = lookup(model, addrA, 8, PocqTxnKind::ReadShared);
    ASSERT_TRUE(result.slcHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, newData);
    EXPECT_FALSE(result.sfHit);
}

TEST(HnfSlcSfTest, ReservationsPreventSeqSlotOvercommit)
{
    HnfSLCSF model(64, 8, 2, 2, 1, 1);
    const uint64_t set0A = TestAddr;
    const uint64_t set1A = TestAddr + 64;
    const uint64_t set0B = TestAddr + 128;
    const uint64_t set1B = TestAddr + 192;
    const auto data = lineData(0x90);

    model.commitRead(set0A, 0, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.commitRead(set1A, 4, PocqTxnKind::ReadUnique, data, false, 0x90);

    ASSERT_TRUE(model.tryReserveSfResources(
        0, set0B, PocqTxnKind::ReadUnique));
    EXPECT_TRUE(model.hasSfReservation(0));
    EXPECT_EQ(model.seqReservationCount(), 1);
    EXPECT_FALSE(model.tryReserveSfResources(
        2, set0B, PocqTxnKind::ReadUnique));
    EXPECT_FALSE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));

    model.commitRead(set0B, 8, PocqTxnKind::ReadUnique, data, false, 0x90);
    model.releaseSfResources(0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    ASSERT_EQ(model.seqOccupancy(), 1);
    EXPECT_FALSE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));

    const auto victim = model.frontPendingSeq();
    constexpr uint32_t CompletionTxn = 7982;
    model.markSeqIssued(
        victim.id, CleanInvalidOpcode, CompletionTxn);
    const auto completion = completeIssuedSfEvict(
        model, victim, CompletionTxn, 3982);
    EXPECT_EQ(completion.status(), SlcSfTerminalStatus::Done);
    ASSERT_TRUE(model.tryReserveSfResources(
        1, set1B, PocqTxnKind::ReadUnique));
    model.releaseSfResources(1);
}

TEST(HnfSlcSfTest, CurrentOwnerWritebackPreservesLatestData)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto oldData = lineData(0xa0);
    const auto newData = lineData(0xb0);

    model.commitRead(TestAddr, 0, PocqTxnKind::ReadUnique,
                     oldData, false);
    model.writeLine(TestAddr, 0, newData,
                    PocqTxnKind::WriteBackFull);

    const auto result =
        lookup(model, TestAddr, 4, PocqTxnKind::ReadShared);
    ASSERT_TRUE(result.slcHit);
    EXPECT_FALSE(result.sfHit);
    EXPECT_TRUE(result.dataDirty);
    EXPECT_EQ(result.data, newData);
}

TEST(HnfSlcSfTest, StaleWritebackCannotOverwriteNewOwner)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    const auto staleData = lineData(0xc0);
    const auto ownerData = lineData(0xd0);

    model.commitRead(TestAddr, 4, PocqTxnKind::ReadUnique,
                     ownerData, false);
    model.writeLine(TestAddr, 0, staleData,
                    PocqTxnKind::WriteBackFull);

    const auto result =
        lookup(model, TestAddr, 8, PocqTxnKind::ReadShared);
    EXPECT_FALSE(result.slcHit);
    ASSERT_TRUE(result.sfHit);
    EXPECT_EQ(result.rnfid, 4);
    EXPECT_EQ(result.snoopTargets, 1ULL << 4);
}

} // namespace gem5::Chi

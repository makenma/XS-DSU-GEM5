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

struct StageAHomeNodeParams
{
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

const SlcSfReqHeader&
requestHeader(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) -> const SlcSfReqHeader& {
            return typed_request.header;
        },
        request);
}

SlcSfResponse
completeLookup(HnfSLCSF& model, SlcSfRequest request)
{
    EXPECT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    for (size_t cycle = 0; cycle < 16; ++cycle) {
        model.wakeup();
        if (auto response = model.popVisibleResponse()) {
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

    model.finishInitialization();
    model.wakeup();
    EXPECT_EQ(model.registeredReqCredits(), model.reqCapacity());
    auto draining_request = lookupRequest(12);
    model.beginDraining();
    EXPECT_EQ(model.tryEnqueue(std::move(draining_request)),
              SlcSfEnqueueResult::Draining);
    EXPECT_EQ(requestHeader(draining_request).reqId, SlcSfReqId{12});
    EXPECT_EQ(model.reqOutstanding(), 0);
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

    HnfSLCSF overridden(64, 4, 2, 4, 2, 8, config);
    EXPECT_EQ(overridden.pipelineConfig().lookupLatency, 11);
    EXPECT_EQ(overridden.pipelineConfig().maxInflight, 5);
    EXPECT_TRUE(overridden.pipelineConfig().enableSetLock);
}

TEST(HnfSlcSfQueueTest, StageASchedulerCompletesLookupWithoutAnotherRxFlit)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    auto request = lookupRequest(20);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    Tick tick = 100;
    size_t homeNodeWakeups = 0;
    bool nextEdgeScheduled = true;
    while (nextEdgeScheduled && !response) {
        const auto previousCycle = model.currentCycle();
        nextEdgeScheduled = advanceEmbeddedSlcsfStageA(
            model, tick++,
            [&] {
                EXPECT_EQ(model.currentCycle(), previousCycle + 1);
                if (model.respVisibleCount() != 0) {
                    response = model.popVisibleResponse();
                }
            },
            [] { return false; }, [] { return false; });
        ++homeNodeWakeups;
        ASSERT_LT(homeNodeWakeups, 16);
    }

    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->reqId(), SlcSfReqId{20});
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Done);
    EXPECT_FALSE(nextEdgeScheduled);
    EXPECT_FALSE(model.hasWork());
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
    auto first = lookupRequest(31);
    auto second = lookupRequest(32);
    auto third = lookupRequest(33);

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

TEST(HnfSlcSfQueueTest, MixedPipesCompleteOutOfAcceptanceOrder)
{
    HnfSLCSFPipelineConfig config{3, 3, 2, 1, 1, 1, 6};
    config.enableSetLock = true;
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);
    auto first_lookup = lookupRequest(61, TestAddr, 601);
    auto second_lookup = lookupRequest(62, TestAddr, 602);
    const auto fill_token = completeLookupToken(model, 600, TestAddr);
    auto fill = fillRequest(
        63, TestAddr, 603, fill_token, fill_token.lookupReqId);

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
                  fill_pipe ? HnfSLCSF::MutationStage::U1PrepareResources :
                              HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    expect_no_stage_write();
    const auto before_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  fill_pipe ? HnfSLCSF::MutationStage::U2ArrayWrite :
                              HnfSLCSF::MutationStage::U3CheckLatch), 1);
    if (fill_pipe) {
        expect_no_stage_write();
    }
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U3CheckLatch), fill_pipe ? 1 : 0);
    EXPECT_EQ(model.hasSfReservation(submitted_header.pocEntryId), fill_pipe);
    const auto after_write = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    EXPECT_TRUE(
        after_write.snapshot.slc.generation !=
            before_write.snapshot.slc.generation ||
        after_write.snapshot.sf.generation !=
            before_write.snapshot.sf.generation);
    if (fill_pipe) {
        model.wakeup();
    }
    EXPECT_EQ(model.reqInflightCount(), 0);
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_FALSE(model.hasSfReservation(submitted_header.pocEntryId));
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
     ExactDirtyTargetStallsWhenAnotherSlcWayIsInvalid)
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
    model.wakeup();
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_FALSE(model.hasSfReservation(1351));
    EXPECT_TRUE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, dirty_addr}).result.dataDirty);

    // Keep the exact target dirty while changing its generation. A U1
    // implementation that only revalidates after resource preparation would
    // otherwise remain stalled forever on the now-stale dirty snapshot.
    model.writeLine(
        dirty_addr, 3, lineData(0x71), PocqTxnKind::WriteUnique);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
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
     TokenStaledDuringU1ReplaysBeforeWriteAndReleasesResources)
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
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U3CheckLatch), 1);
    EXPECT_TRUE(model.hasSfReservation(1331));
    const auto after_revalidate = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectLookupResultsEqual(after_revalidate.result, intervening.result);
    expectArraySnapshotsEqual(
        after_revalidate.snapshot.slc, intervening.snapshot.slc);
    expectArraySnapshotsEqual(
        after_revalidate.snapshot.sf, intervening.snapshot.sf);

    model.wakeup(510);
    EXPECT_FALSE(model.hasSfReservation(1331));
    model.wakeup(520);
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    const auto& replay = std::get<SlcSfReplay>(response->payload());
    EXPECT_EQ(replay.reason, SlcSfReplayReason::StaleCommitToken);
    EXPECT_EQ(replay.retryNotBeforeTick, 502);
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
                  HnfSLCSF::MutationStage::U3CheckLatch), 1);
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
     DirtySlcVictimStallsInU1WithoutPartialMutation)
{
    HnfSLCSF model(64, 1, 1, 1, 1);
    model.writeLine(
        TestAddr, 0, lineData(0x7b), PocqTxnKind::WriteUnique);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    const uint64_t replacement_addr = TestAddr + 64;
    const auto token = completeLookupToken(
        model, 1220, replacement_addr);
    SlcSfRequest request = makeSlcSfFillCleanSharedReq(
        mutationHeader(122, replacement_addr), lineData(0x7c), {}, token,
        token.lookupReqId);
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    model.wakeup();
    model.wakeup();
    model.wakeup();
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_FALSE(model.hasSfReservation(122));
    const auto stalled = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectArraySnapshotsEqual(stalled.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(stalled.snapshot.sf, before.snapshot.sf);
    EXPECT_TRUE(stalled.result.dataDirty);

    model.flushL3(TestAddr);
    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 8 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Replay);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown,
        replacement_addr}).result.slcHit);
    EXPECT_FALSE(model.hasSfReservation(122));
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
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
     DeferredEvictHandlerReturnsErrorWithoutMutation)
{
    HnfSLCSF model(64, 4, 2, 4, 2);
    model.writeLine(
        TestAddr, 0, lineData(0x7d), PocqTxnKind::WriteUnique);
    const auto before = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    SlcSfRequest request = makeSlcSfFlushL3Req(mutationHeader(123));
    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);

    std::optional<SlcSfResponse> response;
    for (size_t cycle = 0; cycle < 6 && !response; ++cycle) {
        model.wakeup();
        response = model.popVisibleResponse();
    }
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(), SlcSfTerminalStatus::Error);
    EXPECT_EQ(response->operationKind(), SlcSfOperationKind::FlushL3);
    const auto after = model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr});
    expectArraySnapshotsEqual(after.snapshot.slc, before.snapshot.slc);
    expectArraySnapshotsEqual(after.snapshot.sf, before.snapshot.sf);
    EXPECT_EQ(model.reqOutstanding(), 0);
    EXPECT_EQ(model.respOccupied(), 0);
    EXPECT_EQ(model.sfReservationCount(), 0);
    EXPECT_EQ(model.seqReservationCount(), 0);
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
    for (size_t cycle = 0; cycle < 3; ++cycle) {
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
    FillOne,
    FillTwo,
    UpdateOne,
    UpdateTwo,
    Evict,
    EarlyReplay
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
    HnfSLCSF model(64, 4, 2, 4, 2, 8, config);

    SlcSfRequest request;
    uint64_t expected_latency = 0;
    switch (GetParam()) {
      case BaseServiceLatencyCase::Lookup:
        request = lookupRequest(1400);
        expected_latency = config.lookupLatency;
        break;
      case BaseServiceLatencyCase::FillOne:
      case BaseServiceLatencyCase::FillTwo: {
        const auto token = completeLookupToken(model, 1401, TestAddr);
        request = makeSlcSfFillCleanSharedReq(
            mutationHeader(1402), lineData(0x91), {}, token,
            token.lookupReqId);
        expected_latency = config.fillLatency;
        break;
      }
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
      case BaseServiceLatencyCase::Evict: {
        const auto token = completeLookupToken(model, 1405, TestAddr);
        request = makeSlcSfFlushL3Req(
            mutationHeader(1406), token, token.lookupReqId);
        expected_latency = config.updateLatency;
        break;
      }
      case BaseServiceLatencyCase::EarlyReplay: {
        model.commitRead(
            TestAddr, 7, PocqTxnKind::ReadShared, lineData(0x93), false);
        const auto stale_token =
            completeLookupToken(model, 1407, TestAddr);
        model.writeLine(
            TestAddr, 7, lineData(0x94), PocqTxnKind::WriteUnique);
        request = makeSlcSfFillCleanSharedReq(
            mutationHeader(1408), lineData(0x95), {}, stale_token,
            stale_token.lookupReqId);
        expected_latency = 1;
        break;
      }
    }

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    const uint64_t issue_cycle = model.currentCycle();
    ASSERT_EQ(model.reqInflightCount(), 1);
    while (model.respPendingCount() == 0) {
        model.wakeup(1000 + model.currentCycle());
        ASSERT_LE(model.currentCycle(), issue_cycle + expected_latency);
    }
    EXPECT_EQ(model.currentCycle(), issue_cycle + expected_latency);
    EXPECT_FALSE(model.popVisibleResponse().has_value());
    model.wakeup(1000 + model.currentCycle());
    auto response = model.popVisibleResponse();
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->status(),
              GetParam() == BaseServiceLatencyCase::Evict ?
                  SlcSfTerminalStatus::Error :
              GetParam() == BaseServiceLatencyCase::EarlyReplay ?
                  SlcSfTerminalStatus::Replay :
                  SlcSfTerminalStatus::Done);
}

INSTANTIATE_TEST_SUITE_P(
    OperationMapping, HnfSlcSfBaseServiceLatencyTest,
    testing::Values(
        BaseServiceLatencyCase::Lookup,
        BaseServiceLatencyCase::FillOne,
        BaseServiceLatencyCase::FillTwo,
        BaseServiceLatencyCase::UpdateOne,
        BaseServiceLatencyCase::UpdateTwo,
        BaseServiceLatencyCase::Evict,
        BaseServiceLatencyCase::EarlyReplay));

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

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup();
    const uint64_t issue_cycle = model.currentCycle();
    model.wakeup();
    EXPECT_EQ(model.currentCycle(), issue_cycle + 1);
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U1PrepareResources), 1);
    EXPECT_EQ(model.respPendingCount(), 0);
    EXPECT_FALSE(model.probe(HnfSlcLookupReq{
        0, RawReq{}, PocqTxnKind::Unknown, TestAddr}).result.slcHit);

    model.releaseSfResources(999);
    model.wakeup();
    EXPECT_EQ(model.mutationStageCount(
                  HnfSLCSF::MutationStage::U2ArrayWrite), 1);
    EXPECT_EQ(model.respPendingCount(), 0);
    model.wakeup();
    EXPECT_EQ(model.respPendingCount(), 1);
    EXPECT_GT(model.currentCycle(), issue_cycle + config.fillLatency);
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
    auto request = lookupRequest(75, victim_addr, 705);

    ASSERT_EQ(model.tryEnqueue(std::move(request)),
              SlcSfEnqueueResult::Accepted);
    model.wakeup(1000);
    const auto victim = model.frontPendingSeq();
    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, {}, false);
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

    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, {}, false);
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
    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, newData, true);

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
    model.markSeqIssued(victim.id);
    model.completeSfEvict(victim.id, {}, false);
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

#include "mem/cache/CHI/HnfSLCSF.hh"

#include <algorithm>
#include <stdexcept>

#include "base/logging.hh"
#include "sim/cur_tick.hh"

namespace gem5::Chi
{

namespace
{

enum class RequestPipe : uint8_t
{
    Lookup,
    Fill,
    Update
};

RequestPipe
requestPipe(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                return RequestPipe::Lookup;
            } else if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return typed_request.kind() ==
                    SlcSfUpdateKind::WriteL3FlushSf ?
                    RequestPipe::Update : RequestPipe::Fill;
            } else {
                return RequestPipe::Update;
            }
        },
        request);
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

bool
isReadTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique || txn == PocqTxnKind::ReadOnce;
}

bool
isMaintenanceTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::MakeUnique ||
        txn == PocqTxnKind::CleanInvalid ||
        txn == PocqTxnKind::MakeInvalid;
}

bool
isWriteTxn(PocqTxnKind txn)
{
    return txn == PocqTxnKind::WriteBackFull ||
        txn == PocqTxnKind::WriteEvictFull ||
        txn == PocqTxnKind::WriteCleanFull ||
        txn == PocqTxnKind::WriteUnique;
}

bool
mayAllocateSf(PocqTxnKind txn)
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::MakeUnique ||
        txn == PocqTxnKind::WriteCleanFull;
}

HnfSlcLookupReq
makeBackendLookupRequest(const SlcSfLookupReq& request)
{
    HnfSlcLookupReq backend_request{};
    backend_request.entry = request.header.pocEntryId;
    backend_request.blockAddr = request.header.lineAddress;
    backend_request.txn = request.txn;
    backend_request.req.addr = request.header.lineAddress;
    backend_request.req.srcid = request.header.requester;
    backend_request.req.opcode = request.header.opcode;
    backend_request.req.qos = request.header.qos;
    backend_request.req.txnid = request.header.trace.transactionId;
    backend_request.req.traceTag = request.header.trace.traceTag;
    return backend_request;
}

HnfSLCSFBackend::LookupSnapshot
mutationTarget(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            HnfSLCSFBackend::LookupSnapshot target{};
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                panic("HnfSLCSF lookup request has no mutation target\n");
            } else {
                target.lookupEpoch = typed_request.token.lookupEpoch;
                target.slc = HnfSLCSFBackend::ArraySnapshot{
                    typed_request.token.slc.hit,
                    typed_request.token.slc.set,
                    typed_request.token.slc.way,
                    typed_request.token.slc.generation, 0};
                target.sf = HnfSLCSFBackend::ArraySnapshot{
                    typed_request.token.sf.hit,
                    typed_request.token.sf.set,
                    typed_request.token.sf.way,
                    typed_request.token.sf.generation, 0};
            }
            return target;
        },
        request);
}

} // anonymous namespace

HnfSLCSFSetLockManager::HnfSLCSFSetLockManager(
    size_t slc_sets, size_t sf_sets)
    : slcOwners(slc_sets), sfOwners(sf_sets)
{
    if (slc_sets == 0 || sf_sets == 0) {
        throw std::invalid_argument(
            "HnfSLCSF set lock manager requires nonzero set counts");
    }
}

bool
HnfSLCSFSetLockManager::tryAcquire(
    uint64_t owner, const SlcSfSetLockRequest& request)
{
    if (owner == 0) {
        throw std::invalid_argument("HnfSLCSF set lock owner must be nonzero");
    }
    if ((request.slcSet && *request.slcSet >= slcOwners.size()) ||
        (request.sfSet && *request.sfSet >= sfOwners.size())) {
        throw std::out_of_range("HnfSLCSF set lock index is out of range");
    }
    if (holds(owner)) {
        throw std::logic_error("HnfSLCSF set lock owner already holds locks");
    }
    if ((request.slcSet && slcOwners[*request.slcSet]) ||
        (request.sfSet && sfOwners[*request.sfSet])) {
        return false;
    }

    const Holder holder{owner, request.mode};
    if (request.slcSet) {
        slcOwners[*request.slcSet] = holder;
    }
    if (request.sfSet) {
        sfOwners[*request.sfSet] = holder;
    }
    return true;
}

void
HnfSLCSFSetLockManager::release(uint64_t owner)
{
    bool released = false;
    for (auto& holder : slcOwners) {
        if (holder && holder->owner == owner) {
            holder.reset();
            released = true;
        }
    }
    for (auto& holder : sfOwners) {
        if (holder && holder->owner == owner) {
            holder.reset();
            released = true;
        }
    }
    if (!released) {
        throw std::logic_error("HnfSLCSF releases an unowned set lock");
    }
}

bool
HnfSLCSFSetLockManager::holds(uint64_t owner) const
{
    const auto owned = [owner](const std::optional<Holder>& holder) {
        return holder && holder->owner == owner;
    };
    return std::any_of(slcOwners.begin(), slcOwners.end(), owned) ||
        std::any_of(sfOwners.begin(), sfOwners.end(), owned);
}

size_t
HnfSLCSFSetLockManager::heldLockCount() const
{
    const auto occupied = [](const std::optional<Holder>& holder) {
        return holder.has_value();
    };
    return std::count_if(slcOwners.begin(), slcOwners.end(), occupied) +
        std::count_if(sfOwners.begin(), sfOwners.end(), occupied);
}

HnfSLCSF::HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways, uint32_t seq_entries,
                   HnfSLCSFPipelineConfig pipeline_config)
    : HnfSLCSFBackend(block_size, slc_num_sets, slc_num_ways,
                      sf_num_sets, sf_num_ways, seq_entries),
      config(pipeline_config), victimBuffer(config.victimBufferEntries),
      setLocks(slc_num_sets, sf_num_sets),
      completionProducer(std::make_shared<const uint8_t>(0)),
      visibleReqCredits(config.reqQueueEntries)
{
    validateConfig(config);
    assertRequestAccounting();
    assertResponseAccounting();
    assertVictimAccounting();
}

void
HnfSLCSF::validateConfig(const HnfSLCSFPipelineConfig& config)
{
    if (config.reqQueueEntries == 0 || config.respQueueEntries == 0 ||
        config.maxInflight == 0 || config.lookupIssueWidth == 0 ||
        config.fillIssueWidth == 0 || config.updateIssueWidth == 0 ||
        config.lookupLatency == 0 || config.fillLatency == 0 ||
        config.updateLatency == 0 || config.victimLatency == 0 ||
        config.sfEvictLatency == 0 || config.replayPenalty == 0 ||
        config.victimBufferEntries == 0 ||
        config.responseConsumeWidth == 0 || config.childClockPeriod == 0) {
        throw std::invalid_argument(
            "HnfSLCSF queue sizes, VictimBuffer, max inflight, widths, "
            "latencies, and replay penalty must be positive");
    }
    if (config.maxInflight > config.reqQueueEntries) {
        throw std::invalid_argument(
            "HnfSLCSF max inflight exceeds request queue entries");
    }
    if (config.maxInflight > 1 && !config.enableSetLock) {
        throw std::invalid_argument(
            "HnfSLCSF max inflight above one requires set locking");
    }
}

uint64_t
HnfSLCSF::serviceLatency(const SlcSfRequest& request) const
{
    uint64_t latency = std::visit(
        [this](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                return config.lookupLatency;
            } else if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return typed_request.kind() ==
                    SlcSfUpdateKind::WriteL3FlushSf ?
                    config.updateLatency : config.fillLatency;
            } else {
                return config.updateLatency;
            }
        },
        request);
    if (mutationProducesSfVictim(request)) {
        panic_if(config.sfEvictLatency > UINT64_MAX - latency,
                 "HnfSLCSF SF-evict latency overflows\n");
        latency += config.sfEvictLatency;
    }
    if (mutationProducesDirtySlcVictim(request)) {
        panic_if(config.victimLatency > UINT64_MAX - latency,
                 "HnfSLCSF dirty-victim latency overflows\n");
        latency += config.victimLatency;
    }
    return std::max<uint64_t>(1, latency);
}

bool
HnfSLCSF::mutationProducesDirtySlcVictim(
    const SlcSfRequest& request) const
{
    if (!std::holds_alternative<SlcSfFillReq>(request)) {
        return false;
    }
    const auto& fill = std::get<SlcSfFillReq>(request);
    const LookupSnapshot target = mutationTarget(request);
    return std::visit(
        [this, &fill, &target](const auto& operation) {
            using Operation = std::decay_t<decltype(operation)>;
            if constexpr (std::is_same_v<Operation, SlcSfCommitRead>) {
                return operation.txn == PocqTxnKind::ReadShared &&
                    slcAllocationWouldDisplaceDirty(
                        fill.header.lineAddress, &target);
            } else if constexpr (std::is_same_v<
                                     Operation, SlcSfFillCleanShared>) {
                return slcAllocationWouldDisplaceDirty(
                    fill.header.lineAddress, &target);
            } else if constexpr (std::is_same_v<Operation, SlcSfWriteLine>) {
                return writeLineWouldDisplaceDirty(
                    fill.header.lineAddress, fill.header.requester,
                    operation.txn, &target);
            } else if constexpr (std::is_same_v<
                                     Operation, SlcSfWriteL3FlushSf>) {
                return slcAllocationWouldDisplaceDirty(
                    fill.header.lineAddress, &target);
            }
            return false;
        },
        fill.operation);
}

bool
HnfSLCSF::mutationProducesSfVictim(const SlcSfRequest& request) const
{
    const bool allocates_sf = std::visit(
        [](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return std::visit(
                    [](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead>) {
                            return operation.txn == PocqTxnKind::ReadShared ||
                                operation.txn == PocqTxnKind::ReadUnique;
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfFillCleanShared>) {
                            return true;
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteLine>) {
                            return operation.txn ==
                                PocqTxnKind::WriteCleanFull;
                        } else {
                            return false;
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                return std::visit(
                    [](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            return operation.txn == PocqTxnKind::MakeUnique;
                        }
                        return false;
                    },
                    typed_request.operation);
            }
            return false;
        },
        request);
    return allocates_sf &&
        sfAllocationWouldDisplace(
            requestHeader(request).lineAddress, mutationTarget(request));
}

Tick
HnfSLCSF::replayDeadline() const
{
    panic_if(config.replayPenalty > MaxTick / config.childClockPeriod,
             "HnfSLCSF replay penalty conversion overflows");
    const Tick penalty = config.replayPenalty * config.childClockPeriod;
    panic_if(penalty > MaxTick - wakeupTick,
             "HnfSLCSF replay deadline overflows");
    return wakeupTick + penalty;
}

bool
HnfSLCSF::lookupReplaysAtL0(const SlcSfRequest& request) const
{
    const auto* lookup_request = std::get_if<SlcSfLookupReq>(&request);
    return lookup_request &&
        probe(makeBackendLookupRequest(*lookup_request)).result.replay;
}

SlcSfEnqueueResult
HnfSLCSF::tryEnqueue(SlcSfRequest&& request)
{
    assertRequestAccounting();
    if (draining) {
        ++drainingRejects;
        return SlcSfEnqueueResult::Draining;
    }
    if (!initialized) {
        ++initializingRejects;
        return SlcSfEnqueueResult::Initializing;
    }
    if (visibleReqCredits == 0 ||
        reqOutstanding() == config.reqQueueEntries) {
        ++noCreditRejects;
        return SlcSfEnqueueResult::NoCredit;
    }

    reqIngress.push_back(std::move(request));
    --visibleReqCredits;
    assertRequestAccounting();
    if (workAvailableCallback) {
        workAvailableCallback();
    }
    return SlcSfEnqueueResult::Accepted;
}

void
HnfSLCSF::wakeup()
{
    // Standalone unit tests have no current event queue. Keep their legacy
    // no-argument driver advancing one tick at a time, while simulation users
    // observe the actual global tick.
    if (Gem5Internal::_curTickPtr) {
        wakeup(curTick());
    } else {
        panic_if(wakeupTick == MaxTick,
                 "HnfSLCSF cannot wake after MaxTick");
        wakeup(wakeupTick + 1);
    }
}

void
HnfSLCSF::wakeup(Tick now)
{
    ++wakeupCycle;
    wakeupTick = now;
    if (!initialized) {
        panic_if(initializationCyclesRemaining == 0,
                 "HnfSLCSF initialization has no completion deadline\n");
        --initializationCyclesRemaining;
        if (initializationCyclesRemaining == 0) {
            finishInitialization();
        }
        updateRegisteredCredits();
        assertRequestAccounting();
        assertResponseAccounting();
        assertVictimAccounting();
        return;
    }
    promotePendingResponses();
    completeInflightRequests();
    promoteIngressRequests();
    issueReadyRequests();
    updateRegisteredCredits();
    assertRequestAccounting();
    assertResponseAccounting();
    assertVictimAccounting();
}

size_t
HnfSLCSF::reqOutstanding() const
{
    return reqIngress.size() + reqReady.size() + inflightRequests.size();
}

size_t
HnfSLCSF::respOccupied() const
{
    return inflightRequests.size() + respPending.size() + respVisible.size();
}

const SlcSfResponse*
HnfSLCSF::frontVisibleResponse() const
{
    return respVisible.empty() ? nullptr : &respVisible.front();
}

SlcSfCompletionAckResult
HnfSLCSF::acknowledgeVisibleCompletion(const SlcSfResponse& response)
{
    if (respVisible.empty()) {
        return SlcSfCompletionAckResult::NotVisible;
    }
    const SlcSfResponse& visible = respVisible.front();
    if (visible.reqId() != response.reqId() ||
        visible.pocEntryId() != response.pocEntryId() ||
        visible.operationKind() != response.operationKind() ||
        visible.status() != SlcSfTerminalStatus::Done ||
        response.status() != SlcSfTerminalStatus::Done) {
        return SlcSfCompletionAckResult::IdentityMismatch;
    }
    const auto* visible_update =
        std::get_if<SlcSfUpdateResponse>(&visible.payload());
    const auto* supplied_update =
        std::get_if<SlcSfUpdateResponse>(&response.payload());
    if (!visible_update || !supplied_update ||
        visible_update->updateKind != supplied_update->updateKind ||
        visible_update->sfVictim || supplied_update->sfVictim ||
        !visible_update->completionLease ||
        !supplied_update->completionLease ||
        *visible_update->completionLease !=
            *supplied_update->completionLease) {
        return SlcSfCompletionAckResult::IdentityMismatch;
    }

    const SlcSfCompletionLease& lease =
        *visible_update->completionLease;
    bool acknowledged = false;
    if (lease.kind() == SlcSfCompletionKind::CompleteSfEvict &&
        visible_update->updateKind == SlcSfUpdateKind::CompleteSfEvict) {
        acknowledged = acknowledgeSfEvict(lease);
    } else if (
        lease.kind() == SlcSfCompletionKind::ReleaseDirtyVictim &&
        visible_update->updateKind ==
            SlcSfUpdateKind::ReleaseDirtyVictim) {
        acknowledged = acknowledgeDirtyVictimRelease(lease);
    } else {
        return SlcSfCompletionAckResult::IdentityMismatch;
    }
    if (!acknowledged) {
        return SlcSfCompletionAckResult::Stale;
    }
    respVisible.pop_front();
    assertResponseAccounting();
    return SlcSfCompletionAckResult::Acknowledged;
}

std::optional<SlcSfResponse>
HnfSLCSF::popVisibleResponse()
{
    assertResponseAccounting();
    if (respVisible.empty()) {
        return std::nullopt;
    }
    const auto* update =
        std::get_if<SlcSfUpdateResponse>(&respVisible.front().payload());
    panic_if(
        respVisible.front().status() == SlcSfTerminalStatus::Done && update &&
            update->completionLease,
        "HnfSLCSF durable Done requires acknowledgeVisibleCompletion\n");

    SlcSfResponse response = std::move(respVisible.front());
    respVisible.pop_front();
    assertResponseAccounting();
    return response;
}

SlcSfCancelResult
HnfSLCSF::cancelRequest(
    uint32_t poc_entry_id, SlcSfReqId req_id, Tick now)
{
    const auto matches = [poc_entry_id, req_id](const auto& request) {
        const auto& header = requestHeader(request);
        return header.pocEntryId == poc_entry_id && header.reqId == req_id;
    };
    if (std::any_of(reqIngress.begin(), reqIngress.end(), matches) ||
        std::any_of(reqReady.begin(), reqReady.end(), matches)) {
        // Queued requests do not yet own a response slot. Keep them intact so
        // cancellation cannot violate Accepted -> exactly one terminal.
        return SlcSfCancelResult::NotCancellable;
    }

    auto inflight = std::find_if(
        inflightRequests.begin(), inflightRequests.end(),
        [&matches](const InflightRequest& request) {
            return matches(request.request);
        });
    if (inflight != inflightRequests.end()) {
        const bool ownerless = std::visit(
            [](const auto& typed_request) {
                using Request = std::decay_t<decltype(typed_request)>;
                if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                    return std::holds_alternative<SlcSfCompleteSfEvict>(
                               typed_request.operation) ||
                        std::holds_alternative<SlcSfReleaseDirtyVictim>(
                            typed_request.operation);
                }
                return false;
            },
            inflight->request);
        if (ownerless) {
            return SlcSfCancelResult::NotCancellable;
        }
        if (inflight->mutationCommitted || inflight->terminalResponse ||
            inflight->terminalReplayReason) {
            return SlcSfCancelResult::TooLate;
        }
        SlcSfResponse response = std::visit(
            [this, now](const auto& typed_request) {
                panic_if(config.replayPenalty >
                             (MaxTick - now) / config.childClockPeriod,
                         "HnfSLCSF cancellation replay deadline overflows");
                return makeSlcSfReplayResponse(
                    typed_request,
                    SlcSfReplay{
                        SlcSfReplayReason::Cancelled,
                        now + config.replayPenalty *
                                  config.childClockPeriod,
                        true});
            },
            inflight->request);
        finishInflight(
            *inflight, FinishReason::Cancelled, std::move(response));
        inflightRequests.erase(inflight);
        assertRequestAccounting();
        assertResponseAccounting();
        return SlcSfCancelResult::Cancelled;
    }

    const auto response_matches =
        [poc_entry_id, req_id](const SlcSfResponse& response) {
        return response.pocEntryId() == poc_entry_id &&
            response.reqId() == req_id;
    };
    if (std::any_of(
            respPending.begin(), respPending.end(), response_matches) ||
        std::any_of(
            respVisible.begin(), respVisible.end(), response_matches)) {
        return SlcSfCancelResult::TooLate;
    }
    return SlcSfCancelResult::NotFound;
}

bool
HnfSLCSF::hasWork() const
{
    return !initialized || reqOutstanding() != 0 || respOccupied() != 0;
}

bool
HnfSLCSF::needsServiceWakeup() const
{
    return !initialized || reqOutstanding() != 0 || !respPending.empty();
}

void
HnfSLCSF::resetForColdStart()
{
    panic_if(reqOutstanding() != 0 || respOccupied() != 0 ||
                 setLockCount() != 0,
             "HnfSLCSF cold reset requires empty timing queues and locks\n");
    resetStorageForColdStart();
    for (VictimEntry& entry : victimBuffer) {
        entry = VictimEntry{};
    }
    nextVictimId = 1;
    nextCompletionNonce = 1;
    nextSetLockOwner = 1;
    draining = false;
    beginInitialization();
}

void
HnfSLCSF::beginInitialization()
{
    initialized = false;
    initializationCyclesRemaining = std::max<size_t>(config.initLatency, 1);
    visibleReqCredits = 0;
}

void
HnfSLCSF::finishInitialization()
{
    initialized = true;
    initializationCyclesRemaining = 0;
}

void
HnfSLCSF::beginDraining()
{
    draining = true;
    visibleReqCredits = 0;
}

void
HnfSLCSF::resumeFromDrain()
{
    draining = false;
}

void
HnfSLCSF::promotePendingResponses()
{
    while (!respPending.empty()) {
        respVisible.push_back(std::move(respPending.front()));
        respPending.pop_front();
    }
}

void
HnfSLCSF::completeInflightRequests()
{
    for (auto request = inflightRequests.begin();
         request != inflightRequests.end();) {
        if (!request->mutationStage &&
            request->completeCycle > wakeupCycle) {
            ++request;
            continue;
        }

        if (!request->mutationStage) {
            SlcSfResponse response = request->earlyLookupReplay ?
                makeSlcSfReplayResponse(
                    std::get<SlcSfLookupReq>(request->request),
                    SlcSfReplay{
                        SlcSfReplayReason::SeqConflict,
                        replayDeadline(), true}) :
                makeTerminalResponse(request->request);
            const FinishReason reason =
                response.status() == SlcSfTerminalStatus::Done ?
                    FinishReason::Done : FinishReason::Replay;
            finishInflight(*request, reason, std::move(response));
            request = inflightRequests.erase(request);
            continue;
        }

        // Preserve a registered stage boundary before the service deadline.
        // Once the deadline is reached, a bounded catch-up may traverse the
        // remaining stages on this wakeup so short configured latencies remain
        // observable without skipping validation, reservation, write, or
        // terminal cleanup. A resource stall leaves the stage unchanged, and
        // stale U1/U2 validation extends completeCycle; either condition stops
        // catch-up until a later wakeup.
        const bool resumed_from_stall = request->mutationStalled;
        const bool before_deadline = request->completeCycle > wakeupCycle;
        const bool may_advance_before_deadline =
            *request->mutationStage == MutationStage::U0DecodeValidate ||
            *request->mutationStage == MutationStage::U1PrepareResources;
        const size_t advances = resumed_from_stall ? 1 :
            before_deadline ? (may_advance_before_deadline ? 1 : 0) : 4;
        for (size_t i = 0; i < advances && request->mutationStage; ++i) {
            const MutationStage previous = *request->mutationStage;
            advanceMutation(*request);
            if (request->mutationStage &&
                *request->mutationStage == previous) {
                request->mutationStalled =
                    previous == MutationStage::U1PrepareResources;
                break;
            }
            request->mutationStalled = false;
            if (request->completeCycle > wakeupCycle) {
                break;
            }
        }
        if (request->mutationStage) {
            ++request;
        } else {
            request = inflightRequests.erase(request);
        }
    }
}

size_t
HnfSLCSF::mutationStageCount(MutationStage stage) const
{
    return std::count_if(
        inflightRequests.begin(), inflightRequests.end(),
        [stage](const InflightRequest& request) {
            return request.mutationStage == stage;
        });
}

size_t
HnfSLCSF::victimBufferOccupancy() const
{
    return std::count_if(
        victimBuffer.begin(), victimBuffer.end(), [](const VictimEntry& entry) {
            return entry.state != VictimState::Free &&
                entry.state != VictimState::Released;
        });
}

size_t
HnfSLCSF::victimReservationCount() const
{
    return std::count_if(
        victimBuffer.begin(), victimBuffer.end(), [](const VictimEntry& entry) {
            return entry.state == VictimState::Reserved ||
                entry.state == VictimState::InstalledSnapshot;
        });
}

HnfSLCSF::VictimEntry*
HnfSLCSF::findDirtyVictim(SlcSfVictimId id)
{
    auto entry = std::find_if(
        victimBuffer.begin(), victimBuffer.end(), [id](const VictimEntry& e) {
            return e.id.value == id.value;
        });
    return entry == victimBuffer.end() ? nullptr : &*entry;
}

const HnfSLCSF::VictimEntry*
HnfSLCSF::findDirtyVictim(SlcSfVictimId id) const
{
    auto entry = std::find_if(
        victimBuffer.begin(), victimBuffer.end(), [id](const VictimEntry& e) {
            return e.id.value == id.value;
        });
    return entry == victimBuffer.end() ? nullptr : &*entry;
}

std::optional<HnfSLCSF::VictimState>
HnfSLCSF::dirtyVictimState(SlcSfVictimId id) const
{
    const VictimEntry* entry = findDirtyVictim(id);
    return entry ? std::optional<VictimState>(entry->state) : std::nullopt;
}

void
HnfSLCSF::markDirtyVictimWritebackIssued(
    SlcSfVictimId id, uint32_t requester, uint8_t opcode,
    uint32_t downstreamTxnId)
{
    VictimEntry* entry = findDirtyVictim(id);
    panic_if(!entry || entry->state != VictimState::HandedOff,
             "HnfSLCSF writeback for unknown or non-handed-off victim=%llu\n",
             static_cast<unsigned long long>(id.value));
    panic_if(downstreamTxnId == 0,
             "HnfSLCSF writeback victim=%llu lacks txn identity\n",
             static_cast<unsigned long long>(id.value));
    entry->state = VictimState::WritebackIssued;
    entry->writebackRequester = requester;
    entry->writebackOpcode = opcode;
    entry->writebackTxnId = downstreamTxnId;
    assertVictimAccounting();
}

bool
HnfSLCSF::victimLeaseMatches(
    const VictimEntry& entry, const SlcSfCompletionLease& lease) const
{
    const bool full_dirty_snapshot = entry.snapshot &&
        entry.snapshot->victimId.value == entry.id.value &&
        entry.snapshot->lineAddress == entry.lineAddress &&
        (entry.snapshot->state == HnfSlcState::MU ||
         entry.snapshot->state == HnfSlcState::MN) &&
        entry.snapshot->line.dirty &&
        entry.snapshot->line.data.size() == blockSizeBytes() &&
        entry.snapshot->line.byteMask.size() == blockSizeBytes() &&
        std::all_of(
            entry.snapshot->line.byteMask.begin(),
            entry.snapshot->line.byteMask.end(),
            [](uint8_t byte) { return byte == 0xff; });
    return lease.valid() &&
        lease.kind() == SlcSfCompletionKind::ReleaseDirtyVictim &&
        full_dirty_snapshot &&
        entry.completionLease && *entry.completionLease == lease &&
        lease.objectId() == entry.id.value &&
        lease.pocEntryId() == UINT32_MAX &&
        lease.lineAddress() == entry.lineAddress &&
        lease.requester() == entry.writebackRequester &&
        lease.opcode() == entry.writebackOpcode &&
        lease.linkSequence() == entry.id.value &&
        lease.transactionId() == entry.writebackTxnId;
}

bool
HnfSLCSF::commitDirtyVictimRelease(
    SlcSfVictimId id, const SlcSfCompletionLease& lease)
{
    VictimEntry* entry = findDirtyVictim(id);
    if (!entry || entry->state != VictimState::ReleaseClaimed ||
        !victimLeaseMatches(*entry, lease)) {
        return false;
    }
    entry->state = VictimState::ReleaseCommittedAwaitAck;
    assertVictimAccounting();
    return true;
}

bool
HnfSLCSF::acknowledgeDirtyVictimRelease(
    const SlcSfCompletionLease& lease)
{
    if (!lease.valid() ||
        lease.kind() != SlcSfCompletionKind::ReleaseDirtyVictim) {
        return false;
    }
    VictimEntry* entry = findDirtyVictim(
        SlcSfVictimId{lease.objectId()});
    if (!entry ||
        entry->state != VictimState::ReleaseCommittedAwaitAck ||
        !victimLeaseMatches(*entry, lease)) {
        return false;
    }
    entry->state = VictimState::Released;
    entry->snapshot.reset();
    entry->lineAddress = 0;
    entry->writebackRequester = 0;
    entry->writebackOpcode = 0;
    entry->writebackTxnId = 0;
    entry->completionLease.reset();
    assertVictimAccounting();
    return true;
}

void
HnfSLCSF::assertVictimAccounting() const
{
    panic_if(victimBufferOccupancy() > victimBuffer.size(),
             "HnfSLCSF VictimBuffer exceeds capacity occupancy=%u/%u\n",
             static_cast<unsigned>(victimBufferOccupancy()),
             static_cast<unsigned>(victimBuffer.size()));
}

SlcSfResponse
HnfSLCSF::makeTerminalResponse(
    const SlcSfRequest& request,
    std::optional<SlcSfSlcVictim> slc_victim,
    std::optional<SlcSfSfVictim> sf_victim,
    std::optional<SlcSfCompletionLease> completion_lease)
{
    return std::visit(
        [this, &slc_victim, &sf_victim,
         &completion_lease](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                HnfSLCSFBackend::LookupSnapshot snapshot{};
                HnfSlcLookupResult result = HnfSLCSFBackend::lookup(
                    makeBackendLookupRequest(typed_request), &snapshot);
                if (result.replay) {
                    return makeSlcSfReplayResponse(
                        typed_request,
                        SlcSfReplay{
                            SlcSfReplayReason::SeqConflict,
                            replayDeadline(), true});
                }

                SlcSfCommitToken token{};
                token.lookupReqId = typed_request.header.reqId;
                token.lineAddress = typed_request.header.lineAddress;
                token.lookupEpoch = snapshot.lookupEpoch;
                token.slc = SlcSfArraySnapshot{
                    snapshot.slc.hit, snapshot.slc.set, snapshot.slc.way,
                    snapshot.slc.generation};
                token.sf = SlcSfArraySnapshot{
                    snapshot.sf.hit, snapshot.sf.set, snapshot.sf.way,
                    snapshot.sf.generation};
                return makeSlcSfDoneResponse(
                    typed_request, std::move(result), token);
            } else if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return makeSlcSfDoneResponse(
                    typed_request, std::move(slc_victim),
                    std::move(sf_victim));
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                return makeSlcSfDoneResponse(
                    typed_request, std::move(sf_victim),
                    std::move(completion_lease));
            } else {
                return makeSlcSfDoneResponse(
                    typed_request, std::move(sf_victim));
            }
        },
        request);
}

std::optional<SlcSfError>
HnfSLCSF::validateMutationRequest(const SlcSfRequest& request) const
{
    const SlcSfReqHeader& header = requestHeader(request);
    if (!header.reqId.valid()) {
        return SlcSfError{
            SlcSfErrorCode::InvalidRequest, "invalid request ID"};
    }
    if (header.lineAddress % blockSizeBytes() != 0 ||
        header.requester >= 64) {
        return SlcSfError{
            SlcSfErrorCode::InvalidRequest,
            "unaligned line address or requester outside RN-F vector"};
    }

    return std::visit(
        [this](const auto& typed_request) -> std::optional<SlcSfError> {
            using Request = std::decay_t<decltype(typed_request)>;
            bool supported = false;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                supported = std::visit(
                    [](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead>) {
                            return isReadTxn(operation.txn);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfFillCleanShared>) {
                            return true;
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteLine>) {
                            return isWriteTxn(operation.txn);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteL3FlushSf>) {
                            return true;
                        } else {
                            return false;
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                supported = std::visit(
                    [this, &typed_request](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            return isMaintenanceTxn(operation.txn);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfCompleteSfEvict>) {
                                return seqCompletionClaimable(
                                       operation.seqId.value,
                                       typed_request.header) &&
                                (!operation.snoopData.dirty ||
                                 operation.snoopData.data.size() ==
                                     blockSizeBytes());
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfReleaseDirtyVictim>) {
                            const VictimEntry* victim = findDirtyVictim(
                                operation.victimId);
                            return victim && victim->state ==
                                    VictimState::WritebackIssued &&
                                typed_request.header.pocEntryId ==
                                    UINT32_MAX &&
                                typed_request.header.lineAddress ==
                                    victim->lineAddress &&
                                typed_request.header.requester ==
                                    victim->writebackRequester &&
                                typed_request.header.opcode ==
                                    victim->writebackOpcode &&
                                typed_request.header.trace.linkSequence ==
                                    operation.victimId.value &&
                                typed_request.header.trace.transactionId ==
                                    victim->writebackTxnId;
                        } else {
                            return std::is_same_v<Operation,
                                                  SlcSfRemoveSharer>;
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfEvictReq>) {
                supported = true;
            }
            if (supported) {
                return std::nullopt;
            }
            return SlcSfError{
                SlcSfErrorCode::InvalidRequest,
                "operation is not supported by the staged mutation service"};
        },
        request);
}

bool
HnfSLCSF::validateMutationToken(const SlcSfRequest& request) const
{
    return std::visit(
        [this](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                return false;
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                const bool ownerless = std::holds_alternative<
                    SlcSfCompleteSfEvict>(typed_request.operation) ||
                    std::holds_alternative<SlcSfReleaseDirtyVictim>(
                        typed_request.operation);
                return ownerless || validateCommitToken(
                    typed_request.token,
                    typed_request.sourceLookupReqId,
                    typed_request.header.lineAddress);
            } else {
                return validateCommitToken(
                    typed_request.token,
                    typed_request.sourceLookupReqId,
                    typed_request.header.lineAddress);
            }
        },
        request);
}

bool
HnfSLCSF::claimDurableCompletion(InflightRequest& request)
{
    const auto* update = std::get_if<SlcSfUpdateReq>(&request.request);
    if (!update) {
        return true;
    }
    const bool durable =
        std::holds_alternative<SlcSfCompleteSfEvict>(update->operation) ||
        std::holds_alternative<SlcSfReleaseDirtyVictim>(update->operation);
    if (!durable) {
        return true;
    }
    if (request.completionLease) {
        return durableClaimMatches(request);
    }
    panic_if(nextCompletionNonce == 0,
             "HnfSLCSF completion lease nonce space exhausted\n");

    if (const auto* complete =
            std::get_if<SlcSfCompleteSfEvict>(&update->operation)) {
        SlcSfCompletionLease lease{
            completionProducer,
            SlcSfCompletionKind::CompleteSfEvict,
            nextCompletionNonce++, update->header, complete->seqId.value};
        if (!claimSeqCompletion(complete->seqId.value, lease)) {
            return false;
        }
        request.completionLease = lease;
        return true;
    }

    const auto& release =
        std::get<SlcSfReleaseDirtyVictim>(update->operation);
    VictimEntry* victim = findDirtyVictim(release.victimId);
    if (!victim || victim->state != VictimState::WritebackIssued) {
        return false;
    }
    SlcSfCompletionLease lease{
        completionProducer,
        SlcSfCompletionKind::ReleaseDirtyVictim,
        nextCompletionNonce++, update->header, release.victimId.value};
    victim->completionLease = lease;
    if (!victimLeaseMatches(*victim, lease)) {
        victim->completionLease.reset();
        return false;
    }
    victim->state = VictimState::ReleaseClaimed;
    request.completionLease = lease;
    return true;
}

bool
HnfSLCSF::durableClaimMatches(const InflightRequest& request) const
{
    if (!request.completionLease) {
        const auto* update = std::get_if<SlcSfUpdateReq>(&request.request);
        return !update ||
            (!std::holds_alternative<SlcSfCompleteSfEvict>(
                 update->operation) &&
             !std::holds_alternative<SlcSfReleaseDirtyVictim>(
                 update->operation));
    }
    const SlcSfCompletionLease& lease = *request.completionLease;
    if (lease.kind() == SlcSfCompletionKind::CompleteSfEvict) {
        return seqClaimMatches(lease);
    }
    const VictimEntry* victim = findDirtyVictim(
        SlcSfVictimId{lease.objectId()});
    return victim && victim->state == VictimState::ReleaseClaimed &&
        victimLeaseMatches(*victim, lease);
}

void
HnfSLCSF::rollbackDurableClaim(InflightRequest& request)
{
    if (!request.completionLease || request.mutationCommitted) {
        return;
    }
    const SlcSfCompletionLease lease = *request.completionLease;
    if (lease.kind() == SlcSfCompletionKind::CompleteSfEvict) {
        releaseSeqClaim(lease);
    } else {
        VictimEntry* victim = findDirtyVictim(
            SlcSfVictimId{lease.objectId()});
        if (victim && victim->state == VictimState::ReleaseClaimed &&
            victimLeaseMatches(*victim, lease)) {
            victim->state = VictimState::WritebackIssued;
            victim->completionLease.reset();
        }
    }
    request.completionLease.reset();
}

bool
HnfSLCSF::reserveDirtyVictim(
    InflightRequest& request, const LookupSnapshot& target)
{
    const uint64_t replacement_addr = requestHeader(request.request).lineAddress;
    const auto victim_addr = dirtySlcVictimAddress(replacement_addr, target);
    panic_if(!victim_addr,
             "HnfSLCSF reserves VictimBuffer without dirty displacement\n");

    if (auto failure = dirtyVictimReservationFailure(
            replacement_addr, target)) {
        request.terminalReplayReason = *failure;
        return true;
    }

    auto entry = std::find_if(
        victimBuffer.begin(), victimBuffer.end(), [](const VictimEntry& e) {
            return e.state == VictimState::Free ||
                e.state == VictimState::Released;
        });
    panic_if(entry == victimBuffer.end(),
             "HnfSLCSF VictimBuffer preflight changed during acquisition\n");
    panic_if(nextVictimId == 0,
             "HnfSLCSF dirty-victim ID space exhausted\n");
    entry->id = SlcSfVictimId{nextVictimId++};
    entry->state = VictimState::Reserved;
    entry->lineAddress = *victim_addr;
    entry->snapshot.reset();
    entry->writebackRequester = 0;
    entry->writebackOpcode = 0;
    entry->writebackTxnId = 0;
    entry->completionLease.reset();
    request.slcVictimId = entry->id;

    DirtyVictimCapture capture = snapshotDirtySlcVictim(
        entry->id, replacement_addr, target);
    entry->snapshot = capture.victim;
    entry->state = VictimState::InstalledSnapshot;
    request.slcVictim = std::move(capture.victim);
    request.slcVictimSeal = std::move(capture.seal);
    assertVictimAccounting();
    return true;
}

std::optional<SlcSfReplayReason>
HnfSLCSF::dirtyVictimReservationFailure(
    uint64_t replacement_addr, const LookupSnapshot& target) const
{
    const auto victim_addr = dirtySlcVictimAddress(replacement_addr, target);
    panic_if(!victim_addr,
             "HnfSLCSF checks VictimBuffer without dirty displacement\n");
    const bool same_line = std::any_of(
        victimBuffer.begin(), victimBuffer.end(),
        [victim_addr](const VictimEntry& entry) {
            return entry.state != VictimState::Free &&
                entry.state != VictimState::Released &&
                entry.lineAddress == *victim_addr;
        });
    if (same_line) {
        return SlcSfReplayReason::ResourceConflict;
    }

    const auto entry = std::find_if(
        victimBuffer.begin(), victimBuffer.end(), [](const VictimEntry& e) {
            return e.state == VictimState::Free ||
                e.state == VictimState::Released;
        });
    if (entry == victimBuffer.end()) {
        return SlcSfReplayReason::VictimBufferFull;
    }
    return std::nullopt;
}

void
HnfSLCSF::cancelDirtyVictimReservation(InflightRequest& request)
{
    if (!request.slcVictimId) {
        return;
    }
    VictimEntry* entry = findDirtyVictim(*request.slcVictimId);
    panic_if(!entry || (entry->state != VictimState::Reserved &&
                        entry->state != VictimState::InstalledSnapshot),
             "HnfSLCSF cancels invalid dirty-victim reservation=%llu\n",
             static_cast<unsigned long long>(request.slcVictimId->value));
    panic_if(!request.slcVictimSeal,
             "HnfSLCSF dirty-victim cancellation lacks installed seal\n");
    discardDirtyVictimSeal(*request.slcVictimSeal);
    entry->state = VictimState::Released;
    entry->snapshot.reset();
    entry->lineAddress = 0;
    entry->writebackRequester = 0;
    entry->writebackOpcode = 0;
    entry->writebackTxnId = 0;
    entry->completionLease.reset();
    request.slcVictim.reset();
    request.slcVictimSeal.reset();
    request.slcVictimId.reset();
    assertVictimAccounting();
}

void
HnfSLCSF::rollbackPreparedResources(InflightRequest& request)
{
    if (request.slcVictimId) {
        cancelDirtyVictimReservation(request);
    }
    const uint32_t owner = requestHeader(request.request).pocEntryId;
    if (request.resourcesPrepared || hasSfReservation(owner)) {
        releaseSfResources(owner);
        request.resourcesPrepared = false;
    }
}

bool
HnfSLCSF::prepareMutationResources(InflightRequest& request)
{
    const LookupSnapshot target = mutationTarget(request.request);
    const auto prepare =
        [this, &request, &target](PocqTxnKind txn, bool may_allocate_slc,
                                  bool write_line) {
        const auto& header = requestHeader(request.request);
        request.resourcesPrepared = request.resourcesPrepared ||
            hasSfReservation(header.pocEntryId);
        bool displaces_dirty = false;
        if (may_allocate_slc) {
            displaces_dirty = write_line ?
                writeLineWouldDisplaceDirty(
                    header.lineAddress, header.requester, txn, &target) :
                slcAllocationWouldDisplaceDirty(
                    header.lineAddress, &target);
            if (displaces_dirty) {
                if (auto failure = dirtyVictimReservationFailure(
                        header.lineAddress, target)) {
                    request.terminalReplayReason = *failure;
                    rollbackPreparedResources(request);
                    return true;
                }
            }
        }
        if (mayAllocateSf(txn) && sfAllocationBlockedBySeq(
                header.lineAddress, target, header.pocEntryId)) {
            request.terminalReplayReason = SlcSfReplayReason::SeqConflict;
            rollbackPreparedResources(request);
            return true;
        }
        if (!tryReserveSfResources(
                header.pocEntryId, header.lineAddress, txn, &target)) {
            rollbackPreparedResources(request);
            return false;
        }
        request.resourcesPrepared = hasSfReservation(header.pocEntryId);
        // VictimBuffer availability was preflighted before the set/SEQ
        // acquisition. Keep a defensive rollback in case that invariant is
        // ever broken by a future resource implementation.
        if (displaces_dirty && !request.slcVictimId &&
            !reserveDirtyVictim(request, target)) {
            rollbackPreparedResources(request);
            return false;
        }
        if (request.terminalReplayReason) {
            rollbackPreparedResources(request);
            return true;
        }
        return true;
    };

    return std::visit(
        [&prepare](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return std::visit(
                    [&prepare](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead> ||
                                      std::is_same_v<
                                          Operation, SlcSfWriteLine>) {
                            const bool may_allocate_slc =
                                std::is_same_v<Operation, SlcSfWriteLine> ||
                                operation.txn == PocqTxnKind::ReadShared;
                            return prepare(
                                operation.txn, may_allocate_slc,
                                std::is_same_v<Operation, SlcSfWriteLine>);
                        } else {
                            if constexpr (std::is_same_v<
                                              Operation,
                                              SlcSfFillCleanShared>) {
                                return prepare(
                                    PocqTxnKind::ReadShared, true, false);
                            } else {
                                return prepare(
                                    PocqTxnKind::WriteUnique, true, true);
                            }
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                return std::visit(
                    [&prepare](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            return prepare(operation.txn, false, false);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfRemoveSharer>) {
                            return prepare(
                                PocqTxnKind::Evict, false, false);
                        } else {
                            return true;
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfEvictReq>) {
                return true;
            } else {
                return false;
            }
        },
        request.request);
}

void
HnfSLCSF::executeMutation(InflightRequest& request)
{
    const SlcSfReqHeader& header = requestHeader(request.request);
    const LookupSnapshot target = mutationTarget(request.request);
    const SlcSfSlcVictim* preserved_victim = request.slcVictim ?
        &*request.slcVictim : nullptr;
    const DirtyVictimSeal* installed_seal = request.slcVictimSeal ?
        &*request.slcVictimSeal : nullptr;
    panic_if(static_cast<bool>(preserved_victim) !=
                 static_cast<bool>(installed_seal),
             "HnfSLCSF mutation has unpaired dirty-victim snapshot/seal\n");
    const SlcSfCompletionLease* completion_lease =
        request.completionLease ? &*request.completionLease : nullptr;
    SeqVictim sf_victim{};
    std::visit(
        [this, &header, &target, &sf_victim, preserved_victim,
         installed_seal,
         completion_lease](
            const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                std::visit(
                    [this, &header, &target, &sf_victim,
                     preserved_victim, installed_seal](
                        const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead>) {
                            commitRead(
                                header.lineAddress, header.requester,
                                operation.txn, operation.line.data,
                                operation.line.dirty,
                                operation.homeNodeId, &target,
                                header.pocEntryId, &sf_victim,
                                preserved_victim, installed_seal);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfFillCleanShared>) {
                            fillCleanShared(
                                header.lineAddress, header.requester,
                                operation.line.data, &target,
                                header.pocEntryId, &sf_victim,
                                preserved_victim, installed_seal);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteLine>) {
                            writeLine(
                                header.lineAddress, header.requester,
                                operation.line.data, operation.txn,
                                operation.homeNodeId, &target,
                                header.pocEntryId, &sf_victim,
                                preserved_victim, installed_seal);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteL3FlushSf>) {
                            writeL3FlushSf(
                                header.lineAddress, header.requester,
                                operation.line.data, &target,
                                preserved_victim, installed_seal);
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                std::visit(
                    [this, &header, &target, &sf_victim,
                     completion_lease](
                        const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            completeMaintenance(
                                header.lineAddress, header.requester,
                                operation.txn, operation.homeNodeId,
                                &target, header.pocEntryId, &sf_victim);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfRemoveSharer>) {
                            removeSharer(
                                header.lineAddress, header.requester);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfCompleteSfEvict>) {
                            panic_if(!completion_lease,
                                     "HnfSLCSF SEQ commit lacks lease\n");
                            commitClaimedSfEvict(
                                operation.seqId.value,
                                operation.snoopData.data,
                                operation.snoopData.dirty,
                                *completion_lease);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfReleaseDirtyVictim>) {
                            panic_if(
                                !completion_lease ||
                                    !commitDirtyVictimRelease(
                                        operation.victimId,
                                        *completion_lease),
                                "HnfSLCSF dirty-victim commit lacks "
                                "exact lease\n");
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfEvictReq>) {
                std::visit(
                    [this, &header](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfFlushSf>) {
                            flushSf(header.lineAddress);
                        } else {
                            flushL3(header.lineAddress);
                        }
                    },
                    typed_request.operation);
            }
        },
        request.request);
    if (sf_victim.id != 0) {
        request.sfVictim = SlcSfSfVictim{
            SlcSfSeqId{sf_victim.id}, sf_victim.blockAddr,
            sf_victim.homeNodeId, sf_victim.state, sf_victim.owner,
            sf_victim.sharers};
    }
    if (preserved_victim) {
        request.slcVictimSeal.reset();
    }
    request.mutationCommitted = true;
}

void
HnfSLCSF::advanceMutation(InflightRequest& request)
{
    switch (*request.mutationStage) {
      case MutationStage::U0DecodeValidate: {
        request.resourcesPrepared = hasSfReservation(
            requestHeader(request.request).pocEntryId);
        // Invoke the always-on validator for every mutation category. Decode
        // errors retain priority in the terminal status, but validation still
        // remains part of the common U0 boundary.
        const bool token_valid = validateMutationToken(request.request);
        if (auto error = validateMutationRequest(request.request)) {
            request.terminalResponse = std::visit(
                [&error](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request, std::move(*error));
                },
                request.request);
            request.mutationStage = MutationStage::U3CheckLatch;
        } else if (!token_valid) {
            request.terminalReplayReason =
                SlcSfReplayReason::StaleCommitToken;
            request.mutationStage = MutationStage::U3CheckLatch;
            request.completeCycle = wakeupCycle;
            latchMutationResponse(request);
        } else if (!claimDurableCompletion(request)) {
            request.terminalResponse = std::visit(
                [](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request,
                        SlcSfError{
                            SlcSfErrorCode::UnknownCompletion,
                            "durable completion owner is already claimed"});
                },
                request.request);
            request.mutationStage = MutationStage::U3CheckLatch;
        } else {
            request.mutationStage = MutationStage::U1PrepareResources;
        }
        break;
      }
      case MutationStage::U1PrepareResources:
        if (!durableClaimMatches(request)) {
            request.terminalResponse = std::visit(
                [](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request,
                        SlcSfError{
                            SlcSfErrorCode::UnknownCompletion,
                            "durable completion claim changed before U1"});
                },
                request.request);
            request.mutationStage = MutationStage::U3CheckLatch;
            request.completeCycle = std::max(
                request.completeCycle, wakeupCycle + 1);
        } else if (!validateMutationToken(request.request)) {
            request.terminalReplayReason =
                SlcSfReplayReason::StaleCommitToken;
            request.mutationStage = MutationStage::U3CheckLatch;
            request.completeCycle = std::max(
                request.completeCycle, wakeupCycle + 1);
        } else if (prepareMutationResources(request)) {
            request.mutationStage = request.terminalReplayReason ?
                MutationStage::U3CheckLatch :
                MutationStage::U2ArrayWrite;
            if (request.terminalReplayReason) {
                request.completeCycle = std::max(
                    request.completeCycle, wakeupCycle + 1);
            }
        } else {
            // Accepted work remains in U1. Contention is a service stall,
            // not another admission attempt and not a correctness Replay.
            ++serviceStalls;
        }
        break;
      case MutationStage::U2ArrayWrite:
        panic_if(request.slcVictim.has_value() !=
                     request.slcVictimSeal.has_value(),
                 "HnfSLCSF U2 has unpaired dirty-victim snapshot/seal\n");
        if (!durableClaimMatches(request)) {
            request.terminalResponse = std::visit(
                [](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request,
                        SlcSfError{
                            SlcSfErrorCode::UnknownCompletion,
                            "durable completion claim changed before U2"});
                },
                request.request);
        } else if (!validateMutationToken(request.request)) {
            request.terminalReplayReason =
                SlcSfReplayReason::StaleCommitToken;
        } else if (request.slcVictim &&
                   !dirtyVictimWriteCanProceed(
                       requestHeader(request.request).lineAddress,
                       mutationTarget(request.request),
                       *request.slcVictim,
                       *request.slcVictimSeal)) {
            request.terminalReplayReason =
                SlcSfReplayReason::StaleCommitToken;
        } else {
            executeMutation(request);
        }
        request.mutationStage = MutationStage::U3CheckLatch;
        if (request.terminalResponse || request.terminalReplayReason) {
            request.completeCycle = std::max(
                request.completeCycle, wakeupCycle + 1);
        }
        if (request.completeCycle <= wakeupCycle) {
            latchMutationResponse(request);
        }
        break;
      case MutationStage::U3CheckLatch:
        if (request.completeCycle <= wakeupCycle) {
            latchMutationResponse(request);
        }
        break;
    }
}

void
HnfSLCSF::latchMutationResponse(InflightRequest& request)
{
    if (request.terminalReplayReason) {
        const SlcSfReplayReason reason = *request.terminalReplayReason;
        request.terminalResponse = std::visit(
            [this, reason](const auto& typed_request) {
                return makeSlcSfReplayResponse(
                    typed_request,
                    SlcSfReplay{reason, replayDeadline(), true});
            },
            request.request);
    }
    if (!request.terminalResponse) {
        request.terminalResponse = makeTerminalResponse(
            request.request, std::move(request.slcVictim),
            std::move(request.sfVictim), request.completionLease);
    }
    const SlcSfTerminalStatus status = request.terminalResponse->status();
    const FinishReason reason = status == SlcSfTerminalStatus::Done ?
        FinishReason::Done : status == SlcSfTerminalStatus::Replay ?
            FinishReason::Replay : FinishReason::Error;
    finishInflight(
        request, reason, std::move(*request.terminalResponse));
}

void
HnfSLCSF::finishInflight(
    InflightRequest& request, FinishReason reason,
    SlcSfResponse response)
{
    panic_if(request.cleanupDone,
             "HnfSLCSF request=%llu is finalized more than once\n",
             static_cast<unsigned long long>(
                 requestHeader(request.request).reqId.value));
    const auto& header = requestHeader(request.request);
    panic_if(response.reqId() != header.reqId ||
                 response.pocEntryId() != header.pocEntryId,
             "HnfSLCSF final response identity disagrees with request\n");
    panic_if(response.operationKind() !=
                 slcSfResponseOperation(request.request),
             "HnfSLCSF final response operation disagrees with request\n");
    const SlcSfTerminalStatus expected =
        reason == FinishReason::Done ? SlcSfTerminalStatus::Done :
        reason == FinishReason::Error ? SlcSfTerminalStatus::Error :
                                       SlcSfTerminalStatus::Replay;
    panic_if(response.status() != expected,
             "HnfSLCSF final reason and response status disagree\n");
    panic_if((reason == FinishReason::Replay ||
              reason == FinishReason::Error ||
              reason == FinishReason::Cancelled) &&
                 request.mutationCommitted,
             "HnfSLCSF cannot roll back a committed mutation\n");

    if (request.mutationCommitted) {
        checkLineInvariant(requestHeader(request.request).lineAddress);
    }
    if (request.slcVictimId) {
        if (reason == FinishReason::Done && request.mutationCommitted) {
            panic_if(request.slcVictimSeal,
                     "HnfSLCSF hands off dirty victim with live seal\n");
            VictimEntry* entry = findDirtyVictim(*request.slcVictimId);
            panic_if(!entry ||
                         entry->state != VictimState::InstalledSnapshot ||
                         !entry->snapshot,
                     "HnfSLCSF hands off invalid dirty victim=%llu\n",
                     static_cast<unsigned long long>(
                         request.slcVictimId->value));
            entry->state = VictimState::HandedOff;
        } else {
            cancelDirtyVictimReservation(request);
        }
    }
    if (reason != FinishReason::Done || !request.mutationCommitted) {
        rollbackDurableClaim(request);
    }
    if (request.resourcesPrepared) {
        releaseSfResources(requestHeader(request.request).pocEntryId);
        request.resourcesPrepared = false;
    }
    if (request.setLockOwner) {
        setLocks.release(*request.setLockOwner);
        request.setLockOwner.reset();
    }
    if (reason == FinishReason::Replay) {
        ++correctnessReplays;
    } else if (reason == FinishReason::Cancelled) {
        ++cancelledRequests;
    }
    respPending.push_back(std::move(response));
    ++finishedRequests;
    request.cleanupDone = true;
    request.mutationStage.reset();
    request.terminalResponse.reset();
    request.terminalReplayReason.reset();
    request.completionLease.reset();
    assertVictimAccounting();
}

bool
HnfSLCSF::validateCommitToken(
    const SlcSfCommitToken& token, SlcSfReqId expected_lookup_req_id,
    uint64_t expected_line_address) const
{
    if (!token.lookupReqId.valid() || !expected_lookup_req_id.valid() ||
        token.lookupReqId != expected_lookup_req_id ||
        token.lineAddress != expected_line_address) {
        return false;
    }

    LookupSnapshot snapshot{};
    snapshot.lookupEpoch = token.lookupEpoch;
    snapshot.slc = ArraySnapshot{
        token.slc.hit, token.slc.set, token.slc.way,
        token.slc.generation, 0};
    snapshot.sf = ArraySnapshot{
        token.sf.hit, token.sf.set, token.sf.way,
        token.sf.generation, 0};
    return validateLookupSnapshot(expected_line_address, snapshot);
}

void
HnfSLCSF::promoteIngressRequests()
{
    while (!reqIngress.empty()) {
        reqReady.push_back(std::move(reqIngress.front()));
        reqIngress.pop_front();
    }
}

void
HnfSLCSF::issueReadyRequests()
{
    size_t lookup_issued = 0;
    size_t fill_issued = 0;
    size_t update_issued = 0;

    for (auto request = reqReady.begin(); request != reqReady.end();) {
        const RequestPipe pipe = requestPipe(*request);
        size_t* issued = nullptr;
        size_t width = 0;
        switch (pipe) {
          case RequestPipe::Lookup:
            issued = &lookup_issued;
            width = config.lookupIssueWidth;
            break;
          case RequestPipe::Fill:
            issued = &fill_issued;
            width = config.fillIssueWidth;
            break;
          case RequestPipe::Update:
            issued = &update_issued;
            width = config.updateIssueWidth;
            break;
        }

        if (inflightRequests.size() >= config.maxInflight ||
            respOccupied() >= config.respQueueEntries ||
            *issued == width) {
            ++serviceStalls;
            ++request;
            continue;
        }

        std::optional<uint64_t> set_lock_owner;
        if (config.enableSetLock) {
            panic_if(nextSetLockOwner == 0 ||
                         nextSetLockOwner == UINT64_MAX,
                     "HnfSLCSF set lock owner ID overflows\n");
            const auto& header = requestHeader(*request);
            const SlcSfSetLockRequest lock_request{
                slcSet(header.lineAddress), sfSet(header.lineAddress),
                pipe == RequestPipe::Lookup ? SlcSfSetLockMode::Read :
                                              SlcSfSetLockMode::Write};
            if (!setLocks.tryAcquire(nextSetLockOwner, lock_request)) {
                ++serviceStalls;
                ++request;
                continue;
            }
            set_lock_owner = nextSetLockOwner++;
        }

        // The in-flight entry is the reservation.  Capacity is checked before
        // emplacing it, and only a successfully emplaced request is erased
        // from ready, so issue and reservation are one state transition.
        const bool mutation = pipe != RequestPipe::Lookup;
        const bool early_lookup_replay =
            !mutation && lookupReplaysAtL0(*request);
        const bool adopted_reservation = hasSfReservation(
            requestHeader(*request).pocEntryId);
        uint64_t latency = serviceLatency(*request);
        if (early_lookup_replay) {
            latency = 1;
        }
        panic_if(latency > UINT64_MAX - wakeupCycle,
                 "HnfSLCSF service completion cycle overflows");
        inflightRequests.push_back(InflightRequest{
            std::move(*request), wakeupCycle, wakeupCycle + latency,
            mutation ? std::optional<MutationStage>(
                           MutationStage::U0DecodeValidate) : std::nullopt,
            std::nullopt, std::nullopt, adopted_reservation, false, false,
            early_lookup_replay, false, std::nullopt, std::nullopt,
            std::nullopt, std::nullopt, std::nullopt,
            set_lock_owner});
        request = reqReady.erase(request);
        ++*issued;
    }
}

void
HnfSLCSF::updateRegisteredCredits()
{
    if (!initialized || draining) {
        visibleReqCredits = 0;
        return;
    }
    visibleReqCredits = config.reqQueueEntries - reqOutstanding();
}

void
HnfSLCSF::assertRequestAccounting() const
{
    panic_if(reqOutstanding() > config.reqQueueEntries,
             "HnfSLCSF request accounting exceeds capacity (%zu/%zu)\n",
             reqOutstanding(), config.reqQueueEntries);
    panic_if(visibleReqCredits > config.reqQueueEntries - reqOutstanding(),
             "HnfSLCSF visible request credit exceeds free capacity "
             "(%zu/%zu)\n", visibleReqCredits,
             config.reqQueueEntries - reqOutstanding());
}

void
HnfSLCSF::assertResponseAccounting() const
{
    panic_if(respOccupied() > config.respQueueEntries,
             "HnfSLCSF response accounting exceeds capacity (%zu/%zu)\n",
             respOccupied(), config.respQueueEntries);
}

} // namespace gem5::Chi

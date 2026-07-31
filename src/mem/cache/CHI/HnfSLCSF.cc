#include "mem/cache/CHI/HnfSLCSF.hh"

#include <algorithm>
#include <stdexcept>
#include <unordered_set>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HnfSLCSF.hh"
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

SlcSfStatOperation
statOperation(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                return SlcSfStatOperation::Lookup;
            } else if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return SlcSfStatOperation::Fill;
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                return SlcSfStatOperation::Update;
            } else {
                return SlcSfStatOperation::Evict;
            }
        },
        request);
}

size_t
replayReasonIndex(SlcSfReplayReason reason)
{
    return static_cast<size_t>(reason);
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

uint64_t
allocatePersistentIdentity(uint64_t& next, const char* name)
{
    panic_if(next == 0 || next == UINT64_MAX,
             "HnfSLCSF %s identity space exhausted next=%llu\n", name,
             static_cast<unsigned long long>(next));
    return next++;
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
HnfSLCSFSetLockManager::heldLockCount(uint64_t owner) const
{
    const auto owned = [owner](const std::optional<Holder>& holder) {
        return holder && holder->owner == owner;
    };
    return std::count_if(slcOwners.begin(), slcOwners.end(), owned) +
        std::count_if(sfOwners.begin(), sfOwners.end(), owned);
}

bool
HnfSLCSFSetLockManager::holdsExact(
    uint64_t owner, const SlcSfSetLockRequest& request) const
{
    if ((request.slcSet && *request.slcSet >= slcOwners.size()) ||
        (request.sfSet && *request.sfSet >= sfOwners.size())) {
        return false;
    }
    const auto matches = [owner, &request](
                             const std::optional<Holder>& holder) {
        return holder && holder->owner == owner &&
            holder->mode == request.mode;
    };
    if ((request.slcSet && !matches(slcOwners[*request.slcSet])) ||
        (request.sfSet && !matches(sfOwners[*request.sfSet]))) {
        return false;
    }
    return heldLockCount(owner) ==
        static_cast<size_t>(request.slcSet.has_value()) +
        static_cast<size_t>(request.sfSet.has_value());
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
                   HnfSLCSFPipelineConfig pipeline_config,
                   HnfSLCSFStatsSink* stats_sink)
    : HnfSLCSFBackend(block_size, slc_num_sets, slc_num_ways,
                      sf_num_sets, sf_num_ways, seq_entries,
                      pipeline_config.slcReplacementPolicy,
                      pipeline_config.slcReplacementSeed,
                      pipeline_config.allowReplacementPolicyOverride),
      config(pipeline_config), victimBuffer(config.victimBufferEntries),
      setLocks(slc_num_sets, sf_num_sets),
      completionProducer(std::make_shared<const uint8_t>(0)),
      visibleReqCredits(config.reqQueueEntries), statsSink(stats_sink)
{
    validateConfig(config);
    visibleReqCreditTicks.resize(config.reqQueueEntries, std::nullopt);
    assertRequestAccounting();
    assertResponseAccounting();
    assertVictimAccounting();
    runBackendGlobalInvariantCheck();
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

bool
HnfSLCSF::wasAccepted(SlcSfReqId reqId) const
{
    const auto next = acceptedIdRanges.upper_bound(reqId.value);
    if (next == acceptedIdRanges.begin()) {
        return false;
    }
    const auto previous = std::prev(next);
    return reqId.value <= previous->second;
}

void
HnfSLCSF::rememberAccepted(SlcSfReqId reqId)
{
    auto next = acceptedIdRanges.upper_bound(reqId.value);
    auto previous = next == acceptedIdRanges.begin() ?
        acceptedIdRanges.end() : std::prev(next);
    const bool joins_previous = previous != acceptedIdRanges.end() &&
        previous->second != UINT64_MAX &&
        previous->second + 1 == reqId.value;
    const bool joins_next = next != acceptedIdRanges.end() &&
        reqId.value != UINT64_MAX && reqId.value + 1 == next->first;
    if (joins_previous && joins_next) {
        previous->second = next->second;
        acceptedIdRanges.erase(next);
    } else if (joins_previous) {
        previous->second = reqId.value;
    } else if (joins_next) {
        const uint64_t end = next->second;
        acceptedIdRanges.erase(next);
        acceptedIdRanges.emplace(reqId.value, end);
    } else {
        acceptedIdRanges.emplace(reqId.value, reqId.value);
    }
}

uint64_t
HnfSLCSF::baseServiceLatency(const SlcSfRequest& request) const
{
    return std::max<uint64_t>(1, std::visit(
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
        request));
}

uint64_t
HnfSLCSF::serviceLatency(const SlcSfRequest& request) const
{
    // Mutation callers must validate the commit token before entering this
    // routine: both victim probes intentionally dereference its selected way.
    uint64_t latency = baseServiceLatency(request);
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
    return pendingSlcVictimKind(request) == SlcSfStatVictim::DirtySlc;
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
    return tryEnqueue(
        std::move(request),
        Gem5Internal::_curTickPtr ? curTick() : wakeupTick);
}

SlcSfEnqueueResult
HnfSLCSF::tryEnqueue(SlcSfRequest&& request, Tick acceptedTick)
{
    if (Gem5Internal::_curTickPtr) {
        settleOccupancy(acceptedTick);
    }
    assertRequestAccounting();
    if (draining) {
        ++drainingRejects;
        return SlcSfEnqueueResult::Draining;
    }
    if (!initialized) {
        ++initializingRejects;
        return SlcSfEnqueueResult::Initializing;
    }
    const auto credit = std::find_if(
        visibleReqCreditTicks.begin(), visibleReqCreditTicks.end(),
        [acceptedTick](const std::optional<Tick>& produced) {
            return !produced || acceptedTick > *produced;
        });
    if (credit == visibleReqCreditTicks.end() ||
        reqOutstanding() == config.reqQueueEntries) {
        ++noCreditRejects;
        ++stats.noCredit;
        if (statsSink) {
            statsSink->rejectedNoCredit();
        }
        return SlcSfEnqueueResult::NoCredit;
    }

    const auto& header = requestHeader(request);
    panic_if(!header.reqId.valid() || wasAccepted(header.reqId),
             "HnfSLCSF duplicate accepted request ID=%llu\n",
             static_cast<unsigned long long>(header.reqId.value));
    const SlcSfStatOperation operation = statOperation(request);
    ++stats.operations[static_cast<size_t>(operation)];
    rememberAccepted(header.reqId);
    activeTraces.emplace(
        header.reqId.value,
        HnfSLCSFTraceRecord{header.reqId, header.pocEntryId,
                            header.lineAddress,
                            slcSfResponseOperation(request), acceptedTick,
                            std::nullopt, std::nullopt, std::nullopt,
                            SlcSfTerminalStatus::Error,
                            std::nullopt});
    ++acceptedTotal;
    DPRINTF(HnfSLCSF,
            "pipeline accepted req=%llu pocq=%u line=%#llx op=%u "
            "accepted=%llu\n",
            static_cast<unsigned long long>(header.reqId.value),
            header.pocEntryId,
            static_cast<unsigned long long>(header.lineAddress),
            static_cast<unsigned>(slcSfResponseOperation(request)),
            static_cast<unsigned long long>(acceptedTick));
    if (statsSink) {
        statsSink->accepted(operation);
    }
    reqIngress.push_back(std::move(request));
    visibleReqCreditTicks.erase(credit);
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
    wakeup(now, 1);
}

void
HnfSLCSF::wakeup(Tick now, uint64_t elapsedCycles)
{
    panic_if(elapsedCycles == 0 || elapsedCycles > UINT64_MAX - wakeupCycle,
             "HnfSLCSF invalid elapsed child cycles=%llu\n",
             static_cast<unsigned long long>(elapsedCycles));
    if (Gem5Internal::_curTickPtr) {
        settleOccupancy(now);
    } else {
        sampleOccupancy(elapsedCycles);
    }
    wakeupCycle += elapsedCycles;
    wakeupTick = now;
    if (!initialized) {
        panic_if(initializationCyclesRemaining == 0,
                 "HnfSLCSF initialization has no completion deadline\n");
        initializationCyclesRemaining =
            elapsedCycles >= initializationCyclesRemaining ?
                0 : initializationCyclesRemaining - elapsedCycles;
        if (initializationCyclesRemaining == 0) {
            finishInitialization();
        }
        updateRegisteredCredits(now);
        checkLifecycle();
        assertRequestAccounting();
        assertResponseAccounting();
        assertVictimAccounting();
        assertGlobalInvariants();
        return;
    }
    // Registered boundary order is intentionally explicit.  In particular,
    // completion precedes issue so a newly issued operation cannot complete
    // on this wakeup even if a defensive latency clamp is ever needed.
    promotePendingResponses();
    promoteIngressRequests();
    completeInflightRequests();
    issueReadyRequests();
    updateRegisteredCredits(now);
    checkLifecycle();
    assertRequestAccounting();
    assertResponseAccounting();
    assertVictimAccounting();
    assertGlobalInvariants();
}

void
HnfSLCSF::sampleOccupancy(uint64_t elapsedCycles)
{
    const size_t req_occupancy = reqOutstanding();
    const size_t resp_occupancy = respOccupied();
    const size_t inflight_occupancy = inflightRequests.size();
    stats.reqOccupancySamples += elapsedCycles;
    stats.reqOccupancyTotal += req_occupancy * elapsedCycles;
    stats.reqOccupancyMax = std::max<uint64_t>(
        stats.reqOccupancyMax, req_occupancy);
    stats.respOccupancySamples += elapsedCycles;
    stats.respOccupancyTotal += resp_occupancy * elapsedCycles;
    stats.respOccupancyMax = std::max<uint64_t>(
        stats.respOccupancyMax, resp_occupancy);
    stats.inflightOccupancySamples += elapsedCycles;
    stats.inflightOccupancyTotal += inflight_occupancy * elapsedCycles;
    stats.inflightOccupancyMax = std::max<uint64_t>(
        stats.inflightOccupancyMax, inflight_occupancy);
    const bool req_full = req_occupancy == config.reqQueueEntries;
    const bool resp_full = resp_occupancy == config.respQueueEntries;
    if (req_full) {
        stats.reqFullCycles += elapsedCycles;
    }
    if (resp_full) {
        stats.respFullCycles += elapsedCycles;
    }
    if (statsSink) {
        statsSink->sampledOccupancy(
            req_occupancy, resp_occupancy, inflight_occupancy,
            elapsedCycles, req_full, resp_full);
        statsSink->sampledStorage(
            slcValidLineCount(), slcCapacityLineCount(),
            maxValidWaysInSet(), elapsedCycles);
    }
}

void
HnfSLCSF::settleOccupancy(Tick observerTick)
{
    if (!lastOccupancyTick) {
        lastOccupancyTick = observerTick;
        return;
    }
    panic_if(observerTick < *lastOccupancyTick,
             "HnfSLCSF occupancy accounting moved backwards\n");
    // Child edges are phase-aligned to Tick zero. Counting crossed edge
    // indices preserves the fractional remainder across any number of
    // mid-edge queue transitions (e.g. 0 -> enqueue@5 -> wakeup@10).
    const uint64_t cycles =
        observerTick / config.childClockPeriod -
        *lastOccupancyTick / config.childClockPeriod;
    if (cycles != 0) {
        sampleOccupancy(cycles);
    }
    // State transitions after this call own the next complete interval.
    lastOccupancyTick = observerTick;
}

size_t
HnfSLCSF::reqOutstanding() const
{
    return reqIngress.size() + reqReady.size() + inflightRequests.size();
}

size_t
HnfSLCSF::registeredReqCredits() const
{
    if (!Gem5Internal::_curTickPtr) {
        return visibleReqCredits;
    }
    return registeredReqCredits(curTick());
}

size_t
HnfSLCSF::registeredReqCredits(Tick observerTick) const
{
    return std::count_if(
        visibleReqCreditTicks.begin(), visibleReqCreditTicks.end(),
        [observerTick](const std::optional<Tick>& produced) {
            return !produced || observerTick > *produced;
        });
}

size_t
HnfSLCSF::respOccupied() const
{
    return inflightRequests.size() + respPending.size() + respVisible.size();
}

size_t
HnfSLCSF::respVisibleCount() const
{
    if (!Gem5Internal::_curTickPtr) {
        return respVisible.size();
    }
    return respVisibleCount(curTick());
}

size_t
HnfSLCSF::respVisibleCount(Tick observerTick) const
{
    return std::count_if(
        respVisibleTicks.begin(), respVisibleTicks.end(),
        [observerTick](Tick produced) { return observerTick > produced; });
}

const SlcSfResponse*
HnfSLCSF::frontVisibleResponse() const
{
    if (respVisible.empty() ||
        (Gem5Internal::_curTickPtr && curTick() <= respVisibleTicks.front())) {
        return nullptr;
    }
    return &respVisible.front();
}

SlcSfCompletionAckResult
HnfSLCSF::acknowledgeVisibleCompletion(const SlcSfResponse& response)
{
    if (!frontVisibleResponse()) {
        return SlcSfCompletionAckResult::NotVisible;
    }
    if (Gem5Internal::_curTickPtr) {
        settleOccupancy(curTick());
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
    respVisibleTicks.pop_front();
    assertResponseAccounting();
    return SlcSfCompletionAckResult::Acknowledged;
}

std::optional<SlcSfResponse>
HnfSLCSF::popVisibleResponse()
{
    assertResponseAccounting();
    if (!frontVisibleResponse()) {
        return std::nullopt;
    }
    if (Gem5Internal::_curTickPtr) {
        settleOccupancy(curTick());
    }
    const auto* update =
        std::get_if<SlcSfUpdateResponse>(&respVisible.front().payload());
    panic_if(
        respVisible.front().status() == SlcSfTerminalStatus::Done && update &&
            update->completionLease,
        "HnfSLCSF durable Done requires acknowledgeVisibleCompletion\n");

    SlcSfResponse response = std::move(respVisible.front());
    respVisible.pop_front();
    respVisibleTicks.pop_front();
    assertResponseAccounting();
    return response;
}

SlcSfCancelResult
HnfSLCSF::cancelRequest(
    uint32_t poc_entry_id, SlcSfReqId req_id, Tick now)
{
    if (Gem5Internal::_curTickPtr) {
        settleOccupancy(now);
    }
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
        const auto trace = activeTraces.find(req_id.value);
        panic_if(trace == activeTraces.end() ||
                     !trace->second.issueTick,
                 "HnfSLCSF in-flight cancellation lacks issue tick "
                 "req=%llu\n",
                 static_cast<unsigned long long>(req_id.value));
        // A parent action at the issue Tick cannot observe across the child
        // register boundary. Leave the request live and reject that same-Tick
        // cancellation explicitly; a later Tick may still cancel it.
        if (now <= *trace->second.issueTick) {
            return SlcSfCancelResult::TooLate;
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
            *inflight, FinishReason::Cancelled, std::move(response), now);
        inflightRequests.erase(inflight);
        updateRegisteredCredits(now);
        assertRequestAccounting();
        assertResponseAccounting();
        if (workAvailableCallback) {
            workAvailableCallback();
        }
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

std::optional<uint64_t>
HnfSLCSF::calculateNextWakeupCycle() const
{
    const auto next_cycle = [this]() {
        panic_if(wakeupCycle == UINT64_MAX,
                 "HnfSLCSF next wakeup cycle overflows\n");
        return wakeupCycle + 1;
    };

    if (!initialized) {
        panic_if(initializationCyclesRemaining == 0 ||
                     initializationCyclesRemaining >
                         UINT64_MAX - wakeupCycle,
                 "HnfSLCSF initialization deadline overflows\n");
        return wakeupCycle + initializationCyclesRemaining;
    }

    // Both double-buffer promotions, ready issue, and response visibility are
    // registered on the immediately following child edge.
    if (!respPending.empty() || !reqIngress.empty() || !reqReady.empty()) {
        return next_cycle();
    }

    std::optional<uint64_t> earliest;
    for (const InflightRequest& request : inflightRequests) {
        uint64_t candidate = request.completeCycle;
        if (request.mutationStage &&
            (*request.mutationStage == MutationStage::U0DecodeValidate ||
             *request.mutationStage == MutationStage::U1PrepareResources ||
             request.mutationStalled)) {
            candidate = next_cycle();
        }
        candidate = std::max(candidate, next_cycle());
        earliest = earliest ? std::min(*earliest, candidate) : candidate;
    }
    return earliest;
}

void
HnfSLCSF::resetForColdStart()
{
    panic_if(reqOutstanding() != 0 || respOccupied() != 0 ||
                 setLockCount() != 0,
             "HnfSLCSF cold reset requires empty timing queues and locks\n");
    resetStorageForColdStart();
    runBackendGlobalInvariantCheck();
    for (VictimEntry& entry : victimBuffer) {
        entry = VictimEntry{};
    }
    nextVictimId = 1;
    nextCompletionNonce = 1;
    nextSetLockOwner = 1;
#ifdef UNIT_TEST
    pendingVictimIdCorruptionForTest.reset();
#endif
    lastOccupancyTick = Gem5Internal::_curTickPtr ?
        std::optional<Tick>(curTick()) : std::nullopt;
    drainRequested = false;
    draining = false;
    beginInitialization();
}

void
HnfSLCSF::beginInitialization()
{
    initialized = false;
    initializationCyclesRemaining = std::max<size_t>(config.initLatency, 1);
    visibleReqCredits = 0;
    visibleReqCreditTicks.clear();
}

void
HnfSLCSF::finishInitialization()
{
    initialized = true;
    initializationCyclesRemaining = 0;
}

void
HnfSLCSF::requestDrain()
{
    drainRequested = true;
}

void
HnfSLCSF::beginDraining()
{
    drainRequested = true;
    draining = true;
    visibleReqCredits = 0;
    visibleReqCreditTicks.clear();
}

void
HnfSLCSF::resumeFromDrain()
{
    drainRequested = false;
    draining = false;
    updateRegisteredCredits(
        Gem5Internal::_curTickPtr ? curTick() : wakeupTick);
    checkLifecycle();
}

void
HnfSLCSF::serializePersistentState(CheckpointOut& cp) const
{
    // Admission intentionally remains open during gem5's concurrent drain.
    // Serialization is reached only after the global fixed-point pass has
    // proved that every producer and this consumer are idle.
    panic_if(!drainRequested,
             "HnfSLCSF checkpoint requires a global drain request\n");
    panic_if(!initialized || initializationCyclesRemaining != 0,
             "HnfSLCSF checkpoint requires completed initialization\n");
    panic_if(!reqIngress.empty() || !reqReady.empty() ||
                 !inflightRequests.empty(),
             "HnfSLCSF checkpoint requires empty request pipeline and "
             "ready ticks\n");
    panic_if(!respPending.empty() || !respVisible.empty(),
             "HnfSLCSF checkpoint requires empty response pipeline\n");
    panic_if(setLockCount() != 0 || victimBufferOccupancy() != 0 ||
                 HnfSLCSFBackend::isBusy(),
             "HnfSLCSF checkpoint requires no reservations or active owners\n");
    panic_if(!isCompletelyIdle(),
             "HnfSLCSF checkpoint requires completely drained state\n");

    paramOut(cp, "initialized", initialized);
    paramOut(cp, "wakeupCycle", wakeupCycle);
    paramOut(cp, "wakeupTick", wakeupTick);
    paramOut(cp, "nextVictimId", nextVictimId);
    paramOut(cp, "nextReservationId", nextCompletionNonce);
    paramOut(cp, "nextSetLockOwner", nextSetLockOwner);
    paramOut(cp, "victimBufferEntries", victimBuffer.size());

    std::vector<uint64_t> victim_id;
    std::vector<uint32_t> victim_state;
    std::vector<uint64_t> victim_address;
    std::vector<uint32_t> victim_snapshot_valid;
    std::vector<uint32_t> victim_snapshot_state;
    std::vector<uint32_t> victim_snapshot_owner;
    std::vector<uint32_t> victim_snapshot_dirty;
    std::vector<uint64_t> victim_snapshot_data_size;
    std::vector<uint32_t> victim_snapshot_data;
    for (size_t i = 0; i < victimBuffer.size(); ++i) {
        // A serialized service is drained, so normalize both Free and
        // Released implementation states to one canonical empty encoding.
        victim_id.push_back(0);
        victim_state.push_back(static_cast<uint32_t>(VictimState::Free));
        victim_address.push_back(0);
        victim_snapshot_valid.push_back(0);
        victim_snapshot_state.push_back(0);
        victim_snapshot_owner.push_back(0);
        victim_snapshot_dirty.push_back(0);
        victim_snapshot_data_size.push_back(0);
    }
    arrayParamOut(cp, "victimId", victim_id);
    arrayParamOut(cp, "victimState", victim_state);
    arrayParamOut(cp, "victimAddress", victim_address);
    arrayParamOut(cp, "victimSnapshotValid", victim_snapshot_valid);
    arrayParamOut(cp, "victimSnapshotState", victim_snapshot_state);
    arrayParamOut(cp, "victimSnapshotOwner", victim_snapshot_owner);
    arrayParamOut(cp, "victimSnapshotDirty", victim_snapshot_dirty);
    arrayParamOut(cp, "victimSnapshotDataSize", victim_snapshot_data_size);
    arrayParamOut(cp, "victimSnapshotData", victim_snapshot_data);

    Serializable::ScopedCheckpointSection backend_section(cp, "backend");
    HnfSLCSFBackend::serializePersistentState(cp);
}

void
HnfSLCSF::unserializePersistentState(CheckpointIn& cp)
{
    fatal_if(reqOutstanding() != 0 || respOccupied() != 0 ||
                 victimBufferOccupancy() != 0 || setLockCount() != 0 ||
                 HnfSLCSFBackend::isBusy(),
             "HnfSLCSF refuses to restore over live timing/backend state\n");
    bool saved_initialized = false;
    size_t saved_victim_entries = 0;
    paramIn(cp, "initialized", saved_initialized);
    paramIn(cp, "wakeupCycle", wakeupCycle);
    paramIn(cp, "wakeupTick", wakeupTick);
    paramIn(cp, "nextVictimId", nextVictimId);
    paramIn(cp, "nextReservationId", nextCompletionNonce);
    paramIn(cp, "nextSetLockOwner", nextSetLockOwner);
    paramIn(cp, "victimBufferEntries", saved_victim_entries);
    fatal_if(!saved_initialized || saved_victim_entries != victimBuffer.size() ||
                 wakeupCycle == UINT64_MAX || wakeupTick == MaxTick ||
                 nextVictimId == 0 || nextCompletionNonce == 0 ||
                 nextSetLockOwner == 0,
             "HnfSLCSF checkpoint has invalid lifecycle or next IDs\n");

    std::vector<uint64_t> victim_id;
    std::vector<uint32_t> victim_state;
    std::vector<uint64_t> victim_address;
    std::vector<uint32_t> victim_snapshot_valid;
    std::vector<uint32_t> victim_snapshot_state;
    std::vector<uint32_t> victim_snapshot_owner;
    std::vector<uint32_t> victim_snapshot_dirty;
    std::vector<uint64_t> victim_snapshot_data_size;
    std::vector<uint32_t> victim_snapshot_data;
    arrayParamIn(cp, "victimId", victim_id);
    arrayParamIn(cp, "victimState", victim_state);
    arrayParamIn(cp, "victimAddress", victim_address);
    arrayParamIn(cp, "victimSnapshotValid", victim_snapshot_valid);
    arrayParamIn(cp, "victimSnapshotState", victim_snapshot_state);
    arrayParamIn(cp, "victimSnapshotOwner", victim_snapshot_owner);
    arrayParamIn(cp, "victimSnapshotDirty", victim_snapshot_dirty);
    arrayParamIn(cp, "victimSnapshotDataSize", victim_snapshot_data_size);
    arrayParamIn(cp, "victimSnapshotData", victim_snapshot_data);
    const size_t entries = victimBuffer.size();
    fatal_if(victim_id.size() != entries || victim_state.size() != entries ||
                 victim_address.size() != entries ||
                 victim_snapshot_valid.size() != entries ||
                 victim_snapshot_state.size() != entries ||
                 victim_snapshot_owner.size() != entries ||
                 victim_snapshot_dirty.size() != entries ||
                 victim_snapshot_data_size.size() != entries,
             "HnfSLCSF checkpoint has malformed VictimBuffer arrays\n");
    for (size_t i = 0; i < entries; ++i) {
        fatal_if(victim_snapshot_valid[i] > 1 ||
                     victim_snapshot_dirty[i] > 1,
                 "HnfSLCSF checkpoint has non-boolean victim fields\n");
        fatal_if(victim_id[i] != 0 ||
                     victim_state[i] !=
                         static_cast<uint32_t>(VictimState::Free) ||
                     victim_address[i] != 0 ||
                     victim_snapshot_valid[i] != 0 ||
                     victim_snapshot_state[i] != 0 ||
                     victim_snapshot_owner[i] != 0 ||
                     victim_snapshot_dirty[i] != 0 ||
                     victim_snapshot_data_size[i] != 0,
                 "HnfSLCSF checkpoint VictimBuffer is not canonical empty\n");
        victimBuffer[i] = VictimEntry{};
    }
    fatal_if(!victim_snapshot_data.empty(),
             "HnfSLCSF canonical VictimBuffer contains snapshot data\n");

    {
        Serializable::ScopedCheckpointSection backend_section(cp, "backend");
        HnfSLCSFBackend::unserializePersistentState(cp);
    }
    runBackendGlobalInvariantCheck();

    reqIngress.clear();
    reqReady.clear();
    inflightRequests.clear();
    respPending.clear();
    respVisible.clear();
    respVisibleTicks.clear();
    lastOccupancyTick = Gem5Internal::_curTickPtr ?
        std::optional<Tick>(curTick()) : std::nullopt;
    activeTraces.clear();
    acceptedIdRanges.clear();
    completedTrace.reset();
    acceptedTotal = 0;
    terminalDoneTotal = 0;
    terminalReplayTotal = 0;
    terminalErrorTotal = 0;
    finishedRequests = 0;
    cancelledRequests = 0;
    correctnessReplays = 0;
#ifdef UNIT_TEST
    pendingVictimIdCorruptionForTest.reset();
#endif
    initialized = true;
    initializationCyclesRemaining = 0;
    drainRequested = false;
    draining = false;
    visibleReqCredits = 0;
    visibleReqCreditTicks.clear();
    updateRegisteredCredits(
        Gem5Internal::_curTickPtr ? curTick() : wakeupTick);
    checkLifecycle();
    assertVictimAccounting();
    assertRequestAccounting();
    assertResponseAccounting();
}

void
HnfSLCSF::promotePendingResponses()
{
    while (!respPending.empty()) {
        const SlcSfReqId req_id = respPending.front().reqId();
        auto trace = activeTraces.find(req_id.value);
        panic_if(trace == activeTraces.end() ||
                     !trace->second.acceptedTick ||
                     !trace->second.issueTick ||
                     !trace->second.completeTick ||
                     trace->second.visibleTick ||
                     *trace->second.issueTick <=
                         *trace->second.acceptedTick ||
                     *trace->second.completeTick <=
                         *trace->second.issueTick ||
                     wakeupTick <= *trace->second.completeTick,
                 "HnfSLCSF trace tick order must be "
                 "accepted < issue < complete < visible req=%llu "
                 "accepted=%llu issue=%llu complete=%llu visible=%llu\n",
                 static_cast<unsigned long long>(req_id.value),
                 static_cast<unsigned long long>(
                     trace == activeTraces.end() ||
                         !trace->second.acceptedTick ? 0 :
                         *trace->second.acceptedTick),
                 static_cast<unsigned long long>(
                     trace == activeTraces.end() ||
                         !trace->second.issueTick ? 0 :
                         *trace->second.issueTick),
                 static_cast<unsigned long long>(
                     trace == activeTraces.end() ||
                         !trace->second.completeTick ? 0 :
                         *trace->second.completeTick),
                 static_cast<unsigned long long>(wakeupTick));
        trace->second.visibleTick = wakeupTick;
        DPRINTF(HnfSLCSF,
                "pipeline terminal req=%llu pocq=%u line=%#llx op=%u "
                "accepted=%llu issue=%llu complete=%llu visible=%llu "
                "status=%u replay=%d\n",
                static_cast<unsigned long long>(req_id.value),
                trace->second.pocEntryId,
                static_cast<unsigned long long>(trace->second.lineAddress),
                static_cast<unsigned>(trace->second.operation),
                static_cast<unsigned long long>(*trace->second.acceptedTick),
                static_cast<unsigned long long>(
                    trace->second.issueTick.value_or(0)),
                static_cast<unsigned long long>(*trace->second.completeTick),
                static_cast<unsigned long long>(*trace->second.visibleTick),
                static_cast<unsigned>(trace->second.status),
                trace->second.replayReason ?
                    static_cast<int>(*trace->second.replayReason) : -1);
        completedTrace = trace->second;
        const Tick tick_latency =
            *trace->second.visibleTick - *trace->second.acceptedTick;
        const uint64_t latency =
            tick_latency / config.childClockPeriod +
            (tick_latency % config.childClockPeriod != 0);
        activeTraces.erase(trace);
        ++stats.acceptedToVisibleSamples;
        stats.acceptedToVisibleLatencyTotal += latency;
        if (statsSink) {
            statsSink->becameVisible(latency);
        }
        respVisible.push_back(std::move(respPending.front()));
        respVisibleTicks.push_back(wakeupTick);
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
            const ProtectedStateSnapshot stage_snapshot =
                protectedStateSnapshot(
                    requestHeader(request->request).lineAddress, true);
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
            finishInflight(
                *request, reason, std::move(response), wakeupTick);
            if (reason != FinishReason::Done) {
                panic_if(stage_snapshot != protectedStateSnapshot(
                             requestHeader(request->request).lineAddress,
                             true),
                         "HnfSLCSF lookup Replay changed protected state\n");
            }
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

#ifdef UNIT_TEST
bool
HnfSLCSF::corruptInstalledDirtyVictimIdForTest(
    SlcSfVictimId replacement)
{
    for (InflightRequest& request : inflightRequests) {
        if (request.mutationStage == MutationStage::U2ArrayWrite &&
            request.slcVictim && request.slcVictimSeal) {
            pendingVictimIdCorruptionForTest = VictimIdCorruptionForTest{
                requestHeader(request.request).reqId, replacement};
            return true;
        }
    }
    return false;
}
#endif

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
    std::unordered_set<uint64_t> victim_ids;
    for (const VictimEntry& entry : victimBuffer) {
        const bool empty = entry.state == VictimState::Free ||
            entry.state == VictimState::Released;
        const bool has_writeback = entry.writebackTxnId != 0;
        const bool full_snapshot = entry.snapshot &&
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
        panic_if(empty &&
                     (entry.snapshot || entry.lineAddress != 0 ||
                      entry.writebackRequester != 0 ||
                      entry.writebackOpcode != 0 || has_writeback ||
                      entry.completionLease),
                 "HnfSLCSF empty victim slot retains owned state id=%llu\n",
                 static_cast<unsigned long long>(entry.id.value));
        if (empty) {
            continue;
        }
        panic_if(entry.id.value == 0 ||
                     !victim_ids.insert(entry.id.value).second,
                 "HnfSLCSF invalid/duplicate live victim id=%llu\n",
                 static_cast<unsigned long long>(entry.id.value));
        if (entry.state == VictimState::Reserved) {
            panic_if(entry.snapshot || has_writeback || entry.completionLease,
                     "HnfSLCSF reserved victim owns premature state\n");
            continue;
        }
        panic_if(!full_snapshot,
                 "HnfSLCSF live victim lacks exact full snapshot id=%llu "
                 "state=%u\n",
                 static_cast<unsigned long long>(entry.id.value),
                 static_cast<unsigned>(entry.state));
        const bool before_writeback =
            entry.state == VictimState::InstalledSnapshot ||
            entry.state == VictimState::HandedOff;
        const bool claimed = entry.state == VictimState::ReleaseClaimed ||
            entry.state == VictimState::ReleaseCommittedAwaitAck;
        panic_if(before_writeback &&
                     (entry.writebackRequester != 0 ||
                      entry.writebackOpcode != 0 || has_writeback ||
                      entry.completionLease),
                 "HnfSLCSF pre-writeback victim owns completion state\n");
        panic_if(entry.state == VictimState::WritebackIssued &&
                     (!has_writeback || entry.completionLease),
                 "HnfSLCSF issued victim has invalid lease/transaction\n");
        panic_if(claimed &&
                     (!has_writeback || !entry.completionLease ||
                      !victimLeaseMatches(
                          entry, *entry.completionLease)),
                 "HnfSLCSF claimed victim has invalid exact lease\n");
    }

    std::unordered_set<uint64_t> installed_ids;
    size_t expected_seals = 0;
    size_t release_claims = 0;
    for (const InflightRequest& request : inflightRequests) {
        if (request.slcVictimId) {
            const VictimEntry* entry = findDirtyVictim(
                *request.slcVictimId);
            panic_if(!entry ||
                         entry->state != VictimState::InstalledSnapshot ||
                         !request.slcVictim ||
                         !installed_ids.insert(
                             request.slcVictimId->value).second ||
                         request.slcVictim->victimId.value !=
                             entry->id.value ||
                         request.slcVictim->lineAddress !=
                             entry->lineAddress ||
                         request.slcVictim->state != entry->snapshot->state ||
                         request.slcVictim->owner != entry->snapshot->owner ||
                         request.slcVictim->line.data !=
                             entry->snapshot->line.data ||
                         request.slcVictim->line.byteMask !=
                             entry->snapshot->line.byteMask,
                     "HnfSLCSF in-flight dirty victim is not exact id=%llu\n",
                     static_cast<unsigned long long>(
                         request.slcVictimId->value));
        } else {
            panic_if(request.slcVictim || request.slcVictimSeal,
                     "HnfSLCSF in-flight victim fields are unpaired\n");
        }
        expected_seals += request.slcVictimSeal.has_value();
        if (request.completionLease &&
            request.completionLease->kind() ==
                SlcSfCompletionKind::ReleaseDirtyVictim) {
            const VictimEntry* entry = findDirtyVictim(SlcSfVictimId{
                request.completionLease->objectId()});
            const bool request_owns_claim = entry &&
                (entry->state == VictimState::ReleaseClaimed ||
                 entry->state ==
                     VictimState::ReleaseCommittedAwaitAck);
            panic_if(!request_owns_claim ||
                         !entry->completionLease ||
                         *entry->completionLease != *request.completionLease,
                     "HnfSLCSF release claim lacks exact in-flight owner\n");
            release_claims +=
                entry->state == VictimState::ReleaseClaimed;
        }
    }
    for (const VictimEntry& entry : victimBuffer) {
        panic_if(entry.state == VictimState::InstalledSnapshot &&
                     !installed_ids.count(entry.id.value),
                 "HnfSLCSF installed victim lacks in-flight owner id=%llu\n",
                 static_cast<unsigned long long>(entry.id.value));
    }
    panic_if(expected_seals != dirtyVictimSealCount(),
             "HnfSLCSF dirty-victim seal ownership mismatch (%u/%u)\n",
             static_cast<unsigned>(expected_seals),
             static_cast<unsigned>(dirtyVictimSealCount()));
    const size_t buffer_release_claims = std::count_if(
        victimBuffer.begin(), victimBuffer.end(), [](const VictimEntry& entry) {
            return entry.state == VictimState::ReleaseClaimed;
        });
    panic_if(release_claims != buffer_release_claims,
             "HnfSLCSF release-claim ownership mismatch (%u/%u)\n",
             static_cast<unsigned>(release_claims),
             static_cast<unsigned>(buffer_release_claims));

    const auto completion_owner_count = [this](
                                             const SlcSfCompletionLease& lease) {
        const auto has = [&lease](const SlcSfResponse& response) {
            const auto* update =
                std::get_if<SlcSfUpdateResponse>(&response.payload());
            return update && update->completionLease &&
                *update->completionLease == lease;
        };
        return std::count_if(
                   inflightRequests.begin(), inflightRequests.end(),
                   [&lease](const InflightRequest& request) {
                       return request.completionLease &&
                           *request.completionLease == lease;
                   }) +
            std::count_if(respPending.begin(), respPending.end(), has) +
            std::count_if(respVisible.begin(), respVisible.end(), has);
    };
    for (const VictimEntry& entry : victimBuffer) {
        if (entry.state == VictimState::ReleaseCommittedAwaitAck) {
            panic_if(!entry.completionLease ||
                         completion_owner_count(*entry.completionLease) != 1,
                     "HnfSLCSF committed victim release lacks one response\n");
        }
    }
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
                    typed_request, std::move(slc_victim),
                    std::move(sf_victim),
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
    if (const auto* complete =
            std::get_if<SlcSfCompleteSfEvict>(&update->operation)) {
        SlcSfCompletionLease lease{
            completionProducer,
            SlcSfCompletionKind::CompleteSfEvict,
            allocatePersistentIdentity(
                nextCompletionNonce, "completion lease nonce"),
            update->header, complete->seqId.value};
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
        allocatePersistentIdentity(
            nextCompletionNonce, "completion lease nonce"),
        update->header, release.victimId.value};
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
    entry->id = SlcSfVictimId{allocatePersistentIdentity(
        nextVictimId, "dirty-victim")};
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
    // CompleteSfEvict is an ownerless durable update, so it deliberately
    // arrives without a lookup token.  Once its exclusion boundary is held
    // (a write set lock for concurrent configurations, or the sole in-flight
    // slot otherwise), capture the exact SLC victim that this completion will
    // use and carry that snapshot through U1/U2 just like a tokened fill.
    if (auto* update = std::get_if<SlcSfUpdateReq>(&request.request)) {
        if (const auto* complete = std::get_if<SlcSfCompleteSfEvict>(
                &update->operation); complete && complete->snoopData.dirty) {
            const LookupSnapshot snapshot = snapshotLookup(
                update->header.lineAddress);
            update->token.lookupEpoch = snapshot.lookupEpoch;
            update->token.lineAddress = update->header.lineAddress;
            update->token.slc = SlcSfArraySnapshot{
                snapshot.slc.hit, snapshot.slc.set, snapshot.slc.way,
                snapshot.slc.generation};
            update->token.sf = SlcSfArraySnapshot{
                snapshot.sf.hit, snapshot.sf.set, snapshot.sf.way,
                snapshot.sf.generation};
        }
    }
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
        [this, &request, &target, &prepare](const auto& typed_request) {
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
                    [this, &request, &target, &prepare](
                        const auto& operation) {
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
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfCompleteSfEvict>) {
                            if (!operation.snoopData.dirty ||
                                findSlc(requestHeader(
                                    request.request).lineAddress)) {
                                return true;
                            }
                            const uint64_t address = requestHeader(
                                request.request).lineAddress;
                            if (!slcAllocationWouldDisplaceDirty(
                                    address, &target)) {
                                return true;
                            }
                            if (auto failure = dirtyVictimReservationFailure(
                                    address, target)) {
                                request.terminalReplayReason = *failure;
                                rollbackPreparedResources(request);
                                return true;
                            }
                            return reserveDirtyVictim(request, target);
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

std::optional<SlcSfStatVictim>
HnfSLCSF::pendingSlcVictimKind(const SlcSfRequest& request) const
{
    bool allocates_slc = false;
    bool uses_commit_target = false;
    std::visit(
        [this, &allocates_slc, &uses_commit_target](
            const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                uses_commit_target = true;
                allocates_slc = std::visit(
                    [this, &typed_request](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead>) {
                            return operation.txn ==
                                PocqTxnKind::ReadShared;
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfFillCleanShared> ||
                                             std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteL3FlushSf>) {
                            return true;
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteLine>) {
                            if (operation.txn ==
                                    PocqTxnKind::WriteBackFull ||
                                operation.txn ==
                                    PocqTxnKind::WriteEvictFull) {
                                const SfLine* tracked = findSf(
                                    typed_request.header.lineAddress);
                                if (tracked &&
                                    (tracked->sharers &
                                     (1ULL << typed_request.header.requester))
                                        == 0) {
                                    return false;
                                }
                            }
                            return true;
                        }
                        return false;
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                if (const auto* complete = std::get_if<
                        SlcSfCompleteSfEvict>(&typed_request.operation)) {
                    allocates_slc = complete->snoopData.dirty;
                }
            }
        },
        request);
    if (!allocates_slc) {
        return std::nullopt;
    }

    const uint64_t address = requestHeader(request).lineAddress;
    if (findSlc(address)) {
        return std::nullopt;
    }
    const SlcLine* victim = nullptr;
    if (uses_commit_target) {
        const LookupSnapshot target = mutationTarget(request);
        panic_if(target.slc.set >= slc.size() ||
                     target.slc.way >= slc[target.slc.set].size(),
                 "HnfSLCSF validated mutation has invalid SLC victim way\n");
        victim = &slc[target.slc.set][target.slc.way];
    } else {
        const auto& set = slc[slcSet(address)];
        victim = &set[selectSlcVictimWay(address)];
    }
    if (!victim || !victim->valid) {
        return std::nullopt;
    }
    const bool dirty = victim->state == HnfSlcState::MU ||
        victim->state == HnfSlcState::MN;
    return dirty ? SlcSfStatVictim::DirtySlc :
                   SlcSfStatVictim::CleanSlc;
}

void
HnfSLCSF::executeMutation(InflightRequest& request)
{
    panic_if(!request.tokenValidated,
             "HnfSLCSF mutation commit bypassed token validation req=%llu\n",
             static_cast<unsigned long long>(
                 requestHeader(request.request).reqId.value));
    const SlcSfReqHeader& header = requestHeader(request.request);
    const LookupSnapshot target = mutationTarget(request.request);
    const std::optional<SlcSfStatVictim> slc_victim_kind =
        pendingSlcVictimKind(request.request);
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
                    [this, &header, &target, &sf_victim, preserved_victim,
                     installed_seal, completion_lease](
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
                                *completion_lease, &target,
                                preserved_victim, installed_seal);
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
    if (slc_victim_kind) {
        ++stats.victims[static_cast<size_t>(*slc_victim_kind)];
        if (statsSink) {
            statsSink->victim(
                *slc_victim_kind, header.requester, target.slc.set,
                slcReplacementPolicyKind());
        }
    }
    if (sf_victim.id != 0) {
        ++stats.victims[static_cast<size_t>(SlcSfStatVictim::Sf)];
        if (statsSink) {
            statsSink->victim(
                SlcSfStatVictim::Sf, header.requester, target.sf.set,
                slcReplacementPolicyKind());
        }
    }
    runBackendGlobalInvariantCheck();
}

bool
HnfSLCSF::tryAcquireSetLock(InflightRequest& request)
{
    if (!config.enableSetLock || request.setLockOwner) {
        return true;
    }
    panic_if(nextSetLockOwner == 0 || nextSetLockOwner == UINT64_MAX,
             "HnfSLCSF set lock owner ID overflows\n");
    const auto& header = requestHeader(request.request);
    const SlcSfSetLockRequest lock_request{
        slcSet(header.lineAddress), sfSet(header.lineAddress),
        requestPipe(request.request) == RequestPipe::Lookup ?
            SlcSfSetLockMode::Read : SlcSfSetLockMode::Write};
    if (!setLocks.tryAcquire(nextSetLockOwner, lock_request)) {
        return false;
    }
    request.setLockOwner = nextSetLockOwner++;
    return true;
}

void
HnfSLCSF::recordConfiguredLatency(
    InflightRequest& request, uint64_t configuredLatency)
{
    panic_if(request.latencyStatsRecorded || configuredLatency == 0,
             "HnfSLCSF records invalid/duplicate configured latency\n");
    request.configuredLatency = configuredLatency;
    request.latencyStatsRecorded = true;
    ++stats.serviceLatencySamples;
    stats.serviceLatencyTotal += configuredLatency;
    if (statsSink) {
        statsSink->issued(configuredLatency);
    }
}

void
HnfSLCSF::advanceMutation(InflightRequest& request)
{
    const MutationStage entered_stage = *request.mutationStage;
    const ProtectedStateSnapshot stage_snapshot = protectedStateSnapshot(
        requestHeader(request.request).lineAddress, true);
    const auto* update = std::get_if<SlcSfUpdateReq>(&request.request);
    const bool needs_rollback_candidate =
        entered_stage == MutationStage::U0DecodeValidate && update &&
        (std::holds_alternative<SlcSfCompleteSfEvict>(update->operation) ||
         std::holds_alternative<SlcSfReleaseDirtyVictim>(
             update->operation));
    const std::optional<ProtectedStateSnapshot> rollback_candidate =
        needs_rollback_candidate ?
            std::optional<ProtectedStateSnapshot>(protectedStateSnapshot(
                requestHeader(request.request).lineAddress, false)) :
            std::nullopt;
    switch (entered_stage) {
      case MutationStage::U0DecodeValidate: {
        request.resourcesPrepared = hasSfReservation(
            requestHeader(request.request).pocEntryId);
        // Invoke the always-on validator for every mutation category. Decode
        // errors retain priority in the terminal status, but validation still
        // remains part of the common U0 boundary.
        const bool token_valid = validateMutationToken(request.request);
        request.tokenValidated = token_valid;
        if (auto error = validateMutationRequest(request.request)) {
            recordConfiguredLatency(request, request.configuredLatency);
            request.terminalResponse = std::visit(
                [&error](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request, std::move(*error));
                },
                request.request);
            request.mutationStage = MutationStage::U3CheckLatch;
        } else if (!token_valid) {
            recordConfiguredLatency(request, 1);
            request.terminalReplayReason =
                SlcSfReplayReason::StaleCommitToken;
            request.mutationStage = MutationStage::U3CheckLatch;
            request.completeCycle = wakeupCycle;
            latchMutationResponse(request);
        } else {
            const uint64_t latency = serviceLatency(request.request);
            panic_if(latency < request.configuredLatency ||
                         latency - request.configuredLatency >
                             UINT64_MAX - request.completeCycle,
                     "HnfSLCSF service completion cycle overflows\n");
            request.completeCycle += latency - request.configuredLatency;
            recordConfiguredLatency(request, latency);
        }
        if (!request.mutationStage) {
            break;
        }
        if (request.terminalResponse || request.terminalReplayReason) {
            break;
        }
        if (!claimDurableCompletion(request)) {
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
            if (request.completionLease) {
                panic_if(!rollback_candidate,
                         "HnfSLCSF durable U0 lacks rollback snapshot\n");
                request.rollbackSnapshot = *rollback_candidate;
            }
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
        } else if (!tryAcquireSetLock(request)) {
            recordServiceStall(true);
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
            if (request.setLockOwner) {
                setLocks.release(*request.setLockOwner);
                request.setLockOwner.reset();
            }
            recordServiceStall();
        }
        break;
      case MutationStage::U2ArrayWrite:
        request.tokenValidated = false;
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
        } else {
#ifdef UNIT_TEST
            std::optional<SlcSfVictimId> injected_original;
            if (pendingVictimIdCorruptionForTest &&
                pendingVictimIdCorruptionForTest->reqId ==
                    requestHeader(request.request).reqId) {
                panic_if(!request.slcVictim,
                         "HnfSLCSF victim-id fault injection lacks victim\n");
                injected_original = request.slcVictim->victimId;
                request.slcVictim->victimId =
                    pendingVictimIdCorruptionForTest->replacement;
                pendingVictimIdCorruptionForTest.reset();
            }
#endif
            const bool victim_write_valid = !request.slcVictim ||
                dirtyVictimWriteCanProceed(
                    requestHeader(request.request).lineAddress,
                    mutationTarget(request.request), *request.slcVictim,
                    *request.slcVictimSeal);
#ifdef UNIT_TEST
            // Restore the test-only transient corruption immediately after
            // the target seal check. The production boundary invariant stays
            // exact while the registered U3 path performs ordinary cleanup.
            if (injected_original) {
                request.slcVictim->victimId = *injected_original;
            }
#endif
            if (!victim_write_valid) {
                request.terminalReplayReason =
                    SlcSfReplayReason::StaleCommitToken;
            } else {
                request.tokenValidated = true;
                executeMutation(request);
            }
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
    // A successful durable claim is the only pre-U2 stage allowed to change
    // fingerprinted state. All terminal, Replay, error, and stall paths must
    // leave the protected arrays/SEQ exactly as they entered the stage.
    const bool successful_u0_claim =
        entered_stage == MutationStage::U0DecodeValidate &&
        request.mutationStage == MutationStage::U1PrepareResources &&
        request.completionLease.has_value();
    const bool cleanup_rolled_back_prior_stage_state =
        entered_stage == MutationStage::U3CheckLatch && request.cleanupDone;
    if (!request.mutationCommitted && !successful_u0_claim &&
        !cleanup_rolled_back_prior_stage_state) {
        panic_if(stage_snapshot != protectedStateSnapshot(
                     requestHeader(request.request).lineAddress, true),
                 "HnfSLCSF non-U2 stage changed protected state req=%llu "
                 "stage=%u\n",
                 static_cast<unsigned long long>(
                     requestHeader(request.request).reqId.value),
                 static_cast<unsigned>(entered_stage));
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
        request, reason, std::move(*request.terminalResponse), wakeupTick);
}

void
HnfSLCSF::finishInflight(
    InflightRequest& request, FinishReason reason,
    SlcSfResponse response, Tick completeTick)
{
    panic_if(request.cleanupDone,
             "HnfSLCSF request=%llu is finalized more than once\n",
             static_cast<unsigned long long>(
                 requestHeader(request.request).reqId.value));
    const auto& header = requestHeader(request.request);
#ifdef UNIT_TEST
    if (pendingVictimIdCorruptionForTest &&
        pendingVictimIdCorruptionForTest->reqId == header.reqId) {
        pendingVictimIdCorruptionForTest.reset();
    }
#endif
    const bool compare_to_rollback = request.rollbackSnapshot.has_value();
    const ProtectedStateSnapshot finish_snapshot = protectedStateSnapshot(
        header.lineAddress, !compare_to_rollback);
    auto trace = activeTraces.find(header.reqId.value);
    panic_if(trace == activeTraces.end() ||
                 !trace->second.acceptedTick ||
                 !trace->second.issueTick ||
                 trace->second.completeTick ||
                 trace->second.visibleTick ||
                 *trace->second.issueTick <= *trace->second.acceptedTick ||
                 completeTick <= *trace->second.issueTick,
             "HnfSLCSF trace tick order must be "
             "accepted < issue < complete req=%llu accepted=%llu "
             "issue=%llu complete=%llu\n",
             static_cast<unsigned long long>(header.reqId.value),
             static_cast<unsigned long long>(
                 trace == activeTraces.end() ||
                     !trace->second.acceptedTick ? 0 :
                     *trace->second.acceptedTick),
             static_cast<unsigned long long>(
                 trace == activeTraces.end() ||
                     !trace->second.issueTick ? 0 :
                     *trace->second.issueTick),
             static_cast<unsigned long long>(completeTick));
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
            request.slcVictimId.reset();
            request.slcVictim.reset();
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
    panic_if(reason != FinishReason::Done &&
                 protectedStateSnapshot(
                     header.lineAddress, !compare_to_rollback) !=
                     request.rollbackSnapshot.value_or(finish_snapshot),
             "HnfSLCSF non-Done terminal mutated persistent state req=%llu "
             "line=%#llx reason=%u\n",
             static_cast<unsigned long long>(header.reqId.value),
             static_cast<unsigned long long>(header.lineAddress),
             static_cast<unsigned>(reason));
    if (reason == FinishReason::Replay) {
        ++correctnessReplays;
    } else if (reason == FinishReason::Cancelled) {
        ++cancelledRequests;
    }
    recordTerminalStats(response);
    trace->second.completeTick = completeTick;
    trace->second.status = response.status();
    if (response.status() == SlcSfTerminalStatus::Replay) {
        trace->second.replayReason =
            std::get<SlcSfReplay>(response.payload()).reason;
        ++terminalReplayTotal;
    } else if (response.status() == SlcSfTerminalStatus::Done) {
        ++terminalDoneTotal;
    } else {
        ++terminalErrorTotal;
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

void
HnfSLCSF::recordTerminalStats(const SlcSfResponse& response)
{
    if (response.status() == SlcSfTerminalStatus::Replay) {
        const auto& replay = std::get<SlcSfReplay>(response.payload());
        const size_t index = replayReasonIndex(replay.reason);
        panic_if(index >= stats.replayReasons.size(),
                 "HnfSLCSF unknown Replay reason\n");
        ++stats.replayReasons[index];
    }

    if (const auto* lookup =
            std::get_if<SlcSfLookupResponse>(&response.payload())) {
        const HnfSlcLookupResult& result = lookup->result;
        const size_t hit_index = result.slcHit ?
            (result.sfHit ?
                 static_cast<size_t>(SlcSfStatHitCombination::SlcSfHit) :
                 static_cast<size_t>(SlcSfStatHitCombination::SlcHit)) :
            (result.sfHit ?
                 static_cast<size_t>(SlcSfStatHitCombination::SfHit) :
                 static_cast<size_t>(SlcSfStatHitCombination::MissMiss));
        ++stats.hitCombinations[hit_index];
        stats.directedSnoops += result.snoopDirected;
        stats.broadcastSnoops += result.snoopBroadcast;
    }

    if (statsSink) {
        statsSink->terminal(response);
    }
}

void
HnfSLCSF::recordServiceStall(bool set_lock_conflict)
{
    ++serviceStalls;
    ++stats.serviceStalls;
    stats.setLockConflicts += set_lock_conflict;
    if (statsSink) {
        statsSink->stalled(set_lock_conflict);
    }
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
        const SlcSfReqId req_id = requestHeader(reqIngress.front()).reqId;
        const auto trace = activeTraces.find(req_id.value);
        panic_if(trace == activeTraces.end() ||
                     !trace->second.acceptedTick,
                 "HnfSLCSF ingress lacks acceptance tick req=%llu\n",
                 static_cast<unsigned long long>(req_id.value));
        if (wakeupTick <= *trace->second.acceptedTick) {
            break;
        }
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
            recordServiceStall();
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
                recordServiceStall(true);
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
        uint64_t latency = baseServiceLatency(*request);
        if (early_lookup_replay) {
            latency = 1;
        }
        panic_if(latency > UINT64_MAX - wakeupCycle,
                 "HnfSLCSF service completion cycle overflows");
        InflightRequest new_inflight(std::move(*request));
        new_inflight.issueCycle = wakeupCycle;
        new_inflight.completeCycle = wakeupCycle + latency;
        new_inflight.configuredLatency = latency;
        new_inflight.mutationStage = mutation ?
            std::optional<MutationStage>(MutationStage::U0DecodeValidate) :
            std::nullopt;
        new_inflight.resourcesPrepared = adopted_reservation;
        new_inflight.earlyLookupReplay = early_lookup_replay;
        new_inflight.setLockOwner = set_lock_owner;
        inflightRequests.push_back(std::move(new_inflight));
        InflightRequest& inflight = inflightRequests.back();
        const auto& header = requestHeader(inflight.request);
        auto trace = activeTraces.find(header.reqId.value);
        panic_if(trace == activeTraces.end() || trace->second.issueTick ||
                     !trace->second.acceptedTick ||
                     wakeupTick <= *trace->second.acceptedTick,
                 "HnfSLCSF duplicate or unknown issue req=%llu\n",
                 static_cast<unsigned long long>(header.reqId.value));
        trace->second.issueTick = wakeupTick;
        DPRINTF(HnfSLCSF,
                "pipeline issue req=%llu pocq=%u line=%#llx op=%u "
                "accepted=%llu issue=%llu completeCycle=%llu\n",
                static_cast<unsigned long long>(header.reqId.value),
                header.pocEntryId,
                static_cast<unsigned long long>(header.lineAddress),
                static_cast<unsigned>(slcSfResponseOperation(
                    inflight.request)),
                static_cast<unsigned long long>(*trace->second.acceptedTick),
                static_cast<unsigned long long>(wakeupTick),
                static_cast<unsigned long long>(wakeupCycle + latency));
        if (!mutation) {
            recordConfiguredLatency(inflight, latency);
        }
        request = reqReady.erase(request);
        ++*issued;
    }
}

void
HnfSLCSF::updateRegisteredCredits(Tick productionTick)
{
    if (!initialized || draining) {
        visibleReqCredits = 0;
        visibleReqCreditTicks.clear();
        return;
    }
    const size_t desired = config.reqQueueEntries - reqOutstanding();
    while (visibleReqCreditTicks.size() > desired) {
        visibleReqCreditTicks.pop_back();
    }
    while (visibleReqCreditTicks.size() < desired) {
        visibleReqCreditTicks.push_back(
            Gem5Internal::_curTickPtr ?
                std::optional<Tick>(productionTick) : std::nullopt);
    }
    visibleReqCredits = visibleReqCreditTicks.size();
}

void
HnfSLCSF::checkLifecycle() const
{
    panic_if(initialized == (initializationCyclesRemaining != 0),
             "HnfSLCSF initialization state/deadline disagree\n");
    panic_if((!initialized || draining) && visibleReqCredits != 0,
             "HnfSLCSF lifecycle gate exposes request credit\n");
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
    panic_if(visibleReqCredits != visibleReqCreditTicks.size(),
             "HnfSLCSF credit timestamp accounting mismatch (%zu/%zu)\n",
             visibleReqCredits, visibleReqCreditTicks.size());
}

void
HnfSLCSF::assertResponseAccounting() const
{
    panic_if(respOccupied() > config.respQueueEntries,
             "HnfSLCSF response accounting exceeds capacity (%zu/%zu)\n",
             respOccupied(), config.respQueueEntries);
    panic_if(respVisible.size() != respVisibleTicks.size(),
             "HnfSLCSF response visibility timestamp mismatch (%zu/%zu)\n",
             respVisible.size(), respVisibleTicks.size());
}

void
HnfSLCSF::runBackendGlobalInvariantCheck()
{
    HnfSLCSFBackend::checkGlobalInvariants();
#ifdef UNIT_TEST
    ++backendGlobalInvariantChecks;
#endif
}

void
HnfSLCSF::assertGlobalInvariants() const
{
    const uint64_t terminal_total = terminalDoneTotal +
        terminalReplayTotal + terminalErrorTotal;
    panic_if(acceptedTotal != terminal_total + reqOutstanding(),
             "HnfSLCSF accepted/terminal accounting mismatch accepted=%llu "
             "terminal=%llu unterminated=%u\n",
             static_cast<unsigned long long>(acceptedTotal),
             static_cast<unsigned long long>(terminal_total),
             static_cast<unsigned>(reqOutstanding()));
    panic_if(activeTraces.size() != reqOutstanding() + respPending.size(),
             "HnfSLCSF trace ownership mismatch active=%u requests=%u "
             "pending=%u\n", static_cast<unsigned>(activeTraces.size()),
             static_cast<unsigned>(reqOutstanding()),
             static_cast<unsigned>(respPending.size()));
    std::unordered_set<uint64_t> lock_owners;
    size_t expected_locks = 0;
    for (const InflightRequest& inflight : inflightRequests) {
        const bool lockless_retry_window = inflight.mutationStage ==
                MutationStage::U1PrepareResources &&
            inflight.mutationStalled;
        panic_if((!config.enableSetLock && inflight.setLockOwner) ||
                     (config.enableSetLock && !lockless_retry_window &&
                      !inflight.setLockOwner) ||
                     (lockless_retry_window && inflight.setLockOwner),
                 "HnfSLCSF request/set-lock reverse ownership mismatch "
                 "req=%llu stage=%d stalled=%u\n",
                 static_cast<unsigned long long>(
                     requestHeader(inflight.request).reqId.value),
                 inflight.mutationStage ?
                     static_cast<int>(*inflight.mutationStage) : -1,
                 inflight.mutationStalled);
        if (!inflight.setLockOwner) {
            continue;
        }
        panic_if(!lock_owners.insert(*inflight.setLockOwner).second,
                 "HnfSLCSF duplicate in-flight set-lock owner=%llu\n",
                 static_cast<unsigned long long>(*inflight.setLockOwner));
        const auto& header = requestHeader(inflight.request);
        const SlcSfSetLockRequest expected{
            slcSet(header.lineAddress), sfSet(header.lineAddress),
            requestPipe(inflight.request) == RequestPipe::Lookup ?
                SlcSfSetLockMode::Read : SlcSfSetLockMode::Write};
        panic_if(!setLocks.holdsExact(*inflight.setLockOwner, expected),
                 "HnfSLCSF in-flight request has inexact set-lock ownership "
                 "owner=%llu\n",
                 static_cast<unsigned long long>(*inflight.setLockOwner));
        expected_locks += expected.slcSet.has_value() +
            expected.sfSet.has_value();
    }
    panic_if(setLockCount() != expected_locks,
             "HnfSLCSF set-lock ownership mismatch actual=%u expected=%u\n",
             static_cast<unsigned>(setLockCount()),
             static_cast<unsigned>(expected_locks));

    std::unordered_set<uint64_t> owned_ids;
    const auto record_request = [this, &owned_ids](
                                    const SlcSfRequest& request,
                                    bool issued) {
        const auto& header = requestHeader(request);
        const auto trace = activeTraces.find(header.reqId.value);
        panic_if(!owned_ids.insert(header.reqId.value).second ||
                     !wasAccepted(header.reqId) ||
                     trace == activeTraces.end() ||
                     !trace->second.acceptedTick ||
                     (issued &&
                      (!trace->second.issueTick ||
                       *trace->second.issueTick <=
                           *trace->second.acceptedTick)) ||
                     (!issued && trace->second.issueTick) ||
                     trace->second.completeTick ||
                     trace->second.visibleTick,
                 "HnfSLCSF invalid active request ownership ID=%llu\n",
                 static_cast<unsigned long long>(header.reqId.value));
    };
    for (const SlcSfRequest& request : reqIngress) {
        record_request(request, false);
    }
    for (const SlcSfRequest& request : reqReady) {
        record_request(request, false);
    }
    for (const InflightRequest& request : inflightRequests) {
        record_request(request.request, true);
    }

    const auto record_response =
        [this, &owned_ids](const SlcSfResponse& response, bool pending) {
        panic_if(!owned_ids.insert(response.reqId().value).second,
                 "HnfSLCSF duplicate response ID=%llu\n",
                 static_cast<unsigned long long>(response.reqId().value));
        const auto trace = activeTraces.find(response.reqId().value);
        panic_if(!wasAccepted(response.reqId()) ||
                     (pending &&
                      (trace == activeTraces.end() ||
                       !trace->second.acceptedTick ||
                       !trace->second.issueTick ||
                       !trace->second.completeTick ||
                       trace->second.visibleTick ||
                       *trace->second.issueTick <=
                           *trace->second.acceptedTick ||
                       *trace->second.completeTick <=
                           *trace->second.issueTick ||
                       trace->second.status != response.status())) ||
                     (!pending && trace != activeTraces.end()),
                 "HnfSLCSF response/trace ownership mismatch ID=%llu\n",
                 static_cast<unsigned long long>(response.reqId().value));
    };
    for (const SlcSfResponse& response : respPending) {
        record_response(response, true);
    }
    for (const SlcSfResponse& response : respVisible) {
        record_response(response, false);
    }
    if (completedTrace) {
        panic_if(!completedTrace->acceptedTick ||
                     !completedTrace->issueTick ||
                     !completedTrace->completeTick ||
                     !completedTrace->visibleTick ||
                     *completedTrace->issueTick <=
                         *completedTrace->acceptedTick ||
                     *completedTrace->completeTick <=
                         *completedTrace->issueTick ||
                     *completedTrace->visibleTick <=
                         *completedTrace->completeTick,
                 "HnfSLCSF completed trace tick order must be "
                 "accepted < issue < complete < visible\n");
    }
}

} // namespace gem5::Chi

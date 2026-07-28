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
                return RequestPipe::Fill;
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

} // anonymous namespace

HnfSLCSF::HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways, uint32_t seq_entries,
                   HnfSLCSFPipelineConfig pipeline_config)
    : HnfSLCSFBackend(block_size, slc_num_sets, slc_num_ways,
                      sf_num_sets, sf_num_ways, seq_entries),
      config(pipeline_config), visibleReqCredits(config.reqQueueEntries)
{
    validateConfig(config);
    assertRequestAccounting();
    assertResponseAccounting();
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
        config.responseConsumeWidth == 0) {
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

SlcSfEnqueueResult
HnfSLCSF::tryEnqueue(SlcSfRequest&& request)
{
    assertRequestAccounting();
    if (draining) {
        return SlcSfEnqueueResult::Draining;
    }
    if (!initialized) {
        return SlcSfEnqueueResult::Initializing;
    }
    if (visibleReqCredits == 0 ||
        reqOutstanding() == config.reqQueueEntries) {
        return SlcSfEnqueueResult::NoCredit;
    }

    reqIngress.push_back(std::move(request));
    --visibleReqCredits;
    assertRequestAccounting();
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
    promotePendingResponses();
    completeInflightRequests();
    promoteIngressRequests();
    issueReadyRequests();
    updateRegisteredCredits();
    assertRequestAccounting();
    assertResponseAccounting();
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

std::optional<SlcSfResponse>
HnfSLCSF::popVisibleResponse()
{
    assertResponseAccounting();
    if (respVisible.empty()) {
        return std::nullopt;
    }

    SlcSfResponse response = std::move(respVisible.front());
    respVisible.pop_front();
    assertResponseAccounting();
    return response;
}

bool
HnfSLCSF::hasWork() const
{
    return reqOutstanding() != 0 || respOccupied() != 0;
}

void
HnfSLCSF::beginInitialization()
{
    initialized = false;
    visibleReqCredits = 0;
}

void
HnfSLCSF::finishInitialization()
{
    initialized = true;
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
        if (request->completeCycle > wakeupCycle) {
            ++request;
            continue;
        }

        if (!request->mutationStage) {
            respPending.push_back(makeTerminalResponse(request->request));
            request = inflightRequests.erase(request);
            continue;
        }

        advanceMutation(*request);
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

SlcSfResponse
HnfSLCSF::makeTerminalResponse(const SlcSfRequest& request)
{
    return std::visit(
        [this](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                HnfSLCSFBackend::LookupSnapshot snapshot{};
                HnfSlcLookupResult result = HnfSLCSFBackend::lookup(
                    makeBackendLookupRequest(typed_request), &snapshot);
                if (result.replay) {
                    panic_if(wakeupTick == MaxTick,
                             "HnfSLCSF cannot schedule a retry after MaxTick");
                    return makeSlcSfReplayResponse(
                        typed_request,
                        SlcSfReplay{
                            SlcSfReplayReason::SeqConflict,
                            wakeupTick + 1, true});
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
            } else {
                return makeSlcSfDoneResponse(typed_request);
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
        [](const auto& typed_request) -> std::optional<SlcSfError> {
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
                        } else {
                            return false;
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                supported = std::visit(
                    [](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            return isMaintenanceTxn(operation.txn);
                        } else {
                            return std::is_same_v<Operation,
                                                  SlcSfRemoveSharer>;
                        }
                    },
                    typed_request.operation);
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
HnfSLCSF::prepareMutationResources(InflightRequest& request)
{
    const auto prepare = [this, &request](PocqTxnKind txn) {
        const auto& header = requestHeader(request.request);
        if (!tryReserveSfResources(
                header.pocEntryId, header.lineAddress, txn)) {
            return false;
        }
        request.resourcesPrepared = hasSfReservation(header.pocEntryId);
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
                            return prepare(operation.txn);
                        } else {
                            return prepare(PocqTxnKind::ReadShared);
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
                            return prepare(operation.txn);
                        } else {
                            return true;
                        }
                    },
                    typed_request.operation);
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
    std::visit(
        [this, &header](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                std::visit(
                    [this, &header](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation, SlcSfCommitRead>) {
                            commitRead(
                                header.lineAddress, header.requester,
                                operation.txn, operation.line.data,
                                operation.line.dirty,
                                operation.homeNodeId);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfFillCleanShared>) {
                            fillCleanShared(
                                header.lineAddress, header.requester,
                                operation.line.data);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfWriteLine>) {
                            writeLine(
                                header.lineAddress, header.requester,
                                operation.line.data, operation.txn,
                                operation.homeNodeId);
                        }
                    },
                    typed_request.operation);
            } else if constexpr (std::is_same_v<Request, SlcSfUpdateReq>) {
                std::visit(
                    [this, &header](const auto& operation) {
                        using Operation =
                            std::decay_t<decltype(operation)>;
                        if constexpr (std::is_same_v<
                                          Operation,
                                          SlcSfCompleteMaintenance>) {
                            completeMaintenance(
                                header.lineAddress, header.requester,
                                operation.txn, operation.homeNodeId);
                        } else if constexpr (std::is_same_v<
                                                 Operation,
                                                 SlcSfRemoveSharer>) {
                            removeSharer(
                                header.lineAddress, header.requester);
                        }
                    },
                    typed_request.operation);
            }
        },
        request.request);
    request.mutationCommitted = true;
    if (request.resourcesPrepared) {
        releaseSfResources(header.pocEntryId);
        request.resourcesPrepared = false;
    }
}

void
HnfSLCSF::advanceMutation(InflightRequest& request)
{
    switch (*request.mutationStage) {
      case MutationStage::U0DecodeValidate:
        if (auto error = validateMutationRequest(request.request)) {
            request.terminalResponse = std::visit(
                [&error](const auto& typed_request) {
                    return makeSlcSfErrorResponse(
                        typed_request, std::move(*error));
                },
                request.request);
            request.mutationStage = MutationStage::U3CheckLatch;
        } else {
            request.mutationStage = MutationStage::U1PrepareResources;
        }
        break;
      case MutationStage::U1PrepareResources:
        if (prepareMutationResources(request)) {
            request.mutationStage = MutationStage::U2ArrayWrite;
        }
        break;
      case MutationStage::U2ArrayWrite:
        executeMutation(request);
        request.mutationStage = MutationStage::U3CheckLatch;
        break;
      case MutationStage::U3CheckLatch:
        if (request.mutationCommitted) {
            checkLineInvariant(requestHeader(request.request).lineAddress);
        }
        if (!request.terminalResponse) {
            request.terminalResponse = makeTerminalResponse(request.request);
        }
        respPending.push_back(std::move(*request.terminalResponse));
        request.mutationStage.reset();
        break;
    }
    request.completeCycle = wakeupCycle + 1;
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

    for (auto request = reqReady.begin();
         request != reqReady.end() &&
         inflightRequests.size() < config.maxInflight &&
         respOccupied() < config.respQueueEntries;) {
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

        if (*issued == width) {
            ++request;
            continue;
        }

        // The in-flight entry is the reservation.  Capacity is checked before
        // emplacing it, and only a successfully emplaced request is erased
        // from ready, so issue and reservation are one state transition.
        const bool mutation = pipe != RequestPipe::Lookup;
        const uint64_t latency = mutation ? 1 :
            std::max<uint64_t>(1, config.lookupLatency);
        inflightRequests.push_back(InflightRequest{
            std::move(*request), wakeupCycle, wakeupCycle + latency,
            mutation ? std::optional<MutationStage>(
                           MutationStage::U0DecodeValidate) : std::nullopt,
            std::nullopt, false, false});
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

#include "dev/ai_mesh/agent_axi_driver.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_plan_codec.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

std::vector<uint8_t>
AgentAxiDriver::encodePlanSq(uint64_t sequence, uint64_t requestId,
                             uint64_t cookie,
                             const AgentSubmissionIntent &intent,
                             uint32_t parameterBlockBytes) const
{
    agent_abi::SqDescriptor value;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.opcode = static_cast<uint16_t>(agent_abi::SqOpcode::GENERATE);
    value.sq_seq = sequence;
    value.request_id = requestId;
    value.parameter_block_addr = intent.parameterAddress;
    value.parameter_block_bytes = parameterBlockBytes;
    value.completion_cookie = cookie;
    if (intent.commandKind == kAgentCommandGenerate) {
        value.flags = kvPolicyToSqFlags(intent.kvPolicy);
        value.session_id = intent.sessionId;
        value.program_id = intent.programId;
        value.profile_id = intent.profileId;
        value.qos = intent.qos;
    } else {
        value.opcode = intent.commandKind == kAgentCommandCancel ?
            agent_abi::kSqOpcodeCANCEL : agent_abi::kSqOpcodeRELEASE_SESSION;
        value.flags = 0;
        value.session_id =
            intent.commandKind == kAgentCommandReleaseSession ?
            intent.controlSessionId : 0;
        value.program_id = 0;
        value.profile_id = 0;
        value.qos = 0;
    }
    value.flags2 = 0;
    value.reserved = 0;
    return toVector(agent_abi::encodeSqDescriptor(value));
}
void
AgentAxiDriver::preparePlanRecords(const AgentSubmissionIntent &intent,
                                   uint64_t sqSequence, uint64_t requestId,
                                   uint64_t cookie)
{
    if (intent.commandKind != kAgentCommandGenerate) {
        recordSemantic("CONTROL_SUBMIT", "CONTROL_WAITER", std::nullopt,
                       requestId, cookie);
        if (intent.commandKind == kAgentCommandCancel &&
                acceptedGenerateRequests.count(intent.targetRequestId) != 0 &&
                consumedGenerateRequests.count(intent.targetRequestId) == 0) {
            if (cancelJoins.size() >= cancelJoinCapacity ||
                    findCancelJoinByTarget(intent.targetRequestId) ||
                    cancelJoins.count(intent.requestId)) {
                requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
                return;
            }
            CancelJoinRecord join;
            join.targetRequestId = intent.targetRequestId;
            join.commandRequestId = intent.requestId;
            join.targetCookie = intent.targetRequestId;
            join.commandCookie = intent.completionCookie;
            cancelJoins.emplace(intent.requestId, join);
        } else {
            standaloneControlRequests.insert(intent.requestId);
        }
        const std::vector<uint8_t> parameterBlob = buildControlParameter(
            intent.commandKind, intent.targetRequestId, intent.controlKvHandle,
            intent.controlGeneration);
        submitParameterBlockBytes = parameterBlob.size();
        const std::vector<uint8_t> sqBlob =
            encodePlanSq(sqSequence, requestId, cookie, intent,
                         submitParameterBlockBytes);
        storeSubmissionContext(SubmissionContext{
            SqSeq(sqSequence), RequestId(requestId),
            CompletionCookie(cookie),
            intent.commandKind == kAgentCommandReleaseSession ?
                intent.controlSessionId : 0,
            intent.userId, intent.taskSequence, 0, 0, 0}, intent);
        storeBytes(sqAddress(sqSequence), sqBlob);
        storeBytes(intent.parameterAddress, parameterBlob);
        recordSemantic("LOCAL_VISIBLE", "PARAMETER", std::nullopt, requestId,
                       cookie);
        recordSemantic("LOCAL_VISIBLE", "SQ_ENTRY", sqSequence, requestId,
                       cookie);
        submitLocalReadyTick =
            curTick() + localVisibilityDelay * clockPeriod();
        return;
    }
    const std::vector<uint8_t> parameterBlob = buildPlanParameter(
        *intent.round, *intent.task, intent.userId,
        PlanWireAddresses{
            intent.inputAddress, intent.parameterAddress, intent.outputAddress,
            intent.outputMetadataAddress},
        intent.workloadDigest.data(), intent.publishChunkBytes);
    submitParameterBlockBytes = parameterBlob.size();
    const std::vector<uint8_t> sqBlob =
        encodePlanSq(sqSequence, requestId, cookie, intent,
                     submitParameterBlockBytes);
    storeSubmissionContext(SubmissionContext{
        SqSeq(sqSequence), RequestId(requestId), CompletionCookie(cookie),
        intent.sessionId, intent.userId, intent.taskSequence,
        intent.repairRound, intent.outputMetadataAddress,
        intent.outputMetadataCapacityBytes}, intent);
    storeBytes(sqAddress(sqSequence), sqBlob);
    storeBytes(intent.parameterAddress, parameterBlob);
    storeBytes(intent.inputAddress,
               agentInputSurrogateBytes(intent.inputDigest.data(),
                                        intent.inputBytes));
    recordSemantic("LOCAL_VISIBLE", "PROMPT", std::nullopt, requestId, cookie);
    recordSemantic("LOCAL_VISIBLE", "PARAMETER", std::nullopt, requestId,
                   cookie);
    recordSemantic("LOCAL_VISIBLE", "SQ_ENTRY", sqSequence, requestId,
                   cookie);
    submitLocalReadyTick = curTick() + localVisibilityDelay * clockPeriod();
}
void
AgentAxiDriver::prepareLocalRecords()
{
    const std::vector<uint8_t> parameterBlob =
        encodeParameter(currentRequestId);
    currentParameterBlockBytes = parameterBlob.size();
    if (mode("PARAMETER_ENVELOPE_LONG"))
        currentParameterBlockBytes += dataBusBytes;
    else if (mode("PARAMETER_ENVELOPE_SHORT"))
        currentParameterBlockBytes -= dataBusBytes;
    const auto parameter =
        agent_abi::decodeParameterHeader(parameterBlob.data());
    const auto sqBlob =
        encodeSq(currentSqSequence, currentRequestId, currentCookie);
    const auto sq = agent_abi::decodeSqDescriptor(sqBlob.data());
    storeSubmissionContext(SubmissionContext{
        SqSeq(currentSqSequence), RequestId(currentRequestId),
        CompletionCookie(currentCookie), sq.session_id, parameter.user_id,
        parameter.task_seq, parameter.repair_round,
        parameter.output_metadata_addr,
        parameter.output_metadata_capacity_bytes}, currentIntent);
    storeBytes(sqAddress(currentSqSequence), sqBlob);
    storeBytes(parameterAddress(currentSqSequence), parameterBlob);
    storeBytes(promptAddress(currentSqSequence),
               makePayload(0x20 + currentSqSequence, dataBusBytes));
    recordSemantic("LOCAL_VISIBLE", "PROMPT", std::nullopt,
                   currentRequestId, currentCookie);
    recordSemantic("LOCAL_VISIBLE", "PARAMETER", std::nullopt,
                   currentRequestId, currentCookie);
    recordSemantic("LOCAL_VISIBLE", "SQ_ENTRY", currentSqSequence,
                   currentRequestId, currentCookie);
    localReadyTick = curTick() + localVisibilityDelay * clockPeriod();
}
void
AgentAxiDriver::storeSubmissionContext(const SubmissionContext &context,
                                       const AgentSubmissionIntent &intent)
{
    const uint64_t slot = context.sqSequence.value();
    if (slot < submissionContexts.size())
        submissionContexts[slot] = context;
    else
        submissionContexts.push_back(context);
    if (!intent.planDriven)
        return;
    if (slot < planIntents.size())
        planIntents[slot] = intent;
    else
        planIntents.push_back(intent);
}

void
AgentAxiDriver::noteControlLocalSubmitFailed(uint64_t requestId)
{
    const auto dissolved = cancelJoins.find(requestId);
    if (dissolved != cancelJoins.end()) {
        const CancelJoinRecord record = dissolved->second;
        cancelJoins.erase(dissolved);
        if (record.targetCqSeen)
            advanceGenerateBusiness(record.targetRequestId,
                                    record.targetStatus);
    }
    standaloneControlRequests.erase(requestId);
    const auto index = controlIndexByRequest.find(requestId);
    if (index != controlIndexByRequest.end() && coordinator)
        coordinator->markLocalSubmitFailed(index->second);
    recordSemantic("CONTROL_TERMINAL", "CONTROL_WAITER", std::nullopt,
                   requestId, std::nullopt, "LOCAL_SUBMIT_FAILED");
}

void
AgentAxiDriver::scheduleControlDeliveryWake()
{
    if (!coordinator)
        return;
    scheduleManagerWake(coordinator->nextDueEdge());
}

void
AgentAxiDriver::notifySqAccepted(uint64_t requestId)
{
    ControlAnchor anchor;
    if (coordinator && workloadManager &&
            workloadManager->anchorForRequest(requestId, anchor.userId,
                                              anchor.taskSeq,
                                              anchor.repairRound)) {
        coordinator->onAuthoritativeEvent(
            ControlTriggerEvent::AfterSqAccept, anchor, 0, curTick());
        scheduleTick();
        scheduleControlDeliveryWake();
    }
}
void
AgentAxiDriver::notifyFirstOutputChunk(uint64_t requestId)
{
    ControlAnchor anchor;
    if (coordinator && workloadManager &&
            workloadManager->anchorForRequest(requestId, anchor.userId,
                                              anchor.taskSeq,
                                              anchor.repairRound)) {
        coordinator->onAuthoritativeEvent(
            ControlTriggerEvent::AfterFirstOutputChunk, anchor, 0, curTick());
        scheduleTick();
        scheduleControlDeliveryWake();
    }
}
void
AgentAxiDriver::notifyGenerateTerminalVisible(uint64_t requestId)
{
    ControlAnchor anchor;
    if (!coordinator || !workloadManager ||
            !workloadManager->anchorForRequest(requestId, anchor.userId,
                                               anchor.taskSeq,
                                               anchor.repairRound))
        return;
    coordinator->onAuthoritativeEvent(
        ControlTriggerEvent::AfterGenerateTerminal, anchor, 0, curTick());
    coordinator->onAuthoritativeEvent(
        ControlTriggerEvent::SameEdgeAsTerminal, anchor, 0, curTick());
    scheduleControlDeliveryWake();
}
void
AgentAxiDriver::notifyReleaseTerminal(uint64_t requestId)
{
    ControlAnchor anchor;
    if (coordinator && workloadManager &&
            workloadManager->anchorForRequest(requestId, anchor.userId,
                                              anchor.taskSeq,
                                              anchor.repairRound)) {
        coordinator->onAuthoritativeEvent(
            ControlTriggerEvent::AfterReleaseTerminal, anchor, 0, curTick());
        scheduleTick();
        scheduleControlDeliveryWake();
    }
}
void
AgentAxiDriver::pumpControlDelivery()
{
    if (!coordinator || !workloadManager || recorder->fatalPending() ||
            recorder->fatalRecorded())
        return;
    if (clockEdge() == curTick()) {
        for (const size_t index : coordinator->due(curTick())) {
            if (!coordinator->markMaterialized(index))
                continue;
            workloadManager->materializeControlAction(index);
        }
    } else if (!coordinator->due(curTick()).empty()) {
        scheduleControlDeliveryWake();
        return;
    }
    if (!workloadManager->controlIntentsPending())
        return;
    if (submitPhase == SubmitPhase::Idle && !activeWrite && !activeRead)
        beginControlSubmission();
}

void
AgentAxiDriver::suppressUnreachableControls()
{
    if (!coordinator || !workloadManager)
        return;
    std::vector<size_t> indexes;
    for (size_t index = 0; index < coordinator->size(); ++index)
        if (workloadManager->controlAnchorSuppressed(index))
            indexes.push_back(index);
    std::sort(indexes.begin(), indexes.end(),
              [this](size_t left, size_t right) {
                  return coordinator->action(left).controlOrdinal <
                      coordinator->action(right).controlOrdinal;
              });
    for (const size_t index : indexes) {
        if (!coordinator->markSuppressedByRunCutoff(index))
            continue;
        const uint64_t requestId = index < controlRequestByIndex.size() ?
            controlRequestByIndex[index] : 0;
        recordSemantic("CONTROL_TERMINAL", "CONTROL_WAITER", std::nullopt,
                       requestId, std::nullopt, "SUPPRESSED_BY_RUN_CUTOFF");
    }
}

void
AgentAxiDriver::beginControlSubmission()
{
    auto intent = workloadManager->takeControlIntent();
    fatal_if(!intent, "%s: control submission has no materialized intent",
             name());
    const auto sequence = ledger.reserve(RequestId(intent->requestId));
    if (!sequence) {
        requestFatal(agent_abi::E_REQUEST_CONTEXT_FULL);
        return;
    }
    submitSqSequence = sequence->value();
    submitRequestId = intent->requestId;
    submitCookie = intent->completionCookie;
    issuedRequestIds.insert(submitRequestId);
    submitIntent = *intent;
    submitDoorbellTail = submitSqSequence + 1;
    ++submissionAttempts;
    const auto index = controlIndexByRequest.find(submitRequestId);
    if (controlDoorbellErrorOrdinal != 0 && coordinator &&
            index != controlIndexByRequest.end() &&
            coordinator->action(index->second).controlOrdinal ==
                controlDoorbellErrorOrdinal)
        controlDoorbellFaultTail = submitDoorbellTail;
    preparePlanRecords(submitIntent, submitSqSequence, submitRequestId,
                       submitCookie);
    submitPhase = SubmitPhase::Fence;
    scheduleTick();
}
bool
AgentAxiDriver::controlCqValid(const agent_abi::CqDescriptor &value) const
{
    if ((value.flags & agent_abi::kCqFlagsMETADATA_VALID) != 0 ||
            (value.flags & agent_abi::kCqFlagsCONTROL_COMMAND) == 0 ||
            value.request_id == 0 || value.completion_cookie != value.request_id)
        return false;
    if (currentSqSequence >= planIntents.size())
        return false;
    const uint8_t kind = planIntents.at(currentSqSequence).commandKind;
    const uint16_t status = value.status;
    const bool releaseAllowed =
        status == agent_abi::kCqStatusSUCCESS ||
        status == agent_abi::kCqStatusNOT_FOUND ||
        status == agent_abi::kCqStatusALREADY_TERMINAL ||
        status == agent_abi::kCqStatusSTALE_GENERATION;
    const bool cancelAllowed =
        status == agent_abi::kCqStatusSUCCESS ||
        status == agent_abi::kCqStatusNOT_FOUND ||
        status == agent_abi::kCqStatusALREADY_TERMINAL ||
        status == agent_abi::kCqStatusPARAM_ERROR;
    return kind == kAgentCommandCancel ? cancelAllowed : releaseAllowed;
}
void
AgentAxiDriver::noteControlCommandCompletion(uint16_t status)
{
    CancelJoinRecord *joinByCommand =
        findCancelJoinByCommand(currentRequestId);
    if (joinByCommand) {
        joinByCommand->commandStatus = status;
        joinByCommand->commandCqSeen = true;
    }
    if (standaloneControlRequests.erase(currentRequestId) > 0)
        recordSemantic("CONTROL_TERMINAL", "CONTROL_WAITER", std::nullopt,
                       currentRequestId, std::nullopt,
                       controlStatusName(status));
    const auto index = controlIndexByRequest.find(currentRequestId);
    if (index != controlIndexByRequest.end())
        coordinator->markControlTerminal(index->second);
}
void
AgentAxiDriver::advanceGenerateBusiness(uint64_t requestId, uint16_t status)
{
    if (workloadManager == nullptr || requestId == 0)
        return;
    consumedGenerateRequests.insert(requestId);
    workloadManager->scheduleGenerateTerminal(
        requestId, isSuccessStatus(status), clockEdgeAtOrAfter(curTick() + 1));
}
void
AgentAxiDriver::resolveCancelJoin()
{
    for (auto entry = cancelJoins.begin();
         entry != cancelJoins.end();) {
        const CancelJoinRecord &record = entry->second;
        if (!record.targetCqSeen || !record.commandCqSeen) {
            ++entry;
            continue;
        }
        const char *winner = cancelJoinWinner(record.commandStatus,
                                              record.targetStatus);
        if (winner == nullptr) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
        recordSemantic("CANCEL_JOIN_RESOLVED", "CONTROL_WAITER", std::nullopt,
                       record.commandRequestId, std::nullopt, winner);
        const CancelJoinRecord resolved = record;
        entry = cancelJoins.erase(entry);
        advanceGenerateBusiness(resolved.targetRequestId,
                                 resolved.targetStatus);
    }
}
Gate3MetadataExpectation
AgentAxiDriver::metadataExpectation() const
{
    const SubmissionContext &context =
        submissionContexts.at(currentSqSequence);
    if (currentSqSequence < planIntents.size()) {
        const AgentSubmissionIntent &intent =
            planIntents.at(currentSqSequence);
        const std::array<uint8_t, 32> seed = surrogateSeed(
            intent.inputDigest.data(), intent.programId, intent.profileId,
            intent.profileKey);
        std::vector<std::array<uint8_t, 32>> tokenDigests;
        tokenDigests.reserve(intent.maxOutputTokens);
        for (uint32_t ordinal = 0; ordinal < intent.maxOutputTokens;
             ++ordinal)
            tokenDigests.push_back(
                surrogateTokenDigest(seed.data(), ordinal));
        Gate3MetadataExpectation expectation{
            currentRequestId, context.sessionId, context.userId,
            context.taskSequence, context.repairRound, observedCqStatus,
            observedCqFlags, observedCqValue, context.metadataCapacity};
        expectation.metadataFlags =
            intent.kvPolicy == kAgentKvPolicyInitial ||
                    intent.kvPolicy == kAgentKvPolicyAllowReprefill ?
            agent_abi::kMetadataFlagsSESSION_ADMITTED : 0;
        expectation.checkSurrogate = true;
        expectation.outputTokens = intent.maxOutputTokens;
        expectation.completedInstanceCount = 0;
        expectation.semanticDigest = surrogateOutputPrefixDigest(
            intent.workloadDigest.data(), intent.workloadPlanItemId,
            intent.maxOutputTokens, tokenDigests);
        return expectation;
    }
    return Gate3MetadataExpectation{
        currentRequestId, context.sessionId, context.userId,
        context.taskSequence, context.repairRound, observedCqStatus,
        observedCqFlags, observedCqValue, context.metadataCapacity};
}

void
AgentAxiDriver::planPump()
{
    planAdvanceCompletion();
    planAdvanceSubmission();
    pumpControlDelivery();
    if ((stage == Stage::Drain || stage == Stage::Done) &&
        completePhase == CompletePhase::Idle) {
        finishIfDrained();
        return;
    }
    if (submitPhase == SubmitPhase::DoorbellResponse ||
        completePhase == CompletePhase::AckResponse)
        return;
    if (submitPhase == SubmitPhase::Idle &&
        completePhase == CompletePhase::Idle &&
        stage != Stage::Drain && stage != Stage::Done &&
        !recorder->hasEvent("IRQ_DELIVER", "MSI", cqConsumer + 1))
        return;
    scheduleTick();
}

void
AgentAxiDriver::planAdvanceCompletion()
{
    if (completePhase != CompletePhase::Idle) {
        switch (completePhase) {
          case CompletePhase::AckResponse:
            consumeB();
            return;
          case CompletePhase::Ack:
            if (activeWrite && activeWrite->control != "CQ_HEAD_ACK")
                return;
            driveWrite();
            return;
          case CompletePhase::AckWait:
            if (activeWrite)
                return;
            startAck();
            driveWrite();
            return;
          case CompletePhase::Metadata:
            startMetadataRead();
            return;
          case CompletePhase::CqRead:
            startCqRead();
            return;
          case CompletePhase::Idle:
            break;
        }
        return;
    }
    if (stage == Stage::Drain || stage == Stage::Done)
        return;
    if (!recorder->hasEvent("IRQ_DELIVER", "MSI", cqConsumer + 1))
        return;
    currentCqSequence = cqConsumer;
    if (!cqReadDelayPending) {
        cqReadDelayPending = true;
        cqReadReadyTick = curTick() + cqReadDelayTicks;
    }
    completePhase = CompletePhase::CqRead;
    startCqRead();
}

void
AgentAxiDriver::planAdvanceSubmission()
{
    if (stage == Stage::Drain || stage == Stage::Done)
        return;
    switch (submitPhase) {
      case SubmitPhase::DoorbellResponse:
        consumeB();
        return;
      case SubmitPhase::Doorbell:
        planIssueDoorbell();
        return;
      case SubmitPhase::Fence:
        if (curTick() < submitLocalReadyTick) {
            if (!tickEvent.scheduled())
                schedule(&tickEvent, submitLocalReadyTick);
            return;
        }
        recordSemantic("RELEASE_FENCE_DONE", "CONTEXT", std::nullopt,
                       submitRequestId, submitCookie);
        submitPhase = SubmitPhase::Doorbell;
        planIssueDoorbell();
        return;
      case SubmitPhase::Idle:
        break;
    }
    if (activeWrite || activeRead || ledger.hasPending())
        return;
    if (runtimeExhausted(completedRequests)) {
        stage = Stage::Drain;
        if (drainUntilTick == 0)
            drainUntilTick = curTick() + drainCycles * clockPeriod();
        return;
    }
    if (ledger.available() == 0)
        return;
    const std::vector<AgentSubmissionIntent> intents =
        requestSource->nextBatch(issuedRequestIds, submissionAttempts,
                                 sqDepth);
    if (intents.empty()) {
        fatal_if(!workloadManager,
                 "%s: request source produced no submission intents", name());
        scheduleManagerWake(workloadManager->nextWakeTick());
        if (!tickEvent.scheduled() && !managerWakeEvent.scheduled())
            scheduleTick();
        return;
    }
    fatal_if(intents.size() > 1,
             "%s: plan submission batch must hold one intent", name());
    submitRequestId = intents.front().requestId;
    submitCookie = intents.front().completionCookie;
    const auto sequence = ledger.reserve(RequestId(submitRequestId));
    if (!sequence) {
        requestFatal(agent_abi::E_REQUEST_CONTEXT_FULL);
        return;
    }
    issuedRequestIds.insert(submitRequestId);
    submitSqSequence = sequence->value();
    submitDoorbellTail = submitSqSequence + 1;
    ++submissionAttempts;
    submitIntent = intents.front();
    preparePlanRecords(submitIntent, submitSqSequence, submitRequestId,
                       submitCookie);
    submitPhase = SubmitPhase::Fence;
    scheduleTick();
}

void
AgentAxiDriver::planIssueDoorbell()
{
    if (!activeWrite) {
        activeWrite = makeWrite(
            "DOORBELL", "SQ_DOORBELL", submitDoorbellTail, submitRequestId,
            submitCookie, rangeEnd(npuControlBase, NpuDoorbellOffset),
            doorbellAxiId, encodeControl(submitDoorbellTail));
        ++doorbellAttempts;
        if (controlDoorbellBHoldTicks != 0 && submitIntent.planDriven &&
                submitIntent.commandKind != kAgentCommandGenerate)
            controlDoorbellBHeldUntil = curTick() + controlDoorbellBHoldTicks;
    }
    driveWrite();
}

}
}

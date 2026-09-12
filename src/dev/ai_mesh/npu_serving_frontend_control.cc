#include "dev/ai_mesh/npu_serving_frontend.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_axi_driver.hh"
#include "sim/core.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

void
NpuServingFrontend::commitCurrentSq()
{
    fatal_if(!sqCommitPending || !currentSqIntakeId,
             "%s: SQ normal commit has no live proposal", name());
    if (!currentObligationId) {
        currentObligationId = completionLedger.reserve(
            SqSeq(currentSqSequence), RequestId(currentRequestId),
            CompletionCookie(currentCookie));
        if (!currentObligationId) {
            if (stage != Stage::Capacity)
                recorder->setMetric(
                    "cq_backpressure",
                    recorder->metric("cq_backpressure") + 1);
            stage = Stage::Capacity;
            scheduleTick();
            return;
        }
        recordSemantic("CQ_OBLIGATION_RESERVE", "CQ_ENTRY", std::nullopt,
                       currentRequestId, currentCookie);
        liveCqObligations = completionLedger.liveCount();
        recorder->setMetric("live_cq_obligations", liveCqObligations);
        recorder->setMetric("live_contexts", liveCqObligations);
        recorder->setMetric(
            "peak_live_cq_obligations",
            std::max<uint64_t>(recorder->metric("peak_live_cq_obligations"),
                               liveCqObligations));
    }
    fatal_if(!sqIntakeLedger.release(*currentSqIntakeId),
             "%s: SQ intake release ownership is invalid", name());
    recorder->setMetric("sq_intakes_released",
                        sqIntakeLedger.releasedCount());
    recorder->setMetric("live_sq_intakes", sqIntakeLedger.liveCount());
    currentSqIntakeId.reset();
    const std::string status = currentError ?
        (currentRequestId == 0 ? "SQ_SEQ_ONLY_ERROR" :
         "SQ_IDENTITY_ERROR") : "";
    recordSemantic("SQ_CONSUME", "SQ_ENTRY", currentSqSequence,
                   currentRequestId, currentCookie, status);
    if (planExecution && !currentError && currentControlOpcode == 0) {
        seenGenerateRequests.insert(currentRequestId);
        currentAcceptTick = curTick();
        if (driverRef)
            driverRef->notifySqAccepted(currentRequestId);
    }
    ++sqConsumer;
    expectedDoorbellTail = sqConsumer + 1;
    recorder->setMetric("npu_sq_consumer_seq", sqConsumer);
    sqCommitPending = false;
    startSqHead();
}

void
NpuServingFrontend::handleParameterRecord()
{
    if (!activeRead)
        return;
    parameterData = activeRead->data;
    const bool readError = activeRead->sawError;
    activeRead.reset();
    if (readError || parameterData.size() < agent_abi::kParameterHeaderBytes) {
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie,
                       "E_PARAMETER_LENGTH_MISMATCH");
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            agent_abi::E_PARAMETER_LENGTH_MISMATCH);
        return;
    }
    const auto value = agent_abi::decodeParameterHeader(parameterData.data());
    const auto structureError =
        gate3ParameterStructureError(value, parameterData.size());
    if (parameterData.size() != currentParameterBytes || structureError) {
        const agent_abi::DetailCode detail =
            parameterData.size() != currentParameterBytes ?
            agent_abi::E_PARAMETER_LENGTH_MISMATCH : *structureError;
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie,
                       agent_abi::detailCodeNameV1(detail));
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            detail);
        return;
    }
    if (planExecution && currentControlOpcode != 0) {
        handleControlParameter(value);
        return;
    }
    std::vector<uint8_t> covered(parameterData.begin(), parameterData.end());
    uint32_t crc_offset = agent_abi::kParameterHeaderCrc32Offset;
    std::fill(covered.begin() + crc_offset, covered.begin() + crc_offset + 4, 0);
    const uint32_t total = value.total_bytes;
    bool valid = value.magic == 0x504e4741 &&
        value.abi_major == agent_abi::kAbiMajor &&
        value.abi_minor == agent_abi::kAbiMinor &&
        value.header_bytes == agent_abi::kParameterHeaderBytes &&
        total >= agent_abi::kParameterHeaderBytes &&
        total % 8 == 0 && total == parameterData.size() &&
        value.request_kind == 1 &&
        value.target_request_id == 0 &&
        value.qos == currentQos &&
        value.output_capacity_bytes >= dataBusBytes &&
        value.output_metadata_capacity_bytes >= agent_abi::kOutputMetadataBytes &&
        agent_abi::crc32c(covered.data(), total) == value.crc32;
    std::vector<uint8_t> inputDigest;
    std::vector<uint8_t> workloadDigest;
    uint64_t chunkBytes = 0;
    bool sawInput = false, sawWorkload = false, sawChunk = false;
    if (valid && value.extension_bytes >= 8 &&
        value.extension_offset + value.extension_bytes <= total) {
        size_t offset = value.extension_offset;
        const size_t end = value.extension_offset + value.extension_bytes;
        while (offset + 8 <= end) {
            const uint16_t type =
                agent_abi::rdU16(parameterData.data() + offset);
            const uint16_t flags =
                agent_abi::rdU16(parameterData.data() + offset + 2);
            const uint32_t payload_bytes =
                agent_abi::rdU32(parameterData.data() + offset + 4);
            if (offset + 8 + payload_bytes > end) { valid = false; break; }
            const uint8_t *payload =
                parameterData.data() + offset + 8;
            if (type == agent_abi::kTlvTypeINPUT_DIGEST &&
                payload_bytes == 32 &&
                (flags & agent_abi::kTlvFlagsREQUIRED)) {
                inputDigest.assign(payload, payload + 32);
                sawInput = true;
            } else if (type == agent_abi::kTlvTypeWORKLOAD_ID_DIGEST &&
                       payload_bytes == 32 &&
                       (flags & agent_abi::kTlvFlagsREQUIRED)) {
                workloadDigest.assign(payload, payload + 32);
                sawWorkload = true;
            } else if (type == agent_abi::kTlvTypeOUTPUT_CHUNK_BYTES &&
                       payload_bytes == 8 &&
                       (flags & agent_abi::kTlvFlagsREQUIRED)) {
                chunkBytes = agent_abi::rdU64(payload);
                sawChunk = true;
            }
            offset += 8 + payload_bytes;
        }
    }
    valid = valid && sawInput && sawWorkload && sawChunk && chunkBytes > 0;
    if (!valid) {
        const agent_abi::DetailCode detail =
            value.total_bytes != parameterData.size() ||
            value.total_bytes % 8 ?
            agent_abi::E_PARAMETER_LENGTH_MISMATCH :
            agent_abi::E_RESERVED_FIELD;
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie,
                       agent_abi::detailCodeNameV1(detail));
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            detail);
        return;
    }
    currentOutputAddress = value.output_addr;
    currentMetadataAddress = value.output_metadata_addr;
    currentInputAddress = value.input_addr;
    currentInputBytes = value.input_bytes;
    currentOutputCapacityBytes = value.output_capacity_bytes;
    currentMetadataCapacityBytes = value.output_metadata_capacity_bytes;
    currentRequestedProfileKey = value.requested_profile_key;
    currentInputTokens = value.input_tokens;
    currentUserId = value.user_id;
    currentTaskSeq = value.task_seq;
    currentRepairRound = value.repair_round;
    currentMaxOutputTokens = value.max_output_tokens;
    currentWorkloadItemId = value.workload_plan_item_id;
    currentPublishChunkBytes = chunkBytes;
    if (inputDigest.size() == currentInputDigest.size())
        std::copy(inputDigest.begin(), inputDigest.end(),
                  currentInputDigest.begin());
    if (workloadDigest.size() == currentWorkloadDigest.size())
        std::copy(workloadDigest.begin(), workloadDigest.end(),
                  currentWorkloadDigest.begin());
    if (planExecution) {
        const bool requireReuse = (currentSqFlags &
            agent_abi::kSqFlagsREQUIRE_KV_REUSE) != 0;
        const bool allowReprefill = (currentSqFlags &
            agent_abi::kSqFlagsALLOW_REPREFILL) != 0;
        const SessionRecord *existing = sessionRecords.find(
            currentSessionId, value.kv_handle);
        const bool identityHit = existing != nullptr &&
            existing->generation == value.kv_generation;
        if (requireReuse || (allowReprefill && !identityHit)) {
            recordSemantic("PARAMETER_REJECT", "PARAMETER",
                           currentSqSequence, currentRequestId,
                           currentCookie, "E_KV_REUSE_REQUIRED");
            queueErrorCompletion(static_cast<uint16_t>(
                agent_abi::CqStatus::PARAM_ERROR),
                agent_abi::kCqFlagsDETAIL_IN_CQ,
                agent_abi::E_KV_REUSE_REQUIRED);
            return;
        }
        if (!allowReprefill) {
            if (existing != nullptr) {
                requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
                return;
            }
            fatal_if(!sessionRecords.insert(currentSessionId, value.kv_handle,
                                            value.kv_generation),
                     "%s: session record admission is invalid", name());
            currentSessionAdmitted = true;
            recorder->setMetric("kv_session_records", sessionRecords.size());
        } else {
            currentSessionAdmitted = identityHit;
        }
    }
    startPromptRead();
}


void
NpuServingFrontend::handlePromptRecord()
{
    if (!activeRead)
        return;
    const bool readError = activeRead->sawError;
    activeRead.reset();
    if (readError) {
        queueErrorCompletion(
            static_cast<uint16_t>(agent_abi::CqStatus::AXI_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            agent_abi::E_AXI_RESPONSE);
        return;
    }
    recordSemantic("CAPACITY_ACCEPT", "CONTEXT", std::nullopt,
                   currentRequestId, currentCookie);
    recorder->setMetric("live_contexts", (unsigned)completionLedger.liveCount());
    NpuExecutionRequest executionRequest;
    executionRequest.sqSequence = currentSqSequence;
    executionRequest.requestId = currentRequestId;
    executionRequest.completionCookie = currentCookie;
    executionRequest.inputAddress = currentInputAddress;
    executionRequest.inputBytes = currentInputBytes;
    executionRequest.outputAddress = currentOutputAddress;
    executionRequest.outputCapacityBytes = currentOutputCapacityBytes;
    executionRequest.metadataAddress = currentMetadataAddress;
    executionRequest.metadataCapacityBytes = currentMetadataCapacityBytes;
    executionRequest.inputTokens = currentInputTokens;
    executionRequest.maxOutputTokens = currentMaxOutputTokens;
    executionRequest.outputTokens = currentMaxOutputTokens;
    executionRequest.requestedProfileKey = currentRequestedProfileKey;
    executionRequest.workloadPlanItemId = currentWorkloadItemId;
    executionRequest.programId = currentProgramId;
    executionRequest.profileId = currentProfileId;
    executionRequest.qos = currentQos;
    executionRequest.inputDigest = currentInputDigest;
    executionRequest.workloadDigest = currentWorkloadDigest;
    lastExecutionRequest = executionRequest;
    const NpuExecutionOutcome outcome =
        requestExecutor->accept(executionRequest);
    if (planExecution && !outcome.admitted) {
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie,
                       agent_abi::detailCodeNameV1(
                           static_cast<agent_abi::DetailCode>(
                               outcome.rejectDetail)));
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            outcome.rejectDetail);
        return;
    }
    if (planExecution &&
            currentMetadataCapacityBytes <
                agent_abi::kOutputMetadataBytes +
                    kSurrogateTimingBreakdownTlvBytes) {
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie,
                       agent_abi::detailCodeNameV1(
                           agent_abi::E_OUTPUT_CAPACITY));
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            agent_abi::E_OUTPUT_CAPACITY);
        return;
    }
    if (outcome.coreStartProbe) {
        recordSemantic("CORE_START", "CONTEXT", std::nullopt,
                       currentRequestId, currentCookie);
        recorder->setMetric("core_starts",
                            recorder->metric("core_starts") + 1);
    }
    if (planExecution) {
        enqueueCurrentBusiness(outcome.serviceNs);
        return;
    }
    startOutput();
}

void
NpuServingFrontend::enqueueCurrentBusiness(uint64_t serviceNs)
{
    fatal_if(acceptedQueue.size() + (executingBusiness ? 1 : 0) >=
                 acceptedQueueEntries,
             "%s: accepted generate queue exceeds capacity plan bound",
             name());
    ParkedBusiness parked;
    parked.sqSequence = currentSqSequence;
    parked.requestId = currentRequestId;
    parked.cookie = currentCookie;
    parked.sessionId = currentSessionId;
    parked.inputAddress = currentInputAddress;
    parked.inputBytes = currentInputBytes;
    parked.outputAddress = currentOutputAddress;
    parked.metadataAddress = currentMetadataAddress;
    parked.obligationId = currentObligationId;
    parked.executionRequest = lastExecutionRequest;
    parked.userId = currentUserId;
    parked.taskSeq = currentTaskSeq;
    parked.maxOutputTokens = currentMaxOutputTokens;
    parked.repairRound = currentRepairRound;
    parked.qos = currentQos;
    parked.sessionAdmitted = currentSessionAdmitted;
    parked.publishChunkBytes = currentPublishChunkBytes;
    parked.serviceNs = serviceNs;
    parked.readyTick = currentAcceptTick;
    const auto deadline = generateDeadlineTicks.find(currentRequestId);
    parked.deadlineTick =
        deadline == generateDeadlineTicks.end() ? kNpuNoDeadline :
        deadline->second;
    acceptedQueue.emplace(parked.requestId, parked);
    currentValid = false;
    currentError = false;
    currentObligationId.reset();
    stage = Stage::Doorbell;
    dispatchAcceptedBusiness();
    processDoorbells();
}

void
NpuServingFrontend::dispatchAcceptedBusiness()
{
    if (!planExecution || executingBusiness || acceptedQueue.empty())
        return;
    fatal_if(businessParked(),
             "%s: parked business conflicts with execution dispatch", name());
    auto selected = acceptedQueue.begin();
    for (auto it = acceptedQueue.begin(); it != acceptedQueue.end(); ++it) {
        const NpuExecutionCandidate candidate{
            it->second.requestId, it->second.deadlineTick,
            it->second.readyTick, it->second.qos};
        const NpuExecutionCandidate best{
            selected->second.requestId, selected->second.deadlineTick,
            selected->second.readyTick, selected->second.qos};
        if (npuExecutionPrecedes(candidate, best))
            selected = it;
    }
    ParkedBusiness parked = selected->second;
    acceptedQueue.erase(selected);
    executingBusiness = true;
    parked.executionDeadlineTick =
        curTick() + (parked.serviceNs > 0 ?
                     parked.serviceNs * sim_clock::as_int::ns : 1);
    parkedBusiness = parked;
    scheduleExecutionEvent(parked.executionDeadlineTick);
    scheduleTick();
}

void
NpuServingFrontend::scheduleExecutionEvent(Tick deadline)
{
    if (executionEvent.scheduled())
        reschedule(&executionEvent, deadline, true);
    else
        schedule(&executionEvent, deadline);
}

}
}

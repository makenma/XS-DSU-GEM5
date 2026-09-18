#include "dev/ai_mesh/npu_serving_frontend.hh"

#include "dev/ai_mesh/npu_serving_frontend_kv_map.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_axi_driver.hh"
#include "dev/ai_mesh/serving_host_bindings.hh"
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

const AgentHostBindingPlan *
NpuServingFrontend::frozenBindingPlan(uint16_t programId,
                                      uint16_t profileId) const
{
    for (const AgentHostBindingPlan &plan : frozenBindingPlans)
        if (plan.programId == programId && plan.profileId == profileId)
            return &plan;
    return nullptr;
}

bool
NpuServingFrontend::rejectParameter(agent_abi::DetailCode code)
{
    const std::optional<agent_abi::DetailDispositionV1> disposition =
        agent_abi::detailDispositionV1(code);
    if (!disposition) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return false;
    }
    recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                   currentRequestId, currentCookie,
                   agent_abi::detailCodeNameV1(code));
    queueErrorCompletion(static_cast<uint16_t>(disposition->cqStatus),
                         agent_abi::kCqFlagsDETAIL_IN_CQ,
                         static_cast<uint32_t>(code));
    return false;
}

bool
NpuServingFrontend::verifyParameterBindings(
    const agent_abi::ParameterHeader &value)
{
    if (!servingExecution)
        return true;
    if (frozenBindingPlans.empty())
        return value.binding_count == 0 ? true :
            rejectParameter(agent_abi::E_BINDING_ROLE);
    const AgentHostBindingPlan *plan =
        frozenBindingPlan(currentProgramId, currentProfileId);
    if (plan == nullptr)
        return rejectParameter(agent_abi::E_REQUEST_PROFILE);
    if (value.binding_record_bytes != agent_abi::kBindingRecordBytes ||
            value.binding_table_offset != agent_abi::kParameterHeaderBytes ||
            value.binding_count != plan->requirements.size() ||
            value.extension_offset != value.binding_table_offset +
                uint32_t(value.binding_count) *
                    agent_abi::kBindingRecordBytes ||
            value.total_bytes != value.extension_offset +
                value.extension_bytes)
        return rejectParameter(agent_abi::E_BINDING_ROLE);
    std::vector<agent_abi::BindingRecord> bindings;
    bindings.reserve(value.binding_count);
    for (uint32_t index = 0; index < value.binding_count; ++index)
        bindings.push_back(agent_abi::decodeBindingRecord(
            parameterData.data() + value.binding_table_offset +
            index * agent_abi::kBindingRecordBytes));
    std::string reason;
    const HostBindingVerdict verdict = verifyHostBindingRequest(
            bindings, *plan, value.input_addr, value.input_bytes,
            value.output_addr, value.output_capacity_bytes,
            value.output_metadata_addr, value.output_metadata_capacity_bytes,
            kvManager.geometry().region_base, kvManager.geometry().regionBytes(),
            frozenKvSessionSlotBytes, reason);
    if (verdict == HostBindingVerdict::AliasMismatch)
        return rejectParameter(agent_abi::E_BINDING_ALIAS_MISMATCH);
    if (verdict != HostBindingVerdict::Match)
        return rejectParameter(agent_abi::E_BINDING_ROLE);
    currentHostBindings = std::move(bindings);
    return true;
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
        rejectParameter(agent_abi::E_PARAMETER_LENGTH_MISMATCH);
        return;
    }
    const auto value = agent_abi::decodeParameterHeader(parameterData.data());
    const auto structureError =
        gate3ParameterStructureError(value, parameterData.size());
    if (parameterData.size() != currentParameterBytes || structureError) {
        rejectParameter(parameterData.size() != currentParameterBytes ?
            agent_abi::E_PARAMETER_LENGTH_MISMATCH : *structureError);
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
    const bool layoutValid = value.magic == 0x504e4741 &&
        value.abi_major == agent_abi::kAbiMajor &&
        value.abi_minor == agent_abi::kAbiMinor &&
        value.header_bytes == agent_abi::kParameterHeaderBytes &&
        total >= agent_abi::kParameterHeaderBytes &&
        total % 8 == 0 && total == parameterData.size() &&
        agent_abi::crc32c(covered.data(), total) == value.crc32;
    if (!layoutValid) {
        rejectParameter(
            value.total_bytes != parameterData.size() ||
            value.total_bytes % 8 ?
            agent_abi::E_PARAMETER_LENGTH_MISMATCH :
            agent_abi::E_REQUEST_BINDING);
        return;
    }
    if (value.request_kind != 1 || value.target_request_id != 0 ||
            value.qos != currentQos || value.reserved != 0 ||
            value.reserved2 != 0 || value.reserved3 != 0 ||
            value.moe_route_profile_id != 0) {
        rejectParameter(agent_abi::E_RESERVED_FIELD);
        return;
    }
    const AgentPlanRequestProof *proof = nullptr;
    if (planExecution) {
        const auto proof_it = planRequestProofs.find(currentRequestId);
        if (proof_it == planRequestProofs.end()) {
            rejectParameter(agent_abi::E_WORKLOAD_PLAN_MISMATCH);
            return;
        }
        proof = &proof_it->second;
        if (proof->sessionId != currentSessionId ||
                proof->programId != currentProgramId ||
                proof->profileId != currentProfileId ||
                proof->itemId != value.workload_plan_item_id ||
                proof->repairRound != value.repair_round ||
                proof->userId != value.user_id ||
                proof->taskSeq != value.task_seq ||
                proof->kvHandle != value.kv_handle ||
                proof->generation != value.kv_generation ||
                proof->qos != value.qos ||
                proof->fullContextTokens != value.input_tokens ||
                proof->fullContextBytes != value.input_bytes ||
                proof->cachedTokens != value.cached_tokens ||
                proof->outputTokens != value.max_output_tokens ||
                proof->outputCapacityBytes != value.output_capacity_bytes ||
                proof->metadataCapacityBytes !=
                    value.output_metadata_capacity_bytes) {
            rejectParameter(agent_abi::E_WORKLOAD_PLAN_MISMATCH);
            return;
        }
        if (proof->profileKey != value.requested_profile_key) {
            rejectParameter(agent_abi::E_REQUEST_PROFILE_KEY);
            return;
        }
    }
    currentHostBindings.clear();
    if (!verifyParameterBindings(value))
        return;
    ParameterTlvValues tlvs;
    if (parameterTlvStructureError(parameterData.data(), parameterData.size(),
                                   value, tlvs)) {
        rejectParameter(agent_abi::E_REQUEST_BINDING);
        return;
    }
    if (tlvs.skippedOptional != 0)
        recorder->setMetric("skipped_optional_tlvs",
                            recorder->metric("skipped_optional_tlvs") +
                            tlvs.skippedOptional);
    if (tlvs.inputDigest.size() != 32 ||
            (proof != nullptr && !std::equal(
                proof->inputDigest.begin(), proof->inputDigest.end(),
                tlvs.inputDigest.begin()))) {
        rejectParameter(agent_abi::E_WORKLOAD_PLAN_MISMATCH);
        return;
    }
    if (tlvs.workloadDigest.size() != 32 ||
            (proof != nullptr && !std::equal(
                frozenWorkloadDigest.begin(), frozenWorkloadDigest.end(),
                tlvs.workloadDigest.begin()))) {
        rejectParameter(agent_abi::E_WORKLOAD_PLAN_MISMATCH);
        return;
    }
    if (tlvs.outputChunkBytes == 0 ||
            (proof != nullptr && tlvs.outputChunkBytes !=
                planRegistry->publishChunkBytes())) {
        rejectParameter(agent_abi::E_OUTPUT_CHUNK_MISMATCH);
        return;
    }
    if (proof != nullptr &&
            (tlvs.hasDeadline != proof->hasDeadline ||
             (proof->hasDeadline && tlvs.deadlineTick != proof->deadlineTick))) {
        rejectParameter(agent_abi::E_WORKLOAD_PLAN_MISMATCH);
        return;
    }
    if (value.output_capacity_bytes < dataBusBytes ||
            value.output_metadata_capacity_bytes <
                agent_abi::kOutputMetadataBytes) {
        rejectParameter(agent_abi::E_OUTPUT_CAPACITY);
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
    currentCachedTokens = value.cached_tokens;
    currentUserId = value.user_id;
    currentTaskSeq = value.task_seq;
    currentRepairRound = value.repair_round;
    currentMaxOutputTokens = value.max_output_tokens;
    currentWorkloadItemId = value.workload_plan_item_id;
    currentPublishChunkBytes = tlvs.outputChunkBytes;
    if (tlvs.inputDigest.size() == currentInputDigest.size())
        std::copy(tlvs.inputDigest.begin(), tlvs.inputDigest.end(),
                  currentInputDigest.begin());
    if (tlvs.workloadDigest.size() == currentWorkloadDigest.size())
        std::copy(tlvs.workloadDigest.begin(), tlvs.workloadDigest.end(),
                  currentWorkloadDigest.begin());
    currentKvHandle = value.kv_handle;
    currentKvGeneration = value.kv_generation;
    if (servingExecution) {
        submitServingRequest();
        return;
    }
    if (planExecution && !admitKvSession())
        return;
    startPromptRead();
}


bool
NpuServingFrontend::admitKvSession()
{
    const uint32_t flags = currentSqFlags &
        (agent_abi::kSqFlagsREQUIRE_KV_REUSE |
         agent_abi::kSqFlagsALLOW_REPREFILL);
    const auto deadline = generateDeadlineTicks.find(currentRequestId);
    KvAdmissionIntent intent;
    intent.request_id = currentRequestId;
    intent.session_id = currentSessionId;
    intent.kv_handle = currentKvHandle;
    intent.generation = currentKvGeneration;
    intent.contract_digest = kvContractDigest;
    intent.deadline_or_max = deadline == generateDeadlineTicks.end() ?
        kNpuNoDeadline : deadline->second;
    intent.qos = currentQos;
    intent.ready_tick = curTick();
    intent.flags = flags;
    KvEdgeInputs edge;
    edge.tick = curTick();
    edge.admissions.push_back(intent);
    const KvEdgeResult result = kvManager.commitEdge(edge);
    recorder->setMetric("kv_session_records",
                        static_cast<unsigned>(kvManager.recordCount()));
    recorder->setMetric("kv_session_tombstones",
                        static_cast<unsigned>(kvManager.tombstoneCount()));
    if (result.fatal) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return false;
    }
    const auto outcome = result.admissions.find(currentRequestId);
    if (outcome == result.admissions.end()) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return false;
    }
    const auto promotion = result.promotions.find(currentRequestId);
    const KvAdmissionMapping mapping = mapKvAdmissionOutcome(
        outcome->second,
        promotion == result.promotions.end()
            ? std::nullopt
            : std::optional<KvPromotionOutcome>(promotion->second));
    if (mapping.protocol_error) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return false;
    }
    if (mapping.admitted)
        currentSessionAdmitted = true;
    if (mapping.success)
        return true;
    return rejectParameter(mapping.detail);
}

void
NpuServingFrontend::submitServingRequest()
{
    if (servingExecution)
        ensureServingPrepared();
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
    executionRequest.cachedTokens = currentCachedTokens;
    executionRequest.kvFlags = currentSqFlags &
        (agent_abi::kSqFlagsREQUIRE_KV_REUSE |
         agent_abi::kSqFlagsALLOW_REPREFILL);
    executionRequest.repairRound = currentRepairRound;
    executionRequest.maxOutputTokens = currentMaxOutputTokens;
    executionRequest.outputTokens = currentMaxOutputTokens;
    executionRequest.requestedProfileKey = currentRequestedProfileKey;
    executionRequest.workloadPlanItemId = currentWorkloadItemId;
    executionRequest.programId = currentProgramId;
    executionRequest.profileId = currentProfileId;
    executionRequest.qos = currentQos;
    executionRequest.inputDigest = currentInputDigest;
    executionRequest.workloadDigest = currentWorkloadDigest;
    executionRequest.sessionId = currentSessionId;
    executionRequest.kvHandle = currentKvHandle;
    executionRequest.generation = currentKvGeneration;
    executionRequest.contractDigest = servingContractDigest;
    executionRequest.hostBindings = currentHostBindings;
    lastExecutionRequest = executionRequest;
    const NpuAdmission admission = activeExecutor()->submit(executionRequest);
    if (servingExecution && admission.admitted)
        currentSessionAdmitted = true;
    if (planExecution && !admission.admitted) {
        rejectParameter(static_cast<agent_abi::DetailCode>(
            admission.rejectDetail));
        return;
    }
    recordSemantic("CAPACITY_ACCEPT", "CONTEXT", std::nullopt,
                   currentRequestId, currentCookie);
    recorder->setMetric("live_contexts",
                        (unsigned)completionLedger.liveCount());
    if (planExecution) {
        enqueueCurrentBusiness();
        return;
    }
    startOutput();
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
    submitServingRequest();
}

void
NpuServingFrontend::enqueueCurrentBusiness()
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
    const uint64_t serviceNs = parked.executionRequest ?
        activeExecutor()->modeledServiceNs(*parked.executionRequest) : 0;
    parked.executionDeadlineTick =
        curTick() + (serviceNs > 0 ? serviceNs * sim_clock::as_int::ns : 1);
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

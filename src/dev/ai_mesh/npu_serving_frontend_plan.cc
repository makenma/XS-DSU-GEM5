#include "dev/ai_mesh/npu_serving_frontend.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_axi_driver.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

std::vector<uint8_t>
NpuServingFrontend::makeOutput(uint64_t sequence) const
{
    if (planExecution && lastExecutionRequest) {
        std::vector<uint8_t> data =
            requestExecutor->outputPayload(*lastExecutionRequest);
        fatal_if(data.empty(),
                 "%s: surrogate executor produced no output payload", name());
        return data;
    }
    std::vector<uint8_t> data(dataBusBytes, 0);
    for (size_t index = 0; index < data.size(); ++index)
        data[index] = static_cast<uint8_t>((sequence + index * 7) & 0xff);
    return data;
}
std::vector<uint8_t>
NpuServingFrontend::makeMetadata(uint64_t sequence, uint32_t status) const
{
    if (planExecution && lastExecutionRequest) {
        agent_abi::OutputMetadata value;
        value.magic = 0x4f4e4741;
        value.abi_major = agent_abi::kAbiMajor;
        value.abi_minor = agent_abi::kAbiMinor;
        value.header_bytes = agent_abi::kOutputMetadataBytes;
        value.total_bytes = agent_abi::kOutputMetadataBytes +
            kSurrogateTimingBreakdownTlvBytes;
        value.flags = currentSessionAdmitted ?
            agent_abi::kMetadataFlagsSESSION_ADMITTED : 0;
        value.terminal_status = status;
        value.request_id = currentRequestId;
        value.session_id = currentSessionId;
        value.user_id = currentUserId;
        value.task_seq = currentTaskSeq;
        value.repair_round = currentRepairRound;
        value.output_tokens = currentMaxOutputTokens;
        value.output_bytes =
            requestExecutor->outputBytes(*lastExecutionRequest);
        value.semantic_content_digest =
            requestExecutor->semanticDigest(*lastExecutionRequest);
        value.completed_instance_count = 0;
        value.moe_invocation_count = 0;
        value.request_start_tick = curTick();
        value.terminal_ready_tick = curTick();
        std::vector<uint8_t> data =
            toVector(agent_abi::encodeOutputMetadata(value));
        const size_t offset = data.size();
        data.resize(offset + kSurrogateTimingBreakdownTlvBytes, 0);
        agent_abi::wrU16(data.data() + offset,
                         agent_abi::kOutputTlvTypeTIMING_BREAKDOWN);
        agent_abi::wrU16(data.data() + offset + 2,
                         agent_abi::kTlvFlagsREQUIRED);
        agent_abi::wrU32(data.data() + offset + 4,
                         kSurrogateTimingBreakdownTlvBytes - 8);
        agent_abi::wrU32(
            data.data() + agent_abi::kOutputMetadataCrcFieldOffset, 0);
        agent_abi::wrU32(
            data.data() + agent_abi::kOutputMetadataCrcFieldOffset,
            agent_abi::crc32c(data.data(), data.size()));
        return data;
    }
    agent_abi::OutputMetadata value;
    value.magic = 0x4f4e4741;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.header_bytes = agent_abi::kOutputMetadataBytes;
    value.total_bytes = agent_abi::kOutputMetadataBytes +
        ((mode("METADATA_TLV_TAIL") ||
          mode("METADATA_TLV_SIZE_MISMATCH")) ? 72 : 0);
    value.flags = mode("METADATA_FLAGS_UNKNOWN") ? 2 : 0;
    value.terminal_status = mode("METADATA_CQ_MISMATCH") ?
        static_cast<uint32_t>(agent_abi::CqStatus::PARAM_ERROR) : status;
    value.request_id = currentRequestId;
    value.session_id = 0x4000 + sequence;
    value.user_id = 7;
    value.task_seq = static_cast<uint32_t>(sequence);
    if (mode("METADATA_SESSION_MISMATCH"))
        value.session_id = 0;
    if (mode("METADATA_USER_MISMATCH"))
        ++value.user_id;
    if (mode("METADATA_TASK_MISMATCH"))
        ++value.task_seq;
    if (mode("METADATA_ROUND_MISMATCH"))
        ++value.repair_round;
    value.output_tokens = 16;
    value.output_bytes = dataBusBytes;
    value.completed_instance_count = 1;
    value.request_start_tick = curTick();
    value.terminal_ready_tick = curTick();
    std::vector<uint8_t> data =
        toVector(agent_abi::encodeOutputMetadata(value));
    if (mode("METADATA_TLV_TAIL") ||
        mode("METADATA_TLV_SIZE_MISMATCH")) {
        const size_t offset = data.size();
        data.resize(offset + 72, 0);
        agent_abi::wrU16(data.data() + offset,
                         agent_abi::kOutputTlvTypeTIMING_BREAKDOWN);
        agent_abi::wrU16(data.data() + offset + 2,
                         agent_abi::kTlvFlagsREQUIRED);
        agent_abi::wrU32(data.data() + offset + 4,
                         mode("METADATA_TLV_SIZE_MISMATCH") ? 56 : 64);
    }
    agent_abi::wrU32(
        data.data() + agent_abi::kOutputMetadataCrcFieldOffset, 0);
    agent_abi::wrU32(
        data.data() + agent_abi::kOutputMetadataCrcFieldOffset,
        agent_abi::crc32c(data.data(), data.size()));
    return data;
}
bool
NpuServingFrontend::controlMatrixValid(
    const agent_abi::ParameterHeader &value) const
{
    if (value.magic != 0x504e4741 ||
            value.abi_major != agent_abi::kAbiMajor ||
            value.abi_minor != agent_abi::kAbiMinor ||
            value.header_bytes != agent_abi::kParameterHeaderBytes ||
            value.total_bytes != agent_abi::kParameterHeaderBytes ||
            parameterData.size() != agent_abi::kParameterHeaderBytes)
        return false;
    if (value.flags != 0 || value.reserved != 0 || value.reserved2 != 0 ||
            value.reserved3 != 0 || value.moe_route_profile_id != 0 ||
            value.workload_plan_item_id != 0 || value.requested_profile_key != 0 ||
            value.binding_table_offset != 0 || value.binding_count != 0 ||
            value.binding_record_bytes != agent_abi::kBindingRecordBytes ||
            value.extension_offset != 0 || value.extension_bytes != 0)
        return false;
    if (value.input_addr != 0 || value.input_bytes != 0 ||
            value.input_tokens != 0 || value.cached_tokens != 0 ||
            value.output_addr != 0 || value.output_capacity_bytes != 0 ||
            value.output_metadata_addr != 0 ||
            value.output_metadata_capacity_bytes != 0 ||
            value.max_output_tokens != 0 || value.user_id != 0 ||
            value.task_seq != 0 || value.repair_round != 0 || value.qos != 0)
        return false;
    const bool cancel = currentControlOpcode == agent_abi::kSqOpcodeCANCEL;
    if (value.request_kind != static_cast<uint8_t>(currentControlOpcode))
        return false;
    if (cancel && (value.target_request_id == 0 || value.kv_handle != 0 ||
                   value.kv_generation != 0))
        return false;
    if (!cancel && (value.target_request_id != 0 || value.kv_handle == 0 ||
                    value.kv_generation == 0))
        return false;
    std::vector<uint8_t> covered(parameterData);
    std::fill(covered.begin() + agent_abi::kParameterHeaderCrc32Offset,
              covered.begin() + agent_abi::kParameterHeaderCrc32Offset + 4, 0);
    return agent_abi::crc32c(covered.data(), covered.size()) == value.crc32;
}
void
NpuServingFrontend::handleControlParameter(
    const agent_abi::ParameterHeader &value)
{
    if (!controlMatrixValid(value)) {
        recordSemantic("PARAMETER_REJECT", "PARAMETER", currentSqSequence,
                       currentRequestId, currentCookie, "E_RESERVED_FIELD");
        queueErrorCompletion(static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR),
            agent_abi::kCqFlagsDETAIL_IN_CQ,
            agent_abi::E_RESERVED_FIELD);
        return;
    }
    resolveControlCommand(value);
}
void
NpuServingFrontend::resolveControlCommand(
    const agent_abi::ParameterHeader &value)
{
    const bool cancel = currentControlOpcode == agent_abi::kSqOpcodeCANCEL;
    uint16_t status = agent_abi::kCqStatusSUCCESS;
    if (cancel) {
        if (seenGenerateRequests.count(value.target_request_id) == 0)
            status = agent_abi::kCqStatusNOT_FOUND;
        else if (terminalGenerateRequests.count(value.target_request_id) != 0)
            status = agent_abi::kCqStatusALREADY_TERMINAL;
        else if (!businessParked() &&
                 acceptedQueue.count(value.target_request_id) == 0) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
    } else if (!resolveSessionRelease(currentSessionId, value.kv_handle,
                                      value.kv_generation, status)) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return;
    }
    recordSemantic("CONTROL_RESOLVE", "CONTROL_WAITER", std::nullopt,
                   currentRequestId, currentCookie,
                   controlStatusName(status));
    currentCqStatus = status;
    currentCqFlags = agent_abi::kCqFlagsCONTROL_COMMAND;
    currentDetailCode = 0;
    if (cancel && status == agent_abi::kCqStatusSUCCESS) {
        const Tick targetReadyTick = curTick();
        const auto cancelTarget = [this, target = value.target_request_id](
                                       Tick now) {
            if (acceptedQueue.count(target) != 0)
                cancelQueuedTarget(target, now);
            else
                cancelParkedTarget(now);
        };
        if (controlCqFirst) {
            stageTerminalRecord(currentObligationId, targetReadyTick,
                                currentSqSequence, currentRequestId,
                                currentCookie, 0, currentCqStatus,
                                currentCqFlags, 0, std::nullopt);
            cancelTarget(targetReadyTick + 1);
        } else {
            cancelTarget(targetReadyTick);
            stageTerminalRecord(currentObligationId, targetReadyTick + 1,
                                currentSqSequence, currentRequestId,
                                currentCookie, 0, currentCqStatus,
                                currentCqFlags, 0, std::nullopt);
        }
        finishCurrent();
        return;
    }
    if (businessParked()) {
        stageTerminalRecord(currentObligationId, curTick(), currentSqSequence,
                            currentRequestId, currentCookie, 0,
                            currentCqStatus, currentCqFlags, 0,
                            std::nullopt);
        restoreParkedBusiness();
        scheduleTerminalWake();
        scheduleTick();
        return;
    }
    stageTerminalResult();
}
void
NpuServingFrontend::maybeNotifyFirstOutputChunk(
    std::optional<uint64_t> requestId, bool outputComplete)
{
    if (!planExecution || outputChunkNotified || !requestId ||
            currentPublishChunkBytes == 0)
        return;
    if (!outputComplete && outputCommittedBytes < currentPublishChunkBytes)
        return;
    outputChunkNotified = true;
    if (driverRef)
        driverRef->notifyFirstOutputChunk(*requestId);
}
bool
NpuServingFrontend::pendingControlIntake() const
{
    return std::any_of(
        doorbells.begin(), doorbells.end(),
        [this](const Doorbell &doorbell) {
            return doorbell.tail > advertisedSqTail;
        });
}
bool
NpuServingFrontend::outputControlIntakeSuppresses() const
{
    if (!currentValid || currentPublishChunkBytes == 0 ||
            !pendingControlIntake())
        return false;
    uint64_t nextOffset = 0;
    for (size_t index = 0; index <= activeWrite->segmentIndex; ++index)
        nextOffset += activeWrite->segments[index].logicalBytes;
    return nextOffset % currentPublishChunkBytes == 0;
}
void
NpuServingFrontend::parkOutputForControlIntake()
{
    ++activeWrite->segmentIndex;
    saveParkedBusiness();
    stage = Stage::Doorbell;
    processDoorbells();
}
void
NpuServingFrontend::cancelParkedTarget(Tick now)
{
    fatal_if(!businessParked(),
             "%s: live cancel target is not the parked business", name());
    const ParkedBusiness parked = *parkedBusiness;
    parkedBusiness.reset();
    if (executionEvent.scheduled())
        deschedule(&executionEvent);
    latchGenerateTerminal(parked.requestId);
    uint16_t flags = 0;
    std::optional<uint32_t> outputBytes;
    if (parked.outputWork) {
        flags = agent_abi::kCqFlagsPARTIAL_OUTPUT;
        outputBytes = static_cast<uint32_t>(parked.outputCommittedBytes);
        recorder->setMetric("output_cancel_requests", 1);
        recorder->setMetric("output_cancel_prefix_bytes",
                            parked.outputCommittedBytes);
    }
    stageTerminalRecord(parked.obligationId, now, parked.sqSequence,
                        parked.requestId, parked.cookie, parked.qos,
                        agent_abi::kCqStatusCANCELLED, flags, 0, outputBytes);
    executingBusiness = false;
    dispatchAcceptedBusiness();
}
void
NpuServingFrontend::cancelQueuedTarget(uint64_t requestId, Tick now)
{
    const auto found = acceptedQueue.find(requestId);
    fatal_if(found == acceptedQueue.end(),
             "%s: queued cancel target is not an accepted request", name());
    const ParkedBusiness parked = found->second;
    acceptedQueue.erase(found);
    latchGenerateTerminal(parked.requestId);
    recorder->setMetric("queued_cancel_requests", 1);
    stageTerminalRecord(parked.obligationId, now, parked.sqSequence,
                        parked.requestId, parked.cookie, parked.qos,
                        agent_abi::kCqStatusCANCELLED, 0, 0, std::nullopt);
}
bool
NpuServingFrontend::resolveSessionRelease(uint64_t sessionId, uint64_t kvHandle,
                                          uint32_t generation,
                                          uint16_t &status)
{
    const SessionRecord *existing = sessionRecords.find(sessionId, kvHandle);
    if (existing == nullptr) {
        status = agent_abi::kCqStatusNOT_FOUND;
        return true;
    }
    if (existing->generation != generation) {
        status = agent_abi::kCqStatusSTALE_GENERATION;
        return true;
    }
    fatal_if(!sessionRecords.erase(sessionId, kvHandle),
             "%s: session record erase ownership is invalid", name());
    recorder->setMetric("kv_session_tombstones", sessionRecords.tombstones());
    recorder->setMetric("kv_session_records", sessionRecords.size());
    status = agent_abi::kCqStatusSUCCESS;
    return true;
}
void
NpuServingFrontend::saveParkedBusiness()
{
    const bool parkingOutput =
        activeWrite && activeWrite->control == "OUTPUT";
    fatal_if(!currentValid || (!parkingOutput && stage != Stage::Execute),
             "%s: business is not parked in execution", name());
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
    if (executionEvent.scheduled())
        parked.executionDeadlineTick = executionEvent.when();
    if (parkingOutput) {
        parked.outputWork = activeWrite;
        parked.outputCommittedBytes = outputCommittedBytes;
        parked.outputChunkNotified = outputChunkNotified;
        activeWrite.reset();
    }
    parkedBusiness = parked;
    currentValid = false;
    currentObligationId.reset();
}
void
NpuServingFrontend::loadBusinessContext(const ParkedBusiness &parked)
{
    currentSqSequence = parked.sqSequence;
    currentRequestId = parked.requestId;
    currentCookie = parked.cookie;
    currentSessionId = parked.sessionId;
    currentInputAddress = parked.inputAddress;
    currentInputBytes = parked.inputBytes;
    currentOutputAddress = parked.outputAddress;
    currentMetadataAddress = parked.metadataAddress;
    currentObligationId = parked.obligationId;
    lastExecutionRequest = parked.executionRequest;
    currentUserId = parked.userId;
    currentTaskSeq = parked.taskSeq;
    currentMaxOutputTokens = parked.maxOutputTokens;
    currentRepairRound = parked.repairRound;
    currentQos = parked.qos;
    currentSessionAdmitted = parked.sessionAdmitted;
    currentPublishChunkBytes = parked.publishChunkBytes;
    currentValid = true;
    currentError = false;
    currentControlOpcode = 0;
}
void
NpuServingFrontend::restoreParkedBusiness()
{
    fatal_if(!businessParked(),
             "%s: no parked business to restore", name());
    const ParkedBusiness parked = *parkedBusiness;
    parkedBusiness.reset();
    loadBusinessContext(parked);
    if (parked.outputWork) {
        outputCommittedBytes = parked.outputCommittedBytes;
        outputChunkNotified = parked.outputChunkNotified;
        activeWrite = parked.outputWork;
        activateWriteSegment(*activeWrite, transferPlanner);
        stage = Stage::Output;
        scheduleTick();
        return;
    }
    stage = Stage::Execute;
    scheduleExecutionEvent(
        std::max<Tick>(parked.executionDeadlineTick, clockEdge(Cycles(1))));
}

}
}

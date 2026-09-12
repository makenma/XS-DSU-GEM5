#include "dev/ai_mesh/agent_axi_driver.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <utility>

#include "base/logging.hh"
#include "params/AgentAxiDriver.hh"
#include "sim/core.hh"
#include "sim/cur_tick.hh"
#include "sim/sim_exit.hh"

namespace gem5
{
namespace ai_mesh
{

AgentAxiDriver::AgentAxiDriver(const Params &p)
    : ClockedObject(p),
      master(p.master), target(p.target), recorder(p.recorder),
      planDrivenMode(p.request_source == "replay_plan"),
      dataBusBytes(p.data_bus_bytes), sqDepth(p.sq_depth),
      cqDepth(p.cq_depth), controlBytes(p.control_bytes),
      maxBurstBeats(p.max_burst_beats),
      hostBase(p.host_base), npuControlBase(p.npu_control_base),
      agentProxyControlBase(p.agent_proxy_control_base),
      cqRingBase(p.cq_ring_base), msiBase(p.msi_base),
      profile(p.profile), requestCount(p.request_count),
      localVisibilityDelay(p.local_visibility_delay),
      requestIdCapacity(p.request_id_capacity), drainCycles(p.drain_cycles),
      doorbellAxiId(p.doorbell_axi_id), ackAxiId(p.ack_axi_id),
      controlDoorbellErrorOrdinal(p.control_doorbell_error_ordinal),
      mutateCancelCommandOrdinal(p.mutate_cancel_command_ordinal),
      mutateCancelCommandStatus(p.mutate_cancel_command_status),
      controlDoorbellBHoldTicks(p.control_doorbell_b_hold_ns *
                                sim_clock::as_int::ns),
      cqReadDelayTicks(p.cq_read_delay_ns * sim_clock::as_int::ns),
      metadataReadDelayTicks(p.metadata_read_delay_ns *
                             sim_clock::as_int::ns),
      ringLayout(p.sq_ring_base, p.sq_depth,
                 p.cq_ring_base, p.cq_depth),
      transferPlanner(p.data_bus_bytes, p.max_burst_beats),
      ledger(p.sq_depth, p.request_id_capacity), irqCommitQueue(p.cq_depth),
      tickEvent(this),
      fatalDrainEvent(this),
      managerWakeEvent(this)
{
    fatal_if(dataBusBytes == 0 || dataBusBytes > 64,
             "%s: invalid AXI data bus width", name());
    fatal_if(controlBytes != sizeof(uint64_t) || controlBytes > dataBusBytes,
             "%s: Gate3 control window must be 8 bytes", name());
    fatal_if(!isPowerOfTwo(sqDepth) || !isPowerOfTwo(cqDepth),
             "%s: ring depths must be powers of two", name());
    const bool planMode = p.request_source == "replay_plan";
    fatal_if(!planMode && p.request_source != "protocol_profile",
             "%s: unknown request source %s", name(), p.request_source);
    if (planMode) {        fatal_if(p.plan_image.empty(),
                 "%s: replay_plan requires a plan image", name());
        auto image = loadAgentPlanImageFile(p.plan_image);
        fatal_if(!image, "%s: plan image %s cannot be loaded", name(),
                 p.plan_image);
        fatal_if(!image->surrogateRegistry(),
                 "%s: plan image has no surrogate profile registry", name());
        for (const AgentControlAction &action : image->controlActions())
            fatal_if(action.triggerKind >
                         static_cast<uint8_t>(
                             ControlTriggerEvent::AfterGenerateTerminal),
                     "%s: control ordinal %u uses trigger kind %u that Gate "
                     "4 does not implement (E_AGENT_PLAN)",
                     name(), action.controlOrdinal, action.triggerKind);
        for (const AgentCommandRecord &record : image->commands()) {
            if (record.commandKind != kAgentCommandCancel)
                continue;
            const AgentCommandRecord *target = nullptr;
            for (const AgentCommandRecord &candidate : image->commands())
                if (candidate.requestId == record.targetRequestId) {
                    target = &candidate;
                    break;
                }
            fatal_if(target == nullptr ||
                         target->commandKind != kAgentCommandGenerate,
                     "%s: CANCEL request %llu targets non-GENERATE command "
                     "(E_AGENT_PLAN)",
                     name(), static_cast<unsigned long long>(
                                 record.targetRequestId));
        }
        coordinator = std::make_unique<ControlTriggerCoordinator>(
            image->controlActions().size());
        fatal_if(!coordinator->loadFromImage(*image),
                 "%s: plan image control actions cannot be loaded (E_AGENT_PLAN)",
                 name());
        controlRequestByIndex.assign(image->controlActions().size(), 0);
        for (size_t index = 0; index < image->controlActions().size();
             ++index)
            for (const AgentCommandRecord &record : image->commands())
                if (record.controlOrdinal ==
                        image->controlActions()[index].controlOrdinal &&
                    record.commandKind != kAgentCommandGenerate) {
                    controlIndexByRequest.emplace(record.requestId, index);
                    controlRequestByIndex[index] = record.requestId;
                    break;
                }
        requestCount = image->generateCommandCount();
        fatal_if(requestCount == 0,
                 "%s: plan image has no GENERATE commands", name());
        fatal_if(p.request_id_capacity < image->commands().size(),
                 "%s: request ID capacity %u cannot cover the plan's %zu "
                 "commands (E_CAPACITY_PLAN)",
                 name(), p.request_id_capacity, image->commands().size());
        fatal_if(p.host_available_fraction_q16 == 0 ||
                 p.host_available_fraction_q16 > 65536,
                 "%s: host_available_fraction_q16 out of range", name());
        fatal_if(p.host_compile_slots == 0 || p.host_test_slots == 0 ||
                 p.host_log_parse_slots == 0 ||
                 p.host_service_queue_depth == 0 ||
                 p.agent_object_table_entries == 0,
                 "%s: synthetic host service capacity must be nonzero",
                 name());
        fatal_if(p.host_local_io_enabled && p.host_local_io_bytes_per_ns == 0,
                 "%s: host_local_io_bytes_per_ns must be nonzero", name());
        const uint64_t ticksPerSecond = sim_clock::as_int::s;
        AgentWorkloadManagerConfig managerConfig;
        managerConfig.hostComputeTokens = p.host_compute_tokens;
        managerConfig.hostAvailableFractionQ16 =
            p.host_available_fraction_q16;
        managerConfig.hostCompileSlots = p.host_compile_slots;
        managerConfig.hostTestSlots = p.host_test_slots;
        managerConfig.hostLogParseSlots = p.host_log_parse_slots;
        managerConfig.hostServiceQueueDepth = p.host_service_queue_depth;
        managerConfig.hostWeightCompile = p.host_weight_compile;
        managerConfig.hostWeightTest = p.host_weight_test;
        managerConfig.hostWeightLogParse = p.host_weight_log_parse;
        managerConfig.hostAgingThresholdNs = p.host_aging_threshold_ns;
        managerConfig.hostLocalIoEnabled = p.host_local_io_enabled;
        managerConfig.hostLocalIoFixedNs = p.host_local_io_fixed_ns;
        managerConfig.hostLocalIoBytesPerNs = p.host_local_io_bytes_per_ns;
        managerConfig.agentObjectTableEntries =
            p.agent_object_table_entries;
        cancelJoinCapacity = p.cancel_join_entries;
        managerConfig.stopAfterCompletedTasks =
            p.stop_after_completed_tasks;
        managerConfig.stopAcceptingEnabled = p.stop_accepting_enabled;
        managerConfig.stopAcceptingAtTick = p.stop_accepting_at_tick;
        if (!p.host_fault_site.empty()) {
            if (p.host_fault_site == "object_produce")
                managerConfig.hostFaultSite =
                    agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE;
            else if (p.host_fault_site == "object_read")
                managerConfig.hostFaultSite =
                    agent_abi::HostLocalFaultSiteV1::OBJECT_READ;
            else
                fatal("%s: unknown host_fault_site %s", name(),
                      p.host_fault_site);
        }
        managerConfig.hostFaultTask = p.host_fault_task;
        managerConfig.hostFaultRound = p.host_fault_round;
        managerConfig.clockTicksPerSecond = ticksPerSecond;
        auto manager = std::make_unique<AgentWorkloadManager>(
            std::move(*image), managerConfig,
            AgentWorkloadFactsSink{
                [this](const std::string &kind, const std::string &object,
                       uint64_t requestId, const std::string &status) {
                    recordSemantic(kind, object, std::nullopt, requestId,
                                   std::nullopt, status);
                },
                [this](const std::string &metricName, uint64_t value) {
                    recorder->setMetric(metricName, value);
                }});
        fatal_if(manager->hostResources().availableTokens() == 0,
                 "%s: host token pool resolves to zero tokens", name());
        workloadManager = manager.get();
        requestSource = std::move(manager);
        return;
    }
    fatal_if(requestCount == 0,
             "%s: request count must be positive", name());
    fatal_if(requestIdCapacity < requestCount,
             "%s: request ID capacity is too small", name());
    requestSource = std::make_unique<ProtocolProfileRequestSource>(
        requestCount);
}

void
AgentAxiDriver::init()
{
    ClockedObject::init();
    fatal_if(!master || !target || !recorder,
             "%s: Gate3 driver has an unbound dependency", name());
    target->registerWriteCommitObserver(this);
    recorder->registerPhaseParticipant(this);
    recorder->setMetric("driver_cq_consumer_seq", 0);
    recorder->setMetric("driver_cq_ack_seq", 0);
    recorder->setMetric("live_submissions", 0);
    recorder->setMetric("ack_wait_b", 0);
    recorder->setMetric("stale_cq_entries", 0);
    recorder->setMetric("cq_request_mismatch", 0);
    recorder->setMetric("cq_cookie_mismatch", 0);
    recorder->setMetric("cq_identity_retries", 0);
}

void
AgentAxiDriver::startup()
{
    ClockedObject::startup();
    if (coordinator)
        coordinator->onAuthoritativeEvent(
            ControlTriggerEvent::ScenarioStart, ControlAnchor{}, 0, curTick());
    schedule(&tickEvent, clockEdge() + 1);
}

void
AgentAxiDriver::scheduleTick()
{
    if (!tickEvent.scheduled())
        schedule(&tickEvent, clockEdge() + 1);
}

Tick
AgentAxiDriver::clockEdgeAtOrAfter(Tick deadline) const
{
    const Tick aligned = clockEdge();
    if (deadline <= aligned)
        return aligned == curTick() ? nextCycle() : aligned;
    return clockEdge(ticksToCycles(deadline - aligned));
}

bool
AgentAxiDriver::managerEdgeDue() const
{
    return workloadManager && !recorder->fatalRecorded() &&
        clockEdge() == curTick();
}

void
AgentAxiDriver::scheduleManagerWake(std::optional<Tick> deadline)
{
    if (!deadline)
        return;
    const Tick edge = clockEdgeAtOrAfter(*deadline);
    if (!managerWakeEvent.scheduled()) {
        schedule(&managerWakeEvent, edge);
        return;
    }
    if (edge < managerWakeEvent.when())
        reschedule(&managerWakeEvent, edge, true);
}

bool
AgentAxiDriver::mode(const char *value) const
{
    return profile == value;
}

uint64_t
AgentAxiDriver::requestIdForCurrent() const
{
    return currentRequestId;
}

uint64_t
AgentAxiDriver::cookieForCurrent() const
{
    return currentCookie;
}

uint64_t
AgentAxiDriver::sqSequenceForCurrent() const
{
    return currentSqSequence;
}

uint64_t
AgentAxiDriver::sqTailForCurrent() const
{
    return currentSqSequence + 1;
}

uint64_t
AgentAxiDriver::cqSequenceForCurrent() const
{
    return currentCqSequence;
}

uint64_t
AgentAxiDriver::sqAddress(uint64_t sequence) const
{
    return ringLayout.sqAddress(SqSeq(sequence));
}

uint64_t
AgentAxiDriver::parameterAddress(uint64_t sequence) const
{
    if (mode("PARAMETER_CROSS_4K") && sequence == 0)
        return rangeEnd(hostBase, 0x10fc0);
    return rangeEnd(hostBase, HostParameterBase + sequence * 0x400);
}

uint64_t
AgentAxiDriver::promptAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostPromptBase + sequence * 0x100);
}

uint64_t
AgentAxiDriver::outputAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostOutputBase + sequence * 0x100);
}

uint64_t
AgentAxiDriver::metadataAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostMetadataBase + sequence * 0x200);
}

uint64_t
AgentAxiDriver::cqAddress(uint64_t sequence) const
{
    return ringLayout.cqAddress(CqSeq(sequence));
}

uint64_t
AgentAxiDriver::msiAddress(uint64_t sequence) const
{
    return msiBase + sequence * 0x100;
}

std::vector<uint8_t>
AgentAxiDriver::makePayload(uint64_t seed, uint64_t bytes) const
{
    std::vector<uint8_t> result(bytes);
    for (uint64_t index = 0; index < bytes; ++index)
        result[index] = static_cast<uint8_t>((seed + index * 13) & 0xff);
    return result;
}

std::vector<uint8_t>
AgentAxiDriver::encodeSq(uint64_t sequence, uint64_t requestId,
                        uint64_t cookie) const
{
    agent_abi::SqDescriptor value;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.opcode = static_cast<uint16_t>(agent_abi::SqOpcode::GENERATE);
    value.sq_seq = sequence;
    value.request_id = requestId;
    value.session_id = 0x4000 + sequence;
    value.parameter_block_addr = parameterAddress(sequence);
    value.parameter_block_bytes =
        static_cast<uint32_t>(currentParameterBlockBytes);
    value.program_id = 1;
    value.profile_id = 1;
    value.completion_cookie = cookie;
    value.qos = static_cast<uint8_t>(sequence & 0xf);
    if (mode("SQ_SEQUENCE_MISMATCH")) {
        const auto next = checkedSequenceAdd(SqSeq(sequence), 1);
        fatal_if(!next, "%s: SQ sequence fault candidate overflows", name());
        value.sq_seq = next->value();
    }
    std::vector<uint8_t> data = toVector(agent_abi::encodeSqDescriptor(value));
    if (mode("SQ_CRC") || mode("EARLY_SEQ_ONLY_CQ") ||
        mode("SQ_CRC_IDENTITY") ||
        (mode("SAME_TICK_SEQ_ONLY") && sequence == 1))
        data[0] ^= 0x5a;
    return data;
}


std::vector<uint8_t>
AgentAxiDriver::encodeParameter(uint64_t requestId) const
{
    agent_abi::ParameterHeader value;
    value.magic = 0x504e4741;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.header_bytes = agent_abi::kParameterHeaderBytes;
    value.input_addr = promptAddress(currentSqSequence);
    value.input_bytes = dataBusBytes;
    value.input_tokens = 16;
    value.output_addr = outputAddress(currentSqSequence);
    value.output_capacity_bytes = mode("CAPACITY_OUTPUT") ? 0 : dataBusBytes;
    value.output_metadata_addr = metadataAddress(currentSqSequence);
    value.output_metadata_capacity_bytes = mode("CAPACITY_METADATA") ?
        0 : agent_abi::kOutputMetadataBytes +
            ((mode("METADATA_TLV_TAIL") ||
              mode("METADATA_TLV_SIZE_MISMATCH")) ? 72 : 0);
    value.max_output_tokens = 16;
    value.user_id = 7;
    value.task_seq = static_cast<uint32_t>(currentSqSequence);
    value.qos = static_cast<uint8_t>(currentSqSequence & 0xf);
    value.request_kind = 1;
    value.target_request_id = 0;
    value.binding_table_offset = agent_abi::kParameterHeaderBytes;
    value.binding_count = 0;
    value.binding_record_bytes = 24;
    value.extension_offset = agent_abi::kParameterHeaderBytes;
    value.requested_profile_key = 1;

    std::vector<uint8_t> inputDigest(32, 0);
    for (size_t i = 0; i < inputDigest.size(); ++i)
        inputDigest[i] = static_cast<uint8_t>((0x20 + currentSqSequence + i * 13));
    std::vector<uint8_t> workloadDigest(32, 0);
    for (size_t i = 0; i < workloadDigest.size(); ++i)
        workloadDigest[i] = static_cast<uint8_t>((0x57 + i * 7));
    std::vector<uint8_t> chunk(8, 0);
    agent_abi::wrU64(chunk.data(), 16);

    std::vector<uint8_t> tail;
    auto push_tlv_header = [&tail](uint16_t type, uint16_t flags,
                                   uint32_t payload_bytes) {
        tail.push_back(static_cast<uint8_t>(type));
        tail.push_back(static_cast<uint8_t>(type >> 8));
        tail.push_back(static_cast<uint8_t>(flags));
        tail.push_back(static_cast<uint8_t>(flags >> 8));
        tail.push_back(static_cast<uint8_t>(payload_bytes));
        tail.push_back(static_cast<uint8_t>(payload_bytes >> 8));
        tail.push_back(static_cast<uint8_t>(payload_bytes >> 16));
        tail.push_back(static_cast<uint8_t>(payload_bytes >> 24));
    };
    tail.reserve(8 + 32 + 8 + 8 + 8 + 32);
    push_tlv_header(agent_abi::kTlvTypeINPUT_DIGEST,
                    agent_abi::kTlvFlagsREQUIRED, 32);
    tail.insert(tail.end(), inputDigest.begin(), inputDigest.end());
    push_tlv_header(agent_abi::kTlvTypeOUTPUT_CHUNK_BYTES,
                    agent_abi::kTlvFlagsREQUIRED, 8);
    tail.insert(tail.end(), chunk.begin(), chunk.end());
    push_tlv_header(agent_abi::kTlvTypeWORKLOAD_ID_DIGEST,
                    agent_abi::kTlvFlagsREQUIRED, 32);
    tail.insert(tail.end(), workloadDigest.begin(), workloadDigest.end());
    value.extension_bytes = static_cast<uint32_t>(tail.size());
    std::vector<uint8_t> bindings;
    const bool bindingProfile =
        mode("PARAMETER_BINDING_END") || mode("PARAMETER_BINDING_OOB") ||
        mode("PARAMETER_BINDING_HEADER_OVERLAP") ||
        mode("PARAMETER_BINDING_EXTENSION_OVERLAP") ||
        mode("PARAMETER_BINDING_RECORD_SIZE");
    if (bindingProfile) {
        agent_abi::BindingRecord binding;
        binding.symbol_id = 1;
        binding.kind = agent_abi::kBindingKindHOST_INPUT;
        binding.flags = agent_abi::kBindingFlagsREAD;
        binding.address = value.input_addr;
        binding.bytes = value.input_bytes;
        bindings = toVector(agent_abi::encodeBindingRecord(binding));
        value.binding_count = 1;
        value.binding_table_offset = agent_abi::kParameterHeaderBytes +
            static_cast<uint32_t>(tail.size());
        if (mode("PARAMETER_BINDING_OOB"))
            value.binding_table_offset = 4096;
        else if (mode("PARAMETER_BINDING_HEADER_OVERLAP"))
            value.binding_table_offset = agent_abi::kParameterHeaderBytes - 8;
        else if (mode("PARAMETER_BINDING_EXTENSION_OVERLAP"))
            value.binding_table_offset = agent_abi::kParameterHeaderBytes;
        if (mode("PARAMETER_BINDING_RECORD_SIZE"))
            value.binding_record_bytes = 16;
    }
    value.total_bytes = agent_abi::kParameterHeaderBytes +
        static_cast<uint32_t>(tail.size() + bindings.size());

    std::vector<uint8_t> data =
        toVector(agent_abi::encodeParameterHeader(value));
    data.insert(data.end(), tail.begin(), tail.end());
    data.insert(data.end(), bindings.begin(), bindings.end());
    uint32_t crc_offset = agent_abi::kParameterHeaderCrc32Offset;
    std::fill(data.begin() + crc_offset, data.begin() + crc_offset + 4, 0);
    uint32_t crc = agent_abi::crc32c(data.data(), data.size());
    for (int i = 0; i < 4; ++i)
        data[crc_offset + i] = static_cast<uint8_t>(crc >> (8 * i));
    if (mode("PARAMETER_CRC") || mode("PARAMETER_ERROR_IDENTITY"))
        data[1] ^= 0x33;
    if (mode("PARAMETER_BOUNDS"))
        data[12] ^= 0x01;
    return data;
}

std::vector<uint8_t>
AgentAxiDriver::encodeControl(uint64_t sequence) const
{
    std::vector<uint8_t> data(controlBytes, 0);
    if (controlBytes >= 8)
        agent_abi::wrU64(data.data(), sequence);
    return data;
}

void
AgentAxiDriver::storeBytes(uint64_t address,
                           const std::vector<uint8_t> &data)
{
    fatal_if(data.empty(), "%s: local store has no data", name());
    for (size_t index = 0; index < data.size(); ++index) {
        const uint64_t current = rangeEnd(address, index);
        fatal_if(!target->containsMemoryAddress(current),
                 "%s: local store escapes host memory", name());
        target->writeMemoryByte(current, data[index]);
    }
}

Gate3WriteWork
AgentAxiDriver::makeWrite(
    const std::string &object, const std::string &control,
    std::optional<uint64_t> absoluteSeq, std::optional<uint64_t> requestId,
    std::optional<uint64_t> cookie, uint64_t address, uint32_t axiId,
    const std::vector<uint8_t> &data, uint8_t qos) const
{
    fatal_if(data.empty(), "%s: empty Gate3 write", name());
    Gate3WriteWork work;
    work.object = object;
    work.control = control;
    work.direction = "DRIVER_TO_NPU";
    work.absoluteSeq = absoluteSeq;
    work.requestId = requestId;
    work.cookie = cookie;
    work.address = address;
    work.axiId = axiId;
    work.qos = qos;
    work.data = data;
    work.segments = transferPlanner.plan(address, data.size(), axiId, qos);
    activateWriteSegment(work, transferPlanner);
    return work;
}

Gate3ReadWork
AgentAxiDriver::makeRead(
    const std::string &object, const std::string &control,
    std::optional<uint64_t> absoluteSeq, std::optional<uint64_t> requestId,
    std::optional<uint64_t> cookie, uint64_t address, uint64_t bytes,
    uint32_t axiId, uint8_t qos) const
{
    fatal_if(bytes == 0, "%s: empty Gate3 read", name());
    Gate3ReadWork work;
    work.object = object;
    work.control = control;
    work.direction = "DRIVER_TO_NPU";
    work.absoluteSeq = absoluteSeq;
    work.requestId = requestId;
    work.cookie = cookie;
    work.address = address;
    work.axiId = axiId;
    work.qos = qos;
    work.bytes = bytes;
    work.segments = transferPlanner.plan(address, bytes, axiId, qos);
    activateReadSegment(work);
    return work;
}

void
AgentAxiDriver::recordAxi(const Gate3WriteWork &work, const char *channel,
                          uint64_t bytes, const std::string &response,
                          const std::string &wstrb,
                          std::optional<uint64_t> beatAddress)
{
    Gate3Event event;
    event.tick = curTick();
    event.phase = 0;
    event.kind = "AXI_ACCEPT";
    event.object = objectForControl(work.control);
    event.absoluteSeq = work.absoluteSeq;
    event.requestId = work.requestId;
    event.cookie = work.cookie;
    event.txn = work.txn;
    event.channel = channel;
    event.direction =
        std::string(channel) == "AW" || std::string(channel) == "W" ?
        work.direction : reverseDirection(work.direction);
    event.control = work.control;
    event.axiId = work.axiId;
    event.address = beatAddress.value_or(work.request.address);
    event.size = work.request.size;
    event.response = response;
    event.bytes = bytes;
    event.wstrb = wstrb;
    setRingIdentity(event, sqDepth, cqDepth);
    recorder->record(std::move(event));
}

void
AgentAxiDriver::recordAxi(const Gate3ReadWork &work, const char *channel,
                          uint64_t bytes, const std::string &response,
                          std::optional<uint64_t> beatAddress)
{
    Gate3Event event;
    event.tick = curTick();
    event.phase = 0;
    event.kind = "AXI_ACCEPT";
    event.object = objectForControl(work.control);
    event.absoluteSeq = work.absoluteSeq;
    event.requestId = work.requestId;
    event.cookie = work.cookie;
    event.txn = work.txn;
    event.channel = channel;
    event.direction = std::string(channel) == "AR" ? work.direction :
        reverseDirection(work.direction);
    event.control = work.control;
    event.axiId = work.axiId;
    event.address = beatAddress.value_or(work.request.address);
    event.size = work.request.size;
    event.response = response;
    event.bytes = bytes;
    setRingIdentity(event, sqDepth, cqDepth);
    recorder->record(std::move(event));
}

void
AgentAxiDriver::recordSemantic(
    const std::string &kind, const std::string &object,
    std::optional<uint64_t> absoluteSeq, std::optional<uint64_t> requestId,
    std::optional<uint64_t> cookie, const std::string &status)
{
    Gate3Event event;
    event.tick = curTick();
    event.phase = kind == "DOORBELL_TARGET_COMMIT" ||
            kind == "MSI_TARGET_COMMIT" ||
            kind == "CQ_ACK_TARGET_COMMIT" ? 1 :
        kind == "FATAL" ? 2 :
        kind == "CORE_START" || kind == "IRQ_DELIVER" ? 4 : 3;
    if (kind == "LOCAL_VISIBLE" || kind == "RELEASE_FENCE_DONE" ||
        kind == "LOCAL_READ")
        event.phase = 0;
    event.kind = kind;
    event.object = object;
    event.absoluteSeq = absoluteSeq;
    event.requestId = requestId;
    event.cookie = cookie;
    event.status = status;
    if (absoluteSeq && (object == "SQ_ENTRY" || object == "CQ_ENTRY")) {
        const uint64_t depth = object == "SQ_ENTRY" ? sqDepth : cqDepth;
        event.slot = static_cast<uint32_t>(*absoluteSeq % depth);
        event.generation = *absoluteSeq / depth;
    }
    recorder->record(std::move(event));
}

void
AgentAxiDriver::requestFatal(agent_abi::DetailCode value)
{
    const agent_abi::InvariantSiteV1 site =
        value == agent_abi::E_REQUEST_CONTEXT_FULL ?
        agent_abi::InvariantSiteV1::REQUEST_CONTEXT_CAPACITY_OWNERSHIP :
        value == agent_abi::E_SQ_MALFORMED ?
        agent_abi::InvariantSiteV1::SQ_MALFORMED_INTERNAL :
        agent_abi::InvariantSiteV1::LEDGER_OWNERSHIP;
    const auto projection = agent_abi::invariantSiteProjectionV1(site);
    fatal_if(projection.detailCode != value,
             "%s: detail code does not match invariant site", name());
    recorder->requestInvariant(
        site, 0, UINT32_MAX, gate3InternalKey(site),
        Gate3PhysicalSourceTokenV1{
            agent_abi::PhysicalSourceKindV1::INTERNAL_EDGE, 0,
            projection.componentKind, 0, UINT32_MAX, UINT64_MAX, 0});
    scheduleTick();
}

void
AgentAxiDriver::requestFault(
    agent_abi::FaultSiteV1 site, std::vector<uint8_t> objectKey,
    uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken)
{
    if (site ==
        agent_abi::FaultSiteV1::DOORBELL_AMBIGUOUS_OR_ACCEPTANCE_CONFLICT) {
        recorder->setMetric("fatal_publications", 1);
        recorder->setMetric("ambiguous_publications",
                            mode("SAME_EDGE_ACCEPTANCE_CONFLICT") ? 0 : 1);
    }
    recorder->requestFault(
        site, 0, UINT32_MAX, std::move(objectKey), issueOrdinal,
        sourceToken);
    scheduleTick();
}

void
AgentAxiDriver::recordFatalPublication(
    const PendingWriteResponse &response)
{
    const PendingSqPublication *pending = ledger.pending();
    fatal_if(!pending,
             "%s: fatal publication has no retained record", name());
    std::vector<uint64_t> requestIds;
    requestIds.reserve(pending->requestIds.size());
    for (const RequestId requestId : pending->requestIds)
        requestIds.push_back(requestId.value());
    const uint64_t ordinal = axiIssueOrdinal(response.work.txn);
    recorder->recordFatalPublication(
        (planDrivenMode ? submitSqSequence : currentSqSequence) + 1, ordinal,
        pending->base.value(),
        pending->tail.value(), std::move(requestIds),
        recorder->hasEvent(
            "DOORBELL_TARGET_COMMIT", "DOORBELL", pending->tail.value()),
        response.response == axi::AxiResp::Okay ? "B_OK" : "B_ERROR",
        response.response != axi::AxiResp::Okay &&
            !mode("SAME_EDGE_ACCEPTANCE_CONFLICT"),
        axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                       false, response.work.txn),
        responseSourceToken(
            agent_abi::FatalComponentKindV1::HOST_AGENT,
            response.responseOrdinal, response.work.axiId));
    recorder->setMetric("fatal_publications", 1);
    recorder->setMetric(
        "ambiguous_publications",
        response.response != axi::AxiResp::Okay &&
            !mode("SAME_EDGE_ACCEPTANCE_CONFLICT") ? 1 : 0);
}

void
AgentAxiDriver::onGate3FatalCut(Tick tick)
{
    if (pendingWriteResponse &&
        pendingWriteResponse->work.control == "SQ_DOORBELL" &&
        pendingWriteResponse->work.requestId)
        recordFatalPublication(*pendingWriteResponse);
    pendingWriteResponse.reset();
    stage = Stage::Drain;
    drainUntilTick = tick + drainCycles * clockPeriod();
    if (!fatalDrainEvent.scheduled())
        schedule(&fatalDrainEvent, clockEdge() + 1);
    scheduleTick();
}

void
AgentAxiDriver::onGate3NormalCommit(Tick tick)
{
    (void)tick;
    if (!pendingWriteResponse)
        return;
    const PendingWriteResponse response = std::move(*pendingWriteResponse);
    pendingWriteResponse.reset();
    const Gate3WriteWork &work = response.work;
    if (work.control == "SQ_DOORBELL" && !work.requestId) {
        stage = Stage::Drain;
        drainUntilTick = curTick() + drainCycles * clockPeriod();
        scheduleTick();
    } else if (work.control == "SQ_DOORBELL") {
        handleDoorbellResponse(work, response.response);
    } else {
        handleAckResponse(work, response.response);
    }
}


void
AgentAxiDriver::prepareRequest()
{
    if (runtimeExhausted(completedRequests)) {
        stage = Stage::Drain;
        if (drainUntilTick == 0)
            drainUntilTick = curTick() + drainCycles * clockPeriod();
        scheduleTick();
        return;
    }
    if (currentPrepared)
        return;
    if (mode("EMPTY_DOORBELL") && !emptySent) {
        emptySent = true;
        startDoorbellProbe(0, 0, 0);
        return;
    }
    if (ledger.available() == 0) {
        scheduleTick();
        return;
    }
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
    if (intents.size() > 1) {
        std::vector<RequestId> requestIds;
        requestIds.reserve(intents.size());
        for (const AgentSubmissionIntent &intent : intents)
            requestIds.emplace_back(intent.requestId);
        const auto base = ledger.reserveBatch(requestIds);
        if (!base) {
            requestFatal(agent_abi::E_REQUEST_CONTEXT_FULL);
            return;
        }
        for (size_t index = 0; index < intents.size(); ++index) {
            currentSqSequence = base->value() + index;
            currentRequestId = intents[index].requestId;
            currentCookie = intents[index].completionCookie;
            issuedRequestIds.insert(currentRequestId);
            currentIntent = intents[index];
            prepareLocalRecords();
        }
        submissionAttempts += intents.size();
        currentDoorbellTail = base->value() + intents.size();
        currentPrepared = true;
        stage = Stage::Fence;
        return;
    }
    currentRequestId = intents.front().requestId;
    currentCookie = intents.front().completionCookie;
    const auto sequence = ledger.reserve(RequestId(currentRequestId));
    if (!sequence) {
        requestFatal(agent_abi::E_REQUEST_CONTEXT_FULL);
        return;
    }
    issuedRequestIds.insert(currentRequestId);
    currentSqSequence = sequence->value();
    currentDoorbellTail = sqTailForCurrent();
    ++submissionAttempts;
    if (mode("SQ_FULL") && !sqBackpressureObserved) {
        const auto nextRequestId = checkedSequenceAdd(
            RequestId(currentRequestId), 1);
        fatal_if(!nextRequestId,
                 "%s: request ID counter exhausted", name());
        if (ledger.available() == 0 && !ledger.reserve(*nextRequestId)) {
            recorder->setMetric("sq_backpressure",
                                recorder->metric("sq_backpressure") + 1);
            sqBackpressureObserved = true;
        }
    }
    currentIntent = intents.front();
    prepareLocalRecords();
    currentPrepared = true;
    stage = Stage::Fence;
}


bool
AgentAxiDriver::runtimeExhausted(uint64_t completedRequests) const
{
    if (!requestSource->exhausted(completedRequests))
        return false;
    if (!workloadManager || !coordinator)
        return true;
    return !workloadManager->controlIntentsPending() &&
        coordinator->allTerminal();
}

void
AgentAxiDriver::driveWrite()
{
    if (!activeWrite)
        return;
    Gate3WriteWork &work = *activeWrite;
    if (!work.awAccepted) {
        if (!master->tryAcceptAw(work.request)) {
            scheduleTick();
            return;
        }
        work.awAccepted = true;
        const auto packet = master->lastAcceptedAw();
        work.txn = packet.meta.txnUid;
        recordAxi(work, "AW", 0, "");
    }
    while (work.nextBeat < work.beats.size()) {
        const size_t index = work.nextBeat;
        if (!master->tryAcceptW(work.beats[index])) {
            scheduleTick();
            return;
        }
        std::ostringstream strobe;
        strobe << std::hex << work.beats[index].byteStrobe;
        recordAxi(work, "W", work.semanticBytes[index], "", strobe.str(),
                  axi::axiBeatAddress(work.request, index));
        ++work.nextBeat;
    }
    if (work.control == "CQ_HEAD_ACK") {
        const uint64_t ordinal = axiIssueOrdinal(work.txn);
        recorder->recordHostAckIssue(
            ordinal, work.absoluteSeq.value_or(0), work.axiId,
            axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                           false, work.txn));
    }
    if (planDrivenMode) {
        if (work.control == "SQ_DOORBELL")
            submitPhase = SubmitPhase::DoorbellResponse;
        else
            completePhase = CompletePhase::AckResponse;
    } else {
        stage = work.control == "SQ_DOORBELL" ?
            (work.requestId ? Stage::DoorbellResponse :
                              Stage::DoorbellProbeResponse) :
            Stage::AckResponse;
    }
    scheduleTick();
}

void
AgentAxiDriver::consumeB()
{
    if (!activeWrite || !activeWrite->awAccepted ||
        activeWrite->nextBeat != activeWrite->beats.size()) {
        scheduleTick();
        return;
    }
    if (activeWrite->control == "SQ_DOORBELL" &&
        activeWrite->requestId && controlDoorbellBHoldTicks != 0 &&
        (planDrivenMode ? submitIntent : currentIntent).planDriven &&
        (planDrivenMode ? submitIntent : currentIntent).commandKind !=
            kAgentCommandGenerate &&
        curTick() < controlDoorbellBHeldUntil &&
        !ledger.pendingCompletionEvidence()) {
        scheduleTick();
        return;
    }
    axi::AxiBBeat response;
    if (!master->tryConsumeB(response)) {
        scheduleTick();
        return;
    }
    fatal_if(responseIngressOrdinal == UINT64_MAX,
             "%s: response observation ordinal exhausted", name());
    ++responseIngressOrdinal;
    recordAxi(*activeWrite, "B", 0, responseName(response.resp));
    if (response.resp == axi::AxiResp::Okay &&
        activeWrite->segmentIndex + 1 < activeWrite->segments.size()) {
        ++activeWrite->segmentIndex;
        activateWriteSegment(*activeWrite, transferPlanner);
        stage = activeWrite->control == "SQ_DOORBELL" ?
            (activeWrite->requestId ? Stage::Doorbell : Stage::DoorbellProbe) :
            Stage::Ack;
        scheduleTick();
        return;
    }
    const Gate3WriteWork work = *activeWrite;
    activeWrite.reset();
    if (work.control == "CQ_HEAD_ACK") {
        const uint64_t ordinal = axiIssueOrdinal(work.txn);
        const bool targetCommitted = recorder->hasEvent(
            "CQ_ACK_TARGET_COMMIT", "CQ_ACK",
            work.absoluteSeq.value_or(0));
        recorder->recordHostAckTerminal(
            ordinal,
            response.resp == axi::AxiResp::Okay ? "B_OK" : "B_ERROR",
            targetCommitted,
            responseSourceToken(
                agent_abi::FatalComponentKindV1::HOST_AGENT,
                responseIngressOrdinal, response.axiId));
    }
    if (recorder->fatalRecorded()) {
        if (work.control == "SQ_DOORBELL" && work.requestId &&
            ledger.pending()) {
            recordFatalPublication(PendingWriteResponse{
                work, response.resp, responseIngressOrdinal});
        }
        stage = Stage::Drain;
        return;
    }
    if (work.control == "SQ_DOORBELL" && !work.requestId) {
        if (response.resp != axi::AxiResp::Okay) {
            requestFault(
                agent_abi::FaultSiteV1::DOORBELL_AMBIGUOUS_OR_ACCEPTANCE_CONFLICT,
                gate3PublicationKey(1, 0, work.absoluteSeq.value_or(0), 0),
                axiIssueOrdinal(work.txn),
                axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                               false, work.txn));
            return;
        }
    } else if (work.control == "SQ_DOORBELL" &&
               response.resp != axi::AxiResp::Okay &&
               !mode("PROVEN_NO_EFFECT_B_ERROR") &&
               !(controlDoorbellFaultTail != 0 &&
                 work.absoluteSeq == controlDoorbellFaultTail)) {
        handleDoorbellResponse(work, response.resp);
        return;
    } else if (work.control == "CQ_HEAD_ACK" &&
               response.resp != axi::AxiResp::Okay &&
               !(futureAckProbe && response.resp == axi::AxiResp::DecErr)) {
        handleAckResponse(work, response.resp);
        return;
    }
    fatal_if(pendingWriteResponse,
             "%s: driver write response proposal already exists", name());
    pendingWriteResponse = PendingWriteResponse{
        work, response.resp, responseIngressOrdinal};
    recorder->requestNormalCommit();
}

void
AgentAxiDriver::consumeR()
{
    if (!activeRead)
        return;
    Gate3ReadWork &work = *activeRead;
    if (!work.accepted) {
        if (!master->tryAcceptAr(work.request)) {
            scheduleTick();
            return;
        }
        work.accepted = true;
        const auto packet = master->lastAcceptedAr();
        work.txn = packet.meta.txnUid;
        recordAxi(work, "AR", 0, "");
    }
    axi::AxiRBeat response;
    if (!master->tryConsumeR(response)) {
        scheduleTick();
        return;
    }
    const uint64_t transferBytes = uint64_t{1} << work.request.size;
    recordAxi(work, "R", transferBytes, responseName(response.resp),
              axi::axiBeatAddress(work.request, work.beatIndex));
    transferPlanner.appendReadBeat(work.segments.at(work.segmentIndex),
                                   work.beatIndex,
                                   response.functionalData, work.data);
    work.bytesConsumed += transferBytes;
    work.sawError = work.sawError || response.resp != axi::AxiResp::Okay;
    ++work.beatIndex;
    const bool segmentComplete =
        work.beatIndex == work.request.beatCount;
    work.sawError = work.sawError || response.last != segmentComplete;
    if (response.last || segmentComplete) {
        if (!work.sawError &&
            work.segmentIndex + 1 < work.segments.size()) {
            ++work.segmentIndex;
            activateReadSegment(work);
            scheduleTick();
            return;
        }
        if (recorder->fatalRecorded()) {
            activeRead.reset();
            stage = Stage::Drain;
            return;
        }
        if (work.control == "CQ_ENTRY_READ")
            handleCqRead();
        else
            handleMetadataRead();
    } else {
        scheduleTick();
    }
}

void
AgentAxiDriver::handleDoorbellResponse(const Gate3WriteWork &work,
                                       axi::AxiResp response)
{
    const uint64_t issueSequence = planDrivenMode ?
        submitSqSequence : currentSqSequence;
    const uint64_t issueRequestId = planDrivenMode ?
        submitRequestId : currentRequestId;
    const uint64_t issueCookie = planDrivenMode ? submitCookie : currentCookie;
    const uint64_t issueDoorbellTail = planDrivenMode ?
        submitDoorbellTail : currentDoorbellTail;
    if (response == axi::AxiResp::Okay) {
        const auto result = ledger.resolve(response);
        if (result != PublicationResolution::Committed) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
        CancelJoinRecord *joinByCommand =
            findCancelJoinByCommand(issueRequestId);
        if (joinByCommand)
            joinByCommand->doorbellCommitted = true;
        recordSemantic("PUBLICATION_COMMIT", "DOORBELL", issueDoorbellTail,
                       issueRequestId, issueCookie);
        if (planDrivenMode && submitIntent.planDriven &&
                submitIntent.commandKind == kAgentCommandGenerate)
            acceptedGenerateRequests.insert(issueRequestId);
        if (planDrivenMode) {
            submitPhase = SubmitPhase::Idle;
            scheduleTick();
        } else {
            stage = Stage::Completion;
            scheduleTick();
        }
        return;
    }
    const bool proven = mode("PROVEN_NO_EFFECT_B_ERROR") ||
        (controlDoorbellFaultTail != 0 &&
         work.absoluteSeq == controlDoorbellFaultTail);
    const auto result = ledger.resolve(response, proven);
    if (result == PublicationResolution::RolledBack) {
        recordSemantic("PUBLICATION_ROLLBACK", "DOORBELL", issueDoorbellTail,
                       issueRequestId, issueCookie);
        recorder->setMetric("publication_rollbacks",
                            recorder->metric("publication_rollbacks") + 1);
        if (submitIntent.planDriven &&
                submitIntent.commandKind != kAgentCommandGenerate)
            noteControlLocalSubmitFailed(issueRequestId);
        if (proven && controlDoorbellFaultTail != 0 &&
                work.absoluteSeq == controlDoorbellFaultTail)
            controlDoorbellFaultTail = 0;
        if (planDrivenMode)
            submitPhase = SubmitPhase::Idle;
        else {
            currentPrepared = false;
            stage = Stage::Prepare;
        }
        scheduleTick();
        return;
    }
    const PendingSqPublication *pending = ledger.pending();
    fatal_if(!pending, "%s: fatal publication has no retained record", name());
    std::vector<uint64_t> requestIds;
    requestIds.reserve(pending->requestIds.size());
    for (const RequestId requestId : pending->requestIds)
        requestIds.push_back(requestId.value());
    const uint64_t ordinal = axiIssueOrdinal(work.txn);
    recorder->recordFatalPublication(
        issueSequence + 1, ordinal, pending->base.value(),
        pending->tail.value(), std::move(requestIds),
        recorder->hasEvent(
            "DOORBELL_TARGET_COMMIT", "DOORBELL", pending->tail.value()),
        "B_ERROR", !mode("SAME_EDGE_ACCEPTANCE_CONFLICT"),
        axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                       false, work.txn),
        responseSourceToken(
            agent_abi::FatalComponentKindV1::HOST_AGENT,
            responseIngressOrdinal, work.axiId));
    requestFault(
        agent_abi::FaultSiteV1::DOORBELL_AMBIGUOUS_OR_ACCEPTANCE_CONFLICT,
        gate3PublicationKey(
            issueSequence + 1, issueSequence, issueDoorbellTail,
            issueRequestId),
        ordinal,
        axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                       false, work.txn));
}

void
AgentAxiDriver::startCqRead()
{
    if (curTick() < localReadyTick) {
        schedule(&tickEvent, localReadyTick);
        return;
    }
    if (curTick() < cqReadReadyTick) {
        if (!tickEvent.scheduled())
            schedule(&tickEvent, cqReadReadyTick);
        return;
    }
    cqReadDelayPending = false;
    Gate3ReadWork work;
    work.object = "CQ_ENTRY";
    work.control = "CQ_ENTRY_READ";
    work.direction = "LOCAL";
    work.absoluteSeq = currentCqSequence;
    work.requestId = currentRequestId;
    work.cookie = currentCookie;
    work.address = cqAddress(currentCqSequence);
    work.bytes = agent_abi::kCqDescriptorBytes;
    if (mutateCancelCommandOrdinal != 0) {
        std::vector<uint8_t> raw(agent_abi::kCqDescriptorBytes);
        for (uint64_t index = 0; index < work.bytes; ++index)
            raw[index] = target->readMemoryByte(work.address + index);
        auto value = agent_abi::decodeCqDescriptor(raw.data());
        const auto control = controlIndexByRequest.find(value.request_id);
        if ((value.flags & agent_abi::kCqFlagsCONTROL_COMMAND) != 0 &&
                control != controlIndexByRequest.end() &&
                coordinator->action(control->second).controlOrdinal ==
                    mutateCancelCommandOrdinal &&
                value.status != static_cast<uint16_t>(
                                   mutateCancelCommandStatus)) {
            value.status = static_cast<uint16_t>(mutateCancelCommandStatus);
            const auto injected = agent_abi::encodeCqDescriptor(value);
            for (uint64_t index = 0; index < work.bytes; ++index)
                target->writeMemoryByte(work.address + index,
                                        injected[index]);
            recordSemantic("FAULT_INJECT", "CQ_ENTRY", value.cq_seq,
                           value.request_id, value.completion_cookie);
        }
    }
    std::vector<uint8_t> saved;
    if (mode("STALE_CQ_SEQ") || mode("CQ_REQUEST_MISMATCH") ||
        mode("CQ_COOKIE_MISMATCH")) {
        saved.resize(work.bytes);
        for (uint64_t index = 0; index < work.bytes; ++index)
            saved[index] = target->readMemoryByte(work.address + index);
        auto value = agent_abi::decodeCqDescriptor(saved.data());
        if (mode("STALE_CQ_SEQ"))
            ++value.cq_seq;
        else if (mode("CQ_REQUEST_MISMATCH"))
            ++value.request_id;
        else
            value.completion_cookie ^= 1;
        const auto injected = agent_abi::encodeCqDescriptor(value);
        for (uint64_t index = 0; index < work.bytes; ++index)
            target->writeMemoryByte(work.address + index, injected[index]);
        recordSemantic("FAULT_INJECT", "CQ_ENTRY", value.cq_seq,
                       value.request_id, value.completion_cookie);
    }
    work.data.assign(agent_abi::kCqDescriptorBytes, 0);
    for (uint64_t i = 0; i < work.bytes; ++i)
        work.data[i] = target->readMemoryByte(work.address + i);
    for (uint64_t index = 0; index < saved.size(); ++index)
        target->writeMemoryByte(work.address + index, saved[index]);
    work.accepted = true;
    activeRead = work;
    recordSemantic("LOCAL_READ", work.control, currentCqSequence,
                   currentRequestId, currentCookie);
    handleCqRead();
}

void
AgentAxiDriver::handleCqRead()
{
    if (!activeRead)
        return;
    const Gate3ReadWork work = *activeRead;
    activeRead.reset();
    if (work.sawError || work.data.size() < agent_abi::kCqDescriptorBytes) {
        requestFault(
            agent_abi::FaultSiteV1::DRIVER_CQ_READ,
            gate3CqEntryKey(currentCqSequence, currentSqSequence + 1,
                            currentRequestId),
            UINT64_MAX,
            internalSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT));
        return;
    }
    const auto value = agent_abi::decodeCqDescriptor(work.data.data());
    const auto context = std::find_if(
        submissionContexts.begin(), submissionContexts.end(),
        [&value, this](const SubmissionContext &candidate) {
            return candidate.requestId.value() == value.request_id &&
                candidate.cookie.value() == value.completion_cookie &&
                consumedSqSequences.count(candidate.sqSequence.value()) == 0;
        });
    const bool matchesAssignment = value.cq_seq == currentCqSequence &&
        context != submissionContexts.end();
    const bool matchesSequenceOnly =
        value.cq_seq == currentCqSequence && value.request_id == 0 &&
        (value.flags & agent_abi::kCqFlagsSQ_SEQ_ONLY_ERROR) &&
        value.completion_cookie < submissionContexts.size() &&
        submissionContexts.at(value.completion_cookie).sqSequence.value() ==
            value.completion_cookie &&
        consumedSqSequences.count(value.completion_cookie) == 0;
    if (!matchesAssignment && !matchesSequenceOnly &&
        (mode("STALE_CQ_SEQ") || mode("CQ_REQUEST_MISMATCH") ||
         mode("CQ_COOKIE_MISMATCH"))) {
        if (mode("STALE_CQ_SEQ")) {
            recorder->setMetric(
                "stale_cq_entries",
                recorder->metric("stale_cq_entries") + 1);
        } else if (mode("CQ_REQUEST_MISMATCH")) {
            recorder->setMetric(
                "cq_request_mismatch",
                recorder->metric("cq_request_mismatch") + 1);
        } else {
            recorder->setMetric(
                "cq_cookie_mismatch",
                recorder->metric("cq_cookie_mismatch") + 1);
        }
        recordSemantic("CQ_REJECT", "CQ_ENTRY", value.cq_seq,
                       value.request_id, value.completion_cookie);
    }
    if (!matchesAssignment && !matchesSequenceOnly) {
        requestFault(
            agent_abi::FaultSiteV1::DRIVER_CQ_READ,
            gate3CqEntryKey(currentCqSequence, currentSqSequence + 1,
                            currentRequestId),
            UINT64_MAX,
            internalSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT));
        return;
    }
    if (matchesSequenceOnly) {
        currentSqSequence = value.completion_cookie;
        currentRequestId = 0;
        currentCookie = value.completion_cookie;
    } else {
        currentSqSequence = context->sqSequence.value();
        currentRequestId = context->requestId.value();
        currentCookie = context->cookie.value();
    }
    consumedSqSequences.insert(currentSqSequence);
    if (value.flags & agent_abi::kCqFlagsDETAIL_IN_CQ) {
        const auto detail = static_cast<agent_abi::DetailCode>(
            value.output_bytes_or_detail_code);
        const auto disposition = agent_abi::detailDispositionV1(detail);
        const char *detailName = agent_abi::detailCodeNameV1(detail);
        if (!disposition || !detailName ||
            disposition->cqStatus != value.status) {
            requestFault(
                agent_abi::FaultSiteV1::DRIVER_CQ_READ,
                gate3CqEntryKey(currentCqSequence,
                                currentSqSequence + 1,
                                currentRequestId),
                UINT64_MAX,
                internalSourceToken(
                    agent_abi::FatalComponentKindV1::HOST_AGENT));
            return;
        }
        recordSemantic("CQ_DETAIL", "CQ_ENTRY", currentCqSequence,
                       currentRequestId, currentCookie, detailName);
    }
    observedCqStatus = value.status;
    observedCqFlags = value.flags;
    observedCqValue = value.output_bytes_or_detail_code;
    if (value.flags & agent_abi::kCqFlagsCONTROL_COMMAND) {
        if (!controlCqValid(value)) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
        recordSemantic("CQ_CONSUME", "CQ_ENTRY", currentCqSequence,
                       currentRequestId, currentCookie,
                       cqConsumeStatusName(value.status));
        noteControlCommandCompletion(value.status);
        resolveCancelJoin();
        ++cqConsumer;
        recorder->setMetric("driver_cq_consumer_seq", cqConsumer);
        recorder->setMetric("live_cq_obligations", 1);
        beginCompletionAck();
        return;
    }
    if (value.flags & agent_abi::kCqFlagsMETADATA_VALID) {
        cqMetadataPending = true;
        metadataReadPhase = MetadataReadPhase::Header;
        metadataTotalBytes = 0;
        metadataData.clear();
        recorder->setMetric("metadata_read_bytes", 0);
        metadataReadReadyTick =
            clockEdge(Cycles(1)) + metadataReadDelayTicks;
        if (planDrivenMode)
            completePhase = CompletePhase::Metadata;
        else
            stage = Stage::MetadataRead;
        if (!tickEvent.scheduled())
            schedule(&tickEvent, metadataReadReadyTick);
        return;
    }
    finishGenerateConsume();
    beginCompletionAck();
}

CancelJoinRecord *
AgentAxiDriver::findCancelJoinByCommand(uint64_t requestId)
{
    const auto entry = cancelJoins.find(requestId);
    return entry == cancelJoins.end() ? nullptr : &entry->second;
}

CancelJoinRecord *
AgentAxiDriver::findCancelJoinByTarget(uint64_t requestId)
{
    for (auto &entry : cancelJoins)
        if (entry.second.targetRequestId == requestId)
            return &entry.second;
    return nullptr;
}

void
AgentAxiDriver::finishGenerateConsume()
{
    recordSemantic("CQ_CONSUME", "CQ_ENTRY", currentCqSequence,
                   currentRequestId, currentCookie,
                   cqConsumeStatusName(observedCqStatus));
    acceptedGenerateRequests.erase(currentRequestId);
    CancelJoinRecord *joinByTarget =
        findCancelJoinByTarget(currentRequestId);
    if (joinByTarget) {
        joinByTarget->targetStatus = observedCqStatus;
        joinByTarget->targetCqSeen = true;
    } else {
        advanceGenerateBusiness(currentRequestId, observedCqStatus);
    }
    resolveCancelJoin();
    notifyGenerateTerminalVisible(currentRequestId);
    ++cqConsumer;
    recorder->setMetric("driver_cq_consumer_seq", cqConsumer);
    recorder->setMetric("live_cq_obligations", 1);
}

void
AgentAxiDriver::startDoorbellProbe(uint64_t tail, uint64_t requestId,
                                   uint64_t cookie)
{
    activeWrite = makeWrite(
        "DOORBELL", "SQ_DOORBELL", std::nullopt, std::nullopt,
        std::nullopt, rangeEnd(npuControlBase, NpuDoorbellOffset), doorbellAxiId,
        encodeControl(tail));
    ++doorbellAttempts;
    stage = Stage::DoorbellProbe;
}

void
AgentAxiDriver::startMetadataRead()
{
    if (curTick() < metadataReadReadyTick) {
        if (!tickEvent.scheduled())
            schedule(&tickEvent, metadataReadReadyTick);
        return;
    }
    if (currentSqSequence >= submissionContexts.size()) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return;
    }
    const SubmissionContext &context =
        submissionContexts.at(currentSqSequence);
    const uint64_t offset = metadataReadPhase == MetadataReadPhase::Header ?
        0 : agent_abi::kOutputMetadataBytes;
    const uint64_t bytes = metadataReadPhase == MetadataReadPhase::Header ?
        agent_abi::kOutputMetadataBytes :
        metadataTotalBytes - agent_abi::kOutputMetadataBytes;
    Gate3ReadWork work;
    work.object = "METADATA";
    work.control = "METADATA_READ";
    work.direction = "LOCAL";
    work.requestId = currentRequestId;
    work.cookie = currentCookie;
    work.address = context.metadataAddress + offset;
    work.bytes = bytes;
    work.data.assign(bytes, 0);
    for (uint64_t i = 0; i < work.bytes; ++i) {
        if (!target->containsMemoryAddress(work.address + i)) {
            work.sawError = true;
            break;
        }
        work.data[i] = target->readMemoryByte(work.address + i);
    }
    work.accepted = true;
    activeRead = work;
    recorder->setMetric(
        "metadata_read_bytes",
        recorder->metric("metadata_read_bytes") + work.bytes);
    Gate3Event event;
    event.tick = curTick();
    event.kind = "LOCAL_READ";
    event.object = work.control;
    event.absoluteSeq = currentCqSequence;
    event.requestId = currentRequestId;
    event.cookie = currentCookie;
    event.direction = "LOCAL";
    event.control = work.control;
    event.address = work.address;
    event.bytes = work.bytes;
    event.status = metadataReadPhase == MetadataReadPhase::Header ?
        "HEADER" : "TAIL";
    recorder->record(std::move(event));
    handleMetadataRead();
}


void
AgentAxiDriver::handleMetadataRead()
{
    if (!activeRead)
        return;
    const Gate3ReadWork work = *activeRead;
    activeRead.reset();
    const SubmissionContext &context =
        submissionContexts.at(currentSqSequence);
    const uint64_t expectedBytes =
        metadataReadPhase == MetadataReadPhase::Header ?
        agent_abi::kOutputMetadataBytes :
        metadataTotalBytes - agent_abi::kOutputMetadataBytes;
    if (work.sawError || work.data.size() != expectedBytes) {
        requestFault(
            agent_abi::FaultSiteV1::DRIVER_METADATA_READ,
            gate3MetadataKey(currentSqSequence + 1, currentRequestId,
                             context.metadataAddress),
            UINT64_MAX,
            internalSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT));
        return;
    }
    if (metadataReadPhase == MetadataReadPhase::Header) {
        const auto header = agent_abi::decodeOutputMetadata(work.data.data());
        if (!gate3MetadataHeaderValid(header, metadataExpectation())) {
            requestFault(
                agent_abi::FaultSiteV1::DRIVER_METADATA_READ,
                gate3MetadataKey(currentSqSequence + 1, currentRequestId,
                                 context.metadataAddress),
                UINT64_MAX,
                internalSourceToken(
                    agent_abi::FatalComponentKindV1::HOST_AGENT));
            return;
        }
        metadataData = work.data;
        metadataTotalBytes = header.total_bytes;
        if (metadataTotalBytes > agent_abi::kOutputMetadataBytes) {
            metadataReadPhase = MetadataReadPhase::Tail;
            metadataReadReadyTick =
                clockEdge(Cycles(1)) + metadataReadDelayTicks;
            if (planDrivenMode)
                completePhase = CompletePhase::Metadata;
            else
                stage = Stage::MetadataRead;
            if (!tickEvent.scheduled())
                schedule(&tickEvent, metadataReadReadyTick);
            return;
        }
    } else {
        metadataData.insert(
            metadataData.end(), work.data.begin(), work.data.end());
    }
    if (!gate3MetadataRecordValid(metadataData, metadataExpectation())) {
        requestFault(
            agent_abi::FaultSiteV1::DRIVER_METADATA_READ,
            gate3MetadataKey(currentSqSequence + 1, currentRequestId,
                             context.metadataAddress),
            UINT64_MAX,
            internalSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT));
        return;
    }
    recorder->setMetric("metadata_reads_validated",
                        recorder->metric("metadata_reads_validated") + 1);
    metadataData.clear();
    metadataTotalBytes = 0;
    metadataReadPhase = MetadataReadPhase::Header;
    if (cqMetadataPending) {
        cqMetadataPending = false;
        finishGenerateConsume();
    }
    beginCompletionAck();
}

void
AgentAxiDriver::startAck(bool future)
{
    const uint64_t sequence = currentCqSequence + (future ? 2 : 1);
    activeWrite = makeWrite(
        "CQ_ACK", "CQ_HEAD_ACK", sequence,
        currentRequestId, currentCookie, rangeEnd(npuControlBase, NpuCqHeadAckOffset),
        ackAxiId, encodeControl(sequence));
    recorder->setMetric("ack_wait_b", 1);
    futureAckProbe = future;
    if (planDrivenMode)
        completePhase = CompletePhase::Ack;
    else
        stage = Stage::Ack;
}

void
AgentAxiDriver::beginCompletionAck()
{
    if (planDrivenMode) {
        completePhase = CompletePhase::AckWait;
        return;
    }
    startAck();
}

void
AgentAxiDriver::handleAckResponse(const Gate3WriteWork &work,
                                  axi::AxiResp response)
{
    if (futureAckProbe) {
        futureAckProbe = false;
        recorder->setMetric("ack_wait_b", 0);
        if (response != axi::AxiResp::DecErr ||
            recorder->metric("future_ack_rejected") == 0) {
            requestFault(
                agent_abi::FaultSiteV1::CONTROL_BAD_ID_OR_WINDOW,
                gate3FaultContextKey(
                    agent_abi::FaultSiteV1::CONTROL_BAD_ID_OR_WINDOW),
                axiIssueOrdinal(work.txn),
                axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                               false, work.txn));
            return;
        }
        stage = Stage::Drain;
        drainUntilTick = curTick() + drainCycles * clockPeriod();
        scheduleTick();
        return;
    }
    if (response != axi::AxiResp::Okay) {
        recorder->setMetric("ack_wait_b", 0);
        recorder->setMetric("host_ack_b_error",
                            recorder->metric("host_ack_b_error") + 1);
        const auto site = recorder->hasEvent(
            "CQ_ACK_TARGET_COMMIT", "CQ_ACK", currentCqSequence + 1) ?
            agent_abi::FaultSiteV1::ACK_B_AFTER_TARGET_COMMIT :
            agent_abi::FaultSiteV1::ACK_B_PROVEN_NO_TARGET_COMMIT;
        const uint64_t ordinal = axiIssueOrdinal(work.txn);
        requestFault(
            site,
            gate3HostAckKey(ordinal, currentCqSequence + 1, work.axiId),
            ordinal,
            axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                           false, work.txn));
        return;
    }
    if (!recorder->hasEvent("CQ_ACK_TARGET_COMMIT", "CQ_ACK",
                             currentCqSequence + 1)) {
        recorder->setMetric("ack_wait_b", 0);
        const uint64_t ordinal = axiIssueOrdinal(work.txn);
        requestFault(
            agent_abi::FaultSiteV1::ACK_B_PROVEN_NO_TARGET_COMMIT,
            gate3HostAckKey(ordinal, currentCqSequence + 1, work.axiId),
            ordinal,
            axiSourceToken(agent_abi::FatalComponentKindV1::HOST_AGENT,
                           false, work.txn));
        return;
    }
    if (completedRequests + 1 < requestCount)
        recordSemantic("SLOT_REUSE", "CQ_ENTRY", currentCqSequence,
                       currentRequestId, currentCookie);
    ++cqAck;
    ++completedRequests;
    recorder->setMetric("driver_cq_consumer_seq", cqConsumer);
    recorder->setMetric("driver_cq_ack_seq", cqAck);
    recorder->setMetric("ack_wait_b", 0);
    currentPrepared = false;
    if (planDrivenMode)
        completePhase = CompletePhase::Idle;
    if (runtimeExhausted(completedRequests)) {
        if (mode("STALE_DOORBELL") && !staleSent) {
            staleSent = true;
            startDoorbellProbe(completedRequests - 1, 0, 0);
        } else if (mode("DUPLICATE_DOORBELL") && !duplicateSent) {
            duplicateSent = true;
            startDoorbellProbe(completedRequests, currentRequestId,
                               currentCookie);
        } else if (mode("ACK_BEYOND_ISSUED") && !futureAckSent) {
            futureAckSent = true;
            startAck(true);
        } else {
            stage = Stage::Drain;
            drainUntilTick = curTick() + drainCycles * clockPeriod();
        }
    } else if (completedRequests < submissionContexts.size()) {
        stage = Stage::Completion;
    } else {
        stage = Stage::Prepare;
    }
    scheduleTick();
}

void
AgentAxiDriver::finishIfDrained()
{
    if (drainUntilTick == 0)
        drainUntilTick = curTick() + drainCycles * clockPeriod();
    if (curTick() < drainUntilTick || !master->functionalIdle() ||
        !target->functionalIdle() || recorder->metric("frontend_drained") == 0) {
        scheduleTick();
        return;
    }
    if (exitIssued)
        return;
    Gate3FinalState final;
    final.sqTentativeProducerSeq = ledger.tentativeProducer().value();
    final.sqCommittedProducerSeq = ledger.committedProducer().value();
    final.sqObservedHeadSeq = observedSqHead;
    final.sqReusableHeadSeq = ledger.reusableHead().value();
    final.npuSqConsumerSeq = recorder->metric("npu_sq_consumer_seq");
    final.npuCqProducerSeq = recorder->metric("npu_cq_producer_seq");
    final.cqMsiIssuedSeq = recorder->metric("npu_cq_msi_issued_seq");
    final.cqNotifiedSeq = recorder->metric("npu_cq_notified_seq");
    final.driverCqConsumerSeq = cqConsumer;
    final.npuCqAckSeq = recorder->metric("npu_cq_ack_seq");
    final.liveSubmissions = ledger.hasPending() ? 1 : 0;
    final.liveContexts = recorder->metric("live_contexts");
    final.liveCqObligations = recorder->metric("live_cq_obligations");
    final.msiRobEntries = recorder->metric("msi_rob_entries");
    final.ackWaitB = recorder->metric("ack_wait_b");
    final.fatal = recorder->fatalRecorded();
    final.coreStarts = recorder->metric("core_starts");
    final.cqAssignments = recorder->metric("cq_assignments");
    final.irqDeliveries = recorder->metric("irq_deliveries");
    recorder->write(final);
    exitIssued = true;
    exitSimLoop(final.fatal ? "AI_MESH_GATE3_INFRA_FATAL" :
                "AI_MESH_GATE3_QUIESCENT_SUCCESS", final.fatal ? 20 : 0);
}

void
AgentAxiDriver::onAxiWriteCommitted(
    const axi::AxiAddressRequest &request,
    const std::vector<axi::AxiDataPacket> &beats, axi::AxiResp resp)
{
    (void)request;
    (void)beats;
    (void)resp;
}

void
AgentAxiDriver::onAxiWriteCommittedWithMeta(
    const axi::AxiAddressPacket &packet,
    const std::vector<axi::AxiDataPacket> &beats, axi::AxiResp resp)
{
    const uint64_t address = packet.request.address;
    std::string control;
    if (address == rangeEnd(agentProxyControlBase, AgentProxySqHeadOffset))
        control = "SQ_HEAD_UPDATE";
    else if (address == rangeEnd(agentProxyControlBase, AgentProxyCqTailOffset))
        control = "CQ_TAIL_UPDATE";
    else if (address >= cqRingBase &&
             address < cqRingBase + ringLayout.cqSpanBytes())
        control = "CQ_ENTRY";
    else if (address >= msiBase && address < msiBase + 0x10000)
        control = "MSI";
    if (control == "MSI" && resp == axi::AxiResp::Okay && !beats.empty()) {
        uint64_t lane = 0;
        if (!beats.empty()) {
            const uint64_t strobe = beats.front().byteStrobe;
            lane = strobe ? static_cast<uint64_t>(
                __builtin_ctzll(strobe)) : 0;
        }
        const std::vector<uint8_t> payload(
            beats.front().functionalData.begin() + lane,
            beats.front().functionalData.begin() + lane + 8);
        const uint64_t tail = readLe(payload, 0);
        const uint64_t ordinal = axiIssueOrdinal(packet.meta.txnUid);
        recorder->recordMsiTargetCommit(
            ordinal);
        const Gate3IrqStageResult result = irqCommitQueue.stage(
            Gate3IrqCommit{tail, ordinal, packet.request.axiId});
        if (result == Gate3IrqStageResult::Duplicate) {
            recorder->setMetric(
                "duplicate_msi_callbacks",
                recorder->metric("duplicate_msi_callbacks") + 1);
        } else if (result == Gate3IrqStageResult::Stale) {
            recorder->setMetric(
                "stale_msi_callbacks",
                recorder->metric("stale_msi_callbacks") + 1);
        } else if (result != Gate3IrqStageResult::Accepted) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        }
    }
    if (control == "SQ_HEAD_UPDATE" && !beats.empty())
        observedSqHead = readLe(beats.front().functionalData, 0);
    if (resp == axi::AxiResp::Okay && control == "SQ_HEAD_UPDATE" &&
        !ledger.observeHead(SqSeq(observedSqHead))) {
        requestFatal(agent_abi::E_SQ_MALFORMED);
    }
    if (resp == axi::AxiResp::Okay && control == "CQ_ENTRY" &&
        !beats.empty() && ledger.pending()) {
        const uint64_t lane = address % dataBusBytes;
        if (lane + agent_abi::kCqDescriptorBytes >
            beats.front().functionalData.size()) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
        const auto value = agent_abi::decodeCqDescriptor(
            beats.front().functionalData.data() + lane);
        const auto &pending = *ledger.pending();
        const uint64_t base = pending.base.value();
        const uint64_t tail = pending.tail.value();
        const bool sequenceInPending = value.cq_seq >= base &&
            value.cq_seq < tail;
        const bool normalIdentity = sequenceInPending &&
            std::any_of(
                submissionContexts.begin(), submissionContexts.end(),
                [&value](const SubmissionContext &context) {
                    return context.requestId.value() == value.request_id &&
                        context.cookie.value() == value.completion_cookie;
                });
        const bool sequenceOnlyIdentity = sequenceInPending &&
            value.request_id == 0 &&
            (value.flags & agent_abi::kCqFlagsSQ_SEQ_ONLY_ERROR) &&
            value.completion_cookie >= base &&
            value.completion_cookie < tail;
        if ((normalIdentity || sequenceOnlyIdentity) &&
            !ledger.observeCompletion(pending.base))
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
    }
    if (resp == axi::AxiResp::Okay && control == "MSI") {
        const uint64_t sequence = readLe(
            beats.empty() ? std::vector<uint8_t>{} :
            beats.front().functionalData, 0);
        recordSemantic("MSI_TARGET_COMMIT", "MSI", sequence);
    }
    scheduleTick();
}

void
AgentAxiDriver::processIrqCommits()
{
    for (const Gate3IrqCommit &commit : irqCommitQueue.consumePrefix()) {
        recordSemantic("IRQ_DELIVER", "MSI", commit.tail);
        recorder->setMetric("irq_deliveries",
                            recorder->metric("irq_deliveries") + 1);
    }
}

void
AgentAxiDriver::wakeup()
{
    if (!recorder->fatalPending())
        processIrqCommits();
    pumpControlDelivery();
    if (managerEdgeDue()) {
        workloadManager->onEdge(curTick());
        suppressUnreachableControls();
    }
    if (workloadManager && !recorder->fatalRecorded())
        scheduleManagerWake(workloadManager->nextWakeTick());
    if (recorder->fatalRecorded()) {
        if (activeWrite) {
            if (!activeWrite->awAccepted) {
                activeWrite.reset();
                stage = Stage::Drain;
            } else if (activeWrite->nextBeat < activeWrite->beats.size()) {
                Gate3WriteWork &work = *activeWrite;
                while (work.nextBeat < work.beats.size()) {
                    const size_t index = work.nextBeat;
                    if (!master->tryAcceptW(work.beats[index]))
                        break;
                    std::ostringstream strobe;
                    strobe << std::hex << work.beats[index].byteStrobe;
                    recordAxi(
                        work, "W", work.semanticBytes[index], "",
                        strobe.str(), axi::axiBeatAddress(work.request, index));
                    ++work.nextBeat;
                }
            } else {
                consumeB();
            }
        } else if (activeRead) {
            if (!activeRead->accepted) {
                activeRead.reset();
                stage = Stage::Drain;
            } else {
                consumeR();
            }
        } else {
            finishIfDrained();
        }
        if (!exitIssued)
            scheduleTick();
        return;
    }
    if (planDrivenMode) {
        planPump();
        return;
    }
    switch (stage) {
      case Stage::Prepare:
        prepareRequest();
        break;
      case Stage::Fence:
        if (curTick() < localReadyTick) {
            if (!tickEvent.scheduled())
                schedule(&tickEvent, localReadyTick);
            break;
        }
        recordSemantic("RELEASE_FENCE_DONE", "CONTEXT", std::nullopt,
                       currentRequestId, currentCookie);
        stage = Stage::Doorbell;
        break;
      case Stage::Doorbell:
        activeWrite = makeWrite(
            "DOORBELL", "SQ_DOORBELL", currentDoorbellTail,
            currentRequestId, currentCookie, rangeEnd(npuControlBase, NpuDoorbellOffset),
            doorbellAxiId,
            encodeControl(currentDoorbellTail));
        ++doorbellAttempts;
        if (controlDoorbellBHoldTicks != 0 && currentIntent.planDriven &&
                currentIntent.commandKind != kAgentCommandGenerate)
            controlDoorbellBHeldUntil = curTick() + controlDoorbellBHoldTicks;
        driveWrite();
        break;
      case Stage::DoorbellResponse:
        consumeB();
        break;
      case Stage::DoorbellProbe:
        driveWrite();
        break;
      case Stage::DoorbellProbeResponse:
        consumeB();
        break;
      case Stage::Completion:
        currentCqSequence = cqConsumer;
        if (recorder->hasEvent("IRQ_DELIVER", "MSI",
                               currentCqSequence + 1)) {
            if (!cqReadDelayPending) {
                cqReadDelayPending = true;
                cqReadReadyTick = curTick() + cqReadDelayTicks;
            }
            startCqRead();
        }
        break;
      case Stage::CqRead:
      case Stage::CqReadResponse:
        consumeR();
        break;
      case Stage::MetadataRead:
        startMetadataRead();
        break;
      case Stage::Ack:
        driveWrite();
        break;
      case Stage::AckResponse:
        consumeB();
        break;
      case Stage::Drain:
        finishIfDrained();
        break;
      case Stage::Done:
        finishIfDrained();
        break;
    }
    pumpControlDelivery();
    if (stage != Stage::Drain && stage != Stage::Done &&
        stage != Stage::DoorbellResponse &&
        stage != Stage::DoorbellProbeResponse &&
        stage != Stage::AckResponse && stage != Stage::Completion)
        scheduleTick();
}

}
}

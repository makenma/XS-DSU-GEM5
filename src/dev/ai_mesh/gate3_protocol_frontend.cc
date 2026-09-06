#include "dev/ai_mesh/gate3_protocol_runtime.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"

#include <algorithm>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <utility>

#include "base/logging.hh"
#include "params/NpuServingFrontend.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

NpuServingFrontend::NpuServingFrontend(const Params &p)
    : ClockedObject(p),
      master(p.master), controlTarget(p.control_target), recorder(p.recorder),
      dataBusBytes(p.data_bus_bytes), sqDepth(p.sq_depth),
      cqDepth(p.cq_depth), controlBytes(p.control_bytes),
      maxBurstBeats(p.max_burst_beats),
      hostBase(p.host_base), npuControlBase(p.npu_control_base),
      agentProxyControlBase(p.agent_proxy_control_base),
      profile(p.profile), requestCount(p.request_count),
      sqReadIssueDelay(p.sq_read_issue_delay),
      doorbellAxiId(p.doorbell_axi_id), sqHeadAxiId(p.sq_head_axi_id),
      cqTailAxiId(p.cq_tail_axi_id), cqEntryAxiId(p.cq_entry_axi_id),
      msiAxiId(p.msi_axi_id), msiAxiIdCount(p.msi_axi_id_count),
      ringLayout(rangeEnd(p.host_base, HostSqBase), p.sq_depth,
                 rangeEnd(p.host_base, HostCqBase), p.cq_depth),
      transferPlanner(p.data_bus_bytes, p.max_burst_beats),
      completionLedger(p.cq_depth),
      msiIdPool(p.msi_axi_id, p.msi_axi_id_count),
      retireEvent(this), terminalEvent(this), tickEvent(this),
      fatalDrainEvent(this)
{
    fatal_if(dataBusBytes == 0 || dataBusBytes > 64,
             "%s: invalid AXI data bus width", name());
    fatal_if(controlBytes != sizeof(uint64_t) || controlBytes > dataBusBytes,
             "%s: Gate3 control window must be 8 bytes", name());
    fatal_if(!isPowerOfTwo(sqDepth) || !isPowerOfTwo(cqDepth),
             "%s: ring depths must be powers of two", name());
    fatal_if(requestCount == 0,
             "%s: request count must be positive", name());
    const uint64_t msiEnd = uint64_t(msiAxiId) + msiAxiIdCount;
    fatal_if(msiAxiIdCount == 0 || msiEnd > uint64_t(UINT32_MAX) + 1,
             "%s: invalid MSI AXI ID range", name());
    for (const uint32_t controlId :
         {doorbellAxiId, sqHeadAxiId, cqTailAxiId, cqEntryAxiId}) {
        fatal_if(controlId >= msiAxiId && controlId < msiEnd,
                 "%s: MSI AXI ID range overlaps control IDs", name());
    }
}

void
NpuServingFrontend::init()
{
    ClockedObject::init();
    fatal_if(!master || !controlTarget || !recorder,
             "%s: Gate3 frontend has an unbound dependency", name());
    controlTarget->registerWriteCommitObserver(this);
    controlTarget->registerPreCommitPolicy(this);
    recorder->registerPhaseParticipant(this);
    recorder->setMetric("npu_sq_consumer_seq", 0);
    recorder->setMetric("npu_cq_producer_seq", 0);
    recorder->setMetric("npu_cq_msi_issued_seq", 0);
    recorder->setMetric("npu_cq_notified_seq", 0);
    recorder->setMetric("live_contexts", 0);
    recorder->setMetric("live_cq_obligations", 0);
    recorder->setMetric("core_starts", 0);
    recorder->setMetric("cq_assignments", 0);
    recorder->setMetric("irq_deliveries", 0);
    recorder->setMetric("frontend_drained", 0);
    recorder->setMetric("sq_intakes_created", 0);
    recorder->setMetric("sq_intakes_released", 0);
    recorder->setMetric("live_sq_intakes", 0);
    recorder->setMetric("fatal_sq_intakes", 0);
}

void
NpuServingFrontend::startup()
{
    ClockedObject::startup();
    schedule(&tickEvent, clockEdge() + 1);
}

void
NpuServingFrontend::scheduleTick()
{
    if (!tickEvent.scheduled())
        schedule(&tickEvent, clockEdge() + 1);
}

bool
NpuServingFrontend::mode(const char *value) const
{
    return profile == value;
}

uint64_t
NpuServingFrontend::sqAddress(uint64_t sequence) const
{
    return ringLayout.sqAddress(SqSeq(sequence));
}

uint64_t
NpuServingFrontend::parameterAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostParameterBase + sequence * 0x400);
}

uint64_t
NpuServingFrontend::promptAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostPromptBase + sequence * 0x100);
}

uint64_t
NpuServingFrontend::outputAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostOutputBase + sequence * 0x100);
}

uint64_t
NpuServingFrontend::metadataAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostMetadataBase + sequence * 0x200);
}

uint64_t
NpuServingFrontend::cqAddress(uint64_t sequence) const
{
    return ringLayout.cqAddress(CqSeq(sequence));
}

uint64_t
NpuServingFrontend::msiAddress(uint64_t sequence) const
{
    return rangeEnd(hostBase, HostMsiBase + sequence * 0x100);
}

std::vector<uint8_t>
NpuServingFrontend::makeControl(uint64_t sequence) const
{
    std::vector<uint8_t> data(controlBytes, 0);
    if (controlBytes >= 8)
        agent_abi::wrU64(data.data(), sequence);
    return data;
}

std::vector<uint8_t>
NpuServingFrontend::makeOutput(uint64_t sequence) const
{
    std::vector<uint8_t> data(dataBusBytes, 0);
    for (size_t index = 0; index < data.size(); ++index)
        data[index] = static_cast<uint8_t>((sequence + index * 7) & 0xff);
    return data;
}

std::vector<uint8_t>
NpuServingFrontend::makeMetadata(uint64_t sequence, uint32_t status) const
{
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

std::vector<uint8_t>
NpuServingFrontend::makeCq(uint64_t sequence, uint64_t requestId,
                           uint64_t cookie, uint16_t status,
                           uint16_t flags) const
{
    agent_abi::CqDescriptor value;
    value.cq_seq = sequence;
    value.request_id = requestId;
    value.completion_cookie = cookie;
    value.status = status;
    value.flags = flags;
    value.output_bytes_or_detail_code = status ==
        static_cast<uint16_t>(agent_abi::CqStatus::SUCCESS)
        ? dataBusBytes : currentDetailCode;
    return toVector(agent_abi::encodeCqDescriptor(value));
}

std::vector<uint8_t>
NpuServingFrontend::makeMsi(uint64_t sequence) const
{
    return makeControl(sequence);
}

Gate3WriteWork
NpuServingFrontend::makeWrite(
    const std::string &object, const std::string &control,
    std::optional<uint64_t> absoluteSeq, std::optional<uint64_t> requestId,
    std::optional<uint64_t> cookie, uint64_t address, uint32_t axiId,
    const std::vector<uint8_t> &data, uint8_t qos) const
{
    fatal_if(data.empty(), "%s: empty Gate3 frontend write", name());
    Gate3WriteWork work;
    work.object = object;
    work.control = control;
    work.direction = "NPU_TO_DRIVER";
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
NpuServingFrontend::makeRead(
    const std::string &object, const std::string &control,
    std::optional<uint64_t> absoluteSeq, std::optional<uint64_t> requestId,
    std::optional<uint64_t> cookie, uint64_t address, uint64_t bytes,
    uint32_t axiId, uint8_t qos, uint32_t maxBeatBytes) const
{
    fatal_if(bytes == 0, "%s: empty Gate3 frontend read", name());
    Gate3ReadWork work;
    work.object = object;
    work.control = control;
    work.direction = "NPU_TO_DRIVER";
    work.absoluteSeq = absoluteSeq;
    work.requestId = requestId;
    work.cookie = cookie;
    work.address = address;
    work.axiId = axiId;
    work.qos = qos;
    work.bytes = bytes;
    work.segments = transferPlanner.plan(
        address, bytes, axiId, qos, maxBeatBytes);
    activateReadSegment(work);
    return work;
}

void
NpuServingFrontend::recordAxi(const Gate3WriteWork &work, const char *channel,
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
    event.direction = std::string(channel) == "AW" ||
        std::string(channel) == "W" ? work.direction :
        reverseDirection(work.direction);
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
NpuServingFrontend::recordAxi(const Gate3ReadWork &work, const char *channel,
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
NpuServingFrontend::recordSemantic(
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
NpuServingFrontend::requestFatal(agent_abi::DetailCode value)
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
NpuServingFrontend::requestFault(
    agent_abi::FaultSiteV1 site, std::vector<uint8_t> objectKey,
    uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken)
{
    recorder->requestFault(
        site, 0, UINT32_MAX, std::move(objectKey), issueOrdinal,
        sourceToken);
    scheduleTick();
}

void
NpuServingFrontend::cutFatalOwnership()
{
    sqCommitPending = false;
    pendingMsiCompletions.clear();
    pendingAckCommits.clear();
    pendingAckRetirements.clear();
    for (const Gate3SqIntakeRecord &intake :
         sqIntakeLedger.transferLiveToFatal()) {
        recorder->recordFatalSqIntake(
            Gate3ObservationRecorder::FatalSqIntake{
                intake.id.value(), intake.expectedSqSeq.value(),
                intake.readTag, intake.firstError,
                gate3SqIntakeStateName(intake.stateAtCut),
                gate3SqIntakeTerminalName(intake.terminal)});
    }
    currentSqIntakeId.reset();
    completionLedger.transferLiveToFatal();
    for (const CompletionObligation &obligation :
         completionLedger.fatalObligations()) {
        std::string state = "PRETERMINAL";
        if (obligation.cqSequence) {
            const uint64_t tail = obligation.cqSequence->value() + 1;
            state = completionLedger.ackReceivedSequence() >= tail &&
                completionLedger.notifiedSequence() < tail ?
                "EARLY_ACK_WAIT_MSI_B" : "POSTED_UNACKED";
        } else if (obligation.terminalReadyTick) {
            state = "TERMINAL_PENDING";
        }
        recorder->recordFatalCqObligation(
            Gate3ObservationRecorder::FatalCqObligation{
                obligation.id.value(), obligation.sqSequence.value(),
                obligation.requestId.value(),
                obligation.cqSequence ?
                    std::optional<uint64_t>(
                        obligation.cqSequence->value()) : std::nullopt,
                obligation.cqSequence ?
                    std::optional<uint32_t>(static_cast<uint32_t>(
                        obligation.cqSequence->value() % cqDepth)) :
                    std::nullopt,
                std::move(state)});
    }
    liveCqObligations = completionLedger.liveCount();
    recorder->setMetric("fatal_cq_obligations",
                        completionLedger.fatalCount());
    recorder->setMetric("live_cq_obligations", liveCqObligations);
    recorder->setMetric("live_sq_intakes", sqIntakeLedger.liveCount());
    recorder->setMetric("fatal_sq_intakes", sqIntakeLedger.fatalCount());
}

void
NpuServingFrontend::onGate3FatalCut(Tick tick)
{
    (void)tick;
    cutFatalOwnership();
    if (!fatalDrainEvent.scheduled())
        schedule(&fatalDrainEvent, clockEdge() + 1);
    scheduleTick();
}

void
NpuServingFrontend::onGate3NormalCommit(Tick tick)
{
    (void)tick;
    if (!pendingAckRetirements.empty())
        commitAckRetire();
    if (!pendingAckCommits.empty())
        commitAckTargets();
    if (!pendingMsiCompletions.empty())
        commitMsiCompletions();
    if (sqCommitPending)
        commitCurrentSq();
}

void
NpuServingFrontend::onAxiWriteCommitted(
    const axi::AxiAddressRequest &request,
    const std::vector<axi::AxiDataPacket> &beats, axi::AxiResp resp)
{
    (void)request;
    (void)beats;
    (void)resp;
}

axi::AxiWritePreCommitPolicy::Decision
NpuServingFrontend::onWritePreCommit(
    const axi::AxiAddressRequest &request,
    const std::vector<axi::AxiDataPacket> &beats,
    axi::AxiResp transport_response)
{
    Decision decision;
    const uint64_t address = request.address;
    const bool is_doorbell =
        address == rangeEnd(npuControlBase, NpuDoorbellOffset);
    const bool is_ack =
        address == rangeEnd(npuControlBase, NpuCqHeadAckOffset);
    if (!is_doorbell && !is_ack) {
        decision.response = axi::AxiResp::SlvErr;
        decision.commit = false;
        return decision;
    }
    if (request.size != 3 || request.beatCount != 1) {
        decision.response = axi::AxiResp::SlvErr;
        decision.commit = false;
        return decision;
    }
    if (!beats.empty() && beats.front().byteStrobe != (uint64_t{0xff} <<
            (address % dataBusBytes))) {
        decision.response = axi::AxiResp::SlvErr;
        decision.commit = false;
        return decision;
    }
    if (transport_response != axi::AxiResp::Okay) {
        decision.commit = false;
        return decision;
    }
    uint64_t value = 0;
    if (!beats.empty()) {
        const uint64_t lane = address % dataBusBytes;
        const auto &word = beats.front().functionalData;
        for (size_t i = 0; i < 8 && lane + i < word.size(); ++i)
            value |= uint64_t(word[lane + i]) << (8 * i);
    }
    if (is_ack && value > completionLedger.issuedSequence()) {
        decision.response = axi::AxiResp::DecErr;
        decision.commit = false;
        return decision;
    }
    if (is_doorbell) {
        if (value > advertisedSqTail && value > sqConsumer + sqDepth) {
            decision.response = axi::AxiResp::SlvErr;
            decision.commit = false;
        }
    }
    return decision;
}

void
NpuServingFrontend::onAxiWriteCommittedWithMeta(
    const axi::AxiAddressPacket &packet,
    const std::vector<axi::AxiDataPacket> &beats, axi::AxiResp resp)
{
    const uint64_t address = packet.request.address;
    uint64_t lane = 0;
    if (!beats.empty()) {
        const uint64_t strobe = beats.front().byteStrobe;
        lane = strobe ? static_cast<uint64_t>(__builtin_ctzll(strobe)) : 0;
    }
    std::vector<uint8_t> payload = beats.empty() ?
        std::vector<uint8_t>{} : std::vector<uint8_t>(
            beats.front().functionalData.begin() + lane,
            beats.front().functionalData.begin() + lane + 8);
    if (address == rangeEnd(npuControlBase, NpuDoorbellOffset)) {
        const uint64_t tail = readLe(payload, 0);
        if (resp == axi::AxiResp::Okay) {
            recordSemantic("DOORBELL_TARGET_COMMIT", "DOORBELL", tail);
            if (!acceptedDoorbellTails.insert(tail).second)
                recorder->setMetric("duplicate_doorbells",
                                    recorder->metric("duplicate_doorbells") +
                                    1);
            doorbells.push_back(Doorbell{tail, packet.meta.txnUid});
        }
    } else if (address == rangeEnd(npuControlBase, NpuCqHeadAckOffset)) {
        const uint64_t sequence = readLe(payload, 0);
        if (resp != axi::AxiResp::Okay &&
            sequence > completionLedger.issuedSequence()) {
            recorder->setMetric("future_ack_rejected",
                                recorder->metric("future_ack_rejected") + 1);
        } else if (resp == axi::AxiResp::Okay) {
            const AckDisposition disposition =
                completionLedger.classifyAck(sequence);
            if (disposition == AckDisposition::Accepted ||
                disposition == AckDisposition::Duplicate) {
                recorder->recordHostAckTargetCommit(
                    axiIssueOrdinal(packet.meta.txnUid));
                recordSemantic("CQ_ACK_TARGET_COMMIT", "CQ_ACK", sequence);
                pendingAckCommits.push_back(sequence);
                recorder->requestNormalCommit();
            }
        }
    }
    scheduleTick();
}

void
NpuServingFrontend::processDoorbells()
{
    while (!doorbells.empty()) {
        const Doorbell doorbell = doorbells.front();
        doorbells.pop_front();
        if (doorbell.tail < advertisedSqTail) {
            recorder->setMetric("stale_doorbells",
                                recorder->metric("stale_doorbells") + 1);
            continue;
        }
        if (doorbell.tail == 0) {
            recorder->setMetric("empty_doorbells",
                                recorder->metric("empty_doorbells") + 1);
            if (mode("EMPTY_DOORBELL")) {
                stage = Stage::Done;
                recorder->setMetric("frontend_drained", 1);
                return;
            }
            continue;
        }
        if (doorbell.tail == advertisedSqTail) {
            recorder->setMetric("duplicate_doorbells",
                                recorder->metric("duplicate_doorbells") + 1);
        } else {
            advertisedSqTail = doorbell.tail;
        }
    }
    if (currentValid)
        return;
    if (sqConsumer >= advertisedSqTail ||
        completionLedger.liveCount() >= cqDepth) {
        if (sqConsumer < advertisedSqTail && !cqBackpressureObserved) {
            recorder->setMetric(
                "cq_backpressure",
                recorder->metric("cq_backpressure") + 1);
            cqBackpressureObserved = true;
        }
        if (!activateReadyTerminal())
            scheduleTerminalWake();
        return;
    }
    cqBackpressureObserved = false;
    currentSqSequence = sqConsumer;
    currentRequestId = 0;
    currentCookie = 0;
    currentValid = true;
    currentDuplicate = false;
    currentError = false;
    currentObligationId.reset();
    stage = Stage::SqRead;
    startSqRead();
}

void
NpuServingFrontend::startSqRead()
{
    fatal_if(currentSqIntakeId,
             "%s: SQ intake ID ownership is invalid", name());
    currentSqIntakeId = sqIntakeLedger.reserve(SqSeq(currentSqSequence));
    recorder->setMetric("sq_intakes_created",
                        sqIntakeLedger.createdCount());
    recorder->setMetric("live_sq_intakes", sqIntakeLedger.liveCount());
    activeRead = makeRead("SQ_ENTRY", "SQ_ENTRY", currentSqSequence,
                          std::nullopt, std::nullopt,
                          sqAddress(currentSqSequence),
                          agent_abi::kSqDescriptorBytes, 11);
    activeRead->sqIntakeId = currentSqIntakeId;
    sqReadIssueReadyTick =
        curTick() + sqReadIssueDelay * clockPeriod();
    stage = Stage::SqRead;
}

void
NpuServingFrontend::startParameterRead()
{
    activeRead = makeRead("PARAMETER", "PARAMETER", std::nullopt,
                          currentRequestId, currentCookie,
                          currentParameterAddress,
                          currentParameterBytes, 12, 0,
                          mode("PARAMETER_NARROW_BEATS") ? 32 : 0);
    recorder->setMetric("parameter_read_segments",
                        activeRead->segments.size());
    recorder->setMetric("parameter_first_segment_bytes",
                        activeRead->segments.front().logicalBytes);
    stage = Stage::ParameterRead;
}

void
NpuServingFrontend::startPromptRead()
{
    activeRead = makeRead("PROMPT", "PROMPT", std::nullopt,
                          currentRequestId, currentCookie,
                          currentInputAddress, currentInputBytes,
                          13);
    stage = Stage::PromptRead;
}

void
NpuServingFrontend::startSqHead()
{
    activeWrite = makeWrite(
        "SQ_HEAD", "SQ_HEAD_UPDATE", currentSqSequence + 1,
        currentRequestId, currentCookie, rangeEnd(agentProxyControlBase, AgentProxySqHeadOffset),
        sqHeadAxiId, makeControl(currentSqSequence + 1));
    stage = Stage::SqHead;
}

void
NpuServingFrontend::startOutput()
{
    activeWrite = makeWrite(
        "OUTPUT", "OUTPUT", std::nullopt, currentRequestId,
        currentCookie, currentOutputAddress, 21,
        makeOutput(currentSqSequence));
    stage = Stage::Output;
}

void
NpuServingFrontend::startMetadata()
{
    activeWrite = makeWrite(
        "METADATA", "METADATA", std::nullopt, currentRequestId,
        currentCookie, currentMetadataAddress, 22,
        makeMetadata(currentSqSequence,
                     static_cast<uint32_t>(agent_abi::CqStatus::SUCCESS)));
    stage = Stage::Metadata;
}

void
NpuServingFrontend::stageTerminalResult()
{
    if (!currentObligationId) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return;
    }
    Tick readyTick = curTick();
    if ((mode("QOS_OUT_OF_ORDER") || mode("SAME_TICK_SEQ_ONLY")) &&
        currentSqSequence == 0) {
        terminalBarrierTick = clockEdge(Cycles(1000));
        readyTick = terminalBarrierTick;
    } else if (mode("SAME_TICK_SEQ_ONLY")) {
        fatal_if(terminalBarrierTick == 0,
                 "%s: missing same-tick terminal barrier", name());
        readyTick = terminalBarrierTick;
    }
    const bool sequenceKeyed =
        currentCqFlags & (agent_abi::kCqFlagsSQ_SEQ_ONLY_ERROR |
                          agent_abi::kCqFlagsSQ_IDENTITY_ERROR);
    const uint8_t effectiveQos = sequenceKeyed ? 0 : currentQos;
    const uint64_t effectiveRequestId = sequenceKeyed ? 0 : currentRequestId;
    if (!completionLedger.markTerminalReady(
            *currentObligationId, readyTick, effectiveQos,
            effectiveRequestId)) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return;
    }
    const uint64_t key = currentObligationId->value();
    const bool inserted = terminalResults.emplace(
        key, TerminalResult{
            *currentObligationId, readyTick, currentSqSequence,
            currentRequestId, currentCookie, effectiveQos,
            currentCqStatus, currentCqFlags, currentDetailCode, false}).second;
    if (!inserted) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return;
    }
    currentValid = false;
    currentError = false;
    currentObligationId.reset();
    recorder->setMetric("live_contexts", completionLedger.liveCount());
    stage = Stage::Doorbell;
    scheduleTerminalWake();
    processDoorbells();
}

void
NpuServingFrontend::emitMaturedTerminals()
{
    for (auto &[key, result] : terminalResults) {
        if (result.readyRecorded || result.readyTick > curTick())
            continue;
        result.readyRecorded = true;
        recordSemantic(
            "TERMINAL_READY", "CONTEXT", result.sqSequence,
            result.requestId, result.cookie,
            isSuccessStatus(result.status) ? "SUCCESS" :
            result.requestId == 0 ? "SQ_SEQ_ONLY_ERROR" : "ERROR");
    }
}

void
NpuServingFrontend::scheduleTerminalWake()
{
    std::optional<Tick> earliest;
    for (const auto &[key, result] : terminalResults) {
        if (result.readyTick <= curTick())
            continue;
        if (!earliest || result.readyTick < *earliest)
            earliest = result.readyTick;
    }
    if (!earliest)
        return;
    if (!terminalEvent.scheduled())
        schedule(&terminalEvent, *earliest);
    else if (*earliest < terminalEvent.when())
        reschedule(&terminalEvent, *earliest);
}

bool
NpuServingFrontend::activateReadyTerminal()
{
    emitMaturedTerminals();
    const auto selected = completionLedger.nextTerminal(curTick());
    if (!selected)
        return false;
    const auto found = terminalResults.find(selected->value());
    if (found == terminalResults.end()) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return true;
    }
    const TerminalResult result = found->second;
    terminalResults.erase(found);
    const auto assigned = completionLedger.assign(*selected);
    if (!assigned) {
        requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
        return true;
    }
    currentObligationId = result.obligationId;
    currentSqSequence = result.sqSequence;
    currentRequestId = result.requestId;
    currentCookie = result.cookie;
    currentQos = result.qos;
    currentCqStatus = result.status;
    currentCqFlags = result.flags;
    currentDetailCode = result.detailCode;
    currentCqSequence = assigned->value();
    nextCqSequence = completionLedger.producerSequence();
    currentValid = true;
    startCqEntry();
    return true;
}

void
NpuServingFrontend::startCqEntry()
{
    activeWrite = makeWrite(
        "CQ_ENTRY", "CQ_ENTRY", currentCqSequence, currentRequestId,
        currentCookie, cqAddress(currentCqSequence), cqEntryAxiId,
        makeCq(currentCqSequence, currentRequestId, currentCookie,
               currentCqStatus, currentCqFlags));
    stage = Stage::CqEntry;
}

void
NpuServingFrontend::startCqTail()
{
    activeWrite = makeWrite(
        "CQ_TAIL", "CQ_TAIL_UPDATE", currentCqSequence + 1,
        currentRequestId, currentCookie, rangeEnd(agentProxyControlBase, AgentProxyCqTailOffset),
        cqTailAxiId, makeControl(currentCqSequence + 1));
    stage = Stage::CqTail;
}

void
NpuServingFrontend::startMsi()
{
    stage = Stage::Msi;
    const auto axiId = msiIdPool.acquire();
    if (!axiId) {
        scheduleTick();
        return;
    }
    activeWrite = makeWrite(
        "MSI", "MSI", currentCqSequence + 1, currentRequestId,
        currentCookie, msiAddress(currentCqSequence), *axiId,
        makeMsi(currentCqSequence + 1));
}

void
NpuServingFrontend::handleSqRecord()
{
    if (!activeRead)
        return;
    sqData = activeRead->data;
    const bool readError = activeRead->sawError;
    const uint64_t readTransaction = activeRead->txn;
    activeRead.reset();
    fatal_if(!currentSqIntakeId,
             "%s: SQ response has no intake reservation", name());
    const SqIntakeId intakeId = *currentSqIntakeId;
    if (readError) {
        const uint64_t ordinal = axiIssueOrdinal(readTransaction);
        fatal_if(!sqIntakeLedger.setFirstError(
                     intakeId, "E_AGENT_PROTOCOL_FATAL"),
                 "%s: SQ intake error ownership is invalid", name());
        requestFault(
            agent_abi::FaultSiteV1::SQ_R_TRANSPORT,
            gate3SqIntakeKey(intakeId.value(), currentSqSequence),
            ordinal,
            axiSourceToken(agent_abi::FatalComponentKindV1::NPU_COMMAND,
                           true, readTransaction));
        return;
    }
    if (sqData.size() < agent_abi::kSqDescriptorBytes) {
        fatal_if(!sqIntakeLedger.setFirstError(
                     intakeId, "E_SQ_MALFORMED"),
                 "%s: SQ intake error ownership is invalid", name());
        requestFatal(agent_abi::E_SQ_MALFORMED);
        return;
    }
    const auto value = agent_abi::decodeSqDescriptor(sqData.data());
    if (agent_abi::crc32c(sqData.data(), 60) != value.crc32) {
        currentError = true;
        currentRequestId = 0;
        currentCookie = currentSqSequence;
        currentCqStatus = static_cast<uint16_t>(agent_abi::CqStatus::SQ_CRC);
        currentCqFlags = agent_abi::kCqFlagsSQ_SEQ_ONLY_ERROR |
            agent_abi::kCqFlagsDETAIL_IN_CQ;
        currentDetailCode = agent_abi::E_SQ_CRC;
    } else if (value.sq_seq != currentSqSequence) {
        const uint64_t ordinal = axiIssueOrdinal(readTransaction);
        fatal_if(!sqIntakeLedger.setFirstError(
                     intakeId, "E_AGENT_PROTOCOL_FATAL"),
                 "%s: SQ intake error ownership is invalid", name());
        requestFault(
            agent_abi::FaultSiteV1::SEQUENCE_MISMATCH,
            gate3SqIntakeKey(intakeId.value(), currentSqSequence),
            ordinal,
            axiSourceToken(agent_abi::FatalComponentKindV1::NPU_COMMAND,
                           true, readTransaction));
        return;
    } else if (value.request_id == 0 || value.completion_cookie == 0) {
        currentError = true;
        currentCqStatus = static_cast<uint16_t>(
            agent_abi::CqStatus::PARAM_ERROR);
        currentCqFlags = agent_abi::kCqFlagsSQ_IDENTITY_ERROR |
            agent_abi::kCqFlagsDETAIL_IN_CQ;
        currentDetailCode = agent_abi::E_REQUEST_IDENTITY;
        currentRequestId = value.request_id;
        currentCookie = value.completion_cookie;
    } else {
        currentRequestId = value.request_id;
        currentCookie = value.completion_cookie;
        currentParameterAddress = value.parameter_block_addr;
        currentParameterBytes = value.parameter_block_bytes;
        currentQos = value.qos;
    }
    fatal_if(sqCommitPending,
             "%s: SQ normal commit proposal already exists", name());
    sqCommitPending = true;
    recorder->requestNormalCommit();
}

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
    recorder->setMetric("live_contexts", completionLedger.liveCount());
    recordSemantic("CORE_START", "CONTEXT", std::nullopt,
                   currentRequestId, currentCookie);
    recorder->setMetric("core_starts", recorder->metric("core_starts") + 1);
    startOutput();
}

void
NpuServingFrontend::handleWriteResponse(const Gate3WriteWork &work,
                                         axi::AxiResp response)
{
    if (response != axi::AxiResp::Okay) {
        if (work.control == "OUTPUT") {
            queueErrorCompletion(
                static_cast<uint16_t>(agent_abi::CqStatus::AXI_ERROR),
                agent_abi::kCqFlagsDETAIL_IN_CQ,
                agent_abi::E_AXI_RESPONSE);
            return;
        }
        const agent_abi::FaultSiteV1 site =
            work.control == "SQ_HEAD_UPDATE" ?
                agent_abi::FaultSiteV1::SQ_HEAD_B :
            work.control == "METADATA" ?
                agent_abi::FaultSiteV1::METADATA_B :
            work.control == "CQ_ENTRY" ?
                agent_abi::FaultSiteV1::CQ_ENTRY_B :
                agent_abi::FaultSiteV1::CQ_TAIL_B;
        std::vector<uint8_t> key;
        if (work.control == "METADATA") {
            key = gate3MetadataKey(
                currentObligationId ? currentObligationId->value() : 0,
                currentRequestId, work.address);
        } else if (work.control == "CQ_ENTRY") {
            key = gate3CqEntryKey(
                currentCqSequence,
                currentObligationId ? currentObligationId->value() : 0,
                currentRequestId);
        } else {
            const auto kind = work.control == "SQ_HEAD_UPDATE" ?
                agent_abi::ControlUpdateKindV1::SQ_HEAD_UPDATE :
                agent_abi::ControlUpdateKindV1::CQ_TAIL_UPDATE;
            key = gate3ControlKey(kind, work.absoluteSeq.value_or(0));
        }
        const uint64_t ordinal = axiIssueOrdinal(work.txn);
        requestFault(
            site, std::move(key), ordinal,
            axiSourceToken(
                work.control == "SQ_HEAD_UPDATE" ?
                    agent_abi::FatalComponentKindV1::NPU_COMMAND :
                    agent_abi::FatalComponentKindV1::NPU_FRONTEND,
                false, work.txn));
        return;
    }
    if (work.control == "SQ_HEAD_UPDATE") {
        if (currentError)
            stageTerminalResult();
        else
            startParameterRead();
    } else if (work.control == "OUTPUT") {
        startMetadata();
    } else if (work.control == "METADATA") {
        currentCqStatus = static_cast<uint16_t>(agent_abi::CqStatus::SUCCESS);
        currentCqFlags = agent_abi::kCqFlagsMETADATA_VALID;
        stageTerminalResult();
    } else if (work.control == "CQ_ENTRY") {
        const std::string status = isSuccessStatus(currentCqStatus) ?
            "SUCCESS" : "ERROR";
        recordSemantic("CQ_ASSIGN", "CQ_ENTRY", currentCqSequence,
                       currentRequestId, currentCookie, status);
        ++cqAssignments;
        recorder->setMetric("npu_cq_producer_seq", cqAssignments);
        recorder->setMetric("cq_assignments", cqAssignments);
        startCqTail();
    } else if (work.control == "CQ_TAIL_UPDATE") {
        startMsi();
    }
}

void
NpuServingFrontend::processAckRetire()
{
    pendingAckRetirements = completionLedger.retirable();
    if (pendingAckRetirements.empty()) {
        scheduleTick();
        return;
    }
    recorder->requestNormalCommit();
}

void
NpuServingFrontend::commitAckRetire()
{
    fatal_if(pendingAckRetirements.empty(),
             "%s: ACK retirement has no normal commit proposal", name());
    const auto retirements = std::move(pendingAckRetirements);
    pendingAckRetirements.clear();
    for (const CqObligationId id : retirements) {
        const CompletionObligation *obligation = completionLedger.find(id);
        fatal_if(!obligation || !obligation->cqSequence,
                 "%s: invalid retirable CQ obligation", name());
        recordSemantic("CQ_OBLIGATION_RETIRE", "CQ_ENTRY",
                       obligation->cqSequence->value(),
                       obligation->requestId.value(),
                       obligation->cookie.value());
        fatal_if(!completionLedger.retire(id),
                 "%s: CQ obligation retirement failed", name());
    }
    liveCqObligations = completionLedger.liveCount();
    recorder->setMetric("live_cq_obligations", liveCqObligations);
    recorder->setMetric("live_contexts", liveCqObligations);
    if (cqAssignments == requestCount && liveCqObligations == 0 &&
        completionLedger.msiRobEntries() == 0) {
        stage = Stage::Done;
        recorder->setMetric("frontend_drained", 1);
    }
    scheduleTick();
}

void
NpuServingFrontend::commitAckTargets()
{
    fatal_if(pendingAckCommits.empty(),
             "%s: ACK target has no normal commit proposal", name());
    const auto commits = std::move(pendingAckCommits);
    pendingAckCommits.clear();
    for (const uint64_t sequence : commits) {
        const AckDisposition disposition = completionLedger.acceptAck(sequence);
        fatal_if(disposition != AckDisposition::Accepted &&
                 disposition != AckDisposition::Duplicate,
                 "%s: staged ACK target commit is no longer valid", name());
    }
    ackReceivedSeq = completionLedger.ackReceivedSequence();
    recorder->setMetric("npu_cq_ack_seq", ackReceivedSeq);
    scheduleRetire();
}

void
NpuServingFrontend::commitMsiCompletions()
{
    fatal_if(pendingMsiCompletions.empty(),
             "%s: MSI has no normal commit proposal", name());
    const auto completions = std::move(pendingMsiCompletions);
    pendingMsiCompletions.clear();
    for (const Gate3WriteWork &work : completions) {
        fatal_if(!completionLedger.completeMsi(
                     work.axiId, axi::AxiResp::Okay),
                 "%s: staged MSI completion is no longer valid", name());
        fatal_if(!msiIdPool.release(work.axiId),
                 "%s: MSI AXI ID release is invalid", name());
    }
    msiConfirmedSeq = completionLedger.notifiedSequence();
    recorder->setMetric("npu_cq_notified_seq", msiConfirmedSeq);
    recorder->setMetric("msi_rob_entries",
                        completionLedger.msiRobEntries());
    scheduleRetire();
}

void
NpuServingFrontend::scheduleRetire()
{
    if (completionLedger.retirable().empty() || retireEvent.scheduled())
        return;
    Tick edge = clockEdge();
    if (edge <= curTick())
        edge += clockPeriod();
    schedule(&retireEvent, edge);
}

void
NpuServingFrontend::finishCurrent()
{
    currentValid = false;
    currentError = false;
    recorder->setMetric("live_contexts", completionLedger.liveCount());
    currentObligationId.reset();
    stage = Stage::Doorbell;
    processDoorbells();
    if (stage == Stage::Doorbell)
        scheduleTerminalWake();
}

void
NpuServingFrontend::queueErrorCompletion(uint16_t status, uint16_t flags,
                                         uint32_t detail_code)
{
    currentError = true;
    currentCqStatus = status;
    currentCqFlags = flags;
    currentDetailCode = detail_code;
    stageTerminalResult();
}

void
NpuServingFrontend::driveWrite()
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
    if (work.control == "MSI") {
        if (!currentObligationId ||
            !completionLedger.issueMsi(*currentObligationId, work.axiId)) {
            requestFatal(agent_abi::E_AGENT_PROTOCOL_FATAL);
            return;
        }
        msiIssued = completionLedger.issuedSequence();
        const MsiNotifyRecord &record =
            completionLedger.msiRecords().rbegin()->second;
        recorder->recordMsiIssue(
            record.issueOrdinal.value(), record.tail, record.axiId,
            axiSourceToken(agent_abi::FatalComponentKindV1::NPU_FRONTEND,
                           false, work.txn));
        recorder->setMetric("npu_cq_msi_issued_seq", msiIssued);
        recorder->setMetric("msi_rob_entries",
                            completionLedger.msiRobEntries());
        msiWrites.emplace(work.axiId, work);
        activeWrite.reset();
        finishCurrent();
        return;
    }
    stage = work.control == "SQ_HEAD_UPDATE" ? Stage::SqHeadResponse :
        work.control == "OUTPUT" ? Stage::OutputResponse :
        work.control == "METADATA" ? Stage::MetadataResponse :
        work.control == "CQ_ENTRY" ? Stage::CqEntryResponse :
        work.control == "CQ_TAIL_UPDATE" ? Stage::CqTailResponse :
        Stage::MsiResponse;
    scheduleTick();
}

void
NpuServingFrontend::handleMsiResponse(
    const Gate3WriteWork &work, axi::AxiResp response,
    uint64_t responseOrdinal)
{
    recordAxi(work, "B", 0, responseName(response));
    const bool targetCommitted = recorder->hasEvent(
        "MSI_TARGET_COMMIT", "MSI", work.absoluteSeq.value_or(0));
    const auto msiRecord = std::find_if(
        completionLedger.msiRecords().begin(),
        completionLedger.msiRecords().end(),
        [&work](const auto &item) {
            return item.second.tail == work.absoluteSeq.value_or(0) &&
                item.second.axiId == work.axiId;
        });
    fatal_if(msiRecord == completionLedger.msiRecords().end(),
             "%s: MSI response has no ledger record", name());
    const uint64_t ordinal = msiRecord->second.issueOrdinal.value();
    recorder->recordMsiTerminal(
        axiIssueOrdinal(work.txn),
        response == axi::AxiResp::Okay ? "B_OK" : "B_ERROR",
        targetCommitted,
        responseSourceToken(
            agent_abi::FatalComponentKindV1::NPU_FRONTEND,
            responseOrdinal, work.axiId));
    if (response != axi::AxiResp::Okay || !targetCommitted) {
        requestFault(
            agent_abi::FaultSiteV1::MSI_TARGET_OR_B,
            gate3MsiKey(ordinal, work.absoluteSeq.value_or(0), work.axiId),
            ordinal,
            axiSourceToken(agent_abi::FatalComponentKindV1::NPU_FRONTEND,
                           false, work.txn));
        return;
    }
    if (recorder->fatalRecorded())
        return;
    pendingMsiCompletions.push_back(work);
    recorder->requestNormalCommit();
}

void
NpuServingFrontend::pollWriteResponses()
{
    axi::AxiBBeat response;
    while (master->tryConsumeB(response)) {
        fatal_if(responseIngressOrdinal == UINT64_MAX,
                 "%s: response observation ordinal exhausted", name());
        ++responseIngressOrdinal;
        const auto msi = msiWrites.find(response.axiId);
        if (msi == msiWrites.end()) {
            deferredWriteResponses[response.axiId].push_back(response);
            continue;
        }
        const Gate3WriteWork work = msi->second;
        msiWrites.erase(msi);
        handleMsiResponse(work, response.resp, responseIngressOrdinal);
    }
}

void
NpuServingFrontend::consumeB()
{
    if (!activeWrite || !activeWrite->awAccepted ||
        activeWrite->nextBeat != activeWrite->beats.size()) {
        scheduleTick();
        return;
    }
    pollWriteResponses();
    auto found = deferredWriteResponses.find(activeWrite->axiId);
    if (found == deferredWriteResponses.end() || found->second.empty()) {
        scheduleTick();
        return;
    }
    const axi::AxiBBeat response = found->second.front();
    found->second.pop_front();
    if (found->second.empty())
        deferredWriteResponses.erase(found);
    recordAxi(*activeWrite, "B", 0, responseName(response.resp));
    const bool fatalPending = recorder->fatalPending();
    if (response.resp == axi::AxiResp::Okay &&
        activeWrite->segmentIndex + 1 < activeWrite->segments.size()) {
        if (fatalPending) {
            activeWrite.reset();
            stage = Stage::Done;
            scheduleTick();
            return;
        }
        ++activeWrite->segmentIndex;
        activateWriteSegment(*activeWrite, transferPlanner);
        stage = activeWrite->control == "SQ_HEAD_UPDATE" ? Stage::SqHead :
            activeWrite->control == "OUTPUT" ? Stage::Output :
            activeWrite->control == "METADATA" ? Stage::Metadata :
            activeWrite->control == "CQ_ENTRY" ? Stage::CqEntry :
            activeWrite->control == "CQ_TAIL_UPDATE" ? Stage::CqTail :
            Stage::Msi;
        scheduleTick();
        return;
    }
    const Gate3WriteWork work = *activeWrite;
    activeWrite.reset();
    if (recorder->fatalRecorded()) {
        stage = Stage::Done;
        if (msiWrites.empty())
            recorder->setMetric("frontend_drained", 1);
        else
            scheduleTick();
        return;
    }
    if (fatalPending) {
        if (response.resp != axi::AxiResp::Okay &&
            work.control != "OUTPUT")
            handleWriteResponse(work, response.resp);
        stage = Stage::Done;
        scheduleTick();
        return;
    }
    handleWriteResponse(work, response.resp);
}

void
NpuServingFrontend::consumeR()
{
    if (!activeRead)
        return;
    Gate3ReadWork &work = *activeRead;
    if (!work.accepted) {
        if (work.sqIntakeId && curTick() < sqReadIssueReadyTick) {
            if (!tickEvent.scheduled())
                schedule(&tickEvent, sqReadIssueReadyTick);
            return;
        }
        if (!master->tryAcceptAr(work.request)) {
            scheduleTick();
            return;
        }
        work.accepted = true;
        const auto packet = master->lastAcceptedAr();
        work.txn = packet.meta.txnUid;
        if (work.sqIntakeId) {
            fatal_if(!sqIntakeLedger.acceptRead(
                         *work.sqIntakeId, axiIssueOrdinal(work.txn)),
                     "%s: SQ read acceptance ownership is invalid", name());
        }
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
            if (recorder->fatalPending()) {
                activeRead.reset();
                stage = Stage::Done;
                scheduleTick();
                return;
            }
            ++work.segmentIndex;
            activateReadSegment(work);
            scheduleTick();
            return;
        }
        if (work.sqIntakeId) {
            const Gate3SqIntakeTerminal terminal = work.sawError ?
                Gate3SqIntakeTerminal::RError : Gate3SqIntakeTerminal::ROk;
            fatal_if(!sqIntakeLedger.observeTerminal(
                         *work.sqIntakeId, terminal),
                     "%s: SQ terminal ownership is invalid", name());
            if (recorder->fatalRecorded()) {
                recorder->updateFatalSqIntakeTerminal(
                    *work.sqIntakeId,
                    gate3SqIntakeTerminalName(terminal));
            }
        }
        if (recorder->fatalRecorded()) {
            activeRead.reset();
            stage = Stage::Done;
            if (msiWrites.empty())
                recorder->setMetric("frontend_drained", 1);
            else
                scheduleTick();
            return;
        }
        const std::string control = work.control;
        if (recorder->fatalPending() && control != "SQ_ENTRY") {
            activeRead.reset();
            stage = Stage::Done;
            scheduleTick();
            return;
        }
        if (control == "SQ_ENTRY")
            handleSqRecord();
        else if (control == "PARAMETER")
            handleParameterRecord();
        else
            handlePromptRecord();
    } else {
        scheduleTick();
    }
}

void
NpuServingFrontend::wakeup()
{
    if (recorder->fatalRecorded()) {
        pollWriteResponses();
        if (activeWrite) {
            if (!activeWrite->awAccepted) {
                if (activeWrite->control == "MSI")
                    fatal_if(!msiIdPool.release(activeWrite->axiId),
                             "%s: abandoned MSI ID is invalid", name());
                activeWrite.reset();
                stage = Stage::Done;
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
                stage = Stage::Done;
            } else {
                consumeR();
            }
        } else if (msiWrites.empty()) {
            stage = Stage::Done;
            recorder->setMetric("frontend_drained", 1);
        }
        if (recorder->metric("frontend_drained") == 0)
            scheduleTick();
        return;
    }
    pollWriteResponses();
    if (recorder->fatalPending()) {
        if (activeWrite && activeWrite->awAccepted &&
            activeWrite->nextBeat == activeWrite->beats.size())
            consumeB();
        else if (activeRead && activeRead->accepted)
            consumeR();
        scheduleTick();
        return;
    }
    switch (stage) {
      case Stage::Doorbell:
        processDoorbells();
        break;
      case Stage::SqRead:
      case Stage::SqReadResponse:
      case Stage::ParameterRead:
      case Stage::ParameterReadResponse:
      case Stage::PromptRead:
      case Stage::PromptReadResponse:
        consumeR();
        break;
      case Stage::SqHead:
      case Stage::Output:
      case Stage::Metadata:
      case Stage::CqEntry:
      case Stage::CqTail:
        driveWrite();
        break;
      case Stage::Msi:
        if (activeWrite)
            driveWrite();
        else
            startMsi();
        break;
      case Stage::SqHeadResponse:
      case Stage::OutputResponse:
      case Stage::MetadataResponse:
      case Stage::CqEntryResponse:
      case Stage::CqTailResponse:
      case Stage::MsiResponse:
        consumeB();
        break;
      case Stage::Capacity:
        recorder->requestNormalCommit();
        break;
      case Stage::Done:
        processDoorbells();
        if (stage == Stage::Done && doorbells.empty() &&
            completionLedger.liveCount() == 0 &&
            completionLedger.msiRobEntries() == 0)
            recorder->setMetric("frontend_drained", 1);
        else
            scheduleTick();
        return;
    }
    if (stage != Stage::Done &&
        (stage != Stage::Doorbell || !doorbells.empty() ||
         sqConsumer < advertisedSqTail ||
         completionLedger.liveCount() != 0))
        scheduleTick();
}

}
}

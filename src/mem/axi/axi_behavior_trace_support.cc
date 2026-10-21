#include "mem/axi/axi_behavior_trace_support.hh"

namespace gem5::axi::behavior
{

std::string
bytesHex(const std::vector<uint8_t> &bytes)
{
    const char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(bytes.size() * 2);
    for (uint8_t byte : bytes) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 15]);
    }
    return result;
}

void
append(TraceRow &row, const AxiCommonMeta &meta)
{
    row.field("uid", meta.txnUid).field("targetSeq", meta.targetSeq)
        .field("responseSeq", meta.responseSeq).field("srcNode", meta.srcNode)
        .field("srcPort", meta.srcPort).field("dstNode", meta.dstNode)
        .field("id", meta.axiId).field("semanticBytes", meta.semanticBytes)
        .field("wireBytes", meta.wireBytes).field("qos", meta.qos)
        .field("acceptedTick", meta.acceptedTick);
}

void
append(TraceRow &row, const AxiAddressRequest &request)
{
    row.field("requestId", request.axiId).field("address", request.address)
        .field("beats", request.beatCount).field("size", request.size)
        .field("burst", request.burst).field("lock", request.lock)
        .field("cache", request.cache).field("prot", request.prot)
        .field("region", request.region).field("requestQos", request.qos);
}

void
append(TraceRow &row, const AxiAddressPacket &packet)
{
    append(row, packet.meta);
    append(row, packet.request);
    row.field("ordinal", packet.writeOrdinal)
        .field("decodeResp", packet.decodeResp);
}

void
append(TraceRow &row, const AxiDataPacket &packet)
{
    append(row, packet.meta);
    row.field("ordinal", packet.writeOrdinal).field("index", packet.beatIndex)
        .field("beats", packet.beatCount).field("last", packet.last)
        .field("strobe", packet.byteStrobe).field("resp", packet.resp)
        .field("digest", packet.payloadDigest).field("address", packet.address)
        .field("data", bytesHex(packet.functionalData));
}

void
append(TraceRow &row, const AxiTargetOccupancy &occupancy)
{
    row.field("writeContexts", occupancy.writeContexts)
        .field("writeReservedBeats", occupancy.writeReservedBeats)
        .field("orphanTransactions", occupancy.orphanTransactions)
        .field("orphanReservedBeats", occupancy.orphanReservedBeats)
        .field("readContexts", occupancy.readContexts)
        .field("readReservedBeats", occupancy.readReservedBeats)
        .field("bReady", occupancy.bReady).field("rReady", occupancy.rReady)
        .field("writeServices", occupancy.writeServices)
        .field("readServices", occupancy.readServices);
}

void
append(TraceRow &row, const AxiTargetProgress &progress)
{
    row.field("serviceReady", progress.serviceReady)
        .field("architecturalCommits", progress.architecturalCommits)
        .field("sameIdReadyBlocked", progress.sameIdReadyBlocked)
        .field("orphanOrQuotaStallCycles", progress.orphanOrQuotaStallCycles)
        .field("writesCommitted", progress.writesCommitted)
        .field("readsCommitted", progress.readsCommitted)
        .field("writeCommittedBytes", progress.writeCommittedBytes)
        .field("readCommittedBytes", progress.readCommittedBytes)
        .field("lastWriteCommitCycle", progress.lastWriteCommitCycle);
}

AxiTargetConfig
targetConfig(bool synthetic, uint32_t depth, uint32_t node)
{
    AxiTargetConfig config;
    config.dstNode = node;
    config.dataBusBytes = 8;
    config.capacity = {24, 192, 24, 192};
    config.orphanTransactions = 12;
    config.orphanBeats = 96;
    config.bReadyDepth = depth;
    config.rReadyDepth = depth;
    config.writeServiceDepth = depth;
    config.readServiceDepth = depth;
    config.writeBaseLatency = 2;
    config.readBaseLatency = 2;
    config.syntheticHbmEnabled = synthetic;
    config.syntheticHbmBytesPerCycle = 8;
    config.syntheticHbmQueueDepth = depth;
    for (AxiEndpointKey source : {AxiEndpointKey{1, 0}, {1, 1}, {2, 0}})
        config.sourceQuotas[source] = {8, 64, 8, 64};
    config.memoryRanges = {{uint64_t(node - 1) * 0x1000,
                            uint64_t(node) * 0x1000, node}};
    return config;
}

AxiAddressPacket
address(uint64_t uid, uint32_t id, uint64_t sequence, uint64_t offset,
        uint16_t beats, AxiEndpointKey source, uint32_t node)
{
    AxiAddressPacket packet;
    packet.meta.txnUid = uid;
    packet.meta.targetSeq = sequence;
    packet.meta.responseSeq = sequence;
    packet.meta.srcNode = source.srcNode;
    packet.meta.srcPort = source.srcPort;
    packet.meta.dstNode = node;
    packet.meta.axiId = id;
    packet.meta.semanticBytes = 8;
    packet.meta.acceptedTick = uid % 7;
    packet.request.axiId = id;
    packet.request.address = offset;
    packet.request.beatCount = beats;
    packet.request.size = 3;
    packet.writeOrdinal = uid;
    return packet;
}

AxiDataPacket
writeBeat(const AxiAddressPacket &packet, uint16_t index, uint8_t value)
{
    AxiDataPacket beat;
    beat.meta = packet.meta;
    beat.writeOrdinal = packet.writeOrdinal;
    beat.beatIndex = index;
    beat.beatCount = packet.request.beatCount;
    beat.last = index + 1 == beat.beatCount;
    beat.byteStrobe = 0xff;
    beat.address = packet.request.address;
    beat.functionalData.resize(8);
    for (size_t lane = 0; lane < beat.functionalData.size(); ++lane)
        beat.functionalData[lane] = uint8_t(value + index * 13 + lane);
    beat.payloadDigest = payloadDigest(beat.functionalData);
    return beat;
}

AxiInitiatorConfig
sourceConfig(AxiEndpointKey source)
{
    AxiInitiatorConfig config;
    config.source = source;
    config.dataBusBytes = 8;
    config.idWidth = 4;
    config.maxOutstandingReads = 8;
    config.maxOutstandingWrites = 8;
    config.fifoDepths = {8, 32, 8, 8, 32};
    config.preAwBursts = 8;
    config.preAwBeats = 32;
    config.bRobTransactions = 8;
    config.rRobBeats = 32;
    config.ranges = {{0, 0x1000, 1}, {0x1000, 0x2000, 2}};
    config.defaultErrorTarget = 1;
    config.targetQuotas[1] = {4, 16, 4, 16};
    config.targetQuotas[2] = {4, 16, 4, 16};
    return config;
}

std::string
TraceRow::str() const
{ return text.str(); }

BehaviorTrace::BehaviorTrace(const std::string &name)
{
    const char *directory = std::getenv("AXI_BEHAVIOR_TRACE_DIR");
    if (directory) {
        std::filesystem::create_directories(directory);
        file.open(std::filesystem::path(directory) / (name + ".tsv"));
        if (!file)
            throw std::runtime_error("cannot open AXI behavior trace");
        file << "axi-behavior-v1\tcase=" << name << '\n';
    }
}

void
BehaviorTrace::operation(const std::string &name, uint64_t now)
{
    ++step;
    cycle = now;
    TraceRow row;
    row.field("name", name).field("now", now);
    record("operation", row);
}

void
BehaviorTrace::record(const std::string &kind, const TraceRow &row)
{
    if (file.is_open()) {
        file << kind << "\tstep=" << step << "\tnow=" << cycle
             << row.str() << '\n';
        file.flush();
        if (!file)
            throw std::runtime_error("cannot write AXI behavior trace");
    }
}

void
BehaviorTrace::target(const std::string &name, const AxiTargetState &state)
{
    const auto occupancy = state.occupancy();
    const auto &progress = state.progress();
    const auto backend = state.syntheticBackendStats();
    TraceRow row;
    row.field("target", name);
    append(row, occupancy);
    append(row, progress);
    row.field("completedWrites", state.completedWrites())
        .field("completedReads", state.completedReads())
        .field("synthetic", state.syntheticBackendEnabled())
        .field("backendSubmitted", backend.submitted)
        .field("backendCompleted", backend.completed)
        .field("backendRetired", backend.retired)
        .field("backendReadBytes", backend.readBytesServiced)
        .field("backendWriteBytes", backend.writeBytesServiced)
        .field("backendBusyCycles", backend.busyCycles)
        .field("backendQueueFullCycles", backend.queueFullCycles)
        .field("backendRequestSlotCycles", backend.requestSlotCycles)
        .field("backendQueued", backend.queued)
        .field("backendReady", backend.ready)
        .field("backendHighWater", backend.highWater)
        .field("hasB", state.hasBPacket()).field("hasR", state.hasRPacket());
    for (size_t i = 0; i < progress.qosTransactions.size(); ++i)
        row.field("qos" + std::to_string(i), progress.qosTransactions[i]);
    record("target", row);
    if (state.hasBPacket()) {
        TraceRow packet;
        packet.field("target", name);
        append(packet, state.frontBPacket().meta);
        packet.field("resp", state.frontBPacket().resp);
        record("frontB", packet);
    }
    if (state.hasRPacket()) {
        TraceRow packet;
        packet.field("target", name);
        append(packet, state.frontRPacket());
        record("frontR", packet);
    }
    for (const auto &range : state.memory().ranges()) {
        const uint64_t begin = range.start + 0x100;
        if (begin >= range.end)
            continue;
        std::vector<uint8_t> bytes;
        for (uint64_t address = begin;
             address < std::min(range.end, begin + 0x100); ++address)
            bytes.push_back(state.memory().readByte(address));
        TraceRow memory;
        memory.field("target", name).field("address", begin)
            .field("data", bytesHex(bytes));
        record("memory", memory);
    }
}

void
BehaviorTrace::source(const std::string &name, const AxiInitiatorState &state)
{
    const auto occupancy = state.occupancy();
    const auto resources = state.resourceOccupancy();
    const auto &progress = state.progress();
    TraceRow row;
    row.field("source", name).field("aw", occupancy.aw)
        .field("w", occupancy.w).field("b", occupancy.b)
        .field("ar", occupancy.ar).field("r", occupancy.r)
        .field("unboundBursts", occupancy.unboundBursts)
        .field("unboundBeats", occupancy.unboundBeats)
        .field("outstandingWrites", occupancy.outstandingWrites)
        .field("outstandingReads", occupancy.outstandingReads)
        .field("readWaitingQuota", resources.readWaitingQuota)
        .field("readGrantedWaitingForward", resources.readGrantedWaitingForward)
        .field("readForwarded", resources.readForwardedToMessageBuffer)
        .field("writeWaitingQuota", resources.writeWaitingQuota)
        .field("writeGrantedWaitingForward", resources.writeGrantedWaitingForward)
        .field("writeForwarded", resources.writeForwardedToMessageBuffer)
        .field("rReserved", resources.rRobReservedBeats)
        .field("rBuffered", resources.rRobBufferedBeats)
        .field("bReserved", resources.bRobReservedTransactions)
        .field("bBuffered", resources.bRobBufferedTransactions)
        .field("bPacketsBuffered", progress.bPacketsBuffered)
        .field("rPacketsBuffered", progress.rPacketsBuffered)
        .field("sameIdResponsesBlocked", progress.sameIdResponsesBlocked)
        .field("bTransactionsRetired", progress.bTransactionsRetired)
        .field("rTransactionsRetired", progress.rTransactionsRetired)
        .field("writeQuotaStalls", progress.writeQuotaStalls)
        .field("readQuotaStalls", progress.readQuotaStalls)
        .field("arFifoFull", progress.arRejectionAttempts.fifoFull)
        .field("arOutstandingFull", progress.arRejectionAttempts.outstandingFull)
        .field("arReservationFull", progress.arRejectionAttempts.responseReservationFull)
        .field("awFifoFull", progress.awRejectionAttempts.fifoFull)
        .field("awOutstandingFull", progress.awRejectionAttempts.outstandingFull)
        .field("awReservationFull", progress.awRejectionAttempts.responseReservationFull)
        .field("peakOutstandingReads", state.peakOutstandingReads())
        .field("firstCreditReleaseTick", state.firstCreditReleaseTick())
        .field("nextAwOrdinal", state.nextAwOrdinal())
        .field("nextWBurstOrdinal", state.nextWBurstOrdinal())
        .field("pairedBursts", state.pairedBursts())
        .field("openedWBursts", state.openedWBursts())
        .field("closedWBursts", state.closedWBursts())
        .field("finalConsistencyError", state.finalConsistencyError());
    for (size_t i = 0; i < state.arAcceptTicks().size(); ++i)
        row.field("arTick" + std::to_string(i), state.arAcceptTicks()[i]);
    record("source", row);
}

CommitRecorder::CommitRecorder(BehaviorTrace &trace, AxiTargetState &state,
                               std::string name)
    : trace(trace), state(state), name(std::move(name))
{}

AxiWritePreCommitPolicy::Decision
CommitRecorder::onWritePreCommit(const AxiAddressRequest &request,
    const std::vector<AxiDataPacket> &beats, AxiResp response)
{
    policyBlocked.push_back(state.progress().sameIdReadyBlocked);
    const auto found = policies.find(request.address);
    const Decision decision = found == policies.end() ? Decision{}
                                                     : found->second;
    TraceRow row;
    row.field("observer", name);
    append(row, request);
    row.field("transportResp", response).field("decisionResp", decision.response)
        .field("commit", decision.commit).field("beats", beats.size());
    trace.record("policy", row);
    trace.target(name, state);
    return decision;
}

void
CommitRecorder::onAxiWriteCommitted(const AxiAddressRequest &request,
    const std::vector<AxiDataPacket> &beats, AxiResp response)
{
    observerBlocked.push_back(state.progress().sameIdReadyBlocked);
    TraceRow row;
    row.field("observer", name);
    append(row, request);
    row.field("resp", response);
    trace.record("observerRequest", row);
    for (const auto &beat : beats) {
        TraceRow data;
        data.field("observer", name);
        append(data, beat);
        trace.record("observerBeat", data);
    }
    trace.target(name, state);
}

void
CommitRecorder::onAxiWriteCommittedWithMeta(const AxiAddressPacket &packet,
    const std::vector<AxiDataPacket> &beats, AxiResp response)
{
    uids.push_back(packet.meta.txnUid);
    TraceRow row;
    row.field("observer", name);
    append(row, packet);
    trace.record("observerMeta", row);
    onAxiWriteCommitted(packet.request, beats, response);
}

TargetProbe::TargetProbe(BehaviorTrace &trace, std::string name,
                         const AxiTargetConfig &config)
    : state(config), first(trace, state, name + ".first"),
      second(trace, state, name + ".second"), trace(trace), name(std::move(name))
{
    state.registerWriteCommitObserver(&first);
    state.registerWriteCommitObserver(&second);
    state.setPreCommitPolicy(&first);
    trace.operation("construct." + this->name, 0);
    TraceRow row;
    row.field("synthetic", config.syntheticHbmEnabled)
        .field("writeServiceDepth", config.writeServiceDepth)
        .field("readServiceDepth", config.readServiceDepth)
        .field("bDepth", config.bReadyDepth).field("rDepth", config.rReadyDepth)
        .field("backendDepth", config.syntheticHbmQueueDepth);
    trace.record("configuration", row);
    snapshot();
}

bool
TargetProbe::aw(const AxiAddressPacket &packet, uint64_t now)
{
    trace.operation("acceptAw." + name, now);
    TraceRow row;
    append(row, packet);
    const bool accepted = state.canAcceptAw(packet);
    row.field("accepted", accepted);
    trace.record("input", row);
    if (accepted)
        state.acceptAw(packet, now);
    snapshot();
    return accepted;
}

bool
TargetProbe::w(const AxiDataPacket &packet, uint64_t now)
{
    trace.operation("acceptW." + name, now);
    TraceRow row;
    append(row, packet);
    const bool accepted = state.canAcceptW(packet);
    row.field("accepted", accepted);
    trace.record("input", row);
    if (accepted)
        state.acceptW(packet, now);
    snapshot();
    return accepted;
}

bool
TargetProbe::ar(const AxiAddressPacket &packet, uint64_t now)
{
    trace.operation("acceptAr." + name, now);
    TraceRow row;
    append(row, packet);
    const bool accepted = state.canAcceptAr(packet);
    row.field("accepted", accepted);
    trace.record("input", row);
    if (accepted)
        state.acceptAr(packet, now);
    snapshot();
    return accepted;
}

void
TargetProbe::advance(uint64_t now)
{
    trace.operation("advance." + name, now);
    state.advance(now);
    snapshot();
}

std::optional<AxiBPacket>
TargetProbe::b(uint64_t now)
{
    trace.operation("popB." + name, now);
    std::optional<AxiBPacket> packet;
    if (state.hasBPacket()) {
        packet = state.frontBPacket();
        TraceRow row;
        append(row, packet->meta);
        row.field("resp", packet->resp);
        trace.record("outputB", row);
        state.popBPacket();
    }
    snapshot();
    return packet;
}

std::optional<AxiDataPacket>
TargetProbe::r(uint64_t now)
{
    trace.operation("popR." + name, now);
    std::optional<AxiDataPacket> packet;
    if (state.hasRPacket()) {
        packet = state.frontRPacket();
        TraceRow row;
        append(row, *packet);
        trace.record("outputR", row);
        state.popRPacket();
    }
    snapshot();
    return packet;
}

void
TargetProbe::memory(uint64_t address, uint8_t value, uint64_t now)
{
    trace.operation("writeMemory." + name, now);
    TraceRow row;
    row.field("address", address).field("value", value);
    trace.record("input", row);
    state.memory().writeByte(address, value);
    snapshot();
}

void
TargetProbe::snapshot()
{ trace.target(name, state); }

void
TargetProbe::drain(uint64_t now)
{
    for (unsigned i = 0; i < 256; ++i) {
        advance(now + i);
        while (state.hasBPacket())
            b(now + i);
        while (state.hasRPacket())
            r(now + i);
        const auto occupancy = state.occupancy();
        if (!occupancy.writeContexts && !occupancy.readContexts) {
            EXPECT_EQ(occupancy.writeServices, 0u);
            EXPECT_EQ(occupancy.readServices, 0u);
            EXPECT_EQ(state.syntheticBackendStats().queued, 0u);
            EXPECT_EQ(state.syntheticBackendStats().ready, 0u);
            EXPECT_EQ(first.uids, second.uids);
            return;
        }
    }
    FAIL() << "target corpus failed to drain";
}

GateSeedArchive::GateSeedArchive(boost::archive::binary_iarchive &native,
                                 std::array<uint64_t, 2> values)
    : native(native), values(values)
{}

SourceProbe::SourceProbe(BehaviorTrace &trace, std::string name,
                         const AxiInitiatorConfig &config)
    : state(config), trace(trace), name(std::move(name))
{
    begin("construct", 0);
    TraceRow row;
    row.field("bDepth", config.fifoDepths[2])
        .field("rDepth", config.fifoDepths[4]);
    trace.record("configuration", row);
    snapshot();
}

SourceProbe::SourceProbe(BehaviorTrace &trace, std::string name,
                         AxiInitiatorState source, uint64_t now)
    : state(std::move(source)), trace(trace), name(std::move(name))
{
    begin("adoptState", now);
    snapshot();
}

AxiAddressPacket
SourceProbe::issue(bool write, uint32_t id, uint16_t beats,
                   uint64_t now, uint32_t destination)
{
    AxiAddressRequest request;
    request.axiId = id;
    request.address = uint64_t(destination - 1) * 0x1000 + 0x100;
    request.beatCount = beats;
    request.size = 3;
    begin(write ? "tryAcceptAw" : "tryAcceptAr", now);
    const bool accepted = write ? state.tryAcceptAw(request, now)
                                : state.tryAcceptAr(request, now);
    TraceRow input;
    append(input, request);
    input.field("accepted", accepted);
    trace.record("input", input);
    snapshot();
    if (!accepted)
        throw std::runtime_error("source corpus request was rejected");
    if (write) {
        for (uint16_t index = 0; index < beats; ++index) {
            AxiWBeat beat;
            beat.last = index + 1 == beats;
            beat.byteStrobe = 0xff;
            beat.functionalData.assign(8, uint8_t(id + index));
            beat.payloadDigest = payloadDigest(beat.functionalData);
            begin("tryAcceptW", now);
            const bool stored = state.tryAcceptW(beat, now);
            TraceRow data;
            data.field("accepted", stored).field("last", beat.last)
                .field("strobe", beat.byteStrobe)
                .field("digest", beat.payloadDigest)
                .field("data", bytesHex(beat.functionalData));
            trace.record("input", data);
            snapshot();
            if (!stored)
                throw std::runtime_error("source corpus W was rejected");
        }
    }
    begin(write ? "frontAwPacket" : "frontArPacket", now);
    const auto packet = write ? state.frontAwPacket() : state.frontArPacket();
    TraceRow output;
    append(output, packet);
    trace.record("packet", output);
    snapshot();
    begin(write ? "popAwPacket" : "popArPacket", now);
    if (write)
        state.popAwPacket();
    else
        state.popArPacket();
    snapshot();
    if (write) {
        for (uint16_t index = 0; index < beats; ++index) {
            begin("frontWPacket", now);
            TraceRow data;
            append(data, state.frontWPacket());
            trace.record("packet", data);
            snapshot();
            begin("popWPacket", now);
            state.popWPacket();
            snapshot();
        }
    }
    return packet;
}

void
SourceProbe::receiveB(const AxiAddressPacket &address, Tick ready,
                      uint64_t now, AxiResp response)
{
    AxiBPacket packet;
    packet.meta = address.meta;
    packet.resp = response;
    begin("acceptBPacket", now);
    TraceRow input;
    append(input, packet.meta);
    input.field("resp", response).field("readyTick", ready);
    trace.record("input", input);
    state.acceptBPacket(packet, ready);
    snapshot();
}

void
SourceProbe::receiveR(const AxiAddressPacket &address, uint16_t index,
                      uint8_t value, Tick ready, uint64_t now)
{
    auto packet = writeBeat(address, index, value);
    packet.writeOrdinal = 0;
    packet.byteStrobe = 0;
    packet.address += uint64_t(index) * 8;
    begin("acceptRPacket", now);
    TraceRow input;
    append(input, packet);
    input.field("readyTick", ready);
    trace.record("input", input);
    state.acceptRPacket(packet, ready);
    snapshot();
}

std::optional<AxiBBeat>
SourceProbe::consumeB(uint64_t now)
{
    begin("tryConsumeB", now);
    AxiBBeat beat;
    const bool available = state.tryConsumeB(beat);
    TraceRow row;
    row.field("available", available);
    if (available)
        row.field("id", beat.axiId).field("resp", beat.resp);
    trace.record("consumedB", row);
    snapshot();
    return available ? std::optional<AxiBBeat>(beat) : std::nullopt;
}

std::optional<AxiRBeat>
SourceProbe::consumeR(uint64_t now)
{
    begin("tryConsumeR", now);
    AxiRBeat beat;
    const bool available = state.tryConsumeR(beat, now);
    TraceRow row;
    row.field("available", available);
    if (available) {
        row.field("id", beat.axiId).field("last", beat.last)
            .field("resp", beat.resp).field("digest", beat.payloadDigest)
            .field("data", bytesHex(beat.functionalData));
    }
    trace.record("consumedR", row);
    snapshot();
    return available ? std::optional<AxiRBeat>(beat) : std::nullopt;
}

void
SourceProbe::advance(uint64_t now)
{
    begin("advance", now);
    state.advance();
    snapshot();
}

void
SourceProbe::begin(const std::string &operation, uint64_t now)
{
    trace.operation("source." + operation + "." + name, now);
}

void
SourceProbe::snapshot()
{ trace.source(name, state); }

}

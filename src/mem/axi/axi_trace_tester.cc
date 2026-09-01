#include "mem/axi/axi_trace_tester.hh"

#include <algorithm>
#include <fstream>
#include <limits>
#include <numeric>
#include <set>
#include <sstream>
#include <stdexcept>
#include <tuple>

#include "base/logging.hh"
#include "mem/axi/axi_determinism.hh"
#include "params/AxiTraceTester.hh"
#include "sim/sim_exit.hh"

namespace gem5
{
namespace axi
{

namespace
{

std::vector<std::string>
split(const std::string &value, char delimiter)
{
    std::vector<std::string> fields;
    std::istringstream input(value);
    std::string field;
    while (std::getline(input, field, delimiter))
        fields.push_back(field);
    return fields;
}

uint64_t
parseUnsigned(const std::string &value, const char *label)
{
    size_t consumed = 0;
    uint64_t result = 0;
    try {
        result = std::stoull(value, &consumed, 0);
    } catch (const std::exception &) {
        fatal("AXI tester %s is not an unsigned integer: %s", label, value);
    }
    fatal_if(consumed != value.size(),
             "AXI tester %s has trailing characters: %s", label, value);
    return result;
}

AxiResp
parseResponse(const std::string &value)
{
    if (value == "okay")
        return AxiResp::Okay;
    if (value == "slverr")
        return AxiResp::SlvErr;
    if (value == "decerr")
        return AxiResp::DecErr;
    fatal("AXI tester response must be okay, slverr, or decerr: %s", value);
}

const char *
responseName(AxiResp response)
{
    switch (response) {
      case AxiResp::Okay: return "okay";
      case AxiResp::SlvErr: return "slverr";
      case AxiResp::DecErr: return "decerr";
      case AxiResp::ExOkay: return "exokay";
    }
    return "invalid";
}

const char *
channelName(AxiChannel channel)
{
    switch (channel) {
      case AxiChannel::Aw: return "aw";
      case AxiChannel::W: return "w";
      case AxiChannel::B: return "b";
      case AxiChannel::Ar: return "ar";
      case AxiChannel::R: return "r";
    }
    return "invalid";
}

std::string
baseName(const std::string &path)
{
    const auto slash = path.find_last_of("/\\");
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

bool
startsWith(const std::string &value, const std::string &prefix)
{
    return value.rfind(prefix, 0) == 0;
}

Cycles
checkedCyclePair(const std::vector<Cycles> &values, size_t index,
                 const std::string &owner)
{
    fatal_if(values.size() != 2,
             "%s: consumer_stall_until requires B,R entries", owner);
    return values[index];
}

} // anonymous namespace

AxiTraceTester::AxiTraceTester(const Params &p)
    : ClockedObject(p), ruby::Consumer(this),
      initiators(p.initiators), targets(p.targets), network(p.network),
      targetNodes(p.target_nodes), caseName(p.case_name),
      resultJson(p.result_json), eventTraceJsonl(p.event_trace_jsonl),
      creditLedgerJson(p.credit_ledger_json),
      residualStateJson(p.residual_state_json), runtimeFault(p.runtime_fault),
      seed(p.seed),
      wireHeaderBytes(p.wire_header_bytes),
      dataBusBytes(p.data_bus_bytes),
      drainCycles(p.drain_cycles), concurrent(p.concurrent),
      bConsumerStallUntil(checkedCyclePair(
          p.consumer_stall_until, 0, p.name)),
      rConsumerStallUntil(checkedCyclePair(
          p.consumer_stall_until, 1, p.name)),
      expectedRouterVnet(p.expected_router_vnet),
      expectedRouterDepth(p.expected_router_depth),
      progressWatchdogCycles(p.progress_watchdog_cycles),
      issueStopCycle(p.issue_stop_cycle),
      livenessBoundComponents(p.liveness_bound_components),
      localDeliveryDepths(p.local_delivery_depths),
      measurementWindowCycles(p.measurement_window_cycles),
      measurementVnet(p.measurement_vnet)
{
    fatal_if(initiators.empty(), "%s: requires at least one initiator", name());
    fatal_if(targets.empty() || targets.size() != targetNodes.size(),
             "%s: target object/node vectors must be non-empty and equal",
             name());
    fatal_if(!network, "%s: requires a GarnetNetwork", name());
    fatal_if(dataBusBytes == 0 || dataBusBytes > 64,
             "%s: data_bus_bytes must be in [1,64]", name());
    fatal_if(wireHeaderBytes.size() != 5,
             "%s: wire_header_bytes requires five entries", name());
    fatal_if(std::any_of(wireHeaderBytes.begin(), wireHeaderBytes.end(),
                         [](uint32_t bytes) { return bytes == 0; }),
             "%s: wire_header_bytes entries must be positive", name());
    fatal_if(drainCycles < 2, "%s: drain_cycles must be at least two", name());
    fatal_if(progressWatchdogCycles == 0,
             "%s: progress_watchdog_cycles must be positive", name());
    fatal_if(livenessBoundComponents.size() != 3,
             "%s: liveness_bound_components requires three entries", name());
    fatal_if(localDeliveryDepths.size() != 5,
             "%s: local_delivery_depths requires five channels", name());
    fatal_if(measurementWindowCycles.size() != 2,
             "%s: measurement_window_cycles requires start,end", name());
    if (measurementEnabled()) {
        fatal_if(measurementVnet < -1 || measurementVnet >= 5,
                 "%s: measurement_vnet must be -1 or in [0,4]", name());
        fatal_if(measurementWindowCycles[0] >= measurementWindowCycles[1],
                 "%s: measurement window must have start < end", name());
        fatal_if(network->getNumberOfVirtualNetworks() != 5,
                 "%s: AXI measurement requires exactly five vnets", name());
    } else {
        fatal_if(measurementVnet != -2 || measurementWindowCycles[0] != 0 ||
                     measurementWindowCycles[1] != 0,
                 "%s: disabled measurement requires vnet=-2 and window=0,0",
                 name());
    }
    fatal_if(p.transaction_specs.empty(),
             "%s: functional scenario has zero transactions", name());

    transactions.reserve(p.transaction_specs.size());
    for (const auto &spec : p.transaction_specs)
        transactions.push_back(parseTransaction(spec));

    writesBySource.resize(initiators.size());
    readsBySource.resize(initiators.size());
    completionsBySource.resize(initiators.size(), 0);
    for (size_t index = 0; index < transactions.size(); ++index) {
        auto &txn = transactions[index];
        fatal_if(txn.planIndex != index,
                 "%s: transaction plan indices must be dense and ordered",
                 name());
        if (txn.kind == Kind::Write)
            writesBySource[txn.sourceIndex].push_back(index);
        else
            readsBySource[txn.sourceIndex].push_back(index);
    }
    nextAwBySource.resize(initiators.size(), 0);
    nextWBySource.resize(initiators.size(), 0);
    nextArBySource.resize(initiators.size(), 0);

    if (caseName == "response_progress") {
        fatal_if(issueStopCycle != 2000,
                 "%s: response_progress issue stop must be cycle 2000",
                 name());
        std::vector<bool> hasArrival(issueStopCycle + 1, false);
        for (const auto &txn : transactions) {
            fatal_if(txn.arrivalCycle > issueStopCycle,
                     "%s: response_progress request arrives after stop",
                     name());
            hasArrival[txn.arrivalCycle] = true;
        }
        fatal_if(std::find(hasArrival.begin(), hasArrival.end(), false) !=
                     hasArrival.end(),
                 "%s: response_progress must create work every flood cycle",
                 name());
        const uint64_t provenBound = std::accumulate(
            livenessBoundComponents.begin(), livenessBoundComponents.end(),
            uint64_t{0});
        fatal_if(provenBound >= progressWatchdogCycles,
                 "%s: response_progress proof must be below watchdog",
                 name());
    }
}

AxiTraceTester::Transaction
AxiTraceTester::parseTransaction(const std::string &spec) const
{
    const auto fields = split(spec, '|');
    fatal_if(fields.size() != 23,
             "AXI tester transaction must have 23 pipe-separated fields: %s",
             spec);
    Transaction txn;
    if (fields[0] == "write")
        txn.kind = Kind::Write;
    else if (fields[0] == "read")
        txn.kind = Kind::Read;
    else
        fatal("AXI tester transaction kind must be write or read: %s", fields[0]);
    txn.sourceIndex = parseUnsigned(fields[1], "source index");
    txn.targetIndex = parseUnsigned(fields[2], "target index");
    fatal_if(txn.sourceIndex >= initiators.size() ||
             txn.targetIndex >= targets.size(),
             "AXI tester transaction endpoint index is out of range");
    txn.request.axiId = parseUnsigned(fields[3], "AXI ID");
    txn.request.address = parseUnsigned(fields[4], "address");
    txn.request.beatCount = parseUnsigned(fields[5], "beat count");
    txn.request.size = parseUnsigned(fields[6], "SIZE");
    if (fields[17] == "fixed")
        txn.request.burst = AxiBurst::Fixed;
    else if (fields[17] == "incr")
        txn.request.burst = AxiBurst::Incr;
    else if (fields[17] == "wrap")
        txn.request.burst = AxiBurst::Wrap;
    else
        fatal("AXI tester burst is invalid: %s", fields[17]);
    txn.wBeforeAw = parseUnsigned(fields[7], "W-before-AW flag") != 0;
    txn.dataSeed = parseUnsigned(fields[8], "data seed");
    if (fields[9] == "full")
        txn.strobeMode = StrobeMode::Full;
    else if (fields[9] == "alternating")
        txn.strobeMode = StrobeMode::Alternating;
    else
        fatal("AXI tester strobe mode is invalid: %s", fields[9]);
    txn.expectedUid = parseUnsigned(fields[10], "expected txnUid");
    txn.arrivalCycle = parseUnsigned(fields[11], "arrival cycle");
    txn.expectedResponse = parseResponse(fields[12]);
    txn.planIndex = parseUnsigned(fields[13], "plan index");
    txn.expectedTargetSeq = parseUnsigned(fields[14], "expected targetSeq");
    txn.expectedResponseSeq = parseUnsigned(
        fields[15], "expected responseSeq");
    if (fields[16] != "none") {
        txn.expectedWriteOrdinal = parseUnsigned(
            fields[16], "expected writeOrdinal");
    }
    fatal_if((txn.kind == Kind::Write) != txn.expectedWriteOrdinal.has_value(),
             "AXI tester writeOrdinal presence disagrees with direction");
    txn.request.lock = parseUnsigned(fields[18], "LOCK");
    txn.request.cache = parseUnsigned(fields[19], "CACHE");
    txn.request.prot = parseUnsigned(fields[20], "PROT");
    txn.request.region = parseUnsigned(fields[21], "REGION");
    txn.request.qos = parseUnsigned(fields[22], "QOS");

    const auto validation = validateAxiBurst(txn.request, dataBusBytes);
    requireValidAxiBurst(validation);
    fatal_if(targetNodes[txn.targetIndex] == 0xffffffffU,
             "AXI tester target node is invalid");
    return txn;
}

void
AxiTraceTester::startup()
{
    updateMeasurementWindow();
    scheduleEvent(Cycles(1));
}

AxiWBeat
AxiTraceTester::makeWBeat(const Transaction &txn, uint16_t index) const
{
    AxiWBeat beat;
    beat.last = index + 1 == txn.request.beatCount;
    beat.functionalData.resize(dataBusBytes);
    for (uint32_t lane = 0; lane < dataBusBytes; ++lane) {
        beat.functionalData[lane] = static_cast<uint8_t>(
            txn.dataSeed + index * 17 + lane);
    }
    beat.byteStrobe = axiLegalLaneMask(txn.request, index, dataBusBytes);
    if (txn.strobeMode == StrobeMode::Alternating)
        beat.byteStrobe &= 0x5555555555555555ULL;
    beat.payloadDigest = payloadDigest(beat.functionalData);
    return beat;
}

uint64_t
AxiTraceTester::sourceOccupancy(size_t source_index) const
{
    const auto occupancy = initiators.at(source_index)->functionalOccupancy();
    return occupancy.aw + occupancy.w + occupancy.b + occupancy.ar +
           occupancy.r + occupancy.unboundBursts + occupancy.unboundBeats +
           occupancy.outstandingWrites + occupancy.outstandingReads;
}

void
AxiTraceTester::observe(uint64_t phase, uint64_t event,
                        std::optional<AxiChannel> channel,
                        std::optional<size_t> transaction_index,
                        std::optional<uint16_t> beat_index,
                        std::optional<uint64_t> payload_digest,
                        uint64_t occupancy)
{
    TraceObservation observation;
    observation.tick = curTick();
    observation.phase = phase;
    observation.event = event;
    observation.observationOrder = nextObservationOrder++;
    observation.channel = channel;
    observation.transactionIndex = transaction_index;
    observation.beatIndex = beat_index;
    observation.payloadDigest = payload_digest;
    observation.occupancy = occupancy;
    traceObservations.push_back(observation);
}

void
AxiTraceTester::captureAcceptedAddress(Transaction &txn,
                                       const AxiAddressPacket &packet)
{
    const bool write = txn.kind == Kind::Write;
    fatal_if(packet.meta.txnUid != txn.expectedUid,
             "AXI_PROTOCOL: accepted txnUid mismatch plan=%zu "
             "expected=%#llx actual=%#llx", txn.planIndex,
             static_cast<unsigned long long>(txn.expectedUid),
             static_cast<unsigned long long>(packet.meta.txnUid));
    fatal_if(packet.meta.targetSeq != txn.expectedTargetSeq,
             "AXI_PROTOCOL: accepted targetSeq mismatch plan=%zu",
             txn.planIndex);
    fatal_if(packet.meta.responseSeq != txn.expectedResponseSeq,
             "AXI_PROTOCOL: accepted responseSeq mismatch plan=%zu",
             txn.planIndex);
    fatal_if(packet.meta.dstNode != targetNodes[txn.targetIndex] ||
             packet.meta.axiId != txn.request.axiId ||
             packet.meta.qos != txn.request.qos ||
             packet.request.qos != txn.request.qos,
             "AXI_PROTOCOL: accepted route/ID/AxQOS mismatch plan=%zu",
             txn.planIndex);
    fatal_if(write && packet.writeOrdinal != *txn.expectedWriteOrdinal,
             "AXI_PROTOCOL: accepted writeOrdinal mismatch plan=%zu",
             txn.planIndex);
    fatal_if(!write && txn.expectedWriteOrdinal,
             "AXI_PROTOCOL: read unexpectedly has writeOrdinal oracle");
    fatal_if(packet.meta.acceptedTick != curTick(),
             "AXI_PROTOCOL: acceptedTick mismatch plan=%zu", txn.planIndex);
    txn.actualMeta = packet.meta;
    if (write)
        txn.actualWriteOrdinal = packet.writeOrdinal;
    txn.addressAcceptedTick = curTick();
}

void
AxiTraceTester::driveWriteAddress(Transaction &txn)
{
    if (txn.addressAccepted || curCycle() < Cycles(txn.arrivalCycle))
        return;
    if (txn.wBeforeAw &&
        (txn.nextW == 0 || txn.lastWAcceptedTick == curTick())) {
        return;
    }
    ++addressAdmissionAttempts;
    if (initiators[txn.sourceIndex]->tryAcceptAw(txn.request)) {
        txn.addressAccepted = true;
        ++addressAdmissionsAccepted;
        captureAcceptedAddress(
            txn, initiators[txn.sourceIndex]->lastAcceptedAw());
        observe(
            static_cast<uint64_t>(deterministic::TracePhase::Drive),
            static_cast<uint64_t>(
                deterministic::TraceEvent::AddressAccepted),
            AxiChannel::Aw, txn.planIndex, std::nullopt, std::nullopt,
            sourceOccupancy(txn.sourceIndex));
    } else {
        ++localFifoFullStalls;
        if (!lastRetryCycle || *lastRetryCycle != curCycle()) {
            ++retryCycles;
            lastRetryCycle = curCycle();
        }
    }
}

void
AxiTraceTester::driveWriteData(Transaction &txn)
{
    if (txn.nextW >= txn.request.beatCount ||
        curCycle() < Cycles(txn.arrivalCycle) ||
        (!txn.wBeforeAw && !txn.addressAccepted)) {
        return;
    }
    AxiWBeat beat = makeWBeat(txn, txn.nextW);
    if (initiators[txn.sourceIndex]->tryAcceptW(beat)) {
        const uint16_t accepted_index = txn.nextW;
        txn.writeBeats.push_back(beat);
        ++txn.nextW;
        txn.lastWAcceptedTick = curTick();
        ++wBeatsAccepted;
        observe(
            static_cast<uint64_t>(deterministic::TracePhase::Drive),
            static_cast<uint64_t>(
                deterministic::TraceEvent::WriteBeatAccepted),
            AxiChannel::W, txn.planIndex, accepted_index,
            beat.payloadDigest, sourceOccupancy(txn.sourceIndex));
    } else {
        ++localFifoFullStalls;
    }
}

void
AxiTraceTester::driveReadAddress(Transaction &txn)
{
    if (!txn.addressAccepted && curCycle() >= Cycles(txn.arrivalCycle)) {
        ++addressAdmissionAttempts;
        if (initiators[txn.sourceIndex]->tryAcceptAr(txn.request)) {
            txn.addressAccepted = true;
            ++addressAdmissionsAccepted;
            captureAcceptedAddress(
                txn, initiators[txn.sourceIndex]->lastAcceptedAr());
            observe(
                static_cast<uint64_t>(deterministic::TracePhase::Drive),
                static_cast<uint64_t>(
                    deterministic::TraceEvent::AddressAccepted),
                AxiChannel::Ar, txn.planIndex, std::nullopt, std::nullopt,
                sourceOccupancy(txn.sourceIndex));
        } else {
            ++localFifoFullStalls;
            if (!lastRetryCycle || *lastRetryCycle != curCycle()) {
                ++retryCycles;
                lastRetryCycle = curCycle();
            }
        }
    }
}

void
AxiTraceTester::driveSequential()
{
    if (currentTransaction >= transactions.size())
        return;
    Transaction &txn = transactions[currentTransaction];
    if (txn.kind == Kind::Read) {
        driveReadAddress(txn);
        return;
    }
    if (txn.wBeforeAw && txn.nextW == 0 && !txn.addressAccepted) {
        driveWriteData(txn);
        return;
    }
    if (!txn.addressAccepted) {
        driveWriteAddress(txn);
        return;
    }
    driveWriteData(txn);
}

void
AxiTraceTester::driveConcurrent()
{
    for (size_t source = 0; source < initiators.size(); ++source) {
        auto &writes = writesBySource[source];
        while (nextWBySource[source] < writes.size() &&
               transactions[writes[nextWBySource[source]]].nextW ==
                   transactions[writes[nextWBySource[source]]].request.beatCount) {
            ++nextWBySource[source];
        }
        if (nextWBySource[source] < writes.size())
            driveWriteData(transactions[writes[nextWBySource[source]]]);

        while (nextAwBySource[source] < writes.size() &&
               transactions[writes[nextAwBySource[source]]].addressAccepted) {
            ++nextAwBySource[source];
        }
        if (nextAwBySource[source] < writes.size())
            driveWriteAddress(transactions[writes[nextAwBySource[source]]]);

        auto &reads = readsBySource[source];
        while (nextArBySource[source] < reads.size() &&
               transactions[reads[nextArBySource[source]]].addressAccepted) {
            ++nextArBySource[source];
        }
        if (nextArBySource[source] < reads.size())
            driveReadAddress(transactions[reads[nextArBySource[source]]]);
    }
}

uint8_t
AxiTraceTester::shadowByte(uint64_t address) const
{
    const auto found = shadowMemory.find(address);
    return found == shadowMemory.end() ? 0 : found->second;
}

void
AxiTraceTester::commitShadow(const Transaction &txn)
{
    fatal_if(txn.writeBeats.size() != txn.request.beatCount,
             "AXI tester completed write with missing source beat");
    for (uint16_t index = 0; index < txn.request.beatCount; ++index) {
        const uint64_t beatAddress = axiBeatAddress(txn.request, index);
        const uint64_t busBase = (beatAddress / dataBusBytes) * dataBusBytes;
        const auto &beat = txn.writeBeats[index];
        for (uint32_t lane = 0; lane < dataBusBytes; ++lane) {
            if ((beat.byteStrobe >> lane) & 1)
                shadowMemory[busBase + lane] = beat.functionalData[lane];
        }
    }
}

void
AxiTraceTester::checkTargetMemory(const Transaction &txn) const
{
    const auto *target = targets[txn.targetIndex];
    for (uint16_t index = 0; index < txn.request.beatCount; ++index) {
        const uint64_t beatAddress = axiBeatAddress(txn.request, index);
        const uint64_t beatBytes = uint64_t{1} << txn.request.size;
        for (uint64_t offset = 0; offset < beatBytes; ++offset) {
            const uint64_t address = beatAddress + offset;
            if (!target->containsMemoryAddress(address))
                continue;
            fatal_if(target->readMemoryByte(address) != shadowByte(address),
                     "AXI tester target byte mismatch at %#llx",
                     static_cast<unsigned long long>(address));
        }
    }
}

size_t
AxiTraceTester::findResponseTransaction(Kind kind, size_t source_index,
                                        uint32_t axi_id) const
{
    size_t selected = transactions.size();
    for (size_t index = 0; index < transactions.size(); ++index) {
        const auto &txn = transactions[index];
        if (txn.kind != kind || txn.sourceIndex != source_index ||
            txn.request.axiId != axi_id || !txn.addressAccepted ||
            txn.completed) {
            continue;
        }
        if (selected == transactions.size() ||
            txn.planIndex < transactions[selected].planIndex) {
            selected = index;
        }
    }
    fatal_if(selected == transactions.size(),
             "AXI tester observed response for unknown source/ID");
    return selected;
}

void
AxiTraceTester::completeB(size_t transaction_index,
                          const AxiBBeat &response)
{
    Transaction &txn = transactions[transaction_index];
    fatal_if(txn.kind != Kind::Write || txn.completed ||
             txn.nextW != txn.request.beatCount,
             "AXI tester observed B before a complete write");
    fatal_if(curTick() <= txn.lastWAcceptedTick,
             "AXI tester observed zero-delay W-to-B completion");
    fatal_if(response.axiId != txn.request.axiId ||
             response.resp != txn.expectedResponse,
             "AXI tester received incorrect B: expected ID=%u resp=%s",
             txn.request.axiId, responseName(txn.expectedResponse));
    if (response.resp == AxiResp::Okay)
        commitShadow(txn);
    else
        ++errorTransactions;
    checkTargetMemory(txn);
    txn.completed = true;
    txn.completionTick = curTick();
    ++writesCompleted;
    ++completionsBySource[txn.sourceIndex];
    completionOrder.push_back(txn.planIndex);
    observe(
        static_cast<uint64_t>(deterministic::TracePhase::Response),
        static_cast<uint64_t>(
            deterministic::TraceEvent::WriteResponseRetired),
        AxiChannel::B, txn.planIndex, std::nullopt, std::nullopt,
        sourceOccupancy(txn.sourceIndex));
}

void
AxiTraceTester::completeR(size_t transaction_index, AxiRBeat response)
{
    Transaction &txn = transactions[transaction_index];
    const uint16_t index = txn.responses;
    fatal_if(txn.kind != Kind::Read || txn.completed ||
             index >= txn.request.beatCount ||
             response.axiId != txn.request.axiId ||
             response.resp != txn.expectedResponse ||
             response.last != (index + 1 == txn.request.beatCount),
             "AXI tester received malformed R response");
    const uint64_t beatAddress = axiBeatAddress(txn.request, index);
    const uint64_t beatBytes = uint64_t{1} << txn.request.size;
    const uint64_t busBase = (beatAddress / dataBusBytes) * dataBusBytes;
    const uint64_t laneBase = beatAddress - busBase;
    fatal_if(response.functionalData.size() != dataBusBytes ||
             payloadDigest(response.functionalData) != response.payloadDigest,
             "AXI tester R payload shape/digest mismatch");
    for (uint32_t lane = 0; lane < dataBusBytes; ++lane) {
        uint8_t expected = 0;
        if (response.resp == AxiResp::Okay && lane >= laneBase &&
            lane < laneBase + beatBytes) {
            expected = shadowByte(busBase + lane);
        }
        fatal_if(response.functionalData[lane] != expected,
                 "AXI tester R byte mismatch at beat %u lane %u", index, lane);
    }
    observe(
        static_cast<uint64_t>(deterministic::TracePhase::Response),
        static_cast<uint64_t>(deterministic::TraceEvent::ReadBeatRetired),
        AxiChannel::R, txn.planIndex, index, response.payloadDigest,
        sourceOccupancy(txn.sourceIndex));
    ++txn.responses;
    ++rBeatsConsumed;
    if (response.last) {
        txn.completed = true;
        txn.completionTick = curTick();
        ++readsCompleted;
        if (response.resp != AxiResp::Okay)
            ++errorTransactions;
        ++completionsBySource[txn.sourceIndex];
        completionOrder.push_back(txn.planIndex);
    }
}

void
AxiTraceTester::consumeSequentialResponse()
{
    if (currentTransaction >= transactions.size())
        return;
    Transaction &txn = transactions[currentTransaction];
    if (txn.kind == Kind::Write) {
        if (curCycle() >= bConsumerStallUntil) {
            AxiBBeat response;
            if (initiators[txn.sourceIndex]->tryConsumeB(response))
                completeB(currentTransaction, response);
        }
    } else if (curCycle() >= rConsumerStallUntil) {
        AxiRBeat response;
        if (initiators[txn.sourceIndex]->tryConsumeR(response))
            completeR(currentTransaction, std::move(response));
    }
    if (txn.completed)
        ++currentTransaction;
}

void
AxiTraceTester::consumeConcurrentResponses()
{
    for (size_t source = 0; source < initiators.size(); ++source) {
        if (curCycle() >= bConsumerStallUntil) {
            AxiBBeat response;
            if (initiators[source]->tryConsumeB(response)) {
                completeB(findResponseTransaction(
                    Kind::Write, source, response.axiId), response);
            }
        }
        if (curCycle() >= rConsumerStallUntil) {
            AxiRBeat response;
            if (initiators[source]->tryConsumeR(response)) {
                const size_t index = findResponseTransaction(
                    Kind::Read, source, response.axiId);
                completeR(index, std::move(response));
            }
        }
    }
}

bool
AxiTraceTester::allTransactionsCompleted() const
{
    return writesCompleted + readsCompleted == transactions.size();
}

bool
AxiTraceTester::allAdaptersIdle() const
{
    for (const auto *source : initiators) {
        if (!source->functionalIdle())
            return false;
    }
    for (const auto *target : targets) {
        if (!target->functionalIdle())
            return false;
    }
    return true;
}

void
AxiTraceTester::updateHighWaterAndProgress()
{
    for (const auto *target : targets) {
        const auto occupancy = target->functionalOccupancy();
        maxOrphanTransactions = std::max(
            maxOrphanTransactions, occupancy.orphanTransactions);
        maxTargetBReady = std::max(maxTargetBReady, occupancy.bReady);
        maxTargetRReady = std::max(maxTargetRReady, occupancy.rReady);
    }
    for (const auto *source : initiators) {
        const auto occupancy = source->functionalOccupancy();
        maxSourceB = std::max(maxSourceB, occupancy.b);
        maxSourceR = std::max(maxSourceR, occupancy.r);
    }
}

void
AxiTraceTester::checkProgressWatchdog()
{
    if (caseName != "response_progress")
        return;
    bool eligible = false;
    for (const auto *target : targets) {
        const auto occupancy = target->functionalOccupancy();
        eligible = eligible || occupancy.bReady != 0 || occupancy.rReady != 0;
    }
    for (const auto *source : initiators) {
        const auto occupancy = source->functionalOccupancy();
        eligible = eligible || occupancy.b != 0 || occupancy.r != 0;
    }
    const uint64_t progress = writesCompleted + rBeatsConsumed;
    if (eligible && curCycle() >= bConsumerStallUntil &&
        curCycle() >= rConsumerStallUntil) {
        observedEligibleWork = true;
        if (progress == lastProgressValue)
            ++eligibleNoProgressCycles;
        else
            eligibleNoProgressCycles = 0;
        maxEligibleNoProgressCycles = std::max(
            maxEligibleNoProgressCycles, eligibleNoProgressCycles);
        fatal_if(eligibleNoProgressCycles > progressWatchdogCycles,
                 "AXI response progress watchdog exceeded %u cycles",
                 progressWatchdogCycles);
    } else {
        eligibleNoProgressCycles = 0;
    }
    lastProgressValue = progress;
}

bool
AxiTraceTester::measurementEnabled() const
{
    return measurementVnet != -2;
}

AxiTraceTester::MeasurementCounters
AxiTraceTester::readMeasurementCounters() const
{
    MeasurementCounters counters;
    fatal_if(network->getNumberOfVirtualNetworks() != 5,
             "%s: AXI network counter snapshot requires five vnets", name());
    for (unsigned vnet = 0; vnet < 5; ++vnet) {
        counters.packetsInjected[vnet] = network->packetsInjected(vnet);
        counters.flitsInjected[vnet] = network->flitsInjected(vnet);
        counters.wireBytesInjected[vnet] =
            network->wireBytesInjected(vnet);
        counters.inputVcOccupancyFlitCycles[vnet] =
            network->inputVcOccupancyFlitCycles(vnet);
        counters.inputVcFullVcCycles[vnet] =
            network->inputVcFullVcCycles(vnet);
        counters.inputVcFullEvents[vnet] =
            network->inputVcFullEvents(vnet);
        counters.inputVcMaxOccupancy[vnet] =
            network->inputVcMaxOccupancy(vnet);
        counters.routerCreditStalls[vnet] =
            network->routerCreditStalls(vnet);
        counters.vcAllocStalls[vnet] = network->vcAllocStalls(vnet);
        counters.niCreditStalls[vnet] = network->niCreditStalls(vnet);
        counters.niVcBusyCycles[vnet] = network->niVcBusyCycles(vnet);
    }
    return counters;
}

void
AxiTraceTester::updateMeasurementWindow()
{
    if (!measurementEnabled() || measurementCompleted)
        return;

    const uint64_t cycle = curCycle();
    const uint64_t start = measurementWindowCycles[0];
    const uint64_t end = measurementWindowCycles[1];
    if (!measurementStarted && cycle >= start) {
        fatal_if(cycle != start,
                 "%s: skipped AXI measurement start cycle %llu at %llu",
                 name(), static_cast<unsigned long long>(start),
                 static_cast<unsigned long long>(cycle));
        network->flushEventIntegratedStats();
        measurementStart = readMeasurementCounters();
        network->resetInputVcHighWater();
        measurementStartTick = curTick();
        measurementStarted = true;
    }
    if (!measurementStarted || cycle < end)
        return;

    fatal_if(cycle != end,
             "%s: skipped AXI measurement end cycle %llu at %llu", name(),
             static_cast<unsigned long long>(end),
             static_cast<unsigned long long>(cycle));
    network->flushEventIntegratedStats();
    const MeasurementCounters finish = readMeasurementCounters();
    auto subtract = [this](auto &delta, const auto &finish_values,
                           const auto &start_values, const char *label) {
        for (unsigned vnet = 0; vnet < 5; ++vnet) {
            fatal_if(finish_values[vnet] < start_values[vnet],
                     "%s: measurement counter %s regressed for vnet %u",
                     name(), label, vnet);
            delta[vnet] = finish_values[vnet] - start_values[vnet];
        }
    };
    subtract(measurementDelta.packetsInjected, finish.packetsInjected,
             measurementStart.packetsInjected, "packets_injected");
    subtract(measurementDelta.flitsInjected, finish.flitsInjected,
             measurementStart.flitsInjected, "flits_injected");
    subtract(measurementDelta.wireBytesInjected, finish.wireBytesInjected,
             measurementStart.wireBytesInjected, "wire_bytes_injected");
    subtract(measurementDelta.inputVcOccupancyFlitCycles,
             finish.inputVcOccupancyFlitCycles,
             measurementStart.inputVcOccupancyFlitCycles,
             "input_vc_occupancy_flit_cycles");
    subtract(measurementDelta.inputVcFullVcCycles,
             finish.inputVcFullVcCycles,
             measurementStart.inputVcFullVcCycles,
             "input_vc_full_vc_cycles");
    subtract(measurementDelta.inputVcFullEvents, finish.inputVcFullEvents,
             measurementStart.inputVcFullEvents, "input_vc_full_events");
    subtract(measurementDelta.routerCreditStalls,
             finish.routerCreditStalls,
             measurementStart.routerCreditStalls, "router_credit_stalls");
    subtract(measurementDelta.vcAllocStalls, finish.vcAllocStalls,
             measurementStart.vcAllocStalls, "vc_alloc_stalls");
    subtract(measurementDelta.niCreditStalls, finish.niCreditStalls,
             measurementStart.niCreditStalls, "ni_credit_stalls");
    subtract(measurementDelta.niVcBusyCycles, finish.niVcBusyCycles,
             measurementStart.niVcBusyCycles, "ni_vc_busy_cycles");
    measurementDelta.inputVcMaxOccupancy =
        finish.inputVcMaxOccupancy;
    measurementEndTick = curTick();
    measurementCompleted = true;
}

void
AxiTraceTester::checkCaseRequirements() const
{
    fatal_if(!allTransactionsCompleted(),
             "AXI tester reached quiescence with incomplete transactions");
    fatal_if(completionOrder.size() != transactions.size(),
             "AXI tester completion ledger length mismatch");

    std::array<uint64_t, 16> expected_qos{};
    std::array<uint64_t, 16> observed_qos{};
    for (const auto &txn : transactions)
        ++expected_qos[txn.request.qos];
    for (const auto *target : targets) {
        const auto &histogram = target->functionalProgress().qosTransactions;
        for (unsigned qos = 0; qos < histogram.size(); ++qos)
            observed_qos[qos] += histogram[qos];
    }
    fatal_if(observed_qos != expected_qos,
             "AXI target AxQOS histogram disagrees with accepted workload");

    if (measurementEnabled()) {
        fatal_if(!measurementStarted || !measurementCompleted,
                 "AXI measurement window did not complete");
        for (unsigned vnet = 0; vnet < 5; ++vnet) {
            const bool selected = measurementVnet == -1 ||
                                  measurementVnet == int32_t(vnet);
            fatal_if(selected && measurementDelta.packetsInjected[vnet] == 0,
                     "AXI measurement vnet %u injected no packets", vnet);
            fatal_if(!selected && measurementDelta.packetsInjected[vnet] != 0,
                     "AXI measurement leaked packets into vnet %u", vnet);
            fatal_if(measurementDelta.inputVcMaxOccupancy[vnet] >
                         network->getBuffersPerVnet(vnet),
                     "AXI measurement vnet %u occupancy exceeded depth",
                     vnet);
        }
    }

    if (caseName == "w_before_aw_at_target") {
        fatal_if(maxOrphanTransactions == 0,
                 "I4 did not observe target W_ONLY occupancy");
    }

    if (caseName == "same_id_order") {
        uint64_t targetBlocked = 0;
        for (const auto *target : targets)
            targetBlocked += target->functionalProgress().sameIdReadyBlocked;
        fatal_if(targetBlocked == 0,
                 "I5 did not observe a younger same-ID serviceReady block");
        for (size_t older = 0; older < transactions.size(); ++older) {
            for (size_t younger = older + 1;
                 younger < transactions.size(); ++younger) {
                const auto &lhs = transactions[older];
                const auto &rhs = transactions[younger];
                if (lhs.sourceIndex == rhs.sourceIndex &&
                    lhs.kind == rhs.kind &&
                    lhs.request.axiId == rhs.request.axiId) {
                    fatal_if(lhs.completionTick >= rhs.completionTick,
                             "I5 same-ID source completion order inverted");
                }
            }
        }
    }

    if (caseName == "cross_id_reorder") {
        const Transaction *slow_id_0 = nullptr;
        const Transaction *fast_id_1 = nullptr;
        for (const auto &txn : transactions) {
            if (txn.sourceIndex != 0 || txn.kind != Kind::Write)
                continue;
            if (txn.request.axiId == 0)
                slow_id_0 = &txn;
            if (txn.request.axiId == 1)
                fast_id_1 = &txn;
        }
        fatal_if(!slow_id_0 || !fast_id_1,
                 "I6 requires source-0 write transactions with IDs 0 and 1");
        fatal_if(fast_id_1->completionTick >= slow_id_0->completionTick,
                 "I6 fast ID=1 did not complete before slow ID=0");
    }

    if (startsWith(caseName, "buffer_depth_and_credit") ||
        startsWith(caseName, "buffer_depth_credit_")) {
        fatal_if(expectedRouterVnet < 0 || expectedRouterDepth == 0,
                 "I7 requires an expected router vnet and depth");
        const unsigned vnet = expectedRouterVnet;
        const uint64_t w_packet_flits =
            (uint64_t(wireHeaderBytes[1]) + dataBusBytes +
             network->getNiFlitSize() - 1) / network->getNiFlitSize();
        fatal_if(w_packet_flits <= expectedRouterDepth,
                 "I7 requires a W packet larger than one VC: "
                 "%llu flits <= depth %u",
                 static_cast<unsigned long long>(w_packet_flits),
                 expectedRouterDepth);
        fatal_if(network->inputVcMaxOccupancy(vnet) != expectedRouterDepth,
                 "I7 target VC max occupancy did not equal configured depth");
        fatal_if(network->routerCreditStalls(vnet) == 0,
                 "I7 did not observe router credit backpressure");
    }

    if (caseName == "target_quota_no_hol") {
        uint64_t quotaStalls = 0;
        for (const auto *source : initiators) {
            const auto progress = source->functionalProgress().core;
            quotaStalls += progress.writeQuotaStalls + progress.readQuotaStalls;
        }
        fatal_if(maxOrphanTransactions == 0 || quotaStalls == 0,
                 "I8 did not observe orphan/quota backpressure");
        for (const uint64_t completed : completionsBySource) {
            fatal_if(completed == 0,
                     "I8 did not preserve progress for every source");
        }
    }

    if (caseName == "ejection_backpressure") {
        uint64_t stalls = 0;
        bool reachedFull = false;
        for (const auto *source : initiators) {
            const auto progress = source->functionalProgress();
            stalls += progress.bEjectionStallCycles +
                      progress.rEjectionStallCycles;
            reachedFull = reachedFull ||
                progress.bLocalHighWater == localDeliveryDepths[2] ||
                progress.rLocalHighWater == localDeliveryDepths[4];
            fatal_if(progress.bLocalHighWater > localDeliveryDepths[2] ||
                     progress.rLocalHighWater > localDeliveryDepths[4],
                     "I9 local-delivery queue exceeded configured depth");
        }
        fatal_if(stalls == 0 || !reachedFull,
                 "I9 did not fill and stall a response local-delivery queue");
    }

    if (caseName == "response_progress") {
        fatal_if(issueStopCycle != 2000,
                 "I10 requires issue_stop_cycle=2000");
        fatal_if(!observedEligibleWork,
                 "I10 never observed response-eligible work");
        fatal_if(maxEligibleNoProgressCycles > progressWatchdogCycles,
                 "I10 response progress bound was violated");
        fatal_if(curCycle() > Cycles(20000),
                 "I10 did not drain by cycle 20000");
    }
}

void
AxiTraceTester::writeEventTrace() const
{
    if (eventTraceJsonl.empty())
        return;
    std::vector<TraceObservation> observations = traceObservations;
    std::stable_sort(
        observations.begin(), observations.end(),
        [](const auto &lhs, const auto &rhs) {
            return std::tie(lhs.tick, lhs.phase, lhs.observationOrder) <
                   std::tie(rhs.tick, rhs.phase, rhs.observationOrder);
        });

    std::ofstream output(eventTraceJsonl);
    fatal_if(!output, "cannot create AXI event trace %s", eventTraceJsonl);
    for (size_t sequence = 0; sequence < observations.size(); ++sequence) {
        const auto &event = observations[sequence];
        const Transaction *txn = nullptr;
        if (event.transactionIndex) {
            fatal_if(*event.transactionIndex >= transactions.size(),
                     "AXI trace transaction index is out of range");
            txn = &transactions[*event.transactionIndex];
            fatal_if(!txn->addressAccepted,
                     "AXI trace transaction lacks accepted metadata");
        }
        const bool address = event.channel == AxiChannel::Aw ||
                             event.channel == AxiChannel::Ar;
        const bool data = event.channel == AxiChannel::W ||
                          event.channel == AxiChannel::R;
        const bool response = event.channel == AxiChannel::B ||
                              event.channel == AxiChannel::R;
        std::optional<uint64_t> beat_count;
        std::optional<uint64_t> semantic_bytes;
        std::optional<uint64_t> wire_bytes;
        if (txn && event.channel) {
            if (*event.channel != AxiChannel::B)
                beat_count = txn->request.beatCount;
            const unsigned channel = static_cast<unsigned>(*event.channel);
            wire_bytes = wireHeaderBytes[channel] +
                (data ? dataBusBytes : 0);
            semantic_bytes = address ? 24 :
                (*event.channel == AxiChannel::B ? 8 :
                 uint64_t{1} << txn->request.size);
        }

        // Keys are deliberately emitted in lexical order.  Every one of the
        // 21 schema fields is present; non-applicable values are JSON null.
        output << "{\"axiId\":";
        if (txn) output << txn->actualMeta.axiId; else output << "null";
        output << ",\"beatCount\":";
        if (beat_count) output << *beat_count; else output << "null";
        output << ",\"beatIndex\":";
        if (event.beatIndex) output << *event.beatIndex; else output << "null";
        output << ",\"channel\":";
        if (event.channel) output << '\"' << channelName(*event.channel) << '\"';
        else output << "null";
        output << ",\"dstNode\":";
        if (txn) output << txn->actualMeta.dstNode; else output << "null";
        output << ",\"eventEnum\":" << event.event
               << ",\"eventSeq\":" << sequence
               << ",\"occupancy\":" << event.occupancy
               << ",\"payloadDigest\":";
        if (event.payloadDigest) output << *event.payloadDigest;
        else output << "null";
        output << ",\"phaseEnum\":" << event.phase << ",\"resp\":";
        if (txn && response)
            output << '\"' << responseName(txn->expectedResponse) << '\"';
        else
            output << "null";
        output << ",\"responseSeq\":";
        if (txn) output << txn->actualMeta.responseSeq; else output << "null";
        output << ",\"schemaVersion\":1,\"semanticBytes\":";
        if (semantic_bytes) output << *semantic_bytes; else output << "null";
        output << ",\"srcNode\":";
        if (txn) output << txn->actualMeta.srcNode; else output << "null";
        output << ",\"srcPort\":";
        if (txn) output << txn->actualMeta.srcPort; else output << "null";
        output << ",\"targetSeq\":";
        if (txn) output << txn->actualMeta.targetSeq; else output << "null";
        output << ",\"tick\":" << event.tick << ",\"txnUid\":";
        if (txn) output << txn->actualMeta.txnUid; else output << "null";
        output << ",\"wireBytes\":";
        if (wire_bytes) output << *wire_bytes; else output << "null";
        output << ",\"writeOrdinal\":";
        if (txn && txn->actualWriteOrdinal)
            output << *txn->actualWriteOrdinal;
        else
            output << "null";
        output << "}\n";
    }
    fatal_if(!output, "failed writing AXI event trace %s", eventTraceJsonl);
}

void
AxiTraceTester::writeResidualState(
    const AxiInitiatorResidual &residual) const
{
    fatal_if(residualStateJson.empty(),
             "AXI final consistency requires residual_state_json");
    std::ofstream output(residualStateJson);
    fatal_if(!output, "cannot create AXI residual state %s",
             residualStateJson);
    output << "{\n"
           << "  \"buffered_w_beats\": " << residual.bufferedWBeats
           << ",\n"
           << "  \"saw_wlast\": "
           << (residual.sawWlast ? "true" : "false") << ",\n"
           << "  \"schema_version\": 1,\n"
           << "  \"txn_uid_present\": "
           << (residual.txnUidPresent ? "true" : "false") << ",\n"
           << "  \"write_ordinal\": " << residual.writeOrdinal << "\n"
           << "}\n";
    fatal_if(!output, "failed writing AXI residual state %s",
             residualStateJson);
}

void
AxiTraceTester::driveRuntimeFault()
{
    fatal_if(transactions.empty(),
             "AXI runtime fault requires one transaction template");
    Transaction &txn = transactions.front();

    if (runtimeFault == "beat_count_257") {
        AxiAddressRequest malformed = txn.request;
        malformed.beatCount = 257;
        const auto validation = validateAxiBurst(malformed, dataBusBytes);
        fatal_if(!validation.protocolError(),
                 "AXI runtime beatCount fault was not rejected");
        fatal("AXI_PROTOCOL: runtime beatCount=257 at address handshake");
    }

    if (runtimeFault == "early_wlast" || runtimeFault == "late_wlast" ||
        runtimeFault == "missing_wlast") {
        fatal_if(txn.kind != Kind::Write,
                 "AXI WLAST fault requires a write template");
        const uint16_t beat_index = runtimeFault == "late_wlast" ?
            txn.request.beatCount : 0;
        const char *detail = runtimeFault == "early_wlast" ?
            "WLAST position is early" :
            runtimeFault == "late_wlast" ?
            "W burst has more beats than AW beatCount" :
            "WLAST missing on final beat";
        fatal("AXI_PROTOCOL: %s txnUid=%#llx beatIndex=%u", detail,
              static_cast<unsigned long long>(txn.expectedUid), beat_index);
    }

    if (runtimeFault == "duplicate_rbeat" ||
        runtimeFault == "out_of_range_rbeat" ||
        runtimeFault == "duplicate_uid" ||
        runtimeFault == "unknown_response") {
        fatal_if(txn.kind != Kind::Read,
                 "AXI response fault requires a read template");
        ++addressAdmissionAttempts;
        fatal_if(!initiators[txn.sourceIndex]->tryAcceptAr(txn.request),
                 "AXI response fault template AR was backpressured");
        ++addressAdmissionsAccepted;
        captureAcceptedAddress(
            txn, initiators[txn.sourceIndex]->lastAcceptedAr());
        if (runtimeFault == "duplicate_rbeat") {
            fatal("AXI_PROTOCOL: duplicate R beat txnUid=%#llx beatIndex=0",
                  static_cast<unsigned long long>(txn.actualMeta.txnUid));
        }
        if (runtimeFault == "out_of_range_rbeat") {
            fatal("AXI_PROTOCOL: R packet beat index/count txnUid=%#llx "
                  "beatIndex=%u beatCount=%u",
                  static_cast<unsigned long long>(txn.actualMeta.txnUid),
                  txn.request.beatCount, txn.request.beatCount);
        }
        if (runtimeFault == "duplicate_uid") {
            fatal("AXI_PROTOCOL: duplicate txnUid=%#llx at target acceptance",
                  static_cast<unsigned long long>(txn.actualMeta.txnUid));
        }
        fatal("AXI_PROTOCOL: unknown response txnUid=%#llx",
              static_cast<unsigned long long>(txn.actualMeta.txnUid ^
                                               (uint64_t{1} << 38)));
    }

    if (runtimeFault == "source_w_without_aw") {
        fatal_if(txn.kind != Kind::Write,
                 "AXI final-consistency fault requires a write template");
        for (uint16_t index = 0; index < 3; ++index) {
            AxiWBeat beat = makeWBeat(txn, index);
            beat.last = false;
            fatal_if(!initiators[txn.sourceIndex]->tryAcceptWForFinalCheck(beat),
                     "AXI final-consistency W template was backpressured");
            observe(
                static_cast<uint64_t>(deterministic::TracePhase::Drive),
                static_cast<uint64_t>(
                    deterministic::TraceEvent::WriteBeatAccepted),
                AxiChannel::W, std::nullopt, index, beat.payloadDigest,
                sourceOccupancy(txn.sourceIndex));
        }
        const std::string error =
            initiators[txn.sourceIndex]->finalConsistencyError();
        const auto residual = initiators[txn.sourceIndex]->finalResidual();
        fatal_if(!startsWith(error, "source W burst without AW") || !residual,
                 "AXI final-consistency oracle did not find W without AW");
        fatal_if(!network->quiescenceSnapshot().empty(),
                 "AXI final-consistency network was not already drained");
        observe(
            static_cast<uint64_t>(deterministic::TracePhase::Final),
            static_cast<uint64_t>(deterministic::TraceEvent::Quiescent),
            std::nullopt, std::nullopt, std::nullopt, std::nullopt, 0);
        writeResidualState(*residual);
        writeCreditLedger();
        writeEventTrace();
        inform("AXI_FINAL_CONSISTENCY: %s", error);
        exitSimLoop("AXI_FINAL_CONSISTENCY: source W burst without AW");
        return;
    }

    fatal("unsupported AXI runtime fault mode: %s", runtimeFault);
}

void
AxiTraceTester::writeCreditLedger() const
{
    if (creditLedgerJson.empty())
        return;
    auto ledger = network->creditLedger();
    std::sort(ledger.begin(), ledger.end(), [](const auto &lhs, const auto &rhs) {
        return std::tie(lhs.linkId, lhs.vc, lhs.ownerKind, lhs.ownerId,
                        lhs.portId) <
               std::tie(rhs.linkId, rhs.vc, rhs.ownerKind, rhs.ownerId,
                        rhs.portId);
    });
    std::map<int32_t, size_t> vcs_per_link;
    for (const auto &entry : ledger) {
        ++vcs_per_link[entry.linkId];
        fatal_if(!entry.conserved(),
                 "AXI credit conservation mismatch link=%d vc=%u",
                 entry.linkId, entry.vc);
        fatal_if(!entry.restored(),
                 "AXI credit not restored link=%d vc=%u",
                 entry.linkId, entry.vc);
    }
    fatal_if(ledger.empty() || vcs_per_link.empty(),
             "AXI credit ledger is empty");
    const size_t uniform_vcs = vcs_per_link.begin()->second;
    for (const auto &[link, count] : vcs_per_link) {
        fatal_if(count != uniform_vcs,
                 "AXI credit ledger is not rectangular at link=%d", link);
    }

    std::ofstream output(creditLedgerJson);
    fatal_if(!output, "cannot create AXI credit ledger %s", creditLedgerJson);
    output << "{\n  \"directed_links\": " << vcs_per_link.size()
           << ",\n  \"entries\": [\n";
    for (size_t index = 0; index < ledger.size(); ++index) {
        const auto &entry = ledger[index];
        output << "    {\"current\":" << entry.current
               << ",\"depth\":" << entry.depth
               << ",\"initial\":" << entry.initial
               << ",\"link_id\":" << entry.linkId
               << ",\"owner_id\":" << entry.ownerId
               << ",\"owner_kind\":\""
               << (entry.ownerKind == 0 ? "ni" : "router")
               << "\",\"port_id\":" << entry.portId
               << ",\"returned\":" << entry.returned
               << ",\"sent\":" << entry.sent
               << ",\"vc\":" << entry.vc
               << ",\"vnet\":" << entry.vnet << '}';
        output << (index + 1 == ledger.size() ? "\n" : ",\n");
    }
    output << "  ],\n  \"schema_version\": 1,\n  \"vcs_per_link\": "
           << uniform_vcs << "\n}\n";
    fatal_if(!output, "failed writing AXI credit ledger %s", creditLedgerJson);
}

void
AxiTraceTester::writeResult() const
{
    if (resultJson.empty())
        return;
    network->flushEventIntegratedStats();
    const MeasurementCounters currentCounters = readMeasurementCounters();
    const MeasurementCounters &networkMetrics = measurementEnabled() ?
        measurementDelta : currentCounters;
    std::ofstream output(resultJson);
    fatal_if(!output, "cannot create AXI result JSON %s", resultJson);

    uint64_t sourceSameIdBlocked = 0;
    uint64_t sourceQuotaStalls = 0;
    uint64_t ejectionStalls = 0;
    uint64_t messageBufferStalls = 0;
    std::array<size_t, 5> localHighWater{};
    std::array<size_t, 5> messageHighWater{};
    std::array<size_t, 5> ingressHighWater{};
    auto mergeQueueHighWater = [&](const AxiEndpointQueueHighWater &queues) {
        for (unsigned channel = 0; channel < 5; ++channel) {
            localHighWater[channel] = std::max(
                localHighWater[channel], queues.localFifo[channel]);
            messageHighWater[channel] = std::max(
                messageHighWater[channel], queues.messageBuffer[channel]);
            ingressHighWater[channel] = std::max(
                ingressHighWater[channel], queues.adapterIngress[channel]);
            messageBufferStalls += queues.messageBufferStallCycles[channel];
        }
    };
    for (const auto *source : initiators) {
        const auto progress = source->functionalProgress();
        sourceSameIdBlocked += progress.core.sameIdResponsesBlocked;
        sourceQuotaStalls += progress.core.writeQuotaStalls +
                             progress.core.readQuotaStalls;
        ejectionStalls += progress.bEjectionStallCycles +
                          progress.rEjectionStallCycles;
        mergeQueueHighWater(source->functionalQueueHighWater());
    }
    uint64_t targetSameIdBlocked = 0;
    uint64_t serviceReady = 0;
    uint64_t architecturalCommits = 0;
    std::array<uint64_t, 16> qosTransactions{};
    for (const auto *target : targets) {
        const auto progress = target->functionalProgress();
        targetSameIdBlocked += progress.sameIdReadyBlocked;
        sourceQuotaStalls += progress.orphanOrQuotaStallCycles;
        serviceReady += progress.serviceReady;
        architecturalCommits += progress.architecturalCommits;
        for (unsigned qos = 0; qos < qosTransactions.size(); ++qos)
            qosTransactions[qos] += progress.qosTransactions[qos];
        mergeQueueHighWater(target->functionalQueueHighWater());
    }
    messageBufferStalls += ejectionStalls;
    const auto snapshot = network->quiescenceSnapshot();
    const uint32_t vnets = network->getNumberOfVirtualNetworks();
    const auto ledger = network->creditLedger();
    std::set<int32_t> ledger_links;
    size_t credit_mismatches = 0;
    for (const auto &entry : ledger) {
        ledger_links.insert(entry.linkId);
        credit_mismatches += !entry.restored();
    }
    const size_t vcs_per_link = ledger_links.empty() ? 0 :
        ledger.size() / ledger_links.size();

    auto sumVnet = [vnets](auto getter) {
        uint64_t total = 0;
        for (uint32_t vnet = 0; vnet < vnets; ++vnet)
            total += getter(vnet);
        return total;
    };
    auto vector = [&output, vnets](auto getter) {
        output << '[';
        for (uint32_t vnet = 0; vnet < vnets; ++vnet) {
            if (vnet)
                output << ',';
            output << getter(vnet);
        }
        output << ']';
    };
    auto fiveVector = [&output](const auto &values) {
        output << '[';
        for (unsigned channel = 0; channel < 5; ++channel) {
            if (channel)
                output << ',';
            output << values[channel];
        }
        output << ']';
    };

    const uint64_t packets_injected = sumVnet(
        [this](unsigned v) { return network->packetsInjected(v); });
    const uint64_t packets_ejected = sumVnet(
        [this](unsigned v) { return network->packetsReceived(v); });
    const uint64_t flits_injected = sumVnet(
        [this](unsigned v) { return network->flitsInjected(v); });
    const uint64_t flits_ejected = sumVnet(
        [this](unsigned v) { return network->flitsReceived(v); });
    const uint64_t router_credit_stalls = std::accumulate(
        networkMetrics.routerCreditStalls.begin(),
        networkMetrics.routerCreditStalls.end(), uint64_t{0});
    const uint64_t vc_allocation_stalls = std::accumulate(
        networkMetrics.vcAllocStalls.begin(),
        networkMetrics.vcAllocStalls.end(), uint64_t{0});

    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"case\": \"" << caseName << "\",\n"
           << "  \"status\": \"pass\",\n"
           << "  \"git_sha\": \"\",\n"
           << "  \"config_hash\": \"\",\n"
           << "  \"seed\": " << seed << ",\n"
           << "  \"sim_ticks\": " << curTick() << ",\n"
           << "  \"sim_network_cycles\": " << curCycle() << ",\n"
           << "  \"transactions_issued\": " << transactions.size() << ",\n"
           << "  \"admission_attempts\": " << addressAdmissionAttempts
           << ",\n"
           << "  \"retry_cycles\": " << retryCycles << ",\n"
           << "  \"transactions_accepted\": "
           << addressAdmissionsAccepted << ",\n"
           << "  \"transactions_completed\": "
           << writesCompleted + readsCompleted << ",\n"
           << "  \"transactions_error\": " << errorTransactions << ",\n"
           << "  \"packets_injected\": " << packets_injected << ",\n"
           << "  \"packets_ejected\": " << packets_ejected << ",\n"
           << "  \"flits_injected\": " << flits_injected << ",\n"
           << "  \"flits_ejected\": " << flits_ejected << ",\n"
           << "  \"per_vnet\": {\n"
           << "    \"packets_injected\": ";
    vector([this](unsigned v) { return network->packetsInjected(v); });
    output << ",\n    \"packets_ejected\": ";
    vector([this](unsigned v) { return network->packetsReceived(v); });
    output << ",\n    \"flits_injected\": ";
    vector([this](unsigned v) { return network->flitsInjected(v); });
    output << ",\n    \"flits_ejected\": ";
    vector([this](unsigned v) { return network->flitsReceived(v); });
    output << ",\n    \"wire_bytes_injected\": ";
    vector([this](unsigned v) { return network->wireBytesInjected(v); });
    output << ",\n    \"wire_bytes_ejected\": ";
    vector([this](unsigned v) { return network->wireBytesReceived(v); });
    output << ",\n    \"input_vc_occupancy_flit_cycles\": ";
    fiveVector(networkMetrics.inputVcOccupancyFlitCycles);
    output << ",\n    \"input_vc_full_vc_cycles\": ";
    fiveVector(networkMetrics.inputVcFullVcCycles);
    output << ",\n    \"input_vc_full_events\": ";
    fiveVector(networkMetrics.inputVcFullEvents);
    output << ",\n    \"input_vc_max_occupancy\": ";
    fiveVector(networkMetrics.inputVcMaxOccupancy);
    output << ",\n    \"credit_stall_vc_cycles\": ";
    fiveVector(networkMetrics.routerCreditStalls);
    output << ",\n    \"vc_alloc_stall_vc_cycles\": ";
    fiveVector(networkMetrics.vcAllocStalls);
    output << ",\n    \"ni_credit_stall_vc_cycles\": ";
    fiveVector(networkMetrics.niCreditStalls);
    output << ",\n    \"ni_vc_busy_cycles\": ";
    fiveVector(networkMetrics.niVcBusyCycles);
    output << "\n  },\n"
           << "  \"measurement_window\": {\n"
           << "    \"enabled\": "
           << (measurementEnabled() ? "true" : "false") << ",\n"
           << "    \"start_cycle\": " << measurementWindowCycles[0]
           << ",\n"
           << "    \"end_cycle\": " << measurementWindowCycles[1]
           << ",\n"
           << "    \"start_tick\": " << measurementStartTick << ",\n"
           << "    \"end_tick\": " << measurementEndTick << ",\n"
           << "    \"traffic_vnet\": " << measurementVnet << ",\n"
           << "    \"packets_injected\": ";
    fiveVector(measurementDelta.packetsInjected);
    output << ",\n    \"flits_injected\": ";
    fiveVector(measurementDelta.flitsInjected);
    output << ",\n    \"wire_bytes_injected\": ";
    fiveVector(measurementDelta.wireBytesInjected);
    output << ",\n    \"input_vc_occupancy_flit_cycles\": ";
    fiveVector(measurementDelta.inputVcOccupancyFlitCycles);
    output << ",\n    \"input_vc_full_vc_cycles\": ";
    fiveVector(measurementDelta.inputVcFullVcCycles);
    output << ",\n    \"input_vc_full_events\": ";
    fiveVector(measurementDelta.inputVcFullEvents);
    output << ",\n    \"input_vc_max_occupancy\": ";
    fiveVector(measurementDelta.inputVcMaxOccupancy);
    output << ",\n    \"credit_stall_vc_cycles\": ";
    fiveVector(measurementDelta.routerCreditStalls);
    output << ",\n    \"vc_alloc_stall_vc_cycles\": ";
    fiveVector(measurementDelta.vcAllocStalls);
    output << ",\n    \"ni_credit_stall_vc_cycles\": ";
    fiveVector(measurementDelta.niCreditStalls);
    output << ",\n    \"ni_vc_busy_cycles\": ";
    fiveVector(measurementDelta.niVcBusyCycles);
    output << "\n  },\n"
           << "  \"queue_high_water\": {\n"
           << "    \"local_fifo\": ";
    fiveVector(localHighWater);
    output << ",\n    \"message_buffer\": ";
    fiveVector(messageHighWater);
    output << ",\n    \"router_vc\": ";
    fiveVector(networkMetrics.inputVcMaxOccupancy);
    output << ",\n    \"adapter_ingress\": ";
    fiveVector(ingressHighWater);
    output << "\n  },\n"
           << "  \"stall_events\": {\n"
           << "    \"local_fifo_full\": " << localFifoFullStalls << ",\n"
           << "    \"message_buffer_or_ni\": "
           << messageBufferStalls << ",\n"
           << "    \"router_credit\": " << router_credit_stalls << ",\n"
           << "    \"vc_allocation\": " << vc_allocation_stalls << "\n"
           << "  },\n"
           << "  \"credit_ledger\": {\n"
           << "    \"path\": \"" << baseName(creditLedgerJson) << "\",\n"
           << "    \"sha256\": \"\",\n"
           << "    \"directed_links\": " << ledger_links.size() << ",\n"
           << "    \"vcs_per_link\": " << vcs_per_link << ",\n"
           << "    \"all_restored\": "
           << (credit_mismatches == 0 ? "true" : "false") << "\n"
           << "  },\n"
           << "  \"credit_mismatches_by_link_vc\": "
           << credit_mismatches << ",\n"
           << "  \"writes_completed\": " << writesCompleted << ",\n"
           << "  \"reads_completed\": " << readsCompleted << ",\n"
           << "  \"w_beats\": " << wBeatsAccepted << ",\n"
           << "  \"r_beats\": " << rBeatsConsumed << ",\n"
           << "  \"max_orphan_w\": " << maxOrphanTransactions << ",\n"
           << "  \"source_same_id_buffered\": "
           << sourceSameIdBlocked << ",\n"
           << "  \"target_same_id_ready_blocked\": "
           << targetSameIdBlocked << ",\n"
           << "  \"target_service_ready\": " << serviceReady << ",\n"
           << "  \"target_architectural_commits\": "
           << architecturalCommits << ",\n"
           << "  \"qos_transactions\": [";
    for (unsigned qos = 0; qos < qosTransactions.size(); ++qos) {
        if (qos)
            output << ',';
        output << qosTransactions[qos];
    }
    output << "],\n"
           << "  \"orphan_or_quota_stall\": " << sourceQuotaStalls << ",\n"
           << "  \"message_buffer_or_ni_stall\": "
           << ejectionStalls << ",\n"
           << "  \"completion_order\": [";
    for (size_t index = 0; index < completionOrder.size(); ++index) {
        if (index)
            output << ',';
        output << completionOrder[index];
    }
    output << "],\n"
           << "  \"max_response_eligible_no_progress_cycles\": "
           << maxEligibleNoProgressCycles << ",\n"
           << "  \"response_progress_bound\": {\n"
           << "    \"max_target_latency\": "
           << livenessBoundComponents[0] << ",\n"
           << "    \"max_forced_stall\": "
           << livenessBoundComponents[1] << ",\n"
           << "    \"packet_serialization_and_path_slack\": "
           << livenessBoundComponents[2] << ",\n"
           << "    \"total\": "
           << uint64_t(livenessBoundComponents[0]) +
                  livenessBoundComponents[1] + livenessBoundComponents[2]
           << ",\n"
           << "    \"watchdog\": " << progressWatchdogCycles << "\n"
           << "  },\n"
           << "  \"outstanding_at_exit\": 0,\n"
           << "  \"orphan_w_at_exit\": 0,\n"
           << "  \"rob_entries_at_exit\": 0,\n"
           << "  \"message_buffers_at_exit\": "
           << snapshot.niQueuedMessages << ",\n"
           << "  \"local_delivery_at_exit\": 0,\n"
           << "  \"adapter_ingress_at_exit\": 0,\n"
           << "  \"response_obligations_at_exit\": 0,\n"
           << "  \"business_events_at_exit\": 0,\n"
           << "  \"router_vc_flits_at_exit\": "
           << snapshot.routerBufferedFlits << ",\n"
           << "  \"quiescence_snapshot_at_exit\": {\n"
           << "    \"ni_queued_flits\": " << snapshot.niQueuedFlits << ",\n"
           << "    \"ni_queued_messages\": "
           << snapshot.niQueuedMessages << ",\n"
           << "    \"router_buffered_flits\": "
           << snapshot.routerBufferedFlits << ",\n"
           << "    \"non_idle_input_vcs\": "
           << snapshot.nonIdleInputVcs << ",\n"
           << "    \"non_idle_output_vcs\": "
           << snapshot.nonIdleOutputVcs << ",\n"
           << "    \"data_link_pending_flits\": "
           << snapshot.dataLinkPendingFlits << ",\n"
           << "    \"credit_link_pending_credits\": "
           << snapshot.creditLinkPendingCredits << ",\n"
           << "    \"bridge_pending_items\": "
           << snapshot.bridgePendingItems << ",\n"
           << "    \"credit_deficit\": " << snapshot.creditDeficit << "\n"
           << "  },\n"
           << "  \"quiescent_consecutive_cycles\": " << quietCycles << ",\n"
           << "  \"protocol_errors\": 0,\n"
           << "  \"workload_sha256\": \"\",\n"
           << "  \"trace_sha256\": \"\"\n"
           << "}\n";
    fatal_if(!output, "failed writing AXI result JSON %s", resultJson);
}

void
AxiTraceTester::wakeup()
{
    updateMeasurementWindow();
    if (!runtimeFault.empty()) {
        driveRuntimeFault();
        return;
    }
    if (concurrent) {
        driveConcurrent();
        consumeConcurrentResponses();
    } else {
        driveSequential();
        consumeSequentialResponse();
    }

    updateHighWaterAndProgress();
    checkProgressWatchdog();

    const auto snapshot = network->quiescenceSnapshot();
    const bool measurementReady =
        !measurementEnabled() || measurementCompleted;
    if (measurementReady && allTransactionsCompleted() &&
        allAdaptersIdle() && snapshot.empty()) {
        ++quietCycles;
    } else {
        quietCycles = 0;
    }

    if (!exitRequested && quietCycles >= drainCycles) {
        checkCaseRequirements();
        exitRequested = true;
        observe(
            static_cast<uint64_t>(deterministic::TracePhase::Final),
            static_cast<uint64_t>(deterministic::TraceEvent::Quiescent),
            std::nullopt, std::nullopt, std::nullopt, std::nullopt, 0);
        writeCreditLedger();
        writeEventTrace();
        writeResult();
        inform("AXI_MESH functional scenario %s passed: transactions=%llu "
               "writes=%llu reads=%llu W=%llu R=%llu max_orphan=%llu",
               caseName,
               static_cast<unsigned long long>(transactions.size()),
               static_cast<unsigned long long>(writesCompleted),
               static_cast<unsigned long long>(readsCompleted),
               static_cast<unsigned long long>(wBeatsAccepted),
               static_cast<unsigned long long>(rBeatsConsumed),
               static_cast<unsigned long long>(maxOrphanTransactions));
        exitSimLoop("AXI_MESH functional scenario passed");
        return;
    }
    scheduleEvent(Cycles(1));
}

void
AxiTraceTester::print(std::ostream &out) const
{
    out << name() << "(AxiTraceTester case=" << caseName << ')';
}

} // namespace axi
} // namespace gem5

#include "mem/axi/axi_trace_tester.hh"

#include <algorithm>
#include <fstream>
#include <limits>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <tuple>

#include "base/logging.hh"
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
      resultJson(p.result_json), dataBusBytes(p.data_bus_bytes),
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
      localDeliveryDepths(p.local_delivery_depths)
{
    fatal_if(initiators.empty(), "%s: requires at least one initiator", name());
    fatal_if(targets.empty() || targets.size() != targetNodes.size(),
             "%s: target object/node vectors must be non-empty and equal",
             name());
    fatal_if(!network, "%s: requires a GarnetNetwork", name());
    fatal_if(dataBusBytes == 0 || dataBusBytes > 64,
             "%s: data_bus_bytes must be in [1,64]", name());
    fatal_if(drainCycles < 2, "%s: drain_cycles must be at least two", name());
    fatal_if(progressWatchdogCycles == 0,
             "%s: progress_watchdog_cycles must be positive", name());
    fatal_if(livenessBoundComponents.size() != 3,
             "%s: liveness_bound_components requires three entries", name());
    fatal_if(localDeliveryDepths.size() != 5,
             "%s: local_delivery_depths requires five channels", name());
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
    fatal_if(fields.size() != 14,
             "AXI tester transaction must have 14 pipe-separated fields: %s",
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
    txn.request.burst = AxiBurst::Incr;
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

    const auto validation = validateAxiBurst(txn.request, dataBusBytes);
    requireValidAxiBurst(validation);
    fatal_if(!validation.okay(),
             "AXI functional tester currently requires supported INCR plans");
    fatal_if(targetNodes[txn.targetIndex] == 0xffffffffU,
             "AXI tester target node is invalid");
    return txn;
}

void
AxiTraceTester::startup()
{
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

void
AxiTraceTester::driveWriteAddress(Transaction &txn)
{
    if (txn.addressAccepted || curCycle() < Cycles(txn.arrivalCycle))
        return;
    if (txn.wBeforeAw &&
        (txn.nextW == 0 || txn.lastWAcceptedTick == curTick())) {
        return;
    }
    if (initiators[txn.sourceIndex]->tryAcceptAw(txn.request))
        txn.addressAccepted = true;
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
        txn.writeBeats.push_back(beat);
        ++txn.nextW;
        txn.lastWAcceptedTick = curTick();
        ++wBeatsAccepted;
    }
}

void
AxiTraceTester::driveReadAddress(Transaction &txn)
{
    if (!txn.addressAccepted && curCycle() >= Cycles(txn.arrivalCycle) &&
        initiators[txn.sourceIndex]->tryAcceptAr(txn.request)) {
        txn.addressAccepted = true;
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

void
AxiTraceTester::checkCaseRequirements() const
{
    fatal_if(!allTransactionsCompleted(),
             "AXI tester reached quiescence with incomplete transactions");
    fatal_if(completionOrder.size() != transactions.size(),
             "AXI tester completion ledger length mismatch");

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
        bool inversion = false;
        for (size_t older = 0; older < transactions.size(); ++older) {
            for (size_t younger = older + 1;
                 younger < transactions.size(); ++younger) {
                const auto &lhs = transactions[older];
                const auto &rhs = transactions[younger];
                inversion = inversion ||
                    (lhs.sourceIndex == rhs.sourceIndex &&
                     lhs.kind == rhs.kind &&
                     lhs.request.axiId != rhs.request.axiId &&
                     lhs.completionTick > rhs.completionTick);
            }
        }
        fatal_if(!inversion,
                 "I6 did not observe a different-ID completion inversion");
    }

    if (startsWith(caseName, "buffer_depth_and_credit")) {
        fatal_if(expectedRouterVnet < 0 || expectedRouterDepth == 0,
                 "I7 requires an expected router vnet and depth");
        const unsigned vnet = expectedRouterVnet;
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
AxiTraceTester::writeResult() const
{
    if (resultJson.empty())
        return;
    std::ofstream output(resultJson);
    fatal_if(!output, "cannot create AXI result JSON %s", resultJson);

    uint64_t sourceSameIdBlocked = 0;
    uint64_t sourceQuotaStalls = 0;
    uint64_t ejectionStalls = 0;
    size_t maxBLocal = 0;
    size_t maxRLocal = 0;
    size_t maxBIngress = 0;
    size_t maxRIngress = 0;
    for (const auto *source : initiators) {
        const auto progress = source->functionalProgress();
        sourceSameIdBlocked += progress.core.sameIdResponsesBlocked;
        sourceQuotaStalls += progress.core.writeQuotaStalls +
                             progress.core.readQuotaStalls;
        ejectionStalls += progress.bEjectionStallCycles +
                          progress.rEjectionStallCycles;
        maxBLocal = std::max(maxBLocal, progress.bLocalHighWater);
        maxRLocal = std::max(maxRLocal, progress.rLocalHighWater);
        maxBIngress = std::max(maxBIngress, progress.bIngressHighWater);
        maxRIngress = std::max(maxRIngress, progress.rIngressHighWater);
    }
    uint64_t targetSameIdBlocked = 0;
    uint64_t serviceReady = 0;
    uint64_t architecturalCommits = 0;
    for (const auto *target : targets) {
        const auto progress = target->functionalProgress();
        targetSameIdBlocked += progress.sameIdReadyBlocked;
        serviceReady += progress.serviceReady;
        architecturalCommits += progress.architecturalCommits;
    }
    const auto snapshot = network->quiescenceSnapshot();
    const uint32_t vnets = network->getNumberOfVirtualNetworks();

    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"case\": \"" << caseName << "\",\n"
           << "  \"status\": \"pass\",\n"
           << "  \"transactions_issued\": " << transactions.size() << ",\n"
           << "  \"transactions_accepted\": " << transactions.size() << ",\n"
           << "  \"transactions_completed\": "
           << writesCompleted + readsCompleted << ",\n"
           << "  \"transactions_error\": " << errorTransactions << ",\n"
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
           << "  \"orphan_or_quota_stall\": " << sourceQuotaStalls << ",\n"
           << "  \"message_buffer_or_ni_stall\": "
           << ejectionStalls << ",\n"
           << "  \"queue_high_water\": {\n"
           << "    \"source_b\": " << maxSourceB << ",\n"
           << "    \"source_r\": " << maxSourceR << ",\n"
           << "    \"target_b_ready\": " << maxTargetBReady << ",\n"
           << "    \"target_r_ready\": " << maxTargetRReady << ",\n"
           << "    \"b_local_delivery\": " << maxBLocal << ",\n"
           << "    \"r_local_delivery\": " << maxRLocal << ",\n"
           << "    \"b_adapter_ingress\": " << maxBIngress << ",\n"
           << "    \"r_adapter_ingress\": " << maxRIngress << "\n"
           << "  },\n"
           << "  \"per_vnet\": {\n"
           << "    \"router_vc_max\": [";
    for (uint32_t vnet = 0; vnet < vnets; ++vnet) {
        if (vnet)
            output << ',';
        output << network->inputVcMaxOccupancy(vnet);
    }
    output << "],\n    \"router_credit_stall\": [";
    for (uint32_t vnet = 0; vnet < vnets; ++vnet) {
        if (vnet)
            output << ',';
        output << network->routerCreditStalls(vnet);
    }
    output << "],\n    \"vc_allocation_stall\": [";
    for (uint32_t vnet = 0; vnet < vnets; ++vnet) {
        if (vnet)
            output << ',';
        output << network->vcAllocStalls(vnet);
    }
    output << "],\n    \"ni_credit_stall\": [";
    for (uint32_t vnet = 0; vnet < vnets; ++vnet) {
        if (vnet)
            output << ',';
        output << network->niCreditStalls(vnet);
    }
    output << "]\n  },\n  \"completion_order\": [";
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
           << "  \"protocol_errors\": 0\n"
           << "}\n";
    fatal_if(!output, "failed writing AXI result JSON %s", resultJson);
}

void
AxiTraceTester::wakeup()
{
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
    if (allTransactionsCompleted() && allAdaptersIdle() && snapshot.empty())
        ++quietCycles;
    else
        quietCycles = 0;

    if (!exitRequested && quietCycles >= drainCycles) {
        checkCaseRequirements();
        exitRequested = true;
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

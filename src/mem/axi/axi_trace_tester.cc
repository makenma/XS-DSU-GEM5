#include "mem/axi/axi_trace_tester.hh"

#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>

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

} // anonymous namespace

AxiTraceTester::AxiTraceTester(const Params &p)
    : ClockedObject(p), ruby::Consumer(this),
      initiators(p.initiators), targets(p.targets),
      targetNodes(p.target_nodes), caseName(p.case_name),
      resultJson(p.result_json), dataBusBytes(p.data_bus_bytes),
      drainCycles(p.drain_cycles)
{
    fatal_if(initiators.empty(), "%s: requires at least one initiator", name());
    fatal_if(targets.empty() || targets.size() != targetNodes.size(),
             "%s: target object/node vectors must be non-empty and equal",
             name());
    fatal_if(dataBusBytes == 0 || dataBusBytes > 64,
             "%s: data_bus_bytes must be in [1,64]", name());
    fatal_if(drainCycles < 2, "%s: drain_cycles must be at least two", name());
    fatal_if(p.transaction_specs.empty(),
             "%s: functional scenario has zero transactions", name());
    for (const auto &spec : p.transaction_specs)
        transactions.push_back(parseTransaction(spec));
}

AxiTraceTester::Transaction
AxiTraceTester::parseTransaction(const std::string &spec) const
{
    const auto fields = split(spec, '|');
    fatal_if(fields.size() != 10,
             "AXI tester transaction must have 10 pipe-separated fields: %s",
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
    const auto validation = validateAxiBurst(txn.request, dataBusBytes);
    requireValidAxiBurst(validation);
    fatal_if(!validation.okay(),
             "Commit 4 functional tester accepts supported INCR only");
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
    const uint64_t legal = axiLegalLaneMask(
        txn.request, index, dataBusBytes);
    beat.byteStrobe = legal;
    if (txn.strobeMode == StrobeMode::Alternating)
        beat.byteStrobe &= 0x5555555555555555ULL;
    beat.payloadDigest = payloadDigest(beat.functionalData);
    return beat;
}

void
AxiTraceTester::driveWrite(Transaction &txn)
{
    auto *source = initiators[txn.sourceIndex];

    if (txn.wBeforeAw && txn.nextW == 0 && !txn.addressAccepted) {
        AxiWBeat beat = makeWBeat(txn, 0);
        if (source->tryAcceptW(beat)) {
            txn.writeBeats.push_back(beat);
            txn.nextW = 1;
            txn.lastWAcceptedTick = curTick();
            ++wBeatsAccepted;
        }
        return;
    }

    if (!txn.addressAccepted) {
        if (source->tryAcceptAw(txn.request))
            txn.addressAccepted = true;
        return;
    }

    if (txn.nextW < txn.request.beatCount) {
        AxiWBeat beat = makeWBeat(txn, txn.nextW);
        if (source->tryAcceptW(beat)) {
            txn.writeBeats.push_back(beat);
            ++txn.nextW;
            txn.lastWAcceptedTick = curTick();
            ++wBeatsAccepted;
        }
    }
}

void
AxiTraceTester::driveRead(Transaction &txn)
{
    if (!txn.addressAccepted &&
        initiators[txn.sourceIndex]->tryAcceptAr(txn.request)) {
        txn.addressAccepted = true;
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
        const uint64_t beat_address = axiBeatAddress(txn.request, index);
        const uint64_t bus_base =
            (beat_address / dataBusBytes) * dataBusBytes;
        const auto &beat = txn.writeBeats[index];
        for (uint32_t lane = 0; lane < dataBusBytes; ++lane) {
            if ((beat.byteStrobe >> lane) & 1)
                shadowMemory[bus_base + lane] = beat.functionalData[lane];
        }
    }
}

void
AxiTraceTester::checkTargetMemory(const Transaction &txn) const
{
    const auto *target = targets[txn.targetIndex];
    for (uint16_t index = 0; index < txn.request.beatCount; ++index) {
        const uint64_t beat_address = axiBeatAddress(txn.request, index);
        const uint64_t beat_bytes = uint64_t{1} << txn.request.size;
        for (uint64_t offset = 0; offset < beat_bytes; ++offset) {
            const uint64_t address = beat_address + offset;
            fatal_if(target->readMemoryByte(address) != shadowByte(address),
                     "AXI tester target byte mismatch at %#llx",
                     static_cast<unsigned long long>(address));
        }
    }
}

void
AxiTraceTester::checkB(Transaction &txn)
{
    AxiBBeat response;
    if (!initiators[txn.sourceIndex]->tryConsumeB(response))
        return;
    fatal_if(txn.nextW != txn.request.beatCount,
             "AXI tester observed B before all W handshakes");
    fatal_if(curTick() <= txn.lastWAcceptedTick,
             "AXI tester observed zero-delay W-to-B completion");
    fatal_if(response.axiId != txn.request.axiId ||
             response.resp != AxiResp::Okay,
             "AXI tester received incorrect B response");
    commitShadow(txn);
    checkTargetMemory(txn);
    ++writesCompleted;
    ++currentTransaction;
}

void
AxiTraceTester::checkR(Transaction &txn)
{
    AxiRBeat response;
    if (!initiators[txn.sourceIndex]->tryConsumeR(response))
        return;
    const uint16_t index = txn.responses;
    fatal_if(index >= txn.request.beatCount ||
             response.axiId != txn.request.axiId ||
             response.resp != AxiResp::Okay ||
             response.last != (index + 1 == txn.request.beatCount),
             "AXI tester received malformed R response");
    const uint64_t beat_address = axiBeatAddress(txn.request, index);
    const uint64_t beat_bytes = uint64_t{1} << txn.request.size;
    const uint64_t bus_base =
        (beat_address / dataBusBytes) * dataBusBytes;
    const uint64_t lane_base = beat_address - bus_base;
    fatal_if(response.functionalData.size() != dataBusBytes ||
             payloadDigest(response.functionalData) != response.payloadDigest,
             "AXI tester R payload shape/digest mismatch");
    for (uint32_t lane = 0; lane < dataBusBytes; ++lane) {
        uint8_t expected = 0;
        if (lane >= lane_base && lane < lane_base + beat_bytes)
            expected = shadowByte(bus_base + lane);
        fatal_if(response.functionalData[lane] != expected,
                 "AXI tester R byte mismatch at beat %u lane %u",
                 index, lane);
    }
    ++txn.responses;
    ++rBeatsConsumed;
    if (response.last) {
        ++readsCompleted;
        ++currentTransaction;
    }
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
AxiTraceTester::writeResult() const
{
    if (resultJson.empty())
        return;
    std::ofstream output(resultJson);
    fatal_if(!output, "cannot create AXI result JSON %s", resultJson);
    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"case\": \"" << caseName << "\",\n"
           << "  \"status\": \"pass\",\n"
           << "  \"transactions_issued\": " << transactions.size() << ",\n"
           << "  \"transactions_completed\": "
           << writesCompleted + readsCompleted << ",\n"
           << "  \"writes_completed\": " << writesCompleted << ",\n"
           << "  \"reads_completed\": " << readsCompleted << ",\n"
           << "  \"w_beats\": " << wBeatsAccepted << ",\n"
           << "  \"r_beats\": " << rBeatsConsumed << ",\n"
           << "  \"max_orphan_w\": " << maxOrphanTransactions << ",\n"
           << "  \"outstanding_at_exit\": 0\n"
           << "}\n";
    fatal_if(!output, "failed writing AXI result JSON %s", resultJson);
}

void
AxiTraceTester::wakeup()
{
    for (const auto *target : targets) {
        maxOrphanTransactions = std::max(
            maxOrphanTransactions,
            target->functionalOccupancy().orphanTransactions);
    }

    if (currentTransaction < transactions.size()) {
        Transaction &txn = transactions[currentTransaction];
        if (txn.kind == Kind::Write) {
            driveWrite(txn);
            checkB(txn);
        } else {
            driveRead(txn);
            checkR(txn);
        }
        quietCycles = 0;
    } else if (allAdaptersIdle()) {
        ++quietCycles;
    } else {
        quietCycles = 0;
    }

    if (!exitRequested && quietCycles >= drainCycles) {
        if (caseName == "w_before_aw_at_target") {
            fatal_if(maxOrphanTransactions == 0,
                     "I4 did not observe target W_ONLY occupancy");
        }
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

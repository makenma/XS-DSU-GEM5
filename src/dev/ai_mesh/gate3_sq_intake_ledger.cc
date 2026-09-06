#include "dev/ai_mesh/gate3_sq_intake_ledger.hh"

#include <stdexcept>
#include <utility>

namespace gem5
{
namespace ai_mesh
{

SqIntakeId
Gate3SqIntakeLedger::reserve(SqSeq expectedSqSeq)
{
    if (nextId == 0)
        throw std::overflow_error("Gate3 SQ intake ID exhausted");
    const SqIntakeId id(nextId++);
    live.emplace(id, Gate3SqIntakeRecord{id, expectedSqSeq});
    ++created;
    return id;
}

Gate3SqIntakeRecord *
Gate3SqIntakeLedger::find(SqIntakeId id)
{
    auto liveRecord = live.find(id);
    if (liveRecord != live.end())
        return &liveRecord->second;
    auto fatalRecord = fatal.find(id);
    return fatalRecord == fatal.end() ? nullptr : &fatalRecord->second;
}

bool
Gate3SqIntakeLedger::acceptRead(SqIntakeId id, uint64_t readTag)
{
    auto found = live.find(id);
    if (found == live.end() || readTag == 0 || found->second.readTag != 0)
        return false;
    found->second.readTag = readTag;
    found->second.stateAtCut = Gate3SqIntakeState::WaitR;
    return true;
}

bool
Gate3SqIntakeLedger::observeTerminal(
    SqIntakeId id, Gate3SqIntakeTerminal terminal)
{
    Gate3SqIntakeRecord *record = find(id);
    if (!record || terminal == Gate3SqIntakeTerminal::None ||
        record->terminal != Gate3SqIntakeTerminal::None)
        return false;
    record->terminal = terminal;
    return true;
}

bool
Gate3SqIntakeLedger::setFirstError(
    SqIntakeId id, std::string firstError)
{
    Gate3SqIntakeRecord *record = find(id);
    if (!record || firstError.empty() || record->firstError != "NONE")
        return false;
    record->firstError = std::move(firstError);
    return true;
}

bool
Gate3SqIntakeLedger::release(SqIntakeId id)
{
    auto found = live.find(id);
    if (found == live.end() ||
        found->second.terminal == Gate3SqIntakeTerminal::None)
        return false;
    live.erase(found);
    ++released;
    return true;
}

std::vector<Gate3SqIntakeRecord>
Gate3SqIntakeLedger::transferLiveToFatal()
{
    std::vector<Gate3SqIntakeRecord> transferred;
    transferred.reserve(live.size());
    for (auto &[id, record] : live) {
        transferred.push_back(record);
        fatal.emplace(id, std::move(record));
    }
    live.clear();
    return transferred;
}

const char *
gate3SqIntakeStateName(Gate3SqIntakeState state)
{
    switch (state) {
      case Gate3SqIntakeState::PreAr:
        return "PRE_AR";
      case Gate3SqIntakeState::WaitR:
        return "WAIT_R";
    }
    throw std::invalid_argument("unknown Gate3 SQ intake state");
}

const char *
gate3SqIntakeTerminalName(Gate3SqIntakeTerminal terminal)
{
    switch (terminal) {
      case Gate3SqIntakeTerminal::None:
        return "NONE";
      case Gate3SqIntakeTerminal::ROk:
        return "R_OK";
      case Gate3SqIntakeTerminal::RError:
        return "R_ERROR";
    }
    throw std::invalid_argument("unknown Gate3 SQ intake terminal");
}

}
}

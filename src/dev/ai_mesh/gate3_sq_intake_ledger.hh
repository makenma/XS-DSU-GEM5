#ifndef DEV_AI_MESH_GATE3_SQ_INTAKE_LEDGER_HH
#define DEV_AI_MESH_GATE3_SQ_INTAKE_LEDGER_HH

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_runtime_types.hh"

namespace gem5
{
namespace ai_mesh
{

enum class Gate3SqIntakeState : uint8_t
{
    PreAr,
    WaitR,
};

enum class Gate3SqIntakeTerminal : uint8_t
{
    None,
    ROk,
    RError,
};

struct Gate3SqIntakeRecord
{
    SqIntakeId id;
    SqSeq expectedSqSeq;
    uint64_t readTag = 0;
    std::string firstError = "NONE";
    Gate3SqIntakeState stateAtCut = Gate3SqIntakeState::PreAr;
    Gate3SqIntakeTerminal terminal = Gate3SqIntakeTerminal::None;
};

class Gate3SqIntakeLedger
{
  public:
    SqIntakeId reserve(SqSeq expectedSqSeq);
    bool acceptRead(SqIntakeId id, uint64_t readTag);
    bool observeTerminal(SqIntakeId id, Gate3SqIntakeTerminal terminal);
    bool setFirstError(SqIntakeId id, std::string firstError);
    bool release(SqIntakeId id);
    std::vector<Gate3SqIntakeRecord> transferLiveToFatal();

    uint64_t createdCount() const { return created; }
    uint64_t releasedCount() const { return released; }
    size_t liveCount() const { return live.size(); }
    size_t fatalCount() const { return fatal.size(); }
    const std::map<SqIntakeId, Gate3SqIntakeRecord> &fatalRecords() const
    { return fatal; }

  private:
    Gate3SqIntakeRecord *find(SqIntakeId id);

    uint64_t nextId = 1;
    uint64_t created = 0;
    uint64_t released = 0;
    std::map<SqIntakeId, Gate3SqIntakeRecord> live;
    std::map<SqIntakeId, Gate3SqIntakeRecord> fatal;
};

const char *gate3SqIntakeStateName(Gate3SqIntakeState state);
const char *gate3SqIntakeTerminalName(Gate3SqIntakeTerminal terminal);

}
}

#endif

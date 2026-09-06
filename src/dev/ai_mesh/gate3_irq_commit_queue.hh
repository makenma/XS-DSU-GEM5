#ifndef DEV_AI_MESH_GATE3_IRQ_COMMIT_QUEUE_HH
#define DEV_AI_MESH_GATE3_IRQ_COMMIT_QUEUE_HH

#include <cstddef>
#include <cstdint>
#include <map>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct Gate3IrqCommit
{
    uint64_t tail = 0;
    uint64_t issueOrdinal = 0;
    uint32_t axiId = 0;

    bool operator==(const Gate3IrqCommit &other) const
    {
        return tail == other.tail && issueOrdinal == other.issueOrdinal &&
            axiId == other.axiId;
    }
};

enum class Gate3IrqStageResult : uint8_t
{
    Accepted,
    Duplicate,
    Stale,
    Conflict,
    Full,
};

class Gate3IrqCommitQueue
{
  public:
    explicit Gate3IrqCommitQueue(size_t capacity);

    Gate3IrqStageResult stage(Gate3IrqCommit commit);
    std::vector<Gate3IrqCommit> consumePrefix();
    uint64_t visibleTail() const { return committedTail; }
    size_t pending() const { return commits.size(); }

  private:
    size_t capacity;
    uint64_t committedTail = 0;
    std::map<uint64_t, Gate3IrqCommit> commits;
};

}
}

#endif

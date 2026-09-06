#include "dev/ai_mesh/gate3_irq_commit_queue.hh"

#include <limits>
#include <stdexcept>

namespace gem5
{
namespace ai_mesh
{

Gate3IrqCommitQueue::Gate3IrqCommitQueue(size_t capacityValue)
    : capacity(capacityValue)
{
    if (capacity == 0)
        throw std::invalid_argument("IRQ commit queue capacity must be positive");
}

Gate3IrqStageResult
Gate3IrqCommitQueue::stage(Gate3IrqCommit commit)
{
    if (commit.tail <= committedTail)
        return Gate3IrqStageResult::Stale;
    const auto found = commits.find(commit.tail);
    if (found != commits.end())
        return found->second == commit ?
            Gate3IrqStageResult::Duplicate : Gate3IrqStageResult::Conflict;
    if (commits.size() == capacity)
        return Gate3IrqStageResult::Full;
    commits.emplace(commit.tail, commit);
    return Gate3IrqStageResult::Accepted;
}

std::vector<Gate3IrqCommit>
Gate3IrqCommitQueue::consumePrefix()
{
    std::vector<Gate3IrqCommit> result;
    while (committedTail != std::numeric_limits<uint64_t>::max()) {
        const auto found = commits.find(committedTail + 1);
        if (found == commits.end())
            break;
        result.push_back(found->second);
        commits.erase(found);
        ++committedTail;
    }
    return result;
}

}
}

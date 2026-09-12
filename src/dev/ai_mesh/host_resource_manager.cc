#include "dev/ai_mesh/host_resource_manager.hh"

#include <algorithm>

namespace gem5
{
namespace ai_mesh
{

namespace
{

size_t kindIndex(HostStageKindV1 kind)
{
    return static_cast<size_t>(kind);
}

uint64_t
saturatingAge(uint64_t nowTick, uint64_t arrivalTick)
{
    return nowTick >= arrivalTick ? nowTick - arrivalTick : 0;
}

}

HostResourceManager::HostResourceManager(
    const std::array<uint32_t, kHostStageKindCount> &slots,
    const std::array<uint32_t, kHostStageKindCount> &queueDepths,
    uint64_t globalTokens,
    const std::array<int64_t, kHostStageKindCount> &weights,
    uint32_t registryCapacity)
    : slotCounts(slots), queueLimits(queueDepths),
      freeSlots_(slots), weights(weights), tokensFree(globalTokens),
      registryCapacity(registryCapacity)
{}

bool
HostResourceManager::arrivalKeyLess(const HostServiceTask &lhs,
                                    const HostServiceTask &rhs)
{
    if (lhs.arrivalTick != rhs.arrivalTick)
        return lhs.arrivalTick < rhs.arrivalTick;
    if (lhs.userId != rhs.userId)
        return lhs.userId < rhs.userId;
    if (lhs.taskSeq != rhs.taskSeq)
        return lhs.taskSeq < rhs.taskSeq;
    if (lhs.repairRound != rhs.repairRound)
        return lhs.repairRound < rhs.repairRound;
    return kindIndex(lhs.kind) < kindIndex(rhs.kind);
}

HostResourceManager::Entry *
HostResourceManager::find(HostTaskId id)
{
    const auto entry = entries.find(id.value());
    return entry == entries.end() ? nullptr : &entry->second;
}

const HostResourceManager::Entry *
HostResourceManager::find(HostTaskId id) const
{
    const auto entry = entries.find(id.value());
    return entry == entries.end() ? nullptr : &entry->second;
}

bool
HostResourceManager::headSatisfiable(size_t kind) const
{
    if (queues[kind].empty())
        return false;
    const Entry *head = find(queues[kind].front());
    return head != nullptr && satisfiable(head->task);
}

bool
HostResourceManager::hasGrantableHead() const
{
    if (agingReservation)
        return false;
    for (size_t kind = 0; kind < kHostStageKindCount; ++kind)
        if (headSatisfiable(kind))
            return true;
    return false;
}

bool
HostResourceManager::satisfiable(const HostServiceTask &task) const
{
    const size_t kind = kindIndex(task.kind);
    return freeSlots_[kind] > 0 &&
           task.hostTokensRequired <= tokensFree;
}

HostEnqueueResult
HostResourceManager::tryEnqueue(const HostServiceTask &task)
{
    if (entries.size() >= registryCapacity)
        return HostEnqueueResult::RejectedRegistryFull;
    const size_t kind = kindIndex(task.kind);
    Entry entry;
    entry.task = task;
    const uint64_t key = task.hostTaskId.value();
    const auto inserted = entries.emplace(key, entry).second;
    if (!inserted)
        return HostEnqueueResult::RejectedRegistryFull;
    tryAdmit(kind);
    return HostEnqueueResult::Accepted;
}

bool
HostResourceManager::tryAdmit(size_t kind)
{
    if (queues[kind].size() >= queueLimits[kind])
        return false;
    Entry *candidateEntry = nullptr;
    for (auto &pair : entries) {
        Entry &entry = pair.second;
        if (entry.state != HostTaskState::WaitHostEnqueue)
            continue;
        if (kindIndex(entry.task.kind) != kind)
            continue;
        if (!candidateEntry ||
            arrivalKeyLess(entry.task, candidateEntry->task))
            candidateEntry = &entry;
    }
    if (!candidateEntry)
        return false;
    const HostTaskId candidate = candidateEntry->task.hostTaskId;
    auto &queue = queues[kind];
    queue.push_back(candidate);
    std::stable_sort(queue.begin(), queue.end(),
                     [this](HostTaskId lhs, HostTaskId rhs) {
                         return arrivalKeyLess(find(lhs)->task,
                                               find(rhs)->task);
                     });
    candidateEntry->state = HostTaskState::Queued;
    return true;
}

void
HostResourceManager::grantFront(size_t kind,
                                std::vector<HostTaskId> &granted)
{
    const HostTaskId id = queues[kind].front();
    queues[kind].erase(queues[kind].begin());
    Entry *entry = find(id);
    entry->state = HostTaskState::AtomicReserved;
    --freeSlots_[kind];
    tokensFree -= entry->task.hostTokensRequired;
    granted.push_back(id);
}

std::vector<HostTaskId>
HostResourceManager::arbitrate(uint64_t nowTick)
{
    std::vector<HostTaskId> granted;
    if (agingReservation) {
        Entry *target = find(reservationTarget);
        if (target && target->state == HostTaskState::Queued &&
            satisfiable(target->task)) {
            grantFront(kindIndex(target->task.kind), granted);
            agingReservation = false;
            reservationTarget = HostTaskId{0};
        } else {
            return granted;
        }
    }
    for (;;) {
        if (agingThresholdTicks != 0) {
            const Entry *oldest = nullptr;
            for (size_t kind = 0; kind < kHostStageKindCount; ++kind) {
                if (queues[kind].empty())
                    continue;
                const Entry *head = find(queues[kind].front());
                if (saturatingAge(nowTick, head->task.arrivalTick) <
                    agingThresholdTicks)
                    continue;
                if (!oldest || arrivalKeyLess(head->task, oldest->task))
                    oldest = head;
            }
            if (oldest) {
                if (satisfiable(oldest->task)) {
                    grantFront(kindIndex(oldest->task.kind), granted);
                    continue;
                }
                agingReservation = true;
                reservationTarget = oldest->task.hostTaskId;
                return granted;
            }
        }
        std::vector<size_t> eligible;
        for (size_t kind = 0; kind < kHostStageKindCount; ++kind) {
            if (queues[kind].empty() || freeSlots_[kind] == 0)
                continue;
            const Entry *head = find(queues[kind].front());
            if (head->task.hostTokensRequired <= tokensFree)
                eligible.push_back(kind);
        }
        if (eligible.empty())
            break;
        int64_t eligibleSum = 0;
        for (const size_t kind : eligible) {
            scores[kind] += weights[kind];
            eligibleSum += weights[kind];
        }
        size_t winner = eligible.front();
        for (const size_t kind : eligible) {
            if (scores[kind] > scores[winner])
                winner = kind;
        }
        scores[winner] -= eligibleSum;
        grantFront(winner, granted);
    }
    return granted;
}

bool
HostResourceManager::markRunning(HostTaskId id)
{
    Entry *entry = find(id);
    if (!entry || entry->state != HostTaskState::AtomicReserved)
        return false;
    entry->state = HostTaskState::RunningTimer;
    return true;
}

bool
HostResourceManager::release(HostTaskId id)
{
    Entry *entry = find(id);
    if (!entry ||
        (entry->state != HostTaskState::RunningTimer &&
         entry->state != HostTaskState::AtomicReserved))
        return false;
    const size_t kind = kindIndex(entry->task.kind);
    ++freeSlots_[kind];
    tokensFree += entry->task.hostTokensRequired;
    entries.erase(id.value());
    while (tryAdmit(kind)) {}
    return true;
}

void
HostResourceManager::setAgingThresholdTicks(uint64_t threshold)
{
    agingThresholdTicks = threshold;
}

HostTaskState
HostResourceManager::state(HostTaskId id) const
{
    const Entry *entry = find(id);
    return entry ? entry->state : HostTaskState::Released;
}

int64_t
HostResourceManager::score(size_t kind) const
{
    return scores[kind];
}

bool
HostResourceManager::hasAgingReservation() const
{
    return agingReservation;
}

uint64_t
HostResourceManager::availableTokens() const
{
    return tokensFree;
}

uint32_t
HostResourceManager::freeSlots(size_t kind) const
{
    return freeSlots_[kind];
}

size_t
HostResourceManager::queueLength(size_t kind) const
{
    return queues[kind].size();
}

size_t
HostResourceManager::waitingCount() const
{
    size_t total = 0;
    for (const auto &pair : entries)
        if (pair.second.state == HostTaskState::WaitHostEnqueue)
            ++total;
    return total;
}

}
}

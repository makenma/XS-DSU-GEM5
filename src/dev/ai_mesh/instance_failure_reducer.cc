#include "dev/ai_mesh/instance_failure_reducer.hh"
#include <algorithm>

namespace gem5::ai_mesh
{
InstanceFailureReducer::InstanceFailureReducer(size_t batch_capacity, size_t limit)
    : member_capacity(limit), rows(batch_capacity)
{
    for (auto &row : rows) {
        row.members.reserve(limit);
        row.record.cancel_tombstone_snapshot.reserve(limit);
    }
}

bool InstanceFailureReducer::begin(uint64_t batch, uint64_t instance,
                                   const std::vector<uint64_t> &population)
{
    if (!batch || !instance || population.size() > member_capacity)
        return false;
    for (const auto &row : rows)
        if (row.active && (row.record.batch_id == batch || row.record.program_instance_id == instance))
            return false;
    for (size_t i = 0; i < population.size(); ++i)
        if (!population[i] || std::find(population.begin(), population.begin() + i, population[i]) !=
                population.begin() + i)
            return false;
    for (auto &row : rows) {
        if (row.active)
            continue;
        row.record.batch_id = batch;
        row.record.program_instance_id = instance;
        for (auto request : population)
            row.members.push_back({request, std::nullopt});
        row.active = true;
        return true;
    }
    return false;
}

bool InstanceFailureReducer::stage(uint64_t instance, const ErrorCandidate &candidate)
{
    for (auto &row : rows) {
        if (!row.active || row.record.program_instance_id != instance)
            continue;
        if (row.sealed)
            return true;
        if ((committed_edge && candidate.tick <= *committed_edge) ||
                (row.pending && row.pending->tick != candidate.tick) || row.pending_count == UINT64_MAX)
            return false;
        if (!row.pending || errorCandidateLess(candidate, *row.pending))
            row.pending = candidate;
        ++row.pending_count;
        return true;
    }
    return false;
}

bool InstanceFailureReducer::cancel(uint64_t instance, uint64_t request, uint64_t tick)
{
    for (auto &row : rows) {
        if (!row.active || row.record.program_instance_id != instance)
            continue;
        for (auto &member : row.members) {
            if (member.request != request)
                continue;
            if (row.sealed)
                return true;
            if (committed_edge && tick < *committed_edge)
                return false;
            if (!member.cancel_tick || tick < *member.cancel_tick)
                member.cancel_tick = tick;
            return true;
        }
    }
    return false;
}

bool InstanceFailureReducer::commitEdge(uint64_t tick)
{
    if (committed_edge && tick <= *committed_edge)
        return false;
    for (const auto &row : rows)
        if (row.active && row.pending && row.pending->tick != tick)
            return false;
    for (auto &row : rows) {
        if (row.pending && !row.sealed) {
            row.record.first = *row.pending;
            for (const auto &member : row.members)
                if (member.cancel_tick && *member.cancel_tick < tick)
                    row.record.cancel_tombstone_snapshot.push_back(member.request);
            std::sort(row.record.cancel_tombstone_snapshot.begin(), row.record.cancel_tombstone_snapshot.end());
            row.first_count = row.pending_count;
            row.sealed = true;
        }
        row.pending.reset();
        row.pending_count = 0;
    }
    committed_edge = tick;
    return true;
}

const BatchFailureRecord *InstanceFailureReducer::failure(uint64_t instance) const
{
    for (const auto &row : rows)
        if (row.active && row.sealed && row.record.program_instance_id == instance)
            return &row.record;
    return nullptr;
}

uint64_t InstanceFailureReducer::firstEdgeCandidateCount(uint64_t instance) const
{
    for (const auto &row : rows)
        if (row.active && row.sealed && row.record.program_instance_id == instance)
            return row.first_count;
    return 0;
}

void InstanceFailureReducer::release(uint64_t instance)
{
    for (auto &row : rows) {
        if (!row.active || row.record.program_instance_id != instance)
            continue;
        row.active = row.sealed = false;
        row.members.clear();
        row.record.batch_id = row.record.program_instance_id = 0;
        row.record.first = {};
        row.record.cancel_tombstone_snapshot.clear();
        row.pending.reset();
        row.pending_count = row.first_count = 0;
        return;
    }
}
}

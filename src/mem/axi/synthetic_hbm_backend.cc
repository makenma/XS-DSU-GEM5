#include "mem/axi/synthetic_hbm_backend.hh"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <tuple>

namespace gem5
{
namespace axi
{

SyntheticHbmBackend::SyntheticHbmBackend(uint32_t bytes_per_cycle,
                                         uint32_t queue_depth)
    : bytesPerCycle(bytes_per_cycle), queueDepth(queue_depth)
{
    if (bytesPerCycle == 0 || queueDepth == 0)
        throw std::invalid_argument("synthetic HBM capacities must be positive");
}

bool
SyntheticHbmBackend::trySubmit(uint64_t uid, SyntheticHbmDirection direction,
                               uint64_t bytes, uint64_t now, uint64_t latency)
{
    if (bytes == 0 || latency > std::numeric_limits<uint64_t>::max() - now)
        throw std::invalid_argument("synthetic HBM request size/time invalid");
    if (jobs.count(uid))
        throw std::invalid_argument("duplicate synthetic HBM request uid");
    advance(now);
    if (full())
        return false;
    if (nextSequence == std::numeric_limits<uint64_t>::max())
        throw std::overflow_error("synthetic HBM sequence exhausted");
    jobs.emplace(uid, Job{direction, bytes, now + latency, nextSequence++, {}});
    ++counters.submitted;
    counters.highWater = std::max(counters.highWater, jobs.size());
    return true;
}

void
SyntheticHbmBackend::advance(uint64_t now)
{
    if (now < currentCycle)
        throw std::invalid_argument("synthetic HBM time moved backwards");
    const uint64_t elapsed = now - currentCycle;
    counters.requestSlotCycles += jobs.size() * elapsed;
    if (full())
        counters.queueFullCycles += elapsed;
    uint64_t cursor = currentCycle;
    while (cursor < now) {
        if (!active) {
            auto selected = jobs.end();
            for (auto it = jobs.begin(); it != jobs.end(); ++it) {
                if (it->second.completedAt)
                    continue;
                if (selected == jobs.end() ||
                    std::tie(it->second.eligibleAt, it->second.sequence) <
                    std::tie(selected->second.eligibleAt,
                             selected->second.sequence))
                    selected = it;
            }
            if (selected == jobs.end() || selected->second.eligibleAt >= now)
                break;
            cursor = std::max(cursor, selected->second.eligibleAt);
            active = selected->first;
        }
        Job &job = jobs.at(*active);
        const uint64_t required = job.remaining / bytesPerCycle +
                                  (job.remaining % bytesPerCycle != 0);
        const uint64_t cycles = std::min(required, now - cursor);
        const uint64_t bytes = cycles == required ? job.remaining
                                                   : cycles * bytesPerCycle;
        job.remaining -= bytes;
        cursor += cycles;
        counters.busyCycles += cycles;
        if (job.direction == SyntheticHbmDirection::Read)
            counters.readBytesServiced += bytes;
        else
            counters.writeBytesServiced += bytes;
        if (job.remaining == 0) {
            job.completedAt = cursor;
            completedUids.push_back(*active);
            ++counters.completed;
            active.reset();
        }
    }
    currentCycle = now;
}

std::vector<SyntheticHbmCompletion>
SyntheticHbmBackend::takeCompleted()
{
    std::vector<SyntheticHbmCompletion> completed;
    completed.reserve(completedUids.size());
    for (const uint64_t uid : completedUids) {
        const auto it = jobs.find(uid);
        const Job &job = it->second;
        completed.push_back({uid, job.direction, *job.completedAt});
        jobs.erase(it);
        ++counters.retired;
    }
    completedUids.clear();
    return completed;
}

SyntheticHbmStats
SyntheticHbmBackend::stats() const
{
    SyntheticHbmStats result = counters;
    for (const auto &[uid, job] : jobs) {
        if (job.completedAt)
            ++result.ready;
        else
            ++result.queued;
    }
    return result;
}

}
}

#include "dev/ai_mesh/publish_segment_ledger.hh"
#include <algorithm>
#include <tuple>

namespace gem5::ai_mesh
{
PublishSegmentLedger::PublishSegmentLedger(std::vector<PublishDescriptorPlan> plans)
{
    std::sort(plans.begin(), plans.end(), [](const auto &a, const auto &b) {
        return std::tie(a.request, a.offset, a.descriptor) <
               std::tie(b.request, b.offset, b.descriptor);
    });
    entries.reserve(plans.size());
    uint64_t request = 0, end = 0;
    for (const auto &plan : plans) {
        if (plan.request != request) {
            request = plan.request;
            end = 0;
        }
        if (!plan.descriptor || !plan.request || !plan.bytes || plan.offset != end ||
                plan.bytes > UINT64_MAX - end || contains(plan.descriptor))
            throw std::invalid_argument("PUBLISH plan must have unique descriptors and contiguous member ranges");
        Entry entry;
        entry.plan = plan;
        entries.push_back(entry);
        end += plan.bytes;
    }
}

bool PublishSegmentLedger::contains(uint32_t descriptor) const
{
    return std::any_of(entries.begin(), entries.end(), [descriptor](const auto &entry) {
        return entry.plan.descriptor == descriptor;
    });
}

PublishSegmentLedger::Decision
PublishSegmentLedger::decision(uint32_t descriptor, uint64_t tick) const
{
    const auto found = std::find_if(entries.begin(), entries.end(), [descriptor](const auto &entry) {
        return entry.plan.descriptor == descriptor;
    });
    if (found == entries.end())
        return Decision::Stop;
    const auto &entry = *found;
    if (entry.inflight || entry.suppressed || entry.committed == entry.plan.bytes)
        return Decision::Wait;
    const bool prior_cancel = entry.cancel_tick && failure_tick &&
        *entry.cancel_tick < *failure_tick;
    if (entry.failed || (failure_tick && !prior_cancel))
        return Decision::Stop;
    if (!entry.started && entry.cancel_tick && *entry.cancel_tick < tick)
        return Decision::Suppress;
    for (const auto &other : entries)
        if (other.plan.request == entry.plan.request) {
            if (other.failed)
                return Decision::Stop;
            if (other.commit_tick && *other.commit_tick >= tick)
                return Decision::Wait;
        }
    if (prefix(entry.plan.request) != entry.plan.offset + entry.committed)
        return Decision::Wait;
    return Decision::Issue;
}

bool PublishSegmentLedger::issue(uint32_t descriptor, uint64_t offset, uint64_t bytes, uint64_t tick)
{
    if (!bytes || decision(descriptor, tick) != Decision::Issue)
        return false;
    for (auto &entry : entries)
        if (entry.plan.descriptor == descriptor) {
            if (offset != entry.committed || bytes > entry.plan.bytes - entry.committed)
                return false;
            entry.inflight = bytes;
            entry.issue_tick = tick;
            entry.started = true;
            if (fact_sink)
                fact_sink({entry.plan.request, 0, descriptor, entry.plan.offset + offset,
                           bytes, tick, PublishSegmentFact::Kind::Issue});
            return true;
        }
    return false;
}

bool PublishSegmentLedger::commit(uint32_t descriptor, uint64_t offset, uint64_t bytes,
                                  bool okay, uint64_t tick)
{
    for (auto &entry : entries)
        if (entry.plan.descriptor == descriptor) {
            if (!bytes || entry.inflight != bytes || offset != entry.committed || tick < entry.issue_tick ||
                    (entry.commit_tick && tick <= *entry.commit_tick))
                return false;
            entry.inflight = 0;
            entry.failed = !okay;
            entry.commit_tick = tick;
            if (okay)
                entry.committed += bytes;
            if (fact_sink)
                fact_sink({entry.plan.request, 0, descriptor, entry.plan.offset + offset,
                           bytes, tick, okay ? PublishSegmentFact::Kind::Okay : PublishSegmentFact::Kind::Error});
            return true;
        }
    return false;
}

bool PublishSegmentLedger::suppress(uint32_t descriptor, uint64_t tick)
{
    if (decision(descriptor, tick) != Decision::Suppress)
        return false;
    for (auto &entry : entries)
        if (entry.plan.descriptor == descriptor) {
            entry.suppressed = true;
            if (fact_sink)
                fact_sink({entry.plan.request, 0, descriptor, entry.plan.offset,
                           entry.plan.bytes, tick, PublishSegmentFact::Kind::Suppress});
            return true;
        }
    return false;
}

bool PublishSegmentLedger::cancel(uint64_t request, uint64_t tick)
{
    bool found = false;
    for (auto &entry : entries)
        if (entry.plan.request == request) {
            if (!entry.cancel_tick || tick < *entry.cancel_tick)
                entry.cancel_tick = tick;
            found = true;
        }
    return found;
}

void PublishSegmentLedger::fail(uint64_t tick)
{
    if (!failure_tick || tick < *failure_tick)
        failure_tick = tick;
}

uint64_t PublishSegmentLedger::prefix(uint64_t request) const
{
    uint64_t bytes = 0;
    for (const auto &entry : entries)
        if (entry.plan.request == request) {
            if (entry.plan.offset != bytes)
                break;
            bytes += entry.committed;
            if (entry.committed != entry.plan.bytes)
                break;
        }
    return bytes;
}

bool PublishSegmentLedger::started(uint32_t descriptor) const
{
    for (const auto &entry : entries)
        if (entry.plan.descriptor == descriptor)
            return entry.started;
    return false;
}

bool PublishSegmentLedger::suppressed(uint32_t descriptor) const
{
    for (const auto &entry : entries)
        if (entry.plan.descriptor == descriptor)
            return entry.suppressed;
    return false;
}
}

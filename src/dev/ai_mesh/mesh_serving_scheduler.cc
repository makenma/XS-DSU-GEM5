#include "dev/ai_mesh/mesh_serving_scheduler.hh"

#include <algorithm>
#include <set>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
orderLess(const SchedulerMember &left, const SchedulerMember &right)
{
    if (left.deadlineTick != right.deadlineTick)
        return left.deadlineTick < right.deadlineTick;
    const uint8_t leftRank = 255 - left.qos;
    const uint8_t rightRank = 255 - right.qos;
    if (leftRank != rightRank)
        return leftRank < rightRank;
    if (left.phaseReadyTick != right.phaseReadyTick)
        return left.phaseReadyTick < right.phaseReadyTick;
    return left.requestId < right.requestId;
}

bool
feasibleCount(const SchedulerMember &head, uint32_t count,
              const SchedulerLimits &limits)
{
    if (uint64_t(count) * head.validTokensPerMember > limits.maxBatchTokens)
        return false;
    switch (head.phase) {
      case SchedulerPhase::Prefill:
        return uint64_t(count) * head.validTokensPerMember
            <= limits.maxPrefillTokensPerBatch;
      case SchedulerPhase::Decode:
        return count <= limits.maxDecodeSequencesPerBatch;
      case SchedulerPhase::Publish:
        return count <= limits.maxActiveSequences;
    }
    return false;
}

uint64_t
checkedAdd(uint64_t left, uint64_t right)
{
    return left > kSchedulerMaxTick - right ? kSchedulerMaxTick
                                            : left + right;
}

uint64_t
cycleTicks(uint64_t cycles, uint64_t period)
{
    return cycles > kSchedulerMaxTick / period ? kSchedulerMaxTick
                                               : cycles * period;
}

} // namespace

bool
ServingBatchKey::operator==(const ServingBatchKey &other) const
{
    return programId == other.programId
        && requestProfileId == other.requestProfileId
        && pathKind == other.pathKind && phase == other.phase
        && validTokensPerMember == other.validTokensPerMember
        && kvTokensBefore == other.kvTokensBefore
        && decodeChunkTokens == other.decodeChunkTokens;
}

const char *
schedulerPhaseName(SchedulerPhase phase)
{
    switch (phase) {
      case SchedulerPhase::Prefill:
        return "PREFILL";
      case SchedulerPhase::Decode:
        return "DECODE";
      case SchedulerPhase::Publish:
        return "PUBLISH";
    }
    return "?";
}

static ServingBatchKey
batchKeyOf(const SchedulerMember &member)
{
    ServingBatchKey key;
    key.programId = member.programId;
    key.requestProfileId = member.requestProfileId;
    key.pathKind = member.pathKind;
    key.phase = member.phase;
    key.validTokensPerMember = member.validTokensPerMember;
    key.kvTokensBefore = member.kvTokensBefore;
    key.decodeChunkTokens = member.decodeChunkTokens;
    return key;
}

SchedulerDecision
servingSchedulerDecide(
    const std::vector<SchedulerMember> &members,
    const SchedulerLimits &limits, uint64_t now,
    const std::function<std::vector<uint32_t>(const ServingBatchKey &)>
        &profileCounts, uint64_t clockPeriodTicks)
{
    fatal_if(clockPeriodTicks == 0, "scheduler clock period must be positive");
    SchedulerDecision decision;
    const SchedulerPhase *phase = nullptr;
    bool agedPrefill = false;
    bool sawDecode = false;
    bool sawPrefill = false;
    for (const SchedulerMember &member : members) {
        if (member.phase == SchedulerPhase::Publish)
            phase = &member.phase;
        if (member.phase == SchedulerPhase::Prefill) {
            sawPrefill = true;
            if (now >= member.phaseReadyTick &&
                now - member.phaseReadyTick >=
                    cycleTicks(limits.prefillStarvationCycles, clockPeriodTicks))
                agedPrefill = true;
        }
        if (member.phase == SchedulerPhase::Decode)
            sawDecode = true;
    }
    SchedulerPhase selected;
    if (phase != nullptr)
        selected = SchedulerPhase::Publish;
    else if (agedPrefill)
        selected = SchedulerPhase::Prefill;
    else if (sawDecode)
        selected = SchedulerPhase::Decode;
    else if (sawPrefill)
        selected = SchedulerPhase::Prefill;
    else
        return decision;

    std::vector<SchedulerMember> candidates;
    for (const SchedulerMember &member : members)
        if (member.phase == selected)
            candidates.push_back(member);
    std::sort(candidates.begin(), candidates.end(), orderLess);
    const SchedulerMember &head = candidates.front();
    const ServingBatchKey key = batchKeyOf(head);
    std::vector<SchedulerMember> prefix;
    for (const SchedulerMember &member : candidates) {
        if (!prefix.empty() && !(batchKeyOf(member) == key))
            break;
        prefix.push_back(member);
    }
    std::set<uint32_t> feasible;
    for (uint32_t count : profileCounts(key))
        if (feasibleCount(head, count, limits))
            feasible.insert(count);
    uint32_t runnable = 0;
    bool largerExists = false;
    for (uint32_t count : feasible) {
        if (count <= prefix.size())
            runnable = std::max(runnable, count);
        else
            largerExists = true;
    }
    decision.phase = selected;
    if (selected == SchedulerPhase::Publish) {
        if (runnable == 0) {
            decision.action = SchedulerAction::FailHead;
            decision.members = {head.requestId};
            decision.memberCount = 1;
            return decision;
        }
    } else {
        const uint64_t deadline =
            checkedAdd(head.phaseReadyTick,
                       cycleTicks(limits.batchWaitCycles, clockPeriodTicks));
        if (runnable == 0) {
            if (now < deadline) {
                decision.action = SchedulerAction::Wait;
                decision.waitUntil = deadline;
                return decision;
            }
            decision.action = SchedulerAction::FailHead;
            decision.members = {head.requestId};
            decision.memberCount = 1;
            return decision;
        }
        if (largerExists && now < deadline) {
            decision.action = SchedulerAction::Wait;
            decision.waitUntil = deadline;
            return decision;
        }
    }
    decision.action = SchedulerAction::Run;
    uint8_t effectiveQos = 0;
    for (uint32_t index = 0; index < runnable; ++index) {
        decision.members.push_back(prefix[index].requestId);
        effectiveQos = std::max(effectiveQos, prefix[index].qos);
    }
    decision.memberCount = runnable;
    decision.batchEffectiveQos = effectiveQos;
    return decision;
}

} // namespace
} // namespace gem5

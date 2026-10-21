#include "dev/ai_mesh/serving_mesh_executor.hh"

namespace gem5::ai_mesh
{

std::vector<ServingMemberIdentity>
ServingMeshExecutor::instanceMembersForFact(uint64_t instance, uint64_t request,
                                           uint32_t generation, uint64_t tick)
{
    if (request != 0) {
        auto *current = requestForFact(request, generation, tick);
        return current ? std::vector<ServingMemberIdentity>{{request, generation, 0}}
                       : std::vector<ServingMemberIdentity>{};
    }
    if (generation == 0)
        for (const auto &batch : batches)
            if (batch->members.size() >= 2 && batch->selected_instance &&
                    batch->selected_instance->value() == instance)
                return batch->members;
    recordStaleFact(tick);
    return {};
}

void
ServingMeshExecutor::onInstanceStarted(uint64_t instance, uint64_t request,
                                      uint32_t generation, uint64_t tick)
{
    for (const auto &member : instanceMembersForFact(instance, request, generation, tick))
        startMember(instance, member.request_id, member.generation, tick);
}

void
ServingMeshExecutor::onInstanceFailure(const BatchFailureRecord &record)
{
    for (const auto &batch : batches) {
        if (!batch->selected_instance ||
                batch->selected_instance->value() != record.program_instance_id)
            continue;
        for (const auto &member : batch->members) {
            auto &current = slot(member.request_id);
            if (!current.batch_failure) {
                current.batch_failure = record.first;
                current.cancelled_before_batch_failure = std::find(
                    record.cancel_tombstone_snapshot.begin(),
                    record.cancel_tombstone_snapshot.end(), member.request_id) !=
                    record.cancel_tombstone_snapshot.end();
            }
            errorMember(record.program_instance_id, member.request_id,
                        member.generation, record.first.tick);
        }
        if (!batch->armed_instance_generation) {
            const auto members = batch->members;
            for (const auto &member : members)
                markFailed(slot(member.request_id), "frozen instance failed before start");
        }
        return;
    }
    recordStaleFact(record.first.tick);
}

void
ServingMeshExecutor::onInstanceSettled(uint64_t instance, uint64_t request,
                                     uint32_t generation, bool errored, uint64_t tick)
{
    for (const auto &member : instanceMembersForFact(instance, request, generation, tick))
        settleMember(instance, member.request_id, member.generation, errored, tick);
}

}

#include "dev/ai_mesh/dma_command_lifecycle.hh"

namespace gem5
{
namespace ai_mesh
{

const DmaCommandState *
DmaCommandLifecycle::find(uint32_t command_id) const
{
    auto found = commands.find(command_id);
    return found == commands.end() ? nullptr : &found->second;
}

bool
DmaCommandLifecycle::admitted(uint32_t command_id) const
{
    return commands.count(command_id) != 0;
}

bool
DmaCommandLifecycle::submitting(uint32_t command_id, uint32_t generation) const
{
    const DmaCommandState *state = find(command_id);
    return state != nullptr && state->generation == generation &&
           !state->terminal && !state->failed && !state->cancelled;
}

DmaCommandState *
DmaCommandLifecycle::admit(const Admission &admission)
{
    DmaCommandState &state = commands[admission.command_id];
    state = DmaCommandState{};
    state.generation = admission.generation;
    state.completion_event = admission.completion_event;
    state.transfer_id = admission.transfer_id;
    state.receiver_core = admission.receiver_core;
    state.receiver_allocations = admission.receiver_allocations;
    state.expected_bytes = admission.expected_bytes;
    state.descriptors = admission.descriptors;
    return &state;
}

uint32_t
DmaCommandLifecycle::nextDescriptor(uint32_t command_id) const
{
    const DmaCommandState *state = find(command_id);
    if (state == nullptr || state->terminal || state->failed ||
        state->cancelled || state->next_descriptor >= state->descriptors.size())
        return 0;
    return state->descriptors[state->next_descriptor];
}

void
DmaCommandLifecycle::noteSubmitted(uint32_t command_id, uint32_t descriptor_id)
{
    auto found = commands.find(command_id);
    if (found == commands.end())
        return;
    DmaCommandState &state = found->second;
    if (state.next_descriptor < state.descriptors.size() &&
        state.descriptors[state.next_descriptor] == descriptor_id)
        state.next_descriptor++;
    state.pending.insert(descriptor_id);
}

bool
DmaCommandLifecycle::fail(uint32_t command_id)
{
    auto found = commands.find(command_id);
    if (found == commands.end())
        return true;
    found->second.failed = true;
    return found->second.pending.empty();
}

bool
DmaCommandLifecycle::cancel(uint32_t command_id)
{
    auto found = commands.find(command_id);
    if (found == commands.end())
        return true;
    DmaCommandState &state = found->second;
    if (state.terminal || state.failed)
        return state.pending.empty();
    state.cancelled = true;
    return state.pending.empty();
}

void
DmaCommandLifecycle::markErrorPublished(uint32_t command_id)
{
    auto found = commands.find(command_id);
    if (found != commands.end())
        found->second.error_published = true;
}

void
DmaCommandLifecycle::finish(uint32_t command_id)
{
    auto found = commands.find(command_id);
    if (found != commands.end())
        found->second.terminal = true;
}

TransferObservation
DmaCommandLifecycle::progressOf(const DmaCommandState &state) const
{
    TransferObservation row;
    row.transfer_id = state.transfer_id;
    row.expected_descriptors = uint32_t(state.descriptors.size());
    row.committed_descriptors = uint32_t(state.committed.size());
    row.expected_bytes = state.expected_bytes;
    row.committed_bytes = state.committed_bytes;
    row.failed = state.failed || state.cancelled;
    row.sender_published = state.published;
    row.sender_notifications = state.notifications;
    return row;
}

DmaCommandLifecycle::Retirement
DmaCommandLifecycle::retire(uint32_t command_id, uint32_t descriptor_id,
                            uint64_t useful_bytes, bool success,
                            uint16_t peer_core)
{
    Retirement result;
    auto found = commands.find(command_id);
    if (found == commands.end())
        return result;
    result.known = true;
    DmaCommandState &state = found->second;
    result.has_transfer = state.transfer_id != 0;
    result.transfer_id = state.transfer_id;
    result.peer_core = peer_core;

    if (!state.pending.erase(descriptor_id))
        result.duplicate = true;
    else if (success && state.committed.insert(descriptor_id).second)
        state.committed_bytes += useful_bytes;

    if (!success && !state.failed) {
        state.failed = true;
        result.first_failure = true;
    }

    if (result.has_transfer && !state.published && !state.failed &&
        !state.cancelled && state.next_descriptor == state.descriptors.size() &&
        state.committed.size() == state.descriptors.size() &&
        state.committed_bytes == state.expected_bytes) {
        state.published = true;
        state.notifications++;
        result.notify = true;
    }

    if (result.has_transfer)
        result.progress = progressOf(state);
    result.drained = state.pending.empty() &&
                     (state.failed || state.cancelled ||
                      state.next_descriptor == state.descriptors.size());
    return result;
}

bool
DmaCommandLifecycle::drained() const
{
    for (const auto &entry : commands)
        if (!entry.second.terminal)
            return false;
    return true;
}

} // namespace ai_mesh
} // namespace gem5

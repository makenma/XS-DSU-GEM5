#include "dev/ai_mesh/mesh_dummy_core.hh"

#include <algorithm>
#include <optional>

#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_hash.hh"

namespace gem5
{
namespace ai_mesh
{

MeshDummyCore::ResourceSnapshot
MeshDummyCore::currentResources() const
{
    ResourceSnapshot snapshot;
    snapshot.live_commands = live_commands;
    snapshot.live_dma_commands = liveDmaCommandCount();
    snapshot.live_dma_tags = live_dma_tags.size();
    snapshot.allocation_pins = allocation_pins.size();
    for (EngineKind kind :
         {EngineKind::Tensor, EngineKind::Vector, EngineKind::Reduce})
        snapshot.engine_occupancy.push_back({kind, engineOccupancy(kind)});
    return snapshot;
}

const ObservationFrame *
MeshDummyCore::archivedObservationFrame() const
{
    if (dispatcher == nullptr)
        return nullptr;
    const InstanceLedger *ledger = dispatcher->lastInstanceLedger(core_id_value);
    return ledger == nullptr ? nullptr : &ledger->observations;
}

std::map<uint32_t, Tick>
MeshDummyCore::commandIssueTicks() const
{
    const ObservationFrame *frame =
        observations.frameActive() ? &observations.view() : archivedObservationFrame();
    std::map<uint32_t, Tick> ticks;
    if (frame == nullptr)
        return ticks;
    for (const CommandObservation &row : frame->commands)
        if (row.issued)
            ticks[row.command.command_id] = row.issue_tick;
    return ticks;
}

std::map<uint32_t, Tick>
MeshDummyCore::commandDoneTicks() const
{
    const ObservationFrame *frame =
        observations.frameActive() ? &observations.view() : archivedObservationFrame();
    std::map<uint32_t, Tick> ticks;
    if (frame == nullptr)
        return ticks;
    for (const CommandObservation &row : frame->commands)
        if (row.terminal)
            ticks[row.command.command_id] = row.terminal_tick;
    return ticks;
}

std::vector<ComputeOutputObservation>
MeshDummyCore::computeOutputs() const
{
    std::vector<ComputeOutputObservation> outputs;
    if (dispatcher != nullptr)
        for (const InstanceLedger *ledger : dispatcher->instanceLedgers(core_id_value))
            outputs.insert(outputs.end(), ledger->observations.compute_outputs.begin(),
                           ledger->observations.compute_outputs.end());
    if (observations.frameActive())
        outputs.insert(outputs.end(), observations.view().compute_outputs.begin(),
                       observations.view().compute_outputs.end());
    return outputs;
}

std::optional<DescriptorKey>
MeshDummyCore::frozenDescriptorKey(uint32_t command_id,
                                   uint32_t descriptor_id) const
{
    const DmaCommandState *found = dma_lifecycle.find(command_id);
    if (found == nullptr)
        return std::nullopt;
    const std::vector<uint32_t> &descriptors = found->descriptors;
    if (std::find(descriptors.begin(), descriptors.end(), descriptor_id) ==
        descriptors.end())
        return std::nullopt;
    DescriptorKey key;
    key.command = CommandGeneration{command_id, found->generation};
    key.descriptor_id = descriptor_id;
    return key;
}

void
MeshDummyCore::recordDescriptorSubmission(const DescriptorKey &key,
                                          Tick submit_tick,
                                          Tick scheduled_completion_tick)
{
    observations.recordDescriptorSubmission(key, submit_tick,
                                            scheduled_completion_tick);
}

void
MeshDummyCore::recordDescriptorCompletion(const DescriptorKey &key,
                                          Tick completion_tick, bool committed,
                                          Tick commit_tick, DmaStatus status,
                                          const TrafficContribution &transfer)
{
    observations.recordDescriptorCompletion(key, completion_tick, committed,
                                            commit_tick, status, transfer);
}

void
MeshDummyCore::recordFillLanding(
    const DescriptorKey &key, const std::string &landing_digest,
    const DestinationStorageObservation &destination_storage,
    const std::vector<SentinelRangeObservation> &sentinel_ranges)
{
    observations.recordFillLanding(key, landing_digest, destination_storage,
                                   sentinel_ranges);
}

void
MeshDummyCore::recordDescriptorSourceRows(
    const DescriptorKey &key, const std::vector<ContentRowObservation> &rows)
{
    observations.recordDescriptorSourceRows(key, rows);
}

void
MeshDummyCore::recordFaultTarget(const DescriptorKey &key,
                                 const std::string &before_digest,
                                 const std::string &after_digest)
{
    observations.recordFaultTarget(key, before_digest, after_digest);
}

void
MeshDummyCore::recordTransferCommit(const DecodedDmaDescriptor &descriptor,
                                    const TransferCommitContent &content)
{
    const DmaCommandState *state = dma_lifecycle.find(descriptor.command_id);
    fatal_if(state == nullptr,
             "transfer commit for unadmitted command %u",
             descriptor.command_id);
    if (state->transfer_id == 0)
        return;
    TransferCommitObservation row;
    row.command = CommandGeneration{descriptor.command_id, state->generation};
    row.descriptor_id = descriptor.descriptor_id;
    row.transfer_id = state->transfer_id;
    row.commit_tick = curTick();
    const uint64_t source_base =
        admittedEndpointAddress(state->descriptors.front(), true);
    const uint64_t source_self =
        admittedEndpointAddress(descriptor.descriptor_id, true);
    row.logical_start =
        source_self >= source_base ? source_self - source_base : 0;
    row.logical_bytes = descriptor.useful_bytes;
    row.source_digest = content.source_digest;
    row.target_digest = content.target_digest;
    row.target_initial_digest = content.target_initial_digest;
    row.committed_bytes = state->committed_bytes;
    row.expected_bytes = state->expected_bytes;
    row.sender_notifications = state->notifications;
    row.sender_published = state->published;

    mesh_hash::Sha256 pending;
    uint64_t pending_bytes = 0;
    bool pending_available = true;
    for (uint32_t admitted_id : state->descriptors) {
        if (state->committed.count(admitted_id) != 0)
            continue;
        const DecodedDmaDescriptor *other = descriptorById(admitted_id);
        if (other == nullptr || other->kind != mesh_abi::kDmaKindP2P_PUSH)
            continue;
        const uint64_t base = admittedEndpointAddress(admitted_id, false);
        std::vector<uint8_t> buffer(other->row_bytes);
        mesh_hash::Sha256 span;
        uint64_t span_bytes = 0;
        for (uint32_t row_index = 0; row_index < other->rows; row_index++) {
            const uint64_t address =
                base + uint64_t(row_index) * other->dst_stride_bytes;
            if (dma == nullptr ||
                !dma->readFunctional(address, other->row_bytes,
                                     buffer.data())) {
                pending_available = false;
                break;
            }
            pending.update(buffer.data(), other->row_bytes);
            span.update(buffer.data(), other->row_bytes);
            pending_bytes += other->row_bytes;
            span_bytes += other->row_bytes;
        }
        if (!pending_available)
            break;
        PendingSpanObservation observation;
        observation.descriptor_id = admitted_id;
        observation.size = span_bytes;
        observation.digest = mesh_hash::digestHex(span.digest());
        row.pending_spans.push_back(std::move(observation));
    }
    row.pending_bytes = pending_bytes;
    if (pending_bytes > 0 && pending_available)
        row.pending_digest = mesh_hash::digestHex(pending.digest());

    if (dispatcher != nullptr) {
        row.receiver_core = state->receiver_core;
        row.receiver_admitted_allocations = state->receiver_allocations;
        const MeshDummyCore *receiver = dispatcher->core(state->receiver_core);
        if (receiver != nullptr) {
            row.receiver_present = true;
            row.receiver_transfer_committed =
                receiver->transferCommitted(state->transfer_id);
            row.receiver_notified_allocation_id =
                receiver->receivedAllocation(state->transfer_id);
            if (!state->receiver_allocations.empty()) {
                row.receiver_allocation_id = state->receiver_allocations.front();
                row.receiver_allocation_valid =
                    receiver->allocationValid(row.receiver_allocation_id);
            }
            row.receiver_notifications =
                receiver->receiveNotificationCount(state->transfer_id);
            row.receiver_notification_tick =
                receiver->receiveNotificationTick(state->transfer_id);
        }
    }
    observations.recordTransferCommit(row);
}

} // namespace ai_mesh
} // namespace gem5

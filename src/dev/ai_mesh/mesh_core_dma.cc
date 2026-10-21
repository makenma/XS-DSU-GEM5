#include "dev/ai_mesh/mesh_dummy_core.hh"

#include <algorithm>
#include <array>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/mesh_clock.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_program_invocation.hh"

namespace gem5::ai_mesh
{

const GatherIndexFact *MeshDummyCore::admittedGatherIndex(uint32_t descriptor_id) const
{
    return admission->dma().gatherIndex(descriptor_id);
}

std::optional<uint64_t> MeshDummyCore::reserveIndexRead(uint64_t offset, uint64_t tick)
{
    fatal_if(tick != curTick(), "gather reservation uses a stale tick");
    const auto service = tryReserveSramService(offset, 4, false);
    if (!service)
        return std::nullopt;
    return std::max(curTick() + service->stall_ticks, clockEdge(Cycles(1)));
}

bool MeshDummyCore::readIndexWord(uint64_t offset, std::array<uint8_t, 4> &bytes)
{
    return functionalSramRead(offset, bytes.size(), bytes.data());
}

bool MeshDummyCore::tryPinDmaAllocations(const DecodedCommand &command)
{
    // SRAM allocations touched by an in-flight DMA are pinned: a second DMA
    // on the same allocation must wait (spec 7.1 admit-to-completion pin).
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        auto it = sram.allocations.find(operand.allocation_id);
        if (it == sram.allocations.end())
            continue;
        if (allocation_pins[operand.allocation_id] > 0)
            return false;
    }
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        if (sram.allocations.count(operand.allocation_id))
            allocation_pins[operand.allocation_id]++;
    }
    return true;
}

void MeshDummyCore::unpinDmaAllocations(const DecodedCommand &command)
{
    for (uint16_t i = 0; i < command.operand_count; i++) {
        const DecodedOperand &operand =
            program->transport.command_operands[command.operand_begin + i];
        auto pin = allocation_pins.find(operand.allocation_id);
        if (pin == allocation_pins.end())
            continue;
        fatal_if(pin->second == 0, "allocation pin underflow");
        if (--pin->second == 0)
            allocation_pins.erase(pin);
    }
}

void
MeshDummyCore::releaseInstanceInputs()
{
    runtime_fill_inputs.reset();
    dma->releaseFillBindings(instance_generation);
}

void
MeshDummyCore::setInstanceBinding(const ServingInstanceBinding &value)
{
    armed_binding = value;
    if (dma != nullptr)
        dma->bindInstance(value);
}

ServingMemberIdentity
MeshDummyCore::dmaOwner(const DecodedDmaDescriptor &descriptor) const
{
    const ServingMemberIdentity batch_owner{
        armed_binding.requestId(), armed_binding.requestGeneration(), UINT32_MAX};
    const bool write = descriptor.kind == mesh_abi::kDmaKindSTORE;
    if ((!write && descriptor.kind != mesh_abi::kDmaKindLOAD) ||
            descriptor.useful_bytes == 0)
        return batch_owner;
    const auto access = armed_binding.resolveDescriptorRows(descriptor.descriptor_id, !write);
    fatal_if(!access, "E_BINDING_ROLE: descriptor %u leaves its frozen member slot",
             descriptor.descriptor_id);
    return access->member.value_or(batch_owner);
}

bool MeshDummyCore::issueDma(const DecodedCommand &command, uint32_t generation)
{
    const DmaCommandState *existing = dma_lifecycle.find(command.command_id);
    if (existing != nullptr && existing->generation != generation) {

        fatal_if(!existing->pending.empty(),
                 "REPEAT generation %u re-admits DMA command %u with pending "
                 "descriptors",
                 generation, command.command_id);
        dma_lifecycle.evict(command.command_id);
    }
    if (!dma_lifecycle.admitted(command.command_id)) {
        const std::vector<uint32_t> *group =
            admission->dma().commandDescriptors(command.command_id);
        fatal_if(group == nullptr || group->empty(),
                 "DMA command %u has no admitted descriptor group",
                 command.command_id);
        if (!tryPinDmaAllocations(command))
            return false;
        const DmaCompletionFact *completion =
            admission->dma().descriptorCompletion(group->front());
        DmaCommandLifecycle::Admission entry;
        entry.command_id = command.command_id;
        entry.generation = generation;
        entry.completion_event =
            completion ? completion->completionEventId() : 0;
        entry.descriptors = *group;
        const P2PTransferFact *transfer = nullptr;
        for (uint32_t descriptor_id : *group) {
            const DecodedDmaDescriptor *descriptor =
                descriptorById(descriptor_id);
            if (descriptor == nullptr ||
                descriptor->kind != mesh_abi::kDmaKindP2P_PUSH)
                continue;
            transfer = admission->dma().p2pTransfer(descriptor->transfer_id);
            if (transfer != nullptr) {
                entry.transfer_id = descriptor->transfer_id;
                break;
            }
        }
        if (transfer != nullptr)
            entry.expected_bytes = transfer->expectedBytes();
        if (transfer != nullptr) {
            const size_t writes = admission->geometry().accessCount(
                command.source_op_id, GeometryAccessRole::Write);
            for (size_t index = 0; index < writes; index++) {
                const GeometryAccessFact *fact = admission->geometry().access(
                    command.source_op_id, GeometryAccessRole::Write, index);
                if (fact == nullptr)
                    continue;
                const mesh_abi::Allocation *destination =
                    admission->backing().local(fact->object().object_id);
                if (destination == nullptr)
                    continue;
                entry.receiver_core = fact->object().owner_core;
                entry.receiver_allocations.push_back(
                    destination->allocation_id);
            }
        }
        dma_lifecycle.admit(entry);
        if (dispatcher != nullptr && entry.transfer_id != 0 &&
            dispatcher->receiverFault() == "receive_premature") {
            for (uint32_t allocation_id : entry.receiver_allocations)
                dispatcher->markReceiverAllocationValid(entry.receiver_core,
                                                        allocation_id);
        }
        commandsIssued++;
        observations.recordCommandAdmission(
            CommandGeneration{command.command_id, generation}, curTick());
        live_commands++;
        live_per_stream[command.stream_id]++;
    }
    if (!dma_lifecycle.submitting(command.command_id, generation))
        return true;
    while (uint32_t descriptor_id =
               dma_lifecycle.nextDescriptor(command.command_id)) {
        const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
        fatal_if(descriptor == nullptr,
                 "admitted descriptor %u of command %u is missing",
                 descriptor_id, command.command_id);
        const auto owner = armed_binding.invocation() ? dmaOwner(*descriptor) : ServingMemberIdentity{};
        if (descriptor->kind == mesh_abi::kDmaKindLOCAL_FILL) {
            const auto *content = runtime_fill_inputs ?
                runtime_fill_inputs->contentFor(commandKey(command.command_id)) : nullptr;
            if (content != nullptr)
                dma->bindFillContent(commandKey(command.command_id), *content, true);
            const DecodedAttr *attr = attrOf(command);
            fatal_if(attr == nullptr || attr->kind != mesh_abi::kAttrKindFILL_V1,
                     "DMA_FILL command %u missing FILL_V1 attr", command.command_id);
            fatal_if(attr->as<mesh_abi::FillV1>()->pattern ==
                         mesh_abi::kDmaFillRuntimeBoundSentinel && content == nullptr,
                     "E_BINDING_ROLE: runtime fill binding missing for command %u",
                     command.command_id);
            dma->bindFillPattern(
                commandKey(command.command_id),
                attr->as<mesh_abi::FillV1>()->pattern);
        }
        if (descriptor->useful_bytes > 0 && !dmaSramAdmissible(*descriptor))
            return false;
        if (!dma->submit(*descriptor, descriptorKey(descriptor_id),
                         commandKey(command.command_id),
                         eventKey(descriptor->completion_event), curTick()))
            return false;
        dma_lifecycle.noteSubmitted(command.command_id, descriptor_id);
        if (dma_observer != nullptr)
            dma_observer->onDmaAccepted(descriptor_id, command.command_id,
                                        instance_generation.value(), owner.request_id,
                                        owner.generation, curTick());
        if (descriptor->kind == mesh_abi::kDmaKindP2P_PUSH &&
            descriptor->useful_bytes > 0 && dispatcher)
            dispatcher->armTransferExpectation(descriptor->transfer_id);
        outstanding_axi++;
        const uint64_t tag = next_dma_tag++;
        live_dma_tags.insert(tag);
        dma_tag_info[tag] = DmaTagInfo{descriptor->kind,
                                       descriptor->dst.memory_space};
        dma_tag_command[tag] = commandKey(command.command_id);
        dma_descriptor_tag[descriptor_id] = tag;
    }
    return true;
}

void MeshDummyCore::onDmaFault(RuntimeObjectKey descriptor, RuntimeObjectKey command,
                              Tick tick, bool descriptor_drained, DmaStatus status)
{
    if (command.domain == mesh_abi::MeshObjectDomain::WEIGHT_FILL) {
        const auto pending = pending_cache_fills.find(command.ordinal);
        fatal_if(!weight_cache || pending == pending_cache_fills.end() || !dispatcher,
                 "cache DMA error has no frozen fill owner");
        auto source = weightFillSource(pending->second.fill);
        source.error_class = mesh_abi::kMoeErrorClassDMA_AXI;
        dispatcher->queueCacheFill(*weight_cache, pending->second.fill,
            ErrorCandidate{tick, source, uint32_t(agent_abi::E_AXI_RESPONSE)},
            std::nullopt, descriptor_drained);
        return;
    }
    const auto error_class = status == DmaStatus::INDEX_OUT_OF_RANGE
        ? mesh_abi::kMoeErrorClassCOMMAND : mesh_abi::kMoeErrorClassDMA_AXI;
    const auto detail = uint32_t(status == DmaStatus::INDEX_OUT_OF_RANGE
        ? agent_abi::E_DCORE_INDEX_BOUNDS : agent_abi::E_AXI_RESPONSE);
    const auto source = status == DmaStatus::INDEX_OUT_OF_RANGE ?
        runtimeErrorSource(error_class, core_id_value, command, errorGeneration(command)) :
        descriptor.domain == mesh_abi::MeshObjectDomain::STATIC ?
        armed_binding.kvDescriptorSource(descriptor.ordinal, error_class,
            core_id_value, descriptor.instance.value()).value_or(
                runtimeErrorSource(error_class, core_id_value, descriptor, errorGeneration(command))) :
        runtimeErrorSource(error_class, core_id_value, descriptor, errorGeneration(command));
    latchInstanceError({tick, source, detail});
}

void MeshDummyCore::onDmaCompleted(RuntimeObjectKey descriptor_key, RuntimeObjectKey command,
                                   RuntimeObjectKey completion_event,
                                   Tick commit_tick, DmaStatus status)
{
    if (dma_observer != nullptr && command.domain == mesh_abi::MeshObjectDomain::STATIC) {
        for (const auto &descriptor : program->transport.dma_descriptors) {
            if (descriptor.descriptor_id != descriptor_key.ordinal)
                continue;
            const auto identity = frozenDescriptorKey(command.ordinal, descriptor_key.ordinal);
            const auto *execution = identity ? observations.findDescriptor(*identity) : nullptr;
            fatal_if(!execution || !execution->completed || !execution->has_transfer ||
                     !execution->has_status || execution->status != status,
                     "DMA observer has no matching execution retirement");
            const uint64_t committed = execution->transfer.committedBytes();
            const uint32_t error_code = status != DmaStatus::OK && status != DmaStatus::SUPPRESSED
                ? static_cast<uint32_t>(status) : 0;
            const auto &segments = execution->transfer.committed_segments;
            const auto owner = armed_binding.invocation() ? dmaOwner(descriptor) : ServingMemberIdentity{};
            dma_observer->onDmaTerminal(descriptor.descriptor_id,
                                        command.ordinal, committed,
                                        error_code,
                                        instance_generation.value(),
                                        owner.request_id,
                                        owner.generation,
                                        commit_tick, segments);
            break;
        }
    }
    if (command.domain == mesh_abi::MeshObjectDomain::WEIGHT_FILL) {
        if (weight_cache == nullptr)
            fatal("cache fill completion without an installed cache");
        const auto entry = pending_cache_fills.find(command.ordinal);
        if (entry == pending_cache_fills.end()) {

            if (instanceErrored())
                return;
            fatal("cache fill completion without a live fill");
        }
        const PendingCacheFill pending = entry->second;
        if (status == DmaStatus::OK) {
            fatal_if(!dispatcher, "cache fill has no scenario edge owner");
            dispatcher->queueCacheFill(*weight_cache, pending.fill, std::nullopt, pending.bytes, true);
            DPRINTF(AiMesh, "core %u fill done tag=%u bytes=%llu\n",
                    core_id_value, pending.fill.weight_tag_index,
                    (unsigned long long)pending.bytes);
            cache_fills_completed++;
            pending_cache_fills.erase(entry);

            if (instanceErrored())
                weight_cache->releaseBatchTokens(cache_batch_id);
            return;
        }
        onDmaFault(descriptor_key, command, commit_tick, true, status);
        pending_cache_fills.erase(entry);
        cache_fills_errored++;
        return;
    }
    if (command.domain == mesh_abi::MeshObjectDomain::MOE_OVERLAY) {
        if (status != DmaStatus::OK && status != DmaStatus::SUPPRESSED)
            onDmaFault(descriptor_key, command, commit_tick, true, status);
        const auto executor = overlay_executors.find(command.regionGroupId);
        fatal_if(executor == overlay_executors.end(),
                 "overlay DMA completion for an unknown layer");
        executor->second->completeDma(command, uint8_t(status));
        return;
    }
    fatal_if(command.instance != instance_generation || descriptor_key.instance != instance_generation ||
             command.domain != mesh_abi::MeshObjectDomain::STATIC ||
             descriptor_key.domain != mesh_abi::MeshObjectDomain::STATIC,
             "DMA completion leaves its active invocation");
    const uint32_t command_id = command.ordinal;
    const uint32_t descriptor_id = descriptor_key.ordinal;
    if (outstanding_axi > 0)
        outstanding_axi--;
    {
        auto tag_it = dma_descriptor_tag.find(descriptor_id);
        fatal_if(tag_it == dma_descriptor_tag.end(),
                 "DMA completion without a live tag (descriptor %u)",
                 descriptor_id);
        const uint64_t retired_tag = tag_it->second;
        live_dma_tags.erase(retired_tag);
        dma_tag_info.erase(retired_tag);
        dma_tag_command.erase(retired_tag);
        dma_descriptor_tag.erase(tag_it);
        if (dispatcher)
            dispatcher->onDmaTagRetired(core_id_value, retired_tag);
    }
    const DmaCommandState *state = dma_lifecycle.find(command_id);
    fatal_if(state == nullptr, "DMA completion for unknown command %u",
             command_id);
    if (state->terminal)
        return;
    const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
    const uint64_t retired_bytes =
        descriptor == nullptr ? 0 : descriptor->useful_bytes;
    const uint16_t peer_core =
        descriptor == nullptr ? 0 : descriptor->dst.owner_core;
    DmaCommandLifecycle::Retirement retired = dma_lifecycle.retire(
        command_id, descriptor_id, retired_bytes, status,
        peer_core);
    fatal_if(!retired.known, "DMA completion for unknown command %u",
             command_id);
    if (retired.has_transfer)
        observations.recordTransferProgress(retired.progress);
    if (retired.notify)
        notifyPeerCommit(peer_core, transferKey(retired.transfer_id));
    if (retired.first_failure) {
        if (!state->error_published) {
            dma_lifecycle.markErrorPublished(command_id);
            onDmaFault(descriptor_key, command, commit_tick, true, status);
            auto *event = new CompletionEvent(this, command, RuntimeObjectKey(), true);
            schedule(event, nextCoreEdge(*this));
        }
    }
    if (!retired.drained)
        return;
    finalizeDmaCommand(command_id);
}

void MeshDummyCore::finalizeDmaCommand(uint32_t command_id)
{
    const DmaCommandState *current = dma_lifecycle.find(command_id);
    fatal_if(current == nullptr, "finalize of unadmitted DMA command %u",
             command_id);
    if (current->terminal)
        return;
    const bool failed = current->failed;
    const bool cancelled = current->cancelled;
    dma_lifecycle.finish(command_id);
    const DecodedCommand *command = nullptr;
    for (const auto &candidate : program->transport.commands)
        if (candidate.command_id == command_id) {
            command = &candidate;
            break;
        }
    if (command)
        unpinDmaAllocations(*command);
    if (failed) {

        return;
    }
    if (cancelled) {

        commandsCancelled++;
        recordTerminal(command_id, issuedGeneration(command_id),
                       TerminalState::Cancelled, curTick());
        live_commands--;
        if (command && live_per_stream.count(command->stream_id) &&
            live_per_stream[command->stream_id])
            live_per_stream[command->stream_id]--;
        return;
    }
    if (command && !current->suppressed)
        markOperandValid(*command);

    auto *event = new CompletionEvent(this, commandKey(command_id),
                                      eventKey(current->completion_event), false);
    event->suppressed_terminal = current->suppressed;
    schedule(event, nextCoreEdge(*this));
}

void MeshDummyCore::cancelDmaCommand(uint32_t command_id)
{
    if (!dma_lifecycle.admitted(command_id))
        return;
    const DmaCommandState *state = dma_lifecycle.find(command_id);
    if (state->terminal || state->failed)
        return;
    if (!dma_lifecycle.cancel(command_id))
        return;
    finalizeDmaCommand(command_id);
}

bool MeshDummyCore::dmaDrained() const
{
    if (outstanding_axi != 0 || !live_dma_tags.empty())
        return false;
    if (dma != nullptr && !dma->idle())
        return false;
    return dma_lifecycle.drained();
}

const DecodedDmaDescriptor *
MeshDummyCore::descriptorById(uint32_t descriptor_id) const
{
    if (program == nullptr)
        return nullptr;
    for (const auto &descriptor : program->transport.dma_descriptors)
        if (descriptor.descriptor_id == descriptor_id)
            return &descriptor;
    return nullptr;
}

std::optional<DestinationStorageObservation>
MeshDummyCore::admittedDestinationStorage(uint32_t descriptor_id) const
{
    if (admission == nullptr || program == nullptr)
        return std::nullopt;
    const DecodedDmaDescriptor *descriptor = descriptorById(descriptor_id);
    if (descriptor == nullptr)
        return std::nullopt;
    uint32_t operation_id = 0;
    bool resolved = false;
    for (const DecodedCommand &command : program->transport.commands)
        if (command.command_id == descriptor->command_id) {
            operation_id = command.source_op_id;
            resolved = true;
            break;
        }
    if (!resolved)
        return std::nullopt;
    const GeometryAccessFact *fact = admission->geometry().access(
        operation_id, GeometryAccessRole::Write, 0);
    if (fact == nullptr)
        return std::nullopt;
    const mesh_abi::Allocation *allocation =
        admission->backing().local(fact->object().object_id);
    if (allocation == nullptr)
        return std::nullopt;
    DestinationStorageObservation storage;
    storage.allocation_id = allocation->allocation_id;
    storage.base = admittedEndpointAddress(descriptor_id, false) -
                   descriptor->dst.offset_bytes;
    storage.bytes = allocation->size_bytes;
    return storage;
}

uint64_t
MeshDummyCore::dmaEndpointAddress(const DecodedDmaDescriptor &descriptor,
                                  const RuntimeObjectKey &key, bool source) const
{
    fatal_if(!invocation || key.instance != instance_generation,
             "DMA endpoint has no matching active invocation");
    std::array<uint64_t, 2> addresses;
    MeshLoadError error;
    fatal_if(!invocation->dmaAddresses(descriptor, key, addresses, error),
             "%s: %s", error.code, error.message);
    return addresses[source ? 0 : 1];
}

uint64_t
MeshDummyCore::dmaLocalOffset(const DecodedDmaDescriptor &descriptor,
                              const RuntimeObjectKey &key, bool source) const
{
    const auto &endpoint = source ? descriptor.src : descriptor.dst;
    const auto *region = admission->arch().region(endpoint.region_id);
    fatal_if(!region || !region->perCore() || endpoint.owner_core != core_id_value,
             "DMA local endpoint does not name its owning tile");
    bool overflow = false;
    const auto base = region->tileBase(core_id_value, overflow);
    const auto address = dmaEndpointAddress(descriptor, key, source);
    fatal_if(overflow || address < base, "DMA local endpoint precedes its tile");
    return address - base;
}

uint64_t
MeshDummyCore::admittedEndpointAddress(uint32_t descriptor_id, bool source) const
{
    fatal_if(!invocation,
             "core %u has no resolved invocation for descriptor %u address",
             core_id_value, descriptor_id);
    return invocation->binding().endpointAddress(descriptor_id, source);
}

uint64_t
MeshDummyCore::admittedLocalOffset(uint32_t descriptor_id, bool source) const
{
    const auto *descriptor = descriptorById(descriptor_id);
    fatal_if(!descriptor, "local DMA offset has no admitted descriptor");
    return dmaLocalOffset(*descriptor, descriptorKey(descriptor_id), source);
}

bool
MeshDummyCore::transferCommitted(uint32_t transfer_id) const
{
    const auto found = committed_transfers.find(transferKey(transfer_id));
    return found != committed_transfers.end() && found->second;
}

uint32_t
MeshDummyCore::receivedAllocation(uint32_t transfer_id) const
{
    const auto found = received_allocations.find(transfer_id);
    return found == received_allocations.end() ? 0 : found->second;
}

bool
MeshDummyCore::allocationValid(uint32_t allocation_id) const
{
    const auto found = sram.allocations.find(allocation_id);
    return found != sram.allocations.end() && found->second.valid;
}

uint32_t
MeshDummyCore::receiveNotificationCount(uint32_t transfer_id) const
{
    const auto found = receive_notifications.find(transfer_id);
    return found == receive_notifications.end() ? 0 : found->second;
}

Tick
MeshDummyCore::receiveNotificationTick(uint32_t transfer_id) const
{
    const auto found = receive_notification_ticks.find(transfer_id);
    return found == receive_notification_ticks.end() ? 0 : found->second;
}

}

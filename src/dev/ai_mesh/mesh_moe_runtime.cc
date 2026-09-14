#include "dev/ai_mesh/mesh_moe_runtime.hh"


#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool isDmaOpcode(uint16_t opcode)
{
    switch (opcode) {
    case mesh_abi::kOpcodeDMA_LOAD:
    case mesh_abi::kOpcodeDMA_STORE:
    case mesh_abi::kOpcodeDMA_P2P_PUSH:
    case mesh_abi::kOpcodeDMA_FILL:
    case mesh_abi::kOpcodeRECV_WAIT:
        return true;
    default:
        return false;
    }
}

bool isComputeOpcode(uint16_t opcode)
{
    return opcode == mesh_abi::kOpcodeGEMM || opcode == mesh_abi::kOpcodeBMM ||
           opcode == mesh_abi::kOpcodeLOCAL_REDUCE;
}

} // anonymous namespace

bool MoeRuntimeCommand::isDma() const { return isDmaOpcode(opcode); }

bool MoeRuntimeCommand::isCompute() const { return isComputeOpcode(opcode); }

bool MoeRuntimeCommand::isTransfer() const
{
    return opcode == mesh_abi::kOpcodeDMA_P2P_PUSH ||
           opcode == mesh_abi::kOpcodeRECV_WAIT;
}

void MoeOverlayEventBus::publish(uint16_t core_id,
                                 const RuntimeObjectKey &event) const
{
    auto it = executors.find(core_id);
    if (it != executors.end() && it->second != nullptr)
        it->second->deliverEvent(event);
}

MoeOverlayExecutor::MoeOverlayExecutor(const Config &config,
                                       ProgramScoreboard *scoreboard,
                                       TensorSram *sram,
                                       const MoeOverlayEventBus *bus,
                                       const MoeOverlayAddressSpace *addresses)
    : config(config), scoreboard(scoreboard), sram(sram), bus(bus),
      addresses(addresses)
{
}

void MoeOverlayExecutor::reset()
{
    commands.clear();
    live.clear();
    finished.clear();
    signal_log.clear();
    completion_log.clear();
    now = 0;
    issued_commands = 0;
    completed_commands = 0;
    sram_read_bytes = 0;
    sram_write_bytes = 0;
    sram_service_cycles = 0;
    sram_bank_conflicts = 0;
    compute_cycles = 0;
    compute_commits = 0;
    copy_through_commands = 0;
    local_reduce_commands = 0;
    committed_views.clear();
    sram_region_bytes.clear();
    sram_region_kind_bytes.clear();
    failed_value = false;
    failure_status = 0;
    failed_command = RuntimeObjectKey();
    started_value = false;
    loaded = false;
    live_dma.clear();
    descriptors.clear();
    views.clear();
    graph_ref = nullptr;
    failure_reason.clear();
}

RuntimeObjectKey MoeOverlayExecutor::eventKey(
    const MoeOverlayEntry &entry) const
{
    RuntimeObjectKey key;
    key.instance = instance_value;
    key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    key.regionGroupId = layer_id_value;
    key.regionId = entry.region_id;
    key.kind = mesh_abi::MeshObjectKind::EVENT;
    key.ordinal = entry.ordinal;
    return key;
}

void MoeOverlayExecutor::load(uint32_t layer_id, InstanceGeneration instance,
                              const MoeOverlayGraph &graph)
{
    fatal_if(loaded, "overlay executor already loaded");
    fatal_if(scoreboard == nullptr, "overlay executor has no scoreboard");
    layer_id_value = layer_id;
    instance_value = instance;
    graph_ref = &graph;
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindCOMMAND)
            continue;
        if (entry.owner_core != config.core_id)
            continue;
        MoeRuntimeCommand command;
        command.key.instance = instance;
        command.key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
        command.key.regionGroupId = layer_id;
        command.key.regionId = entry.region_id;
        command.key.kind = mesh_abi::MeshObjectKind::COMMAND;
        command.key.ordinal = entry.ordinal;
        command.opcode = entry.secondary_kind;
        command.phase = entry.phase;
        command.role = entry.role;
        command.owner_core = entry.owner_core;
        command.src_core = entry.src_core;
        command.dst_core = entry.dst_core;
        command.expert_id = entry.expert_id;
        command.chunk_ordinal = entry.chunk_ordinal;
        command.payload_bytes = entry.bytes;
        command.descriptor_region = entry.ref_region;
        command.descriptor_ordinal = entry.ref_ordinal;
        fatal_if(entry.wait_count > MoeOverlayEntry::kMaxRefs,
                 "overlay command has too many wait refs");
        fatal_if(entry.signal_count > MoeOverlayEntry::kMaxRefs,
                 "overlay command has too many signal refs");
        for (uint32_t index = 0; index < entry.wait_count; index++) {
            RuntimeObjectKey key;
            key.instance = instance;
            key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
            key.regionGroupId = layer_id;
            key.regionId = entry.wait_refs[index][0];
            key.kind = mesh_abi::MeshObjectKind(entry.wait_refs[index][1]);
            key.ordinal = entry.wait_refs[index][2];
            command.waits.push_back(key);
        }
        for (uint32_t index = 0; index < entry.signal_count; index++) {
            RuntimeObjectKey key;
            key.instance = instance;
            key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
            key.regionGroupId = layer_id;
            key.regionId = entry.signal_refs[index][0];
            key.kind = mesh_abi::MeshObjectKind(entry.signal_refs[index][1]);
            key.ordinal = entry.signal_refs[index][2];
            command.signals.push_back(key);
        }
        for (uint32_t index = 0; index < entry.view_count; index++) {
            RuntimeObjectKey key;
            key.instance = instance;
            key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
            key.regionGroupId = layer_id;
            key.regionId = entry.view_refs[index][0];
            key.kind = mesh_abi::MeshObjectKind(entry.view_refs[index][1]);
            key.ordinal = entry.view_refs[index][2];
            command.view_refs.push_back(key);
        }
        commands.push_back(command);
    }
    for (const auto &entry : graph.objects()) {
        if (entry.kind == mesh_abi::kMeshObjectKindDESCRIPTOR)
            descriptors[std::make_pair(entry.region_id, entry.ordinal)] =
                &entry;
        if (entry.kind == mesh_abi::kMeshObjectKindVIEW)
            views[std::make_pair(entry.region_id, entry.ordinal)] = &entry;
    }
    std::sort(commands.begin(), commands.end(),
              [](const MoeRuntimeCommand &left,
                 const MoeRuntimeCommand &right) {
                  return left.key < right.key;
              });
    loaded = true;
}

void MoeOverlayExecutor::publishEntry(const RuntimeObjectKey &event,
                                      uint64_t core_tick)
{
    fatal_if(!loaded, "overlay executor has no program");
    started_value = true;
    now = core_tick;
    scoreboard->set_visible(event);
}

void MoeOverlayExecutor::deliverEvent(const RuntimeObjectKey &event)
{
    scoreboard->set_visible(event);
}

std::string MoeOverlayExecutor::describeBlocked() const
{
    std::string out = "issued " + std::to_string(issued_commands) +
                      " done " + std::to_string(completed_commands) +
                      " live " + std::to_string(live.size()) +
                      " tick " + std::to_string(now);
    for (const auto &entry : live) {
        out += " running r" + std::to_string(entry.command->key.regionId) +
               ":" + std::to_string(entry.command->key.ordinal) + " op" +
               std::to_string(entry.command->opcode) +
               (entry.armed ? " armed@" + std::to_string(entry.complete_at)
                            : " unarmed");
    }
    for (const auto &command : commands) {
        if (finished.count(command.key) != 0)
            continue;
        out += " pending r" + std::to_string(command.key.regionId) + ":" +
               std::to_string(command.key.ordinal) + " waits";
        for (const auto &wait : command.waits) {
            out += " " + std::to_string(wait.regionId) + "/" +
                   std::to_string(uint32_t(wait.kind)) + "/" +
                   std::to_string(wait.ordinal) +
                   (scoreboard->visible(wait) ? "+" : "-");
        }
        break;
    }
    return out;
}

const MoeRuntimeCommand *MoeOverlayExecutor::readyCommand() const
{
    for (const auto &command : commands) {
        if (finished.count(command.key) != 0)
            continue;
        bool waiting = false;
        for (const auto &wait : command.waits)
            if (!scoreboard->visible(wait))
                waiting = true;
        if (waiting)
            continue;
        bool already_live = false;
        for (const auto &live_entry : live)
            if (live_entry.command->key == command.key)
                already_live = true;
        if (already_live)
            continue;
        if (command.isCompute() && compute != nullptr &&
            !compute->computeAdmissible(command.opcode))
            continue;
        return &command;
    }
    return nullptr;
}

MoeComputeShape
MoeOverlayExecutor::computeShape(const MoeRuntimeCommand &command) const
{
    MoeComputeShape shape;
    shape.layer_id = layer_id_value;
    shape.opcode = command.opcode;
    shape.role = command.role;
    shape.payload_bytes = command.payload_bytes;
    if (command.role == mesh_abi::kMoeCommandRoleCOPY_THROUGH ||
        command.role == mesh_abi::kMoeCommandRoleLOCAL_REDUCE) {
        // Combine commands wait on exactly one producer per accepted expert
        // row; the entry event is the only non-contributor wait.
        fatal_if(command.waits.empty(),
                 "overlay combine command (region %u ordinal %u) has no "
                 "contributor",
                 command.key.regionId, command.key.ordinal);
        shape.fan_in = uint32_t(command.waits.size()) - 1;
        fatal_if(shape.fan_in == 0,
                 "overlay combine command (region %u ordinal %u) resolves "
                 "zero contributors",
                 command.key.regionId, command.key.ordinal);
    }
    return shape;
}

uint64_t
MoeOverlayExecutor::serviceCycles(const MoeRuntimeCommand &command) const
{
    // The frozen ABI expresses one service window in 32 bits; anything above
    // that is unschedulable and must be rejected instead of wrapping.
    constexpr uint64_t kMaxServiceCycles = (uint64_t(1) << 32) - 1;
    if (command.role == mesh_abi::kMoeCommandRoleREGION_TERMINAL ||
        command.role == mesh_abi::kMoeCommandRoleEMPTY_REGION_TERMINAL ||
        command.role == mesh_abi::kMoeCommandRoleGROUP_EXIT)
        return uint64_t(config.exit_signal_cycles);
    if (command.isCompute()) {
        const MoeComputeShape shape = computeShape(command);
        uint64_t cycles = 0;
        if (compute != nullptr) {
            cycles = compute->computeCycles(shape);
        } else {
            const uint64_t rows = config.compute_row_bytes == 0
                                      ? 1
                                      : (shape.payload_bytes /
                                         config.compute_row_bytes);
            cycles = uint64_t(config.compute_cycles_per_row) * rows;
        }
        fatal_if(cycles > kMaxServiceCycles,
                 "E_ABI_OVERFLOW: overlay compute command (region %u ordinal "
                 "%u) needs %llu cycles, above the schedulable 32-bit service "
                 "window",
                 command.key.regionId, command.key.ordinal,
                 (unsigned long long)cycles);
        return cycles;
    }
    const uint64_t bytes = command.payload_bytes;
    const uint64_t cycles =
        (bytes + config.dma_bytes_per_cycle - 1) / config.dma_bytes_per_cycle +
        config.dma_setup_cycles;
    fatal_if(cycles > kMaxServiceCycles,
             "E_ABI_OVERFLOW: overlay DMA command (region %u ordinal %u) "
             "needs %llu cycles, above the schedulable 32-bit service window",
             command.key.regionId, command.key.ordinal,
             (unsigned long long)cycles);
    return cycles;
}

const MoeOverlayEntry *
MoeOverlayExecutor::descriptorOf(const MoeRuntimeCommand &command) const
{
    if (command.descriptor_ordinal == 0)
        return nullptr;
    const auto it = descriptors.find(
        std::make_pair(command.descriptor_region, command.descriptor_ordinal));
    return it == descriptors.end() ? nullptr : it->second;
}

RuntimeObjectKey
MoeOverlayExecutor::viewKey(uint32_t region_id, uint32_t ordinal) const
{
    RuntimeObjectKey key;
    key.instance = instance_value;
    key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    key.regionGroupId = uint16_t(layer_id_value);
    key.regionId = uint16_t(region_id);
    key.kind = mesh_abi::MeshObjectKind::VIEW;
    key.ordinal = ordinal;
    return key;
}

const MoeOverlayEntry *
MoeOverlayExecutor::viewOf(const RuntimeObjectKey &ref) const
{
    const auto it = views.find(std::make_pair(ref.regionId, ref.ordinal));
    return it == views.end() ? nullptr : it->second;
}

bool MoeOverlayExecutor::resolveView(const MoeOverlayEntry &view,
                                     mesh_abi::DmaEndpoint &endpoint,
                                     uint64_t &byte_count) const
{
    endpoint = mesh_abi::DmaEndpoint();
    endpoint.tensor_id = 0;
    endpoint.shard_id = 0;
    endpoint.owner_core = uint16_t(view.owner_core);
    byte_count = view.bytes;
    if (view.backing_kind == mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION) {
        const auto *interval =
            graph_ref->resolveAllocation(view.ref_region, view.ref_ordinal);
        if (interval == nullptr)
            return false;
        if (view.offset + view.bytes > interval->bytes)
            return false;
        endpoint.memory_space =
            uint16_t(view.owner_core == config.core_id
                         ? mesh_abi::kMemorySpaceCORE_SRAM
                         : mesh_abi::kMemorySpacePEER_SRAM);
        endpoint.region_id = addresses == nullptr ? 0 : addresses->sram_region;
        endpoint.offset_bytes = interval->offset + view.offset;
        return true;
    }
    if (view.backing_kind == mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT) {
        if (compute == nullptr)
            return false;
        uint64_t base = 0;
        uint64_t capacity = 0;
        if (!compute->weightSlotRange(view.ref_ordinal, base, capacity))
            return false;
        if (view.offset + view.bytes > capacity)
            return false;
        endpoint.memory_space = mesh_abi::kMemorySpaceCORE_SRAM;
        endpoint.region_id = addresses == nullptr ? 0 : addresses->sram_region;
        endpoint.offset_bytes = base + view.offset;
        return true;
    }
    if (view.backing_kind == mesh_abi::kMoeViewBackingSTATIC_ALLOCATION) {
        if (addresses == nullptr)
            return false;
        const auto it = addresses->allocations.find(view.ref_ordinal);
        if (it == addresses->allocations.end())
            return false;
        if (view.offset + view.bytes > it->second.second)
            return false;
        endpoint.memory_space = mesh_abi::kMemorySpaceCORE_SRAM;
        endpoint.region_id = addresses == nullptr ? 0 : addresses->sram_region;
        endpoint.offset_bytes = it->second.first + view.offset;
        return true;
    }
    return false;
}

MoeOverlayExecutor::DmaIssue
MoeOverlayExecutor::submitDma(const MoeRuntimeCommand &command)
{
    const MoeOverlayEntry *descriptor = descriptorOf(command);
    if (descriptor == nullptr || dma == nullptr) {
        failure_reason = "descriptor missing (ref " +
                         std::to_string(command.descriptor_region) + ":" +
                         std::to_string(command.descriptor_ordinal) + ")";
        return DmaIssue::UNRESOLVABLE;
    }
    if (addresses == nullptr)
        return DmaIssue::UNRESOLVABLE;
    const bool is_fill = command.opcode == mesh_abi::kOpcodeDMA_FILL;
    const bool is_load = command.opcode == mesh_abi::kOpcodeDMA_LOAD;
    const bool is_push = command.opcode == mesh_abi::kOpcodeDMA_P2P_PUSH;
    if (!is_fill && !is_load && !is_push)
        return DmaIssue::UNRESOLVABLE;
    mesh_abi::DmaDescriptor wire;
    wire.command_id = command.key.ordinal;
    wire.transfer_id = 0;
    wire.owner_core = uint16_t(config.core_id);
    wire.kind = is_fill ? mesh_abi::kDmaKindLOCAL_FILL
                        : mesh_abi::kDmaKindLOAD;
    wire.rows = 1;
    wire.row_bytes = descriptor->bytes;
    wire.src_stride_bytes = descriptor->bytes;
    wire.dst_stride_bytes = descriptor->bytes;
    wire.useful_bytes = descriptor->bytes;
    wire.physical_storage_bytes = descriptor->bytes;
    wire.completion_event = 0;
    wire.max_burst_beats =
        addresses == nullptr ? 0 : addresses->max_burst_beats;
    uint64_t target_bytes = 0;
    if (is_fill) {
        const MoeOverlayEntry *target =
            viewOf(viewKey(descriptor->dst_view_region,
                           descriptor->dst_view_ordinal));
        if (target == nullptr) {
            failure_reason = "fill target view missing";
            return DmaIssue::UNRESOLVABLE;
        }
        if (!resolveView(*target, wire.dst, target_bytes)) {
            failure_reason = "fill target view unresolvable";
            return DmaIssue::UNRESOLVABLE;
        }
        wire.src = wire.dst;
        wire.descriptor_id = descriptor->ordinal;
        const auto payload = graph_ref->resolvePayload(descriptor->region_id,
                                                       descriptor->ordinal);
        fatal_if(payload.first == nullptr && descriptor->bytes > 0,
                 "fill descriptor %u:%u carries no contract content",
                 descriptor->region_id, descriptor->ordinal);
        const std::vector<uint8_t> content(payload.first,
                                           payload.first + payload.second);
        dma->bindFillContent(command.key, content,
                             graph_ref->fillMode() == 0);
        if (!dma->submitOverlayDma(wire, overlayDescriptorKey(*descriptor),
                                   command.key, now))
            return DmaIssue::BACKPRESSURE;
        live_dma.insert(command.key);
        return DmaIssue::SUBMITTED;
    }
    if (is_push)
        return submitPush(command, *descriptor, wire);
    const uint16_t expert = uint16_t(descriptor->expert_id);
    if (addresses == nullptr) {
        failure_reason = "no address space";
        return DmaIssue::UNRESOLVABLE;
    }
    const auto weight = addresses->expert_weights.find(expert);
    if (weight == addresses->expert_weights.end()) {
        failure_reason = "expert " + std::to_string(expert) +
                         " has no weight binding";
        return DmaIssue::UNRESOLVABLE;
    }
    const MoeOverlayEntry *target =
        viewOf(viewKey(descriptor->dst_view_region,
                       descriptor->dst_view_ordinal));
    if (target == nullptr) {
        failure_reason = "weight view " +
                         std::to_string(descriptor->dst_view_region) + ":" +
                         std::to_string(descriptor->dst_view_ordinal) +
                         " missing";
        return DmaIssue::UNRESOLVABLE;
    }
    if (!resolveView(*target, wire.dst, target_bytes)) {
        failure_reason = "weight view unresolvable";
        return DmaIssue::UNRESOLVABLE;
    }
    if (target_bytes < weight->second.second) {
        failure_reason = "weight view smaller than the bound slice";
        return DmaIssue::UNRESOLVABLE;
    }
    wire.src.memory_space = mesh_abi::kMemorySpaceHBM;
    wire.src.owner_core = 0;
    wire.src.tensor_id = 0;
    wire.src.shard_id = 0;
    wire.src.region_id = addresses->hbm_region;
    wire.src.offset_bytes = weight->second.first;
    wire.row_bytes = weight->second.second;
    wire.src_stride_bytes = weight->second.second;
    wire.dst_stride_bytes = weight->second.second;
    wire.useful_bytes = weight->second.second;
    wire.physical_storage_bytes = weight->second.second;
    wire.descriptor_id = descriptor->ordinal;
    if (!dma->submitOverlayDma(wire, overlayDescriptorKey(*descriptor),
                               command.key, now))
        return DmaIssue::BACKPRESSURE;
    live_dma.insert(command.key);
    return DmaIssue::SUBMITTED;
}

const MoeOverlayEntry *
MoeOverlayExecutor::peerDescriptor(const MoeOverlayEntry &descriptor) const
{
    for (const auto &candidate : descriptors) {
        const MoeOverlayEntry *entry = candidate.second;
        if (entry->dst_view_ordinal == 0)
            continue;
        if (entry->phase != descriptor.phase ||
            entry->expert_id != descriptor.expert_id ||
            entry->chunk_ordinal != descriptor.chunk_ordinal)
            continue;
        if (entry->owner_core == descriptor.owner_core)
            continue;
        return entry;
    }
    return nullptr;
}

MoeOverlayExecutor::DmaIssue
MoeOverlayExecutor::submitPush(const MoeRuntimeCommand &command,
                               const MoeOverlayEntry &descriptor,
                               mesh_abi::DmaDescriptor &wire)
{
    if (descriptor.src_view_ordinal == 0) {
        failure_reason = "push without a local source view";
        return DmaIssue::UNRESOLVABLE;
    }
    const MoeOverlayEntry *source =
        viewOf(viewKey(descriptor.src_view_region, descriptor.src_view_ordinal));
    const MoeOverlayEntry *peer = peerDescriptor(descriptor);
    if (peer == nullptr) {
        failure_reason = "push without a peer destination";
        return DmaIssue::UNRESOLVABLE;
    }
    const MoeOverlayEntry *target =
        viewOf(viewKey(peer->dst_view_region, peer->dst_view_ordinal));
    if (source == nullptr || target == nullptr) {
        failure_reason = "push endpoints missing";
        return DmaIssue::UNRESOLVABLE;
    }
    uint64_t source_bytes = 0;
    uint64_t target_bytes = 0;
    if (!resolveView(*source, wire.src, source_bytes) ||
        !resolveView(*target, wire.dst, target_bytes)) {
        failure_reason = "push endpoints unresolvable";
        return DmaIssue::UNRESOLVABLE;
    }
    if (wire.src.memory_space != mesh_abi::kMemorySpaceCORE_SRAM ||
        wire.dst.memory_space != mesh_abi::kMemorySpacePEER_SRAM) {
        failure_reason = "push must move local SRAM into a peer aperture";
        return DmaIssue::UNRESOLVABLE;
    }
    const uint64_t bytes = std::min<uint64_t>(descriptor.bytes, source_bytes);
    wire.kind = mesh_abi::kDmaKindP2P_PUSH;
    wire.rows = 1;
    wire.row_bytes = bytes;
    wire.src_stride_bytes = bytes;
    wire.dst_stride_bytes = bytes;
    wire.useful_bytes = bytes;
    wire.physical_storage_bytes = bytes;
    wire.transfer_id = 0;
    wire.descriptor_id = descriptor.ordinal;
    if (!dma->submitOverlayDma(wire, overlayDescriptorKey(descriptor),
                               command.key, now))
        return DmaIssue::BACKPRESSURE;
    live_dma.insert(command.key);
    return DmaIssue::SUBMITTED;
}

RuntimeObjectKey
MoeOverlayExecutor::overlayDescriptorKey(const MoeOverlayEntry &entry) const
{
    RuntimeObjectKey key;
    key.instance = instance_value;
    key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    key.regionGroupId = layer_id_value;
    key.regionId = entry.region_id;
    key.kind = mesh_abi::MeshObjectKind::DESCRIPTOR;
    key.ordinal = entry.ordinal;
    return key;
}

void MoeOverlayExecutor::completeDma(const RuntimeObjectKey &command,
                                     uint8_t status)
{
    const auto pending = live_dma.find(command);
    if (pending == live_dma.end())
        return;
    live_dma.erase(pending);
    for (auto it = live.begin(); it != live.end(); ++it) {
        if (it->command->key != command)
            continue;
        if (status == 0) {
            completeCommand(*it->command, 0);
        } else {
            failure_status = status;
            failed_command = command;
            failure_reason = "overlay command r" +
                             std::to_string(command.regionId) + ":" +
                             std::to_string(command.ordinal) +
                             " failed with DMA status " +
                             std::to_string(uint32_t(status));
            live.erase(live.begin(), live.end());
            failed_value = true;
            break;
        }
        live.erase(it);
        return;
    }
}


std::vector<std::tuple<const MoeOverlayEntry *, bool, bool, uint64_t>>
MoeOverlayExecutor::localViews(const MoeRuntimeCommand &command) const
{
    std::vector<std::tuple<const MoeOverlayEntry *, bool, bool, uint64_t>> out;
    const auto accept = [&](const MoeOverlayEntry *view, bool read,
                            bool write, uint64_t bytes) {
        if (view == nullptr)
            return;
        if (view->backing_kind ==
            mesh_abi::kMoeViewBackingINSTANCE_MEMBER_BINDING)
            return;
        if (view->owner_core != config.core_id)
            return;
        out.emplace_back(view, read, write, bytes);
    };
    // One view object is shared by its producer and its consumers, so the
    // declared access cannot describe one command's direction or span: the
    // operand role of the command does.  A combine consumes one contributor
    // row per contributor even when the producer view spans several rows.
    const auto accept_operand = [&](const MoeOverlayEntry *view) {
        if (view == nullptr)
            return;
        switch (command.role) {
        case mesh_abi::kMoeCommandRoleEXPERT_COMPUTE: {
            const bool result =
                view->secondary_kind == mesh_abi::kMoeViewKindEXPERT_OUTPUT;
            accept(view, !result, result, view->bytes);
            return;
        }
        case mesh_abi::kMoeCommandRoleCOPY_THROUGH: {
            // The identity copy reads its unique gather row and writes the
            // member row plus the token's reduce output; nothing accumulates.
            const bool result =
                view->secondary_kind == mesh_abi::kMoeViewKindMEMBER_OUTPUT ||
                view->secondary_kind ==
                    mesh_abi::kMoeViewKindREDUCE_ACCUMULATOR;
            accept(view, !result, result, command.payload_bytes);
            return;
        }
        case mesh_abi::kMoeCommandRoleLOCAL_REDUCE: {
            const bool result =
                view->secondary_kind == mesh_abi::kMoeViewKindMEMBER_OUTPUT;
            const bool accumulates =
                view->secondary_kind ==
                mesh_abi::kMoeViewKindREDUCE_ACCUMULATOR;
            accept(view, !result, result || accumulates,
                   command.payload_bytes);
            return;
        }
        default:
            accept(view, view->access != mesh_abi::kMoeViewAccessWRITE,
                   view->access != mesh_abi::kMoeViewAccessREAD,
                   command.payload_bytes);
            return;
        }
    };
    if (command.isDma()) {
        const MoeOverlayEntry *descriptor = descriptorOf(command);
        if (descriptor != nullptr) {
            accept(viewOf(viewKey(descriptor->src_view_region,
                                  descriptor->src_view_ordinal)),
                   true, false, command.payload_bytes);
            accept(viewOf(viewKey(descriptor->dst_view_region,
                                  descriptor->dst_view_ordinal)),
                   false, true, command.payload_bytes);
        }
        return out;
    }
    for (const auto &ref : command.view_refs) {
        const MoeOverlayEntry *view = viewOf(ref);
        fatal_if(view == nullptr,
                 "overlay command (region %u ordinal %u) references a "
                 "missing view %u:%u",
                 command.key.regionId, command.key.ordinal, ref.regionId,
                 ref.ordinal);
        accept_operand(view);
    }
    return out;
}

void MoeOverlayExecutor::accountSramBytes(const MoeRuntimeCommand &command,
                                          const MoeOverlayEntry &view,
                                          uint64_t bytes, bool is_write)
{
    auto &ledger = sram_region_bytes[command.key.regionId];
    auto &kind = sram_region_kind_bytes[command.key.regionId]
                                      [view.secondary_kind];
    if (is_write) {
        ledger.second += bytes;
        kind.second += bytes;
        sram_write_bytes += bytes;
    } else {
        ledger.first += bytes;
        kind.first += bytes;
        sram_read_bytes += bytes;
    }
}

void MoeOverlayExecutor::accountDmaSram(const MoeRuntimeCommand &command)
{
    for (const auto &entry : localViews(command))
        accountSramBytes(command, *std::get<0>(entry), std::get<3>(entry),
                         std::get<2>(entry));
}

bool MoeOverlayExecutor::reserveComputeSram(Live &entry, uint64_t now_tick)
{
    const MoeRuntimeCommand &command = *entry.command;
    if (entry.sram_reads.empty() && entry.sram_writes.empty()) {
        for (const auto &triple : localViews(command)) {
            if (std::get<1>(triple))
                entry.sram_reads.emplace_back(std::get<0>(triple),
                                              std::get<3>(triple));
            if (std::get<2>(triple))
                entry.sram_writes.emplace_back(std::get<0>(triple),
                                               std::get<3>(triple));
        }
    }
    for (; entry.reads_done < entry.sram_reads.size(); entry.reads_done++) {
        const MoeOverlayEntry &view = *entry.sram_reads[entry.reads_done].first;
        const uint64_t bytes = entry.sram_reads[entry.reads_done].second;
        mesh_abi::DmaEndpoint endpoint;
        uint64_t view_bytes = 0;
        fatal_if(!resolveView(view, endpoint, view_bytes),
                 "overlay compute command (region %u ordinal %u) has an "
                 "unresolvable view %u:%u (kind %u backing %u ref %u:%u)",
                 command.key.regionId, command.key.ordinal, view.region_id,
                 view.ordinal, view.secondary_kind, view.backing_kind,
                 view.ref_region, view.ref_ordinal);
        fatal_if(!sram->fits(endpoint.offset_bytes, bytes),
                 "overlay compute command (region %u ordinal %u) view %u:%u "
                 "spans [%llu,%llu) outside the %llu B SRAM",
                 command.key.regionId, command.key.ordinal, view.region_id,
                 view.ordinal, (unsigned long long)endpoint.offset_bytes,
                 (unsigned long long)(endpoint.offset_bytes + bytes),
                 (unsigned long long)sram->capacity());
        const auto service = sram->tryReserve(now_tick, endpoint.offset_bytes,
                                              bytes, false);
        if (!service)
            return false;
        entry.read_done_at = std::max(entry.read_done_at,
                                      now_tick + service->stall_ticks);
        sram_service_cycles += service->service_ticks / config.ticks_per_cycle;
        sram_bank_conflicts += service->conflict_ticks / config.ticks_per_cycle;
        accountSramBytes(command, view, bytes, false);
    }
    return true;
}

bool MoeOverlayExecutor::reserveComputeWrites(Live &entry, uint64_t now_tick)
{
    const MoeRuntimeCommand &command = *entry.command;
    for (; entry.writes_done < entry.sram_writes.size(); entry.writes_done++) {
        const MoeOverlayEntry &view =
            *entry.sram_writes[entry.writes_done].first;
        const uint64_t bytes = entry.sram_writes[entry.writes_done].second;
        mesh_abi::DmaEndpoint endpoint;
        uint64_t view_bytes = 0;
        fatal_if(!resolveView(view, endpoint, view_bytes),
                 "overlay compute command (region %u ordinal %u) has an "
                 "unresolvable view %u:%u (kind %u backing %u ref %u:%u)",
                 command.key.regionId, command.key.ordinal, view.region_id,
                 view.ordinal, view.secondary_kind, view.backing_kind,
                 view.ref_region, view.ref_ordinal);
        const auto service = sram->tryReserve(now_tick, endpoint.offset_bytes,
                                              bytes, true);
        if (!service)
            return false;
        entry.write_done_at = std::max(entry.write_done_at,
                                       now_tick + service->stall_ticks);
        sram_service_cycles += service->service_ticks / config.ticks_per_cycle;
        sram_bank_conflicts += service->conflict_ticks / config.ticks_per_cycle;
        accountSramBytes(command, view, bytes, true);
    }
    return true;
}

bool MoeOverlayExecutor::armLive(Live &entry, uint64_t now_tick)
{
    const MoeRuntimeCommand &command = *entry.command;
    if (command.isDma()) {
        const bool moves_data =
            command.opcode != mesh_abi::kOpcodeRECV_WAIT &&
            command.descriptor_ordinal != 0;
        if (!moves_data || dma == nullptr) {
            accountDmaSram(command);
            entry.complete_at = now_tick + serviceCycles(command) *
                                            config.ticks_per_cycle;
            entry.armed = true;
            return true;
        }
        const DmaIssue issue = submitDma(command);
        fatal_if(issue == DmaIssue::UNRESOLVABLE,
                 "overlay DMA command (region %u ordinal %u opcode %u) has no "
                 "resolvable endpoints: %s",
                 command.key.regionId, command.key.ordinal, command.opcode,
                 failure_reason.c_str());
        if (issue != DmaIssue::SUBMITTED) {
            // Finite descriptor queue: the command keeps its place in the
            // wait graph and re-submits once the engine accepts work again.
            return false;
        }
        accountDmaSram(command);
        entry.complete_at = now_tick + serviceCycles(command) *
                                        config.ticks_per_cycle;
        entry.armed = true;
        return true;
    }
    if (!command.isCompute()) {
        entry.complete_at = now_tick + serviceCycles(command) *
                                        config.ticks_per_cycle;
        entry.armed = true;
        return true;
    }
    entry.stage = Stage::READS;
    entry.armed = true;
    return true;
}

void MoeOverlayExecutor::startCommand(const MoeRuntimeCommand &command,
                                      uint64_t now_tick)
{
    Live entry;
    entry.command = &command;
    live.push_back(entry);
    issued_commands++;
    if (command.isCompute() && compute != nullptr)
        compute->noteComputeAdmitted(command.opcode);
    armLive(live.back(), now_tick);
}

void MoeOverlayExecutor::signal(const RuntimeObjectKey &event)
{
    scoreboard->set_visible(event);
    signal_log.push_back(event);
}

void MoeOverlayExecutor::commitCompute(const MoeRuntimeCommand &command)
{
    if (sram == nullptr)
        return;
    ComputeCommitRequest request;
    request.command = command.key;
    request.opcode = command.opcode;
    request.identity_copy =
        command.role == mesh_abi::kMoeCommandRoleCOPY_THROUGH;
    for (const auto &triple : localViews(command)) {
        const MoeOverlayEntry &view = *std::get<0>(triple);
        mesh_abi::DmaEndpoint endpoint;
        uint64_t bytes = 0;
        uint64_t view_bytes = 0;
        fatal_if(!resolveView(view, endpoint, view_bytes),
                 "overlay compute command (region %u ordinal %u) has an "
                 "unresolvable view %u:%u (kind %u backing %u ref %u:%u)",
                 command.key.regionId, command.key.ordinal, view.region_id,
                 view.ordinal, view.secondary_kind, view.backing_kind,
                 view.ref_region, view.ref_ordinal);
        const ComputeSpan span{endpoint.offset_bytes, std::get<3>(triple)};
        if (std::get<1>(triple))
            request.inputs.push_back(span);
        if (std::get<2>(triple)) {
            request.results.push_back(span);
            if (view.backing_kind ==
                mesh_abi::kMoeViewBackingSTATIC_ALLOCATION)
                request.valid_allocations.push_back(view.ref_ordinal);
        }
    }
    ComputeCommitter(sram, compute_digests).commit(request);
    compute_commits++;
}

void MoeOverlayExecutor::markViewsCommitted(const MoeRuntimeCommand &command)
{
    for (const auto &triple : localViews(command)) {
        if (!std::get<2>(triple))
            continue;
        const MoeOverlayEntry &view = *std::get<0>(triple);
        if (view.backing_kind != mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION)
            continue;
        committed_views[std::make_pair(view.region_id, view.ordinal)] +=
            std::get<3>(triple);
    }
}

uint32_t MoeOverlayExecutor::uncommittedViews() const
{
    uint32_t count = 0;
    for (const auto &command : commands) {
        if (finished.count(command.key) == 0)
            continue;
        for (const auto &triple : localViews(command)) {
            if (!std::get<2>(triple))
                continue;
            const MoeOverlayEntry &view = *std::get<0>(triple);
            if (view.backing_kind !=
                mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION)
                continue;
            if (committed_views.count(
                    std::make_pair(view.region_id, view.ordinal)) == 0)
                count++;
        }
    }
    return count;
}

void MoeOverlayExecutor::completeCommand(const MoeRuntimeCommand &command,
                                         uint64_t charged_cycles)
{
    if (command.isCompute()) {
        if (compute != nullptr)
            compute->noteComputeFinished(command.opcode, charged_cycles);
        if (command.role == mesh_abi::kMoeCommandRoleCOPY_THROUGH)
            copy_through_commands++;
        else if (command.role == mesh_abi::kMoeCommandRoleLOCAL_REDUCE)
            local_reduce_commands++;
        for (const auto &ref : command.view_refs) {
            const MoeOverlayEntry *view = viewOf(ref);
            if (view == nullptr)
                continue;
            if (view->secondary_kind != mesh_abi::kMoeViewKindWEIGHT ||
                view->backing_kind !=
                    mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT)
                continue;
            compute->noteWeightConsumed(layer_id_value, view->ref_ordinal);
        }
    }
    markViewsCommitted(command);
    finished.insert(command.key);
    completed_commands++;
    completion_log.push_back(command.key);
    signal(command.key);
    for (const auto &event : command.signals)
        signal(event);
    if (bus != nullptr && command.opcode == mesh_abi::kOpcodeDMA_P2P_PUSH &&
        command.dst_core != kInvalidCore &&
        command.dst_core != config.core_id)
        bus->publish(command.dst_core, command.key);
}

bool MoeOverlayExecutor::tick(uint64_t core_tick)
{
    if (!started_value || failed_value)
        return false;
    now = core_tick;
    bool progressed = false;
    for (auto it = live.begin(); it != live.end();) {
        if (live_dma.count(it->command->key) != 0) {
            // The shared engine owns the terminal; completeDma retires it.
            ++it;
            continue;
        }
        if (!it->armed) {
            if (!armLive(*it, now)) {
                ++it;
                continue;
            }
            progressed = true;
        }
        if (!it->command->isCompute()) {
            if (it->complete_at <= now) {
                completeCommand(*it->command, 0);
                it = live.erase(it);
                progressed = true;
            } else {
                ++it;
            }
            continue;
        }
        if (it->stage == Stage::READS) {
            if (sram != nullptr && !reserveComputeSram(*it, now)) {
                ++it;
                continue;
            }
            it->charged_cycles = serviceCycles(*it->command);
            it->engine_done_at = it->read_done_at +
                                 it->charged_cycles * config.ticks_per_cycle;
            it->stage = Stage::ENGINE;
            progressed = true;
        }
        if (it->stage == Stage::ENGINE) {
            if (now < it->engine_done_at) {
                ++it;
                continue;
            }
            it->stage = Stage::WRITES;
            progressed = true;
        }
        if (it->stage == Stage::WRITES) {
            if (sram != nullptr && !reserveComputeWrites(*it, now)) {
                ++it;
                continue;
            }
            if (now < it->write_done_at) {
                ++it;
                continue;
            }
            it->stage = Stage::RETIRE;
        }
        commitCompute(*it->command);
        compute_cycles += it->charged_cycles;
        completeCommand(*it->command, it->charged_cycles);
        it = live.erase(it);
        progressed = true;
    }
    while (const MoeRuntimeCommand *command = readyCommand()) {
        startCommand(*command, now);
        progressed = true;
    }
    return progressed;
}

} // namespace ai_mesh
} // namespace gem5

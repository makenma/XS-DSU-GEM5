#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_TRAFFIC_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_TRAFFIC_HH

inline bool decodeBinding(const uint8_t *data, Binding &out, AbiError &error)
{
    out = Binding{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Binding presence mask has undeclared bits"}; return false; }
    out.slot_id = mesh_abi::rdU64(data + 8);
    out.region_id = mesh_abi::rdU64(data + 16);
    out.owner_core = mesh_abi::rdU64(data + 24);
    out.allocation_offset_bytes = mesh_abi::rdU64(data + 32);
    out.allocation_size_bytes = mesh_abi::rdU64(data + 40);
    out.allocation_alignment_bytes = mesh_abi::rdU64(data + 48);
    if (mesh_abi::rdU32(data + 56 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Binding.access reserved bytes are nonzero"}; return false; }
    if (!validAccess(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "Binding.access is invalid"}; return false; }
    out.access = static_cast<Access>(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kBindingBytes> encodeBinding(const Binding &value)
{
    std::array<uint8_t, kBindingBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.slot_id);
    mesh_abi::wrU64(data.data() + 16, value.region_id);
    mesh_abi::wrU64(data.data() + 24, value.owner_core);
    mesh_abi::wrU64(data.data() + 32, value.allocation_offset_bytes);
    mesh_abi::wrU64(data.data() + 40, value.allocation_size_bytes);
    mesh_abi::wrU64(data.data() + 48, value.allocation_alignment_bytes);
    mesh_abi::wrU32(data.data() + 56 + kEnumValueValueOffset, static_cast<uint32_t>(value.access));
    return data;
}

inline bool decodeBindingSlot(const uint8_t *data, BindingSlot &out, AbiError &error)
{
    out = BindingSlot{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BindingSlot presence mask has undeclared bits"}; return false; }
    out.slot_id = mesh_abi::rdU64(data + 8);
    if (!decodeStringRef(data + 16, out.symbol, error)) return false;
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BindingSlot.memory_space reserved bytes are nonzero"}; return false; }
    if (!validMemorySpace(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "BindingSlot.memory_space is invalid"}; return false; }
    out.memory_space = static_cast<MemorySpace>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    out.region_id = mesh_abi::rdU64(data + 32);
    out.owner_core = mesh_abi::rdU64(data + 40);
    out.required_allocation_bytes = mesh_abi::rdU64(data + 48);
    out.required_allocation_alignment_bytes = mesh_abi::rdU64(data + 56);
    if (mesh_abi::rdU32(data + 64 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BindingSlot.access reserved bytes are nonzero"}; return false; }
    if (!validAccess(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "BindingSlot.access is invalid"}; return false; }
    out.access = static_cast<Access>(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 72, out.reference_binding, error)) return false;
    if (out.reference_binding.row_id == 0 || (out.reference_binding.section_type != 344)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "BindingSlot.reference_binding target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kBindingSlotBytes> encodeBindingSlot(const BindingSlot &value)
{
    std::array<uint8_t, kBindingSlotBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.slot_id);
    encodeStringRef(data.data() + 16, value.symbol);
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.memory_space));
    mesh_abi::wrU64(data.data() + 32, value.region_id);
    mesh_abi::wrU64(data.data() + 40, value.owner_core);
    mesh_abi::wrU64(data.data() + 48, value.required_allocation_bytes);
    mesh_abi::wrU64(data.data() + 56, value.required_allocation_alignment_bytes);
    mesh_abi::wrU32(data.data() + 64 + kEnumValueValueOffset, static_cast<uint32_t>(value.access));
    encodeSemanticRef(data.data() + 72, value.reference_binding);
    return data;
}

inline bool decodeChannelTraffic(const uint8_t *data, ChannelTraffic &out, AbiError &error)
{
    out = ChannelTraffic{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ChannelTraffic presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ChannelTraffic.channel reserved bytes are nonzero"}; return false; }
    if (!validAxiChannel(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ChannelTraffic.channel is invalid"}; return false; }
    out.channel = static_cast<AxiChannel>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    out.vnet = mesh_abi::rdU64(data + 16);
    if (!decodeStringRef(data + 24, out.source_endpoint, error)) return false;
    if (!decodeStringRef(data + 32, out.destination_endpoint, error)) return false;
    out.source_node = mesh_abi::rdU64(data + 40);
    out.source_port = mesh_abi::rdU64(data + 48);
    out.destination_node = mesh_abi::rdU64(data + 56);
    out.destination_port = mesh_abi::rdU64(data + 64);
    out.source_router = mesh_abi::rdU64(data + 72);
    out.destination_router = mesh_abi::rdU64(data + 80);
    out.messages = mesh_abi::rdU64(data + 88);
    out.packets = mesh_abi::rdU64(data + 96);
    out.flits = mesh_abi::rdU64(data + 104);
    out.wire_bytes = mesh_abi::rdU64(data + 112);
    return true;
}

inline std::array<uint8_t, kChannelTrafficBytes> encodeChannelTraffic(const ChannelTraffic &value)
{
    std::array<uint8_t, kChannelTrafficBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.channel));
    mesh_abi::wrU64(data.data() + 16, value.vnet);
    encodeStringRef(data.data() + 24, value.source_endpoint);
    encodeStringRef(data.data() + 32, value.destination_endpoint);
    mesh_abi::wrU64(data.data() + 40, value.source_node);
    mesh_abi::wrU64(data.data() + 48, value.source_port);
    mesh_abi::wrU64(data.data() + 56, value.destination_node);
    mesh_abi::wrU64(data.data() + 64, value.destination_port);
    mesh_abi::wrU64(data.data() + 72, value.source_router);
    mesh_abi::wrU64(data.data() + 80, value.destination_router);
    mesh_abi::wrU64(data.data() + 88, value.messages);
    mesh_abi::wrU64(data.data() + 96, value.packets);
    mesh_abi::wrU64(data.data() + 104, value.flits);
    mesh_abi::wrU64(data.data() + 112, value.wire_bytes);
    return data;
}

inline bool decodeDescriptorIdentity(const uint8_t *data, DescriptorIdentity &out, AbiError &error)
{
    out = DescriptorIdentity{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorIdentity presence mask has undeclared bits"}; return false; }
    out.entrypoint_id = mesh_abi::rdU64(data + 8);
    out.profile_id = mesh_abi::rdU64(data + 16);
    out.command_id = mesh_abi::rdU64(data + 24);
    out.descriptor_id = mesh_abi::rdU64(data + 32);
    out.issuing_core = mesh_abi::rdU64(data + 40);
    out.peer_core = mesh_abi::rdU64(data + 48);
    out.tensor_id = mesh_abi::rdU64(data + 56);
    if (mesh_abi::rdU32(data + 64 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorIdentity.tensor_role reserved bytes are nonzero"}; return false; }
    if (!validTensorRole(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DescriptorIdentity.tensor_role is invalid"}; return false; }
    out.tensor_role = static_cast<TensorRole>(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 72 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorIdentity.direction reserved bytes are nonzero"}; return false; }
    if (!validTrafficDirection(mesh_abi::rdU32(data + 72 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DescriptorIdentity.direction is invalid"}; return false; }
    out.direction = static_cast<TrafficDirection>(mesh_abi::rdU32(data + 72 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kDescriptorIdentityBytes> encodeDescriptorIdentity(const DescriptorIdentity &value)
{
    std::array<uint8_t, kDescriptorIdentityBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.entrypoint_id);
    mesh_abi::wrU64(data.data() + 16, value.profile_id);
    mesh_abi::wrU64(data.data() + 24, value.command_id);
    mesh_abi::wrU64(data.data() + 32, value.descriptor_id);
    mesh_abi::wrU64(data.data() + 40, value.issuing_core);
    mesh_abi::wrU64(data.data() + 48, value.peer_core);
    mesh_abi::wrU64(data.data() + 56, value.tensor_id);
    mesh_abi::wrU32(data.data() + 64 + kEnumValueValueOffset, static_cast<uint32_t>(value.tensor_role));
    mesh_abi::wrU32(data.data() + 72 + kEnumValueValueOffset, static_cast<uint32_t>(value.direction));
    return data;
}

inline bool decodeDescriptorTraffic(const uint8_t *data, DescriptorTraffic &out, AbiError &error)
{
    out = DescriptorTraffic{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorTraffic presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.identity, error)) return false;
    if (out.identity.row_id == 0 || (out.identity.section_type != 347)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "DescriptorTraffic.identity target is invalid"}; return false; }
    out.execution_count = mesh_abi::rdU64(data + 16);
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorTraffic.src_memory_space reserved bytes are nonzero"}; return false; }
    if (!validMemorySpace(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DescriptorTraffic.src_memory_space is invalid"}; return false; }
    out.src_memory_space = static_cast<MemorySpace>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorTraffic.dst_memory_space reserved bytes are nonzero"}; return false; }
    if (!validMemorySpace(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DescriptorTraffic.dst_memory_space is invalid"}; return false; }
    out.dst_memory_space = static_cast<MemorySpace>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    if (!decodeStringRef(data + 40, out.src_endpoint, error)) return false;
    if (!decodeStringRef(data + 48, out.dst_endpoint, error)) return false;
    out.src_router = mesh_abi::rdU64(data + 56);
    out.dst_router = mesh_abi::rdU64(data + 64);
    out.src_address = mesh_abi::rdU64(data + 72);
    out.dst_address = mesh_abi::rdU64(data + 80);
    out.remote_address = mesh_abi::rdU64(data + 88);
    out.rows = mesh_abi::rdU64(data + 96);
    out.row_bytes = mesh_abi::rdU64(data + 104);
    out.remote_stride_bytes = mesh_abi::rdU64(data + 112);
    out.max_burst_beats = mesh_abi::rdU64(data + 120);
    out.hops = mesh_abi::rdU64(data + 128);
    out.useful_bytes = mesh_abi::rdU64(data + 136);
    out.physical_beat_bytes = mesh_abi::rdU64(data + 144);
    out.segments = mesh_abi::rdU64(data + 152);
    out.bursts = mesh_abi::rdU64(data + 160);
    if (!decodeListSpan(data + 168, out.channels, error)) return false;
    out.packets = mesh_abi::rdU64(data + 176);
    out.flits = mesh_abi::rdU64(data + 184);
    out.wire_bytes = mesh_abi::rdU64(data + 192);
    out.hop_wire_bytes = mesh_abi::rdU64(data + 200);
    return true;
}

inline std::array<uint8_t, kDescriptorTrafficBytes> encodeDescriptorTraffic(const DescriptorTraffic &value)
{
    std::array<uint8_t, kDescriptorTrafficBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.identity);
    mesh_abi::wrU64(data.data() + 16, value.execution_count);
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.src_memory_space));
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.dst_memory_space));
    encodeStringRef(data.data() + 40, value.src_endpoint);
    encodeStringRef(data.data() + 48, value.dst_endpoint);
    mesh_abi::wrU64(data.data() + 56, value.src_router);
    mesh_abi::wrU64(data.data() + 64, value.dst_router);
    mesh_abi::wrU64(data.data() + 72, value.src_address);
    mesh_abi::wrU64(data.data() + 80, value.dst_address);
    mesh_abi::wrU64(data.data() + 88, value.remote_address);
    mesh_abi::wrU64(data.data() + 96, value.rows);
    mesh_abi::wrU64(data.data() + 104, value.row_bytes);
    mesh_abi::wrU64(data.data() + 112, value.remote_stride_bytes);
    mesh_abi::wrU64(data.data() + 120, value.max_burst_beats);
    mesh_abi::wrU64(data.data() + 128, value.hops);
    mesh_abi::wrU64(data.data() + 136, value.useful_bytes);
    mesh_abi::wrU64(data.data() + 144, value.physical_beat_bytes);
    mesh_abi::wrU64(data.data() + 152, value.segments);
    mesh_abi::wrU64(data.data() + 160, value.bursts);
    encodeListSpan(data.data() + 168, value.channels);
    mesh_abi::wrU64(data.data() + 176, value.packets);
    mesh_abi::wrU64(data.data() + 184, value.flits);
    mesh_abi::wrU64(data.data() + 192, value.wire_bytes);
    mesh_abi::wrU64(data.data() + 200, value.hop_wire_bytes);
    return data;
}

inline bool decodeTrafficAggregate(const uint8_t *data, TrafficAggregate &out, AbiError &error)
{
    out = TrafficAggregate{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregate presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.key, error)) return false;
    if (out.key.row_id == 0 || (out.key.section_type != 350)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "TrafficAggregate.key target is invalid"}; return false; }
    out.useful_bytes = mesh_abi::rdU64(data + 16);
    out.physical_beat_bytes = mesh_abi::rdU64(data + 24);
    out.static_descriptors = mesh_abi::rdU64(data + 32);
    out.descriptor_executions = mesh_abi::rdU64(data + 40);
    out.segments = mesh_abi::rdU64(data + 48);
    out.bursts = mesh_abi::rdU64(data + 56);
    if (!decodeListSpan(data + 64, out.channels, error)) return false;
    out.packets = mesh_abi::rdU64(data + 72);
    out.flits = mesh_abi::rdU64(data + 80);
    out.wire_bytes = mesh_abi::rdU64(data + 88);
    out.hop_wire_bytes = mesh_abi::rdU64(data + 96);
    return true;
}

inline std::array<uint8_t, kTrafficAggregateBytes> encodeTrafficAggregate(const TrafficAggregate &value)
{
    std::array<uint8_t, kTrafficAggregateBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.key);
    mesh_abi::wrU64(data.data() + 16, value.useful_bytes);
    mesh_abi::wrU64(data.data() + 24, value.physical_beat_bytes);
    mesh_abi::wrU64(data.data() + 32, value.static_descriptors);
    mesh_abi::wrU64(data.data() + 40, value.descriptor_executions);
    mesh_abi::wrU64(data.data() + 48, value.segments);
    mesh_abi::wrU64(data.data() + 56, value.bursts);
    encodeListSpan(data.data() + 64, value.channels);
    mesh_abi::wrU64(data.data() + 72, value.packets);
    mesh_abi::wrU64(data.data() + 80, value.flits);
    mesh_abi::wrU64(data.data() + 88, value.wire_bytes);
    mesh_abi::wrU64(data.data() + 96, value.hop_wire_bytes);
    return data;
}

inline bool decodeTrafficAggregateKey(const uint8_t *data, TrafficAggregateKey &out, AbiError &error)
{
    out = TrafficAggregateKey{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(254)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.level reserved bytes are nonzero"}; return false; }
    if (!validTrafficAggregateLevel(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TrafficAggregateKey.level is invalid"}; return false; }
    out.level = static_cast<TrafficAggregateLevel>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if ((out.presence_mask & (uint64_t(1) << 1)) == 0) {
        if (!zeroBytes(data + 16, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.entrypoint_id absent bytes are nonzero"}; return false; }
    } else {
        out.entrypoint_id = mesh_abi::rdU64(data + 16);
    }
    if ((out.presence_mask & (uint64_t(1) << 2)) == 0) {
        if (!zeroBytes(data + 24, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.profile_id absent bytes are nonzero"}; return false; }
    } else {
        out.profile_id = mesh_abi::rdU64(data + 24);
    }
    if ((out.presence_mask & (uint64_t(1) << 3)) == 0) {
        if (!zeroBytes(data + 32, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.core_id absent bytes are nonzero"}; return false; }
    } else {
        out.core_id = mesh_abi::rdU64(data + 32);
    }
    if ((out.presence_mask & (uint64_t(1) << 4)) == 0) {
        if (!zeroBytes(data + 40, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.peer_core absent bytes are nonzero"}; return false; }
    } else {
        out.peer_core = mesh_abi::rdU64(data + 40);
    }
    if ((out.presence_mask & (uint64_t(1) << 5)) == 0) {
        if (!zeroBytes(data + 48, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.memory_space absent bytes are nonzero"}; return false; }
    } else {
        if (mesh_abi::rdU32(data + 48 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.memory_space reserved bytes are nonzero"}; return false; }
        if (!validMemorySpace(mesh_abi::rdU32(data + 48 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TrafficAggregateKey.memory_space is invalid"}; return false; }
        out.memory_space = static_cast<MemorySpace>(mesh_abi::rdU32(data + 48 + kEnumValueValueOffset));
    }
    if ((out.presence_mask & (uint64_t(1) << 6)) == 0) {
        if (!zeroBytes(data + 56, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.tensor_role absent bytes are nonzero"}; return false; }
    } else {
        if (mesh_abi::rdU32(data + 56 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.tensor_role reserved bytes are nonzero"}; return false; }
        if (!validTensorRole(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TrafficAggregateKey.tensor_role is invalid"}; return false; }
        out.tensor_role = static_cast<TensorRole>(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset));
    }
    if ((out.presence_mask & (uint64_t(1) << 7)) == 0) {
        if (!zeroBytes(data + 64, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.direction absent bytes are nonzero"}; return false; }
    } else {
        if (mesh_abi::rdU32(data + 64 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficAggregateKey.direction reserved bytes are nonzero"}; return false; }
        if (!validTrafficDirection(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TrafficAggregateKey.direction is invalid"}; return false; }
        out.direction = static_cast<TrafficDirection>(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset));
    }
    return true;
}

inline std::array<uint8_t, kTrafficAggregateKeyBytes> encodeTrafficAggregateKey(const TrafficAggregateKey &value)
{
    std::array<uint8_t, kTrafficAggregateKeyBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.level));
    if ((value.presence_mask & (uint64_t(1) << 1)) != 0) {
        mesh_abi::wrU64(data.data() + 16, value.entrypoint_id);
    }
    if ((value.presence_mask & (uint64_t(1) << 2)) != 0) {
        mesh_abi::wrU64(data.data() + 24, value.profile_id);
    }
    if ((value.presence_mask & (uint64_t(1) << 3)) != 0) {
        mesh_abi::wrU64(data.data() + 32, value.core_id);
    }
    if ((value.presence_mask & (uint64_t(1) << 4)) != 0) {
        mesh_abi::wrU64(data.data() + 40, value.peer_core);
    }
    if ((value.presence_mask & (uint64_t(1) << 5)) != 0) {
        mesh_abi::wrU32(data.data() + 48 + kEnumValueValueOffset, static_cast<uint32_t>(value.memory_space));
    }
    if ((value.presence_mask & (uint64_t(1) << 6)) != 0) {
        mesh_abi::wrU32(data.data() + 56 + kEnumValueValueOffset, static_cast<uint32_t>(value.tensor_role));
    }
    if ((value.presence_mask & (uint64_t(1) << 7)) != 0) {
        mesh_abi::wrU32(data.data() + 64 + kEnumValueValueOffset, static_cast<uint32_t>(value.direction));
    }
    return data;
}

inline bool decodeTrafficChannelTotal(const uint8_t *data, TrafficChannelTotal &out, AbiError &error)
{
    out = TrafficChannelTotal{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficChannelTotal presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficChannelTotal.channel reserved bytes are nonzero"}; return false; }
    if (!validAxiChannel(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TrafficChannelTotal.channel is invalid"}; return false; }
    out.channel = static_cast<AxiChannel>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    out.vnet = mesh_abi::rdU64(data + 16);
    out.messages = mesh_abi::rdU64(data + 24);
    out.packets = mesh_abi::rdU64(data + 32);
    out.flits = mesh_abi::rdU64(data + 40);
    out.wire_bytes = mesh_abi::rdU64(data + 48);
    return true;
}

inline std::array<uint8_t, kTrafficChannelTotalBytes> encodeTrafficChannelTotal(const TrafficChannelTotal &value)
{
    std::array<uint8_t, kTrafficChannelTotalBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.channel));
    mesh_abi::wrU64(data.data() + 16, value.vnet);
    mesh_abi::wrU64(data.data() + 24, value.messages);
    mesh_abi::wrU64(data.data() + 32, value.packets);
    mesh_abi::wrU64(data.data() + 40, value.flits);
    mesh_abi::wrU64(data.data() + 48, value.wire_bytes);
    return data;
}

inline bool decodeTrafficReport(const uint8_t *data, TrafficReport &out, AbiError &error)
{
    out = TrafficReport{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TrafficReport presence mask has undeclared bits"}; return false; }
    if (!decodeStringRef(data + 8, out.binding_identity_sha256, error)) return false;
    if (!decodeListSpan(data + 16, out.descriptors, error)) return false;
    if (!decodeListSpan(data + 24, out.aggregates, error)) return false;
    if (!decodeStringRef(data + 32, out.semantic_sha256, error)) return false;
    return true;
}

inline std::array<uint8_t, kTrafficReportBytes> encodeTrafficReport(const TrafficReport &value)
{
    std::array<uint8_t, kTrafficReportBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeStringRef(data.data() + 8, value.binding_identity_sha256);
    encodeListSpan(data.data() + 16, value.descriptors);
    encodeListSpan(data.data() + 24, value.aggregates);
    encodeStringRef(data.data() + 32, value.semantic_sha256);
    return data;
}

#endif

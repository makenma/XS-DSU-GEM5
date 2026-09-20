#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_CODECS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_CODECS_HH

inline bool decodeEntrypoint(const uint8_t *r, Entrypoint &out, AbiError &error)
{
    (void)error;
    out.entrypoint_id = rdU32(r + 0);
    out.name_sid = rdU32(r + 4);
    out.profile_begin = rdU32(r + 8);
    out.profile_count = rdU16(r + 12);
    out.lifecycle_core_id = rdU16(r + 14);
    out.lifecycle_stream_id = rdU16(r + 16);
    out.flags = rdU16(r + 18);
    out.reserved = rdU32(r + 20);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ENTRYPOINTS.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kEntrypointsBytes> encodeEntrypoint(const Entrypoint &value)
{
    std::array<uint8_t, kEntrypointsBytes> data{};
    wrU32(data.data() + 0, value.entrypoint_id);
    wrU32(data.data() + 4, value.name_sid);
    wrU32(data.data() + 8, value.profile_begin);
    wrU16(data.data() + 12, value.profile_count);
    wrU16(data.data() + 14, value.lifecycle_core_id);
    wrU16(data.data() + 16, value.lifecycle_stream_id);
    wrU16(data.data() + 18, value.flags);
    wrU32(data.data() + 20, value.reserved);
    return data;
}

inline bool decodeProfile(const uint8_t *r, Profile &out, AbiError &error)
{
    (void)error;
    out.profile_id = rdU32(r + 0);
    out.entrypoint_id = rdU32(r + 4);
    out.name_sid = rdU32(r + 8);
    out.rank = rdU16(r + 12);
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "PROFILES.reserved must be zero"}; return false; }
    for (size_t i = 0; i < 8; i++) out.dims[i] = rdU64(r + 16 + i * 8);
    return true;
}

inline std::array<uint8_t, kProfilesBytes> encodeProfile(const Profile &value)
{
    std::array<uint8_t, kProfilesBytes> data{};
    wrU32(data.data() + 0, value.profile_id);
    wrU32(data.data() + 4, value.entrypoint_id);
    wrU32(data.data() + 8, value.name_sid);
    wrU16(data.data() + 12, value.rank);
    wrU16(data.data() + 14, value.reserved);
    for (size_t i = 0; i < 8; i++) wrU64(data.data() + 16 + i * 8, value.dims[i]);
    return data;
}

inline bool decodeTensor(const uint8_t *r, Tensor &out, AbiError &error)
{
    (void)error;
    out.tensor_id = rdU32(r + 0);
    out.name_sid = rdU32(r + 4);
    out.role = rdU16(r + 8);
    if (!validTensorRole(out.role)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.role not in closed set"}; return false; }
    out.dtype = rdU16(r + 10);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.dtype not in closed set"}; return false; }
    out.storage_class = rdU16(r + 12);
    if (!validStorageClass(out.storage_class)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.storage_class not in closed set"}; return false; }
    out.access = rdU16(r + 14);
    if (!validAccessKind(out.access)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.access not in closed set"}; return false; }
    out.rank = rdU16(r + 16);
    out.layout = rdU16(r + 18);
    if (!validLayoutKind(out.layout)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.layout not in closed set"}; return false; }
    out.layout_attr = rdU32(r + 20);
    out.placement_id = rdU32(r + 24);
    out.sharding_id = rdU32(r + 28);
    out.flags = rdU32(r + 32);
    if (!flagsWithin(kTensorFlagsAllowedBits, out.flags)) { error = {mesh_diagnostics::E_ABI_ENUM, "TENSORS.flags flags have unknown bits"}; return false; }
    out.reserved = rdU32(r + 36);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TENSORS.reserved must be zero"}; return false; }
    for (size_t i = 0; i < 8; i++) out.dims[i] = rdU64(r + 40 + i * 8);
    std::memcpy(out.content_sha256.data(), r + 104, 32);
    return true;
}

inline std::array<uint8_t, kTensorsBytes> encodeTensor(const Tensor &value)
{
    std::array<uint8_t, kTensorsBytes> data{};
    wrU32(data.data() + 0, value.tensor_id);
    wrU32(data.data() + 4, value.name_sid);
    wrU16(data.data() + 8, value.role);
    wrU16(data.data() + 10, value.dtype);
    wrU16(data.data() + 12, value.storage_class);
    wrU16(data.data() + 14, value.access);
    wrU16(data.data() + 16, value.rank);
    wrU16(data.data() + 18, value.layout);
    wrU32(data.data() + 20, value.layout_attr);
    wrU32(data.data() + 24, value.placement_id);
    wrU32(data.data() + 28, value.sharding_id);
    wrU32(data.data() + 32, value.flags);
    wrU32(data.data() + 36, value.reserved);
    for (size_t i = 0; i < 8; i++) wrU64(data.data() + 40 + i * 8, value.dims[i]);
    std::memcpy(data.data() + 104, value.content_sha256.data(), 32);
    return data;
}

inline bool decodeShard(const uint8_t *r, Shard &out, AbiError &error)
{
    (void)error;
    out.shard_id = rdU32(r + 0);
    out.tensor_id = rdU32(r + 4);
    out.sharding_id = rdU32(r + 8);
    out.owner_core = rdU16(r + 12);
    out.rank = rdU16(r + 14);
    out.reserved = rdU16(r + 16);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SHARDS.reserved must be zero"}; return false; }
    out.flags = rdU16(r + 18);
    out.reserved0 = rdU32(r + 20);
    if (out.reserved0 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SHARDS.reserved0 must be zero"}; return false; }
    for (size_t i = 0; i < 8; i++) out.global_origin[i] = rdU64(r + 24 + i * 8);
    for (size_t i = 0; i < 8; i++) out.local_shape[i] = rdU64(r + 88 + i * 8);
    for (size_t i = 0; i < 8; i++) out.valid_shape[i] = rdU64(r + 152 + i * 8);
    out.allocation_id = rdU32(r + 216);
    out.reserved2 = rdU32(r + 220);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SHARDS.reserved2 must be zero"}; return false; }
    out.allocation_offset = rdU64(r + 224);
    out.span_bytes = rdU64(r + 232);
    return true;
}

inline std::array<uint8_t, kShardsBytes> encodeShard(const Shard &value)
{
    std::array<uint8_t, kShardsBytes> data{};
    wrU32(data.data() + 0, value.shard_id);
    wrU32(data.data() + 4, value.tensor_id);
    wrU32(data.data() + 8, value.sharding_id);
    wrU16(data.data() + 12, value.owner_core);
    wrU16(data.data() + 14, value.rank);
    wrU16(data.data() + 16, value.reserved);
    wrU16(data.data() + 18, value.flags);
    wrU32(data.data() + 20, value.reserved0);
    for (size_t i = 0; i < 8; i++) wrU64(data.data() + 24 + i * 8, value.global_origin[i]);
    for (size_t i = 0; i < 8; i++) wrU64(data.data() + 88 + i * 8, value.local_shape[i]);
    for (size_t i = 0; i < 8; i++) wrU64(data.data() + 152 + i * 8, value.valid_shape[i]);
    wrU32(data.data() + 216, value.allocation_id);
    wrU32(data.data() + 220, value.reserved2);
    wrU64(data.data() + 224, value.allocation_offset);
    wrU64(data.data() + 232, value.span_bytes);
    return data;
}

inline bool decodeAllocation(const uint8_t *r, Allocation &out, AbiError &error)
{
    (void)error;
    out.allocation_id = rdU32(r + 0);
    out.owner_core = rdU16(r + 4);
    out.memory_space = rdU16(r + 6);
    if (!validMemorySpace(out.memory_space)) { error = {mesh_diagnostics::E_ABI_ENUM, "ALLOCATIONS.memory_space not in closed set"}; return false; }
    out.offset_bytes = rdU64(r + 8);
    out.size_bytes = rdU64(r + 16);
    out.alignment_bytes = rdU32(r + 24);
    out.flags = rdU32(r + 28);
    return true;
}

inline std::array<uint8_t, kAllocationsBytes> encodeAllocation(const Allocation &value)
{
    std::array<uint8_t, kAllocationsBytes> data{};
    wrU32(data.data() + 0, value.allocation_id);
    wrU16(data.data() + 4, value.owner_core);
    wrU16(data.data() + 6, value.memory_space);
    wrU64(data.data() + 8, value.offset_bytes);
    wrU64(data.data() + 16, value.size_bytes);
    wrU32(data.data() + 24, value.alignment_bytes);
    wrU32(data.data() + 28, value.flags);
    return data;
}

inline bool decodeStream(const uint8_t *r, Stream &out, AbiError &error)
{
    (void)error;
    out.core_id = rdU16(r + 0);
    out.stream_id = rdU16(r + 2);
    out.command_begin = rdU32(r + 4);
    out.command_count = rdU32(r + 8);
    out.flags = rdU16(r + 12);
    if (!flagsWithin(kStreamFlagsAllowedBits, out.flags)) { error = {mesh_diagnostics::E_ABI_ENUM, "STREAMS.flags flags have unknown bits"}; return false; }
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "STREAMS.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kStreamsBytes> encodeStream(const Stream &value)
{
    std::array<uint8_t, kStreamsBytes> data{};
    wrU16(data.data() + 0, value.core_id);
    wrU16(data.data() + 2, value.stream_id);
    wrU32(data.data() + 4, value.command_begin);
    wrU32(data.data() + 8, value.command_count);
    wrU16(data.data() + 12, value.flags);
    wrU16(data.data() + 14, value.reserved);
    return data;
}

inline bool decodeCommand(const uint8_t *r, Command &out, AbiError &error)
{
    (void)error;
    out.command_id = rdU32(r + 0);
    out.source_op_id = rdU32(r + 4);
    out.core_id = rdU16(r + 8);
    out.stream_id = rdU16(r + 10);
    out.engine = rdU16(r + 12);
    if (!validEngine(out.engine)) { error = {mesh_diagnostics::E_ABI_ENUM, "COMMANDS.engine not in closed set"}; return false; }
    out.opcode = rdU16(r + 14);
    if (!validOpcode(out.opcode)) { error = {mesh_diagnostics::E_ABI_ENUM, "COMMANDS.opcode not in closed set"}; return false; }
    out.wait_begin = rdU32(r + 16);
    out.wait_count = rdU16(r + 20);
    out.operand_count = rdU16(r + 22);
    out.operand_begin = rdU32(r + 24);
    out.signal_event = rdU32(r + 28);
    out.attr_index = rdU32(r + 32);
    out.debug_loc_id = rdU32(r + 36);
    return true;
}

inline std::array<uint8_t, kCommandsBytes> encodeCommand(const Command &value)
{
    std::array<uint8_t, kCommandsBytes> data{};
    wrU32(data.data() + 0, value.command_id);
    wrU32(data.data() + 4, value.source_op_id);
    wrU16(data.data() + 8, value.core_id);
    wrU16(data.data() + 10, value.stream_id);
    wrU16(data.data() + 12, value.engine);
    wrU16(data.data() + 14, value.opcode);
    wrU32(data.data() + 16, value.wait_begin);
    wrU16(data.data() + 20, value.wait_count);
    wrU16(data.data() + 22, value.operand_count);
    wrU32(data.data() + 24, value.operand_begin);
    wrU32(data.data() + 28, value.signal_event);
    wrU32(data.data() + 32, value.attr_index);
    wrU32(data.data() + 36, value.debug_loc_id);
    return data;
}

inline bool decodeCommandWait(const uint8_t *r, CommandWait &out, AbiError &error)
{
    (void)error;
    out.event_id = rdU32(r + 0);
    return true;
}

inline std::array<uint8_t, kCommandWaitsBytes> encodeCommandWait(const CommandWait &value)
{
    std::array<uint8_t, kCommandWaitsBytes> data{};
    wrU32(data.data() + 0, value.event_id);
    return data;
}

inline bool decodeCommandOperand(const uint8_t *r, CommandOperand &out, AbiError &error)
{
    (void)error;
    out.tensor_id = rdU32(r + 0);
    out.shard_id = rdU32(r + 4);
    out.allocation_id = rdU32(r + 8);
    out.access = rdU16(r + 12);
    if (!validAccessKind(out.access)) { error = {mesh_diagnostics::E_ABI_ENUM, "COMMAND_OPERANDS.access not in closed set"}; return false; }
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "COMMAND_OPERANDS.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kCommandOperandsBytes> encodeCommandOperand(const CommandOperand &value)
{
    std::array<uint8_t, kCommandOperandsBytes> data{};
    wrU32(data.data() + 0, value.tensor_id);
    wrU32(data.data() + 4, value.shard_id);
    wrU32(data.data() + 8, value.allocation_id);
    wrU16(data.data() + 12, value.access);
    wrU16(data.data() + 14, value.reserved);
    return data;
}

inline bool decodeEvent(const uint8_t *r, Event &out, AbiError &error)
{
    (void)error;
    out.event_id = rdU32(r + 0);
    out.kind = rdU16(r + 4);
    if (!validEventKind(out.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "EVENTS.kind not in closed set"}; return false; }
    out.reserved = rdU16(r + 6);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EVENTS.reserved must be zero"}; return false; }
    out.producer_command_id = rdU32(r + 8);
    out.expected_arrivals = rdU32(r + 12);
    out.reserved2 = rdU32(r + 16);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EVENTS.reserved2 must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kEventsBytes> encodeEvent(const Event &value)
{
    std::array<uint8_t, kEventsBytes> data{};
    wrU32(data.data() + 0, value.event_id);
    wrU16(data.data() + 4, value.kind);
    wrU16(data.data() + 6, value.reserved);
    wrU32(data.data() + 8, value.producer_command_id);
    wrU32(data.data() + 12, value.expected_arrivals);
    wrU32(data.data() + 16, value.reserved2);
    return data;
}

inline bool decodeDmaEndpoint(const uint8_t *r, DmaEndpoint &out, AbiError &error)
{
    (void)error;
    out.memory_space = rdU16(r + 0);
    if (!validMemorySpace(out.memory_space)) { error = {mesh_diagnostics::E_ABI_ENUM, "DMA_ENDPOINT.memory_space not in closed set"}; return false; }
    out.region_id = rdU16(r + 2);
    out.owner_core = rdU16(r + 4);
    out.tensor_id = rdU32(r + 6);
    out.shard_id = rdU32(r + 10);
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DMA_ENDPOINT.reserved must be zero"}; return false; }
    out.offset_bytes = rdU64(r + 16);
    return true;
}

inline std::array<uint8_t, kDmaEndpointBytes> encodeDmaEndpoint(const DmaEndpoint &value)
{
    std::array<uint8_t, kDmaEndpointBytes> data{};
    wrU16(data.data() + 0, value.memory_space);
    wrU16(data.data() + 2, value.region_id);
    wrU16(data.data() + 4, value.owner_core);
    wrU32(data.data() + 6, value.tensor_id);
    wrU32(data.data() + 10, value.shard_id);
    wrU16(data.data() + 14, value.reserved);
    wrU64(data.data() + 16, value.offset_bytes);
    return data;
}

inline bool decodeDmaDescriptor(const uint8_t *r, DmaDescriptor &out, AbiError &error)
{
    (void)error;
    out.descriptor_id = rdU32(r + 0);
    out.command_id = rdU32(r + 4);
    out.transfer_id = rdU32(r + 8);
    out.owner_core = rdU16(r + 12);
    out.kind = rdU16(r + 14);
    if (!validDmaKind(out.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "DMA_DESCRIPTORS.kind not in closed set"}; return false; }
    if (!decodeDmaEndpoint(r + 16, out.src, error)) return false;
    if (!decodeDmaEndpoint(r + 40, out.dst, error)) return false;
    out.rows = rdU32(r + 64);
    out.row_bytes = rdU64(r + 68);
    out.src_stride_bytes = rdU64(r + 76);
    out.dst_stride_bytes = rdU64(r + 84);
    out.useful_bytes = rdU64(r + 92);
    out.physical_storage_bytes = rdU64(r + 100);
    out.axi_id = rdU16(r + 108);
    out.qos = rdU8(r + 110);
    out.reserved = rdU8(r + 111);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DMA_DESCRIPTORS.reserved must be zero"}; return false; }
    out.max_burst_beats = rdU16(r + 112);
    out.reserved2 = rdU16(r + 114);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DMA_DESCRIPTORS.reserved2 must be zero"}; return false; }
    out.completion_event = rdU32(r + 116);
    return true;
}

inline std::array<uint8_t, kDmaDescriptorsBytes> encodeDmaDescriptor(const DmaDescriptor &value)
{
    std::array<uint8_t, kDmaDescriptorsBytes> data{};
    wrU32(data.data() + 0, value.descriptor_id);
    wrU32(data.data() + 4, value.command_id);
    wrU32(data.data() + 8, value.transfer_id);
    wrU16(data.data() + 12, value.owner_core);
    wrU16(data.data() + 14, value.kind);
    const auto src = encodeDmaEndpoint(value.src);
    std::memcpy(data.data() + 16, src.data(), src.size());
    const auto dst = encodeDmaEndpoint(value.dst);
    std::memcpy(data.data() + 40, dst.data(), dst.size());
    wrU32(data.data() + 64, value.rows);
    wrU64(data.data() + 68, value.row_bytes);
    wrU64(data.data() + 76, value.src_stride_bytes);
    wrU64(data.data() + 84, value.dst_stride_bytes);
    wrU64(data.data() + 92, value.useful_bytes);
    wrU64(data.data() + 100, value.physical_storage_bytes);
    wrU16(data.data() + 108, value.axi_id);
    data[110] = value.qos;
    data[111] = value.reserved;
    wrU16(data.data() + 112, value.max_burst_beats);
    wrU16(data.data() + 114, value.reserved2);
    wrU32(data.data() + 116, value.completion_event);
    return data;
}

inline bool decodeOpAttr(const uint8_t *r, OpAttr &out, AbiError &error)
{
    (void)error;
    out.kind = rdU16(r + 0);
    if (!validAttrKind(out.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "OP_ATTRS.kind not in closed set"}; return false; }
    out.reserved = rdU16(r + 2);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "OP_ATTRS.reserved must be zero"}; return false; }
    std::memcpy(out.payload.data(), r + 4, 28);
    return true;
}

inline std::array<uint8_t, kOpAttrsBytes> encodeOpAttr(const OpAttr &value)
{
    std::array<uint8_t, kOpAttrsBytes> data{};
    wrU16(data.data() + 0, value.kind);
    wrU16(data.data() + 2, value.reserved);
    std::memcpy(data.data() + 4, value.payload.data(), 28);
    return data;
}

inline bool decodeRelocation(const uint8_t *r, Relocation &out, AbiError &error)
{
    (void)error;
    out.relocation_id = rdU32(r + 0);
    out.symbol_sid = rdU32(r + 4);
    out.kind = rdU16(r + 8);
    if (!validRelocationKind(out.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "RELOCATIONS.kind not in closed set"}; return false; }
    out.region_id = rdU16(r + 10);
    out.tensor_id = rdU32(r + 12);
    out.reserved = rdU32(r + 16);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RELOCATIONS.reserved must be zero"}; return false; }
    out.offset_bytes = rdU64(r + 20);
    out.reserved2 = rdU32(r + 28);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RELOCATIONS.reserved2 must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kRelocationsBytes> encodeRelocation(const Relocation &value)
{
    std::array<uint8_t, kRelocationsBytes> data{};
    wrU32(data.data() + 0, value.relocation_id);
    wrU32(data.data() + 4, value.symbol_sid);
    wrU16(data.data() + 8, value.kind);
    wrU16(data.data() + 10, value.region_id);
    wrU32(data.data() + 12, value.tensor_id);
    wrU32(data.data() + 16, value.reserved);
    wrU64(data.data() + 20, value.offset_bytes);
    wrU32(data.data() + 28, value.reserved2);
    return data;
}

inline bool decodeExpectedTraffic(const uint8_t *r, ExpectedTraffic &out, AbiError &error)
{
    (void)error;
    out.entrypoint_id = rdU32(r + 0);
    out.profile_id = rdU32(r + 4);
    out.command_id = rdU32(r + 8);
    out.descriptor_id = rdU32(r + 12);
    out.kind = rdU16(r + 16);
    if (!validDmaKind(out.kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "EXPECTED_TRAFFIC.kind not in closed set"}; return false; }
    out.reserved = rdU16(r + 18);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EXPECTED_TRAFFIC.reserved must be zero"}; return false; }
    out.useful_bytes = rdU64(r + 20);
    out.physical_beat_bytes = rdU64(r + 28);
    out.segments = rdU32(r + 36);
    out.bursts = rdU32(r + 40);
    out.ar_count = rdU32(r + 44);
    out.r_beats = rdU32(r + 48);
    out.aw_count = rdU32(r + 52);
    out.w_beats = rdU32(r + 56);
    out.b_count = rdU32(r + 60);
    out.min_flits = rdU32(r + 64);
    out.reserved2 = rdU32(r + 68);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EXPECTED_TRAFFIC.reserved2 must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kExpectedTrafficBytes> encodeExpectedTraffic(const ExpectedTraffic &value)
{
    std::array<uint8_t, kExpectedTrafficBytes> data{};
    wrU32(data.data() + 0, value.entrypoint_id);
    wrU32(data.data() + 4, value.profile_id);
    wrU32(data.data() + 8, value.command_id);
    wrU32(data.data() + 12, value.descriptor_id);
    wrU16(data.data() + 16, value.kind);
    wrU16(data.data() + 18, value.reserved);
    wrU64(data.data() + 20, value.useful_bytes);
    wrU64(data.data() + 28, value.physical_beat_bytes);
    wrU32(data.data() + 36, value.segments);
    wrU32(data.data() + 40, value.bursts);
    wrU32(data.data() + 44, value.ar_count);
    wrU32(data.data() + 48, value.r_beats);
    wrU32(data.data() + 52, value.aw_count);
    wrU32(data.data() + 56, value.w_beats);
    wrU32(data.data() + 60, value.b_count);
    wrU32(data.data() + 64, value.min_flits);
    wrU32(data.data() + 68, value.reserved2);
    return data;
}

inline bool decodeSourceMap(const uint8_t *r, SourceMap &out, AbiError &error)
{
    (void)error;
    out.loc_id = rdU32(r + 0);
    out.file_sid = rdU32(r + 4);
    out.line = rdU32(r + 8);
    out.column = rdU32(r + 12);
    return true;
}

inline std::array<uint8_t, kSourceMapBytes> encodeSourceMap(const SourceMap &value)
{
    std::array<uint8_t, kSourceMapBytes> data{};
    wrU32(data.data() + 0, value.loc_id);
    wrU32(data.data() + 4, value.file_sid);
    wrU32(data.data() + 8, value.line);
    wrU32(data.data() + 12, value.column);
    return data;
}

inline bool decodeContentDigest(const uint8_t *r, ContentDigest &out, AbiError &error)
{
    (void)error;
    out.object_kind = rdU16(r + 0);
    if (!validContentDigestObjectKind(out.object_kind)) { error = {mesh_diagnostics::E_ABI_ENUM, "CONTENT_DIGESTS.object_kind not in closed set"}; return false; }
    out.reserved = rdU16(r + 2);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CONTENT_DIGESTS.reserved must be zero"}; return false; }
    out.object_id = rdU32(r + 4);
    std::memcpy(out.digest.data(), r + 8, 32);
    return true;
}

inline std::array<uint8_t, kContentDigestsBytes> encodeContentDigest(const ContentDigest &value)
{
    std::array<uint8_t, kContentDigestsBytes> data{};
    wrU16(data.data() + 0, value.object_kind);
    wrU16(data.data() + 2, value.reserved);
    wrU32(data.data() + 4, value.object_id);
    std::memcpy(data.data() + 8, value.digest.data(), 32);
    return data;
}

inline bool decodeProfileHint(const uint8_t *r, ProfileHint &out, AbiError &error)
{
    (void)error;
    out.entrypoint_id = rdU32(r + 0);
    out.profile_id = rdU32(r + 4);
    out.name_sid = rdU32(r + 8);
    out.value_sid = rdU32(r + 12);
    return true;
}

inline std::array<uint8_t, kProfileHintsBytes> encodeProfileHint(const ProfileHint &value)
{
    std::array<uint8_t, kProfileHintsBytes> data{};
    wrU32(data.data() + 0, value.entrypoint_id);
    wrU32(data.data() + 4, value.profile_id);
    wrU32(data.data() + 8, value.name_sid);
    wrU32(data.data() + 12, value.value_sid);
    return data;
}

inline bool decodeRepeatV1(const uint8_t *r, RepeatV1 &out, AbiError &error)
{
    (void)error;
    out.subrange_begin_stream_ordinal = rdU32(r + 0);
    out.subrange_command_count = rdU32(r + 4);
    out.repeat_count = rdU32(r + 8);
    out.flags = rdU32(r + 12);
    if (out.flags != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "REPEAT_V1.flags must equal 0"}; return false; }
    return true;
}

inline std::array<uint8_t, kRepeatV1Bytes> encodeRepeatV1(const RepeatV1 &value)
{
    std::array<uint8_t, kRepeatV1Bytes> data{};
    wrU32(data.data() + 0, value.subrange_begin_stream_ordinal);
    wrU32(data.data() + 4, value.subrange_command_count);
    wrU32(data.data() + 8, value.repeat_count);
    wrU32(data.data() + 12, value.flags);
    return data;
}

inline bool decodeGemmV1(const uint8_t *r, GemmV1 &out, AbiError &error)
{
    (void)error;
    out.batch = rdU32(r + 0);
    out.m = rdU32(r + 4);
    out.n = rdU32(r + 8);
    out.k = rdU32(r + 12);
    out.a_transpose = rdU8(r + 16);
    out.b_transpose = rdU8(r + 17);
    out.dtype = rdU16(r + 18);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "GEMM_V1.dtype not in closed set"}; return false; }
    out.accum_dtype = rdU16(r + 20);
    if (!validDtype(out.accum_dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "GEMM_V1.accum_dtype not in closed set"}; return false; }
    out.epilogue = rdU16(r + 22);
    out.efficiency_q16 = rdU32(r + 24);
    return true;
}

inline std::array<uint8_t, kGemmV1Bytes> encodeGemmV1(const GemmV1 &value)
{
    std::array<uint8_t, kGemmV1Bytes> data{};
    wrU32(data.data() + 0, value.batch);
    wrU32(data.data() + 4, value.m);
    wrU32(data.data() + 8, value.n);
    wrU32(data.data() + 12, value.k);
    data[16] = value.a_transpose;
    data[17] = value.b_transpose;
    wrU16(data.data() + 18, value.dtype);
    wrU16(data.data() + 20, value.accum_dtype);
    wrU16(data.data() + 22, value.epilogue);
    wrU32(data.data() + 24, value.efficiency_q16);
    return data;
}

inline bool decodeBmmV1(const uint8_t *r, BmmV1 &out, AbiError &error)
{
    (void)error;
    out.batch = rdU32(r + 0);
    out.m = rdU32(r + 4);
    out.n = rdU32(r + 8);
    out.k = rdU32(r + 12);
    out.a_transpose = rdU8(r + 16);
    out.b_transpose = rdU8(r + 17);
    out.dtype = rdU16(r + 18);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "BMM_V1.dtype not in closed set"}; return false; }
    out.accum_dtype = rdU16(r + 20);
    if (!validDtype(out.accum_dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "BMM_V1.accum_dtype not in closed set"}; return false; }
    out.epilogue = rdU16(r + 22);
    out.efficiency_q16 = rdU32(r + 24);
    return true;
}

inline std::array<uint8_t, kBmmV1Bytes> encodeBmmV1(const BmmV1 &value)
{
    std::array<uint8_t, kBmmV1Bytes> data{};
    wrU32(data.data() + 0, value.batch);
    wrU32(data.data() + 4, value.m);
    wrU32(data.data() + 8, value.n);
    wrU32(data.data() + 12, value.k);
    data[16] = value.a_transpose;
    data[17] = value.b_transpose;
    wrU16(data.data() + 18, value.dtype);
    wrU16(data.data() + 20, value.accum_dtype);
    wrU16(data.data() + 22, value.epilogue);
    wrU32(data.data() + 24, value.efficiency_q16);
    return data;
}

inline bool decodeElementwiseV1(const uint8_t *r, ElementwiseV1 &out, AbiError &error)
{
    (void)error;
    out.element_count = rdU64(r + 0);
    out.dtype = rdU16(r + 8);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "ELEMENTWISE_V1.dtype not in closed set"}; return false; }
    out.op = rdU16(r + 10);
    out.ops_per_element = rdU16(r + 12);
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ELEMENTWISE_V1.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kElementwiseV1Bytes> encodeElementwiseV1(const ElementwiseV1 &value)
{
    std::array<uint8_t, kElementwiseV1Bytes> data{};
    wrU64(data.data() + 0, value.element_count);
    wrU16(data.data() + 8, value.dtype);
    wrU16(data.data() + 10, value.op);
    wrU16(data.data() + 12, value.ops_per_element);
    wrU16(data.data() + 14, value.reserved);
    return data;
}

inline bool decodeReduceV1(const uint8_t *r, ReduceV1 &out, AbiError &error)
{
    (void)error;
    out.element_count = rdU64(r + 0);
    out.dtype = rdU16(r + 8);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "REDUCE_V1.dtype not in closed set"}; return false; }
    out.accum_dtype = rdU16(r + 10);
    if (!validDtype(out.accum_dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "REDUCE_V1.accum_dtype not in closed set"}; return false; }
    out.op = rdU16(r + 12);
    out.fan_in = rdU16(r + 14);
    return true;
}

inline std::array<uint8_t, kReduceV1Bytes> encodeReduceV1(const ReduceV1 &value)
{
    std::array<uint8_t, kReduceV1Bytes> data{};
    wrU64(data.data() + 0, value.element_count);
    wrU16(data.data() + 8, value.dtype);
    wrU16(data.data() + 10, value.accum_dtype);
    wrU16(data.data() + 12, value.op);
    wrU16(data.data() + 14, value.fan_in);
    return data;
}

inline bool decodeSoftmaxV1(const uint8_t *r, SoftmaxV1 &out, AbiError &error)
{
    (void)error;
    out.axis_size = rdU64(r + 0);
    out.dtype = rdU16(r + 8);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "SOFTMAX_V1.dtype not in closed set"}; return false; }
    out.algorithm = rdU16(r + 10);
    if (!validVectorAlgorithm(out.algorithm)) { error = {mesh_diagnostics::E_ABI_ENUM, "SOFTMAX_V1.algorithm not in closed set"}; return false; }
    out.reserved = rdU32(r + 12);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SOFTMAX_V1.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kSoftmaxV1Bytes> encodeSoftmaxV1(const SoftmaxV1 &value)
{
    std::array<uint8_t, kSoftmaxV1Bytes> data{};
    wrU64(data.data() + 0, value.axis_size);
    wrU16(data.data() + 8, value.dtype);
    wrU16(data.data() + 10, value.algorithm);
    wrU32(data.data() + 12, value.reserved);
    return data;
}

inline bool decodeNormV1(const uint8_t *r, NormV1 &out, AbiError &error)
{
    (void)error;
    out.element_count = rdU64(r + 0);
    out.dtype = rdU16(r + 8);
    if (!validDtype(out.dtype)) { error = {mesh_diagnostics::E_ABI_ENUM, "NORM_V1.dtype not in closed set"}; return false; }
    out.algorithm = rdU16(r + 10);
    if (!validVectorAlgorithm(out.algorithm)) { error = {mesh_diagnostics::E_ABI_ENUM, "NORM_V1.algorithm not in closed set"}; return false; }
    out.reserved = rdU32(r + 12);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "NORM_V1.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kNormV1Bytes> encodeNormV1(const NormV1 &value)
{
    std::array<uint8_t, kNormV1Bytes> data{};
    wrU64(data.data() + 0, value.element_count);
    wrU16(data.data() + 8, value.dtype);
    wrU16(data.data() + 10, value.algorithm);
    wrU32(data.data() + 12, value.reserved);
    return data;
}

inline bool decodeFillV1(const uint8_t *r, FillV1 &out, AbiError &error)
{
    (void)error;
    out.pattern = rdU64(r + 0);
    out.reserved = rdU64(r + 8);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FILL_V1.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kFillV1Bytes> encodeFillV1(const FillV1 &value)
{
    std::array<uint8_t, kFillV1Bytes> data{};
    wrU64(data.data() + 0, value.pattern);
    wrU64(data.data() + 8, value.reserved);
    return data;
}

inline bool decodeBlockedMnkLayoutV1(const uint8_t *r, BlockedMnkLayoutV1 &out, AbiError &error)
{
    (void)error;
    out.block_m = rdU32(r + 0);
    out.block_n = rdU32(r + 4);
    out.block_k = rdU32(r + 8);
    out.minor_to_major = rdU16(r + 12);
    out.reserved = rdU16(r + 14);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BLOCKED_MNK_LAYOUT_V1.reserved must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kBlockedMnkLayoutV1Bytes> encodeBlockedMnkLayoutV1(const BlockedMnkLayoutV1 &value)
{
    std::array<uint8_t, kBlockedMnkLayoutV1Bytes> data{};
    wrU32(data.data() + 0, value.block_m);
    wrU32(data.data() + 4, value.block_n);
    wrU32(data.data() + 8, value.block_k);
    wrU16(data.data() + 12, value.minor_to_major);
    wrU16(data.data() + 14, value.reserved);
    return data;
}

inline bool decodeRecvWaitV1(const uint8_t *r, RecvWaitV1 &out, AbiError &error)
{
    (void)error;
    out.transfer_id = rdU32(r + 0);
    out.reserved0 = rdU32(r + 4);
    if (out.reserved0 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RECV_WAIT_V1.reserved0 must be zero"}; return false; }
    out.reserved1 = rdU32(r + 8);
    if (out.reserved1 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RECV_WAIT_V1.reserved1 must be zero"}; return false; }
    out.reserved2 = rdU32(r + 12);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RECV_WAIT_V1.reserved2 must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kRecvWaitV1Bytes> encodeRecvWaitV1(const RecvWaitV1 &value)
{
    std::array<uint8_t, kRecvWaitV1Bytes> data{};
    wrU32(data.data() + 0, value.transfer_id);
    wrU32(data.data() + 4, value.reserved0);
    wrU32(data.data() + 8, value.reserved1);
    wrU32(data.data() + 12, value.reserved2);
    return data;
}

inline bool decodeFenceV1(const uint8_t *r, FenceV1 &out, AbiError &error)
{
    (void)error;
    out.fence_scope = rdU16(r + 0);
    if (!validFenceScope(out.fence_scope)) { error = {mesh_diagnostics::E_ABI_ENUM, "FENCE_V1.fence_scope not in closed set"}; return false; }
    out.reserved = rdU16(r + 2);
    if (out.reserved != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FENCE_V1.reserved must be zero"}; return false; }
    out.reserved0 = rdU32(r + 4);
    if (out.reserved0 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FENCE_V1.reserved0 must be zero"}; return false; }
    out.reserved1 = rdU32(r + 8);
    if (out.reserved1 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FENCE_V1.reserved1 must be zero"}; return false; }
    out.reserved2 = rdU32(r + 12);
    if (out.reserved2 != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "FENCE_V1.reserved2 must be zero"}; return false; }
    return true;
}

inline std::array<uint8_t, kFenceV1Bytes> encodeFenceV1(const FenceV1 &value)
{
    std::array<uint8_t, kFenceV1Bytes> data{};
    wrU16(data.data() + 0, value.fence_scope);
    wrU16(data.data() + 2, value.reserved);
    wrU32(data.data() + 4, value.reserved0);
    wrU32(data.data() + 8, value.reserved1);
    wrU32(data.data() + 12, value.reserved2);
    return data;
}

using AttrPayload = std::variant<std::monostate, RepeatV1, GemmV1, BmmV1, ElementwiseV1, ReduceV1, SoftmaxV1, NormV1, FillV1, BlockedMnkLayoutV1, RecvWaitV1, FenceV1>;

constexpr uint32_t attrPayloadBytes(uint16_t kind)
{
    switch (kind) {
    case kAttrKindREPEAT_V1: return kRepeatV1Bytes;
    case kAttrKindGEMM_V1: return kGemmV1Bytes;
    case kAttrKindBMM_V1: return kBmmV1Bytes;
    case kAttrKindELEMENTWISE_V1: return kElementwiseV1Bytes;
    case kAttrKindREDUCE_V1: return kReduceV1Bytes;
    case kAttrKindSOFTMAX_V1: return kSoftmaxV1Bytes;
    case kAttrKindNORM_V1: return kNormV1Bytes;
    case kAttrKindFILL_V1: return kFillV1Bytes;
    case kAttrKindBLOCKED_MNK_LAYOUT_V1: return kBlockedMnkLayoutV1Bytes;
    case kAttrKindRECV_WAIT_V1: return kRecvWaitV1Bytes;
    case kAttrKindFENCE_V1: return kFenceV1Bytes;
    default: return 0;
    }
}

inline bool decodeAttrPayload(uint16_t kind, const uint8_t *payload,
                               AttrPayload &out, AbiError &error)
{
    switch (kind) {
    case kAttrKindREPEAT_V1: out = RepeatV1{}; return decodeRepeatV1(payload, std::get<RepeatV1>(out), error);
    case kAttrKindGEMM_V1: out = GemmV1{}; return decodeGemmV1(payload, std::get<GemmV1>(out), error);
    case kAttrKindBMM_V1: out = BmmV1{}; return decodeBmmV1(payload, std::get<BmmV1>(out), error);
    case kAttrKindELEMENTWISE_V1: out = ElementwiseV1{}; return decodeElementwiseV1(payload, std::get<ElementwiseV1>(out), error);
    case kAttrKindREDUCE_V1: out = ReduceV1{}; return decodeReduceV1(payload, std::get<ReduceV1>(out), error);
    case kAttrKindSOFTMAX_V1: out = SoftmaxV1{}; return decodeSoftmaxV1(payload, std::get<SoftmaxV1>(out), error);
    case kAttrKindNORM_V1: out = NormV1{}; return decodeNormV1(payload, std::get<NormV1>(out), error);
    case kAttrKindFILL_V1: out = FillV1{}; return decodeFillV1(payload, std::get<FillV1>(out), error);
    case kAttrKindBLOCKED_MNK_LAYOUT_V1: out = BlockedMnkLayoutV1{}; return decodeBlockedMnkLayoutV1(payload, std::get<BlockedMnkLayoutV1>(out), error);
    case kAttrKindRECV_WAIT_V1: out = RecvWaitV1{}; return decodeRecvWaitV1(payload, std::get<RecvWaitV1>(out), error);
    case kAttrKindFENCE_V1: out = FenceV1{}; return decodeFenceV1(payload, std::get<FenceV1>(out), error);
    default:
        error = {mesh_diagnostics::E_ABI_ENUM, "unknown attr kind"};
        return false;
    }
}

#endif

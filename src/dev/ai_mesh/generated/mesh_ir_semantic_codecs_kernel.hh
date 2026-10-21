#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_KERNEL_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_KERNEL_HH

inline bool decodeAllocAttrs(const uint8_t *data, AllocAttrs &out, AbiError &error)
{
    out = AllocAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "AllocAttrs presence mask has undeclared bits"}; return false; }
    out.object_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kAllocAttrsBytes> encodeAllocAttrs(const AllocAttrs &value)
{
    std::array<uint8_t, kAllocAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.object_id);
    return data;
}

inline bool decodeBarrierAttrs(const uint8_t *data, BarrierAttrs &out, AbiError &error)
{
    out = BarrierAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BarrierAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.participants, error)) return false;
    return true;
}

inline std::array<uint8_t, kBarrierAttrsBytes> encodeBarrierAttrs(const BarrierAttrs &value)
{
    std::array<uint8_t, kBarrierAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.participants);
    return data;
}

inline bool decodeBlockedMnkLayout(const uint8_t *data, BlockedMnkLayout &out, AbiError &error)
{
    out = BlockedMnkLayout{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BlockedMnkLayout presence mask has undeclared bits"}; return false; }
    out.block_m = mesh_abi::rdU64(data + 8);
    out.block_n = mesh_abi::rdU64(data + 16);
    out.block_k = mesh_abi::rdU64(data + 24);
    if (!decodeListSpan(data + 32, out.minor_to_major, error)) return false;
    return true;
}

inline std::array<uint8_t, kBlockedMnkLayoutBytes> encodeBlockedMnkLayout(const BlockedMnkLayout &value)
{
    std::array<uint8_t, kBlockedMnkLayoutBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.block_m);
    mesh_abi::wrU64(data.data() + 16, value.block_n);
    mesh_abi::wrU64(data.data() + 24, value.block_k);
    encodeListSpan(data.data() + 32, value.minor_to_major);
    return data;
}

inline bool decodeBufferObject(const uint8_t *data, BufferObject &out, AbiError &error)
{
    out = BufferObject{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferObject presence mask has undeclared bits"}; return false; }
    out.object_id = mesh_abi::rdU64(data + 8);
    out.storage_tensor_id = mesh_abi::rdU64(data + 16);
    out.owner_core = mesh_abi::rdU64(data + 24);
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferObject.memory_space reserved bytes are nonzero"}; return false; }
    if (!validMemorySpace(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "BufferObject.memory_space is invalid"}; return false; }
    out.memory_space = static_cast<MemorySpace>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    if (!decodeListSpan(data + 40, out.shape, error)) return false;
    if (!decodeListSpan(data + 48, out.strides, error)) return false;
    out.footprint_bytes = mesh_abi::rdU64(data + 56);
    out.alignment_bytes = mesh_abi::rdU64(data + 64);
    if (mesh_abi::rdU64(data + 72) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "BufferObject.persistent is not boolean"}; return false; }
    out.persistent = mesh_abi::rdU64(data + 72) != 0;
    out.buffer_index = mesh_abi::rdU64(data + 80);
    return true;
}

inline std::array<uint8_t, kBufferObjectBytes> encodeBufferObject(const BufferObject &value)
{
    std::array<uint8_t, kBufferObjectBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.object_id);
    mesh_abi::wrU64(data.data() + 16, value.storage_tensor_id);
    mesh_abi::wrU64(data.data() + 24, value.owner_core);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.memory_space));
    encodeListSpan(data.data() + 40, value.shape);
    encodeListSpan(data.data() + 48, value.strides);
    mesh_abi::wrU64(data.data() + 56, value.footprint_bytes);
    mesh_abi::wrU64(data.data() + 64, value.alignment_bytes);
    mesh_abi::wrU64(data.data() + 72, value.persistent ? 1 : 0);
    mesh_abi::wrU64(data.data() + 80, value.buffer_index);
    return data;
}

inline bool decodeBufferView(const uint8_t *data, BufferView &out, AbiError &error)
{
    out = BufferView{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(768)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferView presence mask has undeclared bits"}; return false; }
    out.view_id = mesh_abi::rdU64(data + 8);
    out.object_id = mesh_abi::rdU64(data + 16);
    out.shard_id = mesh_abi::rdU64(data + 24);
    if (!decodeListSpan(data + 32, out.shard_origin, error)) return false;
    if (!decodeListSpan(data + 40, out.padded_shape, error)) return false;
    if (!decodeListSpan(data + 48, out.valid_shape, error)) return false;
    out.object_offset_elements = mesh_abi::rdU64(data + 56);
    if (!decodeListSpan(data + 64, out.object_strides, error)) return false;
    if ((out.presence_mask & (uint64_t(1) << 8)) == 0) {
        if (!zeroBytes(data + 72, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferView.layout absent bytes are nonzero"}; return false; }
    } else {
        if (mesh_abi::rdU32(data + 72 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferView.layout reserved bytes are nonzero"}; return false; }
        if (!validLayout(mesh_abi::rdU32(data + 72 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "BufferView.layout is invalid"}; return false; }
        out.layout = static_cast<Layout>(mesh_abi::rdU32(data + 72 + kEnumValueValueOffset));
    }
    if ((out.presence_mask & (uint64_t(1) << 9)) == 0) {
        if (!zeroBytes(data + 80, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "BufferView.blocked_layout absent bytes are nonzero"}; return false; }
    } else {
        if (!decodeSemanticRef(data + 80, out.blocked_layout, error)) return false;
        if (out.blocked_layout.row_id == 0 || (out.blocked_layout.section_type != 274)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "BufferView.blocked_layout target is invalid"}; return false; }
    }
    out.generation = mesh_abi::rdU64(data + 88);
    return true;
}

inline std::array<uint8_t, kBufferViewBytes> encodeBufferView(const BufferView &value)
{
    std::array<uint8_t, kBufferViewBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.view_id);
    mesh_abi::wrU64(data.data() + 16, value.object_id);
    mesh_abi::wrU64(data.data() + 24, value.shard_id);
    encodeListSpan(data.data() + 32, value.shard_origin);
    encodeListSpan(data.data() + 40, value.padded_shape);
    encodeListSpan(data.data() + 48, value.valid_shape);
    mesh_abi::wrU64(data.data() + 56, value.object_offset_elements);
    encodeListSpan(data.data() + 64, value.object_strides);
    if ((value.presence_mask & (uint64_t(1) << 8)) != 0) {
        mesh_abi::wrU32(data.data() + 72 + kEnumValueValueOffset, static_cast<uint32_t>(value.layout));
    }
    if ((value.presence_mask & (uint64_t(1) << 9)) != 0) {
        encodeSemanticRef(data.data() + 80, value.blocked_layout);
    }
    mesh_abi::wrU64(data.data() + 88, value.generation);
    return data;
}

inline bool decodeCollectiveAttrs(const uint8_t *data, CollectiveAttrs &out, AbiError &error)
{
    out = CollectiveAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CollectiveAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CollectiveAttrs.kind reserved bytes are nonzero"}; return false; }
    if (!validCollectiveKind(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "CollectiveAttrs.kind is invalid"}; return false; }
    out.kind = static_cast<CollectiveKind>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeListSpan(data + 16, out.participants, error)) return false;
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CollectiveAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validCollectiveAlgorithm(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "CollectiveAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<CollectiveAlgorithm>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    out.chunk_bytes = mesh_abi::rdU64(data + 32);
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CollectiveAttrs.reduce_kind reserved bytes are nonzero"}; return false; }
    if (!validReduceKind(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "CollectiveAttrs.reduce_kind is invalid"}; return false; }
    out.reduce_kind = static_cast<ReduceKind>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    out.partial_sum_id = mesh_abi::rdU64(data + 48);
    return true;
}

inline std::array<uint8_t, kCollectiveAttrsBytes> encodeCollectiveAttrs(const CollectiveAttrs &value)
{
    std::array<uint8_t, kCollectiveAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.kind));
    encodeListSpan(data.data() + 16, value.participants);
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    mesh_abi::wrU64(data.data() + 32, value.chunk_bytes);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.reduce_kind));
    mesh_abi::wrU64(data.data() + 48, value.partial_sum_id);
    return data;
}

inline bool decodeControlToken(const uint8_t *data, ControlToken &out, AbiError &error)
{
    out = ControlToken{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ControlToken presence mask has undeclared bits"}; return false; }
    out.token_id = mesh_abi::rdU64(data + 8);
    if (mesh_abi::rdU64(data + 16) > 1) { error = {mesh_diagnostics::E_ABI_ENUM, "ControlToken.initial is not boolean"}; return false; }
    out.initial = mesh_abi::rdU64(data + 16) != 0;
    return true;
}

inline std::array<uint8_t, kControlTokenBytes> encodeControlToken(const ControlToken &value)
{
    std::array<uint8_t, kControlTokenBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.token_id);
    mesh_abi::wrU64(data.data() + 16, value.initial ? 1 : 0);
    return data;
}

inline bool decodeDmaAttrs(const uint8_t *data, DmaAttrs &out, AbiError &error)
{
    out = DmaAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(64)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DmaAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DmaAttrs.kind reserved bytes are nonzero"}; return false; }
    if (!validDmaKind(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DmaAttrs.kind is invalid"}; return false; }
    out.kind = static_cast<DmaKind>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    out.issuing_core = mesh_abi::rdU64(data + 16);
    out.source_core = mesh_abi::rdU64(data + 24);
    out.destination_core = mesh_abi::rdU64(data + 32);
    out.transfer_id = mesh_abi::rdU64(data + 40);
    if (!decodeListSpan(data + 48, out.fill_pattern, error)) return false;
    if ((out.presence_mask & (uint64_t(1) << 6)) == 0) {
        if (!zeroBytes(data + 56, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "DmaAttrs.max_burst_beats absent bytes are nonzero"}; return false; }
    } else {
        out.max_burst_beats = mesh_abi::rdU64(data + 56);
    }
    return true;
}

inline std::array<uint8_t, kDmaAttrsBytes> encodeDmaAttrs(const DmaAttrs &value)
{
    std::array<uint8_t, kDmaAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.kind));
    mesh_abi::wrU64(data.data() + 16, value.issuing_core);
    mesh_abi::wrU64(data.data() + 24, value.source_core);
    mesh_abi::wrU64(data.data() + 32, value.destination_core);
    mesh_abi::wrU64(data.data() + 40, value.transfer_id);
    encodeListSpan(data.data() + 48, value.fill_pattern);
    if ((value.presence_mask & (uint64_t(1) << 6)) != 0) {
        mesh_abi::wrU64(data.data() + 56, value.max_burst_beats);
    }
    return data;
}

inline bool decodeElementRegion(const uint8_t *data, ElementRegion &out, AbiError &error)
{
    out = ElementRegion{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ElementRegion presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.origin, error)) return false;
    if (!decodeListSpan(data + 16, out.shape, error)) return false;
    if (!decodeListSpan(data + 24, out.steps, error)) return false;
    return true;
}

inline std::array<uint8_t, kElementRegionBytes> encodeElementRegion(const ElementRegion &value)
{
    std::array<uint8_t, kElementRegionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.origin);
    encodeListSpan(data.data() + 16, value.shape);
    encodeListSpan(data.data() + 24, value.steps);
    return data;
}

inline bool decodeGemmKernelAttrs(const uint8_t *data, GemmKernelAttrs &out, AbiError &error)
{
    out = GemmKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "GemmKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "GemmKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "GemmKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 266)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "GemmKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "GemmKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "GemmKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "GemmKernelAttrs.phase reserved bytes are nonzero"}; return false; }
    if (!validMatrixPhase(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "GemmKernelAttrs.phase is invalid"}; return false; }
    out.phase = static_cast<MatrixPhase>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    out.partial_sum_id = mesh_abi::rdU64(data + 48);
    return true;
}

inline std::array<uint8_t, kGemmKernelAttrsBytes> encodeGemmKernelAttrs(const GemmKernelAttrs &value)
{
    std::array<uint8_t, kGemmKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.phase));
    mesh_abi::wrU64(data.data() + 48, value.partial_sum_id);
    return data;
}

inline bool decodeKernelComputation(const uint8_t *data, KernelComputation &out, AbiError &error)
{
    out = KernelComputation{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelComputation presence mask has undeclared bits"}; return false; }
    out.computation_id = mesh_abi::rdU64(data + 8);
    if (mesh_abi::rdU32(data + 16 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelComputation.opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelComputation.opcode is invalid"}; return false; }
    out.opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 16 + kEnumValueValueOffset));
    if (!decodeListSpan(data + 24, out.operand_tensor_ids, error)) return false;
    out.result_tensor_id = mesh_abi::rdU64(data + 32);
    if (!decodeSemanticRef(data + 40, out.attrs, error)) return false;
    if (out.attrs.row_id == 0 || (out.attrs.section_type != 266 && out.attrs.section_type != 271 && out.attrs.section_type != 267 && out.attrs.section_type != 264 && out.attrs.section_type != 269 && out.attrs.section_type != 268 && out.attrs.section_type != 270 && out.attrs.section_type != 265)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "KernelComputation.attrs target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kKernelComputationBytes> encodeKernelComputation(const KernelComputation &value)
{
    std::array<uint8_t, kKernelComputationBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.computation_id);
    mesh_abi::wrU32(data.data() + 16 + kEnumValueValueOffset, static_cast<uint32_t>(value.opcode));
    encodeListSpan(data.data() + 24, value.operand_tensor_ids);
    mesh_abi::wrU64(data.data() + 32, value.result_tensor_id);
    encodeSemanticRef(data.data() + 40, value.attrs);
    return data;
}

inline bool decodeKernelCost(const uint8_t *data, KernelCost &out, AbiError &error)
{
    out = KernelCost{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelCost presence mask has undeclared bits"}; return false; }
    out.logical_input_bytes = mesh_abi::rdU64(data + 8);
    out.logical_output_bytes = mesh_abi::rdU64(data + 16);
    out.local_storage_bytes = mesh_abi::rdU64(data + 24);
    out.macs = mesh_abi::rdU64(data + 32);
    out.vector_ops = mesh_abi::rdU64(data + 40);
    out.reduction_ops = mesh_abi::rdU64(data + 48);
    return true;
}

inline std::array<uint8_t, kKernelCostBytes> encodeKernelCost(const KernelCost &value)
{
    std::array<uint8_t, kKernelCostBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.logical_input_bytes);
    mesh_abi::wrU64(data.data() + 16, value.logical_output_bytes);
    mesh_abi::wrU64(data.data() + 24, value.local_storage_bytes);
    mesh_abi::wrU64(data.data() + 32, value.macs);
    mesh_abi::wrU64(data.data() + 40, value.vector_ops);
    mesh_abi::wrU64(data.data() + 48, value.reduction_ops);
    return data;
}

inline bool decodeKernelOp(const uint8_t *data, KernelOp &out, AbiError &error)
{
    out = KernelOp{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(1024)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelOp presence mask has undeclared bits"}; return false; }
    out.op_id = mesh_abi::rdU64(data + 8);
    out.computation_id = mesh_abi::rdU64(data + 16);
    if (!decodeStringRef(data + 24, out.stable_key, error)) return false;
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelOp.opcode reserved bytes are nonzero"}; return false; }
    if (!validKernelOpcode(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelOp.opcode is invalid"}; return false; }
    out.opcode = static_cast<KernelOpcode>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    out.owner_core = mesh_abi::rdU64(data + 40);
    out.result_shard_id = mesh_abi::rdU64(data + 48);
    if (!decodeListSpan(data + 56, out.reads, error)) return false;
    if (!decodeListSpan(data + 64, out.writes, error)) return false;
    if (!decodeSemanticRef(data + 72, out.attrs, error)) return false;
    if (out.attrs.row_id == 0 || (out.attrs.section_type != 272 && out.attrs.section_type != 302 && out.attrs.section_type != 279 && out.attrs.section_type != 295 && out.attrs.section_type != 281 && out.attrs.section_type != 289 && out.attrs.section_type != 301 && out.attrs.section_type != 290 && out.attrs.section_type != 296 && out.attrs.section_type != 297 && out.attrs.section_type != 291 && out.attrs.section_type != 277 && out.attrs.section_type != 288 && out.attrs.section_type != 287 && out.attrs.section_type != 273)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "KernelOp.attrs target is invalid"}; return false; }
    if (!decodeListSpan(data + 80, out.after_tokens, error)) return false;
    if ((out.presence_mask & (uint64_t(1) << 10)) == 0) {
        if (!zeroBytes(data + 88, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelOp.done_token absent bytes are nonzero"}; return false; }
    } else {
        out.done_token = mesh_abi::rdU64(data + 88);
    }
    return true;
}

inline std::array<uint8_t, kKernelOpBytes> encodeKernelOp(const KernelOp &value)
{
    std::array<uint8_t, kKernelOpBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.op_id);
    mesh_abi::wrU64(data.data() + 16, value.computation_id);
    encodeStringRef(data.data() + 24, value.stable_key);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.opcode));
    mesh_abi::wrU64(data.data() + 40, value.owner_core);
    mesh_abi::wrU64(data.data() + 48, value.result_shard_id);
    encodeListSpan(data.data() + 56, value.reads);
    encodeListSpan(data.data() + 64, value.writes);
    encodeSemanticRef(data.data() + 72, value.attrs);
    encodeListSpan(data.data() + 80, value.after_tokens);
    if ((value.presence_mask & (uint64_t(1) << 10)) != 0) {
        mesh_abi::wrU64(data.data() + 88, value.done_token);
    }
    return data;
}

inline bool decodeKernelTensor(const uint8_t *data, KernelTensor &out, AbiError &error)
{
    out = KernelTensor{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(16388)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor presence mask has undeclared bits"}; return false; }
    out.tensor_id = mesh_abi::rdU64(data + 8);
    out.producer_computation_id = mesh_abi::rdU64(data + 16);
    if ((out.presence_mask & (uint64_t(1) << 2)) == 0) {
        if (!zeroBytes(data + 24, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.synthesized_purpose absent bytes are nonzero"}; return false; }
    } else {
        if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.synthesized_purpose reserved bytes are nonzero"}; return false; }
        if (!validSynthesizedTensorPurpose(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelTensor.synthesized_purpose is invalid"}; return false; }
        out.synthesized_purpose = static_cast<SynthesizedTensorPurpose>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    }
    out.alias_root_tensor_id = mesh_abi::rdU64(data + 32);
    out.storage_offset_elements = mesh_abi::rdU64(data + 40);
    if (!decodeStringRef(data + 48, out.name, error)) return false;
    if (mesh_abi::rdU32(data + 56 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.role reserved bytes are nonzero"}; return false; }
    if (!validTensorRole(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelTensor.role is invalid"}; return false; }
    out.role = static_cast<TensorRole>(mesh_abi::rdU32(data + 56 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 64 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.dtype reserved bytes are nonzero"}; return false; }
    if (!validDType(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelTensor.dtype is invalid"}; return false; }
    out.dtype = static_cast<DType>(mesh_abi::rdU32(data + 64 + kEnumValueValueOffset));
    if (!decodeListSpan(data + 72, out.shape, error)) return false;
    if (!decodeListSpan(data + 80, out.strides, error)) return false;
    if (mesh_abi::rdU32(data + 88 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.storage_class reserved bytes are nonzero"}; return false; }
    if (!validStorageClass(mesh_abi::rdU32(data + 88 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelTensor.storage_class is invalid"}; return false; }
    out.storage_class = static_cast<StorageClass>(mesh_abi::rdU32(data + 88 + kEnumValueValueOffset));
    if (mesh_abi::rdU32(data + 96 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.access reserved bytes are nonzero"}; return false; }
    if (!validAccess(mesh_abi::rdU32(data + 96 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "KernelTensor.access is invalid"}; return false; }
    out.access = static_cast<Access>(mesh_abi::rdU32(data + 96 + kEnumValueValueOffset));
    out.logical_extent_bytes = mesh_abi::rdU64(data + 104);
    out.storage_extent_bytes = mesh_abi::rdU64(data + 112);
    if ((out.presence_mask & (uint64_t(1) << 14)) == 0) {
        if (!zeroBytes(data + 120, 8)) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTensor.content_sha256 absent bytes are nonzero"}; return false; }
    } else {
        if (!decodeStringRef(data + 120, out.content_sha256, error)) return false;
    }
    return true;
}

inline std::array<uint8_t, kKernelTensorBytes> encodeKernelTensor(const KernelTensor &value)
{
    std::array<uint8_t, kKernelTensorBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.tensor_id);
    mesh_abi::wrU64(data.data() + 16, value.producer_computation_id);
    if ((value.presence_mask & (uint64_t(1) << 2)) != 0) {
        mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.synthesized_purpose));
    }
    mesh_abi::wrU64(data.data() + 32, value.alias_root_tensor_id);
    mesh_abi::wrU64(data.data() + 40, value.storage_offset_elements);
    encodeStringRef(data.data() + 48, value.name);
    mesh_abi::wrU32(data.data() + 56 + kEnumValueValueOffset, static_cast<uint32_t>(value.role));
    mesh_abi::wrU32(data.data() + 64 + kEnumValueValueOffset, static_cast<uint32_t>(value.dtype));
    encodeListSpan(data.data() + 72, value.shape);
    encodeListSpan(data.data() + 80, value.strides);
    mesh_abi::wrU32(data.data() + 88 + kEnumValueValueOffset, static_cast<uint32_t>(value.storage_class));
    mesh_abi::wrU32(data.data() + 96 + kEnumValueValueOffset, static_cast<uint32_t>(value.access));
    mesh_abi::wrU64(data.data() + 104, value.logical_extent_bytes);
    mesh_abi::wrU64(data.data() + 112, value.storage_extent_bytes);
    if ((value.presence_mask & (uint64_t(1) << 14)) != 0) {
        encodeStringRef(data.data() + 120, value.content_sha256);
    }
    return data;
}

inline bool decodeKernelTile(const uint8_t *data, KernelTile &out, AbiError &error)
{
    out = KernelTile{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTile presence mask has undeclared bits"}; return false; }
    out.batch_origin = mesh_abi::rdU64(data + 8);
    out.m_origin = mesh_abi::rdU64(data + 16);
    out.n_origin = mesh_abi::rdU64(data + 24);
    out.k_origin = mesh_abi::rdU64(data + 32);
    out.batch_extent = mesh_abi::rdU64(data + 40);
    out.m_extent = mesh_abi::rdU64(data + 48);
    out.n_extent = mesh_abi::rdU64(data + 56);
    out.k_extent = mesh_abi::rdU64(data + 64);
    out.valid_batch = mesh_abi::rdU64(data + 72);
    out.valid_m = mesh_abi::rdU64(data + 80);
    out.valid_n = mesh_abi::rdU64(data + 88);
    out.valid_k = mesh_abi::rdU64(data + 96);
    return true;
}

inline std::array<uint8_t, kKernelTileBytes> encodeKernelTile(const KernelTile &value)
{
    std::array<uint8_t, kKernelTileBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.batch_origin);
    mesh_abi::wrU64(data.data() + 16, value.m_origin);
    mesh_abi::wrU64(data.data() + 24, value.n_origin);
    mesh_abi::wrU64(data.data() + 32, value.k_origin);
    mesh_abi::wrU64(data.data() + 40, value.batch_extent);
    mesh_abi::wrU64(data.data() + 48, value.m_extent);
    mesh_abi::wrU64(data.data() + 56, value.n_extent);
    mesh_abi::wrU64(data.data() + 64, value.k_extent);
    mesh_abi::wrU64(data.data() + 72, value.valid_batch);
    mesh_abi::wrU64(data.data() + 80, value.valid_m);
    mesh_abi::wrU64(data.data() + 88, value.valid_n);
    mesh_abi::wrU64(data.data() + 96, value.valid_k);
    return data;
}

inline bool decodeLocalCopyAttrs(const uint8_t *data, LocalCopyAttrs &out, AbiError &error)
{
    out = LocalCopyAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "LocalCopyAttrs presence mask has undeclared bits"}; return false; }
    return true;
}

inline std::array<uint8_t, kLocalCopyAttrsBytes> encodeLocalCopyAttrs(const LocalCopyAttrs &value)
{
    std::array<uint8_t, kLocalCopyAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    return data;
}

inline bool decodeLocalReduceAttrs(const uint8_t *data, LocalReduceAttrs &out, AbiError &error)
{
    out = LocalReduceAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "LocalReduceAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "LocalReduceAttrs.reduce_kind reserved bytes are nonzero"}; return false; }
    if (!validReduceKind(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "LocalReduceAttrs.reduce_kind is invalid"}; return false; }
    out.reduce_kind = static_cast<ReduceKind>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    out.partial_sum_id = mesh_abi::rdU64(data + 16);
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "LocalReduceAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "LocalReduceAttrs.cost target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kLocalReduceAttrsBytes> encodeLocalReduceAttrs(const LocalReduceAttrs &value)
{
    std::array<uint8_t, kLocalReduceAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.reduce_kind));
    mesh_abi::wrU64(data.data() + 16, value.partial_sum_id);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    return data;
}

inline bool decodeMatrixEpilogueKernelAttrs(const uint8_t *data, MatrixEpilogueKernelAttrs &out, AbiError &error)
{
    out = MatrixEpilogueKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MatrixEpilogueKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MatrixEpilogueKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "MatrixEpilogueKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 266)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MatrixEpilogueKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MatrixEpilogueKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MatrixEpilogueKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MatrixEpilogueKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validMatrixEpilogueAlgorithm(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "MatrixEpilogueKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<MatrixEpilogueAlgorithm>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kMatrixEpilogueKernelAttrsBytes> encodeMatrixEpilogueKernelAttrs(const MatrixEpilogueKernelAttrs &value)
{
    std::array<uint8_t, kMatrixEpilogueKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeMovementKernelAttrs(const uint8_t *data, MovementKernelAttrs &out, AbiError &error)
{
    out = MovementKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MovementKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MovementKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "MovementKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 271 && out.semantic_attrs.section_type != 267)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MovementKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MovementKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "MovementKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "MovementKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validMovementAlgorithm(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "MovementKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<MovementAlgorithm>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kMovementKernelAttrsBytes> encodeMovementKernelAttrs(const MovementKernelAttrs &value)
{
    std::array<uint8_t, kMovementKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeNormKernelAttrs(const uint8_t *data, NormKernelAttrs &out, AbiError &error)
{
    out = NormKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "NormKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "NormKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "NormKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 268)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "NormKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "NormKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "NormKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "NormKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validNormAlgorithm(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "NormKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<NormAlgorithm>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kNormKernelAttrsBytes> encodeNormKernelAttrs(const NormKernelAttrs &value)
{
    std::array<uint8_t, kNormKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeOperandAccess(const uint8_t *data, OperandAccess &out, AbiError &error)
{
    out = OperandAccess{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "OperandAccess presence mask has undeclared bits"}; return false; }
    out.state_id = mesh_abi::rdU64(data + 8);
    out.view_id = mesh_abi::rdU64(data + 16);
    if (!decodeSemanticRef(data + 24, out.region, error)) return false;
    if (out.region.row_id == 0 || (out.region.section_type != 280)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "OperandAccess.region target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "OperandAccess.mode reserved bytes are nonzero"}; return false; }
    if (!validOperandAccessMode(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "OperandAccess.mode is invalid"}; return false; }
    out.mode = static_cast<OperandAccessMode>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kOperandAccessBytes> encodeOperandAccess(const OperandAccess &value)
{
    std::array<uint8_t, kOperandAccessBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.state_id);
    mesh_abi::wrU64(data.data() + 16, value.view_id);
    encodeSemanticRef(data.data() + 24, value.region);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.mode));
    return data;
}

inline bool decodePartialSumDefinition(const uint8_t *data, PartialSumDefinition &out, AbiError &error)
{
    out = PartialSumDefinition{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "PartialSumDefinition presence mask has undeclared bits"}; return false; }
    out.partial_sum_id = mesh_abi::rdU64(data + 8);
    out.computation_id = mesh_abi::rdU64(data + 16);
    out.semantic_result_tensor_id = mesh_abi::rdU64(data + 24);
    out.accumulator_tensor_id = mesh_abi::rdU64(data + 32);
    out.placement_id = mesh_abi::rdU64(data + 40);
    return true;
}

inline std::array<uint8_t, kPartialSumDefinitionBytes> encodePartialSumDefinition(const PartialSumDefinition &value)
{
    std::array<uint8_t, kPartialSumDefinitionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.partial_sum_id);
    mesh_abi::wrU64(data.data() + 16, value.computation_id);
    mesh_abi::wrU64(data.data() + 24, value.semantic_result_tensor_id);
    mesh_abi::wrU64(data.data() + 32, value.accumulator_tensor_id);
    mesh_abi::wrU64(data.data() + 40, value.placement_id);
    return data;
}

inline bool decodePlacement(const uint8_t *data, Placement &out, AbiError &error)
{
    out = Placement{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "Placement presence mask has undeclared bits"}; return false; }
    out.placement_id = mesh_abi::rdU64(data + 8);
    if (!decodeListSpan(data + 16, out.core_ids, error)) return false;
    return true;
}

inline std::array<uint8_t, kPlacementBytes> encodePlacement(const Placement &value)
{
    std::array<uint8_t, kPlacementBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.placement_id);
    encodeListSpan(data.data() + 16, value.core_ids);
    return data;
}

inline bool decodeRecvWaitAttrs(const uint8_t *data, RecvWaitAttrs &out, AbiError &error)
{
    out = RecvWaitAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RecvWaitAttrs presence mask has undeclared bits"}; return false; }
    out.transfer_id = mesh_abi::rdU64(data + 8);
    out.source_core = mesh_abi::rdU64(data + 16);
    out.destination_core = mesh_abi::rdU64(data + 24);
    out.expected_bytes = mesh_abi::rdU64(data + 32);
    return true;
}

inline std::array<uint8_t, kRecvWaitAttrsBytes> encodeRecvWaitAttrs(const RecvWaitAttrs &value)
{
    std::array<uint8_t, kRecvWaitAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.transfer_id);
    mesh_abi::wrU64(data.data() + 16, value.source_core);
    mesh_abi::wrU64(data.data() + 24, value.destination_core);
    mesh_abi::wrU64(data.data() + 32, value.expected_bytes);
    return data;
}

inline bool decodeReductionKernelAttrs(const uint8_t *data, ReductionKernelAttrs &out, AbiError &error)
{
    out = ReductionKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReductionKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReductionKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ReductionKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 269)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ReductionKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ReductionKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ReductionKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReductionKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validReductionAlgorithm(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ReductionKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<ReductionAlgorithm>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kReductionKernelAttrsBytes> encodeReductionKernelAttrs(const ReductionKernelAttrs &value)
{
    std::array<uint8_t, kReductionKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeSoftmaxKernelAttrs(const uint8_t *data, SoftmaxKernelAttrs &out, AbiError &error)
{
    out = SoftmaxKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SoftmaxKernelAttrs presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 270)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "SoftmaxKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 16, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "SoftmaxKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "SoftmaxKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "SoftmaxKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validSoftmaxAlgorithm(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "SoftmaxKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<SoftmaxAlgorithm>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kSoftmaxKernelAttrsBytes> encodeSoftmaxKernelAttrs(const SoftmaxKernelAttrs &value)
{
    std::array<uint8_t, kSoftmaxKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.semantic_attrs);
    encodeSemanticRef(data.data() + 16, value.tile);
    encodeSemanticRef(data.data() + 24, value.cost);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeStateTransition(const uint8_t *data, StateTransition &out, AbiError &error)
{
    out = StateTransition{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "StateTransition presence mask has undeclared bits"}; return false; }
    out.old_state_id = mesh_abi::rdU64(data + 8);
    out.new_state_id = mesh_abi::rdU64(data + 16);
    out.view_id = mesh_abi::rdU64(data + 24);
    if (!decodeSemanticRef(data + 32, out.region, error)) return false;
    if (out.region.row_id == 0 || (out.region.section_type != 280)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "StateTransition.region target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "StateTransition.mode reserved bytes are nonzero"}; return false; }
    if (!validOperandAccessMode(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "StateTransition.mode is invalid"}; return false; }
    out.mode = static_cast<OperandAccessMode>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kStateTransitionBytes> encodeStateTransition(const StateTransition &value)
{
    std::array<uint8_t, kStateTransitionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.old_state_id);
    mesh_abi::wrU64(data.data() + 16, value.new_state_id);
    mesh_abi::wrU64(data.data() + 24, value.view_id);
    encodeSemanticRef(data.data() + 32, value.region);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.mode));
    return data;
}

inline bool decodeTensorShard(const uint8_t *data, TensorShard &out, AbiError &error)
{
    out = TensorShard{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TensorShard presence mask has undeclared bits"}; return false; }
    out.shard_id = mesh_abi::rdU64(data + 8);
    out.tensor_id = mesh_abi::rdU64(data + 16);
    out.placement_id = mesh_abi::rdU64(data + 24);
    out.owner_core = mesh_abi::rdU64(data + 32);
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TensorShard.distribution reserved bytes are nonzero"}; return false; }
    if (!validDistributionKind(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TensorShard.distribution is invalid"}; return false; }
    out.distribution = static_cast<DistributionKind>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    if (!decodeListSpan(data + 48, out.global_origin, error)) return false;
    if (!decodeListSpan(data + 56, out.padded_local_shape, error)) return false;
    if (!decodeListSpan(data + 64, out.valid_shape, error)) return false;
    out.partial_sum_id = mesh_abi::rdU64(data + 72);
    return true;
}

inline std::array<uint8_t, kTensorShardBytes> encodeTensorShard(const TensorShard &value)
{
    std::array<uint8_t, kTensorShardBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.shard_id);
    mesh_abi::wrU64(data.data() + 16, value.tensor_id);
    mesh_abi::wrU64(data.data() + 24, value.placement_id);
    mesh_abi::wrU64(data.data() + 32, value.owner_core);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.distribution));
    encodeListSpan(data.data() + 48, value.global_origin);
    encodeListSpan(data.data() + 56, value.padded_local_shape);
    encodeListSpan(data.data() + 64, value.valid_shape);
    mesh_abi::wrU64(data.data() + 72, value.partial_sum_id);
    return data;
}

inline bool decodeTensorState(const uint8_t *data, TensorState &out, AbiError &error)
{
    out = TensorState{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TensorState presence mask has undeclared bits"}; return false; }
    out.state_id = mesh_abi::rdU64(data + 8);
    out.object_id = mesh_abi::rdU64(data + 16);
    out.version = mesh_abi::rdU64(data + 24);
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "TensorState.origin reserved bytes are nonzero"}; return false; }
    if (!validStateOrigin(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "TensorState.origin is invalid"}; return false; }
    out.origin = static_cast<StateOrigin>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    out.partial_sum_id = mesh_abi::rdU64(data + 40);
    return true;
}

inline std::array<uint8_t, kTensorStateBytes> encodeTensorState(const TensorState &value)
{
    std::array<uint8_t, kTensorStateBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.state_id);
    mesh_abi::wrU64(data.data() + 16, value.object_id);
    mesh_abi::wrU64(data.data() + 24, value.version);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.origin));
    mesh_abi::wrU64(data.data() + 40, value.partial_sum_id);
    return data;
}

inline bool decodeVectorKernelAttrs(const uint8_t *data, VectorKernelAttrs &out, AbiError &error)
{
    out = VectorKernelAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "VectorKernelAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "VectorKernelAttrs.graph_opcode reserved bytes are nonzero"}; return false; }
    if (!validOpCode(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "VectorKernelAttrs.graph_opcode is invalid"}; return false; }
    out.graph_opcode = static_cast<OpCode>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 16, out.semantic_attrs, error)) return false;
    if (out.semantic_attrs.row_id == 0 || (out.semantic_attrs.section_type != 264 && out.semantic_attrs.section_type != 265)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VectorKernelAttrs.semantic_attrs target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.tile, error)) return false;
    if (out.tile.row_id == 0 || (out.tile.section_type != 286)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VectorKernelAttrs.tile target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.cost, error)) return false;
    if (out.cost.row_id == 0 || (out.cost.section_type != 283)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VectorKernelAttrs.cost target is invalid"}; return false; }
    if (mesh_abi::rdU32(data + 40 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "VectorKernelAttrs.algorithm reserved bytes are nonzero"}; return false; }
    if (!validVectorAlgorithm(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "VectorKernelAttrs.algorithm is invalid"}; return false; }
    out.algorithm = static_cast<VectorAlgorithm>(mesh_abi::rdU32(data + 40 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kVectorKernelAttrsBytes> encodeVectorKernelAttrs(const VectorKernelAttrs &value)
{
    std::array<uint8_t, kVectorKernelAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.graph_opcode));
    encodeSemanticRef(data.data() + 16, value.semantic_attrs);
    encodeSemanticRef(data.data() + 24, value.tile);
    encodeSemanticRef(data.data() + 32, value.cost);
    mesh_abi::wrU32(data.data() + 40 + kEnumValueValueOffset, static_cast<uint32_t>(value.algorithm));
    return data;
}

inline bool decodeViewDeclarationAttrs(const uint8_t *data, ViewDeclarationAttrs &out, AbiError &error)
{
    out = ViewDeclarationAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ViewDeclarationAttrs presence mask has undeclared bits"}; return false; }
    out.view_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kViewDeclarationAttrsBytes> encodeViewDeclarationAttrs(const ViewDeclarationAttrs &value)
{
    std::array<uint8_t, kViewDeclarationAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.view_id);
    return data;
}

#endif

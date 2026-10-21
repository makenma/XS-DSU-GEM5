#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_KERNEL_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_KERNEL_HH

inline constexpr SemanticFieldDescriptor kAllocAttrsObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const AllocAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAllocAttrsObjectIdField, true, value.object_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const AllocAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAllocAttrsObjectIdField, true, value.object_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBarrierAttrsParticipantsField = {"participants", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BarrierAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierAttrsParticipantsField, true, value.participants)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BarrierAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierAttrsParticipantsField, true, value.participants)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBlockedMnkLayoutBlockMField = {"block_m", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBlockedMnkLayoutBlockNField = {"block_n", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBlockedMnkLayoutBlockKField = {"block_k", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBlockedMnkLayoutMinorToMajorField = {"minor_to_major", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BlockedMnkLayout &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBlockedMnkLayoutBlockMField, true, value.block_m)) return false;
    if (!visitor(kBlockedMnkLayoutBlockNField, true, value.block_n)) return false;
    if (!visitor(kBlockedMnkLayoutBlockKField, true, value.block_k)) return false;
    if (!visitor(kBlockedMnkLayoutMinorToMajorField, true, value.minor_to_major)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BlockedMnkLayout &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBlockedMnkLayoutBlockKField, true, value.block_k)) return false;
    if (!visitor(kBlockedMnkLayoutBlockMField, true, value.block_m)) return false;
    if (!visitor(kBlockedMnkLayoutBlockNField, true, value.block_n)) return false;
    if (!visitor(kBlockedMnkLayoutMinorToMajorField, true, value.minor_to_major)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBufferObjectObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectStorageTensorIdField = {"storage_tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectOwnerCoreField = {"owner_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectMemorySpaceField = {"memory_space", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectShapeField = {"shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectStridesField = {"strides", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectFootprintBytesField = {"footprint_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectAlignmentBytesField = {"alignment_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectPersistentField = {"persistent", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferObjectBufferIndexField = {"buffer_index", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BufferObject &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBufferObjectObjectIdField, true, value.object_id)) return false;
    if (!visitor(kBufferObjectStorageTensorIdField, true, value.storage_tensor_id)) return false;
    if (!visitor(kBufferObjectOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBufferObjectMemorySpaceField, true, value.memory_space)) return false;
    if (!visitor(kBufferObjectShapeField, true, value.shape)) return false;
    if (!visitor(kBufferObjectStridesField, true, value.strides)) return false;
    if (!visitor(kBufferObjectFootprintBytesField, true, value.footprint_bytes)) return false;
    if (!visitor(kBufferObjectAlignmentBytesField, true, value.alignment_bytes)) return false;
    if (!visitor(kBufferObjectPersistentField, true, value.persistent)) return false;
    if (!visitor(kBufferObjectBufferIndexField, true, value.buffer_index)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BufferObject &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBufferObjectAlignmentBytesField, true, value.alignment_bytes)) return false;
    if (!visitor(kBufferObjectBufferIndexField, true, value.buffer_index)) return false;
    if (!visitor(kBufferObjectFootprintBytesField, true, value.footprint_bytes)) return false;
    if (!visitor(kBufferObjectMemorySpaceField, true, value.memory_space)) return false;
    if (!visitor(kBufferObjectObjectIdField, true, value.object_id)) return false;
    if (!visitor(kBufferObjectOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBufferObjectPersistentField, true, value.persistent)) return false;
    if (!visitor(kBufferObjectShapeField, true, value.shape)) return false;
    if (!visitor(kBufferObjectStorageTensorIdField, true, value.storage_tensor_id)) return false;
    if (!visitor(kBufferObjectStridesField, true, value.strides)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBufferViewViewIdField = {"view_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewShardIdField = {"shard_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewShardOriginField = {"shard_origin", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewPaddedShapeField = {"padded_shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewValidShapeField = {"valid_shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewObjectOffsetElementsField = {"object_offset_elements", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewObjectStridesField = {"object_strides", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBufferViewLayoutField = {"layout", SemanticFieldKind::Enum, 256ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kBufferViewBlockedLayoutTargets = {274};
inline constexpr SemanticFieldDescriptor kBufferViewBlockedLayoutField = {"blocked_layout", SemanticFieldKind::Ref, 512ull, kBufferViewBlockedLayoutTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kBufferViewGenerationField = {"generation", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BufferView &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBufferViewViewIdField, true, value.view_id)) return false;
    if (!visitor(kBufferViewObjectIdField, true, value.object_id)) return false;
    if (!visitor(kBufferViewShardIdField, true, value.shard_id)) return false;
    if (!visitor(kBufferViewShardOriginField, true, value.shard_origin)) return false;
    if (!visitor(kBufferViewPaddedShapeField, true, value.padded_shape)) return false;
    if (!visitor(kBufferViewValidShapeField, true, value.valid_shape)) return false;
    if (!visitor(kBufferViewObjectOffsetElementsField, true, value.object_offset_elements)) return false;
    if (!visitor(kBufferViewObjectStridesField, true, value.object_strides)) return false;
    if (!visitor(kBufferViewLayoutField, (value.presence_mask & 256ull) != 0, value.layout)) return false;
    if (!visitor(kBufferViewBlockedLayoutField, (value.presence_mask & 512ull) != 0, value.blocked_layout)) return false;
    if (!visitor(kBufferViewGenerationField, true, value.generation)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BufferView &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBufferViewBlockedLayoutField, (value.presence_mask & 512ull) != 0, value.blocked_layout)) return false;
    if (!visitor(kBufferViewGenerationField, true, value.generation)) return false;
    if (!visitor(kBufferViewLayoutField, (value.presence_mask & 256ull) != 0, value.layout)) return false;
    if (!visitor(kBufferViewObjectIdField, true, value.object_id)) return false;
    if (!visitor(kBufferViewObjectOffsetElementsField, true, value.object_offset_elements)) return false;
    if (!visitor(kBufferViewObjectStridesField, true, value.object_strides)) return false;
    if (!visitor(kBufferViewPaddedShapeField, true, value.padded_shape)) return false;
    if (!visitor(kBufferViewShardIdField, true, value.shard_id)) return false;
    if (!visitor(kBufferViewShardOriginField, true, value.shard_origin)) return false;
    if (!visitor(kBufferViewValidShapeField, true, value.valid_shape)) return false;
    if (!visitor(kBufferViewViewIdField, true, value.view_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kCollectiveAttrsKindField = {"kind", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCollectiveAttrsParticipantsField = {"participants", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCollectiveAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCollectiveAttrsChunkBytesField = {"chunk_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCollectiveAttrsReduceKindField = {"reduce_kind", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCollectiveAttrsPartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const CollectiveAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCollectiveAttrsKindField, true, value.kind)) return false;
    if (!visitor(kCollectiveAttrsParticipantsField, true, value.participants)) return false;
    if (!visitor(kCollectiveAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kCollectiveAttrsChunkBytesField, true, value.chunk_bytes)) return false;
    if (!visitor(kCollectiveAttrsReduceKindField, true, value.reduce_kind)) return false;
    if (!visitor(kCollectiveAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const CollectiveAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCollectiveAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kCollectiveAttrsChunkBytesField, true, value.chunk_bytes)) return false;
    if (!visitor(kCollectiveAttrsKindField, true, value.kind)) return false;
    if (!visitor(kCollectiveAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kCollectiveAttrsParticipantsField, true, value.participants)) return false;
    if (!visitor(kCollectiveAttrsReduceKindField, true, value.reduce_kind)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kControlTokenTokenIdField = {"token_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kControlTokenInitialField = {"initial", SemanticFieldKind::Bool, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ControlToken &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kControlTokenTokenIdField, true, value.token_id)) return false;
    if (!visitor(kControlTokenInitialField, true, value.initial)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ControlToken &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kControlTokenInitialField, true, value.initial)) return false;
    if (!visitor(kControlTokenTokenIdField, true, value.token_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kDmaAttrsKindField = {"kind", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsIssuingCoreField = {"issuing_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsSourceCoreField = {"source_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsDestinationCoreField = {"destination_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsTransferIdField = {"transfer_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsFillPatternField = {"fill_pattern", SemanticFieldKind::Bytes, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDmaAttrsMaxBurstBeatsField = {"max_burst_beats", SemanticFieldKind::U64, 64ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DmaAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDmaAttrsKindField, true, value.kind)) return false;
    if (!visitor(kDmaAttrsIssuingCoreField, true, value.issuing_core)) return false;
    if (!visitor(kDmaAttrsSourceCoreField, true, value.source_core)) return false;
    if (!visitor(kDmaAttrsDestinationCoreField, true, value.destination_core)) return false;
    if (!visitor(kDmaAttrsTransferIdField, true, value.transfer_id)) return false;
    if (!visitor(kDmaAttrsFillPatternField, true, value.fill_pattern)) return false;
    if (!visitor(kDmaAttrsMaxBurstBeatsField, (value.presence_mask & 64ull) != 0, value.max_burst_beats)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DmaAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDmaAttrsDestinationCoreField, true, value.destination_core)) return false;
    if (!visitor(kDmaAttrsFillPatternField, true, value.fill_pattern)) return false;
    if (!visitor(kDmaAttrsIssuingCoreField, true, value.issuing_core)) return false;
    if (!visitor(kDmaAttrsKindField, true, value.kind)) return false;
    if (!visitor(kDmaAttrsMaxBurstBeatsField, (value.presence_mask & 64ull) != 0, value.max_burst_beats)) return false;
    if (!visitor(kDmaAttrsSourceCoreField, true, value.source_core)) return false;
    if (!visitor(kDmaAttrsTransferIdField, true, value.transfer_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kElementRegionOriginField = {"origin", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kElementRegionShapeField = {"shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kElementRegionStepsField = {"steps", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ElementRegion &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kElementRegionOriginField, true, value.origin)) return false;
    if (!visitor(kElementRegionShapeField, true, value.shape)) return false;
    if (!visitor(kElementRegionStepsField, true, value.steps)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ElementRegion &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kElementRegionOriginField, true, value.origin)) return false;
    if (!visitor(kElementRegionShapeField, true, value.shape)) return false;
    if (!visitor(kElementRegionStepsField, true, value.steps)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kGemmKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kGemmKernelAttrsSemanticAttrsTargets = {266};
inline constexpr SemanticFieldDescriptor kGemmKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kGemmKernelAttrsSemanticAttrsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kGemmKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kGemmKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kGemmKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kGemmKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kGemmKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kGemmKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kGemmKernelAttrsPhaseField = {"phase", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kGemmKernelAttrsPartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const GemmKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kGemmKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kGemmKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kGemmKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kGemmKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kGemmKernelAttrsPhaseField, true, value.phase)) return false;
    if (!visitor(kGemmKernelAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const GemmKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kGemmKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kGemmKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kGemmKernelAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kGemmKernelAttrsPhaseField, true, value.phase)) return false;
    if (!visitor(kGemmKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kGemmKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelComputationComputationIdField = {"computation_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelComputationOpcodeField = {"opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelComputationOperandTensorIdsField = {"operand_tensor_ids", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelComputationResultTensorIdField = {"result_tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 8> kKernelComputationAttrsTargets = {266, 271, 267, 264, 269, 268, 270, 265};
inline constexpr SemanticFieldDescriptor kKernelComputationAttrsField = {"attrs", SemanticFieldKind::Ref, 0ull, kKernelComputationAttrsTargets.data(), 8, true};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelComputation &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelComputationComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kKernelComputationOpcodeField, true, value.opcode)) return false;
    if (!visitor(kKernelComputationOperandTensorIdsField, true, value.operand_tensor_ids)) return false;
    if (!visitor(kKernelComputationResultTensorIdField, true, value.result_tensor_id)) return false;
    if (!visitor(kKernelComputationAttrsField, true, value.attrs)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelComputation &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelComputationAttrsField, true, value.attrs)) return false;
    if (!visitor(kKernelComputationComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kKernelComputationOpcodeField, true, value.opcode)) return false;
    if (!visitor(kKernelComputationOperandTensorIdsField, true, value.operand_tensor_ids)) return false;
    if (!visitor(kKernelComputationResultTensorIdField, true, value.result_tensor_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelCostLogicalInputBytesField = {"logical_input_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelCostLogicalOutputBytesField = {"logical_output_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelCostLocalStorageBytesField = {"local_storage_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelCostMacsField = {"macs", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelCostVectorOpsField = {"vector_ops", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelCostReductionOpsField = {"reduction_ops", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelCost &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelCostLogicalInputBytesField, true, value.logical_input_bytes)) return false;
    if (!visitor(kKernelCostLogicalOutputBytesField, true, value.logical_output_bytes)) return false;
    if (!visitor(kKernelCostLocalStorageBytesField, true, value.local_storage_bytes)) return false;
    if (!visitor(kKernelCostMacsField, true, value.macs)) return false;
    if (!visitor(kKernelCostVectorOpsField, true, value.vector_ops)) return false;
    if (!visitor(kKernelCostReductionOpsField, true, value.reduction_ops)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelCost &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelCostLocalStorageBytesField, true, value.local_storage_bytes)) return false;
    if (!visitor(kKernelCostLogicalInputBytesField, true, value.logical_input_bytes)) return false;
    if (!visitor(kKernelCostLogicalOutputBytesField, true, value.logical_output_bytes)) return false;
    if (!visitor(kKernelCostMacsField, true, value.macs)) return false;
    if (!visitor(kKernelCostReductionOpsField, true, value.reduction_ops)) return false;
    if (!visitor(kKernelCostVectorOpsField, true, value.vector_ops)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelOpOpIdField = {"op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpComputationIdField = {"computation_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpStableKeyField = {"stable_key", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpOpcodeField = {"opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpOwnerCoreField = {"owner_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpResultShardIdField = {"result_shard_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kKernelOpReadsTargets = {292};
inline constexpr SemanticFieldDescriptor kKernelOpReadsField = {"reads", SemanticFieldKind::RefList, 0ull, kKernelOpReadsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kKernelOpWritesTargets = {298};
inline constexpr SemanticFieldDescriptor kKernelOpWritesField = {"writes", SemanticFieldKind::RefList, 0ull, kKernelOpWritesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 15> kKernelOpAttrsTargets = {272, 302, 279, 295, 281, 289, 301, 290, 296, 297, 291, 277, 288, 287, 273};
inline constexpr SemanticFieldDescriptor kKernelOpAttrsField = {"attrs", SemanticFieldKind::Ref, 0ull, kKernelOpAttrsTargets.data(), 15, true};
inline constexpr SemanticFieldDescriptor kKernelOpAfterTokensField = {"after_tokens", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelOpDoneTokenField = {"done_token", SemanticFieldKind::U64, 1024ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelOp &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelOpOpIdField, true, value.op_id)) return false;
    if (!visitor(kKernelOpComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kKernelOpStableKeyField, true, value.stable_key)) return false;
    if (!visitor(kKernelOpOpcodeField, true, value.opcode)) return false;
    if (!visitor(kKernelOpOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kKernelOpResultShardIdField, true, value.result_shard_id)) return false;
    if (!visitor(kKernelOpReadsField, true, value.reads)) return false;
    if (!visitor(kKernelOpWritesField, true, value.writes)) return false;
    if (!visitor(kKernelOpAttrsField, true, value.attrs)) return false;
    if (!visitor(kKernelOpAfterTokensField, true, value.after_tokens)) return false;
    if (!visitor(kKernelOpDoneTokenField, (value.presence_mask & 1024ull) != 0, value.done_token)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelOp &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelOpAfterTokensField, true, value.after_tokens)) return false;
    if (!visitor(kKernelOpAttrsField, true, value.attrs)) return false;
    if (!visitor(kKernelOpComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kKernelOpDoneTokenField, (value.presence_mask & 1024ull) != 0, value.done_token)) return false;
    if (!visitor(kKernelOpOpIdField, true, value.op_id)) return false;
    if (!visitor(kKernelOpOpcodeField, true, value.opcode)) return false;
    if (!visitor(kKernelOpOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kKernelOpReadsField, true, value.reads)) return false;
    if (!visitor(kKernelOpResultShardIdField, true, value.result_shard_id)) return false;
    if (!visitor(kKernelOpStableKeyField, true, value.stable_key)) return false;
    if (!visitor(kKernelOpWritesField, true, value.writes)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelTensorTensorIdField = {"tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorProducerComputationIdField = {"producer_computation_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorSynthesizedPurposeField = {"synthesized_purpose", SemanticFieldKind::Enum, 4ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorAliasRootTensorIdField = {"alias_root_tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorStorageOffsetElementsField = {"storage_offset_elements", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorNameField = {"name", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorRoleField = {"role", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorDtypeField = {"dtype", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorShapeField = {"shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorStridesField = {"strides", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorStorageClassField = {"storage_class", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorAccessField = {"access", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorLogicalExtentBytesField = {"logical_extent_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorStorageExtentBytesField = {"storage_extent_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTensorContentSha256Field = {"content_sha256", SemanticFieldKind::String, 16384ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelTensor &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTensorTensorIdField, true, value.tensor_id)) return false;
    if (!visitor(kKernelTensorProducerComputationIdField, true, value.producer_computation_id)) return false;
    if (!visitor(kKernelTensorSynthesizedPurposeField, (value.presence_mask & 4ull) != 0, value.synthesized_purpose)) return false;
    if (!visitor(kKernelTensorAliasRootTensorIdField, true, value.alias_root_tensor_id)) return false;
    if (!visitor(kKernelTensorStorageOffsetElementsField, true, value.storage_offset_elements)) return false;
    if (!visitor(kKernelTensorNameField, true, value.name)) return false;
    if (!visitor(kKernelTensorRoleField, true, value.role)) return false;
    if (!visitor(kKernelTensorDtypeField, true, value.dtype)) return false;
    if (!visitor(kKernelTensorShapeField, true, value.shape)) return false;
    if (!visitor(kKernelTensorStridesField, true, value.strides)) return false;
    if (!visitor(kKernelTensorStorageClassField, true, value.storage_class)) return false;
    if (!visitor(kKernelTensorAccessField, true, value.access)) return false;
    if (!visitor(kKernelTensorLogicalExtentBytesField, true, value.logical_extent_bytes)) return false;
    if (!visitor(kKernelTensorStorageExtentBytesField, true, value.storage_extent_bytes)) return false;
    if (!visitor(kKernelTensorContentSha256Field, (value.presence_mask & 16384ull) != 0, value.content_sha256)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelTensor &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTensorAccessField, true, value.access)) return false;
    if (!visitor(kKernelTensorAliasRootTensorIdField, true, value.alias_root_tensor_id)) return false;
    if (!visitor(kKernelTensorContentSha256Field, (value.presence_mask & 16384ull) != 0, value.content_sha256)) return false;
    if (!visitor(kKernelTensorDtypeField, true, value.dtype)) return false;
    if (!visitor(kKernelTensorLogicalExtentBytesField, true, value.logical_extent_bytes)) return false;
    if (!visitor(kKernelTensorNameField, true, value.name)) return false;
    if (!visitor(kKernelTensorProducerComputationIdField, true, value.producer_computation_id)) return false;
    if (!visitor(kKernelTensorRoleField, true, value.role)) return false;
    if (!visitor(kKernelTensorShapeField, true, value.shape)) return false;
    if (!visitor(kKernelTensorStorageClassField, true, value.storage_class)) return false;
    if (!visitor(kKernelTensorStorageExtentBytesField, true, value.storage_extent_bytes)) return false;
    if (!visitor(kKernelTensorStorageOffsetElementsField, true, value.storage_offset_elements)) return false;
    if (!visitor(kKernelTensorStridesField, true, value.strides)) return false;
    if (!visitor(kKernelTensorSynthesizedPurposeField, (value.presence_mask & 4ull) != 0, value.synthesized_purpose)) return false;
    if (!visitor(kKernelTensorTensorIdField, true, value.tensor_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelTileBatchOriginField = {"batch_origin", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileMOriginField = {"m_origin", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileNOriginField = {"n_origin", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileKOriginField = {"k_origin", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileBatchExtentField = {"batch_extent", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileMExtentField = {"m_extent", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileNExtentField = {"n_extent", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileKExtentField = {"k_extent", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileValidBatchField = {"valid_batch", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileValidMField = {"valid_m", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileValidNField = {"valid_n", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kKernelTileValidKField = {"valid_k", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelTile &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTileBatchOriginField, true, value.batch_origin)) return false;
    if (!visitor(kKernelTileMOriginField, true, value.m_origin)) return false;
    if (!visitor(kKernelTileNOriginField, true, value.n_origin)) return false;
    if (!visitor(kKernelTileKOriginField, true, value.k_origin)) return false;
    if (!visitor(kKernelTileBatchExtentField, true, value.batch_extent)) return false;
    if (!visitor(kKernelTileMExtentField, true, value.m_extent)) return false;
    if (!visitor(kKernelTileNExtentField, true, value.n_extent)) return false;
    if (!visitor(kKernelTileKExtentField, true, value.k_extent)) return false;
    if (!visitor(kKernelTileValidBatchField, true, value.valid_batch)) return false;
    if (!visitor(kKernelTileValidMField, true, value.valid_m)) return false;
    if (!visitor(kKernelTileValidNField, true, value.valid_n)) return false;
    if (!visitor(kKernelTileValidKField, true, value.valid_k)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelTile &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTileBatchExtentField, true, value.batch_extent)) return false;
    if (!visitor(kKernelTileBatchOriginField, true, value.batch_origin)) return false;
    if (!visitor(kKernelTileKExtentField, true, value.k_extent)) return false;
    if (!visitor(kKernelTileKOriginField, true, value.k_origin)) return false;
    if (!visitor(kKernelTileMExtentField, true, value.m_extent)) return false;
    if (!visitor(kKernelTileMOriginField, true, value.m_origin)) return false;
    if (!visitor(kKernelTileNExtentField, true, value.n_extent)) return false;
    if (!visitor(kKernelTileNOriginField, true, value.n_origin)) return false;
    if (!visitor(kKernelTileValidBatchField, true, value.valid_batch)) return false;
    if (!visitor(kKernelTileValidKField, true, value.valid_k)) return false;
    if (!visitor(kKernelTileValidMField, true, value.valid_m)) return false;
    if (!visitor(kKernelTileValidNField, true, value.valid_n)) return false;
    return true;
}


template <typename Visitor> bool visitSemanticFieldsWire(const LocalCopyAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const LocalCopyAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

inline constexpr SemanticFieldDescriptor kLocalReduceAttrsReduceKindField = {"reduce_kind", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kLocalReduceAttrsPartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kLocalReduceAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kLocalReduceAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kLocalReduceAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kLocalReduceAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kLocalReduceAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kLocalReduceAttrsCostTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const LocalReduceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLocalReduceAttrsReduceKindField, true, value.reduce_kind)) return false;
    if (!visitor(kLocalReduceAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kLocalReduceAttrsTileField, true, value.tile)) return false;
    if (!visitor(kLocalReduceAttrsCostField, true, value.cost)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const LocalReduceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLocalReduceAttrsCostField, true, value.cost)) return false;
    if (!visitor(kLocalReduceAttrsPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kLocalReduceAttrsReduceKindField, true, value.reduce_kind)) return false;
    if (!visitor(kLocalReduceAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kMatrixEpilogueKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kMatrixEpilogueKernelAttrsSemanticAttrsTargets = {266};
inline constexpr SemanticFieldDescriptor kMatrixEpilogueKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kMatrixEpilogueKernelAttrsSemanticAttrsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kMatrixEpilogueKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kMatrixEpilogueKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kMatrixEpilogueKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kMatrixEpilogueKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kMatrixEpilogueKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kMatrixEpilogueKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kMatrixEpilogueKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const MatrixEpilogueKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMatrixEpilogueKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const MatrixEpilogueKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMatrixEpilogueKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kMatrixEpilogueKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kMovementKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kMovementKernelAttrsSemanticAttrsTargets = {271, 267};
inline constexpr SemanticFieldDescriptor kMovementKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kMovementKernelAttrsSemanticAttrsTargets.data(), 2, true};
inline constexpr std::array<uint16_t, 1> kMovementKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kMovementKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kMovementKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kMovementKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kMovementKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kMovementKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kMovementKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const MovementKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMovementKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kMovementKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kMovementKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kMovementKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kMovementKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const MovementKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kMovementKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kMovementKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kMovementKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kMovementKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kMovementKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kNormKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kNormKernelAttrsSemanticAttrsTargets = {268};
inline constexpr SemanticFieldDescriptor kNormKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kNormKernelAttrsSemanticAttrsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kNormKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kNormKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kNormKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kNormKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kNormKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kNormKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kNormKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const NormKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kNormKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kNormKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kNormKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kNormKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kNormKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const NormKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kNormKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kNormKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kNormKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kNormKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kNormKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kOperandAccessStateIdField = {"state_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kOperandAccessViewIdField = {"view_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kOperandAccessRegionTargets = {280};
inline constexpr SemanticFieldDescriptor kOperandAccessRegionField = {"region", SemanticFieldKind::Ref, 0ull, kOperandAccessRegionTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kOperandAccessModeField = {"mode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const OperandAccess &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kOperandAccessStateIdField, true, value.state_id)) return false;
    if (!visitor(kOperandAccessViewIdField, true, value.view_id)) return false;
    if (!visitor(kOperandAccessRegionField, true, value.region)) return false;
    if (!visitor(kOperandAccessModeField, true, value.mode)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const OperandAccess &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kOperandAccessModeField, true, value.mode)) return false;
    if (!visitor(kOperandAccessRegionField, true, value.region)) return false;
    if (!visitor(kOperandAccessStateIdField, true, value.state_id)) return false;
    if (!visitor(kOperandAccessViewIdField, true, value.view_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kPartialSumDefinitionPartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kPartialSumDefinitionComputationIdField = {"computation_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kPartialSumDefinitionSemanticResultTensorIdField = {"semantic_result_tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kPartialSumDefinitionAccumulatorTensorIdField = {"accumulator_tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kPartialSumDefinitionPlacementIdField = {"placement_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const PartialSumDefinition &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kPartialSumDefinitionPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kPartialSumDefinitionComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kPartialSumDefinitionSemanticResultTensorIdField, true, value.semantic_result_tensor_id)) return false;
    if (!visitor(kPartialSumDefinitionAccumulatorTensorIdField, true, value.accumulator_tensor_id)) return false;
    if (!visitor(kPartialSumDefinitionPlacementIdField, true, value.placement_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const PartialSumDefinition &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kPartialSumDefinitionAccumulatorTensorIdField, true, value.accumulator_tensor_id)) return false;
    if (!visitor(kPartialSumDefinitionComputationIdField, true, value.computation_id)) return false;
    if (!visitor(kPartialSumDefinitionPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kPartialSumDefinitionPlacementIdField, true, value.placement_id)) return false;
    if (!visitor(kPartialSumDefinitionSemanticResultTensorIdField, true, value.semantic_result_tensor_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kPlacementPlacementIdField = {"placement_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kPlacementCoreIdsField = {"core_ids", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const Placement &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kPlacementPlacementIdField, true, value.placement_id)) return false;
    if (!visitor(kPlacementCoreIdsField, true, value.core_ids)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const Placement &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kPlacementCoreIdsField, true, value.core_ids)) return false;
    if (!visitor(kPlacementPlacementIdField, true, value.placement_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kRecvWaitAttrsTransferIdField = {"transfer_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kRecvWaitAttrsSourceCoreField = {"source_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kRecvWaitAttrsDestinationCoreField = {"destination_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kRecvWaitAttrsExpectedBytesField = {"expected_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const RecvWaitAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRecvWaitAttrsTransferIdField, true, value.transfer_id)) return false;
    if (!visitor(kRecvWaitAttrsSourceCoreField, true, value.source_core)) return false;
    if (!visitor(kRecvWaitAttrsDestinationCoreField, true, value.destination_core)) return false;
    if (!visitor(kRecvWaitAttrsExpectedBytesField, true, value.expected_bytes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const RecvWaitAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRecvWaitAttrsDestinationCoreField, true, value.destination_core)) return false;
    if (!visitor(kRecvWaitAttrsExpectedBytesField, true, value.expected_bytes)) return false;
    if (!visitor(kRecvWaitAttrsSourceCoreField, true, value.source_core)) return false;
    if (!visitor(kRecvWaitAttrsTransferIdField, true, value.transfer_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kReductionKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kReductionKernelAttrsSemanticAttrsTargets = {269};
inline constexpr SemanticFieldDescriptor kReductionKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kReductionKernelAttrsSemanticAttrsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kReductionKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kReductionKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kReductionKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kReductionKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kReductionKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kReductionKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kReductionKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ReductionKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReductionKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kReductionKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kReductionKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kReductionKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kReductionKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ReductionKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReductionKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kReductionKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kReductionKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kReductionKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kReductionKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 1> kSoftmaxKernelAttrsSemanticAttrsTargets = {270};
inline constexpr SemanticFieldDescriptor kSoftmaxKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kSoftmaxKernelAttrsSemanticAttrsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kSoftmaxKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kSoftmaxKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kSoftmaxKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kSoftmaxKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kSoftmaxKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kSoftmaxKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kSoftmaxKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const SoftmaxKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSoftmaxKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kSoftmaxKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kSoftmaxKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kSoftmaxKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const SoftmaxKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kSoftmaxKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kSoftmaxKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kSoftmaxKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kSoftmaxKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kStateTransitionOldStateIdField = {"old_state_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kStateTransitionNewStateIdField = {"new_state_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kStateTransitionViewIdField = {"view_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kStateTransitionRegionTargets = {280};
inline constexpr SemanticFieldDescriptor kStateTransitionRegionField = {"region", SemanticFieldKind::Ref, 0ull, kStateTransitionRegionTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kStateTransitionModeField = {"mode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const StateTransition &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStateTransitionOldStateIdField, true, value.old_state_id)) return false;
    if (!visitor(kStateTransitionNewStateIdField, true, value.new_state_id)) return false;
    if (!visitor(kStateTransitionViewIdField, true, value.view_id)) return false;
    if (!visitor(kStateTransitionRegionField, true, value.region)) return false;
    if (!visitor(kStateTransitionModeField, true, value.mode)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const StateTransition &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStateTransitionModeField, true, value.mode)) return false;
    if (!visitor(kStateTransitionNewStateIdField, true, value.new_state_id)) return false;
    if (!visitor(kStateTransitionOldStateIdField, true, value.old_state_id)) return false;
    if (!visitor(kStateTransitionRegionField, true, value.region)) return false;
    if (!visitor(kStateTransitionViewIdField, true, value.view_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kTensorShardShardIdField = {"shard_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardTensorIdField = {"tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardPlacementIdField = {"placement_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardOwnerCoreField = {"owner_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardDistributionField = {"distribution", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardGlobalOriginField = {"global_origin", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardPaddedLocalShapeField = {"padded_local_shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardValidShapeField = {"valid_shape", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorShardPartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TensorShard &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTensorShardShardIdField, true, value.shard_id)) return false;
    if (!visitor(kTensorShardTensorIdField, true, value.tensor_id)) return false;
    if (!visitor(kTensorShardPlacementIdField, true, value.placement_id)) return false;
    if (!visitor(kTensorShardOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kTensorShardDistributionField, true, value.distribution)) return false;
    if (!visitor(kTensorShardGlobalOriginField, true, value.global_origin)) return false;
    if (!visitor(kTensorShardPaddedLocalShapeField, true, value.padded_local_shape)) return false;
    if (!visitor(kTensorShardValidShapeField, true, value.valid_shape)) return false;
    if (!visitor(kTensorShardPartialSumIdField, true, value.partial_sum_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TensorShard &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTensorShardDistributionField, true, value.distribution)) return false;
    if (!visitor(kTensorShardGlobalOriginField, true, value.global_origin)) return false;
    if (!visitor(kTensorShardOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kTensorShardPaddedLocalShapeField, true, value.padded_local_shape)) return false;
    if (!visitor(kTensorShardPartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kTensorShardPlacementIdField, true, value.placement_id)) return false;
    if (!visitor(kTensorShardShardIdField, true, value.shard_id)) return false;
    if (!visitor(kTensorShardTensorIdField, true, value.tensor_id)) return false;
    if (!visitor(kTensorShardValidShapeField, true, value.valid_shape)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kTensorStateStateIdField = {"state_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorStateObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorStateVersionField = {"version", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorStateOriginField = {"origin", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTensorStatePartialSumIdField = {"partial_sum_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TensorState &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTensorStateStateIdField, true, value.state_id)) return false;
    if (!visitor(kTensorStateObjectIdField, true, value.object_id)) return false;
    if (!visitor(kTensorStateVersionField, true, value.version)) return false;
    if (!visitor(kTensorStateOriginField, true, value.origin)) return false;
    if (!visitor(kTensorStatePartialSumIdField, true, value.partial_sum_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TensorState &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTensorStateObjectIdField, true, value.object_id)) return false;
    if (!visitor(kTensorStateOriginField, true, value.origin)) return false;
    if (!visitor(kTensorStatePartialSumIdField, true, value.partial_sum_id)) return false;
    if (!visitor(kTensorStateStateIdField, true, value.state_id)) return false;
    if (!visitor(kTensorStateVersionField, true, value.version)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kVectorKernelAttrsGraphOpcodeField = {"graph_opcode", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kVectorKernelAttrsSemanticAttrsTargets = {264, 265};
inline constexpr SemanticFieldDescriptor kVectorKernelAttrsSemanticAttrsField = {"semantic_attrs", SemanticFieldKind::Ref, 0ull, kVectorKernelAttrsSemanticAttrsTargets.data(), 2, true};
inline constexpr std::array<uint16_t, 1> kVectorKernelAttrsTileTargets = {286};
inline constexpr SemanticFieldDescriptor kVectorKernelAttrsTileField = {"tile", SemanticFieldKind::Ref, 0ull, kVectorKernelAttrsTileTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVectorKernelAttrsCostTargets = {283};
inline constexpr SemanticFieldDescriptor kVectorKernelAttrsCostField = {"cost", SemanticFieldKind::Ref, 0ull, kVectorKernelAttrsCostTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kVectorKernelAttrsAlgorithmField = {"algorithm", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const VectorKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kVectorKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kVectorKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kVectorKernelAttrsTileField, true, value.tile)) return false;
    if (!visitor(kVectorKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kVectorKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const VectorKernelAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kVectorKernelAttrsAlgorithmField, true, value.algorithm)) return false;
    if (!visitor(kVectorKernelAttrsCostField, true, value.cost)) return false;
    if (!visitor(kVectorKernelAttrsGraphOpcodeField, true, value.graph_opcode)) return false;
    if (!visitor(kVectorKernelAttrsSemanticAttrsField, true, value.semantic_attrs)) return false;
    if (!visitor(kVectorKernelAttrsTileField, true, value.tile)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kViewDeclarationAttrsViewIdField = {"view_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ViewDeclarationAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kViewDeclarationAttrsViewIdField, true, value.view_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ViewDeclarationAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kViewDeclarationAttrsViewIdField, true, value.view_id)) return false;
    return true;
}

#endif

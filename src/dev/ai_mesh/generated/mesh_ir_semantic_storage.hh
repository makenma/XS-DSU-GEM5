#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_STORAGE_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_STORAGE_HH

struct SemanticTables
{
    std::vector<ExecutionWorkPhase> execution_work_phase_rows;
    std::vector<WorkEstimate> work_estimate_rows;
    std::vector<Add> add_rows;
    std::vector<CeilDivByConst> ceil_div_by_const_rows;
    std::vector<Const> const_rows;
    std::vector<FloorDivByConst> floor_div_by_const_rows;
    std::vector<MulByConst> mul_by_const_rows;
    std::vector<Symbol> symbol_rows;
    std::vector<ElementwiseAttrs> elementwise_attrs_rows;
    std::vector<EmbeddingAttrs> embedding_attrs_rows;
    std::vector<MatmulAttrs> matmul_attrs_rows;
    std::vector<MovementAttrs> movement_attrs_rows;
    std::vector<NormAttrs> norm_attrs_rows;
    std::vector<ReduceAttrs> reduce_attrs_rows;
    std::vector<SoftmaxAttrs> softmax_attrs_rows;
    std::vector<ViewAttrs> view_attrs_rows;
    std::vector<AllocAttrs> alloc_attrs_rows;
    std::vector<BarrierAttrs> barrier_attrs_rows;
    std::vector<BlockedMnkLayout> blocked_mnk_layout_rows;
    std::vector<BufferObject> buffer_object_rows;
    std::vector<BufferView> buffer_view_rows;
    std::vector<CollectiveAttrs> collective_attrs_rows;
    std::vector<ControlToken> control_token_rows;
    std::vector<DmaAttrs> dma_attrs_rows;
    std::vector<ElementRegion> element_region_rows;
    std::vector<GemmKernelAttrs> gemm_kernel_attrs_rows;
    std::vector<KernelComputation> kernel_computation_rows;
    std::vector<KernelCost> kernel_cost_rows;
    std::vector<KernelOp> kernel_op_rows;
    std::vector<KernelTensor> kernel_tensor_rows;
    std::vector<KernelTile> kernel_tile_rows;
    std::vector<LocalCopyAttrs> local_copy_attrs_rows;
    std::vector<LocalReduceAttrs> local_reduce_attrs_rows;
    std::vector<MatrixEpilogueKernelAttrs> matrix_epilogue_kernel_attrs_rows;
    std::vector<MovementKernelAttrs> movement_kernel_attrs_rows;
    std::vector<NormKernelAttrs> norm_kernel_attrs_rows;
    std::vector<OperandAccess> operand_access_rows;
    std::vector<PartialSumDefinition> partial_sum_definition_rows;
    std::vector<Placement> placement_rows;
    std::vector<RecvWaitAttrs> recv_wait_attrs_rows;
    std::vector<ReductionKernelAttrs> reduction_kernel_attrs_rows;
    std::vector<SoftmaxKernelAttrs> softmax_kernel_attrs_rows;
    std::vector<StateTransition> state_transition_rows;
    std::vector<TensorShard> tensor_shard_rows;
    std::vector<TensorState> tensor_state_rows;
    std::vector<VectorKernelAttrs> vector_kernel_attrs_rows;
    std::vector<ViewDeclarationAttrs> view_declaration_attrs_rows;
    std::vector<AuthoredProgramOrigin> authored_program_origin_rows;
    std::vector<AuthoredVariantLineage> authored_variant_lineage_rows;
    std::vector<AxiFenceAttrs> axi_fence_attrs_rows;
    std::vector<BarrierArrival> barrier_arrival_rows;
    std::vector<BarrierExecution> barrier_execution_rows;
    std::vector<BarrierGroup> barrier_group_rows;
    std::vector<CommandSemantics> command_semantics_rows;
    std::vector<CompiledProgramOrigin> compiled_program_origin_rows;
    std::vector<CompiledVariantLineage> compiled_variant_lineage_rows;
    std::vector<ComputeExecution> compute_execution_rows;
    std::vector<ControlCommandSource> control_command_source_rows;
    std::vector<ControlExecution> control_execution_rows;
    std::vector<DescriptorEndpointUse> descriptor_endpoint_use_rows;
    std::vector<DescriptorGroup> descriptor_group_rows;
    std::vector<DescriptorSource> descriptor_source_rows;
    std::vector<DmaExecution> dma_execution_rows;
    std::vector<EventSignalAttrs> event_signal_attrs_rows;
    std::vector<EventWaitAttrs> event_wait_attrs_rows;
    std::vector<ExternalSlotBacking> external_slot_backing_rows;
    std::vector<HaltAttrs> halt_attrs_rows;
    std::vector<IdSpan> id_span_rows;
    std::vector<KernelCommandSource> kernel_command_source_rows;
    std::vector<KernelTokenSource> kernel_token_source_rows;
    std::vector<LifecycleSource> lifecycle_source_rows;
    std::vector<LocalAllocationBacking> local_allocation_backing_rows;
    std::vector<ObjectBacking> object_backing_rows;
    std::vector<ObjectSource> object_source_rows;
    std::vector<ProgramSemantics> program_semantics_rows;
    std::vector<ProgramVariant> program_variant_rows;
    std::vector<ReadAccessUse> read_access_use_rows;
    std::vector<RecvWaitExecution> recv_wait_execution_rows;
    std::vector<RepeatCommandAttrs> repeat_command_attrs_rows;
    std::vector<RequestBeginAttrs> request_begin_attrs_rows;
    std::vector<RequestEndAttrs> request_end_attrs_rows;
    std::vector<ResidentView> resident_view_rows;
    std::vector<ScheduledDependency> scheduled_dependency_rows;
    std::vector<ScheduledStream> scheduled_stream_rows;
    std::vector<StateSource> state_source_rows;
    std::vector<StreamOrderSource> stream_order_source_rows;
    std::vector<VariantMembership> variant_membership_rows;
    std::vector<WriteAccessUse> write_access_use_rows;
    std::vector<Binding> binding_rows;
    std::vector<BindingSlot> binding_slot_rows;
    std::vector<ChannelTraffic> channel_traffic_rows;
    std::vector<DescriptorIdentity> descriptor_identity_rows;
    std::vector<DescriptorTraffic> descriptor_traffic_rows;
    std::vector<TrafficAggregate> traffic_aggregate_rows;
    std::vector<TrafficAggregateKey> traffic_aggregate_key_rows;
    std::vector<TrafficChannelTotal> traffic_channel_total_rows;
    std::vector<TrafficReport> traffic_report_rows;
};

template <> struct SemanticRecordTraits<ExecutionWorkPhase>
{
    static constexpr uint16_t section_type = 256;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ExecutionWorkPhase";
    static bool decode(const uint8_t *data, ExecutionWorkPhase &value, AbiError &error) { return decodeExecutionWorkPhase(data, value, error); }
    static std::array<uint8_t, 16> encode(const ExecutionWorkPhase &value) { return encodeExecutionWorkPhase(value); }
};

template <> struct SemanticRecordTraits<WorkEstimate>
{
    static constexpr uint16_t section_type = 257;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "WorkEstimate";
    static bool decode(const uint8_t *data, WorkEstimate &value, AbiError &error) { return decodeWorkEstimate(data, value, error); }
    static std::array<uint8_t, 40> encode(const WorkEstimate &value) { return encodeWorkEstimate(value); }
};

template <> struct SemanticRecordTraits<Add>
{
    static constexpr uint16_t section_type = 258;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "Add";
    static bool decode(const uint8_t *data, Add &value, AbiError &error) { return decodeAdd(data, value, error); }
    static std::array<uint8_t, 24> encode(const Add &value) { return encodeAdd(value); }
};

template <> struct SemanticRecordTraits<CeilDivByConst>
{
    static constexpr uint16_t section_type = 259;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "CeilDivByConst";
    static bool decode(const uint8_t *data, CeilDivByConst &value, AbiError &error) { return decodeCeilDivByConst(data, value, error); }
    static std::array<uint8_t, 24> encode(const CeilDivByConst &value) { return encodeCeilDivByConst(value); }
};

template <> struct SemanticRecordTraits<Const>
{
    static constexpr uint16_t section_type = 260;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "Const";
    static bool decode(const uint8_t *data, Const &value, AbiError &error) { return decodeConst(data, value, error); }
    static std::array<uint8_t, 16> encode(const Const &value) { return encodeConst(value); }
};

template <> struct SemanticRecordTraits<FloorDivByConst>
{
    static constexpr uint16_t section_type = 261;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "FloorDivByConst";
    static bool decode(const uint8_t *data, FloorDivByConst &value, AbiError &error) { return decodeFloorDivByConst(data, value, error); }
    static std::array<uint8_t, 24> encode(const FloorDivByConst &value) { return encodeFloorDivByConst(value); }
};

template <> struct SemanticRecordTraits<MulByConst>
{
    static constexpr uint16_t section_type = 262;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "MulByConst";
    static bool decode(const uint8_t *data, MulByConst &value, AbiError &error) { return decodeMulByConst(data, value, error); }
    static std::array<uint8_t, 24> encode(const MulByConst &value) { return encodeMulByConst(value); }
};

template <> struct SemanticRecordTraits<Symbol>
{
    static constexpr uint16_t section_type = 263;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "Symbol";
    static bool decode(const uint8_t *data, Symbol &value, AbiError &error) { return decodeSymbol(data, value, error); }
    static std::array<uint8_t, 48> encode(const Symbol &value) { return encodeSymbol(value); }
};

template <> struct SemanticRecordTraits<ElementwiseAttrs>
{
    static constexpr uint16_t section_type = 264;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "ElementwiseAttrs";
    static bool decode(const uint8_t *data, ElementwiseAttrs &value, AbiError &error) { return decodeElementwiseAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const ElementwiseAttrs &value) { return encodeElementwiseAttrs(value); }
};

template <> struct SemanticRecordTraits<EmbeddingAttrs>
{
    static constexpr uint16_t section_type = 265;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "EmbeddingAttrs";
    static bool decode(const uint8_t *data, EmbeddingAttrs &value, AbiError &error) { return decodeEmbeddingAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const EmbeddingAttrs &value) { return encodeEmbeddingAttrs(value); }
};

template <> struct SemanticRecordTraits<MatmulAttrs>
{
    static constexpr uint16_t section_type = 266;
    static constexpr uint32_t record_bytes = 72;
    static constexpr std::string_view json_tag = "MatmulAttrs";
    static bool decode(const uint8_t *data, MatmulAttrs &value, AbiError &error) { return decodeMatmulAttrs(data, value, error); }
    static std::array<uint8_t, 72> encode(const MatmulAttrs &value) { return encodeMatmulAttrs(value); }
};

template <> struct SemanticRecordTraits<MovementAttrs>
{
    static constexpr uint16_t section_type = 267;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "MovementAttrs";
    static bool decode(const uint8_t *data, MovementAttrs &value, AbiError &error) { return decodeMovementAttrs(data, value, error); }
    static std::array<uint8_t, 24> encode(const MovementAttrs &value) { return encodeMovementAttrs(value); }
};

template <> struct SemanticRecordTraits<NormAttrs>
{
    static constexpr uint16_t section_type = 268;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "NormAttrs";
    static bool decode(const uint8_t *data, NormAttrs &value, AbiError &error) { return decodeNormAttrs(data, value, error); }
    static std::array<uint8_t, 40> encode(const NormAttrs &value) { return encodeNormAttrs(value); }
};

template <> struct SemanticRecordTraits<ReduceAttrs>
{
    static constexpr uint16_t section_type = 269;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "ReduceAttrs";
    static bool decode(const uint8_t *data, ReduceAttrs &value, AbiError &error) { return decodeReduceAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const ReduceAttrs &value) { return encodeReduceAttrs(value); }
};

template <> struct SemanticRecordTraits<SoftmaxAttrs>
{
    static constexpr uint16_t section_type = 270;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "SoftmaxAttrs";
    static bool decode(const uint8_t *data, SoftmaxAttrs &value, AbiError &error) { return decodeSoftmaxAttrs(data, value, error); }
    static std::array<uint8_t, 32> encode(const SoftmaxAttrs &value) { return encodeSoftmaxAttrs(value); }
};

template <> struct SemanticRecordTraits<ViewAttrs>
{
    static constexpr uint16_t section_type = 271;
    static constexpr uint32_t record_bytes = 64;
    static constexpr std::string_view json_tag = "ViewAttrs";
    static bool decode(const uint8_t *data, ViewAttrs &value, AbiError &error) { return decodeViewAttrs(data, value, error); }
    static std::array<uint8_t, 64> encode(const ViewAttrs &value) { return encodeViewAttrs(value); }
};

template <> struct SemanticRecordTraits<AllocAttrs>
{
    static constexpr uint16_t section_type = 272;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "AllocAttrs";
    static bool decode(const uint8_t *data, AllocAttrs &value, AbiError &error) { return decodeAllocAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const AllocAttrs &value) { return encodeAllocAttrs(value); }
};

template <> struct SemanticRecordTraits<BarrierAttrs>
{
    static constexpr uint16_t section_type = 273;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "BarrierAttrs";
    static bool decode(const uint8_t *data, BarrierAttrs &value, AbiError &error) { return decodeBarrierAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const BarrierAttrs &value) { return encodeBarrierAttrs(value); }
};

template <> struct SemanticRecordTraits<BlockedMnkLayout>
{
    static constexpr uint16_t section_type = 274;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "BlockedMnkLayout";
    static bool decode(const uint8_t *data, BlockedMnkLayout &value, AbiError &error) { return decodeBlockedMnkLayout(data, value, error); }
    static std::array<uint8_t, 40> encode(const BlockedMnkLayout &value) { return encodeBlockedMnkLayout(value); }
};

template <> struct SemanticRecordTraits<BufferObject>
{
    static constexpr uint16_t section_type = 275;
    static constexpr uint32_t record_bytes = 88;
    static constexpr std::string_view json_tag = "BufferObject";
    static bool decode(const uint8_t *data, BufferObject &value, AbiError &error) { return decodeBufferObject(data, value, error); }
    static std::array<uint8_t, 88> encode(const BufferObject &value) { return encodeBufferObject(value); }
};

template <> struct SemanticRecordTraits<BufferView>
{
    static constexpr uint16_t section_type = 276;
    static constexpr uint32_t record_bytes = 96;
    static constexpr std::string_view json_tag = "BufferView";
    static bool decode(const uint8_t *data, BufferView &value, AbiError &error) { return decodeBufferView(data, value, error); }
    static std::array<uint8_t, 96> encode(const BufferView &value) { return encodeBufferView(value); }
};

template <> struct SemanticRecordTraits<CollectiveAttrs>
{
    static constexpr uint16_t section_type = 277;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "CollectiveAttrs";
    static bool decode(const uint8_t *data, CollectiveAttrs &value, AbiError &error) { return decodeCollectiveAttrs(data, value, error); }
    static std::array<uint8_t, 56> encode(const CollectiveAttrs &value) { return encodeCollectiveAttrs(value); }
};

template <> struct SemanticRecordTraits<ControlToken>
{
    static constexpr uint16_t section_type = 278;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "ControlToken";
    static bool decode(const uint8_t *data, ControlToken &value, AbiError &error) { return decodeControlToken(data, value, error); }
    static std::array<uint8_t, 24> encode(const ControlToken &value) { return encodeControlToken(value); }
};

template <> struct SemanticRecordTraits<DmaAttrs>
{
    static constexpr uint16_t section_type = 279;
    static constexpr uint32_t record_bytes = 64;
    static constexpr std::string_view json_tag = "DmaAttrs";
    static bool decode(const uint8_t *data, DmaAttrs &value, AbiError &error) { return decodeDmaAttrs(data, value, error); }
    static std::array<uint8_t, 64> encode(const DmaAttrs &value) { return encodeDmaAttrs(value); }
};

template <> struct SemanticRecordTraits<ElementRegion>
{
    static constexpr uint16_t section_type = 280;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "ElementRegion";
    static bool decode(const uint8_t *data, ElementRegion &value, AbiError &error) { return decodeElementRegion(data, value, error); }
    static std::array<uint8_t, 32> encode(const ElementRegion &value) { return encodeElementRegion(value); }
};

template <> struct SemanticRecordTraits<GemmKernelAttrs>
{
    static constexpr uint16_t section_type = 281;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "GemmKernelAttrs";
    static bool decode(const uint8_t *data, GemmKernelAttrs &value, AbiError &error) { return decodeGemmKernelAttrs(data, value, error); }
    static std::array<uint8_t, 56> encode(const GemmKernelAttrs &value) { return encodeGemmKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<KernelComputation>
{
    static constexpr uint16_t section_type = 282;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "KernelComputation";
    static bool decode(const uint8_t *data, KernelComputation &value, AbiError &error) { return decodeKernelComputation(data, value, error); }
    static std::array<uint8_t, 48> encode(const KernelComputation &value) { return encodeKernelComputation(value); }
};

template <> struct SemanticRecordTraits<KernelCost>
{
    static constexpr uint16_t section_type = 283;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "KernelCost";
    static bool decode(const uint8_t *data, KernelCost &value, AbiError &error) { return decodeKernelCost(data, value, error); }
    static std::array<uint8_t, 56> encode(const KernelCost &value) { return encodeKernelCost(value); }
};

template <> struct SemanticRecordTraits<KernelOp>
{
    static constexpr uint16_t section_type = 284;
    static constexpr uint32_t record_bytes = 96;
    static constexpr std::string_view json_tag = "KernelOp";
    static bool decode(const uint8_t *data, KernelOp &value, AbiError &error) { return decodeKernelOp(data, value, error); }
    static std::array<uint8_t, 96> encode(const KernelOp &value) { return encodeKernelOp(value); }
};

template <> struct SemanticRecordTraits<KernelTensor>
{
    static constexpr uint16_t section_type = 285;
    static constexpr uint32_t record_bytes = 128;
    static constexpr std::string_view json_tag = "KernelTensor";
    static bool decode(const uint8_t *data, KernelTensor &value, AbiError &error) { return decodeKernelTensor(data, value, error); }
    static std::array<uint8_t, 128> encode(const KernelTensor &value) { return encodeKernelTensor(value); }
};

template <> struct SemanticRecordTraits<KernelTile>
{
    static constexpr uint16_t section_type = 286;
    static constexpr uint32_t record_bytes = 104;
    static constexpr std::string_view json_tag = "KernelTile";
    static bool decode(const uint8_t *data, KernelTile &value, AbiError &error) { return decodeKernelTile(data, value, error); }
    static std::array<uint8_t, 104> encode(const KernelTile &value) { return encodeKernelTile(value); }
};

template <> struct SemanticRecordTraits<LocalCopyAttrs>
{
    static constexpr uint16_t section_type = 287;
    static constexpr uint32_t record_bytes = 8;
    static constexpr std::string_view json_tag = "LocalCopyAttrs";
    static bool decode(const uint8_t *data, LocalCopyAttrs &value, AbiError &error) { return decodeLocalCopyAttrs(data, value, error); }
    static std::array<uint8_t, 8> encode(const LocalCopyAttrs &value) { return encodeLocalCopyAttrs(value); }
};

template <> struct SemanticRecordTraits<LocalReduceAttrs>
{
    static constexpr uint16_t section_type = 288;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "LocalReduceAttrs";
    static bool decode(const uint8_t *data, LocalReduceAttrs &value, AbiError &error) { return decodeLocalReduceAttrs(data, value, error); }
    static std::array<uint8_t, 40> encode(const LocalReduceAttrs &value) { return encodeLocalReduceAttrs(value); }
};

template <> struct SemanticRecordTraits<MatrixEpilogueKernelAttrs>
{
    static constexpr uint16_t section_type = 289;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "MatrixEpilogueKernelAttrs";
    static bool decode(const uint8_t *data, MatrixEpilogueKernelAttrs &value, AbiError &error) { return decodeMatrixEpilogueKernelAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const MatrixEpilogueKernelAttrs &value) { return encodeMatrixEpilogueKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<MovementKernelAttrs>
{
    static constexpr uint16_t section_type = 290;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "MovementKernelAttrs";
    static bool decode(const uint8_t *data, MovementKernelAttrs &value, AbiError &error) { return decodeMovementKernelAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const MovementKernelAttrs &value) { return encodeMovementKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<NormKernelAttrs>
{
    static constexpr uint16_t section_type = 291;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "NormKernelAttrs";
    static bool decode(const uint8_t *data, NormKernelAttrs &value, AbiError &error) { return decodeNormKernelAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const NormKernelAttrs &value) { return encodeNormKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<OperandAccess>
{
    static constexpr uint16_t section_type = 292;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "OperandAccess";
    static bool decode(const uint8_t *data, OperandAccess &value, AbiError &error) { return decodeOperandAccess(data, value, error); }
    static std::array<uint8_t, 40> encode(const OperandAccess &value) { return encodeOperandAccess(value); }
};

template <> struct SemanticRecordTraits<PartialSumDefinition>
{
    static constexpr uint16_t section_type = 293;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "PartialSumDefinition";
    static bool decode(const uint8_t *data, PartialSumDefinition &value, AbiError &error) { return decodePartialSumDefinition(data, value, error); }
    static std::array<uint8_t, 48> encode(const PartialSumDefinition &value) { return encodePartialSumDefinition(value); }
};

template <> struct SemanticRecordTraits<Placement>
{
    static constexpr uint16_t section_type = 294;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "Placement";
    static bool decode(const uint8_t *data, Placement &value, AbiError &error) { return decodePlacement(data, value, error); }
    static std::array<uint8_t, 24> encode(const Placement &value) { return encodePlacement(value); }
};

template <> struct SemanticRecordTraits<RecvWaitAttrs>
{
    static constexpr uint16_t section_type = 295;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "RecvWaitAttrs";
    static bool decode(const uint8_t *data, RecvWaitAttrs &value, AbiError &error) { return decodeRecvWaitAttrs(data, value, error); }
    static std::array<uint8_t, 40> encode(const RecvWaitAttrs &value) { return encodeRecvWaitAttrs(value); }
};

template <> struct SemanticRecordTraits<ReductionKernelAttrs>
{
    static constexpr uint16_t section_type = 296;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "ReductionKernelAttrs";
    static bool decode(const uint8_t *data, ReductionKernelAttrs &value, AbiError &error) { return decodeReductionKernelAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const ReductionKernelAttrs &value) { return encodeReductionKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<SoftmaxKernelAttrs>
{
    static constexpr uint16_t section_type = 297;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "SoftmaxKernelAttrs";
    static bool decode(const uint8_t *data, SoftmaxKernelAttrs &value, AbiError &error) { return decodeSoftmaxKernelAttrs(data, value, error); }
    static std::array<uint8_t, 40> encode(const SoftmaxKernelAttrs &value) { return encodeSoftmaxKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<StateTransition>
{
    static constexpr uint16_t section_type = 298;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "StateTransition";
    static bool decode(const uint8_t *data, StateTransition &value, AbiError &error) { return decodeStateTransition(data, value, error); }
    static std::array<uint8_t, 48> encode(const StateTransition &value) { return encodeStateTransition(value); }
};

template <> struct SemanticRecordTraits<TensorShard>
{
    static constexpr uint16_t section_type = 299;
    static constexpr uint32_t record_bytes = 80;
    static constexpr std::string_view json_tag = "TensorShard";
    static bool decode(const uint8_t *data, TensorShard &value, AbiError &error) { return decodeTensorShard(data, value, error); }
    static std::array<uint8_t, 80> encode(const TensorShard &value) { return encodeTensorShard(value); }
};

template <> struct SemanticRecordTraits<TensorState>
{
    static constexpr uint16_t section_type = 300;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "TensorState";
    static bool decode(const uint8_t *data, TensorState &value, AbiError &error) { return decodeTensorState(data, value, error); }
    static std::array<uint8_t, 48> encode(const TensorState &value) { return encodeTensorState(value); }
};

template <> struct SemanticRecordTraits<VectorKernelAttrs>
{
    static constexpr uint16_t section_type = 301;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "VectorKernelAttrs";
    static bool decode(const uint8_t *data, VectorKernelAttrs &value, AbiError &error) { return decodeVectorKernelAttrs(data, value, error); }
    static std::array<uint8_t, 48> encode(const VectorKernelAttrs &value) { return encodeVectorKernelAttrs(value); }
};

template <> struct SemanticRecordTraits<ViewDeclarationAttrs>
{
    static constexpr uint16_t section_type = 302;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ViewDeclarationAttrs";
    static bool decode(const uint8_t *data, ViewDeclarationAttrs &value, AbiError &error) { return decodeViewDeclarationAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const ViewDeclarationAttrs &value) { return encodeViewDeclarationAttrs(value); }
};

template <> struct SemanticRecordTraits<AuthoredProgramOrigin>
{
    static constexpr uint16_t section_type = 303;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "AuthoredProgramOrigin";
    static bool decode(const uint8_t *data, AuthoredProgramOrigin &value, AbiError &error) { return decodeAuthoredProgramOrigin(data, value, error); }
    static std::array<uint8_t, 32> encode(const AuthoredProgramOrigin &value) { return encodeAuthoredProgramOrigin(value); }
};

template <> struct SemanticRecordTraits<AuthoredVariantLineage>
{
    static constexpr uint16_t section_type = 304;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "AuthoredVariantLineage";
    static bool decode(const uint8_t *data, AuthoredVariantLineage &value, AbiError &error) { return decodeAuthoredVariantLineage(data, value, error); }
    static std::array<uint8_t, 16> encode(const AuthoredVariantLineage &value) { return encodeAuthoredVariantLineage(value); }
};

template <> struct SemanticRecordTraits<AxiFenceAttrs>
{
    static constexpr uint16_t section_type = 305;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "AxiFenceAttrs";
    static bool decode(const uint8_t *data, AxiFenceAttrs &value, AbiError &error) { return decodeAxiFenceAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const AxiFenceAttrs &value) { return encodeAxiFenceAttrs(value); }
};

template <> struct SemanticRecordTraits<BarrierArrival>
{
    static constexpr uint16_t section_type = 306;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "BarrierArrival";
    static bool decode(const uint8_t *data, BarrierArrival &value, AbiError &error) { return decodeBarrierArrival(data, value, error); }
    static std::array<uint8_t, 40> encode(const BarrierArrival &value) { return encodeBarrierArrival(value); }
};

template <> struct SemanticRecordTraits<BarrierExecution>
{
    static constexpr uint16_t section_type = 307;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "BarrierExecution";
    static bool decode(const uint8_t *data, BarrierExecution &value, AbiError &error) { return decodeBarrierExecution(data, value, error); }
    static std::array<uint8_t, 16> encode(const BarrierExecution &value) { return encodeBarrierExecution(value); }
};

template <> struct SemanticRecordTraits<BarrierGroup>
{
    static constexpr uint16_t section_type = 308;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "BarrierGroup";
    static bool decode(const uint8_t *data, BarrierGroup &value, AbiError &error) { return decodeBarrierGroup(data, value, error); }
    static std::array<uint8_t, 40> encode(const BarrierGroup &value) { return encodeBarrierGroup(value); }
};

template <> struct SemanticRecordTraits<CommandSemantics>
{
    static constexpr uint16_t section_type = 309;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "CommandSemantics";
    static bool decode(const uint8_t *data, CommandSemantics &value, AbiError &error) { return decodeCommandSemantics(data, value, error); }
    static std::array<uint8_t, 32> encode(const CommandSemantics &value) { return encodeCommandSemantics(value); }
};

template <> struct SemanticRecordTraits<CompiledProgramOrigin>
{
    static constexpr uint16_t section_type = 310;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "CompiledProgramOrigin";
    static bool decode(const uint8_t *data, CompiledProgramOrigin &value, AbiError &error) { return decodeCompiledProgramOrigin(data, value, error); }
    static std::array<uint8_t, 16> encode(const CompiledProgramOrigin &value) { return encodeCompiledProgramOrigin(value); }
};

template <> struct SemanticRecordTraits<CompiledVariantLineage>
{
    static constexpr uint16_t section_type = 311;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "CompiledVariantLineage";
    static bool decode(const uint8_t *data, CompiledVariantLineage &value, AbiError &error) { return decodeCompiledVariantLineage(data, value, error); }
    static std::array<uint8_t, 24> encode(const CompiledVariantLineage &value) { return encodeCompiledVariantLineage(value); }
};

template <> struct SemanticRecordTraits<ComputeExecution>
{
    static constexpr uint16_t section_type = 312;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ComputeExecution";
    static bool decode(const uint8_t *data, ComputeExecution &value, AbiError &error) { return decodeComputeExecution(data, value, error); }
    static std::array<uint8_t, 16> encode(const ComputeExecution &value) { return encodeComputeExecution(value); }
};

template <> struct SemanticRecordTraits<ControlCommandSource>
{
    static constexpr uint16_t section_type = 313;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ControlCommandSource";
    static bool decode(const uint8_t *data, ControlCommandSource &value, AbiError &error) { return decodeControlCommandSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const ControlCommandSource &value) { return encodeControlCommandSource(value); }
};

template <> struct SemanticRecordTraits<ControlExecution>
{
    static constexpr uint16_t section_type = 314;
    static constexpr uint32_t record_bytes = 8;
    static constexpr std::string_view json_tag = "ControlExecution";
    static bool decode(const uint8_t *data, ControlExecution &value, AbiError &error) { return decodeControlExecution(data, value, error); }
    static std::array<uint8_t, 8> encode(const ControlExecution &value) { return encodeControlExecution(value); }
};

template <> struct SemanticRecordTraits<DescriptorEndpointUse>
{
    static constexpr uint16_t section_type = 315;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "DescriptorEndpointUse";
    static bool decode(const uint8_t *data, DescriptorEndpointUse &value, AbiError &error) { return decodeDescriptorEndpointUse(data, value, error); }
    static std::array<uint8_t, 40> encode(const DescriptorEndpointUse &value) { return encodeDescriptorEndpointUse(value); }
};

template <> struct SemanticRecordTraits<DescriptorGroup>
{
    static constexpr uint16_t section_type = 316;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "DescriptorGroup";
    static bool decode(const uint8_t *data, DescriptorGroup &value, AbiError &error) { return decodeDescriptorGroup(data, value, error); }
    static std::array<uint8_t, 48> encode(const DescriptorGroup &value) { return encodeDescriptorGroup(value); }
};

template <> struct SemanticRecordTraits<DescriptorSource>
{
    static constexpr uint16_t section_type = 317;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "DescriptorSource";
    static bool decode(const uint8_t *data, DescriptorSource &value, AbiError &error) { return decodeDescriptorSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const DescriptorSource &value) { return encodeDescriptorSource(value); }
};

template <> struct SemanticRecordTraits<DmaExecution>
{
    static constexpr uint16_t section_type = 318;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "DmaExecution";
    static bool decode(const uint8_t *data, DmaExecution &value, AbiError &error) { return decodeDmaExecution(data, value, error); }
    static std::array<uint8_t, 16> encode(const DmaExecution &value) { return encodeDmaExecution(value); }
};

template <> struct SemanticRecordTraits<EventSignalAttrs>
{
    static constexpr uint16_t section_type = 319;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "EventSignalAttrs";
    static bool decode(const uint8_t *data, EventSignalAttrs &value, AbiError &error) { return decodeEventSignalAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const EventSignalAttrs &value) { return encodeEventSignalAttrs(value); }
};

template <> struct SemanticRecordTraits<EventWaitAttrs>
{
    static constexpr uint16_t section_type = 320;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "EventWaitAttrs";
    static bool decode(const uint8_t *data, EventWaitAttrs &value, AbiError &error) { return decodeEventWaitAttrs(data, value, error); }
    static std::array<uint8_t, 16> encode(const EventWaitAttrs &value) { return encodeEventWaitAttrs(value); }
};

template <> struct SemanticRecordTraits<ExternalSlotBacking>
{
    static constexpr uint16_t section_type = 321;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ExternalSlotBacking";
    static bool decode(const uint8_t *data, ExternalSlotBacking &value, AbiError &error) { return decodeExternalSlotBacking(data, value, error); }
    static std::array<uint8_t, 16> encode(const ExternalSlotBacking &value) { return encodeExternalSlotBacking(value); }
};

template <> struct SemanticRecordTraits<HaltAttrs>
{
    static constexpr uint16_t section_type = 322;
    static constexpr uint32_t record_bytes = 8;
    static constexpr std::string_view json_tag = "HaltAttrs";
    static bool decode(const uint8_t *data, HaltAttrs &value, AbiError &error) { return decodeHaltAttrs(data, value, error); }
    static std::array<uint8_t, 8> encode(const HaltAttrs &value) { return encodeHaltAttrs(value); }
};

template <> struct SemanticRecordTraits<IdSpan>
{
    static constexpr uint16_t section_type = 323;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "IdSpan";
    static bool decode(const uint8_t *data, IdSpan &value, AbiError &error) { return decodeIdSpan(data, value, error); }
    static std::array<uint8_t, 24> encode(const IdSpan &value) { return encodeIdSpan(value); }
};

template <> struct SemanticRecordTraits<KernelCommandSource>
{
    static constexpr uint16_t section_type = 324;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "KernelCommandSource";
    static bool decode(const uint8_t *data, KernelCommandSource &value, AbiError &error) { return decodeKernelCommandSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const KernelCommandSource &value) { return encodeKernelCommandSource(value); }
};

template <> struct SemanticRecordTraits<KernelTokenSource>
{
    static constexpr uint16_t section_type = 325;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "KernelTokenSource";
    static bool decode(const uint8_t *data, KernelTokenSource &value, AbiError &error) { return decodeKernelTokenSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const KernelTokenSource &value) { return encodeKernelTokenSource(value); }
};

template <> struct SemanticRecordTraits<LifecycleSource>
{
    static constexpr uint16_t section_type = 326;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "LifecycleSource";
    static bool decode(const uint8_t *data, LifecycleSource &value, AbiError &error) { return decodeLifecycleSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const LifecycleSource &value) { return encodeLifecycleSource(value); }
};

template <> struct SemanticRecordTraits<LocalAllocationBacking>
{
    static constexpr uint16_t section_type = 327;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "LocalAllocationBacking";
    static bool decode(const uint8_t *data, LocalAllocationBacking &value, AbiError &error) { return decodeLocalAllocationBacking(data, value, error); }
    static std::array<uint8_t, 16> encode(const LocalAllocationBacking &value) { return encodeLocalAllocationBacking(value); }
};

template <> struct SemanticRecordTraits<ObjectBacking>
{
    static constexpr uint16_t section_type = 328;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "ObjectBacking";
    static bool decode(const uint8_t *data, ObjectBacking &value, AbiError &error) { return decodeObjectBacking(data, value, error); }
    static std::array<uint8_t, 24> encode(const ObjectBacking &value) { return encodeObjectBacking(value); }
};

template <> struct SemanticRecordTraits<ObjectSource>
{
    static constexpr uint16_t section_type = 329;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "ObjectSource";
    static bool decode(const uint8_t *data, ObjectSource &value, AbiError &error) { return decodeObjectSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const ObjectSource &value) { return encodeObjectSource(value); }
};

template <> struct SemanticRecordTraits<ProgramSemantics>
{
    static constexpr uint16_t section_type = 330;
    static constexpr uint32_t record_bytes = 200;
    static constexpr std::string_view json_tag = "ProgramSemantics";
    static bool decode(const uint8_t *data, ProgramSemantics &value, AbiError &error) { return decodeProgramSemantics(data, value, error); }
    static std::array<uint8_t, 200> encode(const ProgramSemantics &value) { return encodeProgramSemantics(value); }
};

template <> struct SemanticRecordTraits<ProgramVariant>
{
    static constexpr uint16_t section_type = 331;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "ProgramVariant";
    static bool decode(const uint8_t *data, ProgramVariant &value, AbiError &error) { return decodeProgramVariant(data, value, error); }
    static std::array<uint8_t, 56> encode(const ProgramVariant &value) { return encodeProgramVariant(value); }
};

template <> struct SemanticRecordTraits<ReadAccessUse>
{
    static constexpr uint16_t section_type = 332;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "ReadAccessUse";
    static bool decode(const uint8_t *data, ReadAccessUse &value, AbiError &error) { return decodeReadAccessUse(data, value, error); }
    static std::array<uint8_t, 32> encode(const ReadAccessUse &value) { return encodeReadAccessUse(value); }
};

template <> struct SemanticRecordTraits<RecvWaitExecution>
{
    static constexpr uint16_t section_type = 333;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "RecvWaitExecution";
    static bool decode(const uint8_t *data, RecvWaitExecution &value, AbiError &error) { return decodeRecvWaitExecution(data, value, error); }
    static std::array<uint8_t, 16> encode(const RecvWaitExecution &value) { return encodeRecvWaitExecution(value); }
};

template <> struct SemanticRecordTraits<RepeatCommandAttrs>
{
    static constexpr uint16_t section_type = 334;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "RepeatCommandAttrs";
    static bool decode(const uint8_t *data, RepeatCommandAttrs &value, AbiError &error) { return decodeRepeatCommandAttrs(data, value, error); }
    static std::array<uint8_t, 32> encode(const RepeatCommandAttrs &value) { return encodeRepeatCommandAttrs(value); }
};

template <> struct SemanticRecordTraits<RequestBeginAttrs>
{
    static constexpr uint16_t section_type = 335;
    static constexpr uint32_t record_bytes = 8;
    static constexpr std::string_view json_tag = "RequestBeginAttrs";
    static bool decode(const uint8_t *data, RequestBeginAttrs &value, AbiError &error) { return decodeRequestBeginAttrs(data, value, error); }
    static std::array<uint8_t, 8> encode(const RequestBeginAttrs &value) { return encodeRequestBeginAttrs(value); }
};

template <> struct SemanticRecordTraits<RequestEndAttrs>
{
    static constexpr uint16_t section_type = 336;
    static constexpr uint32_t record_bytes = 8;
    static constexpr std::string_view json_tag = "RequestEndAttrs";
    static bool decode(const uint8_t *data, RequestEndAttrs &value, AbiError &error) { return decodeRequestEndAttrs(data, value, error); }
    static std::array<uint8_t, 8> encode(const RequestEndAttrs &value) { return encodeRequestEndAttrs(value); }
};

template <> struct SemanticRecordTraits<ResidentView>
{
    static constexpr uint16_t section_type = 337;
    static constexpr uint32_t record_bytes = 24;
    static constexpr std::string_view json_tag = "ResidentView";
    static bool decode(const uint8_t *data, ResidentView &value, AbiError &error) { return decodeResidentView(data, value, error); }
    static std::array<uint8_t, 24> encode(const ResidentView &value) { return encodeResidentView(value); }
};

template <> struct SemanticRecordTraits<ScheduledDependency>
{
    static constexpr uint16_t section_type = 338;
    static constexpr uint32_t record_bytes = 48;
    static constexpr std::string_view json_tag = "ScheduledDependency";
    static bool decode(const uint8_t *data, ScheduledDependency &value, AbiError &error) { return decodeScheduledDependency(data, value, error); }
    static std::array<uint8_t, 48> encode(const ScheduledDependency &value) { return encodeScheduledDependency(value); }
};

template <> struct SemanticRecordTraits<ScheduledStream>
{
    static constexpr uint16_t section_type = 339;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "ScheduledStream";
    static bool decode(const uint8_t *data, ScheduledStream &value, AbiError &error) { return decodeScheduledStream(data, value, error); }
    static std::array<uint8_t, 56> encode(const ScheduledStream &value) { return encodeScheduledStream(value); }
};

template <> struct SemanticRecordTraits<StateSource>
{
    static constexpr uint16_t section_type = 340;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "StateSource";
    static bool decode(const uint8_t *data, StateSource &value, AbiError &error) { return decodeStateSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const StateSource &value) { return encodeStateSource(value); }
};

template <> struct SemanticRecordTraits<StreamOrderSource>
{
    static constexpr uint16_t section_type = 341;
    static constexpr uint32_t record_bytes = 16;
    static constexpr std::string_view json_tag = "StreamOrderSource";
    static bool decode(const uint8_t *data, StreamOrderSource &value, AbiError &error) { return decodeStreamOrderSource(data, value, error); }
    static std::array<uint8_t, 16> encode(const StreamOrderSource &value) { return encodeStreamOrderSource(value); }
};

template <> struct SemanticRecordTraits<VariantMembership>
{
    static constexpr uint16_t section_type = 342;
    static constexpr uint32_t record_bytes = 200;
    static constexpr std::string_view json_tag = "VariantMembership";
    static bool decode(const uint8_t *data, VariantMembership &value, AbiError &error) { return decodeVariantMembership(data, value, error); }
    static std::array<uint8_t, 200> encode(const VariantMembership &value) { return encodeVariantMembership(value); }
};

template <> struct SemanticRecordTraits<WriteAccessUse>
{
    static constexpr uint16_t section_type = 343;
    static constexpr uint32_t record_bytes = 32;
    static constexpr std::string_view json_tag = "WriteAccessUse";
    static bool decode(const uint8_t *data, WriteAccessUse &value, AbiError &error) { return decodeWriteAccessUse(data, value, error); }
    static std::array<uint8_t, 32> encode(const WriteAccessUse &value) { return encodeWriteAccessUse(value); }
};

template <> struct SemanticRecordTraits<Binding>
{
    static constexpr uint16_t section_type = 344;
    static constexpr uint32_t record_bytes = 64;
    static constexpr std::string_view json_tag = "Binding";
    static bool decode(const uint8_t *data, Binding &value, AbiError &error) { return decodeBinding(data, value, error); }
    static std::array<uint8_t, 64> encode(const Binding &value) { return encodeBinding(value); }
};

template <> struct SemanticRecordTraits<BindingSlot>
{
    static constexpr uint16_t section_type = 345;
    static constexpr uint32_t record_bytes = 80;
    static constexpr std::string_view json_tag = "BindingSlot";
    static bool decode(const uint8_t *data, BindingSlot &value, AbiError &error) { return decodeBindingSlot(data, value, error); }
    static std::array<uint8_t, 80> encode(const BindingSlot &value) { return encodeBindingSlot(value); }
};

template <> struct SemanticRecordTraits<ChannelTraffic>
{
    static constexpr uint16_t section_type = 346;
    static constexpr uint32_t record_bytes = 120;
    static constexpr std::string_view json_tag = "ChannelTraffic";
    static bool decode(const uint8_t *data, ChannelTraffic &value, AbiError &error) { return decodeChannelTraffic(data, value, error); }
    static std::array<uint8_t, 120> encode(const ChannelTraffic &value) { return encodeChannelTraffic(value); }
};

template <> struct SemanticRecordTraits<DescriptorIdentity>
{
    static constexpr uint16_t section_type = 347;
    static constexpr uint32_t record_bytes = 80;
    static constexpr std::string_view json_tag = "DescriptorIdentity";
    static bool decode(const uint8_t *data, DescriptorIdentity &value, AbiError &error) { return decodeDescriptorIdentity(data, value, error); }
    static std::array<uint8_t, 80> encode(const DescriptorIdentity &value) { return encodeDescriptorIdentity(value); }
};

template <> struct SemanticRecordTraits<DescriptorTraffic>
{
    static constexpr uint16_t section_type = 348;
    static constexpr uint32_t record_bytes = 208;
    static constexpr std::string_view json_tag = "DescriptorTraffic";
    static bool decode(const uint8_t *data, DescriptorTraffic &value, AbiError &error) { return decodeDescriptorTraffic(data, value, error); }
    static std::array<uint8_t, 208> encode(const DescriptorTraffic &value) { return encodeDescriptorTraffic(value); }
};

template <> struct SemanticRecordTraits<TrafficAggregate>
{
    static constexpr uint16_t section_type = 349;
    static constexpr uint32_t record_bytes = 104;
    static constexpr std::string_view json_tag = "TrafficAggregate";
    static bool decode(const uint8_t *data, TrafficAggregate &value, AbiError &error) { return decodeTrafficAggregate(data, value, error); }
    static std::array<uint8_t, 104> encode(const TrafficAggregate &value) { return encodeTrafficAggregate(value); }
};

template <> struct SemanticRecordTraits<TrafficAggregateKey>
{
    static constexpr uint16_t section_type = 350;
    static constexpr uint32_t record_bytes = 72;
    static constexpr std::string_view json_tag = "TrafficAggregateKey";
    static bool decode(const uint8_t *data, TrafficAggregateKey &value, AbiError &error) { return decodeTrafficAggregateKey(data, value, error); }
    static std::array<uint8_t, 72> encode(const TrafficAggregateKey &value) { return encodeTrafficAggregateKey(value); }
};

template <> struct SemanticRecordTraits<TrafficChannelTotal>
{
    static constexpr uint16_t section_type = 351;
    static constexpr uint32_t record_bytes = 56;
    static constexpr std::string_view json_tag = "TrafficChannelTotal";
    static bool decode(const uint8_t *data, TrafficChannelTotal &value, AbiError &error) { return decodeTrafficChannelTotal(data, value, error); }
    static std::array<uint8_t, 56> encode(const TrafficChannelTotal &value) { return encodeTrafficChannelTotal(value); }
};

template <> struct SemanticRecordTraits<TrafficReport>
{
    static constexpr uint16_t section_type = 352;
    static constexpr uint32_t record_bytes = 40;
    static constexpr std::string_view json_tag = "TrafficReport";
    static bool decode(const uint8_t *data, TrafficReport &value, AbiError &error) { return decodeTrafficReport(data, value, error); }
    static std::array<uint8_t, 40> encode(const TrafficReport &value) { return encodeTrafficReport(value); }
};

template <typename Visitor> bool visitSemanticTables(SemanticTables &tables, Visitor &&visitor)
{
    if (!visitor(SemanticRecordTraits<ExecutionWorkPhase>{}, tables.execution_work_phase_rows)) return false;
    if (!visitor(SemanticRecordTraits<WorkEstimate>{}, tables.work_estimate_rows)) return false;
    if (!visitor(SemanticRecordTraits<Add>{}, tables.add_rows)) return false;
    if (!visitor(SemanticRecordTraits<CeilDivByConst>{}, tables.ceil_div_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<Const>{}, tables.const_rows)) return false;
    if (!visitor(SemanticRecordTraits<FloorDivByConst>{}, tables.floor_div_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<MulByConst>{}, tables.mul_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<Symbol>{}, tables.symbol_rows)) return false;
    if (!visitor(SemanticRecordTraits<ElementwiseAttrs>{}, tables.elementwise_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<EmbeddingAttrs>{}, tables.embedding_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MatmulAttrs>{}, tables.matmul_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MovementAttrs>{}, tables.movement_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<NormAttrs>{}, tables.norm_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReduceAttrs>{}, tables.reduce_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<SoftmaxAttrs>{}, tables.softmax_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ViewAttrs>{}, tables.view_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<AllocAttrs>{}, tables.alloc_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierAttrs>{}, tables.barrier_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BlockedMnkLayout>{}, tables.blocked_mnk_layout_rows)) return false;
    if (!visitor(SemanticRecordTraits<BufferObject>{}, tables.buffer_object_rows)) return false;
    if (!visitor(SemanticRecordTraits<BufferView>{}, tables.buffer_view_rows)) return false;
    if (!visitor(SemanticRecordTraits<CollectiveAttrs>{}, tables.collective_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlToken>{}, tables.control_token_rows)) return false;
    if (!visitor(SemanticRecordTraits<DmaAttrs>{}, tables.dma_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ElementRegion>{}, tables.element_region_rows)) return false;
    if (!visitor(SemanticRecordTraits<GemmKernelAttrs>{}, tables.gemm_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelComputation>{}, tables.kernel_computation_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelCost>{}, tables.kernel_cost_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelOp>{}, tables.kernel_op_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTensor>{}, tables.kernel_tensor_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTile>{}, tables.kernel_tile_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalCopyAttrs>{}, tables.local_copy_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalReduceAttrs>{}, tables.local_reduce_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MatrixEpilogueKernelAttrs>{}, tables.matrix_epilogue_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MovementKernelAttrs>{}, tables.movement_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<NormKernelAttrs>{}, tables.norm_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<OperandAccess>{}, tables.operand_access_rows)) return false;
    if (!visitor(SemanticRecordTraits<PartialSumDefinition>{}, tables.partial_sum_definition_rows)) return false;
    if (!visitor(SemanticRecordTraits<Placement>{}, tables.placement_rows)) return false;
    if (!visitor(SemanticRecordTraits<RecvWaitAttrs>{}, tables.recv_wait_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReductionKernelAttrs>{}, tables.reduction_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<SoftmaxKernelAttrs>{}, tables.softmax_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<StateTransition>{}, tables.state_transition_rows)) return false;
    if (!visitor(SemanticRecordTraits<TensorShard>{}, tables.tensor_shard_rows)) return false;
    if (!visitor(SemanticRecordTraits<TensorState>{}, tables.tensor_state_rows)) return false;
    if (!visitor(SemanticRecordTraits<VectorKernelAttrs>{}, tables.vector_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ViewDeclarationAttrs>{}, tables.view_declaration_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<AuthoredProgramOrigin>{}, tables.authored_program_origin_rows)) return false;
    if (!visitor(SemanticRecordTraits<AuthoredVariantLineage>{}, tables.authored_variant_lineage_rows)) return false;
    if (!visitor(SemanticRecordTraits<AxiFenceAttrs>{}, tables.axi_fence_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierArrival>{}, tables.barrier_arrival_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierExecution>{}, tables.barrier_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierGroup>{}, tables.barrier_group_rows)) return false;
    if (!visitor(SemanticRecordTraits<CommandSemantics>{}, tables.command_semantics_rows)) return false;
    if (!visitor(SemanticRecordTraits<CompiledProgramOrigin>{}, tables.compiled_program_origin_rows)) return false;
    if (!visitor(SemanticRecordTraits<CompiledVariantLineage>{}, tables.compiled_variant_lineage_rows)) return false;
    if (!visitor(SemanticRecordTraits<ComputeExecution>{}, tables.compute_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlCommandSource>{}, tables.control_command_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlExecution>{}, tables.control_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorEndpointUse>{}, tables.descriptor_endpoint_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorGroup>{}, tables.descriptor_group_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorSource>{}, tables.descriptor_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<DmaExecution>{}, tables.dma_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<EventSignalAttrs>{}, tables.event_signal_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<EventWaitAttrs>{}, tables.event_wait_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ExternalSlotBacking>{}, tables.external_slot_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<HaltAttrs>{}, tables.halt_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<IdSpan>{}, tables.id_span_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelCommandSource>{}, tables.kernel_command_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTokenSource>{}, tables.kernel_token_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<LifecycleSource>{}, tables.lifecycle_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalAllocationBacking>{}, tables.local_allocation_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<ObjectBacking>{}, tables.object_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<ObjectSource>{}, tables.object_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<ProgramSemantics>{}, tables.program_semantics_rows)) return false;
    if (!visitor(SemanticRecordTraits<ProgramVariant>{}, tables.program_variant_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReadAccessUse>{}, tables.read_access_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<RecvWaitExecution>{}, tables.recv_wait_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<RepeatCommandAttrs>{}, tables.repeat_command_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<RequestBeginAttrs>{}, tables.request_begin_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<RequestEndAttrs>{}, tables.request_end_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ResidentView>{}, tables.resident_view_rows)) return false;
    if (!visitor(SemanticRecordTraits<ScheduledDependency>{}, tables.scheduled_dependency_rows)) return false;
    if (!visitor(SemanticRecordTraits<ScheduledStream>{}, tables.scheduled_stream_rows)) return false;
    if (!visitor(SemanticRecordTraits<StateSource>{}, tables.state_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<StreamOrderSource>{}, tables.stream_order_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<VariantMembership>{}, tables.variant_membership_rows)) return false;
    if (!visitor(SemanticRecordTraits<WriteAccessUse>{}, tables.write_access_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<Binding>{}, tables.binding_rows)) return false;
    if (!visitor(SemanticRecordTraits<BindingSlot>{}, tables.binding_slot_rows)) return false;
    if (!visitor(SemanticRecordTraits<ChannelTraffic>{}, tables.channel_traffic_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorIdentity>{}, tables.descriptor_identity_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorTraffic>{}, tables.descriptor_traffic_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficAggregate>{}, tables.traffic_aggregate_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficAggregateKey>{}, tables.traffic_aggregate_key_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficChannelTotal>{}, tables.traffic_channel_total_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficReport>{}, tables.traffic_report_rows)) return false;
    return true;
}

template <typename Visitor> bool dispatchSemanticTable(uint16_t section_type, SemanticTables &tables, Visitor &&visitor)
{
    switch (section_type) {
    case 256: return visitor(SemanticRecordTraits<ExecutionWorkPhase>{}, tables.execution_work_phase_rows);
    case 257: return visitor(SemanticRecordTraits<WorkEstimate>{}, tables.work_estimate_rows);
    case 258: return visitor(SemanticRecordTraits<Add>{}, tables.add_rows);
    case 259: return visitor(SemanticRecordTraits<CeilDivByConst>{}, tables.ceil_div_by_const_rows);
    case 260: return visitor(SemanticRecordTraits<Const>{}, tables.const_rows);
    case 261: return visitor(SemanticRecordTraits<FloorDivByConst>{}, tables.floor_div_by_const_rows);
    case 262: return visitor(SemanticRecordTraits<MulByConst>{}, tables.mul_by_const_rows);
    case 263: return visitor(SemanticRecordTraits<Symbol>{}, tables.symbol_rows);
    case 264: return visitor(SemanticRecordTraits<ElementwiseAttrs>{}, tables.elementwise_attrs_rows);
    case 265: return visitor(SemanticRecordTraits<EmbeddingAttrs>{}, tables.embedding_attrs_rows);
    case 266: return visitor(SemanticRecordTraits<MatmulAttrs>{}, tables.matmul_attrs_rows);
    case 267: return visitor(SemanticRecordTraits<MovementAttrs>{}, tables.movement_attrs_rows);
    case 268: return visitor(SemanticRecordTraits<NormAttrs>{}, tables.norm_attrs_rows);
    case 269: return visitor(SemanticRecordTraits<ReduceAttrs>{}, tables.reduce_attrs_rows);
    case 270: return visitor(SemanticRecordTraits<SoftmaxAttrs>{}, tables.softmax_attrs_rows);
    case 271: return visitor(SemanticRecordTraits<ViewAttrs>{}, tables.view_attrs_rows);
    case 272: return visitor(SemanticRecordTraits<AllocAttrs>{}, tables.alloc_attrs_rows);
    case 273: return visitor(SemanticRecordTraits<BarrierAttrs>{}, tables.barrier_attrs_rows);
    case 274: return visitor(SemanticRecordTraits<BlockedMnkLayout>{}, tables.blocked_mnk_layout_rows);
    case 275: return visitor(SemanticRecordTraits<BufferObject>{}, tables.buffer_object_rows);
    case 276: return visitor(SemanticRecordTraits<BufferView>{}, tables.buffer_view_rows);
    case 277: return visitor(SemanticRecordTraits<CollectiveAttrs>{}, tables.collective_attrs_rows);
    case 278: return visitor(SemanticRecordTraits<ControlToken>{}, tables.control_token_rows);
    case 279: return visitor(SemanticRecordTraits<DmaAttrs>{}, tables.dma_attrs_rows);
    case 280: return visitor(SemanticRecordTraits<ElementRegion>{}, tables.element_region_rows);
    case 281: return visitor(SemanticRecordTraits<GemmKernelAttrs>{}, tables.gemm_kernel_attrs_rows);
    case 282: return visitor(SemanticRecordTraits<KernelComputation>{}, tables.kernel_computation_rows);
    case 283: return visitor(SemanticRecordTraits<KernelCost>{}, tables.kernel_cost_rows);
    case 284: return visitor(SemanticRecordTraits<KernelOp>{}, tables.kernel_op_rows);
    case 285: return visitor(SemanticRecordTraits<KernelTensor>{}, tables.kernel_tensor_rows);
    case 286: return visitor(SemanticRecordTraits<KernelTile>{}, tables.kernel_tile_rows);
    case 287: return visitor(SemanticRecordTraits<LocalCopyAttrs>{}, tables.local_copy_attrs_rows);
    case 288: return visitor(SemanticRecordTraits<LocalReduceAttrs>{}, tables.local_reduce_attrs_rows);
    case 289: return visitor(SemanticRecordTraits<MatrixEpilogueKernelAttrs>{}, tables.matrix_epilogue_kernel_attrs_rows);
    case 290: return visitor(SemanticRecordTraits<MovementKernelAttrs>{}, tables.movement_kernel_attrs_rows);
    case 291: return visitor(SemanticRecordTraits<NormKernelAttrs>{}, tables.norm_kernel_attrs_rows);
    case 292: return visitor(SemanticRecordTraits<OperandAccess>{}, tables.operand_access_rows);
    case 293: return visitor(SemanticRecordTraits<PartialSumDefinition>{}, tables.partial_sum_definition_rows);
    case 294: return visitor(SemanticRecordTraits<Placement>{}, tables.placement_rows);
    case 295: return visitor(SemanticRecordTraits<RecvWaitAttrs>{}, tables.recv_wait_attrs_rows);
    case 296: return visitor(SemanticRecordTraits<ReductionKernelAttrs>{}, tables.reduction_kernel_attrs_rows);
    case 297: return visitor(SemanticRecordTraits<SoftmaxKernelAttrs>{}, tables.softmax_kernel_attrs_rows);
    case 298: return visitor(SemanticRecordTraits<StateTransition>{}, tables.state_transition_rows);
    case 299: return visitor(SemanticRecordTraits<TensorShard>{}, tables.tensor_shard_rows);
    case 300: return visitor(SemanticRecordTraits<TensorState>{}, tables.tensor_state_rows);
    case 301: return visitor(SemanticRecordTraits<VectorKernelAttrs>{}, tables.vector_kernel_attrs_rows);
    case 302: return visitor(SemanticRecordTraits<ViewDeclarationAttrs>{}, tables.view_declaration_attrs_rows);
    case 303: return visitor(SemanticRecordTraits<AuthoredProgramOrigin>{}, tables.authored_program_origin_rows);
    case 304: return visitor(SemanticRecordTraits<AuthoredVariantLineage>{}, tables.authored_variant_lineage_rows);
    case 305: return visitor(SemanticRecordTraits<AxiFenceAttrs>{}, tables.axi_fence_attrs_rows);
    case 306: return visitor(SemanticRecordTraits<BarrierArrival>{}, tables.barrier_arrival_rows);
    case 307: return visitor(SemanticRecordTraits<BarrierExecution>{}, tables.barrier_execution_rows);
    case 308: return visitor(SemanticRecordTraits<BarrierGroup>{}, tables.barrier_group_rows);
    case 309: return visitor(SemanticRecordTraits<CommandSemantics>{}, tables.command_semantics_rows);
    case 310: return visitor(SemanticRecordTraits<CompiledProgramOrigin>{}, tables.compiled_program_origin_rows);
    case 311: return visitor(SemanticRecordTraits<CompiledVariantLineage>{}, tables.compiled_variant_lineage_rows);
    case 312: return visitor(SemanticRecordTraits<ComputeExecution>{}, tables.compute_execution_rows);
    case 313: return visitor(SemanticRecordTraits<ControlCommandSource>{}, tables.control_command_source_rows);
    case 314: return visitor(SemanticRecordTraits<ControlExecution>{}, tables.control_execution_rows);
    case 315: return visitor(SemanticRecordTraits<DescriptorEndpointUse>{}, tables.descriptor_endpoint_use_rows);
    case 316: return visitor(SemanticRecordTraits<DescriptorGroup>{}, tables.descriptor_group_rows);
    case 317: return visitor(SemanticRecordTraits<DescriptorSource>{}, tables.descriptor_source_rows);
    case 318: return visitor(SemanticRecordTraits<DmaExecution>{}, tables.dma_execution_rows);
    case 319: return visitor(SemanticRecordTraits<EventSignalAttrs>{}, tables.event_signal_attrs_rows);
    case 320: return visitor(SemanticRecordTraits<EventWaitAttrs>{}, tables.event_wait_attrs_rows);
    case 321: return visitor(SemanticRecordTraits<ExternalSlotBacking>{}, tables.external_slot_backing_rows);
    case 322: return visitor(SemanticRecordTraits<HaltAttrs>{}, tables.halt_attrs_rows);
    case 323: return visitor(SemanticRecordTraits<IdSpan>{}, tables.id_span_rows);
    case 324: return visitor(SemanticRecordTraits<KernelCommandSource>{}, tables.kernel_command_source_rows);
    case 325: return visitor(SemanticRecordTraits<KernelTokenSource>{}, tables.kernel_token_source_rows);
    case 326: return visitor(SemanticRecordTraits<LifecycleSource>{}, tables.lifecycle_source_rows);
    case 327: return visitor(SemanticRecordTraits<LocalAllocationBacking>{}, tables.local_allocation_backing_rows);
    case 328: return visitor(SemanticRecordTraits<ObjectBacking>{}, tables.object_backing_rows);
    case 329: return visitor(SemanticRecordTraits<ObjectSource>{}, tables.object_source_rows);
    case 330: return visitor(SemanticRecordTraits<ProgramSemantics>{}, tables.program_semantics_rows);
    case 331: return visitor(SemanticRecordTraits<ProgramVariant>{}, tables.program_variant_rows);
    case 332: return visitor(SemanticRecordTraits<ReadAccessUse>{}, tables.read_access_use_rows);
    case 333: return visitor(SemanticRecordTraits<RecvWaitExecution>{}, tables.recv_wait_execution_rows);
    case 334: return visitor(SemanticRecordTraits<RepeatCommandAttrs>{}, tables.repeat_command_attrs_rows);
    case 335: return visitor(SemanticRecordTraits<RequestBeginAttrs>{}, tables.request_begin_attrs_rows);
    case 336: return visitor(SemanticRecordTraits<RequestEndAttrs>{}, tables.request_end_attrs_rows);
    case 337: return visitor(SemanticRecordTraits<ResidentView>{}, tables.resident_view_rows);
    case 338: return visitor(SemanticRecordTraits<ScheduledDependency>{}, tables.scheduled_dependency_rows);
    case 339: return visitor(SemanticRecordTraits<ScheduledStream>{}, tables.scheduled_stream_rows);
    case 340: return visitor(SemanticRecordTraits<StateSource>{}, tables.state_source_rows);
    case 341: return visitor(SemanticRecordTraits<StreamOrderSource>{}, tables.stream_order_source_rows);
    case 342: return visitor(SemanticRecordTraits<VariantMembership>{}, tables.variant_membership_rows);
    case 343: return visitor(SemanticRecordTraits<WriteAccessUse>{}, tables.write_access_use_rows);
    case 344: return visitor(SemanticRecordTraits<Binding>{}, tables.binding_rows);
    case 345: return visitor(SemanticRecordTraits<BindingSlot>{}, tables.binding_slot_rows);
    case 346: return visitor(SemanticRecordTraits<ChannelTraffic>{}, tables.channel_traffic_rows);
    case 347: return visitor(SemanticRecordTraits<DescriptorIdentity>{}, tables.descriptor_identity_rows);
    case 348: return visitor(SemanticRecordTraits<DescriptorTraffic>{}, tables.descriptor_traffic_rows);
    case 349: return visitor(SemanticRecordTraits<TrafficAggregate>{}, tables.traffic_aggregate_rows);
    case 350: return visitor(SemanticRecordTraits<TrafficAggregateKey>{}, tables.traffic_aggregate_key_rows);
    case 351: return visitor(SemanticRecordTraits<TrafficChannelTotal>{}, tables.traffic_channel_total_rows);
    case 352: return visitor(SemanticRecordTraits<TrafficReport>{}, tables.traffic_report_rows);
    default: return false;
    }
}

template <typename Visitor> bool visitSemanticTables(const SemanticTables &tables, Visitor &&visitor)
{
    if (!visitor(SemanticRecordTraits<ExecutionWorkPhase>{}, tables.execution_work_phase_rows)) return false;
    if (!visitor(SemanticRecordTraits<WorkEstimate>{}, tables.work_estimate_rows)) return false;
    if (!visitor(SemanticRecordTraits<Add>{}, tables.add_rows)) return false;
    if (!visitor(SemanticRecordTraits<CeilDivByConst>{}, tables.ceil_div_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<Const>{}, tables.const_rows)) return false;
    if (!visitor(SemanticRecordTraits<FloorDivByConst>{}, tables.floor_div_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<MulByConst>{}, tables.mul_by_const_rows)) return false;
    if (!visitor(SemanticRecordTraits<Symbol>{}, tables.symbol_rows)) return false;
    if (!visitor(SemanticRecordTraits<ElementwiseAttrs>{}, tables.elementwise_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<EmbeddingAttrs>{}, tables.embedding_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MatmulAttrs>{}, tables.matmul_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MovementAttrs>{}, tables.movement_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<NormAttrs>{}, tables.norm_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReduceAttrs>{}, tables.reduce_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<SoftmaxAttrs>{}, tables.softmax_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ViewAttrs>{}, tables.view_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<AllocAttrs>{}, tables.alloc_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierAttrs>{}, tables.barrier_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BlockedMnkLayout>{}, tables.blocked_mnk_layout_rows)) return false;
    if (!visitor(SemanticRecordTraits<BufferObject>{}, tables.buffer_object_rows)) return false;
    if (!visitor(SemanticRecordTraits<BufferView>{}, tables.buffer_view_rows)) return false;
    if (!visitor(SemanticRecordTraits<CollectiveAttrs>{}, tables.collective_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlToken>{}, tables.control_token_rows)) return false;
    if (!visitor(SemanticRecordTraits<DmaAttrs>{}, tables.dma_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ElementRegion>{}, tables.element_region_rows)) return false;
    if (!visitor(SemanticRecordTraits<GemmKernelAttrs>{}, tables.gemm_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelComputation>{}, tables.kernel_computation_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelCost>{}, tables.kernel_cost_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelOp>{}, tables.kernel_op_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTensor>{}, tables.kernel_tensor_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTile>{}, tables.kernel_tile_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalCopyAttrs>{}, tables.local_copy_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalReduceAttrs>{}, tables.local_reduce_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MatrixEpilogueKernelAttrs>{}, tables.matrix_epilogue_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<MovementKernelAttrs>{}, tables.movement_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<NormKernelAttrs>{}, tables.norm_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<OperandAccess>{}, tables.operand_access_rows)) return false;
    if (!visitor(SemanticRecordTraits<PartialSumDefinition>{}, tables.partial_sum_definition_rows)) return false;
    if (!visitor(SemanticRecordTraits<Placement>{}, tables.placement_rows)) return false;
    if (!visitor(SemanticRecordTraits<RecvWaitAttrs>{}, tables.recv_wait_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReductionKernelAttrs>{}, tables.reduction_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<SoftmaxKernelAttrs>{}, tables.softmax_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<StateTransition>{}, tables.state_transition_rows)) return false;
    if (!visitor(SemanticRecordTraits<TensorShard>{}, tables.tensor_shard_rows)) return false;
    if (!visitor(SemanticRecordTraits<TensorState>{}, tables.tensor_state_rows)) return false;
    if (!visitor(SemanticRecordTraits<VectorKernelAttrs>{}, tables.vector_kernel_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ViewDeclarationAttrs>{}, tables.view_declaration_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<AuthoredProgramOrigin>{}, tables.authored_program_origin_rows)) return false;
    if (!visitor(SemanticRecordTraits<AuthoredVariantLineage>{}, tables.authored_variant_lineage_rows)) return false;
    if (!visitor(SemanticRecordTraits<AxiFenceAttrs>{}, tables.axi_fence_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierArrival>{}, tables.barrier_arrival_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierExecution>{}, tables.barrier_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<BarrierGroup>{}, tables.barrier_group_rows)) return false;
    if (!visitor(SemanticRecordTraits<CommandSemantics>{}, tables.command_semantics_rows)) return false;
    if (!visitor(SemanticRecordTraits<CompiledProgramOrigin>{}, tables.compiled_program_origin_rows)) return false;
    if (!visitor(SemanticRecordTraits<CompiledVariantLineage>{}, tables.compiled_variant_lineage_rows)) return false;
    if (!visitor(SemanticRecordTraits<ComputeExecution>{}, tables.compute_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlCommandSource>{}, tables.control_command_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<ControlExecution>{}, tables.control_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorEndpointUse>{}, tables.descriptor_endpoint_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorGroup>{}, tables.descriptor_group_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorSource>{}, tables.descriptor_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<DmaExecution>{}, tables.dma_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<EventSignalAttrs>{}, tables.event_signal_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<EventWaitAttrs>{}, tables.event_wait_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ExternalSlotBacking>{}, tables.external_slot_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<HaltAttrs>{}, tables.halt_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<IdSpan>{}, tables.id_span_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelCommandSource>{}, tables.kernel_command_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<KernelTokenSource>{}, tables.kernel_token_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<LifecycleSource>{}, tables.lifecycle_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<LocalAllocationBacking>{}, tables.local_allocation_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<ObjectBacking>{}, tables.object_backing_rows)) return false;
    if (!visitor(SemanticRecordTraits<ObjectSource>{}, tables.object_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<ProgramSemantics>{}, tables.program_semantics_rows)) return false;
    if (!visitor(SemanticRecordTraits<ProgramVariant>{}, tables.program_variant_rows)) return false;
    if (!visitor(SemanticRecordTraits<ReadAccessUse>{}, tables.read_access_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<RecvWaitExecution>{}, tables.recv_wait_execution_rows)) return false;
    if (!visitor(SemanticRecordTraits<RepeatCommandAttrs>{}, tables.repeat_command_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<RequestBeginAttrs>{}, tables.request_begin_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<RequestEndAttrs>{}, tables.request_end_attrs_rows)) return false;
    if (!visitor(SemanticRecordTraits<ResidentView>{}, tables.resident_view_rows)) return false;
    if (!visitor(SemanticRecordTraits<ScheduledDependency>{}, tables.scheduled_dependency_rows)) return false;
    if (!visitor(SemanticRecordTraits<ScheduledStream>{}, tables.scheduled_stream_rows)) return false;
    if (!visitor(SemanticRecordTraits<StateSource>{}, tables.state_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<StreamOrderSource>{}, tables.stream_order_source_rows)) return false;
    if (!visitor(SemanticRecordTraits<VariantMembership>{}, tables.variant_membership_rows)) return false;
    if (!visitor(SemanticRecordTraits<WriteAccessUse>{}, tables.write_access_use_rows)) return false;
    if (!visitor(SemanticRecordTraits<Binding>{}, tables.binding_rows)) return false;
    if (!visitor(SemanticRecordTraits<BindingSlot>{}, tables.binding_slot_rows)) return false;
    if (!visitor(SemanticRecordTraits<ChannelTraffic>{}, tables.channel_traffic_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorIdentity>{}, tables.descriptor_identity_rows)) return false;
    if (!visitor(SemanticRecordTraits<DescriptorTraffic>{}, tables.descriptor_traffic_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficAggregate>{}, tables.traffic_aggregate_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficAggregateKey>{}, tables.traffic_aggregate_key_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficChannelTotal>{}, tables.traffic_channel_total_rows)) return false;
    if (!visitor(SemanticRecordTraits<TrafficReport>{}, tables.traffic_report_rows)) return false;
    return true;
}

template <typename Visitor> bool dispatchSemanticTable(uint16_t section_type, const SemanticTables &tables, Visitor &&visitor)
{
    switch (section_type) {
    case 256: return visitor(SemanticRecordTraits<ExecutionWorkPhase>{}, tables.execution_work_phase_rows);
    case 257: return visitor(SemanticRecordTraits<WorkEstimate>{}, tables.work_estimate_rows);
    case 258: return visitor(SemanticRecordTraits<Add>{}, tables.add_rows);
    case 259: return visitor(SemanticRecordTraits<CeilDivByConst>{}, tables.ceil_div_by_const_rows);
    case 260: return visitor(SemanticRecordTraits<Const>{}, tables.const_rows);
    case 261: return visitor(SemanticRecordTraits<FloorDivByConst>{}, tables.floor_div_by_const_rows);
    case 262: return visitor(SemanticRecordTraits<MulByConst>{}, tables.mul_by_const_rows);
    case 263: return visitor(SemanticRecordTraits<Symbol>{}, tables.symbol_rows);
    case 264: return visitor(SemanticRecordTraits<ElementwiseAttrs>{}, tables.elementwise_attrs_rows);
    case 265: return visitor(SemanticRecordTraits<EmbeddingAttrs>{}, tables.embedding_attrs_rows);
    case 266: return visitor(SemanticRecordTraits<MatmulAttrs>{}, tables.matmul_attrs_rows);
    case 267: return visitor(SemanticRecordTraits<MovementAttrs>{}, tables.movement_attrs_rows);
    case 268: return visitor(SemanticRecordTraits<NormAttrs>{}, tables.norm_attrs_rows);
    case 269: return visitor(SemanticRecordTraits<ReduceAttrs>{}, tables.reduce_attrs_rows);
    case 270: return visitor(SemanticRecordTraits<SoftmaxAttrs>{}, tables.softmax_attrs_rows);
    case 271: return visitor(SemanticRecordTraits<ViewAttrs>{}, tables.view_attrs_rows);
    case 272: return visitor(SemanticRecordTraits<AllocAttrs>{}, tables.alloc_attrs_rows);
    case 273: return visitor(SemanticRecordTraits<BarrierAttrs>{}, tables.barrier_attrs_rows);
    case 274: return visitor(SemanticRecordTraits<BlockedMnkLayout>{}, tables.blocked_mnk_layout_rows);
    case 275: return visitor(SemanticRecordTraits<BufferObject>{}, tables.buffer_object_rows);
    case 276: return visitor(SemanticRecordTraits<BufferView>{}, tables.buffer_view_rows);
    case 277: return visitor(SemanticRecordTraits<CollectiveAttrs>{}, tables.collective_attrs_rows);
    case 278: return visitor(SemanticRecordTraits<ControlToken>{}, tables.control_token_rows);
    case 279: return visitor(SemanticRecordTraits<DmaAttrs>{}, tables.dma_attrs_rows);
    case 280: return visitor(SemanticRecordTraits<ElementRegion>{}, tables.element_region_rows);
    case 281: return visitor(SemanticRecordTraits<GemmKernelAttrs>{}, tables.gemm_kernel_attrs_rows);
    case 282: return visitor(SemanticRecordTraits<KernelComputation>{}, tables.kernel_computation_rows);
    case 283: return visitor(SemanticRecordTraits<KernelCost>{}, tables.kernel_cost_rows);
    case 284: return visitor(SemanticRecordTraits<KernelOp>{}, tables.kernel_op_rows);
    case 285: return visitor(SemanticRecordTraits<KernelTensor>{}, tables.kernel_tensor_rows);
    case 286: return visitor(SemanticRecordTraits<KernelTile>{}, tables.kernel_tile_rows);
    case 287: return visitor(SemanticRecordTraits<LocalCopyAttrs>{}, tables.local_copy_attrs_rows);
    case 288: return visitor(SemanticRecordTraits<LocalReduceAttrs>{}, tables.local_reduce_attrs_rows);
    case 289: return visitor(SemanticRecordTraits<MatrixEpilogueKernelAttrs>{}, tables.matrix_epilogue_kernel_attrs_rows);
    case 290: return visitor(SemanticRecordTraits<MovementKernelAttrs>{}, tables.movement_kernel_attrs_rows);
    case 291: return visitor(SemanticRecordTraits<NormKernelAttrs>{}, tables.norm_kernel_attrs_rows);
    case 292: return visitor(SemanticRecordTraits<OperandAccess>{}, tables.operand_access_rows);
    case 293: return visitor(SemanticRecordTraits<PartialSumDefinition>{}, tables.partial_sum_definition_rows);
    case 294: return visitor(SemanticRecordTraits<Placement>{}, tables.placement_rows);
    case 295: return visitor(SemanticRecordTraits<RecvWaitAttrs>{}, tables.recv_wait_attrs_rows);
    case 296: return visitor(SemanticRecordTraits<ReductionKernelAttrs>{}, tables.reduction_kernel_attrs_rows);
    case 297: return visitor(SemanticRecordTraits<SoftmaxKernelAttrs>{}, tables.softmax_kernel_attrs_rows);
    case 298: return visitor(SemanticRecordTraits<StateTransition>{}, tables.state_transition_rows);
    case 299: return visitor(SemanticRecordTraits<TensorShard>{}, tables.tensor_shard_rows);
    case 300: return visitor(SemanticRecordTraits<TensorState>{}, tables.tensor_state_rows);
    case 301: return visitor(SemanticRecordTraits<VectorKernelAttrs>{}, tables.vector_kernel_attrs_rows);
    case 302: return visitor(SemanticRecordTraits<ViewDeclarationAttrs>{}, tables.view_declaration_attrs_rows);
    case 303: return visitor(SemanticRecordTraits<AuthoredProgramOrigin>{}, tables.authored_program_origin_rows);
    case 304: return visitor(SemanticRecordTraits<AuthoredVariantLineage>{}, tables.authored_variant_lineage_rows);
    case 305: return visitor(SemanticRecordTraits<AxiFenceAttrs>{}, tables.axi_fence_attrs_rows);
    case 306: return visitor(SemanticRecordTraits<BarrierArrival>{}, tables.barrier_arrival_rows);
    case 307: return visitor(SemanticRecordTraits<BarrierExecution>{}, tables.barrier_execution_rows);
    case 308: return visitor(SemanticRecordTraits<BarrierGroup>{}, tables.barrier_group_rows);
    case 309: return visitor(SemanticRecordTraits<CommandSemantics>{}, tables.command_semantics_rows);
    case 310: return visitor(SemanticRecordTraits<CompiledProgramOrigin>{}, tables.compiled_program_origin_rows);
    case 311: return visitor(SemanticRecordTraits<CompiledVariantLineage>{}, tables.compiled_variant_lineage_rows);
    case 312: return visitor(SemanticRecordTraits<ComputeExecution>{}, tables.compute_execution_rows);
    case 313: return visitor(SemanticRecordTraits<ControlCommandSource>{}, tables.control_command_source_rows);
    case 314: return visitor(SemanticRecordTraits<ControlExecution>{}, tables.control_execution_rows);
    case 315: return visitor(SemanticRecordTraits<DescriptorEndpointUse>{}, tables.descriptor_endpoint_use_rows);
    case 316: return visitor(SemanticRecordTraits<DescriptorGroup>{}, tables.descriptor_group_rows);
    case 317: return visitor(SemanticRecordTraits<DescriptorSource>{}, tables.descriptor_source_rows);
    case 318: return visitor(SemanticRecordTraits<DmaExecution>{}, tables.dma_execution_rows);
    case 319: return visitor(SemanticRecordTraits<EventSignalAttrs>{}, tables.event_signal_attrs_rows);
    case 320: return visitor(SemanticRecordTraits<EventWaitAttrs>{}, tables.event_wait_attrs_rows);
    case 321: return visitor(SemanticRecordTraits<ExternalSlotBacking>{}, tables.external_slot_backing_rows);
    case 322: return visitor(SemanticRecordTraits<HaltAttrs>{}, tables.halt_attrs_rows);
    case 323: return visitor(SemanticRecordTraits<IdSpan>{}, tables.id_span_rows);
    case 324: return visitor(SemanticRecordTraits<KernelCommandSource>{}, tables.kernel_command_source_rows);
    case 325: return visitor(SemanticRecordTraits<KernelTokenSource>{}, tables.kernel_token_source_rows);
    case 326: return visitor(SemanticRecordTraits<LifecycleSource>{}, tables.lifecycle_source_rows);
    case 327: return visitor(SemanticRecordTraits<LocalAllocationBacking>{}, tables.local_allocation_backing_rows);
    case 328: return visitor(SemanticRecordTraits<ObjectBacking>{}, tables.object_backing_rows);
    case 329: return visitor(SemanticRecordTraits<ObjectSource>{}, tables.object_source_rows);
    case 330: return visitor(SemanticRecordTraits<ProgramSemantics>{}, tables.program_semantics_rows);
    case 331: return visitor(SemanticRecordTraits<ProgramVariant>{}, tables.program_variant_rows);
    case 332: return visitor(SemanticRecordTraits<ReadAccessUse>{}, tables.read_access_use_rows);
    case 333: return visitor(SemanticRecordTraits<RecvWaitExecution>{}, tables.recv_wait_execution_rows);
    case 334: return visitor(SemanticRecordTraits<RepeatCommandAttrs>{}, tables.repeat_command_attrs_rows);
    case 335: return visitor(SemanticRecordTraits<RequestBeginAttrs>{}, tables.request_begin_attrs_rows);
    case 336: return visitor(SemanticRecordTraits<RequestEndAttrs>{}, tables.request_end_attrs_rows);
    case 337: return visitor(SemanticRecordTraits<ResidentView>{}, tables.resident_view_rows);
    case 338: return visitor(SemanticRecordTraits<ScheduledDependency>{}, tables.scheduled_dependency_rows);
    case 339: return visitor(SemanticRecordTraits<ScheduledStream>{}, tables.scheduled_stream_rows);
    case 340: return visitor(SemanticRecordTraits<StateSource>{}, tables.state_source_rows);
    case 341: return visitor(SemanticRecordTraits<StreamOrderSource>{}, tables.stream_order_source_rows);
    case 342: return visitor(SemanticRecordTraits<VariantMembership>{}, tables.variant_membership_rows);
    case 343: return visitor(SemanticRecordTraits<WriteAccessUse>{}, tables.write_access_use_rows);
    case 344: return visitor(SemanticRecordTraits<Binding>{}, tables.binding_rows);
    case 345: return visitor(SemanticRecordTraits<BindingSlot>{}, tables.binding_slot_rows);
    case 346: return visitor(SemanticRecordTraits<ChannelTraffic>{}, tables.channel_traffic_rows);
    case 347: return visitor(SemanticRecordTraits<DescriptorIdentity>{}, tables.descriptor_identity_rows);
    case 348: return visitor(SemanticRecordTraits<DescriptorTraffic>{}, tables.descriptor_traffic_rows);
    case 349: return visitor(SemanticRecordTraits<TrafficAggregate>{}, tables.traffic_aggregate_rows);
    case 350: return visitor(SemanticRecordTraits<TrafficAggregateKey>{}, tables.traffic_aggregate_key_rows);
    case 351: return visitor(SemanticRecordTraits<TrafficChannelTotal>{}, tables.traffic_channel_total_rows);
    case 352: return visitor(SemanticRecordTraits<TrafficReport>{}, tables.traffic_report_rows);
    default: return false;
    }
}

#endif

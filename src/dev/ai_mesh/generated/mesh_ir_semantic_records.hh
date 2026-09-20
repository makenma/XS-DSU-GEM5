#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_RECORDS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_RECORDS_HH

constexpr uint32_t kExecutionWorkPhaseBytes = 16;
constexpr uint32_t kExecutionWorkPhasePresenceMaskOffset = 0;
constexpr uint32_t kExecutionWorkPhaseWorkOffset = 8;
static_assert(kExecutionWorkPhaseBytes == 8 + 8);
struct ExecutionWorkPhase
{
    uint64_t presence_mask = 0;
    ListSpan work = {};
};

constexpr uint32_t kWorkEstimateBytes = 40;
constexpr uint32_t kWorkEstimatePresenceMaskOffset = 0;
constexpr uint32_t kWorkEstimateEngineOffset = 8;
constexpr uint32_t kWorkEstimateUnitOffset = 16;
constexpr uint32_t kWorkEstimateDtypeOffset = 24;
constexpr uint32_t kWorkEstimateOperationsOffset = 32;
static_assert(kWorkEstimateBytes == 32 + 8);
struct WorkEstimate
{
    uint64_t presence_mask = 0;
    Engine engine = {};
    WorkUnit unit = {};
    DType dtype = {};
    uint64_t operations = 0;
};

constexpr uint32_t kAddBytes = 24;
constexpr uint32_t kAddPresenceMaskOffset = 0;
constexpr uint32_t kAddLhsOffset = 8;
constexpr uint32_t kAddRhsOffset = 16;
static_assert(kAddBytes == 16 + 8);
struct Add
{
    uint64_t presence_mask = 0;
    SemanticRef lhs = {};
    SemanticRef rhs = {};
};

constexpr uint32_t kCeilDivByConstBytes = 24;
constexpr uint32_t kCeilDivByConstPresenceMaskOffset = 0;
constexpr uint32_t kCeilDivByConstValueOffset = 8;
constexpr uint32_t kCeilDivByConstDivisorOffset = 16;
static_assert(kCeilDivByConstBytes == 16 + 8);
struct CeilDivByConst
{
    uint64_t presence_mask = 0;
    SemanticRef value = {};
    uint64_t divisor = 0;
};

constexpr uint32_t kConstBytes = 16;
constexpr uint32_t kConstPresenceMaskOffset = 0;
constexpr uint32_t kConstValueOffset = 8;
static_assert(kConstBytes == 8 + 8);
struct Const
{
    uint64_t presence_mask = 0;
    uint64_t value = 0;
};

constexpr uint32_t kFloorDivByConstBytes = 24;
constexpr uint32_t kFloorDivByConstPresenceMaskOffset = 0;
constexpr uint32_t kFloorDivByConstValueOffset = 8;
constexpr uint32_t kFloorDivByConstDivisorOffset = 16;
static_assert(kFloorDivByConstBytes == 16 + 8);
struct FloorDivByConst
{
    uint64_t presence_mask = 0;
    SemanticRef value = {};
    uint64_t divisor = 0;
};

constexpr uint32_t kMulByConstBytes = 24;
constexpr uint32_t kMulByConstPresenceMaskOffset = 0;
constexpr uint32_t kMulByConstValueOffset = 8;
constexpr uint32_t kMulByConstFactorOffset = 16;
static_assert(kMulByConstBytes == 16 + 8);
struct MulByConst
{
    uint64_t presence_mask = 0;
    SemanticRef value = {};
    uint64_t factor = 0;
};

constexpr uint32_t kSymbolBytes = 48;
constexpr uint32_t kSymbolPresenceMaskOffset = 0;
constexpr uint32_t kSymbolSymbolIdOffset = 8;
constexpr uint32_t kSymbolNameOffset = 16;
constexpr uint32_t kSymbolMinimumOffset = 24;
constexpr uint32_t kSymbolMaximumOffset = 32;
constexpr uint32_t kSymbolMultipleOfOffset = 40;
static_assert(kSymbolBytes == 40 + 8);
struct Symbol
{
    uint64_t presence_mask = 0;
    uint64_t symbol_id = 0;
    StringRef name = {};
    uint64_t minimum = 0;
    uint64_t maximum = 0;
    uint64_t multiple_of = 0;
};

constexpr uint32_t kElementwiseAttrsBytes = 48;
constexpr uint32_t kElementwiseAttrsPresenceMaskOffset = 0;
constexpr uint32_t kElementwiseAttrsScalarOffset = 8;
constexpr uint32_t kElementwiseAttrsScalarSideOffset = 24;
constexpr uint32_t kElementwiseAttrsAlphaOffset = 32;
constexpr uint32_t kElementwiseAttrsApproximationOffset = 40;
static_assert(kElementwiseAttrsBytes == 40 + 8);
struct ElementwiseAttrs
{
    uint64_t presence_mask = 0;
    ScalarValue scalar = {};
    StringRef scalar_side = {};
    double alpha = 0;
    StringRef approximation = {};
};

constexpr uint32_t kEmbeddingAttrsBytes = 48;
constexpr uint32_t kEmbeddingAttrsPresenceMaskOffset = 0;
constexpr uint32_t kEmbeddingAttrsPaddingIdxOffset = 8;
constexpr uint32_t kEmbeddingAttrsScaleGradByFreqOffset = 16;
constexpr uint32_t kEmbeddingAttrsSparseOffset = 24;
constexpr uint32_t kEmbeddingAttrsMaxNormOffset = 32;
constexpr uint32_t kEmbeddingAttrsNormTypeOffset = 40;
static_assert(kEmbeddingAttrsBytes == 40 + 8);
struct EmbeddingAttrs
{
    uint64_t presence_mask = 0;
    int64_t padding_idx = 0;
    bool scale_grad_by_freq = false;
    bool sparse = false;
    double max_norm = 0;
    double norm_type = 0;
};

constexpr uint32_t kMatmulAttrsBytes = 72;
constexpr uint32_t kMatmulAttrsPresenceMaskOffset = 0;
constexpr uint32_t kMatmulAttrsBatchAxesOffset = 8;
constexpr uint32_t kMatmulAttrsLhsContractAxisOffset = 16;
constexpr uint32_t kMatmulAttrsRhsContractAxisOffset = 24;
constexpr uint32_t kMatmulAttrsLhsTransposeOffset = 32;
constexpr uint32_t kMatmulAttrsRhsTransposeOffset = 40;
constexpr uint32_t kMatmulAttrsAlphaOffset = 48;
constexpr uint32_t kMatmulAttrsBetaOffset = 56;
constexpr uint32_t kMatmulAttrsAccumDtypeOffset = 64;
static_assert(kMatmulAttrsBytes == 64 + 8);
struct MatmulAttrs
{
    uint64_t presence_mask = 0;
    ListSpan batch_axes = {};
    int64_t lhs_contract_axis = 0;
    int64_t rhs_contract_axis = 0;
    bool lhs_transpose = false;
    bool rhs_transpose = false;
    double alpha = 0;
    double beta = 0;
    DType accum_dtype = {};
};

constexpr uint32_t kMovementAttrsBytes = 24;
constexpr uint32_t kMovementAttrsPresenceMaskOffset = 0;
constexpr uint32_t kMovementAttrsAxisOffset = 8;
constexpr uint32_t kMovementAttrsBoundsCheckOffset = 16;
static_assert(kMovementAttrsBytes == 16 + 8);
struct MovementAttrs
{
    uint64_t presence_mask = 0;
    uint64_t axis = 0;
    bool bounds_check = false;
};

constexpr uint32_t kNormAttrsBytes = 40;
constexpr uint32_t kNormAttrsPresenceMaskOffset = 0;
constexpr uint32_t kNormAttrsAxesOffset = 8;
constexpr uint32_t kNormAttrsEpsilonOffset = 16;
constexpr uint32_t kNormAttrsHasWeightOffset = 24;
constexpr uint32_t kNormAttrsHasBiasOffset = 32;
static_assert(kNormAttrsBytes == 32 + 8);
struct NormAttrs
{
    uint64_t presence_mask = 0;
    ListSpan axes = {};
    double epsilon = 0;
    bool has_weight = false;
    bool has_bias = false;
};

constexpr uint32_t kReduceAttrsBytes = 48;
constexpr uint32_t kReduceAttrsPresenceMaskOffset = 0;
constexpr uint32_t kReduceAttrsAxesOffset = 8;
constexpr uint32_t kReduceAttrsKeepdimOffset = 16;
constexpr uint32_t kReduceAttrsOutputDtypeOffset = 24;
constexpr uint32_t kReduceAttrsAccumDtypeOffset = 32;
constexpr uint32_t kReduceAttrsOrderOffset = 40;
static_assert(kReduceAttrsBytes == 40 + 8);
struct ReduceAttrs
{
    uint64_t presence_mask = 0;
    ListSpan axes = {};
    bool keepdim = false;
    DType output_dtype = {};
    DType accum_dtype = {};
    StringRef order = {};
};

constexpr uint32_t kSoftmaxAttrsBytes = 32;
constexpr uint32_t kSoftmaxAttrsPresenceMaskOffset = 0;
constexpr uint32_t kSoftmaxAttrsAxisOffset = 8;
constexpr uint32_t kSoftmaxAttrsOutputDtypeOffset = 16;
constexpr uint32_t kSoftmaxAttrsZeroFullyMaskedRowsOffset = 24;
static_assert(kSoftmaxAttrsBytes == 24 + 8);
struct SoftmaxAttrs
{
    uint64_t presence_mask = 0;
    uint64_t axis = 0;
    DType output_dtype = {};
    bool zero_fully_masked_rows = false;
};

constexpr uint32_t kViewAttrsBytes = 64;
constexpr uint32_t kViewAttrsPresenceMaskOffset = 0;
constexpr uint32_t kViewAttrsShapeOffset = 8;
constexpr uint32_t kViewAttrsPermutationOffset = 16;
constexpr uint32_t kViewAttrsAxesOffset = 24;
constexpr uint32_t kViewAttrsStartsOffset = 32;
constexpr uint32_t kViewAttrsEndsOffset = 40;
constexpr uint32_t kViewAttrsStepsOffset = 48;
constexpr uint32_t kViewAttrsExpandedAxesOffset = 56;
static_assert(kViewAttrsBytes == 56 + 8);
struct ViewAttrs
{
    uint64_t presence_mask = 0;
    ListSpan shape = {};
    ListSpan permutation = {};
    ListSpan axes = {};
    ListSpan starts = {};
    ListSpan ends = {};
    ListSpan steps = {};
    ListSpan expanded_axes = {};
};

constexpr uint32_t kAllocAttrsBytes = 16;
constexpr uint32_t kAllocAttrsPresenceMaskOffset = 0;
constexpr uint32_t kAllocAttrsObjectIdOffset = 8;
static_assert(kAllocAttrsBytes == 8 + 8);
struct AllocAttrs
{
    uint64_t presence_mask = 0;
    uint64_t object_id = 0;
};

constexpr uint32_t kBarrierAttrsBytes = 16;
constexpr uint32_t kBarrierAttrsPresenceMaskOffset = 0;
constexpr uint32_t kBarrierAttrsParticipantsOffset = 8;
static_assert(kBarrierAttrsBytes == 8 + 8);
struct BarrierAttrs
{
    uint64_t presence_mask = 0;
    ListSpan participants = {};
};

constexpr uint32_t kBlockedMnkLayoutBytes = 40;
constexpr uint32_t kBlockedMnkLayoutPresenceMaskOffset = 0;
constexpr uint32_t kBlockedMnkLayoutBlockMOffset = 8;
constexpr uint32_t kBlockedMnkLayoutBlockNOffset = 16;
constexpr uint32_t kBlockedMnkLayoutBlockKOffset = 24;
constexpr uint32_t kBlockedMnkLayoutMinorToMajorOffset = 32;
static_assert(kBlockedMnkLayoutBytes == 32 + 8);
struct BlockedMnkLayout
{
    uint64_t presence_mask = 0;
    uint64_t block_m = 0;
    uint64_t block_n = 0;
    uint64_t block_k = 0;
    ListSpan minor_to_major = {};
};

constexpr uint32_t kBufferObjectBytes = 88;
constexpr uint32_t kBufferObjectPresenceMaskOffset = 0;
constexpr uint32_t kBufferObjectObjectIdOffset = 8;
constexpr uint32_t kBufferObjectStorageTensorIdOffset = 16;
constexpr uint32_t kBufferObjectOwnerCoreOffset = 24;
constexpr uint32_t kBufferObjectMemorySpaceOffset = 32;
constexpr uint32_t kBufferObjectShapeOffset = 40;
constexpr uint32_t kBufferObjectStridesOffset = 48;
constexpr uint32_t kBufferObjectFootprintBytesOffset = 56;
constexpr uint32_t kBufferObjectAlignmentBytesOffset = 64;
constexpr uint32_t kBufferObjectPersistentOffset = 72;
constexpr uint32_t kBufferObjectBufferIndexOffset = 80;
static_assert(kBufferObjectBytes == 80 + 8);
struct BufferObject
{
    uint64_t presence_mask = 0;
    uint64_t object_id = 0;
    uint64_t storage_tensor_id = 0;
    uint64_t owner_core = 0;
    MemorySpace memory_space = {};
    ListSpan shape = {};
    ListSpan strides = {};
    uint64_t footprint_bytes = 0;
    uint64_t alignment_bytes = 0;
    bool persistent = false;
    uint64_t buffer_index = 0;
};

constexpr uint32_t kBufferViewBytes = 96;
constexpr uint32_t kBufferViewPresenceMaskOffset = 0;
constexpr uint32_t kBufferViewViewIdOffset = 8;
constexpr uint32_t kBufferViewObjectIdOffset = 16;
constexpr uint32_t kBufferViewShardIdOffset = 24;
constexpr uint32_t kBufferViewShardOriginOffset = 32;
constexpr uint32_t kBufferViewPaddedShapeOffset = 40;
constexpr uint32_t kBufferViewValidShapeOffset = 48;
constexpr uint32_t kBufferViewObjectOffsetElementsOffset = 56;
constexpr uint32_t kBufferViewObjectStridesOffset = 64;
constexpr uint32_t kBufferViewLayoutOffset = 72;
constexpr uint32_t kBufferViewBlockedLayoutOffset = 80;
constexpr uint32_t kBufferViewGenerationOffset = 88;
static_assert(kBufferViewBytes == 88 + 8);
struct BufferView
{
    uint64_t presence_mask = 0;
    uint64_t view_id = 0;
    uint64_t object_id = 0;
    uint64_t shard_id = 0;
    ListSpan shard_origin = {};
    ListSpan padded_shape = {};
    ListSpan valid_shape = {};
    uint64_t object_offset_elements = 0;
    ListSpan object_strides = {};
    Layout layout = {};
    SemanticRef blocked_layout = {};
    uint64_t generation = 0;
};

constexpr uint32_t kCollectiveAttrsBytes = 56;
constexpr uint32_t kCollectiveAttrsPresenceMaskOffset = 0;
constexpr uint32_t kCollectiveAttrsKindOffset = 8;
constexpr uint32_t kCollectiveAttrsParticipantsOffset = 16;
constexpr uint32_t kCollectiveAttrsAlgorithmOffset = 24;
constexpr uint32_t kCollectiveAttrsChunkBytesOffset = 32;
constexpr uint32_t kCollectiveAttrsReduceKindOffset = 40;
constexpr uint32_t kCollectiveAttrsPartialSumIdOffset = 48;
static_assert(kCollectiveAttrsBytes == 48 + 8);
struct CollectiveAttrs
{
    uint64_t presence_mask = 0;
    CollectiveKind kind = {};
    ListSpan participants = {};
    CollectiveAlgorithm algorithm = {};
    uint64_t chunk_bytes = 0;
    ReduceKind reduce_kind = {};
    uint64_t partial_sum_id = 0;
};

constexpr uint32_t kControlTokenBytes = 24;
constexpr uint32_t kControlTokenPresenceMaskOffset = 0;
constexpr uint32_t kControlTokenTokenIdOffset = 8;
constexpr uint32_t kControlTokenInitialOffset = 16;
static_assert(kControlTokenBytes == 16 + 8);
struct ControlToken
{
    uint64_t presence_mask = 0;
    uint64_t token_id = 0;
    bool initial = false;
};

constexpr uint32_t kDmaAttrsBytes = 64;
constexpr uint32_t kDmaAttrsPresenceMaskOffset = 0;
constexpr uint32_t kDmaAttrsKindOffset = 8;
constexpr uint32_t kDmaAttrsIssuingCoreOffset = 16;
constexpr uint32_t kDmaAttrsSourceCoreOffset = 24;
constexpr uint32_t kDmaAttrsDestinationCoreOffset = 32;
constexpr uint32_t kDmaAttrsTransferIdOffset = 40;
constexpr uint32_t kDmaAttrsFillPatternOffset = 48;
constexpr uint32_t kDmaAttrsMaxBurstBeatsOffset = 56;
static_assert(kDmaAttrsBytes == 56 + 8);
struct DmaAttrs
{
    uint64_t presence_mask = 0;
    DmaKind kind = {};
    uint64_t issuing_core = 0;
    uint64_t source_core = 0;
    uint64_t destination_core = 0;
    uint64_t transfer_id = 0;
    ListSpan fill_pattern = {};
    uint64_t max_burst_beats = 0;
};

constexpr uint32_t kElementRegionBytes = 32;
constexpr uint32_t kElementRegionPresenceMaskOffset = 0;
constexpr uint32_t kElementRegionOriginOffset = 8;
constexpr uint32_t kElementRegionShapeOffset = 16;
constexpr uint32_t kElementRegionStepsOffset = 24;
static_assert(kElementRegionBytes == 24 + 8);
struct ElementRegion
{
    uint64_t presence_mask = 0;
    ListSpan origin = {};
    ListSpan shape = {};
    ListSpan steps = {};
};

constexpr uint32_t kGemmKernelAttrsBytes = 56;
constexpr uint32_t kGemmKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kGemmKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kGemmKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kGemmKernelAttrsTileOffset = 24;
constexpr uint32_t kGemmKernelAttrsCostOffset = 32;
constexpr uint32_t kGemmKernelAttrsPhaseOffset = 40;
constexpr uint32_t kGemmKernelAttrsPartialSumIdOffset = 48;
static_assert(kGemmKernelAttrsBytes == 48 + 8);
struct GemmKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    MatrixPhase phase = {};
    uint64_t partial_sum_id = 0;
};

constexpr uint32_t kKernelComputationBytes = 48;
constexpr uint32_t kKernelComputationPresenceMaskOffset = 0;
constexpr uint32_t kKernelComputationComputationIdOffset = 8;
constexpr uint32_t kKernelComputationOpcodeOffset = 16;
constexpr uint32_t kKernelComputationOperandTensorIdsOffset = 24;
constexpr uint32_t kKernelComputationResultTensorIdOffset = 32;
constexpr uint32_t kKernelComputationAttrsOffset = 40;
static_assert(kKernelComputationBytes == 40 + 8);
struct KernelComputation
{
    uint64_t presence_mask = 0;
    uint64_t computation_id = 0;
    OpCode opcode = {};
    ListSpan operand_tensor_ids = {};
    uint64_t result_tensor_id = 0;
    SemanticRef attrs = {};
};

constexpr uint32_t kKernelCostBytes = 56;
constexpr uint32_t kKernelCostPresenceMaskOffset = 0;
constexpr uint32_t kKernelCostLogicalInputBytesOffset = 8;
constexpr uint32_t kKernelCostLogicalOutputBytesOffset = 16;
constexpr uint32_t kKernelCostLocalStorageBytesOffset = 24;
constexpr uint32_t kKernelCostMacsOffset = 32;
constexpr uint32_t kKernelCostVectorOpsOffset = 40;
constexpr uint32_t kKernelCostReductionOpsOffset = 48;
static_assert(kKernelCostBytes == 48 + 8);
struct KernelCost
{
    uint64_t presence_mask = 0;
    uint64_t logical_input_bytes = 0;
    uint64_t logical_output_bytes = 0;
    uint64_t local_storage_bytes = 0;
    uint64_t macs = 0;
    uint64_t vector_ops = 0;
    uint64_t reduction_ops = 0;
};

constexpr uint32_t kKernelOpBytes = 96;
constexpr uint32_t kKernelOpPresenceMaskOffset = 0;
constexpr uint32_t kKernelOpOpIdOffset = 8;
constexpr uint32_t kKernelOpComputationIdOffset = 16;
constexpr uint32_t kKernelOpStableKeyOffset = 24;
constexpr uint32_t kKernelOpOpcodeOffset = 32;
constexpr uint32_t kKernelOpOwnerCoreOffset = 40;
constexpr uint32_t kKernelOpResultShardIdOffset = 48;
constexpr uint32_t kKernelOpReadsOffset = 56;
constexpr uint32_t kKernelOpWritesOffset = 64;
constexpr uint32_t kKernelOpAttrsOffset = 72;
constexpr uint32_t kKernelOpAfterTokensOffset = 80;
constexpr uint32_t kKernelOpDoneTokenOffset = 88;
static_assert(kKernelOpBytes == 88 + 8);
struct KernelOp
{
    uint64_t presence_mask = 0;
    uint64_t op_id = 0;
    uint64_t computation_id = 0;
    StringRef stable_key = {};
    KernelOpcode opcode = {};
    uint64_t owner_core = 0;
    uint64_t result_shard_id = 0;
    ListSpan reads = {};
    ListSpan writes = {};
    SemanticRef attrs = {};
    ListSpan after_tokens = {};
    uint64_t done_token = 0;
};

constexpr uint32_t kKernelTensorBytes = 128;
constexpr uint32_t kKernelTensorPresenceMaskOffset = 0;
constexpr uint32_t kKernelTensorTensorIdOffset = 8;
constexpr uint32_t kKernelTensorProducerComputationIdOffset = 16;
constexpr uint32_t kKernelTensorSynthesizedPurposeOffset = 24;
constexpr uint32_t kKernelTensorAliasRootTensorIdOffset = 32;
constexpr uint32_t kKernelTensorStorageOffsetElementsOffset = 40;
constexpr uint32_t kKernelTensorNameOffset = 48;
constexpr uint32_t kKernelTensorRoleOffset = 56;
constexpr uint32_t kKernelTensorDtypeOffset = 64;
constexpr uint32_t kKernelTensorShapeOffset = 72;
constexpr uint32_t kKernelTensorStridesOffset = 80;
constexpr uint32_t kKernelTensorStorageClassOffset = 88;
constexpr uint32_t kKernelTensorAccessOffset = 96;
constexpr uint32_t kKernelTensorLogicalExtentBytesOffset = 104;
constexpr uint32_t kKernelTensorStorageExtentBytesOffset = 112;
constexpr uint32_t kKernelTensorContentSha256Offset = 120;
static_assert(kKernelTensorBytes == 120 + 8);
struct KernelTensor
{
    uint64_t presence_mask = 0;
    uint64_t tensor_id = 0;
    uint64_t producer_computation_id = 0;
    SynthesizedTensorPurpose synthesized_purpose = {};
    uint64_t alias_root_tensor_id = 0;
    uint64_t storage_offset_elements = 0;
    StringRef name = {};
    TensorRole role = {};
    DType dtype = {};
    ListSpan shape = {};
    ListSpan strides = {};
    StorageClass storage_class = {};
    Access access = {};
    uint64_t logical_extent_bytes = 0;
    uint64_t storage_extent_bytes = 0;
    StringRef content_sha256 = {};
};

constexpr uint32_t kKernelTileBytes = 104;
constexpr uint32_t kKernelTilePresenceMaskOffset = 0;
constexpr uint32_t kKernelTileBatchOriginOffset = 8;
constexpr uint32_t kKernelTileMOriginOffset = 16;
constexpr uint32_t kKernelTileNOriginOffset = 24;
constexpr uint32_t kKernelTileKOriginOffset = 32;
constexpr uint32_t kKernelTileBatchExtentOffset = 40;
constexpr uint32_t kKernelTileMExtentOffset = 48;
constexpr uint32_t kKernelTileNExtentOffset = 56;
constexpr uint32_t kKernelTileKExtentOffset = 64;
constexpr uint32_t kKernelTileValidBatchOffset = 72;
constexpr uint32_t kKernelTileValidMOffset = 80;
constexpr uint32_t kKernelTileValidNOffset = 88;
constexpr uint32_t kKernelTileValidKOffset = 96;
static_assert(kKernelTileBytes == 96 + 8);
struct KernelTile
{
    uint64_t presence_mask = 0;
    uint64_t batch_origin = 0;
    uint64_t m_origin = 0;
    uint64_t n_origin = 0;
    uint64_t k_origin = 0;
    uint64_t batch_extent = 0;
    uint64_t m_extent = 0;
    uint64_t n_extent = 0;
    uint64_t k_extent = 0;
    uint64_t valid_batch = 0;
    uint64_t valid_m = 0;
    uint64_t valid_n = 0;
    uint64_t valid_k = 0;
};

constexpr uint32_t kLocalCopyAttrsBytes = 8;
constexpr uint32_t kLocalCopyAttrsPresenceMaskOffset = 0;
static_assert(kLocalCopyAttrsBytes == 8);
struct LocalCopyAttrs
{
    uint64_t presence_mask = 0;
};

constexpr uint32_t kLocalReduceAttrsBytes = 40;
constexpr uint32_t kLocalReduceAttrsPresenceMaskOffset = 0;
constexpr uint32_t kLocalReduceAttrsReduceKindOffset = 8;
constexpr uint32_t kLocalReduceAttrsPartialSumIdOffset = 16;
constexpr uint32_t kLocalReduceAttrsTileOffset = 24;
constexpr uint32_t kLocalReduceAttrsCostOffset = 32;
static_assert(kLocalReduceAttrsBytes == 32 + 8);
struct LocalReduceAttrs
{
    uint64_t presence_mask = 0;
    ReduceKind reduce_kind = {};
    uint64_t partial_sum_id = 0;
    SemanticRef tile = {};
    SemanticRef cost = {};
};

constexpr uint32_t kMatrixEpilogueKernelAttrsBytes = 48;
constexpr uint32_t kMatrixEpilogueKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kMatrixEpilogueKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kMatrixEpilogueKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kMatrixEpilogueKernelAttrsTileOffset = 24;
constexpr uint32_t kMatrixEpilogueKernelAttrsCostOffset = 32;
constexpr uint32_t kMatrixEpilogueKernelAttrsAlgorithmOffset = 40;
static_assert(kMatrixEpilogueKernelAttrsBytes == 40 + 8);
struct MatrixEpilogueKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    MatrixEpilogueAlgorithm algorithm = {};
};

constexpr uint32_t kMovementKernelAttrsBytes = 48;
constexpr uint32_t kMovementKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kMovementKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kMovementKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kMovementKernelAttrsTileOffset = 24;
constexpr uint32_t kMovementKernelAttrsCostOffset = 32;
constexpr uint32_t kMovementKernelAttrsAlgorithmOffset = 40;
static_assert(kMovementKernelAttrsBytes == 40 + 8);
struct MovementKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    MovementAlgorithm algorithm = {};
};

constexpr uint32_t kNormKernelAttrsBytes = 48;
constexpr uint32_t kNormKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kNormKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kNormKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kNormKernelAttrsTileOffset = 24;
constexpr uint32_t kNormKernelAttrsCostOffset = 32;
constexpr uint32_t kNormKernelAttrsAlgorithmOffset = 40;
static_assert(kNormKernelAttrsBytes == 40 + 8);
struct NormKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    NormAlgorithm algorithm = {};
};

constexpr uint32_t kOperandAccessBytes = 40;
constexpr uint32_t kOperandAccessPresenceMaskOffset = 0;
constexpr uint32_t kOperandAccessStateIdOffset = 8;
constexpr uint32_t kOperandAccessViewIdOffset = 16;
constexpr uint32_t kOperandAccessRegionOffset = 24;
constexpr uint32_t kOperandAccessModeOffset = 32;
static_assert(kOperandAccessBytes == 32 + 8);
struct OperandAccess
{
    uint64_t presence_mask = 0;
    uint64_t state_id = 0;
    uint64_t view_id = 0;
    SemanticRef region = {};
    OperandAccessMode mode = {};
};

constexpr uint32_t kPartialSumDefinitionBytes = 48;
constexpr uint32_t kPartialSumDefinitionPresenceMaskOffset = 0;
constexpr uint32_t kPartialSumDefinitionPartialSumIdOffset = 8;
constexpr uint32_t kPartialSumDefinitionComputationIdOffset = 16;
constexpr uint32_t kPartialSumDefinitionSemanticResultTensorIdOffset = 24;
constexpr uint32_t kPartialSumDefinitionAccumulatorTensorIdOffset = 32;
constexpr uint32_t kPartialSumDefinitionPlacementIdOffset = 40;
static_assert(kPartialSumDefinitionBytes == 40 + 8);
struct PartialSumDefinition
{
    uint64_t presence_mask = 0;
    uint64_t partial_sum_id = 0;
    uint64_t computation_id = 0;
    uint64_t semantic_result_tensor_id = 0;
    uint64_t accumulator_tensor_id = 0;
    uint64_t placement_id = 0;
};

constexpr uint32_t kPlacementBytes = 24;
constexpr uint32_t kPlacementPresenceMaskOffset = 0;
constexpr uint32_t kPlacementPlacementIdOffset = 8;
constexpr uint32_t kPlacementCoreIdsOffset = 16;
static_assert(kPlacementBytes == 16 + 8);
struct Placement
{
    uint64_t presence_mask = 0;
    uint64_t placement_id = 0;
    ListSpan core_ids = {};
};

constexpr uint32_t kRecvWaitAttrsBytes = 40;
constexpr uint32_t kRecvWaitAttrsPresenceMaskOffset = 0;
constexpr uint32_t kRecvWaitAttrsTransferIdOffset = 8;
constexpr uint32_t kRecvWaitAttrsSourceCoreOffset = 16;
constexpr uint32_t kRecvWaitAttrsDestinationCoreOffset = 24;
constexpr uint32_t kRecvWaitAttrsExpectedBytesOffset = 32;
static_assert(kRecvWaitAttrsBytes == 32 + 8);
struct RecvWaitAttrs
{
    uint64_t presence_mask = 0;
    uint64_t transfer_id = 0;
    uint64_t source_core = 0;
    uint64_t destination_core = 0;
    uint64_t expected_bytes = 0;
};

constexpr uint32_t kReductionKernelAttrsBytes = 48;
constexpr uint32_t kReductionKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kReductionKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kReductionKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kReductionKernelAttrsTileOffset = 24;
constexpr uint32_t kReductionKernelAttrsCostOffset = 32;
constexpr uint32_t kReductionKernelAttrsAlgorithmOffset = 40;
static_assert(kReductionKernelAttrsBytes == 40 + 8);
struct ReductionKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    ReductionAlgorithm algorithm = {};
};

constexpr uint32_t kSoftmaxKernelAttrsBytes = 40;
constexpr uint32_t kSoftmaxKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kSoftmaxKernelAttrsSemanticAttrsOffset = 8;
constexpr uint32_t kSoftmaxKernelAttrsTileOffset = 16;
constexpr uint32_t kSoftmaxKernelAttrsCostOffset = 24;
constexpr uint32_t kSoftmaxKernelAttrsAlgorithmOffset = 32;
static_assert(kSoftmaxKernelAttrsBytes == 32 + 8);
struct SoftmaxKernelAttrs
{
    uint64_t presence_mask = 0;
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    SoftmaxAlgorithm algorithm = {};
};

constexpr uint32_t kStateTransitionBytes = 48;
constexpr uint32_t kStateTransitionPresenceMaskOffset = 0;
constexpr uint32_t kStateTransitionOldStateIdOffset = 8;
constexpr uint32_t kStateTransitionNewStateIdOffset = 16;
constexpr uint32_t kStateTransitionViewIdOffset = 24;
constexpr uint32_t kStateTransitionRegionOffset = 32;
constexpr uint32_t kStateTransitionModeOffset = 40;
static_assert(kStateTransitionBytes == 40 + 8);
struct StateTransition
{
    uint64_t presence_mask = 0;
    uint64_t old_state_id = 0;
    uint64_t new_state_id = 0;
    uint64_t view_id = 0;
    SemanticRef region = {};
    OperandAccessMode mode = {};
};

constexpr uint32_t kTensorShardBytes = 80;
constexpr uint32_t kTensorShardPresenceMaskOffset = 0;
constexpr uint32_t kTensorShardShardIdOffset = 8;
constexpr uint32_t kTensorShardTensorIdOffset = 16;
constexpr uint32_t kTensorShardPlacementIdOffset = 24;
constexpr uint32_t kTensorShardOwnerCoreOffset = 32;
constexpr uint32_t kTensorShardDistributionOffset = 40;
constexpr uint32_t kTensorShardGlobalOriginOffset = 48;
constexpr uint32_t kTensorShardPaddedLocalShapeOffset = 56;
constexpr uint32_t kTensorShardValidShapeOffset = 64;
constexpr uint32_t kTensorShardPartialSumIdOffset = 72;
static_assert(kTensorShardBytes == 72 + 8);
struct TensorShard
{
    uint64_t presence_mask = 0;
    uint64_t shard_id = 0;
    uint64_t tensor_id = 0;
    uint64_t placement_id = 0;
    uint64_t owner_core = 0;
    DistributionKind distribution = {};
    ListSpan global_origin = {};
    ListSpan padded_local_shape = {};
    ListSpan valid_shape = {};
    uint64_t partial_sum_id = 0;
};

constexpr uint32_t kTensorStateBytes = 48;
constexpr uint32_t kTensorStatePresenceMaskOffset = 0;
constexpr uint32_t kTensorStateStateIdOffset = 8;
constexpr uint32_t kTensorStateObjectIdOffset = 16;
constexpr uint32_t kTensorStateVersionOffset = 24;
constexpr uint32_t kTensorStateOriginOffset = 32;
constexpr uint32_t kTensorStatePartialSumIdOffset = 40;
static_assert(kTensorStateBytes == 40 + 8);
struct TensorState
{
    uint64_t presence_mask = 0;
    uint64_t state_id = 0;
    uint64_t object_id = 0;
    uint64_t version = 0;
    StateOrigin origin = {};
    uint64_t partial_sum_id = 0;
};

constexpr uint32_t kVectorKernelAttrsBytes = 48;
constexpr uint32_t kVectorKernelAttrsPresenceMaskOffset = 0;
constexpr uint32_t kVectorKernelAttrsGraphOpcodeOffset = 8;
constexpr uint32_t kVectorKernelAttrsSemanticAttrsOffset = 16;
constexpr uint32_t kVectorKernelAttrsTileOffset = 24;
constexpr uint32_t kVectorKernelAttrsCostOffset = 32;
constexpr uint32_t kVectorKernelAttrsAlgorithmOffset = 40;
static_assert(kVectorKernelAttrsBytes == 40 + 8);
struct VectorKernelAttrs
{
    uint64_t presence_mask = 0;
    OpCode graph_opcode = {};
    SemanticRef semantic_attrs = {};
    SemanticRef tile = {};
    SemanticRef cost = {};
    VectorAlgorithm algorithm = {};
};

constexpr uint32_t kViewDeclarationAttrsBytes = 16;
constexpr uint32_t kViewDeclarationAttrsPresenceMaskOffset = 0;
constexpr uint32_t kViewDeclarationAttrsViewIdOffset = 8;
static_assert(kViewDeclarationAttrsBytes == 8 + 8);
struct ViewDeclarationAttrs
{
    uint64_t presence_mask = 0;
    uint64_t view_id = 0;
};

constexpr uint32_t kAuthoredProgramOriginBytes = 32;
constexpr uint32_t kAuthoredProgramOriginPresenceMaskOffset = 0;
constexpr uint32_t kAuthoredProgramOriginNamespaceOffset = 8;
constexpr uint32_t kAuthoredProgramOriginNameOffset = 16;
constexpr uint32_t kAuthoredProgramOriginVersionOffset = 24;
static_assert(kAuthoredProgramOriginBytes == 24 + 8);
struct AuthoredProgramOrigin
{
    uint64_t presence_mask = 0;
    StringRef namespace_ = {};
    StringRef name = {};
    uint64_t version = 0;
};

constexpr uint32_t kAuthoredVariantLineageBytes = 16;
constexpr uint32_t kAuthoredVariantLineagePresenceMaskOffset = 0;
constexpr uint32_t kAuthoredVariantLineageAuthoringVariantIdOffset = 8;
static_assert(kAuthoredVariantLineageBytes == 8 + 8);
struct AuthoredVariantLineage
{
    uint64_t presence_mask = 0;
    StringRef authoring_variant_id = {};
};

constexpr uint32_t kAxiFenceAttrsBytes = 16;
constexpr uint32_t kAxiFenceAttrsPresenceMaskOffset = 0;
constexpr uint32_t kAxiFenceAttrsScopeOffset = 8;
static_assert(kAxiFenceAttrsBytes == 8 + 8);
struct AxiFenceAttrs
{
    uint64_t presence_mask = 0;
    FenceScope scope = {};
};

constexpr uint32_t kBarrierArrivalBytes = 40;
constexpr uint32_t kBarrierArrivalPresenceMaskOffset = 0;
constexpr uint32_t kBarrierArrivalParticipantCoreOffset = 8;
constexpr uint32_t kBarrierArrivalKernelOpIdOffset = 16;
constexpr uint32_t kBarrierArrivalCommandIdOffset = 24;
constexpr uint32_t kBarrierArrivalDoneTokenIdOffset = 32;
static_assert(kBarrierArrivalBytes == 32 + 8);
struct BarrierArrival
{
    uint64_t presence_mask = 0;
    uint64_t participant_core = 0;
    uint64_t kernel_op_id = 0;
    uint64_t command_id = 0;
    uint64_t done_token_id = 0;
};

constexpr uint32_t kBarrierExecutionBytes = 16;
constexpr uint32_t kBarrierExecutionPresenceMaskOffset = 0;
constexpr uint32_t kBarrierExecutionBarrierGroupIdOffset = 8;
static_assert(kBarrierExecutionBytes == 8 + 8);
struct BarrierExecution
{
    uint64_t presence_mask = 0;
    uint64_t barrier_group_id = 0;
};

constexpr uint32_t kBarrierGroupBytes = 40;
constexpr uint32_t kBarrierGroupPresenceMaskOffset = 0;
constexpr uint32_t kBarrierGroupBarrierGroupIdOffset = 8;
constexpr uint32_t kBarrierGroupParticipantsOffset = 16;
constexpr uint32_t kBarrierGroupArrivalsOffset = 24;
constexpr uint32_t kBarrierGroupCompletionEventIdOffset = 32;
static_assert(kBarrierGroupBytes == 32 + 8);
struct BarrierGroup
{
    uint64_t presence_mask = 0;
    uint64_t barrier_group_id = 0;
    ListSpan participants = {};
    ListSpan arrivals = {};
    uint64_t completion_event_id = 0;
};

constexpr uint32_t kCommandSemanticsBytes = 32;
constexpr uint32_t kCommandSemanticsPresenceMaskOffset = 0;
constexpr uint32_t kCommandSemanticsCommandIdOffset = 8;
constexpr uint32_t kCommandSemanticsSourceOffset = 16;
constexpr uint32_t kCommandSemanticsExecutionOffset = 24;
static_assert(kCommandSemanticsBytes == 24 + 8);
struct CommandSemantics
{
    uint64_t presence_mask = 0;
    uint64_t command_id = 0;
    SemanticRef source = {};
    SemanticRef execution = {};
};

constexpr uint32_t kCompiledProgramOriginBytes = 16;
constexpr uint32_t kCompiledProgramOriginPresenceMaskOffset = 0;
constexpr uint32_t kCompiledProgramOriginKernelBundleSemanticSha256Offset = 8;
static_assert(kCompiledProgramOriginBytes == 8 + 8);
struct CompiledProgramOrigin
{
    uint64_t presence_mask = 0;
    StringRef kernel_bundle_semantic_sha256 = {};
};

constexpr uint32_t kCompiledVariantLineageBytes = 24;
constexpr uint32_t kCompiledVariantLineagePresenceMaskOffset = 0;
constexpr uint32_t kCompiledVariantLineageKernelModuleOrdinalOffset = 8;
constexpr uint32_t kCompiledVariantLineageKernelModuleSemanticSha256Offset = 16;
static_assert(kCompiledVariantLineageBytes == 16 + 8);
struct CompiledVariantLineage
{
    uint64_t presence_mask = 0;
    uint64_t kernel_module_ordinal = 0;
    StringRef kernel_module_semantic_sha256 = {};
};

constexpr uint32_t kComputeExecutionBytes = 16;
constexpr uint32_t kComputeExecutionPresenceMaskOffset = 0;
constexpr uint32_t kComputeExecutionPhasesOffset = 8;
static_assert(kComputeExecutionBytes == 8 + 8);
struct ComputeExecution
{
    uint64_t presence_mask = 0;
    ListSpan phases = {};
};

constexpr uint32_t kControlCommandSourceBytes = 16;
constexpr uint32_t kControlCommandSourcePresenceMaskOffset = 0;
constexpr uint32_t kControlCommandSourceAttrsOffset = 8;
static_assert(kControlCommandSourceBytes == 8 + 8);
struct ControlCommandSource
{
    uint64_t presence_mask = 0;
    SemanticRef attrs = {};
};

constexpr uint32_t kControlExecutionBytes = 8;
constexpr uint32_t kControlExecutionPresenceMaskOffset = 0;
static_assert(kControlExecutionBytes == 8);
struct ControlExecution
{
    uint64_t presence_mask = 0;
};

constexpr uint32_t kDescriptorEndpointUseBytes = 40;
constexpr uint32_t kDescriptorEndpointUsePresenceMaskOffset = 0;
constexpr uint32_t kDescriptorEndpointUseRefIdOffset = 8;
constexpr uint32_t kDescriptorEndpointUseDescriptorIdOffset = 16;
constexpr uint32_t kDescriptorEndpointUseSideOffset = 24;
constexpr uint32_t kDescriptorEndpointUseUseOffset = 32;
static_assert(kDescriptorEndpointUseBytes == 32 + 8);
struct DescriptorEndpointUse
{
    uint64_t presence_mask = 0;
    uint64_t ref_id = 0;
    uint64_t descriptor_id = 0;
    EndpointSide side = {};
    SemanticRef use = {};
};

constexpr uint32_t kDescriptorGroupBytes = 48;
constexpr uint32_t kDescriptorGroupPresenceMaskOffset = 0;
constexpr uint32_t kDescriptorGroupGroupIdOffset = 8;
constexpr uint32_t kDescriptorGroupCommandIdOffset = 16;
constexpr uint32_t kDescriptorGroupKernelOpIdOffset = 24;
constexpr uint32_t kDescriptorGroupDescriptorIdsOffset = 32;
constexpr uint32_t kDescriptorGroupCompletionEventIdOffset = 40;
static_assert(kDescriptorGroupBytes == 40 + 8);
struct DescriptorGroup
{
    uint64_t presence_mask = 0;
    uint64_t group_id = 0;
    uint64_t command_id = 0;
    uint64_t kernel_op_id = 0;
    ListSpan descriptor_ids = {};
    uint64_t completion_event_id = 0;
};

constexpr uint32_t kDescriptorSourceBytes = 16;
constexpr uint32_t kDescriptorSourcePresenceMaskOffset = 0;
constexpr uint32_t kDescriptorSourceDescriptorIdOffset = 8;
static_assert(kDescriptorSourceBytes == 8 + 8);
struct DescriptorSource
{
    uint64_t presence_mask = 0;
    uint64_t descriptor_id = 0;
};

constexpr uint32_t kDmaExecutionBytes = 16;
constexpr uint32_t kDmaExecutionPresenceMaskOffset = 0;
constexpr uint32_t kDmaExecutionDescriptorGroupIdOffset = 8;
static_assert(kDmaExecutionBytes == 8 + 8);
struct DmaExecution
{
    uint64_t presence_mask = 0;
    uint64_t descriptor_group_id = 0;
};

constexpr uint32_t kEventSignalAttrsBytes = 16;
constexpr uint32_t kEventSignalAttrsPresenceMaskOffset = 0;
constexpr uint32_t kEventSignalAttrsEventIdOffset = 8;
static_assert(kEventSignalAttrsBytes == 8 + 8);
struct EventSignalAttrs
{
    uint64_t presence_mask = 0;
    uint64_t event_id = 0;
};

constexpr uint32_t kEventWaitAttrsBytes = 16;
constexpr uint32_t kEventWaitAttrsPresenceMaskOffset = 0;
constexpr uint32_t kEventWaitAttrsEventIdOffset = 8;
static_assert(kEventWaitAttrsBytes == 8 + 8);
struct EventWaitAttrs
{
    uint64_t presence_mask = 0;
    uint64_t event_id = 0;
};

constexpr uint32_t kExternalSlotBackingBytes = 16;
constexpr uint32_t kExternalSlotBackingPresenceMaskOffset = 0;
constexpr uint32_t kExternalSlotBackingSlotIdOffset = 8;
static_assert(kExternalSlotBackingBytes == 8 + 8);
struct ExternalSlotBacking
{
    uint64_t presence_mask = 0;
    uint64_t slot_id = 0;
};

constexpr uint32_t kHaltAttrsBytes = 8;
constexpr uint32_t kHaltAttrsPresenceMaskOffset = 0;
static_assert(kHaltAttrsBytes == 8);
struct HaltAttrs
{
    uint64_t presence_mask = 0;
};

constexpr uint32_t kIdSpanBytes = 24;
constexpr uint32_t kIdSpanPresenceMaskOffset = 0;
constexpr uint32_t kIdSpanFirstIdOffset = 8;
constexpr uint32_t kIdSpanCountOffset = 16;
static_assert(kIdSpanBytes == 16 + 8);
struct IdSpan
{
    uint64_t presence_mask = 0;
    uint64_t first_id = 0;
    uint64_t count = 0;
};

constexpr uint32_t kKernelCommandSourceBytes = 16;
constexpr uint32_t kKernelCommandSourcePresenceMaskOffset = 0;
constexpr uint32_t kKernelCommandSourceKernelOpIdOffset = 8;
static_assert(kKernelCommandSourceBytes == 8 + 8);
struct KernelCommandSource
{
    uint64_t presence_mask = 0;
    uint64_t kernel_op_id = 0;
};

constexpr uint32_t kKernelTokenSourceBytes = 16;
constexpr uint32_t kKernelTokenSourcePresenceMaskOffset = 0;
constexpr uint32_t kKernelTokenSourceTokenIdOffset = 8;
static_assert(kKernelTokenSourceBytes == 8 + 8);
struct KernelTokenSource
{
    uint64_t presence_mask = 0;
    uint64_t token_id = 0;
};

constexpr uint32_t kLifecycleSourceBytes = 16;
constexpr uint32_t kLifecycleSourcePresenceMaskOffset = 0;
constexpr uint32_t kLifecycleSourceVariantIdOffset = 8;
static_assert(kLifecycleSourceBytes == 8 + 8);
struct LifecycleSource
{
    uint64_t presence_mask = 0;
    uint64_t variant_id = 0;
};

constexpr uint32_t kLocalAllocationBackingBytes = 16;
constexpr uint32_t kLocalAllocationBackingPresenceMaskOffset = 0;
constexpr uint32_t kLocalAllocationBackingAllocationIdOffset = 8;
static_assert(kLocalAllocationBackingBytes == 8 + 8);
struct LocalAllocationBacking
{
    uint64_t presence_mask = 0;
    uint64_t allocation_id = 0;
};

constexpr uint32_t kObjectBackingBytes = 24;
constexpr uint32_t kObjectBackingPresenceMaskOffset = 0;
constexpr uint32_t kObjectBackingObjectIdOffset = 8;
constexpr uint32_t kObjectBackingBackingOffset = 16;
static_assert(kObjectBackingBytes == 16 + 8);
struct ObjectBacking
{
    uint64_t presence_mask = 0;
    uint64_t object_id = 0;
    SemanticRef backing = {};
};

constexpr uint32_t kObjectSourceBytes = 16;
constexpr uint32_t kObjectSourcePresenceMaskOffset = 0;
constexpr uint32_t kObjectSourceObjectIdOffset = 8;
static_assert(kObjectSourceBytes == 8 + 8);
struct ObjectSource
{
    uint64_t presence_mask = 0;
    uint64_t object_id = 0;
};

constexpr uint32_t kProgramSemanticsBytes = 200;
constexpr uint32_t kProgramSemanticsPresenceMaskOffset = 0;
constexpr uint32_t kProgramSemanticsOriginOffset = 8;
constexpr uint32_t kProgramSemanticsVariantsOffset = 16;
constexpr uint32_t kProgramSemanticsKernelTensorsOffset = 24;
constexpr uint32_t kProgramSemanticsComputationsOffset = 32;
constexpr uint32_t kProgramSemanticsPlacementsOffset = 40;
constexpr uint32_t kProgramSemanticsLogicalShardsOffset = 48;
constexpr uint32_t kProgramSemanticsPartialSumsOffset = 56;
constexpr uint32_t kProgramSemanticsObjectsOffset = 64;
constexpr uint32_t kProgramSemanticsViewsOffset = 72;
constexpr uint32_t kProgramSemanticsStatesOffset = 80;
constexpr uint32_t kProgramSemanticsTokensOffset = 88;
constexpr uint32_t kProgramSemanticsKernelOpsOffset = 96;
constexpr uint32_t kProgramSemanticsObjectBackingsOffset = 104;
constexpr uint32_t kProgramSemanticsResidentViewsOffset = 112;
constexpr uint32_t kProgramSemanticsCommandSemanticsOffset = 120;
constexpr uint32_t kProgramSemanticsBarrierGroupsOffset = 128;
constexpr uint32_t kProgramSemanticsDependenciesOffset = 136;
constexpr uint32_t kProgramSemanticsStreamsOffset = 144;
constexpr uint32_t kProgramSemanticsStreamCommandIdsOffset = 152;
constexpr uint32_t kProgramSemanticsDescriptorGroupsOffset = 160;
constexpr uint32_t kProgramSemanticsEndpointUsesOffset = 168;
constexpr uint32_t kProgramSemanticsBindingSlotsOffset = 176;
constexpr uint32_t kProgramSemanticsReferenceBindingIdentitySha256Offset = 184;
constexpr uint32_t kProgramSemanticsIntrinsicTrafficOffset = 192;
static_assert(kProgramSemanticsBytes == 192 + 8);
struct ProgramSemantics
{
    uint64_t presence_mask = 0;
    SemanticRef origin = {};
    ListSpan variants = {};
    ListSpan kernel_tensors = {};
    ListSpan computations = {};
    ListSpan placements = {};
    ListSpan logical_shards = {};
    ListSpan partial_sums = {};
    ListSpan objects = {};
    ListSpan views = {};
    ListSpan states = {};
    ListSpan tokens = {};
    ListSpan kernel_ops = {};
    ListSpan object_backings = {};
    ListSpan resident_views = {};
    ListSpan command_semantics = {};
    ListSpan barrier_groups = {};
    ListSpan dependencies = {};
    ListSpan streams = {};
    ListSpan stream_command_ids = {};
    ListSpan descriptor_groups = {};
    ListSpan endpoint_uses = {};
    ListSpan binding_slots = {};
    StringRef reference_binding_identity_sha256 = {};
    SemanticRef intrinsic_traffic = {};
};

constexpr uint32_t kProgramVariantBytes = 56;
constexpr uint32_t kProgramVariantPresenceMaskOffset = 0;
constexpr uint32_t kProgramVariantVariantIdOffset = 8;
constexpr uint32_t kProgramVariantEntrypointIdOffset = 16;
constexpr uint32_t kProgramVariantProfileIdOffset = 24;
constexpr uint32_t kProgramVariantLineageOffset = 32;
constexpr uint32_t kProgramVariantLifecycleStreamIdOffset = 40;
constexpr uint32_t kProgramVariantMembershipOffset = 48;
static_assert(kProgramVariantBytes == 48 + 8);
struct ProgramVariant
{
    uint64_t presence_mask = 0;
    uint64_t variant_id = 0;
    uint64_t entrypoint_id = 0;
    uint64_t profile_id = 0;
    SemanticRef lineage = {};
    uint64_t lifecycle_stream_id = 0;
    SemanticRef membership = {};
};

constexpr uint32_t kReadAccessUseBytes = 32;
constexpr uint32_t kReadAccessUsePresenceMaskOffset = 0;
constexpr uint32_t kReadAccessUseKernelOpIdOffset = 8;
constexpr uint32_t kReadAccessUseAccessIndexOffset = 16;
constexpr uint32_t kReadAccessUseRegionOffset = 24;
static_assert(kReadAccessUseBytes == 24 + 8);
struct ReadAccessUse
{
    uint64_t presence_mask = 0;
    uint64_t kernel_op_id = 0;
    uint64_t access_index = 0;
    SemanticRef region = {};
};

constexpr uint32_t kRecvWaitExecutionBytes = 16;
constexpr uint32_t kRecvWaitExecutionPresenceMaskOffset = 0;
constexpr uint32_t kRecvWaitExecutionTransferIdOffset = 8;
static_assert(kRecvWaitExecutionBytes == 8 + 8);
struct RecvWaitExecution
{
    uint64_t presence_mask = 0;
    uint64_t transfer_id = 0;
};

constexpr uint32_t kRepeatCommandAttrsBytes = 32;
constexpr uint32_t kRepeatCommandAttrsPresenceMaskOffset = 0;
constexpr uint32_t kRepeatCommandAttrsSubrangeBeginStreamOrdinalOffset = 8;
constexpr uint32_t kRepeatCommandAttrsSubrangeCommandCountOffset = 16;
constexpr uint32_t kRepeatCommandAttrsRepeatCountOffset = 24;
static_assert(kRepeatCommandAttrsBytes == 24 + 8);
struct RepeatCommandAttrs
{
    uint64_t presence_mask = 0;
    uint64_t subrange_begin_stream_ordinal = 0;
    uint64_t subrange_command_count = 0;
    uint64_t repeat_count = 0;
};

constexpr uint32_t kRequestBeginAttrsBytes = 8;
constexpr uint32_t kRequestBeginAttrsPresenceMaskOffset = 0;
static_assert(kRequestBeginAttrsBytes == 8);
struct RequestBeginAttrs
{
    uint64_t presence_mask = 0;
};

constexpr uint32_t kRequestEndAttrsBytes = 8;
constexpr uint32_t kRequestEndAttrsPresenceMaskOffset = 0;
static_assert(kRequestEndAttrsBytes == 8);
struct RequestEndAttrs
{
    uint64_t presence_mask = 0;
};

constexpr uint32_t kResidentViewBytes = 24;
constexpr uint32_t kResidentViewPresenceMaskOffset = 0;
constexpr uint32_t kResidentViewRuntimeShardIdOffset = 8;
constexpr uint32_t kResidentViewViewIdOffset = 16;
static_assert(kResidentViewBytes == 16 + 8);
struct ResidentView
{
    uint64_t presence_mask = 0;
    uint64_t runtime_shard_id = 0;
    uint64_t view_id = 0;
};

constexpr uint32_t kScheduledDependencyBytes = 48;
constexpr uint32_t kScheduledDependencyPresenceMaskOffset = 0;
constexpr uint32_t kScheduledDependencyDependencyIdOffset = 8;
constexpr uint32_t kScheduledDependencySourceCommandIdOffset = 16;
constexpr uint32_t kScheduledDependencyTargetCommandIdOffset = 24;
constexpr uint32_t kScheduledDependencyKindOffset = 32;
constexpr uint32_t kScheduledDependencySourceOffset = 40;
static_assert(kScheduledDependencyBytes == 40 + 8);
struct ScheduledDependency
{
    uint64_t presence_mask = 0;
    uint64_t dependency_id = 0;
    uint64_t source_command_id = 0;
    uint64_t target_command_id = 0;
    ScheduledDependencyKind kind = {};
    SemanticRef source = {};
};

constexpr uint32_t kScheduledStreamBytes = 56;
constexpr uint32_t kScheduledStreamPresenceMaskOffset = 0;
constexpr uint32_t kScheduledStreamStreamIdOffset = 8;
constexpr uint32_t kScheduledStreamCoreIdOffset = 16;
constexpr uint32_t kScheduledStreamPhysicalStreamIdOffset = 24;
constexpr uint32_t kScheduledStreamCommandBeginOffset = 32;
constexpr uint32_t kScheduledStreamCommandCountOffset = 40;
constexpr uint32_t kScheduledStreamFlagsOffset = 48;
static_assert(kScheduledStreamBytes == 48 + 8);
struct ScheduledStream
{
    uint64_t presence_mask = 0;
    uint64_t stream_id = 0;
    uint64_t core_id = 0;
    uint64_t physical_stream_id = 0;
    uint64_t command_begin = 0;
    uint64_t command_count = 0;
    uint64_t flags = 0;
};

constexpr uint32_t kStateSourceBytes = 16;
constexpr uint32_t kStateSourcePresenceMaskOffset = 0;
constexpr uint32_t kStateSourceStateIdOffset = 8;
static_assert(kStateSourceBytes == 8 + 8);
struct StateSource
{
    uint64_t presence_mask = 0;
    uint64_t state_id = 0;
};

constexpr uint32_t kStreamOrderSourceBytes = 16;
constexpr uint32_t kStreamOrderSourcePresenceMaskOffset = 0;
constexpr uint32_t kStreamOrderSourceStreamIdOffset = 8;
static_assert(kStreamOrderSourceBytes == 8 + 8);
struct StreamOrderSource
{
    uint64_t presence_mask = 0;
    uint64_t stream_id = 0;
};

constexpr uint32_t kVariantMembershipBytes = 200;
constexpr uint32_t kVariantMembershipPresenceMaskOffset = 0;
constexpr uint32_t kVariantMembershipAbiTensorsOffset = 8;
constexpr uint32_t kVariantMembershipRuntimeShardsOffset = 16;
constexpr uint32_t kVariantMembershipAllocationsOffset = 24;
constexpr uint32_t kVariantMembershipStreamsOffset = 32;
constexpr uint32_t kVariantMembershipCommandsOffset = 40;
constexpr uint32_t kVariantMembershipEventsOffset = 48;
constexpr uint32_t kVariantMembershipDescriptorsOffset = 56;
constexpr uint32_t kVariantMembershipRelocationsOffset = 64;
constexpr uint32_t kVariantMembershipKernelTensorsOffset = 72;
constexpr uint32_t kVariantMembershipComputationsOffset = 80;
constexpr uint32_t kVariantMembershipPlacementsOffset = 88;
constexpr uint32_t kVariantMembershipLogicalShardsOffset = 96;
constexpr uint32_t kVariantMembershipPartialSumsOffset = 104;
constexpr uint32_t kVariantMembershipObjectsOffset = 112;
constexpr uint32_t kVariantMembershipViewsOffset = 120;
constexpr uint32_t kVariantMembershipStatesOffset = 128;
constexpr uint32_t kVariantMembershipTokensOffset = 136;
constexpr uint32_t kVariantMembershipKernelOpsOffset = 144;
constexpr uint32_t kVariantMembershipObjectBackingsOffset = 152;
constexpr uint32_t kVariantMembershipCommandSemanticsOffset = 160;
constexpr uint32_t kVariantMembershipBarrierGroupsOffset = 168;
constexpr uint32_t kVariantMembershipDependenciesOffset = 176;
constexpr uint32_t kVariantMembershipEndpointUsesOffset = 184;
constexpr uint32_t kVariantMembershipBindingSlotsOffset = 192;
static_assert(kVariantMembershipBytes == 192 + 8);
struct VariantMembership
{
    uint64_t presence_mask = 0;
    SemanticRef abi_tensors = {};
    SemanticRef runtime_shards = {};
    SemanticRef allocations = {};
    SemanticRef streams = {};
    SemanticRef commands = {};
    SemanticRef events = {};
    SemanticRef descriptors = {};
    SemanticRef relocations = {};
    SemanticRef kernel_tensors = {};
    SemanticRef computations = {};
    SemanticRef placements = {};
    SemanticRef logical_shards = {};
    SemanticRef partial_sums = {};
    SemanticRef objects = {};
    SemanticRef views = {};
    SemanticRef states = {};
    SemanticRef tokens = {};
    SemanticRef kernel_ops = {};
    SemanticRef object_backings = {};
    SemanticRef command_semantics = {};
    SemanticRef barrier_groups = {};
    SemanticRef dependencies = {};
    SemanticRef endpoint_uses = {};
    SemanticRef binding_slots = {};
};

constexpr uint32_t kWriteAccessUseBytes = 32;
constexpr uint32_t kWriteAccessUsePresenceMaskOffset = 0;
constexpr uint32_t kWriteAccessUseKernelOpIdOffset = 8;
constexpr uint32_t kWriteAccessUseAccessIndexOffset = 16;
constexpr uint32_t kWriteAccessUseRegionOffset = 24;
static_assert(kWriteAccessUseBytes == 24 + 8);
struct WriteAccessUse
{
    uint64_t presence_mask = 0;
    uint64_t kernel_op_id = 0;
    uint64_t access_index = 0;
    SemanticRef region = {};
};

constexpr uint32_t kBindingBytes = 64;
constexpr uint32_t kBindingPresenceMaskOffset = 0;
constexpr uint32_t kBindingSlotIdOffset = 8;
constexpr uint32_t kBindingRegionIdOffset = 16;
constexpr uint32_t kBindingOwnerCoreOffset = 24;
constexpr uint32_t kBindingAllocationOffsetBytesOffset = 32;
constexpr uint32_t kBindingAllocationSizeBytesOffset = 40;
constexpr uint32_t kBindingAllocationAlignmentBytesOffset = 48;
constexpr uint32_t kBindingAccessOffset = 56;
static_assert(kBindingBytes == 56 + 8);
struct Binding
{
    uint64_t presence_mask = 0;
    uint64_t slot_id = 0;
    uint64_t region_id = 0;
    uint64_t owner_core = 0;
    uint64_t allocation_offset_bytes = 0;
    uint64_t allocation_size_bytes = 0;
    uint64_t allocation_alignment_bytes = 0;
    Access access = {};
};

constexpr uint32_t kBindingSlotBytes = 80;
constexpr uint32_t kBindingSlotPresenceMaskOffset = 0;
constexpr uint32_t kBindingSlotSlotIdOffset = 8;
constexpr uint32_t kBindingSlotSymbolOffset = 16;
constexpr uint32_t kBindingSlotMemorySpaceOffset = 24;
constexpr uint32_t kBindingSlotRegionIdOffset = 32;
constexpr uint32_t kBindingSlotOwnerCoreOffset = 40;
constexpr uint32_t kBindingSlotRequiredAllocationBytesOffset = 48;
constexpr uint32_t kBindingSlotRequiredAllocationAlignmentBytesOffset = 56;
constexpr uint32_t kBindingSlotAccessOffset = 64;
constexpr uint32_t kBindingSlotReferenceBindingOffset = 72;
static_assert(kBindingSlotBytes == 72 + 8);
struct BindingSlot
{
    uint64_t presence_mask = 0;
    uint64_t slot_id = 0;
    StringRef symbol = {};
    MemorySpace memory_space = {};
    uint64_t region_id = 0;
    uint64_t owner_core = 0;
    uint64_t required_allocation_bytes = 0;
    uint64_t required_allocation_alignment_bytes = 0;
    Access access = {};
    SemanticRef reference_binding = {};
};

constexpr uint32_t kChannelTrafficBytes = 120;
constexpr uint32_t kChannelTrafficPresenceMaskOffset = 0;
constexpr uint32_t kChannelTrafficChannelOffset = 8;
constexpr uint32_t kChannelTrafficVnetOffset = 16;
constexpr uint32_t kChannelTrafficSourceEndpointOffset = 24;
constexpr uint32_t kChannelTrafficDestinationEndpointOffset = 32;
constexpr uint32_t kChannelTrafficSourceNodeOffset = 40;
constexpr uint32_t kChannelTrafficSourcePortOffset = 48;
constexpr uint32_t kChannelTrafficDestinationNodeOffset = 56;
constexpr uint32_t kChannelTrafficDestinationPortOffset = 64;
constexpr uint32_t kChannelTrafficSourceRouterOffset = 72;
constexpr uint32_t kChannelTrafficDestinationRouterOffset = 80;
constexpr uint32_t kChannelTrafficMessagesOffset = 88;
constexpr uint32_t kChannelTrafficPacketsOffset = 96;
constexpr uint32_t kChannelTrafficFlitsOffset = 104;
constexpr uint32_t kChannelTrafficWireBytesOffset = 112;
static_assert(kChannelTrafficBytes == 112 + 8);
struct ChannelTraffic
{
    uint64_t presence_mask = 0;
    AxiChannel channel = {};
    uint64_t vnet = 0;
    StringRef source_endpoint = {};
    StringRef destination_endpoint = {};
    uint64_t source_node = 0;
    uint64_t source_port = 0;
    uint64_t destination_node = 0;
    uint64_t destination_port = 0;
    uint64_t source_router = 0;
    uint64_t destination_router = 0;
    uint64_t messages = 0;
    uint64_t packets = 0;
    uint64_t flits = 0;
    uint64_t wire_bytes = 0;
};

constexpr uint32_t kDescriptorIdentityBytes = 80;
constexpr uint32_t kDescriptorIdentityPresenceMaskOffset = 0;
constexpr uint32_t kDescriptorIdentityEntrypointIdOffset = 8;
constexpr uint32_t kDescriptorIdentityProfileIdOffset = 16;
constexpr uint32_t kDescriptorIdentityCommandIdOffset = 24;
constexpr uint32_t kDescriptorIdentityDescriptorIdOffset = 32;
constexpr uint32_t kDescriptorIdentityIssuingCoreOffset = 40;
constexpr uint32_t kDescriptorIdentityPeerCoreOffset = 48;
constexpr uint32_t kDescriptorIdentityTensorIdOffset = 56;
constexpr uint32_t kDescriptorIdentityTensorRoleOffset = 64;
constexpr uint32_t kDescriptorIdentityDirectionOffset = 72;
static_assert(kDescriptorIdentityBytes == 72 + 8);
struct DescriptorIdentity
{
    uint64_t presence_mask = 0;
    uint64_t entrypoint_id = 0;
    uint64_t profile_id = 0;
    uint64_t command_id = 0;
    uint64_t descriptor_id = 0;
    uint64_t issuing_core = 0;
    uint64_t peer_core = 0;
    uint64_t tensor_id = 0;
    TensorRole tensor_role = {};
    TrafficDirection direction = {};
};

constexpr uint32_t kDescriptorTrafficBytes = 208;
constexpr uint32_t kDescriptorTrafficPresenceMaskOffset = 0;
constexpr uint32_t kDescriptorTrafficIdentityOffset = 8;
constexpr uint32_t kDescriptorTrafficExecutionCountOffset = 16;
constexpr uint32_t kDescriptorTrafficSrcMemorySpaceOffset = 24;
constexpr uint32_t kDescriptorTrafficDstMemorySpaceOffset = 32;
constexpr uint32_t kDescriptorTrafficSrcEndpointOffset = 40;
constexpr uint32_t kDescriptorTrafficDstEndpointOffset = 48;
constexpr uint32_t kDescriptorTrafficSrcRouterOffset = 56;
constexpr uint32_t kDescriptorTrafficDstRouterOffset = 64;
constexpr uint32_t kDescriptorTrafficSrcAddressOffset = 72;
constexpr uint32_t kDescriptorTrafficDstAddressOffset = 80;
constexpr uint32_t kDescriptorTrafficRemoteAddressOffset = 88;
constexpr uint32_t kDescriptorTrafficRowsOffset = 96;
constexpr uint32_t kDescriptorTrafficRowBytesOffset = 104;
constexpr uint32_t kDescriptorTrafficRemoteStrideBytesOffset = 112;
constexpr uint32_t kDescriptorTrafficMaxBurstBeatsOffset = 120;
constexpr uint32_t kDescriptorTrafficHopsOffset = 128;
constexpr uint32_t kDescriptorTrafficUsefulBytesOffset = 136;
constexpr uint32_t kDescriptorTrafficPhysicalBeatBytesOffset = 144;
constexpr uint32_t kDescriptorTrafficSegmentsOffset = 152;
constexpr uint32_t kDescriptorTrafficBurstsOffset = 160;
constexpr uint32_t kDescriptorTrafficChannelsOffset = 168;
constexpr uint32_t kDescriptorTrafficPacketsOffset = 176;
constexpr uint32_t kDescriptorTrafficFlitsOffset = 184;
constexpr uint32_t kDescriptorTrafficWireBytesOffset = 192;
constexpr uint32_t kDescriptorTrafficHopWireBytesOffset = 200;
static_assert(kDescriptorTrafficBytes == 200 + 8);
struct DescriptorTraffic
{
    uint64_t presence_mask = 0;
    SemanticRef identity = {};
    uint64_t execution_count = 0;
    MemorySpace src_memory_space = {};
    MemorySpace dst_memory_space = {};
    StringRef src_endpoint = {};
    StringRef dst_endpoint = {};
    uint64_t src_router = 0;
    uint64_t dst_router = 0;
    uint64_t src_address = 0;
    uint64_t dst_address = 0;
    uint64_t remote_address = 0;
    uint64_t rows = 0;
    uint64_t row_bytes = 0;
    uint64_t remote_stride_bytes = 0;
    uint64_t max_burst_beats = 0;
    uint64_t hops = 0;
    uint64_t useful_bytes = 0;
    uint64_t physical_beat_bytes = 0;
    uint64_t segments = 0;
    uint64_t bursts = 0;
    ListSpan channels = {};
    uint64_t packets = 0;
    uint64_t flits = 0;
    uint64_t wire_bytes = 0;
    uint64_t hop_wire_bytes = 0;
};

constexpr uint32_t kTrafficAggregateBytes = 104;
constexpr uint32_t kTrafficAggregatePresenceMaskOffset = 0;
constexpr uint32_t kTrafficAggregateKeyOffset = 8;
constexpr uint32_t kTrafficAggregateUsefulBytesOffset = 16;
constexpr uint32_t kTrafficAggregatePhysicalBeatBytesOffset = 24;
constexpr uint32_t kTrafficAggregateStaticDescriptorsOffset = 32;
constexpr uint32_t kTrafficAggregateDescriptorExecutionsOffset = 40;
constexpr uint32_t kTrafficAggregateSegmentsOffset = 48;
constexpr uint32_t kTrafficAggregateBurstsOffset = 56;
constexpr uint32_t kTrafficAggregateChannelsOffset = 64;
constexpr uint32_t kTrafficAggregatePacketsOffset = 72;
constexpr uint32_t kTrafficAggregateFlitsOffset = 80;
constexpr uint32_t kTrafficAggregateWireBytesOffset = 88;
constexpr uint32_t kTrafficAggregateHopWireBytesOffset = 96;
static_assert(kTrafficAggregateBytes == 96 + 8);
struct TrafficAggregate
{
    uint64_t presence_mask = 0;
    SemanticRef key = {};
    uint64_t useful_bytes = 0;
    uint64_t physical_beat_bytes = 0;
    uint64_t static_descriptors = 0;
    uint64_t descriptor_executions = 0;
    uint64_t segments = 0;
    uint64_t bursts = 0;
    ListSpan channels = {};
    uint64_t packets = 0;
    uint64_t flits = 0;
    uint64_t wire_bytes = 0;
    uint64_t hop_wire_bytes = 0;
};

constexpr uint32_t kTrafficAggregateKeyBytes = 72;
constexpr uint32_t kTrafficAggregateKeyPresenceMaskOffset = 0;
constexpr uint32_t kTrafficAggregateKeyLevelOffset = 8;
constexpr uint32_t kTrafficAggregateKeyEntrypointIdOffset = 16;
constexpr uint32_t kTrafficAggregateKeyProfileIdOffset = 24;
constexpr uint32_t kTrafficAggregateKeyCoreIdOffset = 32;
constexpr uint32_t kTrafficAggregateKeyPeerCoreOffset = 40;
constexpr uint32_t kTrafficAggregateKeyMemorySpaceOffset = 48;
constexpr uint32_t kTrafficAggregateKeyTensorRoleOffset = 56;
constexpr uint32_t kTrafficAggregateKeyDirectionOffset = 64;
static_assert(kTrafficAggregateKeyBytes == 64 + 8);
struct TrafficAggregateKey
{
    uint64_t presence_mask = 0;
    TrafficAggregateLevel level = {};
    uint64_t entrypoint_id = 0;
    uint64_t profile_id = 0;
    uint64_t core_id = 0;
    uint64_t peer_core = 0;
    MemorySpace memory_space = {};
    TensorRole tensor_role = {};
    TrafficDirection direction = {};
};

constexpr uint32_t kTrafficChannelTotalBytes = 56;
constexpr uint32_t kTrafficChannelTotalPresenceMaskOffset = 0;
constexpr uint32_t kTrafficChannelTotalChannelOffset = 8;
constexpr uint32_t kTrafficChannelTotalVnetOffset = 16;
constexpr uint32_t kTrafficChannelTotalMessagesOffset = 24;
constexpr uint32_t kTrafficChannelTotalPacketsOffset = 32;
constexpr uint32_t kTrafficChannelTotalFlitsOffset = 40;
constexpr uint32_t kTrafficChannelTotalWireBytesOffset = 48;
static_assert(kTrafficChannelTotalBytes == 48 + 8);
struct TrafficChannelTotal
{
    uint64_t presence_mask = 0;
    AxiChannel channel = {};
    uint64_t vnet = 0;
    uint64_t messages = 0;
    uint64_t packets = 0;
    uint64_t flits = 0;
    uint64_t wire_bytes = 0;
};

constexpr uint32_t kTrafficReportBytes = 40;
constexpr uint32_t kTrafficReportPresenceMaskOffset = 0;
constexpr uint32_t kTrafficReportBindingIdentitySha256Offset = 8;
constexpr uint32_t kTrafficReportDescriptorsOffset = 16;
constexpr uint32_t kTrafficReportAggregatesOffset = 24;
constexpr uint32_t kTrafficReportSemanticSha256Offset = 32;
static_assert(kTrafficReportBytes == 32 + 8);
struct TrafficReport
{
    uint64_t presence_mask = 0;
    StringRef binding_identity_sha256 = {};
    ListSpan descriptors = {};
    ListSpan aggregates = {};
    StringRef semantic_sha256 = {};
};

#endif

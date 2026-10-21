#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_PROJECTION_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_PROJECTION_HH

enum class CanonicalProjection : uint8_t { Full, Semantic };
enum class ProgramFieldKind : uint8_t { Abi, Bytes, TransportSections, SemanticRef, Digest };
enum class ProgramAbiFieldKind : uint8_t { U16, U64 };
enum class TransportProjectionKind : uint8_t { Strings, Records, AttrPayloads };
enum class TransportFieldKind : uint8_t { U8, U16, U32, U64, U64x8, Bytes, Record };
enum class TransportStringPool : uint8_t { None, Transport, Semantic };
struct ProgramFieldDescriptor { std::string_view name; ProgramFieldKind kind; bool semantic; };
struct ProgramAbiFieldDescriptor { std::string_view name; ProgramAbiFieldKind kind; };
struct TransportSectionDescriptor { std::string_view name; uint16_t section_type; std::string_view program_field; TransportProjectionKind projection; bool optional; bool semantic; };
struct TransportFieldDescriptor { std::string_view name; TransportFieldKind kind; bool semantic; TransportStringPool string_pool; };

inline constexpr std::array<ProgramFieldDescriptor, 5> kProgramCanonicalFields = {{
    {"abi", ProgramFieldKind::Abi, true},
    {"arch_digest", ProgramFieldKind::Bytes, true},
    {"sections", ProgramFieldKind::TransportSections, true},
    {"semantic_sha256", ProgramFieldKind::Digest, false},
    {"semantics", ProgramFieldKind::SemanticRef, true},
}};

template <typename Visitor> bool visitProgramAbiFieldsCanonical(uint16_t abi_major, uint16_t abi_minor, uint16_t min_reader_minor, uint64_t required_features, Visitor &&visitor)
{
    if (!visitor(ProgramAbiFieldDescriptor{"major", ProgramAbiFieldKind::U16}, abi_major)) return false;
    if (!visitor(ProgramAbiFieldDescriptor{"min_reader_minor", ProgramAbiFieldKind::U16}, min_reader_minor)) return false;
    if (!visitor(ProgramAbiFieldDescriptor{"minor", ProgramAbiFieldKind::U16}, abi_minor)) return false;
    if (!visitor(ProgramAbiFieldDescriptor{"required_features", ProgramAbiFieldKind::U64}, required_features)) return false;
    return true;
}

inline constexpr TransportSectionDescriptor kAgentInstanceMemberBindingTransportSection = {"AGENT_INSTANCE_MEMBER_BINDINGS", 16391, "agent_instance_member_bindings", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentInstanceProfileTransportSection = {"AGENT_INSTANCE_PROFILES", 16389, "agent_instance_profiles", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentInstanceTensorBindingTransportSection = {"AGENT_INSTANCE_TENSOR_BINDINGS", 16395, "agent_instance_tensor_bindings", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentPublishSurrogateBindingTransportSection = {"AGENT_PUBLISH_SURROGATE_BINDINGS", 16393, "agent_publish_surrogate_bindings", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentRequestBindingRequirementTransportSection = {"AGENT_REQUEST_BINDING_REQUIREMENTS", 16392, "agent_request_binding_requirements", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentRequestProfileTransportSection = {"AGENT_REQUEST_PROFILES", 16388, "agent_request_profiles", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAgentSourceCoreMapTransportSection = {"AGENT_SOURCE_CORE_MAP", 16390, "agent_source_core_map", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kAllocationTransportSection = {"ALLOCATIONS", 6, "allocations", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandTransportSection = {"COMMANDS", 8, "commands", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandOperandTransportSection = {"COMMAND_OPERANDS", 10, "command_operands", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandWaitTransportSection = {"COMMAND_WAITS", 9, "command_waits", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kContentDigestTransportSection = {"CONTENT_DIGESTS", 103, "content_digests", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kDmaDescriptorTransportSection = {"DMA_DESCRIPTORS", 12, "dma_descriptors", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kEntrypointTransportSection = {"ENTRYPOINTS", 2, "entrypoints", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kEventTransportSection = {"EVENTS", 11, "events", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kExpectedTrafficTransportSection = {"EXPECTED_TRAFFIC", 15, "expected_traffic", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kMoeDynamicRegionTransportSection = {"MOE_DYNAMIC_REGIONS", 16386, "moe_dynamic_regions", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kMoeExpertSpecTransportSection = {"MOE_EXPERT_SPECS", 16385, "moe_expert_specs", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kMoeKernelSpecTransportSection = {"MOE_KERNEL_SPECS", 16387, "moe_kernel_specs", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kMoeLayerSpecTransportSection = {"MOE_LAYER_SPECS", 16384, "moe_layer_specs", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kOpAttrTransportSection = {"OP_ATTRS", 13, "op_attrs", TransportProjectionKind::AttrPayloads, false, true};
inline constexpr TransportSectionDescriptor kProfileTransportSection = {"PROFILES", 3, "profiles", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kProfileHintTransportSection = {"PROFILE_HINTS", 102, "profile_hints", TransportProjectionKind::Records, true, false};
inline constexpr TransportSectionDescriptor kProfileStreamRangeTransportSection = {"PROFILE_STREAM_RANGES", 16394, "profile_stream_ranges", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kRelocationTransportSection = {"RELOCATIONS", 14, "relocations", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kShardTransportSection = {"SHARDS", 5, "shards", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kSourceMapTransportSection = {"SOURCE_MAP", 101, "source_map", TransportProjectionKind::Records, true, false};
inline constexpr TransportSectionDescriptor kStreamTransportSection = {"STREAMS", 7, "streams", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kStringTransportSection = {"STRINGS", 1, "strings", TransportProjectionKind::Strings, false, true};
inline constexpr TransportSectionDescriptor kTensorTransportSection = {"TENSORS", 4, "tensors", TransportProjectionKind::Records, false, true};

inline constexpr std::array<TransportSectionDescriptor, 30> kTransportCanonicalSections = {{
    kAgentInstanceMemberBindingTransportSection,
    kAgentInstanceProfileTransportSection,
    kAgentInstanceTensorBindingTransportSection,
    kAgentPublishSurrogateBindingTransportSection,
    kAgentRequestBindingRequirementTransportSection,
    kAgentRequestProfileTransportSection,
    kAgentSourceCoreMapTransportSection,
    kAllocationTransportSection,
    kCommandTransportSection,
    kCommandOperandTransportSection,
    kCommandWaitTransportSection,
    kContentDigestTransportSection,
    kDmaDescriptorTransportSection,
    kEntrypointTransportSection,
    kEventTransportSection,
    kExpectedTrafficTransportSection,
    kMoeDynamicRegionTransportSection,
    kMoeExpertSpecTransportSection,
    kMoeKernelSpecTransportSection,
    kMoeLayerSpecTransportSection,
    kOpAttrTransportSection,
    kProfileTransportSection,
    kProfileHintTransportSection,
    kProfileStreamRangeTransportSection,
    kRelocationTransportSection,
    kShardTransportSection,
    kSourceMapTransportSection,
    kStreamTransportSection,
    kStringTransportSection,
    kTensorTransportSection,
}};

inline constexpr TransportFieldDescriptor kEntrypointEntrypointIdCanonicalField = {"entrypoint_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointNameSidCanonicalField = {"name_sid", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointProfileBeginCanonicalField = {"profile_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointProfileCountCanonicalField = {"profile_count", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointLifecycleCoreIdCanonicalField = {"lifecycle_core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointLifecycleStreamIdCanonicalField = {"lifecycle_stream_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointFlagsCanonicalField = {"flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEntrypointReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Entrypoint &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kEntrypointEntrypointIdCanonicalField, value.entrypoint_id)) return false;
    if (!visitor(kEntrypointFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kEntrypointLifecycleCoreIdCanonicalField, value.lifecycle_core_id)) return false;
    if (!visitor(kEntrypointLifecycleStreamIdCanonicalField, value.lifecycle_stream_id)) return false;
    if (!visitor(kEntrypointNameSidCanonicalField, value.name_sid)) return false;
    if (!visitor(kEntrypointProfileBeginCanonicalField, value.profile_begin)) return false;
    if (!visitor(kEntrypointProfileCountCanonicalField, value.profile_count)) return false;
    if (!visitor(kEntrypointReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kProfileProfileIdCanonicalField = {"profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileEntrypointIdCanonicalField = {"entrypoint_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileNameSidCanonicalField = {"name_sid", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileRankCanonicalField = {"rank", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileDimsCanonicalField = {"dims", TransportFieldKind::U64x8, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Profile &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kProfileDimsCanonicalField, value.dims)) return false;
    if (!visitor(kProfileEntrypointIdCanonicalField, value.entrypoint_id)) return false;
    if (!visitor(kProfileNameSidCanonicalField, value.name_sid)) return false;
    if (!visitor(kProfileProfileIdCanonicalField, value.profile_id)) return false;
    if (!visitor(kProfileRankCanonicalField, value.rank)) return false;
    if (!visitor(kProfileReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kTensorTensorIdCanonicalField = {"tensor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorNameSidCanonicalField = {"name_sid", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorRoleCanonicalField = {"role", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorDtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorStorageClassCanonicalField = {"storage_class", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorAccessCanonicalField = {"access", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorRankCanonicalField = {"rank", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorLayoutCanonicalField = {"layout", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorLayoutAttrCanonicalField = {"layout_attr", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorPlacementIdCanonicalField = {"placement_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorShardingIdCanonicalField = {"sharding_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorDimsCanonicalField = {"dims", TransportFieldKind::U64x8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kTensorContentSha256CanonicalField = {"content_sha256", TransportFieldKind::Bytes, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Tensor &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kTensorAccessCanonicalField, value.access)) return false;
    if (!visitor(kTensorContentSha256CanonicalField, value.content_sha256)) return false;
    if (!visitor(kTensorDimsCanonicalField, value.dims)) return false;
    if (!visitor(kTensorDtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kTensorFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kTensorLayoutCanonicalField, value.layout)) return false;
    if (!visitor(kTensorLayoutAttrCanonicalField, value.layout_attr)) return false;
    if (!visitor(kTensorNameSidCanonicalField, value.name_sid)) return false;
    if (!visitor(kTensorPlacementIdCanonicalField, value.placement_id)) return false;
    if (!visitor(kTensorRankCanonicalField, value.rank)) return false;
    if (!visitor(kTensorReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kTensorRoleCanonicalField, value.role)) return false;
    if (!visitor(kTensorShardingIdCanonicalField, value.sharding_id)) return false;
    if (!visitor(kTensorStorageClassCanonicalField, value.storage_class)) return false;
    if (!visitor(kTensorTensorIdCanonicalField, value.tensor_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kShardShardIdCanonicalField = {"shard_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardTensorIdCanonicalField = {"tensor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardShardingIdCanonicalField = {"sharding_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardOwnerCoreCanonicalField = {"owner_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardRankCanonicalField = {"rank", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardFlagsCanonicalField = {"flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardReserved0CanonicalField = {"reserved0", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardGlobalOriginCanonicalField = {"global_origin", TransportFieldKind::U64x8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardLocalShapeCanonicalField = {"local_shape", TransportFieldKind::U64x8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardValidShapeCanonicalField = {"valid_shape", TransportFieldKind::U64x8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardAllocationIdCanonicalField = {"allocation_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardReserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardAllocationOffsetCanonicalField = {"allocation_offset", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kShardSpanBytesCanonicalField = {"span_bytes", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Shard &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kShardAllocationIdCanonicalField, value.allocation_id)) return false;
    if (!visitor(kShardAllocationOffsetCanonicalField, value.allocation_offset)) return false;
    if (!visitor(kShardFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kShardGlobalOriginCanonicalField, value.global_origin)) return false;
    if (!visitor(kShardLocalShapeCanonicalField, value.local_shape)) return false;
    if (!visitor(kShardOwnerCoreCanonicalField, value.owner_core)) return false;
    if (!visitor(kShardRankCanonicalField, value.rank)) return false;
    if (!visitor(kShardReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kShardReserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kShardReserved2CanonicalField, value.reserved2)) return false;
    if (!visitor(kShardShardIdCanonicalField, value.shard_id)) return false;
    if (!visitor(kShardShardingIdCanonicalField, value.sharding_id)) return false;
    if (!visitor(kShardSpanBytesCanonicalField, value.span_bytes)) return false;
    if (!visitor(kShardTensorIdCanonicalField, value.tensor_id)) return false;
    if (!visitor(kShardValidShapeCanonicalField, value.valid_shape)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAllocationAllocationIdCanonicalField = {"allocation_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationOwnerCoreCanonicalField = {"owner_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationMemorySpaceCanonicalField = {"memory_space", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationOffsetBytesCanonicalField = {"offset_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationSizeBytesCanonicalField = {"size_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationAlignmentBytesCanonicalField = {"alignment_bytes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAllocationFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Allocation &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAllocationAlignmentBytesCanonicalField, value.alignment_bytes)) return false;
    if (!visitor(kAllocationAllocationIdCanonicalField, value.allocation_id)) return false;
    if (!visitor(kAllocationFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kAllocationMemorySpaceCanonicalField, value.memory_space)) return false;
    if (!visitor(kAllocationOffsetBytesCanonicalField, value.offset_bytes)) return false;
    if (!visitor(kAllocationOwnerCoreCanonicalField, value.owner_core)) return false;
    if (!visitor(kAllocationSizeBytesCanonicalField, value.size_bytes)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kStreamCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kStreamStreamIdCanonicalField = {"stream_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kStreamCommandBeginCanonicalField = {"command_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kStreamCommandCountCanonicalField = {"command_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kStreamFlagsCanonicalField = {"flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kStreamReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Stream &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kStreamCommandBeginCanonicalField, value.command_begin)) return false;
    if (!visitor(kStreamCommandCountCanonicalField, value.command_count)) return false;
    if (!visitor(kStreamCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kStreamFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kStreamReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kStreamStreamIdCanonicalField, value.stream_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kCommandCommandIdCanonicalField = {"command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandSourceOpIdCanonicalField = {"source_op_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandStreamIdCanonicalField = {"stream_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandEngineCanonicalField = {"engine", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOpcodeCanonicalField = {"opcode", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandWaitBeginCanonicalField = {"wait_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandWaitCountCanonicalField = {"wait_count", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandCountCanonicalField = {"operand_count", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandBeginCanonicalField = {"operand_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandSignalEventCanonicalField = {"signal_event", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandAttrIndexCanonicalField = {"attr_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandDebugLocIdCanonicalField = {"debug_loc_id", TransportFieldKind::U32, false, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Command &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kCommandAttrIndexCanonicalField, value.attr_index)) return false;
    if (!visitor(kCommandCommandIdCanonicalField, value.command_id)) return false;
    if (!visitor(kCommandCoreIdCanonicalField, value.core_id)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kCommandDebugLocIdCanonicalField, value.debug_loc_id)) return false;
    if (!visitor(kCommandEngineCanonicalField, value.engine)) return false;
    if (!visitor(kCommandOpcodeCanonicalField, value.opcode)) return false;
    if (!visitor(kCommandOperandBeginCanonicalField, value.operand_begin)) return false;
    if (!visitor(kCommandOperandCountCanonicalField, value.operand_count)) return false;
    if (!visitor(kCommandSignalEventCanonicalField, value.signal_event)) return false;
    if (!visitor(kCommandSourceOpIdCanonicalField, value.source_op_id)) return false;
    if (!visitor(kCommandStreamIdCanonicalField, value.stream_id)) return false;
    if (!visitor(kCommandWaitBeginCanonicalField, value.wait_begin)) return false;
    if (!visitor(kCommandWaitCountCanonicalField, value.wait_count)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kCommandWaitEventIdCanonicalField = {"event_id", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const CommandWait &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kCommandWaitEventIdCanonicalField, value.event_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kCommandOperandTensorIdCanonicalField = {"tensor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandShardIdCanonicalField = {"shard_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandAllocationIdCanonicalField = {"allocation_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandAccessCanonicalField = {"access", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCommandOperandReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const CommandOperand &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kCommandOperandAccessCanonicalField, value.access)) return false;
    if (!visitor(kCommandOperandAllocationIdCanonicalField, value.allocation_id)) return false;
    if (!visitor(kCommandOperandReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kCommandOperandShardIdCanonicalField, value.shard_id)) return false;
    if (!visitor(kCommandOperandTensorIdCanonicalField, value.tensor_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kEventEventIdCanonicalField = {"event_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEventKindCanonicalField = {"kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEventReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEventProducerCommandIdCanonicalField = {"producer_command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEventExpectedArrivalsCanonicalField = {"expected_arrivals", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kEventReserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Event &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kEventEventIdCanonicalField, value.event_id)) return false;
    if (!visitor(kEventExpectedArrivalsCanonicalField, value.expected_arrivals)) return false;
    if (!visitor(kEventKindCanonicalField, value.kind)) return false;
    if (!visitor(kEventProducerCommandIdCanonicalField, value.producer_command_id)) return false;
    if (!visitor(kEventReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kEventReserved2CanonicalField, value.reserved2)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kDmaEndpointMemorySpaceCanonicalField = {"memory_space", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointRegionIdCanonicalField = {"region_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointOwnerCoreCanonicalField = {"owner_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointTensorIdCanonicalField = {"tensor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointShardIdCanonicalField = {"shard_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaEndpointOffsetBytesCanonicalField = {"offset_bytes", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const DmaEndpoint &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kDmaEndpointMemorySpaceCanonicalField, value.memory_space)) return false;
    if (!visitor(kDmaEndpointOffsetBytesCanonicalField, value.offset_bytes)) return false;
    if (!visitor(kDmaEndpointOwnerCoreCanonicalField, value.owner_core)) return false;
    if (!visitor(kDmaEndpointRegionIdCanonicalField, value.region_id)) return false;
    if (!visitor(kDmaEndpointReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kDmaEndpointShardIdCanonicalField, value.shard_id)) return false;
    if (!visitor(kDmaEndpointTensorIdCanonicalField, value.tensor_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kDmaDescriptorDescriptorIdCanonicalField = {"descriptor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorCommandIdCanonicalField = {"command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorTransferIdCanonicalField = {"transfer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorOwnerCoreCanonicalField = {"owner_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorKindCanonicalField = {"kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorSrcCanonicalField = {"src", TransportFieldKind::Record, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorDstCanonicalField = {"dst", TransportFieldKind::Record, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorRowsCanonicalField = {"rows", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorRowBytesCanonicalField = {"row_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorSrcStrideBytesCanonicalField = {"src_stride_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorDstStrideBytesCanonicalField = {"dst_stride_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorUsefulBytesCanonicalField = {"useful_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorPhysicalStorageBytesCanonicalField = {"physical_storage_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorAxiIdCanonicalField = {"axi_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorQosCanonicalField = {"qos", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorReservedCanonicalField = {"reserved", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorMaxBurstBeatsCanonicalField = {"max_burst_beats", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorReserved2CanonicalField = {"reserved2", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kDmaDescriptorCompletionEventCanonicalField = {"completion_event", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const DmaDescriptor &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kDmaDescriptorAxiIdCanonicalField, value.axi_id)) return false;
    if (!visitor(kDmaDescriptorCommandIdCanonicalField, value.command_id)) return false;
    if (!visitor(kDmaDescriptorCompletionEventCanonicalField, value.completion_event)) return false;
    if (!visitor(kDmaDescriptorDescriptorIdCanonicalField, value.descriptor_id)) return false;
    if (!visitor(kDmaDescriptorDstCanonicalField, value.dst)) return false;
    if (!visitor(kDmaDescriptorDstStrideBytesCanonicalField, value.dst_stride_bytes)) return false;
    if (!visitor(kDmaDescriptorKindCanonicalField, value.kind)) return false;
    if (!visitor(kDmaDescriptorMaxBurstBeatsCanonicalField, value.max_burst_beats)) return false;
    if (!visitor(kDmaDescriptorOwnerCoreCanonicalField, value.owner_core)) return false;
    if (!visitor(kDmaDescriptorPhysicalStorageBytesCanonicalField, value.physical_storage_bytes)) return false;
    if (!visitor(kDmaDescriptorQosCanonicalField, value.qos)) return false;
    if (!visitor(kDmaDescriptorReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kDmaDescriptorReserved2CanonicalField, value.reserved2)) return false;
    if (!visitor(kDmaDescriptorRowBytesCanonicalField, value.row_bytes)) return false;
    if (!visitor(kDmaDescriptorRowsCanonicalField, value.rows)) return false;
    if (!visitor(kDmaDescriptorSrcCanonicalField, value.src)) return false;
    if (!visitor(kDmaDescriptorSrcStrideBytesCanonicalField, value.src_stride_bytes)) return false;
    if (!visitor(kDmaDescriptorTransferIdCanonicalField, value.transfer_id)) return false;
    if (!visitor(kDmaDescriptorUsefulBytesCanonicalField, value.useful_bytes)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kOpAttrKindCanonicalField = {"kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kOpAttrReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kOpAttrPayloadCanonicalField = {"payload", TransportFieldKind::Bytes, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const OpAttr &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kOpAttrKindCanonicalField, value.kind)) return false;
    if (!visitor(kOpAttrPayloadCanonicalField, value.payload)) return false;
    if (!visitor(kOpAttrReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kRelocationRelocationIdCanonicalField = {"relocation_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationSymbolSidCanonicalField = {"symbol_sid", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationKindCanonicalField = {"kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationRegionIdCanonicalField = {"region_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationTensorIdCanonicalField = {"tensor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationOffsetBytesCanonicalField = {"offset_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRelocationReserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const Relocation &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kRelocationKindCanonicalField, value.kind)) return false;
    if (!visitor(kRelocationOffsetBytesCanonicalField, value.offset_bytes)) return false;
    if (!visitor(kRelocationRegionIdCanonicalField, value.region_id)) return false;
    if (!visitor(kRelocationRelocationIdCanonicalField, value.relocation_id)) return false;
    if (!visitor(kRelocationReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kRelocationReserved2CanonicalField, value.reserved2)) return false;
    if (!visitor(kRelocationSymbolSidCanonicalField, value.symbol_sid)) return false;
    if (!visitor(kRelocationTensorIdCanonicalField, value.tensor_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kExpectedTrafficEntrypointIdCanonicalField = {"entrypoint_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficProfileIdCanonicalField = {"profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficCommandIdCanonicalField = {"command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficDescriptorIdCanonicalField = {"descriptor_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficKindCanonicalField = {"kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficUsefulBytesCanonicalField = {"useful_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficPhysicalBeatBytesCanonicalField = {"physical_beat_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficSegmentsCanonicalField = {"segments", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficBurstsCanonicalField = {"bursts", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficArCountCanonicalField = {"ar_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficRBeatsCanonicalField = {"r_beats", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficAwCountCanonicalField = {"aw_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficWBeatsCanonicalField = {"w_beats", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficBCountCanonicalField = {"b_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficFlitsCanonicalField = {"flits", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kExpectedTrafficReserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ExpectedTraffic &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kExpectedTrafficArCountCanonicalField, value.ar_count)) return false;
    if (!visitor(kExpectedTrafficAwCountCanonicalField, value.aw_count)) return false;
    if (!visitor(kExpectedTrafficBCountCanonicalField, value.b_count)) return false;
    if (!visitor(kExpectedTrafficBurstsCanonicalField, value.bursts)) return false;
    if (!visitor(kExpectedTrafficCommandIdCanonicalField, value.command_id)) return false;
    if (!visitor(kExpectedTrafficDescriptorIdCanonicalField, value.descriptor_id)) return false;
    if (!visitor(kExpectedTrafficEntrypointIdCanonicalField, value.entrypoint_id)) return false;
    if (!visitor(kExpectedTrafficFlitsCanonicalField, value.flits)) return false;
    if (!visitor(kExpectedTrafficKindCanonicalField, value.kind)) return false;
    if (!visitor(kExpectedTrafficPhysicalBeatBytesCanonicalField, value.physical_beat_bytes)) return false;
    if (!visitor(kExpectedTrafficProfileIdCanonicalField, value.profile_id)) return false;
    if (!visitor(kExpectedTrafficRBeatsCanonicalField, value.r_beats)) return false;
    if (!visitor(kExpectedTrafficReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kExpectedTrafficReserved2CanonicalField, value.reserved2)) return false;
    if (!visitor(kExpectedTrafficSegmentsCanonicalField, value.segments)) return false;
    if (!visitor(kExpectedTrafficUsefulBytesCanonicalField, value.useful_bytes)) return false;
    if (!visitor(kExpectedTrafficWBeatsCanonicalField, value.w_beats)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kSourceMapLocIdCanonicalField = {"loc_id", TransportFieldKind::U32, false, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kSourceMapFileSidCanonicalField = {"file", TransportFieldKind::U32, false, TransportStringPool::Semantic};
inline constexpr TransportFieldDescriptor kSourceMapLineCanonicalField = {"line", TransportFieldKind::U32, false, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kSourceMapColumnCanonicalField = {"column", TransportFieldKind::U32, false, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const SourceMap &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (projection == CanonicalProjection::Full && !visitor(kSourceMapColumnCanonicalField, value.column)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kSourceMapFileSidCanonicalField, value.file_sid)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kSourceMapLineCanonicalField, value.line)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kSourceMapLocIdCanonicalField, value.loc_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kContentDigestObjectKindCanonicalField = {"object_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kContentDigestReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kContentDigestObjectIdCanonicalField = {"object_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kContentDigestDigestCanonicalField = {"digest", TransportFieldKind::Bytes, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ContentDigest &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kContentDigestDigestCanonicalField, value.digest)) return false;
    if (!visitor(kContentDigestObjectIdCanonicalField, value.object_id)) return false;
    if (!visitor(kContentDigestObjectKindCanonicalField, value.object_kind)) return false;
    if (!visitor(kContentDigestReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kProfileHintEntrypointIdCanonicalField = {"entrypoint_id", TransportFieldKind::U32, false, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileHintProfileIdCanonicalField = {"profile_id", TransportFieldKind::U32, false, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileHintNameSidCanonicalField = {"name", TransportFieldKind::U32, false, TransportStringPool::Semantic};
inline constexpr TransportFieldDescriptor kProfileHintValueSidCanonicalField = {"value", TransportFieldKind::U32, false, TransportStringPool::Semantic};

template <typename Visitor> bool visitTransportFieldsCanonical(const ProfileHint &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (projection == CanonicalProjection::Full && !visitor(kProfileHintEntrypointIdCanonicalField, value.entrypoint_id)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kProfileHintNameSidCanonicalField, value.name_sid)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kProfileHintProfileIdCanonicalField, value.profile_id)) return false;
    if (projection == CanonicalProjection::Full && !visitor(kProfileHintValueSidCanonicalField, value.value_sid)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kWeightCacheBaseKeyCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightCacheBaseKeyCachePartitionIdCanonicalField = {"cache_partition_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightCacheBaseKeyWeightTagIndexCanonicalField = {"weight_tag_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightCacheBaseKeyCacheGenerationCanonicalField = {"cache_generation", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const WeightCacheBaseKey &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kWeightCacheBaseKeyCacheGenerationCanonicalField, value.cache_generation)) return false;
    if (!visitor(kWeightCacheBaseKeyCachePartitionIdCanonicalField, value.cache_partition_id)) return false;
    if (!visitor(kWeightCacheBaseKeyCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kWeightCacheBaseKeyWeightTagIndexCanonicalField, value.weight_tag_index)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kWeightFillKeyCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightFillKeyCachePartitionIdCanonicalField = {"cache_partition_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightFillKeyWeightTagIndexCanonicalField = {"weight_tag_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightFillKeyCacheGenerationCanonicalField = {"cache_generation", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kWeightFillKeyFillIncarnationCanonicalField = {"fill_incarnation", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const WeightFillKey &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kWeightFillKeyCacheGenerationCanonicalField, value.cache_generation)) return false;
    if (!visitor(kWeightFillKeyCachePartitionIdCanonicalField, value.cache_partition_id)) return false;
    if (!visitor(kWeightFillKeyCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kWeightFillKeyFillIncarnationCanonicalField, value.fill_incarnation)) return false;
    if (!visitor(kWeightFillKeyWeightTagIndexCanonicalField, value.weight_tag_index)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kCacheDmaDescriptorKeyWeightFillKeyCanonicalField = {"weight_fill_key", TransportFieldKind::Bytes, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kCacheDmaDescriptorKeySegmentOrdinalCanonicalField = {"segment_ordinal", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const CacheDmaDescriptorKey &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kCacheDmaDescriptorKeySegmentOrdinalCanonicalField, value.segment_ordinal)) return false;
    if (!visitor(kCacheDmaDescriptorKeyWeightFillKeyCanonicalField, value.weight_fill_key)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kErrorSourceKeyErrorClassCanonicalField = {"error_class", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyCoreIdOrFfffCanonicalField = {"core_id_or_ffff", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyDomainCanonicalField = {"domain", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyObjectKindCanonicalField = {"object_kind", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyRegionGroupIdCanonicalField = {"region_group_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyRegionIdCanonicalField = {"region_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyOrdinalCanonicalField = {"ordinal", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyGenerationCanonicalField = {"generation", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kErrorSourceKeyAuxKeyCanonicalField = {"aux_key", TransportFieldKind::Bytes, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ErrorSourceKey &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kErrorSourceKeyAuxKeyCanonicalField, value.aux_key)) return false;
    if (!visitor(kErrorSourceKeyCoreIdOrFfffCanonicalField, value.core_id_or_ffff)) return false;
    if (!visitor(kErrorSourceKeyDomainCanonicalField, value.domain)) return false;
    if (!visitor(kErrorSourceKeyErrorClassCanonicalField, value.error_class)) return false;
    if (!visitor(kErrorSourceKeyGenerationCanonicalField, value.generation)) return false;
    if (!visitor(kErrorSourceKeyObjectKindCanonicalField, value.object_kind)) return false;
    if (!visitor(kErrorSourceKeyOrdinalCanonicalField, value.ordinal)) return false;
    if (!visitor(kErrorSourceKeyRegionGroupIdCanonicalField, value.region_group_id)) return false;
    if (!visitor(kErrorSourceKeyRegionIdCanonicalField, value.region_id)) return false;
    if (!visitor(kErrorSourceKeyReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kProfileStreamRangeProfileIdCanonicalField = {"profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileStreamRangeCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileStreamRangeStreamIdCanonicalField = {"stream_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileStreamRangeCommandBeginCanonicalField = {"command_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kProfileStreamRangeCommandCountCanonicalField = {"command_count", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ProfileStreamRange &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kProfileStreamRangeCommandBeginCanonicalField, value.command_begin)) return false;
    if (!visitor(kProfileStreamRangeCommandCountCanonicalField, value.command_count)) return false;
    if (!visitor(kProfileStreamRangeCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kProfileStreamRangeProfileIdCanonicalField, value.profile_id)) return false;
    if (!visitor(kProfileStreamRangeStreamIdCanonicalField, value.stream_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntrySemanticTokenUidCanonicalField = {"semantic_token_uid", TransportFieldKind::Bytes, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryMemberRequestIdCanonicalField = {"member_request_id", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntrySourceRankCanonicalField = {"source_rank", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryTokenOrdinalCanonicalField = {"token_ordinal", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryTopkSlotCanonicalField = {"topk_slot", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntrySelectedExpertIdCanonicalField = {"selected_expert_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryAssignedExpertIdCanonicalField = {"assigned_expert_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryDestinationCoreCanonicalField = {"destination_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryDispositionCanonicalField = {"disposition", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeRuntimeRouteEntryReservedCanonicalField = {"reserved", TransportFieldKind::Bytes, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const MoeRuntimeRouteEntry &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kMoeRuntimeRouteEntryAssignedExpertIdCanonicalField, value.assigned_expert_id)) return false;
    if (!visitor(kMoeRuntimeRouteEntryDestinationCoreCanonicalField, value.destination_core)) return false;
    if (!visitor(kMoeRuntimeRouteEntryDispositionCanonicalField, value.disposition)) return false;
    if (!visitor(kMoeRuntimeRouteEntryMemberRequestIdCanonicalField, value.member_request_id)) return false;
    if (!visitor(kMoeRuntimeRouteEntryReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kMoeRuntimeRouteEntrySelectedExpertIdCanonicalField, value.selected_expert_id)) return false;
    if (!visitor(kMoeRuntimeRouteEntrySemanticTokenUidCanonicalField, value.semantic_token_uid)) return false;
    if (!visitor(kMoeRuntimeRouteEntrySourceRankCanonicalField, value.source_rank)) return false;
    if (!visitor(kMoeRuntimeRouteEntryTokenOrdinalCanonicalField, value.token_ordinal)) return false;
    if (!visitor(kMoeRuntimeRouteEntryTopkSlotCanonicalField, value.topk_slot)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kMoeLayerSpecLayerIdCanonicalField = {"layer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecKernelSpecIndexCanonicalField = {"kernel_spec_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecExpertFirstCanonicalField = {"expert_first", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecExpertCountCanonicalField = {"expert_count", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecTopKCanonicalField = {"top_k", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecTokenBytesCanonicalField = {"token_bytes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecOutputTokenBytesCanonicalField = {"output_token_bytes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecCapacityFactorQ16CanonicalField = {"capacity_factor_q16", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecOverflowPolicyCanonicalField = {"overflow_policy", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecTransportModeCanonicalField = {"transport_mode", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecDynamicRegionFirstCanonicalField = {"dynamic_region_first", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecDynamicRegionCountCanonicalField = {"dynamic_region_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxTokensPerFrozenBatchCanonicalField = {"max_tokens_per_frozen_batch", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxRequestsPerBatchCanonicalField = {"max_requests_per_batch", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxRoutesCanonicalField = {"max_routes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxMaterializedCommandsCanonicalField = {"max_materialized_commands", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxMaterializedDescriptorsCanonicalField = {"max_materialized_descriptors", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxMaterializedTransfersCanonicalField = {"max_materialized_transfers", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxDynamicAllocationsCanonicalField = {"max_dynamic_allocations", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecMaxMaterializedEventsCanonicalField = {"max_materialized_events", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeLayerSpecReserved1CanonicalField = {"reserved1", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const MoeLayerSpec &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kMoeLayerSpecCapacityFactorQ16CanonicalField, value.capacity_factor_q16)) return false;
    if (!visitor(kMoeLayerSpecDynamicRegionCountCanonicalField, value.dynamic_region_count)) return false;
    if (!visitor(kMoeLayerSpecDynamicRegionFirstCanonicalField, value.dynamic_region_first)) return false;
    if (!visitor(kMoeLayerSpecExpertCountCanonicalField, value.expert_count)) return false;
    if (!visitor(kMoeLayerSpecExpertFirstCanonicalField, value.expert_first)) return false;
    if (!visitor(kMoeLayerSpecFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kMoeLayerSpecKernelSpecIndexCanonicalField, value.kernel_spec_index)) return false;
    if (!visitor(kMoeLayerSpecLayerIdCanonicalField, value.layer_id)) return false;
    if (!visitor(kMoeLayerSpecMaxDynamicAllocationsCanonicalField, value.max_dynamic_allocations)) return false;
    if (!visitor(kMoeLayerSpecMaxMaterializedCommandsCanonicalField, value.max_materialized_commands)) return false;
    if (!visitor(kMoeLayerSpecMaxMaterializedDescriptorsCanonicalField, value.max_materialized_descriptors)) return false;
    if (!visitor(kMoeLayerSpecMaxMaterializedEventsCanonicalField, value.max_materialized_events)) return false;
    if (!visitor(kMoeLayerSpecMaxMaterializedTransfersCanonicalField, value.max_materialized_transfers)) return false;
    if (!visitor(kMoeLayerSpecMaxRequestsPerBatchCanonicalField, value.max_requests_per_batch)) return false;
    if (!visitor(kMoeLayerSpecMaxRoutesCanonicalField, value.max_routes)) return false;
    if (!visitor(kMoeLayerSpecMaxTokensPerFrozenBatchCanonicalField, value.max_tokens_per_frozen_batch)) return false;
    if (!visitor(kMoeLayerSpecOutputTokenBytesCanonicalField, value.output_token_bytes)) return false;
    if (!visitor(kMoeLayerSpecOverflowPolicyCanonicalField, value.overflow_policy)) return false;
    if (!visitor(kMoeLayerSpecReserved1CanonicalField, value.reserved1)) return false;
    if (!visitor(kMoeLayerSpecTokenBytesCanonicalField, value.token_bytes)) return false;
    if (!visitor(kMoeLayerSpecTopKCanonicalField, value.top_k)) return false;
    if (!visitor(kMoeLayerSpecTransportModeCanonicalField, value.transport_mode)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kMoeExpertSpecLayerIdCanonicalField = {"layer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecExpertIdCanonicalField = {"expert_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecFlagsCanonicalField = {"flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecReservedCoreCanonicalField = {"reserved_core", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecWeightSymbolIdCanonicalField = {"weight_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecWeightRegionOffsetCanonicalField = {"weight_region_offset", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecWeightBytesCanonicalField = {"weight_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecWeightDigestIndexCanonicalField = {"weight_digest_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeExpertSpecReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const MoeExpertSpec &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kMoeExpertSpecCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kMoeExpertSpecExpertIdCanonicalField, value.expert_id)) return false;
    if (!visitor(kMoeExpertSpecFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kMoeExpertSpecLayerIdCanonicalField, value.layer_id)) return false;
    if (!visitor(kMoeExpertSpecReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kMoeExpertSpecReservedCoreCanonicalField, value.reserved_core)) return false;
    if (!visitor(kMoeExpertSpecWeightBytesCanonicalField, value.weight_bytes)) return false;
    if (!visitor(kMoeExpertSpecWeightDigestIndexCanonicalField, value.weight_digest_index)) return false;
    if (!visitor(kMoeExpertSpecWeightRegionOffsetCanonicalField, value.weight_region_offset)) return false;
    if (!visitor(kMoeExpertSpecWeightSymbolIdCanonicalField, value.weight_symbol_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kMoeDynamicRegionRegionIdCanonicalField = {"region_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionLayerIdCanonicalField = {"layer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionStreamIdCanonicalField = {"stream_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionInsertAfterCommandIdCanonicalField = {"insert_after_command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionResumeBeforeCommandIdCanonicalField = {"resume_before_command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionEntryEventIdCanonicalField = {"entry_event_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionScratchOffsetCanonicalField = {"scratch_offset", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionScratchBytesCanonicalField = {"scratch_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionScratchAlignmentCanonicalField = {"scratch_alignment", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionMaxOverlayCommandsCanonicalField = {"max_overlay_commands", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionMaxOverlayEventsCanonicalField = {"max_overlay_events", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionMaxOverlayDescriptorsCanonicalField = {"max_overlay_descriptors", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionMaxOverlayTransfersCanonicalField = {"max_overlay_transfers", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionMaxOverlayAllocationsCanonicalField = {"max_overlay_allocations", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeDynamicRegionReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const MoeDynamicRegion &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kMoeDynamicRegionCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kMoeDynamicRegionEntryEventIdCanonicalField, value.entry_event_id)) return false;
    if (!visitor(kMoeDynamicRegionFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kMoeDynamicRegionInsertAfterCommandIdCanonicalField, value.insert_after_command_id)) return false;
    if (!visitor(kMoeDynamicRegionLayerIdCanonicalField, value.layer_id)) return false;
    if (!visitor(kMoeDynamicRegionMaxOverlayAllocationsCanonicalField, value.max_overlay_allocations)) return false;
    if (!visitor(kMoeDynamicRegionMaxOverlayCommandsCanonicalField, value.max_overlay_commands)) return false;
    if (!visitor(kMoeDynamicRegionMaxOverlayDescriptorsCanonicalField, value.max_overlay_descriptors)) return false;
    if (!visitor(kMoeDynamicRegionMaxOverlayEventsCanonicalField, value.max_overlay_events)) return false;
    if (!visitor(kMoeDynamicRegionMaxOverlayTransfersCanonicalField, value.max_overlay_transfers)) return false;
    if (!visitor(kMoeDynamicRegionRegionIdCanonicalField, value.region_id)) return false;
    if (!visitor(kMoeDynamicRegionReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kMoeDynamicRegionResumeBeforeCommandIdCanonicalField, value.resume_before_command_id)) return false;
    if (!visitor(kMoeDynamicRegionScratchAlignmentCanonicalField, value.scratch_alignment)) return false;
    if (!visitor(kMoeDynamicRegionScratchBytesCanonicalField, value.scratch_bytes)) return false;
    if (!visitor(kMoeDynamicRegionScratchOffsetCanonicalField, value.scratch_offset)) return false;
    if (!visitor(kMoeDynamicRegionStreamIdCanonicalField, value.stream_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kMoeKernelSpecLayerIdCanonicalField = {"layer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecExpertOpcodeCanonicalField = {"expert_opcode", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecInputDtypeCanonicalField = {"input_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecAccumDtypeCanonicalField = {"accum_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecOutputDtypeCanonicalField = {"output_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecBatchCanonicalField = {"batch", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecNCanonicalField = {"n", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecKCanonicalField = {"k", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecTransposeFlagsCanonicalField = {"transpose_flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecCombineKindCanonicalField = {"combine_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecAlgorithmIdCanonicalField = {"algorithm_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecEfficiencyQ16CanonicalField = {"efficiency_q16", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecTensorSetupCyclesCanonicalField = {"tensor_setup_cycles", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecTensorFlushCyclesCanonicalField = {"tensor_flush_cycles", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecInputTokenBytesCanonicalField = {"input_token_bytes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecOutputTokenBytesCanonicalField = {"output_token_bytes", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecReserved0CanonicalField = {"reserved0", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecWeightOperandBytesCanonicalField = {"weight_operand_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecExpertResultAlignmentCanonicalField = {"expert_result_alignment", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecMaxMCanonicalField = {"max_m", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecCombineSetupCyclesCanonicalField = {"combine_setup_cycles", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecCombineFlushCyclesCanonicalField = {"combine_flush_cycles", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kMoeKernelSpecReserved1CanonicalField = {"reserved1", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const MoeKernelSpec &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kMoeKernelSpecAccumDtypeCanonicalField, value.accum_dtype)) return false;
    if (!visitor(kMoeKernelSpecAlgorithmIdCanonicalField, value.algorithm_id)) return false;
    if (!visitor(kMoeKernelSpecBatchCanonicalField, value.batch)) return false;
    if (!visitor(kMoeKernelSpecCombineFlushCyclesCanonicalField, value.combine_flush_cycles)) return false;
    if (!visitor(kMoeKernelSpecCombineKindCanonicalField, value.combine_kind)) return false;
    if (!visitor(kMoeKernelSpecCombineSetupCyclesCanonicalField, value.combine_setup_cycles)) return false;
    if (!visitor(kMoeKernelSpecEfficiencyQ16CanonicalField, value.efficiency_q16)) return false;
    if (!visitor(kMoeKernelSpecExpertOpcodeCanonicalField, value.expert_opcode)) return false;
    if (!visitor(kMoeKernelSpecExpertResultAlignmentCanonicalField, value.expert_result_alignment)) return false;
    if (!visitor(kMoeKernelSpecFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kMoeKernelSpecInputDtypeCanonicalField, value.input_dtype)) return false;
    if (!visitor(kMoeKernelSpecInputTokenBytesCanonicalField, value.input_token_bytes)) return false;
    if (!visitor(kMoeKernelSpecKCanonicalField, value.k)) return false;
    if (!visitor(kMoeKernelSpecLayerIdCanonicalField, value.layer_id)) return false;
    if (!visitor(kMoeKernelSpecMaxMCanonicalField, value.max_m)) return false;
    if (!visitor(kMoeKernelSpecNCanonicalField, value.n)) return false;
    if (!visitor(kMoeKernelSpecOutputDtypeCanonicalField, value.output_dtype)) return false;
    if (!visitor(kMoeKernelSpecOutputTokenBytesCanonicalField, value.output_token_bytes)) return false;
    if (!visitor(kMoeKernelSpecReserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kMoeKernelSpecReserved1CanonicalField, value.reserved1)) return false;
    if (!visitor(kMoeKernelSpecTensorFlushCyclesCanonicalField, value.tensor_flush_cycles)) return false;
    if (!visitor(kMoeKernelSpecTensorSetupCyclesCanonicalField, value.tensor_setup_cycles)) return false;
    if (!visitor(kMoeKernelSpecTransposeFlagsCanonicalField, value.transpose_flags)) return false;
    if (!visitor(kMoeKernelSpecWeightOperandBytesCanonicalField, value.weight_operand_bytes)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentRequestProfileProgramIdCanonicalField = {"program_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileProfileIdCanonicalField = {"profile_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileFlagsCanonicalField = {"flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileReserved0CanonicalField = {"reserved0", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileRequestedProfileKeyCanonicalField = {"requested_profile_key", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileDeltaInputTokensCanonicalField = {"delta_input_tokens", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileFullInputTokensCanonicalField = {"full_input_tokens", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileExpectedCachedTokensCanonicalField = {"expected_cached_tokens", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileOutputTokensCanonicalField = {"output_tokens", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileInputBindingBytesCanonicalField = {"input_binding_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileDeltaInputDmaBytesCanonicalField = {"delta_input_dma_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileFullInputDmaBytesCanonicalField = {"full_input_dma_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileHostOutputBytesCanonicalField = {"host_output_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfilePrimaryInputSymbolIdCanonicalField = {"primary_input_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfilePrimaryOutputSymbolIdCanonicalField = {"primary_output_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfilePrimaryKvSymbolIdCanonicalField = {"primary_kv_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileSourceRankCountCanonicalField = {"source_rank_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileSourceCoreMapBeginCanonicalField = {"source_core_map_begin", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfilePathMaskCanonicalField = {"path_mask", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfileKvBytesPerTokenCanonicalField = {"kv_bytes_per_token", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestProfilePublishChunkBytesCanonicalField = {"publish_chunk_bytes", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentRequestProfile &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentRequestProfileDeltaInputDmaBytesCanonicalField, value.delta_input_dma_bytes)) return false;
    if (!visitor(kAgentRequestProfileDeltaInputTokensCanonicalField, value.delta_input_tokens)) return false;
    if (!visitor(kAgentRequestProfileExpectedCachedTokensCanonicalField, value.expected_cached_tokens)) return false;
    if (!visitor(kAgentRequestProfileFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kAgentRequestProfileFullInputDmaBytesCanonicalField, value.full_input_dma_bytes)) return false;
    if (!visitor(kAgentRequestProfileFullInputTokensCanonicalField, value.full_input_tokens)) return false;
    if (!visitor(kAgentRequestProfileHostOutputBytesCanonicalField, value.host_output_bytes)) return false;
    if (!visitor(kAgentRequestProfileInputBindingBytesCanonicalField, value.input_binding_bytes)) return false;
    if (!visitor(kAgentRequestProfileKvBytesPerTokenCanonicalField, value.kv_bytes_per_token)) return false;
    if (!visitor(kAgentRequestProfileOutputTokensCanonicalField, value.output_tokens)) return false;
    if (!visitor(kAgentRequestProfilePathMaskCanonicalField, value.path_mask)) return false;
    if (!visitor(kAgentRequestProfilePrimaryInputSymbolIdCanonicalField, value.primary_input_symbol_id)) return false;
    if (!visitor(kAgentRequestProfilePrimaryKvSymbolIdCanonicalField, value.primary_kv_symbol_id)) return false;
    if (!visitor(kAgentRequestProfilePrimaryOutputSymbolIdCanonicalField, value.primary_output_symbol_id)) return false;
    if (!visitor(kAgentRequestProfileProfileIdCanonicalField, value.profile_id)) return false;
    if (!visitor(kAgentRequestProfileProgramIdCanonicalField, value.program_id)) return false;
    if (!visitor(kAgentRequestProfilePublishChunkBytesCanonicalField, value.publish_chunk_bytes)) return false;
    if (!visitor(kAgentRequestProfileRequestedProfileKeyCanonicalField, value.requested_profile_key)) return false;
    if (!visitor(kAgentRequestProfileReserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kAgentRequestProfileSourceCoreMapBeginCanonicalField, value.source_core_map_begin)) return false;
    if (!visitor(kAgentRequestProfileSourceRankCountCanonicalField, value.source_rank_count)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentInstanceProfileInstanceProfileIdCanonicalField = {"instance_profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileRequestProgramIdCanonicalField = {"request_program_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileRequestProfileIdCanonicalField = {"request_profile_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfilePathKindCanonicalField = {"path_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfilePhaseCanonicalField = {"phase", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileMemberCountCanonicalField = {"member_count", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileDecodeChunkTokensCanonicalField = {"decode_chunk_tokens", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileMeshEntrypointIdCanonicalField = {"mesh_entrypoint_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileMeshProfileIdCanonicalField = {"mesh_profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileValidTokensPerMemberCanonicalField = {"valid_tokens_per_member", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileKvTokensBeforeCanonicalField = {"kv_tokens_before", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileLocalPaddedMembersCanonicalField = {"local_padded_members", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileLocalPaddedTokensPerMemberCanonicalField = {"local_padded_tokens_per_member", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfilePrimaryInputSymbolIdCanonicalField = {"primary_input_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfilePrimaryOutputSymbolIdCanonicalField = {"primary_output_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfilePrimaryKvSymbolIdCanonicalField = {"primary_kv_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileFlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileMemberBindingFirstCanonicalField = {"member_binding_first", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileMemberBindingCountCanonicalField = {"member_binding_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileHostInputDmaBytesPerMemberCanonicalField = {"host_input_dma_bytes_per_member", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileHostOutputDmaBytesPerMemberCanonicalField = {"host_output_dma_bytes_per_member", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileKvReadBytesPerMemberCanonicalField = {"kv_read_bytes_per_member", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceProfileKvWriteBytesPerMemberCanonicalField = {"kv_write_bytes_per_member", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentInstanceProfile &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentInstanceProfileDecodeChunkTokensCanonicalField, value.decode_chunk_tokens)) return false;
    if (!visitor(kAgentInstanceProfileFlagsCanonicalField, value.flags)) return false;
    if (!visitor(kAgentInstanceProfileHostInputDmaBytesPerMemberCanonicalField, value.host_input_dma_bytes_per_member)) return false;
    if (!visitor(kAgentInstanceProfileHostOutputDmaBytesPerMemberCanonicalField, value.host_output_dma_bytes_per_member)) return false;
    if (!visitor(kAgentInstanceProfileInstanceProfileIdCanonicalField, value.instance_profile_id)) return false;
    if (!visitor(kAgentInstanceProfileKvReadBytesPerMemberCanonicalField, value.kv_read_bytes_per_member)) return false;
    if (!visitor(kAgentInstanceProfileKvTokensBeforeCanonicalField, value.kv_tokens_before)) return false;
    if (!visitor(kAgentInstanceProfileKvWriteBytesPerMemberCanonicalField, value.kv_write_bytes_per_member)) return false;
    if (!visitor(kAgentInstanceProfileLocalPaddedMembersCanonicalField, value.local_padded_members)) return false;
    if (!visitor(kAgentInstanceProfileLocalPaddedTokensPerMemberCanonicalField, value.local_padded_tokens_per_member)) return false;
    if (!visitor(kAgentInstanceProfileMemberBindingCountCanonicalField, value.member_binding_count)) return false;
    if (!visitor(kAgentInstanceProfileMemberBindingFirstCanonicalField, value.member_binding_first)) return false;
    if (!visitor(kAgentInstanceProfileMemberCountCanonicalField, value.member_count)) return false;
    if (!visitor(kAgentInstanceProfileMeshEntrypointIdCanonicalField, value.mesh_entrypoint_id)) return false;
    if (!visitor(kAgentInstanceProfileMeshProfileIdCanonicalField, value.mesh_profile_id)) return false;
    if (!visitor(kAgentInstanceProfilePathKindCanonicalField, value.path_kind)) return false;
    if (!visitor(kAgentInstanceProfilePhaseCanonicalField, value.phase)) return false;
    if (!visitor(kAgentInstanceProfilePrimaryInputSymbolIdCanonicalField, value.primary_input_symbol_id)) return false;
    if (!visitor(kAgentInstanceProfilePrimaryKvSymbolIdCanonicalField, value.primary_kv_symbol_id)) return false;
    if (!visitor(kAgentInstanceProfilePrimaryOutputSymbolIdCanonicalField, value.primary_output_symbol_id)) return false;
    if (!visitor(kAgentInstanceProfileRequestProfileIdCanonicalField, value.request_profile_id)) return false;
    if (!visitor(kAgentInstanceProfileRequestProgramIdCanonicalField, value.request_program_id)) return false;
    if (!visitor(kAgentInstanceProfileValidTokensPerMemberCanonicalField, value.valid_tokens_per_member)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentSourceCoreMapCoreIdCanonicalField = {"core_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentSourceCoreMapReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentSourceCoreMap &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentSourceCoreMapCoreIdCanonicalField, value.core_id)) return false;
    if (!visitor(kAgentSourceCoreMapReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingInstanceProfileIdCanonicalField = {"instance_profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingMemberOrdinalCanonicalField = {"member_ordinal", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingReserved0CanonicalField = {"reserved0", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingStaticInputSymbolIdCanonicalField = {"static_input_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingStaticOutputSymbolIdCanonicalField = {"static_output_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingStaticKvSymbolIdCanonicalField = {"static_kv_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceMemberBindingExpectedLogicalSourceRankCanonicalField = {"expected_logical_source_rank", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentInstanceMemberBinding &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentInstanceMemberBindingExpectedLogicalSourceRankCanonicalField, value.expected_logical_source_rank)) return false;
    if (!visitor(kAgentInstanceMemberBindingInstanceProfileIdCanonicalField, value.instance_profile_id)) return false;
    if (!visitor(kAgentInstanceMemberBindingMemberOrdinalCanonicalField, value.member_ordinal)) return false;
    if (!visitor(kAgentInstanceMemberBindingReserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kAgentInstanceMemberBindingStaticInputSymbolIdCanonicalField, value.static_input_symbol_id)) return false;
    if (!visitor(kAgentInstanceMemberBindingStaticKvSymbolIdCanonicalField, value.static_kv_symbol_id)) return false;
    if (!visitor(kAgentInstanceMemberBindingStaticOutputSymbolIdCanonicalField, value.static_output_symbol_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingInstanceProfileIdCanonicalField = {"instance_profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingMemberOrdinalCanonicalField = {"member_ordinal", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingBindingKindCanonicalField = {"binding_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingStaticSymbolIdCanonicalField = {"static_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingRequestSymbolIdCanonicalField = {"request_symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingOffsetBytesCanonicalField = {"offset_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentInstanceTensorBindingSpanBytesCanonicalField = {"span_bytes", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentInstanceTensorBinding &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentInstanceTensorBindingBindingKindCanonicalField, value.binding_kind)) return false;
    if (!visitor(kAgentInstanceTensorBindingInstanceProfileIdCanonicalField, value.instance_profile_id)) return false;
    if (!visitor(kAgentInstanceTensorBindingMemberOrdinalCanonicalField, value.member_ordinal)) return false;
    if (!visitor(kAgentInstanceTensorBindingOffsetBytesCanonicalField, value.offset_bytes)) return false;
    if (!visitor(kAgentInstanceTensorBindingRequestSymbolIdCanonicalField, value.request_symbol_id)) return false;
    if (!visitor(kAgentInstanceTensorBindingSpanBytesCanonicalField, value.span_bytes)) return false;
    if (!visitor(kAgentInstanceTensorBindingStaticSymbolIdCanonicalField, value.static_symbol_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementRequestProgramIdCanonicalField = {"request_program_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementRequestProfileIdCanonicalField = {"request_profile_id", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementBindingKindCanonicalField = {"binding_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementBindingFlagsCanonicalField = {"binding_flags", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementSymbolIdCanonicalField = {"symbol_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentRequestBindingRequirementReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentRequestBindingRequirement &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentRequestBindingRequirementBindingFlagsCanonicalField, value.binding_flags)) return false;
    if (!visitor(kAgentRequestBindingRequirementBindingKindCanonicalField, value.binding_kind)) return false;
    if (!visitor(kAgentRequestBindingRequirementRequestProfileIdCanonicalField, value.request_profile_id)) return false;
    if (!visitor(kAgentRequestBindingRequirementRequestProgramIdCanonicalField, value.request_program_id)) return false;
    if (!visitor(kAgentRequestBindingRequirementReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kAgentRequestBindingRequirementSymbolIdCanonicalField, value.symbol_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingInstanceProfileIdCanonicalField = {"instance_profile_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingMemberOrdinalCanonicalField = {"member_ordinal", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingReserved0CanonicalField = {"reserved0", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingAllocationIdCanonicalField = {"allocation_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingProducerCommandIdCanonicalField = {"producer_command_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingCompletionEventIdCanonicalField = {"completion_event_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingFillKindCanonicalField = {"fill_kind", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingAllocationRoleCanonicalField = {"allocation_role", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingDigestSourceCanonicalField = {"digest_source", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kAgentPublishSurrogateBindingReserved1CanonicalField = {"reserved1", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const AgentPublishSurrogateBinding &value, CanonicalProjection projection, Visitor &&visitor)
{
    (void)projection;
    if (!visitor(kAgentPublishSurrogateBindingAllocationIdCanonicalField, value.allocation_id)) return false;
    if (!visitor(kAgentPublishSurrogateBindingAllocationRoleCanonicalField, value.allocation_role)) return false;
    if (!visitor(kAgentPublishSurrogateBindingCompletionEventIdCanonicalField, value.completion_event_id)) return false;
    if (!visitor(kAgentPublishSurrogateBindingDigestSourceCanonicalField, value.digest_source)) return false;
    if (!visitor(kAgentPublishSurrogateBindingFillKindCanonicalField, value.fill_kind)) return false;
    if (!visitor(kAgentPublishSurrogateBindingInstanceProfileIdCanonicalField, value.instance_profile_id)) return false;
    if (!visitor(kAgentPublishSurrogateBindingMemberOrdinalCanonicalField, value.member_ordinal)) return false;
    if (!visitor(kAgentPublishSurrogateBindingProducerCommandIdCanonicalField, value.producer_command_id)) return false;
    if (!visitor(kAgentPublishSurrogateBindingReserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kAgentPublishSurrogateBindingReserved1CanonicalField, value.reserved1)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kRepeatV1SubrangeBeginStreamOrdinalCanonicalField = {"subrange_begin_stream_ordinal", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRepeatV1SubrangeCommandCountCanonicalField = {"subrange_command_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRepeatV1RepeatCountCanonicalField = {"repeat_count", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRepeatV1FlagsCanonicalField = {"flags", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const RepeatV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kRepeatV1FlagsCanonicalField, value.flags)) return false;
    if (!visitor(kRepeatV1RepeatCountCanonicalField, value.repeat_count)) return false;
    if (!visitor(kRepeatV1SubrangeBeginStreamOrdinalCanonicalField, value.subrange_begin_stream_ordinal)) return false;
    if (!visitor(kRepeatV1SubrangeCommandCountCanonicalField, value.subrange_command_count)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kGemmV1BatchCanonicalField = {"batch", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1MCanonicalField = {"m", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1NCanonicalField = {"n", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1KCanonicalField = {"k", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1ATransposeCanonicalField = {"a_transpose", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1BTransposeCanonicalField = {"b_transpose", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1AccumDtypeCanonicalField = {"accum_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1EpilogueCanonicalField = {"epilogue", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGemmV1EfficiencyQ16CanonicalField = {"efficiency_q16", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const GemmV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kGemmV1ATransposeCanonicalField, value.a_transpose)) return false;
    if (!visitor(kGemmV1AccumDtypeCanonicalField, value.accum_dtype)) return false;
    if (!visitor(kGemmV1BTransposeCanonicalField, value.b_transpose)) return false;
    if (!visitor(kGemmV1BatchCanonicalField, value.batch)) return false;
    if (!visitor(kGemmV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kGemmV1EfficiencyQ16CanonicalField, value.efficiency_q16)) return false;
    if (!visitor(kGemmV1EpilogueCanonicalField, value.epilogue)) return false;
    if (!visitor(kGemmV1KCanonicalField, value.k)) return false;
    if (!visitor(kGemmV1MCanonicalField, value.m)) return false;
    if (!visitor(kGemmV1NCanonicalField, value.n)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kBmmV1BatchCanonicalField = {"batch", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1MCanonicalField = {"m", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1NCanonicalField = {"n", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1KCanonicalField = {"k", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1ATransposeCanonicalField = {"a_transpose", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1BTransposeCanonicalField = {"b_transpose", TransportFieldKind::U8, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1AccumDtypeCanonicalField = {"accum_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1EpilogueCanonicalField = {"epilogue", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBmmV1EfficiencyQ16CanonicalField = {"efficiency_q16", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const BmmV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kBmmV1ATransposeCanonicalField, value.a_transpose)) return false;
    if (!visitor(kBmmV1AccumDtypeCanonicalField, value.accum_dtype)) return false;
    if (!visitor(kBmmV1BTransposeCanonicalField, value.b_transpose)) return false;
    if (!visitor(kBmmV1BatchCanonicalField, value.batch)) return false;
    if (!visitor(kBmmV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kBmmV1EfficiencyQ16CanonicalField, value.efficiency_q16)) return false;
    if (!visitor(kBmmV1EpilogueCanonicalField, value.epilogue)) return false;
    if (!visitor(kBmmV1KCanonicalField, value.k)) return false;
    if (!visitor(kBmmV1MCanonicalField, value.m)) return false;
    if (!visitor(kBmmV1NCanonicalField, value.n)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kElementwiseV1ElementCountCanonicalField = {"element_count", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kElementwiseV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kElementwiseV1OpCanonicalField = {"op", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kElementwiseV1OpsPerElementCanonicalField = {"ops_per_element", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kElementwiseV1ReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ElementwiseV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kElementwiseV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kElementwiseV1ElementCountCanonicalField, value.element_count)) return false;
    if (!visitor(kElementwiseV1OpCanonicalField, value.op)) return false;
    if (!visitor(kElementwiseV1OpsPerElementCanonicalField, value.ops_per_element)) return false;
    if (!visitor(kElementwiseV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kReduceV1ElementCountCanonicalField = {"element_count", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kReduceV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kReduceV1AccumDtypeCanonicalField = {"accum_dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kReduceV1OpCanonicalField = {"op", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kReduceV1FanInCanonicalField = {"fan_in", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const ReduceV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kReduceV1AccumDtypeCanonicalField, value.accum_dtype)) return false;
    if (!visitor(kReduceV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kReduceV1ElementCountCanonicalField, value.element_count)) return false;
    if (!visitor(kReduceV1FanInCanonicalField, value.fan_in)) return false;
    if (!visitor(kReduceV1OpCanonicalField, value.op)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kSoftmaxV1AxisSizeCanonicalField = {"axis_size", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kSoftmaxV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kSoftmaxV1AlgorithmCanonicalField = {"algorithm", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kSoftmaxV1ReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const SoftmaxV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kSoftmaxV1AlgorithmCanonicalField, value.algorithm)) return false;
    if (!visitor(kSoftmaxV1AxisSizeCanonicalField, value.axis_size)) return false;
    if (!visitor(kSoftmaxV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kSoftmaxV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kNormV1ElementCountCanonicalField = {"element_count", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kNormV1DtypeCanonicalField = {"dtype", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kNormV1AlgorithmCanonicalField = {"algorithm", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kNormV1ReservedCanonicalField = {"reserved", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const NormV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kNormV1AlgorithmCanonicalField, value.algorithm)) return false;
    if (!visitor(kNormV1DtypeCanonicalField, value.dtype)) return false;
    if (!visitor(kNormV1ElementCountCanonicalField, value.element_count)) return false;
    if (!visitor(kNormV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kFillV1PatternCanonicalField = {"pattern", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kFillV1ReservedCanonicalField = {"reserved", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const FillV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kFillV1PatternCanonicalField, value.pattern)) return false;
    if (!visitor(kFillV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kGatherIndexV1IndexOperandIndexCanonicalField = {"index_operand_index", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGatherIndexV1IndexOffsetBytesCanonicalField = {"index_offset_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGatherIndexV1IndexStrideBytesCanonicalField = {"index_stride_bytes", TransportFieldKind::U64, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kGatherIndexV1ReservedCanonicalField = {"reserved", TransportFieldKind::U64, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const GatherIndexV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kGatherIndexV1IndexOffsetBytesCanonicalField, value.index_offset_bytes)) return false;
    if (!visitor(kGatherIndexV1IndexOperandIndexCanonicalField, value.index_operand_index)) return false;
    if (!visitor(kGatherIndexV1IndexStrideBytesCanonicalField, value.index_stride_bytes)) return false;
    if (!visitor(kGatherIndexV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kBlockedMnkLayoutV1BlockMCanonicalField = {"block_m", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBlockedMnkLayoutV1BlockNCanonicalField = {"block_n", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBlockedMnkLayoutV1BlockKCanonicalField = {"block_k", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBlockedMnkLayoutV1MinorToMajorCanonicalField = {"minor_to_major", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kBlockedMnkLayoutV1ReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const BlockedMnkLayoutV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kBlockedMnkLayoutV1BlockKCanonicalField, value.block_k)) return false;
    if (!visitor(kBlockedMnkLayoutV1BlockMCanonicalField, value.block_m)) return false;
    if (!visitor(kBlockedMnkLayoutV1BlockNCanonicalField, value.block_n)) return false;
    if (!visitor(kBlockedMnkLayoutV1MinorToMajorCanonicalField, value.minor_to_major)) return false;
    if (!visitor(kBlockedMnkLayoutV1ReservedCanonicalField, value.reserved)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kRecvWaitV1TransferIdCanonicalField = {"transfer_id", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRecvWaitV1Reserved0CanonicalField = {"reserved0", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRecvWaitV1Reserved1CanonicalField = {"reserved1", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kRecvWaitV1Reserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const RecvWaitV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kRecvWaitV1Reserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kRecvWaitV1Reserved1CanonicalField, value.reserved1)) return false;
    if (!visitor(kRecvWaitV1Reserved2CanonicalField, value.reserved2)) return false;
    if (!visitor(kRecvWaitV1TransferIdCanonicalField, value.transfer_id)) return false;
    return true;
}

inline constexpr TransportFieldDescriptor kFenceV1FenceScopeCanonicalField = {"fence_scope", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kFenceV1ReservedCanonicalField = {"reserved", TransportFieldKind::U16, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kFenceV1Reserved0CanonicalField = {"reserved0", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kFenceV1Reserved1CanonicalField = {"reserved1", TransportFieldKind::U32, true, TransportStringPool::None};
inline constexpr TransportFieldDescriptor kFenceV1Reserved2CanonicalField = {"reserved2", TransportFieldKind::U32, true, TransportStringPool::None};

template <typename Visitor> bool visitTransportFieldsCanonical(const FenceV1 &value, CanonicalProjection, Visitor &&visitor)
{
    if (!visitor(kFenceV1FenceScopeCanonicalField, value.fence_scope)) return false;
    if (!visitor(kFenceV1ReservedCanonicalField, value.reserved)) return false;
    if (!visitor(kFenceV1Reserved0CanonicalField, value.reserved0)) return false;
    if (!visitor(kFenceV1Reserved1CanonicalField, value.reserved1)) return false;
    if (!visitor(kFenceV1Reserved2CanonicalField, value.reserved2)) return false;
    return true;
}

template <typename Visitor> bool visitAttrPayloadCanonical(uint16_t kind, const AttrPayload &payload, CanonicalProjection projection, Visitor &&visitor)
{
    switch (kind) {
    case kAttrKindREPEAT_V1: {
        const auto *value = std::get_if<RepeatV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindGEMM_V1: {
        const auto *value = std::get_if<GemmV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindBMM_V1: {
        const auto *value = std::get_if<BmmV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindELEMENTWISE_V1: {
        const auto *value = std::get_if<ElementwiseV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindREDUCE_V1: {
        const auto *value = std::get_if<ReduceV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindSOFTMAX_V1: {
        const auto *value = std::get_if<SoftmaxV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindNORM_V1: {
        const auto *value = std::get_if<NormV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindFILL_V1: {
        const auto *value = std::get_if<FillV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindGATHER_INDEX_V1: {
        const auto *value = std::get_if<GatherIndexV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindBLOCKED_MNK_LAYOUT_V1: {
        const auto *value = std::get_if<BlockedMnkLayoutV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindRECV_WAIT_V1: {
        const auto *value = std::get_if<RecvWaitV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    case kAttrKindFENCE_V1: {
        const auto *value = std::get_if<FenceV1>(&payload);
        return value != nullptr && visitTransportFieldsCanonical(*value, projection, visitor);
    }
    default: return false;
    }
}

#endif

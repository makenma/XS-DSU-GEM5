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

inline constexpr TransportSectionDescriptor kAllocationTransportSection = {"ALLOCATIONS", 6, "allocations", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandTransportSection = {"COMMANDS", 8, "commands", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandOperandTransportSection = {"COMMAND_OPERANDS", 10, "command_operands", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kCommandWaitTransportSection = {"COMMAND_WAITS", 9, "command_waits", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kContentDigestTransportSection = {"CONTENT_DIGESTS", 103, "content_digests", TransportProjectionKind::Records, true, true};
inline constexpr TransportSectionDescriptor kDmaDescriptorTransportSection = {"DMA_DESCRIPTORS", 12, "dma_descriptors", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kEntrypointTransportSection = {"ENTRYPOINTS", 2, "entrypoints", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kEventTransportSection = {"EVENTS", 11, "events", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kExpectedTrafficTransportSection = {"EXPECTED_TRAFFIC", 15, "expected_traffic", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kOpAttrTransportSection = {"OP_ATTRS", 13, "op_attrs", TransportProjectionKind::AttrPayloads, false, true};
inline constexpr TransportSectionDescriptor kProfileTransportSection = {"PROFILES", 3, "profiles", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kProfileHintTransportSection = {"PROFILE_HINTS", 102, "profile_hints", TransportProjectionKind::Records, true, false};
inline constexpr TransportSectionDescriptor kRelocationTransportSection = {"RELOCATIONS", 14, "relocations", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kShardTransportSection = {"SHARDS", 5, "shards", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kSourceMapTransportSection = {"SOURCE_MAP", 101, "source_map", TransportProjectionKind::Records, true, false};
inline constexpr TransportSectionDescriptor kStreamTransportSection = {"STREAMS", 7, "streams", TransportProjectionKind::Records, false, true};
inline constexpr TransportSectionDescriptor kStringTransportSection = {"STRINGS", 1, "strings", TransportProjectionKind::Strings, false, true};
inline constexpr TransportSectionDescriptor kTensorTransportSection = {"TENSORS", 4, "tensors", TransportProjectionKind::Records, false, true};

inline constexpr std::array<TransportSectionDescriptor, 18> kTransportCanonicalSections = {{
    kAllocationTransportSection,
    kCommandTransportSection,
    kCommandOperandTransportSection,
    kCommandWaitTransportSection,
    kContentDigestTransportSection,
    kDmaDescriptorTransportSection,
    kEntrypointTransportSection,
    kEventTransportSection,
    kExpectedTrafficTransportSection,
    kOpAttrTransportSection,
    kProfileTransportSection,
    kProfileHintTransportSection,
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
inline constexpr TransportFieldDescriptor kExpectedTrafficMinFlitsCanonicalField = {"min_flits", TransportFieldKind::U32, true, TransportStringPool::None};
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
    if (!visitor(kExpectedTrafficKindCanonicalField, value.kind)) return false;
    if (!visitor(kExpectedTrafficMinFlitsCanonicalField, value.min_flits)) return false;
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

#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_STORAGE_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_STORAGE_HH

struct TypedOpAttr
{
    uint16_t kind = 0;
    AttrPayload payload{};
    template <typename Payload> Payload *as() { return std::get_if<Payload>(&payload); }
    template <typename Payload> const Payload *as() const { return std::get_if<Payload>(&payload); }
};

inline bool decodeTypedOpAttr(const uint8_t *data, TypedOpAttr &out, AbiError &error)
{
    OpAttr raw{};
    if (!decodeOpAttr(data, raw, error)) return false;
    TypedOpAttr candidate{};
    candidate.kind = raw.kind;
    if (!decodeAttrPayload(raw.kind, raw.payload.data(), candidate.payload, error)) return false;
    for (uint32_t index = attrPayloadBytes(raw.kind); index < raw.payload.size(); ++index) {
        if (raw.payload[index] != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "attr payload padding must be zero"}; return false; }
    }
    out = candidate;
    return true;
}

inline bool encodeTypedOpAttr(const TypedOpAttr &value, std::array<uint8_t, kOpAttrsBytes> &out, AbiError &error)
{
    OpAttr raw{};
    raw.kind = value.kind;
    switch (value.kind) {
    case kAttrKindREPEAT_V1: {
        const auto *payload = value.as<RepeatV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "REPEAT_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeRepeatV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindGEMM_V1: {
        const auto *payload = value.as<GemmV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "GEMM_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeGemmV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindBMM_V1: {
        const auto *payload = value.as<BmmV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "BMM_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeBmmV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindELEMENTWISE_V1: {
        const auto *payload = value.as<ElementwiseV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "ELEMENTWISE_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeElementwiseV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindREDUCE_V1: {
        const auto *payload = value.as<ReduceV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "REDUCE_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeReduceV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindSOFTMAX_V1: {
        const auto *payload = value.as<SoftmaxV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "SOFTMAX_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeSoftmaxV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindNORM_V1: {
        const auto *payload = value.as<NormV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "NORM_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeNormV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindFILL_V1: {
        const auto *payload = value.as<FillV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "FILL_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeFillV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindBLOCKED_MNK_LAYOUT_V1: {
        const auto *payload = value.as<BlockedMnkLayoutV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "BLOCKED_MNK_LAYOUT_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeBlockedMnkLayoutV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindRECV_WAIT_V1: {
        const auto *payload = value.as<RecvWaitV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "RECV_WAIT_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeRecvWaitV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    case kAttrKindFENCE_V1: {
        const auto *payload = value.as<FenceV1>();
        if (payload == nullptr) { error = {mesh_diagnostics::E_ABI_ENUM, "FENCE_V1 kind and payload disagree"}; return false; }
        const auto encoded = encodeFenceV1(*payload);
        std::memcpy(raw.payload.data(), encoded.data(), encoded.size());
        break;
    }
    default:
        error = {mesh_diagnostics::E_ABI_ENUM, "unknown attr kind"};
        return false;
    }
    out = encodeOpAttr(raw);
    return true;
}

template <typename Record> struct TransportRecordTraits;

template <> struct TransportRecordTraits<Entrypoint>
{
    static constexpr TransportSectionDescriptor section = kEntrypointTransportSection;
    static constexpr uint32_t record_bytes = kEntrypointsBytes;
    static bool decode(const uint8_t *data, Entrypoint &value, AbiError &error) { return decodeEntrypoint(data, value, error); }
    static bool encode(const Entrypoint &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeEntrypoint(value); return true; }
};

template <> struct TransportRecordTraits<Profile>
{
    static constexpr TransportSectionDescriptor section = kProfileTransportSection;
    static constexpr uint32_t record_bytes = kProfilesBytes;
    static bool decode(const uint8_t *data, Profile &value, AbiError &error) { return decodeProfile(data, value, error); }
    static bool encode(const Profile &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeProfile(value); return true; }
};

template <> struct TransportRecordTraits<Tensor>
{
    static constexpr TransportSectionDescriptor section = kTensorTransportSection;
    static constexpr uint32_t record_bytes = kTensorsBytes;
    static bool decode(const uint8_t *data, Tensor &value, AbiError &error) { return decodeTensor(data, value, error); }
    static bool encode(const Tensor &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeTensor(value); return true; }
};

template <> struct TransportRecordTraits<Shard>
{
    static constexpr TransportSectionDescriptor section = kShardTransportSection;
    static constexpr uint32_t record_bytes = kShardsBytes;
    static bool decode(const uint8_t *data, Shard &value, AbiError &error) { return decodeShard(data, value, error); }
    static bool encode(const Shard &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeShard(value); return true; }
};

template <> struct TransportRecordTraits<Allocation>
{
    static constexpr TransportSectionDescriptor section = kAllocationTransportSection;
    static constexpr uint32_t record_bytes = kAllocationsBytes;
    static bool decode(const uint8_t *data, Allocation &value, AbiError &error) { return decodeAllocation(data, value, error); }
    static bool encode(const Allocation &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeAllocation(value); return true; }
};

template <> struct TransportRecordTraits<Stream>
{
    static constexpr TransportSectionDescriptor section = kStreamTransportSection;
    static constexpr uint32_t record_bytes = kStreamsBytes;
    static bool decode(const uint8_t *data, Stream &value, AbiError &error) { return decodeStream(data, value, error); }
    static bool encode(const Stream &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeStream(value); return true; }
};

template <> struct TransportRecordTraits<Command>
{
    static constexpr TransportSectionDescriptor section = kCommandTransportSection;
    static constexpr uint32_t record_bytes = kCommandsBytes;
    static bool decode(const uint8_t *data, Command &value, AbiError &error) { return decodeCommand(data, value, error); }
    static bool encode(const Command &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeCommand(value); return true; }
};

template <> struct TransportRecordTraits<CommandWait>
{
    static constexpr TransportSectionDescriptor section = kCommandWaitTransportSection;
    static constexpr uint32_t record_bytes = kCommandWaitsBytes;
    static bool decode(const uint8_t *data, CommandWait &value, AbiError &error) { return decodeCommandWait(data, value, error); }
    static bool encode(const CommandWait &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeCommandWait(value); return true; }
};

template <> struct TransportRecordTraits<CommandOperand>
{
    static constexpr TransportSectionDescriptor section = kCommandOperandTransportSection;
    static constexpr uint32_t record_bytes = kCommandOperandsBytes;
    static bool decode(const uint8_t *data, CommandOperand &value, AbiError &error) { return decodeCommandOperand(data, value, error); }
    static bool encode(const CommandOperand &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeCommandOperand(value); return true; }
};

template <> struct TransportRecordTraits<Event>
{
    static constexpr TransportSectionDescriptor section = kEventTransportSection;
    static constexpr uint32_t record_bytes = kEventsBytes;
    static bool decode(const uint8_t *data, Event &value, AbiError &error) { return decodeEvent(data, value, error); }
    static bool encode(const Event &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeEvent(value); return true; }
};

template <> struct TransportRecordTraits<DmaDescriptor>
{
    static constexpr TransportSectionDescriptor section = kDmaDescriptorTransportSection;
    static constexpr uint32_t record_bytes = kDmaDescriptorsBytes;
    static bool decode(const uint8_t *data, DmaDescriptor &value, AbiError &error) { return decodeDmaDescriptor(data, value, error); }
    static bool encode(const DmaDescriptor &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeDmaDescriptor(value); return true; }
};

template <> struct TransportRecordTraits<TypedOpAttr>
{
    static constexpr TransportSectionDescriptor section = kOpAttrTransportSection;
    static constexpr uint32_t record_bytes = kOpAttrsBytes;
    static bool decode(const uint8_t *data, TypedOpAttr &value, AbiError &error) { return decodeTypedOpAttr(data, value, error); }
    static bool encode(const TypedOpAttr &value, std::array<uint8_t, record_bytes> &data, AbiError &error) { return encodeTypedOpAttr(value, data, error); }
};

template <> struct TransportRecordTraits<Relocation>
{
    static constexpr TransportSectionDescriptor section = kRelocationTransportSection;
    static constexpr uint32_t record_bytes = kRelocationsBytes;
    static bool decode(const uint8_t *data, Relocation &value, AbiError &error) { return decodeRelocation(data, value, error); }
    static bool encode(const Relocation &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeRelocation(value); return true; }
};

template <> struct TransportRecordTraits<ExpectedTraffic>
{
    static constexpr TransportSectionDescriptor section = kExpectedTrafficTransportSection;
    static constexpr uint32_t record_bytes = kExpectedTrafficBytes;
    static bool decode(const uint8_t *data, ExpectedTraffic &value, AbiError &error) { return decodeExpectedTraffic(data, value, error); }
    static bool encode(const ExpectedTraffic &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeExpectedTraffic(value); return true; }
};

template <> struct TransportRecordTraits<SourceMap>
{
    static constexpr TransportSectionDescriptor section = kSourceMapTransportSection;
    static constexpr uint32_t record_bytes = kSourceMapBytes;
    static bool decode(const uint8_t *data, SourceMap &value, AbiError &error) { return decodeSourceMap(data, value, error); }
    static bool encode(const SourceMap &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeSourceMap(value); return true; }
};

template <> struct TransportRecordTraits<ProfileHint>
{
    static constexpr TransportSectionDescriptor section = kProfileHintTransportSection;
    static constexpr uint32_t record_bytes = kProfileHintsBytes;
    static bool decode(const uint8_t *data, ProfileHint &value, AbiError &error) { return decodeProfileHint(data, value, error); }
    static bool encode(const ProfileHint &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeProfileHint(value); return true; }
};

template <> struct TransportRecordTraits<ContentDigest>
{
    static constexpr TransportSectionDescriptor section = kContentDigestTransportSection;
    static constexpr uint32_t record_bytes = kContentDigestsBytes;
    static bool decode(const uint8_t *data, ContentDigest &value, AbiError &error) { return decodeContentDigest(data, value, error); }
    static bool encode(const ContentDigest &value, std::array<uint8_t, record_bytes> &data, AbiError &) { data = encodeContentDigest(value); return true; }
};

struct TransportTables
{
    std::vector<std::string> strings;
    std::vector<Entrypoint> entrypoints;
    std::vector<Profile> profiles;
    std::vector<Tensor> tensors;
    std::vector<Shard> shards;
    std::vector<Allocation> allocations;
    std::vector<Stream> streams;
    std::vector<Command> commands;
    std::vector<CommandWait> command_waits;
    std::vector<CommandOperand> command_operands;
    std::vector<Event> events;
    std::vector<DmaDescriptor> dma_descriptors;
    std::vector<TypedOpAttr> op_attrs;
    std::vector<Relocation> relocations;
    std::vector<ExpectedTraffic> expected_traffic;
    std::vector<SourceMap> source_map;
    std::vector<ProfileHint> profile_hints;
    std::vector<ContentDigest> content_digests;
};

template <typename Visitor> bool visitTransportTablesWire(TransportTables &tables, Visitor &&visitor)
{
    if (!visitor(kStringTransportSection, tables.strings)) return false;
    if (!visitor(kEntrypointTransportSection, tables.entrypoints)) return false;
    if (!visitor(kProfileTransportSection, tables.profiles)) return false;
    if (!visitor(kTensorTransportSection, tables.tensors)) return false;
    if (!visitor(kShardTransportSection, tables.shards)) return false;
    if (!visitor(kAllocationTransportSection, tables.allocations)) return false;
    if (!visitor(kStreamTransportSection, tables.streams)) return false;
    if (!visitor(kCommandTransportSection, tables.commands)) return false;
    if (!visitor(kCommandWaitTransportSection, tables.command_waits)) return false;
    if (!visitor(kCommandOperandTransportSection, tables.command_operands)) return false;
    if (!visitor(kEventTransportSection, tables.events)) return false;
    if (!visitor(kDmaDescriptorTransportSection, tables.dma_descriptors)) return false;
    if (!visitor(kOpAttrTransportSection, tables.op_attrs)) return false;
    if (!visitor(kRelocationTransportSection, tables.relocations)) return false;
    if (!visitor(kExpectedTrafficTransportSection, tables.expected_traffic)) return false;
    if (!visitor(kSourceMapTransportSection, tables.source_map)) return false;
    if (!visitor(kProfileHintTransportSection, tables.profile_hints)) return false;
    if (!visitor(kContentDigestTransportSection, tables.content_digests)) return false;
    return true;
}

template <typename Visitor> bool visitTransportTablesCanonical(TransportTables &tables, CanonicalProjection projection, Visitor &&visitor)
{
    if (!visitor(kAllocationTransportSection, tables.allocations)) return false;
    if (!visitor(kCommandTransportSection, tables.commands)) return false;
    if (!visitor(kCommandOperandTransportSection, tables.command_operands)) return false;
    if (!visitor(kCommandWaitTransportSection, tables.command_waits)) return false;
    if (!tables.content_digests.empty() && !visitor(kContentDigestTransportSection, tables.content_digests)) return false;
    if (!visitor(kDmaDescriptorTransportSection, tables.dma_descriptors)) return false;
    if (!visitor(kEntrypointTransportSection, tables.entrypoints)) return false;
    if (!visitor(kEventTransportSection, tables.events)) return false;
    if (!visitor(kExpectedTrafficTransportSection, tables.expected_traffic)) return false;
    if (!visitor(kOpAttrTransportSection, tables.op_attrs)) return false;
    if (!visitor(kProfileTransportSection, tables.profiles)) return false;
    if (projection == CanonicalProjection::Full && !tables.profile_hints.empty() && !visitor(kProfileHintTransportSection, tables.profile_hints)) return false;
    if (!visitor(kRelocationTransportSection, tables.relocations)) return false;
    if (!visitor(kShardTransportSection, tables.shards)) return false;
    if (projection == CanonicalProjection::Full && !tables.source_map.empty() && !visitor(kSourceMapTransportSection, tables.source_map)) return false;
    if (!visitor(kStreamTransportSection, tables.streams)) return false;
    if (!visitor(kStringTransportSection, tables.strings)) return false;
    if (!visitor(kTensorTransportSection, tables.tensors)) return false;
    return true;
}

template <typename Visitor> bool dispatchTransportTable(uint16_t section_type, TransportTables &tables, Visitor &&visitor)
{
    switch (section_type) {
    case kSectionTypeSTRINGS: return visitor(kStringTransportSection, tables.strings);
    case kSectionTypeENTRYPOINTS: return visitor(kEntrypointTransportSection, tables.entrypoints);
    case kSectionTypePROFILES: return visitor(kProfileTransportSection, tables.profiles);
    case kSectionTypeTENSORS: return visitor(kTensorTransportSection, tables.tensors);
    case kSectionTypeSHARDS: return visitor(kShardTransportSection, tables.shards);
    case kSectionTypeALLOCATIONS: return visitor(kAllocationTransportSection, tables.allocations);
    case kSectionTypeSTREAMS: return visitor(kStreamTransportSection, tables.streams);
    case kSectionTypeCOMMANDS: return visitor(kCommandTransportSection, tables.commands);
    case kSectionTypeCOMMAND_WAITS: return visitor(kCommandWaitTransportSection, tables.command_waits);
    case kSectionTypeCOMMAND_OPERANDS: return visitor(kCommandOperandTransportSection, tables.command_operands);
    case kSectionTypeEVENTS: return visitor(kEventTransportSection, tables.events);
    case kSectionTypeDMA_DESCRIPTORS: return visitor(kDmaDescriptorTransportSection, tables.dma_descriptors);
    case kSectionTypeOP_ATTRS: return visitor(kOpAttrTransportSection, tables.op_attrs);
    case kSectionTypeRELOCATIONS: return visitor(kRelocationTransportSection, tables.relocations);
    case kSectionTypeEXPECTED_TRAFFIC: return visitor(kExpectedTrafficTransportSection, tables.expected_traffic);
    case kSectionTypeSOURCE_MAP: return visitor(kSourceMapTransportSection, tables.source_map);
    case kSectionTypePROFILE_HINTS: return visitor(kProfileHintTransportSection, tables.profile_hints);
    case kSectionTypeCONTENT_DIGESTS: return visitor(kContentDigestTransportSection, tables.content_digests);
    default: return false;
    }
}

template <typename Visitor> bool visitTransportTablesWire(const TransportTables &tables, Visitor &&visitor)
{
    if (!visitor(kStringTransportSection, tables.strings)) return false;
    if (!visitor(kEntrypointTransportSection, tables.entrypoints)) return false;
    if (!visitor(kProfileTransportSection, tables.profiles)) return false;
    if (!visitor(kTensorTransportSection, tables.tensors)) return false;
    if (!visitor(kShardTransportSection, tables.shards)) return false;
    if (!visitor(kAllocationTransportSection, tables.allocations)) return false;
    if (!visitor(kStreamTransportSection, tables.streams)) return false;
    if (!visitor(kCommandTransportSection, tables.commands)) return false;
    if (!visitor(kCommandWaitTransportSection, tables.command_waits)) return false;
    if (!visitor(kCommandOperandTransportSection, tables.command_operands)) return false;
    if (!visitor(kEventTransportSection, tables.events)) return false;
    if (!visitor(kDmaDescriptorTransportSection, tables.dma_descriptors)) return false;
    if (!visitor(kOpAttrTransportSection, tables.op_attrs)) return false;
    if (!visitor(kRelocationTransportSection, tables.relocations)) return false;
    if (!visitor(kExpectedTrafficTransportSection, tables.expected_traffic)) return false;
    if (!visitor(kSourceMapTransportSection, tables.source_map)) return false;
    if (!visitor(kProfileHintTransportSection, tables.profile_hints)) return false;
    if (!visitor(kContentDigestTransportSection, tables.content_digests)) return false;
    return true;
}

template <typename Visitor> bool visitTransportTablesCanonical(const TransportTables &tables, CanonicalProjection projection, Visitor &&visitor)
{
    if (!visitor(kAllocationTransportSection, tables.allocations)) return false;
    if (!visitor(kCommandTransportSection, tables.commands)) return false;
    if (!visitor(kCommandOperandTransportSection, tables.command_operands)) return false;
    if (!visitor(kCommandWaitTransportSection, tables.command_waits)) return false;
    if (!tables.content_digests.empty() && !visitor(kContentDigestTransportSection, tables.content_digests)) return false;
    if (!visitor(kDmaDescriptorTransportSection, tables.dma_descriptors)) return false;
    if (!visitor(kEntrypointTransportSection, tables.entrypoints)) return false;
    if (!visitor(kEventTransportSection, tables.events)) return false;
    if (!visitor(kExpectedTrafficTransportSection, tables.expected_traffic)) return false;
    if (!visitor(kOpAttrTransportSection, tables.op_attrs)) return false;
    if (!visitor(kProfileTransportSection, tables.profiles)) return false;
    if (projection == CanonicalProjection::Full && !tables.profile_hints.empty() && !visitor(kProfileHintTransportSection, tables.profile_hints)) return false;
    if (!visitor(kRelocationTransportSection, tables.relocations)) return false;
    if (!visitor(kShardTransportSection, tables.shards)) return false;
    if (projection == CanonicalProjection::Full && !tables.source_map.empty() && !visitor(kSourceMapTransportSection, tables.source_map)) return false;
    if (!visitor(kStreamTransportSection, tables.streams)) return false;
    if (!visitor(kStringTransportSection, tables.strings)) return false;
    if (!visitor(kTensorTransportSection, tables.tensors)) return false;
    return true;
}

template <typename Visitor> bool dispatchTransportTable(uint16_t section_type, const TransportTables &tables, Visitor &&visitor)
{
    switch (section_type) {
    case kSectionTypeSTRINGS: return visitor(kStringTransportSection, tables.strings);
    case kSectionTypeENTRYPOINTS: return visitor(kEntrypointTransportSection, tables.entrypoints);
    case kSectionTypePROFILES: return visitor(kProfileTransportSection, tables.profiles);
    case kSectionTypeTENSORS: return visitor(kTensorTransportSection, tables.tensors);
    case kSectionTypeSHARDS: return visitor(kShardTransportSection, tables.shards);
    case kSectionTypeALLOCATIONS: return visitor(kAllocationTransportSection, tables.allocations);
    case kSectionTypeSTREAMS: return visitor(kStreamTransportSection, tables.streams);
    case kSectionTypeCOMMANDS: return visitor(kCommandTransportSection, tables.commands);
    case kSectionTypeCOMMAND_WAITS: return visitor(kCommandWaitTransportSection, tables.command_waits);
    case kSectionTypeCOMMAND_OPERANDS: return visitor(kCommandOperandTransportSection, tables.command_operands);
    case kSectionTypeEVENTS: return visitor(kEventTransportSection, tables.events);
    case kSectionTypeDMA_DESCRIPTORS: return visitor(kDmaDescriptorTransportSection, tables.dma_descriptors);
    case kSectionTypeOP_ATTRS: return visitor(kOpAttrTransportSection, tables.op_attrs);
    case kSectionTypeRELOCATIONS: return visitor(kRelocationTransportSection, tables.relocations);
    case kSectionTypeEXPECTED_TRAFFIC: return visitor(kExpectedTrafficTransportSection, tables.expected_traffic);
    case kSectionTypeSOURCE_MAP: return visitor(kSourceMapTransportSection, tables.source_map);
    case kSectionTypePROFILE_HINTS: return visitor(kProfileHintTransportSection, tables.profile_hints);
    case kSectionTypeCONTENT_DIGESTS: return visitor(kContentDigestTransportSection, tables.content_digests);
    default: return false;
    }
}

#endif

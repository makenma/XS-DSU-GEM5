#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_SCHEDULED_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_CODECS_SCHEDULED_HH

inline bool decodeAuthoredProgramOrigin(const uint8_t *data, AuthoredProgramOrigin &out, AbiError &error)
{
    out = AuthoredProgramOrigin{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "AuthoredProgramOrigin presence mask has undeclared bits"}; return false; }
    if (!decodeStringRef(data + 8, out.namespace_, error)) return false;
    if (!decodeStringRef(data + 16, out.name, error)) return false;
    out.version = mesh_abi::rdU64(data + 24);
    return true;
}

inline std::array<uint8_t, kAuthoredProgramOriginBytes> encodeAuthoredProgramOrigin(const AuthoredProgramOrigin &value)
{
    std::array<uint8_t, kAuthoredProgramOriginBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeStringRef(data.data() + 8, value.namespace_);
    encodeStringRef(data.data() + 16, value.name);
    mesh_abi::wrU64(data.data() + 24, value.version);
    return data;
}

inline bool decodeAuthoredVariantLineage(const uint8_t *data, AuthoredVariantLineage &out, AbiError &error)
{
    out = AuthoredVariantLineage{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "AuthoredVariantLineage presence mask has undeclared bits"}; return false; }
    if (!decodeStringRef(data + 8, out.authoring_variant_id, error)) return false;
    return true;
}

inline std::array<uint8_t, kAuthoredVariantLineageBytes> encodeAuthoredVariantLineage(const AuthoredVariantLineage &value)
{
    std::array<uint8_t, kAuthoredVariantLineageBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeStringRef(data.data() + 8, value.authoring_variant_id);
    return data;
}

inline bool decodeAxiFenceAttrs(const uint8_t *data, AxiFenceAttrs &out, AbiError &error)
{
    out = AxiFenceAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "AxiFenceAttrs presence mask has undeclared bits"}; return false; }
    if (mesh_abi::rdU32(data + 8 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "AxiFenceAttrs.scope reserved bytes are nonzero"}; return false; }
    if (!validFenceScope(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "AxiFenceAttrs.scope is invalid"}; return false; }
    out.scope = static_cast<FenceScope>(mesh_abi::rdU32(data + 8 + kEnumValueValueOffset));
    return true;
}

inline std::array<uint8_t, kAxiFenceAttrsBytes> encodeAxiFenceAttrs(const AxiFenceAttrs &value)
{
    std::array<uint8_t, kAxiFenceAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU32(data.data() + 8 + kEnumValueValueOffset, static_cast<uint32_t>(value.scope));
    return data;
}

inline bool decodeBarrierArrival(const uint8_t *data, BarrierArrival &out, AbiError &error)
{
    out = BarrierArrival{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BarrierArrival presence mask has undeclared bits"}; return false; }
    out.participant_core = mesh_abi::rdU64(data + 8);
    out.kernel_op_id = mesh_abi::rdU64(data + 16);
    out.command_id = mesh_abi::rdU64(data + 24);
    out.done_token_id = mesh_abi::rdU64(data + 32);
    return true;
}

inline std::array<uint8_t, kBarrierArrivalBytes> encodeBarrierArrival(const BarrierArrival &value)
{
    std::array<uint8_t, kBarrierArrivalBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.participant_core);
    mesh_abi::wrU64(data.data() + 16, value.kernel_op_id);
    mesh_abi::wrU64(data.data() + 24, value.command_id);
    mesh_abi::wrU64(data.data() + 32, value.done_token_id);
    return data;
}

inline bool decodeBarrierExecution(const uint8_t *data, BarrierExecution &out, AbiError &error)
{
    out = BarrierExecution{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BarrierExecution presence mask has undeclared bits"}; return false; }
    out.barrier_group_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kBarrierExecutionBytes> encodeBarrierExecution(const BarrierExecution &value)
{
    std::array<uint8_t, kBarrierExecutionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.barrier_group_id);
    return data;
}

inline bool decodeBarrierGroup(const uint8_t *data, BarrierGroup &out, AbiError &error)
{
    out = BarrierGroup{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "BarrierGroup presence mask has undeclared bits"}; return false; }
    out.barrier_group_id = mesh_abi::rdU64(data + 8);
    if (!decodeListSpan(data + 16, out.participants, error)) return false;
    if (!decodeListSpan(data + 24, out.arrivals, error)) return false;
    out.completion_event_id = mesh_abi::rdU64(data + 32);
    return true;
}

inline std::array<uint8_t, kBarrierGroupBytes> encodeBarrierGroup(const BarrierGroup &value)
{
    std::array<uint8_t, kBarrierGroupBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.barrier_group_id);
    encodeListSpan(data.data() + 16, value.participants);
    encodeListSpan(data.data() + 24, value.arrivals);
    mesh_abi::wrU64(data.data() + 32, value.completion_event_id);
    return data;
}

inline bool decodeCommandSemantics(const uint8_t *data, CommandSemantics &out, AbiError &error)
{
    out = CommandSemantics{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CommandSemantics presence mask has undeclared bits"}; return false; }
    out.command_id = mesh_abi::rdU64(data + 8);
    if (!decodeSemanticRef(data + 16, out.source, error)) return false;
    if (out.source.row_id == 0 || (out.source.section_type != 324 && out.source.section_type != 313)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "CommandSemantics.source target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.execution, error)) return false;
    if (out.execution.row_id == 0 || (out.execution.section_type != 312 && out.execution.section_type != 318 && out.execution.section_type != 333 && out.execution.section_type != 307 && out.execution.section_type != 314)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "CommandSemantics.execution target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kCommandSemanticsBytes> encodeCommandSemantics(const CommandSemantics &value)
{
    std::array<uint8_t, kCommandSemanticsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.command_id);
    encodeSemanticRef(data.data() + 16, value.source);
    encodeSemanticRef(data.data() + 24, value.execution);
    return data;
}

inline bool decodeCompiledProgramOrigin(const uint8_t *data, CompiledProgramOrigin &out, AbiError &error)
{
    out = CompiledProgramOrigin{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CompiledProgramOrigin presence mask has undeclared bits"}; return false; }
    if (!decodeStringRef(data + 8, out.kernel_bundle_semantic_sha256, error)) return false;
    return true;
}

inline std::array<uint8_t, kCompiledProgramOriginBytes> encodeCompiledProgramOrigin(const CompiledProgramOrigin &value)
{
    std::array<uint8_t, kCompiledProgramOriginBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeStringRef(data.data() + 8, value.kernel_bundle_semantic_sha256);
    return data;
}

inline bool decodeCompiledVariantLineage(const uint8_t *data, CompiledVariantLineage &out, AbiError &error)
{
    out = CompiledVariantLineage{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "CompiledVariantLineage presence mask has undeclared bits"}; return false; }
    out.kernel_module_ordinal = mesh_abi::rdU64(data + 8);
    if (!decodeStringRef(data + 16, out.kernel_module_semantic_sha256, error)) return false;
    return true;
}

inline std::array<uint8_t, kCompiledVariantLineageBytes> encodeCompiledVariantLineage(const CompiledVariantLineage &value)
{
    std::array<uint8_t, kCompiledVariantLineageBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.kernel_module_ordinal);
    encodeStringRef(data.data() + 16, value.kernel_module_semantic_sha256);
    return data;
}

inline bool decodeComputeExecution(const uint8_t *data, ComputeExecution &out, AbiError &error)
{
    out = ComputeExecution{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ComputeExecution presence mask has undeclared bits"}; return false; }
    if (!decodeListSpan(data + 8, out.phases, error)) return false;
    return true;
}

inline std::array<uint8_t, kComputeExecutionBytes> encodeComputeExecution(const ComputeExecution &value)
{
    std::array<uint8_t, kComputeExecutionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeListSpan(data.data() + 8, value.phases);
    return data;
}

inline bool decodeControlCommandSource(const uint8_t *data, ControlCommandSource &out, AbiError &error)
{
    out = ControlCommandSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ControlCommandSource presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.attrs, error)) return false;
    if (out.attrs.row_id == 0 || (out.attrs.section_type != 335 && out.attrs.section_type != 336 && out.attrs.section_type != 322 && out.attrs.section_type != 320 && out.attrs.section_type != 319 && out.attrs.section_type != 334 && out.attrs.section_type != 305)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ControlCommandSource.attrs target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kControlCommandSourceBytes> encodeControlCommandSource(const ControlCommandSource &value)
{
    std::array<uint8_t, kControlCommandSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.attrs);
    return data;
}

inline bool decodeControlExecution(const uint8_t *data, ControlExecution &out, AbiError &error)
{
    out = ControlExecution{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ControlExecution presence mask has undeclared bits"}; return false; }
    return true;
}

inline std::array<uint8_t, kControlExecutionBytes> encodeControlExecution(const ControlExecution &value)
{
    std::array<uint8_t, kControlExecutionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    return data;
}

inline bool decodeDescriptorEndpointUse(const uint8_t *data, DescriptorEndpointUse &out, AbiError &error)
{
    out = DescriptorEndpointUse{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorEndpointUse presence mask has undeclared bits"}; return false; }
    out.ref_id = mesh_abi::rdU64(data + 8);
    out.descriptor_id = mesh_abi::rdU64(data + 16);
    if (mesh_abi::rdU32(data + 24 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorEndpointUse.side reserved bytes are nonzero"}; return false; }
    if (!validEndpointSide(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "DescriptorEndpointUse.side is invalid"}; return false; }
    out.side = static_cast<EndpointSide>(mesh_abi::rdU32(data + 24 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 32, out.use, error)) return false;
    if (out.use.row_id == 0 || (out.use.section_type != 332 && out.use.section_type != 343)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "DescriptorEndpointUse.use target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kDescriptorEndpointUseBytes> encodeDescriptorEndpointUse(const DescriptorEndpointUse &value)
{
    std::array<uint8_t, kDescriptorEndpointUseBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.ref_id);
    mesh_abi::wrU64(data.data() + 16, value.descriptor_id);
    mesh_abi::wrU32(data.data() + 24 + kEnumValueValueOffset, static_cast<uint32_t>(value.side));
    encodeSemanticRef(data.data() + 32, value.use);
    return data;
}

inline bool decodeDescriptorGroup(const uint8_t *data, DescriptorGroup &out, AbiError &error)
{
    out = DescriptorGroup{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorGroup presence mask has undeclared bits"}; return false; }
    out.group_id = mesh_abi::rdU64(data + 8);
    out.command_id = mesh_abi::rdU64(data + 16);
    out.kernel_op_id = mesh_abi::rdU64(data + 24);
    if (!decodeListSpan(data + 32, out.descriptor_ids, error)) return false;
    out.completion_event_id = mesh_abi::rdU64(data + 40);
    return true;
}

inline std::array<uint8_t, kDescriptorGroupBytes> encodeDescriptorGroup(const DescriptorGroup &value)
{
    std::array<uint8_t, kDescriptorGroupBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.group_id);
    mesh_abi::wrU64(data.data() + 16, value.command_id);
    mesh_abi::wrU64(data.data() + 24, value.kernel_op_id);
    encodeListSpan(data.data() + 32, value.descriptor_ids);
    mesh_abi::wrU64(data.data() + 40, value.completion_event_id);
    return data;
}

inline bool decodeDescriptorSource(const uint8_t *data, DescriptorSource &out, AbiError &error)
{
    out = DescriptorSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DescriptorSource presence mask has undeclared bits"}; return false; }
    out.descriptor_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kDescriptorSourceBytes> encodeDescriptorSource(const DescriptorSource &value)
{
    std::array<uint8_t, kDescriptorSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.descriptor_id);
    return data;
}

inline bool decodeDmaExecution(const uint8_t *data, DmaExecution &out, AbiError &error)
{
    out = DmaExecution{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "DmaExecution presence mask has undeclared bits"}; return false; }
    out.descriptor_group_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kDmaExecutionBytes> encodeDmaExecution(const DmaExecution &value)
{
    std::array<uint8_t, kDmaExecutionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.descriptor_group_id);
    return data;
}

inline bool decodeEventSignalAttrs(const uint8_t *data, EventSignalAttrs &out, AbiError &error)
{
    out = EventSignalAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EventSignalAttrs presence mask has undeclared bits"}; return false; }
    out.event_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kEventSignalAttrsBytes> encodeEventSignalAttrs(const EventSignalAttrs &value)
{
    std::array<uint8_t, kEventSignalAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.event_id);
    return data;
}

inline bool decodeEventWaitAttrs(const uint8_t *data, EventWaitAttrs &out, AbiError &error)
{
    out = EventWaitAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "EventWaitAttrs presence mask has undeclared bits"}; return false; }
    out.event_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kEventWaitAttrsBytes> encodeEventWaitAttrs(const EventWaitAttrs &value)
{
    std::array<uint8_t, kEventWaitAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.event_id);
    return data;
}

inline bool decodeExternalSlotBacking(const uint8_t *data, ExternalSlotBacking &out, AbiError &error)
{
    out = ExternalSlotBacking{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ExternalSlotBacking presence mask has undeclared bits"}; return false; }
    out.slot_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kExternalSlotBackingBytes> encodeExternalSlotBacking(const ExternalSlotBacking &value)
{
    std::array<uint8_t, kExternalSlotBackingBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.slot_id);
    return data;
}

inline bool decodeHaltAttrs(const uint8_t *data, HaltAttrs &out, AbiError &error)
{
    out = HaltAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "HaltAttrs presence mask has undeclared bits"}; return false; }
    return true;
}

inline std::array<uint8_t, kHaltAttrsBytes> encodeHaltAttrs(const HaltAttrs &value)
{
    std::array<uint8_t, kHaltAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    return data;
}

inline bool decodeIdSpan(const uint8_t *data, IdSpan &out, AbiError &error)
{
    out = IdSpan{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "IdSpan presence mask has undeclared bits"}; return false; }
    out.first_id = mesh_abi::rdU64(data + 8);
    out.count = mesh_abi::rdU64(data + 16);
    return true;
}

inline std::array<uint8_t, kIdSpanBytes> encodeIdSpan(const IdSpan &value)
{
    std::array<uint8_t, kIdSpanBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.first_id);
    mesh_abi::wrU64(data.data() + 16, value.count);
    return data;
}

inline bool decodeKernelCommandSource(const uint8_t *data, KernelCommandSource &out, AbiError &error)
{
    out = KernelCommandSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelCommandSource presence mask has undeclared bits"}; return false; }
    out.kernel_op_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kKernelCommandSourceBytes> encodeKernelCommandSource(const KernelCommandSource &value)
{
    std::array<uint8_t, kKernelCommandSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.kernel_op_id);
    return data;
}

inline bool decodeKernelTokenSource(const uint8_t *data, KernelTokenSource &out, AbiError &error)
{
    out = KernelTokenSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "KernelTokenSource presence mask has undeclared bits"}; return false; }
    out.token_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kKernelTokenSourceBytes> encodeKernelTokenSource(const KernelTokenSource &value)
{
    std::array<uint8_t, kKernelTokenSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.token_id);
    return data;
}

inline bool decodeLifecycleSource(const uint8_t *data, LifecycleSource &out, AbiError &error)
{
    out = LifecycleSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "LifecycleSource presence mask has undeclared bits"}; return false; }
    out.variant_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kLifecycleSourceBytes> encodeLifecycleSource(const LifecycleSource &value)
{
    std::array<uint8_t, kLifecycleSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.variant_id);
    return data;
}

inline bool decodeLocalAllocationBacking(const uint8_t *data, LocalAllocationBacking &out, AbiError &error)
{
    out = LocalAllocationBacking{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "LocalAllocationBacking presence mask has undeclared bits"}; return false; }
    out.allocation_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kLocalAllocationBackingBytes> encodeLocalAllocationBacking(const LocalAllocationBacking &value)
{
    std::array<uint8_t, kLocalAllocationBackingBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.allocation_id);
    return data;
}

inline bool decodeObjectBacking(const uint8_t *data, ObjectBacking &out, AbiError &error)
{
    out = ObjectBacking{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ObjectBacking presence mask has undeclared bits"}; return false; }
    out.object_id = mesh_abi::rdU64(data + 8);
    if (!decodeSemanticRef(data + 16, out.backing, error)) return false;
    if (out.backing.row_id == 0 || (out.backing.section_type != 327 && out.backing.section_type != 321)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ObjectBacking.backing target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kObjectBackingBytes> encodeObjectBacking(const ObjectBacking &value)
{
    std::array<uint8_t, kObjectBackingBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.object_id);
    encodeSemanticRef(data.data() + 16, value.backing);
    return data;
}

inline bool decodeObjectSource(const uint8_t *data, ObjectSource &out, AbiError &error)
{
    out = ObjectSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ObjectSource presence mask has undeclared bits"}; return false; }
    out.object_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kObjectSourceBytes> encodeObjectSource(const ObjectSource &value)
{
    std::array<uint8_t, kObjectSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.object_id);
    return data;
}

inline bool decodeProgramSemantics(const uint8_t *data, ProgramSemantics &out, AbiError &error)
{
    out = ProgramSemantics{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ProgramSemantics presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.origin, error)) return false;
    if (out.origin.row_id == 0 || (out.origin.section_type != 310 && out.origin.section_type != 303)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ProgramSemantics.origin target is invalid"}; return false; }
    if (!decodeListSpan(data + 16, out.variants, error)) return false;
    if (!decodeListSpan(data + 24, out.kernel_tensors, error)) return false;
    if (!decodeListSpan(data + 32, out.computations, error)) return false;
    if (!decodeListSpan(data + 40, out.placements, error)) return false;
    if (!decodeListSpan(data + 48, out.logical_shards, error)) return false;
    if (!decodeListSpan(data + 56, out.partial_sums, error)) return false;
    if (!decodeListSpan(data + 64, out.objects, error)) return false;
    if (!decodeListSpan(data + 72, out.views, error)) return false;
    if (!decodeListSpan(data + 80, out.states, error)) return false;
    if (!decodeListSpan(data + 88, out.tokens, error)) return false;
    if (!decodeListSpan(data + 96, out.kernel_ops, error)) return false;
    if (!decodeListSpan(data + 104, out.object_backings, error)) return false;
    if (!decodeListSpan(data + 112, out.resident_views, error)) return false;
    if (!decodeListSpan(data + 120, out.command_semantics, error)) return false;
    if (!decodeListSpan(data + 128, out.barrier_groups, error)) return false;
    if (!decodeListSpan(data + 136, out.dependencies, error)) return false;
    if (!decodeListSpan(data + 144, out.streams, error)) return false;
    if (!decodeListSpan(data + 152, out.stream_command_ids, error)) return false;
    if (!decodeListSpan(data + 160, out.descriptor_groups, error)) return false;
    if (!decodeListSpan(data + 168, out.endpoint_uses, error)) return false;
    if (!decodeListSpan(data + 176, out.binding_slots, error)) return false;
    if (!decodeStringRef(data + 184, out.reference_binding_identity_sha256, error)) return false;
    if (!decodeSemanticRef(data + 192, out.intrinsic_traffic, error)) return false;
    if (out.intrinsic_traffic.row_id == 0 || (out.intrinsic_traffic.section_type != 352)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ProgramSemantics.intrinsic_traffic target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kProgramSemanticsBytes> encodeProgramSemantics(const ProgramSemantics &value)
{
    std::array<uint8_t, kProgramSemanticsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.origin);
    encodeListSpan(data.data() + 16, value.variants);
    encodeListSpan(data.data() + 24, value.kernel_tensors);
    encodeListSpan(data.data() + 32, value.computations);
    encodeListSpan(data.data() + 40, value.placements);
    encodeListSpan(data.data() + 48, value.logical_shards);
    encodeListSpan(data.data() + 56, value.partial_sums);
    encodeListSpan(data.data() + 64, value.objects);
    encodeListSpan(data.data() + 72, value.views);
    encodeListSpan(data.data() + 80, value.states);
    encodeListSpan(data.data() + 88, value.tokens);
    encodeListSpan(data.data() + 96, value.kernel_ops);
    encodeListSpan(data.data() + 104, value.object_backings);
    encodeListSpan(data.data() + 112, value.resident_views);
    encodeListSpan(data.data() + 120, value.command_semantics);
    encodeListSpan(data.data() + 128, value.barrier_groups);
    encodeListSpan(data.data() + 136, value.dependencies);
    encodeListSpan(data.data() + 144, value.streams);
    encodeListSpan(data.data() + 152, value.stream_command_ids);
    encodeListSpan(data.data() + 160, value.descriptor_groups);
    encodeListSpan(data.data() + 168, value.endpoint_uses);
    encodeListSpan(data.data() + 176, value.binding_slots);
    encodeStringRef(data.data() + 184, value.reference_binding_identity_sha256);
    encodeSemanticRef(data.data() + 192, value.intrinsic_traffic);
    return data;
}

inline bool decodeProgramVariant(const uint8_t *data, ProgramVariant &out, AbiError &error)
{
    out = ProgramVariant{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ProgramVariant presence mask has undeclared bits"}; return false; }
    out.variant_id = mesh_abi::rdU64(data + 8);
    out.entrypoint_id = mesh_abi::rdU64(data + 16);
    out.profile_id = mesh_abi::rdU64(data + 24);
    if (!decodeSemanticRef(data + 32, out.lineage, error)) return false;
    if (out.lineage.row_id == 0 || (out.lineage.section_type != 311 && out.lineage.section_type != 304)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ProgramVariant.lineage target is invalid"}; return false; }
    out.lifecycle_stream_id = mesh_abi::rdU64(data + 40);
    if (!decodeSemanticRef(data + 48, out.membership, error)) return false;
    if (out.membership.row_id == 0 || (out.membership.section_type != 342)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ProgramVariant.membership target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kProgramVariantBytes> encodeProgramVariant(const ProgramVariant &value)
{
    std::array<uint8_t, kProgramVariantBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.variant_id);
    mesh_abi::wrU64(data.data() + 16, value.entrypoint_id);
    mesh_abi::wrU64(data.data() + 24, value.profile_id);
    encodeSemanticRef(data.data() + 32, value.lineage);
    mesh_abi::wrU64(data.data() + 40, value.lifecycle_stream_id);
    encodeSemanticRef(data.data() + 48, value.membership);
    return data;
}

inline bool decodeReadAccessUse(const uint8_t *data, ReadAccessUse &out, AbiError &error)
{
    out = ReadAccessUse{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ReadAccessUse presence mask has undeclared bits"}; return false; }
    out.kernel_op_id = mesh_abi::rdU64(data + 8);
    out.access_index = mesh_abi::rdU64(data + 16);
    if (!decodeSemanticRef(data + 24, out.region, error)) return false;
    if (out.region.row_id == 0 || (out.region.section_type != 280)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ReadAccessUse.region target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kReadAccessUseBytes> encodeReadAccessUse(const ReadAccessUse &value)
{
    std::array<uint8_t, kReadAccessUseBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.kernel_op_id);
    mesh_abi::wrU64(data.data() + 16, value.access_index);
    encodeSemanticRef(data.data() + 24, value.region);
    return data;
}

inline bool decodeRecvWaitExecution(const uint8_t *data, RecvWaitExecution &out, AbiError &error)
{
    out = RecvWaitExecution{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RecvWaitExecution presence mask has undeclared bits"}; return false; }
    out.transfer_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kRecvWaitExecutionBytes> encodeRecvWaitExecution(const RecvWaitExecution &value)
{
    std::array<uint8_t, kRecvWaitExecutionBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.transfer_id);
    return data;
}

inline bool decodeRepeatCommandAttrs(const uint8_t *data, RepeatCommandAttrs &out, AbiError &error)
{
    out = RepeatCommandAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RepeatCommandAttrs presence mask has undeclared bits"}; return false; }
    out.subrange_begin_stream_ordinal = mesh_abi::rdU64(data + 8);
    out.subrange_command_count = mesh_abi::rdU64(data + 16);
    out.repeat_count = mesh_abi::rdU64(data + 24);
    return true;
}

inline std::array<uint8_t, kRepeatCommandAttrsBytes> encodeRepeatCommandAttrs(const RepeatCommandAttrs &value)
{
    std::array<uint8_t, kRepeatCommandAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.subrange_begin_stream_ordinal);
    mesh_abi::wrU64(data.data() + 16, value.subrange_command_count);
    mesh_abi::wrU64(data.data() + 24, value.repeat_count);
    return data;
}

inline bool decodeRequestBeginAttrs(const uint8_t *data, RequestBeginAttrs &out, AbiError &error)
{
    out = RequestBeginAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RequestBeginAttrs presence mask has undeclared bits"}; return false; }
    return true;
}

inline std::array<uint8_t, kRequestBeginAttrsBytes> encodeRequestBeginAttrs(const RequestBeginAttrs &value)
{
    std::array<uint8_t, kRequestBeginAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    return data;
}

inline bool decodeRequestEndAttrs(const uint8_t *data, RequestEndAttrs &out, AbiError &error)
{
    out = RequestEndAttrs{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "RequestEndAttrs presence mask has undeclared bits"}; return false; }
    return true;
}

inline std::array<uint8_t, kRequestEndAttrsBytes> encodeRequestEndAttrs(const RequestEndAttrs &value)
{
    std::array<uint8_t, kRequestEndAttrsBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    return data;
}

inline bool decodeResidentView(const uint8_t *data, ResidentView &out, AbiError &error)
{
    out = ResidentView{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ResidentView presence mask has undeclared bits"}; return false; }
    out.runtime_shard_id = mesh_abi::rdU64(data + 8);
    out.view_id = mesh_abi::rdU64(data + 16);
    return true;
}

inline std::array<uint8_t, kResidentViewBytes> encodeResidentView(const ResidentView &value)
{
    std::array<uint8_t, kResidentViewBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.runtime_shard_id);
    mesh_abi::wrU64(data.data() + 16, value.view_id);
    return data;
}

inline bool decodeScheduledDependency(const uint8_t *data, ScheduledDependency &out, AbiError &error)
{
    out = ScheduledDependency{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ScheduledDependency presence mask has undeclared bits"}; return false; }
    out.dependency_id = mesh_abi::rdU64(data + 8);
    out.source_command_id = mesh_abi::rdU64(data + 16);
    out.target_command_id = mesh_abi::rdU64(data + 24);
    if (mesh_abi::rdU32(data + 32 + kEnumValueReservedOffset) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ScheduledDependency.kind reserved bytes are nonzero"}; return false; }
    if (!validScheduledDependencyKind(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset))) { error = {mesh_diagnostics::E_ABI_ENUM, "ScheduledDependency.kind is invalid"}; return false; }
    out.kind = static_cast<ScheduledDependencyKind>(mesh_abi::rdU32(data + 32 + kEnumValueValueOffset));
    if (!decodeSemanticRef(data + 40, out.source, error)) return false;
    if (out.source.row_id == 0 || (out.source.section_type != 325 && out.source.section_type != 340 && out.source.section_type != 329 && out.source.section_type != 317 && out.source.section_type != 341 && out.source.section_type != 326)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "ScheduledDependency.source target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kScheduledDependencyBytes> encodeScheduledDependency(const ScheduledDependency &value)
{
    std::array<uint8_t, kScheduledDependencyBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.dependency_id);
    mesh_abi::wrU64(data.data() + 16, value.source_command_id);
    mesh_abi::wrU64(data.data() + 24, value.target_command_id);
    mesh_abi::wrU32(data.data() + 32 + kEnumValueValueOffset, static_cast<uint32_t>(value.kind));
    encodeSemanticRef(data.data() + 40, value.source);
    return data;
}

inline bool decodeScheduledStream(const uint8_t *data, ScheduledStream &out, AbiError &error)
{
    out = ScheduledStream{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "ScheduledStream presence mask has undeclared bits"}; return false; }
    out.stream_id = mesh_abi::rdU64(data + 8);
    out.core_id = mesh_abi::rdU64(data + 16);
    out.physical_stream_id = mesh_abi::rdU64(data + 24);
    out.command_begin = mesh_abi::rdU64(data + 32);
    out.command_count = mesh_abi::rdU64(data + 40);
    out.flags = mesh_abi::rdU64(data + 48);
    return true;
}

inline std::array<uint8_t, kScheduledStreamBytes> encodeScheduledStream(const ScheduledStream &value)
{
    std::array<uint8_t, kScheduledStreamBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.stream_id);
    mesh_abi::wrU64(data.data() + 16, value.core_id);
    mesh_abi::wrU64(data.data() + 24, value.physical_stream_id);
    mesh_abi::wrU64(data.data() + 32, value.command_begin);
    mesh_abi::wrU64(data.data() + 40, value.command_count);
    mesh_abi::wrU64(data.data() + 48, value.flags);
    return data;
}

inline bool decodeStateSource(const uint8_t *data, StateSource &out, AbiError &error)
{
    out = StateSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "StateSource presence mask has undeclared bits"}; return false; }
    out.state_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kStateSourceBytes> encodeStateSource(const StateSource &value)
{
    std::array<uint8_t, kStateSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.state_id);
    return data;
}

inline bool decodeStreamOrderSource(const uint8_t *data, StreamOrderSource &out, AbiError &error)
{
    out = StreamOrderSource{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "StreamOrderSource presence mask has undeclared bits"}; return false; }
    out.stream_id = mesh_abi::rdU64(data + 8);
    return true;
}

inline std::array<uint8_t, kStreamOrderSourceBytes> encodeStreamOrderSource(const StreamOrderSource &value)
{
    std::array<uint8_t, kStreamOrderSourceBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.stream_id);
    return data;
}

inline bool decodeVariantMembership(const uint8_t *data, VariantMembership &out, AbiError &error)
{
    out = VariantMembership{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "VariantMembership presence mask has undeclared bits"}; return false; }
    if (!decodeSemanticRef(data + 8, out.abi_tensors, error)) return false;
    if (out.abi_tensors.row_id == 0 || (out.abi_tensors.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.abi_tensors target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 16, out.runtime_shards, error)) return false;
    if (out.runtime_shards.row_id == 0 || (out.runtime_shards.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.runtime_shards target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 24, out.allocations, error)) return false;
    if (out.allocations.row_id == 0 || (out.allocations.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.allocations target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 32, out.streams, error)) return false;
    if (out.streams.row_id == 0 || (out.streams.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.streams target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 40, out.commands, error)) return false;
    if (out.commands.row_id == 0 || (out.commands.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.commands target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 48, out.events, error)) return false;
    if (out.events.row_id == 0 || (out.events.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.events target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 56, out.descriptors, error)) return false;
    if (out.descriptors.row_id == 0 || (out.descriptors.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.descriptors target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 64, out.relocations, error)) return false;
    if (out.relocations.row_id == 0 || (out.relocations.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.relocations target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 72, out.kernel_tensors, error)) return false;
    if (out.kernel_tensors.row_id == 0 || (out.kernel_tensors.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.kernel_tensors target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 80, out.computations, error)) return false;
    if (out.computations.row_id == 0 || (out.computations.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.computations target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 88, out.placements, error)) return false;
    if (out.placements.row_id == 0 || (out.placements.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.placements target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 96, out.logical_shards, error)) return false;
    if (out.logical_shards.row_id == 0 || (out.logical_shards.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.logical_shards target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 104, out.partial_sums, error)) return false;
    if (out.partial_sums.row_id == 0 || (out.partial_sums.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.partial_sums target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 112, out.objects, error)) return false;
    if (out.objects.row_id == 0 || (out.objects.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.objects target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 120, out.views, error)) return false;
    if (out.views.row_id == 0 || (out.views.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.views target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 128, out.states, error)) return false;
    if (out.states.row_id == 0 || (out.states.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.states target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 136, out.tokens, error)) return false;
    if (out.tokens.row_id == 0 || (out.tokens.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.tokens target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 144, out.kernel_ops, error)) return false;
    if (out.kernel_ops.row_id == 0 || (out.kernel_ops.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.kernel_ops target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 152, out.object_backings, error)) return false;
    if (out.object_backings.row_id == 0 || (out.object_backings.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.object_backings target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 160, out.command_semantics, error)) return false;
    if (out.command_semantics.row_id == 0 || (out.command_semantics.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.command_semantics target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 168, out.barrier_groups, error)) return false;
    if (out.barrier_groups.row_id == 0 || (out.barrier_groups.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.barrier_groups target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 176, out.dependencies, error)) return false;
    if (out.dependencies.row_id == 0 || (out.dependencies.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.dependencies target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 184, out.endpoint_uses, error)) return false;
    if (out.endpoint_uses.row_id == 0 || (out.endpoint_uses.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.endpoint_uses target is invalid"}; return false; }
    if (!decodeSemanticRef(data + 192, out.binding_slots, error)) return false;
    if (out.binding_slots.row_id == 0 || (out.binding_slots.section_type != 323)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "VariantMembership.binding_slots target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kVariantMembershipBytes> encodeVariantMembership(const VariantMembership &value)
{
    std::array<uint8_t, kVariantMembershipBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    encodeSemanticRef(data.data() + 8, value.abi_tensors);
    encodeSemanticRef(data.data() + 16, value.runtime_shards);
    encodeSemanticRef(data.data() + 24, value.allocations);
    encodeSemanticRef(data.data() + 32, value.streams);
    encodeSemanticRef(data.data() + 40, value.commands);
    encodeSemanticRef(data.data() + 48, value.events);
    encodeSemanticRef(data.data() + 56, value.descriptors);
    encodeSemanticRef(data.data() + 64, value.relocations);
    encodeSemanticRef(data.data() + 72, value.kernel_tensors);
    encodeSemanticRef(data.data() + 80, value.computations);
    encodeSemanticRef(data.data() + 88, value.placements);
    encodeSemanticRef(data.data() + 96, value.logical_shards);
    encodeSemanticRef(data.data() + 104, value.partial_sums);
    encodeSemanticRef(data.data() + 112, value.objects);
    encodeSemanticRef(data.data() + 120, value.views);
    encodeSemanticRef(data.data() + 128, value.states);
    encodeSemanticRef(data.data() + 136, value.tokens);
    encodeSemanticRef(data.data() + 144, value.kernel_ops);
    encodeSemanticRef(data.data() + 152, value.object_backings);
    encodeSemanticRef(data.data() + 160, value.command_semantics);
    encodeSemanticRef(data.data() + 168, value.barrier_groups);
    encodeSemanticRef(data.data() + 176, value.dependencies);
    encodeSemanticRef(data.data() + 184, value.endpoint_uses);
    encodeSemanticRef(data.data() + 192, value.binding_slots);
    return data;
}

inline bool decodeWriteAccessUse(const uint8_t *data, WriteAccessUse &out, AbiError &error)
{
    out = WriteAccessUse{};
    out.presence_mask = mesh_abi::rdU64(data);
    if ((out.presence_mask & ~uint64_t(0)) != 0) { error = {mesh_diagnostics::E_ABI_RESERVED, "WriteAccessUse presence mask has undeclared bits"}; return false; }
    out.kernel_op_id = mesh_abi::rdU64(data + 8);
    out.access_index = mesh_abi::rdU64(data + 16);
    if (!decodeSemanticRef(data + 24, out.region, error)) return false;
    if (out.region.row_id == 0 || (out.region.section_type != 280)) { error = {mesh_diagnostics::E_ABI_BOUNDS, "WriteAccessUse.region target is invalid"}; return false; }
    return true;
}

inline std::array<uint8_t, kWriteAccessUseBytes> encodeWriteAccessUse(const WriteAccessUse &value)
{
    std::array<uint8_t, kWriteAccessUseBytes> data{};
    mesh_abi::wrU64(data.data(), value.presence_mask);
    mesh_abi::wrU64(data.data() + 8, value.kernel_op_id);
    mesh_abi::wrU64(data.data() + 16, value.access_index);
    encodeSemanticRef(data.data() + 24, value.region);
    return data;
}

#endif

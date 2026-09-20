#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_SCHEDULED_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_SCHEDULED_HH

inline constexpr SemanticFieldDescriptor kAuthoredProgramOriginNamespaceField = {"namespace", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kAuthoredProgramOriginNameField = {"name", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kAuthoredProgramOriginVersionField = {"version", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const AuthoredProgramOrigin &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAuthoredProgramOriginNamespaceField, true, value.namespace_)) return false;
    if (!visitor(kAuthoredProgramOriginNameField, true, value.name)) return false;
    if (!visitor(kAuthoredProgramOriginVersionField, true, value.version)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const AuthoredProgramOrigin &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAuthoredProgramOriginNameField, true, value.name)) return false;
    if (!visitor(kAuthoredProgramOriginNamespaceField, true, value.namespace_)) return false;
    if (!visitor(kAuthoredProgramOriginVersionField, true, value.version)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kAuthoredVariantLineageAuthoringVariantIdField = {"authoring_variant_id", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const AuthoredVariantLineage &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAuthoredVariantLineageAuthoringVariantIdField, true, value.authoring_variant_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const AuthoredVariantLineage &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAuthoredVariantLineageAuthoringVariantIdField, true, value.authoring_variant_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kAxiFenceAttrsScopeField = {"scope", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const AxiFenceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAxiFenceAttrsScopeField, true, value.scope)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const AxiFenceAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kAxiFenceAttrsScopeField, true, value.scope)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBarrierArrivalParticipantCoreField = {"participant_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBarrierArrivalKernelOpIdField = {"kernel_op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBarrierArrivalCommandIdField = {"command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBarrierArrivalDoneTokenIdField = {"done_token_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BarrierArrival &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierArrivalParticipantCoreField, true, value.participant_core)) return false;
    if (!visitor(kBarrierArrivalKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kBarrierArrivalCommandIdField, true, value.command_id)) return false;
    if (!visitor(kBarrierArrivalDoneTokenIdField, true, value.done_token_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BarrierArrival &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierArrivalCommandIdField, true, value.command_id)) return false;
    if (!visitor(kBarrierArrivalDoneTokenIdField, true, value.done_token_id)) return false;
    if (!visitor(kBarrierArrivalKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kBarrierArrivalParticipantCoreField, true, value.participant_core)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBarrierExecutionBarrierGroupIdField = {"barrier_group_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BarrierExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierExecutionBarrierGroupIdField, true, value.barrier_group_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BarrierExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierExecutionBarrierGroupIdField, true, value.barrier_group_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBarrierGroupBarrierGroupIdField = {"barrier_group_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBarrierGroupParticipantsField = {"participants", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kBarrierGroupArrivalsTargets = {306};
inline constexpr SemanticFieldDescriptor kBarrierGroupArrivalsField = {"arrivals", SemanticFieldKind::RefList, 0ull, kBarrierGroupArrivalsTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kBarrierGroupCompletionEventIdField = {"completion_event_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BarrierGroup &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierGroupBarrierGroupIdField, true, value.barrier_group_id)) return false;
    if (!visitor(kBarrierGroupParticipantsField, true, value.participants)) return false;
    if (!visitor(kBarrierGroupArrivalsField, true, value.arrivals)) return false;
    if (!visitor(kBarrierGroupCompletionEventIdField, true, value.completion_event_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BarrierGroup &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBarrierGroupArrivalsField, true, value.arrivals)) return false;
    if (!visitor(kBarrierGroupBarrierGroupIdField, true, value.barrier_group_id)) return false;
    if (!visitor(kBarrierGroupCompletionEventIdField, true, value.completion_event_id)) return false;
    if (!visitor(kBarrierGroupParticipantsField, true, value.participants)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kCommandSemanticsCommandIdField = {"command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kCommandSemanticsSourceTargets = {324, 313};
inline constexpr SemanticFieldDescriptor kCommandSemanticsSourceField = {"source", SemanticFieldKind::Ref, 0ull, kCommandSemanticsSourceTargets.data(), 2, true};
inline constexpr std::array<uint16_t, 5> kCommandSemanticsExecutionTargets = {312, 318, 333, 307, 314};
inline constexpr SemanticFieldDescriptor kCommandSemanticsExecutionField = {"execution", SemanticFieldKind::Ref, 0ull, kCommandSemanticsExecutionTargets.data(), 5, true};

template <typename Visitor> bool visitSemanticFieldsWire(const CommandSemantics &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCommandSemanticsCommandIdField, true, value.command_id)) return false;
    if (!visitor(kCommandSemanticsSourceField, true, value.source)) return false;
    if (!visitor(kCommandSemanticsExecutionField, true, value.execution)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const CommandSemantics &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCommandSemanticsCommandIdField, true, value.command_id)) return false;
    if (!visitor(kCommandSemanticsExecutionField, true, value.execution)) return false;
    if (!visitor(kCommandSemanticsSourceField, true, value.source)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kCompiledProgramOriginKernelBundleSemanticSha256Field = {"kernel_bundle_semantic_sha256", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const CompiledProgramOrigin &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCompiledProgramOriginKernelBundleSemanticSha256Field, true, value.kernel_bundle_semantic_sha256)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const CompiledProgramOrigin &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCompiledProgramOriginKernelBundleSemanticSha256Field, true, value.kernel_bundle_semantic_sha256)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kCompiledVariantLineageKernelModuleOrdinalField = {"kernel_module_ordinal", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kCompiledVariantLineageKernelModuleSemanticSha256Field = {"kernel_module_semantic_sha256", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const CompiledVariantLineage &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCompiledVariantLineageKernelModuleOrdinalField, true, value.kernel_module_ordinal)) return false;
    if (!visitor(kCompiledVariantLineageKernelModuleSemanticSha256Field, true, value.kernel_module_semantic_sha256)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const CompiledVariantLineage &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kCompiledVariantLineageKernelModuleOrdinalField, true, value.kernel_module_ordinal)) return false;
    if (!visitor(kCompiledVariantLineageKernelModuleSemanticSha256Field, true, value.kernel_module_semantic_sha256)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 1> kComputeExecutionPhasesTargets = {256};
inline constexpr SemanticFieldDescriptor kComputeExecutionPhasesField = {"phases", SemanticFieldKind::RefList, 0ull, kComputeExecutionPhasesTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ComputeExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kComputeExecutionPhasesField, true, value.phases)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ComputeExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kComputeExecutionPhasesField, true, value.phases)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 7> kControlCommandSourceAttrsTargets = {335, 336, 322, 320, 319, 334, 305};
inline constexpr SemanticFieldDescriptor kControlCommandSourceAttrsField = {"attrs", SemanticFieldKind::Ref, 0ull, kControlCommandSourceAttrsTargets.data(), 7, true};

template <typename Visitor> bool visitSemanticFieldsWire(const ControlCommandSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kControlCommandSourceAttrsField, true, value.attrs)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ControlCommandSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kControlCommandSourceAttrsField, true, value.attrs)) return false;
    return true;
}


template <typename Visitor> bool visitSemanticFieldsWire(const ControlExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ControlExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

inline constexpr SemanticFieldDescriptor kDescriptorEndpointUseRefIdField = {"ref_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorEndpointUseDescriptorIdField = {"descriptor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorEndpointUseSideField = {"side", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kDescriptorEndpointUseUseTargets = {332, 343};
inline constexpr SemanticFieldDescriptor kDescriptorEndpointUseUseField = {"use", SemanticFieldKind::Ref, 0ull, kDescriptorEndpointUseUseTargets.data(), 2, true};

template <typename Visitor> bool visitSemanticFieldsWire(const DescriptorEndpointUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorEndpointUseRefIdField, true, value.ref_id)) return false;
    if (!visitor(kDescriptorEndpointUseDescriptorIdField, true, value.descriptor_id)) return false;
    if (!visitor(kDescriptorEndpointUseSideField, true, value.side)) return false;
    if (!visitor(kDescriptorEndpointUseUseField, true, value.use)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DescriptorEndpointUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorEndpointUseDescriptorIdField, true, value.descriptor_id)) return false;
    if (!visitor(kDescriptorEndpointUseRefIdField, true, value.ref_id)) return false;
    if (!visitor(kDescriptorEndpointUseSideField, true, value.side)) return false;
    if (!visitor(kDescriptorEndpointUseUseField, true, value.use)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kDescriptorGroupGroupIdField = {"group_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorGroupCommandIdField = {"command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorGroupKernelOpIdField = {"kernel_op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorGroupDescriptorIdsField = {"descriptor_ids", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorGroupCompletionEventIdField = {"completion_event_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DescriptorGroup &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorGroupGroupIdField, true, value.group_id)) return false;
    if (!visitor(kDescriptorGroupCommandIdField, true, value.command_id)) return false;
    if (!visitor(kDescriptorGroupKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kDescriptorGroupDescriptorIdsField, true, value.descriptor_ids)) return false;
    if (!visitor(kDescriptorGroupCompletionEventIdField, true, value.completion_event_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DescriptorGroup &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorGroupCommandIdField, true, value.command_id)) return false;
    if (!visitor(kDescriptorGroupCompletionEventIdField, true, value.completion_event_id)) return false;
    if (!visitor(kDescriptorGroupDescriptorIdsField, true, value.descriptor_ids)) return false;
    if (!visitor(kDescriptorGroupGroupIdField, true, value.group_id)) return false;
    if (!visitor(kDescriptorGroupKernelOpIdField, true, value.kernel_op_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kDescriptorSourceDescriptorIdField = {"descriptor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DescriptorSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorSourceDescriptorIdField, true, value.descriptor_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DescriptorSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorSourceDescriptorIdField, true, value.descriptor_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kDmaExecutionDescriptorGroupIdField = {"descriptor_group_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DmaExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDmaExecutionDescriptorGroupIdField, true, value.descriptor_group_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DmaExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDmaExecutionDescriptorGroupIdField, true, value.descriptor_group_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kEventSignalAttrsEventIdField = {"event_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const EventSignalAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEventSignalAttrsEventIdField, true, value.event_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const EventSignalAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEventSignalAttrsEventIdField, true, value.event_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kEventWaitAttrsEventIdField = {"event_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const EventWaitAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEventWaitAttrsEventIdField, true, value.event_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const EventWaitAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kEventWaitAttrsEventIdField, true, value.event_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kExternalSlotBackingSlotIdField = {"slot_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ExternalSlotBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kExternalSlotBackingSlotIdField, true, value.slot_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ExternalSlotBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kExternalSlotBackingSlotIdField, true, value.slot_id)) return false;
    return true;
}


template <typename Visitor> bool visitSemanticFieldsWire(const HaltAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const HaltAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

inline constexpr SemanticFieldDescriptor kIdSpanFirstIdField = {"first_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kIdSpanCountField = {"count", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const IdSpan &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kIdSpanFirstIdField, true, value.first_id)) return false;
    if (!visitor(kIdSpanCountField, true, value.count)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const IdSpan &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kIdSpanCountField, true, value.count)) return false;
    if (!visitor(kIdSpanFirstIdField, true, value.first_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelCommandSourceKernelOpIdField = {"kernel_op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelCommandSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelCommandSourceKernelOpIdField, true, value.kernel_op_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelCommandSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelCommandSourceKernelOpIdField, true, value.kernel_op_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kKernelTokenSourceTokenIdField = {"token_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const KernelTokenSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTokenSourceTokenIdField, true, value.token_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const KernelTokenSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kKernelTokenSourceTokenIdField, true, value.token_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kLifecycleSourceVariantIdField = {"variant_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const LifecycleSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLifecycleSourceVariantIdField, true, value.variant_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const LifecycleSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLifecycleSourceVariantIdField, true, value.variant_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kLocalAllocationBackingAllocationIdField = {"allocation_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const LocalAllocationBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLocalAllocationBackingAllocationIdField, true, value.allocation_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const LocalAllocationBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kLocalAllocationBackingAllocationIdField, true, value.allocation_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kObjectBackingObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kObjectBackingBackingTargets = {327, 321};
inline constexpr SemanticFieldDescriptor kObjectBackingBackingField = {"backing", SemanticFieldKind::Ref, 0ull, kObjectBackingBackingTargets.data(), 2, true};

template <typename Visitor> bool visitSemanticFieldsWire(const ObjectBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kObjectBackingObjectIdField, true, value.object_id)) return false;
    if (!visitor(kObjectBackingBackingField, true, value.backing)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ObjectBacking &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kObjectBackingBackingField, true, value.backing)) return false;
    if (!visitor(kObjectBackingObjectIdField, true, value.object_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kObjectSourceObjectIdField = {"object_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ObjectSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kObjectSourceObjectIdField, true, value.object_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ObjectSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kObjectSourceObjectIdField, true, value.object_id)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 2> kProgramSemanticsOriginTargets = {310, 303};
inline constexpr SemanticFieldDescriptor kProgramSemanticsOriginField = {"origin", SemanticFieldKind::Ref, 0ull, kProgramSemanticsOriginTargets.data(), 2, true};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsVariantsTargets = {331};
inline constexpr SemanticFieldDescriptor kProgramSemanticsVariantsField = {"variants", SemanticFieldKind::RefList, 0ull, kProgramSemanticsVariantsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsKernelTensorsTargets = {285};
inline constexpr SemanticFieldDescriptor kProgramSemanticsKernelTensorsField = {"kernel_tensors", SemanticFieldKind::RefList, 0ull, kProgramSemanticsKernelTensorsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsComputationsTargets = {282};
inline constexpr SemanticFieldDescriptor kProgramSemanticsComputationsField = {"computations", SemanticFieldKind::RefList, 0ull, kProgramSemanticsComputationsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsPlacementsTargets = {294};
inline constexpr SemanticFieldDescriptor kProgramSemanticsPlacementsField = {"placements", SemanticFieldKind::RefList, 0ull, kProgramSemanticsPlacementsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsLogicalShardsTargets = {299};
inline constexpr SemanticFieldDescriptor kProgramSemanticsLogicalShardsField = {"logical_shards", SemanticFieldKind::RefList, 0ull, kProgramSemanticsLogicalShardsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsPartialSumsTargets = {293};
inline constexpr SemanticFieldDescriptor kProgramSemanticsPartialSumsField = {"partial_sums", SemanticFieldKind::RefList, 0ull, kProgramSemanticsPartialSumsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsObjectsTargets = {275};
inline constexpr SemanticFieldDescriptor kProgramSemanticsObjectsField = {"objects", SemanticFieldKind::RefList, 0ull, kProgramSemanticsObjectsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsViewsTargets = {276};
inline constexpr SemanticFieldDescriptor kProgramSemanticsViewsField = {"views", SemanticFieldKind::RefList, 0ull, kProgramSemanticsViewsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsStatesTargets = {300};
inline constexpr SemanticFieldDescriptor kProgramSemanticsStatesField = {"states", SemanticFieldKind::RefList, 0ull, kProgramSemanticsStatesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsTokensTargets = {278};
inline constexpr SemanticFieldDescriptor kProgramSemanticsTokensField = {"tokens", SemanticFieldKind::RefList, 0ull, kProgramSemanticsTokensTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsKernelOpsTargets = {284};
inline constexpr SemanticFieldDescriptor kProgramSemanticsKernelOpsField = {"kernel_ops", SemanticFieldKind::RefList, 0ull, kProgramSemanticsKernelOpsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsObjectBackingsTargets = {328};
inline constexpr SemanticFieldDescriptor kProgramSemanticsObjectBackingsField = {"object_backings", SemanticFieldKind::RefList, 0ull, kProgramSemanticsObjectBackingsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsResidentViewsTargets = {337};
inline constexpr SemanticFieldDescriptor kProgramSemanticsResidentViewsField = {"resident_views", SemanticFieldKind::RefList, 0ull, kProgramSemanticsResidentViewsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsCommandSemanticsTargets = {309};
inline constexpr SemanticFieldDescriptor kProgramSemanticsCommandSemanticsField = {"command_semantics", SemanticFieldKind::RefList, 0ull, kProgramSemanticsCommandSemanticsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsBarrierGroupsTargets = {308};
inline constexpr SemanticFieldDescriptor kProgramSemanticsBarrierGroupsField = {"barrier_groups", SemanticFieldKind::RefList, 0ull, kProgramSemanticsBarrierGroupsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsDependenciesTargets = {338};
inline constexpr SemanticFieldDescriptor kProgramSemanticsDependenciesField = {"dependencies", SemanticFieldKind::RefList, 0ull, kProgramSemanticsDependenciesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsStreamsTargets = {339};
inline constexpr SemanticFieldDescriptor kProgramSemanticsStreamsField = {"streams", SemanticFieldKind::RefList, 0ull, kProgramSemanticsStreamsTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kProgramSemanticsStreamCommandIdsField = {"stream_command_ids", SemanticFieldKind::U64List, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsDescriptorGroupsTargets = {316};
inline constexpr SemanticFieldDescriptor kProgramSemanticsDescriptorGroupsField = {"descriptor_groups", SemanticFieldKind::RefList, 0ull, kProgramSemanticsDescriptorGroupsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsEndpointUsesTargets = {315};
inline constexpr SemanticFieldDescriptor kProgramSemanticsEndpointUsesField = {"endpoint_uses", SemanticFieldKind::RefList, 0ull, kProgramSemanticsEndpointUsesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsBindingSlotsTargets = {345};
inline constexpr SemanticFieldDescriptor kProgramSemanticsBindingSlotsField = {"binding_slots", SemanticFieldKind::RefList, 0ull, kProgramSemanticsBindingSlotsTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kProgramSemanticsReferenceBindingIdentitySha256Field = {"reference_binding_identity_sha256", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kProgramSemanticsIntrinsicTrafficTargets = {352};
inline constexpr SemanticFieldDescriptor kProgramSemanticsIntrinsicTrafficField = {"intrinsic_traffic", SemanticFieldKind::Ref, 0ull, kProgramSemanticsIntrinsicTrafficTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ProgramSemantics &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kProgramSemanticsOriginField, true, value.origin)) return false;
    if (!visitor(kProgramSemanticsVariantsField, true, value.variants)) return false;
    if (!visitor(kProgramSemanticsKernelTensorsField, true, value.kernel_tensors)) return false;
    if (!visitor(kProgramSemanticsComputationsField, true, value.computations)) return false;
    if (!visitor(kProgramSemanticsPlacementsField, true, value.placements)) return false;
    if (!visitor(kProgramSemanticsLogicalShardsField, true, value.logical_shards)) return false;
    if (!visitor(kProgramSemanticsPartialSumsField, true, value.partial_sums)) return false;
    if (!visitor(kProgramSemanticsObjectsField, true, value.objects)) return false;
    if (!visitor(kProgramSemanticsViewsField, true, value.views)) return false;
    if (!visitor(kProgramSemanticsStatesField, true, value.states)) return false;
    if (!visitor(kProgramSemanticsTokensField, true, value.tokens)) return false;
    if (!visitor(kProgramSemanticsKernelOpsField, true, value.kernel_ops)) return false;
    if (!visitor(kProgramSemanticsObjectBackingsField, true, value.object_backings)) return false;
    if (!visitor(kProgramSemanticsResidentViewsField, true, value.resident_views)) return false;
    if (!visitor(kProgramSemanticsCommandSemanticsField, true, value.command_semantics)) return false;
    if (!visitor(kProgramSemanticsBarrierGroupsField, true, value.barrier_groups)) return false;
    if (!visitor(kProgramSemanticsDependenciesField, true, value.dependencies)) return false;
    if (!visitor(kProgramSemanticsStreamsField, true, value.streams)) return false;
    if (!visitor(kProgramSemanticsStreamCommandIdsField, true, value.stream_command_ids)) return false;
    if (!visitor(kProgramSemanticsDescriptorGroupsField, true, value.descriptor_groups)) return false;
    if (!visitor(kProgramSemanticsEndpointUsesField, true, value.endpoint_uses)) return false;
    if (!visitor(kProgramSemanticsBindingSlotsField, true, value.binding_slots)) return false;
    if (!visitor(kProgramSemanticsReferenceBindingIdentitySha256Field, true, value.reference_binding_identity_sha256)) return false;
    if (!visitor(kProgramSemanticsIntrinsicTrafficField, true, value.intrinsic_traffic)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ProgramSemantics &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kProgramSemanticsBarrierGroupsField, true, value.barrier_groups)) return false;
    if (!visitor(kProgramSemanticsBindingSlotsField, true, value.binding_slots)) return false;
    if (!visitor(kProgramSemanticsCommandSemanticsField, true, value.command_semantics)) return false;
    if (!visitor(kProgramSemanticsComputationsField, true, value.computations)) return false;
    if (!visitor(kProgramSemanticsDependenciesField, true, value.dependencies)) return false;
    if (!visitor(kProgramSemanticsDescriptorGroupsField, true, value.descriptor_groups)) return false;
    if (!visitor(kProgramSemanticsEndpointUsesField, true, value.endpoint_uses)) return false;
    if (!visitor(kProgramSemanticsIntrinsicTrafficField, true, value.intrinsic_traffic)) return false;
    if (!visitor(kProgramSemanticsKernelOpsField, true, value.kernel_ops)) return false;
    if (!visitor(kProgramSemanticsKernelTensorsField, true, value.kernel_tensors)) return false;
    if (!visitor(kProgramSemanticsLogicalShardsField, true, value.logical_shards)) return false;
    if (!visitor(kProgramSemanticsObjectBackingsField, true, value.object_backings)) return false;
    if (!visitor(kProgramSemanticsObjectsField, true, value.objects)) return false;
    if (!visitor(kProgramSemanticsOriginField, true, value.origin)) return false;
    if (!visitor(kProgramSemanticsPartialSumsField, true, value.partial_sums)) return false;
    if (!visitor(kProgramSemanticsPlacementsField, true, value.placements)) return false;
    if (!visitor(kProgramSemanticsReferenceBindingIdentitySha256Field, true, value.reference_binding_identity_sha256)) return false;
    if (!visitor(kProgramSemanticsResidentViewsField, true, value.resident_views)) return false;
    if (!visitor(kProgramSemanticsStatesField, true, value.states)) return false;
    if (!visitor(kProgramSemanticsStreamCommandIdsField, true, value.stream_command_ids)) return false;
    if (!visitor(kProgramSemanticsStreamsField, true, value.streams)) return false;
    if (!visitor(kProgramSemanticsTokensField, true, value.tokens)) return false;
    if (!visitor(kProgramSemanticsVariantsField, true, value.variants)) return false;
    if (!visitor(kProgramSemanticsViewsField, true, value.views)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kProgramVariantVariantIdField = {"variant_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kProgramVariantEntrypointIdField = {"entrypoint_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kProgramVariantProfileIdField = {"profile_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 2> kProgramVariantLineageTargets = {311, 304};
inline constexpr SemanticFieldDescriptor kProgramVariantLineageField = {"lineage", SemanticFieldKind::Ref, 0ull, kProgramVariantLineageTargets.data(), 2, true};
inline constexpr SemanticFieldDescriptor kProgramVariantLifecycleStreamIdField = {"lifecycle_stream_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kProgramVariantMembershipTargets = {342};
inline constexpr SemanticFieldDescriptor kProgramVariantMembershipField = {"membership", SemanticFieldKind::Ref, 0ull, kProgramVariantMembershipTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ProgramVariant &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kProgramVariantVariantIdField, true, value.variant_id)) return false;
    if (!visitor(kProgramVariantEntrypointIdField, true, value.entrypoint_id)) return false;
    if (!visitor(kProgramVariantProfileIdField, true, value.profile_id)) return false;
    if (!visitor(kProgramVariantLineageField, true, value.lineage)) return false;
    if (!visitor(kProgramVariantLifecycleStreamIdField, true, value.lifecycle_stream_id)) return false;
    if (!visitor(kProgramVariantMembershipField, true, value.membership)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ProgramVariant &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kProgramVariantEntrypointIdField, true, value.entrypoint_id)) return false;
    if (!visitor(kProgramVariantLifecycleStreamIdField, true, value.lifecycle_stream_id)) return false;
    if (!visitor(kProgramVariantLineageField, true, value.lineage)) return false;
    if (!visitor(kProgramVariantMembershipField, true, value.membership)) return false;
    if (!visitor(kProgramVariantProfileIdField, true, value.profile_id)) return false;
    if (!visitor(kProgramVariantVariantIdField, true, value.variant_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kReadAccessUseKernelOpIdField = {"kernel_op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kReadAccessUseAccessIndexField = {"access_index", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kReadAccessUseRegionTargets = {280};
inline constexpr SemanticFieldDescriptor kReadAccessUseRegionField = {"region", SemanticFieldKind::Ref, 0ull, kReadAccessUseRegionTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ReadAccessUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReadAccessUseKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kReadAccessUseAccessIndexField, true, value.access_index)) return false;
    if (!visitor(kReadAccessUseRegionField, true, value.region)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ReadAccessUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kReadAccessUseAccessIndexField, true, value.access_index)) return false;
    if (!visitor(kReadAccessUseKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kReadAccessUseRegionField, true, value.region)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kRecvWaitExecutionTransferIdField = {"transfer_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const RecvWaitExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRecvWaitExecutionTransferIdField, true, value.transfer_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const RecvWaitExecution &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRecvWaitExecutionTransferIdField, true, value.transfer_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kRepeatCommandAttrsSubrangeBeginStreamOrdinalField = {"subrange_begin_stream_ordinal", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kRepeatCommandAttrsSubrangeCommandCountField = {"subrange_command_count", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kRepeatCommandAttrsRepeatCountField = {"repeat_count", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const RepeatCommandAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRepeatCommandAttrsSubrangeBeginStreamOrdinalField, true, value.subrange_begin_stream_ordinal)) return false;
    if (!visitor(kRepeatCommandAttrsSubrangeCommandCountField, true, value.subrange_command_count)) return false;
    if (!visitor(kRepeatCommandAttrsRepeatCountField, true, value.repeat_count)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const RepeatCommandAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kRepeatCommandAttrsRepeatCountField, true, value.repeat_count)) return false;
    if (!visitor(kRepeatCommandAttrsSubrangeBeginStreamOrdinalField, true, value.subrange_begin_stream_ordinal)) return false;
    if (!visitor(kRepeatCommandAttrsSubrangeCommandCountField, true, value.subrange_command_count)) return false;
    return true;
}


template <typename Visitor> bool visitSemanticFieldsWire(const RequestBeginAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const RequestBeginAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}


template <typename Visitor> bool visitSemanticFieldsWire(const RequestEndAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const RequestEndAttrs &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    return true;
}

inline constexpr SemanticFieldDescriptor kResidentViewRuntimeShardIdField = {"runtime_shard_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kResidentViewViewIdField = {"view_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ResidentView &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kResidentViewRuntimeShardIdField, true, value.runtime_shard_id)) return false;
    if (!visitor(kResidentViewViewIdField, true, value.view_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ResidentView &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kResidentViewRuntimeShardIdField, true, value.runtime_shard_id)) return false;
    if (!visitor(kResidentViewViewIdField, true, value.view_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kScheduledDependencyDependencyIdField = {"dependency_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledDependencySourceCommandIdField = {"source_command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledDependencyTargetCommandIdField = {"target_command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledDependencyKindField = {"kind", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 6> kScheduledDependencySourceTargets = {325, 340, 329, 317, 341, 326};
inline constexpr SemanticFieldDescriptor kScheduledDependencySourceField = {"source", SemanticFieldKind::Ref, 0ull, kScheduledDependencySourceTargets.data(), 6, true};

template <typename Visitor> bool visitSemanticFieldsWire(const ScheduledDependency &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kScheduledDependencyDependencyIdField, true, value.dependency_id)) return false;
    if (!visitor(kScheduledDependencySourceCommandIdField, true, value.source_command_id)) return false;
    if (!visitor(kScheduledDependencyTargetCommandIdField, true, value.target_command_id)) return false;
    if (!visitor(kScheduledDependencyKindField, true, value.kind)) return false;
    if (!visitor(kScheduledDependencySourceField, true, value.source)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ScheduledDependency &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kScheduledDependencyDependencyIdField, true, value.dependency_id)) return false;
    if (!visitor(kScheduledDependencyKindField, true, value.kind)) return false;
    if (!visitor(kScheduledDependencySourceField, true, value.source)) return false;
    if (!visitor(kScheduledDependencySourceCommandIdField, true, value.source_command_id)) return false;
    if (!visitor(kScheduledDependencyTargetCommandIdField, true, value.target_command_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kScheduledStreamStreamIdField = {"stream_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledStreamCoreIdField = {"core_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledStreamPhysicalStreamIdField = {"physical_stream_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledStreamCommandBeginField = {"command_begin", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledStreamCommandCountField = {"command_count", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kScheduledStreamFlagsField = {"flags", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ScheduledStream &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kScheduledStreamStreamIdField, true, value.stream_id)) return false;
    if (!visitor(kScheduledStreamCoreIdField, true, value.core_id)) return false;
    if (!visitor(kScheduledStreamPhysicalStreamIdField, true, value.physical_stream_id)) return false;
    if (!visitor(kScheduledStreamCommandBeginField, true, value.command_begin)) return false;
    if (!visitor(kScheduledStreamCommandCountField, true, value.command_count)) return false;
    if (!visitor(kScheduledStreamFlagsField, true, value.flags)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ScheduledStream &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kScheduledStreamCommandBeginField, true, value.command_begin)) return false;
    if (!visitor(kScheduledStreamCommandCountField, true, value.command_count)) return false;
    if (!visitor(kScheduledStreamCoreIdField, true, value.core_id)) return false;
    if (!visitor(kScheduledStreamFlagsField, true, value.flags)) return false;
    if (!visitor(kScheduledStreamPhysicalStreamIdField, true, value.physical_stream_id)) return false;
    if (!visitor(kScheduledStreamStreamIdField, true, value.stream_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kStateSourceStateIdField = {"state_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const StateSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStateSourceStateIdField, true, value.state_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const StateSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStateSourceStateIdField, true, value.state_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kStreamOrderSourceStreamIdField = {"stream_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const StreamOrderSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStreamOrderSourceStreamIdField, true, value.stream_id)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const StreamOrderSource &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kStreamOrderSourceStreamIdField, true, value.stream_id)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 1> kVariantMembershipAbiTensorsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipAbiTensorsField = {"abi_tensors", SemanticFieldKind::Ref, 0ull, kVariantMembershipAbiTensorsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipRuntimeShardsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipRuntimeShardsField = {"runtime_shards", SemanticFieldKind::Ref, 0ull, kVariantMembershipRuntimeShardsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipAllocationsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipAllocationsField = {"allocations", SemanticFieldKind::Ref, 0ull, kVariantMembershipAllocationsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipStreamsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipStreamsField = {"streams", SemanticFieldKind::Ref, 0ull, kVariantMembershipStreamsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipCommandsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipCommandsField = {"commands", SemanticFieldKind::Ref, 0ull, kVariantMembershipCommandsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipEventsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipEventsField = {"events", SemanticFieldKind::Ref, 0ull, kVariantMembershipEventsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipDescriptorsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipDescriptorsField = {"descriptors", SemanticFieldKind::Ref, 0ull, kVariantMembershipDescriptorsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipRelocationsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipRelocationsField = {"relocations", SemanticFieldKind::Ref, 0ull, kVariantMembershipRelocationsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipKernelTensorsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipKernelTensorsField = {"kernel_tensors", SemanticFieldKind::Ref, 0ull, kVariantMembershipKernelTensorsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipComputationsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipComputationsField = {"computations", SemanticFieldKind::Ref, 0ull, kVariantMembershipComputationsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipPlacementsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipPlacementsField = {"placements", SemanticFieldKind::Ref, 0ull, kVariantMembershipPlacementsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipLogicalShardsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipLogicalShardsField = {"logical_shards", SemanticFieldKind::Ref, 0ull, kVariantMembershipLogicalShardsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipPartialSumsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipPartialSumsField = {"partial_sums", SemanticFieldKind::Ref, 0ull, kVariantMembershipPartialSumsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipObjectsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipObjectsField = {"objects", SemanticFieldKind::Ref, 0ull, kVariantMembershipObjectsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipViewsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipViewsField = {"views", SemanticFieldKind::Ref, 0ull, kVariantMembershipViewsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipStatesTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipStatesField = {"states", SemanticFieldKind::Ref, 0ull, kVariantMembershipStatesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipTokensTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipTokensField = {"tokens", SemanticFieldKind::Ref, 0ull, kVariantMembershipTokensTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipKernelOpsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipKernelOpsField = {"kernel_ops", SemanticFieldKind::Ref, 0ull, kVariantMembershipKernelOpsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipObjectBackingsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipObjectBackingsField = {"object_backings", SemanticFieldKind::Ref, 0ull, kVariantMembershipObjectBackingsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipCommandSemanticsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipCommandSemanticsField = {"command_semantics", SemanticFieldKind::Ref, 0ull, kVariantMembershipCommandSemanticsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipBarrierGroupsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipBarrierGroupsField = {"barrier_groups", SemanticFieldKind::Ref, 0ull, kVariantMembershipBarrierGroupsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipDependenciesTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipDependenciesField = {"dependencies", SemanticFieldKind::Ref, 0ull, kVariantMembershipDependenciesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipEndpointUsesTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipEndpointUsesField = {"endpoint_uses", SemanticFieldKind::Ref, 0ull, kVariantMembershipEndpointUsesTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kVariantMembershipBindingSlotsTargets = {323};
inline constexpr SemanticFieldDescriptor kVariantMembershipBindingSlotsField = {"binding_slots", SemanticFieldKind::Ref, 0ull, kVariantMembershipBindingSlotsTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const VariantMembership &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kVariantMembershipAbiTensorsField, true, value.abi_tensors)) return false;
    if (!visitor(kVariantMembershipRuntimeShardsField, true, value.runtime_shards)) return false;
    if (!visitor(kVariantMembershipAllocationsField, true, value.allocations)) return false;
    if (!visitor(kVariantMembershipStreamsField, true, value.streams)) return false;
    if (!visitor(kVariantMembershipCommandsField, true, value.commands)) return false;
    if (!visitor(kVariantMembershipEventsField, true, value.events)) return false;
    if (!visitor(kVariantMembershipDescriptorsField, true, value.descriptors)) return false;
    if (!visitor(kVariantMembershipRelocationsField, true, value.relocations)) return false;
    if (!visitor(kVariantMembershipKernelTensorsField, true, value.kernel_tensors)) return false;
    if (!visitor(kVariantMembershipComputationsField, true, value.computations)) return false;
    if (!visitor(kVariantMembershipPlacementsField, true, value.placements)) return false;
    if (!visitor(kVariantMembershipLogicalShardsField, true, value.logical_shards)) return false;
    if (!visitor(kVariantMembershipPartialSumsField, true, value.partial_sums)) return false;
    if (!visitor(kVariantMembershipObjectsField, true, value.objects)) return false;
    if (!visitor(kVariantMembershipViewsField, true, value.views)) return false;
    if (!visitor(kVariantMembershipStatesField, true, value.states)) return false;
    if (!visitor(kVariantMembershipTokensField, true, value.tokens)) return false;
    if (!visitor(kVariantMembershipKernelOpsField, true, value.kernel_ops)) return false;
    if (!visitor(kVariantMembershipObjectBackingsField, true, value.object_backings)) return false;
    if (!visitor(kVariantMembershipCommandSemanticsField, true, value.command_semantics)) return false;
    if (!visitor(kVariantMembershipBarrierGroupsField, true, value.barrier_groups)) return false;
    if (!visitor(kVariantMembershipDependenciesField, true, value.dependencies)) return false;
    if (!visitor(kVariantMembershipEndpointUsesField, true, value.endpoint_uses)) return false;
    if (!visitor(kVariantMembershipBindingSlotsField, true, value.binding_slots)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const VariantMembership &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kVariantMembershipAbiTensorsField, true, value.abi_tensors)) return false;
    if (!visitor(kVariantMembershipAllocationsField, true, value.allocations)) return false;
    if (!visitor(kVariantMembershipBarrierGroupsField, true, value.barrier_groups)) return false;
    if (!visitor(kVariantMembershipBindingSlotsField, true, value.binding_slots)) return false;
    if (!visitor(kVariantMembershipCommandSemanticsField, true, value.command_semantics)) return false;
    if (!visitor(kVariantMembershipCommandsField, true, value.commands)) return false;
    if (!visitor(kVariantMembershipComputationsField, true, value.computations)) return false;
    if (!visitor(kVariantMembershipDependenciesField, true, value.dependencies)) return false;
    if (!visitor(kVariantMembershipDescriptorsField, true, value.descriptors)) return false;
    if (!visitor(kVariantMembershipEndpointUsesField, true, value.endpoint_uses)) return false;
    if (!visitor(kVariantMembershipEventsField, true, value.events)) return false;
    if (!visitor(kVariantMembershipKernelOpsField, true, value.kernel_ops)) return false;
    if (!visitor(kVariantMembershipKernelTensorsField, true, value.kernel_tensors)) return false;
    if (!visitor(kVariantMembershipLogicalShardsField, true, value.logical_shards)) return false;
    if (!visitor(kVariantMembershipObjectBackingsField, true, value.object_backings)) return false;
    if (!visitor(kVariantMembershipObjectsField, true, value.objects)) return false;
    if (!visitor(kVariantMembershipPartialSumsField, true, value.partial_sums)) return false;
    if (!visitor(kVariantMembershipPlacementsField, true, value.placements)) return false;
    if (!visitor(kVariantMembershipRelocationsField, true, value.relocations)) return false;
    if (!visitor(kVariantMembershipRuntimeShardsField, true, value.runtime_shards)) return false;
    if (!visitor(kVariantMembershipStatesField, true, value.states)) return false;
    if (!visitor(kVariantMembershipStreamsField, true, value.streams)) return false;
    if (!visitor(kVariantMembershipTokensField, true, value.tokens)) return false;
    if (!visitor(kVariantMembershipViewsField, true, value.views)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kWriteAccessUseKernelOpIdField = {"kernel_op_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kWriteAccessUseAccessIndexField = {"access_index", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kWriteAccessUseRegionTargets = {280};
inline constexpr SemanticFieldDescriptor kWriteAccessUseRegionField = {"region", SemanticFieldKind::Ref, 0ull, kWriteAccessUseRegionTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const WriteAccessUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kWriteAccessUseKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kWriteAccessUseAccessIndexField, true, value.access_index)) return false;
    if (!visitor(kWriteAccessUseRegionField, true, value.region)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const WriteAccessUse &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kWriteAccessUseAccessIndexField, true, value.access_index)) return false;
    if (!visitor(kWriteAccessUseKernelOpIdField, true, value.kernel_op_id)) return false;
    if (!visitor(kWriteAccessUseRegionField, true, value.region)) return false;
    return true;
}

#endif

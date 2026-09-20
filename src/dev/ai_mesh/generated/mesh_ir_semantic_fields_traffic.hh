#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_TRAFFIC_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_FIELDS_TRAFFIC_HH

inline constexpr SemanticFieldDescriptor kBindingSlotIdField = {"slot_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingRegionIdField = {"region_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingOwnerCoreField = {"owner_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingAllocationOffsetBytesField = {"allocation_offset_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingAllocationSizeBytesField = {"allocation_size_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingAllocationAlignmentBytesField = {"allocation_alignment_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingAccessField = {"access", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const Binding &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBindingSlotIdField, true, value.slot_id)) return false;
    if (!visitor(kBindingRegionIdField, true, value.region_id)) return false;
    if (!visitor(kBindingOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBindingAllocationOffsetBytesField, true, value.allocation_offset_bytes)) return false;
    if (!visitor(kBindingAllocationSizeBytesField, true, value.allocation_size_bytes)) return false;
    if (!visitor(kBindingAllocationAlignmentBytesField, true, value.allocation_alignment_bytes)) return false;
    if (!visitor(kBindingAccessField, true, value.access)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const Binding &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBindingAccessField, true, value.access)) return false;
    if (!visitor(kBindingAllocationAlignmentBytesField, true, value.allocation_alignment_bytes)) return false;
    if (!visitor(kBindingAllocationOffsetBytesField, true, value.allocation_offset_bytes)) return false;
    if (!visitor(kBindingAllocationSizeBytesField, true, value.allocation_size_bytes)) return false;
    if (!visitor(kBindingOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBindingRegionIdField, true, value.region_id)) return false;
    if (!visitor(kBindingSlotIdField, true, value.slot_id)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kBindingSlotSlotIdField = {"slot_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotSymbolField = {"symbol", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotMemorySpaceField = {"memory_space", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotRegionIdField = {"region_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotOwnerCoreField = {"owner_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotRequiredAllocationBytesField = {"required_allocation_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotRequiredAllocationAlignmentBytesField = {"required_allocation_alignment_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kBindingSlotAccessField = {"access", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kBindingSlotReferenceBindingTargets = {344};
inline constexpr SemanticFieldDescriptor kBindingSlotReferenceBindingField = {"reference_binding", SemanticFieldKind::Ref, 0ull, kBindingSlotReferenceBindingTargets.data(), 1, false};

template <typename Visitor> bool visitSemanticFieldsWire(const BindingSlot &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBindingSlotSlotIdField, true, value.slot_id)) return false;
    if (!visitor(kBindingSlotSymbolField, true, value.symbol)) return false;
    if (!visitor(kBindingSlotMemorySpaceField, true, value.memory_space)) return false;
    if (!visitor(kBindingSlotRegionIdField, true, value.region_id)) return false;
    if (!visitor(kBindingSlotOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBindingSlotRequiredAllocationBytesField, true, value.required_allocation_bytes)) return false;
    if (!visitor(kBindingSlotRequiredAllocationAlignmentBytesField, true, value.required_allocation_alignment_bytes)) return false;
    if (!visitor(kBindingSlotAccessField, true, value.access)) return false;
    if (!visitor(kBindingSlotReferenceBindingField, true, value.reference_binding)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const BindingSlot &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kBindingSlotAccessField, true, value.access)) return false;
    if (!visitor(kBindingSlotMemorySpaceField, true, value.memory_space)) return false;
    if (!visitor(kBindingSlotOwnerCoreField, true, value.owner_core)) return false;
    if (!visitor(kBindingSlotReferenceBindingField, true, value.reference_binding)) return false;
    if (!visitor(kBindingSlotRegionIdField, true, value.region_id)) return false;
    if (!visitor(kBindingSlotRequiredAllocationAlignmentBytesField, true, value.required_allocation_alignment_bytes)) return false;
    if (!visitor(kBindingSlotRequiredAllocationBytesField, true, value.required_allocation_bytes)) return false;
    if (!visitor(kBindingSlotSlotIdField, true, value.slot_id)) return false;
    if (!visitor(kBindingSlotSymbolField, true, value.symbol)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kChannelTrafficChannelField = {"channel", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficVnetField = {"vnet", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficSourceEndpointField = {"source_endpoint", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficDestinationEndpointField = {"destination_endpoint", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficSourceNodeField = {"source_node", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficSourcePortField = {"source_port", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficDestinationNodeField = {"destination_node", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficDestinationPortField = {"destination_port", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficSourceRouterField = {"source_router", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficDestinationRouterField = {"destination_router", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficMessagesField = {"messages", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficPacketsField = {"packets", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficFlitsField = {"flits", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kChannelTrafficWireBytesField = {"wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const ChannelTraffic &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kChannelTrafficChannelField, true, value.channel)) return false;
    if (!visitor(kChannelTrafficVnetField, true, value.vnet)) return false;
    if (!visitor(kChannelTrafficSourceEndpointField, true, value.source_endpoint)) return false;
    if (!visitor(kChannelTrafficDestinationEndpointField, true, value.destination_endpoint)) return false;
    if (!visitor(kChannelTrafficSourceNodeField, true, value.source_node)) return false;
    if (!visitor(kChannelTrafficSourcePortField, true, value.source_port)) return false;
    if (!visitor(kChannelTrafficDestinationNodeField, true, value.destination_node)) return false;
    if (!visitor(kChannelTrafficDestinationPortField, true, value.destination_port)) return false;
    if (!visitor(kChannelTrafficSourceRouterField, true, value.source_router)) return false;
    if (!visitor(kChannelTrafficDestinationRouterField, true, value.destination_router)) return false;
    if (!visitor(kChannelTrafficMessagesField, true, value.messages)) return false;
    if (!visitor(kChannelTrafficPacketsField, true, value.packets)) return false;
    if (!visitor(kChannelTrafficFlitsField, true, value.flits)) return false;
    if (!visitor(kChannelTrafficWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const ChannelTraffic &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kChannelTrafficChannelField, true, value.channel)) return false;
    if (!visitor(kChannelTrafficDestinationEndpointField, true, value.destination_endpoint)) return false;
    if (!visitor(kChannelTrafficDestinationNodeField, true, value.destination_node)) return false;
    if (!visitor(kChannelTrafficDestinationPortField, true, value.destination_port)) return false;
    if (!visitor(kChannelTrafficDestinationRouterField, true, value.destination_router)) return false;
    if (!visitor(kChannelTrafficFlitsField, true, value.flits)) return false;
    if (!visitor(kChannelTrafficMessagesField, true, value.messages)) return false;
    if (!visitor(kChannelTrafficPacketsField, true, value.packets)) return false;
    if (!visitor(kChannelTrafficSourceEndpointField, true, value.source_endpoint)) return false;
    if (!visitor(kChannelTrafficSourceNodeField, true, value.source_node)) return false;
    if (!visitor(kChannelTrafficSourcePortField, true, value.source_port)) return false;
    if (!visitor(kChannelTrafficSourceRouterField, true, value.source_router)) return false;
    if (!visitor(kChannelTrafficVnetField, true, value.vnet)) return false;
    if (!visitor(kChannelTrafficWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kDescriptorIdentityEntrypointIdField = {"entrypoint_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityProfileIdField = {"profile_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityCommandIdField = {"command_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityDescriptorIdField = {"descriptor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityIssuingCoreField = {"issuing_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityPeerCoreField = {"peer_core", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityTensorIdField = {"tensor_id", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityTensorRoleField = {"tensor_role", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorIdentityDirectionField = {"direction", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DescriptorIdentity &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorIdentityEntrypointIdField, true, value.entrypoint_id)) return false;
    if (!visitor(kDescriptorIdentityProfileIdField, true, value.profile_id)) return false;
    if (!visitor(kDescriptorIdentityCommandIdField, true, value.command_id)) return false;
    if (!visitor(kDescriptorIdentityDescriptorIdField, true, value.descriptor_id)) return false;
    if (!visitor(kDescriptorIdentityIssuingCoreField, true, value.issuing_core)) return false;
    if (!visitor(kDescriptorIdentityPeerCoreField, true, value.peer_core)) return false;
    if (!visitor(kDescriptorIdentityTensorIdField, true, value.tensor_id)) return false;
    if (!visitor(kDescriptorIdentityTensorRoleField, true, value.tensor_role)) return false;
    if (!visitor(kDescriptorIdentityDirectionField, true, value.direction)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DescriptorIdentity &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorIdentityCommandIdField, true, value.command_id)) return false;
    if (!visitor(kDescriptorIdentityDescriptorIdField, true, value.descriptor_id)) return false;
    if (!visitor(kDescriptorIdentityDirectionField, true, value.direction)) return false;
    if (!visitor(kDescriptorIdentityEntrypointIdField, true, value.entrypoint_id)) return false;
    if (!visitor(kDescriptorIdentityIssuingCoreField, true, value.issuing_core)) return false;
    if (!visitor(kDescriptorIdentityPeerCoreField, true, value.peer_core)) return false;
    if (!visitor(kDescriptorIdentityProfileIdField, true, value.profile_id)) return false;
    if (!visitor(kDescriptorIdentityTensorIdField, true, value.tensor_id)) return false;
    if (!visitor(kDescriptorIdentityTensorRoleField, true, value.tensor_role)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 1> kDescriptorTrafficIdentityTargets = {347};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficIdentityField = {"identity", SemanticFieldKind::Ref, 0ull, kDescriptorTrafficIdentityTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficExecutionCountField = {"execution_count", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficSrcMemorySpaceField = {"src_memory_space", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficDstMemorySpaceField = {"dst_memory_space", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficSrcEndpointField = {"src_endpoint", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficDstEndpointField = {"dst_endpoint", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficSrcRouterField = {"src_router", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficDstRouterField = {"dst_router", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficSrcAddressField = {"src_address", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficDstAddressField = {"dst_address", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficRemoteAddressField = {"remote_address", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficRowsField = {"rows", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficRowBytesField = {"row_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficRemoteStrideBytesField = {"remote_stride_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficMaxBurstBeatsField = {"max_burst_beats", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficHopsField = {"hops", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficUsefulBytesField = {"useful_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficPhysicalBeatBytesField = {"physical_beat_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficSegmentsField = {"segments", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficBurstsField = {"bursts", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kDescriptorTrafficChannelsTargets = {346};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficChannelsField = {"channels", SemanticFieldKind::RefList, 0ull, kDescriptorTrafficChannelsTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficPacketsField = {"packets", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficFlitsField = {"flits", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficWireBytesField = {"wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kDescriptorTrafficHopWireBytesField = {"hop_wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const DescriptorTraffic &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorTrafficIdentityField, true, value.identity)) return false;
    if (!visitor(kDescriptorTrafficExecutionCountField, true, value.execution_count)) return false;
    if (!visitor(kDescriptorTrafficSrcMemorySpaceField, true, value.src_memory_space)) return false;
    if (!visitor(kDescriptorTrafficDstMemorySpaceField, true, value.dst_memory_space)) return false;
    if (!visitor(kDescriptorTrafficSrcEndpointField, true, value.src_endpoint)) return false;
    if (!visitor(kDescriptorTrafficDstEndpointField, true, value.dst_endpoint)) return false;
    if (!visitor(kDescriptorTrafficSrcRouterField, true, value.src_router)) return false;
    if (!visitor(kDescriptorTrafficDstRouterField, true, value.dst_router)) return false;
    if (!visitor(kDescriptorTrafficSrcAddressField, true, value.src_address)) return false;
    if (!visitor(kDescriptorTrafficDstAddressField, true, value.dst_address)) return false;
    if (!visitor(kDescriptorTrafficRemoteAddressField, true, value.remote_address)) return false;
    if (!visitor(kDescriptorTrafficRowsField, true, value.rows)) return false;
    if (!visitor(kDescriptorTrafficRowBytesField, true, value.row_bytes)) return false;
    if (!visitor(kDescriptorTrafficRemoteStrideBytesField, true, value.remote_stride_bytes)) return false;
    if (!visitor(kDescriptorTrafficMaxBurstBeatsField, true, value.max_burst_beats)) return false;
    if (!visitor(kDescriptorTrafficHopsField, true, value.hops)) return false;
    if (!visitor(kDescriptorTrafficUsefulBytesField, true, value.useful_bytes)) return false;
    if (!visitor(kDescriptorTrafficPhysicalBeatBytesField, true, value.physical_beat_bytes)) return false;
    if (!visitor(kDescriptorTrafficSegmentsField, true, value.segments)) return false;
    if (!visitor(kDescriptorTrafficBurstsField, true, value.bursts)) return false;
    if (!visitor(kDescriptorTrafficChannelsField, true, value.channels)) return false;
    if (!visitor(kDescriptorTrafficPacketsField, true, value.packets)) return false;
    if (!visitor(kDescriptorTrafficFlitsField, true, value.flits)) return false;
    if (!visitor(kDescriptorTrafficWireBytesField, true, value.wire_bytes)) return false;
    if (!visitor(kDescriptorTrafficHopWireBytesField, true, value.hop_wire_bytes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const DescriptorTraffic &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kDescriptorTrafficBurstsField, true, value.bursts)) return false;
    if (!visitor(kDescriptorTrafficChannelsField, true, value.channels)) return false;
    if (!visitor(kDescriptorTrafficDstAddressField, true, value.dst_address)) return false;
    if (!visitor(kDescriptorTrafficDstEndpointField, true, value.dst_endpoint)) return false;
    if (!visitor(kDescriptorTrafficDstMemorySpaceField, true, value.dst_memory_space)) return false;
    if (!visitor(kDescriptorTrafficDstRouterField, true, value.dst_router)) return false;
    if (!visitor(kDescriptorTrafficExecutionCountField, true, value.execution_count)) return false;
    if (!visitor(kDescriptorTrafficFlitsField, true, value.flits)) return false;
    if (!visitor(kDescriptorTrafficHopWireBytesField, true, value.hop_wire_bytes)) return false;
    if (!visitor(kDescriptorTrafficHopsField, true, value.hops)) return false;
    if (!visitor(kDescriptorTrafficIdentityField, true, value.identity)) return false;
    if (!visitor(kDescriptorTrafficMaxBurstBeatsField, true, value.max_burst_beats)) return false;
    if (!visitor(kDescriptorTrafficPacketsField, true, value.packets)) return false;
    if (!visitor(kDescriptorTrafficPhysicalBeatBytesField, true, value.physical_beat_bytes)) return false;
    if (!visitor(kDescriptorTrafficRemoteAddressField, true, value.remote_address)) return false;
    if (!visitor(kDescriptorTrafficRemoteStrideBytesField, true, value.remote_stride_bytes)) return false;
    if (!visitor(kDescriptorTrafficRowBytesField, true, value.row_bytes)) return false;
    if (!visitor(kDescriptorTrafficRowsField, true, value.rows)) return false;
    if (!visitor(kDescriptorTrafficSegmentsField, true, value.segments)) return false;
    if (!visitor(kDescriptorTrafficSrcAddressField, true, value.src_address)) return false;
    if (!visitor(kDescriptorTrafficSrcEndpointField, true, value.src_endpoint)) return false;
    if (!visitor(kDescriptorTrafficSrcMemorySpaceField, true, value.src_memory_space)) return false;
    if (!visitor(kDescriptorTrafficSrcRouterField, true, value.src_router)) return false;
    if (!visitor(kDescriptorTrafficUsefulBytesField, true, value.useful_bytes)) return false;
    if (!visitor(kDescriptorTrafficWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

inline constexpr std::array<uint16_t, 1> kTrafficAggregateKeyTargets = {350};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyField = {"key", SemanticFieldKind::Ref, 0ull, kTrafficAggregateKeyTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateUsefulBytesField = {"useful_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregatePhysicalBeatBytesField = {"physical_beat_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateStaticDescriptorsField = {"static_descriptors", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateDescriptorExecutionsField = {"descriptor_executions", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateSegmentsField = {"segments", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateBurstsField = {"bursts", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kTrafficAggregateChannelsTargets = {351};
inline constexpr SemanticFieldDescriptor kTrafficAggregateChannelsField = {"channels", SemanticFieldKind::RefList, 0ull, kTrafficAggregateChannelsTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregatePacketsField = {"packets", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateFlitsField = {"flits", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateWireBytesField = {"wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateHopWireBytesField = {"hop_wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TrafficAggregate &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficAggregateKeyField, true, value.key)) return false;
    if (!visitor(kTrafficAggregateUsefulBytesField, true, value.useful_bytes)) return false;
    if (!visitor(kTrafficAggregatePhysicalBeatBytesField, true, value.physical_beat_bytes)) return false;
    if (!visitor(kTrafficAggregateStaticDescriptorsField, true, value.static_descriptors)) return false;
    if (!visitor(kTrafficAggregateDescriptorExecutionsField, true, value.descriptor_executions)) return false;
    if (!visitor(kTrafficAggregateSegmentsField, true, value.segments)) return false;
    if (!visitor(kTrafficAggregateBurstsField, true, value.bursts)) return false;
    if (!visitor(kTrafficAggregateChannelsField, true, value.channels)) return false;
    if (!visitor(kTrafficAggregatePacketsField, true, value.packets)) return false;
    if (!visitor(kTrafficAggregateFlitsField, true, value.flits)) return false;
    if (!visitor(kTrafficAggregateWireBytesField, true, value.wire_bytes)) return false;
    if (!visitor(kTrafficAggregateHopWireBytesField, true, value.hop_wire_bytes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TrafficAggregate &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficAggregateBurstsField, true, value.bursts)) return false;
    if (!visitor(kTrafficAggregateChannelsField, true, value.channels)) return false;
    if (!visitor(kTrafficAggregateDescriptorExecutionsField, true, value.descriptor_executions)) return false;
    if (!visitor(kTrafficAggregateFlitsField, true, value.flits)) return false;
    if (!visitor(kTrafficAggregateHopWireBytesField, true, value.hop_wire_bytes)) return false;
    if (!visitor(kTrafficAggregateKeyField, true, value.key)) return false;
    if (!visitor(kTrafficAggregatePacketsField, true, value.packets)) return false;
    if (!visitor(kTrafficAggregatePhysicalBeatBytesField, true, value.physical_beat_bytes)) return false;
    if (!visitor(kTrafficAggregateSegmentsField, true, value.segments)) return false;
    if (!visitor(kTrafficAggregateStaticDescriptorsField, true, value.static_descriptors)) return false;
    if (!visitor(kTrafficAggregateUsefulBytesField, true, value.useful_bytes)) return false;
    if (!visitor(kTrafficAggregateWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyLevelField = {"level", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyEntrypointIdField = {"entrypoint_id", SemanticFieldKind::U64, 2ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyProfileIdField = {"profile_id", SemanticFieldKind::U64, 4ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyCoreIdField = {"core_id", SemanticFieldKind::U64, 8ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyPeerCoreField = {"peer_core", SemanticFieldKind::U64, 16ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyMemorySpaceField = {"memory_space", SemanticFieldKind::Enum, 32ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyTensorRoleField = {"tensor_role", SemanticFieldKind::Enum, 64ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficAggregateKeyDirectionField = {"direction", SemanticFieldKind::Enum, 128ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TrafficAggregateKey &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficAggregateKeyLevelField, true, value.level)) return false;
    if (!visitor(kTrafficAggregateKeyEntrypointIdField, (value.presence_mask & 2ull) != 0, value.entrypoint_id)) return false;
    if (!visitor(kTrafficAggregateKeyProfileIdField, (value.presence_mask & 4ull) != 0, value.profile_id)) return false;
    if (!visitor(kTrafficAggregateKeyCoreIdField, (value.presence_mask & 8ull) != 0, value.core_id)) return false;
    if (!visitor(kTrafficAggregateKeyPeerCoreField, (value.presence_mask & 16ull) != 0, value.peer_core)) return false;
    if (!visitor(kTrafficAggregateKeyMemorySpaceField, (value.presence_mask & 32ull) != 0, value.memory_space)) return false;
    if (!visitor(kTrafficAggregateKeyTensorRoleField, (value.presence_mask & 64ull) != 0, value.tensor_role)) return false;
    if (!visitor(kTrafficAggregateKeyDirectionField, (value.presence_mask & 128ull) != 0, value.direction)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TrafficAggregateKey &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficAggregateKeyCoreIdField, (value.presence_mask & 8ull) != 0, value.core_id)) return false;
    if (!visitor(kTrafficAggregateKeyDirectionField, (value.presence_mask & 128ull) != 0, value.direction)) return false;
    if (!visitor(kTrafficAggregateKeyEntrypointIdField, (value.presence_mask & 2ull) != 0, value.entrypoint_id)) return false;
    if (!visitor(kTrafficAggregateKeyLevelField, true, value.level)) return false;
    if (!visitor(kTrafficAggregateKeyMemorySpaceField, (value.presence_mask & 32ull) != 0, value.memory_space)) return false;
    if (!visitor(kTrafficAggregateKeyPeerCoreField, (value.presence_mask & 16ull) != 0, value.peer_core)) return false;
    if (!visitor(kTrafficAggregateKeyProfileIdField, (value.presence_mask & 4ull) != 0, value.profile_id)) return false;
    if (!visitor(kTrafficAggregateKeyTensorRoleField, (value.presence_mask & 64ull) != 0, value.tensor_role)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kTrafficChannelTotalChannelField = {"channel", SemanticFieldKind::Enum, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficChannelTotalVnetField = {"vnet", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficChannelTotalMessagesField = {"messages", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficChannelTotalPacketsField = {"packets", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficChannelTotalFlitsField = {"flits", SemanticFieldKind::U64, 0ull, nullptr, 0, false};
inline constexpr SemanticFieldDescriptor kTrafficChannelTotalWireBytesField = {"wire_bytes", SemanticFieldKind::U64, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TrafficChannelTotal &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficChannelTotalChannelField, true, value.channel)) return false;
    if (!visitor(kTrafficChannelTotalVnetField, true, value.vnet)) return false;
    if (!visitor(kTrafficChannelTotalMessagesField, true, value.messages)) return false;
    if (!visitor(kTrafficChannelTotalPacketsField, true, value.packets)) return false;
    if (!visitor(kTrafficChannelTotalFlitsField, true, value.flits)) return false;
    if (!visitor(kTrafficChannelTotalWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TrafficChannelTotal &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficChannelTotalChannelField, true, value.channel)) return false;
    if (!visitor(kTrafficChannelTotalFlitsField, true, value.flits)) return false;
    if (!visitor(kTrafficChannelTotalMessagesField, true, value.messages)) return false;
    if (!visitor(kTrafficChannelTotalPacketsField, true, value.packets)) return false;
    if (!visitor(kTrafficChannelTotalVnetField, true, value.vnet)) return false;
    if (!visitor(kTrafficChannelTotalWireBytesField, true, value.wire_bytes)) return false;
    return true;
}

inline constexpr SemanticFieldDescriptor kTrafficReportBindingIdentitySha256Field = {"binding_identity_sha256", SemanticFieldKind::String, 0ull, nullptr, 0, false};
inline constexpr std::array<uint16_t, 1> kTrafficReportDescriptorsTargets = {348};
inline constexpr SemanticFieldDescriptor kTrafficReportDescriptorsField = {"descriptors", SemanticFieldKind::RefList, 0ull, kTrafficReportDescriptorsTargets.data(), 1, false};
inline constexpr std::array<uint16_t, 1> kTrafficReportAggregatesTargets = {349};
inline constexpr SemanticFieldDescriptor kTrafficReportAggregatesField = {"aggregates", SemanticFieldKind::RefList, 0ull, kTrafficReportAggregatesTargets.data(), 1, false};
inline constexpr SemanticFieldDescriptor kTrafficReportSemanticSha256Field = {"semantic_sha256", SemanticFieldKind::String, 0ull, nullptr, 0, false};

template <typename Visitor> bool visitSemanticFieldsWire(const TrafficReport &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficReportBindingIdentitySha256Field, true, value.binding_identity_sha256)) return false;
    if (!visitor(kTrafficReportDescriptorsField, true, value.descriptors)) return false;
    if (!visitor(kTrafficReportAggregatesField, true, value.aggregates)) return false;
    if (!visitor(kTrafficReportSemanticSha256Field, true, value.semantic_sha256)) return false;
    return true;
}

template <typename Visitor> bool visitSemanticFieldsCanonical(const TrafficReport &value, Visitor &&visitor)
{
    (void)value;
    (void)visitor;
    if (!visitor(kTrafficReportAggregatesField, true, value.aggregates)) return false;
    if (!visitor(kTrafficReportBindingIdentitySha256Field, true, value.binding_identity_sha256)) return false;
    if (!visitor(kTrafficReportDescriptorsField, true, value.descriptors)) return false;
    if (!visitor(kTrafficReportSemanticSha256Field, true, value.semantic_sha256)) return false;
    return true;
}

#endif

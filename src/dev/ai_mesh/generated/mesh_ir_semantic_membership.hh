#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_MEMBERSHIP_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_MEMBERSHIP_HH

enum class VariantMembershipTargetSource : uint8_t { Transport, Semantic };
struct VariantMembershipTargetDescriptor { std::string_view field; VariantMembershipTargetSource source; std::string_view program_field; };

inline constexpr std::array<VariantMembershipTargetDescriptor, 24> kVariantMembershipTargets = {{
    {"abi_tensors", VariantMembershipTargetSource::Transport, "tensors"},
    {"runtime_shards", VariantMembershipTargetSource::Transport, "shards"},
    {"allocations", VariantMembershipTargetSource::Transport, "allocations"},
    {"streams", VariantMembershipTargetSource::Semantic, "streams"},
    {"commands", VariantMembershipTargetSource::Transport, "commands"},
    {"events", VariantMembershipTargetSource::Transport, "events"},
    {"descriptors", VariantMembershipTargetSource::Transport, "dma_descriptors"},
    {"relocations", VariantMembershipTargetSource::Transport, "relocations"},
    {"kernel_tensors", VariantMembershipTargetSource::Semantic, "kernel_tensors"},
    {"computations", VariantMembershipTargetSource::Semantic, "computations"},
    {"placements", VariantMembershipTargetSource::Semantic, "placements"},
    {"logical_shards", VariantMembershipTargetSource::Semantic, "logical_shards"},
    {"partial_sums", VariantMembershipTargetSource::Semantic, "partial_sums"},
    {"objects", VariantMembershipTargetSource::Semantic, "objects"},
    {"views", VariantMembershipTargetSource::Semantic, "views"},
    {"states", VariantMembershipTargetSource::Semantic, "states"},
    {"tokens", VariantMembershipTargetSource::Semantic, "tokens"},
    {"kernel_ops", VariantMembershipTargetSource::Semantic, "kernel_ops"},
    {"object_backings", VariantMembershipTargetSource::Semantic, "object_backings"},
    {"command_semantics", VariantMembershipTargetSource::Semantic, "command_semantics"},
    {"barrier_groups", VariantMembershipTargetSource::Semantic, "barrier_groups"},
    {"dependencies", VariantMembershipTargetSource::Semantic, "dependencies"},
    {"endpoint_uses", VariantMembershipTargetSource::Semantic, "endpoint_uses"},
    {"binding_slots", VariantMembershipTargetSource::Semantic, "binding_slots"},
}};

template <typename Visitor> bool visitVariantMembershipTargets(const VariantMembership &value, Visitor &&visitor)
{
    if (!visitor(kVariantMembershipTargets[0], value.abi_tensors)) return false;
    if (!visitor(kVariantMembershipTargets[1], value.runtime_shards)) return false;
    if (!visitor(kVariantMembershipTargets[2], value.allocations)) return false;
    if (!visitor(kVariantMembershipTargets[3], value.streams)) return false;
    if (!visitor(kVariantMembershipTargets[4], value.commands)) return false;
    if (!visitor(kVariantMembershipTargets[5], value.events)) return false;
    if (!visitor(kVariantMembershipTargets[6], value.descriptors)) return false;
    if (!visitor(kVariantMembershipTargets[7], value.relocations)) return false;
    if (!visitor(kVariantMembershipTargets[8], value.kernel_tensors)) return false;
    if (!visitor(kVariantMembershipTargets[9], value.computations)) return false;
    if (!visitor(kVariantMembershipTargets[10], value.placements)) return false;
    if (!visitor(kVariantMembershipTargets[11], value.logical_shards)) return false;
    if (!visitor(kVariantMembershipTargets[12], value.partial_sums)) return false;
    if (!visitor(kVariantMembershipTargets[13], value.objects)) return false;
    if (!visitor(kVariantMembershipTargets[14], value.views)) return false;
    if (!visitor(kVariantMembershipTargets[15], value.states)) return false;
    if (!visitor(kVariantMembershipTargets[16], value.tokens)) return false;
    if (!visitor(kVariantMembershipTargets[17], value.kernel_ops)) return false;
    if (!visitor(kVariantMembershipTargets[18], value.object_backings)) return false;
    if (!visitor(kVariantMembershipTargets[19], value.command_semantics)) return false;
    if (!visitor(kVariantMembershipTargets[20], value.barrier_groups)) return false;
    if (!visitor(kVariantMembershipTargets[21], value.dependencies)) return false;
    if (!visitor(kVariantMembershipTargets[22], value.endpoint_uses)) return false;
    if (!visitor(kVariantMembershipTargets[23], value.binding_slots)) return false;
    return true;
}

#endif

#include "dev/ai_mesh/mesh_ir_backing.hh"

#include <algorithm>
#include <limits>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{

class BackingFactIdentity
{};

struct ProgramBackingBuilder
{
    static bool validateExternal(
        const DecodedProgram &, const RuntimeArch &,
        const mesh_abi::semantic_abi::BufferObject &,
        const mesh_abi::semantic_abi::ExternalSlotBacking &,
        const ProgramSemanticContext::MembershipRange &,
        const std::map<uint64_t, const mesh_abi::Relocation *> &,
        const ProgramSemanticContext &, bool, ExternalBackingAddressFact &,
        uint64_t &, std::string &, MeshLoadError &);
    static bool validateVariant(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        uint64_t, VerifiedProgramBacking &, MeshLoadError &);
    static bool run(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        VerifiedProgramBacking &, MeshLoadError &);
};

namespace
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

bool
add(uint64_t left, uint64_t right, uint64_t &out)
{
    if (right > std::numeric_limits<uint64_t>::max() - left)
        return false;
    out = left + right;
    return true;
}

bool
powerOfTwo(uint64_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

template <typename Record>
const Record *
semanticRow(const SemanticRef &reference, const std::vector<Record> &rows)
{
    using Traits = SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > rows.size())
        return nullptr;
    return &rows[reference.row_id - 1];
}

const std::string *
semanticString(const DecodedProgram &program, const StringRef &reference)
{
    if (reference.string_id == 0 ||
        reference.string_id > program.semantic_strings.size())
        return nullptr;
    return &program.semantic_strings[reference.string_id - 1];
}

bool
semanticValues(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<uint64_t> &out, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_u64_values.size()))
        return fail(E_ABI_BOUNDS, "semantic integer span is out of bounds",
                    error);
    out.assign(
        program.semantic_u64_values.begin() + size_t(span.begin),
        program.semantic_u64_values.begin() +
            size_t(span.begin) + size_t(span.count));
    return true;
}

bool
compareVector(
    const DecodedProgram &program, const ListSpan &span,
    const std::array<uint64_t, 8> &actual, uint16_t rank,
    MeshLoadError &error)
{
    std::vector<uint64_t> expected;
    if (!semanticValues(program, span, expected, error))
        return false;
    if (expected.size() != rank || rank > actual.size())
        return false;
    for (size_t index = 0; index < actual.size(); ++index) {
        const uint64_t value = index < expected.size() ? expected[index] : 0;
        if (actual[index] != value)
            return false;
    }
    return true;
}

bool
membershipSet(
    const ProgramSemanticContext::MembershipRange &membership,
    std::set<uint64_t> &out, MeshLoadError &error)
{
    if (membership.count >
        std::numeric_limits<uint64_t>::max() - membership.first)
        return fail(E_ABI_OVERFLOW, "membership end overflows", error);
    out.clear();
    for (uint64_t value = membership.first;
         value < membership.first + membership.count; ++value)
        out.insert(value);
    return true;
}

template <typename Record, typename Identity>
bool
indexMembershipRows(
    const std::vector<Record> &records,
    const ProgramSemanticContext::MembershipRange &membership,
    Identity identity, std::map<uint64_t, const Record *> &out,
    MeshLoadError &error)
{
    if (membership.first == 0 ||
        !spanFits(
            membership.first - 1, membership.count, records.size()))
        return fail(E_ABI_BOUNDS, "membership rows are out of bounds", error);
    out.clear();
    for (uint64_t index = 0; index < membership.count; ++index) {
        const Record *row = &records[size_t(membership.first - 1 + index)];
        if (!out.emplace(identity(*row), row).second)
            return fail(E_ABI_BOUNDS,
                        "variant membership records are duplicated", error);
    }
    return true;
}

bool
validateLocal(
    const BufferObject &object, const LocalAllocationBacking &local,
    const ProgramSemanticContext::MembershipRange &allocations,
    const ProgramSemanticContext &context, const RuntimeArch &arch,
    const Allocation *&allocationOut, MeshLoadError &error)
{
    if (object.memory_space != semantic_abi::MemorySpace::CORE_SRAM ||
        local.allocation_id == 0 || local.allocation_id > UINT32_MAX ||
        !allocations.contains(local.allocation_id))
        return fail(E_ABI_BOUNDS, "local backing is outside its variant",
                    error);
    const auto allocation = context.allocations().find(local.allocation_id);
    if (allocation == context.allocations().end())
        return fail(E_ABI_BOUNDS, "local backing allocation is missing",
                    error);
    const Allocation &value = *allocation->second;
    uint64_t end = 0;
    if (!add(value.offset_bytes, value.size_bytes, end))
        return fail(E_ABI_OVERFLOW, "local allocation end overflows", error);
    if (!arch.hasCore(value.owner_core) ||
        value.memory_space != kMemorySpaceCORE_SRAM ||
        value.owner_core != object.owner_core ||
        value.size_bytes < object.footprint_bytes ||
        !powerOfTwo(value.alignment_bytes) ||
        value.alignment_bytes < object.alignment_bytes ||
        value.offset_bytes % value.alignment_bytes != 0 ||
        end > arch.sram_bytes ||
        value.flags != uint32_t(object.persistent))
        return fail(E_ABI_BOUNDS, "local allocation is not its object backing",
                    error);
    allocationOut = &value;
    return true;
}

}

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;

bool
ProgramBackingBuilder::validateExternal(
    const DecodedProgram &program, const RuntimeArch &arch,
    const BufferObject &object, const ExternalSlotBacking &external,
    const ProgramSemanticContext::MembershipRange &slots,
    const std::map<uint64_t, const Relocation *> &relocations,
    const ProgramSemanticContext &context, bool written,
    ExternalBackingAddressFact &out, uint64_t &slotId,
    std::string &symbol, MeshLoadError &error)
{
    if (object.memory_space != semantic_abi::MemorySpace::HBM &&
        object.memory_space != semantic_abi::MemorySpace::HOST_SHARED)
        return fail(E_ABI_BOUNDS,
                    "external slot is not its object backing", error);
    const auto relocationIt = relocations.find(external.slot_id);
    if (external.slot_id == 0 || external.slot_id > UINT32_MAX ||
        !slots.contains(external.slot_id) ||
        relocationIt == relocations.end())
        return fail(E_RELOCATION, "external backing is outside its variant",
                    error);
    const auto slotIt = context.bindingSlots().find(external.slot_id);
    if (slotIt == context.bindingSlots().end())
        return fail(E_RELOCATION, "external backing record is missing", error);
    const BindingSlot &slot = *slotIt->second;
    const Relocation &relocation = *relocationIt->second;
    const Binding *binding = semanticRow(
        slot.reference_binding, program.semantic_tables.binding_rows);
    const std::string *slotSymbol = semanticString(program, slot.symbol);
    if (!binding || !slotSymbol || slotSymbol->empty() ||
        relocation.symbol_sid == 0 ||
        relocation.symbol_sid > program.transport.strings.size() ||
        program.transport.strings[relocation.symbol_sid - 1] != *slotSymbol ||
        object.storage_tensor_id == 0 ||
        object.storage_tensor_id > UINT32_MAX)
        return fail(E_RELOCATION, "external binding identity is invalid",
                    error);
    if (slot.region_id > UINT16_MAX || slot.owner_core > UINT16_MAX)
        return fail(E_RELOCATION, "external binding identity is out of range",
                    error);
    const RuntimeArch::Region *region = arch.region(uint16_t(slot.region_id));
    const bool slotRegionMatches = region &&
        ((region->is_sram_aperture &&
          arch.hasCore(uint16_t(slot.owner_core)) &&
          slot.memory_space ==
              (slot.owner_core == arch.core_ids.front()
                  ? semantic_abi::MemorySpace::CORE_SRAM
                  : semantic_abi::MemorySpace::PEER_SRAM)) ||
         (!region->is_sram_aperture && slot.owner_core == UINT16_MAX &&
          ((region->kind == RuntimeArch::Region::kKindHbm &&
            slot.memory_space == semantic_abi::MemorySpace::HBM) ||
           (region->kind == RuntimeArch::Region::kKindHostShared &&
            slot.memory_space == semantic_abi::MemorySpace::HOST_SHARED))));
    if (!slotRegionMatches ||
        !powerOfTwo(slot.required_allocation_alignment_bytes))
        return fail(E_RELOCATION, "external binding address is invalid", error);
    if (slot.memory_space != object.memory_space ||
        slot.owner_core != object.owner_core ||
        slot.required_allocation_bytes < object.footprint_bytes ||
        slot.required_allocation_alignment_bytes < object.alignment_bytes ||
        (written && slot.access != Access::READ_WRITE))
        return fail(E_ABI_BOUNDS,
                    "external slot is not its object backing", error);
    if (
        binding->slot_id != slot.slot_id ||
        binding->region_id != slot.region_id ||
        binding->owner_core != slot.owner_core ||
        binding->access != slot.access ||
        !powerOfTwo(binding->allocation_alignment_bytes) ||
        binding->allocation_size_bytes < slot.required_allocation_bytes ||
        binding->allocation_alignment_bytes <
            slot.required_allocation_alignment_bytes ||
        binding->allocation_alignment_bytes %
            slot.required_allocation_alignment_bytes != 0 ||
        relocation.relocation_id != external.slot_id ||
        relocation.kind != kRelocationKindTENSOR_BASE ||
        relocation.region_id != slot.region_id ||
        relocation.tensor_id != object.storage_tensor_id ||
        relocation.offset_bytes != binding->allocation_offset_bytes)
        return fail(E_RELOCATION, "external backing is inconsistent", error);
    uint64_t address = 0;
    uint64_t addressEnd = 0;
    if (!add(region->base, binding->allocation_offset_bytes, address))
        return fail(E_ABI_OVERFLOW,
                    "external binding address overflows", error);
    if (!add(address, binding->allocation_size_bytes, addressEnd))
        return fail(E_ABI_OVERFLOW,
                    "external binding address end overflows", error);
    if (addressEnd - region->base > region->bytes ||
        address % binding->allocation_alignment_bytes != 0)
        return fail(E_RELOCATION, "external binding range is invalid", error);
    out.binding_ = binding;
    out.region_ = region;
    out.allocationAddress_ = address;
    slotId = external.slot_id;
    symbol = *slotSymbol;
    return true;
}

bool
validateResident(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const std::map<uint64_t, const Shard *> &runtimeShards,
    const ProgramSemanticContext::MembershipRange &views,
    const ProgramSemanticContext::MembershipRange &objects,
    const ProgramSemanticContext::MembershipRange &logicalShards,
    const ProgramSemanticContext::MembershipRange &tensors,
    const BufferView &view, const Allocation *allocation,
    const Shard *&out, MeshLoadError &error)
{
    if (!views.contains(view.view_id) || !objects.contains(view.object_id) ||
        !logicalShards.contains(view.shard_id))
        return fail(E_ABI_BOUNDS, "resident view crosses its variant", error);
    const auto resident = context.residents().find(view.view_id);
    const auto object = context.objects().find(view.object_id);
    const auto shard = context.logicalShards().find(view.shard_id);
    if (resident == context.residents().end() ||
        object == context.objects().end() ||
        shard == context.logicalShards().end() ||
        resident->second->runtime_shard_id == 0 ||
        resident->second->runtime_shard_id > UINT32_MAX ||
        runtimeShards.find(resident->second->runtime_shard_id) ==
            runtimeShards.end() ||
        !tensors.contains(shard->second->tensor_id))
        return fail(E_ABI_BOUNDS, "resident view source is invalid", error);
    const auto runtime = runtimeShards.find(resident->second->runtime_shard_id);
    const auto tensor = context.tensors().find(shard->second->tensor_id);
    if (tensor == context.tensors().end())
        return fail(E_ABI_BOUNDS, "resident tensor is missing", error);
    std::vector<uint64_t> padded;
    if (!semanticValues(program, view.padded_shape, padded, error))
        return false;
    uint64_t width = 0;
    if (!dtypeByteWidth(tensor->second->dtype, width))
        return fail(E_ABI_BOUNDS, "resident tensor dtype is invalid", error);
    uint64_t expectedOffset = 0;
    if (view.object_offset_elements != 0 &&
        width > std::numeric_limits<uint64_t>::max() /
            view.object_offset_elements)
        return fail(E_ABI_OVERFLOW, "resident view byte offset overflows",
                    error);
    expectedOffset = view.object_offset_elements * width;
    const Shard &actual = *runtime->second;
    const uint32_t expectedAllocation = allocation
        ? allocation->allocation_id : 0;
    if (actual.tensor_id != shard->second->tensor_id ||
        actual.owner_core != object->second->owner_core ||
        actual.rank != padded.size() ||
        actual.allocation_id != expectedAllocation ||
        actual.allocation_offset != expectedOffset ||
        actual.span_bytes != object->second->footprint_bytes ||
        !compareVector(
            program, shard->second->global_origin, actual.global_origin,
            actual.rank, error) ||
        !compareVector(
            program, view.padded_shape, actual.local_shape, actual.rank,
            error) ||
        !compareVector(
            program, view.valid_shape, actual.valid_shape, actual.rank,
            error))
        return fail(E_ABI_BOUNDS,
                    "runtime shard is not the resident view projection",
                    error);
    out = &actual;
    return true;
}

bool
ProgramBackingBuilder::validateVariant(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry, uint64_t variantId,
    VerifiedProgramBacking &candidate, MeshLoadError &error)
{
    const auto objects = context.membership(variantId, "objects");
    const auto views = context.membership(variantId, "views");
    const auto logicalShards = context.membership(variantId, "logical_shards");
    const auto tensors = context.membership(variantId, "kernel_tensors");
    const auto operations = context.membership(variantId, "kernel_ops");
    const auto objectBackings = context.membership(
        variantId, "object_backings");
    const auto allocations = context.membership(variantId, "allocations");
    const auto runtimeShards = context.membership(
        variantId, "runtime_shards");
    const auto bindingSlots = context.membership(variantId, "binding_slots");
    const auto relocations = context.membership(variantId, "relocations");
    if (!objects || !views || !logicalShards || !tensors || !operations ||
        !objectBackings || !allocations || !runtimeShards ||
        !bindingSlots || !relocations)
        return fail(E_ABI_BOUNDS, "backing membership is invalid", error);

    std::map<uint64_t, const Shard *> runtimeShardsById;
    std::map<uint64_t, const Relocation *> relocationsById;
    if (!indexMembershipRows(
            program.transport.shards, *runtimeShards,
            [](const Shard &row) { return uint64_t(row.shard_id); },
            runtimeShardsById, error) ||
        !indexMembershipRows(
            program.transport.relocations, *relocations,
            [](const Relocation &row) { return uint64_t(row.relocation_id); },
            relocationsById, error))
        return false;
    if (runtimeShardsById.size() != runtimeShards->count ||
        relocationsById.size() != relocations->count)
        return fail(E_ABI_BOUNDS,
                    "variant transport backing records are duplicated", error);

    std::set<uint64_t> expectedObjects;
    std::set<uint64_t> expectedAllocations;
    std::set<uint64_t> expectedSlots;
    std::set<uint64_t> expectedRelocations;
    if (!membershipSet(*objects, expectedObjects, error) ||
        !membershipSet(*allocations, expectedAllocations, error) ||
        !membershipSet(*bindingSlots, expectedSlots, error))
        return false;
    for (const auto &[id, relocation] : relocationsById)
        expectedRelocations.insert(id);

    std::set<uint64_t> writtenObjects;
    if (operations->count >
        std::numeric_limits<uint64_t>::max() - operations->first)
        return fail(E_ABI_OVERFLOW, "operation membership end overflows",
                    error);
    for (uint64_t operationId = operations->first;
         operationId < operations->first + operations->count; ++operationId) {
        const size_t count = geometry.accessCount(
            operationId, GeometryAccessRole::Write);
        for (size_t ordinal = 0; ordinal < count; ++ordinal) {
            const GeometryAccessFact *access = geometry.access(
                operationId, GeometryAccessRole::Write, ordinal);
            if (!access)
                return fail(E_ABI_BOUNDS, "geometry write access is missing",
                            error);
            writtenObjects.insert(access->object().object_id);
        }
    }

    std::set<uint64_t> observedAllocations;
    std::set<uint64_t> observedSlots;
    std::set<uint64_t> observedRelocations;
    std::vector<const Binding *> bindings;
    std::set<std::string> symbols;
    for (const uint64_t objectId : expectedObjects) {
        const auto object = context.objects().find(objectId);
        const auto backing = context.backings().find(objectId);
        if (object == context.objects().end() ||
            backing == context.backings().end() ||
            !objectBackings->contains(objectId))
            return fail(E_ABI_BOUNDS, "object backing is missing", error);
        const LocalAllocationBacking *local = semanticRow(
            backing->second->backing,
            program.semantic_tables.local_allocation_backing_rows);
        const ExternalSlotBacking *external = semanticRow(
            backing->second->backing,
            program.semantic_tables.external_slot_backing_rows);
        if (local) {
            const Allocation *allocation = nullptr;
            if (!validateLocal(
                    *object->second, *local, *allocations, context, arch,
                    allocation, error))
                return false;
            if (!observedAllocations.insert(allocation->allocation_id).second)
                return fail(E_ABI_BOUNDS,
                            "local allocation is assigned more than once",
                            error);
            candidate.locals_.emplace(objectId, allocation);
        } else if (external) {
            ExternalBackingAddressFact fact;
            uint64_t slotId = 0;
            std::string symbol;
            if (!validateExternal(
                    program, arch, *object->second, *external, *bindingSlots,
                    relocationsById, context, writtenObjects.count(objectId),
                    fact, slotId, symbol, error))
                return false;
            if (!observedSlots.insert(slotId).second)
                return fail(E_RELOCATION,
                            "external slot is assigned more than once", error);
            if (!observedRelocations.insert(slotId).second ||
                !symbols.insert(std::move(symbol)).second)
                return fail(E_RELOCATION,
                            "external relocation is duplicated", error);
            bindings.push_back(&fact.binding());
            candidate.externals_.emplace(objectId, std::move(fact));
        } else {
            return fail(E_RELOCATION, "object backing kind is invalid", error);
        }
    }
    if (observedAllocations != expectedAllocations ||
        observedSlots != expectedSlots)
        return fail(E_ABI_BOUNDS, "backing associations are incomplete",
                    error);
    if (observedRelocations != expectedRelocations)
        return fail(E_RELOCATION,
                    "relocations do not exactly cover external slots", error);

    for (size_t index = 0; index < bindings.size(); ++index) {
        uint64_t leftEnd = 0;
        if (!add(bindings[index]->allocation_offset_bytes,
                 bindings[index]->allocation_size_bytes, leftEnd))
            return fail(E_ABI_OVERFLOW,
                        "external binding end overflows", error);
        for (size_t other = index + 1; other < bindings.size(); ++other) {
            uint64_t rightEnd = 0;
            if (!add(bindings[other]->allocation_offset_bytes,
                     bindings[other]->allocation_size_bytes, rightEnd))
                return fail(E_ABI_OVERFLOW,
                            "external binding end overflows", error);
            if (bindings[index]->region_id == bindings[other]->region_id &&
                bindings[index]->owner_core == bindings[other]->owner_core &&
                std::max(bindings[index]->allocation_offset_bytes,
                         bindings[other]->allocation_offset_bytes) <
                    std::min(leftEnd, rightEnd) &&
                (bindings[index]->access == Access::READ_WRITE ||
                 bindings[other]->access == Access::READ_WRITE))
                return fail(E_RELOCATION, "writable bindings overlap", error);
        }
    }

    std::set<uint64_t> observedRuntimeShards;
    if (views->count > std::numeric_limits<uint64_t>::max() - views->first)
        return fail(E_ABI_OVERFLOW, "view membership end overflows", error);
    for (uint64_t viewId = views->first;
         viewId < views->first + views->count; ++viewId) {
        const auto view = context.views().find(viewId);
        if (view == context.views().end())
            return fail(E_ABI_BOUNDS, "resident view is missing", error);
        const auto local = candidate.locals_.find(view->second->object_id);
        const Allocation *allocation = local == candidate.locals_.end()
            ? nullptr : local->second;
        const Shard *runtime = nullptr;
        if (!validateResident(
                program, context, runtimeShardsById, *views, *objects,
                *logicalShards, *tensors, *view->second, allocation, runtime,
                error))
            return false;
        const auto resident = context.residents().find(viewId);
        if (!observedRuntimeShards.insert(
                resident->second->runtime_shard_id).second)
            return fail(E_ABI_BOUNDS,
                        "runtime shard is assigned more than once", error);
        candidate.residents_.emplace(viewId, runtime);
    }
    std::set<uint64_t> expectedRuntimeShards;
    for (const auto &[id, shard] : runtimeShardsById)
        expectedRuntimeShards.insert(id);
    if (observedRuntimeShards != expectedRuntimeShards)
        return fail(E_ABI_BOUNDS, "resident associations are incomplete",
                    error);
    return true;
}

bool
ProgramBackingBuilder::run(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    VerifiedProgramBacking &out, MeshLoadError &error)
{
    VerifiedProgramBacking candidate;
    candidate.program_ = &program;
    candidate.context_ = &context;
    candidate.arch_ = &arch;
    candidate.captureGeometry(geometry);
    if (!candidate.geometryIdentity_)
        return fail(E_ABI_BOUNDS, "geometry facts are not admitted", error);
    for (const auto &[variantId, variant] : context.variants()) {
        if (!variant ||
            !validateVariant(
                program, arch, context, geometry, variantId, candidate,
                error))
            return false;
    }
    candidate.identity_ = std::make_shared<BackingFactIdentity>();
    out = std::move(candidate);
    error = {};
    return true;
}

const Binding &
ExternalBackingAddressFact::binding() const
{
    return *binding_;
}

const RuntimeArch::Region &
ExternalBackingAddressFact::region() const
{
    return *region_;
}

uint64_t
ExternalBackingAddressFact::allocationAddress() const
{
    return allocationAddress_;
}

VerifiedProgramBacking::~VerifiedProgramBacking() = default;

VerifiedProgramBacking::VerifiedProgramBacking(
    VerifiedProgramBacking &&other) noexcept
    : program_(other.program_), context_(other.context_), arch_(other.arch_),
      geometryIdentity_(std::move(other.geometryIdentity_)),
      identity_(std::move(other.identity_)), locals_(std::move(other.locals_)),
      externals_(std::move(other.externals_)),
      residents_(std::move(other.residents_))
{
    other.program_ = nullptr;
    other.context_ = nullptr;
    other.arch_ = nullptr;
    other.locals_.clear();
    other.externals_.clear();
    other.residents_.clear();
}

VerifiedProgramBacking &
VerifiedProgramBacking::operator=(VerifiedProgramBacking &&other) noexcept
{
    if (this == &other)
        return *this;
    program_ = other.program_;
    context_ = other.context_;
    arch_ = other.arch_;
    geometryIdentity_ = std::move(other.geometryIdentity_);
    identity_ = std::move(other.identity_);
    locals_ = std::move(other.locals_);
    externals_ = std::move(other.externals_);
    residents_ = std::move(other.residents_);
    other.program_ = nullptr;
    other.context_ = nullptr;
    other.arch_ = nullptr;
    other.locals_.clear();
    other.externals_.clear();
    other.residents_.clear();
    return *this;
}

bool
VerifiedProgramBacking::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const RuntimeArch &arch, const VerifiedProgramGeometry &geometry) const
{
    return identity_ && geometryIdentity_ &&
        program_ == &program && context_ == &context && arch_ == &arch &&
        context.matches(program) && geometry.matches(program, context) &&
        geometry.matchesInvocation(geometryIdentity_);
}

const Allocation *
VerifiedProgramBacking::local(uint64_t objectId) const
{
    if (!identity_)
        return nullptr;
    const auto found = locals_.find(objectId);
    return found == locals_.end() ? nullptr : found->second;
}

const ExternalBackingAddressFact *
VerifiedProgramBacking::external(uint64_t objectId) const
{
    if (!identity_)
        return nullptr;
    const auto found = externals_.find(objectId);
    return found == externals_.end() ? nullptr : &found->second;
}

const Shard *
VerifiedProgramBacking::resident(uint64_t viewId) const
{
    if (!identity_)
        return nullptr;
    const auto found = residents_.find(viewId);
    return found == residents_.end() ? nullptr : found->second;
}

void
VerifiedProgramBacking::captureGeometry(const VerifiedProgramGeometry &geometry)
{
    geometryIdentity_ = geometry.invocationIdentity();
}

std::shared_ptr<const void>
VerifiedProgramBacking::invocationIdentity() const
{
    return identity_;
}

bool
VerifiedProgramBacking::matchesInvocation(
    const std::shared_ptr<const void> &identity) const
{
    return identity_ && identity && identity_.get() == identity.get();
}

bool
verifyProgramBacking(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    VerifiedProgramBacking &out, MeshLoadError &error)
{
    error = {};
    if (!context.matches(program) || !geometry.matches(program, context))
        return fail(E_ABI_BOUNDS, "backing facts belong to another Program",
                    error);
    return ProgramBackingBuilder::run(
        program, arch, context, geometry, out, error);
}

}
}

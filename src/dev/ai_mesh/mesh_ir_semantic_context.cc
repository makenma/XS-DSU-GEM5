#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

#include <set>
#include <type_traits>
#include <utility>

#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{
class ProgramSemanticContextIdentity
{};

namespace
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
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
isDigest(const std::string &value)
{
    if (value.size() != 64)
        return false;
    for (const char character : value) {
        if (!(character >= '0' && character <= '9') &&
            !(character >= 'a' && character <= 'f'))
            return false;
    }
    return true;
}

template <typename Record>
bool
indexDenseRootRecords(
    const DecodedProgram &program, const ListSpan &span,
    const std::vector<Record> &rows, std::map<uint64_t, const Record *> &out,
    uint64_t Record::*identity, const ProgramSemanticContext *context,
    std::string_view membershipField, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "semantic root list is out of bounds",
                    error);
    for (uint64_t offset = 0; offset < span.count; ++offset) {
        const Record *record = semanticRow(
            program.semantic_references[size_t(span.begin + offset)], rows);
        if (!record)
            return fail("E_ABI_BOUNDS", "semantic identity is invalid", error);
        if (context) {
            const ProgramVariant *positionOwner = context->owner(
                membershipField, offset + 1);
            if (!positionOwner || context->owner(
                    membershipField, record->*identity) != positionOwner)
                return fail("E_ABI_BOUNDS",
                            "semantic identity crosses its variant", error);
        }
        if (record->*identity != offset + 1)
            return fail("E_ABI_ORDER", "semantic identities are not dense",
                        error);
        out.emplace(record->*identity, record);
    }
    return true;
}

template <typename Record>
bool
indexRootRecords(
    const DecodedProgram &program, const ListSpan &span,
    const std::vector<Record> &rows, std::map<uint64_t, const Record *> &out,
    uint64_t Record::*identity, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "semantic root list is out of bounds",
                    error);
    for (uint64_t offset = 0; offset < span.count; ++offset) {
        const Record *record = semanticRow(
            program.semantic_references[size_t(span.begin + offset)], rows);
        if (!record || record->*identity == 0)
            return fail("E_ABI_BOUNDS", "semantic identity is invalid", error);
        if (!out.emplace(record->*identity, record).second)
            return fail("E_ABI_BOUNDS", "semantic identity is duplicated",
                        error);
    }
    return true;
}

bool
validateResidentRuntimeShardIds(
    const DecodedProgram &program, const ListSpan &span,
    const std::vector<ResidentView> &rows, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "semantic root list is out of bounds",
                    error);
    if (span.count != program.transport.shards.size())
        return fail("E_ABI_ORDER",
                    "resident views and runtime shards are not one-to-one",
                    error);
    for (uint64_t offset = 0; offset < span.count; ++offset) {
        const ResidentView *resident = semanticRow(
            program.semantic_references[size_t(span.begin + offset)], rows);
        if (!resident)
            return fail("E_ABI_BOUNDS", "resident view is invalid", error);
        if (resident->runtime_shard_id != offset + 1)
            return fail("E_ABI_ORDER",
                        "resident runtime shard identities are not dense",
                        error);
    }
    return true;
}

bool
collectSemanticTargetCounts(
    const DecodedProgram &program, const ProgramSemantics &root,
    std::map<std::string, uint64_t> &out, MeshLoadError &error)
{
    bool valid = true;
    visitSemanticFieldsWire(
        root,
        [&](const SemanticFieldDescriptor &field, bool present,
            const auto &value) {
            using Value = std::decay_t<decltype(value)>;
            if constexpr (std::is_same_v<Value, ListSpan>) {
                if (field.kind != SemanticFieldKind::RefList || !present)
                    return true;
                if (!spanFits(value.begin, value.count,
                              program.semantic_references.size())) {
                    valid = false;
                    return false;
                }
                out.emplace(std::string(field.name), value.count);
            }
            return true;
        });
    if (!valid)
        return fail("E_ABI_BOUNDS", "semantic root list is out of bounds",
                    error);
    return true;
}

bool
collectTransportTargetCounts(
    const DecodedProgram &program, std::map<std::string, uint64_t> &out)
{
    return visitTransportTablesWire(
        program.transport,
        [&](const TransportSectionDescriptor &field, const auto &rows) {
            return out.emplace(
                std::string(field.program_field), uint64_t(rows.size())).second;
        });
}

}

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;

struct ProgramSemanticContext::Builder
{
static bool
indexCommandRecords(
    const DecodedProgram &program, ProgramSemanticContext &out,
    MeshLoadError &error)
{
    std::set<uint32_t> commandIds;
    for (const Command &command : program.transport.commands) {
        if (!commandIds.insert(command.command_id).second)
            return fail("E_ABI_DUPLICATE", "command identity is duplicated",
                        error);
    }
    for (size_t index = 0; index < program.transport.commands.size(); ++index) {
        const Command &command = program.transport.commands[index];
        if (command.command_id != index + 1)
            return fail("E_ABI_ORDER", "commands must be densely ordered",
                        error);
        out.commands_.emplace(command.command_id, &command);
    }
    return true;
}

static bool
indexOtherTransportRecords(
    const DecodedProgram &program, ProgramSemanticContext &out,
    MeshLoadError &error)
{
    for (size_t index = 0;
         index < program.transport.dma_descriptors.size(); ++index) {
        const DmaDescriptor &descriptor =
            program.transport.dma_descriptors[index];
        if (descriptor.descriptor_id != index + 1)
            return fail("E_ABI_ORDER", "DMA descriptor IDs are not dense",
                        error);
        out.descriptors_.emplace(descriptor.descriptor_id, &descriptor);
    }
    for (size_t index = 0; index < program.transport.allocations.size();
         ++index) {
        const Allocation &allocation = program.transport.allocations[index];
        if (allocation.allocation_id != index + 1)
            return fail("E_ABI_ORDER",
                        "allocation identities are not densely ordered",
                        error);
        out.allocations_.emplace(allocation.allocation_id, &allocation);
    }
    std::set<uint32_t> shardIds;
    for (const Shard &shard : program.transport.shards) {
        if (shard.shard_id == 0 || !shardIds.insert(shard.shard_id).second)
            return fail("E_ABI_BOUNDS", "runtime shard identity is invalid",
                        error);
        out.runtimeShards_.emplace(shard.shard_id, &shard);
    }
    for (size_t index = 0; index < program.transport.events.size(); ++index) {
        const Event &event = program.transport.events[index];
        if (event.event_id != index + 1)
            return fail("E_ABI_ORDER", "event identities are not dense",
                        error);
        out.events_.emplace(event.event_id, event.event_id);
    }
    for (const Relocation &relocation : program.transport.relocations) {
        if (relocation.relocation_id == 0)
            return fail("E_RELOCATION",
                        "relocation identity is invalid", error);
        if (!out.relocations_.emplace(
                relocation.relocation_id, &relocation).second)
            return fail("E_ABI_DUPLICATE",
                        "relocation identity is duplicated", error);
    }
    return true;
}

static bool
indexSemanticRecords(
    const DecodedProgram &program, ProgramSemanticContext &out,
    MeshLoadError &error)
{
    const ProgramSemantics &root = *out.root_;
    return
        indexDenseRootRecords(program, root.streams,
                              program.semantic_tables.scheduled_stream_rows,
                              out.streams_, &ScheduledStream::stream_id,
                              nullptr, {}, error) &&
        indexDenseRootRecords(program, root.kernel_ops,
                              program.semantic_tables.kernel_op_rows,
                              out.operations_, &KernelOp::op_id, &out,
                              "kernel_ops", error) &&
        indexDenseRootRecords(program, root.kernel_tensors,
                              program.semantic_tables.kernel_tensor_rows,
                              out.tensors_, &KernelTensor::tensor_id,
                              &out, "kernel_tensors", error) &&
        indexDenseRootRecords(program, root.logical_shards,
                              program.semantic_tables.tensor_shard_rows,
                              out.logicalShards_, &TensorShard::shard_id,
                              &out, "logical_shards", error) &&
        indexDenseRootRecords(program, root.objects,
                              program.semantic_tables.buffer_object_rows,
                              out.objects_, &BufferObject::object_id,
                              &out, "objects", error) &&
        indexDenseRootRecords(program, root.views,
                              program.semantic_tables.buffer_view_rows,
                              out.views_, &BufferView::view_id, &out,
                              "views", error) &&
        indexDenseRootRecords(program, root.states,
                              program.semantic_tables.tensor_state_rows,
                              out.states_, &TensorState::state_id, &out,
                              "states", error) &&
        indexDenseRootRecords(program, root.computations,
                              program.semantic_tables.kernel_computation_rows,
                              out.computations_,
                              &KernelComputation::computation_id, &out,
                              "computations", error) &&
        indexDenseRootRecords(program, root.placements,
                              program.semantic_tables.placement_rows,
                              out.placements_, &Placement::placement_id, &out,
                              "placements", error) &&
        indexDenseRootRecords(program, root.command_semantics,
                              program.semantic_tables.command_semantics_rows,
                              out.commandSemantics_,
                              &CommandSemantics::command_id, nullptr,
                              {}, error) &&
        indexDenseRootRecords(
            program, root.partial_sums,
            program.semantic_tables.partial_sum_definition_rows,
            out.partialSums_, &PartialSumDefinition::partial_sum_id,
            &out, "partial_sums", error) &&
        indexDenseRootRecords(program, root.tokens,
                              program.semantic_tables.control_token_rows,
                              out.tokens_, &ControlToken::token_id, &out,
                              "tokens", error) &&
        indexDenseRootRecords(program, root.barrier_groups,
                              program.semantic_tables.barrier_group_rows,
                              out.barrierGroups_, &BarrierGroup::barrier_group_id,
                              nullptr, {}, error) &&
        indexDenseRootRecords(program, root.dependencies,
                              program.semantic_tables.scheduled_dependency_rows,
                              out.dependencies_, &ScheduledDependency::dependency_id,
                              nullptr, {}, error) &&
        indexRootRecords(program, root.object_backings,
                         program.semantic_tables.object_backing_rows,
                         out.backings_, &ObjectBacking::object_id, error) &&
        validateResidentRuntimeShardIds(
            program, root.resident_views,
            program.semantic_tables.resident_view_rows, error) &&
        indexRootRecords(program, root.resident_views,
                         program.semantic_tables.resident_view_rows,
                         out.residents_, &ResidentView::view_id, error) &&
        indexRootRecords(program, root.binding_slots,
                         program.semantic_tables.binding_slot_rows,
                         out.bindingSlots_, &BindingSlot::slot_id, error);
}

};

namespace
{

enum class ProgramOriginKind
{
    Authored,
    Compiled,
};

bool
admitProgramOrigin(
    const DecodedProgram &program, const ProgramSemantics &root,
    ProgramOriginKind &kind, MeshLoadError &error)
{
    if (const CompiledProgramOrigin *origin = semanticRow(
            root.origin,
            program.semantic_tables.compiled_program_origin_rows)) {
        const std::string *digest = semanticString(
            program, origin->kernel_bundle_semantic_sha256);
        if (!digest || !isDigest(*digest))
            return fail("E_ABI_CHECKSUM",
                        "compiled Program origin digest is malformed", error);
        kind = ProgramOriginKind::Compiled;
        return true;
    }
    if (const AuthoredProgramOrigin *origin = semanticRow(
            root.origin,
            program.semantic_tables.authored_program_origin_rows)) {
        const std::string *namespaceName = semanticString(
            program, origin->namespace_);
        const std::string *name = semanticString(program, origin->name);
        if (!namespaceName || namespaceName->empty() || !name ||
            name->empty() || origin->version == 0)
            return fail("E_ABI_BOUNDS", "authored Program origin is malformed",
                        error);
        kind = ProgramOriginKind::Authored;
        return true;
    }
    return fail("E_ABI_BOUNDS", "Program origin is invalid", error);
}

bool
admitVariantLineage(
    const DecodedProgram &program, const ProgramVariant &variant,
    ProgramOriginKind originKind, std::set<std::string> &authoredIds,
    MeshLoadError &error)
{
    if (originKind == ProgramOriginKind::Compiled) {
        const CompiledVariantLineage *lineage = semanticRow(
            variant.lineage,
            program.semantic_tables.compiled_variant_lineage_rows);
        if (!lineage)
            return fail("E_ABI_CHECKSUM", "compiled variant lineage is malformed",
                        error);
        const std::string *digest = semanticString(
            program, lineage->kernel_module_semantic_sha256);
        if (!digest || !isDigest(*digest))
            return fail("E_ABI_CHECKSUM", "compiled variant lineage is malformed",
                        error);
        return true;
    }
    const AuthoredVariantLineage *lineage = semanticRow(
        variant.lineage, program.semantic_tables.authored_variant_lineage_rows);
    if (!lineage)
        return fail("E_ABI_BOUNDS", "authored variant lineage is malformed",
                    error);
    const std::string *identity = semanticString(
        program, lineage->authoring_variant_id);
    if (!identity || identity->empty())
        return fail("E_ABI_BOUNDS", "authored variant lineage is malformed",
                    error);
    if (!authoredIds.insert(*identity).second)
        return fail("E_ABI_DUPLICATE",
                    "authored Program variants have duplicate lineage identities",
                    error);
    return true;
}

bool
sameOwner(
    const ProgramSemanticContext &context, std::string_view leftField,
    uint64_t leftIdentity, std::string_view rightField,
    uint64_t rightIdentity)
{
    const ProgramVariant *left = context.owner(leftField, leftIdentity);
    return left && left == context.owner(rightField, rightIdentity);
}

template <typename Record>
bool
admitJoinedRootOwnership(
    const DecodedProgram &program, const ListSpan &span,
    const std::vector<Record> &rows, const ProgramSemanticContext &context,
    std::string_view membershipField, std::string_view targetField,
    uint64_t Record::*identity, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "semantic root list is out of bounds",
                    error);
    for (uint64_t offset = 0; offset < span.count; ++offset) {
        const Record *record = semanticRow(
            program.semantic_references[size_t(span.begin + offset)], rows);
        const ProgramVariant *owner = context.owner(
            membershipField, offset + 1);
        if (!record || !owner ||
            owner != context.owner(targetField, record->*identity))
            return fail("E_ABI_BOUNDS",
                        "joined semantic root row crosses its variant", error);
    }
    return true;
}

bool
admitRootReferenceOwnership(
    const DecodedProgram &program, ProgramSemanticContext &context,
    MeshLoadError &error)
{
    const ProgramSemantics &root = context.root();
    if (!admitJoinedRootOwnership(
            program, root.object_backings,
            program.semantic_tables.object_backing_rows, context,
            "object_backings", "objects", &ObjectBacking::object_id, error) ||
        !admitJoinedRootOwnership(
            program, root.binding_slots,
            program.semantic_tables.binding_slot_rows, context,
            "binding_slots", "binding_slots", &BindingSlot::slot_id, error))
        return false;
    for (const auto &[shardId, shard] : context.logicalShards()) {
        if (!sameOwner(context, "logical_shards", shardId, "kernel_tensors",
                       shard->tensor_id) ||
            !sameOwner(context, "logical_shards", shardId, "placements",
                       shard->placement_id) ||
            (shard->partial_sum_id != 0 &&
             !sameOwner(context, "logical_shards", shardId, "partial_sums",
                        shard->partial_sum_id)))
            return fail("E_ABI_BOUNDS",
                        "logical shard references cross its variant", error);
    }
    for (const auto &[objectId, object] : context.objects()) {
        if (!sameOwner(context, "objects", objectId, "kernel_tensors",
                       object->storage_tensor_id))
            return fail("E_ABI_BOUNDS",
                        "buffer object tensor crosses its variant", error);
    }
    for (const auto &[viewId, view] : context.views()) {
        if (!sameOwner(context, "views", viewId, "objects", view->object_id) ||
            !sameOwner(context, "views", viewId, "logical_shards",
                       view->shard_id))
            return fail("E_ABI_BOUNDS",
                        "buffer view references cross its variant", error);
    }
    for (const auto &[stateId, state] : context.states()) {
        if (!sameOwner(context, "states", stateId, "objects",
                       state->object_id) ||
            (state->partial_sum_id != 0 &&
             !sameOwner(context, "states", stateId, "partial_sums",
                        state->partial_sum_id)))
            return fail("E_ABI_BOUNDS",
                        "tensor state references cross its variant", error);
    }
    for (const auto &[objectId, backing] : context.backings()) {
        if (const LocalAllocationBacking *local = semanticRow(
                backing->backing,
                program.semantic_tables.local_allocation_backing_rows)) {
            if (!sameOwner(context, "objects", objectId, "allocations",
                           local->allocation_id))
                return fail("E_ABI_BOUNDS",
                            "local object backing crosses its variant", error);
            continue;
        }
        if (const ExternalSlotBacking *external = semanticRow(
                backing->backing,
                program.semantic_tables.external_slot_backing_rows)) {
            if (!sameOwner(context, "objects", objectId, "binding_slots",
                           external->slot_id))
                return fail("E_ABI_BOUNDS",
                            "external object backing crosses its variant",
                            error);
            continue;
        }
        return fail("E_ABI_BOUNDS", "object backing is invalid", error);
    }
    for (const auto &[slotId, slot] : context.bindingSlots()) {
        const Binding *binding = semanticRow(
            slot->reference_binding, program.semantic_tables.binding_rows);
        if (!binding || !sameOwner(context, "binding_slots", slotId,
                                   "binding_slots", binding->slot_id))
            return fail("E_ABI_BOUNDS",
                        "binding slot reference crosses its variant", error);
    }
    return true;
}

bool
admitDmaOwnership(
    const DecodedProgram &program, ProgramSemanticContext &context,
    MeshLoadError &error)
{
    const ProgramSemantics &root = context.root();
    if (!spanFits(root.descriptor_groups.begin, root.descriptor_groups.count,
                  program.semantic_references.size()) ||
        !spanFits(root.endpoint_uses.begin, root.endpoint_uses.count,
                  program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "DMA semantic list is out of bounds",
                    error);
    for (uint64_t offset = 0; offset < root.descriptor_groups.count; ++offset) {
        const DescriptorGroup *group = semanticRow(
            program.semantic_references[size_t(
                root.descriptor_groups.begin + offset)],
            program.semantic_tables.descriptor_group_rows);
        const ProgramVariant *owner = group ?
            context.owner("commands", group->command_id) : nullptr;
        if (!owner || owner != context.owner("kernel_ops", group->kernel_op_id) ||
            owner != context.owner("events", group->completion_event_id) ||
            !spanFits(group->descriptor_ids.begin, group->descriptor_ids.count,
                      program.semantic_u64_values.size()))
            return fail("E_DMA_RANGE",
                        "descriptor group references cross its variant", error);
        for (uint64_t index = group->descriptor_ids.begin;
             index < group->descriptor_ids.begin + group->descriptor_ids.count;
             ++index) {
            if (owner != context.owner(
                    "descriptors",
                    program.semantic_u64_values[size_t(index)]))
                return fail("E_DMA_RANGE",
                            "descriptor group references cross its variant",
                            error);
        }
    }
    for (uint64_t offset = 0; offset < root.endpoint_uses.count; ++offset) {
        const DescriptorEndpointUse *endpoint = semanticRow(
            program.semantic_references[size_t(root.endpoint_uses.begin +
                                               offset)],
            program.semantic_tables.descriptor_endpoint_use_rows);
        const ProgramVariant *owner = endpoint ?
            context.owner("descriptors", endpoint->descriptor_id) : nullptr;
        const ReadAccessUse *read = endpoint ? semanticRow(
            endpoint->use, program.semantic_tables.read_access_use_rows) :
            nullptr;
        const WriteAccessUse *write = endpoint ? semanticRow(
            endpoint->use, program.semantic_tables.write_access_use_rows) :
            nullptr;
        const uint64_t operationId = read ? read->kernel_op_id :
            write ? write->kernel_op_id : 0;
        if (!owner || owner != context.owner("kernel_ops", operationId))
            return fail("E_DMA_RANGE",
                        "descriptor endpoint use crosses its variant", error);
    }
    return true;
}

bool
admitCommandOwnership(
    const DecodedProgram &program, ProgramSemanticContext &context,
    MeshLoadError &error)
{
    for (const auto &[commandId, semantic] : context.commandSemantics()) {
        if (commandId > UINT32_MAX ||
            context.commands().count(uint32_t(commandId)) == 0 ||
            !sameOwner(context, "commands", commandId,
                       "command_semantics", commandId))
            return fail("E_ABI_BOUNDS",
                        "command semantics are outside their variant", error);
        const KernelCommandSource *source = semanticRow(
            semantic->source,
            program.semantic_tables.kernel_command_source_rows);
        if (!source)
            continue;
        if (context.operations().count(source->kernel_op_id) == 0 ||
            !sameOwner(context, "commands", commandId,
                       "kernel_ops", source->kernel_op_id))
            return fail("E_ABI_BOUNDS",
                        "Kernel command source is outside its command variant",
                        error);
    }
    for (const auto &[commandId, command] : context.commands()) {
        if (command->signal_event != 0 &&
            !sameOwner(context, "commands", commandId, "events",
                       command->signal_event))
            return fail("E_ABI_BOUNDS",
                        "command synchronization crosses its variant", error);
        if (!spanFits(command->wait_begin, command->wait_count,
                      program.transport.command_waits.size()))
            return fail("E_ABI_BOUNDS", "command synchronization is invalid",
                        error);
        for (uint64_t offset = 0; offset < command->wait_count; ++offset) {
            const CommandWait &wait = program.transport.command_waits[
                size_t(command->wait_begin + offset)];
            if (!sameOwner(context, "commands", commandId, "events",
                           wait.event_id))
                return fail("E_ABI_BOUNDS",
                            "command synchronization crosses its variant",
                            error);
        }
    }
    return true;
}

bool
admitStreamOwnership(
    const DecodedProgram &program, ProgramSemanticContext &context,
    std::map<uint32_t, ProgramSemanticContext::CommandStream> &commandStreams,
    MeshLoadError &error)
{
    const ProgramSemantics &root = context.root();
    if (!spanFits(root.stream_command_ids.begin, root.stream_command_ids.count,
                  program.semantic_u64_values.size()))
        return fail("E_STREAM_CONTRACT",
                    "stream command vector is out of bounds", error);
    if (root.stream_command_ids.count != context.commands().size())
        return fail("E_STREAM_CONTRACT",
                    "stream command vector must exactly partition commands",
                    error);
    for (const auto &[streamId, stream] : context.streams()) {
        const ProgramVariant *owner = context.owner("streams", streamId);
        if (!owner ||
            !spanFits(stream->command_begin, stream->command_count,
                      root.stream_command_ids.count))
            return fail("E_STREAM_CONTRACT",
                        "Scheduled stream ownership is invalid", error);
        for (uint64_t ordinal = 0; ordinal < stream->command_count;
             ++ordinal) {
            const uint64_t commandId = program.semantic_u64_values[size_t(
                root.stream_command_ids.begin + stream->command_begin +
                ordinal)];
            if (commandId > UINT32_MAX || context.commands().count(
                    uint32_t(commandId)) == 0 ||
                context.owner("commands", commandId) != owner ||
                !commandStreams.emplace(
                    uint32_t(commandId),
                    ProgramSemanticContext::CommandStream{stream, ordinal}
                ).second)
                return fail("E_STREAM_CONTRACT",
                            "command is assigned to the wrong semantic stream",
                            error);
        }
    }
    if (commandStreams.size() != context.commands().size())
        return fail("E_STREAM_CONTRACT",
                    "stream command vector must exactly partition commands",
                    error);
    for (const auto &[variantId, variant] : context.variants()) {
        const auto stream = context.streams().find(variant->lifecycle_stream_id);
        if (stream == context.streams().end() ||
            context.owner("streams", stream->first) != variant)
            return fail("E_STREAM_CONTRACT",
                        "variant lifecycle stream is outside its membership",
                        error);
    }
    return true;
}

bool
admitResidentOwnership(ProgramSemanticContext &context, MeshLoadError &error)
{
    if (context.residents().size() != context.views().size())
        return fail("E_ABI_BOUNDS",
                    "resident views do not exactly cover semantic views", error);
    for (const auto &[viewId, view] : context.views()) {
        const auto resident = context.residents().find(viewId);
        if (resident == context.residents().end() ||
            !sameOwner(context, "views", viewId, "runtime_shards",
                       resident->second->runtime_shard_id))
            return fail("E_ABI_BOUNDS",
                        "resident view is missing or crosses its variant",
                        error);
    }
    return true;
}

}

bool
ProgramSemanticContext::matches(const DecodedProgram &program) const
{
    return identity_ && program_ == &program;
}

const ProgramSemantics &
ProgramSemanticContext::root() const
{
    return *root_;
}

const ProgramSemanticContext::MembershipRange *
ProgramSemanticContext::membership(
    uint64_t variantId, std::string_view field) const
{
    const auto variant = memberships_.find(variantId);
    if (variant == memberships_.end())
        return nullptr;
    const auto range = variant->second.find(std::string(field));
    return range == variant->second.end() ? nullptr : &range->second;
}

const ProgramVariant *
ProgramSemanticContext::owner(
    std::string_view field, uint64_t identity) const
{
    for (const auto &[variantId, variant] : variants_) {
        const MembershipRange *range = membership(variantId, field);
        if (range && range->contains(identity))
            return variant;
    }
    return nullptr;
}

const ProgramSemanticContext::CommandStream *
ProgramSemanticContext::commandStream(uint32_t commandId) const
{
    const auto stream = commandStreams_.find(commandId);
    return stream == commandStreams_.end() ? nullptr : &stream->second;
}

const std::map<uint64_t, const ProgramVariant *> &
ProgramSemanticContext::variants() const
{
    return variants_;
}

const std::map<uint64_t, const ScheduledStream *> &
ProgramSemanticContext::streams() const
{
    return streams_;
}

const std::map<uint64_t, const KernelOp *> &
ProgramSemanticContext::operations() const
{
    return operations_;
}

const std::map<uint64_t, const KernelTensor *> &
ProgramSemanticContext::tensors() const
{
    return tensors_;
}

const std::map<uint64_t, const TensorShard *> &
ProgramSemanticContext::logicalShards() const
{
    return logicalShards_;
}

const std::map<uint64_t, const BufferObject *> &
ProgramSemanticContext::objects() const
{
    return objects_;
}

const std::map<uint64_t, const BufferView *> &
ProgramSemanticContext::views() const
{
    return views_;
}

const std::map<uint64_t, const TensorState *> &
ProgramSemanticContext::states() const
{
    return states_;
}

const std::map<uint64_t, const KernelComputation *> &
ProgramSemanticContext::computations() const
{
    return computations_;
}

const std::map<uint64_t, const Placement *> &
ProgramSemanticContext::placements() const
{
    return placements_;
}

const std::map<uint64_t, const PartialSumDefinition *> &
ProgramSemanticContext::partialSums() const
{
    return partialSums_;
}

const std::map<uint64_t, const ControlToken *> &
ProgramSemanticContext::tokens() const
{
    return tokens_;
}

const std::map<uint64_t, const BarrierGroup *> &
ProgramSemanticContext::barrierGroups() const
{
    return barrierGroups_;
}

const std::map<uint64_t, const ScheduledDependency *> &
ProgramSemanticContext::dependencies() const
{
    return dependencies_;
}

const std::map<uint64_t, const CommandSemantics *> &
ProgramSemanticContext::commandSemantics() const
{
    return commandSemantics_;
}

const std::map<uint64_t, const ObjectBacking *> &
ProgramSemanticContext::backings() const
{
    return backings_;
}

const std::map<uint64_t, const ResidentView *> &
ProgramSemanticContext::residents() const
{
    return residents_;
}

const std::map<uint64_t, const BindingSlot *> &
ProgramSemanticContext::bindingSlots() const
{
    return bindingSlots_;
}

const std::map<uint32_t, const Command *> &
ProgramSemanticContext::commands() const
{
    return commands_;
}

const std::map<uint32_t, const DmaDescriptor *> &
ProgramSemanticContext::descriptors() const
{
    return descriptors_;
}

const std::map<uint32_t, const Allocation *> &
ProgramSemanticContext::allocations() const
{
    return allocations_;
}

const std::map<uint32_t, const Shard *> &
ProgramSemanticContext::runtimeShards() const
{
    return runtimeShards_;
}

const Relocation *
ProgramSemanticContext::relocation(uint32_t relocationId) const
{
    const auto found = relocations_.find(relocationId);
    return found == relocations_.end() ? nullptr : found->second;
}

const std::map<uint32_t, uint64_t> &
ProgramSemanticContext::events() const
{
    return events_;
}

bool
buildProgramSemanticContext(
    const DecodedProgram &program, ProgramSemanticContext &out,
    MeshLoadError &error)
{
    if (program.semantic_tables.program_semantics_rows.size() != 1)
        return fail("E_ABI_BOUNDS", "Program semantics root is missing", error);

    ProgramSemanticContext candidate;
    candidate.program_ = &program;
    candidate.identity_ = std::make_shared<ProgramSemanticContextIdentity>();
    candidate.root_ = &program.semantic_tables.program_semantics_rows.front();
    const ProgramSemantics &root = *candidate.root_;
    ProgramOriginKind originKind;
    if (!admitProgramOrigin(program, root, originKind, error))
        return false;

    if (!ProgramSemanticContext::Builder::indexCommandRecords(
            program, candidate, error))
        return false;

    std::map<std::string, uint64_t> semanticCounts;
    std::map<std::string, uint64_t> transportCounts;
    if (!collectSemanticTargetCounts(program, root, semanticCounts, error) ||
        !collectTransportTargetCounts(program, transportCounts))
        return fail("E_ABI_BOUNDS", "membership target is invalid", error);

    if (root.variants.count == 0 ||
        !spanFits(root.variants.begin, root.variants.count,
                  program.semantic_references.size()))
        return fail("E_ABI_BOUNDS", "variant list is out of bounds", error);

    std::map<std::string, uint64_t> next;
    for (const VariantMembershipTargetDescriptor &target :
         kVariantMembershipTargets)
        next.emplace(std::string(target.field), 1);
    std::set<std::pair<uint64_t, uint64_t>> variantPairs;
    std::set<std::string> authoredLineageIds;
    for (uint64_t offset = 0; offset < root.variants.count; ++offset) {
        const ProgramVariant *variant = semanticRow(
            program.semantic_references[size_t(root.variants.begin + offset)],
            program.semantic_tables.program_variant_rows);
        if (!variant)
            return fail("E_ABI_BOUNDS", "Program variant is invalid", error);
        if (variant->variant_id != offset + 1)
            return fail("E_ABI_ORDER", "Program variant IDs are not dense",
                        error);
        if (!candidate.variants_.emplace(variant->variant_id, variant).second)
            return fail("E_ABI_DUPLICATE", "Program variant identity is duplicated",
                        error);
        if (variant->entrypoint_id == 0 ||
            variant->entrypoint_id > UINT32_MAX || variant->profile_id == 0 ||
            variant->profile_id > UINT32_MAX ||
            variant->lifecycle_stream_id == 0 ||
            variant->lifecycle_stream_id > UINT32_MAX)
            return fail("E_ABI_BOUNDS",
                        "Program variant identity is outside positive u32",
                        error);
        if (!admitVariantLineage(
                program, *variant, originKind, authoredLineageIds, error))
            return false;
        if (!variantPairs.emplace(
                variant->entrypoint_id, variant->profile_id).second)
            return fail("E_ABI_DUPLICATE",
                        "entrypoint/profile pair has multiple Program variants",
                        error);
        const VariantMembership *membership = semanticRow(
            variant->membership,
            program.semantic_tables.variant_membership_rows);
        if (!membership)
            return fail("E_ABI_BOUNDS", "variant membership is invalid", error);
        bool valid = visitVariantMembershipTargets(
            *membership,
            [&](const VariantMembershipTargetDescriptor &target,
                const SemanticRef &reference) {
                const IdSpan *span = semanticRow(
                    reference, program.semantic_tables.id_span_rows);
                if (!span)
                    return fail("E_ABI_BOUNDS",
                                "variant membership span is invalid", error);
                const auto &counts = target.source ==
                    VariantMembershipTargetSource::Transport ? transportCounts :
                    semanticCounts;
                const auto total = counts.find(std::string(target.program_field));
                if (total == counts.end())
                    return fail("E_ABI_BOUNDS",
                                "variant membership target is invalid", error);
                const uint64_t expected = next.at(std::string(target.field));
                if (span->first_id != expected) {
                    error.code = "E_ABI_ORDER";
                    error.message = "variant membership span is not canonical";
                    return false;
                }
                const uint64_t begin = span->first_id - 1;
                if (begin > total->second || span->count > total->second - begin) {
                    error.code = "E_ABI_BOUNDS";
                    error.message = "variant membership span exceeds its target";
                    return false;
                }
                next.at(std::string(target.field)) = span->first_id + span->count;
                candidate.memberships_[variant->variant_id].emplace(
                    std::string(target.field),
                    ProgramSemanticContext::MembershipRange{
                        span->first_id, span->count});
                return true;
            });
        if (!valid)
            return false;
    }
    std::set<std::pair<uint64_t, uint64_t>> profilePairs;
    for (const Profile &profile : program.transport.profiles) {
        if (!profilePairs.emplace(profile.entrypoint_id, profile.profile_id).second)
            return fail("E_ABI_DUPLICATE", "ABI profiles have duplicate entrypoint/profile pairs", error);
    }
    if (variantPairs != profilePairs)
        return fail("E_ABI_BOUNDS",
                    "variant membership differs from ABI profiles", error);
    for (const VariantMembershipTargetDescriptor &target :
         kVariantMembershipTargets) {
        const auto &counts = target.source ==
            VariantMembershipTargetSource::Transport ? transportCounts :
            semanticCounts;
        const uint64_t total = counts.at(std::string(target.program_field));
        if (next.at(std::string(target.field)) != total + 1)
            return fail("E_ABI_BOUNDS",
                        "variant membership does not cover its target", error);
    }
    if (!ProgramSemanticContext::Builder::indexOtherTransportRecords(
            program, candidate, error))
        return false;
    if (!ProgramSemanticContext::Builder::indexSemanticRecords(
            program, candidate, error))
        return false;
    std::map<uint32_t, ProgramSemanticContext::CommandStream> commandStreams;
    if (!admitRootReferenceOwnership(program, candidate, error) ||
        !admitCommandOwnership(program, candidate, error) ||
        !admitStreamOwnership(
            program, candidate, commandStreams, error) ||
        !admitResidentOwnership(candidate, error) ||
        !admitDmaOwnership(program, candidate, error))
        return false;
    candidate.commandStreams_ = std::move(commandStreams);
    out = std::move(candidate);
    return true;
}

}
}

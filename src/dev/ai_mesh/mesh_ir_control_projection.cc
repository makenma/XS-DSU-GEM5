#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"

#include <algorithm>
#include <utility>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{
class CommandProjectionIdentity
{};

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

const TypedOpAttr *
commandAttr(const DecodedProgram &program, const Command &command)
{
    if (command.attr_index == 0 ||
        command.attr_index > program.transport.op_attrs.size())
        return nullptr;
    return &program.transport.op_attrs[command.attr_index - 1];
}

bool
matchesControl(
    const Command &command, uint16_t opcode, bool requiresAttr,
    MeshLoadError &error)
{
    if (command.opcode != opcode || command.source_op_id != 0)
        return fail(E_ABI_ENUM, "control command projection is invalid", error);
    if (command.engine != kEngineCONTROL)
        return fail(E_ENGINE_MISMATCH,
                    "control command engine projection is invalid", error);
    if (command.operand_count != 0)
        return fail(E_ABI_BOUNDS, "control command has Kernel operands", error);
    if (!requiresAttr && command.attr_index != 0)
        return fail(E_ABI_ENUM,
                    "control command has an unexpected ABI attribute", error);
    return true;
}

bool
isDmaOpcode(uint16_t opcode)
{
    return opcode == kOpcodeDMA_LOAD || opcode == kOpcodeDMA_STORE ||
        opcode == kOpcodeDMA_P2P_PUSH || opcode == kOpcodeDMA_PREFETCH ||
        opcode == kOpcodeDMA_FILL;
}

bool
matchesCommandOperand(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const KernelOp &operation, uint64_t viewId, Access access,
    const CommandOperand &operand, MeshLoadError &error)
{
    const ProgramVariant *owner = context.owner("kernel_ops", operation.op_id);
    const auto view = context.views().find(viewId);
    if (!owner || view == context.views().end() ||
        owner != context.owner("views", viewId))
        return fail(E_ABI_BOUNDS, "Kernel operand view is invalid", error);
    const auto object = context.objects().find(view->second->object_id);
    const auto shard = context.logicalShards().find(view->second->shard_id);
    const auto resident = context.residents().find(viewId);
    const auto backing = context.backings().find(view->second->object_id);
    if (object == context.objects().end() ||
        shard == context.logicalShards().end() ||
        resident == context.residents().end() ||
        backing == context.backings().end() ||
        owner != context.owner("objects", object->first) ||
        owner != context.owner("logical_shards", shard->first) ||
        owner != context.owner("runtime_shards",
                               resident->second->runtime_shard_id))
        return fail(E_ABI_BOUNDS, "Kernel operand identity crosses its variant",
                    error);

    uint32_t allocationId = 0;
    if (const LocalAllocationBacking *local = semanticRow(
            backing->second->backing,
            program.semantic_tables.local_allocation_backing_rows)) {
        if (local->allocation_id > UINT32_MAX ||
            context.allocations().count(uint32_t(local->allocation_id)) == 0 ||
            owner != context.owner("allocations", local->allocation_id))
            return fail(E_ABI_BOUNDS,
                        "Kernel operand local backing is invalid", error);
        allocationId = uint32_t(local->allocation_id);
    } else {
        const ExternalSlotBacking *external = semanticRow(
            backing->second->backing,
            program.semantic_tables.external_slot_backing_rows);
        if (!external || context.bindingSlots().count(external->slot_id) == 0 ||
            owner != context.owner("binding_slots", external->slot_id))
            return fail(E_ABI_BOUNDS,
                        "Kernel operand external backing is invalid", error);
    }

    if (shard->second->tensor_id > UINT32_MAX ||
        resident->second->runtime_shard_id > UINT32_MAX ||
        operand.tensor_id != shard->second->tensor_id ||
        operand.shard_id != resident->second->runtime_shard_id ||
        operand.allocation_id != allocationId ||
        operand.access != uint16_t(access) || operand.reserved != 0)
        return fail(E_ABI_BOUNDS,
                    "command operand is not its exact Kernel access projection",
                    error);
    return true;
}

}

CommandProjectionFact
VerifiedCommandProjectionFacts::makeCommandFact(
    uint32_t commandId, ProjectedCommandKind kind, uint64_t operationId)
{
    CommandProjectionFact fact;
    fact.commandId_ = commandId;
    fact.kind_ = kind;
    fact.operationId_ = operationId;
    return fact;
}

DmaCommandProjection
VerifiedCommandProjectionFacts::makeDmaProjection(
    uint64_t operationId, uint64_t descriptorGroupId)
{
    DmaCommandProjection projection;
    projection.operationId_ = operationId;
    projection.descriptorGroupId_ = descriptorGroupId;
    return projection;
}

RecvWaitCommandProjection
VerifiedCommandProjectionFacts::makeRecvWaitProjection(uint32_t transferId)
{
    RecvWaitCommandProjection projection;
    projection.transferId_ = transferId;
    return projection;
}

BarrierCommandProjection
VerifiedCommandProjectionFacts::makeBarrierProjection(uint64_t barrierGroupId)
{
    BarrierCommandProjection projection;
    projection.barrierGroupId_ = barrierGroupId;
    return projection;
}

RepeatCommandProjection
VerifiedCommandProjectionFacts::makeRepeatProjection(
    uint64_t beginOrdinal, uint64_t commandCount, uint64_t repeatCount)
{
    RepeatCommandProjection projection;
    projection.beginOrdinal_ = beginOrdinal;
    projection.commandCount_ = commandCount;
    projection.repeatCount_ = repeatCount;
    return projection;
}

struct VerifiedCommandProjectionFacts::Builder
{
    static bool admitCommandOperands(
        const DecodedProgram &program, const ProgramSemanticContext &context,
        const VerifiedCommandProjectionFacts &candidate, MeshLoadError &error)
    {
        size_t cursor = 0;
        for (const Command &command : program.transport.commands) {
            if (size_t(command.operand_begin) != cursor ||
                command.operand_count >
                    program.transport.command_operands.size() - cursor)
                return fail(E_ABI_BOUNDS,
                            "command operand span is not canonical", error);
            const CommandProjectionFact *fact = candidate.command(
                command.command_id);
            if (!fact)
                return fail(E_ABI_BOUNDS,
                            "command has no admitted projection", error);
            if (fact->kind() != ProjectedCommandKind::Control) {
                const auto operation = context.operations().find(
                    fact->operationId());
                if (operation == context.operations().end() ||
                    !spanFits(operation->second->reads.begin,
                              operation->second->reads.count,
                              program.semantic_references.size()) ||
                    !spanFits(operation->second->writes.begin,
                              operation->second->writes.count,
                              program.semantic_references.size()))
                    return fail(E_ABI_BOUNDS,
                                "Kernel operand access list is invalid", error);
                size_t offset = 0;
                for (uint64_t index = 0; index < operation->second->reads.count;
                     ++index, ++offset) {
                    const OperandAccess *read = semanticRow(
                        program.semantic_references[size_t(
                            operation->second->reads.begin + index)],
                        program.semantic_tables.operand_access_rows);
                    if (!read)
                        return fail(E_ABI_BOUNDS,
                                    "Kernel read operand access is invalid",
                                    error);
                    if (!matchesCommandOperand(
                            program, context, *operation->second, read->view_id,
                            Access::READ_ONLY,
                            program.transport.command_operands[cursor + offset],
                            error))
                        return false;
                }
                for (uint64_t index = 0;
                     index < operation->second->writes.count; ++index, ++offset) {
                    const StateTransition *write = semanticRow(
                        program.semantic_references[size_t(
                            operation->second->writes.begin + index)],
                        program.semantic_tables.state_transition_rows);
                    if (!write)
                        return fail(E_ABI_BOUNDS,
                                    "Kernel write operand access is invalid",
                                    error);
                    if (!matchesCommandOperand(
                            program, context, *operation->second, write->view_id,
                            Access::READ_WRITE,
                            program.transport.command_operands[cursor + offset],
                            error))
                        return false;
                }
            }
            cursor += command.operand_count;
        }
        if (cursor != program.transport.command_operands.size())
            return fail(E_ABI_BOUNDS,
                        "command operand spans do not cover the operand table",
                        error);
        return true;
    }

    static bool admitStreams(
        const DecodedProgram &program, const RuntimeArch &arch,
        const ProgramSemanticContext &context, MeshLoadError &error)
    {
        if (context.streams().size() != program.transport.streams.size())
            return fail(E_STREAM_CONTRACT,
                        "semantic and ABI stream tables differ", error);
        const ProgramSemantics &root = context.root();
        auto semantic = context.streams().begin();
        for (const Stream &abi : program.transport.streams) {
            const ScheduledStream &stream = *semantic->second;
            if (stream.core_id > UINT16_MAX ||
                !arch.hasCore(uint16_t(stream.core_id)) ||
                stream.physical_stream_id > UINT32_MAX)
                return fail(E_STREAM_CONTRACT,
                            "semantic stream type or core is invalid", error);
            if (!spanFits(abi.command_begin, abi.command_count,
                          root.stream_command_ids.count))
                return fail(E_ABI_BOUNDS,
                            "ABI stream command range is invalid", error);
            if (uint64_t(abi.core_id) != stream.core_id ||
                uint64_t(abi.stream_id) != stream.physical_stream_id ||
                uint64_t(abi.command_begin) != stream.command_begin ||
                uint64_t(abi.command_count) != stream.command_count ||
                uint64_t(abi.flags) != stream.flags)
                return fail(E_STREAM_CONTRACT,
                            "ABI stream is not the semantic stream projection",
                            error);
            for (uint64_t ordinal = 0; ordinal < stream.command_count;
                 ++ordinal) {
                const uint64_t commandId = program.semantic_u64_values[size_t(
                    root.stream_command_ids.begin + stream.command_begin +
                    ordinal)];
                const ProgramSemanticContext::CommandStream *assignment =
                    commandId <= UINT32_MAX ?
                    context.commandStream(uint32_t(commandId)) : nullptr;
                const auto command = assignment ? context.commands().find(
                    uint32_t(commandId)) : context.commands().end();
                if (!assignment || assignment->stream != &stream ||
                    command == context.commands().end() ||
                    command->second->core_id != stream.core_id ||
                    command->second->stream_id != stream.physical_stream_id)
                    return fail(E_STREAM_CONTRACT,
                                "command is assigned to the wrong semantic stream",
                                error);
            }
            ++semantic;
        }
        for (const auto &[variantId, variant] : context.variants()) {
            const ProgramSemanticContext::MembershipRange *membership =
                context.membership(variantId, "streams");
            if (!membership)
                return fail(E_STREAM_CONTRACT,
                            "variant stream membership is invalid", error);
            std::pair<size_t, uint64_t> previous{};
            bool hasPrevious = false;
            for (uint64_t offset = 0; offset < membership->count; ++offset) {
                const auto stream = context.streams().find(
                    membership->first + offset);
                if (stream == context.streams().end())
                    return fail(E_STREAM_CONTRACT,
                                "variant stream membership is invalid", error);
                const auto core = std::find(
                    arch.core_ids.begin(), arch.core_ids.end(),
                    uint32_t(stream->second->core_id));
                if (core == arch.core_ids.end())
                    return fail(E_STREAM_CONTRACT,
                                "semantic stream type or core is invalid",
                                error);
                const std::pair<size_t, uint64_t> key{
                    size_t(core - arch.core_ids.begin()),
                    stream->second->physical_stream_id};
                if (hasPrevious && key <= previous)
                    return fail(E_STREAM_CONTRACT,
                                "variant streams are not canonical unique physical streams",
                                error);
                previous = key;
                hasPrevious = true;
            }
            const auto lifecycle = context.streams().find(
                variant->lifecycle_stream_id);
            const Entrypoint *entrypoint = nullptr;
            for (const Entrypoint &candidate : program.transport.entrypoints)
                if (candidate.entrypoint_id == variant->entrypoint_id)
                    entrypoint = &candidate;
            if (lifecycle == context.streams().end() || !entrypoint ||
                entrypoint->lifecycle_core_id != lifecycle->second->core_id ||
                entrypoint->lifecycle_stream_id !=
                    lifecycle->second->physical_stream_id)
                return fail(E_STREAM_CONTRACT,
                            "entrypoint lifecycle projection differs from its variant",
                            error);
        }
        return true;
    }

    static bool admitControlProjection(
        const DecodedProgram &program, const Command &command,
        const CommandSemantics &semantic, VerifiedCommandProjectionFacts &candidate,
        MeshLoadError &error)
    {
        const ControlCommandSource *source = semanticRow(
            semantic.source,
            program.semantic_tables.control_command_source_rows);
        const ControlExecution *execution = semanticRow(
            semantic.execution, program.semantic_tables.control_execution_rows);
        if (!source || !execution ||
            semantic.execution.section_type !=
                SemanticRecordTraits<ControlExecution>::section_type)
            return fail(E_ABI_ENUM, "control command semantic union is invalid",
                        error);

        const CommandProjectionFact fact =
            VerifiedCommandProjectionFacts::makeCommandFact(
                command.command_id, ProjectedCommandKind::Control, 0);
        if (semanticRow(
                source->attrs,
                program.semantic_tables.request_begin_attrs_rows)) {
            if (!matchesControl(command, kOpcodeREQUEST_BEGIN, false, error))
                return false;
        } else if (semanticRow(
                       source->attrs,
                       program.semantic_tables.request_end_attrs_rows)) {
            if (!matchesControl(command, kOpcodeREQUEST_END, false, error))
                return false;
        } else if (semanticRow(
                       source->attrs,
                       program.semantic_tables.halt_attrs_rows)) {
            if (!matchesControl(command, kOpcodeHALT, false, error))
                return false;
        } else if (const EventSignalAttrs *attrs = semanticRow(
                       source->attrs,
                       program.semantic_tables.event_signal_attrs_rows)) {
            if (attrs->event_id == 0 || attrs->event_id > UINT32_MAX)
                return fail(E_ABI_BOUNDS, "event control identity is invalid",
                            error);
            if (!matchesControl(command, kOpcodeEVENT_SIGNAL, false, error))
                return false;
            if (command.signal_event != attrs->event_id)
                return fail(E_EVENT_NO_PRODUCER,
                            "event signal command does not produce its typed event",
                            error);
        } else if (const EventWaitAttrs *attrs = semanticRow(
                       source->attrs,
                       program.semantic_tables.event_wait_attrs_rows)) {
            if (attrs->event_id == 0 || attrs->event_id > UINT32_MAX)
                return fail(E_ABI_BOUNDS, "event control identity is invalid",
                            error);
            if (!matchesControl(command, kOpcodeEVENT_WAIT, false, error))
                return false;
            bool found = false;
            for (uint64_t offset = 0; offset < command.wait_count; ++offset)
                found |= program.transport.command_waits[size_t(
                    command.wait_begin + offset)].event_id == attrs->event_id;
            if (!found)
                return fail(E_EVENT_NO_PRODUCER,
                            "event wait command does not wait on its typed event",
                            error);
        } else if (const RepeatCommandAttrs *attrs = semanticRow(
                       source->attrs,
                       program.semantic_tables.repeat_command_attrs_rows)) {
            if (attrs->subrange_begin_stream_ordinal > UINT32_MAX ||
                attrs->subrange_command_count == 0 ||
                attrs->subrange_command_count > UINT32_MAX ||
                attrs->repeat_count == 0 || attrs->repeat_count > UINT32_MAX)
                return fail(E_ABI_BOUNDS,
                            "REPEAT command fields are invalid", error);
            if (!matchesControl(command, kOpcodeREPEAT, true, error))
                return false;
            const TypedOpAttr *attr = commandAttr(program, command);
            if (!attr || attr->kind != kAttrKindREPEAT_V1 ||
                attr->as<RepeatV1>() == nullptr ||
                attr->as<RepeatV1>()->subrange_begin_stream_ordinal !=
                    attrs->subrange_begin_stream_ordinal ||
                attr->as<RepeatV1>()->subrange_command_count !=
                    attrs->subrange_command_count ||
                attr->as<RepeatV1>()->repeat_count != attrs->repeat_count ||
                attr->as<RepeatV1>()->flags != 0)
                return fail(E_ABI_ENUM, "REPEAT command projection is invalid",
                            error);
            candidate.repeats_.emplace(
                command.command_id,
                VerifiedCommandProjectionFacts::makeRepeatProjection(
                    attrs->subrange_begin_stream_ordinal,
                    attrs->subrange_command_count, attrs->repeat_count));
        } else if (const AxiFenceAttrs *attrs = semanticRow(
                       source->attrs,
                       program.semantic_tables.axi_fence_attrs_rows)) {
            if (!validFenceScope(uint32_t(attrs->scope)))
                return fail(E_ABI_ENUM, "AXI fence scope is invalid", error);
            if (!matchesControl(command, kOpcodeAXI_FENCE, true, error))
                return false;
            const TypedOpAttr *attr = commandAttr(program, command);
            if (!attr || attr->kind != kAttrKindFENCE_V1 ||
                attr->as<FenceV1>() == nullptr ||
                attr->as<FenceV1>()->fence_scope != uint16_t(attrs->scope))
                return fail(E_ABI_ENUM, "AXI fence projection is invalid",
                            error);
        } else {
            return fail(E_ABI_ENUM, "control command attributes are invalid",
                        error);
        }
        candidate.commands_.emplace(command.command_id, fact);
        return true;
    }

    static bool admitKernelProjection(
        const DecodedProgram &program, const ProgramSemanticContext &context,
        const Command &command, const CommandSemantics &semantic,
        VerifiedCommandProjectionFacts &candidate, MeshLoadError &error)
    {
        const KernelCommandSource *source = semanticRow(
            semantic.source, program.semantic_tables.kernel_command_source_rows);
        if (!source)
            return fail(E_ABI_ENUM, "command source union is invalid", error);
        const auto operationIt = context.operations().find(source->kernel_op_id);
        if (operationIt == context.operations().end() ||
            command.source_op_id != source->kernel_op_id)
            return fail(E_ABI_BOUNDS, "command Kernel source is invalid", error);
        const KernelOp &operation = *operationIt->second;
        if (command.core_id != operation.owner_core)
            return fail(E_ABI_BOUNDS,
                        "Kernel command core differs from its operation owner",
                        error);
        const uint64_t operandCount =
            uint64_t(operation.reads.count) + operation.writes.count;
        if (operandCount > UINT16_MAX || command.operand_count != operandCount)
            return fail(E_ABI_BOUNDS,
                        "operand count differs from Kernel accesses", error);
        if (operation.opcode == KernelOpcode::COLLECTIVE)
            return fail(E_CAPABILITY_MISMATCH,
                        "abstract collective has no physical execution work",
                        error);

        ProjectedCommandKind kind = ProjectedCommandKind::Kernel;
        if (!candidate.commandsByOperation_.emplace(
                operation.op_id, command.command_id).second)
            return fail(E_ABI_DUPLICATE,
                        "Kernel operation is associated with multiple commands",
                        error);

        if (operation.opcode == KernelOpcode::DMA) {
            const DmaExecution *execution = semanticRow(
                semantic.execution, program.semantic_tables.dma_execution_rows);
            if (!execution || semantic.execution.section_type !=
                    SemanticRecordTraits<DmaExecution>::section_type ||
                !isDmaOpcode(command.opcode))
                return fail(E_ABI_ENUM, "DMA command projection is invalid",
                            error);
            kind = ProjectedCommandKind::Dma;
            candidate.dmas_.emplace(
                command.command_id,
                VerifiedCommandProjectionFacts::makeDmaProjection(
                    operation.op_id, execution->descriptor_group_id));
        } else if (operation.opcode == KernelOpcode::RECV_WAIT) {
            const RecvWaitAttrs *attrs = semanticRow(
                operation.attrs, program.semantic_tables.recv_wait_attrs_rows);
            const RecvWaitExecution *execution = semanticRow(
                semantic.execution,
                program.semantic_tables.recv_wait_execution_rows);
            if (!attrs)
                return fail(E_ABI_ENUM,
                            "receive-wait command projection is invalid", error);
            if (attrs->transfer_id == 0 || attrs->transfer_id > UINT32_MAX ||
                attrs->source_core >= UINT16_MAX ||
                attrs->destination_core >= UINT16_MAX)
                return fail(E_ABI_BOUNDS,
                            "receive-wait attribute identity is invalid", error);
            if (!execution || semantic.execution.section_type !=
                    SemanticRecordTraits<RecvWaitExecution>::section_type ||
                execution->transfer_id != attrs->transfer_id ||
                command.opcode != kOpcodeRECV_WAIT)
                return fail(E_ABI_ENUM,
                            "receive-wait command projection is invalid", error);
            if (command.engine != kEngineCONTROL)
                return fail(E_ENGINE_MISMATCH,
                            "receive-wait command engine projection is invalid",
                            error);
            const TypedOpAttr *attr = commandAttr(program, command);
            if (!attr || attr->kind != kAttrKindRECV_WAIT_V1 ||
                attr->as<RecvWaitV1>() == nullptr ||
                attr->as<RecvWaitV1>()->transfer_id != attrs->transfer_id)
                return fail(E_ABI_ENUM,
                            "receive-wait command projection is invalid", error);
            kind = ProjectedCommandKind::RecvWait;
            candidate.recvWaits_.emplace(
                command.command_id,
                VerifiedCommandProjectionFacts::makeRecvWaitProjection(
                    uint32_t(attrs->transfer_id)));
        } else if (operation.opcode == KernelOpcode::BARRIER) {
            const BarrierExecution *execution = semanticRow(
                semantic.execution, program.semantic_tables.barrier_execution_rows);
            if (!execution || semantic.execution.section_type !=
                    SemanticRecordTraits<BarrierExecution>::section_type ||
                command.opcode != kOpcodeBARRIER || command.attr_index != 0)
                return fail(E_ABI_ENUM, "barrier command projection is invalid",
                            error);
            if (command.engine != kEngineCONTROL)
                return fail(E_ENGINE_MISMATCH,
                            "barrier command engine projection is invalid",
                            error);
            kind = ProjectedCommandKind::Barrier;
            candidate.barriers_.emplace(
                command.command_id,
                VerifiedCommandProjectionFacts::makeBarrierProjection(
                    execution->barrier_group_id));
        }
        candidate.commands_.emplace(
            command.command_id,
            VerifiedCommandProjectionFacts::makeCommandFact(
                command.command_id, kind, operation.op_id));
        return true;
    }
};

bool
VerifiedCommandProjectionFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context) const
{
    return identity_ && program_ == &program && context_ == &context;
}

bool
VerifiedCommandProjectionFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const RuntimeArch &arch) const
{
    return matches(program, context) && arch_ == &arch;
}

const CommandProjectionFact *
VerifiedCommandProjectionFacts::command(uint32_t commandId) const
{
    const auto found = commands_.find(commandId);
    return found == commands_.end() ? nullptr : &found->second;
}

const DmaCommandProjection *
VerifiedCommandProjectionFacts::dma(uint32_t commandId) const
{
    const auto found = dmas_.find(commandId);
    return found == dmas_.end() ? nullptr : &found->second;
}

const RecvWaitCommandProjection *
VerifiedCommandProjectionFacts::recvWait(uint32_t commandId) const
{
    const auto found = recvWaits_.find(commandId);
    return found == recvWaits_.end() ? nullptr : &found->second;
}

const BarrierCommandProjection *
VerifiedCommandProjectionFacts::barrier(uint32_t commandId) const
{
    const auto found = barriers_.find(commandId);
    return found == barriers_.end() ? nullptr : &found->second;
}

const RepeatCommandProjection *
VerifiedCommandProjectionFacts::repeat(uint32_t commandId) const
{
    const auto found = repeats_.find(commandId);
    return found == repeats_.end() ? nullptr : &found->second;
}

const CommandProjectionFact *
VerifiedCommandProjectionFacts::operation(uint64_t operationId) const
{
    const auto found = commandsByOperation_.find(operationId);
    return found == commandsByOperation_.end() ? nullptr :
        command(found->second);
}

bool
verifyProgramCommandProjection(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context, VerifiedCommandProjectionFacts &out,
    MeshLoadError &error)
{
    if (!context.matches(program))
        return fail(E_ABI_BOUNDS, "semantic context belongs to another Program",
                    error);
    if (context.commands().size() != context.commandSemantics().size())
        return fail(E_ABI_BOUNDS,
                    "command semantics do not cover transport commands", error);
    if (!VerifiedCommandProjectionFacts::Builder::admitStreams(
            program, arch, context, error))
        return false;

    VerifiedCommandProjectionFacts candidate;
    candidate.program_ = &program;
    candidate.context_ = &context;
    candidate.arch_ = &arch;
    candidate.identity_ = std::make_shared<CommandProjectionIdentity>();
    for (const auto &[commandId, command] : context.commands()) {
        const auto semanticIt = context.commandSemantics().find(commandId);
        if (semanticIt == context.commandSemantics().end())
            return fail(E_ABI_BOUNDS, "command semantic record is missing",
                        error);
        const CommandSemantics &semantic = *semanticIt->second;
        if (semanticRow(
                semantic.source,
                program.semantic_tables.control_command_source_rows)) {
            if (!VerifiedCommandProjectionFacts::Builder::admitControlProjection(
                    program, *command, semantic, candidate, error))
                return false;
        } else if (semanticRow(
                       semantic.source,
                       program.semantic_tables.kernel_command_source_rows)) {
            if (!VerifiedCommandProjectionFacts::Builder::admitKernelProjection(
                    program, context, *command, semantic, candidate, error))
                return false;
        } else {
            return fail(E_ABI_ENUM, "command source union is invalid", error);
        }
    }

    if (!VerifiedCommandProjectionFacts::Builder::admitCommandOperands(
            program, context, candidate, error))
        return false;

    for (const auto &[operationId, operation] : context.operations()) {
        if (operation->opcode == KernelOpcode::ALLOC ||
            operation->opcode == KernelOpcode::VIEW)
            continue;
        if (candidate.commandsByOperation_.count(operationId) == 0)
            return fail(E_ABI_BOUNDS,
                        "Kernel effect operations and commands do not correspond exactly",
                        error);
    }
    const size_t effectfulCount = size_t(std::count_if(
        context.operations().begin(), context.operations().end(),
        [](const auto &item) {
            return item.second->opcode != KernelOpcode::ALLOC &&
                item.second->opcode != KernelOpcode::VIEW;
        }));
    if (candidate.commandsByOperation_.size() != effectfulCount)
        return fail(E_ABI_BOUNDS,
                    "Kernel effect operations and commands do not correspond exactly",
                    error);
    out = std::move(candidate);
    error = {};
    return true;
}

}
}

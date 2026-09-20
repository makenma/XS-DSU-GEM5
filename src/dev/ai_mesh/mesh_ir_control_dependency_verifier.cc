#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"

#include <algorithm>
#include <map>
#include <memory>
#include <limits>
#include <set>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_enum_traits.hh"
#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_dependency_graph.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dependency_facts_detail.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{
using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;
using namespace detail;

DeclaredScheduledNode
VerifiedControlDependencyFacts::makeDeclaredNode(
    size_t index, std::shared_ptr<const ControlFactIdentity> identity)
{
    return DeclaredScheduledNode(index, std::move(identity));
}

ScheduledStartNode
VerifiedControlDependencyFacts::makeScheduledStartNode(
    size_t index, std::shared_ptr<const ControlFactIdentity> identity)
{
    return ScheduledStartNode(index, std::move(identity));
}

ScheduledCompletionNode
VerifiedControlDependencyFacts::makeScheduledCompletionNode(
    size_t index, std::shared_ptr<const ControlFactIdentity> identity)
{
    return ScheduledCompletionNode(index, std::move(identity));
}

ScheduledEventNode
VerifiedControlDependencyFacts::makeScheduledEventNode(
    size_t index, std::shared_ptr<const ControlFactIdentity> identity)
{
    return ScheduledEventNode(index, std::move(identity));
}

ScheduledCommandFact
VerifiedControlDependencyFacts::makeScheduledFact(
    ScheduledStartNode start, ScheduledCompletionNode completion,
    uint64_t executionCount)
{
    ScheduledCommandFact fact;
    fact.start_ = std::move(start);
    fact.completion_ = std::move(completion);
    fact.executionCount_ = executionCount;
    return fact;
}

ScheduledEventFact
VerifiedControlDependencyFacts::makeEventFact(ScheduledEventNode ready)
{
    ScheduledEventFact fact;
    fact.ready_ = std::move(ready);
    return fact;
}

ScheduledDependencyFact
VerifiedControlDependencyFacts::makeDependencyFact(
    DeclaredScheduledNode source, DeclaredScheduledNode target)
{
    ScheduledDependencyFact fact;
    fact.source_ = std::move(source);
    fact.target_ = std::move(target);
    return fact;
}

struct detail::ControlDependencyBuilder
{
    const DecodedProgram &program;
    const RuntimeArch &arch;
    const ProgramSemanticContext &context;
    const VerifiedProgramGeometry &geometry;
    const VerifiedProgramBacking &backing;
    const VerifiedCommandProjectionFacts &projection;
    const VerifiedDmaDomain &dma;
    const VerifiedIntrinsicDependencyFacts &intrinsic;
    VerifiedControlDependencyFacts candidate;
    const std::map<uint64_t, std::vector<uint64_t>> &afterTokens;
    const std::map<uint64_t, std::vector<uint64_t>> &operandStates;
    const std::map<uint64_t, uint64_t> &tokenProducers;
    const std::map<uint64_t, uint64_t> &stateProducers;
    std::map<uint32_t, size_t> declaredNodes;
    std::map<uint32_t, size_t> startNodes;
    std::map<uint32_t, size_t> completionNodes;
    std::map<uint32_t, size_t> eventNodes;
    std::set<std::tuple<uint32_t, uint32_t, uint32_t, uint16_t, uint64_t>>
        typedDependencies;
    std::set<std::pair<uint32_t, uint32_t>> declaredPairs;
    std::set<std::pair<uint32_t, uint32_t>> factualCompletionPairs;

    ControlDependencyBuilder(
        const DecodedProgram &program, const RuntimeArch &arch,
        const ProgramSemanticContext &context,
        const VerifiedProgramGeometry &geometry,
        const VerifiedProgramBacking &backing,
        const VerifiedCommandProjectionFacts &projection,
        const VerifiedDmaDomain &dma,
        const VerifiedIntrinsicDependencyFacts &intrinsic)
        : program(program), arch(arch), context(context), geometry(geometry),
          backing(backing), projection(projection), dma(dma), intrinsic(intrinsic),
          afterTokens(intrinsic.afterTokens_),
          operandStates(intrinsic.operandStates_),
          tokenProducers(intrinsic.tokenProducers_),
          stateProducers(intrinsic.stateProducers_)
    {
        candidate.program_ = &program;
        candidate.context_ = &context;
        candidate.arch_ = &arch;
        candidate.geometry_ = &geometry;
        candidate.backing_ = &backing;
        candidate.projection_ = &projection;
        candidate.dma_ = &dma;
        candidate.intrinsicIdentity_ = intrinsic.identity_;
        candidate.identity_ = std::make_shared<ControlFactIdentity>();
    }

    const KernelOp *
    kernelOperation(uint32_t commandId) const
    {
        const CommandProjectionFact *fact = projection.command(commandId);
        if (!fact || fact->kind() == ProjectedCommandKind::Control)
            return nullptr;
        const auto operation = context.operations().find(fact->operationId());
        return operation == context.operations().end() ? nullptr :
            operation->second;
    }

    bool
    dependencySource(
        const ScheduledDependency &dependency, uint32_t sourceCommand,
        uint32_t targetCommand, uint16_t &section, uint64_t &identity,
        MeshLoadError &error)
    {
        const KernelOp *sourceOperation = kernelOperation(sourceCommand);
        const KernelOp *targetOperation = kernelOperation(targetCommand);
        switch (dependency.kind) {
          case ScheduledDependencyKind::KERNEL_CONTROL: {
            const KernelTokenSource *source = semanticRow(
                dependency.source,
                program.semantic_tables.kernel_token_source_rows);
            if (!source || source->token_id == 0 ||
                context.tokens().count(source->token_id) == 0 ||
                !sourceOperation || sourceOperation->done_token != source->token_id ||
                (targetOperation && std::find(
                    afterTokens.at(targetOperation->op_id).begin(),
                    afterTokens.at(targetOperation->op_id).end(),
                    source->token_id) ==
                    afterTokens.at(targetOperation->op_id).end()))
                return fail(E_ABI_BOUNDS,
                            "Kernel token dependency contradicts operations",
                            error);
            section = SemanticRecordTraits<KernelTokenSource>::section_type;
            identity = source->token_id;
            return true;
          }
          case ScheduledDependencyKind::KERNEL_STATE: {
            const StateSource *source = semanticRow(
                dependency.source, program.semantic_tables.state_source_rows);
            if (!source || source->state_id == 0 || !sourceOperation ||
                !targetOperation || stateProducers.find(source->state_id) ==
                    stateProducers.end() ||
                stateProducers.at(source->state_id) != sourceOperation->op_id ||
                std::find(operandStates.at(targetOperation->op_id).begin(),
                          operandStates.at(targetOperation->op_id).end(),
                          source->state_id) ==
                    operandStates.at(targetOperation->op_id).end())
                return fail(E_ABI_BOUNDS,
                            "Kernel state dependency contradicts operations",
                            error);
            section = SemanticRecordTraits<StateSource>::section_type;
            identity = source->state_id;
            return true;
          }
          case ScheduledDependencyKind::RAW:
          case ScheduledDependencyKind::WAR:
          case ScheduledDependencyKind::WAW:
          case ScheduledDependencyKind::SRAM_REUSE: {
            const ObjectSource *source = semanticRow(
                dependency.source, program.semantic_tables.object_source_rows);
            if (!source || source->object_id == 0 || !sourceOperation ||
                !targetOperation || context.objects().count(source->object_id) == 0 ||
                !sameOwner(context, "commands", sourceCommand, "objects",
                           source->object_id))
                return fail(E_ABI_BOUNDS,
                            "object dependency is outside its variant", error);
            const auto objectsFor = [&](const KernelOp &operation) {
                std::set<uint64_t> objects;
                for (uint64_t stateId : operandStates.at(operation.op_id))
                    objects.insert(context.states().at(stateId)->object_id);
                return objects;
            };
            const std::set<uint64_t> sourceObjects = objectsFor(*sourceOperation);
            if (dependency.kind == ScheduledDependencyKind::SRAM_REUSE) {
                if (sourceObjects.count(source->object_id) == 0)
                    return fail(E_ABI_BOUNDS,
                                "SRAM reuse source does not access its object",
                                error);
            } else {
                std::vector<const OperandAccess *> sourceReads;
                std::vector<const StateTransition *> sourceWrites;
                std::vector<const OperandAccess *> targetReads;
                std::vector<const StateTransition *> targetWrites;
                if (!operationAccesses(
                        program, *sourceOperation, sourceReads, sourceWrites, error) ||
                    !operationAccesses(
                        program, *targetOperation, targetReads, targetWrites, error))
                    return false;
                const auto objectsForReads = [&](const auto &accesses) {
                    std::set<uint64_t> objects;
                    for (const OperandAccess *access : accesses)
                        objects.insert(
                            context.states().at(access->state_id)->object_id);
                    return objects;
                };
                const auto objectsForWrites = [&](const auto &transitions) {
                    std::set<uint64_t> objects;
                    for (const StateTransition *transition : transitions)
                        objects.insert(context.states().at(
                            transition->old_state_id)->object_id);
                    return objects;
                };
                const std::set<uint64_t> sourceReadObjects =
                    objectsForReads(sourceReads);
                const std::set<uint64_t> sourceWriteObjects =
                    objectsForWrites(sourceWrites);
                const std::set<uint64_t> targetReadObjects =
                    objectsForReads(targetReads);
                const std::set<uint64_t> targetWriteObjects =
                    objectsForWrites(targetWrites);
                const bool valid = dependency.kind == ScheduledDependencyKind::RAW ?
                    sourceWriteObjects.count(source->object_id) &&
                        targetReadObjects.count(source->object_id) :
                    dependency.kind == ScheduledDependencyKind::WAR ?
                    sourceReadObjects.count(source->object_id) &&
                        targetWriteObjects.count(source->object_id) :
                    sourceWriteObjects.count(source->object_id) &&
                        targetWriteObjects.count(source->object_id);
                if (!valid)
                    return fail(E_ABI_BOUNDS,
                                "memory hazard contradicts operation accesses",
                                error);
            }
            section = SemanticRecordTraits<ObjectSource>::section_type;
            identity = source->object_id;
            return true;
          }
          case ScheduledDependencyKind::DMA_PIN:
          case ScheduledDependencyKind::DMA_COMPLETION: {
            const DescriptorSource *source = semanticRow(
                dependency.source,
                program.semantic_tables.descriptor_source_rows);
            const DmaCompletionFact *completion = source &&
                source->descriptor_id <= UINT32_MAX ?
                dma.descriptorCompletion(uint32_t(source->descriptor_id)) : nullptr;
            if (!source || source->descriptor_id == 0 || !completion ||
                completion->producerCommandId() != sourceCommand)
                return fail(E_ABI_BOUNDS,
                            "descriptor dependency contradicts its command",
                            error);
            section = SemanticRecordTraits<DescriptorSource>::section_type;
            identity = source->descriptor_id;
            return true;
          }
          case ScheduledDependencyKind::STREAM_ORDER: {
            const StreamOrderSource *source = semanticRow(
                dependency.source,
                program.semantic_tables.stream_order_source_rows);
            const ProgramSemanticContext::CommandStream *sourceStream =
                context.commandStream(sourceCommand);
            const ProgramSemanticContext::CommandStream *targetStream =
                context.commandStream(targetCommand);
            if (!source || source->stream_id == 0 ||
                source->stream_id > UINT32_MAX || !sourceStream || !targetStream ||
                sourceStream->stream != targetStream->stream ||
                sourceStream->stream->stream_id != source->stream_id ||
                sourceStream->ordinal >= targetStream->ordinal)
                return fail(E_STREAM_CONTRACT,
                            "stream dependency contradicts stream ownership",
                            error);
            section = SemanticRecordTraits<StreamOrderSource>::section_type;
            identity = source->stream_id;
            return true;
          }
          case ScheduledDependencyKind::LIFECYCLE: {
            const LifecycleSource *source = semanticRow(
                dependency.source,
                program.semantic_tables.lifecycle_source_rows);
            const ProgramVariant *owner = context.owner("commands", sourceCommand);
            if (!source || !owner || source->variant_id != owner->variant_id)
                return fail(E_LIFECYCLE,
                            "lifecycle dependency names the wrong variant", error);
            section = SemanticRecordTraits<LifecycleSource>::section_type;
            identity = source->variant_id;
            return true;
          }
          default:
            return fail(E_ABI_BOUNDS,
                        "Scheduled dependency kind is invalid", error);
        }
    }

    bool
    requireDerivedDependencies(MeshLoadError &error)
    {
        for (const auto &[operationId, operation] : context.operations()) {
            if (!effectful(*operation))
                continue;
            const CommandProjectionFact *target = projection.operation(operationId);
            if (!target)
                return fail(E_ABI_BOUNDS,
                            "effectful operation has no command projection", error);
            for (uint64_t tokenId : afterTokens.at(operationId)) {
                const auto producer = tokenProducers.find(tokenId);
                if (producer == tokenProducers.end())
                    continue;
                const uint64_t sourceOperation = producer->second;
                const CommandProjectionFact *source = projection.operation(
                    sourceOperation);
                if (!source)
                    return fail(E_ABI_BOUNDS,
                                "token producer has no command projection", error);
                const auto key = std::tuple(
                    source->commandId(), target->commandId(),
                    uint32_t(ScheduledDependencyKind::KERNEL_CONTROL),
                    SemanticRecordTraits<KernelTokenSource>::section_type, tokenId);
                if (typedDependencies.count(key) == 0)
                    return fail(E_ABI_BOUNDS,
                                "required Kernel token dependency is missing",
                                error);
            }
            for (uint64_t stateId : operandStates.at(operationId)) {
                const auto producer = stateProducers.find(stateId);
                if (producer == stateProducers.end())
                    continue;
                const CommandProjectionFact *source = projection.operation(
                    producer->second);
                if (!source)
                    return fail(E_ABI_BOUNDS,
                                "state producer has no command projection", error);
                const auto key = std::tuple(
                    source->commandId(), target->commandId(),
                    uint32_t(ScheduledDependencyKind::KERNEL_STATE),
                    SemanticRecordTraits<StateSource>::section_type, stateId);
                if (typedDependencies.count(key) == 0)
                    return fail(E_ABI_BOUNDS,
                                "required Kernel state dependency is missing",
                                error);
            }
        }
        return true;
    }

    bool
    admitDeclaredNodes(MeshLoadError &error)
    {
        std::vector<NodeOrderKey> keys;
        for (const auto &[commandId, command] : context.commands()) {
            const size_t node = keys.size() + 1;
            declaredNodes.emplace(commandId, node);
            const std::string id = std::to_string(commandId);
            keys.push_back(NodeOrderKey{
                "command:" + std::string(10 - id.size(), '0') + id,
                std::to_string(command->opcode), {}, node});
        }
        for (const auto &[dependencyId, dependency] : context.dependencies()) {
            if (dependency->source_command_id == 0 ||
                dependency->source_command_id > UINT32_MAX ||
                dependency->target_command_id == 0 ||
                dependency->target_command_id > UINT32_MAX ||
                dependency->source_command_id == dependency->target_command_id ||
                !validScheduledDependencyKind(uint32_t(dependency->kind)) ||
                context.commands().count(uint32_t(dependency->source_command_id)) == 0 ||
                context.commands().count(uint32_t(dependency->target_command_id)) == 0 ||
                !sameOwner(context, "dependencies", dependencyId, "commands",
                           dependency->source_command_id) ||
                !sameOwner(context, "dependencies", dependencyId, "commands",
                           dependency->target_command_id))
                return fail(E_ABI_BOUNDS, "Scheduled dependency is invalid", error);
            const uint32_t source = uint32_t(dependency->source_command_id);
            const uint32_t target = uint32_t(dependency->target_command_id);
            uint16_t section = 0;
            uint64_t identity = 0;
            if (!dependencySource(*dependency, source, target, section, identity,
                                  error))
                return false;
            const auto key = std::tuple(
                source, target, uint32_t(dependency->kind), section, identity);
            if (!typedDependencies.insert(key).second)
                return fail(E_ABI_DUPLICATE,
                            "Scheduled typed dependency is duplicated", error);
            declaredPairs.emplace(source, target);
            if (dependency->kind == ScheduledDependencyKind::KERNEL_CONTROL ||
                dependency->kind == ScheduledDependencyKind::KERNEL_STATE ||
                dependency->kind == ScheduledDependencyKind::RAW ||
                dependency->kind == ScheduledDependencyKind::WAR ||
                dependency->kind == ScheduledDependencyKind::WAW ||
                dependency->kind == ScheduledDependencyKind::SRAM_REUSE)
                factualCompletionPairs.emplace(source, target);
            candidate.dependencies_.emplace(
                dependencyId,
                VerifiedControlDependencyFacts::makeDependencyFact(
                    VerifiedControlDependencyFacts::makeDeclaredNode(
                        declaredNodes.at(source), candidate.identity_),
                    VerifiedControlDependencyFacts::makeDeclaredNode(
                        declaredNodes.at(target), candidate.identity_)));
        }
        if (!requireDerivedDependencies(error))
            return false;
        std::vector<DependencyGraphEdge> edges;
        for (const auto &entry : context.dependencies()) {
            const ScheduledDependency *dependency = entry.second;
            edges.push_back({
                declaredNodes.at(uint32_t(dependency->source_command_id)),
                declaredNodes.at(uint32_t(dependency->target_command_id))});
        }
        auto graph = std::make_shared<VerifiedDependencyGraph>();
        if (!buildVerifiedDependencyGraph(rankNodes(keys), edges, *graph, error))
            return false;
        for (const auto &[source, target] : declaredPairs) {
            const ProgramSemanticContext::CommandStream *sourceStream =
                context.commandStream(source);
            const ProgramSemanticContext::CommandStream *targetStream =
                context.commandStream(target);
            if (sourceStream && targetStream &&
                sourceStream->stream == targetStream->stream &&
                sourceStream->ordinal >= targetStream->ordinal)
                return fail(E_STREAM_CONTRACT,
                            "same-stream dependency order is violated", error);
        }
        candidate.declared_ = std::move(graph);
        return true;
    }

    bool
    admitBarrierProjection(MeshLoadError &error)
    {
        std::map<uint32_t, uint64_t> groupByCommand;
        for (const auto &[groupId, group] : context.barrierGroups()) {
            if (!spanFits(group->arrivals.begin, group->arrivals.count,
                          program.semantic_references.size()))
                return fail(E_ABI_BOUNDS, "barrier arrival span is invalid",
                            error);
            for (uint64_t index = 0; index < group->arrivals.count; ++index) {
                const BarrierArrival *arrival = semanticRow(
                    program.semantic_references[size_t(
                        group->arrivals.begin + index)],
                    program.semantic_tables.barrier_arrival_rows);
                if (!arrival)
                    return fail(E_ABI_BOUNDS,
                                "barrier arrival reference is invalid", error);
                if (arrival->command_id <= UINT32_MAX)
                    groupByCommand[uint32_t(arrival->command_id)] = groupId;
            }
        }
        for (const auto &[commandId, command] : context.commands()) {
            if (command->opcode != kOpcodeBARRIER)
                continue;
            const BarrierCommandProjection *fact = projection.barrier(commandId);
            const auto group = groupByCommand.find(commandId);
            if (!fact || group == groupByCommand.end() ||
                fact->barrierGroupId() != group->second)
                return fail(E_ABI_ENUM,
                            "barrier command projection is invalid", error);
        }
        return true;
    }

    bool
    admitBarrierGroups(
        std::vector<DependencyGraphEdge> &edges,
        std::map<uint32_t, std::vector<uint32_t>> &producers,
        MeshLoadError &error)
    {
        std::set<uint32_t> seenCommands;
        std::set<uint64_t> seenOperations;
        std::set<uint64_t> seenTokens;
        std::set<uint32_t> seenEvents;
        for (const auto &[groupId, group] : context.barrierGroups()) {
            const ProgramVariant *owner = context.owner("barrier_groups", groupId);
            if (!owner || group->completion_event_id == 0 ||
                group->completion_event_id > UINT32_MAX ||
                !sameOwner(context, "barrier_groups", groupId, "events",
                           group->completion_event_id) ||
                !seenEvents.insert(uint32_t(group->completion_event_id)).second)
                return fail(E_EVENT_MULTIPLE_PRODUCERS,
                            "barrier completion event is invalid", error);
            std::vector<uint64_t> participants;
            if (!valueSpan(program, group->participants, participants, error) ||
                participants.empty())
                return fail(E_PLACEMENT_INFEASIBLE,
                            "barrier participants are invalid", error);
            std::vector<uint64_t> expected;
            for (uint32_t core : arch.core_ids)
                expected.push_back(core);
            std::vector<uint64_t> canonical;
            for (uint64_t core : participants) {
                if (std::find(expected.begin(), expected.end(), core) ==
                        expected.end() ||
                    std::find(canonical.begin(), canonical.end(), core) !=
                        canonical.end())
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "barrier participants are invalid", error);
                canonical.push_back(core);
            }
            std::sort(canonical.begin(), canonical.end(),
                [&](uint64_t left, uint64_t right) {
                    return std::find(expected.begin(), expected.end(), left) <
                        std::find(expected.begin(), expected.end(), right);
                });
            if (participants != canonical)
                return fail(E_PLACEMENT_INFEASIBLE,
                            "barrier participants are not canonical", error);
            const Event &event = program.transport.events[
                size_t(group->completion_event_id - 1)];
            if (event.kind != kEventKindBARRIER ||
                event.producer_command_id != 0)
                return fail(E_EVENT_NO_PRODUCER,
                            "barrier completion event projection is invalid",
                            error);
            if (event.expected_arrivals != participants.size())
                return fail(E_ABI_BOUNDS,
                            "barrier completion event arrival count is invalid",
                            error);
            if (!spanFits(group->arrivals.begin, group->arrivals.count,
                          program.semantic_references.size()) ||
                group->arrivals.count != participants.size())
                return fail(E_ABI_BOUNDS,
                            "barrier arrivals do not cover participants", error);
            std::vector<uint32_t> groupProducers;
            for (uint64_t index = 0; index < group->arrivals.count; ++index) {
                const BarrierArrival *arrival = semanticRow(
                    program.semantic_references[size_t(
                        group->arrivals.begin + index)],
                    program.semantic_tables.barrier_arrival_rows);
                if (!arrival || arrival->participant_core != participants[index] ||
                    arrival->command_id == 0 || arrival->command_id > UINT32_MAX)
                    return fail(E_ABI_BOUNDS,
                                "barrier arrivals do not exactly cover participants",
                                error);
                if (!seenCommands.insert(uint32_t(arrival->command_id)).second ||
                    !seenOperations.insert(arrival->kernel_op_id).second ||
                    !seenTokens.insert(arrival->done_token_id).second)
                    return fail(E_EVENT_MULTIPLE_PRODUCERS,
                                "barrier arrival identity is duplicated", error);
                const auto command = context.commands().find(
                    uint32_t(arrival->command_id));
                const auto operation = context.operations().find(
                    arrival->kernel_op_id);
                const BarrierCommandProjection *projectionFact =
                    command == context.commands().end() ? nullptr :
                    projection.barrier(command->first);
                if (command == context.commands().end() ||
                    operation == context.operations().end() ||
                    command->second->opcode != kOpcodeBARRIER ||
                    command->second->core_id != arrival->participant_core ||
                    command->second->signal_event != group->completion_event_id ||
                    operation->second->opcode != KernelOpcode::BARRIER ||
                    operation->second->owner_core != arrival->participant_core ||
                    operation->second->done_token != arrival->done_token_id ||
                    !projectionFact ||
                    projection.command(uint32_t(arrival->command_id)) == nullptr ||
                    projection.command(uint32_t(arrival->command_id))->operationId() !=
                        arrival->kernel_op_id ||
                    projectionFact->barrierGroupId() != groupId ||
                    !sameOwner(context, "barrier_groups", groupId, "commands",
                               arrival->command_id) ||
                    !sameOwner(context, "barrier_groups", groupId, "kernel_ops",
                               arrival->kernel_op_id) ||
                    !sameOwner(context, "barrier_groups", groupId, "tokens",
                               arrival->done_token_id))
                    return fail(E_ABI_BOUNDS,
                                "barrier arrival association is invalid", error);
                const BarrierAttrs *attrs = semanticRow(
                    operation->second->attrs,
                    program.semantic_tables.barrier_attrs_rows);
                std::vector<uint64_t> operationParticipants;
                if (!attrs || !valueSpan(
                        program, attrs->participants, operationParticipants,
                        error) || operationParticipants != participants)
                    return fail(E_ABI_BOUNDS,
                                "barrier Kernel operation association is invalid",
                                error);
                groupProducers.push_back(uint32_t(arrival->command_id));
                edges.push_back({
                    completionNodes.at(uint32_t(arrival->command_id)),
                    eventNodes.at(uint32_t(group->completion_event_id))});
            }
            producers.emplace(uint32_t(group->completion_event_id),
                              std::move(groupProducers));
        }
        for (const auto &[commandId, command] : context.commands()) {
            if (command->opcode == kOpcodeBARRIER &&
                seenCommands.count(commandId) == 0)
                return fail(E_ABI_BOUNDS,
                            "barrier groups do not cover commands", error);
        }
        for (const auto &[operationId, operation] : context.operations()) {
            if (operation->opcode == KernelOpcode::BARRIER &&
                seenOperations.count(operationId) == 0)
                return fail(E_ABI_BOUNDS,
                            "barrier groups do not cover operations", error);
        }
        return true;
    }

    bool
    admitEvents(
        std::vector<DependencyGraphEdge> &edges,
        std::map<uint32_t, std::vector<uint32_t>> &producers,
        MeshLoadError &error)
    {
        std::map<uint32_t, std::vector<uint32_t>> signals;
        for (const auto &[commandId, command] : context.commands()) {
            if (command->signal_event == 0)
                continue;
            if (context.events().count(command->signal_event) == 0)
                return fail(E_ABI_BOUNDS, "command signals an unknown event",
                            error);
            signals[command->signal_event].push_back(commandId);
        }
        for (const auto &entry : context.events()) {
            const uint32_t eventId = entry.first;
            const Event &event = program.transport.events[size_t(eventId - 1)];
            if (!validEventKind(event.kind))
                return fail(E_ABI_ENUM, "event kind is invalid", error);
            const auto signalsIt = signals.find(eventId);
            const std::vector<uint32_t> signallers = signalsIt == signals.end() ?
                std::vector<uint32_t>{} : signalsIt->second;
            if (event.kind == kEventKindNORMAL) {
                if (signallers.size() > 1)
                    return fail(E_EVENT_MULTIPLE_PRODUCERS,
                                "normal event has multiple producers", error);
                if (event.expected_arrivals != 0)
                    return fail(E_ABI_BOUNDS,
                                "normal event expected arrivals are invalid",
                                error);
                if (event.producer_command_id == 0 || signallers.size() != 1 ||
                    event.producer_command_id != signallers.front())
                    return fail(E_EVENT_NO_PRODUCER,
                                "normal event producer projection is invalid",
                                error);
            } else {
                for (uint32_t commandId : signallers)
                    if (context.commands().at(commandId)->opcode != kOpcodeBARRIER)
                        return fail(E_EVENT_MULTIPLE_PRODUCERS,
                                    "barrier event has a non-barrier producer",
                                    error);
            }
        }
        std::map<uint32_t, std::vector<uint32_t>> barrierProducers;
        if (!admitBarrierGroups(edges, barrierProducers, error))
            return false;
        for (const auto &entry : context.events()) {
            const uint32_t eventId = entry.first;
            const Event &event = program.transport.events[size_t(eventId - 1)];
            const auto signalsIt = signals.find(eventId);
            const std::vector<uint32_t> signallers = signalsIt == signals.end() ?
                std::vector<uint32_t>{} : signalsIt->second;
            if (event.kind == kEventKindNORMAL) {
                producers.emplace(eventId, signallers);
                edges.push_back({completionNodes.at(signallers.front()),
                                 eventNodes.at(eventId)});
            } else {
                if (barrierProducers.count(eventId) == 0)
                    return fail(E_EVENT_NO_PRODUCER,
                                "barrier event has no admitted arrivals", error);
                producers.emplace(eventId, barrierProducers.at(eventId));
            }
        }
        for (const auto &[commandId, command] : context.commands()) {
            if (!spanFits(command->wait_begin, command->wait_count,
                          program.transport.command_waits.size()))
                return fail(E_ABI_BOUNDS, "command wait span is invalid", error);
            for (uint64_t offset = 0; offset < command->wait_count; ++offset) {
                const uint32_t eventId = program.transport.command_waits[size_t(
                    command->wait_begin + offset)].event_id;
                if (context.events().count(eventId) == 0)
                    return fail(E_ABI_BOUNDS, "command waits on an unknown event",
                                error);
                edges.push_back({eventNodes.at(eventId), startNodes.at(commandId)});
            }
        }
        return true;
    }

    bool
    admitRecvWaits(MeshLoadError &error)
    {
        std::set<uint32_t> receivedTransfers;
        for (const auto &entry : context.commands()) {
            const uint32_t commandId = entry.first;
            const RecvWaitCommandProjection *recv = projection.recvWait(commandId);
            if (!recv)
                continue;
            const P2PTransferFact *transfer = dma.p2pTransfer(recv->transferId());
            const KernelOp *receive = kernelOperation(commandId);
            const RecvWaitAttrs *attrs = receive ? semanticRow(
                receive->attrs,
                program.semantic_tables.recv_wait_attrs_rows) : nullptr;
            if (!transfer || !receive || !attrs ||
                !receivedTransfers.insert(recv->transferId()).second)
                return fail(E_P2P_UNMATCHED,
                            "receive wait has no unique P2P transfer", error);
            const DmaCompletionFact &completion = transfer->completion();
            if (!sameOwner(context, "commands", commandId, "commands",
                           completion.producerCommandId()))
                return fail(E_P2P_UNMATCHED,
                            "receive wait crosses its P2P transfer",
                            error);
            const KernelOp *send = kernelOperation(completion.producerCommandId());
            const auto after = afterTokens.find(receive->op_id);
            if (attrs->source_core != transfer->sourceCore() ||
                attrs->destination_core != transfer->destinationCore() ||
                attrs->expected_bytes != transfer->expectedBytes() ||
                receive->owner_core != transfer->destinationCore() || !send ||
                after == afterTokens.end() ||
                std::find(after->second.begin(), after->second.end(),
                          send->done_token) == after->second.end())
                return fail(E_P2P_UNMATCHED,
                            "P2P send and receive identities differ", error);
        }
        if (receivedTransfers.size() != dma.p2pTransferCount())
            return fail(E_P2P_UNMATCHED,
                        "P2P transfer has no receive wait", error);
        return true;
    }

    bool
    verifyRecvWaitCompletions(MeshLoadError &error)
    {
        for (const auto &[commandId, command] : context.commands()) {
            const RecvWaitCommandProjection *recv = projection.recvWait(commandId);
            if (!recv)
                continue;
            const P2PTransferFact *transfer = dma.p2pTransfer(
                recv->transferId());
            if (!transfer)
                return fail(E_P2P_UNMATCHED,
                            "receive wait has no unique P2P transfer", error);
            const DmaCompletionFact &completion = transfer->completion();
            bool waitsForCompletion = false;
            for (uint64_t offset = 0; offset < command->wait_count; ++offset)
                waitsForCompletion |= program.transport.command_waits[size_t(
                    command->wait_begin + offset)].event_id ==
                    completion.completionEventId();
            if (!waitsForCompletion)
                return fail(E_P2P_UNMATCHED,
                            "receive wait does not wait for its P2P completion",
                            error);
        }
        return true;
    }

    bool
    hasFactualCompletion(uint32_t source, uint32_t target) const
    {
        return factualCompletionPairs.count({source, target}) != 0;
    }

    bool
    requireCompletionDeclarations(
        const std::map<uint32_t, std::vector<uint32_t>> &eventProducers,
        MeshLoadError &error)
    {
        for (const auto &[targetId, target] : context.commands()) {
            for (uint64_t offset = 0; offset < target->wait_count; ++offset) {
                const uint32_t eventId = program.transport.command_waits[size_t(
                    target->wait_begin + offset)].event_id;
                const auto producers = eventProducers.find(eventId);
                if (producers == eventProducers.end())
                    return fail(E_EVENT_NO_PRODUCER,
                                "command wait has no event producer", error);
                for (uint32_t sourceId : producers->second) {
                    if (declaredPairs.count({sourceId, targetId}) == 0)
                        return fail(E_ABI_BOUNDS,
                                    "command wait has no declared dependency",
                                    error);
                    const std::vector<uint32_t> *descriptors =
                        dma.completionDescriptors(sourceId, eventId);
                    if (descriptors) {
                        for (uint32_t descriptorId : *descriptors) {
                            const auto key = std::tuple(
                                sourceId, targetId,
                                uint32_t(ScheduledDependencyKind::DMA_COMPLETION),
                                SemanticRecordTraits<DescriptorSource>::section_type,
                                uint64_t(descriptorId));
                            if (typedDependencies.count(key) == 0)
                                return fail(E_ABI_BOUNDS,
                                            "descriptor completion dependency is missing",
                                            error);
                        }
                    }
                    const CommandProjectionFact *source = projection.command(sourceId);
                    if (!source)
                        return fail(E_ABI_BOUNDS,
                                    "event producer command is unknown", error);
                    if (source->kind() == ProjectedCommandKind::Dma)
                        continue;
                    if (source->kind() != ProjectedCommandKind::Control) {
                        if (!hasFactualCompletion(sourceId, targetId))
                            return fail(E_ABI_BOUNDS,
                                        "Kernel completion lacks factual dependency",
                                        error);
                    } else {
                        const ProgramVariant *owner = context.owner(
                            "commands", sourceId);
                        if (!owner || typedDependencies.count(std::tuple(
                                sourceId, targetId,
                                uint32_t(ScheduledDependencyKind::LIFECYCLE),
                                SemanticRecordTraits<LifecycleSource>::section_type,
                                owner->variant_id)) == 0)
                            return fail(E_LIFECYCLE,
                                        "control completion lacks lifecycle dependency",
                                        error);
                    }
                }
            }
        }
        return true;
    }

    bool
    verifyCompletionDependencies(MeshLoadError &error)
    {
        for (const auto &entry : context.dependencies()) {
            const ScheduledDependency *dependency = entry.second;
            const uint32_t source = uint32_t(dependency->source_command_id);
            const uint32_t target = uint32_t(dependency->target_command_id);
            if (dependency->kind == ScheduledDependencyKind::STREAM_ORDER)
                continue;
            bool ordered = false;
            if (!candidate.completion_->happensBefore(
                    completionNodes.at(source), startNodes.at(target), ordered,
                    error))
                return false;
            if (!ordered)
                return fail(E_EVENT_NO_PRODUCER,
                            "Scheduled completion lacks event synchronization",
                            error);
        }
        return true;
    }

    bool
    verifyLifecycle(MeshLoadError &error)
    {
        for (const auto &entry : context.variants()) {
            const uint64_t variantId = entry.first;
            const ProgramSemanticContext::MembershipRange *commands =
                context.membership(variantId, "commands");
            if (!commands)
                return fail(E_LIFECYCLE, "variant command membership is missing",
                            error);
            std::vector<uint32_t> begins;
            std::vector<uint32_t> ends;
            std::vector<uint32_t> halts;
            std::vector<uint32_t> owned;
            for (uint64_t id = commands->first;
                 id < commands->first + commands->count; ++id) {
                if (id > UINT32_MAX || context.commands().count(uint32_t(id)) == 0)
                    return fail(E_LIFECYCLE, "variant command membership is invalid",
                                error);
                const uint32_t commandId = uint32_t(id);
                owned.push_back(commandId);
                switch (context.commands().at(commandId)->opcode) {
                  case kOpcodeREQUEST_BEGIN: begins.push_back(commandId); break;
                  case kOpcodeREQUEST_END: ends.push_back(commandId); break;
                  case kOpcodeHALT: halts.push_back(commandId); break;
                  default: break;
                }
            }
            if (begins.size() != 1 || ends.size() != 1 || halts.empty())
                return fail(E_LIFECYCLE, "variant lifecycle set is incomplete",
                            error);
            bool hasLifecycle = false;
            for (const auto &entry : typedDependencies)
                hasLifecycle |= std::get<2>(entry) ==
                    uint32_t(ScheduledDependencyKind::LIFECYCLE) &&
                    std::get<3>(entry) ==
                    SemanticRecordTraits<LifecycleSource>::section_type &&
                    std::get<4>(entry) == variantId;
            if (!hasLifecycle)
                return fail(E_LIFECYCLE,
                            "variant has no lifecycle dependencies", error);
            const uint32_t begin = begins.front();
            const uint32_t end = ends.front();
            for (uint32_t commandId : owned) {
                if (commandId == begin)
                    continue;
                bool declared = false;
                if (!candidate.declared_->happensBefore(
                        declaredNodes.at(begin), declaredNodes.at(commandId),
                        declared, error))
                    return false;
                if (!declared)
                    return fail(E_LIFECYCLE,
                                "variant lifecycle declaration is incomplete",
                                error);
            }
            for (uint32_t halt : halts) {
                bool declared = false;
                if (!candidate.declared_->happensBefore(
                        declaredNodes.at(end), declaredNodes.at(halt), declared,
                        error))
                    return false;
                if (!declared)
                    return fail(E_LIFECYCLE,
                                "variant end does not reach halt", error);
            }
            for (uint32_t commandId : owned) {
                if (commandId == begin || commandId == end ||
                    std::find(halts.begin(), halts.end(), commandId) !=
                        halts.end())
                    continue;
                bool afterBegin = false;
                bool beforeEnd = false;
                if (!candidate.completion_->happensBefore(
                        completionNodes.at(begin), startNodes.at(commandId),
                        afterBegin, error) ||
                    !candidate.completion_->happensBefore(
                        completionNodes.at(commandId), startNodes.at(end),
                        beforeEnd, error))
                    return false;
                if (!afterBegin || !beforeEnd)
                    return fail(E_LIFECYCLE,
                                "variant work lacks lifecycle synchronization",
                                error);
            }
            for (uint32_t halt : halts) {
                bool afterEnd = false;
                if (!candidate.completion_->happensBefore(
                        completionNodes.at(end), startNodes.at(halt), afterEnd,
                        error))
                    return false;
                if (!afterEnd)
                    return fail(E_LIFECYCLE,
                                "variant halt lacks end synchronization", error);
            }
        }
        return true;
    }

    bool
    admitCompletionNodes(MeshLoadError &error)
    {
        std::vector<NodeOrderKey> keys;
        for (const auto &[commandId, command] : context.commands()) {
            const std::string id = std::to_string(commandId);
            const std::string stable =
                "command:" + std::string(10 - id.size(), '0') + id;
            const size_t start = keys.size() + 1;
            keys.push_back(NodeOrderKey{
                stable, std::to_string(command->opcode) + ":START", {}, start});
            const size_t completion = keys.size() + 1;
            keys.push_back(NodeOrderKey{
                stable, std::to_string(command->opcode) + ":COMPLETION", {}, completion});
            startNodes.emplace(commandId, start);
            completionNodes.emplace(commandId, completion);
            candidate.commands_.emplace(
                commandId, VerifiedControlDependencyFacts::makeScheduledFact(
                    VerifiedControlDependencyFacts::makeScheduledStartNode(
                        start, candidate.identity_),
                    VerifiedControlDependencyFacts::makeScheduledCompletionNode(
                        completion, candidate.identity_), 1));
        }
        for (const auto &entry : context.events()) {
            const uint32_t eventId = entry.first;
            const size_t node = keys.size() + 1;
            eventNodes.emplace(eventId, node);
            keys.push_back(NodeOrderKey{
                "event:" + std::to_string(eventId), "READY", {}, node});
            candidate.events_.emplace(
                eventId, VerifiedControlDependencyFacts::makeEventFact(
                    VerifiedControlDependencyFacts::makeScheduledEventNode(
                        node, candidate.identity_)));
        }
        std::vector<DependencyGraphEdge> edges;
        for (const auto &[commandId, start] : startNodes)
            edges.push_back({start, completionNodes.at(commandId)});
        std::map<uint32_t, std::vector<uint32_t>> eventProducers;
        if (!admitEvents(edges, eventProducers, error))
            return false;
        if (!requireCompletionDeclarations(eventProducers, error))
            return false;
        auto graph = std::make_shared<VerifiedDependencyGraph>();
        if (!buildVerifiedDependencyGraph(rankNodes(keys), edges, *graph, error))
            return false;
        candidate.completion_ = std::move(graph);
        return verifyRecvWaitCompletions(error) &&
            verifyCompletionDependencies(error) && verifyLifecycle(error);
    }

    bool
    admitRepeats(MeshLoadError &error)
    {
        const ProgramSemantics &root = context.root();
        if (!spanFits(root.stream_command_ids.begin,
                      root.stream_command_ids.count,
                      program.semantic_u64_values.size()))
            return fail(E_ABI_BOUNDS, "stream command vector is invalid", error);
        std::set<uint32_t> bodies;
        const std::set<uint16_t> forbidden{
            kOpcodeHALT, kOpcodeREQUEST_BEGIN, kOpcodeREQUEST_END,
            kOpcodeBARRIER, kOpcodeDMA_P2P_PUSH, kOpcodeRECV_WAIT,
            kOpcodeREPEAT};
        for (const auto &entry : context.streams()) {
            const ScheduledStream *stream = entry.second;
            if (!spanFits(stream->command_begin, stream->command_count,
                          root.stream_command_ids.count))
                return fail(E_ABI_BOUNDS, "stream command membership is invalid",
                            error);
            std::vector<uint32_t> members;
            members.reserve(size_t(stream->command_count));
            for (uint64_t ordinal = 0; ordinal < stream->command_count;
                 ++ordinal) {
                const uint64_t commandId = program.semantic_u64_values[size_t(
                    root.stream_command_ids.begin + stream->command_begin +
                    ordinal)];
                if (commandId > UINT32_MAX ||
                    context.commands().count(uint32_t(commandId)) == 0)
                    return fail(E_ABI_BOUNDS,
                                "stream command identity is invalid", error);
                members.push_back(uint32_t(commandId));
            }
            for (size_t ordinal = 0; ordinal < members.size(); ++ordinal) {
                const uint32_t commandId = members[ordinal];
                const RepeatCommandProjection *repeat = projection.repeat(
                    commandId);
                if (!repeat) {
                    if (context.commands().at(commandId)->opcode == kOpcodeREPEAT)
                        return fail(E_ABI_ENUM,
                                    "REPEAT command lacks typed repeat semantics",
                                    error);
                    continue;
                }
                if (repeat->beginOrdinal() > ordinal ||
                    repeat->commandCount() > ordinal - repeat->beginOrdinal() ||
                    repeat->beginOrdinal() + repeat->commandCount() != ordinal)
                    return fail(E_ABI_BOUNDS,
                                "REPEAT body must immediately precede its command",
                                error);
                if (repeat->repeatCount() != 0 &&
                    repeat->commandCount() >
                        std::numeric_limits<uint64_t>::max() /
                            repeat->repeatCount())
                    return fail(E_ABI_OVERFLOW,
                                "REPEAT logical command executions overflow", error);
                const size_t begin = size_t(repeat->beginOrdinal());
                const size_t end = ordinal;
                std::set<uint32_t> body;
                for (size_t index = begin; index < end; ++index) {
                    const uint32_t bodyCommand = members[index];
                    if (forbidden.count(context.commands().at(bodyCommand)->opcode) ||
                        !bodies.insert(bodyCommand).second)
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT body contains a forbidden or overlapping command",
                                    error);
                    body.insert(bodyCommand);
                }
                std::vector<const KernelOp *> bodyOperations;
                std::set<uint64_t> producedStates;
                std::set<uint64_t> writtenObjects;
                std::set<uint64_t> bodyTokens;
                for (uint32_t bodyCommand : body) {
                    const KernelOp *operation = kernelOperation(bodyCommand);
                    if (!operation)
                        continue;
                    bodyOperations.push_back(operation);
                    bodyTokens.insert(operation->done_token);
                    std::vector<const OperandAccess *> reads;
                    std::vector<const StateTransition *> writes;
                    if (!operationAccesses(program, *operation, reads, writes, error))
                        return false;
                    for (const StateTransition *write : writes) {
                        producedStates.insert(write->new_state_id);
                        writtenObjects.insert(context.states().at(
                            write->new_state_id)->object_id);
                    }
                }
                for (uint64_t objectId : writtenObjects) {
                    std::set<uint64_t> roots;
                    for (const KernelOp *operation : bodyOperations) {
                        std::vector<const OperandAccess *> reads;
                        std::vector<const StateTransition *> writes;
                        if (!operationAccesses(program, *operation, reads, writes, error))
                            return false;
                        for (const StateTransition *write : writes) {
                            if (context.states().at(write->new_state_id)->object_id !=
                                objectId)
                                continue;
                            if (producedStates.count(write->old_state_id) == 0)
                                roots.insert(write->old_state_id);
                        }
                    }
                    if (roots.size() != 1)
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT writable object lacks one generation-local root",
                                    error);
                    const TensorState &rootState = *context.states().at(
                        *roots.begin());
                    if (rootState.object_id != objectId || rootState.version != 0 ||
                        rootState.origin != StateOrigin::EMPTY)
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT writable object root is invalid", error);
                    for (const KernelOp *operation : bodyOperations) {
                        std::vector<const OperandAccess *> reads;
                        std::vector<const StateTransition *> writes;
                        if (!operationAccesses(program, *operation, reads, writes, error))
                            return false;
                        for (const OperandAccess *read : reads)
                            if (context.states().at(read->state_id)->object_id ==
                                    objectId &&
                                producedStates.count(read->state_id) == 0)
                                return fail(E_ABI_BOUNDS,
                                            "REPEAT reads mutable state outside its generation",
                                            error);
                    }
                }
                for (const KernelOp *operation : bodyOperations) {
                    std::vector<const OperandAccess *> reads;
                    std::vector<const StateTransition *> writes;
                    if (!operationAccesses(program, *operation, reads, writes, error))
                        return false;
                    for (const OperandAccess *read : reads) {
                        const TensorState &state = *context.states().at(
                            read->state_id);
                        if (writtenObjects.count(state.object_id) == 0 &&
                            state.origin != StateOrigin::EXTERNAL &&
                            state.origin != StateOrigin::PRE_RESIDENT)
                            return fail(E_ABI_BOUNDS,
                                        "REPEAT reuses mutable external state",
                                        error);
                    }
                }
                for (const auto &entry : context.commands()) {
                    const uint32_t otherCommandId = entry.first;
                    if (body.count(otherCommandId) != 0)
                        continue;
                    const KernelOp *otherOperation = kernelOperation(otherCommandId);
                    if (!otherOperation)
                        continue;
                    std::set<uint64_t> usedStates;
                    std::vector<const OperandAccess *> reads;
                    std::vector<const StateTransition *> writes;
                    if (!operationAccesses(program, *otherOperation, reads, writes, error))
                        return false;
                    for (const OperandAccess *read : reads)
                        usedStates.insert(read->state_id);
                    for (const StateTransition *write : writes)
                        usedStates.insert(write->old_state_id);
                    std::vector<uint64_t> after;
                    if (!valueSpan(program, otherOperation->after_tokens, after,
                                   error))
                        return false;
                    const bool escapes = std::any_of(
                        usedStates.begin(), usedStates.end(),
                        [&](uint64_t stateId) {
                            return producedStates.count(stateId) != 0;
                        }) || std::any_of(
                        after.begin(), after.end(), [&](uint64_t tokenId) {
                            return bodyTokens.count(tokenId) != 0;
                        });
                    const ProgramSemanticContext::CommandStream *otherStream =
                        context.commandStream(otherCommandId);
                    if (escapes && (!otherStream || otherStream->stream != stream ||
                                    otherStream->ordinal <= ordinal))
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT body result escapes its final generation",
                                    error);
                }
                std::set<uint32_t> signalled;
                std::set<uint32_t> bodyWaits;
                for (uint32_t bodyCommand : body) {
                    const Command &command = *context.commands().at(bodyCommand);
                    if (command.signal_event != 0)
                        signalled.insert(command.signal_event);
                    for (uint64_t offset = 0; offset < command.wait_count;
                         ++offset)
                        bodyWaits.insert(program.transport.command_waits[size_t(
                            command.wait_begin + offset)].event_id);
                }
                for (const auto &[otherCommandId, otherCommand] :
                     context.commands()) {
                    if (body.count(otherCommandId) != 0)
                        continue;
                    bool waitsForBody = false;
                    for (uint64_t offset = 0; offset < otherCommand->wait_count;
                         ++offset)
                        waitsForBody |= signalled.count(
                            program.transport.command_waits[size_t(
                                otherCommand->wait_begin + offset)].event_id) != 0;
                    if (!waitsForBody)
                        continue;
                    const ProgramSemanticContext::CommandStream *otherStream =
                        context.commandStream(otherCommandId);
                    if (!otherStream || otherStream->stream != stream ||
                        (otherCommandId != commandId &&
                         otherStream->ordinal <= ordinal))
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT body event escapes its final generation",
                                    error);
                }
                std::set<uint32_t> repeatWaits;
                const Command &repeatCommand = *context.commands().at(commandId);
                for (uint64_t offset = 0; offset < repeatCommand.wait_count;
                     ++offset)
                    repeatWaits.insert(program.transport.command_waits[size_t(
                        repeatCommand.wait_begin + offset)].event_id);
                std::set<uint32_t> maximalEvents;
                for (uint32_t bodyCommand : body) {
                    const uint32_t signal = context.commands().at(
                        bodyCommand)->signal_event;
                    if (signal == 0)
                        return fail(E_ABI_BOUNDS,
                                    "REPEAT does not drain every generation before completion",
                                    error);
                    if (bodyWaits.count(signal) == 0)
                        maximalEvents.insert(signal);
                }
                if (repeatCommand.signal_event == 0 ||
                    !std::includes(repeatWaits.begin(), repeatWaits.end(),
                                   maximalEvents.begin(), maximalEvents.end()))
                    return fail(E_ABI_BOUNDS,
                                "REPEAT does not drain every generation before completion",
                                error);
                for (uint32_t bodyCommand : body) {
                    ScheduledCommandFact &fact = candidate.commands_.at(
                        bodyCommand);
                    fact = VerifiedControlDependencyFacts::makeScheduledFact(
                        fact.start(), fact.completion(), repeat->repeatCount());
                }
            }
        }
        return true;
    }

    bool
    run(VerifiedControlDependencyFacts &out, MeshLoadError &error)
    {
        if (!context.matches(program) ||
            !projection.matches(program, context, arch) ||
            !dma.matches(program, context, arch, geometry, backing, projection) ||
            !intrinsic.matches(program, context))
            return fail(E_ABI_BOUNDS,
                        "control facts belong to another verification invocation",
                        error);
        if (!admitBarrierProjection(error) || !admitRecvWaits(error) ||
            !admitDeclaredNodes(error) ||
            !admitCompletionNodes(error) || !admitRepeats(error))
            return false;
        out = std::move(candidate);
        error = {};
        return true;
    }
};

bool
VerifiedControlDependencyFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context) const
{
    return identity_ && intrinsicIdentity_ && geometry_ && backing_ && declared_ && completion_ &&
        program_ == &program && context_ == &context;
}

bool
VerifiedControlDependencyFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const RuntimeArch &arch, const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedCommandProjectionFacts &projection,
    const VerifiedDmaDomain &dma,
    const VerifiedIntrinsicDependencyFacts &intrinsic) const
{
    return matches(program, context) && arch_ == &arch && geometry_ == &geometry &&
        backing_ == &backing && projection_ == &projection && dma_ == &dma &&
        intrinsicIdentity_ == intrinsic.identity_ &&
        projection.matches(program, context, arch) &&
        dma.matches(program, context, arch, geometry, backing, projection) &&
        intrinsic.matches(program, context);
}

const ScheduledCommandFact *
VerifiedControlDependencyFacts::scheduled(uint32_t commandId) const
{
    const auto found = commands_.find(commandId);
    return found == commands_.end() ? nullptr : &found->second;
}

const ScheduledEventFact *
VerifiedControlDependencyFacts::event(uint32_t eventId) const
{
    const auto found = events_.find(eventId);
    return found == events_.end() ? nullptr : &found->second;
}

const ScheduledDependencyFact *
VerifiedControlDependencyFacts::dependency(uint64_t dependencyId) const
{
    const auto found = dependencies_.find(dependencyId);
    return found == dependencies_.end() ? nullptr : &found->second;
}

bool
VerifiedControlDependencyFacts::declaredHappensBefore(
    DeclaredScheduledNode source, DeclaredScheduledNode target, bool &answer,
    MeshLoadError &error) const
{
    if (!identity_ || !declared_ || source.identity_ != identity_ ||
        target.identity_ != identity_)
        return fail(E_ABI_BOUNDS, "declared query belongs to another invocation",
                    error);
    return declared_->happensBefore(source.index_, target.index_, answer, error);
}

bool
VerifiedControlDependencyFacts::completionHappensBefore(
    ScheduledCompletionNode source, ScheduledStartNode target, bool &answer,
    MeshLoadError &error) const
{
    if (!identity_ || !completion_ || source.identity_ != identity_ ||
        target.identity_ != identity_)
        return fail(E_ABI_BOUNDS, "completion query belongs to another invocation",
                    error);
    return completion_->happensBefore(source.index_, target.index_, answer,
                                      error);
}

bool
verifyProgramControlDependencyDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedCommandProjectionFacts &projection,
    const VerifiedDmaDomain &dma,
    const VerifiedIntrinsicDependencyFacts &intrinsic,
    VerifiedControlDependencyFacts &out,
    MeshLoadError &error)
{
    return detail::ControlDependencyBuilder(
        program, arch, context, geometry, backing, projection, dma,
        intrinsic).run(out, error);
}

}
}

#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"

#include <map>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_ir_dependency_facts_detail.hh"
#include "dev/ai_mesh/mesh_ir_dependency_graph.hh"

namespace gem5
{
namespace ai_mesh
{
using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;
using namespace detail;

IntrinsicNode
VerifiedIntrinsicDependencyFacts::makeNode(
    size_t index, std::shared_ptr<const IntrinsicFactIdentity> identity)
{
    return IntrinsicNode(index, std::move(identity));
}

KernelOperationFact
VerifiedIntrinsicDependencyFacts::makeOperationFact(
    IntrinsicNode start, IntrinsicNode completion)
{
    KernelOperationFact fact;
    fact.start_ = std::move(start);
    fact.completion_ = std::move(completion);
    return fact;
}

KernelTokenFact
VerifiedIntrinsicDependencyFacts::makeTokenFact(
    IntrinsicNode completion, bool initial,
    std::optional<uint64_t> producerOperation)
{
    KernelTokenFact fact;
    fact.completion_ = std::move(completion);
    fact.initial_ = initial;
    fact.producerOperation_ = producerOperation;
    return fact;
}

struct VerifiedIntrinsicDependencyFacts::Builder
{
    const DecodedProgram &program;
    const ProgramSemanticContext &context;
    VerifiedIntrinsicDependencyFacts candidate;
    std::map<uint64_t, std::vector<uint64_t>> afterTokens;
    std::map<uint64_t, std::vector<uint64_t>> operandStates;
    std::map<uint64_t, uint64_t> tokenProducers;
    std::map<uint64_t, uint64_t> stateProducers;
    std::map<uint64_t, size_t> operationNodes;
    std::map<uint64_t, size_t> tokenNodes;
    std::map<uint64_t, size_t> invocationNodes;

    Builder(
        const DecodedProgram &program, const ProgramSemanticContext &context)
        : program(program), context(context)
    {
        candidate.program_ = &program;
        candidate.context_ = &context;
        candidate.identity_ = std::make_shared<IntrinsicFactIdentity>();
    }

    bool
    localIdentity(
        std::string_view field, uint64_t identity, uint64_t &local,
        MeshLoadError &error) const
    {
        const ProgramVariant *owner = context.owner(field, identity);
        const ProgramSemanticContext::MembershipRange *membership = owner ?
            context.membership(owner->variant_id, field) : nullptr;
        if (!membership || identity < membership->first ||
            identity - membership->first >= membership->count)
            return fail(E_ABI_BOUNDS, "Kernel identity is outside its variant",
                        error);
        local = identity - membership->first + 1;
        return true;
    }

    bool
    admitStates(MeshLoadError &error)
    {
        std::map<uint64_t, std::vector<uint64_t>> versions;
        for (const auto &entry : context.objects())
            versions.emplace(entry.first, std::vector<uint64_t>{});
        for (const auto &[stateId, state] : context.states()) {
            const auto object = context.objects().find(state->object_id);
            if (object == context.objects().end() ||
                !sameOwner(context, "states", stateId, "objects", state->object_id))
                return fail(E_ABI_BOUNDS, "state object is invalid", error);
            if (!validStateOrigin(uint32_t(state->origin)))
                return fail(E_ABI_ORDER, "state origin is invalid", error);
            if ((state->version == 0 && state->origin == StateOrigin::PRODUCED) ||
                (state->version != 0 && state->origin != StateOrigin::PRODUCED))
                return fail(E_ABI_ORDER,
                            "state origin does not match its version", error);
            versions[state->object_id].push_back(state->version);
        }
        for (auto &entry : versions) {
            std::vector<uint64_t> &objectVersions = entry.second;
            std::sort(objectVersions.begin(), objectVersions.end());
            for (size_t index = 0; index < objectVersions.size(); ++index)
                if (objectVersions[index] != index)
                    return fail(E_ABI_ORDER,
                                "object state versions are not consecutive",
                                error);
        }
        return true;
    }

    bool
    admitOperations(MeshLoadError &error)
    {
        for (const auto &entry : context.variants()) {
            const uint64_t variantId = entry.first;
            const ProgramSemanticContext::MembershipRange *operations =
                context.membership(variantId, "kernel_ops");
            const ProgramSemanticContext::MembershipRange *objects =
                context.membership(variantId, "objects");
            const ProgramSemanticContext::MembershipRange *views =
                context.membership(variantId, "views");
            if (!operations || !objects || !views)
                return fail(E_ABI_BOUNDS,
                            "Kernel declaration membership is invalid", error);
            const uint64_t declarationCount = objects->count + views->count;
            if (declarationCount > operations->count)
                return fail(E_ABI_ORDER,
                            "Kernel declarations exceed the operation list",
                            error);
            for (uint64_t offset = 0; offset < objects->count; ++offset) {
                const uint64_t operationId = operations->first + offset;
                const uint64_t objectId = objects->first + offset;
                const auto operation = context.operations().find(operationId);
                const auto object = context.objects().find(objectId);
                const AllocAttrs *attrs = operation == context.operations().end() ?
                    nullptr : semanticRow(
                        operation->second->attrs,
                        program.semantic_tables.alloc_attrs_rows);
                if (operation == context.operations().end() ||
                    object == context.objects().end())
                    return fail(E_ABI_BOUNDS,
                                "Kernel declaration identity is invalid", error);
                if (operation->second->opcode != KernelOpcode::ALLOC || !attrs ||
                    attrs->object_id != objectId)
                    return fail(E_ABI_ORDER,
                                "Kernel declarations do not match record order",
                                error);
                if (operation->second->owner_core != object->second->owner_core)
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "Kernel allocation owner differs from its object",
                                error);
            }
            for (uint64_t offset = 0; offset < views->count; ++offset) {
                const uint64_t operationId =
                    operations->first + objects->count + offset;
                const uint64_t viewId = views->first + offset;
                const auto operation = context.operations().find(operationId);
                const auto view = context.views().find(viewId);
                const ViewDeclarationAttrs *attrs =
                    operation == context.operations().end() ? nullptr :
                    semanticRow(
                        operation->second->attrs,
                        program.semantic_tables.view_declaration_attrs_rows);
                if (operation == context.operations().end() ||
                    view == context.views().end())
                    return fail(E_ABI_BOUNDS,
                                "Kernel declaration identity is invalid", error);
                if (operation->second->opcode != KernelOpcode::VIEW || !attrs ||
                    attrs->view_id != viewId)
                    return fail(E_ABI_ORDER,
                                "Kernel declarations do not match record order",
                                error);
                const auto object = context.objects().find(view->second->object_id);
                if (object == context.objects().end())
                    return fail(E_ABI_BOUNDS,
                                "Kernel view declaration object is invalid", error);
                if (operation->second->owner_core != object->second->owner_core)
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "Kernel view declaration owner differs from its object",
                                error);
            }
            for (uint64_t offset = declarationCount;
                 offset < operations->count; ++offset) {
                const auto operation = context.operations().find(
                    operations->first + offset);
                if (operation == context.operations().end())
                    return fail(E_ABI_BOUNDS,
                                "Kernel operation identity is invalid", error);
                if (operation->second->opcode == KernelOpcode::ALLOC ||
                    operation->second->opcode == KernelOpcode::VIEW)
                    return fail(E_ABI_ORDER,
                                "Kernel declarations must form ALLOC then VIEW prefixes",
                                error);
            }
        }
        for (const auto &[operationId, operation] : context.operations()) {
            std::vector<const OperandAccess *> reads;
            std::vector<const StateTransition *> writes;
            if (!operationAccesses(program, *operation, reads, writes, error))
                return false;
            if (!effectful(*operation)) {
                if (operation->done_token != 0 || operation->after_tokens.count != 0 ||
                    !reads.empty() || !writes.empty())
                    return fail(E_EVENT_MULTIPLE_PRODUCERS,
                                "pure declaration has effects", error);
                continue;
            }
            const auto token = context.tokens().find(operation->done_token);
            if (operation->done_token == 0 || token == context.tokens().end() ||
                !sameOwner(context, "kernel_ops", operationId, "tokens",
                           operation->done_token))
                return fail(E_EVENT_NO_PRODUCER,
                            "effectful operation lacks a completion token", error);
            if (!tokenProducers.emplace(operation->done_token, operationId).second)
                return fail(E_EVENT_MULTIPLE_PRODUCERS,
                            "completion token has multiple producers", error);
            std::vector<uint64_t> dependencies;
            if (!valueSpan(program, operation->after_tokens, dependencies, error))
                return false;
            std::set<uint64_t> uniqueDependencies;
            for (uint64_t tokenId : dependencies) {
                if (context.tokens().count(tokenId) == 0 ||
                    !sameOwner(context, "kernel_ops", operationId, "tokens",
                               tokenId) ||
                    !uniqueDependencies.insert(tokenId).second)
                    return fail(E_ABI_BOUNDS,
                                "operation control dependency is invalid", error);
            }
            afterTokens.emplace(operationId, std::move(dependencies));
            std::vector<uint64_t> states;
            for (const OperandAccess *access : reads) {
                const auto state = context.states().find(access->state_id);
                const auto view = context.views().find(access->view_id);
                if (access->mode != OperandAccessMode::READ ||
                    state == context.states().end() || view == context.views().end() ||
                    state->second->object_id != view->second->object_id ||
                    !sameOwner(context, "kernel_ops", operationId, "states",
                               access->state_id) ||
                    !sameOwner(context, "kernel_ops", operationId, "views",
                               access->view_id))
                    return fail(E_ABI_BOUNDS,
                                "operand access state and view disagree", error);
                states.push_back(access->state_id);
            }
            std::map<uint64_t, uint64_t> groupedOldStates;
            for (const StateTransition *transition : writes) {
                const auto oldState = context.states().find(transition->old_state_id);
                const auto newState = context.states().find(transition->new_state_id);
                const auto view = context.views().find(transition->view_id);
                if (transition->mode != OperandAccessMode::WRITE ||
                    oldState == context.states().end() ||
                    newState == context.states().end() ||
                    view == context.views().end() ||
                    oldState->second->object_id != newState->second->object_id ||
                    newState->second->object_id != view->second->object_id ||
                    newState->second->version != oldState->second->version + 1 ||
                    !sameOwner(context, "kernel_ops", operationId, "states",
                               transition->old_state_id) ||
                    !sameOwner(context, "kernel_ops", operationId, "states",
                               transition->new_state_id) ||
                    !sameOwner(context, "kernel_ops", operationId, "views",
                               transition->view_id))
                    return fail(E_ABI_BOUNDS,
                                "state transition is invalid", error);
                const auto inserted = groupedOldStates.emplace(
                    transition->new_state_id, transition->old_state_id);
                if (!inserted.second &&
                    inserted.first->second != transition->old_state_id)
                    return fail(E_ABI_BOUNDS,
                                "state definition has multiple old states", error);
                states.push_back(transition->old_state_id);
            }
            for (const auto &entry : groupedOldStates)
                if (!stateProducers.emplace(entry.first, operationId).second)
                    return fail(E_ABI_DUPLICATE,
                                "state has multiple definitions", error);
            operandStates.emplace(operationId, std::move(states));
        }
        for (const auto &[tokenId, token] : context.tokens()) {
            const bool produced = tokenProducers.count(tokenId) != 0;
            if ((token->initial && produced))
                return fail(E_EVENT_MULTIPLE_PRODUCERS,
                            "initial token has an operation producer", error);
            if (!token->initial && !produced)
                return fail(E_EVENT_NO_PRODUCER,
                            "completion token has no producer", error);
        }
        for (const auto &[stateId, state] : context.states()) {
            const bool produced = stateProducers.count(stateId) != 0;
            if (state->origin == StateOrigin::PRODUCED && !produced)
                return fail(E_ABI_BOUNDS,
                            "produced state has no definition", error);
            if (state->origin != StateOrigin::PRODUCED && produced)
                return fail(E_ABI_DUPLICATE,
                            "initial state is redefined", error);
        }
        return true;
    }

    bool
    admitIntrinsic(MeshLoadError &error)
    {
        if (!admitStates(error) || !admitOperations(error))
            return false;
        std::vector<NodeOrderKey> keys;
        for (const auto &[operationId, operation] : context.operations()) {
            if (!effectful(*operation))
                continue;
            const std::string *stable = semanticString(program, operation->stable_key);
            if (!stable || stable->empty())
                return fail(E_ABI_BOUNDS, "Kernel operation stable key is invalid",
                            error);
            const size_t node = keys.size() + 1;
            operationNodes.emplace(operationId, node);
            std::vector<uint64_t> localOperands;
            for (uint64_t stateId : operandStates.at(operationId)) {
                uint64_t localState = 0;
                if (!localIdentity("states", stateId, localState, error))
                    return false;
                localOperands.push_back(localState);
            }
            keys.push_back(NodeOrderKey{
                *stable, opcodeName(operation->opcode), std::move(localOperands),
                node});
        }
        for (const auto &[tokenId, token] : context.tokens()) {
            const size_t node = keys.size() + 1;
            tokenNodes.emplace(tokenId, node);
            uint64_t localToken = 0;
            if (!localIdentity("tokens", tokenId, localToken, error))
                return false;
            const auto producer = tokenProducers.find(tokenId);
            std::string stable = "initial:" + std::to_string(localToken);
            if (producer != tokenProducers.end()) {
                const KernelOp *operation = context.operations().at(producer->second);
                const std::string *value = semanticString(program, operation->stable_key);
                if (!value || value->empty())
                    return fail(E_ABI_BOUNDS,
                                "Kernel operation stable key is invalid", error);
                stable = *value;
            }
            keys.push_back(NodeOrderKey{
                std::move(stable), "TOKEN", {localToken}, node});
        }
        for (const auto &entry : context.variants()) {
            const uint64_t variantId = entry.first;
            const size_t node = keys.size() + 1;
            invocationNodes.emplace(variantId, node);
            keys.push_back(NodeOrderKey{"", "INVOCATION_BEGIN", {}, node});
        }

        std::vector<DependencyGraphEdge> edges;
        for (const auto &[operationId, start] : operationNodes) {
            const KernelOp &operation = *context.operations().at(operationId);
            const size_t completion = tokenNodes.at(operation.done_token);
            edges.push_back({start, completion});
            for (uint64_t tokenId : afterTokens.at(operationId))
                edges.push_back({tokenNodes.at(tokenId), start});
            for (uint64_t stateId : operandStates.at(operationId)) {
                const auto producer = stateProducers.find(stateId);
                if (producer != stateProducers.end())
                    edges.push_back({
                        tokenNodes.at(context.operations().at(
                            producer->second)->done_token),
                        start});
            }
            const ProgramVariant *variant = context.owner("kernel_ops", operationId);
            if (!variant)
                return fail(E_ABI_BOUNDS, "Kernel operation owner is invalid",
                            error);
            edges.push_back({invocationNodes.at(variant->variant_id), start});
        }
        for (const auto &[tokenId, token] : context.tokens()) {
            if (!token->initial)
                continue;
            const ProgramVariant *variant = context.owner("tokens", tokenId);
            if (!variant)
                return fail(E_ABI_BOUNDS, "control token owner is invalid", error);
            edges.push_back({invocationNodes.at(variant->variant_id),
                             tokenNodes.at(tokenId)});
        }

        auto graph = std::make_shared<VerifiedDependencyGraph>();
        if (!buildVerifiedDependencyGraph(rankNodes(keys), edges, *graph, error))
            return false;
        candidate.graph_ = std::move(graph);
        std::map<size_t, uint64_t> operationByNode;
        for (const auto &[operationId, node] : operationNodes)
            operationByNode.emplace(node, operationId);
        for (const auto &[variantId, variant] : context.variants()) {
            const ProgramSemanticContext::MembershipRange *operations =
                context.membership(variantId, "kernel_ops");
            if (!operations)
                return fail(E_ABI_BOUNDS,
                            "Kernel operation membership is invalid", error);
            std::vector<uint64_t> declared;
            for (uint64_t operationId = operations->first;
                 operationId < operations->first + operations->count;
                 ++operationId) {
                const auto operation = context.operations().find(operationId);
                if (operation == context.operations().end())
                    return fail(E_ABI_BOUNDS,
                                "Kernel operation membership is invalid", error);
                if (effectful(*operation->second))
                    declared.push_back(operationId);
            }
            std::vector<uint64_t> canonical;
            for (size_t node : candidate.graph_->canonicalOrder()) {
                const auto operation = operationByNode.find(node);
                if (operation != operationByNode.end() &&
                    context.owner("kernel_ops", operation->second) == variant)
                    canonical.push_back(operation->second);
            }
            if (canonical != declared)
                return fail(E_ABI_ORDER,
                            "effectful Kernel operations are not in canonical topological order",
                            error);
        }
        for (const auto &[operationId, start] : operationNodes) {
            const KernelOp &operation = *context.operations().at(operationId);
            candidate.operations_.emplace(
                operationId,
                VerifiedIntrinsicDependencyFacts::makeOperationFact(
                    VerifiedIntrinsicDependencyFacts::makeNode(
                        start, candidate.identity_),
                    VerifiedIntrinsicDependencyFacts::makeNode(
                        tokenNodes.at(operation.done_token),
                        candidate.identity_)));
        }
        for (const auto &[tokenId, node] : tokenNodes) {
            const ControlToken &token = *context.tokens().at(tokenId);
            const auto producer = tokenProducers.find(tokenId);
            candidate.tokens_.emplace(
                tokenId, VerifiedIntrinsicDependencyFacts::makeTokenFact(
                    VerifiedIntrinsicDependencyFacts::makeNode(
                        node, candidate.identity_), token.initial,
                    producer == tokenProducers.end() ?
                        std::optional<uint64_t>{} :
                        std::optional<uint64_t>{producer->second}));
        }
        for (const auto &[variantId, node] : invocationNodes)
            candidate.invocationBegins_.emplace(
                variantId, VerifiedIntrinsicDependencyFacts::makeNode(
                    node, candidate.identity_));
        return true;
    }

    bool
    run(VerifiedIntrinsicDependencyFacts &out, MeshLoadError &error)
    {
        if (!context.matches(program))
            return fail(E_ABI_BOUNDS,
                        "intrinsic facts belong to another verification invocation",
                        error);
        if (!admitIntrinsic(error))
            return false;
        candidate.afterTokens_ = std::move(afterTokens);
        candidate.operandStates_ = std::move(operandStates);
        candidate.tokenProducers_ = std::move(tokenProducers);
        candidate.stateProducers_ = std::move(stateProducers);
        out = std::move(candidate);
        error = {};
        return true;
    }
};

bool
VerifiedIntrinsicDependencyFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context) const
{
    return identity_ && graph_ && program_ == &program && context_ == &context;
}

const KernelOperationFact *
VerifiedIntrinsicDependencyFacts::operation(uint64_t operationId) const
{
    const auto found = operations_.find(operationId);
    return found == operations_.end() ? nullptr : &found->second;
}

const KernelTokenFact *
VerifiedIntrinsicDependencyFacts::token(uint64_t tokenId) const
{
    const auto found = tokens_.find(tokenId);
    return found == tokens_.end() ? nullptr : &found->second;
}

const IntrinsicNode *
VerifiedIntrinsicDependencyFacts::invocationBegin(uint64_t variantId) const
{
    const auto found = invocationBegins_.find(variantId);
    return found == invocationBegins_.end() ? nullptr : &found->second;
}

bool
VerifiedIntrinsicDependencyFacts::intrinsicHappensBefore(
    IntrinsicNode source, IntrinsicNode target, bool &answer,
    MeshLoadError &error) const
{
    if (!identity_ || !graph_ || source.identity_ != identity_ ||
        target.identity_ != identity_)
        return fail(E_ABI_BOUNDS, "intrinsic query belongs to another invocation",
                    error);
    return graph_->happensBefore(source.index_, target.index_, answer, error);
}

const uint64_t *
VerifiedIntrinsicDependencyFacts::stateProducer(uint64_t stateId) const
{
    const auto found = stateProducers_.find(stateId);
    return found == stateProducers_.end() ? nullptr : &found->second;
}

bool
verifyProgramIntrinsicDependencyFacts(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    VerifiedIntrinsicDependencyFacts &out, MeshLoadError &error)
{
    return VerifiedIntrinsicDependencyFacts::Builder(program, context).run(
        out, error);
}

}
}

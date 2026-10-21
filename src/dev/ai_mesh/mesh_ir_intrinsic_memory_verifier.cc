#include "dev/ai_mesh/mesh_ir_intrinsic_memory_verifier.hh"

#include <algorithm>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_ir_dependency_facts_detail.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;
using namespace detail;

struct StateDefinition
{
    uint64_t operationId = 0;
    uint64_t oldStateId = 0;
    ExactByteRegion written;
    std::set<uint64_t> writePartialIds;
    std::vector<size_t> writeOrdinals;
};

struct AccessRecord
{
    uint64_t operationId = 0;
    uint64_t stateId = 0;
    uint64_t objectId = 0;
    bool write = false;
    ExactByteRegion bytes;
};

struct LineageFragment
{
    ExactByteRegion bytes;
    std::vector<uint64_t> contributors;
};

struct LineageWrite
{
    ExactByteRegion bytes;
    std::optional<std::vector<uint64_t>> contributors;
};

class IntrinsicMemoryBuilder
{
  public:
    IntrinsicMemoryBuilder(
        const DecodedProgram &program, const ProgramSemanticContext &context,
        const VerifiedProgramGeometry &geometry,
        const VerifiedIntrinsicDependencyFacts &intrinsic,
        const VerifiedMatrixContractionFacts &matrixFacts)
        : program_(program), context_(context), geometry_(geometry),
          intrinsic_(intrinsic), matrixFacts_(matrixFacts)
    {}

    bool run(MeshLoadError &error)
    {
        if (!admitInitialStates(error) || !deriveDefinitionsAndAccesses(error) ||
            !rejectStaleAccesses(error) || !verifyInitialization(error) ||
            !verifyPartialContributors(error) ||
            !rejectUnorderedHazards(error)) {
            return false;
        }
        error = {};
        return true;
    }

  private:
    bool canonicalOperations(
        std::vector<uint64_t> &out, MeshLoadError &error) const
    {
        out.clear();
        for (const auto &entry : context_.variants()) {
            const uint64_t variantId = entry.first;
            const ProgramSemanticContext::MembershipRange *operations =
                context_.membership(variantId, "kernel_ops");
            if (!operations ||
                operations->count >
                    std::numeric_limits<uint64_t>::max() -
                        operations->first) {
                return fail(E_ABI_BOUNDS,
                            "Kernel operation membership is invalid", error);
            }
            for (uint64_t operationId = operations->first;
                 operationId < operations->first + operations->count;
                 ++operationId) {
                if (context_.operations().count(operationId) == 0) {
                    return fail(E_ABI_BOUNDS,
                                "Kernel operation membership is invalid", error);
                }
                if (intrinsic_.operation(operationId))
                    out.push_back(operationId);
            }
        }
        return true;
    }

    bool initialAliasesStorage(
        uint64_t objectId, uint64_t storageTensorId, MeshLoadError &error) const
    {
        for (const auto &[viewId, view] : context_.views()) {
            if (view->object_id != objectId)
                continue;
            const auto shard = context_.logicalShards().find(view->shard_id);
            const auto tensor = shard == context_.logicalShards().end() ?
                context_.tensors().end() :
                context_.tensors().find(shard->second->tensor_id);
            if (tensor == context_.tensors().end())
                return fail(E_ABI_BOUNDS,
                            "initial state view storage identity is invalid", error);
            if (tensor->second->alias_root_tensor_id != storageTensorId)
                return fail(E_TENSOR_NOT_RESIDENT,
                            "initial backing content differs from its logical view",
                            error);
        }
        return true;
    }

    bool admitInitialStates(MeshLoadError &error)
    {
        std::map<uint64_t, const TensorState *> initial;
        for (const auto &[stateId, state] : context_.states()) {
            if (state->version != 0)
                continue;
            if (!initial.emplace(state->object_id, state).second)
                return fail(E_ABI_BOUNDS,
                            "object has multiple version-zero states", error);
        }
        for (const auto &[objectId, object] : context_.objects()) {
            const auto state = initial.find(objectId);
            const auto tensor = context_.tensors().find(object->storage_tensor_id);
            const ExactByteRegion *bytes = geometry_.objectBytes(objectId);
            if (state == initial.end() || tensor == context_.tensors().end() ||
                !bytes)
                return fail(E_ABI_BOUNDS,
                            "object has no version-zero state", error);
            if (state->second->partial_sum_id != 0)
                return fail(E_TENSOR_NOT_RESIDENT,
                            "initial state cannot claim partial-SUM provenance",
                            error);
            switch (state->second->origin) {
              case StateOrigin::EXTERNAL:
                if (!initialAliasesStorage(
                        objectId, object->storage_tensor_id, error)) {
                    return false;
                }
                if ((object->memory_space != mesh_abi::semantic_abi::MemorySpace::HBM &&
                     object->memory_space != mesh_abi::semantic_abi::MemorySpace::HOST_SHARED) ||
                    tensor->second->storage_class != mesh_abi::semantic_abi::StorageClass::EXTERNAL) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "EXTERNAL state does not name external storage",
                                error);
                }
                initialized_.emplace(state->second->state_id, *bytes);
                break;
              case StateOrigin::PRE_RESIDENT:
                if (!initialAliasesStorage(
                        objectId, object->storage_tensor_id, error)) {
                    return false;
                }
                if (tensor->second->storage_class != mesh_abi::semantic_abi::StorageClass::PRE_RESIDENT ||
                    object->memory_space != mesh_abi::semantic_abi::MemorySpace::CORE_SRAM ||
                    tensor->second->role != mesh_abi::semantic_abi::TensorRole::WEIGHT ||
                    tensor->second->content_sha256.string_id == 0) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "PRE_RESIDENT state is invalid", error);
                }
                initialized_.emplace(state->second->state_id, *bytes);
                break;
              case StateOrigin::EMPTY:
                initialized_.emplace(
                    state->second->state_id, geometry_.emptyBytes());
                break;
              default:
                return fail(E_ABI_ORDER,
                            "version-zero state has invalid origin", error);
            }
        }
        return true;
    }

    bool addAccess(
        uint64_t operationId, uint64_t stateId, bool write,
        const GeometryAccessFact &access, MeshLoadError &error)
    {
        const auto state = context_.states().find(stateId);
        if (state == context_.states().end())
            return fail(E_ABI_BOUNDS, "access state is invalid", error);
        accesses_.push_back({operationId, stateId, state->second->object_id,
                             write, access.bytes()});
        return true;
    }

    bool deriveDefinitionsAndAccesses(MeshLoadError &error)
    {
        std::vector<uint64_t> operationIds;
        if (!canonicalOperations(operationIds, error))
            return false;
        for (const uint64_t operationId : operationIds) {
            std::map<uint64_t,
                     std::vector<std::pair<size_t, const GeometryAccessFact *>>>
                groups;
            for (size_t ordinal = 0;
                 ordinal < geometry_.accessCount(
                               operationId, GeometryAccessRole::Read);
                 ++ordinal) {
                const GeometryAccessFact *access = geometry_.access(
                    operationId, GeometryAccessRole::Read, ordinal);
                if (!access || !access->read() ||
                    !addAccess(operationId, access->read()->state_id, false,
                               *access, error)) {
                    return false;
                }
            }
            for (size_t ordinal = 0;
                 ordinal < geometry_.accessCount(
                               operationId, GeometryAccessRole::Write);
                 ++ordinal) {
                const GeometryAccessFact *access = geometry_.access(
                    operationId, GeometryAccessRole::Write, ordinal);
                if (!access || !access->write() ||
                    !addAccess(operationId, access->write()->old_state_id, true,
                               *access, error)) {
                    return false;
                }
                groups[access->write()->new_state_id].emplace_back(
                    ordinal, access);
            }
            for (const auto &[newStateId, writes] : groups) {
                const uint64_t *producer = intrinsic_.stateProducer(newStateId);
                const auto newState = context_.states().find(newStateId);
                if (!producer || *producer != operationId ||
                    newState == context_.states().end() || writes.empty()) {
                    return fail(E_ABI_BOUNDS,
                                "state definition is absent from intrinsic facts",
                                error);
                }
                const uint64_t oldStateId =
                    writes.front().second->write()->old_state_id;
                ExactByteRegion written = geometry_.emptyBytes();
                std::set<uint64_t> partialIds;
                std::vector<size_t> writeOrdinals;
                for (size_t first = 0; first < writes.size(); ++first) {
                    const GeometryAccessFact &write = *writes[first].second;
                    if (write.write()->old_state_id != oldStateId)
                        return fail(E_ABI_BOUNDS,
                                    "grouped state definition has multiple old states",
                                    error);
                    partialIds.insert(write.logicalShard().partial_sum_id);
                    writeOrdinals.push_back(writes[first].first);
                    for (size_t second = 0; second < first; ++second) {
                        bool disjoint = false;
                        if (!geometry_.disjoint(
                                write.bytes(), writes[second].second->bytes(),
                                disjoint, error)) {
                            return false;
                        }
                        if (!disjoint) {
                            return fail(E_EXPORT_LAYOUT,
                                        "grouped state definition has overlapping writes",
                                        error);
                        }
                    }
                    ExactByteRegion merged;
                    if (!geometry_.unite(
                            written, write.bytes(), merged, error)) {
                        return false;
                    }
                    written = std::move(merged);
                }
                if (!definitions_.emplace(
                        newStateId, StateDefinition{
                            operationId, oldStateId, std::move(written),
                            std::move(partialIds), std::move(writeOrdinals)}).second) {
                    return fail(E_ABI_DUPLICATE,
                                "state has multiple definitions", error);
                }
            }
        }
        return true;
    }

    bool happensBefore(
        uint64_t producerOperationId, uint64_t consumerOperationId,
        bool &ordered, MeshLoadError &error) const
    {
        const KernelOperationFact *producer = intrinsic_.operation(
            producerOperationId);
        const KernelOperationFact *consumer = intrinsic_.operation(
            consumerOperationId);
        if (!producer || !consumer) {
            return fail(E_ABI_BOUNDS,
                        "intrinsic operation fact is unavailable", error);
        }
        return intrinsic_.intrinsicHappensBefore(
            producer->completion(), consumer->start(), ordered, error);
    }

    bool rejectStaleAccesses(MeshLoadError &error) const
    {
        std::map<uint64_t, std::vector<const TensorState *>> statesByObject;
        for (const auto &[stateId, state] : context_.states())
            statesByObject[state->object_id].push_back(state);
        for (auto &[objectId, states] : statesByObject) {
            std::sort(states.begin(), states.end(),
                      [](const TensorState *left, const TensorState *right) {
                          return left->version < right->version;
                      });
        }
        for (const AccessRecord &access : accesses_) {
            const TensorState &state = *context_.states().at(access.stateId);
            for (const TensorState *newer : statesByObject.at(access.objectId)) {
                if (newer->version <= state.version)
                    continue;
                const auto definition = definitions_.find(newer->state_id);
                if (definition == definitions_.end())
                    continue;
                bool disjoint = false;
                if (!geometry_.disjoint(
                        access.bytes, definition->second.written, disjoint,
                        error)) {
                    return false;
                }
                if (disjoint)
                    continue;
                bool ordered = false;
                if (!happensBefore(
                        definition->second.operationId, access.operationId,
                        ordered, error)) {
                    return false;
                }
                if (ordered) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "operation accesses a stale physical tensor state",
                                error);
                }
            }
        }
        return true;
    }

    bool readStatesInitialized(
        uint64_t operationId, MeshLoadError &error) const
    {
        for (size_t ordinal = 0;
             ordinal < geometry_.accessCount(operationId, GeometryAccessRole::Read);
             ++ordinal) {
            const GeometryAccessFact *access = geometry_.access(
                operationId, GeometryAccessRole::Read, ordinal);
            const auto initialized = access && access->read() ?
                initialized_.find(access->read()->state_id) : initialized_.end();
            if (initialized == initialized_.end()) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "read state is not initialized", error);
            }
            bool contains = false;
            if (!geometry_.contains(initialized->second, access->bytes(), contains,
                                    error)) {
                return false;
            }
            if (!contains) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "read is not dominated by initialization", error);
            }
        }
        return true;
    }

    bool expectedPartialId(
        const KernelOp &operation, const StateDefinition &definition,
        uint64_t &partialId, MeshLoadError &error) const
    {
        const auto oldState = context_.states().find(definition.oldStateId);
        if (oldState == context_.states().end())
            return fail(E_ABI_BOUNDS, "write old state is invalid", error);
        partialId = oldState->second->partial_sum_id;
        if (operation.opcode == KernelOpcode::GEMM ||
            operation.opcode == KernelOpcode::BMM) {
            if (definition.writePartialIds.size() != 1) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "grouped matrix writes disagree on partial-SUM identity",
                            error);
            }
            partialId = *definition.writePartialIds.begin();
        } else if (operation.opcode == KernelOpcode::LOCAL_COPY ||
                   operation.opcode == KernelOpcode::DMA) {
            if (operation.opcode == KernelOpcode::DMA) {
                const DmaAttrs *attrs = semanticRow(
                    operation.attrs, program_.semantic_tables.dma_attrs_rows);
                if (!attrs) {
                    return fail(E_ABI_BOUNDS,
                                "DMA attributes are invalid", error);
                }
                if (attrs->kind == semantic_abi::DmaKind::LOCAL_FILL)
                    return true;
            }
            std::set<uint64_t> sources;
            for (size_t ordinal = 0;
                 ordinal < geometry_.accessCount(
                               operation.op_id, GeometryAccessRole::Read);
                 ++ordinal) {
                const GeometryAccessFact *access = geometry_.access(
                    operation.op_id, GeometryAccessRole::Read, ordinal);
                const auto state = access && access->read() ? context_.states().find(
                    access->read()->state_id) : context_.states().end();
                if (state == context_.states().end())
                    return fail(E_ABI_BOUNDS, "local copy read state is invalid",
                                error);
                sources.insert(state->second->partial_sum_id);
            }
            if (sources.size() != 1) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "DMA sources disagree on partial-SUM identity", error);
            }
            partialId = *sources.begin();
        }
        return true;
    }

    bool publishWrites(
        const KernelOp &operation, uint64_t operationId, MeshLoadError &error)
    {
        if (operation.opcode == KernelOpcode::COLLECTIVE ||
            operation.opcode == KernelOpcode::LOCAL_REDUCE) {
            uint64_t declaredPartialId = 0;
            if (operation.opcode == KernelOpcode::COLLECTIVE) {
                const CollectiveAttrs *attrs = semanticRow(
                    operation.attrs,
                    program_.semantic_tables.collective_attrs_rows);
                if (!attrs) {
                    return fail(E_ABI_BOUNDS,
                                "collective attributes are invalid", error);
                }
                declaredPartialId = attrs->partial_sum_id;
            } else {
                const LocalReduceAttrs *attrs = semanticRow(
                    operation.attrs,
                    program_.semantic_tables.local_reduce_attrs_rows);
                if (!attrs) {
                    return fail(E_ABI_BOUNDS,
                                "local reduction attributes are invalid", error);
                }
                declaredPartialId = attrs->partial_sum_id;
            }
            std::set<uint64_t> readPartialIds;
            for (size_t ordinal = 0;
                 ordinal < geometry_.accessCount(
                               operationId, GeometryAccessRole::Read);
                 ++ordinal) {
                const GeometryAccessFact *access = geometry_.access(
                    operationId, GeometryAccessRole::Read, ordinal);
                const auto state = access && access->read() ? context_.states().find(
                    access->read()->state_id) : context_.states().end();
                if (state == context_.states().end()) {
                    return fail(E_ABI_BOUNDS,
                                "reduction read state is invalid", error);
                }
                readPartialIds.insert(state->second->partial_sum_id);
            }
            if (readPartialIds != std::set<uint64_t>{declaredPartialId}) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "reduction operands do not share the declared partial-SUM identity",
                            error);
            }
        }
        for (const auto &[newStateId, definition] : definitions_) {
            if (definition.operationId != operationId)
                continue;
            const auto oldInitialized = initialized_.find(definition.oldStateId);
            const auto oldState = context_.states().find(definition.oldStateId);
            const auto newState = context_.states().find(newStateId);
            if (oldInitialized == initialized_.end() ||
                oldState == context_.states().end() ||
                newState == context_.states().end()) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "write old state is not defined", error);
            }
            uint64_t expected = 0;
            if (!expectedPartialId(operation, definition, expected, error))
                return false;
            if (newState->second->partial_sum_id != expected &&
                !(expected != 0 && operation.opcode == KernelOpcode::LOCAL_REDUCE &&
                  newState->second->partial_sum_id == 0)) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "operation does not preserve partial-SUM dataflow",
                            error);
            }
            if (oldState->second->partial_sum_id !=
                    newState->second->partial_sum_id) {
                bool contains = false;
                if (!geometry_.contains(
                        definition.written, oldInitialized->second, contains,
                        error)) {
                    return false;
                }
                if (!contains) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "partial-SUM identity change leaves mixed object provenance",
                                error);
                }
            }
            ExactByteRegion initialized;
            if (!geometry_.unite(
                    oldInitialized->second, definition.written, initialized,
                    error)) {
                return false;
            }
            initialized_[newStateId] = std::move(initialized);
        }
        return true;
    }

    bool verifyInitialization(MeshLoadError &error)
    {
        std::vector<uint64_t> operationIds;
        if (!canonicalOperations(operationIds, error))
            return false;
        for (const uint64_t operationId : operationIds) {
            if (!readStatesInitialized(operationId, error) ||
                !publishWrites(*context_.operations().at(operationId),
                               operationId, error)) {
                return false;
            }
        }
        return true;
    }

    bool placementContributors(
        uint64_t partialId, std::vector<uint64_t> &out,
        MeshLoadError &error) const
    {
        const auto partial = context_.partialSums().find(partialId);
        if (partial == context_.partialSums().end())
            return fail(E_ABI_BOUNDS,
                        "partial-SUM consumer references an absent definition",
                        error);
        const auto placement = context_.placements().find(
            partial->second->placement_id);
        if (placement == context_.placements().end() ||
            !valueSpan(program_, placement->second->core_ids, out, error)) {
            return fail(E_ABI_BOUNDS,
                        "partial-SUM placement is invalid", error);
        }
        std::sort(out.begin(), out.end());
        return true;
    }

    bool lineageContributors(
        const std::vector<LineageFragment> &entries,
        const ExactByteRegion &required, std::vector<uint64_t> &out,
        MeshLoadError &error) const
    {
        ExactByteRegion available = geometry_.emptyBytes();
        std::set<std::vector<uint64_t>> values;
        for (const LineageFragment &entry : entries) {
            bool disjoint = false;
            if (!geometry_.disjoint(entry.bytes, required, disjoint, error))
                return false;
            if (disjoint)
                continue;
            ExactByteRegion merged;
            if (!geometry_.unite(available, entry.bytes, merged, error))
                return false;
            available = std::move(merged);
            values.insert(entry.contributors);
        }
        bool contains = false;
        if (!geometry_.contains(available, required, contains, error))
            return false;
        if (!contains) {
            return fail(E_TENSOR_NOT_RESIDENT,
                        "partial-SUM access has no complete contributor lineage",
                        error);
        }
        if (values.size() != 1) {
            return fail(E_TENSOR_NOT_RESIDENT,
                        "partial-SUM access mixes contributor lineages", error);
        }
        out = *values.begin();
        return true;
    }

    bool updateLineage(
        uint64_t oldStateId, uint64_t newStateId,
        const std::vector<LineageWrite> &writes, MeshLoadError &error)
    {
        std::vector<LineageFragment> updated;
        const auto append = [this, &updated, &error](
            ExactByteRegion bytes, const std::vector<uint64_t> &contributors) {
            bool empty = false;
            if (!geometry_.contains(
                    geometry_.emptyBytes(), bytes, empty, error)) {
                return false;
            }
            if (empty)
                return true;
            for (LineageFragment &entry : updated) {
                if (entry.contributors != contributors)
                    continue;
                ExactByteRegion merged;
                if (!geometry_.unite(entry.bytes, bytes, merged, error))
                    return false;
                entry.bytes = std::move(merged);
                return true;
            }
            updated.push_back({std::move(bytes), contributors});
            return true;
        };
        const auto previous = lineage_.find(oldStateId);
        if (previous != lineage_.end()) {
            for (const LineageFragment &entry : previous->second) {
                ExactByteRegion remainder = entry.bytes;
                for (const LineageWrite &write : writes) {
                    ExactByteRegion next;
                    if (!geometry_.subtract(
                            remainder, write.bytes, next, error)) {
                        return false;
                    }
                    remainder = std::move(next);
                }
                if (!append(std::move(remainder), entry.contributors))
                    return false;
            }
        }
        for (const LineageWrite &write : writes) {
            if (write.contributors &&
                !append(write.bytes, *write.contributors)) {
                return false;
            }
        }
        lineage_[newStateId] = std::move(updated);
        return true;
    }

    bool matrixPhase(
        const KernelOp &operation, const GemmKernelAttrs *&attrs,
        MeshLoadError &error) const
    {
        attrs = semanticRow(
            operation.attrs, program_.semantic_tables.gemm_kernel_attrs_rows);
        return attrs || fail(E_ABI_BOUNDS,
                             "matrix kernel attributes are invalid", error);
    }

    bool sameLogicalRegion(
        const GeometryAccessFact &left, const GeometryAccessFact &right) const
    {
        return left.globalOrigin() == right.globalOrigin() &&
            left.shape() == right.shape();
    }

    bool verifyMatrixChain(
        const KernelOp &terminal, uint64_t computationId, uint64_t ownerCore,
        uint64_t partialId,
        const GeometryAccessFact &expectedRegion, MeshLoadError &error) const
    {
        const KernelOp *producer = &terminal;
        std::vector<const KernelOp *> chain;
        std::set<uint64_t> seenStates;
        while (true) {
            const GemmKernelAttrs *attrs = nullptr;
            if ((producer->opcode != KernelOpcode::GEMM &&
                 producer->opcode != KernelOpcode::BMM) ||
                producer->computation_id != computationId ||
                producer->owner_core != ownerCore ||
                geometry_.accessCount(producer->op_id, GeometryAccessRole::Write) !=
                    1) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "matrix accumulator has an invalid producer", error);
            }
            if (!matrixPhase(*producer, attrs, error))
                return false;
            if (attrs->partial_sum_id != partialId) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "matrix accumulator has an invalid producer", error);
            }
            const GeometryAccessFact *write = geometry_.access(
                producer->op_id, GeometryAccessRole::Write, 0);
            const MatrixContractionFact *contraction = matrixFacts_.contraction(
                producer->op_id);
            if (!write || !sameLogicalRegion(*write, expectedRegion) ||
                !contraction || !contraction->bounds()) {
                return fail(E_EXPORT_LAYOUT,
                            "matrix accumulator region differs from its producer",
                            error);
            }
            chain.push_back(producer);
            if (attrs->phase == MatrixPhase::ACCUMULATE_FIRST ||
                attrs->phase == MatrixPhase::ACCUMULATE_ONLY) {
                break;
            }
            if ((attrs->phase != MatrixPhase::ACCUMULATE_CONTINUE &&
                 attrs->phase != MatrixPhase::ACCUMULATE_FINAL) ||
                geometry_.accessCount(
                    producer->op_id, GeometryAccessRole::Read) != 3) {
                return fail(E_EXPORT_LAYOUT,
                            "matrix accumulator phase chain is invalid", error);
            }
            const GeometryAccessFact *accumulator = geometry_.access(
                producer->op_id, GeometryAccessRole::Read, 2);
            const uint64_t *producerId = accumulator && accumulator->read() ?
                intrinsic_.stateProducer(accumulator->read()->state_id) : nullptr;
            if (!accumulator || !producerId ||
                !seenStates.insert(accumulator->read()->state_id).second) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "matrix accumulator chain has no producer", error);
            }
            const auto next = context_.operations().find(*producerId);
            if (next == context_.operations().end()) {
                return fail(E_TENSOR_NOT_RESIDENT,
                            "matrix accumulator chain has no producer", error);
            }
            producer = next->second;
        }
        std::reverse(chain.begin(), chain.end());
        for (size_t index = 0; index < chain.size(); ++index) {
            const GemmKernelAttrs *attrs = nullptr;
            if (!matrixPhase(*chain[index], attrs, error))
                return false;
            const MatrixPhase expected = chain.size() == 1 ?
                MatrixPhase::ACCUMULATE_ONLY :
                (index == 0 ? MatrixPhase::ACCUMULATE_FIRST :
                 (index + 1 == chain.size() ? MatrixPhase::ACCUMULATE_FINAL :
                  MatrixPhase::ACCUMULATE_CONTINUE));
            if (attrs->phase != expected) {
                return fail(E_EXPORT_LAYOUT,
                            "matrix accumulator phase sequence is incomplete",
                            error);
            }
        }
        const MatrixContractionBounds *first = matrixFacts_.contraction(
            chain.front()->op_id)->bounds();
        uint64_t cursor = first->domainBegin();
        for (const KernelOp *item : chain) {
            const MatrixContractionBounds *bounds = matrixFacts_.contraction(
                item->op_id)->bounds();
            if (bounds->domainBegin() != first->domainBegin() ||
                bounds->domainEnd() != first->domainEnd() ||
                bounds->intervalBegin() != cursor ||
                bounds->intervalEnd() > first->domainEnd()) {
                return fail(E_EXPORT_LAYOUT,
                            "matrix accumulator K regions are not an exact cover",
                            error);
            }
            cursor = bounds->intervalEnd();
        }
        return cursor == first->domainEnd() ||
            fail(E_EXPORT_LAYOUT,
                 "matrix accumulator K regions do not cover the contraction domain",
                 error);
    }

    bool verifyMatrixCompletion(MeshLoadError &error) const
    {
        std::set<std::pair<std::pair<uint64_t, uint64_t>,
                           std::pair<std::vector<uint64_t>,
                                     std::vector<uint64_t>>>> published;
        std::vector<uint64_t> operationIds;
        if (!canonicalOperations(operationIds, error))
            return false;
        for (const uint64_t operationId : operationIds) {
            const KernelOp *operation = context_.operations().at(operationId);
            if (operation->opcode != KernelOpcode::MATRIX_EPILOGUE ||
                !intrinsic_.operation(operationId)) {
                continue;
            }
            const size_t reads = geometry_.accessCount(
                operationId, GeometryAccessRole::Read);
            const size_t writes = geometry_.accessCount(
                operationId, GeometryAccessRole::Write);
            if (reads == 0 && writes == 0)
                continue;
            const GeometryAccessFact *input = geometry_.access(
                operationId, GeometryAccessRole::Read, 0);
            const GeometryAccessFact *output = geometry_.access(
                operationId, GeometryAccessRole::Write, 0);
            if (!input || !input->read() || !output) {
                return fail(E_ABI_BOUNDS,
                            "matrix epilogue geometry is invalid", error);
            }
            const auto completed = completedPartialStates_.find(
                input->read()->state_id);
            if (completed != completedPartialStates_.end()) {
                const auto partial = context_.partialSums().find(
                    completed->second);
                if (partial == context_.partialSums().end() ||
                    partial->second->computation_id != operation->computation_id) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "matrix epilogue consumes another computation's collective result",
                                error);
                }
            } else {
                const uint64_t *producerId = intrinsic_.stateProducer(
                    input->read()->state_id);
                const auto producer = producerId ? context_.operations().find(
                    *producerId) : context_.operations().end();
                if (producer == context_.operations().end()) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "matrix epilogue has no complete accumulator chain",
                                error);
                }
                if (!verifyMatrixChain(
                        *producer->second, operation->computation_id,
                        operation->owner_core, 0, *input, error)) {
                    return false;
                }
            }
            const auto key = std::make_pair(
                std::make_pair(operation->computation_id, operation->owner_core),
                std::make_pair(output->globalOrigin(), output->shape()));
            if (!published.insert(key).second) {
                return fail(E_EXPORT_LAYOUT,
                            "matrix result region has multiple epilogues", error);
            }
        }
        return true;
    }

    bool verifyPartialContributors(MeshLoadError &error)
    {
        std::vector<uint64_t> operationIds;
        if (!canonicalOperations(operationIds, error))
            return false;
        for (const uint64_t operationId : operationIds) {
            const KernelOp *operation = context_.operations().at(operationId);
            bool ordinary = operation->opcode != KernelOpcode::COLLECTIVE &&
                operation->opcode != KernelOpcode::LOCAL_REDUCE &&
                operation->opcode != KernelOpcode::LOCAL_COPY;
            const DmaAttrs *dma = nullptr;
            if (operation->opcode == KernelOpcode::DMA) {
                dma = semanticRow(
                    operation->attrs, program_.semantic_tables.dma_attrs_rows);
                if (!dma) {
                    return fail(E_ABI_BOUNDS,
                                "DMA attributes are invalid", error);
                }
                ordinary = dma->kind != semantic_abi::DmaKind::P2P_PUSH;
            }
            if (ordinary) {
                const GemmKernelAttrs *matrix = nullptr;
                if (operation->opcode == KernelOpcode::GEMM ||
                    operation->opcode == KernelOpcode::BMM) {
                    if (!matrixPhase(*operation, matrix, error))
                        return false;
                }
                for (size_t ordinal = 0;
                     ordinal < geometry_.accessCount(
                                   operationId, GeometryAccessRole::Read);
                     ++ordinal) {
                    const GeometryAccessFact *access = geometry_.access(
                        operationId, GeometryAccessRole::Read, ordinal);
                    const auto state = access && access->read() ?
                        context_.states().find(access->read()->state_id) :
                        context_.states().end();
                    if (state == context_.states().end()) {
                        return fail(E_ABI_BOUNDS,
                                    "partial-SUM read state is invalid", error);
                    }
                    if (state->second->partial_sum_id == 0 ||
                        (matrix &&
                         (matrix->phase == MatrixPhase::ACCUMULATE_CONTINUE ||
                          matrix->phase == MatrixPhase::ACCUMULATE_FINAL) &&
                         ordinal == 2)) {
                        continue;
                    }
                    std::vector<uint64_t> expected;
                    std::vector<uint64_t> actual;
                    const auto lineage = lineage_.find(state->second->state_id);
                    if (!placementContributors(
                            state->second->partial_sum_id, expected, error) ||
                        !lineageContributors(
                            lineage == lineage_.end() ?
                                std::vector<LineageFragment>{} : lineage->second,
                            access->bytes(), actual, error)) {
                        return false;
                    }
                    if (actual != expected) {
                        return fail(E_TENSOR_NOT_RESIDENT,
                                    "ordinary consumer reads an incomplete partial-SUM region",
                                    error);
                    }
                    if (operation->opcode == KernelOpcode::MATRIX_EPILOGUE) {
                        const auto partial = context_.partialSums().find(
                            state->second->partial_sum_id);
                        if (partial == context_.partialSums().end() ||
                            partial->second->computation_id !=
                                operation->computation_id) {
                            return fail(E_TENSOR_NOT_RESIDENT,
                                        "matrix epilogue consumes another computation's partial-SUM",
                                        error);
                        }
                    }
                    completedPartialStates_[state->second->state_id] =
                        state->second->partial_sum_id;
                }
            }

            if (geometry_.accessCount(
                    operationId, GeometryAccessRole::Write) == 0) {
                continue;
            }

            std::map<size_t, std::optional<std::vector<uint64_t>>> payloads;
            if (operation->opcode == KernelOpcode::GEMM ||
                operation->opcode == KernelOpcode::BMM) {
                const GemmKernelAttrs *matrix = nullptr;
                if (!matrixPhase(*operation, matrix, error))
                    return false;
                if (matrix->phase == MatrixPhase::ACCUMULATE_ONLY ||
                    matrix->phase == MatrixPhase::ACCUMULATE_FINAL) {
                    const GeometryAccessFact *write = geometry_.access(
                        operationId, GeometryAccessRole::Write, 0);
                    const auto state = write && write->write() ?
                        context_.states().find(write->write()->new_state_id) :
                        context_.states().end();
                    if (state == context_.states().end()) {
                        return fail(E_ABI_BOUNDS,
                                    "matrix write state is invalid", error);
                    }
                    if (state->second->partial_sum_id != 0 &&
                        !verifyMatrixChain(
                            *operation, operation->computation_id,
                            operation->owner_core,
                            state->second->partial_sum_id, *write, error)) {
                        return false;
                    }
                    if (state->second->partial_sum_id != 0)
                        payloads.emplace(0,
                                         std::vector<uint64_t>{operation->owner_core});
                }
            } else if (operation->opcode == KernelOpcode::LOCAL_COPY ||
                       (operation->opcode == KernelOpcode::DMA &&
                        dma->kind != semantic_abi::DmaKind::LOCAL_FILL)) {
                for (size_t ordinal = 0;
                     ordinal < geometry_.accessCount(
                                   operationId, GeometryAccessRole::Read);
                     ++ordinal) {
                    const GeometryAccessFact *access = geometry_.access(
                        operationId, GeometryAccessRole::Read, ordinal);
                    const auto state = access && access->read() ?
                        context_.states().find(access->read()->state_id) :
                        context_.states().end();
                    if (state == context_.states().end()) {
                        return fail(E_ABI_BOUNDS,
                                    "movement read state is invalid", error);
                    }
                    const auto lineage = lineage_.find(state->second->state_id);
                    if (state->second->partial_sum_id == 0 &&
                        (lineage == lineage_.end() || lineage->second.empty())) {
                        continue;
                    }
                    std::vector<uint64_t> contributors;
                    if (!lineageContributors(
                            lineage == lineage_.end() ?
                                std::vector<LineageFragment>{} : lineage->second,
                            access->bytes(), contributors, error)) {
                        return false;
                    }
                    payloads.emplace(ordinal, std::move(contributors));
                }
            } else if (operation->opcode == KernelOpcode::LOCAL_REDUCE) {
                const size_t writes = geometry_.accessCount(
                    operationId, GeometryAccessRole::Write);
                const size_t reads = geometry_.accessCount(
                    operationId, GeometryAccessRole::Read);
                if (writes == 0 || reads % writes != 0)
                    return fail(E_ABI_BOUNDS,
                                "local reduction accesses are invalid", error);
                const size_t fanIn = reads / writes;
                for (size_t write = 0; write < writes; ++write) {
                    std::vector<uint64_t> contributors;
                    for (size_t input = 0; input < fanIn; ++input) {
                        const GeometryAccessFact *access = geometry_.access(
                            operationId, GeometryAccessRole::Read,
                            write * fanIn + input);
                        const auto state = access && access->read() ?
                            context_.states().find(access->read()->state_id) :
                            context_.states().end();
                        if (state == context_.states().end()) {
                            return fail(E_ABI_BOUNDS,
                                        "local reduction read state is invalid",
                                        error);
                        }
                        const auto lineage = lineage_.find(state->second->state_id);
                        std::vector<uint64_t> source;
                        if (!lineageContributors(
                                lineage == lineage_.end() ?
                                    std::vector<LineageFragment>{} :
                                    lineage->second,
                                access->bytes(), source, error)) {
                            return false;
                        }
                        contributors.insert(
                            contributors.end(), source.begin(), source.end());
                    }
                    std::sort(contributors.begin(), contributors.end());
                    payloads.emplace(write, std::move(contributors));
                }
            }

            std::set<uint64_t> sourcePartialIds;
            if (operation->opcode == KernelOpcode::DMA ||
                operation->opcode == KernelOpcode::LOCAL_REDUCE ||
                operation->opcode == KernelOpcode::LOCAL_COPY) {
                for (size_t ordinal = 0;
                     ordinal < geometry_.accessCount(
                                   operationId, GeometryAccessRole::Read);
                     ++ordinal) {
                    const GeometryAccessFact *access = geometry_.access(
                        operationId, GeometryAccessRole::Read, ordinal);
                    const auto state = access && access->read() ?
                        context_.states().find(access->read()->state_id) :
                        context_.states().end();
                    if (state == context_.states().end()) {
                        return fail(E_ABI_BOUNDS,
                                    "partial-SUM source state is invalid", error);
                    }
                    if (state->second->partial_sum_id != 0)
                        sourcePartialIds.insert(state->second->partial_sum_id);
                }
            }
            for (const auto &[newStateId, definition] : definitions_) {
                if (definition.operationId != operationId)
                    continue;
                std::vector<LineageWrite> writes;
                writes.reserve(definition.writeOrdinals.size());
                for (const size_t ordinal : definition.writeOrdinals) {
                    const GeometryAccessFact *access = geometry_.access(
                        operationId, GeometryAccessRole::Write, ordinal);
                    if (!access) {
                        return fail(E_ABI_BOUNDS,
                                    "partial-SUM write geometry is invalid",
                                    error);
                    }
                    const auto payload = payloads.find(ordinal);
                    writes.push_back({access->bytes(), payload == payloads.end() ?
                        std::nullopt : payload->second});
                }
                if (!updateLineage(
                        definition.oldStateId, newStateId, writes, error)) {
                    return false;
                }
                const auto oldState = context_.states().find(
                    definition.oldStateId);
                const auto newState = context_.states().find(newStateId);
                if (oldState == context_.states().end() ||
                    newState == context_.states().end()) {
                    return fail(E_ABI_BOUNDS,
                                "partial-SUM state definition is invalid", error);
                }
                std::set<uint64_t> partialIds = sourcePartialIds;
                if (oldState->second->partial_sum_id != 0)
                    partialIds.insert(oldState->second->partial_sum_id);
                if (operation->opcode == KernelOpcode::LOCAL_REDUCE) {
                    const LocalReduceAttrs *attrs = semanticRow(
                        operation->attrs,
                        program_.semantic_tables.local_reduce_attrs_rows);
                    if (!attrs) {
                        return fail(E_ABI_BOUNDS,
                                    "local reduction attributes are invalid",
                                    error);
                    }
                    partialIds.insert(attrs->partial_sum_id);
                }
                if (newState->second->partial_sum_id == 0 &&
                    !partialIds.empty()) {
                    if (partialIds.size() != 1) {
                        return fail(E_TENSOR_NOT_RESIDENT,
                                    "partial-SUM completion mixes definitions",
                                    error);
                    }
                    std::vector<uint64_t> expected;
                    std::vector<uint64_t> actual;
                    const auto initialized = initialized_.find(newStateId);
                    if (initialized == initialized_.end() ||
                        !placementContributors(
                            *partialIds.begin(), expected, error) ||
                        !lineageContributors(
                            lineage_[newStateId], initialized->second, actual,
                            error)) {
                        return false;
                    }
                    if (actual != expected) {
                        return fail(E_TENSOR_NOT_RESIDENT,
                                    "partial-SUM completion has missing or repeated contributors",
                                    error);
                    }
                    completedPartialStates_[newStateId] = *partialIds.begin();
                }
            }
        }
        return verifyMatrixCompletion(error);
    }

    bool rejectUnorderedHazards(MeshLoadError &error) const
    {
        for (size_t first = 0; first < accesses_.size(); ++first) {
            for (size_t second = first + 1; second < accesses_.size(); ++second) {
                const AccessRecord &left = accesses_[first];
                const AccessRecord &right = accesses_[second];
                if (left.operationId == right.operationId ||
                    left.objectId != right.objectId ||
                    (!left.write && !right.write)) {
                    continue;
                }
                bool disjoint = false;
                if (!geometry_.disjoint(
                        left.bytes, right.bytes, disjoint, error)) {
                    return false;
                }
                if (disjoint)
                    continue;
                bool ordered = false;
                if (!happensBefore(
                        left.operationId, right.operationId, ordered, error)) {
                    return false;
                }
                if (!ordered && !happensBefore(
                                    right.operationId, left.operationId,
                                    ordered, error)) {
                    return false;
                }
                if (!ordered) {
                    return fail(E_TENSOR_NOT_RESIDENT,
                                "overlapping physical accesses are unordered",
                                error);
                }
            }
        }
        return true;
    }

    const DecodedProgram &program_;
    const ProgramSemanticContext &context_;
    const VerifiedProgramGeometry &geometry_;
    const VerifiedIntrinsicDependencyFacts &intrinsic_;
    const VerifiedMatrixContractionFacts &matrixFacts_;
    std::map<uint64_t, ExactByteRegion> initialized_;
    std::map<uint64_t, StateDefinition> definitions_;
    std::vector<AccessRecord> accesses_;
    std::map<uint64_t, std::vector<LineageFragment>> lineage_;
    std::map<uint64_t, uint64_t> completedPartialStates_;
};

}

bool
verifyProgramIntrinsicMemoryDomain(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const VerifiedIntrinsicDependencyFacts &intrinsic,
    const VerifiedMatrixContractionFacts &matrixFacts, MeshLoadError &error)
{
    if (!context.matches(program) || !geometry.matches(program, context) ||
        !intrinsic.matches(program, context) ||
        !matrixFacts.matches(program, context, geometry)) {
        return fail(E_ABI_BOUNDS,
                    "intrinsic memory facts belong to another verification invocation",
                    error);
    }
    return IntrinsicMemoryBuilder(
        program, context, geometry, intrinsic, matrixFacts).run(error);
}

}
}

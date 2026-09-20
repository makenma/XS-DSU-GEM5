#include "dev/ai_mesh/mesh_ir_lifetime_verifier.hh"

#include <algorithm>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_dependency_facts_detail.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"

namespace gem5
{
namespace ai_mesh
{
namespace detail
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
struct ObjectFrontier
{
    uint64_t objectId = 0;
    uint64_t variantId = 0;
    const BufferObject *object = nullptr;
    const Allocation *allocation = nullptr;
    bool entryResident = false;
    bool derived = false;
    std::vector<IntrinsicNode> starts;
    std::vector<IntrinsicNode> completions;
    std::vector<IntrinsicNode> minimal;
    std::vector<IntrinsicNode> maximal;
};

struct LifetimeBuilder
{
    const DecodedProgram &program;
    const RuntimeArch &arch;
    const ProgramSemanticContext &context;
    const VerifiedProgramGeometry &geometry;
    const VerifiedProgramBacking &backing;
    const VerifiedIntrinsicDependencyFacts &intrinsic;
    std::map<uint64_t, ObjectFrontier> frontiers;

    LifetimeBuilder(
        const DecodedProgram &program, const RuntimeArch &arch,
        const ProgramSemanticContext &context,
        const VerifiedProgramGeometry &geometry,
        const VerifiedProgramBacking &backing,
        const VerifiedIntrinsicDependencyFacts &intrinsic)
        : program(program), arch(arch), context(context), geometry(geometry),
          backing(backing), intrinsic(intrinsic)
    {}

    bool
    admitObjects(MeshLoadError &error)
    {
        std::set<uint64_t> entryResidents;
        for (const auto &[stateId, state] : context.states()) {
            static_cast<void>(stateId);
            if (state->version != 0 ||
                state->origin != StateOrigin::PRE_RESIDENT) {
                continue;
            }
            const auto object = context.objects().find(state->object_id);
            if (object != context.objects().end() &&
                object->second->memory_space ==
                    semantic_abi::MemorySpace::CORE_SRAM &&
                object->second->footprint_bytes != 0) {
                entryResidents.insert(state->object_id);
            }
        }
        for (const auto &[objectId, object] : context.objects()) {
            if (object->memory_space !=
                semantic_abi::MemorySpace::CORE_SRAM) {
                continue;
            }
            const Allocation *allocation = backing.local(objectId);
            const ProgramVariant *variant = context.owner("objects", objectId);
            if (!allocation || !variant) {
                return fail(E_ABI_BOUNDS,
                            "lifetime object backing is unavailable", error);
            }
            ObjectFrontier frontier;
            frontier.objectId = objectId;
            frontier.variantId = variant->variant_id;
            frontier.object = object;
            frontier.allocation = allocation;
            frontier.entryResident = entryResidents.count(objectId) != 0;
            frontiers.emplace(objectId, std::move(frontier));
        }
        return true;
    }

    bool
    admitAccesses(MeshLoadError &error)
    {
        for (const auto &[operationId, stateIds] : intrinsic.operandStates_) {
            const KernelOperationFact *operation = intrinsic.operation(operationId);
            if (!operation) {
                return fail(E_ABI_BOUNDS,
                            "lifetime operation fact is unavailable", error);
            }
            std::set<uint64_t> objects;
            for (uint64_t stateId : stateIds) {
                const auto state = context.states().find(stateId);
                if (state == context.states().end()) {
                    return fail(E_ABI_BOUNDS,
                                "lifetime operand state is unavailable", error);
                }
                if (frontiers.count(state->second->object_id) != 0) {
                    objects.insert(state->second->object_id);
                }
            }
            for (uint64_t objectId : objects) {
                ObjectFrontier &frontier = frontiers.at(objectId);
                frontier.starts.push_back(operation->start());
                frontier.completions.push_back(operation->completion());
            }
        }
        return true;
    }

    bool
    deriveFrontier(ObjectFrontier &frontier, MeshLoadError &error)
    {
        if (frontier.derived) {
            return true;
        }
        if (frontier.entryResident) {
            const IntrinsicNode *begin = intrinsic.invocationBegin(
                frontier.variantId);
            if (!begin) {
                return fail(E_ABI_BOUNDS,
                            "lifetime invocation boundary is unavailable", error);
            }
            frontier.minimal.push_back(*begin);
        } else {
            for (const IntrinsicNode &start : frontier.starts) {
                bool preceded = false;
                for (const IntrinsicNode &completion : frontier.completions) {
                    bool ordered = false;
                    if (!intrinsic.intrinsicHappensBefore(
                            completion, start, ordered, error)) {
                        return false;
                    }
                    if (ordered) {
                        preceded = true;
                        break;
                    }
                }
                if (!preceded) {
                    frontier.minimal.push_back(start);
                }
            }
        }
        for (const IntrinsicNode &completion : frontier.completions) {
            bool precedes = false;
            for (const IntrinsicNode &start : frontier.starts) {
                bool ordered = false;
                if (!intrinsic.intrinsicHappensBefore(
                        completion, start, ordered, error)) {
                    return false;
                }
                if (ordered) {
                    precedes = true;
                    break;
                }
            }
            if (!precedes) {
                frontier.maximal.push_back(completion);
            }
        }
        if (frontier.entryResident && frontier.maximal.empty()) {
            const IntrinsicNode *begin = intrinsic.invocationBegin(
                frontier.variantId);
            if (!begin) {
                return fail(E_ABI_BOUNDS,
                            "lifetime invocation boundary is unavailable", error);
            }
            frontier.maximal.push_back(*begin);
        }
        frontier.derived = true;
        return true;
    }

    bool
    frontiersOrdered(
        const ObjectFrontier &first, const ObjectFrontier &second,
        bool &ordered, MeshLoadError &error) const
    {
        ordered = false;
        if (first.maximal.empty() || second.minimal.empty()) {
            return true;
        }
        for (const IntrinsicNode &completion : first.maximal) {
            for (const IntrinsicNode &start : second.minimal) {
                bool pairOrdered = false;
                if (!intrinsic.intrinsicHappensBefore(
                        completion, start, pairOrdered, error)) {
                    return false;
                }
                if (!pairOrdered) {
                    return true;
                }
            }
        }
        ordered = true;
        return true;
    }

    bool
    rejectConflicts(MeshLoadError &error)
    {
        std::map<std::pair<uint64_t, uint64_t>,
                 std::vector<ObjectFrontier *>> byCore;
        for (auto &[objectId, frontier] : frontiers) {
            static_cast<void>(objectId);
            byCore[{frontier.variantId, frontier.allocation->owner_core}]
                .push_back(&frontier);
        }
        for (auto &[owner, objects] : byCore) {
            static_cast<void>(owner);
            std::sort(
                objects.begin(), objects.end(),
                [](const ObjectFrontier *first, const ObjectFrontier *second) {
                    if (first->allocation->offset_bytes !=
                        second->allocation->offset_bytes) {
                        return first->allocation->offset_bytes <
                            second->allocation->offset_bytes;
                    }
                    return first->objectId < second->objectId;
                });
            for (size_t firstIndex = 0; firstIndex < objects.size();
                 ++firstIndex) {
                ObjectFrontier &first = *objects[firstIndex];
                if (first.allocation->size_bytes == 0) {
                    continue;
                }
                const uint64_t firstEnd = first.allocation->offset_bytes +
                    first.allocation->size_bytes;
                for (size_t secondIndex = firstIndex + 1;
                     secondIndex < objects.size() &&
                     objects[secondIndex]->allocation->offset_bytes < firstEnd;
                     ++secondIndex) {
                    ObjectFrontier &second = *objects[secondIndex];
                    if (second.allocation->size_bytes == 0) {
                        continue;
                    }
                    if (first.object->persistent || second.object->persistent) {
                        return fail(
                            E_SRAM_OOM,
                            "overlapping local allocations have conflicting lifetimes",
                            error);
                    }
                    if (!deriveFrontier(first, error) ||
                        !deriveFrontier(second, error)) {
                        return false;
                    }
                    if (first.minimal.empty() || second.minimal.empty()) {
                        continue;
                    }
                    bool firstBeforeSecond = false;
                    bool secondBeforeFirst = false;
                    if (!frontiersOrdered(
                            first, second, firstBeforeSecond, error)) {
                        return false;
                    }
                    if (!firstBeforeSecond &&
                        !frontiersOrdered(
                            second, first, secondBeforeFirst, error))
                        return false;
                    if (!firstBeforeSecond && !secondBeforeFirst) {
                        return fail(
                            E_SRAM_OOM,
                            "overlapping local allocations have conflicting lifetimes",
                            error);
                    }
                }
            }
        }
        return true;
    }

    bool
    run(MeshLoadError &error)
    {
        if (!backing.matches(program, context, arch, geometry) ||
            !intrinsic.matches(program, context)) {
            return fail(E_ABI_BOUNDS,
                        "lifetime facts belong to another verification invocation",
                        error);
        }
        if (!admitObjects(error) || !admitAccesses(error) ||
            !rejectConflicts(error)) {
            return false;
        }
        error = {};
        return true;
    }
};

}

bool
verifyProgramLifetimeDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedIntrinsicDependencyFacts &intrinsic, MeshLoadError &error)
{
    return detail::LifetimeBuilder(
        program, arch, context, geometry, backing, intrinsic).run(error);
}

}
}

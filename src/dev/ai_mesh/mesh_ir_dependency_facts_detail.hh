#ifndef GEM5_DEV_AI_MESH_MESH_IR_DEPENDENCY_FACTS_DETAIL_HH
#define GEM5_DEV_AI_MESH_MESH_IR_DEPENDENCY_FACTS_DETAIL_HH

#include <algorithm>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_enum_traits.hh"
#include "dev/ai_mesh/mesh_ir_semantic_context.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{
namespace detail
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;

inline bool
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
    if (reference.section_type != Traits::section_type || reference.row_id == 0 ||
        reference.row_id > rows.size())
        return nullptr;
    return &rows[reference.row_id - 1];
}

inline const std::string *
semanticString(const DecodedProgram &program, const StringRef &reference)
{
    if (reference.string_id == 0 ||
        reference.string_id > program.semantic_strings.size())
        return nullptr;
    return &program.semantic_strings[reference.string_id - 1];
}

inline bool
effectful(const KernelOp &operation)
{
    return operation.opcode != KernelOpcode::ALLOC &&
        operation.opcode != KernelOpcode::VIEW;
}

inline bool
sameOwner(
    const ProgramSemanticContext &context, std::string_view leftField,
    uint64_t leftId, std::string_view rightField, uint64_t rightId)
{
    const ProgramVariant *left = context.owner(leftField, leftId);
    return left && left == context.owner(rightField, rightId);
}

struct NodeOrderKey
{
    std::string stable;
    std::string opcode;
    std::vector<uint64_t> operands;
    size_t node = 0;

    bool operator<(const NodeOrderKey &other) const
    {
        return std::tie(stable, opcode, operands, node) <
            std::tie(other.stable, other.opcode, other.operands, other.node);
    }
};

inline std::vector<size_t>
rankNodes(std::vector<NodeOrderKey> keys)
{
    std::sort(keys.begin(), keys.end());
    std::vector<size_t> ranks(keys.size());
    for (size_t rank = 0; rank < keys.size(); ++rank)
        ranks[keys[rank].node - 1] = rank;
    return ranks;
}

inline std::string
opcodeName(KernelOpcode opcode)
{
    return std::string(SemanticEnumTraits<KernelOpcode>::pythonValue(
        opcode).string_value);
}

inline bool
valueSpan(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<uint64_t> &values, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_u64_values.size()))
        return fail(E_ABI_BOUNDS, "semantic value span is invalid", error);
    values.assign(
        program.semantic_u64_values.begin() + size_t(span.begin),
        program.semantic_u64_values.begin() +
            size_t(span.begin) + size_t(span.count));
    return true;
}

inline bool
operationAccesses(
    const DecodedProgram &program, const KernelOp &operation,
    std::vector<const OperandAccess *> &reads,
    std::vector<const StateTransition *> &writes, MeshLoadError &error)
{
    if (!spanFits(operation.reads.begin, operation.reads.count,
                  program.semantic_references.size()) ||
        !spanFits(operation.writes.begin, operation.writes.count,
                  program.semantic_references.size()))
        return fail(E_ABI_BOUNDS, "Kernel access list is invalid", error);
    reads.clear();
    writes.clear();
    for (uint64_t index = 0; index < operation.reads.count; ++index) {
        const OperandAccess *access = semanticRow(
            program.semantic_references[size_t(operation.reads.begin + index)],
            program.semantic_tables.operand_access_rows);
        if (!access)
            return fail(E_ABI_BOUNDS, "operand access is invalid", error);
        reads.push_back(access);
    }
    for (uint64_t index = 0; index < operation.writes.count; ++index) {
        const StateTransition *transition = semanticRow(
            program.semantic_references[size_t(operation.writes.begin + index)],
            program.semantic_tables.state_transition_rows);
        if (!transition)
            return fail(E_ABI_BOUNDS, "state transition is invalid", error);
        writes.push_back(transition);
    }
    return true;
}

}
}
}

#endif

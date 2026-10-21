#include "dev/ai_mesh/mesh_ir_gather.hh"

#include <algorithm>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5::ai_mesh
{
namespace
{
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;

bool fail(const char *code, const char *message, MeshLoadError &error)
{
    error = {code, message};
    return false;
}

template <typename Record>
const Record *row(const SemanticRef &ref, const std::vector<Record> &records)
{
    if (ref.section_type != SemanticRecordTraits<Record>::section_type ||
        ref.row_id == 0 || ref.row_id > records.size())
        return nullptr;
    return &records[ref.row_id - 1];
}

bool values(const DecodedProgram &program, const ListSpan &span,
            std::vector<uint64_t> &out)
{
    if (!spanFits(span.begin, span.count, program.semantic_u64_values.size()))
        return false;
    out.assign(program.semantic_u64_values.begin() + span.begin,
               program.semantic_u64_values.begin() + span.begin + span.count);
    return true;
}
}

bool GatherGeometry::derive(
    const DecodedProgram &program, const VerifiedProgramGeometry &geometry,
    const mesh_abi::semantic_abi::KernelOp &operation, GatherGeometry &out,
    MeshLoadError &error)
{
    using namespace mesh_abi::semantic_abi;
    using namespace mesh_diagnostics;
    const auto *attrs = row(operation.attrs, program.semantic_tables.dma_attrs_rows);
    if (operation.opcode != KernelOpcode::DMA || !attrs ||
        attrs->kind != DmaKind::GATHER_LOAD || operation.reads.count != 2 ||
        operation.writes.count != 1)
        return fail(E_DMA_RANGE, "gather requires two reads and one write", error);
    if (attrs->fill_pattern.count || attrs->transfer_id ||
        attrs->source_core != UINT16_MAX ||
        attrs->destination_core != attrs->issuing_core ||
        operation.owner_core != attrs->issuing_core)
        return fail(E_DMA_RANGE, "gather attributes or core identities are invalid", error);
    const auto *index = geometry.access(operation.op_id, GeometryAccessRole::Read, 0);
    const auto *table = geometry.access(operation.op_id, GeometryAccessRole::Read, 1);
    const auto *target = geometry.access(operation.op_id, GeometryAccessRole::Write, 0);
    if (!index || !table || !target)
        return fail(E_ABI_BOUNDS, "gather accesses have no verified geometry", error);
    for (const auto *local : {index, target})
        if (local->object().memory_space != MemorySpace::CORE_SRAM ||
            local->object().owner_core != attrs->issuing_core)
            return fail(E_TENSOR_NOT_RESIDENT, "gather local access owner is invalid", error);
    if (table->object().memory_space != MemorySpace::HBM &&
        table->object().memory_space != MemorySpace::HOST_SHARED)
        return fail(E_DMA_RANGE, "gather table must be external", error);
    if (index->tensor().dtype != DType::INT32 ||
        table->tensor().dtype != target->tensor().dtype)
        return fail(E_EXPORT_DTYPE, "gather index or result dtype is invalid", error);
    std::vector<uint64_t> tableShape, tableValid, tableStrides, targetStrides;
    if (!values(program, table->tensor().shape, tableShape) ||
        !values(program, table->view().valid_shape, tableValid) ||
        !values(program, table->view().object_strides, tableStrides) ||
        !values(program, target->view().object_strides, targetStrides))
        return fail(E_ABI_BOUNDS, "gather view metadata is invalid", error);
    if (tableShape.size() != 2 || tableStrides.size() != 2 ||
        table->shape() != tableShape || tableValid != tableShape ||
        table->localOrigin() != std::vector<uint64_t>({0, 0}) ||
        table->steps() != std::vector<uint64_t>({1, 1}))
        return fail(E_DMA_RANGE, "gather source must cover the complete logical table", error);
    if (tableStrides[1] != 1 || tableStrides[0] < tableShape[1])
        return fail(E_EXPORT_LAYOUT, "gather table rows are not contiguous and disjoint", error);
    if (target->shape().size() != 2 || targetStrides.size() != 2 ||
        target->shape()[1] != tableShape[1] || targetStrides[1] != 1 ||
        targetStrides[0] < target->shape()[1] ||
        target->steps() != std::vector<uint64_t>({1, 1}))
        return fail(E_EXPORT_LAYOUT, "gather destination rows are invalid", error);
    GatherGeometry candidate;
    candidate.rows = target->shape()[0];
    candidate.tableRows = tableShape[0];
    if (index->logicalElementCount() != candidate.rows)
        return fail(E_DMA_RANGE, "gather index count differs from destination rows", error);
    uint64_t width = 0;
    if (!dtypeByteWidth(table->tensor().dtype, width))
        return fail(E_EXPORT_DTYPE, "gather table dtype is invalid", error);
    bool overflow = false;
    candidate.rowBytes = checkedMul(tableShape[1], width, overflow);
    candidate.sourceStrideBytes = checkedMul(tableStrides[0], width, overflow);
    candidate.destinationStrideBytes = checkedMul(targetStrides[0], width, overflow);
    const uint64_t viewOffset = checkedMul(index->view().object_offset_elements, 4, overflow);
    if (overflow || index->objectByteOffset() < viewOffset)
        return fail(E_ABI_OVERFLOW, "gather index view offset is invalid", error);
    candidate.indexOffsetBytes = index->objectByteOffset() - viewOffset;
    candidate.indexStrideBytes = 4;
    uint64_t inner = 1;
    bool found = false;
    for (size_t axis = index->shape().size(); axis-- > 0;) {
        const uint64_t extent = index->shape()[axis];
        if (extent <= 1)
            continue;
        if (!found) {
            candidate.indexStrideBytes = index->objectByteStrides()[axis];
            found = true;
        } else if (index->objectByteStrides()[axis] !=
                   checkedMul(inner, candidate.indexStrideBytes, overflow)) {
            return fail(E_EXPORT_LAYOUT, "gather indices require a constant traversal stride", error);
        }
        inner = checkedMul(inner, extent, overflow);
    }
    candidate.usefulBytes = checkedMul(candidate.rows, candidate.rowBytes, overflow);
    const uint64_t tableSpan = candidate.tableRows && candidate.rowBytes ?
        checkedMulAdd(candidate.tableRows - 1, candidate.sourceStrideBytes,
                      candidate.rowBytes, overflow) : 0;
    const uint64_t destinationSpan = candidate.rows && candidate.rowBytes ?
        checkedMulAdd(candidate.rows - 1, candidate.destinationStrideBytes,
                      candidate.rowBytes, overflow) : 0;
    candidate.physicalStorageBytes = candidate.rows ? std::max(tableSpan, destinationSpan) : 0;
    if (overflow)
        return fail(E_ABI_OVERFLOW, "gather geometry overflows", error);
    out = candidate;
    return true;
}

bool verifyStagedGatherResult(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const mesh_abi::semantic_abi::KernelOp &operation, MeshLoadError &error)
{
    using namespace mesh_abi::semantic_abi;
    using namespace mesh_diagnostics;
    if (operation.reads.count != 1 || operation.writes.count != 1)
        return fail(E_DMA_RANGE, "staged gather result requires one read and write", error);
    const auto *input = geometry.access(operation.op_id, GeometryAccessRole::Read, 0);
    const auto *output = geometry.access(operation.op_id, GeometryAccessRole::Write, 0);
    const auto computation = context.computations().find(operation.computation_id);
    if (!input || !input->read() || !output || computation == context.computations().end())
        return fail(E_ABI_BOUNDS, "staged gather result identity is invalid", error);
    if (computation->second->opcode != OpCode::EMBEDDING_LOOKUP)
        return fail(E_EXPORT_UNSUPPORTED_OP, "staged gather requires an embedding computation", error);
    const GeometryAccessFact *produced = nullptr;
    for (const auto &[id, op] : context.operations())
        for (size_t ordinal = 0; ordinal < op->writes.count; ++ordinal) {
            const auto *write = geometry.access(id, GeometryAccessRole::Write, ordinal);
            if (write && write->write() &&
                write->write()->new_state_id == input->read()->state_id) {
                if (produced)
                    return fail(E_TENSOR_NOT_RESIDENT, "staged gather producer is ambiguous", error);
                produced = write;
            }
        }
    if (!produced)
        return fail(E_TENSOR_NOT_RESIDENT, "staged gather producer is absent", error);
    GatherGeometry gather;
    if (!GatherGeometry::derive(program, geometry, produced->operation(), gather, error))
        return false;
    if (produced->view().view_id != input->view().view_id ||
        produced->localOrigin() != input->localOrigin() ||
        produced->shape() != input->shape() || produced->steps() != input->steps())
        return fail(E_DMA_RANGE, "staged gather must consume every occurrence in order", error);
    const auto *index = geometry.access(produced->operation().op_id, GeometryAccessRole::Read, 0);
    const auto *table = geometry.access(produced->operation().op_id, GeometryAccessRole::Read, 1);
    std::vector<uint64_t> operands;
    if (!values(program, computation->second->operand_tensor_ids, operands) ||
        operands != std::vector<uint64_t>({table->tensor().tensor_id, index->tensor().tensor_id}) ||
        output->tensor().tensor_id != computation->second->result_tensor_id)
        return fail(E_ABI_CHECKSUM, "gather producer differs from embedding computation", error);
    auto expectedShape = index->shape();
    expectedShape.push_back(table->shape()[1]);
    if (output->shape() != expectedShape)
        return fail(E_EXPORT_LAYOUT, "staged gather output shape differs from its indices", error);
    return true;
}

}

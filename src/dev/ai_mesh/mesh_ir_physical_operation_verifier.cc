#include "dev/ai_mesh/mesh_ir_physical_operation_verifier.hh"

#include "dev/ai_mesh/mesh_ir_logical_computation_verifier.hh"

#include <algorithm>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"

namespace gem5
{
namespace ai_mesh
{
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
row(const SemanticRef &reference, const std::vector<Record> &records)
{
    using Traits = SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type || reference.row_id == 0 ||
        reference.row_id > records.size())
        return nullptr;
    return &records[reference.row_id - 1];
}

template <typename Record>
const Record *
operationAttributeRow(
    const SemanticRef &reference, const std::vector<Record> &records,
    MeshLoadError &error)
{
    using Traits = SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type) {
        fail(E_EXPORT_UNSUPPORTED_OP, "compute attribute family is invalid",
             error);
        return nullptr;
    }
    const Record *value = row(reference, records);
    if (!value)
        fail(E_ABI_BOUNDS, "physical operation attributes are invalid", error);
    return value;
}

bool
spanValue(const DecodedProgram &program, const ListSpan &span, size_t offset,
          uint64_t &value)
{
    if (offset >= span.count || span.begin > program.semantic_u64_values.size() ||
        span.count > program.semantic_u64_values.size() - span.begin)
        return false;
    value = program.semantic_u64_values[size_t(span.begin) + offset];
    return true;
}

bool
spanValues(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<uint64_t> &values)
{
    if (span.begin > program.semantic_u64_values.size() ||
        span.count > program.semantic_u64_values.size() - span.begin)
        return false;
    const auto begin = program.semantic_u64_values.begin() + span.begin;
    values.assign(begin, begin + span.count);
    return true;
}

bool
shapeProduct(
    const std::vector<uint64_t> &shape, size_t count, uint64_t &out,
    MeshLoadError &error)
{
    out = 1;
    for (size_t index = 0; index < count; ++index) {
        if (shape[index] != 0 && out > UINT64_MAX / shape[index])
            return fail(E_ABI_OVERFLOW, "matrix batch size overflows", error);
        out *= shape[index];
    }
    return true;
}

bool
isPhysicalCompute(KernelOpcode opcode)
{
    switch (opcode) {
      case KernelOpcode::GEMM:
      case KernelOpcode::BMM:
      case KernelOpcode::MATRIX_EPILOGUE:
      case KernelOpcode::VECTOR:
      case KernelOpcode::DATA_MOVEMENT:
      case KernelOpcode::REDUCE:
      case KernelOpcode::SOFTMAX:
      case KernelOpcode::NORM:
      case KernelOpcode::COLLECTIVE:
      case KernelOpcode::LOCAL_REDUCE:
        return true;
      default:
        return false;
    }
}

bool
needsDataContract(KernelOpcode opcode)
{
    return opcode != KernelOpcode::COLLECTIVE &&
        opcode != KernelOpcode::LOCAL_REDUCE;
}

bool
operationSemanticAttributes(
    const DecodedProgram &program, const KernelOp &operation, OpCode &opcode,
    SemanticRef &attrs, MeshLoadError &error)
{
    if (operation.opcode == KernelOpcode::GEMM ||
        operation.opcode == KernelOpcode::BMM) {
        const GemmKernelAttrs *value = operationAttributeRow(
            operation.attrs, program.semantic_tables.gemm_kernel_attrs_rows,
            error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::MATRIX_EPILOGUE) {
        const MatrixEpilogueKernelAttrs *value = operationAttributeRow(
            operation.attrs,
            program.semantic_tables.matrix_epilogue_kernel_attrs_rows, error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::VECTOR) {
        const VectorKernelAttrs *value = operationAttributeRow(
            operation.attrs, program.semantic_tables.vector_kernel_attrs_rows,
            error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::DATA_MOVEMENT) {
        const MovementKernelAttrs *value = operationAttributeRow(
            operation.attrs,
            program.semantic_tables.movement_kernel_attrs_rows, error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::REDUCE) {
        const ReductionKernelAttrs *value = operationAttributeRow(
            operation.attrs,
            program.semantic_tables.reduction_kernel_attrs_rows, error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::SOFTMAX) {
        const SoftmaxKernelAttrs *value = operationAttributeRow(
            operation.attrs,
            program.semantic_tables.softmax_kernel_attrs_rows, error);
        if (!value)
            return false;
        opcode = OpCode::SOFTMAX;
        attrs = value->semantic_attrs;
        return true;
    }
    if (operation.opcode == KernelOpcode::NORM) {
        const NormKernelAttrs *value = operationAttributeRow(
            operation.attrs, program.semantic_tables.norm_kernel_attrs_rows,
            error);
        if (!value)
            return false;
        opcode = value->graph_opcode;
        attrs = value->semantic_attrs;
        return true;
    }
    return fail(E_EXPORT_UNSUPPORTED_OP, "compute attribute family is invalid",
                error);
}

bool
logicalAttributesMatch(
    const DecodedProgram &program, OpCode opcode, const SemanticRef &attrs,
    const KernelComputation &computation, MeshLoadError &error)
{
    if (opcode != computation.opcode ||
        attrs.section_type != computation.attrs.section_type)
        return fail(E_ABI_CHECKSUM,
                    "physical operation differs from its logical computation",
                    error);
    std::ostringstream operationValue;
    std::ostringstream computationValue;
    if (!mesh_binary_detail::writeSemanticValueCanonical(
            program, attrs, operationValue, error) ||
        !mesh_binary_detail::writeSemanticValueCanonical(
            program, computation.attrs, computationValue, error))
        return false;
    return operationValue.str() == computationValue.str() ||
        fail(E_ABI_CHECKSUM,
             "physical operation differs from its logical computation", error);
}

bool
checkResultAssignment(
    const KernelOp &operation, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry, MeshLoadError &error)
{
    const auto result = context.logicalShards().find(operation.result_shard_id);
    if (result == context.logicalShards().end())
        return fail(E_ABI_BOUNDS,
                    "physical compute operation has no valid result shard", error);
    if (result->second->owner_core != operation.owner_core)
        return fail(E_TENSOR_NOT_RESIDENT,
                    "physical result shard owner differs from operation owner",
                    error);
    for (size_t ordinal = 0;
         ordinal < geometry.accessCount(operation.op_id, GeometryAccessRole::Write);
         ++ordinal) {
        const GeometryAccessFact *write = geometry.access(
            operation.op_id, GeometryAccessRole::Write, ordinal);
        if (!write || write->logicalShard().shard_id != operation.result_shard_id)
            return fail(E_EXPORT_LAYOUT,
                        "physical operation write differs from its assigned result shard",
                        error);
    }
    return true;
}

bool
checkPhysicalTensorAssociation(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const KernelOp &operation, const KernelComputation &computation,
    const VerifiedProgramGeometry &geometry, MeshLoadError &error)
{
    const size_t reads = geometry.accessCount(operation.op_id,
                                              GeometryAccessRole::Read);
    const size_t writes = geometry.accessCount(operation.op_id,
                                               GeometryAccessRole::Write);
    if (reads == 0 && writes == 0) {
        const auto shard = context.logicalShards().find(operation.result_shard_id);
        if (shard == context.logicalShards().end())
            return fail(E_ABI_BOUNDS,
                        "empty physical operation has no result shard", error);
        const bool matrix = operation.opcode == KernelOpcode::GEMM ||
            operation.opcode == KernelOpcode::BMM;
        const GemmKernelAttrs *attrs = matrix ? row(
            operation.attrs,
            program.semantic_tables.gemm_kernel_attrs_rows) : nullptr;
        if (matrix && !attrs)
            return fail(E_ABI_BOUNDS, "matrix attributes are invalid", error);
        if (attrs && attrs->phase != MatrixPhase::DIRECT) {
            const auto tensor = context.tensors().find(shard->second->tensor_id);
            if (tensor == context.tensors().end() ||
                tensor->second->producer_computation_id !=
                    computation.computation_id ||
                (tensor->second->synthesized_purpose !=
                     SynthesizedTensorPurpose::ACCUMULATION &&
                 tensor->second->synthesized_purpose !=
                     SynthesizedTensorPurpose::PARTIAL_SUM))
                return fail(E_ABI_BOUNDS,
                            "matrix accumulator differs from its computation",
                            error);
            return verifyMatrixAccumulatorTensorContract(
                program, context, computation, *tensor->second, error);
        }
        if (shard->second->tensor_id != computation.result_tensor_id)
            return fail(E_ABI_CHECKSUM,
                        "empty physical result differs from its logical computation",
                        error);
        return true;
    }
    if (writes == 0)
        return fail(E_ABI_CHECKSUM,
                    "physical tensor accesses differ from logical computation",
                    error);
    std::vector<uint64_t> operands;
    operands.reserve(reads);
    for (size_t ordinal = 0; ordinal < reads; ++ordinal) {
        const GeometryAccessFact *read = geometry.access(
            operation.op_id, GeometryAccessRole::Read, ordinal);
        if (!read)
            return fail(E_ABI_BOUNDS,
                        "physical operation read geometry is invalid", error);
        operands.push_back(read->tensor().tensor_id);
    }
    const GeometryAccessFact *result = geometry.access(
        operation.op_id, GeometryAccessRole::Write, 0);
    if (!result)
        return fail(E_ABI_BOUNDS,
                    "physical operation write geometry is invalid", error);
    const uint64_t resultId = result->tensor().tensor_id;
    if (operation.opcode == KernelOpcode::MATRIX_EPILOGUE) {
        const size_t expectedReads = computation.opcode == OpCode::LINEAR_BIAS ?
            2 : 1;
        const auto accumulator = operands.empty() ? context.tensors().end() :
            context.tensors().find(operands.front());
        uint64_t bias = 0;
        const bool hasBias = computation.opcode != OpCode::LINEAR_BIAS ||
            (computation.operand_tensor_ids.count == 3 && operands.size() == 2 &&
             spanValue(program, computation.operand_tensor_ids, 2, bias) &&
             operands[1] == bias);
        if (operands.size() != expectedReads ||
            resultId != computation.result_tensor_id ||
            accumulator == context.tensors().end() ||
            accumulator->second->producer_computation_id !=
                computation.computation_id ||
            (accumulator->second->synthesized_purpose !=
                 SynthesizedTensorPurpose::ACCUMULATION &&
             accumulator->second->synthesized_purpose !=
                 SynthesizedTensorPurpose::PARTIAL_SUM) ||
            !hasBias)
            return fail(E_ABI_CHECKSUM,
                        "matrix epilogue accesses differ from its logical computation",
                        error);
        return true;
    }
    const bool matrix = operation.opcode == KernelOpcode::GEMM ||
        operation.opcode == KernelOpcode::BMM;
    const GemmKernelAttrs *matrixAttrs = matrix ? row(
        operation.attrs, program.semantic_tables.gemm_kernel_attrs_rows) : nullptr;
    if (matrix && !matrixAttrs)
        return fail(E_ABI_BOUNDS, "matrix attributes are invalid", error);
    if (matrix && matrixAttrs->phase != MatrixPhase::DIRECT) {
        const bool continuation =
            matrixAttrs->phase == MatrixPhase::ACCUMULATE_CONTINUE ||
            matrixAttrs->phase == MatrixPhase::ACCUMULATE_FINAL;
        uint64_t first = 0;
        uint64_t second = 0;
        const auto resultTensor = context.tensors().find(resultId);
        if (computation.operand_tensor_ids.count < 2 ||
            !spanValue(program, computation.operand_tensor_ids, 0, first) ||
            !spanValue(program, computation.operand_tensor_ids, 1, second) ||
            operands.size() != 2 + size_t(continuation) ||
            operands[0] != first || operands[1] != second ||
            (continuation && operands[2] != resultId) ||
            resultTensor == context.tensors().end() ||
            resultTensor->second->producer_computation_id !=
                computation.computation_id ||
            (resultTensor->second->synthesized_purpose !=
                 SynthesizedTensorPurpose::ACCUMULATION &&
             resultTensor->second->synthesized_purpose !=
                 SynthesizedTensorPurpose::PARTIAL_SUM))
            return fail(E_ABI_BOUNDS,
                        "matrix accumulator differs from its computation", error);
        return verifyMatrixAccumulatorTensorContract(
            program, context, computation, *resultTensor->second, error);
    }
    if (computation.operand_tensor_ids.count != operands.size())
        return fail(E_ABI_CHECKSUM,
                    "physical tensor accesses differ from logical computation",
                    error);
    for (size_t ordinal = 0; ordinal < operands.size(); ++ordinal) {
        uint64_t expected = 0;
        if (!spanValue(program, computation.operand_tensor_ids, ordinal,
                       expected))
            return fail(E_ABI_BOUNDS,
                        "computation operand tensor list is invalid", error);
        if (operands[ordinal] != expected)
            return fail(E_ABI_CHECKSUM,
                        "physical tensor accesses differ from logical computation",
                        error);
    }
    if (resultId != computation.result_tensor_id)
        return fail(E_ABI_CHECKSUM,
                    "physical tensor accesses differ from logical computation",
                    error);
    return true;
}

}

class PhysicalOperationBuilder
{
  public:
    PhysicalOperationBuilder(
        const DecodedProgram &program, const ProgramSemanticContext &context,
        const VerifiedProgramGeometry &geometry,
        VerifiedMatrixContractionFacts &facts)
        : program_(program), context_(context), geometry_(geometry), facts_(facts)
    {}

    bool run(MeshLoadError &error)
    {
        if (!checkOperationSemantics(error) ||
            !facts_.bind(program_, context_, geometry_))
            return false;
        return true;
    }

  private:
    bool matrixContractionBounds(
        const mesh_abi::semantic_abi::KernelOp &, MatrixContractionFact &,
        MeshLoadError &);
    bool checkOperationSemantics(MeshLoadError &);

    const DecodedProgram &program_;
    const ProgramSemanticContext &context_;
    const VerifiedProgramGeometry &geometry_;
    VerifiedMatrixContractionFacts &facts_;
};

bool
PhysicalOperationBuilder::matrixContractionBounds(
    const KernelOp &operation, MatrixContractionFact &fact,
    MeshLoadError &error)
{
    fact.bounds_.reset();
    const DecodedProgram &program = program_;
    const VerifiedProgramGeometry &geometry = geometry_;
    const GemmKernelAttrs *attrs = row(
        operation.attrs, program.semantic_tables.gemm_kernel_attrs_rows);
    if (!attrs)
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction attributes are invalid", error);
    if (geometry.accessCount(operation.op_id, GeometryAccessRole::Read) == 0 &&
        geometry.accessCount(operation.op_id, GeometryAccessRole::Write) == 0)
        return true;
    const MatmulAttrs *matmul = row(
        attrs->semantic_attrs, program.semantic_tables.matmul_attrs_rows);
    const KernelTile *tile = row(
        attrs->tile, program.semantic_tables.kernel_tile_rows);
    const GeometryAccessFact *lhs = geometry.access(
        operation.op_id, GeometryAccessRole::Read, 0);
    const GeometryAccessFact *rhs = geometry.access(
        operation.op_id, GeometryAccessRole::Read, 1);
    const GeometryAccessFact *result = geometry.access(
        operation.op_id, GeometryAccessRole::Write, 0);
    if (!matmul || !tile || !lhs || !rhs || lhs->shape().size() < 2 ||
        rhs->shape().size() < 2 || !result || result->shape().size() < 2)
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction operands are invalid", error);
    const std::vector<uint64_t> &lhsShape = lhs->shape();
    const std::vector<uint64_t> &rhsShape = rhs->shape();
    const std::vector<uint64_t> &resultShape = result->shape();
    const uint64_t lhsM = matmul->lhs_transpose ? lhsShape.back() :
                                                 lhsShape[lhsShape.size() - 2];
    const uint64_t lhsK = matmul->lhs_transpose ?
        lhsShape[lhsShape.size() - 2] : lhsShape.back();
    const uint64_t rhsK = matmul->rhs_transpose ? rhsShape.back() :
                                                  rhsShape[rhsShape.size() - 2];
    const uint64_t rhsN = matmul->rhs_transpose ?
        rhsShape[rhsShape.size() - 2] : rhsShape.back();
    if (attrs->graph_opcode == OpCode::BMM) {
        if (lhsShape.size() != 3 || rhsShape.size() != 3 ||
            resultShape.size() != 3 || lhsShape[0] != rhsShape[0] ||
            lhsShape[0] != resultShape[0])
            return fail(E_EXPORT_LAYOUT,
                        "batched matrix operands have inconsistent batch shapes",
                        error);
    } else {
        std::vector<uint64_t> batch;
        const size_t leading = std::max(lhsShape.size(), rhsShape.size()) - 2;
        batch.reserve(leading);
        for (size_t offset = 0; offset < leading; ++offset) {
            const uint64_t left = offset < lhsShape.size() - 2 ?
                lhsShape[lhsShape.size() - 3 - offset] : 1;
            const uint64_t right = offset < rhsShape.size() - 2 ?
                rhsShape[rhsShape.size() - 3 - offset] : 1;
            if (left == right)
                batch.push_back(left);
            else if (left == 1)
                batch.push_back(right);
            else if (right == 1)
                batch.push_back(left);
            else
                return fail(E_EXPORT_LAYOUT,
                            "matrix physical batch regions do not broadcast",
                            error);
        }
        std::reverse(batch.begin(), batch.end());
        if (resultShape.size() - 2 != batch.size() ||
            !std::equal(batch.begin(), batch.end(), resultShape.begin()))
            return fail(E_EXPORT_LAYOUT,
                        "matrix physical batch region differs from its result",
                        error);
    }
    if (attrs->phase == MatrixPhase::DIRECT &&
        attrs->graph_opcode == OpCode::LINEAR_BIAS) {
        const GeometryAccessFact *bias = geometry.access(
            operation.op_id, GeometryAccessRole::Read, 2);
        if (!bias || bias->shape() != std::vector<uint64_t>{rhsN})
            return fail(E_EXPORT_LAYOUT,
                        "linear bias region does not match output columns",
                        error);
    }
    uint64_t batch = 0;
    if (!shapeProduct(resultShape, resultShape.size() - 2, batch, error))
        return false;
    if (lhsK != rhsK || resultShape[resultShape.size() - 2] != lhsM ||
        resultShape.back() != rhsN || tile->valid_batch != batch ||
        tile->valid_m != lhsM || tile->valid_n != rhsN ||
        tile->valid_k != lhsK)
        return fail(E_EXPORT_LAYOUT,
                    "matrix tile does not match operand regions", error);
    const size_t lhsAxis = lhs->shape().size() -
        (matmul->lhs_transpose ? 2 : 1);
    const size_t rhsAxis = rhs->shape().size() -
        (matmul->rhs_transpose ? 1 : 2);
    const TensorShard &lhsShard = lhs->logicalShard();
    const TensorShard &rhsShard = rhs->logicalShard();
    uint64_t lhsBegin = 0;
    uint64_t lhsExtent = 0;
    uint64_t rhsBegin = 0;
    uint64_t rhsExtent = 0;
    if (lhsAxis >= lhs->globalOrigin().size() ||
        rhsAxis >= rhs->globalOrigin().size() ||
        !spanValue(program, lhsShard.global_origin, lhsAxis, lhsBegin) ||
        !spanValue(program, lhsShard.valid_shape, lhsAxis, lhsExtent) ||
        !spanValue(program, rhsShard.global_origin, rhsAxis, rhsBegin) ||
        !spanValue(program, rhsShard.valid_shape, rhsAxis, rhsExtent) ||
        lhsBegin > std::numeric_limits<uint64_t>::max() - lhsExtent ||
        rhsBegin > std::numeric_limits<uint64_t>::max() - rhsExtent ||
        tile->k_origin > std::numeric_limits<uint64_t>::max() - tile->valid_k)
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction coordinates are invalid", error);
    if (lhs->globalOrigin()[lhsAxis] != rhs->globalOrigin()[rhsAxis] ||
        tile->k_origin != lhs->globalOrigin()[lhsAxis] ||
        lhsBegin != rhsBegin || lhsBegin + lhsExtent != rhsBegin + rhsExtent)
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction coordinates differ from physical operand regions",
                    error);
    const uint64_t domainEnd = lhsBegin + lhsExtent;
    const uint64_t intervalEnd = tile->k_origin + tile->valid_k;
    if (attrs->phase == MatrixPhase::DIRECT &&
        (tile->k_origin != lhsBegin || intervalEnd != domainEnd))
        return fail(E_EXPORT_LAYOUT,
                    "direct matrix kernel does not cover its contraction domain",
                    error);
    fact.bounds_.emplace();
    fact.bounds_->domainBegin_ = lhsBegin;
    fact.bounds_->domainEnd_ = domainEnd;
    fact.bounds_->intervalBegin_ = tile->k_origin;
    fact.bounds_->intervalEnd_ = intervalEnd;
    return true;
}

bool
matrixEpilogueRegions(
    const DecodedProgram &program, const KernelOp &operation,
    const VerifiedProgramGeometry &geometry, MeshLoadError &error)
{
    if (geometry.accessCount(operation.op_id, GeometryAccessRole::Read) == 0 &&
        geometry.accessCount(operation.op_id, GeometryAccessRole::Write) == 0)
        return true;
    const MatrixEpilogueKernelAttrs *attrs = row(
        operation.attrs,
        program.semantic_tables.matrix_epilogue_kernel_attrs_rows);
    const GeometryAccessFact *input = geometry.access(
        operation.op_id, GeometryAccessRole::Read, 0);
    const GeometryAccessFact *output = geometry.access(
        operation.op_id, GeometryAccessRole::Write, 0);
    if (!attrs || !input || !output || input->shape() != output->shape() ||
        output->shape().size() < 2)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "matrix epilogue has invalid physical operands", error);
    if (attrs->graph_opcode == OpCode::LINEAR_BIAS) {
        const GeometryAccessFact *bias = geometry.access(
            operation.op_id, GeometryAccessRole::Read, 1);
        if (!bias || bias->shape() !=
                std::vector<uint64_t>{output->shape().back()})
            return fail(E_EXPORT_LAYOUT,
                        "matrix epilogue bias region does not match output columns",
                        error);
    }
    const KernelTile *tile = row(
        attrs->tile, program.semantic_tables.kernel_tile_rows);
    uint64_t batch = 0;
    if (!tile || !shapeProduct(
            output->shape(), output->shape().size() - 2, batch, error))
        return tile ? false : fail(E_ABI_BOUNDS,
                                   "matrix epilogue tile is invalid", error);
    return (tile->valid_batch == batch &&
            tile->valid_m == output->shape()[output->shape().size() - 2] &&
            tile->valid_n == output->shape().back()) ||
        fail(E_EXPORT_LAYOUT,
             "matrix epilogue tile differs from its result region", error);
}


bool
PhysicalOperationBuilder::checkOperationSemantics(MeshLoadError &error)
{
    const DecodedProgram &program = program_;
    const ProgramSemanticContext &context = context_;
    const VerifiedProgramGeometry &geometry = geometry_;
    struct ResultGroup
    {
        uint64_t placementId = 0;
        std::set<uint64_t> owners;
    };
    std::map<std::pair<uint64_t, uint64_t>, ResultGroup> resultGroups;
    for (const auto &entry : context.operations()) {
        const KernelOp &operation = *entry.second;
        if (!isPhysicalCompute(operation.opcode)) {
            if (operation.result_shard_id != 0)
                return fail(E_ABI_BOUNDS,
                            "noncompute operation has a result shard", error);
            if ((operation.opcode == KernelOpcode::RECV_WAIT ||
                 operation.opcode == KernelOpcode::BARRIER) &&
                (geometry.accessCount(operation.op_id, GeometryAccessRole::Read) != 0 ||
                 geometry.accessCount(operation.op_id, GeometryAccessRole::Write) != 0))
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "synchronization operation carries data operands",
                            error);
            continue;
        }
        OpCode semanticOpcode{};
        SemanticRef semanticAttrs;
        if (needsDataContract(operation.opcode) &&
            !operationSemanticAttributes(
                program, operation, semanticOpcode, semanticAttrs, error))
            return false;
        if (!checkResultAssignment(operation, context, geometry, error))
            return false;
        const auto computation = context.computations().find(
            operation.computation_id);
        if (computation == context.computations().end())
            return fail(E_ABI_BOUNDS,
                        "compute operation has no logical computation", error);
        const auto result = context.logicalShards().find(
            operation.result_shard_id);
        if (result == context.logicalShards().end())
            return fail(E_ABI_BOUNDS,
                        "physical compute operation has no valid result shard",
                        error);
        if (operation.opcode == KernelOpcode::COLLECTIVE ||
            operation.opcode == KernelOpcode::LOCAL_REDUCE) {
            uint64_t partialId = 0;
            if (operation.opcode == KernelOpcode::COLLECTIVE) {
                const CollectiveAttrs *attrs = row(
                    operation.attrs,
                    program.semantic_tables.collective_attrs_rows);
                if (!attrs)
                    return fail(E_ABI_BOUNDS,
                                "collective attributes are invalid", error);
                partialId = attrs->partial_sum_id;
            } else {
                const LocalReduceAttrs *attrs = row(
                    operation.attrs,
                    program.semantic_tables.local_reduce_attrs_rows);
                if (!attrs)
                    return fail(E_ABI_BOUNDS,
                                "local reduction attributes are invalid", error);
                partialId = attrs->partial_sum_id;
            }
            const auto partial = context.partialSums().find(partialId);
            if (partial == context.partialSums().end())
                return fail(E_ABI_BOUNDS,
                            "partial operation references an absent definition",
                            error);
            if (result->second->partial_sum_id != partialId ||
                result->second->tensor_id !=
                    partial->second->accumulator_tensor_id ||
                result->second->placement_id != partial->second->placement_id ||
                partial->second->computation_id != operation.computation_id)
                return fail(E_PLACEMENT_INFEASIBLE,
                            "partial result assignment differs from its definition",
                            error);
        }
        const std::pair<uint64_t, uint64_t> resultKey{
            operation.computation_id, result->second->tensor_id};
        auto inserted = resultGroups.emplace(
            resultKey, ResultGroup{result->second->placement_id,
                                    {operation.owner_core}});
        if (!inserted.second) {
            if (inserted.first->second.placementId !=
                result->second->placement_id)
                return fail(E_PLACEMENT_INFEASIBLE,
                            "one physical computation result uses multiple placement groups",
                            error);
            inserted.first->second.owners.insert(operation.owner_core);
        }
        const size_t reads = geometry.accessCount(
            operation.op_id, GeometryAccessRole::Read);
        const size_t writes = geometry.accessCount(
            operation.op_id, GeometryAccessRole::Write);
        if (reads != 0 || writes != 0) {
            bool missingLayout = false;
            for (const GeometryAccessRole role : {
                     GeometryAccessRole::Read, GeometryAccessRole::Write}) {
                const size_t count = role == GeometryAccessRole::Read ?
                    reads : writes;
                for (size_t ordinal = 0; ordinal < count; ++ordinal) {
                    const GeometryAccessFact *access = geometry.access(
                        operation.op_id, role, ordinal);
                    if (!access)
                        return fail(E_ABI_BOUNDS,
                                    "physical operation geometry is invalid",
                                    error);
                    if (access->object().memory_space !=
                            semantic_abi::MemorySpace::CORE_SRAM ||
                        access->object().owner_core != operation.owner_core)
                        return fail(E_TENSOR_NOT_RESIDENT,
                                    "compute operation does not use owner-local SRAM",
                                    error);
                    if ((access->view().presence_mask &
                         kBufferViewLayoutField.optional_presence_mask) == 0)
                        missingLayout = true;
                }
            }
            if (missingLayout)
                return fail(E_EXPORT_LAYOUT,
                            "compute operation uses a view without a consumer layout",
                            error);
        }
        if (!needsDataContract(operation.opcode))
            continue;
        if ((operation.opcode == KernelOpcode::GEMM ||
             operation.opcode == KernelOpcode::BMM) &&
            (reads != 0 || writes != 0)) {
            const GemmKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.gemm_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS, "matrix attributes are invalid",
                            error);
            const size_t expectedReads = attrs->phase == MatrixPhase::DIRECT ?
                (attrs->graph_opcode == OpCode::LINEAR_BIAS ? 3 : 2) :
                (attrs->phase == MatrixPhase::ACCUMULATE_CONTINUE ||
                 attrs->phase == MatrixPhase::ACCUMULATE_FINAL ? 3 : 2);
            if (reads != expectedReads || writes != 1)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "matrix kernel has invalid arity", error);
        }
        if (operation.opcode == KernelOpcode::MATRIX_EPILOGUE &&
            (reads != 0 || writes != 0)) {
            const MatrixEpilogueKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.matrix_epilogue_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS,
                            "matrix epilogue attributes are invalid", error);
            const size_t expectedReads =
                attrs->graph_opcode == OpCode::LINEAR_BIAS ? 2 : 1;
            if (reads != expectedReads || writes != 1)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "matrix epilogue has invalid physical operands",
                            error);
        }
        if (operation.opcode == KernelOpcode::VECTOR &&
            (reads != 0 || writes != 0)) {
            const VectorKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.vector_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS, "vector attributes are invalid",
                            error);
            const bool arithmetic = attrs->graph_opcode == OpCode::ADD ||
                attrs->graph_opcode == OpCode::SUB ||
                attrs->graph_opcode == OpCode::MUL ||
                attrs->graph_opcode == OpCode::DIV;
            const bool embedding =
                attrs->graph_opcode == OpCode::EMBEDDING_LOOKUP;
            const bool supported = arithmetic || embedding ||
                attrs->graph_opcode == OpCode::RELU ||
                attrs->graph_opcode == OpCode::GELU ||
                attrs->graph_opcode == OpCode::SILU ||
                attrs->graph_opcode == OpCode::EXP ||
                attrs->graph_opcode == OpCode::RSQRT;
            if (!supported)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "vector kernel has invalid semantic opcode or arity",
                            error);
            const bool elementwise = !embedding;
            const ElementwiseAttrs *elementwiseAttrs = elementwise ? row(
                attrs->semantic_attrs,
                program.semantic_tables.elementwise_attrs_rows) : nullptr;
            if (elementwise && !elementwiseAttrs)
                return fail(E_ABI_BOUNDS,
                            "vector semantic attributes are invalid", error);
            const size_t expectedReads = arithmetic ?
                ((elementwiseAttrs->presence_mask &
                  kElementwiseAttrsScalarField.optional_presence_mask) != 0 ?
                     1 : 2) :
                (embedding ? 2 : 1);
            if (reads != expectedReads || writes != 1)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "vector kernel has invalid semantic opcode or arity",
                            error);
            if (elementwise) {
                const GeometryAccessFact *output = geometry.access(
                    operation.op_id, GeometryAccessRole::Write, 0);
                if (!output)
                    return fail(E_ABI_BOUNDS,
                                "vector output geometry is invalid", error);
                for (size_t ordinal = 0; ordinal < reads; ++ordinal) {
                    const GeometryAccessFact *input = geometry.access(
                        operation.op_id, GeometryAccessRole::Read, ordinal);
                    if (!input)
                        return fail(E_ABI_BOUNDS,
                                    "vector input geometry is invalid", error);
                    if (input->shape() != output->shape())
                        return fail(E_EXPORT_LAYOUT,
                                    "elementwise operand regions do not match output shape",
                                    error);
                }
            }
        }
        if (operation.opcode == KernelOpcode::DATA_MOVEMENT &&
            (reads != 0 || writes != 0)) {
            const MovementKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.movement_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS, "movement attributes are invalid",
                            error);
            size_t expectedReads = attrs->graph_opcode == OpCode::GATHER_ROWS ?
                2 : 1;
            if (attrs->graph_opcode == OpCode::CONCAT)
                expectedReads = reads;
            if (reads == 0 || reads != expectedReads || writes != 1)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "movement kernel has invalid arity", error);
        }
        if ((operation.opcode == KernelOpcode::REDUCE ||
             operation.opcode == KernelOpcode::SOFTMAX) &&
            (reads != 0 || writes != 0) &&
            (reads != 1 || writes != 1))
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "reduction kernel has invalid arity", error);
        if (operation.opcode == KernelOpcode::NORM &&
            (reads != 0 || writes != 0)) {
            const NormKernelAttrs *attrs = row(
                operation.attrs, program.semantic_tables.norm_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS, "normalization attributes are invalid",
                            error);
            const NormAttrs *semantic = row(
                attrs->semantic_attrs,
                program.semantic_tables.norm_attrs_rows);
            if (!semantic)
                return fail(E_ABI_BOUNDS,
                            "normalization semantic attributes are invalid",
                            error);
            const size_t expectedReads = 1 + semantic->has_weight +
                semantic->has_bias;
            if (reads != expectedReads || writes != 1)
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "normalization kernel has invalid arity", error);
        }
        if (!logicalAttributesMatch(
                program, semanticOpcode, semanticAttrs, *computation->second,
                error) ||
            !checkPhysicalTensorAssociation(program, context, operation,
                                            *computation->second, geometry,
                                            error))
            return false;
        if (operation.opcode == KernelOpcode::MATRIX_EPILOGUE &&
            !matrixEpilogueRegions(program, operation, geometry, error))
            return false;
        if (operation.opcode == KernelOpcode::GEMM ||
            operation.opcode == KernelOpcode::BMM) {
            auto inserted = facts_.contractions_.emplace(
                operation.op_id, MatrixContractionFact{});
            if (!inserted.second || !matrixContractionBounds(
                    operation, inserted.first->second, error))
                return false;
        }
    }
    for (const auto &[key, group] : resultGroups) {
        const auto placement = context.placements().find(group.placementId);
        std::vector<uint64_t> cores;
        if (placement == context.placements().end() ||
            !spanValues(program, placement->second->core_ids, cores))
            return fail(E_ABI_BOUNDS,
                        "physical result placement is invalid", error);
        const std::set<uint64_t> expected(cores.begin(), cores.end());
        if (group.owners != expected)
            return fail(E_PLACEMENT_INFEASIBLE,
                        "physical computation result does not cover every assigned owner",
                        error);
    }
    return true;
}
uint64_t
MatrixContractionBounds::domainBegin() const
{
    return domainBegin_;
}

uint64_t
MatrixContractionBounds::domainEnd() const
{
    return domainEnd_;
}

uint64_t
MatrixContractionBounds::intervalBegin() const
{
    return intervalBegin_;
}

uint64_t
MatrixContractionBounds::intervalEnd() const
{
    return intervalEnd_;
}

const MatrixContractionBounds *
MatrixContractionFact::bounds() const
{
    return bounds_ ? &*bounds_ : nullptr;
}

VerifiedMatrixContractionFacts::~VerifiedMatrixContractionFacts() = default;

VerifiedMatrixContractionFacts::VerifiedMatrixContractionFacts(
    VerifiedMatrixContractionFacts &&other) noexcept
    : program_(other.program_), context_(other.context_),
      geometryIdentity_(std::move(other.geometryIdentity_)),
      contractions_(std::move(other.contractions_))
{
    other.program_ = nullptr;
    other.context_ = nullptr;
}

VerifiedMatrixContractionFacts &
VerifiedMatrixContractionFacts::operator=(
    VerifiedMatrixContractionFacts &&other) noexcept
{
    if (this != &other) {
        program_ = other.program_;
        context_ = other.context_;
        geometryIdentity_ = std::move(other.geometryIdentity_);
        contractions_ = std::move(other.contractions_);
        other.program_ = nullptr;
        other.context_ = nullptr;
    }
    return *this;
}

bool
VerifiedMatrixContractionFacts::bind(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry)
{
    if (!geometry.matches(program, context))
        return false;
    program_ = &program;
    context_ = &context;
    geometryIdentity_ = geometry.invocationIdentity();
    return geometryIdentity_ != nullptr;
}

bool
VerifiedMatrixContractionFacts::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry) const
{
    return program_ == &program && context_ == &context && geometryIdentity_ &&
        geometry.matches(program, context) &&
        geometry.matchesInvocation(geometryIdentity_);
}

const MatrixContractionFact *
VerifiedMatrixContractionFacts::contraction(uint64_t operationId) const
{
    if (!program_ || !context_ || !geometryIdentity_)
        return nullptr;
    const auto item = contractions_.find(operationId);
    return item == contractions_.end() ? nullptr : &item->second;
}

bool
verifyProgramPhysicalOperationDomain(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry, VerifiedMatrixContractionFacts &out,
    MeshLoadError &error)
{
    if (!context.matches(program) || !geometry.matches(program, context)) {
        return fail(E_ABI_BOUNDS,
                    "physical operation facts belong to another verification invocation",
                    error);
    }
    VerifiedMatrixContractionFacts candidate;
    if (!PhysicalOperationBuilder(program, context, geometry, candidate).run(error))
        return false;
    out = std::move(candidate);
    error = {};
    return true;
}

}
}

#include "dev/ai_mesh/mesh_ir_compute_verifier.hh"

#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"

#include <algorithm>
#include <array>
#include <initializer_list>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

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
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > records.size())
        return nullptr;
    return &records[reference.row_id - 1];
}

bool
isComputeKernelOpcode(KernelOpcode opcode)
{
    return opcode == KernelOpcode::GEMM || opcode == KernelOpcode::BMM ||
        opcode == KernelOpcode::MATRIX_EPILOGUE ||
        opcode == KernelOpcode::VECTOR ||
        opcode == KernelOpcode::DATA_MOVEMENT ||
        opcode == KernelOpcode::REDUCE || opcode == KernelOpcode::SOFTMAX ||
        opcode == KernelOpcode::NORM ||
        opcode == KernelOpcode::LOCAL_REDUCE ||
        opcode == KernelOpcode::LOCAL_COPY;
}

struct ComputeProjection
{
    SemanticRef tile;
    SemanticRef cost;
    uint16_t opcode = 0;
    uint16_t attr_kind = 0;
};

bool
computeProjection(
    const DecodedProgram &program, const KernelOp &operation,
    ComputeProjection &out)
{
    switch (operation.opcode) {
    case KernelOpcode::GEMM:
    case KernelOpcode::BMM: {
        const GemmKernelAttrs *attrs = row(
            operation.attrs, program.semantic_tables.gemm_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {
            attrs->tile, attrs->cost,
            operation.opcode == KernelOpcode::GEMM ? kOpcodeGEMM : kOpcodeBMM,
            operation.opcode == KernelOpcode::GEMM ? kAttrKindGEMM_V1
                                                   : kAttrKindBMM_V1};
        return true;
    }
    case KernelOpcode::MATRIX_EPILOGUE: {
        const MatrixEpilogueKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.matrix_epilogue_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeELEMENTWISE,
               kAttrKindELEMENTWISE_V1};
        return true;
    }
    case KernelOpcode::VECTOR: {
        const VectorKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.vector_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeELEMENTWISE,
               kAttrKindELEMENTWISE_V1};
        return true;
    }
    case KernelOpcode::DATA_MOVEMENT: {
        const MovementKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.movement_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeELEMENTWISE,
               kAttrKindELEMENTWISE_V1};
        return true;
    }
    case KernelOpcode::REDUCE: {
        const ReductionKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.reduction_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeLOCAL_REDUCE,
               kAttrKindREDUCE_V1};
        return true;
    }
    case KernelOpcode::SOFTMAX: {
        const SoftmaxKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.softmax_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeSOFTMAX,
               kAttrKindSOFTMAX_V1};
        return true;
    }
    case KernelOpcode::NORM: {
        const NormKernelAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.norm_kernel_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeNORM,
               kAttrKindNORM_V1};
        return true;
    }
    case KernelOpcode::LOCAL_REDUCE: {
        const LocalReduceAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.local_reduce_attrs_rows);
        if (!attrs)
            return false;
        out = {attrs->tile, attrs->cost, kOpcodeLOCAL_REDUCE,
               kAttrKindREDUCE_V1};
        return true;
    }
    case KernelOpcode::LOCAL_COPY: {
        const LocalCopyAttrs *attrs = row(
            operation.attrs,
            program.semantic_tables.local_copy_attrs_rows);
        if (!attrs)
            return false;
        out = {{}, {}, kOpcodeELEMENTWISE, kAttrKindELEMENTWISE_V1};
        return true;
    }
    default:
        return false;
    }
}

bool
spanProduct(
    const DecodedProgram &program, const ListSpan &span, uint64_t &out,
    MeshLoadError &error)
{
    out = 1;
    const size_t end = size_t(span.begin) + span.count;
    if (end > program.semantic_u64_values.size())
        return fail(E_ABI_BOUNDS, "compute region shape is invalid", error);
    for (size_t index = span.begin; index < end; ++index) {
        const uint64_t extent = program.semantic_u64_values[index];
        if (extent != 0 && out > UINT64_MAX / extent)
            return fail(E_ABI_OVERFLOW,
                        "compute region element count overflows", error);
        out *= extent;
    }
    return true;
}

bool
accessProjection(
    const DecodedProgram &program, const SemanticRef &reference, bool write,
    const std::map<uint64_t, const BufferView *> &views,
    const std::map<uint64_t, const TensorShard *> &shards,
    const std::map<uint64_t, const KernelTensor *> &tensors,
    const ElementRegion *&region, uint16_t &dtype, MeshLoadError &error)
{
    uint64_t viewId = 0;
    SemanticRef regionRef;
    if (write) {
        const StateTransition *access = row(
            reference, program.semantic_tables.state_transition_rows);
        if (!access)
            return fail(E_ABI_BOUNDS,
                        "compute write access is invalid", error);
        viewId = access->view_id;
        regionRef = access->region;
    } else {
        const OperandAccess *access = row(
            reference, program.semantic_tables.operand_access_rows);
        if (!access)
            return fail(E_ABI_BOUNDS,
                        "compute read access is invalid", error);
        viewId = access->view_id;
        regionRef = access->region;
    }
    auto viewIt = views.find(viewId);
    region = row(regionRef, program.semantic_tables.element_region_rows);
    if (viewIt == views.end() || !region)
        return fail(E_ABI_BOUNDS,
                    "compute access projection is invalid", error);
    auto shardIt = shards.find(viewIt->second->shard_id);
    if (shardIt == shards.end())
        return fail(E_ABI_BOUNDS,
                    "compute access shard is invalid", error);
    auto tensorIt = tensors.find(shardIt->second->tensor_id);
    if (tensorIt == tensors.end())
        return fail(E_ABI_BOUNDS,
                    "compute access tensor is invalid", error);
    dtype = uint16_t(tensorIt->second->dtype);
    return true;
}

bool
projectionMatches(bool matches, MeshLoadError &error)
{
    return matches || fail(E_ABI_ENUM,
                           "compute attribute projection is invalid", error);
}

bool
computeAttributeMatches(
    const DecodedProgram &program, const KernelOp &operation,
    const ComputeProjection &projection, const TypedOpAttr &attr,
    const std::map<uint64_t, const BufferView *> &views,
    const std::map<uint64_t, const TensorShard *> &shards,
    const std::map<uint64_t, const KernelTensor *> &tensors,
    const KernelComputation *computation, const KernelTensor *emptyTarget,
    MeshLoadError &error)
{
    if (attr.kind != projection.attr_kind)
        return projectionMatches(false, error);
    const bool empty = emptyTarget != nullptr;
    const ElementRegion *readRegion = nullptr;
    const ElementRegion *writeRegion = nullptr;
    uint16_t readDtype = 0;
    uint16_t writeDtype = 0;
    if (!empty && operation.reads.count != 0 &&
        !accessProjection(
            program, program.semantic_references[operation.reads.begin],
            false, views, shards, tensors, readRegion, readDtype, error))
        return false;
    if (!empty && operation.writes.count != 0 &&
        !accessProjection(
            program, program.semantic_references[operation.writes.begin],
            true, views, shards, tensors, writeRegion, writeDtype, error))
        return false;
    uint16_t logicalInputDtype = 0;
    if (empty) {
        if (!computation || computation->operand_tensor_ids.count == 0)
            return fail(E_ABI_BOUNDS,
                        "empty compute operand dtype is invalid", error);
        const uint64_t tensorId = program.semantic_u64_values[
            computation->operand_tensor_ids.begin];
        auto tensorIt = tensors.find(tensorId);
        if (tensorIt == tensors.end())
            return fail(E_ABI_BOUNDS,
                        "empty compute operand tensor is invalid", error);
        logicalInputDtype = uint16_t(tensorIt->second->dtype);
    }
    const KernelTile *tile = projection.tile.row_id == 0 ? nullptr : row(
        projection.tile, program.semantic_tables.kernel_tile_rows);
    const KernelCost *cost = projection.cost.row_id == 0 ? nullptr : row(
        projection.cost, program.semantic_tables.kernel_cost_rows);
    switch (operation.opcode) {
    case KernelOpcode::GEMM:
    case KernelOpcode::BMM: {
        const GemmKernelAttrs *kernelAttrs = row(
            operation.attrs,
            program.semantic_tables.gemm_kernel_attrs_rows);
        const MatmulAttrs *semanticAttrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.matmul_attrs_rows) : nullptr;
        if (!tile || !semanticAttrs || (!empty && operation.reads.count < 2))
            return fail(E_ABI_BOUNDS,
                        "matrix attribute source is invalid", error);
        const uint64_t batch =
            empty ? tile->valid_batch : tile->batch_extent;
        const uint64_t m = empty ? tile->valid_m : tile->m_extent;
        const uint64_t n = empty ? tile->valid_n : tile->n_extent;
        const uint64_t k = empty ? tile->valid_k : tile->k_extent;
        const uint16_t dtype = empty ? logicalInputDtype : readDtype;
        const GemmV1 *gemm = attr.as<GemmV1>();
        const BmmV1 *bmm = attr.as<BmmV1>();
        const uint64_t dimensions[4] = {
            gemm ? gemm->batch : bmm ? bmm->batch : 0,
            gemm ? gemm->m : bmm ? bmm->m : 0,
            gemm ? gemm->n : bmm ? bmm->n : 0,
            gemm ? gemm->k : bmm ? bmm->k : 0};
        uint64_t work = 1;
        for (const uint64_t dimension : dimensions) {
            if (dimension != 0 && work > UINT64_MAX / dimension)
                return fail(E_ABI_OVERFLOW,
                            "matrix workload overflows", error);
            work *= dimension;
            if (work >= (uint64_t(1) << 63))
                return fail(E_ABI_OVERFLOW,
                            "matrix workload exceeds schedulable range",
                            error);
        }
        if (operation.opcode == KernelOpcode::GEMM) {
            return projectionMatches(
                gemm && gemm->batch == batch && gemm->m == m &&
                gemm->n == n && gemm->k == k &&
                gemm->a_transpose == semanticAttrs->lhs_transpose &&
                gemm->b_transpose == semanticAttrs->rhs_transpose &&
                gemm->dtype == dtype && gemm->accum_dtype ==
                    uint16_t(semanticAttrs->accum_dtype) &&
                gemm->epilogue == 0 && gemm->efficiency_q16 == 65536,
                error);
        }
        return projectionMatches(
            bmm && bmm->batch == batch && bmm->m == m &&
            bmm->n == n && bmm->k == k &&
            bmm->a_transpose == semanticAttrs->lhs_transpose &&
            bmm->b_transpose == semanticAttrs->rhs_transpose &&
            bmm->dtype == dtype && bmm->accum_dtype ==
                uint16_t(semanticAttrs->accum_dtype) &&
            bmm->epilogue == 0 && bmm->efficiency_q16 == 65536,
            error);
    }
    case KernelOpcode::MATRIX_EPILOGUE:
    case KernelOpcode::VECTOR:
    case KernelOpcode::DATA_MOVEMENT: {
        if (!cost || (!empty && !writeRegion))
            return fail(E_ABI_BOUNDS,
                        "elementwise attribute source is invalid", error);
        uint64_t count = 0;
        if (!empty && !spanProduct(program, writeRegion->shape, count, error))
            return false;
        const uint64_t divisor = count == 0 ? 1 : count;
        const uint64_t quotient = cost->vector_ops / divisor;
        const uint64_t remainder = cost->vector_ops % divisor;
        const uint64_t perElement = std::max<uint64_t>(
            1, quotient + (remainder != 0));
        const ElementwiseV1 *value = attr.as<ElementwiseV1>();
        return projectionMatches(
            value && value->element_count == count && value->dtype ==
            (empty ? uint16_t(emptyTarget->dtype) : writeDtype) &&
            value->op == 0 && value->ops_per_element == perElement &&
            value->reserved == 0, error);
    }
    case KernelOpcode::LOCAL_COPY: {
        if (operation.writes.count == 0 ||
            operation.reads.count != operation.writes.count)
            return fail(E_ABI_BOUNDS,
                        "local copy access domain is invalid", error);
        uint64_t count = 0;
        const size_t end = size_t(operation.writes.begin) +
            operation.writes.count;
        for (size_t index = operation.writes.begin; index < end; ++index) {
            const ElementRegion *region = nullptr;
            uint16_t dtype = 0;
            if (!accessProjection(
                    program, program.semantic_references[index], true,
                    views, shards, tensors, region, dtype, error))
                return false;
            uint64_t elements = 0;
            if (!spanProduct(program, region->shape, elements, error))
                return false;
            if (count > UINT64_MAX - elements)
                return fail(E_ABI_OVERFLOW,
                            "local copy element count overflows", error);
            count += elements;
        }
        const ElementwiseV1 *value = attr.as<ElementwiseV1>();
        return projectionMatches(
            value && value->element_count == count &&
            value->dtype == writeDtype && value->op == 0 &&
            value->ops_per_element == 1 && value->reserved == 0, error);
    }
    case KernelOpcode::REDUCE: {
        const ReductionKernelAttrs *kernelAttrs = row(
            operation.attrs,
            program.semantic_tables.reduction_kernel_attrs_rows);
        const ReduceAttrs *semanticAttrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.reduce_attrs_rows) : nullptr;
        if (!semanticAttrs || (!empty && !readRegion))
            return fail(E_ABI_BOUNDS,
                        "reduction attribute source is invalid", error);
        uint64_t count = 0;
        if (!empty && !spanProduct(program, readRegion->shape, count, error))
            return false;
        const uint16_t dtype = empty ? logicalInputDtype : readDtype;
        const ReduceV1 *value = attr.as<ReduceV1>();
        return projectionMatches(
            value && value->element_count == count &&
            value->dtype == dtype && value->accum_dtype ==
                uint16_t(semanticAttrs->accum_dtype) && value->op == 0 &&
            value->fan_in == 2, error);
    }
    case KernelOpcode::LOCAL_REDUCE: {
        if (operation.writes.count == 0 ||
            operation.reads.count % operation.writes.count != 0 ||
            operation.reads.count / operation.writes.count < 2)
            return fail(E_ABI_BOUNDS,
                        "local reduction access domain is invalid", error);
        uint64_t count = 0;
        const size_t end = size_t(operation.writes.begin) +
            operation.writes.count;
        for (size_t index = operation.writes.begin; index < end; ++index) {
            const ElementRegion *region = nullptr;
            uint16_t dtype = 0;
            if (!accessProjection(
                    program, program.semantic_references[index], true,
                    views, shards, tensors, region, dtype, error))
                return false;
            uint64_t elements = 0;
            if (!spanProduct(program, region->shape, elements, error))
                return false;
            if (count > UINT64_MAX - elements)
                return fail(E_ABI_OVERFLOW,
                            "local reduction element count overflows", error);
            count += elements;
        }
        const ReduceV1 *value = attr.as<ReduceV1>();
        return projectionMatches(
            value && value->element_count == count &&
            value->dtype == readDtype && value->accum_dtype == readDtype &&
            value->op == 0 && value->fan_in ==
                operation.reads.count / operation.writes.count, error);
    }
    case KernelOpcode::SOFTMAX: {
        const SoftmaxKernelAttrs *kernelAttrs = row(
            operation.attrs,
            program.semantic_tables.softmax_kernel_attrs_rows);
        const SoftmaxAttrs *semanticAttrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.softmax_attrs_rows) : nullptr;
        if (!semanticAttrs || (!empty && !readRegion))
            return fail(E_ABI_BOUNDS,
                        "softmax attribute source is invalid", error);
        uint64_t axisSize = 0;
        if (!empty) {
            if (semanticAttrs->axis >= readRegion->shape.count)
                return fail(E_ABI_BOUNDS,
                            "softmax axis is outside its region", error);
            axisSize = program.semantic_u64_values[
                readRegion->shape.begin + semanticAttrs->axis];
        }
        const SoftmaxV1 *value = attr.as<SoftmaxV1>();
        return projectionMatches(
            value && value->axis_size == axisSize && value->dtype ==
            (empty ? logicalInputDtype : readDtype) &&
            value->algorithm == kVectorAlgorithmSTANDARD &&
            value->reserved == 0, error);
    }
    case KernelOpcode::NORM: {
        if (!empty && !writeRegion)
            return fail(E_ABI_BOUNDS,
                        "normalization attribute source is invalid", error);
        uint64_t count = 0;
        if (!empty && !spanProduct(program, writeRegion->shape, count, error))
            return false;
        const NormV1 *value = attr.as<NormV1>();
        return projectionMatches(
            value && value->element_count == count && value->dtype ==
            (empty ? uint16_t(emptyTarget->dtype) : writeDtype) &&
            value->algorithm == kVectorAlgorithmSTANDARD &&
            value->reserved == 0, error);
    }
    default:
        return projectionMatches(false, error);
    }
}

struct Coordinate
{
    uint64_t origin;
    uint64_t extent;
    uint64_t step;

    bool operator==(const Coordinate &other) const
    {
        return origin == other.origin && extent == other.extent &&
            step == other.step;
    }
};

struct ComputeAccess
{
    const GeometryAccessFact *fact = nullptr;
    std::vector<Coordinate> coordinates;
};

struct WorkPhase
{
    std::vector<WorkEstimate> work;
};

bool
checkedAdd(uint64_t lhs, uint64_t rhs, uint64_t &out, MeshLoadError &error)
{
    if (lhs > UINT64_MAX - rhs)
        return fail(E_ABI_OVERFLOW, "compute work addition overflows", error);
    out = lhs + rhs;
    return true;
}

bool
checkedMul(uint64_t lhs, uint64_t rhs, uint64_t &out, MeshLoadError &error)
{
    if (lhs != 0 && rhs > UINT64_MAX / lhs)
        return fail(E_ABI_OVERFLOW,
                    "compute work multiplication overflows", error);
    out = lhs * rhs;
    return true;
}

bool
computeAccess(
    const DecodedProgram &program, const VerifiedProgramGeometry &geometry,
    uint64_t operationId, GeometryAccessRole role, size_t ordinal,
    ComputeAccess &out, MeshLoadError &error)
{
    const GeometryAccessFact *fact = geometry.access(operationId, role, ordinal);
    if (!fact)
        return fail(E_ABI_BOUNDS,
                    "physical work access geometry is invalid", error);
    const size_t rank = fact->shape().size();
    const BufferView &view = fact->view();
    const KernelTensor &tensor = fact->tensor();
    if (fact->localOrigin().size() != rank ||
        fact->globalOrigin().size() != rank ||
        fact->steps().size() != rank || view.valid_shape.count != rank ||
        tensor.shape.count != rank)
        return fail(E_EXPORT_LAYOUT,
                    "physical work access rank is invalid", error);
    out = {fact, {}};
    out.coordinates.reserve(rank);
    for (size_t index = 0; index < rank; ++index) {
        const uint64_t extent = fact->shape()[index];
        const uint64_t step = fact->steps()[index];
        const uint64_t localOrigin = fact->localOrigin()[index];
        const uint64_t globalOrigin = fact->globalOrigin()[index];
        const uint64_t valid = program.semantic_u64_values[
            view.valid_shape.begin + index];
        if (extent != 0) {
            uint64_t distance = 0;
            uint64_t localLast = 0;
            uint64_t globalLast = 0;
            if (!checkedMul(extent - 1, step, distance, error) ||
                !checkedAdd(localOrigin, distance, localLast, error) ||
                !checkedAdd(globalOrigin, distance, globalLast, error))
                return false;
            if (localLast >= valid)
                return fail(E_EXPORT_LAYOUT,
                            "physical work access exceeds its view", error);
            if (globalLast >= program.semantic_u64_values[
                    tensor.shape.begin + index])
                return fail(E_EXPORT_LAYOUT,
                            "physical work access exceeds its tensor", error);
        }
        out.coordinates.push_back({globalOrigin, extent, step});
    }
    return true;
}

bool
appendWork(
    std::vector<WorkEstimate> &work, semantic_abi::Engine engine,
    WorkUnit unit, semantic_abi::DType dtype, uint64_t operations,
    MeshLoadError &error)
{
    if (operations == 0)
        return true;
    for (WorkEstimate &item : work) {
        if (item.engine == engine && item.unit == unit &&
            item.dtype == dtype) {
            return checkedAdd(
                item.operations, operations, item.operations, error);
        }
    }
    work.push_back({0, engine, unit, dtype, operations});
    return true;
}

bool
appendPhase(
    std::vector<WorkPhase> &phases, std::vector<WorkEstimate> work,
    MeshLoadError &error)
{
    if (work.empty())
        return true;
    if (!phases.empty() &&
        phases.back().work.front().engine == work.front().engine) {
        for (const WorkEstimate &item : work) {
            if (!appendWork(
                    phases.back().work, item.engine, item.unit, item.dtype,
                    item.operations, error))
                return false;
        }
        return true;
    }
    phases.push_back({std::move(work)});
    return true;
}

bool
matrixEpilogueWork(
    OpCode opcode, const MatmulAttrs &attrs, uint64_t elements,
    semantic_abi::DType outputDtype, bool commandPhase,
    std::vector<WorkEstimate> &work, MeshLoadError &error)
{
    const bool alphaMath = attrs.alpha != 1.0;
    if (alphaMath && !appendWork(
            work, semantic_abi::Engine::VECTOR, WorkUnit::MUL,
            attrs.accum_dtype, elements, error))
        return false;
    bool hasMath = alphaMath;
    if (opcode == OpCode::LINEAR_BIAS) {
        if (attrs.beta != 1.0 && !appendWork(
                work, semantic_abi::Engine::VECTOR, WorkUnit::MUL,
                attrs.accum_dtype, elements, error))
            return false;
        if (!appendWork(
                work, semantic_abi::Engine::VECTOR, WorkUnit::ADD,
                attrs.accum_dtype, elements, error))
            return false;
        hasMath = true;
    }
    if (commandPhase && elements != 0) {
        if (outputDtype != attrs.accum_dtype) {
            if (!appendWork(
                    work, semantic_abi::Engine::VECTOR, WorkUnit::CAST,
                    attrs.accum_dtype, elements, error))
                return false;
        } else if (!hasMath && !appendWork(
                work, semantic_abi::Engine::VECTOR, WorkUnit::COPY,
                attrs.accum_dtype, elements, error)) {
            return false;
        }
    }
    return true;
}

bool
rowDomain(
    const DecodedProgram &program, const KernelOp &operation,
    const std::vector<uint64_t> &semanticAxes,
    const VerifiedProgramGeometry &geometry,
    uint64_t &rows, uint64_t &fanIn, semantic_abi::DType &dtype,
    MeshLoadError &error)
{
    if (operation.reads.count == 0 || operation.writes.count == 0)
        return fail(E_ABI_BOUNDS,
                    "physical row operation has no access", error);
    ComputeAccess source;
    if (!computeAccess(
            program, geometry, operation.op_id, GeometryAccessRole::Read, 0,
            source, error))
        return false;
    const size_t rank = source.coordinates.size();
    std::vector<size_t> axes;
    for (const uint64_t axis : semanticAxes) {
        if (axis >= rank ||
            std::find(axes.begin(), axes.end(), size_t(axis)) != axes.end())
            return fail(E_EXPORT_LAYOUT,
                        "physical row operation axes are invalid", error);
        axes.push_back(size_t(axis));
    }
    if (axes.empty())
        return fail(E_EXPORT_LAYOUT,
                    "physical row operation axes are empty", error);
    rows = 1;
    fanIn = 1;
    for (size_t index = 0; index < rank; ++index) {
        const bool reduced =
            std::find(axes.begin(), axes.end(), index) != axes.end();
        const Coordinate &coordinate = source.coordinates[index];
        if (reduced) {
            if (coordinate.origin != 0 || coordinate.step != 1 ||
                coordinate.extent != program.semantic_u64_values[
                    source.fact->tensor().shape.begin + index])
                return fail(E_EXPORT_LAYOUT,
                            "physical row operation splits an axis", error);
            if (!checkedMul(fanIn, coordinate.extent, fanIn, error))
                return false;
        } else if (!checkedMul(rows, coordinate.extent, rows, error)) {
            return false;
        }
    }
    ComputeAccess result;
    if (!computeAccess(
            program, geometry, operation.op_id, GeometryAccessRole::Write, 0,
            result, error))
        return false;
    std::vector<Coordinate> expected;
    if (operation.opcode == KernelOpcode::REDUCE) {
        const ReductionKernelAttrs *kernelAttrs = row(
            operation.attrs,
            program.semantic_tables.reduction_kernel_attrs_rows);
        const ReduceAttrs *attrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.reduce_attrs_rows) : nullptr;
        if (!attrs)
            return fail(E_ABI_BOUNDS,
                        "physical reduction attributes are invalid", error);
        if (attrs->keepdim) {
            expected.reserve(source.coordinates.size());
            for (const Coordinate &coordinate : source.coordinates)
                expected.push_back(coordinate);
            for (const size_t axis : axes)
                expected[axis] = {0, 1, 1};
        } else {
            for (size_t index = 0; index < rank; ++index) {
                if (std::find(axes.begin(), axes.end(), index) == axes.end())
                    expected.push_back(source.coordinates[index]);
            }
        }
    } else {
        expected.reserve(source.coordinates.size());
        for (const Coordinate &coordinate : source.coordinates)
            expected.push_back(coordinate);
    }
    if (result.coordinates != expected)
        return fail(E_EXPORT_LAYOUT,
                    "physical row result coordinates are invalid", error);
    if (operation.opcode == KernelOpcode::NORM) {
        std::vector<Coordinate> normalized;
        for (const size_t axis : axes)
            normalized.push_back(source.coordinates[axis]);
        for (size_t index = 1; index < operation.reads.count; ++index) {
            ComputeAccess affine;
            if (!computeAccess(
                    program, geometry, operation.op_id,
                    GeometryAccessRole::Read, index, affine, error))
                return false;
            if (affine.coordinates != normalized)
                return fail(E_EXPORT_LAYOUT,
                            "normalization affine access is invalid", error);
        }
    }
    dtype = source.fact->tensor().dtype;
    return true;
}

bool
localCopyDomain(
    const DecodedProgram &program, const KernelOp &operation,
    const VerifiedProgramGeometry &geometry,
    uint64_t &elements, semantic_abi::DType &dtype, MeshLoadError &error)
{
    if (operation.reads.count == 0 ||
        operation.reads.count != operation.writes.count)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "local copy physical arity is invalid", error);
    std::set<uint64_t> sourceObjects;
    std::set<uint64_t> destinationObjects;
    std::vector<std::pair<ComputeAccess, ComputeAccess>> pairs;
    pairs.reserve(operation.reads.count);
    elements = 0;
    for (size_t index = 0; index < operation.reads.count; ++index) {
        ComputeAccess source;
        ComputeAccess destination;
        if (!computeAccess(
                program, geometry, operation.op_id, GeometryAccessRole::Read,
                index, source, error) ||
            !computeAccess(
                program, geometry, operation.op_id, GeometryAccessRole::Write,
                index, destination, error))
            return false;
        if (source.fact->object().memory_space !=
                semantic_abi::MemorySpace::CORE_SRAM ||
            destination.fact->object().memory_space !=
                semantic_abi::MemorySpace::CORE_SRAM ||
            source.fact->object().owner_core != operation.owner_core ||
            destination.fact->object().owner_core != operation.owner_core)
            return fail(E_TENSOR_NOT_RESIDENT,
                        "local copy endpoints are not owner-local", error);
        sourceObjects.insert(source.fact->object().object_id);
        destinationObjects.insert(destination.fact->object().object_id);
        pairs.emplace_back(std::move(source), std::move(destination));
    }
    if (sourceObjects.size() != 1 || destinationObjects.size() != 1)
        return fail(E_DMA_RANGE,
                    "movement pieces must share one source and destination object",
                    error);
    for (size_t index = 0; index < pairs.size(); ++index) {
        const ComputeAccess &source = pairs[index].first;
        const ComputeAccess &destination = pairs[index].second;
        if (source.fact->tensor().alias_root_tensor_id !=
                destination.fact->tensor().alias_root_tensor_id ||
            source.fact->tensor().dtype != destination.fact->tensor().dtype ||
            source.coordinates != destination.coordinates)
            return fail(E_DMA_RANGE,
                        "movement source and destination pieces differ",
                        error);
        bool sourceInjective = false;
        if (!geometry.accessMapsInjectively(
                operation.op_id, GeometryAccessRole::Read, index,
                sourceInjective, error))
            return false;
        if (!sourceInjective)
            return fail(E_DMA_RANGE,
                        "movement source and destination pieces differ",
                        error);
        if (!checkedAdd(
                elements, destination.fact->logicalElementCount(), elements,
                error))
            return false;
        dtype = destination.fact->tensor().dtype;
    }
    return true;
}

bool
localReductionDomain(
    const DecodedProgram &program, const KernelOp &operation,
    const VerifiedProgramGeometry &geometry,
    uint64_t &rows, uint64_t &fanIn, semantic_abi::DType &dtype,
    MeshLoadError &error)
{
    if (operation.writes.count == 0 ||
        operation.reads.count % operation.writes.count != 0 ||
        operation.reads.count / operation.writes.count < 2)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "local reduction physical fan-in is invalid", error);
    fanIn = operation.reads.count / operation.writes.count;
    rows = 0;
    for (size_t index = 0; index < operation.writes.count; ++index) {
        ComputeAccess result;
        if (!computeAccess(
                program, geometry, operation.op_id, GeometryAccessRole::Write,
                index, result, error))
            return false;
        if (!checkedAdd(rows, result.fact->logicalElementCount(), rows, error))
            return false;
        for (size_t input = 0; input < fanIn; ++input) {
            ComputeAccess source;
            if (!computeAccess(
                    program, geometry, operation.op_id,
                    GeometryAccessRole::Read, index * fanIn + input, source,
                    error))
                return false;
            if (source.fact->tensor().dtype != result.fact->tensor().dtype ||
                source.coordinates != result.coordinates)
                return fail(E_EXPORT_LAYOUT,
                            "local reduction coordinates are invalid", error);
        }
        dtype = result.fact->tensor().dtype;
    }
    return true;
}

bool
estimateElementWork(
    const DecodedProgram &program, OpCode opcode,
    const SemanticRef &attrsRef, semantic_abi::DType dtype,
    uint64_t elements, std::vector<WorkPhase> &phases,
    MeshLoadError &error)
{
    std::vector<WorkEstimate> work;
    const ElementwiseAttrs *attrs = row(
        attrsRef, program.semantic_tables.elementwise_attrs_rows);
    auto add = [&](WorkUnit unit, uint64_t count) {
        return appendWork(
            work, semantic_abi::Engine::VECTOR, unit, dtype, count, error);
    };
    switch (opcode) {
    case OpCode::ADD:
    case OpCode::SUB:
    case OpCode::MUL:
    case OpCode::DIV:
        if (!attrs)
            return fail(E_ABI_BOUNDS,
                        "elementwise attributes are invalid", error);
        if (!add(
                opcode == OpCode::ADD ? WorkUnit::ADD :
                opcode == OpCode::SUB ? WorkUnit::SUB :
                opcode == OpCode::MUL ? WorkUnit::MUL : WorkUnit::DIV,
                elements))
            return false;
        if ((opcode == OpCode::ADD || opcode == OpCode::SUB) &&
            attrs->alpha != 1.0 && !add(WorkUnit::MUL, elements))
            return false;
        break;
    case OpCode::RELU:
        if (!attrs || !add(WorkUnit::MAX, elements))
            return attrs ? false : fail(
                E_ABI_BOUNDS, "elementwise attributes are invalid", error);
        break;
    case OpCode::GELU: {
        if (!attrs || attrs->approximation.string_id == 0 ||
            attrs->approximation.string_id > program.semantic_strings.size())
            return fail(E_ABI_BOUNDS,
                        "GELU approximation is invalid", error);
        const std::string &approximation =
            program.semantic_strings[attrs->approximation.string_id - 1];
        if (approximation == "none") {
            uint64_t twice = 0;
            if (!checkedMul(elements, 2, twice, error) ||
                !add(WorkUnit::DIV, elements) ||
                !add(WorkUnit::ERF, elements) ||
                !add(WorkUnit::ADD, elements) ||
                !add(WorkUnit::MUL, twice))
                return false;
        } else if (approximation == "tanh") {
            uint64_t six = 0;
            uint64_t twice = 0;
            if (!checkedMul(elements, 6, six, error) ||
                !checkedMul(elements, 2, twice, error) ||
                !add(WorkUnit::MUL, six) ||
                !add(WorkUnit::ADD, twice) ||
                !add(WorkUnit::TANH, elements))
                return false;
        } else {
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "GELU approximation is unsupported", error);
        }
        break;
    }
    case OpCode::SILU:
        if (!attrs || !add(WorkUnit::NEGATE, elements) ||
            !add(WorkUnit::EXP, elements) ||
            !add(WorkUnit::ADD, elements) ||
            !add(WorkUnit::DIV, elements))
            return attrs ? false : fail(
                E_ABI_BOUNDS, "elementwise attributes are invalid", error);
        break;
    case OpCode::EXP:
    case OpCode::RSQRT:
        if (!attrs || !add(
                opcode == OpCode::EXP ? WorkUnit::EXP : WorkUnit::RSQRT,
                elements))
            return attrs ? false : fail(
                E_ABI_BOUNDS, "elementwise attributes are invalid", error);
        break;
    case OpCode::CONTIGUOUS_COPY:
    case OpCode::CONCAT:
        if (!add(WorkUnit::COPY, elements))
            return false;
        break;
    case OpCode::GATHER_ROWS:
    case OpCode::EMBEDDING_LOOKUP:
        if (!add(WorkUnit::GATHER, elements))
            return false;
        break;
    default:
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "element operation work is unsupported", error);
    }
    return appendPhase(phases, std::move(work), error);
}

bool
estimateRowWork(
    const DecodedProgram &program, OpCode opcode,
    const SemanticRef &attrsRef, semantic_abi::DType inputDtype,
    uint64_t rows, uint64_t fanIn, std::vector<WorkPhase> &phases,
    MeshLoadError &error)
{
    uint64_t elements = 0;
    uint64_t comparisons = 0;
    if (!checkedMul(rows, fanIn, elements, error) ||
        !checkedMul(rows, fanIn == 0 ? 0 : fanIn - 1,
                    comparisons, error))
        return false;
    if (opcode == OpCode::REDUCE_SUM || opcode == OpCode::REDUCE_MAX ||
        opcode == OpCode::REDUCE_MEAN) {
        const ReduceAttrs *attrs = row(
            attrsRef, program.semantic_tables.reduce_attrs_rows);
        if (!attrs)
            return fail(E_ABI_BOUNDS,
                        "reduction attributes are invalid", error);
        std::vector<WorkEstimate> reduction;
        if (!appendWork(
                reduction, semantic_abi::Engine::REDUCE,
                opcode == OpCode::REDUCE_MAX ? WorkUnit::MAX : WorkUnit::ADD,
                attrs->accum_dtype, comparisons, error) ||
            !appendPhase(phases, std::move(reduction), error))
            return false;
        if (opcode == OpCode::REDUCE_MEAN) {
            std::vector<WorkEstimate> vector;
            if (!appendWork(
                    vector, semantic_abi::Engine::VECTOR, WorkUnit::DIV,
                    attrs->accum_dtype, rows, error) ||
                !appendPhase(phases, std::move(vector), error))
                return false;
        }
        return true;
    }
    if (elements == 0)
        return true;
    const semantic_abi::DType dtype = computationAccumulationDtype(inputDtype);
    auto phase = [&](semantic_abi::Engine engine,
                     std::initializer_list<std::pair<WorkUnit, uint64_t>> items) {
        std::vector<WorkEstimate> work;
        for (const auto &[unit, operations] : items) {
            if (!appendWork(work, engine, unit, dtype, operations, error))
                return false;
        }
        return appendPhase(phases, std::move(work), error);
    };
    if (opcode == OpCode::SOFTMAX) {
        const SoftmaxAttrs *attrs = row(
            attrsRef, program.semantic_tables.softmax_attrs_rows);
        if (!attrs)
            return fail(E_ABI_BOUNDS,
                        "softmax attributes are invalid", error);
        if (attrs->zero_fully_masked_rows && !phase(
                semantic_abi::Engine::VECTOR,
                {{WorkUnit::PREDICATE, elements}}))
            return false;
        std::vector<std::pair<WorkUnit, uint64_t>> maximum;
        if (attrs->zero_fully_masked_rows)
            maximum.push_back({WorkUnit::LOGICAL_AND, comparisons});
        maximum.push_back({WorkUnit::MAX, comparisons});
        std::vector<WorkEstimate> maximumWork;
        for (const auto &[unit, operations] : maximum) {
            if (!appendWork(
                    maximumWork, semantic_abi::Engine::REDUCE, unit, dtype,
                    operations, error))
                return false;
        }
        if (!appendPhase(phases, std::move(maximumWork), error) ||
            !phase(semantic_abi::Engine::VECTOR,
                   {{WorkUnit::SUB, elements}, {WorkUnit::EXP, elements}}) ||
            !phase(semantic_abi::Engine::REDUCE,
                   {{WorkUnit::ADD, comparisons}}))
            return false;
        std::vector<std::pair<WorkUnit, uint64_t>> normalize = {
            {WorkUnit::DIV, elements}};
        if (attrs->zero_fully_masked_rows)
            normalize.push_back({WorkUnit::SELECT, elements});
        std::vector<WorkEstimate> normalizeWork;
        for (const auto &[unit, operations] : normalize) {
            if (!appendWork(
                    normalizeWork, semantic_abi::Engine::VECTOR, unit,
                    dtype, operations, error))
                return false;
        }
        return appendPhase(phases, std::move(normalizeWork), error);
    }
    const NormAttrs *attrs = row(
        attrsRef, program.semantic_tables.norm_attrs_rows);
    if (!attrs)
        return fail(E_ABI_BOUNDS,
                    "normalization attributes are invalid", error);
    if (opcode == OpCode::LAYERNORM) {
        if (!phase(semantic_abi::Engine::REDUCE,
                   {{WorkUnit::ADD, comparisons}}) ||
            !phase(semantic_abi::Engine::VECTOR,
                   {{WorkUnit::DIV, rows}, {WorkUnit::SUB, elements},
                    {WorkUnit::MUL, elements}}) ||
            !phase(semantic_abi::Engine::REDUCE,
                   {{WorkUnit::ADD, comparisons}}))
            return false;
    } else if (opcode == OpCode::RMSNORM) {
        if (!phase(semantic_abi::Engine::VECTOR,
                   {{WorkUnit::MUL, elements}}) ||
            !phase(semantic_abi::Engine::REDUCE,
                   {{WorkUnit::ADD, comparisons}}))
            return false;
    } else {
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "normalization work is unsupported", error);
    }
    std::vector<std::pair<WorkUnit, uint64_t>> normalize = {
        {WorkUnit::DIV, rows}, {WorkUnit::ADD, rows},
        {WorkUnit::RSQRT, rows}, {WorkUnit::MUL, elements}};
    if (attrs->has_weight)
        normalize.push_back({WorkUnit::MUL, elements});
    if (opcode == OpCode::LAYERNORM && attrs->has_bias)
        normalize.push_back({WorkUnit::ADD, elements});
    std::vector<WorkEstimate> normalizeWork;
    for (const auto &[unit, operations] : normalize) {
        if (!appendWork(
                normalizeWork, semantic_abi::Engine::VECTOR, unit, dtype,
                operations, error))
            return false;
    }
    return appendPhase(phases, std::move(normalizeWork), error);
}

bool
expectedWorkPhases(
    const DecodedProgram &program, const KernelOp &operation,
    const KernelComputation *computation,
    const VerifiedProgramGeometry &geometry,
    const std::map<uint64_t, const KernelTensor *> &tensors,
    std::vector<WorkPhase> &phases, MeshLoadError &error)
{
    if (operation.opcode == KernelOpcode::LOCAL_COPY) {
        uint64_t elements = 0;
        semantic_abi::DType dtype{};
        if (!localCopyDomain(
                program, operation, geometry, elements, dtype, error))
            return false;
        return estimateElementWork(
            program, OpCode::CONTIGUOUS_COPY, {}, dtype, elements, phases,
            error);
    }
    if (operation.opcode == KernelOpcode::LOCAL_REDUCE) {
        uint64_t rows = 0;
        uint64_t fanIn = 0;
        semantic_abi::DType dtype{};
        if (!localReductionDomain(
                program, operation, geometry, rows, fanIn, dtype, error))
            return false;
        std::vector<WorkEstimate> work;
        uint64_t comparisons = 0;
        if (!checkedMul(rows, fanIn - 1, comparisons, error) ||
            !appendWork(
                work, semantic_abi::Engine::REDUCE, WorkUnit::ADD, dtype,
                comparisons, error))
            return false;
        return appendPhase(phases, std::move(work), error);
    }
    if (!computation)
        return fail(E_ABI_BOUNDS,
                    "compute operation identity is invalid", error);
    std::vector<semantic_abi::DType> operandDtypes;
    for (size_t index = 0; index < computation->operand_tensor_ids.count;
         ++index) {
        const uint64_t tensorId = program.semantic_u64_values[
            computation->operand_tensor_ids.begin + index];
        auto tensorIt = tensors.find(tensorId);
        if (tensorIt == tensors.end())
            return fail(E_ABI_BOUNDS,
                        "compute operand tensor is invalid", error);
        operandDtypes.push_back(tensorIt->second->dtype);
    }
    auto resultIt = tensors.find(computation->result_tensor_id);
    if (operandDtypes.empty() || resultIt == tensors.end())
        return fail(E_ABI_BOUNDS,
                    "compute logical tensors are invalid", error);
    semantic_abi::DType resultDtype = resultIt->second->dtype;
    if (operation.opcode == KernelOpcode::GEMM ||
        operation.opcode == KernelOpcode::BMM) {
        const GemmKernelAttrs *kernelAttrs = row(
            operation.attrs, program.semantic_tables.gemm_kernel_attrs_rows);
        const MatmulAttrs *attrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.matmul_attrs_rows) : nullptr;
        const KernelTile *tile = kernelAttrs ? row(
            kernelAttrs->tile,
            program.semantic_tables.kernel_tile_rows) : nullptr;
        if (!attrs || !tile || kernelAttrs->graph_opcode != computation->opcode)
            return fail(E_ABI_BOUNDS,
                        "matrix work attributes are invalid", error);
        uint64_t elements = 0;
        uint64_t operations = 0;
        if (!checkedMul(tile->valid_batch, tile->valid_m, elements, error) ||
            !checkedMul(elements, tile->valid_n, elements, error) ||
            !checkedMul(elements, tile->valid_k, operations, error))
            return false;
        std::vector<WorkEstimate> tensor;
        if (!appendWork(
                tensor, semantic_abi::Engine::TENSOR, WorkUnit::MAC,
                operandDtypes.front(), operations, error) ||
            !appendPhase(phases, std::move(tensor), error))
            return false;
        if (kernelAttrs->phase != MatrixPhase::DIRECT)
            return true;
        std::vector<WorkEstimate> epilogue;
        if (!matrixEpilogueWork(
                computation->opcode, *attrs, elements, resultDtype, false,
                epilogue, error))
            return false;
        return appendPhase(phases, std::move(epilogue), error);
    }
    if (operation.opcode == KernelOpcode::MATRIX_EPILOGUE) {
        const MatrixEpilogueKernelAttrs *kernelAttrs = row(
            operation.attrs,
            program.semantic_tables.matrix_epilogue_kernel_attrs_rows);
        const MatmulAttrs *attrs = kernelAttrs ? row(
            kernelAttrs->semantic_attrs,
            program.semantic_tables.matmul_attrs_rows) : nullptr;
        const KernelTile *tile = kernelAttrs ? row(
            kernelAttrs->tile,
            program.semantic_tables.kernel_tile_rows) : nullptr;
        if (!attrs || !tile || kernelAttrs->graph_opcode != computation->opcode)
            return fail(E_ABI_BOUNDS,
                        "matrix epilogue work attributes are invalid", error);
        uint64_t elements = 0;
        if (!checkedMul(tile->valid_batch, tile->valid_m, elements, error) ||
            !checkedMul(elements, tile->valid_n, elements, error))
            return false;
        std::vector<WorkEstimate> work;
        if (!matrixEpilogueWork(
                computation->opcode, *attrs, elements, resultDtype, true,
                work, error))
            return false;
        return appendPhase(phases, std::move(work), error);
    }
    if (operation.opcode == KernelOpcode::VECTOR ||
        operation.opcode == KernelOpcode::DATA_MOVEMENT) {
        SemanticRef attrsRef;
        OpCode opcode{};
        if (operation.opcode == KernelOpcode::VECTOR) {
            const VectorKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.vector_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS,
                            "vector work attributes are invalid", error);
            attrsRef = attrs->semantic_attrs;
            opcode = attrs->graph_opcode;
        } else {
            const MovementKernelAttrs *attrs = row(
                operation.attrs,
                program.semantic_tables.movement_kernel_attrs_rows);
            if (!attrs)
                return fail(E_ABI_BOUNDS,
                            "movement work attributes are invalid", error);
            attrsRef = attrs->semantic_attrs;
            opcode = attrs->graph_opcode;
        }
        if (opcode != computation->opcode || operation.writes.count == 0)
            return fail(E_ABI_BOUNDS,
                        "element work domain is invalid", error);
        const GeometryAccessFact *write = geometry.access(
            operation.op_id, GeometryAccessRole::Write, 0);
        if (!write)
            return fail(E_ABI_BOUNDS,
                        "element work region is invalid", error);
        return estimateElementWork(
            program, opcode, attrsRef, resultDtype,
            write->logicalElementCount(), phases, error);
    }
    if (operation.opcode == KernelOpcode::REDUCE ||
        operation.opcode == KernelOpcode::SOFTMAX ||
        operation.opcode == KernelOpcode::NORM) {
        SemanticRef attrsRef;
        ListSpan axesSpan;
        OpCode opcode{};
        if (operation.opcode == KernelOpcode::REDUCE) {
            const ReductionKernelAttrs *kernelAttrs = row(
                operation.attrs,
                program.semantic_tables.reduction_kernel_attrs_rows);
            const ReduceAttrs *attrs = kernelAttrs ? row(
                kernelAttrs->semantic_attrs,
                program.semantic_tables.reduce_attrs_rows) : nullptr;
            if (!attrs)
                return fail(E_ABI_BOUNDS,
                            "reduction work attributes are invalid", error);
            attrsRef = kernelAttrs->semantic_attrs;
            axesSpan = attrs->axes;
            opcode = kernelAttrs->graph_opcode;
        } else if (operation.opcode == KernelOpcode::SOFTMAX) {
            const SoftmaxKernelAttrs *kernelAttrs = row(
                operation.attrs,
                program.semantic_tables.softmax_kernel_attrs_rows);
            const SoftmaxAttrs *attrs = kernelAttrs ? row(
                kernelAttrs->semantic_attrs,
                program.semantic_tables.softmax_attrs_rows) : nullptr;
            if (!attrs || attrs->axis > INT64_MAX)
                return fail(E_ABI_BOUNDS,
                            "softmax work attributes are invalid", error);
            attrsRef = kernelAttrs->semantic_attrs;
            opcode = OpCode::SOFTMAX;
            axesSpan = {};
        } else {
            const NormKernelAttrs *kernelAttrs = row(
                operation.attrs,
                program.semantic_tables.norm_kernel_attrs_rows);
            const NormAttrs *attrs = kernelAttrs ? row(
                kernelAttrs->semantic_attrs,
                program.semantic_tables.norm_attrs_rows) : nullptr;
            if (!attrs)
                return fail(E_ABI_BOUNDS,
                            "normalization work attributes are invalid", error);
            attrsRef = kernelAttrs->semantic_attrs;
            axesSpan = attrs->axes;
            opcode = kernelAttrs->graph_opcode;
        }
        if (opcode != computation->opcode)
            return fail(E_ABI_BOUNDS,
                        "row work opcode is invalid", error);
        std::vector<uint64_t> axes;
        if (operation.opcode == KernelOpcode::SOFTMAX) {
            const SoftmaxAttrs *attrs = row(
                attrsRef, program.semantic_tables.softmax_attrs_rows);
            axes.push_back(attrs->axis);
        } else {
            for (size_t index = 0; index < axesSpan.count; ++index)
                axes.push_back(program.semantic_u64_values[
                    axesSpan.begin + index]);
        }
        uint64_t rows = 0;
        uint64_t fanIn = 0;
        semantic_abi::DType inputDtype{};
        if (!rowDomain(
                program, operation, axes, geometry, rows, fanIn, inputDtype,
                error))
            return false;
        return estimateRowWork(
            program, opcode, attrsRef, inputDtype, rows, fanIn, phases,
            error);
    }
    return fail(E_EXPORT_UNSUPPORTED_OP,
                "compute work family is unsupported", error);
}

bool
executionMatches(
    const DecodedProgram &program, const ComputeExecution &execution,
    const std::vector<WorkPhase> &expected, MeshLoadError &error)
{
    if (execution.phases.count != expected.size())
        return projectionMatches(false, error);
    for (size_t phaseIndex = 0; phaseIndex < expected.size(); ++phaseIndex) {
        const ExecutionWorkPhase *phase = row(
            program.semantic_references[
                execution.phases.begin + phaseIndex],
            program.semantic_tables.execution_work_phase_rows);
        if (!phase || phase->work.count != expected[phaseIndex].work.size())
            return projectionMatches(false, error);
        for (size_t workIndex = 0;
             workIndex < expected[phaseIndex].work.size(); ++workIndex) {
            const WorkEstimate *actual = row(
                program.semantic_references[
                    phase->work.begin + workIndex],
                program.semantic_tables.work_estimate_rows);
            const WorkEstimate &wanted = expected[phaseIndex].work[workIndex];
            if (!actual)
                return projectionMatches(false, error);
            if (actual->engine != wanted.engine)
                return projectionMatches(false, error);
            if (actual->unit != wanted.unit || actual->dtype != wanted.dtype ||
                actual->operations != wanted.operations)
                return projectionMatches(false, error);
        }
    }
    return true;
}

bool
byteCostMatches(
    const DecodedProgram &program, const KernelOp &operation,
    const ComputeProjection &projection, const VerifiedProgramGeometry &geometry,
    MeshLoadError &error)
{
    if (projection.cost.row_id == 0)
        return true;
    const KernelCost *cost = row(
        projection.cost, program.semantic_tables.kernel_cost_rows);
    if (!cost)
        return fail(E_ABI_BOUNDS, "compute cost reference is invalid", error);
    const auto matchesDeclaredBytes = [&](GeometryAccessRole role,
                                          uint64_t declared) {
        uint64_t remaining = declared;
        const size_t count = geometry.accessCount(operation.op_id, role);
        for (size_t ordinal = 0; ordinal < count; ++ordinal) {
            uint64_t bytes = 0;
            if (!geometry.accessByteCount(
                    operation.op_id, role, ordinal, bytes, error))
                return false;
            if (bytes > remaining)
                return fail(E_EXPORT_LAYOUT,
                            "kernel byte cost is inconsistent", error);
            remaining -= bytes;
        }
        return remaining == 0 || fail(
            E_EXPORT_LAYOUT, "kernel byte cost is inconsistent", error);
    };
    if (!matchesDeclaredBytes(
            GeometryAccessRole::Read, cost->logical_input_bytes) ||
        !matchesDeclaredBytes(
            GeometryAccessRole::Write, cost->logical_output_bytes))
        return false;
    if (cost->logical_input_bytes >
        UINT64_MAX - cost->logical_output_bytes)
        return fail(E_ABI_OVERFLOW, "unsigned 64-bit addition overflowed",
                    error);
    return cost->local_storage_bytes ==
            cost->logical_input_bytes + cost->logical_output_bytes ||
        fail(E_EXPORT_LAYOUT, "kernel byte cost is inconsistent", error);
}

bool
costMatches(
    const DecodedProgram &program, const ComputeProjection &projection,
    const std::vector<WorkPhase> &phases, MeshLoadError &error)
{
    if (projection.cost.row_id == 0)
        return true;
    const KernelCost *cost = row(
        projection.cost, program.semantic_tables.kernel_cost_rows);
    if (!cost)
        return fail(E_ABI_BOUNDS, "compute cost reference is invalid", error);
    std::array<uint64_t, 3> totals{};
    for (const WorkPhase &phase : phases) {
        for (const WorkEstimate &work : phase.work) {
            const size_t index =
                work.engine == semantic_abi::Engine::TENSOR ? 0 :
                work.engine == semantic_abi::Engine::VECTOR ? 1 : 2;
            if (!checkedAdd(
                    totals[index], work.operations, totals[index], error))
                return false;
        }
    }
    return (totals[0] == cost->macs &&
        totals[1] == cost->vector_ops &&
        totals[2] == cost->reduction_ops) ||
        fail(E_EXPORT_LAYOUT,
             "compute work cost differs from physical execution phases",
             error);
}

bool
workCapabilitiesMatch(
    const std::vector<WorkPhase> &phases, const RuntimeArch &arch,
    MeshLoadError &error)
{
    for (const WorkPhase &phase : phases) {
        for (const WorkEstimate &work : phase.work) {
            const uint32_t dtype = uint32_t(work.dtype);
            if (!validDType(dtype))
                return fail(E_ABI_ENUM,
                            "compute work dtype is invalid", error);
            uint32_t mask = 0;
            switch (work.engine) {
              case semantic_abi::Engine::TENSOR:
                mask = arch.tensor_dtype_mask;
                break;
              case semantic_abi::Engine::VECTOR:
                mask = arch.vector_dtype_mask;
                break;
              case semantic_abi::Engine::REDUCE:
                mask = arch.reduce_dtype_mask;
                break;
              default:
                return fail(E_ABI_ENUM,
                            "compute work engine is invalid", error);
            }
            if (!(mask >> (dtype - 1) & 1u))
                return fail(E_CAPABILITY_MISMATCH,
                            "architecture lacks compute dtype throughput",
                            error);
        }
    }
    return true;
}

}
bool
verifyProgramComputeDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    MeshLoadError &error)
{
    if (!context.matches(program) || !geometry.matches(program, context))
        return fail(E_ABI_BOUNDS, "semantic context belongs to another Program",
                    error);
    const auto &operations = context.operations();
    const auto &commandSemantics = context.commandSemantics();
    const auto &computations = context.computations();
    const auto &tensors = context.tensors();
    const auto &shards = context.logicalShards();
    const auto &views = context.views();
    const auto &commands = context.commands();
    for (const auto &[commandId, semantic] : commandSemantics) {
        const KernelCommandSource *source = row(
            semantic->source,
            program.semantic_tables.kernel_command_source_rows);
        if (!source)
            continue;
        auto operationIt = operations.find(source->kernel_op_id);
        auto commandIt = commands.find(uint32_t(commandId));
        if (operationIt == operations.end() || commandId > UINT32_MAX ||
            commandIt == commands.end())
            return fail(E_ABI_BOUNDS,
                        "Kernel command source is invalid", error);
        const KernelOp &operation = *operationIt->second;
        ComputeProjection projection;
        if (!computeProjection(program, operation, projection)) {
            if (isComputeKernelOpcode(operation.opcode))
                return fail(E_EXPORT_UNSUPPORTED_OP,
                            "compute attribute family is invalid", error);
            continue;
        }
        const ComputeExecution *execution = row(
            semantic->execution,
            program.semantic_tables.compute_execution_rows);
        if (semantic->execution.section_type !=
            SemanticRecordTraits<ComputeExecution>::section_type)
            return fail(E_ABI_ENUM,
                        "compute execution kind is invalid", error);
        if (!execution)
            return fail(E_ABI_BOUNDS,
                        "compute execution is invalid", error);
        const Command &command = *commandIt->second;
        if (command.opcode != projection.opcode)
            return fail(E_ABI_ENUM,
                        "compute opcode projection is invalid", error);
        if (command.attr_index == 0 ||
            command.attr_index > program.transport.op_attrs.size())
            return fail(E_ABI_ENUM,
                        "compute attribute reference is invalid", error);
        const TypedOpAttr &attr =
            program.transport.op_attrs[command.attr_index - 1];
        const KernelComputation *computation = nullptr;
        auto computationIt = computations.find(operation.computation_id);
        if (computationIt != computations.end())
            computation = computationIt->second;
        if (operation.opcode != KernelOpcode::LOCAL_REDUCE &&
            operation.opcode != KernelOpcode::LOCAL_COPY) {
            if (!computation)
                return fail(E_ABI_BOUNDS,
                            "compute operation identity is invalid", error);
        }
        if (operation.reads.count != 0 || operation.writes.count != 0) {
            std::vector<WorkPhase> phases;
            if (!byteCostMatches(
                    program, operation, projection, geometry, error) ||
                !expectedWorkPhases(
                    program, operation, computation, geometry, tensors,
                    phases, error) ||
                !costMatches(program, projection, phases, error) ||
                !workCapabilitiesMatch(phases, arch, error) ||
                !executionMatches(program, *execution, phases, error))
                return false;
            const uint16_t expectedEngine = phases.empty() ? kEngineCONTROL :
                uint16_t(phases.front().work.front().engine);
            if (command.engine != expectedEngine)
                return fail(E_ENGINE_MISMATCH,
                            "compute engine projection is invalid", error);
            if (!computeAttributeMatches(
                    program, operation, projection, attr, views, shards,
                    tensors, nullptr, nullptr, error))
                return false;
            continue;
        }
        auto shardIt = shards.find(operation.result_shard_id);
        if (execution->phases.count != 0 ||
            operation.done_token == 0 || !computation ||
            shardIt == shards.end())
            return fail(E_ABI_BOUNDS,
                        "empty compute identity or execution is invalid", error);
        auto tensorIt = tensors.find(shardIt->second->tensor_id);
        if (tensorIt == tensors.end() ||
            tensorIt->second->tensor_id !=
                computation->result_tensor_id ||
            shardIt->second->owner_core != operation.owner_core)
            return fail(E_EXPORT_LAYOUT,
                        "empty compute target is invalid", error);
        bool emptyShard = false;
        const size_t shapeEnd = size_t(shardIt->second->valid_shape.begin) +
            shardIt->second->valid_shape.count;
        for (size_t index = shardIt->second->valid_shape.begin;
             index < shapeEnd; ++index)
            emptyShard |= program.semantic_u64_values[index] == 0;
        const KernelTile *tile = row(
            projection.tile, program.semantic_tables.kernel_tile_rows);
        const KernelCost *cost = row(
            projection.cost, program.semantic_tables.kernel_cost_rows);
        if (!emptyShard)
            return fail(E_EXPORT_LAYOUT,
                        "empty compute shard is not empty", error);
        if (!tile || !cost ||
            (tile->valid_batch != 0 && tile->valid_m != 0 &&
             tile->valid_n != 0) ||
            cost->logical_input_bytes != 0 ||
            cost->logical_output_bytes != 0 ||
            cost->local_storage_bytes != 0 || cost->macs != 0 ||
            cost->vector_ops != 0 || cost->reduction_ops != 0)
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "empty compute work proof is invalid", error);
        if (command.engine != kEngineCONTROL)
            return fail(E_ENGINE_MISMATCH,
                        "empty compute engine projection is invalid", error);
        if (command.operand_count != 0 || command.signal_event == 0)
            return fail(E_ABI_ENUM,
                        "empty compute transport projection is invalid", error);
        if (!computeAttributeMatches(
                program, operation, projection, attr, views, shards,
                tensors, computation, tensorIt->second, error))
            return false;
    }
    return true;
}

}
}

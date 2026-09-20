#include "dev/ai_mesh/mesh_ir_logical_computation_verifier.hh"

#include "dev/ai_mesh/mesh_ir_dtype.hh"

#include <algorithm>
#include <boost/multiprecision/cpp_int.hpp>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <string>
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

const std::string *
semanticString(const DecodedProgram &program, const StringRef &reference)
{
    if (reference.string_id == 0 ||
        reference.string_id > program.semantic_strings.size())
        return nullptr;
    return &program.semantic_strings[reference.string_id - 1];
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
spanReferences(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<SemanticRef> &values)
{
    if (span.begin > program.semantic_references.size() ||
        span.count > program.semantic_references.size() - span.begin)
        return false;
    const auto begin = program.semantic_references.begin() + span.begin;
    values.assign(begin, begin + span.count);
    return true;
}

struct SliceBound
{
    bool negative = false;
    uint64_t magnitude = 0;
};

bool
spanSliceBounds(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<SliceBound> &values)
{
    if (span.begin > program.semantic_integer_values.size() ||
        span.count > program.semantic_integer_values.size() - span.begin)
        return false;
    values.clear();
    values.reserve(span.count);
    for (uint64_t offset = 0; offset < span.count; ++offset) {
        const SemanticIntegerValue &value = program.semantic_integer_values[
            size_t(span.begin + offset)];
        if (value.kind == semantic_abi::kScalarKindI64) {
            int64_t decoded = 0;
            std::memcpy(&decoded, &value.payload, sizeof(decoded));
            if (decoded >= 0)
                return false;
            values.push_back({true, uint64_t(-(decoded + 1)) + 1});
        } else if (value.kind == semantic_abi::kScalarKindU64) {
            values.push_back({false, value.payload});
        } else {
            return false;
        }
    }
    return true;
}

struct DimensionValue
{
    std::optional<boost::multiprecision::cpp_int> constant;
    bool nonConstant = false;
};

DimensionValue
addDimensions(DimensionValue lhs, const DimensionValue &rhs)
{
    if (rhs.constant) {
        const boost::multiprecision::cpp_int sum =
            lhs.constant.value_or(0) + *rhs.constant;
        if (sum == 0)
            lhs.constant.reset();
        else
            lhs.constant = sum;
    }
    lhs.nonConstant = lhs.nonConstant || rhs.nonConstant;
    return lhs;
}

DimensionValue
scaleDimension(const DimensionValue &value, uint64_t factor)
{
    if (factor == 0)
        return {};
    DimensionValue scaled;
    if (value.constant) {
        const boost::multiprecision::cpp_int product = *value.constant * factor;
        if (product != 0)
            scaled.constant = product;
    }
    scaled.nonConstant = value.nonConstant;
    return scaled;
}

bool
validSymbol(
    const DecodedProgram &program, const Symbol &value,
    MeshLoadError &error)
{
    const std::string *name = semanticString(program, value.name);
    if (value.symbol_id == 0 ||
        value.symbol_id > std::numeric_limits<uint32_t>::max() || !name ||
        name->empty()) {
        return fail(E_CONFIG, "symbol identity is invalid", error);
    }
    if (value.minimum > value.maximum || value.multiple_of == 0) {
        return fail(E_CONFIG, "symbol bounds are invalid", error);
    }
    const uint64_t remainder = value.minimum % value.multiple_of;
    const uint64_t increment = remainder == 0 ? 0 :
        value.multiple_of - remainder;
    if (increment > value.maximum - value.minimum) {
        return fail(E_CONFIG, "symbol bounds are invalid", error);
    }
    return true;
}

bool
dimensionPolynomial(
    const DecodedProgram &program, const SemanticRef &reference,
    std::set<std::pair<uint16_t, uint32_t>> &active, DimensionValue &out,
    MeshLoadError &error)
{
    const std::pair<uint16_t, uint32_t> identity{
        reference.section_type, reference.row_id};
    if (!active.insert(identity).second)
        return false;
    auto finish = [&active, &identity](bool result) {
        active.erase(identity);
        return result;
    };
    if (const Const *value = row(reference, program.semantic_tables.const_rows)) {
        out.constant = value->value;
        return finish(true);
    }
    if (const Symbol *value = row(
            reference, program.semantic_tables.symbol_rows)) {
        if (!validSymbol(program, *value, error))
            return finish(false);
        out.nonConstant = true;
        return finish(true);
    }
    if (const Add *value = row(reference, program.semantic_tables.add_rows)) {
        DimensionValue lhs;
        DimensionValue rhs;
        if (!dimensionPolynomial(program, value->lhs, active, lhs, error) ||
            !dimensionPolynomial(program, value->rhs, active, rhs, error))
            return finish(false);
        out = addDimensions(std::move(lhs), rhs);
        return finish(true);
    }
    if (const MulByConst *value = row(
            reference, program.semantic_tables.mul_by_const_rows)) {
        DimensionValue input;
        if (!dimensionPolynomial(program, value->value, active, input, error))
            return finish(false);
        out = scaleDimension(input, value->factor);
        return finish(true);
    }
    const FloorDivByConst *floor = row(
        reference, program.semantic_tables.floor_div_by_const_rows);
    const CeilDivByConst *ceil = floor ? nullptr : row(
        reference, program.semantic_tables.ceil_div_by_const_rows);
    if (!floor && !ceil)
        return finish(false);
    const SemanticRef inputRef = floor ? floor->value : ceil->value;
    const uint64_t divisor = floor ? floor->divisor : ceil->divisor;
    if (divisor == 0) {
        fail(E_CONFIG, "dimension divisor is invalid", error);
        return finish(false);
    }
    DimensionValue input;
    if (!dimensionPolynomial(program, inputRef, active, input, error))
        return finish(false);
    if (ceil)
        input = addDimensions(
            std::move(input), DimensionValue{divisor - 1, false});
    if (!input.nonConstant) {
        out.constant = input.constant.value_or(0) / divisor;
        return finish(true);
    }
    out.nonConstant = true;
    return finish(true);
}

bool
dimensionsMatch(
    const DecodedProgram &program, const ListSpan &span,
    const std::vector<uint64_t> &dimensions, MeshLoadError &error)
{
    error = {};
    std::vector<SemanticRef> references;
    if (!spanReferences(program, span, references) ||
        references.size() != dimensions.size())
        return false;
    std::set<std::pair<uint16_t, uint32_t>> active;
    for (size_t index = 0; index < references.size(); ++index) {
        DimensionValue actual;
        if (!dimensionPolynomial(
                program, references[index], active, actual, error) ||
            actual.nonConstant || !actual.constant ||
            *actual.constant != dimensions[index])
            return false;
    }
    return true;
}

struct TensorContract
{
    const KernelTensor *tensor = nullptr;
    std::vector<uint64_t> shape;
    std::vector<uint64_t> strides;
};

bool
tensorContract(
    const DecodedProgram &program, const KernelTensor *tensor,
    TensorContract &out)
{
    if (!tensor || !spanValues(program, tensor->shape, out.shape) ||
        !spanValues(program, tensor->strides, out.strides) ||
        out.shape.size() != out.strides.size())
        return false;
    out.tensor = tensor;
    return true;
}

bool
sameShape(const std::vector<uint64_t> &lhs, const std::vector<uint64_t> &rhs)
{
    return lhs == rhs;
}

bool
checkedProduct(const std::vector<uint64_t> &values, uint64_t &out)
{
    out = 1;
    for (const uint64_t value : values) {
        if (value != 0 && out > std::numeric_limits<uint64_t>::max() / value)
            return false;
        out *= value;
    }
    return true;
}

bool
isFloatDtype(DType dtype)
{
    return dtype == DType::FP32 || dtype == DType::FP16 ||
        dtype == DType::BF16;
}

DType
promoteTensorDtype(DType lhs, DType rhs, bool division)
{
    if (lhs == rhs)
        return division && (lhs == DType::INT8 || lhs == DType::INT32) ?
            DType::FP32 : lhs;
    if (lhs == DType::FP32 || rhs == DType::FP32 ||
        ((lhs == DType::FP16 && rhs == DType::BF16) ||
         (lhs == DType::BF16 && rhs == DType::FP16)))
        return DType::FP32;
    if (lhs == DType::FP16 || rhs == DType::FP16)
        return DType::FP16;
    if (lhs == DType::BF16 || rhs == DType::BF16)
        return DType::BF16;
    return division ? DType::FP32 : DType::INT32;
}

bool
stringEquals(
    const DecodedProgram &program, const StringRef &reference,
    const char *value)
{
    const std::string *actual = semanticString(program, reference);
    return actual && *actual == value;
}

bool
hasScalar(const ElementwiseAttrs &attrs)
{
    return (attrs.presence_mask &
            kElementwiseAttrsScalarField.optional_presence_mask) != 0;
}

bool
validScalar(const ScalarValue &value)
{
    constexpr uint64_t jsonSafeInteger = (uint64_t(1) << 53) - 1;
    if (value.kind == semantic_abi::kScalarKindBool ||
        value.kind == semantic_abi::kScalarKindF64)
        return true;
    if (value.kind == semantic_abi::kScalarKindU64)
        return value.payload <= jsonSafeInteger;
    if (value.kind != semantic_abi::kScalarKindI64)
        return false;
    int64_t decoded = 0;
    std::memcpy(&decoded, &value.payload, sizeof(decoded));
    return decoded >= -int64_t(jsonSafeInteger) &&
        decoded <= int64_t(jsonSafeInteger);
}

bool
broadcastShape(
    const std::vector<uint64_t> &lhs, const std::vector<uint64_t> &rhs,
    std::vector<uint64_t> &out)
{
    out.clear();
    const size_t count = std::max(lhs.size(), rhs.size());
    out.reserve(count);
    for (size_t offset = 0; offset < count; ++offset) {
        const uint64_t left = offset < lhs.size() ?
            lhs[lhs.size() - 1 - offset] : 1;
        const uint64_t right = offset < rhs.size() ?
            rhs[rhs.size() - 1 - offset] : 1;
        if (left == right || left == 1)
            out.push_back(right);
        else if (right == 1)
            out.push_back(left);
        else
            return false;
    }
    std::reverse(out.begin(), out.end());
    return true;
}

bool
canonicalAxes(
    const std::vector<uint64_t> &axes, size_t rank,
    std::vector<size_t> &out)
{
    if (axes.empty())
        return false;
    out.clear();
    out.reserve(axes.size());
    for (const uint64_t axis : axes) {
        if (axis >= rank || std::find(out.begin(), out.end(), size_t(axis)) !=
                out.end())
            return false;
        out.push_back(size_t(axis));
    }
    return true;
}

bool
normalizedAxis(int64_t axis, size_t rank, size_t &out)
{
    if (rank > size_t(std::numeric_limits<int64_t>::max()) ||
        axis < -int64_t(rank) || axis >= int64_t(rank))
        return false;
    out = size_t(axis < 0 ? axis + int64_t(rank) : axis);
    return true;
}

bool
checkMatmulContract(
    const DecodedProgram &program, OpCode opcode, const MatmulAttrs &attrs,
    const std::vector<TensorContract> &operands,
    const TensorContract &result, bool accumulation, MeshLoadError &error)
{
    const size_t expectedOperands = opcode == OpCode::LINEAR_BIAS &&
            !accumulation ? 3u : 2u;
    if (operands.size() != expectedOperands)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation operand arity is invalid", error);
    if (!std::isfinite(attrs.alpha) || !std::isfinite(attrs.beta) ||
        !validDType(uint32_t(attrs.accum_dtype)))
        return fail(E_EXPORT_DTYPE, "matmul attributes are invalid", error);
    const DType lhsDtype = operands[0].tensor->dtype;
    if (operands[1].tensor->dtype != lhsDtype ||
        attrs.accum_dtype != computationAccumulationDtype(lhsDtype) ||
        result.tensor->dtype !=
            (accumulation ? attrs.accum_dtype : lhsDtype) ||
        (opcode == OpCode::LINEAR_BIAS && !accumulation &&
         operands[2].tensor->dtype != result.tensor->dtype))
        return fail(E_EXPORT_DTYPE,
                    "matrix operand or result dtype is inconsistent", error);
    if (opcode != OpCode::LINEAR_BIAS && attrs.beta != 1.0)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "matrix beta is unsupported", error);
    std::vector<uint64_t> batchAxes;
    if (!spanValues(program, attrs.batch_axes, batchAxes) ||
        operands[0].shape.size() < 2 || operands[1].shape.size() < 2)
        return fail(E_EXPORT_LAYOUT,
                    "matrix operands require rank at least two", error);
    std::vector<uint64_t> lhs = operands[0].shape;
    std::vector<uint64_t> rhs = operands[1].shape;
    if (attrs.lhs_transpose)
        std::swap(lhs[lhs.size() - 2], lhs[lhs.size() - 1]);
    if (attrs.rhs_transpose)
        std::swap(rhs[rhs.size() - 2], rhs[rhs.size() - 1]);
    if (opcode == OpCode::BMM &&
        (lhs.size() != 3 || rhs.size() != 3 || result.shape.size() != 3 ||
         batchAxes != std::vector<uint64_t>{0}))
        return fail(E_EXPORT_LAYOUT,
                    "BMM requires exact rank-three batch semantics", error);
    if (opcode == OpCode::BMM && lhs[0] != rhs[0])
        return fail(E_EXPORT_LAYOUT,
                    "BMM batch dimensions differ", error);
    size_t lhsAxis = 0;
    size_t rhsAxis = 0;
    if (!normalizedAxis(attrs.lhs_contract_axis, lhs.size(), lhsAxis) ||
        !normalizedAxis(attrs.rhs_contract_axis, rhs.size(), rhsAxis))
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction axis is invalid", error);
    if (lhsAxis != lhs.size() - 1 || rhsAxis != rhs.size() - 2)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "matrix contraction axes are unsupported", error);
    if (lhs[lhsAxis] != rhs[rhsAxis])
        return fail(E_EXPORT_LAYOUT,
                    "matrix contraction dimensions differ", error);
    std::vector<uint64_t> lhsBatch(lhs.begin(), lhs.end() - 2);
    std::vector<uint64_t> rhsBatch(rhs.begin(), rhs.end() - 2);
    std::vector<uint64_t> expected;
    if (!broadcastShape(lhsBatch, rhsBatch, expected))
        return fail(E_EXPORT_LAYOUT,
                    "matrix batch dimensions do not broadcast", error);
    expected.push_back(lhs[lhs.size() - 2]);
    expected.push_back(rhs[rhs.size() - 1]);
    if (!sameShape(result.shape, expected))
        return fail(E_EXPORT_LAYOUT,
                    "matrix result shape is inconsistent", error);
    std::vector<uint64_t> expectedAxes;
    for (size_t index = 0; index < expected.size() - 2; ++index) {
        if (index >= expected.size() - 2 - lhsBatch.size() &&
            index >= expected.size() - 2 - rhsBatch.size())
            expectedAxes.push_back(index);
    }
    if (batchAxes != expectedAxes)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "matrix batch axes are inconsistent", error);
    if (opcode == OpCode::LINEAR_BIAS && !accumulation) {
        std::vector<uint64_t> bias;
        if (!broadcastShape(operands[2].shape, result.shape, bias) ||
            !sameShape(bias, result.shape))
            return fail(E_EXPORT_LAYOUT,
                        "matrix bias shape is incompatible", error);
    }
    return true;
}

bool
checkElementwiseContract(
    const DecodedProgram &program, OpCode opcode,
    const ElementwiseAttrs &attrs, const std::vector<TensorContract> &operands,
    const TensorContract &result, MeshLoadError &error)
{
    if (!std::isfinite(attrs.alpha) ||
        (hasScalar(attrs) && !validScalar(attrs.scalar)) ||
        !semanticString(program, attrs.scalar_side) ||
        !semanticString(program, attrs.approximation))
        return fail(E_EXPORT_DTYPE,
                    "elementwise attributes are invalid", error);
    const bool binary = opcode == OpCode::ADD || opcode == OpCode::SUB ||
        opcode == OpCode::MUL || opcode == OpCode::DIV;
    const bool scalar = hasScalar(attrs);
    if (binary) {
        const size_t expected = scalar ? 1 : 2;
        if (operands.size() != expected)
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "operation operand arity is invalid", error);
        if (!stringEquals(program, attrs.scalar_side,
                          scalar ? "lhs" : "none") &&
            !(scalar && stringEquals(program, attrs.scalar_side, "rhs")))
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "elementwise scalar side is invalid", error);
        if (!stringEquals(program, attrs.approximation, "none") ||
            ((opcode == OpCode::MUL || opcode == OpCode::DIV) &&
             attrs.alpha != 1.0))
            return fail(E_EXPORT_UNSUPPORTED_OP,
                        "elementwise attributes are unsupported", error);
        if (opcode == OpCode::SUB && scalar &&
            attrs.scalar.kind == semantic_abi::kScalarKindBool)
            return fail(E_EXPORT_DTYPE,
                        "boolean subtraction is unsupported", error);
        const DType expectedDtype = scalar ?
            ((opcode == OpCode::DIV &&
              (operands[0].tensor->dtype == DType::INT8 ||
               operands[0].tensor->dtype == DType::INT32)) ||
             (attrs.scalar.kind == semantic_abi::kScalarKindF64 &&
              (operands[0].tensor->dtype == DType::INT8 ||
               operands[0].tensor->dtype == DType::INT32)) ?
                DType::FP32 : operands[0].tensor->dtype) :
            promoteTensorDtype(
                operands[0].tensor->dtype, operands[1].tensor->dtype,
                opcode == OpCode::DIV);
        if (result.tensor->dtype != expectedDtype ||
            ((expectedDtype == DType::INT8 || expectedDtype == DType::INT32) &&
             std::floor(attrs.alpha) != attrs.alpha))
            return fail(E_EXPORT_DTYPE,
                        "elementwise result dtype is inconsistent", error);
        std::vector<uint64_t> expectedShape;
        if (scalar)
            expectedShape = operands[0].shape;
        else if (!broadcastShape(
                     operands[0].shape, operands[1].shape, expectedShape))
            return fail(E_EXPORT_LAYOUT,
                        "tensor dimensions do not broadcast", error);
        return sameShape(result.shape, expectedShape) ||
            fail(E_EXPORT_LAYOUT,
                 "elementwise result shape is inconsistent", error);
    }
    if (operands.size() != 1 || scalar ||
        !stringEquals(program, attrs.scalar_side, "none") ||
        attrs.alpha != 1.0)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "unary operation attributes are invalid", error);
    if ((opcode == OpCode::GELU &&
         !(stringEquals(program, attrs.approximation, "none") ||
           stringEquals(program, attrs.approximation, "tanh"))) ||
        (opcode != OpCode::GELU &&
         !stringEquals(program, attrs.approximation, "none")))
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "unary approximation is unsupported", error);
    const DType inputDtype = operands[0].tensor->dtype;
    if ((opcode == OpCode::GELU || opcode == OpCode::SILU) &&
        !isFloatDtype(inputDtype))
        return fail(E_EXPORT_DTYPE,
                    "unary operation requires floating-point input", error);
    const DType expectedDtype =
        (opcode == OpCode::EXP || opcode == OpCode::RSQRT) &&
                (inputDtype == DType::INT8 || inputDtype == DType::INT32) ?
            DType::FP32 : inputDtype;
    if (result.tensor->dtype != expectedDtype)
        return fail(E_EXPORT_DTYPE,
                    "unary result dtype is inconsistent", error);
    return sameShape(result.shape, operands[0].shape) ||
        fail(E_EXPORT_LAYOUT, "unary result shape is inconsistent", error);
}

bool
viewUnused(const ViewAttrs &attrs, bool shape, bool permutation, bool axes,
           bool starts, bool ends, bool steps, bool expandedAxes)
{
    return (shape || attrs.shape.count == 0) &&
        (permutation || attrs.permutation.count == 0) &&
        (axes || attrs.axes.count == 0) &&
        (starts || attrs.starts.count == 0) &&
        (ends || attrs.ends.count == 0) &&
        (steps || attrs.steps.count == 0) &&
        (expandedAxes || attrs.expanded_axes.count == 0);
}

bool
reshapeChunks(
    const TensorContract &contract,
    std::vector<std::pair<uint64_t, uint64_t>> &chunks)
{
    chunks.clear();
    bool active = false;
    uint64_t product = 0;
    uint64_t unit = 0;
    uint64_t expected = 0;
    for (size_t offset = contract.shape.size(); offset > 0; --offset) {
        const uint64_t dimension = contract.shape[offset - 1];
        const uint64_t stride = contract.strides[offset - 1];
        if (dimension == 1)
            continue;
        if (!active || stride != expected) {
            if (active)
                chunks.emplace_back(product, unit);
            active = true;
            product = dimension;
            unit = stride;
        } else if (dimension != 0 &&
                   product > std::numeric_limits<uint64_t>::max() / dimension) {
            return false;
        } else {
            product *= dimension;
        }
        if (dimension != 0 &&
            stride > std::numeric_limits<uint64_t>::max() / dimension)
            return false;
        expected = stride * dimension;
    }
    if (active)
        chunks.emplace_back(product, unit);
    return true;
}

bool
rowMajorStrides(
    const std::vector<uint64_t> &shape, std::vector<uint64_t> &strides)
{
    strides.assign(shape.size(), 0);
    uint64_t stride = 1;
    for (size_t offset = shape.size(); offset > 0; --offset) {
        strides[offset - 1] = stride;
        if (shape[offset - 1] != 0 &&
            stride > std::numeric_limits<uint64_t>::max() /
                shape[offset - 1])
            return false;
        stride *= shape[offset - 1];
    }
    return true;
}

bool
nonOverlappingLayout(const TensorContract &contract)
{
    std::vector<size_t> axes;
    for (size_t index = 0; index < contract.shape.size(); ++index) {
        if (contract.shape[index] > 1 && contract.strides[index] != 0)
            axes.push_back(index);
    }
    std::sort(axes.begin(), axes.end(), [&contract](size_t left, size_t right) {
        return contract.strides[left] < contract.strides[right];
    });
    uint64_t span = 1;
    for (const size_t axis : axes) {
        const uint64_t stride = contract.strides[axis];
        const uint64_t extent = contract.shape[axis];
        if (stride < span ||
            extent - 1 > std::numeric_limits<uint64_t>::max() / stride)
            return false;
        const uint64_t contribution = (extent - 1) * stride;
        if (span > std::numeric_limits<uint64_t>::max() - contribution)
            return false;
        span += contribution;
    }
    return true;
}

bool
sliceIndices(
    SliceBound start, SliceBound end, uint64_t step, uint64_t dimension,
    uint64_t &first, uint64_t &count)
{
    if (step == 0)
        return false;
    const auto normalize = [dimension](SliceBound value) {
        if (value.negative)
            return value.magnitude >= dimension ? uint64_t(0) :
                dimension - value.magnitude;
        return std::min(value.magnitude, dimension);
    };
    const uint64_t normalizedStart = normalize(start);
    const uint64_t normalizedEnd = normalize(end);
    first = normalizedStart;
    if (normalizedEnd <= normalizedStart) {
        count = 0;
        return true;
    }
    count = 1 + uint64_t(normalizedEnd - normalizedStart - 1) / step;
    return true;
}

bool
checkViewContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const ViewAttrs *attrs = row(
        computation.attrs, program.semantic_tables.view_attrs_rows);
    if (!attrs)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    if (operands.size() != 1)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "view operation operand arity is invalid", error);
    if (result.tensor->dtype != operands[0].tensor->dtype)
        return fail(E_EXPORT_DTYPE,
                    "view or copy dtype contract is inconsistent", error);
    const TensorContract &input = operands[0];
    if (computation.opcode == OpCode::CONTIGUOUS_COPY) {
        std::vector<uint64_t> expectedStrides;
        if (!dimensionsMatch(program, attrs->shape, result.shape, error)) {
            return !error.code.empty() ? false :
                fail(E_EXPORT_LAYOUT,
                     "contiguous copy tensor contract is inconsistent", error);
        }
        if (!viewUnused(*attrs, true, false, false, false, false, false,
                        false) ||
            !sameShape(input.shape, result.shape) ||
            input.tensor->alias_root_tensor_id == result.tensor->alias_root_tensor_id ||
            result.tensor->alias_root_tensor_id != result.tensor->tensor_id ||
            result.tensor->storage_offset_elements != 0 ||
            !rowMajorStrides(result.shape, expectedStrides) ||
            !sameShape(result.strides, expectedStrides))
            return fail(E_EXPORT_LAYOUT,
                        "contiguous copy tensor contract is inconsistent",
                        error);
        return true;
    }
    if (input.tensor->alias_root_tensor_id != result.tensor->alias_root_tensor_id)
        return fail(E_EXPORT_LAYOUT,
                    "view operation must preserve its alias root", error);
    if (computation.opcode == OpCode::RESHAPE_VIEW) {
        std::vector<std::pair<uint64_t, uint64_t>> inputChunks;
        std::vector<std::pair<uint64_t, uint64_t>> resultChunks;
        uint64_t inputElements = 0;
        uint64_t resultElements = 0;
        const bool identicalLayout = sameShape(input.shape, result.shape) &&
            sameShape(input.strides, result.strides);
        if (!dimensionsMatch(program, attrs->shape, result.shape, error)) {
            return !error.code.empty() ? false :
                fail(E_EXPORT_LAYOUT, "reshape view metadata is inconsistent",
                     error);
        }
        if (!viewUnused(*attrs, true, false, false, false, false, false,
                        false) ||
            !checkedProduct(input.shape, inputElements) ||
            !checkedProduct(result.shape, resultElements) ||
            inputElements != resultElements ||
            input.tensor->storage_offset_elements !=
                result.tensor->storage_offset_elements ||
            !nonOverlappingLayout(result) ||
            (!identicalLayout &&
             (!reshapeChunks(input, inputChunks) ||
              !reshapeChunks(result, resultChunks) || inputChunks != resultChunks)))
            return fail(E_EXPORT_LAYOUT,
                        "reshape view metadata is inconsistent", error);
        return true;
    }
    if (computation.opcode == OpCode::TRANSPOSE_VIEW ||
        computation.opcode == OpCode::PERMUTE_VIEW) {
        std::vector<uint64_t> permutation;
        if (!viewUnused(*attrs, false, true, false, false, false, false,
                        false) ||
            !spanValues(program, attrs->permutation, permutation) ||
            permutation.size() != input.shape.size() ||
            result.shape.size() != input.shape.size() ||
            input.tensor->storage_offset_elements !=
                result.tensor->storage_offset_elements)
            return fail(E_EXPORT_LAYOUT,
                        "view permutation is invalid", error);
        std::vector<bool> seen(permutation.size(), false);
        size_t moved = 0;
        for (size_t index = 0; index < permutation.size(); ++index) {
            if (permutation[index] >= permutation.size() ||
                seen[permutation[index]])
                return fail(E_EXPORT_LAYOUT,
                            "view permutation is invalid", error);
            seen[permutation[index]] = true;
            moved += size_t(permutation[index] != index);
            if (result.shape[index] != input.shape[permutation[index]] ||
                result.strides[index] != input.strides[permutation[index]])
                return fail(E_EXPORT_LAYOUT,
                            "permutation view metadata is inconsistent", error);
        }
        if (computation.opcode == OpCode::TRANSPOSE_VIEW &&
            moved != 0 && moved != 2)
            return fail(E_EXPORT_LAYOUT,
                        "transpose must exchange exactly two axes", error);
        return true;
    }
    if (computation.opcode == OpCode::SLICE_VIEW) {
        std::vector<uint64_t> axes;
        std::vector<uint64_t> steps;
        std::vector<SliceBound> starts;
        std::vector<SliceBound> ends;
        if (!viewUnused(*attrs, false, false, true, true, true, true,
                        false) ||
            !spanValues(program, attrs->axes, axes) ||
            !spanValues(program, attrs->steps, steps) ||
            !spanSliceBounds(program, attrs->starts, starts) ||
            !spanSliceBounds(program, attrs->ends, ends) || axes.empty() ||
            axes.size() != starts.size() || axes.size() != ends.size() ||
            axes.size() != steps.size())
            return fail(E_EXPORT_LAYOUT,
                        "slice attributes are incomplete", error);
        std::vector<uint64_t> shape = input.shape;
        std::vector<uint64_t> strides = input.strides;
        uint64_t offset = input.tensor->storage_offset_elements;
        std::set<uint64_t> seen;
        for (size_t index = 0; index < axes.size(); ++index) {
            const uint64_t axis = axes[index];
            uint64_t first = 0;
            uint64_t count = 0;
            if (axis >= input.shape.size() || !seen.insert(axis).second ||
                !sliceIndices(starts[index], ends[index], steps[index],
                              input.shape[axis], first, count))
                return fail(E_EXPORT_LAYOUT,
                            "slice axes or steps are invalid", error);
            if (first != 0 && input.strides[axis] >
                    std::numeric_limits<uint64_t>::max() / first)
                return fail(E_ABI_OVERFLOW,
                            "slice storage offset overflows", error);
            const uint64_t delta = input.strides[axis] * first;
            if (offset > std::numeric_limits<uint64_t>::max() - delta)
                return fail(E_ABI_OVERFLOW,
                            "slice storage offset overflows", error);
            if (input.strides[axis] >
                std::numeric_limits<uint64_t>::max() / steps[index])
                return fail(E_ABI_OVERFLOW, "slice stride overflows", error);
            offset += delta;
            shape[axis] = count;
            strides[axis] *= steps[index];
        }
        if (!sameShape(shape, result.shape) ||
            !sameShape(strides, result.strides) ||
            offset != result.tensor->storage_offset_elements)
            return fail(E_EXPORT_LAYOUT,
                        "slice result metadata is inconsistent", error);
        return true;
    }
    if (computation.opcode != OpCode::EXPAND_VIEW)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "view operation opcode is unsupported", error);
    std::vector<uint64_t> expandedAxes;
    if (!dimensionsMatch(program, attrs->shape, result.shape, error)) {
        return !error.code.empty() ? false :
            fail(E_EXPORT_LAYOUT, "expand view metadata is inconsistent", error);
    }
    if (!viewUnused(*attrs, true, false, false, false, false, false,
                    true) ||
        !spanValues(program, attrs->expanded_axes, expandedAxes) ||
        result.shape.size() < input.shape.size() ||
        input.tensor->storage_offset_elements !=
            result.tensor->storage_offset_elements)
        return fail(E_EXPORT_LAYOUT,
                    "expand view metadata is inconsistent", error);
    const size_t leading = result.shape.size() - input.shape.size();
    std::vector<uint64_t> expectedExpanded;
    for (size_t axis = 0; axis < result.shape.size(); ++axis) {
        if (axis < leading) {
            if (result.shape[axis] == 1)
                continue;
            expectedExpanded.push_back(axis);
            if (result.strides[axis] != 0)
                return fail(E_EXPORT_LAYOUT,
                            "expand stride is inconsistent", error);
            continue;
        }
        const size_t source = axis - leading;
        if (input.shape[source] == result.shape[axis]) {
            if (result.strides[axis] != input.strides[source])
                return fail(E_EXPORT_LAYOUT,
                            "expand stride is inconsistent", error);
        } else if (input.shape[source] == 1) {
            expectedExpanded.push_back(axis);
            if (result.strides[axis] != 0)
                return fail(E_EXPORT_LAYOUT,
                            "expand stride is inconsistent", error);
        } else {
            return fail(E_EXPORT_LAYOUT,
                        "expand changes a non-singleton dimension", error);
        }
    }
    if (!sameShape(expandedAxes, expectedExpanded) ||
        result.tensor->access != Access::READ_ONLY)
        return fail(E_EXPORT_LAYOUT,
                    "expand axes or access are inconsistent", error);
    return true;
}

bool
checkMovementContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const MovementAttrs *attrs = row(
        computation.attrs, program.semantic_tables.movement_attrs_rows);
    if (!attrs)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    const bool concat = computation.opcode == OpCode::CONCAT;
    if ((concat && operands.empty()) || (!concat && operands.size() != 2))
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation operand arity is invalid", error);
    if (!attrs->bounds_check)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "movement attributes are unsupported", error);
    if (attrs->axis >= operands[0].shape.size())
        return fail(E_EXPORT_LAYOUT, "movement axis is invalid", error);
    const size_t axis = size_t(attrs->axis);
    if (concat) {
        std::vector<uint64_t> expected = operands[0].shape;
        uint64_t extent = expected[axis];
        for (size_t index = 1; index < operands.size(); ++index) {
            if (operands[index].shape.size() != expected.size())
                return fail(E_EXPORT_LAYOUT,
                            "concat operand rank is inconsistent", error);
            if (operands[index].tensor->dtype != operands[0].tensor->dtype)
                return fail(E_EXPORT_DTYPE,
                            "concat dtype contract is inconsistent", error);
            for (size_t dimension = 0; dimension < expected.size();
                 ++dimension) {
                if (dimension != axis &&
                    operands[index].shape[dimension] != expected[dimension])
                    return fail(E_EXPORT_LAYOUT,
                                "concat non-axis dimensions differ", error);
            }
            if (extent > std::numeric_limits<uint64_t>::max() -
                    operands[index].shape[axis])
                return fail(E_ABI_OVERFLOW,
                            "concat dimension overflows", error);
            extent += operands[index].shape[axis];
        }
        expected[axis] = extent;
        if (result.tensor->dtype != operands[0].tensor->dtype)
            return fail(E_EXPORT_DTYPE,
                        "concat dtype contract is inconsistent", error);
        return sameShape(result.shape, expected) ||
            fail(E_EXPORT_LAYOUT, "concat result shape is inconsistent", error);
    }
    if (operands[1].shape.size() != 1)
        return fail(E_EXPORT_LAYOUT,
                    "gather indices must have rank one", error);
    if (operands[1].tensor->dtype != DType::INT32 ||
        result.tensor->dtype != operands[0].tensor->dtype)
        return fail(E_EXPORT_DTYPE,
                    "gather dtype contract is inconsistent", error);
    std::vector<uint64_t> expected = operands[0].shape;
    expected[axis] = operands[1].shape[0];
    return sameShape(result.shape, expected) ||
        fail(E_EXPORT_LAYOUT, "gather result shape is inconsistent", error);
}

bool
checkEmbeddingContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const EmbeddingAttrs *attrs = row(
        computation.attrs, program.semantic_tables.embedding_attrs_rows);
    if (!attrs || operands.size() != 2)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    if (operands[1].tensor->dtype != DType::INT32 ||
        result.tensor->dtype != operands[0].tensor->dtype)
        return fail(E_EXPORT_DTYPE,
                    "embedding dtype contract is inconsistent", error);
    if (!std::isfinite(attrs->norm_type) ||
        ((attrs->presence_mask & 8u) != 0) || attrs->scale_grad_by_freq ||
        attrs->sparse || attrs->norm_type <= 0)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "embedding options are unsupported", error);
    if (operands[0].shape.size() != 2)
        return fail(E_EXPORT_LAYOUT,
                    "embedding table must have rank two", error);
    std::vector<uint64_t> expected = operands[1].shape;
    expected.push_back(operands[0].shape[1]);
    if (!sameShape(result.shape, expected))
        return fail(E_EXPORT_LAYOUT,
                    "embedding result shape is inconsistent", error);
    const uint64_t rows = operands[0].shape[0];
    if (rows == 0 ||
        (rows <= uint64_t(std::numeric_limits<int64_t>::max()) &&
         (attrs->padding_idx < -int64_t(rows) ||
          attrs->padding_idx >= int64_t(rows))))
        return fail(E_EXPORT_LAYOUT,
                    "embedding padding index is out of range", error);
    return true;
}

bool
checkReduceContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const ReduceAttrs *attrs = row(
        computation.attrs, program.semantic_tables.reduce_attrs_rows);
    if (!attrs || operands.size() != 1)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    const DType input = operands[0].tensor->dtype;
    if (!validDType(uint32_t(attrs->output_dtype)) ||
        !validDType(uint32_t(attrs->accum_dtype)) ||
        !stringEquals(program, attrs->order, "left_to_right"))
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "reduction attributes are unsupported", error);
    if (attrs->output_dtype != result.tensor->dtype ||
        attrs->accum_dtype != computationAccumulationDtype(input) ||
        (computation.opcode == OpCode::REDUCE_MAX &&
         result.tensor->dtype != input) ||
        (computation.opcode == OpCode::REDUCE_MEAN && !isFloatDtype(input)) ||
        (result.tensor->dtype != input &&
         result.tensor->dtype != computationAccumulationDtype(input)))
        return fail(E_EXPORT_DTYPE,
                    "reduction dtype policy is inconsistent", error);
    std::vector<uint64_t> rawAxes;
    std::vector<size_t> axes;
    if (!spanValues(program, attrs->axes, rawAxes) ||
        !canonicalAxes(rawAxes, operands[0].shape.size(), axes))
        return fail(E_EXPORT_LAYOUT, "reduction axes are invalid", error);
    std::vector<uint64_t> expected;
    for (size_t index = 0; index < operands[0].shape.size(); ++index) {
        const bool reduced = std::find(axes.begin(), axes.end(), index) !=
            axes.end();
        if (attrs->keepdim)
            expected.push_back(reduced ? 1 : operands[0].shape[index]);
        else if (!reduced)
            expected.push_back(operands[0].shape[index]);
    }
    return sameShape(result.shape, expected) ||
        fail(E_EXPORT_LAYOUT, "reduction result shape is inconsistent", error);
}

bool
checkNormContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const NormAttrs *attrs = row(
        computation.attrs, program.semantic_tables.norm_attrs_rows);
    if (!attrs || !std::isfinite(attrs->epsilon))
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    const bool rms = computation.opcode == OpCode::RMSNORM;
    const size_t expectedCount = 1 + size_t(attrs->has_weight) +
        size_t(!rms && attrs->has_bias);
    if (operands.size() != expectedCount)
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation operand arity is invalid", error);
    const DType dtype = operands[0].tensor->dtype;
    if (!isFloatDtype(dtype) || result.tensor->dtype != dtype)
        return fail(E_EXPORT_DTYPE,
                    "normalization dtype policy is inconsistent", error);
    for (size_t index = 1; index < operands.size(); ++index) {
        if (operands[index].tensor->dtype != dtype)
            return fail(E_EXPORT_DTYPE,
                        "normalization dtype policy is inconsistent", error);
    }
    if (attrs->epsilon < 0)
        return fail(E_EXPORT_DTYPE,
                    "normalization epsilon is unsupported", error);
    if (rms && attrs->has_bias)
        return fail(E_EXPORT_UNSUPPORTED_OP, "RMSNorm cannot carry bias",
                    error);
    std::vector<uint64_t> rawAxes;
    std::vector<size_t> axes;
    if (!spanValues(program, attrs->axes, rawAxes) ||
        !canonicalAxes(rawAxes, operands[0].shape.size(), axes))
        return fail(E_EXPORT_LAYOUT,
                    "normalization axes are invalid", error);
    const size_t leading = operands[0].shape.size() - axes.size();
    for (size_t index = 0; index < axes.size(); ++index) {
        if (axes[index] != leading + index)
            return fail(E_EXPORT_LAYOUT,
                        "normalization axes must be trailing and ordered",
                        error);
    }
    std::vector<uint64_t> affine(
        operands[0].shape.begin() + leading, operands[0].shape.end());
    for (size_t index = 1; index < operands.size(); ++index) {
        if (!sameShape(operands[index].shape, affine))
            return fail(E_EXPORT_LAYOUT,
                        "normalization affine shape is inconsistent", error);
    }
    return sameShape(result.shape, operands[0].shape) ||
        fail(E_EXPORT_LAYOUT,
             "normalization result shape is inconsistent", error);
}

bool
checkSoftmaxContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    const SoftmaxAttrs *attrs = row(
        computation.attrs, program.semantic_tables.softmax_attrs_rows);
    if (!attrs || operands.size() != 1 ||
        !validDType(uint32_t(attrs->output_dtype)))
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation attribute contract is invalid", error);
    const DType input = operands[0].tensor->dtype;
    if (!isFloatDtype(input) || attrs->output_dtype != result.tensor->dtype ||
        (result.tensor->dtype != input && result.tensor->dtype != DType::FP32))
        return fail(E_EXPORT_DTYPE,
                    "softmax dtype policy is inconsistent", error);
    if (attrs->axis >= operands[0].shape.size())
        return fail(E_EXPORT_LAYOUT, "softmax axis is invalid", error);
    return sameShape(result.shape, operands[0].shape) ||
        fail(E_EXPORT_LAYOUT, "softmax result shape is inconsistent", error);
}

bool
checkComputationContract(
    const DecodedProgram &program, const KernelComputation &computation,
    const std::vector<TensorContract> &operands, const TensorContract &result,
    MeshLoadError &error)
{
    switch (computation.opcode) {
      case OpCode::MATMUL:
      case OpCode::BMM:
      case OpCode::LINEAR_BIAS: {
        const MatmulAttrs *attrs = row(
            computation.attrs, program.semantic_tables.matmul_attrs_rows);
        return attrs ? checkMatmulContract(
            program, computation.opcode, *attrs, operands, result, false,
            error) :
            fail(E_EXPORT_UNSUPPORTED_OP,
                 "operation attribute contract is invalid", error);
      }
      case OpCode::RESHAPE_VIEW:
      case OpCode::TRANSPOSE_VIEW:
      case OpCode::PERMUTE_VIEW:
      case OpCode::SLICE_VIEW:
      case OpCode::EXPAND_VIEW:
      case OpCode::CONTIGUOUS_COPY:
        return checkViewContract(
            program, computation, operands, result, error);
      case OpCode::CONCAT:
      case OpCode::GATHER_ROWS:
        return checkMovementContract(
            program, computation, operands, result, error);
      case OpCode::ADD:
      case OpCode::SUB:
      case OpCode::MUL:
      case OpCode::DIV:
      case OpCode::RELU:
      case OpCode::GELU:
      case OpCode::SILU:
      case OpCode::EXP:
      case OpCode::RSQRT: {
        const ElementwiseAttrs *attrs = row(
            computation.attrs, program.semantic_tables.elementwise_attrs_rows);
        return attrs ? checkElementwiseContract(
            program, computation.opcode, *attrs, operands, result, error) :
            fail(E_EXPORT_UNSUPPORTED_OP,
                 "operation attribute contract is invalid", error);
      }
      case OpCode::REDUCE_SUM:
      case OpCode::REDUCE_MAX:
      case OpCode::REDUCE_MEAN:
        return checkReduceContract(
            program, computation, operands, result, error);
      case OpCode::LAYERNORM:
      case OpCode::RMSNORM:
        return checkNormContract(
            program, computation, operands, result, error);
      case OpCode::SOFTMAX:
        return checkSoftmaxContract(
            program, computation, operands, result, error);
      case OpCode::EMBEDDING_LOOKUP:
        return checkEmbeddingContract(
            program, computation, operands, result, error);
      default:
        return fail(E_EXPORT_UNSUPPORTED_OP,
                    "operation opcode has no type contract", error);
    }
}

bool
checkAccumulationResultContract(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const KernelComputation &computation, const KernelTensor &result,
    MeshLoadError &error)
{
    if (computation.opcode != OpCode::MATMUL &&
        computation.opcode != OpCode::BMM &&
        computation.opcode != OpCode::LINEAR_BIAS)
        return fail(E_ABI_BOUNDS,
                    "matrix accumulator computation is invalid", error);
    const MatmulAttrs *attrs = row(
        computation.attrs, program.semantic_tables.matmul_attrs_rows);
    if (!attrs || computation.operand_tensor_ids.count < 2)
        return fail(E_ABI_BOUNDS, "matrix attributes are invalid", error);
    std::vector<TensorContract> operands;
    operands.reserve(2);
    for (size_t ordinal = 0; ordinal < 2; ++ordinal) {
        uint64_t tensorId = 0;
        if (!spanValue(program, computation.operand_tensor_ids, ordinal,
                       tensorId))
            return fail(E_ABI_BOUNDS,
                        "computation operand tensor list is invalid", error);
        const auto tensor = context.tensors().find(tensorId);
        TensorContract contract;
        if (tensor == context.tensors().end() ||
            !tensorContract(program, tensor->second, contract))
            return fail(E_ABI_BOUNDS,
                        "computation operand tensor is invalid", error);
        operands.push_back(std::move(contract));
    }
    TensorContract resultContract;
    if (!tensorContract(program, &result, resultContract))
        return fail(E_ABI_BOUNDS,
                    "matrix accumulator tensor is invalid", error);
    return checkMatmulContract(
        program, computation.opcode, *attrs, operands, resultContract, true,
        error);
}

bool
checkStableKeys(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    MeshLoadError &error)
{
    for (const auto &variantEntry : context.variants()) {
        const uint64_t variantId = variantEntry.first;
        const ProgramSemanticContext::MembershipRange *operations =
            context.membership(variantId, "kernel_ops");
        if (!operations)
            return fail(E_ABI_BOUNDS,
                        "Kernel operation membership is invalid", error);
        std::set<std::string> keys;
        for (uint64_t offset = 0; offset < operations->count; ++offset) {
            const auto operation = context.operations().find(
                operations->first + offset);
            const std::string *key = operation == context.operations().end() ?
                nullptr : semanticString(program, operation->second->stable_key);
            if (!key || key->empty())
                return fail(E_ABI_BOUNDS,
                            "Kernel operation stable key is invalid", error);
            if (!keys.insert(*key).second)
                return fail(E_ABI_ORDER,
                            "physical operation stable keys are not unique",
                            error);
        }
    }
    return true;
}

bool
checkLogicalComputations(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    MeshLoadError &error)
{
    for (const auto &entry : context.computations()) {
        const uint64_t computationId = entry.first;
        const KernelComputation &computation = *entry.second;
        const auto result = context.tensors().find(computation.result_tensor_id);
        if (result == context.tensors().end() ||
            result->second->producer_computation_id != computationId)
            return fail(E_ABI_BOUNDS,
                        "computation result producer identity differs", error);
        if (computation.operand_tensor_ids.begin >
                program.semantic_u64_values.size() ||
            computation.operand_tensor_ids.count >
                program.semantic_u64_values.size() -
                    computation.operand_tensor_ids.begin)
            return fail(E_ABI_BOUNDS,
                        "computation operand tensor list is invalid", error);
        std::vector<TensorContract> operands;
        operands.reserve(computation.operand_tensor_ids.count);
        for (uint64_t offset = 0;
             offset < computation.operand_tensor_ids.count; ++offset) {
            const uint64_t tensorId = program.semantic_u64_values[size_t(
                computation.operand_tensor_ids.begin + offset)];
            const auto tensor = context.tensors().find(tensorId);
            TensorContract contract;
            if (tensor == context.tensors().end() ||
                !tensorContract(program, tensor->second, contract))
                return fail(E_ABI_BOUNDS,
                            "computation operand tensor is invalid", error);
            operands.push_back(std::move(contract));
        }
        TensorContract resultContract;
        if (!tensorContract(program, result->second, resultContract))
            return fail(E_ABI_BOUNDS,
                        "computation result tensor is invalid", error);
        if (!checkComputationContract(
                program, computation, operands, resultContract, error))
            return false;
    }
    return true;
}

bool
checkComputationReferencesAndTensorPolicies(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    MeshLoadError &error)
{
    for (const auto &entry : context.operations()) {
        if (entry.second->computation_id != 0 &&
            context.computations().find(entry.second->computation_id) ==
                context.computations().end())
            return fail(E_ABI_BOUNDS,
                        "physical operation references an invalid computation",
                        error);
    }
    for (const auto &entry : context.tensors()) {
        const KernelTensor &tensor = *entry.second;
        const auto producer = context.computations().find(
            tensor.producer_computation_id);
        if (tensor.producer_computation_id != 0 &&
            producer == context.computations().end())
            return fail(E_ABI_BOUNDS,
                        "tensor producer computation is invalid", error);
        if (tensor.synthesized_purpose != SynthesizedTensorPurpose{} &&
            tensor.producer_computation_id == 0)
            return fail(E_ABI_BOUNDS,
                        "synthesized tensor requires a producer computation",
                        error);
        if (tensor.synthesized_purpose != SynthesizedTensorPurpose{} &&
            tensor.alias_root_tensor_id != tensor.tensor_id)
            return fail(E_EXPORT_LAYOUT,
                        "synthesized tensor must own its storage root", error);
        if (tensor.synthesized_purpose == SynthesizedTensorPurpose{} &&
            tensor.producer_computation_id != 0 &&
            producer->second->result_tensor_id != tensor.tensor_id)
            return fail(E_ABI_BOUNDS,
                        "logical tensor producer computation differs", error);
        if (tensor.content_sha256.string_id == 0)
            continue;
        const std::string *digest = semanticString(
            program, tensor.content_sha256);
        if (!digest || digest->size() != 64)
            return fail(E_ABI_CHECKSUM, "tensor content digest is invalid",
                        error);
        for (const char character : *digest) {
            if (!((character >= '0' && character <= '9') ||
                  (character >= 'a' && character <= 'f')))
                return fail(E_ABI_CHECKSUM,
                            "tensor content digest is invalid", error);
        }
    }
    return true;
}

}

bool
verifyMatrixAccumulatorTensorContract(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const KernelComputation &computation, const KernelTensor &result,
    MeshLoadError &error)
{
    return checkAccumulationResultContract(
        program, context, computation, result, error);
}

bool
verifyProgramLogicalComputationDomain(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    MeshLoadError &error)
{
    if (!context.matches(program))
        return fail(E_ABI_BOUNDS,
                    "logical computation facts belong to another verification invocation",
                    error);
    if (!checkLogicalComputations(program, context, error) ||
        !checkStableKeys(program, context, error) ||
        !checkComputationReferencesAndTensorPolicies(program, context, error))
        return false;
    error = {};
    return true;
}

}
}

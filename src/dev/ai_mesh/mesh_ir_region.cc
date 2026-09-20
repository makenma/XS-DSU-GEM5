#include "dev/ai_mesh/mesh_ir_region.hh"

#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <utility>

#include <isl/constraint.h>
#include <isl/ctx.h>
#include <isl/local_space.h>
#include <isl/map.h>
#include <isl/options.h>
#include <isl/set.h>
#include <isl/space.h>
#include <isl/val.h>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_kernel.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_abi;
using namespace mesh_abi::semantic_abi;
using namespace mesh_diagnostics;

constexpr uint64_t kInvalidCoreId = 0xffff;

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

bool
checkedAdd(uint64_t left, uint64_t right, uint64_t &out, MeshLoadError &error)
{
    if (left > std::numeric_limits<uint64_t>::max() - right)
        return fail(E_ABI_OVERFLOW, "geometry addition overflows", error);
    out = left + right;
    return true;
}

bool
checkedMul(uint64_t left, uint64_t right, uint64_t &out, MeshLoadError &error)
{
    if (left != 0 && right > std::numeric_limits<uint64_t>::max() / left)
        return fail(E_ABI_OVERFLOW, "geometry multiplication overflows", error);
    out = left * right;
    return true;
}

bool
product(
    const std::vector<uint64_t> &values, uint64_t &out, MeshLoadError &error)
{
    out = 1;
    for (const uint64_t value : values) {
        if (!checkedMul(out, value, out, error))
            return false;
    }
    return true;
}

bool
storageElements(
    const std::vector<uint64_t> &shape, const std::vector<uint64_t> &strides,
    uint64_t offset, uint64_t &out, MeshLoadError &error)
{
    if (std::any_of(shape.begin(), shape.end(),
                    [](uint64_t value) { return value == 0; })) {
        out = 0;
        return true;
    }
    if (!checkedAdd(offset, 1, out, error))
        return false;
    for (size_t index = 0; index < shape.size(); ++index) {
        uint64_t term = 0;
        if (!checkedMul(shape[index] - 1, strides[index], term, error) ||
            !checkedAdd(out, term, out, error)) {
            return false;
        }
    }
    return true;
}

template <typename Record>
const Record *
semanticRow(const SemanticRef &reference, const std::vector<Record> &rows)
{
    using Traits = SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > rows.size()) {
        return nullptr;
    }
    return &rows[reference.row_id - 1];
}

bool
spanValues(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<uint64_t> &out, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_u64_values.size()))
        return fail(E_ABI_BOUNDS, "geometry vector is invalid", error);
    out.assign(
        program.semantic_u64_values.begin() + size_t(span.begin),
        program.semantic_u64_values.begin() + size_t(span.begin + span.count));
    return true;
}

bool
sameOwner(
    const ProgramSemanticContext &context, const char *leftField,
    uint64_t leftId, const char *rightField, uint64_t rightId)
{
    const ProgramVariant *owner = context.owner(leftField, leftId);
    return owner && owner == context.owner(rightField, rightId);
}

bool
isPowerOfTwo(uint64_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

bool
rankMatches(
    const std::vector<uint64_t> &first, const std::vector<uint64_t> &second,
    MeshLoadError &error)
{
    return first.size() == second.size() ||
        fail(E_ABI_BOUNDS, "geometry ranks differ", error);
}

bool
layoutRankMatches(
    const std::vector<uint64_t> &first, const std::vector<uint64_t> &second,
    MeshLoadError &error)
{
    return first.size() == second.size() ||
        fail(E_EXPORT_LAYOUT, "geometry ranks differ", error);
}

bool
effectiveByteStride(
    uint64_t extent, uint64_t elementStride, uint64_t width, uint64_t &out,
    MeshLoadError &error)
{
    if (extent <= 1) {
        out = 0;
        return true;
    }
    return checkedMul(elementStride, width, out, error);
}

struct SourceStorageProjection
{
    uint64_t elementOffset = 0;
    std::vector<uint64_t> elementStrides;
};

bool
sourceStorageProjection(
    const std::vector<uint64_t> &origin, const std::vector<uint64_t> &shape,
    const std::vector<uint64_t> &steps,
    const std::vector<uint64_t> &viewStrides, uint64_t viewOffset,
    SourceStorageProjection &out, MeshLoadError &error)
{
    if (origin.size() != shape.size() || origin.size() != steps.size() ||
        origin.size() != viewStrides.size()) {
        return fail(E_ABI_BOUNDS, "geometry storage ranks differ", error);
    }
    SourceStorageProjection candidate;
    candidate.elementOffset = viewOffset;
    candidate.elementStrides.resize(origin.size());
    for (size_t index = 0; index < origin.size(); ++index) {
        uint64_t originTerm = 0;
        if (!checkedMul(origin[index], viewStrides[index], originTerm, error) ||
            !checkedAdd(
                candidate.elementOffset, originTerm,
                candidate.elementOffset, error) ||
            !checkedMul(
                steps[index], viewStrides[index],
                candidate.elementStrides[index], error)) {
            return false;
        }
    }
    out = std::move(candidate);
    return true;
}

struct EndpointStorageProjection
{
    uint64_t byteOffset = 0;
    std::vector<uint64_t> byteStrides;
    uint64_t byteEnd = 0;
};

bool
endpointStorageProjection(
    const SourceStorageProjection &source,
    const std::vector<uint64_t> &shape, uint64_t logicalElements,
    uint64_t width, EndpointStorageProjection &out, MeshLoadError &error)
{
    if (source.elementStrides.size() != shape.size()) {
        return fail(E_ABI_BOUNDS, "geometry endpoint ranks differ", error);
    }
    EndpointStorageProjection candidate;
    candidate.byteStrides.resize(shape.size());
    for (size_t index = 0; index < shape.size(); ++index) {
        if (!checkedMul(source.elementStrides[index], width,
                        candidate.byteStrides[index], error)) {
            return false;
        }
    }
    if (!checkedMul(source.elementOffset, width, candidate.byteOffset, error))
        return false;
    if (logicalElements == 0) {
        candidate.byteEnd = candidate.byteOffset;
        out = std::move(candidate);
        return true;
    }
    uint64_t span = width;
    for (size_t index = 0; index < shape.size(); ++index) {
        uint64_t displacement = 0;
        if (!checkedMul(shape[index] - 1, candidate.byteStrides[index],
                        displacement, error) ||
            !checkedAdd(span, displacement, span, error)) {
            return false;
        }
    }
    if (!checkedAdd(candidate.byteOffset, span, candidate.byteEnd, error))
        return false;
    out = std::move(candidate);
    return true;
}

bool
regionsOverlap(
    const TensorShard &first, const TensorShard &second,
    const DecodedProgram &program, bool &out, MeshLoadError &error)
{
    out = false;
    std::vector<uint64_t> firstOrigin;
    std::vector<uint64_t> firstShape;
    std::vector<uint64_t> secondOrigin;
    std::vector<uint64_t> secondShape;
    if (!spanValues(program, first.global_origin, firstOrigin, error) ||
        !spanValues(program, first.valid_shape, firstShape, error) ||
        !spanValues(program, second.global_origin, secondOrigin, error) ||
        !spanValues(program, second.valid_shape, secondShape, error)) {
        return false;
    }
    for (size_t index = 0; index < firstOrigin.size(); ++index) {
        uint64_t firstEnd = 0;
        uint64_t secondEnd = 0;
        if (!checkedAdd(firstOrigin[index], firstShape[index], firstEnd, error) ||
            !checkedAdd(
                secondOrigin[index], secondShape[index], secondEnd, error)) {
            return false;
        }
        if (!(firstOrigin[index] < secondEnd && secondOrigin[index] < firstEnd))
            return true;
    }
    out = true;
    return true;
}

isl_val *
value(isl_ctx *context, uint64_t input)
{
    const std::string decimal = std::to_string(input);
    return isl_val_read_from_str(context, decimal.c_str());
}

isl_constraint *
setCoefficient(
    isl_constraint *constraint, isl_ctx *context, enum isl_dim_type type,
    int index, uint64_t input, bool negate)
{
    isl_val *coefficient = value(context, input);
    if (negate && coefficient)
        coefficient = isl_val_neg(coefficient);
    return isl_constraint_set_coefficient_val(
        constraint, type, index, coefficient);
}

isl_constraint *
setConstant(
    isl_constraint *constraint, isl_ctx *context, uint64_t input,
    bool negate)
{
    isl_val *constant = value(context, input);
    if (negate && constant)
        constant = isl_val_neg(constant);
    return isl_constraint_set_constant_val(constraint, constant);
}

bool
addLowerBound(isl_basic_map *&map, int dimension)
{
    isl_constraint *constraint = isl_constraint_alloc_inequality(
        isl_local_space_from_space(isl_basic_map_get_space(map)));
    if (!constraint)
        return false;
    constraint = isl_constraint_set_coefficient_si(
        constraint, isl_dim_in, dimension, 1);
    map = isl_basic_map_add_constraint(map, constraint);
    return map != nullptr;
}

bool
addUpperBound(isl_basic_map *&map, isl_ctx *context, int dimension,
              uint64_t exclusive)
{
    if (exclusive == 0)
        return false;
    isl_constraint *constraint = isl_constraint_alloc_inequality(
        isl_local_space_from_space(isl_basic_map_get_space(map)));
    if (!constraint)
        return false;
    constraint = isl_constraint_set_coefficient_si(
        constraint, isl_dim_in, dimension, -1);
    constraint = setConstant(constraint, context, exclusive - 1, false);
    map = isl_basic_map_add_constraint(map, constraint);
    return map != nullptr;
}

isl_map *
emptyMap(isl_ctx *context, size_t dimensions)
{
    if (dimensions > size_t(std::numeric_limits<int>::max()))
        return nullptr;
    return isl_map_empty(isl_space_alloc(
        context, 0, unsigned(dimensions), 1));
}

isl_map *
affineMap(
    isl_ctx *context, const std::vector<uint64_t> &shape,
    const std::vector<uint64_t> &strides, uint64_t base,
    uint64_t trailingExtent)
{
    if (shape.size() != strides.size() ||
        shape.size() >= size_t(std::numeric_limits<int>::max()))
        return nullptr;
    if (std::any_of(shape.begin(), shape.end(),
                    [](uint64_t item) { return item == 0; })) {
        return emptyMap(context, shape.size() + (trailingExtent != 0));
    }
    const size_t inputDimensions = shape.size() + (trailingExtent != 0);
    isl_basic_map *map = isl_basic_map_universe(isl_space_alloc(
        context, 0, unsigned(inputDimensions), 1));
    if (!map)
        return nullptr;
    for (size_t index = 0; index < shape.size(); ++index) {
        const int dimension = int(index);
        if (!addLowerBound(map, dimension) ||
            !addUpperBound(map, context, dimension, shape[index])) {
            isl_basic_map_free(map);
            return nullptr;
        }
    }
    if (trailingExtent != 0 &&
        (!addLowerBound(map, int(shape.size())) ||
         !addUpperBound(
             map, context, int(shape.size()), trailingExtent))) {
        isl_basic_map_free(map);
        return nullptr;
    }
    isl_constraint *constraint = isl_constraint_alloc_equality(
        isl_local_space_from_space(isl_basic_map_get_space(map)));
    if (!constraint) {
        isl_basic_map_free(map);
        return nullptr;
    }
    constraint = isl_constraint_set_coefficient_si(
        constraint, isl_dim_out, 0, 1);
    for (size_t index = 0; constraint && index < strides.size(); ++index) {
        constraint = setCoefficient(
            constraint, context, isl_dim_in, int(index), strides[index],
            true);
    }
    if (constraint && trailingExtent != 0) {
        constraint = isl_constraint_set_coefficient_si(
            constraint, isl_dim_in, int(shape.size()), -1);
    }
    constraint = constraint ? setConstant(constraint, context, base, true)
                            : nullptr;
    map = isl_basic_map_add_constraint(map, constraint);
    if (!map)
        return nullptr;
    return isl_map_from_basic_map(map);
}

isl_set *
emptySet(isl_ctx *context)
{
    return isl_set_empty(isl_space_set_alloc(context, 0, 1));
}

isl_set *
intervalSet(isl_ctx *context, uint64_t bytes)
{
    if (bytes == 0)
        return emptySet(context);
    isl_basic_map *map = isl_basic_map_universe(
        isl_space_alloc(context, 0, 1, 1));
    if (!map || !addLowerBound(map, 0) ||
        !addUpperBound(map, context, 0, bytes)) {
        isl_basic_map_free(map);
        return nullptr;
    }
    isl_constraint *constraint = isl_constraint_alloc_equality(
        isl_local_space_from_space(isl_basic_map_get_space(map)));
    if (!constraint) {
        isl_basic_map_free(map);
        return nullptr;
    }
    constraint = isl_constraint_set_coefficient_si(
        constraint, isl_dim_out, 0, 1);
    constraint = isl_constraint_set_coefficient_si(
        constraint, isl_dim_in, 0, -1);
    map = isl_basic_map_add_constraint(map, constraint);
    isl_map *identity = map ? isl_map_from_basic_map(map) : nullptr;
    return identity ? isl_map_range(identity) : nullptr;
}

}

struct RegionContext
{
    RegionContext()
    {
        context = isl_ctx_alloc();
        if (context)
            isl_options_set_on_error(context, ISL_ON_ERROR_CONTINUE);
    }

    ~RegionContext()
    {
        isl_ctx_free(context);
    }

    isl_ctx *context = nullptr;
};

struct ExactByteRegion::Storage
{
    Storage(std::shared_ptr<const RegionContext> inputContext, isl_set *input)
        : context(std::move(inputContext)), set(input)
    {}

    ~Storage()
    {
        isl_set_free(set);
    }

    std::shared_ptr<const RegionContext> context;
    isl_set *set = nullptr;
};

struct VerifiedProgramGeometry::Impl
{
    std::shared_ptr<const RegionContext> context;
    ExactByteRegion empty;
    std::map<uint64_t, ExactByteRegion> objects;
    std::map<std::pair<uint64_t, GeometryAccessRole>,
             std::vector<GeometryAccessFact>> accesses;
};

struct GeometryBuilder
{
    GeometryBuilder(
        const DecodedProgram &inputProgram,
        const ProgramSemanticContext &inputContext, MeshLoadError &inputError)
        : program(inputProgram), context(inputContext), error(inputError),
          impl(std::make_shared<VerifiedProgramGeometry::Impl>())
    {
        impl->context = std::make_shared<RegionContext>();
    }

    bool build()
    {
        if (!impl->context || !impl->context->context ||
            !makeRegion(emptySet(impl->context->context), impl->empty)) {
            return fail(E_ABI_CORRUPT, "exact region context is unavailable",
                        error);
        }
        return admitTensors() && admitPlacementsAndShards() &&
            admitPartialDefinitions() && admitObjects() && admitViews() &&
            admitAccesses();
    }

    bool makeRegion(isl_set *set, ExactByteRegion &out)
    {
        if (!set)
            return false;
        set = isl_set_coalesce(set);
        if (!set)
            return false;
        ExactByteRegion candidate;
        candidate.storage_ = std::make_shared<ExactByteRegion::Storage>(
            impl->context, set);
        out = std::move(candidate);
        return true;
    }

    bool contains(
        const ExactByteRegion &available, const ExactByteRegion &required)
    {
        const isl_bool result = isl_set_is_subset(
            required.storage_->set, available.storage_->set);
        if (result == isl_bool_error) {
            return fail(E_ABI_CORRUPT,
                        "exact region containment query failed", error);
        }
        return result == isl_bool_true ||
            fail(E_EXPORT_LAYOUT, "access exceeds its view", error);
    }

    bool values(const ListSpan &span, std::vector<uint64_t> &out)
    {
        return spanValues(program, span, out, error);
    }

    bool tensorVectors(
        const KernelTensor &tensor, std::vector<uint64_t> &shape,
        std::vector<uint64_t> &strides)
    {
        return values(tensor.shape, shape) && values(tensor.strides, strides) &&
            rankMatches(shape, strides, error);
    }

    bool admitTensors()
    {
        for (const auto &entry : context.tensors()) {
            const uint64_t tensorId = entry.first;
            const KernelTensor *tensor = entry.second;
            auto rootIt = context.tensors().find(tensor->alias_root_tensor_id);
            if (rootIt == context.tensors().end() ||
                rootIt->second->alias_root_tensor_id != rootIt->first ||
                rootIt->second->dtype != tensor->dtype) {
                return fail(E_EXPORT_LAYOUT,
                            "tensor alias root geometry is invalid", error);
            }
            std::vector<uint64_t> shape;
            std::vector<uint64_t> strides;
            if (!tensorVectors(*tensor, shape, strides))
                return false;
            uint64_t width = 0;
            uint64_t logicalElements = 0;
            uint64_t storageElementsCount = 0;
            uint64_t logicalBytes = 0;
            uint64_t storageBytes = 0;
            if (!dtypeByteWidth(tensor->dtype, width))
                return fail(E_EXPORT_DTYPE, "tensor dtype is invalid", error);
            if (!product(shape, logicalElements, error) ||
                !storageElements(
                    shape, strides, tensor->storage_offset_elements,
                    storageElementsCount, error) ||
                !checkedMul(logicalElements, width, logicalBytes, error) ||
                !checkedMul(storageElementsCount, width, storageBytes, error))
                return false;
            if (tensor->logical_extent_bytes != logicalBytes ||
                tensor->storage_extent_bytes != storageBytes) {
                return fail(E_EXPORT_LAYOUT,
                            "tensor extent is inconsistent", error);
            }
            tensorShapes.emplace(tensorId, std::move(shape));
        }
        return true;
    }

    bool placementCores(
        const Placement &placement, std::vector<uint64_t> &out)
    {
        if (!values(placement.core_ids, out))
            return false;
        if (std::any_of(out.begin(), out.end(), [](uint64_t core) {
                return core >= kInvalidCoreId;
            })) {
            return fail(E_ABI_BOUNDS,
                        "placement participant is out of bounds", error);
        }
        if (out.empty() || std::set<uint64_t>(out.begin(), out.end()).size() !=
                out.size()) {
            return fail(E_PLACEMENT_INFEASIBLE,
                        "placement participants are invalid", error);
        }
        return true;
    }

    bool shardVectors(
        const TensorShard &shard, std::vector<uint64_t> &origin,
        std::vector<uint64_t> &padded, std::vector<uint64_t> &valid)
    {
        return values(shard.global_origin, origin) &&
            values(shard.padded_local_shape, padded) &&
            values(shard.valid_shape, valid) &&
            layoutRankMatches(origin, padded, error) &&
            layoutRankMatches(origin, valid, error);
    }

    bool admitPlacementsAndShards()
    {
        for (const auto &[placementId, placement] : context.placements()) {
            std::vector<uint64_t> cores;
            if (!placementCores(*placement, cores))
                return false;
            placementCoresById.emplace(placementId, std::move(cores));
        }
        for (const auto &[shardId, shard] : context.logicalShards()) {
            auto tensorIt = context.tensors().find(shard->tensor_id);
            auto placementIt = context.placements().find(shard->placement_id);
            if (tensorIt == context.tensors().end() ||
                placementIt == context.placements().end() ||
                !sameOwner(
                    context, "logical_shards", shardId, "kernel_tensors",
                    shard->tensor_id) ||
                !sameOwner(
                    context, "logical_shards", shardId, "placements",
                    shard->placement_id)) {
                return fail(E_ABI_BOUNDS,
                            "shard geometry reference is invalid", error);
            }
            const auto &cores = placementCoresById.at(shard->placement_id);
            if (std::find(cores.begin(), cores.end(), shard->owner_core) ==
                cores.end()) {
                return fail(E_ABI_BOUNDS, "shard owner is invalid", error);
            }
            std::vector<uint64_t> origin;
            std::vector<uint64_t> padded;
            std::vector<uint64_t> valid;
            if (!shardVectors(*shard, origin, padded, valid))
                return false;
            const auto &shape = tensorShapes.at(shard->tensor_id);
            if (!layoutRankMatches(origin, shape, error))
                return false;
            for (size_t index = 0; index < origin.size(); ++index) {
                uint64_t end = 0;
                if (valid[index] > padded[index]) {
                    return fail(E_EXPORT_LAYOUT,
                                "shard valid geometry is invalid", error);
                }
                if (!checkedAdd(origin[index], valid[index], end, error))
                    return false;
                if (end > shape[index]) {
                    return fail(E_EXPORT_LAYOUT,
                                "shard valid geometry exceeds its tensor",
                                error);
                }
            }
            if ((shard->distribution == DistributionKind::PARTIAL_SUM) !=
                (shard->partial_sum_id != 0)) {
                return fail(E_EXPORT_LAYOUT,
                            "shard partial identity is invalid", error);
            }
            groups[{shard->tensor_id, shard->placement_id}].push_back(shard);
            shardOrigins.emplace(shardId, std::move(origin));
            shardPadded.emplace(shardId, std::move(padded));
            shardValid.emplace(shardId, std::move(valid));
        }
        for (const auto &[tensorId, tensor] : context.tensors()) {
            const bool hasShard = std::any_of(
                groups.begin(), groups.end(), [tensorId](const auto &entry) {
                    return entry.first.first == tensorId && !entry.second.empty();
                });
            if (!hasShard)
                return fail(E_ABI_BOUNDS, "tensor has no shard", error);
        }
        for (const auto &[key, group] : groups) {
            if (!admitShardGroup(key.first, key.second, group))
                return false;
        }
        return true;
    }

    bool admitShardGroup(
        uint64_t tensorId, uint64_t placementId,
        const std::vector<const TensorShard *> &group)
    {
        const auto &cores = placementCoresById.at(placementId);
        std::vector<uint64_t> owners;
        owners.reserve(group.size());
        std::set<DistributionKind> distributions;
        for (const TensorShard *shard : group) {
            owners.push_back(shard->owner_core);
            distributions.insert(shard->distribution);
        }
        if (std::set<uint64_t>(owners.begin(), owners.end()).size() !=
                owners.size() || distributions.size() != 1 || owners != cores) {
            return fail(E_PLACEMENT_INFEASIBLE,
                        "shard placement group is invalid", error);
        }
        const DistributionKind distribution = *distributions.begin();
        const auto &shape = tensorShapes.at(tensorId);
        if (distribution == DistributionKind::REPLICATED) {
            for (const TensorShard *shard : group) {
                if (std::any_of(
                        shardOrigins.at(shard->shard_id).begin(),
                        shardOrigins.at(shard->shard_id).end(),
                        [](uint64_t value) { return value != 0; }) ||
                    shardValid.at(shard->shard_id) != shape) {
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "replicated shard coverage is invalid", error);
                }
            }
            return true;
        }
        std::vector<const TensorShard *> coverage = group;
        if (distribution == DistributionKind::PARTIAL_SUM) {
            const uint64_t partialId = group.front()->partial_sum_id;
            const auto &origin = shardOrigins.at(group.front()->shard_id);
            const auto &valid = shardValid.at(group.front()->shard_id);
            for (const TensorShard *shard : group) {
                if (shard->partial_sum_id != partialId ||
                    shardOrigins.at(shard->shard_id) != origin ||
                    shardValid.at(shard->shard_id) != valid) {
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "partial shard coverage is invalid", error);
                }
            }
            coverage.assign(1, group.front());
        }
        uint64_t tensorVolume = 0;
        uint64_t volume = 0;
        if (!product(shape, tensorVolume, error))
            return false;
        for (const TensorShard *shard : coverage) {
            uint64_t piece = 0;
            if (!product(shardValid.at(shard->shard_id), piece, error))
                return false;
            if (piece > tensorVolume - volume) {
                return fail(E_EXPORT_LAYOUT,
                            "shard coverage exceeds its tensor", error);
            }
            volume += piece;
        }
        if (volume != tensorVolume) {
            return fail(E_EXPORT_LAYOUT,
                        "shard coverage is incomplete", error);
        }
        for (size_t first = 0; first < coverage.size(); ++first) {
            for (size_t second = first + 1; second < coverage.size(); ++second) {
                bool overlap = false;
                if (!regionsOverlap(
                        *coverage[first], *coverage[second], program,
                        overlap, error)) {
                    return false;
                }
                if (overlap) {
                    return fail(E_EXPORT_LAYOUT,
                                "shard coverage overlaps", error);
                }
            }
        }
        return true;
    }

    bool admitPartialDefinitions()
    {
        for (const auto &[partialId, definition] : context.partialSums()) {
            auto result = context.tensors().find(definition->semantic_result_tensor_id);
            auto accumulator = context.tensors().find(
                definition->accumulator_tensor_id);
            if (result == context.tensors().end() ||
                accumulator == context.tensors().end() ||
                context.placements().count(definition->placement_id) == 0 ||
                context.computations().count(definition->computation_id) == 0 ||
                !sameOwner(
                    context, "partial_sums", partialId, "kernel_tensors",
                    definition->semantic_result_tensor_id) ||
                !sameOwner(
                    context, "partial_sums", partialId, "kernel_tensors",
                    definition->accumulator_tensor_id) ||
                !sameOwner(
                    context, "partial_sums", partialId, "placements",
                    definition->placement_id) ||
                !sameOwner(
                    context, "partial_sums", partialId, "computations",
                    definition->computation_id)) {
                return fail(E_ABI_BOUNDS,
                            "partial definition geometry is invalid", error);
            }
            if ((accumulator->second->presence_mask &
                 kKernelTensorSynthesizedPurposeField.optional_presence_mask) == 0 ||
                accumulator->second->synthesized_purpose !=
                    mesh_abi::semantic_abi::SynthesizedTensorPurpose::PARTIAL_SUM ||
                accumulator->second->producer_computation_id !=
                    definition->computation_id ||
                tensorShapes.at(result->first) !=
                    tensorShapes.at(accumulator->first) ||
                accumulator->second->dtype !=
                    computationAccumulationDtype(result->second->dtype)) {
                return fail(E_EXPORT_DTYPE,
                            "partial definition type geometry is invalid",
                            error);
            }
            bool found = false;
            for (const auto &[key, group] : groups) {
                for (const TensorShard *shard : group) {
                    if (shard->partial_sum_id == partialId) {
                        found = true;
                        if (shard->tensor_id != accumulator->first ||
                            shard->placement_id != definition->placement_id) {
                            return fail(E_PLACEMENT_INFEASIBLE,
                                        "partial definition shards are invalid",
                                        error);
                        }
                    }
                }
            }
            if (!found) {
                return fail(E_PLACEMENT_INFEASIBLE,
                            "partial definition has no shard", error);
            }
        }
        for (const auto &[shardId, shard] : context.logicalShards()) {
            if (shard->partial_sum_id != 0 &&
                context.partialSums().count(shard->partial_sum_id) == 0) {
                return fail(E_ABI_BOUNDS,
                            "shard partial definition is absent", error);
            }
        }
        return true;
    }

    bool admitObjects()
    {
        for (const auto &[objectId, object] : context.objects()) {
            auto tensor = context.tensors().find(object->storage_tensor_id);
            if (tensor == context.tensors().end() ||
                tensor->second->alias_root_tensor_id != tensor->first) {
                return fail(E_EXPORT_LAYOUT,
                            "object storage root is invalid", error);
            }
            if (object->owner_core > kInvalidCoreId) {
                return fail(E_ABI_BOUNDS, "object owner is invalid", error);
            }
            if (!validMemorySpace(uint32_t(object->memory_space))) {
                return fail(E_ABI_ENUM, "object memory space is invalid", error);
            }
            if (object->memory_space ==
                mesh_abi::semantic_abi::MemorySpace::PEER_SRAM) {
                return fail(E_ABI_ENUM,
                            "peer SRAM is not an owning object space", error);
            }
            if (object->memory_space ==
                mesh_abi::semantic_abi::MemorySpace::CORE_SRAM) {
                if (object->owner_core == kInvalidCoreId) {
                    return fail(E_PLACEMENT_INFEASIBLE,
                                "local object owner is invalid", error);
                }
            } else if (object->owner_core != kInvalidCoreId) {
                return fail(E_PLACEMENT_INFEASIBLE,
                            "external object owner is invalid", error);
            }
            std::vector<uint64_t> shape;
            std::vector<uint64_t> strides;
            if (!values(object->shape, shape) || !values(object->strides, strides) ||
                !rankMatches(shape, strides, error)) {
                return false;
            }
            uint64_t width = 0;
            uint64_t elements = 0;
            uint64_t footprint = 0;
            if (!dtypeByteWidth(tensor->second->dtype, width)) {
                return fail(E_EXPORT_DTYPE, "object dtype is invalid", error);
            }
            if (!storageElements(shape, strides, 0, elements, error) ||
                !checkedMul(elements, width, footprint, error)) {
                return false;
            }
            if (object->footprint_bytes != footprint ||
                !isPowerOfTwo(object->alignment_bytes)) {
                return fail(E_EXPORT_LAYOUT,
                            "object extent is invalid", error);
            }
            ExactByteRegion bytes;
            if (!makeRegion(intervalSet(impl->context->context, footprint), bytes)) {
                return fail(E_ABI_CORRUPT,
                            "object byte region is unavailable", error);
            }
            impl->objects.emplace(objectId, std::move(bytes));
        }
        return true;
    }

    bool rowMajorStrides(
        const std::vector<uint64_t> &shape, std::vector<uint64_t> &out)
    {
        out.assign(shape.size(), 1);
        uint64_t stride = 1;
        for (size_t index = shape.size(); index-- > 0;) {
            out[index] = stride;
            if (index != 0 && !checkedMul(stride, shape[index], stride, error))
                return false;
        }
        return true;
    }

    bool transposedStrides(
        const std::vector<uint64_t> &shape, std::vector<uint64_t> &out)
    {
        if (shape.size() < 2)
            return fail(E_EXPORT_LAYOUT,
                        "transposed view has insufficient rank", error);
        uint64_t matrix = 0;
        if (!checkedMul(shape[shape.size() - 2], shape.back(), matrix, error))
            return false;
        out.assign(shape.size(), 0);
        uint64_t leading = matrix;
        for (size_t index = shape.size() - 2; index-- > 0;) {
            out[index] = leading;
            if (index != 0 &&
                !checkedMul(leading, shape[index], leading, error)) {
                return false;
            }
        }
        out[shape.size() - 2] = 1;
        out.back() = shape[shape.size() - 2];
        return true;
    }

    bool admitViews()
    {
        std::set<uint64_t> viewed;
        for (const auto &[viewId, view] : context.views()) {
            auto object = context.objects().find(view->object_id);
            auto shard = context.logicalShards().find(view->shard_id);
            if (object == context.objects().end() ||
                shard == context.logicalShards().end()) {
                return fail(E_ABI_BOUNDS, "view geometry is invalid", error);
            }
            auto tensor = context.tensors().find(shard->second->tensor_id);
            auto storage = context.tensors().find(object->second->storage_tensor_id);
            if (tensor == context.tensors().end() ||
                storage == context.tensors().end() ||
                tensor->second->dtype != storage->second->dtype) {
                return fail(E_EXPORT_DTYPE, "view type is invalid", error);
            }
            if (object->second->memory_space ==
                    mesh_abi::semantic_abi::MemorySpace::CORE_SRAM &&
                object->second->owner_core != shard->second->owner_core) {
                return fail(E_PLACEMENT_INFEASIBLE,
                            "local view owner differs from its shard", error);
            }
            std::vector<uint64_t> origin;
            std::vector<uint64_t> padded;
            std::vector<uint64_t> valid;
            std::vector<uint64_t> strides;
            if (!values(view->shard_origin, origin) ||
                !values(view->padded_shape, padded) ||
                !values(view->valid_shape, valid) ||
                !values(view->object_strides, strides) ||
                !rankMatches(origin, padded, error) ||
                !rankMatches(origin, valid, error) ||
                !rankMatches(origin, strides, error)) {
                return false;
            }
            if (origin.size() != shardPadded.at(view->shard_id).size() ||
                origin.size() != shardValid.at(view->shard_id).size()) {
                return fail(E_EXPORT_DTYPE, "view rank is invalid", error);
            }
            for (size_t index = 0; index < origin.size(); ++index) {
                uint64_t paddedEnd = 0;
                uint64_t validEnd = 0;
                if (valid[index] > padded[index]) {
                    return fail(E_EXPORT_LAYOUT,
                                "view window is invalid", error);
                }
                if (!checkedAdd(origin[index], padded[index], paddedEnd, error) ||
                    !checkedAdd(origin[index], valid[index], validEnd, error)) {
                    return false;
                }
                if (paddedEnd > shardPadded.at(view->shard_id)[index] ||
                    validEnd > shardValid.at(view->shard_id)[index]) {
                    return fail(E_EXPORT_LAYOUT,
                                "view window exceeds its shard", error);
                }
            }
            uint64_t width = 0;
            uint64_t elements = 0;
            uint64_t viewByteCount = 0;
            if (!dtypeByteWidth(tensor->second->dtype, width))
                return fail(E_EXPORT_DTYPE, "view dtype is invalid", error);
            if (!storageElements(padded, strides, view->object_offset_elements,
                                 elements, error) ||
                !checkedMul(elements, width, viewByteCount, error))
                return false;
            if (viewByteCount > object->second->footprint_bytes) {
                return fail(E_EXPORT_LAYOUT,
                            "view exceeds its object", error);
            }
            std::vector<uint64_t> byteStrides(strides.size(), 0);
            uint64_t byteOffset = 0;
            if (elements != 0) {
                for (size_t index = 0; index < strides.size(); ++index) {
                    if (!effectiveByteStride(
                            padded[index], strides[index], width,
                            byteStrides[index], error)) {
                        return false;
                    }
                }
                if (!checkedMul(view->object_offset_elements, width,
                                byteOffset, error)) {
                    return false;
                }
            }
            isl_map *viewMap = affineMap(
                impl->context->context, padded, byteStrides, byteOffset, width);
            ExactByteRegion mappedBytes;
            if (!makeRegion(viewMap ? isl_map_range(viewMap) : nullptr,
                            mappedBytes)) {
                return fail(E_ABI_CORRUPT,
                            "view byte region is unavailable", error);
            }
            if (!contains(impl->objects.at(view->object_id), mappedBytes))
                return false;
            std::vector<uint64_t> expected;
            const bool hasLayout =
                (view->presence_mask &
                 kBufferViewLayoutField.optional_presence_mask) != 0;
            const bool hasBlocked =
                (view->presence_mask &
                 kBufferViewBlockedLayoutField.optional_presence_mask) != 0;
            if (!hasLayout) {
                if (hasBlocked) {
                    return fail(E_EXPORT_LAYOUT,
                                "untyped view carries blocked layout", error);
                }
            } else if (view->layout == Layout::CONTIGUOUS_ROW_MAJOR) {
                if (!rowMajorStrides(padded, expected))
                    return false;
                if (strides != expected || hasBlocked) {
                    return fail(E_EXPORT_LAYOUT,
                                "row-major view layout is invalid", error);
                }
            } else if (view->layout == Layout::TRANSPOSED_2D_VIEW) {
                if (!transposedStrides(padded, expected))
                    return false;
                if (strides != expected || hasBlocked) {
                    return fail(E_EXPORT_LAYOUT,
                                "transposed view layout is invalid", error);
                }
            } else if (view->layout == Layout::BLOCKED_MNK) {
                const BlockedMnkLayout *blocked = semanticRow(
                    view->blocked_layout,
                    program.semantic_tables.blocked_mnk_layout_rows);
                std::vector<uint64_t> order;
                if (!hasBlocked || !blocked || blocked->block_m == 0 ||
                    blocked->block_n == 0 || blocked->block_k == 0) {
                    return fail(E_EXPORT_LAYOUT,
                                "blocked view layout is invalid", error);
                }
                if (!values(blocked->minor_to_major, order))
                    return false;
                if (order.size() != 3 ||
                    std::set<uint64_t>(order.begin(), order.end()) !=
                        std::set<uint64_t>({0, 1, 2})) {
                    return fail(E_EXPORT_LAYOUT,
                                "blocked view layout is invalid", error);
                }
            } else {
                return fail(E_EXPORT_LAYOUT, "view layout is invalid", error);
            }
            viewed.insert(view->object_id);
            viewOrigins.emplace(viewId, std::move(origin));
            viewPadded.emplace(viewId, std::move(padded));
            viewStrides.emplace(viewId, std::move(strides));
            viewBytes.emplace(viewId, std::move(mappedBytes));
        }
        for (const auto &[objectId, object] : context.objects()) {
            if (viewed.count(objectId) == 0) {
                return fail(E_ABI_BOUNDS, "object has no view", error);
            }
        }
        return true;
    }

    bool accessVectors(
        const ElementRegion &region, std::vector<uint64_t> &origin,
        std::vector<uint64_t> &shape, std::vector<uint64_t> &steps)
    {
        if (!values(region.origin, origin) || !values(region.shape, shape) ||
            !values(region.steps, steps) || !rankMatches(origin, shape, error) ||
            !rankMatches(origin, steps, error)) {
            return false;
        }
        if (std::any_of(steps.begin(), steps.end(),
                        [](uint64_t step) { return step == 0; })) {
            return fail(E_ABI_BOUNDS, "access steps are invalid", error);
        }
        return true;
    }

    bool admitAccess(
        const KernelOp &operation, uint64_t operationId,
        GeometryAccessRole role, size_t ordinal, const OperandAccess *read,
        const StateTransition *write)
    {
        const uint64_t viewId = read ? read->view_id : write->view_id;
        const SemanticRef regionRef = read ? read->region : write->region;
        const ElementRegion *region = semanticRow(
            regionRef, program.semantic_tables.element_region_rows);
        auto view = context.views().find(viewId);
        if (!region || view == context.views().end()) {
            return fail(E_ABI_BOUNDS, "access geometry is invalid", error);
        }
        if (read) {
            auto state = context.states().find(read->state_id);
            if (read->mode != OperandAccessMode::READ ||
                state == context.states().end() ||
                state->second->object_id != view->second->object_id ||
                !sameOwner(context, "kernel_ops", operationId, "states",
                           read->state_id)) {
                return fail(E_ABI_BOUNDS, "read access identity is invalid",
                            error);
            }
        } else {
            auto oldState = context.states().find(write->old_state_id);
            auto newState = context.states().find(write->new_state_id);
            if (write->mode != OperandAccessMode::WRITE ||
                oldState == context.states().end() ||
                newState == context.states().end() ||
                oldState->second->object_id != view->second->object_id ||
                newState->second->object_id != view->second->object_id ||
                !sameOwner(context, "kernel_ops", operationId, "states",
                           write->old_state_id) ||
                !sameOwner(context, "kernel_ops", operationId, "states",
                           write->new_state_id)) {
                return fail(E_ABI_BOUNDS, "write access identity is invalid",
                            error);
            }
        }
        auto shard = context.logicalShards().find(view->second->shard_id);
        auto object = context.objects().find(view->second->object_id);
        if (shard == context.logicalShards().end() ||
            object == context.objects().end()) {
            return fail(E_ABI_BOUNDS, "access storage is invalid", error);
        }
        auto tensor = context.tensors().find(shard->second->tensor_id);
        if (tensor == context.tensors().end()) {
            return fail(E_ABI_BOUNDS, "access tensor is invalid", error);
        }
        std::vector<uint64_t> origin;
        std::vector<uint64_t> shape;
        std::vector<uint64_t> steps;
        if (!accessVectors(*region, origin, shape, steps)) {
            return false;
        }
        if (origin.size() != viewPadded.at(viewId).size()) {
            return fail(E_EXPORT_LAYOUT, "access rank differs from its view",
                        error);
        }
        uint64_t logicalElements = 0;
        if (!product(shape, logicalElements, error))
            return false;
        const bool empty = logicalElements == 0;
        uint64_t width = 0;
        if (!dtypeByteWidth(tensor->second->dtype, width))
            return fail(E_EXPORT_DTYPE, "access dtype is invalid", error);
        SourceStorageProjection storage;
        if (!sourceStorageProjection(
                origin, shape, steps, viewStrides.at(viewId),
                view->second->object_offset_elements, storage, error)) {
            return false;
        }
        std::vector<uint64_t> globalOrigin(origin.size(), 0);
        std::vector<uint64_t> byteStrides(origin.size(), 0);
        for (size_t index = 0; index < origin.size(); ++index) {
            if (shape[index] != 0) {
                uint64_t localLast = 0;
                if (!checkedMul(shape[index] - 1, steps[index], localLast,
                                error) ||
                    !checkedAdd(origin[index], localLast, localLast, error)) {
                    return false;
                }
                if (localLast >= viewPadded.at(viewId)[index]) {
                    return fail(E_EXPORT_LAYOUT,
                                "access exceeds its view domain", error);
                }
            }
            uint64_t global = 0;
            if (!checkedAdd(shardOrigins.at(shard->first)[index],
                            viewOrigins.at(viewId)[index], global, error) ||
                !checkedAdd(global, origin[index], global, error)) {
                return false;
            }
            globalOrigin[index] = global;
            if (!empty && !effectiveByteStride(
                               shape[index], storage.elementStrides[index], width,
                               byteStrides[index], error)) {
                return false;
            }
        }
        uint64_t objectByteOffset = 0;
        if (!empty &&
            !checkedMul(storage.elementOffset, width, objectByteOffset, error)) {
            return false;
        }
        isl_map *byteMap = affineMap(
            impl->context->context, shape, byteStrides, objectByteOffset, width);
        isl_set *bytes = byteMap ? isl_map_range(byteMap) : nullptr;
        ExactByteRegion byteRegion;
        if (!makeRegion(bytes, byteRegion)) {
            return fail(E_ABI_CORRUPT, "access byte region is unavailable",
                        error);
        }
        if (!contains(viewBytes.at(viewId), byteRegion) ||
            !contains(impl->objects.at(object->first), byteRegion)) {
            return false;
        }
        if (role == GeometryAccessRole::Write) {
            std::vector<uint64_t> elementStrides = viewStrides.at(viewId);
            for (size_t index = 0; index < elementStrides.size(); ++index) {
                if (!checkedMul(elementStrides[index], steps[index],
                                elementStrides[index], error)) {
                    return false;
                }
            }
            isl_map *elementMap = affineMap(
                impl->context->context, shape, elementStrides,
                storage.elementOffset, 0);
            const isl_bool injective = elementMap ?
                isl_map_is_injective(elementMap) : isl_bool_error;
            isl_map_free(elementMap);
            if (injective == isl_bool_error) {
                return fail(E_ABI_CORRUPT,
                            "write injectivity query failed", error);
            }
            if (injective != isl_bool_true) {
                return fail(E_EXPORT_LAYOUT,
                            "write access is not injective", error);
            }
        }
        GeometryAccessFact fact;
        fact.operation_ = &operation;
        fact.role_ = role;
        fact.ordinal_ = ordinal;
        fact.read_ = read;
        fact.write_ = write;
        fact.tensor_ = tensor->second;
        fact.logicalShard_ = shard->second;
        fact.object_ = object->second;
        fact.view_ = view->second;
        fact.region_ = region;
        fact.logicalElementCount_ = logicalElements;
        fact.localOrigin_ = std::move(origin);
        fact.globalOrigin_ = std::move(globalOrigin);
        fact.shape_ = std::move(shape);
        fact.steps_ = std::move(steps);
        fact.objectByteOffset_ = objectByteOffset;
        fact.objectByteStrides_ = std::move(byteStrides);
        fact.bytes_ = std::move(byteRegion);
        impl->accesses[{operationId, role}].push_back(std::move(fact));
        return true;
    }

    bool admitAccesses()
    {
        for (const auto &[operationId, operation] : context.operations()) {
            const ProgramVariant *owner = context.owner("kernel_ops", operationId);
            if (!owner || !spanFits(
                    operation->reads.begin, operation->reads.count,
                    program.semantic_references.size()) ||
                !spanFits(
                    operation->writes.begin, operation->writes.count,
                    program.semantic_references.size())) {
                return fail(E_ABI_BOUNDS, "operation access list is invalid",
                            error);
            }
            for (uint64_t index = 0; index < operation->reads.count; ++index) {
                const OperandAccess *read = semanticRow(
                    program.semantic_references[size_t(
                        operation->reads.begin + index)],
                    program.semantic_tables.operand_access_rows);
                if (!read || !admitAccess(
                        *operation, operationId, GeometryAccessRole::Read,
                        size_t(index), read, nullptr)) {
                    return false;
                }
            }
            for (uint64_t index = 0; index < operation->writes.count; ++index) {
                const StateTransition *write = semanticRow(
                    program.semantic_references[size_t(
                        operation->writes.begin + index)],
                    program.semantic_tables.state_transition_rows);
                if (!write || !admitAccess(
                        *operation, operationId, GeometryAccessRole::Write,
                        size_t(index), nullptr, write)) {
                    return false;
                }
            }
        }
        return true;
    }

    const DecodedProgram &program;
    const ProgramSemanticContext &context;
    MeshLoadError &error;
    std::shared_ptr<VerifiedProgramGeometry::Impl> impl;
    std::map<uint64_t, std::vector<uint64_t>> tensorShapes;
    std::map<uint64_t, std::vector<uint64_t>> placementCoresById;
    std::map<uint64_t, std::vector<uint64_t>> shardOrigins;
    std::map<uint64_t, std::vector<uint64_t>> shardPadded;
    std::map<uint64_t, std::vector<uint64_t>> shardValid;
    std::map<std::pair<uint64_t, uint64_t>, std::vector<const TensorShard *>>
        groups;
    std::map<uint64_t, std::vector<uint64_t>> viewOrigins;
    std::map<uint64_t, std::vector<uint64_t>> viewPadded;
    std::map<uint64_t, std::vector<uint64_t>> viewStrides;
    std::map<uint64_t, ExactByteRegion> viewBytes;
};

const KernelOp &
GeometryAccessFact::operation() const
{
    return *operation_;
}

GeometryAccessRole
GeometryAccessFact::role() const
{
    return role_;
}

size_t
GeometryAccessFact::ordinal() const
{
    return ordinal_;
}

const OperandAccess *
GeometryAccessFact::read() const
{
    return read_;
}

const StateTransition *
GeometryAccessFact::write() const
{
    return write_;
}

const KernelTensor &
GeometryAccessFact::tensor() const
{
    return *tensor_;
}

const TensorShard &
GeometryAccessFact::logicalShard() const
{
    return *logicalShard_;
}

const BufferObject &
GeometryAccessFact::object() const
{
    return *object_;
}

const BufferView &
GeometryAccessFact::view() const
{
    return *view_;
}

const ElementRegion &
GeometryAccessFact::region() const
{
    return *region_;
}

uint64_t
GeometryAccessFact::logicalElementCount() const
{
    return logicalElementCount_;
}

const std::vector<uint64_t> &
GeometryAccessFact::localOrigin() const
{
    return localOrigin_;
}

const std::vector<uint64_t> &
GeometryAccessFact::globalOrigin() const
{
    return globalOrigin_;
}

const std::vector<uint64_t> &
GeometryAccessFact::shape() const
{
    return shape_;
}

const std::vector<uint64_t> &
GeometryAccessFact::steps() const
{
    return steps_;
}

uint64_t
GeometryAccessFact::objectByteOffset() const
{
    return objectByteOffset_;
}

const std::vector<uint64_t> &
GeometryAccessFact::objectByteStrides() const
{
    return objectByteStrides_;
}

const ExactByteRegion &
GeometryAccessFact::bytes() const
{
    return bytes_;
}

const GeometryAccessFact &
GeometryAccessPieceFact::access() const
{
    return *access_;
}

const std::vector<uint64_t> &
GeometryAccessPieceFact::origin() const
{
    return origin_;
}

const std::vector<uint64_t> &
GeometryAccessPieceFact::shape() const
{
    return shape_;
}

const std::vector<uint64_t> &
GeometryAccessPieceFact::steps() const
{
    return steps_;
}

uint64_t
GeometryAccessPieceFact::logicalElementCount() const
{
    return logicalElementCount_;
}

uint64_t
GeometryAccessPieceFact::elementWidthBytes() const
{
    return elementWidthBytes_;
}

uint64_t
GeometryAccessPieceFact::objectByteOffset() const
{
    return objectByteOffset_;
}

const std::vector<uint64_t> &
GeometryAccessPieceFact::objectByteStrides() const
{
    return objectByteStrides_;
}

uint64_t
GeometryAccessPieceFact::objectByteEnd() const
{
    return objectByteEnd_;
}

VerifiedProgramGeometry::~VerifiedProgramGeometry() = default;

VerifiedProgramGeometry::VerifiedProgramGeometry(
    VerifiedProgramGeometry &&other) noexcept
    : program_(other.program_), context_(other.context_),
      impl_(std::move(other.impl_))
{
    other.program_ = nullptr;
    other.context_ = nullptr;
}

VerifiedProgramGeometry &
VerifiedProgramGeometry::operator=(VerifiedProgramGeometry &&other) noexcept
{
    if (this != &other) {
        program_ = other.program_;
        context_ = other.context_;
        impl_ = std::move(other.impl_);
        other.program_ = nullptr;
        other.context_ = nullptr;
    }
    return *this;
}

bool
VerifiedProgramGeometry::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context) const
{
    return program_ == &program && context_ == &context && impl_ &&
        impl_->context && impl_->context->context;
}

std::shared_ptr<const void>
VerifiedProgramGeometry::invocationIdentity() const
{
    return impl_;
}

bool
VerifiedProgramGeometry::matchesInvocation(
    const std::shared_ptr<const void> &identity) const
{
    return impl_ && identity && impl_.get() == identity.get();
}

size_t
VerifiedProgramGeometry::accessCount(
    uint64_t operationId, GeometryAccessRole role) const
{
    if (!impl_)
        return 0;
    const auto item = impl_->accesses.find({operationId, role});
    return item == impl_->accesses.end() ? 0 : item->second.size();
}

const GeometryAccessFact *
VerifiedProgramGeometry::access(
    uint64_t operationId, GeometryAccessRole role, size_t ordinal) const
{
    if (!impl_)
        return nullptr;
    const auto item = impl_->accesses.find({operationId, role});
    if (item == impl_->accesses.end() || ordinal >= item->second.size())
        return nullptr;
    return &item->second[ordinal];
}

bool
VerifiedProgramGeometry::accessMapsInjectively(
    uint64_t operationId, GeometryAccessRole role, size_t ordinal,
    bool &out, MeshLoadError &error) const
{
    error = {};
    if (!program_ || !context_ || !matches(*program_, *context_))
        return fail(E_ABI_BOUNDS, "geometry access context is invalid", error);
    const GeometryAccessFact *fact = access(operationId, role, ordinal);
    if (!fact)
        return fail(E_ABI_BOUNDS, "geometry access is invalid", error);
    isl_map *map = affineMap(
        impl_->context->context, fact->shape(), fact->objectByteStrides(),
        fact->objectByteOffset(), 0);
    const isl_bool injective = map ? isl_map_is_injective(map) : isl_bool_error;
    isl_map_free(map);
    if (injective == isl_bool_error)
        return fail(E_ABI_CORRUPT, "geometry access injectivity query failed",
                    error);
    out = injective == isl_bool_true;
    return true;
}

bool
VerifiedProgramGeometry::accessByteCount(
    uint64_t operationId, GeometryAccessRole role, size_t ordinal,
    uint64_t &out, MeshLoadError &error) const
{
    error = {};
    if (!program_ || !context_ || !matches(*program_, *context_))
        return fail(E_ABI_BOUNDS, "geometry access context is invalid", error);
    const GeometryAccessFact *fact = access(operationId, role, ordinal);
    if (!fact)
        return fail(E_ABI_BOUNDS, "geometry access is invalid", error);

    bool injective = false;
    if (!accessMapsInjectively(operationId, role, ordinal, injective, error))
        return false;
    if (injective) {
        uint64_t width = 0;
        if (!dtypeByteWidth(fact->tensor().dtype, width)) {
            return fail(E_ABI_CORRUPT, "geometry access dtype is invalid",
                        error);
        }
        uint64_t candidate = 0;
        if (!checkedMul(fact->logicalElementCount(), width, candidate, error))
            return false;
        out = candidate;
        return true;
    }

    if (!fact->bytes_.storage_ || !fact->bytes_.storage_->set) {
        return fail(E_ABI_CORRUPT, "geometry access byte set is invalid",
                    error);
    }
    isl_val *value = isl_set_count_val(fact->bytes_.storage_->set);
    if (!value)
        return fail(E_ABI_CORRUPT, "geometry access byte count failed", error);
    const isl_bool isInteger = isl_val_is_int(value);
    const isl_bool isNegative = isl_val_is_neg(value);
    if (isInteger == isl_bool_error || isNegative == isl_bool_error ||
        isInteger != isl_bool_true || isNegative == isl_bool_true) {
        isl_val_free(value);
        return fail(E_ABI_CORRUPT, "geometry access byte count is invalid",
                    error);
    }
    char *decimal = isl_val_to_str(value);
    isl_val_free(value);
    if (!decimal)
        return fail(E_ABI_CORRUPT, "geometry access byte count is invalid",
                    error);
    const std::string text(decimal);
    std::free(decimal);
    uint64_t candidate = 0;
    const auto parsed = std::from_chars(
        text.data(), text.data() + text.size(), candidate, 10);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        return fail(E_ABI_CORRUPT, "geometry access byte count is invalid",
                    error);
    }
    out = candidate;
    return true;
}

std::optional<GeometryAccessPieceFact>
VerifiedProgramGeometry::projectAccessPiece(
    uint64_t operationId, GeometryAccessRole role, size_t ordinal,
    const SemanticRef &pieceRef, MeshLoadError &error) const
{
    error = {};
    if (!program_ || !context_ || !matches(*program_, *context_)) {
        fail(E_ABI_BOUNDS, "geometry piece context is invalid", error);
        return std::nullopt;
    }
    const GeometryAccessFact *parent = access(operationId, role, ordinal);
    if (!parent) {
        fail(E_ABI_BOUNDS, "geometry piece parent is invalid", error);
        return std::nullopt;
    }
    const ElementRegion *piece = semanticRow(
        pieceRef, program_->semantic_tables.element_region_rows);
    if (!piece) {
        fail(E_DMA_RANGE, "descriptor piece reference is invalid", error);
        return std::nullopt;
    }
    std::vector<uint64_t> origin;
    std::vector<uint64_t> shape;
    std::vector<uint64_t> steps;
    if (!spanValues(*program_, piece->origin, origin, error) ||
        !spanValues(*program_, piece->shape, shape, error) ||
        !spanValues(*program_, piece->steps, steps, error)) {
        return std::nullopt;
    }
    if (origin.size() != shape.size() || origin.size() != steps.size() ||
        std::any_of(
            steps.begin(), steps.end(),
            [](uint64_t step) { return step == 0; })) {
        fail(E_DMA_RANGE, "descriptor piece vectors are invalid", error);
        return std::nullopt;
    }
    std::vector<uint64_t> viewStrides;
    if (!spanValues(
            *program_, parent->view().object_strides, viewStrides, error)) {
        return std::nullopt;
    }
    if (shape.size() != parent->shape().size() ||
        shape.size() != viewStrides.size()) {
        fail(E_DMA_RANGE, "endpoint view and region ranks differ", error);
        return std::nullopt;
    }
    uint64_t logicalElements = 0;
    uint64_t width = 0;
    if (!product(shape, logicalElements, error))
        return std::nullopt;
    if (!dtypeByteWidth(parent->tensor().dtype, width)) {
        fail(E_EXPORT_DTYPE, "endpoint tensor is invalid", error);
        return std::nullopt;
    }
    SourceStorageProjection source;
    EndpointStorageProjection endpoint;
    if (!sourceStorageProjection(
            origin, shape, steps, viewStrides,
            parent->view().object_offset_elements, source, error) ||
        !endpointStorageProjection(
            source, shape, logicalElements, width, endpoint, error)) {
        return std::nullopt;
    }
    if (endpoint.byteEnd > parent->object().footprint_bytes) {
        fail(E_DMA_RANGE, "endpoint access exceeds its object", error);
        return std::nullopt;
    }
    GeometryAccessPieceFact candidate;
    candidate.owner_ = impl_;
    candidate.access_ = parent;
    candidate.origin_ = std::move(origin);
    candidate.shape_ = std::move(shape);
    candidate.steps_ = std::move(steps);
    candidate.logicalElementCount_ = logicalElements;
    candidate.elementWidthBytes_ = width;
    candidate.objectByteOffset_ = endpoint.byteOffset;
    candidate.objectByteStrides_ = std::move(endpoint.byteStrides);
    candidate.objectByteEnd_ = endpoint.byteEnd;
    return std::optional<GeometryAccessPieceFact>(std::move(candidate));
}

const ExactByteRegion *
VerifiedProgramGeometry::objectBytes(uint64_t objectId) const
{
    if (!impl_)
        return nullptr;
    const auto item = impl_->objects.find(objectId);
    return item == impl_->objects.end() ? nullptr : &item->second;
}

const ExactByteRegion &
VerifiedProgramGeometry::emptyBytes() const
{
    static const ExactByteRegion empty;
    return impl_ ? impl_->empty : empty;
}

struct GeometryRegionOperation
{
    static bool valid(
        const VerifiedProgramGeometry::Impl &impl,
        const ExactByteRegion &region)
    {
        return region.storage_ && region.storage_->set &&
            region.storage_->context == impl.context;
    }

    static bool require(
        const VerifiedProgramGeometry::Impl *impl,
        const ExactByteRegion &first, const ExactByteRegion &second,
        MeshLoadError &error)
    {
        if (impl && impl->context && valid(*impl, first) &&
            valid(*impl, second)) {
            return true;
        }
        return fail(E_ABI_BOUNDS, "exact region handle is invalid", error);
    }

    static bool store(
        const VerifiedProgramGeometry::Impl &impl, isl_set *set,
        ExactByteRegion &out, MeshLoadError &error)
    {
        if (!set)
            return fail(E_ABI_CORRUPT, "exact region operation failed", error);
        set = isl_set_coalesce(set);
        if (!set)
            return fail(E_ABI_CORRUPT, "exact region coalescing failed", error);
        ExactByteRegion candidate;
        candidate.storage_ = std::make_shared<ExactByteRegion::Storage>(
            impl.context, set);
        out = std::move(candidate);
        return true;
    }

    static bool query(isl_bool result, bool &out, MeshLoadError &error)
    {
        if (result == isl_bool_error)
            return fail(E_ABI_CORRUPT, "exact region query failed", error);
        out = result == isl_bool_true;
        return true;
    }
};

bool
VerifiedProgramGeometry::unite(
    const ExactByteRegion &first, const ExactByteRegion &second,
    ExactByteRegion &out, MeshLoadError &error) const
{
    if (!GeometryRegionOperation::require(impl_.get(), first, second, error))
        return false;
    return GeometryRegionOperation::store(
        *impl_, isl_set_union(
                    isl_set_copy(first.storage_->set),
                    isl_set_copy(second.storage_->set)),
        out, error);
}

bool
VerifiedProgramGeometry::subtract(
    const ExactByteRegion &first, const ExactByteRegion &second,
    ExactByteRegion &out, MeshLoadError &error) const
{
    if (!GeometryRegionOperation::require(impl_.get(), first, second, error))
        return false;
    return GeometryRegionOperation::store(
        *impl_, isl_set_subtract(
                    isl_set_copy(first.storage_->set),
                    isl_set_copy(second.storage_->set)),
        out, error);
}

bool
VerifiedProgramGeometry::intersect(
    const ExactByteRegion &first, const ExactByteRegion &second,
    ExactByteRegion &out, MeshLoadError &error) const
{
    if (!GeometryRegionOperation::require(impl_.get(), first, second, error))
        return false;
    return GeometryRegionOperation::store(
        *impl_, isl_set_intersect(
                    isl_set_copy(first.storage_->set),
                    isl_set_copy(second.storage_->set)),
        out, error);
}

bool
VerifiedProgramGeometry::contains(
    const ExactByteRegion &available, const ExactByteRegion &required,
    bool &out, MeshLoadError &error) const
{
    if (!GeometryRegionOperation::require(
            impl_.get(), available, required, error))
        return false;
    return GeometryRegionOperation::query(
        isl_set_is_subset(required.storage_->set, available.storage_->set), out,
        error);
}

bool
VerifiedProgramGeometry::disjoint(
    const ExactByteRegion &first, const ExactByteRegion &second,
    bool &out, MeshLoadError &error) const
{
    if (!GeometryRegionOperation::require(impl_.get(), first, second, error))
        return false;
    return GeometryRegionOperation::query(
        isl_set_is_disjoint(first.storage_->set, second.storage_->set), out,
        error);
}

bool
verifyProgramGeometry(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    VerifiedProgramGeometry &out, MeshLoadError &error)
{
    error = {};
    if (!context.matches(program))
        return fail(E_ABI_BOUNDS, "geometry context is invalid", error);
    GeometryBuilder builder(program, context, error);
    if (!builder.build())
        return false;
    VerifiedProgramGeometry candidate;
    candidate.program_ = &program;
    candidate.context_ = &context;
    candidate.impl_ = std::move(builder.impl);
    out = std::move(candidate);
    return true;
}

}
}

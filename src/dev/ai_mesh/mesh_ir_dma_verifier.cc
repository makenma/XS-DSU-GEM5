#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"

#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"

#include <algorithm>
#include <limits>
#include <optional>
#include <set>
#include <tuple>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_ir_spans.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{
class DmaFactIdentity
{};

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

bool
add(uint64_t left, uint64_t right, uint64_t &out)
{
    if (right > std::numeric_limits<uint64_t>::max() - left)
        return false;
    out = left + right;
    return true;
}

bool
multiply(uint64_t left, uint64_t right, uint64_t &out)
{
    if (left != 0 && right > std::numeric_limits<uint64_t>::max() / left)
        return false;
    out = left * right;
    return true;
}

template <typename Record>
const Record *
semanticRow(const SemanticRef &reference, const std::vector<Record> &rows)
{
    using Traits = SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > rows.size())
        return nullptr;
    return &rows[reference.row_id - 1];
}

bool
semanticBytes(
    const DecodedProgram &program, const ListSpan &span,
    std::vector<uint8_t> &out, MeshLoadError &error)
{
    if (!spanFits(span.begin, span.count, program.semantic_bytes.size()))
        return fail(E_ABI_BOUNDS, "semantic byte span is out of bounds",
                    error);
    out.assign(
        program.semantic_bytes.begin() + size_t(span.begin),
        program.semantic_bytes.begin() +
            size_t(span.begin) + size_t(span.count));
    return true;
}

struct RegionValues
{
    std::vector<uint64_t> origin;
    std::vector<uint64_t> shape;
    std::vector<uint64_t> steps;
};

bool
sameRegion(const RegionValues &left, const RegionValues &right)
{
    return left.origin == right.origin && left.shape == right.shape &&
        left.steps == right.steps;
}

bool
orderedRegionInterval(
    const RegionValues &parent, const RegionValues &piece,
    uint64_t &start, uint64_t &count, MeshLoadError &error)
{
    if (parent.origin.size() != piece.origin.size() ||
        parent.shape.size() != piece.shape.size() ||
        parent.steps.size() != piece.steps.size())
        return fail(E_DMA_RANGE, "ordered region ranks differ", error);
    if (std::any_of(parent.shape.begin(), parent.shape.end(),
                    [](uint64_t value) { return value == 0; })) {
        if (!sameRegion(parent, piece))
            return fail(E_DMA_RANGE,
                        "empty ordered region requires its exact piece",
                        error);
        start = 0;
        count = 0;
        return true;
    }
    if (std::any_of(piece.shape.begin(), piece.shape.end(),
                    [](uint64_t value) { return value == 0; }))
        return fail(E_DMA_RANGE,
                    "empty piece cannot cover a nonempty region", error);

    std::vector<uint64_t> parentStrides(parent.shape.size());
    uint64_t inner = 1;
    for (size_t index = parent.shape.size(); index-- > 0;) {
        parentStrides[index] = inner;
        if (!multiply(inner, parent.shape[index], inner))
            return fail(E_ABI_OVERFLOW,
                        "ordered parent element count overflows", error);
    }

    std::vector<uint64_t> starts(parent.shape.size());
    std::vector<uint64_t> ratios(parent.shape.size());
    for (size_t index = 0; index < parent.shape.size(); ++index) {
        if (piece.origin[index] < parent.origin[index])
            return fail(E_DMA_RANGE,
                        "ordered piece is outside its parent lattice", error);
        const uint64_t delta = piece.origin[index] - parent.origin[index];
        if (delta % parent.steps[index] != 0 ||
            (piece.shape[index] > 1 &&
             piece.steps[index] % parent.steps[index] != 0))
            return fail(E_DMA_RANGE,
                        "ordered piece is outside its parent lattice", error);
        starts[index] = delta / parent.steps[index];
        ratios[index] = piece.shape[index] > 1
            ? piece.steps[index] / parent.steps[index] : 0;
        uint64_t displacement = 0;
        uint64_t last = 0;
        if (!multiply(piece.shape[index] - 1, ratios[index], displacement) ||
            !add(starts[index], displacement, last))
            return fail(E_ABI_OVERFLOW,
                        "ordered piece axis end overflows", error);
        if (last >= parent.shape[index])
            return fail(E_DMA_RANGE,
                        "ordered piece exceeds its parent", error);
    }

    count = 1;
    for (uint64_t extent : piece.shape) {
        if (!multiply(count, extent, count))
            return fail(E_ABI_OVERFLOW,
                        "ordered piece element count overflows", error);
    }
    uint64_t contiguousStride = 1;
    for (size_t index = piece.shape.size(); index-- > 0;) {
        uint64_t traversalStride = 0;
        if (piece.shape[index] > 1 &&
            (!multiply(ratios[index], parentStrides[index],
                       traversalStride) ||
             traversalStride != contiguousStride))
            return fail(E_DMA_RANGE,
                        "piece traversal is not a continuous interval",
                        error);
        if (!multiply(contiguousStride, piece.shape[index],
                      contiguousStride))
            return fail(E_ABI_OVERFLOW,
                        "ordered piece traversal overflows", error);
    }
    start = 0;
    for (size_t index = 0; index < starts.size(); ++index) {
        uint64_t offset = 0;
        if (!multiply(starts[index], parentStrides[index], offset) ||
            !add(start, offset, start))
            return fail(E_ABI_OVERFLOW,
                        "ordered piece start overflows", error);
    }
    return true;
}

using AffineAxis = std::pair<uint64_t, uint64_t>;

bool
compactAffineAxes(
    const std::vector<uint64_t> &shape,
    const std::vector<uint64_t> &strides,
    std::vector<AffineAxis> &out, MeshLoadError &error)
{
    if (shape.size() != strides.size() ||
        std::any_of(shape.begin(), shape.end(),
                    [](uint64_t value) { return value == 0; }))
        return fail(E_DMA_RANGE, "ordered affine axes are invalid", error);
    out.clear();
    for (size_t index = shape.size(); index-- > 0;) {
        if (shape[index] == 1)
            continue;
        uint64_t expected = 0;
        if (!out.empty() &&
            !multiply(out.front().first, out.front().second, expected))
            return fail(E_ABI_OVERFLOW,
                        "ordered affine coalescing overflows", error);
        if (!out.empty() && strides[index] == expected) {
            if (!multiply(shape[index], out.front().first,
                          out.front().first))
                return fail(E_ABI_OVERFLOW,
                            "ordered affine extent overflows", error);
        } else {
            out.insert(out.begin(), {shape[index], strides[index]});
        }
    }
    return true;
}

bool
isDmaOpcode(uint16_t opcode)
{
    return opcode == kOpcodeDMA_LOAD || opcode == kOpcodeDMA_STORE ||
        opcode == kOpcodeDMA_P2P_PUSH || opcode == kOpcodeDMA_PREFETCH ||
        opcode == kOpcodeDMA_FILL;
}

uint16_t
commandDmaKind(uint16_t opcode)
{
    switch (opcode) {
      case kOpcodeDMA_LOAD:
        return kDmaKindLOAD;
      case kOpcodeDMA_STORE:
        return kDmaKindSTORE;
      case kOpcodeDMA_P2P_PUSH:
        return kDmaKindP2P_PUSH;
      case kOpcodeDMA_PREFETCH:
        return kDmaKindPREFETCH;
      case kOpcodeDMA_FILL:
        return kDmaKindLOCAL_FILL;
      default:
        return 0;
    }
}

struct Membership
{
    const ProgramSemanticContext::MembershipRange *commands = nullptr;
    const ProgramSemanticContext::MembershipRange *events = nullptr;
    const ProgramSemanticContext::MembershipRange *descriptors = nullptr;
    const ProgramSemanticContext::MembershipRange *tensors = nullptr;
    const ProgramSemanticContext::MembershipRange *logicalShards = nullptr;
    const ProgramSemanticContext::MembershipRange *objects = nullptr;
    const ProgramSemanticContext::MembershipRange *views = nullptr;
    const ProgramSemanticContext::MembershipRange *operations = nullptr;
    const ProgramSemanticContext::MembershipRange *commandSemantics = nullptr;
};

bool
membershipView(
    const ProgramSemanticContext &context, uint64_t variantId,
    Membership &out)
{
    out.commands = context.membership(variantId, "commands");
    out.events = context.membership(variantId, "events");
    out.descriptors = context.membership(variantId, "descriptors");
    out.tensors = context.membership(variantId, "kernel_tensors");
    out.logicalShards = context.membership(variantId, "logical_shards");
    out.objects = context.membership(variantId, "objects");
    out.views = context.membership(variantId, "views");
    out.operations = context.membership(variantId, "kernel_ops");
    out.commandSemantics = context.membership(variantId, "command_semantics");
    return out.commands && out.events && out.descriptors && out.tensors &&
        out.logicalShards && out.objects && out.views && out.operations &&
        out.commandSemantics;
}

struct EndpointUses
{
    const DescriptorEndpointUse *source = nullptr;
    const DescriptorEndpointUse *destination = nullptr;
};

struct AccessProjection
{
    std::optional<GeometryAccessPieceFact> piece;
    RegionValues parentValues;
    RegionValues pieceValues;
};

bool
accessProjection(
    const DecodedProgram &program, const VerifiedProgramGeometry &geometry,
    const KernelOp &operation,
    const DescriptorEndpointUse &endpointUse, bool expectRead,
    uint64_t pairIndex, AccessProjection &out, MeshLoadError &error)
{
    uint64_t operationId = 0;
    uint64_t accessIndex = 0;
    SemanticRef pieceRef;
    if (expectRead) {
        const ReadAccessUse *use = semanticRow(
            endpointUse.use, program.semantic_tables.read_access_use_rows);
        if (!use)
            return fail(E_DMA_RANGE,
                        "descriptor endpoint access type is invalid", error);
        operationId = use->kernel_op_id;
        accessIndex = use->access_index;
        pieceRef = use->region;
    } else {
        const WriteAccessUse *use = semanticRow(
            endpointUse.use, program.semantic_tables.write_access_use_rows);
        if (!use)
            return fail(E_DMA_RANGE,
                        "descriptor endpoint access type is invalid", error);
        operationId = use->kernel_op_id;
        accessIndex = use->access_index;
        pieceRef = use->region;
    }
    if (operationId != operation.op_id || accessIndex != pairIndex)
        return fail(E_DMA_RANGE,
                    "descriptor endpoint is not its ordered access pair",
                    error);
    const ListSpan &accesses = expectRead ? operation.reads : operation.writes;
    if (accessIndex >= accesses.count ||
        !spanFits(accesses.begin, accesses.count,
                  program.semantic_references.size()))
        return fail(E_DMA_RANGE,
                    "descriptor endpoint access index is invalid", error);
    out.piece = geometry.projectAccessPiece(
        operationId, expectRead ? GeometryAccessRole::Read :
                                 GeometryAccessRole::Write,
        size_t(accessIndex), pieceRef, error);
    if (!out.piece)
        return false;
    out.parentValues = {
        out.piece->access().localOrigin(), out.piece->access().shape(),
        out.piece->access().steps()};
    out.pieceValues = {
        out.piece->origin(), out.piece->shape(), out.piece->steps()};
    return true;
}

struct ProgramIndices
{
    const std::map<uint64_t, const KernelOp *> &operations;
    const std::map<uint64_t, const CommandSemantics *> &commandSemantics;
    std::map<uint64_t, const DescriptorGroup *> groupsByOperation;
    std::map<uint64_t, const DescriptorGroup *> groupsByCommand;
    std::map<uint32_t, EndpointUses> endpoints;
    const std::map<uint32_t, const Command *> &commands;
    const std::map<uint32_t, const DmaDescriptor *> &descriptors;
    const std::map<uint32_t, uint64_t> &events;

    explicit ProgramIndices(const ProgramSemanticContext &context)
        : operations(context.operations()),
          commandSemantics(context.commandSemantics()),
          commands(context.commands()), descriptors(context.descriptors()),
          events(context.events())
    {}
};

bool
indexGroupsAndEndpoints(
    const DecodedProgram &program, const ProgramSemantics &root,
    const VerifiedCommandProjectionFacts &projection, ProgramIndices &indices,
    MeshLoadError &error)
{
    if (!spanFits(root.descriptor_groups.begin, root.descriptor_groups.count,
                  program.semantic_references.size()) ||
        !spanFits(root.endpoint_uses.begin, root.endpoint_uses.count,
                  program.semantic_references.size()))
        return fail(E_ABI_BOUNDS, "DMA semantic list is out of bounds", error);
    std::map<uint32_t, uint64_t> descriptorOwnerGroups;
    for (uint64_t offset = 0; offset < root.descriptor_groups.count; ++offset) {
        const DescriptorGroup *group = semanticRow(
            program.semantic_references[size_t(root.descriptor_groups.begin +
                                               offset)],
            program.semantic_tables.descriptor_group_rows);
        if (!group || group->command_id == 0 ||
            group->command_id > UINT32_MAX ||
            group->kernel_op_id == 0 ||
            group->completion_event_id == 0 ||
            group->completion_event_id > UINT32_MAX ||
            group->descriptor_ids.count == 0 ||
            !spanFits(group->descriptor_ids.begin, group->descriptor_ids.count,
                      program.semantic_u64_values.size()))
            return fail(E_DMA_RANGE,
                        "descriptor group fields are invalid", error);
        auto operation = indices.operations.find(group->kernel_op_id);
        auto command = group->command_id > UINT32_MAX ?
            indices.commands.end() : indices.commands.find(
                uint32_t(group->command_id));
        const DmaCommandProjection *projected =
            group->command_id > UINT32_MAX ? nullptr :
            projection.dma(uint32_t(group->command_id));
        if (operation == indices.operations.end() ||
            operation->second->opcode != KernelOpcode::DMA ||
            command == indices.commands.end() ||
            !isDmaOpcode(command->second->opcode) ||
            command->second->signal_event != group->completion_event_id ||
            !projected || projected->operationId() != group->kernel_op_id ||
            projected->descriptorGroupId() != group->group_id)
            return fail(E_DMA_RANGE,
                        "descriptor group contradicts command projection",
                        error);
        if (!indices.groupsByOperation.emplace(
                group->kernel_op_id, group).second ||
            !indices.groupsByCommand.emplace(group->command_id, group).second)
            return fail(E_ABI_DUPLICATE,
                        "DMA operation or command owns multiple groups",
                        error);
        const uint64_t descriptorEnd =
            uint64_t(group->descriptor_ids.begin) +
            uint64_t(group->descriptor_ids.count);
        for (uint64_t index = group->descriptor_ids.begin;
             index < descriptorEnd; ++index) {
            const uint64_t descriptorId =
                program.semantic_u64_values[size_t(index)];
            if (descriptorId == 0 || descriptorId > UINT32_MAX ||
                indices.descriptors.count(uint32_t(descriptorId)) == 0)
                return fail(E_DMA_RANGE,
                            "descriptor group references an unknown descriptor",
                            error);
            const auto [owner, inserted] = descriptorOwnerGroups.emplace(
                uint32_t(descriptorId), group->group_id);
            if (!inserted && owner->second != group->group_id)
                return fail(E_ABI_DUPLICATE,
                            "descriptor belongs to multiple groups", error);
            const DmaDescriptor &descriptor =
                *indices.descriptors.at(uint32_t(descriptorId));
            if (descriptor.command_id != group->command_id ||
                descriptor.completion_event != group->completion_event_id)
                return fail(E_DMA_RANGE,
                            "descriptor contradicts its group owner", error);
        }
    }
    if (descriptorOwnerGroups.size() != indices.descriptors.size())
        return fail(E_DMA_RANGE,
                    "descriptor groups do not cover every descriptor", error);
    for (const auto &[commandId, command] : indices.commands) {
        if (isDmaOpcode(command->opcode) !=
            (indices.groupsByCommand.count(commandId) != 0))
            return fail(E_DMA_RANGE,
                        "DMA command and group coverage differ", error);
    }

    for (uint64_t offset = 0; offset < root.endpoint_uses.count; ++offset) {
        const DescriptorEndpointUse *endpoint = semanticRow(
            program.semantic_references[size_t(root.endpoint_uses.begin +
                                               offset)],
            program.semantic_tables.descriptor_endpoint_use_rows);
        if (!endpoint || endpoint->ref_id == 0 ||
            endpoint->ref_id > UINT32_MAX)
            return fail(E_RELOCATION,
                        "descriptor endpoint ref identity is invalid", error);
        if (endpoint->descriptor_id == 0 ||
            endpoint->descriptor_id > UINT32_MAX ||
            indices.descriptors.count(uint32_t(endpoint->descriptor_id)) == 0)
            return fail(E_DMA_RANGE,
                        "descriptor endpoint use is invalid", error);
        EndpointUses &uses =
            indices.endpoints[uint32_t(endpoint->descriptor_id)];
        if (endpoint->side == EndpointSide::SRC) {
            if (uses.source)
                return fail(E_ABI_DUPLICATE,
                            "descriptor source use is duplicated", error);
            uses.source = endpoint;
        } else if (endpoint->side == EndpointSide::DST) {
            if (uses.destination)
                return fail(E_ABI_DUPLICATE,
                            "descriptor destination use is duplicated", error);
            uses.destination = endpoint;
        } else {
            return fail(E_DMA_RANGE,
                        "descriptor endpoint side is invalid", error);
        }
    }
    if (indices.endpoints.size() != indices.descriptors.size())
        return fail(E_DMA_RANGE,
                    "endpoint uses do not cover every descriptor", error);
    for (const auto &entry : indices.endpoints) {
        const EndpointUses &uses = entry.second;
        if (!uses.source || !uses.destination)
            return fail(E_DMA_RANGE,
                        "descriptor endpoint uses are incomplete", error);
    }
    return true;
}

struct EndpointProjection
{
    uint64_t width = 0;
    uint64_t begin = 0;
    uint64_t end = 0;
    const BufferView *view = nullptr;
    const BufferObject *object = nullptr;
    const TensorShard *logicalShard = nullptr;
    const KernelTensor *tensor = nullptr;
    const Shard *runtimeShard = nullptr;
    std::vector<uint64_t> byteStrides;
};

bool
deriveEndpoint(
    const RuntimeArch &arch, const Membership &member,
    const VerifiedProgramBacking &backing, const DmaDescriptor &descriptor,
    const DmaEndpoint &endpoint, const GeometryAccessPieceFact &piece,
    EndpointProjection &out, uint64_t &address, MeshLoadError &error)
{
    out.view = &piece.access().view();
    out.object = &piece.access().object();
    out.logicalShard = &piece.access().logicalShard();
    out.tensor = &piece.access().tensor();
    out.width = piece.elementWidthBytes();
    if (!member.views->contains(out.view->view_id))
        return fail(E_DMA_RANGE, "endpoint view crosses its variant", error);
    if (!member.objects->contains(out.object->object_id) ||
        !member.logicalShards->contains(out.logicalShard->shard_id))
        return fail(E_DMA_RANGE,
                    "endpoint view source crosses its variant", error);
    if (!member.tensors->contains(out.tensor->tensor_id))
        return fail(E_DMA_RANGE, "endpoint tensor is invalid", error);
    out.runtimeShard = backing.resident(out.view->view_id);
    if (!out.runtimeShard)
        return fail(E_ABI_BOUNDS, "endpoint resident backing is missing",
                    error);
    out.byteStrides = piece.objectByteStrides();
    out.begin = piece.objectByteOffset();
    out.end = piece.objectByteEnd();
    if (endpoint.tensor_id != out.tensor->tensor_id ||
        endpoint.shard_id != out.runtimeShard->shard_id ||
        endpoint.offset_bytes != out.begin)
        return fail(E_DMA_RANGE,
                    "ABI endpoint is not the access projection", error);

    const Allocation *allocation = backing.local(out.object->object_id);
    const ExternalBackingAddressFact *external = nullptr;
    if (allocation) {
        if ((endpoint.memory_space != kMemorySpaceCORE_SRAM &&
             endpoint.memory_space != kMemorySpacePEER_SRAM) ||
            endpoint.owner_core != out.object->owner_core)
            return fail(E_DMA_RANGE,
                        "local endpoint memory projection is invalid", error);
    } else {
        external = backing.external(out.object->object_id);
        if (!external)
            return fail(E_RELOCATION, "endpoint backing is missing", error);
        const Binding &binding = external->binding();
        if (endpoint.region_id != binding.region_id ||
            endpoint.owner_core != binding.owner_core ||
            endpoint.memory_space != uint16_t(out.object->memory_space))
            return fail(E_DMA_RANGE,
                        "external endpoint memory projection is invalid",
                        error);
    }

    const RuntimeArch::Region *endpointRegion = arch.region(endpoint.region_id);
    if (!endpointRegion)
        return fail(E_RELOCATION, "endpoint region is unknown", error);
    uint16_t expectedSpace = 0;
    if (endpointRegion->is_sram_aperture) {
        if (!arch.hasCore(endpoint.owner_core))
            return fail(E_RELOCATION, "endpoint SRAM core is unknown", error);
        expectedSpace = endpoint.owner_core == descriptor.owner_core
            ? kMemorySpaceCORE_SRAM : kMemorySpacePEER_SRAM;
    } else {
        if (endpoint.owner_core != UINT16_MAX)
            return fail(E_RELOCATION,
                        "endpoint non-SRAM core is invalid", error);
        if (endpointRegion->kind == RuntimeArch::Region::kKindHbm)
            expectedSpace = kMemorySpaceHBM;
        else if (endpointRegion->kind == RuntimeArch::Region::kKindHostShared)
            expectedSpace = kMemorySpaceHOST_SHARED;
        else
            return fail(E_RELOCATION, "endpoint region kind is invalid", error);
    }
    if (endpoint.memory_space != expectedSpace)
        return fail(E_RELOCATION,
                    "endpoint memory-space assertion is false", error);

    if (allocation) {
        uint64_t regionOffset = 0;
        if (!add(allocation->offset_bytes, out.begin, regionOffset) ||
            regionOffset > endpointRegion->tile_bytes ||
            out.end - out.begin > endpointRegion->tile_bytes - regionOffset)
            return fail(E_DMA_RANGE,
                        "local endpoint exceeds its region", error);
        bool overflow = false;
        const uint64_t tileBase = checkedMulAdd(
            endpoint.owner_core, endpointRegion->tile_stride,
            endpointRegion->base, overflow);
        if (overflow || !add(tileBase, regionOffset, address))
            return fail(E_ABI_OVERFLOW,
                        "local endpoint address overflows", error);
        return true;
    }

    const Binding &binding = external->binding();
    if (out.begin > binding.allocation_size_bytes ||
        out.end - out.begin > binding.allocation_size_bytes - out.begin)
        return fail(E_DMA_RANGE, "external endpoint exceeds its region", error);
    if (!add(external->allocationAddress(), out.begin, address))
        return fail(E_ABI_OVERFLOW,
                    "external endpoint address overflows", error);
    return true;
}

bool
verifyGeometry(
    const GeometryAccessPieceFact &sourceAccess,
    const GeometryAccessPieceFact &destinationAccess,
    const EndpointProjection &source,
    const EndpointProjection &destination,
    const DmaDescriptor &descriptor, uint64_t elementCount,
    MeshLoadError &error)
{
    if (source.width != destination.width)
        return fail(E_DMA_RANGE,
                    "descriptor endpoint element widths differ", error);
    if (sourceAccess.logicalElementCount() != elementCount ||
        destinationAccess.logicalElementCount() != elementCount)
        return fail(E_DMA_RANGE,
                    "descriptor piece count is inconsistent", error);
    uint64_t useful = 0;
    if (!multiply(elementCount, source.width, useful))
        return fail(E_ABI_OVERFLOW,
                    "descriptor useful byte count overflows", error);
    const bool empty = elementCount == 0;
    if (empty) {
        if (sourceAccess.shape().empty() || destinationAccess.shape().empty())
            return fail(E_ABI_BOUNDS,
                        "empty DMA region has no rank", error);
        uint64_t sourceRows = 1;
        uint64_t destinationRows = 1;
        for (size_t index = 0;
             index + 1 < sourceAccess.shape().size(); ++index)
            if (!multiply(sourceRows,
                          sourceAccess.shape()[index], sourceRows))
                return fail(E_ABI_OVERFLOW,
                            "empty DMA rows overflow", error);
        for (size_t index = 0;
             index + 1 < destinationAccess.shape().size(); ++index)
            if (!multiply(destinationRows,
                          destinationAccess.shape()[index],
                          destinationRows))
                return fail(E_ABI_OVERFLOW,
                            "empty DMA rows overflow", error);
        uint64_t sourceRowBytes = 0;
        uint64_t destinationRowBytes = 0;
        if (!multiply(sourceAccess.shape().back(), source.width,
                      sourceRowBytes) ||
            !multiply(destinationAccess.shape().back(),
                      destination.width, destinationRowBytes))
            return fail(E_ABI_OVERFLOW,
                        "empty DMA row bytes overflow", error);
        if (sourceRows != destinationRows ||
            sourceRowBytes != destinationRowBytes ||
            descriptor.rows != sourceRows ||
            descriptor.row_bytes != sourceRowBytes ||
            descriptor.src_stride_bytes != sourceRowBytes ||
            descriptor.dst_stride_bytes != sourceRowBytes ||
            descriptor.useful_bytes != 0 ||
            descriptor.physical_storage_bytes != 0)
            return fail(E_DMA_RANGE,
                        "empty descriptor geometry is invalid", error);
        return true;
    }
    if (descriptor.rows == 0 || descriptor.row_bytes == 0)
        return fail(E_DMA_RANGE,
                    "descriptor traversal geometry is empty", error);
    uint64_t descriptorUseful = 0;
    if (!multiply(descriptor.rows, descriptor.row_bytes,
                  descriptorUseful))
        return fail(E_ABI_OVERFLOW,
                    "descriptor traversal bytes overflow", error);
    if (descriptorUseful != useful || descriptor.useful_bytes != useful)
        return fail(E_DMA_RANGE,
                    "descriptor traversal differs from its access piece",
                    error);
    std::vector<uint64_t> sourceShape = sourceAccess.shape();
    std::vector<uint64_t> destinationShape = destinationAccess.shape();
    sourceShape.push_back(source.width);
    destinationShape.push_back(destination.width);
    std::vector<uint64_t> sourceStrides = source.byteStrides;
    std::vector<uint64_t> destinationStrides = destination.byteStrides;
    sourceStrides.push_back(1);
    destinationStrides.push_back(1);
    std::vector<AffineAxis> sourceAxes;
    std::vector<AffineAxis> destinationAxes;
    std::vector<AffineAxis> descriptorSourceAxes;
    std::vector<AffineAxis> descriptorDestinationAxes;
    if (!compactAffineAxes(sourceShape, sourceStrides, sourceAxes, error) ||
        !compactAffineAxes(destinationShape, destinationStrides,
                           destinationAxes, error) ||
        !compactAffineAxes(
            {descriptor.rows, descriptor.row_bytes},
            {descriptor.src_stride_bytes, 1}, descriptorSourceAxes,
            error) ||
        !compactAffineAxes(
            {descriptor.rows, descriptor.row_bytes},
            {descriptor.dst_stride_bytes, 1}, descriptorDestinationAxes,
            error))
        return false;
    const uint64_t sourceSpan = source.end - source.begin;
    const uint64_t destinationSpan = destination.end - destination.begin;
    if (sourceAxes != descriptorSourceAxes ||
        destinationAxes != descriptorDestinationAxes ||
        descriptor.physical_storage_bytes !=
            std::max(sourceSpan, destinationSpan))
        return fail(E_DMA_RANGE,
                    "descriptor geometry is not its access traversal",
                    error);
    return true;
}

bool
verifyOperationAttrs(
    const DecodedProgram &program, const KernelOp &operation,
    const DmaAttrs &attrs, const Command &command, MeshLoadError &error)
{
    const uint64_t maxCore = std::numeric_limits<uint16_t>::max();
    if (operation.owner_core > maxCore || attrs.issuing_core > maxCore ||
        attrs.source_core > maxCore || attrs.destination_core > maxCore ||
        operation.owner_core != attrs.issuing_core)
        return fail(E_DMA_RANGE, "DMA core identity is invalid", error);
    std::vector<uint8_t> fillPattern;
    if (!semanticBytes(program, attrs.fill_pattern, fillPattern, error))
        return false;
    if (attrs.kind == semantic_abi::DmaKind::LOCAL_FILL) {
        if (fillPattern.empty() || fillPattern.size() > 16 ||
            attrs.transfer_id != 0 ||
            attrs.source_core != attrs.issuing_core ||
            attrs.destination_core != attrs.issuing_core)
            return fail(E_DMA_RANGE,
                        "local fill attributes are invalid", error);
        if (fillPattern.size() > sizeof(uint64_t))
            return fail(E_ABI_BOUNDS,
                        "DMA fill pattern exceeds the transport width",
                        error);
        uint64_t pattern = 0;
        for (size_t index = 0; index < fillPattern.size(); ++index)
            pattern |= uint64_t(fillPattern[index]) << (index * 8);
        if (command.attr_index == 0 ||
            command.attr_index > program.transport.op_attrs.size())
            return fail(E_ABI_ENUM,
                        "DMA fill transport attribute is missing", error);
        const DecodedAttr &transport =
            program.transport.op_attrs[command.attr_index - 1];
        const FillV1 *fill = transport.as<FillV1>();
        if (transport.kind != kAttrKindFILL_V1 || !fill ||
            fill->pattern != pattern)
            return fail(E_ABI_ENUM,
                        "DMA fill pattern projection differs", error);
        return true;
    }
    if (!fillPattern.empty())
        return fail(E_DMA_RANGE,
                    "non-fill DMA carries a fill pattern", error);
    if (attrs.kind == semantic_abi::DmaKind::LOAD ||
        attrs.kind == semantic_abi::DmaKind::PREFETCH) {
        if (attrs.source_core != maxCore ||
            attrs.destination_core != attrs.issuing_core)
            return fail(E_DMA_RANGE,
                        "load core identities are invalid", error);
    } else if (attrs.kind == semantic_abi::DmaKind::STORE) {
        if (attrs.source_core != attrs.issuing_core ||
            attrs.destination_core != maxCore)
            return fail(E_DMA_RANGE,
                        "store core identities are invalid", error);
    } else if (attrs.kind == semantic_abi::DmaKind::P2P_PUSH) {
        if (attrs.source_core != attrs.issuing_core ||
            attrs.transfer_id == 0)
            return fail(E_P2P_UNMATCHED,
                        "P2P attributes are invalid", error);
    }
    return true;
}

bool
verifyMovementParents(
    const KernelOp &operation, const DmaAttrs &attrs,
    const VerifiedProgramGeometry &geometry, MeshLoadError &error)
{
    if (attrs.kind == semantic_abi::DmaKind::LOCAL_FILL)
        return true;
    if (operation.reads.count == 0 ||
        operation.reads.count != operation.writes.count ||
        operation.reads.count > std::numeric_limits<size_t>::max())
        return fail(E_DMA_RANGE,
                    "DMA accesses do not form ordered parent pairs", error);

    uint64_t sourceObjectId = 0;
    uint64_t destinationObjectId = 0;
    for (size_t ordinal = 0; ordinal < size_t(operation.reads.count);
         ++ordinal) {
        const GeometryAccessFact *source = geometry.access(
            operation.op_id, GeometryAccessRole::Read, ordinal);
        const GeometryAccessFact *destination = geometry.access(
            operation.op_id, GeometryAccessRole::Write, ordinal);
        if (!source || !destination)
            return fail(E_DMA_RANGE, "DMA parent access is missing", error);
        bool sourceInjective = false;
        if (!geometry.accessMapsInjectively(
                operation.op_id, GeometryAccessRole::Read, ordinal,
                sourceInjective, error))
            return false;
        if (!sourceInjective)
            return fail(E_DMA_RANGE,
                        "DMA source access is not injective", error);
        if (ordinal == 0) {
            sourceObjectId = source->object().object_id;
            destinationObjectId = destination->object().object_id;
        } else if (sourceObjectId != source->object().object_id ||
                   destinationObjectId != destination->object().object_id) {
            return fail(E_DMA_RANGE,
                        "DMA pieces do not share parent objects", error);
        }
        if (source->tensor().alias_root_tensor_id !=
                destination->tensor().alias_root_tensor_id ||
            source->tensor().dtype != destination->tensor().dtype ||
            source->globalOrigin() != destination->globalOrigin() ||
            source->shape() != destination->shape() ||
            source->steps() != destination->steps())
            return fail(E_DMA_RANGE,
                        "DMA source and destination parents differ", error);
    }
    return true;
}

bool
verifyDescriptorKind(
    const RuntimeArch &arch, const KernelOp &operation,
    const DmaAttrs &attrs, const Command &command,
    const DmaDescriptor &descriptor, MeshLoadError &error)
{
    const uint16_t expectedKind = commandDmaKind(command.opcode);
    if (expectedKind == 0 || descriptor.kind != expectedKind ||
        descriptor.kind != uint16_t(attrs.kind))
        return fail(E_ABI_ENUM, "descriptor kind projection differs", error);
    if (operation.owner_core > UINT16_MAX || attrs.issuing_core > UINT16_MAX ||
        attrs.transfer_id > UINT32_MAX ||
        command.core_id != operation.owner_core ||
        descriptor.owner_core != operation.owner_core ||
        attrs.issuing_core != operation.owner_core ||
        descriptor.transfer_id != attrs.transfer_id)
        return fail(E_DMA_RANGE, "descriptor owner projection differs", error);
    uint64_t expectedBurst = arch.axi_max_burst_beats;
    if (attrs.presence_mask &
        kDmaAttrsMaxBurstBeatsField.optional_presence_mask) {
        if (attrs.max_burst_beats == 0 || attrs.max_burst_beats > 256)
            return fail(E_ABI_BOUNDS,
                        "DMA burst limit is outside the supported range",
                        error);
        if (attrs.kind == mesh_abi::semantic_abi::DmaKind::LOCAL_FILL)
            return fail(E_DMA_RANGE,
                        "local fill declares an AXI burst limit", error);
        if (attrs.max_burst_beats > arch.axi_max_burst_beats)
            return fail(E_CAPABILITY_MISMATCH,
                        "DMA burst limit exceeds the architecture", error);
        expectedBurst = attrs.max_burst_beats;
    }
    if (expectedBurst > UINT16_MAX ||
        descriptor.max_burst_beats != expectedBurst)
        return fail(E_DMA_RANGE,
                    "descriptor burst limit differs from its Kernel source",
                    error);
    return true;
}

}

uint64_t
VerifiedDmaDomain::sourceAddress(uint32_t descriptor_id) const
{
    return endpointAddresses.at(descriptor_id)[0];
}

uint64_t
VerifiedDmaDomain::destinationAddress(uint32_t descriptor_id) const
{
    return endpointAddresses.at(descriptor_id)[1];
}

const DmaCompletionFact *
VerifiedDmaDomain::descriptorCompletion(uint32_t descriptorId) const
{
    const auto found = descriptorCompletions.find(descriptorId);
    return found == descriptorCompletions.end() ? nullptr : &found->second;
}

const DescriptorBindingFact *
VerifiedDmaDomain::descriptorBinding(uint32_t descriptorId, bool source) const
{
    const auto found = descriptorBindings.find(std::make_pair(descriptorId, source));
    return found == descriptorBindings.end() ? nullptr : &found->second;
}

const P2PTransferFact *
VerifiedDmaDomain::p2pTransfer(uint32_t transferId) const
{
    const auto found = p2pTransfers.find(transferId);
    return found == p2pTransfers.end() ? nullptr : &found->second;
}

const std::vector<uint32_t> *
VerifiedDmaDomain::completionDescriptors(
    uint32_t producerCommandId, uint32_t completionEventId) const
{
    const auto found = descriptorsByCompletion.find(
        {producerCommandId, completionEventId});
    return found == descriptorsByCompletion.end() ? nullptr : &found->second;
}

const std::vector<uint32_t> *
VerifiedDmaDomain::commandDescriptors(uint32_t commandId) const
{
    const auto found = descriptorsByCommand.find(commandId);
    return found == descriptorsByCommand.end() ? nullptr : &found->second;
}

size_t
VerifiedDmaDomain::p2pTransferCount() const
{
    return p2pTransfers.size();
}

bool
VerifiedDmaDomain::matches(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const RuntimeArch &arch, const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedCommandProjectionFacts &projection) const
{
    return identity_ && backingIdentity_ &&
        projection.matches(program, context, arch) &&
        backing.matches(program, context, arch, geometry) &&
        backing.matchesInvocation(backingIdentity_) &&
        program_ == &program && context_ == &context && arch_ == &arch &&
        backing_ == &backing && projection_ == &projection;
}

bool
verifyDmaSemanticDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedCommandProjectionFacts &projection,
    VerifiedDmaDomain &out, MeshLoadError &error)
{
    error = {};
    if (!context.matches(program) || !geometry.matches(program, context) ||
        !backing.matches(program, context, arch, geometry) ||
        !projection.matches(program, context, arch))
        return fail(E_ABI_BOUNDS, "DMA facts belong to another Program", error);
    const ProgramSemantics &root = context.root();
    ProgramIndices indices(context);
    if (!indexGroupsAndEndpoints(program, root, projection, indices, error))
        return false;

    VerifiedDmaDomain candidate;
    candidate.program_ = &program;
    candidate.context_ = &context;
    candidate.arch_ = &arch;
    candidate.projection_ = &projection;
    candidate.backing_ = &backing;
    candidate.backingIdentity_ = backing.invocationIdentity();
    candidate.identity_ = std::make_shared<DmaFactIdentity>();
    if (!candidate.backingIdentity_)
        return fail(E_ABI_BOUNDS, "backing facts are not admitted", error);
    std::set<uint64_t> visitedOperations;
    std::set<uint64_t> visitedGroups;
    std::set<uint32_t> visitedDescriptors;
    std::set<uint64_t> visitedEndpointUses;
    for (const auto &entry : context.variants()) {
        const uint64_t variantId = entry.first;
        Membership member;
        if (!membershipView(context, variantId, member))
            return fail(E_ABI_BOUNDS, "variant membership is invalid", error);
        const uint64_t operationEnd =
            member.operations->first + member.operations->count;
        for (uint64_t operationId = member.operations->first;
             operationId < operationEnd; ++operationId) {
            auto operationIt = indices.operations.find(operationId);
            if (operationIt == indices.operations.end())
                return fail(E_ABI_BOUNDS,
                            "variant operation is missing", error);
            const KernelOp &operation = *operationIt->second;
            if (operation.opcode != KernelOpcode::DMA)
                continue;
            if (!visitedOperations.insert(operation.op_id).second)
                return fail(E_ABI_DUPLICATE,
                            "DMA operation belongs to multiple variants",
                            error);
            auto groupIt = indices.groupsByOperation.find(operation.op_id);
            if (groupIt == indices.groupsByOperation.end())
                return fail(E_DMA_RANGE,
                            "DMA operation has no descriptor group", error);
            const DescriptorGroup &group = *groupIt->second;
            if (!member.commands->contains(group.command_id) ||
                !member.events->contains(group.completion_event_id) ||
                !member.commandSemantics->contains(group.command_id))
                return fail(E_DMA_RANGE,
                            "descriptor group crosses its variant", error);
            if (!visitedGroups.insert(group.group_id).second)
                return fail(E_ABI_DUPLICATE,
                            "descriptor group belongs to multiple variants",
                            error);
            const DmaAttrs *attrs = semanticRow(
                operation.attrs, program.semantic_tables.dma_attrs_rows);
            const Command &command =
                *indices.commands.at(uint32_t(group.command_id));
            if (!attrs)
                return fail(E_DMA_RANGE,
                            "DMA operation attributes are invalid", error);
            if (!verifyOperationAttrs(
                    program, operation, *attrs, command, error))
                return false;
            if ((operation.reads.count != 0 &&
                 operation.reads.count != operation.writes.count) ||
                (operation.reads.count == 0 && operation.writes.count != 1) ||
                !spanFits(operation.reads.begin, operation.reads.count,
                          program.semantic_references.size()) ||
                !spanFits(operation.writes.begin, operation.writes.count,
                          program.semantic_references.size()))
                return fail(E_DMA_RANGE,
                            "DMA accesses do not form ordered pairs", error);
            if (!verifyMovementParents(operation, *attrs, geometry, error))
                return false;
            const uint64_t pairCount = operation.reads.count == 0
                ? 1 : operation.reads.count;
            uint64_t activePair = 0;
            uint64_t cursor = 0;
            uint64_t p2pWrittenBytes = 0;
            const uint64_t descriptorEnd =
                uint64_t(group.descriptor_ids.begin) +
                uint64_t(group.descriptor_ids.count);
            std::vector<uint32_t> groupDescriptors;
            for (uint64_t descriptorIndex = group.descriptor_ids.begin;
                 descriptorIndex < descriptorEnd; ++descriptorIndex) {
                if (activePair >= pairCount)
                    return fail(E_DMA_RANGE,
                                "descriptor group has extra pieces", error);
                const uint64_t descriptorId64 =
                    program.semantic_u64_values[size_t(descriptorIndex)];
                if (descriptorId64 > UINT32_MAX ||
                    !member.descriptors->contains(descriptorId64))
                    return fail(E_DMA_RANGE,
                                "descriptor group crosses its variant",
                                error);
                const uint32_t descriptorId = uint32_t(descriptorId64);
                const DmaDescriptor &descriptor =
                    *indices.descriptors.at(descriptorId);
                if (!verifyDescriptorKind(
                        arch, operation, *attrs, command, descriptor, error))
                    return false;
                if (descriptor.completion_event != command.signal_event ||
                    indices.events.count(descriptor.completion_event) == 0)
                    return fail(E_DMA_RANGE,
                                "descriptor completion is invalid", error);
                const EndpointUses &uses = indices.endpoints.at(descriptorId);
                const bool fill = operation.reads.count == 0;
                AccessProjection sourceAccess;
                AccessProjection destinationAccess;
                if (!accessProjection(
                        program, geometry, operation, *uses.source, !fill,
                        activePair,
                        sourceAccess, error) ||
                    !accessProjection(
                        program, geometry, operation, *uses.destination,
                        false, activePair, destinationAccess, error))
                    return false;
                uint64_t sourceStart = 0;
                uint64_t sourceCount = 0;
                uint64_t destinationStart = 0;
                uint64_t destinationCount = 0;
                if (!orderedRegionInterval(
                        sourceAccess.parentValues, sourceAccess.pieceValues,
                        sourceStart, sourceCount, error) ||
                    !orderedRegionInterval(
                        destinationAccess.parentValues,
                        destinationAccess.pieceValues, destinationStart,
                        destinationCount, error))
                    return false;
                if (sourceStart != cursor || destinationStart != cursor ||
                    sourceCount != destinationCount)
                    return fail(E_DMA_RANGE,
                                "descriptor pieces do not preserve order",
                                error);
                if (!visitedDescriptors.insert(descriptorId).second)
                    return fail(E_DMA_RANGE,
                                "descriptor piece is repeated", error);
                EndpointProjection source;
                EndpointProjection destination;
                uint64_t sourceAddress = 0;
                uint64_t destinationAddress = 0;
                if (!deriveEndpoint(
                        arch, member, backing, descriptor, descriptor.src,
                        *sourceAccess.piece, source,
                        sourceAddress,
                        error) ||
                    !deriveEndpoint(
                        arch, member, backing, descriptor, descriptor.dst,
                        *destinationAccess.piece, destination,
                        destinationAddress, error) ||
                    !verifyGeometry(
                        *sourceAccess.piece, *destinationAccess.piece, source,
                        destination, descriptor, sourceCount, error))
                    return false;
                if (attrs->kind ==
                        mesh_abi::semantic_abi::DmaKind::P2P_PUSH &&
                    !add(p2pWrittenBytes, descriptor.useful_bytes,
                         p2pWrittenBytes))
                    return fail(E_ABI_OVERFLOW,
                                "P2P group byte count overflows", error);
                const bool sourceRemote =
                    descriptor.src.memory_space == kMemorySpaceHBM ||
                    descriptor.src.memory_space == kMemorySpaceHOST_SHARED;
                const bool destinationRemote =
                    descriptor.dst.memory_space == kMemorySpaceHBM ||
                    descriptor.dst.memory_space == kMemorySpaceHOST_SHARED;
                const bool sourceLocal =
                    descriptor.src.memory_space == kMemorySpaceCORE_SRAM &&
                    descriptor.src.owner_core == descriptor.owner_core;
                const bool destinationLocal =
                    descriptor.dst.memory_space == kMemorySpaceCORE_SRAM &&
                    descriptor.dst.owner_core == descriptor.owner_core;
                if ((descriptor.kind == kDmaKindLOAD ||
                     descriptor.kind == kDmaKindPREFETCH) &&
                    (!sourceRemote || !destinationLocal))
                    return fail(E_DMA_RANGE,
                                "load endpoint direction is invalid", error);
                if (descriptor.kind == kDmaKindSTORE &&
                    (!sourceLocal || !destinationRemote))
                    return fail(E_DMA_RANGE,
                                "store endpoint direction is invalid", error);
                if (descriptor.kind == kDmaKindLOCAL_FILL &&
                    (!sourceLocal || !destinationLocal))
                    return fail(E_DMA_RANGE,
                                "fill endpoint direction is invalid", error);
                if (descriptor.kind == kDmaKindP2P_PUSH) {
                    if (attrs->source_core != source.object->owner_core ||
                        attrs->destination_core !=
                            destination.object->owner_core)
                        return fail(E_P2P_UNMATCHED,
                                    "P2P semantic endpoints are invalid",
                                    error);
                    if (!sourceLocal ||
                        descriptor.dst.memory_space != kMemorySpacePEER_SRAM ||
                        descriptor.dst.owner_core == descriptor.owner_core)
                        return fail(E_DMA_RANGE,
                                    "P2P endpoint direction is invalid",
                                    error);
                }
                DmaCompletionFact completion;
                completion.producerCommandId_ = uint32_t(group.command_id);
                completion.completionEventId_ =
                    uint32_t(group.completion_event_id);
                if (!candidate.descriptorCompletions.emplace(
                        descriptorId, completion).second)
                    return fail(E_DMA_RANGE,
                                "descriptor completion is duplicated", error);
                groupDescriptors.push_back(descriptorId);
                candidate.endpointAddresses.emplace(
                    descriptorId,
                    std::array<uint64_t, 2>{sourceAddress,
                                            destinationAddress});
                const ExternalBackingAddressFact *sourceExternal =
                    backing.external(source.object->object_id);
                const ExternalBackingAddressFact *destinationExternal =
                    backing.external(destination.object->object_id);
                DescriptorBindingFact sourceBinding;
                sourceBinding.slot_id_ =
                    sourceExternal
                        ? uint32_t(sourceExternal->binding().slot_id)
                        : 0;
                sourceBinding.begin_ = source.begin;
                DescriptorBindingFact destinationBinding;
                destinationBinding.slot_id_ =
                    destinationExternal
                        ? uint32_t(destinationExternal->binding().slot_id)
                        : 0;
                destinationBinding.begin_ = destination.begin;
                candidate.descriptorBindings.emplace(
                    std::make_pair(descriptorId, true), sourceBinding);
                candidate.descriptorBindings.emplace(
                    std::make_pair(descriptorId, false), destinationBinding);
                visitedEndpointUses.insert(uses.source->ref_id);
                visitedEndpointUses.insert(uses.destination->ref_id);
                if (!add(cursor, sourceCount, cursor))
                    return fail(E_ABI_OVERFLOW,
                                "descriptor coverage overflows", error);
                uint64_t pairElements = 0;
                uint64_t ignoredStart = 0;
                if (!orderedRegionInterval(
                        sourceAccess.parentValues, sourceAccess.parentValues,
                        ignoredStart, pairElements, error))
                    return false;
                if (cursor == pairElements) {
                    ++activePair;
                    cursor = 0;
                } else if (cursor > pairElements) {
                    return fail(E_DMA_RANGE,
                                "descriptor pieces exceed their access pair",
                                error);
                }
            }
            if (activePair != pairCount || cursor != 0)
                return fail(E_DMA_RANGE,
                            "descriptor group does not cover every access",
                            error);
            if (attrs->kind ==
                    mesh_abi::semantic_abi::DmaKind::P2P_PUSH) {
                if (attrs->transfer_id > UINT32_MAX)
                    return fail(E_DMA_RANGE,
                                "P2P transfer identity is invalid", error);
                P2PTransferFact transfer;
                transfer.completion_.producerCommandId_ = uint32_t(
                    group.command_id);
                transfer.completion_.completionEventId_ = uint32_t(
                    group.completion_event_id);
                transfer.sourceCore_ = uint16_t(attrs->source_core);
                transfer.destinationCore_ = uint16_t(attrs->destination_core);
                transfer.expectedBytes_ = p2pWrittenBytes;
                if (!candidate.p2pTransfers.emplace(
                        uint32_t(attrs->transfer_id), std::move(transfer)).second)
                    return fail(E_P2P_UNMATCHED,
                                "P2P transfer identity is duplicated",
                                error);
            }
            if (!candidate.descriptorsByCommand.emplace(
                    uint32_t(group.command_id), groupDescriptors).second)
                return fail(E_DMA_RANGE,
                            "command descriptor group is duplicated", error);
            if (!candidate.descriptorsByCompletion.emplace(
                    std::make_pair(uint32_t(group.command_id),
                                   uint32_t(group.completion_event_id)),
                    std::move(groupDescriptors)).second)
                return fail(E_DMA_RANGE,
                            "DMA completion group is duplicated", error);
        }
    }
    if (visitedOperations.size() != indices.groupsByOperation.size() ||
        visitedGroups.size() != indices.groupsByOperation.size() ||
        visitedDescriptors.size() != indices.descriptors.size() ||
        visitedEndpointUses.size() != root.endpoint_uses.count ||
        candidate.endpointAddresses.size() != indices.descriptors.size() ||
        candidate.descriptorCompletions.size() != indices.descriptors.size())
        return fail(E_DMA_RANGE,
                    "DMA semantic projection is not exactly covered", error);
    out = std::move(candidate);
    error = {};
    return true;
}

}
}

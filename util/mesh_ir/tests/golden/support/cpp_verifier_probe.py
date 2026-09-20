import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="session")
def verifier_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_ir_verifier")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <algorithm>
#include <iostream>
#include <fstream>
#include <iterator>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_computation_verifier.hh"
#include "dev/ai_mesh/mesh_ir_compute_verifier.hh"
#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dtype.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"
#include "dev/ai_mesh/mesh_ir_logical_computation_verifier.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"
#include "dev/ai_mesh/mesh_ir_semantic_context.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

using namespace gem5::ai_mesh;

void
refreshSemanticChecksum(DecodedProgram &program)
{
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream stream(&buffer);
    MeshLoadError error;
    if (!mesh_binary_detail::writeProgramCanonical(
            program, mesh_abi::CanonicalProjection::Semantic,
            stream, error))
        std::exit(9);
    program.metadata.semantic_sha256 = buffer.digest();
}

RuntimeArch
testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.sram_banks = 16;
    arch.sram_alignment = 64;
    arch.axi_data_bytes = 32;
    arch.axi_max_burst_beats = 16;
    arch.regions = {
        {0, 0x800000000, 0x400000000, 0, 0,
         RuntimeArch::Region::kKindHbm, false},
        {1, 0x100000000, 0x10000000, 0, 0,
         RuntimeArch::Region::kKindHostShared, false},
        {2, 0x400000000, 0x800000, 0x400000, 0x200000,
         RuntimeArch::Region::kKindSramAperture, true},
    };
    return arch;
}

bool
buildControlFacts(
    const DecodedProgram &program, const RuntimeArch &arch,
    ProgramSemanticContext &context, VerifiedProgramGeometry &geometry,
    VerifiedProgramBacking &backing, VerifiedCommandProjectionFacts &projection,
    VerifiedDmaDomain &dma, VerifiedIntrinsicDependencyFacts &intrinsic,
    VerifiedControlDependencyFacts &facts,
    MeshLoadError &error)
{
    VerifiedMatrixContractionFacts matrix;
    return buildProgramSemanticContext(program, context, error) &&
        verifyProgramGeometry(program, context, geometry, error) &&
        verifyProgramBacking(program, arch, context, geometry, backing, error) &&
        verifyProgramCommandProjection(program, arch, context, projection, error) &&
        verifyProgramComputationDomain(
            program, context, geometry, matrix, error) &&
        verifyDmaSemanticDomain(
            program, arch, context, geometry, backing, projection, dma, error) &&
        verifyProgramIntrinsicDependencyFacts(program, context, intrinsic, error) &&
        verifyProgramControlDependencyDomain(
            program, arch, context, geometry, backing, projection, dma,
            intrinsic, facts, error);
}

bool
buildGeometry(
    const DecodedProgram &program, ProgramSemanticContext &context,
    VerifiedProgramGeometry &geometry, MeshLoadError &error)
{
    return buildProgramSemanticContext(program, context, error) &&
        verifyProgramGeometry(program, context, geometry, error);
}

bool
buildBacking(
    const DecodedProgram &program, const RuntimeArch &arch,
    ProgramSemanticContext &context, VerifiedProgramGeometry &geometry,
    VerifiedProgramBacking &backing, MeshLoadError &error)
{
    return buildProgramSemanticContext(program, context, error) &&
        verifyProgramGeometry(program, context, geometry, error) &&
        verifyProgramBacking(program, arch, context, geometry, backing, error);
}

const GeometryAccessFact *
firstGeometryAccess(
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry)
{
    for (const auto &[operationId, operation] : context.operations()) {
        if (operation->reads.count != 0) {
            const auto *fact = geometry.access(
                operationId, GeometryAccessRole::Read, 0);
            if (fact)
                return fact;
        }
        if (operation->writes.count != 0) {
            const auto *fact = geometry.access(
                operationId, GeometryAccessRole::Write, 0);
            if (fact)
                return fact;
        }
    }
    return nullptr;
}

template <typename Record>
const Record *
probeSemanticRow(
    const mesh_abi::semantic_abi::SemanticRef &reference,
    const std::vector<Record> &rows)
{
    using Traits = mesh_abi::semantic_abi::SemanticRecordTraits<Record>;
    if (reference.section_type != Traits::section_type ||
        reference.row_id == 0 || reference.row_id > rows.size()) {
        return nullptr;
    }
    return &rows[reference.row_id - 1];
}

bool
descriptorPieceInput(
    const DecodedProgram &program, size_t endpointOffset,
    uint64_t &operationId, GeometryAccessRole &role, size_t &ordinal,
    mesh_abi::semantic_abi::SemanticRef &pieceRef,
    const mesh_abi::semantic_abi::DescriptorEndpointUse *&endpointOut)
{
    const auto &root = program.semantic_tables.program_semantics_rows.front();
    if (endpointOffset >= root.endpoint_uses.count) {
        return false;
    }
    const auto reference = program.semantic_references[size_t(
        root.endpoint_uses.begin + endpointOffset)];
    const auto *endpoint = probeSemanticRow(
        reference, program.semantic_tables.descriptor_endpoint_use_rows);
    if (!endpoint) {
        return false;
    }
    if (const auto *read = probeSemanticRow(
            endpoint->use, program.semantic_tables.read_access_use_rows)) {
        operationId = read->kernel_op_id;
        role = GeometryAccessRole::Read;
        ordinal = size_t(read->access_index);
        pieceRef = read->region;
        endpointOut = endpoint;
        return true;
    }
    if (const auto *write = probeSemanticRow(
            endpoint->use, program.semantic_tables.write_access_use_rows)) {
        operationId = write->kernel_op_id;
        role = GeometryAccessRole::Write;
        ordinal = size_t(write->access_index);
        pieceRef = write->region;
        endpointOut = endpoint;
        return true;
    }
    return false;
}

int
main(int argc, char **argv)
{
    const std::string mode = argc > 1 ? argv[1] : "single";
    const unsigned char *bytes = kGoldenMshbSingle;
    size_t size = sizeof(kGoldenMshbSingle);
    if (mode == "dual") {
        bytes = kGoldenMshbDual;
        size = sizeof(kGoldenMshbDual);
    } else if (mode == "repeat") {
        bytes = kGoldenMshbRepeat;
        size = sizeof(kGoldenMshbRepeat);
    }
    MeshBytes image(bytes, bytes + size);
    if (mode == "file" || mode.rfind("facts_", 0) == 0) {
        if (argc < 3)
            return 2;
        std::ifstream file(argv[2], std::ios::binary);
        image.assign(
            std::istreambuf_iterator<char>(file),
            std::istreambuf_iterator<char>());
    }
    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error)) {
        std::cout << "DECODE:" << error.code << ':' << error.message;
        return 0;
    }
    RuntimeArch arch = testArch();
    if (mode == "facts_backing_basic") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        if (!buildBacking(program, arch, context, geometry, backing, error) ||
            !backing.matches(program, context, arch, geometry)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        bool sawLocal = false;
        bool sawExternal = false;
        for (const auto &[objectId, object] : context.objects()) {
            const auto *local = backing.local(objectId);
            const auto *external = backing.external(objectId);
            if ((!local && !external) || (local && external) ||
                !context.owner("objects", objectId)) {
                std::cout << "BACKING:INVALID";
                return 0;
            }
            if (local) {
                sawLocal = true;
                if (local->owner_core != object->owner_core ||
                    local->size_bytes < object->footprint_bytes) {
                    std::cout << "BACKING:INVALID";
                    return 0;
                }
            }
            if (external) {
                sawExternal = true;
                const auto &binding = external->binding();
                const auto &region = external->region();
                if (binding.allocation_size_bytes < object->footprint_bytes ||
                    external->allocationAddress() < region.base) {
                    std::cout << "BACKING:INVALID";
                    return 0;
                }
            }
        }
        for (const auto &[viewId, view] : context.views()) {
            const auto *resident = backing.resident(viewId);
            if (!resident || resident->owner_core !=
                    context.objects().at(view->object_id)->owner_core) {
                std::cout << "BACKING:INVALID";
                return 0;
            }
        }
        std::cout << (sawLocal && sawExternal ? "BACKING:OK" :
            "BACKING:INVALID");
        return 0;
    }
    if (mode == "facts_backing_default_moved") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        VerifiedProgramBacking empty;
        if (!buildBacking(program, arch, context, geometry, backing, error) ||
            backing.matches(program, context, arch, geometry) == false ||
            empty.matches(program, context, arch, geometry) ||
            empty.local(1) || empty.external(1) || empty.resident(1)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        const auto &[objectId, object] = *context.objects().begin();
        const auto *local = backing.local(objectId);
        const auto *external = backing.external(objectId);
        const auto &[viewId, view] = *context.views().begin();
        const auto *resident = backing.resident(viewId);
        if ((!local && !external) || !resident) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        VerifiedProgramBacking moved(std::move(backing));
        if (backing.matches(program, context, arch, geometry) ||
            backing.local(objectId) || backing.external(objectId) ||
            backing.resident(viewId) ||
            !moved.matches(program, context, arch, geometry) ||
            moved.local(objectId) != local ||
            moved.external(objectId) != external ||
            moved.resident(viewId) != resident ||
            object->object_id != objectId || view->view_id != viewId) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        VerifiedProgramBacking assigned;
        assigned = std::move(moved);
        if (moved.matches(program, context, arch, geometry) ||
            moved.local(objectId) || moved.external(objectId) ||
            moved.resident(viewId) ||
            !assigned.matches(program, context, arch, geometry) ||
            assigned.local(objectId) != local ||
            assigned.external(objectId) != external ||
            assigned.resident(viewId) != resident) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        std::cout << "BACKING:OK";
        return 0;
    }
    if (mode == "facts_backing_generation") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        if (!buildBacking(program, arch, context, geometry, backing, error)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        VerifiedCommandProjectionFacts projection;
        VerifiedDmaDomain dma;
        if (!verifyProgramCommandProjection(
                program, arch, context, projection, error) ||
            !verifyDmaSemanticDomain(
                program, arch, context, geometry, backing, projection,
                dma, error) ||
            !dma.matches(
                program, context, arch, geometry, backing, projection)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        uint64_t externalObjectId = 0;
        const ExternalBackingAddressFact *external = nullptr;
        for (const auto &[objectId, object] : context.objects()) {
            external = backing.external(objectId);
            if (external) {
                externalObjectId = objectId;
                break;
            }
        }
        if (!external) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        const uint64_t externalAddress = external->allocationAddress();
        DecodedProgram rejected = program;
        if (rejected.transport.relocations.empty()) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        rejected.transport.relocations.front().region_id++;
        ProgramSemanticContext rejectedContext;
        VerifiedProgramGeometry rejectedGeometry;
        MeshLoadError rejectedError;
        if (!buildProgramSemanticContext(
                rejected, rejectedContext, rejectedError) ||
            !verifyProgramGeometry(
                rejected, rejectedContext, rejectedGeometry, rejectedError) ||
            verifyProgramBacking(
                rejected, arch, rejectedContext, rejectedGeometry,
                backing, rejectedError) ||
            rejectedError.code != "E_RELOCATION" ||
            !backing.matches(program, context, arch, geometry) ||
            backing.external(externalObjectId) != external ||
            backing.external(externalObjectId)->allocationAddress() !=
                externalAddress ||
            !dma.matches(
                program, context, arch, geometry, backing, projection)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        DecodedProgram other;
        MeshLoadError otherError;
        ProgramSemanticContext otherContext;
        VerifiedProgramGeometry otherGeometry;
        VerifiedProgramBacking otherBacking;
        if (!decodeMeshBinary(image, other, otherError) ||
            !buildBacking(
                other, arch, otherContext, otherGeometry, otherBacking,
                otherError) ||
            backing.matches(other, otherContext, arch, otherGeometry) ||
            otherBacking.matches(program, context, arch, geometry)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        VerifiedProgramGeometry replacementGeometry;
        MeshLoadError replacementError;
        if (!verifyProgramGeometry(
                program, context, replacementGeometry, replacementError) ||
            backing.matches(program, context, arch, replacementGeometry) ||
            dma.matches(
                program, context, arch, replacementGeometry, backing,
                projection) ||
            !verifyProgramBacking(
                program, arch, context, replacementGeometry, backing,
                replacementError) ||
            !backing.matches(
                program, context, arch, replacementGeometry) ||
            dma.matches(
                program, context, arch, replacementGeometry, backing,
                projection)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        std::cout << "BACKING:OK";
        return 0;
    }
    if (mode == "facts_backing_slot_requirement") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        if (!buildBacking(program, arch, context, geometry, backing, error)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        mesh_abi::semantic_abi::BindingSlot *slot = nullptr;
        const mesh_abi::semantic_abi::BufferObject *object = nullptr;
        for (const auto &row : program.semantic_tables.object_backing_rows) {
            const auto *external = probeSemanticRow(
                row.backing,
                program.semantic_tables.external_slot_backing_rows);
            const auto found = context.objects().find(row.object_id);
            if (!external || found == context.objects().end() ||
                found->second->footprint_bytes == 0)
                continue;
            for (auto &candidate : program.semantic_tables.binding_slot_rows)
                if (candidate.slot_id == external->slot_id) {
                    slot = &candidate;
                    object = found->second;
                    break;
                }
            if (slot)
                break;
        }
        if (!slot || !object) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        slot->required_allocation_bytes = object->footprint_bytes - 1;
        MeshLoadError rejectedError;
        if (verifyProgramBacking(
                program, arch, context, geometry, backing, rejectedError) ||
            rejectedError.code != "E_ABI_BOUNDS" ||
            !backing.matches(program, context, arch, geometry)) {
            std::cout << "BACKING:INVALID";
            return 0;
        }
        std::cout << "BACKING:OK";
        return 0;
    }
    if (mode == "facts_geometry_piece") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto &root = context.root();
        bool sawSingletonZero = false;
        bool sawNonzeroOffset = false;
        for (uint64_t offset = 0; offset < root.endpoint_uses.count; ++offset) {
            uint64_t operationId = 0;
            GeometryAccessRole role = GeometryAccessRole::Read;
            size_t ordinal = 0;
            mesh_abi::semantic_abi::SemanticRef pieceRef;
            const mesh_abi::semantic_abi::DescriptorEndpointUse *endpoint =
                nullptr;
            if (!descriptorPieceInput(
                    program, size_t(offset), operationId, role, ordinal,
                    pieceRef, endpoint)) {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            auto piece = geometry.projectAccessPiece(
                operationId, role, ordinal, pieceRef, error);
            if (!piece) {
                std::cout << "GEOMETRY:" << error.code;
                return 0;
            }
            const auto *parent = geometry.access(operationId, role, ordinal);
            const auto *descriptor = static_cast<const mesh_abi::DmaDescriptor *>(nullptr);
            for (const auto &candidate : program.transport.dma_descriptors) {
                if (candidate.descriptor_id == endpoint->descriptor_id) {
                    descriptor = &candidate;
                    break;
                }
            }
            if (!parent || &piece->access() != parent || !descriptor ||
                piece->logicalElementCount() != 0 ||
                piece->shape().size() != piece->objectByteStrides().size() ||
                piece->objectByteEnd() != piece->objectByteOffset()) {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            const uint64_t endpointOffset = endpoint->side ==
                    mesh_abi::semantic_abi::EndpointSide::SRC ?
                descriptor->src.offset_bytes : descriptor->dst.offset_bytes;
            if (piece->objectByteOffset() != endpointOffset) {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            if (piece->objectByteOffset() != 0)
                sawNonzeroOffset = true;
            for (size_t axis = 0; axis < piece->shape().size(); ++axis) {
                const uint64_t rawStride = program.semantic_u64_values[
                    parent->view().object_strides.begin + axis] *
                    piece->steps()[axis] * piece->elementWidthBytes();
                if (piece->objectByteStrides()[axis] != rawStride) {
                    std::cout << "GEOMETRY:INVALID";
                    return 0;
                }
            }
            if (piece->shape().size() == 2 && piece->shape()[0] == 1 &&
                piece->shape()[1] == 0 &&
                piece->objectByteStrides()[0] != 0 &&
                piece->objectByteStrides()[1] != 0) {
                sawSingletonZero = true;
            }
        }
        std::cout << (sawSingletonZero && sawNonzeroOffset ?
            "GEOMETRY:OK" : "GEOMETRY:INVALID");
        return 0;
    }
    if (mode == "facts_geometry_piece_invalid") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto &root = context.root();
        for (uint64_t offset = 0; offset < root.endpoint_uses.count; ++offset) {
            uint64_t operationId = 0;
            GeometryAccessRole role = GeometryAccessRole::Read;
            size_t ordinal = 0;
            mesh_abi::semantic_abi::SemanticRef pieceRef;
            const mesh_abi::semantic_abi::DescriptorEndpointUse *endpoint =
                nullptr;
            if (!descriptorPieceInput(
                    program, size_t(offset), operationId, role, ordinal,
                    pieceRef, endpoint) || !endpoint) {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            auto &piece = program.semantic_tables.element_region_rows[
                pieceRef.row_id - 1];
            if (piece.origin.count == 0 || piece.shape.count == 0 ||
                piece.steps.count == 0) {
                continue;
            }
            auto malformed = pieceRef;
            malformed.row_id = 0;
            MeshLoadError queryError;
            if (geometry.projectAccessPiece(
                    operationId, role, ordinal, malformed, queryError) ||
                queryError.code != "E_DMA_RANGE") {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            const uint32_t originalShapeCount = piece.shape.count;
            piece.shape.count = 0;
            if (geometry.projectAccessPiece(
                    operationId, role, ordinal, pieceRef, queryError) ||
                queryError.code != "E_DMA_RANGE") {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            piece.shape.count = originalShapeCount;
            const uint64_t stepIndex = piece.steps.begin;
            const uint64_t originalStep = program.semantic_u64_values[
                size_t(stepIndex)];
            program.semantic_u64_values[size_t(stepIndex)] = 0;
            if (geometry.projectAccessPiece(
                    operationId, role, ordinal, pieceRef, queryError) ||
                queryError.code != "E_DMA_RANGE") {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
            program.semantic_u64_values[size_t(stepIndex)] = originalStep;
            std::cout << "GEOMETRY:OK";
            return 0;
        }
        std::cout << "GEOMETRY:INVALID";
        return 0;
    }
    if (mode == "facts_geometry_piece_lifetime") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        uint64_t operationId = 0;
        GeometryAccessRole role = GeometryAccessRole::Read;
        size_t ordinal = 0;
        mesh_abi::semantic_abi::SemanticRef pieceRef;
        const mesh_abi::semantic_abi::DescriptorEndpointUse *endpoint =
            nullptr;
        if (!descriptorPieceInput(
                program, 0, operationId, role, ordinal, pieceRef, endpoint) ||
            !endpoint) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedProgramBacking backing;
        if (!verifyProgramBacking(program, arch, context, geometry, backing, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedCommandProjectionFacts projection;
        if (!verifyProgramCommandProjection(
                program, arch, context, projection, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedProgramGeometry defaultGeometry;
        VerifiedProgramBacking defaultBacking;
        VerifiedDmaDomain defaultDma;
        MeshLoadError queryError;
        if (defaultGeometry.projectAccessPiece(
                operationId, role, ordinal, pieceRef, queryError) ||
            queryError.code != "E_ABI_BOUNDS" ||
            verifyDmaSemanticDomain(
                program, arch, context, defaultGeometry, defaultBacking,
                projection, defaultDma, queryError) ||
            queryError.code != "E_ABI_BOUNDS") {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        auto piece = geometry.projectAccessPiece(
            operationId, role, ordinal, pieceRef, error);
        const auto *parent = geometry.access(operationId, role, ordinal);
        if (!piece || !parent || &piece->access() != parent) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const std::vector<uint64_t> retainedShape = parent->shape();
        const ExactByteRegion *retainedBytes = &parent->bytes();
        GeometryAccessPieceFact retained = std::move(*piece);
        VerifiedProgramGeometry moved(std::move(geometry));
        geometry = VerifiedProgramGeometry();
        if (!moved.matches(program, context) ||
            &retained.access() != moved.access(operationId, role, ordinal)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        auto malformed = pieceRef;
        malformed.row_id = 0;
        if (moved.projectAccessPiece(
                operationId, role, ordinal, malformed, queryError) ||
            queryError.code != "E_DMA_RANGE") {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        DecodedProgram other;
        MeshLoadError otherError;
        ProgramSemanticContext otherContext;
        VerifiedProgramGeometry otherGeometry;
        if (!decodeMeshBinary(image, other, otherError) ||
            !buildGeometry(other, otherContext, otherGeometry, otherError)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        auto foreign = otherGeometry.projectAccessPiece(
            operationId, role, ordinal, pieceRef, otherError);
        if (!foreign || &foreign->access() == &retained.access() ||
            verifyDmaSemanticDomain(
                program, arch, context, otherGeometry, backing, projection,
                defaultDma, queryError) ||
            queryError.code != "E_ABI_BOUNDS") {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedDmaDomain dma;
        if (!verifyDmaSemanticDomain(
                program, arch, context, moved, backing, projection, dma,
                queryError)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        moved = VerifiedProgramGeometry();
        if (retained.access().shape() != retainedShape ||
            &retained.access().bytes() != retainedBytes) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        std::cout << "GEOMETRY:OK";
        return 0;
    }
    if (mode == "facts_matrix_generation") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "MATRIX:INVALID";
            return 0;
        }
        VerifiedMatrixContractionFacts facts;
        if (facts.matches(program, context, geometry) ||
            facts.contraction(1) ||
            !verifyProgramComputationDomain(
                program, context, geometry, facts, error) ||
            !facts.matches(program, context, geometry)) {
            std::cout << "MATRIX:INVALID";
            return 0;
        }
        bool found = false;
        for (const auto &[operationId, operation] : context.operations()) {
            const auto *contraction = facts.contraction(operationId);
            if (!contraction)
                continue;
            const auto *bounds = contraction->bounds();
            if (!bounds || bounds->domainBegin() >= bounds->domainEnd() ||
                bounds->intervalBegin() >= bounds->intervalEnd() ||
                bounds->intervalBegin() < bounds->domainBegin() ||
                bounds->intervalEnd() > bounds->domainEnd()) {
                std::cout << "MATRIX:INVALID";
                return 0;
            }
            found = true;
        }
        VerifiedProgramGeometry defaultGeometry;
        MeshLoadError queryError;
        if (!found || facts.matches(program, context, defaultGeometry) ||
            verifyProgramPhysicalOperationDomain(
                program, context, defaultGeometry, facts, queryError) ||
            queryError.code != "E_ABI_BOUNDS" ||
            !facts.matches(program, context, geometry)) {
            std::cout << "MATRIX:INVALID";
            return 0;
        }
        DecodedProgram other;
        MeshLoadError otherError;
        ProgramSemanticContext otherContext;
        VerifiedProgramGeometry otherGeometry;
        if (!decodeMeshBinary(image, other, otherError) ||
            !buildGeometry(other, otherContext, otherGeometry, otherError) ||
            facts.matches(program, context, otherGeometry)) {
            std::cout << "MATRIX:INVALID";
            return 0;
        }
        VerifiedMatrixContractionFacts moved(std::move(facts));
        if (facts.matches(program, context, geometry) ||
            !moved.matches(program, context, geometry) ||
            !verifyProgramGeometry(program, context, geometry, error) ||
            moved.matches(program, context, geometry)) {
            std::cout << "MATRIX:INVALID";
            return 0;
        }
        std::cout << "MATRIX:OK";
        return 0;
    }
    if (mode == "facts_computation_invalid_attrs") {
        if (argc != 3)
            return 2;
        DecodedProgram rejected = program;
        auto operation = std::find_if(
            rejected.semantic_tables.kernel_op_rows.begin(),
            rejected.semantic_tables.kernel_op_rows.end(),
            [](const mesh_abi::semantic_abi::KernelOp &value) {
                return value.opcode == mesh_abi::semantic_abi::KernelOpcode::GEMM;
            });
        if (operation == rejected.semantic_tables.kernel_op_rows.end()) {
            std::cout << "COMPUTATION:INVALID";
            return 0;
        }
        const uint64_t operationId = operation->op_id;
        operation->attrs.row_id = 0;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedMatrixContractionFacts facts;
        if (!buildProgramSemanticContext(rejected, context, error) ||
            !verifyProgramGeometry(rejected, context, geometry, error) ||
            facts.matches(rejected, context, geometry) ||
            facts.contraction(operationId) ||
            verifyProgramComputationDomain(
                rejected, context, geometry, facts, error) ||
            error.code != "E_ABI_BOUNDS" ||
            facts.matches(rejected, context, geometry) ||
            facts.contraction(operationId)) {
            std::cout << "COMPUTATION:INVALID";
            return 0;
        }
        std::cout << "COMPUTATION:" << error.code;
        return 0;
    }
    if (mode == "facts_geometry_basic") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedProgramGeometry repeated;
        MeshLoadError staleError;
        staleError.code = "STALE";
        staleError.message = "stale";
        if (!verifyProgramGeometry(program, context, repeated, staleError) ||
            !staleError.code.empty() || !staleError.message.empty() ||
            !repeated.matches(program, context)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto *fact = firstGeometryAccess(context, geometry);
        if (!fact || !geometry.matches(program, context) ||
            !fact->operation().op_id ||
            (fact->role() == GeometryAccessRole::Read &&
             (!fact->read() || fact->write())) ||
            (fact->role() == GeometryAccessRole::Write &&
             (!fact->write() || fact->read())) ||
            fact->tensor().tensor_id != fact->logicalShard().tensor_id ||
            fact->object().object_id != fact->view().object_id ||
            !geometry.objectBytes(fact->object().object_id)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const ExactByteRegion &bytes = fact->bytes();
        const ExactByteRegion &whole = *geometry.objectBytes(
            fact->object().object_id);
        const ExactByteRegion &empty = geometry.emptyBytes();
        ExactByteRegion merged;
        ExactByteRegion overlap;
        ExactByteRegion difference;
        bool answer = false;
        MeshLoadError queryError;
        if (!geometry.contains(whole, bytes, answer, queryError) || !answer ||
            !geometry.disjoint(bytes, bytes, answer, queryError) || answer ||
            !geometry.intersect(bytes, bytes, overlap, queryError) ||
            !geometry.contains(overlap, bytes, answer, queryError) || !answer ||
            !geometry.subtract(bytes, bytes, difference, queryError) ||
            !geometry.disjoint(difference, bytes, answer, queryError) ||
            !answer || !geometry.unite(empty, bytes, merged, queryError) ||
            !geometry.contains(merged, bytes, answer, queryError) || !answer ||
            !geometry.contains(bytes, merged, answer, queryError) || !answer) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        std::cout << "GEOMETRY:OK";
        return 0;
    }
    if (mode == "facts_geometry_file") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:" << error.code;
            return 0;
        }
        std::cout << "GEOMETRY:ACCEPTED";
        return 0;
    }
    if (mode == "facts_geometry_kind") {
        if (argc != 4)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const std::string kind = argv[3];
        bool found = false;
        for (const auto &[operationId, operation] : context.operations()) {
            const auto check = [&](GeometryAccessRole role, uint64_t count) {
                for (uint64_t ordinal = 0; ordinal < count; ++ordinal) {
                    const auto *fact = geometry.access(
                        operationId, role, size_t(ordinal));
                    if (!fact)
                        return false;
                    if (!found &&
                        (kind == "byte_count_empty" ||
                         kind == "byte_count_scalar" ||
                         kind == "byte_count_strided" ||
                         kind == "byte_count_noninjective") &&
                        (kind != "byte_count_empty" ||
                         fact->logicalElementCount() == 0) &&
                        (kind != "byte_count_scalar" ||
                         (fact->logicalElementCount() == 1 &&
                          fact->shape().empty())) &&
                        (kind != "byte_count_strided" ||
                         (fact->shape().size() == 1 &&
                          fact->shape()[0] > 1 && fact->steps()[0] > 1 &&
                          fact->objectByteStrides().size() == 1)) &&
                        (kind != "byte_count_noninjective" ||
                         (fact->read() && fact->shape().size() == 1 &&
                          fact->shape()[0] > 1 &&
                          fact->view().object_strides.count == 1 &&
                          program.semantic_u64_values[
                              fact->view().object_strides.begin] == 0))) {
                        uint64_t width = 0;
                        uint64_t count = std::numeric_limits<uint64_t>::max();
                        MeshLoadError queryError;
                        if (!dtypeByteWidth(fact->tensor().dtype, width) ||
                            !geometry.accessByteCount(
                                operationId, role, size_t(ordinal), count,
                                queryError)) {
                            return false;
                        }
                        if (kind == "byte_count_strided" &&
                            fact->objectByteStrides()[0] <= width) {
                            return false;
                        }
                        const uint64_t expected =
                            kind == "byte_count_empty" ? 0 :
                            kind == "byte_count_noninjective" ? width :
                            fact->logicalElementCount() * width;
                        if (count != expected) {
                            return false;
                        }
                        found = true;
                    } else if (!found &&
                        (kind == "injective" ||
                         kind == "injective_empty" ||
                         kind == "noninjective") &&
                        (kind != "injective_empty" ||
                         fact->logicalElementCount() == 0) &&
                        (kind != "noninjective" ||
                         (fact->read() && fact->shape().size() == 1 &&
                          fact->shape()[0] > 1 &&
                          fact->view().object_strides.count == 1 &&
                          program.semantic_u64_values[
                              fact->view().object_strides.begin] == 0))) {
                        bool injective = true;
                        MeshLoadError queryError;
                        if (!geometry.accessMapsInjectively(
                                operationId, role, size_t(ordinal),
                                injective, queryError) ||
                            (kind == "noninjective" ? injective :
                                                     !injective)) {
                            return false;
                        }
                        found = true;
                    } else if (kind == "scalar" &&
                        fact->logicalElementCount() == 1 &&
                        fact->shape().empty() &&
                        fact->objectByteStrides().empty()) {
                        found = true;
                    } else if (kind == "empty" &&
                               fact->logicalElementCount() == 0) {
                        bool disjoint = false;
                        MeshLoadError queryError;
                        if (!geometry.disjoint(
                                fact->bytes(), fact->bytes(), disjoint,
                                queryError) || !disjoint) {
                            return false;
                        }
                        found = true;
                    } else if (kind == "strided" &&
                               fact->shape().size() == 1 &&
                               fact->shape()[0] > 1 &&
                               fact->steps()[0] > 1 &&
                               fact->objectByteStrides()[0] >
                                   fact->steps()[0]) {
                        found = true;
                    } else if (kind == "broadcast" &&
                               fact->shape().size() == 2 &&
                               fact->shape()[0] == 1 &&
                               fact->view().object_strides.count == 2 &&
                               program.semantic_u64_values[
                                   fact->view().object_strides.begin] == 0 &&
                               fact->objectByteStrides()[0] == 0) {
                        found = true;
                    } else if (kind == "broadcast_many_to_one" &&
                               fact->read() &&
                               fact->shape().size() == 1 &&
                               fact->shape()[0] > 1 &&
                               fact->view().object_strides.count == 1 &&
                               program.semantic_u64_values[
                                   fact->view().object_strides.begin] == 0 &&
                               fact->objectByteStrides()[0] == 0) {
                        const auto *write = geometry.access(
                            operationId, GeometryAccessRole::Write, 0);
                        bool contains = false;
                        bool disjoint = true;
                        MeshLoadError queryError;
                        if (!write || !geometry.contains(
                                write->bytes(), fact->bytes(), contains,
                                queryError) || !contains ||
                            !geometry.contains(
                                fact->bytes(), write->bytes(), contains,
                                queryError) || contains ||
                            !geometry.disjoint(
                                fact->bytes(), fact->bytes(), disjoint,
                                queryError) || disjoint) {
                            return false;
                        }
                        found = true;
                    } else if (kind == "singleton" &&
                               fact->shape().size() == 1 &&
                               fact->shape()[0] == 1 &&
                               fact->view().object_strides.count == 1 &&
                               program.semantic_u64_values[
                                   fact->view().object_strides.begin] ==
                                   std::numeric_limits<uint64_t>::max() &&
                               fact->objectByteStrides()[0] == 0) {
                        found = true;
                    }
                }
                return true;
            };
            if (!check(GeometryAccessRole::Read, operation->reads.count) ||
                !check(GeometryAccessRole::Write, operation->writes.count)) {
                std::cout << "GEOMETRY:INVALID";
                return 0;
            }
        }
        std::cout << (found ? "GEOMETRY:OK" : "GEOMETRY:INVALID");
        return 0;
    }
    if (mode == "facts_geometry_default_moved") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramGeometry defaultGeometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto *fact = firstGeometryAccess(context, geometry);
        if (!fact) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const ExactByteRegion &bytes = fact->bytes();
        ExactByteRegion output = bytes;
        ExactByteRegion defaultRegion;
        bool answer = true;
        MeshLoadError queryError;
        uint64_t count = std::numeric_limits<uint64_t>::max();
        if (geometry.contains(defaultRegion, bytes, answer, queryError) ||
            !answer || queryError.code != "E_ABI_BOUNDS" ||
            defaultGeometry.accessByteCount(
                fact->operation().op_id, fact->role(), fact->ordinal(),
                count, queryError) ||
            count != std::numeric_limits<uint64_t>::max() ||
            queryError.code != "E_ABI_BOUNDS" ||
            geometry.accessByteCount(
                fact->operation().op_id, fact->role(), fact->ordinal() + 1,
                count, queryError) ||
            count != std::numeric_limits<uint64_t>::max() ||
            queryError.code != "E_ABI_BOUNDS" ||
            geometry.accessMapsInjectively(
                fact->operation().op_id, fact->role(), fact->ordinal() + 1,
                answer, queryError) ||
            !answer || queryError.code != "E_ABI_BOUNDS" ||
            geometry.unite(bytes, defaultRegion, output, queryError) ||
            queryError.code != "E_ABI_BOUNDS" ||
            !geometry.contains(output, bytes, answer, queryError) || !answer) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        VerifiedProgramGeometry moved(std::move(geometry));
        if (geometry.matches(program, context) ||
            !moved.matches(program, context) ||
            geometry.disjoint(bytes, bytes, answer, queryError) || !answer ||
            queryError.code != "E_ABI_BOUNDS" ||
            !moved.contains(bytes, bytes, answer, queryError) || !answer) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        std::cout << "GEOMETRY:OK";
        return 0;
    }
    if (mode == "facts_geometry_foreign_retained") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto *fact = firstGeometryAccess(context, geometry);
        DecodedProgram other = program;
        ProgramSemanticContext otherContext;
        VerifiedProgramGeometry otherGeometry;
        MeshLoadError otherError;
        if (!fact || !buildGeometry(
                other, otherContext, otherGeometry, otherError)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto *foreign = firstGeometryAccess(otherContext, otherGeometry);
        if (!foreign) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        ExactByteRegion output = fact->bytes();
        bool answer = true;
        MeshLoadError queryError;
        if (geometry.unite(fact->bytes(), foreign->bytes(), output, queryError) ||
            queryError.code != "E_ABI_BOUNDS" ||
            !geometry.contains(output, fact->bytes(), answer, queryError) ||
            !answer) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        ExactByteRegion retained = fact->bytes();
        geometry = VerifiedProgramGeometry();
        if (geometry.matches(program, context) ||
            otherGeometry.contains(
                retained, otherGeometry.emptyBytes(), answer, queryError) ||
            !answer || queryError.code != "E_ABI_BOUNDS") {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        std::cout << "GEOMETRY:OK";
        return 0;
    }
    if (mode == "facts_geometry_atomic") {
        if (argc != 3)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        if (!buildGeometry(program, context, geometry, error)) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const auto *fact = firstGeometryAccess(context, geometry);
        if (!fact || program.semantic_tables.buffer_view_rows.empty()) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        const uint64_t operationId = fact->operation().op_id;
        const GeometryAccessRole role = fact->role();
        const size_t ordinal = fact->ordinal();
        DecodedProgram rejected = program;
        rejected.semantic_tables.buffer_view_rows.front().padded_shape.count++;
        ProgramSemanticContext rejectedContext;
        MeshLoadError rejectedError;
        if (!buildProgramSemanticContext(
                rejected, rejectedContext, rejectedError) ||
            verifyProgramGeometry(
                rejected, rejectedContext, geometry, rejectedError) ||
            rejectedError.code != "E_ABI_BOUNDS" ||
            !geometry.matches(program, context) || geometry.matches(
                rejected, rejectedContext) ||
            geometry.access(operationId, role, ordinal) != fact) {
            std::cout << "GEOMETRY:INVALID";
            return 0;
        }
        std::cout << "GEOMETRY:OK";
        return 0;
    }
    if (mode == "facts_event_fanin") {
        if (argc != 11)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        VerifiedCommandProjectionFacts projection;
        VerifiedDmaDomain dma;
        VerifiedIntrinsicDependencyFacts intrinsic;
        VerifiedControlDependencyFacts facts;
        if (!buildControlFacts(
                program, arch, context, geometry, backing, projection, dma, intrinsic,
                facts, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto operationId = std::stoull(argv[3]);
        const auto tokenId = std::stoull(argv[4]);
        const auto waiterOperationId = std::stoull(argv[5]);
        const auto firstSignalCommand =
            static_cast<uint32_t>(std::stoul(argv[6]));
        const auto secondSignalCommand =
            static_cast<uint32_t>(std::stoul(argv[7]));
        const auto waiterCommand = static_cast<uint32_t>(std::stoul(argv[8]));
        const auto eventId = static_cast<uint32_t>(std::stoul(argv[9]));
        const auto dependencyId = std::stoull(argv[10]);
        const auto *operation = intrinsic.operation(operationId);
        const auto *token = intrinsic.token(tokenId);
        const auto *waiter = intrinsic.operation(waiterOperationId);
        const auto *begin = intrinsic.invocationBegin(1);
        const auto *first = facts.scheduled(firstSignalCommand);
        const auto *second = facts.scheduled(secondSignalCommand);
        const auto *scheduledWaiter = facts.scheduled(waiterCommand);
        const auto *event = facts.event(eventId);
        const auto *dependency = facts.dependency(dependencyId);
        if (!intrinsic.matches(program, context) ||
            !facts.matches(
                program, context, arch, geometry, backing, projection, dma,
                intrinsic) ||
            !operation || !token || !waiter || !begin || !first || !second ||
            !scheduledWaiter || !event || !dependency || token->initial() ||
            !token->producerOperation() ||
            *token->producerOperation() != operationId ||
            intrinsic.invocationBegin(999) != nullptr) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        bool answer = false;
        MeshLoadError queryError;
        if (!intrinsic.intrinsicHappensBefore(
                *begin, operation->start(), answer, queryError) || !answer ||
            !intrinsic.intrinsicHappensBefore(
                operation->start(), token->completion(), answer, queryError) ||
            !answer ||
            !intrinsic.intrinsicHappensBefore(
                token->completion(), waiter->start(), answer, queryError) ||
            !answer ||
            !facts.declaredHappensBefore(
                dependency->source(), dependency->target(), answer, queryError) ||
            !answer ||
            !facts.completionHappensBefore(
                first->completion(), scheduledWaiter->start(), answer,
                queryError) || !answer ||
            !facts.completionHappensBefore(
                second->completion(), scheduledWaiter->start(), answer,
                queryError) || answer) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        std::cout << "FACTS:OK";
        return 0;
    }
    if (mode == "facts_default_moved") {
        if (argc != 11)
            return 2;
        ProgramSemanticContext context;
        if (context.matches(program) ||
            !buildProgramSemanticContext(program, context, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        ProgramSemanticContext movedContext(std::move(context));
        if (context.matches(program) || !movedContext.matches(program)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedCommandProjectionFacts projection;
        if (projection.matches(program, movedContext, arch) ||
            !verifyProgramCommandProjection(
                program, arch, movedContext, projection, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedCommandProjectionFacts movedProjection(std::move(projection));
        if (projection.matches(program, movedContext, arch) ||
            !movedProjection.matches(program, movedContext, arch)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedProgramGeometry geometry;
        VerifiedMatrixContractionFacts matrix;
        if (!verifyProgramGeometry(program, movedContext, geometry, error) ||
            !verifyProgramComputationDomain(
                program, movedContext, geometry, matrix, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedProgramBacking backing;
        if (!verifyProgramBacking(
                program, arch, movedContext, geometry, backing, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedDmaDomain dma;
        if (dma.matches(
                program, movedContext, arch, geometry, backing,
                movedProjection) ||
            !verifyDmaSemanticDomain(
                program, arch, movedContext, geometry, backing, movedProjection,
                dma, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedDmaDomain movedDma(std::move(dma));
        if (dma.matches(
                program, movedContext, arch, geometry, backing,
                movedProjection) ||
            !movedDma.matches(
                program, movedContext, arch, geometry, backing,
                movedProjection)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedIntrinsicDependencyFacts intrinsic;
        if (!verifyProgramIntrinsicDependencyFacts(
                program, movedContext, intrinsic, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto operationId = std::stoull(argv[3]);
        const auto *operation = intrinsic.operation(operationId);
        if (!operation) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedIntrinsicDependencyFacts defaultIntrinsic;
        VerifiedIntrinsicDependencyFacts movedIntrinsic(std::move(intrinsic));
        bool answer = true;
        MeshLoadError queryError;
        if (!movedIntrinsic.operation(operationId) ||
            intrinsic.matches(program, movedContext) ||
            intrinsic.intrinsicHappensBefore(
                operation->start(), operation->completion(), answer,
                queryError) || !answer || queryError.code != "E_ABI_BOUNDS" ||
            defaultIntrinsic.matches(program, movedContext) ||
            defaultIntrinsic.intrinsicHappensBefore(
                operation->start(), operation->completion(), answer,
                queryError) || !answer || queryError.code != "E_ABI_BOUNDS" ||
            !movedIntrinsic.matches(program, movedContext) ||
            !movedIntrinsic.intrinsicHappensBefore(
                operation->start(), operation->completion(), answer,
                queryError) || !answer) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedControlDependencyFacts facts;
        if (!verifyProgramControlDependencyDomain(
                program, arch, movedContext, geometry, backing, movedProjection,
                movedDma, movedIntrinsic, facts, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto commandId = static_cast<uint32_t>(std::stoul(argv[6]));
        const auto *scheduled = facts.scheduled(commandId);
        if (!scheduled) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedControlDependencyFacts defaultFacts;
        VerifiedControlDependencyFacts movedFacts(std::move(facts));
        if (!movedFacts.scheduled(commandId) || facts.scheduled(commandId) ||
            defaultFacts.scheduled(commandId) || facts.matches(
                program, movedContext, arch, geometry, backing, movedProjection,
                movedDma, movedIntrinsic) ||
            defaultFacts.matches(
                program, movedContext, arch, geometry, backing,
                movedProjection, movedDma, movedIntrinsic) ||
            !movedFacts.matches(
                program, movedContext, arch, geometry, backing,
                movedProjection, movedDma, movedIntrinsic)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        std::cout << "FACTS:OK";
        return 0;
    }
    if (mode == "facts_cross_invocation") {
        if (argc != 11)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        VerifiedCommandProjectionFacts projection;
        VerifiedDmaDomain dma;
        VerifiedIntrinsicDependencyFacts intrinsic;
        VerifiedControlDependencyFacts facts;
        if (!buildControlFacts(
                program, arch, context, geometry, backing, projection, dma, intrinsic,
                facts, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        DecodedProgram other;
        MeshLoadError otherError;
        if (!decodeMeshBinary(image, other, otherError)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        ProgramSemanticContext otherContext;
        VerifiedProgramGeometry otherGeometry;
        VerifiedProgramBacking otherBacking;
        VerifiedCommandProjectionFacts otherProjection;
        VerifiedDmaDomain otherDma;
        VerifiedIntrinsicDependencyFacts otherIntrinsic;
        VerifiedControlDependencyFacts otherFacts;
        if (!buildControlFacts(
                other, arch, otherContext, otherGeometry, otherBacking,
                otherProjection, otherDma, otherIntrinsic, otherFacts,
                otherError)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto operationId = std::stoull(argv[3]);
        const auto *operation = intrinsic.operation(operationId);
        const auto *foreign = otherIntrinsic.operation(operationId);
        bool answer = true;
        MeshLoadError queryError;
        if (!operation || !foreign ||
            intrinsic.intrinsicHappensBefore(
                operation->start(), foreign->start(), answer, queryError) ||
            !answer || queryError.code != "E_ABI_BOUNDS") {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        std::cout << "FACTS:OK";
        return 0;
    }
    if (mode == "facts_atomic") {
        if (argc != 4)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        VerifiedCommandProjectionFacts projection;
        VerifiedDmaDomain dma;
        VerifiedIntrinsicDependencyFacts intrinsic;
        VerifiedControlDependencyFacts facts;
        if (!buildControlFacts(
                program, arch, context, geometry, backing, projection, dma, intrinsic,
                facts, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        VerifiedIntrinsicDependencyFacts publishedIntrinsic(std::move(intrinsic));
        if (!verifyProgramIntrinsicDependencyFacts(
                program, context, intrinsic, error) ||
            facts.matches(
                program, context, arch, geometry, backing, projection, dma,
                intrinsic) ||
            !facts.matches(
                program, context, arch, geometry, backing, projection, dma,
                publishedIntrinsic)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        std::ifstream rejectedFile(argv[3], std::ios::binary);
        MeshBytes rejectedImage{
            std::istreambuf_iterator<char>(rejectedFile),
            std::istreambuf_iterator<char>()};
        DecodedProgram rejected;
        MeshLoadError rejectedError;
        if (!decodeMeshBinary(rejectedImage, rejected, rejectedError)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        ProgramSemanticContext rejectedContext;
        VerifiedCommandProjectionFacts rejectedProjection;
        VerifiedDmaDomain rejectedDma;
        VerifiedProgramGeometry rejectedGeometry;
        VerifiedProgramBacking rejectedBacking;
        VerifiedMatrixContractionFacts rejectedMatrix;
        VerifiedIntrinsicDependencyFacts rejectedIntrinsic;
        if (!buildProgramSemanticContext(
                rejected, rejectedContext, rejectedError) ||
            !verifyProgramGeometry(
                rejected, rejectedContext, rejectedGeometry, rejectedError) ||
            !verifyProgramBacking(
                rejected, arch, rejectedContext, rejectedGeometry,
                rejectedBacking, rejectedError) ||
            !verifyProgramCommandProjection(
                rejected, arch, rejectedContext, rejectedProjection,
                rejectedError) ||
            !verifyProgramComputationDomain(
                rejected, rejectedContext, rejectedGeometry,
                rejectedMatrix, rejectedError) ||
            !verifyDmaSemanticDomain(
                rejected, arch, rejectedContext, rejectedGeometry,
                rejectedBacking, rejectedProjection, rejectedDma,
                rejectedError) ||
            !verifyProgramIntrinsicDependencyFacts(
                rejected, rejectedContext, rejectedIntrinsic, rejectedError)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto *operation = publishedIntrinsic.operation(1);
        bool answer = false;
        MeshLoadError mismatchError;
        if (!operation || verifyProgramControlDependencyDomain(
                rejected, arch, rejectedContext, rejectedGeometry,
                rejectedBacking, rejectedProjection, rejectedDma, intrinsic,
                facts, mismatchError) ||
            mismatchError.code != "E_ABI_BOUNDS" ||
            !facts.matches(
                program, context, arch, geometry, backing, projection, dma,
                publishedIntrinsic) ||
            verifyProgramControlDependencyDomain(
                rejected, arch, rejectedContext, rejectedGeometry,
                rejectedBacking, rejectedProjection, rejectedDma,
                rejectedIntrinsic, facts, rejectedError) ||
            rejectedError.code != "E_ABI_BOUNDS" ||
            !facts.matches(
                program, context, arch, geometry, backing, projection, dma,
                publishedIntrinsic) ||
            !publishedIntrinsic.intrinsicHappensBefore(
                operation->start(), operation->completion(), answer,
                mismatchError) || !answer) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        std::cout << "FACTS:OK";
        return 0;
    }
    if (mode == "facts_repeat_execution_count") {
        if (argc != 5)
            return 2;
        ProgramSemanticContext context;
        VerifiedProgramGeometry geometry;
        VerifiedProgramBacking backing;
        VerifiedCommandProjectionFacts projection;
        VerifiedDmaDomain dma;
        VerifiedIntrinsicDependencyFacts intrinsic;
        VerifiedControlDependencyFacts facts;
        if (!buildControlFacts(
                program, arch, context, geometry, backing, projection, dma, intrinsic,
                facts, error)) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto commandId = static_cast<uint32_t>(std::stoul(argv[3]));
        const auto expectedCount = std::stoull(argv[4]);
        const auto *scheduled = facts.scheduled(commandId);
        const auto *repeat = projection.repeat(commandId);
        const auto *repeatStream = context.commandStream(commandId);
        if (!scheduled || !repeat || !repeatStream ||
            scheduled->executionCount() != 1 ||
            repeat->repeatCount() != expectedCount ||
            repeat->beginOrdinal() > repeatStream->ordinal ||
            repeat->commandCount() >
                repeatStream->ordinal - repeat->beginOrdinal() ||
            repeat->beginOrdinal() + repeat->commandCount() !=
                repeatStream->ordinal) {
            std::cout << "FACTS:INVALID";
            return 0;
        }
        const auto &root = context.root();
        for (uint64_t ordinal = 0; ordinal < repeatStream->stream->command_count;
             ++ordinal) {
            const uint64_t command = program.semantic_u64_values[size_t(
                root.stream_command_ids.begin +
                repeatStream->stream->command_begin + ordinal)];
            const auto *commandFact = facts.scheduled(uint32_t(command));
            const bool inBody = ordinal >= repeat->beginOrdinal() &&
                ordinal - repeat->beginOrdinal() < repeat->commandCount();
            if (!commandFact || commandFact->executionCount() !=
                    (inBody ? expectedCount : 1)) {
                std::cout << "FACTS:INVALID";
                return 0;
            }
        }
        for (const auto &entry : context.commands()) {
            const auto *commandStream = context.commandStream(entry.first);
            const auto *commandFact = facts.scheduled(entry.first);
            const bool inBody = commandStream &&
                commandStream->stream == repeatStream->stream &&
                commandStream->ordinal >= repeat->beginOrdinal() &&
                commandStream->ordinal - repeat->beginOrdinal() <
                    repeat->commandCount();
            if (!commandFact || commandFact->executionCount() !=
                    (inBody ? expectedCount : 1)) {
                std::cout << "FACTS:INVALID";
                return 0;
            }
        }
        std::cout << "FACTS:OK";
        return 0;
    }
    if (mode == "arch")
        arch.arch_digest_hex = "00";
    else if (mode == "major")
        program.header.abi_major = 0;
    else if (mode == "minor")
        program.header.abi_minor = 0;
    else if (mode == "feature")
        program.header.required_features = 0;
    else if (mode == "minimum")
        program.metadata.min_reader_minor = 0;
    else if (mode == "checksum")
        program.metadata.semantic_sha256.front() ^= 1;
    else if (mode == "group_command") {
        program.semantic_tables.descriptor_group_rows.front().command_id++;
        refreshSemanticChecksum(program);
    } else if (mode == "group_completion") {
        program.semantic_tables.descriptor_group_rows.front().completion_event_id++;
        refreshSemanticChecksum(program);
    } else if (mode == "group_duplicate") {
        auto &groups = program.semantic_tables.descriptor_group_rows;
        program.semantic_u64_values[groups[1].descriptor_ids.begin] =
            program.semantic_u64_values[groups[0].descriptor_ids.begin];
        refreshSemanticChecksum(program);
    } else if (mode == "descriptor_completion") {
        program.transport.dma_descriptors.front().completion_event++;
        refreshSemanticChecksum(program);
    } else if (mode == "duplicate_command") {
        program.transport.commands[1].command_id =
            program.transport.commands[0].command_id;
        refreshSemanticChecksum(program);
    } else if (mode == "duplicate_command_after_order") {
        program.transport.commands[0].command_id = 2;
        program.transport.commands[1].command_id = 2;
        refreshSemanticChecksum(program);
    } else if (mode == "reordered_command") {
        std::swap(program.transport.commands[0].command_id,
                  program.transport.commands[1].command_id);
        refreshSemanticChecksum(program);
    } else if (mode == "descriptor_missing") {
        program.transport.dma_descriptors.erase(
            program.transport.dma_descriptors.begin());
        refreshSemanticChecksum(program);
    } else if (mode == "descriptor_append") {
        program.transport.dma_descriptors.push_back(
            program.transport.dma_descriptors.front());
        refreshSemanticChecksum(program);
    } else if (mode == "allocation_zero") {
        program.transport.allocations.front().allocation_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "allocation_duplicate") {
        program.transport.allocations.back().allocation_id =
            program.transport.allocations.front().allocation_id;
        refreshSemanticChecksum(program);
    } else if (mode == "shard_zero") {
        program.transport.shards.front().shard_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "shard_duplicate") {
        program.transport.shards.back().shard_id =
            program.transport.shards.front().shard_id;
        refreshSemanticChecksum(program);
    } else if (mode == "event_zero") {
        program.transport.events.front().event_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "event_duplicate") {
        program.transport.events.back().event_id =
            program.transport.events.front().event_id;
        refreshSemanticChecksum(program);
    } else if (mode == "resident_runtime_shard_zero") {
        program.semantic_tables.resident_view_rows.front().runtime_shard_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "resident_runtime_shard_duplicate") {
        program.semantic_tables.resident_view_rows.back().runtime_shard_id =
            program.semantic_tables.resident_view_rows.front().runtime_shard_id;
        refreshSemanticChecksum(program);
    } else if (mode == "variant_entrypoint_zero") {
        program.semantic_tables.program_variant_rows.front().entrypoint_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "variant_profile_zero") {
        program.semantic_tables.program_variant_rows.front().profile_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "variant_lifecycle_zero") {
        program.semantic_tables.program_variant_rows.front().lifecycle_stream_id = 0;
        refreshSemanticChecksum(program);
    } else if (mode == "authored_lineage_empty") {
        const auto &lineage =
            program.semantic_tables.authored_variant_lineage_rows.front();
        program.semantic_strings[lineage.authoring_variant_id.string_id - 1]
            .clear();
        ProgramSemanticContext context;
        if (buildProgramSemanticContext(program, context, error)) {
            std::cout << "CONTEXT:ACCEPTED";
            return 0;
        }
        std::cout << "CONTEXT:" << error.code << ':' << error.message;
        return 0;
    } else if (mode == "object_backing_duplicate") {
        program.semantic_tables.object_backing_rows.back().object_id =
            program.semantic_tables.object_backing_rows.front().object_id;
        refreshSemanticChecksum(program);
    } else if (mode == "resident_view_duplicate") {
        program.semantic_tables.resident_view_rows.back().view_id =
            program.semantic_tables.resident_view_rows.front().view_id;
        refreshSemanticChecksum(program);
    } else if (mode == "binding_slot_duplicate") {
        program.semantic_tables.binding_slot_rows.back().slot_id =
            program.semantic_tables.binding_slot_rows.front().slot_id;
        refreshSemanticChecksum(program);
    } else if (mode == "context_atomic") {
        ProgramSemanticContext context;
        if (!buildProgramSemanticContext(program, context, error)) {
            std::cout << "CONTEXT:" << error.code << ':' << error.message;
            return 0;
        }
        const auto *root = &context.root();
        const size_t commandCount = context.commands().size();
        const size_t allocationCount = context.allocations().size();
        const size_t operationCount = context.operations().size();
        const size_t descriptorCount = context.descriptors().size();
        VerifiedCommandProjectionFacts projection;
        if (!verifyProgramCommandProjection(
                program, arch, context, projection, error)) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        VerifiedProgramGeometry geometry;
        if (!verifyProgramGeometry(program, context, geometry, error)) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        VerifiedProgramBacking backing;
        if (!verifyProgramBacking(program, arch, context, geometry, backing, error)) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        VerifiedDmaDomain domain;
        MeshLoadError acceptedDmaError;
        if (!verifyDmaSemanticDomain(
                program, arch, context, geometry, backing, projection, domain,
                acceptedDmaError)) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        const uint32_t descriptorId =
            program.transport.dma_descriptors.front().descriptor_id;
        const uint64_t sourceAddress = domain.sourceAddress(descriptorId);
        DecodedProgram rejected = program;
        rejected.transport.allocations.front().allocation_id = 0;
        refreshSemanticChecksum(rejected);
        MeshLoadError rejectedError;
        rejectedError.code = "E_SEEDED";
        rejectedError.message = "seeded error";
        if (buildProgramSemanticContext(rejected, context, rejectedError) ||
            rejectedError.code != "E_ABI_ORDER" ||
            rejectedError.message !=
                "allocation identities are not densely ordered" ||
            !context.matches(program) || context.matches(rejected) ||
            &context.root() != root ||
            context.commands().size() != commandCount ||
            context.allocations().size() != allocationCount ||
            context.operations().size() != operationCount ||
            context.descriptors().size() != descriptorCount) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        MeshLoadError computeError;
        if (verifyProgramComputeDomain(
                rejected, arch, context, geometry, computeError) ||
            computeError.code != "E_ABI_BOUNDS") {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        MeshLoadError dmaError;
        if (verifyDmaSemanticDomain(
                rejected, arch, context, geometry, backing, projection, domain,
                dmaError) ||
            dmaError.code != "E_ABI_BOUNDS" ||
            domain.sourceAddress(descriptorId) != sourceAddress) {
            std::cout << "CONTEXT:INVALID";
            return 0;
        }
        std::cout << "CONTEXT:" << rejectedError.code << ':'
                  << rejectedError.message;
        return 0;
    }
    if (mode == "facts_metadata_seeded_layout") {
        ProgramSemanticContext context;
        if (!buildProgramSemanticContext(program, context, error)) {
            std::cout << "LOGICAL:INVALID";
            return 0;
        }
        error.code = "E_SEEDED";
        error.message = "seeded error";
        if (!verifyProgramLogicalComputationDomain(program, context, error)) {
            std::cout << "LOGICAL:" << error.code << ':' << error.message;
            return 0;
        }
        std::cout << "ACCEPTED";
        return 0;
    }
    MeshProgramAdmission admission;
    if (!admitProgram(std::make_shared<DecodedProgram>(program), arch,
                      admission, error)) {
        std::cout << "VERIFY:" << error.code << ':' << error.message;
        return 0;
    }
    std::cout << "ACCEPTED";
}
'''.lstrip(),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic-errors",
            "-I",
            str(ROOT / "src"),
            str(source),
            *(str(ROOT / "src/dev/ai_mesh" / name) for name in (
                "mesh_binary.cc",
                "mesh_binary_envelope.cc",
                "mesh_binary_storage.cc",
                "mesh_binary_validation.cc",
                "mesh_binary_canonical.cc",
                "mesh_canonical.cc",
                "mesh_hash.cc",
                "mesh_ir_computation_verifier.cc",
                "mesh_ir_logical_computation_verifier.cc",
                "mesh_ir_physical_operation_verifier.cc",
                "mesh_ir_compute_verifier.cc",
                "mesh_ir_control_dependency_verifier.cc",
                "mesh_ir_control_projection.cc",
                "mesh_ir_dependency_graph.cc",
                "mesh_ir_backing.cc",
                "mesh_ir_lifetime_verifier.cc",
                "mesh_ir_dma_verifier.cc",
                "mesh_ir_intrinsic_dependency_facts.cc",
                "mesh_ir_intrinsic_memory_verifier.cc",
                "mesh_ir_region.cc",
                "mesh_ir_semantic_context.cc",
                "mesh_ir_verifier.cc",
                "mesh_splitter.cc",
            )),
            "-lisl",
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable

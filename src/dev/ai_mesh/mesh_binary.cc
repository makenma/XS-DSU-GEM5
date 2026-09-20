#include "dev/ai_mesh/mesh_binary.hh"

#include <new>
#include <stdexcept>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_binary_envelope.hh"
#include "dev/ai_mesh/mesh_binary_storage.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
resourceFailure(MeshLoadError &error)
{
    error = {
        mesh_diagnostics::E_ABI_OVERFLOW,
        "binary representation exceeds available storage"};
    return false;
}

}

bool
decodeMeshBinary(
    const MeshBytes &image, DecodedProgram &out, MeshLoadError &error)
{
    try {
        mesh_binary_detail::DecodedEnvelope envelope;
        if (!mesh_binary_detail::decodeMeshEnvelope(image, envelope, error))
            return false;
        DecodedProgram candidate;
        if (!mesh_binary_detail::decodeProgramStorage(
                image, envelope, candidate, error) ||
            !mesh_binary_detail::validateProgramSemanticChecksum(
                candidate, error))
            return false;
        out = std::move(candidate);
        return true;
    } catch (const std::bad_alloc &) {
        return resourceFailure(error);
    } catch (const std::length_error &) {
        return resourceFailure(error);
    }
}

bool
encodeMeshBinary(
    const DecodedProgram &program, MeshBytes &out, MeshLoadError &error)
{
    try {
        if (!mesh_binary_detail::validateProgramSemanticChecksum(
                program, error))
            return false;
        std::vector<mesh_binary_detail::MeshSection> sections;
        if (!mesh_binary_detail::encodeProgramStorage(
                program, sections, error))
            return false;
        MeshBytes candidate;
        if (!mesh_binary_detail::encodeMeshEnvelope(
                program.header, sections, candidate, error))
            return false;
        out = std::move(candidate);
        return true;
    } catch (const std::bad_alloc &) {
        return resourceFailure(error);
    } catch (const std::length_error &) {
        return resourceFailure(error);
    }
}

}
}

#include "dev/ai_mesh/mesh_ir_computation_verifier.hh"

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"
#include "dev/ai_mesh/mesh_ir_logical_computation_verifier.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_diagnostics;

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

}

bool
verifyProgramComputationDomain(
    const DecodedProgram &program, const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    VerifiedMatrixContractionFacts &out, MeshLoadError &error)
{
    if (!context.matches(program) || !geometry.matches(program, context)) {
        return fail(E_ABI_BOUNDS,
                    "computation facts belong to another verification invocation",
                    error);
    }
    VerifiedMatrixContractionFacts candidate;
    if (!verifyProgramLogicalComputationDomain(program, context, error) ||
        !verifyProgramPhysicalOperationDomain(
            program, context, geometry, candidate, error))
        return false;
    out = std::move(candidate);
    error = {};
    return true;
}

}
}

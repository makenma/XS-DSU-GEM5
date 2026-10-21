#ifndef DEV_AI_MESH_MESH_IR_GATHER_HH
#define DEV_AI_MESH_MESH_IR_GATHER_HH

#include <cstdint>

#include "dev/ai_mesh/mesh_ir_region.hh"

namespace gem5::ai_mesh
{

struct GatherGeometry
{
    uint64_t rows = 0;
    uint64_t rowBytes = 0;
    uint64_t tableRows = 0;
    uint64_t sourceStrideBytes = 0;
    uint64_t destinationStrideBytes = 0;
    uint64_t indexOffsetBytes = 0;
    uint64_t indexStrideBytes = 0;
    uint64_t usefulBytes = 0;
    uint64_t physicalStorageBytes = 0;

    static bool derive(
        const DecodedProgram &, const VerifiedProgramGeometry &,
        const mesh_abi::semantic_abi::KernelOp &, GatherGeometry &,
        MeshLoadError &);
};

bool verifyStagedGatherResult(
    const DecodedProgram &, const ProgramSemanticContext &,
    const VerifiedProgramGeometry &,
    const mesh_abi::semantic_abi::KernelOp &, MeshLoadError &);

}

#endif

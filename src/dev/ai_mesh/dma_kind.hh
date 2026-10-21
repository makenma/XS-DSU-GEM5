#ifndef DEV_AI_MESH_DMA_KIND_HH
#define DEV_AI_MESH_DMA_KIND_HH

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5::ai_mesh
{

inline bool dmaKindIsRead(uint16_t kind)
{
    return kind == mesh_abi::kDmaKindLOAD || kind == mesh_abi::kDmaKindPREFETCH ||
        kind == mesh_abi::kDmaKindGATHER_LOAD;
}

inline bool dmaOpcodeIsDma(uint16_t opcode)
{
    const auto engine = mesh_abi::opcodeEngine(opcode);
    return engine == mesh_abi::kEngineDMA_READ || engine == mesh_abi::kEngineDMA_WRITE;
}

}

#endif

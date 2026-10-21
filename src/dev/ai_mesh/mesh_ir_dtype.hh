#ifndef GEM5_DEV_AI_MESH_MESH_IR_DTYPE_HH
#define GEM5_DEV_AI_MESH_MESH_IR_DTYPE_HH

#include <cstdint>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_abi.hh"

namespace gem5
{
namespace ai_mesh
{

inline mesh_abi::semantic_abi::DType
computationAccumulationDtype(mesh_abi::semantic_abi::DType dtype)
{
    if (dtype == mesh_abi::semantic_abi::DType::FP16 ||
        dtype == mesh_abi::semantic_abi::DType::BF16)
        return mesh_abi::semantic_abi::DType::FP32;
    if (dtype == mesh_abi::semantic_abi::DType::INT8)
        return mesh_abi::semantic_abi::DType::INT32;
    return dtype;
}

inline bool
dtypeByteWidth(mesh_abi::semantic_abi::DType dtype, uint64_t &out)
{
    using mesh_abi::semantic_abi::DType;
    switch (dtype) {
      case DType::FP32:
      case DType::INT32:
        out = 4;
        return true;
      case DType::FP16:
      case DType::BF16:
        out = 2;
        return true;
      case DType::INT8:
        out = 1;
        return true;
      default:
        return false;
    }
}

}
}

#endif

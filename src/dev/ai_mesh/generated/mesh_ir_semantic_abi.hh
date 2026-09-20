#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_ABI_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_SEMANTIC_ABI_HH

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string_view>
#include <vector>
#include "dev/ai_mesh/generated/mesh_diagnostics.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_abi
{
namespace semantic_abi
{

static_assert(sizeof(double) == sizeof(uint64_t));
static_assert(std::numeric_limits<double>::is_iec559);

constexpr char kSemanticSchemaSha256[] = "87abcecd302eeea330666c2bf11c4e50e1bf52c201a979a78cc87f486f5d544f";
constexpr uint32_t kSemanticRefBytes = 8;
constexpr uint32_t kSemanticRefSectionTypeOffset = 0;
constexpr uint32_t kSemanticRefReservedOffset = 2;
constexpr uint32_t kSemanticRefRowIdOffset = 4;
constexpr uint32_t kListSpanBytes = 8;
constexpr uint32_t kListSpanBeginOffset = 0;
constexpr uint32_t kListSpanCountOffset = 4;
constexpr uint32_t kStringRefBytes = 8;
constexpr uint32_t kStringRefStringIdOffset = 0;
constexpr uint32_t kStringRefReservedOffset = 4;
constexpr uint32_t kEnumValueBytes = 8;
constexpr uint32_t kEnumValueValueOffset = 0;
constexpr uint32_t kEnumValueReservedOffset = 4;
constexpr uint32_t kScalarValueBytes = 16;
constexpr uint32_t kScalarValueKindOffset = 0;
constexpr uint32_t kScalarValueReservedOffset = 2;
constexpr uint32_t kScalarValuePayloadOffset = 8;
constexpr uint32_t kProgramMetadataBytes = 48;
constexpr uint32_t kProgramMetadataMinReaderMinorOffset = 0;
constexpr uint32_t kProgramMetadataFlagsOffset = 2;
constexpr uint32_t kProgramMetadataReservedOffset = 4;
constexpr uint32_t kProgramMetadataSemanticsOffset = 8;
constexpr uint32_t kProgramMetadataSemanticSha256Offset = 16;
constexpr uint32_t kSemanticIntegerValueBytes = 16;
constexpr uint32_t kSemanticIntegerValueKindOffset = 0;
constexpr uint32_t kSemanticIntegerValueReservedOffset = 2;
constexpr uint32_t kSemanticIntegerValuePayloadOffset = 8;
constexpr uint16_t kScalarKindBool = 1;
constexpr uint16_t kScalarKindI64 = 2;
constexpr uint16_t kScalarKindU64 = 3;
constexpr uint16_t kScalarKindF64 = 4;

struct SemanticRef { uint16_t section_type = 0; uint32_t row_id = 0; };
struct ListSpan { uint32_t begin = 0; uint32_t count = 0; };
struct StringRef { uint32_t string_id = 0; };
struct ScalarValue { uint16_t kind = 0; uint64_t payload = 0; };
struct ProgramMetadata { uint16_t min_reader_minor = 0; SemanticRef semantics{}; std::array<uint8_t, kProgramMetadataBytes - kProgramMetadataSemanticSha256Offset> semantic_sha256{}; };
struct SemanticIntegerValue { uint16_t kind = 0; uint64_t payload = 0; };
enum class SemanticEnumPythonKind : uint8_t { Invalid, Integer, String };
struct SemanticEnumPythonValue { SemanticEnumPythonKind kind = SemanticEnumPythonKind::Invalid; int64_t integer_value = 0; std::string_view string_value{}; };
template <typename Enum> struct SemanticEnumTraits;
enum class SemanticFieldKind : uint8_t { U64, I64, Bool, F64, String, Bytes, Enum, Ref, U64List, IntegerList, RefList, Scalar };
struct SemanticFieldDescriptor { std::string_view name; SemanticFieldKind kind; uint64_t optional_presence_mask; const uint16_t *allowed_target_section_types; size_t allowed_target_count; bool json_union_discriminator; };
template <typename Record> struct SemanticRecordTraits;

enum class WorkUnit : uint32_t
{
    MAC = 1,
    ADD = 2,
    SUB = 3,
    MUL = 4,
    DIV = 5,
    MAX = 6,
    EXP = 7,
    ERF = 8,
    TANH = 9,
    NEGATE = 10,
    RSQRT = 11,
    COPY = 12,
    GATHER = 13,
    PREDICATE = 14,
    LOGICAL_AND = 15,
    SELECT = 16,
    CAST = 17,
};

constexpr bool validWorkUnit(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      case 7: return true;
      case 8: return true;
      case 9: return true;
      case 10: return true;
      case 11: return true;
      case 12: return true;
      case 13: return true;
      case 14: return true;
      case 15: return true;
      case 16: return true;
      case 17: return true;
      default: return false;
    }
}

enum class Access : uint32_t
{
    READ_ONLY = 1,
    READ_WRITE = 2,
};

constexpr bool validAccess(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      default: return false;
    }
}

enum class DType : uint32_t
{
    FP32 = 1,
    FP16 = 2,
    BF16 = 3,
    INT8 = 4,
    INT32 = 5,
};

constexpr bool validDType(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      default: return false;
    }
}

enum class DmaKind : uint32_t
{
    LOAD = 1,
    STORE = 2,
    P2P_PUSH = 3,
    PREFETCH = 4,
    LOCAL_FILL = 5,
};

constexpr bool validDmaKind(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      default: return false;
    }
}

enum class Engine : uint32_t
{
    CONTROL = 1,
    DMA_READ = 2,
    DMA_WRITE = 3,
    TENSOR = 4,
    VECTOR = 5,
    REDUCE = 6,
};

constexpr bool validEngine(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      default: return false;
    }
}

enum class Layout : uint32_t
{
    CONTIGUOUS_ROW_MAJOR = 1,
    TRANSPOSED_2D_VIEW = 2,
    BLOCKED_MNK = 3,
};

constexpr bool validLayout(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      default: return false;
    }
}

enum class MemorySpace : uint32_t
{
    HBM = 1,
    HOST_SHARED = 2,
    CORE_SRAM = 3,
    PEER_SRAM = 4,
};

constexpr bool validMemorySpace(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      default: return false;
    }
}

enum class StorageClass : uint32_t
{
    EXTERNAL = 1,
    HBM = 2,
    HOST_SHARED = 3,
    CORE_SRAM = 4,
    PRE_RESIDENT = 5,
};

constexpr bool validStorageClass(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      default: return false;
    }
}

enum class TensorRole : uint32_t
{
    INPUT = 1,
    OUTPUT = 2,
    WEIGHT = 3,
    CONSTANT = 4,
    ACTIVATION = 5,
    KV_CACHE = 6,
    STATE = 7,
};

constexpr bool validTensorRole(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      case 7: return true;
      default: return false;
    }
}

enum class OpCode : uint32_t
{
    MATMUL = 1,
    BMM = 2,
    LINEAR_BIAS = 3,
    RESHAPE_VIEW = 4,
    TRANSPOSE_VIEW = 5,
    PERMUTE_VIEW = 6,
    SLICE_VIEW = 7,
    EXPAND_VIEW = 8,
    CONTIGUOUS_COPY = 9,
    CONCAT = 10,
    GATHER_ROWS = 11,
    ADD = 12,
    SUB = 13,
    MUL = 14,
    DIV = 15,
    RELU = 16,
    GELU = 17,
    SILU = 18,
    EXP = 19,
    RSQRT = 20,
    REDUCE_SUM = 21,
    REDUCE_MAX = 22,
    REDUCE_MEAN = 23,
    LAYERNORM = 24,
    RMSNORM = 25,
    SOFTMAX = 26,
    EMBEDDING_LOOKUP = 27,
};

constexpr bool validOpCode(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      case 7: return true;
      case 8: return true;
      case 9: return true;
      case 10: return true;
      case 11: return true;
      case 12: return true;
      case 13: return true;
      case 14: return true;
      case 15: return true;
      case 16: return true;
      case 17: return true;
      case 18: return true;
      case 19: return true;
      case 20: return true;
      case 21: return true;
      case 22: return true;
      case 23: return true;
      case 24: return true;
      case 25: return true;
      case 26: return true;
      case 27: return true;
      default: return false;
    }
}

enum class CollectiveAlgorithm : uint32_t
{
    AUTO = 1,
    RING = 2,
    TREE = 3,
};

constexpr bool validCollectiveAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      default: return false;
    }
}

enum class CollectiveKind : uint32_t
{
    ALL_REDUCE = 1,
};

constexpr bool validCollectiveKind(uint32_t value)
{
    switch (value) {
      case 1: return true;
      default: return false;
    }
}

enum class DistributionKind : uint32_t
{
    PARTITIONED = 1,
    REPLICATED = 2,
    PARTIAL_SUM = 3,
};

constexpr bool validDistributionKind(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      default: return false;
    }
}

enum class KernelOpcode : uint32_t
{
    ALLOC = 1,
    VIEW = 2,
    DMA = 3,
    GEMM = 4,
    BMM = 5,
    MATRIX_EPILOGUE = 6,
    VECTOR = 7,
    DATA_MOVEMENT = 8,
    REDUCE = 9,
    SOFTMAX = 10,
    NORM = 11,
    COLLECTIVE = 12,
    LOCAL_REDUCE = 13,
    LOCAL_COPY = 14,
    RECV_WAIT = 15,
    BARRIER = 16,
};

constexpr bool validKernelOpcode(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      case 7: return true;
      case 8: return true;
      case 9: return true;
      case 10: return true;
      case 11: return true;
      case 12: return true;
      case 13: return true;
      case 14: return true;
      case 15: return true;
      case 16: return true;
      default: return false;
    }
}

enum class MatrixEpilogueAlgorithm : uint32_t
{
    VECTOR_ACCUMULATION = 1,
};

constexpr bool validMatrixEpilogueAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      default: return false;
    }
}

enum class MatrixPhase : uint32_t
{
    DIRECT = 1,
    ACCUMULATE_ONLY = 2,
    ACCUMULATE_FIRST = 3,
    ACCUMULATE_CONTINUE = 4,
    ACCUMULATE_FINAL = 5,
};

constexpr bool validMatrixPhase(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      default: return false;
    }
}

enum class MovementAlgorithm : uint32_t
{
    STRIDED_COPY = 1,
    CONCAT = 2,
    GATHER_ROWS = 3,
};

constexpr bool validMovementAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      default: return false;
    }
}

enum class NormAlgorithm : uint32_t
{
    LAYER_NORM = 1,
    RMS_NORM = 2,
};

constexpr bool validNormAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      default: return false;
    }
}

enum class OperandAccessMode : uint32_t
{
    READ = 1,
    WRITE = 2,
};

constexpr bool validOperandAccessMode(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      default: return false;
    }
}

enum class ReduceKind : uint32_t
{
    SUM = 1,
};

constexpr bool validReduceKind(uint32_t value)
{
    switch (value) {
      case 1: return true;
      default: return false;
    }
}

enum class ReductionAlgorithm : uint32_t
{
    LEFT_TO_RIGHT = 1,
};

constexpr bool validReductionAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      default: return false;
    }
}

enum class SoftmaxAlgorithm : uint32_t
{
    STABLE_MAX_SUM = 1,
};

constexpr bool validSoftmaxAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      default: return false;
    }
}

enum class StateOrigin : uint32_t
{
    EMPTY = 1,
    EXTERNAL = 2,
    PRE_RESIDENT = 3,
    PRODUCED = 4,
};

constexpr bool validStateOrigin(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      default: return false;
    }
}

enum class SynthesizedTensorPurpose : uint32_t
{
    PADDING = 1,
    COPY = 2,
    ACCUMULATION = 3,
    PARTIAL_SUM = 4,
    REDUCTION = 5,
    DATA_MOVEMENT = 6,
};

constexpr bool validSynthesizedTensorPurpose(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      default: return false;
    }
}

enum class VectorAlgorithm : uint32_t
{
    ELEMENTWISE = 1,
    EMBEDDING_GATHER = 2,
};

constexpr bool validVectorAlgorithm(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      default: return false;
    }
}

enum class EndpointSide : uint32_t
{
    SRC = 1,
    DST = 2,
};

constexpr bool validEndpointSide(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      default: return false;
    }
}

enum class FenceScope : uint32_t
{
    DMA_READ = 1,
    DMA_WRITE = 2,
    P2P = 3,
    HOST_SHARED_WRITE = 4,
    ALL_INSTANCE = 5,
};

constexpr bool validFenceScope(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      default: return false;
    }
}

enum class ScheduledDependencyKind : uint32_t
{
    KERNEL_CONTROL = 1,
    KERNEL_STATE = 2,
    RAW = 3,
    WAR = 4,
    WAW = 5,
    DMA_PIN = 6,
    DMA_COMPLETION = 7,
    SRAM_REUSE = 8,
    STREAM_ORDER = 9,
    LIFECYCLE = 10,
};

constexpr bool validScheduledDependencyKind(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      case 5: return true;
      case 6: return true;
      case 7: return true;
      case 8: return true;
      case 9: return true;
      case 10: return true;
      default: return false;
    }
}

enum class AxiChannel : uint32_t
{
    AW = 0,
    W = 1,
    B = 2,
    AR = 3,
    R = 4,
};

constexpr bool validAxiChannel(uint32_t value)
{
    switch (value) {
      case 0: return true;
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      default: return false;
    }
}

enum class TrafficAggregateLevel : uint32_t
{
    PROGRAM = 1,
    ENTRYPOINT = 2,
    PROFILE = 3,
    DETAIL = 4,
};

constexpr bool validTrafficAggregateLevel(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      default: return false;
    }
}

enum class TrafficDirection : uint32_t
{
    READ = 1,
    WRITE = 2,
    P2P = 3,
    LOCAL = 4,
};

constexpr bool validTrafficDirection(uint32_t value)
{
    switch (value) {
      case 1: return true;
      case 2: return true;
      case 3: return true;
      case 4: return true;
      default: return false;
    }
}

inline bool zeroBytes(const uint8_t *data, uint32_t size)
{
    for (uint32_t index = 0; index < size; ++index) if (data[index] != 0) return false;
    return true;
}
inline int64_t rdI64(const uint8_t *data)
{
    uint64_t bits = mesh_abi::rdU64(data);
    int64_t value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}
inline double rdF64(const uint8_t *data)
{
    uint64_t bits = mesh_abi::rdU64(data);
    double value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}
inline void wrI64(uint8_t *data, int64_t value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    mesh_abi::wrU64(data, bits);
}
inline void wrF64(uint8_t *data, double value)
{
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    mesh_abi::wrU64(data, bits);
}
inline bool decodeSemanticRef(const uint8_t *data, SemanticRef &out, AbiError &error)
{
    out.section_type = mesh_abi::rdU16(data + kSemanticRefSectionTypeOffset);
    if (!zeroBytes(data + kSemanticRefReservedOffset, 2)) { error = {mesh_diagnostics::E_ABI_RESERVED, "semantic_ref reserved field is nonzero"}; return false; }
    out.row_id = mesh_abi::rdU32(data + kSemanticRefRowIdOffset);
    return true;
}
inline void encodeSemanticRef(uint8_t *data, const SemanticRef &value)
{
    std::memset(data, 0, kSemanticRefBytes);
    mesh_abi::wrU16(data + kSemanticRefSectionTypeOffset, value.section_type);
    mesh_abi::wrU32(data + kSemanticRefRowIdOffset, value.row_id);
}
inline bool decodeListSpan(const uint8_t *data, ListSpan &out, AbiError &error)
{
    out.begin = mesh_abi::rdU32(data + kListSpanBeginOffset);
    out.count = mesh_abi::rdU32(data + kListSpanCountOffset);
    if (out.count == 0 && out.begin != 0) { error = {mesh_diagnostics::E_ABI_ORDER, "empty list span has nonzero begin"}; return false; }
    return true;
}
inline void encodeListSpan(uint8_t *data, const ListSpan &value)
{
    mesh_abi::wrU32(data + kListSpanBeginOffset, value.begin);
    mesh_abi::wrU32(data + kListSpanCountOffset, value.count);
}
inline bool decodeStringRef(const uint8_t *data, StringRef &out, AbiError &error)
{
    out.string_id = mesh_abi::rdU32(data + kStringRefStringIdOffset);
    if (!zeroBytes(data + kStringRefReservedOffset, 4)) { error = {mesh_diagnostics::E_ABI_RESERVED, "string reference reserved field is nonzero"}; return false; }
    if (out.string_id == 0) { error = {mesh_diagnostics::E_ABI_BOUNDS, "string reference is absent"}; return false; }
    return true;
}
inline void encodeStringRef(uint8_t *data, const StringRef &value)
{
    std::memset(data, 0, kStringRefBytes);
    mesh_abi::wrU32(data + kStringRefStringIdOffset, value.string_id);
}
inline bool decodeProgramMetadata(const uint8_t *data, ProgramMetadata &out, AbiError &error)
{
    out.min_reader_minor = mesh_abi::rdU16(data + kProgramMetadataMinReaderMinorOffset);
    if (!zeroBytes(data + kProgramMetadataFlagsOffset, kProgramMetadataSemanticsOffset - kProgramMetadataFlagsOffset)) { error = {mesh_diagnostics::E_ABI_RESERVED, "PROGRAM_METADATA reserved fields are nonzero"}; return false; }
    if (!decodeSemanticRef(data + kProgramMetadataSemanticsOffset, out.semantics, error)) return false;
    if (out.semantics.section_type != 330 || out.semantics.row_id != 1) { error = {mesh_diagnostics::E_ABI_BOUNDS, "PROGRAM_METADATA root is invalid"}; return false; }
    std::memcpy(out.semantic_sha256.data(), data + kProgramMetadataSemanticSha256Offset, out.semantic_sha256.size());
    return true;
}
inline std::array<uint8_t, kProgramMetadataBytes> encodeProgramMetadata(const ProgramMetadata &value)
{
    std::array<uint8_t, kProgramMetadataBytes> data{};
    mesh_abi::wrU16(data.data() + kProgramMetadataMinReaderMinorOffset, value.min_reader_minor);
    encodeSemanticRef(data.data() + kProgramMetadataSemanticsOffset, value.semantics);
    std::memcpy(data.data() + kProgramMetadataSemanticSha256Offset, value.semantic_sha256.data(), value.semantic_sha256.size());
    return data;
}
inline bool decodeSemanticIntegerValue(const uint8_t *data, SemanticIntegerValue &out, AbiError &error)
{
    out.kind = mesh_abi::rdU16(data + kSemanticIntegerValueKindOffset);
    out.payload = mesh_abi::rdU64(data + kSemanticIntegerValuePayloadOffset);
    if (!zeroBytes(data + kSemanticIntegerValueReservedOffset, kSemanticIntegerValuePayloadOffset - kSemanticIntegerValueReservedOffset)) { error = {mesh_diagnostics::E_ABI_RESERVED, "semantic integer reserved bytes are nonzero"}; return false; }
    if (out.kind != kScalarKindI64 && out.kind != kScalarKindU64) { error = {mesh_diagnostics::E_ABI_ENUM, "semantic integer kind is invalid"}; return false; }
    if (out.kind == kScalarKindI64 && rdI64(data + kSemanticIntegerValuePayloadOffset) >= 0) { error = {mesh_diagnostics::E_ABI_ORDER, "semantic signed integer is noncanonical"}; return false; }
    return true;
}
inline std::array<uint8_t, kSemanticIntegerValueBytes> encodeSemanticIntegerValue(const SemanticIntegerValue &value)
{
    std::array<uint8_t, kSemanticIntegerValueBytes> data{};
    mesh_abi::wrU16(data.data() + kSemanticIntegerValueKindOffset, value.kind);
    mesh_abi::wrU64(data.data() + kSemanticIntegerValuePayloadOffset, value.payload);
    return data;
}

#include "dev/ai_mesh/generated/mesh_ir_semantic_enum_traits.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_records.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_analysis.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_common.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_graph.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_kernel.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_scheduled.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_codecs_traffic.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_analysis.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_common.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_graph.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_kernel.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_scheduled.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_fields_traffic.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_membership.hh"
#include "dev/ai_mesh/generated/mesh_ir_semantic_storage.hh"

}
}
}
}

#endif

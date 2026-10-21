#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_ENUMS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_ENUMS_HH

enum class Opcode : uint16_t
{
    REQUEST_BEGIN = 1,
    REQUEST_END = 2,
    HALT = 3,
    DMA_LOAD = 4,
    DMA_STORE = 5,
    DMA_P2P_PUSH = 6,
    DMA_PREFETCH = 7,
    DMA_FILL = 8,
    AXI_FENCE = 9,
    GEMM = 10,
    BMM = 11,
    ELEMENTWISE = 12,
    LOCAL_REDUCE = 13,
    SOFTMAX = 14,
    NORM = 15,
    EVENT_WAIT = 16,
    EVENT_SIGNAL = 17,
    RECV_WAIT = 18,
    BARRIER = 19,
    REPEAT = 20,
    DMA_GATHER_LOAD = 21,
};

constexpr uint16_t kOpcodeREQUEST_BEGIN = 1;
constexpr uint16_t kOpcodeREQUEST_END = 2;
constexpr uint16_t kOpcodeHALT = 3;
constexpr uint16_t kOpcodeDMA_LOAD = 4;
constexpr uint16_t kOpcodeDMA_STORE = 5;
constexpr uint16_t kOpcodeDMA_P2P_PUSH = 6;
constexpr uint16_t kOpcodeDMA_PREFETCH = 7;
constexpr uint16_t kOpcodeDMA_FILL = 8;
constexpr uint16_t kOpcodeAXI_FENCE = 9;
constexpr uint16_t kOpcodeGEMM = 10;
constexpr uint16_t kOpcodeBMM = 11;
constexpr uint16_t kOpcodeELEMENTWISE = 12;
constexpr uint16_t kOpcodeLOCAL_REDUCE = 13;
constexpr uint16_t kOpcodeSOFTMAX = 14;
constexpr uint16_t kOpcodeNORM = 15;
constexpr uint16_t kOpcodeEVENT_WAIT = 16;
constexpr uint16_t kOpcodeEVENT_SIGNAL = 17;
constexpr uint16_t kOpcodeRECV_WAIT = 18;
constexpr uint16_t kOpcodeBARRIER = 19;
constexpr uint16_t kOpcodeREPEAT = 20;
constexpr uint16_t kOpcodeDMA_GATHER_LOAD = 21;

constexpr bool validOpcode(uint16_t value)
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
    default: return false;
    }
}

enum class Engine : uint16_t
{
    CONTROL = 1,
    DMA_READ = 2,
    DMA_WRITE = 3,
    TENSOR = 4,
    VECTOR = 5,
    REDUCE = 6,
};

constexpr uint16_t kEngineCONTROL = 1;
constexpr uint16_t kEngineDMA_READ = 2;
constexpr uint16_t kEngineDMA_WRITE = 3;
constexpr uint16_t kEngineTENSOR = 4;
constexpr uint16_t kEngineVECTOR = 5;
constexpr uint16_t kEngineREDUCE = 6;

constexpr bool validEngine(uint16_t value)
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

enum class MemorySpace : uint16_t
{
    HBM = 1,
    HOST_SHARED = 2,
    CORE_SRAM = 3,
    PEER_SRAM = 4,
};

constexpr uint16_t kMemorySpaceHBM = 1;
constexpr uint16_t kMemorySpaceHOST_SHARED = 2;
constexpr uint16_t kMemorySpaceCORE_SRAM = 3;
constexpr uint16_t kMemorySpacePEER_SRAM = 4;

constexpr bool validMemorySpace(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    default: return false;
    }
}

enum class TensorRole : uint16_t
{
    INPUT = 1,
    OUTPUT = 2,
    WEIGHT = 3,
    CONSTANT = 4,
    ACTIVATION = 5,
    KV_CACHE = 6,
    STATE = 7,
};

constexpr uint16_t kTensorRoleINPUT = 1;
constexpr uint16_t kTensorRoleOUTPUT = 2;
constexpr uint16_t kTensorRoleWEIGHT = 3;
constexpr uint16_t kTensorRoleCONSTANT = 4;
constexpr uint16_t kTensorRoleACTIVATION = 5;
constexpr uint16_t kTensorRoleKV_CACHE = 6;
constexpr uint16_t kTensorRoleSTATE = 7;

constexpr bool validTensorRole(uint16_t value)
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

enum class Dtype : uint16_t
{
    FP32 = 1,
    FP16 = 2,
    BF16 = 3,
    INT8 = 4,
    INT32 = 5,
};

constexpr uint16_t kDtypeFP32 = 1;
constexpr uint16_t kDtypeFP16 = 2;
constexpr uint16_t kDtypeBF16 = 3;
constexpr uint16_t kDtypeINT8 = 4;
constexpr uint16_t kDtypeINT32 = 5;

constexpr bool validDtype(uint16_t value)
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

enum class StorageClass : uint16_t
{
    EXTERNAL = 1,
    HBM = 2,
    HOST_SHARED = 3,
    CORE_SRAM = 4,
    PRE_RESIDENT = 5,
};

constexpr uint16_t kStorageClassEXTERNAL = 1;
constexpr uint16_t kStorageClassHBM = 2;
constexpr uint16_t kStorageClassHOST_SHARED = 3;
constexpr uint16_t kStorageClassCORE_SRAM = 4;
constexpr uint16_t kStorageClassPRE_RESIDENT = 5;

constexpr bool validStorageClass(uint16_t value)
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

enum class AccessKind : uint16_t
{
    READ_ONLY = 1,
    READ_WRITE = 2,
};

constexpr uint16_t kAccessKindREAD_ONLY = 1;
constexpr uint16_t kAccessKindREAD_WRITE = 2;

constexpr bool validAccessKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class LayoutKind : uint16_t
{
    CONTIGUOUS_ROW_MAJOR = 1,
    TRANSPOSED_2D_VIEW = 2,
    BLOCKED_MNK = 3,
};

constexpr uint16_t kLayoutKindCONTIGUOUS_ROW_MAJOR = 1;
constexpr uint16_t kLayoutKindTRANSPOSED_2D_VIEW = 2;
constexpr uint16_t kLayoutKindBLOCKED_MNK = 3;

constexpr bool validLayoutKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class DmaKind : uint16_t
{
    LOAD = 1,
    STORE = 2,
    P2P_PUSH = 3,
    PREFETCH = 4,
    LOCAL_FILL = 5,
    GATHER_LOAD = 6,
};

constexpr uint16_t kDmaKindLOAD = 1;
constexpr uint16_t kDmaKindSTORE = 2;
constexpr uint16_t kDmaKindP2P_PUSH = 3;
constexpr uint16_t kDmaKindPREFETCH = 4;
constexpr uint16_t kDmaKindLOCAL_FILL = 5;
constexpr uint16_t kDmaKindGATHER_LOAD = 6;

constexpr bool validDmaKind(uint16_t value)
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

enum class EventKind : uint16_t
{
    NORMAL = 1,
    BARRIER = 2,
};

constexpr uint16_t kEventKindNORMAL = 1;
constexpr uint16_t kEventKindBARRIER = 2;

constexpr bool validEventKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class AttrKind : uint16_t
{
    REPEAT_V1 = 1,
    GEMM_V1 = 2,
    BMM_V1 = 3,
    ELEMENTWISE_V1 = 4,
    REDUCE_V1 = 5,
    SOFTMAX_V1 = 6,
    NORM_V1 = 7,
    FILL_V1 = 8,
    BLOCKED_MNK_LAYOUT_V1 = 9,
    RECV_WAIT_V1 = 10,
    FENCE_V1 = 11,
    GATHER_INDEX_V1 = 12,
};

constexpr uint16_t kAttrKindREPEAT_V1 = 1;
constexpr uint16_t kAttrKindGEMM_V1 = 2;
constexpr uint16_t kAttrKindBMM_V1 = 3;
constexpr uint16_t kAttrKindELEMENTWISE_V1 = 4;
constexpr uint16_t kAttrKindREDUCE_V1 = 5;
constexpr uint16_t kAttrKindSOFTMAX_V1 = 6;
constexpr uint16_t kAttrKindNORM_V1 = 7;
constexpr uint16_t kAttrKindFILL_V1 = 8;
constexpr uint16_t kAttrKindBLOCKED_MNK_LAYOUT_V1 = 9;
constexpr uint16_t kAttrKindRECV_WAIT_V1 = 10;
constexpr uint16_t kAttrKindFENCE_V1 = 11;
constexpr uint16_t kAttrKindGATHER_INDEX_V1 = 12;

constexpr bool validAttrKind(uint16_t value)
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
    default: return false;
    }
}

enum class RelocationKind : uint16_t
{
    REGION_BASE = 1,
    TENSOR_BASE = 2,
};

constexpr uint16_t kRelocationKindREGION_BASE = 1;
constexpr uint16_t kRelocationKindTENSOR_BASE = 2;

constexpr bool validRelocationKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class VectorAlgorithm : uint16_t
{
    STANDARD = 1,
};

constexpr uint16_t kVectorAlgorithmSTANDARD = 1;

constexpr bool validVectorAlgorithm(uint16_t value)
{
    switch (value) {
    case 1: return true;
    default: return false;
    }
}

enum class FenceScope : uint16_t
{
    DMA_READ = 1,
    DMA_WRITE = 2,
    P2P = 3,
    HOST_SHARED_WRITE = 4,
    ALL_INSTANCE = 5,
};

constexpr uint16_t kFenceScopeDMA_READ = 1;
constexpr uint16_t kFenceScopeDMA_WRITE = 2;
constexpr uint16_t kFenceScopeP2P = 3;
constexpr uint16_t kFenceScopeHOST_SHARED_WRITE = 4;
constexpr uint16_t kFenceScopeALL_INSTANCE = 5;

constexpr bool validFenceScope(uint16_t value)
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

enum class StreamFlags : uint16_t
{
    IS_LIFECYCLE = 1,
    IS_LOCAL_CONTROL = 2,
};

constexpr uint16_t kStreamFlagsIS_LIFECYCLE = 1;
constexpr uint16_t kStreamFlagsIS_LOCAL_CONTROL = 2;

constexpr bool validStreamFlags(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class TensorFlags : uint32_t
{
    HAS_CONTENT_SHA256 = 1,
};

constexpr uint32_t kTensorFlagsHAS_CONTENT_SHA256 = 1;

constexpr bool validTensorFlags(uint32_t value)
{
    switch (value) {
    case 1: return true;
    default: return false;
    }
}

enum class ScalarKind : uint16_t
{
    BOOL = 1,
    I64 = 2,
    U64 = 3,
    F64 = 4,
};

constexpr uint16_t kScalarKindBOOL = 1;
constexpr uint16_t kScalarKindI64 = 2;
constexpr uint16_t kScalarKindU64 = 3;
constexpr uint16_t kScalarKindF64 = 4;

constexpr bool validScalarKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    default: return false;
    }
}

enum class ContentDigestObjectKind : uint16_t
{
    TENSOR = 1,
    SHARD = 2,
    ALLOCATION = 3,
    KERNEL_OBJECT = 4,
};

constexpr uint16_t kContentDigestObjectKindTENSOR = 1;
constexpr uint16_t kContentDigestObjectKindSHARD = 2;
constexpr uint16_t kContentDigestObjectKindALLOCATION = 3;
constexpr uint16_t kContentDigestObjectKindKERNEL_OBJECT = 4;

constexpr bool validContentDigestObjectKind(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    default: return false;
    }
}

enum class MeshObjectDomain : uint16_t
{
    STATIC = 0,
    MOE_OVERLAY = 1,
    WEIGHT_FILL = 2,
    COORDINATOR = 3,
    KV_RUNTIME = 4,
    INTERNAL = 5,
};

constexpr uint16_t kMeshObjectDomainSTATIC = 0;
constexpr uint16_t kMeshObjectDomainMOE_OVERLAY = 1;
constexpr uint16_t kMeshObjectDomainWEIGHT_FILL = 2;
constexpr uint16_t kMeshObjectDomainCOORDINATOR = 3;
constexpr uint16_t kMeshObjectDomainKV_RUNTIME = 4;
constexpr uint16_t kMeshObjectDomainINTERNAL = 5;

constexpr bool validMeshObjectDomain(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class MeshObjectKind : uint16_t
{
    COMMAND = 0,
    EVENT = 1,
    DESCRIPTOR = 2,
    TRANSFER = 3,
    ALLOCATION = 4,
    VIEW = 5,
};

constexpr uint16_t kMeshObjectKindCOMMAND = 0;
constexpr uint16_t kMeshObjectKindEVENT = 1;
constexpr uint16_t kMeshObjectKindDESCRIPTOR = 2;
constexpr uint16_t kMeshObjectKindTRANSFER = 3;
constexpr uint16_t kMeshObjectKindALLOCATION = 4;
constexpr uint16_t kMeshObjectKindVIEW = 5;

constexpr bool validMeshObjectKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class ErrorSourceObjectKind : uint16_t
{
    COMMAND = 0,
    EVENT = 1,
    DESCRIPTOR = 2,
    TRANSFER = 3,
    ALLOCATION = 4,
    WEIGHT_FILL_OBLIGATION = 5,
    CACHE_RESERVATION = 6,
    MATERIALIZER_CHECK = 7,
    KV_APPEND = 8,
    INTERNAL_CHECK = 9,
};

constexpr uint16_t kErrorSourceObjectKindCOMMAND = 0;
constexpr uint16_t kErrorSourceObjectKindEVENT = 1;
constexpr uint16_t kErrorSourceObjectKindDESCRIPTOR = 2;
constexpr uint16_t kErrorSourceObjectKindTRANSFER = 3;
constexpr uint16_t kErrorSourceObjectKindALLOCATION = 4;
constexpr uint16_t kErrorSourceObjectKindWEIGHT_FILL_OBLIGATION = 5;
constexpr uint16_t kErrorSourceObjectKindCACHE_RESERVATION = 6;
constexpr uint16_t kErrorSourceObjectKindMATERIALIZER_CHECK = 7;
constexpr uint16_t kErrorSourceObjectKindKV_APPEND = 8;
constexpr uint16_t kErrorSourceObjectKindINTERNAL_CHECK = 9;

constexpr bool validErrorSourceObjectKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    case 6: return true;
    case 7: return true;
    case 8: return true;
    case 9: return true;
    default: return false;
    }
}

enum class MoeErrorClass : uint16_t
{
    WEIGHT_FILL = 0,
    CACHE_RESERVATION = 1,
    DMA_AXI = 2,
    SRAM = 3,
    COMMAND = 4,
    EVENT = 5,
    MATERIALIZE = 6,
    INTERNAL = 7,
};

constexpr uint16_t kMoeErrorClassWEIGHT_FILL = 0;
constexpr uint16_t kMoeErrorClassCACHE_RESERVATION = 1;
constexpr uint16_t kMoeErrorClassDMA_AXI = 2;
constexpr uint16_t kMoeErrorClassSRAM = 3;
constexpr uint16_t kMoeErrorClassCOMMAND = 4;
constexpr uint16_t kMoeErrorClassEVENT = 5;
constexpr uint16_t kMoeErrorClassMATERIALIZE = 6;
constexpr uint16_t kMoeErrorClassINTERNAL = 7;

constexpr bool validMoeErrorClass(uint16_t value)
{
    switch (value) {
    case 0: return true;
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

enum class InternalCheckSite : uint16_t
{
    CACHE_STATE = 0,
    TOKEN_OWNER = 1,
    KV_JOIN = 2,
    LEDGER = 3,
};

constexpr uint16_t kInternalCheckSiteCACHE_STATE = 0;
constexpr uint16_t kInternalCheckSiteTOKEN_OWNER = 1;
constexpr uint16_t kInternalCheckSiteKV_JOIN = 2;
constexpr uint16_t kInternalCheckSiteLEDGER = 3;

constexpr bool validInternalCheckSite(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class MaterializerCheckSite : uint16_t
{
    COUNT = 0,
    KEY_COLLISION = 1,
    BOUNDS = 2,
    DAG = 3,
    TRAFFIC = 4,
    CACHE_BINDING = 5,
};

constexpr uint16_t kMaterializerCheckSiteCOUNT = 0;
constexpr uint16_t kMaterializerCheckSiteKEY_COLLISION = 1;
constexpr uint16_t kMaterializerCheckSiteBOUNDS = 2;
constexpr uint16_t kMaterializerCheckSiteDAG = 3;
constexpr uint16_t kMaterializerCheckSiteTRAFFIC = 4;
constexpr uint16_t kMaterializerCheckSiteCACHE_BINDING = 5;

constexpr bool validMaterializerCheckSite(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class WeightFillFailureSite : uint16_t
{
    CACHE_FILL_AXI_R = 0,
    CACHE_FILL_SRAM_BOUNDS = 1,
    CACHE_FILL_SRAM_COMMIT = 2,
    CACHE_FILL_SOURCE_VALIDITY = 3,
};

constexpr uint16_t kWeightFillFailureSiteCACHE_FILL_AXI_R = 0;
constexpr uint16_t kWeightFillFailureSiteCACHE_FILL_SRAM_BOUNDS = 1;
constexpr uint16_t kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT = 2;
constexpr uint16_t kWeightFillFailureSiteCACHE_FILL_SOURCE_VALIDITY = 3;

constexpr bool validWeightFillFailureSite(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class CacheSlotState : uint16_t
{
    INVALID = 0,
    VALID = 1,
};

constexpr uint16_t kCacheSlotStateINVALID = 0;
constexpr uint16_t kCacheSlotStateVALID = 1;

constexpr bool validCacheSlotState(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class CacheResidencyOutcome : uint16_t
{
    HIT = 0,
    ATTACH = 1,
    NEW_FILL = 2,
    STREAMED = 3,
};

constexpr uint16_t kCacheResidencyOutcomeHIT = 0;
constexpr uint16_t kCacheResidencyOutcomeATTACH = 1;
constexpr uint16_t kCacheResidencyOutcomeNEW_FILL = 2;
constexpr uint16_t kCacheResidencyOutcomeSTREAMED = 3;

constexpr bool validCacheResidencyOutcome(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class CacheSubscriberState : uint16_t
{
    WAITING = 0,
    WOKEN = 1,
    FAIL_NOTIFIED = 2,
    TERMINAL_TOMBSTONED = 3,
    CANCELLED = 4,
    RELEASED = 5,
};

constexpr uint16_t kCacheSubscriberStateWAITING = 0;
constexpr uint16_t kCacheSubscriberStateWOKEN = 1;
constexpr uint16_t kCacheSubscriberStateFAIL_NOTIFIED = 2;
constexpr uint16_t kCacheSubscriberStateTERMINAL_TOMBSTONED = 3;
constexpr uint16_t kCacheSubscriberStateCANCELLED = 4;
constexpr uint16_t kCacheSubscriberStateRELEASED = 5;

constexpr bool validCacheSubscriberState(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class CacheObligationState : uint16_t
{
    RESERVED = 0,
    ISSUING = 1,
    IN_FLIGHT = 2,
    FAILED_DRAINING = 3,
    FAILED_RETIRED = 4,
    RETIRED = 5,
};

constexpr uint16_t kCacheObligationStateRESERVED = 0;
constexpr uint16_t kCacheObligationStateISSUING = 1;
constexpr uint16_t kCacheObligationStateIN_FLIGHT = 2;
constexpr uint16_t kCacheObligationStateFAILED_DRAINING = 3;
constexpr uint16_t kCacheObligationStateFAILED_RETIRED = 4;
constexpr uint16_t kCacheObligationStateRETIRED = 5;

constexpr bool validCacheObligationState(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class MeshCoreInstanceState : uint16_t
{
    PROGRAM_READY = 0,
    REQUEST_ARMED = 1,
    REQUEST_RUNNING = 2,
    REQUEST_DRAINING = 3,
    INSTANCE_DONE = 4,
    INSTANCE_ERROR_DRAINING = 5,
    INSTANCE_OWNED_WORK_DRAINED = 6,
    INSTANCE_ERROR_DRAINED = 7,
};

constexpr uint16_t kMeshCoreInstanceStatePROGRAM_READY = 0;
constexpr uint16_t kMeshCoreInstanceStateREQUEST_ARMED = 1;
constexpr uint16_t kMeshCoreInstanceStateREQUEST_RUNNING = 2;
constexpr uint16_t kMeshCoreInstanceStateREQUEST_DRAINING = 3;
constexpr uint16_t kMeshCoreInstanceStateINSTANCE_DONE = 4;
constexpr uint16_t kMeshCoreInstanceStateINSTANCE_ERROR_DRAINING = 5;
constexpr uint16_t kMeshCoreInstanceStateINSTANCE_OWNED_WORK_DRAINED = 6;
constexpr uint16_t kMeshCoreInstanceStateINSTANCE_ERROR_DRAINED = 7;

constexpr bool validMeshCoreInstanceState(uint16_t value)
{
    switch (value) {
    case 0: return true;
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

enum class MeshBatchState : uint16_t
{
    PROGRAM_READY = 0,
    REQUEST_ARMED = 1,
    REQUEST_RUNNING = 2,
    BATCH_PRESTART_ABORTING = 3,
    BATCH_PRESTART_DRAINED = 4,
    FINAL_DRAINING = 5,
    GLOBAL_QUIESCENT = 6,
};

constexpr uint16_t kMeshBatchStatePROGRAM_READY = 0;
constexpr uint16_t kMeshBatchStateREQUEST_ARMED = 1;
constexpr uint16_t kMeshBatchStateREQUEST_RUNNING = 2;
constexpr uint16_t kMeshBatchStateBATCH_PRESTART_ABORTING = 3;
constexpr uint16_t kMeshBatchStateBATCH_PRESTART_DRAINED = 4;
constexpr uint16_t kMeshBatchStateFINAL_DRAINING = 5;
constexpr uint16_t kMeshBatchStateGLOBAL_QUIESCENT = 6;

constexpr bool validMeshBatchState(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    case 6: return true;
    default: return false;
    }
}

enum class SramPartitionKind : uint16_t
{
    STATIC_PROGRAM = 0,
    WEIGHT_CACHE = 1,
    KV_STAGING_CACHE = 2,
    RUNTIME_SCRATCH = 3,
};

constexpr uint16_t kSramPartitionKindSTATIC_PROGRAM = 0;
constexpr uint16_t kSramPartitionKindWEIGHT_CACHE = 1;
constexpr uint16_t kSramPartitionKindKV_STAGING_CACHE = 2;
constexpr uint16_t kSramPartitionKindRUNTIME_SCRATCH = 3;

constexpr bool validSramPartitionKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class MoeAllocationKind : uint16_t
{
    ROUTE_BUFFER = 0,
    STREAMED_WEIGHT = 1,
    DISPATCH_REMOTE = 2,
    PAD_INPUT = 3,
    EXPERT_OUTPUT = 4,
    COMBINE_REMOTE = 5,
    REDUCE_OUTPUT = 6,
};

constexpr uint16_t kMoeAllocationKindROUTE_BUFFER = 0;
constexpr uint16_t kMoeAllocationKindSTREAMED_WEIGHT = 1;
constexpr uint16_t kMoeAllocationKindDISPATCH_REMOTE = 2;
constexpr uint16_t kMoeAllocationKindPAD_INPUT = 3;
constexpr uint16_t kMoeAllocationKindEXPERT_OUTPUT = 4;
constexpr uint16_t kMoeAllocationKindCOMBINE_REMOTE = 5;
constexpr uint16_t kMoeAllocationKindREDUCE_OUTPUT = 6;

constexpr bool validMoeAllocationKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    case 6: return true;
    default: return false;
    }
}

enum class MoeViewKind : uint16_t
{
    ROUTE_METADATA = 0,
    MEMBER_INPUT = 1,
    DISPATCH_BUFFER = 2,
    PAD_BUFFER = 3,
    WEIGHT = 4,
    EXPERT_OUTPUT = 5,
    COMBINE_BUFFER = 6,
    REDUCE_ACCUMULATOR = 7,
    MEMBER_OUTPUT = 8,
};

constexpr uint16_t kMoeViewKindROUTE_METADATA = 0;
constexpr uint16_t kMoeViewKindMEMBER_INPUT = 1;
constexpr uint16_t kMoeViewKindDISPATCH_BUFFER = 2;
constexpr uint16_t kMoeViewKindPAD_BUFFER = 3;
constexpr uint16_t kMoeViewKindWEIGHT = 4;
constexpr uint16_t kMoeViewKindEXPERT_OUTPUT = 5;
constexpr uint16_t kMoeViewKindCOMBINE_BUFFER = 6;
constexpr uint16_t kMoeViewKindREDUCE_ACCUMULATOR = 7;
constexpr uint16_t kMoeViewKindMEMBER_OUTPUT = 8;

constexpr bool validMoeViewKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    case 6: return true;
    case 7: return true;
    case 8: return true;
    default: return false;
    }
}

enum class MoeViewAccess : uint16_t
{
    READ = 0,
    WRITE = 1,
    READ_WRITE = 2,
};

constexpr uint16_t kMoeViewAccessREAD = 0;
constexpr uint16_t kMoeViewAccessWRITE = 1;
constexpr uint16_t kMoeViewAccessREAD_WRITE = 2;

constexpr bool validMoeViewAccess(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class MoeViewBacking : uint16_t
{
    OVERLAY_ALLOCATION = 0,
    STATIC_ALLOCATION = 1,
    INSTANCE_MEMBER_BINDING = 2,
    WEIGHT_CACHE_SLOT = 3,
    KV_RUNTIME_VIEW = 4,
};

constexpr uint16_t kMoeViewBackingOVERLAY_ALLOCATION = 0;
constexpr uint16_t kMoeViewBackingSTATIC_ALLOCATION = 1;
constexpr uint16_t kMoeViewBackingINSTANCE_MEMBER_BINDING = 2;
constexpr uint16_t kMoeViewBackingWEIGHT_CACHE_SLOT = 3;
constexpr uint16_t kMoeViewBackingKV_RUNTIME_VIEW = 4;

constexpr bool validMoeViewBacking(uint16_t value)
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

enum class MoeSemanticOwner : uint16_t
{
    MEMBER = 0,
    EXPERT = 1,
    TOKEN = 2,
    REGION = 3,
};

constexpr uint16_t kMoeSemanticOwnerMEMBER = 0;
constexpr uint16_t kMoeSemanticOwnerEXPERT = 1;
constexpr uint16_t kMoeSemanticOwnerTOKEN = 2;
constexpr uint16_t kMoeSemanticOwnerREGION = 3;

constexpr bool validMoeSemanticOwner(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class MoeValidityKind : uint16_t
{
    FULL_PREFIX = 0,
    ROW_BITMAP = 1,
};

constexpr uint16_t kMoeValidityKindFULL_PREFIX = 0;
constexpr uint16_t kMoeValidityKindROW_BITMAP = 1;

constexpr bool validMoeValidityKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class MoeTrafficClass : uint16_t
{
    ACTIVATION = 0,
    WEIGHT = 1,
    MOE_DISPATCH = 2,
    MOE_COMBINE = 3,
    PARTIAL_RESULT = 4,
};

constexpr uint16_t kMoeTrafficClassACTIVATION = 0;
constexpr uint16_t kMoeTrafficClassWEIGHT = 1;
constexpr uint16_t kMoeTrafficClassMOE_DISPATCH = 2;
constexpr uint16_t kMoeTrafficClassMOE_COMBINE = 3;
constexpr uint16_t kMoeTrafficClassPARTIAL_RESULT = 4;

constexpr bool validMoeTrafficClass(uint16_t value)
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

enum class MoeTransferPhase : uint16_t
{
    DISPATCH = 0,
    COMBINE = 1,
};

constexpr uint16_t kMoeTransferPhaseDISPATCH = 0;
constexpr uint16_t kMoeTransferPhaseCOMBINE = 1;

constexpr bool validMoeTransferPhase(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class MoeDescriptorKind : uint16_t
{
    ROUTE_FILL = 0,
    STREAMED_WEIGHT = 1,
    PAD_FILL = 2,
    DISPATCH = 3,
    COMBINE = 4,
    DROPPED_TOKEN_FILL = 5,
};

constexpr uint16_t kMoeDescriptorKindROUTE_FILL = 0;
constexpr uint16_t kMoeDescriptorKindSTREAMED_WEIGHT = 1;
constexpr uint16_t kMoeDescriptorKindPAD_FILL = 2;
constexpr uint16_t kMoeDescriptorKindDISPATCH = 3;
constexpr uint16_t kMoeDescriptorKindCOMBINE = 4;
constexpr uint16_t kMoeDescriptorKindDROPPED_TOKEN_FILL = 5;

constexpr bool validMoeDescriptorKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    case 3: return true;
    case 4: return true;
    case 5: return true;
    default: return false;
    }
}

enum class MoeEventPhase : uint16_t
{
    ENTRY = 0,
    FILL = 1,
    DISPATCH = 2,
    WEIGHT = 3,
    EXPERT = 4,
    COMBINE = 5,
    REDUCE = 6,
    EXIT = 7,
};

constexpr uint16_t kMoeEventPhaseENTRY = 0;
constexpr uint16_t kMoeEventPhaseFILL = 1;
constexpr uint16_t kMoeEventPhaseDISPATCH = 2;
constexpr uint16_t kMoeEventPhaseWEIGHT = 3;
constexpr uint16_t kMoeEventPhaseEXPERT = 4;
constexpr uint16_t kMoeEventPhaseCOMBINE = 5;
constexpr uint16_t kMoeEventPhaseREDUCE = 6;
constexpr uint16_t kMoeEventPhaseEXIT = 7;

constexpr bool validMoeEventPhase(uint16_t value)
{
    switch (value) {
    case 0: return true;
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

enum class MoeCommandPhase : uint16_t
{
    ROUTE_FILL = 0,
    STREAMED_WEIGHT_LOAD = 1,
    DISPATCH_PUSH = 2,
    DISPATCH_WAIT = 3,
    PAD_FILL = 4,
    EXPERT_COMPUTE = 5,
    COMBINE_PUSH = 6,
    COMBINE_WAIT = 7,
    DROPPED_TOKEN_FILL = 8,
    COPY_THROUGH = 9,
    LOCAL_REDUCE = 10,
    EXIT_SIGNAL = 11,
};

constexpr uint16_t kMoeCommandPhaseROUTE_FILL = 0;
constexpr uint16_t kMoeCommandPhaseSTREAMED_WEIGHT_LOAD = 1;
constexpr uint16_t kMoeCommandPhaseDISPATCH_PUSH = 2;
constexpr uint16_t kMoeCommandPhaseDISPATCH_WAIT = 3;
constexpr uint16_t kMoeCommandPhasePAD_FILL = 4;
constexpr uint16_t kMoeCommandPhaseEXPERT_COMPUTE = 5;
constexpr uint16_t kMoeCommandPhaseCOMBINE_PUSH = 6;
constexpr uint16_t kMoeCommandPhaseCOMBINE_WAIT = 7;
constexpr uint16_t kMoeCommandPhaseDROPPED_TOKEN_FILL = 8;
constexpr uint16_t kMoeCommandPhaseCOPY_THROUGH = 9;
constexpr uint16_t kMoeCommandPhaseLOCAL_REDUCE = 10;
constexpr uint16_t kMoeCommandPhaseEXIT_SIGNAL = 11;

constexpr bool validMoeCommandPhase(uint16_t value)
{
    switch (value) {
    case 0: return true;
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
    default: return false;
    }
}

enum class MoeEventRole : uint16_t
{
    ENTRY_GATE = 0,
    ROUTE_FILL_DONE = 1,
    DISPATCH_READY = 2,
    WEIGHT_READY = 3,
    EXPERT_RESULT = 4,
    COMBINE_READY = 5,
    TOKEN_OUTPUT_READY = 6,
    REGION_TERMINAL = 7,
    EMPTY_REGION_TERMINAL = 8,
    OVERLAY_EXIT = 9,
    PAD_FILL_DONE = 10,
};

constexpr uint16_t kMoeEventRoleENTRY_GATE = 0;
constexpr uint16_t kMoeEventRoleROUTE_FILL_DONE = 1;
constexpr uint16_t kMoeEventRoleDISPATCH_READY = 2;
constexpr uint16_t kMoeEventRoleWEIGHT_READY = 3;
constexpr uint16_t kMoeEventRoleEXPERT_RESULT = 4;
constexpr uint16_t kMoeEventRoleCOMBINE_READY = 5;
constexpr uint16_t kMoeEventRoleTOKEN_OUTPUT_READY = 6;
constexpr uint16_t kMoeEventRoleREGION_TERMINAL = 7;
constexpr uint16_t kMoeEventRoleEMPTY_REGION_TERMINAL = 8;
constexpr uint16_t kMoeEventRoleOVERLAY_EXIT = 9;
constexpr uint16_t kMoeEventRolePAD_FILL_DONE = 10;

constexpr bool validMoeEventRole(uint16_t value)
{
    switch (value) {
    case 0: return true;
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

enum class MoeCommandRole : uint16_t
{
    ROUTE_FILL = 0,
    STREAMED_WEIGHT_LOAD = 1,
    DISPATCH_SENDER = 2,
    DISPATCH_RECEIVER = 3,
    PAD_FILL = 4,
    EXPERT_COMPUTE = 5,
    COMBINE_SENDER = 6,
    COMBINE_RECEIVER = 7,
    DROPPED_TOKEN_FILL = 8,
    COPY_THROUGH = 9,
    LOCAL_REDUCE = 10,
    REGION_TERMINAL = 11,
    EMPTY_REGION_TERMINAL = 12,
    GROUP_EXIT = 13,
};

constexpr uint16_t kMoeCommandRoleROUTE_FILL = 0;
constexpr uint16_t kMoeCommandRoleSTREAMED_WEIGHT_LOAD = 1;
constexpr uint16_t kMoeCommandRoleDISPATCH_SENDER = 2;
constexpr uint16_t kMoeCommandRoleDISPATCH_RECEIVER = 3;
constexpr uint16_t kMoeCommandRolePAD_FILL = 4;
constexpr uint16_t kMoeCommandRoleEXPERT_COMPUTE = 5;
constexpr uint16_t kMoeCommandRoleCOMBINE_SENDER = 6;
constexpr uint16_t kMoeCommandRoleCOMBINE_RECEIVER = 7;
constexpr uint16_t kMoeCommandRoleDROPPED_TOKEN_FILL = 8;
constexpr uint16_t kMoeCommandRoleCOPY_THROUGH = 9;
constexpr uint16_t kMoeCommandRoleLOCAL_REDUCE = 10;
constexpr uint16_t kMoeCommandRoleREGION_TERMINAL = 11;
constexpr uint16_t kMoeCommandRoleEMPTY_REGION_TERMINAL = 12;
constexpr uint16_t kMoeCommandRoleGROUP_EXIT = 13;

constexpr bool validMoeCommandRole(uint16_t value)
{
    switch (value) {
    case 0: return true;
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
    default: return false;
    }
}

enum class RouteDisposition : uint16_t
{
    ACCEPT = 0,
    DROP = 1,
};

constexpr uint16_t kRouteDispositionACCEPT = 0;
constexpr uint16_t kRouteDispositionDROP = 1;

constexpr bool validRouteDisposition(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class MoeFillMode : uint16_t
{
    FUNCTIONAL_BYTES = 0,
    DIGEST_ONLY = 1,
    VALIDITY_ONLY = 2,
};

constexpr uint16_t kMoeFillModeFUNCTIONAL_BYTES = 0;
constexpr uint16_t kMoeFillModeDIGEST_ONLY = 1;
constexpr uint16_t kMoeFillModeVALIDITY_ONLY = 2;

constexpr bool validMoeFillMode(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class MoeOverflowPolicy : uint16_t
{
    DROP = 0,
    PAD_TO_CAPACITY = 1,
    FAIL = 2,
};

constexpr uint16_t kMoeOverflowPolicyDROP = 0;
constexpr uint16_t kMoeOverflowPolicyPAD_TO_CAPACITY = 1;
constexpr uint16_t kMoeOverflowPolicyFAIL = 2;

constexpr bool validMoeOverflowPolicy(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class MoeTransportMode : uint16_t
{
    VARIABLE_ALL_TO_ALL_V = 0,
};

constexpr uint16_t kMoeTransportModeVARIABLE_ALL_TO_ALL_V = 0;

constexpr bool validMoeTransportMode(uint16_t value)
{
    switch (value) {
    case 0: return true;
    default: return false;
    }
}

enum class MoeCombineKind : uint16_t
{
    NONE = 0,
    LOCAL_REDUCE = 1,
};

constexpr uint16_t kMoeCombineKindNONE = 0;
constexpr uint16_t kMoeCombineKindLOCAL_REDUCE = 1;

constexpr bool validMoeCombineKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class AgentRequestFlags : uint16_t
{
    HAS_KV = 1,
};

constexpr uint16_t kAgentRequestFlagsHAS_KV = 1;

constexpr bool validAgentRequestFlags(uint16_t value)
{
    switch (value) {
    case 1: return true;
    default: return false;
    }
}

enum class PathKind : uint16_t
{
    INITIAL_PREFILL = 0,
    KV_REUSE = 1,
    REPREFILL = 2,
};

constexpr uint16_t kPathKindINITIAL_PREFILL = 0;
constexpr uint16_t kPathKindKV_REUSE = 1;
constexpr uint16_t kPathKindREPREFILL = 2;

constexpr bool validPathKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    case 2: return true;
    default: return false;
    }
}

enum class Phase : uint16_t
{
    PREFILL = 1,
    DECODE = 2,
    PUBLISH = 3,
};

constexpr uint16_t kPhasePREFILL = 1;
constexpr uint16_t kPhaseDECODE = 2;
constexpr uint16_t kPhasePUBLISH = 3;

constexpr bool validPhase(uint16_t value)
{
    switch (value) {
    case 1: return true;
    case 2: return true;
    case 3: return true;
    default: return false;
    }
}

enum class DmaFillKind : uint16_t
{
    CONSTANT_PATTERN = 0,
    AGENT_OUTPUT_SURROGATE = 1,
};

constexpr uint16_t kDmaFillKindCONSTANT_PATTERN = 0;
constexpr uint16_t kDmaFillKindAGENT_OUTPUT_SURROGATE = 1;

constexpr bool validDmaFillKind(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class AgentAllocationRole : uint16_t
{
    NONE = 0,
    PUBLISH_SURROGATE_SOURCE = 1,
};

constexpr uint16_t kAgentAllocationRoleNONE = 0;
constexpr uint16_t kAgentAllocationRolePUBLISH_SURROGATE_SOURCE = 1;

constexpr bool validAgentAllocationRole(uint16_t value)
{
    switch (value) {
    case 0: return true;
    case 1: return true;
    default: return false;
    }
}

enum class AgentDigestSource : uint16_t
{
    REQUEST_SEMANTIC_OUTPUT_DIGEST = 0,
};

constexpr uint16_t kAgentDigestSourceREQUEST_SEMANTIC_OUTPUT_DIGEST = 0;

constexpr bool validAgentDigestSource(uint16_t value)
{
    switch (value) {
    case 0: return true;
    default: return false;
    }
}

#endif

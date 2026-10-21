#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_SECTIONS_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_TRANSPORT_SECTIONS_HH

enum class SectionType : uint16_t
{
    STRINGS = 1,
    ENTRYPOINTS = 2,
    PROFILES = 3,
    TENSORS = 4,
    SHARDS = 5,
    ALLOCATIONS = 6,
    STREAMS = 7,
    COMMANDS = 8,
    COMMAND_WAITS = 9,
    COMMAND_OPERANDS = 10,
    EVENTS = 11,
    DMA_DESCRIPTORS = 12,
    OP_ATTRS = 13,
    RELOCATIONS = 14,
    EXPECTED_TRAFFIC = 15,
    PROGRAM_METADATA = 16,
    SEMANTIC_STRINGS = 17,
    SEMANTIC_U64_VALUES = 18,
    SEMANTIC_I64_VALUES = 19,
    SEMANTIC_REFERENCES = 20,
    SEMANTIC_BYTES = 21,
    SEMANTIC_INTEGER_VALUES = 22,
    SOURCE_MAP = 101,
    PROFILE_HINTS = 102,
    CONTENT_DIGESTS = 103,
    SEMANTIC_EXECUTION_WORK_PHASE = 256,
    SEMANTIC_WORK_ESTIMATE = 257,
    SEMANTIC_ADD = 258,
    SEMANTIC_CEIL_DIV_BY_CONST = 259,
    SEMANTIC_CONST = 260,
    SEMANTIC_FLOOR_DIV_BY_CONST = 261,
    SEMANTIC_MUL_BY_CONST = 262,
    SEMANTIC_SYMBOL = 263,
    SEMANTIC_ELEMENTWISE_ATTRS = 264,
    SEMANTIC_EMBEDDING_ATTRS = 265,
    SEMANTIC_MATMUL_ATTRS = 266,
    SEMANTIC_MOVEMENT_ATTRS = 267,
    SEMANTIC_NORM_ATTRS = 268,
    SEMANTIC_REDUCE_ATTRS = 269,
    SEMANTIC_SOFTMAX_ATTRS = 270,
    SEMANTIC_VIEW_ATTRS = 271,
    SEMANTIC_ALLOC_ATTRS = 272,
    SEMANTIC_BARRIER_ATTRS = 273,
    SEMANTIC_BLOCKED_MNK_LAYOUT = 274,
    SEMANTIC_BUFFER_OBJECT = 275,
    SEMANTIC_BUFFER_VIEW = 276,
    SEMANTIC_COLLECTIVE_ATTRS = 277,
    SEMANTIC_CONTROL_TOKEN = 278,
    SEMANTIC_DMA_ATTRS = 279,
    SEMANTIC_ELEMENT_REGION = 280,
    SEMANTIC_GEMM_KERNEL_ATTRS = 281,
    SEMANTIC_KERNEL_COMPUTATION = 282,
    SEMANTIC_KERNEL_COST = 283,
    SEMANTIC_KERNEL_OP = 284,
    SEMANTIC_KERNEL_TENSOR = 285,
    SEMANTIC_KERNEL_TILE = 286,
    SEMANTIC_LOCAL_COPY_ATTRS = 287,
    SEMANTIC_LOCAL_REDUCE_ATTRS = 288,
    SEMANTIC_MATRIX_EPILOGUE_KERNEL_ATTRS = 289,
    SEMANTIC_MOVEMENT_KERNEL_ATTRS = 290,
    SEMANTIC_NORM_KERNEL_ATTRS = 291,
    SEMANTIC_OPERAND_ACCESS = 292,
    SEMANTIC_PARTIAL_SUM_DEFINITION = 293,
    SEMANTIC_PLACEMENT = 294,
    SEMANTIC_RECV_WAIT_ATTRS = 295,
    SEMANTIC_REDUCTION_KERNEL_ATTRS = 296,
    SEMANTIC_SOFTMAX_KERNEL_ATTRS = 297,
    SEMANTIC_STATE_TRANSITION = 298,
    SEMANTIC_TENSOR_SHARD = 299,
    SEMANTIC_TENSOR_STATE = 300,
    SEMANTIC_VECTOR_KERNEL_ATTRS = 301,
    SEMANTIC_VIEW_DECLARATION_ATTRS = 302,
    SEMANTIC_AUTHORED_PROGRAM_ORIGIN = 303,
    SEMANTIC_AUTHORED_VARIANT_LINEAGE = 304,
    SEMANTIC_AXI_FENCE_ATTRS = 305,
    SEMANTIC_BARRIER_ARRIVAL = 306,
    SEMANTIC_BARRIER_EXECUTION = 307,
    SEMANTIC_BARRIER_GROUP = 308,
    SEMANTIC_COMMAND_SEMANTICS = 309,
    SEMANTIC_COMPILED_PROGRAM_ORIGIN = 310,
    SEMANTIC_COMPILED_VARIANT_LINEAGE = 311,
    SEMANTIC_COMPUTE_EXECUTION = 312,
    SEMANTIC_CONTROL_COMMAND_SOURCE = 313,
    SEMANTIC_CONTROL_EXECUTION = 314,
    SEMANTIC_DESCRIPTOR_ENDPOINT_USE = 315,
    SEMANTIC_DESCRIPTOR_GROUP = 316,
    SEMANTIC_DESCRIPTOR_SOURCE = 317,
    SEMANTIC_DMA_EXECUTION = 318,
    SEMANTIC_EVENT_SIGNAL_ATTRS = 319,
    SEMANTIC_EVENT_WAIT_ATTRS = 320,
    SEMANTIC_EXTERNAL_SLOT_BACKING = 321,
    SEMANTIC_HALT_ATTRS = 322,
    SEMANTIC_ID_SPAN = 323,
    SEMANTIC_KERNEL_COMMAND_SOURCE = 324,
    SEMANTIC_KERNEL_TOKEN_SOURCE = 325,
    SEMANTIC_LIFECYCLE_SOURCE = 326,
    SEMANTIC_LOCAL_ALLOCATION_BACKING = 327,
    SEMANTIC_OBJECT_BACKING = 328,
    SEMANTIC_OBJECT_SOURCE = 329,
    SEMANTIC_PROGRAM_SEMANTICS = 330,
    SEMANTIC_PROGRAM_VARIANT = 331,
    SEMANTIC_READ_ACCESS_USE = 332,
    SEMANTIC_RECV_WAIT_EXECUTION = 333,
    SEMANTIC_REPEAT_COMMAND_ATTRS = 334,
    SEMANTIC_REQUEST_BEGIN_ATTRS = 335,
    SEMANTIC_REQUEST_END_ATTRS = 336,
    SEMANTIC_RESIDENT_VIEW = 337,
    SEMANTIC_SCHEDULED_DEPENDENCY = 338,
    SEMANTIC_SCHEDULED_STREAM = 339,
    SEMANTIC_STATE_SOURCE = 340,
    SEMANTIC_STREAM_ORDER_SOURCE = 341,
    SEMANTIC_VARIANT_MEMBERSHIP = 342,
    SEMANTIC_WRITE_ACCESS_USE = 343,
    SEMANTIC_BINDING = 344,
    SEMANTIC_BINDING_SLOT = 345,
    SEMANTIC_CHANNEL_TRAFFIC = 346,
    SEMANTIC_DESCRIPTOR_IDENTITY = 347,
    SEMANTIC_DESCRIPTOR_TRAFFIC = 348,
    SEMANTIC_TRAFFIC_AGGREGATE = 349,
    SEMANTIC_TRAFFIC_AGGREGATE_KEY = 350,
    SEMANTIC_TRAFFIC_CHANNEL_TOTAL = 351,
    SEMANTIC_TRAFFIC_REPORT = 352,
    SEMANTIC_COMPOSITION_SOURCE = 353,
    SEMANTIC_COMPOSED_PROGRAM_ORIGIN = 354,
    SEMANTIC_EXTERNAL_STORAGE_ADAPTATION = 355,
    SEMANTIC_COMPOSED_VARIANT_LINEAGE = 356,
    PROFILE_STREAM_RANGES = 16394,
    MOE_LAYER_SPECS = 16384,
    MOE_EXPERT_SPECS = 16385,
    MOE_DYNAMIC_REGIONS = 16386,
    MOE_KERNEL_SPECS = 16387,
    AGENT_REQUEST_PROFILES = 16388,
    AGENT_INSTANCE_PROFILES = 16389,
    AGENT_SOURCE_CORE_MAP = 16390,
    AGENT_INSTANCE_MEMBER_BINDINGS = 16391,
    AGENT_REQUEST_BINDING_REQUIREMENTS = 16392,
    AGENT_PUBLISH_SURROGATE_BINDINGS = 16393,
    AGENT_INSTANCE_TENSOR_BINDINGS = 16395,
};

constexpr uint16_t kSectionTypeSTRINGS = 1;
constexpr uint16_t kSectionTypeENTRYPOINTS = 2;
constexpr uint16_t kSectionTypePROFILES = 3;
constexpr uint16_t kSectionTypeTENSORS = 4;
constexpr uint16_t kSectionTypeSHARDS = 5;
constexpr uint16_t kSectionTypeALLOCATIONS = 6;
constexpr uint16_t kSectionTypeSTREAMS = 7;
constexpr uint16_t kSectionTypeCOMMANDS = 8;
constexpr uint16_t kSectionTypeCOMMAND_WAITS = 9;
constexpr uint16_t kSectionTypeCOMMAND_OPERANDS = 10;
constexpr uint16_t kSectionTypeEVENTS = 11;
constexpr uint16_t kSectionTypeDMA_DESCRIPTORS = 12;
constexpr uint16_t kSectionTypeOP_ATTRS = 13;
constexpr uint16_t kSectionTypeRELOCATIONS = 14;
constexpr uint16_t kSectionTypeEXPECTED_TRAFFIC = 15;
constexpr uint16_t kSectionTypePROGRAM_METADATA = 16;
constexpr uint16_t kSectionTypeSEMANTIC_STRINGS = 17;
constexpr uint16_t kSectionTypeSEMANTIC_U64_VALUES = 18;
constexpr uint16_t kSectionTypeSEMANTIC_I64_VALUES = 19;
constexpr uint16_t kSectionTypeSEMANTIC_REFERENCES = 20;
constexpr uint16_t kSectionTypeSEMANTIC_BYTES = 21;
constexpr uint16_t kSectionTypeSEMANTIC_INTEGER_VALUES = 22;
constexpr uint16_t kSectionTypeSOURCE_MAP = 101;
constexpr uint16_t kSectionTypePROFILE_HINTS = 102;
constexpr uint16_t kSectionTypeCONTENT_DIGESTS = 103;
constexpr uint16_t kSectionTypeSEMANTIC_EXECUTION_WORK_PHASE = 256;
constexpr uint16_t kSectionTypeSEMANTIC_WORK_ESTIMATE = 257;
constexpr uint16_t kSectionTypeSEMANTIC_ADD = 258;
constexpr uint16_t kSectionTypeSEMANTIC_CEIL_DIV_BY_CONST = 259;
constexpr uint16_t kSectionTypeSEMANTIC_CONST = 260;
constexpr uint16_t kSectionTypeSEMANTIC_FLOOR_DIV_BY_CONST = 261;
constexpr uint16_t kSectionTypeSEMANTIC_MUL_BY_CONST = 262;
constexpr uint16_t kSectionTypeSEMANTIC_SYMBOL = 263;
constexpr uint16_t kSectionTypeSEMANTIC_ELEMENTWISE_ATTRS = 264;
constexpr uint16_t kSectionTypeSEMANTIC_EMBEDDING_ATTRS = 265;
constexpr uint16_t kSectionTypeSEMANTIC_MATMUL_ATTRS = 266;
constexpr uint16_t kSectionTypeSEMANTIC_MOVEMENT_ATTRS = 267;
constexpr uint16_t kSectionTypeSEMANTIC_NORM_ATTRS = 268;
constexpr uint16_t kSectionTypeSEMANTIC_REDUCE_ATTRS = 269;
constexpr uint16_t kSectionTypeSEMANTIC_SOFTMAX_ATTRS = 270;
constexpr uint16_t kSectionTypeSEMANTIC_VIEW_ATTRS = 271;
constexpr uint16_t kSectionTypeSEMANTIC_ALLOC_ATTRS = 272;
constexpr uint16_t kSectionTypeSEMANTIC_BARRIER_ATTRS = 273;
constexpr uint16_t kSectionTypeSEMANTIC_BLOCKED_MNK_LAYOUT = 274;
constexpr uint16_t kSectionTypeSEMANTIC_BUFFER_OBJECT = 275;
constexpr uint16_t kSectionTypeSEMANTIC_BUFFER_VIEW = 276;
constexpr uint16_t kSectionTypeSEMANTIC_COLLECTIVE_ATTRS = 277;
constexpr uint16_t kSectionTypeSEMANTIC_CONTROL_TOKEN = 278;
constexpr uint16_t kSectionTypeSEMANTIC_DMA_ATTRS = 279;
constexpr uint16_t kSectionTypeSEMANTIC_ELEMENT_REGION = 280;
constexpr uint16_t kSectionTypeSEMANTIC_GEMM_KERNEL_ATTRS = 281;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_COMPUTATION = 282;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_COST = 283;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_OP = 284;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_TENSOR = 285;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_TILE = 286;
constexpr uint16_t kSectionTypeSEMANTIC_LOCAL_COPY_ATTRS = 287;
constexpr uint16_t kSectionTypeSEMANTIC_LOCAL_REDUCE_ATTRS = 288;
constexpr uint16_t kSectionTypeSEMANTIC_MATRIX_EPILOGUE_KERNEL_ATTRS = 289;
constexpr uint16_t kSectionTypeSEMANTIC_MOVEMENT_KERNEL_ATTRS = 290;
constexpr uint16_t kSectionTypeSEMANTIC_NORM_KERNEL_ATTRS = 291;
constexpr uint16_t kSectionTypeSEMANTIC_OPERAND_ACCESS = 292;
constexpr uint16_t kSectionTypeSEMANTIC_PARTIAL_SUM_DEFINITION = 293;
constexpr uint16_t kSectionTypeSEMANTIC_PLACEMENT = 294;
constexpr uint16_t kSectionTypeSEMANTIC_RECV_WAIT_ATTRS = 295;
constexpr uint16_t kSectionTypeSEMANTIC_REDUCTION_KERNEL_ATTRS = 296;
constexpr uint16_t kSectionTypeSEMANTIC_SOFTMAX_KERNEL_ATTRS = 297;
constexpr uint16_t kSectionTypeSEMANTIC_STATE_TRANSITION = 298;
constexpr uint16_t kSectionTypeSEMANTIC_TENSOR_SHARD = 299;
constexpr uint16_t kSectionTypeSEMANTIC_TENSOR_STATE = 300;
constexpr uint16_t kSectionTypeSEMANTIC_VECTOR_KERNEL_ATTRS = 301;
constexpr uint16_t kSectionTypeSEMANTIC_VIEW_DECLARATION_ATTRS = 302;
constexpr uint16_t kSectionTypeSEMANTIC_AUTHORED_PROGRAM_ORIGIN = 303;
constexpr uint16_t kSectionTypeSEMANTIC_AUTHORED_VARIANT_LINEAGE = 304;
constexpr uint16_t kSectionTypeSEMANTIC_AXI_FENCE_ATTRS = 305;
constexpr uint16_t kSectionTypeSEMANTIC_BARRIER_ARRIVAL = 306;
constexpr uint16_t kSectionTypeSEMANTIC_BARRIER_EXECUTION = 307;
constexpr uint16_t kSectionTypeSEMANTIC_BARRIER_GROUP = 308;
constexpr uint16_t kSectionTypeSEMANTIC_COMMAND_SEMANTICS = 309;
constexpr uint16_t kSectionTypeSEMANTIC_COMPILED_PROGRAM_ORIGIN = 310;
constexpr uint16_t kSectionTypeSEMANTIC_COMPILED_VARIANT_LINEAGE = 311;
constexpr uint16_t kSectionTypeSEMANTIC_COMPUTE_EXECUTION = 312;
constexpr uint16_t kSectionTypeSEMANTIC_CONTROL_COMMAND_SOURCE = 313;
constexpr uint16_t kSectionTypeSEMANTIC_CONTROL_EXECUTION = 314;
constexpr uint16_t kSectionTypeSEMANTIC_DESCRIPTOR_ENDPOINT_USE = 315;
constexpr uint16_t kSectionTypeSEMANTIC_DESCRIPTOR_GROUP = 316;
constexpr uint16_t kSectionTypeSEMANTIC_DESCRIPTOR_SOURCE = 317;
constexpr uint16_t kSectionTypeSEMANTIC_DMA_EXECUTION = 318;
constexpr uint16_t kSectionTypeSEMANTIC_EVENT_SIGNAL_ATTRS = 319;
constexpr uint16_t kSectionTypeSEMANTIC_EVENT_WAIT_ATTRS = 320;
constexpr uint16_t kSectionTypeSEMANTIC_EXTERNAL_SLOT_BACKING = 321;
constexpr uint16_t kSectionTypeSEMANTIC_HALT_ATTRS = 322;
constexpr uint16_t kSectionTypeSEMANTIC_ID_SPAN = 323;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_COMMAND_SOURCE = 324;
constexpr uint16_t kSectionTypeSEMANTIC_KERNEL_TOKEN_SOURCE = 325;
constexpr uint16_t kSectionTypeSEMANTIC_LIFECYCLE_SOURCE = 326;
constexpr uint16_t kSectionTypeSEMANTIC_LOCAL_ALLOCATION_BACKING = 327;
constexpr uint16_t kSectionTypeSEMANTIC_OBJECT_BACKING = 328;
constexpr uint16_t kSectionTypeSEMANTIC_OBJECT_SOURCE = 329;
constexpr uint16_t kSectionTypeSEMANTIC_PROGRAM_SEMANTICS = 330;
constexpr uint16_t kSectionTypeSEMANTIC_PROGRAM_VARIANT = 331;
constexpr uint16_t kSectionTypeSEMANTIC_READ_ACCESS_USE = 332;
constexpr uint16_t kSectionTypeSEMANTIC_RECV_WAIT_EXECUTION = 333;
constexpr uint16_t kSectionTypeSEMANTIC_REPEAT_COMMAND_ATTRS = 334;
constexpr uint16_t kSectionTypeSEMANTIC_REQUEST_BEGIN_ATTRS = 335;
constexpr uint16_t kSectionTypeSEMANTIC_REQUEST_END_ATTRS = 336;
constexpr uint16_t kSectionTypeSEMANTIC_RESIDENT_VIEW = 337;
constexpr uint16_t kSectionTypeSEMANTIC_SCHEDULED_DEPENDENCY = 338;
constexpr uint16_t kSectionTypeSEMANTIC_SCHEDULED_STREAM = 339;
constexpr uint16_t kSectionTypeSEMANTIC_STATE_SOURCE = 340;
constexpr uint16_t kSectionTypeSEMANTIC_STREAM_ORDER_SOURCE = 341;
constexpr uint16_t kSectionTypeSEMANTIC_VARIANT_MEMBERSHIP = 342;
constexpr uint16_t kSectionTypeSEMANTIC_WRITE_ACCESS_USE = 343;
constexpr uint16_t kSectionTypeSEMANTIC_BINDING = 344;
constexpr uint16_t kSectionTypeSEMANTIC_BINDING_SLOT = 345;
constexpr uint16_t kSectionTypeSEMANTIC_CHANNEL_TRAFFIC = 346;
constexpr uint16_t kSectionTypeSEMANTIC_DESCRIPTOR_IDENTITY = 347;
constexpr uint16_t kSectionTypeSEMANTIC_DESCRIPTOR_TRAFFIC = 348;
constexpr uint16_t kSectionTypeSEMANTIC_TRAFFIC_AGGREGATE = 349;
constexpr uint16_t kSectionTypeSEMANTIC_TRAFFIC_AGGREGATE_KEY = 350;
constexpr uint16_t kSectionTypeSEMANTIC_TRAFFIC_CHANNEL_TOTAL = 351;
constexpr uint16_t kSectionTypeSEMANTIC_TRAFFIC_REPORT = 352;
constexpr uint16_t kSectionTypeSEMANTIC_COMPOSITION_SOURCE = 353;
constexpr uint16_t kSectionTypeSEMANTIC_COMPOSED_PROGRAM_ORIGIN = 354;
constexpr uint16_t kSectionTypeSEMANTIC_EXTERNAL_STORAGE_ADAPTATION = 355;
constexpr uint16_t kSectionTypeSEMANTIC_COMPOSED_VARIANT_LINEAGE = 356;
constexpr uint16_t kSectionTypePROFILE_STREAM_RANGES = 16394;
constexpr uint16_t kSectionTypeMOE_LAYER_SPECS = 16384;
constexpr uint16_t kSectionTypeMOE_EXPERT_SPECS = 16385;
constexpr uint16_t kSectionTypeMOE_DYNAMIC_REGIONS = 16386;
constexpr uint16_t kSectionTypeMOE_KERNEL_SPECS = 16387;
constexpr uint16_t kSectionTypeAGENT_REQUEST_PROFILES = 16388;
constexpr uint16_t kSectionTypeAGENT_INSTANCE_PROFILES = 16389;
constexpr uint16_t kSectionTypeAGENT_SOURCE_CORE_MAP = 16390;
constexpr uint16_t kSectionTypeAGENT_INSTANCE_MEMBER_BINDINGS = 16391;
constexpr uint16_t kSectionTypeAGENT_REQUEST_BINDING_REQUIREMENTS = 16392;
constexpr uint16_t kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS = 16393;
constexpr uint16_t kSectionTypeAGENT_INSTANCE_TENSOR_BINDINGS = 16395;

constexpr bool validSectionType(uint16_t value)
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
    case 101: return true;
    case 102: return true;
    case 103: return true;
    case 256: return true;
    case 257: return true;
    case 258: return true;
    case 259: return true;
    case 260: return true;
    case 261: return true;
    case 262: return true;
    case 263: return true;
    case 264: return true;
    case 265: return true;
    case 266: return true;
    case 267: return true;
    case 268: return true;
    case 269: return true;
    case 270: return true;
    case 271: return true;
    case 272: return true;
    case 273: return true;
    case 274: return true;
    case 275: return true;
    case 276: return true;
    case 277: return true;
    case 278: return true;
    case 279: return true;
    case 280: return true;
    case 281: return true;
    case 282: return true;
    case 283: return true;
    case 284: return true;
    case 285: return true;
    case 286: return true;
    case 287: return true;
    case 288: return true;
    case 289: return true;
    case 290: return true;
    case 291: return true;
    case 292: return true;
    case 293: return true;
    case 294: return true;
    case 295: return true;
    case 296: return true;
    case 297: return true;
    case 298: return true;
    case 299: return true;
    case 300: return true;
    case 301: return true;
    case 302: return true;
    case 303: return true;
    case 304: return true;
    case 305: return true;
    case 306: return true;
    case 307: return true;
    case 308: return true;
    case 309: return true;
    case 310: return true;
    case 311: return true;
    case 312: return true;
    case 313: return true;
    case 314: return true;
    case 315: return true;
    case 316: return true;
    case 317: return true;
    case 318: return true;
    case 319: return true;
    case 320: return true;
    case 321: return true;
    case 322: return true;
    case 323: return true;
    case 324: return true;
    case 325: return true;
    case 326: return true;
    case 327: return true;
    case 328: return true;
    case 329: return true;
    case 330: return true;
    case 331: return true;
    case 332: return true;
    case 333: return true;
    case 334: return true;
    case 335: return true;
    case 336: return true;
    case 337: return true;
    case 338: return true;
    case 339: return true;
    case 340: return true;
    case 341: return true;
    case 342: return true;
    case 343: return true;
    case 344: return true;
    case 345: return true;
    case 346: return true;
    case 347: return true;
    case 348: return true;
    case 349: return true;
    case 350: return true;
    case 351: return true;
    case 352: return true;
    case 353: return true;
    case 354: return true;
    case 355: return true;
    case 356: return true;
    case 16394: return true;
    case 16384: return true;
    case 16385: return true;
    case 16386: return true;
    case 16387: return true;
    case 16388: return true;
    case 16389: return true;
    case 16390: return true;
    case 16391: return true;
    case 16392: return true;
    case 16393: return true;
    case 16395: return true;
    default: return false;
    }
}

#endif

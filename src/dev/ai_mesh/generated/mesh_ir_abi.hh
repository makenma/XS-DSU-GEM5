#ifndef GEM5_DEV_AI_MESH_GENERATED_MESH_IR_ABI_HH
#define GEM5_DEV_AI_MESH_GENERATED_MESH_IR_ABI_HH

#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>
#include <variant>
#include <vector>
#include "dev/ai_mesh/generated/mesh_diagnostics.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_abi
{

constexpr char kSchemaSha256[] = "87abcecd302eeea330666c2bf11c4e50e1bf52c201a979a78cc87f486f5d544f";
constexpr uint16_t kAbiMajor = 1;
constexpr uint16_t kAbiMinor = 3;
constexpr uint16_t kMinReaderMinor = 3;
constexpr uint64_t kJsonSafeIntegerMax = 9007199254740991ull;
enum class RequiredFeature : uint64_t
{
    SCHEDULED_SEMANTICS = 1,
};
constexpr uint64_t kRequiredFeatures = 1;
constexpr uint16_t kRequiredFeatureScheduledSemanticsMinReaderMinor = 3;
constexpr uint64_t kMagic = 0x010000004248534dull;
constexpr uint32_t kHeaderBytes = 128;
constexpr uint32_t kSectionDirBytes = 40;
constexpr uint32_t kHeaderMagicOffset = 0;
constexpr uint32_t kHeaderMagicBytes = 8;
constexpr uint32_t kHeaderAbiMajorOffset = 8;
constexpr uint32_t kHeaderAbiMajorBytes = 2;
constexpr uint32_t kHeaderAbiMinorOffset = 10;
constexpr uint32_t kHeaderAbiMinorBytes = 2;
constexpr uint32_t kHeaderHeaderBytesOffset = 12;
constexpr uint32_t kHeaderHeaderBytesBytes = 4;
constexpr uint32_t kHeaderFileBytesOffset = 16;
constexpr uint32_t kHeaderFileBytesBytes = 8;
constexpr uint32_t kHeaderSectionDirOffsetOffset = 24;
constexpr uint32_t kHeaderSectionDirOffsetBytes = 8;
constexpr uint32_t kHeaderSectionCountOffset = 32;
constexpr uint32_t kHeaderSectionCountBytes = 4;
constexpr uint32_t kHeaderFlagsOffset = 36;
constexpr uint32_t kHeaderFlagsBytes = 4;
constexpr uint32_t kHeaderArchDigestOffset = 40;
constexpr uint32_t kHeaderArchDigestBytes = 32;
constexpr uint32_t kHeaderPayloadSha256Offset = 72;
constexpr uint32_t kHeaderPayloadSha256Bytes = 32;
constexpr uint32_t kHeaderRequiredFeaturesOffset = 104;
constexpr uint32_t kHeaderRequiredFeaturesBytes = 8;
constexpr uint32_t kHeaderReservedOffset = 112;
constexpr uint32_t kHeaderReservedBytes = 16;
constexpr uint32_t kSectionDirSectionTypeOffset = 0;
constexpr uint32_t kSectionDirSectionTypeBytes = 2;
constexpr uint32_t kSectionDirFlagsOffset = 2;
constexpr uint32_t kSectionDirFlagsBytes = 2;
constexpr uint32_t kSectionDirRecordBytesOffset = 4;
constexpr uint32_t kSectionDirRecordBytesBytes = 4;
constexpr uint32_t kSectionDirOffsetOffset = 8;
constexpr uint32_t kSectionDirOffsetBytes = 8;
constexpr uint32_t kSectionDirSizeOffset = 16;
constexpr uint32_t kSectionDirSizeBytes = 8;
constexpr uint32_t kSectionDirCountOffset = 24;
constexpr uint32_t kSectionDirCountBytes = 8;
constexpr uint32_t kSectionDirCrc32Offset = 32;
constexpr uint32_t kSectionDirCrc32Bytes = 4;
constexpr uint32_t kSectionDirReservedOffset = 36;
constexpr uint32_t kSectionDirReservedBytes = 4;
constexpr uint32_t kStringsBlobHeaderBytes = 4;
constexpr uint32_t kStringsBlobCountOffset = 0;
constexpr uint32_t kStringsBlobCountBytes = 4;
constexpr uint32_t kStringsDirectoryRecordBytes = 8;
constexpr uint32_t kStringsDirectoryOffsetOffset = 0;
constexpr uint32_t kStringsDirectoryOffsetBytes = 4;
constexpr uint32_t kStringsDirectoryLengthOffset = 4;
constexpr uint32_t kStringsDirectoryLengthBytes = 4;
constexpr uint32_t kSemanticStringsBlobHeaderBytes = 4;
constexpr uint32_t kSemanticStringsBlobCountOffset = 0;
constexpr uint32_t kSemanticStringsBlobCountBytes = 4;
constexpr uint32_t kSemanticStringsDirectoryRecordBytes = 8;
constexpr uint32_t kSemanticStringsDirectoryOffsetOffset = 0;
constexpr uint32_t kSemanticStringsDirectoryOffsetBytes = 4;
constexpr uint32_t kSemanticStringsDirectoryLengthOffset = 4;
constexpr uint32_t kSemanticStringsDirectoryLengthBytes = 4;
struct RequiredSectionDescriptor { uint16_t section_type; uint32_t record_bytes; };
inline constexpr std::array<RequiredSectionDescriptor, 119> kRequiredSections = {{
    {1, 0},
    {2, 24},
    {3, 80},
    {4, 136},
    {5, 240},
    {6, 32},
    {7, 16},
    {8, 40},
    {9, 4},
    {10, 16},
    {11, 20},
    {12, 120},
    {13, 32},
    {14, 32},
    {15, 72},
    {16, 48},
    {17, 0},
    {18, 8},
    {19, 8},
    {20, 8},
    {21, 1},
    {22, 16},
    {256, 16},
    {257, 40},
    {258, 24},
    {259, 24},
    {260, 16},
    {261, 24},
    {262, 24},
    {263, 48},
    {264, 48},
    {265, 48},
    {266, 72},
    {267, 24},
    {268, 40},
    {269, 48},
    {270, 32},
    {271, 64},
    {272, 16},
    {273, 16},
    {274, 40},
    {275, 88},
    {276, 96},
    {277, 56},
    {278, 24},
    {279, 64},
    {280, 32},
    {281, 56},
    {282, 48},
    {283, 56},
    {284, 96},
    {285, 128},
    {286, 104},
    {287, 8},
    {288, 40},
    {289, 48},
    {290, 48},
    {291, 48},
    {292, 40},
    {293, 48},
    {294, 24},
    {295, 40},
    {296, 48},
    {297, 40},
    {298, 48},
    {299, 80},
    {300, 48},
    {301, 48},
    {302, 16},
    {303, 32},
    {304, 16},
    {305, 16},
    {306, 40},
    {307, 16},
    {308, 40},
    {309, 32},
    {310, 16},
    {311, 24},
    {312, 16},
    {313, 16},
    {314, 8},
    {315, 40},
    {316, 48},
    {317, 16},
    {318, 16},
    {319, 16},
    {320, 16},
    {321, 16},
    {322, 8},
    {323, 24},
    {324, 16},
    {325, 16},
    {326, 16},
    {327, 16},
    {328, 24},
    {329, 16},
    {330, 200},
    {331, 56},
    {332, 32},
    {333, 16},
    {334, 32},
    {335, 8},
    {336, 8},
    {337, 24},
    {338, 48},
    {339, 56},
    {340, 16},
    {341, 16},
    {342, 200},
    {343, 32},
    {344, 64},
    {345, 80},
    {346, 120},
    {347, 80},
    {348, 208},
    {349, 104},
    {350, 72},
    {351, 56},
    {352, 40},
}};
inline constexpr std::array<uint16_t, 119> kRequiredSectionTypes = {{
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    256,
    257,
    258,
    259,
    260,
    261,
    262,
    263,
    264,
    265,
    266,
    267,
    268,
    269,
    270,
    271,
    272,
    273,
    274,
    275,
    276,
    277,
    278,
    279,
    280,
    281,
    282,
    283,
    284,
    285,
    286,
    287,
    288,
    289,
    290,
    291,
    292,
    293,
    294,
    295,
    296,
    297,
    298,
    299,
    300,
    301,
    302,
    303,
    304,
    305,
    306,
    307,
    308,
    309,
    310,
    311,
    312,
    313,
    314,
    315,
    316,
    317,
    318,
    319,
    320,
    321,
    322,
    323,
    324,
    325,
    326,
    327,
    328,
    329,
    330,
    331,
    332,
    333,
    334,
    335,
    336,
    337,
    338,
    339,
    340,
    341,
    342,
    343,
    344,
    345,
    346,
    347,
    348,
    349,
    350,
    351,
    352,
}};

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
    default: return false;
    }
}

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
};

constexpr uint16_t kDmaKindLOAD = 1;
constexpr uint16_t kDmaKindSTORE = 2;
constexpr uint16_t kDmaKindP2P_PUSH = 3;
constexpr uint16_t kDmaKindPREFETCH = 4;
constexpr uint16_t kDmaKindLOCAL_FILL = 5;

constexpr bool validDmaKind(uint16_t value)
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

constexpr uint16_t opcodeEngine(uint16_t opcode)
{
    switch (opcode) {
    case static_cast<uint16_t>(Opcode::REQUEST_BEGIN): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::REQUEST_END): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::HALT): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::EVENT_WAIT): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::EVENT_SIGNAL): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::BARRIER): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::AXI_FENCE): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::RECV_WAIT): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::REPEAT): return static_cast<uint16_t>(Engine::CONTROL);
    case static_cast<uint16_t>(Opcode::DMA_LOAD): return static_cast<uint16_t>(Engine::DMA_READ);
    case static_cast<uint16_t>(Opcode::DMA_PREFETCH): return static_cast<uint16_t>(Engine::DMA_READ);
    case static_cast<uint16_t>(Opcode::DMA_STORE): return static_cast<uint16_t>(Engine::DMA_WRITE);
    case static_cast<uint16_t>(Opcode::DMA_P2P_PUSH): return static_cast<uint16_t>(Engine::DMA_WRITE);
    case static_cast<uint16_t>(Opcode::DMA_FILL): return static_cast<uint16_t>(Engine::DMA_WRITE);
    case static_cast<uint16_t>(Opcode::GEMM): return static_cast<uint16_t>(Engine::TENSOR);
    case static_cast<uint16_t>(Opcode::BMM): return static_cast<uint16_t>(Engine::TENSOR);
    case static_cast<uint16_t>(Opcode::ELEMENTWISE): return static_cast<uint16_t>(Engine::VECTOR);
    case static_cast<uint16_t>(Opcode::SOFTMAX): return static_cast<uint16_t>(Engine::VECTOR);
    case static_cast<uint16_t>(Opcode::NORM): return static_cast<uint16_t>(Engine::VECTOR);
    case static_cast<uint16_t>(Opcode::LOCAL_REDUCE): return static_cast<uint16_t>(Engine::REDUCE);
    default: return 0;
    }
}

constexpr uint32_t kEntrypointsBytes = 24;
constexpr uint32_t kEntrypointsEntrypointIdOffset = 0;
constexpr uint32_t kEntrypointsNameSidOffset = 4;
constexpr uint32_t kEntrypointsProfileBeginOffset = 8;
constexpr uint32_t kEntrypointsProfileCountOffset = 12;
constexpr uint32_t kEntrypointsLifecycleCoreIdOffset = 14;
constexpr uint32_t kEntrypointsLifecycleStreamIdOffset = 16;
constexpr uint32_t kEntrypointsFlagsOffset = 18;
constexpr uint32_t kEntrypointsReservedOffset = 20;

constexpr uint32_t kProfilesBytes = 80;
constexpr uint32_t kProfilesProfileIdOffset = 0;
constexpr uint32_t kProfilesEntrypointIdOffset = 4;
constexpr uint32_t kProfilesNameSidOffset = 8;
constexpr uint32_t kProfilesRankOffset = 12;
constexpr uint32_t kProfilesReservedOffset = 14;
constexpr uint32_t kProfilesDimsOffset = 16;

constexpr uint32_t kTensorsBytes = 136;
constexpr uint32_t kTensorsTensorIdOffset = 0;
constexpr uint32_t kTensorsNameSidOffset = 4;
constexpr uint32_t kTensorsRoleOffset = 8;
constexpr uint32_t kTensorsDtypeOffset = 10;
constexpr uint32_t kTensorsStorageClassOffset = 12;
constexpr uint32_t kTensorsAccessOffset = 14;
constexpr uint32_t kTensorsRankOffset = 16;
constexpr uint32_t kTensorsLayoutOffset = 18;
constexpr uint32_t kTensorsLayoutAttrOffset = 20;
constexpr uint32_t kTensorsPlacementIdOffset = 24;
constexpr uint32_t kTensorsShardingIdOffset = 28;
constexpr uint32_t kTensorsFlagsOffset = 32;
constexpr uint32_t kTensorsReservedOffset = 36;
constexpr uint32_t kTensorsDimsOffset = 40;
constexpr uint32_t kTensorsContentSha256Offset = 104;

constexpr uint32_t kShardsBytes = 240;
constexpr uint32_t kShardsShardIdOffset = 0;
constexpr uint32_t kShardsTensorIdOffset = 4;
constexpr uint32_t kShardsShardingIdOffset = 8;
constexpr uint32_t kShardsOwnerCoreOffset = 12;
constexpr uint32_t kShardsRankOffset = 14;
constexpr uint32_t kShardsReservedOffset = 16;
constexpr uint32_t kShardsFlagsOffset = 18;
constexpr uint32_t kShardsReserved0Offset = 20;
constexpr uint32_t kShardsGlobalOriginOffset = 24;
constexpr uint32_t kShardsLocalShapeOffset = 88;
constexpr uint32_t kShardsValidShapeOffset = 152;
constexpr uint32_t kShardsAllocationIdOffset = 216;
constexpr uint32_t kShardsReserved2Offset = 220;
constexpr uint32_t kShardsAllocationOffsetOffset = 224;
constexpr uint32_t kShardsSpanBytesOffset = 232;

constexpr uint32_t kAllocationsBytes = 32;
constexpr uint32_t kAllocationsAllocationIdOffset = 0;
constexpr uint32_t kAllocationsOwnerCoreOffset = 4;
constexpr uint32_t kAllocationsMemorySpaceOffset = 6;
constexpr uint32_t kAllocationsOffsetBytesOffset = 8;
constexpr uint32_t kAllocationsSizeBytesOffset = 16;
constexpr uint32_t kAllocationsAlignmentBytesOffset = 24;
constexpr uint32_t kAllocationsFlagsOffset = 28;

constexpr uint32_t kStreamsBytes = 16;
constexpr uint32_t kStreamsCoreIdOffset = 0;
constexpr uint32_t kStreamsStreamIdOffset = 2;
constexpr uint32_t kStreamsCommandBeginOffset = 4;
constexpr uint32_t kStreamsCommandCountOffset = 8;
constexpr uint32_t kStreamsFlagsOffset = 12;
constexpr uint32_t kStreamsReservedOffset = 14;

constexpr uint32_t kCommandsBytes = 40;
constexpr uint32_t kCommandsCommandIdOffset = 0;
constexpr uint32_t kCommandsSourceOpIdOffset = 4;
constexpr uint32_t kCommandsCoreIdOffset = 8;
constexpr uint32_t kCommandsStreamIdOffset = 10;
constexpr uint32_t kCommandsEngineOffset = 12;
constexpr uint32_t kCommandsOpcodeOffset = 14;
constexpr uint32_t kCommandsWaitBeginOffset = 16;
constexpr uint32_t kCommandsWaitCountOffset = 20;
constexpr uint32_t kCommandsOperandCountOffset = 22;
constexpr uint32_t kCommandsOperandBeginOffset = 24;
constexpr uint32_t kCommandsSignalEventOffset = 28;
constexpr uint32_t kCommandsAttrIndexOffset = 32;
constexpr uint32_t kCommandsDebugLocIdOffset = 36;

constexpr uint32_t kCommandWaitsBytes = 4;
constexpr uint32_t kCommandWaitsEventIdOffset = 0;

constexpr uint32_t kCommandOperandsBytes = 16;
constexpr uint32_t kCommandOperandsTensorIdOffset = 0;
constexpr uint32_t kCommandOperandsShardIdOffset = 4;
constexpr uint32_t kCommandOperandsAllocationIdOffset = 8;
constexpr uint32_t kCommandOperandsAccessOffset = 12;
constexpr uint32_t kCommandOperandsReservedOffset = 14;

constexpr uint32_t kEventsBytes = 20;
constexpr uint32_t kEventsEventIdOffset = 0;
constexpr uint32_t kEventsKindOffset = 4;
constexpr uint32_t kEventsReservedOffset = 6;
constexpr uint32_t kEventsProducerCommandIdOffset = 8;
constexpr uint32_t kEventsExpectedArrivalsOffset = 12;
constexpr uint32_t kEventsReserved2Offset = 16;

constexpr uint32_t kDmaEndpointBytes = 24;
constexpr uint32_t kDmaEndpointMemorySpaceOffset = 0;
constexpr uint32_t kDmaEndpointRegionIdOffset = 2;
constexpr uint32_t kDmaEndpointOwnerCoreOffset = 4;
constexpr uint32_t kDmaEndpointTensorIdOffset = 6;
constexpr uint32_t kDmaEndpointShardIdOffset = 10;
constexpr uint32_t kDmaEndpointReservedOffset = 14;
constexpr uint32_t kDmaEndpointOffsetBytesOffset = 16;

constexpr uint32_t kDmaDescriptorsBytes = 120;
constexpr uint32_t kDmaDescriptorsDescriptorIdOffset = 0;
constexpr uint32_t kDmaDescriptorsCommandIdOffset = 4;
constexpr uint32_t kDmaDescriptorsTransferIdOffset = 8;
constexpr uint32_t kDmaDescriptorsOwnerCoreOffset = 12;
constexpr uint32_t kDmaDescriptorsKindOffset = 14;
constexpr uint32_t kDmaDescriptorsSrcOffset = 16;
constexpr uint32_t kDmaDescriptorsDstOffset = 40;
constexpr uint32_t kDmaDescriptorsRowsOffset = 64;
constexpr uint32_t kDmaDescriptorsRowBytesOffset = 68;
constexpr uint32_t kDmaDescriptorsSrcStrideBytesOffset = 76;
constexpr uint32_t kDmaDescriptorsDstStrideBytesOffset = 84;
constexpr uint32_t kDmaDescriptorsUsefulBytesOffset = 92;
constexpr uint32_t kDmaDescriptorsPhysicalStorageBytesOffset = 100;
constexpr uint32_t kDmaDescriptorsAxiIdOffset = 108;
constexpr uint32_t kDmaDescriptorsQosOffset = 110;
constexpr uint32_t kDmaDescriptorsReservedOffset = 111;
constexpr uint32_t kDmaDescriptorsMaxBurstBeatsOffset = 112;
constexpr uint32_t kDmaDescriptorsReserved2Offset = 114;
constexpr uint32_t kDmaDescriptorsCompletionEventOffset = 116;

constexpr uint32_t kOpAttrsBytes = 32;
constexpr uint32_t kOpAttrsKindOffset = 0;
constexpr uint32_t kOpAttrsReservedOffset = 2;
constexpr uint32_t kOpAttrsPayloadOffset = 4;

constexpr uint32_t kRelocationsBytes = 32;
constexpr uint32_t kRelocationsRelocationIdOffset = 0;
constexpr uint32_t kRelocationsSymbolSidOffset = 4;
constexpr uint32_t kRelocationsKindOffset = 8;
constexpr uint32_t kRelocationsRegionIdOffset = 10;
constexpr uint32_t kRelocationsTensorIdOffset = 12;
constexpr uint32_t kRelocationsReservedOffset = 16;
constexpr uint32_t kRelocationsOffsetBytesOffset = 20;
constexpr uint32_t kRelocationsReserved2Offset = 28;

constexpr uint32_t kExpectedTrafficBytes = 72;
constexpr uint32_t kExpectedTrafficEntrypointIdOffset = 0;
constexpr uint32_t kExpectedTrafficProfileIdOffset = 4;
constexpr uint32_t kExpectedTrafficCommandIdOffset = 8;
constexpr uint32_t kExpectedTrafficDescriptorIdOffset = 12;
constexpr uint32_t kExpectedTrafficKindOffset = 16;
constexpr uint32_t kExpectedTrafficReservedOffset = 18;
constexpr uint32_t kExpectedTrafficUsefulBytesOffset = 20;
constexpr uint32_t kExpectedTrafficPhysicalBeatBytesOffset = 28;
constexpr uint32_t kExpectedTrafficSegmentsOffset = 36;
constexpr uint32_t kExpectedTrafficBurstsOffset = 40;
constexpr uint32_t kExpectedTrafficArCountOffset = 44;
constexpr uint32_t kExpectedTrafficRBeatsOffset = 48;
constexpr uint32_t kExpectedTrafficAwCountOffset = 52;
constexpr uint32_t kExpectedTrafficWBeatsOffset = 56;
constexpr uint32_t kExpectedTrafficBCountOffset = 60;
constexpr uint32_t kExpectedTrafficMinFlitsOffset = 64;
constexpr uint32_t kExpectedTrafficReserved2Offset = 68;

constexpr uint32_t kSourceMapBytes = 16;
constexpr uint32_t kSourceMapLocIdOffset = 0;
constexpr uint32_t kSourceMapFileSidOffset = 4;
constexpr uint32_t kSourceMapLineOffset = 8;
constexpr uint32_t kSourceMapColumnOffset = 12;

constexpr uint32_t kContentDigestsBytes = 40;
constexpr uint32_t kContentDigestsObjectKindOffset = 0;
constexpr uint32_t kContentDigestsReservedOffset = 2;
constexpr uint32_t kContentDigestsObjectIdOffset = 4;
constexpr uint32_t kContentDigestsDigestOffset = 8;

constexpr uint32_t kProgramMetadataBytes = 48;
constexpr uint32_t kProgramMetadataMinReaderMinorOffset = 0;
constexpr uint32_t kProgramMetadataFlagsOffset = 2;
constexpr uint32_t kProgramMetadataReservedOffset = 4;
constexpr uint32_t kProgramMetadataSemanticSha256Offset = 16;

constexpr uint32_t kSemanticU64ValuesBytes = 8;
constexpr uint32_t kSemanticU64ValuesValueOffset = 0;

constexpr uint32_t kSemanticI64ValuesBytes = 8;
constexpr uint32_t kSemanticI64ValuesValueOffset = 0;

constexpr uint32_t kSemanticReferencesBytes = 8;

constexpr uint32_t kSemanticBytesBytes = 1;
constexpr uint32_t kSemanticBytesValueOffset = 0;

constexpr uint32_t kSemanticIntegerValuesBytes = 16;
constexpr uint32_t kSemanticIntegerValuesKindOffset = 0;
constexpr uint32_t kSemanticIntegerValuesReservedOffset = 2;
constexpr uint32_t kSemanticIntegerValuesPayloadOffset = 8;

constexpr uint32_t kProfileHintsBytes = 16;
constexpr uint32_t kProfileHintsEntrypointIdOffset = 0;
constexpr uint32_t kProfileHintsProfileIdOffset = 4;
constexpr uint32_t kProfileHintsNameSidOffset = 8;
constexpr uint32_t kProfileHintsValueSidOffset = 12;

constexpr uint32_t kRepeatV1Bytes = 16;
constexpr uint32_t kRepeatV1SubrangeBeginStreamOrdinalOffset = 0;
constexpr uint32_t kRepeatV1SubrangeCommandCountOffset = 4;
constexpr uint32_t kRepeatV1RepeatCountOffset = 8;
constexpr uint32_t kRepeatV1FlagsOffset = 12;

constexpr uint32_t kGemmV1Bytes = 28;
constexpr uint32_t kGemmV1BatchOffset = 0;
constexpr uint32_t kGemmV1MOffset = 4;
constexpr uint32_t kGemmV1NOffset = 8;
constexpr uint32_t kGemmV1KOffset = 12;
constexpr uint32_t kGemmV1ATransposeOffset = 16;
constexpr uint32_t kGemmV1BTransposeOffset = 17;
constexpr uint32_t kGemmV1DtypeOffset = 18;
constexpr uint32_t kGemmV1AccumDtypeOffset = 20;
constexpr uint32_t kGemmV1EpilogueOffset = 22;
constexpr uint32_t kGemmV1EfficiencyQ16Offset = 24;

constexpr uint32_t kBmmV1Bytes = 28;
constexpr uint32_t kBmmV1BatchOffset = 0;
constexpr uint32_t kBmmV1MOffset = 4;
constexpr uint32_t kBmmV1NOffset = 8;
constexpr uint32_t kBmmV1KOffset = 12;
constexpr uint32_t kBmmV1ATransposeOffset = 16;
constexpr uint32_t kBmmV1BTransposeOffset = 17;
constexpr uint32_t kBmmV1DtypeOffset = 18;
constexpr uint32_t kBmmV1AccumDtypeOffset = 20;
constexpr uint32_t kBmmV1EpilogueOffset = 22;
constexpr uint32_t kBmmV1EfficiencyQ16Offset = 24;

constexpr uint32_t kElementwiseV1Bytes = 16;
constexpr uint32_t kElementwiseV1ElementCountOffset = 0;
constexpr uint32_t kElementwiseV1DtypeOffset = 8;
constexpr uint32_t kElementwiseV1OpOffset = 10;
constexpr uint32_t kElementwiseV1OpsPerElementOffset = 12;
constexpr uint32_t kElementwiseV1ReservedOffset = 14;

constexpr uint32_t kReduceV1Bytes = 16;
constexpr uint32_t kReduceV1ElementCountOffset = 0;
constexpr uint32_t kReduceV1DtypeOffset = 8;
constexpr uint32_t kReduceV1AccumDtypeOffset = 10;
constexpr uint32_t kReduceV1OpOffset = 12;
constexpr uint32_t kReduceV1FanInOffset = 14;

constexpr uint32_t kSoftmaxV1Bytes = 16;
constexpr uint32_t kSoftmaxV1AxisSizeOffset = 0;
constexpr uint32_t kSoftmaxV1DtypeOffset = 8;
constexpr uint32_t kSoftmaxV1AlgorithmOffset = 10;
constexpr uint32_t kSoftmaxV1ReservedOffset = 12;

constexpr uint32_t kNormV1Bytes = 16;
constexpr uint32_t kNormV1ElementCountOffset = 0;
constexpr uint32_t kNormV1DtypeOffset = 8;
constexpr uint32_t kNormV1AlgorithmOffset = 10;
constexpr uint32_t kNormV1ReservedOffset = 12;

constexpr uint32_t kFillV1Bytes = 16;
constexpr uint32_t kFillV1PatternOffset = 0;
constexpr uint32_t kFillV1ReservedOffset = 8;

constexpr uint32_t kBlockedMnkLayoutV1Bytes = 16;
constexpr uint32_t kBlockedMnkLayoutV1BlockMOffset = 0;
constexpr uint32_t kBlockedMnkLayoutV1BlockNOffset = 4;
constexpr uint32_t kBlockedMnkLayoutV1BlockKOffset = 8;
constexpr uint32_t kBlockedMnkLayoutV1MinorToMajorOffset = 12;
constexpr uint32_t kBlockedMnkLayoutV1ReservedOffset = 14;

constexpr uint32_t kRecvWaitV1Bytes = 16;
constexpr uint32_t kRecvWaitV1TransferIdOffset = 0;
constexpr uint32_t kRecvWaitV1Reserved0Offset = 4;
constexpr uint32_t kRecvWaitV1Reserved1Offset = 8;
constexpr uint32_t kRecvWaitV1Reserved2Offset = 12;

constexpr uint32_t kFenceV1Bytes = 16;
constexpr uint32_t kFenceV1FenceScopeOffset = 0;
constexpr uint32_t kFenceV1ReservedOffset = 2;
constexpr uint32_t kFenceV1Reserved0Offset = 4;
constexpr uint32_t kFenceV1Reserved1Offset = 8;
constexpr uint32_t kFenceV1Reserved2Offset = 12;

constexpr uint64_t kStreamFlagsAllowedBits = 0x3ull;
constexpr uint64_t kTensorFlagsAllowedBits = 0x1ull;

struct AbiError
{
    const char *code = "";
    const char *message = "";
};

inline uint8_t rdU8(const uint8_t *p) { return p[0]; }
inline uint16_t rdU16(const uint8_t *p)
{
    return static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8);
}
inline uint32_t rdU32(const uint8_t *p)
{
    return static_cast<uint32_t>(rdU16(p)) | (static_cast<uint32_t>(rdU16(p + 2)) << 16);
}
inline uint64_t rdU64(const uint8_t *p)
{
    return static_cast<uint64_t>(rdU32(p)) | (static_cast<uint64_t>(rdU32(p + 4)) << 32);
}
inline void wrU8(uint8_t *p, uint8_t value) { p[0] = value; }
inline void wrU16(uint8_t *p, uint16_t value)
{
    p[0] = static_cast<uint8_t>(value);
    p[1] = static_cast<uint8_t>(value >> 8);
}
inline void wrU32(uint8_t *p, uint32_t value)
{
    wrU16(p, static_cast<uint16_t>(value));
    wrU16(p + 2, static_cast<uint16_t>(value >> 16));
}
inline void wrU64(uint8_t *p, uint64_t value)
{
    wrU32(p, static_cast<uint32_t>(value));
    wrU32(p + 4, static_cast<uint32_t>(value >> 32));
}

inline bool flagsWithin(uint64_t allowed, uint64_t value)
{
    return (value & ~allowed) == 0;
}

struct Entrypoint
{
    uint32_t entrypoint_id = 0;
    uint32_t name_sid = 0;
    uint32_t profile_begin = 0;
    uint16_t profile_count = 0;
    uint16_t lifecycle_core_id = 0;
    uint16_t lifecycle_stream_id = 0;
    uint16_t flags = 0;
    uint32_t reserved = 0;
};

struct Profile
{
    uint32_t profile_id = 0;
    uint32_t entrypoint_id = 0;
    uint32_t name_sid = 0;
    uint16_t rank = 0;
    uint16_t reserved = 0;
    std::array<uint64_t, 8> dims = {};
};

struct Tensor
{
    uint32_t tensor_id = 0;
    uint32_t name_sid = 0;
    uint16_t role = 0;
    uint16_t dtype = 0;
    uint16_t storage_class = 0;
    uint16_t access = 0;
    uint16_t rank = 0;
    uint16_t layout = 0;
    uint32_t layout_attr = 0;
    uint32_t placement_id = 0;
    uint32_t sharding_id = 0;
    uint32_t flags = 0;
    uint32_t reserved = 0;
    std::array<uint64_t, 8> dims = {};
    std::array<uint8_t, 32> content_sha256 = {};
};

struct Shard
{
    uint32_t shard_id = 0;
    uint32_t tensor_id = 0;
    uint32_t sharding_id = 0;
    uint16_t owner_core = 0;
    uint16_t rank = 0;
    uint16_t reserved = 0;
    uint16_t flags = 0;
    uint32_t reserved0 = 0;
    std::array<uint64_t, 8> global_origin = {};
    std::array<uint64_t, 8> local_shape = {};
    std::array<uint64_t, 8> valid_shape = {};
    uint32_t allocation_id = 0;
    uint32_t reserved2 = 0;
    uint64_t allocation_offset = 0;
    uint64_t span_bytes = 0;
};

struct Allocation
{
    uint32_t allocation_id = 0;
    uint16_t owner_core = 0;
    uint16_t memory_space = 0;
    uint64_t offset_bytes = 0;
    uint64_t size_bytes = 0;
    uint32_t alignment_bytes = 0;
    uint32_t flags = 0;
};

struct Stream
{
    uint16_t core_id = 0;
    uint16_t stream_id = 0;
    uint32_t command_begin = 0;
    uint32_t command_count = 0;
    uint16_t flags = 0;
    uint16_t reserved = 0;
};

struct Command
{
    uint32_t command_id = 0;
    uint32_t source_op_id = 0;
    uint16_t core_id = 0;
    uint16_t stream_id = 0;
    uint16_t engine = 0;
    uint16_t opcode = 0;
    uint32_t wait_begin = 0;
    uint16_t wait_count = 0;
    uint16_t operand_count = 0;
    uint32_t operand_begin = 0;
    uint32_t signal_event = 0;
    uint32_t attr_index = 0;
    uint32_t debug_loc_id = 0;
};

struct CommandWait
{
    uint32_t event_id = 0;
};

struct CommandOperand
{
    uint32_t tensor_id = 0;
    uint32_t shard_id = 0;
    uint32_t allocation_id = 0;
    uint16_t access = 0;
    uint16_t reserved = 0;
};

struct Event
{
    uint32_t event_id = 0;
    uint16_t kind = 0;
    uint16_t reserved = 0;
    uint32_t producer_command_id = 0;
    uint32_t expected_arrivals = 0;
    uint32_t reserved2 = 0;
};

struct DmaEndpoint
{
    uint16_t memory_space = 0;
    uint16_t region_id = 0;
    uint16_t owner_core = 0;
    uint32_t tensor_id = 0;
    uint32_t shard_id = 0;
    uint16_t reserved = 0;
    uint64_t offset_bytes = 0;
};

struct DmaDescriptor
{
    uint32_t descriptor_id = 0;
    uint32_t command_id = 0;
    uint32_t transfer_id = 0;
    uint16_t owner_core = 0;
    uint16_t kind = 0;
    DmaEndpoint src;
    DmaEndpoint dst;
    uint32_t rows = 0;
    uint64_t row_bytes = 0;
    uint64_t src_stride_bytes = 0;
    uint64_t dst_stride_bytes = 0;
    uint64_t useful_bytes = 0;
    uint64_t physical_storage_bytes = 0;
    uint16_t axi_id = 0;
    uint8_t qos = 0;
    uint8_t reserved = 0;
    uint16_t max_burst_beats = 0;
    uint16_t reserved2 = 0;
    uint32_t completion_event = 0;
};

struct OpAttr
{
    uint16_t kind = 0;
    uint16_t reserved = 0;
    std::array<uint8_t, 28> payload = {};
};

struct Relocation
{
    uint32_t relocation_id = 0;
    uint32_t symbol_sid = 0;
    uint16_t kind = 0;
    uint16_t region_id = 0;
    uint32_t tensor_id = 0;
    uint32_t reserved = 0;
    uint64_t offset_bytes = 0;
    uint32_t reserved2 = 0;
};

struct ExpectedTraffic
{
    uint32_t entrypoint_id = 0;
    uint32_t profile_id = 0;
    uint32_t command_id = 0;
    uint32_t descriptor_id = 0;
    uint16_t kind = 0;
    uint16_t reserved = 0;
    uint64_t useful_bytes = 0;
    uint64_t physical_beat_bytes = 0;
    uint32_t segments = 0;
    uint32_t bursts = 0;
    uint32_t ar_count = 0;
    uint32_t r_beats = 0;
    uint32_t aw_count = 0;
    uint32_t w_beats = 0;
    uint32_t b_count = 0;
    uint32_t min_flits = 0;
    uint32_t reserved2 = 0;
};

struct SourceMap
{
    uint32_t loc_id = 0;
    uint32_t file_sid = 0;
    uint32_t line = 0;
    uint32_t column = 0;
};

struct ContentDigest
{
    uint16_t object_kind = 0;
    uint16_t reserved = 0;
    uint32_t object_id = 0;
    std::array<uint8_t, 32> digest = {};
};

struct ProfileHint
{
    uint32_t entrypoint_id = 0;
    uint32_t profile_id = 0;
    uint32_t name_sid = 0;
    uint32_t value_sid = 0;
};

struct RepeatV1
{
    uint32_t subrange_begin_stream_ordinal = 0;
    uint32_t subrange_command_count = 0;
    uint32_t repeat_count = 0;
    uint32_t flags = 0;
};

struct GemmV1
{
    uint32_t batch = 0;
    uint32_t m = 0;
    uint32_t n = 0;
    uint32_t k = 0;
    uint8_t a_transpose = 0;
    uint8_t b_transpose = 0;
    uint16_t dtype = 0;
    uint16_t accum_dtype = 0;
    uint16_t epilogue = 0;
    uint32_t efficiency_q16 = 0;
};

struct BmmV1
{
    uint32_t batch = 0;
    uint32_t m = 0;
    uint32_t n = 0;
    uint32_t k = 0;
    uint8_t a_transpose = 0;
    uint8_t b_transpose = 0;
    uint16_t dtype = 0;
    uint16_t accum_dtype = 0;
    uint16_t epilogue = 0;
    uint32_t efficiency_q16 = 0;
};

struct ElementwiseV1
{
    uint64_t element_count = 0;
    uint16_t dtype = 0;
    uint16_t op = 0;
    uint16_t ops_per_element = 0;
    uint16_t reserved = 0;
};

struct ReduceV1
{
    uint64_t element_count = 0;
    uint16_t dtype = 0;
    uint16_t accum_dtype = 0;
    uint16_t op = 0;
    uint16_t fan_in = 0;
};

struct SoftmaxV1
{
    uint64_t axis_size = 0;
    uint16_t dtype = 0;
    uint16_t algorithm = 0;
    uint32_t reserved = 0;
};

struct NormV1
{
    uint64_t element_count = 0;
    uint16_t dtype = 0;
    uint16_t algorithm = 0;
    uint32_t reserved = 0;
};

struct FillV1
{
    uint64_t pattern = 0;
    uint64_t reserved = 0;
};

struct BlockedMnkLayoutV1
{
    uint32_t block_m = 0;
    uint32_t block_n = 0;
    uint32_t block_k = 0;
    uint16_t minor_to_major = 0;
    uint16_t reserved = 0;
};

struct RecvWaitV1
{
    uint32_t transfer_id = 0;
    uint32_t reserved0 = 0;
    uint32_t reserved1 = 0;
    uint32_t reserved2 = 0;
};

struct FenceV1
{
    uint16_t fence_scope = 0;
    uint16_t reserved = 0;
    uint32_t reserved0 = 0;
    uint32_t reserved1 = 0;
    uint32_t reserved2 = 0;
};

#include "dev/ai_mesh/generated/mesh_ir_transport_codecs.hh"
#include "dev/ai_mesh/generated/mesh_ir_transport_projection.hh"
#include "dev/ai_mesh/generated/mesh_ir_transport_storage.hh"

static_assert(kHeaderBytes == 128, "header size fixed by spec");
static_assert(kSectionDirBytes == 40, "section dir size fixed by spec");
static_assert(kCommandsBytes == 40, "COMMANDS record fixed by spec");

}
}
}

#include "dev/ai_mesh/generated/mesh_ir_semantic_abi.hh"

#endif

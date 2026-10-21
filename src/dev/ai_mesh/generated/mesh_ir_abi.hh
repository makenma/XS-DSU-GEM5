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

constexpr char kSchemaSha256[] = "083669ae266eed39fec35d37a826430ed21aed6ab7659eb3b182e0f252735897";
constexpr uint16_t kAbiMajor = 2;
constexpr uint16_t kAbiMinor = 0;
constexpr uint16_t kMinReaderMinor = 0;
constexpr uint64_t kJsonSafeIntegerMax = 9007199254740991ull;
enum class RequiredFeature : uint64_t
{
    SCHEDULED_SEMANTICS = 1ull,
    DYNAMIC_MOE_V1 = 2ull,
    AGENT_SERVING_V1 = 4ull,
    PROFILE_SCOPED_EXECUTION_V1 = 8ull,
};
constexpr uint64_t kRequiredFeatures = 1ull;
constexpr uint64_t kKnownFeatureMask = 15ull;
constexpr uint64_t kFeatureScheduledSemantics = 1ull;
constexpr uint64_t kFeatureScheduledSemanticsRequires = 0ull;
constexpr uint16_t kFeatureScheduledSemanticsMinWriterMinor = 0;
constexpr uint16_t kRequiredFeatureScheduledSemanticsMinReaderMinor = 0;
constexpr uint64_t kFeatureDynamicMoeV1 = 2ull;
constexpr uint64_t kFeatureDynamicMoeV1Requires = 1ull;
constexpr uint16_t kFeatureDynamicMoeV1MinWriterMinor = 0;
constexpr uint16_t kRequiredFeatureDynamicMoeV1MinReaderMinor = 0;
constexpr uint64_t kFeatureAgentServingV1 = 4ull;
constexpr uint64_t kFeatureAgentServingV1Requires = 9ull;
constexpr uint16_t kFeatureAgentServingV1MinWriterMinor = 0;
constexpr uint16_t kRequiredFeatureAgentServingV1MinReaderMinor = 0;
constexpr uint64_t kFeatureProfileScopedExecutionV1 = 8ull;
constexpr uint64_t kFeatureProfileScopedExecutionV1Requires = 1ull;
constexpr uint16_t kFeatureProfileScopedExecutionV1MinWriterMinor = 0;
constexpr uint16_t kRequiredFeatureProfileScopedExecutionV1MinReaderMinor = 0;
struct FeatureSpec
{
    uint64_t bit;
    uint64_t requires_mask;
    uint16_t min_writer_minor;
    uint16_t min_reader_minor;
};
constexpr FeatureSpec kFeatureSpecs[] = {
    {kFeatureScheduledSemantics, kFeatureScheduledSemanticsRequires, kFeatureScheduledSemanticsMinWriterMinor, kRequiredFeatureScheduledSemanticsMinReaderMinor},
    {kFeatureDynamicMoeV1, kFeatureDynamicMoeV1Requires, kFeatureDynamicMoeV1MinWriterMinor, kRequiredFeatureDynamicMoeV1MinReaderMinor},
    {kFeatureAgentServingV1, kFeatureAgentServingV1Requires, kFeatureAgentServingV1MinWriterMinor, kRequiredFeatureAgentServingV1MinReaderMinor},
    {kFeatureProfileScopedExecutionV1, kFeatureProfileScopedExecutionV1Requires, kFeatureProfileScopedExecutionV1MinWriterMinor, kRequiredFeatureProfileScopedExecutionV1MinReaderMinor},
};
constexpr size_t kFeatureCount = 4;
inline bool featureRequirementsMet(uint64_t features)
{
    if ((features & ~kKnownFeatureMask) || (features & kRequiredFeatures) != kRequiredFeatures)
        return false;
    for (const auto &spec : kFeatureSpecs)
        if ((features & spec.bit) && (features & spec.requires_mask) != spec.requires_mask)
            return false;
    return true;
}
inline uint64_t sectionRequiredFeature(uint16_t type)
{
    switch (type) {
      case 16384: return 2ull;
      case 16385: return 2ull;
      case 16386: return 2ull;
      case 16387: return 2ull;
      case 16388: return 4ull;
      case 16389: return 4ull;
      case 16390: return 4ull;
      case 16391: return 4ull;
      case 16395: return 4ull;
      case 16392: return 4ull;
      case 16393: return 4ull;
      case 16394: return 8ull;
      default: return 0;
    }
}
inline bool featureRequiresSection(uint64_t features, uint16_t type)
{
    switch (type) {
      case 16384: return (features & 2ull) != 0;
      case 16385: return (features & 2ull) != 0;
      case 16386: return (features & 2ull) != 0;
      case 16387: return (features & 2ull) != 0;
      case 103: return (features & 2ull) != 0;
      case 16388: return (features & 4ull) != 0;
      case 16389: return (features & 4ull) != 0;
      case 16390: return (features & 4ull) != 0;
      case 16391: return (features & 4ull) != 0;
      case 16395: return (features & 4ull) != 0;
      case 16392: return (features & 4ull) != 0;
      case 16393: return (features & 4ull) != 0;
      case 16394: return (features & 8ull) != 0;
      default: return false;
    }
}
constexpr uint16_t kConditionalSections[] = {16391, 16389, 16395, 16393, 16392, 16388, 16390, 103, 16386, 16385, 16387, 16384, 16394};
constexpr uint64_t kDmaFillRuntimeBoundSentinel = 5932734208044516657ull;
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
inline constexpr std::array<RequiredSectionDescriptor, 123> kRequiredSections = {{
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
    {348, 216},
    {349, 112},
    {350, 72},
    {351, 56},
    {352, 40},
    {353, 32},
    {354, 16},
    {355, 32},
    {356, 40},
}};
inline constexpr std::array<uint16_t, 123> kRequiredSectionTypes = {{
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
    353,
    354,
    355,
    356,
}};

#include "dev/ai_mesh/generated/mesh_ir_transport_sections.hh"
#include "dev/ai_mesh/generated/mesh_ir_transport_enums.hh"
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
    case static_cast<uint16_t>(Opcode::DMA_GATHER_LOAD): return static_cast<uint16_t>(Engine::DMA_READ);
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
constexpr uint32_t kExpectedTrafficFlitsOffset = 64;
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

constexpr uint32_t kWeightCacheBaseKeysBytes = 12;
constexpr uint32_t kWeightCacheBaseKeysCoreIdOffset = 0;
constexpr uint32_t kWeightCacheBaseKeysCachePartitionIdOffset = 2;
constexpr uint32_t kWeightCacheBaseKeysWeightTagIndexOffset = 4;
constexpr uint32_t kWeightCacheBaseKeysCacheGenerationOffset = 8;

constexpr uint32_t kWeightFillKeysBytes = 16;
constexpr uint32_t kWeightFillKeysCoreIdOffset = 0;
constexpr uint32_t kWeightFillKeysCachePartitionIdOffset = 2;
constexpr uint32_t kWeightFillKeysWeightTagIndexOffset = 4;
constexpr uint32_t kWeightFillKeysCacheGenerationOffset = 8;
constexpr uint32_t kWeightFillKeysFillIncarnationOffset = 12;

constexpr uint32_t kCacheDmaDescriptorKeysBytes = 20;
constexpr uint32_t kCacheDmaDescriptorKeysWeightFillKeyOffset = 0;
constexpr uint32_t kCacheDmaDescriptorKeysSegmentOrdinalOffset = 16;

constexpr uint32_t kErrorSourceKeysBytes = 40;
constexpr uint32_t kErrorSourceKeysErrorClassOffset = 0;
constexpr uint32_t kErrorSourceKeysCoreIdOrFfffOffset = 2;
constexpr uint32_t kErrorSourceKeysDomainOffset = 4;
constexpr uint32_t kErrorSourceKeysObjectKindOffset = 5;
constexpr uint32_t kErrorSourceKeysReservedOffset = 6;
constexpr uint32_t kErrorSourceKeysRegionGroupIdOffset = 8;
constexpr uint32_t kErrorSourceKeysRegionIdOffset = 12;
constexpr uint32_t kErrorSourceKeysOrdinalOffset = 16;
constexpr uint32_t kErrorSourceKeysGenerationOffset = 20;
constexpr uint32_t kErrorSourceKeysAuxKeyOffset = 24;

constexpr uint32_t kProfileStreamRangesBytes = 16;
constexpr uint32_t kProfileStreamRangesProfileIdOffset = 0;
constexpr uint32_t kProfileStreamRangesCoreIdOffset = 4;
constexpr uint32_t kProfileStreamRangesStreamIdOffset = 6;
constexpr uint32_t kProfileStreamRangesCommandBeginOffset = 8;
constexpr uint32_t kProfileStreamRangesCommandCountOffset = 12;

constexpr uint32_t kMoeRuntimeRouteEntryBytes = 64;
constexpr uint32_t kMoeRuntimeRouteEntrySemanticTokenUidOffset = 0;
constexpr uint32_t kMoeRuntimeRouteEntryMemberRequestIdOffset = 32;
constexpr uint32_t kMoeRuntimeRouteEntrySourceRankOffset = 40;
constexpr uint32_t kMoeRuntimeRouteEntryTokenOrdinalOffset = 44;
constexpr uint32_t kMoeRuntimeRouteEntryTopkSlotOffset = 48;
constexpr uint32_t kMoeRuntimeRouteEntrySelectedExpertIdOffset = 50;
constexpr uint32_t kMoeRuntimeRouteEntryAssignedExpertIdOffset = 52;
constexpr uint32_t kMoeRuntimeRouteEntryDestinationCoreOffset = 54;
constexpr uint32_t kMoeRuntimeRouteEntryDispositionOffset = 56;
constexpr uint32_t kMoeRuntimeRouteEntryReservedOffset = 57;

constexpr uint32_t kMoeLayerSpecsBytes = 80;
constexpr uint32_t kMoeLayerSpecsLayerIdOffset = 0;
constexpr uint32_t kMoeLayerSpecsKernelSpecIndexOffset = 4;
constexpr uint32_t kMoeLayerSpecsExpertFirstOffset = 8;
constexpr uint32_t kMoeLayerSpecsExpertCountOffset = 12;
constexpr uint32_t kMoeLayerSpecsTopKOffset = 14;
constexpr uint32_t kMoeLayerSpecsTokenBytesOffset = 16;
constexpr uint32_t kMoeLayerSpecsOutputTokenBytesOffset = 20;
constexpr uint32_t kMoeLayerSpecsCapacityFactorQ16Offset = 24;
constexpr uint32_t kMoeLayerSpecsOverflowPolicyOffset = 28;
constexpr uint32_t kMoeLayerSpecsTransportModeOffset = 30;
constexpr uint32_t kMoeLayerSpecsDynamicRegionFirstOffset = 32;
constexpr uint32_t kMoeLayerSpecsDynamicRegionCountOffset = 36;
constexpr uint32_t kMoeLayerSpecsMaxTokensPerFrozenBatchOffset = 40;
constexpr uint32_t kMoeLayerSpecsMaxRequestsPerBatchOffset = 44;
constexpr uint32_t kMoeLayerSpecsMaxRoutesOffset = 48;
constexpr uint32_t kMoeLayerSpecsMaxMaterializedCommandsOffset = 52;
constexpr uint32_t kMoeLayerSpecsMaxMaterializedDescriptorsOffset = 56;
constexpr uint32_t kMoeLayerSpecsMaxMaterializedTransfersOffset = 60;
constexpr uint32_t kMoeLayerSpecsMaxDynamicAllocationsOffset = 64;
constexpr uint32_t kMoeLayerSpecsFlagsOffset = 68;
constexpr uint32_t kMoeLayerSpecsMaxMaterializedEventsOffset = 72;
constexpr uint32_t kMoeLayerSpecsReserved1Offset = 76;

constexpr uint32_t kMoeExpertSpecsBytes = 40;
constexpr uint32_t kMoeExpertSpecsLayerIdOffset = 0;
constexpr uint32_t kMoeExpertSpecsExpertIdOffset = 4;
constexpr uint32_t kMoeExpertSpecsFlagsOffset = 6;
constexpr uint32_t kMoeExpertSpecsCoreIdOffset = 8;
constexpr uint32_t kMoeExpertSpecsReservedCoreOffset = 10;
constexpr uint32_t kMoeExpertSpecsWeightSymbolIdOffset = 12;
constexpr uint32_t kMoeExpertSpecsWeightRegionOffsetOffset = 16;
constexpr uint32_t kMoeExpertSpecsWeightBytesOffset = 24;
constexpr uint32_t kMoeExpertSpecsWeightDigestIndexOffset = 32;
constexpr uint32_t kMoeExpertSpecsReservedOffset = 36;

constexpr uint32_t kMoeDynamicRegionsBytes = 72;
constexpr uint32_t kMoeDynamicRegionsRegionIdOffset = 0;
constexpr uint32_t kMoeDynamicRegionsLayerIdOffset = 4;
constexpr uint32_t kMoeDynamicRegionsCoreIdOffset = 8;
constexpr uint32_t kMoeDynamicRegionsStreamIdOffset = 10;
constexpr uint32_t kMoeDynamicRegionsInsertAfterCommandIdOffset = 12;
constexpr uint32_t kMoeDynamicRegionsResumeBeforeCommandIdOffset = 16;
constexpr uint32_t kMoeDynamicRegionsEntryEventIdOffset = 20;
constexpr uint32_t kMoeDynamicRegionsScratchOffsetOffset = 24;
constexpr uint32_t kMoeDynamicRegionsScratchBytesOffset = 32;
constexpr uint32_t kMoeDynamicRegionsScratchAlignmentOffset = 40;
constexpr uint32_t kMoeDynamicRegionsMaxOverlayCommandsOffset = 44;
constexpr uint32_t kMoeDynamicRegionsMaxOverlayEventsOffset = 48;
constexpr uint32_t kMoeDynamicRegionsMaxOverlayDescriptorsOffset = 52;
constexpr uint32_t kMoeDynamicRegionsMaxOverlayTransfersOffset = 56;
constexpr uint32_t kMoeDynamicRegionsMaxOverlayAllocationsOffset = 60;
constexpr uint32_t kMoeDynamicRegionsFlagsOffset = 64;
constexpr uint32_t kMoeDynamicRegionsReservedOffset = 68;

constexpr uint32_t kMoeKernelSpecsBytes = 88;
constexpr uint32_t kMoeKernelSpecsLayerIdOffset = 0;
constexpr uint32_t kMoeKernelSpecsExpertOpcodeOffset = 4;
constexpr uint32_t kMoeKernelSpecsInputDtypeOffset = 6;
constexpr uint32_t kMoeKernelSpecsAccumDtypeOffset = 8;
constexpr uint32_t kMoeKernelSpecsOutputDtypeOffset = 10;
constexpr uint32_t kMoeKernelSpecsBatchOffset = 12;
constexpr uint32_t kMoeKernelSpecsNOffset = 16;
constexpr uint32_t kMoeKernelSpecsKOffset = 20;
constexpr uint32_t kMoeKernelSpecsTransposeFlagsOffset = 24;
constexpr uint32_t kMoeKernelSpecsCombineKindOffset = 26;
constexpr uint32_t kMoeKernelSpecsAlgorithmIdOffset = 28;
constexpr uint32_t kMoeKernelSpecsEfficiencyQ16Offset = 32;
constexpr uint32_t kMoeKernelSpecsTensorSetupCyclesOffset = 36;
constexpr uint32_t kMoeKernelSpecsTensorFlushCyclesOffset = 40;
constexpr uint32_t kMoeKernelSpecsInputTokenBytesOffset = 44;
constexpr uint32_t kMoeKernelSpecsOutputTokenBytesOffset = 48;
constexpr uint32_t kMoeKernelSpecsReserved0Offset = 52;
constexpr uint32_t kMoeKernelSpecsWeightOperandBytesOffset = 56;
constexpr uint32_t kMoeKernelSpecsExpertResultAlignmentOffset = 64;
constexpr uint32_t kMoeKernelSpecsMaxMOffset = 68;
constexpr uint32_t kMoeKernelSpecsCombineSetupCyclesOffset = 72;
constexpr uint32_t kMoeKernelSpecsCombineFlushCyclesOffset = 76;
constexpr uint32_t kMoeKernelSpecsFlagsOffset = 80;
constexpr uint32_t kMoeKernelSpecsReserved1Offset = 84;

constexpr uint32_t kAgentRequestProfilesBytes = 96;
constexpr uint32_t kAgentRequestProfilesProgramIdOffset = 0;
constexpr uint32_t kAgentRequestProfilesProfileIdOffset = 2;
constexpr uint32_t kAgentRequestProfilesFlagsOffset = 4;
constexpr uint32_t kAgentRequestProfilesReserved0Offset = 6;
constexpr uint32_t kAgentRequestProfilesRequestedProfileKeyOffset = 8;
constexpr uint32_t kAgentRequestProfilesDeltaInputTokensOffset = 16;
constexpr uint32_t kAgentRequestProfilesFullInputTokensOffset = 20;
constexpr uint32_t kAgentRequestProfilesExpectedCachedTokensOffset = 24;
constexpr uint32_t kAgentRequestProfilesOutputTokensOffset = 28;
constexpr uint32_t kAgentRequestProfilesInputBindingBytesOffset = 32;
constexpr uint32_t kAgentRequestProfilesDeltaInputDmaBytesOffset = 40;
constexpr uint32_t kAgentRequestProfilesFullInputDmaBytesOffset = 48;
constexpr uint32_t kAgentRequestProfilesHostOutputBytesOffset = 56;
constexpr uint32_t kAgentRequestProfilesPrimaryInputSymbolIdOffset = 64;
constexpr uint32_t kAgentRequestProfilesPrimaryOutputSymbolIdOffset = 68;
constexpr uint32_t kAgentRequestProfilesPrimaryKvSymbolIdOffset = 72;
constexpr uint32_t kAgentRequestProfilesSourceRankCountOffset = 76;
constexpr uint32_t kAgentRequestProfilesSourceCoreMapBeginOffset = 80;
constexpr uint32_t kAgentRequestProfilesPathMaskOffset = 84;
constexpr uint32_t kAgentRequestProfilesKvBytesPerTokenOffset = 88;
constexpr uint32_t kAgentRequestProfilesPublishChunkBytesOffset = 92;

constexpr uint32_t kAgentInstanceProfilesBytes = 96;
constexpr uint32_t kAgentInstanceProfilesInstanceProfileIdOffset = 0;
constexpr uint32_t kAgentInstanceProfilesRequestProgramIdOffset = 4;
constexpr uint32_t kAgentInstanceProfilesRequestProfileIdOffset = 6;
constexpr uint32_t kAgentInstanceProfilesPathKindOffset = 8;
constexpr uint32_t kAgentInstanceProfilesPhaseOffset = 10;
constexpr uint32_t kAgentInstanceProfilesMemberCountOffset = 12;
constexpr uint32_t kAgentInstanceProfilesDecodeChunkTokensOffset = 14;
constexpr uint32_t kAgentInstanceProfilesMeshEntrypointIdOffset = 16;
constexpr uint32_t kAgentInstanceProfilesMeshProfileIdOffset = 20;
constexpr uint32_t kAgentInstanceProfilesValidTokensPerMemberOffset = 24;
constexpr uint32_t kAgentInstanceProfilesKvTokensBeforeOffset = 28;
constexpr uint32_t kAgentInstanceProfilesLocalPaddedMembersOffset = 32;
constexpr uint32_t kAgentInstanceProfilesLocalPaddedTokensPerMemberOffset = 36;
constexpr uint32_t kAgentInstanceProfilesPrimaryInputSymbolIdOffset = 40;
constexpr uint32_t kAgentInstanceProfilesPrimaryOutputSymbolIdOffset = 44;
constexpr uint32_t kAgentInstanceProfilesPrimaryKvSymbolIdOffset = 48;
constexpr uint32_t kAgentInstanceProfilesFlagsOffset = 52;
constexpr uint32_t kAgentInstanceProfilesMemberBindingFirstOffset = 56;
constexpr uint32_t kAgentInstanceProfilesMemberBindingCountOffset = 60;
constexpr uint32_t kAgentInstanceProfilesHostInputDmaBytesPerMemberOffset = 64;
constexpr uint32_t kAgentInstanceProfilesHostOutputDmaBytesPerMemberOffset = 72;
constexpr uint32_t kAgentInstanceProfilesKvReadBytesPerMemberOffset = 80;
constexpr uint32_t kAgentInstanceProfilesKvWriteBytesPerMemberOffset = 88;

constexpr uint32_t kAgentSourceCoreMapBytes = 4;
constexpr uint32_t kAgentSourceCoreMapCoreIdOffset = 0;
constexpr uint32_t kAgentSourceCoreMapReservedOffset = 2;

constexpr uint32_t kAgentInstanceMemberBindingsBytes = 24;
constexpr uint32_t kAgentInstanceMemberBindingsInstanceProfileIdOffset = 0;
constexpr uint32_t kAgentInstanceMemberBindingsMemberOrdinalOffset = 4;
constexpr uint32_t kAgentInstanceMemberBindingsReserved0Offset = 6;
constexpr uint32_t kAgentInstanceMemberBindingsStaticInputSymbolIdOffset = 8;
constexpr uint32_t kAgentInstanceMemberBindingsStaticOutputSymbolIdOffset = 12;
constexpr uint32_t kAgentInstanceMemberBindingsStaticKvSymbolIdOffset = 16;
constexpr uint32_t kAgentInstanceMemberBindingsExpectedLogicalSourceRankOffset = 20;

constexpr uint32_t kAgentInstanceTensorBindingsBytes = 32;
constexpr uint32_t kAgentInstanceTensorBindingsInstanceProfileIdOffset = 0;
constexpr uint32_t kAgentInstanceTensorBindingsMemberOrdinalOffset = 4;
constexpr uint32_t kAgentInstanceTensorBindingsBindingKindOffset = 6;
constexpr uint32_t kAgentInstanceTensorBindingsStaticSymbolIdOffset = 8;
constexpr uint32_t kAgentInstanceTensorBindingsRequestSymbolIdOffset = 12;
constexpr uint32_t kAgentInstanceTensorBindingsOffsetBytesOffset = 16;
constexpr uint32_t kAgentInstanceTensorBindingsSpanBytesOffset = 24;

constexpr uint32_t kAgentRequestBindingRequirementsBytes = 16;
constexpr uint32_t kAgentRequestBindingRequirementsRequestProgramIdOffset = 0;
constexpr uint32_t kAgentRequestBindingRequirementsRequestProfileIdOffset = 2;
constexpr uint32_t kAgentRequestBindingRequirementsBindingKindOffset = 4;
constexpr uint32_t kAgentRequestBindingRequirementsBindingFlagsOffset = 6;
constexpr uint32_t kAgentRequestBindingRequirementsSymbolIdOffset = 8;
constexpr uint32_t kAgentRequestBindingRequirementsReservedOffset = 12;

constexpr uint32_t kAgentPublishSurrogateBindingsBytes = 28;
constexpr uint32_t kAgentPublishSurrogateBindingsInstanceProfileIdOffset = 0;
constexpr uint32_t kAgentPublishSurrogateBindingsMemberOrdinalOffset = 4;
constexpr uint32_t kAgentPublishSurrogateBindingsReserved0Offset = 6;
constexpr uint32_t kAgentPublishSurrogateBindingsAllocationIdOffset = 8;
constexpr uint32_t kAgentPublishSurrogateBindingsProducerCommandIdOffset = 12;
constexpr uint32_t kAgentPublishSurrogateBindingsCompletionEventIdOffset = 16;
constexpr uint32_t kAgentPublishSurrogateBindingsFillKindOffset = 20;
constexpr uint32_t kAgentPublishSurrogateBindingsAllocationRoleOffset = 22;
constexpr uint32_t kAgentPublishSurrogateBindingsDigestSourceOffset = 24;
constexpr uint32_t kAgentPublishSurrogateBindingsReserved1Offset = 26;

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

constexpr uint32_t kGatherIndexV1Bytes = 28;
constexpr uint32_t kGatherIndexV1IndexOperandIndexOffset = 0;
constexpr uint32_t kGatherIndexV1IndexOffsetBytesOffset = 4;
constexpr uint32_t kGatherIndexV1IndexStrideBytesOffset = 12;
constexpr uint32_t kGatherIndexV1ReservedOffset = 20;

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
constexpr uint64_t kAgentRequestFlagsAllowedBits = 0x1ull;

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
    uint32_t flits = 0;
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

struct WeightCacheBaseKey
{
    uint16_t core_id = 0;
    uint16_t cache_partition_id = 0;
    uint32_t weight_tag_index = 0;
    uint32_t cache_generation = 0;
};

struct WeightFillKey
{
    uint16_t core_id = 0;
    uint16_t cache_partition_id = 0;
    uint32_t weight_tag_index = 0;
    uint32_t cache_generation = 0;
    uint32_t fill_incarnation = 0;
};

struct CacheDmaDescriptorKey
{
    std::array<uint8_t, 16> weight_fill_key = {};
    uint32_t segment_ordinal = 0;
};

struct ErrorSourceKey
{
    uint16_t error_class = 0;
    uint16_t core_id_or_ffff = 0;
    uint8_t domain = 0;
    uint8_t object_kind = 0;
    uint16_t reserved = 0;
    uint32_t region_group_id = 0;
    uint32_t region_id = 0;
    uint32_t ordinal = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 16> aux_key = {};
};

struct ProfileStreamRange
{
    uint32_t profile_id = 0;
    uint16_t core_id = 0;
    uint16_t stream_id = 0;
    uint32_t command_begin = 0;
    uint32_t command_count = 0;
};

struct MoeRuntimeRouteEntry
{
    std::array<uint8_t, 32> semantic_token_uid = {};
    uint64_t member_request_id = 0;
    uint32_t source_rank = 0;
    uint32_t token_ordinal = 0;
    uint16_t topk_slot = 0;
    uint16_t selected_expert_id = 0;
    uint16_t assigned_expert_id = 0;
    uint16_t destination_core = 0;
    uint8_t disposition = 0;
    std::array<uint8_t, 7> reserved = {};
};

struct MoeLayerSpec
{
    uint32_t layer_id = 0;
    uint32_t kernel_spec_index = 0;
    uint32_t expert_first = 0;
    uint16_t expert_count = 0;
    uint16_t top_k = 0;
    uint32_t token_bytes = 0;
    uint32_t output_token_bytes = 0;
    uint32_t capacity_factor_q16 = 0;
    uint16_t overflow_policy = 0;
    uint16_t transport_mode = 0;
    uint32_t dynamic_region_first = 0;
    uint32_t dynamic_region_count = 0;
    uint32_t max_tokens_per_frozen_batch = 0;
    uint32_t max_requests_per_batch = 0;
    uint32_t max_routes = 0;
    uint32_t max_materialized_commands = 0;
    uint32_t max_materialized_descriptors = 0;
    uint32_t max_materialized_transfers = 0;
    uint32_t max_dynamic_allocations = 0;
    uint32_t flags = 0;
    uint32_t max_materialized_events = 0;
    uint32_t reserved1 = 0;
};

struct MoeExpertSpec
{
    uint32_t layer_id = 0;
    uint16_t expert_id = 0;
    uint16_t flags = 0;
    uint16_t core_id = 0;
    uint16_t reserved_core = 0;
    uint32_t weight_symbol_id = 0;
    uint64_t weight_region_offset = 0;
    uint64_t weight_bytes = 0;
    uint32_t weight_digest_index = 0;
    uint32_t reserved = 0;
};

struct MoeDynamicRegion
{
    uint32_t region_id = 0;
    uint32_t layer_id = 0;
    uint16_t core_id = 0;
    uint16_t stream_id = 0;
    uint32_t insert_after_command_id = 0;
    uint32_t resume_before_command_id = 0;
    uint32_t entry_event_id = 0;
    uint64_t scratch_offset = 0;
    uint64_t scratch_bytes = 0;
    uint32_t scratch_alignment = 0;
    uint32_t max_overlay_commands = 0;
    uint32_t max_overlay_events = 0;
    uint32_t max_overlay_descriptors = 0;
    uint32_t max_overlay_transfers = 0;
    uint32_t max_overlay_allocations = 0;
    uint32_t flags = 0;
    uint32_t reserved = 0;
};

struct MoeKernelSpec
{
    uint32_t layer_id = 0;
    uint16_t expert_opcode = 0;
    uint16_t input_dtype = 0;
    uint16_t accum_dtype = 0;
    uint16_t output_dtype = 0;
    uint32_t batch = 0;
    uint32_t n = 0;
    uint32_t k = 0;
    uint16_t transpose_flags = 0;
    uint16_t combine_kind = 0;
    uint32_t algorithm_id = 0;
    uint32_t efficiency_q16 = 0;
    uint32_t tensor_setup_cycles = 0;
    uint32_t tensor_flush_cycles = 0;
    uint32_t input_token_bytes = 0;
    uint32_t output_token_bytes = 0;
    uint32_t reserved0 = 0;
    uint64_t weight_operand_bytes = 0;
    uint32_t expert_result_alignment = 0;
    uint32_t max_m = 0;
    uint32_t combine_setup_cycles = 0;
    uint32_t combine_flush_cycles = 0;
    uint32_t flags = 0;
    uint32_t reserved1 = 0;
};

struct AgentRequestProfile
{
    uint16_t program_id = 0;
    uint16_t profile_id = 0;
    uint16_t flags = 0;
    uint16_t reserved0 = 0;
    uint64_t requested_profile_key = 0;
    uint32_t delta_input_tokens = 0;
    uint32_t full_input_tokens = 0;
    uint32_t expected_cached_tokens = 0;
    uint32_t output_tokens = 0;
    uint64_t input_binding_bytes = 0;
    uint64_t delta_input_dma_bytes = 0;
    uint64_t full_input_dma_bytes = 0;
    uint64_t host_output_bytes = 0;
    uint32_t primary_input_symbol_id = 0;
    uint32_t primary_output_symbol_id = 0;
    uint32_t primary_kv_symbol_id = 0;
    uint32_t source_rank_count = 0;
    uint32_t source_core_map_begin = 0;
    uint32_t path_mask = 0;
    uint32_t kv_bytes_per_token = 0;
    uint32_t publish_chunk_bytes = 0;
};

struct AgentInstanceProfile
{
    uint32_t instance_profile_id = 0;
    uint16_t request_program_id = 0;
    uint16_t request_profile_id = 0;
    uint16_t path_kind = 0;
    uint16_t phase = 0;
    uint16_t member_count = 0;
    uint16_t decode_chunk_tokens = 0;
    uint32_t mesh_entrypoint_id = 0;
    uint32_t mesh_profile_id = 0;
    uint32_t valid_tokens_per_member = 0;
    uint32_t kv_tokens_before = 0;
    uint32_t local_padded_members = 0;
    uint32_t local_padded_tokens_per_member = 0;
    uint32_t primary_input_symbol_id = 0;
    uint32_t primary_output_symbol_id = 0;
    uint32_t primary_kv_symbol_id = 0;
    uint32_t flags = 0;
    uint32_t member_binding_first = 0;
    uint32_t member_binding_count = 0;
    uint64_t host_input_dma_bytes_per_member = 0;
    uint64_t host_output_dma_bytes_per_member = 0;
    uint64_t kv_read_bytes_per_member = 0;
    uint64_t kv_write_bytes_per_member = 0;
};

struct AgentSourceCoreMap
{
    uint16_t core_id = 0;
    uint16_t reserved = 0;
};

struct AgentInstanceMemberBinding
{
    uint32_t instance_profile_id = 0;
    uint16_t member_ordinal = 0;
    uint16_t reserved0 = 0;
    uint32_t static_input_symbol_id = 0;
    uint32_t static_output_symbol_id = 0;
    uint32_t static_kv_symbol_id = 0;
    uint32_t expected_logical_source_rank = 0;
};

struct AgentInstanceTensorBinding
{
    uint32_t instance_profile_id = 0;
    uint16_t member_ordinal = 0;
    uint16_t binding_kind = 0;
    uint32_t static_symbol_id = 0;
    uint32_t request_symbol_id = 0;
    uint64_t offset_bytes = 0;
    uint64_t span_bytes = 0;
};

struct AgentRequestBindingRequirement
{
    uint16_t request_program_id = 0;
    uint16_t request_profile_id = 0;
    uint16_t binding_kind = 0;
    uint16_t binding_flags = 0;
    uint32_t symbol_id = 0;
    uint32_t reserved = 0;
};

struct AgentPublishSurrogateBinding
{
    uint32_t instance_profile_id = 0;
    uint16_t member_ordinal = 0;
    uint16_t reserved0 = 0;
    uint32_t allocation_id = 0;
    uint32_t producer_command_id = 0;
    uint32_t completion_event_id = 0;
    uint16_t fill_kind = 0;
    uint16_t allocation_role = 0;
    uint16_t digest_source = 0;
    uint16_t reserved1 = 0;
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

struct GatherIndexV1
{
    uint32_t index_operand_index = 0;
    uint64_t index_offset_bytes = 0;
    uint64_t index_stride_bytes = 0;
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

#include <gtest/gtest.h>

#include <algorithm>
#include <tuple>

#include <cstring>
#include <fstream>
#include <vector>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/gate5_overlay_golden.inc"
#include "dev/ai_mesh/generated/gate5_rng_golden.inc"
#include "dev/ai_mesh/generated/gate5_weight_golden.inc"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/mesh_serving_verifier.hh"
#include "dev/ai_mesh/mesh_moe_gate.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/mesh_weight_cache.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
#include "dev/ai_mesh/mesh_moe_rng.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

// The embedded golden images come from the Python encoder CLI and pin the
// cross-language ABI contract: the C++ reader must accept them and reject
// any single-bit corruption.
MeshBytes fromArray(const unsigned char *data, size_t size)
{
    return MeshBytes(data, data + size);
}

RuntimeArch testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.sram_banks = 16;
    arch.sram_alignment = 64;
    arch.axi_data_bytes = 32;
    arch.axi_max_burst_beats = 16;
    RuntimeArch::Region hbm;
    hbm.region_id = 0;
    hbm.base = 0x800000000;
    hbm.bytes = 0x400000000;
    RuntimeArch::Region shared;
    shared.region_id = 1;
    shared.base = 0x100000000;
    shared.bytes = 0x10000000;
    RuntimeArch::Region sram;
    sram.region_id = 2;
    sram.base = 0x400000000;
    sram.bytes = 0x800000;
    sram.tile_stride = 0x400000;
    sram.tile_bytes = 0x200000;
    sram.is_sram_aperture = true;
    arch.regions = {hbm, shared, sram};
    return arch;
}

TEST(MeshBinaryTest, LoadsGoldenSingleProgram)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.abi_major, 1u);
    EXPECT_EQ(program.abi_minor, 2u);
    EXPECT_EQ(program.commands.size(), 7u);
    EXPECT_EQ(program.descriptors.size(), 3u);
    EXPECT_EQ(program.traffic.size(), 3u);
    EXPECT_TRUE(verifyDecodedProgram(program, testArch(), error)) << error.message;
}

TEST(MeshBinaryTest, LoadsGoldenDualProgram)
{
    MeshBytes image = fromArray(kGoldenMshbDual, sizeof(kGoldenMshbDual));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.commands.size(), 14u);
    EXPECT_EQ(program.descriptors.size(), 6u);
    EXPECT_TRUE(verifyDecodedProgram(program, testArch(), error)) << error.message;
}

TEST(MeshBinaryTest, RejectsBadMagic)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image[0] ^= 0xFF;
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_MAGIC");
}

TEST(MeshBinaryTest, RejectsSingleBitPayloadCorruption)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image[200] ^= 0x01;
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
}

TEST(MeshBinaryTest, RejectsTruncation)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image.resize(image.size() - 8);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
}

TEST(MeshBinaryTest, RejectsArchDigestMismatch)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    RuntimeArch wrong = testArch();
    wrong.arch_digest_hex = "deadbeef";
    ASSERT_FALSE(verifyDecodedProgram(program, wrong, error));
    EXPECT_EQ(error.code, "E_ARCH_DIGEST");
}

TEST(MeshBinaryTest, HeaderLayoutMatchesSpec)
{
    using namespace mesh_abi;
    static_assert(kHeaderBytes == 128);
    static_assert(kSectionDirBytes == 40);
    static_assert(kCommandsBytes == 40);
    static_assert(kMoeLayerSpecsBytes == 80);
    static_assert(kMoeExpertSpecsBytes == 40);
    static_assert(kMoeDynamicRegionsBytes == 72);
    static_assert(kMoeKernelSpecsBytes == 88);
    static_assert(kAgentRequestProfilesBytes == 96);
    static_assert(kAgentInstanceProfilesBytes == 96);
    static_assert(kAgentSourceCoreMapBytes == 4);
    static_assert(kAgentInstanceMemberBindingsBytes == 24);
    static_assert(kAgentRequestBindingRequirementsBytes == 16);
    static_assert(kAgentPublishSurrogateBindingsBytes == 28);
    EXPECT_EQ(kMagic, 0x010000004248534Dull);
    EXPECT_EQ(kFeatureDynamicMoeV1, 0x1ull);
    EXPECT_EQ(kFeatureAgentServingV1, 0x2ull);
    EXPECT_EQ(kKnownFeatureMask, 0x7ull);
    EXPECT_EQ(kFeatureDynamicMoeV1MinWriterMinor, 1);
    EXPECT_EQ(kFeatureAgentServingV1MinWriterMinor, 1);
    EXPECT_EQ(kDmaFillRuntimeBoundSentinel, 0x52554e54494d4531ull);
    EXPECT_EQ(kSectionTypeMOE_LAYER_SPECS, 0x4000);
    EXPECT_EQ(kSectionTypeMOE_EXPERT_SPECS, 0x4001);
    EXPECT_EQ(kSectionTypeMOE_DYNAMIC_REGIONS, 0x4002);
    EXPECT_EQ(kSectionTypeMOE_KERNEL_SPECS, 0x4003);
    EXPECT_EQ(kSectionTypeAGENT_REQUEST_PROFILES, 0x4004);
    EXPECT_EQ(kSectionTypeAGENT_INSTANCE_PROFILES, 0x4005);
    EXPECT_EQ(kSectionTypeAGENT_SOURCE_CORE_MAP, 0x4006);
    EXPECT_EQ(kSectionTypeAGENT_INSTANCE_MEMBER_BINDINGS, 0x4007);
    EXPECT_EQ(kSectionTypeAGENT_REQUEST_BINDING_REQUIREMENTS, 0x4008);
    EXPECT_EQ(kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS, 0x4009);
    EXPECT_EQ(kPathKindINITIAL_PREFILL, 0);
    EXPECT_EQ(kPathKindKV_REUSE, 1);
    EXPECT_EQ(kPathKindREPREFILL, 2);
    EXPECT_EQ(kPhasePREFILL, 1);
    EXPECT_EQ(kPhaseDECODE, 2);
    EXPECT_EQ(kPhasePUBLISH, 3);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeMOE_LAYER_SPECS),
              kFeatureDynamicMoeV1);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeCONTENT_DIGESTS),
              kFeatureDynamicMoeV1);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeAGENT_REQUEST_PROFILES),
              kFeatureAgentServingV1);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS),
              kFeatureAgentServingV1);
    EXPECT_EQ(kFeatureProfileScopedExecutionV1, 0x4ull);
    EXPECT_EQ(kFeatureProfileScopedExecutionV1MinWriterMinor, 3);
    EXPECT_EQ(kFeatureAgentServingV1Requires, kFeatureProfileScopedExecutionV1);
    EXPECT_TRUE(featureRequirementsMet(kFeatureAgentServingV1 |
                                       kFeatureProfileScopedExecutionV1));
    EXPECT_FALSE(featureRequirementsMet(kFeatureAgentServingV1));
    EXPECT_FALSE(featureRequirementsMet(kFeatureDynamicMoeV1 |
                                        kFeatureAgentServingV1));
    EXPECT_EQ(kSectionTypePROFILE_STREAM_RANGES, 16);
    EXPECT_EQ(kProfileStreamRangesBytes, 16);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypePROFILE_STREAM_RANGES),
              kFeatureProfileScopedExecutionV1);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeCOMMANDS), 0ull);
    EXPECT_FALSE(featureRequiresSection(0, kSectionTypeMOE_LAYER_SPECS));
    EXPECT_FALSE(featureRequiresSection(0, kSectionTypeCONTENT_DIGESTS));
    EXPECT_FALSE(featureRequiresSection(kFeatureAgentServingV1,
                                        kSectionTypeMOE_LAYER_SPECS));
    EXPECT_TRUE(featureRequiresSection(kFeatureDynamicMoeV1,
                                       kSectionTypeMOE_LAYER_SPECS));
    EXPECT_TRUE(featureRequiresSection(kFeatureDynamicMoeV1,
                                       kSectionTypeCONTENT_DIGESTS));
    EXPECT_TRUE(featureRequiresSection(kFeatureAgentServingV1,
                                       kSectionTypeAGENT_REQUEST_PROFILES));
    EXPECT_FALSE(featureRequiresSection(kFeatureDynamicMoeV1,
                                        kSectionTypeCOMMANDS));
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

uint16_t rd16(const MeshBytes &image, size_t offset)
{
    return uint16_t(image[offset]) | (uint16_t(image[offset + 1]) << 8);
}

uint32_t rd32(const MeshBytes &image, size_t offset)
{
    return uint32_t(rd16(image, offset)) | (uint32_t(rd16(image, offset + 2)) << 16);
}

uint64_t rd64(const MeshBytes &image, size_t offset)
{
    return uint64_t(rd32(image, offset)) | (uint64_t(rd32(image, offset + 4)) << 32);
}

void wr32(MeshBytes &image, size_t offset, uint32_t value)
{
    for (int i = 0; i < 4; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

void wr16(MeshBytes &image, size_t offset, uint16_t value)
{
    for (int i = 0; i < 2; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

void wr64(MeshBytes &image, size_t offset, uint64_t value)
{
    for (int i = 0; i < 8; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

size_t sectionOffset(const MeshBytes &image, uint16_t type)
{
    using namespace mesh_abi;
    const size_t dir = size_t(rd64(image, 24));
    const uint32_t count = rd32(image, 32);
    for (uint32_t i = 0; i < count; i++) {
        const size_t entry = dir + i * kSectionDirBytes;
        if (rd16(image, entry) == type)
            return size_t(rd64(image, entry + 8));
    }
    ADD_FAILURE() << "section " << type << " not found";
    return 0;
}

using namespace mesh_abi;

TEST(MeshBinaryFeatureTest, GatesFeatureBitsAndConditionalSections)
{
    const MeshBytes golden =
        fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    struct FeatureCase
    {
        const char *name;
        uint16_t minor;
        uint64_t features;
        const char *code;
    };
    const FeatureCase rejected[] = {
        {"unknown_bit", kAbiMinor, 0x8000000000000000ull,
         "E_ABI_VERSION"},
        {"below_writer_minor", 0, kFeatureDynamicMoeV1, "E_ABI_VERSION"},
        {"missing_feature_sections", 1, kFeatureDynamicMoeV1,
         "E_ABI_SECTION_RANGE"},
    };
    for (const auto &test : rejected) {
        MeshBytes image = golden;
        wr16(image, 10, test.minor);
        wr64(image, 104, test.features);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        EXPECT_FALSE(decodeMeshBinary(image, program, error)) << test.name;
        EXPECT_EQ(error.code, test.code) << test.name;
    }
    MeshBytes accepted = golden;
    wr16(accepted, 10, 1);
    wr64(accepted, 104, 0);
    recomputeMeshChecksums(accepted);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(accepted, program, error)) << error.code;
    EXPECT_EQ(program.abi_minor, 1);
}

struct Mutation
{
    const char *name;
    size_t offset;
    uint8_t mask;
    const char *code;
};

TEST(MeshBinaryParityTest, RejectsRecomputedChecksumMutations)
{
    const MeshBytes golden = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    const size_t attrs = sectionOffset(golden, kSectionTypeOP_ATTRS);
    const size_t descriptors = sectionOffset(golden, kSectionTypeDMA_DESCRIPTORS);
    const size_t operands = sectionOffset(golden, kSectionTypeCOMMAND_OPERANDS);
    const Mutation mutations[] = {
        // OP_ATTRS record 0: flip its reserved u16 -> E_ABI_RESERVED.
        {"attr_reserved",
         attrs + kOpAttrsReservedOffset, 0x01, "E_ABI_RESERVED"},
        // descriptor 0 src.memory_space bits flipped off HBM -> E_DMA_RANGE.
        {"load_direction",
         descriptors + kDmaDescriptorsSrcOffset +
             kDmaEndpointMemorySpaceOffset, 0x06, "E_DMA_RANGE"},
        // descriptor kind high byte -> outside the closed set.
        {"endpoint_wrap",
         descriptors + kDmaDescriptorsKindOffset + 1, 0xFF, "E_DMA_RANGE"},
        {"operand_reserved",
         operands + kCommandOperandsReservedOffset, 0x01,
         "E_ABI_RESERVED"},
    };
    for (const auto &mutation : mutations) {
        MeshBytes image = golden;
        image[mutation.offset] ^= mutation.mask;
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        bool decoded = decodeMeshBinary(image, program, error);
        bool verified = false;
        if (decoded)
            verified = verifyDecodedProgram(program, testArch(), error);
        const bool accepted = decoded && verified;
        EXPECT_FALSE(accepted) << mutation.name;
        if (!accepted) {
            EXPECT_FALSE(error.code.empty()) << mutation.name;
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

// begin+count table slices must be range-checked without 32-bit wrap: a
// wrapped span silently skips per-element validation (or reads far out of
// bounds) instead of failing closed.
TEST(MeshBinarySpanTest, RejectsBeginCountWrap)
{
    using namespace mesh_abi;
    const MeshBytes golden = fromArray(kGoldenMshbRepeat, sizeof(kGoldenMshbRepeat));
    const size_t cmds = sectionOffset(golden, kSectionTypeCOMMANDS);
    const size_t attrs = sectionOffset(golden, kSectionTypeOP_ATTRS);
    const size_t streams = sectionOffset(golden, kSectionTypeSTREAMS);

    DecodedProgram reference;
    MeshLoadError decode_error;
    ASSERT_TRUE(decodeMeshBinary(golden, reference, decode_error));
    const DecodedCommand *repeat = nullptr;
    for (const auto &command : reference.commands)
        if (command.opcode == kOpcodeREPEAT)
            repeat = &command;
    ASSERT_NE(repeat, nullptr);
    uint32_t ordinal = 0;
    for (const auto &stream : reference.streams) {
        if (stream.core_id != repeat->core_id || stream.stream_id != repeat->stream_id)
            continue;
        for (uint32_t i = 0; i < stream.command_count; i++)
            if (reference.commands[stream.command_begin + i].command_id ==
                repeat->command_id)
                ordinal = i;
    }

    struct Case
    {
        const char *name;
        std::function<void(MeshBytes &)> patch;
    };
    const Case cases[] = {
        {"wait_wrap", [&](MeshBytes &image) {
             wr32(image, cmds + kCommandsBytes + kCommandsWaitBeginOffset, 0xFFFFFFFFu);
             wr16(image, cmds + kCommandsBytes + kCommandsWaitCountOffset, 2);
         }},
        {"operand_wrap", [&](MeshBytes &image) {
             wr32(image, cmds + kCommandsBytes + kCommandsOperandBeginOffset, 0xFFFFFFFFu);
             wr16(image, cmds + kCommandsBytes + kCommandsOperandCountOffset, 2);
         }},
        {"stream_wrap", [&](MeshBytes &image) {
             wr32(image, streams + kStreamsCommandBeginOffset, 0xFFFFFFFFu);
             wr32(image, streams + kStreamsCommandCountOffset, 2);
         }},
        {"repeat_wrap", [&](MeshBytes &image) {
             const size_t record = attrs + size_t(repeat->attr_index - 1) * kOpAttrsBytes;
             wr32(image, record + 4 + 0, 0xFFFFFFFFu);
             wr32(image, record + 4 + 4, ordinal + 1);
         }},
    };
    for (const auto &test_case : cases) {
        MeshBytes image = golden;
        test_case.patch(image);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        const bool accepted =
            decodeMeshBinary(image, program, error) &&
            verifyDecodedProgram(program, testArch(), error);
        EXPECT_FALSE(accepted) << test_case.name;
        if (!accepted) {
            EXPECT_EQ(error.code, "E_ABI_BOUNDS") << test_case.name;
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

MeshBytes loadFixture(const char *relative_path)
{
    std::ifstream stream(relative_path, std::ios::binary);
    EXPECT_TRUE(stream.good()) << relative_path;
    return MeshBytes((std::istreambuf_iterator<char>(stream)),
                     std::istreambuf_iterator<char>());
}

RuntimeArch testMoeArch()
{
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = golden::kGate5ArchDigest;
    arch.partitions = {
        {mesh_abi::kSramPartitionKindSTATIC_PROGRAM, 0x000000, 0x080000, 64,
         64, 0},
        {mesh_abi::kSramPartitionKindRUNTIME_SCRATCH, 0x080000, 0x040000, 64,
         64, 0},
        {mesh_abi::kSramPartitionKindWEIGHT_CACHE, 0x0C0000, 0x080000, 64,
         128, 64},
        {mesh_abi::kSramPartitionKindKV_STAGING_CACHE, 0x140000, 0x080000, 64,
         64, 0},
    };
    arch.weight_cache_slot_bytes = 4096;
    return arch;
}

TEST(MeshBinaryMoeTest, LoadsCrossLanguageMoeFixture)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.abi_major, 1u);
    EXPECT_EQ(program.abi_minor, 1u);
    EXPECT_EQ(program.required_features, kFeatureDynamicMoeV1);
    EXPECT_TRUE(program.has_moe_v1);
    ASSERT_EQ(program.content_digests.size(), 1u);
    ASSERT_EQ(program.moe_layer_specs.size(), 1u);
    ASSERT_EQ(program.moe_expert_specs.size(), 2u);
    ASSERT_EQ(program.moe_dynamic_regions.size(), 1u);
    ASSERT_EQ(program.moe_kernel_specs.size(), 1u);
    const auto &layer = program.moe_layer_specs[0];
    EXPECT_EQ(layer.layer_id, 1u);
    EXPECT_EQ(layer.expert_count, 2);
    EXPECT_EQ(layer.top_k, 2);
    EXPECT_EQ(layer.kernel_spec_index, 0u);
    EXPECT_EQ(layer.expert_first, 0u);
    EXPECT_EQ(layer.token_bytes, 64u);
    EXPECT_EQ(layer.output_token_bytes, 128u);
    EXPECT_EQ(layer.capacity_factor_q16, 0x10000u);
    EXPECT_EQ(layer.overflow_policy, kMoeOverflowPolicyDROP);
    EXPECT_EQ(layer.transport_mode, kMoeTransportModeVARIABLE_ALL_TO_ALL_V);
    EXPECT_EQ(layer.dynamic_region_first, 0u);
    EXPECT_EQ(layer.dynamic_region_count, 1u);
    EXPECT_EQ(layer.max_tokens_per_frozen_batch, 8u);
    EXPECT_EQ(layer.max_requests_per_batch, 2u);
    EXPECT_EQ(layer.max_routes, 16u);
    EXPECT_EQ(layer.max_materialized_commands, 32u);
    EXPECT_EQ(layer.max_materialized_descriptors, 32u);
    EXPECT_EQ(layer.max_materialized_transfers, 8u);
    EXPECT_EQ(layer.max_dynamic_allocations, 8u);
    EXPECT_EQ(layer.max_materialized_events, 17u);
    EXPECT_EQ(layer.reserved1, 0u);
    const auto &region = program.moe_dynamic_regions[0];
    EXPECT_EQ(region.region_id, 1u);
    EXPECT_EQ(region.layer_id, 1u);
    EXPECT_EQ(region.core_id, 0);
    EXPECT_EQ(region.stream_id, 0);
    EXPECT_EQ(region.insert_after_command_id, 4u);
    EXPECT_EQ(region.resume_before_command_id, 5u);
    EXPECT_EQ(region.entry_event_id, 4u);
    EXPECT_EQ(region.scratch_offset, 0x80000ull);
    EXPECT_EQ(region.scratch_bytes, 0x40000ull);
    EXPECT_EQ(region.scratch_alignment, 64u);
    EXPECT_EQ(region.max_overlay_commands, 32u);
    EXPECT_EQ(region.max_overlay_events, 16u);
    EXPECT_EQ(region.max_overlay_descriptors, 32u);
    EXPECT_EQ(region.max_overlay_transfers, 8u);
    EXPECT_EQ(region.max_overlay_allocations, 8u);
    const auto &kernel = program.moe_kernel_specs[0];
    EXPECT_EQ(kernel.layer_id, 1u);
    EXPECT_EQ(kernel.expert_opcode, kOpcodeGEMM);
    EXPECT_EQ(kernel.input_dtype, kDtypeFP16);
    EXPECT_EQ(kernel.accum_dtype, kDtypeFP32);
    EXPECT_EQ(kernel.output_dtype, kDtypeFP16);
    EXPECT_EQ(kernel.batch, 1u);
    EXPECT_EQ(kernel.n, 64u);
    EXPECT_EQ(kernel.k, 32u);
    EXPECT_EQ(kernel.combine_kind, kMoeCombineKindLOCAL_REDUCE);
    EXPECT_EQ(kernel.efficiency_q16, 0x10000u);
    EXPECT_EQ(kernel.tensor_setup_cycles, 64u);
    EXPECT_EQ(kernel.tensor_flush_cycles, 32u);
    EXPECT_EQ(kernel.input_token_bytes, 64u);
    EXPECT_EQ(kernel.output_token_bytes, 128u);
    EXPECT_EQ(kernel.weight_operand_bytes, 4096ull);
    EXPECT_EQ(kernel.expert_result_alignment, 64u);
    EXPECT_EQ(kernel.max_m, 8u);
    EXPECT_EQ(kernel.combine_setup_cycles, 32u);
    EXPECT_EQ(kernel.combine_flush_cycles, 16u);
    EXPECT_EQ(kernel.reserved0, 0u);
    EXPECT_EQ(kernel.reserved1, 0u);
    for (const auto &expert : program.moe_expert_specs) {
        EXPECT_EQ(expert.layer_id, 1u);
        EXPECT_EQ(expert.core_id, 0);
        EXPECT_EQ(expert.reserved_core, 0);
        EXPECT_EQ(expert.weight_symbol_id, 4u);
        EXPECT_EQ(expert.weight_bytes, 4096ull);
        EXPECT_EQ(expert.weight_digest_index, 0u);
        EXPECT_EQ(expert.reserved, 0u);
    }
    EXPECT_EQ(program.moe_expert_specs[0].expert_id, 0);
    EXPECT_EQ(program.moe_expert_specs[0].weight_region_offset, 0ull);
    EXPECT_EQ(program.moe_expert_specs[1].expert_id, 1);
    EXPECT_EQ(program.moe_expert_specs[1].weight_region_offset, 4096ull);
    const auto &digest = program.content_digests[0];
    EXPECT_EQ(digest.object_kind, kTensorRoleWEIGHT);
    EXPECT_EQ(digest.reserved, 0);
    EXPECT_EQ(digest.object_id, 2u);
    MeshLoadError verify_error;
    EXPECT_TRUE(verifyDecodedProgram(program, testMoeArch(), verify_error))
        << verify_error.code << ": " << verify_error.message;
}

void writeField(MeshBytes &image, size_t offset, int width, uint64_t value)
{
    if (width == 2) {
        wr16(image, offset, uint16_t(value));
    } else if (width == 4) {
        wr32(image, offset, uint32_t(value));
    } else {
        wr64(image, offset, value);
    }
}

void refreshServingChecksums(MeshBytes &image)
{
    using namespace mesh_abi;
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error))
        return;
    const auto base = programProfileKeyBaseDigest(program);
    const size_t section =
        sectionOffset(image, kSectionTypeAGENT_REQUEST_PROFILES);
    for (size_t i = 0; i < program.agent_request_profiles.size(); i++) {
        const uint64_t key =
            requestProfileKey(program.agent_request_profiles[i], base);
        writeField(image,
                   section + i * kAgentRequestProfilesBytes +
                       kAgentRequestProfilesRequestedProfileKeyOffset,
                   8, key);
    }
    recomputeMeshChecksums(image);
}


struct MoeViolation
{
    const char *name;
    size_t offset;
    int width;
    uint64_t value;
    const char *code;
};

TEST(MeshBinaryMoeTest, RejectsSemanticViolationsAfterRecomputedChecksums)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t layer = sectionOffset(golden, kSectionTypeMOE_LAYER_SPECS);
    const size_t expert = sectionOffset(golden, kSectionTypeMOE_EXPERT_SPECS);
    const size_t region =
        sectionOffset(golden, kSectionTypeMOE_DYNAMIC_REGIONS);
    const size_t kernel = sectionOffset(golden, kSectionTypeMOE_KERNEL_SPECS);
    const size_t digest = sectionOffset(golden, kSectionTypeCONTENT_DIGESTS);
    const MoeViolation cases[] = {
        {"layer_top_k", layer + kMoeLayerSpecsTopKOffset, 2, 3,
         "E_ABI_BOUNDS"},
        {"layer_flags", layer + kMoeLayerSpecsFlagsOffset, 4, 1,
         "E_ABI_RESERVED"},
        {"layer_event_bound",
         layer + kMoeLayerSpecsMaxMaterializedEventsOffset, 4, 16,
         "E_ABI_BOUNDS"},
        {"layer_route_bound", layer + kMoeLayerSpecsMaxRoutesOffset, 4, 1,
         "E_ABI_BOUNDS"},
        {"layer_token_bytes", layer + kMoeLayerSpecsTokenBytesOffset, 4, 0,
         "E_ABI_BOUNDS"},
        {"expert_core",
         expert + kMoeExpertSpecsBytes + kMoeExpertSpecsCoreIdOffset, 2, 9,
         "E_ABI_BOUNDS"},
        {"expert_dense_id",
         expert + kMoeExpertSpecsBytes + kMoeExpertSpecsExpertIdOffset, 2, 5,
         "E_ABI_ORDER"},
        {"expert_weight_escape",
         expert + kMoeExpertSpecsBytes +
             kMoeExpertSpecsWeightRegionOffsetOffset, 8, 8192,
         "E_ABI_BOUNDS"},
        {"expert_weight_empty", expert + kMoeExpertSpecsWeightBytesOffset, 8,
         0, "E_ABI_BOUNDS"},
        {"expert_digest_index",
         expert + kMoeExpertSpecsWeightDigestIndexOffset, 4, 0xFFFFFFFF,
         "E_ABI_BOUNDS"},
        {"region_gate_adjacency",
         region + kMoeDynamicRegionsResumeBeforeCommandIdOffset, 4, 6,
         "E_ABI_BOUNDS"},
        {"region_entry_event", region + kMoeDynamicRegionsEntryEventIdOffset,
         4, 1, "E_ABI_BOUNDS"},
        {"region_scratch_escape",
         region + kMoeDynamicRegionsScratchBytesOffset, 8, 0x200000,
         "E_ABI_BOUNDS"},
        {"region_alignment",
         region + kMoeDynamicRegionsScratchAlignmentOffset, 4, 48,
         "E_ABI_BOUNDS"},
        {"kernel_shape", kernel + kMoeKernelSpecsKOffset, 4, 64,
         "E_ABI_BOUNDS"},
        {"kernel_max_m", kernel + kMoeKernelSpecsMaxMOffset, 4, 1,
         "E_ABI_BOUNDS"},
        {"kernel_opcode", kernel + kMoeKernelSpecsExpertOpcodeOffset, 2,
         kOpcodeSOFTMAX, "E_ABI_ENUM"},
        {"kernel_weight", kernel + kMoeKernelSpecsWeightOperandBytesOffset, 8,
         8192, "E_ABI_BOUNDS"},
        {"digest_object", digest + kContentDigestsObjectIdOffset, 4, 3,
         "E_ABI_BOUNDS"},
    };
    for (const auto &test : cases) {
        MeshBytes image = golden;
        writeField(image, test.offset, test.width, test.value);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        const bool accepted =
            decodeMeshBinary(image, program, error) &&
            verifyDecodedProgram(program, testMoeArch(), error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
}

TEST(MeshBinaryMoeTest, RejectsRegionScratchOutsideRuntimeScratch)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t region =
        sectionOffset(golden, kSectionTypeMOE_DYNAMIC_REGIONS);
    const MoeViolation cases[] = {
        {"scratch_into_weight_cache",
         region + kMoeDynamicRegionsScratchOffsetOffset, 8, 0x0C0000,
         "E_ABI_BOUNDS"},
        {"scratch_above_partition",
         region + kMoeDynamicRegionsScratchOffsetOffset, 8, 0x0C0000 - 0x1000,
         "E_ABI_BOUNDS"},
    };
    for (const auto &test : cases) {
        MeshBytes image = golden;
        writeField(image, test.offset, test.width, test.value);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        const bool accepted =
            decodeMeshBinary(image, program, error) &&
            verifyDecodedProgram(program, testMoeArch(), error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
    RuntimeArch partitioned = testMoeArch();
    partitioned.partitions.clear();
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(golden, program, error));
    EXPECT_FALSE(verifyDecodedProgram(program, partitioned, error));
    EXPECT_EQ(error.code, "E_ARCH_PARTITION");
}

TEST(MeshBinaryMoeTest, LoadsCrossLanguageDualMoeFixture)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    ASSERT_TRUE(verifyDecodedProgram(program, testMoeArch(), error))
        << error.code << ": " << error.message;
    ASSERT_EQ(program.moe_layer_specs.size(), 1u);
    ASSERT_EQ(program.moe_dynamic_regions.size(), 2u);
    ASSERT_EQ(program.moe_expert_specs.size(), 2u);
    EXPECT_EQ(program.moe_layer_specs[0].dynamic_region_count, 2u);
    EXPECT_EQ(program.moe_layer_specs[0].max_materialized_events, 33u);
    EXPECT_EQ(program.moe_dynamic_regions[0].core_id, 0);
    EXPECT_EQ(program.moe_dynamic_regions[1].core_id, 1);
    EXPECT_EQ(program.moe_expert_specs[0].core_id, 0);
    EXPECT_EQ(program.moe_expert_specs[1].core_id, 1);
    EXPECT_LT(program.moe_dynamic_regions[0].insert_after_command_id,
              program.moe_dynamic_regions[0].resume_before_command_id);
    EXPECT_LT(program.moe_dynamic_regions[1].insert_after_command_id,
              program.moe_dynamic_regions[1].resume_before_command_id);
}

TEST(WeightCacheIdentityTest, MatchesTheCrossLanguageKeyGolden)
{
    using namespace mesh_abi;
    WeightFillKey fill;
    fill.core_id = 3;
    fill.cache_partition_id = 1;
    fill.weight_tag_index = 7;
    fill.cache_generation = 2;
    fill.fill_incarnation = 1;
    const auto fill_wire = weightFillKeyBytes(fill);
    EXPECT_EQ(fill_wire.size(), size_t(kWeightFillKeysBytes));
    const std::array<uint8_t, 16> expected_fill = {
        0x03, 0x00, 0x01, 0x00, 0x07, 0x00, 0x00, 0x00,
        0x02, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00};
    EXPECT_TRUE(std::equal(fill_wire.begin(), fill_wire.end(),
                           expected_fill.begin()));
    EXPECT_EQ(weightFillTrafficId(fill),
              "35f9e3feefe6ec9f42bf46be4e1d644c"
              "725f00f3f9d6658ab64a0fd5c4a0fc4d");

    const auto descriptor_wire = cacheDmaDescriptorKeyBytes(fill, 3);
    EXPECT_EQ(descriptor_wire.size(),
              size_t(kCacheDmaDescriptorKeysBytes));
    EXPECT_EQ(descriptor_wire[16], 3);
    EXPECT_EQ(descriptor_wire[17], 0);

    const ErrorSourceKey source = weightFillSource(fill);
    const auto source_wire = errorSourceKeyBytes(source);
    EXPECT_EQ(source_wire.size(), size_t(kErrorSourceKeysBytes));
    EXPECT_EQ(source_wire[kErrorSourceKeysErrorClassOffset],
              uint8_t(kMoeErrorClassWEIGHT_FILL));
    EXPECT_EQ(source_wire[kErrorSourceKeysDomainOffset],
              uint8_t(kMeshObjectDomainWEIGHT_FILL));
    EXPECT_EQ(source_wire[kErrorSourceKeysObjectKindOffset],
              uint8_t(kMeshObjectKindWEIGHT_FILL_OBLIGATION));
    EXPECT_TRUE(std::equal(fill_wire.begin(), fill_wire.end(),
                           source_wire.begin() +
                               kErrorSourceKeysAuxKeyOffset));
}

TEST(WeightCacheIdentityTest, OrdersNumericallyInsteadOfByWireBytes)
{
    using namespace mesh_abi;
    WeightCacheBaseKey low;
    low.core_id = 0x00FF;
    low.cache_partition_id = 1;
    WeightCacheBaseKey high;
    high.core_id = 0x0100;
    high.cache_partition_id = 1;
    EXPECT_TRUE(weightCacheBaseKeyLess(low, high));
    WeightFillKey low_fill;
    low_fill.core_id = 0x00FF;
    low_fill.cache_partition_id = 1;
    WeightFillKey high_fill;
    high_fill.core_id = 0x0100;
    high_fill.cache_partition_id = 1;
    EXPECT_TRUE(weightFillKeyLess(low_fill, high_fill));
    WeightFillKey next = low_fill;
    next.fill_incarnation = 1;
    EXPECT_TRUE(weightFillKeyLess(low_fill, next));
}

TEST(WeightCacheCoordinatorTest, MatchesTheCrossLanguageTransitionGolden)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 3;
    config.slot_bytes = 4096;
    config.mshr_slots = 3;
    config.eviction_slots = 3;
    config.obligation_slots = 3;
    config.subscriber_slots = 6;
    config.cacheable_tags = {1, 2, 3, 4};
    MoeWeightCache cache(config);

    std::vector<MoeCacheToken> cold;
    ASSERT_EQ(cache.reserve(1, 1, {1, 2}, 10, cold),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cold.size(), 2u);
    EXPECT_EQ(cold[0].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(cold[0].slot_id, 0u);
    EXPECT_EQ(cold[0].fill.fill_incarnation, 1u);
    EXPECT_EQ(cold[1].slot_id, 1u);
    EXPECT_EQ(cold[1].fill.fill_incarnation, 2u);
    for (const auto &token : cold) {
        cache.markFilling(token.fill);
        cache.noteFillSuccess(token.fill, 4096, 0);
    }
    std::vector<MoeCacheToken> warm;
    ASSERT_EQ(cache.reserve(2, 1, {1}, 20, warm),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(warm.size(), 1u);
    EXPECT_EQ(warm[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(warm[0].slot_id, 0u);
    EXPECT_FALSE(warm[0].has_fill);

    std::vector<MoeCacheToken> mixed;
    ASSERT_EQ(cache.reserve(3, 1, {2, 3}, 30, mixed),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(mixed.size(), 2u);
    EXPECT_EQ(mixed[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(mixed[1].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(mixed[1].slot_id, 2u);
    EXPECT_EQ(mixed[1].fill.fill_incarnation, 3u);
    cache.markFilling(mixed[1].fill);
    cache.noteFillSuccess(mixed[1].fill, 4096, 0);
    for (const auto &token : cold)
        cache.releaseToken(token.token_id);
    for (const auto &token : warm)
        cache.releaseToken(token.token_id);
    for (const auto &token : mixed)
        cache.releaseToken(token.token_id);

    std::vector<MoeCacheToken> evict;
    ASSERT_EQ(cache.reserve(4, 1, {4}, 40, evict),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(evict.size(), 1u);
    EXPECT_EQ(evict[0].slot_id, 0u);
    EXPECT_EQ(evict[0].fill.fill_incarnation, 4u);
    EXPECT_EQ(cache.evictionFree(), 2u);
    cache.markFilling(evict[0].fill);
    const ErrorSourceKey source = weightFillSource(evict[0].fill);
    cache.noteFillFailure(evict[0].fill,
                          kWeightFillFailureSiteCACHE_FILL_AXI_R, 50,
                          source);
    cache.releaseToken(evict[0].token_id);
    EXPECT_EQ(cache.evictionFree(), 3u);
    std::vector<MoeCacheToken> blocked;
    EXPECT_EQ(cache.reserve(5, 1, {4}, 60, blocked),
              MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_TRUE(blocked.empty());
    EXPECT_EQ(cache.nextFillIncarnation(), 5u);
    EXPECT_EQ(cache.nextUseEpoch(), 6u);
    ASSERT_EQ(cache.slots().size(), 3u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[1].weight_tag_index, 2u);
    EXPECT_EQ(cache.slots()[2].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[2].weight_tag_index, 3u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    const std::string snapshot = cache.snapshotJson();
    EXPECT_NE(snapshot.find("\"next_fill_incarnation\":5"), std::string::npos);
    EXPECT_NE(snapshot.find("\"weight_tag_index\":4"), std::string::npos);
}

TEST(WeightCacheIdentityTest, FailureSitesMapToTheFrozenDetailClosure){
    using namespace mesh_abi;
    EXPECT_STREQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_AXI_R),
                 "E_AXI_RESPONSE");
    EXPECT_STREQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SRAM_BOUNDS),
                 "E_DCORE_SRAM_BOUNDS");
    EXPECT_STREQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT),
                 "E_DCORE_ENGINE");
    EXPECT_STREQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_SOURCE_VALIDITY),
                 "E_DCORE_POISON_READ");
    EXPECT_EQ(weightFillFailureDetail(4), nullptr);
}

TEST(WeightCacheCoordinatorTest, ErrorWinsOverALateSuccessAndRetiresOnce)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(3, 1, {1, 2}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 2u);
    const auto failing = tokens[0].fill;
    cache.advanceCacheEdge();
    cache.advanceEngineEdge();
    cache.markIssued(failing);
    cache.markInFlight(failing);
    cache.noteFillFailure(failing, kWeightFillFailureSiteCACHE_FILL_AXI_R,
                          30, weightFillSource(failing));
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotErrorHeld);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 0u);
    EXPECT_EQ(cache.mshrFree(), 0u);
    cache.noteFillSuccess(failing, 4096, 31);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotErrorHeld);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 0u);
    EXPECT_EQ(cache.mshrFree(), 0u);
    EXPECT_EQ(cache.obligations().at(1).drained_bytes, 4096u);
    cache.noteFillSuccess(tokens[1].fill, 4096, 32);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    for (const auto &token : tokens)
        cache.releaseToken(token.token_id);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotValid);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.evictionFree(), config.eviction_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_TRUE(cache.hasTombstone(cache.baseKey(1)));
    EXPECT_STREQ(weightFillFailureDetail(
                     kWeightFillFailureSiteCACHE_FILL_AXI_R),
                 "E_AXI_RESPONSE");
    std::vector<MoeCacheToken> retry;
    EXPECT_EQ(cache.reserve(4, 1, {1}, 40, retry),
              MoeWeightCache::ReserveStatus::FAILED);
    EXPECT_EQ(cache.nextFillIncarnation(), 3u);
}

TEST(WeightCacheCoordinatorTest, ReleasesAResourceExactlyOncePerFill)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(5, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 1u);
    const auto fill = tokens[0].fill;
    cache.markFilling(fill);
    cache.abortBeforeStart(5);
    cache.noteFailureFanoutDone(5);
    EXPECT_EQ(cache.mshrFree(), 1u);
    EXPECT_EQ(cache.obligationFree(), 1u);
    EXPECT_EQ(cache.subscriberFree(), 3u);
    cache.noteFillSuccess(fill, 4096, 20);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 4096u);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.obligations().size(), 0u);
    EXPECT_EQ(cache.fillLog().size(), 1u);
    EXPECT_EQ(cache.fillLog()[0].bytes, 4096u);
}

TEST(WeightCacheFaultJoinTest, ReleasesOnlyAfterFillDrainAndFanoutMeet)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(tokens.size(), 1u);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 0);
    cache.markFilling(token.fill);
    cache.noteInstanceFault(1, 12);
    EXPECT_EQ(cache.subscribersInState(
                  kCacheSubscriberStateTERMINAL_TOMBSTONED), 1u);
    EXPECT_EQ(cache.liveTokens(), 1u);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteFailureFanoutDone(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteFillSuccess(token.fill, 4096, 20);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.faultedFillTerminals(), 1u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.mshrFree(), config.mshr_slots);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.obligations().size(), 0u);
}

TEST(WeightCacheFaultJoinTest, FillTerminalFirstStillWaitsForTheDrain)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 2, {2}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 0);
    cache.markFilling(token.fill);
    cache.noteInstanceFault(1, 12);
    cache.noteFailureFanoutDone(1);
    cache.noteFillSuccess(token.fill, 4096, 14);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteOwnedWorkDrained(1);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.slots()[token.slot_id].state, kCacheSlotValid);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
}

TEST(WeightCacheFaultJoinTest, FaultedSubscribersAreNeverWoken)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(tokens[0].token_id, 0);
    cache.markFilling(tokens[0].fill);
    cache.noteInstanceFault(1, 11);
    cache.noteFailureFanoutDone(1);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.hasPendingSubscribers(), false);
    cache.noteFillSuccess(tokens[0].fill, 4096, 12);
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 0u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWAITING), 0u);
}

TEST(WeightCacheOwnershipTest, HoldsThePinUntilTheLastConsumerDrains)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(7, 4, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    const MoeCacheToken token = tokens[0];
    cache.setTokenConsumers(token.token_id, 2);
    cache.markFilling(token.fill);
    cache.noteFillSuccess(token.fill, 4096, 20);
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.tokenReleased(token.token_id), false);
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_TRUE(cache.tokenReleased(token.token_id));
    cache.noteConsumerDrained(7, 4, 1);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.obligationFree(), config.obligation_slots);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
}

TEST(WeightCacheReservationTest, PrepareLeavesNoVisibleStateBehind)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 1;
    config.slot_bytes = 4096;
    config.mshr_slots = 1;
    config.eviction_slots = 0;
    config.obligation_slots = 1;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cold(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cold.reserve(1, 1, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cold.markFilling(tokens[0].fill);
    ASSERT_EQ(cold.slots()[0].state, kCacheSlotFilling);
    const uint32_t pins = cold.slots()[0].pins;
    const uint32_t incarnation = cold.nextFillIncarnation();
    const MoeWeightCache::ReservePlan blocked =
        cold.prepare(2, 1, {2}, 20);
    EXPECT_EQ(blocked.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    EXPECT_EQ(cold.liveTokens(), 1u);
    EXPECT_EQ(cold.slots()[0].state, kCacheSlotFilling);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    EXPECT_EQ(cold.nextFillIncarnation(), incarnation);
    EXPECT_EQ(cold.mshrFree(), 0u);
    EXPECT_EQ(cold.obligations().size(), 1u);
    const MoeWeightCache::ReservePlan attach = cold.prepare(2, 1, {1}, 20);
    ASSERT_EQ(attach.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(attach.attached.size(), 1u);
    EXPECT_EQ(cold.liveTokens(), 1u);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    cold.noteFillSuccess(tokens[0].fill, 4096, 21);
    const uint32_t epoch = cold.nextUseEpoch();
    const MoeWeightCache::ReservePlan warm = cold.prepare(3, 1, {1}, 22);
    ASSERT_EQ(warm.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(warm.hits.size(), 1u);
    EXPECT_EQ(cold.slots()[0].pins, pins);
    EXPECT_EQ(cold.nextUseEpoch(), epoch);
    EXPECT_EQ(cold.liveTokens(), 1u);
    const std::vector<MoeCacheToken> committed = cold.commit(warm, 22);
    ASSERT_EQ(committed.size(), 1u);
    EXPECT_EQ(committed[0].outcome, kCacheResidencyOutcomeHIT);
    EXPECT_EQ(cold.slots()[0].pins, pins + 1);
    EXPECT_EQ(cold.liveTokens(), 2u);
}

TEST(WeightCacheOwnershipTest, TwoBatchesShareATagWithoutCrossRelease)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> first;
    ASSERT_EQ(cache.reserve(1, 1, {1}, 10, first),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(first[0].token_id, 1);
    cache.markFilling(first[0].fill);
    cache.noteFillSuccess(first[0].fill, 4096, 20);
    std::vector<MoeCacheToken> second;
    ASSERT_EQ(cache.reserve(2, 1, {1}, 21, second),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(second[0].outcome, kCacheResidencyOutcomeHIT);
    cache.setTokenConsumers(second[0].token_id, 1);
    ASSERT_EQ(cache.liveTokens(), 2u);
    cache.noteConsumerDrained(1, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(first[0].token_id));
    EXPECT_FALSE(cache.tokenReleased(second[0].token_id));
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.slots()[first[0].slot_id].pins, 1u);
    cache.noteConsumerDrained(2, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(second[0].token_id));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[first[0].slot_id].pins, 0u);
}

TEST(WeightCacheFaultJoinTest, LayerThirtyTwoKeepsItsOwnOccurrence)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> tokens;
    ASSERT_EQ(cache.reserve(1, 32, {1}, 10, tokens),
              MoeWeightCache::ReserveStatus::COMMITTED);
    cache.setTokenConsumers(tokens[0].token_id, 1);
    cache.markFilling(tokens[0].fill);
    cache.noteInstanceFault(1, 11);
    cache.noteFailureFanoutDone(1);
    cache.noteOwnedWorkDrained(1);
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.subscribersInState(
                  kCacheSubscriberStateTERMINAL_TOMBSTONED), 1u);
    cache.noteFillSuccess(tokens[0].fill, 4096, 20);
    EXPECT_EQ(cache.tombstonedSubscribers(), 1u);
    EXPECT_EQ(cache.wokenSubscribers(), 0u);
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[tokens[0].slot_id].state, kCacheSlotValid);
}

TEST(WeightCacheReservationTest, MergedShadowCoversEveryLayerOnce)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 4;
    config.slot_bytes = 4096;
    config.mshr_slots = 4;
    config.eviction_slots = 4;
    config.obligation_slots = 4;
    config.subscriber_slots = 8;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1, 2};
    demand[1].layer_id = 2;
    demand[1].tags = {1};
    const MoeWeightCache::ReservePlan plan = cache.prepare(5, demand, 10);
    ASSERT_EQ(plan.status, MoeWeightCache::ReserveStatus::COMMITTED);
    // One physical fill per base key, one token/pin per (batch, layer).
    ASSERT_EQ(plan.fills.size(), 2u);
    EXPECT_EQ(plan.layers.at(1).size(), 2u);
    EXPECT_EQ(plan.layers.at(2).size(), 1u);
    EXPECT_EQ(plan.fills[0].slot_id, 0u);
    EXPECT_EQ(plan.fills[1].slot_id, 1u);
    EXPECT_EQ(plan.first_incarnation, 1u);
    const std::vector<MoeCacheToken> tokens = cache.commit(plan, 10);
    ASSERT_EQ(tokens.size(), 3u);
    EXPECT_EQ(cache.slots()[0].weight_tag_index, 1u);
    EXPECT_EQ(cache.slots()[1].weight_tag_index, 2u);
    EXPECT_EQ(cache.slots()[0].pins, 2u);
    EXPECT_EQ(cache.slots()[1].pins, 1u);
    EXPECT_EQ(cache.nextFillIncarnation(), 3u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWAITING), 3u);
    uint32_t issues = 0;
    uint32_t attaches = 0;
    for (const auto &token : tokens) {
        if (token.base.weight_tag_index != 1)
            continue;
        if (token.outcome == kCacheResidencyOutcomeNEW_FILL)
            issues++;
        if (token.outcome == kCacheResidencyOutcomeATTACH)
            attaches++;
    }
    EXPECT_EQ(issues, 1u);
    EXPECT_EQ(attaches, 1u);
}

TEST(WeightCacheReservationTest, SubscriberPreflightCountsOccurrences)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 2;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1};
    demand[1].layer_id = 2;
    demand[1].tags = {2};
    // Three occurrences over two physical keys: subscriber capacity is the
    // occurrence count, so this is backpressure and never an underflow.
    std::vector<MoeWeightCache::LayerTags> three = demand;
    three[0].tags = {1, 2};
    const MoeWeightCache::ReservePlan blocked =
        cache.prepare(1, three, 10);
    EXPECT_EQ(blocked.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    EXPECT_EQ(blocked.fills.size(), 0u);
    EXPECT_EQ(cache.subscriberFree(), 2u);
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.slots()[1].pins, 0u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    const MoeWeightCache::ReservePlan fits = cache.prepare(1, demand, 11);
    ASSERT_EQ(fits.status, MoeWeightCache::ReserveStatus::COMMITTED);
    EXPECT_EQ(fits.fills.size(), 2u);
}

TEST(WeightCacheOwnershipTest, CrossLayerAliasReleasesEachOccurrenceIndependently)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    std::vector<MoeWeightCache::LayerTags> demand(2);
    demand[0].layer_id = 1;
    demand[0].tags = {1};
    demand[1].layer_id = 2;
    demand[1].tags = {1};
    const MoeWeightCache::ReservePlan plan = cache.prepare(6, demand, 10);
    ASSERT_EQ(plan.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(plan.fills.size(), 1u);
    const std::vector<MoeCacheToken> tokens = cache.commit(plan, 10);
    ASSERT_EQ(tokens.size(), 2u);
    ASSERT_EQ(cache.slots()[0].pins, 2u);
    for (const auto &token : tokens) {
        cache.setTokenConsumers(token.token_id, 1);
        if (token.outcome == kCacheResidencyOutcomeNEW_FILL)
            cache.markFilling(token.fill);
    }
    cache.noteFillSuccess(tokens[0].fill, 4096, 20);
    ASSERT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 2u);
    const uint64_t first = tokens[0].layer_id == 1 ? tokens[0].token_id
                                                   : tokens[1].token_id;
    const uint64_t second = tokens[0].layer_id == 1 ? tokens[1].token_id
                                                    : tokens[0].token_id;
    cache.noteConsumerDrained(6, 1, 1);
    EXPECT_TRUE(cache.tokenReleased(first));
    EXPECT_FALSE(cache.tokenReleased(second));
    EXPECT_EQ(cache.liveTokens(), 1u);
    EXPECT_EQ(cache.slots()[0].pins, 1u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateWOKEN), 1u);
    EXPECT_EQ(cache.subscribersInState(kCacheSubscriberStateRELEASED), 1u);
    EXPECT_EQ(cache.obligations().size(), 1u);
    cache.noteConsumerDrained(6, 2, 1);
    EXPECT_TRUE(cache.tokenReleased(second));
    EXPECT_EQ(cache.liveTokens(), 0u);
    EXPECT_EQ(cache.slots()[0].pins, 0u);
    EXPECT_EQ(cache.obligations().size(), 0u);
    EXPECT_EQ(cache.subscriberFree(), config.subscriber_slots);
    EXPECT_EQ(cache.tokenReleases(), cache.tokensCreated());
}

TEST(WeightCacheReservationTest, RejectsAStaleShadow)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 2;
    config.eviction_slots = 2;
    config.obligation_slots = 2;
    config.subscriber_slots = 4;
    config.cacheable_tags = {1, 2};
    MoeWeightCache cache(config);
    const MoeWeightCache::ReservePlan stale = cache.prepare(1, 1, {1}, 10);
    ASSERT_EQ(stale.status, MoeWeightCache::ReserveStatus::COMMITTED);
    const std::vector<MoeCacheToken> tokens = cache.commit(stale, 10);
    ASSERT_EQ(tokens.size(), 1u);
    bool stale_rejected = false;
    try {
        cache.commit(stale, 11);
    } catch (...) {
        stale_rejected = true;
    }
    EXPECT_TRUE(stale_rejected);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotFreeReserved);
}

TEST(WeightCacheReservationTest, ProtectsASameBatchHitFromItsOwnMissVictim)
{
    using namespace mesh_abi;
    MoeWeightCache::Config config;
    config.core_id = 0;
    config.slot_count = 2;
    config.slot_bytes = 4096;
    config.mshr_slots = 3;
    config.eviction_slots = 3;
    config.obligation_slots = 3;
    config.subscriber_slots = 6;
    config.cacheable_tags = {1, 2, 3};
    MoeWeightCache cache(config);
    std::vector<MoeCacheToken> cold;
    ASSERT_EQ(cache.reserve(1, 1, {1, 2}, 10, cold),
              MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(cold.size(), 2u);
    for (const auto &token : cold) {
        cache.markFilling(token.fill);
        cache.noteFillSuccess(token.fill, 4096, 11);
    }
    for (const auto &token : cold)
        cache.releaseToken(token.token_id);
    ASSERT_EQ(cache.slots()[0].state, kCacheSlotValid);
    ASSERT_EQ(cache.slots()[1].state, kCacheSlotValid);
    ASSERT_EQ(cache.slots()[0].pins, 0u);
    const MoeWeightCache::ReservePlan mixed = cache.prepare(2, 1, {1, 3}, 20);
    ASSERT_EQ(mixed.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(mixed.hits.size(), 1u);
    ASSERT_EQ(mixed.fills.size(), 1u);
    EXPECT_EQ(mixed.hits[0].first.weight_tag_index, 1u);
    EXPECT_EQ(mixed.fills[0].base.weight_tag_index, 3u);
    EXPECT_EQ(mixed.fills[0].slot_id, 1u);
    EXPECT_TRUE(mixed.fills[0].victim);
    const std::vector<MoeCacheToken> tokens = cache.commit(mixed, 20);
    ASSERT_EQ(tokens.size(), 2u);
    EXPECT_EQ(cache.slots()[0].state, kCacheSlotValid);
    EXPECT_EQ(cache.slots()[0].weight_tag_index, 1u);
    EXPECT_EQ(cache.slots()[0].valid_bytes, 4096u);
    EXPECT_EQ(cache.slots()[0].pins, 1u);
    EXPECT_EQ(cache.slots()[1].state, kCacheSlotEvictingReserved);
}

TEST(WeightCacheReservationTest, AWaitingCoreLeavesTheOtherCoreUntouched)
{
    using namespace mesh_abi;
    MoeWeightCache::Config open;
    open.core_id = 0;
    open.slot_count = 2;
    open.slot_bytes = 4096;
    open.mshr_slots = 2;
    open.eviction_slots = 2;
    open.obligation_slots = 2;
    open.subscriber_slots = 4;
    open.cacheable_tags = {1, 2};
    MoeWeightCache first(open);
    MoeWeightCache::Config full = open;
    full.core_id = 1;
    full.slot_count = 1;
    full.mshr_slots = 1;
    full.eviction_slots = 0;
    full.obligation_slots = 1;
    full.subscriber_slots = 2;
    MoeWeightCache second(full);
    std::vector<MoeCacheToken> held;
    ASSERT_EQ(second.reserve(9, 1, {1}, 5, held),
              MoeWeightCache::ReserveStatus::COMMITTED);
    second.markFilling(held[0].fill);
    const MoeWeightCache::ReservePlan left = first.prepare(9, 1, {1}, 6);
    const MoeWeightCache::ReservePlan right = second.prepare(9, 1, {2}, 6);
    ASSERT_EQ(left.status, MoeWeightCache::ReserveStatus::COMMITTED);
    ASSERT_EQ(right.status, MoeWeightCache::ReserveStatus::RESOURCE_WAIT);
    // The coordinator drops both shadows: neither core shows a pin, token,
    // incarnation or slot handoff from the abandoned attempt.
    EXPECT_EQ(first.liveTokens(), 0u);
    EXPECT_EQ(first.slots()[0].pins, 0u);
    EXPECT_EQ(first.slots()[0].state, kCacheSlotFree);
    EXPECT_EQ(first.nextFillIncarnation(), 1u);
    EXPECT_EQ(first.mshrFree(), first.mshrSlots());
    EXPECT_EQ(first.obligations().size(), 0u);
    const MoeWeightCache::ReservePlan retry = first.prepare(9, 1, {1}, 7);
    ASSERT_EQ(retry.status, MoeWeightCache::ReserveStatus::COMMITTED);
    const std::vector<MoeCacheToken> tokens = first.commit(retry, 7);
    ASSERT_EQ(tokens.size(), 1u);
    EXPECT_EQ(tokens[0].outcome, kCacheResidencyOutcomeNEW_FILL);
    EXPECT_EQ(first.slots()[0].state, kCacheSlotFreeReserved);
    EXPECT_EQ(first.slots()[0].pins, 1u);
}

TEST(MeshBinaryMoeTest, RejectsMoeSectionsWithoutTheFeatureBit)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    wr64(image, 104, 0);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_FEATURE");
}

TEST(MeshBinaryMoeTest, RejectsRegionGateOutsideTheRequestBody)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t region =
        sectionOffset(golden, kSectionTypeMOE_DYNAMIC_REGIONS);
    MeshBytes image = golden;
    writeField(image,
               region + kMoeDynamicRegionsInsertAfterCommandIdOffset, 4, 6);
    writeField(image,
               region + kMoeDynamicRegionsResumeBeforeCommandIdOffset, 4, 7);
    writeField(image, region + kMoeDynamicRegionsEntryEventIdOffset, 4, 6);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    const bool accepted =
        decodeMeshBinary(image, program, error) &&
        verifyDecodedProgram(program, testMoeArch(), error);
    EXPECT_FALSE(accepted);
    EXPECT_EQ(error.code, "E_LIFECYCLE");
    EXPECT_EQ(error.message, "region gate must precede the local halt");
}

std::array<uint8_t, 32> hexToDigest(const char *hex)
{
    std::array<uint8_t, 32> out{};
    for (size_t i = 0; i < 32; i++) {
        const auto nibble = [](char c) -> uint8_t {
            return c <= '9' ? uint8_t(c - '0') : uint8_t(c - 'a' + 10);
        };
        out[i] = uint8_t((nibble(hex[2 * i]) << 4) | nibble(hex[2 * i + 1]));
    }
    return out;
}

std::string digestToHex(const std::array<uint8_t, 32> &digest)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

TEST(MeshWeightTagsTest, MatchesCrossLanguageTagManifest)
{
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    const auto semantic = hexToDigest(golden::kGate5ProgramSemanticDigest);
    ASSERT_EQ(program.moe_expert_specs.size(),
              sizeof(golden::kGate5ExpertResolvedDigests) / 32);
    std::vector<WeightRegionBindingV1> bindings;
    for (size_t i = 0; i < program.moe_expert_specs.size(); i++) {
        const auto &expert = program.moe_expert_specs[i];
        WeightRegionBindingV1 binding;
        binding.weight_symbol_id = expert.weight_symbol_id;
        binding.weight_region_offset = expert.weight_region_offset;
        binding.weight_bytes = expert.weight_bytes;
        std::memcpy(binding.resolved_content_digest.data(),
                    golden::kGate5ExpertResolvedDigests[i], 32);
        bindings.push_back(binding);
    }
    const auto manifest = buildWeightTagManifest(semantic, bindings);
    ASSERT_EQ(manifest.size(), golden::kGate5WeightTagCount);
    const auto encoded = weightTagManifestBytes(manifest);
    ASSERT_EQ(encoded.size(), sizeof(golden::kGate5WeightTagManifest));
    EXPECT_TRUE(std::equal(encoded.begin(), encoded.end(),
                           golden::kGate5WeightTagManifest));
    EXPECT_EQ(digestToHex(weightTagManifestDigest(manifest)),
              golden::kGate5WeightTagManifestDigest);
    for (size_t i = 0; i < manifest.size(); i++) {
        EXPECT_EQ(manifest[i].weight_tag_index, i);
        const auto tuple_bytes = encodeWeightFillTagTuple(manifest[i].tuple);
        EXPECT_EQ(tuple_bytes.size(),
                  size_t(golden::kGate5WeightTagTupleBytes));
        constexpr size_t kManifestHeader =
            sizeof("AI_MESH_WEIGHT_TAG_V1") + sizeof(uint32_t);
        constexpr size_t kEntryBytes =
            sizeof(uint32_t) + WeightFillTagTupleV1::kBytes;
        EXPECT_TRUE(std::equal(
            tuple_bytes.begin(), tuple_bytes.end(),
            golden::kGate5WeightTagManifest + kManifestHeader +
                i * kEntryBytes + sizeof(uint32_t)));
        EXPECT_TRUE(std::equal(
            manifest[i].tuple.program_semantic_digest.begin(),
            manifest[i].tuple.program_semantic_digest.end(),
            semantic.begin()));
    }
}

TEST(MeshWeightTagsTest, DeduplicatesAndSortsByWireBytes)
{
    std::array<uint8_t, 32> semantic{};
    semantic[0] = 0x01;
    const std::vector<WeightRegionBindingV1> bindings = {
        {4, 4096, 4096, {0x02}},
        {4, 0, 4096, {0x03}},
        {4, 0, 4096, {0x03}},
    };
    const auto manifest = buildWeightTagManifest(semantic, bindings);
    ASSERT_EQ(manifest.size(), 2u);
    EXPECT_EQ(manifest[0].weight_tag_index, 0u);
    EXPECT_EQ(manifest[1].weight_tag_index, 1u);
    EXPECT_EQ(manifest[0].tuple.weight_region_offset, 0ull);
    EXPECT_EQ(manifest[1].tuple.weight_region_offset, 4096ull);
    const auto first = encodeWeightFillTagTuple(manifest[0].tuple);
    const auto second = encodeWeightFillTagTuple(manifest[1].tuple);
    EXPECT_LT(first, second);
}

TEST(MeshWeightTagsTest, RuntimeTagSitesMatchTheManifestIndices)
{
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    const std::array<uint8_t, 32> semantic =
        hexToDigest(golden::kGate5ProgramSemanticDigest);
    ASSERT_EQ(program.moe_expert_specs.size(),
              sizeof(golden::kGate5ExpertResolvedDigests) / 32);
    for (uint16_t core : {uint16_t(0), uint16_t(1)}) {
        std::vector<WeightRegionBindingV1> bindings;
        for (size_t i = 0; i < program.moe_expert_specs.size(); i++) {
            const auto &expert = program.moe_expert_specs[i];
            if (expert.core_id != core)
                continue;
            WeightRegionBindingV1 binding;
            binding.weight_symbol_id = expert.weight_symbol_id;
            binding.weight_region_offset = expert.weight_region_offset;
            binding.weight_bytes = expert.weight_bytes;
            std::memcpy(binding.resolved_content_digest.data(),
                        golden::kGate5ExpertResolvedDigests[i], 32);
            bindings.push_back(binding);
        }
        const auto manifest = buildWeightTagManifest(semantic, bindings);
        const auto sites = weightTagSitesOf(program, core);
        ASSERT_EQ(sites.size(), manifest.size());
        for (size_t index = 0; index < sites.size(); index++) {
            EXPECT_EQ(sites[index].weight_tag_index, index);
            EXPECT_EQ(sites[index].weight_symbol_id,
                      manifest[index].tuple.weight_symbol_id);
            EXPECT_EQ(sites[index].weight_region_offset,
                      manifest[index].tuple.weight_region_offset);
            EXPECT_EQ(sites[index].weight_bytes,
                      manifest[index].tuple.weight_bytes);
            EXPECT_EQ(sites[index].core_id, core);
            const auto &expert =
                program.moe_expert_specs[sites[index].expert_id];
            EXPECT_EQ(expert.core_id, core);
            EXPECT_EQ(expert.weight_region_offset,
                      sites[index].weight_region_offset);
            EXPECT_EQ(expert.weight_bytes, sites[index].weight_bytes);
        }
    }
}

TEST(MeshWeightTagsTest, RuntimeTagSitesDeduplicateAliasedRegions)
{
    DecodedProgram program;
    mesh_abi::MoeExpertSpec first;
    first.expert_id = 0;
    first.core_id = 0;
    first.weight_symbol_id = 4;
    first.weight_region_offset = 0;
    first.weight_bytes = 4096;
    mesh_abi::MoeExpertSpec alias = first;
    alias.expert_id = 1;
    mesh_abi::MoeExpertSpec other = first;
    other.expert_id = 2;
    other.weight_region_offset = 4096;
    mesh_abi::MoeExpertSpec remote = first;
    remote.expert_id = 3;
    remote.core_id = 1;
    program.moe_expert_specs = {first, alias, other, remote};
    const auto sites = weightTagSitesOf(program, 0);
    ASSERT_EQ(sites.size(), 2u);
    EXPECT_EQ(sites[0].weight_tag_index, 0u);
    EXPECT_EQ(sites[0].weight_region_offset, 0ull);
    EXPECT_EQ(sites[0].expert_id, 0);
    EXPECT_EQ(sites[1].weight_tag_index, 1u);
    EXPECT_EQ(sites[1].weight_region_offset, 4096ull);
    const auto remote_sites = weightTagSitesOf(program, 1);
    ASSERT_EQ(remote_sites.size(), 1u);
    EXPECT_EQ(remote_sites[0].weight_tag_index, 0u);
    EXPECT_EQ(remote_sites[0].expert_id, 3);
    EXPECT_EQ(weightTagSitesOf(program, 2).size(), 0u);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

std::array<uint8_t, 32> tokenOfHex(const char *hex)
{
    std::array<uint8_t, 32> out{};
    if (hex == nullptr || std::strlen(hex) != 64)
        return out;
    for (size_t i = 0; i < 32; i++) {
        const auto nibble = [](char c) -> uint8_t {
            return c <= '9' ? uint8_t(c - '0') : uint8_t(c - 'a' + 10);
        };
        out[i] = uint8_t((nibble(hex[2 * i]) << 4) | nibble(hex[2 * i + 1]));
    }
    return out;
}

MoeRngKey rngKeyOf(const golden::RngKeyFields &fields)
{
    MoeRngKey key;
    key.master_seed = fields.master_seed;
    key.workload_plan_digest = hexToDigest(fields.workload_plan_digest_hex);
    key.user_id = fields.user_id;
    key.task_seq = fields.task_seq;
    key.repair_round = fields.repair_round;
    key.phase = fields.phase;
    key.sequence_ordinal = fields.sequence_ordinal;
    key.token_ordinal = fields.token_ordinal;
    key.layer_id = fields.layer_id;
    key.logical_source_rank = fields.logical_source_rank;
    key.topk_slot = fields.topk_slot;
    key.draw_id = fields.draw_id;
    return key;
}

std::string hexOfBytes(const std::array<uint8_t, MoeRngKey::kBytes> &bytes)
{
    static const char digits[] = "0123456789abcdef";
    std::string hex;
    for (uint8_t byte : bytes) {
        hex += digits[byte >> 4];
        hex += digits[byte & 0xF];
    }
    return hex;
}

TEST(MeshMoeRngTest, MatchesCrossLanguageKeyedGolden)
{
    EXPECT_EQ(golden::kRngKeyBytes, MoeRngKey::kBytes);
    ASSERT_GT(golden::kRngVectorCount, 0u);
    for (uint32_t index = 0; index < golden::kRngVectorCount; index++) {
        const auto &vector = golden::kRngVectors[index];
        const MoeRngKey key = rngKeyOf(vector.key);
        EXPECT_EQ(hexOfBytes(key.encode()), std::string(vector.key_hex))
            << vector.name;
        EXPECT_EQ(moeRngSeed64(key), vector.seed64) << vector.name;
        EXPECT_EQ(moeRngDraw(key), vector.splitmix64) << vector.name;
        EXPECT_EQ(moeRngThreshold(vector.total, vector.splitmix64),
                  vector.threshold) << vector.name;
        std::vector<uint64_t> weights(vector.weights,
                                      vector.weights + vector.weight_count);
        uint64_t total = 0;
        for (uint64_t weight : weights)
            total += weight;
        EXPECT_EQ(total, vector.total) << vector.name;
        EXPECT_EQ(moeRngCategorical(weights, key), vector.selected_index)
            << vector.name;
    }
}

TEST(MeshMoeRngTest, MatchesPublishedSplitmixSequence)
{
    EXPECT_EQ(splitmix64Once(0), 0xE220A8397B1DCDAFull);
    EXPECT_EQ(splitmix64Once(1), 0x910A2DEC89025CC1ull);
}

TEST(MeshMoeRngTest, MatchesCrossLanguageWithoutReplacementGolden)
{
    ASSERT_GT(golden::kUniformVectorCount, 0u);
    for (uint32_t index = 0; index < golden::kUniformVectorCount; index++) {
        const auto &vector = golden::kUniformVectors[index];
        const MoeRngKey base = rngKeyOf(vector.key);
        EXPECT_EQ(hexOfBytes(base.encode()), std::string(vector.key_hex))
            << vector.name;
        const auto selected = moeRngWithoutReplacement(
            vector.expert_count, vector.top_k, base);
        ASSERT_EQ(selected.size(), vector.top_k) << vector.name;
        for (uint32_t slot = 0; slot < vector.top_k; slot++)
            EXPECT_EQ(selected[slot], vector.selected[slot]) << vector.name;
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(MeshMoeOverlayTest, MatchesCrossLanguageOverlayGolden)
{
    MoeOverlayGraph graph;
    ASSERT_GT(golden::kGate5OverlayObjectCount, 0u);
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount;
         index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        MoeOverlayEntry entry;
        entry.kind = source.kind;
        entry.region_id = source.region_id;
        entry.ordinal = source.ordinal;
        entry.owner_core = source.owner_core;
        entry.secondary_kind = source.secondary_kind;
        entry.expert_id = source.expert_id;
        entry.src_core = source.src_core;
        entry.dst_core = source.dst_core;
        entry.chunk_ordinal = source.chunk_ordinal;
        entry.phase = source.phase;
        entry.role = source.role;
        entry.access = source.access;
        entry.bytes = source.bytes;
        entry.alignment = source.alignment;
        entry.offset = source.offset;
        entry.ref_region = source.ref_region;
        entry.ref_ordinal = source.ref_ordinal;
        entry.src_view_region = source.src_view_region;
        entry.src_view_ordinal = source.src_view_ordinal;
        entry.dst_view_region = source.dst_view_region;
        entry.dst_view_ordinal = source.dst_view_ordinal;
        entry.backing_kind = source.backing_kind;
        entry.semantic_owner_kind = source.semantic_owner_kind;
        entry.semantic_owner_ref0 = source.semantic_owner_ref0;
        entry.validity_extent = source.validity_extent;
        entry.token = tokenOfHex(source.token_hex);
        entry.wait_count = source.wait_count;
        entry.signal_count = source.signal_count;
        graph.add(entry);
    }
    std::string error;
    EXPECT_TRUE(graph.validate(error)) << error;
    std::vector<uint32_t> pythonOrdinals;
    for (const auto &entry : graph.objects())
        pythonOrdinals.push_back(entry.ordinal);
    graph.assignOrdinals();
    const auto &objects = graph.objects();
    ASSERT_EQ(objects.size(), size_t(golden::kGate5OverlayObjectCount));
    for (size_t index = 0; index < objects.size(); index++)
        EXPECT_EQ(objects[index].ordinal, pythonOrdinals[index])
            << golden::kGate5OverlayObjects[index].kind << " @ "
            << golden::kGate5OverlayObjects[index].region_id;
    std::string secondError;
    EXPECT_TRUE(graph.validate(secondError)) << secondError;
    if (digestToHex(graph.digest()) !=
        std::string(golden::kGate5OverlayDigest)) {
        const auto bytes = graph.wireBytes();
        static const char digits[] = "0123456789abcdef";
        std::string prefix;
        for (size_t i = 0; i < std::min<size_t>(bytes.size(), 122); i++) {
            prefix += digits[bytes[i] >> 4];
            prefix += digits[bytes[i] & 0xF];
        }
        ADD_FAILURE() << "first overlay entry bytes: " << prefix;
    }
    EXPECT_EQ(digestToHex(graph.digest()),
              std::string(golden::kGate5OverlayDigest));
    size_t allocations = 0;
    size_t views = 0;
    size_t commands = 0;
    size_t events = 0;
    size_t descriptors = 0;
    size_t transfers = 0;
    for (const auto &entry : objects) {
        switch (entry.kind) {
        case mesh_abi::kMeshObjectKindALLOCATION: allocations++; break;
        case mesh_abi::kMeshObjectKindVIEW: views++; break;
        case mesh_abi::kMeshObjectKindCOMMAND: commands++; break;
        case mesh_abi::kMeshObjectKindEVENT: events++; break;
        case mesh_abi::kMeshObjectKindDESCRIPTOR: descriptors++; break;
        case mesh_abi::kMeshObjectKindTRANSFER: transfers++; break;
        default: break;
        }
    }
    EXPECT_EQ(allocations, golden::kGate5OverlayAllocations);
    EXPECT_EQ(views, golden::kGate5OverlayViews);
    EXPECT_EQ(commands, golden::kGate5OverlayCommands);
    EXPECT_EQ(events, golden::kGate5OverlayEvents);
    EXPECT_EQ(descriptors, golden::kGate5OverlayDescriptors);
    EXPECT_EQ(transfers, golden::kGate5OverlayTransfers);
}

TEST(MeshMoeOverlayTest, RejectsDuplicateKeysAndDenseOrdinalBreaks)
{
    MoeOverlayEntry first;
    first.kind = mesh_abi::kMeshObjectKindEVENT;
    first.region_id = 1;
    first.ordinal = 1;
    first.role = mesh_abi::kMoeEventRoleREGION_TERMINAL;
    first.wait_count = 1;
    MoeOverlayEntry duplicate = first;
    duplicate.ordinal = 2;
    MoeOverlayGraph graph;
    graph.add(first);
    graph.add(duplicate);
    std::string error;
    EXPECT_FALSE(graph.validate(error));
    EXPECT_EQ(error, "duplicate canonical overlay key");
    MoeOverlayEntry gap = first;
    gap.ordinal = 3;
    MoeOverlayGraph spaced;
    spaced.add(first);
    spaced.add(gap);
    std::string spacedError;
    EXPECT_FALSE(spaced.validate(spacedError));
    MoeOverlayEntry noProducer = first;
    noProducer.wait_count = 0;
    MoeOverlayGraph orphan;
    orphan.add(noProducer);
    std::string orphanError;
    EXPECT_FALSE(orphan.validate(orphanError));
    EXPECT_EQ(orphanError, "overlay event needs exactly one producer");
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

MoeInsertionGate::RegionSpec gateSpec(uint32_t layer_id, uint32_t region_id,
                                      uint32_t insert_after,
                                      uint32_t resume_before,
                                      uint32_t resume_index)
{
    MoeInsertionGate::RegionSpec spec;
    spec.layer_id = layer_id;
    spec.region_id = region_id;
    spec.insert_after_command_id = insert_after;
    spec.resume_before_command_id = resume_before;
    spec.resume_before_index = resume_index;
    return spec;
}

TEST(MoeInsertionGateTest, HoldsTheCursorBetweenInsertAndResume)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(gate.blockedIndex(0));
    EXPECT_FALSE(gate.blockedIndex(3));
    EXPECT_FALSE(gate.blockedIndex(4));
    gate.noteIssued(1);
    gate.noteIssued(4);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::REACHED);
    EXPECT_FALSE(gate.blockedIndex(3));
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.blockedIndex(5));
    EXPECT_TRUE(gate.blockedIndex(9));
    EXPECT_FALSE(gate.allReleased());
    gate.release(1);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::RELEASED);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(9));
    EXPECT_TRUE(gate.allReleased());
}

TEST(MoeInsertionGateTest, KeepsOtherLayersDecodable)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    gate.arm(gateSpec(2, 1, 6, 7, 6));
    EXPECT_FALSE(gate.blockedIndex(3));
    gate.noteIssued(4);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::REACHED);
    EXPECT_EQ(gate.phase(2), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.blockedIndex(6));
    gate.release(1);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(6));
    gate.noteIssued(6);
    EXPECT_TRUE(gate.blockedIndex(6));
    gate.release(2);
    EXPECT_FALSE(gate.blockedIndex(6));
    EXPECT_TRUE(gate.allReleased());
    EXPECT_NE(gate.describe().find("layer=1"), std::string::npos);
}

TEST(MoeInsertionGateTest, IgnoresUnrelatedCommandsUntilTheGateIsReached)
{
    MoeInsertionGate gate;
    gate.arm(gateSpec(1, 1, 4, 5, 4));
    gate.noteIssued(2);
    gate.noteIssued(3);
    EXPECT_EQ(gate.phase(1), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(gate.blockedIndex(4));
    EXPECT_TRUE(gate.layerArmed(1));
    EXPECT_FALSE(gate.layerArmed(2));
    EXPECT_EQ(gate.phase(2), MoeInsertionGate::RegionPhase::IDLE);
    EXPECT_FALSE(gate.allReleased());
    gate.noteIssued(4);
    EXPECT_TRUE(gate.blockedIndex(4));
    EXPECT_FALSE(gate.blockedIndex(3));
    gate.release(1);
    EXPECT_TRUE(gate.allReleased());
    EXPECT_FALSE(gate.blockedIndex(4));
    MoeInsertionGate fresh;
    fresh.arm(gateSpec(3, 2, 8, 9, 8));
    EXPECT_EQ(fresh.phase(3), MoeInsertionGate::RegionPhase::ARMED);
    EXPECT_FALSE(fresh.blockedIndex(8));
    fresh.noteIssued(8);
    EXPECT_TRUE(fresh.blockedIndex(8));
    fresh.release(3);
    EXPECT_FALSE(fresh.blockedIndex(8));
    EXPECT_NE(fresh.describe().find("region=2"), std::string::npos);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

MoeOverlayGraph overlayGoldenGraph()
{
    MoeOverlayGraph graph;
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount; index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        MoeOverlayEntry entry;
        entry.kind = source.kind;
        entry.region_id = source.region_id;
        entry.ordinal = source.ordinal;
        entry.owner_core = source.owner_core;
        entry.secondary_kind = source.secondary_kind;
        entry.expert_id = source.expert_id;
        entry.src_core = source.src_core;
        entry.dst_core = source.dst_core;
        entry.chunk_ordinal = source.chunk_ordinal;
        entry.phase = source.phase;
        entry.role = source.role;
        entry.access = source.access;
        entry.bytes = source.bytes;
        entry.alignment = source.alignment;
        entry.offset = source.offset;
        entry.ref_region = source.ref_region;
        entry.ref_ordinal = source.ref_ordinal;
        entry.backing_kind = source.backing_kind;
        entry.semantic_owner_kind = source.semantic_owner_kind;
        entry.semantic_owner_ref0 = source.semantic_owner_ref0;
        entry.validity_extent = source.validity_extent;
        entry.token = tokenOfHex(source.token_hex);
        entry.wait_count = source.wait_count;
        entry.signal_count = source.signal_count;
        entry.view_count = source.view_count;
        for (uint32_t ref = 0; ref < MoeOverlayEntry::kMaxRefs; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++) {
                entry.wait_refs[ref][field] = source.wait_refs[ref][field];
                entry.signal_refs[ref][field] = source.signal_refs[ref][field];
                entry.view_refs[ref][field] = source.view_refs[ref][field];
            }
        graph.add(entry);
    }
    for (uint32_t index = 0; index < golden::kGate5OverlayPayloadCount;
         index++) {
        const auto &source = golden::kGate5OverlayPayload[index];
        MoeFillPayload payload;
        payload.region_id = source.region_id;
        payload.ordinal = source.ordinal;
        payload.offset = source.offset;
        payload.bytes = source.bytes;
        graph.addPayload(payload);
    }
    if (golden::kGate5OverlayPayloadBytes > 0)
        graph.addPayloadBytes(golden::kGate5OverlayPayloadBlob,
                              golden::kGate5OverlayPayloadBytes);
    graph.setFillMode(golden::kGate5OverlayFillMode);
    for (uint32_t index = 0; index < golden::kGate5OverlayScratchCount;
         index++) {
        const auto &source = golden::kGate5OverlayScratch[index];
        MoeScratchInterval interval;
        interval.region_id = source.region_id;
        interval.ordinal = source.ordinal;
        interval.offset = source.offset;
        interval.bytes = source.bytes;
        graph.addScratch(interval);
    }
    graph.assignOrdinals();
    return graph;
}

MoeOverlayAddressSpace overlayGoldenAddresses(const MoeOverlayGraph &graph)
{
    MoeOverlayAddressSpace addresses;
    uint64_t next = 0x800;
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindVIEW ||
            entry.backing_kind != mesh_abi::kMoeViewBackingSTATIC_ALLOCATION)
            continue;
        auto &interval = addresses.allocations[entry.ref_ordinal];
        interval.second = std::max<uint64_t>(interval.second, entry.bytes);
        if (interval.first == 0) {
            interval.first = next;
            next += ((entry.bytes + 63) / 64) * 64 + 64;
        }
    }
    addresses.hbm_region = 0;
    addresses.sram_region = 0;
    return addresses;
}

TEST(MoeOverlayRuntimeTest, ExecutesTheGoldenOverlayInDependencyOrder)
{
    const MoeOverlayGraph graph = overlayGoldenGraph();
    const MoeOverlayAddressSpace addresses = overlayGoldenAddresses(graph);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read_bytes_per_cycle*/ 32,
                    /*write_bytes_per_cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    sram.addPartition(mesh_abi::SramPartitionKind::RUNTIME_SCRATCH, 0x80000,
                      0x40000, 64, 64);
    MoeOverlayEventBus bus;
    std::vector<std::unique_ptr<MoeOverlayExecutor>> executors;
    std::map<uint16_t, MoeOverlayExecutor *> by_core;
    std::set<uint16_t> overlay_cores;
    for (const auto &entry : graph.objects())
        if (entry.kind == mesh_abi::kMeshObjectKindCOMMAND)
            overlay_cores.insert(uint16_t(entry.owner_core));
    ASSERT_FALSE(overlay_cores.empty());
    for (uint16_t core : overlay_cores) {
        MoeOverlayExecutor::Config config;
        config.core_id = core;
        auto executor = std::make_unique<MoeOverlayExecutor>(
            config, &scoreboard, &sram, &bus, &addresses);
        executor->load(golden::kGate5OverlayLayerId, InstanceGeneration(1),
                       graph);
        bus.subscribe(core, executor.get());
        by_core[core] = executor.get();
        executors.push_back(std::move(executor));
    }
    std::set<uint32_t> region_ids;
    for (const auto &entry : graph.objects())
        if (entry.kind == mesh_abi::kMeshObjectKindCOMMAND &&
            entry.region_id != 0)
            region_ids.insert(entry.region_id);
    ASSERT_FALSE(region_ids.empty());
    for (uint32_t region_id : region_ids) {
        RuntimeObjectKey entry_key;
        entry_key.instance = InstanceGeneration(1);
        entry_key.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
        entry_key.regionGroupId = golden::kGate5OverlayLayerId;
        entry_key.regionId = uint16_t(region_id);
        entry_key.kind = mesh_abi::MeshObjectKind::EVENT;
        entry_key.ordinal = 0;
        for (auto &kv : by_core)
            kv.second->publishEntry(entry_key, 0);
    }
    for (uint32_t cycle = 0; cycle < 2000; cycle++) {
        bool progressed = false;
        uint64_t live_now = 0;
        for (auto &executor : executors) {
            progressed = executor->tick(cycle) || progressed;
            live_now += executor->liveCommands();
        }
        if (!progressed && live_now == 0)
            break;
    }
    for (auto &executor : executors) {
        if (executor->layerId() != golden::kGate5OverlayLayerId)
            continue;
        if (executor->completedCommands() == 0)
            continue;
        EXPECT_TRUE(executor->drained());
        EXPECT_EQ(executor->issuedCommands(), executor->completedCommands());
    }
    const auto &core0 = *by_core.begin()->second;
    uint64_t all_completed = 0;
    uint64_t all_bytes = 0;
    uint64_t all_service = 0;
    for (auto &kv : by_core) {
        all_completed += kv.second->completedCommands();
        all_bytes += kv.second->sramWriteBytes() + kv.second->sramReadBytes();
        all_service += kv.second->sramServiceCycles();
        for (uint32_t region_id : region_ids) {
            const uint64_t bytes =
                kv.second->sramReadBytesForRegion(region_id) +
                kv.second->sramWriteBytesForRegion(region_id);
            const bool region_here = std::any_of(
                graph.objects().begin(), graph.objects().end(),
                [region_id, core = kv.first](const MoeOverlayEntry &entry) {
                    return entry.kind == mesh_abi::kMeshObjectKindCOMMAND &&
                           entry.region_id == region_id &&
                           entry.owner_core == core;
                });
            if (region_here) {
                EXPECT_GT(bytes, 0u)
                    << "region " << region_id << " served no SRAM bytes";
            }
        }
    }
    EXPECT_GT(all_completed, 0u);
    EXPECT_GT(all_bytes, 0u);
    EXPECT_GT(all_service, 0u);
    ASSERT_FALSE(core0.signalled().empty());
    bool saw_group_exit = false;
    for (const auto &event : core0.signalled())
        if (event.regionId == 0)
            saw_group_exit = true;
    EXPECT_TRUE(saw_group_exit);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

TEST(MoeOverlayImageTest, CachedWeightViewsCarryTheRuntimeTagIndices)
{
    const MeshBytes program_image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb");
    ASSERT_FALSE(program_image.empty());
    DecodedProgram program;
    MeshLoadError load_error;
    ASSERT_TRUE(decodeMeshBinary(program_image, program, load_error))
        << load_error.code << ": " << load_error.message;
    const MeshBytes image = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate5/moe_dual_overlay_cached.bin");
    ASSERT_FALSE(image.empty());
    MoeOverlayImage codec;
    MoeOverlayGraph graph;
    uint32_t layer_id = 0;
    std::string error;
    ASSERT_TRUE(codec.decode(image, graph, layer_id, error)) << error;
    uint32_t weight_views = 0;
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindVIEW ||
            entry.secondary_kind != mesh_abi::kMoeViewKindWEIGHT)
            continue;
        EXPECT_EQ(entry.backing_kind,
                  mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT);
        EXPECT_EQ(entry.semantic_owner_kind, mesh_abi::kMoeSemanticOwnerEXPERT);
        bool found_expert = false;
        for (const auto &expert : program.moe_expert_specs) {
            if (expert.layer_id != layer_id ||
                expert.expert_id != entry.semantic_owner_ref0)
                continue;
            found_expert = true;
            EXPECT_EQ(entry.owner_core, expert.core_id);
            EXPECT_EQ(entry.bytes, expert.weight_bytes);
            bool matched = false;
            for (const auto &site :
                 weightTagSitesOf(program, expert.core_id))
                if (site.weight_symbol_id == expert.weight_symbol_id &&
                    site.weight_region_offset == expert.weight_region_offset &&
                    site.weight_bytes == expert.weight_bytes) {
                    EXPECT_EQ(entry.ref_ordinal, site.weight_tag_index);
                    matched = true;
                }
            EXPECT_TRUE(matched) << "expert " << expert.expert_id
                                 << " of layer " << layer_id
                                 << " has no runtime tag site";
        }
        EXPECT_TRUE(found_expert);
        weight_views++;
    }
    EXPECT_EQ(weight_views, 2u);
    EXPECT_TRUE(std::none_of(
        graph.objects().begin(), graph.objects().end(),
        [](const MoeOverlayEntry &entry) {
            return entry.kind == mesh_abi::kMeshObjectKindDESCRIPTOR &&
                   entry.secondary_kind ==
                       mesh_abi::kMoeDescriptorKindSTREAMED_WEIGHT;
        }));
}

TEST(MoeOverlayImageTest, DecodesTheRuntimeImageIntoTheCanonicalGraph)
{
    const MeshBytes image = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate5/moe_overlay_objects.bin");
    ASSERT_FALSE(image.empty());
    MoeOverlayImage codec;
    MoeOverlayGraph graph;
    uint32_t layer_id = 0;
    std::string error;
    ASSERT_TRUE(codec.decode(image, graph, layer_id, error)) << error;
    EXPECT_EQ(layer_id, golden::kGate5OverlayLayerId);
    EXPECT_EQ(graph.objects().size(), size_t(golden::kGate5OverlayObjectCount));
    std::string validate_error;
    EXPECT_TRUE(graph.validate(validate_error)) << validate_error;
    EXPECT_EQ(digestToHex(graph.digest()),
              std::string(golden::kGate5OverlayDigest));
    const auto &scratch = graph.scratch();
    ASSERT_EQ(scratch.size(), size_t(golden::kGate5OverlayScratchCount));
    for (size_t index = 0; index < scratch.size(); index++) {
        const auto &interval = scratch[index];
        const auto &expected = golden::kGate5OverlayScratch[index];
        EXPECT_EQ(interval.region_id, expected.region_id);
        EXPECT_EQ(interval.ordinal, expected.ordinal);
        EXPECT_EQ(interval.offset, expected.offset);
        EXPECT_EQ(interval.bytes, expected.bytes);
    }
    for (const auto &entry : graph.objects()) {
        if (entry.kind != mesh_abi::kMeshObjectKindALLOCATION)
            continue;
        const auto *interval =
            graph.resolveAllocation(entry.region_id, entry.ordinal);
        ASSERT_NE(interval, nullptr);
        EXPECT_EQ(interval->bytes, entry.bytes);
    }
    std::map<std::tuple<uint32_t, uint32_t, uint32_t>,
             const golden::OverlayObjectEntry *>
        golden_by_key;
    uint32_t golden_views = 0;
    for (uint32_t index = 0; index < golden::kGate5OverlayObjectCount; index++) {
        const auto &source = golden::kGate5OverlayObjects[index];
        golden_by_key[{uint32_t(source.kind), source.region_id,
                       source.ordinal}] = &source;
        golden_views += source.view_count;
    }
    uint32_t decoded_views = 0;
    for (const auto &entry : graph.objects()) {
        const auto it = golden_by_key.find(
            {uint32_t(entry.kind), entry.region_id, entry.ordinal});
        ASSERT_NE(it, golden_by_key.end());
        const auto &source = *it->second;
        EXPECT_EQ(entry.src_view_region, source.src_view_region);
        EXPECT_EQ(entry.src_view_ordinal, source.src_view_ordinal);
        EXPECT_EQ(entry.dst_view_region, source.dst_view_region);
        EXPECT_EQ(entry.dst_view_ordinal, source.dst_view_ordinal);
        EXPECT_EQ(entry.view_count, source.view_count);
        for (uint32_t ref = 0; ref < MoeOverlayEntry::kMaxRefs; ref++)
            for (uint32_t field = 0; field < MoeOverlayEntry::kRefFields;
                 field++)
                EXPECT_EQ(entry.view_refs[ref][field],
                          source.view_refs[ref][field]);
        decoded_views += entry.view_count;
    }
    EXPECT_GT(decoded_views, 0u);
    EXPECT_EQ(decoded_views, golden_views);
    const auto round_trip = codec.encode(graph, layer_id);
    EXPECT_EQ(round_trip, image);
    const MeshBytes truncated(image.begin(), image.end() - 1);
    MoeOverlayGraph rejected;
    uint32_t rejected_layer = 0;
    std::string truncated_error;
    EXPECT_FALSE(codec.decode(truncated, rejected, rejected_layer,
                              truncated_error));
    EXPECT_TRUE(truncated_error ==
                    "overlay image size does not match its object count" ||
                truncated_error == "overlay payload escapes its blob")
        << truncated_error;
    MeshBytes wrong_magic = image;
    wrong_magic[0] ^= 0xFF;
    MoeOverlayGraph other;
    uint32_t other_layer = 0;
    std::string magic_error;
    EXPECT_FALSE(codec.decode(wrong_magic, other, other_layer, magic_error));
    EXPECT_EQ(magic_error, "overlay image magic mismatch");
}

class StubOverlayDmaPort : public MoeOverlayDmaPort
{
  public:
    bool submitOverlayDma(const mesh_abi::DmaDescriptor &descriptor,
                          const RuntimeObjectKey &descriptor_key,
                          const RuntimeObjectKey &command_key,
                          uint64_t issue_tick) override
    {
        submissions.push_back(command_key);
        issue_ticks.push_back(issue_tick);
        if (refusals > 0) {
            --refusals;
            return false;
        }
        ++accepted;
        return true;
    }

    void bindFillPattern(const RuntimeObjectKey &command,
                         uint64_t pattern) override
    {
        patterns[command] = pattern;
    }

    void bindFillContent(const RuntimeObjectKey &command,
                         const std::vector<uint8_t> &content,
                         bool install_bytes) override
    {
        contents[command] = content.size();
        installed[command] = install_bytes;
    }

    uint32_t refusals = 0;
    uint32_t accepted = 0;
    std::vector<RuntimeObjectKey> submissions;
    std::vector<uint64_t> issue_ticks;
    std::map<RuntimeObjectKey, uint64_t> patterns;
    std::map<RuntimeObjectKey, size_t> contents;
    std::map<RuntimeObjectKey, bool> installed;
};

MoeOverlayGraph fillOverlayGraph()
{
    MoeOverlayGraph graph;
    MoeOverlayEntry allocation;
    allocation.kind = mesh_abi::kMeshObjectKindALLOCATION;
    allocation.region_id = 1;
    allocation.ordinal = 1;
    allocation.bytes = 64;
    allocation.alignment = 64;
    allocation.validity_extent = 64;
    graph.add(allocation);
    MoeOverlayEntry view;
    view.kind = mesh_abi::kMeshObjectKindVIEW;
    view.region_id = 1;
    view.ordinal = 1;
    view.owner_core = 0;
    view.secondary_kind = mesh_abi::kMoeViewKindPAD_BUFFER;
    view.access = mesh_abi::kMoeViewAccessWRITE;
    view.bytes = 64;
    view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    view.ref_region = 1;
    view.ref_ordinal = 1;
    graph.add(view);
    MoeOverlayEntry descriptor;
    descriptor.kind = mesh_abi::kMeshObjectKindDESCRIPTOR;
    descriptor.region_id = 1;
    descriptor.ordinal = 1;
    descriptor.secondary_kind = mesh_abi::kMoeDescriptorKindPAD_FILL;
    descriptor.bytes = 64;
    descriptor.dst_view_region = 1;
    descriptor.dst_view_ordinal = 1;
    graph.add(descriptor);
    MoeOverlayEntry command;
    command.kind = mesh_abi::kMeshObjectKindCOMMAND;
    command.region_id = 1;
    command.ordinal = 1;
    command.owner_core = 0;
    command.secondary_kind = mesh_abi::kOpcodeDMA_FILL;
    command.role = mesh_abi::kMoeCommandRolePAD_FILL;
    command.bytes = 64;
    command.ref_region = 1;
    command.ref_ordinal = 1;
    command.wait_count = 1;
    command.wait_refs[0][0] = 1;
    command.wait_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.wait_refs[0][2] = 0;
    command.signal_count = 1;
    command.signal_refs[0][0] = 1;
    command.signal_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.signal_refs[0][2] = 1;
    graph.add(command);
    MoeOverlayEntry event;
    event.kind = mesh_abi::kMeshObjectKindEVENT;
    event.region_id = 1;
    event.ordinal = 1;
    graph.add(event);
    MoeFillPayload payload;
    payload.region_id = 1;
    payload.ordinal = 1;
    payload.bytes = 64;
    graph.addPayload(payload);
    const std::vector<uint8_t> content(64, 0x5A);
    graph.addPayloadBytes(content.data(), content.size());
    return graph;
}

TEST(MoeOverlayRuntimeTest, RetriesBackpressuredDmaUntilTheEngineAccepts)
{
    const MoeOverlayGraph graph = fillOverlayGraph();
    MoeScratchInterval interval;
    interval.region_id = 1;
    interval.ordinal = 1;
    interval.offset = 0x1000;
    interval.bytes = 64;
    MoeOverlayGraph with_scratch = graph;
    with_scratch.addScratch(interval);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 16, /*alignment*/ 64,
                    /*line_bytes*/ 32, /*read_bytes_per_cycle*/ 32,
                    /*write_bytes_per_cycle*/ 32, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayAddressSpace addresses;
    addresses.sram_region = 0;
    addresses.hbm_region = 0;
    addresses.max_burst_beats = 16;
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr,
                                &addresses);
    executor.load(1, InstanceGeneration(1), with_scratch);
    StubOverlayDmaPort port;
    port.refusals = 2;
    executor.bindDma(&port);
    RuntimeObjectKey entry;
    entry.instance = InstanceGeneration(1);
    entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    entry.regionGroupId = 1;
    entry.regionId = 1;
    entry.kind = mesh_abi::MeshObjectKind::EVENT;
    entry.ordinal = 0;
    executor.publishEntry(entry, 0);
    for (uint32_t cycle = 0; cycle < 8 && port.accepted == 0; cycle++)
        executor.tick(cycle);
    EXPECT_EQ(port.submissions.size(), 3u);
    EXPECT_EQ(port.accepted, 1u);
    EXPECT_EQ(executor.completedCommands(), 0u);
    EXPECT_GT(port.issue_ticks[2], port.issue_ticks[0]);
    EXPECT_EQ(port.contents.count(port.submissions[0]), 1u);
    EXPECT_TRUE(port.installed[port.submissions[0]]);
    EXPECT_FALSE(executor.drained());
    executor.completeDma(port.submissions[2], 0);
    for (uint32_t cycle = 0; cycle < 4 && !executor.drained(); cycle++)
        executor.tick(cycle);
    EXPECT_TRUE(executor.drained());
    EXPECT_EQ(executor.completedCommands(), 1u);
    EXPECT_GT(executor.sramWriteBytes(), 0u);
}

class StubOverlayComputePort : public MoeOverlayComputePort
{
  public:
    uint64_t computeCycles(const MoeComputeShape &shape) const override
    {
        shapes.push_back(shape);
        return cycles;
    }
    bool weightSlotRange(uint32_t, uint64_t &, uint64_t &) const override
    {
        return false;
    }
    bool computeAdmissible(uint16_t) const override { return true; }
    void noteComputeAdmitted(uint16_t opcode) override
    {
        admitted.push_back(opcode);
    }
    void noteComputeFinished(uint16_t opcode, uint64_t charged_cycles) override
    {
        finished.push_back(opcode);
        charged.push_back(charged_cycles);
    }
    void noteWeightConsumed(uint32_t, uint32_t) override {}

    mutable std::vector<MoeComputeShape> shapes;
    std::vector<uint16_t> admitted;
    std::vector<uint16_t> finished;
    std::vector<uint64_t> charged;
    uint64_t cycles = 10;
};

MoeOverlayGraph combineOverlayGraph(uint32_t contributors)
{
    MoeOverlayGraph graph;
    MoeScratchInterval read_interval;
    read_interval.region_id = 1;
    read_interval.ordinal = 1;
    read_interval.offset = 0x100;
    read_interval.bytes = 16;
    graph.addScratch(read_interval);
    MoeScratchInterval write_interval;
    write_interval.region_id = 1;
    write_interval.ordinal = 2;
    write_interval.offset = 0x200;
    write_interval.bytes = 16;
    graph.addScratch(write_interval);
    MoeOverlayEntry read_view;
    read_view.kind = mesh_abi::kMeshObjectKindVIEW;
    read_view.region_id = 1;
    read_view.ordinal = 1;
    read_view.owner_core = 0;
    read_view.secondary_kind = mesh_abi::kMoeViewKindEXPERT_OUTPUT;
    read_view.access = mesh_abi::kMoeViewAccessREAD;
    read_view.bytes = 16;
    read_view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    read_view.ref_region = 1;
    read_view.ref_ordinal = 1;
    graph.add(read_view);
    MoeOverlayEntry write_view;
    write_view.kind = mesh_abi::kMeshObjectKindVIEW;
    write_view.region_id = 1;
    write_view.ordinal = 2;
    write_view.owner_core = 0;
    write_view.secondary_kind = mesh_abi::kMoeViewKindMEMBER_OUTPUT;
    write_view.access = mesh_abi::kMoeViewAccessWRITE;
    write_view.bytes = 16;
    write_view.backing_kind = mesh_abi::kMoeViewBackingOVERLAY_ALLOCATION;
    write_view.ref_region = 1;
    write_view.ref_ordinal = 2;
    graph.add(write_view);
    MoeOverlayEntry command;
    command.kind = mesh_abi::kMeshObjectKindCOMMAND;
    command.region_id = 1;
    command.ordinal = 1;
    command.owner_core = 0;
    command.secondary_kind = mesh_abi::kOpcodeLOCAL_REDUCE;
    command.role = contributors == 1 ? mesh_abi::kMoeCommandRoleCOPY_THROUGH
                                     : mesh_abi::kMoeCommandRoleLOCAL_REDUCE;
    command.bytes = 16;
    command.wait_count = 1 + contributors;
    command.wait_refs[0][0] = 1;
    command.wait_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.wait_refs[0][2] = 0;
    for (uint32_t index = 0; index < contributors; index++) {
        command.wait_refs[index + 1][0] = 1;
        command.wait_refs[index + 1][1] = mesh_abi::kMeshObjectKindEVENT;
        command.wait_refs[index + 1][2] = 10 + index;
    }
    command.signal_count = 1;
    command.signal_refs[0][0] = 1;
    command.signal_refs[0][1] = mesh_abi::kMeshObjectKindEVENT;
    command.signal_refs[0][2] = 1;
    command.view_count = 2;
    command.view_refs[0][0] = 1;
    command.view_refs[0][1] = mesh_abi::kMeshObjectKindVIEW;
    command.view_refs[0][2] = 1;
    command.view_refs[1][0] = 1;
    command.view_refs[1][1] = mesh_abi::kMeshObjectKindVIEW;
    command.view_refs[1][2] = 2;
    graph.add(command);
    for (uint32_t index = 0; index < contributors; index++) {
        MoeOverlayEntry event;
        event.kind = mesh_abi::kMeshObjectKindEVENT;
        event.region_id = 1;
        event.ordinal = 10 + index;
        graph.add(event);
    }
    MoeOverlayEntry result;
    result.kind = mesh_abi::kMeshObjectKindEVENT;
    result.region_id = 1;
    result.ordinal = 1;
    graph.add(result);
    return graph;
}

uint64_t runCombineStages(const MoeOverlayGraph &graph, uint32_t contributors,
                          StubOverlayComputePort &port,
                          MoeOverlayExecutor &executor)
{
    RuntimeObjectKey entry;
    entry.instance = InstanceGeneration(1);
    entry.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    entry.regionGroupId = 1;
    entry.regionId = 1;
    entry.kind = mesh_abi::MeshObjectKind::EVENT;
    entry.ordinal = 0;
    executor.publishEntry(entry, 1000);
    for (uint32_t index = 0; index < contributors; index++) {
        RuntimeObjectKey contributor = entry;
        contributor.ordinal = 10 + index;
        executor.deliverEvent(contributor);
    }
    uint64_t completed_at = 0;
    for (uint64_t tick = 1001; tick < 1200; tick++) {
        executor.tick(tick);
        if (executor.completedCommands() != 0) {
            completed_at = tick;
            break;
        }
    }
    return completed_at;
}

TEST(MoeOverlayRuntimeTest, ComputeStagesGateCompletionOnAbsoluteTicks)
{
    const MoeOverlayGraph graph = combineOverlayGraph(1);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 1, /*alignment*/ 8,
                    /*line_bytes*/ 8, /*read_bytes_per_cycle*/ 8,
                    /*write_bytes_per_cycle*/ 8, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    config.ticks_per_cycle = 1;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr, nullptr);
    executor.load(1, InstanceGeneration(1), graph);
    StubOverlayComputePort port;
    port.cycles = 10;
    std::vector<ComputeDigest> digests;
    executor.bindCompute(&port);
    executor.bindComputeCommit(&digests);
    const uint8_t source[16] = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                                0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E,
                                0x0F, 0x10};
    ASSERT_TRUE(sram.write(0x100, sizeof(source), source));
    const uint64_t completed_at = runCombineStages(graph, 1, port, executor);
    // Reads stall two ticks, the engine window is ten cycles and the
    // two-line output write stalls two more: the write tick retires it.
    EXPECT_EQ(completed_at, 1016u);
    ASSERT_EQ(port.shapes.size(), 1u);
    EXPECT_EQ(port.shapes[0].role, mesh_abi::kMoeCommandRoleCOPY_THROUGH);
    EXPECT_EQ(port.shapes[0].fan_in, 1u);
    EXPECT_EQ(port.shapes[0].payload_bytes, 16u);
    EXPECT_EQ(executor.sramReadBytes(), 16u);
    EXPECT_EQ(executor.sramWriteBytes(), 16u);
    EXPECT_EQ(executor.computeCommits(), 1u);
    EXPECT_EQ(executor.uncommittedViews(), 0u);
    ASSERT_EQ(digests.size(), 1u);
    EXPECT_EQ(digests[0].offset, 0x200u);
    uint8_t result[16] = {};
    ASSERT_TRUE(sram.read(0x200, sizeof(result), result));
    EXPECT_TRUE(std::equal(source, source + 16, result));
    uint8_t commit_snapshot[16];
    std::copy(result, result + 16, commit_snapshot);
    executor.tick(1100);
    uint8_t after[16] = {};
    ASSERT_TRUE(sram.read(0x200, sizeof(after), after));
    EXPECT_TRUE(std::equal(commit_snapshot, commit_snapshot + 16, after));
}

TEST(MoeOverlayRuntimeTest, ComputeEngineWindowScalesWithKernelCycles)
{
    const MoeOverlayGraph graph = combineOverlayGraph(2);
    ProgramScoreboard scoreboard;
    TensorSram sram(/*bytes*/ 1 << 20, /*banks*/ 1, /*alignment*/ 8,
                    /*line_bytes*/ 8, /*read_bytes_per_cycle*/ 8,
                    /*write_bytes_per_cycle*/ 8, /*read_ports*/ 1,
                    /*write_ports*/ 1, /*line_tick*/ 1);
    MoeOverlayExecutor::Config config;
    config.core_id = 0;
    config.ticks_per_cycle = 1;
    MoeOverlayExecutor executor(config, &scoreboard, &sram, nullptr, nullptr);
    executor.load(1, InstanceGeneration(1), graph);
    StubOverlayComputePort port;
    port.cycles = 30;
    std::vector<ComputeDigest> digests;
    executor.bindCompute(&port);
    executor.bindComputeCommit(&digests);
    const uint64_t completed_at = runCombineStages(graph, 2, port, executor);
    EXPECT_EQ(completed_at, 1036u);
    ASSERT_EQ(port.shapes.size(), 1u);
    EXPECT_EQ(port.shapes[0].role, mesh_abi::kMoeCommandRoleLOCAL_REDUCE);
    EXPECT_EQ(port.shapes[0].fan_in, 2u);
    ASSERT_EQ(port.charged.size(), 1u);
    EXPECT_EQ(port.charged[0], 30u);
    EXPECT_EQ(executor.computeCyclesTotal(), 30u);
    EXPECT_EQ(executor.localReduceCommands(), 1u);
}

TEST(MeshBinaryServingTest, LoadsCrossLanguageServingFixture)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.abi_major, 1u);
    EXPECT_EQ(program.abi_minor, 3u);
    EXPECT_EQ(program.required_features,
              kFeatureAgentServingV1 | kFeatureProfileScopedExecutionV1);
    EXPECT_TRUE(program.has_serving_v1);
    EXPECT_TRUE(program.has_profile_scoped_execution_v1);
    EXPECT_FALSE(program.has_moe_v1);
    ASSERT_EQ(program.agent_request_profiles.size(), 1u);
    ASSERT_EQ(program.agent_instance_profiles.size(), 3u);
    ASSERT_EQ(program.agent_source_core_map.size(), 1u);
    ASSERT_EQ(program.agent_instance_member_bindings.size(), 3u);
    ASSERT_EQ(program.agent_request_binding_requirements.size(), 4u);
    ASSERT_EQ(program.agent_publish_surrogate_bindings.size(), 1u);
    const auto &request = program.agent_request_profiles[0];
    EXPECT_EQ(request.program_id, 1u);
    EXPECT_EQ(request.profile_id, 1u);
    EXPECT_EQ(request.flags, kAgentRequestFlagsHAS_KV);
    EXPECT_EQ(request.requested_profile_key, 0x1e7508bdba23c5f6ull);
    EXPECT_EQ(request.path_mask, 0x1u);
    EXPECT_EQ(request.publish_chunk_bytes, 64u);
    const auto &prefill = program.agent_instance_profiles[0];
    const auto &decode = program.agent_instance_profiles[1];
    const auto &publish = program.agent_instance_profiles[2];
    EXPECT_EQ(prefill.phase, kPhasePREFILL);
    EXPECT_EQ(decode.phase, kPhaseDECODE);
    EXPECT_EQ(publish.phase, kPhasePUBLISH);
    EXPECT_EQ(prefill.decode_chunk_tokens, 0u);
    EXPECT_EQ(decode.decode_chunk_tokens, 1u);
    EXPECT_EQ(publish.host_input_dma_bytes_per_member, 0ull);
    EXPECT_EQ(publish.host_output_dma_bytes_per_member, 128ull);
    EXPECT_EQ(publish.kv_read_bytes_per_member, 0ull);
    EXPECT_EQ(prefill.kv_write_bytes_per_member, 128ull);
    const auto &binding = program.agent_publish_surrogate_bindings[0];
    EXPECT_EQ(binding.fill_kind, kDmaFillKindAGENT_OUTPUT_SURROGATE);
    EXPECT_EQ(binding.allocation_role,
              kAgentAllocationRolePUBLISH_SURROGATE_SOURCE);
    EXPECT_EQ(binding.digest_source,
              kAgentDigestSourceREQUEST_SEMANTIC_OUTPUT_DIGEST);
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_TRUE(verifyDecodedProgram(program, arch, error))
        << error.code << ": " << error.message;
    ServingCapacityRequirement required;
    ASSERT_TRUE(servingCapacityRequired(program, required, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(required.batch_weight_binding_entries, 1u);
    EXPECT_EQ(required.instance_member_binding_entries, 2u);
    EXPECT_EQ(required.batch_interval_entries, 5u);
}

TEST(MeshBinaryServingTest, LoadsTwoTokenServingFixture)
{
    using namespace mesh_abi;
    const MeshBytes image = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_TRUE(program.has_serving_v1);
    ASSERT_EQ(program.agent_request_profiles.size(), 1u);
    ASSERT_EQ(program.agent_instance_profiles.size(), 4u);
    ASSERT_EQ(program.agent_instance_member_bindings.size(), 4u);
    ASSERT_EQ(program.agent_publish_surrogate_bindings.size(), 1u);
    const auto &request = program.agent_request_profiles[0];
    EXPECT_EQ(request.output_tokens, 2u);
    EXPECT_EQ(request.host_output_bytes, 256ull);
    const auto &prefill = program.agent_instance_profiles[0];
    const auto &decode0 = program.agent_instance_profiles[1];
    const auto &decode1 = program.agent_instance_profiles[2];
    const auto &publish = program.agent_instance_profiles[3];
    EXPECT_EQ(prefill.instance_profile_id, 11u);
    EXPECT_EQ(decode0.instance_profile_id, 12u);
    EXPECT_EQ(decode1.instance_profile_id, 13u);
    EXPECT_EQ(publish.instance_profile_id, 14u);
    EXPECT_EQ(prefill.phase, kPhasePREFILL);
    EXPECT_EQ(decode0.phase, kPhaseDECODE);
    EXPECT_EQ(decode1.phase, kPhaseDECODE);
    EXPECT_EQ(publish.phase, kPhasePUBLISH);
    EXPECT_EQ(decode0.kv_tokens_before, 8u);
    EXPECT_EQ(decode1.kv_tokens_before, 9u);
    EXPECT_EQ(decode0.kv_read_bytes_per_member, 128ull);
    EXPECT_EQ(decode1.kv_read_bytes_per_member, 144ull);
    EXPECT_EQ(decode1.kv_write_bytes_per_member, 16ull);
    EXPECT_EQ(publish.kv_tokens_before, 10u);
    EXPECT_EQ(publish.kv_read_bytes_per_member, 0ull);
    EXPECT_EQ(publish.kv_write_bytes_per_member, 0ull);
    EXPECT_EQ(publish.host_output_dma_bytes_per_member, 256ull);
    EXPECT_NE(decode0.mesh_profile_id, decode1.mesh_profile_id);
    const auto &binding = program.agent_publish_surrogate_bindings[0];
    EXPECT_EQ(binding.instance_profile_id, 14u);
    EXPECT_EQ(binding.fill_kind, kDmaFillKindAGENT_OUTPUT_SURROGATE);
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_TRUE(verifyDecodedProgram(program, arch, error))
        << error.code << ": " << error.message;
}

std::string hexDigest(const std::array<uint8_t, 32> &digest)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

TEST(MeshBinaryServingTest, ServingDetailCodesHaveFixedDispositions)
{
    using namespace ::gem5::ai_mesh::agent_abi;
    const auto param = detailDispositionV1(E_BINDING_ROLE);
    ASSERT_TRUE(param.has_value());
    EXPECT_EQ(param->severity, DetailSeverityV1::REQUEST_RECOVERABLE);
    EXPECT_EQ(param->cqStatus, static_cast<int32_t>(CqStatus::PARAM_ERROR));
    const auto chunk = detailDispositionV1(E_OUTPUT_CHUNK_MISMATCH);
    ASSERT_TRUE(chunk.has_value());
    EXPECT_EQ(chunk->cqStatus, static_cast<int32_t>(CqStatus::PARAM_ERROR));
    const auto profile = detailDispositionV1(E_REQUEST_PROFILE);
    ASSERT_TRUE(profile.has_value());
    EXPECT_EQ(profile->cqStatus,
              static_cast<int32_t>(CqStatus::PROFILE_ERROR));
    const auto key = detailDispositionV1(E_REQUEST_PROFILE_KEY);
    ASSERT_TRUE(key.has_value());
    EXPECT_EQ(key->cqStatus, static_cast<int32_t>(CqStatus::PROFILE_ERROR));
    const auto host = detailDispositionV1(E_HOST_IO_SIZE_MISMATCH);
    ASSERT_TRUE(host.has_value());
    EXPECT_EQ(host->cqStatus, static_cast<int32_t>(CqStatus::PROFILE_ERROR));
    const auto kv = detailDispositionV1(E_KV_TOKEN_MISMATCH);
    ASSERT_TRUE(kv.has_value());
    EXPECT_EQ(kv->cqStatus, static_cast<int32_t>(CqStatus::PROFILE_ERROR));
    const auto capacity = detailDispositionV1(E_CAPACITY_PLAN);
    ASSERT_TRUE(capacity.has_value());
    EXPECT_EQ(capacity->severity, DetailSeverityV1::CONFIG_FATAL);
}

TEST(MeshBinaryServingTest, JsonEscapingMatchesPython)
{
    const std::string raw = "a\"b\\c\n\t\xc3\xa9\xe4\xb8\xad"
                            "\xf0\x9f\x98\x80\x01";
    std::string out;
    mesh_abi::jsonString(out, raw.data(), raw.size());
    EXPECT_EQ(out,
              "\"a\\\"b\\\\c\\n\\t\\u00e9\\u4e2d"
              "\\ud83d\\ude00\\u0001\"");
}

TEST(MeshBinaryServingTest, MatchesThePythonProfileKeyGolden)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(hexDigest(programProfileKeyBaseDigest(program)),
              "32f907bb2496112a2485a2a3a24598a6"
              "ee06a61e6c796328dedd86ab4cba596f");
    EXPECT_EQ(hexDigest(programSemanticDigest(program)),
              "4516eb428ab47e038ba71c5e5baea8d0"
              "8a731b5f70358b327fe6cd6c46e91d04");
    EXPECT_TRUE(verifyRequestProfileKeys(program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.agent_request_profiles[0].requested_profile_key,
              0x1e7508bdba23c5f6ull);
}

TEST(MeshBinaryServingTest, RejectsATamperedProfileKey)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    const size_t request =
        sectionOffset(golden, kSectionTypeAGENT_REQUEST_PROFILES);
    writeField(image,
               request + kAgentRequestProfilesRequestedProfileKeyOffset, 8, 1);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_FALSE(verifyDecodedProgram(program, arch, error));
    EXPECT_EQ(error.code, "E_REQUEST_PROFILE_KEY");
}

TEST(MeshBinaryFullViewTest, LoadsCrossLanguageFullViewFixture)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_TRUE(verifyDecodedProgram(program, arch, error))
        << error.code << ": " << error.message;
    ASSERT_EQ(program.agent_instance_profiles.size(), 6u);
    const AgentInstanceProfile *dual = nullptr;
    for (const auto &instance : program.agent_instance_profiles)
        if (instance.instance_profile_id == 111)
            dual = &instance;
    ASSERT_NE(dual, nullptr);
    EXPECT_EQ(dual->member_count, 2u);
    EXPECT_EQ(dual->member_binding_count, 2u);
    ASSERT_EQ(program.agent_publish_surrogate_bindings.size(), 3u);
    uint32_t allocations = 0;
    for (const auto &record : program.agent_publish_surrogate_bindings)
        if (record.instance_profile_id == 113)
            allocations++;
    EXPECT_EQ(allocations, 2u);
}

TEST(MeshBinaryFullViewTest, RejectsAliasedMemberSlot)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t relocations =
        sectionOffset(golden, kSectionTypeRELOCATIONS);
    MeshBytes image = golden;
    writeField(image, relocations + 7 * kRelocationsBytes +
                  kRelocationsOffsetBytesOffset, 8, 1048576);
    refreshServingChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_FALSE(verifyDecodedProgram(program, arch, error));
    EXPECT_EQ(error.code, "E_BINDING_ROLE");
}

TEST(MeshBinaryFullViewTest, RejectsCrossedPublishBinding)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t publish =
        sectionOffset(golden, kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS);
    MeshBytes image = golden;
    const size_t second = publish + kAgentPublishSurrogateBindingsBytes;
    const size_t third = publish + 2 * kAgentPublishSurrogateBindingsBytes;
    for (const auto &field : {std::make_pair(
             kAgentPublishSurrogateBindingsAllocationIdOffset, 4u),
         std::make_pair(
             kAgentPublishSurrogateBindingsProducerCommandIdOffset, 4u),
         std::make_pair(
             kAgentPublishSurrogateBindingsCompletionEventIdOffset, 4u)}) {
        const uint32_t left = rd32(image, second + field.first);
        const uint32_t right = rd32(image, third + field.first);
        writeField(image, second + field.first, field.second, right);
        writeField(image, third + field.first, field.second, left);
    }
    refreshServingChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    ASSERT_EQ(program.agent_publish_surrogate_bindings.size(), 3u);
    const AgentPublishSurrogateBinding &first_record =
        program.agent_publish_surrogate_bindings[1];
    const AgentPublishSurrogateBinding &second_record =
        program.agent_publish_surrogate_bindings[2];
    EXPECT_EQ(first_record.instance_profile_id, 113u);
    EXPECT_EQ(second_record.instance_profile_id, 113u);
    EXPECT_EQ(first_record.member_ordinal, 0u);
    EXPECT_EQ(second_record.member_ordinal, 1u);
    EXPECT_NE(first_record.allocation_id, second_record.allocation_id);
    EXPECT_EQ(first_record.allocation_id,
              rd32(golden, third +
                   kAgentPublishSurrogateBindingsAllocationIdOffset));
    EXPECT_EQ(first_record.producer_command_id,
              rd32(golden, third +
                   kAgentPublishSurrogateBindingsProducerCommandIdOffset));
    EXPECT_EQ(first_record.completion_event_id,
              rd32(golden, third +
                   kAgentPublishSurrogateBindingsCompletionEventIdOffset));
    EXPECT_EQ(second_record.allocation_id,
              rd32(golden, second +
                   kAgentPublishSurrogateBindingsAllocationIdOffset));
    EXPECT_EQ(second_record.producer_command_id,
              rd32(golden, second +
                   kAgentPublishSurrogateBindingsProducerCommandIdOffset));
    EXPECT_EQ(second_record.completion_event_id,
              rd32(golden, second +
                   kAgentPublishSurrogateBindingsCompletionEventIdOffset));
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_FALSE(verifyDecodedProgram(program, arch, error));
    EXPECT_EQ(error.code, "E_BINDING_ROLE");
    EXPECT_EQ(error.message,
              "publish store must read the bound surrogate allocation");
}

TEST(MeshBinaryServingTest, LoadsProfileStreamRangesFixture)
{
    using namespace mesh_abi;
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_TRUE(program.has_profile_scoped_execution_v1);
    ASSERT_EQ(program.profile_stream_ranges.size(), 3u);
    const uint32_t begins[] = {0u, 8u, 14u};
    const uint32_t counts[] = {8u, 6u, 6u};
    for (size_t i = 0; i < program.profile_stream_ranges.size(); i++) {
        const auto &range = program.profile_stream_ranges[i];
        EXPECT_EQ(range.profile_id, i + 1) << i;
        EXPECT_EQ(range.core_id, 0u) << i;
        EXPECT_EQ(range.stream_id, 0u) << i;
        EXPECT_EQ(range.command_begin, begins[i]) << i;
        EXPECT_EQ(range.command_count, counts[i]) << i;
    }
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = program.arch_digest_hex;
    EXPECT_TRUE(verifyDecodedProgram(program, arch, error))
        << error.code << ": " << error.message;
}

TEST(MeshBinaryServingTest, RejectsProfileStreamRangeViolations)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t ranges =
        sectionOffset(golden, kSectionTypePROFILE_STREAM_RANGES);
    const MoeViolation cases[] = {
        {"empty_range",
         ranges + kProfileStreamRangesCommandCountOffset, 4, 0,
         "E_ABI_BOUNDS"},
        {"unknown_profile",
         ranges + kProfileStreamRangesProfileIdOffset, 4, 9, "E_ABI_BOUNDS"},
        {"range_leaves_stream",
         ranges + kProfileStreamRangesCommandCountOffset, 4, 9,
         "E_ABI_SECTION_RANGE"},
        {"command_belongs_elsewhere",
         ranges + kProfileStreamRangesBytes +
             kProfileStreamRangesCommandCountOffset, 4, 9,
         "E_ABI_SECTION_RANGE"},
        {"duplicate_identity",
         ranges + kProfileStreamRangesBytes +
             kProfileStreamRangesProfileIdOffset, 4, 1, "E_ABI_DUPLICATE"},
        {"unsorted_identities",
         ranges + kProfileStreamRangesBytes +
             kProfileStreamRangesProfileIdOffset, 4, 0, "E_ABI_BOUNDS"},
    };
    for (const auto &test : cases) {
        MeshBytes image = golden;
        writeField(image, test.offset, test.width, test.value);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        RuntimeArch arch = testArch();
        const bool decoded = decodeMeshBinary(image, program, error);
        if (decoded)
            arch.arch_digest_hex = program.arch_digest_hex;
        const bool accepted =
            decoded && verifyDecodedProgram(program, arch, error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
}

TEST(MeshBinaryServingTest, RejectsServingFeatureWithoutExecutionRanges)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    writeField(image, 104, 8, kFeatureAgentServingV1);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_FEATURE");
}

TEST(MeshBinaryServingTest, RejectsServingSectionWithoutTheFeatureBit)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    writeField(image, 104, 8, 0);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_FEATURE");
}

TEST(MeshBinaryServingTest, RejectsServingViolationsAfterRecomputedChecksums)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    const size_t request =
        sectionOffset(golden, kSectionTypeAGENT_REQUEST_PROFILES);
    const size_t instance =
        sectionOffset(golden, kSectionTypeAGENT_INSTANCE_PROFILES);
    const size_t members =
        sectionOffset(golden, kSectionTypeAGENT_INSTANCE_MEMBER_BINDINGS);
    const size_t requirements =
        sectionOffset(golden, kSectionTypeAGENT_REQUEST_BINDING_REQUIREMENTS);
    const size_t publish =
        sectionOffset(golden, kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS);
    const size_t relocations =
        sectionOffset(golden, kSectionTypeRELOCATIONS);
    const size_t commands = sectionOffset(golden, kSectionTypeCOMMANDS);
    const size_t descriptors =
        sectionOffset(golden, kSectionTypeDMA_DESCRIPTORS);
    const size_t prefill_io =
        instance + kAgentInstanceProfilesHostInputDmaBytesPerMemberOffset;
    const size_t decode_io =
        instance + kAgentInstanceProfilesBytes +
        kAgentInstanceProfilesKvReadBytesPerMemberOffset;
    const size_t publish_io =
        instance + 2 * kAgentInstanceProfilesBytes +
        kAgentInstanceProfilesHostOutputDmaBytesPerMemberOffset;
    const MoeViolation cases[] = {
        {"host_load_short", prefill_io, 8, 127,
         "E_BINDING_ROLE"},
        {"host_load_long", prefill_io, 8, 129,
         "E_HOST_IO_SIZE_MISMATCH"},
        {"kv_read_short", decode_io, 8, 129, "E_KV_TOKEN_MISMATCH"},
        {"publish_store_short", publish_io, 8, 127,
         "E_HOST_IO_SIZE_MISMATCH"},
        {"request_reserved0", request + kAgentRequestProfilesReserved0Offset,
         2, 1, "E_ABI_RESERVED"},
        {"request_flags", request + kAgentRequestProfilesFlagsOffset, 2, 0,
         "E_REQUEST_PROFILE"},
        {"request_path_mask", request + kAgentRequestProfilesPathMaskOffset, 4,
         0x8, "E_REQUEST_PROFILE"},
        {"request_kv_bytes", request + kAgentRequestProfilesKvBytesPerTokenOffset,
         4, 0, "E_REQUEST_PROFILE"},
        {"request_chunk", request +
             kAgentRequestProfilesPublishChunkBytesOffset, 4, 7,
         "E_OUTPUT_CHUNK_MISMATCH"},
        {"instance_id_zero",
         instance + kAgentInstanceProfilesInstanceProfileIdOffset, 4, 0,
         "E_REQUEST_PROFILE"},
        {"instance_path_kind",
         instance + kAgentInstanceProfilesPathKindOffset, 2, 7,
         "E_ABI_ENUM"},
        {"instance_phase",
         instance + kAgentInstanceProfilesPhaseOffset, 2, 7, "E_ABI_ENUM"},
        {"instance_flags",
         instance + kAgentInstanceProfilesFlagsOffset, 4, 1,
         "E_ABI_RESERVED"},
        {"instance_chunk",
         instance + kAgentInstanceProfilesBytes +
             kAgentInstanceProfilesDecodeChunkTokensOffset, 2, 0,
         "E_REQUEST_PROFILE"},
        {"instance_kv_bytes",
         instance + kAgentInstanceProfilesBytes +
             kAgentInstanceProfilesKvWriteBytesPerMemberOffset, 8, 1,
         "E_KV_TOKEN_MISMATCH"},
        {"member_rank",
         members + kAgentInstanceMemberBindingsExpectedLogicalSourceRankOffset,
         4, 7, "E_REQUEST_PROFILE"},
        {"member_reserved",
         members + kAgentInstanceMemberBindingsReserved0Offset, 2, 1,
         "E_ABI_RESERVED"},
        {"requirement_reserved",
         requirements + kAgentRequestBindingRequirementsReservedOffset, 4, 1,
         "E_ABI_RESERVED"},
        {"requirement_kind",
         requirements + kAgentRequestBindingRequirementsBindingKindOffset, 2,
         9, "E_BINDING_ROLE"},
        {"requirement_flags",
         requirements + kAgentRequestBindingRequirementsBindingFlagsOffset, 2,
         4, "E_BINDING_ROLE"},
        {"publish_member_ordinal",
         publish + kAgentPublishSurrogateBindingsMemberOrdinalOffset, 2, 5,
         "E_BINDING_ROLE"},
        {"publish_reserved",
         publish + kAgentPublishSurrogateBindingsReserved0Offset, 2, 1,
         "E_ABI_RESERVED"},
        {"publish_fill_kind",
         publish + kAgentPublishSurrogateBindingsFillKindOffset, 2,
         kDmaFillKindCONSTANT_PATTERN, "E_BINDING_ROLE"},
        {"publish_digest_source",
         publish + kAgentPublishSurrogateBindingsDigestSourceOffset, 2, 1,
         "E_ABI_ENUM"},
        {"kv_requirement_flags",
         requirements + 2 * kAgentRequestBindingRequirementsBytes +
             kAgentRequestBindingRequirementsBindingFlagsOffset, 2, 1,
         "E_BINDING_ROLE"},
        {"weight_requirement_flags",
         requirements + kAgentRequestBindingRequirementsBytes +
             kAgentRequestBindingRequirementsBindingFlagsOffset, 2, 4,
         "E_BINDING_ROLE"},
        {"member_slot_tensor",
         relocations + 4 * kRelocationsBytes + kRelocationsTensorIdOffset, 4,
         6, "E_BINDING_ROLE"},
        {"kv_store_offset",
         descriptors + 6 * kDmaDescriptorsBytes + kDmaDescriptorsDstOffset +
             kDmaEndpointOffsetBytesOffset, 8, 4194304,
         "E_BINDING_ROLE"},
        {"publish_store_wait",
         commands + 16 * kCommandsBytes + kCommandsWaitCountOffset, 4, 0,
         "E_DMA_RANGE"},
    };
    for (const auto &test : cases) {
        MeshBytes image = golden;
        writeField(image, test.offset, test.width, test.value);
        refreshServingChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        RuntimeArch arch = testArch();
        const bool decoded = decodeMeshBinary(image, program, error);
        if (decoded)
            arch.arch_digest_hex = program.arch_digest_hex;
        const bool accepted =
            decoded && verifyDecodedProgram(program, arch, error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
}


} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

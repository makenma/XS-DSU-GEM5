#include "dev/ai_mesh/mesh_binary_test_support.hh"
using namespace gem5::ai_mesh::test_support;
namespace gem5::ai_mesh { namespace {
void expectAccepts(const unsigned char *bytes, size_t size)
{
    MeshBytes image(bytes, bytes + size);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.header.abi_major, mesh_abi::kAbiMajor);
    EXPECT_EQ(program.header.abi_minor, mesh_abi::kAbiMinor);
    EXPECT_EQ(program.header.required_features,
              mesh_abi::kRequiredFeatures);
    ASSERT_TRUE(admit(program, testArch(), error))
        << error.code << ": " << error.message;
    MeshBytes reencoded;
    ASSERT_TRUE(encodeMeshBinary(program, reencoded, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(reencoded, image);
}
} }
namespace gem5
{
namespace ai_mesh
{
namespace
{

// The embedded golden images come from the Python encoder CLI and pin the
// cross-language ABI contract: the C++ reader must accept them and reject
// any single-bit corruption.

TEST(MeshBinaryTest, LoadsGoldenSingleProgram)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.header.abi_major, 2u);
    EXPECT_EQ(program.header.abi_minor, 0u);
    EXPECT_EQ(program.transport.commands.size(), 7u);
    EXPECT_EQ(program.transport.dma_descriptors.size(), 3u);
    EXPECT_EQ(program.transport.expected_traffic.size(), 3u);
    EXPECT_TRUE(admit(program, testArch(), error)) << error.message;
}

TEST(MeshBinaryTest, LoadsGoldenDualProgram)
{
    MeshBytes image = fromArray(kGoldenMshbDual, sizeof(kGoldenMshbDual));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.transport.commands.size(), 15u);
    EXPECT_EQ(program.transport.dma_descriptors.size(), 6u);
    EXPECT_TRUE(admit(program, testArch(), error)) << error.message;
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
    ASSERT_FALSE(admit(program, wrong, error));
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
    EXPECT_EQ(kFeatureDynamicMoeV1, 0x2ull);
    EXPECT_EQ(kFeatureAgentServingV1, 0x4ull);
    EXPECT_EQ(kKnownFeatureMask, 0xfull);
    EXPECT_EQ(kFeatureDynamicMoeV1MinWriterMinor, 0);
    EXPECT_EQ(kFeatureAgentServingV1MinWriterMinor, 0);
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
              0ull);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeAGENT_REQUEST_PROFILES),
              kFeatureAgentServingV1);
    EXPECT_EQ(sectionRequiredFeature(kSectionTypeAGENT_PUBLISH_SURROGATE_BINDINGS),
              kFeatureAgentServingV1);
    EXPECT_EQ(kFeatureProfileScopedExecutionV1, 0x8ull);
    EXPECT_EQ(kFeatureProfileScopedExecutionV1MinWriterMinor, 0);
    EXPECT_EQ(kFeatureAgentServingV1Requires, kFeatureProfileScopedExecutionV1 | kFeatureScheduledSemantics);
    EXPECT_TRUE(featureRequirementsMet(kFeatureAgentServingV1 |
                                       kFeatureProfileScopedExecutionV1 | kFeatureScheduledSemantics));
    EXPECT_FALSE(featureRequirementsMet(kFeatureAgentServingV1));
    EXPECT_FALSE(featureRequirementsMet(kFeatureDynamicMoeV1 |
                                        kFeatureAgentServingV1));
    EXPECT_EQ(kSectionTypePROFILE_STREAM_RANGES, 0x400a);
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
        {"missing_scheduled_semantics", 0, kFeatureDynamicMoeV1, "E_ABI_VERSION"},
        {"missing_feature_sections", 0, kFeatureScheduledSemantics | kFeatureDynamicMoeV1,
         "E_ABI_FEATURE"},
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
    wr64(accepted, 104, kFeatureScheduledSemantics);
    recomputeMeshChecksums(accepted);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(accepted, program, error)) << error.code;
    EXPECT_EQ(program.header.abi_minor, 1);
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
            verified = admit(program, testArch(), error);
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
    for (const auto &command : reference.transport.commands)
        if (command.opcode == kOpcodeREPEAT)
            repeat = &command;
    ASSERT_NE(repeat, nullptr);
    MeshProgramAdmission admission;
    ASSERT_TRUE(admitProgram(std::make_shared<DecodedProgram>(reference),
                             testArch(), admission, decode_error));
    const auto *owner = admission.context().commandStream(repeat->command_id);
    ASSERT_NE(owner, nullptr);
    const uint32_t ordinal = owner->ordinal;

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
            admit(program, testArch(), error);
        EXPECT_FALSE(accepted) << test_case.name;
        if (!accepted) {
            EXPECT_EQ(error.code, std::string(test_case.name) == "repeat_wrap" ?
                "E_ABI_ENUM" : "E_ABI_BOUNDS") << test_case.name << ": " << error.message;
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
    EXPECT_EQ(program.header.abi_major, 2u);
    EXPECT_EQ(program.header.abi_minor, 0u);
    EXPECT_EQ(program.header.required_features, kFeatureScheduledSemantics | kFeatureDynamicMoeV1);
    EXPECT_TRUE((program.header.required_features & mesh_abi::kFeatureDynamicMoeV1));
    ASSERT_EQ(program.transport.content_digests.size(), 1u);
    ASSERT_EQ(program.transport.moe_layer_specs.size(), 1u);
    ASSERT_EQ(program.transport.moe_expert_specs.size(), 2u);
    ASSERT_EQ(program.transport.moe_dynamic_regions.size(), 1u);
    ASSERT_EQ(program.transport.moe_kernel_specs.size(), 1u);
    const auto &layer = program.transport.moe_layer_specs[0];
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
    const auto &region = program.transport.moe_dynamic_regions[0];
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
    const auto &kernel = program.transport.moe_kernel_specs[0];
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
    for (const auto &expert : program.transport.moe_expert_specs) {
        EXPECT_EQ(expert.layer_id, 1u);
        EXPECT_EQ(expert.core_id, 0);
        EXPECT_EQ(expert.reserved_core, 0);
        EXPECT_EQ(expert.weight_symbol_id, 4u);
        EXPECT_EQ(expert.weight_bytes, 4096ull);
        EXPECT_EQ(expert.weight_digest_index, 0u);
        EXPECT_EQ(expert.reserved, 0u);
    }
    EXPECT_EQ(program.transport.moe_expert_specs[0].expert_id, 0);
    EXPECT_EQ(program.transport.moe_expert_specs[0].weight_region_offset, 0ull);
    EXPECT_EQ(program.transport.moe_expert_specs[1].expert_id, 1);
    EXPECT_EQ(program.transport.moe_expert_specs[1].weight_region_offset, 4096ull);
    const auto &digest = program.transport.content_digests[0];
    EXPECT_EQ(digest.object_kind, kContentDigestObjectKindTENSOR);
    EXPECT_EQ(digest.reserved, 0);
    EXPECT_EQ(digest.object_id, 2u);
    MeshLoadError verify_error;
    EXPECT_TRUE(admit(program, testMoeArch(), verify_error))
        << verify_error.code << ": " << verify_error.message;
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
            admit(program, testMoeArch(), error);
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
            admit(program, testMoeArch(), error);
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
    EXPECT_FALSE(admit(program, partitioned, error));
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
    ASSERT_TRUE(admit(program, testMoeArch(), error))
        << error.code << ": " << error.message;
    ASSERT_EQ(program.transport.moe_layer_specs.size(), 1u);
    ASSERT_EQ(program.transport.moe_dynamic_regions.size(), 2u);
    ASSERT_EQ(program.transport.moe_expert_specs.size(), 2u);
    EXPECT_EQ(program.transport.moe_layer_specs[0].dynamic_region_count, 2u);
    EXPECT_EQ(program.transport.moe_layer_specs[0].max_materialized_events, 33u);
    EXPECT_EQ(program.transport.moe_dynamic_regions[0].core_id, 0);
    EXPECT_EQ(program.transport.moe_dynamic_regions[1].core_id, 1);
    EXPECT_EQ(program.transport.moe_expert_specs[0].core_id, 0);
    EXPECT_EQ(program.transport.moe_expert_specs[1].core_id, 1);
    EXPECT_LT(program.transport.moe_dynamic_regions[0].insert_after_command_id,
              program.transport.moe_dynamic_regions[0].resume_before_command_id);
    EXPECT_LT(program.transport.moe_dynamic_regions[1].insert_after_command_id,
              program.transport.moe_dynamic_regions[1].resume_before_command_id);
}

TEST(MeshBinaryMoeTest, RejectsMoeSectionsWithoutTheFeatureBit)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    wr64(image, 104, kFeatureScheduledSemantics);
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
        admit(program, testMoeArch(), error);
    EXPECT_FALSE(accepted);
    EXPECT_EQ(error.code, "E_LIFECYCLE");
    EXPECT_EQ(error.message, "region gate must precede the local halt");
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
    ASSERT_EQ(program.transport.moe_expert_specs.size(),
              sizeof(golden::kGate5ExpertResolvedDigests) / 32);
    std::vector<WeightRegionBindingV1> bindings;
    for (size_t i = 0; i < program.transport.moe_expert_specs.size(); i++) {
        const auto &expert = program.transport.moe_expert_specs[i];
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
    ASSERT_EQ(program.transport.moe_expert_specs.size(),
              sizeof(golden::kGate5ExpertResolvedDigests) / 32);
    for (uint16_t core : {uint16_t(0), uint16_t(1)}) {
        std::vector<WeightRegionBindingV1> bindings;
        for (size_t i = 0; i < program.transport.moe_expert_specs.size(); i++) {
            const auto &expert = program.transport.moe_expert_specs[i];
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
                program.transport.moe_expert_specs[sites[index].expert_id];
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
    program.transport.moe_expert_specs = {first, alias, other, remote};
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
    EXPECT_EQ(program.header.abi_major, 2u);
    EXPECT_EQ(program.header.abi_minor, 0u);
    EXPECT_EQ(program.header.required_features,
              kFeatureScheduledSemantics | kFeatureAgentServingV1 | kFeatureProfileScopedExecutionV1);
    EXPECT_TRUE((program.header.required_features & mesh_abi::kFeatureAgentServingV1));
    EXPECT_TRUE((program.header.required_features & mesh_abi::kFeatureProfileScopedExecutionV1));
    EXPECT_FALSE((program.header.required_features & mesh_abi::kFeatureDynamicMoeV1));
    ASSERT_EQ(program.transport.agent_request_profiles.size(), 1u);
    ASSERT_EQ(program.transport.agent_instance_profiles.size(), 3u);
    ASSERT_EQ(program.transport.agent_source_core_map.size(), 1u);
    ASSERT_EQ(program.transport.agent_instance_member_bindings.size(), 3u);
    ASSERT_EQ(program.transport.agent_request_binding_requirements.size(), 4u);
    ASSERT_EQ(program.transport.agent_publish_surrogate_bindings.size(), 1u);
    const auto &request = program.transport.agent_request_profiles[0];
    EXPECT_EQ(request.program_id, 1u);
    EXPECT_EQ(request.profile_id, 1u);
    EXPECT_EQ(request.flags, kAgentRequestFlagsHAS_KV);
    EXPECT_EQ(request.requested_profile_key, golden::kServingRequestedProfileKey);
    EXPECT_EQ(request.path_mask, 0x1u);
    EXPECT_EQ(request.publish_chunk_bytes, 64u);
    const auto &prefill = program.transport.agent_instance_profiles[0];
    const auto &decode = program.transport.agent_instance_profiles[1];
    const auto &publish = program.transport.agent_instance_profiles[2];
    EXPECT_EQ(prefill.phase, kPhasePREFILL);
    EXPECT_EQ(decode.phase, kPhaseDECODE);
    EXPECT_EQ(publish.phase, kPhasePUBLISH);
    EXPECT_EQ(prefill.decode_chunk_tokens, 0u);
    EXPECT_EQ(decode.decode_chunk_tokens, 1u);
    EXPECT_EQ(publish.host_input_dma_bytes_per_member, 0ull);
    EXPECT_EQ(publish.host_output_dma_bytes_per_member, 128ull);
    EXPECT_EQ(publish.kv_read_bytes_per_member, 0ull);
    EXPECT_EQ(prefill.kv_write_bytes_per_member, 128ull);
    const auto &binding = program.transport.agent_publish_surrogate_bindings[0];
    EXPECT_EQ(binding.fill_kind, kDmaFillKindAGENT_OUTPUT_SURROGATE);
    EXPECT_EQ(binding.allocation_role,
              kAgentAllocationRolePUBLISH_SURROGATE_SOURCE);
    EXPECT_EQ(binding.digest_source,
              kAgentDigestSourceREQUEST_SEMANTIC_OUTPUT_DIGEST);
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_TRUE(admit(program, arch, error))
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
    EXPECT_TRUE((program.header.required_features & mesh_abi::kFeatureAgentServingV1));
    ASSERT_EQ(program.transport.agent_request_profiles.size(), 1u);
    ASSERT_EQ(program.transport.agent_instance_profiles.size(), 4u);
    ASSERT_EQ(program.transport.agent_instance_member_bindings.size(), 4u);
    ASSERT_EQ(program.transport.agent_publish_surrogate_bindings.size(), 1u);
    const auto &request = program.transport.agent_request_profiles[0];
    EXPECT_EQ(request.output_tokens, 2u);
    EXPECT_EQ(request.host_output_bytes, 256ull);
    const auto &prefill = program.transport.agent_instance_profiles[0];
    const auto &decode0 = program.transport.agent_instance_profiles[1];
    const auto &decode1 = program.transport.agent_instance_profiles[2];
    const auto &publish = program.transport.agent_instance_profiles[3];
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
    const auto &binding = program.transport.agent_publish_surrogate_bindings[0];
    EXPECT_EQ(binding.instance_profile_id, 14u);
    EXPECT_EQ(binding.fill_kind, kDmaFillKindAGENT_OUTPUT_SURROGATE);
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_TRUE(admit(program, arch, error))
        << error.code << ": " << error.message;
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
    std::ostringstream out;
    ASSERT_TRUE(mesh_canonical::writeString(out, raw));
    EXPECT_EQ(out.str(),
              "\"a\\\"b\\\\c\\n\\té中"
              "😀\\u0001\"");
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
    std::array<uint8_t, 32> base_digest;
    ASSERT_TRUE(programProfileKeyBaseDigest(program, base_digest, error)) << error.message;
    EXPECT_EQ(hexDigest(base_digest),
              golden::kServingProfileKeyBaseDigest);
    EXPECT_EQ(hexDigest(program.metadata.semantic_sha256),
              golden::kServingSemanticDigest);
    EXPECT_TRUE(verifyRequestProfileKeys(program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.transport.agent_request_profiles[0].requested_profile_key,
              golden::kServingRequestedProfileKey);
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
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_FALSE(admit(program, arch, error));
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
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_TRUE(admit(program, arch, error))
        << error.code << ": " << error.message;
    ASSERT_EQ(program.transport.agent_instance_profiles.size(), 6u);
    const AgentInstanceProfile *dual = nullptr;
    for (const auto &instance : program.transport.agent_instance_profiles)
        if (instance.instance_profile_id == 111)
            dual = &instance;
    ASSERT_NE(dual, nullptr);
    EXPECT_EQ(dual->member_count, 2u);
    EXPECT_EQ(dual->member_binding_count, 2u);
    ASSERT_EQ(program.transport.agent_publish_surrogate_bindings.size(), 3u);
    uint32_t allocations = 0;
    for (const auto &record : program.transport.agent_publish_surrogate_bindings)
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
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_FALSE(admit(program, arch, error));
    EXPECT_EQ(error.code, "E_RELOCATION");
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
    ASSERT_EQ(program.transport.agent_publish_surrogate_bindings.size(), 3u);
    const AgentPublishSurrogateBinding &first_record =
        program.transport.agent_publish_surrogate_bindings[1];
    const AgentPublishSurrogateBinding &second_record =
        program.transport.agent_publish_surrogate_bindings[2];
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
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_FALSE(admit(program, arch, error));
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
    EXPECT_TRUE((program.header.required_features & mesh_abi::kFeatureProfileScopedExecutionV1));
    ASSERT_EQ(program.transport.profile_stream_ranges.size(), 3u);
    const uint32_t begins[] = {0u, 8u, 14u};
    const uint32_t counts[] = {8u, 6u, 5u};
    for (size_t i = 0; i < program.transport.profile_stream_ranges.size(); i++) {
        const auto &range = program.transport.profile_stream_ranges[i];
        EXPECT_EQ(range.profile_id, i + 1) << i;
        EXPECT_EQ(range.core_id, 0u) << i;
        EXPECT_EQ(range.stream_id, 0u) << i;
        EXPECT_EQ(range.command_begin, begins[i]) << i;
        EXPECT_EQ(range.command_count, counts[i]) << i;
    }
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    EXPECT_TRUE(admit(program, arch, error))
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
         "E_ABI_BOUNDS"},
        {"command_belongs_elsewhere",
         ranges + kProfileStreamRangesBytes +
             kProfileStreamRangesCommandCountOffset, 4, 9,
         "E_ABI_BOUNDS"},
        {"duplicate_identity",
         ranges + kProfileStreamRangesBytes +
             kProfileStreamRangesProfileIdOffset, 4, 1, "E_ABI_BOUNDS"},
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
            arch.arch_digest_hex = hexDigest(program.header.arch_digest);
        const bool accepted =
            decoded && admit(program, arch, error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
}

TEST(MeshBinaryServingTest, RejectsServingFeatureWithoutItsExecutionDependency)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    writeField(image, kHeaderRequiredFeaturesOffset, 8,
               kFeatureScheduledSemantics | kFeatureAgentServingV1);
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_VERSION");
}

TEST(MeshBinaryServingTest, RejectsServingSectionWithoutTheFeatureBit)
{
    using namespace mesh_abi;
    const MeshBytes golden =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(golden.empty());
    MeshBytes image = golden;
    writeField(image, kHeaderRequiredFeaturesOffset, 8, kFeatureScheduledSemantics);
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
    DecodedProgram original;
    MeshLoadError original_error;
    ASSERT_TRUE(decodeMeshBinary(golden, original, original_error));
    const auto producer = original.transport.agent_publish_surrogate_bindings.front().producer_command_id;
    const auto output = std::find_if(original.transport.dma_descriptors.begin(),
        original.transport.dma_descriptors.end(), [&](const auto &descriptor) {
            if (descriptor.kind != kDmaKindSTORE)
                return false;
            const auto command = std::find_if(original.transport.commands.begin(),
                original.transport.commands.end(), [&](const auto &row) { return row.command_id == producer; });
            return command != original.transport.commands.end() && descriptor.src.tensor_id ==
                original.transport.command_operands.at(command->operand_begin).tensor_id;
        });
    ASSERT_NE(output, original.transport.dma_descriptors.end());
    const auto store = std::find_if(original.transport.commands.begin(), original.transport.commands.end(),
        [&](const auto &command) { return command.command_id == output->command_id; });
    ASSERT_NE(store, original.transport.commands.end());
    ASSERT_GT(store->wait_count, 0u);
    const size_t store_index = std::distance(original.transport.commands.begin(), store);
    const MoeViolation cases[] = {
        {"host_load_short", prefill_io, 8, 127,
         "E_HOST_IO_SIZE_MISMATCH"},
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
         original.transport.relocations.at(4).tensor_id + 1, "E_RELOCATION"},
        {"kv_store_offset",
         descriptors + 6 * kDmaDescriptorsBytes + kDmaDescriptorsDstOffset +
             kDmaEndpointOffsetBytesOffset, 8, 4194304,
         "E_DMA_RANGE"},
        {"publish_store_wait",
         commands + store_index * kCommandsBytes + kCommandsWaitCountOffset, 2, 0,
         "E_EVENT_NO_PRODUCER"},
    };
    for (const auto &test : cases) {
        MeshBytes image = golden;
        writeField(image, test.offset, test.width, test.value);
        ASSERT_NE(image, golden) << test.name;
        refreshServingChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        RuntimeArch arch = testArch();
        const bool decoded = decodeMeshBinary(image, program, error);
        if (decoded)
            arch.arch_digest_hex = hexDigest(program.header.arch_digest);
        const bool accepted =
            decoded && admit(program, arch, error);
        EXPECT_FALSE(accepted) << test.name;
        if (!accepted) {
            EXPECT_EQ(error.code, test.code) << test.name;
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

namespace gem5::ai_mesh { namespace {
TEST(MeshBinaryTest, LoadsCompleteGoldenPrograms)
{
    expectAccepts(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    expectAccepts(kGoldenMshbDual, sizeof(kGoldenMshbDual));
    expectAccepts(kGoldenMshbRepeat, sizeof(kGoldenMshbRepeat));
}
} }

namespace gem5::ai_mesh { namespace {
TEST(MeshBinaryTest, RejectsPayloadCorruptionAtomically)
{
    MeshBytes image(
        kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    image.back() ^= 1;
    DecodedProgram program;
    program.header.abi_minor = 77;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
    EXPECT_EQ(program.header.abi_minor, 77);
}
} }

namespace gem5::ai_mesh { namespace {
TEST(MeshBinaryTest, RejectsMutableVersionAndChecksum)
{
    MeshBytes image(
        kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    program.header.abi_major = 0;
    EXPECT_FALSE(admit(program, testArch(), error));
    EXPECT_EQ(error.code, "E_ABI_VERSION");
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    program.metadata.semantic_sha256.front() ^= 1;
    EXPECT_FALSE(admit(program, testArch(), error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
}
} }

namespace gem5::ai_mesh { namespace {
TEST(MeshBinaryTest, LayoutConstantsMatchSchema)
{
    static_assert(mesh_abi::kHeaderBytes == 128);
    static_assert(mesh_abi::kSectionDirBytes == 40);
    static_assert(mesh_abi::kCommandsBytes == 40);
    EXPECT_EQ(mesh_abi::kAbiMajor, 2);
    EXPECT_EQ(mesh_abi::kAbiMinor, 0);
}
} }

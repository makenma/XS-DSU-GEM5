#include <gtest/gtest.h>
#include <fstream>

#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/mesh_moe_materializer.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

using namespace gem5::ai_mesh;

class MoeMaterializerGolden : public ::testing::TestWithParam<int> {};

TEST_P(MoeMaterializerGolden, SharedPopulationMatchesIndependentPythonGraph)
{
    std::ifstream source(GetParam() >= 3 ?
        "tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb" :
        "tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb", std::ios::binary);
    ASSERT_TRUE(source.good());
    const MeshBytes image((std::istreambuf_iterator<char>(source)), {});
    DecodedProgram program;
    MeshLoadError failure;
    ASSERT_TRUE(decodeMeshBinary(image, program, failure));
    std::ifstream fixture("tests/gem5/ai_mesh/fixtures/gate6/moe_materialized_" +
                          std::to_string(GetParam()) + ".bin", std::ios::binary);
    ASSERT_TRUE(fixture.good());
    const MeshBytes golden((std::istreambuf_iterator<char>(fixture)), {});
    MoeOverlayGraph expected;
    uint32_t layer;
    std::string error;
    ASSERT_TRUE(MoeOverlayImage{}.decode(golden, expected, layer, error)) << error;
    MoePopulation population;
    auto spec = program.transport.moe_layer_specs[0];
    if (GetParam() == 1 || GetParam() == 2) {
        spec.capacity_factor_q16 = GetParam() == 1 ? 32768 : 131072;
        spec.overflow_policy = GetParam() == 1 ? mesh_abi::kMoeOverflowPolicyDROP :
                                               mesh_abi::kMoeOverflowPolicyPAD_TO_CAPACITY;
    }
    ASSERT_TRUE(population.freezeUniform(spec,
        {{{1, 0, 0, 0, 1, 0, 0}, 0, 0}, {{2, 1, 0, 0, 1, 0, 0}, 1, GetParam() == 3 ? 1u : 0u}}, {}, 17, error));
    MoeMaterializeConfig config;
    config.program_digest = expected.programDigest();
    config.program_semantic_digest = (program).metadata.semantic_sha256;
    std::vector<MoeMemberSlice> members;
    for (uint32_t ordinal = 0; ordinal < 2; ++ordinal) {
        const uint16_t core = GetParam() == 3 ? ordinal : 0;
        const auto region = std::find_if(program.transport.moe_dynamic_regions.begin(),
            program.transport.moe_dynamic_regions.end(), [&](const auto &row) {
                return row.layer_id == spec.layer_id && row.core_id == core;
            });
        ASSERT_NE(region, program.transport.moe_dynamic_regions.end());
        const auto gate = std::find_if(program.transport.commands.begin(), program.transport.commands.end(),
            [&](const auto &row) { return row.command_id == region->insert_after_command_id; });
        ASSERT_NE(gate, program.transport.commands.end());
        MoeMemberSlice member{11 + ordinal, ordinal, core, 0, 1, 0, 0, 0, 0};
        for (uint32_t index = gate->operand_begin; index < gate->operand_begin + gate->operand_count; ++index) {
            const auto &operand = program.transport.command_operands[index];
            const auto tensor = std::find_if(program.transport.tensors.begin(), program.transport.tensors.end(),
                [&](const auto &row) { return row.tensor_id == operand.tensor_id; });
            ASSERT_NE(tensor, program.transport.tensors.end());
            if (operand.access == mesh_abi::kAccessKindREAD_ONLY && tensor->role == mesh_abi::kTensorRoleINPUT)
                member.input_allocation = operand.allocation_id;
            if (operand.access == mesh_abi::kAccessKindREAD_WRITE)
                member.output_allocation = operand.allocation_id;
        }
        ASSERT_NE(member.input_allocation, 0);
        ASSERT_NE(member.output_allocation, 0);
        member.input_offset = GetParam() == 3 ? 0 : 64 * ordinal;
        member.output_offset = GetParam() == 3 ? 0 : 128 * ordinal;
        members.push_back(member);
    }
    if (GetParam() == 4)
        for (auto &allocation : program.transport.allocations)
            if (allocation.allocation_id == members[0].input_allocation ||
                allocation.allocation_id == members[0].output_allocation)
                allocation.size_bytes *= 2;
    MoeOverlayGraph actual;
    MoePreparationError preparation_error;
    ASSERT_TRUE(materializeMoeOverlay(program, spec, population,
        members,
        config, actual, preparation_error)) << preparation_error.message;
    for (const auto field : {&mesh_abi::MoeLayerSpec::max_materialized_commands,
                             &mesh_abi::MoeLayerSpec::max_materialized_events,
                             &mesh_abi::MoeLayerSpec::max_materialized_descriptors,
                             &mesh_abi::MoeLayerSpec::max_dynamic_allocations}) {
        auto insufficient = spec;
        insufficient.*field = 0;
        MoeOverlayGraph rejected;
        MoePreparationError rejection;
        EXPECT_FALSE(materializeMoeOverlay(program, insufficient, population,
            members,
            config, rejected, rejection));
        EXPECT_EQ(rejection.detail, agent_abi::E_MOE_MATERIALIZE_CAPACITY);
        EXPECT_EQ(rejection.source.error_class, mesh_abi::kMoeErrorClassMATERIALIZE);
        EXPECT_EQ(rejection.source.domain, mesh_abi::kMeshObjectDomainCOORDINATOR);
        EXPECT_EQ(rejection.source.object_kind, mesh_abi::kErrorSourceObjectKindMATERIALIZER_CHECK);
        EXPECT_EQ(rejection.source.region_group_id, spec.layer_id);
        EXPECT_EQ(rejection.source.ordinal, mesh_abi::kMaterializerCheckSiteCOUNT);
        EXPECT_TRUE(rejected.objects().empty());
    }
    const auto encoded = MoeOverlayImage{}.encode(actual, layer);
    ASSERT_EQ(encoded.size(), golden.size());
    for (size_t i = 0; i < golden.size(); ++i)
        ASSERT_EQ(unsigned(encoded[i]), unsigned(golden[i])) << "wire offset " << i;
}

INSTANTIATE_TEST_SUITE_P(CapacityPolicies, MoeMaterializerGolden, ::testing::Values(0, 1, 2, 3, 4));

TEST(MoeMaterializer, FinalizeRewritesReferencesAndAllocatesBoundedScratch)
{
    using namespace mesh_abi;
    MoeOverlayGraph graph;
    MoeOverlayEntry allocation;
    allocation.kind = kMeshObjectKindALLOCATION;
    allocation.region_id = 1;
    allocation.ordinal = 1;
    allocation.secondary_kind = kMoeAllocationKindEXPERT_OUTPUT;
    allocation.bytes = 64;
    allocation.alignment = 64;
    graph.add(allocation);
    allocation.ordinal = 2;
    allocation.secondary_kind = kMoeAllocationKindROUTE_BUFFER;
    graph.add(allocation);
    MoeOverlayEntry view;
    view.kind = kMeshObjectKindVIEW;
    view.region_id = 1;
    view.ordinal = 1;
    view.secondary_kind = kMoeViewKindEXPERT_OUTPUT;
    view.backing_kind = kMoeViewBackingOVERLAY_ALLOCATION;
    view.ref_region = 1;
    view.ref_ordinal = 1;
    view.bytes = 64;
    graph.add(view);
    MoeDynamicRegion region;
    region.region_id = 1;
    region.scratch_offset = 1024;
    region.scratch_bytes = 128;
    region.scratch_alignment = 64;
    region.max_overlay_allocations = 2;
    std::string error;
    ASSERT_TRUE(graph.finalize({region}, error)) << error;
    const auto *output = graph.resolveAllocation(1, 2);
    ASSERT_NE(output, nullptr);
    EXPECT_EQ(output->offset, 1088u);
    const auto found = graph.objects().back();
    EXPECT_EQ(found.ref_ordinal, 2u);
    MoeOverlayGraph undersized;
    undersized.add(allocation);
    region.scratch_bytes = 63;
    EXPECT_FALSE(undersized.finalize({region}, error));
    EXPECT_TRUE(undersized.scratch().empty());
}

TEST(MoeMaterializer, FinalizeRejectsUnknownOwnersAndOutOfRangeViewsAtomically)
{
    using namespace mesh_abi;
    for (unsigned mode = 0; mode < 4; ++mode) {
        MoeOverlayGraph graph;
        MoeOverlayEntry allocation;
        allocation.kind = kMeshObjectKindALLOCATION;
        allocation.region_id = mode == 0 ? 2 : 1;
        allocation.ordinal = 1;
        allocation.bytes = 64;
        allocation.alignment = mode == 1 ? 3 : 64;
        graph.add(allocation);
        if (mode >= 2) {
            MoeOverlayEntry view;
            view.kind = kMeshObjectKindVIEW;
            view.region_id = 1;
            view.ordinal = 1;
            view.owner_core = mode == 3 ? 1 : 0;
            view.backing_kind = kMoeViewBackingOVERLAY_ALLOCATION;
            view.ref_region = 1;
            view.ref_ordinal = 1;
            view.offset = mode == 2 ? 63 : 0;
            view.bytes = 64;
            graph.add(view);
        }
        MoeDynamicRegion region;
        region.region_id = 1;
        region.scratch_offset = 1024;
        region.scratch_bytes = 128;
        region.scratch_alignment = 64;
        region.max_overlay_allocations = 1;
        std::string error;
        EXPECT_FALSE(graph.finalize({region}, error)) << mode;
        EXPECT_FALSE(error.empty());
        EXPECT_TRUE(graph.scratch().empty());
    }
}

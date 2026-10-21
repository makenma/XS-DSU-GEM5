#include <gtest/gtest.h>

#include "dev/ai_mesh/mesh_program_test_support.hh"

namespace gem5::ai_mesh
{
TEST(MeshExecutionViewTest, OneVariantExposesOnlyItsSemanticCommands)
{
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(MeshBytes(kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle)), program, error));
    ProgramSemanticContext context;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error)) << error.message;
    ExecutionViewSet views;
    ASSERT_TRUE(buildExecutionViews(program, context, views, error)) << error.message;
    ASSERT_TRUE(views.wholeProgram());
    const auto *view = views.wholeProgramView();
    ASSERT_NE(view, nullptr);
    EXPECT_EQ(views.forInstance(view->entrypoint_id, view->profile_id), view);
    EXPECT_EQ(views.forInstance(UINT32_MAX, UINT32_MAX), nullptr);
    size_t count = 0;
    for (const auto &range : view->ranges) {
        EXPECT_EQ(range.command_ids.size(), range.command_count);
        EXPECT_EQ(view->rangesForCore(range.core_id).size(), 1u);
        EXPECT_EQ(view->rangeFor(range.core_id, range.stream_id), &range);
        for (auto id : range.command_ids) {
            const auto owner = context.commandStream(id);
            ASSERT_NE(owner, nullptr);
            EXPECT_EQ(owner->stream->core_id, range.core_id);
            EXPECT_EQ(owner->stream->physical_stream_id, range.stream_id);
        }
        count += range.command_count;
    }
    EXPECT_EQ(count, program.transport.commands.size());
    EXPECT_TRUE(view->rangesForCore(UINT16_MAX).empty());
}

TEST(MeshExecutionViewTest, ScopedFixtureIndexesEveryPhaseAndRejectsCrossedSelectors)
{
    auto program = test_support::loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ProgramSemanticContext context;
    MeshLoadError error;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error)) << error.message;
    ExecutionViewSet views;
    ASSERT_TRUE(buildExecutionViews(program, context, views, error)) << error.message;
    EXPECT_FALSE(views.wholeProgram());
    EXPECT_EQ(views.wholeProgramView(), nullptr);
    ASSERT_EQ(program.transport.profile_stream_ranges.size(), 3u);
    for (const auto &range : program.transport.profile_stream_ranges) {
        const auto profile = std::find_if(program.transport.profiles.begin(), program.transport.profiles.end(),
            [&](const auto &row) { return row.profile_id == range.profile_id; });
        ASSERT_NE(profile, program.transport.profiles.end());
        const auto *view = views.forInstance(profile->entrypoint_id, range.profile_id);
        ASSERT_NE(view, nullptr);
        ASSERT_EQ(view->ranges.size(), 1u);
        const auto *selected = view->rangeFor(range.core_id, range.stream_id);
        ASSERT_NE(selected, nullptr);
        EXPECT_EQ(selected->command_begin, range.command_begin);
        EXPECT_EQ(selected->command_count, range.command_count);
        for (const auto &other : program.transport.profiles) {
            if (other.entrypoint_id != profile->entrypoint_id) {
                EXPECT_EQ(views.forInstance(other.entrypoint_id, range.profile_id), nullptr);
            }
        }
    }
}

TEST(MeshExecutionViewTest, RejectsAForeignContextAndAChangedRangeProjection)
{
    auto program = test_support::loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ProgramSemanticContext context;
    MeshLoadError error;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error)) << error.message;
    ExecutionViewSet views;
    auto changed = program;
    EXPECT_FALSE(buildExecutionViews(changed, context, views, error));
    ++changed.transport.profile_stream_ranges[0].command_begin;
    ProgramSemanticContext changed_context;
    ASSERT_TRUE(buildProgramSemanticContext(changed, changed_context, error)) << error.message;
    EXPECT_FALSE(buildExecutionViews(changed, changed_context, views, error));
}
TEST(MeshExecutionViewTest, RejectsTransportStreamOwnerDriftAsStreamContract)
{
    auto program = test_support::loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ++program.transport.commands.back().stream_id;
    ProgramSemanticContext context;
    MeshLoadError error;
    ASSERT_TRUE(buildProgramSemanticContext(program, context, error)) << error.message;
    ExecutionViewSet views;
    EXPECT_FALSE(buildExecutionViews(program, context, views, error));
    EXPECT_EQ(error.code, "E_STREAM_CONTRACT");
}

}

#include <gtest/gtest.h>

#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_execution_view.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

using namespace mesh_abi;

MeshBytes loadFixture(const std::string &path)
{
    std::ifstream file(path, std::ios::binary);
    return MeshBytes((std::istreambuf_iterator<char>(file)),
                     std::istreambuf_iterator<char>());
}

DecodedProgram legacyProgram()
{
    DecodedProgram program;
    DecodedStream first;
    first.core_id = 0;
    first.stream_id = 0;
    first.command_begin = 0;
    first.command_count = 4;
    DecodedStream second;
    second.core_id = 1;
    second.stream_id = 0;
    second.command_begin = 4;
    second.command_count = 3;
    program.streams = {first, second};
    DecodedEntrypoint entrypoint;
    entrypoint.entrypoint_id = 1;
    entrypoint.profile_begin = 0;
    entrypoint.profile_count = 1;
    entrypoint.lifecycle_core_id = 0;
    entrypoint.lifecycle_stream_id = 0;
    program.entrypoints = {entrypoint};
    DecodedProfile profile;
    profile.profile_id = 1;
    profile.entrypoint_id = 1;
    program.profiles = {profile};
    return program;
}

DecodedProgram scopedProgram()
{
    DecodedProgram program = legacyProgram();
    program.has_profile_scoped_execution_v1 = true;
    DecodedProfile second;
    second.profile_id = 2;
    second.entrypoint_id = 2;
    program.profiles.push_back(second);
    DecodedEntrypoint second_entry;
    second_entry.entrypoint_id = 2;
    second_entry.profile_begin = 1;
    second_entry.profile_count = 1;
    second_entry.lifecycle_core_id = 0;
    second_entry.lifecycle_stream_id = 0;
    program.entrypoints.push_back(second_entry);
    ProfileStreamRange first;
    first.profile_id = 1;
    first.core_id = 0;
    first.stream_id = 0;
    first.command_begin = 0;
    first.command_count = 2;
    ProfileStreamRange second_range;
    second_range.profile_id = 2;
    second_range.core_id = 0;
    second_range.stream_id = 0;
    second_range.command_begin = 2;
    second_range.command_count = 2;
    program.profile_stream_ranges = {first, second_range};
    return program;
}

TEST(MeshExecutionViewTest, LegacyProgramExposesOneWholeProgramView)
{
    const ExecutionViewSet set =
        ExecutionViewSet::fromVerified(legacyProgram());
    EXPECT_TRUE(set.wholeProgram());
    const ProfileExecutionView *view = set.forInstance(1, 1);
    ASSERT_NE(view, nullptr);
    EXPECT_TRUE(view->whole_program);
    ASSERT_EQ(view->ranges.size(), 2u);
    EXPECT_EQ(view->ranges[0].core_id, 0u);
    EXPECT_EQ(view->ranges[0].command_count, 4u);
    EXPECT_EQ(view->ranges[1].core_id, 1u);
    EXPECT_EQ(view->ranges[1].command_begin, 4u);
    EXPECT_EQ(view->rangesForCore(0).size(), 1u);
    EXPECT_EQ(view->rangesForCore(1).size(), 1u);
    EXPECT_EQ(view->rangesForCore(2).size(), 0u);
    EXPECT_EQ(view->rangeFor(0, 0)->command_count, 4u);
    EXPECT_EQ(view->rangeFor(1, 0)->command_count, 3u);
    EXPECT_EQ(view->rangeFor(0, 1), nullptr);
    EXPECT_EQ(set.forInstance(9, 9), nullptr);
}

TEST(MeshExecutionViewTest, ScopedProgramIndexesEachProfileRange)
{
    const ExecutionViewSet set =
        ExecutionViewSet::fromVerified(scopedProgram());
    EXPECT_FALSE(set.wholeProgram());
    const ProfileExecutionView *first = set.forInstance(1, 1);
    const ProfileExecutionView *second = set.forInstance(2, 2);
    ASSERT_NE(first, nullptr);
    ASSERT_NE(second, nullptr);
    EXPECT_FALSE(first->whole_program);
    EXPECT_EQ(first->profile_id, 1u);
    EXPECT_EQ(first->entrypoint_id, 1u);
    ASSERT_EQ(first->ranges.size(), 1u);
    EXPECT_EQ(first->ranges[0].command_begin, 0u);
    EXPECT_EQ(first->ranges[0].command_count, 2u);
    ASSERT_EQ(second->ranges.size(), 1u);
    EXPECT_EQ(second->ranges[0].command_begin, 2u);
    EXPECT_EQ(second->rangesForCore(1).size(), 0u);
    EXPECT_EQ(set.forInstance(1, 2), nullptr);
    EXPECT_EQ(set.forInstance(2, 1), nullptr);
}

TEST(MeshExecutionViewTest, ScopedFixtureIndexesTheThreePhaseProfiles)
{
    const MeshBytes image =
        loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_min.mshb");
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    const ExecutionViewSet set = ExecutionViewSet::fromVerified(program);
    EXPECT_FALSE(set.wholeProgram());
    ASSERT_EQ(program.profile_stream_ranges.size(), 3u);
    for (const auto &range : program.profile_stream_ranges) {
        const ProfileExecutionView *view =
            set.forInstance(program.profiles[range.profile_id - 1].
                                entrypoint_id,
                            range.profile_id);
        ASSERT_NE(view, nullptr) << range.profile_id;
        ASSERT_EQ(view->ranges.size(), 1u);
        EXPECT_EQ(view->ranges[0].command_begin, range.command_begin);
        EXPECT_EQ(view->ranges[0].command_count, range.command_count);
        EXPECT_EQ(view->rangeFor(range.core_id, range.stream_id)
                      ->command_begin,
                  range.command_begin);
    }
}

} // namespace
} // namespace ai_mesh
} // namespace gem5

#include <gtest/gtest.h>

#include <fstream>

#include "dev/ai_mesh/serving_fill_inputs.hh"

namespace
{

using namespace gem5::ai_mesh;

class ServingFillInputsTest : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        std::ifstream stream(
            "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb",
            std::ios::binary);
        ASSERT_TRUE(stream.good());
        std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(stream)),
                                  std::istreambuf_iterator<char>());
        MeshLoadError error;
        ASSERT_TRUE(decodeMeshBinary(bytes, program, error));
        const auto &record = program.agent_publish_surrogate_bindings.front();
        for (const auto &instance : program.agent_instance_profiles)
            if (instance.instance_profile_id == record.instance_profile_id) {
                views = ExecutionViewSet::fromVerified(program);
                view = views.forInstance(instance.mesh_entrypoint_id,
                                         instance.mesh_profile_id);
                binding.content.assign(instance.host_output_dma_bytes_per_member,
                                       0x6a);
            }
        ASSERT_NE(view, nullptr);
        binding.request_id = 1;
        binding.request_generation = 3;
        binding.instance_profile_id = record.instance_profile_id;
        binding.member_ordinal = record.member_ordinal;
        binding.allocation_id = record.allocation_id;
        binding.producer_command_id = record.producer_command_id;
    }

    DecodedProgram program;
    ExecutionViewSet views;
    const ProfileExecutionView *view = nullptr;
    ServingFillBinding binding;
    ServingInstanceBinding owner{1, 3};
    std::string reason;
};

TEST_F(ServingFillInputsTest, FreezesContentAndPreservesItAcrossIssueRetries)
{
    auto inputs = ServingFillInputs::freeze(
        program, *view, InstanceGeneration(4), owner, {binding}, reason);
    ASSERT_TRUE(inputs) << reason;
    const auto command = staticProgramObject(InstanceGeneration(4),
        mesh_abi::MeshObjectKind::COMMAND, binding.producer_command_id);
    binding.content.assign(binding.content.size(), 0xff);
    const auto *first = inputs->contentFor(command);
    ASSERT_NE(first, nullptr);
    EXPECT_EQ(first->front(), 0x6a);
    EXPECT_EQ(first, inputs->contentFor(command));
    auto stale = command;
    stale.instance = InstanceGeneration(5);
    EXPECT_EQ(inputs->contentFor(stale), nullptr);
    stale = command;
    stale.domain = mesh_abi::MeshObjectDomain::MOE_OVERLAY;
    EXPECT_EQ(inputs->contentFor(stale), nullptr);
}

TEST_F(ServingFillInputsTest, NextInstanceRequiresItsOwnBindingAfterDrain)
{
    auto active = ServingFillInputs::freeze(
        program, *view, InstanceGeneration(4), owner, {binding}, reason);
    ASSERT_TRUE(active) << reason;
    active.reset();
    active = ServingFillInputs::freeze(
        program, *view, InstanceGeneration(5), owner, {}, reason);
    EXPECT_FALSE(active);
    EXPECT_NE(reason.find("missing"), std::string::npos);
}

TEST_F(ServingFillInputsTest, MissingKvSentinelContentIsRejectedBeforeArm)
{
    const auto &profile = program.agent_instance_profiles.front();
    const auto *prefill = views.forInstance(profile.mesh_entrypoint_id,
                                           profile.mesh_profile_id);
    ASSERT_NE(prefill, nullptr);
    EXPECT_FALSE(ServingFillInputs::freeze(
        program, *prefill, InstanceGeneration(1), owner, {}, reason));
}

TEST_F(ServingFillInputsTest, RejectsIncorrectProducerIdentityAndContentRange)
{
    for (unsigned mutation = 0; mutation < 9; ++mutation) {
        auto invalid = binding;
        switch (mutation) {
          case 0: invalid.request_id = 0; break;
          case 1: ++invalid.instance_profile_id; break;
          case 2: ++invalid.member_ordinal; break;
          case 3: ++invalid.allocation_id; break;
          case 4: ++invalid.producer_command_id; break;
          case 5: invalid.content.pop_back(); break;
          case 6: invalid.content.push_back(0); break;
          case 7: ++invalid.request_id; break;
          case 8: ++invalid.request_generation; break;
        }
        EXPECT_FALSE(ServingFillInputs::freeze(
            program, *view, InstanceGeneration(4), owner, {invalid}, reason))
            << mutation;
    }
    EXPECT_FALSE(ServingFillInputs::freeze(
        program, *view, InstanceGeneration(4), owner, {binding, binding}, reason));
}

}

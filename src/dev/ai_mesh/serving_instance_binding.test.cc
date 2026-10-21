#include <gtest/gtest.h>

#include "dev/ai_mesh/mesh_program_test_support.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"

namespace
{
using namespace gem5::ai_mesh;

class ServingInstanceBindingTest : public ::testing::Test
{
  protected:
    test_support::AdmittedFixture fixture{
        "tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb"};
    std::shared_ptr<const MeshProgramInvocation> invocation;
    ServingInstanceBinding binding{2};
    std::vector<uint32_t> slots;

    void SetUp() override
    {
        const auto &profiles = fixture.program.transport.agent_instance_profiles;
        const auto profile = std::find_if(profiles.begin(), profiles.end(), [](const auto &row) {
            return row.phase == mesh_abi::kPhaseDECODE && row.member_count == 2;
        });
        ASSERT_NE(profile, profiles.end());
        invocation = fixture.invocation(profile->mesh_entrypoint_id, profile->mesh_profile_id);
        ASSERT_TRUE(binding.installInvocation(invocation));
        for (uint32_t ordinal = 0; ordinal < 2; ++ordinal) {
            const auto &member = fixture.program.transport.agent_instance_member_bindings[
                profile->member_binding_first + ordinal];
            const auto source = fixture.admission->context().symbolBinding(profile->mesh_entrypoint_id,
                profile->mesh_profile_id, member.static_kv_symbol_id);
            ASSERT_TRUE(source);
            slots.push_back(source->slot->slot_id);
        }
    }
};

TEST_F(ServingInstanceBindingTest, ResolvesThePreparedInvocationAddress)
{
    ASSERT_TRUE(binding.bindSlot(slots[0], {0, 128}, {128, 16}));
    const auto read = binding.resolveSlot(slots[0], 64, 64, false);
    ASSERT_TRUE(read);
    EXPECT_EQ(read->address, invocation->binding().binding(slots[0])->allocation_address + 64);
    const auto write = binding.resolveSlot(slots[0], 128, 16, true);
    ASSERT_TRUE(write);
    EXPECT_EQ(write->address, read->address + 64);
}

TEST_F(ServingInstanceBindingTest, EnforcesFrozenReadAndAppendWindows)
{
    ASSERT_TRUE(binding.bindSlot(slots[0], {0, 128}, {128, 16}));
    EXPECT_FALSE(binding.resolveSlot(slots[0], 128, 1, false));
    EXPECT_FALSE(binding.resolveSlot(slots[0], 127, 1, true));
    EXPECT_FALSE(binding.resolveSlot(slots[0], 143, 2, true));
    EXPECT_FALSE(binding.resolveSlot(slots[0], UINT64_MAX, 1, false));
    EXPECT_FALSE(binding.bindSlot(slots[0], {0, 128}, {}));
}

TEST_F(ServingInstanceBindingTest, MissingInvocationAndUnknownSlotsFailClosed)
{
    ServingInstanceBinding unbound;
    EXPECT_FALSE(unbound.bindSlot(slots[0], {0, 64}, {}));
    EXPECT_FALSE(unbound.resolveSlot(slots[0], 0, 64, false));
    EXPECT_FALSE(binding.bindSlot(UINT32_MAX, {0, 64}, {}));
    EXPECT_FALSE(binding.resolveSlot(UINT32_MAX, 0, 64, false));
    EXPECT_FALSE(binding.installInvocation(invocation));
}

TEST_F(ServingInstanceBindingTest, DisjointMembersKeepDistinctAddressesAndWindows)
{
    for (auto slot : slots)
        ASSERT_TRUE(binding.bindSlot(slot, {0, 128}, {128, 16}));
    const auto left = binding.resolveSlot(slots[0], 64, 64, false);
    const auto right = binding.resolveSlot(slots[1], 64, 64, false);
    ASSERT_TRUE(left);
    ASSERT_TRUE(right);
    EXPECT_NE(left->address, right->address);
    EXPECT_FALSE(binding.resolveSlot(slots[0], 120, 16, false));
    EXPECT_TRUE(binding.resolveSlot(slots[1], 128, 16, true));
}

TEST_F(ServingInstanceBindingTest, OverflowAndCrossWindowTransfersAreRejected)
{
    EXPECT_FALSE(binding.bindSlot(slots[0], {UINT64_MAX - 8, 16}, {}));
    ASSERT_TRUE(binding.bindSlot(slots[0], {0, 64}, {}));
    EXPECT_FALSE(binding.resolveSlot(slots[0], 32, 64, false));
    EXPECT_FALSE(binding.resolveSlot(slots[0], 1, UINT64_MAX, false));
    EXPECT_TRUE(binding.resolveSlot(slots[0], 0, 64, false));
}

TEST_F(ServingInstanceBindingTest, CapacityAndResetPreserveTheLifecycleBoundary)
{
    ServingInstanceBinding limited(1);
    ASSERT_TRUE(limited.installInvocation(invocation));
    ASSERT_TRUE(limited.bindSlot(slots[0], {0, 64}, {}));
    EXPECT_FALSE(limited.bindSlot(slots[1], {0, 64}, {}));
    limited.reset(2, 3);
    EXPECT_TRUE(limited.empty());
    EXPECT_FALSE(limited.invocation());
    EXPECT_EQ(limited.requestId(), 2u);
    EXPECT_EQ(limited.requestGeneration(), 3u);
    ASSERT_TRUE(limited.installInvocation(invocation));
    ASSERT_TRUE(limited.bindSlot(slots[1], {0, 64}, {}));
    EXPECT_FALSE(limited.bindSlot(slots[0], {0, 64}, {}));
    ServingInstanceBinding empty(0);
    ASSERT_TRUE(empty.installInvocation(invocation));
    EXPECT_FALSE(empty.bindSlot(slots[0], {0, 64}, {}));
}

TEST_F(ServingInstanceBindingTest, MemberIdentityFollowsItsSlotAndAccessWindow)
{
    ASSERT_TRUE(binding.bindSlot(slots[0], {0, 128}, {128, 16}, ServingMemberIdentity{11, 3, 0}));
    ASSERT_TRUE(binding.bindSlot(slots[1], {0, 128}, {128, 16}, ServingMemberIdentity{22, 4, 1}));
    for (unsigned ordinal = 0; ordinal < 2; ++ordinal) {
        const auto access = binding.resolveSlot(slots[ordinal], 128, 16, true);
        ASSERT_TRUE(access);
        ASSERT_TRUE(access->member);
        EXPECT_EQ(access->member->request_id, ordinal ? 22u : 11u);
        EXPECT_EQ(access->member->generation, 3u + ordinal);
        EXPECT_EQ(access->member->ordinal, ordinal);
    }
    EXPECT_EQ(binding.memberRequests(), (std::vector<uint64_t>{11, 22}));
}

TEST_F(ServingInstanceBindingTest, DescriptorAccessUsesAdmittedRowsAndTheBoundSlot)
{
    for (unsigned ordinal = 0; ordinal < 2; ++ordinal)
        ASSERT_TRUE(binding.bindSlot(slots[ordinal], {0, 128}, {128, 16}, ServingMemberIdentity{11 + ordinal, 3, ordinal}));
    unsigned checked = 0;
    for (const auto &descriptor : fixture.program.transport.dma_descriptors) {
        if (!invocation->binding().hasEndpoint(descriptor.descriptor_id))
            continue;
        const bool source = descriptor.kind == mesh_abi::kDmaKindLOAD;
        if (!source && descriptor.kind != mesh_abi::kDmaKindSTORE)
            continue;
        const auto access = binding.resolveDescriptorRows(descriptor.descriptor_id, source);
        ASSERT_TRUE(access);
        ASSERT_TRUE(access->member);
        EXPECT_EQ(access->address, invocation->binding().endpointAddress(descriptor.descriptor_id, source));
        ++checked;
    }
    EXPECT_EQ(checked, 4u);
    EXPECT_FALSE(binding.resolveDescriptorRows(UINT32_MAX, true));
    binding.reset(2, 4);
    EXPECT_FALSE(binding.resolveDescriptorRows(1, true));
}
}

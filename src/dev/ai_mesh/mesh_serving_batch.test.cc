#include "dev/ai_mesh/mesh_serving_batch.hh"
#include "dev/ai_mesh/serving_mesh_executor_test.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::serving_test;

class ServingBatchFixture : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        program = loadFixture("tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb");
        admission = admittedFixture(program, arch);
        KvEdgeInputs edge;
        edge.tick = 10;
        for (size_t i = 0; i < requests.size(); ++i) {
            auto &request = requests[i];
            request = executionRequest(0x100300000 + i * 4096,
                "tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb");
            request.requestId += i;
            request.sessionId += i;
            request.kvHandle += i;
            request.inputAddress += i * 4096;
            request.metadataAddress = 0x100500000 + i * 4096;
            request.metadataCapacityBytes = 256;
            request.outputTokens = request.maxOutputTokens = 1;
            for (auto &binding : request.hostBindings) {
                if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                    binding.address = request.inputAddress;
                if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                    binding.address = request.outputAddress;
            }
            KvAdmissionIntent intent;
            intent.request_id = request.requestId;
            intent.session_id = request.sessionId;
            intent.kv_handle = request.kvHandle;
            intent.generation = request.generation;
            intent.contract_digest = request.contractDigest;
            intent.required_tokens_after_round = 9;
            edge.admissions.push_back(intent);
        }
        const auto admission = manager.commitEdge(edge);
        ASSERT_FALSE(admission.fatal) << admission.fatal.value_or("");
        ASSERT_TRUE(manager.hasPin(requests[0].requestId));
        ASSERT_FALSE(manager.hasPin(requests[1].requestId));
        edge = {};
        edge.tick = 11;
        const auto next_admission = manager.commitEdge(edge);
        ASSERT_FALSE(next_admission.fatal) << next_admission.fatal.value_or("");
        for (const auto &request : requests)
            ASSERT_TRUE(manager.hasPin(request.requestId));
        edge = {};
        edge.tick = 12;
        edge.view_arms = {{1, 0}, {2, 1}};
        const auto result = manager.commitEdge(edge);
        ASSERT_FALSE(result.fatal) << result.fatal.value_or("");
        for (size_t i = 0; i < requests.size(); ++i) {
            ASSERT_TRUE(manager.ownedSlot(requests[i].requestId));
            members[i] = {&requests[i], 0,
                *manager.ownedSlot(requests[i].requestId), result.views.at({i + 1, i})};
        }
    }
    const mesh_abi::AgentInstanceProfile &instance(uint16_t phase) const
    {
        return *std::find_if(program.transport.agent_instance_profiles.begin(),
            program.transport.agent_instance_profiles.end(), [phase](const auto &row) {
                return row.member_count == 2 && row.phase == phase;
            });
    }
    DecodedProgram program;
    RuntimeArch arch = test_support::testArch();
    std::shared_ptr<const MeshProgramAdmission> admission;
    MeshKvManager manager{geometry(), capacity()};
    std::array<NpuExecutionRequest, 2> requests;
    std::array<ServingBatchMember, 2> members;
};

TEST_F(ServingBatchFixture, FrozenTablesSeparateMembersAndIgnoreLaterRequestEdits)
{
    MeshServingBatch batch({1, 4, 9});
    requests[0].qos = 2;
    requests[1].qos = 201;
    std::string reason;
    ASSERT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePREFILL),
                           members.data(), members.size(), reason),
              MeshServingBatch::FreezeResult::Frozen) << reason;
    EXPECT_EQ(batch.memberBindingCount(), 4u);
    EXPECT_EQ(batch.weightBindingCount(), 1u);
    EXPECT_EQ(batch.intervalCount(), 9u);
    EXPECT_EQ(batch.executionBinding().effectiveQos(7), 201u);
    requests[1].qos = 0;
    EXPECT_EQ(batch.executionBinding().effectiveQos(7), 201u);
    EXPECT_EQ(ServingInstanceBinding{}.effectiveQos(7), 7u);
    for (size_t index = 0; index < batch.memberBindingCount(); ++index) {
        const auto &row = *batch.memberBinding(index);
        EXPECT_EQ(row.request_id, requests[row.ordinal].requestId);
        const bool write = row.kind == agent_abi::kBindingKindKV_EXTERNAL;
        const auto window = write ? row.write : row.read;
        const auto access = batch.executionBinding().resolveSlot(
            row.slot_id, window.begin, window.bytes, write);
        ASSERT_TRUE(access);
        ASSERT_TRUE(access->member);
        EXPECT_EQ(access->member->request_id, requests[row.ordinal].requestId);
        EXPECT_EQ(access->member->generation, requests[row.ordinal].generation);
        EXPECT_EQ(access->member->ordinal, row.ordinal);
        if (row.kind == agent_abi::kBindingKindHOST_INPUT)
            EXPECT_EQ(slotAddress(batch.executionBinding(), row.slot_id, 0, 128, false), requests[row.ordinal].inputAddress);
        else {
            ASSERT_TRUE(row.kv_view);
            EXPECT_EQ(slotAddress(batch.executionBinding(), row.slot_id, 0, 128, true), members[row.ordinal].owned_slot.address);
            EXPECT_FALSE(slotAddress(batch.executionBinding(), row.slot_id, 0, 1, false));
        }
    }
    const auto saved = *batch.memberBinding(0);
    requests[0].inputAddress += 8192;
    EXPECT_EQ(batch.memberBinding(0)->address, saved.address);
    EXPECT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePREFILL),
                           members.data(), members.size(), reason),
              MeshServingBatch::FreezeResult::Busy);
    EXPECT_EQ(batch.memberBinding(0)->address, saved.address);
}

TEST_F(ServingBatchFixture, EveryTableIsReservedBeforePublishingAnyEntry)
{
    for (auto limits : {ServingCapacityRequirement{0, 4, 9},
                        ServingCapacityRequirement{1, 3, 9},
                        ServingCapacityRequirement{1, 4, 8}}) {
        MeshServingBatch batch(limits);
        std::string reason;
        EXPECT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePREFILL),
                               members.data(), members.size(), reason),
                  MeshServingBatch::FreezeResult::NoCapacity);
        EXPECT_FALSE(batch.frozen());
        EXPECT_EQ(batch.memberBindingCount(), 0u);
        EXPECT_EQ(batch.weightBindingCount(), 0u);
        EXPECT_EQ(batch.intervalCount(), 0u);
        EXPECT_TRUE(batch.executionBinding().empty());
    }
}

TEST_F(ServingBatchFixture, BadRankWeightOrPhysicalAliasPublishesNothing)
{
    for (unsigned mode = 0; mode < 4; ++mode) {
        auto candidates = members;
        auto second = requests[1];
        candidates[1].request = &second;
        if (mode == 0)
            candidates[1].logical_source_rank = 1;
        if (mode == 1)
            for (auto &binding : second.hostBindings)
                if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL)
                    binding.address += 64;
        if (mode == 2)
            second.metadataAddress = requests[0].inputAddress;
        if (mode == 3)
            candidates[1].owned_slot.address = members[0].owned_slot.address;
        MeshServingBatch batch({1, 4, 9});
        std::string reason;
        EXPECT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePREFILL),
                               candidates.data(), candidates.size(), reason),
                  MeshServingBatch::FreezeResult::Invalid) << mode;
        EXPECT_FALSE(reason.empty());
        EXPECT_FALSE(batch.memberBinding(0));
        EXPECT_FALSE(batch.weightBinding(0));
        EXPECT_FALSE(batch.interval(0));
        EXPECT_TRUE(batch.executionBinding().empty());
    }
}

TEST_F(ServingBatchFixture, PublishAuditsOwnedSlotsWithoutCreatingKvOperands)
{
    for (auto &member : members)
        member.kv_view.reset();
    MeshServingBatch batch({1, 4, 9});
    std::string reason;
    ASSERT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePUBLISH),
                           members.data(), members.size(), reason),
              MeshServingBatch::FreezeResult::Frozen) << reason;
    EXPECT_EQ(batch.memberBindingCount(), 2u);
    EXPECT_EQ(batch.intervalCount(), 9u);
    const auto storage = batch.memberBinding(0);
    size_t owned_slots = 0;
    for (size_t i = 0; i < batch.intervalCount(); ++i) {
        const auto &interval = *batch.interval(i);
        if (interval.kind != BatchIntervalKind::Kv) {
            EXPECT_FALSE(interval.owned_slot);
            continue;
        }
        ASSERT_TRUE(interval.owned_slot);
        const auto &request = requests[interval.ordinal];
        EXPECT_EQ(interval.owned_slot->request_id, request.requestId);
        EXPECT_EQ(interval.owned_slot->session_id, request.sessionId);
        EXPECT_EQ(interval.owned_slot->kv_handle, request.kvHandle);
        EXPECT_EQ(interval.owned_slot->generation, request.generation);
        EXPECT_EQ(interval.owned_slot->address, interval.address);
        EXPECT_EQ(interval.owned_slot->bytes, interval.end - interval.address);
        ++owned_slots;
    }
    EXPECT_EQ(owned_slots, members.size());
    for (size_t i = 0; i < batch.memberBindingCount(); ++i) {
        EXPECT_EQ(batch.memberBinding(i)->kind, agent_abi::kBindingKindHOST_OUTPUT);
        EXPECT_FALSE(batch.memberBinding(i)->kv_view);
    }
    batch.release();
    EXPECT_FALSE(batch.frozen());
    ASSERT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePUBLISH),
                           members.data(), members.size(), reason),
              MeshServingBatch::FreezeResult::Frozen) << reason;
    EXPECT_EQ(batch.memberBinding(0), storage);
}

TEST_F(ServingBatchFixture, KvErrorDescriptorsHaveFrozenDenseMemberIdentity)
{
    MeshServingBatch batch({1, 4, 9});
    for (auto &member : members)
        member.phase_ordinal = 7;
    std::string reason;
    ASSERT_EQ(batch.freeze(admission, instance(mesh_abi::kPhasePREFILL),
                           members.data(), members.size(), reason),
              MeshServingBatch::FreezeResult::Frozen) << reason;
    std::vector<mesh_abi::ErrorSourceKey> sources;
    for (const auto &descriptor : program.transport.dma_descriptors) {
        const auto source = batch.executionBinding().kvDescriptorSource(
            descriptor.descriptor_id, mesh_abi::kMoeErrorClassDMA_AXI, 256, 91);
        if (source)
            sources.push_back(*source);
    }
    ASSERT_EQ(sources.size(), 2u);
    std::sort(sources.begin(), sources.end(), [](const auto &a, const auto &b) {
        return a.ordinal < b.ordinal;
    });
    for (size_t i = 0; i < sources.size(); ++i) {
        EXPECT_EQ(sources[i].domain, mesh_abi::kMeshObjectDomainKV_RUNTIME);
        EXPECT_EQ(sources[i].object_kind, mesh_abi::kErrorSourceObjectKindDESCRIPTOR);
        EXPECT_EQ(sources[i].core_id_or_ffff, 256u);
        EXPECT_EQ(sources[i].region_group_id, 7u);
        EXPECT_EQ(sources[i].region_id, i);
        EXPECT_EQ(sources[i].ordinal, i + 1);
        EXPECT_EQ(sources[i].generation, requests[i].generation);
        EXPECT_EQ(agent_abi::rdU64(sources[i].aux_key.data()), requests[i].requestId);
        EXPECT_EQ(agent_abi::rdU64(sources[i].aux_key.data() + 8), 91u);
    }
}

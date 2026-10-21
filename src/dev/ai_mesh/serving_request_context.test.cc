#include <gtest/gtest.h>

#include <array>
#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <string>
#include <stdexcept>
#include <utility>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_program_test_support.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/serving_request_context.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"

namespace
{

using namespace gem5::ai_mesh;
using namespace mesh_abi;

DecodedProgram loadFixture(const std::string &path)
{
    std::ifstream stream(path, std::ios::binary);
    EXPECT_TRUE(stream.good()) << path;
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(stream)),
                               std::istreambuf_iterator<char>());
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(bytes, program, error))
        << error.code << ": " << error.message;
    return program;
}

KvGeometry geometry()
{
    KvGeometry geo;
    geo.region_base = 0x100000;
    geo.slot_bytes = 4096;
    geo.slot_alignment = 4096;
    geo.max_sessions = 2;
    geo.bytes_per_token = 16;
    return geo;
}

KvCapacity capacity()
{
    KvCapacity cap;
    cap.record_entries = 4;
    cap.tombstone_entries = 2;
    cap.admission_wait_entries = 2;
    cap.release_waiter_entries = 2;
    return cap;
}

ServingRequestIdentity identity(uint32_t path_kind = 0)
{
    ServingRequestIdentity id;
    id.request_id = 1;
    id.session_id = 11;
    id.kv_handle = 11;
    id.generation = 1;
    id.program_id = 1;
    id.profile_id = 1;
    id.path_kind = path_kind;
    id.contract_digest.fill(0x5c);
    const DecodedProgram fixture = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    const mesh_abi::AgentRequestProfile &bound =
        fixture.transport.agent_request_profiles.at(0);
    id.requested_profile_key = requestProfileKey(
        bound, test_support::profileBaseDigest(fixture));
    id.input_bytes = bound.full_input_dma_bytes;
    id.input_tokens = bound.full_input_tokens;
    id.output_tokens = bound.output_tokens;
    id.output_capacity_bytes = bound.host_output_bytes;
    id.kv_bytes_per_token = bound.kv_bytes_per_token;
    return id;
}

std::vector<bool> prefixBitmap(uint32_t tokens, bool ok = true)
{
    return std::vector<bool>(tokens, ok);
}

std::vector<bool> partialBitmap(uint32_t tokens, uint32_t ok_prefix)
{
    std::vector<bool> bits(tokens, false);
    for (uint32_t index = 0; index < ok_prefix && index < tokens; ++index)
        bits[index] = true;
    return bits;
}

}

TEST(ServingRequestContext, ResolvesTheExactPhaseChain)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    ASSERT_EQ(context.plan().size(), 4u);
    EXPECT_EQ(context.plan()[0].phase, kPhasePREFILL);
    EXPECT_EQ(context.plan()[1].phase, kPhaseDECODE);
    EXPECT_EQ(context.plan()[2].phase, kPhaseDECODE);
    EXPECT_EQ(context.plan()[3].phase, kPhasePUBLISH);
    EXPECT_EQ(context.plan()[0].instance_profile_id, 11u);
    EXPECT_EQ(context.plan()[1].instance_profile_id, 12u);
    EXPECT_EQ(context.plan()[2].instance_profile_id, 13u);
    EXPECT_EQ(context.plan()[3].instance_profile_id, 14u);
    EXPECT_EQ(context.plan()[0].kv_tokens_before, 0u);
    EXPECT_EQ(context.plan()[1].kv_tokens_before, 8u);
    EXPECT_EQ(context.plan()[2].kv_tokens_before, 9u);
    EXPECT_EQ(context.plan()[3].kv_tokens_before, 10u);
    EXPECT_EQ(context.plan()[0].cached_tokens_after, 8u);
    EXPECT_EQ(context.plan()[1].cached_tokens_after, 9u);
    EXPECT_EQ(context.plan()[2].cached_tokens_after, 10u);
    EXPECT_EQ(context.plan()[3].cached_tokens_after, 10u);
    EXPECT_EQ(context.plan()[1].kv_read_bytes_per_member, 128u);
    EXPECT_EQ(context.plan()[2].kv_read_bytes_per_member, 144u);
    EXPECT_EQ(context.plan()[3].kv_write_bytes_per_member, 0u);
    EXPECT_EQ(context.requiredTokensAfterRound(), 10u);
    EXPECT_FALSE(context.finished());
}

TEST(ServingRequestContext, AdmissionIntentCarriesTheContractIdentity)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    const KvAdmissionIntent intent = context.admissionIntent(1000);
    EXPECT_EQ(intent.request_id, 1u);
    EXPECT_EQ(intent.session_id, 11u);
    EXPECT_EQ(intent.kv_handle, 11u);
    EXPECT_EQ(intent.generation, 1u);
    EXPECT_EQ(intent.deadline_or_max, UINT64_MAX);
    EXPECT_EQ(intent.flags, 0u);
    EXPECT_EQ(intent.required_cached_tokens, 0u);
    EXPECT_EQ(intent.required_tokens_after_round, 10u);
    EXPECT_TRUE(intent.contract_digest == identity().contract_digest);
}

TEST(ServingRequestContext, DrivesOneGenerateThroughTheKvManager)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    MeshKvManager manager(geometry(), capacity());

    KvEdgeInputs admission;
    admission.tick = 100;
    admission.admissions.push_back(context.admissionIntent(100));
    const KvEdgeResult admitted = manager.commitEdge(admission);
    ASSERT_FALSE(admitted.fatal);
    ASSERT_EQ(admitted.admissions.at(1), KvAdmissionOutcome::Claimed);
    ASSERT_EQ(admitted.promotions.at(1), KvPromotionOutcome::Pinned);
    ASSERT_TRUE(admitted.handoffs.count(1) == 1u);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 0u);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);

    KvEdgeInputs prefill;
    prefill.tick = 200;
    context.beginPhase(prefill);
    ASSERT_EQ(prefill.view_arms.size(), 1u);
    EXPECT_TRUE(prefill.core_starts.empty());
    ASSERT_EQ(prefill.append_arms.size(), 1u);
    EXPECT_EQ(prefill.append_arms[0].base_tokens, 0u);
    EXPECT_EQ(prefill.append_arms[0].append_tokens, 8u);
    const KvEdgeResult prefilled = manager.commitEdge(prefill);
    ASSERT_FALSE(prefilled.fatal);
    KvEdgeInputs start;
    start.tick = 210;
    start.core_starts.push_back(1);
    ASSERT_FALSE(manager.commitEdge(start).fatal);
    ASSERT_TRUE(prefilled.views.count({1, 0}) == 1u);
    EXPECT_EQ(prefilled.views.at({1, 0}).valid_bytes_at_arm, 0u);
    record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    ASSERT_TRUE(record->slot_id.has_value());
    EXPECT_EQ(record->view_epoch, 1u);

    context.noteCoreDrain();
    EXPECT_FALSE(context.joinSatisfied());
    context.noteAppendTerminal(prefixBitmap(8), std::nullopt);
    EXPECT_TRUE(context.joinSatisfied());
    KvEdgeInputs commit;
    commit.tick = 300;
    commit.append_terminals.push_back(KvAppendTerminal{
        1, prefixBitmap(8), std::nullopt});
    const KvEdgeResult committed = manager.commitEdge(commit);
    ASSERT_FALSE(committed.fatal);
    record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 8u);
    EXPECT_EQ(record->valid_bytes, 128u);
    ASSERT_TRUE(context.commitPhase(reason)) << reason;
    EXPECT_EQ(context.phaseIndex(), 1u);

    for (uint32_t expected_cursor : {8u, 9u}) {
        const ServingPhaseStep &step = context.step();
        EXPECT_EQ(step.kv_tokens_before, expected_cursor);
        KvEdgeInputs edge;
        edge.tick = 400 + expected_cursor;
        context.beginPhase(edge);
        EXPECT_TRUE(edge.core_starts.empty());
        ASSERT_EQ(edge.view_arms.size(), 1u);
        ASSERT_EQ(edge.append_arms.size(), 1u);
        EXPECT_EQ(edge.append_arms[0].base_tokens, expected_cursor);
        EXPECT_EQ(edge.append_arms[0].append_tokens, 1u);
        const KvEdgeResult applied = manager.commitEdge(edge);
        ASSERT_FALSE(applied.fatal);
        EXPECT_EQ(applied.views.at({1, 0}).valid_bytes_at_arm,
                  expected_cursor * 16u);
        record = manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        EXPECT_EQ(record->cached_tokens, expected_cursor);
        context.noteCoreDrain();
        context.noteAppendTerminal(prefixBitmap(1), std::nullopt);
        KvEdgeInputs terminal;
        terminal.tick = 500 + expected_cursor;
        terminal.append_terminals.push_back(KvAppendTerminal{
            1, prefixBitmap(1), std::nullopt});
        const KvEdgeResult done = manager.commitEdge(terminal);
        ASSERT_FALSE(done.fatal);
        record = manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        EXPECT_EQ(record->cached_tokens, expected_cursor + 1);
        ASSERT_TRUE(context.commitPhase(reason)) << reason;
    }

    ASSERT_FALSE(context.finished());
    EXPECT_EQ(context.step().phase, kPhasePUBLISH);
    KvEdgeInputs publish;
    publish.tick = 600;
    context.beginPhase(publish);
    EXPECT_TRUE(publish.view_arms.empty());
    EXPECT_TRUE(publish.append_arms.empty());
    EXPECT_TRUE(publish.core_starts.empty());
    const KvEdgeResult applied = manager.commitEdge(publish);
    ASSERT_FALSE(applied.fatal);
    context.noteCoreDrain();
    EXPECT_TRUE(context.joinSatisfied());
    ASSERT_TRUE(context.commitPhase(reason)) << reason;
    EXPECT_TRUE(context.finished());

    KvEdgeInputs release;
    release.tick = 700;
    release.owner_terminals.push_back(context.ownerTerminal(
        KvTerminalStatus::Success));
    const KvEdgeResult released = manager.commitEdge(release);
    ASSERT_FALSE(released.fatal);
    ASSERT_TRUE(released.terminals.count(1) == 1u);
    EXPECT_EQ(released.terminals.at(1).cached_tokens, 10u);
    EXPECT_EQ(released.terminals.at(1).valid_bytes, 160u);
    EXPECT_FALSE(manager.hasPin(1));
    EXPECT_TRUE(manager.validate().empty());
}

TEST(ServingRequestContext, JoinNeedsBothCoreDrainAndAppendTerminal)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    MeshKvManager manager(geometry(), capacity());
    KvEdgeInputs admission;
    admission.tick = 100;
    admission.admissions.push_back(context.admissionIntent(100));
    ASSERT_FALSE(manager.commitEdge(admission).fatal);
    KvEdgeInputs prefill;
    prefill.tick = 200;
    context.beginPhase(prefill);
    ASSERT_FALSE(manager.commitEdge(prefill).fatal);
    EXPECT_FALSE(context.joinSatisfied());
    context.noteCoreDrain();
    EXPECT_FALSE(context.joinSatisfied());
    context.noteAppendTerminal(prefixBitmap(8), std::nullopt);
    EXPECT_TRUE(context.joinSatisfied());
    ASSERT_TRUE(context.commitPhase(reason)) << reason;
    EXPECT_EQ(context.phaseIndex(), 1u);
}

TEST(ServingRequestContext, SameEdgeAppendAndDrainCommitExactlyOnce)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    for (const bool drainFirst : {true, false}) {
        ServingRequestContext context(program, identity());
        std::string reason;
        ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
        MeshKvManager manager(geometry(), capacity());
        KvEdgeInputs admission;
        admission.tick = 100;
        admission.admissions.push_back(context.admissionIntent(100));
        ASSERT_FALSE(manager.commitEdge(admission).fatal);
        KvEdgeInputs prefill;
        prefill.tick = 200;
        context.beginPhase(prefill);
        ASSERT_FALSE(manager.commitEdge(prefill).fatal);
        if (drainFirst) {
            context.noteCoreDrain();
            EXPECT_FALSE(context.joinSatisfied());
            context.noteAppendTerminal(prefixBitmap(8), std::nullopt);
        } else {
            context.noteAppendTerminal(prefixBitmap(8), std::nullopt);
            EXPECT_FALSE(context.joinSatisfied());
            context.noteCoreDrain();
        }
        EXPECT_TRUE(context.joinSatisfied());
        KvEdgeInputs terminal;
        terminal.tick = 200;
        terminal.append_terminals.push_back(KvAppendTerminal{
            1, prefixBitmap(8), std::nullopt});
        ASSERT_FALSE(manager.commitEdge(terminal).fatal);
        ASSERT_TRUE(context.commitPhase(reason)) << reason;
        EXPECT_EQ(context.phaseIndex(), 1u);
        EXPECT_FALSE(context.joinSatisfied());
        EXPECT_EQ(manager.findRecord(11, 11)->cached_tokens, 8u);
    }
}

TEST(ServingRequestContext, PartialAppendPrefixBlocksTheNextPhase)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    MeshKvManager manager(geometry(), capacity());
    KvEdgeInputs admission;
    admission.tick = 100;
    admission.admissions.push_back(context.admissionIntent(100));
    ASSERT_FALSE(manager.commitEdge(admission).fatal);
    KvEdgeInputs prefill;
    prefill.tick = 200;
    context.beginPhase(prefill);
    ASSERT_FALSE(manager.commitEdge(prefill).fatal);
    context.noteCoreDrain();
    context.noteAppendTerminal(partialBitmap(8, 3), std::nullopt);
    EXPECT_FALSE(context.joinSatisfied());
    KvEdgeInputs terminal;
    terminal.tick = 300;
    terminal.append_terminals.push_back(KvAppendTerminal{
        1, partialBitmap(8, 3), std::nullopt});
    const KvEdgeResult committed = manager.commitEdge(terminal);
    ASSERT_FALSE(committed.fatal);
    const KvRecord *record = manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 3u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(context.commitPhase(reason));
    EXPECT_FALSE(reason.empty());
    EXPECT_EQ(context.phaseIndex(), 0u);
}

TEST(ServingRequestContext, RejectsAPathThatIsNotDeclaredReachable)
{
    const DecodedProgram program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    ServingRequestContext context(program, identity());
    std::string reason;
    EXPECT_FALSE(context.resolve(KvPath::KvReuse, reason));
    EXPECT_FALSE(reason.empty());
}

TEST(ServingRequestContext, RepairUsesFullHeaderAndAppendsAfterTheResidentPrefix)
{
    auto program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    auto &profile = program.transport.agent_request_profiles.at(0);
    profile.path_mask = 6;
    profile.expected_cached_tokens = 10;
    profile.full_input_tokens = 18;
    profile.input_binding_bytes = profile.full_input_dma_bytes = 256;
    for (auto &instance : program.transport.agent_instance_profiles) {
        instance.path_kind = kPathKindKV_REUSE;
        instance.kv_tokens_before += 10;
        if (instance.phase != kPhasePUBLISH)
            instance.kv_read_bytes_per_member += 160;
    }
    auto id = identity();
    id.repair_round = 1;
    id.kv_flags = agent_abi::kSqFlagsALLOW_REPREFILL;
    id.cached_tokens = 10;
    id.input_tokens = 18;
    id.input_bytes = 256;
    id.requested_profile_key = requestProfileKey(profile, test_support::profileBaseDigest(program));
    ServingRequestContext context(program, id);
    std::string reason;
    ASSERT_TRUE(context.validate(reason)) << reason;
    EXPECT_EQ(context.admissionIntent(1).flags, agent_abi::kSqFlagsALLOW_REPREFILL);
    ASSERT_TRUE(context.resolve(KvPath::KvReuse, reason)) << reason;
    ASSERT_EQ(context.plan().size(), 4u);
    EXPECT_EQ(context.plan()[0].kv_tokens_before, 10u);
    EXPECT_EQ(context.plan()[0].valid_tokens_per_member, 8u);
    EXPECT_EQ(context.plan()[0].cached_tokens_after, 18u);
    EXPECT_EQ(context.plan()[0].kv_read_bytes_per_member, 160u);
    EXPECT_EQ(context.plan()[0].host_input_dma_bytes_per_member, 128u);
    EXPECT_EQ(context.plan().back().cached_tokens_after, 20u);
    id.input_bytes = 128;
    ServingRequestContext delta_header(program, id);
    EXPECT_FALSE(delta_header.validate(reason));
}

namespace
{
std::pair<DecodedProgram, ServingRequestIdentity>
prefillOnlyFixture()
{
    auto program = loadFixture(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb");
    auto id = identity();
    auto &request = program.transport.agent_request_profiles.at(0);
    request.flags = 0;
    request.path_mask = 1;
    request.primary_kv_symbol_id = 0;
    request.kv_bytes_per_token = 0;
    request.output_tokens = 0;
    auto &instances = program.transport.agent_instance_profiles;
    instances.erase(std::remove_if(instances.begin(), instances.end(),
        [](const auto &row) { return row.phase == kPhaseDECODE; }), instances.end());
    for (auto &instance : instances) {
        instance.primary_kv_symbol_id = 0;
        instance.kv_tokens_before = 0;
        instance.kv_read_bytes_per_member = 0;
        instance.kv_write_bytes_per_member = 0;
    }
    for (auto &member : program.transport.agent_instance_member_bindings)
        member.static_kv_symbol_id = 0;
    auto &mappings = program.transport.agent_instance_tensor_bindings;
    mappings.erase(std::remove_if(mappings.begin(), mappings.end(), [&](const auto &row) {
        return row.binding_kind == agent_abi::kBindingKindKV_EXTERNAL ||
            std::none_of(instances.begin(), instances.end(), [&](const auto &instance) {
                return instance.instance_profile_id == row.instance_profile_id;
            });
    }), mappings.end());
    auto &requirements = program.transport.agent_request_binding_requirements;
    requirements.erase(std::remove_if(requirements.begin(), requirements.end(), [](const auto &row) {
        return row.binding_kind == agent_abi::kBindingKindKV_EXTERNAL;
    }), requirements.end());
    id.kv_handle = 0;
    id.generation = 0;
    id.kv_bytes_per_token = 0;
    id.output_tokens = 0;
    id.requested_profile_key = requestProfileKey(request, test_support::profileBaseDigest(program));
    return {std::move(program), id};
}
}

TEST(ServingRequestContext, PrefillOnlyDrainsWithoutKvEdges)
{
    auto [program, id] = prefillOnlyFixture();
    ServingRequestContext context(program, id);
    std::string reason;
    ASSERT_TRUE(context.resolve(KvPath::InitialPrefill, reason)) << reason;
    EXPECT_FALSE(context.usesKv());
    ASSERT_EQ(context.plan().size(), 2u);
    EXPECT_EQ(context.plan()[0].phase, kPhasePREFILL);
    EXPECT_EQ(context.plan()[0].valid_tokens_per_member, id.input_tokens);
    EXPECT_EQ(context.plan()[1].phase, kPhasePUBLISH);
    EXPECT_EQ(context.requiredTokensAfterRound(), 0u);
    MeshKvManager manager(geometry(), capacity());
    for (size_t phase = 0; phase < context.plan().size(); ++phase) {
        EXPECT_EQ(context.step().kv_tokens_before, 0u);
        EXPECT_EQ(context.step().cached_tokens_after, 0u);
        KvEdgeInputs edge;
        edge.tick = 100 * (phase + 1);
        context.beginPhase(edge);
        EXPECT_TRUE(edge.view_arms.empty());
        EXPECT_TRUE(edge.append_arms.empty());
        EXPECT_FALSE(manager.commitEdge(edge).fatal);
        EXPECT_EQ(manager.findRecord(id.session_id, id.kv_handle), nullptr);
        EXPECT_FALSE(context.joinSatisfied());
        EXPECT_FALSE(context.commitPhase(reason));
        context.noteCoreDrain();
        EXPECT_TRUE(context.joinSatisfied());
        ASSERT_TRUE(context.commitPhase(reason)) << reason;
    }
    EXPECT_TRUE(context.finished());
    EXPECT_THROW(context.admissionIntent(100), std::logic_error);
    EXPECT_THROW(context.ownerTerminal(KvTerminalStatus::Success), std::logic_error);
    EXPECT_THROW(context.rollbackTerminal(KvTerminalStatus::Error, KvRollbackToken{}), std::logic_error);
}

TEST(ServingRequestContext, PrefillOnlyRejectsKvAndGeneratedTokenState)
{
    for (unsigned mutation = 0; mutation < 16; ++mutation) {
        auto [program, id] = prefillOnlyFixture();
        auto &request = program.transport.agent_request_profiles.at(0);
        switch (mutation) {
          case 0: id.kv_handle = 1; break;
          case 1: id.generation = 1; break;
          case 2: id.cached_tokens = 1; break;
          case 3: id.repair_round = 1; break;
          case 4: id.kv_flags = agent_abi::kSqFlagsALLOW_REPREFILL; break;
          case 5: id.kv_bytes_per_token = request.kv_bytes_per_token = 16; break;
          case 6: id.output_tokens = request.output_tokens = 1; break;
          case 7: program.transport.agent_instance_profiles[0].kv_write_bytes_per_member = 16; break;
          case 8: program.transport.agent_instance_profiles[1].kv_tokens_before = 8; break;
          case 9: request.primary_kv_symbol_id = 7; break;
          case 10: request.flags = 2; break;
          case 11: request.flags = kAgentRequestFlagsHAS_KV; break;
          case 12: program.transport.agent_instance_tensor_bindings[0].binding_kind = agent_abi::kBindingKindKV_EXTERNAL; break;
          case 13: program.transport.agent_instance_profiles.push_back(program.transport.agent_instance_profiles[0]);
              program.transport.agent_instance_profiles.back().phase = kPhaseDECODE; break;
          case 14: program.transport.agent_instance_member_bindings[0].static_kv_symbol_id = 9; break;
          case 15: program.transport.agent_request_binding_requirements[0].binding_kind = agent_abi::kBindingKindKV_EXTERNAL; break;
        }
        if (mutation != 10)
            id.requested_profile_key = requestProfileKey(request, test_support::profileBaseDigest(program));
        ServingRequestContext context(program, id);
        std::string reason;
        EXPECT_FALSE(context.resolve(KvPath::InitialPrefill, reason)) << mutation;
        EXPECT_FALSE(reason.empty()) << mutation;
    }
}

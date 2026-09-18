#include <gtest/gtest.h>

#include <array>
#include <cstdint>
#include <fstream>
#include <string>
#include <utility>
#include <vector>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/serving_host_bindings.hh"
#include "dev/ai_mesh/serving_mesh_executor.hh"

namespace
{

using namespace gem5::ai_mesh;
using namespace mesh_abi;

class RecordingDispatch : public ServingMeshDispatch
{
  public:
    bool installSelectors(
        const std::vector<std::pair<uint32_t, uint32_t>> &value,
        std::string &reason) override
    {
        if (installed) {
            reason = "selectors already installed";
            return false;
        }
        selectors = value;
        installed = true;
        return true;
    }

    void failCurrentInstance() override { fails++; }

    void bindInstance(const ServingInstanceBinding &binding) override
    { instance_bindings.push_back(binding); }

    bool canAccessKv(uint64_t, uint64_t) const override
    { return kv_reachable; }

    void bindFillContent(const ServingFillBinding &binding) override
    {
        fills.push_back(binding);
        publish_content = binding.content;
    }

    void bindSemanticDigest(const std::array<uint8_t, 32> &digest) override
    { semantic_digest = digest; }

    void beginFirstInstance() override
    {
        begins++;
        if (on_start)
            on_start();
    }

    void cancelPendingInstance() override { cancels++; }

    void resumeInstance() override
    {
        resumes++;
        if (on_start)
            on_start();
    }

    std::vector<std::pair<uint32_t, uint32_t>> selectors;
    bool installed = false;
    bool kv_reachable = true;
    uint32_t begins = 0;
    uint32_t cancels = 0;
    uint32_t fails = 0;
    uint32_t resumes = 0;
    std::function<void()> on_start;
    std::vector<uint8_t> publish_content;
    std::vector<ServingFillBinding> fills;
    std::vector<ServingInstanceBinding> instance_bindings;
    std::array<uint8_t, 32> semantic_digest = {};
};

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

ServingRequestIdentity identity(
    const std::string &fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb")
{
    ServingRequestIdentity id;
    id.request_id = 1;
    id.session_id = 11;
    id.kv_handle = 11;
    id.generation = 1;
    id.program_id = 1;
    id.profile_id = 1;
    id.path_kind = kPathKindINITIAL_PREFILL;
    id.contract_digest.fill(0x5c);
    const DecodedProgram program = loadFixture(fixture);
    const mesh_abi::AgentRequestProfile &profile =
        program.agent_request_profiles.at(0);
    id.requested_profile_key = requestProfileKey(
        profile, programProfileKeyBaseDigest(program));
    id.input_bytes = profile.full_input_dma_bytes;
    id.input_tokens = profile.full_input_tokens;
    id.output_tokens = profile.output_tokens;
    id.output_capacity_bytes = profile.host_output_bytes;
    id.kv_bytes_per_token = profile.kv_bytes_per_token;
    return id;
}

uint32_t kvTensorId(const DecodedProgram &program)
{
    uint32_t symbol = 0;
    for (const auto &request : program.agent_request_profiles)
        symbol = request.primary_kv_symbol_id;
    for (const auto &relocation : program.relocations)
        if (relocation.symbol_sid == symbol)
            return relocation.tensor_id;
    return 0;
}

struct KvStore
{
    uint32_t descriptor_id = 0;
    uint32_t command_id = 0;
};

std::vector<KvStore> kvStoreCommands(const DecodedProgram &program)
{
    const uint32_t tensor = kvTensorId(program);
    std::vector<KvStore> out;
    for (const auto &descriptor : program.descriptors)
        if (descriptor.kind == kDmaKindSTORE &&
                descriptor.dst.tensor_id == tensor)
            out.push_back({descriptor.descriptor_id, descriptor.command_id});
    return out;
}

struct Harness
{
    Harness()
        : Harness("tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb")
    {}

    explicit Harness(const std::string &fixture)
        : program(loadFixture(fixture)),
          manager(geometry(), capacity()),
          executor(program, manager)
    {
        executor.attachDispatch(dispatch);
        dispatch.on_start = [this]() {
            executor.onInstanceStarted(static_cast<uint32_t>(executor.phasesCommitted()) + 1, 1, 1, 150);
        };
        executor.setDigestSource([](const NpuExecutionRequest &request,
                                    uint64_t bytes) {
            std::array<uint8_t, 32> digest{};
            for (size_t i = 0; i < digest.size(); ++i)
                digest[i] = static_cast<uint8_t>(0x40 + i + (bytes & 0x1f) +
                                                 request.requestId);
            return digest;
        });
    }

    uint64_t currentInstance() const
    { return static_cast<uint64_t>(executor.phasesCommitted()) + 1; }

    DecodedProgram program;
    MeshKvManager manager;
    RecordingDispatch dispatch;
    ServingMeshExecutor executor;
};

}

class RecordingSink : public NpuCompletionSink
{
  public:
    void onCoreStart(uint64_t) override { core_starts++; }
    void onRequestComplete(const NpuExecutionRequest &,
                           const NpuExecutionCompletion &value) override
    {
        completions++;
        last = value;
        if (on_complete)
            on_complete(value);
    }

    uint32_t core_starts = 0;
    uint32_t completions = 0;
    NpuExecutionCompletion last;
    std::function<void(const NpuExecutionCompletion &)> on_complete;
};

NpuExecutionRequest executionRequest(
    uint64_t output_address = 0x300000,
    const std::string &fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb")
{
    NpuExecutionRequest request;
    request.requestId = 1;
    request.sessionId = 11;
    request.kvHandle = 11;
    request.generation = 1;
    request.programId = 1;
    request.profileId = 1;
    request.pathKind = kPathKindINITIAL_PREFILL;
    request.inputAddress = 0x100200000;
    request.outputAddress = output_address;
    request.outputTokens = 2;
    request.maxOutputTokens = 2;
    const ServingRequestIdentity source = identity(fixture);
    request.requestedProfileKey = source.requested_profile_key;
    request.inputBytes = source.input_bytes;
    request.inputTokens = source.input_tokens;
    request.outputCapacityBytes = source.output_capacity_bytes;
    request.contractDigest = source.contract_digest;
    const DecodedProgram program = loadFixture(fixture);
    std::vector<mesh_abi::AgentRequestBindingRequirement> requirements;
    for (const auto &requirement : program.agent_request_binding_requirements)
        if (requirement.request_program_id == request.programId &&
                requirement.request_profile_id == request.profileId)
            requirements.push_back(requirement);
    request.hostBindings = expectedHostBindings(
        requirements, request.inputAddress, request.inputBytes,
        request.outputAddress, request.outputCapacityBytes,
        geometry().slot_bytes);
    return request;
}

void driveOneGenerate(Harness &harness, const std::vector<KvStore> &stores,
                      uint32_t first = 1)
{
    // Phases 1..3 each issue one KV store; phase 4 publishes without one.
    for (uint32_t phase = first; phase <= 3; ++phase) {
        const KvStore &store = stores.at(phase - 1);
        const uint64_t tick = 100 + 100 * phase;
        harness.executor.onDmaAccepted(store.descriptor_id, store.command_id,
                                       phase, 1, 1, tick);
        harness.executor.onDmaTerminal(store.descriptor_id, store.command_id, phase == 1 ? 128 : 16, 0, phase, 1, 1,
                                           tick + 10,
                                           std::vector<DmaCommittedSegment>{{0, 0, uint64_t(phase == 1 ? 128 : 16)}});
        harness.executor.onInstanceSettled(phase, 1, 1, false, tick + 20);
    }
    harness.executor.onInstanceSettled(4, 1, 1, false, 520);
}

TEST(ServingMeshExecutor, TerminalSnapshotPrecedesCompletion)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    uint32_t pin_count_at_completion = 0;
    uint32_t claim_count_at_completion = 0;
    uint32_t kv_dma_at_completion = 0;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        pin_count_at_completion = record->pin_count;
        claim_count_at_completion = record->admission_claim_count;
        kv_dma_at_completion = record->outstanding_kv_dma;
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission admission =
        harness.executor.submit(executionRequest());
    ASSERT_TRUE(admission.admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(sink.completions, 1u);
    EXPECT_EQ(pin_count_at_completion, 0u);
    EXPECT_EQ(claim_count_at_completion, 0u);
    EXPECT_EQ(kv_dma_at_completion, 0u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_EQ(observed->request_id, 1u);
    EXPECT_EQ(observed->status, KvTerminalStatus::Success);
    EXPECT_EQ(sink.last.kvTerminal->cached_tokens, 10u);
    EXPECT_EQ(sink.last.kvTerminal->valid_bytes, 160u);
    EXPECT_EQ(sink.last.phaseInstances, 4u);
    EXPECT_EQ(sink.last.requestStartTick, 100u);
    EXPECT_EQ(sink.last.terminalTick, 520u);
    EXPECT_EQ(harness.executor.terminalTick(), 520u);
    EXPECT_LT(sink.last.terminalTick, 521u);
}

TEST(ServingMeshExecutor, TerminalSnapshotSurvivesLaterKvEdges)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(sink.completions, 1u);
    ASSERT_TRUE(harness.executor.kvTerminal().has_value());
    const KvTerminalSnapshot frozen = *harness.executor.kvTerminal();
    const uint64_t terminal_tick = harness.executor.terminalTick();

    MeshKvManager &manager = harness.manager;
    KvEdgeInputs later;
    later.tick = 900;
    KvAdmissionIntent next;
    next.request_id = next.session_id = next.kv_handle = 12;
    next.generation = 1;
    next.contract_digest = identity().contract_digest;
    next.required_tokens_after_round = 10;
    later.admissions.push_back(next);
    const KvEdgeResult result = manager.commitEdge(later);
    EXPECT_FALSE(result.fatal.has_value());

    const KvTerminalSnapshot &after = *harness.executor.kvTerminal();
    EXPECT_EQ(after.request_id, frozen.request_id);
    EXPECT_EQ(after.status, KvTerminalStatus::Success);
    EXPECT_EQ(after.cached_tokens, frozen.cached_tokens);
    EXPECT_EQ(after.valid_bytes, frozen.valid_bytes);
    EXPECT_EQ(after.state, frozen.state);
    EXPECT_EQ(harness.executor.terminalTick(), terminal_tick);
    EXPECT_EQ(sink.last.terminalTick, terminal_tick);
    EXPECT_EQ(sink.last.kvTerminal->status, KvTerminalStatus::Success);
}

TEST(ServingMeshExecutor, SubmitAdmitsAndCompletesOnceWithRealOutput)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    const NpuAdmission admission =
        harness.executor.submit(executionRequest());
    EXPECT_TRUE(admission.admitted);
    EXPECT_EQ(harness.executor.kind(), NpuExecutorKind::ServingMesh);
    EXPECT_EQ(harness.executor.modeledServiceNs(executionRequest()), 0u);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_TRUE(sink.last.success);
    EXPECT_TRUE(sink.last.coreStarted);
    EXPECT_EQ(sink.last.phaseInstances, 4u);
    EXPECT_EQ(sink.last.outputBytes, 256u);
    EXPECT_TRUE(sink.last.outputPayload.empty());
    EXPECT_EQ(harness.dispatch.publish_content.size(), 256u);
    std::array<uint8_t, 32> expected{};
    for (size_t i = 0; i < expected.size(); ++i)
        expected[i] = static_cast<uint8_t>(0x40 + i + (256 & 0x1f) + 1);
    EXPECT_EQ(sink.last.semanticDigest, expected);
    EXPECT_FALSE(harness.executor.failed());
}

TEST(ServingMeshExecutor, BindsKvInputAndTokenContentAtAbsolutePositions)
{
    Harness harness;
    auto request = executionRequest();
    request.inputDigest.fill(0x3b);
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    driveOneGenerate(harness, kvStoreCommands(harness.program));
    ASSERT_EQ(harness.dispatch.fills.size(), 4u);
    EXPECT_EQ(harness.dispatch.fills[0].content,
              agentInputSurrogateBytes(request.inputDigest.data(), 128));
    const auto seed = surrogateSeed(request.inputDigest.data(), request.programId,
                                    request.profileId, request.requestedProfileKey);
    for (uint32_t ordinal = 0; ordinal < request.outputTokens; ++ordinal) {
        const auto token = surrogateTokenDigest(seed.data(), ordinal);
        EXPECT_EQ(harness.dispatch.fills[ordinal + 1].content,
                  surrogateOutputBytes(token.data(), 16));
    }
    EXPECT_NE(harness.dispatch.fills[1].content,
              harness.dispatch.fills[2].content);
}

TEST(ServingMeshExecutor, BindsAllocatedNonzeroKvSlotAndFrozenReadWindow)
{
    Harness harness;
    KvEdgeInputs occupied;
    KvAdmissionIntent first;
    first.request_id = first.session_id = first.kv_handle = 99;
    first.generation = 1;
    first.contract_digest = identity().contract_digest;
    first.required_tokens_after_round = 10;
    occupied.admissions.push_back(first);
    ASSERT_FALSE(harness.manager.commitEdge(occupied).fatal);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const auto symbol = harness.program.agent_request_profiles[0].primary_kv_symbol_id;
    const auto relocation = std::find_if(
        harness.program.relocations.begin(), harness.program.relocations.end(),
        [&](const auto &item) { return item.symbol_sid == symbol; });
    ASSERT_NE(relocation, harness.program.relocations.end());
    ASSERT_FALSE(harness.dispatch.instance_bindings.empty());
    const auto prefill = harness.dispatch.instance_bindings.back();
    EXPECT_EQ(prefill.resolve(relocation->tensor_id, relocation->offset_bytes,
                             128, true, 0),
              std::optional<uint64_t>(geometry().slotBase(1)));
    EXPECT_FALSE(prefill.resolve(relocation->tensor_id, relocation->offset_bytes,
                                 1, false, 0));
    const auto stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    const auto decode = harness.dispatch.instance_bindings.back();
    EXPECT_EQ(decode.resolve(relocation->tensor_id, relocation->offset_bytes,
                            128, false, 0),
              std::optional<uint64_t>(geometry().slotBase(1)));
    EXPECT_FALSE(decode.resolve(relocation->tensor_id, relocation->offset_bytes,
                                129, false, 0));
    EXPECT_EQ(decode.resolve(relocation->tensor_id, relocation->offset_bytes + 128,
                            16, true, 0),
              std::optional<uint64_t>(geometry().slotBase(1) + 128));
    EXPECT_FALSE(prefill.resolve(relocation->tensor_id, relocation->offset_bytes,
                                 1, false, 0));
}

TEST(ServingMeshExecutor, RejectsUnreachableKvSlotsBeforeInstallingSelectors)
{
    Harness harness;
    harness.dispatch.kv_reachable = false;
    const auto admission = harness.executor.submit(executionRequest());
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE);
    EXPECT_FALSE(harness.dispatch.installed);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_EQ(harness.dispatch.begins, 0u);
}

TEST(ServingMeshExecutor, SubmitRejectsAnUnknownProfileWithoutCompletion)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    NpuExecutionRequest request = executionRequest();
    request.profileId = 9;
    const NpuAdmission admission = harness.executor.submit(request);
    EXPECT_FALSE(admission.admitted);
    EXPECT_EQ(admission.rejectDetail, agent_abi::E_REQUEST_PROFILE);
    EXPECT_EQ(sink.completions, 0u);
    EXPECT_EQ(harness.dispatch.selectors.size(), 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FaultReportsOneFailedCompletion)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.executor.onInstanceSettled(1, 1, 1, true, 200);
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(sink.last.success);
    EXPECT_EQ(sink.last.phaseInstances, 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
}

TEST(ServingMeshExecutor, RejectsRequestsWithoutTheBoundProfileProof)
{
    struct Case
    {
        const char *name;
        uint32_t detail;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"wrong_key", agent_abi::E_REQUEST_PROFILE,
         [](NpuExecutionRequest &request) {
             request.requestedProfileKey ^= 1;
         }},
        {"input_bytes", agent_abi::E_BINDING_ROLE,
         [](NpuExecutionRequest &request) { request.inputBytes = 1; }},
        {"input_tokens", agent_abi::E_HOST_IO_SIZE_MISMATCH,
         [](NpuExecutionRequest &request) { request.inputTokens = 999; }},
        {"output_capacity", agent_abi::E_HOST_IO_SIZE_MISMATCH,
         [](NpuExecutionRequest &request) {
             request.outputCapacityBytes = 32;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.bytes = 32;
         }},
        {"output_capacity_single_sided", agent_abi::E_BINDING_ROLE,
         [](NpuExecutionRequest &request) {
             request.outputCapacityBytes = 32;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, item.detail) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_EQ(harness.dispatch.resumes, 0u) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
        EXPECT_FALSE(harness.executor.finished()) << item.name;
    }
}

TEST(ServingMeshExecutor, OldInstanceFactsAndRepeatedSettlesAreDropped)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);

    // Phase 1 completes and commits, which retires instance 1.
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 1, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(), 1u);

    // Phase 2 is armed as instance 2.  A fact from the old instance that
    // reuses the same request identity *and* the same descriptor/command ids
    // must not reach the current ledger.
    const uint32_t accepted = harness.executor.acceptedKvDescriptors();
    const uint32_t terminal = harness.executor.terminalKvDescriptors();
    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   1, 1, 1, 300);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 1, 1, 1,
                                           310,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), accepted);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(), terminal);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), 2u);

    // The current instance drains before its append terminal arrives: the
    // settle is consumed once and the phase stays open.
    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    const uint32_t settles = harness.executor.instanceSettles();
    EXPECT_EQ(settles, 2u);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);

    // Repeating that settle cannot count or advance anything.
    harness.executor.onInstanceSettled(2, 1, 1, false, 321);
    harness.executor.onInstanceSettled(2, 1, 1, true, 322);
    EXPECT_EQ(harness.executor.instanceSettles(), settles);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);
    EXPECT_EQ(harness.executor.staleInstanceFacts(), 4u);
    EXPECT_FALSE(harness.executor.failed());

    // The same instance's drain facts are still accepted and close the phase.
    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   2, 1, 1, 330);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 2, 1, 1,
                                           340,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), accepted + 1);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(), terminal + 1);
    EXPECT_EQ(harness.executor.phasesCommitted(), 2u);
    EXPECT_FALSE(harness.executor.failed());

    // A settle for an instance that was never armed is a contract violation.
    harness.executor.onInstanceSettled(99, 1, 1, false, 350);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.executor.phasesCommitted(), 2u);
}

TEST(ServingMeshExecutor, LateDmaFactsCannotRewriteASealedSuccess)
{
    for (bool terminal : {false, true}) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        const NpuExecutionRequest request = executionRequest();
        ASSERT_TRUE(harness.executor.submit(request).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        driveOneGenerate(harness, stores);
        ASSERT_TRUE(harness.executor.finished());
        ASSERT_EQ(sink.completions, 1u);
        ASSERT_TRUE(sink.last.success);
        ASSERT_TRUE(harness.executor.completionFor(request).success);
        if (terminal)
            harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 99, 1, 1,
                                           600,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        else
            harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                           stores[0].command_id, 99, 1, 1, 600);
        EXPECT_FALSE(harness.executor.failed());
        EXPECT_TRUE(harness.executor.completionFor(request).success);
        EXPECT_EQ(harness.dispatch.fails, 0u);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_TRUE(sink.last.success);
    }
}

TEST(ServingMeshExecutor, UnknownInstanceDmaFactsFailTheRequest)
{
    const auto stores_for = [](Harness &harness) {
        return kvStoreCommands(harness.program);
    };
    {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([]() { return 100; });
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = stores_for(harness);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id, 99, 1, 1, 200);
        EXPECT_TRUE(harness.executor.failed());
        EXPECT_EQ(harness.dispatch.fails, 1u);
        EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 0u);
        EXPECT_EQ(sink.completions, 0u);
    }
    {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        harness.executor.setTickSource([]() { return 100; });
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = stores_for(harness);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        ASSERT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
        harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 99, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        EXPECT_TRUE(harness.executor.failed());
        EXPECT_EQ(harness.dispatch.fails, 1u);
        EXPECT_EQ(harness.executor.terminalKvDescriptors(), 0u);
        EXPECT_EQ(sink.completions, 0u);

        harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
        harness.executor.onInstanceSettled(harness.currentInstance(), 1, 1,
                                           false, 320);
        EXPECT_EQ(sink.completions, 1u);
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.hasClaim(1));
        EXPECT_TRUE(harness.manager.validate().empty());
        EXPECT_EQ(harness.executor.terminalKvDescriptors(), 1u);
    }
}

TEST(ServingMeshExecutor, DmaFactsBeforeTheRealStartRollBackThePrestartClaim)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.dispatch.on_start = nullptr;
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    ASSERT_FALSE(harness.executor.finished());
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_EQ(sink.last.kvTerminal->status, KvTerminalStatus::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, AStaleInstanceDrainCannotCloseTheCurrentPhase)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, 1, 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(), 1u);
    ASSERT_FALSE(harness.executor.failed());

    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   1, 1, 1, 310);
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);
    EXPECT_FALSE(harness.executor.failed());

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id,
                                   2, 1, 1, 400);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, 2, 1, 1,
                                           410,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(2, 1, 1, false, 420);
    EXPECT_EQ(harness.executor.phasesCommitted(), 2u);
    EXPECT_FALSE(harness.executor.failed());
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, InstallsEveryVerifiedRoleIntoTheExecutionView)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    NpuExecutionRequest request = executionRequest();
    ASSERT_FALSE(request.hostBindings.empty());
    ASSERT_TRUE(harness.executor.submit(request).admitted);
    ASSERT_FALSE(harness.dispatch.instance_bindings.empty());
    const ServingInstanceBinding &view =
        harness.dispatch.instance_bindings.back();
    uint32_t installed = 0;
    for (const agent_abi::BindingRecord &binding : request.hostBindings) {
        if (binding.kind == agent_abi::kBindingKindKV_EXTERNAL)
            continue;
        const auto relocation = std::find_if(
            harness.program.relocations.begin(),
            harness.program.relocations.end(),
            [&](const auto &item) {
                return item.symbol_sid == binding.symbol_id;
            });
        ASSERT_NE(relocation, harness.program.relocations.end());
        const bool write = binding.kind == agent_abi::kBindingKindHOST_OUTPUT;
        const uint64_t fallback = 0xDead0000ull;
        const auto resolved = view.resolve(relocation->tensor_id,
                                           relocation->offset_bytes,
                                           binding.bytes, write, fallback);
        ASSERT_TRUE(resolved.has_value()) << binding.symbol_id;
        EXPECT_EQ(*resolved, binding.address) << binding.symbol_id;
        EXPECT_NE(*resolved, fallback + relocation->offset_bytes)
            << binding.symbol_id;
        ++installed;
    }
    EXPECT_EQ(installed, 3u);
}

TEST(ServingMeshExecutor, RejectsATamperedHostBindingTableBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"missing", [](NpuExecutionRequest &request) {
             request.hostBindings.pop_back();
         }},
        {"extra", [](NpuExecutionRequest &request) {
             request.hostBindings.push_back(request.hostBindings[0]);
         }},
        {"wrong_role", [](NpuExecutionRequest &request) {
             request.hostBindings[0].kind =
                 agent_abi::kBindingKindKV_EXTERNAL;
         }},
        {"wrong_flags", [](NpuExecutionRequest &request) {
             request.hostBindings[0].flags = agent_abi::kBindingFlagsWRITE;
         }},
        {"zero_range", [](NpuExecutionRequest &request) {
             request.hostBindings[0].bytes = 0;
         }},
        {"zero_address", [](NpuExecutionRequest &request) {
             request.hostBindings[0].address = 0;
         }},
        {"kv_physical_address", [](NpuExecutionRequest &request) {
             request.hostBindings[2].address = 0x400000;
         }},
        {"kv_wrong_slot_bytes", [](NpuExecutionRequest &request) {
             request.hostBindings[2].bytes = 8192;
         }},
        {"absent", [](NpuExecutionRequest &request) {
             request.hostBindings.clear();
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsOverlappingRequestRangesBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"output_aliases_input", [](NpuExecutionRequest &request) {
             request.outputAddress = request.inputAddress;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.inputAddress;
         }},
        {"metadata_aliases_input", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.inputAddress;
             request.metadataCapacityBytes = 200;
         }},
        {"metadata_overlaps_output", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.outputAddress;
             request.metadataCapacityBytes = 64;
         }},
        {"partial_overlap", [](NpuExecutionRequest &request) {
             request.metadataAddress = request.outputAddress +
                 request.outputCapacityBytes - 64;
             request.metadataCapacityBytes = 128;
         }},
        {"range_overflow", [](NpuExecutionRequest &request) {
             request.metadataAddress = UINT64_MAX - 32;
             request.metadataCapacityBytes = 64;
         }},
        {"weight_overlap", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = 0x800200000;
                     binding.bytes = 128;
                 }
             request.metadataAddress = 0x800200000;
             request.metadataCapacityBytes = 64;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail,
                  agent_abi::E_BINDING_ALIAS_MISMATCH) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
    }
    Harness harness;
    NpuExecutionRequest adjacent = executionRequest();
    adjacent.metadataAddress = adjacent.outputAddress +
        adjacent.outputCapacityBytes;
    adjacent.metadataCapacityBytes = 200;
    const NpuAdmission admission = harness.executor.submit(adjacent);
    EXPECT_TRUE(admission.admitted);
}

TEST(ServingMeshExecutor, WireBindingsCannotDisagreeWithTheCheckedHeader)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"output_address", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.inputAddress;
         }},
        {"input_address", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = request.outputAddress;
         }},
        {"output_bytes", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.bytes = request.inputBytes;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsZeroRequestAddressesBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"input_double_zero", [](NpuExecutionRequest &request) {
             request.inputAddress = 0;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = 0;
         }},
        {"output_double_zero", [](NpuExecutionRequest &request) {
             request.outputAddress = 0;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = 0;
         }},
        {"metadata_zero", [](NpuExecutionRequest &request) {
             request.metadataAddress = 0;
             request.metadataCapacityBytes = 200;
         }},
        {"input_single_zero", [](NpuExecutionRequest &request) {
             request.inputAddress = 0;
         }},
        {"weight_zero", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL)
                     binding.address = 0;
         }},
    };
    for (const Case &item : cases) {
        Harness harness;
        NpuExecutionRequest request = executionRequest();
        item.mutate(request);
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, agent_abi::E_BINDING_ROLE)
            << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
    }
}

TEST(ServingMeshExecutor, RejectsCrossKindAndKvRangeOverlapsBeforeAdmission)
{
    struct Case
    {
        const char *name;
        void (*mutate)(NpuExecutionRequest &);
    };
    const Case cases[] = {
        {"input_aliases_weight", [](NpuExecutionRequest &request) {
             request.inputAddress = 0x800200000;
             for (auto &binding : request.hostBindings) {
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = request.inputAddress;
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = request.inputAddress;
                     binding.bytes = request.inputBytes;
                 }
             }
         }},
        {"input_partially_overlaps_weight", [](NpuExecutionRequest &request) {
             for (auto &binding : request.hostBindings) {
                 if (binding.kind == agent_abi::kBindingKindHOST_INPUT)
                     binding.address = 0x800200000 - 64;
                 if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
                     binding.address = 0x800200000;
                     binding.bytes = request.inputBytes;
                 }
             }
         }},
        {"output_aliases_kv_region", [](NpuExecutionRequest &request) {
             request.outputAddress = geometry().region_base +
                 geometry().slot_bytes;
             for (auto &binding : request.hostBindings)
                 if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
                     binding.address = request.outputAddress;
         }},
        {"metadata_inside_kv_region", [](NpuExecutionRequest &request) {
             request.metadataAddress = geometry().region_base + 64;
             request.metadataCapacityBytes = 128;
         }},
    };
    Harness adjacentHarness;
    NpuExecutionRequest adjacent = executionRequest();
    adjacent.outputAddress = geometry().region_base +
        geometry().regionBytes();
    for (auto &binding : adjacent.hostBindings)
        if (binding.kind == agent_abi::kBindingKindHOST_OUTPUT)
            binding.address = adjacent.outputAddress;
    EXPECT_TRUE(adjacentHarness.executor.submit(adjacent).admitted);
}

TEST(ServingMeshExecutor, HostBindingRequestRangeRules)
{
    AgentHostBindingPlan plan;
    plan.programId = 1;
    plan.profileId = 1;
    AgentHostBindingRequirement input;
    input.symbolId = 1;
    input.kind = agent_abi::kBindingKindHOST_INPUT;
    input.flags = exactBindingFlags(input.kind);
    AgentHostBindingRequirement output;
    output.symbolId = 2;
    output.kind = agent_abi::kBindingKindHOST_OUTPUT;
    output.flags = exactBindingFlags(output.kind);
    AgentHostBindingRequirement weight;
    weight.symbolId = 3;
    weight.kind = agent_abi::kBindingKindWEIGHT_EXTERNAL;
    weight.flags = exactBindingFlags(weight.kind);
    weight.platformAddress = 0x800200000;
    weight.platformBytes = 128;
    AgentHostBindingRequirement aliasWeight = weight;
    aliasWeight.symbolId = 4;

    auto record = [](uint32_t symbol, uint16_t kind, uint16_t flags,
                     uint64_t address, uint64_t bytes) {
        agent_abi::BindingRecord record;
        record.symbol_id = symbol;
        record.kind = kind;
        record.flags = flags;
        record.address = address;
        record.bytes = bytes;
        return record;
    };
    const uint64_t kvBegin = 0x400000;
    const uint64_t kvBytes = 8192;
    std::string reason;

    plan.requirements = {input, output, weight};
    std::vector<agent_abi::BindingRecord> control = {
        record(1, input.kind, input.flags, 0x1000, 128),
        record(2, output.kind, output.flags, 0x2000, 128),
        record(3, weight.kind, weight.flags, weight.platformAddress,
               weight.platformBytes),
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  control, plan, 0x1000, 128, 0x2000, 128, 0x3000, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Match) << reason;

    plan.requirements = {input, output, weight, aliasWeight};
    std::vector<agent_abi::BindingRecord> equalWeights = {
        control[0], control[1], control[2],
        record(4, aliasWeight.kind, aliasWeight.flags,
               weight.platformAddress, weight.platformBytes),
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  equalWeights, plan, 0x1000, 128, 0x2000, 128, 0x3000, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> duplicateSymbol = {
        control[0], control[1], control[2], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  duplicateSymbol, plan, 0x1000, 128, 0x2000, 128, 0x3000,
                  128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    plan.requirements = {input, output, weight};
    std::vector<agent_abi::BindingRecord> zeroInput = {
        record(1, input.kind, input.flags, 0, 128),
        control[1], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroInput, plan, 0, 128, 0x2000, 128, 0x3000, 128, kvBegin,
                  kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);
    EXPECT_NE(reason.find("E_BINDING_ROLE"), std::string::npos) << reason;

    std::vector<agent_abi::BindingRecord> zeroOutput = {
        control[0],
        record(2, output.kind, output.flags, 0, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroOutput, plan, 0x1000, 128, 0, 128, 0x3000, 128, kvBegin,
                  kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    std::vector<agent_abi::BindingRecord> zeroMetadata = control;
    EXPECT_EQ(verifyHostBindingRequest(
                  zeroMetadata, plan, 0x1000, 128, 0x2000, 128, 0, 128,
                  kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Mismatch);

    std::vector<agent_abi::BindingRecord> inputAtWeight = {
        record(1, input.kind, input.flags, weight.platformAddress, 128),
        control[1], control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  inputAtWeight, plan, weight.platformAddress, 128, 0x2000,
                  128, 0x3000, 128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> outputAtKv = {
        control[0],
        record(2, output.kind, output.flags, kvBegin + 64, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  outputAtKv, plan, 0x1000, 128, kvBegin + 64, 128, 0x3000,
                  128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::AliasMismatch);

    std::vector<agent_abi::BindingRecord> adjacentToKv = {
        control[0],
        record(2, output.kind, output.flags, kvBegin + kvBytes, 128),
        control[2],
    };
    EXPECT_EQ(verifyHostBindingRequest(
                  adjacentToKv, plan, 0x1000, 128, kvBegin + kvBytes, 128,
                  0x5000, 128, kvBegin, kvBytes, 4096, reason),
              HostBindingVerdict::Match) << reason;
}

TEST(ServingMeshExecutor, DrivesOneGenerateThroughRealKvEdges)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    EXPECT_EQ(harness.dispatch.begins, 1u);
    ASSERT_EQ(harness.dispatch.selectors.size(), 4u);
    EXPECT_EQ(harness.dispatch.selectors[0], std::make_pair(1u, 1u));
    EXPECT_EQ(harness.dispatch.selectors[1], std::make_pair(2u, 2u));
    EXPECT_EQ(harness.dispatch.selectors[2], std::make_pair(3u, 3u));
    EXPECT_EQ(harness.dispatch.selectors[3], std::make_pair(4u, 4u));
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 0u);

    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(), 1u);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);
    EXPECT_EQ(harness.dispatch.resumes, 1u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 8u);
    EXPECT_EQ(record->valid_bytes, 128u);

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id, harness.currentInstance(), 1, 1, 300);
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           310,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    EXPECT_EQ(harness.executor.phasesCommitted(), 2u);
    EXPECT_EQ(harness.dispatch.resumes, 2u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 9u);

    harness.executor.onDmaAccepted(stores[2].descriptor_id, stores[2].command_id, harness.currentInstance(), 1, 1, 400);
    harness.executor.onDmaTerminal(stores[2].descriptor_id, stores[2].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           410,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(3, 1, 1, false, 420);
    EXPECT_EQ(harness.executor.phasesCommitted(), 3u);
    EXPECT_EQ(harness.dispatch.resumes, 3u);
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 10u);
    EXPECT_EQ(record->valid_bytes, 160u);

    harness.executor.onInstanceSettled(4, 1, 1, false, 520);
    EXPECT_TRUE(harness.executor.finished());
    EXPECT_FALSE(harness.executor.failed());
    EXPECT_EQ(harness.executor.phasesCommitted(), 4u);
    EXPECT_EQ(harness.dispatch.resumes, harness.executor.phasesCommitted());
    const std::optional<KvTerminalSnapshot> &terminal =
        harness.executor.kvTerminal();
    ASSERT_TRUE(terminal.has_value());
    EXPECT_EQ(terminal->request_id, 1u);
    EXPECT_EQ(terminal->status, KvTerminalStatus::Success);
    EXPECT_EQ(terminal->cached_tokens, 10u);
    EXPECT_EQ(harness.executor.terminalTick(), 520u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->pin_count, 0u);
    EXPECT_EQ(record->admission_claim_count, 0u);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, PhaseFactsRecordTheRealCausalChain)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phaseFacts().size(), 2u);
    const ServingPhaseFact &prefill = harness.executor.phaseFacts()[0];
    EXPECT_EQ(prefill.phase, kPhasePREFILL);
    EXPECT_EQ(prefill.instance_profile_id, 11u);
    EXPECT_EQ(prefill.mesh_profile_id, 1u);
    EXPECT_EQ(prefill.kv_tokens_before, 0u);
    EXPECT_EQ(prefill.append_tokens, 8u);
    EXPECT_EQ(prefill.accepted_descriptors, 1u);
    EXPECT_EQ(prefill.terminal_descriptors, 1u);
    EXPECT_EQ(prefill.append_terminal_tick, 210u);
    EXPECT_EQ(prefill.core_drain_tick, 220u);
    EXPECT_EQ(prefill.commit_tick, 220u);
    const ServingPhaseFact &decode0 = harness.executor.phaseFacts()[1];
    EXPECT_EQ(decode0.phase, kPhaseDECODE);
    EXPECT_EQ(decode0.kv_tokens_before, 8u);
    EXPECT_EQ(decode0.append_tokens, 1u);
    EXPECT_EQ(decode0.commit_tick, 0u);
    driveOneGenerate(harness, stores, 2);
    ASSERT_EQ(harness.executor.phaseFacts().size(), 4u);
    EXPECT_EQ(harness.executor.phaseFacts()[3].phase, kPhasePUBLISH);
    EXPECT_EQ(harness.executor.phaseFacts()[3].append_tokens, 0u);
    EXPECT_EQ(harness.executor.phaseFacts()[3].accepted_descriptors, 0u);
}

TEST(ServingMeshExecutor, PartialTokenPrefixBlocksThePhase)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_EQ(stores.size(), 3u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 16, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 1u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, RetriedDescriptorTerminalIsNotDoubleCounted)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 201);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           211,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
    EXPECT_EQ(harness.executor.terminalKvDescriptors(), 1u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, CoreDrainAndAppendTerminalAreBothRequired)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_EQ(harness.executor.phasesCommitted(), 1u);
    EXPECT_EQ(harness.dispatch.resumes, 1u);
}

TEST(ServingMeshExecutor, SamePhaseSecondStoreDoesNotBlockTheWrapUp)
{
    Harness harness(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb");
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission split_admission = harness.executor.submit(
        executionRequest(0x300000,
                         "tests/gem5/ai_mesh/fixtures/gate6/"
                         "serving_split_kv.mshb"));
    ASSERT_TRUE(split_admission.admitted)
        << uint32_t(split_admission.rejectDetail);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    ASSERT_EQ(stores[0].command_id + 1, stores[1].command_id);
    ASSERT_EQ(stores[1].descriptor_id, stores[0].descriptor_id + 1);

    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    ASSERT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_NE(observed->status, KvTerminalStatus::Success);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, SamePhaseSecondStoreWaitsForTheIssuedTerminal)
{
    Harness harness(
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb");
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    const NpuAdmission split_admission = harness.executor.submit(
        executionRequest(0x300000,
                         "tests/gem5/ai_mesh/fixtures/gate6/"
                         "serving_split_kv.mshb"));
    ASSERT_TRUE(split_admission.admitted)
        << uint32_t(split_admission.rejectDetail);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());

    harness.executor.onInstanceSettled(1, 1, 1, false, 300);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 0, harness.currentInstance(), 1, 1,
                                           320,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailedRequestWaitsForItsDrainBeforeReporting)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    ASSERT_EQ(harness.executor.acceptedKvDescriptors(), 1u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_FALSE(harness.manager.fatalReason().has_value());

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_NE(observed->status, KvTerminalStatus::Success);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailedRequestWaitsForTheLastKvTerminalAfterTheDrain)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());

    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           340,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnissuedDescriptorsDoNotBlockTheFailedWrapUp)
{
    Harness harness;
    RecordingSink sink;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 1u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, SecondPhaseFailureWaitsForItsOwnDrain)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_GE(stores.size(), 2u);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    ASSERT_EQ(harness.executor.phasesCommitted(), 1u);
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaAccepted(stores[1].descriptor_id, stores[1].command_id, harness.currentInstance(), 1, 1, 240);
    harness.executor.onDmaAccepted(stores[1].descriptor_id + 100, stores[1].command_id, harness.currentInstance(), 1, 1, 250);
    EXPECT_TRUE(harness.executor.failed());
    harness.executor.onDmaTerminal(stores[1].descriptor_id, stores[1].command_id, 16, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 16}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onInstanceSettled(2, 1, 1, false, 320);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, ErroredInstanceDrainUsesTheSameTerminalGate)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);

    harness.executor.onInstanceSettled(1, 1, 1, true, 210);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 0u);

    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           300,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_EQ(sink.completions, 1u);
    ASSERT_TRUE(sink.last.kvTerminal.has_value());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FailureStopsTheRunningCoreThroughTheMesh)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    EXPECT_EQ(harness.dispatch.fails, 0u);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 210);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.dispatch.fails, 1u);
    EXPECT_EQ(harness.dispatch.cancels, 0u);
}

TEST(ServingMeshExecutor, ErrorCompletionCarriesTheOwnerTerminal)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    bool ownership_released = false;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        ownership_released =
            !harness.manager.hasPin(1) && !harness.manager.hasClaim(1);
    };
    harness.executor.attachSink(&sink);
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    harness.executor.onInstanceSettled(1, 1, 1, true, 200);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_GE(sink.completions, 1u);
    ASSERT_TRUE(observed.has_value());
    EXPECT_FALSE(observed->status == KvTerminalStatus::Success);
    EXPECT_TRUE(ownership_released);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, MeshFaultReleasesTheKvClaim)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    harness.executor.onInstanceSettled(1, 1, 1, true, 200);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_FALSE(harness.executor.finished());
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, FaultedDmaTerminalRecordsTheErrorPrefix)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 0, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{});
    harness.executor.onInstanceSettled(1, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    EXPECT_EQ(harness.dispatch.resumes, 0u);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 0u);
    EXPECT_EQ(record->state, KvState::Error);
    ASSERT_TRUE(record->first_error.has_value());
    EXPECT_EQ(record->first_error->code, 7u);
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, MismatchedDescriptorIdentityIsRejected)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id + 100, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.executor.acceptedKvDescriptors(), 0u);
}

TEST(ServingMeshExecutor, CancelCompletionCarriesTheOwnerTerminal)
{
    Harness harness;
    RecordingSink sink;
    std::optional<KvTerminalSnapshot> observed;
    bool ownership_released = false;
    sink.on_complete = [&](const NpuExecutionCompletion &value) {
        ASSERT_TRUE(value.kvTerminal.has_value());
        observed = value.kvTerminal;
        ownership_released =
            !harness.manager.hasPin(1) && !harness.manager.hasClaim(1);
    };
    harness.executor.attachSink(&sink);
    harness.dispatch.on_start = nullptr;
    harness.executor.setTickSource([]() { return 100; });
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    std::string reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(150, reason)) << reason;
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(sink.last.success);
    ASSERT_TRUE(observed.has_value());
    EXPECT_EQ(observed->status, KvTerminalStatus::Cancelled);
    EXPECT_TRUE(ownership_released);
    EXPECT_EQ(sink.last.requestStartTick, 100u);
    EXPECT_EQ(sink.last.terminalTick, 150u);
    EXPECT_EQ(harness.executor.terminalTick(), 150u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnknownRequestProfileIsRejectedWithoutSideEffects)
{
    Harness harness;
    ServingRequestIdentity bad = identity();
    bad.profile_id = 9;
    std::string reason;
    EXPECT_FALSE(harness.executor.start(bad, 100, reason));
    EXPECT_EQ(reason.rfind("E_REQUEST_PROFILE", 0), 0u);
    EXPECT_FALSE(harness.executor.failed());
    EXPECT_EQ(harness.dispatch.selectors.size(), 0u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, UnknownInstanceCallbackDoesNotAdvanceThePhase)
{
    Harness harness;
    std::string reason;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id, harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 128, 0, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 128}});
    harness.executor.onInstanceSettled(999, 1, 1, false, 220);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
    EXPECT_TRUE(harness.executor.failed());
    EXPECT_FALSE(harness.executor.finished());
    harness.executor.onInstanceSettled(1, 1, 1, false, 230);
    EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
}

TEST(ServingMeshExecutor, AbortBeforeTheRealStartRollsBackTheClaim)
{
    Harness harness;
    std::string reason;
    harness.dispatch.on_start = nullptr;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    ASSERT_TRUE(harness.executor.abortBeforeStart(160, reason)) << reason;
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_EQ(harness.dispatch.begins, 1u);
    EXPECT_FALSE(harness.executor.abortBeforeStart(170, reason));
    EXPECT_EQ(harness.dispatch.cancels, 1u);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, TheRealStartConsumesTheRollbackAuthority)
{
    Harness harness;
    std::string reason;
    harness.dispatch.on_start = nullptr;
    ASSERT_TRUE(harness.executor.start(identity(), 100, reason)) << reason;
    ASSERT_TRUE(harness.executor.beginFirstPhase(150, reason)) << reason;
    harness.executor.onInstanceStarted(1, 1, 1, 160);
    EXPECT_FALSE(harness.executor.abortBeforeStart(170, reason));
}

TEST(ServingMeshExecutor, SuccessfulBurstPrefixSurvivesDescriptorError)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id, 64, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{{0, 0, 64}});
    harness.executor.onInstanceSettled(1, 1, 1, true, 220);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->cached_tokens, 4u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
    EXPECT_EQ(sink.completions, 1u);
}

TEST(ServingMeshExecutor, CommittedSegmentsCoverOnlyTheirOwnBytes)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    struct Case
    {
        const char *name;
        std::vector<DmaCommittedSegment> segments;
        uint32_t expected_tokens;
    };
    const Case cases[] = {
        {"leading_prefix", {{0, 0, 16}}, 1u},
        {"leading_two", {{0, 0, 32}}, 2u},
        {"trailing_success", {{0, 48, 16}}, 0u},
        {"out_of_order", {{0, 48, 16}, {0, 16, 16}}, 0u},
        {"reversed_prefix", {{0, 32, 32}, {0, 0, 16}}, 1u},
        {"duplicate", {{0, 16, 16}, {0, 16, 16}}, 0u},
    };
    for (const Case &item : cases) {
        Harness harness(fixture);
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        uint64_t committed = 0;
        for (const DmaCommittedSegment &segment : item.segments)
            committed += segment.bytes;
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, committed, 7,
                                       harness.currentInstance(), 1, 1, 210,
                                       item.segments);
        harness.executor.onInstanceSettled(1, 1, 1, true, 220);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr) << item.name;
        EXPECT_EQ(record->cached_tokens, item.expected_tokens) << item.name;
        EXPECT_EQ(record->state, KvState::Error) << item.name;
        EXPECT_FALSE(harness.manager.fatalReason().has_value()) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
        EXPECT_EQ(sink.completions, 1u) << item.name;
    }
}

TEST(ServingMeshExecutor, SameEdgeFaultWinnerDoesNotFollowCallbackOrder)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    for (bool reverse : {false, true}) {
        Harness harness(fixture);
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        for (unsigned index : {0u, 1u})
            harness.executor.onDmaAccepted(stores[index].descriptor_id,
                                           stores[index].command_id,
                                           harness.currentInstance(), 1, 1,
                                           200);
        for (unsigned ordinal : {0u, 1u}) {
            const unsigned index = reverse ? 1u - ordinal : ordinal;
            harness.executor.onDmaTerminal(stores[index].descriptor_id, stores[index].command_id, 0, 7, harness.currentInstance(), 1, 1,
                                           210,
                                           std::vector<DmaCommittedSegment>{});
        }
        harness.executor.onInstanceSettled(1, 1, 1, true, 220);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        ASSERT_TRUE(record->first_error.has_value()) << "reverse=" << reverse;
        EXPECT_EQ(record->first_error->source.ordinal,
                  std::min(stores[0].descriptor_id, stores[1].descriptor_id))
            << "reverse=" << reverse;
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_TRUE(harness.manager.validate().empty());
        EXPECT_EQ(sink.completions, 1u);
    }
}

TEST(ServingMeshExecutor, SqFlagsMapToTheDeclaredRequestPathKind)
{
    EXPECT_EQ(pathKindOfSqFlags(0), mesh_abi::kPathKindINITIAL_PREFILL);
    EXPECT_EQ(pathKindOfSqFlags(agent_abi::kSqFlagsREQUIRE_KV_REUSE),
              mesh_abi::kPathKindKV_REUSE);
    EXPECT_EQ(pathKindOfSqFlags(agent_abi::kSqFlagsALLOW_REPREFILL),
              mesh_abi::kPathKindREPREFILL);
    EXPECT_GT(pathKindOfSqFlags(agent_abi::kSqFlagsREQUIRE_KV_REUSE |
                                agent_abi::kSqFlagsALLOW_REPREFILL),
              mesh_abi::kPathKindREPREFILL);
    EXPECT_EQ(pathKindOfSqFlags(agent_abi::kSqFlagsBATCH_REPLAY),
              mesh_abi::kPathKindINITIAL_PREFILL);
}

TEST(ServingMeshExecutor, RoundAndFlagsAreProvenBeforeAdmission)
{
    struct Case
    {
        const char *name;
        uint16_t round;
        uint16_t flags;
        uint32_t cached_tokens;
        uint32_t expected_detail;
    };
    const Case cases[] = {
        {"round_zero_reuse", 0, agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0,
         agent_abi::E_KV_FLAG_COMBINATION},
        {"round_zero_reprefill", 0, agent_abi::kSqFlagsALLOW_REPREFILL, 0,
         agent_abi::E_KV_FLAG_COMBINATION},
        {"repair_no_path", 1, 0, 0, agent_abi::E_KV_FLAG_COMBINATION},
        {"repair_both_paths", 1,
         uint16_t(agent_abi::kSqFlagsREQUIRE_KV_REUSE |
                  agent_abi::kSqFlagsALLOW_REPREFILL),
         0, agent_abi::E_KV_FLAG_COMBINATION},
        {"reuse_path_not_reachable", 1,
         agent_abi::kSqFlagsREQUIRE_KV_REUSE, 0,
         agent_abi::E_REQUEST_PROFILE},
        {"cached_tokens_mismatch", 0, 0, 1,
         agent_abi::E_KV_TOKEN_MISMATCH},
    };
    for (const Case &item : cases) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        NpuExecutionRequest request = executionRequest();
        request.repairRound = item.round;
        request.kvFlags = item.flags;
        request.cachedTokens = item.cached_tokens;
        const NpuAdmission admission = harness.executor.submit(request);
        EXPECT_FALSE(admission.admitted) << item.name;
        EXPECT_EQ(admission.rejectDetail, item.expected_detail) << item.name;
        EXPECT_FALSE(harness.dispatch.installed) << item.name;
        EXPECT_EQ(harness.dispatch.begins, 0u) << item.name;
        EXPECT_FALSE(harness.manager.hasPin(1)) << item.name;
        EXPECT_FALSE(harness.manager.hasClaim(1)) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
        EXPECT_EQ(sink.completions, 0u) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
    }
}

TEST(ServingMeshExecutor, MissingOrInconsistentSegmentsAreAContractError)
{
    const std::string fixture =
        "tests/gem5/ai_mesh/fixtures/gate6/serving_split_kv.mshb";
    struct Case
    {
        const char *name;
        uint64_t committed;
        uint32_t error;
        std::vector<DmaCommittedSegment> segments;
    };
    const Case cases[] = {
        {"missing_success", 64, 0, {}},
        {"missing_fault", 64, 7, {}},
        {"understated", 64, 7, {{0, 0, 32}}},
        {"overstated", 64, 7, {{0, 0, 64}, {0, 32, 16}}},
        {"out_of_range", 64, 7, {{9, 0, 64}}},
    };
    for (const Case &item : cases) {
        Harness harness(fixture);
        ASSERT_TRUE(harness.executor.submit(
            executionRequest(0x300000, fixture)).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_GE(stores.size(), 2u);
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, item.committed,
                                       item.error, harness.currentInstance(), 1,
                                       1, 210, item.segments);
        EXPECT_TRUE(harness.executor.failed()) << item.name;
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u) << item.name;
        EXPECT_FALSE(harness.manager.fatalReason().has_value()) << item.name;
        EXPECT_TRUE(harness.manager.validate().empty()) << item.name;
    }
}

TEST(ServingMeshExecutor, InvalidSegmentsStillReachOneTerminalAfterDrain)
{
    Harness harness;
    RecordingSink sink;
    harness.executor.attachSink(&sink);
    ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
    const std::vector<KvStore> stores = kvStoreCommands(harness.program);
    ASSERT_FALSE(stores.empty());
    harness.executor.onDmaAccepted(stores[0].descriptor_id, stores[0].command_id,
                                   harness.currentInstance(), 1, 1, 200);
    harness.executor.onDmaTerminal(stores[0].descriptor_id, stores[0].command_id,
                                   128, 0, harness.currentInstance(), 1, 1, 210,
                                   {});
    ASSERT_TRUE(harness.executor.failed());
    harness.executor.onInstanceSettled(1, 1, 1, true, 220);
    const KvRecord *record = harness.manager.findRecord(11, 11);
    ASSERT_NE(record, nullptr);
    EXPECT_EQ(record->outstanding_kv_dma, 0u);
    EXPECT_EQ(record->state, KvState::Error);
    EXPECT_FALSE(harness.manager.hasPin(1));
    EXPECT_FALSE(harness.manager.hasClaim(1));
    EXPECT_EQ(sink.completions, 1u);
    EXPECT_FALSE(harness.manager.fatalReason().has_value());
    EXPECT_TRUE(harness.manager.validate().empty());
}

TEST(ServingMeshExecutor, DuplicateOrWrappedSegmentsCannotProveSuccess)
{
    const std::vector<std::vector<DmaCommittedSegment>> cases = {
        {{0, 0, 64}, {0, 0, 64}},
        {{0, UINT64_MAX - 63, 128}},
    };
    for (const auto &segments : cases) {
        Harness harness;
        RecordingSink sink;
        harness.executor.attachSink(&sink);
        ASSERT_TRUE(harness.executor.submit(executionRequest()).admitted);
        const std::vector<KvStore> stores = kvStoreCommands(harness.program);
        ASSERT_FALSE(stores.empty());
        harness.executor.onDmaAccepted(stores[0].descriptor_id,
                                       stores[0].command_id,
                                       harness.currentInstance(), 1, 1, 200);
        harness.executor.onDmaTerminal(stores[0].descriptor_id,
                                       stores[0].command_id, 128, 0,
                                       harness.currentInstance(), 1, 1, 210,
                                       segments);
        EXPECT_TRUE(harness.executor.failed());
        harness.executor.onInstanceSettled(1, 1, 1, false, 220);
        EXPECT_TRUE(harness.executor.failed());
        EXPECT_EQ(harness.executor.phasesCommitted(), 0u);
        EXPECT_EQ(sink.completions, 1u);
        const KvRecord *record = harness.manager.findRecord(11, 11);
        ASSERT_NE(record, nullptr);
        EXPECT_EQ(record->outstanding_kv_dma, 0u);
        EXPECT_FALSE(harness.manager.hasPin(1));
        EXPECT_FALSE(harness.manager.fatalReason().has_value());
        EXPECT_TRUE(harness.manager.validate().empty());
    }
}

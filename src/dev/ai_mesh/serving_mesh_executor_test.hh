#ifndef DEV_AI_MESH_SERVING_MESH_EXECUTOR_TEST_HH
#define DEV_AI_MESH_SERVING_MESH_EXECUTOR_TEST_HH

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
#include "dev/ai_mesh/mesh_program_test_support.hh"
#include <stdexcept>
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/serving_host_bindings.hh"
#include "dev/ai_mesh/serving_mesh_executor.hh"

namespace gem5::ai_mesh::serving_test
{

using namespace gem5::ai_mesh;
using namespace mesh_abi;

class RecordingDispatch : public ServingMeshDispatch
{
  public:
    void configureServingCapacity(uint32_t) override {}
    std::optional<InstanceGeneration> selectInstance(
        uint32_t entrypoint_id, uint32_t profile_id, std::string &) override
    {
        selectors.emplace_back(entrypoint_id, profile_id);
        return InstanceGeneration(++generation_counter);
    }

    void failCurrentInstance(const ErrorCandidate &failure) override
    { fails++; failures.push_back(failure); }
    std::vector<ErrorCandidate> failures;
    void noteMemberCancel(uint64_t instance, uint64_t request, uint64_t tick) override
    { member_cancels.emplace_back(instance, request, tick); }
    std::vector<std::tuple<uint64_t, uint64_t, uint64_t>> member_cancels;

    bool prepareMoe(const mesh_abi::AgentInstanceProfile &,
        const std::vector<ServingBatchMember> &, const ServingInstanceBinding &,
        std::shared_ptr<const MoeOverlaySet> &, MoePreparationError &error) override
    { if (preparation_failure) { error = *preparation_failure; return false; } return true; }
    std::optional<MoePreparationError> preparation_failure;
    void bindInstance(const ServingInstanceBinding &binding,
                      std::shared_ptr<const MoeOverlaySet> = {}) override
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

    void beginSelectedInstance(std::optional<ErrorCandidate> failure = {}) override
    {
        if (selectors.size() == 1)
            begins++;
        else
            resumes++;
        if (failure) { failures.push_back(*failure); return; }
        if (on_start)
            on_start();
    }

    void cancelPendingInstance(uint64_t instance = 0) override { cancels++; }

    bool replayQuiescent() const override { return replay_quiescent; }
    bool replay_quiescent = true;
    void finishExecution() override { finishes++; }

    std::vector<std::pair<uint32_t, uint32_t>> selectors;
    uint64_t generation_counter = 0;
    bool kv_reachable = true;
    uint32_t begins = 0;
    uint32_t cancels = 0;
    uint32_t fails = 0;
    uint32_t resumes = 0;
    uint32_t finishes = 0;
    std::function<void()> on_start;
    std::vector<uint8_t> publish_content;
    std::vector<ServingFillBinding> fills;
    std::vector<ServingInstanceBinding> instance_bindings;
    std::array<uint8_t, 32> semantic_digest = {};
};

using test_support::loadFixture;
using test_support::profileBaseDigest;
using test_support::admittedFixture;

inline KvGeometry geometry()
{
    KvGeometry geo;
    geo.region_base = 0x800100000;
    geo.slot_bytes = 4096;
    geo.slot_alignment = 4096;
    geo.max_sessions = 2;
    geo.bytes_per_token = 16;
    return geo;
}

inline KvCapacity capacity()
{
    KvCapacity cap;
    cap.record_entries = 4;
    cap.tombstone_entries = 2;
    cap.admission_wait_entries = 2;
    cap.release_waiter_entries = 2;
    return cap;
}

inline ServingRequestIdentity identity(
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
        program.transport.agent_request_profiles.at(0);
    id.requested_profile_key = requestProfileKey(
        profile, profileBaseDigest(program));
    id.input_bytes = profile.full_input_dma_bytes;
    id.input_tokens = profile.full_input_tokens;
    id.output_tokens = profile.output_tokens;
    id.output_capacity_bytes = profile.host_output_bytes;
    id.kv_bytes_per_token = profile.kv_bytes_per_token;
    return id;
}

inline uint32_t symbolSlot(const ServingInstanceBinding &binding, uint32_t symbol)
{
    const auto &invocation = *binding.invocation();
    const auto &view = invocation.executionView();
    const auto source = invocation.admission().context().symbolBinding(
        view.entrypoint_id, view.profile_id, symbol);
    if (!source)
        throw std::runtime_error("missing scoped symbol binding");
    return source->slot->slot_id;
}

inline std::optional<uint64_t> slotAddress(const ServingInstanceBinding &binding,
    uint32_t slot, uint64_t offset, uint64_t bytes, bool write)
{
    const auto resolved = binding.resolveSlot(slot, offset, bytes, write);
    return resolved ? std::optional<uint64_t>(resolved->address) : std::nullopt;
}

inline uint32_t memberSlot(const ServingInstanceBinding &binding, bool output)
{
    const auto &program = binding.invocation()->admission().program().transport;
    const auto instance = std::find_if(program.agent_instance_profiles.begin(),
        program.agent_instance_profiles.end(), [&](const auto &row) {
            return row.instance_profile_id == binding.instanceProfileId();
        });
    if (instance == program.agent_instance_profiles.end())
        throw std::runtime_error("missing scoped member profile");
    const auto &member = program.agent_instance_member_bindings.at(instance->member_binding_first);
    return symbolSlot(binding, output ? member.static_output_symbol_id : member.static_kv_symbol_id);
}

struct KvStore
{
    uint32_t descriptor_id = 0;
    uint32_t command_id = 0;
};

inline std::vector<KvStore> kvStoreCommands(const DecodedProgram &program)
{
    ProgramSemanticContext context;
    MeshLoadError error;
    if (!buildProgramSemanticContext(program, context, error))
        throw std::runtime_error(error.code + ": " + error.message);
    std::vector<KvStore> out;
    for (const auto &instance : program.transport.agent_instance_profiles) {
        for (uint32_t ordinal = 0; ordinal < instance.member_count; ++ordinal) {
            const auto &member = program.transport.agent_instance_member_bindings.at(instance.member_binding_first + ordinal);
            if (!member.static_kv_symbol_id)
                continue;
            const auto source = context.symbolBinding(instance.mesh_entrypoint_id,
                instance.mesh_profile_id, member.static_kv_symbol_id);
            if (!source)
                throw std::runtime_error("missing scoped KV binding");
            for (const auto &descriptor : program.transport.dma_descriptors)
                if (descriptor.kind == kDmaKindSTORE && descriptor.dst.tensor_id == source->tensor->tensor_id)
                    out.push_back({descriptor.descriptor_id, descriptor.command_id});
        }
    }
    return out;
}

struct Harness
{
    Harness()
        : Harness("tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb")
    {}

    explicit Harness(const std::string &fixture)
        : Harness(loadFixture(fixture))
    {}

    explicit Harness(DecodedProgram loaded, KvGeometry geo = geometry(),
                     KvCapacity limits = capacity())
        : program(std::move(loaded)),
          admission(admittedFixture(program, arch)),
          manager(geo, limits),
          executor(admission, manager, [&]() {
              ServingCapacityRequirement required;
              MeshLoadError error;
              EXPECT_TRUE(servingCapacityRequired(program, required, error));
              return required;
          }(), 8)
    {
        executor.attachDispatch(dispatch);
        executor.setQuantumDriver([this](uint64_t tick) {
            executor.onQuantumEdge(tick);
        });
        dispatch.on_start = [this]() {
            executor.onInstanceStarted(dispatch.generation_counter, 1, 1, 150);
        };
        executor.setDigestSource([](const NpuExecutionRequest &request,
                                    uint32_t) {
            std::array<uint8_t, 32> digest{};
            for (size_t i = 0; i < digest.size(); ++i)
                digest[i] = static_cast<uint8_t>(0x40 + i +
                                                 request.requestId);
            return digest;
        });
    }

    void drainOutput(uint64_t tick)
    {
        const auto &ledger = dispatch.instance_bindings.back().outputLedger();
        if (!ledger)
            return;
        std::vector<const DecodedDmaDescriptor *> outputs;
        for (const auto &descriptor : program.transport.dma_descriptors)
            if (ledger->contains(descriptor.descriptor_id))
                outputs.push_back(&descriptor);
        std::sort(outputs.begin(), outputs.end(), [](const auto *a, const auto *b) {
            return a->dst.offset_bytes < b->dst.offset_bytes;
        });
        for (const auto *descriptor : outputs) {
            if (ledger->decision(descriptor->descriptor_id, tick) == PublishSegmentLedger::Decision::Suppress) {
                ASSERT_TRUE(ledger->suppress(descriptor->descriptor_id, tick));
            } else {
                ASSERT_TRUE(ledger->issue(descriptor->descriptor_id, 0, descriptor->useful_bytes, tick));
                ASSERT_TRUE(ledger->commit(descriptor->descriptor_id, 0, descriptor->useful_bytes, true, tick + 1));
            }
            tick += 2;
        }
    }

    void settleFailedInstance(uint64_t instance, uint64_t request,
                              uint32_t generation, uint64_t tick)
    {
        BatchFailureRecord failure;
        failure.batch_id = failure.program_instance_id = instance;
        failure.first = {tick, {}, agent_abi::E_AXI_RESPONSE};
        executor.onInstanceFailure(failure);
        executor.onInstanceSettled(instance, request, generation, true, tick);
    }

    uint64_t currentInstance() const
    { return dispatch.generation_counter; }

    NpuAdmission admitOnly(const NpuExecutionRequest &request, uint64_t tick)
    {
        executor.setTickSource([tick]() { return tick; });
        executor.setQuantumDriver({});
        const auto result = executor.submit(request);
        executor.setQuantumDriver([this](uint64_t next) {
            executor.onQuantumEdge(next);
        });
        return result;
    }

    std::map<uint64_t, KvRollbackToken> occupyKvSlots()
    {
        std::map<uint64_t, KvRollbackToken> handoffs;
        for (uint64_t request_id : {101, 102}) {
            KvEdgeInputs edge;
            edge.tick = request_id - 100;
            KvAdmissionIntent intent;
            intent.request_id = intent.session_id = intent.kv_handle = request_id;
            intent.generation = 1;
            intent.required_tokens_after_round = 10;
            edge.admissions.push_back(intent);
            const auto result = manager.commitEdge(edge);
            EXPECT_FALSE(result.fatal.has_value());
            EXPECT_TRUE(manager.hasPin(request_id));
            handoffs.insert(result.handoffs.begin(), result.handoffs.end());
        }
        return handoffs;
    }

    DecodedProgram program;
    RuntimeArch arch = test_support::testArch();
    std::shared_ptr<const MeshProgramAdmission> admission;
    MeshKvManager manager;
    RecordingDispatch dispatch;
    ServingMeshExecutor executor;
};

class RecordingSink : public NpuCompletionSink
{
  public:
    void onSessionAdmitted(uint64_t) override { admissions++; }
    void onBatchFrozen(uint64_t) override {}
    void onPublishSegment(const PublishSegmentFact &, bool) override {}
    void onCoreStart(const NpuExecutionRequest &) override { core_starts++; }
    void onRequestComplete(const NpuExecutionRequest &,
                           const NpuExecutionCompletion &value) override
    {
        completions++;
        last = value;
        if (on_complete)
            on_complete(value);
    }

    uint32_t core_starts = 0;
    uint32_t admissions = 0;
    uint32_t completions = 0;
    NpuExecutionCompletion last;
    std::function<void(const NpuExecutionCompletion &)> on_complete;
};

inline NpuExecutionRequest executionRequest(
    uint64_t output_address = 0x100300000,
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
    request.inputAddress = 0x100200000;
    request.outputAddress = output_address;
    request.metadataAddress = 0x100500000;
    request.metadataCapacityBytes = 256;
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
    for (const auto &requirement : program.transport.agent_request_binding_requirements)
        if (requirement.request_program_id == request.programId &&
                requirement.request_profile_id == request.profileId)
            requirements.push_back(requirement);
    request.hostBindings = expectedHostBindings(
        requirements, request.inputAddress, request.inputBytes,
        request.outputAddress, request.outputCapacityBytes,
        geometry().slot_bytes);
    return request;
}

inline void driveOneGenerate(Harness &harness, const std::vector<KvStore> &stores,
                      uint32_t first = 1)
{
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
    harness.drainOutput(500);
    harness.executor.onInstanceSettled(4, 1, 1, false, 520);
}


}

#endif

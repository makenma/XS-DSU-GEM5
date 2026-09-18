#ifndef DEV_AI_MESH_SERVING_MESH_EXECUTOR_HH
#define DEV_AI_MESH_SERVING_MESH_EXECUTOR_HH

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <functional>

#include "dev/ai_mesh/kv_token_ledger.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"
#include "dev/ai_mesh/npu_request_executor.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/serving_mesh_dispatch.hh"
#include "dev/ai_mesh/serving_request_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct ServingPhaseFact
{
    uint32_t phase = 0;
    uint32_t instance_profile_id = 0;
    uint32_t mesh_profile_id = 0;
    uint32_t kv_tokens_before = 0;
    uint32_t append_tokens = 0;
    uint32_t accepted_descriptors = 0;
    uint32_t terminal_descriptors = 0;
    uint64_t core_drain_tick = 0;
    uint64_t append_terminal_tick = 0;
    uint64_t commit_tick = 0;
};

class ServingMeshExecutor : public MeshDmaObserver,
                          public NpuRequestExecutor,
                          public MeshInstanceObserver
{
  public:
    ServingMeshExecutor(const DecodedProgram &program, MeshKvManager &kv);

    void attachDispatch(ServingMeshDispatch &dispatch)
    {
        mesh_dispatch = &dispatch;
    }

    using PhaseFactSink = std::function<void(const ServingPhaseFact &)>;
    using DigestSource = std::function<std::array<uint8_t, 32>(
        const NpuExecutionRequest &, uint64_t output_bytes)>;

    void setDigestSource(DigestSource source) { digest_source = std::move(source); }
    void setPhaseFactSink(PhaseFactSink sink) { phase_fact_sink = std::move(sink); }
    void setTickSource(std::function<uint64_t()> source)
    { tick_source = std::move(source); }

    NpuExecutorKind kind() const override
    { return NpuExecutorKind::ServingMesh; }
    NpuAdmission submit(const NpuExecutionRequest &request) override;
    uint64_t modeledServiceNs(const NpuExecutionRequest &) const override
    { return 0; }
    NpuExecutionCompletion completionFor(
        const NpuExecutionRequest &request) const override;

    bool start(const ServingRequestIdentity &identity, uint64_t tick,
               std::string &reason);
    bool beginFirstPhase(uint64_t tick, std::string &reason);
    void onInstanceStarted(uint64_t instance_generation, uint64_t request_id,
                           uint32_t request_generation,
                           uint64_t tick) override;
    void onInstanceSettled(uint64_t instance_generation, uint64_t request_id,
                           uint32_t request_generation, bool errored,
                           uint64_t tick) override;
    void onDmaAccepted(uint32_t descriptor_id, uint32_t command_id,
                       uint64_t instance_generation, uint64_t request_id,
                       uint32_t request_generation, uint64_t tick) override;
    void onDmaTerminal(
        uint32_t descriptor_id, uint32_t command_id, uint64_t committed_bytes,
        uint32_t error_code, uint64_t instance_generation, uint64_t request_id,
        uint32_t request_generation, uint64_t tick,
        const std::vector<DmaCommittedSegment> &committed_segments) override;
    bool abortBeforeStart(uint64_t tick, std::string &reason);

    bool finished() const { return phase_finished; }
    bool failed() const { return failed_flag; }
    const std::string &failureReason() const { return failure_reason; }
    uint32_t phasesCommitted() const { return phases_committed; }
    bool bindRequestTensors(const NpuExecutionRequest &request,
                            std::string &reason);
    bool bindPhaseFillContent(const NpuExecutionRequest &request,
                              std::string &reason);
    bool verifyHostBindings(const NpuExecutionRequest &request,
                            std::string &reason) const;
    const std::vector<ServingPhaseFact> &phaseFacts() const
    { return phase_facts; }
    uint32_t instanceSettles() const { return instance_settles; }
    uint32_t staleInstanceFacts() const { return stale_instance_facts; }
    uint32_t acceptedKvDescriptors() const { return accepted_kv_descriptors; }
    uint32_t terminalKvDescriptors() const { return terminal_kv_descriptors; }
    uint64_t lastTick() const { return last_tick; }
    const ServingRequestContext &context() const { return request_context; }
    const std::optional<KvTerminalSnapshot> &kvTerminal() const
    {
        return kv_terminal_snapshot;
    }
    uint64_t terminalTick() const { return kv_terminal_tick; }

  private:
    struct KvDescriptorBinding
    {
        uint32_t command_id = 0;
        uint32_t descriptor_id = 0;
        uint32_t first_token = 0;
        uint32_t tokens = 0;
        uint32_t rows = 1;
        uint64_t append_offset = 0;
        uint64_t dst_stride_bytes = 0;
        uint64_t row_bytes = 0;
    };

    bool sealTerminalSnapshot(KvTerminalStatus status, uint64_t tick,
                              std::string &reason);
    void settleDeferredTerminal();
    void commitPendingFault();
    bool terminalJoinSatisfied() const;
    bool commitEdge(KvEdgeInputs &edge, KvEdgeResult &result,
                    std::string &reason);
    bool buildPhaseKvDescriptors(std::string &reason);
    bool commitDmaCount(const char *kind, uint32_t count, uint64_t tick,
                        std::string &reason);
    bool finishAppend(uint64_t tick, std::string &reason);
    bool submitPhaseEdge(uint64_t tick, std::string &reason);
    bool advance(uint64_t tick, std::string &reason);
    void recordStaleFact(uint64_t tick);
    bool factBelongsToThisRequest(uint64_t request_id,
                                  uint32_t request_generation, uint64_t tick);
    enum class InstanceFactVerdict
    {
        Current,
        PendingStart,
        Stale,
        Violation,
    };
    InstanceFactVerdict classifyInstanceFact(uint64_t instance_generation,
                                             uint64_t tick);
    bool rollbackPrestart(KvTerminalStatus status, std::string &reason);
    bool latchRunFailure(const std::string &reason);
    bool markFailed(const std::string &reason);
    uint64_t planOutputBytes() const;
    static uint32_t rejectDetailOf(const std::string &reason);
    void reportCompletion();
    std::vector<std::pair<uint32_t, uint32_t>> selectors(
        std::string &reason) const;

    const DecodedProgram *program;
    MeshKvManager *kv;
    ServingMeshDispatch *mesh_dispatch = nullptr;
    ServingRequestContext request_context;
    std::optional<KvRollbackToken> rollback_token;
    uint64_t last_tick = 0;
    uint32_t phases_committed = 0;
    uint32_t instance_settles = 0;
    uint32_t stale_instance_facts = 0;
    // The mesh instance ordinal the dispatcher armed this phase with.  It is
    // the fact identity: request identity alone cannot separate phases of the
    // same request, and per-phase descriptor/command ids repeat across them.
    std::optional<uint64_t> armed_instance_generation;
    std::optional<uint64_t> settled_instance_generation;
    bool started = false;
    bool first_phase_pending = false;
    uint32_t phase_append_tokens = 0;
    std::optional<KvTokenLedger> append_ledger;
    std::vector<KvDescriptorBinding> kv_descriptors;
    std::vector<bool> kv_accepted;
    std::vector<bool> kv_terminal;
    uint32_t accepted_kv_descriptors = 0;
    uint32_t terminal_kv_descriptors = 0;
    uint64_t request_start_tick = 0;
    std::optional<KvTerminalSnapshot> kv_terminal_snapshot;
    uint64_t kv_terminal_tick = 0;
    bool ownership_claimed = false;
    bool terminal_deferred = false;
    uint32_t drained_instance = 0;
    std::optional<KvFaultEvent> pending_fault;
    std::vector<ServingPhaseFact> phase_facts;
    bool phase_finished = false;
    bool failed_flag = false;
    bool completion_reported = false;
    std::string failure_reason;
    std::optional<NpuExecutionRequest> submitted_request;
    DigestSource digest_source;
    PhaseFactSink phase_fact_sink;
    ServingInstanceBinding instance_binding;
    std::array<uint8_t, 32> frozen_digest = {};
    bool digest_frozen = false;
    std::function<uint64_t()> tick_source;
};

}
}
#endif

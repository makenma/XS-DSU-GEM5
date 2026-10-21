#ifndef DEV_AI_MESH_SERVING_MESH_EXECUTOR_HH
#define DEV_AI_MESH_SERVING_MESH_EXECUTOR_HH

#include <algorithm>
#include <array>
#include <cstdint>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <functional>

#include "base/logging.hh"
#include "dev/ai_mesh/kv_token_ledger.hh"
#include "dev/ai_mesh/mesh_dma_observer.hh"
#include "dev/ai_mesh/npu_request_executor.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/mesh_serving_scheduler.hh"
#include "dev/ai_mesh/mesh_serving_batch.hh"
#include "dev/ai_mesh/serving_phase_fact.hh"
#include "dev/ai_mesh/serving_batch_replay.hh"
#include "dev/ai_mesh/serving_mesh_dispatch.hh"
#include "dev/ai_mesh/serving_request_context.hh"

namespace gem5
{
namespace ai_mesh
{


class ServingMeshExecutor : public MeshDmaObserver,
                          public NpuRequestExecutor,
                          public MeshInstanceObserver,
                          public KvEdgeObserver
{
  public:
    ServingMeshExecutor(std::shared_ptr<const MeshProgramAdmission> admission, MeshKvManager &kv,
                        const ServingCapacityRequirement &capacity,
                        uint32_t max_contexts);
    ~ServingMeshExecutor() override;
    void onKvEdgeCommitted(const KvEdgeResult &result) override;

    void attachDispatch(ServingMeshDispatch &dispatch)
    {
        dispatch.configureServingCapacity(max_contexts);
        mesh_dispatch = &dispatch;
    }

    using PhaseFactSink = std::function<void(const ServingPhaseFact &)>;
    using ReleaseOutcomeSink = std::function<void(const KvEdgeResult &)>;
    void setReleaseOutcomeSink(ReleaseOutcomeSink sink)
    { release_outcome_sink = std::move(sink); }
    using DigestSource = std::function<std::array<uint8_t, 32>(
        const NpuExecutionRequest &, uint32_t output_tokens)>;

    void setDigestSource(DigestSource source) { digest_source = std::move(source); }
    void setPhaseFactSink(PhaseFactSink sink) { phase_fact_sink = std::move(sink); }
    void setTickSource(std::function<uint64_t()> source)
    { tick_source = std::move(source); }
    void setSchedulerLimits(const SchedulerLimits &limits)
    { scheduler_limits = limits; }
    using QuantumDriver = std::function<void(uint64_t tick)>;
    void setQuantumDriver(QuantumDriver driver)
    { quantum_driver = std::move(driver); }
    void setClockPeriod(uint64_t period)
    {
        fatal_if(period == 0, "scheduler clock period must be positive");
        clock_period_ticks = period;
    }
    void onQuantumEdge(uint64_t tick);
    void configureReplay(std::optional<ServingBatchReplay> replay, bool strict);
    template<class Archive> void serialize(Archive &archive, unsigned)
    {
        if (!programBoundaryReady())
            throw std::runtime_error("E_CHECKPOINT_BOUNDARY: serving executor is active");
        checkpoint::fields(archive, latest_instance, run_finished, last_tick,
                           stale_instance_facts, replay_cursor);
    }

    void onReplayMemberTerminal(const ServingReplayIdentity &identity) const;

    NpuExecutorKind kind() const override
    { return NpuExecutorKind::ServingMesh; }
    NpuAdmission submit(const NpuExecutionRequest &request) override;
    void retireCompletedRequest(uint64_t request_id);
    void finishServingRun();
    bool programBoundaryReady() const;
    std::set<uint32_t> replayBoundaryAdmissions(const std::set<uint32_t> &activeItems) const;
    uint64_t modeledServiceNs(const NpuExecutionRequest &) const override
    { return 0; }
    NpuExecutionCompletion completionFor(
        const NpuExecutionRequest &request) const override;

    void onInstanceStarted(uint64_t instance_generation, uint64_t request_id,
                           uint32_t request_generation,
                           uint64_t tick) override;
    void onInstanceFailure(const BatchFailureRecord &record) override;
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
    bool abortBeforeStart(uint64_t request_id, uint64_t tick, std::string &reason);
    enum class CancelResult { Accepted, NotFound, AlreadyTerminal };
    CancelResult requestCancel(uint64_t request_id, uint64_t tick);

    bool finished(uint64_t request_id) const { return slot(request_id).phase_finished; }
    bool failed(uint64_t request_id) const { return slot(request_id).failed_flag; }
    const std::string &failureReason(uint64_t request_id) const { return slot(request_id).failure_reason; }
    uint32_t phasesCommitted(uint64_t request_id) const { return slot(request_id).phases_committed; }
    const std::vector<ServingPhaseFact> &phaseFacts(uint64_t request_id) const
    { return slot(request_id).phase_facts; }
    uint32_t instanceSettles(uint64_t request_id) const { return slot(request_id).instance_settles; }
    uint32_t staleInstanceFacts() const { return stale_instance_facts; }
    uint32_t acceptedKvDescriptors(uint64_t request_id) const
    { return slot(request_id).accepted_kv_descriptors; }
    uint32_t terminalKvDescriptors(uint64_t request_id) const
    { return slot(request_id).terminal_kv_descriptors; }
    uint64_t lastTick() const { return last_tick; }
    const ServingRequestContext &context(uint64_t request_id) const
    { return slot(request_id).request_context; }
    const std::optional<KvTerminalSnapshot> &kvTerminal(uint64_t request_id) const
    {
        return slot(request_id).kv_terminal_snapshot;
    }
    uint64_t terminalTick(uint64_t request_id) const { return slot(request_id).terminal_tick; }

  private:
    std::optional<ServingBatchReplay> batch_replay;
    size_t replay_cursor = 0;
    bool strict_replay = false;
    SchedulerDecision selectReplayBatch(uint64_t tick, uint64_t available) const;
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

    struct RequestSlot
    {
        RequestSlot(const DecodedProgram &program,
                    const ServingRequestIdentity &identity)
            : request_context(program, identity)
        {}

        void clearKvDescriptors()
        {
            append_ledger.reset();
            kv_descriptors.clear();
            kv_accepted.clear();
            kv_terminal.clear();
        }

        ServingRequestContext request_context;
        std::optional<KvRollbackToken> rollback_token;
        std::optional<NpuExecutionRequest> submitted_request;
        uint32_t phases_committed = 0;
        uint32_t output_tokens_committed = 0;
        uint64_t output_bytes_committed = 0;
        uint32_t instance_settles = 0;
        std::optional<uint64_t> started_instance;
        std::optional<uint64_t> settled_instance;
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
        uint64_t terminal_tick = 0;
        bool ownership_claimed = false;
        std::optional<agent_abi::DetailCode> admission_reject_detail;
        std::optional<agent_abi::DetailCode> infrastructure_failure;
        bool terminal_deferred = false;
        std::optional<KvFaultEvent> pending_fault;
        std::vector<ServingPhaseFact> phase_facts;
        bool phase_finished = false;
        bool failed_flag = false;
        std::optional<NpuExecutionOutcome> terminal_outcome;
        std::optional<uint64_t> cancel_tick;
        RequestTerminalPosition cancel_position;
        std::optional<uint64_t> first_error_tick;
        std::optional<ErrorCandidate> batch_failure;
        bool cancelled_before_batch_failure = false;
        std::string failure_reason;
        std::array<uint8_t, 32> frozen_digest = {};
        bool digest_frozen = false;
        bool output_chunk_notified = false;
        uint64_t kv_admission_ready_tick = 0;
        std::optional<uint64_t> phase_ready_tick;
    };

    struct BatchContext
    {
        explicit BatchContext(const ServingCapacityRequirement &capacity)
            : bindings(capacity)
        { members.reserve(capacity.instance_member_binding_entries); }
        std::vector<ServingMemberIdentity> members;
        const mesh_abi::AgentInstanceProfile *profile = nullptr;
        std::optional<InstanceGeneration> selected_instance;
        std::optional<uint64_t> armed_instance_generation;
        std::optional<uint64_t> settled_instance_generation;
        std::optional<uint64_t> join_ready_tick;
        MeshServingBatch bindings;
        std::shared_ptr<const MoeOverlaySet> overlays;
        std::optional<ErrorCandidate> preparation_failure;
        void release()
        {
            members.clear();
            profile = nullptr;
            selected_instance.reset();
            armed_instance_generation.reset();
            join_ready_tick.reset();
            bindings.release();
            overlays.reset();
            preparation_failure.reset();
        }
        bool draining() const
        {
            return armed_instance_generation &&
                armed_instance_generation != settled_instance_generation;
        }
    };

    RequestSlot &slot(uint64_t request_id)
    {
        const auto it = slots.find(request_id);
        fatal_if(it == slots.end(), "unknown request slot %llu", request_id);
        return it->second;
    }
    const RequestSlot &slot(uint64_t request_id) const
    {
        const auto it = slots.find(request_id);
        fatal_if(it == slots.end(), "unknown request slot %llu", request_id);
        return it->second;
    }
    BatchContext &batchFor(const RequestSlot &current)
    {
        return const_cast<BatchContext &>(std::as_const(*this).batchFor(current));
    }
    const BatchContext &batchFor(const RequestSlot &current) const
    {
        const auto &identity = current.request_context.identity();
        for (const auto &candidate : batches)
            for (const auto &member : candidate->members)
                if (member.request_id == identity.request_id && member.generation == identity.generation)
                    return *candidate;
        fatal("request does not own a serving batch");
    }

    bool ownsBatch(const RequestSlot &current) const
    {
        const auto &identity = current.request_context.identity();
        for (const auto &candidate : batches)
            for (const auto &member : candidate->members)
                if (member.request_id == identity.request_id && member.generation == identity.generation)
                    return true;
        return false;
    }

    bool prepareRequest(RequestSlot &current, uint64_t tick, std::string &reason);
    bool admit(RequestSlot &current, uint64_t tick, std::string &reason);
    bool terminalizePendingAdmission(RequestSlot &current, KvTerminalStatus status,
                                     std::string &reason);
    bool bindPhaseFillContent(RequestSlot &current, const NpuExecutionRequest &request,
                              uint32_t ordinal, std::string &reason);
    bool verifyHostBindings(const NpuExecutionRequest &request,
                            std::string &reason) const;
    bool sealTerminalSnapshot(RequestSlot &current, KvTerminalStatus status, uint64_t tick,
                              std::string &reason);
    void settleDeferredTerminal(RequestSlot &current);
    void commitPendingFault(RequestSlot &current);
    bool terminalJoinSatisfied(const RequestSlot &current) const;
    bool commitEdge(KvEdgeInputs &edge, KvEdgeResult &result,
                    std::string &reason);
    bool buildPhaseKvDescriptors(RequestSlot &current, uint32_t ordinal, std::string &reason);
    bool commitDmaCount(RequestSlot &current, const char *kind, uint32_t count, uint64_t tick,
                        std::string &reason);
    bool finishAppend(RequestSlot &current, uint64_t tick, std::string &reason);
    bool submitPhaseEdge(BatchContext &batch, uint64_t tick, std::string &reason);
    void notePhaseJoin(RequestSlot &current, uint64_t tick);
    bool commitPhaseJoin(RequestSlot &current, uint64_t tick, std::string &reason);
    void recordStaleFact(uint64_t tick);
    RequestSlot *requestForFact(uint64_t request_id,
                                  uint32_t request_generation, uint64_t tick);
    enum class InstanceFactVerdict
    {
        Current,
        PendingStart,
        Stale,
        Violation,
    };
    InstanceFactVerdict classifyInstanceFact(RequestSlot &current, uint64_t instance_generation,
                                             uint64_t tick);
    SchedulerDecision selectBatch(uint64_t tick) const;
    std::vector<ServingMemberIdentity> instanceMembersForFact(
        uint64_t instance, uint64_t request, uint32_t generation, uint64_t tick);
    void startMember(uint64_t instance, uint64_t request, uint32_t generation, uint64_t tick);
    void errorMember(uint64_t instance, uint64_t request, uint32_t generation, uint64_t tick);
    void settleMember(uint64_t instance, uint64_t request, uint32_t generation,
                      bool errored, uint64_t tick);
    bool rollbackPrestart(RequestSlot &current, KvTerminalStatus status, std::string &reason);
    void cancelPendingInstance(RequestSlot &current);
    bool latchRunFailure(RequestSlot &current, const std::string &reason);
    KvTerminalStatus failureTerminalStatus(const RequestSlot &current) const;
    void bindCancellation(RequestSlot &current);
    bool markFailed(RequestSlot &current, const std::string &reason);
    uint64_t planOutputBytes(const RequestSlot &current) const;
    static uint32_t rejectDetailOf(const std::string &reason);
    void reportCompletion(RequestSlot &current, NpuExecutionOutcome outcome);
    std::shared_ptr<const MeshProgramAdmission> program_admission;
    const DecodedProgram *program;
    const ExecutionViewSet *execution_views;
    const ProfileExecutionView &executionView(
        const mesh_abi::AgentInstanceProfile &) const;
    MeshKvManager *kv;
    ServingMeshDispatch *mesh_dispatch = nullptr;
    std::map<uint64_t, RequestSlot> slots;
    const uint32_t max_contexts;
    std::vector<std::unique_ptr<BatchContext>> batches;
    uint64_t latest_instance = 0;
    bool run_finished = false;
    uint64_t last_tick = 0;
    uint32_t stale_instance_facts = 0;
    DigestSource digest_source;
    PhaseFactSink phase_fact_sink;
    ReleaseOutcomeSink release_outcome_sink;
    SchedulerLimits scheduler_limits;
    std::function<uint64_t()> tick_source;
    QuantumDriver quantum_driver;
    uint64_t clock_period_ticks = 1;
    std::optional<uint64_t> quantum_scheduled_tick;
    void requestQuantum(uint64_t tick);
    void requestNextQuantum(uint64_t ready_tick);

};

}
}
#endif

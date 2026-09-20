#ifndef DEV_AI_MESH_MESH_RUNTIME_OBSERVATIONS_HH
#define DEV_AI_MESH_MESH_RUNTIME_OBSERVATIONS_HH

#include <array>
#include <cstdint>
#include <map>
#include <ostream>
#include <string>
#include <vector>

#include "base/types.hh"
#include "dev/ai_mesh/dma_records.hh"

namespace gem5
{
namespace ai_mesh
{

struct CommandGeneration
{
    uint32_t command_id = 0;
    uint32_t generation = 0;

    bool operator<(const CommandGeneration &other) const
    {
        return command_id != other.command_id ? command_id < other.command_id
                                              : generation < other.generation;
    }
    bool operator==(const CommandGeneration &other) const
    {
        return command_id == other.command_id && generation == other.generation;
    }
};

enum class EngineKind
{
    Tensor,
    Vector,
    Reduce,
};

const char *engineKindName(EngineKind kind);

struct DescriptorKey
{
    CommandGeneration command;
    uint32_t descriptor_id = 0;

    bool operator<(const DescriptorKey &other) const
    {
        if (command < other.command)
            return true;
        if (other.command < command)
            return false;
        return descriptor_id < other.descriptor_id;
    }
};

struct TrafficContribution
{
    uint64_t read_bytes = 0;
    uint64_t write_bytes = 0;
    uint64_t p2p_bytes = 0;
    uint64_t fill_bytes = 0;
    uint32_t read_bursts = 0;
    uint32_t write_bursts = 0;
    uint32_t p2p_bursts = 0;
    std::string payload_digest;
};

struct CommandObservation
{
    CommandGeneration command;
    bool issued = false;
    Tick issue_tick = 0;
    bool terminal = false;
    Tick terminal_tick = 0;
};

struct EngineExecutionObservation
{
    CommandGeneration command;
    EngineKind engine = EngineKind::Tensor;
    Tick begin_tick = 0;
    Tick scheduled_end_tick = 0;
    bool ended = false;
    Tick end_tick = 0;
    uint64_t cycles = 0;
};

struct EngineBlockObservation
{
    CommandGeneration command;
    EngineKind engine = EngineKind::Tensor;
    uint32_t episode_index = 0;
    Tick first_reject_tick = 0;
    Tick last_reject_tick = 0;
    uint32_t occupied = 0;
    uint32_t depth = 0;
    std::vector<CommandGeneration> holders;
};

// Physical content evidence of one contiguous written or read byte run.
struct ContentRowObservation
{
    uint64_t address = 0;
    uint64_t size = 0;
    std::string digest;
    // Post-quiescence readback or producer read of the whole run, present only
    // when the run fits the configured byte-dump limit, so a reviewer can
    // compare real bytes instead of trusting the digest.
    std::string bytes_hex;
};

struct ComputeOutputObservation
{
    CommandGeneration command;
    uint32_t allocation_id = 0;
    uint64_t offset = 0;
    std::array<uint32_t, 4> digest_words{0, 0, 0, 0};
    std::vector<ContentRowObservation> rows;
    std::vector<ContentRowObservation> merged_rows;
};

struct SentinelRangeObservation
{
    std::string kind;
    uint64_t address = 0;
    uint64_t size = 0;
    bool available = false;
    std::string before_digest;
    std::string after_digest;
};

struct DestinationStorageObservation
{
    uint32_t allocation_id = 0;
    uint64_t base = 0;
    uint64_t bytes = 0;
};

struct DescriptorObservation
{
    CommandGeneration command;
    uint32_t descriptor_id = 0;
    Tick submit_tick = 0;
    bool scheduled = false;
    Tick scheduled_completion_tick = 0;
    bool completed = false;
    Tick completion_tick = 0;
    bool committed = false;
    Tick commit_tick = 0;
    bool has_status = false;
    DmaStatus status = DmaStatus::OK;
    bool has_transfer = false;
    TrafficContribution transfer;
    std::string landing_digest;
    bool has_destination_storage = false;
    DestinationStorageObservation destination_storage;
    std::vector<ContentRowObservation> source_rows;
    std::vector<SentinelRangeObservation> sentinel_ranges;
    std::string fault_target_before_digest;
    std::string fault_target_after_digest;
};

struct TransferObservation
{
    uint32_t transfer_id = 0;
    uint32_t expected_descriptors = 0;
    uint32_t committed_descriptors = 0;
    uint64_t expected_bytes = 0;
    uint64_t committed_bytes = 0;
    bool failed = false;
    bool sender_published = false;
    uint32_t sender_notifications = 0;
};

struct PendingSpanObservation
{
    uint32_t descriptor_id = 0;
    uint64_t size = 0;
    std::string digest;
};

struct TransferCommitObservation
{
    CommandGeneration command;
    uint32_t descriptor_id = 0;
    uint32_t transfer_id = 0;
    Tick commit_tick = 0;
    uint64_t logical_start = 0;
    uint64_t logical_bytes = 0;
    std::string source_digest;
    std::string target_digest;
    std::string target_initial_digest;
    uint64_t committed_bytes = 0;
    uint64_t expected_bytes = 0;
    uint32_t sender_notifications = 0;
    bool sender_published = false;
    uint64_t pending_bytes = 0;
    std::string pending_digest;
    std::vector<PendingSpanObservation> pending_spans;
    bool receiver_present = false;
    uint16_t receiver_core = 0;
    bool receiver_transfer_committed = false;
    uint32_t receiver_allocation_id = 0;
    std::vector<uint32_t> receiver_admitted_allocations;
    uint32_t receiver_notified_allocation_id = 0;
    bool receiver_allocation_valid = false;
    uint32_t receiver_notifications = 0;
    Tick receiver_notification_tick = 0;
};

struct ObservationFrame
{
    uint32_t instance_id = 0;
    uint16_t core_id = 0;
    std::vector<CommandObservation> commands;
    std::vector<EngineExecutionObservation> engine_executions;
    std::vector<EngineBlockObservation> engine_blocks;
    std::vector<ComputeOutputObservation> compute_outputs;
    std::vector<DescriptorObservation> descriptors;
    std::vector<TransferObservation> transfers;
    std::vector<TransferCommitObservation> transfer_commits;
    std::vector<uint32_t> residency_begin;
    std::vector<uint32_t> residency_end;
};

void writeObservationFrameJson(std::ostream &out, const ObservationFrame &frame);

// One JSON writer for sentinel evidence, shared by the observation frame and
// the real target owners so the semantics cannot drift.
void writeSentinelRanges(std::ostream &out,
                         const std::vector<SentinelRangeObservation> &ranges);

class RuntimeObservations
{
  public:
    void beginFrame(uint32_t instance_id, uint16_t core_id);
    bool frameActive() const { return active; }
    uint32_t instanceId() const { return frame.instance_id; }
    uint16_t coreId() const { return frame.core_id; }

    void recordCommandAdmission(const CommandGeneration &command, Tick issue_tick);
    void recordCommandTerminal(const CommandGeneration &command, Tick terminal_tick);
    void recordEnginePlan(const CommandGeneration &command, EngineKind engine,
                          uint64_t cycles,
                          Tick begin_tick, Tick scheduled_end_tick);
    void recordEngineEnd(const CommandGeneration &command, Tick end_tick);
    void recordEngineBlock(const CommandGeneration &command, EngineKind engine,
                           const std::vector<CommandGeneration> &holders,
                           Tick tick, uint32_t occupied, uint32_t depth);
    void closeEngineEpisode(const CommandGeneration &command);
    void recordComputeOutput(const CommandGeneration &command,
                             uint32_t allocation_id, uint64_t offset,
                             const std::array<uint32_t, 4> &digest_words,
                             const std::vector<ContentRowObservation> &rows,
                             const std::vector<ContentRowObservation> &merged_rows);
    void recordDescriptorSourceRows(
        const DescriptorKey &key,
        const std::vector<ContentRowObservation> &rows);
    void recordDescriptorSubmission(const DescriptorKey &key, Tick submit_tick,
                                    Tick scheduled_completion_tick);
    void recordDescriptorCompletion(const DescriptorKey &key, Tick completion_tick,
                                    bool committed, Tick commit_tick,
                                    DmaStatus status,
                                    const TrafficContribution &transfer);
    void recordFillLanding(
        const DescriptorKey &key, const std::string &landing_digest,
        const DestinationStorageObservation &destination_storage,
        const std::vector<SentinelRangeObservation> &sentinel_ranges);
    void recordFaultTarget(const DescriptorKey &key,
                           const std::string &before_digest,
                           const std::string &after_digest);
    void recordTransferProgress(const TransferObservation &transfer);
    void recordTransferCommit(const TransferCommitObservation &commit);
    void recordResidencyBegin(const std::vector<uint32_t> &allocations);
    void recordResidencyEnd(const std::vector<uint32_t> &allocations);

    const ObservationFrame &view() const { return frame; }
    ObservationFrame takeFrame();

    const CommandObservation *findCommand(const CommandGeneration &command) const;
    const EngineExecutionObservation *
    findEngineExecution(const CommandGeneration &command) const;
    const DescriptorObservation *findDescriptor(const DescriptorKey &key) const;

  private:
    ObservationFrame frame;
    bool active = false;
    std::map<CommandGeneration, size_t> command_index;
    std::map<CommandGeneration, size_t> execution_index;
    std::map<DescriptorKey, size_t> descriptor_index;
    std::map<uint32_t, size_t> transfer_index;
    std::map<CommandGeneration, size_t> open_block_index;

    void clearIndexes();
};

} // namespace ai_mesh
} // namespace gem5

#endif

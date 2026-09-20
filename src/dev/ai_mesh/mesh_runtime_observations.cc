#include "dev/ai_mesh/mesh_runtime_observations.hh"

#include <algorithm>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

const char *
engineKindName(EngineKind kind)
{
    switch (kind) {
      case EngineKind::Tensor:
        return "tensor";
      case EngineKind::Vector:
        return "vector";
      case EngineKind::Reduce:
        return "reduce";
    }
    return "unknown";
}

void
RuntimeObservations::clearIndexes()
{
    command_index.clear();
    execution_index.clear();
    descriptor_index.clear();
    open_block_index.clear();
    transfer_index.clear();
}

void
RuntimeObservations::beginFrame(uint32_t instance_id, uint16_t core_id)
{
    fatal_if(active, "core %u opens an observation frame while instance %u is "
                     "still unarchived",
             core_id, frame.instance_id);
    frame = ObservationFrame();
    frame.instance_id = instance_id;
    frame.core_id = core_id;
    clearIndexes();
    active = true;
}

ObservationFrame
RuntimeObservations::takeFrame()
{
    fatal_if(!active, "core %u hands over an observation frame it does not own",
             frame.core_id);
    ObservationFrame taken = std::move(frame);
    frame = ObservationFrame();
    clearIndexes();
    active = false;
    return taken;
}

void
RuntimeObservations::recordCommandAdmission(const CommandGeneration &command,
                                            Tick issue_tick)
{
    fatal_if(!active, "command admission observation outside an instance frame");
    const auto existing = command_index.find(command);
    if (existing != command_index.end()) {
        CommandObservation &row = frame.commands[existing->second];
        fatal_if(row.issued,
                 "command (%u,%u) records a second admission",
                 command.command_id, command.generation);
        row.issued = true;
        row.issue_tick = issue_tick;
        return;
    }
    command_index[command] = frame.commands.size();
    CommandObservation row;
    row.command = command;
    row.issued = true;
    row.issue_tick = issue_tick;
    frame.commands.push_back(std::move(row));
}

void
RuntimeObservations::recordCommandTerminal(const CommandGeneration &command,
                                           Tick terminal_tick)
{
    fatal_if(!active, "command terminal observation outside an instance frame");
    const auto existing = command_index.find(command);
    if (existing == command_index.end()) {
        command_index[command] = frame.commands.size();
        CommandObservation row;
        row.command = command;
        row.terminal = true;
        row.terminal_tick = terminal_tick;
        frame.commands.push_back(std::move(row));
        return;
    }
    CommandObservation &row = frame.commands[existing->second];
    fatal_if(row.terminal, "command (%u,%u) records a second terminal",
             command.command_id, command.generation);
    row.terminal = true;
    row.terminal_tick = terminal_tick;
}

void
RuntimeObservations::recordEnginePlan(const CommandGeneration &command,
                                      EngineKind engine, uint64_t cycles,
                                      Tick begin_tick, Tick scheduled_end_tick)
{
    fatal_if(!active, "engine plan observation outside an instance frame");
    fatal_if(execution_index.count(command) != 0,
             "command (%u,%u) records a second engine plan",
             command.command_id, command.generation);
    execution_index[command] = frame.engine_executions.size();
    EngineExecutionObservation row;
    row.command = command;
    row.engine = engine;
    row.begin_tick = begin_tick;
    row.scheduled_end_tick = scheduled_end_tick;
    row.cycles = cycles;
    frame.engine_executions.push_back(std::move(row));
}

void
RuntimeObservations::recordEngineEnd(const CommandGeneration &command,
                                     Tick end_tick)
{
    fatal_if(!active, "engine end observation outside an instance frame");
    const auto existing = execution_index.find(command);
    fatal_if(existing == execution_index.end(),
             "command (%u,%u) ends an engine execution it never planned",
             command.command_id, command.generation);
    EngineExecutionObservation &row = frame.engine_executions[existing->second];
    fatal_if(row.ended, "command (%u,%u) records a second engine end",
             command.command_id, command.generation);
    row.ended = true;
    row.end_tick = end_tick;
}

void
RuntimeObservations::recordEngineBlock(
    const CommandGeneration &command, EngineKind engine,
    const std::vector<CommandGeneration> &holders, Tick tick, uint32_t occupied,
    uint32_t depth)
{
    fatal_if(!active, "engine block observation outside an instance frame");
    const auto open = open_block_index.find(command);
    if (open != open_block_index.end()) {
        EngineBlockObservation &row = frame.engine_blocks[open->second];
        if (row.occupied == occupied && row.holders == holders) {
            row.last_reject_tick = tick;
            return;
        }
    }
    uint32_t episode_index = 0;
    for (const EngineBlockObservation &row : frame.engine_blocks)
        if (row.command == command && row.engine == engine)
            episode_index++;
    EngineBlockObservation row;
    row.command = command;
    row.engine = engine;
    row.episode_index = episode_index;
    row.first_reject_tick = tick;
    row.last_reject_tick = tick;
    row.occupied = occupied;
    row.depth = depth;
    row.holders = holders;
    frame.engine_blocks.push_back(std::move(row));
    open_block_index[command] = frame.engine_blocks.size() - 1;
}

void
RuntimeObservations::closeEngineEpisode(const CommandGeneration &command)
{
    open_block_index.erase(command);
}

void
RuntimeObservations::recordComputeOutput(
    const CommandGeneration &command, uint32_t allocation_id, uint64_t offset,
    const std::array<uint32_t, 4> &digest_words,
    const std::vector<ContentRowObservation> &rows,
    const std::vector<ContentRowObservation> &merged_rows)
{
    fatal_if(!active, "compute output observation outside an instance frame");
    for (const ComputeOutputObservation &row : frame.compute_outputs)
        fatal_if(row.command == command,
                 "command (%u,%u) records a second compute output",
                 command.command_id, command.generation);
    ComputeOutputObservation row;
    row.command = command;
    row.allocation_id = allocation_id;
    row.offset = offset;
    row.digest_words = digest_words;
    row.rows = rows;
    row.merged_rows = merged_rows;
    frame.compute_outputs.push_back(std::move(row));
}

void
RuntimeObservations::recordDescriptorSourceRows(
    const DescriptorKey &key, const std::vector<ContentRowObservation> &rows)
{
    fatal_if(!active, "descriptor source rows outside an instance frame");
    const auto found = descriptor_index.find(key);
    fatal_if(found == descriptor_index.end(),
             "descriptor %u source rows without a submission record",
             key.descriptor_id);
    frame.descriptors[found->second].source_rows = rows;
}

void
RuntimeObservations::recordDescriptorSubmission(const DescriptorKey &key,
                                                Tick submit_tick,
                                                Tick scheduled_completion_tick)
{
    fatal_if(!active, "descriptor submission outside an instance frame");
    const auto existing = descriptor_index.find(key);
    if (existing != descriptor_index.end()) {
        DescriptorObservation &row = frame.descriptors[existing->second];
        fatal_if(row.submit_tick != 0,
                 "descriptor (%u,%u,%u) records a second submission",
                 key.command.command_id, key.command.generation,
                 key.descriptor_id);
    }
    descriptor_index[key] = frame.descriptors.size();
    DescriptorObservation row;
    row.command = key.command;
    row.descriptor_id = key.descriptor_id;
    row.submit_tick = submit_tick;
    row.scheduled = true;
    row.scheduled_completion_tick = scheduled_completion_tick;
    frame.descriptors.push_back(std::move(row));
}

void
RuntimeObservations::recordDescriptorCompletion(
    const DescriptorKey &key, Tick completion_tick, bool committed,
    Tick commit_tick, DmaStatus status, const TrafficContribution &transfer)
{
    fatal_if(!active, "descriptor completion outside an instance frame");
    const auto existing = descriptor_index.find(key);
    fatal_if(existing == descriptor_index.end(),
             "descriptor (%u,%u,%u) completes without a submission",
             key.command.command_id, key.command.generation, key.descriptor_id);
    DescriptorObservation &row = frame.descriptors[existing->second];
    fatal_if(row.completed,
             "descriptor (%u,%u,%u) records a second completion",
             key.command.command_id, key.command.generation, key.descriptor_id);
    row.completed = true;
    row.completion_tick = completion_tick;
    row.committed = committed;
    if (committed)
        row.commit_tick = commit_tick;
    row.has_status = true;
    row.status = status;
    row.has_transfer = true;
    row.transfer = transfer;
}

void
RuntimeObservations::recordFillLanding(
    const DescriptorKey &key, const std::string &landing_digest,
    const DestinationStorageObservation &destination_storage,
    const std::vector<SentinelRangeObservation> &sentinel_ranges)
{
    fatal_if(!active, "fill landing outside an instance frame");
    const auto found = descriptor_index.find(key);
    fatal_if(found == descriptor_index.end(),
             "fill landing for descriptor %u without a submission record",
             key.descriptor_id);
    DescriptorObservation &row = frame.descriptors[found->second];
    row.landing_digest = landing_digest;
    row.has_destination_storage = true;
    row.destination_storage = destination_storage;
    row.sentinel_ranges = sentinel_ranges;
}

void
RuntimeObservations::recordFaultTarget(const DescriptorKey &key,
                                        const std::string &before_digest,
                                        const std::string &after_digest)
{
    fatal_if(!active, "fault target outside an instance frame");
    const auto found = descriptor_index.find(key);
    fatal_if(found == descriptor_index.end(),
             "fault target for descriptor %u without a submission record",
             key.descriptor_id);
    DescriptorObservation &row = frame.descriptors[found->second];
    row.fault_target_before_digest = before_digest;
    row.fault_target_after_digest = after_digest;
}

void
RuntimeObservations::recordTransferProgress(const TransferObservation &transfer)
{
    fatal_if(!active, "transfer progress outside an instance frame");
    const auto found = transfer_index.find(transfer.transfer_id);
    if (found == transfer_index.end()) {
        transfer_index[transfer.transfer_id] = frame.transfers.size();
        frame.transfers.push_back(transfer);
        return;
    }
    TransferObservation &row = frame.transfers[found->second];
    fatal_if(row.sender_published && transfer.sender_published && row.committed_descriptors == transfer.committed_descriptors,
             "transfer %u republishes an unchanged progress record", transfer.transfer_id);
    row = transfer;
}

void
RuntimeObservations::recordTransferCommit(const TransferCommitObservation &commit)
{
    fatal_if(!active, "transfer commit outside an instance frame");
    for (const TransferCommitObservation &row : frame.transfer_commits)
        fatal_if(row.command == commit.command &&
                     row.descriptor_id == commit.descriptor_id,
                 "transfer commit repeats descriptor %u", commit.descriptor_id);
    frame.transfer_commits.push_back(commit);
}

void
RuntimeObservations::recordResidencyBegin(
    const std::vector<uint32_t> &allocations)
{
    fatal_if(!active, "residency begin outside an instance frame");
    frame.residency_begin = allocations;
}

void
RuntimeObservations::recordResidencyEnd(
    const std::vector<uint32_t> &allocations)
{
    fatal_if(!active, "residency end outside an instance frame");
    frame.residency_end = allocations;
}

const CommandObservation *
RuntimeObservations::findCommand(const CommandGeneration &command) const
{
    const auto found = command_index.find(command);
    return found == command_index.end() ? nullptr : &frame.commands[found->second];
}

const EngineExecutionObservation *
RuntimeObservations::findEngineExecution(const CommandGeneration &command) const
{
    const auto found = execution_index.find(command);
    return found == execution_index.end()
               ? nullptr
               : &frame.engine_executions[found->second];
}

const DescriptorObservation *
RuntimeObservations::findDescriptor(const DescriptorKey &key) const
{
    const auto found = descriptor_index.find(key);
    return found == descriptor_index.end() ? nullptr
                                           : &frame.descriptors[found->second];
}

static void
writeDigestWords(std::ostream &out, const std::array<uint32_t, 4> &words)
{
    for (size_t index = 0; index < words.size(); index++) {
        if (index)
            out << ",";
        out << words[index];
    }
}

void
writeSentinelRanges(std::ostream &out,
                    const std::vector<SentinelRangeObservation> &ranges)
{
    out << "[";
    bool first = true;
    for (const SentinelRangeObservation &range : ranges) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"kind\": \"" << range.kind
            << "\", \"address\": " << range.address
            << ", \"size\": " << range.size
            << ", \"available\": " << (range.available ? "true" : "false")
            << ", \"before_digest\": "
            << (range.before_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + range.before_digest + "\"")
            << ", \"after_digest\": "
            << (range.after_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + range.after_digest + "\"")
            << "}";
    }
    out << "]";
}

static void
writeContentRows(std::ostream &out, const std::vector<ContentRowObservation> &rows)
{
    for (size_t index = 0; index < rows.size(); index++) {
        if (index != 0)
            out << ", ";
        out << "{\"address\": " << rows[index].address
            << ", \"size\": " << rows[index].size << ", \"digest\": "
            << (rows[index].digest.empty()
                    ? std::string("null")
                    : std::string("\"") + rows[index].digest + "\"")
            << ", \"bytes_hex\": "
            << (rows[index].bytes_hex.empty()
                    ? std::string("null")
                    : std::string("\"") + rows[index].bytes_hex + "\"")
            << "}";
    }
}

void
writeObservationFrameJson(std::ostream &out, const ObservationFrame &frame)
{
    out << "{\"instance\": " << frame.instance_id
        << ", \"core_id\": " << frame.core_id << ", \"residency\": {"
        << "\"at_begin\": [";
    for (size_t index = 0; index < frame.residency_begin.size(); index++) {
        if (index != 0)
            out << ", ";
        out << frame.residency_begin[index];
    }
    out << "], \"at_end\": [";
    for (size_t index = 0; index < frame.residency_end.size(); index++) {
        if (index != 0)
            out << ", ";
        out << frame.residency_end[index];
    }
    out << "]}, \"commands\": [";
    bool first = true;
    for (const CommandObservation &row : frame.commands) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"issued\": " << (row.issued ? "true" : "false")
            << ", \"issue_tick\": " << (row.issued ? std::to_string(row.issue_tick) : "null")
            << ", \"terminal\": " << (row.terminal ? "true" : "false")
            << ", \"terminal_tick\": "
            << (row.terminal ? std::to_string(row.terminal_tick) : "null") << "}";
    }
    out << "], \"engine_executions\": [";
    first = true;
    for (const EngineExecutionObservation &row : frame.engine_executions) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"engine\": \"" << engineKindName(row.engine)
            << "\", \"begin_tick\": " << row.begin_tick
            << ", \"scheduled_end_tick\": " << row.scheduled_end_tick
            << ", \"ended\": " << (row.ended ? "true" : "false")
            << ", \"end_tick\": " << (row.ended ? std::to_string(row.end_tick) : "null")
            << ", \"cycles\": " << row.cycles
            << "}";
    }
    out << "], \"engine_blocks\": [";
    first = true;
    for (const EngineBlockObservation &row : frame.engine_blocks) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"engine\": \"" << engineKindName(row.engine)
            << "\", \"episode_index\": " << row.episode_index
            << ", \"first_reject_tick\": " << row.first_reject_tick
            << ", \"last_reject_tick\": " << row.last_reject_tick
            << ", \"begin_tick\": " << row.first_reject_tick
            << ", \"end_tick\": " << row.last_reject_tick
            << ", \"occupied\": " << row.occupied << ", \"depth\": " << row.depth
            << ", \"holders\": [";
        bool first_holder = true;
        for (const CommandGeneration &holder : row.holders) {
            if (!first_holder)
                out << ", ";
            first_holder = false;
            out << "{\"command_id\": " << holder.command_id
                << ", \"generation\": " << holder.generation << "}";
        }
        out << "]}";
    }
    out << "], \"compute_outputs\": [";
    first = true;
    for (const ComputeOutputObservation &row : frame.compute_outputs) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"allocation_id\": " << row.allocation_id
            << ", \"offset\": " << row.offset << ", \"digest_words\": [";
        writeDigestWords(out, row.digest_words);
        out << "], \"rows\": [";
        writeContentRows(out, row.rows);
        out << "], \"merged_rows\": [";
        writeContentRows(out, row.merged_rows);
        out << "]}";
    }
    out << "], \"transfers\": [";
    first = true;
    for (const TransferObservation &row : frame.transfers) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"transfer_id\": " << row.transfer_id
            << ", \"expected_descriptors\": " << row.expected_descriptors
            << ", \"committed_descriptors\": " << row.committed_descriptors
            << ", \"expected_bytes\": " << row.expected_bytes
            << ", \"committed_bytes\": " << row.committed_bytes
            << ", \"failed\": " << (row.failed ? "true" : "false")
            << ", \"sender_published\": "
            << (row.sender_published ? "true" : "false")
            << ", \"sender_notifications\": " << row.sender_notifications
            << "}";
    }
    out << "], \"descriptor_executions\": [";
    first = true;
    for (const DescriptorObservation &row : frame.descriptors) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"descriptor_id\": " << row.descriptor_id
            << ", \"submit_tick\": " << row.submit_tick
            << ", \"scheduled\": " << (row.scheduled ? "true" : "false")
            << ", \"scheduled_completion_tick\": "
            << (row.scheduled ? std::to_string(row.scheduled_completion_tick)
                              : "null")
            << ", \"completed\": " << (row.completed ? "true" : "false")
            << ", \"completion_tick\": "
            << (row.completed ? std::to_string(row.completion_tick) : "null")
            << ", \"committed\": " << (row.committed ? "true" : "false")
            << ", \"commit_tick\": "
            << (row.committed ? std::to_string(row.commit_tick) : "null")
            << ", \"status\": "
            << (row.has_status
                    ? std::string("\"") + dmaStatusName(row.status) + "\""
                    : std::string("null"))
            << ", \"fault_target_before_digest\": "
            << (row.fault_target_before_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + row.fault_target_before_digest + "\"")
            << ", \"fault_target_after_digest\": "
            << (row.fault_target_after_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + row.fault_target_after_digest + "\"")
            << ", \"landing_digest\": "
            << (row.landing_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + row.landing_digest + "\"")
            << ", \"destination_storage\": ";
        if (row.has_destination_storage) {
            out << "{\"allocation_id\": "
                << row.destination_storage.allocation_id
                << ", \"base\": " << row.destination_storage.base
                << ", \"bytes\": " << row.destination_storage.bytes << "}";
        } else {
            out << "null";
        }
        out << ", \"source_rows\": [";
        writeContentRows(out, row.source_rows);
        out << "], \"sentinel_ranges\": ";
        writeSentinelRanges(out, row.sentinel_ranges);
        out << ", \"transfer\": ";
        if (!row.has_transfer) {
            out << "null";
        } else {
            out << "{\"read_bytes\": " << row.transfer.read_bytes
                << ", \"write_bytes\": " << row.transfer.write_bytes
                << ", \"p2p_bytes\": " << row.transfer.p2p_bytes
                << ", \"fill_bytes\": " << row.transfer.fill_bytes
                << ", \"read_bursts\": " << row.transfer.read_bursts
                << ", \"write_bursts\": " << row.transfer.write_bursts
                << ", \"p2p_bursts\": " << row.transfer.p2p_bursts
                << ", \"payload_digest\": \"" << row.transfer.payload_digest
                << "\"}";
        }
        out << "}";
    }
    out << "], \"transfer_commits\": [";
    first = true;
    for (const TransferCommitObservation &row : frame.transfer_commits) {
        if (!first)
            out << ", ";
        first = false;
        out << "{\"command_id\": " << row.command.command_id
            << ", \"generation\": " << row.command.generation
            << ", \"descriptor_id\": " << row.descriptor_id
            << ", \"transfer_id\": " << row.transfer_id
            << ", \"commit_tick\": " << row.commit_tick
            << ", \"logical_start\": " << row.logical_start
            << ", \"logical_bytes\": " << row.logical_bytes
            << ", \"source_digest\": \"" << row.source_digest
            << "\", \"target_digest\": \"" << row.target_digest
            << "\", \"target_initial_digest\": \""
            << row.target_initial_digest
            << "\", \"committed_bytes\": " << row.committed_bytes
            << ", \"expected_bytes\": " << row.expected_bytes
            << ", \"sender_notifications\": " << row.sender_notifications
            << ", \"sender_published\": "
            << (row.sender_published ? "true" : "false")
            << ", \"pending_bytes\": " << row.pending_bytes
            << ", \"pending_digest\": "
            << (row.pending_digest.empty()
                    ? std::string("null")
                    : std::string("\"") + row.pending_digest + "\"")
            << ", \"pending_spans\": [";
            {
                bool first_span = true;
                for (const PendingSpanObservation &span : row.pending_spans) {
                    if (!first_span)
                        out << ", ";
                    first_span = false;
                    out << "{\"descriptor_id\": " << span.descriptor_id
                        << ", \"size\": " << span.size
                        << ", \"digest\": \"" << span.digest << "\"}";
                }
            }
            out << "]"
            << ", \"receiver_present\": "
            << (row.receiver_present ? "true" : "false")
            << ", \"receiver_core\": " << row.receiver_core
            << ", \"receiver_transfer_committed\": "
            << (row.receiver_transfer_committed ? "true" : "false")
            << ", \"receiver_allocation_id\": "
            << row.receiver_allocation_id
            << ", \"receiver_admitted_allocations\": [";
        {
            bool first_allocation = true;
            for (uint32_t allocation_id : row.receiver_admitted_allocations) {
                if (!first_allocation)
                    out << ", ";
                first_allocation = false;
                out << allocation_id;
            }
        }
        out << "], \"receiver_notified_allocation_id\": "
            << row.receiver_notified_allocation_id
            << ", \"receiver_allocation_valid\": "
            << (row.receiver_allocation_valid ? "true" : "false")
            << ", \"receiver_notifications\": "
            << row.receiver_notifications
            << ", \"receiver_notification_tick\": "
            << row.receiver_notification_tick << "}";
    }
    out << "]}";
}

} // namespace ai_mesh
} // namespace gem5

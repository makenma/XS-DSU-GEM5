#include "dev/ai_mesh/mesh_dummy_core.hh"

#include <sstream>

#include "dev/ai_mesh/program_scoreboard.hh"

namespace gem5
{
namespace ai_mesh
{

std::string MeshDummyCore::waitForGraph() const
{
    // Every edge is derived from the admitted facts (single event producers,
    // barrier participant sets, descriptor groups, allocation pins) plus the
    // live engine state, so the graph names what is actually pending and who
    // owes it (spec 8.8/12.3.12).
    std::ostringstream out;
    out << "core " << core_id_value << " live=" << live_commands
        << " outstanding_axi=" << outstanding_axi
        << " halted=" << (core_halted ? 1 : 0)
        << " error_drained=" << (error_drained ? 1 : 0);
    if (observations.frameActive()) {
        const ObservationFrame &frame = observations.view();
        size_t submitted = 0, completed = 0, committed = 0;
        for (const DescriptorObservation &row : frame.descriptors) {
            submitted++;
            completed += row.completed ? 1 : 0;
            committed += row.committed ? 1 : 0;
        }
        out << "\n  observation_frame instance=" << frame.instance_id
            << " core=" << frame.core_id << " commands=" << frame.commands.size()
            << " engine_executions=" << frame.engine_executions.size()
            << " descriptors_submitted=" << submitted
            << " descriptors_completed=" << completed
            << " descriptors_committed=" << committed;
        out << "\n  observation_frame_json ";
        writeObservationFrameJson(out, frame);
    }

    std::set<std::pair<uint32_t, uint32_t>> terminal_keys;
    for (const TerminalRecord &record : instance_terminals)
        terminal_keys.insert({record.command_id, record.generation});
    const auto commandById =
        [&](uint32_t command_id) -> const DecodedCommand * {
        for (const auto &command : program->transport.commands)
            if (command.command_id == command_id)
                return &command;
        return nullptr;
    };
    // transfer -> commit state and the admitted descriptors that carry it.
    const auto recvEdge = [&](uint32_t command_id, uint32_t transfer_id) {
        out << "\n  recv cmd=" << command_id << " transfer=" << transfer_id
            << " committed="
            << (committed_transfers.count(transfer_id) &&
                        committed_transfers.at(transfer_id)
                    ? 1
                    : 0)
            << " producers=";
        bool first_producer = true;
        for (const auto &descriptor : program->transport.dma_descriptors) {
            if (descriptor.transfer_id != transfer_id)
                continue;
            if (!first_producer)
                out << ",";
            first_producer = false;
            out << "cmd" << descriptor.command_id << ".descriptor"
                << descriptor.descriptor_id;
        }
        if (first_producer)
            out << "none";
    };
    const auto producer = [&](uint32_t event_id) {
        std::string names;
        bool first_name = true;
        for (const auto &command : program->transport.commands) {
            if (command.signal_event != event_id)
                continue;
            if (!first_name)
                names += ",";
            first_name = false;
            names += "cmd" + std::to_string(command.command_id);
        }
        if (!first_name)
            return names;
        for (const auto &descriptor : program->transport.dma_descriptors)
            if (descriptor.completion_event == event_id)
                return "cmd" + std::to_string(descriptor.command_id) +
                       ".descriptor" + std::to_string(descriptor.descriptor_id);
        return std::string("none");
    };

    // wait-event-producer: the decode stage is stuck on the head of each
    // stream.  An issued command has already consumed its waits (its
    // completion is driven by the engine, the scoreboard or the drain gate),
    // so only the not-yet-issued head is a real dependency edge.
    for (const auto &kv : cursors) {
        const StreamCursor &cursor = kv.second;
        if (cursor.next_command >= cursor.end_command)
            continue;
        const uint32_t index = cursor.next_command;
        const DecodedCommand &command = program->transport.commands[index];
        const uint32_t generation = generationAt(kv.first, index);
        if (admitWindowBlocked(cursor, command, generation)) {
            const uint32_t occupied =
                liveStreamCommands(command.stream_id) -
                (cursor.repeat_gate_command != 0 ? 1 : 0);
            out << "\n  window-blocked cmd=" << command.command_id
                << " generation=" << generation
                << " stream=" << command.stream_id << " occupied=" << occupied
                << "/" << admit_window_value;
        }
        for (uint16_t w = 0; w < command.wait_count; w++) {
            const uint32_t event_id =
                program->transport.command_waits[command.wait_begin + w]
                    .event_id;
            if (scoreboard->visible(event_id))
                continue;
            out << "\n  wait-event cmd=" << command.command_id
                << " generation=" << generation << " event=" << event_id
                << " producer=" << producer(event_id);
        }
        if (command.opcode == mesh_abi::kOpcodeRECV_WAIT) {
            const DecodedAttr *attr = attrOf(command);
            if (attr != nullptr && attr->kind == mesh_abi::kAttrKindRECV_WAIT_V1)
                recvEdge(command.command_id,
                         attr->as<mesh_abi::RecvWaitV1>()->transfer_id);
        }
    }

    // barrier missing participant: an open rendezvous and the absent arrivals.
    for (const ProgramScoreboard::BarrierSnapshot &barrier :
         scoreboard->openBarriers()) {
        out << "\n  barrier event=" << barrier.event_id
            << " generation=" << barrier.generation
            << " arrived=" << barrier.arrivals.size() << "[";
        bool first_arrived = true;
        for (const auto &command : program->transport.commands) {
            if (command.signal_event != barrier.event_id ||
                !barrier.arrivals.count(command.command_id))
                continue;
            if (!first_arrived)
                out << ",";
            first_arrived = false;
            out << "cmd" << command.command_id << "(core" << command.core_id
                << ")";
        }
        if (first_arrived)
            out << "none";
        out << "] expected=" << barrier.expected << " missing=";
        bool first_missing = true;
        for (const auto &command : program->transport.commands) {
            if (command.signal_event != barrier.event_id ||
                barrier.arrivals.count(command.command_id))
                continue;
            if (!first_missing)
                out << ",";
            first_missing = false;
            out << "cmd" << command.command_id << "(core"
                << command.core_id << ")";
            for (uint16_t w = 0; w < command.wait_count; w++) {
                const uint32_t waited =
                    program->transport.command_waits[command.wait_begin + w]
                        .event_id;
                out << " waits[e" << waited;
                if (scoreboard->visible(waited))
                    out << "=visible";
                else
                    out << "=pending";
                for (const auto &producer : program->transport.commands)
                    if (producer.signal_event == waited)
                        out << "<-cmd" << producer.command_id << "(core"
                            << producer.core_id << ")";
                for (const auto &state : dma_lifecycle.states())
                    if (!state.second.descriptors.empty() &&
                        !state.second.terminal)
                        for (const auto &candidate :
                             program->transport.commands)
                            if (candidate.command_id == state.first &&
                                candidate.signal_event == waited)
                                out << "<-dma_cmd" << state.first
                                    << " submitted="
                                    << state.second.next_descriptor << "/"
                                    << state.second.descriptors.size();
                out << "]";
            }
        }
        if (first_missing)
            out << "none";
    }

    // transfer edge: a staged peer commit that has not published yet.
    for (const auto &entry : dma_lifecycle.states()) {
        const DmaCommandState &state = entry.second;
        if (state.transfer_id == 0 || state.published || state.failed ||
            state.cancelled)
            continue;
        out << "\n  transfer=" << state.transfer_id
            << " expected_descriptors=" << state.descriptors.size()
            << " committed_descriptors=" << state.committed.size()
            << " expected_bytes=" << state.expected_bytes
            << " committed_bytes=" << state.committed_bytes
            << " pending=[";
        bool first_pending = true;
        for (uint32_t descriptor_id : state.descriptors) {
            if (state.committed.count(descriptor_id))
                continue;
            if (!first_pending)
                out << ",";
            first_pending = false;
            out << descriptor_id;
        }
        if (first_pending)
            out << "none";
        out << "]";
    }

    // descriptor edge: the issued/pending split of every live descriptor group.
    for (const auto &entry : dma_lifecycle.states()) {
        const DmaCommandState &state = entry.second;
        if (state.terminal)
            continue;
        out << "\n  dma cmd=" << entry.first
            << " generation=" << state.generation
            << " submitted=" << state.next_descriptor << "/"
            << state.descriptors.size() << " pending=";
        bool first_pending = true;
        for (uint32_t descriptor_id : state.pending) {
            if (!first_pending)
                out << ",";
            first_pending = false;
            out << descriptor_id;
        }
        if (first_pending)
            out << "none";
        out << " failed=" << (state.failed ? 1 : 0)
            << " cancelled=" << (state.cancelled ? 1 : 0);
    }

    // resource edge: a decoded DMA command held back by the allocation pins of
    // an admitted command (the admission-time pin, spec 7.1).
    for (const auto &kv : cursors) {
        const StreamCursor &cursor = kv.second;
        for (uint32_t index = cursor.next_command;
             index < cursor.end_command; index++) {
            const DecodedCommand &command = program->transport.commands[index];
            if (dma_lifecycle.admitted(command.command_id))
                continue;
            if (command.opcode != mesh_abi::kOpcodeDMA_LOAD &&
                command.opcode != mesh_abi::kOpcodeDMA_STORE &&
                command.opcode != mesh_abi::kOpcodeDMA_P2P_PUSH &&
                command.opcode != mesh_abi::kOpcodeDMA_PREFETCH &&
                command.opcode != mesh_abi::kOpcodeDMA_FILL)
                continue;
            for (uint16_t i = 0; i < command.operand_count; i++) {
                const DecodedOperand &operand =
                    program->transport.command_operands[command.operand_begin +
                                                        i];
                auto pin = allocation_pins.find(operand.allocation_id);
                if (pin == allocation_pins.end() || pin->second == 0)
                    continue;
                out << "\n  pin-blocked cmd=" << command.command_id
                    << " generation=" << generationAt(kv.first, index)
                    << " allocation=" << operand.allocation_id
                    << " pins=" << pin->second << " held_by=";
                bool first_holder = true;
                for (const auto &held : dma_lifecycle.states()) {
                    if (held.second.terminal)
                        continue;
                    const DecodedCommand *holder = commandById(held.first);
                    if (holder == nullptr)
                        continue;
                    for (uint16_t h = 0; h < holder->operand_count; h++)
                        if (program->transport
                                .command_operands[holder->operand_begin + h]
                                .allocation_id == operand.allocation_id) {
                            if (!first_holder)
                                out << ",";
                            first_holder = false;
                            out << "cmd" << held.first;
                            break;
                        }
                }
                if (first_holder)
                    out << "none";
                break;
            }
        }
    }

    // tag edge: a fence whose captured tags have not retired.
    for (const auto &entry : fence_waiters) {
        out << "\n  fence cmd=" << entry.first << " tags=";
        bool first_tag = true;
        for (uint64_t tag : entry.second) {
            if (!live_dma_tags.count(tag))
                continue;
            if (!first_tag)
                out << ",";
            first_tag = false;
            auto owner = dma_tag_command.find(tag);
            out << tag << "(cmd"
                << (owner == dma_tag_command.end() ? 0 : owner->second) << ")";
        }
        if (first_tag)
            out << "none";
    }

    // transfer edge: a RECV_WAIT registered as waiting on peer bytes.
    for (const auto &entry : recv_waiters)
        recvEdge(entry.second, entry.first);
    return out.str();
}

} // namespace ai_mesh
} // namespace gem5

#include "dev/ai_mesh/mesh_experiment_observer.hh"

#include <array>
#include <charconv>
#include <fstream>
#include <limits>

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "base/logging.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "params/MeshExperimentObserver.hh"

namespace gem5::ai_mesh
{

namespace py = pybind11;
using namespace pybind11::literals;

namespace
{

void
writeJson(const std::string &path, const py::dict &document)
{
    const auto serialized = py::module_::import("json").attr("dumps")(
        document, "sort_keys"_a=true, "allow_nan"_a=false, "indent"_a=2);
    std::ofstream output(path);
    fatal_if(!output, "cannot open experiment artifact %s", path);
    output << serialized.cast<std::string>() << '\n';
    output.flush();
    fatal_if(!output, "cannot write experiment artifact %s", path);
}

}

MeshExperimentObserver::MeshExperimentObserver(const Params &p)
    : SimObject(p), cores(p.cores), targets(p.targets),
      network(dynamic_cast<ruby::garnet::GarnetNetwork *>(p.network))
{
    fatal_if(!network || cores.empty() || targets.empty(),
             "%s: experiment observer requires cores, targets, and Garnet",
             name());
    for (const auto *core : cores) {
        fatal_if(!core, "%s: null observed core", name());
        auto *engine = dynamic_cast<AxiTensorDmaEngine *>(core->dmaEngine());
        fatal_if(!engine || !engine->bridgeOf(),
                 "%s: observer requires real AXI DMA and bridge", name());
        engines.push_back(engine);
    }
    for (const auto *target : targets)
        fatal_if(!target, "%s: null observed target", name());
    for (bool local : {true, false}) {
        const auto &checks = local ? p.core_memory_checks : p.target_memory_checks;
        for (const auto &text : checks) {
            std::array<uint64_t, 4> fields{};
            size_t begin = 0;
            for (size_t field = 0; field < fields.size(); ++field) {
                const size_t separator = text.find(':', begin);
                const size_t end = separator == std::string::npos ?
                    text.size() : separator;
                fatal_if((field + 1 == fields.size()) !=
                         (separator == std::string::npos) || begin == end,
                         "%s: invalid memory check %s", name(), text);
                int radix = 10;
                if (end - begin > 2 && text.compare(begin, 2, "0x") == 0) {
                    begin += 2;
                    radix = 16;
                }
                const auto parsed = std::from_chars(text.data() + begin,
                    text.data() + end, fields[field], radix);
                fatal_if(parsed.ec != std::errc{} ||
                         parsed.ptr != text.data() + end,
                         "%s: invalid memory check field %s", name(), text);
                begin = end + 1;
            }
            fatal_if(fields[0] >= (local ? cores.size() : targets.size()) ||
                     fields[2] > std::numeric_limits<uint64_t>::max() - fields[1] ||
                     fields[3] > 255,
                     "%s: memory check is outside declared bounds: %s",
                     name(), text);
            memoryChecks.push_back({local, static_cast<size_t>(fields[0]),
                fields[1], fields[2], static_cast<uint8_t>(fields[3])});
        }
    }
}

bool
MeshExperimentObserver::drained() const
{
    for (size_t index = 0; index < cores.size(); ++index) {
        if (!cores[index]->halted() || !cores[index]->quiescent() ||
            !engines[index]->idle() || !engines[index]->bridgeOf()->idle())
            return false;
    }
    for (const auto *target : targets) {
        if (!target->functionalIdle())
            return false;
        const auto occupancy = target->functionalOccupancy();
        if (occupancy.readServices || occupancy.writeServices)
            return false;
        if (target->syntheticBackendEnabled()) {
            const auto backend = target->syntheticBackendStats();
            if (backend.queued || backend.ready)
                return false;
        }
    }
    if (!network->isQuiescent())
        return false;
    for (const auto &entry : network->creditLedger()) {
        if (!entry.restored())
            return false;
    }
    return true;
}

void
MeshExperimentObserver::dumpSnapshot(const std::string &path) const
{
    py::gil_scoped_acquire gil;
    const auto snapshot = network->experimentSnapshot();
    py::dict document("schema"_a="ai_mesh_experiment_snapshot_v1",
                      "tick"_a=snapshot.tick,
                      "network_cycle"_a=snapshot.networkCycle,
                      "drained"_a=drained());
    document["network_counters"] = py::dict(
        "packets_injected"_a=snapshot.packetsInjected,
        "packets_received"_a=snapshot.packetsReceived,
        "flits_injected"_a=snapshot.flitsInjected,
        "flits_received"_a=snapshot.flitsReceived,
        "wire_bytes_injected"_a=snapshot.wireBytesInjected,
        "wire_bytes_received"_a=snapshot.wireBytesReceived);
    py::list input_vcs;
    for (const auto &entry : snapshot.inputVcs) {
        input_vcs.append(py::dict(
            "router_id"_a=entry.routerId, "inport_id"_a=entry.inportId,
            "ingress_port"_a=entry.ingressPort, "lane"_a=entry.lane,
            "direction"_a=entry.direction, "vnet"_a=entry.vnet,
            "vc"_a=entry.vc, "depth"_a=entry.depth,
            "occupancy"_a=entry.occupancy, "high_water"_a=entry.highWater,
            "enqueued"_a=entry.enqueued, "dequeued"_a=entry.dequeued,
            "credit_stalls"_a=entry.creditStalls,
            "no_vc_stalls"_a=entry.noVcStalls, "sa_lost"_a=entry.saLost,
            "time_histogram"_a=entry.timeHistogram));
    }
    document["input_vcs"] = input_vcs;
    py::list links;
    for (const auto &entry : snapshot.links) {
        links.append(py::dict("link_id"_a=entry.linkId,
            "width_bytes"_a=entry.widthBytes, "flits"_a=entry.flits,
            "vc_flits"_a=entry.vcFlits));
    }
    document["links"] = links;
    py::list capacity;
    for (const auto &entry : network->receiverCapacityMap()) {
        capacity.append(py::dict(
            "sender_kind"_a=entry.senderKind, "sender_id"_a=entry.senderId,
            "sender_port"_a=entry.senderPort,
            "sender_direction"_a=entry.senderDirection,
            "receiver_kind"_a=entry.receiverKind,
            "receiver_id"_a=entry.receiverId,
            "receiver_port"_a=entry.receiverPort,
            "receiver_direction"_a=entry.receiverDirection,
            "link_id"_a=entry.linkId, "vnet"_a=entry.vnet, "vc"_a=entry.vc,
            "depth"_a=entry.depth, "initial_credit"_a=entry.initialCredit));
    }
    document["receiver_capacity_map"] = capacity;
    py::list ledger;
    for (const auto &entry : network->creditLedger()) {
        ledger.append(py::dict("owner_kind"_a=entry.ownerKind,
            "owner_id"_a=entry.ownerId, "port_id"_a=entry.portId,
            "link_id"_a=entry.linkId, "vc"_a=entry.vc, "vnet"_a=entry.vnet,
            "initial"_a=entry.initial, "sent"_a=entry.sent,
            "returned"_a=entry.returned, "current"_a=entry.current,
            "depth"_a=entry.depth));
    }
    document["credit_ledger"] = ledger;
    const auto quiet = network->quiescenceSnapshot();
    document["quiescence"] = py::dict(
        "ni_queued_flits"_a=quiet.niQueuedFlits,
        "ni_queued_messages"_a=quiet.niQueuedMessages,
        "router_buffered_flits"_a=quiet.routerBufferedFlits,
        "non_idle_input_vcs"_a=quiet.nonIdleInputVcs,
        "non_idle_output_vcs"_a=quiet.nonIdleOutputVcs,
        "data_link_pending_flits"_a=quiet.dataLinkPendingFlits,
        "credit_link_pending_credits"_a=quiet.creditLinkPendingCredits,
        "bridge_pending_items"_a=quiet.bridgePendingItems,
        "credit_deficit"_a=quiet.creditDeficit);
    py::list core_rows;
    for (size_t index = 0; index < cores.size(); ++index) {
        const auto *core = cores[index];
        const auto *engine = engines[index];
        const auto *bridge = engine->bridgeOf();
        const auto &counters = bridge->counters();
        py::dict row("index"_a=index, "core_id"_a=core->archCoreId(),
            "name"_a=core->name(), "halted"_a=core->halted(),
            "quiescent"_a=core->quiescent(), "dma_idle"_a=engine->idle(),
            "bridge_idle"_a=bridge->idle(), "errored"_a=core->instanceErrored(),
            "core_clock_period_ticks"_a=core->clockPeriod(),
            "live_descriptors"_a=engine->liveDescriptors(),
            "pending_reads"_a=bridge->pendingReads(),
            "pending_writes"_a=bridge->pendingWrites(),
            "pending_read_reservations"_a=engine->pendingReadReservations(),
            "scheduled_read_commits"_a=engine->scheduledReadCommits(),
            "axi_read_outstanding"_a=bridge->outstandingReads(),
            "axi_write_outstanding"_a=bridge->outstandingWrites(),
            "peak_axi_read_outstanding"_a=bridge->peakOutstandingReads(),
            "read_committed_bytes"_a=engine->validReadBytes(),
            "write_completed_bytes"_a=engine->validWriteBytes(),
            "submitted_read_bursts"_a=engine->submittedReadBursts(),
            "submitted_write_bursts"_a=engine->submittedWriteBursts(),
            "completed_read_bursts"_a=engine->completedReadBursts(),
            "read_rlast_consumed"_a=engine->readRlastConsumed(),
            "completed_write_bursts"_a=engine->completedWriteBursts(),
            "error_read_bursts"_a=engine->errorReadBursts(),
            "error_write_bursts"_a=engine->errorWriteBursts(),
            "ar_accepted"_a=counters.arAccepted,
            "aw_accepted"_a=counters.awAccepted,
            "w_accepted"_a=counters.wAccepted,
            "peak_w_accepted_per_cycle"_a=counters.peakWAcceptedPerCycle,
            "b_consumed"_a=counters.bConsumed,
            "r_beats_consumed"_a=counters.rBeatsConsumed,
            "b_errors"_a=counters.bErrorCount,
            "r_error_beats"_a=counters.rErrorBeats,
            "command_issue_ticks"_a=core->commandIssueTicks(),
            "command_done_ticks"_a=core->commandDoneTicks(),
            "sram_bank_conflicts"_a=core->sramBankConflicts.value(),
            "sram_service_cycles"_a=core->sramServiceCycles.value());
        const auto progress = bridge->initiatorProgress().core;
        const auto resources = bridge->initiatorResourceOccupancy();
        row["axi_initiator_progress"] = py::dict(
            "b_packets_buffered"_a=progress.bPacketsBuffered,
            "r_packets_buffered"_a=progress.rPacketsBuffered,
            "same_id_responses_blocked"_a=progress.sameIdResponsesBlocked,
            "b_transactions_retired"_a=progress.bTransactionsRetired,
            "r_transactions_retired"_a=progress.rTransactionsRetired,
            "write_quota_stalls"_a=progress.writeQuotaStalls,
            "read_quota_stalls"_a=progress.readQuotaStalls,
            "ar_rejection_attempts"_a=py::dict(
                "fifo_full"_a=progress.arRejectionAttempts.fifoFull,
                "outstanding_full"_a=
                    progress.arRejectionAttempts.outstandingFull,
                "response_reservation_full"_a=
                    progress.arRejectionAttempts.responseReservationFull),
            "aw_rejection_attempts"_a=py::dict(
                "fifo_full"_a=progress.awRejectionAttempts.fifoFull,
                "outstanding_full"_a=
                    progress.awRejectionAttempts.outstandingFull,
                "response_reservation_full"_a=
                    progress.awRejectionAttempts.responseReservationFull));
        row["axi_initiator_occupancy"] = py::dict(
            "read_waiting_quota"_a=resources.readWaitingQuota,
            "read_granted_waiting_forward"_a=
                resources.readGrantedWaitingForward,
            "read_forwarded_to_message_buffer"_a=
                resources.readForwardedToMessageBuffer,
            "write_waiting_quota"_a=resources.writeWaitingQuota,
            "write_granted_waiting_forward"_a=
                resources.writeGrantedWaitingForward,
            "write_forwarded_to_message_buffer"_a=
                resources.writeForwardedToMessageBuffer,
            "r_rob_reserved_beats"_a=resources.rRobReservedBeats,
            "r_rob_buffered_beats"_a=resources.rRobBufferedBeats,
            "b_rob_reserved_transactions"_a=resources.bRobReservedTransactions,
            "b_rob_buffered_transactions"_a=resources.bRobBufferedTransactions);
        row["sram_reservation_rejection_attempts"] = py::dict(
            "read"_a=core->sramReservationRejectionAttempts(false),
            "write"_a=core->sramReservationRejectionAttempts(true));
        py::list bursts;
        for (const auto &[ordinal, timing] : bridge->burstTimings()) {
            py::dict burst("ordinal"_a=ordinal, "read"_a=timing.read,
                "axi_id"_a=timing.axi_id, "address"_a=timing.address,
                "beats"_a=timing.beats, "beat_bytes"_a=timing.beat_bytes,
                "addr_accept_tick"_a=timing.addr_accept,
                "first_w_tick"_a=timing.first_w,
                "response_tick"_a=timing.resp_last);
            const auto commit = engine->readCommitTicks().find(ordinal);
            if (commit != engine->readCommitTicks().end())
                burst["local_commit_tick"] = commit->second;
            bursts.append(burst);
        }
        row["burst_timings"] = bursts;
        py::list descriptors;
        for (const auto &[id, timing] : engine->descriptorTimings()) {
            descriptors.append(py::dict("descriptor_id"_a=id,
                "first_ar_tick"_a=timing.first_ar_tick,
                "first_aw_tick"_a=timing.first_aw_tick,
                "first_w_tick"_a=timing.first_w_tick,
                "first_b_tick"_a=timing.first_b_tick,
                "last_r_tick"_a=timing.last_r_tick,
                "local_commit_tick"_a=timing.local_commit_tick,
                "done_tick"_a=timing.done_tick));
        }
        row["descriptor_timings"] = descriptors;
        const auto queue = bridge->queueHighWater();
        row["queue_high_water"] = py::dict("local_fifo"_a=queue.localFifo,
            "message_buffer"_a=queue.messageBuffer,
            "adapter_ingress"_a=queue.adapterIngress,
            "message_buffer_stall_cycles"_a=queue.messageBufferStallCycles);
        core_rows.append(row);
    }
    document["cores"] = core_rows;
    py::list target_rows;
    for (size_t index = 0; index < targets.size(); ++index) {
        const auto *target = targets[index];
        const auto occupancy = target->functionalOccupancy();
        const auto progress = target->functionalProgress();
        py::dict row("index"_a=index, "name"_a=target->name(),
            "idle"_a=target->functionalIdle(),
            "clock_period_ticks"_a=target->clockPeriod(),
            "write_contexts"_a=occupancy.writeContexts,
            "write_reserved_beats"_a=occupancy.writeReservedBeats,
            "orphan_transactions"_a=occupancy.orphanTransactions,
            "orphan_reserved_beats"_a=occupancy.orphanReservedBeats,
            "read_contexts"_a=occupancy.readContexts,
            "read_reserved_beats"_a=occupancy.readReservedBeats,
            "b_ready"_a=occupancy.bReady, "r_ready"_a=occupancy.rReady,
            "write_services"_a=occupancy.writeServices,
            "read_services"_a=occupancy.readServices,
            "writes_committed"_a=progress.writesCommitted,
            "reads_committed"_a=progress.readsCommitted,
            "write_committed_bytes"_a=progress.writeCommittedBytes,
            "read_committed_bytes"_a=progress.readCommittedBytes,
            "last_write_commit_cycle"_a=progress.lastWriteCommitCycle,
            "service_ready"_a=progress.serviceReady,
            "architectural_commits"_a=progress.architecturalCommits,
            "same_id_ready_blocked"_a=progress.sameIdReadyBlocked,
            "orphan_or_quota_stall_cycles"_a=progress.orphanOrQuotaStallCycles);
        if (target->syntheticBackendEnabled()) {
            const auto stats = target->syntheticBackendStats();
            row["synthetic_backend"] = py::dict(
                "submitted"_a=stats.submitted, "completed"_a=stats.completed,
                "retired"_a=stats.retired,
                "read_bytes_serviced"_a=stats.readBytesServiced,
                "write_bytes_serviced"_a=stats.writeBytesServiced,
                "busy_cycles"_a=stats.busyCycles,
                "queue_full_cycles"_a=stats.queueFullCycles,
                "request_slot_cycles"_a=stats.requestSlotCycles,
                "queued"_a=stats.queued, "ready"_a=stats.ready,
                "high_water"_a=stats.highWater);
        } else {
            row["synthetic_backend"] = py::none();
        }
        const auto queue = target->functionalQueueHighWater();
        row["queue_high_water"] = py::dict("local_fifo"_a=queue.localFifo,
            "message_buffer"_a=queue.messageBuffer,
            "adapter_ingress"_a=queue.adapterIngress,
            "message_buffer_stall_cycles"_a=queue.messageBufferStallCycles);
        target_rows.append(row);
    }
    document["targets"] = target_rows;
    writeJson(path, document);
}

void
MeshExperimentObserver::dumpMemoryChecks(const std::string &path) const
{
    fatal_if(!drained(), "%s: memory checks require full drain", name());
    py::gil_scoped_acquire gil;
    uint64_t requested = 0;
    uint64_t checked = 0;
    uint64_t mismatches = 0;
    uint64_t unreadable = 0;
    py::object first = py::none();
    for (const auto &check : memoryChecks) {
        fatal_if(check.size > std::numeric_limits<uint64_t>::max() - requested,
                 "%s: memory check byte total overflows", name());
        requested += check.size;
        for (uint64_t offset = 0; offset < check.size; ++offset) {
            const uint64_t address = check.address + offset;
            uint8_t byte = 0;
            bool readable;
            if (check.core) {
                readable = cores[check.index]->functionalSramRead(address, 1, &byte);
            } else {
                readable = targets[check.index]->containsMemoryAddress(address);
                if (readable)
                    byte = targets[check.index]->readMemoryByte(address);
            }
            checked += readable;
            unreadable += !readable;
            if (!readable || byte != check.expected) {
                ++mismatches;
                if (first.is_none()) {
                    py::dict detail("kind"_a=(check.core ? "core" : "target"),
                        "index"_a=check.index, "address"_a=address,
                        "expected"_a=check.expected, "readable"_a=readable);
                    detail["actual"] = readable ? py::cast(byte) : py::none();
                    first = detail;
                }
            }
        }
    }
    writeJson(path, py::dict("schema"_a="ai_mesh_experiment_memory_checks_v1",
        "drained"_a=true, "requested_bytes"_a=requested,
        "checked_bytes"_a=checked, "mismatch_count"_a=mismatches,
        "unreadable_bytes"_a=unreadable, "first_mismatch"_a=first));
}

}

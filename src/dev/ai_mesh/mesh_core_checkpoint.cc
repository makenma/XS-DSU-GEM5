#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void MeshDummyCore::serialize(Archive &archive, unsigned)
{
    fatal_if(!quiescent() || !dma->idle() || !pending_cache_fills.empty() ||
             !queued_cache_fills.empty() || runtime_fill_inputs,
             "E_CHECKPOINT_BOUNDARY: core is not drained");
    checkpoint::fields(archive, sram, region_gate, overlay_exited,
        overlay_entry_published, cache_fills_issued, overlay_entry_tick,
        cache_fills_completed, cache_fills_errored, cache_fills_retried,
        cache_batch_id, cursors, signaled, barrier_arrivals, recv_waiters,
        committed_transfers, live_per_stream, fence_waiters, live_dma_tags,
        dma_tag_info, dma_tag_command, dma_lifecycle, dma_descriptor_tag, next_dma_tag,
        instance_generation, instance_state, error_latch_tick, work_drained_tick,
        allocation_pins, computeDigests, completed_command_ids,
        instance_suppressed_ids, errored_command_ids, cancelled_command_ids,
        observations, instance_terminals, command_generations, plan_instances_cancelled,
        engine_slots, receive_notifications, receive_notification_ticks, received_allocations,
        commandsIssued, commandsCompleted, commandsErrored, commandsCancelled,
        eventsPublished, haltCommands, requestBegins, requestEnds, gemmCycles,
        reduceCommands, reduceCycles, sramBankConflicts, sramServiceCycles,
        poisonReadFaults);
    bool cached = bool(weight_cache);
    archive & cached;
    fatal_if(cached != bool(weight_cache), "E_CHECKPOINT_CONFIG: cache policy differs");
    if (cached)
        archive & *weight_cache;
    size_t overlays = overlay_executors.size();
    archive & overlays;
    if constexpr (Archive::is_loading::value) {
        overlay_executors.clear();
        for (size_t index = 0; index < overlays; ++index) {
            uint32_t layer;
            archive & layer;
            auto executor = std::make_unique<MoeOverlayExecutor>(
                MoeOverlayExecutor::Config{}, scoreboard, &sram, overlay_bus);
            archive & *executor;
            fatal_if(layer != executor->layerId() || overlay_executors.count(layer),
                     "E_CHECKPOINT_STATE: duplicate or inconsistent overlay");
            overlay_executors.emplace(layer, std::move(executor));
        }
    } else {
        for (auto &entry : overlay_executors)
            checkpoint::fields(archive, entry.first, *entry.second);
    }
    checkpoint::event(archive, tick_event, *this);
}

void MeshDummyCore::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void MeshDummyCore::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void MeshDummyCore::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

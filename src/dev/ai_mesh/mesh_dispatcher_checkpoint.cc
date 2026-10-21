#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void MeshDispatcher::serialize(Archive &archive, unsigned)
{
    fatal_if(!replayQuiescent() || !pending_fences.empty() ||
             !participant_cores.empty() || selected_view || failure_pending ||
             edge_commit_active || cache_reservation_waiting,
             "E_CHECKPOINT_BOUNDARY: dispatcher is not drained");
    checkpoint::fields(archive, allocated_instance, candidate_events,
        candidate_waits, candidate_attaches, candidate_resumes, program_terminal,
        instance_records, instance_error_latched, drained_error_core,
        output_digest_before, semantic_digest, semantic_digest_set,
        scoreboard, instance_counter, overlay_exits_seen, batch_state,
        halted_cores, last_progress_snapshot, next_cache_request_id,
        cache_reservation_attempts, cache_identity_reassignments,
        cache_reservation_commits, prestart_failures, batch_starts,
        watchdog_fired_count, instancesDispatched, instancesCompleted,
        failure_reducer, live_destinations, sampled_destinations,
        drain_active, drain_begin_tick, drain_begin_pending, drain_end_tick,
        drain_end_pending, drained_instances, dma_tags_retired);
    checkpoint::event(archive, drain_event, *this);
    checkpoint::event(archive, failure_event, *this);
    checkpoint::event(archive, start_event, *this);
    checkpoint::event(archive, watchdog_event, *this);
    checkpoint::event(archive, cache_retry_event, *this);
    checkpoint::event(archive, settle_retry_event, *this);
}

void MeshDispatcher::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void MeshDispatcher::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void MeshDispatcher::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}

}

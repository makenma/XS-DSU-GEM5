#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void AxiTensorDmaEngine::serialize(Archive &archive, unsigned)
{
    fatal_if(!idle(), "E_CHECKPOINT_BOUNDARY: DMA is not drained");
    checkpoint::fields(archive, read_commit_ticks, payload_state, actual, timings,
        timing_log, instance_bursts, attribution_by_ordinal, instance_counter,
        fill_patterns, fill_contents, free_read_ids, free_write_ids,
        read_bursts_submitted, write_bursts_submitted, read_bursts_completed,
        write_bursts_completed, read_bursts_errored, write_bursts_errored,
        read_valid_bytes, write_valid_bytes, read_ar_accepted, read_rlast_consumed,
        peak_read_window, first_rlast_tick, ar_accept_ticks, protocol_dropped_beats);
    checkpoint::event(archive, tick_event, *this);
}

void AxiTensorDmaEngine::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void AxiTensorDmaEngine::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void AxiTensorDmaEngine::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}

template<class Archive>
void AxiGarnetBridge::serialize(Archive &archive, unsigned)
{
    fatal_if(!idle(), "E_CHECKPOINT_BOUNDARY: bridge is not drained");
    checkpoint::fields(archive, ctr, next_ordinal, peak_live_writes, w_accept_cycle,
        w_accepted_this_cycle, ordinal_handshake, read_beats_of_ordinal);
    checkpoint::event(archive, tick_event, *this);
}

void AxiGarnetBridge::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void AxiGarnetBridge::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void AxiGarnetBridge::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

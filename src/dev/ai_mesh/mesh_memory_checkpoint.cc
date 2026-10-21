#include "dev/ai_mesh/peer_sram_aperture.hh"
#include "dev/ai_mesh/npu_memory_endpoint.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void PeerSramAperture::serialize(Archive &archive, unsigned)
{
    fatal_if(!idle() || replay_event.scheduled(),
             "E_CHECKPOINT_BOUNDARY: peer SRAM is not drained");
    checkpoint::fields(archive, coverage, sentinel_spans, sentinels,
        captured_commit, replay_scheduled, committed_valid_bytes,
        error_drained_bytes, last_commit_tick);
}

void PeerSramAperture::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void PeerSramAperture::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void PeerSramAperture::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}

template<class Archive>
void NpuMemoryEndpoint::serialize(Archive &archive, unsigned)
{
    fatal_if(!idle(), "E_CHECKPOINT_BOUNDARY: memory endpoint is active");
    checkpoint::fields(archive, seeds, verifies, sentinel_spans, sentinels,
        committed_valid_bytes, error_drained_bytes);
}

void NpuMemoryEndpoint::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void NpuMemoryEndpoint::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void NpuMemoryEndpoint::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

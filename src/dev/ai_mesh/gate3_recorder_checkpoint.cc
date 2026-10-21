#include "dev/ai_mesh/gate3_observation_recorder.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void Gate3ObservationRecorder::serialize(Archive &archive, unsigned)
{
    fatal_if(fatalPending() || fatalRecorded(),
             "E_CHECKPOINT_BOUNDARY: recorder has a fatal event");
    checkpoint::fields(archive, events, metrics, _fatalSqIntakes,
        _fatalCqObligations, _hostAckRecords, _msiRecords, _msiTransactionOrdinals,
        _fatalPublications, _servingPhases, _publishSegments, _servingFailures,
        _servingCancellations, kvPersistentManifest);
    checkpoint::event(archive, phaseBarrierEvent, *this);
}

void Gate3ObservationRecorder::serialize(CheckpointOut &cp) const
{
    checkpoint::save(cp, name(), *this);
}

void Gate3ObservationRecorder::unserialize(CheckpointIn &cp)
{
    checkpointState = checkpoint::read(cp, name());
}

void Gate3ObservationRecorder::drainResume()
{
    SimObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/ruby/common/ConsumerCheckpoint.hh"

namespace gem5::axi
{
template<class Archive>
void AxiInitiatorAdapter::serialize(Archive &archive, unsigned)
{
    fatal_if(rawProbe || !functionalState || !functionalIdle(),
             "E_CHECKPOINT_BOUNDARY: initiator %s is not drained", name());
    Consumer::checkpoint(archive);
    checkpoint::fields(archive, *functionalState, adapterProgress, queueHighWater);
}

void AxiInitiatorAdapter::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void AxiInitiatorAdapter::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void AxiInitiatorAdapter::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}

template<class Archive>
void AxiTargetAdapter::serialize(Archive &archive, unsigned)
{
    fatal_if(rawProbe || !functionalState || !functionalIdle() ||
             !responseDestinations.empty(),
             "E_CHECKPOINT_BOUNDARY: target %s is not drained", name());
    Consumer::checkpoint(archive);
    checkpoint::fields(archive, *functionalState, queueHighWater,
                       orphanOrQuotaStallCycles);
}

void AxiTargetAdapter::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void AxiTargetAdapter::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void AxiTargetAdapter::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

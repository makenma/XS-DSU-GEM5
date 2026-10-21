#include "dev/ai_mesh/npu_serving_frontend.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void NpuServingFrontend::serialize(Archive &archive, unsigned)
{
    fatal_if(!servingExecution || !meshExecutor,
             "E_CHECKPOINT_CONFIG: frontend requires serving execution");
    if constexpr (Archive::is_saving::value)
        fatal_if(!programBoundaryReady(),
                 "E_CHECKPOINT_BOUNDARY: frontend is not drained");
    checkpoint::fields(archive, completionLedger, sqAdmission, sqIntakeLedger,
        kvManager, *meshExecutor, ackReceivedSeq, msiConfirmedSeq,
        liveCqObligations, stage, requestCount, generateDeadlineTicks,
        terminalGenerateRequests, expectedDoorbellTail, advertisedSqTail,
        nextCqSequence, sqConsumer, cqAssignments, msiIssued,
        cqBackpressureObserved, acceptedDoorbellTails, terminalResults,
        terminalBarrierTick, responseIngressOrdinal, sqReadIssueReadyTick,
        outputBErrorFired);
    checkpoint::event(archive, tickEvent, *this);
    checkpoint::event(archive, fatalDrainEvent, *this);
    checkpoint::event(archive, retireEvent, *this);
    checkpoint::event(archive, terminalEvent, *this);
    checkpoint::event(archive, executionEvent, *this);
    checkpoint::event(archive, quantumEvent, *this);
    checkpoint::event(archive, replayPrepareEvent, *this);
}

void NpuServingFrontend::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    if (servingExecution)
        checkpoint::save(cp, name(), *this);
}

void NpuServingFrontend::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    if (servingExecution)
        checkpointState = checkpoint::read(cp, name());
}

void NpuServingFrontend::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty()) {
        ensureServingPrepared();
        checkpoint::restore(checkpointState, *this);
    }
}
}

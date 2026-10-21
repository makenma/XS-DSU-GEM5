#include "dev/ai_mesh/agent_axi_driver.hh"
#include "sim/checkpoint_archive.hh"

namespace gem5::ai_mesh
{
template<class Archive>
void AgentAxiDriver::serialize(Archive &archive, unsigned)
{
    fatal_if(!planDrivenMode || !workloadManager || !coordinator,
             "E_CHECKPOINT_CONFIG: driver requires a workload plan");
    if constexpr (Archive::is_saving::value)
        fatal_if(!programBoundaryReady(),
                 "E_CHECKPOINT_BOUNDARY: driver is not drained");
    checkpoint::fields(archive, *workloadManager, *coordinator, ledger,
        irqCommitQueue, controlIndexByRequest, controlRequestByIndex,
        standaloneControlRequests, consumedGenerateRequests, acceptedGenerateRequests,
        stage, currentSqSequence, currentRequestId, currentCookie,
        currentParameterBlockBytes, currentCqSequence, completedRequests,
        submissionAttempts, doorbellAttempts, localReadyTick, metadataReadReadyTick,
        cqReadReadyTick, cqReadDelayPending, controlDoorbellFaultTail,
        controlDoorbellBHeldUntil, metadataReadPhase, metadataTotalBytes, metadataData,
        drainUntilTick, cqConsumer, cqAck, liveCqObligations, currentPrepared,
        staleSent, duplicateSent, emptySent, sqBackpressureObserved, futureAckSent,
        futureAckProbe, irqSeen, observedSqHead, currentDoorbellTail, exitIssued,
        issuedRequestIds, consumedSqSequences, submissionContexts, planIntents,
        currentIntent, submitPhase, submitIntent, submitSqSequence, submitRequestId,
        submitCookie, submitDoorbellTail, submitParameterBlockBytes, submitLocalReadyTick,
        completePhase, observedCqStatus, observedCqFlags, observedCqValue,
        cqMetadataPending, responseIngressOrdinal, requestCount);
    if constexpr (Archive::is_loading::value) {
        for (auto &intent : planIntents)
            intent = workloadManager->intentForRequest(intent.requestId);
        currentIntent = workloadManager->intentForRequest(currentIntent.requestId);
        submitIntent = workloadManager->intentForRequest(submitIntent.requestId);
    }
    checkpoint::event(archive, tickEvent, *this);
    checkpoint::event(archive, managerWakeEvent, *this);
    checkpoint::event(archive, fatalDrainEvent, *this);
}

void AgentAxiDriver::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    checkpoint::save(cp, name(), *this);
}

void AgentAxiDriver::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    checkpointState = checkpoint::read(cp, name());
}

void AgentAxiDriver::drainResume()
{
    ClockedObject::drainResume();
    if (!checkpointState.empty())
        checkpoint::restore(checkpointState, *this);
}
}

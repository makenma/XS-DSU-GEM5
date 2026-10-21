#include "dev/ai_mesh/agent_axi_driver.hh"
#include "dev/ai_mesh/npu_serving_frontend.hh"
#include "mem/ruby/network/Network.hh"

namespace gem5::ai_mesh
{

void AgentAxiDriver::switchProgramTiming(bool aggregate)
{
    fatal_if(!programBoundaryReady(), "network timing handoff requires a drained program boundary");
    network->setAggregateMode(aggregate);
}

void NpuServingFrontend::switchProgramTiming(bool aggregate)
{
    fatal_if(!programBoundaryReady(), "network timing handoff requires a drained program boundary");
    driverRef->switchProgramTiming(aggregate);
}

void
AgentAxiDriver::requestProgramBoundary(const ServingMeshExecutor &executor)
{
    fatal_if(!workloadManager || drainBegun() || recorder->fatalPending() ||
             recorder->fatalRecorded() || workloadManager->generateIsPaused(),
             "serving program boundary requires an active unpaused workload");
    std::set<uint32_t> activeItems;
    for (const auto &intent : planIntents)
        if (intent.commandKind == kAgentCommandGenerate &&
                !consumedGenerateRequests.count(intent.requestId))
            activeItems.insert(intent.workloadPlanItemId);
    workloadManager->pauseGenerate(executor.replayBoundaryAdmissions(activeItems));
    scheduleTick();
}

bool
AgentAxiDriver::programBoundaryReady() const
{
    return workloadManager && workloadManager->generateIsPaused() &&
        !recorder->fatalPending() && !recorder->fatalRecorded() &&
        submitPhase == SubmitPhase::Idle && completePhase == CompletePhase::Idle &&
        !activeWrite && !activeRead && !pendingWriteResponse &&
        !ledger.hasPending() && irqCommitQueue.pending() == 0 &&
        completedRequests == submissionAttempts && cancelJoins.empty() &&
        !workloadManager->controlIntentsPending() &&
        !workloadManager->boundaryAdmissionsPending() &&
        master->functionalIdle() && target->functionalIdle() &&
        network && network->isQuiescent();
}

void
AgentAxiDriver::resumeProgramBoundary()
{
    fatal_if(!programBoundaryReady(), "serving program boundary is not drained");
    workloadManager->resumeGenerate();
    scheduleTick();
}

bool
ServingMeshExecutor::programBoundaryReady() const
{
    return slots.empty() && !quantum_scheduled_tick && mesh_dispatch &&
        mesh_dispatch->replayQuiescent() &&
        std::all_of(batches.begin(), batches.end(), [](const auto &batch) {
            return batch->members.empty() && !batch->draining();
        });
}

void
NpuServingFrontend::requestProgramBoundary()
{
    fatal_if(!servingExecution || !driverRef,
             "program boundary requires serving execution with a workload driver");
    ensureServingPrepared();
    driverRef->requestProgramBoundary(*meshExecutor);
}

bool
NpuServingFrontend::programBoundaryReady() const
{
    std::string reason;
    return servingExecution && driverRef && driverRef->programBoundaryReady() &&
        !currentValid && !businessParked() && acceptedQueue.empty() &&
        pendingReleases.empty() && pendingCancels.empty() && doorbells.empty() &&
        !activeRead && !activeWrite && msiWrites.empty() &&
        deferredWriteResponses.empty() && pendingMsiCompletions.empty() &&
        pendingAckCommits.empty() && pendingAckRetirements.empty() &&
        !sqCommitPending && sqConsumer == advertisedSqTail &&
        completionLedger.liveCount() == 0 && completionLedger.msiRobEntries() == 0 &&
        sqIntakeLedger.liveCount() == 0 && master->functionalIdle() &&
        controlTarget->functionalIdle() && meshExecutor &&
        meshExecutor->programBoundaryReady() && kvManager.persistentReady(reason);
}

void
NpuServingFrontend::resumeProgramBoundary()
{
    fatal_if(!programBoundaryReady(), "serving program boundary is not drained");
    driverRef->resumeProgramBoundary();
}

}

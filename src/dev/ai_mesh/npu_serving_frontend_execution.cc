#include "dev/ai_mesh/npu_serving_frontend.hh"

#include <fstream>
#include <iterator>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_axi_driver.hh"
#include "dev/ai_mesh/gate3_protocol_runtime_internal.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_kv_layout.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/serving_mesh_executor.hh"
#include "sim/core.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

void
NpuServingFrontend::onGate3DrainComplete()
{
    if (servingExecution && meshExecutor && !recorder->fatalRecorded()) {
        meshExecutor->finishServingRun();
        std::string reason;
        const auto manifest = KvPersistentManifest::capture(kvManager, servingPersistentIdentity, reason);
        fatal_if(!manifest, "persistent KV capture failed: %s", reason);
        const auto json = manifest->json(reason);
        fatal_if(!json, "persistent KV manifest failed: %s", reason);
        recorder->setKvPersistentManifest(*json);
    }
}

DrainState
NpuServingFrontend::drain()
{
    fatal_if(servingExecution && !programBoundaryReady(),
             "E_CHECKPOINT_BOUNDARY: serving checkpoint requires a drained program boundary");
    return ClockedObject::drain();
}

void
NpuServingFrontend::onPublishSegment(const PublishSegmentFact &fact, bool firstChunk)
{
    recorder->recordPublishSegment(fact);
    if (firstChunk && driverRef)
        driverRef->notifyControlEvent(fact.request, ControlTriggerEvent::AfterFirstOutputChunk);
}

void
NpuServingFrontend::onBatchFrozen(uint64_t requestId)
{
    recordSemantic("BATCH_FREEZE", "CONTEXT", std::nullopt, requestId);
    if (driverRef)
        driverRef->notifyControlEvent(requestId, ControlTriggerEvent::AfterBatchFreeze);
}

void
NpuServingFrontend::onSessionAdmitted(uint64_t requestId)
{
    recordSemantic("SESSION_ADMISSION", "CONTEXT", std::nullopt, requestId);
    if (driverRef)
        driverRef->notifyControlEvent(requestId, ControlTriggerEvent::AfterSessionAdmission);
    const auto queued = acceptedQueue.find(requestId);
    if (queued != acceptedQueue.end()) {
        queued->second.sessionAdmitted = true;
        return;
    }
    if (parkedBusiness && parkedBusiness->requestId == requestId) {
        parkedBusiness->sessionAdmitted = true;
        return;
    }
    fatal_if(requestId != currentRequestId,
             "%s: admission has no matching request context", name());
    currentSessionAdmitted = true;
}

void
NpuServingFrontend::onCoreStart(const NpuExecutionRequest &request)
{
    recordSemantic("CORE_START", "CONTEXT", std::nullopt, request.requestId,
                   request.completionCookie);
    recorder->setMetric("core_starts", recorder->metric("core_starts") + 1);
}

void
NpuServingFrontend::onRequestComplete(
    const NpuExecutionRequest &request,
    const NpuExecutionCompletion &completion)
{
    if (completion.outcome == NpuExecutionOutcome::Cancelled)
        recorder->recordServingCancellation(request.requestId, completion.terminalPosition);
    if (completion.failure)
        recorder->recordServingFailure(request.requestId, *completion.failure);
    if (completion.kvTerminal)
        recorder->setMetric("kv_cached_tokens",
                            completion.kvTerminal->cached_tokens);
    const auto queued = acceptedQueue.find(request.requestId);
    if (queued != acceptedQueue.end()) {
        queued->second.completion = completion;
        return;
    }
    if (parkedBusiness && parkedBusiness->requestId == request.requestId) {
        parkedBusiness->completion = completion;
        return;
    }
    fatal_if(request.requestId != currentRequestId,
             "%s: completion request has no matching context", name());
    currentCompletion = completion;
    if (!executionCurrent())
        return;
    if (servingExecution) {
        finishServingRequest();
        return;
    }
    startOutput();
}

void
NpuServingFrontend::finishServingRequest()
{
    fatal_if(!currentCompletion, "%s: missing serving completion", name());
    fatal_if(currentCompletion->outcome == NpuExecutionOutcome::Pending,
             "%s: serving completion is not terminal", name());
    if (currentCompletion->infrastructureFailure) {
        requestFatal(*currentCompletion->infrastructureFailure);
        return;
    }
    if (currentCompletion->admissionRejectDetail) {
        const auto detail = *currentCompletion->admissionRejectDetail;
        const auto disposition = agent_abi::detailDispositionV1(detail);
        fatal_if(!disposition, "%s: unmapped admission rejection", name());
        if (currentSessionAdmitted) {
            currentCqStatus = static_cast<uint16_t>(disposition->cqStatus);
            currentDetailCode = detail;
            currentOutputBytes = currentCompletion->outputBytes;
            startMetadata();
        } else {
            queueErrorCompletion(static_cast<uint16_t>(disposition->cqStatus),
                                 agent_abi::kCqFlagsDETAIL_IN_CQ, detail);
        }
    } else if (currentCompletion->outcome == NpuExecutionOutcome::Cancelled) {
        if (currentSessionAdmitted) {
            currentCqStatus = agent_abi::kCqStatusCANCELLED;
            currentOutputBytes = currentCompletion->outputBytes;
            startMetadata();
        } else {
            queueErrorCompletion(agent_abi::kCqStatusCANCELLED,
                agent_abi::kCqFlagsDETAIL_IN_CQ, agent_abi::E_REQUEST_CANCELLED);
        }
    } else if (!currentCompletion->succeeded()) {
        fatal_if(!currentCompletion->failure, "runtime error has no batch failure record");
        currentDetailCode = currentCompletion->failure->code;
        const auto disposition = agent_abi::detailDispositionV1(
            static_cast<agent_abi::DetailCode>(currentDetailCode));
        fatal_if(!disposition || !currentSessionAdmitted, "runtime error has no admitted disposition");
        currentCqStatus = static_cast<uint16_t>(disposition->cqStatus);
        currentOutputBytes = currentCompletion->outputBytes;
        startMetadata();
    } else {
        currentCqStatus = agent_abi::kCqStatusSUCCESS;
        startMetadata();
    }
}

void
NpuServingFrontend::completeExecution()
{
    fatal_if(!lastExecutionRequest,
             "%s: execution completion without a submitted request", name());
    activeExecutor()->postCompletion(*lastExecutionRequest);
}

void
NpuServingFrontend::ensureServingPrepared()
{
    if (meshExecutor)
        return;
    fatal_if(servingLoader == nullptr || servingDispatcher == nullptr,
             "%s: serving_mesh is not configured", name());
    fatal_if(!servingLoader->loaded(),
             "%s: serving program is not loaded", name());
    std::array<uint8_t, 32> weight_digest{};
    for (size_t index = 0; index < weight_digest.size(); ++index) {
        const std::string byte = servingWeightImageDigest.substr(index * 2, 2);
        weight_digest[index] = static_cast<uint8_t>(
            std::stoul(byte, nullptr, 16));
    }
    const DecodedProgram &serving_program = servingLoader->program();
    std::array<uint8_t, 32> layout_digest{};
    MeshLoadError layout_error;
    const auto kv_profile = std::find_if(serving_program.transport.agent_request_profiles.begin(),
        serving_program.transport.agent_request_profiles.end(), [](const auto &row) {
            return row.flags & mesh_abi::kAgentRequestFlagsHAS_KV;
        });
    const bool uses_kv = kv_profile != serving_program.transport.agent_request_profiles.end();
    fatal_if(uses_kv && !kvLayoutDigest(serving_program, servingLoader->programAdmission().context(), layout_digest, layout_error),
             "%s: %s: %s", name(), layout_error.code, layout_error.message);
    fatal_if(serving_program.transport.agent_request_profiles.empty(),
             "%s: serving program has no request profile", name());
    servingPersistentIdentity.architecture = serving_program.header.arch_digest;
    servingPersistentIdentity.program = (serving_program).metadata.semantic_sha256;
    servingPersistentIdentity.weights = weight_digest;
    servingPersistentIdentity.layout = layout_digest;
    fatal_if(!planImage, "persistent identity requires the installed command plan");
    servingPersistentIdentity.plan = planImage->imageDigest();
    if (uses_kv)
        servingContractDigest = MeshKvManager::contractDigest(
            serving_program.metadata.semantic_sha256, weight_digest, layout_digest, kv_profile->kv_bytes_per_token);
    for (const auto &profile : serving_program.transport.agent_request_profiles) {
        const auto *binding = findHostBindingPlan(frozenBindingPlans, profile.program_id, profile.profile_id);
        fatal_if(!binding || binding->publishChunkBytes != profile.publish_chunk_bytes ||
            binding->primaryKvSymbolId != profile.primary_kv_symbol_id,
            "E_BINDING_ROLE: Host phase contract differs from the admitted request profile");
    }
    meshExecutor = std::make_unique<ServingMeshExecutor>(
        servingLoader->admissionPointer(), kvManager, servingLoader->servingCapacity(), acceptedQueueEntries);
    std::optional<ServingBatchReplay> replay;
    if (!servingBatchReplayArtifact.empty()) {
        std::ifstream file(servingBatchReplayArtifact);
        fatal_if(!file, "E_BATCH_REPLAY_DIVERGENCE: cannot open replay artifact");
        const std::string json((std::istreambuf_iterator<char>(file)), {});
        std::string reason;
        replay = ServingBatchReplay::parse(json, *planImage, serving_program, reason);
        fatal_if(!replay, "E_BATCH_REPLAY_DIVERGENCE: %s", reason);
        inform("Serving batch replay digest: %s", replay->digest);
    }
    meshExecutor->configureReplay(std::move(replay), servingStrictReplay);
    meshExecutor->setReleaseOutcomeSink([this](const KvEdgeResult &result) {
        consumeReleaseOutcomes(result);
    });
    SchedulerLimits limits;
    limits.maxActiveSequences = schedulerMaxActiveSequences;
    limits.maxBatchTokens = schedulerMaxBatchTokens;
    limits.maxPrefillTokensPerBatch = schedulerMaxPrefillTokensPerBatch;
    limits.maxDecodeSequencesPerBatch =
        schedulerMaxDecodeSequencesPerBatch;
    limits.batchWaitCycles = schedulerBatchWaitCycles;
    limits.prefillStarvationCycles = schedulerPrefillStarvationCycles;
    meshExecutor->setSchedulerLimits(limits);
    meshExecutor->setClockPeriod(clockPeriod());
    meshExecutor->setQuantumDriver([this](uint64_t tick) {
        scheduleServingQuantum(tick);
    });
    meshExecutor->attachDispatch(*servingDispatcher);
    servingDispatcher->setDmaObserver(meshExecutor.get());
    servingDispatcher->setInstanceObserver(meshExecutor.get());
    meshExecutor->setTickSource([this]() { return curTick(); });
    meshExecutor->setPhaseFactSink([this](const ServingPhaseFact &fact) {
        recorder->recordServingPhase(fact);
    });
    meshExecutor->attachSink(this);
    if (planRegistry != nullptr) {
        for (const auto &request_profile : serving_program.transport.agent_request_profiles) {
            const auto *surrogate = planRegistry->find(request_profile.program_id, request_profile.profile_id);
            fatal_if(surrogate && surrogate->outputBytes != request_profile.host_output_bytes,
                     "%s: surrogate output size differs from the immutable mesh request profile", name());
        }
        meshExecutor->setDigestSource(
            [this](const NpuExecutionRequest &request, uint32_t output_tokens) {
                fatal_if(output_tokens > request.maxOutputTokens,
                         "%s: serving output token prefix exceeds the request", name());
                const std::array<uint8_t, 32> seed = surrogateSeed(
                    request.inputDigest.data(), request.programId,
                    request.profileId, request.requestedProfileKey);
                std::vector<std::array<uint8_t, 32>> token_digests;
                token_digests.reserve(output_tokens);
                for (uint32_t ordinal = 0; ordinal < output_tokens;
                     ++ordinal)
                    token_digests.push_back(
                        surrogateTokenDigest(seed.data(), ordinal));
                return surrogateOutputPrefixDigest(
                    request.workloadDigest.data(),
                    request.workloadPlanItemId, output_tokens,
                    token_digests);
            });
    }
    if (kvManager.fatalReason()) {
        fatal("%s: invalid KV geometry or capacity: %s", name(),
              kvManager.fatalReason()->c_str());
    }
}

void
NpuServingFrontend::scheduleExecutionEvent(Tick deadline)
{
    if (executionEvent.scheduled())
        reschedule(&executionEvent, deadline, true);
    else
        schedule(&executionEvent, deadline);
}

void
NpuServingFrontend::scheduleServingQuantum(uint64_t tick)
{
    const Tick edge = clockEdge();
    if (tick <= edge) {
        tick = edge;
    } else {
        const Tick delta = tick - edge;
        const Tick cycles = delta / clockPeriod() + (delta % clockPeriod() != 0);
        fatal_if(cycles > (MaxTick - edge) / clockPeriod(),
                 "%s: scheduler quantum exceeds the simulation clock", name());
        tick = edge + cycles * clockPeriod();
    }
    if (quantumEvent.scheduled()) {
        if (static_cast<uint64_t>(quantumEvent.when()) <= tick)
            return;
        reschedule(&quantumEvent, tick, true);
        return;
    }
    schedule(&quantumEvent, tick);
}

void
NpuServingFrontend::processServingQuantum()
{
    if (!meshExecutor || recorder->fatalRecorded())
        return;
    meshExecutor->onQuantumEdge(curTick());
}

}
}

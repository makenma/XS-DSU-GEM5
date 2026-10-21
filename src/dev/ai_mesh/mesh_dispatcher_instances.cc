#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"
#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/peer_sram_aperture.hh"

#include <algorithm>
#include <filesystem>
#include <fstream>

namespace gem5::ai_mesh
{

bool MeshDispatcher::replayQuiescent() const
{
    if (started || staging_candidate || active_candidate || !cache_fill_edge->empty() ||
        std::any_of(serving_candidates.begin(), serving_candidates.end(),
            [](const auto &candidate) { return candidate.has_value(); }))
        return false;
    for (const auto *core : cores) {
        const auto *cache = core->weightCache();
        const auto *dma = dynamic_cast<const AxiTensorDmaEngine *>(core->dmaEngine());
        if (!core->quiescent() || !core->dmaEngine()->idle() ||
            (dma && !dma->bridgeOf()->idle()) ||
            (cache && !cache->quiescent()))
            return false;
    }
    return memory.idle() &&
        std::all_of(apertures.begin(), apertures.end(),
            [](const auto *aperture) { return aperture->idle(); });
}

void MeshDispatcher::configureServingCapacity(uint32_t capacity)
{
    fatal_if(!serving_candidates.empty() || !capacity, "invalid serving candidate capacity");
    serving_candidates.resize(capacity);
    failure_reducer.configureMemberCapacity(capacity);
}

std::optional<InstanceGeneration>
MeshDispatcher::selectInstance(uint32_t entrypoint, uint32_t profile, std::string &reason)
{
    if (!instance_observer || !loader->loaded() || staging_candidate ||
            program_terminal != ProgramTerminal::NONE) {
        reason = "E_KV_STATE: dispatcher cannot stage a serving candidate";
        return std::nullopt;
    }
    const auto *view = loader->executionViews().forInstance(entrypoint, profile);
    const auto free = std::find_if(serving_candidates.begin(), serving_candidates.end(),
        [](const auto &candidate) { return !candidate; });
    const auto next = checkedSequenceAdd(allocated_instance, 1);
    if (!view || !entrypoint || !profile || !next || free == serving_candidates.end()) {
        reason = "E_REQUEST_PROFILE: serving candidate has no view, identity or capacity";
        return std::nullopt;
    }
    free->emplace();
    staging_candidate = &**free;
    staging_candidate->instance = *next;
    staging_candidate->view = view;
    allocated_instance = *next;
    return next;
}

void MeshDispatcher::beginSelectedInstance(std::optional<ErrorCandidate> failure)
{
    fatal_if(!staging_candidate, "dispatcher has no staged serving candidate");
    if (staging_candidate->overlays && loader->weightPolicy() == "cached")
        staging_candidate->demand = cacheDemands(*staging_candidate->overlays);
    if (staging_candidate->overlays && !result_json_path.empty()) {
        for (const auto &[layer, graph] : *staging_candidate->overlays) {
            const auto name = "moe_instance_" + std::to_string(staging_candidate->instance.value()) +
                "_layer_" + std::to_string(layer) + ".bin";
            const auto path = std::filesystem::path(result_json_path).parent_path() / name;
            const auto image = MoeOverlayImage{}.encode(graph, layer);
            std::ofstream file(path, std::ios::binary);
            file.write(reinterpret_cast<const char *>(image.data()), image.size());
            fatal_if(!file, "cannot write frozen MoE artifact %s", path.string());
        }
    }
    fatal_if(!failure_reducer.begin(staging_candidate->instance.value(),
                 staging_candidate->instance.value(), staging_candidate->binding.memberRequests()),
             "frozen candidate has no failure record capacity");
    const auto instance = staging_candidate->instance.value();
    staging_candidate->failure_pending = failure.has_value();
    staging_candidate = nullptr;
    if (failure)
        stageInstanceFailure(instance, *failure);
    else
        retryCacheReservations();
}

void MeshDispatcher::pumpServingCandidates()
{
    std::vector<ServingCandidate *> ordered;
    ordered.reserve(serving_candidates.size());
    for (auto &candidate : serving_candidates)
        if (candidate && &*candidate != staging_candidate)
            ordered.push_back(&*candidate);
    std::sort(ordered.begin(), ordered.end(), [](const auto *a, const auto *b) {
        return a->instance.value() < b->instance.value();
    });
    bool waiting = false;
    for (auto *candidate : ordered) {
        if (candidate->failure_pending)
            continue;
        auto status = MoeWeightCache::ReserveStatus::COMMITTED;
        if (!candidate->ready) {
            status = reserveCacheDemand(candidate->instance.value(), candidate->demand);
        } else {
            for (const auto &request : candidate->demand)
                if (coreOf(request.core_id)->weightCache()->prepare(candidate->instance.value(),
                        request.demand, curTick()).status == MoeWeightCache::ReserveStatus::FAILED)
                    status = MoeWeightCache::ReserveStatus::FAILED;
        }
        if (status == MoeWeightCache::ReserveStatus::FAILED) {
            const auto instance = candidate->instance.value();
            bool found = false;
            for (const auto &request : candidate->demand)
                for (const auto &layer : request.demand)
                    for (const auto tag : layer.tags)
                        if (auto error = coreOf(request.core_id)->weightCache()->failure(tag)) {
                            error->tick = curTick();
                            stageInstanceFailure(instance, *error);
                            found = true;
                        }
            fatal_if(!found, "cache rejection has no immutable failure record");
            candidate->failure_pending = true;
            continue;
        }
        if (status == MoeWeightCache::ReserveStatus::RESOURCE_WAIT) {
            if (!candidate->waited) {
                candidate_waits++;
                candidate_events.push_back({candidate->instance.value(), curTick(), "RESOURCE_WAIT"});
            }
            candidate->waited = true;
            waiting = true;
            continue;
        }
        if (!candidate->ready) {
            candidate->ready = true;
            if (candidate->waited) {
                candidate_resumes++;
                candidate_events.push_back({candidate->instance.value(), curTick(), "RESUMED"});
            }
            for (const auto &request : candidate->demand)
                for (const auto &token : request.tokens)
                    if (token.outcome == mesh_abi::kCacheResidencyOutcomeATTACH)
                        candidate->attached = true;
            if (candidate->attached)
                candidate_attaches++;
            candidate_events.push_back({candidate->instance.value(), curTick(),
                candidate->attached ? "ATTACHED" : "RESERVED"});
        }
    }
    if (waiting && !cache_retry_event.scheduled())
        schedule(&cache_retry_event, clockEdge(Cycles(1)) + 1);
    if (started || batch_setup_done || selected_view || start_event.scheduled() || active_candidate)
        return;
    ServingCandidate *next = nullptr;
    for (auto &candidate : serving_candidates)
        if (candidate && candidate->ready && !candidate->failure_pending &&
                (!next || candidate->instance.value() < next->instance.value()))
            next = &*candidate;
    if (!next)
        return;
    for (auto &candidate : serving_candidates)
        if (candidate && &*candidate == next) {
            active_candidate.emplace(std::move(*candidate));
            candidate.reset();
            break;
        }
    instance_counter = active_candidate->instance;
    selected_view = active_candidate->view;
    instance_binding = active_candidate->binding;
    serving_overlays = active_candidate->overlays;
    pending_fill_bindings = std::move(active_candidate->fills);
    semantic_digest = active_candidate->digest;
    semantic_digest_set = active_candidate->has_digest;
    begin();
}

}

#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_program_loader.hh"

#include <algorithm>

namespace gem5::ai_mesh
{
std::vector<MeshDispatcher::CacheReservationRequest>
MeshDispatcher::cacheDemands(const MoeOverlaySet &overlays) const
{
    std::vector<CacheReservationRequest> requests;
    for (MeshDummyCore *core : cores) {
        if (core->weightCache() == nullptr)
            continue;
        CacheReservationRequest request;
        request.core_id = core->archCoreId();
        std::map<uint32_t, std::map<uint32_t, uint32_t>> demand;
        for (const auto &entry : overlays) {
            const uint32_t layer_id = entry.first;
            for (const auto &object : entry.second.objects()) {
                if (object.kind != mesh_abi::kMeshObjectKindVIEW ||
                    object.secondary_kind != mesh_abi::kMoeViewKindWEIGHT)
                    continue;
                if (object.backing_kind !=
                    mesh_abi::kMoeViewBackingWEIGHT_CACHE_SLOT)
                    continue;
                if (object.owner_core != core->archCoreId())
                    continue;
                demand[layer_id][object.ref_ordinal]++;
            }
        }
        for (const auto &layer : demand) {
            MoeWeightCache::LayerTags tags;
            tags.layer_id = layer.first;
            for (const auto &tag : layer.second) {
                tags.tags.push_back(tag.first);
                request.consumers[layer.first][tag.first] = tag.second;
            }
            request.demand.push_back(tags);
        }
        if (request.demand.empty())
            continue;
        requests.push_back(request);
    }
    std::sort(requests.begin(), requests.end(),
              [](const CacheReservationRequest &left,
                 const CacheReservationRequest &right) {
                  return std::tie(left.core_id) < std::tie(right.core_id);
              });
    return requests;
}

bool MeshDispatcher::reserveWeightCaches()
{
    if (loader->weightPolicy() != "cached")
        return true;
    std::vector<CacheReservationRequest> requests;
    if (cache_reservation_queue.empty()) {
        requests = cacheDemands(loader->overlayGraphs());
        cache_queue_capacity = uint32_t(requests.size());
        cache_reservation_queue.clear();
        next_cache_request_id = 1;
    } else {
        requests = cache_reservation_queue;
    }
    if (requests.empty())
        return true;
    if (resolveCacheReservations(requests)) {
        cache_reservation_waiting = false;
        if (cache_retry_event.scheduled())
            deschedule(&cache_retry_event);
        return true;
    }
    cache_reservation_waiting = !instance_error_latched;
    if (!cache_reservation_waiting) {
        if (cache_retry_event.scheduled())
            deschedule(&cache_retry_event);
        return false;
    }
    if (watchdog_ticks_value > 0 && !watchdog_event.scheduled())
        schedule(&watchdog_event, curTick() + watchdog_ticks_value);
    if (!cache_retry_event.scheduled())
        schedule(&cache_retry_event, clockEdge() + 1);
    return false;
}

void MeshDispatcher::queueCacheFill(MoeWeightCache &cache, const mesh_abi::WeightFillKey &key,
    std::optional<ErrorCandidate> failure, std::optional<uint64_t> valid_bytes, bool descriptor_drained)
{
    fatal_if(!cache_fill_edge->stage(cache, key, curTick(), failure, valid_bytes, descriptor_drained),
             "cache fill response cannot enter its bounded edge");
    if (!failure_event.scheduled())
        schedule(&failure_event, curTick());
}

bool MeshDispatcher::retryCacheReservations()
{
    if (cache_retry_event.scheduled() && cache_retry_event.when() > curTick())
        deschedule(&cache_retry_event);
    if (!cache_retry_event.scheduled())
        schedule(&cache_retry_event, curTick());
    return true;
}

bool MeshDispatcher::commitCacheReservations()
{
    if (instance_observer) {
        pumpServingCandidates();
        return true;
    }
    if (!cache_reservation_waiting)
        return false;
    if (!reserveWeightCaches())
        return false;
    // Resources arrived: finish the arm exactly once and start the batch.
    armOverlayGates();
    cache_reservation_waiting = false;
    batch_state = mesh_abi::MeshBatchState::REQUEST_ARMED;
    startInstance();
    return true;
}

bool MeshDispatcher::resolveCacheReservations(
    const std::vector<CacheReservationRequest> &requests)
{
    auto reservation = requests;
    const auto status = reserveCacheDemand(instance_counter.value(), reservation);
    if (status == MoeWeightCache::ReserveStatus::FAILED) {
        commitPrestartFailure();
        return false;
    }
    if (status == MoeWeightCache::ReserveStatus::RESOURCE_WAIT) {
        cache_reservation_queue = requests;
        for (auto &request : cache_reservation_queue)
            if (!request.request_id)
                request.request_id = next_cache_request_id++;
        return false;
    }
    for (const auto &request : reservation)
        coreOf(request.core_id)->installCacheTokens(request.tokens, request.consumers);
    cache_reservation_queue.clear();
    return true;
}

MoeWeightCache::ReserveStatus MeshDispatcher::reserveCacheDemand(uint64_t batch,
    std::vector<CacheReservationRequest> &requests)
{
    cache_reservation_attempts++;
    struct Prepared
    {
        CacheReservationRequest *request = nullptr;
        MoeWeightCache *cache = nullptr;
        MoeWeightCache::ReservePlan plan;
    };
    std::vector<Prepared> prepared;
    bool all_committed = true;
    bool any_failed = false;
    for (CacheReservationRequest &request : requests) {
        MeshDummyCore *core = coreOf(uint16_t(request.core_id));
        if (core == nullptr || core->weightCache() == nullptr)
            continue;
        Prepared entry;
        entry.request = &request;
        entry.cache = core->weightCache();
        entry.plan = entry.cache->prepare(batch,
                                          request.demand, curTick());
        if (entry.plan.status == MoeWeightCache::ReserveStatus::FAILED)
            any_failed = true;
        if (entry.plan.status != MoeWeightCache::ReserveStatus::COMMITTED)
            all_committed = false;
        prepared.push_back(entry);
    }
    if (any_failed)
        return MoeWeightCache::ReserveStatus::FAILED;
    if (!all_committed)
        return MoeWeightCache::ReserveStatus::RESOURCE_WAIT;
    for (auto &entry : prepared) {
        entry.request->tokens = entry.cache->commit(entry.plan, curTick());
        for (const auto &token : entry.request->tokens)
            entry.cache->setTokenConsumers(token.token_id,
                entry.request->consumers.at(token.layer_id).at(token.base.weight_tag_index));
        cache_reservation_commits++;
    }
    return MoeWeightCache::ReserveStatus::COMMITTED;
}

}

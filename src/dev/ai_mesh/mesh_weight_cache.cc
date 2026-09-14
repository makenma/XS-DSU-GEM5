#include "dev/ai_mesh/mesh_weight_cache.hh"

#include <cstring>
#include <vector>

#include <algorithm>
#include <tuple>

#include "base/logging.hh"
#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr char kFillTrafficDomain[] = "AI_MESH_WEIGHT_FILL_V1";

void putU16(uint8_t *out, uint16_t value)
{
    out[0] = uint8_t(value);
    out[1] = uint8_t(value >> 8);
}

void putU32(uint8_t *out, uint32_t value)
{
    for (int i = 0; i < 4; i++)
        out[i] = uint8_t(value >> (8 * i));
}

} // anonymous namespace

bool weightCacheBaseKeyLess(const mesh_abi::WeightCacheBaseKey &lhs,
                            const mesh_abi::WeightCacheBaseKey &rhs)
{
    if (lhs.core_id != rhs.core_id)
        return lhs.core_id < rhs.core_id;
    if (lhs.cache_partition_id != rhs.cache_partition_id)
        return lhs.cache_partition_id < rhs.cache_partition_id;
    if (lhs.weight_tag_index != rhs.weight_tag_index)
        return lhs.weight_tag_index < rhs.weight_tag_index;
    return lhs.cache_generation < rhs.cache_generation;
}

bool weightFillKeyLess(const mesh_abi::WeightFillKey &lhs,
                       const mesh_abi::WeightFillKey &rhs)
{
    mesh_abi::WeightCacheBaseKey left;
    left.core_id = lhs.core_id;
    left.cache_partition_id = lhs.cache_partition_id;
    left.weight_tag_index = lhs.weight_tag_index;
    left.cache_generation = lhs.cache_generation;
    mesh_abi::WeightCacheBaseKey right;
    right.core_id = rhs.core_id;
    right.cache_partition_id = rhs.cache_partition_id;
    right.weight_tag_index = rhs.weight_tag_index;
    right.cache_generation = rhs.cache_generation;
    if (weightCacheBaseKeyLess(left, right))
        return true;
    if (weightCacheBaseKeyLess(right, left))
        return false;
    return lhs.fill_incarnation < rhs.fill_incarnation;
}

std::array<uint8_t, mesh_abi::kWeightFillKeysBytes>
weightFillKeyBytes(const mesh_abi::WeightFillKey &key)
{
    std::array<uint8_t, mesh_abi::kWeightFillKeysBytes> out{};
    putU16(out.data() + mesh_abi::kWeightFillKeysCoreIdOffset, key.core_id);
    putU16(out.data() + mesh_abi::kWeightFillKeysCachePartitionIdOffset,
           key.cache_partition_id);
    putU32(out.data() + mesh_abi::kWeightFillKeysWeightTagIndexOffset,
           key.weight_tag_index);
    putU32(out.data() + mesh_abi::kWeightFillKeysCacheGenerationOffset,
           key.cache_generation);
    putU32(out.data() + mesh_abi::kWeightFillKeysFillIncarnationOffset,
           key.fill_incarnation);
    return out;
}

std::array<uint8_t, mesh_abi::kCacheDmaDescriptorKeysBytes>
cacheDmaDescriptorKeyBytes(const mesh_abi::WeightFillKey &key,
                           uint32_t segment_ordinal)
{
    std::array<uint8_t, mesh_abi::kCacheDmaDescriptorKeysBytes> out{};
    const auto key_bytes = weightFillKeyBytes(key);
    std::memcpy(out.data(), key_bytes.data(), key_bytes.size());
    putU32(out.data() + mesh_abi::kCacheDmaDescriptorKeysSegmentOrdinalOffset,
           segment_ordinal);
    return out;
}

std::string
weightFillTrafficId(const mesh_abi::WeightFillKey &key)
{
    const auto key_bytes = weightFillKeyBytes(key);
    std::vector<uint8_t> payload(
        reinterpret_cast<const uint8_t *>(kFillTrafficDomain),
        reinterpret_cast<const uint8_t *>(kFillTrafficDomain) +
            sizeof(kFillTrafficDomain));
    payload.insert(payload.end(), key_bytes.begin(), key_bytes.end());
    const auto digest = agentSha256(payload);
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

std::array<uint8_t, mesh_abi::kErrorSourceKeysBytes>
errorSourceKeyBytes(const mesh_abi::ErrorSourceKey &key)
{
    std::array<uint8_t, mesh_abi::kErrorSourceKeysBytes> out{};
    putU16(out.data() + mesh_abi::kErrorSourceKeysErrorClassOffset,
           key.error_class);
    putU16(out.data() + mesh_abi::kErrorSourceKeysCoreIdOrFfffOffset,
           key.core_id_or_ffff);
    out[mesh_abi::kErrorSourceKeysDomainOffset] = key.domain;
    out[mesh_abi::kErrorSourceKeysObjectKindOffset] = key.object_kind;
    putU32(out.data() + mesh_abi::kErrorSourceKeysRegionGroupIdOffset,
           key.region_group_id);
    putU32(out.data() + mesh_abi::kErrorSourceKeysRegionIdOffset,
           key.region_id);
    putU32(out.data() + mesh_abi::kErrorSourceKeysOrdinalOffset, key.ordinal);
    putU32(out.data() + mesh_abi::kErrorSourceKeysGenerationOffset,
           key.generation);
    std::memcpy(out.data() + mesh_abi::kErrorSourceKeysAuxKeyOffset,
                key.aux_key.data(), key.aux_key.size());
    return out;
}

mesh_abi::ErrorSourceKey
weightFillSource(const mesh_abi::WeightFillKey &key)
{
    mesh_abi::ErrorSourceKey source;
    source.error_class = mesh_abi::kMoeErrorClassWEIGHT_FILL;
    source.core_id_or_ffff = 0xFFFF;
    source.domain = mesh_abi::kMeshObjectDomainWEIGHT_FILL;
    source.object_kind = mesh_abi::kMeshObjectKindWEIGHT_FILL_OBLIGATION;
    source.generation = key.fill_incarnation;
    source.aux_key = weightFillKeyBytes(key);
    return source;
}

const char *
weightFillFailureDetail(uint16_t site)
{
    switch (site) {
    case mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R:
        return "E_AXI_RESPONSE";
    case mesh_abi::kWeightFillFailureSiteCACHE_FILL_SRAM_BOUNDS:
        return "E_DCORE_SRAM_BOUNDS";
    case mesh_abi::kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT:
        return "E_DCORE_ENGINE";
    case mesh_abi::kWeightFillFailureSiteCACHE_FILL_SOURCE_VALIDITY:
        return "E_DCORE_POISON_READ";
    default:
        return nullptr;
    }
}

namespace
{

std::string u64Text(uint64_t value)
{
    return std::to_string(value);
}

} // anonymous namespace

MoeWeightCache::MoeWeightCache(const Config &config) : config(config)
{
    if (config.slot_count == 0 || config.slot_bytes == 0)
        fatal("weight cache needs a positive slot geometry");
    slots_value.resize(config.slot_count);
    for (uint32_t index = 0; index < config.slot_count; index++)
        slots_value[index].slot_id = index;
    mshr_free = config.mshr_slots;
    eviction_free = config.eviction_slots;
    obligation_free = config.obligation_slots;
    subscriber_free = config.subscriber_slots;
    for (uint32_t tag : config.cacheable_tags)
        cacheable_tags.insert(tag);
}

mesh_abi::WeightCacheBaseKey
MoeWeightCache::baseKey(uint32_t tag_index) const
{
    if (cacheable_tags.count(tag_index) == 0)
        fatal("weight tag %u is not cacheable", tag_index);
    mesh_abi::WeightCacheBaseKey key;
    key.core_id = config.core_id;
    key.cache_partition_id = 1;
    key.weight_tag_index = tag_index;
    key.cache_generation = cache_generation;
    return key;
}

uint64_t
MoeWeightCache::slotBase(uint32_t slot_id) const
{
    return config.partition_base + uint64_t(slot_id) * config.slot_bytes;
}

bool
MoeWeightCache::hasTombstone(const mesh_abi::WeightCacheBaseKey &key) const
{
    return tombstoned_tags.count(tagOf(key)) != 0;
}

MoeCacheSlot *
MoeWeightCache::slotOf(const mesh_abi::WeightCacheBaseKey &key)
{
    for (auto &slot : slots_value)
        if (slot.state == kCacheSlotValid &&
            slot.weight_tag_index == key.weight_tag_index)
            return &slot;
    return nullptr;
}

bool
MoeWeightCache::shadow(
    const std::vector<mesh_abi::WeightCacheBaseKey> &keys,
    uint32_t occurrences,
    std::vector<std::pair<mesh_abi::WeightCacheBaseKey, uint32_t>> &plan,
    std::vector<std::pair<mesh_abi::WeightCacheBaseKey,
                          uint32_t>> &attached,
    std::vector<PendingFill> &fills,
    std::map<uint32_t, uint32_t> &victims, uint32_t &incarnation,
    uint64_t &next_epoch) const
{
    std::set<uint32_t> taken;
    for (const auto &key : keys)
        if (hasTombstone(key))
            return false;
    for (const auto &key : keys) {
        const MoeCacheSlot *slot = nullptr;
        for (const auto &candidate : slots_value)
            if (candidate.state == kCacheSlotValid &&
                candidate.weight_tag_index == key.weight_tag_index)
                slot = &candidate;
        if (slot != nullptr) {
            plan.emplace_back(key, slot->slot_id);
            taken.insert(slot->slot_id);
        }
    }
    for (const auto &key : keys) {
        bool hit = false;
        for (const auto &entry : plan)
            if (entry.first.weight_tag_index == key.weight_tag_index)
                hit = true;
        if (hit)
            continue;
        auto obligation = obligations_value.find(key.weight_tag_index);
        if (obligation != obligations_value.end())
            attached.emplace_back(key, key.weight_tag_index);
    }
    for (const auto &key : keys) {
        bool handled = false;
        for (const auto &entry : plan)
            if (entry.first.weight_tag_index == key.weight_tag_index)
                handled = true;
        for (const auto &entry : attached)
            if (entry.first.weight_tag_index == key.weight_tag_index)
                handled = true;
        if (handled)
            continue;
        const MoeCacheSlot *free = nullptr;
        for (const auto &slot : slots_value)
            if (slot.state == kCacheSlotFree &&
                taken.count(slot.slot_id) == 0) {
                free = &slot;
                break;
            }
        PendingFill fill;
        fill.base = key;
        if (free == nullptr) {
            const MoeCacheSlot *victim = nullptr;
            for (const auto &slot : slots_value) {
                if (slot.state != kCacheSlotValid || slot.pins != 0 ||
                    taken.count(slot.slot_id) != 0)
                    continue;
                const auto order = [](const MoeCacheSlot &candidate) {
                    return std::make_tuple(
                        candidate.has_epoch ? candidate.last_use_epoch : 0,
                        candidate.weight_tag_index, candidate.slot_id);
                };
                if (victim == nullptr || order(slot) < order(*victim))
                    victim = &slot;
            }
            if (victim == nullptr)
                return false;
            fill.victim = true;
            fill.slot_id = victim->slot_id;
            victims[victim->slot_id] = victim->weight_tag_index;
        } else {
            fill.slot_id = free->slot_id;
        }
        taken.insert(fill.slot_id);
        fills.push_back(fill);
    }
    if (uint32_t(fills.size()) > mshr_free ||
        uint32_t(fills.size()) > obligation_free ||
        uint32_t(victims.size()) > eviction_free ||
        occurrences > subscriber_free)
        return false;
    incarnation = next_fill_incarnation;
    next_epoch = next_use_epoch;
    return true;
}

MoeWeightCache::ReservePlan
MoeWeightCache::prepare(uint64_t batch_id,
                        const std::vector<LayerTags> &demand, uint64_t) const
{
    std::vector<mesh_abi::WeightCacheBaseKey> keys;
    ReservePlan plan;
    plan.batch_id = batch_id;
    plan.commit_epoch = commit_epoch;
    for (const LayerTags &layer : demand) {
        for (uint32_t tag : layer.tags) {
            mesh_abi::WeightCacheBaseKey key = baseKey(tag);
            bool duplicate = false;
            for (const auto &existing : keys)
                if (existing.weight_tag_index == key.weight_tag_index)
                    duplicate = true;
            if (!duplicate)
                keys.push_back(key);
            auto &layers = plan.layers[key.weight_tag_index];
            if (std::find(layers.begin(), layers.end(), layer.layer_id) ==
                layers.end())
                layers.push_back(layer.layer_id);
        }
    }
    for (auto &entry : plan.layers)
        std::sort(entry.second.begin(), entry.second.end());
    std::sort(keys.begin(), keys.end(),
              [](const mesh_abi::WeightCacheBaseKey &left,
                 const mesh_abi::WeightCacheBaseKey &right) {
                  return weightCacheBaseKeyLess(left, right);
              });
    if (keys.empty()) {
        plan.status = ReserveStatus::COMMITTED;
        return plan;
    }
    for (const auto &key : keys)
        if (hasTombstone(key)) {
            plan.status = ReserveStatus::FAILED;
            plan.layers.clear();
            return plan;
        }
    uint32_t occurrences = 0;
    for (const auto &entry : plan.layers)
        occurrences += uint32_t(entry.second.size());
    std::map<uint32_t, uint32_t> victims;
    uint32_t incarnation = 0;
    uint64_t next_epoch = 0;
    if (!shadow(keys, occurrences, plan.hits, plan.attached, plan.fills,
                victims, incarnation, next_epoch)) {
        // Backpressure discards the whole shadow: nothing is visible and no
        // partial plan can ever be committed.
        plan.status = ReserveStatus::RESOURCE_WAIT;
        plan.layers.clear();
        plan.hits.clear();
        plan.attached.clear();
        plan.fills.clear();
        return plan;
    }
    plan.status = ReserveStatus::COMMITTED;
    plan.first_incarnation = incarnation;
    plan.first_epoch = next_epoch;
    return plan;
}

MoeWeightCache::ReservePlan
MoeWeightCache::prepare(uint64_t batch_id, uint32_t layer_id,
                        const std::vector<uint32_t> &tag_indices,
                        uint64_t tick) const
{
    LayerTags layer;
    layer.layer_id = layer_id;
    layer.tags = tag_indices;
    return prepare(batch_id, std::vector<LayerTags>{layer}, tick);
}

bool
MoeWeightCache::planIsCurrent(const ReservePlan &plan) const
{
    for (const auto &entry : plan.hits) {
        const auto slot = std::find_if(
            slots_value.begin(), slots_value.end(),
            [&entry](const MoeCacheSlot &candidate) {
                return candidate.slot_id == entry.second;
            });
        if (slot == slots_value.end() || slot->state != kCacheSlotValid ||
            slot->weight_tag_index != entry.first.weight_tag_index)
            return false;
    }
    for (const auto &entry : plan.attached)
        if (obligations_value.count(entry.first.weight_tag_index) == 0)
            return false;
    for (const auto &fill : plan.fills) {
        const auto slot = std::find_if(
            slots_value.begin(), slots_value.end(),
            [&fill](const MoeCacheSlot &candidate) {
                return candidate.slot_id == fill.slot_id;
            });
        if (slot == slots_value.end())
            return false;
        const bool reusable =
            slot->state == kCacheSlotFree ||
            (slot->state == kCacheSlotValid && slot->pins == 0 &&
             slot->weight_tag_index != fill.base.weight_tag_index);
        if (!reusable)
            return false;
    }
    return plan.first_incarnation >= next_fill_incarnation;
}

std::vector<MoeCacheToken>
MoeWeightCache::commit(const ReservePlan &plan, uint64_t tick)
{
    fatal_if(plan.status != ReserveStatus::COMMITTED,
             "only a committed reservation plan can be applied");
    fatal_if(plan.commit_epoch != commit_epoch,
             "reservation shadow is stale: %llu commits happened after it "
             "was taken",
             (unsigned long long)(commit_epoch - plan.commit_epoch));
    fatal_if(!planIsCurrent(plan),
             "reservation shadow is stale: another commit already consumed "
             "its slots or incarnation");
    commit_epoch++;
    std::vector<MoeCacheToken> tokens;
    uint64_t epoch = plan.first_epoch;
    uint32_t incarnation = plan.first_incarnation;
    const uint64_t batch_id = plan.batch_id;
    const auto layers_of = [&plan](uint32_t tag) {
        const auto entry = plan.layers.find(tag);
        return entry == plan.layers.end() ? std::vector<uint32_t>()
                                          : entry->second;
    };
    const auto subscribe = [&](MoeCacheObligation &obligation, uint32_t tag) {
        for (uint32_t layer_id : layers_of(tag)) {
            obligation.subscribers[{batch_id, layer_id}].batch_id = batch_id;
            obligation.subscribers[{batch_id, layer_id}].layer_id = layer_id;
            obligation.subscribers[{batch_id, layer_id}].state =
                mesh_abi::kCacheSubscriberStateWAITING;
            subscriber_free--;
        }
    };
    // Every occurrence layer holds its own token and pin; only the physical
    // fill is shared, so the first layer of a key issues it and the rest
    // attach to the same obligation.
    const auto emit_tokens = [&](const mesh_abi::WeightCacheBaseKey &key,
                                 uint32_t slot_id, bool has_fill,
                                 const mesh_abi::WeightFillKey &fill,
                                 uint32_t outcome) {
        MoeCacheSlot &slot = slots_value[slot_id];
        const std::vector<uint32_t> layers = layers_of(key.weight_tag_index);
        for (uint32_t index = 0; index < layers.size(); index++) {
            slot.last_use_epoch = epoch++;
            slot.has_epoch = true;
            slot.pins++;
            MoeCacheToken token;
            token.token_id = next_token_id++;
            token.batch_id = batch_id;
            token.layer_id = layers[index];
            token.base = key;
            token.outcome = index == 0 ? outcome
                                       : mesh_abi::kCacheResidencyOutcomeATTACH;
            token.slot_id = slot_id;
            token.has_fill = has_fill;
            token.fill = fill;
            tokens_value[token.token_id] = token;
            tokens_created++;
            tokens.push_back(token);
        }
    };
    for (const auto &entry : plan.hits)
        emit_tokens(entry.first, entry.second, false,
                    mesh_abi::WeightFillKey(),
                    mesh_abi::kCacheResidencyOutcomeHIT);
    for (const auto &entry : plan.attached) {
        MoeCacheObligation &obligation = obligations_value.at(entry.second);
        subscribe(obligation, entry.first.weight_tag_index);
        emit_tokens(entry.first, obligation.slot_id, true, obligation.key,
                    mesh_abi::kCacheResidencyOutcomeATTACH);
    }
    for (const auto &fill : plan.fills) {
        mesh_abi::WeightFillKey key;
        key.core_id = fill.base.core_id;
        key.cache_partition_id = fill.base.cache_partition_id;
        key.weight_tag_index = fill.base.weight_tag_index;
        key.cache_generation = fill.base.cache_generation;
        key.fill_incarnation = incarnation++;
        MoeCacheSlot &slot = slots_value[fill.slot_id];
        slot.state = fill.victim ? kCacheSlotEvictingReserved
                                 : kCacheSlotFreeReserved;
        slot.bound_fill = key;
        slot.bound = true;
        slot.weight_tag_index = fill.base.weight_tag_index;
        slot.valid_bytes = 0;
        mshr_free--;
        obligation_free--;
        if (fill.victim)
            eviction_free--;
        MoeCacheObligation obligation;
        obligation.key = key;
        obligation.slot_id = fill.slot_id;
        obligation.opened_tick = tick;
        obligation.consumed_eviction = fill.victim;
        subscribe(obligation, fill.base.weight_tag_index);
        obligations_value[fill.base.weight_tag_index] = obligation;
        emit_tokens(fill.base, fill.slot_id, true, key,
                    mesh_abi::kCacheResidencyOutcomeNEW_FILL);
    }
    next_use_epoch = epoch;
    next_fill_incarnation = incarnation;
    return tokens;
}

MoeWeightCache::ReserveStatus
MoeWeightCache::reserve(uint64_t batch_id, uint32_t layer_id,
                        const std::vector<uint32_t> &tag_indices,
                        uint64_t tick, std::vector<MoeCacheToken> &tokens)
{
    const ReservePlan plan = prepare(batch_id, layer_id, tag_indices, tick);
    if (plan.status != ReserveStatus::COMMITTED)
        return plan.status;
    tokens = commit(plan, tick);
    return ReserveStatus::COMMITTED;
}

void
MoeWeightCache::markFilling(const mesh_abi::WeightFillKey &key)
{
    auto obligation = obligations_value.find(key.weight_tag_index);
    if (obligation == obligations_value.end())
        fatal("fill without an active obligation");
    MoeCacheSlot &slot = slots_value[obligation->second.slot_id];
    if (slot.state != kCacheSlotFreeReserved &&
        slot.state != kCacheSlotEvictingReserved)
        fatal("slot is not reserved");
    slot.state = kCacheSlotFilling;
    slot.valid_bytes = 0;
    obligation->second.eligible_engine_edge = engine_edge + 1;
}

void
MoeWeightCache::advanceCacheEdge()
{
    cache_edge++;
    std::vector<mesh_abi::WeightFillKey> pending;
    for (const auto &entry : obligations_value) {
        const MoeCacheSlot &slot = slots_value[entry.second.slot_id];
        if (slot.state == kCacheSlotFreeReserved ||
            slot.state == kCacheSlotEvictingReserved)
            pending.push_back(entry.second.key);
    }
    for (const auto &key : pending)
        markFilling(key);
}

void
MoeWeightCache::advanceEngineEdge()
{
    engine_edge++;
}

std::vector<mesh_abi::WeightFillKey>
MoeWeightCache::dmaEligible() const
{
    std::vector<mesh_abi::WeightFillKey> eligible;
    for (const auto &entry : obligations_value) {
        const MoeCacheObligation &obligation = entry.second;
        if (obligation.state != mesh_abi::kCacheObligationStateRESERVED)
            continue;
        if (obligation.eligible_engine_edge > engine_edge)
            continue;
        if (slots_value[obligation.slot_id].state != kCacheSlotFilling)
            continue;
        eligible.push_back(obligation.key);
    }
    std::sort(eligible.begin(), eligible.end(),
              [](const mesh_abi::WeightFillKey &left,
                 const mesh_abi::WeightFillKey &right) {
                  return weightFillKeyLess(left, right);
              });
    return eligible;
}

void
MoeWeightCache::markIssued(const mesh_abi::WeightFillKey &key)
{
    auto obligation = obligations_value.find(key.weight_tag_index);
    if (obligation == obligations_value.end())
        fatal("fill has no active obligation");
    bool eligible = false;
    for (const auto &candidate : dmaEligible())
        if (candidate.fill_incarnation == key.fill_incarnation)
            eligible = true;
    if (!eligible)
        fatal("fill is not eligible on this engine edge");
    obligation->second.state = mesh_abi::kCacheObligationStateISSUING;
}

void
MoeWeightCache::markInFlight(const mesh_abi::WeightFillKey &key)
{
    auto obligation = obligations_value.find(key.weight_tag_index);
    if (obligation == obligations_value.end() ||
        obligation->second.state != mesh_abi::kCacheObligationStateISSUING)
        fatal("fill was never issued");
    obligation->second.state = mesh_abi::kCacheObligationStateIN_FLIGHT;
}

void
MoeWeightCache::noteFillSuccess(const mesh_abi::WeightFillKey &key,
                                uint64_t valid_bytes,
                                uint64_t completion_tick)
{
    auto obligation = obligations_value.find(key.weight_tag_index);
    if (obligation == obligations_value.end())
        fatal("fill has no active obligation");
    if (valid_bytes > config.slot_bytes)
        fatal("fill exceeds the slot size");
    MoeCacheObligation &entry = obligation->second;
    if (entry.state == mesh_abi::kCacheObligationStateFAILED_DRAINING) {
        entry.drained_bytes = valid_bytes;
        return;
    }
    MoeCacheSlot &slot = slots_value[entry.slot_id];
    slot.state = kCacheSlotValid;
    slot.valid_bytes = valid_bytes;
    slot.has_epoch = true;
    mshr_free++;
    if (entry.consumed_eviction) {
        eviction_free++;
        entry.consumed_eviction = false;
    }
    entry.completed = true;
    FillLogEntry logged;
    logged.key = key;
    logged.slot_id = entry.slot_id;
    logged.address = slotBase(entry.slot_id);
    logged.bytes = valid_bytes;
    logged.committed_bytes = valid_bytes;
    logged.done_tick = completion_tick;
    fill_log.push_back(logged);
    for (auto &subscriber : entry.subscribers)
        if (subscriber.second.state ==
            mesh_abi::kCacheSubscriberStateWAITING) {
            subscriber.second.state = mesh_abi::kCacheSubscriberStateWOKEN;
            woken_marked++;
        }
    if (!entry.subscribers.empty() && !batch_faults.empty())
        faulted_fill_terminals++;
    releaseFaultedBatches();
    retireIfSettled(entry);
}

void
MoeWeightCache::noteFillFailure(const mesh_abi::WeightFillKey &key,
                                uint16_t site, uint64_t tick,
                                const mesh_abi::ErrorSourceKey &source)
{
    auto obligation = obligations_value.find(key.weight_tag_index);
    if (obligation == obligations_value.end())
        fatal("fill has no active obligation");
    const char *detail = weightFillFailureDetail(site);
    if (detail == nullptr)
        fatal("failure site is outside the detail closure");
    MoeCacheObligation &entry = obligation->second;
    if (!entry.has_first_error || tick < entry.first_error_tick) {
        entry.has_first_error = true;
        entry.first_error_tick = tick;
        entry.first_error_source = source;
        entry.first_error_code = detail;
    }
    entry.state = mesh_abi::kCacheObligationStateFAILED_DRAINING;
    MoeCacheSlot &slot = slots_value[entry.slot_id];
    slot.state = kCacheSlotErrorHeld;
    slot.valid_bytes = 0;
    for (auto &subscriber : entry.subscribers)
        if (subscriber.second.state ==
            mesh_abi::kCacheSubscriberStateWAITING)
            subscriber.second.state =
                mesh_abi::kCacheSubscriberStateFAIL_NOTIFIED;
}

void
MoeWeightCache::releaseFaultedBatches()
{
    if (batch_faults.empty())
        return;
    std::vector<uint64_t> batches;
    for (const auto &entry : batch_faults)
        batches.push_back(entry.first);
    for (uint64_t batch_id : batches)
        releaseBatchTokens(batch_id);
}

bool
MoeWeightCache::subscriberTombstoned(const MoeCacheToken &token) const
{
    const auto obligation = obligations_value.find(token.fill.weight_tag_index);
    if (obligation == obligations_value.end())
        return true;
    const auto subscriber = obligation->second.subscribers.find(
        {token.batch_id, token.layer_id});
    if (subscriber == obligation->second.subscribers.end())
        return true;
    return subscriber->second.state ==
           mesh_abi::kCacheSubscriberStateTERMINAL_TOMBSTONED;
}

bool
MoeWeightCache::fillTerminal(const MoeCacheToken &token) const
{
    const auto obligation = obligations_value.find(token.fill.weight_tag_index);
    if (obligation == obligations_value.end())
        return true;
    if (obligation->second.completed)
        return true;
    return obligation->second.state ==
               mesh_abi::kCacheObligationStateFAILED_DRAINING ||
           obligation->second.state ==
               mesh_abi::kCacheObligationStateFAILED_RETIRED;
}

bool
MoeWeightCache::releaseTokenIfOwned(uint64_t token_id)
{
    const auto entry = tokens_value.find(token_id);
    if (entry == tokens_value.end() || entry->second.released)
        return false;
    const MoeCacheToken &token = entry->second;
    const auto fault = batch_faults.find(token.batch_id);
    // A faulted batch releases its whole token set through the join; a
    // healthy batch holds each pin until its last consumer drains.
    if (fault == batch_faults.end() && token.consumers > 0)
        return false;
    if (fault != batch_faults.end()) {
        if (!fault->second.fanout_done)
            return false;
        if (fault->second.started && !fault->second.owned_drained)
            return false;
        if (token.has_fill && subscriberTombstoned(token) &&
            !fillTerminal(token))
            return false;
    }
    releaseToken(token_id);
    return true;
}

void
MoeWeightCache::releaseBatchTokens(uint64_t batch_id)
{
    std::vector<uint64_t> pending;
    for (const auto &entry : tokens_value)
        if (entry.second.batch_id == batch_id && !entry.second.released)
            pending.push_back(entry.first);
    std::sort(pending.begin(), pending.end());
    for (uint64_t token_id : pending)
        releaseTokenIfOwned(token_id);
}

void
MoeWeightCache::noteInstanceFault(uint64_t batch_id, uint64_t tick)
{
    MoeBatchFault &fault = batch_faults[batch_id];
    fault.started = true;
    fault.tick = tick;
    for (auto &entry : obligations_value) {
        for (auto &subscriber : entry.second.subscribers) {
            if (subscriber.first.first != batch_id)
                continue;
            if (subscriber.second.state ==
                mesh_abi::kCacheSubscriberStateWAITING) {
                subscriber.second.state =
                    mesh_abi::kCacheSubscriberStateTERMINAL_TOMBSTONED;
                tombstones_marked++;
            }
        }
    }
}

void
MoeWeightCache::noteFailureFanoutDone(uint64_t batch_id)
{
    batch_faults[batch_id].fanout_done = true;
    releaseBatchTokens(batch_id);
}

void
MoeWeightCache::noteOwnedWorkDrained(uint64_t batch_id)
{
    batch_faults[batch_id].owned_drained = true;
    releaseBatchTokens(batch_id);
}

void
MoeWeightCache::setTokenConsumers(uint64_t token_id, uint32_t consumers)
{
    const auto entry = tokens_value.find(token_id);
    fatal_if(entry == tokens_value.end(),
             "consumer registration for an unknown token");
    entry->second.consumers = consumers;
}

uint32_t
MoeWeightCache::tokenConsumerCount(uint64_t token_id) const
{
    const auto entry = tokens_value.find(token_id);
    return entry == tokens_value.end() ? 0 : entry->second.consumers;
}

uint32_t
MoeWeightCache::subscribersForTag(uint64_t batch_id,
                                  uint32_t tag_index) const
{
    uint32_t count = 0;
    for (const auto &entry : tokens_value)
        if (entry.second.batch_id == batch_id &&
            entry.second.base.weight_tag_index == tag_index)
            count++;
    return count;
}

void
MoeWeightCache::noteConsumerDrained(uint64_t batch_id, uint32_t layer_id,
                                    uint32_t tag_index)
{
    std::vector<uint64_t> pending;
    for (const auto &entry : tokens_value) {
        const MoeCacheToken &token = entry.second;
        if (token.released || token.batch_id != batch_id)
            continue;
        if (token.layer_id != layer_id)
            continue;
        if (token.base.weight_tag_index != tag_index)
            continue;
        pending.push_back(entry.first);
    }
    std::sort(pending.begin(), pending.end());
    for (uint64_t token_id : pending) {
        MoeCacheToken &token = tokens_value[token_id];
        if (token.consumers > 0)
            token.consumers--;
        releaseTokenIfOwned(token_id);
    }
}

bool
MoeWeightCache::tokenReleased(uint64_t token_id) const
{
    const auto entry = tokens_value.find(token_id);
    return entry == tokens_value.end() || entry->second.released;
}

void
MoeWeightCache::retireIfSettled(MoeCacheObligation &obligation)
{
    const auto live = obligations_value.find(obligation.key.weight_tag_index);
    if (live == obligations_value.end() || &live->second != &obligation)
        return;
    for (const auto &subscriber : obligation.subscribers)
        if (subscriber.second.state !=
            mesh_abi::kCacheSubscriberStateRELEASED)
            return;
    if (obligation.state == mesh_abi::kCacheObligationStateFAILED_DRAINING) {
        if (!obligation.has_first_error)
            fatal("failed fill has no first error");
        tombstoned_tags.insert(obligation.key.weight_tag_index);
        failure_detail[obligation.key.weight_tag_index] =
            obligation.first_error_code;
        MoeCacheSlot &slot = slots_value[obligation.slot_id];
        slot.state = kCacheSlotFree;
        slot.weight_tag_index = 0;
        slot.valid_bytes = 0;
        slot.bound = false;
        slot.has_epoch = false;
        mshr_free++;
        if (obligation.consumed_eviction) {
            eviction_free++;
            obligation.consumed_eviction = false;
        }
        obligation_free++;
    } else if (obligation.completed) {
        obligation_free++;
    } else {
        return;
    }
    obligations_value.erase(obligation.key.weight_tag_index);
}

void
MoeWeightCache::releaseToken(uint64_t token_id)
{
    auto entry = tokens_value.find(token_id);
    if (entry == tokens_value.end() || entry->second.released)
        return;
    MoeCacheToken &token = entry->second;
    token.released = true;
    token_releases++;
    if (token.pinned) {
        token.pinned = false;
        slots_value[token.slot_id].pins--;
    }
    if (!token.has_fill)
        return;
    auto obligation = obligations_value.find(token.fill.weight_tag_index);
    if (obligation == obligations_value.end())
        return;
    const auto subscriber = obligation->second.subscribers.find(
        {token.batch_id, token.layer_id});
    if (subscriber != obligation->second.subscribers.end()) {
        subscriber_free++;
        if (subscriber->second.state !=
            mesh_abi::kCacheSubscriberStateWAITING)
            subscriber->second.state =
                mesh_abi::kCacheSubscriberStateRELEASED;
    }
    retireIfSettled(obligation->second);
}

void
MoeWeightCache::cancelMember(uint64_t batch_id)
{
    cancelled_members.insert(batch_id);
}

void
MoeWeightCache::abortBeforeStart(uint64_t batch_id)
{
    MoeBatchFault &fault = batch_faults[batch_id];
    fault.started = false;
    fault.owned_drained = true;
    std::vector<uint64_t> pending;
    for (const auto &entry : tokens_value)
        if (entry.second.batch_id == batch_id && !entry.second.released)
            pending.push_back(entry.first);
    std::sort(pending.begin(), pending.end());
    for (uint64_t token_id : pending) {
        MoeCacheToken &token = tokens_value[token_id];
        if (token.has_fill &&
            token.outcome != mesh_abi::kCacheResidencyOutcomeHIT) {
            const auto obligation =
                obligations_value.find(token.fill.weight_tag_index);
            if (obligation != obligations_value.end()) {
                auto subscriber = obligation->second.subscribers.find(
                    {token.batch_id, token.layer_id});
                if (subscriber != obligation->second.subscribers.end() &&
                    subscriber->second.state ==
                        mesh_abi::kCacheSubscriberStateWAITING) {
                    subscriber->second.state =
                        mesh_abi::kCacheSubscriberStateTERMINAL_TOMBSTONED;
                    tombstones_marked++;
                }
            }
        }
    }
    releaseBatchTokens(batch_id);
}

std::string
MoeWeightCache::snapshotJson() const
{
    for (const auto &slot : slots_value)
        if (slot.state != kCacheSlotFree && slot.state != kCacheSlotValid)
            fatal("only a quiescent cache can be serialized");
    for (const auto &entry : obligations_value)
        if (!entry.second.subscribers.empty())
            fatal("active subscribers cannot be serialized");
    std::vector<const MoeCacheSlot *> valid;
    for (const auto &slot : slots_value)
        if (slot.state == kCacheSlotValid)
            valid.push_back(&slot);
    std::sort(valid.begin(), valid.end(),
              [](const MoeCacheSlot *left, const MoeCacheSlot *right) {
                  return std::make_pair(left->last_use_epoch,
                                        left->weight_tag_index) <
                         std::make_pair(right->last_use_epoch,
                                        right->weight_tag_index);
              });
    std::map<uint32_t, uint32_t> ranks;
    for (uint32_t rank = 0; rank < valid.size(); rank++)
        ranks[valid[rank]->slot_id] = rank;
    std::string out = "{\"core_id\":" + u64Text(config.core_id) +
                      ",\"cache_generation\":" +
                      u64Text(cache_generation) +
                      ",\"next_fill_incarnation\":" +
                      u64Text(next_fill_incarnation) + ",\"slots\":[";
    bool first = true;
    for (const auto &slot : slots_value) {
        if (!first)
            out += ",";
        first = false;
        const bool is_valid = slot.state == kCacheSlotValid;
        out += "{\"slot_id\":" + u64Text(slot.slot_id) + ",\"state\":" +
               (is_valid ? "1" : "0") + ",\"weight_tag_index\":";
        out += is_valid ? u64Text(slot.weight_tag_index) : "null";
        out += ",\"valid_bytes\":" +
               u64Text(is_valid ? slot.valid_bytes : 0) + ",\"lru_rank\":";
        out += is_valid ? u64Text(ranks[slot.slot_id]) : "null";
        out += "}";
    }
    out += "],\"failure_tombstones\":[";
    first = true;
    for (uint32_t tag : tombstoned_tags) {
        if (!first)
            out += ",";
        first = false;
        out += "{\"weight_tag_index\":" + u64Text(tag) + "}";
    }
    out += "]}";
    return out;
}

} // namespace ai_mesh
} // namespace gem5

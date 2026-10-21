#include "dev/ai_mesh/mesh_weight_cache.hh"

#include <iomanip>
#include <sstream>

#include "base/logging.hh"

namespace gem5::ai_mesh
{
bool MoeWeightCache::quiescent() const
{
    return obligations_value.empty() && liveTokens() == 0 &&
        mshr_free == config.mshr_slots && eviction_free == config.eviction_slots &&
        obligation_free == config.obligation_slots && subscriber_free == config.subscriber_slots &&
        std::all_of(slots_value.begin(), slots_value.end(), [](const auto &slot) {
            return !slot.pins && (slot.state == kCacheSlotFree || slot.state == kCacheSlotValid);
        });
}

bool MoeWeightCache::validateReplay(const ReplayState &state, std::string &reason) const
{
    const auto reject = [&](const char *message) { reason = message; return false; };
    if (state.core_id != config.core_id || state.slots.size() != config.slot_count ||
        state.next_fill_incarnation == 0 || state.cache_generation != cache_generation)
        return reject("cache replay identity or geometry differs from its owner");
    std::set<uint32_t> tags, ranks;
    for (const auto &slot : state.slots) {
        if (!slot.weight_tag_index) {
            if (slot.valid_bytes || slot.lru_rank)
                return reject("invalid cache slot carries live metadata");
            continue;
        }
        if (!cacheable_tags.count(*slot.weight_tag_index) || !slot.valid_bytes ||
            slot.valid_bytes > config.slot_bytes || !slot.lru_rank ||
            !tags.insert(*slot.weight_tag_index).second || !ranks.insert(*slot.lru_rank).second)
            return reject("valid cache slot has invalid tag, bytes or LRU rank");
    }
    uint32_t ordinal = 0;
    for (const auto rank : ranks)
        if (rank != ordinal++)
            return reject("cache LRU ranks must be dense");
    for (const auto &[tag, failure] : state.failure_tombstones) {
        const auto &source = failure.source;
        bool detail = false;
        for (const auto site : {mesh_abi::kWeightFillFailureSiteCACHE_FILL_AXI_R,
             mesh_abi::kWeightFillFailureSiteCACHE_FILL_SRAM_BOUNDS,
             mesh_abi::kWeightFillFailureSiteCACHE_FILL_SRAM_COMMIT,
             mesh_abi::kWeightFillFailureSiteCACHE_FILL_SOURCE_VALIDITY})
            detail |= uint32_t(*weightFillFailureDetail(site)) == failure.code;
        if (!cacheable_tags.count(tag) || tags.count(tag) || !detail ||
            !validErrorSource(source) || source.domain != mesh_abi::kMeshObjectDomainWEIGHT_FILL ||
            source.core_id_or_ffff != state.core_id ||
            agent_abi::rdU16(source.aux_key.data() + 2) != mesh_abi::kSramPartitionKindWEIGHT_CACHE ||
            agent_abi::rdU32(source.aux_key.data() + 4) != tag ||
            agent_abi::rdU32(source.aux_key.data() + 8) != state.cache_generation ||
            agent_abi::rdU32(source.aux_key.data() + 12) >= state.next_fill_incarnation)
            return reject("cache failure tombstone has invalid provenance");
    }
    reason.clear();
    return true;
}

std::optional<MoeWeightCache::ReplayState>
MoeWeightCache::replayState(std::string &reason) const
{
    if (!quiescent()) {
        reason = "cache has live owners or work";
        return std::nullopt;
    }
    ReplayState state;
    state.core_id = config.core_id;
    state.cache_generation = cache_generation;
    state.next_fill_incarnation = next_fill_incarnation;
    state.slots.resize(slots_value.size());
    state.failure_tombstones = failure_records;
    std::vector<const MoeCacheSlot *> valid;
    for (const auto &slot : slots_value)
        if (slot.state == kCacheSlotValid)
            valid.push_back(&slot);
    std::sort(valid.begin(), valid.end(), [](const auto *left, const auto *right) {
        return std::tie(left->last_use_epoch, left->weight_tag_index) <
               std::tie(right->last_use_epoch, right->weight_tag_index);
    });
    for (uint32_t rank = 0; rank < valid.size(); ++rank) {
        const auto &slot = *valid[rank];
        state.slots[slot.slot_id] = {slot.weight_tag_index, slot.valid_bytes, rank};
    }
    if (!validateReplay(state, reason))
        return std::nullopt;
    return state;
}

bool MoeWeightCache::installReplay(const ReplayState &state, std::string &reason)
{
    if (!quiescent()) {
        reason = "cache replay requires a quiescent owner";
        return false;
    }
    if (!validateReplay(state, reason))
        return false;
    if (commit_epoch == UINT64_MAX) {
        reason = "cache commit epoch exhausted";
        return false;
    }
    std::vector<MoeCacheSlot> slots(state.slots.size());
    auto failures = state.failure_tombstones;
    uint64_t epoch = 0;
    for (uint32_t index = 0; index < slots.size(); ++index) {
        auto &slot = slots[index];
        const auto &input = state.slots[index];
        slot.slot_id = index;
        if (!input.weight_tag_index)
            continue;
        slot.state = kCacheSlotValid;
        slot.weight_tag_index = *input.weight_tag_index;
        slot.valid_bytes = input.valid_bytes;
        slot.last_use_epoch = *input.lru_rank;
        slot.has_epoch = true;
        ++epoch;
    }
    slots_value.swap(slots);
    failure_records.swap(failures);
    cache_generation = state.cache_generation;
    next_fill_incarnation = state.next_fill_incarnation;
    next_use_epoch = epoch;
    ++commit_epoch;
    tokens_value.clear();
    cancelled_members.clear();
    batch_faults.clear();
    reason.clear();
    return true;
}

std::string MoeWeightCache::snapshotJson() const
{
    std::string reason;
    const auto state = replayState(reason);
    fatal_if(!state, "%s", reason);
    std::ostringstream out;
    out << "{\"core_id\":" << state->core_id << ",\"cache_generation\":" << state->cache_generation
        << ",\"next_fill_incarnation\":" << state->next_fill_incarnation << ",\"slots\":[";
    for (size_t index = 0; index < state->slots.size(); ++index) {
        if (index) out << ',';
        const auto &slot = state->slots[index];
        out << "{\"slot_id\":" << index << ",\"state\":" << bool(slot.weight_tag_index)
            << ",\"weight_tag_index\":";
        if (slot.weight_tag_index) out << *slot.weight_tag_index; else out << "null";
        out << ",\"valid_bytes\":" << slot.valid_bytes << ",\"lru_rank\":";
        if (slot.lru_rank) out << *slot.lru_rank; else out << "null";
        out << '}';
    }
    out << "],\"failure_tombstones\":[";
    bool first = true;
    for (const auto &[tag, failure] : state->failure_tombstones) {
        if (!first) out << ',';
        first = false;
        out << "{\"weight_tag_index\":" << tag << ",\"first_error\":{\"tick\":";
        if (failure.tick <= (uint64_t(1) << 53) - 1)
            out << failure.tick;
        else
            out << "\"0x" << std::hex << std::setfill('0') << std::setw(16) << failure.tick << std::dec << '"';
        out << ",\"error_code\":" << failure.code << ",\"source_key_wire\":\"";
        for (const auto byte : errorSourceKeyBytes(failure.source))
            out << std::hex << std::setfill('0') << std::setw(2) << unsigned(byte);
        out << std::dec << "\"}}";
    }
    out << "]}";
    return out.str();
}
}

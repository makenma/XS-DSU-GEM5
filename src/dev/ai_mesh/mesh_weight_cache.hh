#ifndef DEV_AI_MESH_MESH_WEIGHT_CACHE_HH
#define DEV_AI_MESH_MESH_WEIGHT_CACHE_HH

#include <algorithm>
#include <array>
#include <map>
#include <set>
#include <utility>
#include <vector>
#include <cstdint>
#include <string>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

// Behaviour over the ABI-generated weight cache key records (main contract
// 7.8.2): the field layout has one definition source in the ABI registry,
// and both languages order keys numerically instead of comparing wire bytes.
bool weightCacheBaseKeyLess(const mesh_abi::WeightCacheBaseKey &lhs,
                            const mesh_abi::WeightCacheBaseKey &rhs);

bool weightFillKeyLess(const mesh_abi::WeightFillKey &lhs,
                       const mesh_abi::WeightFillKey &rhs);

std::array<uint8_t, mesh_abi::kWeightFillKeysBytes>
weightFillKeyBytes(const mesh_abi::WeightFillKey &key);

std::array<uint8_t, mesh_abi::kCacheDmaDescriptorKeysBytes>
cacheDmaDescriptorKeyBytes(const mesh_abi::WeightFillKey &key,
                           uint32_t segment_ordinal);

std::string weightFillTrafficId(const mesh_abi::WeightFillKey &key);

std::array<uint8_t, mesh_abi::kErrorSourceKeysBytes>
errorSourceKeyBytes(const mesh_abi::ErrorSourceKey &key);

mesh_abi::ErrorSourceKey weightFillSource(const mesh_abi::WeightFillKey &key);

// Failure sites map to the frozen detail-code closure (contract 8.7.5); the
// table lives in one place so a tombstone reader cannot invent a code.
const char *weightFillFailureDetail(uint16_t site);

// Runtime-only slot states (the replay artifact only carries INVALID/VALID).
enum MoeCacheSlotState : uint32_t
{
    kCacheSlotFree = 0,
    kCacheSlotFreeReserved = 1,
    kCacheSlotFilling = 2,
    kCacheSlotEvictingReserved = 3,
    kCacheSlotValid = 4,
    kCacheSlotErrorHeld = 5,
};

struct MoeCacheSlot
{
    uint32_t slot_id = 0;
    uint32_t state = kCacheSlotFree;
    uint32_t weight_tag_index = 0;
    uint64_t valid_bytes = 0;
    uint64_t last_use_epoch = 0;
    bool has_epoch = false;
    uint32_t pins = 0;
    mesh_abi::WeightFillKey bound_fill;
    bool bound = false;
};

struct MoeCacheSubscriber
{
    uint64_t batch_id = 0;
    uint32_t layer_id = 0;
    uint32_t state = mesh_abi::kCacheSubscriberStateWAITING;
};

struct MoeCacheObligation
{
    mesh_abi::WeightFillKey key;
    uint32_t state = mesh_abi::kCacheObligationStateRESERVED;
    uint32_t slot_id = 0;
    uint64_t opened_tick = 0;
    uint64_t eligible_engine_edge = 0;
    bool consumed_eviction = false;
    bool completed = false;
    std::map<std::pair<uint64_t, uint32_t>, MoeCacheSubscriber> subscribers;
    bool has_first_error = false;
    uint64_t drained_bytes = 0;
    uint64_t first_error_tick = 0;
    mesh_abi::ErrorSourceKey first_error_source;
    std::string first_error_code;
};

struct MoeCacheToken
{
    uint64_t token_id = 0;
    uint64_t batch_id = 0;
    mesh_abi::WeightCacheBaseKey base;
    uint32_t outcome = 0;
    uint32_t slot_id = 0;
    bool has_fill = false;
    mesh_abi::WeightFillKey fill;
    bool pinned = true;
    bool released = false;
    // Occurrence ownership: every layer of the owning batch that reads this
    // line, with the number of its expert commands that still have to drain.
    // One physical token/pin/fill serves all of them.
    // One occurrence of the batch: the token owns its own pin and is released
    // when this layer's consumers drain, independent of every other layer that
    // shares the same physical fill.
    uint32_t layer_id = 0;
    uint32_t consumers = 0;
};

// One batch's fault join: a started instance releases its tokens only after
// the fill terminal, the instance-owned work drain and the failure fanout all
// happened, in either order; a prestart abort needs the fanout only.
struct MoeBatchFault
{
    bool started = true;
    bool fanout_done = false;
    bool owned_drained = false;
    uint64_t tick = 0;
};

// Runtime weight cache coordinator (main contract 7.8, 8.7).  The Python
// coordinator is the offline oracle for the same state machine; both order
// keys numerically and share the ABI-generated key layout.
class MoeWeightCache
{
  public:
    struct Config
    {
        uint16_t core_id = 0;
        uint32_t slot_count = 0;
        uint64_t slot_bytes = 0;
        uint32_t mshr_slots = 0;
        uint32_t eviction_slots = 0;
        uint32_t obligation_slots = 0;
        uint32_t subscriber_slots = 0;
        uint64_t partition_base = 0;
        std::vector<uint32_t> cacheable_tags;
    };

    enum class ReserveStatus
    {
        COMMITTED = 0,
        RESOURCE_WAIT = 1,
        FAILED = 2,
    };

    struct PendingFill
    {
        mesh_abi::WeightCacheBaseKey base;
        uint32_t slot_id = 0;
        bool victim = false;
    };

    // Side-effect-free reservation shadow: prepare() only reads live state, so
    // a coordinator can preflight every core of a batch and either commit all
    // plans or drop them without leaving a single visible change.
    struct LayerTags
    {
        uint32_t layer_id = 0;
        std::vector<uint32_t> tags;
    };

    struct ReservePlan
    {
        ReserveStatus status = ReserveStatus::RESOURCE_WAIT;
        uint64_t batch_id = 0;
        // Per referenced tag, the sorted layers of the batch that read it: one
        // physical key serves every occurrence, so the plan carries them all.
        std::map<uint32_t, std::vector<uint32_t>> layers;
        std::vector<std::pair<mesh_abi::WeightCacheBaseKey, uint32_t>> hits;
        std::vector<std::pair<mesh_abi::WeightCacheBaseKey, uint32_t>>
            attached;
        std::vector<PendingFill> fills;
        uint32_t first_incarnation = 0;
        uint64_t first_epoch = 0;
        // Cache commit epoch the shadow was taken from: a plan can only be
        // applied while no other commit happened in between.
        uint64_t commit_epoch = 0;
    };

    explicit MoeWeightCache(const Config &config);

    ReservePlan prepare(uint64_t batch_id,
                        const std::vector<LayerTags> &demand,
                        uint64_t tick) const;
    ReservePlan prepare(uint64_t batch_id, uint32_t layer_id,
                        const std::vector<uint32_t> &tag_indices,
                        uint64_t tick) const;
    std::vector<MoeCacheToken> commit(const ReservePlan &plan, uint64_t tick);
    ReserveStatus reserve(uint64_t batch_id, uint32_t layer_id,
                          const std::vector<uint32_t> &tag_indices,
                          uint64_t tick, std::vector<MoeCacheToken> &tokens);

    void advanceCacheEdge();
    void advanceEngineEdge();
    std::vector<mesh_abi::WeightFillKey> dmaEligible() const;
    void markFilling(const mesh_abi::WeightFillKey &key);
    void markIssued(const mesh_abi::WeightFillKey &key);
    void markInFlight(const mesh_abi::WeightFillKey &key);
    void noteFillSuccess(const mesh_abi::WeightFillKey &key,
                         uint64_t valid_bytes, uint64_t completion_tick);
    void noteFillFailure(const mesh_abi::WeightFillKey &key, uint16_t site,
                         uint64_t tick,
                         const mesh_abi::ErrorSourceKey &source);
    void cancelMember(uint64_t batch_id);
    void abortBeforeStart(uint64_t batch_id);
    void noteInstanceFault(uint64_t batch_id, uint64_t tick);
    void noteFailureFanoutDone(uint64_t batch_id);
    void noteOwnedWorkDrained(uint64_t batch_id);
    void setTokenConsumers(uint64_t token_id, uint32_t consumers);
    void noteConsumerDrained(uint64_t batch_id, uint32_t layer_id,
                             uint32_t tag_index);
    uint32_t tokenConsumerCount(uint64_t token_id) const;
    uint32_t subscribersForTag(uint64_t batch_id, uint32_t tag_index) const;
    void releaseBatchTokens(uint64_t batch_id);
    void releaseToken(uint64_t token_id);
    bool tokenReleased(uint64_t token_id) const;
    uint32_t liveTokensOfBatch(uint64_t batch_id) const
    {
        uint32_t live = 0;
        for (const auto &entry : tokens_value)
            if (entry.second.batch_id == batch_id && !entry.second.released)
                live++;
        return live;
    }
    uint32_t subscribersInState(uint32_t state) const
    {
        uint32_t count = 0;
        for (const auto &entry : obligations_value)
            for (const auto &subscriber : entry.second.subscribers)
                if (subscriber.second.state == state)
                    count++;
        return count;
    }
    bool hasSubscriber(uint64_t batch_id, uint32_t layer_id) const
    {
        for (const auto &entry : obligations_value)
            if (entry.second.subscribers.count({batch_id, layer_id}) != 0)
                return true;
        return false;
    }

    uint64_t slotBase(uint32_t slot_id) const;
    mesh_abi::WeightCacheBaseKey baseKey(uint32_t tag_index) const;
    bool hasTombstone(const mesh_abi::WeightCacheBaseKey &key) const;
    const std::vector<MoeCacheSlot> &slots() const { return slots_value; }
    const std::map<uint32_t, MoeCacheObligation> &obligations() const
    {
        return obligations_value;
    }
    uint32_t nextFillIncarnation() const { return next_fill_incarnation; }
    uint64_t nextUseEpoch() const { return next_use_epoch; }
    uint32_t cacheGeneration() const { return cache_generation; }
    struct FillLogEntry
    {
        mesh_abi::WeightFillKey key;
        uint32_t slot_id = 0;
        uint64_t address = 0;
        uint64_t bytes = 0;
        uint64_t committed_bytes = 0;
        uint64_t done_tick = 0;
    };
    const std::vector<FillLogEntry> &fillLog() const { return fill_log; }

    // True while any reservation subscriber is still WAITING for its fill, so
    // the cached policy can hold the overlay release until every weight line
    // is resident (contract 8.7.3).
    bool hasPendingSubscribers() const
    {
        for (const auto &entry : obligations_value)
            for (const auto &subscriber : entry.second.subscribers)
                if (subscriber.second.state ==
                    mesh_abi::kCacheSubscriberStateWAITING)
                    return true;
        return false;
    }
    // A batch's occurrence layers in one obligation, sorted and deduplicated.
    std::vector<uint32_t> layersOfTag(uint64_t batch_id,
                                      uint32_t tag_index) const
    {
        std::vector<uint32_t> layers;
        const auto entry = obligations_value.find(tag_index);
        if (entry == obligations_value.end())
            return layers;
        for (const auto &subscriber : entry->second.subscribers)
            if (subscriber.first.first == batch_id)
                layers.push_back(subscriber.first.second);
        std::sort(layers.begin(), layers.end());
        layers.erase(std::unique(layers.begin(), layers.end()), layers.end());
        return layers;
    }
    const std::set<uint32_t> &cacheableTags() const
    {
        return cacheable_tags;
    }
    uint32_t mshrFree() const { return mshr_free; }
    uint32_t evictionFree() const { return eviction_free; }
    uint32_t obligationFree() const { return obligation_free; }
    uint32_t subscriberFree() const { return subscriber_free; }
    uint32_t mshrSlots() const { return config.mshr_slots; }
    uint32_t evictionSlots() const { return config.eviction_slots; }
    uint32_t obligationSlots() const { return config.obligation_slots; }
    uint32_t subscriberSlots() const { return config.subscriber_slots; }
    uint32_t liveTokens() const
    {
        uint32_t live = 0;
        for (const auto &entry : tokens_value)
            if (!entry.second.released)
                live++;
        return live;
    }
    uint32_t pendingFills() const
    {
        uint32_t pending = 0;
        for (const auto &entry : obligations_value)
            if (entry.second.state ==
                    mesh_abi::kCacheObligationStateRESERVED ||
                entry.second.state == mesh_abi::kCacheObligationStateISSUING ||
                entry.second.state ==
                    mesh_abi::kCacheObligationStateIN_FLIGHT)
                pending++;
        return pending;
    }
    uint32_t pendingSubscribers() const
    {
        uint32_t pending = 0;
        for (const auto &entry : obligations_value)
            for (const auto &subscriber : entry.second.subscribers)
                if (subscriber.second.state ==
                    mesh_abi::kCacheSubscriberStateWAITING)
                    pending++;
        return pending;
    }
    uint32_t validLines() const
    {
        uint32_t valid = 0;
        for (const auto &slot : slots_value)
            if (slot.state == kCacheSlotValid)
                valid++;
        return valid;
    }
    uint32_t tombstoneCount() const
    {
        return uint32_t(failure_detail.size());
    }
    // Exactly-once lifecycle observability: every token is released once, and
    // a faulted batch's subscribers are tombstoned instead of woken.
    uint64_t tokensCreated() const { return tokens_created; }
    uint64_t tokenReleases() const { return token_releases; }
    uint64_t tombstonedSubscribers() const { return tombstones_marked; }
    uint64_t wokenSubscribers() const { return woken_marked; }
    uint64_t faultedFillTerminals() const { return faulted_fill_terminals; }

    // Canonical replay projection (contract 8.7.1); serialized as compact
    // JSON so a golden can pin the cross-language document byte for byte.
    std::string snapshotJson() const;

  private:
    uint32_t tagOf(const mesh_abi::WeightCacheBaseKey &key) const
    {
        return key.weight_tag_index;
    }
    MoeCacheSlot *slotOf(const mesh_abi::WeightCacheBaseKey &key);
    bool shadow(const std::vector<mesh_abi::WeightCacheBaseKey> &keys,
                uint32_t occurrences,
                std::vector<std::pair<mesh_abi::WeightCacheBaseKey,
                                      uint32_t>> &plan,
                std::vector<std::pair<mesh_abi::WeightCacheBaseKey,
                                      uint32_t>> &attached,
                std::vector<PendingFill> &fills,
                std::map<uint32_t, uint32_t> &victims,
                uint32_t &incarnation, uint64_t &next_epoch) const;
    void retireIfSettled(MoeCacheObligation &obligation);
    bool planIsCurrent(const ReservePlan &plan) const;
    void releaseFaultedBatches();
    bool releaseTokenIfOwned(uint64_t token_id);
    bool fillTerminal(const MoeCacheToken &token) const;
    bool subscriberTombstoned(const MoeCacheToken &token) const;

    Config config;
    std::vector<MoeCacheSlot> slots_value;
    std::map<uint32_t, MoeCacheObligation> obligations_value;
    std::map<uint64_t, MoeCacheToken> tokens_value;
    std::map<uint32_t, std::string> failure_detail;
    std::vector<FillLogEntry> fill_log;
    std::set<uint32_t> cacheable_tags;
    std::set<uint32_t> tombstoned_tags;
    std::set<uint64_t> cancelled_members;
    std::map<uint64_t, MoeBatchFault> batch_faults;
    uint64_t next_token_id = 1;
    uint64_t next_use_epoch = 0;
    uint32_t next_fill_incarnation = 1;
    uint32_t cache_generation = 0;
    uint64_t cache_edge = 1;
    uint64_t commit_epoch = 0;
    uint64_t engine_edge = 1;
    uint64_t tokens_created = 0;
    uint64_t token_releases = 0;
    uint64_t tombstones_marked = 0;
    uint64_t woken_marked = 0;
    uint64_t faulted_fill_terminals = 0;
    uint32_t mshr_free = 0;
    uint32_t eviction_free = 0;
    uint32_t obligation_free = 0;
    uint32_t subscriber_free = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

#include "dev/ai_mesh/cache_fill_edge.hh"

#include "base/logging.hh"

namespace gem5::ai_mesh
{
bool CacheFillEdge::stage(MoeWeightCache &cache, const mesh_abi::WeightFillKey &key,
                         uint64_t tick, std::optional<ErrorCandidate> failure,
                         std::optional<uint64_t> valid_bytes, bool descriptor_drained)
{
    if (failures_committed || (!failure && !valid_bytes && !descriptor_drained) ||
            (failure && failure->tick != tick) ||
            (!rows.empty() && rows.front().tick != tick))
        return false;
    const auto obligation = cache.obligations().find(key.weight_tag_index);
    if (obligation == cache.obligations().end() ||
            weightFillKeyBytes(obligation->second.key) != weightFillKeyBytes(key))
        return false;
    if (failure) {
        bool allowed = false;
        for (uint16_t site = 0; site < 32; ++site)
            if (const auto code = weightFillFailureDetail(site); code && *code == failure->code)
                allowed = true;
        if (!allowed)
            return false;
    }
    for (auto &row : rows) {
        if (row.cache != &cache || weightFillKeyBytes(row.key) != weightFillKeyBytes(key))
            continue;
        if (valid_bytes && row.valid_bytes && *valid_bytes != *row.valid_bytes)
            return false;
        if (failure && (!row.failure || errorCandidateLess(*failure, *row.failure)))
            row.failure = failure;
        if (valid_bytes)
            row.valid_bytes = valid_bytes;
        row.descriptor_drained |= descriptor_drained;
        return true;
    }
    if (rows.size() == limit)
        return false;
    rows.push_back({&cache, key, tick, failure, valid_bytes, descriptor_drained});
    return true;
}

std::vector<ErrorCandidate> CacheFillEdge::failures() const
{
    std::vector<ErrorCandidate> result;
    for (const auto &row : rows)
        if (row.failure) {
            auto first = row.cache->failure(row.key.weight_tag_index);
            fatal_if(!first, "cache failure has not been committed");
            first->tick = row.tick;
            result.push_back(*first);
        }
    return result;
}

void CacheFillEdge::commitFailures()
{
    fatal_if(failures_committed, "cache fill failures committed twice");
    std::sort(rows.begin(), rows.end(), [](const auto &a, const auto &b) {
        return weightFillKeyLess(a.key, b.key);
    });
    for (const auto &row : rows) {
        if (!row.failure)
            continue;
        for (uint16_t site = 0; site < 32; ++site) {
            if (const auto code = weightFillFailureDetail(site); code && *code == row.failure->code) {
                row.cache->noteFillFailure(row.key, site, row.tick, row.failure->source, false);
                break;
            }
        }
    }
    failures_committed = true;
}

void CacheFillEdge::commitSuccesses()
{
    fatal_if(!failures_committed, "cache success precedes failure reduction");
    for (const auto &row : rows) {
        if (!row.failure && row.valid_bytes && !row.cache->failure(row.key.weight_tag_index))
            row.cache->noteFillSuccess(row.key, *row.valid_bytes, row.tick);
        else if (row.descriptor_drained)
            row.cache->noteFillDrained(row.key);
    }
    rows.clear();
    failures_committed = false;
}
}

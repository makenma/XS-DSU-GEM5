#ifndef DEV_AI_MESH_CACHE_FILL_EDGE_HH
#define DEV_AI_MESH_CACHE_FILL_EDGE_HH

#include "dev/ai_mesh/mesh_weight_cache.hh"

namespace gem5::ai_mesh
{
class CacheFillEdge
{
  public:
    explicit CacheFillEdge(size_t capacity) : limit(capacity) { rows.reserve(capacity); }
    bool stage(MoeWeightCache &cache, const mesh_abi::WeightFillKey &key,
               uint64_t tick, std::optional<ErrorCandidate> failure,
               std::optional<uint64_t> valid_bytes, bool descriptor_drained);
    void commitFailures();
    void commitSuccesses();
    bool empty() const { return rows.empty(); }
    std::vector<ErrorCandidate> failures() const;

  private:
    struct Row
    {
        MoeWeightCache *cache;
        mesh_abi::WeightFillKey key;
        uint64_t tick;
        std::optional<ErrorCandidate> failure;
        std::optional<uint64_t> valid_bytes;
        bool descriptor_drained;
    };
    const size_t limit;
    std::vector<Row> rows;
    bool failures_committed = false;
};
}
#endif

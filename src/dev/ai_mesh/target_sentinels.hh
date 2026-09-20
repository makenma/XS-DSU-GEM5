#ifndef DEV_AI_MESH_TARGET_SENTINELS_HH
#define DEV_AI_MESH_TARGET_SENTINELS_HH

#include <cstdint>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_runtime_observations.hh"

namespace gem5
{

namespace axi
{
class AxiTargetAdapter;
}

namespace ai_mesh
{

// Sentinel spans of a real AXI target memory: declared as ``<address> <size>``
// lines, sampled before cycle 0 and re-read at drain.  They are the evidence
// that bytes a transfer must not touch (WSTRB-disabled lanes, row padding and
// the unaligned head and tail around a payload) kept their initial value.
class TargetSentinels
{
  public:
    void load(const std::string &path);
    // Read and remember every declared span; call once before any DMA runs.
    void sample(axi::AxiTargetAdapter &adapter);
    // Re-read every declared span and report its before/after digests.
    std::vector<SentinelRangeObservation>
    compare(axi::AxiTargetAdapter &adapter) const;
    bool empty() const { return rows.empty(); }

  private:
    struct Row
    {
        uint64_t address = 0;
        uint64_t size = 0;
        std::string before;
    };
    std::vector<Row> rows;
    bool sampled = false;
};

} // namespace ai_mesh
} // namespace gem5

#endif

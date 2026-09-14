#ifndef DEV_AI_MESH_MESH_MOE_GATE_HH
#define DEV_AI_MESH_MESH_MOE_GATE_HH

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

// Insertion gate of contract 7.2.2.  A participating core admits the static
// command that produces the region entry event (`insert_after`), then stops
// decoding at `resume_before` until the layer's overlay group exit has been
// published.  The gate owns no simulation state: it only answers whether a
// command index may be decoded right now.
class MoeInsertionGate
{
  public:
    struct RegionSpec
    {
        uint32_t layer_id = 0;
        uint32_t region_id = 0;
        uint32_t insert_after_command_id = 0;
        uint32_t resume_before_command_id = 0;
        uint32_t resume_before_index = 0;
    };

    enum class RegionPhase
    {
        IDLE = 0,
        ARMED = 1,
        REACHED = 2,
        RELEASED = 3,
    };

    // Registers one region of this core.  Re-registering the same layer is a
    // programming error: a core has exactly one region per layer.
    void arm(const RegionSpec &spec);

    // Called when the core successfully issues a static command.
    void noteIssued(uint32_t command_id);

    // Called by the dispatcher once the layer's overlay group exit is
    // published; the resume point becomes decodable again.
    void release(uint32_t layer_id);

    // True while the given command index must not be decoded.
    bool blockedIndex(uint32_t command_index) const;

    bool layerArmed(uint32_t layer_id) const;

    RegionPhase phase(uint32_t layer_id) const;

    bool allReleased() const;

    std::string describe() const;

  private:
    struct Region
    {
        RegionSpec spec;
        RegionPhase phase = RegionPhase::IDLE;
    };

    std::map<uint32_t, Region> regions;
    std::vector<uint32_t> order;
};

} // namespace ai_mesh
} // namespace gem5

#endif

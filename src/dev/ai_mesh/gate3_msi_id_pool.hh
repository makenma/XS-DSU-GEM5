#ifndef DEV_AI_MESH_GATE3_MSI_ID_POOL_HH
#define DEV_AI_MESH_GATE3_MSI_ID_POOL_HH

#include <cstddef>
#include <cstdint>
#include <optional>
#include <set>

namespace gem5
{
namespace ai_mesh
{

class Gate3MsiIdPool
{
  public:
    Gate3MsiIdPool(uint32_t base, uint32_t count);

    std::optional<uint32_t> acquire();
    bool release(uint32_t id);
    size_t available() const { return freeIds.size(); }
    size_t inUse() const { return count - freeIds.size(); }

  private:
    uint32_t base;
    size_t count;
    std::set<uint32_t> freeIds;
};

}
}

#endif

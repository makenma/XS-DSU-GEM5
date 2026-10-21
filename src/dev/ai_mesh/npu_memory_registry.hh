#ifndef DEV_AI_MESH_NPU_MEMORY_REGISTRY_HH
#define DEV_AI_MESH_NPU_MEMORY_REGISTRY_HH

#include <map>
#include <string>
#include <vector>

#include "dev/ai_mesh/memory_address_map.hh"
#include "dev/ai_mesh/mesh_memory_image.hh"

namespace gem5::ai_mesh
{
class NpuMemoryEndpoint;

class NpuMemoryRegistry
{
  public:
    explicit NpuMemoryRegistry(std::vector<NpuMemoryEndpoint *> endpoints);
    const std::vector<NpuMemoryEndpoint *> &endpoints() const { return members; }
    bool contains(uint64_t address, uint64_t size) const;
    bool read(uint64_t address, uint64_t size, uint8_t *out) const;
    bool installImage(const std::vector<MemoryImageSpan> &image);
    bool installFiles(const std::vector<MemoryFileSpan> &image, std::string &reason);
    std::string rangeDigest(uint64_t address, uint64_t size) const;
    bool idle() const;

  private:
    const std::vector<NpuMemoryEndpoint *> members;
    std::map<uint32_t, NpuMemoryEndpoint *> by_node;
    MemoryAddressMap address_map;
};
}
#endif

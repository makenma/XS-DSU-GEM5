#ifndef DEV_AI_MESH_MEMORY_ADDRESS_MAP_HH
#define DEV_AI_MESH_MEMORY_ADDRESS_MAP_HH

#include <optional>
#include <vector>

#include "mem/axi/axi_validation.hh"

namespace gem5::ai_mesh
{
struct MemoryAddressSpan
{
    uint32_t owner;
    uint64_t address;
    uint64_t size;
};

class MemoryAddressMap
{
  public:
    explicit MemoryAddressMap(std::vector<axi::AxiRange> ranges);
    std::optional<std::vector<MemoryAddressSpan>> split(
        uint64_t address, uint64_t size) const;

  private:
    axi::AxiAddressDecoder decoder;
};
}
#endif

#ifndef DEV_AI_MESH_MESH_IR_VERIFIER_HH
#define DEV_AI_MESH_MESH_IR_VERIFIER_HH

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshProgramAdmission;

struct RuntimeArch
{
    std::string arch_digest_hex;
    std::vector<uint32_t> core_ids;
    uint64_t sram_bytes = 0;
    uint32_t sram_banks = 0;
    uint32_t sram_alignment = 0;
    uint32_t axi_data_bytes = 0;
    uint32_t axi_max_burst_beats = 0;
    uint32_t axi_address_bits = 64;
    struct Region
    {
        enum Kind : uint32_t
        {
            kKindHbm = 0,
            kKindHostShared = 1,
            kKindSramAperture = 2,
        };
        uint32_t region_id = 0;
        uint64_t base = 0;
        uint64_t bytes = 0;
        uint64_t tile_stride = 0;
        uint64_t tile_bytes = 0;
        uint32_t kind = kKindHbm;
        bool is_sram_aperture = false;

        bool perCore() const
        {
            return is_sram_aperture || kind == kKindSramAperture;
        }
        // Absolute base of this region's per-core tile.  Every absolute
        // endpoint address is anchored here, and every consumer that needs a
        // tile-relative local offset subtracts it exactly once.
        uint64_t tileBase(uint16_t owner_core, bool &overflow) const;
    };
    struct AddressRange
    {
        uint32_t region_id = 0;
        uint16_t owner_core = 0xffff;
        uint64_t offset_bytes = 0;
        uint64_t size_bytes = 0;
        bool writable = false;
    };
    struct FabricTarget
    {
        std::string name;
        std::vector<AddressRange> ranges;
    };

    // Dtype capability bitmasks (bit n-1 = dtype enum value n, 1..5).
    uint32_t tensor_dtype_mask = 0x1F;
    uint32_t vector_dtype_mask = 0x1F;
    uint32_t reduce_dtype_mask = 0x1F;
    std::vector<Region> regions;
    std::vector<FabricTarget> fabric_targets;

    bool hasCore(uint16_t core_id) const;
    const Region *region(uint16_t region_id) const;
};

bool validateRuntimeArch(const RuntimeArch &arch, MeshLoadError &error);

bool admitProgram(std::shared_ptr<const DecodedProgram> program,
                  const RuntimeArch &arch, MeshProgramAdmission &admission,
                  MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

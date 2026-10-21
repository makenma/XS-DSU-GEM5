#ifndef DEV_AI_MESH_MESH_MOE_MATERIALIZER_HH
#define DEV_AI_MESH_MESH_MOE_MATERIALIZER_HH

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/mesh_moe_population.hh"
#include "dev/ai_mesh/mesh_error_candidate.hh"

namespace gem5::ai_mesh
{

struct MoePreparationError
{
    mesh_abi::ErrorSourceKey source;
    agent_abi::DetailCode detail = agent_abi::E_MOE_PROFILE;
    std::string message;

    void set(uint32_t layer, uint32_t region, mesh_abi::MaterializerCheckSite site,
             const std::string &text, agent_abi::DetailCode code = agent_abi::E_MOE_PROFILE)
    {
        source = {};
        source.error_class = mesh_abi::kMoeErrorClassMATERIALIZE;
        source.core_id_or_ffff = UINT16_MAX;
        source.domain = mesh_abi::kMeshObjectDomainCOORDINATOR;
        source.object_kind = mesh_abi::kErrorSourceObjectKindMATERIALIZER_CHECK;
        source.region_group_id = layer;
        source.region_id = region;
        source.ordinal = static_cast<uint32_t>(site);
        detail = code;
        message = text;
    }
};

struct MoeMemberSlice
{
    uint64_t request_id = 0;
    uint32_t ordinal = 0;
    uint16_t core = 0;
    uint32_t first_token = 0;
    uint32_t token_count = 0;
    uint32_t input_allocation = 0;
    uint32_t input_offset = 0;
    uint32_t output_allocation = 0;
    uint32_t output_offset = 0;
};

struct MoeMaterializeConfig
{
    std::array<uint8_t, 32> program_digest{};
    std::array<uint8_t, 32> program_semantic_digest{};
    std::array<uint8_t, 32> workload_digest{};
    uint32_t bus_bytes = 32;
    uint32_t chunk_bytes = 4096;
    uint32_t fill_mode = mesh_abi::kMoeFillModeFUNCTIONAL_BYTES;
    bool cached = false;
    std::vector<uint32_t> weight_tags;
};

bool materializeMoeOverlay(const DecodedProgram &program,
                          const mesh_abi::MoeLayerSpec &layer,
                          const MoePopulation &population,
                          const std::vector<MoeMemberSlice> &members,
                          const MoeMaterializeConfig &config,
                          MoeOverlayGraph &out, MoePreparationError &error);

}
#endif

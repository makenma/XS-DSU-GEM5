#ifndef DEV_AI_MESH_MESH_CACHE_REPLAY_HH
#define DEV_AI_MESH_MESH_CACHE_REPLAY_HH

#include <memory>
#include "dev/ai_mesh/mesh_memory_image.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_weight_cache.hh"

namespace gem5::ai_mesh
{
struct CacheStartupImage
{
    std::map<uint16_t, std::unique_ptr<MoeWeightCache>> caches;
    std::vector<MemoryImageSpan> weights;
    std::map<uint16_t, std::vector<MemoryImageSpan>> slots;

    static std::optional<CacheStartupImage> prepare(
        const std::string &image, const std::string &replay,
        const MeshProgramAdmission &admission,
        const std::string &effective_digest, const std::string &image_digest,
        const std::map<uint16_t, MoeWeightCache::Config> &configs,
        std::string &reason);
};
}
#endif

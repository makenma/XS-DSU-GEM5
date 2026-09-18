#ifndef DEV_AI_MESH_SERVING_INSTANCE_BINDING_HH
#define DEV_AI_MESH_SERVING_INSTANCE_BINDING_HH

#include <cstdint>
#include <optional>
#include <utility>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct ServingByteWindow
{
    uint64_t begin = 0;
    uint64_t bytes = 0;
};

class ServingInstanceBinding
{
  public:
    ServingInstanceBinding() = default;
    ServingInstanceBinding(uint64_t request_id, uint32_t generation)
        : request_id(request_id), generation(generation) {}

    uint64_t requestId() const { return request_id; }
    uint32_t requestGeneration() const { return generation; }

    bool bind(uint32_t tensor_id, uint64_t origin, uint64_t base,
              ServingByteWindow read, ServingByteWindow write)
    {
        for (const auto &entry : tensors)
            if (entry.tensor_id == tensor_id)
                return false;
        for (const auto window : {read, write})
            if (window.begin > UINT64_MAX - window.bytes ||
                    base > UINT64_MAX - window.begin - window.bytes)
                return false;
        tensors.push_back({tensor_id, origin, base, read, write});
        return true;
    }

    bool empty() const { return tensors.empty(); }

    std::optional<uint64_t> resolve(uint32_t tensor_id, uint64_t offset,
                                    uint64_t bytes, bool write,
                                    uint64_t fallback) const
    {
        for (const auto &entry : tensors) {
            if (entry.tensor_id != tensor_id)
                continue;
            if (offset < entry.origin)
                return std::nullopt;
            const uint64_t relative = offset - entry.origin;
            const auto window = write ? entry.write : entry.read;
            if (relative < window.begin || relative - window.begin > window.bytes ||
                    bytes > window.bytes - (relative - window.begin))
                return std::nullopt;
            return entry.base + relative;
        }
        if (offset > UINT64_MAX - fallback ||
                bytes > UINT64_MAX - fallback - offset)
            return std::nullopt;
        return fallback + offset;
    }

  private:
    struct Tensor
    {
        uint32_t tensor_id;
        uint64_t origin;
        uint64_t base;
        ServingByteWindow read;
        ServingByteWindow write;
    };
    std::vector<Tensor> tensors;
    uint64_t request_id = 0;
    uint32_t generation = 0;
};

}
}
#endif

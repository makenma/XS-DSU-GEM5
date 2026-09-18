#ifndef DEV_AI_MESH_SERVING_MESH_DISPATCH_HH
#define DEV_AI_MESH_SERVING_MESH_DISPATCH_HH

#include <array>
#include <cstdint>
#include <string>

#include "dev/ai_mesh/serving_instance_binding.hh"
#include "dev/ai_mesh/serving_fill_inputs.hh"
#include <utility>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

class MeshInstanceObserver
{
  public:
    virtual ~MeshInstanceObserver() = default;
    virtual void onInstanceStarted(uint64_t instance_generation,
                                   uint64_t request_id,
                                   uint32_t request_generation,
                                   uint64_t tick) = 0;
    virtual void onInstanceSettled(uint64_t instance_generation,
                                   uint64_t request_id,
                                   uint32_t request_generation, bool errored,
                                   uint64_t tick) = 0;
};

class ServingMeshDispatch
{
  public:
    virtual ~ServingMeshDispatch() = default;
    virtual bool installSelectors(
        const std::vector<std::pair<uint32_t, uint32_t>> &selectors,
        std::string &reason) = 0;
    virtual void bindInstance(const ServingInstanceBinding &value) = 0;
    virtual bool canAccessKv(uint64_t address, uint64_t bytes) const = 0;
    virtual void bindFillContent(const ServingFillBinding &binding) = 0;
    virtual void bindSemanticDigest(
        const std::array<uint8_t, 32> &digest) = 0;
    virtual void cancelPendingInstance() = 0;
    // Stop new issue on the running instance through the dispatcher's existing
    // instance-error path; the executor owns the terminal, the mesh owns the
    // stop.
    virtual void failCurrentInstance() = 0;
    virtual void beginFirstInstance() = 0;
    virtual void resumeInstance() = 0;
};

}
}
#endif

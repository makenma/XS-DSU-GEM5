#ifndef DEV_AI_MESH_SERVING_MESH_DISPATCH_HH
#define DEV_AI_MESH_SERVING_MESH_DISPATCH_HH

#include "dev/ai_mesh/mesh_moe_materializer.hh"

#include <array>
#include <cstdint>
#include <optional>
#include <memory>
#include <string>

#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/instance_failure_reducer.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"
#include "dev/ai_mesh/serving_fill_inputs.hh"
#include <utility>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct ServingBatchMember;

class MeshInstanceObserver
{
  public:
    virtual ~MeshInstanceObserver() = default;
    virtual void onInstanceStarted(uint64_t instance_generation,
                                   uint64_t request_id,
                                   uint32_t request_generation,
                                   uint64_t tick) = 0;
    virtual void onInstanceFailure(const BatchFailureRecord &record) = 0;
    virtual void onInstanceSettled(uint64_t instance_generation,
                                   uint64_t request_id,
                                   uint32_t request_generation, bool errored,
                                   uint64_t tick) = 0;
};

class ServingMeshDispatch
{
  public:
    virtual ~ServingMeshDispatch() = default;
    virtual void configureServingCapacity(uint32_t capacity) = 0;
    virtual std::optional<InstanceGeneration> selectInstance(
        uint32_t entrypoint_id, uint32_t profile_id, std::string &reason) = 0;
    virtual bool prepareMoe(const mesh_abi::AgentInstanceProfile &profile,
        const std::vector<ServingBatchMember> &members, const ServingInstanceBinding &binding,
        std::shared_ptr<const MoeOverlaySet> &overlays, MoePreparationError &error) = 0;
    virtual void bindInstance(const ServingInstanceBinding &value,
        std::shared_ptr<const MoeOverlaySet> overlays = {}) = 0;
    virtual bool canAccessKv(uint64_t address, uint64_t bytes) const = 0;
    virtual void bindFillContent(const ServingFillBinding &binding) = 0;
    virtual void bindSemanticDigest(
        const std::array<uint8_t, 32> &digest) = 0;
    virtual void cancelPendingInstance(uint64_t instance = 0) = 0;
    // Stop new issue on the running instance through the dispatcher's existing
    // instance-error path; the executor owns the terminal, the mesh owns the
    // stop.
    virtual void failCurrentInstance(const ErrorCandidate &failure) = 0;
    virtual void noteMemberCancel(uint64_t instance, uint64_t request, uint64_t tick) = 0;
    virtual void beginSelectedInstance(std::optional<ErrorCandidate> failure = {}) = 0;
    virtual bool replayQuiescent() const = 0;
    virtual void finishExecution() = 0;
};

}
}
#endif

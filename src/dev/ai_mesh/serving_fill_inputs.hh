#ifndef DEV_AI_MESH_SERVING_FILL_INPUTS_HH
#define DEV_AI_MESH_SERVING_FILL_INPUTS_HH

#include <map>
#include <optional>

#include "dev/ai_mesh/mesh_execution_view.hh"
#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"

namespace gem5
{
namespace ai_mesh
{

struct ServingFillBinding
{
    uint64_t request_id = 0;
    uint32_t request_generation = 0;
    uint32_t instance_profile_id = 0;
    uint32_t member_ordinal = 0;
    uint32_t allocation_id = 0;
    uint32_t producer_command_id = 0;
    std::vector<uint8_t> content;
};

class ServingFillInputs
{
  public:
    static std::optional<ServingFillInputs> freeze(
        const DecodedProgram &program, const ProfileExecutionView &view,
        InstanceGeneration instance, const ServingInstanceBinding &owner,
        const std::vector<ServingFillBinding> &bindings,
        std::string &reason);

    const std::vector<uint8_t> *contentFor(const RuntimeObjectKey &command) const;

  private:
    std::map<RuntimeObjectKey, ServingFillBinding> producers;
};

}
}
#endif

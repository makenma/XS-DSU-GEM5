#ifndef DEV_AI_MESH_MESH_EXECUTION_VIEW_HH
#define DEV_AI_MESH_MESH_EXECUTION_VIEW_HH

#include <cstdint>
#include <map>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct ProfileStreamRangeView
{
    uint16_t core_id = 0;
    uint16_t stream_id = 0;
    uint32_t command_begin = 0;
    uint32_t command_count = 0;
    std::vector<uint32_t> command_ids;
};

struct ProfileExecutionView
{
    uint32_t profile_id = 0;
    uint32_t entrypoint_id = 0;
    std::vector<ProfileStreamRangeView> ranges;

    std::vector<ProfileStreamRangeView> rangesForCore(uint16_t core_id) const;
    uint64_t commandCount(uint16_t core_id) const;
    const ProfileStreamRangeView *rangeFor(uint16_t core_id,
                                           uint16_t stream_id) const;
};

class ExecutionViewSet
{
  public:
    bool wholeProgram() const { return by_instance.size() == 1; }
    const ProfileExecutionView *wholeProgramView() const
    {
        return wholeProgram() ? &by_instance.begin()->second : nullptr;
    }
    const ProfileExecutionView *forInstance(uint32_t entrypoint_id,
                                            uint32_t profile_id) const;

  private:
    std::map<std::pair<uint32_t, uint32_t>, ProfileExecutionView>
        by_instance;
    friend bool buildExecutionViews(
        const DecodedProgram &, const ProgramSemanticContext &,
        ExecutionViewSet &, MeshLoadError &);
};

bool buildExecutionViews(
    const DecodedProgram &, const ProgramSemanticContext &,
    ExecutionViewSet &, MeshLoadError &);

}
}

#endif

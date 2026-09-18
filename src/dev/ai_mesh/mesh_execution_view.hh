#ifndef DEV_AI_MESH_MESH_EXECUTION_VIEW_HH
#define DEV_AI_MESH_MESH_EXECUTION_VIEW_HH

#include <cstdint>
#include <map>
#include <utility>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"

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
};

struct ProfileExecutionView
{
    uint32_t profile_id = 0;
    uint32_t entrypoint_id = 0;
    bool whole_program = false;
    std::vector<ProfileStreamRangeView> ranges;

    std::vector<ProfileStreamRangeView> rangesForCore(uint16_t core_id) const;
    const ProfileStreamRangeView *rangeFor(uint16_t core_id,
                                           uint16_t stream_id) const;
};

class ExecutionViewSet
{
  public:
    static ExecutionViewSet fromVerified(const DecodedProgram &program);

    bool wholeProgram() const { return whole_program_value; }
    const ProfileExecutionView &wholeProgramView() const
    {
        return whole_view;
    }
    const ProfileExecutionView *forInstance(uint32_t entrypoint_id,
                                            uint32_t profile_id) const;

  private:
    bool whole_program_value = true;
    ProfileExecutionView whole_view;
    std::map<std::pair<uint32_t, uint32_t>, ProfileExecutionView>
        by_instance;
};

} // namespace ai_mesh
} // namespace gem5

#endif

#include "dev/ai_mesh/mesh_execution_view.hh"

#include <algorithm>
#include <tuple>

namespace gem5
{
namespace ai_mesh
{

std::vector<ProfileStreamRangeView>
ProfileExecutionView::rangesForCore(uint16_t core_id) const
{
    std::vector<ProfileStreamRangeView> out;
    for (const auto &range : ranges)
        if (range.core_id == core_id)
            out.push_back(range);
    return out;
}

const ProfileStreamRangeView *
ProfileExecutionView::rangeFor(uint16_t core_id, uint16_t stream_id) const
{
    for (const auto &range : ranges)
        if (range.core_id == core_id && range.stream_id == stream_id)
            return &range;
    return nullptr;
}

ExecutionViewSet ExecutionViewSet::fromVerified(const DecodedProgram &program)
{
    ExecutionViewSet set;
    set.whole_view.whole_program = true;
    set.whole_view.ranges.reserve(program.streams.size());
    for (const auto &stream : program.streams)
        set.whole_view.ranges.push_back(
            {stream.core_id, stream.stream_id, stream.command_begin,
             stream.command_count});
    std::sort(set.whole_view.ranges.begin(), set.whole_view.ranges.end(),
              [](const ProfileStreamRangeView &left,
                 const ProfileStreamRangeView &right) {
                  return std::tie(left.core_id, left.stream_id) <
                         std::tie(right.core_id, right.stream_id);
              });
    if (!program.has_profile_scoped_execution_v1) {
        set.whole_program_value = true;
        for (const auto &entrypoint : program.entrypoints)
            for (const auto &profile : program.profiles)
                if (profile.entrypoint_id == entrypoint.entrypoint_id)
                    set.by_instance[{entrypoint.entrypoint_id,
                                     profile.profile_id}] =
                        set.whole_view;
        return set;
    }
    set.whole_program_value = false;
    std::map<uint32_t, std::vector<ProfileStreamRangeView>> grouped;
    for (const auto &range : program.profile_stream_ranges)
        grouped[range.profile_id].push_back(
            {range.core_id, range.stream_id, range.command_begin,
             range.command_count});
    for (auto &entry : grouped) {
        ProfileExecutionView view;
        view.profile_id = entry.first;
        view.whole_program = false;
        view.ranges = std::move(entry.second);
        std::sort(view.ranges.begin(), view.ranges.end(),
                  [](const ProfileStreamRangeView &left,
                     const ProfileStreamRangeView &right) {
                      return std::tie(left.core_id, left.stream_id) <
                             std::tie(right.core_id, right.stream_id);
                  });
        for (const auto &profile : program.profiles)
            if (profile.profile_id == view.profile_id)
                view.entrypoint_id = profile.entrypoint_id;
        set.by_instance[{view.entrypoint_id, view.profile_id}] = view;
    }
    return set;
}

const ProfileExecutionView *
ExecutionViewSet::forInstance(uint32_t entrypoint_id,
                              uint32_t profile_id) const
{
    const auto found = by_instance.find({entrypoint_id, profile_id});
    if (found == by_instance.end())
        return nullptr;
    return &found->second;
}

} // namespace ai_mesh
} // namespace gem5

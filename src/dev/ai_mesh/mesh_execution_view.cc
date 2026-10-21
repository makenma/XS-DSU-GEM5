#include "dev/ai_mesh/mesh_execution_view.hh"

#include <algorithm>
#include <tuple>
#include <set>

namespace gem5
{
namespace ai_mesh
{

uint64_t
ProfileExecutionView::commandCount(uint16_t core_id) const
{
    uint64_t count = 0;
    for (const auto &range : ranges)
        if (range.core_id == core_id)
            count += range.command_count;
    return count;
}

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

bool
buildExecutionViews(const DecodedProgram &program,
                    const ProgramSemanticContext &context,
                    ExecutionViewSet &out, MeshLoadError &error)
{
    auto reject = [&](const char *message) {
        error.code = "E_ABI_BOUNDS";
        error.message = message;
        return false;
    };
    if (!context.matches(program))
        return reject("execution views require this program's semantic context");
    ExecutionViewSet result;
    std::map<uint64_t, std::map<uint64_t, uint32_t>> commands;
    for (const auto &[id, command] : context.commands()) {
        const auto *stream = context.commandStream(id);
        if (!stream || command->core_id != stream->stream->core_id ||
            command->stream_id != stream->stream->physical_stream_id) {
            error = {"E_STREAM_CONTRACT", "profile stream command owner differs"};
            return false;
        }
        commands[stream->stream->stream_id].emplace(stream->ordinal, id);
    }
    using RangeKey = std::tuple<uint32_t, uint16_t, uint16_t,
                                uint32_t, uint32_t>;
    std::vector<RangeKey> expected;
    for (const auto &[id, variant] : context.variants()) {
        ProfileExecutionView view;
        view.profile_id = variant->profile_id;
        view.entrypoint_id = variant->entrypoint_id;
        for (const auto &[streamId, stream] : context.streams()) {
            if (context.owner("streams", streamId) != variant)
                continue;
            if (stream->core_id > UINT16_MAX ||
                stream->physical_stream_id > UINT16_MAX ||
                stream->command_begin > UINT32_MAX ||
                stream->command_count > UINT32_MAX)
                return reject("profile stream range exceeds wire bounds");
            ProfileStreamRangeView range;
            range.core_id = stream->core_id;
            range.stream_id = stream->physical_stream_id;
            range.command_begin = stream->command_begin;
            range.command_count = stream->command_count;
            for (const auto &[ordinal, command] : commands[streamId])
                range.command_ids.push_back(command);
            if (range.command_ids.size() != range.command_count)
                return reject("profile stream lacks its semantic commands");
            expected.emplace_back(view.profile_id, range.core_id,
                                  range.stream_id, range.command_begin,
                                  range.command_count);
            view.ranges.push_back(std::move(range));
        }
        std::sort(view.ranges.begin(), view.ranges.end(),
            [](const auto &left, const auto &right) {
                return std::tie(left.core_id, left.stream_id) <
                       std::tie(right.core_id, right.stream_id);
            });
        if (!result.by_instance.emplace(
                std::make_pair(view.entrypoint_id, view.profile_id),
                std::move(view)).second)
            return reject("execution view profile identity is duplicated");
    }
    std::sort(expected.begin(), expected.end());
    std::vector<RangeKey> actual;
    for (const auto &range : program.transport.profile_stream_ranges)
        actual.emplace_back(range.profile_id, range.core_id, range.stream_id,
                            range.command_begin, range.command_count);
    if (program.header.required_features &
        mesh_abi::kFeatureProfileScopedExecutionV1) {
        if (actual != expected)
            return reject("profile ranges differ from scheduled variant projection");
    } else if (!actual.empty()) {
        return reject("profile ranges require the profile feature");
    }
    if (result.by_instance.empty() ||
        result.by_instance.size() != program.transport.profiles.size())
        return reject("execution views do not cover declared profiles");
    std::set<std::pair<uint32_t, uint32_t>> profiles;
    for (const auto &profile : program.transport.profiles) {
        const auto key = std::make_pair(profile.entrypoint_id, profile.profile_id);
        if (!profiles.insert(key).second || !result.by_instance.count(key))
            return reject("execution views differ from declared profiles");
    }
    out = std::move(result);
    return true;
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

}
}

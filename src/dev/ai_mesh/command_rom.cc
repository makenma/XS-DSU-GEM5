#include <map>

#include "dev/ai_mesh/command_rom.hh"

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{
bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}
} // anonymous namespace

const std::vector<uint32_t> *
CommandRom::commandIndices(uint16_t stream_id) const
{
    auto it = commands_.find(stream_id);
    return it == commands_.end() ? nullptr : &it->second;
}

bool
buildCommandRom(const MeshProgramAdmission &admission, uint64_t variant_id,
                uint16_t core_id, CommandRom &out, MeshLoadError &error)
{
    const auto &variants = admission.context().variants();
    auto variant = variants.find(variant_id);
    if (variant == variants.end())
        return fail("E_RELOCATION", "selected Program variant is unknown",
                    error);
    const DecodedProgram &program = admission.program();
    const auto *view = admission.executionViews().forInstance(
        variant->second->entrypoint_id, variant->second->profile_id);
    if (!view)
        return fail("E_RELOCATION", "selected variant has no execution view",
                    error);
    std::map<uint32_t, size_t> command_index;
    for (size_t index = 0; index < program.transport.commands.size(); index++)
        command_index.emplace(program.transport.commands[index].command_id, index);
    CommandRom rom;
    rom.variant_id_ = variant_id;
    for (const auto &range : view->ranges) {
        if (range.core_id != core_id)
            continue;
        rom.streams_.push_back(range.stream_id);
        auto &indices = rom.commands_[range.stream_id];
        for (const auto command_id : range.command_ids) {
            const auto found = command_index.find(command_id);
            if (found == command_index.end())
                return fail("E_ABI_BOUNDS",
                            "admitted command has no transport command", error);
            const size_t index = found->second;
            indices.push_back(index);
            if (program.transport.commands[index].opcode == mesh_abi::kOpcodeHALT)
                rom.halts_[range.stream_id] = index;
        }
    }
    out = rom;
    return true;
}

} // namespace ai_mesh
} // namespace gem5

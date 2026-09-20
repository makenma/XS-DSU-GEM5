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
    const auto &semantic_streams = admission.context().streams();
    CommandRom rom;
    rom.variant_id_ = variant_id;
    for (const auto &stream : program.transport.streams) {
        if (stream.core_id != core_id)
            continue;
        const mesh_abi::semantic_abi::ScheduledStream *semantic = nullptr;
        for (const auto &[semantic_id, candidate] : semantic_streams)
            if (candidate->core_id == stream.core_id &&
                candidate->physical_stream_id == stream.stream_id) {
                semantic = candidate;
                break;
            }
        if (!semantic)
            return fail("E_ABI_BOUNDS",
                        "transport stream has no admitted semantic stream",
                        error);
        if (admission.context().owner("streams", semantic->stream_id) !=
            variant->second)
            continue;
        rom.streams_.push_back(stream.stream_id);
        auto &indices = rom.commands_[stream.stream_id];
        for (uint32_t index = stream.command_begin;
             index < stream.command_begin + stream.command_count; ++index) {
            if (index >= program.transport.commands.size())
                return fail("E_ABI_BOUNDS",
                            "selected stream command range is out of bounds",
                            error);
            indices.push_back(index);
            if (program.transport.commands[index].opcode ==
                mesh_abi::kOpcodeHALT)
                rom.halts_[stream.stream_id] = index;
        }
    }
    out = rom;
    return true;
}

} // namespace ai_mesh
} // namespace gem5

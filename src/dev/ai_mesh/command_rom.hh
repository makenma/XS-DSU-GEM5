#ifndef DEV_AI_MESH_COMMAND_ROM_HH
#define DEV_AI_MESH_COMMAND_ROM_HH

#include <cstdint>
#include <map>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshProgramAdmission;

// Immutable per-core projection of exactly one selected Program variant.
// The loader builds it once during installation from the admitted semantic
// ownership facts; the core only decodes streams and commands from here, so
// an unselected variant can never execute.
class CommandRom
{
  public:
    CommandRom() = default;

    uint64_t variantId() const { return variant_id_; }
    const std::vector<uint16_t> &streams() const { return streams_; }
    const std::vector<uint32_t> *commandIndices(uint16_t stream_id) const;
    bool hasStream(uint16_t stream_id) const
    {
        return commands_.count(stream_id) != 0;
    }
    bool hasHalt(uint16_t stream_id) const
    {
        return halts_.count(stream_id) != 0;
    }

  private:
    uint64_t variant_id_ = 0;
    std::vector<uint16_t> streams_;
    std::map<uint16_t, std::vector<uint32_t>> commands_;
    std::map<uint16_t, uint32_t> halts_;

    friend bool buildCommandRom(
        const MeshProgramAdmission &, uint64_t, uint16_t, CommandRom &,
        MeshLoadError &);
};

bool buildCommandRom(const MeshProgramAdmission &admission, uint64_t variant_id,
                     uint16_t core_id, CommandRom &out, MeshLoadError &error);

} // namespace ai_mesh
} // namespace gem5

#endif

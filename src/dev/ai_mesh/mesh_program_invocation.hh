#ifndef DEV_AI_MESH_MESH_PROGRAM_INVOCATION_HH
#define DEV_AI_MESH_MESH_PROGRAM_INVOCATION_HH

#include <map>
#include <memory>

#include "dev/ai_mesh/command_rom.hh"
#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/mesh_invocation_binding.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"

namespace gem5
{
namespace ai_mesh
{

class MeshProgramInvocation
{
  public:
    struct TransferPlan
    {
        uint16_t receiver_core = 0;
        uint64_t useful_bytes = 0;
        std::vector<std::pair<uint64_t, uint64_t>> ranges;
    };

    bool dmaAddresses(const DecodedDmaDescriptor &descriptor,
                      const RuntimeObjectKey &key, std::array<uint64_t, 2> &addresses,
                      MeshLoadError &error) const;
    const MeshProgramAdmission &admission() const { return *admission_; }
    uint64_t variantId() const { return binding_.variantId(); }
    const MeshInvocationBinding &binding() const { return binding_; }
    const ProfileExecutionView &executionView() const { return *view_; }
    const std::map<uint32_t, TransferPlan> &transferPlans() const
    {
        return transfers_;
    }
    const TransferPlan *transferPlan(uint32_t transfer) const
    {
        const auto found = transfers_.find(transfer);
        return found == transfers_.end() ? nullptr : &found->second;
    }
    const CommandRom *rom(uint16_t core) const
    {
        const auto found = roms_.find(core);
        return found == roms_.end() ? nullptr : &found->second;
    }

  private:
    MeshProgramInvocation() = default;
    std::shared_ptr<const MeshProgramAdmission> admission_;
    MeshInvocationBinding binding_;
    const ProfileExecutionView *view_ = nullptr;
    std::map<uint16_t, CommandRom> roms_;
    std::map<uint32_t, TransferPlan> transfers_;

    friend bool prepareProgramInvocation(
        std::shared_ptr<const MeshProgramAdmission>, uint32_t, uint32_t,
        const std::vector<DispatchBinding> &,
        std::shared_ptr<const MeshProgramInvocation> &, MeshLoadError &);
};

bool prepareProgramInvocation(
    std::shared_ptr<const MeshProgramAdmission>, uint32_t entrypoint_id,
    uint32_t profile_id, const std::vector<DispatchBinding> &,
    std::shared_ptr<const MeshProgramInvocation> &, MeshLoadError &);

}
}

#endif

#ifndef DEV_AI_MESH_MESH_IR_ADMISSION_HH
#define DEV_AI_MESH_MESH_IR_ADMISSION_HH

#include <map>
#include <memory>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_backing.hh"
#include "dev/ai_mesh/mesh_ir_control_dependency_verifier.hh"
#include "dev/ai_mesh/mesh_ir_dma_verifier.hh"
#include "dev/ai_mesh/mesh_ir_intrinsic_dependency_facts.hh"
#include "dev/ai_mesh/mesh_ir_physical_operation_verifier.hh"
#include "dev/ai_mesh/mesh_ir_region.hh"
#include "dev/ai_mesh/mesh_ir_semantic_context.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

// The single admitted program: the decoded image plus every Gate 3 verified
// fact domain that the runtime is allowed to consult.  Runtime consumers
// query these facts instead of re-deriving semantics from the raw transport
// tables.  The object is heap-stable and neither copyable nor movable because
// the fact domains retain pointers into its program, arch and context
// members.
class MeshProgramAdmission
{
  public:
    MeshProgramAdmission() = default;
    ~MeshProgramAdmission() = default;
    MeshProgramAdmission(const MeshProgramAdmission &) = delete;
    MeshProgramAdmission &operator=(const MeshProgramAdmission &) = delete;
    MeshProgramAdmission(MeshProgramAdmission &&) = delete;
    MeshProgramAdmission &operator=(MeshProgramAdmission &&) = delete;

    const DecodedProgram &program() const { return *program_; }
    const std::shared_ptr<const DecodedProgram> &programPointer() const
    {
        return program_;
    }
    const RuntimeArch &arch() const { return *arch_; }
    const ProgramSemanticContext &context() const { return context_; }
    const VerifiedProgramGeometry &geometry() const { return geometry_; }
    const VerifiedProgramBacking &backing() const { return backing_; }
    const VerifiedCommandProjectionFacts &projection() const
    {
        return projection_;
    }
    const VerifiedMatrixContractionFacts &matrixFacts() const
    {
        return matrix_facts_;
    }
    const VerifiedDmaDomain &dma() const { return dma_; }
    const VerifiedIntrinsicDependencyFacts &intrinsic() const
    {
        return intrinsic_;
    }
    const VerifiedControlDependencyFacts &control() const { return control_; }

    // The compiler's initialization analysis treats a version-zero EXTERNAL or
    // PRE_RESIDENT state as already resident; a local SRAM allocation can only
    // be PRE_RESIDENT, so this is the residency the runtime must install before
    // any command is issued.
    const std::vector<uint32_t> &initialResidentAllocations(
        uint64_t variant_id, uint16_t core_id) const
    {
        static const std::vector<uint32_t> empty;
        const auto variant = initial_residents_.find(variant_id);
        if (variant == initial_residents_.end())
            return empty;
        const auto core = variant->second.find(core_id);
        return core == variant->second.end() ? empty : core->second;
    }

  private:
    std::shared_ptr<const DecodedProgram> program_;
    const RuntimeArch *arch_ = nullptr;
    ProgramSemanticContext context_;
    VerifiedProgramGeometry geometry_;
    VerifiedProgramBacking backing_;
    VerifiedCommandProjectionFacts projection_;
    VerifiedMatrixContractionFacts matrix_facts_;
    VerifiedDmaDomain dma_;
    VerifiedIntrinsicDependencyFacts intrinsic_;
    VerifiedControlDependencyFacts control_;
    std::map<uint64_t, std::map<uint16_t, std::vector<uint32_t>>>
        initial_residents_;

    friend bool admitProgram(
        std::shared_ptr<const DecodedProgram>, const RuntimeArch &,
        MeshProgramAdmission &, MeshLoadError &);
};

} // namespace ai_mesh
} // namespace gem5

#endif

#ifndef DEV_AI_MESH_MESH_IR_BACKING_HH
#define DEV_AI_MESH_MESH_IR_BACKING_HH

#include <cstdint>
#include <map>
#include <memory>

#include "dev/ai_mesh/mesh_ir_region.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{

class BackingFactIdentity;
class VerifiedCommandProjectionFacts;
class VerifiedDmaDomain;
struct ProgramBackingBuilder;

class ExternalBackingAddressFact
{
  public:
    const mesh_abi::semantic_abi::Binding &binding() const;
    const RuntimeArch::Region &region() const;
    uint64_t allocationAddress() const;

  private:
    ExternalBackingAddressFact() = default;

    const mesh_abi::semantic_abi::Binding *binding_ = nullptr;
    const RuntimeArch::Region *region_ = nullptr;
    uint64_t allocationAddress_ = 0;

    friend struct ProgramBackingBuilder;
};

class VerifiedProgramBacking
{
  public:
    VerifiedProgramBacking() = default;
    ~VerifiedProgramBacking();
    VerifiedProgramBacking(const VerifiedProgramBacking &) = delete;
    VerifiedProgramBacking &operator=(const VerifiedProgramBacking &) = delete;
    VerifiedProgramBacking(VerifiedProgramBacking &&) noexcept;
    VerifiedProgramBacking &operator=(VerifiedProgramBacking &&) noexcept;

    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &,
        const RuntimeArch &, const VerifiedProgramGeometry &) const;
    const mesh_abi::Allocation *local(uint64_t objectId) const;
    const ExternalBackingAddressFact *external(uint64_t objectId) const;
    const mesh_abi::Shard *resident(uint64_t viewId) const;

  private:
    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    const RuntimeArch *arch_ = nullptr;
    std::shared_ptr<const void> geometryIdentity_;
    std::shared_ptr<const BackingFactIdentity> identity_;
    std::map<uint64_t, const mesh_abi::Allocation *> locals_;
    std::map<uint64_t, ExternalBackingAddressFact> externals_;
    std::map<uint64_t, const mesh_abi::Shard *> residents_;

    void captureGeometry(const VerifiedProgramGeometry &);
    std::shared_ptr<const void> invocationIdentity() const;
    bool matchesInvocation(const std::shared_ptr<const void> &) const;

    friend class VerifiedDmaDomain;
    friend struct ProgramBackingBuilder;
    friend bool verifyDmaSemanticDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &,
        const VerifiedCommandProjectionFacts &,
        VerifiedDmaDomain &, MeshLoadError &);
    friend bool verifyProgramBacking(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        VerifiedProgramBacking &, MeshLoadError &);
};

bool verifyProgramBacking(
    const DecodedProgram &, const RuntimeArch &,
    const ProgramSemanticContext &, const VerifiedProgramGeometry &,
    VerifiedProgramBacking &, MeshLoadError &);

}
}

#endif

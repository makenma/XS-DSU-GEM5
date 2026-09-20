#ifndef DEV_AI_MESH_MESH_IR_DMA_VERIFIER_HH
#define DEV_AI_MESH_MESH_IR_DMA_VERIFIER_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <vector>

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct RuntimeArch;
class VerifiedCommandProjectionFacts;
class VerifiedProgramGeometry;
class VerifiedProgramBacking;
class VerifiedDmaDomain;
class DmaFactIdentity;

class DmaCompletionFact
{
  public:
    uint32_t producerCommandId() const { return producerCommandId_; }
    uint32_t completionEventId() const { return completionEventId_; }

  private:
    uint32_t producerCommandId_ = 0;
    uint32_t completionEventId_ = 0;

    friend bool verifyDmaSemanticDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        VerifiedDmaDomain &, MeshLoadError &);
};

class P2PTransferFact
{
  public:
    const DmaCompletionFact &completion() const { return completion_; }
    uint16_t sourceCore() const { return sourceCore_; }
    uint16_t destinationCore() const { return destinationCore_; }
    uint64_t expectedBytes() const { return expectedBytes_; }

  private:
    DmaCompletionFact completion_;
    uint16_t sourceCore_ = 0;
    uint16_t destinationCore_ = 0;
    uint64_t expectedBytes_ = 0;

    friend bool verifyDmaSemanticDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        VerifiedDmaDomain &, MeshLoadError &);
};

class DescriptorBindingFact
{
  public:
    // slot_id 0 marks a local (compiler-placed) endpoint; otherwise the
    // endpoint is external and bound through that dispatch slot.
    uint32_t slotId() const { return slot_id_; }
    uint64_t begin() const { return begin_; }

  private:
    uint32_t slot_id_ = 0;
    uint64_t begin_ = 0;

    friend bool verifyDmaSemanticDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        VerifiedDmaDomain &, MeshLoadError &);
};

class VerifiedDmaDomain
{
  public:
    uint64_t sourceAddress(uint32_t descriptor_id) const;
    uint64_t destinationAddress(uint32_t descriptor_id) const;
    const DmaCompletionFact *descriptorCompletion(uint32_t descriptorId) const;
    const P2PTransferFact *p2pTransfer(uint32_t transferId) const;
    const std::vector<uint32_t> *completionDescriptors(
        uint32_t producerCommandId, uint32_t completionEventId) const;
    const std::vector<uint32_t> *commandDescriptors(uint32_t commandId) const;
    const DescriptorBindingFact *descriptorBinding(uint32_t descriptorId,
                                                   bool source) const;
    size_t p2pTransferCount() const;
    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &,
        const RuntimeArch &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &,
        const VerifiedCommandProjectionFacts &) const;

  private:
    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    const RuntimeArch *arch_ = nullptr;
    const VerifiedCommandProjectionFacts *projection_ = nullptr;
    const VerifiedProgramBacking *backing_ = nullptr;
    std::shared_ptr<const void> backingIdentity_;
    std::shared_ptr<const DmaFactIdentity> identity_;
    std::map<uint32_t, std::array<uint64_t, 2>> endpointAddresses;
    std::map<std::pair<uint32_t, bool>, DescriptorBindingFact> descriptorBindings;
    std::map<uint32_t, DmaCompletionFact> descriptorCompletions;
    std::map<uint32_t, P2PTransferFact> p2pTransfers;
    std::map<uint32_t, std::vector<uint32_t>> descriptorsByCommand;
    std::map<std::pair<uint32_t, uint32_t>, std::vector<uint32_t>>
        descriptorsByCompletion;

    friend bool verifyDmaSemanticDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        VerifiedDmaDomain &, MeshLoadError &);
};

bool verifyDmaSemanticDomain(
    const DecodedProgram &program, const RuntimeArch &arch,
    const ProgramSemanticContext &context,
    const VerifiedProgramGeometry &geometry,
    const VerifiedProgramBacking &backing,
    const VerifiedCommandProjectionFacts &projection,
    VerifiedDmaDomain &out, MeshLoadError &error);

}
}

#endif

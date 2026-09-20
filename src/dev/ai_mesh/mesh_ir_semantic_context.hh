#ifndef GEM5_DEV_AI_MESH_MESH_IR_SEMANTIC_CONTEXT_HH
#define GEM5_DEV_AI_MESH_MESH_IR_SEMANTIC_CONTEXT_HH

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <string_view>

#include "dev/ai_mesh/mesh_binary.hh"

namespace gem5
{
namespace ai_mesh
{

class ProgramSemanticContextIdentity;

class ProgramSemanticContext
{
  public:
    struct MembershipRange
    {
        uint64_t first = 0;
        uint64_t count = 0;

        bool contains(uint64_t value) const
        {
            return value >= first && value - first < count;
        }
    };

    struct CommandStream
    {
        const mesh_abi::semantic_abi::ScheduledStream *stream = nullptr;
        uint64_t ordinal = 0;
    };

    bool matches(const DecodedProgram &program) const;
    const mesh_abi::semantic_abi::ProgramSemantics &root() const;
    const MembershipRange *membership(
        uint64_t variantId, std::string_view field) const;
    const mesh_abi::semantic_abi::ProgramVariant *owner(
        std::string_view field, uint64_t identity) const;
    const CommandStream *commandStream(uint32_t commandId) const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ProgramVariant *> &variants() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ScheduledStream *> &streams() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::KernelOp *> &operations() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::KernelTensor *> &tensors() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::TensorShard *> &logicalShards() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::BufferObject *> &objects() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::BufferView *> &views() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::TensorState *> &states() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::KernelComputation *> &computations() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::Placement *> &placements() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::PartialSumDefinition *> &partialSums() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ControlToken *> &tokens() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::BarrierGroup *> &barrierGroups() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ScheduledDependency *> &dependencies() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::CommandSemantics *> &commandSemantics() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ObjectBacking *> &backings() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::ResidentView *> &residents() const;
    const std::map<uint64_t, const mesh_abi::semantic_abi::BindingSlot *> &bindingSlots() const;
    const std::map<uint32_t, const mesh_abi::Command *> &commands() const;
    const std::map<uint32_t, const mesh_abi::DmaDescriptor *> &descriptors() const;
    const std::map<uint32_t, const mesh_abi::Allocation *> &allocations() const;
    const std::map<uint32_t, const mesh_abi::Shard *> &runtimeShards() const;
    const mesh_abi::Relocation *relocation(uint32_t relocationId) const;
    const std::map<uint32_t, uint64_t> &events() const;

  private:
    struct Builder;

    const DecodedProgram *program_ = nullptr;
    const mesh_abi::semantic_abi::ProgramSemantics *root_ = nullptr;
    std::shared_ptr<const ProgramSemanticContextIdentity> identity_;
    std::map<uint64_t, std::map<std::string, MembershipRange>> memberships_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ProgramVariant *> variants_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ScheduledStream *> streams_;
    std::map<uint32_t, CommandStream> commandStreams_;
    std::map<uint64_t, const mesh_abi::semantic_abi::KernelOp *> operations_;
    std::map<uint64_t, const mesh_abi::semantic_abi::KernelTensor *> tensors_;
    std::map<uint64_t, const mesh_abi::semantic_abi::TensorShard *> logicalShards_;
    std::map<uint64_t, const mesh_abi::semantic_abi::BufferObject *> objects_;
    std::map<uint64_t, const mesh_abi::semantic_abi::BufferView *> views_;
    std::map<uint64_t, const mesh_abi::semantic_abi::TensorState *> states_;
    std::map<uint64_t, const mesh_abi::semantic_abi::KernelComputation *> computations_;
    std::map<uint64_t, const mesh_abi::semantic_abi::Placement *> placements_;
    std::map<uint64_t, const mesh_abi::semantic_abi::PartialSumDefinition *> partialSums_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ControlToken *> tokens_;
    std::map<uint64_t, const mesh_abi::semantic_abi::BarrierGroup *> barrierGroups_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ScheduledDependency *> dependencies_;
    std::map<uint64_t, const mesh_abi::semantic_abi::CommandSemantics *> commandSemantics_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ObjectBacking *> backings_;
    std::map<uint64_t, const mesh_abi::semantic_abi::ResidentView *> residents_;
    std::map<uint64_t, const mesh_abi::semantic_abi::BindingSlot *> bindingSlots_;
    std::map<uint32_t, const mesh_abi::Command *> commands_;
    std::map<uint32_t, const mesh_abi::DmaDescriptor *> descriptors_;
    std::map<uint32_t, const mesh_abi::Allocation *> allocations_;
    std::map<uint32_t, const mesh_abi::Shard *> runtimeShards_;
    std::map<uint32_t, const mesh_abi::Relocation *> relocations_;
    std::map<uint32_t, uint64_t> events_;

    friend struct Builder;
    friend bool buildProgramSemanticContext(
        const DecodedProgram &, ProgramSemanticContext &, MeshLoadError &);
};

bool buildProgramSemanticContext(
    const DecodedProgram &, ProgramSemanticContext &, MeshLoadError &);

}
}

#endif

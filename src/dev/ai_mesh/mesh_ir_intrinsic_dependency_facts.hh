#ifndef GEM5_DEV_AI_MESH_MESH_IR_INTRINSIC_DEPENDENCY_FACTS_HH
#define GEM5_DEV_AI_MESH_MESH_IR_INTRINSIC_DEPENDENCY_FACTS_HH

#include <cstddef>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <vector>

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

class VerifiedDependencyGraph;
class VerifiedControlDependencyFacts;

namespace detail
{
struct ControlDependencyBuilder;
struct LifetimeBuilder;
}

class IntrinsicFactIdentity
{
  public:
    IntrinsicFactIdentity() = default;
};

class IntrinsicNode
{
  public:
    bool operator==(const IntrinsicNode &other) const
    {
        return index_ == other.index_ && identity_ == other.identity_;
    }

  private:
    IntrinsicNode(
        size_t index, std::shared_ptr<const IntrinsicFactIdentity> identity)
        : index_(index), identity_(std::move(identity))
    {}

    size_t index_ = 0;
    std::shared_ptr<const IntrinsicFactIdentity> identity_;

    friend class KernelOperationFact;
    friend class KernelTokenFact;
    friend class VerifiedIntrinsicDependencyFacts;
    friend struct detail::ControlDependencyBuilder;
};

class KernelOperationFact
{
  public:
    IntrinsicNode start() const { return start_; }
    IntrinsicNode completion() const { return completion_; }

  private:
    IntrinsicNode start_{0, nullptr};
    IntrinsicNode completion_{0, nullptr};

    friend class VerifiedIntrinsicDependencyFacts;
};

class KernelTokenFact
{
  public:
    IntrinsicNode completion() const { return completion_; }
    bool initial() const { return initial_; }
    std::optional<uint64_t> producerOperation() const
    {
        return producerOperation_;
    }

  private:
    IntrinsicNode completion_{0, nullptr};
    bool initial_ = false;
    std::optional<uint64_t> producerOperation_;

    friend class VerifiedIntrinsicDependencyFacts;
};

class VerifiedIntrinsicDependencyFacts
{
  public:
    bool matches(const DecodedProgram &, const ProgramSemanticContext &) const;
    const KernelOperationFact *operation(uint64_t operationId) const;
    const KernelTokenFact *token(uint64_t tokenId) const;
    const IntrinsicNode *invocationBegin(uint64_t variantId) const;
    bool intrinsicHappensBefore(
        IntrinsicNode, IntrinsicNode, bool &, MeshLoadError &) const;
    const uint64_t *stateProducer(uint64_t stateId) const;

  private:
    struct Builder;

    static IntrinsicNode makeNode(
        size_t, std::shared_ptr<const IntrinsicFactIdentity>);
    static KernelOperationFact makeOperationFact(IntrinsicNode, IntrinsicNode);
    static KernelTokenFact makeTokenFact(
        IntrinsicNode, bool, std::optional<uint64_t>);

    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    std::shared_ptr<const IntrinsicFactIdentity> identity_;
    std::map<uint64_t, KernelOperationFact> operations_;
    std::map<uint64_t, KernelTokenFact> tokens_;
    std::map<uint64_t, IntrinsicNode> invocationBegins_;
    std::map<uint64_t, std::vector<uint64_t>> afterTokens_;
    std::map<uint64_t, std::vector<uint64_t>> operandStates_;
    std::map<uint64_t, uint64_t> tokenProducers_;
    std::map<uint64_t, uint64_t> stateProducers_;
    std::shared_ptr<const VerifiedDependencyGraph> graph_;

    friend struct Builder;
    friend struct detail::ControlDependencyBuilder;
    friend struct detail::LifetimeBuilder;
    friend class VerifiedControlDependencyFacts;
    friend bool verifyProgramIntrinsicDependencyFacts(
        const DecodedProgram &, const ProgramSemanticContext &,
        VerifiedIntrinsicDependencyFacts &, MeshLoadError &);
};

bool verifyProgramIntrinsicDependencyFacts(
    const DecodedProgram &, const ProgramSemanticContext &,
    VerifiedIntrinsicDependencyFacts &, MeshLoadError &);

}
}

#endif

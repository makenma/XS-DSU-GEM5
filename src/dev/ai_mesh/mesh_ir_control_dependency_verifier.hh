#ifndef GEM5_DEV_AI_MESH_MESH_IR_CONTROL_DEPENDENCY_VERIFIER_HH
#define GEM5_DEV_AI_MESH_MESH_IR_CONTROL_DEPENDENCY_VERIFIER_HH

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <memory>
#include <utility>

#include "dev/ai_mesh/mesh_ir_semantic_context.hh"

namespace gem5
{
namespace ai_mesh
{

struct RuntimeArch;
class VerifiedDmaDomain;
class VerifiedDependencyGraph;
class VerifiedIntrinsicDependencyFacts;
class VerifiedProgramBacking;
class VerifiedProgramGeometry;
class IntrinsicFactIdentity;
class CommandProjectionIdentity;

namespace detail
{
struct ControlDependencyBuilder;
}

class ControlFactIdentity
{
  public:
    ControlFactIdentity() = default;
};

class DeclaredScheduledNode
{
  public:
    bool operator==(const DeclaredScheduledNode &other) const
    {
        return index_ == other.index_ && identity_ == other.identity_;
    }

  private:
    DeclaredScheduledNode(size_t index, std::shared_ptr<const ControlFactIdentity> identity)
        : index_(index), identity_(std::move(identity))
    {}

    size_t index_ = 0;
    std::shared_ptr<const ControlFactIdentity> identity_;

    friend class ScheduledDependencyFact;
    friend class VerifiedControlDependencyFacts;
};

class ScheduledStartNode
{
  public:
    bool operator==(const ScheduledStartNode &other) const
    {
        return index_ == other.index_ && identity_ == other.identity_;
    }

  private:
    ScheduledStartNode(size_t index, std::shared_ptr<const ControlFactIdentity> identity)
        : index_(index), identity_(std::move(identity))
    {}

    size_t index_ = 0;
    std::shared_ptr<const ControlFactIdentity> identity_;

    friend class ScheduledCommandFact;
    friend class VerifiedControlDependencyFacts;
};

class ScheduledCompletionNode
{
  public:
    bool operator==(const ScheduledCompletionNode &other) const
    {
        return index_ == other.index_ && identity_ == other.identity_;
    }

  private:
    ScheduledCompletionNode(size_t index, std::shared_ptr<const ControlFactIdentity> identity)
        : index_(index), identity_(std::move(identity))
    {}

    size_t index_ = 0;
    std::shared_ptr<const ControlFactIdentity> identity_;

    friend class ScheduledCommandFact;
    friend class VerifiedControlDependencyFacts;
};

class ScheduledEventNode
{
  public:
    bool operator==(const ScheduledEventNode &other) const
    {
        return index_ == other.index_ && identity_ == other.identity_;
    }

  private:
    ScheduledEventNode(size_t index, std::shared_ptr<const ControlFactIdentity> identity)
        : index_(index), identity_(std::move(identity))
    {}

    size_t index_ = 0;
    std::shared_ptr<const ControlFactIdentity> identity_;

    friend class ScheduledEventFact;
    friend class VerifiedControlDependencyFacts;
};

class ScheduledCommandFact
{
  public:
    ScheduledStartNode start() const { return start_; }
    ScheduledCompletionNode completion() const { return completion_; }
    uint64_t executionCount() const { return executionCount_; }

  private:
    ScheduledStartNode start_{0, nullptr};
    ScheduledCompletionNode completion_{0, nullptr};
    uint64_t executionCount_ = 1;

    friend class VerifiedControlDependencyFacts;
};

class ScheduledEventFact
{
  public:
    ScheduledEventNode ready() const { return ready_; }

  private:
    ScheduledEventNode ready_{0, nullptr};

    friend class VerifiedControlDependencyFacts;
};

class ScheduledDependencyFact
{
  public:
    DeclaredScheduledNode source() const { return source_; }
    DeclaredScheduledNode target() const { return target_; }

  private:
    DeclaredScheduledNode source_{0, nullptr};
    DeclaredScheduledNode target_{0, nullptr};

    friend class VerifiedControlDependencyFacts;
};

enum class ProjectedCommandKind : uint8_t
{
    Control,
    Kernel,
    Dma,
    RecvWait,
    Barrier,
};

class CommandProjectionFact
{
  public:
    uint32_t commandId() const { return commandId_; }
    ProjectedCommandKind kind() const { return kind_; }
    uint64_t operationId() const { return operationId_; }

  private:
    uint32_t commandId_ = 0;
    ProjectedCommandKind kind_ = ProjectedCommandKind::Control;
    uint64_t operationId_ = 0;

    friend class VerifiedCommandProjectionFacts;
};

class DmaCommandProjection
{
  public:
    uint64_t operationId() const { return operationId_; }
    uint64_t descriptorGroupId() const { return descriptorGroupId_; }

  private:
    uint64_t operationId_ = 0;
    uint64_t descriptorGroupId_ = 0;

    friend class VerifiedCommandProjectionFacts;
};

class RecvWaitCommandProjection
{
  public:
    uint32_t transferId() const { return transferId_; }

  private:
    uint32_t transferId_ = 0;

    friend class VerifiedCommandProjectionFacts;
};

class BarrierCommandProjection
{
  public:
    uint64_t barrierGroupId() const { return barrierGroupId_; }

  private:
    uint64_t barrierGroupId_ = 0;

    friend class VerifiedCommandProjectionFacts;
};

class RepeatCommandProjection
{
  public:
    uint64_t beginOrdinal() const { return beginOrdinal_; }
    uint64_t commandCount() const { return commandCount_; }
    uint64_t repeatCount() const { return repeatCount_; }

  private:
    uint64_t beginOrdinal_ = 0;
    uint64_t commandCount_ = 0;
    uint64_t repeatCount_ = 0;

    friend class VerifiedCommandProjectionFacts;
};

class VerifiedCommandProjectionFacts
{
  public:
    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &) const;
    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &,
        const RuntimeArch &) const;
    const CommandProjectionFact *command(uint32_t commandId) const;
    const DmaCommandProjection *dma(uint32_t commandId) const;
    const RecvWaitCommandProjection *recvWait(uint32_t commandId) const;
    const BarrierCommandProjection *barrier(uint32_t commandId) const;
    const RepeatCommandProjection *repeat(uint32_t commandId) const;
    const CommandProjectionFact *operation(uint64_t operationId) const;

  private:
    struct Builder;

    static CommandProjectionFact makeCommandFact(
        uint32_t, ProjectedCommandKind, uint64_t);
    static DmaCommandProjection makeDmaProjection(uint64_t, uint64_t);
    static RecvWaitCommandProjection makeRecvWaitProjection(uint32_t);
    static BarrierCommandProjection makeBarrierProjection(uint64_t);
    static RepeatCommandProjection makeRepeatProjection(
        uint64_t, uint64_t, uint64_t);

    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    const RuntimeArch *arch_ = nullptr;
    std::shared_ptr<const CommandProjectionIdentity> identity_;
    std::map<uint32_t, CommandProjectionFact> commands_;
    std::map<uint64_t, uint32_t> commandsByOperation_;
    std::map<uint32_t, DmaCommandProjection> dmas_;
    std::map<uint32_t, RecvWaitCommandProjection> recvWaits_;
    std::map<uint32_t, BarrierCommandProjection> barriers_;
    std::map<uint32_t, RepeatCommandProjection> repeats_;

    friend struct Builder;
    friend bool verifyProgramCommandProjection(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, VerifiedCommandProjectionFacts &,
        MeshLoadError &);
};

class VerifiedControlDependencyFacts
{
  public:
    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &) const;
    bool matches(
        const DecodedProgram &, const ProgramSemanticContext &,
        const RuntimeArch &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        const VerifiedDmaDomain &, const VerifiedIntrinsicDependencyFacts &) const;
    const ScheduledCommandFact *scheduled(uint32_t commandId) const;
    const ScheduledEventFact *event(uint32_t eventId) const;
    const ScheduledDependencyFact *dependency(uint64_t dependencyId) const;
    bool declaredHappensBefore(
        DeclaredScheduledNode, DeclaredScheduledNode, bool &,
        MeshLoadError &) const;
    bool completionHappensBefore(
        ScheduledCompletionNode, ScheduledStartNode, bool &,
        MeshLoadError &) const;

  private:
    static DeclaredScheduledNode makeDeclaredNode(
        size_t, std::shared_ptr<const ControlFactIdentity>);
    static ScheduledStartNode makeScheduledStartNode(
        size_t, std::shared_ptr<const ControlFactIdentity>);
    static ScheduledCompletionNode makeScheduledCompletionNode(
        size_t, std::shared_ptr<const ControlFactIdentity>);
    static ScheduledEventNode makeScheduledEventNode(
        size_t, std::shared_ptr<const ControlFactIdentity>);
    static ScheduledCommandFact makeScheduledFact(
        ScheduledStartNode, ScheduledCompletionNode, uint64_t);
    static ScheduledEventFact makeEventFact(ScheduledEventNode);
    static ScheduledDependencyFact makeDependencyFact(
        DeclaredScheduledNode, DeclaredScheduledNode);

    const DecodedProgram *program_ = nullptr;
    const ProgramSemanticContext *context_ = nullptr;
    const RuntimeArch *arch_ = nullptr;
    const VerifiedProgramGeometry *geometry_ = nullptr;
    const VerifiedProgramBacking *backing_ = nullptr;
    const VerifiedCommandProjectionFacts *projection_ = nullptr;
    const VerifiedDmaDomain *dma_ = nullptr;
    std::shared_ptr<const IntrinsicFactIdentity> intrinsicIdentity_;
    std::shared_ptr<const ControlFactIdentity> identity_;
    std::map<uint32_t, ScheduledCommandFact> commands_;
    std::map<uint32_t, ScheduledEventFact> events_;
    std::map<uint64_t, ScheduledDependencyFact> dependencies_;
    std::shared_ptr<const VerifiedDependencyGraph> declared_;
    std::shared_ptr<const VerifiedDependencyGraph> completion_;

    friend struct detail::ControlDependencyBuilder;
    friend bool verifyProgramControlDependencyDomain(
        const DecodedProgram &, const RuntimeArch &,
        const ProgramSemanticContext &, const VerifiedProgramGeometry &,
        const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
        const VerifiedDmaDomain &, const VerifiedIntrinsicDependencyFacts &,
        VerifiedControlDependencyFacts &,
        MeshLoadError &);
};

bool verifyProgramCommandProjection(
    const DecodedProgram &, const RuntimeArch &,
    const ProgramSemanticContext &, VerifiedCommandProjectionFacts &,
    MeshLoadError &);

bool verifyProgramControlDependencyDomain(
    const DecodedProgram &, const RuntimeArch &,
    const ProgramSemanticContext &, const VerifiedProgramGeometry &,
    const VerifiedProgramBacking &, const VerifiedCommandProjectionFacts &,
    const VerifiedDmaDomain &, const VerifiedIntrinsicDependencyFacts &,
    VerifiedControlDependencyFacts &,
    MeshLoadError &);

}
}

#endif

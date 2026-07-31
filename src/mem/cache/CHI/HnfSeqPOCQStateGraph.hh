#ifndef __MEM_CACHE_CHI_HNF_SEQ_POCQ_STATE_GRAPH_HH__
#define __MEM_CACHE_CHI_HNF_SEQ_POCQ_STATE_GRAPH_HH__

#include <initializer_list>
#include <memory>
#include <vector>

#include "mem/cache/CHI/base/DirectedGraph.hh"

namespace gem5::Chi
{

enum class SeqPocqState : uint8_t
{
    Idle,
    HazardCheck,
    Sleep,
    WaitSnoop,
    CompleteIssue,
    CompleteWait
};

enum class SeqPocqEventKind : uint8_t
{
    Admit,
    HazardBlocked,
    HazardClear,
    SnoopDone,
    CompleteAccepted,
    CompleteReplay,
    CompleteDone
};

enum class SeqPocqActionKind : uint8_t
{
    CheckHazard,
    QueueCleanInvalid,
    IssueCompleteSfEvict,
    Retire
};

struct SeqPocqEvent
{
    SeqPocqEventKind kind = SeqPocqEventKind::Admit;
};

class SeqPocqNode;
class SeqPocqEdge;

class SeqPocqNode : public DGNode<SeqPocqNode, SeqPocqEdge>
{
  public:
    explicit SeqPocqNode(SeqPocqState state) : state(state) {}

    SeqPocqState getState() const { return state; }
    bool isEqualTo(const SeqPocqNode& other) const
    {
        return state == other.state;
    }

  private:
    SeqPocqState state;
};

class SeqPocqEdge : public DGEdge<SeqPocqNode, SeqPocqEdge>
{
  public:
    using Condition = bool (*)(const SeqPocqEvent&);

    SeqPocqEdge(SeqPocqNode& target, Condition condition,
                std::initializer_list<SeqPocqActionKind> actions);

    bool isEqualTo(const SeqPocqEdge& other) const
    {
        return this == &other;
    }
    bool matches(const SeqPocqEvent& event) const;
    const std::vector<SeqPocqActionKind>& getActions() const
    {
        return actions;
    }

  private:
    Condition condition;
    std::vector<SeqPocqActionKind> actions;
};

struct SeqPocqStepResult
{
    bool stepped = false;
    SeqPocqState oldState = SeqPocqState::Idle;
    SeqPocqState nextState = SeqPocqState::Idle;
    std::vector<SeqPocqActionKind> actions;
};

class SEQ_POCQ_StateGraph : public DirectedGraph<SeqPocqNode, SeqPocqEdge>
{
  public:
    SEQ_POCQ_StateGraph();

    SeqPocqStepResult tryStep(SeqPocqState& state,
                              const SeqPocqEvent& event) const;

    static const char* stateName(SeqPocqState state);
    static const char* eventName(SeqPocqEventKind event);
    static const char* actionName(SeqPocqActionKind action);

  private:
    SeqPocqNode idle;
    SeqPocqNode hazardCheck;
    SeqPocqNode sleep;
    SeqPocqNode waitSnoop;
    SeqPocqNode completeIssue;
    SeqPocqNode completeWait;
    std::vector<std::unique_ptr<SeqPocqEdge>> edgeStorage;

    const SeqPocqNode& nodeFor(SeqPocqState state) const;
    void addTransition(SeqPocqNode& src, SeqPocqNode& dst,
                       SeqPocqEdge::Condition condition,
                       std::initializer_list<SeqPocqActionKind> actions);
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_HNF_SEQ_POCQ_STATE_GRAPH_HH__

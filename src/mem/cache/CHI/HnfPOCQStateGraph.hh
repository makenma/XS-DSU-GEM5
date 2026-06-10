#ifndef __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__
#define __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__

#include <initializer_list>
#include <memory>
#include <vector>

#include "mem/cache/CHI/HnfCcTypes.hh"
#include "mem/cache/CHI/base/DirectedGraph.hh"

namespace gem5::Chi
{

class PocqEdge;

class PocqNode : public DGNode<PocqNode, PocqEdge>
{
  public:
    explicit PocqNode(PocqState state) : state(state) {}

    PocqState getState() const { return state; }

    bool
    isEqualTo(const PocqNode& node) const
    {
        return state == node.state;
    }

  private:
    PocqState state = PocqState::Idle;
};

class PocqEdge : public DGEdge<PocqNode, PocqEdge>
{
  public:
    using Condition = bool (*)(const PocqEvent&);

    PocqEdge(PocqNode& target, Condition condition,
             std::initializer_list<PocqActionKind> actions);

    bool isEqualTo(const PocqEdge& edge) const { return this == &edge; }
    bool matches(const PocqEvent& event) const;
    const std::vector<PocqActionKind>& getActions() const { return actions; }

  private:
    Condition condition = nullptr;
    std::vector<PocqActionKind> actions;
};

struct PocqStepResult
{
    bool stepped = false;
    PocqState oldState = PocqState::Idle;
    PocqState nextState = PocqState::Idle;
    std::vector<PocqActionKind> actions;
};

class POCQ_StateGraph : public DirectedGraph<PocqNode, PocqEdge>
{
  public:
    POCQ_StateGraph();

    PocqStepResult tryStep(PocqState& state, const PocqEvent& event) const;

    static const char* stateName(PocqState state);
    static const char* eventName(PocqEventKind event);
    static const char* actionName(PocqActionKind action);

  private:
    PocqNode idle;
    PocqNode slcLookup;
    PocqNode slcUpdate;
    PocqNode txLink;
    PocqNode waitCompAck;
    PocqNode issueMcRead;
    PocqNode sleep;

    std::vector<std::unique_ptr<PocqEdge>> edgeStorage;

    PocqNode& nodeFor(PocqState state);
    const PocqNode& nodeFor(PocqState state) const;
    void addTransition(PocqNode& src, PocqNode& dst,
                       PocqEdge::Condition condition,
                       std::initializer_list<PocqActionKind> actions);
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__

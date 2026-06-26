#ifndef __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__
#define __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__

#include <initializer_list>
#include <memory>
#include <ostream>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/HnfCcTypes.hh"
#include "mem/cache/CHI/base/DirectedGraph.hh"

namespace gem5::Chi
{

class POCQ_StateGraph;
class PocqEdge;

enum class SubGraphPublicState : uint8_t
{
    Start,  // Sub-graph entry point (always the Idle node)
    Exit    // Sub-graph exit — reaching this means "sub done"
};

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

    // --- Sub-graph support ---
    bool hasSubGraph() const { return subGraph != nullptr; }
    void setSubGraph(POCQ_StateGraph* graph) { subGraph = graph; }
    POCQ_StateGraph* getSubGraph() const { return subGraph; }

    // Recursive debug print: "SlcLookup > SlcUpdate > Idle"
    void printState(std::ostream& os) const;

  private:
    PocqState state = PocqState::Idle;
    POCQ_StateGraph* subGraph = nullptr;
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
    friend class PocqNode;

  public:
    POCQ_StateGraph();

    PocqStepResult tryStep(PocqState& state, const PocqEvent& event) const;

    // Mark boundary nodes for sub-graph use.
    void markPublicNode(PocqNode& node, SubGraphPublicState role);
    PocqNode* getStartNode() const { return startNode_; }
    PocqNode* getExitNode() const { return exitNode_; }

    // Sub-graph lifecycle (called internally by tryStep).
    bool isActive() const;
    void enter();
    void exit();

    const PocqNode& nodeFor(PocqState state) const;
    PocqNode& nodeFor(PocqState state);

    void addTransition(PocqNode& src, PocqNode& dst,
                       PocqEdge::Condition condition,
                       std::initializer_list<PocqActionKind> actions);

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

    // Sub-graph boundary markers.
    PocqNode* startNode_ = nullptr;
    PocqNode* exitNode_ = nullptr;

    // Per-entry sub-state tracking.
    // Key: pointer to the entry's PocqState variable.
    // Value: current state within this sub-graph for that entry.
    // Presence of a key means the entry is active in this sub-graph.
    mutable std::unordered_map<const void*, PocqState> entrySubStates_;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_HNF_POCQ_STATE_GRAPH_HH__

#ifndef __MEM_CACHE_CHI_POCQ_STATE_GRAPH_HH__
#define __MEM_CACHE_CHI_POCQ_STATE_GRAPH_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <memory>
#include <optional>
#include <vector>

#include "mem/cache/CHI/base/DirectedGraph.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"

namespace gem5::Chi
{

// A graph id is deliberately separate from the CHI request opcode. Top-level
// request graphs are selected from an opcode, while reusable graphs such as
// MCRead and SnpUnique have no corresponding incoming request opcode.
enum class PocqGraphId : uint8_t
{
    CleanInvalid,
    Evict,
    McDmt,
    McRead,
    MakeInvalid,
    MakeUnique,
    ReadNoSnp,
    ReadOnce,
    ReadShared,
    ReadUnique,
    SnpCleanInvalid,
    SnpMakeInvalid,
    SnpOnce,
    SnpOnceFwd,
    SnpShared,
    SnpUnique,
    WriteBackFull,
    WriteCleanFull,
    NumGraphs,
    Invalid = 0xff
};

// Node names are local roles. A node is uniquely identified by the
// (PocqGraphId, PocqNodeRole) pair, avoiding names such as
// ReadUnique_SlcLookup while still distinguishing nodes in different graphs.
enum class PocqNodeRole : uint8_t
{
    Entry,
    SlcLookup,
    SlcFill,
    Idle,

    TxDat,
    TxSnp,
    TxRsp,
    McRetry,

    CallMcRead,
    CallMcDmt,
    CallSnpCleanInvalid,
    CallSnpMakeInvalid,
    CallSnpOnce,
    CallSnpOnceFwd,
    CallSnpShared,
    CallSnpUnique,

    WaitReadReceipt,
    WaitCompData,
    WaitCompAck,
    WaitRnfCompAck,
    WaitSnpResp,
    WaitSnpRespData,
    WaitWriteData,

    Exit,
    NumRoles,
    Invalid = 0xff
};

struct PocqNodeKey
{
    PocqGraphId graph = PocqGraphId::Invalid;
    PocqNodeRole role = PocqNodeRole::Invalid;

    bool
    operator==(const PocqNodeKey &other) const
    {
        return graph == other.graph && role == other.role;
    }

    bool operator!=(const PocqNodeKey &other) const { return !(*this == other); }
};

enum class PocqNodeKind : uint8_t
{
    State,
    Immediate,
    Composite,
    Exit
};

enum class PocqEventSource : uint8_t
{
    Internal,
    LinkLayer,
    SlcSf
};

enum class PocqEventKind : uint8_t
{
    Enter,
    SubGraphDone,
    SlcLookupDone,
    ReadReceipt,
    CompData,
    CompAck,
    SnpResp,
    SnpRespData,
    WriteData,
    SlcSfDone
};

// Event payload fields are intentionally kept next to the event. Guards can
// inspect them without storing per-entry state in the shared graph objects.
struct PocqEvent
{
    PocqEventKind kind = PocqEventKind::Enter;
    PocqEventSource source = PocqEventSource::Internal;
    bool slcHit = false;
    bool sfHit = false;
    bool orderNonZero = false;
    bool dirty = false;

    static PocqEvent enter();
    static PocqEvent subGraphDone();
};

enum class PocqActionTarget : uint8_t
{
    LinkLayer,
    SlcSf,
    Internal
};

enum class PocqActionKind : uint8_t
{
    StartSlcLookup,
    SendReadNoSnp,
    SendCompAck,
    SendCompData,
    SendComp,
    SendCompDbidResp,
    SendSnpOnce,
    SendSnpOnceFwd,
    SendSnpShared,
    SendSnpUnique,
    SendSnpMakeInvalid,
    FlushSf,
    FlushL3,
    WriteL3,
    WriteL3FlushSf,
    UpdateSf
};

struct PocqAction
{
    PocqActionKind kind;
    PocqActionTarget target;

    bool
    operator==(const PocqAction &other) const
    {
        return kind == other.kind && target == other.target;
    }
};

class PocqNode;
class PocqEdge;
class PocqGraph;
class PocqGraphPanel;

struct PocqMachineContext
{
    bool dctEnabled = false;
};

// Per-entry state. The graph panel owns all graph objects; a transaction only
// carries a current node pointer, a return stack, and its mutable context.
struct PocqMachine
{
    static constexpr std::size_t MaxSubGraphDepth = 8;

    const PocqNode *state = nullptr;
    PocqMachineContext context{};
    bool completed = false;

    void reset(const PocqNode *entry);
    std::size_t subgraph_depth() const { return callDepth; }

  private:
    friend class PocqNode;

    std::array<const PocqNode *, MaxSubGraphDepth> callStack{};
    std::size_t callDepth = 0;
};

class PocqStepResult
{
  public:
    static constexpr std::size_t MaxActions = 8;

    bool transitioned() const { return didTransition; }
    bool is_complete() const { return completed; }
    bool need_action() const { return actionCount != 0; }
    std::size_t action_count() const { return actionCount; }

    const PocqAction &get_action(std::size_t index = 0) const;
    PocqNodeKey old_state() const { return oldState; }
    PocqNodeKey new_state() const { return newState; }

  private:
    friend class PocqEdge;
    friend class PocqNode;

    void addAction(const PocqAction &action);

    bool didTransition = false;
    bool completed = false;
    PocqNodeKey oldState{};
    PocqNodeKey newState{};
    std::array<PocqAction, MaxActions> actions{};
    std::size_t actionCount = 0;
};

using PocqGuard = bool (*)(const PocqMachine &, const PocqEvent &);

class PocqEdge : public DGEdge<PocqNode, PocqEdge>
{
  public:
    PocqEdge(PocqNode &target, PocqEventKind event, PocqGuard guard,
             std::initializer_list<PocqAction> actions);

    bool matches(const PocqMachine &machine, const PocqEvent &event) const;
    bool isEqualTo(const PocqEdge &other) const { return this == &other; }
    void appendActions(PocqStepResult &result) const;

  private:
    static constexpr std::size_t MaxActions = 4;

    PocqEventKind eventKind;
    PocqGuard guard;
    std::array<PocqAction, MaxActions> actions{};
    std::size_t actionCount = 0;
};

class PocqNode : public DGNode<PocqNode, PocqEdge>
{
  public:
    PocqNode(PocqNodeKey key, PocqNodeKind kind);

    PocqNodeKey key() const { return nodeKey; }
    PocqNodeKind kind() const { return nodeKind; }
    const PocqGraph *childGraph() const { return child; }

    // The shared node is immutable while stepping; all mutable state lives in
    // the supplied per-entry machine. A null optional means that this event
    // has no matching outgoing edge from the current node.
    std::optional<PocqStepResult>
    step(PocqMachine &machine, const PocqEvent &event) const;

    bool isEqualTo(const PocqNode &other) const
    {
        return nodeKey == other.nodeKey;
    }

  private:
    friend class PocqGraphPanel;

    void setChildGraph(const PocqGraph *graph) { child = graph; }

    PocqNodeKey nodeKey;
    PocqNodeKind nodeKind;
    const PocqGraph *child = nullptr;
};

class PocqGraph : public DirectedGraph<PocqNode, PocqEdge>
{
  public:
    explicit PocqGraph(PocqGraphId id) : graphId(id) {}

    PocqGraphId id() const { return graphId; }
    const PocqNode *entry() const { return entryNode; }
    const PocqNode *exit() const { return exitNode; }

  private:
    friend class PocqGraphPanel;

    void setEntry(PocqNode *node) { entryNode = node; }
    void setExit(PocqNode *node) { exitNode = node; }

    PocqGraphId graphId;
    PocqNode *entryNode = nullptr;
    PocqNode *exitNode = nullptr;
};

// Owns all registered graphs, nodes, and edges. Node addresses remain stable
// because the objects themselves are individually allocated, even if the
// storage vectors grow while the panel is being built.
class PocqGraphPanel
{
  public:
    PocqGraphPanel();

    bool hasGraph(PocqGraphId id) const;
    const PocqGraph &graph(PocqGraphId id) const;
    const PocqNode *node(PocqGraphId graph, PocqNodeRole role) const;

    bool initialize(PocqMachine &machine, PocqGraphId graph) const;
    static std::optional<PocqGraphId> graphForReq(ReqMinor opcode);

  private:
    static constexpr std::size_t GraphCount =
        static_cast<std::size_t>(PocqGraphId::NumGraphs);
    static constexpr std::size_t RoleCount =
        static_cast<std::size_t>(PocqNodeRole::NumRoles);

    using NodeIndex =
        std::array<std::array<PocqNode *, RoleCount>, GraphCount>;

    PocqGraph &makeGraph(PocqGraphId id);
    PocqNode &makeNode(PocqGraphId graph, PocqNodeRole role,
                       PocqNodeKind kind);
    PocqEdge &addEdge(PocqNode &source, PocqNode &target,
                      PocqEventKind event, PocqGuard guard,
                      std::initializer_list<PocqAction> actions = {});

    void buildMcRead();
    void buildReadUnique();
    void buildSnpCleanInvalid();
    void buildCleanInvalid();

    std::array<std::unique_ptr<PocqGraph>, GraphCount> graphs{};
    NodeIndex nodeIndex{};
    std::vector<std::unique_ptr<PocqNode>> nodeStorage;
    std::vector<std::unique_ptr<PocqEdge>> edgeStorage;
};

const char *pocqGraphName(PocqGraphId id);
const char *pocqNodeRoleName(PocqNodeRole role);
const char *pocqActionName(PocqActionKind action);

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_POCQ_STATE_GRAPH_HH__

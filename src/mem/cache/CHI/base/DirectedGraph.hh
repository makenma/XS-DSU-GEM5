#ifndef __CHI_DIRECTEDGRAPH__HH__
#define __CHI_DIRECTEDGRAPH__HH__

// Generic directed-graph base, adapted from LLVM's ADT/DirectedGraph.h.
// Uses CRTP (static polymorphism): concrete node/edge types derive from
// DGNode<DGEdge<...>> and pass themselves as the template parameter, so the
// base can call derived hooks (isEqualTo) at compile time with zero virtual
// overhead. std containers are used instead of LLVM's SmallVector/SetVector
// to keep this header dependency-free.

#include <algorithm>
#include <cassert>
#include <vector>

namespace gem5::Chi {

// ───────────────────────── Edge ─────────────────────────
// Points to a target node. Derived edge type provides the real semantics
// (transition condition, etc.) via isEqualTo.
template <class NodeType, class EdgeType>
class DGEdge
{
  public:
    DGEdge() = delete;
    DGEdge(NodeType &N) : TargetNode(N) {}
    DGEdge(const DGEdge &) = default;
    DGEdge &operator=(const DGEdge &) = default;
    virtual ~DGEdge() = default;

    bool operator==(const DGEdge &E) const {
        return getDerived().isEqualTo(E.getDerived());
    }
    bool operator!=(const DGEdge &E) const { return !(*this == E); }

    const NodeType &getTargetNode() const { return TargetNode; }
    NodeType &getTargetNode() { return TargetNode; }
    void setTargetNode(NodeType &N) { TargetNode = N; }

  protected:
    // Default: pointer identity. Derived class overrides for semantic equality.
    bool isEqualTo(const EdgeType &E) const { return this == &E; }

    EdgeType &getDerived() { return *static_cast<EdgeType *>(this); }
    const EdgeType &getDerived() const {
        return *static_cast<const EdgeType *>(this);
    }

    NodeType &TargetNode;
};

// ───────────────────────── Node ─────────────────────────
// Holds outgoing edges. Derived node type provides isEqualTo.
template <class NodeType, class EdgeType>
class DGNode
{
  public:
    using EdgeListTy = std::vector<EdgeType *>;
    using iterator = typename EdgeListTy::iterator;
    using const_iterator = typename EdgeListTy::const_iterator;

    DGNode() = default;
    DGNode(EdgeType &E) { Edges.push_back(&E); }
    virtual ~DGNode() = default;

    friend bool operator==(const NodeType &M, const NodeType &N) {
        return M.isEqualTo(N);
    }
    friend bool operator!=(const NodeType &M, const NodeType &N) {
        return !(M == N);
    }

    const EdgeType &front() const { return *Edges.front(); }
    EdgeType &front() { return *Edges.front(); }
    const EdgeType &back() const { return *Edges.back(); }
    EdgeType &back() { return *Edges.back(); }

    iterator begin() { return Edges.begin(); }
    iterator end() { return Edges.end(); }
    const_iterator begin() const { return Edges.begin(); }
    const_iterator end() const { return Edges.end(); }

    bool addEdge(EdgeType &E) {
        if (std::find(Edges.begin(), Edges.end(), &E) != Edges.end())
            return false;                       // already present
        Edges.push_back(&E);
        return true;
    }
    void removeEdge(EdgeType &E) {
        auto it = std::find(Edges.begin(), Edges.end(), &E);
        if (it != Edges.end())
            Edges.erase(it);
    }
    bool findEdgesTo(const NodeType &N, std::vector<EdgeType *> &EL) const {
        EL.clear();
        for (auto *E : Edges)
            if (E->getTargetNode() == N)
                EL.push_back(E);
        return !EL.empty();
    }
    bool hasEdgeTo(const NodeType &N) const {
        return std::any_of(Edges.begin(), Edges.end(),
                           [&](EdgeType *E) { return E->getTargetNode() == N; });
    }
    const EdgeListTy &getEdges() const { return Edges; }
    EdgeListTy &getEdges() { return Edges; }
    void clear() { Edges.clear(); }

  protected:
    bool isEqualTo(const NodeType &N) const { return this == &N; }
    NodeType &getDerived() { return *static_cast<NodeType *>(this); }
    const NodeType &getDerived() const {
        return *static_cast<const NodeType *>(this);
    }

    EdgeListTy Edges;
};

// ───────────────────────── Graph ─────────────────────────
// Owns node pointers. Derived graph types may add domain-specific queries.
template <class NodeType, class EdgeType>
class DirectedGraph
{
  public:
    using NodeListTy = std::vector<NodeType *>;
    using iterator = typename NodeListTy::iterator;
    using const_iterator = typename NodeListTy::const_iterator;

    DirectedGraph() = default;
    DirectedGraph(NodeType &N) { addNode(N); }
    virtual ~DirectedGraph() = default;

    const NodeType &front() const { return *Nodes.front(); }
    NodeType &front() { return *Nodes.front(); }
    const NodeType &back() const { return *Nodes.back(); }
    NodeType &back() { return *Nodes.back(); }

    iterator begin() { return Nodes.begin(); }
    iterator end() { return Nodes.end(); }
    const_iterator begin() const { return Nodes.begin(); }
    const_iterator end() const { return Nodes.end(); }

    size_t size() const { return Nodes.size(); }
    bool empty() const { return Nodes.empty(); }

    iterator findNode(const NodeType &N) {
        return std::find_if(Nodes.begin(), Nodes.end(),
                            [&](NodeType *p) { return *p == N; });
    }
    const_iterator findNode(const NodeType &N) const {
        return std::find_if(Nodes.begin(), Nodes.end(),
                            [&](NodeType *p) { return *p == N; });
    }
    bool addNode(NodeType &N) {
        if (findNode(N) != Nodes.end())
            return false;
        Nodes.push_back(&N);
        return true;
    }
    bool removeNode(NodeType &N) {
        auto it = findNode(N);
        if (it == Nodes.end())
            return false;
        std::vector<EdgeType *> EL;
        for (auto *Node : Nodes) {
            if (Node == &N)
                continue;
            Node->findEdgesTo(N, EL);
            for (auto *E : EL)
                Node->removeEdge(*E);
            EL.clear();
        }
        N.clear();
        Nodes.erase(it);
        return true;
    }
    // Connect Src→Dst with edge E (E must already target Dst).
    bool connect(NodeType &Src, NodeType &Dst, EdgeType &E) {
        assert(&E.getTargetNode() == &Dst && "edge target != Dst");
        return Src.addEdge(E);
    }
    bool findIncomingEdgesToNode(const NodeType &N,
                                 std::vector<EdgeType *> &EL) const {
        EL.clear();
        std::vector<EdgeType *> tmp;
        for (auto *Node : Nodes) {
            if (Node == &N)
                continue;
            Node->findEdgesTo(N, tmp);
            for (auto *E : tmp)
                EL.push_back(E);
            tmp.clear();
        }
        return !EL.empty();
    }

  protected:
    NodeListTy Nodes;
};

}  // namespace gem5::Chi

#endif  // __CHI_DIRECTEDGRAPH__HH__

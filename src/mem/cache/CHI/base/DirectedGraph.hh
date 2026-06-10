#ifndef __MEM_CACHE_CHI_BASE_DIRECTED_GRAPH_HH__
#define __MEM_CACHE_CHI_BASE_DIRECTED_GRAPH_HH__

#include <algorithm>
#include <cassert>
#include <utility>
#include <vector>

namespace gem5::Chi
{

template <class NodeType, class EdgeType>
class DGEdge
{
  public:
    DGEdge() = delete;

    explicit DGEdge(NodeType& n) : targetNode(n) {}
    explicit DGEdge(const DGEdge<NodeType, EdgeType>& edge)
        : targetNode(edge.targetNode)
    {}

    DGEdge<NodeType, EdgeType>&
    operator=(const DGEdge<NodeType, EdgeType>& edge)
    {
        targetNode = edge.targetNode;
        return *this;
    }

    bool
    operator==(const DGEdge& edge) const
    {
        return getDerived().isEqualTo(edge.getDerived());
    }

    bool
    operator!=(const DGEdge& edge) const
    {
        return !operator==(edge);
    }

    const NodeType&
    getTargetNode() const
    {
        return targetNode;
    }

    NodeType&
    getTargetNode()
    {
        return const_cast<NodeType&>(
            static_cast<const DGEdge<NodeType, EdgeType>&>(*this)
                .getTargetNode());
    }

    void
    setTargetNode(const NodeType& node)
    {
        targetNode = node;
    }

  protected:
    bool
    isEqualTo(const EdgeType& edge) const
    {
        return this == &edge;
    }

    EdgeType&
    getDerived()
    {
        return *static_cast<EdgeType*>(this);
    }

    const EdgeType&
    getDerived() const
    {
        return *static_cast<const EdgeType*>(this);
    }

    NodeType& targetNode;
};

template <class NodeType, class EdgeType>
class DGNode
{
  public:
    using EdgeList = std::vector<EdgeType*>;
    using iterator = typename EdgeList::iterator;
    using const_iterator = typename EdgeList::const_iterator;

    explicit DGNode(EdgeType& edge) : edges() { addEdge(edge); }
    DGNode() = default;

    explicit DGNode(const DGNode<NodeType, EdgeType>& node)
        : edges(node.edges)
    {}
    DGNode(DGNode<NodeType, EdgeType>&& node)
        : edges(std::move(node.edges))
    {}

    DGNode<NodeType, EdgeType>&
    operator=(const DGNode<NodeType, EdgeType>& node)
    {
        edges = node.edges;
        return *this;
    }

    DGNode<NodeType, EdgeType>&
    operator=(DGNode<NodeType, EdgeType>&& node)
    {
        edges = std::move(node.edges);
        return *this;
    }

    friend bool
    operator==(const NodeType& lhs, const NodeType& rhs)
    {
        return lhs.isEqualTo(rhs);
    }

    friend bool
    operator!=(const NodeType& lhs, const NodeType& rhs)
    {
        return !(lhs == rhs);
    }

    const_iterator begin() const { return edges.begin(); }
    const_iterator end() const { return edges.end(); }
    iterator begin() { return edges.begin(); }
    iterator end() { return edges.end(); }

    const EdgeType& front() const { return *edges.front(); }
    EdgeType& front() { return *edges.front(); }
    const EdgeType& back() const { return *edges.back(); }
    EdgeType& back() { return *edges.back(); }

    bool
    findEdgesTo(const NodeType& node, std::vector<EdgeType*>& edgeList) const
    {
        assert(edgeList.empty() && "Expected the edge list to be empty.");
        for (auto* edge : edges) {
            if (edge->getTargetNode() == node) {
                edgeList.push_back(edge);
            }
        }
        return !edgeList.empty();
    }

    bool
    addEdge(EdgeType& edge)
    {
        if (std::find_if(edges.begin(), edges.end(),
                [&edge](const EdgeType* existing) {
                    return *existing == edge;
                }) != edges.end()) {
            return false;
        }
        edges.push_back(&edge);
        return true;
    }

    void
    removeEdge(EdgeType& edge)
    {
        edges.erase(std::remove(edges.begin(), edges.end(), &edge),
                    edges.end());
    }

    bool
    hasEdgeTo(const NodeType& node) const
    {
        return findEdgeTo(node) != edges.end();
    }

    const EdgeList& getEdges() const { return edges; }
    EdgeList& getEdges()
    {
        return const_cast<EdgeList&>(
            static_cast<const DGNode<NodeType, EdgeType>&>(*this).edges);
    }

    void clear() { edges.clear(); }

  protected:
    bool
    isEqualTo(const NodeType& node) const
    {
        return this == &node;
    }

    NodeType& getDerived() { return *static_cast<NodeType*>(this); }
    const NodeType& getDerived() const
    {
        return *static_cast<const NodeType*>(this);
    }

    const_iterator
    findEdgeTo(const NodeType& node) const
    {
        return std::find_if(edges.begin(), edges.end(),
            [&node](const EdgeType* edge) {
                return edge->getTargetNode() == node;
            });
    }

    EdgeList edges;
};

template <class NodeType, class EdgeType>
class DirectedGraph
{
  protected:
    using NodeList = std::vector<NodeType*>;
    using EdgeList = std::vector<EdgeType*>;

  public:
    using iterator = typename NodeList::iterator;
    using const_iterator = typename NodeList::const_iterator;
    using DGraphType = DirectedGraph<NodeType, EdgeType>;

    DirectedGraph() = default;
    explicit DirectedGraph(NodeType& node) : nodes() { addNode(node); }
    DirectedGraph(const DGraphType& graph) : nodes(graph.nodes) {}
    DirectedGraph(DGraphType&& graph) : nodes(std::move(graph.nodes)) {}

    DGraphType&
    operator=(const DGraphType& graph)
    {
        nodes = graph.nodes;
        return *this;
    }

    DGraphType&
    operator=(DGraphType&& graph)
    {
        nodes = std::move(graph.nodes);
        return *this;
    }

    const_iterator begin() const { return nodes.begin(); }
    const_iterator end() const { return nodes.end(); }
    iterator begin() { return nodes.begin(); }
    iterator end() { return nodes.end(); }

    const NodeType& front() const { return *nodes.front(); }
    NodeType& front() { return *nodes.front(); }
    const NodeType& back() const { return *nodes.back(); }
    NodeType& back() { return *nodes.back(); }

    size_t size() const { return nodes.size(); }

    const_iterator
    findNode(const NodeType& node) const
    {
        return std::find_if(nodes.begin(), nodes.end(),
            [&node](const NodeType* candidate) {
                return *candidate == node;
            });
    }

    iterator
    findNode(const NodeType& node)
    {
        return std::find_if(nodes.begin(), nodes.end(),
            [&node](const NodeType* candidate) {
                return *candidate == node;
            });
    }

    bool
    addNode(NodeType& node)
    {
        if (findNode(node) != nodes.end()) {
            return false;
        }
        nodes.push_back(&node);
        return true;
    }

    bool
    findIncomingEdgesToNode(const NodeType& node,
                            std::vector<EdgeType*>& edgeList) const
    {
        assert(edgeList.empty() && "Expected the edge list to be empty.");
        EdgeList tempList;
        for (auto* candidate : nodes) {
            if (*candidate == node) {
                continue;
            }
            candidate->findEdgesTo(node, tempList);
            edgeList.insert(edgeList.end(), tempList.begin(), tempList.end());
            tempList.clear();
        }
        return !edgeList.empty();
    }

    bool
    removeNode(NodeType& node)
    {
        iterator it = findNode(node);
        if (it == nodes.end()) {
            return false;
        }

        EdgeList edgeList;
        for (auto* candidate : nodes) {
            if (*candidate == node) {
                continue;
            }
            candidate->findEdgesTo(node, edgeList);
            for (auto* edge : edgeList) {
                candidate->removeEdge(*edge);
            }
            edgeList.clear();
        }

        node.clear();
        nodes.erase(it);
        return true;
    }

    bool
    connect(NodeType& src, NodeType& dst, EdgeType& edge)
    {
        assert(findNode(src) != nodes.end() && "Src node should be present.");
        assert(findNode(dst) != nodes.end() && "Dst node should be present.");
        assert(edge.getTargetNode() == dst &&
               "Target of the given edge does not match Dst.");
        return src.addEdge(edge);
    }

  protected:
    NodeList nodes;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_BASE_DIRECTED_GRAPH_HH__

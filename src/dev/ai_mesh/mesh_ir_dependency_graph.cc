#include "dev/ai_mesh/mesh_ir_dependency_graph.hh"

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"

#include <algorithm>
#include <functional>
#include <queue>
#include <utility>

namespace gem5
{
namespace ai_mesh
{

using namespace mesh_diagnostics;

namespace
{

bool
fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

}

const std::vector<size_t> &
VerifiedDependencyGraph::canonicalOrder() const
{
    return canonicalOrder_;
}

bool
VerifiedDependencyGraph::happensBefore(
    size_t source, size_t target, bool &answer, MeshLoadError &error) const
{
    if (!admitted_ || source == 0 || target == 0 ||
        source >= adjacency_.size() || target >= adjacency_.size())
        return fail(E_ABI_BOUNDS, "dependency query node is invalid", error);

    error.code.clear();
    error.message.clear();
    answer = false;
    if (source == target || positions_[source] >= positions_[target])
        return true;

    std::vector<bool> seen(adjacency_.size());
    std::vector<size_t> worklist{source};
    seen[source] = true;
    while (!worklist.empty()) {
        const size_t node = worklist.back();
        worklist.pop_back();
        for (const size_t next : adjacency_[node]) {
            if (next == target) {
                answer = true;
                return true;
            }
            if (positions_[next] > positions_[target] || seen[next])
                continue;
            seen[next] = true;
            worklist.push_back(next);
        }
    }
    return true;
}

bool
buildVerifiedDependencyGraph(
    const std::vector<size_t> &orderRanks,
    const std::vector<DependencyGraphEdge> &edges,
    VerifiedDependencyGraph &out, MeshLoadError &error)
{
    const size_t nodeCount = orderRanks.size();
    std::vector<bool> rankSeen(nodeCount);
    for (const size_t rank : orderRanks) {
        if (rank >= nodeCount || rankSeen[rank])
            return fail(E_ABI_BOUNDS, "dependency graph rank is invalid", error);
        rankSeen[rank] = true;
    }

    std::vector<DependencyGraphEdge> normalized = edges;
    std::sort(
        normalized.begin(), normalized.end(),
        [](const DependencyGraphEdge &left, const DependencyGraphEdge &right) {
            return left.source != right.source ? left.source < right.source :
                left.target < right.target;
        });
    normalized.erase(
        std::unique(
            normalized.begin(), normalized.end(),
            [](const DependencyGraphEdge &left,
               const DependencyGraphEdge &right) {
                return left.source == right.source &&
                    left.target == right.target;
            }),
        normalized.end());

    VerifiedDependencyGraph candidate;
    candidate.adjacency_.resize(nodeCount + 1);
    std::vector<size_t> indegree(nodeCount + 1);
    for (const DependencyGraphEdge &edge : normalized) {
        if (edge.source == 0 || edge.target == 0 ||
            edge.source > nodeCount || edge.target > nodeCount)
            return fail(E_ABI_BOUNDS, "dependency graph edge is invalid", error);
    }
    for (const DependencyGraphEdge &edge : normalized) {
        candidate.adjacency_[edge.source].push_back(edge.target);
        ++indegree[edge.target];
    }

    using RankedNode = std::pair<size_t, size_t>;
    std::priority_queue<RankedNode, std::vector<RankedNode>,
                        std::greater<RankedNode>> ready;
    for (size_t node = 1; node <= nodeCount; ++node) {
        if (indegree[node] == 0)
            ready.emplace(orderRanks[node - 1], node);
    }
    candidate.canonicalOrder_.reserve(nodeCount);
    while (!ready.empty()) {
        const size_t node = ready.top().second;
        ready.pop();
        candidate.canonicalOrder_.push_back(node);
        for (const size_t next : candidate.adjacency_[node]) {
            --indegree[next];
            if (indegree[next] == 0)
                ready.emplace(orderRanks[next - 1], next);
        }
    }
    if (candidate.canonicalOrder_.size() != nodeCount)
        return fail(E_DEPENDENCY_CYCLE, "dependency graph contains a cycle", error);

    candidate.positions_.resize(nodeCount + 1);
    for (size_t position = 0; position < nodeCount; ++position)
        candidate.positions_[candidate.canonicalOrder_[position]] = position;
    candidate.admitted_ = true;
    out = std::move(candidate);
    error.code.clear();
    error.message.clear();
    return true;
}

}
}

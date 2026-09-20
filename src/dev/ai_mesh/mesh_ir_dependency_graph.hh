#ifndef GEM5_DEV_AI_MESH_MESH_IR_DEPENDENCY_GRAPH_HH
#define GEM5_DEV_AI_MESH_MESH_IR_DEPENDENCY_GRAPH_HH

#include <cstddef>
#include <vector>

#include "dev/ai_mesh/mesh_binary_types.hh"

namespace gem5
{
namespace ai_mesh
{

struct DependencyGraphEdge
{
    size_t source;
    size_t target;
};

class VerifiedDependencyGraph
{
  public:
    const std::vector<size_t> &canonicalOrder() const;
    bool happensBefore(
        size_t source, size_t target, bool &answer, MeshLoadError &error) const;

  private:
    bool admitted_ = false;
    std::vector<std::vector<size_t>> adjacency_;
    std::vector<size_t> positions_;
    std::vector<size_t> canonicalOrder_;

    friend bool buildVerifiedDependencyGraph(
        const std::vector<size_t> &, const std::vector<DependencyGraphEdge> &,
        VerifiedDependencyGraph &, MeshLoadError &);
};

bool buildVerifiedDependencyGraph(
    const std::vector<size_t> &orderRanks,
    const std::vector<DependencyGraphEdge> &edges,
    VerifiedDependencyGraph &out, MeshLoadError &error);

}
}

#endif

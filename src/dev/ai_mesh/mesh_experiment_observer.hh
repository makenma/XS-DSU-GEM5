#ifndef DEV_AI_MESH_MESH_EXPERIMENT_OBSERVER_HH
#define DEV_AI_MESH_MESH_EXPERIMENT_OBSERVER_HH

#include <cstdint>
#include <string>
#include <vector>

#include "sim/sim_object.hh"

namespace gem5
{
struct MeshExperimentObserverParams;
namespace axi { class AxiTargetAdapter; }
namespace ruby::garnet { class GarnetNetwork; }
namespace ai_mesh
{

class MeshDummyCore;
class AxiTensorDmaEngine;

class MeshExperimentObserver : public SimObject
{
  public:
    using Params = MeshExperimentObserverParams;
    explicit MeshExperimentObserver(const Params &p);
    void dumpSnapshot(const std::string &path) const;
    void dumpMemoryChecks(const std::string &path) const;
    bool drained() const;

  private:
    struct MemoryCheck
    {
        bool core;
        size_t index;
        uint64_t address;
        uint64_t size;
        uint8_t expected;
    };

    const std::vector<MeshDummyCore *> cores;
    const std::vector<axi::AxiTargetAdapter *> targets;
    ruby::garnet::GarnetNetwork *const network;
    std::vector<AxiTensorDmaEngine *> engines;
    std::vector<MemoryCheck> memoryChecks;
};

}
}

#endif

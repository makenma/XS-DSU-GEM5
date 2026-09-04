#ifndef DEV_AI_MESH_MESH_DISPATCHER_HH
#define DEV_AI_MESH_MESH_DISPATCHER_HH

#include <set>
#include <string>
#include <vector>

#include "base/statistics.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/program_scoreboard.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{
namespace ruby
{
class Network;
}
struct MeshDispatcherParams;

namespace ai_mesh
{

class MeshDummyCore;
class MeshProgramLoader;
class MockAxiTransport;
class NpuMemoryEndpoint;
class PeerSramAperture;

// Dispatches program instances at core edges, watches progress and writes
// the machine-readable result artifact (traffic oracle, per-core counters,
// conservation and drain state) consumed by the acceptance configs.
class MeshDispatcher : public ClockedObject
{
  public:
    using Params = MeshDispatcherParams;

    MeshDispatcher(const Params &p);

    void regStats() override;
    void startup() override;

    void notifyCoreHalted(uint16_t core_id);
    void latchInstanceError(MeshDummyCore *source, Tick tick);
    bool instanceErrored() const { return instance_error_latched; }
    void bindScoreboardTo(MeshDummyCore *core);
    void routePeerCommit(uint16_t peer_core, uint32_t transfer_id);
    bool registerFence(MeshDummyCore *core, uint32_t command_id,
                       uint32_t signal_event);
    void onDmaTagRetired(uint16_t core_id, uint64_t tag);
    void cancelFencesOf(uint16_t core_id);
    void armTransferExpectation(uint32_t transfer_id);

  private:
    struct StartEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit StartEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->dispatch(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.start"; }
    };

    struct WatchdogEvent : public Event
    {
        MeshDispatcher *dispatcher;
        explicit WatchdogEvent(MeshDispatcher *d) : Event(), dispatcher(d) {}
        void process() override { dispatcher->checkWatchdog(); }
        const char *description() const override
        { return "ai_mesh.dispatcher.watchdog"; }
    };

    void dispatch();
    void checkWatchdog();
    void checkAllHalted();
    void writeResultJson();
    void writeConservationJson(std::ofstream &out);
    bool anyCoreErrored() const;

    MeshProgramLoader *const loader;
    const std::vector<MeshDummyCore *> cores;
    MockAxiTransport *const transport;
    const std::vector<PeerSramAperture *> apertures;
    NpuMemoryEndpoint *const endpoint;
    ruby::Network *const network;
    const std::string result_json_path;
    const uint32_t total_instances;
    const uint64_t watchdog_ticks_value;

    struct PendingFence
    {
        uint16_t core_id;
        uint32_t command_id;
        uint32_t signal_event;
        std::set<std::pair<uint16_t, uint64_t>> waiting;
    };
    struct InstanceRecord
    {
        std::map<uint16_t, MeshDummyCore::InstanceLedger> cores;
    };
    std::vector<InstanceRecord> instance_records;

    std::vector<PendingFence> pending_fences;

    bool instance_error_latched = false;

    ProgramScoreboard scoreboard;
    uint32_t instance_counter = 0;
    bool started = false;
    std::set<uint16_t> halted_cores;
    uint64_t last_progress_snapshot = 0;
    uint32_t watchdog_fired_count = 0;

    statistics::Scalar instancesDispatched;
    statistics::Scalar instancesCompleted;

    StartEvent start_event;
    WatchdogEvent watchdog_event;
};

} // namespace ai_mesh
} // namespace gem5

#endif

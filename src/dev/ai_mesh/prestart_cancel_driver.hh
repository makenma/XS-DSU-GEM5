#ifndef DEV_AI_MESH_PRESTART_CANCEL_DRIVER_HH
#define DEV_AI_MESH_PRESTART_CANCEL_DRIVER_HH

#include <cstdint>
#include <string>
#include <vector>

#include "sim/sim_object.hh"

namespace gem5
{
struct PrestartCancelDriverParams;
namespace ai_mesh
{

class MeshDispatcher;
class MeshDummyCore;

// External action driver for the reservation-wait window.  It observes the
// real dispatcher state on the real event queue and, once the dispatcher
// reports a live reservation wait, applies the existing prestart cancel entry.
// It never fabricates reservation results and it bounds every phase: a run
// that never reaches the wait, never issues the cancel or never advances past
// the watchdog horizon terminates with an explicit failure cause.
class PrestartCancelDriver : public SimObject
{
  public:
    using Params = PrestartCancelDriverParams;
    explicit PrestartCancelDriver(const Params &p);

    void startup() override;

  private:
    class PollEvent : public Event
    {
      public:
        explicit PollEvent(PrestartCancelDriver *driver)
            : Event(), driver(driver)
        {}
        void process() override { driver->poll(); }
        const char *description() const override
        {
            return "ai_mesh.prestart_cancel.poll";
        }

      private:
        PrestartCancelDriver *const driver;
    };

    class ObserveEvent : public Event
    {
      public:
        explicit ObserveEvent(PrestartCancelDriver *driver)
            : Event(), driver(driver)
        {}
        void process() override { driver->observe(); }
        const char *description() const override
        {
            return "ai_mesh.prestart_cancel.observe";
        }

      private:
        PrestartCancelDriver *const driver;
    };

    void poll();
    void observe();
    uint64_t commandsIssued() const;
    void finish(const std::string &cause);

    MeshDispatcher *const dispatcher;
    const std::vector<MeshDummyCore *> cores;
    const std::string evidence_path;
    const Tick poll_interval;
    const Tick wait_timeout;
    const Tick observe_ticks;
    const bool cancel_on_wait;

    PollEvent poll_event;
    ObserveEvent observe_event;

    bool wait_observed = false;
    bool cancel_issued = false;
    Tick wait_tick = 0;
    Tick cancel_tick = 0;
    Tick observe_tick = 0;
    uint64_t cancelled_generation = 0;
    uint64_t generation_after_cancel = 0;
    uint64_t starts_before_cancel = 0;
    uint64_t starts_after_cancel = 0;
    uint64_t issued_before_cancel = 0;
    uint64_t issued_after_cancel = 0;
    size_t pending_after_cancel = 0;
    bool retry_after_cancel = false;
    bool watchdog_after_cancel = false;
    std::string failure;
};

}
}

#endif

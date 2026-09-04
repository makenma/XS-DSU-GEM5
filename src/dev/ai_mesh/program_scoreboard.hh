#ifndef DEV_AI_MESH_PROGRAM_SCOREBOARD_HH
#define DEV_AI_MESH_PROGRAM_SCOREBOARD_HH

#include <cstdint>
#include <map>
#include <set>
#include <utility>

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

// Program-instance-wide event scoreboard.  All participating cores share
// one scoreboard so a signal published on one core is visible to waiters
// on every core (the verifier guarantees program-global single producers
// for NORMAL events and fixed participant sets for barriers).  The owner
// resets it at each instance boundary; nothing survives a redispatch.
class ProgramScoreboard
{
  public:
    void reset()
    {
        signaled.clear();
        barriers.clear();
    }

    bool visible(uint32_t event_id) const
    {
        auto it = signaled.find(event_id);
        return it != signaled.end() && it->second;
    }

    void set_visible(uint32_t event_id) { signaled[event_id] = true; }

    // REPEAT generation resets retract a not-yet-consumed subrange event.
    void unpublish(uint32_t event_id) { signaled[event_id] = false; }

    // Barrier arrival with participant identity and generation (spec 6.2):
    // each participant of a {event, generation} rendezvous arrives exactly
    // once; the generation closes when full, and the next REPEAT generation
    // rendezvous under a fresh key.
    bool arrive(uint32_t event_id, uint32_t generation, uint32_t participant,
                uint32_t expected)
    {
        BarrierState &state = barriers[std::make_pair(event_id, generation)];
        if (state.expected == 0)
            state.expected = expected;
        fatal_if(state.expected != expected,
                 "barrier event %u expected_arrivals changed mid-rendezvous",
                 event_id);
        fatal_if(state.closed,
                 "barrier event %u over-arrived (generation %u closed)",
                 event_id, generation);
        fatal_if(!state.arrivals.insert(participant).second,
                 "barrier event %u duplicate participant %u", event_id,
                 participant);
        if (state.arrivals.size() == state.expected) {
            state.closed = true;
            return true;
        }
        return false;
    }

  private:
    struct BarrierState
    {
        uint32_t expected = 0;
        std::set<uint32_t> arrivals;
        bool closed = false;
    };
    std::map<uint32_t, bool> signaled;
    std::map<std::pair<uint32_t, uint32_t>, BarrierState> barriers;
};

} // namespace ai_mesh
} // namespace gem5

#endif

#ifndef DEV_AI_MESH_PROGRAM_SCOREBOARD_HH
#define DEV_AI_MESH_PROGRAM_SCOREBOARD_HH

#include <cstdint>
#include <map>
#include <set>
#include <utility>

#include "base/logging.hh"
#include "dev/ai_mesh/runtime_key.hh"

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

    bool visible(RuntimeObjectKey event_id) const
    {
        auto it = signaled.find(event_id);
        return it != signaled.end() && it->second;
    }

    void set_visible(RuntimeObjectKey event_id) { signaled[event_id] = true; }

    // REPEAT generation resets retract a not-yet-consumed subrange event.
    void unpublish(RuntimeObjectKey event_id) { signaled[event_id] = false; }

    // Barrier arrival with participant identity and generation (spec 6.2):
    // each participant of a {event, generation} rendezvous arrives exactly
    // once; the generation closes when full, and the next REPEAT generation
    // rendezvous under a fresh key.
    bool arrive(RuntimeObjectKey event_id, RepeatGeneration generation,
                RuntimeObjectKey participant, uint32_t expected)
    {
        BarrierState &state = barriers[std::make_pair(event_id, generation)];
        if (state.expected == 0)
            state.expected = expected;
        fatal_if(state.expected != expected,
                 "barrier event %u expected_arrivals changed mid-rendezvous",
                 event_id.ordinal);
        fatal_if(state.closed,
                 "barrier event %u over-arrived (generation %u closed)",
                 event_id.ordinal, generation.value());
        fatal_if(!state.arrivals.insert(participant).second,
                 "barrier event %u duplicate participant %u", event_id.ordinal,
                 participant.ordinal);
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
        std::set<RuntimeObjectKey> arrivals;
        bool closed = false;
    };
    std::map<RuntimeObjectKey, bool> signaled;
    std::map<std::pair<RuntimeObjectKey, RepeatGeneration>, BarrierState>
        barriers;
};

} // namespace ai_mesh
} // namespace gem5

#endif

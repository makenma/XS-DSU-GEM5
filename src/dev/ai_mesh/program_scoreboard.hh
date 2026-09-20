#ifndef DEV_AI_MESH_PROGRAM_SCOREBOARD_HH
#define DEV_AI_MESH_PROGRAM_SCOREBOARD_HH

#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "base/logging.hh"
#include "base/types.hh"

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
    enum class BarrierPhase
    {
        Collecting,
        Released,
        Cancelled,
    };

    struct BarrierArrival
    {
        uint32_t participant = 0;
        Tick tick = 0;
    };

    struct BarrierGroup
    {
        uint32_t event_id = 0;
        uint32_t generation = 0;
        uint32_t expected = 0;
        std::vector<BarrierArrival> arrivals;
        BarrierPhase phase = BarrierPhase::Collecting;
        Tick release_tick = 0;
        Tick cancel_tick = 0;
    };

    void reset()
    {
        signaled.clear();
        groups.clear();
        open_index.clear();
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
    // once; the last legal arrival releases the group exactly once, and the
    // next REPEAT generation rendezvous under a fresh key.
    bool arrive(uint32_t event_id, uint32_t generation, uint32_t participant,
                uint32_t expected, Tick tick)
    {
        for (const BarrierGroup &finished : groups)
            if (finished.event_id == event_id &&
                finished.generation == generation &&
                finished.phase != BarrierPhase::Collecting)
                fatal("barrier event %u generation %u already %s", event_id,
                      generation,
                      finished.phase == BarrierPhase::Released ? "released"
                                                               : "cancelled");
        const auto key = std::make_pair(event_id, generation);
        auto open = open_index.find(key);
        if (open == open_index.end()) {
            BarrierGroup created;
            created.event_id = event_id;
            created.generation = generation;
            created.expected = expected;
            groups.push_back(std::move(created));
            open_index[key] = groups.size() - 1;
            open = open_index.find(key);
        }
        BarrierGroup &group = groups[open->second];
        fatal_if(group.phase != BarrierPhase::Collecting,
                 "barrier event %u generation %u already %s", event_id,
                 generation,
                 group.phase == BarrierPhase::Released ? "released"
                                                       : "cancelled");
        fatal_if(group.expected != expected,
                 "barrier event %u expected_arrivals changed mid-rendezvous",
                 event_id);
        for (const BarrierArrival &arrival : group.arrivals)
            fatal_if(arrival.participant == participant,
                     "barrier event %u duplicate participant %u", event_id,
                     participant);
        group.arrivals.push_back(BarrierArrival{participant, tick});
        if (group.arrivals.size() == group.expected) {
            group.phase = BarrierPhase::Released;
            group.release_tick = tick;
            open_index.erase(key);
            return true;
        }
        return false;
    }

    // Instance error cancels every still-collecting group; the already
    // recorded arrivals stay as history and no missing arrival is forged.
    void cancelOpen(Tick tick)
    {
        for (auto &entry : open_index) {
            BarrierGroup &group = groups[entry.second];
            group.phase = BarrierPhase::Cancelled;
            group.cancel_tick = tick;
        }
        open_index.clear();
    }

    struct BarrierSnapshot
    {
        uint32_t event_id = 0;
        uint32_t generation = 0;
        uint32_t expected = 0;
        std::set<uint32_t> arrivals;
        BarrierPhase phase = BarrierPhase::Collecting;
        Tick release_tick = 0;
        Tick cancel_tick = 0;
    };
    std::vector<BarrierSnapshot> barrierGroups() const
    {
        std::vector<BarrierSnapshot> snapshot;
        for (const BarrierGroup &group : groups) {
            BarrierSnapshot row;
            row.event_id = group.event_id;
            row.generation = group.generation;
            row.expected = group.expected;
            for (const BarrierArrival &arrival : group.arrivals)
                row.arrivals.insert(arrival.participant);
            row.phase = group.phase;
            row.release_tick = group.release_tick;
            row.cancel_tick = group.cancel_tick;
            snapshot.push_back(std::move(row));
        }
        return snapshot;
    }

    std::vector<BarrierSnapshot> openBarriers() const
    {
        std::vector<BarrierSnapshot> snapshot;
        for (const BarrierSnapshot &row : barrierGroups())
            if (row.phase == BarrierPhase::Collecting)
                snapshot.push_back(row);
        return snapshot;
    }

    const std::vector<BarrierGroup> &barrierHistory() const { return groups; }

  private:
    std::map<uint32_t, bool> signaled;
    std::vector<BarrierGroup> groups;
    std::map<std::pair<uint32_t, uint32_t>, size_t> open_index;
};

} // namespace ai_mesh
} // namespace gem5

#endif

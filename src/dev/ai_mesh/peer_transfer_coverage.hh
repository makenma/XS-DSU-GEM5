#ifndef DEV_AI_MESH_PEER_TRANSFER_COVERAGE_HH
#define DEV_AI_MESH_PEER_TRANSFER_COVERAGE_HH

#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

// One real target commit that changed the coverage of a live transfer.
struct PeerTransferStage
{
    Tick tick = 0;
    uint64_t covered_bytes = 0;
    uint64_t uncovered_bytes = 0;
    uint32_t transactions = 0;
    bool notified = false;
};

// One real target commit that landed new bytes on a transfer: when it landed
// and, through its single-use transaction identity, which destination runs it
// newly covered.  A snapshot at tick T may only be explained by the events
// that had already landed by T, never by the transfer's final coverage.
struct PeerLandingEvent
{
    Tick tick = 0;
    uint64_t txn_uid = 0;
    std::vector<std::pair<uint64_t, uint64_t>> ranges;
};

// Coverage facts of one observed transfer: the receiving tile's own record,
// archived per instance frame and exported as the result JSON's per-frame peer
// evidence.  ``transaction_uids`` are the real AXI transaction identities whose
// commits covered this transfer, so a completion can be traced to the
// transactions that produced it instead of to a count.
struct PeerTransferCoverage
{
    uint32_t transfer_id = 0;
    Tick commit_tick = 0;
    uint64_t expected_bytes = 0;
    uint64_t covered_bytes = 0;
    uint64_t duplicate_notifications = 0;
    uint64_t replayed_lanes = 0;
    uint64_t uncovered_bytes = 0;
    uint32_t transactions = 0;
    bool notified = false;
    bool abandoned = false;
    std::vector<uint64_t> transaction_uids;
    // The exact destination runs this transfer's own commits landed, in
    // address order: a partially committed transfer can be split into what
    // really landed and what still holds its admitted initial content.
    std::vector<std::pair<uint64_t, uint64_t>> covered_ranges;
    std::vector<PeerTransferStage> stages;
    std::vector<PeerLandingEvent> landing_events;
};

// Receiver-side ownership of transfer coverage.  The sender arms one
// expectation per admitted transfer and every real write commit is attributed
// to it; a completion is only published once the expectation is covered by its
// own accepted transactions.  A transaction identity is single-use: a replayed
// or foreign commit never extends a live expectation, so a retired transfer's
// callback cannot stand in for a new transfer that reuses the same address.
class PeerTransferCoverageTable
{
  public:
    struct CommitFacts
    {
        Tick tick = 0;
        uint64_t base_address = 0;
        uint64_t bus_bytes = 1;
        std::vector<uint64_t> lanes;
        bool has_meta = false;
        uint64_t txn_uid = 0;
    };

    struct CommitResult
    {
        uint64_t newly_covered = 0;
        uint64_t unaccounted = 0;
        uint64_t replayed_lanes = 0;
        std::vector<uint32_t> completed;
        std::vector<uint32_t> staged;
    };

    void expect(uint32_t transfer_id,
                const std::vector<std::pair<uint64_t, uint64_t>> &ranges);
    void abandonAll();
    void beginInstance();
    CommitResult commit(const CommitFacts &facts);

    bool live() const;
    bool expects(uint32_t transfer_id) const;
    bool notified(uint32_t transfer_id) const;
    uint64_t expectedBytes(uint32_t transfer_id) const;
    uint64_t replayedLanes() const { return replayed_lanes; }
    std::vector<PeerTransferCoverage> rows() const;
    const std::map<uint32_t, Tick> &commitTicks() const
    {
        return transfer_commit_ticks;
    }

  private:
    struct Expectation
    {
        std::vector<std::pair<uint64_t, uint64_t>> ranges;
        // Per-range coverage bitmap, one entry per admitted byte.
        std::vector<std::vector<uint8_t>> covered;
        std::vector<uint64_t> transaction_uids;
        std::set<uint64_t> transactions;
        uint64_t expected = 0;
        uint64_t observed = 0;
        uint64_t duplicates = 0;
        uint64_t replayed = 0;
        bool notified = false;
        bool abandoned = false;
        Tick commit_tick = 0;
        std::vector<PeerTransferStage> stages;
        std::vector<PeerLandingEvent> landing_events;
    };

    // First attribution of one real transaction identity: the transfer whose
    // coverage it extended and the instance it belonged to.  A transaction is
    // never re-attributed, so a replay can never cover a later expectation, and
    // a transaction from an earlier instance can never cover this instance's
    // expectation even when the same transfer id is armed again.
    struct TransactionOwner
    {
        uint32_t transfer_id = 0;
        uint32_t instance = 0;
    };
    std::map<uint64_t, TransactionOwner> transaction_owners;

    std::map<uint32_t, Expectation> expectations;
    std::map<uint32_t, Tick> transfer_commit_ticks;
    uint32_t instance_counter = 0;
    uint64_t replayed_lanes = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

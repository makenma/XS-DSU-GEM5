#include "dev/ai_mesh/peer_transfer_coverage.hh"

#include "base/logging.hh"

namespace gem5
{
namespace ai_mesh
{

void
PeerTransferCoverageTable::expect(
    uint32_t transfer_id, const std::vector<std::pair<uint64_t, uint64_t>> &ranges)
{
    fatal_if(ranges.empty(), "transfer %u has no ranges", transfer_id);
    // The sender arms a transfer once per admitted descriptor it submits, so a
    // repeated installation of the same admitted range set states the same
    // expectation; a different set is a real conflict.
    auto installed = expectations.find(transfer_id);
    if (installed != expectations.end()) {
        fatal_if(installed->second.ranges != ranges,
                 "transfer %u was expected with a different admitted range set",
                 transfer_id);
        return;
    }
    Expectation expectation;
    for (const auto &range : ranges) {
        fatal_if(range.first >= range.second,
                 "transfer %u has an empty range", transfer_id);
        for (const auto &other : expectation.ranges)
            fatal_if(range.first < other.second && other.first < range.second,
                     "transfer %u ranges overlap", transfer_id);
        for (const auto &live : expectations) {
            // A retired transfer stays in the table so its coverage remains
            // reportable; only a still-live expectation can claim new bytes.
            if (live.second.notified || live.second.abandoned)
                continue;
            for (const auto &other : live.second.ranges)
                fatal_if(
                    range.first < other.second && other.first < range.second,
                    "transfer %u range overlaps live transfer %u",
                    transfer_id, live.first);
        }
        expectation.ranges.push_back(range);
        expectation.expected += range.second - range.first;
        expectation.covered.emplace_back(range.second - range.first, 0);
    }
    expectations[transfer_id] = std::move(expectation);
}

void
PeerTransferCoverageTable::abandonAll()
{
    for (auto &[transfer_id, expectation] : expectations)
        if (!expectation.notified)
            expectation.abandoned = true;
}

void
PeerTransferCoverageTable::beginInstance()
{
    instance_counter++;
    transfer_commit_ticks.clear();
    expectations.clear();
}

PeerTransferCoverageTable::CommitResult
PeerTransferCoverageTable::commit(const CommitFacts &facts)
{
    CommitResult result;
    // Every strobed lane is attributed exactly once: a byte of a live
    // expectation is new coverage or a duplicate notification of it, a byte
    // only a retired expectation still knows about is a replayed notification
    // of that retired transfer (never new coverage), and any other byte is
    // committed but unaccounted.
    std::set<uint32_t> touched;
    std::map<uint32_t, uint64_t> observed_before;
    for (const auto &[transfer_id, expectation] : expectations)
        observed_before.emplace(transfer_id, expectation.observed);

    // The addresses this commit newly covered, per expectation: they become
    // one landing event, the only fact a snapshot taken before the next commit
    // may be explained with.
    std::map<uint32_t, std::set<uint64_t>> fresh_lanes;
    for (uint64_t address : facts.lanes) {
        bool claimed = false;
        // A live expectation that is still waiting for bytes owns this address.
        for (auto &[transfer_id, expectation] : expectations) {
            if (expectation.notified)
                continue;
            for (size_t range = 0; range < expectation.ranges.size(); range++) {
                const auto &bounds = expectation.ranges[range];
                if (address < bounds.first || address >= bounds.second)
                    continue;
                const uint64_t offset = address - bounds.first;
                // A transaction identity is single-use: it can only ever cover
                // the transfer it first extended, so a replayed or foreign
                // commit can never complete a later expectation that reuses
                // the same address, in this instance or a later one.
                if (facts.has_meta) {
                    auto owner = transaction_owners.find(facts.txn_uid);
                    if (owner != transaction_owners.end() &&
                        (owner->second.transfer_id != transfer_id ||
                         owner->second.instance != instance_counter)) {
                        expectation.replayed++;
                        replayed_lanes++;
                        result.replayed_lanes++;
                        claimed = true;
                        break;
                    }
                    transaction_owners.emplace(
                        facts.txn_uid,
                        TransactionOwner{transfer_id, instance_counter});
                }
                if (expectation.covered[range][offset])
                    expectation.duplicates++;
                else {
                    expectation.covered[range][offset] = 1;
                    expectation.observed++;
                    result.newly_covered++;
                    fresh_lanes[transfer_id].insert(address);
                }
                touched.insert(transfer_id);
                claimed = true;
                break;
            }
            if (claimed)
                break;
        }
        if (claimed)
            continue;
        for (auto &[transfer_id, expectation] : expectations) {
            if (!expectation.notified)
                continue;
            for (const auto &bounds : expectation.ranges) {
                if (address < bounds.first || address >= bounds.second)
                    continue;
                expectation.duplicates++;
                claimed = true;
                break;
            }
            if (claimed)
                break;
        }
        if (!claimed)
            result.unaccounted++;
    }

    for (auto &[transfer_id, addresses] : fresh_lanes) {
        PeerLandingEvent event;
        event.tick = facts.tick;
        event.txn_uid = facts.has_meta ? facts.txn_uid : 0;
        for (uint64_t address : addresses) {
            if (!event.ranges.empty() &&
                event.ranges.back().first + event.ranges.back().second
                    == address) {
                event.ranges.back().second++;
                continue;
            }
            event.ranges.emplace_back(address, 1);
        }
        expectations.at(transfer_id).landing_events.push_back(
            std::move(event));
    }

    std::vector<uint32_t> completed;
    for (auto &[transfer_id, expectation] : expectations) {
        if (touched.count(transfer_id) && facts.has_meta) {
            if (expectation.transactions.insert(facts.txn_uid).second)
                expectation.transaction_uids.push_back(facts.txn_uid);
        }
        if (!expectation.notified &&
            expectation.observed == expectation.expected)
            completed.push_back(transfer_id);
        // Only a commit that really extended the coverage is a stage: a
        // replayed notification of already covered bytes adds no progress.
        if (touched.count(transfer_id) &&
            expectation.observed != observed_before.at(transfer_id)) {
            PeerTransferStage stage;
            stage.tick = facts.tick;
            stage.covered_bytes = expectation.observed;
            stage.uncovered_bytes = expectation.expected - expectation.observed;
            stage.transactions = uint32_t(expectation.transactions.size());
            stage.notified = expectation.observed == expectation.expected;
            expectation.stages.push_back(stage);
            result.staged.push_back(transfer_id);
        }
    }

    for (uint32_t transfer_id : completed) {
        Expectation &expectation = expectations.at(transfer_id);
        if (expectation.notified)
            continue;
        expectation.notified = true;
        expectation.commit_tick = facts.tick;
        transfer_commit_ticks[transfer_id] = facts.tick;
        result.completed.push_back(transfer_id);
    }
    return result;
}

bool
PeerTransferCoverageTable::live() const
{
    for (const auto &[transfer_id, expectation] : expectations)
        if (!expectation.notified && !expectation.abandoned)
            return true;
    return false;
}

bool
PeerTransferCoverageTable::expects(uint32_t transfer_id) const
{
    return expectations.count(transfer_id) != 0;
}

bool
PeerTransferCoverageTable::notified(uint32_t transfer_id) const
{
    auto found = expectations.find(transfer_id);
    return found != expectations.end() && found->second.notified;
}

uint64_t
PeerTransferCoverageTable::expectedBytes(uint32_t transfer_id) const
{
    auto found = expectations.find(transfer_id);
    return found == expectations.end() ? 0 : found->second.expected;
}

std::vector<PeerTransferCoverage>
PeerTransferCoverageTable::rows() const
{
    std::vector<PeerTransferCoverage> rows;
    for (const auto &[transfer_id, expectation] : expectations) {
        PeerTransferCoverage row;
        row.transfer_id = transfer_id;
        row.commit_tick = expectation.commit_tick;
        row.expected_bytes = expectation.expected;
        row.covered_bytes = expectation.observed;
        row.duplicate_notifications = expectation.duplicates;
        row.replayed_lanes = expectation.replayed;
        row.uncovered_bytes = expectation.expected - expectation.observed;
        row.transactions = uint32_t(expectation.transactions.size());
        row.transaction_uids = expectation.transaction_uids;
        for (size_t range = 0; range < expectation.ranges.size(); range++) {
            const auto &bounds = expectation.ranges[range];
            uint64_t run_start = 0;
            bool in_run = false;
            for (uint64_t offset = 0;
                 offset <= bounds.second - bounds.first; offset++) {
                const bool covered = offset < bounds.second - bounds.first
                    && expectation.covered[range][offset];
                if (covered && !in_run) {
                    run_start = bounds.first + offset;
                    in_run = true;
                } else if (!covered && in_run) {
                    row.covered_ranges.emplace_back(
                        run_start, bounds.first + offset - run_start);
                    in_run = false;
                }
            }
        }
        row.notified = expectation.notified;
        row.abandoned = expectation.abandoned;
        row.stages = expectation.stages;
        row.landing_events = expectation.landing_events;
        rows.push_back(row);
    }
    return rows;
}

} // namespace ai_mesh
} // namespace gem5

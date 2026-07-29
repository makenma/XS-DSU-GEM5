#ifndef __HNF_SLCSF_BACKEND_HH__
#define __HNF_SLCSF_BACKEND_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <unordered_map>
#include <vector>

#include "mem/cache/CHI/HnfSLCSFRequest.hh"

namespace gem5::Chi
{

class HnfSLCSFBackend
{
  public:
    using SeqId = uint64_t;

    struct ArraySnapshot
    {
        bool hit = false;
        uint32_t set = 0;
        uint32_t way = 0;
        uint64_t generation = 0;
        uint64_t replacementStamp = 0;
    };

    struct LookupSnapshot
    {
        uint64_t lookupEpoch = 0;
        ArraySnapshot slc{};
        ArraySnapshot sf{};
    };

    struct LookupObservation
    {
        HnfSlcLookupResult result{};
        LookupSnapshot snapshot{};
    };

    struct SeqVictim
    {
        SeqId id = 0;
        uint64_t blockAddr = 0;
        uint32_t homeNodeId = 0;
        HnfSfState state = HnfSfState::I;
        uint32_t owner = 0;
        uint64_t sharers = 0;
        bool issued = false;
    };

    HnfSLCSFBackend(uint32_t block_size, uint32_t slc_num_sets,
                    uint32_t slc_num_ways, uint32_t sf_num_sets,
                    uint32_t sf_num_ways, uint32_t seq_entries = 8);

    /** Inspect lookup semantics and version snapshots without side effects. */
    LookupObservation probe(const HnfSlcLookupReq& req) const;

    /** Probe and commit one replacement access when the result is not Replay. */
    HnfSlcLookupResult lookup(
        const HnfSlcLookupReq& req, LookupSnapshot* snapshot = nullptr);
    bool tryReserveSfResources(uint32_t entry, uint64_t block_addr,
                               PocqTxnKind txn,
                               const LookupSnapshot* target = nullptr);
    void releaseSfResources(uint32_t entry);
    bool hasSfReservation(uint32_t entry) const;
    size_t sfReservationCount() const { return sfReservations.size(); }
    size_t seqReservationCount() const { return reservedSeqSlots; }
    void commitRead(uint64_t block_addr, uint32_t requester,
                    PocqTxnKind txn, const std::vector<uint8_t>& data,
                    bool data_dirty, uint32_t home_node_id = 0,
                    const LookupSnapshot* target = nullptr,
                    std::optional<uint32_t> reservation_owner = std::nullopt,
                    SeqVictim* sf_victim = nullptr,
                    const SlcSfSlcVictim* preserved_victim = nullptr);
    void completeMaintenance(uint64_t block_addr, uint32_t requester,
                             PocqTxnKind txn,
                             uint32_t home_node_id = 0,
                             const LookupSnapshot* target = nullptr,
                             std::optional<uint32_t> reservation_owner =
                                 std::nullopt,
                             SeqVictim* sf_victim = nullptr);
    void removeSharer(uint64_t block_addr, uint32_t requester);
    void fillCleanShared(uint64_t block_addr, uint32_t requester,
                         const std::vector<uint8_t>& data,
                         const LookupSnapshot* target = nullptr,
                         std::optional<uint32_t> reservation_owner =
                             std::nullopt,
                         SeqVictim* sf_victim = nullptr,
                         const SlcSfSlcVictim* preserved_victim = nullptr);
    void writeLine(uint64_t block_addr, uint32_t requester,
                   const std::vector<uint8_t>& data,
                   PocqTxnKind txn = PocqTxnKind::WriteUnique,
                   uint32_t home_node_id = 0,
                   const LookupSnapshot* target = nullptr,
                   std::optional<uint32_t> reservation_owner = std::nullopt,
                   SeqVictim* sf_victim = nullptr,
                   const SlcSfSlcVictim* preserved_victim = nullptr);
    void flushSf(uint64_t block_addr);
    void flushL3(uint64_t block_addr);
    void writeL3FlushSf(uint64_t block_addr, uint32_t requester,
                        const std::vector<uint8_t>& data,
                        const LookupSnapshot* target = nullptr,
                        const SlcSfSlcVictim* preserved_victim = nullptr);

    bool hasPendingSeq() const { return !seqPending.empty(); }
    SeqVictim frontPendingSeq() const;
    void markSeqIssued(SeqId id);
    void completeSfEvict(SeqId id, const std::vector<uint8_t>& data,
                         bool dirty_data);
    bool seqContains(uint64_t block_addr) const;
    bool seqCompletionMatches(SeqId id, uint64_t block_addr) const;
    size_t seqOccupancy() const;
    size_t seqCapacity() const { return seq.size(); }
    uint64_t currentLookupEpoch() const { return lookupEpoch; }
    uint64_t currentLookupAccessCount() const { return lookupAccessCount; }
    uint32_t blockSizeBytes() const { return blockSize; }

    /** Report an unsupported dirty SLC displacement without changing state. */
    bool slcAllocationWouldDisplaceDirty(
        uint64_t block_addr, const LookupSnapshot* target = nullptr) const;
    bool writeLineWouldDisplaceDirty(
        uint64_t block_addr, uint32_t requester, PocqTxnKind txn,
        const LookupSnapshot* target = nullptr) const;
    std::optional<uint64_t> dirtySlcVictimAddress(
        uint64_t block_addr, const LookupSnapshot& target) const;
    SlcSfSlcVictim snapshotDirtySlcVictim(
        uint64_t block_addr, const LookupSnapshot& target) const;

    /** Validate a prior lookup snapshot without consulting replacement order. */
    bool validateLookupSnapshot(
        uint64_t block_addr, const LookupSnapshot& snapshot) const;

    /** Return whether the token-selected SF way is a valid displacement. */
    bool sfAllocationWouldDisplace(
        uint64_t block_addr, const LookupSnapshot& target) const;
    bool sfAllocationBlockedBySeq(
        uint64_t block_addr, const LookupSnapshot& target,
        std::optional<uint32_t> reservation_owner = std::nullopt) const;

    /** Invalidate every outstanding lookup snapshot at a lifecycle boundary. */
    void invalidateCommitTokens();

    /** Check the cross-array state invariant after a staged mutation. */
    void checkLineInvariant(uint64_t block_addr) const;

    bool isBusy() const { return seqOccupancy() != 0; }

  private:
    struct SlcLine
    {
        bool valid = false;
        uint64_t tag = 0;
        HnfSlcState state = HnfSlcState::I;
        uint32_t owner = 0;
        uint64_t generation = 0;
        uint64_t lastUse = 0;
        std::vector<uint8_t> data;
    };

    struct SfLine
    {
        bool valid = false;
        uint64_t tag = 0;
        HnfSfState state = HnfSfState::I;
        uint32_t homeNodeId = 0;
        uint32_t owner = 0;
        uint64_t sharers = 0;
        uint64_t generation = 0;
        uint64_t lastUse = 0;
    };

    struct SeqEntry
    {
        bool valid = false;
        SeqVictim victim{};
    };

    struct SfReservation
    {
        uint32_t set = 0;
        uint64_t blockAddr = 0;
        bool seqSlot = false;
    };

    uint32_t blockSize = 64;
    uint32_t slcSets = 1024;
    uint32_t slcWays = 16;
    uint32_t sfSets = 1024;
    uint32_t sfWays = 16;

    std::vector<std::vector<SlcLine>> slc;
    std::vector<std::vector<SfLine>> sf;
    std::vector<SeqEntry> seq;
    std::deque<SeqId> seqPending;
    std::vector<int64_t> sfReservationOwners;
    std::unordered_map<uint32_t, SfReservation> sfReservations;
    size_t reservedSeqSlots = 0;
    uint64_t accessCounter = 0;
    uint64_t lookupEpoch = 1;
    uint64_t lookupAccessCount = 0;
    SeqId nextSeqId = 1;

    uint64_t blockNumber(uint64_t block_addr) const;
    uint64_t slcTag(uint64_t block_addr) const;
    uint64_t sfTag(uint64_t block_addr) const;
    uint32_t slcSet(uint64_t block_addr) const;
    uint32_t sfSet(uint64_t block_addr) const;
    uint64_t requesterMask(uint32_t requester) const;
    uint64_t sfBlockAddr(uint64_t tag, uint32_t set) const;

    SlcLine* findSlc(uint64_t block_addr);
    const SlcLine* findSlc(uint64_t block_addr) const;
    SfLine* findSf(uint64_t block_addr);
    const SfLine* findSf(uint64_t block_addr) const;
    SlcLine& allocateSlc(uint64_t block_addr,
                         const ArraySnapshot* target = nullptr,
                         const SlcSfSlcVictim* preserved_victim = nullptr);
    SfLine& allocateSf(uint64_t block_addr, uint32_t home_node_id,
                       const ArraySnapshot* target = nullptr,
                       std::optional<uint32_t> reservation_owner =
                           std::nullopt,
                       SeqVictim* sf_victim = nullptr);
    const SfLine* selectSfVictim(
        uint64_t block_addr, const ArraySnapshot* target = nullptr) const;
    LookupSnapshot snapshotLookup(uint64_t block_addr) const;
    void recordAccess(uint64_t block_addr);
    bool sfAllocationWouldReplay(
        uint64_t block_addr,
        std::optional<uint32_t> reservation_owner = std::nullopt) const;
    bool txnTouchesSf(PocqTxnKind txn) const;
    bool txnMayAllocateSf(PocqTxnKind txn) const;
    SeqId installSeqVictim(
        uint32_t set, const SfLine& victim, SeqVictim* snapshot);
    void consumeSeqReservation(
        uint64_t block_addr,
        std::optional<uint32_t> reservation_owner = std::nullopt);
    void assertSeqAccounting() const;
    SeqEntry* findSeq(SeqId id);
    const SeqEntry* findSeq(SeqId id) const;
    void installSlc(uint64_t block_addr, HnfSlcState state,
                    uint32_t requester, const std::vector<uint8_t>& data,
                    const ArraySnapshot* target = nullptr,
                    const SlcSfSlcVictim* preserved_victim = nullptr);
    void invalidateSlc(uint64_t block_addr);
    void invalidateSf(uint64_t block_addr);
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_BACKEND_HH__

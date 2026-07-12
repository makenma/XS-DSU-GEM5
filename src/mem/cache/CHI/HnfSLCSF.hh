#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <vector>

#include "mem/cache/CHI/HnfCcTypes.hh"

namespace gem5::Chi
{

class HnfSLCSF
{
  public:
    using SeqId = uint64_t;

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

    HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
             uint32_t slc_num_ways, uint32_t sf_num_sets,
             uint32_t sf_num_ways, uint32_t seq_entries = 8);

    HnfSlcLookupResult lookup(const HnfSlcLookupReq& req);
    void commitRead(uint64_t block_addr, uint32_t requester,
                    PocqTxnKind txn, const std::vector<uint8_t>& data,
                    bool data_dirty, uint32_t home_node_id = 0);
    void completeMaintenance(uint64_t block_addr, uint32_t requester,
                             PocqTxnKind txn,
                             uint32_t home_node_id = 0);
    void removeSharer(uint64_t block_addr, uint32_t requester);
    void fillCleanShared(uint64_t block_addr, uint32_t requester,
                         const std::vector<uint8_t>& data);
    void writeLine(uint64_t block_addr, uint32_t requester,
                   const std::vector<uint8_t>& data,
                   PocqTxnKind txn = PocqTxnKind::WriteUnique,
                   uint32_t home_node_id = 0);
    void flushSf(uint64_t block_addr);
    void flushL3(uint64_t block_addr);
    void writeL3FlushSf(uint64_t block_addr, uint32_t requester,
                        const std::vector<uint8_t>& data);

    bool hasPendingSeq() const { return !seqPending.empty(); }
    SeqVictim frontPendingSeq() const;
    void markSeqIssued(SeqId id);
    void completeSfEvict(SeqId id, const std::vector<uint8_t>& data,
                         bool dirty_data);
    bool seqContains(uint64_t block_addr) const;
    size_t seqOccupancy() const;
    size_t seqCapacity() const { return seq.size(); }

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

    uint32_t blockSize = 64;
    uint32_t slcSets = 1024;
    uint32_t slcWays = 16;
    uint32_t sfSets = 1024;
    uint32_t sfWays = 16;

    std::vector<std::vector<SlcLine>> slc;
    std::vector<std::vector<SfLine>> sf;
    std::vector<SeqEntry> seq;
    std::deque<SeqId> seqPending;
    uint64_t accessCounter = 0;
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
    SlcLine& allocateSlc(uint64_t block_addr);
    SfLine& allocateSf(uint64_t block_addr, uint32_t home_node_id);
    const SfLine* selectSfVictim(uint64_t block_addr) const;
    bool sfAllocationWouldReplay(uint64_t block_addr) const;
    bool txnMayAllocateSf(PocqTxnKind txn) const;
    SeqId installSeqVictim(uint32_t set, const SfLine& victim,
                           uint32_t home_node_id);
    SeqEntry* findSeq(SeqId id);
    const SeqEntry* findSeq(SeqId id) const;
    void installSlc(uint64_t block_addr, HnfSlcState state,
                    uint32_t requester, const std::vector<uint8_t>& data);
    void invalidateSlc(uint64_t block_addr);
    void invalidateSf(uint64_t block_addr);
    void checkLineInvariant(uint64_t block_addr) const;
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

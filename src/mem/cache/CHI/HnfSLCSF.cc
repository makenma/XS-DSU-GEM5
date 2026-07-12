#include "mem/cache/CHI/HnfSLCSF.hh"

#include <algorithm>
#include <limits>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HnfSLCSF.hh"

namespace gem5::Chi
{

namespace
{

namespace SnpOp
{
constexpr uint8_t Shared = 0x01;
constexpr uint8_t Once = 0x03;
constexpr uint8_t Unique = 0x07;
constexpr uint8_t CleanInvalid = 0x09;
constexpr uint8_t MakeInvalid = 0x0a;
} // namespace SnpOp

bool
isDirty(HnfSlcState state)
{
    return state == HnfSlcState::MU || state == HnfSlcState::MN;
}

bool
hasSingleBit(uint64_t mask)
{
    return mask != 0 && (mask & (mask - 1)) == 0;
}

} // anonymous namespace

HnfSLCSF::HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways, uint32_t seq_entries)
    : blockSize(block_size),
      slcSets(slc_num_sets),
      slcWays(slc_num_ways),
      sfSets(sf_num_sets),
      sfWays(sf_num_ways),
      slc(slc_num_sets, std::vector<SlcLine>(slc_num_ways)),
      sf(sf_num_sets, std::vector<SfLine>(sf_num_ways)),
      seq(seq_entries)
{
    fatal_if(blockSize == 0, "HnfSLCSF block_size must be non-zero\n");
    fatal_if(slcSets == 0 || slcWays == 0,
             "HnfSLCSF SLC sets/ways must be non-zero\n");
    fatal_if(sfSets == 0 || sfWays == 0,
             "HnfSLCSF SF sets/ways must be non-zero\n");
    fatal_if(seq.empty(), "HnfSLCSF SEQ must contain at least one entry\n");
}

uint64_t
HnfSLCSF::blockNumber(uint64_t block_addr) const
{
    return block_addr / blockSize;
}

uint32_t
HnfSLCSF::slcSet(uint64_t block_addr) const
{
    return blockNumber(block_addr) % slcSets;
}

uint32_t
HnfSLCSF::sfSet(uint64_t block_addr) const
{
    return blockNumber(block_addr) % sfSets;
}

uint64_t
HnfSLCSF::slcTag(uint64_t block_addr) const
{
    return blockNumber(block_addr) / slcSets;
}

uint64_t
HnfSLCSF::sfTag(uint64_t block_addr) const
{
    return blockNumber(block_addr) / sfSets;
}

uint64_t
HnfSLCSF::sfBlockAddr(uint64_t tag, uint32_t set) const
{
    return (tag * sfSets + set) * blockSize;
}

uint64_t
HnfSLCSF::requesterMask(uint32_t requester) const
{
    panic_if(requester >= 64,
             "HnfSLCSF RNF node id %u exceeds the 64-bit directory mask\n",
             requester);
    return 1ULL << requester;
}

HnfSLCSF::SlcLine*
HnfSLCSF::findSlc(uint64_t block_addr)
{
    const uint64_t tag = slcTag(block_addr);
    auto& set = slc[slcSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SlcLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

const HnfSLCSF::SlcLine*
HnfSLCSF::findSlc(uint64_t block_addr) const
{
    const uint64_t tag = slcTag(block_addr);
    const auto& set = slc[slcSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SlcLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

HnfSLCSF::SfLine*
HnfSLCSF::findSf(uint64_t block_addr)
{
    const uint64_t tag = sfTag(block_addr);
    auto& set = sf[sfSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SfLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

const HnfSLCSF::SfLine*
HnfSLCSF::findSf(uint64_t block_addr) const
{
    const uint64_t tag = sfTag(block_addr);
    const auto& set = sf[sfSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SfLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

HnfSLCSF::SlcLine&
HnfSLCSF::allocateSlc(uint64_t block_addr)
{
    if (SlcLine* hit = findSlc(block_addr)) {
        hit->lastUse = ++accessCounter;
        return *hit;
    }

    auto& set = slc[slcSet(block_addr)];
    auto victim = std::find_if(set.begin(), set.end(), [](const SlcLine& line) {
        return !line.valid;
    });
    if (victim == set.end()) {
        victim = std::min_element(
            set.begin(), set.end(), [](const SlcLine& lhs, const SlcLine& rhs) {
                return lhs.lastUse < rhs.lastUse;
            });
        panic_if(isDirty(victim->state),
                 "HnfSLCSF dirty SLC victim requires VictimBuffer before "
                 "replacement set=%u tag=%llu state=%u\n",
                 slcSet(block_addr),
                 static_cast<unsigned long long>(victim->tag),
                 static_cast<unsigned>(victim->state));
    }

    const uint64_t generation = ++accessCounter;
    *victim = SlcLine{};
    victim->valid = true;
    victim->tag = slcTag(block_addr);
    victim->generation = generation;
    victim->lastUse = generation;
    return *victim;
}

const HnfSLCSF::SfLine*
HnfSLCSF::selectSfVictim(uint64_t block_addr) const
{
    const auto& set = sf[sfSet(block_addr)];
    auto victim = std::min_element(
        set.begin(), set.end(), [](const SfLine& lhs, const SfLine& rhs) {
            return lhs.lastUse < rhs.lastUse;
        });
    return victim == set.end() ? nullptr : &*victim;
}

bool
HnfSLCSF::txnMayAllocateSf(PocqTxnKind txn) const
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::MakeUnique;
}

bool
HnfSLCSF::sfAllocationWouldReplay(uint64_t block_addr) const
{
    if (findSf(block_addr)) {
        return false;
    }

    const auto& set = sf[sfSet(block_addr)];
    if (std::any_of(set.begin(), set.end(),
                    [](const SfLine& line) { return !line.valid; })) {
        return false;
    }

    const SfLine* victim = selectSfVictim(block_addr);
    panic_if(!victim, "HnfSLCSF failed to select a full-set SF victim\n");
    const uint64_t victimAddr =
        sfBlockAddr(victim->tag, sfSet(block_addr));
    return seqOccupancy() == seq.size() || seqContains(victimAddr);
}

HnfSLCSF::SeqId
HnfSLCSF::installSeqVictim(uint32_t set, const SfLine& victim,
                           uint32_t home_node_id)
{
    auto slot = std::find_if(seq.begin(), seq.end(),
                             [](const SeqEntry& entry) {
                                 return !entry.valid;
                             });
    panic_if(slot == seq.end(),
             "HnfSLCSF installs SF victim while SEQ is full\n");

    SeqVictim snapshot{};
    snapshot.id = nextSeqId++;
    snapshot.blockAddr = sfBlockAddr(victim.tag, set);
    snapshot.homeNodeId = home_node_id;
    snapshot.state = victim.state;
    snapshot.owner = victim.owner;
    snapshot.sharers = victim.sharers;
    slot->valid = true;
    slot->victim = snapshot;
    seqPending.push_back(snapshot.id);

    DPRINTF(HnfSLCSF,
            "SEQ install id=%llu addr=%#llx state=%u owner=%u "
            "sharers=%#llx occupancy=%u/%u\n",
            static_cast<unsigned long long>(snapshot.id),
            static_cast<unsigned long long>(snapshot.blockAddr),
            static_cast<unsigned>(snapshot.state), snapshot.owner,
            static_cast<unsigned long long>(snapshot.sharers),
            static_cast<unsigned>(seqOccupancy()),
            static_cast<unsigned>(seq.size()));
    return snapshot.id;
}

HnfSLCSF::SfLine&
HnfSLCSF::allocateSf(uint64_t block_addr, uint32_t home_node_id)
{
    if (SfLine* hit = findSf(block_addr)) {
        hit->lastUse = ++accessCounter;
        return *hit;
    }

    auto& set = sf[sfSet(block_addr)];
    auto victim = std::find_if(set.begin(), set.end(), [](const SfLine& line) {
        return !line.valid;
    });
    if (victim == set.end()) {
        victim = std::min_element(
            set.begin(), set.end(), [](const SfLine& lhs, const SfLine& rhs) {
                return lhs.lastUse < rhs.lastUse;
            });
        panic_if(victim->sharers == 0,
                 "HnfSLCSF valid SF victim has no sharers set=%u tag=%llu\n",
                 sfSet(block_addr),
                 static_cast<unsigned long long>(victim->tag));
        const uint64_t victimAddr =
            sfBlockAddr(victim->tag, sfSet(block_addr));
        panic_if(seqContains(victimAddr),
                 "HnfSLCSF SF victim collides with active SEQ addr=%#llx\n",
                 static_cast<unsigned long long>(victimAddr));
        installSeqVictim(sfSet(block_addr), *victim, home_node_id);
    }

    const uint64_t generation = ++accessCounter;
    *victim = SfLine{};
    victim->valid = true;
    victim->tag = sfTag(block_addr);
    victim->generation = generation;
    victim->lastUse = generation;
    return *victim;
}

void
HnfSLCSF::installSlc(uint64_t block_addr, HnfSlcState state,
                     uint32_t requester, const std::vector<uint8_t>& data)
{
    SlcLine& line = allocateSlc(block_addr);
    line.valid = true;
    line.state = state;
    line.owner = requester;
    line.data.assign(blockSize, 0);
    const size_t bytes = std::min<size_t>(blockSize, data.size());
    std::copy(data.begin(), data.begin() + bytes, line.data.begin());
    line.generation = ++accessCounter;
    line.lastUse = accessCounter;
}

void
HnfSLCSF::invalidateSlc(uint64_t block_addr)
{
    if (SlcLine* line = findSlc(block_addr)) {
        line->valid = false;
        line->state = HnfSlcState::I;
        line->data.clear();
        line->generation = ++accessCounter;
        line->lastUse = accessCounter;
    }
}

void
HnfSLCSF::invalidateSf(uint64_t block_addr)
{
    if (SfLine* line = findSf(block_addr)) {
        line->valid = false;
        line->state = HnfSfState::I;
        line->sharers = 0;
        line->generation = ++accessCounter;
        line->lastUse = accessCounter;
    }
}

HnfSlcLookupResult
HnfSLCSF::lookup(const HnfSlcLookupReq& req)
{
    HnfSlcLookupResult result{};
    result.valid = true;
    result.entry = req.entry;

    if (seqContains(req.blockAddr) ||
        (txnMayAllocateSf(req.txn) &&
         sfAllocationWouldReplay(req.blockAddr))) {
        result.replay = true;
        DPRINTF(HnfSLCSF,
                "lookup entry=%u txn=%u addr=%#llx replay for SEQ "
                "occupancy=%u/%u hit=%u\n",
                req.entry, static_cast<unsigned>(req.txn),
                static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned>(seqOccupancy()),
                static_cast<unsigned>(seq.size()),
                seqContains(req.blockAddr));
        return result;
    }

    if (SlcLine* line = findSlc(req.blockAddr)) {
        result.slcHit = true;
        result.slcState = line->state;
        result.dataDirty = isDirty(line->state);
        result.data = line->data;
        line->lastUse = ++accessCounter;
    }

    if (SfLine* line = findSf(req.blockAddr)) {
        result.sfHit = true;
        result.sfState = line->state;
        result.rnfid = line->owner;
        result.rnfvec = line->sharers;
        line->lastUse = ++accessCounter;

        const uint64_t otherSharers =
            line->sharers & ~requesterMask(req.req.srcid);
        switch (req.txn) {
          case PocqTxnKind::ReadUnique:
            result.snoopOpcode = SnpOp::Unique;
            result.snoopTargets = otherSharers;
            break;
          case PocqTxnKind::MakeUnique:
          case PocqTxnKind::MakeInvalid:
            result.snoopOpcode = SnpOp::MakeInvalid;
            result.snoopTargets = otherSharers;
            break;
          case PocqTxnKind::CleanInvalid:
            result.snoopOpcode = SnpOp::CleanInvalid;
            result.snoopTargets = otherSharers;
            break;
          case PocqTxnKind::ReadShared:
            if (!result.slcHit) {
                result.snoopOpcode = SnpOp::Shared;
                result.snoopTargets = otherSharers;
            }
            break;
          case PocqTxnKind::ReadOnce:
            if (!result.slcHit) {
                result.snoopOpcode = SnpOp::Once;
                result.snoopTargets = otherSharers;
            }
            break;
          default:
            break;
        }

        if (result.snoopTargets != 0) {
            const bool vectorState = line->state == HnfSfState::EN ||
                                     line->state == HnfSfState::SN;
            result.snoopBroadcast = vectorState ||
                __builtin_popcountll(result.snoopTargets) > 1;
            result.snoopDirected = !result.snoopBroadcast;
        }
    }

    result.mcreqNonspec = !result.slcHit && result.snoopTargets == 0;

    DPRINTF(HnfSLCSF,
            "lookup entry=%u txn=%u addr=%#llx slcHit=%u slcState=%u "
            "sfHit=%u sfState=%u owner=%u sharers=%#llx snoop=%#llx "
            "opcode=0x%x broadcast=%u directed=%u dataDirty=%u\n",
            req.entry, static_cast<unsigned>(req.txn),
            static_cast<unsigned long long>(req.blockAddr), result.slcHit,
            static_cast<unsigned>(result.slcState), result.sfHit,
            static_cast<unsigned>(result.sfState), result.rnfid,
            static_cast<unsigned long long>(result.rnfvec),
            static_cast<unsigned long long>(result.snoopTargets),
            result.snoopOpcode, result.snoopBroadcast, result.snoopDirected,
            result.dataDirty);
    return result;
}

void
HnfSLCSF::commitRead(uint64_t block_addr, uint32_t requester,
                     PocqTxnKind txn, const std::vector<uint8_t>& data,
                     bool data_dirty, uint32_t home_node_id)
{
    const uint64_t requesterBit = requesterMask(requester);
    switch (txn) {
      case PocqTxnKind::ReadShared: {
        installSlc(block_addr,
                   data_dirty ? HnfSlcState::MN : HnfSlcState::EN,
                   requester, data);
        SfLine& line = allocateSf(block_addr, home_node_id);
        line.sharers |= requesterBit;
        if (line.sharers == requesterBit) {
            line.owner = requester;
        }
        line.state = data_dirty ? HnfSfState::SN : HnfSfState::EN;
        line.generation = ++accessCounter;
        line.lastUse = accessCounter;
        break;
      }
      case PocqTxnKind::ReadUnique:
        invalidateSlc(block_addr);
        {
            SfLine& line = allocateSf(block_addr, home_node_id);
            line.state = HnfSfState::EU;
            line.owner = requester;
            line.sharers = requesterBit;
            line.generation = ++accessCounter;
            line.lastUse = accessCounter;
        }
        break;
      case PocqTxnKind::ReadOnce:
        break;
      default:
        panic("HnfSLCSF commitRead called for non-read txn=%u\n",
              static_cast<unsigned>(txn));
    }

    checkLineInvariant(block_addr);
    DPRINTF(HnfSLCSF,
            "commit read txn=%u addr=%#llx requester=%u dirty=%u bytes=%u\n",
            static_cast<unsigned>(txn),
            static_cast<unsigned long long>(block_addr), requester, data_dirty,
            static_cast<unsigned>(data.size()));
}

void
HnfSLCSF::completeMaintenance(uint64_t block_addr, uint32_t requester,
                              PocqTxnKind txn, uint32_t home_node_id)
{
    switch (txn) {
      case PocqTxnKind::MakeUnique: {
        invalidateSlc(block_addr);
        SfLine& line = allocateSf(block_addr, home_node_id);
        line.state = HnfSfState::EU;
        line.owner = requester;
        line.sharers = requesterMask(requester);
        line.generation = ++accessCounter;
        line.lastUse = accessCounter;
        break;
      }
      case PocqTxnKind::CleanInvalid:
      case PocqTxnKind::MakeInvalid:
        invalidateSlc(block_addr);
        invalidateSf(block_addr);
        break;
      default:
        panic("HnfSLCSF completeMaintenance called for txn=%u\n",
              static_cast<unsigned>(txn));
    }
    checkLineInvariant(block_addr);
}

void
HnfSLCSF::removeSharer(uint64_t block_addr, uint32_t requester)
{
    SfLine* line = findSf(block_addr);
    if (!line) {
        return;
    }

    line->sharers &= ~requesterMask(requester);
    line->generation = ++accessCounter;
    line->lastUse = accessCounter;
    if (line->sharers == 0) {
        invalidateSf(block_addr);
    } else if (hasSingleBit(line->sharers)) {
        line->owner = __builtin_ctzll(line->sharers);
        line->state = HnfSfState::SU;
    } else {
        const SlcLine* slcLine = findSlc(block_addr);
        line->state = slcLine && isDirty(slcLine->state) ?
            HnfSfState::SN : HnfSfState::EN;
    }
    checkLineInvariant(block_addr);
    DPRINTF(HnfSLCSF,
            "remove sharer addr=%#llx requester=%u remaining=%#llx\n",
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned long long>(line->valid ? line->sharers : 0));
}

void
HnfSLCSF::fillCleanShared(uint64_t block_addr, uint32_t requester,
                          const std::vector<uint8_t>& data)
{
    commitRead(block_addr, requester, PocqTxnKind::ReadShared, data, false);
}

void
HnfSLCSF::writeLine(uint64_t block_addr, uint32_t requester,
                    const std::vector<uint8_t>& data, PocqTxnKind txn,
                    uint32_t home_node_id)
{
    switch (txn) {
      case PocqTxnKind::WriteBackFull:
      case PocqTxnKind::WriteEvictFull:
        installSlc(block_addr, HnfSlcState::MU, requester, data);
        removeSharer(block_addr, requester);
        if (const SfLine* line = findSf(block_addr); line && line->valid) {
            SlcLine* slcLine = findSlc(block_addr);
            slcLine->state = HnfSlcState::MN;
        }
        break;
      case PocqTxnKind::WriteCleanFull: {
        installSlc(block_addr, HnfSlcState::EN, requester, data);
        SfLine& line = allocateSf(block_addr, home_node_id);
        line.sharers |= requesterMask(requester);
        line.owner = requester;
        line.state = HnfSfState::EN;
        line.generation = ++accessCounter;
        line.lastUse = accessCounter;
        break;
      }
      case PocqTxnKind::WriteUnique:
        installSlc(block_addr, HnfSlcState::MU, requester, data);
        invalidateSf(block_addr);
        break;
      default:
        panic("HnfSLCSF writeLine called for txn=%u\n",
              static_cast<unsigned>(txn));
    }
    checkLineInvariant(block_addr);
    DPRINTF(HnfSLCSF,
            "write line txn=%u addr=%#llx requester=%u bytes=%u\n",
            static_cast<unsigned>(txn),
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned>(data.size()));
}

void
HnfSLCSF::flushSf(uint64_t block_addr)
{
    invalidateSf(block_addr);
    DPRINTF(HnfSLCSF, "flush SF addr=%#llx\n",
            static_cast<unsigned long long>(block_addr));
}

void
HnfSLCSF::flushL3(uint64_t block_addr)
{
    invalidateSlc(block_addr);
    DPRINTF(HnfSLCSF, "flush L3 addr=%#llx\n",
            static_cast<unsigned long long>(block_addr));
}

void
HnfSLCSF::writeL3FlushSf(uint64_t block_addr, uint32_t requester,
                         const std::vector<uint8_t>& data)
{
    installSlc(block_addr, HnfSlcState::MU, requester, data);
    invalidateSf(block_addr);
    checkLineInvariant(block_addr);
}

HnfSLCSF::SeqEntry*
HnfSLCSF::findSeq(SeqId id)
{
    auto it = std::find_if(seq.begin(), seq.end(), [id](const SeqEntry& entry) {
        return entry.valid && entry.victim.id == id;
    });
    return it == seq.end() ? nullptr : &*it;
}

const HnfSLCSF::SeqEntry*
HnfSLCSF::findSeq(SeqId id) const
{
    auto it = std::find_if(seq.begin(), seq.end(), [id](const SeqEntry& entry) {
        return entry.valid && entry.victim.id == id;
    });
    return it == seq.end() ? nullptr : &*it;
}

bool
HnfSLCSF::seqContains(uint64_t block_addr) const
{
    return std::any_of(seq.begin(), seq.end(), [block_addr](const SeqEntry& e) {
        return e.valid && e.victim.blockAddr == block_addr;
    });
}

size_t
HnfSLCSF::seqOccupancy() const
{
    return std::count_if(seq.begin(), seq.end(), [](const SeqEntry& entry) {
        return entry.valid;
    });
}

HnfSLCSF::SeqVictim
HnfSLCSF::frontPendingSeq() const
{
    panic_if(seqPending.empty(), "HnfSLCSF frontPendingSeq on empty queue\n");
    const SeqEntry* entry = findSeq(seqPending.front());
    panic_if(!entry, "HnfSLCSF pending SEQ id=%llu is missing\n",
             static_cast<unsigned long long>(seqPending.front()));
    return entry->victim;
}

void
HnfSLCSF::markSeqIssued(SeqId id)
{
    panic_if(seqPending.empty() || seqPending.front() != id,
             "HnfSLCSF issues SEQ id=%llu out of order\n",
             static_cast<unsigned long long>(id));
    SeqEntry* entry = findSeq(id);
    panic_if(!entry || entry->victim.issued,
             "HnfSLCSF issues invalid/duplicate SEQ id=%llu\n",
             static_cast<unsigned long long>(id));
    entry->victim.issued = true;
    seqPending.pop_front();
    DPRINTF(HnfSLCSF, "SEQ issue id=%llu addr=%#llx\n",
            static_cast<unsigned long long>(id),
            static_cast<unsigned long long>(entry->victim.blockAddr));
}

void
HnfSLCSF::completeSfEvict(SeqId id, const std::vector<uint8_t>& data,
                          bool dirty_data)
{
    SeqEntry* entry = findSeq(id);
    panic_if(!entry || !entry->victim.issued,
             "HnfSLCSF completes invalid/unissued SEQ id=%llu\n",
             static_cast<unsigned long long>(id));
    const uint64_t addr = entry->victim.blockAddr;
    const uint32_t owner = entry->victim.owner;
    if (dirty_data) {
        panic_if(data.size() < blockSize,
                 "HnfSLCSF SEQ id=%llu dirty data is short (%u/%u)\n",
                 static_cast<unsigned long long>(id),
                 static_cast<unsigned>(data.size()), blockSize);
        installSlc(addr, HnfSlcState::MU, owner, data);
    }
    *entry = SeqEntry{};
    DPRINTF(HnfSLCSF,
            "SEQ complete id=%llu addr=%#llx dirty=%u occupancy=%u/%u\n",
            static_cast<unsigned long long>(id),
            static_cast<unsigned long long>(addr), dirty_data,
            static_cast<unsigned>(seqOccupancy()),
            static_cast<unsigned>(seq.size()));
}

void
HnfSLCSF::checkLineInvariant(uint64_t block_addr) const
{
    const SlcLine* slcLine = findSlc(block_addr);
    const SfLine* sfLine = findSf(block_addr);

    panic_if(slcLine && isDirty(slcLine->state) &&
                 slcLine->data.size() != blockSize,
             "HnfSLCSF dirty line lacks full data addr=%#llx bytes=%u/%u\n",
             static_cast<unsigned long long>(block_addr),
             static_cast<unsigned>(slcLine->data.size()), blockSize);
    panic_if(sfLine && sfLine->sharers == 0,
             "HnfSLCSF valid SF line has no sharer addr=%#llx state=%u\n",
             static_cast<unsigned long long>(block_addr),
             static_cast<unsigned>(sfLine->state));
    panic_if(sfLine && sfLine->state == HnfSfState::EU &&
                 (!hasSingleBit(sfLine->sharers) || slcLine),
             "HnfSLCSF EU invariant failed addr=%#llx sharers=%#llx slc=%u\n",
             static_cast<unsigned long long>(block_addr),
             static_cast<unsigned long long>(sfLine->sharers), slcLine != nullptr);
}

} // namespace gem5::Chi

#include "mem/cache/CHI/HnfSLCSFBackend.hh"

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

uint64_t
dataPrefix(const std::vector<uint8_t>& data)
{
    uint64_t value = 0;
    const size_t bytes = std::min<size_t>(sizeof(value), data.size());
    for (size_t i = 0; i < bytes; ++i) {
        value |= static_cast<uint64_t>(data[i]) << (i * 8);
    }
    return value;
}

} // anonymous namespace

HnfSLCSFBackend::HnfSLCSFBackend(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways, uint32_t seq_entries)
    : blockSize(block_size),
      slcSets(slc_num_sets),
      slcWays(slc_num_ways),
      sfSets(sf_num_sets),
      sfWays(sf_num_ways),
      slc(slc_num_sets, std::vector<SlcLine>(slc_num_ways)),
      sf(sf_num_sets, std::vector<SfLine>(sf_num_ways)),
      seq(seq_entries),
      sfReservationOwners(sf_num_sets, -1)
{
    fatal_if(blockSize == 0, "HnfSLCSF block_size must be non-zero\n");
    fatal_if(slcSets == 0 || slcWays == 0,
             "HnfSLCSF SLC sets/ways must be non-zero\n");
    fatal_if(sfSets == 0 || sfWays == 0,
             "HnfSLCSF SF sets/ways must be non-zero\n");
    fatal_if(seq.empty(), "HnfSLCSF SEQ must contain at least one entry\n");
}

uint64_t
HnfSLCSFBackend::blockNumber(uint64_t block_addr) const
{
    return block_addr / blockSize;
}

uint32_t
HnfSLCSFBackend::slcSet(uint64_t block_addr) const
{
    return blockNumber(block_addr) % slcSets;
}

uint32_t
HnfSLCSFBackend::sfSet(uint64_t block_addr) const
{
    return blockNumber(block_addr) % sfSets;
}

uint64_t
HnfSLCSFBackend::slcTag(uint64_t block_addr) const
{
    return blockNumber(block_addr) / slcSets;
}

uint64_t
HnfSLCSFBackend::sfTag(uint64_t block_addr) const
{
    return blockNumber(block_addr) / sfSets;
}

uint64_t
HnfSLCSFBackend::sfBlockAddr(uint64_t tag, uint32_t set) const
{
    return (tag * sfSets + set) * blockSize;
}

uint64_t
HnfSLCSFBackend::requesterMask(uint32_t requester) const
{
    panic_if(requester >= 64,
             "HnfSLCSF RNF node id %u exceeds the 64-bit directory mask\n",
             requester);
    return 1ULL << requester;
}

HnfSLCSFBackend::SlcLine*
HnfSLCSFBackend::findSlc(uint64_t block_addr)
{
    const uint64_t tag = slcTag(block_addr);
    auto& set = slc[slcSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SlcLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

const HnfSLCSFBackend::SlcLine*
HnfSLCSFBackend::findSlc(uint64_t block_addr) const
{
    const uint64_t tag = slcTag(block_addr);
    const auto& set = slc[slcSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SlcLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

HnfSLCSFBackend::SfLine*
HnfSLCSFBackend::findSf(uint64_t block_addr)
{
    const uint64_t tag = sfTag(block_addr);
    auto& set = sf[sfSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SfLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

const HnfSLCSFBackend::SfLine*
HnfSLCSFBackend::findSf(uint64_t block_addr) const
{
    const uint64_t tag = sfTag(block_addr);
    const auto& set = sf[sfSet(block_addr)];
    auto it = std::find_if(set.begin(), set.end(), [tag](const SfLine& line) {
        return line.valid && line.tag == tag;
    });
    return it == set.end() ? nullptr : &*it;
}

HnfSLCSFBackend::SlcLine&
HnfSLCSFBackend::allocateSlc(uint64_t block_addr,
                             const ArraySnapshot* target)
{
    panic_if(target &&
                 (target->set != slcSet(block_addr) ||
                  target->way >= slcWays),
             "HnfSLCSF invalid token SLC target set=%u way=%u\n",
             target ? target->set : 0, target ? target->way : 0);
    if (SlcLine* hit = findSlc(block_addr)) {
        panic_if(target && &slc[target->set][target->way] != hit,
                 "HnfSLCSF token SLC hit way changed before mutation\n");
        hit->lastUse = ++accessCounter;
        return *hit;
    }

    auto& set = slc[slcSet(block_addr)];
    auto victim = target ? set.begin() + target->way :
        std::find_if(set.begin(), set.end(), [](const SlcLine& line) {
            return !line.valid;
        });
    if (victim == set.end()) {
        victim = std::min_element(
            set.begin(), set.end(), [](const SlcLine& lhs, const SlcLine& rhs) {
                return lhs.lastUse < rhs.lastUse;
            });
    }
    if (victim->valid) {
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

bool
HnfSLCSFBackend::slcAllocationWouldDisplaceDirty(
    uint64_t block_addr, const LookupSnapshot* target) const
{
    if (findSlc(block_addr)) {
        return false;
    }

    const auto& set = slc[slcSet(block_addr)];
    if (target) {
        panic_if(target->slc.set != slcSet(block_addr) ||
                     target->slc.way >= slcWays,
                 "HnfSLCSF invalid dirty-preflight SLC target\n");
        return set[target->slc.way].valid &&
            isDirty(set[target->slc.way].state);
    }
    if (std::any_of(set.begin(), set.end(),
                    [](const SlcLine& line) { return !line.valid; })) {
        return false;
    }
    const auto victim = std::min_element(
        set.begin(), set.end(),
        [](const SlcLine& lhs, const SlcLine& rhs) {
            return lhs.lastUse < rhs.lastUse;
        });
    return victim != set.end() && isDirty(victim->state);
}

bool
HnfSLCSFBackend::writeLineWouldDisplaceDirty(
    uint64_t block_addr, uint32_t requester, PocqTxnKind txn,
    const LookupSnapshot* target) const
{
    if (txn == PocqTxnKind::WriteBackFull ||
        txn == PocqTxnKind::WriteEvictFull) {
        const SfLine* tracked = findSf(block_addr);
        if (tracked &&
            (tracked->sharers & requesterMask(requester)) == 0) {
            return false;
        }
    }
    return slcAllocationWouldDisplaceDirty(block_addr, target);
}

const HnfSLCSFBackend::SfLine*
HnfSLCSFBackend::selectSfVictim(
    uint64_t block_addr, const ArraySnapshot* target) const
{
    const auto& set = sf[sfSet(block_addr)];
    panic_if(target &&
                 (target->set != sfSet(block_addr) ||
                  target->way >= sfWays),
             "HnfSLCSF invalid token SF target set=%u way=%u\n",
             target ? target->set : 0, target ? target->way : 0);
    auto victim = target ? set.begin() + target->way :
        std::min_element(
            set.begin(), set.end(),
            [](const SfLine& lhs, const SfLine& rhs) {
                return lhs.lastUse < rhs.lastUse;
            });
    return victim == set.end() ? nullptr : &*victim;
}

bool
HnfSLCSFBackend::txnTouchesSf(PocqTxnKind txn) const
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::CleanInvalid ||
        txn == PocqTxnKind::MakeInvalid ||
        txn == PocqTxnKind::MakeUnique ||
        txn == PocqTxnKind::Evict ||
        txn == PocqTxnKind::WriteBackFull ||
        txn == PocqTxnKind::WriteCleanFull ||
        txn == PocqTxnKind::WriteUnique ||
        txn == PocqTxnKind::WriteEvictFull;
}

bool
HnfSLCSFBackend::txnMayAllocateSf(PocqTxnKind txn) const
{
    return txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::MakeUnique ||
        txn == PocqTxnKind::WriteCleanFull;
}

bool
HnfSLCSFBackend::sfAllocationWouldReplay(
    uint64_t block_addr,
    std::optional<uint32_t> reservation_owner) const
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
    size_t otherReservations = reservedSeqSlots;
    if (reservation_owner) {
        auto reservation = sfReservations.find(*reservation_owner);
        if (reservation != sfReservations.end() &&
            reservation->second.seqSlot) {
            panic_if(otherReservations == 0,
                     "HnfSLCSF missing reserved SEQ slot for entry=%u\n",
                     *reservation_owner);
            --otherReservations;
        }
    }
    return seqOccupancy() + otherReservations >= seq.size() ||
        seqContains(victimAddr);
}

bool
HnfSLCSFBackend::tryReserveSfResources(uint32_t entry, uint64_t block_addr,
                                PocqTxnKind txn,
                                const LookupSnapshot* target)
{
    if (!txnTouchesSf(txn)) {
        return true;
    }

    const uint32_t set = sfSet(block_addr);
    bool needsSeqSlot = false;
    uint64_t victimAddr = 0;
    if (txnMayAllocateSf(txn) && !findSf(block_addr)) {
        const auto& lines = sf[set];
        panic_if(target &&
                     (target->sf.set != set || target->sf.way >= sfWays),
                 "HnfSLCSF invalid reservation SF target\n");
        const bool targetValid = target ?
            lines[target->sf.way].valid : false;
        const bool setFull = std::none_of(
            lines.begin(), lines.end(),
            [](const SfLine& line) { return !line.valid; });
        needsSeqSlot = target ? targetValid : setFull;
        if (needsSeqSlot) {
            const SfLine* victim = selectSfVictim(
                block_addr, target ? &target->sf : nullptr);
            panic_if(!victim,
                     "HnfSLCSF failed to reserve an SF victim\n");
            victimAddr = sfBlockAddr(victim->tag, set);
        }
    }

    auto existing = sfReservations.find(entry);
    if (existing != sfReservations.end()) {
        panic_if(existing->second.set != set ||
                     existing->second.blockAddr != block_addr,
                 "HnfSLCSF entry=%u changes SF reservation from "
                 "addr=%#llx/set=%u to addr=%#llx/set=%u\n",
                 entry,
                 static_cast<unsigned long long>(
                     existing->second.blockAddr),
                 existing->second.set,
                 static_cast<unsigned long long>(block_addr), set);
        if (seqContains(block_addr) ||
            (needsSeqSlot && seqContains(victimAddr))) {
            return false;
        }
        if (needsSeqSlot && !existing->second.seqSlot) {
            if (seqOccupancy() + reservedSeqSlots >= seq.size()) {
                return false;
            }
            existing->second.seqSlot = true;
            ++reservedSeqSlots;
            assertSeqAccounting();
        }
        return true;
    }

    if (sfReservationOwners[set] >= 0 || seqContains(block_addr)) {
        DPRINTF(HnfSLCSF,
                "reserve entry=%u txn=%u addr=%#llx set=%u blocked "
                "owner=%lld seqHit=%u\n",
                entry, static_cast<unsigned>(txn),
                static_cast<unsigned long long>(block_addr), set,
                static_cast<long long>(sfReservationOwners[set]),
                seqContains(block_addr));
        return false;
    }

    if (needsSeqSlot &&
        (seqContains(victimAddr) ||
         seqOccupancy() + reservedSeqSlots >= seq.size())) {
        DPRINTF(HnfSLCSF,
                "reserve entry=%u txn=%u addr=%#llx set=%u "
                "blocked for SEQ victim=%#llx occupancy=%u "
                "reserved=%u capacity=%u\n",
                entry, static_cast<unsigned>(txn),
                static_cast<unsigned long long>(block_addr), set,
                static_cast<unsigned long long>(victimAddr),
                static_cast<unsigned>(seqOccupancy()),
                static_cast<unsigned>(reservedSeqSlots),
                static_cast<unsigned>(seq.size()));
        return false;
    }

    sfReservationOwners[set] = entry;
    sfReservations.emplace(
        entry, SfReservation{set, block_addr, needsSeqSlot});
    if (needsSeqSlot) {
        ++reservedSeqSlots;
    }
    assertSeqAccounting();
    DPRINTF(HnfSLCSF,
            "reserve entry=%u txn=%u addr=%#llx set=%u seqSlot=%u "
            "seqReserved=%u\n",
            entry, static_cast<unsigned>(txn),
            static_cast<unsigned long long>(block_addr), set,
            needsSeqSlot, static_cast<unsigned>(reservedSeqSlots));
    return true;
}

void
HnfSLCSFBackend::releaseSfResources(uint32_t entry)
{
    auto reservation = sfReservations.find(entry);
    if (reservation == sfReservations.end()) {
        return;
    }

    const SfReservation held = reservation->second;
    panic_if(sfReservationOwners[held.set] != static_cast<int64_t>(entry),
             "HnfSLCSF entry=%u releases SF set=%u owned by %lld\n",
             entry, held.set,
             static_cast<long long>(sfReservationOwners[held.set]));
    sfReservationOwners[held.set] = -1;
    if (held.seqSlot) {
        panic_if(reservedSeqSlots == 0,
                 "HnfSLCSF entry=%u releases an unreserved SEQ slot\n",
                 entry);
        --reservedSeqSlots;
    }
    sfReservations.erase(reservation);
    assertSeqAccounting();
    DPRINTF(HnfSLCSF,
            "release entry=%u addr=%#llx set=%u seqSlot=%u "
            "seqReserved=%u\n",
            entry, static_cast<unsigned long long>(held.blockAddr), held.set,
            held.seqSlot, static_cast<unsigned>(reservedSeqSlots));
}

bool
HnfSLCSFBackend::hasSfReservation(uint32_t entry) const
{
    return sfReservations.find(entry) != sfReservations.end();
}

HnfSLCSFBackend::SeqId
HnfSLCSFBackend::installSeqVictim(
    uint32_t set, const SfLine& victim, SeqVictim* installed)
{
    panic_if(seqOccupancy() + reservedSeqSlots >= seq.size(),
             "HnfSLCSF installs SF victim without available SEQ capacity\n");
    auto slot = std::find_if(seq.begin(), seq.end(),
                             [](const SeqEntry& entry) {
                                 return !entry.valid;
                             });
    panic_if(slot == seq.end(),
             "HnfSLCSF installs SF victim while SEQ is full\n");

    SeqVictim snapshot{};
    snapshot.id = nextSeqId++;
    snapshot.blockAddr = sfBlockAddr(victim.tag, set);
    snapshot.homeNodeId = victim.homeNodeId;
    snapshot.state = victim.state;
    snapshot.owner = victim.owner;
    snapshot.sharers = victim.sharers;
    slot->valid = true;
    slot->victim = snapshot;
    seqPending.push_back(snapshot.id);
    if (installed) {
        *installed = snapshot;
    }
    assertSeqAccounting();

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

void
HnfSLCSFBackend::consumeSeqReservation(
    uint64_t block_addr, std::optional<uint32_t> reservation_owner)
{
    auto reservation = sfReservations.end();
    if (reservation_owner) {
        reservation = sfReservations.find(*reservation_owner);
        panic_if(reservation == sfReservations.end() ||
                     reservation->second.blockAddr != block_addr ||
                     !reservation->second.seqSlot,
                 "HnfSLCSF entry=%u installs an unreserved SF victim "
                 "addr=%#llx\n",
                 *reservation_owner,
                 static_cast<unsigned long long>(block_addr));
    } else {
        for (auto candidate = sfReservations.begin();
             candidate != sfReservations.end(); ++candidate) {
            if (candidate->second.blockAddr != block_addr ||
                !candidate->second.seqSlot) {
                continue;
            }
            panic_if(reservation != sfReservations.end(),
                     "HnfSLCSF ambiguous SEQ reservation for addr=%#llx\n",
                     static_cast<unsigned long long>(block_addr));
            reservation = candidate;
        }
    }

    if (reservation == sfReservations.end()) {
        return;
    }
    panic_if(reservedSeqSlots == 0,
             "HnfSLCSF consumes an uncounted SEQ reservation\n");
    reservation->second.seqSlot = false;
    --reservedSeqSlots;
    assertSeqAccounting();
}

void
HnfSLCSFBackend::assertSeqAccounting() const
{
    panic_if(seqOccupancy() + reservedSeqSlots > seq.size(),
             "HnfSLCSF SEQ accounting exceeds capacity occupancy=%u "
             "reserved=%u capacity=%u\n",
             static_cast<unsigned>(seqOccupancy()),
             static_cast<unsigned>(reservedSeqSlots),
             static_cast<unsigned>(seq.size()));
}

HnfSLCSFBackend::SfLine&
HnfSLCSFBackend::allocateSf(uint64_t block_addr, uint32_t home_node_id,
                            const ArraySnapshot* target,
                            std::optional<uint32_t> reservation_owner,
                            SeqVictim* sf_victim)
{
    panic_if(target &&
                 (target->set != sfSet(block_addr) ||
                  target->way >= sfWays),
             "HnfSLCSF invalid token SF target set=%u way=%u\n",
             target ? target->set : 0, target ? target->way : 0);
    if (SfLine* hit = findSf(block_addr)) {
        panic_if(target && &sf[target->set][target->way] != hit,
                 "HnfSLCSF token SF hit way changed before mutation\n");
        hit->lastUse = ++accessCounter;
        return *hit;
    }

    auto& set = sf[sfSet(block_addr)];
    auto victim = target ? set.begin() + target->way :
        std::find_if(set.begin(), set.end(), [](const SfLine& line) {
            return !line.valid;
        });
    if (victim == set.end()) {
        victim = std::min_element(
            set.begin(), set.end(), [](const SfLine& lhs, const SfLine& rhs) {
                return lhs.lastUse < rhs.lastUse;
            });
    }
    if (victim->valid) {
        panic_if(victim->sharers == 0,
                 "HnfSLCSF valid SF victim has no sharers set=%u tag=%llu\n",
                 sfSet(block_addr),
                 static_cast<unsigned long long>(victim->tag));
        const uint64_t victimAddr =
            sfBlockAddr(victim->tag, sfSet(block_addr));
        panic_if(seqContains(victimAddr),
                 "HnfSLCSF SF victim collides with active SEQ addr=%#llx\n",
                 static_cast<unsigned long long>(victimAddr));
        // Convert the pre-held capacity into an occupied SEQ entry before
        // overwriting the selected way. This keeps occupancy plus reservations
        // bounded while installSeqVictim copies the complete directory line.
        consumeSeqReservation(block_addr, reservation_owner);
        installSeqVictim(sfSet(block_addr), *victim, sf_victim);
    }

    const uint64_t generation = ++accessCounter;
    *victim = SfLine{};
    victim->valid = true;
    victim->tag = sfTag(block_addr);
    victim->homeNodeId = home_node_id;
    victim->generation = generation;
    victim->lastUse = generation;
    return *victim;
}

bool
HnfSLCSFBackend::sfAllocationWouldDisplace(
    uint64_t block_addr, const LookupSnapshot& target) const
{
    if (target.sf.set != sfSet(block_addr) || target.sf.way >= sfWays ||
        target.sf.hit) {
        return false;
    }
    return sf[target.sf.set][target.sf.way].valid;
}

bool
HnfSLCSFBackend::sfAllocationBlockedBySeq(
    uint64_t block_addr, const LookupSnapshot& target,
    std::optional<uint32_t> reservation_owner) const
{
    if (!sfAllocationWouldDisplace(block_addr, target)) {
        return false;
    }

    const SfLine& victim = sf[target.sf.set][target.sf.way];
    const uint64_t victim_addr = sfBlockAddr(victim.tag, target.sf.set);
    size_t other_reservations = reservedSeqSlots;
    if (reservation_owner) {
        const auto reservation = sfReservations.find(*reservation_owner);
        if (reservation != sfReservations.end() &&
            reservation->second.seqSlot) {
            panic_if(other_reservations == 0,
                     "HnfSLCSF missing counted SEQ reservation entry=%u\n",
                     *reservation_owner);
            --other_reservations;
        }
    }
    return seqContains(victim_addr) ||
        seqOccupancy() + other_reservations >= seq.size();
}

void
HnfSLCSFBackend::installSlc(uint64_t block_addr, HnfSlcState state,
                     uint32_t requester, const std::vector<uint8_t>& data,
                     const ArraySnapshot* target)
{
    SlcLine& line = allocateSlc(block_addr, target);
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
HnfSLCSFBackend::invalidateSlc(uint64_t block_addr)
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
HnfSLCSFBackend::invalidateSf(uint64_t block_addr)
{
    if (SfLine* line = findSf(block_addr)) {
        line->valid = false;
        line->state = HnfSfState::I;
        line->sharers = 0;
        line->generation = ++accessCounter;
        line->lastUse = accessCounter;
    }
}

HnfSLCSFBackend::LookupSnapshot
HnfSLCSFBackend::snapshotLookup(uint64_t block_addr) const
{
    LookupSnapshot snapshot{};
    snapshot.lookupEpoch = lookupEpoch;

    const auto capture = [](const auto& lines, uint32_t set,
                            uint64_t tag) {
        ArraySnapshot array{};
        array.set = set;
        auto selected = std::find_if(
            lines.begin(), lines.end(), [tag](const auto& line) {
                return line.valid && line.tag == tag;
            });
        array.hit = selected != lines.end();
        if (!array.hit) {
            selected = std::find_if(
                lines.begin(), lines.end(), [](const auto& line) {
                    return !line.valid;
                });
            if (selected == lines.end()) {
                selected = std::min_element(
                    lines.begin(), lines.end(),
                    [](const auto& lhs, const auto& rhs) {
                        return lhs.lastUse < rhs.lastUse;
                    });
            }
        }
        panic_if(selected == lines.end(),
                 "HnfSLCSF cannot snapshot an empty array set\n");
        array.way = std::distance(lines.begin(), selected);
        array.generation = selected->generation;
        array.replacementStamp = selected->lastUse;
        return array;
    };

    const uint32_t slc_set = slcSet(block_addr);
    const uint32_t sf_set = sfSet(block_addr);
    snapshot.slc = capture(slc[slc_set], slc_set, slcTag(block_addr));
    snapshot.sf = capture(sf[sf_set], sf_set, sfTag(block_addr));
    return snapshot;
}

bool
HnfSLCSFBackend::validateLookupSnapshot(
    uint64_t block_addr, const LookupSnapshot& snapshot) const
{
    if (snapshot.lookupEpoch != lookupEpoch) {
        return false;
    }

    const auto matches = [](const auto& lines, uint32_t expected_set,
                            uint64_t expected_tag,
                            const ArraySnapshot& recorded) {
        if (recorded.set != expected_set || recorded.way >= lines.size()) {
            return false;
        }

        const auto hit = std::find_if(
            lines.begin(), lines.end(), [expected_tag](const auto& line) {
                return line.valid && line.tag == expected_tag;
            });
        const bool current_hit = hit != lines.end();
        if (recorded.hit != current_hit) {
            return false;
        }
        if (current_hit &&
            static_cast<size_t>(std::distance(lines.begin(), hit)) !=
                recorded.way) {
            return false;
        }
        return lines[recorded.way].generation == recorded.generation;
    };

    const uint32_t slc_set = slcSet(block_addr);
    const uint32_t sf_set = sfSet(block_addr);
    return matches(slc[slc_set], slc_set, slcTag(block_addr), snapshot.slc) &&
        matches(sf[sf_set], sf_set, sfTag(block_addr), snapshot.sf);
}

void
HnfSLCSFBackend::invalidateCommitTokens()
{
    panic_if(lookupEpoch == std::numeric_limits<uint64_t>::max(),
             "HnfSLCSF lookup epoch space exhausted\n");
    ++lookupEpoch;
}

void
HnfSLCSFBackend::recordAccess(uint64_t block_addr)
{
    const uint64_t access = ++accessCounter;
    if (SlcLine* line = findSlc(block_addr)) {
        line->lastUse = access;
    }
    if (SfLine* line = findSf(block_addr)) {
        line->lastUse = access;
    }
    ++lookupAccessCount;
}

HnfSLCSFBackend::LookupObservation
HnfSLCSFBackend::probe(const HnfSlcLookupReq& req) const
{
    LookupObservation observation{};
    HnfSlcLookupResult& result = observation.result;
    result.valid = true;
    result.entry = req.entry;
    observation.snapshot = snapshotLookup(req.blockAddr);

    if (seqContains(req.blockAddr) ||
        (txnMayAllocateSf(req.txn) &&
         sfAllocationWouldReplay(req.blockAddr, req.entry))) {
        result.replay = true;
        DPRINTF(HnfSLCSF,
                "lookup entry=%u txn=%u addr=%#llx replay for SEQ "
                "occupancy=%u/%u hit=%u\n",
                req.entry, static_cast<unsigned>(req.txn),
                static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned>(seqOccupancy()),
                static_cast<unsigned>(seq.size()),
                seqContains(req.blockAddr));
        return observation;
    }

    if (const SlcLine* line = findSlc(req.blockAddr)) {
        result.slcHit = true;
        result.slcState = line->state;
        result.dataDirty = isDirty(line->state);
        result.data = line->data;
    }

    if (const SfLine* line = findSf(req.blockAddr)) {
        result.sfHit = true;
        result.sfState = line->state;
        result.rnfid = line->owner;
        result.rnfvec = line->sharers;
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
    return observation;
}

HnfSlcLookupResult
HnfSLCSFBackend::lookup(const HnfSlcLookupReq& req, LookupSnapshot* snapshot)
{
    LookupObservation observation = probe(req);
    if (!observation.result.replay) {
        recordAccess(req.blockAddr);
    }
    if (snapshot) {
        *snapshot = observation.snapshot;
    }
    return std::move(observation.result);
}

void
HnfSLCSFBackend::commitRead(uint64_t block_addr, uint32_t requester,
                     PocqTxnKind txn, const std::vector<uint8_t>& data,
                     bool data_dirty, uint32_t home_node_id,
                     const LookupSnapshot* target,
                     std::optional<uint32_t> reservation_owner,
                     SeqVictim* sf_victim)
{
    const uint64_t requesterBit = requesterMask(requester);
    switch (txn) {
      case PocqTxnKind::ReadShared: {
        installSlc(block_addr,
                   data_dirty ? HnfSlcState::MN : HnfSlcState::EN,
                   requester, data, target ? &target->slc : nullptr);
        SfLine& line = allocateSf(
            block_addr, home_node_id, target ? &target->sf : nullptr,
            reservation_owner, sf_victim);
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
            SfLine& line = allocateSf(
                block_addr, home_node_id, target ? &target->sf : nullptr,
                reservation_owner, sf_victim);
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
            "commit read txn=%u addr=%#llx requester=%u dirty=%u bytes=%u "
            "word0=%#llx\n",
            static_cast<unsigned>(txn),
            static_cast<unsigned long long>(block_addr), requester, data_dirty,
            static_cast<unsigned>(data.size()),
            static_cast<unsigned long long>(dataPrefix(data)));
}

void
HnfSLCSFBackend::completeMaintenance(uint64_t block_addr, uint32_t requester,
                              PocqTxnKind txn, uint32_t home_node_id,
                              const LookupSnapshot* target,
                              std::optional<uint32_t> reservation_owner,
                              SeqVictim* sf_victim)
{
    switch (txn) {
      case PocqTxnKind::MakeUnique: {
        invalidateSlc(block_addr);
        SfLine& line = allocateSf(
            block_addr, home_node_id, target ? &target->sf : nullptr,
            reservation_owner, sf_victim);
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
HnfSLCSFBackend::removeSharer(uint64_t block_addr, uint32_t requester)
{
    SfLine* line = findSf(block_addr);
    if (!line) {
        return;
    }

    const uint64_t requester_bit = requesterMask(requester);
    if ((line->sharers & requester_bit) == 0) {
        return;
    }

    line->sharers &= ~requester_bit;
    if (line->sharers == 0) {
        invalidateSf(block_addr);
    } else if (hasSingleBit(line->sharers)) {
        line->owner = __builtin_ctzll(line->sharers);
        line->state = HnfSfState::SU;
        line->generation = ++accessCounter;
        line->lastUse = accessCounter;
    } else {
        const SlcLine* slcLine = findSlc(block_addr);
        line->state = slcLine && isDirty(slcLine->state) ?
            HnfSfState::SN : HnfSfState::EN;
        line->generation = ++accessCounter;
        line->lastUse = accessCounter;
    }
    checkLineInvariant(block_addr);
    DPRINTF(HnfSLCSF,
            "remove sharer addr=%#llx requester=%u remaining=%#llx\n",
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned long long>(line->valid ? line->sharers : 0));
}

void
HnfSLCSFBackend::fillCleanShared(uint64_t block_addr, uint32_t requester,
                          const std::vector<uint8_t>& data,
                          const LookupSnapshot* target,
                          std::optional<uint32_t> reservation_owner,
                          SeqVictim* sf_victim)
{
    commitRead(
        block_addr, requester, PocqTxnKind::ReadShared, data, false, 0,
        target, reservation_owner, sf_victim);
}

void
HnfSLCSFBackend::writeLine(uint64_t block_addr, uint32_t requester,
                    const std::vector<uint8_t>& data, PocqTxnKind txn,
                    uint32_t home_node_id, const LookupSnapshot* target,
                    std::optional<uint32_t> reservation_owner,
                    SeqVictim* sf_victim)
{
    switch (txn) {
      case PocqTxnKind::WriteBackFull:
      case PocqTxnKind::WriteEvictFull: {
        const SfLine* tracked = findSf(block_addr);
        if (tracked &&
            (tracked->sharers & requesterMask(requester)) == 0) {
            DPRINTF(HnfSLCSF,
                    "discard stale writeback txn=%u addr=%#llx "
                    "requester=%u currentSharers=%#llx\n",
                    static_cast<unsigned>(txn),
                    static_cast<unsigned long long>(block_addr), requester,
                    static_cast<unsigned long long>(tracked->sharers));
            break;
        }
        installSlc(
            block_addr, HnfSlcState::MU, requester, data,
            target ? &target->slc : nullptr);
        removeSharer(block_addr, requester);
        if (const SfLine* line = findSf(block_addr); line && line->valid) {
            SlcLine* slcLine = findSlc(block_addr);
            slcLine->state = HnfSlcState::MN;
        }
        break;
      }
      case PocqTxnKind::WriteCleanFull: {
        installSlc(
            block_addr, HnfSlcState::EN, requester, data,
            target ? &target->slc : nullptr);
        SfLine& line = allocateSf(
            block_addr, home_node_id, target ? &target->sf : nullptr,
            reservation_owner, sf_victim);
        line.sharers |= requesterMask(requester);
        line.owner = requester;
        line.state = HnfSfState::EN;
        line.generation = ++accessCounter;
        line.lastUse = accessCounter;
        break;
      }
      case PocqTxnKind::WriteUnique:
        installSlc(
            block_addr, HnfSlcState::MU, requester, data,
            target ? &target->slc : nullptr);
        invalidateSf(block_addr);
        break;
      default:
        panic("HnfSLCSF writeLine called for txn=%u\n",
              static_cast<unsigned>(txn));
    }
    checkLineInvariant(block_addr);
    DPRINTF(HnfSLCSF,
            "write line txn=%u addr=%#llx requester=%u bytes=%u "
            "word0=%#llx\n",
            static_cast<unsigned>(txn),
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned>(data.size()),
            static_cast<unsigned long long>(dataPrefix(data)));
}

void
HnfSLCSFBackend::flushSf(uint64_t block_addr)
{
    invalidateSf(block_addr);
    DPRINTF(HnfSLCSF, "flush SF addr=%#llx\n",
            static_cast<unsigned long long>(block_addr));
}

void
HnfSLCSFBackend::flushL3(uint64_t block_addr)
{
    invalidateSlc(block_addr);
    DPRINTF(HnfSLCSF, "flush L3 addr=%#llx\n",
            static_cast<unsigned long long>(block_addr));
}

void
HnfSLCSFBackend::writeL3FlushSf(uint64_t block_addr, uint32_t requester,
                                const std::vector<uint8_t>& data,
                                const LookupSnapshot* target)
{
    installSlc(block_addr, HnfSlcState::MU, requester, data,
               target ? &target->slc : nullptr);
    invalidateSf(block_addr);
    checkLineInvariant(block_addr);
}

HnfSLCSFBackend::SeqEntry*
HnfSLCSFBackend::findSeq(SeqId id)
{
    auto it = std::find_if(seq.begin(), seq.end(), [id](const SeqEntry& entry) {
        return entry.valid && entry.victim.id == id;
    });
    return it == seq.end() ? nullptr : &*it;
}

const HnfSLCSFBackend::SeqEntry*
HnfSLCSFBackend::findSeq(SeqId id) const
{
    auto it = std::find_if(seq.begin(), seq.end(), [id](const SeqEntry& entry) {
        return entry.valid && entry.victim.id == id;
    });
    return it == seq.end() ? nullptr : &*it;
}

bool
HnfSLCSFBackend::seqContains(uint64_t block_addr) const
{
    return std::any_of(seq.begin(), seq.end(), [block_addr](const SeqEntry& e) {
        return e.valid && e.victim.blockAddr == block_addr;
    });
}

bool
HnfSLCSFBackend::seqCompletionMatches(SeqId id, uint64_t block_addr) const
{
    const SeqEntry* entry = findSeq(id);
    return entry && entry->victim.issued &&
        entry->victim.blockAddr == block_addr;
}

size_t
HnfSLCSFBackend::seqOccupancy() const
{
    return std::count_if(seq.begin(), seq.end(), [](const SeqEntry& entry) {
        return entry.valid;
    });
}

HnfSLCSFBackend::SeqVictim
HnfSLCSFBackend::frontPendingSeq() const
{
    panic_if(seqPending.empty(), "HnfSLCSF frontPendingSeq on empty queue\n");
    const SeqEntry* entry = findSeq(seqPending.front());
    panic_if(!entry, "HnfSLCSF pending SEQ id=%llu is missing\n",
             static_cast<unsigned long long>(seqPending.front()));
    return entry->victim;
}

void
HnfSLCSFBackend::markSeqIssued(SeqId id)
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
HnfSLCSFBackend::completeSfEvict(SeqId id, const std::vector<uint8_t>& data,
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
HnfSLCSFBackend::checkLineInvariant(uint64_t block_addr) const
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

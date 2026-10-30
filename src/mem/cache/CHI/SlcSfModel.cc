#include "mem/cache/CHI/SlcSfModel.hh"

#include <algorithm>
#include <istream>
#include <ostream>
#include <stdexcept>
#include <utility>

namespace gem5::Chi
{
namespace
{
bool sameState(SlcState a, SlcState b)
{ return a.mesi == b.mesi && a.unique == b.unique; }
bool any(const std::vector<bool> &v)
{ return std::any_of(v.begin(), v.end(), [](bool b) { return b; }); }
bool sharedRead(uint8_t op)
{ return op == 0x01 || op == 0x02 || op == 0x26; }
bool uniqueRead(uint8_t op)
{ return op == 0x07 || op == 0x0c || op == 0x0b; }
bool invalidate(uint8_t op)
{ return op == 0x09 || op == 0x0a; }
bool readData(uint8_t op)
{ return sharedRead(op) || op == 0x07 || op == 0x03 || op == 0x04; }
bool writeback(uint8_t op)
{ return op == 0x1b || op == 0x1a || op == 0x15; }
bool uniqueWrite(uint8_t op) { return op == 0x18 || op == 0x19; }
bool noSnpWrite(uint8_t op) { return op == 0x1c || op == 0x1d; }
bool supportedOpcode(uint8_t op)
{
    return readData(op) || uniqueRead(op) || invalidate(op) ||
        op == 0x08 || op == 0x0d || writeback(op) || op == 0x17 ||
        uniqueWrite(op) || noSnpWrite(op);
}
}

unsigned SlcSfModel::SfTagEntry::rnfIdWidth() const
{
    unsigned bits = 0;
    for (auto n = sfRnfVec.size() - 1; n; n >>= 1) ++bits;
    return bits;
}
void SlcSfModel::SfTagEntry::setRnfId(uint32_t r)
{
    if (r >= sfRnfVec.size()) throw std::out_of_range("SF RNF ID");
    rnfId = r;
}
SlcSfModel::SlcSfModel() : SlcSfModel(Config{}) {}
SlcSfModel::SlcSfModel(const Config &c)
    : cfg(c), slcTags(c.slcSets, c.slcWays, c.rnfCount),
      sfTags(c.sfSets, c.sfWays, c.rnfCount),
      slcData(c.slcSets, c.slcWays, c.lineBytes),
      lfsr(c.replacementSeed & 15), seq(c.seqEntries)
{
    if (!c.slcSets || !c.slcWays || !c.sfSets || !c.sfWays || !c.rnfCount)
        throw std::invalid_argument("SLCSF dimensions must be nonzero");
    if (!c.homeCount || c.homeIndex >= c.homeCount)
        throw std::invalid_argument("SLCSF home index must be less than nonzero home count");
    if (c.lineBytes != 64 || c.ramLatency != 2 || c.seqEntries != 8)
        throw std::invalid_argument("SLCSF requires 64B, RAM latency 2, 8 SEQ entries");
    // Default RRIP_SDM_PAIRS=1: 1024 sets compare low 5 index bits.
    // Reduced unit-test geometries retain at least one leader pair.
    unsigned indexBits = 0;
    for (auto n = c.slcSets - 1; n; n >>= 1) ++indexBits;
    const auto leaderBits = indexBits > 5 ? indexBits - 5 : 1;
    leaderMask = (std::size_t{1} << leaderBits) - 1;
}

uint64_t SlcSfModel::localLine(uint64_t addr) const
{
    const auto physical_line = addr / 64;
    if (cfg.homeCount == 1) return physical_line;
    if (physical_line % cfg.homeCount != cfg.homeIndex)
        throw std::invalid_argument("SLCSF address belongs to another HNF bank");
    return physical_line / cfg.homeCount;
}

std::optional<std::size_t>
SlcSfModel::matchingSlc(uint64_t addr, bool ns) const
{
    const auto line = localLine(addr);
    const auto &row = slcTags.row(line % cfg.slcSets);
    for (std::size_t w = 0; w < row.size(); ++w)
        if (row[w].mesi != SlcMesi::Invalid && row[w].ns == ns &&
            row[w].paTag == line / cfg.slcSets) return w;
    return std::nullopt;
}
std::optional<std::size_t>
SlcSfModel::matchingSf(uint64_t addr, bool ns) const
{
    const auto line = localLine(addr);
    const auto &row = sfTags.row(line % cfg.sfSets);
    for (std::size_t w = 0; w < row.size(); ++w)
        if (row[w].mesi != SlcMesi::Invalid && row[w].ns == ns &&
            row[w].paTag == line / cfg.sfSets) return w;
    return std::nullopt;
}
SlcSfModel::L3TagEntry *SlcSfModel::findSlc(uint64_t addr, bool ns)
{
    auto w = matchingSlc(addr, ns);
    return w ? &slcTags.at(localLine(addr) % cfg.slcSets, *w) : nullptr;
}
const SlcSfModel::L3TagEntry *SlcSfModel::findSlc(uint64_t addr, bool ns) const
{
    auto w = matchingSlc(addr, ns);
    return w ? &slcTags.at(localLine(addr) % cfg.slcSets, *w) : nullptr;
}
SlcSfModel::SfTagEntry *SlcSfModel::findSf(uint64_t addr, bool ns)
{
    auto w = matchingSf(addr, ns);
    return w ? &sfTags.at(localLine(addr) % cfg.sfSets, *w) : nullptr;
}
const SlcSfModel::SfTagEntry *SlcSfModel::findSf(uint64_t addr, bool ns) const
{
    auto w = matchingSf(addr, ns);
    return w ? &sfTags.at(localLine(addr) % cfg.sfSets, *w) : nullptr;
}
void SlcSfModel::preloadSlc(uint64_t addr, bool ns, SlcState state,
                           uint32_t rnf, const SlcLineData &data, std::size_t way)
{
    if (hasPipelineWork()) throw std::logic_error("preload during SLCSF traffic");
    if (rnf >= cfg.rnfCount) throw std::out_of_range("SLCSF RNF ID");
    const auto line = localLine(addr);
    auto &tag = slcTags.at(line % cfg.slcSets, way);
    tag.paTag = line / cfg.slcSets; tag.ns = ns;
    tag.setState(state); tag.rnfId = rnf; tag.rrpv = 2;
    auto &bytes = slcData.at(line % cfg.slcSets, way).bytes;
    std::copy(data.begin(), data.end(), bytes.begin());
}
void SlcSfModel::preloadSf(uint64_t addr, bool ns, SlcState state,
                          uint32_t rnf, const std::vector<bool> &v, std::size_t way)
{
    if (hasPipelineWork()) throw std::logic_error("preload during SLCSF traffic");
    if (rnf >= cfg.rnfCount || v.size() != cfg.rnfCount || state.dirty())
        throw std::invalid_argument("SF needs legal RNF vector and E/S/I state");
    const auto line = localLine(addr);
    auto &tag = sfTags.at(line % cfg.sfSets, way);
    tag.paTag = line / cfg.sfSets; tag.ns = ns;
    tag.setState(state); tag.rnfId = rnf; tag.sfRnfVec = v; tag.rrpv = 2;
}

uint32_t SlcSfModel::random(bool advance)
{
    // cmn_hns_sf_tagpipe.sv: the zero-inclusive, 16-state four-bit LFSR.
    const bool feedback = !((bool(lfsr & 14)) ^
                           (bool(lfsr & 2) ^ bool(lfsr & 1)));
    const auto next = (lfsr >> 1) | (uint32_t(feedback) << 3);
    if (advance) lfsr = next;
    return next;
}
std::size_t SlcSfModel::chooseSlcWay(const Attempt &a)
{
    const auto &row = slcTags.row(a.slcSet);
    for (std::size_t w = 0; w < row.size(); ++w)
        if (!row[w].state().valid() && !a.slcReserved[w]) return w;
    // SRRIP: age all candidates until one reaches the distant-reuse bucket.
    // Aging is committed with the allocation, so replay is side-effect free.
    const auto max = std::max_element(row.begin(), row.end(),
        [](const auto &a, const auto &b) { return a.rrpv < b.rrpv; })->rrpv;
    // Global last-victim mask rotates equal-RRPV candidates across sets.
    const auto start = lastSlcVictim == NoWay ? 0 : (lastSlcVictim + 1) % row.size();
    for (std::size_t n = 0; n < row.size(); ++n) {
        const auto way = (start + n) % row.size();
        if (row[way].rrpv == max) return way;
    }
    throw std::logic_error("empty replacement candidate set");
}
std::size_t SlcSfModel::chooseSfWay(const Attempt &a, bool allocate)
{
    const auto &row = sfTags.row(a.sfSet);
    for (std::size_t w = 0; w < row.size(); ++w)
        if (!row[w].state().valid() && !a.sfReserved[w]) return w;
    // The candidate is computed for every SF access. Only an allocating
    // victim advances the LFSR; nonallocating misses still protect the way.
    return random(allocate) % row.size();
}
std::vector<bool> SlcSfModel::targets(const SfTagEntry &tag) const
{
    auto v = tag.sfRnfVec;
    if (tag.unique) {
        std::fill(v.begin(), v.end(), false);
        v.at(tag.rnfId) = true;
    }
    return v;
}
const SlcSfSeqRequest *SlcSfModel::seqHit(uint64_t addr, bool ns) const
{
    for (const auto &e : seq)
        if (e.valid && e.req.addr == lineAddr(addr) && e.req.ns == ns)
            return &e.req;
    return nullptr;
}

bool SlcSfModel::tryAccept(const SlcSfRequest &r, uint64_t c)
{
    const auto line = localLine(r.addr);
    advance(c);
    if (c < nextAccept || returnPortUsed) return false;
    for (std::size_t i = 0; i < activeCount; ++i)
        if (attempts[activeOrder[i]]->req.token == r.token)
            throw std::invalid_argument("duplicate live SLCSF attempt token");
    if (activeCount == MaxAttempts || results.size() + (activeCount + 1) * 4 > MaxResults)
        return false;
    if (r.op == SlcSfOp::Lookup && !supportedOpcode(r.opcode))
        throw std::invalid_argument("unsupported ordinary SLCSF opcode");
    if (r.rnf >= cfg.rnfCount || (!r.sfTargets.empty() &&
                                 r.sfTargets.size() != cfg.rnfCount))
        throw std::invalid_argument("SLCSF request RNF/vector out of range");
    for (std::size_t index = 0; index < attempts.size(); ++index) {
        auto &slot = attempts[index];
        if (!slot) {
            slot.emplace(); slot->req = r; slot->accepted = c;
            slot->slcSet = line % cfg.slcSets;
            slot->sfSet = line % cfg.sfSets;
            slot->lookup.snoopTargets.assign(cfg.rnfCount, false);
            activeOrder[activeCount++] = index;
            nextAccept = c + 2;
            return true;
        }
    }
    return false;
}
void SlcSfModel::advance(uint64_t c)
{
    if (c < currentCycle) throw std::invalid_argument("SLCSF time cannot go backwards");
    while (currentCycle < c) {
        if (!hasClockWork()) {
            // No register can produce a new event during this interval.
            // History expiration and H0's previous-edge snapshot still need
            // to agree with repeated idle clocks at the destination edge.
            currentCycle = c;
            returnPortUsed = false;
            expireHistory();
            previousSeqRequest = seqRequest;
            break;
        }
        ++currentCycle;
        tick();
    }
}
bool SlcSfModel::hasPipelineWork() const
{
    return activeCount != 0;
}
bool SlcSfModel::hasClockWork() const
{ return hasPipelineWork() || seqNeedsClock(); }
bool SlcSfModel::hasWork() const
{ return hasPipelineWork() || !results.empty() || seqOccupancy() || seqInFlight; }
bool SlcSfModel::drained() const
{
    return !hasWork() && !seqNeedsClock() && !seqRequest &&
           !previousSeqRequest && slcHistory.empty() && sfHistory.empty() &&
           !returnPortUsed && currentCycle >= nextAccept;
}

void SlcSfModel::save(std::ostream &out) const
{
    if (!drained()) throw std::logic_error("SLCSF save requires drained state");
    // Fixed little-endian integers keep the format independent of padding,
    // vector<bool> representation and host word size.
    auto put = [&](uint64_t value) {
        for (unsigned i = 0; i < 8; ++i) out.put(char(value >> (i * 8)));
    };
    put(0x534c435346000002ULL);
    for (auto n : {cfg.slcSets, cfg.slcWays, cfg.sfSets, cfg.sfWays,
                   cfg.rnfCount, cfg.lineBytes, cfg.seqEntries}) put(n);
    put(cfg.ramLatency);
    put(cfg.homeCount); put(cfg.homeIndex);
    put(currentCycle); put(nextAccept); put(nextSeqId); put(lfsr);
    put(lastSlcVictim == NoWay ? UINT64_MAX : lastSlcVictim);
    put(notificationCounter); put(uint16_t(psel)); put(brripCounter);
    put(seqReadPointer);
    for (std::size_t s = 0; s < cfg.slcSets; ++s) {
        for (std::size_t w = 0; w < cfg.slcWays; ++w) {
            const auto &t = slcTags.at(s, w);
            put(t.paTag); put(t.ns); put(unsigned(t.mesi)); put(t.unique);
            put(t.rnfId); put(t.rrpv);
            const auto &data = slcData.at(s, w).bytes;
            out.write(reinterpret_cast<const char *>(data.data()), data.size());
        }
    }
    for (std::size_t s = 0; s < cfg.sfSets; ++s) {
        for (std::size_t w = 0; w < cfg.sfWays; ++w) {
            const auto &t = sfTags.at(s, w);
            put(t.paTag); put(t.ns); put(unsigned(t.mesi)); put(t.unique);
            put(t.rnfId); put(t.rrpv); put(t.slcIsSource);
            for (bool target : t.sfRnfVec) put(target);
        }
    }
    if (!out) throw std::runtime_error("Failed writing SLCSF checkpoint");
}

void SlcSfModel::load(std::istream &in)
{
    if (!drained()) throw std::logic_error("SLCSF load requires drained state");
    auto get = [&]() {
        uint64_t value = 0;
        for (unsigned i = 0; i < 8; ++i) {
            const auto byte = in.get();
            if (byte == std::char_traits<char>::eof())
                throw std::runtime_error("Truncated SLCSF checkpoint");
            value |= uint64_t(uint8_t(byte)) << (i * 8);
        }
        return value;
    };
    if (get() != 0x534c435346000002ULL)
        throw std::runtime_error("Unsupported SLCSF checkpoint format");
    for (auto n : {cfg.slcSets, cfg.slcWays, cfg.sfSets, cfg.sfWays,
                   cfg.rnfCount, cfg.lineBytes, cfg.seqEntries})
        if (get() != n) throw std::runtime_error("SLCSF checkpoint geometry changed");
    if (get() != cfg.ramLatency)
        throw std::runtime_error("SLCSF checkpoint RAM latency changed");
    if (get() != cfg.homeCount || get() != cfg.homeIndex)
        throw std::runtime_error("SLCSF checkpoint home striping changed");
    currentCycle = get(); nextAccept = get(); nextSeqId = get(); lfsr = get();
    const auto victim = get();
    lastSlcVictim = victim == UINT64_MAX ? NoWay : victim;
    notificationCounter = get(); psel = int16_t(uint16_t(get()));
    brripCounter = get(); seqReadPointer = get();
    if (seqReadPointer >= PhysicalSeqSlots || nextAccept > currentCycle ||
        lfsr > 15 || (lastSlcVictim != NoWay && lastSlcVictim >= cfg.slcWays))
        throw std::runtime_error("Invalid SLCSF checkpoint control state");
    for (std::size_t s = 0; s < cfg.slcSets; ++s) {
        for (std::size_t w = 0; w < cfg.slcWays; ++w) {
            auto &t = slcTags.at(s, w);
            t.paTag = get(); t.ns = get(); t.mesi = SlcMesi(get());
            t.unique = get(); t.rnfId = get(); t.rrpv = get();
            if (unsigned(t.mesi) > unsigned(SlcMesi::Modified) ||
                t.rnfId >= cfg.rnfCount || t.rrpv > 3)
                throw std::runtime_error("Invalid SLC checkpoint tag");
            auto &data = slcData.at(s, w).bytes;
            in.read(reinterpret_cast<char *>(data.data()), data.size());
        }
    }
    for (std::size_t s = 0; s < cfg.sfSets; ++s) {
        for (std::size_t w = 0; w < cfg.sfWays; ++w) {
            auto &t = sfTags.at(s, w);
            t.paTag = get(); t.ns = get(); t.mesi = SlcMesi(get());
            t.unique = get(); t.rnfId = get(); t.rrpv = get();
            t.slcIsSource = get();
            for (std::size_t i = 0; i < cfg.rnfCount; ++i) t.sfRnfVec[i] = get();
            if (unsigned(t.mesi) > unsigned(SlcMesi::Exclusive) ||
                t.rnfId >= cfg.rnfCount || t.rrpv > 3)
                throw std::runtime_error("Invalid SF checkpoint tag");
        }
    }
    if (!in) throw std::runtime_error("Failed reading SLCSF checkpoint");
}
std::optional<SlcSfResult> SlcSfModel::popResult()
{
    if (results.empty()) return std::nullopt;
    auto r = std::move(results.front()); results.pop_front(); return r;
}
bool SlcSfModel::historyHit(const std::deque<History> &h,
                          std::size_t set, std::size_t way) const
{
    return std::any_of(h.begin(), h.end(), [&](const auto &v) {
        return v.set == set && v.way == way;
    });
}
void SlcSfModel::expireHistory()
{
    auto expire = [&](auto &h) {
        // H7 inserts in admission order and every record has the same
        // lifetime. Removal on replay preserves this expiration order.
        while (!h.empty() && h.front().expires < currentCycle)
            h.pop_front();
    };
    expire(slcHistory); expire(sfHistory);
}
void SlcSfModel::removeHistory(uint64_t token)
{
    auto erase = [&](auto &h) {
        h.erase(std::remove_if(h.begin(), h.end(), [&](const auto &v) {
            return v.token == token;
        }), h.end());
    };
    erase(slcHistory); erase(sfHistory);
}

void SlcSfModel::snapshotReservations(Attempt &a, bool slc)
{
    // These masks are sampled one edge before the corresponding tag compare:
    // SLC H3->H4 and SF H4->H5. Raw H5/H6 selections remain reservations even
    // if H6 later replays them; only the history FIFO input is replay-gated.
    auto &mask = slc ? a.slcReserved : a.sfReserved;
    mask.assign(slc ? cfg.slcWays : cfg.sfWays, false);
    const auto set = slc ? a.slcSet : a.sfSet;
    for (const auto &h : slc ? slcHistory : sfHistory)
        if (h.set == set) mask[h.way] = true;
    for (const auto &slot : attempts) {
        if (!slot || &*slot == &a) continue;
        const auto age = currentCycle - slot->accepted;
        const auto way = slc ? slot->slcWay : slot->sfWay;
        const bool raw = slc ? (age == 5 || age == 6) : age == 6;
        if (raw && way != NoWay &&
            (slc ? slot->slcSet : slot->sfSet) == set) mask[way] = true;
    }
}

void SlcSfModel::checkReplay(Attempt &a)
{
    // H6 combines both sides before either architectural update is enabled.
    // POC compares at H3 (+2), registers hp_replay_h4 at +3, and
    // LAT2 aligns that input through H4a/H5 before the H6 combination.
    if (a.req.victimConflict || (victimConflictQuery &&
        victimConflictQuery(a.req, a.accepted + 2)))
        a.replay = SlcSfReplay::VictimConflict;
    if (a.slcWay != NoWay && historyHit(slcHistory, a.slcSet, a.slcWay))
        a.replay = SlcSfReplay::SlcSetWay;
    if (a.sfWay != NoWay && historyHit(sfHistory, a.sfSet, a.sfWay))
        a.replay = SlcSfReplay::SfSetWay;
}

void SlcSfModel::recordHistory(const Attempt &a)
{
    auto record = [&](auto &history, std::size_t set, std::size_t way) {
        if (way == NoWay) return;
        if (history.size() == 4)
            throw std::logic_error("SLCSF set-way history pipeline overflow");
        history.push_back({set, way, a.req.token, a.accepted + HistoryLastOffset});
    };
    // LAT=2, ECC off: H6-qualified entry appears at +7 and traverses the
    // four registers until +14 inclusive; the +15 edge clears the last one.
    record(slcHistory, a.slcSet, a.slcWay);
    record(sfHistory, a.sfSet, a.sfWay);
}

void SlcSfModel::snapshotSlc(Attempt &a)
{
    const uint64_t line = localLine(a.req.addr);
    a.slcSet = line % cfg.slcSets;
    if (auto sw = matchingSlc(a.req.addr, a.req.ns)) {
        a.slcWay = *sw; a.slcNext = slcTags.at(a.slcSet, *sw);
        a.lookup.slcHit = true; a.lookup.slcState = a.slcNext.state();
        a.lookup.slcRnf = a.slcNext.rnfId;
    }
    const auto leader = a.slcSet & leaderMask;
    const bool brrip = leader == 1 || (leader > 1 && psel < 0);
    a.insertionRrpv = brrip && brripCounter != 31 ? 3 : 2;
    if (a.req.op == SlcSfOp::Fill && !a.lookup.slcHit) {
        if (brrip) brripCounter = (brripCounter + 1) & 31;
        a.slcWay = chooseSlcWay(a);
        if (a.req.state.valid() && a.req.allocate && a.req.cacheable && !a.req.noFill) {
            a.victimTag = slcTags.at(a.slcSet, a.slcWay);
            a.fullSetVictim = a.victimTag.state().valid();
            if (a.fullSetVictim) a.replacementAge = 3 - a.victimTag.rrpv;
        }
    }
    if (a.req.op == SlcSfOp::Lookup && a.lookup.slcHit) {
        if (leader == 0) psel = std::min<int>(511, psel + 1);
        if (leader == 1) psel = std::max<int>(-512, psel - 1);
    }
}
void SlcSfModel::decide(Attempt &a)
{
    const auto &r = a.req;
    auto &l = a.lookup;
    const uint64_t line = localLine(r.addr);
    a.sfSet = line % cfg.sfSets;
    const bool sw = l.slcHit;
    const auto fw = matchingSf(r.addr, r.ns);
    l.sfHit = fw.has_value();
    const auto *sq = seqHit(r.addr, r.ns);
    // Ordinary EVICT/FILL and explicit updates do not probe the SEQ.
    // RTL also probes EVICT+RPU, which is outside this ordinary-op interface.
    l.seqHit = r.op == SlcSfOp::Lookup && sq != nullptr &&
               (!r.seq || sq->id != r.seqId);
    if (l.seqHit) a.seqHitId = sq->id;
    if (fw) {
        a.sfWay = *fw; a.sfNext = sfTags.at(a.sfSet, *fw);
        l.sfState = a.sfNext.state(); l.sfRnf = a.sfNext.rnfId;
    }
    const bool coherent = !r.nonCoherent && r.opcode != 0x04 && !noSnpWrite(r.opcode);
    const bool copy = sharedRead(r.opcode);
    const bool unique = uniqueRead(r.opcode);
    const bool inv = invalidate(r.opcode);
    const bool allocating = r.cacheable && coherent;
    bool sfAllocate = false;

    if (r.op == SlcSfOp::Fill) {
        if (r.state.valid() && r.allocate && r.cacheable && !r.noFill) {
            const auto old = sw ? a.slcNext : a.victimTag;
            // The global RRIP mask register advances at H4->H5, before
            // replay. It is policy state, not an architectural tag write.
            if (a.fullSetVictim) lastSlcVictim = a.slcWay;
            if (!sw && old.state().valid()) {
                l.victim = true;
                l.victimAddr = physicalAddress(old.paTag * cfg.slcSets + a.slcSet);
                l.victimNs = old.ns; l.victimState = old.state();
                a.victimData = old.state().dirty();
                a.returnData = a.victimData;
            }
            a.slcNext.paTag = line / cfg.slcSets;
            a.slcNext.ns = r.ns; a.slcNext.rnfId = r.rnf;
            a.slcNext.setState(r.state);
            if (r.likelyShared && (r.opcode == 0x01 || r.opcode == 0x02) &&
                r.state.mesi == SlcMesi::Exclusive) a.slcNext.mesi = SlcMesi::Shared;
            a.slcNext.rrpv = sw ? 0 :
                ((fw && a.sfNext.slcIsSource) ? 0 : a.insertionRrpv);
            a.writeSlc = a.writeData = true;
        }
    } else if (r.op == SlcSfOp::FlushSlc || r.op == SlcSfOp::FlushBoth) {
        if (sw) { a.slcNext.setState({}); a.writeSlc = true; }
    }
    if (r.op == SlcSfOp::FlushSf || r.op == SlcSfOp::FlushBoth) {
        if (fw) { a.sfNext.setState({}); a.writeSf = true; }
    } else if (r.op == SlcSfOp::UpdateSf) {
        if (r.state.dirty()) throw std::invalid_argument("SF cannot contain M state");
        if (fw || r.state.valid()) {
            sfAllocate = !fw;
            a.sfNext.setState(r.state); a.sfNext.rnfId = r.rnf;
            a.sfNext.sfRnfVec = r.sfTargets;
            if (a.sfNext.sfRnfVec.empty()) {
                a.sfNext.sfRnfVec.assign(cfg.rnfCount, false);
                if (r.state.valid()) a.sfNext.sfRnfVec[r.rnf] = true;
            }
            a.writeSf = true;
        }
    } else if (r.op == SlcSfOp::Lookup) {
        if (fw && (coherent || uniqueWrite(r.opcode))) {
            l.snoopTargets = targets(a.sfNext);
            if (r.opcode == 0x03 && sw) // ReadOnce can use a clean local copy.
                std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), false);
            if (copy && !a.sfNext.unique && sw)
                std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), false);
            if (!a.sfNext.unique && !sw && (copy || r.opcode == 0x03)) {
                std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), false);
                if (!a.sfNext.sfRnfVec[r.rnf]) l.snoopTargets[a.sfNext.rnfId] = true;
            }
            l.snoopTargets[r.rnf] = !coherent && l.snoopTargets[r.rnf];
            l.broadcast = !a.sfNext.unique &&
                (unique || inv || uniqueWrite(r.opcode));
        }
        if (l.seqHit) {
            // A SEQ hit has lost the in-RAM directory precision. RTL uses
            // broadcast for exclusives/CMOs and copy reads without SLC data.
            l.broadcast = unique || inv || uniqueWrite(r.opcode) ||
                r.opcode == 0x08 ||
                ((!sw) && (copy || r.opcode == 0x03));
            if (l.broadcast)
                std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), true);
            l.snoopTargets[r.rnf] = l.broadcast && !coherent;
        }
        if (r.opcode == 0x08 && !l.seqHit &&
            !(fw && a.sfNext.unique && a.sfNext.mesi == SlcMesi::Exclusive))
            std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), false);
        if (coherent && r.snoopMe &&
            (r.opcode == 0x08 || r.opcode == 0x09 || r.opcode == 0x0a))
            l.snoopTargets[r.rnf] = true;
        if (r.opcode == 0x04 || r.opcode == 0x0d ||
            writeback(r.opcode) || r.opcode == 0x17)
            std::fill(l.snoopTargets.begin(), l.snoopTargets.end(), false);
        if (unique || uniqueWrite(r.opcode) || noSnpWrite(r.opcode)) l.snoopOpcode = 0x07;
        else if (r.opcode == 0x09) l.snoopOpcode = 0x09;
        else if (r.opcode == 0x0a) l.snoopOpcode = 0x0a;
        else if (r.opcode == 0x02) l.snoopOpcode = 0x02;
        else if (r.opcode == 0x03) l.snoopOpcode = 0x03;
        else if (r.opcode == 0x26) l.snoopOpcode = 0x04;
        else if (r.opcode == 0x08) l.snoopOpcode = 0x08;
        else l.snoopOpcode = 0x01;
        l.selfCancel = !coherent &&
            ((l.seqHit && l.broadcast) ||
             (fw && !a.sfNext.unique && a.sfNext.sfRnfVec[r.rnf] &&
              uniqueWrite(r.opcode)));
        if (sw) {
            auto state = a.slcNext.state();
            if (unique || inv || uniqueWrite(r.opcode) || noSnpWrite(r.opcode) ||
                writeback(r.opcode) || (copy && state.unique &&
                a.slcNext.rnfId == r.rnf &&
                (state.mesi == SlcMesi::Exclusive ||
                 (state.dirty() && r.opcode != 0x02)))) state = {};
            else {
                if (r.opcode == 0x08 && state.dirty()) state.mesi = SlcMesi::Exclusive;
                if (r.opcode != 0x03 && r.opcode != 0x08 && r.opcode != 0x0d)
                    state.unique = false;
            }
            if ((uniqueWrite(r.opcode) || noSnpWrite(r.opcode)) && !r.noFill)
                state = a.slcNext.state();
            a.writeSlc = !sameState(state, a.slcNext.state());
            a.slcNext.setState(state);
            // READ hit data-enable is opcode independent in l3_tagpipe.
            a.returnData = true;
            a.slcNext.rrpv = 0;
        }
        if (fw) {
            if (inv || (uniqueWrite(r.opcode) && r.noFill)) {
                a.sfNext.setState({}); a.writeSf = true;
            }
            else if (unique && allocating) {
                a.sfNext.setState({SlcMesi::Exclusive, true});
                a.sfNext.rnfId = r.rnf;
                a.sfNext.sfRnfVec.assign(cfg.rnfCount, false);
                a.sfNext.sfRnfVec[r.rnf] = true; a.writeSf = true;
            } else if (copy && allocating) {
                auto v = targets(a.sfNext);
                v[r.rnf] = true;
                if (a.sfNext.rnfId != r.rnf) a.sfNext.unique = false;
                a.sfNext.rnfId = r.rnf; a.sfNext.sfRnfVec = std::move(v);
                a.writeSf = true;
            } else if (r.opcode == 0x0d || writeback(r.opcode)) {
                auto v = targets(a.sfNext); v[r.rnf] = false;
                a.sfNext.sfRnfVec = v;
                if (!any(v)) a.sfNext.setState({});
                else if (a.sfNext.rnfId == r.rnf)
                    a.sfNext.rnfId = std::find(v.begin(), v.end(), true) - v.begin();
                a.writeSf = true;
            }
        } else if (!fw && allocating && (copy || unique) && !l.seqHit) {
            sfAllocate = a.writeSf = true;
            a.sfNext.rnfId = r.rnf;
            a.sfNext.sfRnfVec.assign(cfg.rnfCount, false);
            a.sfNext.sfRnfVec[r.rnf] = true;
            // EN/MN local copies create EN directory entries, not SN.
            SlcState state{SlcMesi::Exclusive, true};
            if (!unique && sw) {
                state.mesi = l.slcState.mesi == SlcMesi::Shared ?
                    SlcMesi::Shared : SlcMesi::Exclusive;
                state.unique = l.slcState.unique && l.slcRnf == r.rnf &&
                    l.slcState.mesi != SlcMesi::Shared &&
                    !(r.opcode == 0x02 && l.slcState.dirty());
            }
            a.sfNext.setState(state);
        }
        l.noFill = (r.opcode == 0x04 || noSnpWrite(r.opcode)) &&
                   (l.sfHit || l.seqHit);
        l.memoryRead = readData(r.opcode) && !sw && !any(l.snoopTargets) && !l.selfCancel;
    }
    // EVICT/FILL also updates an existing directory for a relinquishing
    // writer; a write does not allocate an SF entry on a miss.
    if (r.op == SlcSfOp::Fill && fw &&
        (writeback(r.opcode) || uniqueWrite(r.opcode))) {
        if (uniqueWrite(r.opcode) && !r.noFill) a.sfNext.setState({});
        else if (writeback(r.opcode)) {
            auto v = targets(a.sfNext); v[r.rnf] = false;
            a.sfNext.sfRnfVec = v;
            if (!any(v)) a.sfNext.setState({});
            else if (a.sfNext.rnfId == r.rnf)
                a.sfNext.rnfId = std::find(v.begin(), v.end(), true) - v.begin();
        }
        a.writeSf = true;
    }
    // SF_PRECISE_SETWAY_EN resets to zero: even ReadOnce miss/no-update
    // accesses reserve their replacement candidate and can replay on it.
    if (!fw) a.sfWay = chooseSfWay(a, sfAllocate);
    if (sfAllocate) {
        // RTL clocks the random selector on H5 victim selection even if
        // the attempt is subsequently replayed in H6.
        const auto &old = sfTags.at(a.sfSet, a.sfWay);
        if (old.state().valid()) {
            SlcSfSeqRequest victim;
            victim.addr = physicalAddress(old.paTag * cfg.sfSets + a.sfSet);
            victim.ns = old.ns; victim.state = old.state();
            victim.targets = targets(old); victim.rnf = old.rnfId;
            victim.broadcast = !old.unique;
            a.victim = std::move(victim);
            // H5 checks fullness; the actual free-pointer mux is H6.
            if (seqOccupancy() == seq.size()) a.replay = SlcSfReplay::SeqFull;
        }
        a.sfNext.paTag = line / cfg.sfSets; a.sfNext.ns = r.ns;
        a.sfNext.rrpv = 2; a.sfNext.slcIsSource = sw;
    }
    l.dataExpected = a.returnData;
    a.completionOffset = LookupOffset;
    if (a.returnData) a.completionOffset = DataOffset + 1;
    if (a.writeSlc) a.completionOffset = std::max(a.completionOffset, SlcTagCommitOffset);
    if (a.writeSf) a.completionOffset = std::max(a.completionOffset, SfTagCommitOffset);
    if (a.writeData) a.completionOffset = CommitOffset;
    a.decided = true;
}

void SlcSfModel::emit(Attempt &a, SlcSfResultKind kind, unsigned beat)
{
    if (results.size() >= MaxResults) throw std::logic_error("SLCSF result credit invariant");
    SlcSfResult r;
    r.token = a.req.token; r.srcid = a.req.srcid; r.txnid = a.req.txnid;
    r.kind = kind; r.cycle = currentCycle;
    // Terminal results retain all metadata. After these events the attempt
    // only holds the return-port reservation or awaits retirement; neither
    // operation reads lookup again, so its owned vector can move with it.
    if (kind == SlcSfResultKind::CommitDone || kind == SlcSfResultKind::Replay)
        r.lookup = std::move(a.lookup);
    else
        r.lookup = a.lookup;
    r.replay = a.replay; r.beat = beat; r.victim = a.victimData;
    if (kind == SlcSfResultKind::DataBeat)
        std::copy_n(a.line.begin() + beat * 32, 32, r.data.begin());
    results.push_back(std::move(r));
    ++notificationCounter;
}
void SlcSfModel::commitSlcTag(Attempt &a)
{
    if (a.writeSlc) {
        if (a.fullSetVictim) {
            auto &row = slcTags.row(a.slcSet);
            for (auto &t : row) t.rrpv = std::min(3, t.rrpv + a.replacementAge);
        }
        slcTags.at(a.slcSet, a.slcWay) = a.slcNext;
    }
}
void SlcSfModel::commitSfTag(Attempt &a)
{
    if (a.writeSf) sfTags.at(a.sfSet, a.sfWay) = a.sfNext;
}
void SlcSfModel::commitDataBeat(Attempt &a, unsigned beat)
{
    auto &bytes = slcData.at(a.slcSet, a.slcWay).bytes;
    std::copy_n(a.req.data.begin() + beat * 32, 32, bytes.begin() + beat * 32);
}
void SlcSfModel::tick()
{
    returnPortUsed = false;
    tickSeq();
    expireHistory();
    // Walk the already ordered live slots. Each pass remains a distinct RTL
    // edge operation: reservation, reads, then writes/retirement.
    for (std::size_t i = 0; i < activeCount; ++i) {
        const auto *a = &*attempts[activeOrder[i]];
        if (currentCycle == a->accepted + LookupOffset &&
            a->replay == SlcSfReplay::None) recordHistory(*a);
    }
    for (std::size_t i = 0; i < activeCount; ++i) {
        auto *a = &*attempts[activeOrder[i]];
        const auto age = currentCycle - a->accepted;
        if (age == SlcSnapshotOffset - 1) snapshotReservations(*a, true);
        if (age == DecisionOffset - 1) snapshotReservations(*a, false);
    }
    // RAM responses were read before this edge's recirculated writes. Read
    // every attempt before committing any tag, rather than letting the host
    // iteration order turn an older invalidate into a younger false miss.
    for (std::size_t i = 0; i < activeCount; ++i) {
        auto *a = &*attempts[activeOrder[i]];
        const auto age = currentCycle - a->accepted;
        if (age == SlcSnapshotOffset) snapshotSlc(*a);
        if (age == DecisionOffset) decide(*a);
        if (a->returnData && age >= SeqOffset && age <= SeqOffset + 1) {
            const auto &data = slcData.at(a->slcSet, a->slcWay).bytes;
            const auto offset = (age - SeqOffset) * 32;
            std::copy_n(data.begin() + offset, 32, a->line.begin() + offset);
        }
    }
    for (std::size_t i = 0; i < activeCount; ++i) {
        auto &a = *attempts[activeOrder[i]];
        const auto age = currentCycle - a.accepted;
        if (age == SeqOffset) checkReplay(a);
        if (age == SeqOffset && a.seqHitId) {
            // H5 hit updates these conservative registers at H6 even when
            // H6 replay cancels the architectural tag/data changes.
            for (auto &entry : seq) {
                if (!entry.valid || entry.req.id != *a.seqHitId) continue;
                entry.req.broadcast = true;
                std::fill(entry.req.targets.begin(), entry.req.targets.end(), true);
                if (seqInFlight && seqInFlight->id == entry.req.id)
                    seqInFlight = entry.req;
            }
        }
        if (age == SeqOffset && a.decided && a.replay == SlcSfReplay::None) {
            if (a.victim) {
                for (std::size_t i = 0; i < seq.size(); ++i)
                    if (!seq[i].valid) { a.seqSlot = i; break; }
                if (!a.seqSlot) a.replay = SlcSfReplay::SeqSlotBusy;
            }
            if (a.replay != SlcSfReplay::None) removeHistory(a.req.token);
        }
        if (age == SeqWriteOffset && a.seqSlot &&
            a.replay == SlcSfReplay::None) {
            auto &entry = seq.at(*a.seqSlot);
            if (entry.valid) throw std::logic_error("SEQ write to occupied slot");
            a.victim->id = nextSeqId++;
            a.victim->createdCycle = currentCycle;
            entry = SeqEntry{};
            entry.req = *a.victim; entry.valid = true;
            a.seqWritten = true;
        }
        // Every ordinary READ/EVICT reserves the H7 control return slot,
        // including replay. A recirculated update adds H8a/H9 bubbles.
        if (age == ReturnOffset) {
            returnPortUsed = true;
            const bool update = a.replay == SlcSfReplay::None &&
                (a.writeSlc || a.writeSf || a.writeData);
            nextAccept = std::max(nextAccept, currentCycle + (update ? 3 : 1));
        }
        if (a.replay != SlcSfReplay::None) {
            if (age == LookupOffset) emit(a, SlcSfResultKind::Replay);
            continue;
        }
        if (age == LookupOffset) {
            emit(a, SlcSfResultKind::LookupDone); a.lookupSent = true;
        }
        if (a.returnData && age >= DataOffset && age <= DataOffset + 1) {
            emit(a, SlcSfResultKind::DataBeat, age - DataOffset); ++a.beatsSent;
        }
        if (age == SlcTagCommitOffset) commitSlcTag(a);
        if (age == SfTagCommitOffset) commitSfTag(a);
        if (a.writeData && age >= FillDataOffset && age <= FillDataOffset + 1)
            commitDataBeat(a, age - FillDataOffset);
        if (age == a.completionOffset) {
            emit(a, SlcSfResultKind::CommitDone); a.complete = true;
        }
    }
    std::size_t kept = 0;
    for (std::size_t i = 0; i < activeCount; ++i) {
        auto &a = attempts[activeOrder[i]];
        if (currentCycle >= a->accepted + ReturnOffset &&
            (a->complete || (a->replay != SlcSfReplay::None &&
                             currentCycle >= a->accepted + LookupOffset))) a.reset();
        else activeOrder[kept++] = activeOrder[i];
    }
    activeCount = kept;
}

std::size_t SlcSfModel::seqOccupancy() const
{
    return std::count_if(seq.begin(), seq.end(), [](const auto &e) { return e.valid; });
}
bool SlcSfModel::seqNeedsClock() const
{
    bool unsent = false;
    for (const auto &entry : seq) {
        if (entry.sentCycle || entry.releaseCycle) return true;
        unsent |= entry.valid && !entry.sent;
    }
    const auto *selected = seqReadPointer < seq.size() ? &seq[seqReadPointer] : nullptr;
    if (selected && selected->valid && selected->sent) return true;
    if ((!selected || !selected->valid) && unsent) return true;
    if (selected && selected->valid && !selected->sent)
        return !seqRequest || seqRequest->id != selected->req.id ||
               seqRequest->broadcast != selected->req.broadcast;
    return seqRequest.has_value();
}
void SlcSfModel::tickSeq()
{
    // Both the read pointer and request register sample the previous edge.
    // Process ACK/deallocation registers only after computing their inputs.
    const auto *selected = seqReadPointer < seq.size() ? &seq[seqReadPointer] : nullptr;
    const bool unsent = std::any_of(seq.begin(), seq.end(),
        [](const auto &entry) { return entry.valid && !entry.sent; });
    const bool haveRequest = selected && selected->valid && !selected->sent;
    const bool newlyReady = haveRequest &&
        (!seqRequest || seqRequest->id != selected->req.id);
    const auto readyCycle = haveRequest && !newlyReady ?
        seqRequest->readyCycle : currentCycle;
    if ((selected && selected->valid && selected->sent) ||
        ((!selected || !selected->valid) && unsent))
        seqReadPointer = (seqReadPointer + 1) % PhysicalSeqSlots;
    // Capture the old register before updating it. Copy assignment reuses
    // each snapshot's target storage while a request remains pending,
    // instead of allocating a new temporary vector on every clock.
    previousSeqRequest = seqRequest;
    if (haveRequest) {
        seqRequest = selected->req;
        seqRequest->readyCycle = readyCycle;
    } else {
        seqRequest.reset();
    }
    if (newlyReady) ++notificationCounter;
    for (auto &entry : seq) {
        if (entry.sentCycle && *entry.sentCycle <= currentCycle) {
            entry.sent = true;
            entry.sentCycle.reset();
        }
        if (entry.releaseCycle && *entry.releaseCycle <= currentCycle) {
            if (seqInFlight && seqInFlight->id == entry.req.id)
                seqInFlight = entry.req;
            entry.valid = false;
            entry.releaseCycle.reset();
        }
    }
}
std::optional<SlcSfSeqRequest> SlcSfModel::pendingSeq(bool previousEdge) const
{
    const auto &request = previousEdge ? previousSeqRequest : seqRequest;
    if (seqInFlight || !request) return std::nullopt;
    for (const auto &entry : seq)
        if (entry.valid && !entry.sent && !entry.sentCycle &&
            entry.req.id == request->id) return request;
    return std::nullopt;
}
std::optional<SlcSfSeqRequest> SlcSfModel::seqSnapshot(uint64_t id) const
{
    for (const auto &e : seq)
        if (e.valid && e.req.id == id) return e.req;
    if (seqInFlight && seqInFlight->id == id) return seqInFlight;
    return std::nullopt;
}
void SlcSfModel::acceptSeq(uint64_t id, bool hazard)
{
    if (seqInFlight) throw std::logic_error("only one ordinary SEQ transaction may enter POCQ");
    for (auto &e : seq) if (e.valid && e.req.id == id && !e.sent) {
        if (!seqRequest || seqRequest->id != id)
            throw std::invalid_argument("accept of unregistered SEQ request");
        e.sentCycle = currentCycle + 1; seqInFlight = e.req;
        if (!hazard) e.releaseCycle = currentCycle + SeqNoHazardReleaseDelay;
        return;
    }
    throw std::invalid_argument("accept of unknown SEQ entry");
}
void SlcSfModel::releaseSeq(uint64_t id)
{
    if (!seqInFlight || seqInFlight->id != id)
        throw std::invalid_argument("release of unknown SEQ transaction");
    for (auto &e : seq) if (e.valid && e.req.id == id) {
        if (!e.sent && !e.sentCycle)
            throw std::logic_error("release of unsent SEQ entry");
        // Foreground lookups can turn a sleeping eviction into a broadcast.
        // Preserve that latest directory snapshot when its valid slot clears.
        seqInFlight = e.req;
        if (!e.releaseCycle) e.releaseCycle = currentCycle + 1;
        return;
    }
    // A repeated deallocation after the valid edge is harmless.
}
void SlcSfModel::completeSeq(uint64_t id)
{
    releaseSeq(id);
    seqInFlight.reset();
}

} // namespace gem5::Chi

#ifndef __MEM_CACHE_CHI_SLC_SF_MODEL_HH__
#define __MEM_CACHE_CHI_SLC_SF_MODEL_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <iosfwd>
#include <utility>
#include <optional>
#include <vector>

#include "mem/cache/CHI/SlcSfTypes.hh"

namespace gem5::Chi
{

/** Pure, cycle-stepped CMN ordinary SLC/SF datapath (LAT=2, no ECC/DSLICES).
 * All data and tag snapshots are owned by the attempt until CommitDone. The
 * finite attempt bank is the backing store for the pipeline registers; there
 * is no unbounded input queue. A failed tryAccept has no side effects.
 */
class SlcSfModel
{
  public:
    struct Config
    {
        std::size_t slcSets = 1024, slcWays = 16;
        std::size_t sfSets = 2048, sfWays = 16;
        std::size_t rnfCount = 1, lineBytes = 64;
        unsigned ramLatency = 2;
        std::size_t seqEntries = 8;
        uint32_t replacementSeed = 0;
        std::size_t homeCount = 1, homeIndex = 0;
    };
    using MesiState = SlcMesi;
    struct L3TagEntry
    {
        bool unique = false;
        uint64_t paTag = 0;
        bool ns = true;
        uint32_t rnfId = 0;
        MesiState mesi = MesiState::Invalid;
        uint8_t rrpv = 3;
        explicit L3TagEntry(std::size_t = 1) {}
        SlcState state() const { return {mesi, unique}; }
        void setState(SlcState s) { mesi = s.mesi; unique = s.unique; }
    };
    struct SfTagEntry
    {
        bool slcIsSource = false;
        std::vector<bool> sfRnfVec;
        uint64_t paTag = 0;
        bool ns = true, unique = false;
        uint32_t rnfId = 0;
        MesiState mesi = MesiState::Invalid;
        uint8_t rrpv = 3;
        explicit SfTagEntry(std::size_t n = 1) : sfRnfVec(n, false) {}
        SlcState state() const { return {mesi, unique}; }
        void setState(SlcState s) { mesi = s.mesi; unique = s.unique; }
        void setPaTag(uint64_t t) { paTag = t; }
        void setRnfId(uint32_t r);
        unsigned rnfIdWidth() const;
    };
    struct L3DataEntry
    {
        std::vector<uint8_t> bytes;
        explicit L3DataEntry(std::size_t n = 64) : bytes(n, 0) {}
    };
    template<class Entry>
    class Ram
    {
      public:
        using Row = std::vector<Entry>;
        Ram(std::size_t sets, std::size_t ways, std::size_t width)
            : _sets(sets), _ways(ways), _width(width),
              entries(sets, Row(ways, Entry(width))) {}
        Entry &at(std::size_t s, std::size_t w) { return entries.at(s).at(w); }
        const Entry &at(std::size_t s, std::size_t w) const
        { return entries.at(s).at(w); }
        Row &row(std::size_t s) { return entries.at(s); }
        const Row &row(std::size_t s) const { return entries.at(s); }
        std::size_t sets() const { return _sets; }
        std::size_t ways() const { return _ways; }
        std::size_t rnfNum() const { return _width; }
        std::size_t lineSize() const { return _width; }
        unsigned rnfIdWidth() const;
      private:
        std::size_t _sets, _ways, _width;
        std::vector<Row> entries;
    };
    using L3TagRam = Ram<L3TagEntry>;
    using SfTagRam = Ram<SfTagEntry>;
    using L3DataRam = Ram<L3DataEntry>;

    using VictimConflictQuery =
        std::function<bool(const SlcSfRequest &, uint64_t sampleCycle)>;
    void setVictimConflictQuery(VictimConflictQuery query)
    { victimConflictQuery = std::move(query); }

    SlcSfModel();
    explicit SlcSfModel(const Config &config);
    bool tryAccept(const SlcSfRequest &request, uint64_t cycle);
    bool tryAccept(const SlcSfRequest &request)
    { return tryAccept(request, currentCycle); }
    void advance(uint64_t cycle);
    uint64_t cycle() const { return currentCycle; }
    bool hasWork() const;
    bool hasPipelineWork() const;
    bool hasClockWork() const;
    bool drained() const;
    // Stable RAM and replacement state only. In-flight protocol/register
    // state must finish before a checkpoint can be taken.
    void save(std::ostream &out) const;
    void load(std::istream &in);
    std::optional<SlcSfResult> popResult();
    std::optional<uint64_t> frontResultCycle() const
    { return results.empty() ? std::nullopt : std::optional<uint64_t>(results.front().cycle); }
    uint64_t notificationSerial() const { return notificationCounter; }
    std::optional<SlcSfSeqRequest> pendingSeq(bool previousEdge = false) const;
    std::optional<SlcSfSeqRequest> seqSnapshot(uint64_t id) const;
    void acceptSeq(uint64_t id, bool hazard);
    void releaseSeq(uint64_t id);
    void completeSeq(uint64_t id);
    std::size_t seqOccupancy() const;
    bool hasUnsentSeq() const
    {
        for (const auto &entry : seq)
            if (entry.valid && !entry.sent && !entry.sentCycle) return true;
        return false;
    }

    bool needsPocqEntry() const
    { return !seqInFlight && hasUnsentSeq(); }

    L3TagRam &l3TagRam() { return slcTags; }
    const L3TagRam &l3TagRam() const { return slcTags; }
    SfTagRam &sfTagRam() { return sfTags; }
    const SfTagRam &sfTagRam() const { return sfTags; }
    L3DataRam &l3DataRam() { return slcData; }
    const L3DataRam &l3DataRam() const { return slcData; }
    L3TagEntry *findSlc(uint64_t addr, bool ns = true);
    const L3TagEntry *findSlc(uint64_t addr, bool ns = true) const;
    SfTagEntry *findSf(uint64_t addr, bool ns = true);
    const SfTagEntry *findSf(uint64_t addr, bool ns = true) const;
    void preloadSlc(uint64_t addr, bool ns, SlcState state, uint32_t rnf,
                    const SlcLineData &data, std::size_t way = 0);
    void preloadSf(uint64_t addr, bool ns, SlcState state, uint32_t rnf,
                   const std::vector<bool> &targets, std::size_t way = 0);
    const Config &config() const { return cfg; }
    int replacementPsel() const { return psel; }
    unsigned replacementBrripCounter() const { return brripCounter; }

    // Register-derived offsets, measured from the accepted H1 edge. Kept
    // visible so test traces report the boundary being compared to RTL.
    static constexpr uint64_t SlcSnapshotOffset = 4;
    static constexpr uint64_t DecisionOffset = 5;
    static constexpr uint64_t SeqOffset = 6;
    static constexpr uint64_t SeqWriteOffset = 7;
    static constexpr uint64_t SeqNoHazardReleaseDelay = 3;
    static constexpr uint64_t LookupOffset = 7;
    static constexpr uint64_t ReturnOffset = 9;
    static constexpr uint64_t DataOffset = 9;
    static constexpr uint64_t HistoryLastOffset = 14;
    static constexpr uint64_t SlcTagCommitOffset = 12;
    static constexpr uint64_t SfTagCommitOffset = 13;
    static constexpr uint64_t FillDataOffset = 16;
    static constexpr uint64_t CommitOffset = 17;
    static constexpr std::size_t MaxAttempts = 16;
    static constexpr std::size_t MaxResults = 64;

  private:
    static constexpr std::size_t NoWay = static_cast<std::size_t>(-1);
    struct Attempt
    {
        SlcSfRequest req;
        uint64_t accepted = 0;
        SlcSfLookup lookup;
        SlcSfReplay replay = SlcSfReplay::None;
        std::size_t slcSet = 0, slcWay = NoWay;
        std::size_t sfSet = 0, sfWay = NoWay;
        std::vector<bool> slcReserved, sfReserved;
        bool writeSlc = false, writeSf = false, writeData = false;
        bool returnData = false, victimData = false;
        bool decided = false, lookupSent = false, returned = false;
        bool seqWritten = false, complete = false;
        uint64_t completionOffset = LookupOffset;
        uint8_t insertionRrpv = 2, replacementAge = 0;
        bool fullSetVictim = false;
        L3TagEntry victimTag;
        unsigned beatsSent = 0;
        L3TagEntry slcNext;
        // A hit copies the RAM vector; an allocating miss constructs it in
        // decide(). Non-writing misses need no temporary RNF allocation.
        SfTagEntry sfNext{0};
        SlcLineData line{};
        std::optional<SlcSfSeqRequest> victim;
        std::optional<std::size_t> seqSlot;
        std::optional<uint64_t> seqHitId;
    };
    struct History
    {
        std::size_t set = 0, way = 0;
        uint64_t token = 0, expires = 0;
    };
    struct SeqEntry
    {
        bool valid = false, sent = false;
        std::optional<uint64_t> sentCycle, releaseCycle;
        SlcSfSeqRequest req;
    };
    VictimConflictQuery victimConflictQuery;
    Config cfg;
    L3TagRam slcTags;
    SfTagRam sfTags;
    L3DataRam slcData;
    uint64_t currentCycle = 0, nextAccept = 0, nextSeqId = 1;
    uint32_t lfsr;
    std::size_t lastSlcVictim = NoWay;
    uint64_t notificationCounter = 0;
    int16_t psel = 0;
    uint8_t brripCounter = 0;
    std::size_t leaderMask = 31;
    std::array<std::optional<Attempt>, MaxAttempts> attempts;
    // Admission is monotonic in time. Keep this order when slots retire or
    // are recycled, instead of reconstructing and sorting it every clock.
    std::array<uint8_t, MaxAttempts> activeOrder{};
    std::size_t activeCount = 0;
    std::deque<SlcSfResult> results;
    std::deque<History> slcHistory, sfHistory;
    std::vector<SeqEntry> seq;
    std::optional<SlcSfSeqRequest> seqInFlight;
    std::optional<SlcSfSeqRequest> seqRequest, previousSeqRequest;
    // CMN scans all 12 physical slots; the four ABF slots stay invalid.
    static constexpr std::size_t PhysicalSeqSlots = 12;
    std::size_t seqReadPointer = 0;
    bool returnPortUsed = false;

    void tick();
    void tickSeq();
    bool seqNeedsClock() const;
    void snapshotSlc(Attempt &a);
    void snapshotReservations(Attempt &a, bool slc);
    void checkReplay(Attempt &a);
    void recordHistory(const Attempt &a);
    void decide(Attempt &a);
    void commitSlcTag(Attempt &a);
    void commitSfTag(Attempt &a);
    void commitDataBeat(Attempt &a, unsigned beat);
    void emit(Attempt &a, SlcSfResultKind kind, unsigned beat = 0);
    void expireHistory();
    void removeHistory(uint64_t token);
    bool historyHit(const std::deque<History> &h,
                    std::size_t set, std::size_t way) const;
    std::size_t chooseSlcWay(const Attempt &a);
    std::size_t chooseSfWay(const Attempt &a, bool allocate);
    uint32_t random(bool advance = true);
    std::optional<std::size_t> matchingSlc(uint64_t addr, bool ns) const;
    std::optional<std::size_t> matchingSf(uint64_t addr, bool ns) const;
    std::vector<bool> targets(const SfTagEntry &tag) const;
    const SlcSfSeqRequest *seqHit(uint64_t addr, bool ns) const;
    uint64_t lineAddr(uint64_t addr) const { return addr & ~uint64_t{63}; }
    uint64_t localLine(uint64_t addr) const;
    uint64_t physicalAddress(uint64_t local_line) const
    { return (local_line * cfg.homeCount + cfg.homeIndex) * 64; }
};

template<class Entry>
unsigned SlcSfModel::Ram<Entry>::rnfIdWidth() const
{
    unsigned bits = 0;
    for (auto n = _width - 1; n; n >>= 1) ++bits;
    return bits;
}

} // namespace gem5::Chi
#endif

#ifndef __MEM_CACHE_CHI_SLC_SF_TYPES_HH__
#define __MEM_CACHE_CHI_SLC_SF_TYPES_HH__

#include <array>
#include <cstdint>
#include <vector>

namespace gem5::Chi
{

enum class SlcMesi : uint8_t { Invalid, Shared, Exclusive, Modified };

struct SlcState
{
    SlcMesi mesi = SlcMesi::Invalid;
    bool unique = false;
    bool valid() const { return mesi != SlcMesi::Invalid; }
    bool dirty() const { return mesi == SlcMesi::Modified; }
};

using SlcLineData = std::array<uint8_t, 64>;

enum class SlcSfOp : uint8_t
{
    Lookup, Fill, FlushSlc, FlushSf, FlushBoth, UpdateSf
};

struct SlcSfRequest
{
    // The token identifies a single pipeline attempt, not a recyclable CHI ID.
    uint64_t token = 0;
    uint32_t srcid = 0;
    uint32_t txnid = 0;
    SlcSfOp op = SlcSfOp::Lookup;
    uint8_t opcode = 0;
    uint64_t addr = 0;
    bool ns = true;
    uint32_t rnf = 0;
    // Allocation permission for the SLC; SF directory tracking is independent.
    bool allocate = true;
    // Protocol no-fill decision controls READ vs EVICT write tag updates.
    bool noFill = false;
    bool cacheable = true;
    bool likelyShared = false;
    bool nonCoherent = false;
    bool snoopMe = false;
    bool victimConflict = false;
    SlcState state{};
    SlcLineData data{};
    std::vector<bool> sfTargets;
    // An internal SF eviction already carries its directory snapshot.
    bool seq = false;
    uint64_t seqId = 0;
};

enum class SlcSfReplay : uint8_t
{
    None, SlcSetWay, SfSetWay, VictimConflict, SeqFull, SeqSlotBusy
};

struct SlcSfLookup
{
    bool slcHit = false;
    bool sfHit = false;
    bool seqHit = false;
    SlcState slcState{};
    SlcState sfState{};
    uint32_t slcRnf = 0;
    uint32_t sfRnf = 0;
    std::vector<bool> snoopTargets;
    uint8_t snoopOpcode = 0;
    bool broadcast = false;
    bool selfCancel = false;
    bool memoryRead = false;
    bool noFill = false;
    bool dataExpected = false;
    bool victim = false;
    uint64_t victimAddr = 0;
    bool victimNs = true;
    SlcState victimState{};
};

enum class SlcSfResultKind : uint8_t
{
    LookupDone, DataBeat, CommitDone, Replay
};

struct SlcSfResult
{
    uint64_t token = 0;
    uint32_t srcid = 0;
    uint32_t txnid = 0;
    uint64_t cycle = 0;
    SlcSfResultKind kind = SlcSfResultKind::LookupDone;
    SlcSfLookup lookup{};
    SlcSfReplay replay = SlcSfReplay::None;
    std::array<uint8_t, 32> data{};
    uint8_t beat = 0;
    bool victim = false;
};

struct SlcSfSeqRequest
{
    uint64_t id = 0;
    // First registered edge at which this snapshot exists in SEQ.
    uint64_t createdCycle = 0;
    // Registered H0 request, visible to the H1 consumer on the next edge.
    uint64_t readyCycle = 0;
    uint64_t addr = 0;
    bool ns = true;
    SlcState state{};
    std::vector<bool> targets;
    uint32_t rnf = 0;
    bool broadcast = false;
};

} // namespace gem5::Chi

#endif

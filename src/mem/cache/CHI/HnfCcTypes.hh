#ifndef __HNF_CC_TYPES_HH__
#define __HNF_CC_TYPES_HH__

#include <cstdint>
#include <optional>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi
{

enum class HnfCcEntryState : uint8_t
{
    Idle,
    Sleep,
    Working,
    WaitSlc,
    WaitSnoop,
    IssueMcRead,
    WaitCompAck,
    WaitWriteData,
    Retire
};

enum class PocqTxnKind : uint8_t
{
    Unknown,
    ReadShared,
    ReadUnique,
    ReadNoSnp,
    ReadOnce,
    CleanInvalid,
    MakeInvalid,
    MakeUnique,
    Evict,
    WriteBackFull,
    WriteCleanFull,
    WriteUnique,
    WriteEvictFull
};

enum class PocqState : uint8_t
{
    Idle,
    SlcLookup,
    WaitSnoop,
    SlcUpdateIssue,
    SlcUpdateWait,
    TxLink,
    WaitCompAck,
    IssueMcRead,
    TxRsp,
    WaitWriteData,
    Sleep
};

enum class PocqEventKind : uint8_t
{
    Admit,
    SlcLookupDone,
    SlcUpdateAccepted,
    SlcUpdateDone,
    SnoopDone,
    TxLinkDone,
    TxRspDone,
    McDataDone,
    CompAck,
    WriteDataDone
};

enum class PocqActionKind : uint8_t
{
    DoSlcLookup,
    QueueSnoops,
    CommitRead,
    CommitMaintenance,
    RemoveSharer,
    UpdateSlcSf,
    QueueTxReq,
    QueueCompData,
    QueueComp,
    QueueCompDBIDResp,
    WaitCompAck,
    WaitWriteData,
    StoreWriteData,
    FlushSf,
    FlushL3,
    WriteL3FlushSf,
    Retire,
    SleepForReplay
};

struct PocqEvent
{
    PocqEventKind kind = PocqEventKind::Admit;
    PocqTxnKind txn = PocqTxnKind::Unknown;
    bool slcHit = false;
    bool sfHit = false;
    bool replay = false;
    bool needsSnoop = false;
    bool dataAvailable = false;
    bool needsCompAck = false;
};

enum class HnfSlcState : uint8_t
{
    I,
    EU,
    EN,
    SU,
    SN,
    MU,
    MN
};

enum class HnfSfState : uint8_t
{
    I,
    EU,
    EN,
    SU,
    SN
};

struct HnfLinkToCcReq
{
    bool valid = false;
    uint64_t seq = 0;
    uint64_t dueCycle = 0;
    RawReq req{};
    int tokenId = -1;
    uint8_t priority = 0;
    uint8_t resourceClass = 0;
    bool isDynamic = false;
    bool isStatic = false;
    bool isFvb = false;
};

struct HnfCcAdmitResult
{
    bool valid = false;
    uint64_t seq = 0;
    int tokenId = -1;
    bool accepted = false;
    RawReq req{};
};

struct HnfCcRetireInfo
{
    bool valid = false;
    int tokenId = -1;
    uint64_t allocationSeq = 0;
    uint8_t resourceClass = 0;
    uint8_t reqPriority = 0;
    uint32_t srcid = 0;
    uint32_t txnid = 0;
    uint8_t pcrdtype = 0;
};

struct HnfCcAllocationIdentity
{
    uint64_t sequence = 0;
    uint32_t srcid = 0;
    uint32_t txnid = 0;
};

enum class HnfCcIdentityMatch : uint8_t
{
    Active,
    StaleGeneration,
    CorruptOwner
};

inline constexpr HnfCcIdentityMatch
classifyHnfCcIdentity(const HnfCcAllocationIdentity& active,
                      const HnfCcAllocationIdentity& event)
{
    if (active.sequence != event.sequence) {
        return HnfCcIdentityMatch::StaleGeneration;
    }
    if (active.srcid != event.srcid || active.txnid != event.txnid) {
        return HnfCcIdentityMatch::CorruptOwner;
    }
    return HnfCcIdentityMatch::Active;
}

struct HnfCcTxReq
{
    uint32_t entry = 0;
    std::optional<uint64_t> dirtyVictimId;
    RawReq req{};
};

struct HnfCcTxSnp
{
    uint32_t entry = 0;
    uint32_t targetNode = 0;
    RawSnp snp{};
};

struct HnfCcTxDat
{
    uint32_t entry = 0;
    std::optional<uint64_t> dirtyVictimId;
    RawDat dat{};
};

struct HnfCcTxRsp
{
    uint32_t entry = 0;
    RawRsp rsp{};
    std::optional<HnfCcRetireInfo> retire;
    // Delays REQUESTER_DONE until HomeLinkLayer accepts this response.
    bool traceDirtyVictimRequesterDone = false;
};

struct HnfSlcLookupReq
{
    uint32_t entry = 0;
    RawReq req{};
    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint64_t blockAddr = 0;
};

struct HnfSlcLookupResult
{
    bool valid = false;
    uint32_t entry = 0;
    bool slcHit = false;
    bool sfHit = false;
    bool replay = false;
    bool mcreqNonspec = false;
    bool snoopBroadcast = false;
    bool snoopDirected = false;
    uint8_t snoopOpcode = 0;
    uint64_t snoopTargets = 0;
    uint32_t rnfid = 0;
    uint64_t rnfvec = 0;
    HnfSlcState slcState = HnfSlcState::I;
    HnfSfState sfState = HnfSfState::I;
    bool dataDirty = false;
    std::vector<uint8_t> data;
};

} // namespace gem5::Chi

#endif // __HNF_CC_TYPES_HH__

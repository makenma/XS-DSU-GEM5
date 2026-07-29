#ifndef __HNF_SLCSF_REQUEST_HH__
#define __HNF_SLCSF_REQUEST_HH__

#include <cstdint>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#include "mem/cache/CHI/HnfCcTypes.hh"

namespace gem5::Chi
{

/** A service-local request identity, deliberately distinct from LinkLayer seq. */
struct SlcSfReqId
{
    uint64_t value = 0;

    bool valid() const { return value != 0; }
};

/** A pointer-free snapshot of one array lookup. */
struct SlcSfArraySnapshot
{
    bool hit = false;
    uint32_t set = 0;
    uint32_t way = 0;
    uint64_t generation = 0;
};

/** Versioned lookup facts owned by every request that may mutate storage. */
struct SlcSfCommitToken
{
    SlcSfReqId lookupReqId{};
    uint64_t lineAddress = 0;
    uint64_t lookupEpoch = 0;
    SlcSfArraySnapshot slc{};
    SlcSfArraySnapshot sf{};
};

inline bool
operator==(SlcSfReqId lhs, SlcSfReqId rhs)
{
    return lhs.value == rhs.value;
}

inline bool
operator!=(SlcSfReqId lhs, SlcSfReqId rhs)
{
    return !(lhs == rhs);
}

/**
 * Monotonically allocates request identities for one SLCSF service instance.
 *
 * The allocator is intentionally neither copyable nor movable: cloning it
 * would allow two owners to issue the same identity.  Zero remains the
 * invalid/default identity and IDs are never wrapped or reused.
 */
class SlcSfReqIdAllocator
{
  public:
    SlcSfReqIdAllocator() = default;
    SlcSfReqIdAllocator(const SlcSfReqIdAllocator&) = delete;
    SlcSfReqIdAllocator& operator=(const SlcSfReqIdAllocator&) = delete;
    SlcSfReqIdAllocator(SlcSfReqIdAllocator&&) = delete;
    SlcSfReqIdAllocator& operator=(SlcSfReqIdAllocator&&) = delete;

    SlcSfReqId allocate()
    {
        if (nextId == std::numeric_limits<uint64_t>::max()) {
            throw std::overflow_error("SLCSF request ID space exhausted");
        }
        return SlcSfReqId{nextId++};
    }

  private:
    uint64_t nextId = 1;
};

/** Copied tracing metadata which remains valid after the caller returns. */
struct SlcSfTraceContext
{
    uint64_t linkSequence = 0;
    uint64_t originTick = 0;
    uint32_t transactionId = 0;
    bool traceTag = false;
};

/** Fields common to every delayed SLCSF operation. */
struct SlcSfReqHeader
{
    SlcSfReqId reqId{};
    uint32_t pocEntryId = 0;
    uint64_t lineAddress = 0;
    uint32_t requester = 0;
    uint8_t opcode = 0;
    uint8_t qos = 0;
    SlcSfTraceContext trace{};
};

inline SlcSfReqHeader
makeSlcSfReqHeader(SlcSfReqIdAllocator& ids, uint32_t poc_entry_id, uint64_t line_address, const RawReq& req,
                   uint64_t link_sequence = 0, uint64_t origin_tick = 0)
{
    return SlcSfReqHeader{ids.allocate(),
                          poc_entry_id,
                          line_address,
                          req.srcid,
                          req.opcode,
                          req.qos,
                          SlcSfTraceContext{link_sequence, origin_tick, req.txnid, req.traceTag}};
}

/** Every storage mutation represented by a Fill, Update, or Evict request. */
enum class SlcSfUpdateKind : uint8_t
{
    CommitRead,
    FillCleanShared,
    CompleteMaintenance,
    RemoveSharer,
    WriteLine,
    FlushSf,
    FlushL3,
    WriteL3FlushSf,
    CompleteSfEvict,
    ReleaseDirtyVictim
};

/** Owned cache-line bytes and byte mask. */
struct SlcSfCacheLine
{
    std::vector<uint8_t> data;
    std::vector<uint8_t> byteMask;
    bool dirty = false;
};

/** Stable identities for the two independently managed victim stores. */
struct SlcSfSeqId
{
    uint64_t value = 0;
};

struct SlcSfVictimId
{
    uint64_t value = 0;
};

/** Owned snapshot of a dirty line displaced from the SLC. */
struct SlcSfSlcVictim
{
    SlcSfVictimId victimId{};
    uint64_t lineAddress = 0;
    HnfSlcState state = HnfSlcState::I;
    uint32_t owner = 0;
    SlcSfCacheLine line;
};

/** Owned snapshot of a directory line displaced from the SF into SEQ. */
struct SlcSfSfVictim
{
    SlcSfSeqId seqId{};
    uint64_t lineAddress = 0;
    uint32_t homeNodeId = 0;
    HnfSfState state = HnfSfState::I;
    uint32_t owner = 0;
    uint64_t sharers = 0;
};

using SlcSfVictim = std::variant<SlcSfSlcVictim, SlcSfSfVictim>;

/** Owned data returned by snooping a victim. */
struct SlcSfSnoopData
{
    std::vector<uint8_t> data;
    std::vector<uint8_t> byteMask;
    bool dirty = false;
};

struct SlcSfLookupReq
{
    SlcSfReqHeader header{};
    PocqTxnKind txn = PocqTxnKind::Unknown;
};

struct SlcSfCommitRead
{
    static constexpr SlcSfUpdateKind Kind = SlcSfUpdateKind::CommitRead;

    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint32_t homeNodeId = 0;
    SlcSfCacheLine line;
};

struct SlcSfFillCleanShared
{
    static constexpr SlcSfUpdateKind Kind =
        SlcSfUpdateKind::FillCleanShared;

    SlcSfCacheLine line;
};

struct SlcSfWriteLine
{
    static constexpr SlcSfUpdateKind Kind = SlcSfUpdateKind::WriteLine;

    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint32_t homeNodeId = 0;
    SlcSfCacheLine line;
};

struct SlcSfWriteL3FlushSf
{
    static constexpr SlcSfUpdateKind Kind =
        SlcSfUpdateKind::WriteL3FlushSf;

    SlcSfCacheLine line;
};

using SlcSfFillOperation =
    std::variant<SlcSfCommitRead, SlcSfFillCleanShared, SlcSfWriteLine,
                 SlcSfWriteL3FlushSf>;

struct SlcSfFillReq
{
    SlcSfReqHeader header{};
    SlcSfFillOperation operation{};
    SlcSfCommitToken token{};

    SlcSfUpdateKind kind() const;
};

struct SlcSfCompleteMaintenance
{
    static constexpr SlcSfUpdateKind Kind =
        SlcSfUpdateKind::CompleteMaintenance;

    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint32_t homeNodeId = 0;
};

struct SlcSfRemoveSharer
{
    static constexpr SlcSfUpdateKind Kind = SlcSfUpdateKind::RemoveSharer;
};

struct SlcSfCompleteSfEvict
{
    static constexpr SlcSfUpdateKind Kind =
        SlcSfUpdateKind::CompleteSfEvict;

    SlcSfSeqId seqId{};
    SlcSfSnoopData snoopData;
};

struct SlcSfReleaseDirtyVictim
{
    static constexpr SlcSfUpdateKind Kind =
        SlcSfUpdateKind::ReleaseDirtyVictim;

    SlcSfVictimId victimId{};
};

using SlcSfUpdateOperation =
    std::variant<SlcSfCompleteMaintenance, SlcSfRemoveSharer,
                 SlcSfCompleteSfEvict, SlcSfReleaseDirtyVictim>;

struct SlcSfUpdateReq
{
    SlcSfReqHeader header{};
    SlcSfUpdateOperation operation{};
    SlcSfCommitToken token{};

    SlcSfUpdateKind kind() const;
};

struct SlcSfFlushSf
{
    static constexpr SlcSfUpdateKind Kind = SlcSfUpdateKind::FlushSf;
};

struct SlcSfFlushL3
{
    static constexpr SlcSfUpdateKind Kind = SlcSfUpdateKind::FlushL3;
};

using SlcSfEvictOperation = std::variant<SlcSfFlushSf, SlcSfFlushL3>;

struct SlcSfEvictReq
{
    SlcSfReqHeader header{};
    SlcSfEvictOperation operation{};
    SlcSfCommitToken token{};

    SlcSfUpdateKind kind() const;
};

using SlcSfRequest = std::variant<SlcSfLookupReq, SlcSfFillReq, SlcSfUpdateReq, SlcSfEvictReq>;

template <class Operation>
constexpr SlcSfUpdateKind
slcSfOperationKind(const Operation&)
{
    return std::decay_t<Operation>::Kind;
}

inline SlcSfUpdateKind
SlcSfFillReq::kind() const
{
    return std::visit(
        [](const auto& op) { return slcSfOperationKind(op); }, operation);
}

inline SlcSfUpdateKind
SlcSfUpdateReq::kind() const
{
    return std::visit(
        [](const auto& op) { return slcSfOperationKind(op); }, operation);
}

inline SlcSfUpdateKind
SlcSfEvictReq::kind() const
{
    return std::visit(
        [](const auto& op) { return slcSfOperationKind(op); }, operation);
}

inline SlcSfLookupReq
makeSlcSfLookupReq(SlcSfReqHeader header, PocqTxnKind txn)
{
    return SlcSfLookupReq{std::move(header), txn};
}

inline SlcSfFillReq
makeSlcSfCommitReadReq(SlcSfReqHeader header, PocqTxnKind txn,
                       std::vector<uint8_t> data, bool dirty,
                       uint32_t home_node_id = 0,
                       std::vector<uint8_t> byte_mask = {},
                       SlcSfCommitToken token = {})
{
    SlcSfCacheLine line{std::move(data), std::move(byte_mask), dirty};
    return SlcSfFillReq{
        std::move(header),
        SlcSfCommitRead{txn, home_node_id, std::move(line)}, token};
}

inline SlcSfFillReq
makeSlcSfFillCleanSharedReq(SlcSfReqHeader header,
                            std::vector<uint8_t> data,
                            std::vector<uint8_t> byte_mask = {},
                            SlcSfCommitToken token = {})
{
    SlcSfCacheLine line{std::move(data), std::move(byte_mask), false};
    return SlcSfFillReq{
        std::move(header), SlcSfFillCleanShared{std::move(line)}, token};
}

inline SlcSfUpdateReq
makeSlcSfCompleteMaintenanceReq(SlcSfReqHeader header, PocqTxnKind txn,
                                uint32_t home_node_id = 0,
                                SlcSfCommitToken token = {})
{
    return SlcSfUpdateReq{
        std::move(header), SlcSfCompleteMaintenance{txn, home_node_id},
        token};
}

inline SlcSfUpdateReq
makeSlcSfRemoveSharerReq(SlcSfReqHeader header,
                         SlcSfCommitToken token = {})
{
    return SlcSfUpdateReq{
        std::move(header), SlcSfRemoveSharer{}, token};
}

inline SlcSfFillReq
makeSlcSfWriteLineReq(SlcSfReqHeader header, std::vector<uint8_t> data,
                      PocqTxnKind txn = PocqTxnKind::WriteUnique,
                      uint32_t home_node_id = 0,
                      std::vector<uint8_t> byte_mask = {},
                      SlcSfCommitToken token = {})
{
    const bool dirty = txn != PocqTxnKind::WriteCleanFull;
    SlcSfCacheLine line{std::move(data), std::move(byte_mask), dirty};
    return SlcSfFillReq{
        std::move(header),
        SlcSfWriteLine{txn, home_node_id, std::move(line)}, token};
}

inline SlcSfEvictReq
makeSlcSfFlushSfReq(SlcSfReqHeader header, SlcSfCommitToken token = {})
{
    return SlcSfEvictReq{std::move(header), SlcSfFlushSf{}, token};
}

inline SlcSfEvictReq
makeSlcSfFlushL3Req(SlcSfReqHeader header, SlcSfCommitToken token = {})
{
    return SlcSfEvictReq{std::move(header), SlcSfFlushL3{}, token};
}

inline SlcSfFillReq
makeSlcSfWriteL3FlushSfReq(SlcSfReqHeader header,
                           std::vector<uint8_t> data,
                           std::vector<uint8_t> byte_mask = {},
                           SlcSfCommitToken token = {})
{
    SlcSfCacheLine line{std::move(data), std::move(byte_mask), true};
    return SlcSfFillReq{
        std::move(header), SlcSfWriteL3FlushSf{std::move(line)}, token};
}

inline SlcSfUpdateReq
makeSlcSfCompleteSfEvictReq(SlcSfReqHeader header, SlcSfSeqId seq_id,
                            std::vector<uint8_t> data, bool dirty,
                            std::vector<uint8_t> byte_mask = {},
                            SlcSfCommitToken token = {})
{
    SlcSfSnoopData snoop{
        std::move(data), std::move(byte_mask), dirty};
    return SlcSfUpdateReq{
        std::move(header), SlcSfCompleteSfEvict{seq_id, std::move(snoop)},
        token};
}

inline SlcSfUpdateReq
makeSlcSfReleaseDirtyVictimReq(SlcSfReqHeader header,
                               SlcSfVictimId victim_id,
                               SlcSfCommitToken token = {})
{
    return SlcSfUpdateReq{
        std::move(header), SlcSfReleaseDirtyVictim{victim_id}, token};
}

}  // namespace gem5::Chi

#endif  // __HNF_SLCSF_REQUEST_HH__

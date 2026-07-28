#ifndef __HNF_SLCSF_REQUEST_HH__
#define __HNF_SLCSF_REQUEST_HH__

#include <cstdint>
#include <limits>
#include <optional>
#include <stdexcept>
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

/** Owned snapshot of a selected victim. */
struct SlcSfVictim
{
    uint64_t victimId = 0;
    uint64_t lineAddress = 0;
    uint32_t homeNodeId = 0;
    HnfSfState state = HnfSfState::I;
    uint32_t owner = 0;
    uint64_t sharers = 0;
    SlcSfCacheLine line;
};

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

struct SlcSfFillReq
{
    SlcSfReqHeader header{};
    SlcSfUpdateKind kind = SlcSfUpdateKind::CommitRead;
    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint32_t homeNodeId = 0;
    SlcSfCacheLine line;
};

struct SlcSfUpdateReq
{
    SlcSfReqHeader header{};
    SlcSfUpdateKind kind = SlcSfUpdateKind::CompleteMaintenance;
    PocqTxnKind txn = PocqTxnKind::Unknown;
    uint32_t homeNodeId = 0;
    SlcSfCacheLine line;
    std::optional<SlcSfVictim> victim;
    std::optional<SlcSfSnoopData> snoopData;
};

struct SlcSfEvictReq
{
    SlcSfReqHeader header{};
    SlcSfUpdateKind kind = SlcSfUpdateKind::FlushSf;
    std::optional<SlcSfVictim> victim;
    std::optional<SlcSfSnoopData> snoopData;
};

using SlcSfRequest = std::variant<SlcSfLookupReq, SlcSfFillReq, SlcSfUpdateReq, SlcSfEvictReq>;

}  // namespace gem5::Chi

#endif  // __HNF_SLCSF_REQUEST_HH__

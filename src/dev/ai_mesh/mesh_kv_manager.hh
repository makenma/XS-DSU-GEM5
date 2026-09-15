#ifndef DEV_AI_MESH_MESH_KV_MANAGER_HH
#define DEV_AI_MESH_MESH_KV_MANAGER_HH

#include <array>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

inline constexpr uint32_t kKvNoSlot = 0xFFFFFFFF;
inline constexpr uint32_t kKvNoIntent = 0xFF;
inline constexpr uint32_t kKvNoPolicy = 0xFF;
inline constexpr uint32_t kKvNoRollback = 0;
inline constexpr uint32_t kKvSchemaVersion = 2;
inline constexpr uint32_t kKvRequireReuse =
    agent_abi::kSqFlagsREQUIRE_KV_REUSE;
inline constexpr uint32_t kKvAllowReprefill =
    agent_abi::kSqFlagsALLOW_REPREFILL;
inline constexpr uint32_t kKvFlagMask = kKvRequireReuse | kKvAllowReprefill;

inline bool
derivedInitial(uint32_t flags)
{
    return (flags & kKvFlagMask) == 0;
}

inline bool
legalFlags(uint32_t flags)
{
    return (flags & ~kKvFlagMask) == 0 && flags != kKvFlagMask;
}

namespace kv_detail
{

inline bool
checkedAdd(const char *name, uint64_t left, uint64_t right, uint64_t limit,
           uint64_t &out, std::string &reason)
{
    if (right > limit - left) {
        reason = std::string(name) + " overflow";
        return false;
    }
    out = left + right;
    return true;
}

inline bool
digestEqual(const std::array<uint8_t, 32> &left,
            const std::array<uint8_t, 32> &right)
{
    return std::memcmp(left.data(), right.data(), 32) == 0;
}

inline bool
checkedMul(const char *name, uint64_t left, uint64_t right, uint64_t &out,
           std::string &reason)
{
    if (left != 0 && right > UINT64_MAX / left) {
        reason = std::string(name) + " overflow";
        return false;
    }
    out = left * right;
    return true;
}

}

enum class KvState : uint32_t
{
    Allocating = 0,
    Resident = 1,
    Evicting = 2,
    Evicted = 3,
    Error = 4,
};

enum class KvPath : uint32_t
{
    InitialPrefill = 0,
    KvReuse = 1,
    Reprefill = 2,
};

inline bool
stateClosed(KvState state)
{
    return state == KvState::Allocating || state == KvState::Resident ||
        state == KvState::Evicting || state == KvState::Evicted ||
        state == KvState::Error;
}

inline bool
pathClosed(KvPath path)
{
    return path == KvPath::InitialPrefill || path == KvPath::KvReuse ||
        path == KvPath::Reprefill;
}

enum class KvSnapshotSource : uint32_t
{
    Pin = 0,
    AdmissionClaim = 1,
};

enum class KvTerminalStatus : uint32_t
{
    Success = 0,
    Cancelled = 1,
    Error = 2,
};

enum class KvReleaseOutcome : uint32_t
{
    Success = 0,
    NotFound = 1,
    StaleGeneration = 2,
    Busy = 3,
};

enum class KvAdmissionOutcome : uint32_t
{
    Waiting = 0,
    Claimed = 1,
    Backpressure = 2,
    SessionExists = 3,
    SessionNotFound = 4,
    StaleGeneration = 5,
    ContractMismatch = 6,
    KvState = 7,
    FlagCombination = 8,
};

enum class KvPromotionOutcome : uint32_t
{
    Pinned = 0,
    WaitingSlot = 1,
    ReuseRequired = 2,
    TokenMismatch = 3,
    KvState = 4,
};

enum class KvRollbackKind : uint32_t
{
    InitialErrorRecord = 0,
    RestorePrior = 1,
};

enum class KvPinPhase : uint32_t
{
    Prestart = 0,
    Started = 1,
};

enum class KvWaitReason : uint32_t
{
    RecordTableFull = 0,
    TupleOwnerActive = 1,
    ReleasePending = 2,
    RecordBusy = 3,
};

enum class KvPolicyError : uint32_t
{
    ReuseRequired = 0,
    TokenMismatch = 1,
    KvState = 2,
    None = 0xFF,
};

struct KvGeometry
{
    uint64_t region_base = 0;
    uint64_t slot_bytes = 0;
    uint64_t slot_alignment = 0;
    uint32_t max_sessions = 0;
    uint32_t bytes_per_token = 0;

    bool valid(std::string &reason) const;
    uint32_t tokensPerSlot() const
    { return static_cast<uint32_t>(slot_bytes / bytes_per_token); }
    uint64_t slotBase(uint32_t slot_id) const
    { return region_base + static_cast<uint64_t>(slot_id) * slot_bytes; }
};

struct KvCapacity
{
    uint32_t record_entries = 0;
    uint32_t tombstone_entries = 0;
    uint32_t admission_wait_entries = 0;
    uint32_t release_waiter_entries = 0;

    bool valid(std::string &reason) const;
};

struct KvErrorCandidate
{
    uint64_t tick = 0;
    mesh_abi::ErrorSourceKey source;
    uint32_t code = 0;
};

struct KvRecord
{
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    KvState state = KvState::Evicted;
    std::optional<uint32_t> slot_id;
    uint32_t cached_tokens = 0;
    uint64_t valid_bytes = 0;
    std::optional<std::array<uint8_t, 32>> content_digest;
    uint64_t view_epoch = 0;
    uint64_t last_use_epoch = 0;
    uint32_t pin_count = 0;
    uint32_t admission_claim_count = 0;
    uint32_t outstanding_kv_dma = 0;
    std::optional<KvErrorCandidate> first_error;
    uint32_t diagnostic_prefix_tokens = 0;
    uint64_t diagnostic_prefix_bytes = 0;
    std::optional<std::array<uint8_t, 32>> diagnostic_prefix_digest;

    std::pair<uint64_t, uint64_t> tupleKey() const
    { return {session_id, kv_handle}; }

    std::vector<std::string> gaps(const KvGeometry &geometry,
                                  bool live = true) const;
};

struct KvAdmissionIntent
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    uint64_t deadline_or_max = 0;
    uint32_t qos = 0;
    uint64_t ready_tick = 0;
    uint32_t flags = 0;
    uint32_t required_cached_tokens = 0;
    uint32_t required_tokens_after_round = 0;
};

struct KvAdmissionWaiter
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    uint64_t deadline_or_max = 0;
    uint32_t qos = 0;
    uint64_t ready_tick = 0;
    uint32_t flags = 0;
    uint32_t required_cached_tokens = 0;
    uint32_t required_tokens_after_round = 0;
    bool initial = false;
    bool claimed = false;
    std::optional<KvPath> intent;
    KvPolicyError policy_error = KvPolicyError::None;
    bool prior_absent = false;
    KvRecord prior;

    std::pair<uint64_t, uint64_t> tupleKey() const
    { return {session_id, kv_handle}; }
};

struct KvRollbackPayload
{
    uint64_t serial = 0;
    KvPath intent = KvPath::InitialPrefill;
    bool prior_absent = false;
    KvRecord prior;
};

struct KvRollbackToken
{
    uint64_t request_id = 0;
    uint64_t serial = 0;
};

struct KvRequestPin
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    uint32_t flags = 0;
    uint32_t required_cached_tokens = 0;
    uint32_t required_tokens_after_round = 0;
    KvPinPhase phase = KvPinPhase::Prestart;
    std::optional<KvRollbackPayload> payload;

    std::pair<uint64_t, uint64_t> tupleKey() const
    { return {session_id, kv_handle}; }
};

struct KvReleaseIntent
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
};

struct KvReleaseWaiter
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;

    std::pair<uint64_t, uint64_t> tupleKey() const
    { return {session_id, kv_handle}; }
};

struct KvAppendObligation
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    uint32_t base_tokens = 0;
    uint32_t append_tokens = 0;
    uint64_t base_byte = 0;
    uint64_t bytes = 0;

    std::pair<uint64_t, uint64_t> tupleKey() const
    { return {session_id, kv_handle}; }
};

struct KvTerminalSnapshot
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    KvState state = KvState::Evicted;
    uint32_t cached_tokens = 0;
    uint64_t valid_bytes = 0;
    std::optional<std::array<uint8_t, 32>> content_digest;
    KvTerminalStatus status = KvTerminalStatus::Success;
    KvSnapshotSource source = KvSnapshotSource::Pin;
};

struct KvRuntimeView
{
    uint64_t request_id = 0;
    uint32_t member_ordinal = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    std::array<uint8_t, 32> contract_digest = {};
    uint64_t view_epoch = 0;
    uint32_t slot_id = 0;
    uint64_t slot_base = 0;
    uint64_t slot_bytes = 0;
    uint64_t valid_bytes_at_arm = 0;
};

struct KvEvictingEntry
{
    KvState prior_state = KvState::Resident;
    uint32_t generation = 0;
    uint32_t cached_tokens = 0;
    uint64_t valid_bytes = 0;
    std::optional<std::array<uint8_t, 32>> content_digest;
};

struct KvEvictionEvent
{
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    KvState prior_state = KvState::Resident;
    uint32_t diagnostic_prefix_tokens = 0;
    uint64_t diagnostic_prefix_bytes = 0;
};

struct KvOwnerTerminal
{
    uint64_t request_id = 0;
    KvTerminalStatus status = KvTerminalStatus::Success;
    std::optional<KvRollbackToken> token;
    bool strict = true;
};

struct KvLaterFault
{
    uint64_t request_id = 0;
    KvErrorCandidate candidate;
};

struct KvAppendArm
{
    uint64_t request_id = 0;
    uint32_t base_tokens = 0;
    uint32_t append_tokens = 0;
};

struct KvAppendTerminal
{
    uint64_t request_id = 0;
    std::vector<bool> tokens_ok;
    std::optional<std::array<uint8_t, 32>> content_digest;
};

struct KvDmaCount
{
    uint64_t request_id = 0;
    uint32_t count = 0;
};

struct KvFaultEvent
{
    uint64_t request_id = 0;
    KvErrorCandidate candidate;
};

struct KvViewArm
{
    uint64_t request_id = 0;
    uint32_t member_ordinal = 0;
};

struct KvEdgeInputs
{
    uint64_t tick = 0;
    std::vector<KvAdmissionIntent> admissions;
    std::vector<KvReleaseIntent> releases;
    std::vector<KvOwnerTerminal> owner_terminals;
    std::vector<KvLaterFault> later_faults;
    std::vector<uint64_t> core_starts;
    std::vector<KvAppendArm> append_arms;
    std::vector<KvAppendTerminal> append_terminals;
    std::vector<KvDmaCount> dma_accepts;
    std::vector<KvDmaCount> dma_terminals;
    std::vector<KvFaultEvent> faults;
    std::vector<KvViewArm> view_arms;
};

struct KvEdgeResult
{
    uint64_t commit_tick = 0;
    std::map<uint64_t, KvAdmissionOutcome> admissions;
    std::map<uint64_t, KvPromotionOutcome> promotions;
    std::map<uint64_t, KvWaitReason> waits;
    std::map<uint64_t, KvReleaseOutcome> releases;
    std::vector<KvEvictionEvent> evictions_started;
    std::vector<KvEvictionEvent> evictions_completed;
    std::map<uint64_t, KvRollbackKind> rollbacks;
    std::map<uint64_t, KvTerminalSnapshot> terminals;
    std::map<uint64_t, KvRollbackToken> handoffs;
    std::map<std::pair<uint64_t, uint32_t>, KvRuntimeView> views;
    std::vector<uint64_t> ownerless_cancels;
    std::optional<std::string> fatal;
};

struct KvPersistentState
{
    std::vector<std::optional<std::pair<uint64_t, uint64_t>>> slot_owners;
    std::vector<KvRecord> records;
    std::vector<std::pair<std::pair<uint64_t, uint64_t>, uint32_t>> tombstones;
    uint64_t next_kv_use_epoch = 0;
};

struct KvLiveState
{
    std::vector<KvRecord> records;
    std::vector<std::optional<std::pair<uint64_t, uint64_t>>> slot_owners;
    std::vector<KvAdmissionWaiter> waiters;
    std::vector<KvRequestPin> pins;
    std::vector<KvReleaseWaiter> release_waiters;
    std::vector<KvAppendObligation> appends;
    std::map<std::pair<uint64_t, uint64_t>, KvEvictingEntry> evicting;
    std::vector<std::pair<std::pair<uint64_t, uint64_t>, uint32_t>> tombstones;
    uint64_t next_kv_use_epoch = 0;
    uint64_t next_rollback_serial = 0;
};

class MeshKvManager
{
  public:
    MeshKvManager(const KvGeometry &geometry, const KvCapacity &capacity);

    KvEdgeResult commitEdge(const KvEdgeInputs &edge);

    const KvRecord *findRecord(uint64_t session_id, uint64_t kv_handle) const;
    size_t recordCount() const { return records.size(); }
    size_t tombstoneCount() const { return tombstones.size(); }
    bool releasePending(uint64_t session_id, uint64_t kv_handle) const;
    bool hasPin(uint64_t request_id) const;
    bool hasClaim(uint64_t request_id) const;
    bool hasReleaseWaiter(uint64_t request_id) const;
    bool liveAppend(uint64_t session_id, uint64_t kv_handle) const;
    uint64_t nextKvUseEpoch() const { return next_kv_use_epoch; }
    uint64_t nextRollbackSerial() const { return next_rollback_serial; }

    std::vector<std::string> validate() const;
    std::vector<uint64_t> stateScalars() const;
    std::vector<uint64_t> decisionScalars(const KvEdgeResult &result) const;
    std::vector<uint64_t> edgeScalars(const KvEdgeResult &result) const;

    KvPersistentState savePersistent() const;
    bool loadPersistent(const KvPersistentState &state, std::string &reason);
    KvLiveState saveLive() const;
    bool loadLive(const KvLiveState &state, std::string &reason);

    const std::optional<std::string> &fatalReason() const
    { return fatal_; }

    static std::array<uint8_t, 32> contractDigest(
        const std::array<uint8_t, 32> &program_semantic_digest,
        const std::array<uint8_t, 32> &model_weight_image_digest,
        const std::array<uint8_t, 32> &kv_layout_digest,
        uint32_t bytes_per_token);

  private:
    KvRecord *find(uint64_t session_id, uint64_t kv_handle);
    const KvRecord *find(uint64_t session_id, uint64_t kv_handle) const;
    KvRecord *find(const std::pair<uint64_t, uint64_t> &key);
    const KvRecord *find(const std::pair<uint64_t, uint64_t> &key) const;
    KvAdmissionWaiter *waiter(uint64_t request_id);
    KvRequestPin *pin(uint64_t request_id);
    KvRecord *pinnedRecord(uint64_t request_id);

    void commitIdentity(const KvEdgeInputs &edge,
                        const std::vector<uint64_t> &entry_pins);
    void commitFacts(const KvEdgeInputs &edge, KvEdgeResult &result);
    void armAppend(const KvAppendArm &arm);
    void commitAppend(const KvAppendTerminal &terminal);
    void promoteAllocating(const KvEdgeInputs &edge);
    void latchReleases(const KvEdgeInputs &edge, KvEdgeResult &result);
    void commitTerminals(const KvEdgeInputs &edge, KvEdgeResult &result);
    void terminalizeOwner(const KvOwnerTerminal &terminal,
                          KvEdgeResult &result);
    void applyRollback(const KvOwnerTerminal &terminal,
                       KvEdgeResult &result);
    void applyLaterFault(const KvLaterFault &later, KvEdgeResult &result);
    void terminalizeClaim(KvAdmissionWaiter &entry, KvTerminalStatus status,
                          KvSnapshotSource source, KvEdgeResult &result);
    void releasePin(uint64_t request_id, KvTerminalStatus status,
                    KvSnapshotSource source, KvEdgeResult &result);
    void issueTerminal(const KvRecord &record, uint64_t request_id,
                       KvTerminalStatus status, KvSnapshotSource source,
                       KvEdgeResult &result);
    std::optional<uint32_t> requiredTokens(uint64_t request_id) const;
    void commitReleaseAndEviction(KvEdgeResult &result);
    bool releaseReady(const KvReleaseWaiter &waiter) const;
    void finishRelease(const KvReleaseWaiter &waiter, KvEdgeResult &result);
    void commitAdmissions(const KvEdgeInputs &edge,
                          const std::vector<uint64_t> &cancelled,
                          KvEdgeResult &result);
    void openWaiter(const KvAdmissionIntent &intent, KvEdgeResult &result);
    void commitHead(KvEdgeResult &result);
    void commitCoreStarts(const KvEdgeInputs &edge);
    void openRelease(const KvReleaseIntent &intent, KvEdgeResult &result);
    void pinClaim(KvAdmissionWaiter &entry, bool create_record,
                  const KvRecord &prior, bool prior_absent, KvPath path,
                  KvEdgeResult &result);
    void commitClaim(KvAdmissionWaiter &head, KvRecord *record,
                     const KvRecord &prior, bool prior_absent, KvPath path,
                     KvEdgeResult &result);
    void commitPolicyError(KvAdmissionWaiter &head, const KvRecord &prior,
                           bool prior_absent, KvPolicyError policy_error,
                           KvEdgeResult &result);
    void eraseWaiter(uint64_t request_id);
    uint32_t pinBound() const;
    std::optional<uint32_t> lowestFreeSlot() const;
    void takeSlot(KvRecord &record, uint32_t slot_id, uint64_t view_epoch);
    void freeSlot(KvRecord &record);
    void install(KvRecord record);
    const KvRecord *selectVictim() const;
    void startEviction(KvRecord &victim, KvEdgeResult &result);
    void armViews(const KvEdgeInputs &edge, KvEdgeResult &result);
    std::optional<KvAdmissionOutcome> permanentRejection(
        const KvAdmissionWaiter &entry, const KvRecord *record) const;
    std::optional<KvWaitReason> temporaryBlock(
        const KvAdmissionWaiter &entry, const KvRecord *record) const;
    static KvPolicyError decidePath(uint32_t flags,
                                    uint32_t required_cached_tokens,
                                    const KvRecord &prior,
                                    std::optional<KvPath> &path);
    std::vector<std::string> validateStructure(
        const std::vector<KvRecord> &records,
        const std::vector<std::optional<std::pair<uint64_t, uint64_t>>>
            &slot_owners,
        const std::vector<KvAdmissionWaiter> &waiters,
        const std::vector<KvRequestPin> &pins,
        const std::vector<KvReleaseWaiter> &release_waiters,
        const std::vector<KvAppendObligation> &appends,
        const std::map<std::pair<uint64_t, uint64_t>, KvEvictingEntry>
            &evicting,
        const std::vector<std::pair<std::pair<uint64_t, uint64_t>, uint32_t>>
            &tombstones,
        uint64_t next_epoch, uint64_t next_serial, bool live) const;
    std::vector<std::string> waiterGaps(
        const KvAdmissionWaiter &entry) const;
    std::vector<std::string> pinGaps(const KvRequestPin &pin,
                                     uint64_t next_epoch,
                                     uint64_t next_serial) const;
    std::vector<std::string> priorGaps(const KvRequestPin &owner,
                                       KvPath intent, bool prior_absent,
                                       const KvRecord &prior,
                                       uint64_t next_epoch,
                                       const KvRecord *record) const;
    std::vector<std::string> priorGaps(const KvAdmissionWaiter &owner,
                                       KvPath intent, bool prior_absent,
                                       const KvRecord &prior,
                                       uint64_t next_epoch,
                                       const KvRecord *record) const;
    static void mergeFirstError(KvRecord &record,
                                const KvErrorCandidate &candidate);
    static bool candidateLess(const KvErrorCandidate &left,
                              const KvErrorCandidate &right);
    void appendRecordScalars(std::vector<uint64_t> &out,
                             const KvRecord &record) const;
    bool fail(std::string reason);

    KvGeometry geometry_;
    KvCapacity capacity_;
    std::vector<KvRecord> records;
    std::vector<std::pair<std::pair<uint64_t, uint64_t>, uint32_t>> tombstones;
    std::vector<std::optional<std::pair<uint64_t, uint64_t>>> slot_owners;
    std::vector<KvAdmissionWaiter> waiters;
    std::vector<KvRequestPin> pins;
    std::vector<KvReleaseWaiter> release_waiters;
    std::vector<KvAppendObligation> appends;
    std::map<std::pair<uint64_t, uint64_t>, KvEvictingEntry> evicting;
    uint64_t next_kv_use_epoch = 1;
    uint64_t next_rollback_serial = 1;
    std::optional<std::string> fatal_;
};

}
}

#endif

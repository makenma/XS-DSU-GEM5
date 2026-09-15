#include "dev/ai_mesh/mesh_kv_manager.hh"

#include <algorithm>
#include <cstring>
#include <utility>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

using kv_detail::checkedAdd;
using kv_detail::checkedMul;
using kv_detail::digestEqual;

template <typename T>
bool ascendingRequest(const T &left, const T &right)
{
    return left.request_id < right.request_id;
}

bool viewArmLess(const KvViewArm &left, const KvViewArm &right)
{
    if (left.request_id != right.request_id)
        return left.request_id < right.request_id;
    return left.member_ordinal < right.member_ordinal;
}

KvRecord
emptyInitial(const KvAdmissionWaiter &head)
{
    KvRecord record;
    record.session_id = head.session_id;
    record.kv_handle = head.kv_handle;
    record.generation = head.generation;
    record.contract_digest = head.contract_digest;
    record.state = KvState::Allocating;
    return record;
}

bool queueKeyLess(const KvAdmissionWaiter &left,
                  const KvAdmissionWaiter &right)
{
    if (left.deadline_or_max != right.deadline_or_max)
        return left.deadline_or_max < right.deadline_or_max;
    if ((255 - left.qos) != (255 - right.qos))
        return (255 - left.qos) < (255 - right.qos);
    if (left.ready_tick != right.ready_tick)
        return left.ready_tick < right.ready_tick;
    return left.request_id < right.request_id;
}

bool frozenKeyEqual(const KvAdmissionWaiter &entry,
                    const KvAdmissionIntent &intent)
{
    return entry.session_id == intent.session_id &&
        entry.kv_handle == intent.kv_handle &&
        entry.generation == intent.generation &&
        kv_detail::digestEqual(entry.contract_digest,
                               intent.contract_digest) &&
        entry.flags == intent.flags &&
        entry.required_cached_tokens == intent.required_cached_tokens &&
        entry.required_tokens_after_round ==
        intent.required_tokens_after_round;
}

}

bool
KvGeometry::valid(std::string &reason) const
{
    if (!slot_alignment || !max_sessions || !bytes_per_token || !slot_bytes) {
        reason = "kv geometry fields must be positive";
        return false;
    }
    if (region_base % slot_alignment) {
        reason = "kv_region_base % kv_slot_alignment != 0";
        return false;
    }
    if (slot_bytes % slot_alignment) {
        reason = "kv_session_slot_bytes % kv_slot_alignment != 0";
        return false;
    }
    if (slot_bytes > UINT64_MAX / max_sessions) {
        reason = "kv_region_bytes overflow";
        return false;
    }
    const uint64_t region_bytes = slot_bytes * max_sessions;
    if (region_bytes > UINT64_MAX - region_base) {
        reason = "kv_region_end overflow";
        return false;
    }
    return true;
}

bool
KvCapacity::valid(std::string &reason) const
{
    if (!record_entries || !tombstone_entries || !admission_wait_entries ||
            !release_waiter_entries) {
        reason = "kv capacity entries must be positive";
        return false;
    }
    return true;
}

MeshKvManager::MeshKvManager(const KvGeometry &geometry,
                             const KvCapacity &capacity) :
    geometry_(geometry), capacity_(capacity)
{
    std::string reason;
    if (!geometry_.valid(reason) || !capacity_.valid(reason))
        fatal_ = reason;
    slot_owners.resize(geometry_.max_sessions);
}

std::array<uint8_t, 32>
MeshKvManager::contractDigest(
    const std::array<uint8_t, 32> &program_semantic_digest,
    const std::array<uint8_t, 32> &model_weight_image_digest,
    const std::array<uint8_t, 32> &kv_layout_digest,
    uint32_t bytes_per_token)
{
    const char domain[] = "AI_MESH_KV_CONTRACT_V1";
    std::vector<uint8_t> payload(domain, domain + sizeof(domain));
    payload.insert(payload.end(), program_semantic_digest.begin(),
                   program_semantic_digest.end());
    payload.insert(payload.end(), model_weight_image_digest.begin(),
                   model_weight_image_digest.end());
    payload.insert(payload.end(), kv_layout_digest.begin(),
                   kv_layout_digest.end());
    payload.push_back(bytes_per_token & 0xFF);
    payload.push_back((bytes_per_token >> 8) & 0xFF);
    payload.push_back((bytes_per_token >> 16) & 0xFF);
    payload.push_back((bytes_per_token >> 24) & 0xFF);
    return agentSha256(payload);
}

bool
MeshKvManager::fail(std::string reason)
{
    if (!fatal_)
        fatal_ = std::move(reason);
    return false;
}

KvRecord *
MeshKvManager::find(const std::pair<uint64_t, uint64_t> &key)
{
    for (KvRecord &record : records)
        if (record.tupleKey() == key)
            return &record;
    return nullptr;
}

const KvRecord *
MeshKvManager::find(const std::pair<uint64_t, uint64_t> &key) const
{
    for (const KvRecord &record : records)
        if (record.tupleKey() == key)
            return &record;
    return nullptr;
}

KvRecord *
MeshKvManager::find(uint64_t session_id, uint64_t kv_handle)
{
    return find({session_id, kv_handle});
}

const KvRecord *
MeshKvManager::find(uint64_t session_id, uint64_t kv_handle) const
{
    return find({session_id, kv_handle});
}

const KvRecord *
MeshKvManager::findRecord(uint64_t session_id, uint64_t kv_handle) const
{
    return find(session_id, kv_handle);
}

KvAdmissionWaiter *
MeshKvManager::waiter(uint64_t request_id)
{
    for (KvAdmissionWaiter &entry : waiters)
        if (entry.request_id == request_id)
            return &entry;
    return nullptr;
}

KvRequestPin *
MeshKvManager::pin(uint64_t request_id)
{
    for (KvRequestPin &entry : pins)
        if (entry.request_id == request_id)
            return &entry;
    return nullptr;
}

KvRecord *
MeshKvManager::pinnedRecord(uint64_t request_id)
{
    KvRequestPin *entry = pin(request_id);
    if (entry == nullptr)
        return nullptr;
    return find(entry->session_id, entry->kv_handle);
}

bool
MeshKvManager::releasePending(uint64_t session_id, uint64_t kv_handle) const
{
    for (const KvReleaseWaiter &waiter : release_waiters)
        if (waiter.session_id == session_id && waiter.kv_handle == kv_handle)
            return true;
    return false;
}

bool
MeshKvManager::hasPin(uint64_t request_id) const
{
    for (const KvRequestPin &entry : pins)
        if (entry.request_id == request_id)
            return true;
    return false;
}

bool
MeshKvManager::hasClaim(uint64_t request_id) const
{
    for (const KvAdmissionWaiter &entry : waiters)
        if (entry.request_id == request_id && entry.claimed)
            return true;
    return false;
}

bool
MeshKvManager::hasReleaseWaiter(uint64_t request_id) const
{
    for (const KvReleaseWaiter &entry : release_waiters)
        if (entry.request_id == request_id)
            return true;
    return false;
}

bool
MeshKvManager::liveAppend(uint64_t session_id, uint64_t kv_handle) const
{
    for (const KvAppendObligation &obligation : appends)
        if (obligation.session_id == session_id &&
                obligation.kv_handle == kv_handle)
            return true;
    return false;
}

bool
MeshKvManager::candidateLess(const KvErrorCandidate &left,
                             const KvErrorCandidate &right)
{
    if (left.tick != right.tick)
        return left.tick < right.tick;
    const mesh_abi::ErrorSourceKey &a = left.source;
    const mesh_abi::ErrorSourceKey &b = right.source;
    if (a.error_class != b.error_class)
        return a.error_class < b.error_class;
    if (a.core_id_or_ffff != b.core_id_or_ffff)
        return a.core_id_or_ffff < b.core_id_or_ffff;
    if (a.domain != b.domain)
        return a.domain < b.domain;
    if (a.object_kind != b.object_kind)
        return a.object_kind < b.object_kind;
    if (a.region_group_id != b.region_group_id)
        return a.region_group_id < b.region_group_id;
    if (a.region_id != b.region_id)
        return a.region_id < b.region_id;
    if (a.ordinal != b.ordinal)
        return a.ordinal < b.ordinal;
    if (a.generation != b.generation)
        return a.generation < b.generation;
    for (size_t index = 0; index < a.aux_key.size(); ++index) {
        if (a.aux_key[index] != b.aux_key[index])
            return a.aux_key[index] < b.aux_key[index];
    }
    return left.code < right.code;
}

void
MeshKvManager::mergeFirstError(KvRecord &record,
                               const KvErrorCandidate &candidate)
{
    if (!record.first_error ||
            candidateLess(candidate, *record.first_error)) {
        record.first_error = candidate;
    }
}

KvEdgeResult
MeshKvManager::commitEdge(const KvEdgeInputs &edge)
{
    KvEdgeResult result;
    result.commit_tick = edge.tick;
    if (fatal_) {
        result.fatal = fatal_;
        return result;
    }
    std::vector<uint64_t> entry_pins;
    for (const KvRequestPin &entry : pins)
        entry_pins.push_back(entry.request_id);
    std::vector<uint64_t> cancelled;
    for (const KvOwnerTerminal &terminal : edge.owner_terminals)
        if (!terminal.strict)
            cancelled.push_back(terminal.request_id);

    commitIdentity(edge, entry_pins);
    if (!fatal_)
        commitFacts(edge, result);
    if (!fatal_)
        promoteAllocating(edge);
    if (!fatal_)
        latchReleases(edge, result);
    if (!fatal_)
        commitCoreStarts(edge);
    if (!fatal_)
        commitTerminals(edge, result);
    if (!fatal_)
        commitReleaseAndEviction(result);
    if (!fatal_)
        commitAdmissions(edge, cancelled, result);
    if (fatal_)
        result.fatal = fatal_;
    return result;
}

void
MeshKvManager::commitIdentity(const KvEdgeInputs &edge,
                              const std::vector<uint64_t> &entry_pins)
{
    std::vector<uint64_t> seen;
    for (const KvAdmissionIntent &intent : edge.admissions) {
        if (std::find(seen.begin(), seen.end(), intent.request_id) !=
                seen.end()) {
            fail("duplicate KV command identity in one edge");
            return;
        }
        seen.push_back(intent.request_id);
    }
    for (const KvReleaseIntent &intent : edge.releases) {
        if (std::find(seen.begin(), seen.end(), intent.request_id) !=
                seen.end()) {
            fail("duplicate KV command identity in one edge");
            return;
        }
        seen.push_back(intent.request_id);
    }
    for (const KvAdmissionIntent &intent : edge.admissions) {
        if (std::find(entry_pins.begin(), entry_pins.end(),
                      intent.request_id) != entry_pins.end()) {
            fail("duplicate KV acquire for a pinned request");
            return;
        }
        if (hasReleaseWaiter(intent.request_id)) {
            fail("request id is a live release waiter");
            return;
        }
        KvAdmissionWaiter *entry = waiter(intent.request_id);
        if (entry == nullptr)
            continue;
        if (!frozenKeyEqual(*entry, intent)) {
            fail("waiter retry changed the frozen identity");
            return;
        }
        if (entry->deadline_or_max != intent.deadline_or_max ||
                entry->qos != intent.qos) {
            fail("waiter retry changed the schedule key");
            return;
        }
    }
    for (const KvReleaseIntent &intent : edge.releases) {
        if (std::find(entry_pins.begin(), entry_pins.end(),
                      intent.request_id) != entry_pins.end()) {
            fail("release request id is a live KV pin");
            return;
        }
        if (waiter(intent.request_id) != nullptr) {
            fail("release request id is a live admission waiter");
            return;
        }
        if (hasReleaseWaiter(intent.request_id)) {
            fail("release request id is a live release waiter");
            return;
        }
    }
}

void
MeshKvManager::eraseWaiter(uint64_t request_id)
{
    for (size_t index = 0; index < waiters.size(); ++index) {
        if (waiters[index].request_id == request_id) {
            waiters.erase(waiters.begin() + index);
            break;
        }
    }
}

void
MeshKvManager::commitFacts(const KvEdgeInputs &edge, KvEdgeResult &result)
{
    armViews(edge, result);
    if (fatal_)
        return;

    std::vector<KvAppendArm> arms = edge.append_arms;
    std::sort(arms.begin(), arms.end(), ascendingRequest<KvAppendArm>);
    for (const KvAppendArm &arm : arms) {
        armAppend(arm);
        if (fatal_)
            return;
    }

    std::vector<KvDmaCount> accepts = edge.dma_accepts;
    std::sort(accepts.begin(), accepts.end(), ascendingRequest<KvDmaCount>);
    for (const KvDmaCount &accept : accepts) {
        KvRecord *record = pinnedRecord(accept.request_id);
        if (record == nullptr) {
            fail("KV DMA accept without a KV pin");
            return;
        }
        uint64_t next = 0;
        std::string reason;
        if (!checkedAdd("outstanding_kv_dma", record->outstanding_kv_dma,
                        accept.count, UINT32_MAX, next, reason)) {
            fail(reason);
            return;
        }
        record->outstanding_kv_dma = static_cast<uint32_t>(next);
    }

    std::vector<KvDmaCount> terminals = edge.dma_terminals;
    std::sort(terminals.begin(), terminals.end(), ascendingRequest<KvDmaCount>);
    for (const KvDmaCount &terminal : terminals) {
        KvRecord *record = pinnedRecord(terminal.request_id);
        if (record == nullptr) {
            fail("KV DMA terminal without a KV pin");
            return;
        }
        if (terminal.count > record->outstanding_kv_dma) {
            fail("outstanding_kv_dma underflow");
            return;
        }
        record->outstanding_kv_dma -= terminal.count;
    }

    std::vector<KvAppendTerminal> append_terminals = edge.append_terminals;
    std::sort(append_terminals.begin(), append_terminals.end(),
              ascendingRequest<KvAppendTerminal>);
    for (const KvAppendTerminal &terminal : append_terminals) {
        commitAppend(terminal);
        if (fatal_)
            return;
    }

    std::vector<KvFaultEvent> faults = edge.faults;
    std::sort(faults.begin(), faults.end(),
              [](const KvFaultEvent &left, const KvFaultEvent &right) {
                  if (left.request_id != right.request_id)
                      return left.request_id < right.request_id;
                  return MeshKvManager::candidateLess(left.candidate,
                                                      right.candidate);
              });
    for (const KvFaultEvent &fault : faults) {
        KvRecord *record = pinnedRecord(fault.request_id);
        if (record == nullptr) {
            fail("KV fault without a KV pin");
            return;
        }
        mergeFirstError(*record, fault.candidate);
    }
}

void
MeshKvManager::armAppend(const KvAppendArm &arm)
{
    KvRecord *record = pinnedRecord(arm.request_id);
    if (record == nullptr) {
        fail("KV append arm without a KV pin");
        return;
    }
    for (const KvAppendObligation &obligation : appends) {
        if (obligation.request_id == arm.request_id) {
            fail("duplicate KV append obligation");
            return;
        }
    }
    if (!record->slot_id) {
        fail("KV append arm without a resident slot");
        return;
    }
    if (arm.base_tokens != record->cached_tokens) {
        fail("KV append base must equal cached tokens");
        return;
    }
    if (uint64_t(arm.base_tokens) + arm.append_tokens >
            geometry_.tokensPerSlot()) {
        fail("KV append exceeds kv_tokens_per_slot");
        return;
    }
    KvAppendObligation obligation;
    obligation.request_id = arm.request_id;
    obligation.session_id = record->session_id;
    obligation.kv_handle = record->kv_handle;
    obligation.generation = record->generation;
    obligation.base_tokens = arm.base_tokens;
    obligation.append_tokens = arm.append_tokens;
    std::string reason;
    if (!checkedMul("kv append base byte", arm.base_tokens,
                    geometry_.bytes_per_token, obligation.base_byte,
                    reason) ||
            !checkedMul("kv append bytes", arm.append_tokens,
                        geometry_.bytes_per_token, obligation.bytes,
                        reason)) {
        fail(reason);
        return;
    }
    size_t index = 0;
    while (index < appends.size() &&
            appends[index].request_id < obligation.request_id)
        ++index;
    appends.insert(appends.begin() + index, obligation);
}

void
MeshKvManager::commitAppend(const KvAppendTerminal &terminal)
{
    KvAppendObligation *obligation = nullptr;
    size_t obligation_index = 0;
    for (size_t index = 0; index < appends.size(); ++index) {
        if (appends[index].request_id == terminal.request_id) {
            obligation = &appends[index];
            obligation_index = index;
            break;
        }
    }
    if (obligation == nullptr) {
        fail("KV append terminal without an obligation");
        return;
    }
    KvRecord *record = find(obligation->session_id, obligation->kv_handle);
    if (record == nullptr) {
        fail("KV append terminal without a record");
        return;
    }
    if (terminal.tokens_ok.size() != obligation->append_tokens) {
        fail("KV append bitmap size mismatch");
        return;
    }
    uint32_t prefix = 0;
    for (bool ok : terminal.tokens_ok) {
        if (!ok)
            break;
        ++prefix;
    }
    std::string reason;
    uint64_t cached = 0;
    if (!checkedAdd("cached_tokens", obligation->base_tokens, prefix,
                    geometry_.tokensPerSlot(), cached, reason)) {
        fail(reason);
        return;
    }
    record->cached_tokens = static_cast<uint32_t>(cached);
    uint64_t valid = 0;
    if (!checkedMul("kv valid bytes", record->cached_tokens,
                    geometry_.bytes_per_token, valid, reason)) {
        fail(reason);
        return;
    }
    record->valid_bytes = valid;
    const uint32_t append_tokens = obligation->append_tokens;
    appends.erase(appends.begin() + obligation_index);
    if (prefix == append_tokens) {
        uint64_t epoch = 0;
        if (!checkedAdd("view_epoch", record->view_epoch, 1, UINT64_MAX,
                        epoch, reason)) {
            fail(reason);
            return;
        }
        record->view_epoch = epoch;
        if (terminal.content_digest) {
            record->content_digest = terminal.content_digest;
        }
        return;
    }
    record->diagnostic_prefix_tokens = record->cached_tokens;
    record->diagnostic_prefix_bytes = record->valid_bytes;
    record->diagnostic_prefix_digest = record->content_digest;
    record->content_digest.reset();
    record->state = KvState::Error;
}

void
MeshKvManager::promoteAllocating(const KvEdgeInputs &edge)
{
    std::vector<uint64_t> terminalizing;
    for (const KvOwnerTerminal &terminal : edge.owner_terminals)
        if (terminal.token)
            terminalizing.push_back(terminal.request_id);
    for (const KvLaterFault &later : edge.later_faults)
        terminalizing.push_back(later.request_id);

    for (KvRecord &record : records) {
        if (record.state != KvState::Allocating || !record.slot_id)
            continue;
        if (record.first_error || !record.pin_count ||
                record.admission_claim_count)
            continue;
        const KvRequestPin *owner = nullptr;
        for (const KvRequestPin &entry : pins)
            if (entry.session_id == record.session_id &&
                    entry.kv_handle == record.kv_handle)
                owner = &entry;
        if (owner == nullptr)
            continue;
        bool blocked = false;
        for (uint64_t request_id : terminalizing)
            if (request_id == owner->request_id)
                blocked = true;
        if (blocked)
            continue;
        record.state = KvState::Resident;
    }
}

void
MeshKvManager::latchReleases(const KvEdgeInputs &edge, KvEdgeResult &result)
{
    std::vector<KvReleaseIntent> releases = edge.releases;
    std::sort(releases.begin(), releases.end(),
              ascendingRequest<KvReleaseIntent>);
    for (const KvReleaseIntent &intent : releases) {
        openRelease(intent, result);
        if (fatal_)
            return;
    }
}

void
MeshKvManager::commitCoreStarts(const KvEdgeInputs &edge)
{
    std::vector<uint64_t> starts = edge.core_starts;
    std::sort(starts.begin(), starts.end());
    starts.erase(std::unique(starts.begin(), starts.end()), starts.end());
    for (uint64_t request_id : starts) {
        KvRequestPin *entry = pin(request_id);
        if (entry == nullptr) {
            fail("core start without a KV pin");
            return;
        }
        if (entry->phase != KvPinPhase::Prestart || !entry->payload) {
            fail("core start without rollback authority");
            return;
        }
        entry->phase = KvPinPhase::Started;
        entry->payload.reset();
    }
}

void
MeshKvManager::commitTerminals(const KvEdgeInputs &edge,
                               KvEdgeResult &result)
{
    std::vector<KvLaterFault> later_faults = edge.later_faults;
    std::sort(later_faults.begin(), later_faults.end(),
              ascendingRequest<KvLaterFault>);
    for (size_t index = 1; index < later_faults.size(); ++index) {
        if (later_faults[index].request_id ==
                later_faults[index - 1].request_id) {
            fail("duplicate KV later fault");
            return;
        }
    }
    for (const KvLaterFault &later : later_faults) {
        applyLaterFault(later, result);
        if (fatal_)
            return;
    }
    std::vector<KvOwnerTerminal> terminals = edge.owner_terminals;
    std::sort(terminals.begin(), terminals.end(),
              ascendingRequest<KvOwnerTerminal>);
    for (size_t index = 1; index < terminals.size(); ++index) {
        if (terminals[index].request_id ==
                terminals[index - 1].request_id) {
            fail("duplicate KV owner terminal");
            return;
        }
    }
    for (const KvOwnerTerminal &terminal : terminals) {
        terminalizeOwner(terminal, result);
        if (fatal_)
            return;
    }
}

void
MeshKvManager::terminalizeOwner(const KvOwnerTerminal &terminal,
                                KvEdgeResult &result)
{
    const uint64_t request_id = terminal.request_id;
    if (terminal.token) {
        applyRollback(terminal, result);
        return;
    }
    KvAdmissionWaiter *entry = waiter(request_id);
    if (entry != nullptr && entry->claimed) {
        terminalizeClaim(*entry, terminal.status,
                         KvSnapshotSource::AdmissionClaim, result);
        return;
    }
    if (pin(request_id) != nullptr) {
        releasePin(request_id, terminal.status, KvSnapshotSource::Pin,
                   result);
        return;
    }
    if (entry != nullptr) {
        eraseWaiter(request_id);
    } else if (terminal.strict) {
        fail("KV owner terminal without an owner");
        return;
    }
    result.ownerless_cancels.push_back(request_id);
}

void
MeshKvManager::applyRollback(const KvOwnerTerminal &terminal,
                             KvEdgeResult &result)
{
    const uint64_t request_id = terminal.request_id;
    KvRequestPin *entry = pin(request_id);
    if (entry == nullptr || !entry->payload || !terminal.token ||
            terminal.token->request_id != request_id ||
            entry->payload->serial != terminal.token->serial) {
        fail("rollback authority is no longer valid");
        return;
    }
    const KvRollbackPayload payload = *entry->payload;
    KvRecord *record = find(entry->session_id, entry->kv_handle);
    if (record == nullptr) {
        fail("prestart rollback without a record");
        return;
    }
    freeSlot(*record);
    for (size_t index = 0; index < appends.size(); ++index) {
        if (appends[index].request_id == request_id) {
            appends.erase(appends.begin() + index);
            break;
        }
    }
    if (payload.prior_absent) {
        record->state = KvState::Error;
        record->cached_tokens = 0;
        record->valid_bytes = 0;
        record->content_digest.reset();
        record->view_epoch = 0;
        record->last_use_epoch = 0;
        record->diagnostic_prefix_tokens = 0;
        record->diagnostic_prefix_bytes = 0;
        record->diagnostic_prefix_digest.reset();
        record->pin_count = 0;
        record->admission_claim_count = 0;
        record->outstanding_kv_dma = 0;
        record->first_error.reset();
        result.rollbacks[request_id] = KvRollbackKind::InitialErrorRecord;
    } else {
        KvRecord restored = payload.prior;
        restored.pin_count = 0;
        restored.admission_claim_count = 0;
        restored.outstanding_kv_dma = 0;
        install(restored);
        record = find(restored.session_id, restored.kv_handle);
        result.rollbacks[request_id] = KvRollbackKind::RestorePrior;
    }
    eraseWaiter(request_id);
    for (size_t index = 0; index < pins.size(); ++index) {
        if (pins[index].request_id == request_id) {
            pins.erase(pins.begin() + index);
            break;
        }
    }
    issueTerminal(*record, request_id, terminal.status,
                  KvSnapshotSource::Pin, result);
}

void
MeshKvManager::applyLaterFault(const KvLaterFault &later,
                               KvEdgeResult &result)
{
    KvRequestPin *entry = pin(later.request_id);
    if (entry == nullptr) {
        fail("later fault without a KV pin");
        return;
    }
    KvRecord *record = find(entry->session_id, entry->kv_handle);
    if (record == nullptr) {
        fail("later fault without a record");
        return;
    }
    mergeFirstError(*record, later.candidate);
    if (record->state == KvState::Resident ||
            record->state == KvState::Allocating) {
        record->state = KvState::Error;
    }
    releasePin(later.request_id, KvTerminalStatus::Error,
               KvSnapshotSource::Pin, result);
}

void
MeshKvManager::terminalizeClaim(KvAdmissionWaiter &entry,
                                KvTerminalStatus status,
                                KvSnapshotSource source,
                                KvEdgeResult &result)
{
    KvRecord *record = find(entry.session_id, entry.kv_handle);
    if (record == nullptr) {
        fail("claim terminal without a record");
        return;
    }
    freeSlot(*record);
    if (entry.prior_absent) {
        record->state = KvState::Error;
        record->cached_tokens = 0;
        record->valid_bytes = 0;
        record->content_digest.reset();
        record->view_epoch = 0;
        record->last_use_epoch = 0;
        record->diagnostic_prefix_tokens = 0;
        record->diagnostic_prefix_bytes = 0;
        record->diagnostic_prefix_digest.reset();
        record->pin_count = 0;
        record->admission_claim_count = 0;
        record->outstanding_kv_dma = 0;
        record->first_error.reset();
    } else {
        KvRecord restored = entry.prior;
        restored.pin_count = 0;
        restored.admission_claim_count = 0;
        restored.outstanding_kv_dma = 0;
        install(restored);
        record = find(restored.session_id, restored.kv_handle);
    }
    const uint64_t request_id = entry.request_id;
    eraseWaiter(request_id);
    for (size_t index = 0; index < appends.size(); ++index) {
        if (appends[index].request_id == request_id) {
            appends.erase(appends.begin() + index);
            break;
        }
    }
    issueTerminal(*record, request_id, status, source, result);
}

void
MeshKvManager::releasePin(uint64_t request_id, KvTerminalStatus status,
                          KvSnapshotSource source, KvEdgeResult &result)
{
    KvRequestPin *entry = pin(request_id);
    if (entry == nullptr) {
        fail("pin release without an owner");
        return;
    }
    KvRecord *record = find(entry->session_id, entry->kv_handle);
    if (record == nullptr) {
        fail("pin release without a record");
        return;
    }
    if (record->pin_count != 1) {
        fail("pin release with a mismatched owner set");
        return;
    }
    if (record->outstanding_kv_dma ||
            liveAppend(record->session_id, record->kv_handle)) {
        fail("pin release with live KV work");
        return;
    }
    issueTerminal(*record, request_id, status, source, result);
    if (fatal_)
        return;
    record->pin_count = 0;
    for (size_t index = 0; index < pins.size(); ++index) {
        if (pins[index].request_id == request_id) {
            pins.erase(pins.begin() + index);
            break;
        }
    }
}

std::optional<uint32_t>
MeshKvManager::requiredTokens(uint64_t request_id) const
{
    for (const KvRequestPin &entry : pins)
        if (entry.request_id == request_id)
            return entry.required_tokens_after_round;
    for (const KvAdmissionWaiter &entry : waiters)
        if (entry.request_id == request_id && entry.claimed)
            return entry.required_tokens_after_round;
    return std::nullopt;
}

void
MeshKvManager::issueTerminal(const KvRecord &record, uint64_t request_id,
                             KvTerminalStatus status,
                             KvSnapshotSource source, KvEdgeResult &result)
{
    if (status == KvTerminalStatus::Success) {
        std::optional<uint32_t> required = requiredTokens(request_id);
        if (required) {
            std::string reason;
            uint64_t valid = 0;
            if (!checkedMul("kv valid bytes", *required,
                            geometry_.bytes_per_token, valid, reason)) {
                fail(reason);
                return;
            }
            if (record.cached_tokens != *required ||
                    record.valid_bytes != valid) {
                fail("success terminal must match the required prefix");
                return;
            }
        }
    }
    KvTerminalSnapshot snapshot;
    snapshot.request_id = request_id;
    snapshot.session_id = record.session_id;
    snapshot.kv_handle = record.kv_handle;
    snapshot.generation = record.generation;
    snapshot.contract_digest = record.contract_digest;
    snapshot.state = record.state;
    snapshot.cached_tokens = record.cached_tokens;
    snapshot.valid_bytes = record.valid_bytes;
    snapshot.content_digest = record.content_digest;
    snapshot.status = status;
    snapshot.source = source;
    result.terminals[request_id] = snapshot;
}

void
MeshKvManager::commitReleaseAndEviction(KvEdgeResult &result)
{
    for (const auto &entry : evicting) {
        KvRecord *record = find(entry.first.first, entry.first.second);
        if (record == nullptr || record->state != KvState::Evicting)
            continue;
        const KvState prior = entry.second.prior_state;
        const uint32_t prior_cached = entry.second.cached_tokens;
        const uint64_t prior_valid = entry.second.valid_bytes;
        const std::optional<std::array<uint8_t, 32>> prior_digest =
            entry.second.content_digest;
        freeSlot(*record);
        if (prior == KvState::Error) {
            record->diagnostic_prefix_tokens = prior_cached;
            record->diagnostic_prefix_bytes = prior_valid;
            record->diagnostic_prefix_digest = prior_digest;
            record->state = KvState::Error;
        } else {
            record->state = KvState::Evicted;
        }
        record->cached_tokens = 0;
        record->valid_bytes = 0;
        record->content_digest.reset();
        KvEvictionEvent event;
        event.session_id = record->session_id;
        event.kv_handle = record->kv_handle;
        event.generation = record->generation;
        event.prior_state = prior;
        event.diagnostic_prefix_tokens = record->diagnostic_prefix_tokens;
        event.diagnostic_prefix_bytes = record->diagnostic_prefix_bytes;
        result.evictions_completed.push_back(event);
        evicting.erase(entry.first);
        break;
    }

    std::vector<KvReleaseWaiter> pending = release_waiters;
    std::sort(pending.begin(), pending.end(),
              [](const KvReleaseWaiter &left, const KvReleaseWaiter &right) {
                  if (left.session_id != right.session_id)
                      return left.session_id < right.session_id;
                  if (left.kv_handle != right.kv_handle)
                      return left.kv_handle < right.kv_handle;
                  return left.request_id < right.request_id;
              });
    for (const KvReleaseWaiter &waiter : pending) {
        if (releaseReady(waiter)) {
            finishRelease(waiter, result);
            break;
        }
    }
}

bool
MeshKvManager::releaseReady(const KvReleaseWaiter &waiter) const
{
    const KvRecord *record = find(waiter.session_id, waiter.kv_handle);
    if (record == nullptr)
        return false;
    if (record->state == KvState::Allocating ||
            record->state == KvState::Evicting)
        return false;
    return !(record->admission_claim_count || record->pin_count ||
             record->outstanding_kv_dma ||
             liveAppend(waiter.session_id, waiter.kv_handle));
}

void
MeshKvManager::finishRelease(const KvReleaseWaiter &waiter,
                             KvEdgeResult &result)
{
    KvRecord *record = find(waiter.session_id, waiter.kv_handle);
    if (record == nullptr) {
        fail("release completion without a record");
        return;
    }
    if (tombstones.size() >= capacity_.tombstone_entries) {
        fail("E_CAPACITY_PLAN: session tombstone table is full");
        return;
    }
    uint64_t generation = 0;
    std::string reason;
    if (!checkedAdd("session generation", record->generation, 1, UINT32_MAX,
                    generation, reason)) {
        fail(reason);
        return;
    }
    const auto key = record->tupleKey();
    freeSlot(*record);
    for (size_t index = 0; index < records.size(); ++index) {
        if (records[index].tupleKey() == key) {
            records.erase(records.begin() + index);
            break;
        }
    }
    std::pair<std::pair<uint64_t, uint64_t>, uint32_t> tombstone{
        key, static_cast<uint32_t>(generation)};
    size_t index = 0;
    while (index < tombstones.size() && tombstones[index].first < key)
        ++index;
    tombstones.insert(tombstones.begin() + index, tombstone);
    for (size_t slot = 0; slot < release_waiters.size(); ++slot) {
        if (release_waiters[slot].request_id == waiter.request_id) {
            release_waiters.erase(release_waiters.begin() + slot);
            break;
        }
    }
    result.releases[waiter.request_id] = KvReleaseOutcome::Success;
}

void
MeshKvManager::commitAdmissions(const KvEdgeInputs &edge,
                                const std::vector<uint64_t> &cancelled,
                                KvEdgeResult &result)
{
    std::vector<KvAdmissionIntent> admissions = edge.admissions;
    std::sort(admissions.begin(), admissions.end(),
              [](const KvAdmissionIntent &left,
                 const KvAdmissionIntent &right) {
                  if (left.request_id != right.request_id)
                      return left.request_id < right.request_id;
                  return left.ready_tick < right.ready_tick;
              });
    for (const KvAdmissionIntent &intent : admissions) {
        bool skip = false;
        for (uint64_t request_id : cancelled)
            if (request_id == intent.request_id)
                skip = true;
        if (skip)
            continue;
        openWaiter(intent, result);
        if (fatal_)
            return;
    }
    commitHead(result);
}

void
MeshKvManager::openWaiter(const KvAdmissionIntent &intent,
                          KvEdgeResult &result)
{
    if (!legalFlags(intent.flags)) {
        result.admissions[intent.request_id] =
            KvAdmissionOutcome::FlagCombination;
        return;
    }
    if (intent.generation == 0 || intent.request_id == 0) {
        result.admissions[intent.request_id] = KvAdmissionOutcome::KvState;
        return;
    }
    KvAdmissionWaiter *existing = waiter(intent.request_id);
    if (existing != nullptr) {
        result.admissions[intent.request_id] = existing->claimed
            ? KvAdmissionOutcome::Claimed : KvAdmissionOutcome::Waiting;
        return;
    }
    if (waiters.size() >= capacity_.admission_wait_entries) {
        result.admissions[intent.request_id] =
            KvAdmissionOutcome::Backpressure;
        return;
    }
    KvAdmissionWaiter entry;
    entry.request_id = intent.request_id;
    entry.session_id = intent.session_id;
    entry.kv_handle = intent.kv_handle;
    entry.generation = intent.generation;
    entry.contract_digest = intent.contract_digest;
    entry.deadline_or_max = intent.deadline_or_max;
    entry.qos = intent.qos;
    entry.ready_tick = intent.ready_tick;
    entry.flags = intent.flags;
    entry.required_cached_tokens = intent.required_cached_tokens;
    entry.required_tokens_after_round = intent.required_tokens_after_round;
    entry.initial = derivedInitial(intent.flags);
    size_t index = 0;
    while (index < waiters.size() &&
            waiters[index].request_id < entry.request_id)
        ++index;
    waiters.insert(waiters.begin() + index, entry);
    result.admissions[intent.request_id] = KvAdmissionOutcome::Waiting;
}

std::optional<KvAdmissionOutcome>
MeshKvManager::permanentRejection(const KvAdmissionWaiter &entry,
                                  const KvRecord *record) const
{
    const uint32_t *tombstone = nullptr;
    for (const auto &item : tombstones)
        if (item.first == entry.tupleKey())
            tombstone = &item.second;
    if (derivedInitial(entry.flags)) {
        if (record != nullptr || tombstone != nullptr)
            return KvAdmissionOutcome::SessionExists;
        return std::nullopt;
    }
    if (record == nullptr) {
        const bool stale = tombstone != nullptr &&
            entry.generation < *tombstone;
        return stale ? KvAdmissionOutcome::StaleGeneration
                     : KvAdmissionOutcome::SessionNotFound;
    }
    if (record->generation != entry.generation)
        return KvAdmissionOutcome::StaleGeneration;
    if (!kv_detail::digestEqual(record->contract_digest,
                                entry.contract_digest))
        return KvAdmissionOutcome::ContractMismatch;
    if (record->state == KvState::Error)
        return KvAdmissionOutcome::KvState;
    return std::nullopt;
}

std::optional<KvWaitReason>
MeshKvManager::temporaryBlock(const KvAdmissionWaiter &entry,
                              const KvRecord *record) const
{
    if (derivedInitial(entry.flags)) {
        if (records.size() >= capacity_.record_entries)
            return KvWaitReason::RecordTableFull;
        return std::nullopt;
    }
    if (releasePending(entry.session_id, entry.kv_handle))
        return KvWaitReason::ReleasePending;
    if (record->pin_count || record->admission_claim_count)
        return KvWaitReason::TupleOwnerActive;
    if (record->state == KvState::Allocating ||
            record->state == KvState::Evicting)
        return KvWaitReason::RecordBusy;
    return std::nullopt;
}

void
MeshKvManager::commitHead(KvEdgeResult &result)
{
    KvAdmissionWaiter *head = nullptr;
    for (KvAdmissionWaiter &entry : waiters) {
        if (head == nullptr || queueKeyLess(entry, *head))
            head = &entry;
    }
    if (head == nullptr)
        return;
    KvRecord *record = find(head->session_id, head->kv_handle);
    if (head->claimed) {
        if (record == nullptr) {
            fail("admission head lost its record");
            return;
        }
        if (!head->intent) {
            fail("claimed waiter lost its path");
            return;
        }
        pinClaim(*head, false, head->prior, head->prior_absent, *head->intent,
                 result);
        return;
    }
    const std::optional<KvAdmissionOutcome> rejection =
        permanentRejection(*head, record);
    if (rejection) {
        const uint64_t request_id = head->request_id;
        eraseWaiter(request_id);
        result.admissions[request_id] = *rejection;
        return;
    }
    const std::optional<KvWaitReason> block = temporaryBlock(*head, record);
    if (block) {
        result.waits[head->request_id] = *block;
        return;
    }
    KvRecord prior;
    bool prior_absent = false;
    KvPath path = KvPath::InitialPrefill;
    KvPolicyError policy_error = KvPolicyError::None;
    if (derivedInitial(head->flags)) {
        prior = emptyInitial(*head);
        prior_absent = true;
    } else {
        prior = *record;
        std::optional<KvPath> decided;
        policy_error = decidePath(head->flags, head->required_cached_tokens,
                                  *record, decided);
        path = decided.value_or(KvPath::InitialPrefill);
    }
    if (policy_error != KvPolicyError::None) {
        commitPolicyError(*head, prior, prior_absent, policy_error, result);
        return;
    }
    if (path != KvPath::KvReuse && !lowestFreeSlot()) {
        commitClaim(*head, record, prior, prior_absent, path, result);
        if (!fatal_) {
            const KvRecord *victim = selectVictim();
            if (victim != nullptr) {
                KvRecord *mutable_victim = find(victim->session_id,
                                                victim->kv_handle);
                startEviction(*mutable_victim, result);
            }
            result.promotions[head->request_id] =
                KvPromotionOutcome::WaitingSlot;
        }
        return;
    }
    pinClaim(*head, derivedInitial(head->flags), prior, prior_absent,
             path, result);
}

void
MeshKvManager::commitClaim(KvAdmissionWaiter &head, KvRecord *record,
                           const KvRecord &prior, bool prior_absent,
                           KvPath path, KvEdgeResult &result)
{
    if (derivedInitial(head.flags)) {
        KvRecord claimed = prior;
        claimed.admission_claim_count = 1;
        install(claimed);
    } else {
        if (record == nullptr) {
            fail("admission claim without a record");
            return;
        }
        std::string reason;
        uint64_t next = 0;
        if (!checkedAdd("admission_claim_count",
                        record->admission_claim_count, 1, UINT32_MAX, next,
                        reason)) {
            fail(reason);
            return;
        }
        record->admission_claim_count = static_cast<uint32_t>(next);
    }
    head.claimed = true;
    head.prior = prior;
    head.prior_absent = prior_absent;
    head.intent = path;
    head.policy_error = KvPolicyError::None;
    result.admissions[head.request_id] = KvAdmissionOutcome::Claimed;
}

void
MeshKvManager::commitPolicyError(KvAdmissionWaiter &head,
                                 const KvRecord &prior, bool prior_absent,
                                 KvPolicyError policy_error,
                                 KvEdgeResult &result)
{
    if (prior_absent) {
        KvRecord created = prior;
        created.state = KvState::Error;
        created.cached_tokens = 0;
        created.valid_bytes = 0;
        created.content_digest.reset();
        created.view_epoch = 0;
        created.last_use_epoch = 0;
        created.diagnostic_prefix_tokens = 0;
        created.diagnostic_prefix_bytes = 0;
        created.diagnostic_prefix_digest.reset();
        created.pin_count = 0;
        created.admission_claim_count = 0;
        created.outstanding_kv_dma = 0;
        created.first_error.reset();
        install(created);
    }
    KvRecord *record = find(head.session_id, head.kv_handle);
    if (record == nullptr) {
        fail("admission policy error without a record");
        return;
    }
    const uint64_t request_id = head.request_id;
    result.admissions[request_id] = KvAdmissionOutcome::Claimed;
    eraseWaiter(request_id);
    issueTerminal(*record, request_id, KvTerminalStatus::Error,
                  KvSnapshotSource::AdmissionClaim, result);
    if (fatal_)
        return;
    switch (policy_error) {
      case KvPolicyError::ReuseRequired:
        result.promotions[request_id] = KvPromotionOutcome::ReuseRequired;
        break;
      case KvPolicyError::TokenMismatch:
        result.promotions[request_id] = KvPromotionOutcome::TokenMismatch;
        break;
      default:
        result.promotions[request_id] = KvPromotionOutcome::KvState;
        break;
    }
}

KvPolicyError
MeshKvManager::decidePath(uint32_t flags, uint32_t required_cached_tokens,
                          const KvRecord &prior,
                          std::optional<KvPath> &path)
{
    const bool require_reuse = (flags & kKvRequireReuse) != 0;
    const bool allow_reprefill = (flags & kKvAllowReprefill) != 0;
    const bool matched = prior.cached_tokens == required_cached_tokens;
    path.reset();
    if (prior.state == KvState::Resident && matched) {
        path = KvPath::KvReuse;
        return KvPolicyError::None;
    }
    if (require_reuse) {
        if (prior.state != KvState::Resident)
            return KvPolicyError::ReuseRequired;
        return KvPolicyError::TokenMismatch;
    }
    if (!allow_reprefill)
        return KvPolicyError::KvState;
    if (prior.state == KvState::Evicted) {
        path = KvPath::Reprefill;
        return KvPolicyError::None;
    }
    if (prior.state == KvState::Resident)
        return KvPolicyError::TokenMismatch;
    return KvPolicyError::KvState;
}

void
MeshKvManager::openRelease(const KvReleaseIntent &intent,
                           KvEdgeResult &result)
{
    if (releasePending(intent.session_id, intent.kv_handle)) {
        result.releases[intent.request_id] = KvReleaseOutcome::Busy;
        return;
    }
    KvRecord *record = find(intent.session_id, intent.kv_handle);
    if (record == nullptr) {
        const auto key = std::make_pair(intent.session_id, intent.kv_handle);
        const uint32_t *tombstone = nullptr;
        for (const auto &entry : tombstones)
            if (entry.first == key)
                tombstone = &entry.second;
        const bool stale = tombstone != nullptr &&
            intent.generation < *tombstone;
        result.releases[intent.request_id] = stale
            ? KvReleaseOutcome::StaleGeneration
            : KvReleaseOutcome::NotFound;
        return;
    }
    if (record->generation != intent.generation) {
        result.releases[intent.request_id] =
            KvReleaseOutcome::StaleGeneration;
        return;
    }
    if (release_waiters.size() >= capacity_.release_waiter_entries) {
        result.releases[intent.request_id] = KvReleaseOutcome::Busy;
        return;
    }
    KvReleaseWaiter waiter;
    waiter.request_id = intent.request_id;
    waiter.session_id = intent.session_id;
    waiter.kv_handle = intent.kv_handle;
    waiter.generation = intent.generation;
    size_t index = 0;
    while (index < release_waiters.size() &&
            release_waiters[index].request_id < waiter.request_id)
        ++index;
    release_waiters.insert(release_waiters.begin() + index, waiter);
}

void
MeshKvManager::pinClaim(KvAdmissionWaiter &entry, bool create_record,
                        const KvRecord &prior, bool prior_absent, KvPath path,
                        KvEdgeResult &result)
{
    std::optional<uint32_t> slot_id;
    uint64_t view_epoch = 0;
    std::string reason;
    if (path != KvPath::KvReuse) {
        slot_id = lowestFreeSlot();
        if (!slot_id) {
            const KvRecord *victim = selectVictim();
            if (victim != nullptr) {
                KvRecord *mutable_victim = find(victim->session_id,
                                                victim->kv_handle);
                startEviction(*mutable_victim, result);
            }
            result.promotions[entry.request_id] =
                KvPromotionOutcome::WaitingSlot;
            return;
        }
        if (prior_absent) {
            view_epoch = 1;
        } else if (!checkedAdd("view_epoch", prior.view_epoch, 1,
                               UINT64_MAX, view_epoch, reason)) {
            fail(reason);
            return;
        }
    }
    if (pins.size() >= pinBound()) {
        fail("E_CAPACITY_PLAN: KV pin table is full");
        return;
    }
    uint64_t epoch = 0;
    uint64_t serial = 0;
    if (!checkedAdd("next_kv_use_epoch", next_kv_use_epoch, 1, UINT64_MAX,
                    epoch, reason) ||
            !checkedAdd("next_rollback_serial", next_rollback_serial, 1,
                        UINT64_MAX, serial, reason)) {
        fail(reason);
        return;
    }
    KvRequestPin pin_entry;
    pin_entry.request_id = entry.request_id;
    pin_entry.session_id = entry.session_id;
    pin_entry.kv_handle = entry.kv_handle;
    pin_entry.generation = entry.generation;
    pin_entry.contract_digest = entry.contract_digest;
    pin_entry.flags = entry.flags;
    pin_entry.required_cached_tokens = entry.required_cached_tokens;
    pin_entry.required_tokens_after_round = entry.required_tokens_after_round;
    pin_entry.phase = KvPinPhase::Prestart;
    KvRollbackPayload payload;
    payload.serial = next_rollback_serial;
    payload.intent = path;
    payload.prior_absent = prior_absent;
    payload.prior = prior;
    pin_entry.payload = payload;

    if (create_record) {
        KvRecord created = prior;
        created.admission_claim_count = 0;
        install(created);
    }
    KvRecord *record = find(entry.session_id, entry.kv_handle);
    if (record == nullptr) {
        fail("claim->pin without a record");
        return;
    }
    if (slot_id)
        takeSlot(*record, *slot_id, view_epoch);
    record->last_use_epoch = next_kv_use_epoch;
    record->admission_claim_count = 0;
    record->pin_count = 1;
    next_kv_use_epoch = epoch;
    next_rollback_serial = serial;
    const uint64_t request_id = entry.request_id;
    eraseWaiter(request_id);
    size_t index = 0;
    while (index < pins.size() && pins[index].request_id < request_id)
        ++index;
    pins.insert(pins.begin() + index, pin_entry);
    KvRollbackToken token;
    token.request_id = request_id;
    token.serial = payload.serial;
    result.admissions[request_id] = KvAdmissionOutcome::Claimed;
    result.handoffs[request_id] = token;
    result.promotions[request_id] = KvPromotionOutcome::Pinned;
}

uint32_t
MeshKvManager::pinBound() const
{
    return std::min(capacity_.record_entries, geometry_.max_sessions);
}

std::optional<uint32_t>
MeshKvManager::lowestFreeSlot() const
{
    for (size_t slot = 0; slot < slot_owners.size(); ++slot)
        if (!slot_owners[slot])
            return static_cast<uint32_t>(slot);
    return std::nullopt;
}

void
MeshKvManager::takeSlot(KvRecord &record, uint32_t slot_id,
                        uint64_t view_epoch)
{
    record.slot_id = slot_id;
    record.view_epoch = view_epoch;
    record.state = KvState::Allocating;
    slot_owners[slot_id] = record.tupleKey();
}

void
MeshKvManager::freeSlot(KvRecord &record)
{
    if (!record.slot_id)
        return;
    if (slot_owners[*record.slot_id] &&
            *slot_owners[*record.slot_id] == record.tupleKey()) {
        slot_owners[*record.slot_id].reset();
    }
    record.slot_id.reset();
}

void
MeshKvManager::install(KvRecord record)
{
    for (KvRecord &entry : records) {
        if (entry.tupleKey() == record.tupleKey()) {
            entry = record;
            if (record.slot_id)
                slot_owners[*record.slot_id] = record.tupleKey();
            return;
        }
    }
    size_t index = 0;
    while (index < records.size() &&
            records[index].tupleKey() < record.tupleKey())
        ++index;
    records.insert(records.begin() + index, record);
    if (record.slot_id)
        slot_owners[*record.slot_id] = record.tupleKey();
}

const KvRecord *
MeshKvManager::selectVictim() const
{
    const KvRecord *victim = nullptr;
    for (const KvRecord &record : records) {
        if (!(record.state == KvState::Resident ||
                record.state == KvState::Error))
            continue;
        if (!record.slot_id || record.pin_count ||
                record.admission_claim_count ||
                record.outstanding_kv_dma ||
                liveAppend(record.session_id, record.kv_handle) ||
                releasePending(record.session_id, record.kv_handle))
            continue;
        if (victim == nullptr) {
            victim = &record;
            continue;
        }
        const uint32_t left_priority =
            record.state == KvState::Error ? 0 : 1;
        const uint32_t right_priority =
            victim->state == KvState::Error ? 0 : 1;
        bool earlier = false;
        if (left_priority != right_priority) {
            earlier = left_priority < right_priority;
        } else if (record.last_use_epoch != victim->last_use_epoch) {
            earlier = record.last_use_epoch < victim->last_use_epoch;
        } else if (record.session_id != victim->session_id) {
            earlier = record.session_id < victim->session_id;
        } else if (record.kv_handle != victim->kv_handle) {
            earlier = record.kv_handle < victim->kv_handle;
        } else {
            earlier = record.generation < victim->generation;
        }
        if (earlier)
            victim = &record;
    }
    return victim;
}

void
MeshKvManager::startEviction(KvRecord &victim, KvEdgeResult &result)
{
    KvEvictingEntry entry;
    entry.prior_state = victim.state;
    entry.generation = victim.generation;
    entry.cached_tokens = victim.cached_tokens;
    entry.valid_bytes = victim.valid_bytes;
    entry.content_digest = victim.content_digest;
    evicting[victim.tupleKey()] = entry;
    victim.state = KvState::Evicting;
    KvEvictionEvent event;
    event.session_id = victim.session_id;
    event.kv_handle = victim.kv_handle;
    event.generation = victim.generation;
    event.prior_state = entry.prior_state;
    result.evictions_started.push_back(event);
}

void
MeshKvManager::armViews(const KvEdgeInputs &edge, KvEdgeResult &result)
{
    std::vector<KvViewArm> arms = edge.view_arms;
    std::sort(arms.begin(), arms.end(), viewArmLess);
    for (const KvViewArm &arm : arms) {
        KvRequestPin *entry = pin(arm.request_id);
        if (entry == nullptr) {
            fail("runtime view without a KV pin");
            return;
        }
        KvRecord *record = find(entry->session_id, entry->kv_handle);
        if (record == nullptr || !record->slot_id) {
            fail("runtime view without a resident slot");
            return;
        }
        KvRuntimeView view;
        view.request_id = arm.request_id;
        view.member_ordinal = arm.member_ordinal;
        view.session_id = record->session_id;
        view.kv_handle = record->kv_handle;
        view.generation = record->generation;
        view.contract_digest = record->contract_digest;
        view.view_epoch = record->view_epoch;
        view.slot_id = *record->slot_id;
        view.slot_base = geometry_.slotBase(*record->slot_id);
        view.slot_bytes = geometry_.slot_bytes;
        view.valid_bytes_at_arm = record->valid_bytes;
        result.views[{arm.request_id, arm.member_ordinal}] = view;
    }
}

}
}

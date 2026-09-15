#include "dev/ai_mesh/mesh_kv_manager.hh"

#include <algorithm>
#include <cstring>
#include <utility>

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr size_t kKvRecordScalars = 37;


using kv_detail::checkedAdd;
using kv_detail::checkedMul;
using kv_detail::digestEqual;
using ai_mesh::pathClosed;
using ai_mesh::stateClosed;

void appendDigest(std::vector<uint64_t> &out,
                  const std::array<uint8_t, 32> &digest)
{
    for (size_t word = 0; word < 4; ++word) {
        uint64_t value = 0;
        for (size_t byte = 0; byte < 8; ++byte) {
            value |= uint64_t(digest[word * 8 + byte]) << (8 * byte);
        }
        out.push_back(value);
    }
}

void appendDigest(std::vector<uint64_t> &out,
                  const std::optional<std::array<uint8_t, 32>> &digest)
{
    if (!digest) {
        out.insert(out.end(), 4, 0);
        return;
    }
    appendDigest(out, *digest);
}

bool duplicateIds(const std::vector<uint64_t> &ids)
{
    std::vector<uint64_t> seen;
    for (uint64_t value : ids) {
        if (std::find(seen.begin(), seen.end(), value) != seen.end())
            return true;
        seen.push_back(value);
    }
    return false;
}

bool abiEnumValid(uint32_t value, uint32_t mask)
{
    return value < 32 && ((mask >> value) & 1u) != 0;
}

bool emptyRecord(const KvRecord &record)
{
    return record.session_id == 0 && record.kv_handle == 0 &&
        record.generation == 0 && !record.slot_id &&
        record.cached_tokens == 0 && record.valid_bytes == 0 &&
        !record.content_digest && record.view_epoch == 0 &&
        record.last_use_epoch == 0 && record.pin_count == 0 &&
        record.admission_claim_count == 0 && record.outstanding_kv_dma == 0;
}

bool duplicateTuple(const std::vector<std::pair<uint64_t, uint64_t>> &keys)
{
    std::vector<std::pair<uint64_t, uint64_t>> seen;
    for (const auto &key : keys) {
        for (const auto &entry : seen)
            if (entry == key)
                return true;
        seen.push_back(key);
    }
    return false;
}

void appendSource(std::vector<uint64_t> &out,
                  const mesh_abi::ErrorSourceKey &source)
{
    uint8_t wire[mesh_abi::kErrorSourceKeysBytes];
    mesh_abi::writeErrorSourceKey(wire, source);
    for (size_t word = 0; word < 5; ++word) {
        uint64_t value = 0;
        for (size_t byte = 0; byte < 8; ++byte) {
            value |= uint64_t(wire[word * 8 + byte]) << (8 * byte);
        }
        out.push_back(value);
    }
}

}



std::vector<std::string>
KvRecord::gaps(const KvGeometry &geometry, bool live) const
{
    std::vector<std::string> problems;
    std::string reason;
    if (session_id == 0 || kv_handle == 0)
        problems.push_back("record tuple must be nonzero");
    if (generation == 0)
        problems.push_back("record generation must be a nonzero u32");
    if (!stateClosed(state))
        problems.push_back("record state is outside the closed set");
    if (slot_id && *slot_id >= geometry.max_sessions)
        problems.push_back("slot id outside the slot bitmap");
    if (problems.empty()) {
        if (state == KvState::Resident && !slot_id)
            problems.push_back("RESIDENT requires a slot");
        if (state == KvState::Evicting) {
            if (!slot_id)
                problems.push_back("EVICTING requires a slot");
            if (pin_count || admission_claim_count || outstanding_kv_dma)
                problems.push_back("EVICTING must have no live owner");
        }
        if (state == KvState::Evicted &&
                (slot_id || cached_tokens || valid_bytes || content_digest))
            problems.push_back("EVICTED must hold no residency");
        if (!slot_id && (cached_tokens || valid_bytes))
            problems.push_back("slotless record must hold no residency");
        if (cached_tokens > geometry.tokensPerSlot())
            problems.push_back("cached tokens exceed kv_tokens_per_slot");
        uint64_t valid = 0;
        std::string mul_reason;
        if (!checkedMul("kv valid bytes", cached_tokens,
                        geometry.bytes_per_token, valid, mul_reason) ||
                valid_bytes != valid)
            problems.push_back(
                "valid_bytes != cached_tokens * bytes_per_token");
        if (state == KvState::Allocating && cached_tokens)
            problems.push_back("ALLOCATING must not hold cached tokens");
        if (live && state == KvState::Allocating && !pin_count &&
                !admission_claim_count)
            problems.push_back("ALLOCATING must hold a KV owner");
        if (!slot_id && view_epoch && state != KvState::Evicted &&
                state != KvState::Error)
            problems.push_back("slotless record must keep view epoch 0");
        if (content_digest && cached_tokens == 0)
            problems.push_back("content digest requires a committed prefix");
        uint64_t diagnostic = 0;
        if (!checkedMul("kv diagnostic bytes", diagnostic_prefix_tokens,
                        geometry.bytes_per_token, diagnostic, mul_reason) ||
                diagnostic_prefix_bytes != diagnostic)
            problems.push_back("diagnostic prefix bytes mismatch");
        if (diagnostic_prefix_digest && diagnostic_prefix_tokens == 0)
            problems.push_back(
                "diagnostic digest requires a diagnostic prefix");
        if (state != KvState::Error &&
                (diagnostic_prefix_tokens || diagnostic_prefix_digest))
            problems.push_back("diagnostic prefix only belongs to ERROR");
        if (first_error &&
                (!abiEnumValid(first_error->source.error_class,
                               mesh_abi::kMoeErrorClassValuesMask) ||
                 !abiEnumValid(first_error->source.domain,
                               mesh_abi::kMeshObjectDomainValuesMask) ||
                 !abiEnumValid(first_error->source.object_kind,
                               mesh_abi::kMeshObjectKindValuesMask)))
            problems.push_back(
                "first error source is outside the closed enums");
    }
    return problems;
}

std::vector<std::string>
MeshKvManager::waiterGaps(const KvAdmissionWaiter &entry) const
{
    std::vector<std::string> problems;
    if (entry.request_id == 0 || entry.session_id == 0 ||
            entry.kv_handle == 0)
        problems.push_back("waiter identity must be a nonzero u64");
    if (entry.generation == 0)
        problems.push_back("waiter generation must be a nonzero u32");
    if (!legalFlags(entry.flags))
        problems.push_back("waiter flags must be a legal combination");
    if (entry.qos > 255)
        problems.push_back("waiter qos must be a u8");
    if (entry.policy_error != KvPolicyError::None)
        problems.push_back("waiter policy error is outside the closed set");
    if (!problems.empty())
        return problems;
    if (entry.initial != derivedInitial(entry.flags))
        problems.push_back("waiter kind must match the frozen flags");
    if (entry.claimed) {
        if (!entry.intent || !pathClosed(*entry.intent)) {
            problems.push_back("claimed waiter must carry a path");
        } else if (derivedInitial(entry.flags) !=
                   (*entry.intent == KvPath::InitialPrefill)) {
            problems.push_back("claimed path must match the waiter kind");
        }
        if (derivedInitial(entry.flags) != entry.prior_absent)
            problems.push_back("absent prior only belongs to INITIAL");
    } else if (entry.intent || entry.prior_absent ||
               !emptyRecord(entry.prior)) {
        problems.push_back("waiting waiter must not carry a prior");
    }
    return problems;
}

std::vector<std::string>
MeshKvManager::validateStructure(
    const std::vector<KvRecord> &records_in,
    const std::vector<std::optional<std::pair<uint64_t, uint64_t>>>
        &slot_owners_in,
    const std::vector<KvAdmissionWaiter> &waiters_in,
    const std::vector<KvRequestPin> &pins_in,
    const std::vector<KvReleaseWaiter> &release_waiters_in,
    const std::vector<KvAppendObligation> &appends_in,
    const std::map<std::pair<uint64_t, uint64_t>, KvEvictingEntry>
        &evicting_in,
    const std::vector<std::pair<std::pair<uint64_t, uint64_t>, uint32_t>>
        &tombstones_in,
    uint64_t next_epoch, uint64_t next_serial, bool live) const
{
    std::vector<std::string> problems;
    std::string reason;
    if (slot_owners_in.size() != geometry_.max_sessions) {
        problems.push_back("slot bitmap length must equal kv_max_sessions");
        return problems;
    }
    for (const auto &owner : slot_owners_in) {
        if (!owner)
            continue;
        if (owner->first == 0 || owner->second == 0) {
            problems.push_back(
                "slot bitmap entry must be empty or a valid tuple");
            return problems;
        }
    }
    if (live && records_in.size() > capacity_.record_entries)
        problems.push_back("record table exceeds its capacity");
    for (const KvRecord &record : records_in)
        for (const std::string &gap : record.gaps(geometry_, live))
            problems.push_back(gap);
    const uint32_t bounds[4] = {capacity_.admission_wait_entries,
                                pinBound(),
                                capacity_.release_waiter_entries,
                                capacity_.tombstone_entries};
    const size_t sizes[4] = {waiters_in.size(), pins_in.size(),
                             release_waiters_in.size(),
                             tombstones_in.size()};
    const char *names[4] = {"admission waiter", "KV pin", "release waiter",
                            "tombstone"};
    for (size_t index = 0; index < 4; ++index)
        if ((live || index == 3) && sizes[index] > bounds[index])
            problems.push_back(std::string(names[index]) +
                               " table exceeds its capacity");
    for (const KvAdmissionWaiter &entry : waiters_in)
        for (const std::string &gap : waiterGaps(entry))
            problems.push_back(gap);
    std::vector<uint64_t> serials;
    for (const KvRequestPin &entry : pins_in) {
        for (const std::string &gap : pinGaps(entry, next_epoch,
                                              next_serial))
            problems.push_back(gap);
        if (entry.phase == KvPinPhase::Prestart && entry.payload) {
            if (std::find(serials.begin(), serials.end(),
                          entry.payload->serial) != serials.end())
                problems.push_back("duplicate rollback serial");
            serials.push_back(entry.payload->serial);
        }
    }
    for (const KvReleaseWaiter &entry : release_waiters_in) {
        if (entry.request_id == 0 || entry.session_id == 0 ||
                entry.kv_handle == 0 || entry.generation == 0)
            problems.push_back(
                "release waiter identity must be a complete u64");
    }
    for (const KvAppendObligation &entry : appends_in) {
        if (entry.request_id == 0 || entry.session_id == 0 ||
                entry.kv_handle == 0 || entry.generation == 0) {
            problems.push_back("append identity must be a complete u64");
            break;
        }
        uint64_t bytes = 0;
        uint64_t base = 0;
        std::string mul_reason;
        if (!checkedMul("kv append bytes", entry.append_tokens,
                        geometry_.bytes_per_token, bytes, mul_reason) ||
                !checkedMul("kv append base byte", entry.base_tokens,
                            geometry_.bytes_per_token, base, mul_reason) ||
                entry.bytes != bytes || entry.base_byte != base) {
            problems.push_back("append obligation geometry is invalid");
            break;
        }
    }
    for (const auto &item : evicting_in) {
        if (item.first.first == 0 || item.first.second == 0 ||
                item.second.generation == 0) {
            problems.push_back("eviction entry must be a typed identity");
            break;
        }
        if (!stateClosed(item.second.prior_state)) {
            problems.push_back("eviction prior state is outside the set");
            break;
        }
    }
    for (const auto &item : tombstones_in) {
        if (item.first.first == 0 || item.first.second == 0 ||
                item.second == 0) {
            problems.push_back("tombstone identity must be a complete u64");
            break;
        }
    }
    if (!problems.empty())
        return problems;

    std::vector<std::pair<uint64_t, uint64_t>> record_keys;
    for (const KvRecord &record : records_in)
        record_keys.push_back(record.tupleKey());
    if (duplicateTuple(record_keys))
        problems.push_back("duplicate session record");
    std::vector<uint64_t> waiter_ids;
    std::vector<uint64_t> pin_ids;
    std::vector<uint64_t> release_ids;
    for (const KvAdmissionWaiter &entry : waiters_in)
        waiter_ids.push_back(entry.request_id);
    for (const KvRequestPin &entry : pins_in)
        pin_ids.push_back(entry.request_id);
    for (const KvReleaseWaiter &entry : release_waiters_in)
        release_ids.push_back(entry.request_id);
    if (duplicateIds(waiter_ids))
        problems.push_back("duplicate admission waiter request identity");
    if (duplicateIds(pin_ids))
        problems.push_back("duplicate KV pin request identity");
    std::vector<std::pair<uint64_t, uint64_t>> pin_slots;
    for (const KvRequestPin &entry : pins_in)
        pin_slots.push_back(entry.tupleKey());
    if (duplicateTuple(pin_slots))
        problems.push_back("duplicate KV pin slot owner");
    if (duplicateIds(release_ids))
        problems.push_back("duplicate release waiter request identity");
    std::vector<std::pair<uint64_t, uint64_t>> release_tuples;
    for (const KvReleaseWaiter &entry : release_waiters_in)
        release_tuples.push_back(entry.tupleKey());
    if (duplicateTuple(release_tuples))
        problems.push_back("duplicate release waiter target identity");
    std::vector<uint64_t> append_ids;
    for (const KvAppendObligation &entry : appends_in)
        append_ids.push_back(entry.request_id);
    if (duplicateIds(append_ids))
        problems.push_back("duplicate append obligation identity");
    std::vector<std::pair<uint64_t, uint64_t>> evicting_keys;
    for (const auto &item : evicting_in)
        evicting_keys.push_back(item.first);
    if (duplicateTuple(evicting_keys))
        problems.push_back("duplicate eviction entry identity");
    std::vector<std::pair<uint64_t, uint64_t>> tombstone_keys;
    for (const auto &item : tombstones_in)
        tombstone_keys.push_back(item.first);
    if (duplicateTuple(tombstone_keys))
        problems.push_back("duplicate tombstone identity");
    for (uint64_t request_id : pin_ids)
        if (std::find(waiter_ids.begin(), waiter_ids.end(), request_id) !=
                waiter_ids.end())
            problems.push_back(
                "pin and admission waiter identities must differ");
    for (uint64_t request_id : release_ids)
        if (std::find(pin_ids.begin(), pin_ids.end(), request_id) !=
                    pin_ids.end() ||
                std::find(waiter_ids.begin(), waiter_ids.end(),
                          request_id) != waiter_ids.end())
            problems.push_back("release waiter identity is not unique");
    for (const auto &key : tombstone_keys)
        if (std::find(record_keys.begin(), record_keys.end(), key) !=
                record_keys.end())
            problems.push_back(
                "record and tombstone tuple must be disjoint");
    if (next_epoch == 0)
        problems.push_back("next_kv_use_epoch must be nonzero");
    if (next_serial == 0)
        problems.push_back("next_rollback_serial must be nonzero");
    return problems;
}

std::vector<std::string>
MeshKvManager::pinGaps(const KvRequestPin &pin, uint64_t next_epoch,
                        uint64_t next_serial) const
{
    std::vector<std::string> problems;
    if (pin.request_id == 0 || pin.session_id == 0 || pin.kv_handle == 0)
        problems.push_back("KV pin identity must be a nonzero u64");
    if (pin.generation == 0)
        problems.push_back("KV pin generation must be a nonzero u32");
    if (!legalFlags(pin.flags))
        problems.push_back("KV pin flags must be a legal combination");
    if (pin.phase != KvPinPhase::Prestart &&
            pin.phase != KvPinPhase::Started) {
        problems.push_back("KV pin phase is outside the closed set");
        return problems;
    }
    if (!problems.empty())
        return problems;
    if (pin.phase == KvPinPhase::Started) {
        if (pin.payload)
            problems.push_back(
                "STARTED pin must not carry rollback payload");
        return problems;
    }
    if (!pin.payload) {
        problems.push_back("PRESTART pin must carry its rollback payload");
        return problems;
    }
    if (pin.payload->serial == 0 || pin.payload->serial >= next_serial)
        problems.push_back("rollback payload serial is not issued");
    if (!pathClosed(pin.payload->intent))
        problems.push_back("rollback payload path is outside the set");
    if (derivedInitial(pin.flags) !=
            (pin.payload->intent == KvPath::InitialPrefill))
        problems.push_back("pin path must match the frozen flags");
    for (const std::string &gap : priorGaps(
            pin, pin.payload->intent, pin.payload->prior_absent,
            pin.payload->prior, next_epoch, nullptr))
        problems.push_back(gap);
    return problems;
}

std::vector<std::string>
MeshKvManager::priorGaps(const KvRequestPin &owner, KvPath intent,
                         bool prior_absent, const KvRecord &prior,
                         uint64_t next_epoch, const KvRecord *record) const
{
    std::vector<std::string> problems = prior.gaps(geometry_, false);
    if (prior.tupleKey() != owner.tupleKey())
        problems.push_back("prior tuple must match the request identity");
    if (prior.generation != owner.generation)
        problems.push_back("prior generation must match the request identity");
    if (!digestEqual(prior.contract_digest, owner.contract_digest))
        problems.push_back("prior contract must match the request identity");
    if (prior.pin_count || prior.admission_claim_count ||
            prior.outstanding_kv_dma)
        problems.push_back("prior snapshot must not carry live ownership");
    if (prior.last_use_epoch && prior.last_use_epoch >= next_epoch)
        problems.push_back("prior epoch must precede next epoch");
    if (prior_absent) {
        if (intent != KvPath::InitialPrefill) {
            problems.push_back("absent prior only belongs to INITIAL");
        } else if (prior.state != KvState::Allocating || prior.slot_id ||
                   prior.cached_tokens || prior.valid_bytes ||
                   prior.content_digest || prior.view_epoch ||
                   prior.last_use_epoch || prior.first_error ||
                   prior.diagnostic_prefix_tokens ||
                   prior.diagnostic_prefix_bytes ||
                   prior.diagnostic_prefix_digest) {
            problems.push_back("absent prior must be the empty INITIAL state");
        }
    } else if (intent == KvPath::InitialPrefill) {
        problems.push_back("INITIAL prior must be absent");
    } else if (intent == KvPath::Reprefill) {
        if (prior.state != KvState::Evicted || prior.slot_id ||
                prior.cached_tokens || prior.valid_bytes ||
                prior.content_digest)
            problems.push_back("REPREFILL prior must be an EVICTED record");
    } else if (intent == KvPath::KvReuse) {
        if (prior.state != KvState::Resident || !prior.slot_id)
            problems.push_back("KV_REUSE prior must be a RESIDENT record");
        else if (prior.cached_tokens != owner.required_cached_tokens)
            problems.push_back("KV_REUSE prior must satisfy the reuse input");
        if (record != nullptr && prior.slot_id && record->slot_id &&
                *prior.slot_id != *record->slot_id)
            problems.push_back("KV_REUSE prior must hold the owner slot");
    }
    if (!derivedInitial(owner.flags)) {
        std::optional<KvPath> path;
        const KvPolicyError policy_error = decidePath(
            owner.flags, owner.required_cached_tokens, prior, path);
        if (policy_error != KvPolicyError::None)
            problems.push_back("prior does not satisfy the frozen policy");
        else if (path != intent)
            problems.push_back("prior path must match the frozen flags");
    }
    return problems;
}

std::vector<std::string>
MeshKvManager::priorGaps(const KvAdmissionWaiter &owner, KvPath intent,
                         bool prior_absent, const KvRecord &prior,
                         uint64_t next_epoch, const KvRecord *record) const
{
    KvRequestPin proxy;
    proxy.request_id = owner.request_id;
    proxy.session_id = owner.session_id;
    proxy.kv_handle = owner.kv_handle;
    proxy.generation = owner.generation;
    proxy.contract_digest = owner.contract_digest;
    proxy.flags = owner.flags;
    proxy.required_cached_tokens = owner.required_cached_tokens;
    return priorGaps(proxy, intent, prior_absent, prior, next_epoch, record);
}

std::vector<std::string>
MeshKvManager::validate() const
{
    std::vector<std::string> problems;
    std::string reason;
    if (!geometry_.valid(reason))
        problems.push_back(reason);
    if (!capacity_.valid(reason))
        problems.push_back(reason);
    problems = [&problems, this]() {
        std::vector<std::string> all = problems;
        for (const std::string &gap : validateStructure(
                 records, slot_owners, waiters, pins, release_waiters,
                 appends, evicting, tombstones, next_kv_use_epoch,
                 next_rollback_serial, true))
            all.push_back(gap);
        return all;
    }();
    if (!problems.empty())
        return problems;

    for (const KvRecord &record : records) {
        if (record.slot_id &&
                (!slot_owners[*record.slot_id] ||
                 *slot_owners[*record.slot_id] != record.tupleKey()))
            problems.push_back("slot bitmap disagrees with the record");
        if (record.last_use_epoch &&
                record.last_use_epoch >= next_kv_use_epoch)
            problems.push_back("record epoch must precede next epoch");
    }
    std::vector<std::pair<uint64_t, uint64_t>> seen;
    for (size_t slot = 0; slot < slot_owners.size(); ++slot) {
        if (!slot_owners[slot])
            continue;
        for (const auto &owner : seen)
            if (owner == *slot_owners[slot])
                problems.push_back("duplicate slot owner");
        seen.push_back(*slot_owners[slot]);
        const KvRecord *record = find(*slot_owners[slot]);
        if (record == nullptr || !record->slot_id ||
                *record->slot_id != slot)
            problems.push_back("slot bitmap carries a stale owner");
    }
    for (const KvRecord &record : records) {
        uint32_t pin_count = 0;
        uint32_t claim_count = 0;
        for (const KvRequestPin &entry : pins)
            if (entry.session_id == record.session_id &&
                    entry.kv_handle == record.kv_handle)
                ++pin_count;
        for (const KvAdmissionWaiter &entry : waiters)
            if (entry.claimed && entry.session_id == record.session_id &&
                    entry.kv_handle == record.kv_handle)
                ++claim_count;
        if (record.pin_count != pin_count)
            problems.push_back("pin count must equal the owner set size");
        if (record.admission_claim_count != claim_count)
            problems.push_back("claim count must equal the owner set size");
        if (record.pin_count && record.admission_claim_count)
            problems.push_back("claim and pin must not coexist");
        if (record.pin_count > 1 || record.admission_claim_count > 1)
            problems.push_back("record accepts at most one KV owner");
    }
    for (const KvRequestPin &entry : pins) {
        const KvRecord *record = find(entry.session_id, entry.kv_handle);
        if (record == nullptr || record->generation != entry.generation ||
                !digestEqual(record->contract_digest,
                             entry.contract_digest)) {
            problems.push_back("KV pin identity mismatch");
            continue;
        }
        if (!record->slot_id ||
                !slot_owners[*record->slot_id] ||
                *slot_owners[*record->slot_id] != record->tupleKey())
            problems.push_back("KV pin must own its slot");
        if (entry.payload)
            for (const std::string &gap : priorGaps(
                    entry, entry.payload->intent,
                    entry.payload->prior_absent, entry.payload->prior,
                    next_kv_use_epoch, record))
                problems.push_back(gap);
    }
    for (const KvAdmissionWaiter &entry : waiters) {
        if (!entry.claimed)
            continue;
        const KvRecord *record = find(entry.session_id, entry.kv_handle);
        if (record == nullptr || record->generation != entry.generation ||
                !digestEqual(record->contract_digest,
                             entry.contract_digest)) {
            problems.push_back("KV claim identity mismatch");
            continue;
        }
        if (record->admission_claim_count != 1)
            problems.push_back("claim count must equal the owner set size");
        if (entry.intent && *entry.intent == KvPath::KvReuse) {
            if (!record->slot_id)
                problems.push_back("KV_REUSE claim must hold its slot");
        } else if (record->slot_id) {
            problems.push_back("pending claim must not hold a slot");
        }
        if (entry.intent)
            for (const std::string &gap : priorGaps(
                    entry, *entry.intent, entry.prior_absent, entry.prior,
                    next_kv_use_epoch, record))
                problems.push_back(gap);
    }
    for (const KvReleaseWaiter &entry : release_waiters) {
        const KvRecord *record = find(entry.session_id, entry.kv_handle);
        if (record == nullptr || record->generation != entry.generation)
            problems.push_back("release waiter identity mismatch");
    }
    for (const KvAppendObligation &entry : appends) {
        const KvRecord *record = find(entry.session_id, entry.kv_handle);
        if (record == nullptr || record->generation != entry.generation) {
            problems.push_back("append obligation identity mismatch");
        } else {
            bool owned = false;
            for (const KvRequestPin &entry_pin : pins)
                if (entry_pin.request_id == entry.request_id &&
                        entry_pin.tupleKey() == entry.tupleKey())
                    owned = true;
            if (!owned)
                problems.push_back(
                    "append obligation must belong to a live pin");
        }
    }
    for (const auto &item : evicting) {
        const KvRecord *record = find(item.first.first, item.first.second);
        if (record == nullptr || record->state != KvState::Evicting)
            problems.push_back("EVICTING entry without a matching record");
        else if (record->generation != item.second.generation)
            problems.push_back("EVICTING entry generation mismatch");
    }
    return problems;
}

KvPersistentState
MeshKvManager::savePersistent() const
{
    KvPersistentState state;
    state.slot_owners = slot_owners;
    state.records = records;
    state.tombstones = tombstones;
    state.next_kv_use_epoch = next_kv_use_epoch;
    return state;
}

bool
MeshKvManager::loadPersistent(const KvPersistentState &state,
                              std::string &reason)
{
    if (state.records.size() > capacity_.record_entries) {
        reason = "restore exceeds the record capacity";
        return false;
    }
    const std::vector<std::string> structure = validateStructure(
        state.records, state.slot_owners, {}, {}, {}, {}, {},
        state.tombstones, state.next_kv_use_epoch, 1, false);
    if (!structure.empty()) {
        reason = structure.front();
        return false;
    }
    MeshKvManager candidate(geometry_, capacity_);
    if (candidate.fatalReason()) {
        reason = *candidate.fatalReason();
        return false;
    }
    candidate.slot_owners = state.slot_owners;
    candidate.next_kv_use_epoch = state.next_kv_use_epoch;
    for (const KvRecord &record : state.records)
        candidate.records.push_back(record);
    std::sort(candidate.records.begin(), candidate.records.end(),
              [](const KvRecord &left, const KvRecord &right) {
                  return left.tupleKey() < right.tupleKey();
              });
    candidate.tombstones = state.tombstones;
    std::sort(candidate.tombstones.begin(), candidate.tombstones.end());
    const std::vector<std::string> problems = candidate.validate();
    if (!problems.empty()) {
        reason = problems.front();
        return false;
    }
    for (const KvRecord &record : candidate.records) {
        if (record.state != KvState::Resident &&
                record.state != KvState::Evicted &&
                record.state != KvState::Error) {
            reason = "persistent record state is not allowed";
            return false;
        }
    }
    records = candidate.records;
    tombstones = candidate.tombstones;
    slot_owners = candidate.slot_owners;
    next_kv_use_epoch = candidate.next_kv_use_epoch;
    waiters.clear();
    pins.clear();
    release_waiters.clear();
    appends.clear();
    evicting.clear();
    next_rollback_serial = 1;
    fatal_.reset();
    return true;
}

KvLiveState
MeshKvManager::saveLive() const
{
    KvLiveState state;
    state.records = records;
    state.slot_owners = slot_owners;
    state.waiters = waiters;
    state.pins = pins;
    state.release_waiters = release_waiters;
    state.appends = appends;
    state.evicting = evicting;
    state.tombstones = tombstones;
    state.next_kv_use_epoch = next_kv_use_epoch;
    state.next_rollback_serial = next_rollback_serial;
    return state;
}

bool
MeshKvManager::loadLive(const KvLiveState &state, std::string &reason)
{
    const std::vector<std::string> structure = validateStructure(
        state.records, state.slot_owners, state.waiters, state.pins,
        state.release_waiters, state.appends, state.evicting,
        state.tombstones, state.next_kv_use_epoch, state.next_rollback_serial,
        true);
    if (!structure.empty()) {
        reason = structure.front();
        return false;
    }
    MeshKvManager candidate(geometry_, capacity_);
    if (candidate.fatalReason()) {
        reason = *candidate.fatalReason();
        return false;
    }
    candidate.records = state.records;
    candidate.slot_owners = state.slot_owners;
    candidate.waiters = state.waiters;
    candidate.pins = state.pins;
    candidate.release_waiters = state.release_waiters;
    candidate.appends = state.appends;
    candidate.evicting = state.evicting;
    candidate.tombstones = state.tombstones;
    candidate.next_kv_use_epoch = state.next_kv_use_epoch;
    candidate.next_rollback_serial = state.next_rollback_serial;
    const std::vector<std::string> problems = candidate.validate();
    if (!problems.empty()) {
        reason = problems.front();
        return false;
    }
    records = candidate.records;
    slot_owners = candidate.slot_owners;
    waiters = candidate.waiters;
    pins = candidate.pins;
    release_waiters = candidate.release_waiters;
    appends = candidate.appends;
    evicting = candidate.evicting;
    tombstones = candidate.tombstones;
    next_kv_use_epoch = candidate.next_kv_use_epoch;
    next_rollback_serial = candidate.next_rollback_serial;
    fatal_.reset();
    return true;
}

void
MeshKvManager::appendRecordScalars(std::vector<uint64_t> &out,
                                   const KvRecord &record) const
{
    out.push_back(record.session_id);
    out.push_back(record.kv_handle);
    out.push_back(record.generation);
    out.push_back(static_cast<uint64_t>(record.state));
    out.push_back(record.slot_id ? 1 : 0);
    out.push_back(record.slot_id ? *record.slot_id : kKvNoSlot);
    out.push_back(record.cached_tokens);
    out.push_back(record.valid_bytes);
    out.push_back(record.view_epoch);
    out.push_back(record.last_use_epoch);
    out.push_back(record.pin_count);
    out.push_back(record.admission_claim_count);
    out.push_back(record.outstanding_kv_dma);
    out.push_back(record.content_digest ? 1 : 0);
    out.push_back(record.diagnostic_prefix_tokens);
    out.push_back(record.diagnostic_prefix_bytes);
    out.push_back(record.diagnostic_prefix_digest ? 1 : 0);
    appendDigest(out, record.contract_digest);
    appendDigest(out, record.content_digest);
    appendDigest(out, record.diagnostic_prefix_digest);
    if (!record.first_error) {
        out.insert(out.end(), 8, 0);
    } else {
        out.push_back(1);
        out.push_back(record.first_error->tick);
        out.push_back(record.first_error->code);
        appendSource(out, record.first_error->source);
    }
}

std::vector<uint64_t>
MeshKvManager::stateScalars() const
{
    std::vector<uint64_t> out;
    out.push_back(kKvSchemaVersion);
    out.push_back(next_kv_use_epoch);
    out.push_back(next_rollback_serial);
    const uint32_t words = (geometry_.max_sessions + 63) / 64;
    out.push_back(words);
    for (uint32_t word = 0; word < words; ++word) {
        uint64_t value = 0;
        for (uint32_t bit = 0; bit < 64; ++bit) {
            const uint32_t slot = word * 64 + bit;
            if (slot < geometry_.max_sessions && slot_owners[slot])
                value |= uint64_t(1) << bit;
        }
        out.push_back(value);
    }
    out.push_back(records.size());
    for (const KvRecord &record : records)
        appendRecordScalars(out, record);
    out.push_back(waiters.size());
    for (const KvAdmissionWaiter &entry : waiters) {
        out.push_back(entry.request_id);
        out.push_back(entry.session_id);
        out.push_back(entry.kv_handle);
        out.push_back(entry.generation);
        out.push_back(entry.flags);
        out.push_back(entry.deadline_or_max);
        out.push_back(entry.qos);
        out.push_back(entry.ready_tick);
        out.push_back(entry.required_cached_tokens);
        out.push_back(entry.required_tokens_after_round);
        out.push_back(entry.initial ? 1 : 0);
        out.push_back(entry.claimed ? 1 : 0);
        out.push_back(entry.intent ? static_cast<uint64_t>(*entry.intent)
                                   : kKvNoIntent);
        out.push_back(static_cast<uint64_t>(entry.policy_error));
        out.push_back(entry.prior_absent ? 1 : 0);
        if (entry.claimed)
            appendRecordScalars(out, entry.prior);
        else
            out.insert(out.end(), kKvRecordScalars, 0);
    }
    out.push_back(pins.size());
    for (const KvRequestPin &entry : pins) {
        out.push_back(entry.request_id);
        out.push_back(entry.session_id);
        out.push_back(entry.kv_handle);
        out.push_back(entry.generation);
        out.push_back(entry.flags);
        out.push_back(entry.required_cached_tokens);
        out.push_back(entry.required_tokens_after_round);
        out.push_back(static_cast<uint64_t>(entry.phase));
        if (!entry.payload) {
            out.push_back(0);
            out.push_back(kKvNoIntent);
            out.push_back(0);
            out.insert(out.end(), kKvRecordScalars, 0);
        } else {
            out.push_back(entry.payload->serial);
            out.push_back(static_cast<uint64_t>(entry.payload->intent));
            out.push_back(entry.payload->prior_absent ? 1 : 0);
            appendRecordScalars(out, entry.payload->prior);
        }
    }
    out.push_back(release_waiters.size());
    for (const KvReleaseWaiter &entry : release_waiters) {
        out.push_back(entry.request_id);
        out.push_back(entry.session_id);
        out.push_back(entry.kv_handle);
        out.push_back(entry.generation);
    }
    out.push_back(appends.size());
    for (const KvAppendObligation &entry : appends) {
        out.push_back(entry.request_id);
        out.push_back(entry.session_id);
        out.push_back(entry.kv_handle);
        out.push_back(entry.generation);
        out.push_back(entry.base_tokens);
        out.push_back(entry.append_tokens);
        out.push_back(entry.base_byte);
        out.push_back(entry.bytes);
    }
    out.push_back(evicting.size());
    for (const auto &item : evicting) {
        out.push_back(item.first.first);
        out.push_back(item.first.second);
        out.push_back(item.second.generation);
        out.push_back(static_cast<uint64_t>(item.second.prior_state));
        out.push_back(item.second.cached_tokens);
        out.push_back(item.second.valid_bytes);
    }
    out.push_back(tombstones.size());
    for (const auto &item : tombstones) {
        out.push_back(item.first.first);
        out.push_back(item.first.second);
        out.push_back(item.second);
    }
    return out;
}

std::vector<uint64_t>
MeshKvManager::decisionScalars(const KvEdgeResult &result) const
{
    std::vector<uint64_t> out;
    out.push_back(result.commit_tick);
    for (const auto &entry : result.admissions) {
        out.push_back(0);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(entry.second));
    }
    for (const auto &entry : result.promotions) {
        out.push_back(1);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(entry.second));
    }
    for (const auto &entry : result.waits) {
        out.push_back(10);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(entry.second));
    }
    for (const auto &entry : result.releases) {
        out.push_back(2);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(entry.second));
    }
    std::vector<KvEvictionEvent> started = result.evictions_started;
    std::sort(started.begin(), started.end(),
              [](const KvEvictionEvent &left, const KvEvictionEvent &right) {
                  if (left.session_id != right.session_id)
                      return left.session_id < right.session_id;
                  if (left.kv_handle != right.kv_handle)
                      return left.kv_handle < right.kv_handle;
                  return left.generation < right.generation;
              });
    for (const KvEvictionEvent &event : started) {
        out.push_back(3);
        out.push_back(event.session_id);
        out.push_back(event.kv_handle);
        out.push_back(event.generation);
        out.push_back(static_cast<uint64_t>(event.prior_state));
    }
    std::vector<KvEvictionEvent> completed = result.evictions_completed;
    std::sort(completed.begin(), completed.end(),
              [](const KvEvictionEvent &left, const KvEvictionEvent &right) {
                  if (left.session_id != right.session_id)
                      return left.session_id < right.session_id;
                  if (left.kv_handle != right.kv_handle)
                      return left.kv_handle < right.kv_handle;
                  return left.generation < right.generation;
              });
    for (const KvEvictionEvent &event : completed) {
        out.push_back(4);
        out.push_back(event.session_id);
        out.push_back(event.kv_handle);
        out.push_back(event.generation);
        out.push_back(static_cast<uint64_t>(event.prior_state));
        out.push_back(event.diagnostic_prefix_tokens);
        out.push_back(event.diagnostic_prefix_bytes);
    }
    for (const auto &entry : result.rollbacks) {
        out.push_back(5);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(entry.second));
    }
    for (const auto &entry : result.terminals) {
        const KvTerminalSnapshot &snapshot = entry.second;
        out.push_back(6);
        out.push_back(entry.first);
        out.push_back(static_cast<uint64_t>(snapshot.state));
        out.push_back(snapshot.cached_tokens);
        out.push_back(snapshot.valid_bytes);
        out.push_back(static_cast<uint64_t>(snapshot.status));
        out.push_back(static_cast<uint64_t>(snapshot.source));
        out.push_back(snapshot.content_digest ? 1 : 0);
        appendDigest(out, snapshot.contract_digest);
    }
    for (const auto &entry : result.handoffs) {
        out.push_back(7);
        out.push_back(entry.first);
        out.push_back(entry.second.serial);
    }
    for (const auto &entry : result.views) {
        const KvRuntimeView &view = entry.second;
        out.push_back(8);
        out.push_back(view.request_id);
        out.push_back(view.member_ordinal);
        out.push_back(view.view_epoch);
        out.push_back(view.slot_id);
        out.push_back(view.slot_base);
        out.push_back(view.slot_bytes);
        out.push_back(view.valid_bytes_at_arm);
    }
    std::vector<uint64_t> ownerless = result.ownerless_cancels;
    std::sort(ownerless.begin(), ownerless.end());
    for (uint64_t request_id : ownerless) {
        out.push_back(9);
        out.push_back(request_id);
    }
    return out;
}

std::vector<uint64_t>
MeshKvManager::edgeScalars(const KvEdgeResult &result) const
{
    std::vector<uint64_t> out = decisionScalars(result);
    const std::vector<uint64_t> state = stateScalars();
    out.insert(out.end(), state.begin(), state.end());
    return out;
}

}
}

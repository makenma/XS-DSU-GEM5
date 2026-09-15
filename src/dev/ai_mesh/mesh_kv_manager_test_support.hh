#ifndef DEV_AI_MESH_MESH_KV_MANAGER_TEST_SUPPORT_HH
#define DEV_AI_MESH_MESH_KV_MANAGER_TEST_SUPPORT_HH

#include <array>
#include <cstdint>
#include <cstring>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace gem5
{
namespace ai_mesh
{
namespace mesh_kv_test
{

constexpr uint32_t kBytesPerToken = 64;
constexpr uint64_t kSlotBytes = 4096;
constexpr uint32_t kKindAdmission = 0;
constexpr uint32_t kKindRelease = 1;
constexpr uint32_t kKindOwnerTerminal = 2;
constexpr uint32_t kKindLaterFault = 3;
constexpr uint32_t kKindCoreStart = 4;
constexpr uint32_t kKindAppendArm = 5;
constexpr uint32_t kKindAppendTerminal = 6;
constexpr uint32_t kKindDmaAccept = 7;
constexpr uint32_t kKindDmaTerminal = 8;
constexpr uint32_t kKindFault = 9;
constexpr uint32_t kKindViewArm = 10;
constexpr size_t kEventWords = 12;
constexpr size_t kRecordWords = 40;

KvGeometry testGeometry(uint32_t sessions = 4)
{
    KvGeometry geometry;
    geometry.region_base = 0x100000;
    geometry.slot_bytes = kSlotBytes;
    geometry.slot_alignment = kSlotBytes;
    geometry.max_sessions = sessions;
    geometry.bytes_per_token = kBytesPerToken;
    return geometry;
}

KvCapacity testCapacity(uint32_t records = 4, uint32_t tombstones = 2,
                        uint32_t waiters = 4, uint32_t releases = 2)
{
    KvCapacity capacity;
    capacity.record_entries = records;
    capacity.tombstone_entries = tombstones;
    capacity.admission_wait_entries = waiters;
    capacity.release_waiter_entries = releases;
    return capacity;
}

std::array<uint8_t, 32> digestOf(uint8_t seed)
{
    std::array<uint8_t, 32> out = {};
    for (size_t index = 0; index < out.size(); ++index)
        out[index] = static_cast<uint8_t>(seed + index);
    return out;
}

std::array<uint8_t, 32> contractDigest(uint32_t bytes_per_token)
{
    return MeshKvManager::contractDigest(digestOf(0), digestOf(32),
                                         digestOf(64), bytes_per_token);
}

KvAdmissionIntent admit(uint64_t request_id, uint64_t session_id,
                        uint32_t generation = 1, uint32_t flags = 0,
                        uint32_t qos = 0, uint64_t ready_tick = 0,
                        uint64_t deadline = 0, uint32_t cached = 0,
                        uint32_t after_round = 0,
                        std::optional<std::array<uint8_t, 32>> contract =
                            std::nullopt)
{
    KvAdmissionIntent intent;
    intent.request_id = request_id;
    intent.session_id = session_id;
    intent.kv_handle = session_id;
    intent.generation = generation;
    intent.contract_digest = contract ? *contract : contractDigest(
        kBytesPerToken);
    intent.deadline_or_max = deadline;
    intent.qos = qos;
    intent.ready_tick = ready_tick ? ready_tick : request_id;
    intent.flags = flags;
    intent.required_cached_tokens = cached;
    intent.required_tokens_after_round = after_round;
    return intent;
}

KvReleaseIntent release(uint64_t request_id, uint64_t session_id,
                        uint32_t generation = 1)
{
    KvReleaseIntent intent;
    intent.request_id = request_id;
    intent.session_id = session_id;
    intent.kv_handle = session_id;
    intent.generation = generation;
    return intent;
}

KvErrorCandidate candidate(uint64_t tick, uint32_t ordinal = 0)
{
    KvErrorCandidate value;
    value.tick = tick;
    value.source.error_class = 2;
    value.source.core_id_or_ffff = 0xFFFF;
    value.source.domain = 4;
    value.source.object_kind = 9;
    value.source.ordinal = ordinal;
    value.code = agent_abi::E_AXI_RESPONSE;
    return value;
}

KvRecord makeRecord(uint64_t session_id = 7, uint32_t generation = 1,
                    KvState state = KvState::Evicted,
                    std::optional<uint32_t> slot_id = std::nullopt,
                    uint32_t cached = 0, uint64_t view_epoch = 0,
                    uint64_t last_use = 0)
{
    KvRecord record;
    record.session_id = session_id;
    record.kv_handle = session_id;
    record.generation = generation;
    record.contract_digest = contractDigest(kBytesPerToken);
    record.state = state;
    record.slot_id = slot_id;
    record.cached_tokens = cached;
    record.valid_bytes = uint64_t(cached) * kBytesPerToken;
    if (cached)
        record.content_digest = digestOf(200);
    record.view_epoch = view_epoch;
    record.last_use_epoch = last_use;
    return record;
}

KvRequestPin makePin(uint64_t request_id, uint64_t session_id,
                     KvPinPhase phase = KvPinPhase::Prestart,
                     std::optional<KvRollbackPayload> payload = std::nullopt)
{
    KvRequestPin pin;
    pin.request_id = request_id;
    pin.session_id = session_id;
    pin.kv_handle = session_id;
    pin.generation = 1;
    pin.contract_digest = contractDigest(kBytesPerToken);
    pin.phase = phase;
    pin.payload = payload;
    return pin;
}

KvPersistentState persistentState(
    const std::vector<KvRecord> &records,
    const std::vector<std::optional<std::pair<uint64_t, uint64_t>>>
        &slot_owners,
    uint64_t next_epoch)
{
    KvPersistentState state;
    state.records = records;
    state.slot_owners = slot_owners;
    state.next_kv_use_epoch = next_epoch;
    return state;
}


}
}
}

#endif

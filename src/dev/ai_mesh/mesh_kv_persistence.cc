#include "dev/ai_mesh/mesh_kv_persistence.hh"

#include <sstream>
#include <tuple>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5::ai_mesh
{
namespace
{
std::string hex(const std::array<uint8_t, 32> &digest)
{
    constexpr char digits[] = "0123456789abcdef";
    std::string out;
    for (const auto byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 15];
    }
    return out;
}

std::array<uint8_t, 32> hash(const std::string &text)
{
    return agentSha256(std::vector<uint8_t>(text.begin(), text.end()));
}
}

bool
KvPersistentIdentity::operator==(const KvPersistentIdentity &other) const
{
    return std::tie(architecture, program, weights, layout, plan) ==
        std::tie(other.architecture, other.program, other.weights, other.layout, other.plan);
}

std::optional<std::string>
KvPersistentManifest::payload(std::string &reason) const
{
    MeshKvManager normalized(geometry, capacity);
    if (!normalized.loadPersistent(state, reason))
        return std::nullopt;
    const auto contract = MeshKvManager::contractDigest(identity.program,
        identity.weights, identity.layout, geometry.bytes_per_token);
    for (const auto &record : state.records)
        if (record.contract_digest != contract) {
            reason = "persistent record contract differs from the program, image or layout";
            return std::nullopt;
        }
    std::ostringstream out;
    out << "{\"schema\":\"ai_mesh_kv_persistent_v1\",\"identity\":{"
        << "\"architecture\":\"" << hex(identity.architecture)
        << "\",\"program\":\"" << hex(identity.program)
        << "\",\"weights\":\"" << hex(identity.weights)
        << "\",\"layout\":\"" << hex(identity.layout)
        << "\",\"plan\":\"" << hex(identity.plan)
        << "\"},\"geometry\":{\"region_base\":" << geometry.region_base
        << ",\"slot_bytes\":" << geometry.slot_bytes
        << ",\"slot_alignment\":" << geometry.slot_alignment
        << ",\"max_sessions\":" << geometry.max_sessions
        << ",\"bytes_per_token\":" << geometry.bytes_per_token
        << "},\"capacity\":{\"record_entries\":" << capacity.record_entries
        << ",\"tombstone_entries\":" << capacity.tombstone_entries
        << ",\"admission_wait_entries\":" << capacity.admission_wait_entries
        << ",\"release_waiter_entries\":" << capacity.release_waiter_entries
        << "},\"state_scalars\":[";
    bool first = true;
    for (auto scalar : normalized.stateScalars()) {
        if (!first)
            out << ',';
        first = false;
        out << scalar;
    }
    out << "]}";
    return out.str();
}

std::optional<KvPersistentManifest>
KvPersistentManifest::capture(const MeshKvManager &manager,
    const KvPersistentIdentity &identity, std::string &reason)
{
    KvPersistentManifest result;
    result.identity = identity;
    result.geometry = manager.geometry();
    result.capacity = manager.capacity();
    if (!manager.savePersistent(result.state, reason))
        return std::nullopt;
    const auto payload = result.payload(reason);
    if (!payload)
        return std::nullopt;
    result.digest = hash(*payload);
    return result;
}

bool
KvPersistentManifest::restore(MeshKvManager &manager,
    const KvPersistentIdentity &expected, const std::array<uint8_t, 32> &expectedDigest,
    std::string &reason) const
{
    const auto &target = manager.geometry();
    if (digest != expectedDigest || !(identity == expected) ||
            std::tie(geometry.region_base, geometry.slot_bytes, geometry.slot_alignment,
                     geometry.max_sessions, geometry.bytes_per_token) !=
            std::tie(target.region_base, target.slot_bytes, target.slot_alignment,
                     target.max_sessions, target.bytes_per_token)) {
        reason = "persistent identity or physical geometry differs from the restore target";
        return false;
    }
    const auto data = payload(reason);
    if (!data)
        return false;
    if (hash(*data) != digest) {
        reason = "persistent manifest digest mismatch";
        return false;
    }
    return manager.loadPersistent(state, reason);
}

std::optional<std::string>
KvPersistentManifest::json(std::string &reason) const
{
    const auto data = payload(reason);
    if (!data || hash(*data) != digest) {
        if (data)
            reason = "persistent manifest digest mismatch";
        return std::nullopt;
    }
    return "{\"manifest\":" + *data + ",\"sha256\":\"" + hex(digest) + "\"}\n";
}
}

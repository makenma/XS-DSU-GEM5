#include "dev/ai_mesh/mesh_serving_projection.hh"

#include <ostream>
#include <set>

#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr char KeyBaseDomain[] = "AGENT_PROFILE_KEY_BASE_V1";
constexpr char RequestKeyDomain[] = "AGENT_REQUEST_PROFILE_V1";

}

bool
verifyRequestPhaseMode(const DecodedProgram &program,
    const mesh_abi::AgentRequestProfile &request, MeshLoadError &error)
{
    using namespace mesh_abi;
    const auto reject = [&](const char *message) {
        error = {"E_REQUEST_PROFILE", message};
        return false;
    };
    if (request.flags != 0 && request.flags != kAgentRequestFlagsHAS_KV)
        return reject("unknown request mode");
    if (request.flags == kAgentRequestFlagsHAS_KV)
        return (request.primary_kv_symbol_id && request.kv_bytes_per_token && request.output_tokens) ||
            reject("generation requires KV and output tokens");
    if (request.primary_kv_symbol_id || request.kv_bytes_per_token || request.output_tokens ||
            request.expected_cached_tokens || request.path_mask != (1 << kPathKindINITIAL_PREFILL))
        return reject("prefill-only request carries KV or generation state");
    for (const auto &row : program.transport.agent_request_binding_requirements)
        if (row.request_program_id == request.program_id && row.request_profile_id == request.profile_id &&
                row.binding_kind == agent_abi::kBindingKindKV_EXTERNAL)
            return reject("prefill-only request declares KV storage");
    for (const auto &instance : program.transport.agent_instance_profiles) {
        if (instance.request_program_id != request.program_id || instance.request_profile_id != request.profile_id)
            continue;
        if ((instance.phase != kPhasePREFILL && instance.phase != kPhasePUBLISH) ||
                instance.path_kind != kPathKindINITIAL_PREFILL || instance.primary_kv_symbol_id ||
                instance.kv_tokens_before || instance.kv_read_bytes_per_member ||
                instance.kv_write_bytes_per_member || instance.decode_chunk_tokens)
            return reject("prefill-only instance carries KV or generation state");
        for (const auto &row : program.transport.agent_instance_tensor_bindings)
            if (row.instance_profile_id == instance.instance_profile_id && row.binding_kind == agent_abi::kBindingKindKV_EXTERNAL)
                return reject("prefill-only tensor mapping carries KV state");
        for (const auto &member : program.transport.agent_instance_member_bindings)
            if (member.instance_profile_id == instance.instance_profile_id && member.static_kv_symbol_id)
                return reject("prefill-only member carries KV state");
    }
    return true;
}

bool
programProfileKeyBaseDigest(
    const DecodedProgram &program, std::array<uint8_t, 32> &out,
    MeshLoadError &error)
{
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream stream(&buffer);
    stream.write(KeyBaseDomain, sizeof(KeyBaseDomain));
    if (!mesh_binary_detail::writeProgramProfileKeyBaseCanonical(
            program, stream, error))
        return false;
    out = buffer.digest();
    return true;
}

uint64_t
requestProfileKey(const mesh_abi::AgentRequestProfile &record,
                  const std::array<uint8_t, 32> &base_digest)
{
    auto canonical = record;
    canonical.requested_profile_key = 0;
    canonical.reserved0 = 0;
    const auto wire = mesh_abi::encodeAgentRequestProfile(canonical);
    mesh_hash::Sha256 hash;
    hash.update(reinterpret_cast<const uint8_t *>(RequestKeyDomain),
                sizeof(RequestKeyDomain));
    hash.update(base_digest.data(), base_digest.size());
    hash.update(wire.data(), wire.size());
    const auto digest = hash.digest();
    return mesh_abi::rdU64(digest.data());
}

bool
verifyRequestProfileKeys(const DecodedProgram &program, MeshLoadError &error)
{
    if (!(program.header.required_features & mesh_abi::kFeatureAgentServingV1))
        return true;
    std::array<uint8_t, 32> base;
    if (!programProfileKeyBaseDigest(program, base, error))
        return false;
    std::set<uint64_t> seen;
    for (const auto &record : program.transport.agent_request_profiles) {
        const auto expected = requestProfileKey(record, base);
        if (!record.requested_profile_key ||
            record.requested_profile_key != expected ||
            !seen.insert(expected).second) {
            error = {mesh_diagnostics::E_REQUEST_PROFILE_KEY,
                     "request profile key is invalid or duplicated"};
            return false;
        }
    }
    return true;
}

}
}

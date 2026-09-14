#include "dev/ai_mesh/mesh_serving_projection.hh"

#include <cstring>
#include <set>
#include <vector>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

using namespace mesh_abi;

constexpr char kKeyBaseDomain[] = "AGENT_PROFILE_KEY_BASE_V1";
constexpr char kRequestKeyDomain[] = "AGENT_REQUEST_PROFILE_V1";

void appendKey(std::string &out, const char *name)
{
    out += '"';
    out += name;
    out += "\":";
}

void appendOpen(std::string &out)
{
    out += "{\"abi\":{\"major\":";
    out += std::to_string(kAbiMajor);
    out += ",\"minor\":";
    out += std::to_string(kAbiMinor);
}

void appendRequiredFeatures(std::string &out, uint64_t features)
{
    if (features == 0)
        return;
    out += ",\"required_features\":";
    out += std::to_string(features);
}

void appendArchDigest(std::string &out, const std::string &hex)
{
    out += "},\"arch_digest\":\"";
    out += hex;
    out += "\",\"sections\":{";
}

template <typename T>
void appendTable(std::string &out, const std::vector<T> &records,
                 void (*emit)(const T &, std::string &))
{
    out += '[';
    for (size_t i = 0; i < records.size(); i++) {
        if (i)
            out += ',';
        emit(records[i], out);
    }
    out += ']';
}

void appendWait(const uint32_t &event_id, std::string &out)
{
    out += "{\"event_id\":";
    out += std::to_string(event_id);
    out += '}';
}

void appendString(const std::string &value, std::string &out)
{
    jsonString(out, value.data(), value.size());
}

void appendAttr(const DecodedAttr &attr, std::string &out)
{
    jsonAttrPayload(attr.kind, attr.typed, out);
}

void appendRequestProfile(std::string &out,
                          const AgentRequestProfile &record)
{
    AgentRequestProfile copy = record;
    copy.requested_profile_key = 0;
    copy.reserved0 = 0;
    jsonAgentRequestProfile(copy, out);
}

void appendRequestProfileKeyed(std::string &out,
                               const AgentRequestProfile &record)
{
    jsonAgentRequestProfile(record, out);
}

} // anonymous namespace

std::string canonicalProgramProjection(const DecodedProgram &program,
                                       bool zero_request_keys)
{
    std::string out;
    appendOpen(out);
    appendRequiredFeatures(out, program.required_features);
    appendArchDigest(out, program.arch_digest_hex);

    bool first = true;
    auto section = [&out, &first](const char *name) {
        if (!first)
            out += ',';
        first = false;
        appendKey(out, name);
    };

    if (program.required_features & kFeatureAgentServingV1) {
        section("AGENT_INSTANCE_MEMBER_BINDINGS");
        appendTable(out, program.agent_instance_member_bindings,
                    jsonAgentInstanceMemberBinding);
        section("AGENT_INSTANCE_PROFILES");
        appendTable(out, program.agent_instance_profiles,
                    jsonAgentInstanceProfile);
        section("AGENT_PUBLISH_SURROGATE_BINDINGS");
        appendTable(out, program.agent_publish_surrogate_bindings,
                    jsonAgentPublishSurrogateBinding);
        section("AGENT_REQUEST_BINDING_REQUIREMENTS");
        appendTable(out, program.agent_request_binding_requirements,
                    jsonAgentRequestBindingRequirement);
        section("AGENT_REQUEST_PROFILES");
        out += '[';
        for (size_t i = 0; i < program.agent_request_profiles.size(); i++) {
            if (i)
                out += ',';
            if (zero_request_keys)
                appendRequestProfile(out, program.agent_request_profiles[i]);
            else
                appendRequestProfileKeyed(out,
                                          program.agent_request_profiles[i]);
        }
        out += ']';
        section("AGENT_SOURCE_CORE_MAP");
        appendTable(out, program.agent_source_core_map,
                    jsonAgentSourceCoreMap);
    }

    section("ALLOCATIONS");
    appendTable(out, program.allocations, jsonAllocation);
    section("COMMANDS");
    appendTable(out, program.commands, jsonCommand);
    section("COMMAND_OPERANDS");
    appendTable(out, program.operands, jsonCommandOperand);
    section("COMMAND_WAITS");
    appendTable(out, program.waits, appendWait);
    if (program.required_features & kFeatureDynamicMoeV1) {
        section("CONTENT_DIGESTS");
        appendTable(out, program.content_digests, jsonContentDigest);
    }
    section("DMA_DESCRIPTORS");
    appendTable(out, program.descriptors, jsonDmaDescriptor);
    section("ENTRYPOINTS");
    appendTable(out, program.entrypoints, jsonEntrypoint);
    section("EVENTS");
    appendTable(out, program.events, jsonEvent);
    section("EXPECTED_TRAFFIC");
    appendTable(out, program.traffic, jsonExpectedTraffic);
    if (program.required_features & kFeatureDynamicMoeV1) {
        section("MOE_DYNAMIC_REGIONS");
        appendTable(out, program.moe_dynamic_regions, jsonMoeDynamicRegion);
        section("MOE_EXPERT_SPECS");
        appendTable(out, program.moe_expert_specs, jsonMoeExpertSpec);
        section("MOE_KERNEL_SPECS");
        appendTable(out, program.moe_kernel_specs, jsonMoeKernelSpec);
        section("MOE_LAYER_SPECS");
        appendTable(out, program.moe_layer_specs, jsonMoeLayerSpec);
    }
    section("OP_ATTRS");
    appendTable(out, program.attrs, appendAttr);
    section("PROFILES");
    appendTable(out, program.profiles, jsonProfile);
    section("RELOCATIONS");
    appendTable(out, program.relocations, jsonRelocation);
    section("SHARDS");
    appendTable(out, program.shards, jsonShard);
    section("STREAMS");
    appendTable(out, program.streams, jsonStream);
    section("STRINGS");
    appendTable(out, program.strings, appendString);
    section("TENSORS");
    appendTable(out, program.tensors, jsonTensor);

    out += "}}";
    return out;
}

std::array<uint8_t, 32> programProfileKeyBaseDigest(
    const DecodedProgram &program)
{
    const std::string projection = canonicalProgramProjection(program, true);
    AgentSha256 hash;
    hash.update(reinterpret_cast<const uint8_t *>(kKeyBaseDomain),
                sizeof(kKeyBaseDomain));
    hash.update(reinterpret_cast<const uint8_t *>(projection.data()),
                projection.size());
    return hash.finish();
}

std::array<uint8_t, 32> programSemanticDigest(const DecodedProgram &program)
{
    const std::string projection = canonicalProgramProjection(program, false);
    AgentSha256 hash;
    hash.update(reinterpret_cast<const uint8_t *>(projection.data()),
                projection.size());
    return hash.finish();
}

uint64_t requestProfileKey(const AgentRequestProfile &record,
                           const std::array<uint8_t, 32> &base_digest)
{
    uint8_t wire[kAgentRequestProfilesBytes] = {};
    writeAgentRequestProfile(wire, record);
    std::memset(wire + kAgentRequestProfilesRequestedProfileKeyOffset, 0, 8);
    std::memset(wire + kAgentRequestProfilesReserved0Offset, 0, 2);
    AgentSha256 hash;
    hash.update(reinterpret_cast<const uint8_t *>(kRequestKeyDomain),
                sizeof(kRequestKeyDomain));
    hash.update(base_digest.data(), base_digest.size());
    hash.update(wire, sizeof(wire));
    const auto digest = hash.finish();
    uint64_t key = 0;
    for (int i = 7; i >= 0; i--)
        key = (key << 8) | digest[i];
    return key;
}

bool verifyRequestProfileKeys(const DecodedProgram &program,
                              MeshLoadError &error)
{
    if ((program.required_features & kFeatureAgentServingV1) == 0)
        return true;
    const auto base = programProfileKeyBaseDigest(program);
    std::set<uint64_t> seen;
    for (const auto &record : program.agent_request_profiles) {
        const uint64_t expected = requestProfileKey(record, base);
        if (record.requested_profile_key == 0 ||
            record.requested_profile_key != expected) {
            error.code = "E_REQUEST_PROFILE_KEY";
            error.message = "request profile key does not match the base "
                            "projection";
            return false;
        }
        if (!seen.insert(expected).second) {
            error.code = "E_REQUEST_PROFILE_KEY";
            error.message = "request profile keys must be unique";
            return false;
        }
    }
    return true;
}

} // namespace ai_mesh
} // namespace gem5

#include "dev/ai_mesh/mesh_kv_layout.hh"

#include <algorithm>
#include <set>
#include <string>
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

const char kKvLayoutDomain[] = "AI_MESH_KV_LAYOUT_V1";
constexpr uint8_t kKvLayoutDomainPad = 0;

bool fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

const Relocation *relocationOf(const DecodedProgram &program, uint32_t symbol)
{
    for (const auto &relocation : program.relocations)
        if (relocation.symbol_sid == symbol)
            return &relocation;
    return nullptr;
}

void appendBinding(std::string &out, const char *role,
                   const std::string &profile, const std::string &relocation,
                   const std::string &tensor, bool &first)
{
    if (!first)
        out += ',';
    first = false;
    out += "{\"profile\":";
    out += profile;
    out += ",\"relocation\":";
    out += relocation;
    out += ",\"role\":\"";
    out += role;
    out += "\",\"tensor\":";
    out += tensor;
    out += '}';
}

std::string tensorJson(const DecodedProgram &program, uint32_t tensor_id)
{
    for (const auto &tensor : program.tensors)
        if (tensor.tensor_id == tensor_id) {
            std::string out;
            jsonTensor(tensor, out);
            return out;
        }
    return std::string();
}

std::string relocationJson(const Relocation &relocation)
{
    std::string out;
    jsonRelocation(relocation, out);
    return out;
}

}

bool kvLayoutDigest(const DecodedProgram &program,
                    std::array<uint8_t, 32> &digest, MeshLoadError &error)
{
    std::set<uint32_t> tensors;
    std::set<uint32_t> token_bytes;
    std::string bindings;
    bool first_binding = true;

    std::vector<const AgentRequestProfile *> requests;
    for (const auto &request : program.agent_request_profiles)
        requests.push_back(&request);
    std::sort(requests.begin(), requests.end(),
              [](const AgentRequestProfile *left,
                 const AgentRequestProfile *right) {
                  if (left->program_id != right->program_id)
                      return left->program_id < right->program_id;
                  return left->profile_id < right->profile_id;
              });
    for (const AgentRequestProfile *request : requests) {
        if (request->primary_kv_symbol_id == 0)
            return fail("E_KV_CONTRACT_MISMATCH",
                        "request profile has no KV binding", error);
        const Relocation *relocation =
            relocationOf(program, request->primary_kv_symbol_id);
        if (relocation == nullptr)
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV symbol has no relocation", error);
        tensors.insert(relocation->tensor_id);
        token_bytes.insert(request->kv_bytes_per_token);
        std::string record;
        jsonAgentRequestProfile(*request, record);
        const std::string tensor = tensorJson(program, relocation->tensor_id);
        if (tensor.empty())
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV binding references an unknown tensor", error);
        appendBinding(bindings, "REQUEST", record,
                      relocationJson(*relocation), tensor, first_binding);
    }

    std::vector<const AgentInstanceProfile *> instances;
    for (const auto &instance : program.agent_instance_profiles)
        instances.push_back(&instance);
    std::sort(instances.begin(), instances.end(),
              [](const AgentInstanceProfile *left,
                 const AgentInstanceProfile *right) {
                  return left->instance_profile_id <
                         right->instance_profile_id;
              });
    for (const AgentInstanceProfile *instance : instances) {
        if (instance->primary_kv_symbol_id == 0)
            continue;
        const Relocation *relocation =
            relocationOf(program, instance->primary_kv_symbol_id);
        if (relocation == nullptr)
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV symbol has no relocation", error);
        tensors.insert(relocation->tensor_id);
        std::string record;
        jsonAgentInstanceProfile(*instance, record);
        const std::string tensor = tensorJson(program, relocation->tensor_id);
        if (tensor.empty())
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV binding references an unknown tensor", error);
        appendBinding(bindings, "INSTANCE", record,
                      relocationJson(*relocation), tensor, first_binding);
    }

    for (const auto &member : program.agent_instance_member_bindings) {
        if (member.static_kv_symbol_id == 0)
            continue;
        const Relocation *relocation =
            relocationOf(program, member.static_kv_symbol_id);
        if (relocation == nullptr)
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV symbol has no relocation", error);
        tensors.insert(relocation->tensor_id);
        std::string record;
        jsonAgentInstanceMemberBinding(member, record);
        const std::string tensor = tensorJson(program, relocation->tensor_id);
        if (tensor.empty())
            return fail("E_KV_CONTRACT_MISMATCH",
                        "KV binding references an unknown tensor", error);
        appendBinding(bindings, "MEMBER", record,
                      relocationJson(*relocation), tensor, first_binding);
    }
    if (tensors.size() != 1)
        return fail("E_KV_CONTRACT_MISMATCH",
                    "KV bindings do not resolve to one tensor", error);
    if (token_bytes.size() != 1)
        return fail("E_KV_CONTRACT_MISMATCH",
                    "kv_bytes_per_token differs across request profiles",
                    error);

    std::vector<const DmaDescriptor *> descriptors;
    for (const auto &descriptor : program.descriptors)
        if (descriptor.kind == kDmaKindLOAD ||
                descriptor.kind == kDmaKindSTORE)
            descriptors.push_back(&descriptor);
    std::sort(descriptors.begin(), descriptors.end(),
              [](const DmaDescriptor *left, const DmaDescriptor *right) {
                  return left->descriptor_id < right->descriptor_id;
              });
    std::string descriptor_list;
    bool first_descriptor = true;
    for (const DmaDescriptor *descriptor : descriptors) {
        if (!tensors.count(descriptor->src.tensor_id) &&
                !tensors.count(descriptor->dst.tensor_id))
            continue;
        if (!first_descriptor)
            descriptor_list += ',';
        first_descriptor = false;
        jsonDmaDescriptor(*descriptor, descriptor_list);
    }

    std::string projection = "{\"bindings\":[";
    projection += bindings;
    projection += "],\"descriptors\":[";
    projection += descriptor_list;
    projection += "],\"schema\":\"mesh_kv_layout_v1\",\"token_bytes\":";
    projection += std::to_string(*token_bytes.begin());
    projection += '}';

    AgentSha256 hash;
    hash.update(reinterpret_cast<const uint8_t *>(kKvLayoutDomain),
                sizeof(kKvLayoutDomain) - 1);
    hash.update(&kKvLayoutDomainPad, 1);
    hash.update(reinterpret_cast<const uint8_t *>(projection.data()),
                projection.size());
    digest = hash.finish();
    return true;
}

}
}

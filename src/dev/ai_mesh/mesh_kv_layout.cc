#include "dev/ai_mesh/mesh_kv_layout.hh"

#include <algorithm>
#include <map>
#include <ostream>
#include <set>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"

namespace gem5
{
namespace ai_mesh
{

bool kvLayoutDigest(const DecodedProgram &program,
                    const ProgramSemanticContext &context,
                    std::array<uint8_t, 32> &digest, MeshLoadError &error)
{
    using namespace mesh_abi;
    const auto &transport = program.transport;
    auto reject = [&](const char *message) {
        error = {"E_KV_CONTRACT_MISMATCH", message};
        return false;
    };
    if (!context.matches(program))
        return reject("KV layout requires its own semantic context");
    struct Binding
    {
        size_t request;
        size_t instance;
        size_t member;
        size_t relocation;
        size_t tensor;
    };
    using RequestKey = std::pair<uint32_t, uint32_t>;
    std::vector<Binding> bindings;
    std::set<uint32_t> tensors;
    std::set<uint32_t> tokenBytes;
    std::map<RequestKey, size_t> requests;
    for (size_t index = 0; index < transport.agent_request_profiles.size(); ++index) {
        const auto &request = transport.agent_request_profiles[index];
        if (!(request.flags & mesh_abi::kAgentRequestFlagsHAS_KV))
            continue;
        if (!request.primary_kv_symbol_id ||
            request.primary_kv_symbol_id > transport.strings.size() ||
            !requests.emplace(RequestKey{request.program_id, request.profile_id}, index).second)
            return reject("KV request identities or roles are invalid");
        tokenBytes.insert(request.kv_bytes_per_token);
    }
    if (requests.empty() || tokenBytes.size() != 1 || !*tokenBytes.begin())
        return reject("KV requests require one positive token size");
    std::vector<size_t> instances;
    std::set<uint32_t> instanceIds;
    for (size_t index = 0; index < transport.agent_instance_profiles.size(); ++index) {
        if (!instanceIds.insert(transport.agent_instance_profiles[index].instance_profile_id).second)
            return reject("KV instance identities are duplicated");
        instances.push_back(index);
    }
    std::sort(instances.begin(), instances.end(), [&](size_t left, size_t right) {
        return transport.agent_instance_profiles[left].instance_profile_id <
               transport.agent_instance_profiles[right].instance_profile_id;
    });
    std::set<RequestKey> boundRequests;
    std::set<size_t> consumedMembers;
    for (const size_t instanceIndex : instances) {
        const auto &instance = transport.agent_instance_profiles[instanceIndex];
        if (!instance.primary_kv_symbol_id)
            continue;
        const RequestKey key{instance.request_program_id, instance.request_profile_id};
        const auto requestIt = requests.find(key);
        if (requestIt == requests.end() || instance.primary_kv_symbol_id !=
            transport.agent_request_profiles[requestIt->second].primary_kv_symbol_id)
            return reject("instance KV role differs from its request");
        const semantic_abi::ProgramVariant *variant = nullptr;
        for (const auto &[id, candidate] : context.variants()) {
            if (candidate->entrypoint_id == instance.mesh_entrypoint_id &&
                candidate->profile_id == instance.mesh_profile_id) {
                if (variant)
                    return reject("KV instance variant is ambiguous");
                variant = candidate;
            }
        }
        if (!variant)
            return reject("KV instance has no admitted variant");
        const uint64_t begin = instance.member_binding_first;
        const uint64_t end = begin + instance.member_binding_count;
        if (!instance.member_count || instance.member_binding_count != instance.member_count ||
            end > transport.agent_instance_member_bindings.size())
            return reject("KV member span is invalid");
        for (uint64_t index = begin; index < end; ++index) {
            const auto &member = transport.agent_instance_member_bindings[index];
            if (!consumedMembers.insert(index).second ||
                member.instance_profile_id != instance.instance_profile_id ||
                member.member_ordinal != index - begin)
                return reject("KV member has no unique instance owner");
            if (!member.static_kv_symbol_id || member.static_kv_symbol_id > transport.strings.size())
                return reject("KV member slot symbol is missing");
            const auto binding = context.symbolBinding(variant->variant_id,
                transport.strings[member.static_kv_symbol_id - 1]);
            if (!binding)
                return reject("KV member slot has no admitted variant owner");
            const auto &tensor = *binding->tensor;
            const auto &logical = *binding->logical_tensor;
            if (tensor.role != kTensorRoleKV_CACHE ||
                tensor.storage_class != kStorageClassEXTERNAL || tensor.access != kAccessKindREAD_WRITE ||
                uint16_t(logical.role) != kTensorRoleKV_CACHE ||
                uint16_t(logical.storage_class) != kStorageClassEXTERNAL ||
                uint16_t(logical.access) != kAccessKindREAD_WRITE)
                return reject("KV member slot must own an external read-write KV tensor");
            bindings.push_back({requestIt->second, instanceIndex, size_t(index),
                                size_t(binding->relocation - transport.relocations.data()),
                                size_t(binding->tensor - transport.tensors.data())});
            tensors.insert(tensor.tensor_id);
            boundRequests.insert(key);
        }
    }
    if (boundRequests.size() != requests.size())
        return reject("KV request has no bound instance member");
    for (size_t index = 0; index < transport.agent_instance_member_bindings.size(); ++index)
        if (bool(transport.agent_instance_member_bindings[index].static_kv_symbol_id) !=
            bool(consumedMembers.count(index)))
            return reject("KV member is not owned by its instance span");
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream out(&buffer);
    constexpr char domain[] = "AI_MESH_KV_LAYOUT_V2";
    out.write(domain, sizeof(domain));
    auto row = [&](uint16_t section, size_t index) {
        return mesh_binary_detail::writeTransportRowCanonical(program, section, index, out, error);
    };
    out << "{\"bindings\":[";
    bool first = true;
    for (const auto &binding : bindings) {
        if (!first) out << ',';
        first = false;
        out << "{\"instance\":";
        if (!row(kSectionTypeAGENT_INSTANCE_PROFILES, binding.instance)) return false;
        out << ",\"member\":";
        if (!row(kSectionTypeAGENT_INSTANCE_MEMBER_BINDINGS, binding.member)) return false;
        out << ",\"relocation\":";
        if (!row(kSectionTypeRELOCATIONS, binding.relocation)) return false;
        out << ",\"request\":";
        if (!row(kSectionTypeAGENT_REQUEST_PROFILES, binding.request)) return false;
        out << ",\"tensor\":";
        if (!row(kSectionTypeTENSORS, binding.tensor)) return false;
        out << '}';
    }
    std::vector<size_t> descriptors;
    for (size_t index = 0; index < transport.dma_descriptors.size(); ++index) {
        const auto &descriptor = transport.dma_descriptors[index];
        if ((descriptor.kind == kDmaKindLOAD || descriptor.kind == kDmaKindSTORE) &&
            (tensors.count(descriptor.src.tensor_id) || tensors.count(descriptor.dst.tensor_id)))
            descriptors.push_back(index);
    }
    std::sort(descriptors.begin(), descriptors.end(), [&](size_t left, size_t right) {
        return transport.dma_descriptors[left].descriptor_id < transport.dma_descriptors[right].descriptor_id;
    });
    out << "],\"descriptors\":[";
    first = true;
    for (const size_t index : descriptors) {
        if (!first) out << ',';
        first = false;
        if (!row(kSectionTypeDMA_DESCRIPTORS, index)) return false;
    }
    out << "],\"schema\":\"mesh_kv_layout_v2\",\"token_bytes\":" << *tokenBytes.begin() << '}';
    if (!out)
        return reject("KV canonical output stream failed");
    digest = buffer.digest();
    return true;
}

}
}

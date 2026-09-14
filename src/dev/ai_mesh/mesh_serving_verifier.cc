#include "dev/ai_mesh/mesh_serving_verifier.hh"

#include <algorithm>
#include <cstdint>
#include <map>
#include <set>
#include <tuple>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

using namespace mesh_abi;

bool fail(const char *code, const char *message, MeshLoadError &error)
{
    error.code = code;
    error.message = message;
    return false;
}

constexpr uint16_t kPathMaskAll = 0x7;

bool anyServingTable(const DecodedProgram &program)
{
    return !program.agent_request_profiles.empty() ||
           !program.agent_instance_profiles.empty() ||
           !program.agent_source_core_map.empty() ||
           !program.agent_instance_member_bindings.empty() ||
           !program.agent_request_binding_requirements.empty() ||
           !program.agent_publish_surrogate_bindings.empty();
}

bool coreInArch(const RuntimeArch &arch, uint16_t core_id)
{
    return std::find(arch.core_ids.begin(), arch.core_ids.end(), core_id) !=
           arch.core_ids.end();
}

const AgentRequestProfile *requestOf(const DecodedProgram &program,
                                     uint16_t program_id,
                                     uint16_t profile_id)
{
    for (const auto &record : program.agent_request_profiles)
        if (record.program_id == program_id &&
            record.profile_id == profile_id)
            return &record;
    return nullptr;
}

const Command *commandOf(const DecodedProgram &program, uint32_t command_id)
{
    for (const auto &record : program.commands)
        if (record.command_id == command_id)
            return &record;
    return nullptr;
}

const Event *eventOf(const DecodedProgram &program, uint32_t event_id)
{
    for (const auto &record : program.events)
        if (record.event_id == event_id)
            return &record;
    return nullptr;
}

const Allocation *allocationOf(const DecodedProgram &program,
                               uint32_t allocation_id)
{
    for (const auto &record : program.allocations)
        if (record.allocation_id == allocation_id)
            return &record;
    return nullptr;
}


using RankVector = std::vector<uint32_t>;
using ServingSelector = std::tuple<uint16_t, uint16_t, uint16_t, uint16_t,
                                   uint16_t, uint32_t, uint32_t, uint16_t,
                                   RankVector>;

struct ServingInterval
{
    uint64_t start = 0;
    uint64_t end = 0;
};

void collectRows(std::vector<ServingInterval> &out, uint32_t wanted,
                 const DmaEndpoint &endpoint, uint64_t stride,
                 uint32_t rows, uint64_t row_bytes);
bool coverageGaps(const std::vector<ServingInterval> &intervals,
                  uint64_t start, uint64_t end, std::string &reason);

constexpr int kRoleHostInput = 0;
constexpr int kRoleHostOutput = 1;
constexpr int kRoleKvRead = 2;
constexpr int kRoleKvStore = 3;

struct MemberView
{
    bool present = false;
    uint32_t tensor = 0;
    uint64_t start = 0;
    uint64_t end = 0;
};

bool memberView(const DecodedProgram &program,
                const AgentRequestProfile &request,
                const AgentInstanceProfile &instance, uint32_t ordinal,
                int role, MemberView &out);
bool descriptorRoleMatches(const DecodedProgram &program,
                           const AgentRequestProfile &request,
                           const DmaDescriptor &descriptor, int role);
bool descriptorCandidates(const DecodedProgram &program,
                          const AgentRequestProfile &request,
                          const AgentInstanceProfile &instance,
                          const DmaDescriptor &descriptor, int role,
                          std::vector<uint32_t> &out, MeshLoadError &error);
bool descriptorAttributedTo(const DecodedProgram &program,
                            const AgentRequestProfile &request,
                            const AgentInstanceProfile &instance,
                            const DmaDescriptor &descriptor, int role,
                            uint32_t ordinal, MeshLoadError &error);

const Relocation *relocationOf(const DecodedProgram &program, uint32_t symbol)
{
    for (const auto &record : program.relocations)
        if (record.symbol_sid == symbol)
            return &record;
    return nullptr;
}

bool rankVectorOf(const DecodedProgram &program,
                  const AgentInstanceProfile &instance, RankVector &out)
{
    const size_t first = instance.member_binding_first;
    const size_t end = first + instance.member_binding_count;
    if (end > program.agent_instance_member_bindings.size())
        return false;
    out.clear();
    for (size_t i = first; i < end; i++)
        out.push_back(program.agent_instance_member_bindings[i].
                      expected_logical_source_rank);
    return true;
}

bool memberCore(const DecodedProgram &program,
                const AgentRequestProfile &request,
                const AgentInstanceProfile &instance, uint32_t ordinal,
                RankVector &ranks, uint32_t &core)
{
    if (!rankVectorOf(program, instance, ranks) || ordinal >= ranks.size())
        return false;
    const uint64_t index = uint64_t(request.source_core_map_begin) +
                           ranks[ordinal];
    if (index >= program.agent_source_core_map.size())
        return false;
    core = program.agent_source_core_map[index].core_id;
    return true;
}

uint32_t endpointAllocation(const DecodedProgram &program,
                            const DmaEndpoint &endpoint)
{
    if (endpoint.memory_space != kMemorySpaceCORE_SRAM ||
        endpoint.shard_id == 0)
        return 0;
    for (const auto &shard : program.shards)
        if (shard.shard_id == endpoint.shard_id)
            return shard.allocation_id;
    return 0;
}

bool computeWriteOpcode(uint16_t opcode)
{
    switch (opcode) {
    case kOpcodeGEMM:
    case kOpcodeBMM:
    case kOpcodeELEMENTWISE:
    case kOpcodeLOCAL_REDUCE:
    case kOpcodeSOFTMAX:
    case kOpcodeNORM:
        return true;
    default:
        return false;
    }
}

bool sramWriteKind(uint16_t kind)
{
    switch (kind) {
    case kDmaKindLOAD:
    case kDmaKindLOCAL_FILL:
    case kDmaKindPREFETCH:
    case kDmaKindP2P_PUSH:
        return true;
    default:
        return false;
    }
}

std::set<uint32_t> allocationWriters(const DecodedProgram &program,
                                     uint32_t allocation_id)
{
    std::set<uint32_t> writers;
    for (const auto &command : program.commands) {
        if (!computeWriteOpcode(command.opcode))
            continue;
        const size_t end = command.operand_begin + command.operand_count;
        if (end > program.operands.size())
            continue;
        for (size_t i = command.operand_begin; i < end; i++)
            if (program.operands[i].allocation_id == allocation_id &&
                program.operands[i].access == kAccessKindREAD_WRITE)
                writers.insert(command.command_id);
    }
    for (const auto &descriptor : program.descriptors) {
        if (!sramWriteKind(descriptor.kind))
            continue;
        if (endpointAllocation(program, descriptor.dst) == allocation_id)
            writers.insert(descriptor.command_id);
    }
    return writers;
}

bool waitsFor(const DecodedProgram &program, const Command &command,
              uint32_t event_id)
{
    const size_t end = command.wait_begin + command.wait_count;
    if (end > program.waits.size())
        return false;
    for (size_t i = command.wait_begin; i < end; i++)
        if (program.waits[i] == event_id)
            return true;
    return false;
}

std::vector<const DmaDescriptor *>
profileDescriptors(const DecodedProgram &program,
                  const AgentInstanceProfile &instance)
{
    std::vector<const DmaDescriptor *> out;
    for (const auto &row : program.traffic) {
        if (row.entrypoint_id != instance.mesh_entrypoint_id ||
            row.profile_id != instance.mesh_profile_id)
            continue;
        for (const auto &descriptor : program.descriptors)
            if (descriptor.descriptor_id == row.descriptor_id)
                out.push_back(&descriptor);
    }
    return out;
}

bool legalBindingFlags(uint16_t kind, uint16_t flags)
{
    using namespace ::gem5::ai_mesh::agent_abi;
    const uint16_t read = kBindingFlagsREAD;
    const uint16_t write = kBindingFlagsWRITE;
    const uint16_t persist = kBindingFlagsPERSISTENT;
    const uint16_t by_handle = kBindingFlagsRESOLVE_BY_HANDLE;
    switch (kind) {
    case kBindingKindHOST_INPUT:
        return flags == read;
    case kBindingKindHOST_OUTPUT:
        return flags == write;
    case kBindingKindKV_EXTERNAL:
        return flags == (read | write | persist | by_handle);
    case kBindingKindWEIGHT_EXTERNAL:
        return flags == (read | persist);
    default:
        return false;
    }
}

bool phaseAccess(uint16_t phase, bool &input, bool &output, bool &kv)
{
    switch (phase) {
    case kPhasePREFILL:
        input = true, output = false, kv = true;
        return true;
    case kPhaseDECODE:
        input = false, output = false, kv = true;
        return true;
    case kPhasePUBLISH:
        input = false, output = true, kv = false;
        return true;
    default:
        return false;
    }
}

bool memberSpan(const DecodedProgram &program,
                const AgentInstanceProfile &instance, size_t &first,
                size_t &end, MeshLoadError &error)
{
    first = instance.member_binding_first;
    end = first + instance.member_binding_count;
    if (end > program.agent_instance_member_bindings.size())
        return fail("E_BINDING_ROLE", "member binding span out of range",
                    error);
    return true;
}

bool verifyRequestProfiles(const DecodedProgram &program,
                           const RuntimeArch &arch, MeshLoadError &error)
{
    std::set<std::pair<uint16_t, uint16_t>> keys;
    std::map<uint16_t, std::set<std::pair<uint32_t, uint32_t>>> rank_maps;
    uint32_t kv_bytes_per_token = 0;
    for (const auto &request : program.agent_request_profiles) {
        if (request.program_id == 0 || request.profile_id == 0)
            return fail("E_REQUEST_PROFILE",
                        "request profile id must be nonzero", error);
        if (!keys.insert({request.program_id, request.profile_id}).second)
            return fail("E_ABI_DUPLICATE", "duplicate request profile",
                        error);
        if (request.flags != kAgentRequestFlagsHAS_KV)
            return fail("E_REQUEST_PROFILE",
                        "serving request profile requires HAS_KV", error);
        if (request.path_mask == 0 ||
            (request.path_mask & ~kPathMaskAll) != 0)
            return fail("E_REQUEST_PROFILE",
                        "path_mask is not a legal path set", error);
        if (request.kv_bytes_per_token == 0)
            return fail("E_REQUEST_PROFILE",
                        "kv_bytes_per_token must be positive", error);
        if (kv_bytes_per_token == 0)
            kv_bytes_per_token = request.kv_bytes_per_token;
        else if (kv_bytes_per_token != request.kv_bytes_per_token)
            return fail("E_REQUEST_PROFILE",
                        "kv_bytes_per_token must match across the program",
                        error);
        if (request.full_input_dma_bytes != request.input_binding_bytes ||
            request.delta_input_dma_bytes == 0 ||
            request.delta_input_dma_bytes > request.full_input_dma_bytes)
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "request host input byte relation is broken", error);
        if (request.delta_input_tokens > request.full_input_tokens ||
            request.full_input_tokens - request.delta_input_tokens !=
                request.expected_cached_tokens)
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "request token/cached relation is broken", error);
        if (request.publish_chunk_bytes == 0 ||
            request.publish_chunk_bytes % arch.axi_data_bytes != 0 ||
            request.publish_chunk_bytes > request.host_output_bytes)
            return fail("E_OUTPUT_CHUNK_MISMATCH",
                        "publish_chunk_bytes is not AXI-aligned in range",
                        error);
        if (request.host_output_bytes == 0 || request.output_tokens == 0)
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "request output size/tokens must be positive", error);
        if (request.source_rank_count == 0)
            return fail("E_REQUEST_PROFILE",
                        "source_rank_count must be positive", error);
        rank_maps[request.program_id].insert(
            {request.source_rank_count, request.source_core_map_begin});
    }
    for (const auto &entry : rank_maps)
        if (entry.second.size() > 1)
            return fail("E_REQUEST_PROFILE",
                        "request profiles of one program must share one "
                        "rank map", error);
    return true;
}

bool verifyInstanceMeshBinding(const DecodedProgram &program,
                               const AgentInstanceProfile &instance,
                               MeshLoadError &error)
{
    const Entrypoint *entrypoint = nullptr;
    for (const auto &record : program.entrypoints)
        if (record.entrypoint_id == instance.mesh_entrypoint_id)
            entrypoint = &record;
    if (entrypoint == nullptr)
        return fail("E_REQUEST_PROFILE", "instance entrypoint is missing",
                    error);
    bool profile_found = false;
    for (const auto &record : program.profiles)
        if (record.profile_id == instance.mesh_profile_id)
            profile_found = true;
    if (!profile_found)
        return fail("E_REQUEST_PROFILE", "instance mesh profile is missing",
                    error);
    const size_t begin = entrypoint->profile_begin;
    const size_t end = begin + entrypoint->profile_count;
    if (end > program.profiles.size())
        return fail("E_REQUEST_PROFILE", "entrypoint profile span is broken",
                    error);
    for (size_t i = begin; i < end; i++)
        if (program.profiles[i].profile_id == instance.mesh_profile_id)
            return true;
    return fail("E_REQUEST_PROFILE",
                "instance mesh profile is outside its entrypoint", error);
}

bool verifyInstanceByteRelation(const DecodedProgram &program,
                                const AgentInstanceProfile &instance,
                                MeshLoadError &error)
{
    const AgentRequestProfile *request =
        requestOf(program, instance.request_program_id,
                  instance.request_profile_id);
    if (request == nullptr)
        return fail("E_REQUEST_PROFILE",
                    "instance references no request profile", error);
    uint64_t new_tokens = 0;
    if (instance.phase == kPhasePREFILL)
        new_tokens = instance.valid_tokens_per_member;
    else if (instance.phase == kPhaseDECODE)
        new_tokens = instance.decode_chunk_tokens;
    if (instance.kv_write_bytes_per_member !=
        new_tokens * request->kv_bytes_per_token)
        return fail("E_KV_TOKEN_MISMATCH",
                    "kv_write_bytes_per_member does not match the phase",
                    error);
    if (instance.phase == kPhasePUBLISH) {
        if (instance.kv_read_bytes_per_member != 0 ||
            instance.host_input_dma_bytes_per_member != 0 ||
            instance.host_output_dma_bytes_per_member !=
                request->host_output_bytes)
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "PUBLISH instance byte roles are broken", error);
        return true;
    }
    if (instance.host_output_dma_bytes_per_member != 0)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "non-PUBLISH instance must not store host output", error);
    if (instance.phase == kPhasePREFILL &&
        instance.host_input_dma_bytes_per_member == 0)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "PREFILL instance must load host input", error);
    if (instance.phase == kPhaseDECODE &&
        instance.host_input_dma_bytes_per_member != 0)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "DECODE instance must not load host input", error);
    return true;
}

bool verifyInstanceProfiles(const DecodedProgram &program,
                            MeshLoadError &error)
{
    std::set<uint32_t> ids;
    std::set<ServingSelector> selectors;
    for (const auto &instance : program.agent_instance_profiles) {
        if (instance.instance_profile_id == 0)
            return fail("E_REQUEST_PROFILE",
                        "instance profile id must be nonzero", error);
        if (!ids.insert(instance.instance_profile_id).second)
            return fail("E_ABI_DUPLICATE", "duplicate instance_profile_id",
                        error);
        if (requestOf(program, instance.request_program_id,
                      instance.request_profile_id) == nullptr)
            return fail("E_REQUEST_PROFILE",
                        "instance references no request profile", error);
        bool input = false, output = false, kv = false;
        if (!phaseAccess(instance.phase, input, output, kv))
            return fail("E_REQUEST_PROFILE", "unknown instance phase", error);
        if (instance.member_count == 0)
            return fail("E_REQUEST_PROFILE", "member_count must be positive",
                        error);
        if (instance.member_binding_count != instance.member_count)
            return fail("E_BINDING_ROLE",
                        "member_binding_count must equal member_count", error);
        size_t first = 0, end = 0;
        if (!memberSpan(program, instance, first, end, error))
            return false;
        (void)first;
        (void)end;
        RankVector ranks;
        if (!rankVectorOf(program, instance, ranks))
            return fail("E_BINDING_ROLE",
                        "member binding span out of range", error);
        ServingSelector selector{instance.request_program_id,
                                 instance.request_profile_id,
                                 instance.path_kind, instance.phase,
                                 instance.member_count,
                                 instance.valid_tokens_per_member,
                                 instance.kv_tokens_before,
                                 instance.decode_chunk_tokens, ranks};
        if (!selectors.insert(selector).second)
            return fail("E_ABI_DUPLICATE", "duplicate instance selector",
                        error);
        if (!verifyInstanceMeshBinding(program, instance, error))
            return false;
        if ((instance.primary_input_symbol_id != 0) != input)
            return fail("E_BINDING_ROLE",
                        "instance primary input symbol role mismatch", error);
        if ((instance.primary_output_symbol_id != 0) != output)
            return fail("E_BINDING_ROLE",
                        "instance primary output symbol role mismatch", error);
        if ((instance.primary_kv_symbol_id != 0) != kv)
            return fail("E_BINDING_ROLE",
                        "instance primary kv symbol role mismatch", error);
        if (instance.flags != 0)
            return fail("E_ABI_RESERVED", "instance flags must be zero",
                        error);
        if (instance.phase == kPhaseDECODE) {
            if (instance.decode_chunk_tokens == 0)
                return fail("E_REQUEST_PROFILE",
                            "decode instance needs a positive chunk", error);
        } else if (instance.decode_chunk_tokens != 0) {
            return fail("E_REQUEST_PROFILE",
                        "only DECODE instances carry decode_chunk_tokens",
                        error);
        }
        if (!verifyInstanceByteRelation(program, instance, error))
            return false;
    }
    return true;
}

bool verifySourceCoreMap(const DecodedProgram &program,
                         const RuntimeArch &arch, MeshLoadError &error)
{
    for (const auto &request : program.agent_request_profiles) {
        const uint64_t begin = request.source_core_map_begin;
        const uint64_t end = begin + request.source_rank_count;
        if (end > program.agent_source_core_map.size())
            return fail("E_REQUEST_PROFILE",
                        "source core map span out of range", error);
        for (uint64_t i = begin; i < end; i++)
            if (!coreInArch(arch, program.agent_source_core_map[i].core_id))
                return fail("E_REQUEST_PROFILE",
                            "source core map core is not in the architecture",
                            error);
    }
    return true;
}

bool verifyMemberBindings(const DecodedProgram &program,
                          MeshLoadError &error)
{
    std::set<std::pair<uint32_t, uint16_t>> roles;
    for (const auto &instance : program.agent_instance_profiles) {
        const AgentRequestProfile *request =
            requestOf(program, instance.request_program_id,
                      instance.request_profile_id);
        if (request == nullptr)
            return fail("E_REQUEST_PROFILE",
                        "instance references no request profile", error);
        bool input = false, output = false, kv = false;
        if (!phaseAccess(instance.phase, input, output, kv))
            return fail("E_REQUEST_PROFILE", "unknown instance phase", error);
        size_t first = 0, end = 0;
        if (!memberSpan(program, instance, first, end, error))
            return false;
        std::set<uint32_t> symbols;
        uint32_t ordinal = 0;
        for (size_t i = first; i < end; i++, ordinal++) {
            const auto &record = program.agent_instance_member_bindings[i];
            if (record.instance_profile_id != instance.instance_profile_id)
                return fail("E_BINDING_ROLE",
                            "member binding targets another instance profile",
                            error);
            if (record.member_ordinal != ordinal)
                return fail("E_BINDING_ROLE",
                            "member ordinals must be dense from zero", error);
            if (record.expected_logical_source_rank >=
                request->source_rank_count)
                return fail("E_REQUEST_PROFILE",
                            "member expected rank exceeds source_rank_count",
                            error);
            if ((record.static_input_symbol_id != 0) != input ||
                (record.static_output_symbol_id != 0) != output ||
                (record.static_kv_symbol_id != 0) != kv)
                return fail("E_BINDING_ROLE",
                            "member symbol presence violates the phase "
                            "matrix", error);
            const std::pair<uint32_t, uint16_t> pairs[] = {
                {record.static_input_symbol_id, 0},
                {record.static_output_symbol_id, 1},
                {record.static_kv_symbol_id, 2},
            };
            for (const auto &pair : pairs) {
                if (pair.first == 0)
                    continue;
                if (!symbols.insert(pair.first).second)
                    return fail("E_BINDING_ROLE",
                                "member symbols must be globally distinct",
                                error);
                const auto role = std::make_pair(pair.first, pair.second);
                if (!roles.insert(role).second) {
                    for (const auto &existing : roles)
                        if (existing.first == pair.first &&
                            existing.second != pair.second)
                            return fail("E_BINDING_ROLE",
                                        "symbol changes role across profiles",
                                        error);
                }
            }
        }
        const uint32_t expected_input =
            instance.phase == kPhasePREFILL
                ? request->primary_input_symbol_id : 0;
        const uint32_t expected_output =
            instance.phase == kPhasePUBLISH
                ? request->primary_output_symbol_id : 0;
        const uint32_t expected_kv =
            instance.phase == kPhasePUBLISH
                ? 0 : request->primary_kv_symbol_id;
        if (instance.primary_input_symbol_id != expected_input ||
            instance.primary_output_symbol_id != expected_output ||
            instance.primary_kv_symbol_id != expected_kv)
            return fail("E_BINDING_ROLE", "instance primary symbol mismatch",
                        error);
    }
    return true;
}

bool primaryRequirement(const DecodedProgram &program, uint16_t program_id,
                        uint16_t profile_id, uint16_t symbol_id,
                        uint16_t kind)
{
    if (symbol_id == 0)
        return true;
    size_t matches = 0;
    bool kind_ok = false;
    for (const auto &requirement :
         program.agent_request_binding_requirements) {
        if (requirement.request_program_id != program_id ||
            requirement.request_profile_id != profile_id ||
            requirement.symbol_id != symbol_id)
            continue;
        matches++;
        kind_ok = requirement.binding_kind == kind;
    }
    return matches == 1 && kind_ok;
}

bool verifyRequirements(const DecodedProgram &program, MeshLoadError &error)
{
    using namespace ::gem5::ai_mesh::agent_abi;
    std::set<std::tuple<uint16_t, uint16_t, uint32_t>> seen;
    std::tuple<uint16_t, uint16_t, uint32_t> previous{0, 0, 0};
    bool first = true;
    for (const auto &requirement :
         program.agent_request_binding_requirements) {
        if (requestOf(program, requirement.request_program_id,
                      requirement.request_profile_id) == nullptr)
            return fail("E_REQUEST_PROFILE",
                        "requirement references no request profile", error);
        if (requirement.symbol_id == 0)
            return fail("E_BINDING_ROLE",
                        "requirement symbol id 0 is invalid", error);
        auto key = std::make_tuple(requirement.request_program_id,
                                   requirement.request_profile_id,
                                   requirement.symbol_id);
        if (!seen.insert(key).second)
            return fail("E_ABI_DUPLICATE", "duplicate request requirement",
                        error);
        if (!first && key < previous)
            return fail("E_ABI_ORDER",
                        "requirements must be sorted by request and symbol",
                        error);
        previous = key;
        first = false;
        if (!legalBindingFlags(requirement.binding_kind,
                               requirement.binding_flags))
            return fail("E_BINDING_ROLE",
                        "binding flags are not legal for the kind", error);
    }
    for (const auto &request : program.agent_request_profiles) {
        if (!primaryRequirement(program, request.program_id,
                                request.profile_id,
                                request.primary_input_symbol_id,
                                kBindingKindHOST_INPUT) ||
            !primaryRequirement(program, request.program_id,
                                request.profile_id,
                                request.primary_output_symbol_id,
                                kBindingKindHOST_OUTPUT) ||
            !primaryRequirement(program, request.program_id,
                                request.profile_id,
                                request.primary_kv_symbol_id,
                                kBindingKindKV_EXTERNAL))
            return fail("E_BINDING_ROLE",
                        "primary symbol has no matching requirement", error);
    }
    return true;
}

bool verifySymbolClassification(const DecodedProgram &program,
                                MeshLoadError &error)
{
    std::set<uint32_t> requirement_symbols;
    for (const auto &requirement :
         program.agent_request_binding_requirements)
        requirement_symbols.insert(requirement.symbol_id);
    std::set<uint32_t> member_symbols;
    for (const auto &record : program.agent_instance_member_bindings) {
        const uint32_t symbols[] = {record.static_input_symbol_id,
                                    record.static_output_symbol_id,
                                    record.static_kv_symbol_id};
        for (uint32_t symbol : symbols)
            if (symbol != 0)
                member_symbols.insert(symbol);
    }
    for (uint32_t symbol : member_symbols)
        if (requirement_symbols.count(symbol))
            return fail("E_BINDING_ROLE",
                        "symbol is both REQUEST_BINDABLE and "
                        "INSTANCE_MEMBER_SLOT", error);
    for (const auto &relocation : program.relocations) {
        const Tensor *tensor = nullptr;
        for (const auto &record : program.tensors)
            if (record.tensor_id == relocation.tensor_id)
                tensor = &record;
        if (tensor == nullptr)
            return fail("E_RELOCATION",
                        "relocation references a missing tensor", error);
        if (tensor->storage_class != kStorageClassEXTERNAL)
            continue;
        if (!requirement_symbols.count(relocation.symbol_sid) &&
            !member_symbols.count(relocation.symbol_sid))
            return fail("E_BINDING_ROLE",
                        "external relocation symbol is unclassified", error);
    }
    return true;
}

bool verifyPublishStoreChain(const DecodedProgram &program,
                             const AgentRequestProfile &request,
                             const AgentInstanceProfile &instance,
                             uint32_t ordinal,
                             const AgentPublishSurrogateBinding &record,
                             MeshLoadError &error)
{
    MemberView output_view;
    if (!memberView(program, request, instance, ordinal, kRoleHostOutput,
                    output_view) || !output_view.present)
        return fail("E_BINDING_ROLE",
                    "PUBLISH member has no output slot view", error);
    const uint64_t output_base = output_view.start;
    bool found_store = false;
    for (const DmaDescriptor *descriptor : profileDescriptors(program,
                                                              instance)) {
        if (descriptor->kind != kDmaKindSTORE ||
            !descriptorRoleMatches(program, request, *descriptor,
                                   kRoleHostOutput))
            continue;
        std::vector<uint32_t> candidates;
        if (!descriptorCandidates(program, request, instance, *descriptor,
                                  kRoleHostOutput, candidates, error))
            return false;
        bool mine = false;
        for (uint32_t candidate : candidates)
            if (candidate == ordinal)
                mine = true;
        if (!mine)
            continue;
        found_store = true;
        if (endpointAllocation(program, descriptor->src) !=
            record.allocation_id)
            return fail("E_BINDING_ROLE",
                        "publish store must read the bound surrogate "
                        "allocation", error);
        const Command *command = commandOf(program, descriptor->command_id);
        if (command == nullptr ||
            !waitsFor(program, *command, record.completion_event_id))
            return fail("E_BINDING_ROLE",
                        "publish store must wait for the producer "
                        "completion", error);
        const Allocation *allocation =
            allocationOf(program, record.allocation_id);
        if (allocation == nullptr)
            return fail("E_BINDING_ROLE",
                        "publish allocation is missing", error);
        for (uint32_t row = 0; row < descriptor->rows; row++) {
            const uint64_t source_start = descriptor->src.offset_bytes +
                uint64_t(row) * descriptor->src_stride_bytes;
            const uint64_t target_start = descriptor->dst.offset_bytes +
                uint64_t(row) * descriptor->dst_stride_bytes;
            if (source_start - allocation->offset_bytes !=
                target_start - output_base)
                return fail("E_BINDING_ROLE",
                            "publish store source must match the member "
                            "output offset", error);
        }
    }
    if (!found_store)
        return fail("E_BINDING_ROLE",
                    "publish surrogate allocation has no output store",
                    error);
    return true;
}

bool verifyPublishRecord(const DecodedProgram &program,
                         const AgentRequestProfile &request,
                         const AgentInstanceProfile &instance,
                         uint32_t ordinal,
                         const AgentPublishSurrogateBinding &record,
                         MeshLoadError &error)
{
    if (record.fill_kind != kDmaFillKindAGENT_OUTPUT_SURROGATE)
        return fail("E_BINDING_ROLE",
                    "publish producer must use the serving fill", error);
    if (record.allocation_role !=
        kAgentAllocationRolePUBLISH_SURROGATE_SOURCE)
        return fail("E_BINDING_ROLE", "publish allocation role mismatch",
                    error);
    if (record.digest_source !=
        kAgentDigestSourceREQUEST_SEMANTIC_OUTPUT_DIGEST)
        return fail("E_BINDING_ROLE", "publish digest source mismatch", error);
    if (record.allocation_id == 0 || record.producer_command_id == 0 ||
        record.completion_event_id == 0)
        return fail("E_BINDING_ROLE", "publish binding ids must be nonzero",
                    error);
    const Allocation *allocation =
        allocationOf(program, record.allocation_id);
    if (allocation == nullptr)
        return fail("E_BINDING_ROLE", "publish allocation is missing", error);
    if (allocation->size_bytes !=
        instance.host_output_dma_bytes_per_member)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "publish allocation size must equal the member output "
                    "bytes", error);
    const Command *command = commandOf(program, record.producer_command_id);
    if (command == nullptr || command->opcode != kOpcodeDMA_FILL)
        return fail("E_BINDING_ROLE",
                    "publish producer must be a DMA_FILL command", error);
    const Event *event = eventOf(program, record.completion_event_id);
    bool completes = false;
    for (const auto &descriptor : program.descriptors)
        if (descriptor.command_id == command->command_id &&
            descriptor.completion_event == record.completion_event_id)
            completes = true;
    if (event == nullptr ||
        event->producer_command_id != command->command_id || !completes)
        return fail("E_BINDING_ROLE",
                    "publish completion event is not the producer signal",
                    error);
    if (command->attr_index == 0 ||
        command->attr_index > program.attrs.size())
        return fail("E_BINDING_ROLE",
                    "publish producer needs a FILL_V1 attr", error);
    const DecodedAttr &attr = program.attrs[command->attr_index - 1];
    if (attr.kind != kAttrKindFILL_V1)
        return fail("E_BINDING_ROLE",
                    "publish producer needs a FILL_V1 attr", error);
    if (std::get<FillV1>(attr.typed).pattern !=
        kDmaFillRuntimeBoundSentinel)
        return fail("E_BINDING_ROLE",
                    "publish producer must use the runtime-bound sentinel",
                    error);
    const auto writers = allocationWriters(program, record.allocation_id);
    if (writers.size() != 1 ||
        *writers.begin() != command->command_id)
        return fail("E_BINDING_ROLE",
                    "surrogate allocation must have exactly one producer",
                    error);
    std::vector<ServingInterval> producer_fill;
    for (const DmaDescriptor *descriptor : profileDescriptors(program,
                                                              instance)) {
        if (descriptor->command_id != command->command_id)
            continue;
        if (endpointAllocation(program, descriptor->dst) !=
            record.allocation_id)
            continue;
        collectRows(producer_fill, descriptor->dst.tensor_id,
                    descriptor->dst, descriptor->dst_stride_bytes,
                    descriptor->rows, descriptor->row_bytes);
    }
    if (producer_fill.empty())
        return fail("E_BINDING_ROLE",
                    "publish producer is not in the PUBLISH profile "
                    "closure", error);
    std::string reason;
    if (!coverageGaps(producer_fill, allocation->offset_bytes,
                      allocation->offset_bytes + allocation->size_bytes,
                      reason))
        return fail("E_BINDING_ROLE",
                    "publish producer must fill the bound allocation "
                    "exactly", error);
    return verifyPublishStoreChain(program, request, instance, ordinal,
                                   record, error);
}

bool verifyPublishBindings(const DecodedProgram &program,
                           MeshLoadError &error)
{
    std::set<std::pair<uint32_t, uint16_t>> seen;
    for (const auto &record : program.agent_publish_surrogate_bindings) {
        auto key = std::make_pair(record.instance_profile_id,
                                  record.member_ordinal);
        if (!seen.insert(key).second)
            return fail("E_ABI_DUPLICATE",
                        "duplicate publish surrogate binding", error);
    }
    size_t index = 0;
    for (const auto &key : seen) {
        const auto &record =
            program.agent_publish_surrogate_bindings[index++];
        if (std::make_pair(record.instance_profile_id,
                           record.member_ordinal) != key)
            return fail("E_ABI_ORDER",
                        "publish surrogate bindings must be sorted", error);
    }
    for (const auto &record : program.agent_publish_surrogate_bindings) {
        bool known = false;
        for (const auto &instance : program.agent_instance_profiles)
            if (instance.instance_profile_id == record.instance_profile_id)
                known = true;
        if (!known)
            return fail("E_REQUEST_PROFILE",
                        "publish binding references no instance profile",
                        error);
    }
    for (const auto &instance : program.agent_instance_profiles) {
        std::vector<const AgentPublishSurrogateBinding *> records;
        for (const auto &record : program.agent_publish_surrogate_bindings)
            if (record.instance_profile_id ==
                instance.instance_profile_id)
                records.push_back(&record);
        if (instance.phase != kPhasePUBLISH) {
            if (!records.empty())
                return fail("E_BINDING_ROLE",
                            "non-PUBLISH profile carries publish bindings",
                            error);
            continue;
        }
        if (records.size() != instance.member_count)
            return fail("E_BINDING_ROLE",
                        "PUBLISH binding ordinal set must be exact", error);
        for (uint16_t ordinal = 0; ordinal < instance.member_count;
             ordinal++) {
            bool found = false;
            for (const auto *record : records)
                if (record->member_ordinal == ordinal)
                    found = true;
            if (!found)
                return fail("E_BINDING_ROLE",
                            "PUBLISH profile needs one binding per member "
                            "ordinal", error);
        }
        std::set<uint32_t> allocations;
        for (const auto *record : records)
            allocations.insert(record->allocation_id);
        if (allocations.size() != records.size())
            return fail("E_BINDING_ROLE",
                        "each PUBLISH member needs its own surrogate "
                        "allocation", error);
        const AgentRequestProfile *request =
            requestOf(program, instance.request_program_id,
                      instance.request_profile_id);
        if (request == nullptr)
            return fail("E_REQUEST_PROFILE",
                        "instance references no request profile", error);
        for (const auto *record : records)
            if (!verifyPublishRecord(program, *request, instance,
                                     record->member_ordinal, *record,
                                     error))
                return false;
    }
    return true;
}

void collectRows(std::vector<ServingInterval> &out,
                 uint32_t wanted, const DmaEndpoint &endpoint,
                 uint64_t stride, uint32_t rows,
                 uint64_t row_bytes)
{
    if (wanted == 0 || endpoint.tensor_id != wanted)
        return;
    for (uint32_t index = 0; index < rows; index++) {
        const uint64_t start =
            endpoint.offset_bytes + index * stride;
        out.push_back({start, start + row_bytes});
    }
}

bool overlapGaps(const std::vector<ServingInterval> &intervals,
                 std::string &reason)
{
    std::vector<ServingInterval> ordered = intervals;
    std::sort(ordered.begin(), ordered.end(),
              [](const ServingInterval &left, const ServingInterval &right) {
                  return left.start < right.start;
              });
    bool first = true;
    uint64_t cursor = 0;
    for (const auto &item : ordered) {
        if (item.end <= item.start) {
            reason = "empty interval";
            return false;
        }
        if (!first && item.start < cursor) {
            reason = "overlap";
            return false;
        }
        cursor = item.end;
        first = false;
    }
    return true;
}

bool coverageGaps(const std::vector<ServingInterval> &intervals,
                  uint64_t start, uint64_t end, std::string &reason)
{
    std::vector<ServingInterval> ordered = intervals;
    std::sort(ordered.begin(), ordered.end(),
              [](const ServingInterval &left, const ServingInterval &right) {
                  return left.start < right.start;
              });
    uint64_t cursor = start;
    for (const auto &item : ordered) {
        if (item.start > cursor) {
            reason = "hole";
            return false;
        }
        cursor = std::max(cursor, item.end);
    }
    if (cursor < end) {
        reason = "hole at end";
        return false;
    }
    if (cursor > end) {
        reason = "range exceeds the declared bound";
        return false;
    }
    return true;
}

uint64_t intervalBytes(const std::vector<ServingInterval> &intervals)
{
    uint64_t total = 0;
    for (const auto &item : intervals)
        total += item.end - item.start;
    return total;
}

std::vector<ServingInterval> relative(
    const std::vector<ServingInterval> &intervals, uint64_t base)
{
    std::vector<ServingInterval> out;
    for (const auto &item : intervals)
        out.push_back({item.start - base, item.end - base});
    return out;
}

void requestKindTensors(const DecodedProgram &program,
                        const AgentRequestProfile &request,
                        uint32_t tensors[3])
{
    tensors[0] = tensors[1] = tensors[2] = 0;
    const Relocation *input =
        relocationOf(program, request.primary_input_symbol_id);
    const Relocation *output =
        relocationOf(program, request.primary_output_symbol_id);
    const Relocation *kv =
        relocationOf(program, request.primary_kv_symbol_id);
    if (input != nullptr)
        tensors[0] = input->tensor_id;
    if (output != nullptr)
        tensors[1] = output->tensor_id;
    if (kv != nullptr)
        tensors[2] = kv->tensor_id;
}

bool memberView(const DecodedProgram &program,
                const AgentRequestProfile &request,
                const AgentInstanceProfile &instance, uint32_t ordinal,
                int role, MemberView &out)
{
    out = MemberView{};
    const size_t index = instance.member_binding_first + ordinal;
    if (index >= program.agent_instance_member_bindings.size())
        return false;
    const auto &record = program.agent_instance_member_bindings[index];
    uint32_t symbol = 0;
    if (role == kRoleHostInput)
        symbol = record.static_input_symbol_id;
    else if (role == kRoleHostOutput)
        symbol = record.static_output_symbol_id;
    else
        symbol = record.static_kv_symbol_id;
    if (symbol == 0)
        return true;
    const Relocation *relocation = relocationOf(program, symbol);
    if (relocation == nullptr)
        return false;
    out.present = true;
    out.tensor = relocation->tensor_id;
    const uint64_t slot_base = relocation->offset_bytes;
    const uint64_t valid = uint64_t(instance.kv_tokens_before) *
                           request.kv_bytes_per_token;
    if (role == kRoleKvRead) {
        out.start = slot_base;
        out.end = slot_base + valid;
    } else if (role == kRoleKvStore) {
        out.start = slot_base + valid;
        out.end = out.start + instance.kv_write_bytes_per_member;
    } else if (role == kRoleHostInput) {
        out.start = slot_base;
        if (instance.path_kind == kPathKindKV_REUSE)
            out.start += request.input_binding_bytes -
                         request.delta_input_dma_bytes;
        out.end = out.start + instance.host_input_dma_bytes_per_member;
    } else {
        out.start = slot_base;
        out.end = slot_base + instance.host_output_dma_bytes_per_member;
    }
    return true;
}

bool sameTensorOverlap(const MemberView &left, const MemberView &right)
{
    if (!left.present || !right.present || left.tensor != right.tensor)
        return false;
    return left.start < right.end && right.start < left.end;
}

bool verifyMemberOwnership(const DecodedProgram &program,
                           const AgentRequestProfile &request,
                           const AgentInstanceProfile &instance,
                           MeshLoadError &error)
{
    if (instance.member_count < 2)
        return true;
    std::vector<std::vector<MemberView>> views(instance.member_count);
    std::vector<uint32_t> cores;
    for (uint32_t ordinal = 0; ordinal < instance.member_count; ordinal++) {
        RankVector ranks;
        uint32_t core = 0;
        if (!memberCore(program, request, instance, ordinal, ranks, core))
            return fail("E_REQUEST_PROFILE",
                        "member source core is unresolved", error);
        cores.push_back(core);
        for (int role = kRoleHostInput; role <= kRoleKvStore; role++) {
            MemberView view;
            if (!memberView(program, request, instance, ordinal, role,
                            view))
                return fail("E_BINDING_ROLE",
                            "member view is unresolved", error);
            views[ordinal].push_back(view);
        }
    }
    for (uint32_t left = 0; left < instance.member_count; left++) {
        for (uint32_t right = left + 1; right < instance.member_count;
             right++) {
            if (cores[left] != cores[right])
                continue;
            for (size_t role = 0; role < views[left].size(); role++)
                if (sameTensorOverlap(views[left][role],
                                      views[right][role]))
                    return fail("E_BINDING_ROLE",
                                "member slots alias on one core", error);
        }
    }
    return true;
}

bool verifyMemberSymbolRoles(const DecodedProgram &program,
                             const AgentRequestProfile &request,
                             const AgentInstanceProfile &instance,
                             uint32_t ordinal, MeshLoadError &error)
{
    const size_t index = instance.member_binding_first + ordinal;
    if (index >= program.agent_instance_member_bindings.size())
        return fail("E_BINDING_ROLE",
                    "member binding span out of range", error);
    const auto &record = program.agent_instance_member_bindings[index];
    const uint32_t symbols[] = {record.static_input_symbol_id,
                                record.static_output_symbol_id,
                                record.static_kv_symbol_id};
    const uint32_t primaries[] = {request.primary_input_symbol_id,
                                  request.primary_output_symbol_id,
                                  request.primary_kv_symbol_id};
    for (int kind = 0; kind < 3; kind++) {
        if (symbols[kind] == 0)
            continue;
        if (primaries[kind] == 0)
            return fail("E_BINDING_ROLE",
                        "member slot is present in a forbidden phase kind",
                        error);
        const Relocation *relocation = relocationOf(program, symbols[kind]);
        const Relocation *primary = relocationOf(program, primaries[kind]);
        if (relocation == nullptr || primary == nullptr ||
            relocation->tensor_id != primary->tensor_id)
            return fail("E_BINDING_ROLE",
                        "member slot must view the request primary tensor",
                        error);
    }
    return true;
}

bool rowsFit(const DmaEndpoint &endpoint, uint64_t stride, uint32_t rows,
             uint64_t row_bytes, const MemberView &view)
{
    if (!view.present || endpoint.tensor_id != view.tensor)
        return false;
    for (uint32_t row = 0; row < rows; row++) {
        const uint64_t start = endpoint.offset_bytes + uint64_t(row) * stride;
        if (start < view.start || start + row_bytes > view.end)
            return false;
    }
    return true;
}

bool descriptorRoleMatches(const DecodedProgram &program,
                           const AgentRequestProfile &request,
                           const DmaDescriptor &descriptor, int role)
{
    const bool read = role == kRoleHostInput || role == kRoleKvRead;
    if (read && descriptor.kind != kDmaKindLOAD)
        return false;
    if (!read && descriptor.kind != kDmaKindSTORE)
        return false;
    uint32_t kind_tensors[3];
    requestKindTensors(program, request, kind_tensors);
    const uint32_t wanted = role == kRoleKvRead || role == kRoleKvStore
                                ? kind_tensors[2] : kind_tensors[role];
    if (wanted == 0)
        return false;
    const DmaEndpoint &endpoint =
        read ? descriptor.src : descriptor.dst;
    return endpoint.tensor_id == wanted;
}

bool descriptorCandidates(const DecodedProgram &program,
                          const AgentRequestProfile &request,
                          const AgentInstanceProfile &instance,
                          const DmaDescriptor &descriptor, int role,
                          std::vector<uint32_t> &out, MeshLoadError &error)
{
    out.clear();
    if (!descriptorRoleMatches(program, request, descriptor, role))
        return true;
    const bool read = role == kRoleHostInput || role == kRoleKvRead;
    const DmaEndpoint &endpoint = read ? descriptor.src : descriptor.dst;
    const uint64_t stride = read ? descriptor.src_stride_bytes
                                 : descriptor.dst_stride_bytes;
    for (uint32_t ordinal = 0; ordinal < instance.member_count; ordinal++) {
        RankVector ranks;
        uint32_t core = 0;
        if (!memberCore(program, request, instance, ordinal, ranks, core))
            return fail("E_REQUEST_PROFILE",
                        "member source core is unresolved", error);
        if (descriptor.owner_core != core)
            continue;
        MemberView view;
        if (!memberView(program, request, instance, ordinal, role, view))
            return fail("E_BINDING_ROLE", "member view is unresolved",
                        error);
        if (rowsFit(endpoint, stride, descriptor.rows, descriptor.row_bytes,
                    view))
            out.push_back(ordinal);
    }
    return true;
}

bool descriptorAttributedTo(const DecodedProgram &program,
                            const AgentRequestProfile &request,
                            const AgentInstanceProfile &instance,
                            const DmaDescriptor &descriptor, int role,
                            uint32_t ordinal, MeshLoadError &error)
{
    std::vector<uint32_t> candidates;
    if (!descriptorCandidates(program, request, instance, descriptor, role,
                              candidates, error))
        return false;
    for (uint32_t candidate : candidates)
        if (candidate == ordinal)
            return true;
    return false;
}

bool verifyDescriptorAttribution(const DecodedProgram &program,
                                 const AgentRequestProfile &request,
                                 const AgentInstanceProfile &instance,
                                 MeshLoadError &error)
{
    const int roles[] = {kRoleHostInput, kRoleHostOutput, kRoleKvRead,
                         kRoleKvStore};
    for (const DmaDescriptor *descriptor : profileDescriptors(program,
                                                              instance)) {
        for (int role : roles) {
            if (!descriptorRoleMatches(program, request, *descriptor, role))
                continue;
            std::vector<uint32_t> candidates;
            if (!descriptorCandidates(program, request, instance,
                                      *descriptor, role, candidates, error))
                return false;
            if (candidates.size() != 1)
                return fail("E_BINDING_ROLE",
                            "descriptor must belong to exactly one member "
                            "slot", error);
        }
    }
    return true;
}

bool verifyMemberIo(const DecodedProgram &program,
                    const AgentRequestProfile &request,
                    const AgentInstanceProfile &instance, uint16_t ordinal,
                    MeshLoadError &error)
{
    RankVector ranks;
    uint32_t core = 0;
    if (!memberCore(program, request, instance, ordinal, ranks, core))
        return fail("E_REQUEST_PROFILE", "member source core is unresolved",
                    error);
    (void)core;
    uint32_t tensors[3];
    requestKindTensors(program, request, tensors);
    if (!verifyMemberSymbolRoles(program, request, instance, ordinal, error))
        return false;
    MemberView input_window, output_window, kv_window;
    if (!memberView(program, request, instance, ordinal, kRoleHostInput,
                    input_window) ||
        !memberView(program, request, instance, ordinal, kRoleHostOutput,
                    output_window) ||
        !memberView(program, request, instance, ordinal, kRoleKvRead,
                    kv_window))
        return fail("E_BINDING_ROLE", "member view is unresolved", error);
    const Relocation *input = input_window.present
        ? relocationOf(program, program.agent_instance_member_bindings[
              instance.member_binding_first + ordinal].static_input_symbol_id)
        : nullptr;
    const Relocation *output = output_window.present
        ? relocationOf(program, program.agent_instance_member_bindings[
              instance.member_binding_first + ordinal].static_output_symbol_id)
        : nullptr;
    const Relocation *kv = kv_window.present
        ? relocationOf(program, program.agent_instance_member_bindings[
              instance.member_binding_first + ordinal].static_kv_symbol_id)
        : nullptr;
    const uint64_t input_base = input ? input->offset_bytes : 0;
    const uint64_t output_base = output ? output->offset_bytes : 0;
    const uint64_t kv_base = kv ? kv->offset_bytes : 0;
    std::vector<ServingInterval> reads, writes, kv_reads, kv_writes;
    for (const DmaDescriptor *descriptor : profileDescriptors(program,
                                                              instance)) {
        if (descriptor->owner_core != core)
            continue;
        if (descriptor->rows == 0 ||
            descriptor->useful_bytes !=
                uint64_t(descriptor->rows) * descriptor->row_bytes)
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "descriptor row geometry is inconsistent", error);
        if (descriptor->kind == kDmaKindLOAD) {
            if (descriptorAttributedTo(program, request, instance,
                                       *descriptor, kRoleHostInput, ordinal,
                                       error))
                collectRows(reads, tensors[0], descriptor->src,
                            descriptor->src_stride_bytes, descriptor->rows,
                            descriptor->row_bytes);
            if (descriptorAttributedTo(program, request, instance,
                                       *descriptor, kRoleKvRead, ordinal,
                                       error))
                collectRows(kv_reads, tensors[2], descriptor->src,
                            descriptor->src_stride_bytes, descriptor->rows,
                            descriptor->row_bytes);
        } else if (descriptor->kind == kDmaKindSTORE) {
            if (descriptorAttributedTo(program, request, instance,
                                       *descriptor, kRoleHostOutput, ordinal,
                                       error))
                collectRows(writes, tensors[1], descriptor->dst,
                            descriptor->dst_stride_bytes, descriptor->rows,
                            descriptor->row_bytes);
            if (descriptorAttributedTo(program, request, instance,
                                       *descriptor, kRoleKvStore, ordinal,
                                       error))
                collectRows(kv_writes, tensors[2], descriptor->dst,
                            descriptor->dst_stride_bytes, descriptor->rows,
                            descriptor->row_bytes);
        }
    }
    const std::vector<ServingInterval> rel_reads = relative(reads, input_base);
    const std::vector<ServingInterval> rel_writes =
        relative(writes, output_base);
    const std::vector<ServingInterval> rel_kv_reads =
        relative(kv_reads, kv_base);
    const std::vector<ServingInterval> rel_kv_writes =
        relative(kv_writes, kv_base);
    std::string reason;
    if (!overlapGaps(rel_reads, reason) ||
        !overlapGaps(rel_writes, reason) ||
        !overlapGaps(rel_kv_reads, reason) ||
        !overlapGaps(rel_kv_writes, reason))
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "serving interval overlap", error);
    if (intervalBytes(rel_reads) != instance.host_input_dma_bytes_per_member)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "host input load bytes do not match the record", error);
    if (intervalBytes(rel_writes) != instance.host_output_dma_bytes_per_member)
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "host output store bytes do not match the record", error);
    if (intervalBytes(rel_kv_reads) != instance.kv_read_bytes_per_member)
        return fail("E_KV_TOKEN_MISMATCH",
                    "kv read bytes do not match the record", error);
    if (intervalBytes(rel_kv_writes) != instance.kv_write_bytes_per_member)
        return fail("E_KV_TOKEN_MISMATCH",
                    "kv write bytes do not match the record", error);
    if (instance.phase == kPhasePUBLISH) {
        if (!rel_reads.empty() || !rel_kv_reads.empty() ||
            !rel_kv_writes.empty())
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "PUBLISH instance must not read Host/KV", error);
        if (rel_writes.empty())
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "PUBLISH instance must store host output", error);
        if (!coverageGaps(rel_writes, 0, request.host_output_bytes, reason))
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "host output interval union is not exact", error);
        std::vector<ServingInterval> ordered = rel_writes;
        std::sort(ordered.begin(), ordered.end(),
                  [](const ServingInterval &left,
                     const ServingInterval &right) {
                      return left.start < right.start;
                  });
        for (size_t i = 0; i < ordered.size(); i++) {
            const uint64_t size = ordered[i].end - ordered[i].start;
            const bool last = i + 1 == ordered.size();
            if (size > request.publish_chunk_bytes ||
                (!last && size != request.publish_chunk_bytes))
                return fail("E_OUTPUT_CHUNK_MISMATCH",
                            "publish store does not follow "
                            "publish_chunk_bytes", error);
        }
        return true;
    }
    if (!rel_writes.empty())
        return fail("E_HOST_IO_SIZE_MISMATCH",
                    "non-PUBLISH instance must not store Host output", error);
    if (instance.phase == kPhaseDECODE) {
        if (!rel_reads.empty())
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "DECODE instance must not load host input", error);
    } else {
        if (rel_reads.empty())
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "PREFILL instance must load host input", error);
        const uint64_t start =
            instance.path_kind == kPathKindKV_REUSE
                ? request.full_input_dma_bytes - request.delta_input_dma_bytes
                : 0;
        if (!coverageGaps(rel_reads, start, request.full_input_dma_bytes,
                          reason))
            return fail("E_HOST_IO_SIZE_MISMATCH",
                        "host input interval union is not exact", error);
    }
    const uint64_t valid =
        uint64_t(instance.kv_tokens_before) * request.kv_bytes_per_token;
    for (const auto &item : rel_kv_reads)
        if (item.end > valid)
            return fail("E_KV_TOKEN_MISMATCH",
                        "kv read leaves the start valid prefix", error);
    const uint64_t append = instance.kv_write_bytes_per_member;
    if (append == 0) {
        if (!rel_kv_writes.empty())
            return fail("E_KV_TOKEN_MISMATCH",
                        "phase must not append KV tokens", error);
        return true;
    }
    const uint64_t base = valid;
    if (!coverageGaps(rel_kv_writes, base, base + append, reason))
        return fail("E_KV_TOKEN_MISMATCH",
                    "kv append interval union is not exact", error);
    return true;
}

bool verifyPathClosure(const DecodedProgram &program,
                       const AgentRequestProfile &request,
                       MeshLoadError &error)
{
    std::set<ServingSelector> selectors;
    for (const auto &instance : program.agent_instance_profiles) {
        RankVector ranks;
        if (!rankVectorOf(program, instance, ranks))
            return fail("E_BINDING_ROLE",
                        "member binding span out of range", error);
        ServingSelector key{instance.request_program_id,
                            instance.request_profile_id, instance.path_kind,
                            instance.phase, instance.member_count,
                            instance.valid_tokens_per_member,
                            instance.kv_tokens_before,
                            instance.decode_chunk_tokens, ranks};
        if (!selectors.insert(key).second)
            return fail("E_ABI_DUPLICATE", "duplicate instance selector",
                        error);
    }
    const uint16_t paths[] = {kPathKindINITIAL_PREFILL,
                              kPathKindKV_REUSE, kPathKindREPREFILL};
    for (uint16_t path_kind : paths) {
        if ((request.path_mask & (1u << path_kind)) == 0)
            continue;
        const uint32_t valid =
            path_kind == kPathKindKV_REUSE ? request.delta_input_tokens
                                           : request.full_input_tokens;
        const uint32_t kv_before =
            path_kind == kPathKindKV_REUSE
                ? request.expected_cached_tokens : 0;
        bool singleton = false;
        std::set<RankVector> rank_vectors;
        for (uint32_t rank = 0; rank < request.source_rank_count; rank++)
            rank_vectors.insert(RankVector{rank});
        for (const auto &instance : program.agent_instance_profiles) {
            if (instance.request_program_id != request.program_id ||
                instance.request_profile_id != request.profile_id ||
                instance.path_kind != path_kind)
                continue;
            RankVector ranks;
            if (!rankVectorOf(program, instance, ranks))
                return fail("E_BINDING_ROLE",
                            "member binding span out of range", error);
            rank_vectors.insert(ranks);
            if (instance.phase != kPhasePREFILL ||
                instance.member_count != 1)
                continue;
            singleton = true;
            if (instance.valid_tokens_per_member != valid ||
                instance.kv_tokens_before != kv_before)
                return fail("E_REQUEST_PROFILE",
                            "singleton PREFILL does not match the path "
                            "formula", error);
        }
        if (!singleton)
            return fail("E_REQUEST_PROFILE",
                        "no singleton PREFILL profile for the path", error);
        for (const auto &ranks : rank_vectors) {
            RankVector expected_ranks = ranks;
            if (!selectors.count(
                    {request.program_id, request.profile_id, path_kind,
                     kPhasePREFILL, static_cast<uint16_t>(ranks.size()),
                     valid, kv_before, 0, expected_ranks}))
                return fail("E_REQUEST_PROFILE",
                            "missing PREFILL profile for a reachable rank",
                            error);
            uint16_t chunk = 0;
            for (const auto &instance : program.agent_instance_profiles) {
                if (instance.request_program_id != request.program_id ||
                    instance.request_profile_id != request.profile_id ||
                    instance.path_kind != path_kind ||
                    instance.phase != kPhaseDECODE)
                    continue;
                RankVector candidate;
                if (!rankVectorOf(program, instance, candidate))
                    return fail("E_BINDING_ROLE",
                                "member binding span out of range", error);
                if (candidate == ranks)
                    chunk = std::max<uint16_t>(
                        chunk, instance.decode_chunk_tokens);
            }
            if (request.output_tokens != 0 && chunk == 0)
                return fail("E_REQUEST_PROFILE",
                            "no DECODE profile for the path and rank", error);
            uint32_t produced = 0;
            while (produced < request.output_tokens) {
                const uint32_t step =
                    std::min<uint32_t>(chunk,
                                       request.output_tokens - produced);
                if (!selectors.count(
                        {request.program_id, request.profile_id, path_kind,
                         kPhaseDECODE,
                         static_cast<uint16_t>(ranks.size()),
                         static_cast<uint16_t>(step),
                         request.full_input_tokens + produced,
                         static_cast<uint16_t>(step), ranks}))
                    return fail("E_REQUEST_PROFILE",
                                "missing DECODE chunk profile", error);
                produced += step;
            }
            if (!selectors.count(
                    {request.program_id, request.profile_id, path_kind,
                     kPhasePUBLISH, static_cast<uint16_t>(ranks.size()), 0,
                     request.full_input_tokens + request.output_tokens, 0,
                     ranks}))
                return fail("E_REQUEST_PROFILE",
                            "missing PUBLISH instance profile", error);
        }
    }
    return true;
}

bool verifyServingClosureAndIo(const DecodedProgram &program,
                               MeshLoadError &error)
{
    for (const auto &request : program.agent_request_profiles) {
        if (!verifyPathClosure(program, request, error))
            return false;
        for (const auto &instance : program.agent_instance_profiles) {
            if (instance.request_program_id != request.program_id ||
                instance.request_profile_id != request.profile_id)
                continue;
            if (!verifyMemberOwnership(program, request, instance, error))
                return false;
            if (!verifyDescriptorAttribution(program, request, instance,
                                             error))
                return false;
            for (uint16_t ordinal = 0; ordinal < instance.member_count;
                 ordinal++)
                if (!verifyMemberIo(program, request, instance, ordinal,
                                    error))
                    return false;
        }
    }
    return true;
}

uint16_t phaseRoleCount(uint16_t phase)
{
    switch (phase) {
    case kPhasePREFILL: return 2;
    case kPhaseDECODE: return 1;
    case kPhasePUBLISH: return 1;
    default: return 0;
    }
}

bool servingCapacityFor(const DecodedProgram &program,
                        const AgentInstanceProfile &instance,
                        ServingCapacityRequirement &out, MeshLoadError &error)
{
    uint64_t weights = 0;
    for (const auto &requirement :
         program.agent_request_binding_requirements) {
        if (requirement.request_program_id != instance.request_program_id ||
            requirement.request_profile_id != instance.request_profile_id)
            continue;
        if (requirement.binding_kind ==
            ::gem5::ai_mesh::agent_abi::kBindingKindWEIGHT_EXTERNAL)
            weights++;
    }
    const uint16_t roles = phaseRoleCount(instance.phase);
    if (roles == 0)
        return fail("E_CAPACITY_PLAN", "unknown instance phase", error);
    const uint64_t members = instance.member_count;
    out.batch_weight_binding_entries = std::max(
        out.batch_weight_binding_entries, weights);
    out.instance_member_binding_entries = std::max(
        out.instance_member_binding_entries, members * roles);
    out.batch_interval_entries = std::max(
        out.batch_interval_entries, 4 * members + weights);
    return true;
}

} // anonymous namespace

bool servingCapacityRequired(const DecodedProgram &program,
                             ServingCapacityRequirement &out,
                             MeshLoadError &error)
{
    if ((program.required_features & kFeatureAgentServingV1) == 0)
        return true;
    if (program.agent_instance_profiles.empty())
        return fail("E_CAPACITY_PLAN",
                    "serving program has no reachable profile", error);
    for (const auto &instance : program.agent_instance_profiles)
        if (!servingCapacityFor(program, instance, out, error))
            return false;
    return true;
}

bool verifyServingV1(const DecodedProgram &program, const RuntimeArch &arch,
                     MeshLoadError &error)
{
    if ((program.required_features & ~kKnownFeatureMask) != 0)
        return fail("E_ABI_FEATURE", "unknown required feature bits", error);
    if ((program.required_features & kFeatureAgentServingV1) == 0) {
        if (anyServingTable(program))
            return fail("E_ABI_FEATURE",
                        "Agent serving sections without the feature bit",
                        error);
        return true;
    }
    if (program.agent_request_profiles.empty() ||
        program.agent_instance_profiles.empty() ||
        program.agent_source_core_map.empty() ||
        program.agent_instance_member_bindings.empty() ||
        program.agent_request_binding_requirements.empty())
        return fail("E_ABI_BOUNDS",
                    "Agent serving feature requires every conditional "
                    "section", error);
    return verifyRequestProfileKeys(program, error) &&
           verifyRequestProfiles(program, arch, error) &&
           verifyInstanceProfiles(program, error) &&
           verifySourceCoreMap(program, arch, error) &&
           verifyMemberBindings(program, error) &&
           verifyRequirements(program, error) &&
           verifySymbolClassification(program, error) &&
           verifyPublishBindings(program, error) &&
           verifyServingClosureAndIo(program, error);
}

} // namespace ai_mesh
} // namespace gem5

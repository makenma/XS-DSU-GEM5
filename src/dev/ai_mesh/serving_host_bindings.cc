#include "dev/ai_mesh/serving_host_bindings.hh"

#include <algorithm>

namespace gem5
{
namespace ai_mesh
{

uint16_t
exactBindingFlags(uint16_t kind)
{
    switch (kind) {
      case agent_abi::kBindingKindHOST_INPUT:
        return agent_abi::kBindingFlagsREAD;
      case agent_abi::kBindingKindHOST_OUTPUT:
        return agent_abi::kBindingFlagsWRITE;
      case agent_abi::kBindingKindKV_EXTERNAL:
        return agent_abi::kBindingFlagsREAD | agent_abi::kBindingFlagsWRITE |
            agent_abi::kBindingFlagsPERSISTENT |
            agent_abi::kBindingFlagsRESOLVE_BY_HANDLE;
      case agent_abi::kBindingKindWEIGHT_EXTERNAL:
        return agent_abi::kBindingFlagsREAD | agent_abi::kBindingFlagsPERSISTENT;
      default:
        return 0;
    }
}

namespace
{

struct RequestRange
{
    uint64_t begin;
    uint64_t end;
};

enum class RangeCollectResult
{
    Collected,
    ZeroAddress,
    Overflow,
};

RangeCollectResult
aliasRange(uint64_t address, uint64_t bytes,
           std::vector<RequestRange> &ranges)
{
    if (bytes == 0)
        return RangeCollectResult::Collected;
    if (address > UINT64_MAX - bytes)
        return RangeCollectResult::Overflow;
    if (address == 0)
        return RangeCollectResult::ZeroAddress;
    ranges.push_back({address, address + bytes});
    return RangeCollectResult::Collected;
}

HostBindingVerdict
noteRangeCollect(RangeCollectResult result, std::string &reason)
{
    switch (result) {
      case RangeCollectResult::ZeroAddress:
        reason = "E_BINDING_ROLE: a request range address must be nonzero";
        return HostBindingVerdict::Mismatch;
      case RangeCollectResult::Overflow:
        reason = "E_BINDING_ALIAS_MISMATCH: a request range overflows the "
                 "address space";
        return HostBindingVerdict::AliasMismatch;
      case RangeCollectResult::Collected:
        return HostBindingVerdict::Match;
    }
    return HostBindingVerdict::Match;
}

HostBindingVerdict
collectHeaderRanges(uint64_t inputAddress, uint64_t inputBytes,
                    uint64_t outputAddress, uint64_t outputBytes,
                    uint64_t metadataAddress, uint64_t metadataBytes,
                    uint64_t kvRegionAddress, uint64_t kvRegionBytes,
                    std::vector<RequestRange> &ranges, std::string &reason)
{
    const std::pair<uint64_t, uint64_t> fields[] = {
        {inputAddress, inputBytes}, {outputAddress, outputBytes},
        {metadataAddress, metadataBytes}, {kvRegionAddress, kvRegionBytes}};
    for (const auto &field : fields) {
        const HostBindingVerdict verdict =
            noteRangeCollect(aliasRange(field.first, field.second, ranges),
                             reason);
        if (verdict != HostBindingVerdict::Match)
            return verdict;
    }
    return HostBindingVerdict::Match;
}

HostBindingVerdict
checkRequestRangeAliases(const std::vector<RequestRange> &ranges,
                         std::string &reason)
{
    for (size_t left = 0; left < ranges.size(); ++left)
        for (size_t right = left + 1; right < ranges.size(); ++right)
            if (ranges[left].begin < ranges[right].end &&
                    ranges[right].begin < ranges[left].end) {
                reason = "E_BINDING_ALIAS_MISMATCH: request ranges alias "
                         "without a proven read-only alias declaration";
                return HostBindingVerdict::AliasMismatch;
            }
    return HostBindingVerdict::Match;
}

}

HostBindingVerdict
verifyHostBindingRequest(
    const std::vector<agent_abi::BindingRecord> &bindings,
    const AgentHostBindingPlan &plan, uint64_t inputAddress,
    uint64_t inputBytes, uint64_t outputAddress, uint64_t outputBytes,
    uint64_t metadataAddress, uint64_t metadataBytes,
    uint64_t kvRegionAddress, uint64_t kvRegionBytes,
    uint64_t kvSessionSlotBytes, std::string &reason)
{
    if (bindings.empty())
        return HostBindingVerdict::Absent;
    if (bindings.size() != plan.requirements.size()) {
        reason = "binding table does not match the selected profile size";
        return HostBindingVerdict::Mismatch;
    }
    for (size_t index = 0; index < bindings.size(); ++index) {
        const agent_abi::BindingRecord &binding = bindings[index];
        const AgentHostBindingRequirement &requirement =
            plan.requirements[index];
        if (binding.symbol_id != requirement.symbolId) {
            reason = "binding table symbol set differs from the profile";
            return HostBindingVerdict::Mismatch;
        }
        if (binding.kind != requirement.kind ||
                binding.flags != requirement.flags) {
            reason = "binding table role or permission differs from the profile";
            return HostBindingVerdict::Mismatch;
        }
        if (binding.flags != exactBindingFlags(binding.kind)) {
            reason = "binding permission is not the exact combination";
            return HostBindingVerdict::Mismatch;
        }
        if (binding.bytes == 0) {
            reason = "binding range must be nonzero";
            return HostBindingVerdict::Mismatch;
        }
        switch (binding.kind) {
          case agent_abi::kBindingKindHOST_INPUT:
            if (binding.address != inputAddress ||
                    binding.bytes != inputBytes) {
                reason = "HOST_INPUT binding differs from the request header";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindHOST_OUTPUT:
            if (binding.address != outputAddress ||
                    binding.bytes != outputBytes) {
                reason = "HOST_OUTPUT binding differs from the request header";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindKV_EXTERNAL:
            if (binding.address != 0 || binding.bytes != kvSessionSlotBytes) {
                reason = "KV binding must resolve by handle";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindWEIGHT_EXTERNAL:
            if (binding.address != requirement.platformAddress ||
                    binding.bytes != requirement.platformBytes) {
                reason = "WEIGHT binding differs from the platform range";
                return HostBindingVerdict::Mismatch;
            }
            break;
          default:
            reason = "binding uses an unknown kind";
            return HostBindingVerdict::Mismatch;
        }
    }
    const std::pair<uint16_t, uint32_t> primaries[] = {
        {agent_abi::kBindingKindHOST_INPUT, plan.primaryInputSymbolId},
        {agent_abi::kBindingKindHOST_OUTPUT, plan.primaryOutputSymbolId},
        {agent_abi::kBindingKindKV_EXTERNAL, plan.primaryKvSymbolId}};
    for (const auto &primary : primaries) {
        if (primary.second == 0)
            continue;
        size_t matches = 0;
        for (const agent_abi::BindingRecord &binding : bindings)
            if (binding.kind == primary.first)
                ++matches;
        if (matches != 1) {
            reason = "a primary binding kind must appear exactly once";
            return HostBindingVerdict::Mismatch;
        }
        for (const agent_abi::BindingRecord &binding : bindings)
            if (binding.kind == primary.first &&
                    binding.symbol_id != primary.second) {
                reason = "primary binding symbol is replaced";
                return HostBindingVerdict::Mismatch;
            }
    }
    std::vector<RequestRange> ranges;
    for (const AgentHostBindingRequirement &requirement : plan.requirements)
        if (requirement.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
            const HostBindingVerdict verdict = noteRangeCollect(
                aliasRange(requirement.platformAddress,
                           requirement.platformBytes, ranges), reason);
            if (verdict != HostBindingVerdict::Match)
                return verdict;
        }
    const HostBindingVerdict headerVerdict = collectHeaderRanges(
        inputAddress, inputBytes, outputAddress, outputBytes,
        metadataAddress, metadataBytes, kvRegionAddress, kvRegionBytes,
        ranges, reason);
    if (headerVerdict != HostBindingVerdict::Match)
        return headerVerdict;
    return checkRequestRangeAliases(ranges, reason);
}

HostBindingVerdict
verifyHostBindingRequirements(
    const std::vector<agent_abi::BindingRecord> &bindings,
    const std::vector<mesh_abi::AgentRequestBindingRequirement> &requirements,
    uint64_t inputAddress, uint64_t inputBytes, uint64_t outputAddress,
    uint64_t outputBytes, uint64_t metadataAddress, uint64_t metadataBytes,
    uint64_t kvRegionAddress, uint64_t kvRegionBytes,
    uint64_t kvSessionSlotBytes, std::string &reason)
{
    if (bindings.empty())
        return HostBindingVerdict::Absent;
    if (bindings.size() != requirements.size()) {
        reason = "E_BINDING_ROLE: binding table does not match the selected "
                "profile size";
        return HostBindingVerdict::Mismatch;
    }
    std::vector<mesh_abi::AgentRequestBindingRequirement> ordered = requirements;
    std::sort(ordered.begin(), ordered.end(),
              [](const mesh_abi::AgentRequestBindingRequirement &lhs,
                 const mesh_abi::AgentRequestBindingRequirement &rhs) {
                  return lhs.symbol_id < rhs.symbol_id;
              });
    for (size_t index = 0; index < bindings.size(); ++index) {
        const agent_abi::BindingRecord &binding = bindings[index];
        const mesh_abi::AgentRequestBindingRequirement &requirement =
            ordered[index];
        if (binding.symbol_id != requirement.symbol_id ||
                binding.kind != requirement.binding_kind ||
                binding.flags != requirement.binding_flags ||
                binding.flags != exactBindingFlags(binding.kind)) {
            reason = "E_BINDING_ROLE: binding table differs from the selected "
                "profile";
            return HostBindingVerdict::Mismatch;
        }
        if (binding.bytes == 0) {
            reason = "E_BINDING_ROLE: binding range must be nonzero";
            return HostBindingVerdict::Mismatch;
        }
        switch (binding.kind) {
          case agent_abi::kBindingKindKV_EXTERNAL:
            if (binding.address != 0 || binding.bytes != kvSessionSlotBytes) {
                reason = "E_BINDING_ROLE: KV binding must resolve by handle";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindHOST_INPUT:
            if (binding.address != inputAddress ||
                    binding.bytes != inputBytes) {
                reason = "E_BINDING_ROLE: HOST_INPUT binding differs from "
                         "the request header";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindHOST_OUTPUT:
            if (binding.address != outputAddress ||
                    binding.bytes != outputBytes) {
                reason = "E_BINDING_ROLE: HOST_OUTPUT binding differs from "
                         "the request header";
                return HostBindingVerdict::Mismatch;
            }
            break;
          case agent_abi::kBindingKindWEIGHT_EXTERNAL:
            break;
          default:
            reason = "E_BINDING_ROLE: binding uses an unknown kind";
            return HostBindingVerdict::Mismatch;
        }
    }
    std::vector<RequestRange> ranges;
    for (const agent_abi::BindingRecord &binding : bindings)
        if (binding.kind == agent_abi::kBindingKindWEIGHT_EXTERNAL) {
            const HostBindingVerdict verdict =
                noteRangeCollect(aliasRange(binding.address, binding.bytes,
                                            ranges), reason);
            if (verdict != HostBindingVerdict::Match)
                return verdict;
        }
    const HostBindingVerdict headerVerdict = collectHeaderRanges(
        inputAddress, inputBytes, outputAddress, outputBytes,
        metadataAddress, metadataBytes, kvRegionAddress, kvRegionBytes,
        ranges, reason);
    if (headerVerdict != HostBindingVerdict::Match)
        return headerVerdict;
    return checkRequestRangeAliases(ranges, reason);
}

std::vector<agent_abi::BindingRecord>
expectedHostBindings(
    const std::vector<mesh_abi::AgentRequestBindingRequirement> &requirements,
    uint64_t inputAddress, uint64_t inputBytes, uint64_t outputAddress,
    uint64_t outputBytes, uint64_t kvSessionSlotBytes)
{
    std::vector<mesh_abi::AgentRequestBindingRequirement> ordered = requirements;
    std::sort(ordered.begin(), ordered.end(),
              [](const mesh_abi::AgentRequestBindingRequirement &lhs,
                 const mesh_abi::AgentRequestBindingRequirement &rhs) {
                  return lhs.symbol_id < rhs.symbol_id;
              });
    std::vector<agent_abi::BindingRecord> bindings;
    bindings.reserve(ordered.size());
    for (const mesh_abi::AgentRequestBindingRequirement &requirement : ordered) {
        agent_abi::BindingRecord record;
        record.symbol_id = requirement.symbol_id;
        record.kind = requirement.binding_kind;
        record.flags = requirement.binding_flags;
        switch (record.kind) {
          case agent_abi::kBindingKindHOST_INPUT:
            record.address = inputAddress;
            record.bytes = inputBytes;
            break;
          case agent_abi::kBindingKindHOST_OUTPUT:
            record.address = outputAddress;
            record.bytes = outputBytes;
            break;
          case agent_abi::kBindingKindKV_EXTERNAL:
            record.address = 0;
            record.bytes = kvSessionSlotBytes;
            break;
          case agent_abi::kBindingKindWEIGHT_EXTERNAL:
            record.address = 0x800200000;
            record.bytes = 128;
            break;
          default:
            break;
        }
        bindings.push_back(record);
    }
    return bindings;
}

}
}

#include "dev/ai_mesh/agent_plan_codec.hh"

#include <algorithm>
#include <limits>

#include "base/logging.hh"

#include <cstring>

#include "dev/ai_mesh/agent_axi_work.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

void
pushTlvHeader(std::vector<uint8_t> &tail, uint16_t type, uint16_t flags,
              uint32_t payloadBytes)
{
    tail.push_back(static_cast<uint8_t>(type));
    tail.push_back(static_cast<uint8_t>(type >> 8));
    tail.push_back(static_cast<uint8_t>(flags));
    tail.push_back(static_cast<uint8_t>(flags >> 8));
    tail.push_back(static_cast<uint8_t>(payloadBytes));
    tail.push_back(static_cast<uint8_t>(payloadBytes >> 8));
    tail.push_back(static_cast<uint8_t>(payloadBytes >> 16));
    tail.push_back(static_cast<uint8_t>(payloadBytes >> 24));
}

void
pushTlv(std::vector<uint8_t> &tail, uint16_t type, const uint8_t *payload,
        uint32_t payloadBytes)
{
    pushTlvHeader(tail, type, agent_abi::kTlvFlagsREQUIRED, payloadBytes);
    tail.insert(tail.end(), payload, payload + payloadBytes);
}

}

uint64_t
planParameterBytes(const AgentPlanRound &round, size_t binding_count)
{
    constexpr uint64_t kInputDigestTlv = 40;
    constexpr uint64_t kOutputChunkTlv = 16;
    constexpr uint64_t kWorkloadDigestTlv = 40;
    constexpr uint64_t kDeadlineTlv = 16;
    return agent_abi::kParameterHeaderBytes + kInputDigestTlv +
        kOutputChunkTlv + kWorkloadDigestTlv +
        (round.hasDeadline ? kDeadlineTlv : 0) +
        binding_count * agent_abi::kBindingRecordBytes;
}

bool
normalizeBindings(std::vector<agent_abi::BindingRecord> &bindings,
                  std::string &reason)
{
    std::sort(bindings.begin(), bindings.end(),
              [](const agent_abi::BindingRecord &lhs,
                 const agent_abi::BindingRecord &rhs) {
                  return lhs.symbol_id < rhs.symbol_id;
              });
    for (size_t index = 1; index < bindings.size(); ++index) {
        if (bindings[index - 1].symbol_id == bindings[index].symbol_id) {
            reason = "binding table repeats symbol " +
                std::to_string(bindings[index].symbol_id);
            return false;
        }
    }
    return true;
}

bool
bindingTableRepresentable(size_t binding_count, size_t tail_bytes,
                          std::string &reason)
{
    if (binding_count > 0xFFFFu) {
        reason = "binding count exceeds the u16 wire field";
        return false;
    }
    const uint64_t limit = std::numeric_limits<uint32_t>::max();
    const uint64_t header = agent_abi::kParameterHeaderBytes;
    if (tail_bytes > limit - header) {
        reason = "parameter tail does not fit the u32 parameter size";
        return false;
    }
    if (binding_count >
            (limit - header - tail_bytes) / agent_abi::kBindingRecordBytes) {
        reason = "binding table does not fit the u32 parameter size";
        return false;
    }
    return true;
}


uint32_t
parameterBlockBytes(const AgentPlanRound &round, size_t binding_count)
{
    const uint32_t header = agent_abi::kParameterHeaderBytes;
    const uint32_t extensions = 40 + 16 + 40 + (round.hasDeadline ? 16 : 0);
    return header + extensions +
        static_cast<uint32_t>(binding_count) * agent_abi::kBindingRecordBytes;
}

std::vector<agent_abi::BindingRecord>
resolveHostBindings(const AgentHostBindingPlan &plan,
                    const PlanWireAddresses &addresses,
                    const AgentPlanRound &round, uint64_t kvSessionSlotBytes)
{
    std::vector<agent_abi::BindingRecord> bindings;
    bindings.reserve(plan.requirements.size());
    for (const AgentHostBindingRequirement &requirement : plan.requirements) {
        agent_abi::BindingRecord record;
        record.symbol_id = requirement.symbolId;
        record.kind = requirement.kind;
        record.flags = requirement.flags;
        switch (requirement.kind) {
          case agent_abi::kBindingKindHOST_INPUT:
            record.address = addresses.inputBase;
            record.bytes = round.fullContextBytes;
            break;
          case agent_abi::kBindingKindHOST_OUTPUT:
            record.address = addresses.outputBase;
            record.bytes = round.outputCapacityBytes;
            break;
          case agent_abi::kBindingKindKV_EXTERNAL:
            record.address = 0;
            record.bytes = kvSessionSlotBytes;
            break;
          case agent_abi::kBindingKindWEIGHT_EXTERNAL:
            record.address = requirement.platformAddress;
            record.bytes = requirement.platformBytes;
            break;
          default:
            fatal("binding requirement %u uses unknown kind %u",
                  requirement.symbolId, requirement.kind);
        }
        bindings.push_back(record);
    }
    return bindings;
}

std::vector<uint8_t>
buildPlanParameter(const AgentPlanRound &round, const AgentPlanTask &task,
                   uint32_t userId, const PlanWireAddresses &addresses,
                   const uint8_t workloadDigest[32],
                   uint32_t outputChunkBytes,
                   const std::vector<agent_abi::BindingRecord> &bindings)
{
    agent_abi::ParameterHeader value;
    value.magic = 0x504e4741;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.header_bytes = agent_abi::kParameterHeaderBytes;
    value.input_addr = addresses.inputBase;
    value.input_bytes = round.fullContextBytes;
    value.input_tokens = round.fullContextTokens;
    value.cached_tokens = round.cachedTokens;
    value.output_addr = addresses.outputBase;
    value.output_capacity_bytes = round.outputCapacityBytes;
    value.output_metadata_addr = addresses.metadataBase;
    value.output_metadata_capacity_bytes = round.metadataCapacityBytes;
    value.max_output_tokens = round.outputTokens;
    value.kv_handle = task.kvHandle;
    value.kv_generation = task.generation;
    value.moe_route_profile_id = 0;
    value.user_id = userId;
    value.task_seq = task.taskSeq;
    value.repair_round = round.repairRound;
    value.qos = round.qos;
    value.request_kind = static_cast<uint8_t>(
        agent_abi::SqOpcode::GENERATE);
    value.workload_plan_item_id = round.itemId;
    value.target_request_id = 0;
    value.binding_record_bytes = agent_abi::kBindingRecordBytes;
    value.requested_profile_key = round.profileKey;

    std::vector<uint8_t> tail;
    pushTlv(tail, agent_abi::kTlvTypeINPUT_DIGEST,
            round.inputDigest.data(), 32);
    if (round.hasDeadline) {
        uint8_t deadline[8];
        for (size_t index = 0; index < 8; ++index)
            deadline[index] = uint8_t(round.deadlineTick >> (8 * index));
        pushTlv(tail, agent_abi::kTlvTypeDEADLINE, deadline, 8);
    }
    uint8_t chunk[8];
    for (size_t index = 0; index < 8; ++index)
        chunk[index] = uint8_t(uint64_t(outputChunkBytes) >> (8 * index));
    pushTlv(tail, agent_abi::kTlvTypeOUTPUT_CHUNK_BYTES, chunk, 8);
    pushTlv(tail, agent_abi::kTlvTypeWORKLOAD_ID_DIGEST, workloadDigest,
            32);
    value.extension_bytes = static_cast<uint32_t>(tail.size());
    std::string bindingReason;
    if (!bindingTableRepresentable(bindings.size(), tail.size(),
                                   bindingReason))
        fatal("%s", bindingReason.c_str());
    std::vector<uint8_t> bindingBytes;
    for (const agent_abi::BindingRecord &binding : bindings) {
        const auto encoded = agent_abi::encodeBindingRecord(binding);
        bindingBytes.insert(bindingBytes.end(), encoded.begin(),
                            encoded.end());
    }
    value.binding_count = static_cast<uint32_t>(bindings.size());
    value.binding_table_offset = agent_abi::kParameterHeaderBytes;
    value.extension_offset = agent_abi::kParameterHeaderBytes +
        static_cast<uint32_t>(bindingBytes.size());
    value.total_bytes = parameterBlockBytes(round, bindings.size());
    if (value.total_bytes != agent_abi::kParameterHeaderBytes +
            static_cast<uint32_t>(bindingBytes.size()) +
            static_cast<uint32_t>(tail.size()))
        fatal("parameter length authority disagrees with the encoded parts");

    std::vector<uint8_t> data =
        toVector(agent_abi::encodeParameterHeader(value));
    data.insert(data.end(), bindingBytes.begin(), bindingBytes.end());
    data.insert(data.end(), tail.begin(), tail.end());
    const uint32_t crcOffset = agent_abi::kParameterHeaderCrc32Offset;
    std::fill(data.begin() + crcOffset, data.begin() + crcOffset + 4, 0);
    const uint32_t crc = agent_abi::crc32c(data.data(), data.size());
    for (size_t index = 0; index < 4; ++index)
        data[crcOffset + index] = static_cast<uint8_t>(crc >> (8 * index));
    return data;
}

uint16_t
kvPolicyToSqFlags(uint8_t kvPolicy)
{
    switch (kvPolicy) {
      case kAgentKvPolicyInitial:
        return 0;
      case kAgentKvPolicyRequireReuse:
        return agent_abi::kSqFlagsREQUIRE_KV_REUSE;
      case kAgentKvPolicyAllowReprefill:
        return agent_abi::kSqFlagsALLOW_REPREFILL;
      default:
        return 0xffff;
    }
}

std::vector<uint8_t>
buildControlParameter(uint8_t commandKind, uint64_t targetRequestId,
                      uint64_t kvHandle, uint32_t generation)
{
    const bool cancel = commandKind == 2;
    const uint16_t opcode = cancel ?
        agent_abi::kSqOpcodeCANCEL : agent_abi::kSqOpcodeRELEASE_SESSION;
    agent_abi::ParameterHeader value;
    value.magic = 0x504e4741;
    value.abi_major = agent_abi::kAbiMajor;
    value.abi_minor = agent_abi::kAbiMinor;
    value.header_bytes = agent_abi::kParameterHeaderBytes;
    value.request_kind = static_cast<uint8_t>(opcode);
    value.target_request_id = cancel ? targetRequestId : 0;
    value.kv_handle = cancel ? 0 : kvHandle;
    value.kv_generation = cancel ? 0 : generation;
    value.binding_table_offset = 0;
    value.binding_count = 0;
    value.binding_record_bytes = agent_abi::kBindingRecordBytes;
    value.extension_offset = 0;
    value.extension_bytes = 0;
    value.total_bytes = agent_abi::kParameterHeaderBytes;

    std::vector<uint8_t> data =
        toVector(agent_abi::encodeParameterHeader(value));
    const uint32_t crcOffset = agent_abi::kParameterHeaderCrc32Offset;
    std::fill(data.begin() + crcOffset, data.begin() + crcOffset + 4, 0);
    const uint32_t crc = agent_abi::crc32c(data.data(), data.size());
    for (size_t index = 0; index < 4; ++index)
        data[crcOffset + index] = static_cast<uint8_t>(crc >> (8 * index));
    return data;
}

}
}

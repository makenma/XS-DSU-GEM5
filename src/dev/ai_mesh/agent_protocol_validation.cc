#include "dev/ai_mesh/agent_protocol_validation.hh"

#include <limits>

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
checkedRange(uint64_t offset, uint64_t count, uint64_t width,
             uint64_t total, uint64_t &end)
{
    if (count != 0 && width > std::numeric_limits<uint64_t>::max() / count)
        return false;
    const uint64_t bytes = count * width;
    if (offset > std::numeric_limits<uint64_t>::max() - bytes)
        return false;
    end = offset + bytes;
    return offset >= agent_abi::kParameterHeaderBytes && end <= total;
}

bool
overlaps(uint64_t leftBegin, uint64_t leftEnd,
         uint64_t rightBegin, uint64_t rightEnd)
{
    return leftBegin < rightEnd && rightBegin < leftEnd;
}

}

std::optional<agent_abi::DetailCode>
gate3ParameterStructureError(const agent_abi::ParameterHeader &header,
                             uint64_t actualBytes)
{
    if (header.total_bytes != actualBytes ||
        header.total_bytes < agent_abi::kParameterHeaderBytes ||
        header.total_bytes % 8 != 0)
        return agent_abi::E_PARAMETER_LENGTH_MISMATCH;
    if (header.binding_record_bytes != agent_abi::kBindingRecordBytes)
        return agent_abi::E_REQUEST_BINDING;
    uint64_t bindingEnd = header.binding_table_offset;
    uint64_t extensionEnd = header.extension_offset;
    if (header.binding_count != 0 &&
        (header.binding_table_offset % 8 != 0 ||
         !checkedRange(header.binding_table_offset, header.binding_count,
                       header.binding_record_bytes, header.total_bytes,
                       bindingEnd)))
        return agent_abi::E_REQUEST_BINDING;
    if (header.extension_bytes != 0 &&
        (header.extension_offset % 8 != 0 ||
         !checkedRange(header.extension_offset, 1, header.extension_bytes,
                       header.total_bytes, extensionEnd)))
        return agent_abi::E_REQUEST_BINDING;
    if (header.binding_count != 0 && header.extension_bytes != 0 &&
        overlaps(header.binding_table_offset, bindingEnd,
                 header.extension_offset, extensionEnd))
        return agent_abi::E_REQUEST_BINDING;
    return std::nullopt;
}

bool
gate3MetadataHeaderValid(const agent_abi::OutputMetadata &header,
                         const Gate3MetadataExpectation &expected)
{
    const bool extended = expected.cqFlags &
        agent_abi::kCqFlagsOUTPUT_BYTES_EXTENDED;
    const bool outputMatches = extended ?
        expected.cqValue == UINT32_MAX && header.output_bytes > UINT32_MAX :
        header.output_bytes == expected.cqValue;
    const bool surrogateMatches = !expected.checkSurrogate ||
        (header.output_tokens == expected.outputTokens &&
         header.completed_instance_count ==
             expected.completedInstanceCount &&
         header.semantic_content_digest == expected.semanticDigest);
    return header.magic == 0x4f4e4741 &&
        header.abi_major == agent_abi::kAbiMajor &&
        header.abi_minor <= agent_abi::kAbiMinor &&
        header.header_bytes == agent_abi::kOutputMetadataBytes &&
        header.total_bytes >= agent_abi::kOutputMetadataBytes &&
        header.total_bytes % 8 == 0 &&
        header.total_bytes <= expected.capacity &&
        header.flags == expected.metadataFlags &&
        header.terminal_status == expected.cqStatus &&
        header.request_id == expected.requestId &&
        header.session_id == expected.sessionId &&
        header.user_id == expected.userId &&
        header.task_seq == expected.taskSequence &&
        header.repair_round == expected.repairRound &&
        header.reserved == 0 && header.reserved2 == 0 && outputMatches &&
        surrogateMatches;
}

bool
gate3MetadataRecordValid(const std::vector<uint8_t> &data,
                         const Gate3MetadataExpectation &expected)
{
    if (data.size() < agent_abi::kOutputMetadataBytes)
        return false;
    const auto header = agent_abi::decodeOutputMetadata(data.data());
    if (!gate3MetadataHeaderValid(header, expected) ||
        header.total_bytes != data.size())
        return false;
    std::vector<uint8_t> covered(data);
    agent_abi::wrU32(
        covered.data() + agent_abi::kOutputMetadataCrcFieldOffset, 0);
    if (agent_abi::crc32c(covered.data(), covered.size()) != header.crc32)
        return false;
    uint16_t previousType = 0;
    size_t offset = agent_abi::kOutputMetadataBytes;
    while (offset < data.size()) {
        if (data.size() - offset < agent_abi::kTlvHeaderBytes)
            return false;
        const auto tlv = agent_abi::decodeTlvHeader(data.data() + offset);
        if (tlv.type <= previousType ||
            (tlv.flags & ~agent_abi::kTlvFlagsREQUIRED) != 0 ||
            tlv.payload_bytes % 8 != 0 ||
            tlv.payload_bytes >
                data.size() - offset - agent_abi::kTlvHeaderBytes)
            return false;
        const bool known = tlv.type == agent_abi::kOutputTlvTypeDIAGNOSTIC ||
            tlv.type == agent_abi::kOutputTlvTypeTIMING_BREAKDOWN ||
            tlv.type == agent_abi::kOutputTlvTypeROUTE_DIGESTS;
        if ((known && tlv.payload_bytes != 64) ||
            (!known && (tlv.flags & agent_abi::kTlvFlagsREQUIRED)))
            return false;
        previousType = tlv.type;
        offset += agent_abi::kTlvHeaderBytes + tlv.payload_bytes;
    }
    return offset == data.size();
}

}
}

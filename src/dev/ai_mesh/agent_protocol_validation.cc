#include "dev/ai_mesh/agent_protocol_validation.hh"

#include <algorithm>
#include <limits>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{
std::array<uint8_t, 32>
diagnosticDigest(uint32_t kind, const std::vector<uint8_t> &wire, agent_abi::DetailCode detail)
{
    constexpr char domain[] = "AGENT_DIAGNOSTIC_V1";
    std::vector<uint8_t> source(domain, domain + sizeof(domain));
    const size_t offset = source.size();
    source.resize(offset + 8 + wire.size() + 4, 0);
    agent_abi::wrU32(source.data() + offset, kind);
    agent_abi::wrU32(source.data() + offset + 4, wire.size());
    std::copy(wire.begin(), wire.end(), source.begin() + offset + 8);
    agent_abi::wrU32(source.data() + offset + 8 + wire.size(), detail);
    return agentSha256(source);
}
}

std::optional<std::array<uint8_t, 64>>
errorSourceDiagnostic(const ErrorCandidate &candidate)
{
    const auto detail = static_cast<agent_abi::DetailCode>(candidate.code);
    const auto disposition = agent_abi::detailDispositionV1(detail);
    const auto projection = disposition ?
        agent_abi::diagnosticProjectionV1(disposition->cqStatus, detail) : std::nullopt;
    const auto &source = candidate.source;
    const auto component = agent_abi::diagnosticErrorComponentV1(source.domain, source.error_class);
    if (!projection || projection->sourceKind != agent_abi::DiagnosticSourceKindV1::ERROR_SOURCE_KEY ||
            !component || !validErrorSource(source))
        return std::nullopt;
    const auto wire = errorSourceKeyBytes(source);
    const auto digest = diagnosticDigest(agent_abi::kDiagnosticSourceKindV1ERROR_SOURCE_KEY,
        std::vector<uint8_t>(wire.begin(), wire.end()), detail);
    std::array<uint8_t, 64> result{};
    agent_abi::wrU32(result.data(), candidate.code);
    agent_abi::wrU16(result.data() + 4, static_cast<uint16_t>(*component));
    agent_abi::wrU16(result.data() + 6, source.core_id_or_ffff);
    agent_abi::wrU32(result.data() + 8, source.region_group_id);
    agent_abi::wrU32(result.data() + 12, source.region_id);
    agent_abi::wrU32(result.data() + 16, source.ordinal);
    agent_abi::wrU32(result.data() + 20, agent_abi::kDiagnosticSourceKindV1ERROR_SOURCE_KEY);
    std::copy(digest.begin(), digest.end(), result.begin() + 24);
    return result;
}

std::array<uint8_t, 64>
requestCancellationDiagnostic(uint64_t requestId, const RequestTerminalPosition &position)
{
    constexpr auto projection = agent_abi::diagnosticProjectionV1(
        agent_abi::kCqStatusCANCELLED, agent_abi::E_REQUEST_CANCELLED);
    static_assert(projection.has_value());
    std::vector<uint8_t> source(24, 0);
    agent_abi::wrU64(source.data(), requestId);
    agent_abi::wrU64(source.data() + 8, position.instance);
    agent_abi::wrU32(source.data() + 16, position.phase);
    const auto digest = diagnosticDigest(agent_abi::kDiagnosticSourceKindV1REQUEST_TERMINAL,
        source, agent_abi::E_REQUEST_CANCELLED);
    std::array<uint8_t, 64> result{};
    agent_abi::wrU32(result.data(), agent_abi::E_REQUEST_CANCELLED);
    agent_abi::wrU16(result.data() + 4, static_cast<uint16_t>(projection->component));
    agent_abi::wrU16(result.data() + 6, UINT16_MAX);
    agent_abi::wrU32(result.data() + 8, position.phase);
    agent_abi::wrU32(result.data() + 20, agent_abi::kDiagnosticSourceKindV1REQUEST_TERMINAL);
    std::copy(digest.begin(), digest.end(), result.begin() + 24);
    return result;
}

std::optional<std::array<uint8_t, 64>>
kvPolicyDiagnostic(uint64_t requestId, uint64_t sessionId, uint64_t handle,
                   uint32_t generation, agent_abi::DetailCode detail)
{
    const auto disposition = agent_abi::detailDispositionV1(detail);
    const auto projection = disposition ?
        agent_abi::diagnosticProjectionV1(disposition->cqStatus, detail) : std::nullopt;
    if (!projection || projection->sourceKind != agent_abi::DiagnosticSourceKindV1::KV_POLICY)
        return std::nullopt;
    const auto site = projection->site;
    std::vector<uint8_t> source(32, 0);
    agent_abi::wrU64(source.data(), requestId);
    agent_abi::wrU64(source.data() + 8, sessionId);
    agent_abi::wrU64(source.data() + 16, handle);
    agent_abi::wrU32(source.data() + 24, generation);
    agent_abi::wrU32(source.data() + 28, site);
    const auto digest = diagnosticDigest(agent_abi::kDiagnosticSourceKindV1KV_POLICY, source, detail);
    std::array<uint8_t, 64> result{};
    agent_abi::wrU32(result.data(), detail);
    agent_abi::wrU16(result.data() + 4, static_cast<uint16_t>(projection->component));
    agent_abi::wrU16(result.data() + 6, UINT16_MAX);
    agent_abi::wrU32(result.data() + 8, generation);
    agent_abi::wrU32(result.data() + 16, site);
    agent_abi::wrU32(result.data() + 20, agent_abi::kDiagnosticSourceKindV1KV_POLICY);
    std::copy(digest.begin(), digest.end(), result.begin() + 24);
    return result;
}

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

std::optional<agent_abi::DetailCode>
parameterTlvStructureError(const uint8_t *data, uint64_t actualBytes,
                           const agent_abi::ParameterHeader &header,
                           ParameterTlvValues &values)
{
    if (header.extension_bytes % 8 != 0 ||
            header.extension_offset + header.extension_bytes > actualBytes)
        return agent_abi::E_REQUEST_BINDING;
    size_t offset = header.extension_offset;
    const size_t end = header.extension_offset + header.extension_bytes;
    uint32_t previousType = 0;
    bool sawEntry = false;
    while (offset < end) {
        if (end - offset < 8)
            return agent_abi::E_REQUEST_BINDING;
        const uint16_t type = agent_abi::rdU16(data + offset);
        const uint16_t flags = agent_abi::rdU16(data + offset + 2);
        const uint32_t payloadBytes = agent_abi::rdU32(data + offset + 4);
        const size_t payload = offset + 8;
        const size_t entryEnd = payload + payloadBytes;
        const size_t alignedEnd = (entryEnd + 7) & ~size_t(7);
        if (entryEnd > end)
            return agent_abi::E_REQUEST_BINDING;
        for (size_t pad = entryEnd; pad < alignedEnd; ++pad)
            if (data[pad] != 0)
                return agent_abi::E_REQUEST_BINDING;
        if (sawEntry && type <= previousType)
            return agent_abi::E_REQUEST_BINDING;
        const bool required = flags == agent_abi::kTlvFlagsREQUIRED;
        if (type == agent_abi::kTlvTypeINPUT_DIGEST) {
            if (!required || payloadBytes != 32)
                return agent_abi::E_REQUEST_BINDING;
            values.inputDigest.assign(data + payload, data + payload + 32);
        } else if (type == agent_abi::kTlvTypeWORKLOAD_ID_DIGEST) {
            if (!required || payloadBytes != 32)
                return agent_abi::E_REQUEST_BINDING;
            values.workloadDigest.assign(data + payload, data + payload + 32);
        } else if (type == agent_abi::kTlvTypeDEADLINE) {
            if (!required || payloadBytes != 8)
                return agent_abi::E_REQUEST_BINDING;
            values.hasDeadline = true;
            values.deadlineTick = agent_abi::rdU64(data + payload);
        } else if (type == agent_abi::kTlvTypeOUTPUT_CHUNK_BYTES) {
            if (!required || payloadBytes != 8 ||
                    agent_abi::rdU32(data + payload + 4) != 0)
                return agent_abi::E_REQUEST_BINDING;
            values.outputChunkBytes = agent_abi::rdU32(data + payload);
        } else if (flags != 0) {
            return agent_abi::E_REQUEST_BINDING;
        } else {
            ++values.skippedOptional;
        }
        previousType = type;
        sawEntry = true;
        offset = alignedEnd;
    }
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
    if (expected.outputBytes && header.output_bytes != *expected.outputBytes)
        return false;
    if (expected.outputBytes &&
            bool(expected.cqFlags & agent_abi::kCqFlagsPARTIAL_OUTPUT) !=
                (expected.cqStatus != agent_abi::kCqStatusSUCCESS && *expected.outputBytes != 0))
        return false;
    const bool executionMatches = !expected.checkExecution ||
        (header.output_tokens == expected.outputTokens &&
         std::find(expected.completedInstanceCounts.begin(), expected.completedInstanceCounts.end(),
                   header.completed_instance_count) != expected.completedInstanceCounts.end() &&
         header.semantic_content_digest == expected.semanticDigest);
    return header.magic == 0x4f4e4741 &&
        header.abi_major == agent_abi::kAbiMajor &&
        header.abi_minor <= agent_abi::kAbiMinor &&
        header.header_bytes == agent_abi::kOutputMetadataBytes &&
        header.total_bytes >= agent_abi::kOutputMetadataBytes &&
        header.total_bytes % 8 == 0 &&
        header.total_bytes <= expected.capacity &&
        (header.flags & ~agent_abi::kMetadataFlagsSESSION_ADMITTED) == 0 &&
        (!expected.metadataFlags || header.flags == *expected.metadataFlags) &&
        header.terminal_status == expected.cqStatus &&
        header.request_id == expected.requestId &&
        header.session_id == expected.sessionId &&
        header.user_id == expected.userId &&
        header.task_seq == expected.taskSequence &&
        header.repair_round == expected.repairRound &&
        header.reserved == 0 && header.reserved2 == 0 && outputMatches &&
        executionMatches;
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
    bool diagnostic = false;
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
        if (tlv.type == agent_abi::kOutputTlvTypeDIAGNOSTIC &&
                expected.cqStatus == agent_abi::kCqStatusCANCELLED) {
            const auto canonical = requestCancellationDiagnostic(expected.requestId, expected.terminalPosition);
            if (tlv.flags != agent_abi::kTlvFlagsREQUIRED ||
                    !std::equal(canonical.begin(), canonical.end(), data.begin() + offset + 8))
                return false;
            diagnostic = true;
        }
        if (tlv.type == agent_abi::kOutputTlvTypeDIAGNOSTIC &&
                expected.cqStatus != agent_abi::kCqStatusCANCELLED) {
            const auto detail = static_cast<agent_abi::DetailCode>(agent_abi::rdU32(data.data() + offset + 8));
            const auto disposition = agent_abi::detailDispositionV1(detail);
            const auto canonical = expected.failure ? errorSourceDiagnostic(*expected.failure) :
                kvPolicyDiagnostic(expected.requestId, expected.sessionId,
                    expected.kvHandle, expected.generation, detail);
            if (!canonical || !disposition ||
                    static_cast<uint16_t>(disposition->cqStatus) != expected.cqStatus ||
                    tlv.flags != agent_abi::kTlvFlagsREQUIRED ||
                    !std::equal(canonical->begin(), canonical->end(), data.begin() + offset + 8))
                return false;
            diagnostic = true;
        }
        if (expected.cqStatus != agent_abi::kCqStatusSUCCESS &&
                tlv.type == agent_abi::kOutputTlvTypeTIMING_BREAKDOWN)
            return false;
        previousType = tlv.type;
        offset += agent_abi::kTlvHeaderBytes + tlv.payload_bytes;
    }
    return offset == data.size() &&
        (expected.cqStatus == agent_abi::kCqStatusSUCCESS || diagnostic);
}

}
}

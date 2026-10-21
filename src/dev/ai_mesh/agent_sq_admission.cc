#include "dev/ai_mesh/agent_sq_admission.hh"

#include <stdexcept>

namespace gem5::ai_mesh
{
using namespace agent_abi;

AgentSqAdmission::AgentSqAdmission(size_t limit)
    : capacity(limit), identities(std::make_unique<Identity[]>(limit))
{
    if (!limit)
        throw std::invalid_argument("SQ identity capacity must be positive");
}

std::optional<uint16_t>
AgentSqAdmission::opcode(uint64_t request_id) const
{
    for (size_t i = 0; i < count; ++i)
        if (identities[i].request == request_id)
            return identities[i].opcode;
    return std::nullopt;
}

SqAdmissionResult
AgentSqAdmission::accept(const SqDescriptor &sq)
{
    if (sq.abi_major != kAbiMajor || sq.abi_minor > kAbiMinor)
        return {E_REQUEST_BINDING, kCqFlagsSQ_ABI_ERROR | kCqFlagsDETAIL_IN_CQ};
    if (!sq.request_id || sq.completion_cookie != sq.request_id || opcode(sq.request_id))
        return {E_REQUEST_IDENTITY, kCqFlagsSQ_IDENTITY_ERROR | kCqFlagsDETAIL_IN_CQ};
    if (count == capacity)
        return {static_cast<DetailCode>(0), 0, true};
    identities[count++] = {sq.request_id, sq.opcode};
    const bool control = sq.opcode == kSqOpcodeCANCEL || sq.opcode == kSqOpcodeRELEASE_SESSION;
    const uint16_t flags = kCqFlagsDETAIL_IN_CQ | (control ? kCqFlagsCONTROL_COMMAND : 0);
    if (!sq.parameter_block_addr || sq.parameter_block_addr % 8 ||
            sq.parameter_block_addr > UINT64_MAX - sq.parameter_block_bytes)
        return {E_REQUEST_BINDING, flags};
    if (sq.parameter_block_bytes < kParameterHeaderBytes ||
            sq.parameter_block_bytes > (1u << 20) || sq.parameter_block_bytes % 8 ||
            (control && sq.parameter_block_bytes != kParameterHeaderBytes))
        return {E_PARAMETER_LENGTH_MISMATCH, flags};
    if (sq.reserved || sq.flags2 ||
            (sq.opcode != kSqOpcodeGENERATE && !control) ||
            (sq.flags & ~(kSqFlagsREQUIRE_KV_REUSE | kSqFlagsALLOW_REPREFILL | kSqFlagsBATCH_REPLAY)))
        return {E_RESERVED_FIELD, flags};
    if (control && (sq.flags || sq.qos || sq.program_id || sq.profile_id ||
            (sq.opcode == kSqOpcodeCANCEL ? sq.session_id != 0 : sq.session_id == 0)))
        return {E_RESERVED_FIELD, flags};
    return {};
}

bool controlCqValid(const CqDescriptor &cq, uint16_t opcode)
{
    if ((opcode != kSqOpcodeCANCEL && opcode != kSqOpcodeRELEASE_SESSION) ||
            !cq.request_id || cq.completion_cookie != cq.request_id)
        return false;
    if (cq.flags == (kCqFlagsCONTROL_COMMAND | kCqFlagsDETAIL_IN_CQ)) {
        const auto disposition = detailDispositionV1(static_cast<DetailCode>(cq.output_bytes_or_detail_code));
        return disposition && disposition->cqStatus == cq.status &&
            (cq.status == kCqStatusPARAM_ERROR || cq.status == kCqStatusAXI_ERROR);
    }
    if (cq.flags != kCqFlagsCONTROL_COMMAND || cq.output_bytes_or_detail_code)
        return false;
    if (cq.status == kCqStatusSUCCESS || cq.status == kCqStatusNOT_FOUND)
        return true;
    return opcode == kSqOpcodeCANCEL ? cq.status == kCqStatusALREADY_TERMINAL :
        cq.status == kCqStatusBUSY || cq.status == kCqStatusSTALE_GENERATION;
}

bool sequenceKeyedCqValid(const CqDescriptor &cq)
{
    if (cq.request_id != 0)
        return false;
    const uint16_t kind = cq.flags & ~kCqFlagsDETAIL_IN_CQ;
    if (cq.flags != (kind | kCqFlagsDETAIL_IN_CQ))
        return false;
    switch (kind) {
      case kCqFlagsSQ_SEQ_ONLY_ERROR:
        return cq.status == kCqStatusSQ_CRC && cq.output_bytes_or_detail_code == E_SQ_CRC;
      case kCqFlagsSQ_ABI_ERROR:
        return cq.status == kCqStatusPARAM_ERROR && cq.output_bytes_or_detail_code == E_REQUEST_BINDING;
      case kCqFlagsSQ_IDENTITY_ERROR:
        return cq.status == kCqStatusPARAM_ERROR && cq.output_bytes_or_detail_code == E_REQUEST_IDENTITY;
      default:
        return false;
    }
}
}

#ifndef DEV_AI_MESH_GATE3_PROTOCOL_RUNTIME_INTERNAL_HH
#define DEV_AI_MESH_GATE3_PROTOCOL_RUNTIME_INTERNAL_HH

#include <array>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#include "base/logging.hh"
#include "dev/ai_mesh/gate3_protocol_runtime.hh"

namespace gem5
{
namespace ai_mesh
{

inline constexpr uint64_t HostSqBase = 0x1000;
inline constexpr uint64_t HostParameterBase = 0x10000;
inline constexpr uint64_t HostPromptBase = 0x20000;
inline constexpr uint64_t HostOutputBase = 0x30000;
inline constexpr uint64_t HostMetadataBase = 0x40000;
inline constexpr uint64_t HostCqBase = 0x50000;
inline constexpr uint64_t HostMsiBase = 0x60000;
inline constexpr uint64_t NpuDoorbellOffset = 0x0000;
inline constexpr uint64_t NpuCqHeadAckOffset = 0x0008;
inline constexpr uint64_t AgentProxySqHeadOffset = 0x0000;
inline constexpr uint64_t AgentProxyCqTailOffset = 0x0008;
inline constexpr uint64_t NpuControlBase = 0x90000;
inline constexpr uint64_t AgentProxyControlBase = 0x91000;

inline void
activateWriteSegment(Gate3WriteWork &work,
                     const Gate3AxiTransferPlanner &planner)
{
    work.request = work.segments.at(work.segmentIndex).request;
    work.beats = planner.packWrite(work.data,
                                   work.segments.at(work.segmentIndex));
    work.semanticBytes.assign(
        work.beats.size(), uint64_t{1} << work.request.size);
    work.nextBeat = 0;
    work.awAccepted = false;
    work.txn = 0;
}

inline void
activateReadSegment(Gate3ReadWork &work)
{
    work.request = work.segments.at(work.segmentIndex).request;
    work.beatIndex = 0;
    work.accepted = false;
    work.txn = 0;
}

inline std::string
responseName(axi::AxiResp response)
{
    switch (response) {
      case axi::AxiResp::Okay:
        return "OKAY";
      case axi::AxiResp::SlvErr:
        return "SLVERR";
      case axi::AxiResp::DecErr:
        return "DECERR";
      case axi::AxiResp::ExOkay:
        return "AMBIGUOUS";
    }
    return "AMBIGUOUS";
}

inline uint64_t
axiIssueOrdinal(uint64_t transactionUid)
{
    constexpr uint64_t LocalMask = (uint64_t{1} << 39) - 1;
    const uint64_t local = transactionUid & LocalMask;
    fatal_if(local == LocalMask, "AXI transaction ordinal overflow");
    return local + 1;
}

inline Gate3PhysicalSourceTokenV1
axiSourceToken(agent_abi::FatalComponentKindV1 component, bool read,
               uint64_t transactionUid)
{
    return Gate3PhysicalSourceTokenV1{
        agent_abi::PhysicalSourceKindV1::AXI_TRANSACTION,
        static_cast<uint16_t>(read ?
            agent_abi::AxiTransactionSubkindV1::READ_AR :
            agent_abi::AxiTransactionSubkindV1::WRITE_AW_W),
        component, 0, UINT32_MAX, axiIssueOrdinal(transactionUid), 0};
}

inline Gate3PhysicalSourceTokenV1
internalSourceToken(agent_abi::FatalComponentKindV1 component)
{
    return Gate3PhysicalSourceTokenV1{
        agent_abi::PhysicalSourceKindV1::INTERNAL_EDGE, 0,
        component, 0, UINT32_MAX, UINT64_MAX, 0};
}

inline Gate3PhysicalSourceTokenV1
responseSourceToken(agent_abi::FatalComponentKindV1 component,
                    uint64_t ordinal, uint32_t axiId)
{
    fatal_if(ordinal == 0, "AXI response observation ordinal is zero");
    return Gate3PhysicalSourceTokenV1{
        agent_abi::PhysicalSourceKindV1::AXI_RESPONSE_OBSERVATION,
        static_cast<uint16_t>(
            agent_abi::AxiResponseObservationSubkindV1::B),
        component, 0, UINT32_MAX, ordinal, axiId};
}

inline uint64_t
readLe(const std::vector<uint8_t> &data, size_t offset)
{
    if (offset > data.size() || data.size() - offset < sizeof(uint64_t))
        return 0;
    uint64_t value = 0;
    for (size_t index = 0; index < sizeof(uint64_t); ++index)
        value |= uint64_t(data[offset + index]) << (index * 8);
    return value;
}

template <size_t N>
std::vector<uint8_t>
toVector(const std::array<uint8_t, N> &data)
{
    return std::vector<uint8_t>(data.begin(), data.end());
}

inline std::string
objectForControl(const std::string &control)
{
    if (control == "SQ_DOORBELL")
        return "DOORBELL";
    if (control == "SQ_HEAD_UPDATE")
        return "SQ_HEAD";
    if (control == "CQ_TAIL_UPDATE")
        return "CQ_TAIL";
    if (control == "CQ_HEAD_ACK")
        return "CQ_ACK";
    if (control == "CQ_ENTRY_READ")
        return "CQ_ENTRY";
    if (control == "METADATA_READ")
        return "METADATA";
    return control;
}

inline std::string
reverseDirection(const std::string &direction)
{
    return direction == "DRIVER_TO_NPU" ? "NPU_TO_DRIVER" :
        "DRIVER_TO_NPU";
}

inline bool
isPowerOfTwo(uint32_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

inline void
setRingIdentity(Gate3Event &event, uint32_t sqDepth, uint32_t cqDepth)
{
    if (!event.absoluteSeq)
        return;
    if (event.object == "SQ_ENTRY") {
        event.slot = *event.absoluteSeq % sqDepth;
        event.generation = *event.absoluteSeq / sqDepth;
    } else if (event.object == "CQ_ENTRY") {
        event.slot = *event.absoluteSeq % cqDepth;
        event.generation = *event.absoluteSeq / cqDepth;
    }
}

inline bool
isSuccessStatus(uint16_t status)
{
    return status == static_cast<uint16_t>(agent_abi::CqStatus::SUCCESS);
}

inline uint64_t
rangeEnd(uint64_t base, uint64_t offset)
{
    if (offset > std::numeric_limits<uint64_t>::max() - base)
        throw std::invalid_argument("Gate3 address overflows uint64");
    return base + offset;
}

}
}

#endif

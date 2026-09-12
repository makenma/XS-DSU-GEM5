#ifndef DEV_AI_MESH_AGENT_RUNTIME_TYPES_HH
#define DEV_AI_MESH_AGENT_RUNTIME_TYPES_HH

#include <cstdint>
#include <limits>
#include <optional>

namespace gem5
{
namespace ai_mesh
{

template <class Tag>
class SequenceValue
{
  public:
    constexpr explicit SequenceValue(uint64_t value = 0) : _value(value) {}

    constexpr uint64_t value() const { return _value; }

    friend constexpr bool operator==(SequenceValue lhs, SequenceValue rhs)
    { return lhs._value == rhs._value; }
    friend constexpr bool operator!=(SequenceValue lhs, SequenceValue rhs)
    { return !(lhs == rhs); }
    friend constexpr bool operator<(SequenceValue lhs, SequenceValue rhs)
    { return lhs._value < rhs._value; }
    friend constexpr bool operator<=(SequenceValue lhs, SequenceValue rhs)
    { return lhs._value <= rhs._value; }
    friend constexpr bool operator>(SequenceValue lhs, SequenceValue rhs)
    { return rhs < lhs; }
    friend constexpr bool operator>=(SequenceValue lhs, SequenceValue rhs)
    { return rhs <= lhs; }

  private:
    uint64_t _value;
};

struct SqSeqTag;
struct CqSeqTag;
struct RequestIdTag;
struct CompletionCookieTag;
struct AxiTxnUidTag;
struct CqObligationIdTag;
struct MsiIssueOrdinalTag;
struct SqIntakeIdTag;
struct HostTaskIdTag;
struct AgentObjectIdTag;

using SqSeq = SequenceValue<SqSeqTag>;
using CqSeq = SequenceValue<CqSeqTag>;
using RequestId = SequenceValue<RequestIdTag>;
using CompletionCookie = SequenceValue<CompletionCookieTag>;
using AxiTxnUid = SequenceValue<AxiTxnUidTag>;
using CqObligationId = SequenceValue<CqObligationIdTag>;
using MsiIssueOrdinal = SequenceValue<MsiIssueOrdinalTag>;
using SqIntakeId = SequenceValue<SqIntakeIdTag>;
using HostTaskId = SequenceValue<HostTaskIdTag>;
using AgentObjectId = SequenceValue<AgentObjectIdTag>;

template <class Sequence>
std::optional<Sequence>
checkedSequenceAdd(Sequence base, uint64_t delta)
{
    if (delta > std::numeric_limits<uint64_t>::max() - base.value())
        return std::nullopt;
    return Sequence(base.value() + delta);
}

}
}

#endif

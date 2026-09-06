#include "dev/ai_mesh/gate3_fatal_reducer.hh"

#include <algorithm>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <tuple>

namespace gem5
{
namespace ai_mesh
{

namespace
{

template <typename T>
void
appendLe(std::vector<uint8_t> &wire, T value)
{
    for (size_t index = 0; index < sizeof(T); ++index)
        wire.push_back(static_cast<uint8_t>(value >> (index * 8)));
}

template <typename... Values>
std::vector<uint8_t>
keyOf(Values... values)
{
    std::vector<uint8_t> result;
    (appendLe(result, values), ...);
    return result;
}

}

std::array<uint8_t, 32>
Gate3PhysicalSourceTokenV1::wire() const
{
    std::vector<uint8_t> encoded;
    encoded.reserve(32);
    appendLe(encoded, static_cast<uint16_t>(kind));
    appendLe(encoded, channelOrSubkind);
    appendLe(encoded, static_cast<uint16_t>(producerComponent));
    appendLe(encoded, uint16_t{0});
    appendLe(encoded, componentLocalId);
    appendLe(encoded, endpointId);
    appendLe(encoded, primaryOrdinal);
    appendLe(encoded, subOrdinal);
    appendLe(encoded, uint32_t{0});
    std::array<uint8_t, 32> result{};
    std::copy(encoded.begin(), encoded.end(), result.begin());
    return result;
}

std::vector<uint8_t>
Gate3FatalCandidateV1::wire() const
{
    if (objectKey.size() > std::numeric_limits<uint16_t>::max())
        throw std::length_error("fatal object key exceeds u16 length");
    std::vector<uint8_t> encoded;
    encoded.reserve(40 + objectKey.size());
    appendLe(encoded, static_cast<uint64_t>(observedTick));
    appendLe(encoded, static_cast<uint16_t>(sourceClass));
    appendLe(encoded, static_cast<uint16_t>(siteDomain));
    appendLe(encoded, siteId);
    appendLe(encoded, static_cast<uint16_t>(componentKind));
    appendLe(encoded, componentLocalId);
    appendLe(encoded, endpointId);
    appendLe(encoded, static_cast<uint16_t>(objectKind));
    appendLe(encoded, static_cast<uint16_t>(objectKey.size()));
    encoded.insert(encoded.end(), objectKey.begin(), objectKey.end());
    appendLe(encoded, issueOrdinal);
    appendLe(encoded, static_cast<uint32_t>(errorCode));
    return encoded;
}

bool
Gate3FatalCandidateV1::operator<(const Gate3FatalCandidateV1 &other) const
{
    const auto prefix = std::tuple{
        observedTick, static_cast<uint16_t>(sourceClass),
        static_cast<uint16_t>(siteDomain), siteId,
        static_cast<uint16_t>(componentKind), componentLocalId, endpointId,
        static_cast<uint16_t>(objectKind)};
    const auto otherPrefix = std::tuple{
        other.observedTick, static_cast<uint16_t>(other.sourceClass),
        static_cast<uint16_t>(other.siteDomain), other.siteId,
        static_cast<uint16_t>(other.componentKind), other.componentLocalId,
        other.endpointId, static_cast<uint16_t>(other.objectKind)};
    if (prefix != otherPrefix)
        return prefix < otherPrefix;
    if (objectKey != other.objectKey)
        return objectKey < other.objectKey;
    return std::tie(issueOrdinal, errorCode) <
        std::tie(other.issueOrdinal, other.errorCode);
}

bool
Gate3FatalReducer::stageFault(
    Tick tick, agent_abi::FaultSiteV1 site, uint32_t componentLocalId,
    uint32_t endpointId, std::vector<uint8_t> objectKey,
    uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken)
{
    const auto projection = agent_abi::fatalSiteProjectionV1(site);
    if (!projection)
        throw std::invalid_argument("reserved fatal fault site");
    return stage(Gate3FatalCandidateV1{
        tick, projection->sourceClass, agent_abi::FatalSiteDomainV1::FAULT,
        static_cast<uint16_t>(site), projection->componentKind,
        componentLocalId, endpointId, projection->objectKind,
        std::move(objectKey), issueOrdinal, projection->detailCode,
        sourceToken});
}

bool
Gate3FatalReducer::stageInvariant(
    Tick tick, agent_abi::InvariantSiteV1 site, uint32_t componentLocalId,
    uint32_t endpointId, std::vector<uint8_t> objectKey,
    Gate3PhysicalSourceTokenV1 sourceToken)
{
    const auto projection = agent_abi::invariantSiteProjectionV1(site);
    return stage(Gate3FatalCandidateV1{
        tick, projection.sourceClass, agent_abi::FatalSiteDomainV1::INVARIANT,
        static_cast<uint16_t>(site), projection.componentKind,
        componentLocalId, endpointId, projection.objectKind,
        std::move(objectKey), UINT64_MAX, projection.detailCode,
        sourceToken});
}

bool
Gate3FatalReducer::stage(Gate3FatalCandidateV1 candidate)
{
    if (reductionTick && candidate.observedTick != *reductionTick)
        return false;
    candidate.wire();
    if (!reductionTick)
        reductionTick = candidate.observedTick;
    staged.push_back(std::move(candidate));
    if (!minimum || staged.back() < *minimum)
        minimum = staged.back();
    return true;
}

const Gate3FatalCandidateV1 &
Gate3FatalReducer::first() const
{
    if (!minimum)
        throw std::logic_error("fatal reducer has no candidate");
    return *minimum;
}

std::vector<uint8_t>
gate3SqIntakeKey(uint64_t intakeId, uint64_t expectedSqSequence)
{
    return keyOf(intakeId, expectedSqSequence);
}

std::vector<uint8_t>
gate3PublicationKey(uint64_t publicationId, uint64_t baseSequence,
                    uint64_t pendingTail, uint64_t requestId)
{
    return keyOf(publicationId, baseSequence, pendingTail, requestId);
}

std::vector<uint8_t>
gate3CqEntryKey(uint64_t cqSequence, uint64_t obligationId,
                uint64_t requestId)
{
    return keyOf(cqSequence, obligationId, requestId);
}

std::vector<uint8_t>
gate3MetadataKey(uint64_t obligationId, uint64_t requestId, uint64_t address)
{
    return keyOf(obligationId, requestId, address);
}

std::vector<uint8_t>
gate3ControlKey(agent_abi::ControlUpdateKindV1 kind, uint64_t nextSequence)
{
    std::vector<uint8_t> result = keyOf(static_cast<uint16_t>(kind));
    result.resize(8, 0);
    appendLe(result, nextSequence);
    return result;
}

std::vector<uint8_t>
gate3MsiKey(uint64_t issueOrdinal, uint64_t tail, uint32_t axiId)
{
    return keyOf(issueOrdinal, tail, axiId);
}

std::vector<uint8_t>
gate3HostAckKey(uint64_t issueOrdinal, uint64_t sequence, uint32_t axiId)
{
    return keyOf(issueOrdinal, sequence, axiId);
}

std::vector<uint8_t>
gate3InternalKey(agent_abi::InvariantSiteV1 site)
{
    std::vector<uint8_t> result = keyOf(static_cast<uint32_t>(site));
    result.resize(36, 0);
    return result;
}

std::vector<uint8_t>
gate3FaultContextKey(agent_abi::FaultSiteV1 site)
{
    std::vector<uint8_t> result = keyOf(static_cast<uint16_t>(site));
    result.resize(36, 0);
    return result;
}

std::string
gate3Hex(const uint8_t *data, size_t size)
{
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (size_t index = 0; index < size; ++index)
        output << std::setw(2) << unsigned(data[index]);
    return output.str();
}

}
}

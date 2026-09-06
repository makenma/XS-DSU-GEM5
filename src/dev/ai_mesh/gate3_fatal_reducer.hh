#ifndef DEV_AI_MESH_GATE3_FATAL_REDUCER_HH
#define DEV_AI_MESH_GATE3_FATAL_REDUCER_HH

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "base/types.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

struct Gate3PhysicalSourceTokenV1
{
    agent_abi::PhysicalSourceKindV1 kind;
    uint16_t channelOrSubkind = 0;
    agent_abi::FatalComponentKindV1 producerComponent;
    uint32_t componentLocalId = 0;
    uint32_t endpointId = UINT32_MAX;
    uint64_t primaryOrdinal = UINT64_MAX;
    uint32_t subOrdinal = 0;

    std::array<uint8_t, 32> wire() const;
};

struct Gate3FatalCandidateV1
{
    Tick observedTick = 0;
    agent_abi::FatalSourceClassV1 sourceClass;
    agent_abi::FatalSiteDomainV1 siteDomain;
    uint16_t siteId = 0;
    agent_abi::FatalComponentKindV1 componentKind;
    uint32_t componentLocalId = 0;
    uint32_t endpointId = UINT32_MAX;
    agent_abi::FatalObjectKindV1 objectKind;
    std::vector<uint8_t> objectKey;
    uint64_t issueOrdinal = UINT64_MAX;
    agent_abi::DetailCode errorCode = agent_abi::E_OK;
    Gate3PhysicalSourceTokenV1 sourceToken;

    std::vector<uint8_t> wire() const;
    bool operator<(const Gate3FatalCandidateV1 &other) const;
};

class Gate3FatalReducer
{
  public:
    bool stageFault(
        Tick tick, agent_abi::FaultSiteV1 site, uint32_t componentLocalId,
        uint32_t endpointId, std::vector<uint8_t> objectKey,
        uint64_t issueOrdinal, Gate3PhysicalSourceTokenV1 sourceToken);
    bool stageInvariant(
        Tick tick, agent_abi::InvariantSiteV1 site,
        uint32_t componentLocalId, uint32_t endpointId,
        std::vector<uint8_t> objectKey,
        Gate3PhysicalSourceTokenV1 sourceToken);

    bool pending() const { return minimum.has_value(); }
    const Gate3FatalCandidateV1 &first() const;
    const std::vector<Gate3FatalCandidateV1> &candidates() const
    { return staged; }
    uint64_t candidateCount() const { return staged.size(); }

  private:
    bool stage(Gate3FatalCandidateV1 candidate);

    std::optional<Tick> reductionTick;
    std::optional<Gate3FatalCandidateV1> minimum;
    std::vector<Gate3FatalCandidateV1> staged;
};

std::vector<uint8_t> gate3SqIntakeKey(uint64_t intakeId,
                                      uint64_t expectedSqSequence);
std::vector<uint8_t> gate3PublicationKey(
    uint64_t publicationId, uint64_t baseSequence, uint64_t pendingTail,
    uint64_t requestId);
std::vector<uint8_t> gate3CqEntryKey(
    uint64_t cqSequence, uint64_t obligationId, uint64_t requestId);
std::vector<uint8_t> gate3MetadataKey(
    uint64_t obligationId, uint64_t requestId, uint64_t address);
std::vector<uint8_t> gate3ControlKey(
    agent_abi::ControlUpdateKindV1 kind, uint64_t nextSequence);
std::vector<uint8_t> gate3MsiKey(
    uint64_t issueOrdinal, uint64_t tail, uint32_t axiId);
std::vector<uint8_t> gate3HostAckKey(
    uint64_t issueOrdinal, uint64_t sequence, uint32_t axiId);
std::vector<uint8_t> gate3InternalKey(agent_abi::InvariantSiteV1 site);
std::vector<uint8_t> gate3FaultContextKey(agent_abi::FaultSiteV1 site);
std::string gate3Hex(const uint8_t *data, size_t size);

}
}

#endif

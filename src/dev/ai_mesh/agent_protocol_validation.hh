#ifndef DEV_AI_MESH_AGENT_PROTOCOL_VALIDATION_HH
#define DEV_AI_MESH_AGENT_PROTOCOL_VALIDATION_HH

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

struct Gate3MetadataExpectation
{
    uint64_t requestId = 0;
    uint64_t sessionId = 0;
    uint32_t userId = 0;
    uint32_t taskSequence = 0;
    uint16_t repairRound = 0;
    uint16_t cqStatus = 0;
    uint16_t cqFlags = 0;
    uint32_t cqValue = 0;
    uint32_t capacity = 0;
    uint32_t metadataFlags = 0;
    bool checkSurrogate = false;
    uint32_t outputTokens = 0;
    uint32_t completedInstanceCount = 0;
    std::array<uint8_t, 32> semanticDigest{};
};

std::optional<agent_abi::DetailCode> gate3ParameterStructureError(
    const agent_abi::ParameterHeader &header, uint64_t actualBytes);
bool gate3MetadataHeaderValid(
    const agent_abi::OutputMetadata &header,
    const Gate3MetadataExpectation &expected);
bool gate3MetadataRecordValid(
    const std::vector<uint8_t> &data,
    const Gate3MetadataExpectation &expected);

}
}

#endif

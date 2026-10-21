#ifndef DEV_AI_MESH_AGENT_PROTOCOL_VALIDATION_HH
#define DEV_AI_MESH_AGENT_PROTOCOL_VALIDATION_HH

#include <array>
#include <cstdint>
#include <optional>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_error_candidate.hh"

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
    std::optional<uint32_t> metadataFlags = 0;
    uint64_t kvHandle = 0;
    uint32_t generation = 0;
    std::optional<ErrorCandidate> failure = std::nullopt;
    RequestTerminalPosition terminalPosition{};
    std::optional<uint64_t> outputBytes = std::nullopt;
    bool checkExecution = false;
    uint32_t outputTokens = 0;
    std::vector<uint32_t> completedInstanceCounts{0};
    std::array<uint8_t, 32> semanticDigest{};
};

std::optional<std::array<uint8_t, 64>> errorSourceDiagnostic(const ErrorCandidate &candidate);
std::array<uint8_t, 64> requestCancellationDiagnostic(uint64_t requestId,
    const RequestTerminalPosition &position = {});
std::optional<std::array<uint8_t, 64>> kvPolicyDiagnostic(
    uint64_t requestId, uint64_t sessionId, uint64_t handle, uint32_t generation,
    agent_abi::DetailCode detail);

std::optional<agent_abi::DetailCode> gate3ParameterStructureError(
    const agent_abi::ParameterHeader &header, uint64_t actualBytes);
bool gate3MetadataHeaderValid(
    const agent_abi::OutputMetadata &header,
    const Gate3MetadataExpectation &expected);
bool gate3MetadataRecordValid(
    const std::vector<uint8_t> &data,
    const Gate3MetadataExpectation &expected);

struct ParameterTlvValues
{
    std::vector<uint8_t> inputDigest;
    std::vector<uint8_t> workloadDigest;
    uint32_t outputChunkBytes = 0;
    bool hasDeadline = false;
    uint64_t deadlineTick = 0;
    uint32_t skippedOptional = 0;
};

std::optional<agent_abi::DetailCode> parameterTlvStructureError(
    const uint8_t *data, uint64_t actualBytes,
    const agent_abi::ParameterHeader &header, ParameterTlvValues &values);

}
}

#endif

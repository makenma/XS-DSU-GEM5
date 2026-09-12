#ifndef DEV_AI_MESH_NPU_REQUEST_EXECUTOR_HH
#define DEV_AI_MESH_NPU_REQUEST_EXECUTOR_HH

#include <array>
#include <cstdint>
#include <vector>

#include "dev/ai_mesh/agent_surrogate_codec.hh"

namespace gem5
{
namespace ai_mesh
{

struct NpuExecutionRequest
{
    uint64_t sqSequence = 0;
    uint64_t requestId = 0;
    uint64_t completionCookie = 0;
    uint64_t inputAddress = 0;
    uint64_t inputBytes = 0;
    uint64_t outputAddress = 0;
    uint64_t outputCapacityBytes = 0;
    uint64_t metadataAddress = 0;
    uint32_t metadataCapacityBytes = 0;
    uint32_t inputTokens = 0;
    uint32_t maxOutputTokens = 0;
    uint32_t outputTokens = 0;
    uint64_t requestedProfileKey = 0;
    uint32_t workloadPlanItemId = 0;
    uint16_t programId = 0;
    uint16_t profileId = 0;
    uint8_t qos = 0;
    std::array<uint8_t, 32> inputDigest{};
    std::array<uint8_t, 32> workloadDigest{};
};

struct NpuExecutionOutcome
{
    uint64_t serviceNs = 0;
    bool coreStartProbe = false;
    bool admitted = false;
    uint32_t rejectDetail = 0;
};

class NpuRequestExecutor
{
  public:
    virtual ~NpuRequestExecutor() = default;
    virtual NpuExecutionOutcome accept(
        const NpuExecutionRequest &request) = 0;
    virtual bool idle() const = 0;
    virtual std::vector<uint8_t> outputPayload(
        const NpuExecutionRequest &request) const
    { return {}; }
    virtual std::array<uint8_t, 32> semanticDigest(
        const NpuExecutionRequest &request) const
    { return {}; }
    virtual uint64_t outputBytes(const NpuExecutionRequest &request) const
    { return 0; }
};

class NpuProtocolProbeExecutor final : public NpuRequestExecutor
{
  public:
    NpuExecutionOutcome accept(const NpuExecutionRequest &request) override;
    bool idle() const override { return true; }
};

class FullContextSurrogateExecutor final : public NpuRequestExecutor
{
  public:
    explicit FullContextSurrogateExecutor(SurrogateProfileRegistry registry);

    NpuExecutionOutcome accept(
        const NpuExecutionRequest &request) override;
    bool idle() const override { return true; }
    const SurrogateProfile *profileFor(
        const NpuExecutionRequest &request) const;
    std::vector<uint8_t> outputPayload(
        const NpuExecutionRequest &request) const override;
    std::array<uint8_t, 32> semanticDigest(
        const NpuExecutionRequest &request) const override;
    uint64_t outputBytes(const NpuExecutionRequest &request) const override;

  private:
    const SurrogateProfileRegistry registry;
};

}
}
#endif

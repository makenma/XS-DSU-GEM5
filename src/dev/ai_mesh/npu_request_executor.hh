#ifndef DEV_AI_MESH_NPU_REQUEST_EXECUTOR_HH
#define DEV_AI_MESH_NPU_REQUEST_EXECUTOR_HH

#include <array>
#include <cstdint>
#include <vector>

#include "dev/ai_mesh/agent_surrogate_codec.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"

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
    uint64_t sessionId = 0;
    uint64_t kvHandle = 0;
    uint32_t generation = 0;
    uint32_t pathKind = 0;
    uint32_t cachedTokens = 0;
    uint16_t kvFlags = 0;
    uint16_t repairRound = 0;
    std::array<uint8_t, 32> contractDigest{};
    uint16_t programId = 0;
    uint16_t profileId = 0;
    uint8_t qos = 0;
    std::array<uint8_t, 32> inputDigest{};
    std::array<uint8_t, 32> workloadDigest{};
    std::vector<agent_abi::BindingRecord> hostBindings;
};

enum class NpuExecutorKind
{
    ProtocolProbe,
    FullContextSurrogate,
    ServingMesh,
};

struct NpuAdmission
{
    bool admitted = false;
    uint32_t rejectDetail = 0;
};

struct NpuExecutionCompletion
{
    bool success = false;
    bool coreStarted = false;
    std::vector<uint8_t> outputPayload;
    std::array<uint8_t, 32> semanticDigest{};
    uint64_t outputBytes = 0;
    uint32_t phaseInstances = 0;
    std::optional<KvTerminalSnapshot> kvTerminal;
    uint64_t requestStartTick = 0;
    uint64_t terminalTick = 0;
};

class NpuCompletionSink
{
  public:
    virtual ~NpuCompletionSink() = default;
    virtual void onCoreStart(uint64_t requestId) = 0;
    virtual void onRequestComplete(
        const NpuExecutionRequest &request,
        const NpuExecutionCompletion &completion) = 0;
};

class NpuRequestExecutor
{
  public:
    virtual ~NpuRequestExecutor() = default;
    void attachSink(NpuCompletionSink *sink) { sink_ = sink; }
    virtual NpuExecutorKind kind() const = 0;
    virtual NpuAdmission submit(const NpuExecutionRequest &request) = 0;
    virtual uint64_t modeledServiceNs(const NpuExecutionRequest &) const
    { return 0; }
    virtual NpuExecutionCompletion completionFor(
        const NpuExecutionRequest &request) const = 0;
    void postCompletion(const NpuExecutionRequest &request) const
    {
        if (sink_ != nullptr)
            sink_->onRequestComplete(request, completionFor(request));
    }

  protected:
    NpuCompletionSink *sink_ = nullptr;
};

class NpuProtocolProbeExecutor final : public NpuRequestExecutor
{
  public:
    NpuExecutorKind kind() const override
    { return NpuExecutorKind::ProtocolProbe; }
    NpuAdmission submit(const NpuExecutionRequest &request) override;
    NpuExecutionCompletion completionFor(
        const NpuExecutionRequest &request) const override;
};

class FullContextSurrogateExecutor final : public NpuRequestExecutor
{
  public:
    explicit FullContextSurrogateExecutor(SurrogateProfileRegistry registry);

    NpuExecutorKind kind() const override
    { return NpuExecutorKind::FullContextSurrogate; }
    NpuAdmission submit(const NpuExecutionRequest &request) override;
    uint64_t modeledServiceNs(const NpuExecutionRequest &request) const
        override;
    NpuExecutionCompletion completionFor(
        const NpuExecutionRequest &request) const override;
    static const SurrogateProfile *profileFor(
        const SurrogateProfileRegistry &registry,
        const NpuExecutionRequest &request);
    static std::array<uint8_t, 32> semanticDigestFor(
        const NpuExecutionRequest &request,
        const SurrogateProfile &profile);

  private:
    const SurrogateProfileRegistry registry;
};

}
}
#endif

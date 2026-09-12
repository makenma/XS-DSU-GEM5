#ifndef DEV_AI_MESH_HOST_RESOURCE_MANAGER_HH
#define DEV_AI_MESH_HOST_RESOURCE_MANAGER_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <vector>

#include "dev/ai_mesh/agent_runtime_types.hh"

namespace gem5
{
namespace ai_mesh
{

enum class HostStageKindV1 : uint8_t
{
    Compile = 0,
    Test = 1,
    LogParse = 2,
};

constexpr size_t kHostStageKindCount = 3;

enum class HostTaskState : uint8_t
{
    WaitHostEnqueue = 0,
    Queued,
    AtomicReserved,
    RunningTimer,
    Done,
    Released,
};

struct HostServiceTask
{
    HostTaskId hostTaskId{0};
    HostStageKindV1 kind = HostStageKindV1::Compile;
    uint64_t arrivalTick = 0;
    uint32_t userId = 0;
    uint32_t taskSeq = 0;
    uint16_t repairRound = 0;
    uint16_t hostTokensRequired = 1;
};

enum class HostEnqueueResult
{
    Accepted,
    RejectedRegistryFull,
};

class HostResourceManager
{
  public:
    HostResourceManager(
        const std::array<uint32_t, kHostStageKindCount> &slots,
        const std::array<uint32_t, kHostStageKindCount> &queueDepths,
        uint64_t globalTokens,
        const std::array<int64_t, kHostStageKindCount> &weights,
        uint32_t registryCapacity);

    HostEnqueueResult tryEnqueue(const HostServiceTask &task);
    std::vector<HostTaskId> arbitrate(uint64_t nowTick);
    bool markRunning(HostTaskId id);
    bool release(HostTaskId id);

    void setAgingThresholdTicks(uint64_t threshold);

    HostTaskState state(HostTaskId id) const;
    int64_t score(size_t kind) const;
    bool hasAgingReservation() const;
    bool hasGrantableHead() const;
    uint64_t availableTokens() const;
    uint32_t freeSlots(size_t kind) const;
    size_t queueLength(size_t kind) const;
    size_t waitingCount() const;

  private:
    struct Entry
    {
        HostServiceTask task;
        HostTaskState state = HostTaskState::WaitHostEnqueue;
    };

    static bool arrivalKeyLess(const HostServiceTask &lhs,
                               const HostServiceTask &rhs);
    Entry *find(HostTaskId id);
    const Entry *find(HostTaskId id) const;
    bool satisfiable(const HostServiceTask &task) const;
    bool headSatisfiable(size_t kind) const;
    void grantFront(size_t kind, std::vector<HostTaskId> &granted);
    bool tryAdmit(size_t kind);

    std::array<uint32_t, kHostStageKindCount> slotCounts;
    std::array<uint32_t, kHostStageKindCount> queueLimits;
    std::array<uint32_t, kHostStageKindCount> freeSlots_;
    std::array<int64_t, kHostStageKindCount> weights;
    std::array<int64_t, kHostStageKindCount> scores{0, 0, 0};
    std::array<std::vector<HostTaskId>, kHostStageKindCount> queues;
    std::map<uint64_t, Entry> entries;
    uint64_t tokensFree;
    uint32_t registryCapacity;
    uint64_t agingThresholdTicks = 0;
    bool agingReservation = false;
    HostTaskId reservationTarget{0};
};

}
}
#endif

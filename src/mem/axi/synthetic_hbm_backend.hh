#ifndef __MEM_AXI_SYNTHETIC_HBM_BACKEND_HH__
#define __MEM_AXI_SYNTHETIC_HBM_BACKEND_HH__

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>

namespace gem5
{
namespace axi
{

enum class SyntheticHbmDirection : uint8_t
{
    Read,
    Write
};

struct SyntheticHbmStats
{
    uint64_t submitted = 0;
    uint64_t completed = 0;
    uint64_t retired = 0;
    uint64_t readBytesServiced = 0;
    uint64_t writeBytesServiced = 0;
    uint64_t busyCycles = 0;
    uint64_t queueFullCycles = 0;
    uint64_t requestSlotCycles = 0;
    size_t queued = 0;
    size_t ready = 0;
    size_t highWater = 0;
};

class SyntheticHbmBackend
{
  public:
    SyntheticHbmBackend(uint32_t bytes_per_cycle, uint32_t queue_depth);

    bool trySubmit(uint64_t uid, SyntheticHbmDirection direction,
                   uint64_t bytes, uint64_t now, uint64_t latency);
    void advance(uint64_t now);
    bool ready(uint64_t uid) const;
    uint64_t completionCycle(uint64_t uid) const;
    void retire(uint64_t uid);
    bool idle() const { return jobs.empty(); }
    bool full() const { return jobs.size() == queueDepth; }
    SyntheticHbmStats stats() const;

  private:
    struct Job
    {
        SyntheticHbmDirection direction;
        uint64_t remaining;
        uint64_t eligibleAt;
        uint64_t sequence;
        std::optional<uint64_t> completedAt;
    };

    const uint32_t bytesPerCycle;
    const uint32_t queueDepth;
    uint64_t currentCycle = 0;
    uint64_t nextSequence = 0;
    std::map<uint64_t, Job> jobs;
    std::optional<uint64_t> active;
    SyntheticHbmStats counters;
};

}
}

#endif

#ifndef __CPU_O3_PROBE_GUEST_CALL_STACK_PROFILER_HH__
#define __CPU_O3_PROBE_GUEST_CALL_STACK_PROFILER_HH__

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "base/types.hh"
#include "cpu/o3/dyn_inst_ptr.hh"
#include "params/GuestCallStackProfiler.hh"
#include "sim/core.hh"
#include "sim/probe/probe.hh"

namespace gem5
{

namespace o3
{

/**
 * Samples a guest-only shadow call stack from the O3 Commit probe.
 *
 * The stack is rooted at the first committed PC after start(), because the
 * shared checkpoint predates this optional listener. Calls and returns after
 * that exact ROI boundary are derived from retired guest instructions. The
 * raw output retains that limitation explicitly; offline tooling must not
 * present the root as a full DWARF unwind of pre-ROI ancestry.
 */
class GuestCallStackProfiler : public ProbeListenerObject
{
  public:
    explicit GuestCallStackProfiler(const GuestCallStackProfilerParams& p);

    void regProbeListeners() override;
    void start();
    void stop();

  private:
    struct Sample
    {
        Tick tick = 0;
        uint64_t retiredMacroInsts = 0;
        Addr pc = 0;
        std::vector<Addr> frames;
    };

    void observeCommit(const DynInstConstPtr& dyn_inst);
    void writeRawSamples();
    void writeMetadata();

    const std::string profileFile;
    const std::string metadataFile;
    const uint64_t samplePeriodInsts;

    bool active = false;
    bool pendingCall = false;
    Tick startTick = 0;
    Tick stopTick = 0;
    uint64_t retiredMacroInsts = 0;
    uint64_t callCount = 0;
    uint64_t returnCount = 0;
    uint64_t returnUnderflowCount = 0;
    uint64_t roiRootResetCount = 0;
    size_t maxDepth = 0;
    std::vector<Addr> shadowStack;
    std::vector<Sample> samples;
};

} // namespace o3
} // namespace gem5

#endif // __CPU_O3_PROBE_GUEST_CALL_STACK_PROFILER_HH__

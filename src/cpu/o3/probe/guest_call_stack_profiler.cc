#include "cpu/o3/probe/guest_call_stack_profiler.hh"

#include <algorithm>
#include <iomanip>
#include <ostream>

#include "base/logging.hh"
#include "base/output.hh"
#include "cpu/o3/dyn_inst.hh"

namespace gem5
{

namespace o3
{

GuestCallStackProfiler::GuestCallStackProfiler(
    const GuestCallStackProfilerParams& p)
    : ProbeListenerObject(p),
      profileFile(p.profile_file),
      metadataFile(p.metadata_file),
      samplePeriodInsts(p.sample_period_insts)
{
    fatal_if(samplePeriodInsts == 0,
             "%s requires sample_period_insts > 0\n", name());
    fatal_if(profileFile.empty() || metadataFile.empty(),
             "%s requires non-empty profile and metadata files\n", name());
}

void
GuestCallStackProfiler::regProbeListeners()
{
    using CommitListener =
        ProbeListenerArg<GuestCallStackProfiler, DynInstConstPtr>;
    listeners.push_back(new CommitListener(
        this, "Commit", &GuestCallStackProfiler::observeCommit));
}

void
GuestCallStackProfiler::start()
{
    fatal_if(active, "%s guest stack profiler was started twice\n", name());
    active = true;
    pendingCall = false;
    startTick = curTick();
    stopTick = 0;
    retiredMacroInsts = 0;
    callCount = 0;
    returnCount = 0;
    returnUnderflowCount = 0;
    roiRootResetCount = 0;
    maxDepth = 0;
    shadowStack.clear();
    samples.clear();
}

void
GuestCallStackProfiler::stop()
{
    fatal_if(!active, "%s guest stack profiler stopped while inactive\n",
             name());
    active = false;
    stopTick = curTick();
    writeRawSamples();
    writeMetadata();
}

void
GuestCallStackProfiler::observeCommit(const DynInstConstPtr& dyn_inst)
{
    if (!active ||
        (dyn_inst->isMicroop() && !dyn_inst->isLastMicroop())) {
        return;
    }

    const Addr pc = dyn_inst->pcState().instAddr();
    if (pendingCall) {
        shadowStack.push_back(pc);
        pendingCall = false;
    }
    if (shadowStack.empty()) {
        shadowStack.push_back(pc);
        ++roiRootResetCount;
    }
    maxDepth = std::max(maxDepth, shadowStack.size());

    ++retiredMacroInsts;
    if (retiredMacroInsts == 1 ||
        retiredMacroInsts % samplePeriodInsts == 0) {
        Sample sample;
        sample.tick = curTick();
        sample.retiredMacroInsts = retiredMacroInsts;
        sample.pc = pc;
        sample.frames = shadowStack;
        // The exact current PC is retained as the leaf. Offline folding
        // coalesces adjacent addresses that resolve to the same function.
        sample.frames.push_back(pc);
        samples.push_back(std::move(sample));
    }

    if (dyn_inst->isReturn()) {
        ++returnCount;
        if (shadowStack.size() > 1) {
            shadowStack.pop_back();
        } else {
            ++returnUnderflowCount;
        }
    }
    if (dyn_inst->isCall()) {
        ++callCount;
        pendingCall = true;
    }
}

void
GuestCallStackProfiler::writeRawSamples()
{
    OutputStream* output = simout.create(profileFile, false, true);
    fatal_if(!output, "%s cannot create guest sample file %s\n",
             name(), profileFile);
    std::ostream& stream = *output->stream();
    stream << "schema_version,profiler_name,sample_index,tick,"
              "retired_macro_insts,pc,stack_depth,stack_addresses\n";
    for (size_t index = 0; index < samples.size(); ++index) {
        const Sample& sample = samples[index];
        stream << "1," << name() << ',' << index << ',' << sample.tick
               << ',' << sample.retiredMacroInsts << ",0x" << std::hex
               << sample.pc << std::dec << ',' << sample.frames.size()
               << ',';
        for (size_t frame = 0; frame < sample.frames.size(); ++frame) {
            if (frame) {
                stream << ';';
            }
            stream << "0x" << std::hex << sample.frames[frame] << std::dec;
        }
        stream << '\n';
    }
    simout.close(output);
}

void
GuestCallStackProfiler::writeMetadata()
{
    OutputStream* output = simout.create(metadataFile, false, true);
    fatal_if(!output, "%s cannot create guest profile metadata %s\n",
             name(), metadataFile);
    std::ostream& stream = *output->stream();
    stream << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"profile_kind\": \"guest_roi_shadow_call_stack\",\n"
           << "  \"source\": \"O3 retired guest macro-instruction Commit probe\",\n"
           << "  \"host_gem5_profile\": false,\n"
           << "  \"semantic_timing_effect\": false,\n"
           << "  \"pre_roi_ancestry_unwound\": false,\n"
           << "  \"roi_root_is_truncated\": true,\n"
           << "  \"sample_period_insts\": " << samplePeriodInsts << ",\n"
           << "  \"start_tick\": " << startTick << ",\n"
           << "  \"stop_tick\": " << stopTick << ",\n"
           << "  \"retired_macro_insts_observed\": "
           << retiredMacroInsts << ",\n"
           << "  \"samples\": " << samples.size() << ",\n"
           << "  \"calls_seen\": " << callCount << ",\n"
           << "  \"returns_seen\": " << returnCount << ",\n"
           << "  \"return_underflows\": " << returnUnderflowCount << ",\n"
           << "  \"roi_root_resets\": " << roiRootResetCount << ",\n"
           << "  \"max_shadow_depth\": " << maxDepth << ",\n"
           << "  \"pending_call_at_stop\": "
           << (pendingCall ? "true" : "false") << "\n"
           << "}\n";
    simout.close(output);
}

} // namespace o3
} // namespace gem5

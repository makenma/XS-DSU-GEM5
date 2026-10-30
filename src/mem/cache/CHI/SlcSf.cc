#include "mem/cache/CHI/SlcSf.hh"

#include <fstream>
#include "base/logging.hh"
#include "sim/serialize.hh"

namespace gem5::Chi
{
namespace
{
SlcSfModel::Config modelConfig(const SlcSfParams &p)
{
    fatal_if(p.ram_latency != 2 || p.cache_line_size != 64 || p.seq_entries != 8,
             "SlcSf supports RAM latency 2, 64B lines and 8 ordinary SEQ entries");
    fatal_if(!p.l3_tag_sets || !p.l3_tag_ways || !p.sf_tag_sets ||
             !p.sf_tag_ways || !p.rnf_num, "SlcSf dimensions must be nonzero");
    SlcSfModel::Config c;
    c.slcSets = p.l3_tag_sets; c.slcWays = p.l3_tag_ways;
    c.sfSets = p.sf_tag_sets; c.sfWays = p.sf_tag_ways;
    c.rnfCount = p.rnf_num; c.lineBytes = p.cache_line_size;
    c.ramLatency = p.ram_latency; c.seqEntries = p.seq_entries;
    c.replacementSeed = p.replacement_seed;
    c.homeCount = p.home_count; c.homeIndex = p.home_index;
    return c;
}
}
SlcSf::SlcSf(const Params &p)
    : ClockedObject(p), Consumer(this), impl(modelConfig(p))
{}

bool SlcSf::tryAccept(const SlcSfRequest &request)
{
    const auto before = impl.notificationSerial();
    const bool accepted = impl.tryAccept(request, uint64_t(curCycle()));
    if (notify && impl.notificationSerial() != before) notify();
    if (accepted) scheduleEvent(Cycles(1));
    return accepted;
}
std::optional<SlcSfResult> SlcSf::popResult()
{
    // HNF consumes only results from a strictly earlier clock edge. This
    // makes visibility independent of event insertion order at this tick.
    if (impl.frontResultCycle() && *impl.frontResultCycle() >= uint64_t(curCycle()))
        return std::nullopt;
    return impl.popResult();
}
void SlcSf::wakeup()
{
    const auto before = impl.notificationSerial();
    impl.advance(uint64_t(curCycle()));
    if (notify && impl.notificationSerial() != before) notify();
    bool draining = false;
    if (drainState() == DrainState::Draining) {
        draining = !impl.drained();
        if (!draining) signalDrainDone();
    }
    if (impl.hasClockWork() || draining) scheduleEvent(Cycles(1));
}
bool SlcSf::checkpointReady()
{
    advanceToCurrent();
    return impl.drained();
}
DrainState SlcSf::drain()
{
    if (checkpointReady()) return DrainState::Drained;
    scheduleEvent(Cycles(1));
    return DrainState::Draining;
}
void SlcSf::serialize(CheckpointOut &cp) const
{
    ClockedObject::serialize(cp);
    const std::string stateFile = name() + ".slcsf";
    SERIALIZE_SCALAR(stateFile);
    std::ofstream out(CheckpointIn::dir() + "/" + stateFile, std::ios::binary);
    fatal_if(!out, "Cannot create SLCSF checkpoint %s", stateFile);
    try { impl.save(out); }
    catch (const std::exception &error) { fatal("%s", error.what()); }
}
void SlcSf::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    std::string stateFile;
    UNSERIALIZE_SCALAR(stateFile);
    std::ifstream in(cp.getCptDir() + "/" + stateFile, std::ios::binary);
    fatal_if(!in, "Cannot open SLCSF checkpoint %s", stateFile);
    try { impl.load(in); }
    catch (const std::exception &error) { fatal("%s", error.what()); }
}
void SlcSf::advanceToCurrent()
{
    const auto before = impl.notificationSerial();
    impl.advance(uint64_t(curCycle()));
    if (notify && impl.notificationSerial() != before) notify();
    if (impl.hasClockWork()) scheduleEvent(Cycles(1));
}
std::optional<SlcSfSeqRequest> SlcSf::pendingSeq()
{
    // Explicit advancement also makes ACK/release timing independent of
    // which ClockedObject callback happens first at this tick.
    advanceToCurrent();
    auto request = impl.pendingSeq(true);
    if (request && request->readyCycle >= uint64_t(curCycle())) return std::nullopt;
    return request;
}
std::optional<SlcSfSeqRequest> SlcSf::seqSnapshot(uint64_t id)
{
    advanceToCurrent();
    return impl.seqSnapshot(id);
}
void SlcSf::acceptSeq(uint64_t id, bool hazard)
{
    advanceToCurrent();
    impl.acceptSeq(id, hazard);
    scheduleEvent(Cycles(1));
}
void SlcSf::releaseSeq(uint64_t id)
{
    advanceToCurrent();
    impl.releaseSeq(id);
    scheduleEvent(Cycles(1));
}
void SlcSf::completeSeq(uint64_t id)
{
    advanceToCurrent();
    impl.completeSeq(id);
    if (impl.hasClockWork()) scheduleEvent(Cycles(1));
    if (notify) notify();
}
void SlcSf::print(std::ostream &out) const
{
    out << name() << " SLC=" << impl.config().slcSets << 'x' << impl.config().slcWays
        << " SF=" << impl.config().sfSets << 'x' << impl.config().sfWays
        << " RNFs=" << impl.config().rnfCount << " SEQ=" << impl.seqOccupancy();
}
} // namespace gem5::Chi

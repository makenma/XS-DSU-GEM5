#ifndef __MEM_CACHE_CHI_SLC_SF_HH__
#define __MEM_CACHE_CHI_SLC_SF_HH__

#include <functional>
#include <optional>
#include <ostream>

#include "mem/cache/CHI/SlcSfModel.hh"
#include "mem/ruby/common/Consumer.hh"
#include "params/SlcSf.hh"
#include "sim/clocked_object.hh"

namespace gem5::Chi
{

/** Clocked adapter for the ordinary SLC/SF datapath. It deliberately does
 * not use ChiPipeline: RAM return arbitration and replay are local to this
 * block and cannot be represented by a free-running flit traversal.
 */
class SlcSf : public ClockedObject, public ruby::Consumer
{
  public:
    PARAMS(SlcSf);
    using MesiState = SlcSfModel::MesiState;
    using L3TagEntry = SlcSfModel::L3TagEntry;
    using SfTagEntry = SlcSfModel::SfTagEntry;
    using L3DataEntry = SlcSfModel::L3DataEntry;
    using L3TagRam = SlcSfModel::L3TagRam;
    using SfTagRam = SlcSfModel::SfTagRam;
    using L3DataRam = SlcSfModel::L3DataRam;

    explicit SlcSf(const Params &p);
    void wakeup() override;
    void print(std::ostream &out) const override;
    bool tryAccept(const SlcSfRequest &request);
    std::optional<SlcSfResult> popResult();
    bool hasWork() const { return impl.hasWork(); }
    bool checkpointReady();
    DrainState drain() override;
    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;
    std::optional<SlcSfSeqRequest> pendingSeq();
    std::optional<SlcSfSeqRequest> seqSnapshot(uint64_t id);
    void acceptSeq(uint64_t id, bool hazard);
    void releaseSeq(uint64_t id);
    void completeSeq(uint64_t id);
    bool needsPocqEntry()
    { advanceToCurrent(); return impl.needsPocqEntry(); }
    void setVictimConflictQuery(SlcSfModel::VictimConflictQuery query)
    { impl.setVictimConflictQuery(std::move(query)); }
    void setWakeup(std::function<void()> callback) { notify = std::move(callback); }
    SlcSfModel &model() { return impl; }
    const SlcSfModel &model() const { return impl; }

    L3TagRam &l3TagRam() { return impl.l3TagRam(); }
    const L3TagRam &l3TagRam() const { return impl.l3TagRam(); }
    SfTagRam &sfTagRam() { return impl.sfTagRam(); }
    const SfTagRam &sfTagRam() const { return impl.sfTagRam(); }
    L3DataRam &l3DataRam() { return impl.l3DataRam(); }
    const L3DataRam &l3DataRam() const { return impl.l3DataRam(); }

  private:
    void advanceToCurrent();
    SlcSfModel impl;
    std::function<void()> notify;
};

} // namespace gem5::Chi
#endif

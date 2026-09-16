#include "mem/cache/CHI/SlcSf.hh"

#include <limits>
#include <type_traits>
#include <utility>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/SlcSf.hh"

namespace gem5::Chi
{

unsigned
SlcSf::ceilLog2(std::size_t value)
{
    unsigned bits = 0;
    if (value > 0)
        --value;
    while (value != 0) {
        ++bits;
        value >>= 1;
    }
    return bits;
}

SlcSf::SfTagEntry::SfTagEntry(std::size_t rnf_num)
    : sfRnfVec(rnf_num, false),
      _rnfIdWidth(SlcSf::ceilLog2(rnf_num))
{}

void
SlcSf::SfTagEntry::setRnfId(uint32_t value)
{
    if (_rnfIdWidth == 0) {
        rnfId = 0;
    } else if (_rnfIdWidth >= std::numeric_limits<uint32_t>::digits) {
        rnfId = value;
    } else {
        rnfId = value & ((uint32_t{1} << _rnfIdWidth) - 1);
    }
}

SlcSf::L3TagRam::L3TagRam(
    std::size_t sets, std::size_t ways, std::size_t rnf_num)
    : _sets(sets), _ways(ways), _rnfNum(rnf_num), entries(sets)
{
    fatal_if(sets == 0 || ways == 0 || rnf_num == 0,
             "L3TagRam sets, ways and rnf_num must all be non-zero");

    for (Row &set : entries) {
        set.reserve(ways);
        for (std::size_t way = 0; way < ways; ++way)
            set.emplace_back(rnf_num);
    }
}

SlcSf::L3TagEntry &
SlcSf::L3TagRam::at(std::size_t set, std::size_t way)
{
    return entries.at(set).at(way);
}

const SlcSf::L3TagEntry &
SlcSf::L3TagRam::at(std::size_t set, std::size_t way) const
{
    return entries.at(set).at(way);
}

SlcSf::L3TagRam::Row &
SlcSf::L3TagRam::row(std::size_t set)
{
    return entries.at(set);
}

const SlcSf::L3TagRam::Row &
SlcSf::L3TagRam::row(std::size_t set) const
{
    return entries.at(set);
}

SlcSf::SfTagRam::SfTagRam(
    std::size_t sets, std::size_t ways, std::size_t rnf_num)
    : _sets(sets), _ways(ways), _rnfNum(rnf_num),
      _rnfIdWidth(SlcSf::ceilLog2(rnf_num)), entries(sets)
{
    fatal_if(sets == 0 || ways == 0 || rnf_num == 0,
             "SfTagRam sets, ways and rnf_num must all be non-zero");

    for (Row &set : entries) {
        set.reserve(ways);
        for (std::size_t way = 0; way < ways; ++way)
            set.emplace_back(rnf_num);
    }
}

SlcSf::SfTagEntry &
SlcSf::SfTagRam::at(std::size_t set, std::size_t way)
{
    return entries.at(set).at(way);
}

const SlcSf::SfTagEntry &
SlcSf::SfTagRam::at(std::size_t set, std::size_t way) const
{
    return entries.at(set).at(way);
}

SlcSf::SfTagRam::Row &
SlcSf::SfTagRam::row(std::size_t set)
{
    return entries.at(set);
}

const SlcSf::SfTagRam::Row &
SlcSf::SfTagRam::row(std::size_t set) const
{
    return entries.at(set);
}

SlcSf::L3DataRam::L3DataRam(
    std::size_t sets, std::size_t ways, std::size_t line_size)
    : _sets(sets), _ways(ways), _lineSize(line_size), entries(sets)
{
    fatal_if(sets == 0 || ways == 0 || line_size == 0,
             "L3DataRam sets, ways and line size must all be non-zero");

    for (Row &set : entries) {
        set.reserve(ways);
        for (std::size_t way = 0; way < ways; ++way)
            set.emplace_back(line_size);
    }
}

SlcSf::L3DataEntry &
SlcSf::L3DataRam::at(std::size_t set, std::size_t way)
{
    return entries.at(set).at(way);
}

const SlcSf::L3DataEntry &
SlcSf::L3DataRam::at(std::size_t set, std::size_t way) const
{
    return entries.at(set).at(way);
}

SlcSf::L3DataRam::Row &
SlcSf::L3DataRam::row(std::size_t set)
{
    return entries.at(set);
}

const SlcSf::L3DataRam::Row &
SlcSf::L3DataRam::row(std::size_t set) const
{
    return entries.at(set);
}

SlcSf::SlcSf(const Params &p)
    : ClockedObject(p),
      Consumer(this),
      ChiPipeline({
          &SlcSf::doStageH0,
          &SlcSf::doStageH1,
          &SlcSf::doStageH2,
          &SlcSf::doStageH3,
          &SlcSf::doStageH4,
          &SlcSf::doStageH5,
          &SlcSf::doStageH6,
          &SlcSf::doStageH7,
          &SlcSf::doStageH8,
          &SlcSf::doStageH9,
          &SlcSf::doStageH10,
      }),
      cacheLineSize(p.cache_line_size),
      m_l3TagRam(p.l3_tag_sets, p.l3_tag_ways, p.rnf_num),
      m_sfTagRam(p.sf_tag_sets, p.sf_tag_ways, p.rnf_num),
      m_l3DataRam(p.l3_tag_sets, p.l3_tag_ways, p.cache_line_size)
{}

void
SlcSf::enqueue(FlitVariant flit)
{
    baseFlit(flit).stage = 0;
    enqueuePipeline(0, std::move(flit));
    scheduleEvent(Cycles(1));
}

std::optional<FlitVariant>
SlcSf::popCompleted()
{
    if (completedFlits.empty())
        return std::nullopt;

    FlitVariant flit = std::move(completedFlits.front());
    completedFlits.pop_front();
    return flit;
}

void
SlcSf::wakeup()
{
    drivePipeline(0, [this](FlitVariant &flit) {
        completedFlits.push_back(std::move(flit));
        return true;
    });

    if (hasPipelineWork())
        scheduleEvent(Cycles(1));
}

void
SlcSf::print(std::ostream &out) const
{
    out << name()
        << " L3Tag=" << m_l3TagRam.sets() << 'x' << m_l3TagRam.ways()
        << " SfTag=" << m_sfTagRam.sets() << 'x' << m_sfTagRam.ways()
        << " L3Data=" << m_l3DataRam.sets() << 'x' << m_l3DataRam.ways()
        << 'x' << m_l3DataRam.lineSize() << "B"
        << " rnf_num=" << m_l3TagRam.rnfNum();
}

std::optional<uint64_t>
SlcSf::addressOf(const FlitVariant &flit)
{
    return std::visit([](const auto &raw) -> std::optional<uint64_t> {
        using Flit = std::decay_t<decltype(raw)>;
        if constexpr (std::is_same_v<Flit, RawReq> ||
                      std::is_same_v<Flit, RawSnp>) {
            return raw.addr;
        } else {
            return std::nullopt;
        }
    }, flit);
}

void
SlcSf::accessTagRams(const FlitVariant &flit, std::size_t stage)
{
    const auto address = addressOf(flit);
    if (!address)
        return;

    const uint64_t line = *address / cacheLineSize;
    const std::size_t l3_set = line % m_l3TagRam.sets();
    const std::size_t sf_set = line % m_sfTagRam.sets();

    // Touch the full set row. Match, replacement, and update behavior will
    // be added on top of these row accessors later.
    (void)m_l3TagRam.row(l3_set);
    (void)m_sfTagRam.row(sf_set);
    DPRINTF(SlcSf, "H%zu tag access addr=%#llx l3_set=%zu sf_set=%zu\n",
            stage, static_cast<unsigned long long>(*address),
            l3_set, sf_set);
}

void
SlcSf::accessDataRam(const FlitVariant &flit, std::size_t stage)
{
    const auto address = addressOf(flit);
    if (!address)
        return;

    const uint64_t line = *address / cacheLineSize;
    const std::size_t set = line % m_l3DataRam.sets();
    (void)m_l3DataRam.row(set);
    DPRINTF(SlcSf, "H%zu data access addr=%#llx set=%zu\n",
            stage, static_cast<unsigned long long>(*address), set);
}

void
SlcSf::advanceStage(FlitVariant &flit, std::size_t stage,
                    StageResource resource)
{
    BaseFlit &base = baseFlit(flit);
    if (!base.isCurrentStage(stage))
        return;

    switch (resource) {
      case StageResource::Tags:
        accessTagRams(flit, stage);
        break;
      case StageResource::Data:
        accessDataRam(flit, stage);
        break;
      case StageResource::Finish:
        DPRINTF(SlcSf, "H%zu completion stage srcid=%u txnid=%u\n",
                stage, base.srcId(), base.txnId());
        break;
    }
    base.next_stage();
}

#define SLC_SF_STAGE(NUMBER, RESOURCE)                                      \
    void                                                                    \
    SlcSf::doStageH##NUMBER(FlitVariant &flit)                              \
    {                                                                       \
        advanceStage(flit, NUMBER, StageResource::RESOURCE);                \
    }

SLC_SF_STAGE(0, Tags)
SLC_SF_STAGE(1, Tags)
SLC_SF_STAGE(2, Tags)
SLC_SF_STAGE(3, Tags)
SLC_SF_STAGE(4, Data)
SLC_SF_STAGE(5, Data)
SLC_SF_STAGE(6, Data)
SLC_SF_STAGE(7, Data)
SLC_SF_STAGE(8, Data)
SLC_SF_STAGE(9, Finish)
SLC_SF_STAGE(10, Finish)

#undef SLC_SF_STAGE

} // namespace gem5::Chi

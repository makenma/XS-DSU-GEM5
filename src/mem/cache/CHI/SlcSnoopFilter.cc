#include "mem/cache/CHI/SlcSnoopFilter.hh"

#include "base/trace.hh"
#include "debug/SlcSnoopFilter.hh"

namespace gem5::Chi
{

SlcSnoopFilter::SlcSnoopFilter(const SlcSnoopFilterParams& p)
    : ClockedObject(p),
      slcsf(p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
            p.sf_num_ways, p.seq_entries,
            makeEmbeddedSlcsfConfig(p, clockPeriod()))
{
    DPRINTF(SlcSnoopFilter,
            "Created block=%u SLC=%ux%u SF=%ux%u SEQ=%u\n",
            p.block_size, p.slc_num_sets, p.slc_num_ways, p.sf_num_sets,
            p.sf_num_ways, p.seq_entries);
}

} // namespace gem5::Chi

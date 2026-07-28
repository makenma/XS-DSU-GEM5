#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include "mem/cache/CHI/HnfSLCSFBackend.hh"

namespace gem5::Chi
{

/**
 * Compatibility adapter for synchronous callers during the timing migration.
 *
 * Storage and coherence semantics live in HnfSLCSFBackend.  Keeping this
 * adapter preserves the existing controller interface until callers migrate
 * to owned, asynchronous requests.
 */
class HnfSLCSF : public HnfSLCSFBackend
{
  public:
    using HnfSLCSFBackend::HnfSLCSFBackend;
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

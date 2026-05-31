#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include <cstddef>
#include <cstdint>
#include <vector>

#include "mem/cache/CHI/HnfCcTypes.hh"

namespace gem5::Chi
{

class HnfSLCSF
{
  public:
    HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
             uint32_t slc_num_ways, uint32_t sf_num_sets,
             uint32_t sf_num_ways);

    HnfSlcLookupResult lookup(const HnfSlcLookupReq& req);
    void fillCleanShared(uint64_t block_addr, uint32_t requester,
                         const std::vector<uint8_t>& data);

    bool isBusy() const { return false; }

  private:
    struct SlcLine
    {
        bool valid = false;
        uint64_t tag = 0;
        HnfSlcState state = HnfSlcState::I;
        uint32_t owner = 0;
        std::vector<uint8_t> data;
    };

    struct SfLine
    {
        bool valid = false;
        uint64_t tag = 0;
        HnfSfState state = HnfSfState::I;
        uint32_t owner = 0;
        uint64_t sharers = 0;
    };

    uint32_t blockSize = 64;
    uint32_t slcSets = 1024;
    uint32_t slcWays = 16;
    uint32_t sfSets = 1024;
    uint32_t sfWays = 16;

    std::vector<SlcLine> slc;
    std::vector<SfLine> sf;

    uint64_t blockNumber(uint64_t block_addr) const;
    size_t slcSet(uint64_t block_addr) const;
    size_t sfSet(uint64_t block_addr) const;
    size_t slcIndex(size_t set, size_t way) const;
    size_t sfIndex(size_t set, size_t way) const;
    size_t chooseSlcWay(size_t set) const;
    size_t chooseSfWay(size_t set) const;
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

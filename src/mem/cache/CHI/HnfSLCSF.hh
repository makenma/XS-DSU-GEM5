#ifndef __HNF_SLCSF_HH__
#define __HNF_SLCSF_HH__

#include <cstddef>
#include <cstdint>
#include <unordered_map>
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
    void writeLine(uint64_t block_addr, uint32_t requester,
                   const std::vector<uint8_t>& data);
    void flushSf(uint64_t block_addr);
    void flushL3(uint64_t block_addr);
    void writeL3FlushSf(uint64_t block_addr, uint32_t requester,
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

    std::unordered_map<uint64_t, SlcLine> slc;
    std::unordered_map<uint64_t, SfLine> sf;

    uint64_t blockNumber(uint64_t block_addr) const;
};

} // namespace gem5::Chi

#endif // __HNF_SLCSF_HH__

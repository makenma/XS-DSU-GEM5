#include "mem/cache/CHI/HnfSLCSF.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HnfSLCSF.hh"

namespace gem5::Chi
{

HnfSLCSF::HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways)
    : blockSize(block_size),
      slcSets(slc_num_sets),
      slcWays(slc_num_ways),
      sfSets(sf_num_sets),
      sfWays(sf_num_ways)
{
    fatal_if(blockSize == 0, "HnfSLCSF block_size must be non-zero\n");
    fatal_if(slcSets == 0 || slcWays == 0,
             "HnfSLCSF SLC sets/ways must be non-zero\n");
    fatal_if(sfSets == 0 || sfWays == 0,
             "HnfSLCSF SF sets/ways must be non-zero\n");
}

uint64_t
HnfSLCSF::blockNumber(uint64_t block_addr) const
{
    return block_addr / blockSize;
}

HnfSlcLookupResult
HnfSLCSF::lookup(const HnfSlcLookupReq& req)
{
    HnfSlcLookupResult result{};
    result.valid = true;
    result.entry = req.entry;
    result.mcreqNonspec = true;

    const uint64_t tag = blockNumber(req.blockAddr);
    auto slcIt = slc.find(tag);
    if (slcIt != slc.end() && slcIt->second.valid) {
        const SlcLine& line = slcIt->second;
        result.slcHit = true;
        result.mcreqNonspec = false;
        result.slcState = line.state;
        result.data = line.data;
        DPRINTF(HnfSLCSF,
                "SLC lookup hit entry=%u addr=%#llx tag=%llu state=%u\n",
                req.entry, static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned long long>(tag),
                static_cast<unsigned>(line.state));
    }

    auto sfIt = sf.find(tag);
    if (sfIt != sf.end() && sfIt->second.valid) {
        const SfLine& line = sfIt->second;
        result.sfHit = true;
        result.sfState = line.state;
        result.rnfid = line.owner;
        result.rnfvec = static_cast<uint32_t>(line.sharers);
    }

    if (!result.slcHit) {
        DPRINTF(HnfSLCSF,
                "SLC/SF lookup miss entry=%u addr=%#llx tag=%llu sfHit=%u\n",
                req.entry, static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned long long>(tag), result.sfHit);
    }

    return result;
}

void
HnfSLCSF::fillCleanShared(uint64_t block_addr, uint32_t requester,
                          const std::vector<uint8_t>& data)
{
    const uint64_t tag = blockNumber(block_addr);

    SlcLine& slcLine = slc[tag];
    slcLine.valid = true;
    slcLine.tag = tag;
    slcLine.state = HnfSlcState::EN;
    slcLine.owner = requester;
    slcLine.data.assign(blockSize, 0);
    const size_t bytes = std::min<size_t>(blockSize, data.size());
    std::copy(data.begin(), data.begin() + bytes, slcLine.data.begin());

    SfLine& sfLine = sf[tag];
    sfLine.valid = true;
    sfLine.tag = tag;
    sfLine.state = HnfSfState::EN;
    sfLine.owner = requester;
    if (requester < 64) {
        sfLine.sharers |= 1ULL << requester;
    }

    DPRINTF(HnfSLCSF,
            "fill clean shared addr=%#llx requester=%u tag=%llu\n",
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned long long>(tag));
}

void
HnfSLCSF::writeLine(uint64_t block_addr, uint32_t requester,
                    const std::vector<uint8_t>& data)
{
    const uint64_t tag = blockNumber(block_addr);

    SlcLine& slcLine = slc[tag];
    slcLine.valid = true;
    slcLine.tag = tag;
    slcLine.state = HnfSlcState::MU;
    slcLine.owner = requester;
    slcLine.data.assign(blockSize, 0);
    const size_t bytes = std::min<size_t>(blockSize, data.size());
    std::copy(data.begin(), data.begin() + bytes, slcLine.data.begin());

    SfLine& sfLine = sf[tag];
    sfLine.valid = true;
    sfLine.tag = tag;
    sfLine.state = HnfSfState::EU;
    sfLine.owner = requester;
    sfLine.sharers = requester < 64 ? (1ULL << requester) : 0;

    DPRINTF(HnfSLCSF,
            "write line addr=%#llx requester=%u tag=%llu bytes=%llu\n",
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned long long>(tag),
            static_cast<unsigned long long>(bytes));
}

void
HnfSLCSF::flushSf(uint64_t block_addr)
{
    const uint64_t tag = blockNumber(block_addr);
    sf.erase(tag);
    DPRINTF(HnfSLCSF, "flush SF addr=%#llx tag=%llu\n",
            static_cast<unsigned long long>(block_addr),
            static_cast<unsigned long long>(tag));
}

void
HnfSLCSF::flushL3(uint64_t block_addr)
{
    const uint64_t tag = blockNumber(block_addr);
    slc.erase(tag);
    DPRINTF(HnfSLCSF, "flush L3 addr=%#llx tag=%llu\n",
            static_cast<unsigned long long>(block_addr),
            static_cast<unsigned long long>(tag));
}

void
HnfSLCSF::writeL3FlushSf(uint64_t block_addr, uint32_t requester,
                         const std::vector<uint8_t>& data)
{
    writeLine(block_addr, requester, data);
    flushSf(block_addr);
}

} // namespace gem5::Chi

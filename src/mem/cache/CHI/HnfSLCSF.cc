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
      sfWays(sf_num_ways),
      slc(static_cast<size_t>(slc_num_sets) * slc_num_ways),
      sf(static_cast<size_t>(sf_num_sets) * sf_num_ways)
{
    fatal_if(blockSize == 0, "HnfSLCSF block_size must be non-zero\n");
    fatal_if(slcSets == 0 || slcWays == 0,
             "HnfSLCSF SLC sets/ways must be non-zero\n");
    fatal_if(sfSets == 0 || sfWays == 0,
             "HnfSLCSF SF sets/ways must be non-zero\n");

    for (auto& line : slc) {
        line.data.assign(blockSize, 0);
    }
}

uint64_t
HnfSLCSF::blockNumber(uint64_t block_addr) const
{
    return block_addr / blockSize;
}

size_t
HnfSLCSF::slcSet(uint64_t block_addr) const
{
    return static_cast<size_t>(blockNumber(block_addr) % slcSets);
}

size_t
HnfSLCSF::sfSet(uint64_t block_addr) const
{
    return static_cast<size_t>(blockNumber(block_addr) % sfSets);
}

size_t
HnfSLCSF::slcIndex(size_t set, size_t way) const
{
    return set * slcWays + way;
}

size_t
HnfSLCSF::sfIndex(size_t set, size_t way) const
{
    return set * sfWays + way;
}

size_t
HnfSLCSF::chooseSlcWay(size_t set) const
{
    for (size_t way = 0; way < slcWays; ++way) {
        if (!slc[slcIndex(set, way)].valid) {
            return way;
        }
    }
    return 0;
}

size_t
HnfSLCSF::chooseSfWay(size_t set) const
{
    for (size_t way = 0; way < sfWays; ++way) {
        if (!sf[sfIndex(set, way)].valid) {
            return way;
        }
    }
    return 0;
}

HnfSlcLookupResult
HnfSLCSF::lookup(const HnfSlcLookupReq& req)
{
    HnfSlcLookupResult result{};
    result.valid = true;
    result.entry = req.entry;
    result.mcreqNonspec = true;

    const uint64_t tag = blockNumber(req.blockAddr);
    const size_t sset = slcSet(req.blockAddr);
    for (size_t way = 0; way < slcWays; ++way) {
        const SlcLine& line = slc[slcIndex(sset, way)];
        if (!line.valid || line.tag != tag) {
            continue;
        }
        result.slcHit = true;
        result.mcreqNonspec = false;
        result.slcState = line.state;
        result.data = line.data;
        DPRINTF(HnfSLCSF,
                "SLC lookup hit entry=%u addr=%#llx set=%llu way=%llu "
                "state=%u\n",
                req.entry, static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned long long>(sset),
                static_cast<unsigned long long>(way),
                static_cast<unsigned>(line.state));
        break;
    }

    const size_t fset = sfSet(req.blockAddr);
    for (size_t way = 0; way < sfWays; ++way) {
        const SfLine& line = sf[sfIndex(fset, way)];
        if (!line.valid || line.tag != tag) {
            continue;
        }
        result.sfHit = true;
        result.sfState = line.state;
        result.rnfid = line.owner;
        result.rnfvec = static_cast<uint32_t>(line.sharers);
        break;
    }

    if (!result.slcHit) {
        DPRINTF(HnfSLCSF,
                "SLC/SF cold miss entry=%u addr=%#llx slcSet=%llu "
                "sfSet=%llu\n",
                req.entry, static_cast<unsigned long long>(req.blockAddr),
                static_cast<unsigned long long>(sset),
                static_cast<unsigned long long>(fset));
    }

    return result;
}

void
HnfSLCSF::fillCleanShared(uint64_t block_addr, uint32_t requester,
                          const std::vector<uint8_t>& data)
{
    const uint64_t tag = blockNumber(block_addr);

    const size_t sset = slcSet(block_addr);
    const size_t sway = chooseSlcWay(sset);
    SlcLine& slcLine = slc[slcIndex(sset, sway)];
    slcLine.valid = true;
    slcLine.tag = tag;
    slcLine.state = HnfSlcState::EN;
    slcLine.owner = requester;
    slcLine.data.assign(blockSize, 0);
    const size_t bytes = std::min<size_t>(blockSize, data.size());
    std::copy(data.begin(), data.begin() + bytes, slcLine.data.begin());

    const size_t fset = sfSet(block_addr);
    const size_t fway = chooseSfWay(fset);
    SfLine& sfLine = sf[sfIndex(fset, fway)];
    sfLine.valid = true;
    sfLine.tag = tag;
    sfLine.state = HnfSfState::EN;
    sfLine.owner = requester;
    if (requester < 64) {
        sfLine.sharers |= 1ULL << requester;
    }

    DPRINTF(HnfSLCSF,
            "fill clean shared addr=%#llx requester=%u slcSet=%llu "
            "way=%llu sfSet=%llu sfWay=%llu\n",
            static_cast<unsigned long long>(block_addr), requester,
            static_cast<unsigned long long>(sset),
            static_cast<unsigned long long>(sway),
            static_cast<unsigned long long>(fset),
            static_cast<unsigned long long>(fway));
}

} // namespace gem5::Chi

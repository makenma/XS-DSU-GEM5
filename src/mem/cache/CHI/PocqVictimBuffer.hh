#ifndef __MEM_CACHE_CHI_POCQ_VICTIM_BUFFER_HH__
#define __MEM_CACHE_CHI_POCQ_VICTIM_BUFFER_HH__

#include <algorithm>
#include <cstdint>
#include <deque>
#include <limits>
#include <stdexcept>

namespace gem5::Chi
{

/** Registered POC victim-buffer occupancy, including the recent past.
 * HNF mutations are visible on the next edge. SLCSF checks H3 occupancy when
 * its delayed replay register reaches H6, so a live-only scan is insufficient.
 */
class PocqVictimBuffer
{
  public:
    void allocate(uint32_t id, uint64_t owner, uint64_t addr, bool ns,
                  uint64_t cycle)
    {
        prune(cycle);
        records.push_back({id, owner, addr >> 6, ns, cycle + 1, Forever});
    }
    void release(uint32_t id, uint64_t cycle)
    {
        for (auto &record : records) {
            if (record.id == id && record.end == Forever) {
                record.end = cycle + 1;
                prune(cycle);
                return;
            }
        }
        throw std::logic_error("release of unknown POC victim buffer");
    }
    bool conflicts(uint64_t owner, uint64_t addr, bool ns,
                   uint64_t sampleCycle) const
    {
        return std::any_of(records.begin(), records.end(), [&](const auto &r) {
            return r.owner != owner && r.line == (addr >> 6) && r.ns == ns &&
                   r.begin <= sampleCycle && sampleCycle < r.end;
        });
    }
    static uint64_t ownerKey(uint32_t srcid, uint32_t txnid)
    { return (uint64_t(srcid) << 32) | txnid; }

  private:
    static constexpr uint64_t Forever = std::numeric_limits<uint64_t>::max();
    struct Record
    {
        uint32_t id;
        uint64_t owner, line;
        bool ns;
        uint64_t begin, end;
    };
    std::deque<Record> records;
    void prune(uint64_t cycle)
    {
        // H3 -> H6 is four model cycles. Seven edges leave room for the
        // explicit HNF boundary while bounding retired history under load.
        if (cycle < 7) return;
        records.erase(std::remove_if(records.begin(), records.end(),
            [=](const auto &r) { return r.end < cycle - 7; }), records.end());
    }
};

} // namespace gem5::Chi
#endif

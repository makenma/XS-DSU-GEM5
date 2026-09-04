#include "dev/ai_mesh/peer_sram_aperture.hh"

#include <algorithm>

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "params/PeerSramAperture.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

PeerSramAperture::PeerSramAperture(const Params &p)
    : ClockedObject(p),
      adapter(p.adapter),
      core_id(p.core_id),
      sram_base(p.sram_base),
      sram_bytes(p.sram_bytes)
{}

void
PeerSramAperture::init()
{
    ClockedObject::init();
    fatal_if(adapter == nullptr, "%s: no target adapter bound", name());
    adapter->registerWriteCommitObserver(this);
}

void
PeerSramAperture::regStats()
{
    ClockedObject::regStats();
}

bool
PeerSramAperture::read(uint64_t offset, uint64_t size, uint8_t *out) const
{
    if (offset > sram_bytes || size > sram_bytes - offset)
        return false;
    for (uint64_t i = 0; i < size; i++)
        out[i] = adapter->readMemoryByte(sram_base + offset + i);
    return true;
}

bool
PeerSramAperture::write(uint64_t offset, uint64_t size, const uint8_t *in)
{
    if (offset > sram_bytes || size > sram_bytes - offset)
        return false;
    for (uint64_t i = 0; i < size; i++)
        adapter->writeMemoryByte(sram_base + offset + i, in[i]);
    return true;
}

void
PeerSramAperture::expectTransfer(
    uint32_t transfer_id, const std::vector<std::pair<uint64_t, uint64_t>> &ranges)
{
    fatal_if(ranges.empty(), "transfer %u has no ranges", transfer_id);
    fatal_if(expectations.count(transfer_id),
             "transfer %u expected twice", transfer_id);
    Expectation expectation;
    for (const auto &range : ranges) {
        fatal_if(range.first >= range.second,
                 "transfer %u has an empty range", transfer_id);
        fatal_if(!adapter->containsMemoryAddress(range.first) ||
                 !adapter->containsMemoryAddress(range.second - 1),
                 "transfer %u range [%#llx,%#llx) escapes the SRAM tile",
                 transfer_id, (unsigned long long)range.first,
                 (unsigned long long)range.second);
        for (const auto &other : expectation.ranges)
            fatal_if(range.first < other.second && other.first < range.second,
                     "transfer %u ranges overlap", transfer_id);
        for (const auto &installed : expectations)
            for (const auto &other : installed.second.ranges)
                fatal_if(
                    range.first < other.second && other.first < range.second,
                    "transfer %u range overlaps live transfer %u",
                    transfer_id, installed.first);
        expectation.ranges.push_back(range);
        expectation.expected += range.second - range.first;
    }
    expectations[transfer_id] = expectation;
}

void
PeerSramAperture::cancelAllExpectations()
{
    expectations.clear();
}

void
PeerSramAperture::beginInstance()
{
    instance_counter++;
    transfer_commit_ticks.clear();
    expectations.clear();
}

void
PeerSramAperture::onAxiWriteCommitted(
    const axi::AxiAddressRequest &request,
    const std::vector<axi::AxiDataPacket> &beats,
    axi::AxiResp resp)
{
    const uint64_t bus = uint64_t(1) << request.size;
    uint64_t strobed = 0;
    for (const auto &beat : beats)
        strobed += __builtin_popcountll(beat.byteStrobe);

    last_commit_tick = curTick();
    if (resp != axi::AxiResp::Okay) {
        error_drained_bytes += strobed;
        return;
    }
    committed_valid_bytes += strobed;

    std::vector<uint32_t> completed;
    for (auto &kv : expectations) {
        Expectation &expectation = kv.second;
        if (expectation.observed >= expectation.expected)
            continue;
        // Full-width beats: lane l of beat i lands at
        // address + i*bus + l (AxADDR is bus aligned for our bursts).
        for (size_t i = 0; i < beats.size() &&
             expectation.observed < expectation.expected; i++) {
            const uint64_t lane_base = request.address + uint64_t(i) * bus;
            uint64_t strobe = beats[i].byteStrobe;
            while (strobe && expectation.observed < expectation.expected) {
                const int lane = __builtin_ctzll(strobe);
                const uint64_t addr = lane_base + uint64_t(lane);
                for (const auto &range : expectation.ranges)
                    if (addr >= range.first && addr < range.second) {
                        expectation.observed++;
                        break;
                    }
                strobe &= ~(uint64_t(1) << lane);
            }
        }
        if (expectation.observed == expectation.expected)
            completed.push_back(kv.first);
    }
    for (uint32_t transfer_id : completed) {
        Expectation &expectation = expectations.at(transfer_id);
        if (expectation.notified)
            continue;
        expectation.notified = true;
        transfer_commit_ticks[transfer_id] = curTick();
        expectations.erase(transfer_id);
        if (owner)
            owner->onTransferCommitted(transfer_id);
    }
}

} // namespace ai_mesh
} // namespace gem5


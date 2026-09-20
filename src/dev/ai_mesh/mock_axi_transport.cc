#include "dev/ai_mesh/mock_axi_transport.hh"

#include "base/logging.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "params/MockAxiTransport.hh"

namespace gem5
{
namespace ai_mesh
{

MockAxiTransport::MockAxiTransport(const Params &p)
    : ClockedObject(p),
      data_bus_bytes(p.data_bus_bytes),
      burst_base_latency(p.burst_base_latency),
      sram_region_base(p.sram_region_base),
      sram_tile_stride(p.sram_tile_stride),
      error_descriptors(p.error_descriptors.begin(), p.error_descriptors.end()),
      lost_descriptors(p.lost_descriptors.begin(), p.lost_descriptors.end()),
      fault_occurrence(p.fault_occurrence)
{}

MockAxiTransport::DescriptorFault
MockAxiTransport::takeDescriptorFault(uint32_t descriptor_id)
{
    const bool listed = error_descriptors.count(descriptor_id) != 0 ||
                        lost_descriptors.count(descriptor_id) != 0;
    if (!listed)
        return DescriptorFault::None;
    const uint32_t occurrence = ++descriptor_completions[descriptor_id];
    if (fault_occurrence != 0 && occurrence != fault_occurrence)
        return DescriptorFault::None;
    if (lost_descriptors.count(descriptor_id) != 0)
        return DescriptorFault::Lost;
    return DescriptorFault::Error;
}

void MockAxiTransport::regStats()
{
    ClockedObject::regStats();
    readBytes.name(name() + ".dma_read_bytes").desc("HBM/shared read bytes committed");
    writeBytes.name(name() + ".dma_write_bytes").desc("HBM/shared write bytes committed");
    p2pBytes.name(name() + ".p2p_bytes").desc("Peer SRAM push bytes committed");
    readBursts.name(name() + ".dma_read_bursts").desc("Read bursts");
    writeBursts.name(name() + ".dma_write_bursts").desc("Write bursts");
    p2pBursts.name(name() + ".p2p_bursts").desc("P2P bursts");
    fillBytes.name(name() + ".fill_bytes").desc("Local SRAM fill bytes");
}

void MockAxiTransport::registerCore(uint16_t core_id, MeshDummyCore *core)
{
    cores[core_id] = core;
}

std::vector<uint8_t> &MockAxiTransport::page(uint64_t addr)
{
    static const size_t PAGE = 4096;
    uint64_t index = addr / PAGE;
    auto it = hbm_pages.find(index);
    if (it == hbm_pages.end())
        it = hbm_pages.emplace(index, std::vector<uint8_t>(PAGE, 0)).first;
    return it->second;
}

bool MockAxiTransport::readHbm(uint64_t addr, uint64_t size, uint8_t *out)
{
    static const size_t PAGE = 4096;
    uint64_t done = 0;
    while (done < size) {
        uint64_t offset_in_page = (addr + done) % PAGE;
        uint64_t take = std::min<uint64_t>(size - done, PAGE - offset_in_page);
        const auto &source = page(addr + done);
        std::memcpy(out + done, source.data() + offset_in_page, take);
        done += take;
    }
    return true;
}

bool MockAxiTransport::writeHbm(uint64_t addr, uint64_t size, const uint8_t *in)
{
    static const size_t PAGE = 4096;
    uint64_t done = 0;
    while (done < size) {
        uint64_t offset_in_page = (addr + done) % PAGE;
        uint64_t take = std::min<uint64_t>(size - done, PAGE - offset_in_page);
        auto &target = page(addr + done);
        std::memcpy(target.data() + offset_in_page, in + done, take);
        done += take;
    }
    return true;
}

bool MockAxiTransport::sramAddressToTile(uint64_t addr, uint16_t &core_id,
                                         uint64_t &offset) const
{
    if (addr < sram_region_base || sram_tile_stride == 0)
        return false;
    uint64_t relative = addr - sram_region_base;
    uint64_t tile = relative / sram_tile_stride;
    for (const auto &kv : cores)
        if (kv.first == tile) {
            core_id = kv.first;
            offset = relative % sram_tile_stride;
            return true;
        }
    return false;
}

bool MockAxiTransport::readSram(uint64_t addr, uint64_t size, uint8_t *out)
{
    uint16_t core_id;
    uint64_t offset;
    if (!sramAddressToTile(addr, core_id, offset))
        return false;
    auto it = cores.find(core_id);
    if (it == cores.end())
        return false;
    return it->second->functionalSramRead(offset, size, out);
}

bool MockAxiTransport::writeSram(uint64_t addr, uint64_t size, const uint8_t *in)
{
    uint16_t core_id;
    uint64_t offset;
    if (!sramAddressToTile(addr, core_id, offset))
        return false;
    auto it = cores.find(core_id);
    if (it == cores.end())
        return false;
    return it->second->functionalSramWrite(offset, size, in);
}

Tick MockAxiTransport::burstLatency(uint64_t beats) const
{
    return clockPeriod() * (burst_base_latency + Cycles(beats));
}

Tick MockAxiTransport::transferLatency(uint64_t beats_total, uint32_t bursts) const
{
    uint64_t cycles = uint64_t(burst_base_latency) * bursts + beats_total;
    return clockPeriod() * Cycles(cycles);
}

ai_mesh::TrafficContribution
MockAxiTransport::accountRead(uint32_t descriptor_id, uint64_t bytes,
                              uint32_t bursts)
{
    auto &row = actual[descriptor_id];
    row.read_bytes += bytes;
    row.read_bursts += bursts;
    readBytes += bytes;
    readBursts += bursts;

    TrafficContribution contribution;
    contribution.read_bytes = bytes;
    contribution.read_bursts = bursts;
    contribution.payload_digest = finishPayloadDigest(descriptor_id);
    return contribution;
}

ai_mesh::TrafficContribution
MockAxiTransport::accountWrite(uint32_t descriptor_id, uint64_t bytes,
                               uint32_t bursts)
{
    auto &row = actual[descriptor_id];
    row.write_bytes += bytes;
    row.write_bursts += bursts;
    writeBytes += bytes;
    writeBursts += bursts;

    TrafficContribution contribution;
    contribution.write_bytes = bytes;
    contribution.write_bursts = bursts;
    contribution.payload_digest = finishPayloadDigest(descriptor_id);
    return contribution;
}

ai_mesh::TrafficContribution
MockAxiTransport::accountP2p(uint32_t descriptor_id, uint64_t bytes,
                             uint32_t bursts)
{
    auto &row = actual[descriptor_id];
    row.p2p_bytes += bytes;
    row.p2p_bursts += bursts;
    p2pBytes += bytes;
    p2pBursts += bursts;

    TrafficContribution contribution;
    contribution.p2p_bytes = bytes;
    contribution.p2p_bursts = bursts;
    contribution.payload_digest = finishPayloadDigest(descriptor_id);
    return contribution;
}

ai_mesh::TrafficContribution
MockAxiTransport::accountFill(uint32_t descriptor_id, uint64_t bytes)
{
    auto &row = actual[descriptor_id];
    row.fill_bytes += bytes;
    fillBytes += bytes;

    TrafficContribution contribution;
    contribution.fill_bytes = bytes;
    contribution.payload_digest = finishPayloadDigest(descriptor_id);
    return contribution;
}

void MockAxiTransport::accountInjectedError(uint32_t descriptor_id)
{
    // An injected response failure commits no bytes and no payload: the row is
    // materialized with zero traffic so the accounting stays complete without
    // claiming or erasing a successful execution's digest.
    actual[descriptor_id];
    digest_state.erase(descriptor_id);
}

void MockAxiTransport::beginPayloadDigest(uint32_t descriptor_id)
{
    digest_state[descriptor_id] = {0xcbf29ce484222325ull,
                                   0x9E3779B97F4A7C15ull};
}

void MockAxiTransport::notePayload(uint32_t descriptor_id, const uint8_t *data,
                                   uint64_t size)
{
    // Rolling digest over the functionally moved bytes; deterministic and
    // reported in the result JSON for content-flow reconciliation.  The
    // state is created at submit and rolls across every row until the
    // descriptor finishes.
    auto it = digest_state.find(descriptor_id);
    if (it == digest_state.end())
        it = digest_state.emplace(
            descriptor_id,
            std::make_pair(0xcbf29ce484222325ull,
                           0x9E3779B97F4A7C15ull)).first;
    uint64_t &h0 = it->second.first;
    uint64_t &h1 = it->second.second;
    for (uint64_t i = 0; i < size; i++) {
        h0 = (h0 ^ data[i]) * 0x100000001B3ull;
        h1 = (h1 + ((h0 >> 31) ^ data[i])) * 0xBF58476D1CE4E5B9ull;
    }
}

std::string
MockAxiTransport::finishPayloadDigest(uint32_t descriptor_id)
{
    auto it = digest_state.find(descriptor_id);
    if (it == digest_state.end())
        return std::string();
    static const char *hex = "0123456789abcdef";
    std::string out;
    for (int shift = 60; shift >= 0; shift -= 4)
        out += hex[(it->second.first >> shift) & 0xF];
    out += '-';
    for (int shift = 60; shift >= 0; shift -= 4)
        out += hex[(it->second.second >> shift) & 0xF];
    actual[descriptor_id].payload_digest = out;
    digest_state.erase(it);
    return out;
}

} // namespace ai_mesh
} // namespace gem5

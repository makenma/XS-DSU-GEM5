#ifndef DEV_AI_MESH_MOCK_AXI_TRANSPORT_HH
#define DEV_AI_MESH_MOCK_AXI_TRANSPORT_HH

#include <cstdint>
#include <map>
#include <vector>

#include <string>

#include "base/statistics.hh"
#include "dev/ai_mesh/dma_types.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct MockAxiTransportParams;

namespace ai_mesh
{

class MeshDummyCore;

// Functional mock AXI transport (Dummy Core runtime prototype scope):
//   - HBM / host-shared backing store is a sparse 4 KiB page map;
//   - SRAM tiles belong to MeshDummyCore objects registered by the loader;
//   - latency is analytic: per burst base latency + one cycle per beat;
//   - per-descriptor byte accounting feeds the traffic oracle result JSON.
class MockAxiTransport : public ClockedObject
{
  public:
    using Params = MockAxiTransportParams;

    explicit MockAxiTransport(const Params &p);

    void regStats() override;

    void registerCore(uint16_t core_id, MeshDummyCore *core);

    // Absolute-address functional accessors used by the DMA engine.
    bool readHbm(uint64_t addr, uint64_t size, uint8_t *out);
    bool writeHbm(uint64_t addr, uint64_t size, const uint8_t *in);
    bool readSram(uint64_t addr, uint64_t size, uint8_t *out);
    bool writeSram(uint64_t addr, uint64_t size, const uint8_t *in);

    bool sramAddressToTile(uint64_t addr, uint16_t &core_id, uint64_t &offset) const;

    // Analytic completion latency for one burst of `beats` beats, plus the
    // serialized latency of a whole burst list.
    Tick burstLatency(uint64_t beats) const;
    Tick transferLatency(uint64_t beats_total, uint32_t bursts) const;

    // Per-descriptor accounting (oracle reconciliation).
    void accountRead(uint32_t descriptor_id, uint64_t bytes, uint32_t bursts);
    void accountWrite(uint32_t descriptor_id, uint64_t bytes, uint32_t bursts);
    void accountP2p(uint32_t descriptor_id, uint64_t bytes, uint32_t bursts);
    void accountFill(uint32_t descriptor_id, uint64_t bytes);
    void notePayload(uint32_t descriptor_id, const uint8_t *data, uint64_t size);

    // Digest state lifecycle: begin at submit, rows accumulate, finish
    // formats and retires the state (spec: descriptor rolling state).
    void beginPayloadDigest(uint32_t descriptor_id);
    void finishPayloadDigest(uint32_t descriptor_id);

    const std::map<uint32_t, ActualTraffic> &actualTraffic() const { return actual; }

    uint32_t dataBusBytes() const { return data_bus_bytes; }
    uint64_t burstBaseLatencyCycles() const
    {
        return static_cast<uint64_t>(burst_base_latency);
    }

  private:
    statistics::Scalar readBytes;
    statistics::Scalar writeBytes;
    statistics::Scalar p2pBytes;
    statistics::Scalar readBursts;
    statistics::Scalar writeBursts;
    statistics::Scalar p2pBursts;
    statistics::Scalar fillBytes;

    uint32_t data_bus_bytes;
    Cycles burst_base_latency;
    uint64_t sram_region_base;
    uint64_t sram_tile_stride;

    std::map<uint16_t, MeshDummyCore *> cores;
    std::map<uint64_t, std::vector<uint8_t>> hbm_pages;
    std::map<uint32_t, ActualTraffic> actual;
    std::map<uint32_t, std::pair<uint64_t, uint64_t>> digest_state;

    std::vector<uint8_t> &page(uint64_t addr);
};

} // namespace ai_mesh
} // namespace gem5

#endif

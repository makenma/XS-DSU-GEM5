#ifndef DEV_AI_MESH_NPU_MEMORY_ENDPOINT_HH
#define DEV_AI_MESH_NPU_MEMORY_ENDPOINT_HH

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "dev/ai_mesh/target_sentinels.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct NpuMemoryEndpointParams;

namespace ai_mesh
{

// HBM / host-shared memory endpoint: a real AXI target adapter whose
// simple memory backs the DMA LOAD source and DMA_STORE destination
// ranges.  Seeding is a host-side pre-simulation setup step; verification
// digests are computed from the committed bytes at drain time.
class NpuMemoryEndpoint : public ClockedObject,
                          public axi::AxiWriteCommitObserver
{
  public:
    using Params = NpuMemoryEndpointParams;

    NpuMemoryEndpoint(const Params &p);

    void init() override;
    void startup() override;
    void regStats() override;

    void seed(uint64_t address, uint64_t size, uint8_t pattern);
    // Digest of the current committed bytes of [address, address+size).
    std::string rangeDigest(uint64_t address, uint64_t size) const;
    // Raw committed bytes of [address, address+size) from the real backing.
    bool readBytes(uint64_t address, uint64_t size, uint8_t *out) const;
    uint64_t committedBytes() const { return committed_valid_bytes; }
    // No accepted write or response of this target endpoint is still pending.
    bool functionalIdle() const { return adapter->functionalIdle(); }
    uint64_t errorDrainBytes() const { return error_drained_bytes; }

    // Drain-time digest computation for every registered verify row, and the
    // before/after comparison of every declared sentinel span.
    void computeVerifyDigests();
    const std::vector<SentinelRangeObservation> &sentinelRanges() const
    {
        return sentinels;
    }
    bool sentinelsEmpty() const { return sentinel_spans.empty(); }

    struct SeedRow
    {
        uint64_t address = 0;
        uint64_t size = 0;
        uint8_t pattern = 0;
        std::string digest;
    };
    const std::vector<SeedRow> &seedRows() const { return seeds; }

    // Verification rows exported to the result JSON by the dispatcher.
    struct VerifyRow
    {
        uint64_t address = 0;
        uint64_t size = 0;
        std::string digest;
        std::string after_digest; // digest of bytes [16, size)
        // Post-quiescence readback of the whole row, present only when the
        // row fits the configured byte-dump limit.  It lets a reviewer
        // compare the real committed bytes against the admitted source
        // without trusting a digest.
        std::string bytes_hex;
    };
    const std::vector<VerifyRow> &verifyRows() const { return verifies; }

    void onAxiWriteCommitted(const axi::AxiAddressRequest &request,
                              const std::vector<axi::AxiDataPacket> &beats,
                              axi::AxiResp resp) override;

    axi::AxiEndpointQueueHighWater queueHighWater() const
    { return adapter->functionalQueueHighWater(); }

  private:
    void seedBytes(uint64_t address, uint64_t size, uint8_t pattern);

    axi::AxiTargetAdapter *const adapter;
    const std::string seed_json_path;
    const std::string verify_json_path;
    const uint64_t verify_bytes_limit;

    const std::string sentinel_json_path;

    std::vector<SeedRow> seeds;
    std::vector<VerifyRow> verifies;
    TargetSentinels sentinel_spans;
    std::vector<SentinelRangeObservation> sentinels;
    uint64_t committed_valid_bytes = 0;
    uint64_t error_drained_bytes = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

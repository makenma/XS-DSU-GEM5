#ifndef DEV_AI_MESH_PEER_SRAM_APERTURE_HH
#define DEV_AI_MESH_PEER_SRAM_APERTURE_HH

#include <cstdint>
#include <map>
#include <utility>
#include <vector>

#include "dev/ai_mesh/tensor_sram.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct PeerSramApertureParams;

namespace ai_mesh
{

class MeshDummyCore;

// Per-core SRAM tile exposed as a real AXI target on the NPU Garnet.
// The aperture's target-adapter memory is the single functional store of
// the tile: local DMA commits route through the core's TensorSram backing
// into it, and P2P W beats commit into it through the real network.  A
// write-commit observer turns observed committed bytes into transfer
// completions that release the receiving core's RECV_WAIT commands.
class PeerSramAperture : public ClockedObject,
                         public SramBacking,
                         public axi::AxiWriteCommitObserver
{
  public:
    using Params = PeerSramApertureParams;

    PeerSramAperture(const Params &p);

    void init() override;
    void regStats() override;

    uint16_t coreId() const { return core_id; }

    // SramBacking (tile-local offset accessors).
    bool read(uint64_t offset, uint64_t size, uint8_t *out) const override;
    bool write(uint64_t offset, uint64_t size, const uint8_t *in) override;

    uint64_t sramBase() const { return sram_base; }
    uint64_t committedBytes() const { return committed_valid_bytes; }
    uint64_t errorDrainBytes() const { return error_drained_bytes; }
    Tick lastCommitTick() const { return last_commit_tick; }

    // Loader installs the expected transfer table: transfer_id -> absolute
    // destination byte ranges (one per descriptor row) of the logical P2P
    // bytes.  A transfer completes when exactly its expectation has been
    // observed inside its ranges.
    void expectTransfer(uint32_t transfer_id,
                        const std::vector<std::pair<uint64_t, uint64_t>> &ranges);
    const std::map<uint32_t, Tick> &transferCommitTicks() const
    {
        return transfer_commit_ticks;
    }

    // Every program instance must re-observe its transfers: reset the
    // per-instance observation state while keeping the expectation table
    // and accumulating lifetime statistics.
    void beginInstance();
    void cancelAllExpectations();
    uint32_t instanceCount() const { return instance_counter; }

    void onAxiWriteCommitted(const axi::AxiAddressRequest &request,
                              const std::vector<axi::AxiDataPacket> &beats,
                              axi::AxiResp resp) override;

    void bindCore(MeshDummyCore *core) { owner = core; }

    axi::AxiEndpointQueueHighWater queueHighWater() const
    { return adapter->functionalQueueHighWater(); }

  private:
    axi::AxiTargetAdapter *const adapter;
    MeshDummyCore *owner = nullptr;
    const uint16_t core_id;
    const uint64_t sram_base;
    const uint64_t sram_bytes;

    struct Expectation
    {
        std::vector<std::pair<uint64_t, uint64_t>> ranges;
        uint64_t expected = 0;
        uint64_t observed = 0;
        bool notified = false;
    };
    std::map<uint32_t, Expectation> expectations;
    std::map<uint32_t, Tick> transfer_commit_ticks;
    uint32_t instance_counter = 0;

    uint64_t committed_valid_bytes = 0;
    uint64_t error_drained_bytes = 0;
    Tick last_commit_tick = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

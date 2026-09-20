#ifndef DEV_AI_MESH_PEER_SRAM_APERTURE_HH
#define DEV_AI_MESH_PEER_SRAM_APERTURE_HH

#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "dev/ai_mesh/peer_transfer_coverage.hh"
#include "dev/ai_mesh/target_sentinels.hh"
#include "dev/ai_mesh/tensor_sram.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/axi/axi_types.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

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
// write-commit observer turns real committed bytes into transfer completions
// that release the receiving core's RECV_WAIT commands.
//
// Coverage is owned by PeerTransferCoverageTable: a per-byte set, not a
// counter, and a transaction identity is single-use, so a transfer can only be
// released by its own accepted transactions covering every admitted byte.  A
// replayed notification of an already attributed transaction never extends a
// later transfer that reuses the same address.
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
    uint64_t sramBytes() const { return sram_bytes; }
    uint64_t committedBytes() const { return committed_valid_bytes; }
    uint64_t errorDrainBytes() const { return error_drained_bytes; }
    Tick lastCommitTick() const { return last_commit_tick; }

    // The loader installs the expected transfer table: transfer_id -> absolute
    // destination byte ranges (one per descriptor row) of the logical P2P
    // bytes.
    void expectTransfer(uint32_t transfer_id,
                        const std::vector<std::pair<uint64_t, uint64_t>> &ranges);

    std::vector<PeerTransferCoverage> transferCoverage() const;
    const std::map<uint32_t, Tick> &transferCommitTicks() const
    {
        return coverage.commitTicks();
    }
    // Lanes refused because their transaction identity had already been
    // attributed: a replayed or foreign commit never extends coverage.
    uint64_t replayedLanes() const { return coverage.replayedLanes(); }

    // Every program instance must re-observe its transfers: reset the
    // per-instance observation state while keeping the expectation table
    // and accumulating lifetime statistics.
    void beginInstance();
    // An instance error stops the wait for the rest of a transfer's plan: the
    // expectation retires as abandoned, keeping the coverage its own real
    // commits landed, so the partial outcome stays reportable.
    void abandonAllExpectations();
    uint32_t instanceCount() const { return instance_counter; }

    void onAxiWriteCommitted(const axi::AxiAddressRequest &request,
                              const std::vector<axi::AxiDataPacket> &beats,
                              axi::AxiResp resp) override;
    void onAxiWriteCommittedWithMeta(const axi::AxiAddressPacket &packet,
                                     const std::vector<axi::AxiDataPacket> &beats,
                                     axi::AxiResp resp) override;

    // Sentinel spans of this tile: sampled before cycle 0 and re-read at
    // drain, so WSTRB-disabled lanes and row padding are proven untouched.
    void sampleSentinels();
    // Does any armed expectation still wait for its admitted coverage?
    bool liveExpectations() const { return coverage.live(); }
    bool expects(uint32_t transfer_id) const
    {
        return coverage.expects(transfer_id);
    }
    const std::vector<SentinelRangeObservation> &sentinelRanges() const
    {
        return sentinels;
    }

    void bindCore(MeshDummyCore *core) { owner = core; }

    axi::AxiEndpointQueueHighWater queueHighWater() const
    { return adapter->functionalQueueHighWater(); }

  private:
    axi::AxiTargetAdapter *const adapter;
    MeshDummyCore *owner = nullptr;
    const uint16_t core_id;
    const uint64_t sram_base;
    const uint64_t sram_bytes;

    const std::string sentinel_json_path;
    TargetSentinels sentinel_spans;
    std::vector<SentinelRangeObservation> sentinels;

    // Test-only stale delivery: the write commit of this transaction identity is
    // re-delivered through the same observer once a later expectation covering
    // its range is armed, so the owner's attribution rule is exercised at the
    // real commit boundary rather than in a probe.
    const uint64_t replay_commit_uid;
    const Tick replay_commit_delay;
    struct ReplayCommit
    {
        axi::AxiAddressRequest request;
        std::vector<axi::AxiDataPacket> beats;
        axi::AxiResp resp = axi::AxiResp::Okay;
        uint64_t txn_uid = 0;
        bool captured = false;
    };
    void replayCommitEvent();
    bool replayCovers(
        const std::vector<std::pair<uint64_t, uint64_t>> &ranges) const;
    ReplayCommit captured_commit;
    bool replay_scheduled = false;
    EventWrapper<PeerSramAperture, &PeerSramAperture::replayCommitEvent>
        replay_event;

    PeerTransferCoverageTable coverage;
    uint32_t instance_counter = 0;

    void observeCommit(const axi::AxiAddressRequest &request,
                       const std::vector<axi::AxiDataPacket> &beats,
                       axi::AxiResp resp, bool has_meta, uint64_t txn_uid);

    uint64_t committed_valid_bytes = 0;
    uint64_t error_drained_bytes = 0;
    Tick last_commit_tick = 0;
};

} // namespace ai_mesh
} // namespace gem5

#endif

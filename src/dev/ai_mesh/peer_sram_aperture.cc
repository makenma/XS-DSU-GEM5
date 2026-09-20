#include "dev/ai_mesh/peer_sram_aperture.hh"

#include "dev/ai_mesh/mesh_dispatcher.hh"

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
      sram_bytes(p.sram_bytes),
      sentinel_json_path(p.sentinel_json),
      replay_commit_uid(p.replay_commit_uid),
      replay_commit_delay(p.replay_commit_delay),
      replay_event(this)
{}

void
PeerSramAperture::init()
{
    ClockedObject::init();
    fatal_if(adapter == nullptr, "%s: no target adapter bound", name());
    adapter->registerWriteCommitObserver(this);
    sentinel_spans.load(sentinel_json_path);
    if (!sentinel_spans.empty())
        sentinel_spans.sample(*adapter);
}

void
PeerSramAperture::sampleSentinels()
{
    sentinels = sentinel_spans.compare(*adapter);
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
    // Only this tile knows its own address window; the coverage owner holds the
    // expectation and attribution rules.
    for (const auto &range : ranges)
        fatal_if(!adapter->containsMemoryAddress(range.first) ||
                 !adapter->containsMemoryAddress(range.second - 1),
                 "transfer %u range [%#llx,%#llx) escapes the SRAM tile",
                 transfer_id, (unsigned long long)range.first,
                 (unsigned long long)range.second);
    const bool was_expected = coverage.expects(transfer_id);
    coverage.expect(transfer_id, ranges);
    if (!was_expected && captured_commit.captured && !replay_scheduled &&
        replayCovers(ranges)) {
        replay_scheduled = true;
        schedule(replay_event, curTick() + replay_commit_delay);
    }
}

bool
PeerSramAperture::replayCovers(
    const std::vector<std::pair<uint64_t, uint64_t>> &ranges) const
{
    for (size_t index = 0; index < captured_commit.beats.size(); index++) {
        const uint64_t lane_base = captured_commit.request.address
            + uint64_t(index) * (uint64_t(1) << captured_commit.request.size);
        uint64_t strobe = captured_commit.beats[index].byteStrobe;
        while (strobe) {
            const int lane = __builtin_ctzll(strobe);
            strobe &= ~(uint64_t(1) << lane);
            const uint64_t address = lane_base + uint64_t(lane);
            for (const auto &bounds : ranges)
                if (address >= bounds.first && address < bounds.second)
                    return true;
        }
    }
    return false;
}

void
PeerSramAperture::replayCommitEvent()
{
    // The stale delivery enters through the real observer entry, so a rejected
    // replay is rejected by the owner rather than by the fault hook.
    observeCommit(captured_commit.request, captured_commit.beats,
                  captured_commit.resp, true, captured_commit.txn_uid);
}

void
PeerSramAperture::abandonAllExpectations()
{
    coverage.abandonAll();
}

void
PeerSramAperture::beginInstance()
{
    instance_counter++;
    coverage.beginInstance();
}

std::vector<PeerTransferCoverage>
PeerSramAperture::transferCoverage() const
{
    return coverage.rows();
}

void
PeerSramAperture::onAxiWriteCommitted(const axi::AxiAddressRequest &request,
                                      const std::vector<axi::AxiDataPacket> &beats,
                                      axi::AxiResp resp)
{
    observeCommit(request, beats, resp, false, 0);
}

void
PeerSramAperture::onAxiWriteCommittedWithMeta(
    const axi::AxiAddressPacket &packet,
    const std::vector<axi::AxiDataPacket> &beats, axi::AxiResp resp)
{
    observeCommit(packet.request, beats, resp, true, packet.meta.txnUid);
}

void
PeerSramAperture::observeCommit(const axi::AxiAddressRequest &request,
                                const std::vector<axi::AxiDataPacket> &beats,
                                axi::AxiResp resp, bool has_meta,
                                uint64_t txn_uid)
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

    PeerTransferCoverageTable::CommitFacts facts;
    facts.tick = curTick();
    facts.base_address = request.address;
    facts.bus_bytes = bus;
    facts.has_meta = has_meta;
    facts.txn_uid = txn_uid;
    facts.lanes.reserve(strobed);
    for (size_t index = 0; index < beats.size(); index++) {
        const uint64_t lane_base = request.address + uint64_t(index) * bus;
        uint64_t lane_strobe = beats[index].byteStrobe;
        while (lane_strobe) {
            const int lane = __builtin_ctzll(lane_strobe);
            lane_strobe &= ~(uint64_t(1) << lane);
            facts.lanes.push_back(lane_base + uint64_t(lane));
        }
    }
    if (has_meta && replay_commit_uid != ~uint64_t(0) &&
        txn_uid == replay_commit_uid && !captured_commit.captured) {
        captured_commit.request = request;
        captured_commit.beats = beats;
        captured_commit.resp = resp;
        captured_commit.txn_uid = txn_uid;
        captured_commit.captured = true;
    }
    const PeerTransferCoverageTable::CommitResult result = coverage.commit(facts);
    committed_valid_bytes += result.newly_covered + result.unaccounted;
    if (owner == nullptr)
        return;
    // The notification crosses the same delivery boundary the mock runtime
    // uses, so one fault-injection point covers both backends.
    MeshDispatcher *dispatcher = owner->runtimeDispatcher();
    for (uint32_t transfer_id : result.completed) {
        if (dispatcher != nullptr)
            dispatcher->routePeerCommit(core_id, transfer_id);
        else
            owner->onTransferCommitted(transfer_id);
    }
}

} // namespace ai_mesh
} // namespace gem5

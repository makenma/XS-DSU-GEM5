#include "dev/ai_mesh/axi_tensor_dma_engine.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/statistics.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "params/AxiTensorDmaEngine.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

uint64_t
tileStrideOf(const RuntimeArch::Region &region)
{
    return region.tile_stride ? region.tile_stride : region.tile_bytes;
}

} // anonymous namespace

AxiTensorDmaEngine::AxiTensorDmaEngine(const Params &p)
    : DmaEngineBase(p),
      bridge(p.bridge),
      data_bus_bytes(p.data_bus_bytes),
      max_burst_beats(p.max_burst_beats),
      setup_cycles(p.setup_cycles),
      descriptor_queue_depth(p.descriptor_queue_depth),
      max_outstanding_bursts(p.max_outstanding_bursts),
      axi_id_count(p.axi_id_count),
      tick_event(this)
{
    for (uint32_t id = 0; id < axi_id_count; id++) {
        free_read_ids.push_back(uint16_t(id));
        free_write_ids.push_back(uint16_t(id));
    }
}

AxiTensorDmaEngine::~AxiTensorDmaEngine() = default;

void
AxiTensorDmaEngine::regStats()
{
    ClockedObject::regStats();
}

void
AxiTensorDmaEngine::startup()
{
    ClockedObject::startup();
    fatal_if(bridge == nullptr, "%s: no bridge bound", name());
    bridge->setReadBeatSink(this, &AxiTensorDmaEngine::readBeatTrampoline);
    bridge->setWriteDoneSink(this, &AxiTensorDmaEngine::writeDoneTrampoline);
    bridge->setProtocolErrorSink(this,
                                 &AxiTensorDmaEngine::protocolErrorTrampoline);
}

void
AxiTensorDmaEngine::bindOwner(MeshDummyCore *core, const RuntimeArch *a)
{
    owner = core;
    arch = a;
}

void
AxiTensorDmaEngine::bindFillPattern(uint32_t command_id, uint64_t pattern)
{
    fill_patterns[command_id] = pattern;
}

bool
AxiTensorDmaEngine::idle() const
{
    return read_queue.empty() && write_queue.empty() &&
           live_read_bursts.empty() && live_write_bursts.empty() &&
           pending_local_events == 0;
}

bool
AxiTensorDmaEngine::submit(const DecodedDmaDescriptor &descriptor,
                           Tick issue_tick)
{
    const bool is_fill = descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL;
    const bool is_read = descriptor.kind == mesh_abi::kDmaKindLOAD ||
                         descriptor.kind == mesh_abi::kDmaKindPREFETCH;
    // FILL shares the finite write-engine queue (spec 4.3); its rows are
    // scheduled one SRAM write service each.
    auto &queue = is_read ? read_queue : write_queue;
    if (queue.size() >= descriptor_queue_depth)
        return false;

    DescriptorState state;
    state.descriptor = descriptor;
    state.ready_tick =
        issue_tick + Cycles(setup_cycles) * clockPeriod();
    // Payload digests and timing records are per-completion; byte counters
    // accumulate across program instances, per-descriptor state must not.
    payload_state.erase(descriptor.descriptor_id);
    timings.erase(descriptor.descriptor_id);
    if (descriptor.useful_bytes == 0)
        state.bursts_total = 0;  // zero-length: every kind, no SRAM service
    else if (is_fill)
        state.bursts_total = descriptor.rows;
    else
        state.bursts_total = uint32_t(planOf(descriptor).size());
    queue.push_back(state);
    scheduleTick();
    return true;
}

std::vector<AxiBurst>
AxiTensorDmaEngine::planOf(const DecodedDmaDescriptor &descriptor) const
{
    // The AXI address is always the remote endpoint: LOAD/PREFETCH plan on
    // the source, STORE/P2P plan on the destination.
    const bool is_read = descriptor.kind == mesh_abi::kDmaKindLOAD ||
                         descriptor.kind == mesh_abi::kDmaKindPREFETCH;
    const DecodedDmaEndpoint &remote =
        is_read ? descriptor.src : descriptor.dst;
    const RuntimeArch::Region *region = arch->region(remote.region_id);
    fatal_if(!region, "dma remote region unresolved");
    const uint32_t limit = std::min(
        max_burst_beats, uint32_t(descriptor.max_burst_beats));
    const uint64_t stride =
        is_read ? descriptor.src_stride_bytes : descriptor.dst_stride_bytes;
    std::vector<AxiBurst> plan;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t row_abs =
            region->base + uint64_t(remote.owner_core) * tileStrideOf(*region) +
            remote.offset_bytes + uint64_t(row) * stride;
        for (auto burst : splitBursts(row_abs, descriptor.row_bytes,
                                      data_bus_bytes, limit))
            plan.push_back(burst);
    }
    return plan;
}

ActualTraffic &
AxiTensorDmaEngine::rowOf(uint32_t descriptor_id)
{
    return actual[descriptor_id];
}

void
AxiTensorDmaEngine::notePayload(uint32_t descriptor_id, const uint8_t *data,
                                uint64_t size)
{
    auto &state = payload_state[descriptor_id];
    if (state.first == 0 && state.second == 0) {
        state.first = 0xCBF29CE484222325ull;
        state.second = 0x9E3779B97F4A7C15ull;
    }
    for (uint64_t i = 0; i < size; i++) {
        state.first = (state.first ^ data[i]) * 0x100000001B3ull;
        state.second =
            (state.second + ((state.first >> 31) ^ data[i])) *
            0xBF58476D1CE4E5B9ull;
    }
}

void
AxiTensorDmaEngine::scheduleTick()
{
    if (!tick_event.scheduled())
        schedule(&tick_event, clockEdge() + 1);
}

void
AxiTensorDmaEngine::tick()
{
    if (!read_queue.empty())
        driveReadDescriptor();
    if (!write_queue.empty())
        driveWriteDescriptor();

    if (!read_queue.empty() || !write_queue.empty() ||
        !live_read_bursts.empty() || !live_write_bursts.empty() ||
        pending_local_events > 0)
        scheduleTick();
}

void
AxiTensorDmaEngine::driveReadDescriptor()
{
    DescriptorState &state = read_queue.front();
    if (state.terminal || curTick() < state.ready_tick)
        return;
    if (state.bursts_total == 0) {
        // Zero-byte descriptor: no AXI, at least one core cycle of control.
        finishDescriptor(true, DmaStatus::OK);
        return;
    }
    if (state.burst_ordinals.size() >= state.bursts_total)
        return;
    if (live_read_bursts.size() >= max_outstanding_bursts)
        return;

    const std::vector<AxiBurst> plan = planOf(state.descriptor);
    const AxiBurst &burst_plan = plan[state.burst_ordinals.size()];

    // Destination: local SRAM offset of this burst's logical interval.
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    const RuntimeArch::Region *src_region =
        arch->region(descriptor.src.region_id);
    uint64_t dst_base = 0;
    bool found = false;
    for (uint32_t row = 0; row < descriptor.rows && !found; row++) {
        const uint64_t src_abs =
            src_region->base +
            uint64_t(descriptor.src.owner_core) *
                tileStrideOf(*src_region) +
            descriptor.src.offset_bytes +
            uint64_t(row) * descriptor.src_stride_bytes;
        if (burst_plan.logical_start >= src_abs &&
            burst_plan.logical_start < src_abs + descriptor.row_bytes) {
            dst_base = descriptor.dst.offset_bytes +
                       uint64_t(row) * descriptor.dst_stride_bytes +
                       (burst_plan.logical_start - src_abs);
            found = true;
        }
    }
    fatal_if(!found, "read burst logical start outside descriptor rows");

    if (free_read_ids.empty())
        return; // every AXI ID still has a live response: backpressure
    const uint16_t axi_id = free_read_ids.front();
    free_read_ids.pop_front();
    uint64_t ordinal = 0;
    if (!bridge->submitRead(burst_plan.beat_base, burst_plan.beats, axi_id,
                            descriptor.qos, &ordinal)) {
        free_read_ids.push_back(axi_id);
        return;
    }
    ReadBurst burst;
    burst.plan = burst_plan;
    burst.dst_base = dst_base;
    burst.descriptor_id = descriptor.descriptor_id;
    burst.axi_id = axi_id;
    burst.packed.reserve(burst_plan.useful_bytes);
    live_read_bursts.emplace(ordinal, std::move(burst));
    state.burst_ordinals.push_back(ordinal);
    read_bursts_submitted++;
    DescriptorTiming &timing = timings[descriptor.descriptor_id];
    if (timing.first_ar_tick == 0)
        timing.first_ar_tick = curTick();
}

void
AxiTensorDmaEngine::driveWriteDescriptor()
{
    DescriptorState &state = write_queue.front();
    if (state.terminal || curTick() < state.ready_tick)
        return;
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    if (state.bursts_total == 0) {
        // Zero-length descriptor of every write kind (FILL included):
        // legal, zero traffic, completes at the normal completion point.
        finishDescriptor(false, DmaStatus::OK);
        return;
    }
    if (descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL) {
        driveFill(state);
        return;
    }
    if (state.burst_ordinals.size() >= state.bursts_total)
        return;
    if (live_write_bursts.size() >= max_outstanding_bursts)
        return;

    const std::vector<AxiBurst> plan = planOf(descriptor);
    const uint32_t burst_index = uint32_t(state.burst_ordinals.size());
    const AxiBurst &burst_plan = plan[burst_index];

    if (!state.prepared_beats.has_value()) {
        if (state.prepare_pending)
            return; // SRAM read service in flight for this burst
        // The burst's logical interval lives in remote (destination)
        // address space; the source mapping uses the same logical offset
        // within the descriptor row.
        const RuntimeArch::Region *dst_region =
            arch->region(descriptor.dst.region_id);
        uint64_t row_src_local = 0;
        bool found = false;
        for (uint32_t row = 0; row < descriptor.rows && !found; row++) {
            const uint64_t dst_abs =
                dst_region->base +
                uint64_t(descriptor.dst.owner_core) *
                    tileStrideOf(*dst_region) +
                descriptor.dst.offset_bytes +
                uint64_t(row) * descriptor.dst_stride_bytes;
            if (burst_plan.logical_start >= dst_abs &&
                burst_plan.logical_start < dst_abs + descriptor.row_bytes) {
                row_src_local = descriptor.src.offset_bytes +
                                uint64_t(row) * descriptor.src_stride_bytes +
                                (burst_plan.logical_start - dst_abs);
                found = true;
            }
        }
        fatal_if(!found, "write burst logical start outside descriptor rows");

        // The local SRAM read must complete before any W data of this
        // burst is offered to the bridge (spec 7.4): schedule the beat
        // build on the read-service completion event.
        const auto service = owner->reserveSramService(
            row_src_local, burst_plan.useful_bytes, false);
        const Tick ready = curTick() + service.stall_ticks;
        state.prepare_pending = true;
        pending_local_events++;
        auto *event = new WritePrepareEvent(
            this, descriptor.descriptor_id, burst_index, row_src_local);
        schedule(event, ready > clockEdge(Cycles(1)) ? ready
                                                     : clockEdge(Cycles(1)));
        return;
    }

    if (free_write_ids.empty())
        return; // every AXI ID still has a live response: backpressure
    const uint16_t axi_id = free_write_ids.front();
    free_write_ids.pop_front();
    uint64_t ordinal = 0;
    if (!bridge->submitWrite(burst_plan.beat_base, burst_plan.beats, axi_id,
                             descriptor.qos, *state.prepared_beats, &ordinal)) {
        free_write_ids.push_back(axi_id);
        return;
    }
    state.prepared_beats.reset();

    WriteBurst burst;
    burst.plan = burst_plan;
    burst.descriptor_id = descriptor.descriptor_id;
    burst.kind = descriptor.kind;
    burst.axi_id = axi_id;
    live_write_bursts.emplace(ordinal, burst);
    state.burst_ordinals.push_back(ordinal);
    write_bursts_submitted++;
    DescriptorTiming &timing = timings[descriptor.descriptor_id];
    if (timing.first_aw_tick == 0)
        timing.first_aw_tick = curTick();
    if (timing.first_w_tick == 0)
        timing.first_w_tick = curTick();
    write_valid_bytes += burst_plan.useful_bytes;
    ActualTraffic &submit_row = rowOf(descriptor.descriptor_id);
    if (descriptor.kind == mesh_abi::kDmaKindSTORE)
        submit_row.write_bursts++;
    else
        submit_row.p2p_bursts++;
}

void
AxiTensorDmaEngine::prepareWriteBurst(uint32_t descriptor_id,
                                      uint32_t burst_index,
                                      uint64_t row_src_local)
{
    pending_local_events--;
    fatal_if(write_queue.empty() ||
                 write_queue.front().descriptor.descriptor_id != descriptor_id,
             "write prepare for non-head descriptor %u", descriptor_id);
    DescriptorState &state = write_queue.front();
    fatal_if(!state.prepare_pending, "unexpected write prepare event");
    state.prepare_pending = false;

    const std::vector<AxiBurst> plan = planOf(state.descriptor);
    const AxiBurst &burst_plan = plan[burst_index];

    std::vector<uint8_t> logical(burst_plan.useful_bytes);
    fatal_if(!owner->functionalSramRead(row_src_local,
                                        burst_plan.useful_bytes,
                                        logical.data()),
             "write burst source read escapes SRAM");
    notePayload(descriptor_id, logical.data(), logical.size());
    if (curTick() >= timings[descriptor_id].local_commit_tick)
        timings[descriptor_id].local_commit_tick = curTick();

    std::vector<axi::AxiWBeat> beats(burst_plan.beats);
    uint64_t consumed = 0;
    for (uint32_t b = 0; b < burst_plan.beats; b++) {
        const uint64_t lane_base =
            burst_plan.beat_base + uint64_t(b) * data_bus_bytes;
        beats[b].functionalData.assign(data_bus_bytes, 0);
        uint64_t strobe = 0;
        for (uint32_t lane = 0; lane < data_bus_bytes; lane++) {
            const uint64_t addr = lane_base + lane;
            if (addr >= burst_plan.logical_start &&
                addr < burst_plan.logical_start + burst_plan.useful_bytes) {
                strobe |= uint64_t(1) << lane;
                beats[b].functionalData[lane] = logical[consumed++];
            }
        }
        beats[b].byteStrobe = strobe;
        beats[b].last = b + 1 == burst_plan.beats;
        beats[b].payloadDigest = axi::payloadDigest(
            beats[b].functionalData);
    }
    fatal_if(consumed != burst_plan.useful_bytes,
             "write burst lane packing bug");
    state.prepared_beats = std::move(beats);
    scheduleTick();
}

void
AxiTensorDmaEngine::driveFill(DescriptorState &state)
{
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    if (state.prepare_pending)
        return; // a row's SRAM write service is in flight
    if (state.bursts_done >= state.bursts_total)
        return;
    const uint32_t next_row = state.bursts_done;
    const uint64_t dst_off = descriptor.dst.offset_bytes +
                             uint64_t(next_row) * descriptor.dst_stride_bytes;
    const auto service = owner->reserveSramService(
        dst_off, descriptor.row_bytes, true);
    const Tick ready = curTick() + service.stall_ticks;
    state.prepare_pending = true;
    pending_local_events++;
    auto *event = new FillRowEvent(this, descriptor.descriptor_id, next_row);
    schedule(event, ready > clockEdge(Cycles(1)) ? ready
                                                 : clockEdge(Cycles(1)));
}

void
AxiTensorDmaEngine::commitFillRow(uint32_t descriptor_id, uint32_t row)
{
    pending_local_events--;
    fatal_if(write_queue.empty() ||
                 write_queue.front().descriptor.descriptor_id != descriptor_id,
             "fill commit for non-head descriptor %u", descriptor_id);
    DescriptorState &state = write_queue.front();
    fatal_if(!state.prepare_pending, "unexpected fill row event");
    state.prepare_pending = false;
    const DecodedDmaDescriptor &descriptor = state.descriptor;

    auto it = fill_patterns.find(descriptor.command_id);
    fatal_if(it == fill_patterns.end(),
             "fill pattern not bound for command %u", descriptor.command_id);
    const uint64_t pattern = it->second;
    std::vector<uint8_t> bytes(descriptor.row_bytes);
    for (uint64_t i = 0; i < descriptor.row_bytes; i++)
        bytes[i] = static_cast<uint8_t>((pattern >> (8 * (i % 8))) & 0xFF);
    const uint64_t dst_off = descriptor.dst.offset_bytes +
                             uint64_t(row) * descriptor.dst_stride_bytes;
    fatal_if(!owner->functionalSramWrite(dst_off, descriptor.row_bytes,
                                         bytes.data()),
             "fill destination write out of bounds");
    notePayload(descriptor_id, bytes.data(), bytes.size());
    ActualTraffic &row_stats = rowOf(descriptor_id);
    row_stats.fill_bytes += descriptor.row_bytes;
    if (curTick() >= timings[descriptor_id].local_commit_tick)
        timings[descriptor_id].local_commit_tick = curTick();

    state.bursts_done++;
    if (state.bursts_done >= state.bursts_total)
        finishDescriptor(false, DmaStatus::OK);
    else
        scheduleTick();
}

void
AxiTensorDmaEngine::readBeatTrampoline(void *ctx, uint64_t ordinal,
                                       uint16_t beat_index, bool last,
                                       axi::AxiResp resp, const uint8_t *data)
{
    static_cast<AxiTensorDmaEngine *>(ctx)->onReadBeat(ordinal, beat_index,
                                                       last, resp, data);
}

void
AxiTensorDmaEngine::writeDoneTrampoline(void *ctx, uint64_t ordinal,
                                        axi::AxiResp resp)
{
    static_cast<AxiTensorDmaEngine *>(ctx)->onWriteDone(ordinal, resp);
}

void
AxiTensorDmaEngine::protocolErrorTrampoline(void *ctx, bool read_direction)
{
    static_cast<AxiTensorDmaEngine *>(ctx)->onProtocolError(read_direction);
}

void
AxiTensorDmaEngine::onReadBeat(uint64_t burst_ordinal, uint16_t beat_index,
                               bool last, axi::AxiResp resp,
                               const uint8_t *data)
{
    auto it = live_read_bursts.find(burst_ordinal);
    if (it == live_read_bursts.end()) {
        // Late beat of an already-terminal descriptor: count and drop.
        protocol_dropped_beats++;
        return;
    }
    ReadBurst &burst = it->second;
    const AxiBurst &plan = burst.plan;

    // Error state latches on ANY beat of the burst (spec 7.4): the burst
    // commits nothing and all of its useful bytes count as discarded.
    if (resp != axi::AxiResp::Okay)
        burst.errored = true;

    if (!burst.errored) {
        // Pack only the logical lanes; overfetched padding is dropped.
        const uint64_t lane_base =
            plan.beat_base + uint64_t(beat_index) * data_bus_bytes;
        for (uint32_t lane = 0; lane < data_bus_bytes; lane++) {
            const uint64_t addr = lane_base + lane;
            if (addr >= plan.logical_start &&
                addr < plan.logical_start + plan.useful_bytes)
                burst.packed.push_back(data[lane]);
        }
    }

    if (!last)
        return;
    burst.last_r_tick = curTick();
    timings[burst.descriptor_id].last_r_tick = curTick();

    if (burst.errored) {
        // Error bursts drain every beat (delivered above) and commit zero
        // bytes; retire immediately, no SRAM service is scheduled.
        read_bursts_errored++;
        ActualTraffic &row = rowOf(burst.descriptor_id);
        row.read_discarded_bytes += plan.useful_bytes;
        row.read_bursts++;
        read_bursts_completed++;
        free_read_ids.push_back(burst.axi_id);
        live_read_bursts.erase(burst_ordinal);
        retireBurst(true, burst_ordinal, true);
        return;
    }

    // The functional commit and the burst's retirement happen on the SRAM
    // write-service completion event, not at RLAST (spec 7.4).
    const auto service = owner->reserveSramService(
        burst.dst_base, burst.packed.size(), true);
    const Tick ready = curTick() + service.stall_ticks;
    pending_local_events++;
    auto *event = new ReadCommitEvent(this, burst_ordinal);
    schedule(event, ready > clockEdge(Cycles(1)) ? ready
                                                 : clockEdge(Cycles(1)));
}

void
AxiTensorDmaEngine::commitReadBurst(uint64_t burst_ordinal)
{
    pending_local_events--;
    auto it = live_read_bursts.find(burst_ordinal);
    fatal_if(it == live_read_bursts.end(),
             "read commit for unknown burst ordinal %llu",
             (unsigned long long)burst_ordinal);
    ReadBurst &burst = it->second;

    fatal_if(!owner->functionalSramWrite(burst.dst_base,
                                         burst.packed.size(),
                                         burst.packed.data()),
             "read beat commit escapes SRAM");
    read_valid_bytes += burst.packed.size();
    notePayload(burst.descriptor_id, burst.packed.data(),
                burst.packed.size());
    ActualTraffic &row = rowOf(burst.descriptor_id);
    row.read_bytes += burst.packed.size();
    row.read_bursts++;
    read_bursts_completed++;
    // Keep the RLAST of the burst whose local commit is the latest, so the
    // exported pair satisfies rlast <= commit <= done exactly.
    if (curTick() >= timings[burst.descriptor_id].local_commit_tick) {
        timings[burst.descriptor_id].local_commit_tick = curTick();
        timings[burst.descriptor_id].last_r_tick = burst.last_r_tick;
    }
    free_read_ids.push_back(burst.axi_id);
    live_read_bursts.erase(burst_ordinal);
    retireBurst(true, burst_ordinal, false);
}

void
AxiTensorDmaEngine::onProtocolError(bool read_direction)
{
    // Unknown-ID response: the offending direction's head descriptor takes
    // the structured error terminal; late beats of its bursts are dropped.
    auto &queue = read_direction ? read_queue : write_queue;
    if (queue.empty() || queue.front().terminal)
        return;
    const DecodedDmaDescriptor descriptor = queue.front().descriptor;
    if (read_direction) {
        for (auto it = live_read_bursts.begin();
             it != live_read_bursts.end();) {
            if (it->second.descriptor_id == descriptor.descriptor_id)
                it = live_read_bursts.erase(it);
            else
                ++it;
        }
    } else {
        for (auto it = live_write_bursts.begin();
             it != live_write_bursts.end();) {
            if (it->second.descriptor_id == descriptor.descriptor_id)
                it = live_write_bursts.erase(it);
            else
                ++it;
        }
    }
    finishDescriptor(read_direction,
                     read_direction ? DmaStatus::AXI_READ_ERROR
                                    : DmaStatus::AXI_WRITE_ERROR);
}

void
AxiTensorDmaEngine::onWriteDone(uint64_t burst_ordinal, axi::AxiResp resp)
{
    auto it = live_write_bursts.find(burst_ordinal);
    if (it == live_write_bursts.end()) {
        protocol_dropped_beats++;
        return;
    }
    const WriteBurst &burst = it->second;
    const bool errored = resp != axi::AxiResp::Okay;
    ActualTraffic &row = rowOf(burst.descriptor_id);
    if (errored) {
        write_bursts_errored++;
        row.write_drained_uncommitted_bytes += burst.plan.useful_bytes;
    } else {
        // Committed bytes are accounted only on the successful B; faulted
        // bursts drained all W beats but committed zero bytes.
        if (burst.kind == mesh_abi::kDmaKindSTORE)
            row.write_bytes += burst.plan.useful_bytes;
        else
            row.p2p_bytes += burst.plan.useful_bytes;
    }
    write_bursts_completed++;
    DescriptorTiming &timing = timings[burst.descriptor_id];
    if (timing.first_b_tick == 0)
        timing.first_b_tick = curTick();
    free_write_ids.push_back(burst.axi_id);
    live_write_bursts.erase(burst_ordinal);
    retireBurst(false, burst_ordinal, errored);
}

void
AxiTensorDmaEngine::retireBurst(bool read, uint64_t burst_ordinal,
                               bool errored)
{
    auto &queue = read ? read_queue : write_queue;
    fatal_if(queue.empty() || queue.front().terminal,
             "burst retired without a live head descriptor");
    DescriptorState &state = queue.front();
    fatal_if(std::find(state.burst_ordinals.begin(),
                       state.burst_ordinals.end(),
                       burst_ordinal) == state.burst_ordinals.end(),
             "burst ordinal %llu does not belong to the head descriptor",
             (unsigned long long)burst_ordinal);
    state.bursts_done++;
    if (errored)
        state.bursts_errored++;
    if (state.bursts_done < state.bursts_total)
        return;
    const DmaStatus status = state.bursts_errored
        ? (read ? DmaStatus::AXI_READ_ERROR : DmaStatus::AXI_WRITE_ERROR)
        : DmaStatus::OK;
    finishDescriptor(read, status);
}

void
AxiTensorDmaEngine::finishDescriptor(bool read, DmaStatus status)
{
    auto &queue = read ? read_queue : write_queue;
    fatal_if(queue.empty(), "descriptor finished without a queue entry");
    // The completion record is copied out before the queue entry is
    // destroyed; every later use reads the local copy.
    const std::vector<uint64_t> burst_ordinals = queue.front().burst_ordinals;
    const DecodedDmaDescriptor descriptor = queue.front().descriptor;
    const uint32_t descriptor_id = descriptor.descriptor_id;
    const uint32_t command_id = descriptor.command_id;
    const uint32_t completion_event = descriptor.completion_event;
    queue.pop_front();

    ActualTraffic &row = rowOf(descriptor_id);
    row.error_code = status == DmaStatus::AXI_READ_ERROR ? 1
                  : status == DmaStatus::AXI_WRITE_ERROR ? 2 : 0;
    DescriptorTiming &timing = timings[descriptor_id];
    timing.done_tick = curTick();
    if (descriptor.useful_bytes == 0) {
        // Zero-length: no AXI and no SRAM service tick is exported.
        timing.first_ar_tick = 0;
        timing.first_aw_tick = 0;
        timing.first_w_tick = 0;
        timing.first_b_tick = 0;
        timing.last_r_tick = 0;
        timing.local_commit_tick = 0;
    }
    // Adopt the bridge's real per-ordinal handshake ticks, scoped by the
    // descriptor direction: reads export AR/R only, writes export
    // local-read/AW/W/B, FILL exports no AXI tick at all.
    const bool is_read_kind =
        descriptor.kind == mesh_abi::kDmaKindLOAD ||
        descriptor.kind == mesh_abi::kDmaKindPREFETCH;
    const bool is_fill = descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL;
    if (!is_fill) {
        Tick min_addr = 0, min_w = 0, min_resp = 0, max_resp = 0;
        for (uint64_t ordinal : burst_ordinals) {
            const auto *ticks = bridge->ordinalTicks(ordinal);
            if (!ticks)
                continue;
            if (min_addr == 0 ||
                (ticks->addr_accept && ticks->addr_accept < min_addr))
                min_addr = ticks->addr_accept;
            if (min_w == 0 || (ticks->first_w && ticks->first_w < min_w))
                min_w = ticks->first_w;
            if (ticks->resp_last) {
                if (min_resp == 0 || ticks->resp_last < min_resp)
                    min_resp = ticks->resp_last;
                if (ticks->resp_last > max_resp)
                    max_resp = ticks->resp_last;
            }
        }
        if (is_read_kind) {
            if (min_addr)
                timing.first_ar_tick = min_addr;
            if (max_resp)
                timing.last_r_tick = max_resp;
        } else {
            if (min_addr)
                timing.first_aw_tick = min_addr;
            if (min_w)
                timing.first_w_tick = min_w;
            if (min_resp)
                timing.first_b_tick = min_resp;
        }
    }
    if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
        descriptor.useful_bytes == 0 && owner)
        owner->notifyPeerCommit(descriptor.dst.owner_core,
                                descriptor.transfer_id);
    notifyOwner(descriptor_id, command_id, completion_event, status);
}

void
AxiTensorDmaEngine::notifyOwner(uint32_t descriptor_id, uint32_t command_id,
                                uint32_t completion_event, DmaStatus status)
{
    auto it = payload_state.find(descriptor_id);
    if (it != payload_state.end()) {
        char digest[40];
        snprintf(digest, sizeof(digest), "%016llx-%016llx",
                 (unsigned long long)it->second.first,
                 (unsigned long long)it->second.second);
        rowOf(descriptor_id).payload_digest = digest;
    }
    if (owner)
        owner->onDmaCompleted(command_id, completion_event, curTick(),
                              status);
}

} // namespace ai_mesh
} // namespace gem5

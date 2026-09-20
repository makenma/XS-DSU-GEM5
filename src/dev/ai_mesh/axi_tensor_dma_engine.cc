#include "dev/ai_mesh/axi_tensor_dma_engine.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/statistics.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "dev/ai_mesh/axi_garnet_bridge.hh"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_dummy_core.hh"
#include "dev/ai_mesh/mesh_dispatcher.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "params/AxiTensorDmaEngine.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr uint64_t kDigestSeedFirst = 0xCBF29CE484222325ull;
constexpr uint64_t kDigestSeedSecond = 0x9E3779B97F4A7C15ull;

// Rolling content digest over the functionally moved bytes; the same dual-FNV
// construction the mock transport uses, so both backends publish one digest
// semantic.
void
fnvUpdate(uint64_t &first, uint64_t &second, const uint8_t *data, uint64_t size)
{
    for (uint64_t index = 0; index < size; index++) {
        first = (first ^ data[index]) * 0x100000001B3ull;
        second = (second + ((first >> 31) ^ data[index])) *
                 0xBF58476D1CE4E5B9ull;
    }
}

std::string
fnvDigest(uint64_t first, uint64_t second)
{
    char digest[40];
    snprintf(digest, sizeof(digest), "%016llx-%016llx",
             (unsigned long long)first, (unsigned long long)second);
    return digest;
}

} // anonymous namespace

AxiTensorDmaEngine::AxiTensorDmaEngine(const Params &p)
    : DmaEngineBase(p),
      bridge(p.bridge),
      data_bus_bytes(p.data_bus_bytes),
      max_burst_beats(p.max_burst_beats),
      setup_cycles(p.setup_cycles),
      descriptor_queue_depth(p.descriptor_queue_depth),
      segment_queue_depth(p.segment_queue_depth),
      axi_id_count(p.axi_id_count),
      source_bytes_limit(p.source_bytes_limit),
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
AxiTensorDmaEngine::bindOwner(MeshDummyCore *core, const RuntimeArch *)
{
    owner = core;
}

void
AxiTensorDmaEngine::beginInstance()
{
    // The previous instance must have drained: a leftover burst or descriptor
    // would otherwise be matched against the next instance's identities.
    fatal_if(!idle(), "core dma engine is not idle at instance begin");
    bridge->beginInstance();
    read_commit_ticks.clear();
    ar_accept_ticks.clear();
    instance_counter++;
    instance_bursts.clear();
    attribution_by_ordinal.clear();
}

BurstAttribution &
AxiTensorDmaEngine::noteBurst(const DescriptorState &state,
                              const AxiBurst &plan, uint32_t burst_index,
                              uint64_t ordinal, uint16_t axi_id, bool read)
{
    BurstAttribution record;
    record.instance = instance_counter;
    record.core_id = owner == nullptr ? 0 : owner->archCoreId();
    record.command_id = state.key ? state.key->command.command_id : 0;
    record.generation = state.key ? state.key->command.generation : 0;
    record.descriptor_id = state.descriptor.descriptor_id;
    record.burst_index = burst_index;
    record.ordinal = ordinal;
    record.read = read;
    record.axi_id = axi_id;
    record.address = plan.beat_base;
    record.beats = plan.beats;
    record.beat_bytes = data_bus_bytes;
    record.useful_bytes = plan.useful_bytes;
    record.logical_start = plan.logical_start;
    record.issue_tick = curTick();
    attribution_by_ordinal[ordinal] = instance_bursts.size();
    instance_bursts.push_back(record);
    return instance_bursts.back();
}

BurstAttribution *
AxiTensorDmaEngine::burstAttribution(uint64_t ordinal)
{
    auto found = attribution_by_ordinal.find(ordinal);
    if (found == attribution_by_ordinal.end())
        return nullptr;
    return &instance_bursts[found->second];
}

void
AxiTensorDmaEngine::finishBurstAttribution(const DescriptorState &state,
                                           Tick done_tick)
{
    for (uint64_t ordinal : state.burst_ordinals) {
        BurstAttribution *record = burstAttribution(ordinal);
        if (record != nullptr)
            record->done_tick = done_tick;
    }
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

uint32_t
AxiTensorDmaEngine::pendingReadReservations() const
{
    uint32_t count = 0;
    for (const auto &[ordinal, burst] : live_read_bursts)
        count += burst.service_state == ReadServiceState::AwaitingReservation;
    return count;
}

uint32_t
AxiTensorDmaEngine::scheduledReadCommits() const
{
    uint32_t count = 0;
    for (const auto &[ordinal, burst] : live_read_bursts)
        count += burst.service_state == ReadServiceState::CommitScheduled;
    return count;
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
    fatal_if(!owner, "dma engine has no core owner for descriptor %u",
             descriptor.descriptor_id);
    state.key = owner->frozenDescriptorKey(descriptor.command_id,
                                          descriptor.descriptor_id);
    fatal_if(!state.key,
             "admitted descriptor %u has no frozen execution identity",
             descriptor.descriptor_id);
    // Payload digests and timing records are per-completion; byte counters
    // accumulate across program instances, per-descriptor state must not.
    payload_state[descriptor.descriptor_id] =
        std::make_pair(kDigestSeedFirst, kDigestSeedSecond);
    timings.erase(descriptor.descriptor_id);
    if (descriptor.useful_bytes == 0)
        state.bursts_total = 0;  // zero-length: every kind, no SRAM service
    else if (is_fill)
        state.bursts_total = descriptor.rows;
    else {
        state.plan = planOf(descriptor);
        state.bursts_total = uint32_t(state.plan.size());
    }
    owner->recordDescriptorSubmission(*state.key, curTick(), state.ready_tick);
    queue.push_back(std::move(state));
    scheduleTick();
    return true;
}

std::vector<AxiBurst>
AxiTensorDmaEngine::planOf(const DecodedDmaDescriptor &descriptor) const
{
    // The AXI address is always the remote endpoint: LOAD/PREFETCH plan on
    // the source, STORE/P2P plan on the destination.  The row-0 anchor is the
    // admitted binding address, so dispatch relocation is honoured and no
    // backend-specific placement formula is maintained here.
    const bool is_read = descriptor.kind == mesh_abi::kDmaKindLOAD ||
                         descriptor.kind == mesh_abi::kDmaKindPREFETCH;
    fatal_if(!owner, "dma engine has no core owner to resolve endpoint %u",
             descriptor.descriptor_id);
    const uint64_t remote_base =
        owner->admittedEndpointAddress(descriptor.descriptor_id, is_read);
    const uint32_t limit = std::min(
        max_burst_beats, uint32_t(descriptor.max_burst_beats));
    const uint64_t stride =
        is_read ? descriptor.src_stride_bytes : descriptor.dst_stride_bytes;
    std::vector<AxiBurst> plan;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t row_abs = remote_base + uint64_t(row) * stride;
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
    fnvUpdate(state.first, state.second, data, size);
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
    for (auto &[ordinal, burst] : live_read_bursts)
        if (burst.service_state == ReadServiceState::AwaitingReservation)
            tryScheduleReadCommit(ordinal, burst);
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
    if (live_read_bursts.size() >= segment_queue_depth)
        return;

    const std::vector<AxiBurst> &plan = state.plan;
    const AxiBurst &burst_plan = plan[state.burst_ordinals.size()];

    // Destination: local SRAM offset of this burst's logical interval.
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    const uint64_t src_row_base =
        owner->admittedEndpointAddress(descriptor.descriptor_id, true);
    const uint64_t dst_row_base =
        owner->admittedLocalOffset(descriptor.descriptor_id, false);
    uint64_t dst_base = 0;
    bool found = false;
    for (uint32_t row = 0; row < descriptor.rows && !found; row++) {
        const uint64_t src_abs =
            src_row_base + uint64_t(row) * descriptor.src_stride_bytes;
        if (burst_plan.logical_start >= src_abs &&
            burst_plan.logical_start < src_abs + descriptor.row_bytes) {
            dst_base = dst_row_base +
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
    noteBurst(state, burst_plan, uint32_t(state.burst_ordinals.size()), ordinal,
              axi_id, true);
    state.burst_ordinals.push_back(ordinal);
    read_bursts_submitted++;
    ++read_ar_accepted;
    ar_accept_ticks.push_back(curTick());
    const uint32_t window = uint32_t(
        read_ar_accepted - read_rlast_consumed);
    if (window > peak_read_window)
        peak_read_window = window;
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
    if (live_write_bursts.size() >= segment_queue_depth)
        return;

    const std::vector<AxiBurst> &plan = state.plan;
    const uint32_t burst_index = uint32_t(state.burst_ordinals.size());
    const AxiBurst &burst_plan = plan[burst_index];

    if (!state.prepared_beats.has_value()) {
        if (state.prepare_pending)
            return; // SRAM read service in flight for this burst
        // The burst's logical interval lives in remote (destination)
        // address space; the source mapping uses the same logical offset
        // within the descriptor row.
        const uint64_t dst_row_base =
            owner->admittedEndpointAddress(descriptor.descriptor_id, false);
        const uint64_t src_row_base =
            owner->admittedLocalOffset(descriptor.descriptor_id, true);
        uint64_t row_src_local = 0;
        bool found = false;
        for (uint32_t row = 0; row < descriptor.rows && !found; row++) {
            const uint64_t dst_abs =
                dst_row_base + uint64_t(row) * descriptor.dst_stride_bytes;
            if (burst_plan.logical_start >= dst_abs &&
                burst_plan.logical_start < dst_abs + descriptor.row_bytes) {
                row_src_local = src_row_base +
                                uint64_t(row) * descriptor.src_stride_bytes +
                                (burst_plan.logical_start - dst_abs);
                found = true;
            }
        }
        fatal_if(!found, "write burst logical start outside descriptor rows");

        // The local SRAM read must complete before any W data of this
        // burst is offered to the bridge (spec 7.4): schedule the beat
        // build on the read-service completion event.
        const auto service = owner->tryReserveSramService(
            row_src_local, burst_plan.useful_bytes, false);
        if (!service)
            return;
        const Tick ready = curTick() + service->stall_ticks;
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
    noteBurst(state, burst_plan, burst_index, ordinal, axi_id, false);
    burst.axi_id = axi_id;
    live_write_bursts.emplace(ordinal, burst);
    state.burst_ordinals.push_back(ordinal);
    write_bursts_submitted++;
    DescriptorTiming &timing = timings[descriptor.descriptor_id];
    if (timing.first_aw_tick == 0)
        timing.first_aw_tick = curTick();
    if (timing.first_w_tick == 0)
        timing.first_w_tick = curTick();
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

    const std::vector<AxiBurst> &plan = state.plan;
    const AxiBurst &burst_plan = plan[burst_index];

    std::vector<uint8_t> logical(burst_plan.useful_bytes);
    fatal_if(!owner->functionalSramRead(row_src_local,
                                        burst_plan.useful_bytes,
                                        logical.data()),
             "write burst source read escapes SRAM");
    state.ordered_burst_payload.emplace(burst_plan.logical_start, logical);
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
    const uint64_t dst_off =
        owner->admittedLocalOffset(descriptor.descriptor_id, false) +
        uint64_t(next_row) * descriptor.dst_stride_bytes;
    const auto service = owner->tryReserveSramService(
        dst_off, descriptor.row_bytes, true);
    if (!service)
        return;
    const Tick ready = curTick() + service->stall_ticks;
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
    for (uint64_t i = 0; i < descriptor.row_bytes; i++) {
        const uint64_t offset = uint64_t(row) * descriptor.row_bytes + i;
        bytes[i] = static_cast<uint8_t>((pattern >> (8 * (offset % 8))) & 0xFF);
    }
    const uint64_t dst_off =
        owner->admittedLocalOffset(descriptor.descriptor_id, false) +
        uint64_t(row) * descriptor.dst_stride_bytes;
    fatal_if(!owner->functionalSramWrite(dst_off, descriptor.row_bytes,
                                         bytes.data()),
             "fill destination write out of bounds");
    notePayload(descriptor_id, bytes.data(), bytes.size());
    state.contribution.fill_bytes += descriptor.row_bytes;
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
AxiTensorDmaEngine::releaseReadAxiId(ReadBurst &burst)
{
    if (burst.axiIdReleased)
        return;
    burst.axiIdReleased = true;
    free_read_ids.push_back(burst.axi_id);
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
    ++read_rlast_consumed;
    if (first_rlast_tick == 0)
        first_rlast_tick = curTick();
    burst.last_r_tick = curTick();
    if (BurstAttribution *record = burstAttribution(burst_ordinal)) {
        record->response_tick = curTick();
        record->retire_tick = curTick();
        record->errored = burst.errored;
    }
    timings[burst.descriptor_id].last_r_tick = curTick();

    // The AXI read transaction ends at RLAST: the AXI ID returns to the
    // pool here for both the normal and the error path, so a new AR can be
    // accepted while this burst still waits for its local SRAM commit
    // (spec 7.4 vs the AXI outstanding window, which the initiator
    // adapter owns).  The burst itself stays ordinal-keyed until commit.
    releaseReadAxiId(burst);

    if (burst.errored) {
        // Error bursts drain every beat (delivered above) and commit zero
        // bytes; retire immediately, no SRAM service is scheduled.
        read_bursts_errored++;
        DescriptorState &state = read_queue.front();
        state.discarded_bytes += plan.useful_bytes;
        read_bursts_completed++;
        live_read_bursts.erase(burst_ordinal);
        retireBurst(true, burst_ordinal, true);
        return;
    }

    // The functional commit and the burst's retirement happen on the SRAM
    // write-service completion event, not at RLAST (spec 7.4).
    burst.service_state = ReadServiceState::AwaitingReservation;
    tryScheduleReadCommit(burst_ordinal, burst);
    scheduleTick();
}

void
AxiTensorDmaEngine::tryScheduleReadCommit(uint64_t burst_ordinal,
                                          ReadBurst &burst)
{
    fatal_if(burst.service_state != ReadServiceState::AwaitingReservation,
             "read SRAM reservation attempted in the wrong state");
    const auto service = owner->tryReserveSramService(
        burst.dst_base, burst.packed.size(), true);
    if (!service)
        return;
    burst.service_state = ReadServiceState::CommitScheduled;
    const Tick ready = curTick() + service->stall_ticks;
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

    fatal_if(burst.service_state != ReadServiceState::CommitScheduled,
             "read SRAM commit attempted in the wrong state");

    fatal_if(!owner->functionalSramWrite(burst.dst_base,
                                         burst.packed.size(),
                                         burst.packed.data()),
             "read beat commit escapes SRAM");
    read_commit_ticks.emplace(burst_ordinal, curTick());
    if (BurstAttribution *record = burstAttribution(burst_ordinal))
        record->local_commit_tick = curTick();
    read_valid_bytes += burst.packed.size();
    DescriptorState &state = read_queue.front();
    state.ordered_burst_payload.emplace(burst.plan.logical_start, burst.packed);
    state.committed_bursts.insert(burst.plan.logical_start);
    state.contribution.read_bytes += burst.packed.size();
    state.contribution.read_bursts++;
    read_bursts_completed++;
    // Keep the RLAST of the burst whose local commit is the latest, so the
    // exported pair satisfies rlast <= commit <= done exactly.
    if (curTick() >= timings[burst.descriptor_id].local_commit_tick) {
        timings[burst.descriptor_id].local_commit_tick = curTick();
        timings[burst.descriptor_id].last_r_tick = burst.last_r_tick;
    }
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
    DescriptorState &state = write_queue.front();
    if (BurstAttribution *record = burstAttribution(burst_ordinal)) {
        record->response_tick = curTick();
        record->retire_tick = curTick();
        record->errored = errored;
    }
    if (errored) {
        write_bursts_errored++;
        state.drained_bytes += burst.plan.useful_bytes;
    } else {
        write_valid_bytes += burst.plan.useful_bytes;
        // Committed bytes are accounted only on the successful B; faulted
        // bursts drained all W beats but committed zero bytes.
        state.committed_bursts.insert(burst.plan.logical_start);
        if (burst.kind == mesh_abi::kDmaKindSTORE) {
            state.contribution.write_bytes += burst.plan.useful_bytes;
            state.contribution.write_bursts++;
        } else {
            state.contribution.p2p_bytes += burst.plan.useful_bytes;
            state.contribution.p2p_bursts++;
        }
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
    // The completion record is owned locally before the queue entry is
    // destroyed; every later use reads the local copy.
    DescriptorState state = std::move(queue.front());
    queue.pop_front();
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    const uint32_t descriptor_id = descriptor.descriptor_id;
    const uint32_t command_id = descriptor.command_id;
    const uint32_t completion_event = descriptor.completion_event;
    const Tick completion_tick = curTick();

    DescriptorTiming &timing = timings[descriptor_id];
    timing.done_tick = completion_tick;
    finishBurstAttribution(state, completion_tick);
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
        for (uint64_t ordinal : state.burst_ordinals) {
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

    // The transport row is the cross-instance accumulation of the exact
    // per-execution facts; the contribution itself stays execution-scoped.
    ActualTraffic &row = rowOf(descriptor_id);
    row.read_bytes += state.contribution.read_bytes;
    row.write_bytes += state.contribution.write_bytes;
    row.p2p_bytes += state.contribution.p2p_bytes;
    row.fill_bytes += state.contribution.fill_bytes;
    row.read_bursts += state.contribution.read_bursts;
    row.write_bursts += state.contribution.write_bursts;
    row.p2p_bursts += state.contribution.p2p_bursts;
    row.read_discarded_bytes += state.discarded_bytes;
    row.write_drained_uncommitted_bytes += state.drained_bytes;
    row.error_code = status == DmaStatus::AXI_READ_ERROR ? 1
                  : status == DmaStatus::AXI_WRITE_ERROR ? 2 : 0;
    if (is_fill) {
        const auto payload = payload_state.find(descriptor_id);
        if (payload != payload_state.end())
            row.payload_digest = fnvDigest(payload->second.first,
                                           payload->second.second);
    } else {
        uint64_t first = kDigestSeedFirst;
        uint64_t second = kDigestSeedSecond;
        for (const auto &[logical_start, bytes] : state.ordered_burst_payload)
            if (state.committed_bursts.count(logical_start))
                fnvUpdate(first, second, bytes.data(), bytes.size());
        row.payload_digest = fnvDigest(first, second);
    }

    if (descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
        descriptor.useful_bytes == 0 && owner)
        owner->notifyPeerCommit(descriptor.dst.owner_core,
                                descriptor.transfer_id);
    const std::string source_digest = recordSourceRows(state);

    if (owner && state.key) {
        state.contribution.payload_digest = row.payload_digest;
        owner->recordDescriptorCompletion(
            *state.key, completion_tick, status == DmaStatus::OK,
            status == DmaStatus::OK
                ? (timing.local_commit_tick ? timing.local_commit_tick
                                            : completion_tick)
                : 0,
            status, state.contribution);
    }
    notifyOwner(descriptor_id, command_id, completion_event, status);
    if (owner && state.key && status == DmaStatus::OK &&
        descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
        descriptor.useful_bytes > 0 && descriptor.transfer_id != 0) {
        MeshDummyCore::TransferCommitContent content;
        content.source_digest = source_digest;
        content.target_digest = destinationDigest(descriptor);
        owner->recordTransferCommit(descriptor, content);
    }
}

std::string
AxiTensorDmaEngine::destinationDigest(const DecodedDmaDescriptor &descriptor)
{
    if (!owner)
        return {};
    mesh_hash::Sha256 digest;
    std::vector<uint8_t> buffer(descriptor.row_bytes);
    const uint64_t base =
        owner->admittedEndpointAddress(descriptor.descriptor_id, false);
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t address = base + uint64_t(row) * descriptor.dst_stride_bytes;
        if (!readFunctional(address, descriptor.row_bytes, buffer.data()))
            return {};
        digest.update(buffer.data(), buffer.size());
    }
    return mesh_hash::digestHex(digest.digest());
}

bool
AxiTensorDmaEngine::readFunctional(uint64_t address, uint64_t size, uint8_t *out)
{
    MeshDispatcher *dispatcher = owner ? owner->runtimeDispatcher() : nullptr;
    if (dispatcher == nullptr)
        return false;
    return dispatcher->readFunctional(address, size, out);
}

std::string
AxiTensorDmaEngine::recordSourceRows(const DescriptorState &state)
{
    if (!owner || !state.key)
        return {};
    const DecodedDmaDescriptor &descriptor = state.descriptor;
    const bool local_source = descriptor.kind == mesh_abi::kDmaKindSTORE ||
                              descriptor.kind == mesh_abi::kDmaKindP2P_PUSH;
    if (!local_source || descriptor.useful_bytes == 0)
        return {};
    const uint64_t local_base =
        owner->admittedLocalOffset(descriptor.descriptor_id, true);
    const uint64_t absolute_base =
        owner->admittedEndpointAddress(descriptor.descriptor_id, true);
    std::vector<uint8_t> buffer(descriptor.row_bytes);
    std::vector<ContentRowObservation> rows;
    mesh_hash::Sha256 payload;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t local =
            local_base + uint64_t(row) * descriptor.src_stride_bytes;
        fatal_if(!owner->functionalSramRead(local, descriptor.row_bytes,
                                           buffer.data()),
                 "dma source row read escapes SRAM on descriptor %u",
                 descriptor.descriptor_id);
        payload.update(buffer.data(), buffer.size());
        mesh_hash::Sha256 hash;
        hash.update(buffer.data(), buffer.size());
        ContentRowObservation observation;
        observation.address =
            absolute_base + uint64_t(row) * descriptor.src_stride_bytes;
        observation.size = descriptor.row_bytes;
        observation.digest = mesh_hash::digestHex(hash.digest());
        if (source_bytes_limit != 0 &&
            descriptor.row_bytes <= source_bytes_limit)
            observation.bytes_hex =
                mesh_hash::bytesHex(buffer.data(), buffer.size());
        rows.push_back(std::move(observation));
    }
    owner->recordDescriptorSourceRows(*state.key, rows);
    return mesh_hash::digestHex(payload.digest());
}

void
AxiTensorDmaEngine::notifyOwner(uint32_t descriptor_id, uint32_t command_id,
                                uint32_t completion_event, DmaStatus status)
{
    if (owner)
        owner->onDmaCompleted(command_id, descriptor_id, completion_event,
                              curTick(), status);
}

} // namespace ai_mesh
} // namespace gem5

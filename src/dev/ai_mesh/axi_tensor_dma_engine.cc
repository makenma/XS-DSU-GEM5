#include "dev/ai_mesh/axi_tensor_dma_engine.hh"
#include "dev/ai_mesh/dma_kind.hh"

#include <algorithm>
#include <iterator>

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
    instance_counter = owner->instanceGeneration().value();
    instance_bursts.clear();
    attribution_by_ordinal.clear();
}

BurstAttribution &
AxiTensorDmaEngine::noteBurst(const DescriptorState &state,
                              const AxiBurst &plan, uint32_t burst_index,
                              uint64_t ordinal, uint16_t axi_id, bool read)
{
    BurstAttribution record;
    record.descriptor = state.descriptor_key;
    record.core_id = owner == nullptr ? 0 : owner->archCoreId();
    record.command_id = state.command_key.ordinal;
    record.generation = state.key ? state.key->command.generation : 0;
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
AxiTensorDmaEngine::bindFillPattern(RuntimeObjectKey command, uint64_t pattern)
{
    fill_patterns[command] = pattern;
}

void
AxiTensorDmaEngine::bindFillContent(RuntimeObjectKey command,
                                    const std::vector<uint8_t> &content,
                                    bool install_bytes)
{
    FillContent entry;
    entry.bytes = content;
    entry.install_bytes = install_bytes;
    fill_contents[command] = std::move(entry);
}

void
AxiTensorDmaEngine::releaseFillBindings(InstanceGeneration instance)
{
    for (auto item = fill_contents.begin(); item != fill_contents.end();)
        item = item->first.instance == instance ? fill_contents.erase(item) : std::next(item);
    for (auto item = fill_patterns.begin(); item != fill_patterns.end();)
        item = item->first.instance == instance ? fill_patterns.erase(item) : std::next(item);
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
                           const RuntimeObjectKey &descriptor_key,
                           const RuntimeObjectKey &command_key,
                           const RuntimeObjectKey &completion_event,
                           Tick issue_tick)
{
    const bool is_fill = descriptor.kind == mesh_abi::kDmaKindLOCAL_FILL;
    const bool is_read = dmaKindIsRead(descriptor.kind);
    // FILL shares the finite write-engine queue (spec 4.3); its rows are
    // scheduled one SRAM write service each.
    auto &queue = is_read ? read_queue : write_queue;
    if (queue.size() >= descriptor_queue_depth)
        return false;

    rowOf(descriptor_key).dma_kind = descriptor.kind;
    DescriptorState state;
    state.descriptor = descriptor;
    state.descriptor_key = descriptor_key;
    state.command_key = command_key;
    state.completion_event = completion_event;
    state.ready_tick =
        issue_tick + Cycles(setup_cycles) * clockPeriod();
    fatal_if(!owner, "dma engine has no core owner for descriptor %u",
             descriptor.descriptor_id);
    if (descriptor_key.domain == mesh_abi::MeshObjectDomain::STATIC) {
        state.key = owner->frozenDescriptorKey(descriptor.command_id, descriptor.descriptor_id);
        fatal_if(!state.key, "admitted DMA has no frozen execution identity");
    } else {
        state.key = DescriptorKey{{command_key.ordinal, 0}, descriptor_key};
    }
    // Payload digests and timing records are per-completion; byte counters
    // accumulate across program instances, per-descriptor state must not.
    payload_state[descriptor_key] =
        std::make_pair(kDigestSeedFirst, kDigestSeedSecond);
    timings.erase(descriptor_key);
    if (descriptor.kind == mesh_abi::kDmaKindGATHER_LOAD) {
        const auto *index = owner->admittedGatherIndex(descriptor.descriptor_id);
        fatal_if(!index, "gather descriptor has no admitted index layout");
        owner->checkDmaSourceValidity(command_key);
        state.gather.emplace(index->layout());
    }
    if (descriptor.useful_bytes == 0 || state.gather)
        state.bursts_total = 0;  // zero-length: every kind, no SRAM service
    else if (is_fill)
        state.bursts_total = descriptor.rows;
    else {
        state.plan = planOf(descriptor, descriptor_key);
        state.bursts_total = uint32_t(state.plan.size());
    }
    if (state.key)
        owner->recordDescriptorSubmission(*state.key, curTick(), state.ready_tick);
    queue.push_back(std::move(state));
    scheduleTick();
    return true;
}

uint64_t
AxiTensorDmaEngine::remoteRowAddress(const DecodedDmaDescriptor &descriptor,
                                    const RuntimeObjectKey &key, uint32_t row) const
{
    const bool source = dmaKindIsRead(descriptor.kind);
    const auto stride = source ? descriptor.src_stride_bytes : descriptor.dst_stride_bytes;
    return owner->dmaEndpointAddress(descriptor, key, source) + uint64_t(row) * stride;
}

std::vector<AxiBurst>
AxiTensorDmaEngine::planOf(const DecodedDmaDescriptor &descriptor,
                            const RuntimeObjectKey &key) const
{
    const uint32_t limit = std::min(
        max_burst_beats, uint32_t(descriptor.max_burst_beats));
    std::vector<AxiBurst> plan;
    for (uint32_t row = 0; row < descriptor.rows; row++) {
        const uint64_t row_abs = remoteRowAddress(descriptor, key, row);
        for (auto burst : splitBursts(row_abs, descriptor.row_bytes,
                                      data_bus_bytes, limit)) {
            burst.row = row;
            plan.push_back(burst);
        }
    }
    return plan;
}

ActualTraffic &
AxiTensorDmaEngine::rowOf(RuntimeObjectKey descriptor)
{
    return actual[descriptor];
}

void
AxiTensorDmaEngine::notePayload(RuntimeObjectKey descriptor,
                                const uint8_t *data, uint64_t size)
{
    auto &state = payload_state[descriptor];
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
    if (state.descriptor.useful_bytes == 0) {
        finishDescriptor(true, DmaStatus::OK);
        return;
    }
    if (state.gather) {
        auto &reader = *state.gather;
        if (owner->instanceErrored() && state.gather_status == DmaStatus::OK) {
            reader.cancel();
            state.gather_status = DmaStatus::SUPPRESSED;
            state.plan.resize(state.burst_ordinals.size());
            state.bursts_total = state.plan.size();
        }
        if (state.bursts_done == state.bursts_total && !reader.done()) {
            const auto read = reader.advance(curTick(), *owner);
            if (read) {
                owner->observations.recordDescriptorIndexRead(*state.key, *read);
                if (reader.invalidIndex()) {
                    state.gather_status = DmaStatus::INDEX_OUT_OF_RANGE;
                } else if (!reader.cancelled()) {
                    const auto &descriptor = state.descriptor;
                    const uint64_t address = owner->dmaEndpointAddress(
                        descriptor, state.descriptor_key, true) +
                        uint64_t(read->raw_index) * descriptor.src_stride_bytes;
                    for (auto burst : splitBursts(address, descriptor.row_bytes,
                            data_bus_bytes, std::min(max_burst_beats, uint32_t(descriptor.max_burst_beats)))) {
                        burst.row = read->occurrence;
                        state.plan.push_back(burst);
                    }
                    state.bursts_total = state.plan.size();
                }
            }
            if (reader.readError())
                state.gather_status = DmaStatus::AXI_READ_ERROR;
            if (state.gather_status != DmaStatus::OK && state.gather_status != DmaStatus::SUPPRESSED) {
                auto &timing = timings[state.descriptor_key];
                timing.first_error_tick = curTick();
                owner->onDmaFault(state.descriptor_key, state.command_key, curTick(),
                                  false, state.gather_status);
            }
        }
        if (reader.done() && state.bursts_done == state.bursts_total) {
            finishDescriptor(true, state.bursts_errored ? DmaStatus::AXI_READ_ERROR : state.gather_status);
            return;
        }
    }
    if (state.burst_ordinals.size() >= state.bursts_total)
        return;
    if (live_read_bursts.size() >= segment_queue_depth)
        return;

    const std::vector<AxiBurst> &plan = state.plan;
    const AxiBurst &burst_plan = plan[state.burst_ordinals.size()];

    const auto &descriptor = state.descriptor;
    const auto &descriptor_key = state.descriptor_key;
    const uint64_t dst_base = owner->dmaLocalOffset(descriptor, descriptor_key, false) +
        uint64_t(burst_plan.row) * descriptor.dst_stride_bytes + burst_plan.row_offset;

    if (free_read_ids.empty())
        return; // every AXI ID still has a live response: backpressure
    const uint16_t axi_id = free_read_ids.front();
    free_read_ids.pop_front();
    uint64_t ordinal = 0;
    if (!bridge->submitRead(burst_plan.beat_base, burst_plan.beats, axi_id,
                            instance_binding.effectiveQos(descriptor.qos), &ordinal)) {
        free_read_ids.push_back(axi_id);
        return;
    }
    ReadBurst burst;
    burst.descriptor_key = descriptor_key;
    burst.plan = burst_plan;
    burst.dst_base = dst_base;
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
    DescriptorTiming &timing = timings[descriptor_key];
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
    const RuntimeObjectKey &descriptor_key = state.descriptor_key;
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
    const auto &output = instance_binding.outputLedger();
    const bool publish = output && descriptor_key.domain == mesh_abi::MeshObjectDomain::STATIC &&
        output->contains(descriptor.descriptor_id);
    if (publish) {
        const auto decision = output->decision(descriptor.descriptor_id, curTick());
        if (decision == PublishSegmentLedger::Decision::Wait)
            return;
        if (decision != PublishSegmentLedger::Decision::Issue) {
            if (state.prepare_pending)
                return;
            if (decision == PublishSegmentLedger::Decision::Suppress) {
                fatal_if(!output->suppress(descriptor.descriptor_id, curTick()), "PUBLISH suppress changed at commit");
                finishDescriptor(false, DmaStatus::SUPPRESSED);
            } else {
                finishDescriptor(false, DmaStatus::AXI_WRITE_ERROR);
            }
            return;
        }
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
        const uint64_t row_src_local = owner->dmaLocalOffset(descriptor, descriptor_key, true) +
            uint64_t(burst_plan.row) * descriptor.src_stride_bytes + burst_plan.row_offset;

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
            this, descriptor_key, burst_index,
            row_src_local);
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
                             instance_binding.effectiveQos(descriptor.qos),
                             *state.prepared_beats, &ordinal)) {
        free_write_ids.push_back(axi_id);
        return;
    }
    WriteBurst burst;
    if (publish) {
        const auto offset = uint64_t(burst_plan.row) * descriptor.row_bytes +
            burst_plan.row_offset;
        fatal_if(!output->issue(descriptor.descriptor_id, offset, burst_plan.useful_bytes, curTick()),
                 "PUBLISH issue violated the frozen segment chain");
        for (const auto &beat : *state.prepared_beats)
            for (uint32_t lane = 0; lane < data_bus_bytes; ++lane)
                if ((beat.byteStrobe >> lane) & 1)
                    burst.payload.push_back(beat.functionalData[lane]);
    }
    state.prepared_beats.reset();

    burst.descriptor_key = descriptor_key;
    burst.plan = burst_plan;
    burst.kind = descriptor.kind;
    noteBurst(state, burst_plan, burst_index, ordinal, axi_id, false);
    burst.axi_id = axi_id;
    live_write_bursts.emplace(ordinal, burst);
    state.burst_ordinals.push_back(ordinal);
    write_bursts_submitted++;
    DescriptorTiming &timing = timings[descriptor_key];
    if (timing.first_aw_tick == 0)
        timing.first_aw_tick = curTick();
    if (timing.first_w_tick == 0)
        timing.first_w_tick = curTick();
}

void
AxiTensorDmaEngine::prepareWriteBurst(RuntimeObjectKey descriptor_key,
                                      uint32_t burst_index,
                                      uint64_t row_src_local)
{
    pending_local_events--;
    fatal_if(
        write_queue.empty() ||
            write_queue.front().descriptor_key != descriptor_key,
        "write prepare for non-head descriptor %u", descriptor_key.ordinal);
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
    state.ordered_burst_payload.emplace(std::make_pair(burst_plan.row, burst_plan.row_offset), logical);
    if (curTick() >= timings[descriptor_key].local_commit_tick)
        timings[descriptor_key].local_commit_tick = curTick();

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
    const RuntimeObjectKey &descriptor_key = state.descriptor_key;
    if (state.prepare_pending)
        return; // a row's SRAM write service is in flight
    if (state.bursts_done >= state.bursts_total)
        return;
    const uint32_t next_row = state.bursts_done;
    const uint64_t dst_off =
        owner->dmaLocalOffset(descriptor, descriptor_key, false) +
        uint64_t(next_row) * descriptor.dst_stride_bytes;
    const auto service = owner->tryReserveSramService(
        dst_off, descriptor.row_bytes, true);
    if (!service)
        return;
    const Tick ready = curTick() + service->stall_ticks;
    state.prepare_pending = true;
    pending_local_events++;
    auto *event =
        new FillRowEvent(this, descriptor_key, next_row);
    schedule(event, ready > clockEdge(Cycles(1)) ? ready
                                                 : clockEdge(Cycles(1)));
}

void
AxiTensorDmaEngine::commitFillRow(RuntimeObjectKey descriptor_key,
                                  uint32_t row)
{
    pending_local_events--;
    fatal_if(write_queue.empty() ||
                 write_queue.front().descriptor_key != descriptor_key,
             "fill commit for non-head descriptor %u", descriptor_key.ordinal);
    DescriptorState &state = write_queue.front();
    fatal_if(!state.prepare_pending, "unexpected fill row event");
    state.prepare_pending = false;
    const DecodedDmaDescriptor &descriptor = state.descriptor;

    const auto content_it = fill_contents.find(state.command_key);
    std::vector<uint8_t> bytes(descriptor.row_bytes);
    std::vector<uint8_t> installed(descriptor.row_bytes, 0);
    if (content_it != fill_contents.end()) {
        const FillContent &content = content_it->second;
        const uint64_t row_off = uint64_t(row) * descriptor.row_bytes;
        fatal_if(row_off + descriptor.row_bytes > content.bytes.size(),
                 "fill content is shorter than the descriptor rows");
        bytes.assign(content.bytes.begin() + row_off,
                     content.bytes.begin() + row_off + descriptor.row_bytes);
        if (content.install_bytes)
            installed = bytes;
    } else {
        auto it = fill_patterns.find(state.command_key);
        fatal_if(it == fill_patterns.end(),
                 "fill content not bound for command %u",
                 descriptor.command_id);
        const uint64_t pattern = it->second;
        fatal_if(pattern == mesh_abi::kDmaFillRuntimeBoundSentinel,
                 "E_BINDING_ROLE: runtime sentinel is not a fill pattern");
        for (uint64_t i = 0; i < descriptor.row_bytes; i++)
            bytes[i] = static_cast<uint8_t>((pattern >> (8 * ((uint64_t(row) * descriptor.row_bytes + i) % 8))) & 0xFF);
        installed = bytes;
    }
    const uint64_t dst_off = owner->dmaLocalOffset(descriptor, descriptor_key, false) +
                             uint64_t(row) * descriptor.dst_stride_bytes;
    fatal_if(!owner->functionalSramWrite(dst_off, descriptor.row_bytes,
                                         installed.data()),
             "fill destination write out of bounds");
    notePayload(descriptor_key, bytes.data(), bytes.size());
    state.contribution.fill_bytes += descriptor.row_bytes;
    if (curTick() >= timings[descriptor_key].local_commit_tick)
        timings[descriptor_key].local_commit_tick = curTick();
    state.contribution.committed_segments.push_back(
        DmaCommittedSegment{row, 0, descriptor.row_bytes});

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
    if (resp != axi::AxiResp::Okay && !burst.errored) {
        burst.errored = true;
        auto &state = read_queue.front();
        fatal_if(state.descriptor_key != burst.descriptor_key, "read error has no descriptor owner");
        state.bursts_total = state.burst_ordinals.size();
        auto &timing = timings[burst.descriptor_key];
        if (!timing.first_error_tick)
            timing.first_error_tick = curTick();
        if (owner)
            owner->onDmaFault(burst.descriptor_key, state.command_key, curTick(), false);
    }

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
    timings[burst.descriptor_key].last_r_tick = curTick();

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
    state.ordered_burst_payload.emplace(std::make_pair(burst.plan.row, burst.plan.row_offset), burst.packed);
    state.committed_bursts.emplace(burst.plan.row, burst.plan.row_offset);
    state.contribution.read_bytes += burst.packed.size();
    state.contribution.read_bursts++;
    state.contribution.committed_segments.push_back(
        DmaCommittedSegment{burst.plan.row,
                            burst.plan.row_offset,
                            burst.packed.size()});
    read_bursts_completed++;
    // Keep the RLAST of the burst whose local commit is the latest, so the
    // exported pair satisfies rlast <= commit <= done exactly.
    if (curTick() >= timings[burst.descriptor_key].local_commit_tick) {
        timings[burst.descriptor_key].local_commit_tick = curTick();
        timings[burst.descriptor_key].last_r_tick = burst.last_r_tick;
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
    const RuntimeObjectKey descriptor = queue.front().descriptor_key;
    if (read_direction) {
        for (auto it = live_read_bursts.begin();
             it != live_read_bursts.end();) {
            if (it->second.descriptor_key == descriptor)
                it = live_read_bursts.erase(it);
            else
                ++it;
        }
    } else {
        for (auto it = live_write_bursts.begin();
             it != live_write_bursts.end();) {
            if (it->second.descriptor_key == descriptor)
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
    if (errored) {
        auto &state = write_queue.front();
        fatal_if(state.descriptor_key != burst.descriptor_key, "write error has no descriptor owner");
        auto &timing = timings[burst.descriptor_key];
        if (!timing.first_error_tick)
            timing.first_error_tick = curTick();
        if (owner)
            owner->onDmaFault(burst.descriptor_key, state.command_key, curTick(), false);
    }
    const auto &output = instance_binding.outputLedger();
    if (output && burst.descriptor_key.domain == mesh_abi::MeshObjectDomain::STATIC &&
            output->contains(burst.descriptor_key.ordinal)) {
        const auto &descriptor = write_queue.front().descriptor;
        const auto offset = uint64_t(burst.plan.row) * descriptor.row_bytes +
            burst.plan.row_offset;
        fatal_if(!output->commit(burst.descriptor_key.ordinal, offset, burst.plan.useful_bytes, !errored, curTick()),
                 "PUBLISH response does not match its accepted segment");
    }
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
        state.committed_bursts.emplace(burst.plan.row, burst.plan.row_offset);
        if (burst.kind == mesh_abi::kDmaKindSTORE) {
            state.contribution.write_bytes += burst.plan.useful_bytes;
            state.contribution.write_bursts++;
        } else {
            state.contribution.p2p_bytes += burst.plan.useful_bytes;
            state.contribution.p2p_bursts++;
        }
        state.contribution.committed_segments.push_back(
            DmaCommittedSegment{burst.plan.row,
                                burst.plan.row_offset,
                                burst.plan.useful_bytes});
    }
    write_bursts_completed++;
    DescriptorTiming &timing = timings[burst.descriptor_key];
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
    const auto &output = instance_binding.outputLedger();
    const bool stopped_publish = !read && errored && output &&
        state.descriptor_key.domain == mesh_abi::MeshObjectDomain::STATIC &&
        output->contains(state.descriptor.descriptor_id);
    if (state.gather && (!state.gather->done() || state.bursts_done < state.bursts_total))
        return;
    if (state.bursts_done < state.bursts_total && !stopped_publish)
        return;
    const DmaStatus status = state.bursts_errored
        ? (read ? DmaStatus::AXI_READ_ERROR : DmaStatus::AXI_WRITE_ERROR)
        : state.gather_status;
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
    const auto &descriptor_key = state.descriptor_key;
    const auto &command_key = state.command_key;
    const auto &completion_event = state.completion_event;
    const Tick completion_tick = curTick();

    DescriptorTiming &timing = timings[descriptor_key];
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
        dmaKindIsRead(descriptor.kind);
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
    DescriptorTimingEntry entry;
    entry.descriptor = descriptor_key;
    entry.command = state.key->command;
    entry.dma_kind = descriptor.kind;
    entry.timing = timing;
    timing_log.push_back(std::move(entry));

    // The transport row is the cross-instance accumulation of the exact
    // per-execution facts; the contribution itself stays execution-scoped.
    ActualTraffic &row = rowOf(descriptor_key);
    row.committed_segments.insert(row.committed_segments.end(),
        state.contribution.committed_segments.begin(), state.contribution.committed_segments.end());
    row.read_bytes += state.contribution.read_bytes;
    row.write_bytes += state.contribution.write_bytes;
    row.p2p_bytes += state.contribution.p2p_bytes;
    row.fill_bytes += state.contribution.fill_bytes;
    row.read_bursts += state.contribution.read_bursts;
    row.write_bursts += state.contribution.write_bursts;
    row.p2p_bursts += state.contribution.p2p_bursts;
    row.read_discarded_bytes += state.discarded_bytes;
    row.write_drained_uncommitted_bytes += state.drained_bytes;
    row.error_code = status == DmaStatus::OK || status == DmaStatus::SUPPRESSED
        ? 0 : uint32_t(status);
    if (is_fill) {
        const auto payload = payload_state.find(descriptor_key);
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
    notifyOwner(descriptor_key, command_key, completion_event, status);
    if (owner && state.key && descriptor_key.domain == mesh_abi::MeshObjectDomain::STATIC &&
        status == DmaStatus::OK && descriptor.kind == mesh_abi::kDmaKindP2P_PUSH &&
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
        owner->dmaLocalOffset(descriptor, state.descriptor_key, true);
    const uint64_t absolute_base =
        owner->dmaEndpointAddress(descriptor, state.descriptor_key, true);
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
AxiTensorDmaEngine::notifyOwner(RuntimeObjectKey descriptor, RuntimeObjectKey command,
                                RuntimeObjectKey completion_event, DmaStatus status)
{
    if (command.domain != mesh_abi::MeshObjectDomain::STATIC) {
        fill_contents.erase(command);
        fill_patterns.erase(command);
    }
    payload_state.erase(descriptor);
    if (owner)
        owner->onDmaCompleted(descriptor, command, completion_event, curTick(), status);
}

} // namespace ai_mesh
} // namespace gem5

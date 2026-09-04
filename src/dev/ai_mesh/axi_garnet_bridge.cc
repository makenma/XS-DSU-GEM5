#include "dev/ai_mesh/axi_garnet_bridge.hh"

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AiMesh.hh"
#include "mem/axi/axi_garnet_endpoint.hh"
#include "params/AxiGarnetBridge.hh"
#include "sim/cur_tick.hh"

namespace gem5
{
namespace ai_mesh
{

AxiGarnetBridge::AxiGarnetBridge(const Params &p)
    : ClockedObject(p),
      data_bus_bytes(p.data_bus_bytes),
      aw_queue_depth(p.aw_queue_depth),
      ar_queue_depth(p.ar_queue_depth),
      max_axi_ids(p.axi_id_count),
      axi_id_base(p.axi_id_base),
      tick_event(this)
{
    adapter = p.adapter;
}

void
AxiGarnetBridge::bindAdapter(axi::AxiInitiatorAdapter *a)
{
    adapter = a;
}

void AxiGarnetBridge::setReadBeatSink(
    void *ctx,
    void (*on_beat)(void *, uint64_t, uint16_t, bool, axi::AxiResp,
                    const uint8_t *))
{
    read_sink_ctx = ctx;
    read_beat_fn = on_beat;
}

void AxiGarnetBridge::setWriteDoneSink(void *ctx,
                                       void (*on_done)(void *, uint64_t,
                                                       axi::AxiResp))
{
    write_sink_ctx = ctx;
    write_done_fn = on_done;
}

void AxiGarnetBridge::startup()
{
    ClockedObject::startup();
}

bool
AxiGarnetBridge::submitRead(uint64_t beat_base, uint32_t beats, uint16_t axi_id,
                            uint8_t qos, uint64_t *ordinal_out)
{
    if (pending_ar.size() >= ar_queue_depth)
        return false;
    PendingRead read;
    read.ar.address = beat_base;
    read.ar.beatCount = uint16_t(beats);
    read.ar.size = uint8_t(__builtin_ctz(data_bus_bytes));
    read.ar.burst = axi::AxiBurst::Incr;
    read.ar.axiId = axi_id;
    read.ar.qos = qos;
    read.ordinal = next_ordinal++;
    pending_ar.push_back(read);
    *ordinal_out = read.ordinal;
    scheduleTick();
    return true;
}

bool
AxiGarnetBridge::submitWrite(uint64_t beat_base, uint32_t beats,
                             uint16_t axi_id, uint8_t qos,
                             const std::vector<axi::AxiWBeat> &w_beats,
                             uint64_t *ordinal_out)
{
    fatal_if(w_beats.size() != beats, "bridge W beat count mismatch");
    if (pending_aw.size() >= aw_queue_depth)
        return false;
    PendingWrite write;
    write.aw.address = beat_base;
    write.aw.beatCount = uint16_t(beats);
    write.aw.size = uint8_t(__builtin_ctz(data_bus_bytes));
    write.aw.burst = axi::AxiBurst::Incr;
    write.aw.axiId = axi_id;
    write.aw.qos = qos;
    write.beats = w_beats;
    const uint64_t ordinal = next_ordinal++;
    write.ordinal = ordinal;
    pending_aw.push_back(std::move(write));
    *ordinal_out = ordinal;
    scheduleTick();
    return true;
}

void AxiGarnetBridge::driveWrites()
{
    if (adapter == nullptr || pending_aw.empty())
        return;
    PendingWrite &write = pending_aw.front();

    if (!write.aw_accepted) {
        if (!adapter->tryAcceptAw(write.aw))
            return;
        write.aw_accepted = true;
        ordinal_handshake[write.ordinal].addr_accept = curTick();
        ctr.awAccepted++;
        write_ordinal_by_id[write.aw.axiId].push_back(write.ordinal);
        live_writes.emplace(write.ordinal, PendingWrite{});
        DPRINTF(AiMesh, "bridge aw ordinal %llu addr=%#llx beats=%u id=%u\n",
                (unsigned long long)write.ordinal,
                (unsigned long long)write.aw.address, write.aw.beatCount,
                write.aw.axiId);
    }

    while (write.next_beat < write.beats.size()) {
        if (!adapter->tryAcceptW(write.beats[write.next_beat]))
            return;
        auto &ticks = ordinal_handshake[write.ordinal];
        if (ticks.first_w == 0)
            ticks.first_w = curTick();
        ctr.wAccepted++;
        write.next_beat++;
    }

    live_writes.at(write.ordinal) = std::move(write);
    pending_aw.pop_front();
}

void AxiGarnetBridge::driveReads()
{
    if (adapter == nullptr || pending_ar.empty())
        return;
    PendingRead read = pending_ar.front();
    if (!adapter->tryAcceptAr(read.ar))
        return;
    pending_ar.pop_front();
    ordinal_handshake[read.ordinal].addr_accept = curTick();
    ctr.arAccepted++;
    read_beats_of_ordinal[read.ordinal] = read.ar.beatCount;
    live_reads.emplace(read.ordinal, read);
}

void
AxiGarnetBridge::setProtocolErrorSink(void *ctx,
                                      void (*on_error)(void *, bool))
{
    protocol_error_ctx = ctx;
    protocol_error_fn = on_error;
}

const AxiGarnetBridge::OrdinalTicks *
AxiGarnetBridge::ordinalTicks(uint64_t ordinal) const
{
    auto it = ordinal_handshake.find(ordinal);
    return it == ordinal_handshake.end() ? nullptr : &it->second;
}

void AxiGarnetBridge::consumeB()
{
    if (adapter == nullptr || write_done_fn == nullptr)
        return;
    axi::AxiBBeat beat;
    if (!adapter->tryConsumeB(beat))
        return;
    auto &queue = write_ordinal_by_id[beat.axiId];
    if (queue.empty() || live_writes.count(queue.front()) == 0) {
        // Structured protocol error (unknown B ID): fail the head write
        // descriptor of the engine instead of killing the simulation;
        // the response itself is fully consumed here.
        ctr.bConsumed++;
        ctr.bErrorCount++;
        if (protocol_error_fn)
            protocol_error_fn(protocol_error_ctx, false);
        return;
    }
    const uint64_t ordinal = queue.front();
    queue.pop_front();
    if (queue.empty())
        write_ordinal_by_id.erase(beat.axiId);
    live_writes.erase(ordinal);
    ordinal_handshake[ordinal].resp_last = curTick();
    ctr.bConsumed++;
    ctr.bOrder.push_back(ordinal);
    if (beat.resp != axi::AxiResp::Okay)
        ctr.bErrorCount++;
    write_done_fn(write_sink_ctx, ordinal, beat.resp);
}

void AxiGarnetBridge::consumeR()
{
    if (adapter == nullptr || read_beat_fn == nullptr)
        return;
    axi::AxiRBeat beat;
    if (!adapter->tryConsumeR(beat))
        return;
    ReadHead &head = read_head_by_id[beat.axiId];
    if (!head.active) {
        uint64_t oldest = UINT64_MAX;
        for (const auto &kv : live_reads)
            if (kv.second.ar.axiId == beat.axiId && kv.first < oldest)
                oldest = kv.first;
        if (oldest == UINT64_MAX) {
            // Structured protocol error (unknown R ID): consume the beat,
            // fail the engine's head read descriptor.
            ctr.rBeatsConsumed++;
            ctr.rErrorBeats++;
            if (protocol_error_fn)
                protocol_error_fn(protocol_error_ctx, true);
            return;
        }
        head.active = true;
        head.ordinal = oldest;
        head.beats_seen = 0;
        head.beats_total = read_beats_of_ordinal.at(oldest);
    }
    head.beats_seen++;
    ctr.rBeatsConsumed++;
    axi::AxiResp resp = beat.resp;
    if (head.beats_seen > head.beats_total ||
        (beat.last && head.beats_seen != head.beats_total))
        resp = axi::AxiResp::SlvErr;  // early RLAST / beat overrun
    if (resp != axi::AxiResp::Okay)
        ctr.rErrorBeats++;
    read_beat_fn(read_sink_ctx, head.ordinal, uint16_t(head.beats_seen - 1),
                 beat.last, resp, beat.functionalData.data());
    if (beat.last || head.beats_seen >= head.beats_total) {
        ordinal_handshake[head.ordinal].resp_last = curTick();
        live_reads.erase(head.ordinal);
        read_beats_of_ordinal.erase(head.ordinal);
        read_head_by_id.erase(beat.axiId);
    }
}

uint64_t
AxiGarnetBridge::completedWrites() const
{
    return ctr.bConsumed;
}

uint32_t
AxiGarnetBridge::outstandingReads() const
{
    return live_reads.size();
}

uint32_t
AxiGarnetBridge::outstandingWrites() const
{
    return live_writes.size();
}

bool
AxiGarnetBridge::idle() const
{
    return pending_aw.empty() && pending_ar.empty() && live_writes.empty() &&
           live_reads.empty() && write_ordinal_by_id.empty() &&
           read_head_by_id.empty();
}

void AxiGarnetBridge::scheduleTick()
{
    if (!tick_event.scheduled())
        schedule(&tick_event, clockEdge() + 1);
}

void AxiGarnetBridge::tick()
{
    driveWrites();
    driveReads();
    consumeB();
    consumeR();
    if (!pending_aw.empty() || !pending_ar.empty() || !live_writes.empty() ||
        !live_reads.empty())
        scheduleTick();
}

} // namespace ai_mesh
} // namespace gem5

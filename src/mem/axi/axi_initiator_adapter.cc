#include "mem/axi/axi_initiator_adapter.hh"

#include <limits>
#include <sstream>
#include <stdexcept>

#include "base/logging.hh"

namespace gem5
{
namespace axi
{

namespace
{

constexpr uint64_t UidCounterLimit = uint64_t{1} << 39;

uint64_t
checkedIncrement(uint64_t &counter, uint64_t limit, const char *label)
{
    panic_if(counter >= limit, "AXI_PROTOCOL: %s counter would wrap", label);
    return counter++;
}

} // anonymous namespace

AxiInitiatorState::AxiInitiatorState(const AxiInitiatorConfig &config)
    : _config(config), _decoder(config.ranges, config.defaultErrorTarget),
      _awReady(config.fifoDepths[0]), _wReady(config.fifoDepths[1]),
      _bReady(config.fifoDepths[2]), _arReady(config.fifoDepths[3]),
      _rReady(config.fifoDepths[4])
{
    validateConfig();
}

void
AxiInitiatorState::validateConfig() const
{
    if (_config.source.srcNode >= (uint32_t{1} << 16) ||
        _config.source.srcPort >= (uint16_t{1} << 8)) {
        throw std::invalid_argument("AXI source endpoint exceeds UID field width");
    }
    if (_config.dataBusBytes == 0 || _config.dataBusBytes > 64)
        throw std::invalid_argument("AXI data bus bytes must be in [1,64]");
    if (_config.idWidth == 0 || _config.idWidth > 16)
        throw std::invalid_argument("AXI ID width must be in [1,16]");
    if (_config.maxOutstandingWrites == 0 ||
        _config.maxOutstandingReads == 0 || _config.preAwBursts == 0 ||
        _config.preAwBeats == 0 || _config.bRobTransactions == 0 ||
        _config.rRobBeats == 0) {
        throw std::invalid_argument("AXI source capacities must be positive");
    }
    if (_config.bRobTransactions < _config.maxOutstandingWrites)
        throw std::invalid_argument("AXI B ROB cannot cover outstanding writes");
    for (const auto &[target, quota] : _config.targetQuotas) {
        if (quota.writeContexts == 0 || quota.writeBeats == 0 ||
            quota.readContexts == 0 || quota.readBeats == 0) {
            throw std::invalid_argument("AXI source target quota must be positive");
        }
        (void)target;
    }
}

AxiCommonMeta
AxiInitiatorState::allocateMeta(const AxiAddressRequest &request, bool read,
                                uint32_t dst_node,
                                uint32_t semantic_bytes,
                                Tick accepted_tick)
{
    const uint64_t local = read ?
        checkedIncrement(_nextReadUid, UidCounterLimit, "read UID") :
        checkedIncrement(_nextWriteUid, UidCounterLimit, "write UID");
    const uint64_t uid =
        (static_cast<uint64_t>(_config.source.srcNode) << 48) |
        (static_cast<uint64_t>(_config.source.srcPort) << 40) |
        (static_cast<uint64_t>(read) << 39) | local;

    const TargetSeqKey target_key{
        _config.source.srcNode, _config.source.srcPort,
        request.axiId, read, dst_node};
    const ResponseSeqKey response_key{
        _config.source.srcNode, _config.source.srcPort,
        request.axiId, read};
    uint64_t &target_counter = _nextTargetSeq[target_key];
    uint64_t &response_counter = _nextResponseSeq[response_key];

    AxiCommonMeta meta;
    meta.txnUid = uid;
    meta.targetSeq = checkedIncrement(
        target_counter, std::numeric_limits<uint64_t>::max(), "targetSeq");
    meta.responseSeq = checkedIncrement(
        response_counter, std::numeric_limits<uint64_t>::max(),
        "responseSeq");
    meta.srcNode = _config.source.srcNode;
    meta.srcPort = _config.source.srcPort;
    meta.dstNode = dst_node;
    meta.axiId = request.axiId;
    meta.semanticBytes = semantic_bytes;
    meta.qos = request.qos;
    meta.acceptedTick = accepted_tick;
    return meta;
}

bool
AxiInitiatorState::tryAcceptAw(const AxiAddressRequest &aw,
                               Tick accepted_tick)
{
    if (_awReady.full() ||
        _writeOrdinalByUid.size() >= _config.maxOutstandingWrites ||
        _writeOrdinalByUid.size() >= _config.bRobTransactions) {
        return false;
    }
    panic_if(aw.axiId >= (uint32_t{1} << _config.idWidth),
             "AXI_PROTOCOL: AWID exceeds configured width");
    const auto validation = validateAxiBurst(aw, _config.dataBusBytes);
    requireValidAxiBurst(validation);
    const auto decode = _decoder.decode(aw, validation);
    panic_if(_config.targetQuotas.count(decode.dstNode) == 0,
             "AXI_PROTOCOL: no quota configured for AW target %u",
             decode.dstNode);

    const uint64_t ordinal = checkedIncrement(
        _nextAwOrdinal, std::numeric_limits<uint64_t>::max(), "AW ordinal");
    auto [it, inserted] = _writesByOrdinal.emplace(ordinal, WriteState{});
    WriteState &state = it->second;
    if (inserted)
        state.ordinal = ordinal;
    panic_if(state.aw.has_value(), "AXI_PROTOCOL: duplicate AW ordinal %llu",
             static_cast<unsigned long long>(ordinal));

    AxiAddressPacket packet;
    packet.meta = allocateMeta(aw, false, decode.dstNode, 24, accepted_tick);
    packet.request = aw;
    packet.writeOrdinal = ordinal;
    packet.decodeResp = decode.response;
    state.aw = packet;
    _writeOrdinalByUid.emplace(packet.meta.txnUid, ordinal);
    panic_if(!_awReady.push(ordinal), "AXI source AW FIFO overflow");

    if (!state.stagedW.empty())
        bindAndValidate(state);
    advance();
    return true;
}

AxiDataPacket
AxiInitiatorState::makeWPacket(const WriteState &state,
                               const AxiWBeat &beat,
                               uint16_t beat_index,
                               Tick accepted_tick) const
{
    panic_if(!state.aw, "AXI internal W packet constructed before AW bind");
    requireValidAxiWriteBeat(
        state.aw->request, beat_index, beat, _config.dataBusBytes);

    AxiDataPacket packet;
    packet.meta = state.aw->meta;
    packet.meta.semanticBytes = static_cast<uint32_t>(
        uint64_t{1} << state.aw->request.size);
    packet.meta.acceptedTick = accepted_tick;
    packet.writeOrdinal = state.ordinal;
    packet.beatIndex = beat_index;
    packet.beatCount = state.aw->request.beatCount;
    packet.last = beat.last;
    packet.byteStrobe = beat.byteStrobe;
    packet.resp = state.aw->decodeResp;
    packet.payloadDigest = beat.payloadDigest;
    packet.address = state.aw->request.address;
    packet.functionalData = beat.functionalData;
    return packet;
}

bool
AxiInitiatorState::tryAcceptW(const AxiWBeat &w, Tick accepted_tick)
{
    if (w.functionalData.size() != _config.dataBusBytes)
        panic("AXI_PROTOCOL: W functionalData must contain one full bus word");

    bool opens_burst = !_openWOrdinal.has_value();
    uint64_t ordinal = 0;
    if (opens_burst) {
        if (_unboundBursts == _config.preAwBursts &&
            _writesByOrdinal.count(_nextWBurstOrdinal) == 0) {
            return false;
        }
        ordinal = _nextWBurstOrdinal;
    } else {
        ordinal = *_openWOrdinal;
    }

    auto existing = _writesByOrdinal.find(ordinal);
    const bool bound = existing != _writesByOrdinal.end() &&
        existing->second.aw.has_value();
    if (bound) {
        if (_wReady.full())
            return false;
    } else if (_unboundBeats == _config.preAwBeats) {
        return false;
    }

    if (opens_burst) {
        checkedIncrement(_nextWBurstOrdinal,
                         std::numeric_limits<uint64_t>::max(), "W ordinal");
        _openWOrdinal = ordinal;
        ++_openedWBursts;
        auto [it, inserted] = _writesByOrdinal.emplace(ordinal, WriteState{});
        if (inserted)
            it->second.ordinal = ordinal;
        existing = it;
        if (!it->second.aw)
            ++_unboundBursts;
    }

    WriteState &state = existing->second;
    if (state.aw && !state.paired) {
        state.paired = true;
        ++_pairedBursts;
    }
    const uint16_t beat_index = state.observedW;
    panic_if(beat_index == std::numeric_limits<uint16_t>::max(),
             "AXI_PROTOCOL: W beat index would wrap");

    if (state.aw) {
        AxiDataPacket packet = makeWPacket(
            state, w, beat_index, accepted_tick);
        panic_if(!_wReady.push(std::move(packet)),
                 "AXI source W FIFO overflow");
    } else {
        AxiDataPacket staged;
        staged.writeOrdinal = ordinal;
        staged.beatIndex = beat_index;
        staged.last = w.last;
        staged.byteStrobe = w.byteStrobe;
        staged.payloadDigest = w.payloadDigest;
        staged.functionalData = w.functionalData;
        staged.meta.acceptedTick = accepted_tick;
        state.stagedW.push_back(std::move(staged));
        ++_unboundBeats;
    }
    ++state.observedW;

    if (w.last) {
        state.sawWlast = true;
        _openWOrdinal.reset();
        ++_closedWBursts;
    }
    advance();
    return true;
}

void
AxiInitiatorState::bindAndValidate(WriteState &state)
{
    panic_if(!state.aw, "AXI internal bind without AW");
    panic_if(state.observedW > state.aw->request.beatCount,
             "AXI_PROTOCOL: W burst has more beats than AW beatCount");
    if (state.sawWlast) {
        panic_if(state.observedW != state.aw->request.beatCount,
                 "AXI_PROTOCOL: WLAST position does not match AW beatCount");
    } else {
        panic_if(state.observedW == state.aw->request.beatCount,
                 "AXI_PROTOCOL: WLAST missing on final beat");
    }

    if (!state.paired) {
        panic_if(_unboundBursts == 0, "AXI unbound burst accounting underflow");
        --_unboundBursts;
        panic_if(_unboundBeats < state.stagedW.size(),
                 "AXI unbound beat accounting underflow");
        _unboundBeats -= state.stagedW.size();
        state.paired = true;
        ++_pairedBursts;
    }
}

void
AxiInitiatorState::pumpStagedW()
{
    for (auto &[ordinal, state] : _writesByOrdinal) {
        (void)ordinal;
        if (!state.aw)
            continue;
        while (!state.stagedW.empty() && !_wReady.full()) {
            const AxiDataPacket staged = std::move(state.stagedW.front());
            state.stagedW.pop_front();
            AxiWBeat beat;
            beat.last = staged.last;
            beat.byteStrobe = staged.byteStrobe;
            beat.payloadDigest = staged.payloadDigest;
            beat.functionalData = staged.functionalData;
            AxiDataPacket packet = makeWPacket(
                state, beat, staged.beatIndex, staged.meta.acceptedTick);
            panic_if(!_wReady.push(std::move(packet)),
                     "AXI source W FIFO overflow while binding");
        }
        if (_wReady.full())
            return;
    }
}

bool
AxiInitiatorState::acquireWriteQuota(WriteState &state)
{
    if (state.quotaAcquired)
        return true;
    panic_if(!state.aw, "AXI write quota requested before AW bind");
    const uint32_t target = state.aw->meta.dstNode;
    const AxiQuota limit = _config.targetQuotas.at(target);
    AxiQuota &active = _activeQuota[target];
    if (active.writeContexts == limit.writeContexts ||
        state.aw->request.beatCount >
            limit.writeBeats - active.writeBeats) {
        return false;
    }
    ++active.writeContexts;
    active.writeBeats += state.aw->request.beatCount;
    state.quotaAcquired = true;
    return true;
}

bool
AxiInitiatorState::acquireReadQuota(ReadState &state)
{
    if (state.quotaAcquired)
        return true;
    const uint32_t target = state.ar.meta.dstNode;
    const AxiQuota limit = _config.targetQuotas.at(target);
    AxiQuota &active = _activeQuota[target];
    if (active.readContexts == limit.readContexts ||
        state.ar.request.beatCount > limit.readBeats - active.readBeats) {
        return false;
    }
    ++active.readContexts;
    active.readBeats += state.ar.request.beatCount;
    state.quotaAcquired = true;
    return true;
}

void
AxiInitiatorState::releaseWriteQuota(const WriteState &state)
{
    panic_if(!state.quotaAcquired || !state.aw,
             "AXI write quota release without acquisition");
    AxiQuota &active = _activeQuota[state.aw->meta.dstNode];
    panic_if(active.writeContexts == 0 ||
             active.writeBeats < state.aw->request.beatCount,
             "AXI write quota accounting underflow");
    --active.writeContexts;
    active.writeBeats -= state.aw->request.beatCount;
}

void
AxiInitiatorState::releaseReadQuota(const ReadState &state)
{
    panic_if(!state.quotaAcquired,
             "AXI read quota release without acquisition");
    AxiQuota &active = _activeQuota[state.ar.meta.dstNode];
    panic_if(active.readContexts == 0 ||
             active.readBeats < state.ar.request.beatCount,
             "AXI read quota accounting underflow");
    --active.readContexts;
    active.readBeats -= state.ar.request.beatCount;
}

void
AxiInitiatorState::advance()
{
    pumpStagedW();
    pumpReadyR();
}

bool
AxiInitiatorState::hasAwPacket()
{
    if (_awReady.empty())
        return false;
    WriteState &state = _writesByOrdinal.at(_awReady.front());
    return acquireWriteQuota(state);
}

bool
AxiInitiatorState::hasWPacket()
{
    if (_wReady.empty())
        return false;
    WriteState &state = _writesByOrdinal.at(_wReady.front().writeOrdinal);
    return acquireWriteQuota(state);
}

bool
AxiInitiatorState::hasArPacket()
{
    if (_arReady.empty())
        return false;
    ReadState &state = _readsByUid.at(_arReady.front());
    return acquireReadQuota(state);
}

const AxiAddressPacket &
AxiInitiatorState::frontAwPacket()
{
    panic_if(!hasAwPacket(), "frontAwPacket called without eligible AW");
    return *_writesByOrdinal.at(_awReady.front()).aw;
}

const AxiDataPacket &
AxiInitiatorState::frontWPacket()
{
    panic_if(!hasWPacket(), "frontWPacket called without eligible W");
    return _wReady.front();
}

const AxiAddressPacket &
AxiInitiatorState::frontArPacket()
{
    panic_if(!hasArPacket(), "frontArPacket called without eligible AR");
    return _readsByUid.at(_arReady.front()).ar;
}

void
AxiInitiatorState::popAwPacket()
{
    panic_if(!hasAwPacket(), "popAwPacket called without eligible AW");
    _writesByOrdinal.at(_awReady.front()).awInjected = true;
    _awReady.pop();
}

void
AxiInitiatorState::popWPacket()
{
    panic_if(!hasWPacket(), "popWPacket called without eligible W");
    ++_writesByOrdinal.at(_wReady.front().writeOrdinal).wInjected;
    _wReady.pop();
    pumpStagedW();
}

void
AxiInitiatorState::popArPacket()
{
    panic_if(!hasArPacket(), "popArPacket called without eligible AR");
    _readsByUid.at(_arReady.front()).arInjected = true;
    _arReady.pop();
}

bool
AxiInitiatorState::tryAcceptAr(const AxiAddressRequest &ar,
                               Tick accepted_tick)
{
    if (_arReady.full() || _readsByUid.size() >= _config.maxOutstandingReads ||
        _config.rRobBeats < ar.beatCount)
        return false;
    size_t reserved = 0;
    for (const auto &[uid, state] : _readsByUid) {
        (void)uid;
        reserved += state.ar.request.beatCount;
    }
    if (reserved + ar.beatCount > _config.rRobBeats)
        return false;
    panic_if(ar.axiId >= (uint32_t{1} << _config.idWidth),
             "AXI_PROTOCOL: ARID exceeds configured width");
    const auto validation = validateAxiBurst(ar, _config.dataBusBytes);
    requireValidAxiBurst(validation);
    const auto decode = _decoder.decode(ar, validation);
    panic_if(_config.targetQuotas.count(decode.dstNode) == 0,
             "AXI_PROTOCOL: no quota configured for AR target %u",
             decode.dstNode);

    AxiAddressPacket packet;
    packet.meta = allocateMeta(ar, true, decode.dstNode, 24, accepted_tick);
    packet.request = ar;
    packet.decodeResp = decode.response;
    ReadState state;
    state.ar = packet;
    state.received.resize(ar.beatCount);
    const uint64_t uid = packet.meta.txnUid;
    panic_if(!_readsByUid.emplace(uid, std::move(state)).second,
             "AXI_PROTOCOL: duplicate read txnUid");
    panic_if(!_arReady.push(uid), "AXI source AR FIFO overflow");
    return true;
}

bool
AxiInitiatorState::canAcceptBPacket() const
{
    return !_bReady.full();
}

bool
AxiInitiatorState::canAcceptRPacket() const
{
    return _rReady.available() != 0 || _readsByUid.size() != 0;
}

void
AxiInitiatorState::acceptBPacket(const AxiBPacket &packet)
{
    panic_if(_bReady.full(), "AXI source B ingress FIFO overflow");
    const auto uid = _writeOrdinalByUid.find(packet.meta.txnUid);
    panic_if(uid == _writeOrdinalByUid.end(),
             "AXI_PROTOCOL: unknown B txnUid");
    WriteState &state = _writesByOrdinal.at(uid->second);
    panic_if(!state.aw || packet.meta.axiId != state.aw->meta.axiId,
             "AXI_PROTOCOL: B fields conflict with AW");
    panic_if(state.wInjected != state.aw->request.beatCount,
             "AXI_PROTOCOL: B arrived before all W beats were injected");
    panic_if(!_bReady.push(packet), "AXI source B ingress FIFO overflow");
}

void
AxiInitiatorState::acceptRPacket(const AxiDataPacket &packet)
{
    auto found = _readsByUid.find(packet.meta.txnUid);
    panic_if(found == _readsByUid.end(),
             "AXI_PROTOCOL: unknown R txnUid");
    ReadState &state = found->second;
    panic_if(packet.beatCount != state.ar.request.beatCount ||
             packet.beatIndex >= state.ar.request.beatCount,
             "AXI_PROTOCOL: R beat index/count conflict");
    panic_if(packet.last !=
             (packet.beatIndex + 1 == packet.beatCount),
             "AXI_PROTOCOL: RLAST position is invalid");
    panic_if(state.received[packet.beatIndex].has_value(),
             "AXI_PROTOCOL: duplicate R beat");
    state.received[packet.beatIndex] = packet;
    pumpReadyR();
}

void
AxiInitiatorState::pumpReadyR()
{
    for (auto &[uid, state] : _readsByUid) {
        while (!_rReady.full() &&
               state.nextReadyBeat < state.received.size() &&
               state.received[state.nextReadyBeat].has_value()) {
            ReadyR ready;
            ready.txnUid = uid;
            ready.packet = std::move(
                *state.received[state.nextReadyBeat]);
            state.received[state.nextReadyBeat].reset();
            ++state.nextReadyBeat;
            panic_if(!_rReady.push(std::move(ready)),
                     "AXI source R ready FIFO overflow");
        }
        if (_rReady.full())
            return;
    }
}

bool
AxiInitiatorState::tryConsumeB(AxiBBeat &beat)
{
    if (_bReady.empty())
        return false;
    const AxiBPacket packet = _bReady.front();
    _bReady.pop();
    const uint64_t ordinal = _writeOrdinalByUid.at(packet.meta.txnUid);
    const WriteState &state = _writesByOrdinal.at(ordinal);
    beat.axiId = packet.meta.axiId;
    beat.resp = packet.resp;
    releaseWriteQuota(state);
    _writeOrdinalByUid.erase(packet.meta.txnUid);
    _writesByOrdinal.erase(ordinal);
    return true;
}

bool
AxiInitiatorState::tryConsumeR(AxiRBeat &beat)
{
    if (_rReady.empty())
        return false;
    ReadyR ready = std::move(_rReady.front());
    _rReady.pop();
    ReadState &state = _readsByUid.at(ready.txnUid);
    beat.axiId = ready.packet.meta.axiId;
    beat.last = ready.packet.last;
    beat.resp = ready.packet.resp;
    beat.payloadDigest = ready.packet.payloadDigest;
    beat.functionalData = std::move(ready.packet.functionalData);
    ++state.consumedBeats;
    if (beat.last) {
        panic_if(state.consumedBeats != state.ar.request.beatCount,
                 "AXI_PROTOCOL: RLAST consumed before complete burst");
        releaseReadQuota(state);
        _readsByUid.erase(ready.txnUid);
    }
    pumpReadyR();
    return true;
}

AxiInitiatorOccupancy
AxiInitiatorState::occupancy() const
{
    return {_awReady.size(), _wReady.size(), _bReady.size(),
            _arReady.size(), _rReady.size(), _unboundBursts,
            _unboundBeats, _writeOrdinalByUid.size(), _readsByUid.size()};
}

std::string
AxiInitiatorState::finalConsistencyError() const
{
    if (_openWOrdinal) {
        std::ostringstream out;
        out << "source W burst without WLAST, writeOrdinal="
            << *_openWOrdinal;
        return out.str();
    }
    for (const auto &[ordinal, state] : _writesByOrdinal) {
        if (!state.aw) {
            std::ostringstream out;
            out << "source W burst without AW, writeOrdinal=" << ordinal;
            return out.str();
        }
        if (state.observedW != state.aw->request.beatCount ||
            !state.sawWlast) {
            std::ostringstream out;
            out << "source partial W burst, writeOrdinal=" << ordinal;
            return out.str();
        }
    }
    return {};
}

} // namespace axi
} // namespace gem5

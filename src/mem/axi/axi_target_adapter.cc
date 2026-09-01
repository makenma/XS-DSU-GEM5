#include "mem/axi/axi_target_adapter.hh"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <tuple>

#include "base/logging.hh"

namespace gem5
{
namespace axi
{

AxiSimpleMemory::AxiSimpleMemory(std::vector<AxiRange> ranges)
    : _ranges(std::move(ranges))
{
    std::sort(_ranges.begin(), _ranges.end(),
              [](const auto &lhs, const auto &rhs) {
                  return std::tie(lhs.start, lhs.end, lhs.dstNode) <
                         std::tie(rhs.start, rhs.end, rhs.dstNode);
              });
    const std::string error = validateAxiRanges(_ranges);
    if (!error.empty())
        throw std::invalid_argument(error);
}

bool
AxiSimpleMemory::contains(uint64_t address) const
{
    for (const auto &range : _ranges) {
        if (address >= range.start && address < range.end)
            return true;
    }
    return false;
}

uint8_t
AxiSimpleMemory::readByte(uint64_t address) const
{
    panic_if(!contains(address),
             "AXI simple memory read outside configured range");
    const auto found = _bytes.find(address);
    return found == _bytes.end() ? 0 : found->second;
}

void
AxiSimpleMemory::writeByte(uint64_t address, uint8_t value)
{
    panic_if(!contains(address),
             "AXI simple memory write outside configured range");
    _bytes[address] = value;
}

void
AxiSimpleMemory::fill(uint8_t value)
{
    for (const auto &range : _ranges) {
        for (uint64_t address = range.start; address < range.end; ++address) {
            _bytes[address] = value;
            panic_if(address == std::numeric_limits<uint64_t>::max(),
                     "AXI memory range iteration overflow");
        }
    }
}

std::vector<uint8_t>
AxiSimpleMemory::readBeat(const AxiAddressRequest &request,
                          uint16_t beat_index,
                          uint32_t data_bus_bytes) const
{
    std::vector<uint8_t> data(data_bus_bytes, 0);
    const uint64_t beat_bytes = uint64_t{1} << request.size;
    const uint64_t beat_address = axiBeatAddress(request, beat_index);
    const uint64_t bus_base =
        (beat_address / data_bus_bytes) * data_bus_bytes;
    const uint64_t lane_base = beat_address - bus_base;
    for (uint64_t offset = 0; offset < beat_bytes; ++offset)
        data[lane_base + offset] = readByte(beat_address + offset);
    return data;
}

void
AxiSimpleMemory::commitWrite(const AxiAddressRequest &request,
                             const std::vector<AxiDataPacket> &beats,
                             uint32_t data_bus_bytes)
{
    panic_if(beats.size() != request.beatCount,
             "AXI simple memory commit has incomplete burst");
    for (uint16_t index = 0; index < request.beatCount; ++index) {
        AxiWBeat beat;
        beat.last = beats[index].last;
        beat.byteStrobe = beats[index].byteStrobe;
        beat.payloadDigest = beats[index].payloadDigest;
        beat.functionalData = beats[index].functionalData;
        requireValidAxiWriteBeat(request, index, beat, data_bus_bytes);
        const uint64_t beat_address = axiBeatAddress(request, index);
        const uint64_t bus_base =
            (beat_address / data_bus_bytes) * data_bus_bytes;
        for (uint32_t lane = 0; lane < data_bus_bytes; ++lane) {
            if ((beat.byteStrobe >> lane) & 1)
                panic_if(!contains(bus_base + lane),
                         "AXI simple memory write span escaped target range");
        }
    }

    for (const auto &beat : beats) {
        const uint64_t beat_address = axiBeatAddress(
            request, beat.beatIndex);
        const uint64_t bus_base =
            (beat_address / data_bus_bytes) * data_bus_bytes;
        for (uint32_t lane = 0; lane < data_bus_bytes; ++lane) {
            if ((beat.byteStrobe >> lane) & 1)
                _bytes[bus_base + lane] = beat.functionalData[lane];
        }
    }
}

AxiTargetState::AxiTargetState(const AxiTargetConfig &config)
    : _config(config), _memory(config.memoryRanges),
      _bReady(config.bReadyDepth), _rReady(config.rReadyDepth)
{
    if (_config.dataBusBytes == 0 || _config.dataBusBytes > 64 ||
        _config.capacity.writeContexts == 0 ||
        _config.capacity.writeAssemblyBeats == 0 ||
        _config.capacity.readContexts == 0 ||
        _config.capacity.readResponseBeats == 0 ||
        _config.orphanTransactions == 0 || _config.orphanBeats == 0 ||
        _config.bReadyDepth == 0 || _config.rReadyDepth == 0 ||
        _config.writeServiceDepth == 0 ||
        _config.readServiceDepth == 0) {
        throw std::invalid_argument("AXI target capacities must be positive");
    }
    const std::string quota_error = validateAxiQuotaSums(
        _config.sourceQuotas, _config.capacity);
    if (!quota_error.empty())
        throw std::invalid_argument(quota_error);
    for (const auto &range : _config.memoryRanges) {
        if (range.dstNode != _config.dstNode)
            throw std::invalid_argument(
                "AXI target memory range has a different dstNode");
    }
    for (const auto &[uid, response] : _config.transactionFaults) {
        (void)uid;
        if (response != AxiResp::Okay && response != AxiResp::SlvErr)
            throw std::invalid_argument(
                "AXI target fault response must be OKAY or SLVERR");
    }
}

void
AxiTargetState::validateAddressPacket(const AxiAddressPacket &packet,
                                      AxiChannel channel) const
{
    panic_if(channel != AxiChannel::Aw && channel != AxiChannel::Ar,
             "AXI internal address packet channel is invalid");
    panic_if(packet.meta.dstNode != _config.dstNode,
             "AXI_PROTOCOL: address packet reached wrong target");
    const auto validation = validateAxiBurst(
        packet.request, _config.dataBusBytes);
    requireValidAxiBurst(validation);
    panic_if(packet.request.axiId != packet.meta.axiId,
             "AXI_PROTOCOL: address packet AXI ID conflict");
    panic_if(packet.request.qos != packet.meta.qos || packet.meta.qos > 15,
             "AXI_PROTOCOL: address packet AxQOS conflict or out of range");
    panic_if(packet.request.beatCount == 0 ||
             packet.request.beatCount > 256,
             "AXI_PROTOCOL: address packet beatCount invalid");
    if (packet.decodeResp != AxiResp::DecErr) {
        bool contained = false;
        for (const auto &range : _config.memoryRanges) {
            if (packet.request.address >= range.start &&
                validation.lastByteExclusive <= range.end) {
                contained = true;
                break;
            }
        }
        panic_if(!contained,
                 "AXI_PROTOCOL: non-DECERR request span is outside target range");
    }
}

void
AxiTargetState::validateDataPacket(const AxiDataPacket &packet) const
{
    panic_if(packet.meta.dstNode != _config.dstNode,
             "AXI_PROTOCOL: W packet reached wrong target");
    panic_if(packet.meta.qos > 15,
             "AXI_PROTOCOL: W packet AxQOS is out of range");
    panic_if(packet.beatCount == 0 || packet.beatCount > 256 ||
             packet.beatIndex >= packet.beatCount,
             "AXI_PROTOCOL: W packet beat index/count invalid");
    panic_if(packet.functionalData.size() != _config.dataBusBytes,
             "AXI_PROTOCOL: W packet data is not one full bus word");
    panic_if(packet.last != (packet.beatIndex + 1 == packet.beatCount),
             "AXI_PROTOCOL: WLAST position does not match beatCount");
}

bool
AxiTargetState::canReserveWrite(const AxiCommonMeta &meta,
                                uint16_t beat_count) const
{
    if (_writes.size() == _config.capacity.writeContexts ||
        beat_count > _config.capacity.writeAssemblyBeats -
                     _reservedWriteBeats) {
        return false;
    }
    const AxiEndpointKey source{meta.srcNode, meta.srcPort};
    const auto limit_it = _config.sourceQuotas.find(source);
    panic_if(limit_it == _config.sourceQuotas.end(),
             "AXI_PROTOCOL: target has no write quota for source");
    const AxiQuota active = _activeQuota.count(source) ?
        _activeQuota.at(source) : AxiQuota{};
    const AxiQuota limit = limit_it->second;
    panic_if(active.writeContexts > limit.writeContexts ||
             active.writeBeats > limit.writeBeats,
             "AXI target active write quota exceeds static limit");
    return active.writeContexts < limit.writeContexts &&
           beat_count <= limit.writeBeats - active.writeBeats;
}

bool
AxiTargetState::canReserveRead(const AxiCommonMeta &meta,
                               uint16_t beat_count) const
{
    if (_reads.size() == _config.capacity.readContexts ||
        beat_count > _config.capacity.readResponseBeats -
                     _reservedReadBeats) {
        return false;
    }
    const AxiEndpointKey source{meta.srcNode, meta.srcPort};
    const auto limit_it = _config.sourceQuotas.find(source);
    panic_if(limit_it == _config.sourceQuotas.end(),
             "AXI_PROTOCOL: target has no read quota for source");
    const AxiQuota active = _activeQuota.count(source) ?
        _activeQuota.at(source) : AxiQuota{};
    const AxiQuota limit = limit_it->second;
    panic_if(active.readContexts > limit.readContexts ||
             active.readBeats > limit.readBeats,
             "AXI target active read quota exceeds static limit");
    return active.readContexts < limit.readContexts &&
           beat_count <= limit.readBeats - active.readBeats;
}

void
AxiTargetState::reserveWrite(WriteContext &context)
{
    panic_if(!canReserveWrite(context.meta, context.beatCount),
             "AXI target write reservation exceeded static quota");
    const AxiEndpointKey source{context.meta.srcNode, context.meta.srcPort};
    ++_activeQuota[source].writeContexts;
    _activeQuota[source].writeBeats += context.beatCount;
    _reservedWriteBeats += context.beatCount;
    context.quotaReserved = true;
}

void
AxiTargetState::releaseWrite(const WriteContext &context)
{
    const AxiEndpointKey source{context.meta.srcNode, context.meta.srcPort};
    AxiQuota &active = _activeQuota[source];
    panic_if(!context.quotaReserved || active.writeContexts == 0 ||
             active.writeBeats < context.beatCount ||
             _reservedWriteBeats < context.beatCount,
             "AXI target write reservation underflow");
    --active.writeContexts;
    active.writeBeats -= context.beatCount;
    _reservedWriteBeats -= context.beatCount;
}

void
AxiTargetState::reserveRead(ReadContext &context)
{
    panic_if(!canReserveRead(context.ar.meta,
                            context.ar.request.beatCount),
             "AXI target read reservation exceeded static quota");
    const AxiEndpointKey source{
        context.ar.meta.srcNode, context.ar.meta.srcPort};
    ++_activeQuota[source].readContexts;
    _activeQuota[source].readBeats += context.ar.request.beatCount;
    _reservedReadBeats += context.ar.request.beatCount;
    context.quotaReserved = true;
}

void
AxiTargetState::releaseRead(const ReadContext &context)
{
    const AxiEndpointKey source{
        context.ar.meta.srcNode, context.ar.meta.srcPort};
    AxiQuota &active = _activeQuota[source];
    const uint16_t beats = context.ar.request.beatCount;
    panic_if(!context.quotaReserved || active.readContexts == 0 ||
             active.readBeats < beats || _reservedReadBeats < beats,
             "AXI target read reservation underflow");
    --active.readContexts;
    active.readBeats -= beats;
    _reservedReadBeats -= beats;
}

bool
AxiTargetState::canAcceptAw(const AxiAddressPacket &packet) const
{
    const auto found = _writes.find(packet.meta.txnUid);
    if (found != _writes.end())
        return true;
    return canReserveWrite(packet.meta, packet.request.beatCount);
}

bool
AxiTargetState::canAcceptW(const AxiDataPacket &packet) const
{
    const auto found = _writes.find(packet.meta.txnUid);
    if (found != _writes.end())
        return true;
    if (_orphanTransactions == _config.orphanTransactions ||
        packet.beatCount > _config.orphanBeats - _orphanBeats) {
        return false;
    }
    return canReserveWrite(packet.meta, packet.beatCount);
}

bool
AxiTargetState::canAcceptAr(const AxiAddressPacket &packet) const
{
    if (_reads.count(packet.meta.txnUid))
        return true;
    return canReserveRead(packet.meta, packet.request.beatCount);
}

void
AxiTargetState::acceptAw(const AxiAddressPacket &packet, uint64_t now)
{
    validateAddressPacket(packet, AxiChannel::Aw);
    auto found = _writes.find(packet.meta.txnUid);
    if (found == _writes.end()) {
        panic_if(!canAcceptAw(packet),
                 "AXI target AW accepted without capacity");
        WriteContext context;
        context.meta = packet.meta;
        context.beatCount = packet.request.beatCount;
        context.aw = packet;
        context.beats.resize(context.beatCount);
        reserveWrite(context);
        _writes.emplace(packet.meta.txnUid, std::move(context));
    } else {
        WriteContext &context = found->second;
        panic_if(context.aw.has_value(), "AXI_PROTOCOL: duplicate AW packet");
        panic_if(context.meta.srcNode != packet.meta.srcNode ||
                 context.meta.srcPort != packet.meta.srcPort ||
                 context.meta.dstNode != packet.meta.dstNode ||
                 context.meta.axiId != packet.meta.axiId ||
                 context.meta.qos != packet.meta.qos ||
                 context.meta.targetSeq != packet.meta.targetSeq ||
                 context.meta.responseSeq != packet.meta.responseSeq ||
                 context.beatCount != packet.request.beatCount,
                 "AXI_PROTOCOL: AW fields conflict with orphan W context");
        context.aw = packet;
        if (context.wasOrphan) {
            panic_if(_orphanTransactions == 0 ||
                     _orphanBeats < context.beatCount,
                     "AXI orphan accounting underflow");
            --_orphanTransactions;
            _orphanBeats -= context.beatCount;
            context.wasOrphan = false;
        }
    }
    ++_progress.qosTransactions[packet.meta.qos];
    advance(now);
}

void
AxiTargetState::acceptW(const AxiDataPacket &packet, uint64_t now)
{
    validateDataPacket(packet);
    auto found = _writes.find(packet.meta.txnUid);
    if (found == _writes.end()) {
        panic_if(!canAcceptW(packet),
                 "AXI target W accepted without orphan capacity");
        WriteContext context;
        context.meta = packet.meta;
        context.beatCount = packet.beatCount;
        context.beats.resize(context.beatCount);
        context.wasOrphan = true;
        reserveWrite(context);
        ++_orphanTransactions;
        _orphanBeats += context.beatCount;
        found = _writes.emplace(
            packet.meta.txnUid, std::move(context)).first;
    }

    WriteContext &context = found->second;
    panic_if(context.beatCount != packet.beatCount ||
             context.meta.srcNode != packet.meta.srcNode ||
             context.meta.srcPort != packet.meta.srcPort ||
             context.meta.dstNode != packet.meta.dstNode ||
             context.meta.axiId != packet.meta.axiId ||
             context.meta.qos != packet.meta.qos ||
             context.meta.targetSeq != packet.meta.targetSeq ||
             context.meta.responseSeq != packet.meta.responseSeq,
             "AXI_PROTOCOL: W fields conflict with write context");
    panic_if(context.beats[packet.beatIndex].has_value(),
             "AXI_PROTOCOL: duplicate W beat");
    context.beats[packet.beatIndex] = packet;
    ++context.receivedBeats;
    advance(now);
}

void
AxiTargetState::acceptAr(const AxiAddressPacket &packet, uint64_t now)
{
    validateAddressPacket(packet, AxiChannel::Ar);
    panic_if(_reads.count(packet.meta.txnUid),
             "AXI_PROTOCOL: duplicate AR packet");
    panic_if(!canAcceptAr(packet),
             "AXI target AR accepted without capacity");
    ReadContext context;
    context.ar = packet;
    reserveRead(context);
    _reads.emplace(packet.meta.txnUid, std::move(context));
    ++_progress.qosTransactions[packet.meta.qos];
    advance(now);
}

uint32_t
AxiTargetState::serviceLatency(const AxiCommonMeta &meta, bool read) const
{
    const uint32_t base = read ? _config.readBaseLatency :
                                 _config.writeBaseLatency;
    const auto extra = _config.extraLatency.find(meta.txnUid);
    if (extra == _config.extraLatency.end())
        return base;
    panic_if(extra->second > std::numeric_limits<uint32_t>::max() - base,
             "AXI target service latency overflows uint32");
    return base + extra->second;
}

AxiResp
AxiTargetState::serviceResponse(const AxiCommonMeta &meta,
                                AxiResp decode_resp) const
{
    const auto fault = _config.transactionFaults.find(meta.txnUid);
    if (fault == _config.transactionFaults.end())
        return decode_resp;
    return mergeResp(decode_resp, fault->second);
}

void
AxiTargetState::startServices(uint64_t now)
{
    while (_activeWriteServices < _config.writeServiceDepth) {
        WriteContext *selected = nullptr;
        for (auto &[uid, context] : _writes) {
            if (context.serviceStarted || !context.aw ||
                context.receivedBeats != context.beatCount) {
                continue;
            }
            if (!selected ||
                std::tie(context.aw->meta.acceptedTick, uid) <
                std::tie(selected->aw->meta.acceptedTick,
                         selected->meta.txnUid)) {
                selected = &context;
            }
        }
        if (!selected)
            break;
        const uint64_t latency = serviceLatency(selected->meta, false);
        panic_if(latency > std::numeric_limits<uint64_t>::max() - now,
                 "AXI target write service-ready time overflows uint64");
        selected->serviceStarted = true;
        selected->serviceReadyAt = now + latency;
        selected->serviceResponse = serviceResponse(
            selected->meta, selected->aw->decodeResp);
        ++_activeWriteServices;
    }

    while (_activeReadServices < _config.readServiceDepth) {
        ReadContext *selected = nullptr;
        for (auto &[uid, context] : _reads) {
            if (context.serviceStarted)
                continue;
            if (!selected ||
                std::tie(context.ar.meta.acceptedTick, uid) <
                std::tie(selected->ar.meta.acceptedTick,
                         selected->ar.meta.txnUid)) {
                selected = &context;
            }
        }
        if (!selected)
            break;
        const uint64_t latency = serviceLatency(selected->ar.meta, true);
        panic_if(latency > std::numeric_limits<uint64_t>::max() - now,
                 "AXI target read service-ready time overflows uint64");
        selected->serviceStarted = true;
        selected->serviceReadyAt = now + latency;
        selected->serviceResponse = serviceResponse(
            selected->ar.meta, selected->ar.decodeResp);
        ++_activeReadServices;
    }
}

void
AxiTargetState::updateServiceReady(uint64_t now)
{
    for (auto &[uid, context] : _writes) {
        (void)uid;
        if (context.serviceStarted && !context.serviceReady &&
            now >= context.serviceReadyAt) {
            panic_if(_activeWriteServices == 0,
                     "AXI target write service accounting underflow");
            context.serviceReady = true;
            --_activeWriteServices;
            ++_progress.serviceReady;
        }
    }
    for (auto &[uid, context] : _reads) {
        (void)uid;
        if (context.serviceStarted && !context.serviceReady &&
            now >= context.serviceReadyAt) {
            panic_if(_activeReadServices == 0,
                     "AXI target read service accounting underflow");
            context.serviceReady = true;
            --_activeReadServices;
            ++_progress.serviceReady;
        }
    }
}

void
AxiTargetState::commitWrites(uint64_t now)
{
    (void)now;
    while (!_bReady.full()) {
        uint64_t selected_uid = 0;
        WriteContext *selected = nullptr;
        OrderingKey selected_key;
        for (auto &[uid, context] : _writes) {
            if (!context.serviceReady || !context.aw)
                continue;
            const OrderingKey key{
                context.meta.srcNode, context.meta.srcPort,
                context.meta.axiId, false, context.meta.dstNode};
            const uint64_t expected = _nextTargetCommit[key];
            panic_if(context.meta.targetSeq < expected,
                     "AXI_PROTOCOL: write targetSeq retired twice");
            if (context.meta.targetSeq != expected) {
                if (!context.orderingBlockCounted) {
                    context.orderingBlockCounted = true;
                    ++_progress.sameIdReadyBlocked;
                }
                continue;
            }
            if (!selected ||
                std::tie(context.serviceReadyAt, uid) <
                std::tie(selected->serviceReadyAt, selected_uid)) {
                selected_uid = uid;
                selected = &context;
                selected_key = key;
            }
        }
        if (!selected)
            return;

        std::vector<AxiDataPacket> beats;
        beats.reserve(selected->beatCount);
        for (uint16_t index = 0; index < selected->beatCount; ++index) {
            panic_if(!selected->beats[index],
                     "AXI_PROTOCOL: write burst has a missing beat");
            panic_if(selected->beats[index]->beatIndex != index,
                     "AXI_PROTOCOL: write beat index conflict");
            AxiWBeat beat;
            beat.last = selected->beats[index]->last;
            beat.byteStrobe = selected->beats[index]->byteStrobe;
            beat.payloadDigest = selected->beats[index]->payloadDigest;
            beat.functionalData = selected->beats[index]->functionalData;
            requireValidAxiWriteBeat(
                selected->aw->request, index, beat, _config.dataBusBytes);
            beats.push_back(std::move(*selected->beats[index]));
        }

        AxiResp response = selected->serviceResponse;
        for (const auto &beat : beats)
            response = mergeResp(response, beat.resp);
        panic_if(response == AxiResp::ExOkay,
                 "AXI model must never generate EXOKAY");
        if (response == AxiResp::Okay) {
            _memory.commitWrite(
                selected->aw->request, beats, _config.dataBusBytes);
        }

        AxiBPacket b;
        b.meta = selected->aw->meta;
        b.meta.semanticBytes = 8;
        b.resp = response;
        panic_if(!_bReady.push(std::move(b)),
                 "AXI target B ready FIFO overflow");
        releaseWrite(*selected);
        ++_completedWrites;
        ++_progress.architecturalCommits;
        ++_progress.writesCommitted;
        ++_nextTargetCommit[selected_key];
        _writes.erase(selected_uid);
    }
}

void
AxiTargetState::commitReads(uint64_t now)
{
    while (true) {
        uint64_t selected_uid = 0;
        ReadContext *selected = nullptr;
        OrderingKey selected_key;
        for (auto &[uid, context] : _reads) {
            if (!context.serviceReady || context.responseEligible)
                continue;
            const OrderingKey key{
                context.ar.meta.srcNode, context.ar.meta.srcPort,
                context.ar.meta.axiId, true, context.ar.meta.dstNode};
            const uint64_t expected = _nextTargetCommit[key];
            panic_if(context.ar.meta.targetSeq < expected,
                     "AXI_PROTOCOL: read targetSeq retired twice");
            if (context.ar.meta.targetSeq != expected) {
                if (!context.orderingBlockCounted) {
                    context.orderingBlockCounted = true;
                    ++_progress.sameIdReadyBlocked;
                }
                continue;
            }
            if (!selected ||
                std::tie(context.serviceReadyAt, uid) <
                std::tie(selected->serviceReadyAt, selected_uid)) {
                selected_uid = uid;
                selected = &context;
                selected_key = key;
            }
        }
        if (!selected)
            return;

        selected->frozenBeats.reserve(selected->ar.request.beatCount);
        for (uint16_t index = 0;
             index < selected->ar.request.beatCount; ++index) {
            AxiDataPacket packet;
            packet.meta = selected->ar.meta;
            packet.meta.semanticBytes = static_cast<uint32_t>(
                uint64_t{1} << selected->ar.request.size);
            packet.beatIndex = index;
            packet.beatCount = selected->ar.request.beatCount;
            packet.last = index + 1 == packet.beatCount;
            packet.resp = selected->serviceResponse;
            packet.address = selected->ar.request.address;
            if (packet.resp == AxiResp::Okay) {
                packet.functionalData = _memory.readBeat(
                    selected->ar.request, index, _config.dataBusBytes);
            } else {
                packet.functionalData.assign(_config.dataBusBytes, 0);
            }
            packet.payloadDigest = payloadDigest(packet.functionalData);
            selected->frozenBeats.push_back(std::move(packet));
        }
        selected->responseEligible = true;
        selected->responseEligibleAt = now;
        ++_nextTargetCommit[selected_key];
        ++_progress.architecturalCommits;
        ++_progress.readsCommitted;
    }
}

void
AxiTargetState::generateReads()
{
    while (!_rReady.full()) {
        uint64_t selected_uid = 0;
        ReadContext *selected = nullptr;
        for (auto &[uid, context] : _reads) {
            if (!context.responseEligible ||
                context.nextBeat >= context.frozenBeats.size()) {
                continue;
            }
            if (!selected ||
                std::tie(context.responseEligibleAt, uid,
                         context.nextBeat) <
                std::tie(selected->responseEligibleAt, selected_uid,
                         selected->nextBeat)) {
                selected_uid = uid;
                selected = &context;
            }
        }
        if (!selected)
            return;
        panic_if(!_rReady.push(std::move(
                     selected->frozenBeats[selected->nextBeat])),
                 "AXI target R ready FIFO overflow");
        ++selected->nextBeat;
        if (selected->nextBeat == selected->frozenBeats.size()) {
            releaseRead(*selected);
            ++_completedReads;
            _reads.erase(selected_uid);
        }
    }
}

void
AxiTargetState::advance(uint64_t now)
{
    panic_if(now < _now, "AXI target time moved backwards");
    _now = now;
    startServices(now);
    updateServiceReady(now);
    commitWrites(now);
    commitReads(now);
    generateReads();
    // A zero-latency service may become available after a commit frees a
    // bounded service slot.  Starting it here preserves the edge boundary;
    // it will become ready on this or a later explicit advance call.
    startServices(now);
    updateServiceReady(now);
    commitWrites(now);
    commitReads(now);
    generateReads();
}

void
AxiTargetState::popBPacket()
{
    panic_if(_bReady.empty(), "popBPacket called on empty queue");
    _bReady.pop();
    advance(_now);
}

void
AxiTargetState::popRPacket()
{
    panic_if(_rReady.empty(), "popRPacket called on empty queue");
    _rReady.pop();
    advance(_now);
}

AxiTargetOccupancy
AxiTargetState::occupancy() const
{
    return {_writes.size(), _reservedWriteBeats,
            _orphanTransactions, _orphanBeats,
            _reads.size(), _reservedReadBeats,
            _bReady.size(), _rReady.size(),
            _activeWriteServices, _activeReadServices};
}

} // namespace axi
} // namespace gem5

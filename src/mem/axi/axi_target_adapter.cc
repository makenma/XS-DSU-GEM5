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
    return containsRange(address, 1);
}

bool
AxiSimpleMemory::containsRange(uint64_t address, uint64_t size) const
{
    if (size == 0 || size > std::numeric_limits<uint64_t>::max() - address)
        return false;
    const uint64_t end = address + size;
    for (const auto &range : _ranges) {
        if (range.end <= address)
            continue;
        if (range.start > address)
            return false;
        if (end <= range.end)
            return true;
        address = range.end;
    }
    return false;
}

uint8_t
AxiSimpleMemory::readByte(uint64_t address) const
{
    panic_if(!contains(address),
             "AXI simple memory read outside configured range");
    const auto found = _pages.find(address / PageBytes);
    return found == _pages.end() ? 0 : found->second[address % PageBytes];
}

void
AxiSimpleMemory::writeByte(uint64_t address, uint8_t value)
{
    panic_if(!contains(address),
             "AXI simple memory write outside configured range");
    _pages[address / PageBytes][address % PageBytes] = value;
}

void
AxiSimpleMemory::fill(uint8_t value)
{
    for (const auto &range : _ranges) {
        uint64_t address = range.start;
        while (address < range.end) {
            auto &page = _pages[address / PageBytes];
            const uint64_t offset = address % PageBytes;
            const uint64_t size = std::min(range.end - address,
                                             PageBytes - offset);
            std::fill_n(page.begin() + offset, size, value);
            address += size;
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
                writeByte(bus_base + lane, beat.functionalData[lane]);
        }
    }
}

AxiTargetState::AxiTargetState(const AxiTargetConfig &config)
    : _config(config), _memory(config.memoryRanges),
      _rReady(config.rReadyDepth)
{
    if (_config.syntheticHbmEnabled)
        _syntheticBackend.emplace(_config.syntheticHbmBytesPerCycle,
                                   _config.syntheticHbmQueueDepth);
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
    for (const auto &[uid, response] : _config.transactionPostCommitFaults) {
        (void)uid;
        if (response != AxiResp::Okay && response != AxiResp::SlvErr)
            throw std::invalid_argument(
                "AXI target post-commit fault response must be OKAY or SLVERR");
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
        found = _writes.emplace(packet.meta.txnUid, std::move(context)).first;
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
    indexPendingWriteService(found->second);
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
    indexPendingWriteService(context);
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
    _pendingReadServices.emplace(packet.meta.acceptedTick, packet.meta.txnUid);
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
AxiTargetState::indexPendingWriteService(const WriteContext &context)
{
    if (!context.serviceStarted && context.aw &&
        context.receivedBeats == context.beatCount) {
        const Tick accepted_tick = _syntheticBackend ?
            context.meta.acceptedTick : context.aw->meta.acceptedTick;
        _pendingWriteServices.emplace(accepted_tick, context.meta.txnUid);
    }
}

void
AxiTargetState::startServices(uint64_t now)
{
    if (_syntheticBackend) {
        while (!_syntheticBackend->full()) {
            WriteContext *write = nullptr;
            ReadContext *read = nullptr;
            const AxiCommonMeta *selected = nullptr;
            if (_activeWriteServices < _config.writeServiceDepth &&
                !_pendingWriteServices.empty()) {
                write = &_writes.at(_pendingWriteServices.top().second);
                selected = &write->meta;
            }
            if (_activeReadServices < _config.readServiceDepth &&
                !_pendingReadServices.empty() &&
                (!selected || _pendingReadServices.top() <
                                  _pendingWriteServices.top())) {
                read = &_reads.at(_pendingReadServices.top().second);
                selected = &read->ar.meta;
                write = nullptr;
            }
            if (!selected)
                break;
            const auto &request = read ? read->ar.request : write->aw->request;
            const uint64_t bytes = uint64_t(request.beatCount) << request.size;
            const bool accepted = _syntheticBackend->trySubmit(
                selected->txnUid, read ? SyntheticHbmDirection::Read
                                       : SyntheticHbmDirection::Write,
                bytes, now, serviceLatency(*selected, read != nullptr));
            panic_if(!accepted, "synthetic HBM admission changed without a grant");
            if (read) {
                read->serviceStarted = true;
                read->serviceResponse = serviceResponse(*selected,
                                                         read->ar.decodeResp);
                ++_activeReadServices;
                _pendingReadServices.pop();
            } else {
                write->serviceStarted = true;
                write->serviceResponse = serviceResponse(*selected,
                                                          write->aw->decodeResp);
                ++_activeWriteServices;
                _pendingWriteServices.pop();
            }
        }
        return;
    }
    while (_activeWriteServices < _config.writeServiceDepth &&
           !_pendingWriteServices.empty()) {
        WriteContext *selected =
            &_writes.at(_pendingWriteServices.top().second);
        const uint64_t latency = serviceLatency(selected->meta, false);
        panic_if(latency > std::numeric_limits<uint64_t>::max() - now,
                 "AXI target write service-ready time overflows uint64");
        selected->serviceStarted = true;
        selected->serviceReadyAt = now + latency;
        selected->serviceResponse = serviceResponse(
            selected->meta, selected->aw->decodeResp);
        ++_activeWriteServices;
        _pendingWriteServices.pop();
    }

    while (_activeReadServices < _config.readServiceDepth &&
           !_pendingReadServices.empty()) {
        ReadContext *selected =
            &_reads.at(_pendingReadServices.top().second);
        const uint64_t latency = serviceLatency(selected->ar.meta, true);
        panic_if(latency > std::numeric_limits<uint64_t>::max() - now,
                 "AXI target read service-ready time overflows uint64");
        selected->serviceStarted = true;
        selected->serviceReadyAt = now + latency;
        selected->serviceResponse = serviceResponse(
            selected->ar.meta, selected->ar.decodeResp);
        ++_activeReadServices;
        _pendingReadServices.pop();
    }
}

void
AxiTargetState::updateServiceReady(uint64_t now)
{
    if (_syntheticBackend) {
        _syntheticBackend->advance(now);
        auto completed = _syntheticBackend->takeCompleted();
        std::sort(completed.begin(), completed.end(),
            [](const auto &lhs, const auto &rhs) {
                if (lhs.direction != rhs.direction)
                    return lhs.direction == SyntheticHbmDirection::Write;
                return lhs.uid < rhs.uid;
            });
        for (const auto &completion : completed) {
            if (completion.direction == SyntheticHbmDirection::Write) {
                WriteContext &context = _writes.at(completion.uid);
                markWriteServiceReady(context, completion.completedAt);
            } else {
                ReadContext &context = _reads.at(completion.uid);
                markReadServiceReady(context, completion.completedAt);
            }
        }
        return;
    }
    for (auto &[uid, context] : _writes) {
        if (context.serviceStarted && !context.serviceReady &&
            now >= context.serviceReadyAt) {
            markWriteServiceReady(context, context.serviceReadyAt);
        }
    }
    for (auto &[uid, context] : _reads) {
        if (context.serviceStarted && !context.serviceReady &&
            now >= context.serviceReadyAt) {
            markReadServiceReady(context, context.serviceReadyAt);
        }
    }
}

void
AxiTargetState::markWriteServiceReady(WriteContext &context, uint64_t ready_at)
{
    panic_if(_activeWriteServices == 0,
             "AXI target write service accounting underflow");
    context.serviceReadyAt = ready_at;
    context.serviceReady = true;
    --_activeWriteServices;
    ++_progress.serviceReady;
    const auto key = context.orderingKey();
    const uint64_t uid = context.meta.txnUid;
    _writeCommitOrder.emplace(key, context.meta.targetSeq, uid);
    _writeScanObligations.insert(uid);
    const auto gate = _nextTargetCommit.find(key);
    const uint64_t expected = gate == _nextTargetCommit.end() ? 0 : gate->second;
    if (context.meta.targetSeq == expected)
        _eligibleWrites.insert(writeCommitCandidateKey(context));
}

void
AxiTargetState::markReadServiceReady(ReadContext &context, uint64_t ready_at)
{
    panic_if(_activeReadServices == 0,
             "AXI target read service accounting underflow");
    context.serviceReadyAt = ready_at;
    context.serviceReady = true;
    --_activeReadServices;
    ++_progress.serviceReady;
    const auto key = context.orderingKey();
    const uint64_t uid = context.ar.meta.txnUid;
    _readCommitOrder.emplace(key, context.ar.meta.targetSeq, uid);
    _readScanObligations.insert(uid);
    const auto gate = _nextTargetCommit.find(key);
    const uint64_t expected = gate == _nextTargetCommit.end() ? 0 : gate->second;
    if (context.ar.meta.targetSeq == expected)
        _eligibleReads.emplace(context.serviceReadyAt, uid);
}

AxiTargetState::WriteCommitCandidateKey
AxiTargetState::writeCommitCandidateKey(const WriteContext &context) const
{
    const uint64_t uid = context.meta.txnUid;
    const auto rank = _config.writeCommitTieBreakRanks.find(uid);
    return {context.serviceReadyAt,
            rank == _config.writeCommitTieBreakRanks.end() ?
                std::numeric_limits<uint32_t>::max() : rank->second,
            uid};
}

void
AxiTargetState::commitWrites(uint64_t now)
{
    (void)now;
    while (_bReady.size() < _config.bReadyDepth) {
        while (!_writeScanObligations.empty()) {
            const auto obligation = _writeScanObligations.begin();
            WriteContext &context = _writes.at(*obligation);
            const uint64_t expected = _nextTargetCommit[context.orderingKey()];
            panic_if(context.meta.targetSeq < expected,
                     "AXI_PROTOCOL: write targetSeq retired twice");
            if (context.meta.targetSeq != expected &&
                !context.orderingBlockCounted) {
                context.orderingBlockCounted = true;
                ++_progress.sameIdReadyBlocked;
            }
            _writeScanObligations.erase(obligation);
        }
        if (_eligibleWrites.empty())
            return;
        const auto candidate = _eligibleWrites.begin();
        const uint64_t selected_uid = std::get<2>(*candidate);
        WriteContext *selected = &_writes.at(selected_uid);
        const OrderingKey selected_key = selected->orderingKey();
        const uint64_t retired_seq = selected->meta.targetSeq;

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
        bool policy_commit = true;
        if (preCommitPolicy) {
            const auto decision = preCommitPolicy->onWritePreCommit(
                selected->aw->request, beats, response);
            policy_commit = decision.commit;
            response = mergeResp(response, decision.response);
        }
        const auto post_commit = _config.transactionPostCommitFaults.find(
            selected->meta.txnUid);
        const bool post_commit_fault = post_commit !=
            _config.transactionPostCommitFaults.end() &&
            response == AxiResp::Okay;
        if ((response == AxiResp::Okay && policy_commit) ||
            post_commit_fault) {
            _memory.commitWrite(
                selected->aw->request, beats, _config.dataBusBytes);
            for (const auto &beat : beats)
                _progress.writeCommittedBytes += __builtin_popcountll(
                    beat.byteStrobe);
            _progress.lastWriteCommitCycle = now;
        }
        uint64_t b_ready_at = now;
        const auto b_delay = _config.bEjectionDelay.find(selected->meta.txnUid);
        if (b_delay != _config.bEjectionDelay.end())
            b_ready_at += b_delay->second;
        const AxiResp target_commit_response = response;
        if (post_commit_fault)
            response = mergeResp(response, post_commit->second);
        const auto observer_replays =
            _config.writeCommitObserverReplays.find(selected->meta.txnUid);
        for (auto *observer : writeCommitObservers) {
            observer->onAxiWriteCommittedWithMeta(
                *selected->aw, beats, target_commit_response);
            if (observer_replays != _config.writeCommitObserverReplays.end()) {
                for (uint32_t replay = 0; replay < observer_replays->second; ++replay)
                    observer->onAxiWriteCommittedWithMeta(
                    *selected->aw, beats, target_commit_response);
            }
        }

        AxiBPacket b;
        b.meta = selected->aw->meta;
        b.meta.semanticBytes = 8;
        b.resp = response;
        _bReady.push_back(ReadyB{std::move(b), b_ready_at});
        releaseWrite(*selected);
        ++_completedWrites;
        ++_progress.architecturalCommits;
        ++_progress.writesCommitted;
        ++_nextTargetCommit[selected_key];
        _writes.erase(selected_uid);
        _eligibleWrites.erase(candidate);
        _writeCommitOrder.erase({selected_key, retired_seq, selected_uid});
        const auto old_end = _writeCommitOrder.upper_bound(
            {selected_key, retired_seq, std::numeric_limits<uint64_t>::max()});
        for (auto it = _writeCommitOrder.lower_bound(
                 {selected_key, retired_seq, 0}); it != old_end; ++it) {
            const uint64_t uid = std::get<2>(*it);
            _eligibleWrites.erase(writeCommitCandidateKey(_writes.at(uid)));
            _writeScanObligations.insert(uid);
        }
        const uint64_t expected = _nextTargetCommit.at(selected_key);
        const auto new_end = _writeCommitOrder.upper_bound(
            {selected_key, expected, std::numeric_limits<uint64_t>::max()});
        for (auto it = _writeCommitOrder.lower_bound(
                 {selected_key, expected, 0}); it != new_end; ++it) {
            const uint64_t uid = std::get<2>(*it);
            _eligibleWrites.insert(writeCommitCandidateKey(_writes.at(uid)));
        }
    }
}

void
AxiTargetState::commitReads(uint64_t now)
{
    while (true) {
        while (!_readScanObligations.empty()) {
            const auto obligation = _readScanObligations.begin();
            ReadContext &context = _reads.at(*obligation);
            const uint64_t expected = _nextTargetCommit[context.orderingKey()];
            panic_if(context.ar.meta.targetSeq < expected,
                     "AXI_PROTOCOL: read targetSeq retired twice");
            if (context.ar.meta.targetSeq != expected &&
                !context.orderingBlockCounted) {
                context.orderingBlockCounted = true;
                ++_progress.sameIdReadyBlocked;
            }
            _readScanObligations.erase(obligation);
        }
        if (_eligibleReads.empty())
            return;
        const auto candidate = _eligibleReads.begin();
        const uint64_t selected_uid = candidate->second;
        ReadContext *selected = &_reads.at(selected_uid);
        const OrderingKey selected_key = selected->orderingKey();
        const uint64_t retired_seq = selected->ar.meta.targetSeq;

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
        if (selected->serviceResponse == AxiResp::Okay)
            _progress.readCommittedBytes +=
                uint64_t(selected->ar.request.beatCount) <<
                selected->ar.request.size;
        selected->responseEligibleAt = now;
        ++_nextTargetCommit[selected_key];
        ++_progress.architecturalCommits;
        ++_progress.readsCommitted;
        _eligibleReads.erase(candidate);
        _readCommitOrder.erase({selected_key, retired_seq, selected_uid});
        const auto old_end = _readCommitOrder.upper_bound(
            {selected_key, retired_seq, std::numeric_limits<uint64_t>::max()});
        for (auto it = _readCommitOrder.lower_bound(
                 {selected_key, retired_seq, 0}); it != old_end; ++it) {
            const uint64_t uid = std::get<2>(*it);
            const ReadContext &context = _reads.at(uid);
            _eligibleReads.erase({context.serviceReadyAt, uid});
            _readScanObligations.insert(uid);
        }
        const uint64_t expected = _nextTargetCommit.at(selected_key);
        const auto new_end = _readCommitOrder.upper_bound(
            {selected_key, expected, std::numeric_limits<uint64_t>::max()});
        for (auto it = _readCommitOrder.lower_bound(
                 {selected_key, expected, 0}); it != new_end; ++it) {
            const uint64_t uid = std::get<2>(*it);
            _eligibleReads.emplace(_reads.at(uid).serviceReadyAt, uid);
        }
        _readGenerationReady.emplace(
            AxiEndpointKey{selected->ar.meta.srcNode, selected->ar.meta.srcPort},
            selected->responseEligibleAt, selected_uid);
    }
}

void
AxiTargetState::generateReads()
{
    while (!_rReady.full()) {
        if (_readGenerationReady.empty())
            return;
        auto grant = _lastReadSource ? _readGenerationReady.upper_bound(
            {*_lastReadSource, std::numeric_limits<uint64_t>::max(),
             std::numeric_limits<uint64_t>::max()}) : _readGenerationReady.begin();
        if (grant == _readGenerationReady.end())
            grant = _readGenerationReady.begin();
        const AxiEndpointKey source = std::get<0>(*grant);
        const uint64_t selected_uid = std::get<2>(*grant);
        ReadContext &selected = _reads.at(selected_uid);
        panic_if(!_rReady.push(std::move(
                     selected.frozenBeats[selected.nextBeat])),
                 "AXI target R ready FIFO overflow");
        _lastReadSource = source;
        ++selected.nextBeat;
        if (selected.nextBeat == selected.frozenBeats.size()) {
            releaseRead(selected);
            ++_completedReads;
            _readGenerationReady.erase(grant);
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

bool
AxiTargetState::hasBPacket() const
{
    return readyBIndex().has_value();
}

std::optional<size_t>
AxiTargetState::readyBIndex() const
{
    std::optional<size_t> selected;
    for (size_t index = 0; index < _bReady.size(); ++index) {
        const ReadyB &candidate = _bReady[index];
        if (candidate.readyAt > _now)
            continue;
        bool ordered = true;
        for (size_t older = 0; older < _bReady.size(); ++older) {
            const AxiCommonMeta &lhs = _bReady[older].packet.meta;
            const AxiCommonMeta &rhs = candidate.packet.meta;
            if (lhs.srcNode == rhs.srcNode && lhs.srcPort == rhs.srcPort &&
                lhs.dstNode == rhs.dstNode && lhs.axiId == rhs.axiId &&
                lhs.responseSeq < rhs.responseSeq) {
                ordered = false;
                break;
            }
        }
        if (!ordered)
            continue;
        if (!selected ||
            std::tie(candidate.readyAt, index) <
            std::tie(_bReady[*selected].readyAt, *selected))
            selected = index;
    }
    return selected;
}

const AxiBPacket &
AxiTargetState::frontBPacket() const
{
    const auto index = readyBIndex();
    panic_if(!index, "frontBPacket called without an eligible B response");
    return _bReady[*index].packet;
}

void
AxiTargetState::popBPacket()
{
    const auto index = readyBIndex();
    panic_if(!index, "popBPacket called without an eligible B response");
    _bReady.erase(_bReady.begin() + *index);
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

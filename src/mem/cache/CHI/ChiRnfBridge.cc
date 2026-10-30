#include "mem/cache/CHI/ChiRnfBridge.hh"

#include <algorithm>
#include <cstring>
#include <utility>

#include "base/cprintf.hh"
#include "base/logging.hh"

namespace gem5::Chi
{

ChiRnfBridge::Stats::Stats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(cpuRequests, statistics::units::Count::get(), "Accepted CPU packets"),
      ADD_STAT(hits, statistics::units::Count::get(), "CPU packets hitting RNF cache"),
      ADD_STAT(misses, statistics::units::Count::get(), "CPU packets requiring CHI"),
      ADD_STAT(readUnique, statistics::units::Count::get(), "ReadUnique requests"),
      ADD_STAT(writebacks, statistics::units::Count::get(), "WriteBackFull requests"),
      ADD_STAT(snoops, statistics::units::Count::get(), "Received coherence snoops"),
      ADD_STAT(dirtySnoops, statistics::units::Count::get(), "Snoops transferring dirty data"),
      ADD_STAT(atomicOps, statistics::units::Count::get(), "Completed AMOs and swaps"),
      ADD_STAT(storeConditionals, statistics::units::Count::get(), "SC packets reaching RNF"),
      ADD_STAT(failedStoreConditionals, statistics::units::Count::get(), "RNF reservation failures"),
      ADD_STAT(deferredSnoops, statistics::units::Count::get(), "Ownership snoops briefly held for LR/SC forward progress")
{}

ChiRnfBridge::CpuPort::CpuPort(const std::string &name, ChiRnfBridge &owner,
                              PortID id)
    : ResponsePort(name, &owner, id), bridge(owner), index(id)
{}

AddrRangeList
ChiRnfBridge::CpuPort::getAddrRanges() const
{
    return {bridge.memoryRange};
}

bool
ChiRnfBridge::CpuPort::recvTimingReq(PacketPtr pkt)
{
    if (bridge.recvCpu(pkt, index)) return true;
    retryRequest = true;
    return false;
}

Tick
ChiRnfBridge::CpuPort::recvAtomic(PacketPtr pkt)
{
    panic("%s accepts timing CPU memory requests only; atomic bypass is forbidden",
          bridge.name());
}

void
ChiRnfBridge::CpuPort::recvFunctional(PacketPtr pkt)
{
    // Initial ELF/DTB loading belongs to SNF.functional/SystemPort. A runtime
    // debugger must not silently read stale backing memory behind a dirty RNF.
    auto *line = bridge.findLine(pkt->getAddr(), !pkt->isSecure());
    panic_if(!line || pkt->getOffset(LineBytes) + pkt->getSize() > LineBytes,
             "%s functional CPU access requires a resident complete line; "
             "load images via SNF.functional", bridge.name());
    auto *bytes = line->data.data() + pkt->getOffset(LineBytes);
    if (pkt->isRead()) {
        pkt->setData(bytes);
    } else if (pkt->isWrite()) {
        pkt->writeData(bytes);
        line->dirty = true;
        bridge.invalidateReservation(line->address, line->ns);
    } else {
        panic("%s unsupported functional CPU packet", bridge.name());
    }
    pkt->makeResponse();
}

void
ChiRnfBridge::CpuPort::recvRespRetry()
{
    panic_if(bridge.cpuPort != index || !bridge.responseBlocked,
             "%s unexpected CPU response retry", bridge.name());
    bridge.responseBlocked = false;
    bridge.wakeup();
}

ChiRnfBridge::ChiRnfBridge(const Params &p)
    : ClockedObject(p), Consumer(this), nodeId(p.node_id), homes(p.hnf_ids),
      memoryRange(p.range), hitLatency(p.hit_latency),
      reservationCycles(p.reservation_cycles), cacheWays(p.cache_ways),
      cache(p.cache_lines),
      chi(p.name + ".chi", this),
      tickEvent([this] { tick(); }, p.name + ".tick"), stats(this)
{
    fatal_if(cache.empty() || homes.empty() || hitLatency == Cycles(0),
             "%s requires cache lines, home nodes and positive hit latency", name());
    fatal_if(cacheWays < 2 || cache.size() % cacheWays,
             "%s requires at least two ways and an integral number of sets "
             "so instruction fetch cannot perpetually evict an LR reservation", name());
    fatal_if(memoryRange.start() % LineBytes || memoryRange.size() % LineBytes,
             "%s RAM range must align to 64-byte lines", name());
    for (unsigned i = 0; i < p.port_cpu_side_connection_count; ++i)
        cpuPorts.emplace_back(std::make_unique<CpuPort>(
            csprintf("%s.cpu_side[%u]", name(), i), *this, i));
}

void
ChiRnfBridge::init()
{
    ClockedObject::init();
    fatal_if(!chi.isConnected() || cpuPorts.empty(),
             "%s requires CPU and mesh connections", name());
    for (const auto &port : cpuPorts) port->sendRangeChange();
}

Port &
ChiRnfBridge::getPort(const std::string &name, PortID id)
{
    if (name == "chi") return chi;
    if (name == "cpu_side") {
        panic_if(id < 0 || id >= cpuPorts.size(), "Invalid RNF CPU port");
        return *cpuPorts[id];
    }
    return ClockedObject::getPort(name, id);
}

void
ChiRnfBridge::print(std::ostream &out) const
{
    out << name() << " RNF=" << nodeId << " phase=" << unsigned(phase)
        << " queued=" << queuedFlits << " cpu=" << bool(cpuPacket);
}

void
ChiRnfBridge::wakeup()
{
    if (!tickEvent.scheduled()) schedule(tickEvent, nextCycle());
}

uint32_t
ChiRnfBridge::allocateTxn()
{
    panic_if(!nextTxn, "%s exhausted transaction identifiers", name());
    return nextTxn++;
}

ChiRnfBridge::Line *
ChiRnfBridge::findLine(Addr address, bool ns)
{
    return const_cast<Line *>(std::as_const(*this).findLine(address, ns));
}

const ChiRnfBridge::Line *
ChiRnfBridge::findLine(Addr address, bool ns) const
{
    const size_t base = setBase(address);
    for (unsigned way = 0; way < cacheWays; ++way) {
        const auto &line = cache[base + way];
        if (line.valid && line.address == (address & ~Addr(63)) && line.ns == ns)
            return &line;
    }
    return nullptr;
}

ChiRnfBridge::Line &
ChiRnfBridge::slot(Addr address)
{
    const size_t base = setBase(address);
    Line *victim = &cache[base];
    for (unsigned way = 0; way < cacheWays; ++way) {
        auto &line = cache[base + way];
        if (!line.valid) return line;
        if (line.lastTouch < victim->lastTouch) victim = &line;
    }
    return *victim;
}

bool
ChiRnfBridge::recvCpu(PacketPtr pkt, PortID port)
{
    if (cpuPacket) return false;
    panic_if(!pkt->isRequest() || !pkt->getAddrRange().isSubset(memoryRange) ||
             pkt->getOffset(LineBytes) + pkt->getSize() > LineBytes,
             "%s CPU packet must address one coherent RAM line", name());
    panic_if(pkt->cacheResponding(), "%s must be the CPU's private cache", name());
    cpuPacket = pkt;
    cpuPort = port;
    cpuReady = clockEdge(hitLatency) + pkt->headerDelay + pkt->payloadDelay;
    pkt->headerDelay = pkt->payloadDelay = 0;
    cpuResponse = false;
    responseBlocked = false;
    stats.cpuRequests++;
    if (findLine(pkt->getAddr(), !pkt->isSecure())) stats.hits++;
    else stats.misses++;
    wakeup();
    return true;
}

void
ChiRnfBridge::send(ChannelType channel, const FlitVariant &flit)
{
    panic_if(queuedFlits >= ResponseLimit + 4,
             "%s response buffering exceeded its finite bound", name());
    outgoing[channel].push_back(flit);
    ++queuedFlits;
    wakeup();
}

void
ChiRnfBridge::sendLineData(uint32_t target, uint32_t txn, uint32_t dbid,
                          uint8_t opcode, const Line &line, bool valid)
{
    for (unsigned beat = 0; beat < 2; ++beat) {
        RawDat dat{};
        dat.srcid = nodeId;
        dat.tgtid = target;
        dat.txnid = txn;
        dat.dbid = dbid;
        dat.opcode = opcode;
        // Both supported snoops invalidate the RNF. CopyBackWrData instead
        // reports the state being written back before relinquishing it.
        dat.resp = opcode == 0x01 ? (line.dirty ? 4 : 0) :
                   (valid ? (line.dirty ? 6 : 2) : 0);
        dat.passDirty = valid && line.dirty;
        dat.dataid = beat * 2;
        dat.last = beat == 1;
        dat.setPayload(line.data.data() + 32 * beat, 32);
        send(DAT, dat);
    }
}

void
ChiRnfBridge::startRead(Addr address, bool ns)
{
    activeTxn = allocateTxn();
    activeHome = home(address);
    fill = Line{};
    fill.address = address & ~Addr(63);
    fill.ns = ns;
    fillMask = 0;
    phase = Phase::Read;
    RawReq req{};
    req.srcid = nodeId;
    req.tgtid = activeHome;
    req.txnid = activeTxn;
    req.opcode = 0x07;
    req.size = 6;
    req.addr = fill.address;
    req.ns = ns;
    req.AllowRetry = 0;
    req.expCompAck = true;
    send(REQ, req);
    stats.readUnique++;
}

void
ChiRnfBridge::startWriteback(const Line &line)
{
    victim = line;
    victimInvalidated = false;
    activeTxn = allocateTxn();
    activeHome = home(line.address);
    phase = Phase::WritebackDbid;
    RawReq req{};
    req.srcid = nodeId;
    req.tgtid = activeHome;
    req.txnid = activeTxn;
    req.opcode = 0x1b;
    req.size = 6;
    req.addr = line.address;
    req.ns = line.ns;
    req.AllowRetry = 0;
    req.expCompAck = false;
    send(REQ, req);
    stats.writebacks++;
    // Replacement may fail an LR/SC pair, but the line remains snoopable
    // until the home accepts the writeback and returns completion.
    invalidateReservation(line.address, line.ns);
    snoopCpu(line.address, line.ns);
}

void
ChiRnfBridge::snoopCpu(Addr address, bool ns)
{
    auto req = std::make_shared<Request>(address, LineBytes, 0,
                                         Request::funcRequestorId);
    if (!ns) req->setFlags(Request::SECURE);
    Packet snoop(req, MemCmd::InvalidateReq);
    snoop.setExpressSnoop();
    for (const auto &port : cpuPorts)
        if (port->isSnooping()) port->sendTimingSnoopReq(&snoop);
}

void
ChiRnfBridge::invalidateReservation(Addr address, bool ns)
{
    if (reservationValid && reservationNs == ns &&
        (reservationAddress & ~Addr(63)) == (address & ~Addr(63)))
        reservationValid = false;
}

void
ChiRnfBridge::invalidate(Line &line)
{
    invalidateReservation(line.address, line.ns);
    snoopCpu(line.address, line.ns);
    line.valid = false;
}

void
ChiRnfBridge::receive(const RawSnp &snp)
{
    panic_if(snp.tgtid != nodeId || (snp.opcode != 0x07 && snp.opcode != 0x09),
             "%s unsupported snoop opcode=%#x target=%u", name(), snp.opcode,
             snp.tgtid);
    stats.snoops++;
    Line *line = findLine(snp.addr, snp.ns);
    if (line && (line->dirty || snp.opcode == 0x07)) {
        if (line->dirty) stats.dirtySnoops++;
        sendLineData(snp.srcid, snp.txnid, 0, 0x01, *line, true);
    } else {
        RawRsp rsp{};
        rsp.srcid = nodeId;
        rsp.tgtid = snp.srcid;
        rsp.txnid = snp.txnid;
        rsp.opcode = 0x01;
        send(RSP, rsp);
    }
    if (line) invalidate(*line);
    else {
        invalidateReservation(snp.addr, snp.ns);
        snoopCpu(snp.addr & ~Addr(63), snp.ns);
    }
    if ((phase == Phase::WritebackDbid || phase == Phase::WritebackComp) &&
        victim.address == (snp.addr & ~Addr(63)) && victim.ns == snp.ns) {
        // A preceding transaction won the home's address hazard. Its snoop
        // transferred ownership, so the queued WBF must return Invalid data.
        panic_if(phase != Phase::WritebackDbid,
                 "%s snoop raced a DBID-issued writeback on the same home", name());
        victimInvalidated = true;
    }
}

void
ChiRnfBridge::receive(const RawRsp &rsp)
{
    panic_if(rsp.tgtid != nodeId || rsp.srcid != activeHome ||
             rsp.txnid != activeTxn, "%s unmatched CHI response", name());
    if (phase == Phase::WritebackDbid && rsp.opcode == 0x06) {
        sendLineData(activeHome, rsp.dbid, rsp.dbid, 0x02,
                     victim, !victimInvalidated);
        phase = Phase::WritebackComp;
    } else if (phase == Phase::WritebackComp && rsp.opcode == 0x04) {
        if (auto *line = findLine(victim.address, victim.ns)) invalidate(*line);
        phase = Phase::Idle;
    } else if (phase == Phase::Maintenance && rsp.opcode == 0x04) {
        phase = Phase::Idle;
        finishCpu();
    } else {
        panic("%s unexpected CHI RSP opcode=%#x phase=%u", name(),
              rsp.opcode, unsigned(phase));
    }
}

void
ChiRnfBridge::receive(const RawDat &dat)
{
    panic_if(phase != Phase::Read || dat.opcode != 0x04 || dat.tgtid != nodeId ||
             dat.srcid != activeHome || dat.txnid != activeTxn ||
             dat.payloadSize() != 32 || (dat.dataid != 0 && dat.dataid != 2) ||
             bool(dat.last) != (dat.dataid == 2),
             "%s unmatched or malformed CompData", name());
    const unsigned beat = dat.dataid / 2;
    panic_if(fillMask & (1U << beat), "%s duplicate CompData beat", name());
    std::copy_n(dat.payloadData(), 32, fill.data.begin() + 32 * beat);
    fillMask |= 1U << beat;
    fill.dirty |= dat.passDirty || (dat.resp & 4);
    if (fillMask != 3) return;
    panic_if(slot(fill.address).valid, "%s fill would overwrite an RNF victim", name());
    fill.valid = true;
    fill.lastTouch = ++cacheSequence;
    slot(fill.address) = fill;
    RawRsp ack{};
    ack.srcid = nodeId;
    ack.tgtid = dat.srcid;
    ack.txnid = dat.dbid;
    ack.opcode = 0x02;
    send(RSP, ack);
    phase = Phase::Idle;
}

void
ChiRnfBridge::accessLine(Line &line)
{
    line.lastTouch = ++cacheSequence;
    PacketPtr pkt = cpuPacket;
    auto *bytes = line.data.data() + pkt->getOffset(LineBytes);
    if (pkt->cmd == MemCmd::SwapReq) {
        stats.atomicOps++;
        if (pkt->isAtomicOp()) {
            pkt->setData(bytes);
            (*pkt->getAtomicOp())(bytes);
        } else {
            std::array<uint8_t, LineBytes> value{};
            pkt->writeData(value.data());
            pkt->setData(bytes);
            bool overwrite = true;
            if (pkt->req->isCondSwap()) {
                panic_if(pkt->getSize() != 4 && pkt->getSize() != 8,
                         "%s invalid conditional swap size", name());
                const uint64_t expected = pkt->req->getExtraData();
                overwrite = !std::memcmp(bytes, &expected, pkt->getSize());
            }
            if (overwrite) std::copy_n(value.data(), pkt->getSize(), bytes);
        }
        line.dirty = true;
        invalidateReservation(line.address, line.ns);
    } else if (pkt->isRead()) {
        pkt->setData(bytes);
        if (pkt->isLLSC()) {
            reservationValid = true;
            reservationAddress = pkt->getAddr() & ~Addr(15);
            reservationNs = line.ns;
            reservationContext = pkt->req->contextId();
            reservationUntil = clockEdge(reservationCycles);
        }
    } else if (pkt->isWrite()) {
        bool store = true;
        if (pkt->isLLSC()) {
            stats.storeConditionals++;
            store = reservationValid && reservationNs == line.ns &&
                reservationAddress == (pkt->getAddr() & ~Addr(15)) &&
                reservationContext == pkt->req->contextId();
            reservationValid = false;
            pkt->req->setExtraData(store ? 1 : 0);
            if (!store) stats.failedStoreConditionals++;
        }
        if (store) {
            pkt->writeData(bytes); // Honors every byte-enable bit.
            line.dirty = true;
            invalidateReservation(line.address, line.ns);
        }
    } else {
        panic("%s unsupported CPU packet %s", name(), pkt->cmdString());
    }
    finishCpu();
}

void
ChiRnfBridge::progressCpu()
{
    if (!cpuPacket || cpuResponse || phase != Phase::Idle ||
        curTick() < cpuReady || queuedFlits >= ResponseLimit)
        return;
    const Addr address = cpuPacket->getAddr() & ~Addr(63);
    const bool ns = !cpuPacket->isSecure();
    // An invalidated reservation fails without reacquiring the line, so a
    // remote owner cannot accidentally make a stale SC succeed after refill.
    if (cpuPacket->isWrite() && cpuPacket->isLLSC() &&
        (!reservationValid || reservationAddress != (cpuPacket->getAddr() & ~Addr(15)) ||
         reservationNs != ns || reservationContext != cpuPacket->req->contextId())) {
        stats.storeConditionals++;
        stats.failedStoreConditionals++;
        reservationValid = false;
        cpuPacket->req->setExtraData(0);
        finishCpu();
        return;
    }
    if (cpuPacket->isClean() || cpuPacket->isInvalidate()) {
        activeTxn = allocateTxn();
        activeHome = home(address);
        phase = Phase::Maintenance;
        RawReq req{};
        req.srcid = nodeId;
        req.tgtid = activeHome;
        req.txnid = activeTxn;
        req.addr = address;
        req.ns = ns;
        req.opcode = 0x09;
        req.size = 6;
        req.AllowRetry = 0;
        req.expCompAck = false;
        req.snoopMe = true;
        send(REQ, req);
        return;
    }
    if (auto *line = findLine(address, ns)) {
        accessLine(*line);
    } else if (slot(address).valid) {
        startWriteback(slot(address));
    } else {
        startRead(address, ns);
    }
}

void
ChiRnfBridge::finishCpu()
{
    panic_if(!cpuPacket || cpuResponse, "%s duplicate CPU completion", name());
    if (cpuPacket->needsResponse()) {
        cpuPacket->makeResponse();
        cpuResponse = true;
    } else {
        delete cpuPacket;
        cpuPacket = nullptr;
        cpuPort = InvalidPortID;
    }
}

void
ChiRnfBridge::retryCpuResponse()
{
    if (!cpuResponse || responseBlocked) return;
    PacketPtr pkt = cpuPacket;
    const PortID port = cpuPort;
    // The CPU can synchronously submit its next packet during the callback.
    cpuPacket = nullptr;
    cpuResponse = false;
    cpuPort = InvalidPortID;
    if (!cpuPorts[port]->sendTimingResp(pkt)) {
        panic_if(cpuPacket, "%s CPU accepted new work while rejecting response", name());
        cpuPacket = pkt;
        cpuResponse = true;
        cpuPort = port;
        responseBlocked = true;
    }
}

void
ChiRnfBridge::sendCpuRetries()
{
    if (cpuPacket) return;
    for (const auto &port : cpuPorts) {
        if (!port->retryRequest) continue;
        port->retryRequest = false;
        port->sendRetryReq();
        if (cpuPacket) break;
    }
}

bool
ChiRnfBridge::idle() const
{
    if (cpuPacket || phase != Phase::Idle || queuedFlits || deferredSnoop ||
        !chi.drained())
        return false;
    return std::none_of(cpuPorts.begin(), cpuPorts.end(),
        [](const auto &port) { return port->retryRequest; });
}

bool
ChiRnfBridge::needsTick() const
{
    return !idle();
}

void
ChiRnfBridge::tick()
{
    for (unsigned c = 0; c < NUM_CHANNELS; ++c) {
        const auto ch = static_cast<ChannelType>(c);
        if (!outgoing[c].empty() && chi.peerHasRxCredit(ch) &&
            chi.enqueueRx(ch, outgoing[c].front())) {
            outgoing[c].pop_front();
            --queuedFlits;
        }
    }
    if (deferredSnoop && queuedFlits < ResponseLimit &&
        (!reservationValid || reservationNs != deferredSnoop->ns ||
         (reservationAddress & ~Addr(63)) != (deferredSnoop->addr & ~Addr(63)) ||
         curTick() >= deferredUntil)) {
        // The wire credit is held until the deferred snoop is actually
        // processed. This remains a finite, backpressured transaction slot.
        const RawSnp snp = *deferredSnoop;
        deferredSnoop.reset();
        receive(snp);
        chi.returnTxCredit(SNP);
    }
    for (unsigned c = 0; c < NUM_CHANNELS; ++c) {
        const auto ch = static_cast<ChannelType>(c);
        if (queuedFlits >= ResponseLimit || !chi.hasTxFlit(ch, true)) continue;
        auto flit = ch == SNP ? chi.getTxFlitNoCredit(ch) : chi.getTxFlit(ch);
        panic_if(!flit, "%s CHI flit vanished", name());
        if (ch == SNP) {
            const auto &snp = std::get<RawSnp>(*flit);
            const bool protect = reservationValid && reservationNs == snp.ns &&
                (reservationAddress & ~Addr(63)) == (snp.addr & ~Addr(63)) &&
                curTick() < reservationUntil;
            if (protect) {
                panic_if(deferredSnoop, "%s duplicate snoop of reserved line", name());
                deferredSnoop = snp;
                deferredUntil = reservationUntil;
                stats.deferredSnoops++;
                continue;
            }
        }
        std::visit([this](const auto &raw) {
            using T = std::decay_t<decltype(raw)>;
            if constexpr (std::is_same_v<T, RawReq>)
                panic("%s received a request on an RNF", name());
            else receive(raw);
        }, *flit);
        if (ch == SNP) chi.returnTxCredit(SNP);
    }
    progressCpu();
    retryCpuResponse();
    sendCpuRetries();
    if (needsTick()) {
        if (!tickEvent.scheduled()) schedule(tickEvent, nextCycle());
    } else {
        if (tickEvent.scheduled()) deschedule(tickEvent);
        if (drainState() == DrainState::Draining) signalDrainDone();
    }
}

DrainState
ChiRnfBridge::drain()
{
    if (idle()) {
        if (tickEvent.scheduled()) deschedule(tickEvent);
        return DrainState::Drained;
    }
    wakeup();
    return DrainState::Draining;
}

void
ChiRnfBridge::drainResume()
{
    if (!idle()) wakeup();
}

void
ChiRnfBridge::serialize(CheckpointOut &cp) const
{
    panic_if(!idle(), "%s checkpoint requires a drained RNF", name());
    ClockedObject::serialize(cp);
    SERIALIZE_SCALAR(nextTxn);
    SERIALIZE_SCALAR(cacheSequence);
    SERIALIZE_SCALAR(cacheWays);
    SERIALIZE_SCALAR(reservationValid);
    SERIALIZE_SCALAR(reservationAddress);
    SERIALIZE_SCALAR(reservationNs);
    SERIALIZE_SCALAR(reservationContext);
    SERIALIZE_SCALAR(reservationUntil);
    const unsigned count = cache.size();
    SERIALIZE_SCALAR(count);
    for (unsigned i = 0; i < count; ++i) {
        ScopedCheckpointSection section(cp, csprintf("line%u", i));
        const auto &line = cache[i];
        paramOut(cp, "address", line.address);
        paramOut(cp, "ns", line.ns);
        paramOut(cp, "valid", line.valid);
        paramOut(cp, "dirty", line.dirty);
        paramOut(cp, "lastTouch", line.lastTouch);
        arrayParamOut(cp, "data", line.data.data(), line.data.size());
    }
}

void
ChiRnfBridge::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    UNSERIALIZE_SCALAR(nextTxn);
    UNSERIALIZE_SCALAR(cacheSequence);
    unsigned savedWays;
    paramIn(cp, "cacheWays", savedWays);
    fatal_if(savedWays != cacheWays, "%s checkpoint RNF associativity changed", name());
    UNSERIALIZE_SCALAR(reservationValid);
    UNSERIALIZE_SCALAR(reservationAddress);
    UNSERIALIZE_SCALAR(reservationNs);
    UNSERIALIZE_SCALAR(reservationContext);
    UNSERIALIZE_SCALAR(reservationUntil);
    unsigned count;
    UNSERIALIZE_SCALAR(count);
    fatal_if(count != cache.size(), "%s checkpoint RNF capacity changed", name());
    for (unsigned i = 0; i < count; ++i) {
        ScopedCheckpointSection section(cp, csprintf("line%u", i));
        auto &line = cache[i];
        paramIn(cp, "address", line.address);
        paramIn(cp, "ns", line.ns);
        paramIn(cp, "valid", line.valid);
        paramIn(cp, "dirty", line.dirty);
        paramIn(cp, "lastTouch", line.lastTouch);
        arrayParamIn(cp, "data", line.data.data(), line.data.size());
    }
}

} // namespace gem5::Chi

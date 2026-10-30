#include "mem/cache/CHI/HomePocq.hh"

#include <algorithm>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HomePocq.hh"
#include "mem/cache/CHI/HomeNodeFull.hh"

namespace gem5::Chi
{

HomePocq::HomePocq(HomeNodeFull *hnf, std::size_t entry_num)
    : Consumer(hnf), m_homenode(hnf), graphPanel(),
      m_entry_num(entry_num), m_pocq(m_entry_num, graphPanel),
      processEvent([this] { process(); },
                   getObject()->name() + ".processEvent")
{}

bool HomePocq::idle() const
{
    return m_pocq.size() == 0 && incoming.empty() && m_pending.empty() &&
           !processEvent.scheduled();
}

void HomePocq::serialize(CheckpointOut &cp) const
{
    panic_if(!idle(), "POCQ checkpoint requires drained transactions");
    SERIALIZE_SCALAR(nextToken);
    SERIALIZE_SCALAR(nextWireId);
}

void HomePocq::unserialize(CheckpointIn &cp)
{
    panic_if(!idle(), "Cannot restore into a busy POCQ");
    UNSERIALIZE_SCALAR(nextToken);
    UNSERIALIZE_SCALAR(nextWireId);
    // Retired victim windows cannot intersect any new post-restore lookup.
    victimBuffers = PocqVictimBuffer{};
    issuedActions.clear();
}

const BaseFlit &
HomePocq::baseFlit(const FlitVariant &flit)
{
    return std::visit(
        [](const auto &raw) -> const BaseFlit & { return raw; }, flit);
}

uint32_t
HomePocq::allocateWireId()
{
    panic_if(nextWireId == 0, "HNF transaction ID space exhausted");
    return nextWireId++;
}

bool
HomePocq::allocate(const FlitVariant &flit)
{
    return allocateImpl(flit, baseFlit(flit).qosPriority(), false);
}

bool
HomePocq::allocate(const FlitVariant &flit, PoolPriority pool)
{
    return allocateImpl(flit, pool, true);
}

bool
HomePocq::allocateImpl(const FlitVariant &flit, PoolPriority priority,
                       bool qos_pool_reserved, bool internal_seq)
{
    // Preserve an escape entry for pending directory evictions. Without
    // this priority, external REQs can refill every retired slot until all
    // foreground entries replay on SEQ-full and no SEQ can enter POCQ.
    // When there is no SEQ work all configured entries remain available.
    if (!internal_seq && m_pocq.size() + 1 >= m_entry_num &&
        m_homenode->slcSf().needsPocqEntry())
        return false;
    if (!m_pocq.allocate(flit, priority, qos_pool_reserved))
        return false;
    m_pocq.queue.back().wireId = allocateWireId();
    DPRINTF(HomePocq, "allocate src=%u txn=%u internal=%d active=%u seq_waiting=%d\n",
            baseFlit(flit).srcId(), baseFlit(flit).txnId(), internal_seq,
            unsigned(m_pocq.size()), m_homenode->slcSf().model().needsPocqEntry());
    scheduleEvent(Cycles(1));
    return true;
}

bool
HomePocq::hasFreeEntry() const
{
    return m_pocq.hasFreeEntry();
}

std::optional<HomePocq::IssuedAction>
HomePocq::popIssuedAction()
{
    if (issuedActions.empty())
        return std::nullopt;
    IssuedAction result = issuedActions.front();
    issuedActions.pop_front();
    return result;
}

void
HomePocq::trigger(uint32_t srcid, uint32_t txnid, const PocqEvent &event)
{
    m_pending.push_back({srcid, txnid, event});
    if (!processEvent.scheduled())
        getObject()->schedule(processEvent, getObject()->nextCycle());
}

void
HomePocq::process()
{
    std::vector<Trigger> ready;
    ready.swap(m_pending);
    for (const auto &item : ready)
        step(item);
}

void
HomePocq::dispatch(Entry &entry, PocqEvent event)
{
    if (!entry.valid || entry.is_sleep || !entry.state)
        return;
    auto result = entry.step(event);
    panic_if(!result, "POCQ graph rejected event %u in %s/%s",
             static_cast<unsigned>(event.kind),
             pocqGraphName(entry.state->key().graph),
             pocqNodeRoleName(entry.state->key().role));
    entry.appendActions(*result);
}

void
HomePocq::step(const Trigger &trigger)
{
    if (auto *entry = m_pocq.find(trigger.srcid, trigger.txnid))
        dispatch(*entry, trigger.event);
    scheduleEvent(Cycles(1));
}

HomePocq::Entry *
HomePocq::findWire(uint32_t wire)
{
    for (auto &entry : m_pocq.queue) {
        if (entry.valid && entry.wireId == wire)
            return &entry;
    }
    return nullptr;
}

void
HomePocq::receive(const RawRsp &rsp)
{
    incoming.push_back({getObject()->nextCycle(), rsp});
    scheduleEvent(Cycles(1));
}

void
HomePocq::receive(const RawDat &dat)
{
    RawDat owned = dat;
    owned.ownPayload();
    incoming.push_back({getObject()->nextCycle(), std::move(owned)});
    scheduleEvent(Cycles(1));
}

void
HomePocq::consumeLink()
{
    while (!incoming.empty() && incoming.front().ready <= curTick()) {
        auto item = std::move(incoming.front());
        incoming.pop_front();
        std::visit([this](const auto &raw) { processLink(raw); }, item.flit);
    }
}

void
HomePocq::processLink(const RawRsp &rsp)
{
    // A writeback uses an independent wire ID so a late memory completion
    // cannot alias the request or a reused POCQ slot.
    for (auto &entry : m_pocq.queue) {
        if (!entry.valid || rsp.srcid != m_homenode->snNodeId())
            continue;
        for (auto &write : entry.writebacks) {
            if (write.wireId != rsp.txnid || !write.sent)
                continue;
            if (rsp.opcode == 0x06 || rsp.opcode == 0x05) {
                write.dbid = rsp.dbid;
                write.haveDbid = true;
            }
            if (rsp.opcode == 0x04 || rsp.opcode == 0x05)
                write.complete = true;
            scheduleEvent(Cycles(1));
            return;
        }
    }
    Entry *entry = findWire(rsp.txnid);
    if (!entry || entry->is_sleep)
        return;
    if (rsp.opcode == 0x02 &&
        rsp.srcid == entry->rawflit.req.srcid &&
        entry->completionSent && !entry->internalSeq) {
        PocqEvent event{PocqEventKind::CompAck,
                        PocqEventSource::LinkLayer};
        dispatch(*entry, event);
    } else if (rsp.opcode == 0x01) {
        auto target = entry->snoops.find(rsp.srcid);
        if (target != entry->snoops.end() && target->second.sent) {
            // SnpResp is a terminal, data-less reply. A data reply is only
            // terminal once both data beats have been collected.
            target->second.done = true;
        }
    } else if (rsp.opcode == 0x08 &&
               rsp.srcid == m_homenode->snNodeId() && entry->memorySent) {
        entry->memoryReceipt = true;
        if (entry->isAt(PocqNodeRole::WaitReadReceipt)) {
            dispatch(*entry, {PocqEventKind::ReadReceipt,
                              PocqEventSource::LinkLayer});
        }
    }
    scheduleEvent(Cycles(1));
}

void
HomePocq::processLink(const RawDat &dat)
{
    Entry *entry = findWire(dat.txnid);
    if (!entry || entry->is_sleep)
        return;
    const auto size = dat.payloadSize();
    const auto *bytes = dat.payloadData();
    panic_if(!bytes || (size != 32 && size != 64),
             "HNF DAT must carry 32 or 64 bytes");
    panic_if((dat.dataid != 0 && dat.dataid != 2) ||
             (size == 64 && dat.dataid != 0),
             "Invalid 64-byte HNF data beat ID");
    const unsigned beat = dat.dataid == 0 ? 0 : 1;
    auto collect = [&](SlcLineData &line, uint8_t &mask) {
        const uint8_t incomingMask = size == 64 ? 3 : (1 << beat);
        panic_if(mask & incomingMask, "Duplicate HNF DAT beat");
        std::copy_n(bytes, size, line.begin() + beat * 32);
        mask |= incomingMask;
    };
    if (decodeDat(dat.opcode).major == DatMajor::SnpRespData) {
        auto target = entry->snoops.find(dat.srcid);
        if (target == entry->snoops.end() || !target->second.sent)
            return;
        auto &reply = target->second;
        collect(reply.data, reply.beats);
        reply.dirty |= dat.passDirty || (dat.resp & 4);
        reply.done = reply.beats == 3;
    } else if (dat.opcode == 0x04 && entry->memorySent &&
               dat.srcid == m_homenode->snNodeId()) {
        collect(entry->line, entry->lineBeats);
        entry->memoryDbid = dat.dbid;
        entry->memoryDone = entry->lineBeats == 3;
    } else if (dat.opcode == 0x02 && entry->writeDbidSent &&
               entry->reqOpcode.minor == ReqMinor::WriteBack &&
               dat.srcid == entry->rawflit.req.srcid) {
        const uint8_t response = dat.resp | (dat.passDirty ? 4 : 0);
        panic_if(response != 0 && response != 2 && response != 6,
                 "WriteBackFull requires I, UC or UD CopyBackWrData");
        panic_if(entry->writeDataResp && *entry->writeDataResp != response,
                 "WriteBackFull data beats disagree on cache state");
        entry->writeDataResp = response;
        entry->writeDataValid = response != 0;
        entry->dirty = response == 6;
        collect(entry->line, entry->lineBeats);
    }
    scheduleEvent(Cycles(1));
}

void
HomePocq::collectSlcResults()
{
    while (auto result = m_homenode->slcSf().popResult()) {
        Entry *owner = nullptr;
        for (auto &entry : m_pocq.queue) {
            if (entry.valid && entry.slcAction &&
                entry.slcToken == result->token) {
                owner = &entry;
                break;
            }
        }
        panic_if(!owner, "SLCSF returned an unknown pipeline token");
        auto &entry = *owner;
        switch (result->kind) {
          case SlcSfResultKind::Replay:
            entry.pendingActions.push_front(*entry.slcAction);
            entry.slcAction.reset();
            entry.slcCommitted = false;
            break;
          case SlcSfResultKind::LookupDone:
            if (entry.slcAction->kind == PocqActionKind::StartSlcLookup) {
                entry.lookup = result->lookup;
                entry.lookupSeen = true;
                entry.dirty = result->lookup.slcState.dirty();
            } else if (entry.slcAction->kind == PocqActionKind::WriteL3 ||
                       entry.slcAction->kind == PocqActionKind::WriteL3FlushSf) {
                // Fill may displace a dirty SLC victim. Preserve its old
                // address/state without overwriting the incoming RN-F line.
                entry.lookup = result->lookup;
                entry.lookupSeen = true;
            }
            break;
          case SlcSfResultKind::DataBeat: {
            panic_if(result->beat > 1, "Invalid SLCSF data beat");
            auto &line = result->victim ? entry.victimData : entry.line;
            auto &mask = result->victim ? entry.victimBeats : entry.lineBeats;
            std::copy(result->data.begin(), result->data.end(),
                      line.begin() + result->beat * 32);
            mask |= 1 << result->beat;
            break;
          }
          case SlcSfResultKind::CommitDone:
            entry.slcCommitted = true;
            break;
        }
    }
}

void
HomePocq::acceptSeq()
{
    for (const auto &entry : m_pocq.queue) {
        if (entry.valid && entry.internalSeq)
            return;
    }
    auto request = m_homenode->slcSf().pendingSeq();
    DPRINTF(HomePocq, "SEQ admission request=%d active=%u valid=%u waiting=%d\n",
            request.has_value(), unsigned(m_pocq.size()),
            unsigned(m_homenode->slcSf().model().seqOccupancy()),
            m_homenode->slcSf().model().needsPocqEntry());
    if (!request || !hasFreeEntry())
        return;
    RawReq req{};
    req.srcid = 0xfffffffe;
    req.txnid = allocateWireId();
    req.opcode = 0x09;
    req.addr = request->addr;
    req.ns = request->ns;
    if (!allocateImpl(req, req.qosPriority(), false, true))
        return;
    Entry &entry = m_pocq.queue.back();
    entry.internalSeq = true;
    entry.seqId = request->id;
    entry.lookupSeen = true;
    entry.lookupDelivered = true;
    entry.slcCommitted = true;
    entry.lookup.sfHit = true;
    entry.lookup.sfState = request->state;
    entry.lookup.snoopTargets = request->targets;
    entry.lookup.broadcast = request->broadcast;
    graphPanel.initialize(entry, PocqGraphId::SnpCleanInvalid);
    m_homenode->slcSf().acceptSeq(request->id, entry.is_sleep);
    entry.seqReleased = !entry.is_sleep;
}

void
HomePocq::queueWriteback(Entry &entry, uint64_t addr, bool ns,
                         const SlcLineData &data)
{
    Writeback write;
    write.addr = addr & ~uint64_t(63);
    write.ns = ns;
    write.data = data;
    write.wireId = allocateWireId();
    victimBuffers.allocate(write.wireId, PocqVictimBuffer::ownerKey(
        entry.rawflit.req.srcid, entry.rawflit.req.txnid), addr, ns,
        uint64_t(m_homenode->curCycle()));
    entry.writebacks.push_back(std::move(write));
}

void
HomePocq::progressWritebacks(Entry &entry)
{
    if (entry.writebacks.empty())
        return;
    auto &write = entry.writebacks.front();
    if (!write.sent) {
        RawReq req{};
        req.srcid = m_homenode->nodeId();
        req.tgtid = m_homenode->snNodeId();
        req.txnid = write.wireId;
        req.opcode = 0x1d; // WriteNoSnpFull
        req.addr = write.addr;
        req.ns = write.ns;
        req.AllowRetry = 0;
        req.expCompAck = false;
        write.sent = m_homenode->transmit(REQ, req);
    } else if (write.haveDbid && write.beats < 2) {
        RawDat dat{};
        dat.srcid = m_homenode->nodeId();
        dat.tgtid = m_homenode->snNodeId();
        dat.txnid = write.dbid;
        dat.dbid = write.dbid;
        dat.opcode = 0x03; // NonCopyBackWriteData
        dat.dataid = write.beats * 2;
        dat.last = write.beats == 1;
        dat.setPayload(write.data.data() + write.beats * 32, 32);
        if (m_homenode->transmit(DAT, dat))
            ++write.beats;
    }
    if (write.complete && write.beats == 2) {
        victimBuffers.release(write.wireId, uint64_t(m_homenode->curCycle()));
        entry.writebacks.pop_front();
    }
}

void
HomePocq::progressEntry(Entry &entry)
{
    if (entry.is_sleep)
        return;
    if (entry.internalSeq && !entry.seqReleased) {
        // An older same-line request may expand a still-valid SEQ entry
        // while this internal transaction sleeps. Refresh before releasing
        // the slot so the final eviction reaches the current owner(s).
        const auto current = m_homenode->slcSf().seqSnapshot(entry.seqId);
        panic_if(!current, "Sleeping POCQ entry lost its SEQ snapshot");
        entry.lookup.sfState = current->state;
        entry.lookup.snoopTargets = current->targets;
        entry.lookup.broadcast = current->broadcast;
        m_homenode->slcSf().releaseSeq(entry.seqId);
        entry.seqReleased = true;
    }
    if (entry.reqOpcode.minor == ReqMinor::WriteBack &&
        entry.isAt(PocqNodeRole::WaitWriteData) && entry.lineBeats == 3) {
        PocqEvent event{PocqEventKind::WriteData, PocqEventSource::LinkLayer};
        event.dataValid = entry.writeDataValid;
        dispatch(entry, event);
    }
    if (entry.slcAction && entry.slcCommitted) {
        const bool lookup = entry.slcAction->kind ==
                            PocqActionKind::StartSlcLookup;
        const bool fill = entry.slcAction->kind == PocqActionKind::WriteL3 ||
                          entry.slcAction->kind == PocqActionKind::WriteL3FlushSf;
        if ((lookup || fill) && (!entry.lookupSeen ||
            (lookup && entry.lookup.dataExpected && entry.lineBeats != 3) ||
            (entry.lookup.victim && entry.lookup.victimState.dirty() &&
             entry.victimBeats != 3)))
            return;
        entry.slcAction.reset();
        if (lookup || fill) {
            if (entry.lookup.victim && entry.lookup.victimState.dirty() &&
                !entry.victimQueued) {
                queueWriteback(entry, entry.lookup.victimAddr,
                               entry.lookup.victimNs, entry.victimData);
                entry.victimQueued = true;
            }
        }
        if (fill && entry.reqOpcode.minor == ReqMinor::WriteBack) {
            dispatch(entry, {PocqEventKind::SlcSfDone, PocqEventSource::SlcSf});
        } else if (lookup) {
            PocqEvent event{PocqEventKind::SlcLookupDone,
                            PocqEventSource::SlcSf};
            event.slcHit = entry.lookup.slcHit;
            // SnpMe can require a CMO snoop even when both arrays miss.
            // The graph's SF branch represents required snoop work; keep
            // the physical hit bits unmodified in the saved lookup result.
            event.sfHit = entry.lookup.sfHit || entry.lookup.seqHit ||
                std::any_of(entry.lookup.snoopTargets.begin(),
                            entry.lookup.snoopTargets.end(),
                            [](bool target) { return target; });
            event.orderNonZero = entry.rawflit.req.order != 0;
            entry.lookupDelivered = true;
            dispatch(entry, event);
        }
    }
    if (entry.snoopStarted && !entry.snoopDelivered) {
        bool done = true;
        for (const auto &[id, reply] : entry.snoops)
            done &= reply.sent && reply.done;
        if (done) {
            // A dirty upper-level copy takes precedence over the SLC copy.
            for (const auto &[id, reply] : entry.snoops) {
                if (reply.beats == 3 && (!entry.dirty || reply.dirty)) {
                    entry.line = reply.data;
                    entry.lineBeats = 3;
                    entry.dirty |= reply.dirty;
                }
            }
            entry.snoopDelivered = true;
            PocqEvent event{PocqEventKind::SnpResp,
                            PocqEventSource::LinkLayer};
            event.slcHit = entry.lineBeats == 3;
            event.dirty = entry.dirty;
            event.orderNonZero = entry.rawflit.req.order != 0;
            dispatch(entry, event);
        }
    }
    if (entry.memoryDone && !entry.memoryEventSent &&
        entry.isAt(PocqNodeRole::WaitCompData)) {
        entry.memoryEventSent = true;
        dispatch(entry, {PocqEventKind::CompData,
                         PocqEventSource::LinkLayer});
    }
    // Maintenance returns Comp only after dirty data reaches memory. Wait
    // for all snoop responses so stale SLC data cannot win over a dirty RN-F.
    if (entry.reqOpcode.minor == ReqMinor::CleanInvalid &&
        entry.lookupDelivered &&
        (!entry.snoopStarted || entry.snoopDelivered) &&
        entry.dirty && !entry.dirtyQueued && entry.lineBeats == 3 &&
        entry.completed) {
        queueWriteback(entry, entry.ReqAddr(), entry.rawflit.req.ns,
                       entry.line);
        entry.dirtyQueued = true;
    }
}

bool
HomePocq::sendSnoops(Entry &entry, uint8_t opcode)
{
    if (!entry.snoopStarted) {
        entry.snoopStarted = true;
        for (std::size_t rnf = 0; rnf < entry.lookup.snoopTargets.size();
             ++rnf) {
            if (entry.lookup.snoopTargets[rnf])
                entry.snoops.emplace(m_homenode->rnfNodeId(rnf), SnoopReply{});
        }
    }
    for (auto &[rnf, reply] : entry.snoops) {
        if (reply.sent)
            continue;
        RawSnp snp{};
        snp.srcid = m_homenode->nodeId();
        snp.tgtid = rnf;
        snp.txnid = entry.wireId;
        snp.opcode = opcode;
        snp.addr = entry.ReqAddr() & ~uint64_t(63);
        snp.ns = entry.rawflit.req.ns;
        if (!m_homenode->transmit(SNP, snp))
            return false;
        reply.sent = true;
    }
    return true;
}

bool
HomePocq::operationsComplete(const Entry &entry) const
{
    if (entry.slcAction || !entry.writebacks.empty())
        return false;
    for (const auto &action : entry.pendingActions) {
        if (action.target == PocqActionTarget::SlcSf)
            return false;
    }
    return true;
}

bool
HomePocq::sendCompletion(Entry &entry, bool data)
{
    if (!operationsComplete(entry) ||
        (entry.snoopStarted && !entry.snoopDelivered))
        return false;
    if (data) {
        if (entry.lineBeats != 3)
            return false;
        RawDat dat{};
        dat.srcid = m_homenode->nodeId();
        dat.tgtid = entry.rawflit.req.srcid;
        dat.txnid = entry.rawflit.req.txnid;
        dat.dbid = entry.wireId;
        dat.HomeNID = m_homenode->nodeId();
        dat.opcode = 0x04;
        dat.resp = entry.dirty ? 6 : 2; // UD / UC
        dat.passDirty = entry.dirty;
        dat.dataid = entry.txDataBeat * 2;
        dat.last = entry.txDataBeat == 1;
        dat.setPayload(entry.line.data() + entry.txDataBeat * 32, 32);
        if (!m_homenode->transmit(DAT, dat))
            return false;
        if (++entry.txDataBeat != 2)
            return false;
    } else {
        RawRsp rsp{};
        rsp.srcid = m_homenode->nodeId();
        rsp.tgtid = entry.rawflit.req.srcid;
        rsp.txnid = entry.rawflit.req.txnid;
        rsp.opcode = 0x04;
        if (!m_homenode->transmit(RSP, rsp))
            return false;
    }
    entry.completionSent = true;
    if (data && !entry.rawflit.req.expCompAck) {
        dispatch(entry, {PocqEventKind::CompAck,
                         PocqEventSource::Internal});
    }
    return true;
}

bool
HomePocq::issueAction(Entry &entry, const PocqAction &action)
{
    bool accepted = false;
    if (action.target == PocqActionTarget::SlcSf) {
        if (entry.slcAction)
            return false;
        SlcSfRequest request{};
        request.token = nextToken;
        request.srcid = entry.rawflit.req.srcid;
        request.txnid = entry.rawflit.req.txnid;
        request.opcode = entry.rawflit.req.opcode;
        request.addr = entry.ReqAddr();
        request.ns = entry.rawflit.req.ns;
        request.rnf = m_homenode->rnfIndex(entry.rawflit.req.srcid);
        request.allocate = entry.rawflit.req.allocate;
        request.cacheable = entry.rawflit.req.cacheable;
        request.likelyShared = entry.rawflit.req.likelyShared;
        request.snoopMe = entry.rawflit.req.snoopMe;
        request.data = entry.line;
        request.state = {entry.dirty ? SlcMesi::Modified :
                                      SlcMesi::Exclusive, true};
        switch (action.kind) {
          case PocqActionKind::StartSlcLookup:
            request.op = SlcSfOp::Lookup;
            break;
          case PocqActionKind::FlushSf: request.op = SlcSfOp::FlushSf; break;
          case PocqActionKind::FlushL3: request.op = SlcSfOp::FlushSlc; break;
          case PocqActionKind::WriteL3:
          case PocqActionKind::WriteL3FlushSf:
            request.op = SlcSfOp::Fill;
            break;
          case PocqActionKind::UpdateSf: request.op = SlcSfOp::UpdateSf; break;
          default: panic("Invalid SLCSF action");
        }
        accepted = m_homenode->slcSf().tryAccept(request);
        if (accepted) {
            entry.slcToken = nextToken++;
            entry.slcAction = action;
            entry.slcCommitted = false;
            if (action.kind == PocqActionKind::StartSlcLookup) {
                entry.lookupSeen = false;
                entry.lineBeats = 0;
                entry.victimBeats = 0;
            } else if (action.kind == PocqActionKind::WriteL3 ||
                       action.kind == PocqActionKind::WriteL3FlushSf) {
                entry.lookupSeen = false;
                entry.victimBeats = 0;
            }
        }
    } else {
        switch (action.kind) {
          case PocqActionKind::SendReadNoSnp: {
            RawReq req{};
            req.srcid = m_homenode->nodeId();
            req.tgtid = m_homenode->snNodeId();
            req.txnid = entry.wireId;
            req.opcode = 0x04;
            req.addr = entry.ReqAddr() & ~uint64_t(63);
            req.ns = entry.rawflit.req.ns;
            req.order = entry.rawflit.req.order;
            req.AllowRetry = 0;
            accepted = m_homenode->transmit(REQ, req);
            entry.memorySent |= accepted;
            break;
          }
          case PocqActionKind::SendCompAck: {
            RawRsp rsp{};
            rsp.srcid = m_homenode->nodeId();
            rsp.tgtid = m_homenode->snNodeId();
            rsp.txnid = entry.memoryDbid;
            rsp.opcode = 0x02;
            accepted = m_homenode->transmit(RSP, rsp);
            break;
          }
          case PocqActionKind::SendCompData:
            accepted = sendCompletion(entry, true);
            break;
          case PocqActionKind::SendComp:
            accepted = sendCompletion(entry, false);
            break;
          case PocqActionKind::SendDbidResp: {
            RawRsp rsp{};
            rsp.srcid = m_homenode->nodeId();
            rsp.tgtid = entry.rawflit.req.srcid;
            rsp.txnid = entry.rawflit.req.txnid;
            rsp.dbid = entry.wireId;
            rsp.opcode = 0x06;
            accepted = m_homenode->transmit(RSP, rsp);
            entry.writeDbidSent |= accepted;
            break;
          }
          case PocqActionKind::SendSnpUnique:
            accepted = sendSnoops(entry, 0x07);
            break;
          case PocqActionKind::SendSnpCleanInvalid:
            accepted = sendSnoops(entry, 0x09);
            break;
          default: panic("Unimplemented POCQ action %s",
                         pocqActionName(action.kind));
        }
    }
    if (accepted) {
        // A bounded trace is diagnostic only; never retains payload ownership.
        if (issuedActions.size() == 256)
            issuedActions.pop_front();
        issuedActions.push_back({entry.rawflit.req.srcid,
                                 entry.rawflit.req.txnid, action});
    }
    return accepted;
}

void
HomePocq::startReadyEntries()
{
    for (auto &entry : m_pocq.queue) {
        if (entry.valid && !entry.is_sleep && !entry.completed &&
            entry.isAt(PocqNodeRole::Entry) &&
            entry.pendingActions.empty()) {
            dispatch(entry, PocqEvent::enter());
        }
    }
}

void
HomePocq::arbitrateActions()
{
    auto winners = m_pocq.Arb_Oldest();
    for (int resource = POCQueue::SlcLookup;
         resource < POCQueue::Num_ArbWin; ++resource) {
        auto *entry = winners[resource];
        if (!entry)
            continue;
        auto action = m_pocq.takeAction(
            *entry, static_cast<POCQueue::Arb_win>(resource));
        if (action && !issueAction(*entry, *action))
            entry->pendingActions.push_front(*action);
    }
}

bool
HomePocq::retireCompletedEntries()
{
    std::vector<Entry *> completed;
    for (auto &entry : m_pocq.queue) {
        if (entry.valid && entry.completed && entry.pendingActions.empty() &&
            operationsComplete(entry) &&
            (entry.internalSeq || entry.completionSent))
            completed.push_back(&entry);
    }
    bool woke = false;
    for (auto *entry : completed) {
        DPRINTF(HomePocq, "retire src=%u txn=%u internal=%d active=%u\n",
                entry->rawflit.req.srcid, entry->rawflit.req.txnid,
                entry->internalSeq, unsigned(m_pocq.size()));
        if (entry->qosPoolReserved)
            m_homenode->releasePocqQos(entry->qosPriority);
        if (entry->internalSeq)
            m_homenode->slcSf().completeSeq(entry->seqId);
        woke |= m_pocq.retire(*entry);
    }
    return woke;
}

bool
HomePocq::hasPendingPocqWork() const
{
    if (m_pocq.hasPendingActions() || !incoming.empty())
        return true;
    for (const auto &entry : m_pocq.queue) {
        if (entry.valid && !entry.is_sleep &&
            (!entry.writebacks.empty() || entry.slcAction ||
             entry.isAt(PocqNodeRole::Entry) ||
             (entry.snoopStarted && !entry.snoopDelivered)))
            return true;
    }
    return false;
}

void
HomePocq::wakeup()
{
    consumeLink();
    collectSlcResults();
    acceptSeq();
    for (auto &entry : m_pocq.queue) {
        if (!entry.valid)
            continue;
        progressEntry(entry);
        progressWritebacks(entry);
    }
    startReadyEntries();
    arbitrateActions();
    const bool woke = retireCompletedEntries();
    // acceptSeq() precedes retirement. A newly freed slot must trigger a
    // retry even if every remaining foreground entry is waiting for an ACK.
    if (hasPendingPocqWork() || woke ||
        (hasFreeEntry() && m_homenode->slcSf().model().needsPocqEntry()))
        scheduleEvent(Cycles(1));
}

} // namespace gem5::Chi

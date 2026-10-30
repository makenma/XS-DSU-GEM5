#include "mem/cache/CHI/ChiHnfTester.hh"

#include <algorithm>
#include <iostream>

#include "base/logging.hh"
#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/SlcSf.hh"
#include "sim/sim_exit.hh"

namespace gem5::Chi
{
ChiHnfTester::ChiHnfTester(const Params &p)
    : ClockedObject(p), Consumer(this), hnf(p.hnf),
      port(p.name + ".chi", this),
      tickEvent([this] { tick(); }, p.name + ".tick"),
      checkEvent([this] { checkAfterEdge(); }, p.name + ".check", false,
                 Event::Default_Pri + 1),
      scenario(p.scenario), maxCycles(p.max_cycles),
      responseDelay(p.response_delay), throughputRequests(p.throughput_requests),
      earlySchedule(p.early_schedule)
{}

Port &
ChiHnfTester::getPort(const std::string &name, PortID idx)
{
    if (name == "chi")
        return port;
    return ClockedObject::getPort(name, idx);
}

void
ChiHnfTester::print(std::ostream &out) const
{
    out << name() << " scenario=" << scenario << " cycle=" << cycle;
}

void
ChiHnfTester::wakeup()
{
    // Port notifications may arrive during another component's clock edge.
    // Always defer work; never advance either endpoint twice in one cycle.
    if (!done && !tickEvent.scheduled())
        schedule(tickEvent, nextCycle());
}

void
ChiHnfTester::startup()
{
    setup();
    wakeup();
}

SlcLineData
ChiHnfTester::pattern(uint64_t addr, bool dirty)
{
    SlcLineData data{};
    for (unsigned i = 0; i < data.size(); ++i)
        data[i] = static_cast<uint8_t>((addr >> 6) + 3 * i) ^
                  (dirty ? 0xa5 : 0);
    return data;
}

void
ChiHnfTester::send(ChannelType ch, FlitVariant flit, unsigned delay)
{
    Pending value{cycle + delay, ch, std::move(flit)};
    auto position = std::upper_bound(pending.begin(), pending.end(), value.due,
        [](uint64_t due, const Pending &p) { return due < p.due; });
    pending.insert(position, std::move(value));
}

void
ChiHnfTester::addRequest(uint32_t src, uint32_t txn, uint8_t opcode,
                        uint64_t addr, bool ns, const SlcLineData &expected)
{
    RawReq req{};
    req.srcid = src;
    req.tgtid = hnf->nodeId();
    req.txnid = txn;
    req.opcode = opcode;
    req.addr = addr;
    req.ns = ns;
    req.size = 6;
    req.AllowRetry = 0;
    req.expCompAck = opcode == 0x07;
    req.order = scenario == "ordered_read" || scenario == "sf_stale_ordered";
    req.snoopMe = scenario == "clean_invalid_self" ||
                  scenario == "clean_invalid_self_miss";
    req.allocate = true;
    req.cacheable = true;
    requests.emplace(std::make_pair(src, txn), Read{req, expected});
    send(REQ, req);
}

void
ChiHnfTester::recordFlit(ChannelType ch, const FlitVariant &flit, bool outgoing)
{
    auto add = [&](uint64_t value) {
        for (unsigned i = 0; i < 8; ++i) {
            traceDigest = (traceDigest ^ (value & 255)) * 1099511628211ULL;
            value >>= 8;
        }
    };
    add(cycle); add(ch); add(outgoing);
    std::visit([&](const auto &raw) {
        using T = std::decay_t<decltype(raw)>;
        add(raw.srcid); add(raw.tgtid); add(raw.txnid); add(raw.opcode);
        if constexpr (std::is_same_v<T, RawReq> || std::is_same_v<T, RawSnp>) {
            add(raw.addr); add(raw.ns);
        }
        if constexpr (std::is_same_v<T, RawRsp> || std::is_same_v<T, RawDat>) {
            add(raw.dbid); add(raw.resp);
        }
        if constexpr (std::is_same_v<T, RawDat>) {
            add(raw.dataid); add(raw.last); add(raw.passDirty);
            for (std::size_t i = 0; i < raw.payloadSize(); ++i)
                add(raw.payloadData()[i]);
        }
    }, flit);
}

void
ChiHnfTester::addThroughputRequest()
{
    const uint32_t txn = ++throughputIssued;
    const uint64_t addr = uint64_t(txn) * 64;
    addRequest(txn & 3, txn, 0x07, addr, true, pattern(addr));
}

void
ChiHnfTester::setup()
{
    if (scenario == "throughput" || scenario == "seq_pressure") {
        fatal_if(!throughputRequests, "throughput_requests must be positive");
        const auto capacity = hnf->slcSf().sfTagRam().sets() *
                              hnf->slcSf().sfTagRam().ways();
        expectedSnoops = throughputRequests > capacity ?
                        throughputRequests - capacity : 0;
        for (unsigned i = 0; i < std::min(32U, throughputRequests); ++i)
            addThroughputRequest();
        return;
    }
    constexpr uint64_t addr = 0x1000;
    auto &slc = hnf->slcSf();
    const auto clean = pattern(addr);
    const auto dirty = pattern(addr, true);
    memory[key(addr, true)] = clean;
    memory[key(addr, false)] = clean;

    auto preloadSlc = [&](SlcMesi mesi, bool unique, bool ns,
                          const SlcLineData &data) {
        const auto line = addr / 64;
        const auto set = line % slc.l3TagRam().sets();
        auto &tag = slc.l3TagRam().at(set, 0);
        tag.paTag = line / slc.l3TagRam().sets();
        tag.ns = ns;
        tag.mesi = mesi;
        tag.unique = unique;
        tag.rnfId = 1;
        auto &bytes = slc.l3DataRam().at(set, 0).bytes;
        std::copy(data.begin(), data.end(), bytes.begin());
    };
    auto preloadSf = [&](bool multiple, bool isDirty) {
        const auto line = addr / 64;
        auto &tag = slc.sfTagRam().at(line % slc.sfTagRam().sets(), 0);
        tag.paTag = line / slc.sfTagRam().sets();
        tag.ns = true;
        tag.mesi = SlcMesi::Exclusive;
        tag.unique = !multiple;
        tag.rnfId = 1;
        tag.sfRnfVec.assign(slc.sfTagRam().rnfNum(), false);
        tag.sfRnfVec.at(1) = true;
        if (multiple)
            tag.sfRnfVec.at(2) = true;
        for (unsigned rnf : {1U, 2U}) {
            if (rnf == 2 && !multiple)
                continue;
            owners[{rnf, key(addr, true)}] = isDirty ? dirty : clean;
            ownerDirty[{rnf, key(addr, true)}] = isDirty && rnf == 1;
        }
    };

    uint8_t opcode = 0x07;
    SlcLineData expected = clean;
    if (scenario == "cold_read" || scenario == "ordered_read") {
        // Empty arrays must fetch the full line from SN.
    } else if (scenario == "sf_stale_ordered") {
        preloadSf(false, false);
        owners.clear();
        ownerDirty.clear();
        expectedSnoops = 1;
    } else if (scenario == "slc_clean" || scenario == "slc_dirty") {
        const bool isDirty = scenario == "slc_dirty";
        expected = isDirty ? dirty : clean;
        preloadSlc(isDirty ? SlcMesi::Modified : SlcMesi::Exclusive,
                   true, true, expected);
        expectNoMemoryRead = true;
    } else if (scenario == "sf_clean" || scenario == "sf_dirty" ||
               scenario == "both_read") {
        const bool isDirty = scenario != "sf_clean";
        const bool both = scenario == "both_read";
        preloadSf(both, isDirty);
        if (both)
            preloadSlc(SlcMesi::Exclusive, false, true, clean);
        expected = isDirty ? dirty : clean;
        expectedSnoops = both ? 2 : 1;
        expectNoMemoryRead = true;
    } else if (scenario == "clean_invalid_self" ||
               scenario == "clean_invalid_self_miss") {
        opcode = 0x09;
        if (scenario == "clean_invalid_self")
            slc.model().preloadSf(addr, true, {SlcMesi::Exclusive, true},
                                 0, {true, false, false, false});
        owners[{0, key(addr, true)}] = dirty;
        ownerDirty[{0, key(addr, true)}] = true;
        expected = dirty;
        expectedSnoops = expectedWrites = 1;
        expectNoMemoryRead = true;
    } else if (scenario == "clean_invalid_miss") {
        opcode = 0x09;
        expectNoMemoryRead = true;
    } else if (scenario == "clean_invalid_clean" ||
               scenario == "clean_invalid_dirty" ||
               scenario == "clean_invalid_sf_dirty" ||
               scenario == "clean_invalid_both") {
        opcode = 0x09;
        const bool sf = scenario == "clean_invalid_sf_dirty" ||
                        scenario == "clean_invalid_both";
        const bool isDirty = scenario != "clean_invalid_clean";
        if (scenario != "clean_invalid_sf_dirty")
            preloadSlc(sf ? SlcMesi::Exclusive :
                       (isDirty ? SlcMesi::Modified : SlcMesi::Exclusive),
                       !sf, true, sf ? clean : (isDirty ? dirty : clean));
        if (sf) {
            preloadSf(true, true);
            expectedSnoops = 2;
        }
        expectedWrites = isDirty ? 1 : 0;
        expected = isDirty ? dirty : clean;
    } else if (scenario == "seq_hazard" ||
               scenario == "seq_broadcast_hazard") {
        // Seed 0 selects way 8 for the first replacement. A hits way 8,
        // then B replaces A while A's CompAck is deliberately delayed.
        fatal_if(slc.sfTagRam().ways() != 16 ||
                 slc.model().config().replacementSeed != 0,
                 "SEQ hazard regression requires the CMN seed/ways");
        const auto set = (addr / 64) % slc.sfTagRam().sets();
        for (unsigned way = 0; way < slc.sfTagRam().ways(); ++way) {
            const uint64_t old = way == 8 ? addr :
                addr + (way + 1) * slc.sfTagRam().sets() * 64;
            auto &tag = slc.sfTagRam().at(set, way);
            tag.paTag = old / 64 / slc.sfTagRam().sets();
            tag.ns = true;
            tag.mesi = SlcMesi::Exclusive;
            tag.unique = true;
            tag.rnfId = 1;
            tag.sfRnfVec.assign(slc.sfTagRam().rnfNum(), false);
            tag.sfRnfVec.at(1) = true;
        }
        owners[{1, key(addr, true)}] = dirty;
        ownerDirty[{1, key(addr, true)}] = true;
        expected = dirty;
        expectedWrites = 1;
        expectedSnoops = scenario == "seq_broadcast_hazard" ? 8 : 2;
    } else if (scenario == "sf_eviction") {
        const auto set = (addr / 64) % slc.sfTagRam().sets();
        for (unsigned way = 0; way < slc.sfTagRam().ways(); ++way) {
            uint64_t old = addr + (way + 1) * slc.sfTagRam().sets() * 64;
            auto &tag = slc.sfTagRam().at(set, way);
            tag.paTag = old / 64 / slc.sfTagRam().sets();
            tag.ns = true;
            tag.mesi = SlcMesi::Exclusive;
            tag.unique = true;
            tag.rnfId = 1;
            tag.sfRnfVec.assign(slc.sfTagRam().rnfNum(), false);
            tag.sfRnfVec.at(1) = true;
            memory[key(old, true)] = pattern(old);
            owners[{1, key(old, true)}] = pattern(old, true);
            ownerDirty[{1, key(old, true)}] = true;
        }
        expectedSnoops = 1;
        expectedWrites = 1;
    } else if (scenario == "ns_isolation") {
        preloadSlc(SlcMesi::Modified, true, false, dirty);
    } else if (scenario == "same_line" || scenario == "offset_hazard") {
        addRequest(0, 1, opcode, addr, true, clean);
        addRequest(2, 1, opcode,
                   addr + (scenario == "offset_hazard" ? 8 : 0), true, clean);
        expectedSnoops = 1;
        return;
    } else if (scenario == "tx_backpressure") {
        stallUntil = 160;
        for (unsigned i = 0; i < 24; ++i) {
            uint64_t a = addr + i * 64;
            memory[key(a, true)] = pattern(a);
            addRequest(i % 4, i + 1, opcode, a, true, pattern(a));
        }
        return;
    } else {
        fatal("Unknown CHI HNF regression scenario: %s", scenario);
    }
    addRequest(0, 1, opcode, addr, true, expected);
}

void
ChiHnfTester::sendData(uint32_t src, uint32_t tgt, uint32_t txn,
                      uint32_t dbid, uint8_t opcode,
                      const SlcLineData &data, bool dirty)
{
    for (unsigned beat = 0; beat < 2; ++beat) {
        RawDat dat{};
        dat.srcid = src;
        dat.tgtid = tgt;
        dat.txnid = txn;
        dat.dbid = dbid;
        dat.opcode = opcode;
        dat.dataid = beat * 2;
        dat.last = beat == 1;
        dat.passDirty = dirty;
        dat.resp = dirty ? 2 : 0;
        dat.setPayload(data.data() + beat * 32, 32);
        send(DAT, dat, responseDelay + beat);
    }
}

void
ChiHnfTester::receive(const RawReq &req)
{
    fatal_if(req.tgtid != hnf->snNodeId(), "Unexpected SN destination");
    const auto k = key(req.addr, req.ns);
    if (req.opcode == 0x04) {
        ++readsFromMemory;
        if (!memory.count(k))
            memory[k] = pattern(req.addr);
        if (req.order) {
            RawRsp receipt{};
            receipt.srcid = req.tgtid;
            receipt.tgtid = req.srcid;
            receipt.txnid = req.txnid;
            receipt.opcode = 0x08;
            send(RSP, receipt, scenario == "sf_stale_ordered" ?
                 responseDelay + 8 : responseDelay - (responseDelay != 0));
        }
        sendData(req.tgtid, req.srcid, req.txnid, req.txnid,
                 0x04, memory.at(k), false);
    } else {
        const uint32_t dbid = nextDbid++;
        writes.emplace(dbid, Write{req});
        RawRsp rsp{};
        rsp.srcid = req.tgtid;
        rsp.tgtid = req.srcid;
        rsp.txnid = req.txnid;
        rsp.dbid = dbid;
        rsp.opcode = 0x06;
        send(RSP, rsp, responseDelay);
    }
}

void
ChiHnfTester::receive(const RawSnp &snp)
{
    ++snoops;
    if (scenario == "seq_hazard" && snp.tgtid == 0) {
        fatal_if(!hazardAckDelivered || !hazardHeld,
                 "SEQ snoop ran before its POCQ address hazard cleared");
        seqReleaseCheckPending = true;
    }
    if (scenario == "seq_broadcast_hazard" && snp.opcode == 0x09) {
        fatal_if(!broadcastAckDelivered || !hazardHeld,
                 "SEQ eviction ran before the newer owner acknowledged its read");
        seqReleaseCheckPending = true;
        seqBroadcastTargets |= 1U << snp.tgtid;
    }
    const auto k = key(snp.addr, snp.ns);
    const auto owner = std::make_pair(snp.tgtid, k);
    const bool hasData = owners.count(owner);
    const bool dirty = hasData && ownerDirty[owner];
    // CleanInvalid only needs data when dirty. SnpUnique may obtain clean data.
    if (hasData && (dirty || snp.opcode == 0x07)) {
        sendData(snp.tgtid, snp.srcid, snp.txnid, 0,
                 0x01, owners.at(owner), dirty);
    } else {
        RawRsp rsp{};
        rsp.srcid = snp.tgtid;
        rsp.tgtid = snp.srcid;
        rsp.txnid = snp.txnid;
        rsp.opcode = 0x01;
        rsp.resp = 0;
        send(RSP, rsp, responseDelay + (snp.tgtid == 2 ? 4 : 0));
    }
    owners.erase(owner);
    ownerDirty.erase(owner);
}

void
ChiHnfTester::receive(const RawRsp &rsp)
{
    if (rsp.opcode == 0x02 && rsp.tgtid == hnf->snNodeId())
        return; // CompAck for a memory read.
    fatal_if(rsp.opcode != 0x04, "Unexpected HNF response opcode %#x", rsp.opcode);
    auto it = requests.find({rsp.tgtid, rsp.txnid});
    fatal_if(it == requests.end(), "Unmatched HNF completion");
    Read &request = it->second;
    fatal_if(request.req.opcode != 0x09 || request.complete,
             "Unexpected or duplicate maintenance completion");
    fatal_if(writesToMemory != expectedWrites || !writes.empty() ||
             memory.at(key(request.req.addr, request.req.ns)) != request.expected,
             "CleanInvalid completed before dirty data reached memory");
    for (const auto &item : pending) {
        const auto *ack = std::get_if<RawRsp>(&item.flit);
        fatal_if(ack && ack->srcid == hnf->snNodeId() && ack->opcode == 0x04,
                 "CleanInvalid completed before SN acknowledged the write");
    }
    fatal_if(hnf->slcSf().model().findSlc(request.req.addr, request.req.ns) ||
             hnf->slcSf().model().findSf(request.req.addr, request.req.ns),
             "CleanInvalid response preceded tag commit");
    request.complete = true;
    ++completed;
}

void
ChiHnfTester::receive(const RawDat &dat)
{
    fatal_if(dat.payloadSize() != 32 || (dat.dataid != 0 && dat.dataid != 2),
             "HNF data must contain two numbered 32-byte beats");
    const unsigned beat = dat.dataid / 2;
    if (dat.tgtid == hnf->snNodeId()) {
        auto it = writes.find(dat.txnid);
        fatal_if(it == writes.end(), "Write data does not match allocated SN DBID");
        Write &write = it->second;
        fatal_if(write.mask & (1U << beat), "Duplicate SN write beat");
        std::copy_n(dat.payloadData(), 32, write.data.begin() + beat * 32);
        write.mask |= 1U << beat;
        if (write.mask == 3) {
            if (scenario == "sf_eviction" || scenario == "seq_hazard" ||
                scenario == "seq_broadcast_hazard") {
                fatal_if(write.data != pattern(write.req.addr, true),
                         "SF eviction lost snooped dirty data");
            }
            memory[key(write.req.addr, write.req.ns)] = write.data;
            RawRsp comp{};
            comp.srcid = dat.tgtid;
            comp.tgtid = dat.srcid;
            comp.txnid = write.req.txnid;
            comp.opcode = 0x04;
            send(RSP, comp, responseDelay);
            writes.erase(it);
            ++writesToMemory;
        }
        return;
    }
    fatal_if(dat.opcode != 0x04, "Requester did not receive CompData");
    auto it = requests.find({dat.tgtid, dat.txnid});
    fatal_if(it == requests.end(), "Unmatched CompData");
    Read &read = it->second;
    fatal_if(read.complete || (read.mask & (1U << beat)), "Duplicate CompData beat");
    std::copy_n(dat.payloadData(), 32, read.received.begin() + beat * 32);
    read.mask |= 1U << beat;
    if (read.mask == 3) {
        fatal_if(read.received != read.expected, "ReadUnique returned incorrect data");
        fatal_if(hnf->pocqActiveEntries() == 0,
                 "ReadUnique retired before the requester CompAck");
        fatal_if(hnf->slcSf().model().findSlc(read.req.addr, read.req.ns),
                 "ReadUnique sent CompData before SLC invalidation committed");
        read.complete = true;
        ++completed;
        const auto owner = std::make_pair(dat.tgtid, key(read.req.addr, read.req.ns));
        owners[owner] = read.received;
        ownerDirty[owner] = dat.passDirty;
        RawRsp ack{};
        ack.srcid = dat.tgtid;
        ack.tgtid = dat.srcid;
        ack.txnid = dat.dbid;
        ack.opcode = 0x02;
        if ((scenario == "seq_hazard" ||
             scenario == "seq_broadcast_hazard") && dat.tgtid == 0) {
            hazardAckId = dat.dbid;
            const uint64_t b = read.req.addr +
                (hnf->slcSf().sfTagRam().ways() + 2) *
                hnf->slcSf().sfTagRam().sets() * 64;
            memory[key(b, true)] = pattern(b);
            if (scenario == "seq_broadcast_hazard") {
                // C must queue on A before B evicts A and creates the SEQ
                // transaction. C then runs ahead of that sleeping SEQ.
                addRequest(3, 3, 0x07, read.req.addr, true, read.expected);
            }
            addRequest(2, 2, 0x07, b, true, pattern(b));
            send(RSP, ack, 100);
        } else {
            if (scenario == "seq_broadcast_hazard" && dat.tgtid == 3)
                broadcastAckId = dat.dbid;
            send(RSP, ack, responseDelay + 5);
        }
        if ((scenario == "throughput" || scenario == "seq_pressure") &&
            throughputIssued < throughputRequests)
            addThroughputRequest();
    }
}

void
ChiHnfTester::checkFinished()
{
    if (completed != requests.size() || !pending.empty() || !writes.empty() ||
        hnf->pocqActiveEntries() != 0 || hnf->slcSf().hasWork()) {
        idleSince = 0;
        return;
    }
    if (!idleSince)
        idleSince = cycle;
    if (cycle - idleSince < 12)
        return;
    fatal_if(writesToMemory != expectedWrites, "Incorrect dirty writeback count");
    fatal_if(snoops < expectedSnoops, "Missing directory snoops");
    if (scenario == "throughput" || scenario == "seq_pressure") {
        fatal_if(snoops != expectedSnoops, "Repeated directory eviction snoops");
        fatal_if(readsFromMemory != throughputRequests,
                 "Streaming reads were lost or repeated");
    }
    fatal_if(expectNoMemoryRead && readsFromMemory, "Unnecessary SN read");
    for (const auto &[identity, read] : requests) {
        if (read.req.opcode == 0x09) {
            fatal_if(memory.at(key(read.req.addr, read.req.ns)) != read.expected,
                     "CleanInvalid lost dirty data");
            fatal_if(hnf->slcSf().model().findSlc(read.req.addr, read.req.ns) ||
                     hnf->slcSf().model().findSf(read.req.addr, read.req.ns),
                     "CleanInvalid completed before both tags were invalidated");
        } else {
            fatal_if(hnf->slcSf().model().findSlc(read.req.addr, read.req.ns),
                     "ReadUnique left an SLC copy behind");
        }
    }
    if (scenario == "seq_broadcast_hazard") {
        fatal_if(!broadcastAckDelivered || seqBroadcastTargets != 0xf,
                 "Sleeping SEQ did not refresh its expanded broadcast targets");
    }
    if (scenario == "seq_hazard" || scenario == "seq_broadcast_hazard") {
        fatal_if(!hazardHeld || !hazardReleased || !hazardAckDelivered,
                 "SEQ hazard lifecycle was not exercised");
        fatal_if(memory.at(key(0x1000, true)) != pattern(0x1000, true),
                 "SEQ hazard writeback lost the dirty owner data");
    }
    if (scenario == "ns_isolation") {
        const auto *tag = hnf->slcSf().model().findSlc(0x1000, false);
        fatal_if(!tag || tag->mesi != SlcMesi::Modified,
                 "Nonsecure lookup altered the secure cache line");
    }
    std::cout << "CHI_HNF_PASS scenario=" << scenario << " cycles=" << cycle
              << " completed=" << completed << " snoops=" << snoops
              << " memory_reads=" << readsFromMemory
              << " memory_writes=" << writesToMemory
              << " trace_digest=" << std::hex << traceDigest << std::dec << '\n';
    done = true;
    if (tickEvent.scheduled()) deschedule(tickEvent);
    exitSimLoop("CHI HNF integration passed", 0);
}

void
ChiHnfTester::tick()
{
    ++cycle;
    if (earlySchedule) schedule(tickEvent, nextCycle());
    fatal_if(cycle > maxCycles,
             "CHI HNF watchdog: %s completed=%u/%u active=%u seq_valid=%u seq_waiting=%u",
             scenario, completed, unsigned(requests.size()),
             unsigned(hnf->pocqActiveEntries()),
             unsigned(hnf->slcSf().model().seqOccupancy()),
             unsigned(hnf->slcSf().model().hasUnsentSeq()));
    std::array<bool, NUM_CHANNELS> sent{};
    for (auto it = pending.begin(); it != pending.end();) {
        if (it->due > cycle)
            break;
        if (!sent[it->channel] && port.peerHasRxCredit(it->channel) &&
            port.enqueueRx(it->channel, it->flit)) {
            recordFlit(it->channel, it->flit, true);
            sent[it->channel] = true;
            if ((scenario == "seq_hazard" ||
                 scenario == "seq_broadcast_hazard") && it->channel == RSP) {
                const auto &rsp = std::get<RawRsp>(it->flit);
                if (rsp.opcode == 0x02 && rsp.txnid == hazardAckId)
                    hazardAckDelivered = true;
                if (rsp.opcode == 0x02 && rsp.txnid == broadcastAckId)
                    broadcastAckDelivered = true;
            }
            it = pending.erase(it);
        } else {
            ++it;
        }
    }
    // First hold all TX channels, then allow memory/snoop traffic while
    // DAT remains blocked. This fills both the request and data credits.
    if (cycle >= stallUntil / 2) {
        for (unsigned ch = 0; ch < NUM_CHANNELS; ++ch) {
            if (ch == DAT && cycle < stallUntil)
                continue;
            if (!port.hasTxFlit(static_cast<ChannelType>(ch), true))
                continue;
            auto flit = port.getTxFlit(static_cast<ChannelType>(ch));
            if (flit) {
                recordFlit(static_cast<ChannelType>(ch), *flit, false);
                std::visit([this](const auto &raw) { receive(raw); }, *flit);
            }
        }
    }
    if (scenario == "tx_backpressure" && cycle == stallUntil - 1)
        fatal_if(hnf->pocqActiveEntries() != 16,
                 "Backpressure did not exercise all 16 POCQ entries");
    if (completed == requests.size() || scenario == "seq_hazard" ||
        scenario == "seq_broadcast_hazard")
        schedule(checkEvent, curTick());
    if (!done && !tickEvent.scheduled())
        schedule(tickEvent, nextCycle());
}

void
ChiHnfTester::checkAfterEdge()
{
    // Observe committed registers after all default-priority DUT events.
    // Moving endpoint injection earlier must not move this sampling point.
    if (seqReleaseCheckPending) {
        fatal_if(hnf->slcSf().model().seqOccupancy() != 0,
                 "SEQ valid slot was not released at the wakeup edge");
        seqReleaseCheckPending = false;
        hazardReleased = true;
    }
    // These fixtures do not backpressure SNP. The first eviction snoop is
    // observed one edge after the sleeping SEQ wakes, at its valid-release
    // edge checked above. Keep checking until that observation, including
    // the cycles while CompAck is still in the RX/LinkLayer/POCQ pipeline.
    if ((scenario == "seq_hazard" || scenario == "seq_broadcast_hazard") &&
        !hazardReleased) {
        const auto occupancy = hnf->slcSf().model().seqOccupancy();
        fatal_if(hazardHeld && occupancy != 1,
                 "SEQ slot released before its POCQ hazard wakeup edge");
        hazardHeld |= occupancy == 1;
    }
    checkFinished();
}
} // namespace gem5::Chi

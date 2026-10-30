#include "mem/cache/CHI/ChiSnfBridge.hh"

#include <algorithm>

#include "base/logging.hh"
#include "mem/packet.hh"
#include "sim/serialize.hh"
#include "sim/system.hh"

namespace gem5::Chi
{

ChiSnfBridge::ChiSnfBridge(const Params &p)
    : AbstractMemory(p), Consumer(this), chi(p.name + ".chi", this),
      functional(p.name + ".functional", *this),
      tickEvent([this] { tick(); }, p.name + ".tick"), nodeId(p.node_id),
      responseLatency(p.response_latency), maxTransactions(p.max_transactions)
{
    fatal_if(!maxTransactions || responseLatency < Cycles(1),
             "%s needs positive transaction capacity and response latency", name());
    fatal_if(range.interleaved() || (range.start() & 63) || (range.size() & 63),
             "%s requires a contiguous, 64-byte aligned memory range", name());
    fatal_if(p.null, "%s requires real physical-memory backing", name());
}

void
ChiSnfBridge::init()
{
    AbstractMemory::init();
    fatal_if(!chi.isConnected(), "%s CHI port is not connected", name());
    fatal_if(!system(), "%s must belong to a System memory map", name());
    requestorId = system()->getRequestorId(this);
    if (functional.isConnected()) functional.sendRangeChange();
}

Port &
ChiSnfBridge::getPort(const std::string &name, PortID idx)
{
    if (name == "chi") return chi;
    if (name == "functional") return functional;
    return AbstractMemory::getPort(name, idx);
}

void
ChiSnfBridge::print(std::ostream &out) const
{
    out << name() << " transactions=" << transactions.size();
}

void
ChiSnfBridge::wakeup()
{
    if (!tickEvent.scheduled()) schedule(tickEvent, clockEdge(Cycles(1)));
}

bool
ChiSnfBridge::idle() const
{
    if (!transactions.empty() || !chi.drained()) return false;
    for (unsigned c = 0; c < NUM_CHANNELS; ++c) {
        const auto ch = static_cast<ChannelType>(c);
        if (!pending[c].empty() || chi.hasRxFlit(ch) || chi.hasTxFlit(ch))
            return false;
    }
    return true;
}

DrainState
ChiSnfBridge::drain()
{
    if (idle()) return DrainState::Drained;
    wakeup();
    return DrainState::Draining;
}

void
ChiSnfBridge::serialize(CheckpointOut &cp) const
{
    fatal_if(!idle(), "%s checkpoint requires drained CHI transactions", name());
    AbstractMemory::serialize(cp);
    // System::physmem serializes the AbstractMemory backing store.
    SERIALIZE_SCALAR(nextDbid);
}

void
ChiSnfBridge::unserialize(CheckpointIn &cp)
{
    AbstractMemory::unserialize(cp);
    UNSERIALIZE_SCALAR(nextDbid);
}

uint32_t
ChiSnfBridge::allocateDbid()
{
    while (!nextDbid || transactions.count(nextDbid)) ++nextDbid;
    return nextDbid++;
}

void
ChiSnfBridge::accessLine(Transaction &transaction, bool write)
{
    const auto &req = transaction.req;
    auto request = std::make_shared<Request>(req.addr, 64,
        req.ns ? Request::Flags(0) : Request::Flags(Request::SECURE), requestorId);
    Packet packet(request, write ? MemCmd::WriteReq : MemCmd::ReadReq);
    packet.dataStatic(transaction.data.data());
    access(&packet);
}

void
ChiSnfBridge::queue(ChannelType ch, uint32_t dbid, FlitVariant flit, Cycles delay)
{
    Pending response{clockEdge(delay), dbid, std::move(flit)};
    auto &out = pending[ch];
    const auto at = std::upper_bound(out.begin(), out.end(), response.due,
        [](Tick due, const Pending &item) { return due < item.due; });
    out.insert(at, std::move(response));
    ++transactions.at(dbid).responses;
}

void
ChiSnfBridge::receive(const RawReq &req)
{
    fatal_if(req.tgtid != nodeId || req.size != 6 || (req.addr & 63) ||
             !AddrRange(req.addr, req.addr + 64).isSubset(range),
             "%s invalid 64-byte SN request target=%u addr=%#llx size=%u",
             name(), req.tgtid, req.addr, req.size);
    fatal_if(req.opcode != 0x04 && req.opcode != 0x1d,
             "%s unsupported SN request opcode %#x", name(), req.opcode);
    for (const auto &[id, live] : transactions)
        fatal_if(live.req.srcid == req.srcid && live.req.txnid == req.txnid,
                 "%s duplicate live SN transaction src=%u txn=%u",
                 name(), req.srcid, req.txnid);

    const uint32_t dbid = allocateDbid();
    auto &transaction = transactions.emplace(dbid, Transaction{}).first->second;
    transaction.req = req;
    if (req.opcode == 0x1d) {
        fatal_if(req.expCompAck, "%s WriteNoSnpFull CompAck is not supported", name());
        RawRsp response{};
        response.srcid = nodeId;
        response.tgtid = req.srcid;
        response.txnid = req.txnid;
        response.dbid = dbid;
        response.opcode = 0x06; // DBIDResp; Comp follows the actual memory write.
        queue(RSP, dbid, response, responseLatency);
        return;
    }

    accessLine(transaction, false);
    if (req.order) {
        RawRsp receipt{};
        receipt.srcid = nodeId;
        receipt.tgtid = req.srcid;
        receipt.txnid = req.txnid;
        receipt.opcode = 0x08; // ReadReceipt.
        queue(RSP, dbid, receipt, Cycles(1));
    }
    for (unsigned beat = 0; beat < 2; ++beat) {
        RawDat response{};
        response.srcid = nodeId;
        response.tgtid = req.srcid;
        response.txnid = req.txnid;
        response.dbid = dbid;
        response.HomeNID = nodeId;
        response.opcode = 0x04; // CompData.
        response.dataid = beat * 2;
        response.last = beat == 1;
        response.setPayload(transaction.data.data() + beat * 32, 32);
        queue(DAT, dbid, response, responseLatency + Cycles(beat));
    }
}

void
ChiSnfBridge::receive(const RawDat &dat)
{
    const auto found = transactions.find(dat.txnid);
    fatal_if(dat.tgtid != nodeId || dat.opcode != 0x03 ||
             dat.payloadSize() != 32 || (dat.dataid != 0 && dat.dataid != 2) ||
             found == transactions.end(),
             "%s invalid NonCopyBackWriteData txn=%u", name(), dat.txnid);
    auto &transaction = found->second;
    const auto &req = transaction.req;
    const unsigned beat = dat.dataid / 2;
    fatal_if(req.opcode != 0x1d || req.srcid != dat.srcid ||
             (transaction.received & (1U << beat)) || dat.last != (beat == 1),
             "%s mismatched or duplicate SN write beat txn=%u", name(), dat.txnid);
    std::copy_n(dat.payloadData(), 32, transaction.data.begin() + beat * 32);
    transaction.received |= 1U << beat;
    if (transaction.received != 3) return;

    accessLine(transaction, true);
    RawRsp response{};
    response.srcid = nodeId;
    response.tgtid = req.srcid;
    response.txnid = req.txnid;
    response.opcode = 0x04; // Comp only after both beats have reached memory.
    queue(RSP, dat.txnid, response, responseLatency);
}

void
ChiSnfBridge::receive(const RawRsp &rsp)
{
    const auto found = transactions.find(rsp.txnid);
    fatal_if(rsp.tgtid != nodeId || rsp.opcode != 0x02 ||
             found == transactions.end(),
             "%s unmatched SN CompAck txn=%u", name(), rsp.txnid);
    auto &transaction = found->second;
    fatal_if(transaction.req.opcode != 0x04 ||
             transaction.req.srcid != rsp.srcid || !transaction.req.expCompAck ||
             transaction.acknowledged || transaction.sent != 3,
             "%s invalid or premature SN CompAck txn=%u", name(), rsp.txnid);
    transaction.acknowledged = true;
    retireIfComplete(rsp.txnid);
}

void
ChiSnfBridge::receive(const RawSnp &snp)
{
    fatal("%s SN-F cannot receive snoop opcode %#x", name(), snp.opcode);
}

void
ChiSnfBridge::retireIfComplete(uint32_t dbid)
{
    const auto found = transactions.find(dbid);
    auto &transaction = found->second;
    if (transaction.responses) return;
    const bool complete = transaction.req.opcode == 0x1d ?
        transaction.received == 3 : transaction.sent == 3 &&
        (!transaction.req.expCompAck || transaction.acknowledged);
    if (complete) transactions.erase(found);
}

void
ChiSnfBridge::transmitted(ChannelType ch, const Pending &response)
{
    auto &transaction = transactions.at(response.dbid);
    --transaction.responses;
    if (ch == DAT) {
        const auto &data = std::get<RawDat>(response.flit);
        transaction.sent |= 1U << (data.dataid / 2);
    }
    retireIfComplete(response.dbid);
}

void
ChiSnfBridge::tick()
{
    // Read ports and credits from the preceding edge. A response generated
    // below cannot traverse the endpoint in the same tick as its request.
    for (unsigned c = 0; c < NUM_CHANNELS; ++c) {
        const auto ch = static_cast<ChannelType>(c);
        auto &out = pending[c];
        if (!out.empty() && out.front().due <= curTick() &&
            chi.peerHasRxCredit(ch) && chi.enqueueRx(ch, out.front().flit)) {
            transmitted(ch, out.front());
            out.pop_front();
        }
    }
    for (unsigned c = 0; c < NUM_CHANNELS; ++c) {
        const auto ch = static_cast<ChannelType>(c);
        fatal_if(chi.hasRxFlit(ch), "%s expects incoming CHI traffic on TX", name());
        if (ch == REQ && transactions.size() >= maxTransactions) continue;
        if (!chi.hasTxFlit(ch, true)) continue;
        const auto flit = chi.getTxFlit(ch);
        std::visit([this](const auto &raw) { receive(raw); }, *flit);
    }
    if (!idle()) {
        if (!tickEvent.scheduled()) schedule(tickEvent, clockEdge(Cycles(1)));
    } else if (drainState() == DrainState::Draining) {
        signalDrainDone();
    }
}

ChiSnfBridge::FunctionalPort::FunctionalPort(
    const std::string &name, ChiSnfBridge &owner)
    : ResponsePort(name, &owner), owner(owner)
{}

AddrRangeList
ChiSnfBridge::FunctionalPort::getAddrRanges() const
{
    return {owner.getAddrRange()};
}

Tick
ChiSnfBridge::FunctionalPort::recvAtomic(PacketPtr pkt)
{
    fatal("%s accepts functional image loading only; atomic access must not bypass CHI", name());
}

bool
ChiSnfBridge::FunctionalPort::recvTimingReq(PacketPtr pkt)
{
    fatal("%s accepts functional image loading only; timing access must use CHI", name());
}

void
ChiSnfBridge::FunctionalPort::recvFunctional(PacketPtr pkt)
{
    fatal_if(!owner.idle(), "%s functional loading requires an idle SN-F", name());
    owner.functionalAccess(pkt);
}

void
ChiSnfBridge::FunctionalPort::recvRespRetry()
{
    panic("%s has no timing responses to retry", name());
}

} // namespace gem5::Chi

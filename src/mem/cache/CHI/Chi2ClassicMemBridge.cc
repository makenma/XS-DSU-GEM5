#include "mem/cache/CHI/Chi2ClassicMemBridge.hh"

#include <algorithm>
#include <cstring>
#include <memory>
#include <type_traits>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/Chi2ClassicMemBridge.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/system.hh"

namespace gem5
{

namespace Chi
{

namespace
{

namespace ReqOp
{
constexpr uint8_t ReadNoSnp = 0x04;
}

namespace DatOp
{
constexpr uint8_t CompData = 0x04;
}

constexpr uint8_t RespSC = 0x01;

} // anonymous namespace

Chi2ClassicMemBridge::MemSidePort::MemSidePort(
    const std::string& name, Chi2ClassicMemBridge& owner)
    : RequestPort(name, &owner), owner(owner)
{
}

bool
Chi2ClassicMemBridge::MemSidePort::recvTimingResp(PacketPtr pkt)
{
    return owner.recvMemResp(pkt);
}

void
Chi2ClassicMemBridge::MemSidePort::recvReqRetry()
{
    owner.schedulePump();
}

void
Chi2ClassicMemBridge::MemSidePort::recvRangeChange()
{
    DPRINTF(Chi2ClassicMemBridge, "classic memory side range change\n");
}

Chi2ClassicMemBridge::Chi2ClassicMemBridge(
    const Chi2ClassicMemBridgeParams& p)
    : ClockedObject(p),
      ruby::Consumer(this),
      chiPort(csprintf("%s.chi_side", name()),
              static_cast<ruby::Consumer*>(this), 0),
      memPort(csprintf("%s.mem_side", name()), *this),
      nodeId(p.node_id),
      hnfNodeId(p.hnf_node_id),
      blockSize(p.block_size),
      dataBeatBytes(p.data_beat_bytes),
      maxOutstanding(p.max_outstanding),
      requestorId(p.system->getRequestorId(this)),
      pumpEvent([this]{ pump(); }, name() + ".pumpEvent")
{
    fatal_if(blockSize == 0 || !isPowerOf2(blockSize),
             "%s requires a non-zero power-of-two block_size\n", name());
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "%s requires 0 < data_beat_bytes <= block_size\n", name());
    fatal_if(maxOutstanding == 0,
             "%s requires max_outstanding > 0\n", name());
}

Port&
Chi2ClassicMemBridge::getPort(const std::string& if_name, PortID idx)
{
    if (if_name == "chi_side") {
        return chiPort;
    }
    if (if_name == "mem_side") {
        return memPort;
    }
    return ClockedObject::getPort(if_name, idx);
}

void
Chi2ClassicMemBridge::wakeup()
{
    schedulePump();
}

void
Chi2ClassicMemBridge::print(std::ostream& out) const
{
    out << "Chi2ClassicMemBridge(" << name() << ")";
}

void
Chi2ClassicMemBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        schedule(pumpEvent, nextCycle());
    }
}

bool
Chi2ClassicMemBridge::hasPumpWork() const
{
    return blockedPkt || !txDatQ.empty() || chiPort.hasTxFlit(REQ);
}

uint32_t
Chi2ClassicMemBridge::expectedDataBytes(const RawReq& req) const
{
    return req.size ? req.size : blockSize;
}

PacketPtr
Chi2ClassicMemBridge::makeReadPacket(const RawReq& req)
{
    const uint32_t bytes = expectedDataBytes(req);
    RequestPtr request =
        std::make_shared<Request>(req.addr, bytes, 0, requestorId);
    auto* pkt = new Packet(request, MemCmd::ReadReq);
    pkt->allocate();
    pkt->pushSenderState(new SnfSenderState(req.txnid));
    return pkt;
}

bool
Chi2ClassicMemBridge::sendMemPacket(PacketPtr pkt)
{
    panic_if(blockedPkt && blockedPkt != pkt,
             "%s tried to send a new packet while retry is pending\n", name());
    if (!memPort.sendTimingReq(pkt)) {
        blockedPkt = pkt;
        DPRINTF(Chi2ClassicMemBridge,
                "classic ReadReq blocked addr=%#llx size=%u\n",
                static_cast<unsigned long long>(pkt->getAddr()),
                pkt->getSize());
        return false;
    }

    if (blockedPkt == pkt) {
        blockedPkt = nullptr;
    }
    DPRINTF(Chi2ClassicMemBridge,
            "classic ReadReq sent addr=%#llx size=%u\n",
            static_cast<unsigned long long>(pkt->getAddr()), pkt->getSize());
    return true;
}

void
Chi2ClassicMemBridge::acceptReadNoSnp(const RawReq& req)
{
    const auto decoded = decodeReq(req.opcode);
    if (decoded.minor != ReqMinor::ReadNoSnp) {
        warn("%s drops unsupported SNF REQ opcode=0x%x src=%u tgt=%u "
             "txn=%u\n",
             name(), req.opcode, req.srcid, req.tgtid, req.txnid);
        return;
    }

    if (txns.size() >= maxOutstanding) {
        warn("%s drops ReadNoSnp txn=%u because outstanding is full (%llu)\n",
             name(), req.txnid,
             static_cast<unsigned long long>(txns.size()));
        return;
    }

    panic_if(txns.count(req.txnid),
             "%s duplicate ReadNoSnp txnid=%u src=%u\n",
             name(), req.txnid, req.srcid);

    TxnEntry entry{};
    entry.req = req;
    entry.expectedBytes = expectedDataBytes(req);
    entry.pkt = makeReadPacket(req);
    auto [it, inserted] = txns.emplace(req.txnid, entry);
    panic_if(!inserted, "%s failed to insert ReadNoSnp txnid=%u\n",
             name(), req.txnid);

    DPRINTF(Chi2ClassicMemBridge,
            "accept ReadNoSnp src=%u tgt=%u txn=%u addr=%#llx bytes=%u "
            "to classic\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr), entry.expectedBytes);
    sendMemPacket(it->second.pkt);
}

void
Chi2ClassicMemBridge::drainChiReq()
{
    while (auto flit = chiPort.getTxFlit(REQ)) {
        std::visit([this](auto&& f) {
            using T = std::decay_t<decltype(f)>;
            if constexpr (std::is_same_v<T, RawReq>) {
                acceptReadNoSnp(f);
            } else {
                panic("%s sampled non-REQ flit from REQ channel\n", name());
            }
        }, *flit);
    }
}

bool
Chi2ClassicMemBridge::recvMemResp(PacketPtr pkt)
{
    auto* state = pkt->findNextSenderState<SnfSenderState>();
    panic_if(!state, "%s got classic response without SNF sender state\n",
             name());

    auto it = txns.find(state->txnid);
    panic_if(it == txns.end(),
             "%s got classic response for unknown txnid=%u\n",
             name(), state->txnid);

    TxnEntry& txn = it->second;
    panic_if(pkt != txn.pkt,
             "%s got response packet mismatch txnid=%u\n",
             name(), state->txnid);
    panic_if(!pkt->isResponse(),
             "%s got non-response classic packet cmd=%s txnid=%u\n",
             name(), pkt->cmdString().c_str(), state->txnid);

    const uint8_t* src = pkt->hasData() ? pkt->getConstPtr<uint8_t>() : nullptr;
    std::vector<uint8_t> zeros;
    if (!src) {
        zeros.assign(txn.expectedBytes, 0);
        src = zeros.data();
    }

    DPRINTF(Chi2ClassicMemBridge,
            "classic ReadResp received txn=%u addr=%#llx bytes=%u cmd=%s\n",
            state->txnid, static_cast<unsigned long long>(pkt->getAddr()),
            pkt->getSize(), pkt->cmdString().c_str());
    queueCompData(txn, src, std::min<uint32_t>(txn.expectedBytes,
                                               pkt->getSize()));

    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;
    txns.erase(it);
    schedulePump();
    return true;
}

void
Chi2ClassicMemBridge::queueCompData(const TxnEntry& txn,
                                    const uint8_t* data,
                                    uint32_t data_bytes)
{
    const uint32_t bytes = txn.expectedBytes;
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        RawDat dat{};
        dat.qos = txn.req.qos;
        dat.srcid = nodeId ? nodeId : txn.req.tgtid;
        dat.tgtid = hnfNodeId ? hnfNodeId : txn.req.srcid;
        dat.txnid = txn.req.txnid;
        dat.opcode = DatOp::CompData;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = dat.tgtid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = RespSC;
        dat.beatOffset = offset;
        dat.data.assign(beatBytes, 0);
        if (offset < data_bytes) {
            const uint32_t copyBytes =
                std::min<uint32_t>(beatBytes, data_bytes - offset);
            std::memcpy(dat.data.data(), data + offset, copyBytes);
        }
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        txDatQ.push_back(std::move(dat));
    }

    DPRINTF(Chi2ClassicMemBridge,
            "queue CompData txn=%u beats=%u bytes=%u sn=%u hnf=%u\n",
            txn.req.txnid, (bytes + dataBeatBytes - 1) / dataBeatBytes,
            bytes, nodeId, hnfNodeId);
}

void
Chi2ClassicMemBridge::sendPendingDat()
{
    while (!txDatQ.empty()) {
        RawDat dat = txDatQ.front();
        if (!chiPort.enqueueRx(DAT, dat)) {
            DPRINTF(Chi2ClassicMemBridge,
                    "CompData blocked txn=%u dataid=%u bytes=%u\n",
                    dat.txnid, dat.dataid,
                    static_cast<unsigned>(dat.data.size()));
            return;
        }

        DPRINTF(Chi2ClassicMemBridge,
                "send CompData txn=%u dataid=%u bytes=%u last=%u "
                "src=%u tgt=%u\n",
                dat.txnid, dat.dataid,
                static_cast<unsigned>(dat.data.size()), dat.last,
                dat.srcid, dat.tgtid);
        txDatQ.pop_front();
    }
}

void
Chi2ClassicMemBridge::pump()
{
    if (blockedPkt) {
        sendMemPacket(blockedPkt);
    }
    sendPendingDat();
    drainChiReq();
    sendPendingDat();

    if (hasPumpWork()) {
        schedulePump();
    }
}

} // namespace Chi
} // namespace gem5

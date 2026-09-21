#ifndef __CHICOSIMBRIDGE_CC__
#define __CHICOSIMBRIDGE_CC__

#include "mem/cache/CHI/ChiCosimBridge.hh"

#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <cassert>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <utility>

#include "base/logging.hh"
#include "base/trace.hh"
#include "base/types.hh"
#include "debug/ChiCosimBridge.hh"
#include "sim/core.hh"
#include "sim/sim_exit.hh"
#include "sim/system.hh"

namespace gem5
{

namespace Chi
{

namespace
{

double
wallSeconds()
{
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch())
        .count();
}

/** Tiny recursive-descent JSON parser for the co-sim protocol. */
struct JsonParser
{
    const std::string& s;
    size_t i = 0;
    bool ok = true;

    explicit JsonParser(const std::string& text) : s(text) {}

    void ws() { while (i < s.size() && isspace((unsigned char)s[i])) ++i; }

    bool value(CosimJson& out)
    {
        ws();
        if (i >= s.size()) return fail();
        char c = s[i];
        if (c == '{') return object(out);
        if (c == '[') return array(out);
        if (c == '"') { out.type = CosimJson::Type::Str; return string(out.str); }
        if (c == 't') { out.type = CosimJson::Type::Bool; out.boolean = true;
                        return lit("true"); }
        if (c == 'f') { out.type = CosimJson::Type::Bool; out.boolean = false;
                        return lit("false"); }
        if (c == 'n') return lit("null");
        return number(out);
    }

    bool fail() { ok = false; return false; }

    bool lit(const char* l)
    {
        const size_t n = std::strlen(l);
        if (s.compare(i, n, l) != 0) return fail();
        i += n;
        return true;
    }

    bool number(CosimJson& out)
    {
        out.type = CosimJson::Type::Num;
        char* end = nullptr;
        out.num = std::strtod(s.c_str() + i, &end);
        if (end == s.c_str() + i) return fail();
        i = end - s.c_str();
        return true;
    }

    bool string(std::string& out)
    {
        if (i >= s.size() || s[i] != '"') return fail();
        ++i;
        out.clear();
        while (i < s.size() && s[i] != '"') {
            char c = s[i];
            if (c == '\\' && i + 1 < s.size()) {
                ++i;
                char e = s[i++];
                switch (e) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'u': { // keep it simple: skip 4 hex digits, emit '?'
                    i = std::min(s.size(), i + 4);
                    out += '?';
                    break;
                }
                default: out += e;
                }
            } else {
                out += c;
                ++i;
            }
        }
        if (i >= s.size()) return fail();
        ++i; // closing quote
        return true;
    }

    bool object(CosimJson& out)
    {
        out.type = CosimJson::Type::Obj;
        ++i; // '{'
        ws();
        if (i < s.size() && s[i] == '}') { ++i; return true; }
        while (true) {
            ws();
            std::string key;
            if (!string(key)) return fail();
            ws();
            if (i >= s.size() || s[i] != ':') return fail();
            ++i;
            CosimJson val;
            if (!value(val)) return fail();
            out.obj.emplace(std::move(key), std::move(val));
            ws();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == '}') { ++i; return true; }
            return fail();
        }
    }

    bool array(CosimJson& out)
    {
        out.type = CosimJson::Type::Arr;
        ++i; // '['
        ws();
        if (i < s.size() && s[i] == ']') { ++i; return true; }
        while (true) {
            CosimJson val;
            if (!value(val)) return fail();
            out.arr.push_back(std::move(val));
            ws();
            if (i < s.size() && s[i] == ',') { ++i; continue; }
            if (i < s.size() && s[i] == ']') { ++i; return true; }
            return fail();
        }
    }
};

} // anonymous namespace

// ---------------------------------------------------------------------------
// CosimJson helpers
// ---------------------------------------------------------------------------

const CosimJson*
CosimJson::get(const std::string& key) const
{
    if (type != Type::Obj) return nullptr;
    auto it = obj.find(key);
    return it == obj.end() ? nullptr : &it->second;
}

long
CosimJson::asInt(long def) const
{
    if (type == Type::Num) return (long)num;
    if (type == Type::Str) return strtol(str.c_str(), nullptr, 0);
    return def;
}

std::unique_ptr<CosimJson>
CosimJson::parse(const std::string& text)
{
    JsonParser p(text);
    auto out = std::make_unique<CosimJson>();
    if (!p.value(*out)) return nullptr;
    p.ws();
    if (!p.ok || p.i != text.size()) return nullptr;
    return out;
}

// ---------------------------------------------------------------------------
// construction
// ---------------------------------------------------------------------------

ChiCosimBridge::ChiCosimBridge(const ChiCosimBridgeParams& p)
    : ClockedObject(p),
      Consumer(this),
      chiPort(csprintf("%s.chi_side", name()), *this),
      memPort(csprintf("%s.mem_side", name()), *this),
      bypassPort(csprintf("%s.bypass_side", name()), *this),
      sys(*p.system),
      nodeId(p.node_id),
      hnfNodeId(p.hnf_node_id),
      quantumCycles(p.quantum_cycles),
      barrierTimeout(p.barrier_timeout),
      socketPath(p.socket_path),
      bypassRoute(p.bypass_route),
      requestorId(p.system->getRequestorId(this)),
      barrierEvent([this]{ barrier(); }, name() + ".barrierEvent"),
      pumpEvent([this]{ pump(); }, name() + ".pumpEvent"),
      memSendEvent([this]{ sendOneMemReq(); }, name() + ".memSendEvent"),
      respSendEvent([this]{ sendOneBypassResp(); },
                    name() + ".respSendEvent")
{
    fatal_if(quantumCycles == 0, "%s quantum_cycles must be > 0\n", name());
    fatal_if(bypassRoute != "all" && bypassRoute != "dram_only" &&
             bypassRoute != "none",
             "%s bypass_route must be all|dram_only|none\n", name());
}

Port&
ChiCosimBridge::getPort(const std::string& if_name, PortID idx)
{
    if (if_name == "chi_side") return chiPort;
    if (if_name == "mem_side") return memPort;
    if (if_name == "bypass_side") return bypassPort;
    return ClockedObject::getPort(if_name, idx);
}

void
ChiCosimBridge::print(std::ostream& out) const
{
    out << "ChiCosimBridge(" << name() << ")";
}

// ---------------------------------------------------------------------------
// classic ports
// ---------------------------------------------------------------------------

ChiCosimBridge::MemPort::MemPort(const std::string& name,
                                 ChiCosimBridge& owner)
    : RequestPort(name, &owner), owner(owner) {}

bool
ChiCosimBridge::MemPort::recvTimingResp(PacketPtr pkt)
{
    return owner.memPortRecvResp(pkt);
}

void
ChiCosimBridge::MemPort::recvReqRetry()
{
    if (!owner.memSendEvent.scheduled()) {
        owner.schedule(owner.memSendEvent, owner.clockEdge());
    }
}

void
ChiCosimBridge::MemPort::recvRangeChange()
{
    owner.bypassPort.sendRangeChange();
}

ChiCosimBridge::BypassPort::BypassPort(const std::string& name,
                                       ChiCosimBridge& owner)
    : ResponsePort(name, &owner), owner(owner) {}

bool
ChiCosimBridge::BypassPort::recvTimingReq(PacketPtr pkt)
{
    return owner.bypassRecvReq(pkt);
}

Tick
ChiCosimBridge::BypassPort::recvAtomic(PacketPtr pkt)
{
    // Atomic accesses bypass the co-sim loop (like functional ones).
    return owner.memPort.sendAtomic(pkt);
}

void
ChiCosimBridge::BypassPort::recvFunctional(PacketPtr pkt)
{
    // Functional accesses bypass the co-sim loop by design (debuggers,
    // checkpointing); they are served from the local memory system.
    owner.memPort.sendFunctional(pkt);
}

void
ChiCosimBridge::BypassPort::recvRespRetry()
{
    if (!owner.respSendEvent.scheduled()) {
        owner.schedule(owner.respSendEvent, owner.clockEdge());
    }
}

AddrRangeList
ChiCosimBridge::BypassPort::getAddrRanges() const
{
    return owner.memPort.getAddrRanges();
}

// ---------------------------------------------------------------------------
// lifecycle
// ---------------------------------------------------------------------------

void
ChiCosimBridge::startup()
{
    ClockedObject::startup();
    openListener();
    schedule(barrierEvent, clockEdge(Cycles(quantumCycles)));
}

void
ChiCosimBridge::drainResume()
{
    ClockedObject::drainResume();
    if (!peerDone && !barrierEvent.scheduled()) {
        schedule(barrierEvent, clockEdge(Cycles(quantumCycles)));
    }
}

void
ChiCosimBridge::wakeup()
{
    schedulePump();
}

// ---------------------------------------------------------------------------
// CHI side
// ---------------------------------------------------------------------------

void
ChiCosimBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        schedule(pumpEvent, nextCycle());
    }
}

bool
ChiCosimBridge::hasPumpWork() const
{
    return !txRspQ.empty() || !txDatQ.empty() ||
        chiPort.hasTxFlit(ChannelType::REQ) ||
        chiPort.hasTxFlit(ChannelType::DAT);
}

void
ChiCosimBridge::pump()
{
    sendPendingT2g();
    drainChiReq();
    drainChiDat();
    if (hasPumpWork()) {
        schedulePump();
    }
}

void
ChiCosimBridge::sendPendingT2g()
{
    while (!txRspQ.empty()) {
        if (!chiPort.peerHasRxCredit(ChannelType::RSP)) return;
        const RawRsp rsp = txRspQ.front();
        if (!chiPort.enqueueRx(ChannelType::RSP, rsp)) {
            warn_once("%s lost reserved RSP credit\n", name());
            return;
        }
        txRspQ.pop_front();
        DPRINTF(ChiCosimBridge,
                "t2g RSP opcode=0x%x src=%u tgt=%u txn=%u dbid=%u\n",
                (unsigned)rsp.opcode, rsp.srcid, rsp.tgtid, rsp.txnid,
                (unsigned)rsp.dbid);
    }
    while (!txDatQ.empty()) {
        if (!chiPort.peerHasRxCredit(ChannelType::DAT)) return;
        const RawDat dat = txDatQ.front();
        if (!chiPort.enqueueRx(ChannelType::DAT, dat)) {
            warn_once("%s lost reserved DAT credit\n", name());
            return;
        }
        txDatQ.pop_front();
        DPRINTF(ChiCosimBridge,
                "t2g DAT opcode=0x%x src=%u tgt=%u txn=%u dataid=%u\n",
                (unsigned)dat.opcode, dat.srcid, dat.tgtid, dat.txnid,
                (unsigned)dat.dataid);
    }
}

void
ChiCosimBridge::drainChiReq()
{
    while (chiPort.hasTxFlit(ChannelType::REQ)) {
        auto flit = chiPort.getTxFlit(ChannelType::REQ);
        if (!flit) break;
        if (auto* req = std::get_if<RawReq>(&*flit)) {
            DPRINTF(ChiCosimBridge,
                    "g2t REQ opcode=0x%x src=%u tgt=%u txn=%u addr=%#llx"
                    " size=%u retry=%u\n",
                    req->opcode, req->srcid, req->tgtid, req->txnid,
                    (unsigned long long)req->addr, req->size, req->AllowRetry);
            outbox.push_back(reqToJson(*req));
        }
    }
}

void
ChiCosimBridge::drainChiDat()
{
    while (chiPort.hasTxFlit(ChannelType::DAT)) {
        auto flit = chiPort.getTxFlit(ChannelType::DAT);
        if (!flit) break;
        if (auto* dat = std::get_if<RawDat>(&*flit)) {
            DPRINTF(ChiCosimBridge,
                    "g2t DAT opcode=0x%x src=%u tgt=%u txn=%u dbid=%u"
                    " bytes=%zu\n",
                    dat->opcode, dat->srcid, dat->tgtid, dat->txnid,
                    dat->dbid, dat->data.size());
            outbox.push_back(datToJson(*dat));
        }
    }
}

// ---------------------------------------------------------------------------
// socket / barrier
// ---------------------------------------------------------------------------

void
ChiCosimBridge::openListener()
{
    listenFd = ::socket(AF_UNIX, SOCK_STREAM, 0);
    fatal_if(listenFd < 0, "%s cannot create socket: %s\n",
             name(), strerror(errno));
    ::unlink(socketPath.c_str());
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    fatal_if(socketPath.size() >= sizeof(addr.sun_path),
             "%s socket_path too long: %s\n", name(), socketPath);
    strcpy(addr.sun_path, socketPath.c_str());
    fatal_if(::bind(listenFd, (struct sockaddr*)&addr, sizeof(addr)) < 0,
             "%s cannot bind %s: %s\n", name(), socketPath, strerror(errno));
    fatal_if(::listen(listenFd, 1) < 0,
             "%s cannot listen on %s: %s\n", name(), socketPath,
             strerror(errno));
    int flags = fcntl(listenFd, F_GETFL, 0);
    fcntl(listenFd, F_SETFL, flags | O_NONBLOCK);
    inform("%s co-sim bridge listening on %s (quantum=%u cycles)\n",
           name(), socketPath, quantumCycles);
}

void
ChiCosimBridge::tryAccept()
{
    if (connected) return;
    struct sockaddr_un addr;
    socklen_t len = sizeof(addr);
    int fd = ::accept(listenFd, (struct sockaddr*)&addr, &len);
    if (fd < 0) return; // no client yet
    connFd = fd;
    connected = true;
    rxBuf.clear();
    inform("%s co-sim peer connected\n", name());
}

void
ChiCosimBridge::sendLine(const std::string& line)
{
    std::string buf = line + "\n";
    const char* p = buf.data();
    size_t left = buf.size();
    while (left > 0) {
        ssize_t n = ::send(connFd, p, left, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                struct pollfd pf{connFd, POLLOUT, 0};
                poll(&pf, 1, 1000);
                continue;
            }
            panic("%s peer disconnected (send: %s)\n", name(),
                  strerror(errno));
        }
        p += n;
        left -= n;
    }
}

bool
ChiCosimBridge::recvJsonLine(std::unique_ptr<CosimJson>& out, double timeout_s)
{
    const double deadline = wallSeconds() + timeout_s;
    while (true) {
        size_t nl = rxBuf.find('\n');
        if (nl != std::string::npos) {
            std::string line = rxBuf.substr(0, nl);
            rxBuf.erase(0, nl + 1);
            if (line.empty()) continue;
            out = CosimJson::parse(line);
            if (!out) {
                panic("%s malformed co-sim message: %.80s\n", name(),
                      line.c_str());
            }
            return true;
        }
        double remain = deadline - wallSeconds();
        if (remain <= 0) return false;
        struct pollfd pf{connFd, POLLIN, 0};
        int rc = poll(&pf, 1, std::min(remain, 0.1) * 1000.0);
        if (rc < 0 && errno != EINTR) {
            panic("%s poll failed: %s\n", name(), strerror(errno));
        }
        if (rc == 0) continue;
        if (pf.revents & (POLLHUP | POLLERR)) {
            panic("%s co-sim peer hung up mid-barrier\n", name());
        }
        char tmp[65536];
        ssize_t n = ::recv(connFd, tmp, sizeof(tmp), 0);
        if (n == 0) {
            panic("%s co-sim peer closed the connection\n", name());
        }
        if (n < 0) {
            if (errno == EINTR || errno == EAGAIN) continue;
            panic("%s recv failed: %s\n", name(), strerror(errno));
        }
        rxBuf.append(tmp, n);
    }
}

void
ChiCosimBridge::barrier()
{
    if (peerDone) return;
    if (!connected) {
        // Lockstep starts here: hold the gem5 event loop until the
        // testbench connects, otherwise gem5 would race ahead and the
        // CPUs would stall on unanswered SNF requests.
        const double deadline = wallSeconds() + barrierTimeout;
        while (!connected) {
            tryAccept();
            if (connected) break;
            if (wallSeconds() > deadline) {
                panic("%s no co-sim peer connected to %s within %.1fs\n",
                      name(), socketPath, barrierTimeout);
            }
            struct pollfd pf{listenFd, POLLIN, 0};
            poll(&pf, 1, 100);
        }
    }
    if (!helloSent) {
        std::ostringstream os;
        os << "{\"t\":\"hello\",\"ver\":1"
           << ",\"quantum_cycles\":" << quantumCycles
           << ",\"clk_period_ns\":" << clockPeriod() / 1000.0
           << ",\"snf_id\":" << nodeId
           << ",\"hnf_id\":" << hnfNodeId << "}";
        sendLine(os.str());
        helloSent = true;
    }

    for (const std::string& line : outbox) sendLine(line);
    outbox.clear();

    {
        std::ostringstream os;
        os << "{\"t\":\"sync\",\"cycle\":" << barrierCount * quantumCycles
           << ",\"tick\":" << curTick() << "}";
        sendLine(os.str());
    }
    DPRINTF(ChiCosimBridge, "barrier %llu: sync sent, waiting for ack\n",
            (unsigned long long)barrierCount);

    const double deadline = wallSeconds() + barrierTimeout;
    bool acked = false;
    while (!acked) {
        std::unique_ptr<CosimJson> msg;
        double remain = deadline - wallSeconds();
        if (remain <= 0) {
            panic("%s barrier %llu timed out after %.1fs waiting for the "
                  "co-sim peer\n", name(),
                  (unsigned long long)barrierCount, barrierTimeout);
        }
        if (!recvJsonLine(msg, remain)) continue;
        const CosimJson* t = msg->get("t");
        const std::string kind = t ? t->asStr() : "";
        if (kind == "ack") {
            acked = true;
        } else if (kind == "flit") {
            handleBarrierMessage(*msg);
        } else if (kind == "mem_read") {
            issueMemRead(msg->get("id")->asInt(),
                         (Addr)msg->get("addr")->asInt(),
                         (uint32_t)msg->get("len")->asInt());
        } else if (kind == "mem_write") {
            const auto data = hexDecode(msg->get("data")->asStr());
            const auto strb = hexDecode(msg->get("strb")->asStr());
            issueMemWrite(msg->get("id")->asInt(),
                          (Addr)msg->get("addr")->asInt(), data, strb);
        } else if (kind == "bypass_resp") {
            handleBypassResp(*msg);
        } else if (kind == "hello_ack" || kind == "sync") {
            // hello_ack expected once; stray syncs ignored
        } else if (kind == "bye") {
            inform("%s co-sim peer said bye: %s\n", name(),
                   msg->get("reason") ? msg->get("reason")->asStr().c_str()
                                      : "?");
            peerDone = true;
            exitSimLoop("cosim peer exit");
            return;
        } else {
            warn("%s unknown co-sim message '%s'\n", name(), kind.c_str());
        }
    }

    ++barrierCount;
    schedule(barrierEvent, clockEdge(Cycles(quantumCycles)));
}

void
ChiCosimBridge::handleBarrierMessage(const CosimJson& msg)
{
    const CosimJson* flit = msg.get("flit");
    const CosimJson* ch = msg.get("ch");
    if (!flit || !ch) {
        warn("%s flit message missing fields\n", name());
        return;
    }
    if (ch->asStr() == "rsp") {
        RawRsp rsp;
        if (jsonToRsp(*flit, rsp)) {
            txRspQ.push_back(rsp);
        } else {
            warn("%s failed to parse RSP flit from peer\n", name());
        }
    } else if (ch->asStr() == "dat") {
        RawDat dat;
        if (jsonToDat(*flit, dat)) {
            DPRINTF(ChiCosimBridge, "queued t2g DAT txn=%u bytes=%u\n",
                    dat.txnid, (unsigned)dat.data.size());
            txDatQ.push_back(dat);
        } else {
            warn("%s failed to parse DAT flit from peer\n", name());
        }
    } else {
        warn("%s unexpected flit channel '%s' from peer\n", name(),
             ch->asStr().c_str());
        return;
    }
    schedulePump();
}

// ---------------------------------------------------------------------------
// mem side
// ---------------------------------------------------------------------------

void
ChiCosimBridge::issueMemRead(uint64_t id, Addr addr, uint32_t len)
{
    DPRINTF(ChiCosimBridge, "mem_read id=%llu addr=%#llx len=%u\n",
            (unsigned long long)id, (unsigned long long)addr, len);
    RequestPtr req = std::make_shared<Request>(addr, len, 0, requestorId);
    PacketPtr pkt = new Packet(req, MemCmd::ReadReq);
    pkt->allocate();
    pkt->pushSenderState(new MemState(id, true));
    queueMemReq(pkt);
}

void
ChiCosimBridge::issueMemWrite(uint64_t id, Addr addr,
                              const std::vector<uint8_t>& data,
                              const std::vector<uint8_t>& strb)
{
    DPRINTF(ChiCosimBridge, "mem_write id=%llu addr=%#llx len=%zu\n",
            (unsigned long long)id, (unsigned long long)addr, data.size());
    RequestPtr req =
        std::make_shared<Request>(addr, data.size(), 0, requestorId);
    PacketPtr pkt = new Packet(req, MemCmd::WriteReq);
    pkt->allocate();
    if (!data.empty()) {
        std::memcpy(pkt->getPtr<uint8_t>(), data.data(),
                    std::min(data.size(), (size_t)pkt->getSize()));
        // Classic gem5 packets carry no byte-enable; a partial strobe
        // would need read-modify-write.  The C2XM AXI path only issues
        // fully strobed beats, so warn and write the full line.
        if (strb.size() == data.size() &&
            std::any_of(strb.begin(), strb.end(),
                        [](uint8_t b) { return b == 0; })) {
            warn_once("%s partial write strobe ignored (id=%llu)\n",
                      name(), (unsigned long long)id);
        }
    }
    pkt->pushSenderState(new MemState(id, false));
    queueMemReq(pkt);
}

void
ChiCosimBridge::queueMemReq(PacketPtr pkt)
{
    memReqQ.push_back(pkt);
    if (!memSendEvent.scheduled()) {
        schedule(memSendEvent, clockEdge());
    }
}

void
ChiCosimBridge::sendOneMemReq()
{
    if (memReqQ.empty()) return;
    if (memPort.sendTimingReq(memReqQ.front())) {
        memReqQ.pop_front();
        if (!memReqQ.empty() && !memSendEvent.scheduled()) {
            schedule(memSendEvent, clockEdge());
        }
    }
    // on failure the membus calls recvReqRetry, which reschedules us
}

bool
ChiCosimBridge::memPortRecvResp(PacketPtr pkt)
{
    auto* state = dynamic_cast<MemState*>(pkt->senderState);
    panic_if(!state, "%s mem response without co-sim state\n", name());
    pkt->popSenderState();

    std::ostringstream os;
    os << "{\"t\":" << (state->read ? "\"mem_data\"" : "\"mem_resp\"")
       << ",\"id\":" << state->id;
    if (state->read) {
        const uint8_t* data = pkt->getPtr<uint8_t>();
        os << ",\"data\":\"" << hexEncode({data, data + pkt->getSize()})
           << "\",\"resp\":0";
    } else {
        os << ",\"resp\":0";
    }
    os << "}";
    outbox.push_back(os.str());

    DPRINTF(ChiCosimBridge, "mem resp id=%llu read=%u -> outbox\n",
            (unsigned long long)state->id, state->read);
    delete state;
    delete pkt;
    return true;
}

// ---------------------------------------------------------------------------
// bypass
// ---------------------------------------------------------------------------

bool
ChiCosimBridge::bypassToLocal(PacketPtr pkt)
{
    queueMemReq(pkt);
    return true;
}

bool
ChiCosimBridge::bypassRecvReq(PacketPtr pkt)
{
    const bool is_read = pkt->isRead();
    const bool dram = sys.isMemAddr(pkt->getAddr());

    bool via_cosim;
    if (bypassRoute == "all") {
        via_cosim = true;
    } else if (bypassRoute == "dram_only") {
        via_cosim = dram;
    } else {
        via_cosim = false;
    }

    if (!via_cosim) {
        DPRINTF(ChiCosimBridge,
                "bypass local %s addr=%#llx size=%u\n",
                is_read ? "read" : "write",
                (unsigned long long)pkt->getAddr(), pkt->getSize());
        if (!bypassToLocal(pkt)) {
            // memPort busy and a previous packet is already parked: apply
            // backpressure to the requester.
            return false;
        }
        return true;
    }

    const uint64_t id = nextBypassPkt++;
    bypassPkts[id] = BypassEntry{pkt, is_read};

    std::ostringstream os;
    if (is_read) {
        os << "{\"t\":\"bypass_read\",\"pkt\":" << id
           << ",\"addr\":" << (unsigned long long)pkt->getAddr()
           << ",\"len\":" << pkt->getSize() << "}";
    } else {
        const uint8_t* data = pkt->getConstPtr<uint8_t>();
        std::vector<uint8_t> bytes(data, data + pkt->getSize());
        os << "{\"t\":\"bypass_write\",\"pkt\":" << id
           << ",\"addr\":" << (unsigned long long)pkt->getAddr()
           << ",\"data\":\"" << hexEncode(bytes) << "\""
           << ",\"strb\":\"" << std::string(bytes.size() * 2, '1')
           << "\"}";
    }
    outbox.push_back(os.str());
    DPRINTF(ChiCosimBridge,
            "bypass->cosim pkt=%llu %s addr=%#llx\n",
            (unsigned long long)id, is_read ? "read" : "write",
            (unsigned long long)pkt->getAddr());
    return true;
}

void
ChiCosimBridge::trySendBypassResp(PacketPtr pkt)
{
    bypassRespQ.push_back(pkt);
    if (!respSendEvent.scheduled()) {
        schedule(respSendEvent, clockEdge());
    }
}

void
ChiCosimBridge::sendOneBypassResp()
{
    if (bypassRespQ.empty()) return;
    if (bypassPort.sendTimingResp(bypassRespQ.front())) {
        bypassRespQ.pop_front();
        if (!bypassRespQ.empty() && !respSendEvent.scheduled()) {
            schedule(respSendEvent, clockEdge());
        }
    }
    // on failure the bus calls recvRespRetry, which reschedules us
}

void
ChiCosimBridge::handleBypassResp(const CosimJson& msg)
{
    const uint64_t id = msg.get("pkt")->asInt();
    auto it = bypassPkts.find(id);
    if (it == bypassPkts.end()) {
        warn("%s bypass_resp for unknown packet %llu\n", name(),
             (unsigned long long)id);
        return;
    }
    PacketPtr pkt = it->second.pkt;
    const bool read = it->second.read;
    bypassPkts.erase(it);

    const long resp = msg.get("resp") ? msg.get("resp")->asInt() : 0;
    if (read) {
        const auto data = hexDecode(msg.get("data")
                                        ? msg.get("data")->asStr() : "");
        panic_if(data.size() < pkt->getSize(),
                 "%s bypass read data short (%zu < %u)\n", name(),
                 data.size(), pkt->getSize());
        pkt->makeResponse();
        std::memcpy(pkt->getPtr<uint8_t>(), data.data(), pkt->getSize());
    } else {
        pkt->makeResponse();
    }
    if (resp != 0) {
        warn("%s bypass packet %llu peer error %ld\n", name(),
             (unsigned long long)id, resp);
    }
    trySendBypassResp(pkt);
    DPRINTF(ChiCosimBridge, "bypass resp pkt=%llu done\n",
            (unsigned long long)id);
}

// ---------------------------------------------------------------------------
// serialization
// ---------------------------------------------------------------------------

std::string
ChiCosimBridge::jsonEscape(const std::string& s)
{
    std::string out;
    for (char c : s) {
        if (c == '"' || c == '\\') out += '\\';
        out += c;
    }
    return out;
}

std::string
ChiCosimBridge::hexEncode(const std::vector<uint8_t>& bytes)
{
    static const char* digits = "0123456789abcdef";
    std::string out;
    out.reserve(bytes.size() * 2);
    for (uint8_t b : bytes) {
        out += digits[b >> 4];
        out += digits[b & 0xF];
    }
    return out;
}

std::vector<uint8_t>
ChiCosimBridge::hexDecode(const std::string& hex)
{
    std::vector<uint8_t> out;
    const auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    size_t i = 0;
    while (i + 1 < hex.size()) {
        const int hi = nib(hex[i]), lo = nib(hex[i + 1]);
        if (hi < 0 || lo < 0) break;
        out.push_back((uint8_t)((hi << 4) | lo));
        i += 2;
    }
    return out;
}

std::string
ChiCosimBridge::reqToJson(const RawReq& req) const
{
    std::ostringstream os;
    os << "{\"t\":\"flit\",\"ch\":\"req\",\"dir\":\"g2t\",\"flit\":{"
       << "\"qos\":" << +req.qos
       << ",\"srcid\":" << req.srcid
       << ",\"tgtid\":" << req.tgtid
       << ",\"txnid\":" << req.txnid
       << ",\"opcode\":" << +req.opcode
       << ",\"AllowRetry\":" << +req.AllowRetry
       << ",\"addr\":" << (unsigned long long)req.addr
       << ",\"size\":" << +req.size
       << ",\"ReturnNid\":" << req.ReturnNid
       << ",\"order\":" << +req.order
       << ",\"pcrdtype\":" << +req.pcrdtype
       << ",\"memattr\":" << +req.memattr
       << ",\"snpattr\":" << +req.snpattr
       << ",\"expCompAck\":" << (req.expCompAck ? "true" : "false")
       << ",\"traceTag\":" << (req.traceTag ? "true" : "false")
       << ",\"srcType\":" << +req.srcType
       << ",\"ldid\":" << +req.ldid
       << "}}";
    return os.str();
}

std::string
ChiCosimBridge::datToJson(const RawDat& dat) const
{
    std::ostringstream os;
    os << "{\"t\":\"flit\",\"ch\":\"dat\",\"dir\":\"g2t\",\"flit\":{"
       << "\"qos\":" << +dat.qos
       << ",\"srcid\":" << dat.srcid
       << ",\"tgtid\":" << dat.tgtid
       << ",\"txnid\":" << dat.txnid
       << ",\"opcode\":" << +dat.opcode
       << ",\"last\":" << +dat.last
       << ",\"HomeNID\":" << dat.HomeNID
       << ",\"dbid\":" << +dat.dbid
       << ",\"dataid\":" << +dat.dataid
       << ",\"resp\":" << +dat.resp
       << ",\"beatOffset\":" << +dat.beatOffset
       << ",\"byteEnable\":\"" << hexEncode(dat.byteEnable) << "\""
       << ",\"chunkValid\":\"" << hexEncode(dat.chunkValid) << "\""
       << ",\"data\":\"" << hexEncode(dat.data) << "\""
       << "}}";
    return os.str();
}

bool
ChiCosimBridge::jsonToRsp(const CosimJson& f, RawRsp& rsp) const
{
    rsp = RawRsp{};
    if (auto* v = f.get("qos")) rsp.qos = v->asInt();
    if (auto* v = f.get("srcid")) rsp.srcid = v->asInt();
    if (auto* v = f.get("tgtid")) rsp.tgtid = v->asInt();
    if (auto* v = f.get("txnid")) rsp.txnid = v->asInt();
    if (auto* v = f.get("opcode")) rsp.opcode = v->asInt();
    if (auto* v = f.get("dbid")) rsp.dbid = v->asInt();
    if (auto* v = f.get("resp")) rsp.resp = v->asInt();
    if (auto* v = f.get("respErr")) rsp.respErr = v->asInt();
    if (auto* v = f.get("pcrdtype")) rsp.pcrdtype = v->asInt();
    if (auto* v = f.get("rspKind")) rsp.rspKind =
        static_cast<RspKind>(v->asInt());
    return true;
}

bool
ChiCosimBridge::jsonToDat(const CosimJson& f, RawDat& dat) const
{
    if (f.type != CosimJson::Type::Obj) return false;
    dat = RawDat{};
    if (auto* v = f.get("qos")) dat.qos = v->asInt();
    if (auto* v = f.get("srcid")) dat.srcid = v->asInt();
    if (auto* v = f.get("tgtid")) dat.tgtid = v->asInt();
    if (auto* v = f.get("txnid")) dat.txnid = v->asInt();
    if (auto* v = f.get("opcode")) dat.opcode = v->asInt();
    if (auto* v = f.get("last")) dat.last = v->asInt();
    if (auto* v = f.get("HomeNID")) dat.HomeNID = v->asInt();
    if (auto* v = f.get("dbid")) dat.dbid = v->asInt();
    if (auto* v = f.get("dataid")) dat.dataid = v->asInt();
    if (auto* v = f.get("resp")) dat.resp = v->asInt();
    if (auto* v = f.get("beatOffset")) dat.beatOffset = v->asInt();
    if (auto* v = f.get("byteEnable"))
        dat.byteEnable = hexDecode(v->asStr());
    if (auto* v = f.get("chunkValid"))
        dat.chunkValid = hexDecode(v->asStr());
    if (auto* v = f.get("data")) dat.data = hexDecode(v->asStr());
    return true;
}

} // namespace Chi
} // namespace gem5

#endif // __CHICOSIMBRIDGE_CC__

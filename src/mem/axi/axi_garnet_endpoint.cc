#include "mem/axi/axi_garnet_endpoint.hh"

#include <memory>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/AxiGarnetProbe.hh"
#include "mem/ruby/network/MessageBuffer.hh"
#include "mem/ruby/protocol/AxiBurst.hh"
#include "mem/ruby/protocol/AxiChannel.hh"
#include "mem/ruby/protocol/AxiMeshMsg.hh"
#include "mem/ruby/protocol/AxiResp.hh"
#include "mem/ruby/protocol/DataBlock.hh"
#include "mem/ruby/protocol/MessageSizeType.hh"
#include "mem/ruby/protocol/NetDest.hh"
#include "mem/ruby/slicc_interface/AbstractController.hh"
#include "params/AxiInitiatorAdapter.hh"
#include "params/AxiTargetAdapter.hh"
#include "sim/sim_exit.hh"

namespace gem5
{
namespace axi
{

namespace
{

using ruby::AxiMeshMsg;

constexpr uint64_t RawAwUid = 0xa001;
constexpr uint64_t RawBUid = 0xb001;

std::shared_ptr<AxiMeshMsg>
rawMessage(Tick now, ruby::AxiChannel channel,
           const ruby::MachineID &source, const ruby::MachineID &destination,
           uint32_t src_node, uint16_t src_port, uint32_t dst_node,
           uint64_t uid)
{
    auto msg = std::make_shared<AxiMeshMsg>(now);
    ruby::NetDest destinations;
    destinations.add(destination);

    msg->setChannel(channel);
    msg->setSource(source);
    msg->setDestination(destinations);
    msg->setSrcNode(src_node);
    msg->setSrcPort(src_port);
    msg->setDstNode(dst_node);
    msg->setTxnUid(uid);
    msg->setAxiId(0);
    msg->setTargetSeq(uid);
    msg->setResponseSeq(uid);
    msg->setWriteOrdinal(uid);
    msg->setAddress(0x1000 + uid * 64);
    msg->setBeatIndex(0);
    msg->setBeatCount(1);
    msg->setSize(3);
    msg->setBurst(ruby::AxiBurst_Incr);
    msg->setLock(0);
    msg->setCache(0);
    msg->setProt(0);
    msg->setRegion(0);
    msg->setByteStrobe(0);
    msg->setLast(false);
    msg->setResp(ruby::AxiResp_Okay);
    msg->setQos(0);
    msg->setSemanticBytes(24);
    // Commit 3 activates the protocol-neutral dynamic wire-size hook.  Zero
    // intentionally exercises the legacy MessageSize fallback in Commit 1.
    msg->setWireSizeBytes(0);
    msg->setPayloadDigest(0);
    ruby::DataBlock data;
    data.clear();
    msg->setDataBlk(data);
    msg->setMessageSize(ruby::MessageSizeType_Control);
    return msg;
}

std::shared_ptr<AxiMeshMsg>
peekAxi(ruby::MessageBuffer *buffer, const char *channel)
{
    auto msg = std::dynamic_pointer_cast<AxiMeshMsg>(buffer->peekMsgPtr());
    fatal_if(!msg, "raw AXI shim probe found non-AxiMeshMsg on %s", channel);
    return msg;
}

} // anonymous namespace

AxiInitiatorAdapter::AxiInitiatorAdapter(const Params &p)
    : ClockedObject(p), ruby::Consumer(this),
      shim(p.shim), peer(p.peer), awOut(p.aw_out), wOut(p.w_out),
      arOut(p.ar_out), bLocal(p.b_local), rLocal(p.r_local),
      srcNode(p.src_node), srcPort(p.src_port), dstNode(p.dst_node),
      rawProbe(p.raw_probe), rawProbeHoldCycles(p.raw_probe_hold_cycles)
{
    fatal_if(rawProbe && rawProbeHoldCycles < Cycles(2),
             "%s: raw probe hold must be at least two cycles", name());
}

AxiInitiatorAdapter::~AxiInitiatorAdapter()
{
    bLocal->unregisterDequeueCallback();
    rLocal->unregisterDequeueCallback();
}

void
AxiInitiatorAdapter::init()
{
    ClockedObject::init();
    bLocal->setConsumer(this);
    rLocal->setConsumer(this);

    // A local dequeue may make a SLICC forwarding action eligible again.
    // Wake the existing (and sole) consumer of the network input next cycle.
    bLocal->registerDequeueCallback(
        [this] { shim->scheduleEvent(Cycles(1)); });
    rLocal->registerDequeueCallback(
        [this] { shim->scheduleEvent(Cycles(1)); });
}

void
AxiInitiatorAdapter::startup()
{
    if (rawProbe)
        scheduleEvent(Cycles(1));
}

void
AxiInitiatorAdapter::injectRawAw()
{
    if (rawAwSent || !awOut->areNSlotsAvailable(1, curTick())) {
        return;
    }

    auto msg = rawMessage(curTick(), ruby::AxiChannel_AW,
                          shim->getMachineID(), peer->getMachineID(),
                          srcNode, srcPort, dstNode, RawAwUid);
    awOut->enqueue(msg, curTick(), clockPeriod());
    rawAwSent = true;
    DPRINTF(AxiGarnetProbe, "independent raw AW-like enqueued\n");
}

void
AxiInitiatorAdapter::consumeRawB()
{
    fatal_if(rLocal->isReady(curTick()),
             "%s: unexpected R-like message in Commit 1 probe", name());

    if (!bLocal->isReady(curTick()))
        return;

    fatal_if(rawBDrained,
             "%s: duplicate independent raw B-like message", name());
    auto msg = peekAxi(bLocal, "B local delivery");
    fatal_if(msg->getChannel() != ruby::AxiChannel_B,
             "%s: expected B-like message, got %s", name(),
             msg->getChannel());
    fatal_if(msg->getDestination().count() != 1 ||
             !msg->getDestination().isElement(shim->getMachineID()),
             "%s: B-like message has an invalid Ruby destination", name());
    fatal_if(msg->getSource() != peer->getMachineID(),
             "%s: B-like message has an invalid Ruby source", name());
    fatal_if(msg->getTxnUid() != RawBUid,
             "%s: unexpected independent raw B-like uid", name());

    if (!rawBVisible) {
        rawBVisible = true;
        rawBHoldUntil = clockEdge(rawProbeHoldCycles);
        scheduleEvent(rawProbeHoldCycles);
        DPRINTF(AxiGarnetProbe,
                "holding depth-one B local queue until tick %llu\n",
                static_cast<unsigned long long>(rawBHoldUntil));
        return;
    }

    if (curTick() < rawBHoldUntil)
        return;

    bLocal->dequeue(curTick());
    rawBDrained = true;
    DPRINTF(AxiGarnetProbe, "independent raw B-like drained\n");
}

void
AxiInitiatorAdapter::wakeup()
{
    if (!rawProbe)
        return;

    consumeRawB();
    injectRawAw();

    if (!rawAwSent)
        scheduleEvent(Cycles(1));

    if (!exitRequested && rawAwDrained && rawBDrained) {
        exitRequested = true;
        inform("AXI_MESH raw shim ownership probe passed: "
               "independent messages=2 (AW-like=1, B-like=1), "
               "single-consumer local delivery preserved");
        exitSimLoop("AXI_MESH raw shim ownership probe passed");
    }
}

void
AxiInitiatorAdapter::noteRawAwDrained()
{
    fatal_if(!rawProbe, "%s: raw AW completion outside probe", name());
    fatal_if(rawAwDrained, "%s: duplicate raw AW drain notification", name());
    rawAwDrained = true;
    scheduleEvent(Cycles(1));
}

void
AxiInitiatorAdapter::print(std::ostream &out) const
{
    out << name() << "(AxiInitiatorAdapter)";
}

AxiTargetAdapter::AxiTargetAdapter(const Params &p)
    : ClockedObject(p), ruby::Consumer(this),
      shim(p.shim), peer(p.peer), probeObserver(p.probe_observer),
      bOut(p.b_out), rOut(p.r_out),
      awLocal(p.aw_local), wLocal(p.w_local), arLocal(p.ar_local),
      srcNode(p.src_node), srcPort(p.src_port), dstNode(p.dst_node),
      rawProbe(p.raw_probe),
      rawProbeHoldCycles(p.raw_probe_hold_cycles)
{
    fatal_if(rawProbe && (!peer || !probeObserver),
             "%s: raw probe requires an initiator shim and observer", name());
    fatal_if(rawProbe && rawProbeHoldCycles < Cycles(2),
             "%s: raw probe hold must be at least two cycles", name());
}

AxiTargetAdapter::~AxiTargetAdapter()
{
    awLocal->unregisterDequeueCallback();
    wLocal->unregisterDequeueCallback();
    arLocal->unregisterDequeueCallback();
}

void
AxiTargetAdapter::init()
{
    ClockedObject::init();
    awLocal->setConsumer(this);
    wLocal->setConsumer(this);
    arLocal->setConsumer(this);

    awLocal->registerDequeueCallback(
        [this] { shim->scheduleEvent(Cycles(1)); });
    wLocal->registerDequeueCallback(
        [this] { shim->scheduleEvent(Cycles(1)); });
    arLocal->registerDequeueCallback(
        [this] { shim->scheduleEvent(Cycles(1)); });
}

void
AxiTargetAdapter::startup()
{
    if (rawProbe)
        scheduleEvent(Cycles(1));
}

void
AxiTargetAdapter::injectRawB()
{
    if (rawBSent || !bOut->areNSlotsAvailable(1, curTick()))
        return;

    // This fixed B-like message is an independent reverse-direction probe.
    // It is not derived from, and does not acknowledge, the raw AW-like one.
    auto msg = rawMessage(curTick(), ruby::AxiChannel_B,
                          shim->getMachineID(), peer->getMachineID(),
                          srcNode, srcPort, dstNode, RawBUid);
    msg->setSemanticBytes(8);
    bOut->enqueue(msg, curTick(), clockPeriod());
    rawBSent = true;
    DPRINTF(AxiGarnetProbe, "independent raw B-like enqueued\n");
}

void
AxiTargetAdapter::consumeRawAw()
{
    if (!awLocal->isReady(curTick()))
        return;

    fatal_if(rawAwDrained,
             "%s: duplicate independent raw AW-like message", name());

    if (!holdStarted) {
        holdStarted = true;
        holdUntilTick = clockEdge(rawProbeHoldCycles);
        scheduleEvent(rawProbeHoldCycles);
        DPRINTF(AxiGarnetProbe,
                "holding depth-one AW local queue until tick %llu\n",
                static_cast<unsigned long long>(holdUntilTick));
        return;
    }

    if (curTick() < holdUntilTick)
        return;

    auto request = peekAxi(awLocal, "AW local delivery");
    fatal_if(request->getChannel() != ruby::AxiChannel_AW,
             "%s: expected raw AW-like channel", name());
    fatal_if(request->getTxnUid() != RawAwUid,
             "%s: unexpected independent raw AW-like uid", name());
    fatal_if(request->getDestination().count() != 1 ||
             !request->getDestination().isElement(shim->getMachineID()),
             "%s: AW-like message has an invalid Ruby destination", name());
    fatal_if(request->getSource() != peer->getMachineID(),
             "%s: AW-like message has an invalid Ruby source", name());

    awLocal->dequeue(curTick());
    rawAwDrained = true;
    probeObserver->noteRawAwDrained();
    DPRINTF(AxiGarnetProbe, "independent raw AW-like drained\n");
}

void
AxiTargetAdapter::wakeup()
{
    if (!rawProbe)
        return;

    fatal_if(wLocal->isReady(curTick()) || arLocal->isReady(curTick()),
             "%s: unexpected W/AR message in Commit 1 raw probe", name());
    injectRawB();
    consumeRawAw();

    if (!rawBSent)
        scheduleEvent(Cycles(1));
}

void
AxiTargetAdapter::print(std::ostream &out) const
{
    out << name() << "(AxiTargetAdapter)";
}

} // namespace axi
} // namespace gem5

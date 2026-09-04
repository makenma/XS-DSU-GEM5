#include "mem/axi/axi_garnet_endpoint.hh"

#include <array>
#include <limits>
#include <memory>
#include <tuple>
#include <vector>

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

template <class T>
void
requireVectorSize(const std::vector<T> &values, size_t expected,
                  const std::string &owner, const char *label)
{
    fatal_if(values.size() != expected,
             "%s: %s length %llu must equal %llu", owner, label,
             static_cast<unsigned long long>(values.size()),
             static_cast<unsigned long long>(expected));
}

uint32_t
checkedDepth(const std::vector<uint32_t> &values, size_t expected,
             size_t index, const std::string &owner, const char *label)
{
    requireVectorSize(values, expected, owner, label);
    fatal_if(values[index] == 0, "%s: %s[%llu] must be positive",
             owner, label, static_cast<unsigned long long>(index));
    return values[index];
}

AxiInitiatorConfig
initiatorConfig(const AxiInitiatorAdapterParams &p)
{
    AxiInitiatorConfig config;
    config.source = {p.src_node, p.src_port};
    config.dataBusBytes = p.data_bus_bytes;
    config.idWidth = p.id_width;
    config.maxOutstandingReads = p.max_outstanding_reads;
    config.maxOutstandingWrites = p.max_outstanding_writes;
    requireVectorSize(p.source_fifo_depths, 5, p.name,
                      "source_fifo_depths");
    for (size_t i = 0; i < config.fifoDepths.size(); ++i) {
        fatal_if(p.source_fifo_depths[i] == 0,
                 "%s: source_fifo_depths[%llu] must be positive", p.name,
                 static_cast<unsigned long long>(i));
        config.fifoDepths[i] = p.source_fifo_depths[i];
    }
    config.preAwBursts = p.pre_aw_bursts;
    config.preAwBeats = p.pre_aw_beats;
    config.bRobTransactions = p.b_rob_transactions;
    config.rRobBeats = p.r_rob_beats;
    config.defaultErrorTarget = p.default_error_target;

    requireVectorSize(p.range_ends, p.range_starts.size(), p.name,
                      "range_ends");
    requireVectorSize(p.range_targets, p.range_starts.size(), p.name,
                      "range_targets");
    for (size_t i = 0; i < p.range_starts.size(); ++i) {
        config.ranges.push_back(
            {p.range_starts[i], p.range_ends[i], p.range_targets[i]});
    }

    const size_t quota_count = p.quota_target_nodes.size();
    requireVectorSize(p.quota_write_contexts, quota_count, p.name,
                      "quota_write_contexts");
    requireVectorSize(p.quota_write_beats, quota_count, p.name,
                      "quota_write_beats");
    requireVectorSize(p.quota_read_contexts, quota_count, p.name,
                      "quota_read_contexts");
    requireVectorSize(p.quota_read_beats, quota_count, p.name,
                      "quota_read_beats");
    for (size_t i = 0; i < quota_count; ++i) {
        const bool inserted = config.targetQuotas.emplace(
            p.quota_target_nodes[i],
            AxiQuota{p.quota_write_contexts[i], p.quota_write_beats[i],
                     p.quota_read_contexts[i], p.quota_read_beats[i]}).second;
        fatal_if(!inserted, "%s: duplicate source quota target node %u",
                 p.name, p.quota_target_nodes[i]);
    }
    return config;
}

AxiTargetConfig
targetConfig(const AxiTargetAdapterParams &p)
{
    AxiTargetConfig config;
    config.dstNode = p.dst_node;
    config.dataBusBytes = p.data_bus_bytes;
    config.capacity = {
        p.target_write_contexts, p.target_write_assembly_beats,
        p.target_read_contexts, p.target_read_response_beats};
    config.orphanTransactions = p.orphan_w_transactions;
    config.orphanBeats = p.orphan_w_beats;
    requireVectorSize(p.response_ready_depths, 2, p.name,
                      "response_ready_depths");
    config.bReadyDepth = p.response_ready_depths[0];
    config.rReadyDepth = p.response_ready_depths[1];
    requireVectorSize(p.service_depths, 2, p.name, "service_depths");
    requireVectorSize(p.base_latencies, 2, p.name, "base_latencies");
    config.writeServiceDepth = p.service_depths[0];
    config.readServiceDepth = p.service_depths[1];
    config.writeBaseLatency = p.base_latencies[0];
    config.readBaseLatency = p.base_latencies[1];

    requireVectorSize(p.planned_extra_latency_cycles,
                      p.planned_uids.size(), p.name,
                      "planned_extra_latency_cycles");
    requireVectorSize(p.planned_fault_responses, p.planned_uids.size(),
                      p.name, "planned_fault_responses");
    for (size_t i = 0; i < p.planned_uids.size(); ++i) {
        const uint64_t uid = p.planned_uids[i];
        fatal_if(!config.extraLatency.emplace(
                     uid, p.planned_extra_latency_cycles[i]).second,
                 "%s: duplicate planned txnUid %#llx", p.name,
                 static_cast<unsigned long long>(uid));
        const std::string &response = p.planned_fault_responses[i];
        AxiResp fault = AxiResp::Okay;
        if (response == "slverr") {
            fault = AxiResp::SlvErr;
        } else {
            fatal_if(response != "okay",
                     "%s: planned fault response must be okay or slverr",
                     p.name);
        }
        config.transactionFaults.emplace(uid, fault);
    }

    const size_t source_count = p.source_nodes.size();
    requireVectorSize(p.source_ports, source_count, p.name, "source_ports");
    requireVectorSize(p.quota_write_contexts, source_count, p.name,
                      "quota_write_contexts");
    requireVectorSize(p.quota_write_beats, source_count, p.name,
                      "quota_write_beats");
    requireVectorSize(p.quota_read_contexts, source_count, p.name,
                      "quota_read_contexts");
    requireVectorSize(p.quota_read_beats, source_count, p.name,
                      "quota_read_beats");
    for (size_t i = 0; i < source_count; ++i) {
        const AxiEndpointKey source{p.source_nodes[i], p.source_ports[i]};
        const bool inserted = config.sourceQuotas.emplace(
            source,
            AxiQuota{p.quota_write_contexts[i], p.quota_write_beats[i],
                     p.quota_read_contexts[i], p.quota_read_beats[i]}).second;
        fatal_if(!inserted, "%s: duplicate target quota source %u:%u",
                 p.name, source.srcNode, source.srcPort);
    }

    requireVectorSize(p.memory_range_ends, p.memory_range_starts.size(),
                      p.name, "memory_range_ends");
    for (size_t i = 0; i < p.memory_range_starts.size(); ++i) {
        config.memoryRanges.push_back(
            {p.memory_range_starts[i], p.memory_range_ends[i], p.dst_node});
    }
    return config;
}

AxiBurst
fromRubyBurst(ruby::AxiBurst burst)
{
    switch (burst) {
      case ruby::AxiBurst_Fixed: return AxiBurst::Fixed;
      case ruby::AxiBurst_Incr: return AxiBurst::Incr;
      case ruby::AxiBurst_Wrap: return AxiBurst::Wrap;
      default: panic("AXI_PROTOCOL: unknown Ruby AxiBurst");
    }
}

ruby::AxiBurst
toRubyBurst(AxiBurst burst)
{
    switch (burst) {
      case AxiBurst::Fixed: return ruby::AxiBurst_Fixed;
      case AxiBurst::Incr: return ruby::AxiBurst_Incr;
      case AxiBurst::Wrap: return ruby::AxiBurst_Wrap;
      default: panic("AXI_PROTOCOL: unknown AxiBurst");
    }
}

AxiResp
fromRubyResp(ruby::AxiResp resp)
{
    switch (resp) {
      case ruby::AxiResp_Okay: return AxiResp::Okay;
      case ruby::AxiResp_ExOkay: return AxiResp::ExOkay;
      case ruby::AxiResp_SlvErr: return AxiResp::SlvErr;
      case ruby::AxiResp_DecErr: return AxiResp::DecErr;
      default: panic("AXI_PROTOCOL: unknown Ruby AxiResp");
    }
}

ruby::AxiResp
toRubyResp(AxiResp resp)
{
    switch (resp) {
      case AxiResp::Okay: return ruby::AxiResp_Okay;
      case AxiResp::ExOkay: return ruby::AxiResp_ExOkay;
      case AxiResp::SlvErr: return ruby::AxiResp_SlvErr;
      case AxiResp::DecErr: return ruby::AxiResp_DecErr;
      default: panic("AXI_PROTOCOL: unknown AxiResp");
    }
}

void
setCommon(AxiMeshMsg &msg, const AxiCommonMeta &meta,
          ruby::AxiChannel channel, const ruby::MachineID &source,
          const ruby::MachineID &destination, int wire_bytes)
{
    ruby::NetDest destinations;
    destinations.add(destination);
    msg.setChannel(channel);
    msg.setSource(source);
    msg.setDestination(destinations);
    msg.setSrcNode(meta.srcNode);
    msg.setSrcPort(meta.srcPort);
    msg.setDstNode(meta.dstNode);
    msg.setTxnUid(meta.txnUid);
    msg.setAxiId(meta.axiId);
    msg.setTargetSeq(meta.targetSeq);
    msg.setResponseSeq(meta.responseSeq);
    msg.setQos(meta.qos);
    msg.setAcceptedTick(meta.acceptedTick);
    msg.setSemanticBytes(meta.semanticBytes);
    msg.setWireSizeBytes(wire_bytes);
}

std::shared_ptr<AxiMeshMsg>
addressMessage(Tick now, const AxiAddressPacket &packet,
               ruby::AxiChannel channel, const ruby::MachineID &source,
               const ruby::MachineID &destination, int wire_bytes)
{
    auto msg = std::make_shared<AxiMeshMsg>(now);
    setCommon(*msg, packet.meta, channel, source, destination, wire_bytes);
    msg->setWriteOrdinal(packet.writeOrdinal);
    msg->setAddress(packet.request.address);
    msg->setBeatIndex(0);
    msg->setBeatCount(packet.request.beatCount);
    msg->setSize(packet.request.size);
    msg->setBurst(toRubyBurst(packet.request.burst));
    msg->setLock(packet.request.lock);
    msg->setCache(packet.request.cache);
    msg->setProt(packet.request.prot);
    msg->setRegion(packet.request.region);
    msg->setByteStrobe(0);
    msg->setLast(false);
    msg->setResp(toRubyResp(packet.decodeResp));
    msg->setPayloadDigest(0);
    ruby::DataBlock data;
    data.clear();
    msg->setDataBlk(data);
    msg->setMessageSize(ruby::MessageSizeType_Control);
    return msg;
}

std::shared_ptr<AxiMeshMsg>
dataMessage(Tick now, const AxiDataPacket &packet,
            ruby::AxiChannel channel, const ruby::MachineID &source,
            const ruby::MachineID &destination, int wire_bytes,
            uint32_t data_bus_bytes)
{
    auto msg = std::make_shared<AxiMeshMsg>(now);
    setCommon(*msg, packet.meta, channel, source, destination, wire_bytes);
    msg->setWriteOrdinal(packet.writeOrdinal);
    msg->setAddress(packet.address);
    msg->setBeatIndex(packet.beatIndex);
    msg->setBeatCount(packet.beatCount);
    msg->setSize(0);
    msg->setBurst(ruby::AxiBurst_Incr);
    msg->setLock(0);
    msg->setCache(0);
    msg->setProt(0);
    msg->setRegion(0);
    msg->setByteStrobe(packet.byteStrobe);
    msg->setLast(packet.last);
    msg->setResp(toRubyResp(packet.resp));
    msg->setPayloadDigest(packet.payloadDigest);
    fatal_if(packet.functionalData.size() != data_bus_bytes,
             "AXI data packet must carry one full bus word");
    ruby::DataBlock data;
    data.clear();
    for (uint32_t i = 0; i < data_bus_bytes; ++i)
        data.setByte(i, packet.functionalData[i]);
    msg->setDataBlk(data);
    msg->setMessageSize(ruby::MessageSizeType_Data);
    return msg;
}

std::shared_ptr<AxiMeshMsg>
bMessage(Tick now, const AxiBPacket &packet,
         const ruby::MachineID &source,
         const ruby::MachineID &destination, int wire_bytes)
{
    auto msg = std::make_shared<AxiMeshMsg>(now);
    setCommon(*msg, packet.meta, ruby::AxiChannel_B,
              source, destination, wire_bytes);
    msg->setWriteOrdinal(0);
    msg->setAddress(0);
    msg->setBeatIndex(0);
    msg->setBeatCount(1);
    msg->setSize(0);
    msg->setBurst(ruby::AxiBurst_Incr);
    msg->setLock(0);
    msg->setCache(0);
    msg->setProt(0);
    msg->setRegion(0);
    msg->setByteStrobe(0);
    msg->setLast(false);
    msg->setResp(toRubyResp(packet.resp));
    msg->setPayloadDigest(0);
    ruby::DataBlock data;
    data.clear();
    msg->setDataBlk(data);
    msg->setMessageSize(ruby::MessageSizeType_Control);
    return msg;
}

AxiCommonMeta
messageMeta(const AxiMeshMsg &msg)
{
    AxiCommonMeta meta;
    meta.txnUid = msg.getTxnUid();
    meta.targetSeq = msg.getTargetSeq();
    meta.responseSeq = msg.getResponseSeq();
    meta.srcNode = msg.getSrcNode();
    fatal_if(msg.getSrcPort() < 0 || msg.getSrcPort() > 255,
             "AXI_PROTOCOL: message SrcPort is out of range");
    meta.srcPort = msg.getSrcPort();
    meta.dstNode = msg.getDstNode();
    meta.axiId = msg.getAxiId();
    fatal_if(msg.getSemanticBytes() < 0 || msg.getWireSizeBytes() <= 0 ||
             msg.getQos() < 0 || msg.getQos() > 255,
             "AXI_PROTOCOL: message common integer field is invalid");
    meta.semanticBytes = msg.getSemanticBytes();
    meta.wireBytes = msg.getWireSizeBytes();
    meta.qos = msg.getQos();
    meta.acceptedTick = msg.getAcceptedTick();
    return meta;
}

AxiAddressPacket
messageAddressPacket(const AxiMeshMsg &msg)
{
    fatal_if(msg.getBeatCount() < 0 || msg.getBeatCount() > 65535 ||
             msg.getSize() < 0 || msg.getSize() > 255 ||
             msg.getLock() < 0 || msg.getLock() > 255 ||
             msg.getCache() < 0 || msg.getCache() > 255 ||
             msg.getProt() < 0 || msg.getProt() > 255 ||
             msg.getRegion() < 0 || msg.getRegion() > 255,
             "AXI_PROTOCOL: address message field is out of range");
    AxiAddressPacket packet;
    packet.meta = messageMeta(msg);
    packet.request.axiId = msg.getAxiId();
    packet.request.address = msg.getAddress();
    packet.request.beatCount = msg.getBeatCount();
    packet.request.size = msg.getSize();
    packet.request.burst = fromRubyBurst(msg.getBurst());
    packet.request.lock = msg.getLock();
    packet.request.cache = msg.getCache();
    packet.request.prot = msg.getProt();
    packet.request.region = msg.getRegion();
    packet.request.qos = packet.meta.qos;
    packet.writeOrdinal = msg.getWriteOrdinal();
    packet.decodeResp = fromRubyResp(msg.getResp());
    return packet;
}

AxiDataPacket
messageDataPacket(const AxiMeshMsg &msg, uint32_t data_bus_bytes)
{
    fatal_if(msg.getBeatIndex() < 0 || msg.getBeatIndex() > 65535 ||
             msg.getBeatCount() < 0 || msg.getBeatCount() > 65535,
             "AXI_PROTOCOL: data message beat field is out of range");
    AxiDataPacket packet;
    packet.meta = messageMeta(msg);
    packet.writeOrdinal = msg.getWriteOrdinal();
    packet.beatIndex = msg.getBeatIndex();
    packet.beatCount = msg.getBeatCount();
    packet.last = msg.getLast();
    packet.byteStrobe = msg.getByteStrobe();
    packet.resp = fromRubyResp(msg.getResp());
    packet.payloadDigest = msg.getPayloadDigest();
    packet.address = msg.getAddress();
    packet.functionalData.resize(data_bus_bytes);
    const ruby::DataBlock &data = msg.getDataBlk();
    for (uint32_t i = 0; i < data_bus_bytes; ++i)
        packet.functionalData[i] = data.getByte(i);
    fatal_if(payloadDigest(packet.functionalData) != packet.payloadDigest,
             "AXI_PROTOCOL: data message payload digest mismatch");
    return packet;
}

AxiBPacket
messageBPacket(const AxiMeshMsg &msg)
{
    AxiBPacket packet;
    packet.meta = messageMeta(msg);
    packet.resp = fromRubyResp(msg.getResp());
    return packet;
}

AxiWireBytes
checkedWireBytes(const std::string &owner,
                 const std::vector<uint32_t> &header_bytes,
                 uint32_t data_bus_bytes)
{
    AxiWireBytes wire_bytes;
    const std::string error = normalizeAxiWireBytes(
        header_bytes, data_bus_bytes, wire_bytes);
    fatal_if(!error.empty(), "%s: %s", owner, error);
    return wire_bytes;
}

std::shared_ptr<AxiMeshMsg>
rawMessage(Tick now, ruby::AxiChannel channel,
           const ruby::MachineID &source, const ruby::MachineID &destination,
           uint32_t src_node, uint16_t src_port, uint32_t dst_node,
           uint64_t uid, int wire_size_bytes)
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
    msg->setAcceptedTick(now);
    msg->setSemanticBytes(24);
    msg->setWireSizeBytes(wire_size_bytes);
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
      channelWireBytes(checkedWireBytes(
          p.name, p.wire_header_bytes, p.data_bus_bytes)),
      dataBusBytes(p.data_bus_bytes), rawProbe(p.raw_probe),
      rawProbeHoldCycles(p.raw_probe_hold_cycles),
      functionalState(p.raw_probe ? nullptr :
          std::make_unique<AxiInitiatorState>(initiatorConfig(p))),
      bIngress(checkedDepth(p.source_fifo_depths, 5, 2, p.name,
                            "source_fifo_depths")),
      rIngress(checkedDepth(p.source_fifo_depths, 5, 4, p.name,
                            "source_fifo_depths")),
      awInjectionDelay((requireVectorSize(
          p.injection_delays, 3, p.name, "injection_delays"),
          p.injection_delays[0])),
      wInjectionDelay(p.injection_delays[1]),
      arInjectionDelay(p.injection_delays[2]),
      bResponseEjectionStallUntil((requireVectorSize(
          p.response_ejection_stall_until, 2, p.name,
          "response_ejection_stall_until"),
          p.response_ejection_stall_until[0])),
      rResponseEjectionStallUntil(p.response_ejection_stall_until[1])
{
    fatal_if(rawProbe && rawProbeHoldCycles < Cycles(2),
             "%s: raw probe hold must be at least two cycles", name());
    if (!rawProbe) {
        requireVectorSize(p.targets, p.target_nodes.size(), p.name,
                          "targets");
        for (size_t i = 0; i < p.targets.size(); ++i) {
            fatal_if(!p.targets[i], "%s: null functional target shim", name());
            fatal_if(!targetsByNode.emplace(
                p.target_nodes[i], p.targets[i]).second,
                "%s: duplicate functional target node %u", name(),
                p.target_nodes[i]);
        }
        fatal_if(targetsByNode.count(p.default_error_target) == 0,
                 "%s: default error target %u has no Ruby shim", name(),
                 p.default_error_target);
        for (const uint32_t target : p.range_targets) {
            fatal_if(targetsByNode.count(target) == 0,
                     "%s: normal range target %u has no Ruby shim", name(),
                     target);
        }
    }
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
                          srcNode, srcPort, dstNode, RawAwUid,
                          wireBytesFor(channelWireBytes, AxiWireSlot::Aw));
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
    if (!rawProbe) {
        functionalWakeup();
        return;
    }

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

bool
AxiInitiatorAdapter::tryAcceptAw(const AxiAddressRequest &aw)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional AW used during raw probe", name());
    const bool accepted = functionalState->tryAcceptAw(aw, curTick());
    if (accepted) {
        updateQueueHighWater();
        scheduleEvent(Cycles(1));
    }
    return accepted;
}

bool
AxiInitiatorAdapter::tryAcceptW(const AxiWBeat &w)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional W used during raw probe", name());
    const bool accepted = functionalState->tryAcceptW(w, curTick());
    if (accepted) {
        updateQueueHighWater();
        scheduleEvent(Cycles(1));
    }
    return accepted;
}

bool
AxiInitiatorAdapter::tryAcceptWForFinalCheck(const AxiWBeat &w)
{
    fatal_if(rawProbe || !functionalState,
             "%s: final-check W used during raw probe", name());
    const bool accepted = functionalState->tryAcceptW(w, curTick());
    if (accepted)
        updateQueueHighWater();
    return accepted;
}

bool
AxiInitiatorAdapter::tryAcceptAr(const AxiAddressRequest &ar)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional AR used during raw probe", name());
    const bool accepted = functionalState->tryAcceptAr(ar, curTick());
    if (accepted) {
        updateQueueHighWater();
        scheduleEvent(Cycles(1));
    }
    return accepted;
}

AxiAddressPacket
AxiInitiatorAdapter::lastAcceptedAw() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional AW metadata used during raw probe", name());
    return functionalState->lastAcceptedAw();
}

AxiAddressPacket
AxiInitiatorAdapter::lastAcceptedAr() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional AR metadata used during raw probe", name());
    return functionalState->lastAcceptedAr();
}

bool
AxiInitiatorAdapter::tryConsumeB(AxiBBeat &b)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional B used during raw probe", name());
    const bool consumed = functionalState->tryConsumeB(b);
    if (consumed)
        scheduleEvent(Cycles(1));
    return consumed;
}

bool
AxiInitiatorAdapter::tryConsumeR(AxiRBeat &r)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional R used during raw probe", name());
    const bool consumed = functionalState->tryConsumeR(r);
    if (consumed)
        scheduleEvent(Cycles(1));
    return consumed;
}

void
AxiInitiatorAdapter::processFunctionalIngress()
{
    if (!bIngress.empty()) {
        functionalState->acceptBPacket(bIngress.front(), curTick());
        bIngress.pop();
    }
    if (!rIngress.empty()) {
        functionalState->acceptRPacket(rIngress.front(), curTick());
        rIngress.pop();
    }
}

void
AxiInitiatorAdapter::ingestFunctionalResponses()
{
    const bool b_stalled = curCycle() < bResponseEjectionStallUntil;
    const bool r_stalled = curCycle() < rResponseEjectionStallUntil;
    if (b_stalled && bLocal->isReady(curTick()))
        ++adapterProgress.bEjectionStallCycles;
    if (r_stalled && rLocal->isReady(curTick()))
        ++adapterProgress.rEjectionStallCycles;

    if (!b_stalled && !bIngress.full() && bLocal->isReady(curTick())) {
        auto msg = peekAxi(bLocal, "B local delivery");
        fatal_if(msg->getChannel() != ruby::AxiChannel_B,
                 "%s: non-B message in B local delivery", name());
        const AxiBPacket packet = messageBPacket(*msg);
        const auto target = targetsByNode.find(packet.meta.dstNode);
        fatal_if(target == targetsByNode.end() ||
                 msg->getSource() != target->second->getMachineID(),
                 "%s: B response has an invalid Ruby source", name());
        fatal_if(msg->getDestination().count() != 1 ||
                 !msg->getDestination().isElement(shim->getMachineID()),
                 "%s: B response has an invalid Ruby destination", name());
        panic_if(!bIngress.push(packet), "AXI B ingress FIFO overflow");
        bLocal->dequeue(curTick());
    }

    if (!r_stalled && !rIngress.full() && rLocal->isReady(curTick())) {
        auto msg = peekAxi(rLocal, "R local delivery");
        fatal_if(msg->getChannel() != ruby::AxiChannel_R,
                 "%s: non-R message in R local delivery", name());
        const AxiDataPacket packet = messageDataPacket(*msg, dataBusBytes);
        const auto target = targetsByNode.find(packet.meta.dstNode);
        fatal_if(target == targetsByNode.end() ||
                 msg->getSource() != target->second->getMachineID(),
                 "%s: R response has an invalid Ruby source", name());
        fatal_if(msg->getDestination().count() != 1 ||
                 !msg->getDestination().isElement(shim->getMachineID()),
                 "%s: R response has an invalid Ruby destination", name());
        panic_if(!rIngress.push(packet), "AXI R ingress FIFO overflow");
        rLocal->dequeue(curTick());
    }
}

void
AxiInitiatorAdapter::injectFunctionalRequests()
{
    functionalState->advance();

    if (functionalState->hasAwPacket()) {
        const auto &packet = functionalState->frontAwPacket();
        const Tick eligible = packet.meta.acceptedTick +
            awInjectionDelay * clockPeriod();
        if (curTick() >= eligible &&
            awOut->areNSlotsAvailable(1, curTick())) {
            auto target = targetsByNode.at(packet.meta.dstNode);
            auto msg = addressMessage(
                curTick(), packet, ruby::AxiChannel_AW,
                shim->getMachineID(), target->getMachineID(),
                wireBytesFor(channelWireBytes, AxiWireSlot::Aw));
            awOut->enqueue(msg, curTick(), clockPeriod());
            functionalState->popAwPacket();
        } else if (curTick() >= eligible) {
            ++queueHighWater.messageBufferStallCycles[0];
        }
    }

    if (functionalState->hasWPacket()) {
        const auto &packet = functionalState->frontWPacket();
        const Tick eligible = packet.meta.acceptedTick +
            wInjectionDelay * clockPeriod();
        if (curTick() >= eligible &&
            wOut->areNSlotsAvailable(1, curTick())) {
            auto target = targetsByNode.at(packet.meta.dstNode);
            auto msg = dataMessage(
                curTick(), packet, ruby::AxiChannel_W,
                shim->getMachineID(), target->getMachineID(),
                wireBytesFor(channelWireBytes, AxiWireSlot::W),
                dataBusBytes);
            wOut->enqueue(msg, curTick(), clockPeriod());
            functionalState->popWPacket();
        } else if (curTick() >= eligible) {
            ++queueHighWater.messageBufferStallCycles[1];
        }
    }

    if (functionalState->hasArPacket()) {
        const auto &packet = functionalState->frontArPacket();
        const Tick eligible = packet.meta.acceptedTick +
            arInjectionDelay * clockPeriod();
        if (curTick() >= eligible &&
            arOut->areNSlotsAvailable(1, curTick())) {
            auto target = targetsByNode.at(packet.meta.dstNode);
            auto msg = addressMessage(
                curTick(), packet, ruby::AxiChannel_AR,
                shim->getMachineID(), target->getMachineID(),
                wireBytesFor(channelWireBytes, AxiWireSlot::Ar));
            arOut->enqueue(msg, curTick(), clockPeriod());
            functionalState->popArPacket();
        } else if (curTick() >= eligible) {
            ++queueHighWater.messageBufferStallCycles[3];
        }
    }
}

bool
AxiInitiatorAdapter::hasFunctionalWork() const
{
    const auto occupancy = functionalState->occupancy();
    return occupancy.aw != 0 || occupancy.w != 0 || occupancy.ar != 0 ||
           !bIngress.empty() || !rIngress.empty() ||
           bLocal->isReady(curTick()) || rLocal->isReady(curTick());
}

void
AxiInitiatorAdapter::functionalWakeup()
{
    updateQueueHighWater();
    adapterProgress.bLocalHighWater = std::max(
        adapterProgress.bLocalHighWater,
        static_cast<size_t>(bLocal->getNumMessages()));
    adapterProgress.rLocalHighWater = std::max(
        adapterProgress.rLocalHighWater,
        static_cast<size_t>(rLocal->getNumMessages()));
    adapterProgress.bIngressHighWater = std::max(
        adapterProgress.bIngressHighWater, bIngress.size());
    adapterProgress.rIngressHighWater = std::max(
        adapterProgress.rIngressHighWater, rIngress.size());
    processFunctionalIngress();
    injectFunctionalRequests();
    ingestFunctionalResponses();
    updateQueueHighWater();
    if (hasFunctionalWork())
        scheduleEvent(Cycles(1));
}

void
AxiInitiatorAdapter::updateQueueHighWater()
{
    const auto occupancy = functionalState->occupancy();
    const std::array<size_t, 5> local = {
        occupancy.aw, occupancy.w, occupancy.b, occupancy.ar, occupancy.r};
    const std::array<size_t, 5> messages = {
        static_cast<size_t>(awOut->getNumMessages()),
        static_cast<size_t>(wOut->getNumMessages()),
        static_cast<size_t>(bLocal->getNumMessages()),
        static_cast<size_t>(arOut->getNumMessages()),
        static_cast<size_t>(rLocal->getNumMessages())};
    const std::array<size_t, 5> ingress = {
        0, 0, bIngress.size(), 0, rIngress.size()};
    for (unsigned channel = 0; channel < 5; ++channel) {
        queueHighWater.localFifo[channel] = std::max(
            queueHighWater.localFifo[channel], local[channel]);
        queueHighWater.messageBuffer[channel] = std::max(
            queueHighWater.messageBuffer[channel], messages[channel]);
        queueHighWater.adapterIngress[channel] = std::max(
            queueHighWater.adapterIngress[channel], ingress[channel]);
    }
}

AxiInitiatorOccupancy
AxiInitiatorAdapter::functionalOccupancy() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional occupancy used during raw probe", name());
    auto occupancy = functionalState->occupancy();
    occupancy.b += bIngress.size();
    occupancy.r += rIngress.size();
    return occupancy;
}

AxiInitiatorAdapterProgress
AxiInitiatorAdapter::functionalProgress() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional progress used during raw probe", name());
    AxiInitiatorAdapterProgress result = adapterProgress;
    result.core = functionalState->progress();
    return result;
}

AxiEndpointQueueHighWater
AxiInitiatorAdapter::functionalQueueHighWater() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional queue stats used during raw probe", name());
    return queueHighWater;
}

std::string
AxiInitiatorAdapter::finalConsistencyError() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: final consistency used during raw probe", name());
    return functionalState->finalConsistencyError();
}

std::optional<AxiInitiatorResidual>
AxiInitiatorAdapter::finalResidual() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: final residual used during raw probe", name());
    return functionalState->finalResidual();
}

bool
AxiInitiatorAdapter::functionalIdle() const
{
    if (rawProbe || !functionalState)
        return false;
    const auto occupancy = functionalOccupancy();
    return occupancy.aw == 0 && occupancy.w == 0 && occupancy.b == 0 &&
           occupancy.ar == 0 && occupancy.r == 0 &&
           occupancy.unboundBursts == 0 && occupancy.unboundBeats == 0 &&
           occupancy.outstandingWrites == 0 &&
           occupancy.outstandingReads == 0 &&
           !bLocal->isReady(curTick()) && !rLocal->isReady(curTick());
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
      channelWireBytes(checkedWireBytes(
          p.name, p.wire_header_bytes, p.data_bus_bytes)),
      dataBusBytes(p.data_bus_bytes), rawProbe(p.raw_probe),
      rawProbeHoldCycles(p.raw_probe_hold_cycles),
      functionalState(p.raw_probe ? nullptr :
          std::make_unique<AxiTargetState>(targetConfig(p))),
      awIngress(checkedDepth(p.ingress_depths, 3, 0, p.name,
                             "ingress_depths")),
      wIngress(checkedDepth(p.ingress_depths, 3, 1, p.name,
                            "ingress_depths")),
      arIngress(checkedDepth(p.ingress_depths, 3, 2, p.name,
                             "ingress_depths"))
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
                          srcNode, srcPort, dstNode, RawBUid,
                          wireBytesFor(channelWireBytes, AxiWireSlot::B));
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
    if (!rawProbe) {
        functionalWakeup();
        return;
    }

    fatal_if(wLocal->isReady(curTick()) || arLocal->isReady(curTick()),
             "%s: unexpected W/AR message in Commit 1 raw probe", name());
    injectRawB();
    consumeRawAw();

    if (!rawBSent)
        scheduleEvent(Cycles(1));
}

void
AxiTargetAdapter::processFunctionalIngress()
{
    const uint64_t now = curTick() / clockPeriod();
    // W is considered first so an intentionally earlier W can create W_ONLY;
    // the channels remain independent and a blocked W never blocks AW/AR.
    if (!wIngress.empty()) {
        if (functionalState->canAcceptW(wIngress.front())) {
            functionalState->acceptW(wIngress.front(), now);
            wIngress.pop();
        } else {
            ++orphanOrQuotaStallCycles;
        }
    }
    if (!awIngress.empty()) {
        if (functionalState->canAcceptAw(awIngress.front())) {
            functionalState->acceptAw(awIngress.front(), now);
            awIngress.pop();
        } else {
            ++orphanOrQuotaStallCycles;
        }
    }
    if (!arIngress.empty()) {
        if (functionalState->canAcceptAr(arIngress.front())) {
            functionalState->acceptAr(arIngress.front(), now);
            arIngress.pop();
        } else {
            ++orphanOrQuotaStallCycles;
        }
    }
}

void
AxiTargetAdapter::ingestFunctionalRequests()
{
    auto remember_route = [this](const AxiMeshMsg &msg) {
        const uint64_t uid = msg.getTxnUid();
        const auto [it, inserted] = responseDestinations.emplace(
            uid, msg.getSource());
        fatal_if(!inserted && it->second != msg.getSource(),
                 "%s: txnUid arrived from conflicting Ruby sources", name());
    };

    if (!wIngress.full() && wLocal->isReady(curTick())) {
        auto msg = peekAxi(wLocal, "W local delivery");
        fatal_if(msg->getChannel() != ruby::AxiChannel_W,
                 "%s: non-W message in W local delivery", name());
        fatal_if(msg->getDestination().count() != 1 ||
                 !msg->getDestination().isElement(shim->getMachineID()),
                 "%s: W request has an invalid Ruby destination", name());
        AxiDataPacket packet = messageDataPacket(*msg, dataBusBytes);
        fatal_if(packet.meta.dstNode != dstNode,
                 "%s: W logical destination mismatch", name());
        remember_route(*msg);
        panic_if(!wIngress.push(std::move(packet)),
                 "AXI W ingress FIFO overflow");
        wLocal->dequeue(curTick());
    }

    if (!awIngress.full() && awLocal->isReady(curTick())) {
        auto msg = peekAxi(awLocal, "AW local delivery");
        fatal_if(msg->getChannel() != ruby::AxiChannel_AW,
                 "%s: non-AW message in AW local delivery", name());
        fatal_if(msg->getDestination().count() != 1 ||
                 !msg->getDestination().isElement(shim->getMachineID()),
                 "%s: AW request has an invalid Ruby destination", name());
        AxiAddressPacket packet = messageAddressPacket(*msg);
        fatal_if(packet.meta.dstNode != dstNode,
                 "%s: AW logical destination mismatch", name());
        remember_route(*msg);
        panic_if(!awIngress.push(std::move(packet)),
                 "AXI AW ingress FIFO overflow");
        awLocal->dequeue(curTick());
    }

    if (!arIngress.full() && arLocal->isReady(curTick())) {
        auto msg = peekAxi(arLocal, "AR local delivery");
        fatal_if(msg->getChannel() != ruby::AxiChannel_AR,
                 "%s: non-AR message in AR local delivery", name());
        fatal_if(msg->getDestination().count() != 1 ||
                 !msg->getDestination().isElement(shim->getMachineID()),
                 "%s: AR request has an invalid Ruby destination", name());
        AxiAddressPacket packet = messageAddressPacket(*msg);
        fatal_if(packet.meta.dstNode != dstNode,
                 "%s: AR logical destination mismatch", name());
        remember_route(*msg);
        panic_if(!arIngress.push(std::move(packet)),
                 "AXI AR ingress FIFO overflow");
        arLocal->dequeue(curTick());
    }
}

void
AxiTargetAdapter::injectFunctionalResponses()
{
    functionalState->advance(curTick() / clockPeriod());
    if (functionalState->hasBPacket()) {
        if (!bOut->areNSlotsAvailable(1, curTick())) {
            ++queueHighWater.messageBufferStallCycles[2];
        } else {
        const AxiBPacket &packet = functionalState->frontBPacket();
        const auto destination = responseDestinations.find(
            packet.meta.txnUid);
        fatal_if(destination == responseDestinations.end(),
                 "%s: B has no saved request Ruby source", name());
        auto msg = bMessage(
            curTick(), packet, shim->getMachineID(), destination->second,
            wireBytesFor(channelWireBytes, AxiWireSlot::B));
        bOut->enqueue(msg, curTick(), clockPeriod());
        functionalState->popBPacket();
        responseDestinations.erase(destination);
        }
    }

    if (functionalState->hasRPacket()) {
        if (!rOut->areNSlotsAvailable(1, curTick())) {
            ++queueHighWater.messageBufferStallCycles[4];
        } else {
        const AxiDataPacket &packet = functionalState->frontRPacket();
        const auto destination = responseDestinations.find(
            packet.meta.txnUid);
        fatal_if(destination == responseDestinations.end(),
                 "%s: R has no saved request Ruby source", name());
        const bool last = packet.last;
        auto msg = dataMessage(
            curTick(), packet, ruby::AxiChannel_R,
            shim->getMachineID(), destination->second,
            wireBytesFor(channelWireBytes, AxiWireSlot::R), dataBusBytes);
        rOut->enqueue(msg, curTick(), clockPeriod());
        functionalState->popRPacket();
        if (last)
            responseDestinations.erase(destination);
        }
    }
}

bool
AxiTargetAdapter::hasFunctionalWork() const
{
    const auto occupancy = functionalState->occupancy();
    return !awIngress.empty() || !wIngress.empty() || !arIngress.empty() ||
           occupancy.writeContexts != 0 || occupancy.readContexts != 0 ||
           occupancy.writeServices != 0 || occupancy.readServices != 0 ||
           occupancy.bReady != 0 || occupancy.rReady != 0 ||
           awLocal->isReady(curTick()) || wLocal->isReady(curTick()) ||
           arLocal->isReady(curTick());
}

void
AxiTargetAdapter::functionalWakeup()
{
    updateQueueHighWater();
    processFunctionalIngress();
    injectFunctionalResponses();
    ingestFunctionalRequests();
    updateQueueHighWater();
    if (hasFunctionalWork())
        scheduleEvent(Cycles(1));
}

void
AxiTargetAdapter::updateQueueHighWater()
{
    const auto occupancy = functionalState->occupancy();
    const std::array<size_t, 5> local = {
        occupancy.writeContexts, occupancy.writeReservedBeats,
        occupancy.bReady, occupancy.readContexts, occupancy.rReady};
    const std::array<size_t, 5> messages = {
        static_cast<size_t>(awLocal->getNumMessages()),
        static_cast<size_t>(wLocal->getNumMessages()),
        static_cast<size_t>(bOut->getNumMessages()),
        static_cast<size_t>(arLocal->getNumMessages()),
        static_cast<size_t>(rOut->getNumMessages())};
    const std::array<size_t, 5> ingress = {
        awIngress.size(), wIngress.size(), 0, arIngress.size(), 0};
    for (unsigned channel = 0; channel < 5; ++channel) {
        queueHighWater.localFifo[channel] = std::max(
            queueHighWater.localFifo[channel], local[channel]);
        queueHighWater.messageBuffer[channel] = std::max(
            queueHighWater.messageBuffer[channel], messages[channel]);
        queueHighWater.adapterIngress[channel] = std::max(
            queueHighWater.adapterIngress[channel], ingress[channel]);
    }
}

AxiTargetOccupancy
AxiTargetAdapter::functionalOccupancy() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional occupancy used during raw probe", name());
    auto occupancy = functionalState->occupancy();
    occupancy.writeContexts += awIngress.size() + wIngress.size();
    occupancy.readContexts += arIngress.size();
    return occupancy;
}

AxiTargetProgress
AxiTargetAdapter::functionalProgress() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional progress used during raw probe", name());
    AxiTargetProgress result = functionalState->progress();
    result.orphanOrQuotaStallCycles = orphanOrQuotaStallCycles;
    return result;
}

AxiEndpointQueueHighWater
AxiTargetAdapter::functionalQueueHighWater() const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional queue stats used during raw probe", name());
    return queueHighWater;
}

uint8_t
AxiTargetAdapter::readMemoryByte(uint64_t address) const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional memory used during raw probe", name());
    return functionalState->memory().readByte(address);
}

bool
AxiTargetAdapter::containsMemoryAddress(uint64_t address) const
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional memory used during raw probe", name());
    return functionalState->memory().contains(address);
}

void
AxiTargetAdapter::writeMemoryByte(uint64_t address, uint8_t value)
{
    fatal_if(rawProbe || !functionalState,
             "%s: functional memory used during raw probe", name());
    functionalState->memory().writeByte(address, value);
}

void
AxiTargetAdapter::registerWriteCommitObserver(AxiWriteCommitObserver *observer)
{
    fatal_if(rawProbe || !functionalState,
             "%s: commit observer used during raw probe", name());
    functionalState->setWriteCommitObserver(observer);
}

bool
AxiTargetAdapter::functionalIdle() const
{
    if (rawProbe || !functionalState)
        return false;
    const auto occupancy = functionalOccupancy();
    return occupancy.writeContexts == 0 &&
           occupancy.writeReservedBeats == 0 &&
           occupancy.orphanTransactions == 0 &&
           occupancy.orphanReservedBeats == 0 &&
           occupancy.readContexts == 0 &&
           occupancy.readReservedBeats == 0 &&
           occupancy.bReady == 0 && occupancy.rReady == 0 &&
           responseDestinations.empty() &&
           !awLocal->isReady(curTick()) && !wLocal->isReady(curTick()) &&
           !arLocal->isReady(curTick());
}

void
AxiTargetAdapter::print(std::ostream &out) const
{
    out << name() << "(AxiTargetAdapter)";
}

} // namespace axi
} // namespace gem5

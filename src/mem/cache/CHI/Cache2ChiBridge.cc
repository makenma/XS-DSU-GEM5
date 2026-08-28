#include "mem/cache/CHI/Cache2ChiBridge.hh"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "base/intmath.hh"
#include "base/logging.hh"
#include "base/output.hh"
#include "base/trace.hh"
#include "debug/Cache2ChiBridge.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"
#include "mem/cache/CHI/base/SnoopOpcode.hh"
#include "mem/packet.hh"
#include "mem/request.hh"
#include "sim/eventq.hh"
#include "sim/serialize.hh"
#include "sim/sim_object.hh"

namespace gem5
{

namespace Chi
{

namespace
{

namespace ReqOp
{
constexpr uint8_t ReadShared = 0x01;
constexpr uint8_t ReadUnique = 0x07;
constexpr uint8_t CleanInvalid = 0x09;
constexpr uint8_t MakeInvalid = 0x0a;
constexpr uint8_t MakeUnique = 0x0c;
constexpr uint8_t Evict = 0x0d;
constexpr uint8_t WriteEvictFull = 0x55;
constexpr uint8_t WriteCleanFull = 0x57;
constexpr uint8_t WriteUniquePtl = 0x58;
constexpr uint8_t WriteUniqueFull = 0x59;
constexpr uint8_t WriteBackFull = 0x5b;
} // namespace ReqOp

namespace RspOp
{
constexpr uint8_t SnpResp = 0x01;
constexpr uint8_t CompAck = 0x02;
} // namespace RspOp

namespace DatOp
{
constexpr uint8_t SnpRespData = 0x01;
constexpr uint8_t CopyBackWriteData = 0x02;
constexpr uint8_t NonCopyBackWriteData = 0x03;
constexpr uint8_t CompData = 0x04;
} // namespace DatOp

bool
isDirtyResp(uint8_t resp)
{
    return resp == 3 || resp == 4 || resp == 5;
}

} // anonymous namespace

Cache2ChiBridge::Cache2ChiBridge(const Cache2ChiBridgeParams& p)
    : ClockedObject(p),
      ruby::Consumer(this),
      cachePort(csprintf("%s.cache_side", name()), *this),
      chiPort(csprintf("%s.chi_side", name()), static_cast<ruby::Consumer*>(this),
              /*id*/ 0),
      memPort(p.name + ".mem_side", this),
      nodeId(p.node_id),
      homeNodeId(p.home_node_id),
      homeNodeIds(p.home_node_ids.begin(), p.home_node_ids.end()),
      hnfHashPaBits(p.hnf_hash_pa_bits),
      txnIdBase(p.txnid_base),
      txnIdNamespace(p.txnid_namespace_id),
      txnIdNamespaceCount(p.txnid_namespace_count),
      maxTxns(p.num_txns),
      blockSize(p.block_size),
      dataBeatBytes(p.data_beat_bytes),
      enableRetry(p.enable_retry),
      sinkHnfTxReq(p.sink_hnf_txreq),
      transactionLatencyTraceFile(p.transaction_latency_trace_file),
      pumpEvent([this]{ pump(); }, name() + ".pumpEvent")
{
    fatal_if(blockSize == 0 || !isPowerOf2(blockSize),
             "%s requires a non-zero power-of-two block_size\n", name());
    fatal_if(dataBeatBytes == 0 || dataBeatBytes > blockSize,
             "%s requires 0 < data_beat_bytes <= block_size\n", name());
    fatal_if(1 + (blockSize - 1) / dataBeatBytes > UINT8_MAX + 1,
             "%s block requires more than the 8-bit DataID space\n", name());
    fatal_if(maxTxns == 0, "%s requires num_txns > 0\n", name());
    fatal_if(hnfHashPaBits < 6 || hnfHashPaBits > 64,
             "%s hnf_hash_pa_bits=%u must be in [6, 64]\n", name(),
             hnfHashPaBits);
    fatal_if(!homeNodeIds.empty() &&
                 (homeNodeIds.size() > 64 ||
                  !isPowerOf2(homeNodeIds.size())),
             "%s home_node_ids count=%llu must be a power of two <= 64\n",
             name(),
             static_cast<unsigned long long>(homeNodeIds.size()));
    if (!homeNodeIds.empty()) {
        std::unordered_set<uint32_t> uniqueTargets(
            homeNodeIds.begin(), homeNodeIds.end());
        fatal_if(uniqueTargets.size() != homeNodeIds.size(),
                 "%s home_node_ids must contain unique target IDs\n", name());
    }
    fatal_if(txnIdNamespaceCount == 0 ||
                 txnIdNamespace >= txnIdNamespaceCount,
             "%s requires txnid_namespace_id=%u < "
             "txnid_namespace_count=%u\n",
             name(), txnIdNamespace, txnIdNamespaceCount);
    nextTxnId = firstTxnIdInNamespace(
        txnIdBase, txnIdNamespace, txnIdNamespaceCount);
    const uint64_t max_txnid = std::numeric_limits<uint32_t>::max();
    const uint64_t namespace_capacity = nextTxnId <= max_txnid ?
        1 + (max_txnid - nextTxnId) / txnIdNamespaceCount : 0;
    fatal_if(maxTxns > namespace_capacity,
             "%s TxnID namespace %u/%u above base=%u cannot represent "
             "num_txns=%u simultaneous transactions\n",
             name(), txnIdNamespace, txnIdNamespaceCount, txnIdBase,
             maxTxns);
}

namespace
{

const char*
reqOpcodeName(uint8_t opcode)
{
    switch (opcode) {
      case ReqOp::ReadShared: return "ReadShared";
      case ReqOp::ReadUnique: return "ReadUnique";
      case ReqOp::CleanInvalid: return "CleanInvalid";
      case ReqOp::MakeInvalid: return "MakeInvalid";
      case ReqOp::MakeUnique: return "MakeUnique";
      case ReqOp::Evict: return "Evict";
      case ReqOp::WriteEvictFull: return "WriteEvictFull";
      case ReqOp::WriteCleanFull: return "WriteCleanFull";
      case ReqOp::WriteUniquePtl: return "WriteUniquePtl";
      case ReqOp::WriteUniqueFull: return "WriteUniqueFull";
      case ReqOp::WriteBackFull: return "WriteBackFull";
      default: return "ReservedOrUnsupportedReqOp";
    }
}

const char*
snpOpcodeName(uint8_t opcode)
{
    switch (decodeSnp(opcode).minor) {
      case SnpMinor::SnpLCrdReturn: return "SnpLCrdReturn";
      case SnpMinor::SnpShared: return "SnpShared";
      case SnpMinor::SnpClean: return "SnpClean";
      case SnpMinor::SnpOnce: return "SnpOnce";
      case SnpMinor::SnpNotSharedDirty: return "SnpNotSharedDirty";
      case SnpMinor::SnpUnique: return "SnpUnique";
      case SnpMinor::SnpCleanShared: return "SnpCleanShared";
      case SnpMinor::SnpCleanInvalid: return "SnpCleanInvalid";
      case SnpMinor::SnpMakeInvalid: return "SnpMakeInvalid";
      case SnpMinor::SnpPreferUnique: return "SnpPreferUnique";
      case SnpMinor::SnpUniqueStash: return "SnpUniqueStash";
      case SnpMinor::SnpMakeInvalidStash: return "SnpMakeInvalidStash";
      case SnpMinor::SnpStashUnique: return "SnpStashUnique";
      case SnpMinor::SnpStashShared: return "SnpStashShared";
      case SnpMinor::SnpDVMOp: return "SnpDVMOp";
      case SnpMinor::SnpQuery: return "SnpQuery";
      case SnpMinor::SnpSharedFwd: return "SnpSharedFwd";
      case SnpMinor::SnpCleanFwd: return "SnpCleanFwd";
      case SnpMinor::SnpOnceFwd: return "SnpOnceFwd";
      case SnpMinor::SnpNotSharedDirtyFwd: return "SnpNotSharedDirtyFwd";
      case SnpMinor::SnpPreferUniqueFwd: return "SnpPreferUniqueFwd";
      case SnpMinor::SnpUniqueFwd: return "SnpUniqueFwd";
      default: return "ReservedOrUnsupportedSnpOp";
    }
}

const char*
rspOpcodeName(uint8_t opcode)
{
    switch (decodeRsp(opcode).minor) {
      case RspMinor::RespLCrdReturn: return "RespLCrdReturn";
      case RspMinor::SnpResp: return "SnpResp";
      case RspMinor::CompAck: return "CompAck";
      case RspMinor::RetryAck: return "RetryAck";
      case RspMinor::Comp: return "Comp";
      case RspMinor::CompDBIDResp: return "CompDBIDResp";
      case RspMinor::DBIDResp: return "DBIDResp";
      case RspMinor::PCrdGrant: return "PCrdGrant";
      case RspMinor::ReadReceipt: return "ReadReceipt";
      case RspMinor::SnpRespFwded: return "SnpRespFwded";
      case RspMinor::TagMatch: return "TagMatch";
      case RspMinor::RespSepData: return "RespSepData";
      case RspMinor::Persist: return "Persist";
      case RspMinor::CompPersist: return "CompPersist";
      case RspMinor::DBIDRespOrd: return "DBIDRespOrd";
      case RspMinor::StashDone: return "StashDone";
      case RspMinor::CompStashDone: return "CompStashDone";
      case RspMinor::CompCMO: return "CompCMO";
      default: return "ReservedOrUnsupportedRspOp";
    }
}

const char*
datOpcodeName(uint8_t opcode)
{
    switch (decodeDat(opcode).minor) {
      case DatMinor::DataLCrdReturn: return "DataLCrdReturn";
      case DatMinor::SnpRespData: return "SnpRespData";
      case DatMinor::SnpRespDataPtl: return "SnpRespDataPtl";
      case DatMinor::SnpRespDataFwded: return "SnpRespDataFwded";
      case DatMinor::CopyBackWriteData: return "CopyBackWriteData";
      case DatMinor::NonCopyBackWriteData: return "NonCopyBackWriteData";
      case DatMinor::CompData: return "CompData";
      case DatMinor::DataSepResp: return "DataSepResp";
      case DatMinor::NCBWrDataCompAck: return "NCBWrDataCompAck";
      case DatMinor::WriteDataCancel: return "WriteDataCancel";
      default: return "ReservedOrUnsupportedDatOp";
    }
}

const char*
channelName(ChannelType channel)
{
    switch (channel) {
      case REQ: return "REQ";
      case RSP: return "RSP";
      case DAT: return "DAT";
      case SNP: return "SNP";
      default: return "NONE";
    }
}

const char*
opcodeName(ChannelType channel, uint8_t opcode)
{
    switch (channel) {
      case REQ: return reqOpcodeName(opcode);
      case RSP: return rspOpcodeName(opcode);
      case DAT: return datOpcodeName(opcode);
      case SNP: return snpOpcodeName(opcode);
      default: return "CensoredAtRoiEnd";
    }
}

} // anonymous namespace

void
Cache2ChiBridge::startTransactionLatencyTrace()
{
    fatal_if(transactionLatencyTraceFile.empty(),
             "%s cannot start RNF latency trace without an output file\n",
             name());
    fatal_if(transactionLatencyTraceEnabled,
             "%s RNF latency trace was started twice\n", name());
    transactionLatencyRecords.clear();
    transactionLatencyTraceStartTick = curTick();
    transactionLatencyTraceStopTick = 0;
    transactionLatencyTraceEnabled = true;
}

void
Cache2ChiBridge::stopTransactionLatencyTrace()
{
    fatal_if(!transactionLatencyTraceEnabled,
             "%s RNF latency trace was stopped while inactive\n", name());
    transactionLatencyTraceStopTick = curTick();
    transactionLatencyTraceEnabled = false;

    for (auto& [id, txn] : txns) {
        if (txn.latencyTracked && !txn.latencyRecorded) {
            transactionLatencyRecords.push_back({
                false, REQ, txn.req.opcode, txn.req.srcid, txn.req.tgtid,
                txn.txnid, txn.latencyStartTick,
                transactionLatencyTraceStopTick, NUM_CHANNELS, 0xff,
                txn.retryCount});
            txn.latencyRecorded = true;
        }
    }
    for (const auto& [id, snoop] : snoops) {
        if (snoop.latencyTracked) {
            transactionLatencyRecords.push_back({
                false, SNP, snoop.snp.opcode, snoop.snp.srcid,
                snoop.snp.tgtid, snoop.snp.txnid, snoop.latencyStartTick,
                transactionLatencyTraceStopTick, NUM_CHANNELS, 0xff,
                snoop.retryCount});
        }
    }
    for (const auto& snoop : pendingSnoopResponses) {
        if (snoop.latencyTracked) {
            transactionLatencyRecords.push_back({
                false, SNP, snoop.snp.opcode, snoop.snp.srcid,
                snoop.snp.tgtid, snoop.snp.txnid, snoop.latencyStartTick,
                transactionLatencyTraceStopTick, NUM_CHANNELS, 0xff,
                snoop.retryCount});
        }
    }
    std::queue<PendingSnoopRetry> retries = pendingSnoopRetries;
    while (!retries.empty()) {
        const PendingSnoopRetry& retry = retries.front();
        if (retry.latencyTracked) {
            transactionLatencyRecords.push_back({
                false, SNP, retry.snp.opcode, retry.snp.srcid,
                retry.snp.tgtid, retry.snp.txnid, retry.latencyStartTick,
                transactionLatencyTraceStopTick, NUM_CHANNELS, 0xff,
                retry.attempts});
        }
        retries.pop();
    }
    writeTransactionLatencyTrace();
}

void
Cache2ChiBridge::writeTransactionLatencyTrace()
{
    OutputStream* output = simout.create(
        transactionLatencyTraceFile, false, true);
    fatal_if(!output, "%s could not create RNF latency trace %s\n",
             name(), transactionLatencyTraceFile);
    std::ostream& stream = *output->stream();
    stream << "schema_version,rnf_name,rnf_node_id,status,"
              "transaction_channel,transaction_type,opcode_hex,source_id,"
              "target_id,transaction_id,start_tick,end_tick,latency_ticks,"
              "chi_clock_period_ticks,terminal_channel,terminal_type,"
              "terminal_opcode_hex,retry_count,trace_start_tick,"
              "trace_stop_tick\n";
    for (const TransactionLatencyRecord& record : transactionLatencyRecords) {
        const Tick latency = record.endTick - record.startTick;
        stream << "1," << name() << ',' << nodeId << ','
               << (record.completed ? "completed" : "censored_roi_end")
               << ',' << channelName(record.transactionChannel) << ','
               << opcodeName(record.transactionChannel,
                             record.transactionOpcode)
               << ",0x" << std::hex
               << static_cast<unsigned>(record.transactionOpcode)
               << std::dec << ',' << record.sourceId << ',' << record.targetId
               << ',' << record.transactionId << ',' << record.startTick << ','
               << record.endTick << ',';
        if (record.completed) {
            stream << latency;
        }
        stream << ',' << clockPeriod() << ','
               << channelName(record.terminalChannel) << ','
               << opcodeName(record.terminalChannel, record.terminalOpcode)
               << ',';
        if (record.completed) {
            stream << "0x" << std::hex
                   << static_cast<unsigned>(record.terminalOpcode)
                   << std::dec;
        }
        stream << ',' << record.retryCount << ','
               << transactionLatencyTraceStartTick << ','
               << transactionLatencyTraceStopTick << '\n';
    }
    simout.close(output);
}

uint32_t
Cache2ChiBridge::selectHomeNode(Addr address) const
{
    if (homeNodeIds.empty()) {
        return homeNodeId;
    }
    return homeNodeIds[cmnHnfIndex(
        address, homeNodeIds.size(), hnfHashPaBits)];
}

Cache2ChiBridge::CacheSidePort::CacheSidePort(
    const std::string& pname, Cache2ChiBridge& owner)
    : ResponsePort(pname, &owner), owner(owner)
{
    DPRINTF(Cache2ChiBridge, "Cache2ChiBridge constructed\n");
}

Cache2ChiBridge::MemSidePort::MemSidePort(const std::string& name,
                                          Cache2ChiBridge* owner)
    : RequestPort(name, owner), owner(owner)
{
}

void
Cache2ChiBridge::wakeup()
{
    schedulePump();
}

void
Cache2ChiBridge::print(std::ostream& out) const
{
    out << "Cache2ChiBridge(" << name() << ")";
}

Port&
Cache2ChiBridge::getPort(const std::string& if_name, PortID idx)
{
    DPRINTF(Cache2ChiBridge, "getPort(%s)\n", if_name);
    if (if_name == "cache_side") {
        return cachePort;
    }
    if (if_name == "chi_side") {
        return chiPort;
    }
    if (if_name == "mem_side") {
        return memPort;
    }

    return ClockedObject::getPort(if_name, idx);
}

void
Cache2ChiBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        schedule(pumpEvent, nextCycle());
    }
}

bool
Cache2ChiBridge::hasPumpWork() const
{
    const bool has_pending_data = std::any_of(
        txns.begin(), txns.end(), [](const auto& entry) {
            return txnDataPending(entry.second);
        });
    return (!memReqBlocked && !pendingReqPkts.empty()) ||
           (!cacheRespBlocked && !pendingRespPkts.empty()) ||
           !retryTxnIds.empty() || !pendingCompAcks.empty() ||
           !pendingSnoopRetries.empty() ||
           !pendingSnoopResponses.empty() || has_pending_data ||
           chiPort.hasTxFlit(REQ) || chiPort.hasTxFlit(RSP) ||
           chiPort.hasTxFlit(DAT) || chiPort.hasTxFlit(SNP);
}

bool
Cache2ChiBridge::completelyIdle() const
{
    // A bridge transaction owns a PacketPtr after the classic cache has
    // accepted the request.  Neither that packet nor an accepted CHI flit has
    // a stable checkpoint representation, so both the bookkeeping and the
    // port queues must reach the global drain fixed point before serialization.
    return pendingReqPkts.empty() && pendingRespPkts.empty() &&
           retryTxnIds.empty() && pendingCompAcks.empty() &&
           pendingSnoopRetries.empty() && pendingSnoopResponses.empty() &&
           txns.empty() && snoops.empty() && promotedUpgradePkts.empty() &&
           !chiPort.hasTxFlit(REQ) && !chiPort.hasTxFlit(RSP) &&
           !chiPort.hasTxFlit(DAT) && !chiPort.hasTxFlit(SNP);
}

void
Cache2ChiBridge::testDrainComplete()
{
    if (drainState() == DrainState::Draining && completelyIdle()) {
        signalDrainDone();
    }
}

DrainState
Cache2ChiBridge::drain()
{
    // Keep accepting traffic while every producer drains.  gem5 repeats the
    // global drain pass, so a bridge which was perturbed after reporting
    // Drained will be observed on the next pass.
    if (completelyIdle()) {
        return DrainState::Drained;
    }
    schedulePump();
    return DrainState::Draining;
}

std::optional<uint32_t>
Cache2ChiBridge::allocateTxnId()
{
    const auto id = allocateMonotonicTxnId(
        nextTxnId, txns.size(), maxTxns, txnIdNamespaceCount);
    fatal_if(!id && txns.size() < maxTxns,
             "%s exhausted monotonic 32-bit CHI TxnID namespace %u/%u; "
             "refusing to wrap because an older cross-channel flit may "
             "still carry a retired ID\n",
             name(), txnIdNamespace, txnIdNamespaceCount);
    panic_if(id && txns.count(*id),
             "%s monotonic allocator produced active txnid=%u\n",
             name(), id ? *id : 0);
    return id;
}

void
Cache2ChiBridge::freeTxn(uint32_t txnid)
{
    txns.erase(txnid);
}

uint32_t
Cache2ChiBridge::allocateSnoopTxnId()
{
    const uint32_t id = nextSnoopTxnId++;
    if (nextSnoopTxnId == 0) {
        nextSnoopTxnId = 1;
    }
    return id;
}

Cache2ChiBridge::MemoryIntent
Cache2ChiBridge::classify(PacketPtr pkt) const
{
    const MemCmd cmd = pkt->cmd;

    // A classic crossbar keeps forwarding a writable request after an
    // upper cache has promised the data.  The forwarded packet is an
    // express snoop used only to invalidate copies below that responder;
    // the crossbar deliberately does not install a route for a downstream
    // response.  At the CHI boundary this is therefore an ownership-only
    // MakeUnique transaction, not another data request.
    if (pkt->cacheResponding()) {
        panic_if(!pkt->isExpressSnoop() || !pkt->needsWritable() ||
                     pkt->responderHadWritable(),
                 "%s: invalid cache-responding coordination packet %s\n",
                 name(), pkt->print());
        return cacheRespondingCoordinationIntent();
    }

    MemoryIntent intent{};
    intent.needsResponse = pkt->needsResponse();

    if (cmd == MemCmd::SCUpgradeFailReq) {
        return failedScRefillIntent(intent.needsResponse);
    }
    if (cmd == MemCmd::LockedRMWReadReq ||
        cmd == MemCmd::LockedRMWWriteReq) {
        panic("%s: LockedRMW* requires CHI Atomic/exclusive locking support; "
              "v1 refuses to map it to ReadUnique\n", name());
    }

    if (cmd == MemCmd::ReadReq || cmd == MemCmd::ReadSharedReq) {
        intent.kind = IntentKind::ReadShared;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        return intent;
    }

    if (cmd == MemCmd::ReadCleanReq) {
        intent.kind = IntentKind::ReadShared;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        intent.forceCleanResponse = true;
        return intent;
    }

    if (cmd == MemCmd::ReadExReq) {
        intent.kind = IntentKind::ReadUnique;
        intent.txnClass = TxnClass::Read;
        intent.expectsData = true;
        intent.expectsComp = true;
        intent.requiresCompAck = true;
        return intent;
    }

    if (cmd == MemCmd::UpgradeReq || cmd == MemCmd::SCUpgradeReq) {
        intent.kind = IntentKind::MakeUnique;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::InvalidateReq) {
        intent.kind = IntentKind::MakeInvalid;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::CleanInvalidReq) {
        intent.kind = IntentKind::CleanInvalid;
        intent.txnClass = TxnClass::Maintenance;
        intent.expectsComp = true;
        return intent;
    }

    if (cmd == MemCmd::WriteReq || cmd == MemCmd::WriteLineReq) {
        panic_if(!pkt->isWholeLineWrite(blockSize),
                 "%s: partial coherent write addr=%#llx bytes=%u is disabled "
                 "until HNF models a masked RMW/no-allocate path\n",
                 name(), static_cast<unsigned long long>(pkt->getAddr()),
                 pkt->getSize());
        intent.kind = IntentKind::WriteUniqueFull;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        intent.isPartial = false;
        return intent;
    }

    if (cmd == MemCmd::WritebackDirty) {
        intent.kind = IntentKind::WriteBackFull;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        return intent;
    }

    if (cmd == MemCmd::WritebackClean) {
        intent.kind = pkt->hasSharers() ?
            IntentKind::Evict : IntentKind::WriteEvictFull;
        intent.txnClass = pkt->hasSharers() ? TxnClass::Evict : TxnClass::Write;
        intent.expectsComp = true;
        intent.expectsDbid = !pkt->hasSharers();
        intent.carriesData = !pkt->hasSharers();
        return intent;
    }

    if (cmd == MemCmd::WriteClean) {
        intent.kind = IntentKind::WriteCleanFull;
        intent.txnClass = TxnClass::Write;
        intent.expectsDbid = true;
        intent.expectsComp = true;
        intent.carriesData = true;
        return intent;
    }

    if (cmd == MemCmd::CleanEvict) {
        intent.kind = IntentKind::Evict;
        intent.txnClass = TxnClass::Evict;
        intent.expectsComp = true;
        return intent;
    }

    panic("%s: unsupported classic packet command %s for conservative CHI "
          "translation\n", name(), pkt->cmdString().c_str());
}

RawReq
Cache2ChiBridge::mapToReq(const MemoryIntent& intent, PacketPtr pkt,
                          uint32_t txnid) const
{
    RawReq req{};
    req.addr = pkt->getBlockAddr(blockSize);
    req.size = static_cast<uint8_t>(pkt->getSize());
    req.qos = pkt->qosValue();
    req.srcid = nodeId;
    req.tgtid = selectHomeNode(req.addr);
    req.txnid = txnid;
    req.stage = 0;
    req.AllowRetry = enableRetry ? 1 : 0;
    req.ReturnNid = nodeId;

    switch (intent.kind) {
      case IntentKind::ReadShared:
      case IntentKind::ReadSharedForceClean:
        req.opcode = ReqOp::ReadShared;
        break;
      case IntentKind::ReadUnique:
        req.opcode = ReqOp::ReadUnique;
        break;
      case IntentKind::MakeUnique:
        req.opcode = ReqOp::MakeUnique;
        break;
      case IntentKind::MakeInvalid:
        req.opcode = ReqOp::MakeInvalid;
        break;
      case IntentKind::CleanInvalid:
        req.opcode = ReqOp::CleanInvalid;
        break;
      case IntentKind::WriteUniqueFull:
        req.opcode = ReqOp::WriteUniqueFull;
        break;
      case IntentKind::WriteUniquePtl:
        req.opcode = ReqOp::WriteUniquePtl;
        req.addr = pkt->getAddr();
        break;
      case IntentKind::WriteBackFull:
        req.opcode = ReqOp::WriteBackFull;
        break;
      case IntentKind::WriteCleanFull:
        req.opcode = ReqOp::WriteCleanFull;
        break;
      case IntentKind::WriteEvictFull:
        req.opcode = ReqOp::WriteEvictFull;
        break;
      case IntentKind::Evict:
        req.opcode = ReqOp::Evict;
        break;
    }

    return req;
}

std::vector<RawDat>
Cache2ChiBridge::packDataBeats(const MemoryIntent& intent, PacketPtr pkt,
                               const RawReq& req) const
{
    std::vector<RawDat> beats;
    if (!intent.carriesData) {
        return beats;
    }

    panic_if(!pkt->hasData(), "%s: %s carries CHI data but Packet has no data\n",
             name(), pkt->cmdString().c_str());

    DPRINTF(Cache2ChiBridge,
            "pack classic data txnid=%u cmd=%s addr=%#llx bytes=%u\n",
            req.txnid, pkt->cmdString().c_str(),
            static_cast<unsigned long long>(pkt->getAddr()), pkt->getSize());
    const uint8_t* src = pkt->getConstPtr<uint8_t>();
    const uint32_t pktSize = pkt->getSize();
    const uint32_t startOffset = pkt->getOffset(blockSize);
    const uint32_t opcode = intent.txnClass == TxnClass::Write &&
        (intent.kind == IntentKind::WriteUniqueFull ||
         intent.kind == IntentKind::WriteUniquePtl) ?
        DatOp::NonCopyBackWriteData : DatOp::CopyBackWriteData;

    for (uint32_t copied = 0, dataid = 0; copied < pktSize;
         copied += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, pktSize - copied);
        RawDat dat{};
        dat.qos = pkt->qosValue();
        dat.srcid = nodeId;
        dat.tgtid = req.tgtid;
        dat.txnid = req.txnid;
        dat.opcode = static_cast<uint8_t>(opcode);
        dat.stage = 0;
        dat.last = (copied + beatBytes) == pktSize;
        dat.HomeNID = req.tgtid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = static_cast<uint8_t>(RespState::Unknown);
        dat.beatOffset = intent.isPartial ? startOffset + copied : copied;
        dat.data.assign(src + copied, src + copied + beatBytes);
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);
        beats.push_back(std::move(dat));
    }

    return beats;
}

bool
Cache2ChiBridge::cacheRecvTimingReq(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge,
        "cacheRecvTimingReq: cmd=%s addr=0x%lx size=%u\n",
        pkt->cmdString().c_str(),
        static_cast<unsigned long>(pkt->getAddr()),
        pkt->getSize());
    if (shouldPromoteUpgrade(pkt->cmd, pkt->cacheResponding())) {
        DPRINTF(Cache2ChiBridge,
                "promote %s to ReadExReq so a retried/invalidation-raced "
                "upgrade can refill data\n",
                pkt->cmdString().c_str());
        pkt->cmd = MemCmd::ReadExReq;
        pkt->allocate();
        promotedUpgradePkts.insert(pkt);
    } else if (pkt->cacheResponding()) {
        DPRINTF(Cache2ChiBridge,
                "cache responder already supplies cmd=%s addr=%#lx; "
                "forward as ownership-only CHI coordination\n",
                pkt->cmdString().c_str(),
                static_cast<unsigned long>(pkt->getAddr()));
    }
    pendingReqPkts.push(pkt);
    schedulePump();
    return true;
}

Tick
Cache2ChiBridge::cacheRecvAtomic(PacketPtr pkt)
{
    panic("%s: atomic access is not supported by Cache2ChiBridge RNF v1\n",
          name());
}

void
Cache2ChiBridge::cacheRecvFunctional(PacketPtr pkt)
{
    memPort.sendFunctional(pkt);
}

bool
Cache2ChiBridge::cacheRecvTimingSnoopResp(PacketPtr pkt)
{
    auto* state = pkt->findNextSenderState<SnoopSenderState>();
    panic_if(!state, "%s: got classic snoop response without bridge state\n",
             name());

    auto it = snoops.find(state->txnid);
    panic_if(it == snoops.end(),
             "%s: got classic snoop response for unknown snoop txn %u\n",
             name(), state->txnid);

    SnoopEntry& snoop = it->second;
    if (pkt->hasData()) {
        sendSnoopData(snoop, pkt);
    } else {
        const RespState state = pkt->hasSharers() && !snoop.invalidating ?
            RespState::SC : RespState::I;
        sendSnoopRsp(snoop, state);
        DPRINTF(Cache2ChiBridge,
                "classic snoop response txnid=%u cmd=%s has no data; "
                "sending SnpResp state=%u\n",
                snoop.txnid, pkt->cmdString().c_str(),
                static_cast<unsigned>(state));
    }

    PacketPtr original_snoop_pkt = snoop.snoopPkt;
    snoop.snoopPkt = nullptr;
    snoop.pendingData = false;
    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;

    if (original_snoop_pkt && original_snoop_pkt != pkt) {
        delete original_snoop_pkt;
    }
    SnoopEntry pending = std::move(snoop);
    snoops.erase(it);
    queuePendingSnoopResponse(std::move(pending));
    schedulePump();
    return true;
}

void
Cache2ChiBridge::cacheRecvRespRetry()
{
    panic_if(!cacheRespBlocked,
             "%s: got classic response retry while not blocked\n", name());
    cacheRespBlocked = false;
    schedulePump();
}

AddrRangeList
Cache2ChiBridge::cacheGetAddrRanges() const
{
    return memPort.getAddrRanges();
}

void
Cache2ChiBridge::pump()
{
    if (!pendingSnoopRetries.empty()) {
        PendingSnoopRetry retry = pendingSnoopRetries.front();
        pendingSnoopRetries.pop();
        handleSnp(retry.snp, retry.attempts, retry.latencyStartTick,
                  retry.latencyTracked);
    }

    drainChiTx();
    sendPendingSnoopResponses();
    sendPendingResponses();
    sendPendingCompAcks();

    // A DBID response can arrive when the DAT channel has space for only a
    // prefix of a multi-beat write.  Retry every such transaction from its
    // saved nextDataBeat; merely rescheduling the pump is not sufficient
    // because handleRsp() will not run again for the same DBID response.
    std::vector<uint32_t> pending_data_txns;
    for (const auto& [txnid, txn] : txns) {
        if (txn.hasDbid && txn.nextDataBeat < txn.dataBeats.size()) {
            pending_data_txns.push_back(txnid);
        }
    }
    for (const uint32_t txnid : pending_data_txns) {
        auto it = txns.find(txnid);
        if (it == txns.end() || !sendTxnData(it->second)) {
            continue;
        }
        maybeComplete(it->second);
    }

    while (!retryTxnIds.empty()) {
        const uint32_t txnid = retryTxnIds.front();
        if (!reissueRetriedTxn(txnid)) {
            break;
        }
        retryTxnIds.pop();
    }

    while (!pendingReqPkts.empty()) {
        PacketPtr pkt = pendingReqPkts.front();
        if (pkt->req->isUncacheable()) {
            if (!advanceUncacheableBypass(
                    pkt, memReqBlocked,
                    [this](PacketPtr pending) {
                        return memPort.sendTimingReq(pending);
                    })) {
                DPRINTF(Cache2ChiBridge,
                        "uncacheable classic bypass blocked cmd=%s "
                        "addr=%#llx bytes=%u\n",
                        pkt->cmdString().c_str(),
                        static_cast<unsigned long long>(pkt->getAddr()),
                        pkt->getSize());
                break;
            }
            DPRINTF(Cache2ChiBridge,
                    "uncacheable classic bypass sent cmd=%s addr=%#llx "
                    "bytes=%u\n",
                    pkt->cmdString().c_str(),
                    static_cast<unsigned long long>(pkt->getAddr()),
                    pkt->getSize());
            pendingReqPkts.pop();
            continue;
        }

        MemoryIntent intent = classify(pkt);
        auto txnid = allocateTxnId();
        if (!txnid) {
            break;
        }

        TxnEntry txn{};
        txn.txnid = *txnid;
        txn.pkt = pkt;
        txn.intent = intent;
        txn.intent.respondAsUpgrade = promotedUpgradePkts.count(pkt) != 0;
        txn.req = mapToReq(intent, pkt, *txnid);
        txn.dataBeats = packDataBeats(intent, pkt, txn.req);
        txn.readExpectedBytes = intent.expectsData ? pkt->getSize() : 0;

        DPRINTF(Cache2ChiBridge,
                "enqueue CHI REQ opcode=0x%x txnid=%u cmd=%s addr=0x%lx\n",
                txn.req.opcode, txn.txnid, pkt->cmdString().c_str(),
                static_cast<unsigned long>(txn.req.addr));

        if (!chiPort.enqueueRx(REQ, txn.req)) {
            break;
        }

        if (transactionLatencyTraceEnabled) {
            txn.latencyStartTick = curTick();
            txn.latencyTracked = true;
        }

        promotedUpgradePkts.erase(pkt);
        pendingReqPkts.pop();
        txns.emplace(txn.txnid, std::move(txn));
    }

    drainChiTx();
    sendPendingSnoopResponses();
    sendPendingResponses();
    sendPendingCompAcks();

    if (hasPumpWork()) {
        schedulePump();
    }
    testDrainComplete();
}

void
Cache2ChiBridge::drainChiTx()
{
    bool madeProgress = true;
    while (madeProgress) {
        madeProgress = false;
        if (auto flit = chiPort.getTxFlit(REQ)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawReq>) {
                    handleTxReq(f);
                } else {
                    panic("%s: non-REQ flit on REQ channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(RSP)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawRsp>) {
                    handleRsp(f);
                } else {
                    panic("%s: non-RSP flit on RSP channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(DAT)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawDat>) {
                    handleDat(f);
                } else {
                    panic("%s: non-DAT flit on DAT channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
        if (auto flit = chiPort.getTxFlit(SNP)) {
            std::visit([this](auto&& f) {
                using T = std::decay_t<decltype(f)>;
                if constexpr (std::is_same_v<T, RawSnp>) {
                    handleSnp(f);
                } else {
                    panic("%s: non-SNP flit on SNP channel\n", name());
                }
            }, *flit);
            madeProgress = true;
        }
    }
}

void
Cache2ChiBridge::handleTxReq(const RawReq& req)
{
    if (!sinkHnfTxReq) {
        panic("%s: received HNF TXREQ opcode=0x%x while sink_hnf_txreq is "
              "disabled\n",
              name(), req.opcode);
    }

    const auto decoded = decodeReq(req.opcode);
    if (decoded.minor != ReqMinor::ReadNoSnp) {
        warn("%s: direct SN sink dropping unsupported HNF TXREQ opcode=0x%x "
             "src=%u tgt=%u txn=%u addr=%#llx\n",
             name(), req.opcode, req.srcid, req.tgtid, req.txnid,
             static_cast<unsigned long long>(req.addr));
        return;
    }

    DPRINTF(Cache2ChiBridge,
            "direct SN sink accepted HNF TXREQ ReadNoSnp src=%u tgt=%u "
            "txn=%u addr=%#llx size=%u\n",
            req.srcid, req.tgtid, req.txnid,
            static_cast<unsigned long long>(req.addr), req.size);

    const uint32_t bytes = req.size ? req.size : blockSize;
    RequestPtr request = std::make_shared<Request>(
        req.addr, bytes, 0, Request::funcRequestorId);
    PacketPtr pkt = new Packet(request, MemCmd::ReadReq);
    pkt->allocate();
    memPort.sendFunctional(pkt);

    const uint8_t* data = pkt->getConstPtr<uint8_t>();
    for (uint32_t offset = 0, dataid = 0; offset < bytes;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beatBytes = std::min(dataBeatBytes, bytes - offset);
        RawDat dat{};
        dat.qos = req.qos;
        dat.srcid = req.tgtid;
        dat.tgtid = req.srcid;
        dat.txnid = req.txnid;
        dat.opcode = DatOp::CompData;
        dat.last = (offset + beatBytes) >= bytes;
        dat.HomeNID = req.ReturnNid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = static_cast<uint8_t>(RespState::SC);
        dat.beatOffset = offset;
        dat.data.assign(data + offset, data + offset + beatBytes);
        dat.byteEnable.assign(beatBytes, 1);
        dat.chunkValid.assign((beatBytes + 7) / 8, 1);

        panic_if(!chiPort.enqueueRx(DAT, dat),
                 "%s: direct SN sink failed to return CompData txn=%u "
                 "dataid=%u\n",
                 name(), req.txnid, dat.dataid);
        DPRINTF(Cache2ChiBridge,
                "direct SN sink returns CompData txn=%u dataid=%u "
                "offset=%u bytes=%u last=%u\n",
                req.txnid, dat.dataid, offset, beatBytes, dat.last);
    }

    delete pkt;
}

void
Cache2ChiBridge::handleRsp(const RawRsp& rsp)
{
    const auto decoded = decodeRsp(rsp.opcode);
    auto it = txns.find(rsp.txnid);
    panic_if(it == txns.end(), "%s: RSP opcode=0x%x for unknown txnid=%u\n",
             name(), rsp.opcode, rsp.txnid);
    TxnEntry& txn = it->second;

    switch (decoded.minor) {
      case RspMinor::RetryAck:
        txn.retryBlocked = true;
        ++txn.retryCount;
        break;
      case RspMinor::PCrdGrant:
        if (txn.retryBlocked) {
            txn.req.AllowRetry = 0;
            txn.req.pcrdtype = rsp.pcrdtype;
            retryTxnIds.push(txn.txnid);
        }
        break;
      case RspMinor::DBIDResp:
      case RspMinor::DBIDRespOrd:
        txn.hasDbid = true;
        txn.dbid = rsp.dbid;
        sendTxnData(txn);
        break;
      case RspMinor::CompDBIDResp:
        txn.hasDbid = true;
        txn.dbid = rsp.dbid;
        txn.gotComp = true;
        txn.lastResponseChannel = RSP;
        txn.lastResponseOpcode = rsp.opcode;
        sendTxnData(txn);
        maybeComplete(txn);
        break;
      case RspMinor::Comp:
      case RspMinor::RespSepData:
      case RspMinor::CompPersist:
      case RspMinor::CompCMO:
        if (txn.intent.forceCleanResponse && isDirtyResp(rsp.resp)) {
            panic("%s: ReadCleanReq mapped as ReadShared got dirty CHI "
                  "response state %u\n", name(), rsp.resp);
        }
        txn.gotComp = true;
        txn.lastResponseChannel = RSP;
        txn.lastResponseOpcode = rsp.opcode;
        maybeComplete(txn);
        break;
      case RspMinor::ReadReceipt:
        break;
      default:
        warn("%s: ignoring unsupported RSP opcode=0x%x txnid=%u\n",
             name(), rsp.opcode, rsp.txnid);
        break;
    }
}

void
Cache2ChiBridge::handleDat(const RawDat& dat)
{
    auto it = txns.find(dat.txnid);
    panic_if(it == txns.end(), "%s: DAT opcode=0x%x for unknown txnid=%u\n",
             name(), dat.opcode, dat.txnid);
    TxnEntry& txn = it->second;

    acceptReadDataBeat(txn, dat, dataBeatBytes, name().c_str());
    txn.lastResponseChannel = DAT;
    txn.lastResponseOpcode = dat.opcode;
    if (dat.resp == static_cast<uint8_t>(RespState::SC) ||
        dat.resp == static_cast<uint8_t>(RespState::SD_PD)) {
        txn.pkt->setHasSharers();
    } else if (dat.resp == static_cast<uint8_t>(RespState::UD_PD) &&
               !txn.pkt->cacheResponding()) {
        txn.pkt->setCacheResponding();
    }
    DPRINTF(Cache2ChiBridge,
            "handle DAT opcode=0x%x txnid=%u dataid=%u offset=%u "
            "bytes=%u last=%u readBytes=%u/%u\n",
            dat.opcode, dat.txnid, dat.dataid, dat.beatOffset,
            static_cast<unsigned>(dat.data.size()), dat.last,
            txn.readDataBytes, txn.readExpectedBytes);
    maybeComplete(txn);
}

void
Cache2ChiBridge::handleSnp(const RawSnp& snp, uint32_t attempts,
                           Tick latency_start_tick, bool latency_tracked)
{
    if (attempts == 0 && transactionLatencyTraceEnabled) {
        latency_start_tick = curTick();
        latency_tracked = true;
    }
    if (respondFromPendingCopyback(
            snp, latency_start_tick, latency_tracked, attempts)) {
        return;
    }

    const uint32_t snoopTxn = allocateSnoopTxnId();
    auto req = std::make_shared<Request>(
        snp.addr & ~(static_cast<Addr>(blockSize) - 1),
        blockSize, 0, Request::funcRequestorId);
    PacketPtr pkt = new Packet(req, snoopCmdFor(snp), blockSize, snoopTxn);
    pkt->allocate();
    pkt->pushSenderState(new SnoopSenderState(snoopTxn));
    pkt->setExpressSnoop();
    if (snoopPrecedesPendingTxn(snp)) {
        pkt->setSnoopPrecedesMshr();
        DPRINTF(Cache2ChiBridge,
                "snoop txnid=%u addr=%#llx precedes pending outbound txn\n",
                snp.txnid, static_cast<unsigned long long>(snp.addr));
    }

    SnoopEntry snoop{};
    snoop.txnid = snoopTxn;
    snoop.snp = snp;
    snoop.snoopPkt = pkt;
    snoop.invalidating = snoopInvalidates(snp);
    snoop.latencyStartTick = latency_start_tick;
    snoop.latencyTracked = latency_tracked;
    snoop.retryCount = attempts;
    snoops.emplace(snoopTxn, snoop);

    cachePort.sendTimingSnoopReq(pkt);

    auto it = snoops.find(snoopTxn);
    if (it == snoops.end()) {
        return;
    }
    SnoopEntry& entry = it->second;
    if (pkt->cacheResponding()) {
        entry.pendingData = true;
        return;
    }

    constexpr uint32_t maxTransientSnoopRetries = 32;
    if (pkt->hasSharers() && attempts < maxTransientSnoopRetries) {
        DPRINTF(Cache2ChiBridge,
                "retry transient snoop txnid=%u addr=%#llx attempt=%u\n",
                snp.txnid, static_cast<unsigned long long>(snp.addr),
                attempts + 1);
        pendingSnoopRetries.push(
            {snp, attempts + 1, latency_start_tick, latency_tracked});

        Packet::SenderState* popped = pkt->popSenderState();
        delete popped;
        delete pkt;
        snoops.erase(it);
        return;
    }

    const RespState state = pkt->hasSharers() && !entry.invalidating ?
        RespState::SC : RespState::I;
    sendSnoopRsp(entry, state);

    PacketPtr original_snoop_pkt = entry.snoopPkt;
    entry.snoopPkt = nullptr;
    Packet::SenderState* popped = pkt->popSenderState();
    delete popped;
    delete pkt;
    if (original_snoop_pkt && original_snoop_pkt != pkt) {
        delete original_snoop_pkt;
    }
    SnoopEntry pending = std::move(entry);
    snoops.erase(it);
    queuePendingSnoopResponse(std::move(pending));
}

bool
Cache2ChiBridge::respondFromPendingCopyback(
    const RawSnp& snp, Tick latency_start_tick, bool latency_tracked,
    uint32_t retry_count)
{
    const Addr snoopAddr =
        snp.addr & ~(static_cast<Addr>(blockSize) - 1);
    auto respond = [this, &snp, snoopAddr, latency_start_tick,
                    latency_tracked, retry_count](
                       PacketPtr pkt, const char* source, uint32_t txnid) {
        SnoopEntry buffered{};
        buffered.snp = snp;
        buffered.invalidating = snoopInvalidates(snp);
        buffered.latencyStartTick = latency_start_tick;
        buffered.latencyTracked = latency_tracked;
        buffered.retryCount = retry_count;
        sendSnoopData(buffered, pkt);
        DPRINTF(Cache2ChiBridge,
                "%s copyback txnid=%u supplies snoop txnid=%u "
                "opcode=0x%x addr=%#llx invalidating=%u\n",
                source, txnid, snp.txnid, snp.opcode,
                static_cast<unsigned long long>(snoopAddr),
                buffered.invalidating);
        queuePendingSnoopResponse(std::move(buffered));
    };

    for (auto& [txnid, txn] : txns) {
        const bool copyback =
            txn.intent.kind == IntentKind::WriteBackFull ||
            txn.intent.kind == IntentKind::WriteEvictFull;
        if (!copyback || txn.completed || !txn.pkt ||
            !txn.pkt->hasData() ||
            txn.pkt->getBlockAddr(blockSize) != snoopAddr) {
            continue;
        }

        respond(txn.pkt, "active", txnid);
        return true;
    }

    std::queue<PacketPtr> pending = pendingReqPkts;
    while (!pending.empty()) {
        PacketPtr pkt = pending.front();
        pending.pop();
        if (pkt->req->isUncacheable() || !pkt->hasData() ||
            pkt->getBlockAddr(blockSize) != snoopAddr) {
            continue;
        }

        const MemoryIntent intent = classify(pkt);
        if (intent.kind != IntentKind::WriteBackFull &&
            intent.kind != IntentKind::WriteEvictFull) {
            continue;
        }

        respond(pkt, "queued", 0);
        return true;
    }
    return false;
}

bool
Cache2ChiBridge::snoopPrecedesPendingTxn(const RawSnp& snp)
{
    const Addr snoopAddr =
        snp.addr & ~(static_cast<Addr>(blockSize) - 1);
    const bool invalidating = snoopInvalidates(snp);
    bool precedes = false;

    for (auto& item : txns) {
        TxnEntry& txn = item.second;
        if (!txn.completed && !txn.gotComp && !txn.gotData && !txn.hasDbid &&
            (txn.req.addr & ~(static_cast<Addr>(blockSize) - 1)) ==
                snoopAddr) {
            precedes = true;
            const bool retained = retainPromotedUpgradeResponse(
                txn.intent.respondAsUpgrade, true, invalidating);
            if (txn.intent.respondAsUpgrade && !retained) {
                DPRINTF(Cache2ChiBridge,
                        "preceding invalidating snoop txnid=%u addr=%#llx "
                        "requires data-bearing response for promoted "
                        "upgrade txnid=%u\n",
                        snp.txnid,
                        static_cast<unsigned long long>(snoopAddr),
                        txn.txnid);
            }
            txn.intent.respondAsUpgrade = retained;
        }
    }

    std::queue<PacketPtr> pending = pendingReqPkts;
    while (!pending.empty()) {
        PacketPtr pkt = pending.front();
        pending.pop();
        if (!pkt->req->isUncacheable() &&
            pkt->getBlockAddr(blockSize) == snoopAddr) {
            precedes = true;
            const bool promoted = promotedUpgradePkts.count(pkt) != 0;
            if (!retainPromotedUpgradeResponse(
                    promoted, true, invalidating) &&
                promotedUpgradePkts.erase(pkt)) {
                DPRINTF(Cache2ChiBridge,
                        "preceding invalidating snoop txnid=%u addr=%#llx "
                        "requires data-bearing response for queued promoted "
                        "upgrade\n",
                        snp.txnid,
                        static_cast<unsigned long long>(snoopAddr));
            }
        }
    }
    return precedes;
}

bool
Cache2ChiBridge::sendPendingResponses()
{
    if (cacheRespBlocked) {
        return false;
    }

    while (!pendingRespPkts.empty()) {
        PendingClassicResponse& pending = pendingRespPkts.front();
        if (!cachePort.sendTimingResp(pending.pkt)) {
            cacheRespBlocked = true;
            return false;
        }

        const std::optional<uint32_t> txnid = pending.txnid;
        if (pending.compAck) {
            panic_if(!txnid,
                     "%s: pending CompAck has no bridge transaction\n",
                     name());
            pendingCompAcks.push({*pending.compAck, *txnid});
        } else if (txnid) {
            freeTxn(*txnid);
        }
        pendingRespPkts.pop();
    }
    return true;
}

bool
Cache2ChiBridge::sendPendingCompAcks()
{
    while (!pendingCompAcks.empty()) {
        const PendingCompAck& pending = pendingCompAcks.front();
        if (!chiPort.enqueueRx(RSP, pending.rsp)) {
            return false;
        }
        DPRINTF(Cache2ChiBridge,
                "send CompAck txnid=%u after classic response acceptance\n",
                pending.rsp.txnid);
        const uint32_t txnid = pending.txnid;
        pendingCompAcks.pop();
        freeTxn(txnid);
    }
    return true;
}

std::optional<RawRsp>
Cache2ChiBridge::makeCompAck(const TxnEntry& txn) const
{
    if (!txn.intent.requiresCompAck) {
        return std::nullopt;
    }

    RawRsp ack{};
    ack.qos = txn.req.qos;
    ack.srcid = nodeId;
    ack.tgtid = txn.req.tgtid;
    ack.txnid = txn.txnid;
    ack.opcode = RspOp::CompAck;
    ack.stage = 0;
    ack.dbid = 0;
    ack.resp = static_cast<uint8_t>(RespState::Unknown);
    return ack;
}

bool
Cache2ChiBridge::sendTxnData(TxnEntry& txn)
{
    return advanceTxnData(
        txn, [this, &txn](ChannelType channel, const FlitVariant& flit) {
            const RawDat& dat = std::get<RawDat>(flit);
            const bool sent = chiPort.enqueueRx(channel, flit);
            if (sent) {
                DPRINTF(Cache2ChiBridge,
                        "send write DAT txnid=%u dbid=%u dataid=%u "
                        "bytes=%u last=%u\n",
                        txn.txnid, dat.dbid, dat.dataid,
                        static_cast<unsigned>(dat.data.size()), dat.last);
            }
            return sent;
        });
}

bool
Cache2ChiBridge::reissueRetriedTxn(uint32_t txnid)
{
    auto it = txns.find(txnid);
    if (it == txns.end()) {
        return true;
    }
    TxnEntry& txn = it->second;
    if (!txn.retryBlocked) {
        return true;
    }
    if (!chiPort.enqueueRx(REQ, txn.req)) {
        return false;
    }
    txn.retryBlocked = false;
    txn.hasDbid = false;
    txn.gotComp = false;
    txn.gotData = false;
    txn.nextDataBeat = 0;
    txn.readData.clear();
    txn.readCoverage.clear();
    txn.seenReadDataIds.clear();
    txn.readDataBytes = 0;
    txn.sawReadDataLast = false;
    txn.readResp.reset();
    return true;
}

void
Cache2ChiBridge::maybeComplete(TxnEntry& txn)
{
    if (txn.completed || txn.retryBlocked) {
        return;
    }
    if (txn.intent.expectsData && !txn.gotData) {
        return;
    }
    if (txn.intent.carriesData && txn.nextDataBeat < txn.dataBeats.size()) {
        return;
    }
    if (txn.intent.expectsComp && !txn.gotComp) {
        return;
    }
    completeClassicTxn(txn);
}

void
Cache2ChiBridge::completeClassicTxn(TxnEntry& txn)
{
    recordReqLatency(txn);
    txn.completed = true;
    PacketPtr pkt = txn.pkt;
    const uint32_t txnid = txn.txnid;
    const std::optional<RawRsp> compAck = makeCompAck(txn);

    if (txn.intent.needsResponse) {
        const std::string cmd = pkt->cmdString();
        if (txn.intent.respondAsUpgrade) {
            pkt->cmd = MemCmd::UpgradeReq;
        }
        pkt->makeTimingResponse();
        if (pkt->hasData() && txn.intent.expectsData &&
            !txn.intent.respondAsUpgrade) {
            panic_if(txn.readData.size() < pkt->getSize(),
                     "%s: completing read txn %u with only %zu/%u bytes\n",
                     name(), txn.txnid, txn.readData.size(), pkt->getSize());
            pkt->setData(txn.readData.data());
        }
        const bool sent = !cacheRespBlocked && cachePort.sendTimingResp(pkt);
        if (!sent) {
            cacheRespBlocked = true;
            pendingRespPkts.push({pkt, compAck, txnid});
        } else if (compAck) {
            pendingCompAcks.push({*compAck, txnid});
        } else {
            freeTxn(txnid);
        }
        DPRINTF(Cache2ChiBridge,
                "complete classic txnid=%u cmd=%s responded=%u\n",
                txnid, cmd.c_str(), sent);
    } else {
        panic_if(compAck,
                 "%s: transaction %u requires CompAck without a classic "
                 "response\n",
                 name(), txnid);
        DPRINTF(Cache2ChiBridge, "complete no-response txnid=%u cmd=%s\n",
                txnid, pkt->cmdString().c_str());
        delete pkt;
        freeTxn(txnid);
    }
}

void
Cache2ChiBridge::recordReqLatency(TxnEntry& txn)
{
    if (!txn.latencyTracked || txn.latencyRecorded) {
        return;
    }
    panic_if(txn.lastResponseChannel == NUM_CHANNELS,
             "%s completes traced REQ txnid=%u without a terminal CHI flit\n",
             name(), txn.txnid);
    transactionLatencyRecords.push_back({
        true, REQ, txn.req.opcode, txn.req.srcid, txn.req.tgtid, txn.txnid,
        txn.latencyStartTick, curTick(), txn.lastResponseChannel,
        txn.lastResponseOpcode, txn.retryCount});
    txn.latencyRecorded = true;
}

void
Cache2ChiBridge::recordSnoopLatency(
    const SnoopEntry& snoop, ChannelType terminal_channel,
    uint8_t terminal_opcode)
{
    if (!snoop.latencyTracked) {
        return;
    }
    transactionLatencyRecords.push_back({
        true, SNP, snoop.snp.opcode, snoop.snp.srcid, snoop.snp.tgtid,
        snoop.snp.txnid, snoop.latencyStartTick, curTick(), terminal_channel,
        terminal_opcode, snoop.retryCount});
}

void
Cache2ChiBridge::sendSnoopRsp(SnoopEntry& snoop, RespState state)
{
    panic_if(snoop.pendingRsp || !snoop.dataBeats.empty() ||
                 snoop.nextDataBeat != 0,
             "%s: snoop txnid=%u already has a pending CHI response\n",
             name(), snoop.snp.txnid);
    RawRsp rsp{};
    rsp.qos = snoop.snp.qos;
    rsp.srcid = nodeId;
    rsp.tgtid = snoop.snp.srcid;
    rsp.txnid = snoop.snp.txnid;
    rsp.opcode = RspOp::SnpResp;
    rsp.stage = 0;
    rsp.dbid = 0;
    rsp.resp = static_cast<uint8_t>(state);
    snoop.pendingRsp = rsp;
}

void
Cache2ChiBridge::sendSnoopData(SnoopEntry& snoop, PacketPtr pkt)
{
    panic_if(snoop.pendingRsp || !snoop.dataBeats.empty() ||
                 snoop.nextDataBeat != 0,
             "%s: snoop txnid=%u already has pending response data\n",
             name(), snoop.snp.txnid);
    panic_if(pkt->getSize() != blockSize,
             "%s: snoop data txnid=%u has %u/%u line bytes\n",
             name(), snoop.snp.txnid, pkt->getSize(), blockSize);
    DPRINTF(Cache2ChiBridge,
            "pack classic snoop data txnid=%u cmd=%s addr=%#llx bytes=%u\n",
            snoop.txnid, pkt->cmdString().c_str(),
            static_cast<unsigned long long>(pkt->getAddr()), pkt->getSize());
    const uint8_t* data = pkt->getConstPtr<uint8_t>();
    for (uint32_t offset = 0, dataid = 0; offset < blockSize;
         offset += dataBeatBytes, ++dataid) {
        const uint32_t beat_bytes =
            std::min(dataBeatBytes, blockSize - offset);
        RawDat dat{};
        dat.qos = snoop.snp.qos;
        dat.srcid = nodeId;
        dat.tgtid = snoop.snp.srcid;
        dat.txnid = snoop.snp.txnid;
        dat.opcode = DatOp::SnpRespData;
        dat.stage = 0;
        dat.last = offset + beat_bytes == blockSize;
        dat.HomeNID = snoop.snp.srcid;
        dat.dbid = 0;
        dat.dataid = static_cast<uint8_t>(dataid);
        dat.resp = static_cast<uint8_t>(
            snoop.invalidating ? RespState::I_PD : RespState::SD_PD);
        dat.beatOffset = offset;
        dat.data.assign(data + offset, data + offset + beat_bytes);
        dat.byteEnable.assign(beat_bytes, 1);
        dat.chunkValid.assign((beat_bytes + 7) / 8, 1);

        snoop.dataBeats.push_back(std::move(dat));
    }
}

void
Cache2ChiBridge::queuePendingSnoopResponse(SnoopEntry&& snoop)
{
    panic_if(snoop.snoopPkt,
             "%s: queues snoop txnid=%u while retaining classic packet\n",
             name(), snoop.snp.txnid);
    pendingSnoopResponses.push_back(std::move(snoop));
    sendPendingSnoopResponses();
}

bool
Cache2ChiBridge::sendPendingSnoopResponses()
{
    const size_t responses = pendingSnoopResponses.size();
    bool all_sent = true;
    for (size_t i = 0; i < responses; ++i) {
        SnoopEntry snoop = std::move(pendingSnoopResponses.front());
        pendingSnoopResponses.pop_front();
        const size_t first_unsent = snoop.nextDataBeat;
        const ChannelType terminal_channel =
            snoop.pendingRsp ? RSP : DAT;
        const uint8_t terminal_opcode = snoop.pendingRsp ?
            snoop.pendingRsp->opcode : snoop.dataBeats.back().opcode;
        const bool sent = advancePendingSnoopResponse(
            snoop, [this](ChannelType channel, const FlitVariant& flit) {
                return chiPort.enqueueRx(channel, flit);
            });
        if (!sent) {
            DPRINTF(Cache2ChiBridge,
                    "snoop txnid=%u response blocked after DAT beat=%u/%u\n",
                    snoop.snp.txnid,
                    static_cast<unsigned>(snoop.nextDataBeat),
                    static_cast<unsigned>(snoop.dataBeats.size()));
            pendingSnoopResponses.push_back(std::move(snoop));
            all_sent = false;
            continue;
        }
        recordSnoopLatency(snoop, terminal_channel, terminal_opcode);
        DPRINTF(Cache2ChiBridge,
                "snoop txnid=%u response sent DAT beats=%u..%u\n",
                snoop.snp.txnid, static_cast<unsigned>(first_unsent),
                static_cast<unsigned>(snoop.nextDataBeat));
    }
    return all_sent;
}

MemCmd
Cache2ChiBridge::snoopCmdFor(const RawSnp& snp) const
{
    const auto decoded = decodeSnp(snp.opcode);
    switch (decoded.minor) {
      case SnpMinor::SnpShared:
      case SnpMinor::SnpOnce:
        return MemCmd::ReadSharedReq;
      case SnpMinor::SnpClean:
      case SnpMinor::SnpNotSharedDirty:
        return MemCmd::ReadCleanReq;
      case SnpMinor::SnpUnique:
      case SnpMinor::SnpCleanInvalid:
      case SnpMinor::SnpMakeInvalid:
        return MemCmd::ReadExReq;
      default:
        panic("%s: unsupported TXSNP opcode=0x%x in RNF v1\n",
              name(), snp.opcode);
    }
}

bool
Cache2ChiBridge::snoopInvalidates(const RawSnp& snp) const
{
    const auto decoded = decodeSnp(snp.opcode);
    return decoded.minor == SnpMinor::SnpUnique ||
           decoded.minor == SnpMinor::SnpCleanInvalid ||
           decoded.minor == SnpMinor::SnpMakeInvalid;
}

bool
Cache2ChiBridge::memSidePortRecvTimingResp(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got resp from memory side for addr: %#x\n",
            pkt->getAddr());
    if (cacheRespBlocked || !cachePort.sendTimingResp(pkt)) {
        cacheRespBlocked = true;
        pendingRespPkts.push({pkt, std::nullopt, std::nullopt});
    }
    return true;
}

void
Cache2ChiBridge::memSidePortRecvReqRetry()
{
    DPRINTF(Cache2ChiBridge, "Got req retry from memory side\n");
    panic_if(!memReqBlocked,
             "%s: got classic request retry while not blocked\n", name());
    memReqBlocked = false;
    schedulePump();
}

void
Cache2ChiBridge::memSidePortRecvTimingSnoopReq(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    cachePort.sendTimingSnoopReq(pkt);
}

void
Cache2ChiBridge::memSidePortRecvRangeChange()
{
    DPRINTF(Cache2ChiBridge, "Got range change from memory side\n");
    cachePort.sendRangeChange();
}

void
Cache2ChiBridge::memSidePortRecvFunctionalSnoop(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got functional snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    cachePort.sendFunctionalSnoop(pkt);
}

Tick
Cache2ChiBridge::memSidePortRecvAtomicSnoop(PacketPtr pkt)
{
    DPRINTF(Cache2ChiBridge, "Got atomic snoop from memory side for addr: %#x\n",
            pkt->getAddr());
    return cachePort.sendAtomicSnoop(pkt);
}

void
Cache2ChiBridge::serialize(CheckpointOut &cp) const
{
    panic_if(!completelyIdle(),
             "%s checkpoint attempted before the CHI RN reached its drain "
             "fixed point\n",
             name());
    ClockedObject::serialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "cache2chi");

    // TxnID allocation cursors and TxnID-keyed outstanding transactions.
    paramOut(cp, "nextTxnId", nextTxnId);
    paramOut(cp, "nextSnoopTxnId", nextSnoopTxnId);
    paramOut(cp, "txnsSize", txns.size());
    std::vector<uint32_t> txn_ids;
    txn_ids.reserve(txns.size());
    for (const auto &kv : txns) txn_ids.push_back(kv.first);
    arrayParamOut(cp, "txnIds", txn_ids);

    paramOut(cp, "snoopsSize", snoops.size());
    std::vector<uint32_t> snoop_ids;
    snoop_ids.reserve(snoops.size());
    for (const auto &kv : snoops) snoop_ids.push_back(kv.first);
    arrayParamOut(cp, "snoopIds", snoop_ids);

    // Retry TxnID queue (queue + TxnID).
    std::vector<uint32_t> retry_ids;
    {
        std::queue<uint32_t> tmp = retryTxnIds;
        while (!tmp.empty()) {
            retry_ids.push_back(tmp.front());
            tmp.pop();
        }
    }
    paramOut(cp, "retryTxnIdsSize", retry_ids.size());
    arrayParamOut(cp, "retryTxnIds", retry_ids);

    // Queue occupancy is serialized as a drain invariant. Packet/flit-bearing
    // entries have no stable representation, so a valid global checkpoint
    // must have emptied every queue before serialization.
    paramOut(cp, "pendingCompAcksSize", pendingCompAcks.size());
    paramOut(cp, "pendingSnoopResponsesSize", pendingSnoopResponses.size());
    paramOut(cp, "pendingSnoopRetriesSize", pendingSnoopRetries.size());
    paramOut(cp, "pendingRespPktsSize", pendingRespPkts.size());
    paramOut(cp, "pendingReqPktsSize", pendingReqPkts.size());
}

void
Cache2ChiBridge::unserialize(CheckpointIn &cp)
{
    ClockedObject::unserialize(cp);
    Serializable::ScopedCheckpointSection section(cp, "cache2chi");

    paramIn(cp, "nextTxnId", nextTxnId);
    paramIn(cp, "nextSnoopTxnId", nextSnoopTxnId);

    // TxnID-keyed maps hold PacketPtrs that cannot be checkpoint-preserved.
    // A valid global checkpoint therefore requires both maps to be empty.
    uint32_t txn_size = 0;
    paramIn(cp, "txnsSize", txn_size);
    txns.clear();
    (void)txn_size;
    uint32_t snoop_size = 0;
    paramIn(cp, "snoopsSize", snoop_size);
    snoops.clear();
    (void)snoop_size;

    uint32_t retry_size = 0;
    paramIn(cp, "retryTxnIdsSize", retry_size);
    std::vector<uint32_t> retry_ids;
    arrayParamIn(cp, "retryTxnIds", retry_ids);
    while (!retryTxnIds.empty()) retryTxnIds.pop();
    for (uint32_t i = 0; i < retry_size && i < retry_ids.size(); ++i) {
        retryTxnIds.push(retry_ids[i]);
    }

    uint64_t compacks_sz = 0, snoopresp_sz = 0, snoopretry_sz = 0;
    uint64_t resppkts_sz = 0, reqpkts_sz = 0;
    paramIn(cp, "pendingCompAcksSize", compacks_sz);
    paramIn(cp, "pendingSnoopResponsesSize", snoopresp_sz);
    paramIn(cp, "pendingSnoopRetriesSize", snoopretry_sz);
    paramIn(cp, "pendingRespPktsSize", resppkts_sz);
    paramIn(cp, "pendingReqPktsSize", reqpkts_sz);
    // PacketPtr/flit-bearing state cannot be reconstructed from stable
    // identities alone.  A valid full-system checkpoint must reach the
    // global drain fixed point, where every one of these fields is empty.
    // Reject an invalid checkpoint instead of silently dropping a request
    // and allowing the restored guest to hang later.
    fatal_if(txn_size != 0 || snoop_size != 0 || retry_size != 0 ||
                 compacks_sz != 0 || snoopresp_sz != 0 ||
                 snoopretry_sz != 0 || resppkts_sz != 0 || reqpkts_sz != 0,
             "%s cannot restore non-drained CHI RN state: "
             "txns=%u snoops=%u retries=%u compacks=%llu "
             "snoopRsp=%llu snoopRetry=%llu respPkts=%llu reqPkts=%llu\n",
             name(), txn_size, snoop_size, retry_size,
             static_cast<unsigned long long>(compacks_sz),
             static_cast<unsigned long long>(snoopresp_sz),
             static_cast<unsigned long long>(snoopretry_sz),
             static_cast<unsigned long long>(resppkts_sz),
             static_cast<unsigned long long>(reqpkts_sz));
}

} // namespace Chi
} // namespace gem5

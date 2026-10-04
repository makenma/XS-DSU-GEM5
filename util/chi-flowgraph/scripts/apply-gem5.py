"""Install FlowGraph support against exactly the pinned gem5 source. Idempotent.

Rebuild with SCons, preserving gem5's C++17 standard. The observer is read-only;
the explicit RN and downstream retry support runs with the observer both on and off.
"""
import argparse
import pathlib
import shutil
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--gem5', required=True, type=pathlib.Path)
args = parser.parse_args()
project = pathlib.Path(__file__).resolve().parent.parent
gem5 = args.gem5.resolve()
commit = '65f3241a6cd486dffddaca251536f5c691af9a1f'
assert subprocess.check_output(['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip() == commit
prefix = 'src/mem/cache/CHI/'
pending = {}

def source(name):
    return subprocess.check_output(['git', '-C', str(gem5), 'show', f'{commit}:{prefix}{name}'], text=True)

def replace(s, old, new, count=1):
    assert s.count(old) == count, (old[:100], s.count(old), count)
    return s.replace(old, new)

s = source('base/ChiChannel.hh')
s = s.replace('#include <vector>', '#include <vector>\n#include <string>')
s = replace(s, '    struct BaseFlit\n', '''    // FlowGraph observer metadata, never used for architectural decisions.
    struct FlowGraphIdentity {
        std::string rootId, legId, parentLegId, attemptId, messageId;
        std::string payloadOrigin = "unknown";
        uint64_t address = 0;
    };

    struct BaseFlit
''')
s = replace(s, '        uint8_t      qos = 0;', '        FlowGraphIdentity flow;\n        uint8_t      qos = 0;')
s = replace(s, '        uint32_t    ReturnNid = 0;', '        uint32_t    ReturnNid = 0;\n        uint32_t    ReturnTxnId = 0;')
pending['base/ChiChannel.hh'] = s

s = source('HnfCoherencyController.hh')
s = '#include <map>\n#include <tuple>\n' + s
s = replace(s, '        RawReq req{};\n', '''        RawReq req{};
        RawReq mcRequest{};
        bool mcRetryPending = false;
        uint8_t mcRetryType = 0;
        FlowGraphIdentity mcFlow;
        std::unordered_map<uint32_t, FlowGraphIdentity> snoopFlows;
        std::string flowPayloadOrigin = "hnf_slc";
''')
s = replace(s, '        SlcSfSlcVictim victim;\n', '        SlcSfSlcVictim victim;\n        FlowGraphIdentity flow;\n')
s = replace(s, '    void queueMcRead(uint32_t entry);', '''    void queueMcRead(uint32_t entry);
    void retryCreditedDownstreamRequests();
    std::map<std::tuple<uint32_t,uint32_t,uint8_t>,unsigned> downstreamCredits;''')
pending['HnfCoherencyController.hh'] = s

s = source('HnfCoherencyController.cc')
s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + s
s = replace(s, '    req.AllowRetry = 0;', '    req.AllowRetry = 1; // First downstream attempt has no pre-granted P-Credit.')
s = replace(s, '    req.ReturnNid = entry.req.tgtid;', '    req.ReturnNid = entry.req.tgtid;\n    req.ReturnTxnId = req.txnid;')
s = replace(s, '    out.req.hnfDirtyVictim = true;', '    out.req.memattr = 0x0d; // Issue H B2.8.3.5: system-cache eviction.\n    out.req.snpattr = 0;\n    out.req.hnfDirtyVictim = true;')
s = replace(s, '    entry.req = in.req;\n', '''    entry.req = in.req;
    if (FlowGraphTrace::get().enabled() && entry.req.flow.rootId.empty()) {
        entry.req.flow = FlowGraphTrace::get().root();
        entry.req.flow.address = in.req.addr;
        FlowGraphTrace::get().stamp(entry.req);
    }
    FlowGraphTrace::get().event(entry.req.flow, "allocate", entry.req.tgtid,
        {{"opcode", FlowGraphTrace::opcode(REQ, in.req.opcode)}});
''')
s = replace(s, '                entry.sleepingOn = i;\n', '''                entry.sleepingOn = i;
                FlowGraphTrace::get().event(entry.req.flow, "wait_start", entry.req.tgtid,
                    {{"reason","same_address"},{"waitId",entry.req.flow.legId + "/address"},
                     {"blocker",entries[i].req.flow.legId}});
''')
s = replace(s, '        entry.sleepingOn.reset();\n', '''        FlowGraphTrace::get().event(entry.req.flow, "wait_end", entry.req.tgtid,
            {{"waitId",entry.req.flow.legId + "/address"}});
        entry.sleepingOn.reset();
''')
s = replace(s, '        snp.size = static_cast<uint8_t>(blockSize);\n        txSnpQ.push_back(out);\n', '''        snp.size = static_cast<uint8_t>(blockSize);
        snp.flow = FlowGraphTrace::get().child(entry.req.flow, "snoop", snp.addr);
        FlowGraphTrace::get().stamp(snp);
        entry.snoopFlows[target_src] = snp.flow;
        txSnpQ.push_back(out);
''', count=1)
s = replace(s, '    out.req = req;\n    txReqQ.push_back(out);', '''    req.flow = FlowGraphTrace::get().child(entry.req.flow, "memory", req.addr);
    FlowGraphTrace::get().stamp(req);
    entry.mcFlow = req.flow;
    entry.mcRequest = req;
    out.req = req;
    txReqQ.push_back(out);''')
s = replace(s, '        txDatQ.push_back(out);\n', '''        dat.flow = entry.req.flow;
        dat.flow.messageId.clear(); dat.flow.payloadOrigin = entry.flowPayloadOrigin;
        FlowGraphTrace::get().stamp(dat);
        txDatQ.push_back(out);
''')
s = replace(s, '    txRspQ.push_back(out);\n', '''    rsp.flow = entry.req.flow; rsp.flow.messageId.clear();
    FlowGraphTrace::get().stamp(rsp);
    txRspQ.push_back(out);
''', count=2)
s = replace(s, '    HnfCcRetireInfo info = makeRetireInfo(entry);\n', '''    FlowGraphTrace::get().event(entry.req.flow, "complete", entry.req.tgtid);
    HnfCcRetireInfo info = makeRetireInfo(entry);
''')
s = replace(s, '        entry.slcLookupResult = lookup->result;\n', '''        entry.slcLookupResult = lookup->result;
        FlowGraphTrace::get().event(entry.req.flow, "lookup", entry.req.tgtid,
            {{"fields",{{"slcHit",lookup->result.slcHit},{"sfHit",lookup->result.sfHit},
                        {"replay",lookup->result.replay},{"snoopTargets",std::to_string(lookup->result.snoopTargets)}}}});
''')
# The owner pointer is explicit at the dirty-victim handoff. Preserve it before
# the requester may retire; never reconstruct it from a line address later.
s = replace(s, '''        entries[entryId].req.txnid, true);
}''', '''        entries[entryId].req.txnid, true);
    auto& owner = dirtyVictimTxns.at(victim.victimId.value);
    owner.flow = FlowGraphTrace::get().child(entries[entryId].req.flow, "dirty-victim", victim.lineAddress);
    owner.flow.payloadOrigin = "hnf_dirty_victim";
    for (auto& queued : txReqQ) if (queued.dirtyVictimId == victim.victimId.value) {
        queued.req.flow = owner.flow; FlowGraphTrace::get().stamp(queued.req);
    }
}''')
s = replace(s, '    out.req.hnfDirtyVictim = true;\n', '''    out.req.hnfDirtyVictim = true;
    out.req.flow = transaction.flow; out.req.flow.messageId.clear();
    if (!allowRetry && !transaction.flow.rootId.empty()) {
        out.req.flow.attemptId = std::to_string(std::stoul(transaction.flow.attemptId) + 1);
        transaction.flow.attemptId = out.req.flow.attemptId;
    }
    FlowGraphTrace::get().stamp(out.req);
''')
s = replace(s, '    out.dat.data = transaction.victim.line.data;\n', '''    out.dat.data = transaction.victim.line.data;
    out.dat.flow = transaction.flow; out.dat.flow.messageId.clear();
    FlowGraphTrace::get().stamp(out.dat);
''')
s = replace(s, '    const uint64_t victim_id = transaction.victim.victimId.value;\n', '''    FlowGraphTrace::get().event(transaction.flow, "complete", transaction.homeNodeId);
    const uint64_t victim_id = transaction.victim.victimId.value;
''')
for anchor in ['"snoop entry", snoopIt->second);', '"write entry", *entryId);', 'entry.mcDataAssembly, entry.readData, dat, "MC entry", entryId);']:
    s = replace(s, anchor, anchor + '\n        entry.flowPayloadOrigin = dat.flow.payloadOrigin;')
s = replace(s, '        PocqEvent updateDone{};', '''        if (FlowGraphTrace::get().enabled()) {
            HnfSlcLookupReq query{}; query.blockAddr = entry.blockAddr;
            query.req = entry.req; query.txn = entry.txnKind;
            const auto probe = slcsfUnit->probe(query);
            const auto& line = probe.result;
            static const char* slcNames[] = {"I","EU","EN","SU","SN","MU","MN"};
            static const char* sfNames[] = {"I","EU","EN","SU","SN"};
            nlohmann::ordered_json state = {{"state",slcNames[unsigned(line.slcState)]},
                {"sfState",sfNames[unsigned(line.sfState)]},{"slcHit",line.slcHit},
                {"sfHit",line.sfHit},{"dirty",line.dataDirty},{"sharers",nlohmann::ordered_json::array()},
                {"set",probe.snapshot.slc.set},{"way",probe.snapshot.slc.way}};
            for (unsigned bit=0;bit<64;++bit) if (line.rnfvec & (1ull<<bit))
                state["sharers"].push_back(FlowGraphTrace::get().node(slcsfUnit->srcIdForIndex(bit)));
            if (line.sfState==HnfSfState::EU || line.sfState==HnfSfState::EN)
                state["owner"]=FlowGraphTrace::get().node(line.rnfid);
            if (line.slcHit) {
                state["data"]=FlowGraphTrace::bytes(line.data);
                state["validMask"]=std::string(line.data.size()*2,'f');
            }
            FlowGraphTrace::get().event(entry.req.flow,"state",entry.req.tgtid,{{"state",state}});
        }
        PocqEvent updateDone{};''')
for operation in ['Update','Lookup']:
    anchor = f'        slcsfUnit->tryEnqueue(std::move(*entry.pendingSlc{operation}));'
    s = replace(s, anchor, anchor + '''
    if (FlowGraphTrace::get().enabled()) {
        const char* reason = result==SlcSfEnqueueResult::NoCredit?"slcsf_no_credit":
            result==SlcSfEnqueueResult::Initializing?"slcsf_initializing":"slcsf_draining";
        FlowGraphTrace::get().wait(entry.req.flow,entry.req.tgtid,"slcsf_admission",
            result!=SlcSfEnqueueResult::Accepted,reason,
            {{"occupancy",slcsfUnit->reqOutstanding()},{"capacity",slcsfUnit->reqCapacity()},
             {"blocker","SLCSF request admission"}});
    }
''')
s = replace(s, '    entry.retryNotBeforeTick = replay->retryNotBeforeTick;', '''    static const char* traceReasons[]={"stale_commit_token","resource_conflict","seq_conflict","victim_buffer_full","cancelled"};
    FlowGraphTrace::get().wait(entry.req.flow,entry.req.tgtid,"slcsf_replay",true,
        traceReasons[unsigned(replay->reason)],{{"blocker","SLCSF replay"},
            {"retryNotBeforeTick",std::to_string(replay->retryNotBeforeTick)}});
    entry.retryNotBeforeTick = replay->retryNotBeforeTick;''')
s = replace(s, '        if (entry.slcUpdatePhase == SlcUpdatePhase::ReplayWait) {', '''        FlowGraphTrace::get().wait(entry.req.flow,entry.req.tgtid,"slcsf_replay",false,"");
        if (entry.slcUpdatePhase == SlcUpdatePhase::ReplayWait) {''')
pending['HnfCoherencyController.cc'] = s
s = replace(s, '''    const bool dirtyVictimRsp =''', '''    if (decoded.minor == RspMinor::PCrdGrant) {
        if (rsp.respErr==0) {
            ++downstreamCredits[std::make_tuple(rsp.tgtid,rsp.srcid,rsp.pcrdtype)];
            retryCreditedDownstreamRequests();
        }
        return std::nullopt;
    }
    if (decoded.minor == RspMinor::RetryAck) {
        auto match=mcTxnToEntry.find(rsp.txnid);
        if(match!=mcTxnToEntry.end()) {
            Entry& entry=entries[match->second];
            if(rsp.srcid!=entry.mcRequest.tgtid || rsp.tgtid!=entry.mcRequest.srcid ||
               rsp.respErr!=0 || !entry.mcRequest.AllowRetry || !entry.mcReadIssued ||
               entry.mcRetryPending || entry.mcDataAssembly.coveredBytes) return std::nullopt;
            entry.mcRetryPending=true;entry.mcReadIssued=false;entry.mcRetryType=rsp.pcrdtype;
            FlowGraphTrace::get().wait(entry.mcFlow,entry.req.tgtid,"pcredit",true,"pcredit",
                {{"blocker",FlowGraphTrace::get().node(rsp.srcid)}});
            retryCreditedDownstreamRequests();
            return std::nullopt;
        }
    }
    const bool dirtyVictimRsp =''')
s = replace(s, '''            victim.phase = DirtyVictimPhase::RetryPending;''', '''            victim.phase = DirtyVictimPhase::RetryPending;
            retryCreditedDownstreamRequests();''')
s += '\n' + (project / 'native/gem5/McRetry.inc').read_text()
pending['HnfCoherencyController.cc'] = s

s = source('base/ChiCommonPort.hh')
s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + s
s = replace(s, '''            !dst.rxAdmissionCallback(ch, f)) {
            return false;''', '''            !dst.rxAdmissionCallback(ch, f)) {
            FlowGraphTrace::get().port(ch,f,name(),dst.name(),false,queues[idx].size(),creditLimit[idx],"endpoint_admission");
            return false;''')
s = replace(s, '''        if (credit[idx] == 0) {
            return false;''', '''        if (credit[idx] == 0) {
            FlowGraphTrace::get().port(ch,f,name(),dst.name(),false,queues[idx].size(),creditLimit[idx]);
            return false;''')
s = replace(s, '        queues[idx].push_back(f);', '''        queues[idx].push_back(f);
        FlowGraphTrace::get().port(ch,f,name(),dst.name(),true,queues[idx].size(),creditLimit[idx]);''')
pending['base/ChiCommonPort.hh'] = s

s = '#include <map>\n#include <tuple>\n' + source('Cache2ChiBridge.hh')
s = replace(s, '        bool retryBlocked = false;', '        bool retryBlocked = false;\n        bool retryQueued = false;')
s = replace(s, '    std::queue<uint32_t> retryTxnIds;', '''    using RetryCredits = std::map<std::tuple<uint32_t,uint32_t,uint32_t>,uint32_t>;
    RetryCredits retryCredits;
    static std::vector<uint32_t> acceptRetryResponse(
        const RawRsp&, uint32_t requester,
        std::unordered_map<uint32_t, TxnEntry>&, RetryCredits&);
    std::queue<uint32_t> retryTxnIds;''')
pending['Cache2ChiBridge.hh'] = s
pending['Cache2ChiBridgeData.cc'] = source('Cache2ChiBridgeData.cc') + (project/'native/gem5/RnfRetry.inc').read_text()
s = replace(source('Cache2ChiBridge.test.cc'), '''class Cache2ChiBridgeProtocolTestPeer
{
  public:''', '''class Cache2ChiBridgeProtocolTestPeer
{
  public:
    struct RetryHarness {
        std::unordered_map<uint32_t, Cache2ChiBridge::TxnEntry> txns;
        Cache2ChiBridge::RetryCredits credits;
        void add(uint32_t id) {
            Cache2ChiBridge::TxnEntry txn{};
            txn.txnid=id;txn.req.txnid=id;txn.req.srcid=TestRequester;
            txn.req.tgtid=TestHome;txn.req.AllowRetry=1;
            txns.emplace(id,txn);
        }
        std::vector<uint32_t> accept(const RawRsp& rsp) {
            return Cache2ChiBridge::acceptRetryResponse(rsp,TestRequester,txns,credits);
        }
    };''')
pending['Cache2ChiBridge.test.cc'] = s + (project/'native/gem5/RnfRetryTests.inc').read_text()

s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + source('Cache2ChiBridge.cc')
s = replace(s, '    req.ReturnNid = nodeId;', '''    req.ReturnNid = 0; // Inapplicable on RN -> HN requests (B2.4.10).
    req.memattr = 0x0d; // Normal, cacheable, allocate, EWA for coherent requests.
    req.snpattr = 1;
    req.expCompAck = intent.requiresCompAck;''')
s = replace(s, '    return req;\n', '''    req.flow = FlowGraphTrace::get().root("rnf"); req.flow.address = req.addr;
    FlowGraphTrace::get().stamp(req);
    FlowGraphTrace::get().event(req.flow,"allocate",nodeId,{{"opcode",FlowGraphTrace::opcode(REQ,req.opcode)}});
    FlowGraphTrace::get().event(req.flow,"sam",nodeId,
        {{"target",FlowGraphTrace::get().node(req.tgtid)},
         {"fields",{{"homeNodeIds",homeNodeIds},{"homeNodeId",homeNodeId},
                    {"paBits",hnfHashPaBits},{"method","CMN SCG XOR"},
                    {"index",homeNodeIds.empty()?0:cmnHnfIndex(req.addr,homeNodeIds.size(),hnfHashPaBits)},
                    {"selectedNodeId",req.tgtid},{"classicCommand",pkt->cmdString()}}}});
    return req;
''')
s = replace(s, '        beats.push_back(std::move(dat));', '''        dat.flow=req.flow; dat.flow.messageId.clear(); dat.flow.payloadOrigin="classic_packet";
        FlowGraphTrace::get().stamp(dat);
        beats.push_back(std::move(dat));''')
s = replace(s, '    return ack;', '''    ack.flow=txn.req.flow; ack.flow.messageId.clear(); FlowGraphTrace::get().stamp(ack);
    return ack;''')
s = replace(s, '    snoop.pendingRsp = rsp;', '''    rsp.flow=snoop.snp.flow; rsp.flow.messageId.clear(); FlowGraphTrace::get().stamp(rsp);
    snoop.pendingRsp = rsp;''')
s = replace(s, '        snoop.dataBeats.push_back(std::move(dat));', '''        dat.flow=snoop.snp.flow; dat.flow.messageId.clear(); dat.flow.payloadOrigin="classic_cache_snoop";
        FlowGraphTrace::get().stamp(dat);
        snoop.dataBeats.push_back(std::move(dat));''')
s = replace(s, '''    const auto decoded = decodeRsp(rsp.opcode);
    auto it = txns.find(rsp.txnid);''', '''    const auto decoded = decodeRsp(rsp.opcode);
    if (decoded.minor == RspMinor::PCrdGrant || decoded.minor == RspMinor::RetryAck) {
        for (auto id : acceptRetryResponse(rsp,nodeId,txns,retryCredits)) {
            auto& txn=txns.at(id);
            if (FlowGraphTrace::get().enabled()) {
                txn.req.flow.attemptId=std::to_string(txn.retryCount);
                txn.req.flow.messageId.clear(); FlowGraphTrace::get().stamp(txn.req);
                for (auto& beat:txn.dataBeats) { beat.flow=txn.req.flow; beat.flow.messageId.clear(); FlowGraphTrace::get().stamp(beat); }
            }
            retryTxnIds.push(id);
        }
        return;
    }
    auto it = txns.find(rsp.txnid);''')
s = replace(s, '''      case RspMinor::RetryAck:
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
''', '')
s = replace(s, '    txn.retryBlocked = false;', '    txn.retryBlocked = false;\n    txn.retryQueued = false;')
s = replace(s, '    // Retry TxnID queue (queue + TxnID).', '''    std::vector<uint32_t> creditState;
    for (const auto& [key,count] : retryCredits) {
        creditState.insert(creditState.end(), {std::get<0>(key),std::get<1>(key),std::get<2>(key),count});
    }
    paramOut(cp,"retryCreditsSize",creditState.size());
    arrayParamOut(cp,"retryCredits",creditState);

    // Retry TxnID queue (queue + TxnID).''')
s = replace(s, '    uint32_t retry_size = 0;', '''    retryCredits.clear();
    uint32_t creditSize=0;
    if (optParamIn(cp,"retryCreditsSize",creditSize)) {
        std::vector<uint32_t> creditState;
        arrayParamIn(cp,"retryCredits",creditState);
        fatal_if(creditSize%4 || creditState.size()!=creditSize,"Invalid RN credit checkpoint");
        for(size_t i=0;i<creditState.size();i+=4)
            retryCredits[std::make_tuple(creditState[i],creditState[i+1],creditState[i+2])]=creditState[i+3];
    }
    uint32_t retry_size = 0;''')
pending['Cache2ChiBridge.cc'] = s
s = replace(s, '    txns.erase(txnid);', '''    auto traceTxn=txns.find(txnid);
    if (traceTxn!=txns.end()) FlowGraphTrace::get().event(traceTxn->second.req.flow,"complete",nodeId);
    txns.erase(txnid);''')
pending['Cache2ChiBridge.cc'] = s

s = source('Chi2ClassicMemTxnPolicy.hh')
s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + s
s = replace(s, '        return rsp;', '''        rsp.flow=req.flow; rsp.flow.messageId.clear(); FlowGraphTrace::get().stamp(rsp);
        return rsp;''')
pending['Chi2ClassicMemTxnPolicy.hh'] = s

s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + source('Chi2ClassicMemBridge.cc')
s = replace(s, '    queueCompData(txn, src,', '''    txn.req.flow.payloadOrigin = pkt->hasData()?"memory_response":"simulator_zero_fill";
    queueCompData(txn, src,''')
s = replace(s, '        txDatQ.push_back(', '''        dat.flow=txn.req.flow; dat.flow.messageId.clear();
        if (offset+beatBytes>data_bytes) dat.flow.payloadOrigin="memory_response_with_simulator_padding";
        FlowGraphTrace::get().stamp(dat);
        txDatQ.push_back(''')
s = replace(s, '    entry.req = req;', '''    entry.req = req;
    FlowGraphTrace::get().event(req.flow,"allocate",nodeId,{{"opcode",FlowGraphTrace::opcode(REQ,req.opcode)}});''', count=2)
s = replace(s, '            txns.erase(txn);', '''            FlowGraphTrace::get().event(txn->second.req.flow,"complete",nodeId);
            txns.erase(txn);''')
s = replace(s, '\n        txns.erase(txn);', '''
        FlowGraphTrace::get().event(txn->second.req.flow,"complete",nodeId);
        txns.erase(txn);''')
# The original reads a destroyed deque element after pop_front. Snapshot the
# two scalar values before destruction; required when metadata owns strings.
s = replace(s, '''        txDatQ.pop_front();
        if (dat.last) {''', '''        const bool traceLast = dat.last;
        const uint32_t traceTxnId = dat.txnid;
        txDatQ.pop_front();
        if (traceLast) {''')
s = replace(s, '''                     name(), dat.txnid);
            FlowGraphTrace''', '''                     name(), traceTxnId);
            FlowGraphTrace''')
pending['Chi2ClassicMemBridge.cc'] = s

s = source('HomeLinkLayer.hh')
for name in ['RetryRecord', 'PendingWinner']:
    s = replace(s, f'    struct {name}\n    {{', f'    struct {name}\n    {{\n        FlowGraphIdentity flow;')
pending['HomeLinkLayer.hh'] = s
s = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + source('HomeLinkLayer.cc')
s = replace(s, '                rec.originSeq = entry.seq;', '                rec.flow = req.flow;\n                rec.originSeq = entry.seq;')
s = replace(s, '            winner.valid = true;', '            winner.flow = it->flow;\n            winner.valid = true;')
s = replace(s, '    rsp.qos = rec.qos;', '    rsp.flow=rec.flow; rsp.flow.messageId.clear(); FlowGraphTrace::get().stamp(rsp);\n    rsp.qos = rec.qos;')
s = replace(s, '        rsp.qos = winner.qos;', '        rsp.flow=winner.flow; rsp.flow.messageId.clear(); FlowGraphTrace::get().stamp(rsp);\n        rsp.qos = winner.qos;')
s = replace(s, '    rsp.qos = req.qos;', '    rsp.flow=req.flow; rsp.flow.messageId.clear(); FlowGraphTrace::get().stamp(rsp);\n    rsp.qos = req.qos;')
pending['HomeLinkLayer.cc'] = s

s = source('SConscript')
s += '''
# FlowGraph: observer and component harness, C++17 like the original gem5.
env.Append(CPPPATH=[Dir('#ext/flowgraph-json')])
GTest('flowgraph.test',
      'FlowGraph.test.cc',
      'HnfCoherencyController.cc',
      'HnfPOCQStateGraph.cc',
      'HnfSeqPOCQStateGraph.cc',
      'HnfSLCSF.cc',
      'HnfSLCSFBackend.cc',
      with_tag('gem5 trace'))
'''
pending['SConscript'] = s

# Verify all destinations before editing any tracked source.
for name, content in pending.items():
    path = gem5 / prefix / name
    current = path.read_text()
    previous = project / 'build' / 'gem5-installed' / name
    accepted = {source(name), content}
    if previous.exists(): accepted.add(previous.read_text())
    if current not in accepted:
        raise RuntimeError(f'Refusing to overwrite independent edits: {path}')
for name, content in pending.items():
    (gem5 / prefix / name).write_text(content)
    previous = project / 'build' / 'gem5-installed' / name
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text(content)
for name in ['FlowGraphTrace.hh', 'FlowGraph.test.cc']:
    shutil.copyfile(project / 'native/gem5' / name, gem5 / prefix / name)
shutil.copytree(project / 'vendor/json/nlohmann', gem5 / 'ext/flowgraph-json/nlohmann', dirs_exist_ok=True)
print('Installed FlowGraph observer and harness on pinned gem5.')
subprocess.run(['python3', str(project / 'scripts/apply-extensions.py'), '--gem5', str(gem5)], check=True)
subprocess.run(['python3', str(project / 'scripts/apply-riscv-cbo.py'), '--gem5', str(gem5)], check=True)
subprocess.run(['python3', str(project / 'scripts/apply-bpu-scratch-lifetime.py'), '--gem5', str(gem5)], check=True)
subprocess.run(['python3', str(project / 'scripts/apply-nonspec-tlb-retry.py'), '--gem5', str(gem5)], check=True)

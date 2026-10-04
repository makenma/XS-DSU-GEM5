#include <gtest/gtest.h>
#include <filesystem>
#include <functional>
#include <stdexcept>
#include "mem/cache/CHI/FlowGraphTrace.hh"
#include "mem/cache/CHI/HnfCoherencyController.hh"
#include "mem/cache/CHI/HnfSLCSF.hh"

namespace gem5::Chi {
namespace {
using J=nlohmann::ordered_json;
constexpr uint32_t HN=0x90,SN=0x80;
constexpr uint64_t A=0x80004040;
void require(bool ok,const char* what){if(!ok)throw std::runtime_error(what);}
std::vector<uint8_t> pattern(uint8_t seed){std::vector<uint8_t> d(64);for(unsigned i=0;i<64;++i)d[i]=seed+i;return d;}
const char* slcName(HnfSlcState s){static const char* names[]={"I","EU","EN","SU","SN","MU","MN"};return names[unsigned(s)];}
const char* sfName(HnfSfState s){static const char* names[]={"I","EU","EN","SU","SN"};return names[unsigned(s)];}

struct Harness {
    HnfSLCSF slcsf;
    HnfCoherencyController cc;
    FlowGraphTrace& trace=FlowGraphTrace::get();
    Tick tick=0;
    unsigned allocation=0,reads=0,snoops=0,writes=0;
    bool dirtySnoop=false,retry=false;
    std::vector<uint8_t> memory=pattern(0x20),owner=pattern(0x70),victimBytes;
    std::map<std::string,std::vector<uint8_t>> received;
    std::map<uint32_t,RawReq> requests;
    explicit Harness(const std::string& scenario,const std::string& file):
        slcsf(64,scenario=="writeback"||scenario=="retry"?1:4,scenario=="writeback"||scenario=="retry"?1:2,16,4),
        cc(64,32,8,SN,false,1,true) {
        cc.setSlcsf(&slcsf);dirtySnoop=scenario=="snoop-owner";retry=scenario=="retry";
        const std::map<std::string,std::string> titles={{"miss-hit","一次 miss，随后同地址 hit"},{"snoop-owner","Dirty owner 供数"},{"unique","ReadUnique · 副本失效"},{"writeback","RN 写回与 dirty victim 下游写入"},{"retry","RetryAck · P-Credit · 重发"},{"same-address","同地址事务等待"}};
        J nodes=J::array();for(auto [id,name,role]:std::vector<std::tuple<unsigned,std::string,std::string>>{{0,"RN0","RN-F"},{4,"RN1","RN-F"},{8,"RN2","RN-F"},{HN,"HN0","HN-F"},{SN,"SN0","SN-F"}})nodes.push_back({{"id",name},{"nodeId",id},{"role",role},{"label",name}});
        J manifest={{"type","manifest"},{"schemaVersion","1.0"},{"title",titles.at(scenario)},
            {"description","真实 gem5 HNF/SLCSF 组件；RN 与 SN 使用确定性测试端点。"},
            {"source",{{"kind","component_harness"},{"gem5Commit","65f3241a6cd486dffddaca251536f5c691af9a1f"},{"chironCommit","55cd1e1d92142960af2b57c686fc9f35ce7286be"},{"generator","FlowGraph.test.cc"},{"captureBoundary","HNF controller and component test endpoints"}}},
            {"protocol","ARM IHI 0050 Issue H"},{"profile","h-core-256"},{"dataWidth",256},{"lineBytes",64},{"topology",nodes},{"complete",true},
            {"clock",{{"tickHz","1000000000000"},{"source","explicit component Tick"}}},
            {"sam",{{"description","组件测试固定 homeNodeIds=[HN0]；index=(PA >> 6) % 1 = 0"},{"homeNodeIds",J::array({"HN0"})},{"lineShift",6},{"method","line-modulo"}}},
            {"initialState",J::object()}};
        trace.open(file,manifest);trace.setComponentTick(tick);
    }
    ~Harness(){trace.close();}
    void advance(){trace.setComponentTick(++tick);slcsf.wakeup(tick);cc.serviceInternalWork(tick);}
    void until(const std::function<bool()>& predicate){for(unsigned i=0;i<128&&!predicate();++i)advance();require(predicate(),"Component made no expected progress in 128 ticks");}
    template<class F> void pair(ChannelType ch,F f){trace.stamp(f);trace.flit(ch,f,"TX","component test endpoint");trace.flit(ch,f,"RX","component test endpoint");}
    HnfLinkToCcReq make(unsigned requester,uint8_t opcode,uint64_t address=A,unsigned entry=0) {
        HnfLinkToCcReq r{};r.valid=true;r.seq=++allocation;r.tokenId=entry;
        r.req.srcid=requester;r.req.tgtid=HN;r.req.txnid=11+allocation;r.req.opcode=opcode;r.req.addr=address;r.req.size=64;r.req.expCompAck=opcode==1||opcode==7;r.req.AllowRetry=1;r.req.memattr=0x0d;r.req.snpattr=1;
        r.req.flow=trace.root(opcode==0x1b?"writeback":"read");r.req.flow.address=address;trace.stamp(r.req);requests[r.req.txnid]=r.req;return r;
    }
    void admit(const HnfLinkToCcReq& r){
        trace.event(r.req.flow,"sam",r.req.srcid,{{"target","HN0"},{"fields",{{"lineShift",6},{"homeCount",1},{"index",0},{"addressLine",std::to_string(r.req.addr>>6)}}}});
        require(cc.acceptLinkReq(r,tick).accepted,"Request admission failed");pair(REQ,r.req);
    }
    J state(uint64_t address,unsigned requester=0){
        HnfSlcLookupReq q{};q.blockAddr=address;q.req.srcid=requester;q.txn=PocqTxnKind::ReadShared;
        const auto probe=slcsf.probe(q);const auto& line=probe.result;J sharers=J::array();
        for(unsigned i=0;i<64;++i)if(line.rnfvec&(1ull<<i))sharers.push_back(trace.node(slcsf.srcIdForIndex(i)));
        J result={{"state",slcName(line.slcState)},{"sfState",sfName(line.sfState)},{"sharers",sharers},{"dirty",line.dataDirty},
            {"slcHit",line.slcHit},{"sfHit",line.sfHit},{"set",probe.snapshot.slc.set},{"way",probe.snapshot.slc.way}};
        if(line.sfState==HnfSfState::EU||line.sfState==HnfSfState::EN)result["owner"]=trace.node(line.rnfid);
        if(line.slcHit){result["data"]=trace.bytes(line.data);result["validMask"]=std::string(line.data.size()*2,'f');}
        return result;
    }
    void sample(const RawReq& request){trace.event(request.flow,"state",HN,{{"state",state(request.addr,request.srcid)}});}
    RawDat beat(const RawReq& request,unsigned offset,const std::vector<uint8_t>& bytes,uint8_t opcode=4) {
        RawDat d{};d.srcid=opcode==2?request.srcid:SN;d.tgtid=HN;d.txnid=request.txnid;d.opcode=opcode;
        d.HomeNID=HN;d.dataid=offset/32;d.beatOffset=offset;d.last=offset==32;d.data.assign(bytes.begin()+offset,bytes.begin()+offset+32);
        d.byteEnable.assign(32,1);d.chunkValid.assign(4,1);d.flow=request.flow;d.flow.messageId.clear();d.flow.payloadOrigin=opcode==2?"test_rnf_write":"test_memory_response";trace.stamp(d);return d;
    }
    void memoryRead(){
        auto out=cc.frontTxReq();require(!out.dirtyVictimId,"Expected memory read");cc.popTxReq();cc.notifyTxReqSent(out);pair(REQ,out.req);++reads;
        trace.event(out.req.flow,"allocate",SN,{{"opcode","ReadNoSnp"}});
        trace.event(out.req.flow,"wait_start",HN,{{"reason","data"},{"waitId",out.req.flow.legId+"/data"}});
        advance();for(unsigned offset:{0u,32u}){auto d=beat(out.req,offset,memory);cc.acceptRxDat(d);pair(DAT,d);}
        trace.event(out.req.flow,"wait_end",HN,{{"waitId",out.req.flow.legId+"/data"}});trace.event(out.req.flow,"complete",SN);
    }
    void snoop(){
        auto out=cc.frontTxSnp();cc.popTxSnp();pair(SNP,out.snp);++snoops;
        if(dirtySnoop) {
            // Deliberately deliver the higher beat first. Data is supplied by
            // the driver; HNF assembly, dirty-state handling and forwarding
            // are the unmodified real component behavior.
            for(unsigned offset:{32u,0u}) {RawReq r{};r.txnid=out.snp.txnid;r.flow=out.snp.flow;auto d=beat(r,offset,owner,1);d.srcid=out.targetNode;d.resp=5;d.flow.payloadOrigin="test_dirty_owner";cc.acceptRxDat(d);pair(DAT,d);}
        } else {RawRsp r{};r.srcid=out.targetNode;r.tgtid=HN;r.txnid=out.snp.txnid;r.opcode=1;r.flow=out.snp.flow;r.flow.messageId.clear();cc.acceptRxRsp(r);pair(RSP,r);}
    }
    RawRsp response(const HnfCcTxReq& request,uint8_t opcode,uint8_t dbid=0,uint8_t type=0) {
        RawRsp r{};r.srcid=SN;r.tgtid=HN;r.txnid=request.req.txnid;r.opcode=opcode;r.dbid=dbid;r.pcrdtype=type;r.flow=request.req.flow;r.flow.messageId.clear();return r;
    }
    void victim(){
        auto out=cc.frontTxReq();require(out.dirtyVictimId.has_value(),"Expected dirty victim");cc.popTxReq();cc.notifyTxReqSent(out);pair(REQ,out.req);++writes;
        if(retry){auto rejected=response(out,3,0,7);cc.acceptRxRsp(rejected);pair(RSP,rejected);
            trace.event(out.req.flow,"wait_start",HN,{{"reason","pcredit"},{"waitId",out.req.flow.legId+"/credit"}});advance();
            auto grant=response(out,7,0,7);cc.acceptRxRsp(grant);pair(RSP,grant);
            trace.event(out.req.flow,"wait_end",HN,{{"waitId",out.req.flow.legId+"/credit"}});
            require(cc.hasTxReq(),"No retry request");out=cc.frontTxReq();cc.popTxReq();cc.notifyTxReqSent(out);pair(REQ,out.req);}
        auto dbid=response(out,6,22,out.req.pcrdtype);cc.acceptRxRsp(dbid);pair(RSP,dbid);
        until([&]{return cc.hasTxDat();});
        while(cc.hasTxDat()&&!cc.frontTxDat().dirtyVictimId)upstreamData();
        require(cc.hasTxDat(),"Missing dirty write data");auto d=cc.frontTxDat().dat;victimBytes=d.data;cc.popTxDat();pair(DAT,d);
        auto comp=response(out,4,22,out.req.pcrdtype);cc.acceptRxRsp(comp);pair(RSP,comp);
    }
    void upstreamData(){auto d=cc.frontTxDat().dat;cc.popTxDat();pair(DAT,d);auto& result=received[std::to_string(d.txnid)];if(result.empty())result.resize(64);std::copy(d.data.begin(),d.data.end(),result.begin()+d.beatOffset);}
    std::vector<uint8_t> completeRead(const HnfLinkToCcReq& request){
        for(unsigned loops=0;loops<200;++loops){
            if(cc.hasTxSnp())snoop();
            else if(cc.hasTxReq()){if(cc.frontTxReq().dirtyVictimId)victim();else memoryRead();}
            else if(cc.hasTxDat())upstreamData();
            else if(received.count(std::to_string(request.req.txnid)))break;
            else advance();
        }
        auto result=received.at(std::to_string(request.req.txnid));
        RawRsp ack{};ack.srcid=request.req.srcid;ack.tgtid=HN;ack.txnid=request.req.txnid;ack.opcode=2;ack.flow=request.req.flow;ack.flow.messageId.clear();
        require(cc.acceptRxRsp(ack).has_value(),"CompAck did not retire read");pair(RSP,ack);sample(request.req);return result;
    }
    void writeback(){
        auto r=make(0,0x1b);admit(r);until([&]{return cc.hasTxRsp();});auto response=cc.frontTxRsp().rsp;cc.popTxRsp();pair(RSP,response);
        for(unsigned offset:{0u,32u}){auto d=beat(r.req,offset,owner,2);d.dbid=response.dbid;d.resp=5;cc.acceptRxDat(d);pair(DAT,d);}
        until([&]{return cc.hasDeferredRetire();});cc.popDeferredRetire();sample(r.req);
        auto replacement=make(4,1,A+64);admit(replacement);require(completeRead(replacement)==memory,"Replacement read bytes changed");
        require(victimBytes==owner,"Dirty victim did not preserve RN write bytes");require(writes==1,"No downstream dirty write");
    }
    J run(const std::string& scenario){
        if(scenario=="unique"||scenario=="snoop-owner") {
            slcsf.commitRead(A,0,PocqTxnKind::ReadShared,memory,false,HN);
            if(scenario=="unique")slcsf.commitRead(A,4,PocqTxnKind::ReadShared,memory,false,HN);
            auto r=make(8,7);sample(r.req);admit(r);auto result=completeRead(r);
            require(result==(dirtySnoop?owner:memory),"Snoop bytes were overwritten");require(reads==0,"Unexpected memory read during snoop");
            require(snoops==(dirtySnoop?1u:2u),"Snoop target count changed");
        } else if(scenario=="writeback"||scenario=="retry")writeback();
        else {
            auto first=make(0,1);admit(first);
            auto second=make(0,1,A,scenario=="same-address"?1:0);
            if(scenario=="same-address")admit(second);
            require(completeRead(first)==memory,"Miss response bytes changed");
            if(scenario!="same-address")admit(second);
            require(completeRead(second)==memory,"Hit response bytes changed");require(reads==1,"Same-address hit issued another memory request");
        }
        return {{"tick",tick},{"reads",reads},{"writes",writes},{"snoops",snoops},{"stateA",state(A)},{"stateB",state(A+64)},{"data",received},{"victim",victimBytes}};
    }
};

TEST(FlowGraph, ComponentScenariosAndObserverEquivalence) {
    const char* dir=std::getenv("CHI_DEMO_DIR");require(dir,"Set CHI_DEMO_DIR to a writable output directory");std::filesystem::create_directories(dir);
    for(const std::string name:{"miss-hit","snoop-owner","unique","writeback","retry","same-address"}){
        SCOPED_TRACE(name);J baseline;
        {Harness disabled(name,"");baseline=disabled.run(name);}
        {Harness enabled(name,std::string(dir)+"/"+name+".coarse.jsonl");EXPECT_EQ(enabled.run(name),baseline);}
    }
}
TEST(FlowGraph, McReadUsesRealRetryFieldsAndNodePairCredits) {
    for (bool early : {false,true}) {
        Harness h("miss-hit","");
        const auto upstream=h.make(0,1);h.admit(upstream);
        h.until([&]{return h.cc.hasTxReq();});
        const auto first=h.cc.frontTxReq();h.cc.popTxReq();h.cc.notifyTxReqSent(first);
        ASSERT_EQ(first.req.AllowRetry,1);
        EXPECT_EQ(first.req.ReturnNid,HN);EXPECT_EQ(first.req.ReturnTxnId,first.req.txnid);
        EXPECT_EQ(first.req.memattr,upstream.req.memattr);EXPECT_EQ(first.req.snpattr,0);
        auto credit=h.response(first,7,0,7);credit.txnid=0xfefe;
        if(early)h.cc.acceptRxRsp(credit);
        auto retry=h.response(first,3,0,7);h.cc.acceptRxRsp(retry);
        if(!early) {
            ASSERT_FALSE(h.cc.hasTxReq());
            auto wrong=credit;wrong.pcrdtype=6;h.cc.acceptRxRsp(wrong);
            ASSERT_FALSE(h.cc.hasTxReq());
            wrong=credit;wrong.srcid=SN+4;h.cc.acceptRxRsp(wrong);
            ASSERT_FALSE(h.cc.hasTxReq());
            h.cc.acceptRxRsp(credit);
        }
        ASSERT_TRUE(h.cc.hasTxReq());
        const auto reissued=h.cc.frontTxReq();h.cc.popTxReq();h.cc.notifyTxReqSent(reissued);
        EXPECT_EQ(reissued.req.AllowRetry,0);EXPECT_EQ(reissued.req.pcrdtype,7);
        EXPECT_NE(reissued.req.txnid,first.req.txnid);
        EXPECT_EQ(reissued.req.ReturnTxnId,reissued.req.txnid);
        for(unsigned offset:{32u,0u})h.cc.acceptRxDat(h.beat(reissued.req,offset,h.memory));
        EXPECT_EQ(h.completeRead(upstream),h.memory);
    }
}
TEST(FlowGraph, CaptureWindowPreservesUnknownStateAndByteValidity) {
    const char* dir=std::getenv("CHI_DEMO_DIR");
    require(dir,"Set CHI_DEMO_DIR to a writable output directory");
    const auto path=std::filesystem::path(dir)/"capture-window.test.jsonl";
    auto& recorder=FlowGraphTrace::get();
    const uint64_t start=9007199254740993ull;
    J manifest={{"type","manifest"},{"complete",true},{"initialState",{{"HN0",{{"lines",J::object()}}}}},
        {"topology",J::array({{{"id","RN0"},{"nodeId",0}},{{"id","HN0"},{"nodeId",HN}},{{"id","SN0"},{"nodeId",SN}}})}};
    recorder.open(path.string(),manifest,{{"startTick",std::to_string(start)},{"endTick",std::to_string(start+2)},
        {"address",FlowGraphTrace::hex(A)},{"nodes",J::array({"HN0"})}});
    auto identity=recorder.root();identity.address=A;
    recorder.setComponentTick(start-1);recorder.event(identity,"allocate",HN);
    recorder.setComponentTick(start);recorder.event(identity,"allocate",HN);
    recorder.setComponentTick(start+1);recorder.event(identity,"allocate",SN);
    auto other=identity;other.address=A+64;recorder.event(other,"allocate",HN);
    RawDat beat{};beat.flow=identity;beat.srcid=SN;beat.tgtid=HN;beat.opcode=4;
    beat.data.assign(32,0xab);beat.chunkValid={1,0,1,0};beat.byteEnable.assign(32,0);beat.byteEnable[0]=1;
    recorder.stamp(beat);recorder.flit(DAT,beat,"TX");
    recorder.setComponentTick(start+3);recorder.event(identity,"complete",HN);recorder.close();
    std::ifstream stream(path);std::vector<J> records;std::string line;
    while(std::getline(stream,line))records.push_back(J::parse(line));
    ASSERT_EQ(records.size(),3u);
    EXPECT_FALSE(records[0]["complete"].get<bool>());EXPECT_TRUE(records[0]["initialState"].empty());
    EXPECT_EQ(records[1]["tick"],std::to_string(start));
    EXPECT_EQ(records[2]["tick"],std::to_string(start+1));
    EXPECT_EQ(records[2]["validMask"],std::string(16,'f')+std::string(16,'0')+std::string(16,'f')+std::string(16,'0'));
    EXPECT_EQ(records[2]["byteEnable"],"ff"+std::string(62,'0'));
    EXPECT_EQ(records[2]["payloadOrigin"],"unknown");
}
}
}

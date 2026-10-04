#include <algorithm>
#include <array>
#include <bit>
#include <cctype>
#include <deque>
#include <fstream>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>
#include <nlohmann/json.hpp>
#include <chi_eb/xact/chi_eb_xact_checkers_field.hpp>
#include <chi_eb/xact/chi_eb_joint.hpp>

using Json = nlohmann::ordered_json;
using Config = CHI::Eb::FlitConfiguration<11,52,0,0,256,false,false,false>;
namespace C = CHI::Eb;

static uint64_t integer(const Json& value) {
    if (value.is_number_unsigned()) return value.get<uint64_t>();
    if (value.is_number_integer() && value.get<int64_t>() >= 0) return value.get<uint64_t>();
    if (value.is_boolean()) return value.get<bool>() ? 1 : 0;
    if (!value.is_string()) throw std::runtime_error("Expected a non-negative integer");
    const auto text=value.get<std::string>(); size_t end=0;
    if (text.empty() || text[0]=='-' || std::isspace(static_cast<unsigned char>(text[0]))) throw std::runtime_error("Invalid integer: "+text);
    const auto result=std::stoull(text,&end,text.starts_with("0x")?16:10);
    if(end!=text.size()) throw std::runtime_error("Invalid integer: "+text);
    return result;
}
static Json field(Json value, const std::string& origin="modeled", const std::string& source="", const std::string& name="", const std::string& rule="B2.4") {
    Json f={{"value",std::move(value)},{"origin",origin},{"rule",rule}};
    if(!source.empty()) { f["sourceEventId"]=source; f["sourceField"]=name; }
    return f;
}
static std::string hex(uint64_t n) { std::ostringstream s;s<<"0x"<<std::hex<<n;return s.str(); }
static bool isHex(const std::string& s) { return s.size()%2==0 && std::all_of(s.begin(),s.end(),[](unsigned char c){return std::isxdigit(c);}); }

struct Pool {
    std::map<std::string,std::set<unsigned>> used;
    unsigned take(const std::string& node,unsigned avoid=0,unsigned preferred=0) {
        auto& ids=used[node];
        if(preferred&&preferred!=avoid&&!ids.contains(preferred)){ids.insert(preferred);return preferred;}
        for(unsigned i=1;i<4096;++i)if(i!=avoid&&!ids.contains(i)){ids.insert(i);return i;}
        throw std::runtime_error("Protocol ID space exhausted at "+node);
    }
    void release(const std::string& node,unsigned id){used[node].erase(id);}
};
struct Leg {
    Json anchor;
    std::string root,leg,attempt,requester,completer,requestEvent,dbidEvent,requestBinding,dbidBinding,opcode;
    unsigned txn=0,dbid=0,size=64,retryType=0;
    std::optional<uint64_t> observedDbid;
    std::map<std::string,uint64_t> dataFields;
    bool ack=false,write=false,snoop=false,retried=false,done=false,txnLive=false,dbidLive=false,completion=false,acked=false;
    bool expectsReceipt=false,receipt=false;
    std::set<unsigned> offsets;
    std::array<int,64> bytes;
    Leg(){bytes.fill(-1);}
};
class Refiner {
    Json manifest;
    std::vector<Json> observed, protocol, checks;
    std::map<std::string,unsigned> nodeId;
    std::map<std::string,std::string> role;
    std::map<std::string,Leg> legs;
    std::set<std::pair<std::string,std::string>> observedReceipts;
    std::map<std::string,std::vector<Json>> messageObservations;
    std::map<std::string,std::deque<std::string>> credits;
    std::set<std::string> processedMessages,unsupportedRoots;
    Pool txnIds, dbids;
    C::Xact::Global<Config> chironGlobal;
    std::map<std::string,C::Xact::RNFJoint<Config>> rnfJoints;
    std::map<std::string,C::Xact::SNFJoint<Config>> snfJoints;
    std::set<std::string> jointUnsupportedAttempts;
    uint64_t seq=0,validationSeq=0;
    std::map<uint64_t,unsigned> substeps;
    bool ordered=false,rotateRetryId=false,extended=false,native=false;
    const char* profileName() const {return native?"h-native-256":extended?"h-extended-256":"h-core-256";}
    static unsigned beats(const Leg& l) { return (l.opcode.starts_with("Read")||l.write||l.snoop)?(l.size<=32?1:2):0; }
    static std::string creditKey(const std::string& req,const std::string& comp,unsigned type) {return req+"|"+comp+"|"+std::to_string(type);}
    void check(const Json& e,const std::string& status,const std::string& rule,const std::string& message,const std::string& engine="Issue H") {
        checks.push_back({{"type","validation"},{"id","v:"+std::to_string(++validationSeq)},{"engine",engine},{"status",status},{"rule",rule},{"message",message},{"eventIds",Json::array({e.at("id")})},{"rootId",e.value("rootId","")}});
    }
    Json event(const Json& e,const std::string& channel,const std::string& opcode,const std::string& kind="flit",const std::string& rule="B2.4") {
        Json sources=Json::array({e.at("id")});
        const auto message=e.value("messageId","");
        if(!message.empty()) {sources=Json::array();for(const auto& peer:messageObservations[message])sources.push_back(peer.at("id"));}
        auto anchor=e.at("seq").get<uint64_t>();
        Json p={{"type","protocol"},{"id","p:"+std::to_string(++seq)},{"seq",seq},{"kind",kind},
            {"rootId",e.at("rootId")},{"legId",e.at("legId")},{"attemptId",e.value("attemptId","0")},
            {"parentLegId",e.value("parentLegId","")},{"source",e.value("source",e.value("node",""))},{"target",e.value("target","")},
            {"channel",channel},{"opcode",opcode},{"fields",Json::object()},
            {"sourceEventIds",sources},{"dependencies",Json::array()},
            {"anchorTick",e.at("tick")},{"anchorSeq",anchor},{"substep",++substeps[anchor]},
            {"origin","derived"},{"rule",rule}};
        if(e.contains("address"))p["address"]=e["address"];
        for(const auto* key:{"causeInstructionIds","dataProducerInstructionIds","messageId"})if(e.contains(key))p[key]=e[key];
        return p;
    }
    std::string binding(const Json& e,Leg& l,const std::string& name,unsigned value,const std::string& action) {
        auto p=event(e,"RSP",action+name,"binding");
        p["description"]=action+" "+name+"; namespace="+(name=="TxnID"?l.requester:l.completer)+"; leg="+l.leg+"; attempt="+l.attempt;
        p["fields"][name]=field(value);p["fields"]["Action"]=field(action);p["fields"]["Namespace"]=field(name=="TxnID"?l.requester:l.completer);
        protocol.push_back(p);return p["id"];
    }
    void releaseTxn(const Json& e,Leg& l) {
        if(l.txnLive){binding(e,l,"TxnID",l.txn,"release");txnIds.release(l.requester,l.txn);l.txnLive=false;}
    }
    void finish(const Json& e,Leg& l) {
        if(l.done||(l.expectsReceipt&&!l.receipt))return;
        releaseTxn(e,l);
        if(l.dbidLive){binding(e,l,"DBID",l.dbid,"release");dbids.release(l.completer,l.dbid);l.dbidLive=false;}
        auto p=event(e,"RSP","Complete","complete");protocol.push_back(p);l.done=true;
    }
    void allocateDbid(const Json& e,Leg& l) {
        if(!l.dbid){l.dbid=dbids.take(l.completer);l.dbidLive=true;l.dbidBinding=binding(e,l,"DBID",l.dbid,"allocate");}
    }
    uint64_t raw(const Json& e,const std::string& key,uint64_t fallback=0) const {
        if(!e.contains("fields")||!e["fields"].contains(key))return fallback;
        return integer(e["fields"][key]);
    }
    void observedField(Json& p,const Json& e,const std::string& output,const std::string& input,uint64_t fallback=0) {
        const bool exists=e.contains("fields")&&e["fields"].contains(input);
        p["fields"][output]=field(raw(e,input,fallback),exists?"observed":"modeled",exists?e.at("id").get<std::string>():"",input);
    }
    static uint64_t value(const Json& p,const std::string& name) {
        return p["fields"].contains(name) && !p["fields"][name]["value"].is_null()?integer(p["fields"][name]["value"]):0;
    }
    void chiron(const Json& p) {
        const std::map<std::string,unsigned> widths={{"QoS",4},{"SrcID",11},{"TgtID",11},{"ReturnNID",11},{"HomeNID",11},{"TxnID",12},{"ReturnTxnID",12},{"DBID",12},{"PCrdType",4},{"Size",3},{"Order",2},{"AllowRetry",1},{"MemAttr",4},{"SnpAttr",1},{"ExpCompAck",1},{"Resp",3},{"RespErr",2},{"DataID",2},{"BE",32}};
        for (const auto& [name,width]:widths) if(p["fields"].contains(name) && value(p,name)>=(1ull<<width)) {
            check(p,"fail","field-width",name+" exceeds its configured CHI field width; value was not silently truncated.");
            return;
        }
        C::Xact::XactDenialEnum denial=C::Xact::XactDenial::ACCEPTED;
        const auto channel=p.at("channel").get<std::string>();
        const auto op=value(p,"Opcode");
        const std::string src=p["source"],dst=p["target"];
        // CHIron's ordering argument is a logical ordinal, never a fabricated tick.
        const auto ordinal=integer(p["seq"]);
        const bool jointSupported=!native&&!jointUnsupportedAttempts.contains(p.at("legId").get<std::string>()+":"+p.value("attemptId","0"));
        const auto jointResult=[&](C::Xact::XactDenialEnum result,const std::string& node) {
            check(p,result==C::Xact::XactDenial::ACCEPTED?"pass":"fail","transaction-joint",
                node+": "+result->name+"（CHIron E.b 事务关联；时间参数为逻辑步骤）","CHIron E.b");
        };
        if(channel=="REQ") {
            C::Flits::REQ<Config> f{};
            f.Opcode()=op;f.SrcID()=value(p,"SrcID");f.TgtID()=value(p,"TgtID");f.TxnID()=value(p,"TxnID");
            f.Addr()=value(p,"Addr");f.Size()=value(p,"Size");f.QoS()=value(p,"QoS");f.AllowRetry()=value(p,"AllowRetry");
            f.PCrdType()=value(p,"PCrdType");f.MemAttr()=value(p,"MemAttr");f.SnpAttr()=value(p,"SnpAttr");
            f.ExpCompAck()=value(p,"ExpCompAck");f.Order()=value(p,"Order");f.ReturnNID()=value(p,"ReturnNID");f.ReturnTxnID()=value(p,"ReturnTxnID");f.LikelyShared()=value(p,"LikelyShared");if(p["opcode"].get<std::string>().starts_with("StashOnce")){f.StashNID()=value(p,"StashNID");f.StashNIDValid()=value(p,"StashNIDValid");}
            denial=C::Xact::RequestFieldMappingChecker<Config>{}.Check(f);
            if(denial==C::Xact::XactDenial::ACCEPTED&&jointSupported) {
                if(role[src]=="RN-F")jointResult(rnfJoints[src].NextTXREQ(chironGlobal,ordinal,f),src);
                if(role[dst]=="SN-F")jointResult(snfJoints[dst].NextRXREQ(chironGlobal,ordinal,f),dst);
            }
        } else if(channel=="RSP") {
            C::Flits::RSP<Config> f{};
            f.Opcode()=op;f.SrcID()=value(p,"SrcID");f.TgtID()=value(p,"TgtID");f.TxnID()=value(p,"TxnID");
            f.DataPull()=value(p,"DataPull");f.DBID()=value(p,"DBID");f.PCrdType()=value(p,"PCrdType");f.Resp()=value(p,"Resp");f.RespErr()=value(p,"RespErr");f.QoS()=value(p,"QoS");
            denial=C::Xact::ResponseFieldMappingChecker<Config>{}.Check(f);
            if(denial==C::Xact::XactDenial::ACCEPTED&&jointSupported) {
                if(role[src]=="RN-F")jointResult(rnfJoints[src].NextTXRSP(chironGlobal,ordinal,f),src);
                if(role[dst]=="RN-F")jointResult(rnfJoints[dst].NextRXRSP(chironGlobal,ordinal,f),dst);
                if(role[src]=="SN-F")jointResult(snfJoints[src].NextTXRSP(chironGlobal,ordinal,f),src);
            }
        } else if(channel=="DAT") {
            C::Flits::DAT<Config> f{};
            f.Opcode()=op;f.SrcID()=value(p,"SrcID");f.TgtID()=value(p,"TgtID");f.TxnID()=value(p,"TxnID");
            f.DBID()=value(p,"DBID");f.HomeNID()=value(p,"HomeNID");f.DataID()=value(p,"DataID");
            f.Resp()=value(p,"Resp");f.RespErr()=value(p,"RespErr");f.QoS()=value(p,"QoS");f.BE()=value(p,"BE");
            const auto payload=p.value("payload","");
            for(size_t i=0;i<payload.size()/2;++i) f.Data()[i/8] |= uint64_t(std::stoul(payload.substr(i*2,2),nullptr,16))<<((i%8)*8);
            denial=C::Xact::DataFieldMappingChecker<Config>{}.Check(f);
            if(denial==C::Xact::XactDenial::ACCEPTED&&jointSupported) {
                if(role[src]=="RN-F")jointResult(rnfJoints[src].NextTXDAT(chironGlobal,ordinal,f),src);
                if(role[dst]=="RN-F")jointResult(rnfJoints[dst].NextRXDAT(chironGlobal,ordinal,f),dst);
                if(role[src]=="SN-F")jointResult(snfJoints[src].NextTXDAT(chironGlobal,ordinal,f),src);
                if(role[dst]=="SN-F")jointResult(snfJoints[dst].NextRXDAT(chironGlobal,ordinal,f),dst);
            }
        } else {
            C::Flits::SNP<Config> f{};
            f.Opcode()=op;f.SrcID()=value(p,"SrcID");f.TxnID()=value(p,"TxnID");f.Addr()=value(p,"Addr");
            f.RetToSrc()=value(p,"RetToSrc");f.DoNotGoToSD()=value(p,"DoNotGoToSD");f.QoS()=value(p,"QoS");
            denial=C::Xact::SnoopFieldMappingChecker<Config>{}.Check(f);
            if(denial==C::Xact::XactDenial::ACCEPTED&&jointSupported&&role[dst]=="RN-F")jointResult(rnfJoints[dst].NextRXSNP(chironGlobal,ordinal,f,nodeId.at(dst)),dst);
        }
        if(native && channel=="RSP" && op==2 && value(p,"Resp")!=0 &&
           std::string(denial->name)=="XACT_DENIED_RSP_FIELD_RESP") {
            check(p,"unsupported","field-mapping",
                "Pinned CHIron E.b requires CompAck.Resp=0; Issue H CopyBack CompAck has applicable cache state (B4.35/B4.44). Original Resp retained; check-native-wire.py checks the request/response association and legal state.","CHIron E.b");
        } else check(p,denial==C::Xact::XactDenial::ACCEPTED?"pass":"fail","field-mapping",std::string(denial->name)+" (E.b field mapping only; not complete Issue H validation)","CHIron E.b");
    }
    void push(Json p,unsigned opcode) {
        p["fields"]["Opcode"]=field(opcode,"derived",p["sourceEventIds"][0],"opcode");
        p["fields"]["SrcID"]=field(nodeId.at(p["source"]),"derived",p["sourceEventIds"][0],"source");
        if(p["channel"]!="SNP")p["fields"]["TgtID"]=field(nodeId.at(p["target"]),"derived",p["sourceEventIds"][0],"target");
        // Every checked zero is a documented profile default, rather than an
        // unstated assumption about an absent observed wire field.
        if (!p.contains("description")) p["description"]=std::string(profileName())+" 有限协议展开；未列出的可选及保留字段按 0 建模。";
        chiron(p);protocol.push_back(std::move(p));
    }
    static unsigned opcodeCode(const std::string& op) {
        static const std::map<std::string,unsigned> codes={{"ReadShared",1},{"ReadOnce",3},{"ReadNoSnp",4},{"ReadUnique",7},{"ReadNotSharedDirty",0x26},{"ReadOnceCleanInvalid",0x24},{"ReadOnceMakeInvalid",0x25},{"CleanShared",8},{"CleanInvalid",9},{"MakeInvalid",10},{"MakeUnique",12},{"Evict",13},{"WriteEvictFull",0x15},{"WriteUniquePtl",0x18},{"StashOnceShared",0x22},{"StashOnceUnique",0x23},
            {"CleanUnique",0x0b},{"WriteEvictOrEvict",0x42},{"WriteUniqueFull",C::Opcodes::REQ::WriteUniqueFull},{"WriteNoSnpFull",C::Opcodes::REQ::WriteNoSnpFull},{"WriteBackFull",C::Opcodes::REQ::WriteBackFull},{"WriteCleanFull",C::Opcodes::REQ::WriteCleanFull},{"WriteNoSnpPtl",C::Opcodes::REQ::WriteNoSnpPtl},
            {"SnpShared",1},{"SnpClean",2},{"SnpOnce",3},{"SnpNotSharedDirty",4},{"SnpUnique",7},{"SnpCleanShared",8},{"SnpStashUnique",0xb},{"SnpStashShared",0xc},{"SnpCleanInvalid",9},{"SnpMakeInvalid",0xa},
            {"SnpResp",1},{"CompAck",2},{"RetryAck",3},{"Comp",4},{"CompDBIDResp",5},{"DBIDResp",6},{"PCrdGrant",7},{"ReadReceipt",8},
            {"SnpRespData",1},{"CopyBackWriteData",2},{"NonCopyBackWriteData",3},{"CompData",4}};
        return codes.at(op);
    }
    void request(const Json& e) {
        const std::string leg=e["legId"],attempt=e.value("attemptId","0"),op=e["opcode"],src=e["source"],dst=e["target"];
        std::string credit;
        unsigned priorTxn=0;
        bool retainedTxn=false;
        if(e["channel"]=="REQ"&&(!e.contains("fields")||!e["fields"].contains("allowRetry"))) {
            check(e,"insufficient","B2.10","AllowRetry 未被采集；请在 gem5 请求构造与导出接点补齐，保留原始事务。");
            unsupportedRoots.insert(e["rootId"]);return;
        }
        if(auto it=legs.find(leg);it!=legs.end()) {
            auto& prior=it->second;
            priorTxn=prior.txn;
            retainedTxn=extended&&prior.txnLive;
            if(!prior.retried || attempt==prior.attempt || src!=prior.requester || dst!=prior.completer) {check(e,"fail","B2.4","Stable leg/attempt reused without an explicitly rejected attempt.");return;}
            const unsigned type=raw(e,"pcrdtype");
            auto& bank=credits[creditKey(src,dst,type)];
            if(type!=prior.retryType||raw(e,"allowRetry",1)!=0||bank.empty()){check(e,"fail","B2.10","Retry requires matching requester/completer/PCrdType, RetryAck and available P-Credit.");return;}
            credit=bank.front();bank.pop_front();
        } else if(e["channel"]=="REQ"&&raw(e,"allowRetry")==0) {
            check(e,manifest.value("complete",false)?"fail":"insufficient","B2.10.2","AllowRetry=0 需要本事务先前的 RetryAck 和适合的 P-Credit；预先收到信用不能替代 RetryAck。保留原始事务。");
            unsupportedRoots.insert(e["rootId"]);return;
        }
        Leg l;l.anchor=e;l.root=e["rootId"];l.leg=leg;l.attempt=attempt;l.requester=src;l.completer=dst;l.opcode=op;
        l.snoop=e["channel"]=="SNP";l.write=op.starts_with("Write");
        l.size=static_cast<unsigned>(raw(e,"sizeBytes",64));
        if((!extended&&l.size!=64)||!l.size||l.size>64||(l.size&(l.size-1))){check(e,"unsupported",profileName(),extended?"Access size must be a power of two in [1, 64] bytes; original events retained.":"This profile currently expands only aligned 64-byte requests; original events retained.");unsupportedRoots.insert(l.root);return;}
        if(!e.contains("address")||!e["address"].is_string()){check(e,"insufficient","B2.4","Missing string physical address.");return;}
        const auto address=integer(e["address"]);
        if(address>= (1ull<<52) || (extended?(address%l.size||(address%64)+l.size>64):address%64)){check(e,"unsupported",profileName(),"Address exceeds the configured 52-bit CHIron field or selected access alignment; raw string preserved.");unsupportedRoots.insert(l.root);return;}
        const bool mandatoryAck=op=="ReadShared"||op=="ReadUnique"||op=="ReadNotSharedDirty"||op=="MakeUnique"||op=="CleanUnique";
        l.ack=!l.snoop&&raw(e,"expCompAck",mandatoryAck?1:0)!=0;
        if(mandatoryAck&&!l.ack)check(e,"fail","B2.7.3","ReadShared/ReadUnique require ExpCompAck=1 and CompAck.");
        if(l.write&&l.ack) {check(e,"unsupported",profileName(),"Streaming ordered writes with CompAck are outside this finite profile; original transaction retained.");unsupportedRoots.insert(l.root);return;}
        if(txnIds.used[src].size()>=1024) {check(e,"unsupported","B2.4.2","This profile supports at most 1024 simultaneously allocated request IDs per node; original transaction retained.");unsupportedRoots.insert(l.root);return;}
        l.txn=retainedTxn?priorTxn:txnIds.take(src,rotateRetryId?priorTxn:0,rotateRetryId?0:priorTxn);l.txnLive=true;l.requestBinding=binding(e,l,"TxnID",l.txn,retainedTxn?"retain-for-retry":"allocate");
        if(priorTxn&&l.txn!=priorTxn) {
            jointUnsupportedAttempts.insert(leg+":"+attempt);
            check(e,"unsupported","transaction-joint","固定 CHIron 版本按 TxnID 匹配重试，不能检查重发换 ID；本项目仍按 leg/attempt 与 P-Credit 验证。","CHIron E.b");
        }
        auto p=event(e,e["channel"],op,"flit",l.snoop?"B2.5.1":"B2.4");
        auto& f=p["fields"]; f["TxnID"]=field(l.txn,"modeled",l.requestBinding,"TxnID");
        f["Addr"]=field(hex(l.snoop?address>>3:address),"derived",e["id"],"address");
        observedField(p,e,"QoS","qos");
        if(l.snoop) {f["RetToSrc"]=field(op=="SnpCleanShared"||op=="SnpCleanInvalid"||op=="SnpMakeInvalid"||op.starts_with("SnpStash")?0:1);f["DoNotGoToSD"]=field(op=="SnpUnique"||op=="SnpCleanShared"||op=="SnpCleanInvalid"||op=="SnpMakeInvalid"||op.starts_with("SnpStash")?1:0);p["description"]="SNP 目标属于拓扑路由信息，flit 无 TgtID 字段。SNP.Addr = PA >> 3。";}
        else {
            f["Size"]=field(std::countr_zero(l.size),"derived",e["id"],"sizeBytes","B2.9");
            observedField(p,e,"AllowRetry","allowRetry");
            observedField(p,e,"PCrdType","pcrdtype");
            const bool coherent=op!="ReadNoSnp"&&!op.starts_with("WriteNoSnp");
            observedField(p,e,"MemAttr","memattr",coherent?13:0);observedField(p,e,"SnpAttr","snpattr",coherent?1:0);
            observedField(p,e,"ExpCompAck","expCompAck",l.ack?1:0);
            const bool downstream=role[src]=="HN-F"&&role[dst]=="SN-F";
            if(op.starts_with("StashOnce")){observedField(p,e,"StashNID","stashNid");f["StashNIDValid"]=field(1);}
            if(raw(e,"allowRetry")&&raw(e,"pcrdtype"))check(e,"fail","B2.10.2.2","AllowRetry=1 requires PCrdType=0.");
            if(downstream&&l.ack)check(e,"fail","B2.7.3","HN-F -> SN-F must not request or send CompAck.");
            const auto parent=e.value("parentLegId","");
            if(downstream&&!parent.empty()&&legs.contains(parent)&&!l.write&&value(p,"MemAttr")!=raw(legs.at(parent).anchor,"memattr",legs.at(parent).opcode=="ReadNoSnp"?0:13))
                check(e,"fail","B2.8.3.5","Downstream memory attributes must preserve the upstream request.");
            if(downstream&&l.write&&!parent.empty()&&!(legs.contains(parent)&&legs.at(parent).opcode.starts_with("WriteNoSnp"))&&value(p,"MemAttr")!=13)
                check(e,"fail","B2.8.3.5","System-cache eviction requires EWA/Cacheable/Allocate=1 and Device=0.");
            const bool receipt=ordered&&op=="ReadNoSnp"&&role[src]=="HN-F"&&role[dst]=="SN-F";
            const auto order=receipt?1:raw(e,"order");
            l.expectsReceipt=downstream&&op=="ReadNoSnp"&&order==1;
            if(order>3||(order==1&&!downstream)||(downstream&&order>1)||(order&&op!="ReadNoSnp"&&op!="ReadOnce"&&!op.starts_with("WriteNoSnp")))check(e,"fail","B2.7.5","Invalid Order for this opcode/node pair; HN-F -> SN-F permits only 00 or 01.");
            observedField(p,e,"Order","order");
            if(receipt)f["Order"]=field(order,"modeled","","","B2.7.5");
            f["ReturnNID"]=field(downstream?nodeId[src]:0,"derived",e["id"],"source","B2.4.10");
            f["ReturnTxnID"]=field(downstream&&op=="ReadNoSnp"?l.txn:0,"derived",l.requestBinding,"TxnID","B2.4.4");
            if(!credit.empty())p["dependencies"].push_back(credit);
        }
        if(extended && (op=="WriteEvictFull"||op.starts_with("Stash")||op.starts_with("SnpStash")||op=="SnpCleanShared"))jointUnsupportedAttempts.insert(leg+":"+attempt);
        l.requestEvent=p["id"];push(p,opcodeCode(op));legs[leg]=l;
        const bool hasObservedReceipt=observedReceipts.contains({leg,attempt});
        if(ordered&&op=="ReadNoSnp"&&role[src]=="HN-F"&&role[dst]=="SN-F"&&!hasObservedReceipt) {
            auto r=event(e,"RSP","ReadReceipt","flit","B2.7.5");r["source"]=dst;r["target"]=src;r["origin"]="modeled";
            r["fields"]["TxnID"]=field(l.txn,"derived",l.requestEvent,"TxnID");r["fields"]["DBID"]=field(0,"modeled","","","B2.5.1");r["fields"]["DBID"]["applicable"]=false;
            r["dependencies"].push_back(l.requestEvent);r["description"]="显式有序读配置补出的 ReadReceipt；只表示请求锚点后的逻辑顺序。DBID 不适用，置 0。";
            push(r,8);
            legs[leg].receipt=true;
        }
    }
    void response(const Json& e,Leg& l) {
        const std::string op=e["opcode"];
        if(op=="Comp"&&(l.opcode=="WriteBackFull"||l.opcode=="WriteCleanFull")&&l.dbidEvent.empty()) {
            check(e,"unsupported",profileName(),"CopyBack completion without data requires a different CompAck flow; original transaction retained.");unsupportedRoots.insert(l.root);return;
        }
        if ((op=="SnpResp"&&!l.snoop) || ((op=="DBIDResp"||op=="CompDBIDResp")&&!l.write)) {
            check(e,"fail","B2.5","Response opcode is not valid for this explicit transaction kind.");return;
        }
        if ((op=="DBIDResp"||op=="CompDBIDResp"||(op=="Comp"&&l.write))&&e.contains("fields")&&e["fields"].contains("dbid")) {
            if(l.observedDbid&&*l.observedDbid!=raw(e,"dbid")){check(e,"fail","B2.5.3","Observed DBID changed within one write attempt.");return;}
            l.observedDbid=raw(e,"dbid");
        }
        if(op=="DBIDResp"||op=="CompDBIDResp"||(op=="Comp"&&(l.write||l.ack)))allocateDbid(e,l);
        auto p=event(e,"RSP",op,"flit",op=="RetryAck"?"B2.10":l.write?"B2.5.3":"B2.5.1");
        auto& f=p["fields"];observedField(p,e,"QoS","qos");observedField(p,e,"RespErr","respErr");
        f["TxnID"]=field(l.txn,"derived",l.requestEvent,"TxnID");f["DBID"]=field(0);
        if(op=="CompAck") {
            if(!l.dbid || !l.ack){check(e,"fail","B2.5.1","CompAck has no allocated completion DBID or is not expected.");return;}
            f["TxnID"]=field(l.dbid,"derived",l.dbidEvent,"DBID");p["dependencies"].push_back(l.dbidEvent);l.acked=true;
            if(l.offsets.size()!=beats(l))check(e,"fail","B2.9.4","CompAck precedes complete data coverage.");
        } else if(op=="DBIDResp"||op=="CompDBIDResp"||(op=="Comp"&&(l.write||l.ack))) {
            f["DBID"]=field(l.dbid,"modeled",l.dbidBinding,"DBID");
            if(op!="Comp"||l.ack)l.dbidEvent=p["id"];
            if(op!="DBIDResp")l.completion=true;
        } else if(op=="RetryAck") {
            if(l.retried||l.completion||!raw(l.anchor,"allowRetry",1)){check(e,"fail","B2.10","RetryAck is illegal for this attempt.");return;}
            l.retried=true;l.retryType=raw(e,"pcrdtype");observedField(p,e,"PCrdType","pcrdtype");
        } else if(op=="SnpResp") { observedField(p,e,"Resp","resp"); l.completion=true;
            if(raw(e,"dataPull")) {
                f["Resp"]=field(raw(e,"resp")&7,"derived",e["id"],"resp");
                f["DataPull"]=field(1,"observed",e["id"],"dataPull");
                const auto pull=e["fields"].value("pullLegId","");
                if(legs.contains(pull))f["DBID"]=field(legs.at(pull).txn,"derived",legs.at(pull).requestBinding,"TxnID");
                else check(e,"insufficient","stash-data-pull","Missing implicit read allocation; not inferred from address.");
            }
        } else if(op=="ReadReceipt") {
            f["DBID"]["applicable"]=false;
            if(raw(l.anchor,"order")==0&&!ordered)check(e,"fail","B2.7.5","ReadReceipt observed for unordered request.");
            if(l.receipt)check(e,"fail","B2.7.5","Duplicate ReadReceipt for one attempt.");
            l.receipt=true;
        } else if(op=="Comp")l.completion=true;
        if(op=="Comp")observedField(p,e,"Resp","resp");
        p["dependencies"].push_back(l.requestEvent);push(p,opcodeCode(op));
        if(l.write&&l.completion&&!l.dbidEvent.empty())releaseTxn(e,l);
        if(op=="RetryAck"&&!extended)releaseTxn(e,l);
        else if(op=="CompAck"||op=="SnpResp"||(op=="ReadReceipt"&&l.completion&&!l.ack)||(op=="Comp"&&beats(l)==0&&!l.ack)||(l.write&&l.completion&&l.offsets.size()==beats(l)&&!l.ack))finish(e,l);
    }
    void data(const Json& e,Leg& l) {
        const std::string op=e["opcode"];
        if ((l.write&&op!="CopyBackWriteData"&&op!="NonCopyBackWriteData") ||
            (l.snoop&&op!="SnpRespData") || (!l.write&&!l.snoop&&op!="CompData")) {
            check(e,"fail","B2.5","Data opcode is not valid for this explicit transaction kind.");return;
        }
        if(l.write&&((l.opcode=="WriteBackFull"||l.opcode=="WriteCleanFull"||l.opcode=="WriteEvictFull")!=(op=="CopyBackWriteData"))) {
            check(e,"fail","B2.5.3","CopyBack and Immediate writes require their respective DAT opcode.");return;
        }
        // B2.9.4 permits QoS and RespErr to vary, but these observed fields must
        // remain stable across the packets of a single DAT message.
        for(const auto* name:{"txnid","HomeNID","resp","fwdState","dataPull","dataSource","ccid"}) {
            if(!e.contains("fields")||!e["fields"].contains(name))continue;
            const auto v=raw(e,name);
            if(l.dataFields.contains(name)&&l.dataFields[name]!=v)check(e,"fail","B2.9.4",std::string("DAT packets disagree in ")+name);
            else l.dataFields[name]=v;
        }
        if(l.ack&&e.contains("fields")&&e["fields"].contains("dbid")) {
            const auto v=raw(e,"dbid");
            if(l.dataFields.contains("dbid")&&l.dataFields["dbid"]!=v)check(e,"fail","B2.9.4","CompData packets disagree in applicable DBID.");
            else l.dataFields["dbid"]=v;
        }
        if(l.write&&l.observedDbid&&e.contains("fields")&&e["fields"].contains("dbid")&&raw(e,"dbid")!=*l.observedDbid) {
            check(e,"fail","B2.5.3","Coarse write DAT DBID does not match its explicit DBID response.");return;
        }
        if(l.write&&l.dbidEvent.empty()){check(e,"fail","B2.5.3","Write DAT precedes DBID response; cannot invent a DBID handshake.");return;}
        if((l.write||l.snoop)&&!e.contains("byteEnable")) {
            check(e,"insufficient","B2.9.3","Write/snoop BE was not captured; unknown enables cannot be treated as all zero. Original transaction retained.");
            unsupportedRoots.insert(l.root);return;
        }
        if(op=="CompData"&&l.ack)allocateDbid(e,l);
        std::string payload=e.value("payload","");
        size_t length=payload.empty()?32:payload.size()/2;
        if(!isHex(payload)|| (length!=32&&length!=64&&!extended)){check(e,"fail","B2.9.4","Expected 32 or 64 bytes of complete hexadecimal data.");return;}
        if(!e.contains("fields")||!e["fields"].contains("beatOffset")){check(e,"insufficient","B2.9.4","Missing beatOffset; coarse dataid is not assumed to be a CHI DataID.");return;}
        auto offset=raw(e,"beatOffset");
        std::string mask=e.value("validMask",std::string(length*2,'0')),be=e.value("byteEnable",std::string(length*2,'0'));
        if(extended && l.size<=32) {
            const auto absolute=offset+(l.write?0:integer(l.anchor["address"])%64);
            const auto padding=absolute%32;
            if(padding+length>32){check(e,"fail","B2.9.4","Small transfer crosses its 256-bit packet");return;}
            const auto pad=[&](std::string v){return std::string(padding*2,'0')+v+std::string((32-padding-length)*2,'0');};
            payload=pad(payload);mask=pad(mask);be=pad(be);length=32;offset=absolute-padding;
        }
        if(offset%32 || offset+length>64){check(e,"fail","B2.9.4","Invalid or overlapping DAT beat range.");return;}
        if(!isHex(mask)||!isHex(be)||mask.size()!=length*2||be.size()!=length*2){check(e,"fail","B2.9","Mask length/encoding does not match payload.");return;}
        for(size_t b=0;b<length;++b)for(const auto* m:{&mask,&be}) {
            const auto byte=m->substr(b*2,2);
            if(byte!="00"&&byte!="ff"&&byte!="FF"){check(e,"fail","B2.9","Byte masks must use 00 or ff per byte.");return;}
        }
        if(l.write||l.snoop) {
            const bool invalidCopy=op=="CopyBackWriteData"&&raw(e,"resp")==0;
            const bool full=l.opcode.ends_with("Full")||op=="SnpRespData";
            for(size_t b=0;b<length;++b) {
                const bool enabled=be.substr(b*2,2)!="00";
                if((invalidCopy&&enabled)||(!invalidCopy&&full&&!enabled)) {check(e,"fail","B2.9.3","Invalid BE for a full transfer or CopyBackWriteData_I.");return;}
                if(!enabled&&!payload.empty()&&mask.substr(b*2,2)!="00"&&payload.substr(b*2,2)!="00") {check(e,"fail","B2.9.3","Write/snoop data bytes with BE=0 must be zero.");return;}
            }
        }
        if(payload.empty()||mask.find_first_not_of('0')==std::string::npos)check(e,"insufficient","payload","Payload bytes are unknown; no zero-filled data is inferred.");
        for(size_t pos=0;pos<length;pos+=32) {
            const auto start=static_cast<unsigned>(offset+pos);
            if(!l.offsets.insert(start).second)check(e,"fail","B2.9.4","Duplicate DAT beat offset "+std::to_string(start));
            auto p=event(e,"DAT",op,"flit",l.write?"B2.5.3":"B2.9.4");auto& f=p["fields"];
            f["TxnID"]=field(l.write?l.dbid:l.txn,"derived",l.write?l.dbidEvent:l.requestEvent,l.write?"DBID":"TxnID");
            f["DataID"]=field(start/16,"derived",e["id"],"beatOffset","B2.9.4");
            f["DBID"]=field(op=="CompData"&&l.ack?l.dbid:0,"modeled",op=="CompData"&&l.ack?l.dbidBinding:"","DBID");
            f["HomeNID"]=field(op=="CompData"?nodeId[l.completer]:0,"modeled");
            observedField(p,e,"QoS","qos");observedField(p,e,"Resp","resp");observedField(p,e,"RespErr","respErr");
            uint32_t enables=0;
            for(size_t b=0;b<32;++b){if(be.substr((pos+b)*2,2)!="00")enables|=1u<<b;
                if(!payload.empty()&&mask.substr((pos+b)*2,2)!="00"&&((!l.write&&!l.snoop)||be.substr((pos+b)*2,2)!="00"))l.bytes[start+b]=std::stoi(payload.substr((pos+b)*2,2),nullptr,16);}
            f["BE"]=field(hex(enables),"derived",e["id"],"byteEnable","B2.9");
            f["BE"]["applicable"]=l.write||l.snoop;
            if(!payload.empty())p["payload"]=payload.substr(pos*2,64);
            p["validMask"]=mask.substr(pos*2,64);p["byteEnable"]=be.substr(pos*2,64);p["payloadOrigin"]=e.value("payloadOrigin","unknown");
            p["dependencies"].push_back(l.write?l.dbidEvent:l.requestEvent);
            if(op=="CompData"&&l.dbidEvent.empty()&&l.ack)l.dbidEvent=p["id"];
            push(p,opcodeCode(op));
        }
        if(l.offsets.size()==beats(l)) {
            l.completion = l.write?l.completion:true;
            if(!l.ack&&l.completion)finish(e,l);
            else if(l.ack&&l.acked&&l.completion)finish(e,l);
        }
    }
    void runNative() {
        // No Pool, binding generation, opcode conversion, beat splitting, or
        // causal reordering is permitted here. Only endpoint TX is displayed;
        // all actual RX observations remain attached to their transmitted flit.
        bool jointNotice=false;
        const std::map<std::string,unsigned> widths={{"QoS",4},{"SrcID",11},{"TgtID",11},
            {"TxnID",12},{"DBID",12},{"ReturnNID",11},{"ReturnTxnID",12},{"StashNID",11},
            {"HomeNID",11},{"Size",3},{"Order",2},{"AllowRetry",1},{"PCrdType",4},
            {"MemAttr",4},{"SnpAttr",1},{"ExpCompAck",1},{"Resp",3},{"RespErr",2},
            {"DataID",2},{"RetToSrc",1},{"DoNotGoToSD",1},{"LikelyShared",1},{"CAH",1}};
        for(const auto& e:observed) {
            if(e.value("kind","")!="flit")continue;
            if(e.value("wireEncoding","")!="native-chi") {
                check(e,"fail","native-source","Native capture contains a coarse/undeclared flit; no correction or expansion was applied.");continue;
            }
            const auto observation=e.value("observation","");
            if(observation!="TX"&&observation!="RX") {
                check(e,"fail","native-source","Native flit must be observed at an actual TX or RX endpoint boundary.");continue;
            }
            if(observation=="RX")continue;
            const auto message=e.value("messageId","");
            if(message.empty()) {check(e,"fail","native-source","Native TX has no stable message identity.");continue;}
            if(!processedMessages.insert(message).second) {check(e,"fail","native-source","Duplicate native TX message identity.");continue;}
            if(!nodeId.contains(e.value("source",""))||!nodeId.contains(e.value("target",""))) {
                check(e,"insufficient","topology","Native endpoint absent from topology; raw event retained.");continue;
            }
            const std::string channel=e.at("channel"),op=e.at("opcode");
            if(channel!="REQ"&&channel!="RSP"&&channel!="DAT"&&channel!="SNP") {
                check(e,"fail","channel-opcode","Invalid native CHI channel; raw event retained.");continue;
            }
            auto p=event(e,channel,op,"flit","B13.9");p["origin"]="observed";
            p["wireEncoding"]="native-chi";
            p["description"]="原生 CHI 端点实测发送；字段、ID、拍和数据保持原值。未采集的固定配置字段明确标为建模 0；事务检查以独立原始轨迹 gate 为准。";
            bool complete=true;
            const auto copy=[&](const std::string& output,const std::string& input,bool required=true) {
                if(e.contains("fields")&&e["fields"].contains(input))
                    p["fields"][output]=field(e["fields"][input],"observed",e.at("id"),input,"B13.9");
                else if(required) {
                    p["fields"][output]=field(nullptr,"unknown","",input,"B13.9");
                    check(e,"insufficient","native-field","Required native field not captured: "+input+"; no replacement value supplied.");complete=false;
                } else p["fields"][output]=field(0,"modeled","","h-native-256 fixed profile zero","B13.9");
            };
            for(const auto& names:std::vector<std::pair<std::string,std::string>>{{"QoS","qos"},{"SrcID","srcid"},{"TxnID","txnid"},{"Opcode","opcode"}})copy(names.first,names.second);
            if(channel!="SNP")copy("TgtID","tgtid");
            if(channel=="REQ") {
                for(const auto& names:std::vector<std::pair<std::string,std::string>>{
                    {"Size","size"},{"Order","order"},{"AllowRetry","allowRetry"},{"PCrdType","pcrdtype"},
                    {"MemAttr","memattr"},{"SnpAttr","snpattr"},{"ExpCompAck","expCompAck"},
                    {"ReturnNID","returnNid"},{"ReturnTxnID","returnTxnId"}})copy(names.first,names.second);
                p["fields"]["Addr"]=field(e.at("address"),"observed",e.at("id"),"address","B13.9.1");
                for(const auto* name:{"NS","Endian","Excl","LPID","StashNIDValid","DoDWT","TraceTag"})
                    copy(name,name,false);
                copy("LikelyShared",e["fields"].contains("likelyShared")?"likelyShared":"LikelyShared",false);
                // CAH is an Issue H extension not represented by the pinned E.b
                // checker. Zero is compatible; nonzero is explicitly unsupported.
                copy("CAH",e["fields"].contains("cah")?"cah":"CAH",false);
                if(op.starts_with("StashOnce"))copy("StashNID","stashNid");
            } else if(channel=="RSP") {
                for(const auto& names:std::vector<std::pair<std::string,std::string>>{
                    {"DBID","dbid"},{"Resp","resp"},{"RespErr","respErr"},{"PCrdType","pcrdtype"}})copy(names.first,names.second);
                copy("DataPull","dataPull",false);
                if(op=="ReadReceipt")p["fields"]["DBID"]["applicable"]=false;
            } else if(channel=="SNP") {
                copy("Addr","addr");copy("RetToSrc","retToSrc");copy("DoNotGoToSD","doNotGoToSD");
                for(const auto* name:{"FwdNID","FwdTxnID","NS","TraceTag"})copy(name,name,false);
            } else {
                for(const auto& names:std::vector<std::pair<std::string,std::string>>{
                    {"DBID","dbid"},{"HomeNID","HomeNID"},{"DataID","dataid"},{"Resp","resp"}})copy(names.first,names.second);
                for(const auto& names:std::vector<std::pair<std::string,std::string>>{
                    {"RespErr","respErr"},{"DataSource","dataSource"},{"CCID","ccid"},{"DataPull","dataPull"}})copy(names.first,names.second,false);
                for(const auto* key:{"payload","validMask","byteEnable","payloadOrigin"})
                    if(e.contains(key))p[key]=e[key];
                bool geometry=true;
                for(const auto* key:{"payload","validMask","byteEnable"}) {
                    if(!e.contains(key)||e[key].is_null()) {
                        check(e,"insufficient","native-DAT-evidence",std::string(key)+" was not captured; it remains unknown and is not synthesized.");geometry=false;complete=false;
                    } else if(!e[key].is_string()||e[key].get<std::string>().size()!=64||!isHex(e[key])) {
                        check(e,"fail","native-DAT-width",std::string(key)+" must preserve exactly 32 physical bytes; no padding/splitting applied.");geometry=false;complete=false;
                    }
                }
                if(geometry) {
                    uint64_t be=0;const auto mask=e.at("byteEnable").get<std::string>();
                    for(unsigned i=0;i<32;++i) {
                        const auto byte=std::stoul(mask.substr(i*2,2),nullptr,16);
                        if(byte!=0&&byte!=255){check(e,"fail","native-BE","BE byte encoding must be 00 or ff; not normalized.");complete=false;}
                        if(byte==255)be|=uint64_t(1)<<i;
                    }
                    p["fields"]["BE"]=field(be,"observed",e.at("id"),"byteEnable","B2.9.3");
                    if(op=="CompData")p["fields"]["BE"]["applicable"]=false;
                }
            }
            if(complete) {
                // Those fields are not implemented by the finite native producer.
                // Preserve unexpected observed values but do not check a different,
                // all-zero CHIron flit and then report it as this flit's result.
                for(const auto* name:{"NS","Endian","Excl","LPID","StashNIDValid","DoDWT",
                                     "CAH","TraceTag","FwdNID","FwdTxnID","DataSource","CCID","DataPull"})
                    if(p["fields"].contains(name)&&value(p,name)!=0) {
                        check(e,"unsupported","native-fixed-profile",std::string(name)+" is nonzero outside the declared fixed native profile; original field retained.");complete=false;
                    }
                for(const auto& [name,width]:widths)if(p["fields"].contains(name)&&value(p,name)>=(uint64_t(1)<<width)) {
                    check(e,"fail","field-width",name+" exceeds native field width; the recorded value is preserved.");complete=false;
                }
                const unsigned opcodeWidth=channel=="REQ"?7:channel=="DAT"?4:5;
                if(value(p,"Opcode")>=(1u<<opcodeWidth)) {check(e,"fail","field-width","Native Opcode exceeds channel width.");complete=false;}
                if(p["fields"].contains("Addr")&&value(p,"Addr")>=(uint64_t(1)<<(channel=="SNP"?49:52))) {
                    check(e,"fail","field-width","Native Addr exceeds configured physical address width.");complete=false;
                }
                if(channel=="REQ"&&value(p,"Size")==7) {check(e,"fail","B2.9.1","Native Size=7 is reserved; original value retained.");complete=false;}
                if(channel=="DAT"&&value(p,"DataID")!=0&&value(p,"DataID")!=2) {
                    check(e,"fail","B2.9.4","Native 256-bit DataID must be 0 or 2; original value retained.");complete=false;
                }
                try {if(opcodeCode(op)!=value(p,"Opcode")){check(e,"fail","native-opcode","Recorded opcode label and native numeric Opcode disagree; neither was corrected.");complete=false;}}
                catch(...) {check(e,"unsupported","native-opcode","Opcode is outside this field-checking profile; native value retained.");complete=false;}
                if(value(p,"SrcID")!=nodeId.at(e.at("source")) ||
                   (channel!="SNP"&&value(p,"TgtID")!=nodeId.at(e.at("target")))) {
                    check(e,"fail","native-node-id","Native NodeID disagrees with recorded topology endpoint; no remapping applied.");complete=false;
                }
            }
            if(complete)chiron(p);
            protocol.push_back(p);
            if(!jointNotice) {
                check(p,"unsupported","transaction-joint","Native transaction joints are not run in TX display order: independently observed RX timing must drive each endpoint. Use check-native-wire.py for direct raw lifecycle/handshake validation; field-mapping passes are not transaction passes.","CHIron E.b");
                jointNotice=true;
            }
        }
        for(const auto& [message,peers]:messageObservations) {
            unsigned tx=0,rx=0;for(const auto& peer:peers){tx+=peer.value("observation","")=="TX";rx+=peer.value("observation","")=="RX";}
            if(tx!=1||rx!=1)check(peers.front(),manifest.value("complete",false)?"fail":"insufficient","native-boundaries",
                "Native message "+message+" has TX="+std::to_string(tx)+", RX="+std::to_string(rx)+"; missing capture evidence is not synthesized.");
        }
    }
public:
    Refiner(std::vector<Json> records,bool orderedRead,bool rotate,bool extendedProfile=false,bool nativeProfile=false):ordered(orderedRead),rotateRetryId(rotate),extended(extendedProfile),native(nativeProfile) {
        if(records.empty()||records[0].value("type","")!="manifest"||(records[0].value("schemaVersion","")!="1.0"&&records[0].value("schemaVersion","")!="1.1"))throw std::runtime_error("First record must be a schema 1.0 manifest");
        manifest=records[0];
        native=native||manifest.value("profile","")=="h-native-256";
        if(native&&(ordered||rotate))throw std::runtime_error("Native CHI must preserve observed messages and IDs; --ordered-read/--rotate-retry-id are not permitted");
        if(manifest.value("lineBytes",0)!=64||manifest.value("dataWidth",0)!=256)throw std::runtime_error("Selected profile requires lineBytes=64, dataWidth=256");
        std::set<unsigned> numericNodes;
        for(const auto& n:manifest.at("topology")) {const std::string id=n.at("id");const auto numeric=integer(n.at("nodeId"));if(nodeId.contains(id)||numeric>2047||!numericNodes.insert(numeric).second)throw std::runtime_error("Duplicate/out-of-range topology ID");nodeId[id]=numeric;role[id]=n.at("role");}
        for(const auto& [id,numeric]:nodeId) chironGlobal.TOPOLOGY.SetNode(numeric,role[id]=="RN-F"?C::Xact::NodeType::RN_F:role[id]=="HN-F"?C::Xact::NodeType::HN_F:C::Xact::NodeType::SN_F,id);
        std::set<std::string> ids;uint64_t priorTick=0,priorSeq=0;
        for(size_t i=1;i<records.size();++i) {
            auto& e=records[i];if(e.value("type","")!="observed")continue;
            if(!e.at("tick").is_string() || (e.contains("address")&&!e["address"].is_string()))throw std::runtime_error("Tick and address must be strings");
            const auto tick=integer(e["tick"]),s=integer(e.at("seq"));
            if(tick<priorTick || s<=priorSeq)throw std::runtime_error("Observed sequence/tick moved backwards or duplicated");
            priorTick=tick;priorSeq=s;
            if(!ids.insert(e.at("id")).second || e.value("rootId","").empty()||e.value("legId","").empty())throw std::runtime_error("Missing/duplicate stable identity");
            observed.push_back(e);
            if(e.value("opcode","")=="ReadReceipt")observedReceipts.emplace(e.at("legId").get<std::string>(),e.value("attemptId","0"));
            if(e.value("kind","")=="flit"&&!e.value("messageId","").empty())messageObservations[e["messageId"]].push_back(e);
            if(e.value("kind","")=="flit") {
                try{opcodeCode(e.at("opcode"));}catch(...){unsupportedRoots.insert(e.at("rootId"));check(e,"unsupported","profile","Opcode outside selected finite profile; complete root retained as raw events.");continue;}
                const std::string op=e["opcode"],ch=e["channel"];
                const bool valid=ch=="REQ"?(op.starts_with("Write")|| (op.starts_with("Read")&&op!="ReadReceipt")||op.starts_with("Clean")||op.starts_with("Make")||op=="Evict"||op.starts_with("StashOnce")):
                    ch=="SNP"?(op.starts_with("Snp")&&op!="SnpResp"&&op!="SnpRespData"):
                    ch=="DAT"?(op=="CompData"||op=="SnpRespData"||op=="CopyBackWriteData"||op=="NonCopyBackWriteData"):
                    ch=="RSP"?(op=="CompAck"||op=="Comp"||op=="CompDBIDResp"||op=="DBIDResp"||op=="RetryAck"||op=="PCrdGrant"||op=="ReadReceipt"||op=="SnpResp"):false;
                if(!valid){check(e,"fail","channel-opcode","Opcode does not belong to the recorded channel; original root retained.");unsupportedRoots.insert(e["rootId"]);}
            }
        }
        for(const auto& [message,peers]:messageObservations) {
            (void)message;
            const auto& first=peers.front();
            for(size_t i=1;i<peers.size();++i)for(const auto* key:{"rootId","legId","attemptId","source","target","channel","opcode","fields","payload","validMask","byteEnable"})
                if(first.value(key,Json())!=peers[i].value(key,Json())) {
                    check(peers[i],"fail","observation-consistency","TX/RX with the same messageId disagree in "+std::string(key));
                    break;
                }
        }
        // MSHR merging can add a source after the original REQ was emitted.
        // It applies to the whole explicitly identified transaction tree.
        std::map<std::string,std::set<std::string>> causes;
        for(const auto& e:observed)for(const auto& id:e.value("causeInstructionIds",Json::array()))causes[e.at("rootId").get<std::string>()].insert(id.get<std::string>());
        if(!native)for(auto& e:observed)if(!causes[e.at("rootId").get<std::string>()].empty())e["causeInstructionIds"]=causes[e.at("rootId").get<std::string>()];
        manifest["refinement"]={{"tool","chi-refine 0.2"},{"orderedRead",ordered},{"chironCoverage",extended?"E.b fields and supported transaction joints; Issue H WriteEvict/Stash/CleanShared and implicit DataPull use project flow checks":"E.b field mapping and RN-F/SN-F transaction joints"},{"issueHCoverage",extended?"finite h-extended-256 identity, retry credit, aligned small accesses, BE, DataPull and payload checks":"finite h-core-256 identity, retry credit, beat and payload checks"},{"initialAllowRetry","observed from gem5; never rewritten"},{"unlistedOptionalFields","modeled zero"}};
        manifest["refinement"]["retryIdPolicy"]=rotate?"rotate":"reuse";
        if(native)manifest["refinement"]={{"tool","chi-refine 0.3"},{"mode","native-observed-pass-through"},
            {"wireProfile","h-native-256"},{"chironCoverage","E.b field mappings on original numeric wire fields; transaction joints explicitly unsupported"},
            {"issueHCoverage","independent check-native-wire.py validates original endpoint traffic; not implicitly run by this conversion"},
            {"unrecordedFixedProfileFields","modeled zero; never marked observed"},{"retryIdPolicy","preserve observed"},
            {"timing","original TX tick; all TX/RX evidence retained; no generated messages or protocol IDs"}};
    }
    void run() {
        if(native){runNative();return;}
        for(const auto& e:observed) {
            if(extended&&e.value("kind","")=="allocate"&&e.value("implicitDataPull",false)) {
                const auto parent=e.value("parentLegId","");
                if(!legs.contains(parent)){check(e,"insufficient","stash-data-pull","Stash snoop is outside the capture window.");continue;}
                const auto& snoop=legs.at(parent);
                Leg l;l.anchor=e;l.root=e["rootId"];l.leg=e["legId"];l.attempt=e.value("attemptId","0");l.requester=snoop.completer;l.completer=snoop.requester;l.opcode=e["opcode"];l.ack=true;
                l.txn=txnIds.take(l.requester);l.txnLive=true;l.requestBinding=binding(e,l,"TxnID",l.txn,"allocate");l.requestEvent=l.requestBinding;
                jointUnsupportedAttempts.insert(l.leg+":"+l.attempt);legs[l.leg]=std::move(l);continue;
            }
            if(e.value("kind","")!="flit"||unsupportedRoots.contains(e["rootId"]))continue;
            const auto message=e.value("messageId","");if(!message.empty()&&!processedMessages.insert(message).second)continue;
            if(!nodeId.contains(e.value("source",""))||!nodeId.contains(e.value("target",""))){check(e,"insufficient","topology","Endpoint is absent from topology; original event retained.");continue;}
            const std::string op=e["opcode"],channel=e["channel"];
            if(op=="PCrdGrant") {
                auto p=event(e,"RSP",op,"flit","B2.10");p["fields"]["TxnID"]=field(0);p["fields"]["DBID"]=field(0);observedField(p,e,"PCrdType","pcrdtype");
                credits[creditKey(e["target"],e["source"],raw(e,"pcrdtype"))].push_back(p["id"]);
                p["description"]="P-Credit 按 requester、completer 和 PCrdType 管理，与 TxnID 无关；可以先于 RetryAck 到达。";push(p,7);continue;
            }
            if(channel=="REQ"||channel=="SNP"){request(e);continue;}
            auto it=legs.find(e["legId"]);
            if(it==legs.end()){check(e,"insufficient","B2.4","Request is outside the capture window; response cannot be associated by address/TxnID alone.");continue;}
            auto& l=it->second;
            if(l.attempt!=e.value("attemptId","0")){check(e,"fail","B2.4","Response belongs to a different attempt generation.");continue;}
            if(l.done||l.retried){check(e,"fail","B2.4","Message after completion or RetryAck for this attempt.");continue;}
            const bool forward=op=="CompAck"||op=="CopyBackWriteData"||op=="NonCopyBackWriteData";
            if(e["source"]!=(forward?l.requester:l.completer)||e["target"]!=(forward?l.completer:l.requester)){check(e,"fail","B2.5","Endpoint direction does not match the explicit transaction owner.");continue;}
            if(channel=="RSP")response(e,l);else if(channel=="DAT")data(e,l);
        }
        for(auto& [id,l]:legs) {
            (void)id;
            if(unsupportedRoots.contains(l.root))continue;
            if(!l.retried&&l.expectsReceipt&&!l.receipt)check(l.anchor,manifest.value("complete",false)?"fail":"insufficient","B2.7.5","Missing required ReadReceipt; request identity remains allocated.");
            if(!l.retried && l.offsets.size()!=beats(l) && !(l.snoop&&l.done))check(l.anchor,manifest.value("complete",false)?"fail":"insufficient","B2.9.4","Missing data beat(s); open capture windows do not establish deadlock.");
            else if(!l.retried)check(l.anchor,"pass","B2.9.4",beats(l)==0?"Dataless transaction needs no DAT beat.":beats(l)==1?"One 256-bit flit carries the aligned small access.":"Two 256-bit beats cover a 64-byte line with DataID 0 and 2.");
            if(!l.done&&!l.retried)check(l.anchor,"insufficient","completion","Transaction is still open at end of capture; no deadlock inference.");
            if(!l.anchor.value("parentLegId","").empty() && l.opcode=="ReadNoSnp") {
                auto parent=legs.find(l.anchor["parentLegId"]);if(parent!=legs.end()&&!parent->second.write)compareData(l,parent->second);
            }
            if(l.snoop&&l.offsets.size()==beats(l)) {auto parent=legs.find(l.anchor.value("parentLegId",""));if(parent!=legs.end())compareData(l,parent->second);}
        }
        // A root discovered to be unsupported during expansion is shown wholly
        // in raw mode, avoiding the appearance of a complete partial expansion.
        std::erase_if(protocol,[&](const Json& p){return unsupportedRoots.contains(p.at("rootId"));});
    }
    void compareData(const Leg& source,const Leg& target) {
        size_t known=0;bool same=true;
        for(size_t i=0;i<64;++i)if(source.bytes[i]>=0&&target.bytes[i]>=0){++known;same &= source.bytes[i]==target.bytes[i];}
        check(target.anchor,!same?"fail":known==64?"pass":"insufficient","payload-conservation",!same?"Child response and parent data disagree byte-for-byte.":known==64?"All 64 bytes preserved from child response to upstream data.":"Insufficient known/enabled bytes for a complete payload comparison.");
    }
    void write(std::ostream& out) const {
        out<<manifest.dump()<<'\n';for(const auto& e:observed)out<<e.dump()<<'\n';for(const auto& p:protocol)out<<p.dump()<<'\n';for(const auto& c:checks)out<<c.dump()<<'\n';
        const auto failures=std::count_if(checks.begin(),checks.end(),[](const Json& c){return c["status"]=="fail";});
        std::cerr<<observed.size()<<" observed, "<<protocol.size()<<" protocol, "<<checks.size()<<" validations ("<<failures<<" failures)\n";
    }
};

int main(int argc,char** argv) {
    try {
        std::string input,output,profile="h-core-256";bool ordered=false,rotate=false;
        for(int i=1;i<argc;++i){const std::string arg=argv[i];if(arg=="--ordered-read")ordered=true;
            else if(arg=="--rotate-retry-id")rotate=true;
            else if((arg=="--input"||arg=="--output"||arg=="--profile")&&i+1<argc){const std::string v=argv[++i];if(arg=="--input")input=v;else if(arg=="--output")output=v;else profile=v;}
            else if(arg=="--help"){std::cout<<"chi-refine --input coarse.jsonl --output session.chi.jsonl --profile h-core-256|h-extended-256|h-native-256 [--ordered-read] [--rotate-retry-id]\n";return 0;}
            else throw std::runtime_error("Unknown/incomplete argument: "+arg);}
        if(input.empty()||output.empty()||(profile!="h-core-256"&&profile!="h-extended-256"&&profile!="h-native-256"))throw std::runtime_error("Supply --input, --output and --profile h-core-256, h-extended-256 or h-native-256");
        if(input==output)throw std::runtime_error("Input and output must differ");
        std::ifstream in(input);if(!in)throw std::runtime_error("Cannot read input: "+input);
        std::vector<Json> records;std::string line;size_t n=0;
        while(std::getline(in,line)){++n;if(n==1&&line.starts_with("\xEF\xBB\xBF"))line.erase(0,3);if(line.find_first_not_of(" \t\r")==std::string::npos)continue;
            try{records.push_back(Json::parse(line));}catch(const std::exception& e){throw std::runtime_error("Line "+std::to_string(n)+": "+e.what());}}
        Refiner refiner(std::move(records),ordered,rotate,profile=="h-extended-256",profile=="h-native-256");refiner.run();
        std::ofstream out(output);if(!out)throw std::runtime_error("Cannot create output: "+output);refiner.write(out);
        if(!out)throw std::runtime_error("Output write failed");
        return 0;
    }catch(const std::exception& error){std::cerr<<"chi-refine: "<<error.what()<<'\n';return 2;}
}

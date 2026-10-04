#pragma once

// C++17-only observer. No scheduling, cache access, or protocol decisions.
#include <cstdlib>
#include <array>
#include <algorithm>
#include <map>
#include <fstream>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <nlohmann/json.hpp>
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/ChiTransferCensus.hh"
#include "sim/cur_tick.hh"
#include "mem/request.hh"

namespace gem5::Chi {
class FlowGraphTrace {
    using Json = nlohmann::ordered_json;
    std::ofstream output;
    Json manifest, filter;
    uint64_t sequence = 0, identity = 0, message = 0;
    std::optional<uint64_t> componentTick;
    std::unordered_map<uint32_t, std::string> nodes;
    std::unordered_map<std::string, uint32_t> endpointPorts;
    std::unordered_set<std::string> waits;
    std::unordered_set<std::string> instructionEvents;
    std::unordered_set<std::string> cancelledInstructions;
    std::unordered_map<RequestPtr,std::vector<std::string>> requestCauses;
    std::unordered_map<RequestPtr,FlowGraphIdentity> requestRoots;
    std::map<std::pair<uint32_t,Addr>,std::vector<std::string>> bufferedStores;
    std::map<std::pair<uint32_t,Addr>,std::array<std::vector<std::string>,64>> dataWriters;
    bool initialized = false;
  public:
    static FlowGraphTrace& get() { static FlowGraphTrace recorder; return recorder; }
    bool enabled() {
        if (!initialized) {
            initialized = true;
            const char* path = std::getenv("CHI_FLOWGRAPH_OUT");
            const char* config = std::getenv("CHI_FLOWGRAPH_CONFIG");
            if (path && config) {
                std::ifstream file(config);
                Json settings; file >> settings;
                open(path, settings.at("manifest"), settings.value("filter", Json::object()));
            }
        }
        return output.is_open();
    }
    void open(const std::string& path, Json metadata, Json selection = Json::object()) {
        cancelledInstructions.clear();
        close(); initialized = true; manifest = std::move(metadata); filter = std::move(selection);
        nodes.clear(); endpointPorts.clear(); waits.clear(); instructionEvents.clear(); requestCauses.clear();requestRoots.clear();bufferedStores.clear();dataWriters.clear(); sequence = identity = message = 0;
        if (!filter.empty()) {
            manifest["complete"] = false;
            manifest["captureFilter"] = filter;
            manifest["initialState"] = Json::object();
        }
        if (manifest.contains("endpointPorts"))
            for (auto it=manifest["endpointPorts"].begin();it!=manifest["endpointPorts"].end();++it)
                endpointPorts[it.key()] = it.value().get<uint32_t>();
        for (const auto& n : manifest.at("topology")) nodes[n.at("nodeId").get<uint32_t>()] = n.at("id");
        if (manifest.contains("nodeAliases")) for (auto it=manifest["nodeAliases"].begin();it!=manifest["nodeAliases"].end();++it)
            nodes[std::stoul(it.key())] = it.value().get<std::string>();
        if (!path.empty()) {
            output.open(path); if (!output) throw std::runtime_error("Cannot open FlowGraph output: " + path);
            output << manifest.dump() << '\n';
        }
    }
    void close() { if (output.is_open()) output.close(); componentTick.reset(); }
    void setComponentTick(uint64_t tick) { componentTick = tick; }
    uint64_t tick() const { return componentTick ? *componentTick : curTick(); }
    std::string node(uint32_t id) const {
        const auto it = nodes.find(id); return it == nodes.end() ? "node:" + std::to_string(id) : it->second;
    }
    static std::string hex(uint64_t value) { std::ostringstream s; s << "0x" << std::hex << value; return s.str(); }
    static std::string bytes(const std::vector<uint8_t>& data) {
        std::ostringstream s; s << std::hex << std::setfill('0');
        for (auto b : data) s << std::setw(2) << unsigned(b);
        return s.str();
    }
    FlowGraphIdentity root(const std::string& label = "transaction") {
        if (!enabled()) return {};
        const std::string id = label + ":" + std::to_string(++identity);
        return {id, id + "/up", "", "0", "", "unknown", 0, {}, {}};
    }
    FlowGraphIdentity child(const FlowGraphIdentity& parent, const std::string& kind, uint64_t address) {
        if (!enabled() || parent.rootId.empty()) return {};
        return {parent.rootId, parent.legId + "/" + kind + ":" + std::to_string(++identity), parent.legId, "0", "", parent.payloadOrigin, address, parent.causeInstructionIds, parent.dataProducerInstructionIds};
    }
    void stamp(BaseFlit& flit) { if (enabled() && !flit.flow.rootId.empty() && flit.flow.messageId.empty()) flit.flow.messageId = "message:" + std::to_string(++message); }
    Json base(const FlowGraphIdentity& owner, const std::string& kind) {
        return {{"type","observed"},{"id","o:" + std::to_string(++sequence)},{"seq",sequence},
            {"tick",std::to_string(tick())},{"kind",kind},{"rootId",owner.rootId},{"legId",owner.legId},
            {"parentLegId",owner.parentLegId},{"attemptId",owner.attemptId},{"address",hex(owner.address)},
            {"causeInstructionIds",owner.causeInstructionIds},{"dataProducerInstructionIds",owner.dataProducerInstructionIds}};
    }
    std::string instruction(const RequestPtr& request, uint32_t nodeId, const std::string& origin="cpu", uint64_t callPc=0) {
        if(!enabled() || !request->hasPC() || !request->hasInstSeqNum()) return "";
        const auto core=request->hasContextId()?request->contextId():0;
        const auto id="cpu:"+std::to_string(core)+":0:"+std::to_string(request->getReqInstSeqNum());
        if(instructionEvents.insert(id).second) {
            FlowGraphIdentity owner; owner.rootId=id; owner.legId=id; owner.address=request->hasPaddr()?request->getPaddr():0;
            auto e=base(owner,"instruction"); e["node"]=node(nodeId); e["instructionId"]=id;
            e["instruction"]={{"id",id},{"hartId",core},{"iteration",0},{"dynamicSeq",std::to_string(request->getReqInstSeqNum())},{"pc",hex(request->getPC())},{"origin",origin}};
            if(manifest.contains("litmus") && manifest["litmus"].contains("pcMap")) {
                const auto& mapping=manifest["litmus"]["pcMap"];
                const auto key=std::to_string(core)+":"+hex(request->getPC());
                if(mapping.contains(key)) e["instruction"].update(mapping[key]);
                const auto callKey=std::to_string(core)+":"+hex(callPc);
                if(callPc&&mapping.contains(callKey)) {e["instruction"]["callSite"]=mapping[callKey];e["instruction"]["callSite"]["pc"]=hex(callPc);}
            }
            emit(std::move(e));
        }
        if(callPc && manifest.contains("litmus") && manifest["litmus"].contains("pcMap")) {
            const auto& mapping=manifest["litmus"]["pcMap"];const auto key=std::to_string(core)+":"+hex(callPc);
            if(mapping.contains(key)) {FlowGraphIdentity owner;owner.rootId=id;owner.legId=id;
                auto e=base(owner,"instruction_context");e["instructionId"]=id;e["fields"]["callSite"]=mapping[key];e["fields"]["callSite"]["pc"]=hex(callPc);
                e["description"]="测试程序提交的实际调用点；动态身份和 PC 仍属于 doorbell 指令。";emit(std::move(e));}
        }
        return id;
    }
    void observeInstruction(const RequestPtr& request) {
        if(!enabled()||!request->hasContextId()||!manifest.contains("litmus"))return;
        for(const auto& thread:manifest["litmus"]["threads"]) if(thread["hartId"]==request->contextId()) {
            for(const auto& endpoint:manifest["topology"]) if(endpoint["id"]==thread["node"]) {
                const auto id=instruction(request,endpoint["nodeId"].get<uint32_t>());
                if(!id.empty()) {auto& causes=requestCauses[request];if(std::find(causes.begin(),causes.end(),id)==causes.end())causes.push_back(id);}
            }
        }
    }
    void cancelInstruction(uint32_t core,uint64_t dynamicSeq) {
        if(!enabled())return;
        const auto id="cpu:"+std::to_string(core)+":0:"+std::to_string(dynamicSeq);
        if(instructionEvents.count(id)&&cancelledInstructions.insert(id).second) {
            FlowGraphIdentity owner;owner.rootId=id;owner.legId=id;
            auto e=base(owner,"instruction_cancel");e["instructionId"]=id;
            e["description"]="CPU 撤销该动态 load；已经发出的 CHI 消息仍保留真实来源。";emit(std::move(e));
        }
    }
    std::vector<std::string> causes(const RequestPtr& request) { if(!enabled())return {};observeInstruction(request);return requestCauses[request]; }
    void bind(const RequestPtr& request,const FlowGraphIdentity& root) { if(enabled())requestRoots[request]=root; }
    void merge(const RequestPtr& request,const RequestPtr& incoming) {
        if(!enabled())return;
        const auto added=causes(incoming);auto& merged=requestCauses[request];
        for(const auto& id:added)if(std::find(merged.begin(),merged.end(),id)==merged.end())merged.push_back(id);
        const auto root=requestRoots.find(request);
        if(root!=requestRoots.end()) {auto e=base(root->second,"cause_add");e["causeInstructionIds"]=merged;emit(std::move(e));}
    }
    void bufferStore(const RequestPtr& request) {
        if(!enabled()||!request->hasContextId()||!request->hasPaddr())return;
        auto& ids=bufferedStores[{request->contextId(),request->getPaddr()&~Addr(63)}];
        for(const auto& id:causes(request)) if(std::find(ids.begin(),ids.end(),id)==ids.end())ids.push_back(id);
    }
    void bindBufferedStores(const RequestPtr& request) {
        if(!enabled())return;
        const auto key=std::make_pair(uint32_t(request->contextId()),request->getPaddr()&~Addr(63));
        auto it=bufferedStores.find(key);
        if(it!=bufferedStores.end()) {requestCauses[request]=std::move(it->second);bufferedStores.erase(it);}
    }
    void observeWrite(const RequestPtr& request) {
        if(!enabled()||!request->hasContextId()||!request->hasPaddr())return;
        const auto ids=causes(request);if(ids.empty())return;
        const auto& mask=request->getByteEnable();
        auto& writers=dataWriters[{request->contextId(),request->getPaddr()&~Addr(63)}];
        const unsigned start=request->getPaddr()%64;
        for(unsigned i=0;i<request->getSize()&&start+i<64;++i)if(mask.empty()||mask[i])writers[start+i]=ids;
    }
    std::vector<std::string> producers(uint32_t rnf,Addr address) {
        if(!enabled()||!manifest.contains("litmus"))return {};
        for(const auto& t:manifest["litmus"]["threads"])if(t["node"]==node(rnf)) {
            std::vector<std::string> result;
            for(const auto& ids:dataWriters[{t["hartId"].get<uint32_t>(),address&~Addr(63)}])for(const auto& id:ids)if(std::find(result.begin(),result.end(),id)==result.end())result.push_back(id);
            return result;
        }
        return {};
    }
    void emit(Json event) {
        if (!enabled() || event.value("rootId", "").empty()) return;
        const auto t = std::stoull(event.at("tick").get<std::string>());
        if (filter.contains("startTick") && t < std::stoull(filter["startTick"].get<std::string>())) return;
        if (filter.contains("endTick") && t > std::stoull(filter["endTick"].get<std::string>())) return;
        if (filter.contains("address") && event.value("address", "") != hex(std::stoull(filter["address"].get<std::string>(), nullptr, 0))) return;
        if (filter.contains("nodes")) {
            bool match = false;
            for (const auto& n : filter["nodes"]) if (n == event.value("node", "") || n == event.value("source", "") || n == event.value("target", "")) match = true;
            if (!match) return;
        }
        output << event.dump() << '\n';
    }
    void event(const FlowGraphIdentity& owner, const std::string& kind, uint32_t nodeId, Json extra = Json::object()) {
        if (!enabled() || owner.rootId.empty()) return;
        auto e=base(owner,kind); e["node"]=node(nodeId); e.update(extra); emit(std::move(e));
    }
    void wait(const FlowGraphIdentity& owner, uint32_t nodeId, const std::string& resource,
              bool blocked, const std::string& reason, Json extra=Json::object()) {
        if (!enabled()||owner.rootId.empty()) return;
        const auto id=owner.legId+"/"+resource;
        if (blocked && waits.insert(id).second) {
            extra["waitId"]=id;extra["reason"]=reason;event(owner,"wait_start",nodeId,std::move(extra));
        } else if (!blocked&&waits.erase(id)) event(owner,"wait_end",nodeId,{{"waitId",id}});
    }
    // Call only at an actual port enqueue attempt. Endpoint names are explicit
    // configuration, so no router-internal delay is attributed to an endpoint.
    void port(ChannelType ch, const FlitVariant& f, const std::string& src,
              const std::string& dst, bool accepted, unsigned occupancy,
              unsigned capacity, const std::string& reason = "link_credit") {
        auto& census=ChiTransferCensus::get();
        if(census.enabled())census.observe(ch,f,src,dst,accepted,tick());
        if (!enabled()) return;
        auto from=endpointPorts.find(src), to=endpointPorts.find(dst);
        if (from==endpointPorts.end() && to==endpointPorts.end()) return;
        std::visit([&](const auto& flit) {
            const auto& owner=flit.flow;
            if (owner.rootId.empty()) return;
            const auto endpoint=from!=endpointPorts.end()?from:to;
            const auto key=endpoint->first + "/" + owner.messageId;
            if (!accepted) {
                if (waits.insert(key).second) event(owner,"wait_start",endpoint->second,
                    {{"waitId",key},{"reason",reason},{"blocker",dst},
                     {"occupancy",occupancy},{"capacity",capacity}});
            } else if (waits.erase(key)) event(owner,"wait_end",endpoint->second,{{"waitId",key}});
        }, f);
        if (accepted) {
            if (from!=endpointPorts.end()) flit(ch,f,"TX",src);
            if (to!=endpointPorts.end()) flit(ch,f,"RX",dst);
        }
    }
    static std::string opcode(ChannelType channel, uint8_t code) {
        if(channel==REQ&&code==0x42)return "WriteEvictOrEvict";
        if(channel==REQ&&code==0x0b)return "CleanUnique";
        // gem5's coarse full-write encodings differ from Issue H wire values.
        static const std::unordered_map<int,std::string> req={{1,"ReadShared"},{3,"ReadOnce"},{4,"ReadNoSnp"},{7,"ReadUnique"},{8,"CleanShared"},{9,"CleanInvalid"},{10,"MakeInvalid"},{12,"MakeUnique"},{13,"Evict"},{0x15,"WriteEvictFull"},{0x55,"WriteEvictFull"},{0x18,"WriteUniquePtl"},{0x58,"WriteUniquePtl"},{0x19,"WriteUniqueFull"},{0x1d,"WriteNoSnpFull"},{0x5c,"WriteNoSnpFull"},{0x1b,"WriteBackFull"},{0x5b,"WriteBackFull"},{0x59,"WriteUniqueFull"},{0x57,"WriteCleanFull"},{0x17,"WriteCleanFull"},{0x1c,"WriteNoSnpPtl"},{0x22,"StashOnceShared"},{0x23,"StashOnceUnique"},{0x24,"ReadOnceCleanInvalid"},{0x25,"ReadOnceMakeInvalid"},{0x26,"ReadNotSharedDirty"}};
        static const std::unordered_map<int,std::string> rsp={{1,"SnpResp"},{2,"CompAck"},{3,"RetryAck"},{4,"Comp"},{5,"CompDBIDResp"},{6,"DBIDResp"},{7,"PCrdGrant"},{8,"ReadReceipt"}};
        static const std::unordered_map<int,std::string> dat={{1,"SnpRespData"},{2,"CopyBackWriteData"},{3,"NonCopyBackWriteData"},{4,"CompData"}};
        static const std::unordered_map<int,std::string> snp={{1,"SnpShared"},{2,"SnpClean"},{3,"SnpOnce"},{4,"SnpNotSharedDirty"},{7,"SnpUnique"},{8,"SnpCleanShared"},{9,"SnpCleanInvalid"},{0xa,"SnpMakeInvalid"},{0xb,"SnpStashUnique"},{0xc,"SnpStashShared"}};
        const auto& map=channel==REQ?req:channel==RSP?rsp:channel==DAT?dat:snp;
        auto it=map.find(code); return it==map.end()?"Unsupported:"+hex(code):it->second;
    }
    void route(const FlitVariant& variant,const std::string& object,int direction) {
        if(!enabled()||!manifest.contains("fabric")) return;
        const auto& routers=manifest["fabric"]["routers"];
        for(const auto& from:routers) if(from.value("object","")==object) {
            const int x=from["x"].get<int>()+(direction==0?1:direction==2?-1:0);
            const int y=from["y"].get<int>()+(direction==3?1:direction==1?-1:0);
            for(const auto& to:routers) if(to["x"]==x&&to["y"]==y) std::visit([&](const auto& f){
                auto e=base(f.flow,"route");e["messageId"]=f.flow.messageId;
                e["fields"]={{"fromRouter",from["id"]},{"toRouter",to["id"]}};emit(std::move(e));
            },variant);
        }
    }
    void flit(ChannelType channel, const FlitVariant& variant, const std::string& observation, const std::string& boundary="HNF controller") {
        if (!enabled()) return;
        std::visit([&](const auto& f) {
            if (f.flow.rootId.empty()) return;
            auto e=base(f.flow,"flit"); e["source"]=node(f.srcid);e["target"]=node(f.tgtid);
            e["channel"]=channel==REQ?"REQ":channel==RSP?"RSP":channel==DAT?"DAT":"SNP";
            e["opcode"]=opcode(channel,f.opcode);e["observation"]=observation;e["messageId"]=f.flow.messageId;
            e["boundary"]=boundary;
            e["wireEncoding"]=f.nativeChi?"native-chi":"gem5-coarse";
            Json fields={{"qos",f.qos},{"srcid",f.srcid},{"tgtid",f.tgtid},{"txnid",f.txnid},{"opcode",f.opcode}};
            using F=std::decay_t<decltype(f)>;
            if constexpr (std::is_same_v<F,RawReq>) {
                e["address"]=hex(f.addr);fields.update({{"sizeBytes",f.size},{"order",f.order},{"allowRetry",f.AllowRetry},{"pcrdtype",f.pcrdtype},{"memattr",f.memattr},{"snpattr",f.snpattr},{"expCompAck",f.expCompAck},{"returnNid",f.ReturnNid},{"returnTxnId",f.ReturnTxnId},{"stashNid",f.stashNid}});
                if(f.nativeChi) {fields.erase("sizeBytes");fields["size"]=f.size;fields["likelyShared"]=f.likelyShared;fields["cah"]=f.cah;}
            } else if constexpr (std::is_same_v<F,RawSnp>) { e["address"]=hex(f.addr);fields["sizeBytes"]=f.size;
                if(f.nativeChi) {e["address"]=hex(f.addr<<3);fields.erase("sizeBytes");fields["addr"]=hex(f.addr);fields["retToSrc"]=f.retToSrc;fields["doNotGoToSD"]=f.doNotGoToSD;}
            } else if constexpr (std::is_same_v<F,RawRsp>) {
                fields.update({{"dbid",f.dbid},{"resp",f.resp},{"respErr",f.respErr},{"pcrdtype",f.pcrdtype}});
                if(f.opcode==1&&(f.resp&8)) { fields["dataPull"]=true;fields["pullLegId"]=f.pullFlow.legId; }
            } else if constexpr (std::is_same_v<F,RawDat>) {
                fields.update({{"dbid",f.dbid},{"HomeNID",f.HomeNID},{"dataid",f.dataid},{"beatOffset",f.beatOffset},{"resp",f.resp},{"last",f.last}});
                e["payload"]=bytes(f.data);e["payloadOrigin"]=f.flow.payloadOrigin;
                std::vector<uint8_t> mask(f.data.size(),0),be(f.data.size(),0);
                for (size_t i=0;i<f.data.size();++i) { if(i/8<f.chunkValid.size()&&f.chunkValid[i/8])mask[i]=255; if(i<f.byteEnable.size()&&f.byteEnable[i])be[i]=255; }
                e["validMask"]=bytes(mask);e["byteEnable"]=bytes(be);
            }
            e["fields"]=std::move(fields);emit(std::move(e));
        },variant);
    }
};
}

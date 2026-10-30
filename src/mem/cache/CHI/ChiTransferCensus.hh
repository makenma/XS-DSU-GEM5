#pragma once
// Optional endpoint traffic counts for long FS runs. Counts are observations,
// not a protocol validation result; detailed traces remain independently opt-in.
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <tuple>
#include <nlohmann/json.hpp>
#include "mem/cache/CHI/ChiNativeChannel.hh"

namespace gem5::Chi {
class ChiTransferCensus {
    using Json = nlohmann::ordered_json;
    using Key = std::tuple<std::string, std::string, unsigned, unsigned, unsigned, unsigned>;
    struct Count { uint64_t count=0, first=0, last=0; };
    std::string path;
    std::map<std::string,unsigned> endpoints;
    std::map<Key,Count> counts;
    uint64_t observations=0, lastTick=0;
    bool initialized=false;
    void initialize() {
        initialized=true;
        const char* output=std::getenv("CHI_FLOWGRAPH_COUNTS");
        if(!output||!*output)return;
        const char* config=std::getenv("CHI_FLOWGRAPH_CONFIG");
        if(!config)throw std::runtime_error("CHI_FLOWGRAPH_COUNTS requires CHI_FLOWGRAPH_CONFIG");
        std::ifstream input(config);Json settings;input>>settings;
        configure(output,settings.at("manifest").at("endpointPorts"));
    }
    void add(const std::string& boundary,const char* direction,ChannelType channel,
             const NativeBaseFlit& flit,unsigned opcode,uint64_t tick) {
        auto& c=counts[{boundary,direction,unsigned(channel),flit.srcid,flit.tgtid,opcode}];
        if(!c.count)c.first=tick;
        ++c.count;c.last=tick;lastTick=tick;++observations;
    }
  public:
    static ChiTransferCensus& get(){static ChiTransferCensus census;return census;}
    bool enabled(){if(!initialized)initialize();return !path.empty();}
    ~ChiTransferCensus(){if(!path.empty())try{flush("process-exit");}catch(...){}}
    void configure(const std::string& output,const Json& ports) {
        initialized=true;path=output;counts.clear();endpoints.clear();observations=lastTick=0;
        for(auto it=ports.begin();it!=ports.end();++it)endpoints[it.key()]=it.value().get<unsigned>();
        flush("initialized");
    }
    void observe(ChannelType channel,const NativeFlitVariant& variant,const std::string& src,
                 const std::string& dst,bool accepted,uint64_t tick) {
        if(!enabled()||!accepted)return;
        const auto from=endpoints.find(src),to=endpoints.find(dst);
        if(from==endpoints.end()&&to==endpoints.end())return;
        std::visit([&](const auto& flit){
            if(!flit.nativeChi)return;
            if(from!=endpoints.end())add(src,"TX",channel,flit,flit.opcode,tick);
            if(to!=endpoints.end())add(dst,"RX",channel,flit,flit.opcode,tick);
        },variant);
        if(observations&&observations%100000==0)flush("periodic");
    }
    void flush(const char* reason="explicit") const {
        if(path.empty())return;
        Json rows=Json::array();
        for(const auto& [key,count]:counts){
            const auto& [boundary,direction,channel,src,tgt,opcode]=key;
            rows.push_back({{"boundary",boundary},{"observation",direction},
                {"channel",channel==REQ?"REQ":channel==RSP?"RSP":channel==DAT?"DAT":"SNP"},
                {"srcid",src},{"tgtid",tgt},{"opcode",opcode},{"count",std::to_string(count.count)},
                {"firstTick",std::to_string(count.first)},{"lastTick",std::to_string(count.last)}});
        }
        Json report={{"schemaVersion","1.0"},{"source","accepted native endpoint transfers"},
            {"snapshotKind",reason},{"lastTick",std::to_string(lastTick)},
            {"observations",std::to_string(observations)},{"counts",rows},
            {"scope","Counts attempts including retries; no lifecycle, data, final-state or test-pass claim"}};
        const auto temporary=path+".tmp";
        {std::ofstream stream(temporary);stream<<report.dump(2)<<'\n';stream.flush();
         if(!stream)throw std::runtime_error("Cannot write CHI transfer census: "+temporary);}
        if(std::rename(temporary.c_str(),path.c_str())!=0)
            throw std::runtime_error("Cannot replace CHI transfer census: "+path);
    }
};
}

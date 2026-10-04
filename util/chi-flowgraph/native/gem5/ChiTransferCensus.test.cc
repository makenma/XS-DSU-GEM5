#include <cassert>
#include <fstream>
#include "mem/cache/CHI/ChiTransferCensus.hh"
int main(int argc,char** argv) {
    assert(argc==2);
    using namespace gem5::Chi;
    using J=nlohmann::ordered_json;
    ChiTransferCensus counter;
    counter.configure(argv[1],J{{"rn",0},{"hn",4}});
    RawReq request{};request.nativeChi=true;request.srcid=0;request.tgtid=4;request.opcode=7;
    const uint64_t tick=9007199254740993ULL;
    counter.observe(REQ,request,"rn","router",false,tick); // no TX on rejection
    counter.observe(REQ,request,"router","router2",true,tick); // internal hops not endpoints
    counter.observe(REQ,request,"rn","router",true,tick);
    counter.observe(REQ,request,"router2","hn",true,tick+1);
    counter.observe(REQ,request,"rn","router",true,tick+2); // retry attempt counts again
    request.nativeChi=false;
    counter.observe(REQ,request,"rn","router",true,tick+3); // coarse traffic is distinct
    counter.flush();
    std::ifstream input(argv[1]);J result;input>>result;
    assert(result["observations"]=="3");
    assert(result["counts"].size()==2);
    for(const auto& row:result["counts"]){
        if(row["observation"]=="TX"){
            assert(row["count"]=="2");
            assert(row["firstTick"]=="9007199254740993");
            assert(row["lastTick"]=="9007199254740995");
        }else assert(row["count"]=="1");
    }
    assert(result["snapshotKind"]=="explicit");
}

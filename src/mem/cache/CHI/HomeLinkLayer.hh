#ifndef __HOMELINKLAYER__HH__
#define __HOMELINKLAYER__HH__


#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HomeLinkLayer.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/ruby/common/Consumer.hh"

namespace gem5::Chi {

class HomeNodeFull;

class HomeLinkLayer :  public ruby::Consumer
{
    public:
        template<typename FlitType>
        using MemFn = void (HomeLinkLayer::*)(FlitType*);

        HomeLinkLayer(HomeNodeFull *HNF);
        void wakeup();
        void print(std::ostream& out) const {};
        inline void setRxPort(ChiCommonPort* port) { rxport = port; }
    private:

        HomeNodeFull *m_homenode;
        std::deque<FlitVariant> pipline_queue;
        ChiCommonPort *rxport;
        std::array<MemFn<RawReq>, 4> reqFuncs;
        std::array<MemFn<RawRsp>, 4> rspFuncs;
        std::array<MemFn<RawSnp>, 4> snpFuncs;
        std::array<MemFn<RawDat>, 4> datFuncs;
        std::unordered_map<std::type_index, std::vector<StageFunc>> pipelineMap;
        template<typename FlitType, size_t N>
        std::vector<StageFunc> initPipeline(const std::array<MemFn<FlitType>, N>& funcs);

        void advancePipeline(FlitVariant& fv);

        //pipline function
        void doStageH0_Req(RawReq* Req);
        void doStageH0_Rsp(RawRsp* Rsp);
        void doStageH0_Snp(RawSnp* Snp);
        void doStageH0_Dat(RawDat* Dat);

        void doStageH1_Req(RawReq* Req);
        void doStageH1_Rsp(RawRsp* Rsp);
        void doStageH1_Snp(RawSnp* Snp);
        void doStageH1_Dat(RawDat* Dat);

        void doStageH2_Req(RawReq* Req);
        void doStageH2_Rsp(RawRsp* Rsp);
        void doStageH2_Snp(RawSnp* Snp);
        void doStageH2_Dat(RawDat* Dat);

        void doStageH3_Req(RawReq* Req);
        void doStageH3_Rsp(RawRsp* Rsp);
        void doStageH3_Snp(RawSnp* Snp);
        void doStageH3_Dat(RawDat* Dat);





};

template<typename FlitType, size_t N>
std::vector<StageFunc> HomeLinkLayer::initPipeline(const std::array<MemFn<FlitType>, N>& funcs)
{
    std::vector<StageFunc> ans(N);
    for (int i = 0; i < N; ++i) {
        ans[i] = [this, fn = funcs[i]](BaseFlit* f) {
            (this->*fn)(static_cast<FlitType*>(f));
        };
    }
    return(ans);
}


}




#endif

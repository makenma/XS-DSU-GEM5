#ifndef __HOMELINKLAYER__HH__
#define __HOMELINKLAYER__HH__

#include <array>
#include <deque>
#include <type_traits>
#include <unordered_map>
#include <vector>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HomeLinkLayer.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/ruby/common/Consumer.hh"

namespace gem5::Chi {

class HomeNodeFull;

class HomeLinkLayer :  public ruby::Consumer
{
    public:
        template<typename FlitType>
        using MemFn = void (HomeLinkLayer::*)(FlitType*);

        HomeLinkLayer(HomeNodeFull *HNF,
            const std::array<int, 4>& thresholds,
            const int RnfNum
        );
        void wakeup();
        void print(std::ostream& out) const {};
        inline void setRxPort(ChiCommonPort* port) { rxport = port; }
    private:

        HomeNodeFull *m_homenode;
        std::deque<FlitVariant> pipline_queue;
        ChiCommonPort *rxport;
        std::array<FlitVariant, 4> flits = {
            RawReq{},
            RawRsp{},
            RawSnp{},
            RawDat{}
        };


        struct QosPool
        {
            enum PoolDim { QosCount, QosThreshold, PoolDimNum };
            enum PoolPriority { HighHigh, High, Medium, Low, PoolPriorityNum };
            std::array<std::array<int, PoolDimNum>, PoolPriorityNum> pool = {};


            PoolPriority WhichPriority(int qos){
                switch (qos) {
                    case 15:          return HighHigh;
                    case 12 ... 14:   return High;
                    case 8 ... 11:    return Medium;
                    case 0 ... 7:     return Low;
                    default:          return Low;
                }
            }

            bool is_CanPush(int qos, PoolPriority Pri)
            {
                for (int p = Pri; p < PoolPriorityNum; ++p) {
                    if (pool[p][QosCount] + 1 <= pool[p][QosThreshold]) {
                        pool[p][QosCount]++;
                        return true;
                    }
                }
                return false;
            }

            bool enqueue(int qos){
                PoolPriority Pri;
                Pri  = WhichPriority(qos);
                return is_CanPush(qos, Pri);
            }

            QosPool() = default;
            QosPool(const std::array<int, PoolPriorityNum>& thresholds) {
                for (int p = 0; p < PoolPriorityNum; ++p) {
                    pool[p][QosCount]     = 0;
                    pool[p][QosThreshold] = thresholds[p];
                }
            }
        };

        struct PendingElement
        {
            int srcid;
            int txnid;
        };

        struct PendingRetry:QosPool
        {
            std::array<std::vector<PendingElement>, PoolPriorityNum> PendingPool;

            bool enqueue(RawReq req){
                PoolPriority Pri = WhichPriority(req.qos);
                if (PendingPool[Pri].size() == 256){
                    panic("Retry overflow!!");
                }
                PendingElement ans;
                ans.txnid = req.txnid;
                ans.srcid = req.srcid;
                PendingPool[Pri].push_back(ans);
                return true;

            }



            PendingRetry() = default;


        };

        const int RnfNum;
        QosPool m_qosPool;
        PendingRetry m_PendingRetry;
        std::array<MemFn<RawReq>, 4> reqFuncs;
        std::array<MemFn<RawRsp>, 4> rspFuncs;
        std::array<MemFn<RawSnp>, 4> snpFuncs;
        std::array<MemFn<RawDat>, 4> datFuncs;


        template<typename FlitType>
        auto& funcsFor();

        void advancePipeline(FlitVariant& fv);

        void LoopChannelPipline(const std::type_index& flitType);

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

template<typename FlitType>
auto&
HomeLinkLayer::funcsFor()
{
    if constexpr (std::is_same_v<FlitType, RawReq>) {
        return reqFuncs;
    } else if constexpr (std::is_same_v<FlitType, RawRsp>) {
        return rspFuncs;
    } else if constexpr (std::is_same_v<FlitType, RawSnp>) {
        return snpFuncs;
    } else {
        static_assert(std::is_same_v<FlitType, RawDat>,
                      "Unsupported CHI flit type");
        return datFuncs;
    }
}


}




#endif

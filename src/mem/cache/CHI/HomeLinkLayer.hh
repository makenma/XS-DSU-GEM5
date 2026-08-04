#ifndef __HOMELINKLAYER__HH__
#define __HOMELINKLAYER__HH__

#include <array>
#include <deque>
#include <map>
#include <type_traits>

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
            const int RnfNum,
            const int retryfifo_num
        );
        void wakeup();
        void print(std::ostream& out) const {};
        inline void setRxPort(ChiCommonPort* port) { rxport = port; }
    private:

        HomeNodeFull *m_homenode;
        ChiCommonPort *rxport;

        // 每类型一条在途流水队列: 同类型多个 flit 可同时处于不同阶段
        // (flit0 在 H3、flit1 在 H1 并行)。索引与 flits 一一对应。
        // 入流水深度受 rx credit 限制; 出流水受 TX credit 背压。
        std::array<std::deque<FlitVariant>, 4> in_flight;
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


            PoolPriority WhichPriority(int qos) const {
                switch (qos) {
                    case 15:          return HighHigh;
                    case 12 ... 14:   return High;
                    case 8 ... 11:    return Medium;
                    case 0 ... 7:     return Low;
                    default:          return Low;
                }
            }

            // Try to push into the target priority pool, falling back to
            // lower-priority pools if the target one is full. Mutates pool
            // on success.
            bool tryPush(PoolPriority Pri)
            {
                for (int p = Pri; p < PoolPriorityNum; ++p) {
                    if (pool[p][QosCount] + 1 <= pool[p][QosThreshold]) {
                        pool[p][QosCount]++;
                        return true;
                    }
                }
                return false;
            }


            bool enqueue(int qos) {
                return tryPush(WhichPriority(qos));
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
            int qos;
        };

        // 待重试队列: 每个 srcid 一条记录, 内含 4 个优先级的计数和该
        // srcid 的优先级轮询指针。计数与指针同条目存储, 结构性消除了
        // "两张 map 忘记同步"的隐患 (旧版 PendingPool/ArbPointer 靠
        // enqueue 手工维护不变量)。
        struct PendingRetry : QosPool
        {
            using SrcId = int;

            struct Entry
            {
                std::array<int, PoolPriorityNum> counts = {};
                PoolPriority arbPointer = HighHigh;
            };

            std::map<SrcId, Entry> pendingPool;
            SrcId arbSrcPointer = 0;

            bool enqueue(RawReq req);

            // 轮询 srcid 的 counts (从 arbPointer 起绕一圈), 命中则消费
            // 该优先级并把指针推进到赢家之后, 返回赢家优先级;
            // srcid 无记录或全空时返回 PoolPriorityNum。
            PoolPriority arbQos(SrcId srcid);

            // 从 arbSrcPointer 起绕一圈, 返回第一个有积压的 srcid;
            // 全空返回 -1。
            SrcId arbSrcId();

            // 主入口: 返回仲裁赢家的 {srcid, 优先级} (qos 字段承载优先级),
            // 全空返回 {-1, -1}。
            PendingElement arbPend();

            // map 里没有任何 srcid 的记录时返回 true。
            bool empty() const { return pendingPool.empty(); }

            PendingRetry() = default;

          private:
            // 轮询指针 +1 回绕 (HighHigh → High → Medium → Low → HighHigh)。
            static PoolPriority nextPriority(PoolPriority p)
            {
                int next = static_cast<int>(p) + 1;
                return next == PoolPriorityNum
                    ? HighHigh
                    : static_cast<PoolPriority>(next);
            }

            // 该 srcid 是否有积压; 有则推进 arbSrcPointer 并返回 srcid,
            // 无则返回 -1。
            SrcId tryPick(std::map<SrcId, Entry>::iterator it);

            int pendingNum(int qos, SrcId srcid) const
            {
                auto it = pendingPool.find(srcid);
                return it == pendingPool.end()
                    ? -1
                    : it->second.counts[WhichPriority(qos)];
            }
        };

        struct RetryFifo
        {
            struct Item
            {
                int srcid;
                int qos;
            };

            explicit RetryFifo(int capacity)
              : buf(capacity), capacity(capacity)
            {
            }

            bool push(const Item& item)          // 满 → false, 策略交给调用方
            {
                if (count == capacity)
                    return false;
                buf[tail] = item;
                tail = (tail + 1) % capacity;
                count++;
                return true;
            }

            bool pop(Item& out)                  // 空 → false
            {
                if (count == 0)
                    return false;
                out = buf[head];
                head = (head + 1) % capacity;
                count--;
                return true;
            }

            bool full()  const { return count == capacity; }
            bool empty() const { return count == 0; }
            int  size()  const { return count; }

          private:
            std::vector<Item> buf;
            int head = 0;
            int tail = 0;
            int count = 0;
            int capacity;
        };


        const int RnfNum;
        QosPool m_qosPool;
        PendingRetry m_PendingRetry;
        RetryFifo m_RetryFifo;
        std::array<MemFn<RawReq>, 4> reqFuncs;
        std::array<MemFn<RawRsp>, 4> rspFuncs;
        std::array<MemFn<RawSnp>, 4> snpFuncs;
        std::array<MemFn<RawDat>, 4> datFuncs;


        template<typename FlitType>
        auto& funcsFor();

        void advancePipeline(FlitVariant& fv);

        // flit 类型 → 通道映射 (与 ChannelType 枚举顺序一致)。
        template <typename FlitType>
        static ChannelType channelOf()
        {
            if constexpr (std::is_same_v<FlitType, RawReq>) {
                return ChannelType::REQ;
            } else if constexpr (std::is_same_v<FlitType, RawRsp>) {
                return ChannelType::RSP;
            } else if constexpr (std::is_same_v<FlitType, RawSnp>) {
                return ChannelType::SNP;
            } else {
                static_assert(std::is_same_v<FlitType, RawDat>,
                              "Unsupported CHI flit type");
                return ChannelType::DAT;
            }
        }

        void ArbPcrdCredit();

        // 流水非空 或 retry 池/队列有待发数据时返回 true。
        // wakeup 据此决定是否调度下一拍 (按需唤醒, 省仿真时间)。
        bool hasPendingWork() const;

        //pipline function
        void doStageH0_Req(RawReq* Req);
        void doStageH0_Rsp(RawRsp* Rsp);
        void doStageH0_Snp(RawSnp* Snp);
        void doStageH0_Dat(RawDat* Dat);

        void doStageH1_Req(RawReq* Req);
        void doStageH1_Rsp(RawRsp* Rsp);
        void doStageH1_Snp(RawSnp* Snp);
        void doStageH1_Dat(RawDat* Dat);




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

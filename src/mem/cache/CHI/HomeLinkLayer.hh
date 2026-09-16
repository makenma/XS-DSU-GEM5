#ifndef __HOMELINKLAYER__HH__
#define __HOMELINKLAYER__HH__

#include <array>
#include <map>
#include <optional>
#include <vector>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/HomeLinkLayer.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "mem/cache/CHI/base/ChiPipeline.hh"
#include "mem/ruby/common/Consumer.hh"

namespace gem5::Chi {

class HomeNodeFull;
class HomePocq;

class HomeLinkLayer : public ruby::Consumer,
                      private ChiPipeline<HomeLinkLayer, 2>
{
    public:
        HomeLinkLayer(HomeNodeFull *HNF, HomePocq *pocq,
            const std::array<int, 4>& thresholds,
            const int RnfNum,
            const int retryfifo_num
        );
        void wakeup() override;
        void print(std::ostream& out) const override {};
        inline void setRxPort(ChiCommonPort* port) { rxport = port; }
        void releaseQos(PoolPriority priority);
    private:
        friend class ChiPipeline<HomeLinkLayer, 2>;

        HomeNodeFull *m_homenode;
        HomePocq *m_HomePocq;
        ChiCommonPort *rxport;

        struct QosPool
        {
            enum PoolDim { QosCount, QosThreshold, PoolDimNum };
            std::array<std::array<int, PoolDimNum>, PoolPriorityNum> pool = {};

            // Try to push into the target priority pool, falling back to
            // lower-priority pools if the target one is full. Mutates pool
            // on success.
            bool tryPush(PoolPriority Pri)
            {
                const auto selected = selectPool(Pri);
                return selected && reserve(*selected);
            }

            std::optional<PoolPriority>
            selectPool(PoolPriority pri) const
            {
                for (int p = pri; p < PoolPriorityNum; ++p) {
                    if (pool[p][QosCount] < pool[p][QosThreshold])
                        return static_cast<PoolPriority>(p);
                }
                return std::nullopt;
            }

            bool reserve(PoolPriority priority)
            {
                if (pool[priority][QosCount] >=
                    pool[priority][QosThreshold]) {
                    return false;
                }
                ++pool[priority][QosCount];
                return true;
            }

            void release(PoolPriority priority)
            {
                panic_if(pool[priority][QosCount] == 0,
                         "releasing an empty QoS pool");
                --pool[priority][QosCount];
            }

            bool canPush(PoolPriority pri) const
            {
                return selectPool(pri).has_value();
            }

            bool enqueue(int qos) {
                return tryPush(WhichPriority(qos));
            }

            bool canEnqueue(int qos) const
            {
                return canPush(WhichPriority(qos));
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

        void ArbPcrdCredit();

        // 流水非空 或 retry 池/队列有待发数据时返回 true。
        // wakeup 据此决定是否调度下一拍 (按需唤醒, 省仿真时间)。
        bool hasPendingWork() const;

        // ChiPipeline 调用的统一 stage 入口。它们再根据
        // FlitVariant 的实际类型选择下面的重载函数。
        void dispatchStageH0(FlitVariant &flit);
        void dispatchStageH1(FlitVariant &flit);

        // Pipeline functions. std::visit 通过参数类型自动选择重载。
        void doStageH0(RawReq *req);
        void doStageH0(RawRsp *rsp);
        void doStageH0(RawSnp *snp);
        void doStageH0(RawDat *dat);

        void doStageH1(RawReq *req);
        void doStageH1(RawRsp *rsp);
        void doStageH1(RawSnp *snp);
        void doStageH1(RawDat *dat);




};


}




#endif

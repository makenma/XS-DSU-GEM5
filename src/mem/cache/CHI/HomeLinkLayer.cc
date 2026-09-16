#ifndef __HOMELINKLAYER__CC__
#define __HOMELINKLAYER__CC_


#include "mem/cache/CHI/HomeLinkLayer.hh"

#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/HomePocq.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi {

#define IS_THIS_STAGE(t , stage)\
    if (!t->isCurrentStage(stage)) return;

HomeLinkLayer::HomeLinkLayer(HomeNodeFull* hnf, HomePocq* pocq,
                          const std::array<int, 4>& thresholds,
                            const int RnfNum, const int retryfifo_num)
  : Consumer(hnf),
    ChiPipeline({
        &HomeLinkLayer::dispatchStageH0,
        &HomeLinkLayer::dispatchStageH1,
    }),
    m_homenode(hnf),
    m_HomePocq(pocq),
    RnfNum(RnfNum),
    m_qosPool(thresholds),
    m_RetryFifo(retryfifo_num)
{}

bool
HomeLinkLayer::PendingRetry::enqueue(RawReq req)
{
    // operator[] 自动建条目: counts 全 0, arbPointer = HighHigh。
    pendingPool[req.srcid].counts[WhichPriority(req.qos)]++;
    return true;
}

PoolPriority
HomeLinkLayer::PendingRetry::arbQos(SrcId srcid)
{
    auto it = pendingPool.find(srcid);
    if (it == pendingPool.end())
        return PoolPriorityNum;

    Entry& e = it->second;
    for (int idx = 0; idx < PoolPriorityNum; ++idx) {
        int pri = (static_cast<int>(e.arbPointer) + idx) % PoolPriorityNum;
        if (e.counts[pri] > 0) {
            e.counts[pri]--;
            // 指针推进到赢家之后: 被服务的优先级排到队尾, 保证公平轮询。
            e.arbPointer = nextPriority(static_cast<PoolPriority>(pri));
            return static_cast<PoolPriority>(pri);
        }
    }
    return PoolPriorityNum;
}

HomeLinkLayer::PendingRetry::SrcId
HomeLinkLayer::PendingRetry::arbSrcId()
{
    // 从 arbSrcPointer 起绕一圈: [pointer, end) 再回绕 [begin, pointer)。
    auto stop = pendingPool.lower_bound(arbSrcPointer);

    for (auto it = stop; it != pendingPool.end(); ++it) {
        SrcId win = tryPick(it);
        if (win != -1)
            return win;
    }
    for (auto it = pendingPool.begin(); it != stop; ++it) {
        SrcId win = tryPick(it);
        if (win != -1)
            return win;
    }
    return -1;
}

HomeLinkLayer::PendingRetry::SrcId
HomeLinkLayer::PendingRetry::tryPick(std::map<SrcId, Entry>::iterator it)
{
    for (int count : it->second.counts) {
        if (count > 0) {
            arbSrcPointer = it->first + 1;   // 推进到赢家之后, 保证公平
            return it->first;
        }
    }
    return -1;
}

HomeLinkLayer::PendingElement
HomeLinkLayer::PendingRetry::arbPend()
{
    SrcId srcid = arbSrcId();
    if (srcid == -1)
        return PendingElement{-1, -1};       // 无待重试

    PoolPriority pri = arbQos(srcid);
    if (pri == PoolPriorityNum)
        panic("arbPend: srcid %d went empty between arbSrcId and arbQos",
              srcid);
    return PendingElement{srcid, pri};
}

void
HomeLinkLayer::releaseQos(PoolPriority priority)
{
    m_qosPool.release(priority);
}


void
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeLinklayer wakeup!!!\n");
    DPRINTF(HomeLinkLayer, "home wakeup: rxport=%p\n", rxport);

    // ① retry 仲裁: 与流水【同一拍并行】的独立数据通路, 不是串行步骤。
    //    放在最前 = retry 优先占用 REQ credit (硬件防死锁惯例);
    //    想给流水优先就挪到循环后面。每拍无条件执行, 不依赖 REQ 到达。
    ArbPcrdCredit();

    for (std::size_t i = 0; i < NUM_CHANNELS; ++i) {
        const auto channel = static_cast<ChannelType>(i);

        // ①② 公共层推进整条流水并按顺序退休。TX 无
        // credit 时 callback 返回 false，flit 会恢复到队首。
        drivePipeline(i, [this, channel](FlitVariant &flit) {
            return rxport->enqueueTx(channel, flit);
        });

        // ③ 准入: RX 有新 flit 则放入对应 channel 流水。
        auto flit = rxport->getRxFlit(channel);
        if (flit)
            enqueuePipeline(i, std::move(*flit));
    }

    // 按需调度: 有活才排下一拍, 没活就睡 (省仿真时间)。
    // 唤醒源: ①新 flit 到达 (端口 wakeup) ②credit 返还 (increaseTxCredit
    // 里 wakeup) ③这里自调度。三者缺一就会卡死。
    if (hasPendingWork())
        scheduleEvent(gem5::Cycles(1));
}

bool
HomeLinkLayer::hasPendingWork() const
{
    // 流水里还有 flit, 或 retry 池/队列有待发数据
    return hasPipelineWork() ||
           !m_RetryFifo.empty() || !m_PendingRetry.empty();
}

void
HomeLinkLayer::dispatchStageH0(FlitVariant &flit)
{
    std::visit([this](auto &raw) { doStageH0(&raw); }, flit);
}

void
HomeLinkLayer::dispatchStageH1(FlitVariant &flit)
{
    std::visit([this](auto &raw) { doStageH1(&raw); }, flit);
}


void HomeLinkLayer::ArbPcrdCredit(){
    // 先查 credit 再出队: 不满足就下拍再试, 不空转不丢序
    if (m_PendingRetry.empty())
        return;
    if (!rxport->hasTxCredit(ChannelType::RSP))
        return;

    PendingElement pcrd_grant = m_PendingRetry.arbPend();
    //TODO: 用 pcrd_grant (srcid/优先级) 构造 retry flit 并 enqueueTx 发送
}




// RawReq
void HomeLinkLayer::doStageH0(RawReq* Req) {
    IS_THIS_STAGE(Req, 0)
    Req->next_stage();
    DPRINTF(HomeLinkLayer, "HomeLinklayer get req!!!\n");
}
void HomeLinkLayer::doStageH1(RawReq* Req) {
    IS_THIS_STAGE(Req, 1)
    panic_if(!m_HomePocq, "HomeLinkLayer has no HomePocq");

    // 先做 QoS 容量的无副作用检查，再准入 POCQ。这样
    // POCQ 失败时不会留下一个已占用的 QoS 计数。
    const auto qos_pool =
        m_qosPool.selectPool(WhichPriority(Req->qos));
    if (qos_pool &&
        m_HomePocq->allocate(FlitVariant{*Req}, *qos_pool)) {
        // selectPool() 和 reserve() 之间没有并发修改，因此这里
        // 必须成功。Entry 保存的也是实际占用的 pool 优先级。
        panic_if(!m_qosPool.reserve(*qos_pool),
                 "QoS pool changed during POCQ admission");
    }
    else {
        //fast path
        if (m_RetryFifo.full()){
            //TODO:stall req credit
        }
        else {
            m_RetryFifo.push({static_cast<int>(Req->srcid), Req->qos});
            m_PendingRetry.enqueue(*Req);
            PendingElement pcrd_grant;
            pcrd_grant = m_PendingRetry.arbPend();
            //TODO:return req credit send Pcrd_grant to RNf
        }
    }
    Req->next_stage();
}

// RawRsp
void HomeLinkLayer::doStageH0(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 0) Rsp->next_stage();
}
void HomeLinkLayer::doStageH1(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 1) Rsp->next_stage();
}

// RawSnp
void HomeLinkLayer::doStageH0(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 0) Snp->next_stage();
}
void HomeLinkLayer::doStageH1(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 1) Snp->next_stage();
}

// RawDat
void HomeLinkLayer::doStageH0(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 0) Dat->next_stage();
}
void HomeLinkLayer::doStageH1(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 1) Dat->next_stage();
}



}



#endif

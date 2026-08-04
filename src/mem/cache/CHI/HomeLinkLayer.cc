#ifndef __HOMELINKLAYER__CC__
#define __HOMELINKLAYER__CC_


#include "mem/cache/CHI/HomeLinkLayer.hh"

#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi {

#define IS_THIS_STAGE(t , stage)\
    if (!t->isCurrentStage(stage)) return;

HomeLinkLayer::HomeLinkLayer( HomeNodeFull* hnf,
                          const std::array<int, 4>& thresholds,
                            const int RnfNum, const int retryfifo_num)
  : Consumer(hnf),
    m_homenode(hnf),
    RnfNum(RnfNum),
    m_qosPool(thresholds),
    m_RetryFifo(retryfifo_num)
{
    reqFuncs = {
        &HomeLinkLayer::doStageH0_Req,
        &HomeLinkLayer::doStageH1_Req,
        &HomeLinkLayer::doStageH2_Req,
        &HomeLinkLayer::doStageH3_Req
        };
     rspFuncs = {
        &HomeLinkLayer::doStageH0_Rsp,
        &HomeLinkLayer::doStageH1_Rsp,
        &HomeLinkLayer::doStageH2_Rsp,
        &HomeLinkLayer::doStageH3_Rsp
    };
    snpFuncs = {
        &HomeLinkLayer::doStageH0_Snp,
        &HomeLinkLayer::doStageH1_Snp,
        &HomeLinkLayer::doStageH2_Snp,
        &HomeLinkLayer::doStageH3_Snp
    };
    datFuncs = {
        &HomeLinkLayer::doStageH0_Dat,
        &HomeLinkLayer::doStageH1_Dat,
        &HomeLinkLayer::doStageH2_Dat,
        &HomeLinkLayer::doStageH3_Dat
    };
}

bool
HomeLinkLayer::PendingRetry::enqueue(RawReq req)
{
    // operator[] 自动建条目: counts 全 0, arbPointer = HighHigh。
    pendingPool[req.srcid].counts[WhichPriority(req.qos)]++;
    return true;
}

HomeLinkLayer::PendingRetry::PoolPriority
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
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeLinklayer wakeup!!!\n");
    DPRINTF(HomeLinkLayer, "home wakeup: rxport=%p\n", rxport);

    for (int i = 0; i < 4; ++i) {
        std::visit([this, i](auto& f) {
            using FlitType = std::decay_t<decltype(f)>;
            auto& q = in_flight[i];

            // ① 推进: 每个在途 flit 每周期走一级 (H0→H1→H2→H3)
            for (auto& fv : q)
                advancePipeline(fv);

            // ② 出流水: 走完 H3 的 (stage>=4) 发到 TX; TX 无 credit 则
            //    留在队头等下周期再发 (背压, 不丢 flit)
            while (!q.empty() &&
                   std::visit([](auto& x) { return x.stage >= 4; },
                              q.front())) {
                FlitVariant done = std::move(q.front());
                q.pop_front();
                if (!rxport->enqueueTx(channelOf<FlitType>(), done)) {
                    q.push_front(done);
                    break;
                }
            }

            // ③ 准入: rx 有新 flit 则入流水 (rx 深度受 credit 限制)
            auto getFlit = rxport->getRxFlit(typeid(f));
            if (getFlit)
                q.push_back(*getFlit);
        }, flits[i]);
    }
}

void
HomeLinkLayer::advancePipeline(FlitVariant& fv)
{
    std::visit([this](auto& flit) {
        using FlitType = std::decay_t<decltype(flit)>;
        auto& funcs = funcsFor<FlitType>();

        if (flit.stage >= funcs.size()) {
            DPRINTF(HomeLinkLayer,
                    "advancePipeline: stage=%d out of range\n",
                    static_cast<int>(flit.stage));
            return;
        }

        for(auto fn=funcs.rbegin(); fn!=funcs.rend(); ++fn) {
            if (*fn) {
                (this->**fn)(&flit);
            }
        }
    }, fv);
}




// RawReq
void HomeLinkLayer::doStageH0_Req(RawReq* Req) {
    IS_THIS_STAGE(Req, 0)
    Req->next_stage();
    DPRINTF(HomeLinkLayer, "HomeLinklayer get req!!!\n");
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH1_Req(RawReq* Req) {
    IS_THIS_STAGE(Req, 1)
    if (m_qosPool.enqueue(Req->qos)){
        //TODO: transfer flit to qocq entry then send req credit

    }
    else{
        //fast path
        if (m_RetryFifo.full()){
            //TODO:stall req credit
        }
        else {
            m_RetryFifo.push({static_cast<int>(Req->srcid), Req->qos});
            //TODO:return req credit
        }
    }
    Req->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH2_Req(RawReq* Req) {
    IS_THIS_STAGE(Req, 2) Req->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH3_Req(RawReq* Req) {
    IS_THIS_STAGE(Req, 3) Req->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}

// RawRsp
void HomeLinkLayer::doStageH0_Rsp(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 0) Rsp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH1_Rsp(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 1) Rsp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH2_Rsp(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 2) Rsp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH3_Rsp(RawRsp* Rsp) {
    IS_THIS_STAGE(Rsp, 3) Rsp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}

// RawSnp
void HomeLinkLayer::doStageH0_Snp(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 0) Snp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH1_Snp(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 1) Snp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH2_Snp(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 2) Snp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH3_Snp(RawSnp* Snp) {
    IS_THIS_STAGE(Snp, 3) Snp->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}

// RawDat
void HomeLinkLayer::doStageH0_Dat(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 0) Dat->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH1_Dat(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 1) Dat->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH2_Dat(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 2) Dat->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH3_Dat(RawDat* Dat) {
    IS_THIS_STAGE(Dat, 3) Dat->next_stage();
    m_homenode->scheduleEvent(gem5::Cycles(1));
}



}



#endif

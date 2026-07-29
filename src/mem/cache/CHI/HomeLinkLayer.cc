#ifndef __HOMELINKLAYER__CC__
#define __HOMELINKLAYER__CC_


#include "mem/cache/CHI/HomeLinkLayer.hh"

#include <optional>

#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi {

#define IS_THIS_STAGE(t , stage)\
    if (!t->isCurrentStage(stage)) return;

HomeLinkLayer::HomeLinkLayer( HomeNodeFull* hnf,
                          const std::array<int, 4>& thresholds)
  : Consumer(hnf),
    m_homenode(hnf),
    m_qosPool(thresholds)
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

void
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeLinklayer wakeup!!!\n");
    DPRINTF(HomeLinkLayer, "home wakeup: rxport=%p\n", rxport);

    for (auto& flit : flits) {
        std::visit([this](auto& f) {
            LoopChannelPipline(typeid(f));
        }, flit);
    }
}

void
HomeLinkLayer::LoopChannelPipline(const std::type_index& flitType)
{
    auto getFlit = rxport->getRxFlit(flitType);
    if (!getFlit) {
        return;
    }

    advancePipeline(*getFlit);
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
    IS_THIS_STAGE(Req, 1) Req->next_stage();
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

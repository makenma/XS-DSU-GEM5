#ifndef __HOMELINKLAYER__CC__
#define __HOMELINKLAYER__CC_


#include "mem/cache/CHI/HomeLinkLayer.hh"

#include <optional>

#include "mem/cache/CHI/HomeNodeFull.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi {

HomeLinkLayer::HomeLinkLayer( HomeNodeFull* hnf)
  : Consumer(hnf),
    m_homenode(hnf)
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
    pipelineMap[typeid(RawReq)] = initPipeline(reqFuncs);
    pipelineMap[typeid(RawRsp)] = initPipeline(rspFuncs);
    pipelineMap[typeid(RawSnp)] = initPipeline(snpFuncs);
    pipelineMap[typeid(RawDat)] = initPipeline(datFuncs);



}

void
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "HomeLinklayer wakeup!!!\n");
    DPRINTF(HomeLinkLayer, "home wakeup: rxport=%p\n", rxport);
    for (ChannelType ch : {ChannelType::REQ, ChannelType::RSP, ChannelType::SNP, ChannelType::DAT}) {
        auto flit = rxport->getRxFlit(ch);
        if (flit.has_value()){
            DPRINTF(HomeLinkLayer, "got flit on channel %d\n", (int)ch);
            FlitVariant rxflit = flit.value();
            advancePipeline(rxflit);

        }

    }


}

void HomeLinkLayer::advancePipeline(FlitVariant& fv)
{
    std::visit([&](auto& f) {
        using T = std::decay_t<decltype(f)>;
        auto& funcs = pipelineMap.at(typeid(T));

        DPRINTF(HomeLinkLayer,
            "advancePipeline: type=%s stage=%d funcs.size=%d\n",
            typeid(T).name(), (int)f.stage, (int)funcs.size());

        if (f.stage < funcs.size() && funcs[f.stage]) {
            DPRINTF(HomeLinkLayer, "dispatching stage %d\n", (int)f.stage);
            funcs[f.stage](static_cast<BaseFlit*>(&f));
        } else {
            DPRINTF(HomeLinkLayer,
                "skip dispatch: stage=%d funcs.size=%d\n",
                (int)f.stage, (int)funcs.size());
        }
    }, fv);
}


// RawReq
void HomeLinkLayer::doStageH0_Req(RawReq* Req) {
    Req->next_stage();
    DPRINTF(HomeLinkLayer, "HomeLinklayer get req!!!\n");
    m_homenode->scheduleEvent(gem5::Cycles(1));
}
void HomeLinkLayer::doStageH1_Req(RawReq* Req) { Req->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH2_Req(RawReq* Req) { Req->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH3_Req(RawReq* Req) { Req->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }

// RawRsp
void HomeLinkLayer::doStageH0_Rsp(RawRsp* Rsp) { Rsp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH1_Rsp(RawRsp* Rsp) { Rsp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH2_Rsp(RawRsp* Rsp) { Rsp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH3_Rsp(RawRsp* Rsp) { Rsp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }

// RawSnp
void HomeLinkLayer::doStageH0_Snp(RawSnp* Snp) { Snp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH1_Snp(RawSnp* Snp) { Snp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH2_Snp(RawSnp* Snp) { Snp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH3_Snp(RawSnp* Snp) { Snp->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }

// RawDat
void HomeLinkLayer::doStageH0_Dat(RawDat* Dat) { Dat->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH1_Dat(RawDat* Dat) { Dat->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH2_Dat(RawDat* Dat) { Dat->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }
void HomeLinkLayer::doStageH3_Dat(RawDat* Dat) { Dat->next_stage(); m_homenode->scheduleEvent(gem5::Cycles(1)); }



}



#endif

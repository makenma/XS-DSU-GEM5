// Protocol support added to the pinned coarse model. Runs identically with the
// FlowGraph observer enabled or disabled. Credits are owned by a node pair and
// PCrdType; PCrdGrant.TxnID never participates in matching.
namespace gem5::Chi {
void HnfCoherencyController::retryCreditedDownstreamRequests()
{
    for (uint32_t id=0;id<entries.size();++id) {
        Entry& entry=entries[id];
        if (!entry.mcRetryPending) continue;
        auto key=std::make_tuple(entry.mcRequest.srcid,entry.mcRequest.tgtid,entry.mcRetryType);
        auto credit=downstreamCredits.find(key);
        if (credit==downstreamCredits.end()||!credit->second) continue;
        --credit->second;
        const uint32_t previous=entry.mcTxnId;
        const uint32_t next=allocateMcTxnId();
        panic_if(mcTxnToEntry.erase(previous)!=1,"HnfCC retry lost MC identity\n");
        mcTxnToEntry.emplace(next,id);entry.mcTxnId=next;
        entry.mcRequest.txnid=next;entry.mcRequest.AllowRetry=0;
        entry.mcRequest.ReturnTxnId=next;
        entry.mcRequest.pcrdtype=entry.mcRetryType;
        if (!entry.mcRequest.flow.rootId.empty()) {
            entry.mcRequest.flow.attemptId=std::to_string(std::stoul(entry.mcRequest.flow.attemptId)+1);
            entry.mcRequest.flow.messageId.clear();FlowGraphTrace::get().stamp(entry.mcRequest);
        }
        entry.mcFlow=entry.mcRequest.flow;
        HnfCcTxReq out{};out.entry=id;out.req=entry.mcRequest;txReqQ.push_back(out);
        entry.mcRetryPending=false;entry.mcReadIssued=false;
        FlowGraphTrace::get().wait(entry.mcFlow,entry.req.tgtid,"pcredit",false,"");
    }
    for (auto& [id,victim]:dirtyVictimTxns) {
        (void)id;
        if(victim.phase!=DirtyVictimPhase::RetryPending) continue;
        auto key=std::make_tuple(victim.homeNodeId,selectSnNode(victim.victim.lineAddress),victim.retryPcrdtype);
        auto credit=downstreamCredits.find(key);
        if(credit==downstreamCredits.end()||!credit->second) continue;
        --credit->second;queueDirtyVictimRequest(victim,false,victim.retryPcrdtype);
    }
}
}

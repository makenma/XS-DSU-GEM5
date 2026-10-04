
namespace gem5::Chi {
std::vector<uint32_t>
Cache2ChiBridge::acceptRetryResponse(
    const RawRsp& rsp, uint32_t requester,
    std::unordered_map<uint32_t, TxnEntry>& transactions, RetryCredits& credits)
{
    // B2.10: PCrdGrant belongs to a node pair and credit type, never a TxnID.
    if (rsp.tgtid != requester) return {};
    if (rsp.opcode == 0x07) {
        ++credits[std::make_tuple(requester, rsp.srcid, rsp.pcrdtype)];
    } else if (rsp.opcode == 0x03) {
        auto it = transactions.find(rsp.txnid);
        panic_if(it == transactions.end(), "RetryAck for unknown RN request");
        auto& txn = it->second;
        panic_if(txn.req.tgtid != rsp.srcid || !txn.req.AllowRetry ||
                     txn.retryBlocked || txn.gotComp || txn.gotData || txn.hasDbid,
                 "Invalid RetryAck for RN attempt");
        txn.retryBlocked = true;
        txn.req.pcrdtype = rsp.pcrdtype;
        ++txn.retryCount;
    } else {
        return {};
    }
    // Deterministic allocation when multiple rejected requests share a credit.
    std::vector<uint32_t> candidates, ready;
    for (const auto& [id, txn] : transactions)
        if (txn.retryBlocked && !txn.retryQueued) candidates.push_back(id);
    std::sort(candidates.begin(), candidates.end());
    for (auto id : candidates) {
        auto& txn = transactions.at(id);
        const auto key = std::make_tuple(txn.req.srcid, txn.req.tgtid,
                                        uint32_t(txn.req.pcrdtype));
        auto credit = credits.find(key);
        if (credit == credits.end() || credit->second == 0) continue;
        if (--credit->second == 0) credits.erase(credit);
        txn.req.AllowRetry = 0;
        txn.retryQueued = true;
        ready.push_back(id);
    }
    return ready;
}
} // namespace gem5::Chi

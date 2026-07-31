#ifndef __MEM_CACHE_CHI_CHI2CLASSICMEMBRIDGETXNKEY_HH__
#define __MEM_CACHE_CHI_CHI2CLASSICMEMBRIDGETXNKEY_HH__

#include <cstdint>
#include <functional>

#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5
{
namespace Chi
{

/**
 * CHI transaction identity at an SN-F.
 *
 * TxnID is allocated independently by every requester, so it is only unique
 * within a SrcID.  In particular, several HN-Fs can legally send requests
 * with the same TxnID to one shared SN-F bridge.
 */
struct Chi2ClassicTxnKey
{
    uint32_t srcId = 0;
    uint32_t txnId = 0;

    static Chi2ClassicTxnKey
    fromReq(const RawReq& req)
    {
        return {req.srcid, req.txnid};
    }

    static Chi2ClassicTxnKey
    fromWriteData(const RawDat& dat)
    {
        return {dat.srcid, dat.txnid};
    }

    /** Select the original requester unless a legacy fixed HN-F is set. */
    static uint32_t
    responseTarget(const RawReq& req, uint32_t fixed_hnf_id)
    {
        return fixed_hnf_id ? fixed_hnf_id : req.srcid;
    }

    bool
    operator==(const Chi2ClassicTxnKey& other) const
    {
        return srcId == other.srcId && txnId == other.txnId;
    }

    bool
    operator!=(const Chi2ClassicTxnKey& other) const
    {
        return !(*this == other);
    }
};

struct Chi2ClassicTxnKeyHash
{
    size_t
    operator()(const Chi2ClassicTxnKey& key) const
    {
        const uint64_t packed =
            (static_cast<uint64_t>(key.srcId) << 32) | key.txnId;
        return std::hash<uint64_t>{}(packed);
    }
};

} // namespace Chi
} // namespace gem5

#endif // __MEM_CACHE_CHI_CHI2CLASSICMEMBRIDGETXNKEY_HH__

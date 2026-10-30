#ifndef __CHI_NATIVE_CHANNEL_HH__
#define __CHI_NATIVE_CHANNEL_HH__

#include "mem/cache/CHI/base/ChiChannel.hh"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <variant>
#include <vector>
#include <string>

namespace gem5
{

namespace Chi
{






    enum class RspKind : uint8_t
    {
        MainPath,
        ShortPath,
        RetryAck,
        PCrdGrant
    };

    // FlowGraph observer metadata, never used for architectural decisions.
    struct FlowGraphIdentity {
        std::string rootId, legId, parentLegId, attemptId, messageId;
        std::string payloadOrigin = "unknown";
        uint64_t address = 0;
        std::vector<std::string> causeInstructionIds, dataProducerInstructionIds;
    };

    struct NativeBaseFlit
    {
        FlowGraphIdentity flow;
        // Transport mode metadata, not a CHI field. Native endpoints construct
        // Issue H field values before handing the flit to the router.
        bool nativeChi = false;
        uint8_t      qos = 0;
        uint32_t     srcid = 0;
        uint32_t     tgtid = 0;
        uint32_t     txnid = 0;
        uint8_t      opcode = 0;
        //module pipline virtual in rawflit
        uint32_t     stage = 0;

        void next_stage(){
            this->stage++;
        }

    };

    struct NativeReq:NativeBaseFlit
    {
        uint8_t     AllowRetry = 0;
        uint64_t    addr = 0;
        uint8_t     size = 0;
        uint32_t    ReturnNid = 0;
        uint32_t    ReturnTxnId = 0;
        uint32_t    stashNid = 0;
        bool likelyShared = false;
        bool cah = false;

        uint8_t     order = 0;
        uint8_t     pcrdtype = 0;
        uint8_t     memattr = 0;
        uint8_t     snpattr = 0;
        bool        expCompAck = false;
        bool        traceTag = false;
        uint8_t     srcType = 0;
        uint8_t     ldid = 0;
        // Internal provenance carried losslessly with the modeled flit. This
        // is not inferred from transaction-number ranges or CHI attributes.
        bool        hnfDirtyVictim = false;
    };

    struct NativeRsp:NativeBaseFlit
    {
        FlowGraphIdentity pullFlow;
        uint16_t    dbid = 0;
        uint8_t     resp = 0;
        uint8_t     respErr = 0;
        uint8_t     pcrdtype = 0;
        RspKind     rspKind = RspKind::MainPath;
        uint64_t    originSeq = 0;
        uint64_t    originCycle = 0;
    };


    struct NativeSnp:NativeBaseFlit
    {
        bool retToSrc = false;
        bool doNotGoToSD = false;
        uint64_t    addr = 0;
        uint8_t     size = 0;
    };

    struct NativeDat:NativeBaseFlit
    {
        uint8_t     last = 0;
        uint32_t    HomeNID = 0;
        uint16_t    dbid = 0;
        uint8_t     dataid = 0;
        uint8_t     resp = 0;
        uint32_t    beatOffset = 0;
        std::vector<uint8_t> byteEnable;
        std::vector<uint8_t> chunkValid;
        std::vector<uint8_t> data;

        size_t
        byteLength() const
        {
            return data.size();
        }
    };

    using NativeFlitVariant = std::variant<NativeReq, NativeRsp, NativeSnp, NativeDat>;



}

}




#endif

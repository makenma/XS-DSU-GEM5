#ifndef __CHICHANNEL__HH__
#define __CHICHANNEL__HH__

#include <cstddef>
#include <cstdint>
#include <functional>
#include <variant>
#include <vector>

namespace gem5
{

namespace Chi
{


    enum ChannelType: uint8_t
    {
        REQ = 0,
        RSP = 1,
        SNP = 2,
        DAT = 3,
        NUM_CHANNELS
    };



    enum class RspKind : uint8_t
    {
        MainPath,
        ShortPath,
        RetryAck,
        PCrdGrant
    };

    struct BaseFlit
    {
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

    struct RawReq:BaseFlit
    {
        uint8_t     AllowRetry = 0;
        uint64_t    addr = 0;
        uint8_t     size = 0;
        uint32_t    ReturnNid = 0;

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
        // ---- co-sim ABI v2 fields (ChiCosimBridge socket protocol) -------
        // CHI ReturnTxnID: independent of txnid; wide enough for the
        // producer's full transaction id (the SN-F adapter virtualizes it
        // onto the 12-bit wire).  returnTxnIdValid separates "field
        // absent" from the perfectly legal value 0.
        uint32_t    ReturnTxnID = 0;
        bool        returnTxnIdValid = false;
        bool        nonSecure = false;
        uint8_t     user = 0;       // 4-bit C2XM user field
    };

    struct RawRsp:BaseFlit
    {
        // 12-bit wire DBID identifier (C2XM slot / HNF return tag for DMT).
        uint16_t    dbid = 0;
        uint8_t     resp = 0;
        uint8_t     respErr = 0;
        uint8_t     pcrdtype = 0;
        RspKind     rspKind = RspKind::MainPath;
        uint64_t    originSeq = 0;
        uint64_t    originCycle = 0;
        bool        traceTag = false;
    };


    struct RawSnp:BaseFlit
    {
        uint64_t    addr = 0;
        uint8_t     size = 0;
    };

    struct RawDat:BaseFlit
    {
        uint8_t     last = 0;
        uint32_t    HomeNID = 0;
        // 12-bit wire DBID identifier; 0 is a valid DBID value.
        uint16_t    dbid = 0;
        uint8_t     dataid = 0;
        uint8_t     resp = 0;
        uint32_t    beatOffset = 0;
        std::vector<uint8_t> byteEnable;
        std::vector<uint8_t> chunkValid;
        std::vector<uint8_t> data;

        // ---- co-sim ABI v2 fields (error / metadata preservation) --------
        uint8_t     respErr = 0;
        uint8_t     dataSource = 0;
        // Poison granularity for this C2XM profile: one bit per 8-byte chunk
        // of the 32-byte beat (4 bits).
        uint8_t     poison = 0;
        uint8_t     ccid = 0;
        bool        traceTag = false;
        // DataCheck bytes, one per 8-byte chunk when present (empty = none).
        std::vector<uint8_t> dataCheck;

        size_t
        byteLength() const
        {
            return data.size();
        }
    };

    struct FlitId
    {
        uint8_t XId;
        uint8_t YId;
        uint8_t PId;
        uint8_t DId;
    };

    inline FlitId DecodeFlitId(int id)
    {
        FlitId DecodeId;
        DecodeId.XId = (id >> 7) & 0xF;
        DecodeId.YId = (id >> 4) & 0x7;
        DecodeId.PId = (id >> 2) & 0x3;
        DecodeId.DId =  id       & 0x3;
        return DecodeId;
    }


    using StageFunc = std::function<void(BaseFlit*)>;
    using FlitVariant = std::variant<RawReq, RawRsp, RawSnp, RawDat>;



}

}




#endif

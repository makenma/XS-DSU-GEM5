#ifndef __RSPOPCODE__HH__
#define __RSPOPCODE__HH__


#include <cstdint>

namespace gem5
{

namespace Chi
{
    struct RspOpcode
    {
        uint8_t raw; // only bits[4:0] used

        uint8_t code() const { return raw & 0x1F; } // 0..31
    };

    enum class RspMajor : uint8_t
    {
        Credit,       // LCrd/PCrd 相关
        Snoop,        // SnpResp / SnpRespFwded
        Completion,   // Comp* / CompAck
        Retry,        // RetryAck
        DBID,         // DBIDResp*
        Receipt,      // ReadReceipt
        Tag,          // TagMatch
        Persist,      // Persist / CompPersist
        Stash,        // StashDone / CompStashDone
        CMO,          // CompCMO
        ReservedOrUnsupported
    };

    enum class RspMinor : uint8_t
    {
        RespLCrdReturn,
        SnpResp,
        CompAck,
        RetryAck,
        Comp,
        CompDBIDResp,
        DBIDResp,
        PCrdGrant,
        ReadReceipt,
        SnpRespFwded,
        TagMatch,
        RespSepData,
        Persist,
        CompPersist,
        DBIDRespOrd,
        StashDone,
        CompStashDone,
        CompCMO,

        ReservedOrUnsupported
    };

    struct DecodedRsp
    {
        RspOpcode op;      // 精确 raw
        RspMajor  major;   // 动作大类
        RspMinor  minor;   // 具体语义
    };

    inline DecodedRsp decodeRsp(uint8_t raw)
    {
        RspOpcode op{raw};
        const uint8_t c = op.code();

        DecodedRsp out{op, RspMajor::ReservedOrUnsupported, RspMinor::ReservedOrUnsupported};

        switch (c)
        {
            case 0x00: out.major = RspMajor::Credit;      out.minor = RspMinor::RespLCrdReturn; break;
            case 0x01: out.major = RspMajor::Snoop;       out.minor = RspMinor::SnpResp; break;
            case 0x02: out.major = RspMajor::Completion;  out.minor = RspMinor::CompAck; break;
            case 0x03: out.major = RspMajor::Retry;       out.minor = RspMinor::RetryAck; break;
            case 0x04: out.major = RspMajor::Completion;  out.minor = RspMinor::Comp; break;
            case 0x05: out.major = RspMajor::Completion;  out.minor = RspMinor::CompDBIDResp; break;
            case 0x06: out.major = RspMajor::DBID;        out.minor = RspMinor::DBIDResp; break;
            case 0x07: out.major = RspMajor::Credit;      out.minor = RspMinor::PCrdGrant; break;
            case 0x08: out.major = RspMajor::Receipt;     out.minor = RspMinor::ReadReceipt; break;
            case 0x09: out.major = RspMajor::Snoop;       out.minor = RspMinor::SnpRespFwded; break;
            case 0x0A: out.major = RspMajor::Tag;         out.minor = RspMinor::TagMatch; break;
            case 0x0B: out.major = RspMajor::Completion;  out.minor = RspMinor::RespSepData; break;
            case 0x0C: out.major = RspMajor::Persist;     out.minor = RspMinor::Persist; break;
            case 0x0D: out.major = RspMajor::Persist;     out.minor = RspMinor::CompPersist; break;
            case 0x0E: out.major = RspMajor::DBID;        out.minor = RspMinor::DBIDRespOrd; break;

            // 0x0F reserved

            case 0x10: out.major = RspMajor::Stash;       out.minor = RspMinor::StashDone; break;
            case 0x11: out.major = RspMajor::Stash;       out.minor = RspMinor::CompStashDone; break;

            // 0x12-0x13 reserved

            case 0x14: out.major = RspMajor::CMO;         out.minor = RspMinor::CompCMO; break;

            // 0x15-0x1F reserved / C2C reserved
            default: break;
        }

        return out;
    }

}

}// namespace gem5

#endif

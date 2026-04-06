#ifndef __DATOPCODE__HH__
#define __DATOPCODE__HH__

#include <cstdint>

namespace gem5
{

namespace Chi
{
/* =========================
 * Raw DAT Opcode (wire)
 * ========================= */
struct DatOpcode
{
    uint8_t raw; // only bits[3:0] valid

    constexpr uint8_t code() const
    {
        return raw & 0x0F;
    }
};

/* =========================
 * DAT Major Category
 * ========================= */
enum class DatMajor : uint8_t
{
    Credit,         // DataLCrdReturn
    SnpRespData,    // snoop response data (incl ptl/fwded)
    WriteData,      // copyback/noncopyback write data
    CompletionData, // CompData / DataSepResp / Comp-related
    Cancel,         // WriteDataCancel
    ReservedOrUnsupported
};

/* =========================
 * DAT Minor Semantic
 * ========================= */
enum class DatMinor : uint8_t
{
    DataLCrdReturn,

    SnpRespData,
    SnpRespDataPtl,
    SnpRespDataFwded,

    CopyBackWriteData,
    NonCopyBackWriteData,

    CompData,
    DataSepResp,
    NCBWrDataCompAck,

    WriteDataCancel,

    ReservedOrUnsupported
};

/* =========================
 * Decoded DAT Opcode
 * ========================= */
struct DecodedDat
{
    DatOpcode op;     // raw opcode (wire-accurate)
    DatMajor  major;  // big category
    DatMinor  minor;  // fine semantic
};

/* =========================
 * Decode function
 * ========================= */
inline DecodedDat decodeDat(uint8_t raw)
{
    DatOpcode op{raw};
    const uint8_t c = op.code();

    DecodedDat out{
        op,
        DatMajor::ReservedOrUnsupported,
        DatMinor::ReservedOrUnsupported
    };

    switch (c)
    {
        case 0x0:
            out.major = DatMajor::Credit;
            out.minor = DatMinor::DataLCrdReturn;
            break;

        case 0x1:
            out.major = DatMajor::SnpRespData;
            out.minor = DatMinor::SnpRespData;
            break;

        case 0x2:
            out.major = DatMajor::WriteData;
            out.minor = DatMinor::CopyBackWriteData;
            break;

        case 0x3:
            out.major = DatMajor::WriteData;
            out.minor = DatMinor::NonCopyBackWriteData;
            break;

        case 0x4:
            out.major = DatMajor::CompletionData;
            out.minor = DatMinor::CompData;
            break;

        case 0x5:
            out.major = DatMajor::SnpRespData;
            out.minor = DatMinor::SnpRespDataPtl;
            break;

        case 0x6:
            out.major = DatMajor::SnpRespData;
            out.minor = DatMinor::SnpRespDataFwded;
            break;

        case 0x7:
            out.major = DatMajor::Cancel;
            out.minor = DatMinor::WriteDataCancel;
            break;

        // 0x8-0xA reserved

        case 0xB:
            out.major = DatMajor::CompletionData;
            out.minor = DatMinor::DataSepResp;
            break;

        case 0xC:
            out.major = DatMajor::CompletionData;
            out.minor = DatMinor::NCBWrDataCompAck;
            break;

        // 0xD reserved for C2C use
        // 0xE-0xF reserved

        default:
            break;
    }

    return out;
}

}

}// namespace gem5


#endif

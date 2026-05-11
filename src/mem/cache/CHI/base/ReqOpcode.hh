#ifndef __REQOPCODE__HH__
#define __REQOPCODE__HH__

#include <cstdint>

namespace gem5
{


namespace Chi
{

 enum class ReqVariant : uint8_t { V0 = 0, V1 = 1 };

    struct ReqOpcode
    {
        uint8_t raw; // [6:0]

        uint8_t base() const { return raw & 0x3F; }          // Opcode[5:0]
        ReqVariant variant() const { return ReqVariant((raw >> 6) & 0x1); } // Opcode[6]
        bool v1() const { return (raw & 0x40) != 0; }
    };
    enum class ReqMajor : uint8_t
    {
        Read,
        Write,
        Maintenance,
        Atomic,
        DVM,
        Prefetch,
        ReservedOrUnsupported
    };

    enum class ReqMinor : uint8_t
    {
        // Read minors
        ReadShared,
        MakeReadUnique,
        ReadClean,
        ReadOnce,
        ReadNoSnp,
        ReadUnique,
        ReadNoSnpSep,
        ReadPreferUnique,

        // Write minors
        WriteUnique,
        WriteBack,
        WriteClean,
        WriteNoSnp,
        WriteEvict,

        // Maintenance minors
        CleanShared,
        CleanInvalid,
        CleanUnique,
        MakeInvalid,
        MakeUnique,
        Evict,
        CleanSharedPersist,
        CleanSharedPersistSep,

        // Other
        Atomic,
        DVMOp,
        PrefetchTgt,

        ReservedOrUnsupported
    };

    struct DecodedReq
    {
        ReqOpcode op;     // 第一层：精确值
        ReqMajor major;   // 第二层：大类
        ReqMinor minor;   // 第二层：子类（可选，但很有用）
    };


    inline DecodedReq decodeReq(uint8_t raw)
    {
        ReqOpcode op{raw};
        const uint8_t b = op.base();

        DecodedReq out{op, ReqMajor::ReservedOrUnsupported, ReqMinor::ReservedOrUnsupported};

        // ranges: atomic
        if (b >= 0x28 && b <= 0x2F) {
            out.major = ReqMajor::Atomic;
            out.minor = ReqMinor::Atomic;
            return out;
        }
        if (b >= 0x30 && b <= 0x37) {
            out.major = ReqMajor::Atomic;
            out.minor = ReqMinor::Atomic;
            return out;
        }

        switch (b)
        {
            case 0x01: // ReadShared / MakeReadUnique
                out.major = ReqMajor::Read;
                out.minor = op.v1() ? ReqMinor::MakeReadUnique : ReqMinor::ReadShared;
                return out;

            case 0x02: // ReadClean / WriteEvictOrEvict (bit6=1)
                if (!op.v1()) { out.major = ReqMajor::Read; out.minor = ReqMinor::ReadClean; }
                else          { out.major = ReqMajor::Write; out.minor = ReqMinor::WriteEvict; }
                return out;

            case 0x03: // ReadOnce / WriteUniqueZero (bit6=1)
                if (!op.v1()) { out.major = ReqMajor::Read; out.minor = ReqMinor::ReadOnce; }
                else          { out.major = ReqMajor::Write; out.minor = ReqMinor::WriteUnique; }
                return out;

            case 0x04: // ReadNoSnp / WriteNoSnpZero (bit6=1)
                if (!op.v1()) { out.major = ReqMajor::Read; out.minor = ReqMinor::ReadNoSnp; }
                else          { out.major = ReqMajor::Write; out.minor = ReqMinor::WriteNoSnp; }
                return out;

            case 0x07: // ReadUnique / StashOnceSepShared (bit6=1)
                if (!op.v1()) { out.major = ReqMajor::Read; out.minor = ReqMinor::ReadUnique; }
                else
                {
                    out.major = ReqMajor::Maintenance;
                    out.minor = ReqMinor::ReservedOrUnsupported;
                } // stash可另设类
                return out;

            case 0x08: // CleanShared / StashOnceSepUnique (bit6=1)
                out.major = ReqMajor::Maintenance;
                out.minor = ReqMinor::CleanShared;
                return out;

            case 0x09:
                out.major = ReqMajor::Maintenance;
                out.minor = ReqMinor::CleanInvalid;
                return out;

            case 0x0A:
                out.major = ReqMajor::Maintenance;
                out.minor = ReqMinor::MakeInvalid;
                return out;

            case 0x0B:
                out.major = ReqMajor::Maintenance;
                out.minor = ReqMinor::CleanUnique;
                return out;

            case 0x0C: // MakeUnique / ReadPreferUnique (bit6=1)
                out.major = ReqMajor::Maintenance;
                out.minor = op.v1() ? ReqMinor::ReadPreferUnique : ReqMinor::MakeUnique;
                // 你也可以把 ReadPreferUnique 归到 Read major，看你 FSM 怎么写
                return out;

            case 0x0D:
                out.major = ReqMajor::Maintenance;
                out.minor = ReqMinor::Evict;
                return out;

            case 0x14: // DVMOp / WriteUniqueFullCleanSh (bit6=1)
                if (!op.v1()) { out.major = ReqMajor::DVM; out.minor = ReqMinor::DVMOp; }
                else          { out.major = ReqMajor::Write; out.minor = ReqMinor::WriteUnique; }
                return out;

            case 0x15:
                out.major = ReqMajor::Write;
                out.minor = ReqMinor::WriteEvict;
                return out;

            case 0x17:
                out.major = ReqMajor::Write;
                out.minor = ReqMinor::WriteClean;
                return out;

            case 0x18:
            case 0x19:
                out.major = ReqMajor::Write;
                out.minor = ReqMinor::WriteUnique;
                return out;

            case 0x1B:
                out.major = ReqMajor::Write;
                out.minor = ReqMinor::WriteBack;
                return out;

            case 0x1C:
            case 0x1D:
                out.major = ReqMajor::Write;
                out.minor = ReqMinor::WriteNoSnp;
                return out;

            case 0x3A:
                out.major = ReqMajor::Prefetch;
                out.minor = ReqMinor::PrefetchTgt;
                return out;

            default:
                return out;
        }
    }

}
}// namespace gem5

#endif

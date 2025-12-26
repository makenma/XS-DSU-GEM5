#ifndef __SNOOPOPCODE__HH__
#define __SNOOPOPCODE__HH__

#include <cstdint>

namespace gem5
{

/* =========================
 * Raw Snoop Opcode (wire)
 * ========================= */
struct SnpOpcode
{
    uint8_t raw;  // only bits[4:0] valid

    constexpr uint8_t code() const
    {
        return raw & 0x1F;
    }
};

/* =========================
 * Snoop Major Category
 * ========================= */
enum class SnpMajor : uint8_t
{
    Credit,         // SnpLCrdReturn
    Snoop,          // normal snoop (shared/clean/unique/once…)
    SnoopFwd,       // forwarded snoop
    Stash,          // stash-related
    DVM,            // DVM operation
    Query,          // SnpQuery
    ReservedOrUnsupported
};

/* =========================
 * Snoop Minor Semantic
 * ========================= */
enum class SnpMinor : uint8_t
{
    // Credit
    SnpLCrdReturn,

    // Basic snoop
    SnpShared,
    SnpClean,
    SnpOnce,
    SnpNotSharedDirty,
    SnpUnique,
    SnpCleanShared,
    SnpCleanInvalid,
    SnpMakeInvalid,
    SnpPreferUnique,

    // Stash
    SnpUniqueStash,
    SnpMakeInvalidStash,
    SnpStashUnique,
    SnpStashShared,

    // DVM
    SnpDVMOp,

    // Query
    SnpQuery,

    // Forwarded snoop
    SnpSharedFwd,
    SnpCleanFwd,
    SnpOnceFwd,
    SnpNotSharedDirtyFwd,
    SnpPreferUniqueFwd,
    SnpUniqueFwd,

    ReservedOrUnsupported
};

/* =========================
 * Decoded Snoop Opcode
 * ========================= */
struct DecodedSnp
{
    SnpOpcode op;     // raw opcode (wire-accurate)
    SnpMajor  major;  // big category
    SnpMinor  minor;  // fine semantic
};

/* =========================
 * Decode function
 * ========================= */
inline DecodedSnp decodeSnp(uint8_t raw)
{
    SnpOpcode op{raw};
    const uint8_t c = op.code();

    DecodedSnp out{
        op,
        SnpMajor::ReservedOrUnsupported,
        SnpMinor::ReservedOrUnsupported
    };

    switch (c)
    {
        case 0x00:
            out.major = SnpMajor::Credit;
            out.minor = SnpMinor::SnpLCrdReturn;
            break;

        case 0x01:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpShared;
            break;

        case 0x02:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpClean;
            break;

        case 0x03:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpOnce;
            break;

        case 0x04:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpNotSharedDirty;
            break;

        case 0x05:
            out.major = SnpMajor::Stash;
            out.minor = SnpMinor::SnpUniqueStash;
            break;

        case 0x06:
            out.major = SnpMajor::Stash;
            out.minor = SnpMinor::SnpMakeInvalidStash;
            break;

        case 0x07:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpUnique;
            break;

        case 0x08:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpCleanShared;
            break;

        case 0x09:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpCleanInvalid;
            break;

        case 0x0A:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpMakeInvalid;
            break;

        case 0x0B:
            out.major = SnpMajor::Stash;
            out.minor = SnpMinor::SnpStashUnique;
            break;

        case 0x0C:
            out.major = SnpMajor::Stash;
            out.minor = SnpMinor::SnpStashShared;
            break;

        case 0x0D:
            out.major = SnpMajor::DVM;
            out.minor = SnpMinor::SnpDVMOp;
            break;

        // 0x0E - 0x0F reserved

        case 0x10:
            out.major = SnpMajor::Query;
            out.minor = SnpMinor::SnpQuery;
            break;

        case 0x11:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpSharedFwd;
            break;

        case 0x12:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpCleanFwd;
            break;

        case 0x13:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpOnceFwd;
            break;

        case 0x14:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpNotSharedDirtyFwd;
            break;

        case 0x15:
            out.major = SnpMajor::Snoop;
            out.minor = SnpMinor::SnpPreferUnique;
            break;

        case 0x16:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpPreferUniqueFwd;
            break;

        case 0x17:
            out.major = SnpMajor::SnoopFwd;
            out.minor = SnpMinor::SnpUniqueFwd;
            break;

        // 0x18 - 0x1F reserved

        default:
            break;
    }

    return out;
}

} // namespace gem5
#endif

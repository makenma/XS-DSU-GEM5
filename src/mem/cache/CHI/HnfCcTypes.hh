#ifndef __HNF_CC_TYPES_HH__
#define __HNF_CC_TYPES_HH__

#include <cstdint>
#include <optional>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi
{

enum class HnfCcEntryState : uint8_t
{
    Idle,
    Sleep,
    Working,
    WaitSlc,
    IssueMcRead,
    WaitCompAck,
    Retire
};

enum class HnfSlcState : uint8_t
{
    I,
    EU,
    EN,
    SU,
    SN,
    MU,
    MN
};

enum class HnfSfState : uint8_t
{
    I,
    EU,
    EN,
    SU,
    SN
};

struct HnfLinkToCcReq
{
    bool valid = false;
    uint64_t seq = 0;
    uint64_t dueCycle = 0;
    RawReq req{};
    int tokenId = -1;
    uint8_t priority = 0;
    uint8_t resourceClass = 0;
    bool isDynamic = false;
    bool isStatic = false;
    bool isFvb = false;
};

struct HnfCcAdmitResult
{
    bool valid = false;
    uint64_t seq = 0;
    int tokenId = -1;
    bool accepted = false;
    RawReq req{};
};

struct HnfCcRetireInfo
{
    bool valid = false;
    int tokenId = -1;
    uint8_t resourceClass = 0;
    uint8_t reqPriority = 0;
    uint32_t srcid = 0;
    uint8_t pcrdtype = 0;
};

struct HnfCcTxReq
{
    uint32_t entry = 0;
    RawReq req{};
};

struct HnfCcTxDat
{
    uint32_t entry = 0;
    RawDat dat{};
};

struct HnfSlcLookupReq
{
    uint32_t entry = 0;
    RawReq req{};
    uint64_t blockAddr = 0;
};

struct HnfSlcLookupResult
{
    bool valid = false;
    uint32_t entry = 0;
    bool slcHit = false;
    bool sfHit = false;
    bool replay = false;
    bool mcreqNonspec = false;
    bool snoopBroadcast = false;
    bool snoopDirected = false;
    uint32_t rnfid = 0;
    uint32_t rnfvec = 0;
    HnfSlcState slcState = HnfSlcState::I;
    HnfSfState sfState = HnfSfState::I;
    std::vector<uint8_t> data;
};

} // namespace gem5::Chi

#endif // __HNF_CC_TYPES_HH__

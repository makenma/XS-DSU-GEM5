#pragma once

// Issue H field semantics for the deliberately bounded, non-forwarding profile
// used by ChiNativeL2. This is C++17 and does not depend on gem5 scheduling.
//
// nativeChi, flow, stage, last, beatOffset and chunkValid are simulator metadata,
// never CHI fields. In particular, receivers must not use last/beatOffset to
// reconstruct a native DAT transfer. DataID and the request Size determine it.
// Unrepresented optional/error/forward/tag fields are fixed to zero in this
// profile; the helpers are not a claim to implement every Issue H extension.

#include <algorithm>
#include <array>
#include <cstdint>
#include <initializer_list>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>
#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi::NativeWire {

constexpr unsigned LineBytes = 64;
constexpr unsigned BeatBytes = 32;
constexpr uint32_t MaxTxnID = 0xfff;
constexpr unsigned DefaultNodeBits = 11;
constexpr unsigned DefaultAddressBits = 48;

inline bool oneOf(unsigned value, std::initializer_list<unsigned> choices)
{
    return std::find(choices.begin(), choices.end(), value) != choices.end();
}

inline uint8_t sizeCode(unsigned bytes)
{
    if (!bytes || bytes > LineBytes || (bytes & (bytes - 1)))
        throw std::invalid_argument("CHI transfer size must be a power of two in 1..64 bytes");
    uint8_t code = 0;
    while (bytes > 1) { bytes >>= 1; ++code; }
    return code;
}

inline unsigned sizeBytes(unsigned code)
{
    if (code > 6)
        throw std::invalid_argument("CHI Size=7 is reserved; expected Size in 0..6");
    return 1u << code;
}

inline uint64_t snoopAddress(uint64_t physicalAddress)
{
    if (physicalAddress & 7)
        throw std::invalid_argument("CHI SNP address cannot represent PA[2:0]");
    return physicalAddress >> 3;
}

inline uint64_t physicalSnoopAddress(uint64_t wireAddress)
{
    if (wireAddress > (UINT64_MAX >> 3))
        throw std::invalid_argument("CHI SNP address overflows physical address");
    return wireAddress << 3;
}

inline uint8_t dataId(unsigned lineOffset)
{
    if (lineOffset >= LineBytes)
        throw std::invalid_argument("CHI DAT byte offset is outside the cache line");
    return (lineOffset & 32) ? 2 : 0;
}

inline unsigned dataOffset(unsigned id)
{
    if (id != 0 && id != 2)
        throw std::invalid_argument("256-bit CHI DAT permits DataID 0 or 2 only");
    return id * 16;
}

inline bool readData(unsigned opcode) { return opcode == 4; }
inline bool writeData(unsigned opcode) { return opcode == 2 || opcode == 3; }

inline std::string validateBase(const BaseFlit& f,
                                unsigned nodeBits = DefaultNodeBits)
{
    if (!f.nativeChi) return {};
    if (nodeBits < 7 || nodeBits > 16) return "NodeID_Width must be in 7..16";
    const uint32_t nodeLimit = uint32_t(1) << nodeBits;
    if (f.srcid >= nodeLimit || f.tgtid >= nodeLimit) return "NodeID exceeds configured field width";
    if (f.txnid > MaxTxnID) return "TxnID exceeds 12 bits";
    if (f.qos > 15) return "QoS exceeds 4 bits";
    return {};
}

inline std::string validate(const RawReq& f,
                            unsigned nodeBits = DefaultNodeBits,
                            unsigned addressBits = DefaultAddressBits)
{
    if (!f.nativeChi) return {};
    if (auto error = validateBase(f, nodeBits); !error.empty()) return error;
    if (addressBits < 44 || addressBits > 52) return "Req_Addr_Width must be in 44..52";
    if (f.addr >= (uint64_t(1) << addressBits)) return "REQ Addr exceeds configured field width";
    if (f.size > 6) return "REQ Size must encode log2(bytes), 0..6; 7 is reserved";
    // Explicitly exclude the historical 0x55/0x57/0x59/0x5b/0x5c aliases.
    if (!oneOf(f.opcode, {1,3,4,7,8,9,10,11,12,13,0x15,0x17,0x18,
                         0x19,0x1b,0x1c,0x1d,0x22,0x23,0x24,0x25,0x26,0x42}))
        return "REQ opcode is outside the native non-forwarding profile";
    const uint32_t nodeLimit = uint32_t(1) << nodeBits;
    if (f.ReturnNid >= nodeLimit || f.stashNid >= nodeLimit)
        return "REQ return/stash NodeID exceeds configured field width";
    if (f.ReturnTxnId > MaxTxnID) return "ReturnTxnID exceeds 12 bits";
    if (f.AllowRetry > 1 || f.snpattr > 1) return "REQ single-bit field exceeds 1 bit";
    if (f.memattr > 15 || f.pcrdtype > 15 || f.order > 3)
        return "REQ MemAttr/PCrdType/Order exceeds field width";
    if (f.AllowRetry && f.pcrdtype) return "AllowRetry=1 requires PCrdType=0";
    return {};
}

inline std::string validate(const RawRsp& f,
                            unsigned nodeBits = DefaultNodeBits)
{
    if (!f.nativeChi) return {};
    if (auto error = validateBase(f, nodeBits); !error.empty()) return error;
    if (!oneOf(f.opcode, {1,2,3,4,5,6,7,8}))
        return "RSP opcode is outside the native non-forwarding profile";
    if (f.dbid > MaxTxnID) return "RSP DBID exceeds 12 bits";
    if (f.resp > 7 || f.respErr > 3 || f.pcrdtype > 15)
        return "RSP Resp/RespErr/PCrdType exceeds field width";
    return {};
}

inline std::string validate(const RawSnp& f,
                            unsigned nodeBits = DefaultNodeBits,
                            unsigned addressBits = DefaultAddressBits)
{
    if (!f.nativeChi) return {};
    if (auto error = validateBase(f, nodeBits); !error.empty()) return error;
    if (addressBits < 44 || addressBits > 52) return "Req_Addr_Width must be in 44..52";
    if (f.addr >= (uint64_t(1) << (addressBits - 3)))
        return "SNP Addr exceeds PA[Req_Addr_Width-1:3] field width";
    if (!oneOf(f.opcode, {1,2,3,4,7,8,9,10,11,12}))
        return "SNP opcode is outside the native non-forwarding profile";
    if (oneOf(f.opcode,{8,9,10,11,12}) && f.retToSrc)
        return "Maintenance/Stash SNP RetToSrc must be zero";
    if (oneOf(f.opcode,{7,8,9,10,11,12}) && !f.doNotGoToSD)
        return "Unique/Maintenance/Stash SNP DoNotGoToSD must be one";
    // RawSnp::size is legacy metadata. CHI SNP has no Size field.
    return {};
}

inline std::string validate(const RawDat& f,
                            unsigned nodeBits = DefaultNodeBits)
{
    if (!f.nativeChi) return {};
    if (auto error = validateBase(f, nodeBits); !error.empty()) return error;
    if (!oneOf(f.opcode, {1,2,3,4}))
        return "DAT opcode is outside the native non-forwarding profile";
    if (f.dbid > MaxTxnID) return "DAT DBID exceeds 12 significant bits";
    if (f.HomeNID >= (uint32_t(1) << nodeBits)) return "HomeNID exceeds configured field width";
    if (f.resp > 7) return "DAT Resp exceeds 3 bits";
    if (f.opcode == 1 && f.resp == 7) return "Snoop DAT Resp=7 is reserved";
    if ((f.opcode == 2 || f.opcode == 4) && !oneOf(f.resp,{0,1,2,6,7}))
        return "Read/CopyBack DAT Resp is reserved";
    if (f.opcode == 3 && f.resp != 0) return "NonCopyBackWriteData Resp must be zero";
    if (f.dataid != 0 && f.dataid != 2) return "256-bit DAT DataID must be 0 or 2";
    if (f.data.size() != BeatBytes || f.byteEnable.size() != BeatBytes)
        return "256-bit DAT requires 32 Data bytes and 32 BE bits, including partial transfers";
    if (f.chunkValid.size() != BeatBytes / 8)
        return "DAT evidence requires four 8-byte validity chunks";
    if (std::any_of(f.byteEnable.begin(), f.byteEnable.end(), [](auto b){return b > 1;}) ||
        std::any_of(f.chunkValid.begin(), f.chunkValid.end(), [](auto b){return b > 1;}))
        return "DAT BE/known-chunk metadata must contain only 0 or 1";
    if (!readData(f.opcode)) {
        if (f.HomeNID) return "HomeNID must be zero outside read response data";
        for (unsigned b = 0; b < BeatBytes; ++b)
            if (!f.byteEnable[b] && f.chunkValid[b/8] && f.data[b])
                return "Known write/snoop data byte with BE=0 must be zero";
    }
    if (f.opcode == 1) {
        if (f.dbid) return "SnpRespData without DataPull must have DBID=0";
        if (std::any_of(f.byteEnable.begin(), f.byteEnable.end(), [](auto b){return !b;}))
            return "SnpRespData requires all BE bits set";
    }
    if (f.opcode == 2 && f.resp == 0 &&
        std::any_of(f.byteEnable.begin(), f.byteEnable.end(), [](auto b){return b != 0;}))
        return "CopyBackWriteData_I requires all BE bits clear";
    return {};
}

inline std::string validate(ChannelType channel, const FlitVariant& flit,
                            unsigned nodeBits = DefaultNodeBits,
                            unsigned addressBits = DefaultAddressBits)
{
    if (!std::visit([](const auto& f){return f.nativeChi;}, flit)) return {};
    switch (channel) {
      case REQ:
        if (auto f = std::get_if<RawReq>(&flit)) return validate(*f,nodeBits,addressBits);
        break;
      case RSP:
        if (auto f = std::get_if<RawRsp>(&flit)) return validate(*f,nodeBits);
        break;
      case SNP:
        if (auto f = std::get_if<RawSnp>(&flit)) return validate(*f,nodeBits,addressBits);
        break;
      case DAT:
        if (auto f = std::get_if<RawDat>(&flit)) return validate(*f,nodeBits);
        break;
      default: break;
    }
    return "Native flit type does not match CHI channel";
}

// Normal-memory transfer window (B2.9.2): address is rounded down to Size.
inline unsigned transferStart(uint64_t address, unsigned transferBytes)
{
    sizeCode(transferBytes);
    return static_cast<unsigned>(address & (LineBytes - 1)) & ~(transferBytes - 1);
}

// Construct native physical flits at the producer, not at the router or recorder.
// The input vectors are indexed by the full 64-byte line, including for partial
// writes. Bytes outside the transfer window are transported as zero padding.
// For read data, BE is inapplicable and is deliberately emitted as zero.
inline std::vector<RawDat> packData(RawDat prototype,
                                   const std::vector<uint8_t>& line,
                                   const std::vector<uint8_t>& byteEnables,
                                   uint64_t address, unsigned transferBytes)
{
    if (line.size() != LineBytes || (!byteEnables.empty() && byteEnables.size() != LineBytes))
        throw std::invalid_argument("Native DAT construction requires line-indexed 64-byte inputs");
    const unsigned start = transferStart(address, transferBytes);
    const unsigned end = start + transferBytes;
    std::vector<RawDat> result;
    for (unsigned offset = start & ~(BeatBytes - 1); offset < end; offset += BeatBytes) {
        RawDat d = prototype;
        d.nativeChi = true;
        d.dataid = dataId(offset);
        d.data.assign(BeatBytes, 0);
        d.byteEnable.assign(BeatBytes, 0);
        d.chunkValid.assign(BeatBytes / 8, 1);
        d.beatOffset = offset; // Optional simulator metadata; not used on receive.
        d.last = offset + BeatBytes >= end;
        d.flow.messageId.clear();
        for (unsigned lane = 0; lane < BeatBytes; ++lane) {
            const unsigned pos = offset + lane;
            if (pos < start || pos >= end) continue;
            const auto enable = byteEnables.empty() ? 1 : byteEnables[pos];
            if (enable > 1) throw std::invalid_argument("Native DAT construction BE must be 0 or 1");
            d.byteEnable[lane] = readData(d.opcode) ? 0 : enable;
            d.data[lane] = readData(d.opcode) || enable ? line[pos] : 0;
        }
        if (const auto error = validate(d); !error.empty())
            throw std::invalid_argument(error);
        result.push_back(std::move(d));
    }
    return result;
}

// Reassembly is order independent and requires each physical beat exactly once.
// Geometry completion and evidence completeness are separate: an unknown chunk
// is retained as unknown; callers must check allKnown() before supplying CPU data.
class DataAssembler {
    unsigned start, count;
    std::array<bool,2> seen{{false,false}};
    std::optional<RawDat> first;
    std::vector<uint8_t> lineData = std::vector<uint8_t>(LineBytes,0);
    std::vector<uint8_t> lineBE = std::vector<uint8_t>(LineBytes,0);
    std::vector<uint8_t> lineKnown = std::vector<uint8_t>(LineBytes,0);
  public:
    explicit DataAssembler(uint64_t address = 0, unsigned transferBytes = LineBytes)
      : start(transferStart(address,transferBytes)), count(transferBytes) {}

    std::string accept(const RawDat& d)
    {
        if (!d.nativeChi) return "Native DAT assembler cannot accept a coarse flit";
        if (const auto error = validate(d); !error.empty()) return error;
        const unsigned offset = dataOffset(d.dataid);
        if (offset >= start + count || offset + BeatBytes <= start)
            return "DAT DataID is outside the request transfer window";
        if (seen[offset/BeatBytes]) return "Duplicate native DAT beat";
        if (first) {
            const auto& f = *first;
            if (std::tie(d.srcid,d.tgtid,d.txnid,d.opcode,d.HomeNID,d.resp) !=
                std::tie(f.srcid,f.tgtid,f.txnid,f.opcode,f.HomeNID,f.resp))
                return "Native DAT identity/response changes between beats";
            // WriteData DBID is inapplicable and can vary. CompData DBID is
            // conservatively stable in this profile (including ExpCompAck=0).
            if (readData(d.opcode) && d.dbid != f.dbid)
                return "Native read DAT DBID changes between beats";
        }
        // For writes, B2.9.3 requires all BE outside Addr/Size window to be zero.
        if (!readData(d.opcode))
            for (unsigned lane = 0; lane < BeatBytes; ++lane) {
                const unsigned pos = offset + lane;
                if ((pos < start || pos >= start + count) && d.byteEnable[lane])
                    return "Write DAT BE is set outside Addr/Size window";
            }
        if (!first) first = d;
        seen[offset/BeatBytes] = true;
        for (unsigned lane = 0; lane < BeatBytes; ++lane) {
            const unsigned pos = offset + lane;
            if (pos < start || pos >= start + count) continue;
            lineData[pos] = d.data[lane];
            lineBE[pos] = readData(d.opcode) ? 1 : d.byteEnable[lane];
            lineKnown[pos] = d.chunkValid[lane/8];
        }
        return {};
    }

    bool complete() const
    {
        for (unsigned offset = start & ~(BeatBytes - 1); offset < start + count; offset += BeatBytes)
            if (!seen[offset/BeatBytes]) return false;
        return true;
    }
    bool allKnown() const
    {
        return complete() && std::all_of(lineKnown.begin()+start,
            lineKnown.begin()+start+count, [](auto b){return b != 0;});
    }
    const std::vector<uint8_t>& data() const { return lineData; }
    const std::vector<uint8_t>& byteEnable() const { return lineBE; }
    const std::vector<uint8_t>& validBytes() const { return lineKnown; }
};

} // namespace gem5::Chi::NativeWire

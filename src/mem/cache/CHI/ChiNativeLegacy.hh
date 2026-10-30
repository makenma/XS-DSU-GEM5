#pragma once

#include <map>
#include <stdexcept>
#include <type_traits>

#include "mem/cache/CHI/ChiNativeWire.hh"

namespace gem5::Chi
{

// Adapt the native L2's owned flits to ancient_coding's transport without
// changing that branch's router, HNF, SNF or shared flit layout. Ancient HNF
// allocates monotonically increasing 32-bit tokens; map only live tokens into
// the native endpoint's 12-bit ID space and retire them after accepted traffic.
class ChiNativeLegacy
{
    using Key = std::pair<uint32_t, uint32_t>;
    std::map<Key, uint16_t> encoded;
    std::map<Key, uint32_t> decoded;

    uint16_t encode(uint32_t home, uint32_t token)
    {
        const Key key{home, token};
        if (auto it = encoded.find(key); it != encoded.end())
            return it->second;
        for (unsigned id = 0; id <= NativeWire::MaxTxnID; ++id) {
            if (decoded.count({home, id}))
                continue;
            encoded[key] = id;
            decoded[{home, id}] = token;
            return id;
        }
        throw std::runtime_error("Ancient HNF live token space exhausted");
    }

    uint32_t decode(uint32_t home, uint32_t id) const
    {
        return decoded.at({home, id});
    }

    template<class Out, class In>
    static Out base(const In &in)
    {
        Out out{};
        out.srcid = in.srcid;
        out.tgtid = in.tgtid;
        out.txnid = in.txnid;
        out.qos = in.qos;
        out.opcode = in.opcode;
        return out;
    }

  public:
    NativeRsp receive(const RawRsp &in)
    {
        auto out = base<NativeRsp>(in);
        out.nativeChi = true;
        out.resp = in.resp;
        if (in.opcode == 5 || in.opcode == 6)
            out.dbid = encode(in.srcid, in.dbid);
        return out;
    }

    NativeDat receive(const RawDat &in)
    {
        auto out = base<NativeDat>(in);
        out.nativeChi = true;
        out.dbid = encode(in.srcid, in.dbid);
        out.HomeNID = in.HomeNID;
        out.dataid = in.dataid;
        out.last = in.last;
        out.resp = in.resp | (in.passDirty ? 4 : 0);
        if (in.payloadSize() != NativeWire::BeatBytes)
            throw std::runtime_error("Ancient HNF returned a non-256-bit DAT");
        out.data.assign(in.payloadData(), in.payloadData() + in.payloadSize());
        out.byteEnable.assign(NativeWire::BeatBytes, 0);
        out.chunkValid.assign(NativeWire::BeatBytes / 8, 1);
        return out;
    }

    NativeSnp receive(const RawSnp &in)
    {
        auto out = base<NativeSnp>(in);
        out.nativeChi = true;
        out.txnid = encode(in.srcid, in.txnid);
        out.addr = NativeWire::snoopAddress(in.addr);
        out.retToSrc = false;
        out.doNotGoToSD = true;
        return out;
    }

    FlitVariant transmit(const NativeFlitVariant &flit) const
    {
        return std::visit([this](const auto &in) -> FlitVariant {
            using F = std::decay_t<decltype(in)>;
            if constexpr (std::is_same_v<F, NativeReq>) {
                auto out = base<RawReq>(in);
                if (in.opcode != 7 && in.opcode != 9 && in.opcode != 0x1b)
                    throw std::runtime_error("Unsupported ancient HNF REQ");
                out.addr = in.addr;
                out.size = in.size;
                out.AllowRetry = in.AllowRetry;
                out.expCompAck = in.expCompAck;
                out.likelyShared = in.likelyShared;
                return out;
            } else if constexpr (std::is_same_v<F, NativeRsp>) {
                auto out = base<RawRsp>(in);
                out.txnid = decode(in.tgtid, in.txnid);
                out.resp = in.resp;
                out.passDirty = in.resp & 4;
                return out;
            } else if constexpr (std::is_same_v<F, NativeDat>) {
                auto out = base<RawDat>(in);
                out.txnid = decode(in.tgtid, in.txnid);
                out.dbid = out.txnid;
                out.dataid = in.dataid;
                out.last = in.last;
                out.resp = in.resp;
                out.passDirty = in.resp & 4;
                out.setPayload(in.data.data(), in.data.size());
                return out;
            } else {
                throw std::runtime_error("An RN-F cannot transmit SNP");
            }
        }, flit);
    }

    void accepted(const NativeFlitVariant &flit)
    {
        std::visit([this](const auto &in) {
            using F = std::decay_t<decltype(in)>;
            if constexpr (std::is_same_v<F, NativeRsp> ||
                          std::is_same_v<F, NativeDat>) {
                if constexpr (std::is_same_v<F, NativeDat>) {
                    if (!in.last)
                        return;
                }
                const Key key{in.tgtid, in.txnid};
                const auto token = decoded.at(key);
                decoded.erase(key);
                encoded.erase({in.tgtid, token});
            }
        }, flit);
    }

    size_t pendingTokens() const { return encoded.size(); }
};

} // namespace gem5::Chi

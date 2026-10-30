#ifndef __CHICHANNEL__HH__
#define __CHICHANNEL__HH__

#include <cstddef>
#include <cstdint>
#include <variant>
#include <array>
#include <algorithm>
#include <stdexcept>

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
    enum PoolPriority { HighHigh, High, Medium, Low, PoolPriorityNum };

    constexpr PoolPriority
    WhichPriority(uint8_t qos) noexcept
    {
        if (qos == 15)
            return HighHigh;
        if (qos >= 12)
            return High;
        if (qos >= 8)
            return Medium;
        return Low;
    }


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

        bool isCurrentStage(uint32_t stage) const {
            return this->stage == stage;
        }

        PoolPriority qosPriority() const noexcept {
            return WhichPriority(qos);
        }

        uint32_t srcId() const noexcept {
            return srcid;
        }

        uint32_t txnId() const noexcept {
            return txnid;
        }

    };



    struct RawReq:BaseFlit
    {
        uint8_t     AllowRetry = 1;
        uint64_t    addr = 0;
        uint8_t     size = 6;
        uint32_t    ReturnNid = 0;
        bool ns = true;
        bool allocate = true;
        bool cacheable = true;
        bool likelyShared = false;
        bool snoopMe = false;
        uint8_t order = 0;
        bool expCompAck = true;

    };

    struct RawRsp:BaseFlit
    {
        uint32_t    dbid = 0;
        uint8_t     resp = 0;
        bool passDirty = false;
    };


    struct RawSnp:BaseFlit
    {
        uint64_t    addr = 0;
        uint8_t     size = 6;
        bool ns = true;
    };

    struct data_payload
    {
        uint8_t* data = nullptr;
        size_t   ByteLength = 0;// in bytes
    };

    struct RawDat:BaseFlit
    {
        uint8_t     last = 0;
        uint32_t    HomeNID = 0;
        uint32_t    dbid = 0;
        uint8_t     resp = 0;
        bool passDirty = false;
        uint8_t     dataid = 0;
        data_payload data{};
        // A flit owns at most one cache line. Copies rebase the legacy
        // data pointer, so both APIs remain valid after pipeline retirement.
        std::array<uint8_t, 64> payload{};
        std::size_t payloadLength = 0;

        RawDat() = default;
        RawDat(const RawDat &other) { *this = other; }
        RawDat(RawDat &&other) noexcept { *this = other; }
        RawDat &operator=(const RawDat &other) {
            if (this == &other) return *this;
            static_cast<BaseFlit &>(*this) = other;
            last = other.last;
            HomeNID = other.HomeNID;
            dbid = other.dbid;
            resp = other.resp;
            passDirty = other.passDirty;
            dataid = other.dataid;
            payload = other.payload;
            payloadLength = other.payloadLength;
            data = payloadLength ? data_payload{payload.data(), payloadLength}
                                 : other.data;
            return *this;
        }
        RawDat &operator=(RawDat &&other) noexcept { return *this = other; }
        void setPayload(const uint8_t *bytes, std::size_t length) {
            if (length > payload.size() || (length && !bytes))
                throw std::invalid_argument("Invalid CHI data payload");
            if (length)
                std::copy_n(bytes, length, payload.begin());
            payloadLength = length;
            data = {payload.data(), payloadLength};
        }
        void ownPayload() {
            if (!payloadLength && data.data && data.ByteLength)
                setPayload(data.data, data.ByteLength);
        }
        const uint8_t *payloadData() const {
            return payloadLength ? payload.data() : data.data;
        }
        std::size_t payloadSize() const {
            return payloadLength ? payloadLength : data.ByteLength;
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


    using FlitVariant = std::variant<RawReq, RawRsp, RawSnp, RawDat>;



}

}




#endif

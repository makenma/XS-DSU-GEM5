#ifndef __CHICHANNEL__HH__
#define __CHICHANNEL__HH__

#include <cstddef>
#include <cstdint>

namespace gem5
{

    enum ChannelType: uint8_t
    {
        REQ = 0,
        RSP = 1,
        SNP = 2,
        DAT = 3,
        NUM_CHANNELS
    };



    struct CommonHdr
    {
        uint8_t      qos;
        uint32_t     srcid;
        uint32_t     tgtid;
        uint32_t     txnid;
        uint8_t      opcode;
    };

    struct RawReq
    {
        CommonHdr   hdr;
        uint8_t     AllowRetry;
        uint64_t    addr;
        uint8_t     size;
        uint32_t    ReturnNid;

    };

    struct RawRsp
    {
        CommonHdr   hdr;
        uint8_t     dbid;
    };


    struct RawSnp
    {
        CommonHdr   hdr;
        uint64_t    addr;
        uint8_t     size;
    };

    struct data_payload
    {
        uint8_t* data;
        size_t   ByteLength;// in bytes
    };

    struct RawDat
    {
        CommonHdr   hdr;
        uint8_t     last;
        uint32_t    HomeNID;
        uint8_t     dbid;
        uint8_t     dataid;
        data_payload data;
        // uint8_t[]   data; // variable length data payload
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







}




#endif

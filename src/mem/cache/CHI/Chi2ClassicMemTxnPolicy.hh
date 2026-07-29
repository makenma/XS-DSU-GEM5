#ifndef __MEM_CACHE_CHI_CHI2CLASSICMEMTXNPOLICY_HH__
#define __MEM_CACHE_CHI_CHI2CLASSICMEMTXNPOLICY_HH__

#include <algorithm>
#include <cstdint>
#include <optional>
#include <vector>

#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"

namespace gem5
{
namespace Chi
{

/**
 * Pure CHI protocol policy shared by Chi2ClassicMemBridge and its unit tests.
 * Packet allocation, queues, ports, and event scheduling remain in the bridge.
 */
class Chi2ClassicMemTxnPolicy
{
  public:
    static constexpr uint8_t ReadNoSnpOpcode = 0x04;
    static constexpr uint8_t WriteNoSnpFullOpcode = 0x5c;
    static constexpr uint8_t NonCopyBackWriteDataOpcode = 0x03;
    static constexpr uint8_t CompDataOpcode = 0x04;
    static constexpr uint8_t CompOpcode = 0x04;
    static constexpr uint8_t DBIDRespOpcode = 0x06;
    static constexpr uint8_t RespSC = 0x01;

    /**
     * Stable hash used only by the dirty-victim trace contract.  FNV-1a is
     * deliberately small and deterministic so the HNF producer and SN
     * consumer can report the same full-line identity without dumping data.
     */
    static uint64_t
    traceDataHash(const std::vector<uint8_t>& data)
    {
        uint64_t hash = 14695981039346656037ULL;
        for (const uint8_t byte : data) {
            hash ^= byte;
            hash *= 1099511628211ULL;
        }
        return hash;
    }

    enum class Phase : uint8_t
    {
        MemReqQueued,
        MemRespPending,
        WriteDataPending,
        ChiResponsePending,
        Complete
    };

    enum class Event : uint8_t
    {
        WriteDataAccepted,
        ClassicRequestSent,
        ClassicResponseReceived,
        ChiResponseSent
    };

    static uint32_t
    expectedDataBytes(const RawReq& req, uint32_t block_size)
    {
        return req.size ? req.size : block_size;
    }

    static bool
    isWriteNoSnpFull(const RawReq& req, uint32_t block_size)
    {
        return block_size != 0 &&
            req.opcode == WriteNoSnpFullOpcode &&
            decodeReq(req.opcode).minor == ReqMinor::WriteNoSnp &&
            expectedDataBytes(req, block_size) == block_size &&
            (req.addr % block_size) == 0;
    }

    static bool
    isHnfDirtyVictimWriteback(const RawReq& req, uint32_t block_size)
    {
        return req.hnfDirtyVictim && isWriteNoSnpFull(req, block_size);
    }

    static bool
    isExactWriteData(const RawDat& dat, const RawReq& req,
                     uint8_t assigned_dbid, uint32_t expected_bytes,
                     uint32_t node_id)
    {
        const uint32_t expected_tgtid = node_id ? node_id : req.tgtid;
        return dat.opcode == NonCopyBackWriteDataOpcode &&
            decodeDat(dat.opcode).minor ==
                DatMinor::NonCopyBackWriteData &&
            dat.txnid == req.txnid && assigned_dbid != 0 &&
            dat.dbid == assigned_dbid && dat.srcid == req.srcid &&
            dat.tgtid == expected_tgtid && dat.HomeNID == req.srcid &&
            dat.qos == req.qos && dat.last && dat.dataid == 0 &&
            dat.beatOffset == 0 && dat.data.size() == expected_bytes &&
            dat.byteEnable.size() == expected_bytes &&
            allNonZero(dat.byteEnable) &&
            dat.chunkValid.size() == (expected_bytes + 7) / 8 &&
            allNonZero(dat.chunkValid);
    }

    static RawRsp
    makeDbidResp(const RawReq& req, uint8_t dbid, uint32_t node_id,
                 uint32_t hnf_node_id)
    {
        return makeResponse(
            req, dbid, node_id, hnf_node_id, DBIDRespOpcode, false);
    }

    static RawRsp
    makeComp(const RawReq& req, uint8_t dbid, uint32_t node_id,
             uint32_t hnf_node_id, bool error)
    {
        return makeResponse(
            req, dbid, node_id, hnf_node_id, CompOpcode, error);
    }

    /**
     * Return the state resulting from an attempted event. Backpressure is an
     * accepted=false event and retains the current state. Invalid source/event
     * pairs return std::nullopt.
     */
    static std::optional<Phase>
    transition(Phase phase, Event event, bool accepted = true)
    {
        const auto target = transitionTarget(phase, event);
        if (!target) {
            return std::nullopt;
        }
        return accepted ? target : std::optional<Phase>(phase);
    }

  private:
    static bool
    allNonZero(const std::vector<uint8_t>& values)
    {
        return std::all_of(values.begin(), values.end(),
                           [](uint8_t value) { return value != 0; });
    }

    static RawRsp
    makeResponse(const RawReq& req, uint8_t dbid, uint32_t node_id,
                 uint32_t hnf_node_id, uint8_t opcode, bool error)
    {
        RawRsp rsp{};
        rsp.qos = req.qos;
        rsp.srcid = node_id ? node_id : req.tgtid;
        rsp.tgtid = hnf_node_id ? hnf_node_id : req.srcid;
        rsp.txnid = req.txnid;
        rsp.opcode = opcode;
        rsp.dbid = dbid;
        rsp.resp = RespSC;
        rsp.respErr = error ? 1 : 0;
        rsp.pcrdtype = req.pcrdtype;
        return rsp;
    }

    static std::optional<Phase>
    transitionTarget(Phase phase, Event event)
    {
        if (phase == Phase::WriteDataPending &&
            event == Event::WriteDataAccepted) {
            return Phase::MemReqQueued;
        }
        if (phase == Phase::MemReqQueued &&
            event == Event::ClassicRequestSent) {
            return Phase::MemRespPending;
        }
        if (phase == Phase::MemRespPending &&
            event == Event::ClassicResponseReceived) {
            return Phase::ChiResponsePending;
        }
        if (phase == Phase::ChiResponsePending &&
            event == Event::ChiResponseSent) {
            return Phase::Complete;
        }
        return std::nullopt;
    }
};

} // namespace Chi
} // namespace gem5

#endif // __MEM_CACHE_CHI_CHI2CLASSICMEMTXNPOLICY_HH__

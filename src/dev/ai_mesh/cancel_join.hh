#ifndef DEV_AI_MESH_CANCEL_JOIN_HH
#define DEV_AI_MESH_CANCEL_JOIN_HH

#include <cstdint>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5
{
namespace ai_mesh
{

struct CancelJoinRecord
{
    uint64_t targetRequestId = 0;
    uint64_t commandRequestId = 0;
    uint64_t targetCookie = 0;
    uint64_t commandCookie = 0;
    uint16_t targetStatus = 0;
    uint16_t commandStatus = 0;
    bool targetCqSeen = false;
    bool commandCqSeen = false;
    bool doorbellCommitted = false;
};

inline const char *
cancelJoinWinner(uint16_t commandStatus, uint16_t targetStatus)
{
    if (commandStatus == agent_abi::kCqStatusSUCCESS &&
            targetStatus == agent_abi::kCqStatusCANCELLED)
        return "CANCEL_WINS";
    if (commandStatus == agent_abi::kCqStatusALREADY_TERMINAL) {
        if (targetStatus == agent_abi::kCqStatusSUCCESS)
            return "TARGET_SUCCESS_WINS";
        if (targetStatus != agent_abi::kCqStatusSUCCESS &&
                targetStatus != agent_abi::kCqStatusCANCELLED)
            return "TARGET_ERROR_WINS";
    }
    return nullptr;
}

}

}

#endif

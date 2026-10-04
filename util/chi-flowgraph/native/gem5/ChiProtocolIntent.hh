#pragma once
#include <functional>
#include <string>
#include <vector>
#include "mem/packet.hh"

namespace gem5::Chi {
// Explicit simulator test operation. Ordinary CPU packets never carry this.
struct ChiProtocolIntent : Packet::SenderState {
    uint8_t opcode = 0;
    uint32_t stashTarget = 0;
    std::string instructionId;
    std::function<void(PacketPtr)> complete;
};
inline bool chiRead(uint8_t op) {
    return op==1 || op==3 || op==4 || op==7 || op==0x24 || op==0x25 || op==0x26;
}
inline bool chiWrite(uint8_t op) {
    return op==0x15 || op==0x18 || op==0x19 || op==0x1b || op==0x1c || op==0x1d;
}
inline bool chiCopyback(uint8_t op) { return op==0x15 || op==0x1b; }
inline bool chiAllocating(uint8_t op) { return op==1 || op==7 || op==0x26; }
inline bool chiStash(uint8_t op) { return op==0x22 || op==0x23; }
}

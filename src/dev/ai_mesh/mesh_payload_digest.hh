#ifndef DEV_AI_MESH_MESH_PAYLOAD_DIGEST_HH
#define DEV_AI_MESH_MESH_PAYLOAD_DIGEST_HH

#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>

namespace gem5
{
namespace ai_mesh
{

inline std::string
dualFnvDigest(const uint8_t *data, uint64_t size)
{
    uint64_t h0 = 0xCBF29CE484222325ull;
    uint64_t h1 = 0x9E3779B97F4A7C15ull;
    for (uint64_t i = 0; i < size; i++) {
        h0 = (h0 ^ data[i]) * 0x100000001B3ull;
        h1 = (h1 + ((h0 >> 31) ^ data[i])) * 0xBF58476D1CE4E5B9ull;
    }
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << h0 << "-"
        << std::setw(16) << h1;
    return out.str();
}

}
}
#endif

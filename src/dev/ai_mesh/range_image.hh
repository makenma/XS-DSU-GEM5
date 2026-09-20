#ifndef DEV_AI_MESH_RANGE_IMAGE_HH
#define DEV_AI_MESH_RANGE_IMAGE_HH

#include <cstdint>
#include <string>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

// One admitted destination segment with the raw bytes a real target owner
// holds at the moment the segment is sampled.  The bytes are the evidence; the
// digest of the same bytes is recomputed by the consumer, so an archive cannot
// substitute a digest for missing or changed content.
struct DestinationSegment
{
    uint32_t descriptor_id = 0;
    uint32_t command_id = 0;
    uint32_t generation = 0;
    uint16_t kind = 0;
    uint32_t row = 0;
    uint64_t address = 0;
    uint64_t size = 0;
    std::string bytes_hex;
};

inline std::string
bytesHex(const std::vector<uint8_t> &bytes)
{
    static const char *digits = "0123456789abcdef";
    std::string out;
    out.resize(bytes.size() * 2);
    for (size_t i = 0; i < bytes.size(); i++) {
        out[2 * i] = digits[bytes[i] >> 4];
        out[2 * i + 1] = digits[bytes[i] & 0xF];
    }
    return out;
}

} // namespace ai_mesh
} // namespace gem5

#endif

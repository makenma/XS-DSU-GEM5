#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

#include <cstddef>

namespace gem5
{
namespace ai_mesh
{
namespace agent_abi
{

namespace
{
struct Table
{
    uint32_t v[256];
    Table()
    {
        const uint32_t poly = 0x82F63B78u;
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++)
                c = (c & 1u) ? (c >> 1) ^ poly : c >> 1;
            v[i] = c;
        }
    }
};
const Table kTable;
} // namespace

uint32_t
crc32c(const uint8_t *data, size_t size)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < size; i++)
        crc = kTable.v[(crc ^ data[i]) & 0xFFu] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}

} // namespace agent_abi
} // namespace ai_mesh
} // namespace gem5

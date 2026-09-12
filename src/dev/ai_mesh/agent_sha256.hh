#ifndef DEV_AI_MESH_AGENT_SHA256_HH
#define DEV_AI_MESH_AGENT_SHA256_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

class AgentSha256
{
  public:
    AgentSha256();

    void update(const uint8_t *data, size_t length);
    std::array<uint8_t, 32> finish();

  private:
    void compress(const uint8_t block[64]);

    uint32_t state[8];
    uint64_t bitCount = 0;
    uint8_t buffer[64];
    size_t bufferLength = 0;
};

std::array<uint8_t, 32>
agentSha256(const std::vector<uint8_t> &data);

}
}
#endif

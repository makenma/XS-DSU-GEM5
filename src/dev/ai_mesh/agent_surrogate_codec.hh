#ifndef DEV_AI_MESH_AGENT_SURROGATE_CODEC_HH
#define DEV_AI_MESH_AGENT_SURROGATE_CODEC_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace gem5
{
namespace ai_mesh
{

struct SurrogateProfile
{
    uint64_t profileKey = 0;
    uint64_t inputBytes = 0;
    uint64_t outputBytes = 0;
    uint64_t serviceNs = 0;
    uint32_t inputTokens = 0;
    uint32_t outputTokens = 0;
    uint32_t publishChunkBytes = 0;
    uint16_t programId = 0;
    uint16_t profileId = 0;
};

class SurrogateProfileRegistry
{
  public:
    static std::optional<SurrogateProfileRegistry>
    parse(const uint8_t *data, size_t length);

    const SurrogateProfile *find(uint16_t programId,
                                 uint16_t profileId) const;
    uint32_t publishChunkBytes() const;
    size_t profileCount() const { return profiles_.size(); }
    const std::array<uint8_t, 32> &fixtureDigest() const
    { return fixtureDigest_; }

  private:
    std::vector<SurrogateProfile> profiles_;
    std::array<uint8_t, 32> fixtureDigest_{};
};

std::array<uint8_t, 32>
surrogateSeed(const uint8_t inputDigest[32], uint16_t programId,
              uint16_t profileId, uint64_t profileKey);

std::array<uint8_t, 32>
surrogateTokenDigest(const uint8_t seed[32], uint32_t tokenOrdinal);

std::array<uint8_t, 32>
surrogateOutputPrefixDigest(const uint8_t workloadDigest[32],
                            uint32_t itemId, uint32_t outputTokens,
                            const std::vector<std::array<uint8_t, 32>>
                                &tokenDigests);

std::vector<uint8_t>
surrogateOutputBytes(const uint8_t semanticDigest[32], uint64_t totalBytes);

std::vector<uint8_t>
agentInputSurrogateBytes(const uint8_t inputDigest[32], uint64_t totalBytes);

}
}
#endif

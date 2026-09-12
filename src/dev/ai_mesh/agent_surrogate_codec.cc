#include "dev/ai_mesh/agent_surrogate_codec.hh"

#include <cstring>
#include <utility>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

void
hashDomain(AgentSha256 &hash, const char *domain)
{
    const size_t length = std::strlen(domain) + 1;
    hash.update(reinterpret_cast<const uint8_t *>(domain), length);
}

void
hashU16(AgentSha256 &hash, uint16_t value)
{
    const uint8_t bytes[2] = {uint8_t(value), uint8_t(value >> 8)};
    hash.update(bytes, sizeof(bytes));
}

void
hashU32(AgentSha256 &hash, uint32_t value)
{
    uint8_t bytes[4];
    for (size_t index = 0; index < 4; ++index)
        bytes[index] = uint8_t(value >> (8 * index));
    hash.update(bytes, sizeof(bytes));
}

void
hashU64(AgentSha256 &hash, uint64_t value)
{
    uint8_t bytes[8];
    for (size_t index = 0; index < 8; ++index)
        bytes[index] = uint8_t(value >> (8 * index));
    hash.update(bytes, sizeof(bytes));
}

class SectionReader
{
  public:
    SectionReader(const uint8_t *data, size_t length)
        : data(data), length(length) {}

    bool failed() const { return failed_; }

    uint32_t u32()
    {
        if (!take(4))
            return 0;
        uint32_t value = 0;
        for (size_t index = 0; index < 4; ++index)
            value |= uint32_t(data[offset_ + index]) << (8 * index);
        offset_ += 4;
        return value;
    }

    uint64_t u64()
    {
        if (!take(8))
            return 0;
        uint64_t value = 0;
        for (size_t index = 0; index < 8; ++index)
            value |= uint64_t(data[offset_ + index]) << (8 * index);
        offset_ += 8;
        return value;
    }

    uint16_t u16()
    {
        if (!take(2))
            return 0;
        const uint16_t value = uint16_t(data[offset_]) |
                               uint16_t(data[offset_ + 1]) << 8;
        offset_ += 2;
        return value;
    }

    bool bytes(uint8_t *target, size_t count)
    {
        if (!take(count))
            return false;
        std::memcpy(target, data + offset_, count);
        offset_ += count;
        return true;
    }

    bool consumedAll() const { return offset_ == length; }

  private:
    bool take(size_t count)
    {
        if (failed_ || count > length - offset_) {
            failed_ = true;
            return false;
        }
        return true;
    }

    const uint8_t *data;
    size_t length;
    size_t offset_ = 0;
    bool failed_ = false;
};

}

std::optional<SurrogateProfileRegistry>
SurrogateProfileRegistry::parse(const uint8_t *data, size_t length)
{
    if (!data || length < 32 + 4)
        return std::nullopt;
    SurrogateProfileRegistry registry;
    SectionReader reader(data, length);
    if (!reader.bytes(registry.fixtureDigest_.data(), 32))
        return std::nullopt;
    const uint32_t count = reader.u32();
    if (reader.failed() || count == 0 || count > 65536)
        return std::nullopt;
    for (uint32_t index = 0; index < count; ++index) {
        SurrogateProfile profile;
        profile.programId = reader.u16();
        profile.profileId = reader.u16();
        profile.profileKey = reader.u64();
        profile.inputTokens = reader.u32();
        profile.inputBytes = reader.u64();
        profile.outputTokens = reader.u32();
        profile.outputBytes = reader.u64();
        profile.serviceNs = reader.u64();
        profile.publishChunkBytes = reader.u32();
        if (reader.failed())
            return std::nullopt;
        registry.profiles_.push_back(profile);
    }
    if (!reader.consumedAll())
        return std::nullopt;
    for (size_t index = 1; index < registry.profiles_.size(); ++index) {
        const SurrogateProfile &previous = registry.profiles_[index - 1];
        const SurrogateProfile &current = registry.profiles_[index];
        if (std::make_pair(previous.programId, previous.profileId) >=
            std::make_pair(current.programId, current.profileId))
            return std::nullopt;
    }
    return registry;
}

const SurrogateProfile *
SurrogateProfileRegistry::find(uint16_t programId, uint16_t profileId) const
{
    for (const SurrogateProfile &profile : profiles_)
        if (profile.programId == programId && profile.profileId == profileId)
            return &profile;
    return nullptr;
}

uint32_t
SurrogateProfileRegistry::publishChunkBytes() const
{
    return profiles_.front().publishChunkBytes;
}

std::array<uint8_t, 32>
surrogateSeed(const uint8_t inputDigest[32], uint16_t programId,
              uint16_t profileId, uint64_t profileKey)
{
    AgentSha256 hash;
    hashDomain(hash, "AI_MESH_AGENT_SURROGATE_V1");
    hash.update(inputDigest, 32);
    hashU16(hash, programId);
    hashU16(hash, profileId);
    hashU64(hash, profileKey);
    return hash.finish();
}

std::array<uint8_t, 32>
surrogateTokenDigest(const uint8_t seed[32], uint32_t tokenOrdinal)
{
    AgentSha256 hash;
    hash.update(seed, 32);
    hashU32(hash, tokenOrdinal);
    return hash.finish();
}

std::array<uint8_t, 32>
surrogateOutputPrefixDigest(const uint8_t workloadDigest[32],
                            uint32_t itemId, uint32_t outputTokens,
                            const std::vector<std::array<uint8_t, 32>>
                                &tokenDigests)
{
    AgentSha256 hash;
    hashDomain(hash, "AGENT_OUTPUT_PREFIX_V1");
    hash.update(workloadDigest, 32);
    hashU32(hash, itemId);
    hashU32(hash, outputTokens);
    for (const auto &digest : tokenDigests)
        hash.update(digest.data(), 32);
    return hash.finish();
}

std::vector<uint8_t>
surrogateOutputBytes(const uint8_t semanticDigest[32], uint64_t totalBytes)
{
    std::vector<uint8_t> output;
    output.reserve(totalBytes);
    uint64_t ordinal = 0;
    while (output.size() < totalBytes) {
        AgentSha256 hash;
        hashDomain(hash, "AI_MESH_OUTPUT_BYTES_V1");
        hash.update(semanticDigest, 32);
        hashU64(hash, ordinal);
        const std::array<uint8_t, 32> block = hash.finish();
        const uint64_t remaining = totalBytes - output.size();
        const size_t take = remaining < 32 ? size_t(remaining) : 32;
        output.insert(output.end(), block.begin(), block.begin() + take);
        ++ordinal;
    }
    return output;
}

std::vector<uint8_t>
agentInputSurrogateBytes(const uint8_t inputDigest[32], uint64_t totalBytes)
{
    std::vector<uint8_t> output;
    output.reserve(totalBytes);
    uint64_t ordinal = 0;
    while (output.size() < totalBytes) {
        AgentSha256 hash;
        hashDomain(hash, "AI_MESH_INPUT_V1");
        hash.update(inputDigest, 32);
        hashU64(hash, ordinal);
        const std::array<uint8_t, 32> block = hash.finish();
        const uint64_t remaining = totalBytes - output.size();
        const size_t take = remaining < 32 ? size_t(remaining) : 32;
        output.insert(output.end(), block.begin(), block.begin() + take);
        ++ordinal;
    }
    return output;
}

}
}

#ifndef GEM5_DEV_AI_MESH_MESH_HASH_HH
#define GEM5_DEV_AI_MESH_MESH_HASH_HH

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <streambuf>

namespace gem5
{
namespace ai_mesh
{
namespace mesh_hash
{

using Sha256Digest = std::array<uint8_t, 32>;

std::string digestHex(const Sha256Digest &digest);

// Lowercase hex of a raw byte range, for archives that must stay replayable
// without trusting a digest.
std::string bytesHex(const uint8_t *data, size_t size);

class Sha256
{
  public:
    Sha256();

    void update(const uint8_t *data, size_t size);
    Sha256Digest digest() const;

  private:
    void transform(const uint8_t *block);
    Sha256Digest finish();

    std::array<uint32_t, 8> state;
    std::array<uint8_t, 64> buffer{};
    size_t buffered = 0;
    uint64_t total = 0;
};

class Sha256StreamBuffer : public std::streambuf
{
  public:
    Sha256Digest digest() const;

  protected:
    std::streamsize xsputn(
        const char *data,
        std::streamsize size) override;
    int_type overflow(int_type value) override;

  private:
    Sha256 hash;
};

}
}
}

#endif

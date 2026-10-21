#include "dev/ai_mesh/mesh_hash.hh"

#include <algorithm>
#include <cstring>

namespace gem5
{
namespace ai_mesh
{
namespace mesh_hash
{

namespace
{

constexpr std::array<uint32_t, 64> RoundConstants = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

uint32_t
rotateRight(uint32_t value, uint32_t amount)
{
    return (value >> amount) | (value << (32 - amount));
}

}

Sha256::Sha256()
    : state{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19}
{
}

void
Sha256::transform(const uint8_t *block)
{
    std::array<uint32_t, 64> words{};
    for (size_t index = 0; index < 16; ++index) {
        words[index] =
            (static_cast<uint32_t>(block[4 * index]) << 24) |
            (static_cast<uint32_t>(block[4 * index + 1]) << 16) |
            (static_cast<uint32_t>(block[4 * index + 2]) << 8) |
            static_cast<uint32_t>(block[4 * index + 3]);
    }
    for (size_t index = 16; index < words.size(); ++index) {
        const uint32_t first =
            rotateRight(words[index - 15], 7) ^
            rotateRight(words[index - 15], 18) ^
            (words[index - 15] >> 3);
        const uint32_t second =
            rotateRight(words[index - 2], 17) ^
            rotateRight(words[index - 2], 19) ^
            (words[index - 2] >> 10);
        words[index] = words[index - 16] + first +
                       words[index - 7] + second;
    }

    uint32_t a = state[0];
    uint32_t b = state[1];
    uint32_t c = state[2];
    uint32_t d = state[3];
    uint32_t e = state[4];
    uint32_t f = state[5];
    uint32_t g = state[6];
    uint32_t h = state[7];
    for (size_t index = 0; index < words.size(); ++index) {
        const uint32_t sumOne = rotateRight(e, 6) ^
                                rotateRight(e, 11) ^
                                rotateRight(e, 25);
        const uint32_t choose = (e & f) ^ (~e & g);
        const uint32_t first = h + sumOne + choose +
                               RoundConstants[index] + words[index];
        const uint32_t sumZero = rotateRight(a, 2) ^
                                 rotateRight(a, 13) ^
                                 rotateRight(a, 22);
        const uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const uint32_t second = sumZero + majority;
        h = g;
        g = f;
        f = e;
        e = d + first;
        d = c;
        c = b;
        b = a;
        a = first + second;
    }
    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
    state[4] += e;
    state[5] += f;
    state[6] += g;
    state[7] += h;
}

void
Sha256::update(const uint8_t *data, size_t size)
{
    total += size;
    while (size != 0) {
        const size_t count = std::min(size, buffer.size() - buffered);
        std::memcpy(buffer.data() + buffered, data, count);
        buffered += count;
        data += count;
        size -= count;
        if (buffered == buffer.size()) {
            transform(buffer.data());
            buffered = 0;
        }
    }
}

Sha256Digest
Sha256::finish()
{
    const uint64_t bitCount = total * 8;
    const uint8_t marker = 0x80;
    update(&marker, 1);
    const uint8_t zero = 0;
    while (buffered != 56)
        update(&zero, 1);
    std::array<uint8_t, 8> length{};
    for (size_t index = 0; index < length.size(); ++index) {
        length[index] = static_cast<uint8_t>(
            bitCount >> (8 * (length.size() - index - 1)));
    }
    update(length.data(), length.size());

    Sha256Digest result{};
    for (size_t index = 0; index < state.size(); ++index) {
        result[4 * index] = static_cast<uint8_t>(state[index] >> 24);
        result[4 * index + 1] =
            static_cast<uint8_t>(state[index] >> 16);
        result[4 * index + 2] =
            static_cast<uint8_t>(state[index] >> 8);
        result[4 * index + 3] = static_cast<uint8_t>(state[index]);
    }
    return result;
}

Sha256Digest
Sha256::digest() const
{
    Sha256 copy = *this;
    return copy.finish();
}

std::streamsize
Sha256StreamBuffer::xsputn(const char *data, std::streamsize size)
{
    if (size <= 0)
        return 0;
    hash.update(
        reinterpret_cast<const uint8_t *>(data),
        static_cast<size_t>(size));
    return size;
}

Sha256StreamBuffer::int_type
Sha256StreamBuffer::overflow(int_type value)
{
    if (traits_type::eq_int_type(value, traits_type::eof()))
        return traits_type::not_eof(value);
    const auto byte = static_cast<uint8_t>(traits_type::to_char_type(value));
    hash.update(&byte, 1);
    return value;
}

Sha256Digest
Sha256StreamBuffer::digest() const
{
    return hash.digest();
}


std::string
digestHex(const Sha256Digest &digest)
{
    static const char *hex = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (const uint8_t byte : digest) {
        out += hex[byte >> 4];
        out += hex[byte & 0xF];
    }
    return out;
}

std::string
bytesHex(const uint8_t *data, size_t size)
{
    static const char *hex = "0123456789abcdef";
    std::string out;
    out.reserve(size * 2);
    for (size_t index = 0; index < size; index++) {
        out += hex[data[index] >> 4];
        out += hex[data[index] & 0xF];
    }
    return out;
}
}
}
}

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr uint32_t kRoundConstants[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b,
    0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
    0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7,
    0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152,
    0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
    0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f,
    0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

constexpr uint32_t
rotateRight(uint32_t value, uint32_t bits)
{
    return (value >> bits) | (value << (32 - bits));
}

}

AgentSha256::AgentSha256()
{
    state[0] = 0x6a09e667;
    state[1] = 0xbb67ae85;
    state[2] = 0x3c6ef372;
    state[3] = 0xa54ff53a;
    state[4] = 0x510e527f;
    state[5] = 0x9b05688c;
    state[6] = 0x1f83d9ab;
    state[7] = 0x5be0cd19;
}

void
AgentSha256::compress(const uint8_t block[64])
{
    uint32_t w[64];
    for (size_t index = 0; index < 16; ++index) {
        w[index] = uint32_t(block[index * 4]) << 24 |
                   uint32_t(block[index * 4 + 1]) << 16 |
                   uint32_t(block[index * 4 + 2]) << 8 |
                   uint32_t(block[index * 4 + 3]);
    }
    for (size_t index = 16; index < 64; ++index) {
        const uint32_t s0 = rotateRight(w[index - 15], 7) ^
                            rotateRight(w[index - 15], 18) ^
                            (w[index - 15] >> 3);
        const uint32_t s1 = rotateRight(w[index - 2], 17) ^
                            rotateRight(w[index - 2], 19) ^
                            (w[index - 2] >> 10);
        w[index] = w[index - 16] + s0 + w[index - 7] + s1;
    }
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
    for (size_t index = 0; index < 64; ++index) {
        const uint32_t s1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^
                            rotateRight(e, 25);
        const uint32_t ch = (e & f) ^ (~e & g);
        const uint32_t temp1 = h + s1 + ch + kRoundConstants[index] +
                               w[index];
        const uint32_t s0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^
                            rotateRight(a, 22);
        const uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        const uint32_t temp2 = s0 + maj;
        h = g;
        g = f;
        f = e;
        e = d + temp1;
        d = c;
        c = b;
        b = a;
        a = temp1 + temp2;
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
AgentSha256::update(const uint8_t *data, size_t length)
{
    bitCount += uint64_t(length) * 8;
    while (length > 0) {
        const size_t space = 64 - bufferLength;
        const size_t take = length < space ? length : space;
        for (size_t index = 0; index < take; ++index)
            buffer[bufferLength + index] = data[index];
        bufferLength += take;
        data += take;
        length -= take;
        if (bufferLength == 64) {
            compress(buffer);
            bufferLength = 0;
        }
    }
}

std::array<uint8_t, 32>
AgentSha256::finish()
{
    const uint64_t bits = bitCount;
    const uint8_t pad = 0x80;
    update(&pad, 1);
    const uint8_t zero = 0;
    while (bufferLength != 56)
        update(&zero, 1);
    uint8_t lengthBytes[8];
    for (size_t index = 0; index < 8; ++index)
        lengthBytes[index] = uint8_t(bits >> (56 - index * 8));
    update(lengthBytes, 8);
    std::array<uint8_t, 32> digest{};
    for (size_t index = 0; index < 8; ++index) {
        digest[index * 4] = uint8_t(state[index] >> 24);
        digest[index * 4 + 1] = uint8_t(state[index] >> 16);
        digest[index * 4 + 2] = uint8_t(state[index] >> 8);
        digest[index * 4 + 3] = uint8_t(state[index]);
    }
    return digest;
}

std::array<uint8_t, 32>
agentSha256(const std::vector<uint8_t> &data)
{
    AgentSha256 hash;
    hash.update(data.data(), data.size());
    return hash.finish();
}

}
}

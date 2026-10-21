#ifndef DEV_AI_MESH_MESH_BINARY_TEST_SUPPORT_HH
#define DEV_AI_MESH_MESH_BINARY_TEST_SUPPORT_HH
#include <gtest/gtest.h>

#include <algorithm>
#include <tuple>

#include <cstring>
#include <fstream>
#include <vector>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/generated/gate5_overlay_golden.inc"
#include "dev/ai_mesh/generated/gate5_rng_golden.inc"
#include "dev/ai_mesh/generated/gate5_weight_golden.inc"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/generated/serving_golden.inc"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/mesh_serving_verifier.hh"
#include "dev/ai_mesh/mesh_serving_capacity.hh"
#include "dev/ai_mesh/mesh_moe_gate.hh"
#include "dev/ai_mesh/mesh_moe_overlay.hh"
#include "dev/ai_mesh/mesh_weight_cache.hh"
#include "dev/ai_mesh/mesh_moe_runtime.hh"
#include "dev/ai_mesh/mesh_moe_rng.hh"
#include "dev/ai_mesh/mesh_weight_tags.hh"

#include <memory>
#include <sstream>
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_program_test_support.hh"

namespace gem5::ai_mesh::test_support
{
using namespace mesh_abi;
inline MeshBytes fromArray(const unsigned char *data, size_t size)
{
    return MeshBytes(data, data + size);
}

inline uint16_t rd16(const MeshBytes &image, size_t offset)
{
    return uint16_t(image[offset]) | (uint16_t(image[offset + 1]) << 8);
}

inline uint32_t rd32(const MeshBytes &image, size_t offset)
{
    return uint32_t(rd16(image, offset)) | (uint32_t(rd16(image, offset + 2)) << 16);
}

inline uint64_t rd64(const MeshBytes &image, size_t offset)
{
    return uint64_t(rd32(image, offset)) | (uint64_t(rd32(image, offset + 4)) << 32);
}

inline void wr32(MeshBytes &image, size_t offset, uint32_t value)
{
    for (int i = 0; i < 4; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

inline void wr16(MeshBytes &image, size_t offset, uint16_t value)
{
    for (int i = 0; i < 2; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

inline void wr64(MeshBytes &image, size_t offset, uint64_t value)
{
    for (int i = 0; i < 8; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

inline size_t sectionOffset(const MeshBytes &image, uint16_t type)
{
    using namespace mesh_abi;
    const size_t dir = size_t(rd64(image, 24));
    const uint32_t count = rd32(image, 32);
    for (uint32_t i = 0; i < count; i++) {
        const size_t entry = dir + i * kSectionDirBytes;
        if (rd16(image, entry) == type)
            return size_t(rd64(image, entry + 8));
    }
    ADD_FAILURE() << "section " << type << " not found";
    return 0;
}

inline MeshBytes loadFixture(const char *relative_path)
{
    std::ifstream stream(relative_path, std::ios::binary);
    EXPECT_TRUE(stream.good()) << relative_path;
    return MeshBytes((std::istreambuf_iterator<char>(stream)),
                     std::istreambuf_iterator<char>());
}

inline RuntimeArch testMoeArch()
{
    RuntimeArch arch = testArch();
    arch.arch_digest_hex = golden::kGate5ArchDigest;
    arch.partitions = {
        {mesh_abi::kSramPartitionKindSTATIC_PROGRAM, 0x000000, 0x080000, 64,
         64, 0},
        {mesh_abi::kSramPartitionKindRUNTIME_SCRATCH, 0x080000, 0x040000, 64,
         64, 0},
        {mesh_abi::kSramPartitionKindWEIGHT_CACHE, 0x0C0000, 0x080000, 64,
         128, 64},
        {mesh_abi::kSramPartitionKindKV_STAGING_CACHE, 0x140000, 0x080000, 64,
         64, 0},
    };
    arch.weight_cache_slot_bytes = 4096;
    return arch;
}

inline void writeField(MeshBytes &image, size_t offset, int width, uint64_t value)
{
    if (width == 2) {
        wr16(image, offset, uint16_t(value));
    } else if (width == 4) {
        wr32(image, offset, uint32_t(value));
    } else {
        wr64(image, offset, value);
    }
}

inline void recomputeMeshChecksums(MeshBytes &image)
{
    const auto wire = [&]() {
        const auto directory = rd64(image, 24);
        for (uint32_t index = 0; index < rd32(image, 32); ++index) {
            const auto entry = directory + index * mesh_abi::kSectionDirBytes;
            const auto offset = rd64(image, entry + 8), size = rd64(image, entry + 16);
            ASSERT_LE(offset, image.size());
            ASSERT_LE(size, image.size() - offset);
            wr32(image, entry + 32, mesh_binary_detail::meshCrc32(image.data() + offset, size));
        }
        mesh_hash::Sha256 hash;
        hash.update(image.data() + mesh_abi::kHeaderBytes, image.size() - mesh_abi::kHeaderBytes);
        const auto digest = hash.digest();
        std::copy(digest.begin(), digest.end(), image.begin() + 72);
    };
    wire();
    mesh_binary_detail::DecodedEnvelope envelope;
    DecodedProgram program;
    MeshLoadError error;
    if (!mesh_binary_detail::decodeMeshEnvelope(image, envelope, error) ||
        !mesh_binary_detail::decodeProgramStorage(image, envelope, program, error))
        return;
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream out(&buffer);
    if (!mesh_binary_detail::writeProgramCanonical(program,
            mesh_abi::CanonicalProjection::Semantic, out, error))
        return;
    program.metadata.semantic_sha256 = buffer.digest();
    ASSERT_TRUE(encodeMeshBinary(program, image, error)) << error.message;
}

inline void refreshServingChecksums(MeshBytes &image)
{
    using namespace mesh_abi;
    recomputeMeshChecksums(image);
    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error))
        return;
    std::array<uint8_t, 32> base;
    ASSERT_TRUE(programProfileKeyBaseDigest(program, base, error)) << error.message;
    const size_t section =
        sectionOffset(image, kSectionTypeAGENT_REQUEST_PROFILES);
    for (size_t i = 0; i < program.transport.agent_request_profiles.size(); i++) {
        const uint64_t key =
            requestProfileKey(program.transport.agent_request_profiles[i], base);
        writeField(image,
                   section + i * kAgentRequestProfilesBytes +
                       kAgentRequestProfilesRequestedProfileKeyOffset,
                   8, key);
    }
    recomputeMeshChecksums(image);
}

inline std::array<uint8_t, 32> hexToDigest(const char *hex)
{
    std::array<uint8_t, 32> out{};
    for (size_t i = 0; i < 32; i++) {
        const auto nibble = [](char c) -> uint8_t {
            return c <= '9' ? uint8_t(c - '0') : uint8_t(c - 'a' + 10);
        };
        out[i] = uint8_t((nibble(hex[2 * i]) << 4) | nibble(hex[2 * i + 1]));
    }
    return out;
}

inline std::string digestToHex(const std::array<uint8_t, 32> &digest)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}


}
#endif

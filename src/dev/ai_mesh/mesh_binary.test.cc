#include <gtest/gtest.h>

#include <cstring>
#include <vector>

#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

// The embedded golden images come from the Python encoder CLI and pin the
// cross-language ABI contract: the C++ reader must accept them and reject
// any single-bit corruption.
MeshBytes fromArray(const unsigned char *data, size_t size)
{
    return MeshBytes(data, data + size);
}

RuntimeArch testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.sram_banks = 16;
    arch.sram_alignment = 64;
    arch.axi_data_bytes = 32;
    arch.axi_max_burst_beats = 16;
    RuntimeArch::Region hbm;
    hbm.region_id = 0;
    hbm.base = 0x800000000;
    hbm.bytes = 0x400000000;
    RuntimeArch::Region shared;
    shared.region_id = 1;
    shared.base = 0x100000000;
    shared.bytes = 0x10000000;
    RuntimeArch::Region sram;
    sram.region_id = 2;
    sram.base = 0x400000000;
    sram.bytes = 0x800000;
    sram.tile_stride = 0x400000;
    sram.tile_bytes = 0x200000;
    sram.is_sram_aperture = true;
    arch.regions = {hbm, shared, sram};
    return arch;
}

TEST(MeshBinaryTest, LoadsGoldenSingleProgram)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.abi_major, 1u);
    EXPECT_EQ(program.abi_minor, 2u);
    EXPECT_EQ(program.commands.size(), 7u);
    EXPECT_EQ(program.descriptors.size(), 3u);
    EXPECT_EQ(program.traffic.size(), 3u);
    EXPECT_TRUE(verifyDecodedProgram(program, testArch(), error)) << error.message;
}

TEST(MeshBinaryTest, LoadsGoldenDualProgram)
{
    MeshBytes image = fromArray(kGoldenMshbDual, sizeof(kGoldenMshbDual));
    ASSERT_FALSE(image.empty());
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error)) << error.code << ": " << error.message;
    EXPECT_EQ(program.commands.size(), 14u);
    EXPECT_EQ(program.descriptors.size(), 6u);
    EXPECT_TRUE(verifyDecodedProgram(program, testArch(), error)) << error.message;
}

TEST(MeshBinaryTest, RejectsBadMagic)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image[0] ^= 0xFF;
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_MAGIC");
}

TEST(MeshBinaryTest, RejectsSingleBitPayloadCorruption)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image[200] ^= 0x01;
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
}

TEST(MeshBinaryTest, RejectsTruncation)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    image.resize(image.size() - 8);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_FALSE(decodeMeshBinary(image, program, error));
}

TEST(MeshBinaryTest, RejectsArchDigestMismatch)
{
    MeshBytes image = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    RuntimeArch wrong = testArch();
    wrong.arch_digest_hex = "deadbeef";
    ASSERT_FALSE(verifyDecodedProgram(program, wrong, error));
    EXPECT_EQ(error.code, "E_ARCH_DIGEST");
}

TEST(MeshBinaryTest, HeaderLayoutMatchesSpec)
{
    using namespace mesh_abi;
    static_assert(kHeaderBytes == 128);
    static_assert(kSectionDirBytes == 40);
    static_assert(kCommandsBytes == 40);
    EXPECT_EQ(kMagic, 0x010000004248534Dull);
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

uint16_t rd16(const MeshBytes &image, size_t offset)
{
    return uint16_t(image[offset]) | (uint16_t(image[offset + 1]) << 8);
}

uint32_t rd32(const MeshBytes &image, size_t offset)
{
    return uint32_t(rd16(image, offset)) | (uint32_t(rd16(image, offset + 2)) << 16);
}

uint64_t rd64(const MeshBytes &image, size_t offset)
{
    return uint64_t(rd32(image, offset)) | (uint64_t(rd32(image, offset + 4)) << 32);
}

void wr32(MeshBytes &image, size_t offset, uint32_t value)
{
    for (int i = 0; i < 4; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

void wr16(MeshBytes &image, size_t offset, uint16_t value)
{
    for (int i = 0; i < 2; i++)
        image[offset + i] = uint8_t(value >> (8 * i));
}

size_t sectionOffset(const MeshBytes &image, uint16_t type)
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

// The three recomputed-checksum mutations from the cross-language parity
// audit: each must be rejected by BOTH the Python and the C++ readers.
using namespace mesh_abi;

struct Mutation
{
    const char *name;
    size_t offset;
    uint8_t mask;
    const char *code;
};

TEST(MeshBinaryParityTest, RejectsRecomputedChecksumMutations)
{
    const MeshBytes golden = fromArray(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    const size_t attrs = sectionOffset(golden, kSectionTypeOP_ATTRS);
    const size_t descriptors = sectionOffset(golden, kSectionTypeDMA_DESCRIPTORS);
    const size_t operands = sectionOffset(golden, kSectionTypeCOMMAND_OPERANDS);
    const Mutation mutations[] = {
        // OP_ATTRS record 0: flip its reserved u16 -> E_ABI_RESERVED.
        {"attr_reserved",
         attrs + kOpAttrsReservedOffset, 0x01, "E_ABI_RESERVED"},
        // descriptor 0 src.memory_space bits flipped off HBM -> E_DMA_RANGE.
        {"load_direction",
         descriptors + kDmaDescriptorsSrcOffset +
             kDmaEndpointMemorySpaceOffset, 0x06, "E_DMA_RANGE"},
        // descriptor kind high byte -> outside the closed set.
        {"endpoint_wrap",
         descriptors + kDmaDescriptorsKindOffset + 1, 0xFF, "E_DMA_RANGE"},
        {"operand_reserved",
         operands + kCommandOperandsReservedOffset, 0x01,
         "E_ABI_RESERVED"},
    };
    for (const auto &mutation : mutations) {
        MeshBytes image = golden;
        image[mutation.offset] ^= mutation.mask;
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        bool decoded = decodeMeshBinary(image, program, error);
        bool verified = false;
        if (decoded)
            verified = verifyDecodedProgram(program, testArch(), error);
        const bool accepted = decoded && verified;
        EXPECT_FALSE(accepted) << mutation.name;
        if (!accepted) {
            EXPECT_FALSE(error.code.empty()) << mutation.name;
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5


namespace gem5
{
namespace ai_mesh
{
namespace
{

// begin+count table slices must be range-checked without 32-bit wrap: a
// wrapped span silently skips per-element validation (or reads far out of
// bounds) instead of failing closed.
TEST(MeshBinarySpanTest, RejectsBeginCountWrap)
{
    using namespace mesh_abi;
    const MeshBytes golden = fromArray(kGoldenMshbRepeat, sizeof(kGoldenMshbRepeat));
    const size_t cmds = sectionOffset(golden, kSectionTypeCOMMANDS);
    const size_t attrs = sectionOffset(golden, kSectionTypeOP_ATTRS);
    const size_t streams = sectionOffset(golden, kSectionTypeSTREAMS);

    DecodedProgram reference;
    MeshLoadError decode_error;
    ASSERT_TRUE(decodeMeshBinary(golden, reference, decode_error));
    const DecodedCommand *repeat = nullptr;
    for (const auto &command : reference.commands)
        if (command.opcode == kOpcodeREPEAT)
            repeat = &command;
    ASSERT_NE(repeat, nullptr);
    uint32_t ordinal = 0;
    for (const auto &stream : reference.streams) {
        if (stream.core_id != repeat->core_id || stream.stream_id != repeat->stream_id)
            continue;
        for (uint32_t i = 0; i < stream.command_count; i++)
            if (reference.commands[stream.command_begin + i].command_id ==
                repeat->command_id)
                ordinal = i;
    }

    struct Case
    {
        const char *name;
        std::function<void(MeshBytes &)> patch;
    };
    const Case cases[] = {
        {"wait_wrap", [&](MeshBytes &image) {
             wr32(image, cmds + kCommandsBytes + kCommandsWaitBeginOffset, 0xFFFFFFFFu);
             wr16(image, cmds + kCommandsBytes + kCommandsWaitCountOffset, 2);
         }},
        {"operand_wrap", [&](MeshBytes &image) {
             wr32(image, cmds + kCommandsBytes + kCommandsOperandBeginOffset, 0xFFFFFFFFu);
             wr16(image, cmds + kCommandsBytes + kCommandsOperandCountOffset, 2);
         }},
        {"stream_wrap", [&](MeshBytes &image) {
             wr32(image, streams + kStreamsCommandBeginOffset, 0xFFFFFFFFu);
             wr32(image, streams + kStreamsCommandCountOffset, 2);
         }},
        {"repeat_wrap", [&](MeshBytes &image) {
             const size_t record = attrs + size_t(repeat->attr_index - 1) * kOpAttrsBytes;
             wr32(image, record + 4 + 0, 0xFFFFFFFFu);
             wr32(image, record + 4 + 4, ordinal + 1);
         }},
    };
    for (const auto &test_case : cases) {
        MeshBytes image = golden;
        test_case.patch(image);
        recomputeMeshChecksums(image);
        DecodedProgram program;
        MeshLoadError error;
        const bool accepted =
            decodeMeshBinary(image, program, error) &&
            verifyDecodedProgram(program, testArch(), error);
        EXPECT_FALSE(accepted) << test_case.name;
        if (!accepted) {
            EXPECT_EQ(error.code, "E_ABI_BOUNDS") << test_case.name;
        }
    }
}

} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

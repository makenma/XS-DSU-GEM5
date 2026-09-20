#include <gtest/gtest.h>

#include <cstddef>
#include <memory>

#include "dev/ai_mesh/generated/golden_mshb.inc"
#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"

namespace gem5
{
namespace ai_mesh
{
namespace
{

bool
admit(const DecodedProgram &program, const RuntimeArch &arch,
      MeshLoadError &error)
{
    MeshProgramAdmission admission;
    return admitProgram(std::make_shared<DecodedProgram>(program), arch,
                        admission, error);
}

RuntimeArch
testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.sram_banks = 16;
    arch.sram_alignment = 64;
    arch.axi_data_bytes = 32;
    arch.axi_max_burst_beats = 16;
    arch.regions = {
        {0, 0x800000000, 0x400000000, 0, 0,
         RuntimeArch::Region::kKindHbm, false},
        {1, 0x100000000, 0x10000000, 0, 0,
         RuntimeArch::Region::kKindHostShared, false},
        {2, 0x400000000, 0x800000, 0x400000, 0x200000,
         RuntimeArch::Region::kKindSramAperture, true},
    };
    return arch;
}

void
expectAccepts(const unsigned char *bytes, size_t size)
{
    MeshBytes image(bytes, bytes + size);
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(program.header.abi_major, mesh_abi::kAbiMajor);
    EXPECT_EQ(program.header.abi_minor, mesh_abi::kAbiMinor);
    EXPECT_EQ(program.header.required_features,
              mesh_abi::kRequiredFeatures);
    ASSERT_TRUE(admit(program, testArch(), error))
        << error.code << ": " << error.message;
    MeshBytes reencoded;
    ASSERT_TRUE(encodeMeshBinary(program, reencoded, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(reencoded, image);
}

TEST(MeshBinaryTest, LoadsCompleteGoldenPrograms)
{
    expectAccepts(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    expectAccepts(kGoldenMshbDual, sizeof(kGoldenMshbDual));
    expectAccepts(kGoldenMshbRepeat, sizeof(kGoldenMshbRepeat));
}

TEST(MeshBinaryTest, RejectsBadMagic)
{
    MeshBytes image(
        kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    image.front() ^= 0xff;
    DecodedProgram program;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_MAGIC");
}

TEST(MeshBinaryTest, RejectsPayloadCorruptionAtomically)
{
    MeshBytes image(
        kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    image.back() ^= 1;
    DecodedProgram program;
    program.header.abi_minor = 77;
    MeshLoadError error;
    EXPECT_FALSE(decodeMeshBinary(image, program, error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
    EXPECT_EQ(program.header.abi_minor, 77);
}

TEST(MeshBinaryTest, RejectsMutableVersionAndChecksum)
{
    MeshBytes image(
        kGoldenMshbSingle,
        kGoldenMshbSingle + sizeof(kGoldenMshbSingle));
    DecodedProgram program;
    MeshLoadError error;
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    program.header.abi_major = 0;
    EXPECT_FALSE(admit(program, testArch(), error));
    EXPECT_EQ(error.code, "E_ABI_VERSION");
    ASSERT_TRUE(decodeMeshBinary(image, program, error));
    program.metadata.semantic_sha256.front() ^= 1;
    EXPECT_FALSE(admit(program, testArch(), error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
}

TEST(MeshBinaryTest, LayoutConstantsMatchSchema)
{
    static_assert(mesh_abi::kHeaderBytes == 128);
    static_assert(mesh_abi::kSectionDirBytes == 40);
    static_assert(mesh_abi::kCommandsBytes == 40);
    EXPECT_EQ(mesh_abi::kAbiMajor, 1);
    EXPECT_EQ(mesh_abi::kAbiMinor, 3);
}

}
}
}

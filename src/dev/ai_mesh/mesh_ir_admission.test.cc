#include <gtest/gtest.h>

#include <cstddef>
#include <memory>

#include "dev/ai_mesh/command_rom.hh"
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
    arch.axi_address_bits = 64;
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

std::shared_ptr<DecodedProgram>
decodeGolden(const unsigned char *bytes, size_t size)
{
    auto program = std::make_shared<DecodedProgram>();
    MeshBytes image(bytes, bytes + size);
    MeshLoadError error;
    EXPECT_TRUE(decodeMeshBinary(image, *program, error))
        << error.code << ": " << error.message;
    return program;
}

TEST(MeshIrAdmissionTest, BuildsConsistentFactDomain)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    const RuntimeArch arch = testArch();
    MeshProgramAdmission admission;
    MeshLoadError error;
    ASSERT_TRUE(admitProgram(program, arch, admission, error))
        << error.code << ": " << error.message;
    const DecodedProgram &decoded = admission.program();
    EXPECT_EQ(&admission.arch(), &arch);
    EXPECT_EQ(decoded.header.abi_major, mesh_abi::kAbiMajor);
    EXPECT_TRUE(admission.geometry().matches(decoded, admission.context()));
    EXPECT_TRUE(admission.backing().matches(decoded, admission.context(), arch,
                                            admission.geometry()));
    EXPECT_TRUE(admission.projection().matches(decoded, admission.context(),
                                               arch));
    EXPECT_TRUE(admission.matrixFacts().matches(decoded, admission.context(),
                                                admission.geometry()));
    EXPECT_TRUE(admission.dma().matches(
        decoded, admission.context(), arch, admission.geometry(),
        admission.backing(), admission.projection()));
    EXPECT_TRUE(admission.intrinsic().matches(decoded, admission.context()));
    EXPECT_TRUE(admission.control().matches(
        decoded, admission.context(), arch, admission.geometry(),
        admission.backing(), admission.projection(), admission.dma(),
        admission.intrinsic()));
    EXPECT_GE(admission.context().variants().size(), 1u);
}

TEST(MeshIrAdmissionTest, RejectsRuntimeArchOutsideAddressWidth)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    RuntimeArch arch = testArch();
    arch.axi_address_bits = 32;
    MeshProgramAdmission admission;
    MeshLoadError error;
    EXPECT_FALSE(admitProgram(program, arch, admission, error));
    EXPECT_EQ(error.code, "E_ABI_BOUNDS");
}

TEST(MeshIrAdmissionTest, RejectsOverlappingRuntimeRegions)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    RuntimeArch arch = testArch();
    arch.regions[1].base = arch.regions[0].base + 0x1000;
    MeshProgramAdmission admission;
    MeshLoadError error;
    EXPECT_FALSE(admitProgram(program, arch, admission, error));
    EXPECT_EQ(error.code, "E_ABI_BOUNDS");
}

TEST(MeshIrAdmissionTest, RejectsDuplicateRuntimeCoreIds)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    RuntimeArch arch = testArch();
    arch.core_ids = {0, 0};
    MeshProgramAdmission admission;
    MeshLoadError error;
    EXPECT_FALSE(admitProgram(program, arch, admission, error));
    EXPECT_EQ(error.code, "E_ABI_BOUNDS");
}

TEST(MeshIrAdmissionTest, RejectsUnsupportedAxiDataWidth)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    RuntimeArch arch = testArch();
    arch.axi_data_bytes = 24;
    MeshProgramAdmission admission;
    MeshLoadError error;
    EXPECT_FALSE(admitProgram(program, arch, admission, error));
    EXPECT_EQ(error.code, "E_ABI_ENUM");
}

TEST(MeshIrAdmissionTest, RejectsTamperedChecksum)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    program->metadata.semantic_sha256.front() ^= 1;
    MeshProgramAdmission admission;
    MeshLoadError error;
    EXPECT_FALSE(admitProgram(program, testArch(), admission, error));
    EXPECT_EQ(error.code, "E_ABI_CHECKSUM");
}

TEST(MeshIrAdmissionTest, CommandRomSelectsOnlyOwnedCoreStreams)
{
    auto program = decodeGolden(kGoldenMshbSingle, sizeof(kGoldenMshbSingle));
    const RuntimeArch arch = testArch();
    MeshProgramAdmission admission;
    MeshLoadError error;
    ASSERT_TRUE(admitProgram(program, arch, admission, error))
        << error.code << ": " << error.message;

    CommandRom rom;
    ASSERT_TRUE(buildCommandRom(admission, 1, 0, rom, error))
        << error.code << ": " << error.message;
    EXPECT_EQ(rom.variantId(), 1u);
    ASSERT_FALSE(rom.streams().empty());
    for (uint16_t stream_id : rom.streams()) {
        const std::vector<uint32_t> *indices = rom.commandIndices(stream_id);
        ASSERT_NE(indices, nullptr);
        ASSERT_FALSE(indices->empty());
        for (uint32_t index : *indices) {
            ASSERT_LT(index, admission.program().transport.commands.size());
            EXPECT_EQ(admission.program().transport.commands[index].stream_id,
                      stream_id);
        }
    }

    CommandRom unknown;
    EXPECT_FALSE(buildCommandRom(admission, 999, 0, unknown, error));
    EXPECT_EQ(error.code, "E_RELOCATION");
}

}
}
}

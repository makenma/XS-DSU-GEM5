#ifndef DEV_AI_MESH_MESH_PROGRAM_TEST_SUPPORT_HH
#define DEV_AI_MESH_MESH_PROGRAM_TEST_SUPPORT_HH

#include <fstream>
#include <stdexcept>
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_program_invocation.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/generated/golden_mshb.inc"

namespace gem5::ai_mesh::test_support
{
using namespace mesh_abi;
inline bool admit(const DecodedProgram &program, const RuntimeArch &arch, MeshLoadError &error)
{
    MeshProgramAdmission admission;
    return admitProgram(std::make_shared<DecodedProgram>(program), arch, admission, error);
}

inline RuntimeArch testArch()
{
    RuntimeArch arch;
    arch.arch_digest_hex = kGoldenArchDigest;
    arch.core_ids = {0, 1};
    arch.sram_bytes = 2097152;
    arch.command_rom_entries = 65536;
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
    shared.kind = RuntimeArch::Region::kKindHostShared;
    shared.base = 0x100000000;
    shared.bytes = 0x10000000;
    RuntimeArch::Region sram;
    sram.region_id = 2;
    sram.base = 0x400000000;
    sram.bytes = 0x800000;
    sram.tile_stride = 0x400000;
    sram.tile_bytes = 0x200000;
    sram.kind = RuntimeArch::Region::kKindSramAperture;
    sram.is_sram_aperture = true;
    arch.regions = {hbm, shared, sram};
    return arch;
}


inline std::string hexDigest(const std::array<uint8_t, 32> &digest)
{
    static const char digits[] = "0123456789abcdef";
    std::string out;
    for (uint8_t byte : digest) {
        out += digits[byte >> 4];
        out += digits[byte & 0xF];
    }
    return out;
}

inline DecodedProgram loadFixture(const std::string &path)
{
    std::ifstream file(path, std::ios::binary);
    if (!file.good())
        throw std::runtime_error("fixture is unavailable: " + path);
    MeshBytes bytes((std::istreambuf_iterator<char>(file)), {});
    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(bytes, program, error))
        throw std::runtime_error(error.code + ": " + error.message);
    return program;
}

inline std::array<uint8_t, 32> profileBaseDigest(const DecodedProgram &program)
{
    std::array<uint8_t, 32> result{};
    MeshLoadError error;
    if (!programProfileKeyBaseDigest(program, result, error))
        throw std::runtime_error(error.code + ": " + error.message);
    return result;
}

inline std::shared_ptr<const MeshProgramAdmission> admittedFixture(
    const DecodedProgram &program, RuntimeArch &arch)
{
    arch.arch_digest_hex = hexDigest(program.header.arch_digest);
    auto admission = std::make_shared<MeshProgramAdmission>();
    MeshLoadError error;
    if (!admitProgram(std::make_shared<DecodedProgram>(program), arch, *admission, error))
        throw std::runtime_error(error.code + ": " + error.message);
    return admission;
}

struct AdmittedFixture
{
    DecodedProgram program;
    RuntimeArch arch = testArch();
    std::shared_ptr<const MeshProgramAdmission> admission;

    explicit AdmittedFixture(const std::string &path)
        : program(loadFixture(path)), admission(admittedFixture(program, arch)) {}

    std::shared_ptr<const MeshProgramInvocation> invocation(
        uint32_t entrypoint, uint32_t profile,
        const std::vector<DispatchBinding> &bindings = {}) const
    {
        std::shared_ptr<const MeshProgramInvocation> result;
        MeshLoadError error;
        if (!prepareProgramInvocation(admission, entrypoint, profile, bindings, result, error))
            throw std::runtime_error(error.code + ": " + error.message);
        return result;
    }
};
}

#endif

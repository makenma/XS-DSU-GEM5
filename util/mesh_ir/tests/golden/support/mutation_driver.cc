// Standalone fail-closed driver for the cross-language mutation corpus.
// argv[1] = .mshb path, argv[2] = architecture facts file.  Prints exactly
// one line:
//   ACCEPTED            (decode + verify both passed)
//   DECODE:<code>       (binary integrity rejection)
//   VERIFY:<code>       (structural rejection)
// The architecture digest and every behavioral fact come from the facts
// file (written by the Python harness straight from mesh_1x2.yaml), never
// from the binary under test, so a mutant cannot self-certify its arch
// binding.  Built with -fsanitize=address,undefined by the test harness;
// any crash, hang or sanitizer abort is itself a failure signal.
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

using namespace gem5::ai_mesh;

int main(int argc, char **argv)
{
    if (argc != 3)
        return 2;
    std::ifstream file(argv[1], std::ios::binary);
    std::vector<uint8_t> image((std::istreambuf_iterator<char>(file)),
                               std::istreambuf_iterator<char>());
    if (image.empty())
        return 2;

    RuntimeArch arch;
    {
        std::ifstream facts(argv[2]);
        if (!facts)
            return 2;
        std::string digest;
        uint64_t sram_bytes = 0, region_base = 0, region_bytes = 0,
                 tile_stride = 0, tile_bytes = 0;
        uint32_t sram_banks = 0, sram_alignment = 0, axi_data_bytes = 0,
                 axi_max_burst_beats = 0, region_id = 0;
        std::string core_ids_csv, region_line;
        std::getline(facts, digest);
        facts >> sram_bytes >> sram_banks >> sram_alignment >>
            axi_data_bytes >> axi_max_burst_beats;
        facts >> core_ids_csv;
        arch.arch_digest_hex = digest;
        arch.sram_bytes = sram_bytes;
        arch.sram_banks = sram_banks;
        arch.sram_alignment = sram_alignment;
        arch.axi_data_bytes = axi_data_bytes;
        arch.axi_max_burst_beats = axi_max_burst_beats;
        {
            std::istringstream cores(core_ids_csv);
            std::string token;
            while (std::getline(cores, token, ','))
                if (!token.empty())
                    arch.core_ids.push_back(uint32_t(std::stoul(token)));
        }
        std::string rest;
        std::getline(facts, rest); // consume newline after core ids
        uint32_t kind = 0;
        facts >> arch.tensor_dtype_mask >> arch.vector_dtype_mask >>
            arch.reduce_dtype_mask;
        while (std::getline(facts, region_line)) {
            if (region_line.empty())
                continue;
            std::istringstream fields(region_line);
            if (!(fields >> region_id >> region_base >> region_bytes >>
                  tile_stride >> tile_bytes >> kind))
                continue;
            RuntimeArch::Region region;
            region.region_id = region_id;
            region.base = region_base;
            region.bytes = region_bytes;
            region.tile_stride = tile_stride;
            region.tile_bytes = tile_bytes;
            region.kind = kind;
            region.is_sram_aperture = kind == 2;
            arch.regions.push_back(region);
        }
    }

    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error)) {
        std::printf("DECODE:%s\n", error.code.c_str());
        return 0;
    }
    if (!verifyDecodedProgram(program, arch, error)) {
        std::printf("VERIFY:%s\n", error.code.c_str());
        return 0;
    }
    std::printf("ACCEPTED\n");
    return 0;
}

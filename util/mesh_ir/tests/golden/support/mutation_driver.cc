#include <algorithm>
#include <charconv>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <limits>
#include <memory>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_ir_admission.hh"
#include "dev/ai_mesh/mesh_ir_verifier.hh"

using namespace gem5::ai_mesh;

template <class T>
bool
parseUnsigned(std::string_view text, T &value)
{
    uint64_t parsed = 0;
    const auto result = std::from_chars(
        text.data(), text.data() + text.size(), parsed);
    if (text.empty() || result.ec != std::errc() ||
        result.ptr != text.data() + text.size() ||
        parsed > std::numeric_limits<T>::max())
        return false;
    value = static_cast<T>(parsed);
    return true;
}

bool
splitFields(const std::string &line, std::vector<std::string> &fields)
{
    std::istringstream input(line);
    std::string field;
    while (input >> field)
        fields.push_back(field);
    return input.eof();
}

bool
parseArchFacts(const char *path, RuntimeArch &arch)
{
    std::ifstream facts(path);
    if (!facts)
        return false;
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(facts, line))
        lines.push_back(line);
    if (!facts.eof() || lines.size() < 9)
        return false;
    if (lines[0].size() != 64 ||
        !std::all_of(
            lines[0].begin(), lines[0].end(),
            [](unsigned char value) {
                return (value >= '0' && value <= '9') ||
                    (value >= 'a' && value <= 'f') ||
                    (value >= 'A' && value <= 'F');
            }))
        return false;
    if (!parseUnsigned(lines[1], arch.sram_bytes) ||
        !parseUnsigned(lines[2], arch.sram_banks) ||
        !parseUnsigned(lines[3], arch.sram_alignment) ||
        !parseUnsigned(lines[4], arch.axi_data_bytes) ||
        !parseUnsigned(lines[5], arch.axi_max_burst_beats))
        return false;
    arch.arch_digest_hex = lines[0];
    std::string_view coreIds(lines[6]);
    size_t begin = 0;
    while (begin < coreIds.size()) {
        const size_t end = coreIds.find(',', begin);
        const std::string_view token = coreIds.substr(
            begin, end == std::string_view::npos ? coreIds.size() - begin :
            end - begin);
        uint32_t coreId = 0;
        if (!parseUnsigned(token, coreId) ||
            std::find(arch.core_ids.begin(), arch.core_ids.end(), coreId) !=
                arch.core_ids.end())
            return false;
        arch.core_ids.push_back(coreId);
        if (end == std::string_view::npos)
            break;
        if (end + 1 == coreIds.size())
            return false;
        begin = end + 1;
    }
    if (arch.core_ids.empty())
        return false;
    std::vector<std::string> fields;
    if (!splitFields(lines[7], fields) || fields.size() != 3 ||
        !parseUnsigned(fields[0], arch.tensor_dtype_mask) ||
        !parseUnsigned(fields[1], arch.vector_dtype_mask) ||
        !parseUnsigned(fields[2], arch.reduce_dtype_mask))
        return false;
    for (size_t index = 8; index < lines.size(); ++index) {
        fields.clear();
        if (!splitFields(lines[index], fields) || fields.size() != 6)
            return false;
        RuntimeArch::Region region;
        if (!parseUnsigned(fields[0], region.region_id) ||
            !parseUnsigned(fields[1], region.base) ||
            !parseUnsigned(fields[2], region.bytes) ||
            !parseUnsigned(fields[3], region.tile_stride) ||
            !parseUnsigned(fields[4], region.tile_bytes) ||
            !parseUnsigned(fields[5], region.kind) ||
            region.kind > RuntimeArch::Region::kKindSramAperture ||
            std::any_of(
                arch.regions.begin(), arch.regions.end(),
                [&](const RuntimeArch::Region &existing) {
                    return existing.region_id == region.region_id;
                }))
            return false;
        region.is_sram_aperture =
            region.kind == RuntimeArch::Region::kKindSramAperture;
        arch.regions.push_back(region);
    }
    return true;
}

bool
writeFile(FILE *output, const char *data, size_t size)
{
    if (size && std::fwrite(data, 1, size, output) != size) {
        std::fclose(output);
        return false;
    }
    return std::fclose(output) == 0;
}

int
main(int argc, char **argv)
{
    if (argc != 3 && argc != 5)
        return 2;
    std::ifstream file(argv[1], std::ios::binary);
    if (!file)
        return 2;
    std::vector<uint8_t> image(
        (std::istreambuf_iterator<char>(file)),
        std::istreambuf_iterator<char>());
    if (image.empty() || file.bad())
        return 2;
    RuntimeArch arch;
    if (!parseArchFacts(argv[2], arch))
        return 2;
    DecodedProgram program;
    MeshLoadError error;
    if (!decodeMeshBinary(image, program, error)) {
        std::printf("DECODE:%s\n", error.code.c_str());
        return 0;
    }
    MeshProgramAdmission admission;
    if (!admitProgram(std::make_shared<DecodedProgram>(program), arch,
                      admission, error)) {
        std::printf("VERIFY:%s\n", error.code.c_str());
        return 0;
    }
    if (argc == 5) {
        MeshBytes reencoded;
        if (!encodeMeshBinary(program, reencoded, error))
            return 2;
        std::ostringstream canonical;
        if (!mesh_binary_detail::writeProgramCanonical(
                program, mesh_abi::CanonicalProjection::Full, canonical,
                error) || !canonical.good())
            return 2;
        const std::string canonicalBytes = canonical.str();
        FILE *imageOutput = std::fopen(argv[3], "wbx");
        if (!imageOutput)
            return 2;
        FILE *canonicalOutput = std::fopen(argv[4], "wbx");
        if (!canonicalOutput) {
            std::fclose(imageOutput);
            std::remove(argv[3]);
            return 2;
        }
        if (!writeFile(
                imageOutput, reinterpret_cast<const char *>(reencoded.data()),
                reencoded.size())) {
            std::fclose(canonicalOutput);
            std::remove(argv[3]);
            std::remove(argv[4]);
            return 2;
        }
        if (!writeFile(
                canonicalOutput, canonicalBytes.data(),
                canonicalBytes.size())) {
            std::remove(argv[3]);
            std::remove(argv[4]);
            return 2;
        }
    }
    std::printf("ACCEPTED\n");
    return 0;
}

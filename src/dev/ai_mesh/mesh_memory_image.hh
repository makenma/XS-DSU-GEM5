#ifndef DEV_AI_MESH_MESH_MEMORY_IMAGE_HH
#define DEV_AI_MESH_MESH_MEMORY_IMAGE_HH

#include <cstdint>
#include <array>
#include <functional>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/memory_address_map.hh"

namespace gem5::ai_mesh
{
struct MemoryImageSpan
{
    uint64_t address = 0;
    std::vector<uint8_t> data;
};

struct MemoryFileSpan
{
    uint64_t address;
    std::string path;
    uint64_t offset;
    uint64_t size;
    std::array<uint8_t, 32> sha256;
};

using MemoryImageWriter = std::function<bool(
    uint32_t, uint64_t, size_t, const uint8_t *)>;

bool installMemoryFiles(const std::vector<MemoryFileSpan> &image,
    const MemoryAddressMap &map, const MemoryImageWriter &write,
    std::string &reason);

std::optional<std::vector<MemoryFileSpan>> parseMemoryFileImage(
    const std::string &document, const std::string &directory,
    const std::string &program_sha256, const std::string &architecture_sha256,
    std::string &reason);
}
#endif

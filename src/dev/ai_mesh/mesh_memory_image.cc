#include "dev/ai_mesh/mesh_memory_image.hh"

#include <algorithm>
#include <fstream>
#include <filesystem>
#include <limits>
#include <stdexcept>

#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_json.hh"

namespace gem5::ai_mesh
{
std::optional<std::vector<MemoryFileSpan>>
parseMemoryFileImage(const std::string &document, const std::string &directory,
    const std::string &program_sha256, const std::string &architecture_sha256,
    std::string &reason)
{
    const auto parsed = parseMeshJson(document, reason);
    if (!parsed)
        return std::nullopt;
    const auto exact = [](const boost::json::object &object,
                          std::initializer_list<const char *> keys) {
        if (object.size() != keys.size() ||
            std::any_of(keys.begin(), keys.end(),
                [&](const auto key) { return !object.contains(key); }))
            throw std::invalid_argument("file image has missing or unknown fields");
    };
    const auto digest = [](const boost::json::value &value) {
        const auto &text = value.as_string();
        if (text.size() != 64 || !std::all_of(text.begin(), text.end(), [](char c) {
                return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }))
            throw std::invalid_argument("file image digest is not lowercase SHA-256");
        std::array<uint8_t, 32> result;
        for (size_t i = 0; i < result.size(); ++i)
            result[i] = std::stoul(std::string(text.data() + i * 2, 2), nullptr, 16);
        return result;
    };
    const auto number = [](const boost::json::value &value) -> uint64_t {
        if (value.is_uint64())
            return value.as_uint64();
        if (value.is_int64() && value.as_int64() >= 0)
            return value.as_int64();
        throw std::invalid_argument("file image interval requires unsigned integers");
    };
    try {
        const auto &root = parsed->as_object();
        exact(root, {"schema", "program_semantic_sha256", "architecture_sha256", "spans"});
        if (root.at("schema").as_string() != "mesh-memory-file-image-v1" ||
            mesh_hash::digestHex(digest(root.at("program_semantic_sha256"))) != program_sha256 ||
            mesh_hash::digestHex(digest(root.at("architecture_sha256"))) != architecture_sha256)
            throw std::invalid_argument("file image Program or architecture identity mismatch");
        std::vector<MemoryFileSpan> result;
        for (const auto &entry : root.at("spans").as_array()) {
            const auto &row = entry.as_object();
            exact(row, {"address", "path", "offset", "size", "sha256"});
            const auto &name = row.at("path").as_string();
            if (name.empty() || std::find(name.begin(), name.end(), '\0') != name.end())
                throw std::invalid_argument("file image path is empty or contains NUL");
            const auto path = std::filesystem::path(directory) /
                std::string(name.data(), name.size());
            result.push_back({number(row.at("address")), path.string(),
                number(row.at("offset")), number(row.at("size")), digest(row.at("sha256"))});
        }
        if (result.empty())
            throw std::invalid_argument("file image contains no spans");
        return result;
    } catch (const std::exception &error) {
        reason = error.what();
        return std::nullopt;
    }
}

bool
installMemoryFiles(const std::vector<MemoryFileSpan> &image,
    const MemoryAddressMap &map, const MemoryImageWriter &write,
    std::string &reason)
{
    reason.clear();
    std::vector<std::ifstream> files;
    std::vector<std::vector<MemoryAddressSpan>> plans;
    std::vector<std::pair<uint64_t, uint64_t>> destinations;
    for (const auto &row : image) {
        auto plan = map.split(row.address, row.size);
        if (!plan || row.offset > uint64_t(std::numeric_limits<std::streamoff>::max()) ||
            row.size > uint64_t(std::numeric_limits<std::streamoff>::max()) - row.offset) {
            reason = "file image has an unmapped or unrepresentable interval";
            return false;
        }
        files.emplace_back(row.path, std::ios::binary | std::ios::ate);
        auto &file = files.back();
        const auto length = file.tellg();
        if (!file || length < 0 || uint64_t(length) < row.offset + row.size) {
            reason = "file image source is missing or truncated: " + row.path;
            return false;
        }
        plans.push_back(std::move(*plan));
        destinations.emplace_back(row.address, row.address + row.size);
    }
    std::sort(destinations.begin(), destinations.end());
    for (size_t i = 1; i < destinations.size(); ++i)
        if (destinations[i].first < destinations[i - 1].second) {
            reason = "file image destinations overlap";
            return false;
        }
    std::array<uint8_t, 65536> buffer;
    for (bool install : {false, true}) {
        for (size_t i = 0; i < image.size(); ++i) {
            const auto &row = image[i];
            auto &file = files[i];
            file.clear();
            file.seekg(row.offset);
            mesh_hash::Sha256 hash;
            for (const auto &span : plans[i]) {
                uint64_t consumed = 0;
                while (consumed < span.size) {
                    const size_t count = std::min<uint64_t>(buffer.size(), span.size - consumed);
                    if (!file.read(reinterpret_cast<char *>(buffer.data()), count)) {
                        reason = "file image read failed: " + row.path;
                        return false;
                    }
                    hash.update(buffer.data(), count);
                    if (install && !write(span.owner, span.address + consumed, count, buffer.data())) {
                        reason = "file image backing write failed";
                        return false;
                    }
                    consumed += count;
                }
            }
            if (hash.digest() != row.sha256) {
                reason = "file image SHA-256 mismatch: " + row.path;
                return false;
            }
        }
    }
    return true;
}
}

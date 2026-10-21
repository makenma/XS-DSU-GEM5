#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <unistd.h>

#include "dev/ai_mesh/mesh_memory_image.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_json.hh"
#include "mem/axi/axi_target_adapter.hh"

namespace gem5::ai_mesh
{
class MemoryFileImageTest : public ::testing::Test
{
  protected:
    std::string path;
    std::vector<uint8_t> payload;

    void SetUp() override
    {
        char name[] = "/tmp/mesh-memory-image-XXXXXX";
        const int fd = mkstemp(name);
        ASSERT_GE(fd, 0);
        close(fd);
        path = name;
        payload.resize(200003);
        for (size_t i = 0; i < payload.size(); ++i)
            payload[i] = uint8_t(i * 31 + i / 257);
        std::ofstream out(path, std::ios::binary);
        out.write(reinterpret_cast<const char *>(payload.data()), payload.size());
        ASSERT_TRUE(out);
    }

    void TearDown() override { std::filesystem::remove(path); }

    MemoryFileSpan span(uint64_t address, uint64_t offset, uint64_t size)
    {
        mesh_hash::Sha256 hash;
        hash.update(payload.data() + offset, size);
        return {address, path, offset, size, hash.digest()};
    }
};

TEST_F(MemoryFileImageTest, ReadsBoundedFileSliceAcrossPhysicalOwners)
{
    const MemoryAddressMap map({{0x1000, 0x10000, 7}, {0x10000, 0x40000, 9}});
    axi::AxiSimpleMemory left({{0x1000, 0x10000, 7}});
    axi::AxiSimpleMemory right({{0x10000, 0x40000, 9}});
    size_t calls = 0;
    std::string reason;
    const auto row = span(0x1013, 19, payload.size() - 29);
    ASSERT_TRUE(installMemoryFiles({row}, map,
        [&](uint32_t owner, uint64_t address, size_t size, const uint8_t *data) {
            EXPECT_LE(size, 65536u);
            auto &memory = owner == 7 ? left : right;
            EXPECT_TRUE(memory.containsRange(address, size));
            for (size_t i = 0; i < size; ++i)
                memory.writeByte(address + i, data[i]);
            ++calls;
            return true;
        }, reason)) << reason;
    EXPECT_GT(calls, 3u);
    for (size_t i = 0; i < row.size; ++i) {
        const auto address = row.address + i;
        EXPECT_EQ((address < 0x10000 ? left : right).readByte(address), payload[19 + i]);
    }
    EXPECT_EQ(left.readByte(row.address - 1), 0);
    EXPECT_EQ(right.readByte(row.address + row.size), 0);
}

TEST_F(MemoryFileImageTest, InvalidImageDoesNotWriteAnyOwner)
{
    const MemoryAddressMap map({{0x1000, 0x2000, 7}, {0x3000, 0x4000, 9}});
    const auto good = span(0x1000, 3, 17);
    std::vector<MemoryFileSpan> invalid;
    invalid.push_back(span(0x1ff0, 0, 32));
    invalid.push_back(span(UINT64_MAX - 7, 0, 16));
    invalid.push_back(span(0x1008, 0, 16));
    auto bad = span(0x3000, 0, 16);
    bad.sha256[0] ^= 1;
    invalid.push_back(bad);
    bad = span(0x3000, 0, 16);
    bad.offset = payload.size() - 1;
    invalid.push_back(bad);
    bad.offset = UINT64_MAX;
    invalid.push_back(bad);
    bad = span(0x3000, 0, 16);
    bad.path += ".missing";
    invalid.push_back(bad);
    bad = span(0x3000, 0, 16);
    bad.size = 0;
    invalid.push_back(bad);
    for (const auto &row : invalid) {
        size_t writes = 0;
        std::string reason;
        EXPECT_FALSE(installMemoryFiles({good, row}, map,
            [&](uint32_t, uint64_t, size_t, const uint8_t *) { ++writes; return true; }, reason));
        EXPECT_EQ(writes, 0u);
        EXPECT_FALSE(reason.empty());
    }
}

TEST_F(MemoryFileImageTest, BackingFailureIsReported)
{
    std::string reason;
    size_t calls = 0;
    EXPECT_FALSE(installMemoryFiles({span(0x1000, 7, 23)},
        MemoryAddressMap({{0x1000, 0x2000, 7}}),
        [&](uint32_t, uint64_t, size_t, const uint8_t *) { ++calls; return false; }, reason));
    EXPECT_EQ(calls, 1u);
    EXPECT_FALSE(reason.empty());
}

TEST_F(MemoryFileImageTest, ManifestBindsProgramArchitectureAndRelativePayload)
{
    const auto row = span(0x1000, 3, 29);
    boost::json::object document{
        {"schema", "mesh-memory-file-image-v1"},
        {"program_semantic_sha256", std::string(64, 'a')},
        {"architecture_sha256", std::string(64, 'b')},
        {"spans", boost::json::array{boost::json::object{
            {"address", row.address}, {"offset", row.offset}, {"size", row.size},
            {"path", std::filesystem::path(path).filename().string()},
            {"sha256", mesh_hash::digestHex(row.sha256)}}}}};
    std::string reason;
    auto image = parseMemoryFileImage(canonicalMeshJson(document), "/tmp",
        std::string(64, 'a'), std::string(64, 'b'), reason);
    ASSERT_TRUE(image) << reason;
    ASSERT_EQ(image->size(), 1u);
    EXPECT_EQ(image->front().path, path);
    EXPECT_EQ(image->front().sha256, row.sha256);
    EXPECT_FALSE(parseMemoryFileImage(canonicalMeshJson(document), "/tmp",
        std::string(64, 'c'), std::string(64, 'b'), reason));
    EXPECT_FALSE(parseMemoryFileImage(canonicalMeshJson(document), "/tmp",
        std::string(64, 'a'), std::string(64, 'c'), reason));
    for (const auto &value : {boost::json::value(-1), boost::json::value(true),
                             boost::json::value(1.5), boost::json::value("12")}) {
        auto invalid = document;
        invalid["spans"].as_array()[0].as_object()["size"] = value;
        EXPECT_FALSE(parseMemoryFileImage(canonicalMeshJson(invalid), "/tmp",
            std::string(64, 'a'), std::string(64, 'b'), reason));
    }
    auto invalid = document;
    invalid["spans"].as_array()[0].as_object()["sha256"] = std::string(64, 'G');
    EXPECT_FALSE(parseMemoryFileImage(canonicalMeshJson(invalid), "/tmp",
        std::string(64, 'a'), std::string(64, 'b'), reason));
    invalid = document;
    invalid["unknown"] = 1;
    EXPECT_FALSE(parseMemoryFileImage(canonicalMeshJson(invalid), "/tmp",
        std::string(64, 'a'), std::string(64, 'b'), reason));
}
}

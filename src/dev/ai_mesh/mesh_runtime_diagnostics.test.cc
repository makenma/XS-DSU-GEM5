#include <gtest/gtest.h>

#include <unistd.h>

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

#include "dev/ai_mesh/mesh_runtime_diagnostics.hh"

using namespace gem5::ai_mesh;

namespace
{

std::string
uniqueBase(const std::string &name)
{
    const std::filesystem::path directory =
        std::filesystem::temp_directory_path() /
        ("mesh_diagnostics_test_" + name + "_" +
         std::to_string(::getpid()));
    std::filesystem::create_directories(directory);
    std::filesystem::remove(directory / "runtime_diagnostics.jsonl");
    return (directory / "program.mshb").string();
}

std::string
readFile(const std::string &path)
{
    std::ifstream in(path, std::ios::binary);
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
}

RuntimeDiagnostic
recordWith(const std::string &value)
{
    RuntimeDiagnostic record;
    record.code = mesh_diagnostics::DiagnosticCode::E_ABI_MAGIC;
    record.stage = DiagnosticStage::Load;
    record.context = {{"program_file", value}};
    return record;
}

} // namespace

TEST(MeshRuntimeDiagnosticsTest, EscapesEveryControlCharacterAndKeepsUtf8)
{
    std::string value;
    for (char item = 0; item < 0x20; item++)
        value.push_back(item);
    value += "\"\\";
    value += "é";
    const std::string base = uniqueBase("control");
    MeshRuntimeDiagnostics sink(base);
    ASSERT_TRUE(sink.write(recordWith(value))) << sink.failure();
    ASSERT_TRUE(sink.write(recordWith("second"))) << sink.failure();

    const std::string content = readFile(sink.path());
    ASSERT_FALSE(content.empty());
    EXPECT_EQ(content.back(), '\n');
    for (const char item : content) {
        const auto byte = static_cast<unsigned char>(item);
        EXPECT_TRUE(byte == '\n' || byte >= 0x20) << "raw control byte in JSONL";
    }
    EXPECT_NE(content.find("\\u0000"), std::string::npos);
    EXPECT_NE(content.find("\\u001f"), std::string::npos);
    EXPECT_NE(content.find("\\b"), std::string::npos);
    EXPECT_NE(content.find("\\f"), std::string::npos);
    EXPECT_NE(content.find("\\n"), std::string::npos);
    EXPECT_NE(content.find("\\r"), std::string::npos);
    EXPECT_NE(content.find("\\t"), std::string::npos);
    EXPECT_NE(content.find("\\\"\\\\"), std::string::npos);
    EXPECT_NE(content.find("é"), std::string::npos);
    EXPECT_NE(content.find("\"severity\": \"error\""), std::string::npos);
    EXPECT_EQ(std::count(content.begin(), content.end(), '\n'), 2);
}

TEST(MeshRuntimeDiagnosticsTest, WritesThroughOnEveryCallAndCarriesCatalogFields)
{
    const std::string base = uniqueBase("flush");
    MeshRuntimeDiagnostics sink(base);
    ASSERT_TRUE(sink.write(recordWith("/tmp/program.mshb"))) << sink.failure();
    const std::string content = readFile(sink.path());
    EXPECT_NE(content.find("\"code\": \"E_ABI_MAGIC\""), std::string::npos);
    EXPECT_NE(content.find("\"stage\": \"load\""), std::string::npos);
    EXPECT_NE(content.find("binary magic is invalid"), std::string::npos);
    EXPECT_NE(content.find("\"program_file\": \"/tmp/program.mshb\""),
              std::string::npos);
    EXPECT_TRUE(sink.failure().empty());
}

TEST(MeshRuntimeDiagnosticsTest, UnwritableTargetFailsClosed)
{
    MeshRuntimeDiagnostics sink(
        "/mesh-diagnostics-missing-directory/program.mshb");
    EXPECT_FALSE(sink.write(recordWith("/tmp/program.mshb")));
    EXPECT_FALSE(sink.failure().empty());
}

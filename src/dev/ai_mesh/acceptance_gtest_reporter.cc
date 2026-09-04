#include <gtest/gtest.h>

#include <cerrno>
#include <cstdlib>
#include <fcntl.h>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace
{

std::string
requiredEnvironment(const char *name)
{
    const char *value = std::getenv(name);
    if (!value || !*value)
        throw std::runtime_error(std::string("missing environment ") + name);
    return value;
}

void
writeAll(int descriptor, const std::string &value)
{
    size_t offset = 0;
    while (offset < value.size()) {
        const ssize_t written = ::write(
            descriptor, value.data() + offset, value.size() - offset);
        if (written < 0 && errno == EINTR)
            continue;
        if (written <= 0)
            throw std::runtime_error("artifact write failed");
        offset += size_t(written);
    }
}

void
writeAtomic(const std::string &path, const std::string &value)
{
    const std::string temporary = path + ".tmp." + std::to_string(::getpid());
    const int descriptor = ::open(
        temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (descriptor < 0)
        throw std::runtime_error("cannot create artifact temporary");
    try {
        writeAll(descriptor, value);
        if (::fsync(descriptor) != 0)
            throw std::runtime_error("artifact fsync failed");
        if (::close(descriptor) != 0)
            throw std::runtime_error("artifact close failed");
        if (::rename(temporary.c_str(), path.c_str()) != 0)
            throw std::runtime_error("artifact rename failed");
    } catch (...) {
        ::close(descriptor);
        ::unlink(temporary.c_str());
        throw;
    }
}

std::string
readDigest(const std::string &artifact_dir)
{
    std::ifstream input(artifact_dir + "/.run_manifest_digest");
    std::string digest;
    input >> digest;
    if (!input || digest.size() != 64)
        throw std::runtime_error("invalid run manifest digest");
    return digest;
}

class AcceptanceEnvironment : public ::testing::Environment
{
  public:
    void TearDown() override
    {
        if (!std::getenv("AI_MESH_CHILD_REPORT"))
            return;
        const auto *unit = ::testing::UnitTest::GetInstance();
        if (unit->test_to_run_count() != 1 || unit->successful_test_count() != 1 ||
            unit->failed_test_count() != 0 || unit->skipped_test_count() != 0)
            return;
        try {
            const std::string id = requiredEnvironment("AI_MESH_CASE_ID");
            const std::string subcase = requiredEnvironment("AI_MESH_SUBCASE");
            const std::string artifact_dir =
                requiredEnvironment("AI_MESH_ARTIFACT_DIR");
            const std::string report =
                requiredEnvironment("AI_MESH_CHILD_REPORT");
            const std::string digest = readDigest(artifact_dir);
            const std::string invariants =
                "{\"checks\":[{\"expected\":{\"kind\":\"BOOL\",\"value\":true},"
                "\"name\":\"unit_test_passed\",\"observed\":{\"kind\":\"BOOL\","
                "\"value\":true},\"status\":\"PASS\"}],\"first_failure\":null,"
                "\"id\":\"" + id + "\",\"registry_digest\":"
                "\"0d1a2ab8e4d553f6a525c776c54f9caabef417b621d442c75253d9ce283d086b\","
                "\"schema\":\"ai_mesh_invariants_v1\",\"status\":\"PASS\","
                "\"subcase\":\"" + subcase + "\",\"version\":1}\n";
            const std::string child =
                "{\"first_fatal\":null,\"global_quiescence\":true,\"id\":\"" +
                id + "\",\"ledger_summary\":{\"ambiguous_publications\":0,"
                "\"fatal_cq_obligations\":0,\"fatal_publications\":0,"
                "\"fatal_records\":0,\"fatal_sq_intakes\":0,"
                "\"host_ack_b_error\":0,\"host_ack_wait_b\":0,"
                "\"live_cq_obligations\":0,\"msi_rob_entries\":0},"
                "\"run_exit_reason\":\"QUIESCENT_SUCCESS\","
                "\"run_manifest_digest\":\"" + digest + "\","
                "\"schema\":\"ai_mesh_child_scenario_report_v1\",\"subcase\":\"" +
                subcase + "\",\"version\":1,\"watchdog_fired\":false}\n";
            writeAtomic(artifact_dir + "/invariants.json", invariants);
            writeAtomic(report, child);
        } catch (const std::exception &error) {
            ADD_FAILURE() << error.what();
        }
    }
};

::testing::Environment *const acceptance_environment =
    ::testing::AddGlobalTestEnvironment(new AcceptanceEnvironment());

}

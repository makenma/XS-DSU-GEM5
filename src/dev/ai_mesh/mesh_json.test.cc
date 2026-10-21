#include <gtest/gtest.h>
#include <limits>
#include <sstream>
#include <stdexcept>
#include "dev/ai_mesh/mesh_json.hh"

using namespace gem5::ai_mesh;

TEST(MeshJson, CounterSerializationPreservesLargeIntegerDigits)
{
    for (uint64_t value : {uint64_t(0), uint64_t(1000001),
            uint64_t(142846137), uint64_t(9007199254740991)}) {
        std::ostringstream output;
        output << meshJsonCounter(static_cast<double>(value));
        EXPECT_EQ(output.str(), std::to_string(value));
        std::string reason;
        const auto parsed = parseMeshJson(output.str(), reason);
        ASSERT_TRUE(parsed) << reason;
        EXPECT_TRUE(parsed->is_int64() || parsed->is_uint64());
    }
    for (double value : {-1.0, 0.5, 9007199254740992.0,
            std::numeric_limits<double>::infinity(),
            std::numeric_limits<double>::quiet_NaN()})
        EXPECT_THROW(meshJsonCounter(value), std::invalid_argument);
}

TEST(MeshJson, RejectsDuplicateKeysAtEveryNestingLevel)
{
    for (const std::string text : {"{\"a\":1,\"a\":2}",
            "{\"a\":[{\"b\":1,\"b\":2}]}", "{} trailing", "{\"x\":NaN}"}) {
        std::string reason;
        EXPECT_FALSE(parseMeshJson(text, reason));
        EXPECT_FALSE(reason.empty());
    }
}

TEST(MeshJson, CanonicalizesNestedObjectsWithoutChangingArrayOrder)
{
    std::string reason;
    auto value = parseMeshJson(" {\"z\": [{\"d\": 4, \"a\": 1}, 2], \"a\": true} \n", reason);
    ASSERT_TRUE(value) << reason;
    EXPECT_EQ(canonicalMeshJson(*value), "{\"a\":true,\"z\":[{\"a\":1,\"d\":4},2]}");
    EXPECT_TRUE(parseMeshJson("[{\"a\":1},{\"a\":2}]", reason));
}

#include <gtest/gtest.h>

#include <cstdint>
#include <fstream>
#include <iterator>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_workload_manager.hh"

namespace
{

using gem5::ai_mesh::AgentPlanImage;
using gem5::ai_mesh::AgentWorkloadFactsSink;
using gem5::ai_mesh::AgentWorkloadManager;
using gem5::ai_mesh::AgentWorkloadManagerConfig;

namespace agent_abi = gem5::ai_mesh::agent_abi;

struct Fact
{
    std::string kind;
    std::string object;
    uint64_t requestId = 0;
    std::string status;
};

struct Facts
{
    std::vector<Fact> semantics;
    std::map<std::string, uint64_t> metrics;

    AgentWorkloadFactsSink sink()
    {
        return AgentWorkloadFactsSink{
            [this](const std::string &kind, const std::string &object,
                   uint64_t requestId, const std::string &status) {
                semantics.push_back(Fact{kind, object, requestId, status});
            },
            [this](const std::string &name, uint64_t value) {
                metrics[name] = value;
            }};
    }

    std::vector<Fact> ofKind(const std::string &kind) const
    {
        std::vector<Fact> result;
        for (const Fact &fact : semantics)
            if (fact.kind == kind)
                result.push_back(fact);
        return result;
    }

    std::vector<std::string> objectsOfKind(const std::string &kind) const
    {
        std::vector<std::string> result;
        for (const Fact &fact : ofKind(kind))
            result.push_back(fact.object);
        return result;
    }

    std::vector<std::string> statusesOfKind(const std::string &kind) const
    {
        std::vector<std::string> result;
        for (const Fact &fact : ofKind(kind))
            result.push_back(fact.status);
        return result;
    }
};

std::optional<AgentPlanImage>
loadImage(const std::string &name)
{
    std::ifstream stream("tests/gem5/ai_mesh/fixtures/gate4/" + name,
                         std::ios::binary);
    const std::vector<uint8_t> bytes(
        (std::istreambuf_iterator<char>(stream)),
        std::istreambuf_iterator<char>());
    return AgentPlanImage::parse(bytes.data(), bytes.size());
}

AgentWorkloadManagerConfig
defaultConfig()
{
    AgentWorkloadManagerConfig config;
    config.clockTicksPerSecond = 1000000000;
    return config;
}

uint64_t
runToCompletion(AgentWorkloadManager &manager, uint64_t startTick)
{
    uint64_t now = startTick;
    while (!manager.exhausted(0)) {
        const auto wake = manager.nextWakeTick();
        now = wake && *wake > now ? *wake : now + 1;
        manager.onEdge(now);
        for (const auto &intent : manager.nextBatch({}, 0, 4))
            manager.onGenerateTerminal(intent.requestId, true, now);
    }
    return now;
}

constexpr uint64_t kThinkTick = 1000000;
constexpr uint64_t kCompileAdmitTick = kThinkTick + 501;
constexpr uint64_t kCompileStartTick = kThinkTick + 502;
constexpr uint64_t kCompileNominalTicks = 1000000;
constexpr uint64_t kTestNominalTicks = 2000000;
constexpr uint64_t kParseNominalTicks = 500000;
constexpr uint64_t kUserOneThinkTick = 3000000;
constexpr uint64_t kUserOneRequest = (1ull << 32) | 1;

}

TEST(AgentWorkloadManager, FirstPassWalkCommitStageByStage)
{
    auto image = loadImage("agent_plan_image_su_first_pass.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    EXPECT_FALSE(manager.exhausted(0));
    manager.onEdge(kThinkTick - 1);
    EXPECT_TRUE(manager.nextBatch({}, 0, 4).empty());
    manager.onEdge(kThinkTick);
    const auto intents = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(intents.size(), 1u);
    EXPECT_EQ(intents[0].requestId, 1u);
    EXPECT_TRUE(intents[0].planDriven);
    EXPECT_TRUE(manager.nextBatch({}, 0, 4).empty());
    EXPECT_EQ(manager.nextWakeTick(), std::nullopt);
    EXPECT_EQ(facts.objectsOfKind("TASK_THINK_READY"),
              (std::vector<std::string>{"TASK"}));

    manager.onGenerateTerminal(1, true, kThinkTick + 500);
    EXPECT_NE(manager.nextWakeTick(), std::nullopt);
    manager.onEdge(kCompileAdmitTick);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_ENQUEUE"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_TRUE(facts.ofKind("HOST_STAGE_START").empty());
    manager.onEdge(kCompileStartTick);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(*manager.nextWakeTick(),
              kCompileStartTick + kCompileNominalTicks);

    manager.onEdge(kCompileStartTick + kCompileNominalTicks - 1);
    EXPECT_TRUE(facts.ofKind("HOST_STAGE_DONE").empty());
    const uint64_t compileDone =
        kCompileStartTick + kCompileNominalTicks;
    manager.onEdge(compileDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.statusesOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"SUCCESS"}));
    const uint64_t testStart = compileDone + 1;
    const uint64_t testDone = testStart + kTestNominalTicks;
    manager.onEdge(testStart);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE", "TEST"}));
    EXPECT_EQ(*manager.nextWakeTick(), testDone);

    manager.onEdge(testDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE", "TEST"}));
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"BUSINESS_DONE"}));
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_TRUE(manager.allTasksTerminal());
    EXPECT_EQ(manager.completedTasks(), 1u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(manager.nextWakeTick(), std::nullopt);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
    EXPECT_EQ(facts.metrics.at("raw_log_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("scanned_log_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("excerpt_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("excerpt_tokens"), 0u);
    EXPECT_EQ(facts.metrics.at("repair_input_excerpt_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("npu_fabric_raw_log_bytes"), 0u);
}

TEST(AgentWorkloadManager, ActualDoneTakesMaxOfNominalAndLocalIo)
{
    struct Case
    {
        const char *name;
        bool ioEnabled;
        uint64_t fixedNs;
        uint64_t expectedDuration;
    };
    const Case cases[] = {
        {"nominal_first", true, 1000, kCompileNominalTicks},
        {"io_first", true, 3000080, 3000092},
        {"equal", true, 999988, kCompileNominalTicks},
        {"io_disabled", false, 1000, kCompileNominalTicks},
    };
    for (const Case &testCase : cases) {
        auto image = loadImage("agent_plan_image_su_first_pass.bin");
        ASSERT_TRUE(image.has_value());
        Facts facts;
        AgentWorkloadManagerConfig config = defaultConfig();
        config.hostLocalIoEnabled = testCase.ioEnabled;
        config.hostLocalIoFixedNs = testCase.fixedNs;
        AgentWorkloadManager manager(std::move(*image), config,
                                     facts.sink());
        manager.onEdge(kThinkTick);
        const auto intents = manager.nextBatch({}, 0, 4);
        ASSERT_EQ(intents.size(), 1u);
        manager.onGenerateTerminal(intents[0].requestId, true,
                                   kThinkTick + 500);
        manager.onEdge(kCompileAdmitTick);
        manager.onEdge(kCompileStartTick);
        ASSERT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
                  (std::vector<std::string>{"COMPILE"})) << testCase.name;
        EXPECT_EQ(*manager.nextWakeTick(),
                  kCompileStartTick + testCase.expectedDuration)
            << testCase.name;
        manager.onEdge(kCompileStartTick + testCase.expectedDuration - 1);
        EXPECT_TRUE(facts.ofKind("HOST_STAGE_DONE").empty())
            << testCase.name;
        manager.onEdge(kCompileStartTick + testCase.expectedDuration);
        EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
                  (std::vector<std::string>{"COMPILE"})) << testCase.name;
    }
}

TEST(AgentWorkloadManager, CompileFailRepairParsesBeforeNextGenerate)
{
    auto image = loadImage("agent_plan_image_su_compile_repair.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    manager.onEdge(kThinkTick);
    const auto first = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(first.size(), 1u);
    EXPECT_EQ(first[0].requestId, 1u);
    manager.onGenerateTerminal(1, true, kThinkTick + 500);
    manager.onEdge(kCompileAdmitTick);
    manager.onEdge(kCompileStartTick);
    const uint64_t compileDone =
        kCompileStartTick + kCompileNominalTicks;
    manager.onEdge(compileDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.statusesOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"FAIL"}));
    EXPECT_EQ(facts.metrics.at("raw_log_bytes"), 32768u);
    EXPECT_TRUE(manager.nextBatch({}, 0, 4).empty());
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    const uint64_t parseStart = compileDone + 1;
    const uint64_t parseDone = parseStart + kParseNominalTicks;
    manager.onEdge(parseStart);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE", "LOG_PARSE"}));
    EXPECT_EQ(*manager.nextWakeTick(), parseDone);

    manager.onEdge(parseDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE", "LOG_PARSE"}));
    EXPECT_EQ(facts.metrics.at("scanned_log_bytes"), 32768u);
    EXPECT_EQ(facts.metrics.at("excerpt_bytes"), 4096u);
    EXPECT_EQ(facts.metrics.at("excerpt_tokens"), 512u);
    EXPECT_EQ(facts.metrics.at("repair_input_excerpt_bytes"), 4096u);
    const auto repair = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(repair.size(), 1u);
    EXPECT_EQ(repair[0].requestId, 2u);
    EXPECT_EQ(repair[0].repairRound, 1u);

    manager.onGenerateTerminal(2, true, parseDone);
    const uint64_t repairCompileAdmit = parseDone + 1;
    const uint64_t repairCompileStart = parseDone + 2;
    manager.onEdge(repairCompileAdmit);
    manager.onEdge(repairCompileStart);
    const uint64_t repairCompileDone =
        repairCompileStart + kCompileNominalTicks;
    manager.onEdge(repairCompileDone);
    const uint64_t repairTestStart = repairCompileDone + 1;
    const uint64_t repairTestDone = repairTestStart + kTestNominalTicks;
    manager.onEdge(repairTestStart);
    manager.onEdge(repairTestDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{
                  "COMPILE", "LOG_PARSE", "COMPILE", "TEST"}));
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"BUSINESS_DONE"}));
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.completedTasks(), 1u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, TestFailRepairReturnsThroughNpu)
{
    auto image = loadImage("agent_plan_image_su_test_repair.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    runToCompletion(manager, 0);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{
                  "COMPILE", "TEST", "LOG_PARSE", "COMPILE", "TEST"}));
    EXPECT_EQ(facts.statusesOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{
                  "SUCCESS", "FAIL", "NONE", "SUCCESS", "SUCCESS"}));
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"BUSINESS_DONE"}));
    EXPECT_EQ(manager.completedTasks(), 1u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(facts.metrics.at("raw_log_bytes"), 32768u);
    EXPECT_EQ(facts.metrics.at("scanned_log_bytes"), 32768u);
    EXPECT_EQ(facts.metrics.at("excerpt_bytes"), 4096u);
    EXPECT_EQ(facts.metrics.at("repair_input_excerpt_bytes"), 4096u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, RepairLimitFailsWithoutParsing)
{
    auto image = loadImage("agent_plan_image_su_repair_limit.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    runToCompletion(manager, 0);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_ENQUEUE"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.statusesOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"FAIL"}));
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"BUSINESS_FAILED"}));
    EXPECT_EQ(manager.completedTasks(), 0u);
    EXPECT_EQ(manager.failedTasks(), 1u);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(facts.metrics.at("raw_log_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("scanned_log_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("excerpt_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("repair_input_excerpt_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, GenerateFailureFailsTaskWithoutHostStages)
{
    auto image = loadImage("agent_plan_image_su_first_pass.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    manager.onEdge(kThinkTick);
    const auto intents = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(intents.size(), 1u);
    manager.onGenerateTerminal(intents[0].requestId, false,
                               kThinkTick + 200);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.failedTasks(), 1u);
    EXPECT_EQ(manager.completedTasks(), 0u);
    EXPECT_TRUE(facts.ofKind("HOST_STAGE_ENQUEUE").empty());
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"BUSINESS_FAILED"}));
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, AgingReservationStartsTargetAfterRelease)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.hostComputeTokens = 2;
    config.hostCompileSlots = 1;
    config.hostTestSlots = 1;
    config.hostLogParseSlots = 1;
    config.hostAgingThresholdNs = 500;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    manager.onEdge(kThinkTick);
    const auto first = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(first.size(), 1u);
    EXPECT_EQ(first[0].requestId, 1u);
    manager.onGenerateTerminal(1, true, kThinkTick);
    manager.onEdge(kThinkTick + 1);
    manager.onEdge(kThinkTick + 2);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));

    manager.onEdge(kUserOneThinkTick);
    const auto second = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(second.size(), 1u);
    EXPECT_EQ(second[0].requestId, kUserOneRequest);
    manager.onGenerateTerminal(kUserOneRequest, true, kUserOneThinkTick);
    manager.onEdge(kUserOneThinkTick + 1);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_FALSE(manager.hostResources().hasAgingReservation());
    EXPECT_EQ(*manager.nextWakeTick(), kUserOneThinkTick + 501);
    manager.onEdge(kUserOneThinkTick + 501);
    EXPECT_TRUE(manager.hostResources().hasAgingReservation());
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.metrics.at("aging_reservations"), 1u);

    const uint64_t firstCompileDone = kThinkTick + 2 + 100000000;
    manager.onEdge(firstCompileDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE", "COMPILE"}));
    EXPECT_FALSE(manager.hostResources().hasAgingReservation());

    runToCompletion(manager, firstCompileDone);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.completedTasks(), 3u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, QueueFullWaiterAdmittedOnRelease)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.hostServiceQueueDepth = 1;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    manager.onEdge(kThinkTick);
    const auto first = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(first.size(), 1u);

    manager.onEdge(kUserOneThinkTick);
    const auto second = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(second.size(), 1u);
    EXPECT_EQ(second[0].requestId, kUserOneRequest);
    manager.onGenerateTerminal(first[0].requestId, true, kUserOneThinkTick);
    manager.onGenerateTerminal(kUserOneRequest, true, kUserOneThinkTick);

    manager.onEdge(kUserOneThinkTick + 1);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_ENQUEUE"),
              (std::vector<std::string>{"COMPILE", "COMPILE"}));
    EXPECT_TRUE(facts.ofKind("HOST_STAGE_START").empty());
    EXPECT_EQ(manager.hostResources().queueLength(0), 1u);
    EXPECT_EQ(manager.hostResources().waitingCount(), 1u);

    manager.onEdge(kUserOneThinkTick + 2);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE"}));
    EXPECT_EQ(manager.hostResources().waitingCount(), 1u);
    const uint64_t firstCompileDone =
        kUserOneThinkTick + 2 + 100000000u;
    EXPECT_EQ(*manager.nextWakeTick(), firstCompileDone);

    manager.onEdge(firstCompileDone);
    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_START"),
              (std::vector<std::string>{"COMPILE", "COMPILE"}));
    EXPECT_EQ(manager.hostResources().waitingCount(), 0u);

    runToCompletion(manager, firstCompileDone);
    EXPECT_EQ(manager.completedTasks(), 3u);
    EXPECT_EQ(manager.failedTasks(), 0u);
}

TEST(AgentWorkloadManager, StopAfterCompletedTasksSuppressesNextTask)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.stopAfterCompletedTasks = 2;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    std::vector<uint64_t> handed;
    uint64_t now = 0;
    while (!manager.exhausted(0)) {
        const auto wake = manager.nextWakeTick();
        now = wake && *wake > now ? *wake : now + 1;
        manager.onEdge(now);
        for (const auto &intent : manager.nextBatch({}, 0, 4)) {
            handed.push_back(intent.requestId);
            manager.onGenerateTerminal(intent.requestId, true, now);
        }
    }
    EXPECT_EQ(handed,
              (std::vector<uint64_t>{1, kUserOneRequest, 2}));
    EXPECT_EQ(manager.completedTasks(), 2u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_TRUE(manager.allTasksTerminal());
    EXPECT_EQ(facts.objectsOfKind("TASK_THINK_READY").size(), 2u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, TaskArrivalFollowsPreviousLifecycleFinal)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());

    manager.onEdge(kThinkTick - 1);
    EXPECT_TRUE(facts.ofKind("TASK_THINK_READY").empty());
    manager.onEdge(kThinkTick);
    EXPECT_EQ(facts.ofKind("TASK_THINK_READY").size(), 1u);
    EXPECT_EQ(manager.nextBatch({}, 0, 4).size(), 1u);
    const auto wake = manager.nextWakeTick();
    ASSERT_TRUE(wake.has_value());
    EXPECT_EQ(*wake, kUserOneThinkTick);
}

TEST(AgentWorkloadManager, HostFaultObjectProduceOnCompileFailDrainsToInfraFailed)
{
    auto image = loadImage("agent_plan_image_su_compile_repair.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.hostFaultSite = agent_abi::HostLocalFaultSiteV1::OBJECT_PRODUCE;
    config.hostFaultTask = 0;
    config.hostFaultRound = 0;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    manager.onEdge(kThinkTick);
    const auto intents = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(intents.size(), 1u);
    manager.onGenerateTerminal(intents[0].requestId, true, kThinkTick + 500);
    manager.onEdge(kCompileAdmitTick);
    manager.onEdge(kCompileStartTick);
    manager.onEdge(kCompileStartTick + kCompileNominalTicks);

    EXPECT_EQ(facts.objectsOfKind("HOST_STAGE_DONE"),
              (std::vector<std::string>{"COMPILE"}));
    const auto faults = facts.ofKind("HOST_FAULT");
    ASSERT_EQ(faults.size(), 1u);
    EXPECT_EQ(faults[0].object, "OBJECT_PRODUCE");
    EXPECT_EQ(faults[0].status, "E_HOST_LOCAL_OBJECT");
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"INFRA_FAILED"}));
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.completedTasks(), 0u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(manager.infraFailedTasks(), 1u);
    EXPECT_EQ(facts.metrics.at("infra_failed_tasks"), 1u);
    EXPECT_EQ(facts.metrics.at("raw_log_bytes"), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
    EXPECT_EQ(facts.ofKind("HOST_STAGE_ENQUEUE").size(), 1u);
    EXPECT_EQ(manager.nextWakeTick(), std::nullopt);
}

TEST(AgentWorkloadManager, HostFaultObjectReadOnCompileAcquireDrainsToInfraFailed)
{
    auto image = loadImage("agent_plan_image_su_first_pass.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.hostFaultSite = agent_abi::HostLocalFaultSiteV1::OBJECT_READ;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    manager.onEdge(kThinkTick);
    const auto intents = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(intents.size(), 1u);
    manager.onGenerateTerminal(intents[0].requestId, true, kThinkTick + 500);
    manager.onEdge(kCompileAdmitTick);
    manager.onEdge(kCompileStartTick);

    EXPECT_TRUE(facts.ofKind("HOST_STAGE_START").empty());
    const auto faults = facts.ofKind("HOST_FAULT");
    ASSERT_EQ(faults.size(), 1u);
    EXPECT_EQ(faults[0].object, "OBJECT_READ");
    EXPECT_EQ(faults[0].status, "E_HOST_LOCAL_OBJECT");
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{"INFRA_FAILED"}));
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.infraFailedTasks(), 1u);
    EXPECT_EQ(manager.completedTasks(), 0u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
    EXPECT_EQ(manager.hostResources().availableTokens(), 24u);
    EXPECT_EQ(manager.nextWakeTick(), std::nullopt);
}

TEST(AgentWorkloadManager, HostFaultLeavesOtherTasksUnaffected)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.hostFaultSite = agent_abi::HostLocalFaultSiteV1::OBJECT_READ;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    runToCompletion(manager, 0);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.infraFailedTasks(), 1u);
    EXPECT_EQ(manager.completedTasks(), 2u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
    EXPECT_EQ(facts.ofKind("HOST_FAULT").size(), 1u);
    EXPECT_EQ(facts.statusesOfKind("TASK_TERMINAL"),
              (std::vector<std::string>{
                  "INFRA_FAILED", "BUSINESS_DONE", "BUSINESS_DONE"}));
}

TEST(AgentWorkloadManager, StopAcceptingAtTickSuppressesLaterThinks)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.stopAcceptingEnabled = true;
    config.stopAcceptingAtTick = kThinkTick + 1;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    runToCompletion(manager, 0);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.completedTasks(), 1u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(manager.infraFailedTasks(), 0u);
    EXPECT_EQ(facts.ofKind("TASK_THINK_READY").size(), 1u);
    EXPECT_EQ(facts.ofKind("TASK_SUPPRESSED").size(), 2u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, StopAcceptingAtTickZeroSuppressesEveryThink)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.stopAcceptingEnabled = true;
    config.stopAcceptingAtTick = 0;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    runToCompletion(manager, 0);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(manager.completedTasks(), 0u);
    EXPECT_EQ(manager.failedTasks(), 0u);
    EXPECT_EQ(manager.infraFailedTasks(), 0u);
    EXPECT_EQ(facts.ofKind("TASK_THINK_READY").size(), 0u);
    EXPECT_EQ(facts.ofKind("TASK_SUPPRESSED").size(), 3u);
    EXPECT_EQ(manager.nextBatch({}, 0, 4).size(), 0u);
    EXPECT_EQ(facts.metrics.at("agent_live_objects"), 0u);
}

TEST(AgentWorkloadManager, StopAcceptingDisabledKeepsTickZeroInactive)
{
    auto image = loadImage("agent_plan_image_two_user.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManagerConfig config = defaultConfig();
    config.stopAcceptingAtTick = 0;
    AgentWorkloadManager manager(std::move(*image), config, facts.sink());

    runToCompletion(manager, 0);
    EXPECT_TRUE(manager.exhausted(0));
    EXPECT_EQ(facts.ofKind("TASK_SUPPRESSED").size(), 0u);
    EXPECT_EQ(manager.completedTasks(), 3u);
}

TEST(AgentWorkloadManager, MaterializedControlIntentIsOfferedByNextBatch)
{
    auto image = loadImage("agent_plan_image_ctrl_cancel_live.bin");
    ASSERT_TRUE(image.has_value());
    Facts facts;
    AgentWorkloadManager manager(std::move(*image), defaultConfig(),
                                 facts.sink());
    EXPECT_FALSE(manager.controlIntentsPending());
    manager.materializeControlAction(0);
    EXPECT_TRUE(manager.controlIntentsPending());
    manager.onEdge(kThinkTick);
    const auto intents = manager.nextBatch({}, 0, 4);
    ASSERT_EQ(intents.size(), 1u);
    EXPECT_EQ(intents[0].commandKind, 2u);
    EXPECT_TRUE(intents[0].planDriven);
    EXPECT_EQ(intents[0].requestId, (1ull << 32) | 2);
    EXPECT_EQ(intents[0].completionCookie, (1ull << 32) | 2);
    EXPECT_EQ(intents[0].targetRequestId, (1ull << 32) | 1);
    EXPECT_EQ(intents[0].userId, 1u);
    EXPECT_NE(intents[0].parameterAddress, 0u);
    EXPECT_FALSE(manager.controlIntentsPending());
    uint32_t user = 0, task = 0;
    uint16_t round = 0;
    EXPECT_TRUE(manager.anchorForRequest((1ull << 32) | 1, user, task, round));
    EXPECT_EQ(user, 1u);
    EXPECT_EQ(task, 0u);
    EXPECT_EQ(round, 0u);
    EXPECT_FALSE(manager.anchorForRequest((1ull << 32) | 2, user, task, round));
}

TEST(AgentWorkloadManager, ControlAnchorSuppressedOnlyAfterRunCutoff)
{
    auto liveImage = loadImage("agent_plan_image_ctrl_cancel_live.bin");
    ASSERT_TRUE(liveImage.has_value());
    Facts liveFacts;
    AgentWorkloadManager live(std::move(*liveImage), defaultConfig(),
                              liveFacts.sink());
    EXPECT_FALSE(live.controlAnchorSuppressed(0));
    runToCompletion(live, 0);
    EXPECT_TRUE(live.exhausted(0));
    EXPECT_FALSE(live.controlAnchorSuppressed(0));

    auto cutImage = loadImage("agent_plan_image_ctrl_cancel_live.bin");
    ASSERT_TRUE(cutImage.has_value());
    Facts cutFacts;
    AgentWorkloadManagerConfig cutConfig = defaultConfig();
    cutConfig.stopAcceptingEnabled = true;
    cutConfig.stopAcceptingAtTick = kThinkTick + 1;
    AgentWorkloadManager cut(std::move(*cutImage), cutConfig,
                             cutFacts.sink());
    runToCompletion(cut, 0);
    EXPECT_TRUE(cut.exhausted(0));
    EXPECT_EQ(cutFacts.ofKind("TASK_SUPPRESSED").size(), 2u);
    EXPECT_TRUE(cut.controlAnchorSuppressed(0));
}

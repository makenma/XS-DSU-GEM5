#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/agent_protocol_abi.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"
#include "dev/ai_mesh/mesh_kv_manager_test_support.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

using namespace mesh_kv_test;

constexpr uint32_t kBytesPerToken = 64;
constexpr uint64_t kSlotBytes = 4096;
constexpr uint32_t kKindAdmission = 0;
constexpr uint32_t kKindRelease = 1;
constexpr uint32_t kKindOwnerTerminal = 2;
constexpr uint32_t kKindLaterFault = 3;
constexpr uint32_t kKindCoreStart = 4;
constexpr uint32_t kKindAppendArm = 5;
constexpr uint32_t kKindAppendTerminal = 6;
constexpr uint32_t kKindDmaAccept = 7;
constexpr uint32_t kKindDmaTerminal = 8;
constexpr uint32_t kKindFault = 9;
constexpr uint32_t kKindViewArm = 10;
constexpr size_t kEventWords = 12;
constexpr size_t kRecordWords = 40;

// ----------------------------------------------------------- trace reader

struct Cursor
{
    const std::vector<uint8_t> &data;
    size_t offset = 0;

    uint32_t u32()
    {
        uint32_t value = 0;
        for (size_t byte = 0; byte < 4; ++byte)
            value |= uint32_t(data[offset + byte]) << (8 * byte);
        offset += 4;
        return value;
    }

    uint64_t u64()
    {
        uint64_t value = 0;
        for (size_t byte = 0; byte < 8; ++byte)
            value |= uint64_t(data[offset + byte]) << (8 * byte);
        offset += 8;
        return value;
    }

    std::string text()
    {
        const uint32_t length = u32();
        std::string value(data.begin() + offset,
                          data.begin() + offset + length);
        offset += length;
        return value;
    }
};

struct TraceEdge
{
    std::vector<std::vector<uint64_t>> events;
    std::vector<uint64_t> scalars;
    std::vector<std::string> names;
};

struct TraceScenario
{
    std::string name;
    KvGeometry geometry;
    KvCapacity capacity;
    KvPersistentState initial;
    std::vector<TraceEdge> edges;
    uint32_t fatal_edges = 0;
};

std::array<uint8_t, 32> readDigest(Cursor &cursor)
{
    std::array<uint8_t, 32> out = {};
    for (size_t index = 0; index < out.size(); ++index)
        out[index] = cursor.data[cursor.offset + index];
    cursor.offset += 32;
    return out;
}

KvRecord readRecord(Cursor &cursor)
{
    std::vector<uint64_t> words;
    for (size_t index = 0; index < kRecordWords; ++index)
        words.push_back(cursor.u64());
    KvRecord record;
    record.session_id = words[0];
    record.kv_handle = words[1];
    record.generation = static_cast<uint32_t>(words[2]);
    record.state = static_cast<KvState>(words[3]);
    if (words[4])
        record.slot_id = static_cast<uint32_t>(words[5]);
    record.cached_tokens = static_cast<uint32_t>(words[6]);
    record.valid_bytes = words[7];
    if (words[8]) {
        std::array<uint8_t, 32> digest = {};
        for (size_t word = 0; word < 4; ++word)
            for (size_t byte = 0; byte < 8; ++byte)
                digest[word * 8 + byte] =
                    uint8_t(words[29 + word] >> (8 * byte));
        record.content_digest = digest;
    }
    record.view_epoch = words[9];
    record.last_use_epoch = words[10];
    record.pin_count = static_cast<uint32_t>(words[11]);
    record.admission_claim_count = static_cast<uint32_t>(words[12]);
    record.outstanding_kv_dma = static_cast<uint32_t>(words[13]);
    record.diagnostic_prefix_tokens = static_cast<uint32_t>(words[14]);
    record.diagnostic_prefix_bytes = words[15];
    if (words[16]) {
        std::array<uint8_t, 32> digest = {};
        for (size_t word = 0; word < 4; ++word)
            for (size_t byte = 0; byte < 8; ++byte)
                digest[word * 8 + byte] =
                    uint8_t(words[33 + word] >> (8 * byte));
        record.diagnostic_prefix_digest = digest;
    }
    if (words[17]) {
        KvErrorCandidate error;
        error.tick = words[18];
        error.code = static_cast<uint32_t>(words[19]);
        uint8_t wire[mesh_abi::kErrorSourceKeysBytes];
        for (size_t word = 0; word < 5; ++word)
            for (size_t byte = 0; byte < 8; ++byte)
                wire[word * 8 + byte] =
                    uint8_t(words[20 + word] >> (8 * byte));
        mesh_abi::AbiError abi_error;
        EXPECT_TRUE(mesh_abi::decodeErrorSourceKey(wire, error.source,
                                                   abi_error));
        record.first_error = error;
    }
    for (size_t word = 0; word < 4; ++word)
        for (size_t byte = 0; byte < 8; ++byte)
            record.contract_digest[word * 8 + byte] =
                uint8_t(words[25 + word] >> (8 * byte));
    return record;
}

std::vector<TraceScenario> loadTrace(const char *path)
{
    std::ifstream stream(path, std::ios::binary);
    EXPECT_TRUE(stream.good()) << path;
    std::vector<uint8_t> data((std::istreambuf_iterator<char>(stream)),
                              std::istreambuf_iterator<char>());
    EXPECT_GE(data.size(), 8u);
    EXPECT_EQ(std::string(data.begin(), data.begin() + 4), "KVT1");
    Cursor cursor{data, 0};
    cursor.offset = 4;
    const uint32_t version = cursor.u32();
    EXPECT_EQ(version, 1u);
    const uint32_t scenario_count = cursor.u32();
    std::vector<TraceScenario> scenarios;
    for (uint32_t index = 0; index < scenario_count; ++index) {
        TraceScenario scenario;
        scenario.name = cursor.text();
        scenario.geometry.region_base = cursor.u64();
        scenario.geometry.slot_bytes = cursor.u64();
        scenario.geometry.slot_alignment = cursor.u64();
        scenario.geometry.max_sessions = cursor.u32();
        scenario.geometry.bytes_per_token = cursor.u32();
        scenario.capacity.record_entries = cursor.u32();
        scenario.capacity.tombstone_entries = cursor.u32();
        scenario.capacity.admission_wait_entries = cursor.u32();
        scenario.capacity.release_waiter_entries = cursor.u32();
        const uint32_t contracts = cursor.u32();
        EXPECT_EQ(contracts, 2u);
        for (uint32_t contract = 0; contract < contracts; ++contract) {
            const std::array<uint8_t, 32> digest = readDigest(cursor);
            EXPECT_EQ(digest, MeshKvManager::contractDigest(
                digestOf(static_cast<uint8_t>(contract)), digestOf(32),
                digestOf(64), kBytesPerToken))
                << "unexpected contract digest " << contract;
        }
        const uint32_t sessions = cursor.u32();
        EXPECT_EQ(sessions, scenario.geometry.max_sessions);
        for (uint32_t slot = 0; slot < sessions; ++slot) {
            const uint64_t session_id = cursor.u64();
            const uint64_t kv_handle = cursor.u64();
            const uint64_t present = cursor.u64();
            scenario.initial.slot_owners.push_back(
                present ? std::optional(std::make_pair(session_id, kv_handle))
                        : std::nullopt);
        }
        const uint32_t record_count = cursor.u32();
        for (uint32_t record = 0; record < record_count; ++record)
            scenario.initial.records.push_back(readRecord(cursor));
        const uint32_t tombstone_count = cursor.u32();
        for (uint32_t entry = 0; entry < tombstone_count; ++entry) {
            const uint64_t session_id = cursor.u64();
            const uint64_t kv_handle = cursor.u64();
            const uint32_t generation = cursor.u32();
            scenario.initial.tombstones.push_back(
                {{session_id, kv_handle}, generation});
        }
        scenario.initial.next_kv_use_epoch = cursor.u64();
        const uint32_t edge_count = cursor.u32();
        scenario.fatal_edges = cursor.u32();
        for (uint32_t edge_index = 0; edge_index < edge_count; ++edge_index) {
            TraceEdge edge;
            const uint32_t event_count = cursor.u32();
            for (uint32_t event = 0; event < event_count; ++event) {
                std::vector<uint64_t> words;
                for (size_t word = 0; word < kEventWords; ++word)
                    words.push_back(cursor.u64());
                edge.events.push_back(words);
            }
            const uint32_t scalar_count = cursor.u32();
            for (uint32_t scalar = 0; scalar < scalar_count; ++scalar)
                edge.scalars.push_back(cursor.u64());
            const uint32_t name_count = cursor.u32();
            for (uint32_t name = 0; name < name_count; ++name)
                edge.names.push_back(cursor.text());
            scenario.edges.push_back(edge);
        }
        scenarios.push_back(scenario);
    }
    EXPECT_EQ(cursor.offset, data.size());
    return scenarios;
}

KvEdgeInputs decodeEdge(
    const std::vector<std::vector<uint64_t>> &events,
    const std::map<uint64_t, KvRollbackToken> &handoffs)
{
    KvEdgeInputs edge;
    for (const std::vector<uint64_t> &words : events) {
        switch (words[0]) {
          case kKindAdmission: {
            KvAdmissionIntent intent;
            intent.request_id = words[1];
            intent.session_id = words[2];
            intent.kv_handle = words[3];
            intent.generation = static_cast<uint32_t>(words[4]);
            intent.flags = static_cast<uint32_t>(words[5]);
            intent.contract_digest = contractDigest(kBytesPerToken);
            intent.deadline_or_max = words[7];
            intent.qos = static_cast<uint32_t>(words[8]);
            intent.ready_tick = words[9];
            intent.required_cached_tokens = static_cast<uint32_t>(words[10]);
            intent.required_tokens_after_round =
                static_cast<uint32_t>(words[11]);
            edge.admissions.push_back(intent);
            break;
          }
          case kKindRelease: {
            KvReleaseIntent intent;
            intent.request_id = words[1];
            intent.session_id = words[2];
            intent.kv_handle = words[3];
            intent.generation = static_cast<uint32_t>(words[4]);
            edge.releases.push_back(intent);
            break;
          }
          case kKindOwnerTerminal: {
            KvOwnerTerminal terminal;
            terminal.request_id = words[1];
            terminal.status = static_cast<KvTerminalStatus>(words[2]);
            terminal.strict = words[3] != 0;
            if (words[4]) {
                const auto found = handoffs.find(words[4]);
                EXPECT_NE(found, handoffs.end());
                if (found != handoffs.end())
                    terminal.token = found->second;
            }
            edge.owner_terminals.push_back(terminal);
            break;
          }
          case kKindLaterFault: {
            KvLaterFault later;
            later.request_id = words[1];
            later.candidate.tick = words[2];
            later.candidate.source.ordinal = static_cast<uint32_t>(words[3]);
            later.candidate.code = static_cast<uint32_t>(words[4]);
            later.candidate.source.error_class = 2;
            later.candidate.source.core_id_or_ffff = 0xFFFF;
            later.candidate.source.domain = 4;
            later.candidate.source.object_kind = 9;
            edge.later_faults.push_back(later);
            break;
          }
          case kKindCoreStart:
            edge.core_starts.push_back(words[1]);
            break;
          case kKindAppendArm: {
            KvAppendArm arm;
            arm.request_id = words[1];
            arm.base_tokens = static_cast<uint32_t>(words[2]);
            arm.append_tokens = static_cast<uint32_t>(words[3]);
            edge.append_arms.push_back(arm);
            break;
          }
          case kKindAppendTerminal: {
            KvAppendTerminal terminal;
            terminal.request_id = words[1];
            for (uint32_t index = 0; index < words[3]; ++index)
                terminal.tokens_ok.push_back(((words[2] >> index) & 1) != 0);
            edge.append_terminals.push_back(terminal);
            break;
          }
          case kKindDmaAccept: {
            KvDmaCount accept;
            accept.request_id = words[1];
            accept.count = static_cast<uint32_t>(words[2]);
            edge.dma_accepts.push_back(accept);
            break;
          }
          case kKindDmaTerminal: {
            KvDmaCount terminal;
            terminal.request_id = words[1];
            terminal.count = static_cast<uint32_t>(words[2]);
            edge.dma_terminals.push_back(terminal);
            break;
          }
          case kKindFault: {
            KvFaultEvent fault;
            fault.request_id = words[1];
            fault.candidate.tick = words[2];
            fault.candidate.source.ordinal = static_cast<uint32_t>(words[3]);
            fault.candidate.code = static_cast<uint32_t>(words[4]);
            fault.candidate.source.error_class = 2;
            fault.candidate.source.core_id_or_ffff = 0xFFFF;
            fault.candidate.source.domain = 4;
            fault.candidate.source.object_kind = 9;
            edge.faults.push_back(fault);
            break;
          }
          case kKindViewArm: {
            KvViewArm arm;
            arm.request_id = words[1];
            arm.member_ordinal = static_cast<uint32_t>(words[2]);
            edge.view_arms.push_back(arm);
            break;
          }
          default:
            ADD_FAILURE() << "unknown event kind " << words[0];
            break;
        }
    }
    return edge;
}

std::string firstMismatch(const std::vector<uint64_t> &actual,
                          const std::vector<uint64_t> &expected,
                          const std::vector<std::string> &names)
{
    if (actual.size() != expected.size()) {
        return "size " + std::to_string(actual.size()) + " != " +
            std::to_string(expected.size());
    }
    for (size_t index = 0; index < actual.size(); ++index) {
        if (actual[index] == expected[index])
            continue;
        const std::string name = index < names.size()
            ? names[index] : "?";
        return "index " + std::to_string(index) + " (" + name + ") " +
            std::to_string(actual[index]) + " != " +
            std::to_string(expected[index]);
    }
    return "";
}

TEST(MeshKvManager, MatchesTheCrossLanguageTrace)
{
    const std::vector<TraceScenario> scenarios =
        loadTrace("tests/gem5/ai_mesh/fixtures/gate6/kv_trace.bin");
    ASSERT_EQ(scenarios.size(), 13u);
    for (const TraceScenario &scenario : scenarios) {
        MeshKvManager manager(scenario.geometry, scenario.capacity);
        std::string reason;
        ASSERT_TRUE(manager.loadPersistent(scenario.initial, reason))
            << scenario.name << ": " << reason;
        std::map<uint64_t, KvRollbackToken> handoffs;
        uint32_t seen_fatals = 0;
        for (size_t index = 0; index < scenario.edges.size(); ++index) {
            const TraceEdge &edge = scenario.edges[index];
            const KvEdgeInputs inputs = decodeEdge(edge.events, handoffs);
            MeshKvManager restored(scenario.geometry, scenario.capacity);
            ASSERT_TRUE(restored.loadLive(manager.saveLive(), reason))
                << scenario.name << " edge " << index + 1 << ": " << reason;
            const KvEdgeResult result = manager.commitEdge(inputs);
            const KvEdgeResult clone = restored.commitEdge(inputs);
            const std::string context = scenario.name + " edge " +
                std::to_string(index + 1) + " fatal=" +
                (result.fatal ? *result.fatal : "none");
            EXPECT_EQ(firstMismatch(manager.edgeScalars(result),
                                    edge.scalars, edge.names), "")
                << context;
            EXPECT_EQ(firstMismatch(restored.edgeScalars(clone),
                                    manager.edgeScalars(result), edge.names),
                      "") << "replay " << context;
            EXPECT_TRUE(manager.validate().empty()) << context;
            EXPECT_TRUE(restored.validate().empty()) << "replay " << context;
            EXPECT_TRUE(manager.stateScalars() == restored.stateScalars())
                << "replay state " << context;
            if (result.fatal) {
                ++seen_fatals;
                break;
            }
            for (const auto &entry : result.handoffs)
                handoffs[entry.first] = entry.second;
        }
        EXPECT_EQ(seen_fatals, scenario.fatal_edges) << scenario.name;
        EXPECT_TRUE(manager.validate().empty()) << scenario.name;
    }
}

// -------------------------------------------------------------- capacity


} // anonymous namespace
} // namespace ai_mesh
} // namespace gem5

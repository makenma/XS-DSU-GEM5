#include <gtest/gtest.h>

#include <cstdint>
#include <limits>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "dev/ai_mesh/agent_plan_codec.hh"
#include "dev/ai_mesh/agent_plan_image.hh"
#include "dev/ai_mesh/agent_sha256.hh"

namespace
{

const char kFixturePath[] =
    "tests/gem5/ai_mesh/fixtures/gate4/agent_plan_image_two_user.bin";

std::vector<uint8_t>
loadFixture()
{
    std::ifstream stream(kFixturePath, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(stream)),
                                std::istreambuf_iterator<char>());
}

std::string
hexOf(const uint8_t *data, size_t length)
{
    static const char *digits = "0123456789abcdef";
    std::string text;
    for (size_t index = 0; index < length; ++index) {
        text += digits[data[index] >> 4];
        text += digits[data[index] & 0xf];
    }
    return text;
}

const char kParam0Hex[] =
    "41474e5001000000a00000000001000000000000000000000000000101000000"
    "0040000000000000000400000000000000000005010000000020000000000000"
    "0000000901000000000200008000000001000000000000000100000000000000"
    "000000000000000000000401010000000000000000000000a000000000001800"
    "a000000060000000011000000000000000000000000000002c917c7a00000000"
    "0100010020000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    "aaaaaaaaaaaaaaaa030001000800000000100000000000000400010020000000"
    "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e";

}

TEST(AgentPlanCodec, ParameterRound0MatchesPythonGolden)
{
    const std::vector<uint8_t> image = loadFixture();
    ASSERT_FALSE(image.empty());
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    const auto &round = task.rounds[0];
    gem5::ai_mesh::PlanWireAddresses addresses;
    addresses.inputBase = 0x0000000101000000ull;
    addresses.outputBase = 0x0000000105000000ull;
    addresses.metadataBase = 0x0000000109000000ull;
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, parsed->workloadPlanDigest().data(),
        4096);
    ASSERT_EQ(parameter.size(), 256u);
    EXPECT_EQ(hexOf(parameter.data(), parameter.size()), kParam0Hex);
}

TEST(AgentPlanCodec, ParameterRound1MatchesPythonDigest)
{
    const std::vector<uint8_t> image = loadFixture();
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    const auto &round = task.rounds[1];
    gem5::ai_mesh::PlanWireAddresses addresses;
    addresses.inputBase = 0x0000000101000000ull + 16384;
    addresses.outputBase = 0x0000000105000000ull + 8192;
    addresses.metadataBase = 0x0000000109000000ull + 512;
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, parsed->workloadPlanDigest().data(),
        4096);
    ASSERT_EQ(parameter.size(), 256u);
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(parameter).data(), 32),
              "2854ec712c5739031bf5256627f1d26d18c567ee5417fe095e0891b7343"
              "137c7");
}

TEST(AgentPlanCodec, DeadlineTlvKeepsAscendingTypeOrder)
{
    gem5::ai_mesh::AgentPlanRound round;
    round.hasDeadline = true;
    round.deadlineTick = 12345;
    round.fullContextBytes = 64;
    round.fullContextTokens = 2;
    round.outputCapacityBytes = 64;
    round.metadataCapacityBytes = 512;
    round.outputTokens = 1;
    round.itemId = 9;
    round.qos = 1;
    gem5::ai_mesh::AgentPlanTask task;
    task.taskSeq = 0;
    task.kvHandle = 7;
    task.generation = 1;
    gem5::ai_mesh::PlanWireAddresses addresses;
    const uint8_t workloadDigest[32] = {};
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, workloadDigest, 4096);
    ASSERT_EQ(parameter.size(), 160u + 40 + 16 + 16 + 40);
    const size_t tail = 160;
    EXPECT_EQ(parameter[tail], 0x01);
    EXPECT_EQ(parameter[tail + 8 + 32], 0x02);
    EXPECT_EQ(parameter[tail + 8 + 32 + 8 + 8], 0x03);
    EXPECT_EQ(parameter[tail + 8 + 32 + 8 + 8 + 8 + 8], 0x04);
    uint64_t deadline = 0;
    for (size_t index = 0; index < 8; ++index)
        deadline |= uint64_t(parameter[tail + 8 + 32 + 8 + index])
                    << (8 * index);
    EXPECT_EQ(deadline, 12345u);
}

TEST(AgentPlanCodec, KvPolicyMapsToSqFlags)
{
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(0), 0);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(1), 0x0001);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(2), 0x0002);
    EXPECT_EQ(gem5::ai_mesh::kvPolicyToSqFlags(3), 0xffff);
}

const char kControlCancelHex[] =
    "41474e5001000000a0000000a000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000030000000001000000010000000000000000001800"
    "0000000000000000000000000000000000000000000000007ffdc3cb00000000";
const char kControlReleaseHex[] =
    "41474e5001000000a0000000a000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000b000000000000000100000000000000"
    "0000000000000000000000020000000000000000000000000000000000001800"
    "000000000000000000000000000000000000000000000000ec41f81e00000000";

TEST(AgentPlanCodec, ControlParameterMatchesPythonGolden)
{
    const auto cancel = gem5::ai_mesh::buildControlParameter(
        2, (1ull << 32) | 1, 0, 0);
    ASSERT_EQ(cancel.size(), 160u);
    EXPECT_EQ(hexOf(cancel.data(), cancel.size()), kControlCancelHex);
    const auto release = gem5::ai_mesh::buildControlParameter(1, 0, 11, 1);
    ASSERT_EQ(release.size(), 160u);
    EXPECT_EQ(hexOf(release.data(), release.size()), kControlReleaseHex);
}

TEST(AgentPlanCodec, NormalizeBindingsSortsAndRejectsRepeats)
{
    std::vector<gem5::ai_mesh::agent_abi::BindingRecord> bindings(2);
    bindings[0].symbol_id = 7;
    bindings[0].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_OUTPUT;
    bindings[1].symbol_id = 3;
    bindings[1].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_INPUT;
    std::string reason;
    ASSERT_TRUE(gem5::ai_mesh::normalizeBindings(bindings, reason)) << reason;
    EXPECT_EQ(bindings[0].symbol_id, 3u);
    EXPECT_EQ(bindings[1].symbol_id, 7u);

    std::vector<gem5::ai_mesh::agent_abi::BindingRecord> same(2);
    same[0].symbol_id = 5;
    same[0].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_INPUT;
    same[1].symbol_id = 5;
    same[1].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_OUTPUT;
    EXPECT_FALSE(gem5::ai_mesh::normalizeBindings(same, reason));

    std::vector<gem5::ai_mesh::agent_abi::BindingRecord> same_kind(2);
    same_kind[0].symbol_id = 9;
    same_kind[0].kind = gem5::ai_mesh::agent_abi::kBindingKindKV_EXTERNAL;
    same_kind[1] = same_kind[0];
    EXPECT_FALSE(gem5::ai_mesh::normalizeBindings(same_kind, reason));
    std::vector<gem5::ai_mesh::agent_abi::BindingRecord> empty;
    EXPECT_TRUE(gem5::ai_mesh::normalizeBindings(empty, reason));
}

TEST(AgentPlanCodec, BindingCountStaysRepresentable)
{
    std::string reason;
    EXPECT_TRUE(gem5::ai_mesh::bindingTableRepresentable(0, 0, reason));
    EXPECT_TRUE(gem5::ai_mesh::bindingTableRepresentable(0xFFFFu, 96, reason))
        << reason;
    EXPECT_FALSE(gem5::ai_mesh::bindingTableRepresentable(0x10000u, 96,
                                                          reason));
    EXPECT_FALSE(reason.empty());
    const uint64_t limit = std::numeric_limits<uint32_t>::max();
    const uint64_t header = gem5::ai_mesh::agent_abi::kParameterHeaderBytes;
    EXPECT_TRUE(gem5::ai_mesh::bindingTableRepresentable(0, limit - header,
                                                         reason)) << reason;
    EXPECT_FALSE(gem5::ai_mesh::bindingTableRepresentable(
        0, limit - header + 1, reason));
    EXPECT_FALSE(gem5::ai_mesh::bindingTableRepresentable(
        0, limit, reason));
    EXPECT_FALSE(reason.empty());
}

TEST(AgentPlanCodec, NonEmptyBindingTableLayoutAndCrc)
{
    for (const bool withDeadline : {false, true}) {
        gem5::ai_mesh::AgentPlanRound round;
        round.hasDeadline = withDeadline;
        round.deadlineTick = 12345;
        round.fullContextBytes = 128;
        round.fullContextTokens = 8;
        round.outputCapacityBytes = 256;
        round.metadataCapacityBytes = 512;
        round.outputTokens = 2;
        round.itemId = 3;
        round.qos = 1;
        gem5::ai_mesh::AgentPlanTask task;
        task.taskSeq = 1;
        task.kvHandle = 4;
        task.generation = 1;
        gem5::ai_mesh::PlanWireAddresses addresses;
        addresses.inputBase = 0x100000;
        addresses.outputBase = 0x300000;
        const uint8_t workloadDigest[32] = {};
        std::vector<gem5::ai_mesh::agent_abi::BindingRecord> bindings(2);
        bindings[0].symbol_id = 1;
        bindings[0].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_INPUT;
        bindings[0].flags = gem5::ai_mesh::agent_abi::kBindingFlagsREAD;
        bindings[0].address = addresses.inputBase;
        bindings[0].bytes = 128;
        bindings[1].symbol_id = 2;
        bindings[1].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_OUTPUT;
        bindings[1].flags = gem5::ai_mesh::agent_abi::kBindingFlagsWRITE;
        bindings[1].address = addresses.outputBase;
        bindings[1].bytes = 256;
        const auto parameter = gem5::ai_mesh::buildPlanParameter(
            round, task, 0, addresses, workloadDigest, 4096, bindings);
        ASSERT_GE(parameter.size(), 160u + 48u);
        const auto header =
            gem5::ai_mesh::agent_abi::decodeParameterHeader(parameter.data());
        EXPECT_EQ(header.binding_count, 2u);
        EXPECT_EQ(header.binding_table_offset, 160u);
        EXPECT_EQ(header.binding_record_bytes, gem5::ai_mesh::agent_abi::kBindingRecordBytes);
        EXPECT_EQ(header.extension_offset, 160u + 48u);
        EXPECT_EQ(header.total_bytes, parameter.size());
        EXPECT_EQ(header.extension_offset + header.extension_bytes,
                  parameter.size());
        for (uint32_t index = 0; index < 2; ++index) {
            const auto record = gem5::ai_mesh::agent_abi::decodeBindingRecord(
                parameter.data() + header.binding_table_offset +
                index * gem5::ai_mesh::agent_abi::kBindingRecordBytes);
            EXPECT_EQ(record.symbol_id, bindings[index].symbol_id);
            EXPECT_EQ(record.kind, bindings[index].kind);
            EXPECT_EQ(record.flags, bindings[index].flags);
            EXPECT_EQ(record.address, bindings[index].address);
            EXPECT_EQ(record.bytes, bindings[index].bytes);
        }
        const size_t crcOffset =
            gem5::ai_mesh::agent_abi::kParameterHeaderCrc32Offset;
        uint32_t stored = 0;
        for (size_t index = 0; index < 4; ++index)
            stored |= uint32_t(parameter[crcOffset + index]) << (8 * index);
        std::vector<uint8_t> copy = parameter;
        std::fill(copy.begin() + crcOffset, copy.begin() + crcOffset + 4, 0);
        EXPECT_EQ(stored, gem5::ai_mesh::agent_abi::crc32c(copy.data(),
                                                           copy.size()));
        copy = parameter;
        copy[header.binding_table_offset + 4] ^= 0xff;
        std::fill(copy.begin() + crcOffset, copy.begin() + crcOffset + 4, 0);
        EXPECT_NE(stored, gem5::ai_mesh::agent_abi::crc32c(copy.data(),
                                                           copy.size()));
    }
}

TEST(AgentPlanCodec, NonEmptyBindingTableMatchesPythonGolden)
{
    const std::vector<uint8_t> image = loadFixture();
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    const auto &round = task.rounds[0];
    gem5::ai_mesh::PlanWireAddresses addresses;
    addresses.inputBase = 0x0000000101000000ull;
    addresses.outputBase = 0x0000000105000000ull;
    addresses.metadataBase = 0x0000000109000000ull;
    std::vector<gem5::ai_mesh::agent_abi::BindingRecord> bindings(2);
    bindings[0].symbol_id = 1;
    bindings[0].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_INPUT;
    bindings[0].flags = gem5::ai_mesh::agent_abi::kBindingFlagsREAD;
    bindings[0].address = addresses.inputBase;
    bindings[0].bytes = round.fullContextBytes;
    bindings[1].symbol_id = 2;
    bindings[1].kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_OUTPUT;
    bindings[1].flags = gem5::ai_mesh::agent_abi::kBindingFlagsWRITE;
    bindings[1].address = addresses.outputBase;
    bindings[1].bytes = round.outputCapacityBytes;
    const auto parameter = gem5::ai_mesh::buildPlanParameter(
        round, task, 0, addresses, parsed->workloadPlanDigest().data(),
        4096, bindings);
    EXPECT_EQ(hexOf(gem5::ai_mesh::agentSha256(parameter).data(), 32),
              "2abd6a99b91d3c84919fea81ca3d56707c84c1841e568ef1a13fa54d09b0de22");
}

TEST(AgentPlanCodec, PlannedLengthMatchesEncodedBlob)
{
    const std::vector<uint8_t> image = loadFixture();
    const auto parsed = gem5::ai_mesh::AgentPlanImage::parse(
        image.data(), image.size());
    ASSERT_TRUE(parsed.has_value());
    const auto &task = parsed->users()[0].tasks[0];
    for (size_t roundIndex = 0; roundIndex < 2; ++roundIndex) {
        const auto &round = task.rounds[roundIndex];
        for (size_t count : {size_t(0), size_t(2)}) {
            std::vector<gem5::ai_mesh::agent_abi::BindingRecord> bindings;
            for (size_t index = 0; index < count; ++index) {
                gem5::ai_mesh::agent_abi::BindingRecord record;
                record.symbol_id = uint32_t(index + 1);
                record.kind = gem5::ai_mesh::agent_abi::kBindingKindHOST_INPUT;
                bindings.push_back(record);
            }
            const uint8_t workloadDigest[32] = {};
            const auto parameter = gem5::ai_mesh::buildPlanParameter(
                round, task, 0, gem5::ai_mesh::PlanWireAddresses(),
                workloadDigest, 4096, bindings);
            EXPECT_EQ(gem5::ai_mesh::planParameterBytes(round, count),
                      parameter.size())
                << "round " << roundIndex << " count " << count;
        }
    }
}

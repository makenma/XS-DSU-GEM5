#include "dev/ai_mesh/agent_plan_image.hh"

#include <cstring>
#include <fstream>
#include <iterator>
#include <utility>

#include "dev/ai_mesh/agent_sha256.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

constexpr uint8_t kMagic[4] = {'A', 'G', 'P', 'I'};
constexpr uint32_t kVersion = 1;
constexpr uint16_t kSectionWorkload = 1;
constexpr uint16_t kSectionCommandIdentity = 2;
constexpr uint16_t kSectionHostTaskIdentity = 3;
constexpr uint16_t kSectionArena = 4;
constexpr uint16_t kSectionSurrogate = 5;
constexpr uint16_t kSectionControl = 6;

class Reader
{
  public:
    Reader(const uint8_t *data, size_t length)
        : data(data), length(length) {}

    bool failed() const { return failed_; }
    size_t offset() const { return offset_; }

    uint8_t u8()
    {
        if (!take(1))
            return 0;
        return data[offset_++];
    }

    uint16_t u16()
    {
        if (!take(2))
            return 0;
        const uint16_t value = uint16_t(data[offset_]) |
                               uint16_t(data[offset_ + 1]) << 8;
        offset_ += 2;
        return value;
    }

    uint32_t u32()
    {
        if (!take(4))
            return 0;
        uint32_t value = 0;
        for (size_t index = 0; index < 4; ++index)
            value |= uint32_t(data[offset_ + index]) << (8 * index);
        offset_ += 4;
        return value;
    }

    uint64_t u64()
    {
        if (!take(8))
            return 0;
        uint64_t value = 0;
        for (size_t index = 0; index < 8; ++index)
            value |= uint64_t(data[offset_ + index]) << (8 * index);
        offset_ += 8;
        return value;
    }

    bool bytes(uint8_t *target, size_t count)
    {
        if (!take(count))
            return false;
        std::memcpy(target, data + offset_, count);
        offset_ += count;
        return true;
    }

    bool skip(size_t count)
    {
        if (!take(count))
            return false;
        offset_ += count;
        return true;
    }

  private:
    bool take(size_t count)
    {
        if (failed_ || count > length - offset_) {
            failed_ = true;
            return false;
        }
        return true;
    }

    const uint8_t *data;
    size_t length;
    size_t offset_ = 0;
    bool failed_ = false;
};

AgentPlanStage
readStage(Reader &reader)
{
    AgentPlanStage stage;
    stage.nominalNs = reader.u64();
    stage.hostTokensRequired = reader.u16();
    stage.outcome = reader.u8();
    stage.readBytes = reader.u64();
    stage.writeBytes = reader.u64();
    stage.rawLogBytes = reader.u64();
    stage.excerptBytes = reader.u64();
    stage.excerptTokens = reader.u32();
    return stage;
}

bool
readWorkloadSection(Reader &reader, std::vector<AgentPlanUser> &users)
{
    const uint32_t userCount = reader.u32();
    if (reader.failed() || userCount > 4096)
        return false;
    for (uint32_t userIndex = 0; userIndex < userCount; ++userIndex) {
        AgentPlanUser user;
        user.userId = reader.u32();
        const uint32_t taskCount = reader.u32();
        if (reader.failed() || taskCount > 65536)
            return false;
        for (uint32_t taskIndex = 0; taskIndex < taskCount; ++taskIndex) {
            AgentPlanTask task;
            task.taskSeq = reader.u32();
            task.thinkNs = reader.u64();
            task.sessionId = reader.u64();
            task.kvHandle = reader.u64();
            task.generation = reader.u32();
            task.effectiveCap = reader.u16();
            const uint32_t roundCount = reader.u32();
            if (reader.failed() || roundCount > 65536)
                return false;
            for (uint32_t roundIndex = 0; roundIndex < roundCount; ++roundIndex) {
                AgentPlanRound round;
                round.itemId = reader.u32();
                round.repairRound = reader.u16();
                round.kvPolicy = reader.u8();
                round.hasDeadline = reader.u8() != 0;
                round.deadlineTick = reader.u64();
                round.fullContextTokens = reader.u32();
                round.fullContextBytes = reader.u64();
                round.cachedTokens = reader.u32();
                round.cachedContextBytes = reader.u64();
                round.outputTokens = reader.u32();
                round.generatedCodeBytes = reader.u64();
                round.outputCapacityBytes = reader.u64();
                round.metadataCapacityBytes = reader.u32();
                round.programId = reader.u16();
                round.profileId = reader.u16();
                round.profileKey = reader.u64();
                round.qos = reader.u8();
                if (!reader.bytes(round.inputDigest.data(), 32))
                    return false;
                round.compile = readStage(reader);
                round.hasTest = reader.u8() != 0;
                if (round.hasTest)
                    round.test = readStage(reader);
                round.hasParse = reader.u8() != 0;
                if (round.hasParse)
                    round.logParse = readStage(reader);
                round.hasPrompt = reader.u8() != 0;
                round.promptTokens = reader.u32();
                round.promptBytes = reader.u64();
                round.hasDelta = reader.u8() != 0;
                round.deltaPromptTokens = reader.u32();
                round.deltaPromptBytes = reader.u64();
                if (reader.failed())
                    return false;
                task.rounds.push_back(round);
            }
            user.tasks.push_back(task);
        }
        users.push_back(user);
    }
    return !reader.failed();
}

bool
readCommandSection(Reader &reader, std::vector<AgentCommandRecord> &commands)
{
    const uint32_t count = reader.u32();
    if (reader.failed() || count > 65536)
        return false;
    for (uint32_t index = 0; index < count; ++index) {
        AgentCommandRecord record;
        record.userId = reader.u32();
        record.perUserCommandSeq = reader.u32();
        record.requestId = reader.u64();
        record.commandKind = reader.u8();
        record.taskSeq = reader.u32();
        record.repairRoundOrFFFF = reader.u16();
        record.controlOrdinal = reader.u32();
        record.targetRequestId = reader.u64();
        record.sessionId = reader.u64();
        record.kvHandle = reader.u64();
        record.generation = reader.u32();
        if (reader.failed())
            return false;
        commands.push_back(record);
    }
    return true;
}

bool
readHostTaskSection(Reader &reader, std::vector<AgentHostTaskRecord> &hostTasks)
{
    const uint32_t count = reader.u32();
    if (reader.failed() || count > 262144)
        return false;
    for (uint32_t index = 0; index < count; ++index) {
        AgentHostTaskRecord record;
        record.userId = reader.u32();
        record.taskSeq = reader.u32();
        record.repairRound = reader.u16();
        record.stageKind = reader.u8();
        record.hostTaskId = reader.u64();
        if (reader.failed())
            return false;
        hostTasks.push_back(record);
    }
    return true;
}

bool
readArenaSection(Reader &reader, std::vector<AgentArenaRecord> &arena)
{
    const uint32_t count = reader.u32();
    if (reader.failed() || count > 262144)
        return false;
    for (uint32_t index = 0; index < count; ++index) {
        AgentArenaRecord record;
        record.userId = reader.u32();
        record.taskSeq = reader.u32();
        record.repairRoundOrFFFF = reader.u16();
        record.commandKind = reader.u8();
        record.arenaKind = reader.u8();
        record.perUserCommandSeq = reader.u32();
        record.requestId = reader.u64();
        record.base = reader.u64();
        record.allocationBytes = reader.u64();
        record.initialValidBytes = reader.u64();
        record.alignment = reader.u32();
        if (reader.failed())
            return false;
        arena.push_back(record);
    }
    return true;
}

bool
readControlSection(Reader &reader, std::vector<AgentControlAction> &actions)
{
    const uint32_t count = reader.u32();
    if (reader.failed() || count > 65536)
        return false;
    for (uint32_t index = 0; index < count; ++index) {
        AgentControlAction action;
        action.controlOrdinal = reader.u32();
        action.opcode = reader.u8();
        action.triggerKind = reader.u8();
        action.hasAnchor = reader.u8() != 0;
        action.anchorUserId = reader.u32();
        action.anchorTaskSeq = reader.u32();
        action.anchorRepairRound = reader.u16();
        action.hasAfterOrdinal = reader.u8() != 0;
        action.afterControlOrdinal = reader.u32();
        action.issuerUserId = reader.u32();
        action.issuerTaskSeq = reader.u32();
        action.targetUserId = reader.u16();
        action.targetTaskSeq = reader.u32();
        action.targetRepairRound = reader.u16();
        action.targetSessionId = reader.u64();
        action.targetKvHandle = reader.u64();
        action.targetGeneration = reader.u32();
        if (reader.failed())
            return false;
        actions.push_back(action);
    }
    return true;
}

}

std::optional<AgentPlanImage>
AgentPlanImage::parse(const uint8_t *data, size_t length)
{
    if (!data || length < 4 + 4 + 4 + 32 + 32 + 32)
        return std::nullopt;
    AgentSha256 hash;
    hash.update(data, length - 32);
    const std::array<uint8_t, 32> computed = hash.finish();
    std::array<uint8_t, 32> stored{};
    std::memcpy(stored.data(), data + length - 32, 32);
    if (computed != stored)
        return std::nullopt;

    Reader reader(data, length - 32);
    uint8_t magic[4];
    if (!reader.bytes(magic, 4) ||
        std::memcmp(magic, kMagic, sizeof(kMagic)) != 0)
        return std::nullopt;
    if (reader.u32() != kVersion)
        return std::nullopt;
    const uint32_t sectionCount = reader.u32();
    if (reader.failed() || sectionCount < 4 || sectionCount > 6)
        return std::nullopt;

    AgentPlanImage image;
    if (!reader.bytes(image.workloadPlanDigest_.data(), 32) ||
        !reader.bytes(image.controlPlanDigest_.data(), 32))
        return std::nullopt;

    for (uint32_t section = 0; section < sectionCount; ++section) {
        const uint16_t type = reader.u16();
        const uint64_t sectionLength = reader.u64();
        if (reader.failed())
            return std::nullopt;
        const size_t start = reader.offset();
        bool ok = false;
        if (type == kSectionWorkload)
            ok = readWorkloadSection(reader, image.users_);
        else if (type == kSectionCommandIdentity)
            ok = readCommandSection(reader, image.commands_);
        else if (type == kSectionHostTaskIdentity)
            ok = readHostTaskSection(reader, image.hostTasks_);
        else if (type == kSectionArena)
            ok = readArenaSection(reader, image.arena_);
        else if (type == kSectionControl)
            ok = readControlSection(reader, image.controlActions_);
        else if (type == kSectionSurrogate) {
            if (sectionLength > length - 32 - start)
                return std::nullopt;
            auto registry = SurrogateProfileRegistry::parse(
                data + start, sectionLength);
            if (!registry.has_value())
                return std::nullopt;
            image.surrogate_ = std::move(registry);
            ok = reader.skip(sectionLength);
        } else
            return std::nullopt;
        if (!ok)
            return std::nullopt;
        const size_t consumed = reader.offset() - start;
        if (consumed != sectionLength)
            return std::nullopt;
    }
    if (reader.failed() || reader.offset() != length - 32)
        return std::nullopt;
    image.imageDigest_ = stored;
    return image;
}

uint32_t
AgentPlanImage::generateCommandCount() const
{
    uint32_t count = 0;
    for (const AgentCommandRecord &record : commands_)
        if (record.commandKind == kAgentCommandGenerate)
            ++count;
    return count;
}

std::optional<AgentPlanImage>
loadAgentPlanImageFile(const std::string &path)
{
    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        return std::nullopt;
    const std::vector<uint8_t> bytes(
        (std::istreambuf_iterator<char>(stream)),
        std::istreambuf_iterator<char>());
    if (bytes.empty())
        return std::nullopt;
    return AgentPlanImage::parse(bytes.data(), bytes.size());
}

}
}

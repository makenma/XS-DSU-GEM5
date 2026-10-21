#include "dev/ai_mesh/serving_batch_replay.hh"

#include <algorithm>
#include <map>
#include <stdexcept>

#include "dev/ai_mesh/agent_sha256.hh"
#include "dev/ai_mesh/mesh_json.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"

namespace gem5::ai_mesh
{
namespace
{
std::string hex(const std::array<uint8_t, 32> &digest)
{
    const char *digits = "0123456789abcdef";
    std::string text;
    for (uint8_t byte : digest) {
        text += digits[byte >> 4];
        text += digits[byte & 15];
    }
    return text;
}

void exact(const boost::json::object &object, std::initializer_list<const char *> keys)
{
    if (object.size() != keys.size() || std::any_of(keys.begin(), keys.end(),
            [&](const char *key) { return !object.contains(key); }))
        throw std::invalid_argument("replay object has missing or unknown fields");
}

uint64_t number(const boost::json::object &object, const char *key, uint64_t maximum)
{
    const auto &value = object.at(key);
    if ((!value.is_int64() && !value.is_uint64()) ||
            (value.is_int64() && value.as_int64() < 0))
        throw std::invalid_argument("replay integer has an invalid type or sign");
    const uint64_t result = value.is_uint64() ? value.as_uint64() : value.as_int64();
    if (result > maximum)
        throw std::invalid_argument("replay integer is out of range");
    return result;
}

struct Progress
{
    uint16_t path = 0;
    uint32_t iteration = 0;
    uint32_t produced = 0;
    bool published = false;
};
}

std::optional<ServingBatchReplay>
ServingBatchReplay::parse(const std::string &text, const AgentPlanImage &plan,
                         const DecodedProgram &program, std::string &reason)
{
    auto document = parseMeshJson(text, reason);
    if (!document)
        return std::nullopt;
    try {
        using namespace mesh_abi;
        const auto &root = document->as_object();
        exact(root, {"schema", "version", "workload_plan_digest", "mesh_program_digest", "batches"});
        if (root.at("schema").as_string() != "serving_batch_replay_v1" ||
                number(root, "version", 1) != 1 ||
                root.at("workload_plan_digest").as_string() != hex(plan.workloadPlanDigest()) ||
                root.at("mesh_program_digest").as_string() != hex((program).metadata.semantic_sha256))
            throw std::invalid_argument("replay schema, version or input digest mismatch");
        std::map<ServingReplayIdentity, const AgentPlanRound *> rounds;
        for (const auto &user : plan.users())
            for (const auto &task : user.tasks)
                for (const auto &round : task.rounds)
                    if (round.batchReplay)
                        rounds.emplace(ServingReplayIdentity{round.itemId, user.userId,
                            task.taskSeq, round.repairRound}, &round);
        std::map<ServingReplayIdentity, Progress> cursors;
        ServingBatchReplay replay;
        for (const auto &value : root.at("batches").as_array()) {
            const auto &row = value.as_object();
            exact(row, {"batch_ordinal", "phase", "decode_iteration", "concrete_profile_id", "members"});
            ServingReplayBatch batch;
            batch.ordinal = number(row, "batch_ordinal", (uint64_t(1) << 53) - 1);
            batch.decodeIteration = number(row, "decode_iteration", UINT32_MAX);
            batch.profile = number(row, "concrete_profile_id", UINT32_MAX);
            const auto &phase = row.at("phase").as_string();
            batch.phase = phase == "PREFILL" ? kPhasePREFILL : phase == "DECODE" ? kPhaseDECODE :
                phase == "PUBLISH" ? kPhasePUBLISH : 0;
            const auto profile = std::find_if(program.transport.agent_instance_profiles.begin(),
                program.transport.agent_instance_profiles.end(), [&](const auto &entry) {
                    return entry.instance_profile_id == batch.profile;
                });
            if (batch.ordinal != replay.batches.size() || !batch.phase ||
                    profile == program.transport.agent_instance_profiles.end() || profile->phase != batch.phase)
                throw std::invalid_argument("replay ordinal or concrete phase mismatch");
            const auto &members = row.at("members").as_array();
            if (members.empty() || members.size() != profile->member_count)
                throw std::invalid_argument("replay population differs from the concrete profile");
            for (const auto &entry : members) {
                const auto &memberObject = entry.as_object();
                exact(memberObject, {"workload_plan_item_id", "user_id", "task_seq", "repair_round",
                    "sequence_ordinal_begin", "valid_token_count", "logical_source_rank"});
                ServingReplayMember member;
                member.identity.item = number(memberObject, "workload_plan_item_id", UINT32_MAX);
                member.identity.user = number(memberObject, "user_id", UINT32_MAX);
                member.identity.task = number(memberObject, "task_seq", UINT32_MAX);
                member.identity.repair = number(memberObject, "repair_round", UINT16_MAX);
                member.sequenceBegin = number(memberObject, "sequence_ordinal_begin", UINT32_MAX);
                member.validTokens = number(memberObject, "valid_token_count", UINT32_MAX);
                member.logicalSourceRank = number(memberObject, "logical_source_rank", UINT32_MAX);
                const auto source = rounds.find(member.identity);
                if (source == rounds.end() || (!batch.members.empty() &&
                        !(batch.members.back().identity < member.identity)))
                    throw std::invalid_argument("replay member is unflagged, unknown, duplicated or unsorted");
                const auto &round = *source->second;
                const auto &binding = program.transport.agent_instance_member_bindings.at(
                    profile->member_binding_first + batch.members.size());
                if (profile->request_program_id != round.programId ||
                        profile->request_profile_id != round.profileId ||
                        member.logicalSourceRank != round.logicalSourceRank ||
                        member.logicalSourceRank != binding.expected_logical_source_rank)
                    throw std::invalid_argument("replay member profile or stable rank mismatch");
                const auto previous = cursors.find(member.identity);
                uint32_t begin = 0, valid = 0;
                Progress following;
                if (batch.phase == kPhasePREFILL) {
                    const bool reuse = profile->path_kind == kPathKindKV_REUSE;
                    const bool allowed = round.kvPolicy == kAgentKvPolicyInitial ?
                        profile->path_kind == kPathKindINITIAL_PREFILL :
                        round.kvPolicy == kAgentKvPolicyRequireReuse ? reuse :
                        round.kvPolicy == kAgentKvPolicyAllowReprefill &&
                        (reuse || profile->path_kind == kPathKindREPREFILL);
                    if (previous != cursors.end() || batch.decodeIteration || !allowed)
                        throw std::invalid_argument("replay PREFILL has an invalid predecessor or KV policy");
                    begin = reuse ? round.cachedTokens : 0;
                    valid = reuse ? round.deltaPromptTokens : round.fullContextTokens;
                    following.path = profile->path_kind;
                } else {
                    if (previous == cursors.end() || previous->second.published ||
                            previous->second.path != profile->path_kind ||
                            previous->second.iteration != batch.decodeIteration)
                        throw std::invalid_argument("replay phase has no matching live predecessor");
                    following = previous->second;
                    begin = following.produced;
                    if (batch.phase == kPhaseDECODE) {
                        valid = profile->decode_chunk_tokens;
                        if (!valid || uint64_t(begin) + valid > round.outputTokens)
                            throw std::invalid_argument("replay DECODE exceeds the frozen output range");
                        ++following.iteration;
                        following.produced += valid;
                    } else {
                        if (begin != round.outputTokens)
                            throw std::invalid_argument("replay PUBLISH precedes complete DECODE coverage");
                        following.published = true;
                    }
                    if (profile->kv_tokens_before != uint64_t(round.fullContextTokens) + begin)
                        throw std::invalid_argument("replay concrete KV cursor mismatch");
                }
                if (member.sequenceBegin != begin || member.validTokens != valid ||
                        profile->valid_tokens_per_member != valid)
                    throw std::invalid_argument("replay member token interval mismatch");
                cursors[member.identity] = following;
                batch.members.push_back(member);
            }
            replay.batches.push_back(std::move(batch));
        }
        if (cursors.size() != rounds.size() || std::any_of(cursors.begin(), cursors.end(),
                [](const auto &row) { return !row.second.published; }))
            throw std::invalid_argument("replay does not cover every flagged round through PUBLISH");
        const auto canonical = canonicalMeshJson(*document);
        replay.digest = hex(agentSha256(std::vector<uint8_t>(canonical.begin(), canonical.end())));
        reason.clear();
        return replay;
    } catch (const std::invalid_argument &error) {
        reason = error.what();
    } catch (const std::out_of_range &error) {
        reason = error.what();
    } catch (const boost::system::system_error &error) {
        reason = error.what();
    }
    return std::nullopt;
}
}

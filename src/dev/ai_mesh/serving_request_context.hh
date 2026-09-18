#ifndef DEV_AI_MESH_SERVING_REQUEST_CONTEXT_HH
#define DEV_AI_MESH_SERVING_REQUEST_CONTEXT_HH

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "dev/ai_mesh/generated/mesh_ir_abi.hh"
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_kv_manager.hh"

namespace gem5
{
namespace ai_mesh
{

uint32_t pathKindOfSqFlags(uint16_t sq_flags);

struct ServingRequestIdentity
{
    uint64_t request_id = 0;
    uint64_t session_id = 0;
    uint64_t kv_handle = 0;
    uint32_t generation = 0;
    uint32_t program_id = 0;
    uint32_t profile_id = 0;
    uint32_t path_kind = 0;
    uint32_t cached_tokens = 0;
    uint16_t kv_flags = 0;
    uint16_t repair_round = 0;
    uint64_t requested_profile_key = 0;
    uint64_t input_bytes = 0;
    uint32_t input_tokens = 0;
    uint32_t output_tokens = 0;
    uint64_t output_capacity_bytes = 0;
    uint32_t kv_bytes_per_token = 0;
    std::array<uint8_t, 32> contract_digest = {};
};

struct ServingPhaseStep
{
    uint32_t phase = 0;
    uint32_t instance_profile_id = 0;
    uint32_t mesh_profile_id = 0;
    uint32_t member_count = 0;
    uint32_t valid_tokens_per_member = 0;
    uint32_t kv_tokens_before = 0;
    uint32_t decode_chunk_tokens = 0;
    uint32_t cached_tokens_after = 0;
    uint64_t kv_read_bytes_per_member = 0;
    uint64_t kv_write_bytes_per_member = 0;
    uint64_t host_input_dma_bytes_per_member = 0;
    uint64_t host_output_dma_bytes_per_member = 0;
};

class ServingRequestContext
{
  public:
    ServingRequestContext(const DecodedProgram &program,
                          const ServingRequestIdentity &identity);

    bool resolve(std::string &reason);
    uint32_t rejectDetail() const { return reject_detail; }
    const ServingRequestIdentity &identity() const
    {
        return request_identity;
    }
    const std::vector<ServingPhaseStep> &plan() const
    { return steps; }
    size_t phaseIndex() const { return phase_index; }
    const ServingPhaseStep &step() const
    { return steps.at(phase_index); }
    bool finished() const { return phase_index >= steps.size(); }
    uint32_t requiredTokensAfterRound() const;
    const mesh_abi::AgentRequestProfile *requestProfile() const;
    const mesh_abi::AgentInstanceProfile *instanceProfile() const;

    KvAdmissionIntent admissionIntent(uint64_t tick) const;
    void beginPhase(KvEdgeInputs &edge);
    void noteCoreDrain() { core_drained = true; }
    void noteAppendTerminal(std::vector<bool> tokens_ok,
                            std::optional<std::array<uint8_t, 32>> digest);
    bool joinSatisfied() const;
    bool commitPhase(std::string &reason);
    KvOwnerTerminal ownerTerminal(KvTerminalStatus status) const;
    KvOwnerTerminal rollbackTerminal(KvTerminalStatus status,
                                     const KvRollbackToken &token) const;

  private:
    const mesh_abi::AgentInstanceProfile *select(uint16_t phase,
                                                 uint32_t kv_tokens_before,
                                                 std::string &reason) const;
    bool validatePrefill(const mesh_abi::AgentRequestProfile &request,
                         const mesh_abi::AgentInstanceProfile &instance,
                         uint32_t tokens, uint64_t host_input,
                         std::string &reason) const;
    bool validateDecode(const mesh_abi::AgentRequestProfile &request,
                        const mesh_abi::AgentInstanceProfile &instance,
                        uint32_t cursor, std::string &reason) const;
    bool validatePublish(const mesh_abi::AgentRequestProfile &request,
                         const mesh_abi::AgentInstanceProfile &instance,
                         uint32_t cursor, std::string &reason) const;
    uint32_t appendTokens(const ServingPhaseStep &item) const;
    bool fail(std::string &reason, const std::string &code,
              const std::string &detail) const;

    const DecodedProgram *program = nullptr;
    ServingRequestIdentity request_identity;
    std::vector<ServingPhaseStep> steps;
    size_t phase_index = 0;
    bool core_started = false;
    bool core_drained = false;
    bool append_terminal_seen = false;
    bool append_prefix_complete = false;
    mutable uint32_t reject_detail = 0;
    std::optional<std::array<uint8_t, 32>> content_digest;
};

}
}

#endif

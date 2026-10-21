#ifndef DEV_AI_MESH_AGENT_SQ_ADMISSION_HH
#define DEV_AI_MESH_AGENT_SQ_ADMISSION_HH

#include <memory>
#include <optional>
#include <stdexcept>
#include "base/checkpoint_fields.hh"
#include "dev/ai_mesh/generated/agent_protocol_abi.hh"

namespace gem5::ai_mesh
{
struct SqAdmissionResult
{
    agent_abi::DetailCode detail = static_cast<agent_abi::DetailCode>(0);
    uint16_t flags = 0;
    bool capacity_error = false;
    bool accepted() const { return !detail && !capacity_error; }
};

class AgentSqAdmission
{
  public:
    explicit AgentSqAdmission(size_t capacity);
    SqAdmissionResult accept(const agent_abi::SqDescriptor &sq);
    std::optional<uint16_t> opcode(uint64_t request_id) const;
    size_t size() const { return count; }
    template<class Archive> void serialize(Archive &archive, unsigned)
    {
        archive & count;
        if (count > capacity)
            throw std::runtime_error("E_CHECKPOINT_CONFIG: SQ identity capacity exceeded");
        for (size_t i = 0; i < count; ++i)
            checkpoint::fields(archive, identities[i].request, identities[i].opcode);
    }


  private:
    struct Identity { uint64_t request; uint16_t opcode; };
    const size_t capacity;
    std::unique_ptr<Identity[]> identities;
    size_t count = 0;
};

bool sequenceKeyedCqValid(const agent_abi::CqDescriptor &cq);
bool controlCqValid(const agent_abi::CqDescriptor &cq, uint16_t opcode);
}
#endif

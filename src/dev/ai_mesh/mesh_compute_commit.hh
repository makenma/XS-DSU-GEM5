#ifndef DEV_AI_MESH_MESH_COMPUTE_COMMIT_HH
#define DEV_AI_MESH_MESH_COMPUTE_COMMIT_HH

#include <cstdint>
#include <vector>

#include "dev/ai_mesh/runtime_key.hh"
#include "dev/ai_mesh/tensor_sram.hh"

namespace gem5
{
namespace ai_mesh
{

struct ComputeDigest
{
    RuntimeObjectKey command;
    uint32_t allocation_id = 0;
    uint64_t offset = 0;
    uint32_t digest_words[4] = {0, 0, 0, 0};
};

// One operand or result span of a compute command in absolute SRAM
// coordinates; the two callers (static program, MoE overlay) resolve their own
// view identities and hand the spans over.
struct ComputeSpan
{
    uint64_t offset = 0;
    uint64_t span = 0;
};

struct ComputeCommitRequest
{
    RuntimeObjectKey command;
    uint16_t opcode = 0;
    const uint8_t *attributes = nullptr;
    uint32_t attribute_bytes = 0;
    std::vector<ComputeSpan> inputs;
    std::vector<ComputeSpan> results;
    uint32_t result_allocation_id = 0;
    std::vector<uint32_t> valid_allocations;
    // A combine with exactly one contributor is an identity copy: the result
    // repeats the gather row byte for byte instead of a derived stream.
    bool identity_copy = false;
};

// Shared compute commit: reads the operand spans, derives the deterministic
// semantic digest, materializes the FUNCTIONAL_BYTES result over the result
// spans and marks the result allocations valid (spec 17.2.12/13).
class ComputeCommitter
{
  public:
    ComputeCommitter(TensorSram *sram, std::vector<ComputeDigest> *digests)
        : sram(sram), digests(digests)
    {
    }

    void commit(const ComputeCommitRequest &request) const;

  private:
    TensorSram *sram = nullptr;
    std::vector<ComputeDigest> *digests = nullptr;
};

} // namespace ai_mesh
} // namespace gem5

#endif

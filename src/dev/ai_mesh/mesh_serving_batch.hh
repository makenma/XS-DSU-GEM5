#ifndef DEV_AI_MESH_MESH_SERVING_BATCH_HH
#define DEV_AI_MESH_MESH_SERVING_BATCH_HH

#include <memory>

#include "dev/ai_mesh/mesh_serving_capacity.hh"
#include "dev/ai_mesh/npu_request_executor.hh"
#include "dev/ai_mesh/serving_instance_binding.hh"

namespace gem5::ai_mesh
{

struct ServingBatchMember
{
    const NpuExecutionRequest *request = nullptr;
    uint32_t logical_source_rank = 0;
    KvOwnedSlot owned_slot;
    std::optional<KvRuntimeView> kv_view;
    KvPath path = KvPath::InitialPrefill;
    uint32_t phase_ordinal = 0;
};

struct FrozenMemberBinding
{
    uint32_t ordinal = 0;
    uint64_t request_id = 0;
    uint32_t generation = 0;
    uint32_t symbol_id = 0;
    uint16_t kind = 0;
    uint32_t slot_id = 0;
    uint64_t capacity_bytes = 0;
    uint64_t address = 0;
    ServingByteWindow read;
    ServingByteWindow write;
    std::optional<KvRuntimeView> kv_view;
};

struct FrozenWeightBinding
{
    agent_abi::BindingRecord binding;
    uint32_t slot_id = 0;
    uint64_t capacity_bytes = 0;
};

enum class BatchIntervalKind { Input, Output, Metadata, Kv, Weight, Workspace };

struct FrozenBatchInterval
{
    uint64_t address = 0;
    uint64_t end = 0;
    BatchIntervalKind kind = BatchIntervalKind::Input;
    uint32_t ordinal = 0;
    uint32_t symbol_id = 0;
    std::optional<KvOwnedSlot> owned_slot;
};

class MeshServingBatch
{
  public:
    enum class FreezeResult { Frozen, Busy, NoCapacity, Invalid };
    explicit MeshServingBatch(const ServingCapacityRequirement &capacity);
    FreezeResult freeze(std::shared_ptr<const MeshProgramAdmission> admission,
                        const mesh_abi::AgentInstanceProfile &instance,
                        const ServingBatchMember *members, size_t count,
                        std::string &reason);
    void release();
    bool frozen() const { return published; }
    size_t memberBindingCount() const { return published ? member_count : 0; }
    size_t weightBindingCount() const { return published ? weight_count : 0; }
    size_t intervalCount() const { return published ? interval_count : 0; }
    const FrozenMemberBinding *memberBinding(size_t index) const
    { return published && index < member_count ? &member_table[index] : nullptr; }
    const FrozenWeightBinding *weightBinding(size_t index) const
    { return published && index < weight_count ? &weight_table[index] : nullptr; }
    const FrozenBatchInterval *interval(size_t index) const
    { return published && index < interval_count ? &interval_table[index] : nullptr; }
    const ServingInstanceBinding &executionBinding() const { return execution_binding; }

  private:
    ServingCapacityRequirement capacity;
    std::unique_ptr<FrozenMemberBinding[]> member_table;
    std::unique_ptr<FrozenWeightBinding[]> weight_table;
    std::unique_ptr<FrozenBatchInterval[]> interval_table;
    size_t member_count = 0;
    size_t weight_count = 0;
    size_t interval_count = 0;
    bool published = false;
    ServingInstanceBinding execution_binding;
};
}
#endif

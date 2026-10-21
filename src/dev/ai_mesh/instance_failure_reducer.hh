#ifndef DEV_AI_MESH_INSTANCE_FAILURE_REDUCER_HH
#define DEV_AI_MESH_INSTANCE_FAILURE_REDUCER_HH

#include <optional>
#include <vector>
#include "dev/ai_mesh/mesh_error_candidate.hh"

namespace gem5::ai_mesh
{
struct BatchFailureRecord
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t batch_id = 0;
    uint64_t program_instance_id = 0;
    ErrorCandidate first;
    std::vector<uint64_t> cancel_tombstone_snapshot;
};

class InstanceFailureReducer
{
  public:
    InstanceFailureReducer(size_t batch_capacity, size_t member_capacity);
    GEM5_CHECKPOINT_FIELDS(rows, committed_edge)
    void configureMemberCapacity(size_t capacity)
    {
        for (const auto &row : rows)
            if (row.members.size() > capacity)
                throw std::runtime_error("failure population exceeds configured capacity");
        member_capacity = capacity;
    }

    bool begin(uint64_t batch, uint64_t instance, const std::vector<uint64_t> &members);
    bool stage(uint64_t instance, const ErrorCandidate &candidate);
    bool cancel(uint64_t instance, uint64_t request, uint64_t tick);
    bool commitEdge(uint64_t tick);
    void release(uint64_t instance);
    const BatchFailureRecord *failure(uint64_t instance) const;
    uint64_t firstEdgeCandidateCount(uint64_t instance) const;

  private:
    struct Member
    {
        GEM5_CHECKPOINT_AGGREGATE()
        uint64_t request = 0;
        std::optional<uint64_t> cancel_tick;
    };
    struct Row
    {
        GEM5_CHECKPOINT_AGGREGATE()
        std::vector<Member> members;
        BatchFailureRecord record;
        std::optional<ErrorCandidate> pending;
        uint64_t pending_count = 0;
        uint64_t first_count = 0;
        bool active = false;
        bool sealed = false;
    };
    size_t member_capacity;
    std::vector<Row> rows;
    std::optional<uint64_t> committed_edge;
};
}
#endif

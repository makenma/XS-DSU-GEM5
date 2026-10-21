#ifndef DEV_AI_MESH_SERVING_PHASE_FACT_HH
#define DEV_AI_MESH_SERVING_PHASE_FACT_HH

#include <cstdint>
#include "base/checkpoint_fields.hh"

namespace gem5::ai_mesh
{
struct ServingPhaseFact
{
    GEM5_CHECKPOINT_AGGREGATE()
    uint64_t request_id = 0;
    uint32_t request_generation = 0;
    uint64_t instance_generation = 0;
    uint32_t phase = 0;
    uint32_t instance_profile_id = 0;
    uint32_t mesh_profile_id = 0;
    uint32_t kv_tokens_before = 0;
    uint32_t append_tokens = 0;
    uint32_t accepted_descriptors = 0;
    uint32_t terminal_descriptors = 0;
    uint64_t core_drain_tick = 0;
    uint64_t append_terminal_tick = 0;
    uint64_t commit_tick = 0;
};
}
#endif

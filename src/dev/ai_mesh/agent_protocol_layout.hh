#ifndef DEV_AI_MESH_AGENT_PROTOCOL_LAYOUT_HH
#define DEV_AI_MESH_AGENT_PROTOCOL_LAYOUT_HH

#include <cstdint>

#include "dev/ai_mesh/agent_runtime_types.hh"

namespace gem5
{
namespace ai_mesh
{

class AgentProtocolLayout
{
  public:
    AgentProtocolLayout(uint64_t sqBase, uint32_t sqDepth,
                        uint64_t cqBase, uint32_t cqDepth);

    uint64_t sqAddress(SqSeq sequence) const;
    uint64_t cqAddress(CqSeq sequence) const;
    uint64_t sqSpanBytes() const { return sqSpan; }
    uint64_t cqSpanBytes() const { return cqSpan; }

  private:
    uint64_t sqBase;
    uint64_t cqBase;
    uint32_t sqDepth;
    uint32_t cqDepth;
    uint64_t sqSpan;
    uint64_t cqSpan;
};

}
}

#endif

#ifndef DEV_AI_MESH_AGENT_RING_STATE_HH
#define DEV_AI_MESH_AGENT_RING_STATE_HH

#include <cstdint>
#include <optional>

#include "dev/ai_mesh/agent_runtime_types.hh"

namespace gem5
{
namespace ai_mesh
{

class RingGeometry
{
  public:
    explicit RingGeometry(uint32_t depth);

    uint32_t depth() const { return _depth; }

    template <class Sequence>
    uint32_t slot(Sequence sequence) const
    {
        return static_cast<uint32_t>(sequence.value() % _depth);
    }

    template <class Sequence>
    uint64_t generation(Sequence sequence) const
    {
        return sequence.value() / _depth;
    }

  private:
    uint32_t _depth;
};

template <class Sequence>
class RingState
{
  public:
    explicit RingState(uint32_t depth) : _geometry(depth) {}

    const RingGeometry &geometry() const { return _geometry; }
    Sequence consumer() const { return _consumer; }
    Sequence producer() const { return _producer; }

    uint64_t occupancy() const
    {
        return _producer.value() - _consumer.value();
    }

    uint64_t available() const
    {
        return _geometry.depth() - occupancy();
    }

    std::optional<Sequence> reserve(uint64_t count)
    {
        if (count == 0 || count > available())
            return std::nullopt;
        const auto next = checkedSequenceAdd(_producer, count);
        if (!next)
            return std::nullopt;
        const Sequence begin = _producer;
        _producer = *next;
        return begin;
    }

    bool consume(uint64_t count)
    {
        if (count == 0 || count > occupancy())
            return false;
        const auto next = checkedSequenceAdd(_consumer, count);
        if (!next)
            return false;
        _consumer = *next;
        return true;
    }

  private:
    RingGeometry _geometry;
    Sequence _consumer;
    Sequence _producer;
};

}
}

#endif

#ifndef DEV_AI_MESH_PUBLISH_SEGMENT_LEDGER_HH
#define DEV_AI_MESH_PUBLISH_SEGMENT_LEDGER_HH

#include <cstdint>
#include "base/checkpoint_fields.hh"
#include <functional>
#include <optional>
#include <stdexcept>
#include <vector>

namespace gem5::ai_mesh
{
struct PublishSegmentFact
{
    GEM5_CHECKPOINT_AGGREGATE()
    enum class Kind : uint8_t { Issue, Okay, Error, Suppress };
    uint64_t request = 0;
    uint64_t instance = 0;
    uint32_t descriptor = 0;
    uint64_t offset = 0;
    uint64_t bytes = 0;
    uint64_t tick = 0;
    Kind kind = Kind::Issue;
};

struct PublishDescriptorPlan
{
    uint32_t descriptor = 0;
    uint64_t request = 0;
    uint64_t offset = 0;
    uint64_t bytes = 0;
};

class PublishSegmentLedger
{
  public:
    enum class Decision { Issue, Wait, Suppress, Stop };
    explicit PublishSegmentLedger(std::vector<PublishDescriptorPlan> plans);
    using Sink = std::function<void(PublishSegmentFact)>;
    void setSink(Sink sink) { fact_sink = std::move(sink); }
    bool contains(uint32_t descriptor) const;
    Decision decision(uint32_t descriptor, uint64_t tick) const;
    bool issue(uint32_t descriptor, uint64_t offset, uint64_t bytes, uint64_t tick);
    bool commit(uint32_t descriptor, uint64_t offset, uint64_t bytes, bool okay, uint64_t tick);
    bool suppress(uint32_t descriptor, uint64_t tick);
    bool cancel(uint64_t request, uint64_t tick);
    void fail(uint64_t tick);
    uint64_t prefix(uint64_t request) const;
    bool started(uint32_t descriptor) const;
    bool suppressed(uint32_t descriptor) const;

  private:
    struct Entry
    {
        PublishDescriptorPlan plan;
        uint64_t committed = 0;
        uint64_t inflight = 0;
        uint64_t issue_tick = 0;
        std::optional<uint64_t> cancel_tick;
        std::optional<uint64_t> commit_tick;
        bool started = false;
        bool suppressed = false;
        bool failed = false;
    };
    std::vector<Entry> entries;
    std::optional<uint64_t> failure_tick;
    Sink fact_sink;
};
}
#endif

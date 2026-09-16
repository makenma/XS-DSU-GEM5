#ifndef __MEM_CACHE_CHI_BASE_CHI_PIPELINE_HH__
#define __MEM_CACHE_CHI_BASE_CHI_PIPELINE_HH__

#include <array>
#include <cstddef>
#include <deque>
#include <utility>
#include <variant>

#include "mem/cache/CHI/base/ChiChannel.hh"

namespace gem5::Chi
{

// Common flit-pipeline storage and clock driver. A derived component only
// supplies its stage functions; queue traversal, reverse stage dispatch and
// ordered retirement are shared here.
template <class Derived, std::size_t NumStages,
          std::size_t NumQueues = NUM_CHANNELS>
class ChiPipeline
{
  protected:
    using StageFunction = void (Derived::*)(FlitVariant &);
    using StageFunctions = std::array<StageFunction, NumStages>;
    using PipelineQueue = std::deque<FlitVariant>;
    using PipelineQueues = std::array<PipelineQueue, NumQueues>;

    explicit ChiPipeline(StageFunctions functions)
        : stageFunctions(std::move(functions))
    {}

    static BaseFlit &
    baseFlit(FlitVariant &flit)
    {
        return std::visit(
            [](auto &raw) -> BaseFlit & { return raw; }, flit);
    }

    static const BaseFlit &
    baseFlit(const FlitVariant &flit)
    {
        return std::visit(
            [](const auto &raw) -> const BaseFlit & { return raw; }, flit);
    }

    template <class Retire>
    void
    drivePipeline(std::size_t queue_index, Retire &&retire)
    {
        PipelineQueue &queue = pipelineQueues.at(queue_index);
        advancePipelineQueue(queue);
        retireCompleted(queue, retire);
    }

    void enqueuePipeline(std::size_t queue, FlitVariant flit)
    {
        pipelineQueues.at(queue).push_back(std::move(flit));
    }

    bool hasPipelineWork() const
    {
        for (const PipelineQueue &queue : pipelineQueues) {
            if (!queue.empty())
                return true;
        }
        return false;
    }

    std::size_t pipelineSize(std::size_t queue = 0) const
    {
        return pipelineQueues.at(queue).size();
    }

  private:
    static bool
    isComplete(const FlitVariant &flit)
    {
        return baseFlit(flit).stage >= NumStages;
    }

    void
    advancePipeline(FlitVariant &flit)
    {
        if (isComplete(flit))
            return;

        Derived &derived = static_cast<Derived &>(*this);
        // Stage functions inspect BaseFlit::stage themselves. Reverse order
        // prevents a flit advanced by H0 from also running H1 in this cycle.
        for (auto fn = stageFunctions.rbegin();
             fn != stageFunctions.rend(); ++fn) {
            if (*fn)
                (derived.**fn)(flit);
        }
    }

    void
    advancePipelineQueue(PipelineQueue &queue)
    {
        for (FlitVariant &flit : queue)
            advancePipeline(flit);
    }

    template <class Retire>
    void
    retireCompleted(PipelineQueue &queue, Retire &&retire)
    {
        while (!queue.empty() && isComplete(queue.front())) {
            FlitVariant flit = std::move(queue.front());
            queue.pop_front();

            // A false result means downstream backpressure. Restore the flit
            // at the head and preserve pipeline order.
            if (!retire(flit)) {
                queue.push_front(std::move(flit));
                break;
            }
        }
    }

    PipelineQueues pipelineQueues{};
    const StageFunctions stageFunctions;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_BASE_CHI_PIPELINE_HH__

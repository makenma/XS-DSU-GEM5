#ifndef __MEM_RUBY_NETWORK_GARNET_LANE_ARBITER_HH__
#define __MEM_RUBY_NETWORK_GARNET_LANE_ARBITER_HH__

#include <array>
#include <cassert>
#include <optional>
#include <vector>

#include "mem/ruby/network/garnet/DualLane.hh"

namespace gem5::ruby::garnet
{

struct SwitchGrant
{
    size_t slot;
    int vc;
};

class LaneArbiter
{
  public:
    LaneArbiter(const std::vector<int> &ports, int outputs, int vcs)
        : nextInput(outputs, 0), numVcs(vcs)
    {
        assert(outputs >= 0 && vcs > 0);
        for (const auto port : ports)
            inputs.push_back({port, 0, -1, -1});
    }

    size_t size() const { return inputs.size(); }
    int inputPort(size_t slot) const { return inputs.at(slot).port; }
    int firstVc(size_t slot) const { return inputs.at(slot).nextVc; }
    int pendingVc(size_t slot) const { return inputs.at(slot).vc; }

    void request(size_t slot, int output, int vc)
    {
        assert(output >= 0 && output < static_cast<int>(nextInput.size()));
        assert(vc >= 0 && vc < numVcs);
        auto &input = inputs.at(slot);
        assert(input.output == -1);
        input.output = output;
        input.vc = vc;
    }

    std::optional<SwitchGrant> candidate(int output) const
    {
        const auto start = nextInput.at(output);
        for (size_t offset = 0; offset < inputs.size(); ++offset) {
            const auto slot = (start + offset) % inputs.size();
            if (inputs[slot].output == output)
                return SwitchGrant{slot, inputs[slot].vc};
        }
        return std::nullopt;
    }

    void commit(int output, const SwitchGrant &grant)
    {
        auto &input = inputs.at(grant.slot);
        assert(input.output == output && input.vc == grant.vc);
        nextInput.at(output) = (grant.slot + 1) % inputs.size();
        input.nextVc = (grant.vc + 1) % numVcs;
        input.output = -1;
        input.vc = -1;
    }

    void clearRequests()
    {
        for (auto &input : inputs) {
            input.output = -1;
            input.vc = -1;
        }
    }

  private:
    struct InputState
    {
        int port;
        int nextVc;
        int output;
        int vc;
    };

    std::vector<InputState> inputs;
    std::vector<size_t> nextInput;
    int numVcs;
};

class LaneMergeArbiter
{
  public:
    std::optional<LaneId> select(const std::array<bool, 2> &ready) const
    {
        if (ready[nextLane])
            return nextLane;
        const LaneId other = nextLane ^ 1;
        return ready[other] ? std::optional<LaneId>(other) : std::nullopt;
    }

    void commit(LaneId lane)
    {
        assert(lane <= MaxLaneId);
        nextLane = lane ^ 1;
    }

  private:
    LaneId nextLane = 0;
};

}

#endif

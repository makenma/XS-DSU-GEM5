#ifndef __MEM_CACHE_CHI_ROUTER_TESTER_HH__
#define __MEM_CACHE_CHI_ROUTER_TESTER_HH__

#include <fstream>
#include <memory>
#include <vector>

#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "params/ChiRouterTester.hh"
#include "sim/eventq.hh"

namespace gem5::Chi
{
class ChiRouterRefModel;

/** Independent endpoints; no router-private state drives traffic decisions. */
class ChiRouterTester : public ClockedObject, public ruby::Consumer
{
  public:
    PARAMS(ChiRouterTester);
    explicit ChiRouterTester(const Params &p);
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;
    Port &getPort(const std::string &name, PortID idx) override;

  private:
    struct Transaction {
        unsigned source, output, channel;
        uint32_t target;
        uint64_t due, accepted = 0;
        bool sent = false, received = false, tx = false;
    };
    void tick();
    void setup();
    void inject(unsigned index);
    void drain(unsigned output, ChannelType channel, bool tx);
    void record(char event, unsigned id, unsigned port);
    ChiRouterRefModel *router;
    std::vector<std::unique_ptr<ChiCommonPort>> sources, sinks;
    std::vector<Transaction> transactions;
    std::array<std::array<std::deque<unsigned>, 4>, 24> pending;
    EventFunctionWrapper tickEvent;
    std::ofstream trace;
    const std::string scenario;
    const unsigned repetitions;
    const uint64_t limit;
    const bool earlySchedule;
    uint64_t cycle = 0, digest = 1469598103934665603ULL;
    unsigned accepted = 0, received = 0;
    bool finished = false;
};
} // namespace gem5::Chi
#endif

#ifndef __MEM_AXI_AXI_TRACE_TESTER_HH__
#define __MEM_AXI_AXI_TRACE_TESTER_HH__

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "mem/axi/axi_garnet_endpoint.hh"
#include "mem/ruby/common/Consumer.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct AxiTraceTesterParams;

namespace axi
{

class AxiTraceTester : public ClockedObject, public ruby::Consumer
{
  public:
    using Params = AxiTraceTesterParams;

    explicit AxiTraceTester(const Params &p);
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;

  private:
    enum class Kind { Write, Read };
    enum class StrobeMode { Full, Alternating };

    struct Transaction
    {
        Kind kind = Kind::Write;
        size_t sourceIndex = 0;
        size_t targetIndex = 0;
        AxiAddressRequest request;
        bool wBeforeAw = false;
        uint8_t dataSeed = 0;
        StrobeMode strobeMode = StrobeMode::Full;
        bool addressAccepted = false;
        uint16_t nextW = 0;
        uint16_t responses = 0;
        std::vector<AxiWBeat> writeBeats;
        Tick lastWAcceptedTick = 0;
    };

    Transaction parseTransaction(const std::string &spec) const;
    AxiWBeat makeWBeat(const Transaction &txn, uint16_t index) const;
    void driveWrite(Transaction &txn);
    void driveRead(Transaction &txn);
    void checkB(Transaction &txn);
    void checkR(Transaction &txn);
    void commitShadow(const Transaction &txn);
    void checkTargetMemory(const Transaction &txn) const;
    uint8_t shadowByte(uint64_t address) const;
    bool allAdaptersIdle() const;
    void writeResult() const;

    std::vector<AxiInitiatorAdapter *> initiators;
    std::vector<AxiTargetAdapter *> targets;
    std::vector<uint32_t> targetNodes;
    std::vector<Transaction> transactions;
    const std::string caseName;
    const std::string resultJson;
    const uint32_t dataBusBytes;
    const uint32_t drainCycles;

    std::map<uint64_t, uint8_t> shadowMemory;
    size_t currentTransaction = 0;
    uint32_t quietCycles = 0;
    uint64_t writesCompleted = 0;
    uint64_t readsCompleted = 0;
    uint64_t wBeatsAccepted = 0;
    uint64_t rBeatsConsumed = 0;
    size_t maxOrphanTransactions = 0;
    bool exitRequested = false;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_TRACE_TESTER_HH__

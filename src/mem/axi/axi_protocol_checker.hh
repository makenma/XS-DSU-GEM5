#ifndef __MEM_AXI_AXI_PROTOCOL_CHECKER_HH__
#define __MEM_AXI_AXI_PROTOCOL_CHECKER_HH__

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace axi
{

struct AxiCheckerTransaction
{
    AxiCommonMeta meta;
    bool read = false;
    uint16_t beatCount = 0;
    AxiResp response = AxiResp::Okay;
    std::vector<std::vector<uint8_t>> expectedData;
};

struct AxiCheckerEvent
{
    AxiChannel channel = AxiChannel::Aw;
    AxiCommonMeta meta;
    uint16_t beatIndex = 0;
    uint16_t beatCount = 0;
    bool last = false;
    AxiResp response = AxiResp::Okay;
    uint64_t payloadDigest = 0;
    std::vector<uint8_t> functionalData;
};

/**
 * Independent event-stream checker.  It intentionally does not call the DUT
 * burst validator, lane mapper, pairing state, or ordering helpers.
 */
class AxiProtocolChecker
{
  public:
    bool addExpected(const AxiCheckerTransaction &transaction);
    bool observe(const AxiCheckerEvent &event);
    bool finish();

    bool okay() const { return _error.empty(); }
    const std::string &error() const { return _error; }

  private:
    struct State
    {
        AxiCheckerTransaction expected;
        bool addressSeen = false;
        bool responseSeen = false;
        std::vector<bool> beatsSeen;
    };

    using RetireKey = std::tuple<uint32_t, uint16_t, uint32_t, bool>;

    bool fail(const std::string &message);
    bool checkMeta(const AxiCommonMeta &actual,
                   const AxiCommonMeta &expected);
    bool checkData(const AxiCheckerEvent &event,
                   const std::vector<uint8_t> &expected);
    bool retire(const State &state);

    std::map<uint64_t, State> _states;
    std::map<RetireKey, uint64_t> _nextResponseSeq;
    std::string _error;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_PROTOCOL_CHECKER_HH__

#ifndef __MEM_AXI_AXI_VALIDATION_HH__
#define __MEM_AXI_AXI_VALIDATION_HH__

#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "mem/axi/axi_types.hh"

namespace gem5
{
namespace axi
{

enum class AxiValidationDisposition
{
    Okay,
    DecErr,
    ProtocolError
};

struct AxiValidationResult
{
    AxiValidationDisposition disposition =
        AxiValidationDisposition::ProtocolError;
    AxiResp response = AxiResp::Okay;
    uint64_t beatBytes = 0;
    uint64_t spanBytes = 0;
    uint64_t lastByteExclusive = 0;
    std::string error;

    bool okay() const
    { return disposition == AxiValidationDisposition::Okay; }
    bool decerr() const
    { return disposition == AxiValidationDisposition::DecErr; }
    bool protocolError() const
    { return disposition == AxiValidationDisposition::ProtocolError; }
};

AxiValidationResult validateAxiBurst(const AxiAddressRequest &request,
                                     uint32_t data_bus_bytes);

void requireValidAxiBurst(const AxiValidationResult &validation);

std::string validateAxiWriteBeat(const AxiAddressRequest &request,
                                 uint16_t beat_index,
                                 const AxiWBeat &beat,
                                 uint32_t data_bus_bytes);

void requireValidAxiWriteBeat(const AxiAddressRequest &request,
                              uint16_t beat_index,
                              const AxiWBeat &beat,
                              uint32_t data_bus_bytes);

uint64_t axiBeatAddress(const AxiAddressRequest &request,
                        uint16_t beat_index);

uint64_t axiLegalLaneMask(const AxiAddressRequest &request,
                          uint16_t beat_index,
                          uint32_t data_bus_bytes);

struct AxiDecodeResult
{
    uint32_t dstNode = 0;
    AxiResp response = AxiResp::DecErr;
};

class AxiAddressDecoder
{
  public:
    AxiAddressDecoder(std::vector<AxiRange> ranges,
                      uint32_t default_error_target);

    AxiDecodeResult decode(const AxiAddressRequest &request,
                           const AxiValidationResult &validation) const;
    const std::vector<AxiRange> &ranges() const { return _ranges; }
    uint32_t defaultErrorTarget() const { return _defaultErrorTarget; }

  private:
    std::vector<AxiRange> _ranges;
    uint32_t _defaultErrorTarget;
};

std::string validateAxiRanges(const std::vector<AxiRange> &ranges);

std::string validateAxiQuotaSums(
    const std::map<AxiEndpointKey, AxiQuota> &quotas,
    const AxiTargetCapacity &capacity);

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_VALIDATION_HH__

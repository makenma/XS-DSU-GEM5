#include "mem/axi/axi_validation.hh"

#include <algorithm>
#include <limits>
#include <sstream>
#include <stdexcept>

#include "base/logging.hh"

namespace gem5
{
namespace axi
{

namespace
{

AxiValidationResult
protocolError(const std::string &error)
{
    AxiValidationResult result;
    result.disposition = AxiValidationDisposition::ProtocolError;
    result.error = error;
    return result;
}

} // anonymous namespace

AxiValidationResult
validateAxiBurst(const AxiAddressRequest &request, uint32_t data_bus_bytes)
{
    if (request.beatCount < 1 || request.beatCount > 256)
        return protocolError("AXI beatCount must be in [1,256]");
    if (data_bus_bytes == 0 || data_bus_bytes > 64)
        return protocolError("AXI dataBusBytes must be in [1,64]");
    if (request.size >= 64)
        return protocolError("AXI SIZE shift is not representable");

    const uint64_t beat_bytes = uint64_t{1} << request.size;
    if (beat_bytes == 0 || beat_bytes > data_bus_bytes)
        return protocolError("AXI SIZE exceeds the configured data bus");

    AxiValidationResult result;
    result.beatBytes = beat_bytes;

    if (request.burst != AxiBurst::Incr) {
        result.disposition = AxiValidationDisposition::DecErr;
        result.response = AxiResp::DecErr;
        return result;
    }

    if (request.beatCount >
        std::numeric_limits<uint64_t>::max() / beat_bytes) {
        return protocolError("AXI INCR burst span overflows uint64");
    }
    result.spanBytes = request.beatCount * beat_bytes;
    if (result.spanBytes >
        std::numeric_limits<uint64_t>::max() - request.address) {
        return protocolError("AXI INCR last address overflows uint64");
    }
    result.lastByteExclusive = request.address + result.spanBytes;
    if ((request.address >> 12) !=
        ((result.lastByteExclusive - 1) >> 12)) {
        return protocolError("AXI INCR burst crosses a 4 KiB boundary");
    }

    if (request.address % beat_bytes != 0 || request.lock != 0 ||
        request.region != 0) {
        result.disposition = AxiValidationDisposition::DecErr;
        result.response = AxiResp::DecErr;
        return result;
    }

    result.disposition = AxiValidationDisposition::Okay;
    result.response = AxiResp::Okay;
    return result;
}

void
requireValidAxiBurst(const AxiValidationResult &validation)
{
    panic_if(validation.protocolError(), "AXI_PROTOCOL: %s",
             validation.error);
}

uint64_t
axiBeatAddress(const AxiAddressRequest &request, uint16_t beat_index)
{
    const uint64_t beat_bytes = uint64_t{1} << request.size;
    return request.address + static_cast<uint64_t>(beat_index) * beat_bytes;
}

uint64_t
axiLegalLaneMask(const AxiAddressRequest &request, uint16_t beat_index,
                 uint32_t data_bus_bytes)
{
    if (data_bus_bytes == 0 || data_bus_bytes > 64 || request.size >= 64)
        return 0;
    const uint64_t beat_bytes = uint64_t{1} << request.size;
    if (beat_bytes == 0 || beat_bytes > data_bus_bytes)
        return 0;
    const uint64_t address = axiBeatAddress(request, beat_index);
    const uint64_t lane_base = address % data_bus_bytes;
    if (lane_base + beat_bytes > data_bus_bytes)
        return 0;
    if (beat_bytes == 64)
        return std::numeric_limits<uint64_t>::max();
    return ((uint64_t{1} << beat_bytes) - 1) << lane_base;
}

std::string
validateAxiWriteBeat(const AxiAddressRequest &request, uint16_t beat_index,
                     const AxiWBeat &beat, uint32_t data_bus_bytes)
{
    if (beat_index >= request.beatCount)
        return "AXI W beat index is outside AW beatCount";
    if (beat.functionalData.size() != data_bus_bytes)
        return "AXI W functionalData must contain one full bus word";

    const bool expected_last = beat_index + 1 == request.beatCount;
    if (beat.last != expected_last)
        return expected_last ? "AXI WLAST missing on final beat" :
                               "AXI WLAST asserted before final beat";

    const uint64_t legal_mask = axiLegalLaneMask(
        request, beat_index, data_bus_bytes);
    if (legal_mask == 0)
        return "AXI W beat has no representable legal lane mask";
    if ((beat.byteStrobe & ~legal_mask) != 0)
        return "AXI WSTRB selects a lane outside the transfer";
    return {};
}

void
requireValidAxiWriteBeat(const AxiAddressRequest &request,
                         uint16_t beat_index, const AxiWBeat &beat,
                         uint32_t data_bus_bytes)
{
    const std::string error = validateAxiWriteBeat(
        request, beat_index, beat, data_bus_bytes);
    panic_if(!error.empty(), "AXI_PROTOCOL: %s", error);
}

std::string
validateAxiRanges(const std::vector<AxiRange> &ranges)
{
    uint64_t previous_end = 0;
    bool first = true;
    for (size_t i = 0; i < ranges.size(); ++i) {
        const auto &range = ranges[i];
        if (range.start >= range.end) {
            std::ostringstream out;
            out << "AXI target range[" << i << "] must be non-empty";
            return out.str();
        }
        if (!first && range.start < previous_end) {
            std::ostringstream out;
            out << "AXI target range[" << i << "] overlaps its predecessor";
            return out.str();
        }
        first = false;
        previous_end = range.end;
    }
    return {};
}

AxiAddressDecoder::AxiAddressDecoder(std::vector<AxiRange> ranges,
                                     uint32_t default_error_target)
    : _ranges(std::move(ranges)),
      _defaultErrorTarget(default_error_target)
{
    std::sort(_ranges.begin(), _ranges.end(),
              [](const auto &lhs, const auto &rhs) {
                  return std::tie(lhs.start, lhs.end, lhs.dstNode) <
                         std::tie(rhs.start, rhs.end, rhs.dstNode);
              });
    const std::string error = validateAxiRanges(_ranges);
    if (!error.empty())
        throw std::invalid_argument(error);
}

AxiDecodeResult
AxiAddressDecoder::decode(const AxiAddressRequest &request,
                          const AxiValidationResult &validation) const
{
    if (!validation.okay())
        return {_defaultErrorTarget, AxiResp::DecErr};

    for (const auto &range : _ranges) {
        if (request.address >= range.start &&
            validation.lastByteExclusive <= range.end) {
            return {range.dstNode, AxiResp::Okay};
        }
    }
    return {_defaultErrorTarget, AxiResp::DecErr};
}

std::string
validateAxiQuotaSums(const std::map<AxiEndpointKey, AxiQuota> &quotas,
                     const AxiTargetCapacity &capacity)
{
    uint64_t write_contexts = 0;
    uint64_t write_beats = 0;
    uint64_t read_contexts = 0;
    uint64_t read_beats = 0;
    for (const auto &[source, quota] : quotas) {
        if (quota.writeContexts == 0 || quota.writeBeats == 0 ||
            quota.readContexts == 0 || quota.readBeats == 0) {
            std::ostringstream out;
            out << "AXI quota for source " << source.srcNode << ':'
                << source.srcPort << " must be positive";
            return out.str();
        }
        write_contexts += quota.writeContexts;
        write_beats += quota.writeBeats;
        read_contexts += quota.readContexts;
        read_beats += quota.readBeats;
    }
    if (write_contexts > capacity.writeContexts)
        return "AXI write context quota sum exceeds target pool";
    if (write_beats > capacity.writeAssemblyBeats)
        return "AXI write beat quota sum exceeds target pool";
    if (read_contexts > capacity.readContexts)
        return "AXI read context quota sum exceeds target pool";
    if (read_beats > capacity.readResponseBeats)
        return "AXI read beat quota sum exceeds target pool";
    return {};
}

} // namespace axi
} // namespace gem5

#include "dev/ai_mesh/gate3_axi_transfer.hh"

#include <algorithm>
#include <limits>
#include <stdexcept>

#include "mem/axi/axi_validation.hh"

namespace gem5
{
namespace ai_mesh
{

namespace
{

bool
powerOfTwo(uint32_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

uint32_t
largestBeat(uint64_t address, uint64_t remaining, uint32_t maximum)
{
    uint32_t beat = 1;
    while (beat <= maximum / 2 && beat <= remaining / 2 &&
           address % (beat * 2) == 0)
        beat *= 2;
    return beat;
}

uint8_t
sizeFor(uint32_t bytes)
{
    uint8_t size = 0;
    while ((uint32_t{1} << size) != bytes)
        ++size;
    return size;
}

}

Gate3AxiTransferPlanner::Gate3AxiTransferPlanner(
    uint32_t dataBusBytesValue, uint16_t maxBurstBeatsValue)
    : dataBusBytes(dataBusBytesValue), maxBurstBeats(maxBurstBeatsValue)
{
    if (!powerOfTwo(dataBusBytes) || dataBusBytes > 64)
        throw std::invalid_argument("AXI data bus bytes must be a power of two in [1,64]");
    if (maxBurstBeats == 0 || maxBurstBeats > 256)
        throw std::invalid_argument("AXI max burst beats must be in [1,256]");
}

std::vector<Gate3AxiSegment>
Gate3AxiTransferPlanner::plan(
    uint64_t address, uint64_t bytes, uint32_t axiId, uint8_t qos,
    uint32_t maxBeatBytes) const
{
    if (bytes == 0)
        throw std::invalid_argument("AXI transfer range must be non-empty");
    if (address > std::numeric_limits<uint64_t>::max() - bytes)
        throw std::invalid_argument("AXI transfer range overflows uint64");
    const uint32_t beatLimit = maxBeatBytes == 0 ? dataBusBytes : maxBeatBytes;
    if (!powerOfTwo(beatLimit) || beatLimit > dataBusBytes)
        throw std::invalid_argument("AXI maximum beat bytes is invalid");

    std::vector<Gate3AxiSegment> result;
    uint64_t current = address;
    uint64_t remaining = bytes;
    uint64_t logicalOffset = 0;
    while (remaining != 0) {
        const uint32_t beatBytes = largestBeat(current, remaining, beatLimit);
        const uint64_t pageBeats = (4096 - (current & 0xfff)) / beatBytes;
        const uint64_t rangeBeats = remaining / beatBytes;
        uint64_t beats = std::min<uint64_t>(
            maxBurstBeats, std::min(pageBeats, rangeBeats));
        if (beatBytes < beatLimit && current % (beatBytes * 2) != 0)
            beats = std::min<uint64_t>(beats, 1);
        if (beats == 0)
            throw std::logic_error("AXI exact transfer planner made no progress");

        Gate3AxiSegment segment;
        segment.request.axiId = axiId;
        segment.request.address = current;
        segment.request.beatCount = static_cast<uint16_t>(beats);
        segment.request.size = sizeFor(beatBytes);
        segment.request.burst = axi::AxiBurst::Incr;
        segment.request.qos = qos;
        segment.logicalOffset = logicalOffset;
        segment.logicalBytes = beats * beatBytes;
        const auto validation = axi::validateAxiBurst(
            segment.request, dataBusBytes);
        if (!validation.okay())
            throw std::logic_error("AXI exact transfer planner produced an invalid burst");
        result.push_back(segment);
        current += segment.logicalBytes;
        logicalOffset += segment.logicalBytes;
        remaining -= segment.logicalBytes;
    }
    return result;
}

std::vector<axi::AxiWBeat>
Gate3AxiTransferPlanner::packWrite(
    const std::vector<uint8_t> &data,
    const Gate3AxiSegment &segment) const
{
    if (segment.logicalOffset > data.size() ||
        segment.logicalBytes > data.size() - segment.logicalOffset)
        throw std::invalid_argument("AXI write segment exceeds logical data");
    std::vector<axi::AxiWBeat> beats(segment.request.beatCount);
    uint64_t source = segment.logicalOffset;
    const uint64_t beatBytes = uint64_t{1} << segment.request.size;
    for (uint16_t index = 0; index < segment.request.beatCount; ++index) {
        auto &beat = beats[index];
        beat.functionalData.assign(dataBusBytes, 0);
        const uint64_t address = axi::axiBeatAddress(segment.request, index);
        const uint64_t lane = address % dataBusBytes;
        std::copy_n(data.begin() + source, beatBytes,
                    beat.functionalData.begin() + lane);
        beat.byteStrobe = axi::axiLegalLaneMask(
            segment.request, index, dataBusBytes);
        beat.last = index + 1 == segment.request.beatCount;
        beat.payloadDigest = axi::payloadDigest(beat.functionalData);
        source += beatBytes;
    }
    return beats;
}

void
Gate3AxiTransferPlanner::appendReadBeat(
    const Gate3AxiSegment &segment, uint16_t beatIndex,
    const std::vector<uint8_t> &functionalData,
    std::vector<uint8_t> &output) const
{
    if (beatIndex >= segment.request.beatCount ||
        functionalData.size() != dataBusBytes)
        throw std::invalid_argument("AXI read beat does not match segment");
    const uint64_t beatBytes = uint64_t{1} << segment.request.size;
    const uint64_t address = axi::axiBeatAddress(segment.request, beatIndex);
    const uint64_t lane = address % dataBusBytes;
    output.insert(output.end(), functionalData.begin() + lane,
                  functionalData.begin() + lane + beatBytes);
}

}
}

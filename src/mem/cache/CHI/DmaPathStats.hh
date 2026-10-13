#ifndef CHI_DMA_PATH_STATS_HH
#define CHI_DMA_PATH_STATS_HH
#include "base/statistics.hh"
namespace gem5::Chi
{
struct DmaPathStats : statistics::Group
{
    statistics::Scalar packets, responses, chiTransactions, readBytes, writeBytes;
    statistics::Scalar reqRetry, respRetry, creditStalls, protocolRetry;
    statistics::Scalar entries, entryHighWater, outstanding, chiHighWater;
    statistics::Scalar latencyTicks, occupancyTicks, errors, pioRequests;
    statistics::Scalar reorderedData, chiReadBytes, chiWriteBytes;
    explicit DmaPathStats(statistics::Group *p) : statistics::Group(p),
        ADD_STAT(packets, statistics::units::Count::get(), "Accepted Packet/REQ"),
        ADD_STAT(responses, statistics::units::Count::get(), "Accepted completions"),
        ADD_STAT(chiTransactions, statistics::units::Count::get(), "Injected CHI REQ including retries"),
        ADD_STAT(readBytes, statistics::units::Byte::get(), "Original read payload"),
        ADD_STAT(writeBytes, statistics::units::Byte::get(), "Original write payload"),
        ADD_STAT(reqRetry, statistics::units::Count::get(), "Packet request rejection"),
        ADD_STAT(respRetry, statistics::units::Count::get(), "Packet response rejection"),
        ADD_STAT(creditStalls, statistics::units::Count::get(), "CHI send attempts without credit"),
        ADD_STAT(protocolRetry, statistics::units::Count::get(), "CHI RetryAck"),
        ADD_STAT(entries, statistics::units::Count::get(), "Current parent entries"),
        ADD_STAT(entryHighWater, statistics::units::Count::get(), "Peak parent entries"),
        ADD_STAT(outstanding, statistics::units::Count::get(), "Current actual CHI transactions"),
        ADD_STAT(chiHighWater, statistics::units::Count::get(), "Peak actual CHI transactions"),
        ADD_STAT(latencyTicks, statistics::units::Tick::get(), "Accepted request to response sum"),
        ADD_STAT(occupancyTicks, statistics::units::Tick::get(), "Entry occupied tick integral"),
        ADD_STAT(errors, statistics::units::Count::get(), "Error completions"),
        ADD_STAT(pioRequests, statistics::units::Count::get(), "Accepted PIO sends / injected RNF MMIO REQ"),
        ADD_STAT(reorderedData, statistics::units::Count::get(), "Terminal DAT beat arrived before earlier beats"),
        ADD_STAT(chiReadBytes, statistics::units::Byte::get(), "Injected read transaction payload including memory RMW"),
        ADD_STAT(chiWriteBytes, statistics::units::Byte::get(), "Injected write transaction payload")
    {}
};
}
#endif

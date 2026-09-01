#ifndef __MEM_AXI_AXI_DETERMINISM_HH__
#define __MEM_AXI_AXI_DETERMINISM_HH__

#include <cstdint>

namespace gem5
{
namespace axi
{

// Stable numeric tables shared with tests/gem5/axi_garnet/schema_constants.json.
// Values are explicit because trace/replay hashes must not depend on implicit
// C++ enum numbering.
namespace deterministic
{

constexpr uint32_t SchemaVersion = 1;

enum class Stage : uint64_t
{
    Workload = 1,
    MemoryLatency = 2,
    ChannelSkew = 3,
    ResponseStall = 4,
    FaultInjection = 5,
};

enum class DrawKind : uint64_t
{
    TransactionKind = 1,
    Source = 2,
    Target = 3,
    AxiId = 4,
    BeatCount = 5,
    TransferSize = 6,
    Strobe = 7,
    Arrival = 8,
    Latency = 9,
    Fault = 10,
    ChannelDelay = 11,
    DataSeed = 12,
    Qos = 13,
};

enum class TracePhase : uint64_t
{
    Drive = 10,
    Response = 20,
    Final = 30,
};

enum class TraceEvent : uint64_t
{
    AddressAccepted = 10,
    WriteBeatAccepted = 11,
    WriteResponseRetired = 20,
    ReadBeatRetired = 21,
    Quiescent = 30,
};

uint64_t splitmix64(uint64_t value);
uint64_t keyedRandom(uint64_t master_seed, uint64_t key_id,
                     Stage stage, uint64_t beat_index, DrawKind draw_kind);

} // namespace deterministic
} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_DETERMINISM_HH__

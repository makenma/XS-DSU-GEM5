#ifndef DEV_SIMPLE_DMA_HH
#define DEV_SIMPLE_DMA_HH
#include <memory>
#include <vector>
#include "base/statistics.hh"
#include "dev/dma_device.hh"
#include "params/SimpleDma.hh"

namespace gem5
{
class SimpleDma : public DmaDevice
{
  public:
    explicit SimpleDma(const SimpleDmaParams &p);
    DrainState drain() override;
    bool idle() const;
    void serialize(CheckpointOut &) const override;
  private:
    struct Chunk {
        std::vector<uint8_t> data;
        uint64_t offset = 0;
        bool active = false;
        EventFunctionWrapper readDone, writeDone;
        Chunk(SimpleDma &d, unsigned id, unsigned bytes);
    };
    Addr base, window;
    Tick latency;
    AddrRange aperture;
    unsigned chunkBytes;
    uint64_t src = 0, dst = 0, length = 0, scratch = 0;
    uint64_t jobSrc = 0, jobDst = 0, jobLength = 0, issued = 0;
    uint64_t bytesDone = 0, jobId = 0;
    uint32_t status = 0, error = 0, active = 0;
    bool failed = false;
    std::vector<std::unique_ptr<Chunk>> chunks;
    EventFunctionWrapper workEvent;
    struct Stats : statistics::Group {
        statistics::Scalar accesses, polls, jobs, startsRejected, errors;
        statistics::Scalar chunksIssued, activeChunks, chunkHighWater;
        statistics::Scalar bytesWritten, latencyTicks;
        explicit Stats(SimpleDma *d);
    } stats;
    Tick jobStart = 0;
    AddrRangeList getAddrRanges() const override;
    Tick read(PacketPtr) override;
    Tick write(PacketPtr) override;
    Tick access(PacketPtr, bool);
    void fail(uint32_t code);
    void start();
    void pump();
    void readComplete(unsigned id);
    void writeComplete(unsigned id);
    void scheduleWork();
};
}
#endif

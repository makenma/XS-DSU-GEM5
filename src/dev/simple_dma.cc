#include "dev/simple_dma.hh"
#include <algorithm>
#include <limits>
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/DmaPath.hh"
#include "dev/simple_dma_regs.h"

namespace gem5
{
SimpleDma::Stats::Stats(SimpleDma *d) : statistics::Group(d),
    ADD_STAT(accesses, statistics::units::Count::get(), "PIO actions"),
    ADD_STAT(polls, statistics::units::Count::get(), "STATUS reads"),
    ADD_STAT(jobs, statistics::units::Count::get(), "Accepted jobs"),
    ADD_STAT(startsRejected, statistics::units::Count::get(), "Busy START"),
    ADD_STAT(errors, statistics::units::Count::get(), "Reported errors"),
    ADD_STAT(chunksIssued, statistics::units::Count::get(), "Chunks issued"),
    ADD_STAT(activeChunks, statistics::units::Count::get(), "Current chunks"),
    ADD_STAT(chunkHighWater, statistics::units::Count::get(), "Peak chunks"),
    ADD_STAT(bytesWritten, statistics::units::Byte::get(), "Committed bytes"),
    ADD_STAT(latencyTicks, statistics::units::Tick::get(), "Job latency sum")
{}

SimpleDma::Chunk::Chunk(SimpleDma &d, unsigned id, unsigned bytes)
    : data(bytes),
      readDone([&d, id]{ d.readComplete(id); }, d.name()+".readDone"),
      writeDone([&d, id]{ d.writeComplete(id); }, d.name()+".writeDone")
{}

SimpleDma::SimpleDma(const SimpleDmaParams &p) : DmaDevice(p),
    base(p.pio_addr), window(p.pio_size), latency(p.pio_latency),
    aperture(p.dma_range), chunkBytes(p.chunk_bytes),
    workEvent([this]{ pump(); }, name()+".work"), stats(this)
{
    fatal_if(!chunkBytes || chunkBytes > (1u<<20) || !p.parallel_chunks ||
             p.parallel_chunks > 64 || window < 0x38,
             "invalid SimpleDma finite resources");
    for (unsigned i=0; i<p.parallel_chunks; ++i)
        chunks.emplace_back(std::make_unique<Chunk>(*this, i, chunkBytes));
    dmaPort.errorCallback = [this](PacketPtr){ failed = true; fail(SD_ERR_DMA); };
}
AddrRangeList SimpleDma::getAddrRanges() const
{ return {AddrRange(base, base+window)}; }
Tick SimpleDma::read(PacketPtr p) { return access(p, false); }
Tick SimpleDma::write(PacketPtr p) { return access(p, true); }
void SimpleDma::fail(uint32_t code)
{ status |= SD_ERROR; error = code; ++stats.errors; }

Tick SimpleDma::access(PacketPtr p, bool wr)
{
    ++stats.accesses;
    const Addr off = p->getAddr()-base;
    const unsigned n = p->getSize();
    uint64_t *reg = nullptr;
    uint32_t value32 = 0;
    bool wide = false, ro = false;
    Addr rbase = off & ~Addr(7);
    switch (rbase) {
      case SD_SRC: reg=&src; wide=true; break;
      case SD_DST: reg=&dst; wide=true; break;
      case SD_LENGTH: reg=&length; wide=true; break;
      case SD_BYTES_DONE: reg=&bytesDone; wide=true; ro=true; break;
      case SD_SCRATCH: reg=&scratch; wide=true; break;
      default: break;
    }
    const bool narrow = off==SD_CONTROL || off==SD_STATUS || off==SD_ERROR_CODE;
    const bool legal = p->getAddr()>=base && off<window &&
        ((wide && ((n==8 && off==rbase) || (n==4 && (off==rbase || off==rbase+4)))) ||
         (narrow && n==4)) && !p->isMaskedWrite();
    if (!legal || (wr && (ro || off==SD_STATUS || off==SD_ERROR_CODE))) {
        fail(SD_ERR_ACCESS);
        p->makeResponse(); p->setBadAddress(); return latency;
    }
    if (!wr) {
        uint64_t val = reg ? *reg : off==SD_STATUS ? status :
                       off==SD_ERROR_CODE ? error : 0;
        if (off==SD_STATUS) ++stats.polls;
        if (reg && n==4) val >>= 8*(off-rbase);
        for (unsigned i=0; i<n; ++i) p->getPtr<uint8_t>()[i]=(val>>(8*i))&255;
    } else {
        uint64_t val=0;
        for (unsigned i=0; i<n; ++i) val |= uint64_t(p->getConstPtr<uint8_t>()[i])<<(8*i);
        if (reg) {
            if (n==8) *reg=val;
            else {
                const unsigned shift=8*(off-rbase);
                *reg=(*reg & ~(uint64_t(UINT32_MAX)<<shift)) | (val<<shift);
            }
        } else if (off==SD_CONTROL) {
            value32=val;
            if (value32 & ~(SD_START|SD_CLEAR)) fail(SD_ERR_ACCESS);
            else if (status & SD_BUSY) {
                if (value32 & SD_START) { ++stats.startsRejected; fail(SD_ERR_BUSY); }
                // CLEAR while BUSY is ignored; never cancels work or hides errors.
            } else {
                if (value32 & SD_CLEAR) { status=0; error=0; bytesDone=0; }
                if (value32 & SD_START) start();
            }
        }
    }
    DPRINTF(DmaPath, "PIO job=%llu addr=%#llx bytes=%u write=%u status=%u\n",
            jobId, p->getAddr(), n, wr, status);
    p->makeResponse();
    return latency;
}
void SimpleDma::start()
{
    jobSrc=src; jobDst=dst; jobLength=length;
    bytesDone=issued=0; status=0; error=0; failed=false;
    if (length > std::numeric_limits<Addr>::max()-src ||
        length > std::numeric_limits<Addr>::max()-dst) { fail(SD_ERR_OVERFLOW); return; }
    if (length && src < dst+length && dst < src+length) { fail(SD_ERR_OVERLAP); return; }
    if (length && (!aperture.contains(src) || !aperture.contains(src+length-1) ||
                   !aperture.contains(dst) || !aperture.contains(dst+length-1))) {
        fail(SD_ERR_ADDRESS); return;
    }
    ++jobId; ++stats.jobs; jobStart=curTick();
    if (!length) { status=SD_DONE; return; }
    status=SD_BUSY; scheduleWork();
    DPRINTF(DmaPath, "START job=%llu src=%#llx dst=%#llx length=%llu\n",
            jobId, jobSrc, jobDst, jobLength);
}
void SimpleDma::scheduleWork()
{ if (!workEvent.scheduled()) schedule(workEvent, nextCycle()); }
void SimpleDma::pump()
{
    for (auto &c : chunks) {
        if (c->active || issued==jobLength || failed) continue;
        c->active=true; c->offset=issued;
        unsigned n=std::min<uint64_t>(chunkBytes, jobLength-issued);
        issued+=n; ++active; ++stats.chunksIssued;
        stats.activeChunks=active;
        if (active > stats.chunkHighWater.value()) stats.chunkHighWater=active;
        DPRINTF(DmaPath, "READ_ISSUE job=%llu chunk=%u src=%#llx offset=%llu bytes=%u\n",
                jobId, unsigned(&c - &chunks[0]), jobSrc+c->offset, c->offset, n);
        dmaPort.dmaAction(MemCmd::ReadReq, jobSrc+c->offset, n,
                          &c->readDone, c->data.data(), 0, Request::UNCACHEABLE);
    }
    if (!active && (failed || bytesDone==jobLength)) {
        status &= ~SD_BUSY;
        if (!failed) status |= SD_DONE;
        stats.latencyTicks += curTick()-jobStart;
        DPRINTF(DmaPath, "DONE job=%llu bytes=%llu status=%u\n", jobId, bytesDone, status);
        if (drainState()==DrainState::Draining) signalDrainDone();
    }
}
void SimpleDma::readComplete(unsigned id)
{
    Chunk &c=*chunks[id];
    unsigned n=std::min<uint64_t>(chunkBytes, jobLength-c.offset);
    if (failed) { c.active=false; --active; stats.activeChunks=active; scheduleWork(); return; }
    DPRINTF(DmaPath, "WRITE_ISSUE job=%llu chunk=%u dst=%#llx offset=%llu bytes=%u\n",
            jobId, id, jobDst+c.offset, c.offset, n);
    dmaPort.dmaAction(MemCmd::WriteReq, jobDst+c.offset, n,
                      &c.writeDone, c.data.data(), 0, Request::UNCACHEABLE);
}
void SimpleDma::writeComplete(unsigned id)
{
    Chunk &c=*chunks[id];
    unsigned n=std::min<uint64_t>(chunkBytes, jobLength-c.offset);
    if (!failed) { bytesDone+=n; stats.bytesWritten+=n; }
    DPRINTF(DmaPath, "WRITE_DONE job=%llu chunk=%u offset=%llu bytes=%u\n",jobId,id,c.offset,n);
    c.active=false; --active; stats.activeChunks=active; scheduleWork();
}
DrainState SimpleDma::drain()
{ return (active || workEvent.scheduled() || dmaPending()) ? DrainState::Draining : DrainState::Drained; }
bool SimpleDma::idle() const
{
    if (active || workEvent.scheduled() || dmaPending()) return false;
    for (const auto &c : chunks)
        if (c->active || c->readDone.scheduled() || c->writeDone.scheduled()) return false;
    return true;
}
void SimpleDma::serialize(CheckpointOut &) const
{ fatal("SimpleDma checkpoint/restore is not implemented"); }
}

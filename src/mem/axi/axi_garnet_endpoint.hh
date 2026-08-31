#ifndef __MEM_AXI_AXI_GARNET_ENDPOINT_HH__
#define __MEM_AXI_AXI_GARNET_ENDPOINT_HH__

#include <cstdint>

#include "mem/axi/axi_packetization.hh"
#include "mem/ruby/common/Consumer.hh"
#include "sim/clocked_object.hh"

namespace gem5
{

struct AxiInitiatorAdapterParams;
struct AxiTargetAdapterParams;

namespace ruby
{

class AbstractController;
class AxiMeshMsg;
class MessageBuffer;

} // namespace ruby

namespace axi
{

/**
 * Commit 1 endpoint shells.
 *
 * They deliberately implement only the raw queue-ownership probe.  The AXI
 * channel API, validation, pairing, ordering, and memory service are added by
 * the later behavior commits.
 */
class AxiInitiatorAdapter : public ClockedObject, public ruby::Consumer
{
  public:
    using Params = AxiInitiatorAdapterParams;

    explicit AxiInitiatorAdapter(const Params &p);
    ~AxiInitiatorAdapter() override;

    void init() override;
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;

    /** Commit 1-only out-of-band completion observation. */
    void noteRawAwDrained();

  private:
    void injectRawAw();
    void consumeRawB();

    ruby::AbstractController *const shim;
    ruby::AbstractController *const peer;
    ruby::MessageBuffer *const awOut;
    ruby::MessageBuffer *const wOut;
    ruby::MessageBuffer *const arOut;
    ruby::MessageBuffer *const bLocal;
    ruby::MessageBuffer *const rLocal;

    const uint32_t srcNode;
    const uint16_t srcPort;
    const uint32_t dstNode;
    const AxiWireBytes channelWireBytes;
    const bool rawProbe;
    const Cycles rawProbeHoldCycles;

    bool rawAwSent = false;
    bool rawAwDrained = false;
    bool rawBVisible = false;
    bool rawBDrained = false;
    Tick rawBHoldUntil = 0;
    bool exitRequested = false;
};

class AxiTargetAdapter : public ClockedObject, public ruby::Consumer
{
  public:
    using Params = AxiTargetAdapterParams;

    explicit AxiTargetAdapter(const Params &p);
    ~AxiTargetAdapter() override;

    void init() override;
    void startup() override;
    void wakeup() override;
    void print(std::ostream &out) const override;

  private:
    void injectRawB();
    void consumeRawAw();

    ruby::AbstractController *const shim;
    ruby::AbstractController *const peer;
    AxiInitiatorAdapter *const probeObserver;
    ruby::MessageBuffer *const bOut;
    ruby::MessageBuffer *const rOut;
    ruby::MessageBuffer *const awLocal;
    ruby::MessageBuffer *const wLocal;
    ruby::MessageBuffer *const arLocal;

    const uint32_t srcNode;
    const uint16_t srcPort;
    const uint32_t dstNode;
    const AxiWireBytes channelWireBytes;
    const bool rawProbe;
    const Cycles rawProbeHoldCycles;

    bool rawBSent = false;
    bool rawAwDrained = false;
    bool holdStarted = false;
    Tick holdUntilTick = 0;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_GARNET_ENDPOINT_HH__

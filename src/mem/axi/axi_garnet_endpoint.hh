#ifndef __MEM_AXI_AXI_GARNET_ENDPOINT_HH__
#define __MEM_AXI_AXI_GARNET_ENDPOINT_HH__

#include <cstdint>
#include <map>
#include <memory>

#include "mem/axi/axi_initiator_adapter.hh"
#include "mem/axi/axi_packetization.hh"
#include "mem/axi/axi_target_adapter.hh"
#include "mem/ruby/common/Consumer.hh"
#include "mem/ruby/common/MachineID.hh"
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

struct AxiInitiatorAdapterProgress
{
    AxiInitiatorProgress core;
    uint64_t bEjectionStallCycles = 0;
    uint64_t rEjectionStallCycles = 0;
    size_t bLocalHighWater = 0;
    size_t rLocalHighWater = 0;
    size_t bIngressHighWater = 0;
    size_t rIngressHighWater = 0;
};

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

    bool tryAcceptAw(const AxiAddressRequest &aw);
    bool tryAcceptW(const AxiWBeat &w);
    bool tryAcceptAr(const AxiAddressRequest &ar);
    bool tryConsumeB(AxiBBeat &b);
    bool tryConsumeR(AxiRBeat &r);
    AxiInitiatorOccupancy functionalOccupancy() const;
    AxiInitiatorAdapterProgress functionalProgress() const;
    bool functionalIdle() const;

    /** Commit 1-only out-of-band completion observation. */
    void noteRawAwDrained();

  private:
    void injectRawAw();
    void consumeRawB();
    void functionalWakeup();
    void processFunctionalIngress();
    void ingestFunctionalResponses();
    void injectFunctionalRequests();
    bool hasFunctionalWork() const;

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
    const uint32_t dataBusBytes;
    const bool rawProbe;
    const Cycles rawProbeHoldCycles;

    bool rawAwSent = false;
    bool rawAwDrained = false;
    bool rawBVisible = false;
    bool rawBDrained = false;
    Tick rawBHoldUntil = 0;
    bool exitRequested = false;

    std::map<uint32_t, ruby::AbstractController *> targetsByNode;
    std::unique_ptr<AxiInitiatorState> functionalState;
    BoundedFifo<AxiBPacket> bIngress;
    BoundedFifo<AxiDataPacket> rIngress;
    const Cycles awInjectionDelay;
    const Cycles wInjectionDelay;
    const Cycles arInjectionDelay;
    const Cycles bResponseEjectionStallUntil;
    const Cycles rResponseEjectionStallUntil;
    AxiInitiatorAdapterProgress adapterProgress;
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

    AxiTargetOccupancy functionalOccupancy() const;
    AxiTargetProgress functionalProgress() const;
    uint8_t readMemoryByte(uint64_t address) const;
    void writeMemoryByte(uint64_t address, uint8_t value);
    bool functionalIdle() const;

  private:
    void injectRawB();
    void consumeRawAw();
    void functionalWakeup();
    void processFunctionalIngress();
    void ingestFunctionalRequests();
    void injectFunctionalResponses();
    bool hasFunctionalWork() const;

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
    const uint32_t dataBusBytes;
    const bool rawProbe;
    const Cycles rawProbeHoldCycles;

    bool rawBSent = false;
    bool rawAwDrained = false;
    bool holdStarted = false;
    Tick holdUntilTick = 0;

    std::unique_ptr<AxiTargetState> functionalState;
    BoundedFifo<AxiAddressPacket> awIngress;
    BoundedFifo<AxiDataPacket> wIngress;
    BoundedFifo<AxiAddressPacket> arIngress;
    std::map<uint64_t, ruby::MachineID> responseDestinations;
};

} // namespace axi
} // namespace gem5

#endif // __MEM_AXI_AXI_GARNET_ENDPOINT_HH__

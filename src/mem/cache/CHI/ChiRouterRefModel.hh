#ifndef __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__
#define __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "base/statistics.hh"
#include "mem/cache/CHI/ChiRouterUnits.hh"
#include "sim/eventq.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiCommonPort.hh"
#include "params/ChiRouterRefModel.hh"

namespace gem5::Chi
{

class ChiRouterRefModel : public BasicChiComponent, public ruby::Consumer
{
  public:
    PARAMS(ChiRouterRefModel);

    explicit ChiRouterRefModel(const Params &p);

    void init() override;
    void wakeup() override;
    void print(std::ostream &out) const override;
    DrainState drain() override;
    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;
    bool idle() const { return !hasWork(); }
    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;

  private:
    static constexpr int RefChannels = static_cast<int>(ChannelType::NUM_CHANNELS);
    static constexpr int MaxRouters = 2;
    static constexpr int DefaultPNum = 4;
    static constexpr int DefaultDNum = 4;
    static constexpr int DirectionNum = 4;
    static constexpr int InternalNum = 8;
    static constexpr int InPortNum = 12;
    static constexpr int OutPortNum = 12;
    static constexpr int OutArbN = 8;

    enum InPort : int
    {
        IN_P0 = 0,
        IN_P1,
        IN_P2,
        IN_P3,
        IN_E,
        IN_S,
        IN_W,
        IN_N,
        IN_E_EXT,
        IN_S_EXT,
        IN_W_EXT,
        IN_N_EXT,
    };

    enum OutPort : int
    {
        OUT_P0 = 0,
        OUT_P1,
        OUT_P2,
        OUT_P3,
        OUT_E,
        OUT_S,
        OUT_W,
        OUT_N,
        OUT_E_EXT,
        OUT_S_EXT,
        OUT_W_EXT,
        OUT_N_EXT,
    };

    enum class QueueDir : uint8_t
    {
        Rx,
        Tx
    };

    struct RoutedFlit
    {
        FlitVariant flit;
        ChannelType channel = ChannelType::REQ;
        bool fromDevice = false;
        bool fromLocalPort = false;
        bool fromInternalPeerPort = false;
        int p = 0;
        int d = 0;
        int internal = 0;
        int routerField = 0;
        OutPort out = OUT_P0;
        int destDevice = 0;
        QueueDir dir = QueueDir::Rx;
        uint64_t seq = 0;
    };

    struct ControlToken {
        OutPort out;
        int routerField;
        uint64_t seq;
    };

    using FlitQueue = std::deque<RoutedFlit>;
    using CtrlQueue = std::deque<OutPort>;

    std::vector<std::unique_ptr<ChiCommonPort>> devicePorts;
    std::vector<std::unique_ptr<ChiCommonPort>> localPorts;
    std::vector<std::unique_ptr<ChiCommonPort>> internalPorts;
    std::vector<std::unique_ptr<ChiCommonPort>> internalPeerPorts;

    struct IngressPort {
        ChiCommonPort *port;
        int source;
        bool alternate;
    };
    // Binding is fixed after init. Preserve the original source/port order.
    std::vector<IngressPort> ingressPorts;

    const uint8_t localX;
    const uint8_t localY;
    const bool yIncIsNorth;
    const bool multiFlitOut;
    const int pNum;
    const int dNum;
    const int internalNum;
    const int pTxDepth;
    const int pCandDepth;

    std::array<bool, RefChannels> useDualCh{};
    ChiRouterRoutingUnit routing;

    std::array<std::array<FlitQueue, 24>, RefChannels> inputQ;
    std::array<std::array<std::deque<ControlToken>, InternalNum>, RefChannels>
        inputFlitvQ;
    std::array<std::array<std::optional<ControlToken>, InternalNum>, RefChannels>
        admittedControl;
    using InputUnit = ChiRouterInputUnit<RoutedFlit, OutPort, OutPortNum>;
    std::array<std::array<std::array<InputUnit, InPortNum>, MaxRouters>,
               RefChannels> inputs;

    std::array<std::array<std::array<FlitQueue, DefaultPNum>, MaxRouters>,
               RefChannels>
        pCandQ;
    std::array<std::array<std::optional<RoutedFlit>, DefaultPNum>, RefChannels>
        pPipeQ;
    using OutputUnit = ChiRouterOutputUnit<RoutedFlit, DefaultDNum>;
    std::array<std::array<OutputUnit, DefaultPNum>, RefChannels> pTxFifo;
    std::array<std::array<std::array<int, InPortNum>, MaxRouters>, RefChannels>
        occ{};
    std::array<std::array<std::array<int, InPortNum>, MaxRouters>, RefChannels>
        occIncPipe{};
    std::array<std::array<std::array<int, InPortNum>, MaxRouters>, RefChannels>
        occDecPipe{};

    std::array<std::array<int, DefaultPNum>, RefChannels> mdlCredit{};
    std::array<std::array<int, DefaultPNum>, RefChannels> mdlCreditIncPipe{};
    std::array<std::array<int, DefaultPNum>, RefChannels> mdlCreditIncPipeH1{};
    std::array<std::array<int, DefaultPNum>, RefChannels> mdlCreditIncPipeH2{};
    std::array<std::array<int, DefaultPNum>, RefChannels> mdlCreditDecPipe{};

    std::array<std::array<ChiAgeArbiter<DefaultDNum>, DefaultPNum>,
               RefChannels> pdevArbiters;
    std::array<std::array<std::array<ChiAgeArbiter<OutArbN>, OutPortNum>,
                          MaxRouters>, RefChannels> outputArbiters;
    std::array<std::array<ChiAgeArbiter<MaxRouters>, DefaultPNum>,
               RefChannels> mergeArbiters;

    std::array<std::array<std::array<bool, DefaultDNum>, DefaultPNum>,
               RefChannels>
        pdevReqPipe{};
    std::array<std::array<uint16_t, MaxRouters>, RefChannels> outGrantMask{};
    std::array<std::array<std::array<InPort, OutPortNum>, MaxRouters>,
               RefChannels>
        outGrantWinIp{};
    std::array<std::array<int, DefaultPNum>, RefChannels> rrPtrPdevOut{};

    uint64_t nextSeq = 1;
    // Count each payload once between port sampling and successful egress.
    // Control tokens and output grants always belong to one such payload.
    std::array<size_t, RefChannels> ownedFlits{};
    size_t totalOwnedFlits = 0;
    uint8_t pendingCreditChannels = 0;
    EventFunctionWrapper tickEvent;
    void tick();

    struct RouterStats : public statistics::Group
    {
        explicit RouterStats(statistics::Group *parent);

        /** Flits accepted from local endpoints, indexed by CHI channel. */
        statistics::Vector localInjectedFlits;
        /** Flits delivered to local endpoints, indexed by CHI channel. */
        statistics::Vector localDeliveredFlits;
        /** Directional-link ingress flits, indexed by channel/direction. */
        statistics::Vector2d internalReceivedFlits;
        /** Directional-link egress flits, indexed by channel/direction. */
        statistics::Vector2d internalSentFlits;
        /** Blocked directional-output flit-cycles by channel/direction. */
        statistics::Vector2d internalOutputStallCycles;
        /** Blocked local-injection P-port cycles by channel/P port. */
        statistics::Vector2d localInjectionStallCycles;
        /** Blocked local-delivery P-port cycles by channel/P port. */
        statistics::Vector2d localDeliveryStallCycles;
    } stats;

    void softReset();
    void initPdevAge();
    void initOutAge();
    void initMergeAge();

    void sampleRxPorts();
    void stepOneCycle();
    void ptxDrainByCreditStage(ChannelType ch);
    void outSendCommitStage(ChannelType ch);
    void pportMergeStage(ChannelType ch);
    void ptxEnqueueStage(ChannelType ch);
    void internalPayloadCommitStage(ChannelType ch);
    void doOutputArbitration(ChannelType ch);
    void pdevArbStage(ChannelType ch);
    void samplePdevReqStage(ChannelType ch);
    void sampleInternalFlitvStage(ChannelType ch);
    void occUpdateStage(uint8_t active_channels);

    void makeRoutedFlit(RoutedFlit &tr, ChannelType ch,
                       const FlitVariant &flit, QueueDir dir,
                       bool from_device, int p, int d, int internal);
    uint32_t flitTargetId(const FlitVariant &flit) const;
    uint32_t routeFieldFor(ChannelType ch, const FlitVariant &flit) const;
    void rememberReverseRoute(ChannelType ch, const FlitVariant &flit,
                              bool from_device, int p, int d);
    OutPort decodeRouterToOutport(uint32_t router_field) const;

    int chIdx(ChannelType ch) const { return static_cast<int>(ch); }
    ChannelType idxToCh(int idx) const { return static_cast<ChannelType>(idx); }
    const char *channelName(ChannelType ch) const;
    const char *inPortName(InPort ip) const;
    const char *outPortName(OutPort op) const;
    int directionIndex(OutPort op) const;

    InPort srcToInport(int src_index) const;
    InPort pToInport(int p) const;
    int inportToP(InPort ip) const;
    bool isPInport(InPort ip) const;
    bool isPOut(OutPort op) const;
    bool isExtOut(OutPort op) const;
    bool isInternalIp(InPort ip) const;
    int internalOutPortIndex(int router, OutPort op) const;
    int selectRouterForInternal(ChannelType ch, int src) const;
    int selectRouterForPinj(ChannelType ch, int p) const;
    int getPdevCreditInit(ChannelType ch) const;
    bool hasPdevCredit(ChannelType ch, int p) const;

    int inbufSize(ChannelType ch, int router, InPort ip) const;
    int internalReqSize(ChannelType ch, int router, InPort ip) const;
    void enqueueInbufFlit(ChannelType ch, int router, InPort ip,
                          const RoutedFlit &flit);
    void enqueueInternalReq(ChannelType ch, int router, InPort ip,
                            OutPort out);
    bool popInternalReq(ChannelType ch, int router, InPort ip, OutPort out);
    bool hasReqForOut(ChannelType ch, int router, InPort ip, OutPort out) const;

    int pickOldestPdev(ChannelType ch, int p,
                       const std::array<bool, DefaultDNum> &req) const;
    void updatePdevAge(ChannelType ch, int p, int win_d);
    InPort arbReqIdxToIp(int router, int idx) const;
    int pickOldestOut(ChannelType ch, int router, OutPort out,
                      const std::array<bool, OutArbN> &req) const;
    bool pickOutputWinnerByAge(ChannelType ch, int router, OutPort out,
                               InPort &win_ip, int &win_idx) const;
    void updateOutAge(ChannelType ch, int router, OutPort out, int win_idx);
    bool pCandHasSpace(ChannelType ch, int router, int p) const;
    bool pTxHasSpace(ChannelType ch, int p) const;
    int pickOldestMerge(ChannelType ch, int p,
                        const std::array<bool, MaxRouters> &req) const;
    bool pickMergeRouterAge(ChannelType ch, int p, int &picked_router) const;
    void updateMergeAge(ChannelType ch, int p, int win_router);
    bool canSendOut(ChannelType ch, int router, OutPort out,
                    QueueDir dir) const;
    bool routerCanDriveOut(ChannelType ch, int router, OutPort out) const;
    bool portCanSend(const ChiCommonPort &port, ChannelType ch,
                     QueueDir dir) const;
    ChiCommonPort *selectLocalOutputPort(ChannelType ch, int p, int d,
                                         QueueDir dir) const;
    bool sendToInternal(ChannelType ch, int router, OutPort out,
                        const RoutedFlit &flit);
    bool sendToDevice(ChannelType ch, int p, int d, const RoutedFlit &flit);
    void returnIngressCredit(const RoutedFlit &flit);
    bool hasWork() const;
    bool portsDrained() const;
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__

#ifndef __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__
#define __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

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

    void wakeup() override;
    void print(std::ostream &out) const override;
    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;

  private:
    static constexpr int RefChannels = static_cast<int>(ChannelType::NUM_CHANNELS);
    static constexpr int MaxRouters = 2;
    static constexpr int DefaultPNum = 4;
    static constexpr int DefaultDNum = 4;
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
        int srcIndex = 0;
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

    struct TxnRouteKey
    {
        uint32_t node = 0;
        uint32_t txn = 0;

        bool operator==(const TxnRouteKey &other) const
        {
            return node == other.node && txn == other.txn;
        }
    };

    struct TxnRouteKeyHash
    {
        std::size_t operator()(const TxnRouteKey &key) const
        {
            return (static_cast<std::size_t>(key.node) << 32) ^ key.txn;
        }
    };

    struct InternalCtrl
    {
        int src = 0;
        int router = 0;
        InPort in = IN_E;
        OutPort out = OUT_P0;
    };

    using FlitQueue = std::deque<RoutedFlit>;
    using CtrlQueue = std::deque<OutPort>;

    std::vector<std::unique_ptr<ChiCommonPort>> devicePorts;
    std::vector<std::unique_ptr<ChiCommonPort>> localPorts;
    std::vector<std::unique_ptr<ChiCommonPort>> internalPorts;
    std::vector<std::unique_ptr<ChiCommonPort>> internalPeerPorts;

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
    std::unordered_map<uint32_t, uint32_t> routeTable;
    std::unordered_map<TxnRouteKey, uint32_t, TxnRouteKeyHash> reverseRoute;

    std::array<std::array<FlitQueue, 24>, RefChannels> inputQ;
    std::array<std::array<FlitQueue, InternalNum>, RefChannels> inputFlitvQ;

    std::array<std::array<std::array<FlitQueue, InPortNum>, MaxRouters>,
               RefChannels>
        inbufData;
    std::array<
        std::array<std::array<std::array<FlitQueue, OutPortNum>, InPortNum>,
                   MaxRouters>,
        RefChannels>
        inbufDataByOut;

    std::array<std::array<std::array<CtrlQueue, InPortNum>, MaxRouters>,
               RefChannels>
        internalReqQ;
    std::array<
        std::array<std::array<std::array<CtrlQueue, OutPortNum>, InPortNum>,
                   MaxRouters>,
        RefChannels>
        internalReqByOutQ;

    std::array<std::array<std::array<FlitQueue, OutPortNum>, MaxRouters>,
               RefChannels>
        outQ;
    std::array<std::array<std::array<FlitQueue, DefaultPNum>, MaxRouters>,
               RefChannels>
        pCandQ;
    std::array<std::array<FlitQueue, DefaultPNum>, RefChannels> pPipeQ;
    std::array<std::array<FlitQueue, DefaultPNum>, RefChannels> pTxFifo;
    std::array<std::array<std::array<FlitQueue, DefaultDNum>, DefaultPNum>,
               RefChannels>
        outQPdev;

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

    std::array<std::array<std::array<int, OutPortNum>, MaxRouters>,
               RefChannels>
        outCredit{};
    std::array<std::array<std::array<int, DefaultDNum>, DefaultPNum>,
               RefChannels>
        pdevCredit{};

    std::array<std::array<std::array<std::array<bool, DefaultDNum>, DefaultDNum>,
                          DefaultPNum>,
               RefChannels>
        pdevAge{};
    std::array<
        std::array<std::array<std::array<std::array<bool, OutArbN>, OutArbN>,
                              OutPortNum>,
                   MaxRouters>,
        RefChannels>
        outAge{};
    std::array<std::array<std::array<std::array<bool, MaxRouters>, MaxRouters>,
                          DefaultPNum>,
               RefChannels>
        mergeAge{};

    std::array<std::array<std::array<bool, DefaultDNum>, DefaultPNum>,
               RefChannels>
        pdevReqPipe{};
    std::array<std::array<std::array<bool, OutPortNum>, MaxRouters>,
               RefChannels>
        outGrantVld{};
    std::array<std::array<std::array<InPort, OutPortNum>, MaxRouters>,
               RefChannels>
        outGrantWinIp{};
    std::array<std::array<int, DefaultPNum>, RefChannels> rrPtrPdevOut{};

    uint64_t nextSeq = 1;

    void softReset();
    void initPdevAge();
    void initOutAge();
    void initMergeAge();

    void sampleRxPorts();
    void stepOneCycle();
    void updateCreditFromIf();
    void ptxDrainByCreditStage(ChannelType ch);
    void outSendCommitStage(ChannelType ch);
    void pportMergeStage(ChannelType ch);
    void ptxEnqueueStage(ChannelType ch);
    void internalPayloadCommitStage(ChannelType ch);
    void doOutputArbitration(ChannelType ch);
    void pdevArbStage(ChannelType ch);
    void samplePdevReqStage(ChannelType ch);
    void sampleInternalFlitvStage(ChannelType ch);
    void occUpdateStage();

    RoutedFlit makeRoutedFlit(ChannelType ch, const FlitVariant &flit,
                              QueueDir dir, bool from_device, int src_index,
                              int p, int d, int internal);
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
    bool popInbufFlit(ChannelType ch, int router, InPort ip, OutPort out,
                      RoutedFlit &flit);
    bool peekInbufFlit(ChannelType ch, int router, InPort ip, OutPort out,
                       RoutedFlit &flit) const;
    bool popInternalReq(ChannelType ch, int router, InPort ip, OutPort out);
    bool ctrlHasReq(ChannelType ch, int router, InPort ip, OutPort &out) const;
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
    bool popFirstMatchDev(FlitQueue &queue, int d, RoutedFlit &flit);
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
};

} // namespace gem5::Chi

#endif // __MEM_CACHE_CHI_CHI_ROUTER_REFMODEL_HH__

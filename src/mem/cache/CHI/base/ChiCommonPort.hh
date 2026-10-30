#ifndef __CHICOMMONPORT__HH__
#define __CHICOMMONPORT__HH__

#include <deque>
#include <optional>
#include <queue>
#include <typeindex>

#include "base/logging.hh"
#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/ChiFlitBuffer.hh"
#include "mem/port.hh"
#include "mem/ruby/common/Consumer.hh"
#include "sim/cur_tick.hh"

namespace gem5
{

namespace Chi
{

class ChiCommonPort : public Port
{
    static constexpr size_t NUM_CH =
        static_cast<size_t>(ChannelType::NUM_CHANNELS);

    enum class QueueKind
    {
        Rx,
        Tx
    };

public:
    explicit ChiCommonPort(const std::string& name,
                            ruby::Consumer* m_consumer ,
                           PortID id = InvalidPortID,
                           const std::array<uint8_t, NUM_CH>& init =
                               std::array<uint8_t, NUM_CH>{4, 8, 4, 8})
        : Port(name, id),
            m_consumer(m_consumer),
          rxCredit(init),
          rxCreditLimit(init),
          txCredit(init),
          txCreditLimit(init)
    {
        for (std::size_t ch = 0; ch < NUM_CH; ++ch) {
            rx_queue[ch].configure(init[ch]);
            tx_queue[ch].configure(init[ch]);
        }
    }

    void bind(Port &peer) override {
        auto *typed = dynamic_cast<ChiCommonPort *>(&peer);
        panic_if(!typed, "ChiCommonPort peer has incompatible type");
        Port::bind(peer);
        peerPort = typed;
    }
    void unbind() override {
        Port::unbind();
        peerPort = nullptr;
    }

    // Credit management
    void increaseRxCredit(ChannelType ch, uint8_t val = 1) {
        auto idx = static_cast<size_t>(ch);
        rxCredit[idx] += val;
        checkRxCredit(ch);
        // 收到 credit 返还 → 唤醒本端 consumer (可能有 flit 等着发)
        if (m_consumer)
            m_consumer->wakeup();
    }

    void increaseTxCredit(ChannelType ch, uint8_t val = 1) {
        auto idx = static_cast<size_t>(ch);
        txCredit[idx] += val;
        checkTxCredit(ch);
        // 收到 credit 返还 → 唤醒本端 consumer (可能有 retry 等着发)
        if (m_consumer)
            m_consumer->wakeup();
    }

    void checkRxCredit(ChannelType ch) const {
        auto idx = static_cast<size_t>(ch);
        assert(rxCredit[idx] <= rxCreditLimit[idx] && "Rx credit overflow!");
    }

    void checkTxCredit(ChannelType ch) const {
        auto idx = static_cast<size_t>(ch);
        assert(txCredit[idx] <= txCreditLimit[idx] && "Tx credit overflow!");
    }

    // RX enqueue
    bool enqueueRx(ChannelType ch, const FlitVariant& f) {
        return enqueueFlit(QueueKind::Rx, ch, f);
    }

    // TX enqueue
    bool enqueueTx(ChannelType ch, const FlitVariant& f) {
        return enqueueFlit(QueueKind::Tx, ch, f);
    }

    // 查询 TX 通道是否还有 credit (与 enqueueTx 的判断一致)。
    // 供仲裁器先查 credit 再出队, 避免弹出后发不出去。
    bool hasTxCredit(ChannelType ch) {
        auto idx = static_cast<size_t>(ch);
        return targetPort().txCredit[idx] > 0;
    }

    // RX dequeue
    std::optional<FlitVariant> getRxFlit(ChannelType ch) {
        return dequeueFlit(QueueKind::Rx, ch);
    }

    std::optional<FlitVariant>
    getRxFlit(const std::type_index& flitType)
    {
        if (flitType == std::type_index(typeid(RawReq))) {
            return getRxFlit(ChannelType::REQ);
        }
        if (flitType == std::type_index(typeid(RawRsp))) {
            return getRxFlit(ChannelType::RSP);
        }
        if (flitType == std::type_index(typeid(RawSnp))) {
            return getRxFlit(ChannelType::SNP);
        }
        if (flitType == std::type_index(typeid(RawDat))) {
            return getRxFlit(ChannelType::DAT);
        }

        return std::nullopt;
    }

    // TX dequeue
    std::optional<FlitVariant> getTxFlit(ChannelType ch) {
        return dequeueFlit(QueueKind::Tx, ch);
    }

    // Routers retain wire-buffer ownership until the associated input slot
    // is released. A dequeue alone must not create an upstream credit.
    std::optional<FlitVariant> getRxFlitNoCredit(ChannelType ch) {
        return dequeueFlit(QueueKind::Rx, ch, false);
    }
    std::optional<FlitVariant> getTxFlitNoCredit(ChannelType ch) {
        return dequeueFlit(QueueKind::Tx, ch, false);
    }
    void returnRxCredit(ChannelType ch, uint8_t count = 1) {
        returnCredit(QueueKind::Rx, ch, count);
    }
    void returnTxCredit(ChannelType ch, uint8_t count = 1) {
        returnCredit(QueueKind::Tx, ch, count);
    }
    bool hasRxFlit(ChannelType ch, bool next_edge_only = false) const {
        const auto &q = rx_queue[static_cast<size_t>(ch)];
        return !q.empty() && (!next_edge_only || q.front().accepted < curTick());
    }
    bool hasTxFlit(ChannelType ch, bool next_edge_only = false) const {
        const auto &q = tx_queue[static_cast<size_t>(ch)];
        return !q.empty() && (!next_edge_only || q.front().accepted < curTick());
    }
    bool peerHasRxCredit(ChannelType ch) const {
        return targetPort().visibleCredit(QueueKind::Rx, ch) != 0;
    }
    bool peerHasTxCredit(ChannelType ch) const {
        return targetPort().visibleCredit(QueueKind::Tx, ch) != 0;
    }
    unsigned availableRxCredit(ChannelType ch) const {
        return rxCredit[static_cast<size_t>(ch)];
    }
    unsigned availableTxCredit(ChannelType ch) const {
        return txCredit[static_cast<size_t>(ch)];
    }
    // A drained wire owns no flit, including flits dequeued with credit held
    // by a router. Wait until a same-edge return becomes visible as well.
    bool drained() const {
        for (std::size_t c = 0; c < NUM_CH; ++c) {
            if (!rx_queue[c].empty() || !tx_queue[c].empty() ||
                rxCredit[c] != rxCreditLimit[c] ||
                txCredit[c] != txCreditLimit[c])
                return false;
            for (std::size_t k = 0; k < 2; ++k)
                if (creditReturnTick[k][c] == curTick() && creditReturned[k][c])
                    return false;
        }
        return true;
    }

private:

    ruby::Consumer *m_consumer;
    ChiCommonPort *peerPort = nullptr;
    std::array<uint8_t, NUM_CH> rxCredit{};
    std::array<uint8_t, NUM_CH> rxCreditLimit{};
    std::array<uint8_t, NUM_CH> txCredit{};
    std::array<uint8_t, NUM_CH> txCreditLimit{};
    // Strict router credit queries observe a return on the next edge. Legacy
    // enqueue/hasTxCredit users keep their existing acceptance semantics.
    std::array<std::array<Tick, NUM_CH>, 2> creditReturnTick{};
    std::array<std::array<unsigned, NUM_CH>, 2> creditReturned{};



    ChiFlitBuffer rx_queue[NUM_CH];
    ChiFlitBuffer tx_queue[NUM_CH];

    ChiCommonPort&
    targetPort()
    {
        // Binding (including takeOverFrom) validates the type once. The peer
        // remains fixed between bind/unbind calls, also under backpressure.
        return peerPort ? *peerPort : *this;
    }

    const ChiCommonPort& targetPort() const {
        return const_cast<ChiCommonPort*>(this)->targetPort();
    }

    void markReturned(QueueKind kind, ChannelType ch, unsigned count) {
        const auto k = static_cast<size_t>(kind), c = static_cast<size_t>(ch);
        if (creditReturnTick[k][c] != curTick()) {
            creditReturnTick[k][c] = curTick();
            creditReturned[k][c] = 0;
        }
        creditReturned[k][c] += count;
    }
    unsigned visibleCredit(QueueKind kind, ChannelType ch) const {
        const auto k = static_cast<size_t>(kind), c = static_cast<size_t>(ch);
        const unsigned total = kind == QueueKind::Rx ? rxCredit[c] : txCredit[c];
        const unsigned pending = creditReturnTick[k][c] == curTick() ?
            creditReturned[k][c] : 0;
        return total > pending ? total - pending : 0;
    }

    void returnCredit(QueueKind kind, ChannelType ch, uint8_t count) {
        auto &credit = creditArray(kind);
        const auto idx = static_cast<size_t>(ch);
        assert(unsigned(credit[idx]) + count <= creditLimitArray(kind)[idx]);
        credit[idx] += count;
        markReturned(kind, ch, count);
        // Notify the sender on an edge, never recursively execute a router
        // stage while its peer is releasing a flit.
        if (isConnected() && targetPort().m_consumer)
            targetPort().m_consumer->scheduleEvent(Cycles(1));
    }

    std::array<uint8_t, NUM_CH>&
    creditArray(QueueKind kind)
    {
        return kind == QueueKind::Rx ? rxCredit : txCredit;
    }

    const std::array<uint8_t, NUM_CH>&
    creditLimitArray(QueueKind kind) const
    {
        return kind == QueueKind::Rx ? rxCreditLimit : txCreditLimit;
    }

    ChiFlitBuffer*
    queueArray(QueueKind kind)
    {
        return kind == QueueKind::Rx ? rx_queue : tx_queue;
    }

    void
    checkCredit(QueueKind kind, ChannelType ch) const
    {
        if (kind == QueueKind::Rx) {
            checkRxCredit(ch);
        } else {
            checkTxCredit(ch);
        }
    }

    bool
    enqueueFlit(QueueKind kind, ChannelType ch, const FlitVariant& f)
    {
        ChiCommonPort& dst = targetPort();
        auto& credit = dst.creditArray(kind);
        const auto& creditLimit = dst.creditLimitArray(kind);
        auto* queues = dst.queueArray(kind);
        size_t idx = static_cast<size_t>(ch);
        if (credit[idx] == 0) {
            return false;
        }
        credit[idx]--;
        assert(credit[idx] <= creditLimit[idx] && "CHI credit overflow!");
        queues[idx].push(f, curTick());
        assert(dst.m_consumer && "ChiCommonPort m_consumer is null");
        dst.m_consumer->wakeup();
        return true;
    }

    std::optional<FlitVariant>
    dequeueFlit(QueueKind kind, ChannelType ch, bool release = true)
    {
        auto& credit = creditArray(kind);
        auto* queues = queueArray(kind);
        size_t idx = static_cast<size_t>(ch);
        if (queues[idx].empty()) {
            return std::nullopt;
        }
        FlitVariant f = queues[idx].pop();
        if (release) {
            credit[idx]++;
            markReturned(kind, ch, 1);
            checkCredit(kind, ch);
        }
        return f;
    }
};




}

}//namespace gem5

#endif

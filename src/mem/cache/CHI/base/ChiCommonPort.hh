#ifndef __CHICOMMONPORT__HH__
#define __CHICOMMONPORT__HH__

#include <deque>
#include <optional>
#include <queue>

#include "mem/cache/CHI/base/BasicChiComponent.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/port.hh"
#include "mem/ruby/common/Consumer.hh"

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
    {}

    // Credit management
    void increaseRxCredit(ChannelType ch, uint8_t val = 1) {
        auto idx = static_cast<size_t>(ch);
        rxCredit[idx] += val;
        checkRxCredit(ch);
    }

    void increaseTxCredit(ChannelType ch, uint8_t val = 1) {
        auto idx = static_cast<size_t>(ch);
        txCredit[idx] += val;
        checkTxCredit(ch);
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

    // RX dequeue
    std::optional<FlitVariant> getRxFlit(ChannelType ch) {
        return dequeueFlit(QueueKind::Rx, ch);
    }

    // TX dequeue
    std::optional<FlitVariant> getTxFlit(ChannelType ch) {
        return dequeueFlit(QueueKind::Tx, ch);
    }

private:

    ruby::Consumer *m_consumer;
    std::array<uint8_t, NUM_CH> rxCredit{};
    std::array<uint8_t, NUM_CH> rxCreditLimit{};
    std::array<uint8_t, NUM_CH> txCredit{};
    std::array<uint8_t, NUM_CH> txCreditLimit{};



    std::deque<FlitVariant> rx_queue[NUM_CH];
    std::deque<FlitVariant> tx_queue[NUM_CH];

    ChiCommonPort&
    targetPort()
    {
        if (!isConnected()) {
            return *this;
        }

        auto *peer = dynamic_cast<ChiCommonPort*>(&getPeer());
        assert(peer && "ChiCommonPort peer has incompatible type");
        return *peer;
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

    std::deque<FlitVariant>*
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
        queues[idx].push_back(f);
        assert(dst.m_consumer && "ChiCommonPort m_consumer is null");
        dst.m_consumer->wakeup();
        return true;
    }

    std::optional<FlitVariant>
    dequeueFlit(QueueKind kind, ChannelType ch)
    {
        auto& credit = creditArray(kind);
        auto* queues = queueArray(kind);
        size_t idx = static_cast<size_t>(ch);
        if (queues[idx].empty()) {
            return std::nullopt;
        }
        FlitVariant f = std::move(queues[idx].front());
        queues[idx].pop_front();
        credit[idx]++;
        checkCredit(kind, ch);
        return f;
    }
};




}

}//namespace gem5

#endif

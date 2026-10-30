#ifndef __HOMEPOCQ__HH__
#define __HOMEPOCQ__HH__

#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <map>
#include <list>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#include "mem/cache/CHI/PocqStateGraph.hh"
#include "mem/cache/CHI/PocqVictimBuffer.hh"
#include "mem/cache/CHI/SlcSfTypes.hh"
#include "mem/cache/CHI/base/ChiChannel.hh"
#include "mem/cache/CHI/base/DatOpcode.hh"
#include "mem/cache/CHI/base/ReqOpcode.hh"
#include "mem/cache/CHI/base/RspOpcode.hh"
#include "mem/cache/CHI/base/SnoopOpcode.hh"
#include "mem/ruby/common/Consumer.hh"
#include "sim/eventq.hh"
#include "sim/serialize.hh"

namespace gem5::Chi
{

class HomeNodeFull;

// ────────────── HomePocq: 统一输入流水 + 事件驱动状态机 ──────────────
// HomePocq 本身不再保留重复的 flit stage pipeline。
// LinkLayer H0 打空拍，H1 调 allocate()。HomePocq 在下一拍先
// 同时推进所有可运行 Entry，再对产生的 action 做资源仲裁。
// trigger() 仍用于记录状态机事件, processEvent 在下一拍调用 process()。
class HomePocq : public ruby::Consumer
{
  public:
    HomePocq(HomeNodeFull *hnf, std::size_t entry_num);

    // Consumer 接口: 仲裁 Entry 并推进赢家状态。
    void wakeup() override;
    void print(std::ostream &out) const override {}

    // LinkLayer 的直接准入接口。成功后 Entry 已经进入 POCQ，
    // 并且 HomePocq 会自己调度下一拍 wakeup()。
    bool allocate(const FlitVariant &flit);
    bool allocate(const FlitVariant &flit, PoolPriority reserved_pool);
    bool hasFreeEntry() const;
    bool victimConflict(const SlcSfRequest &request, uint64_t cycle) const
    {
        return victimBuffers.conflicts(PocqVictimBuffer::ownerKey(
            request.srcid, request.txnid), request.addr, request.ns, cycle);
    }
    std::size_t activeEntries() const { return m_pocq.size(); }
    bool idle() const;
    void serialize(CheckpointOut &cp) const;
    void unserialize(CheckpointIn &cp);
    void receive(const RawRsp &rsp);
    void receive(const RawDat &dat);

    struct IssuedAction
    {
        uint32_t srcid;
        uint32_t txnid;
        PocqAction action;
    };

    bool hasIssuedAction() const { return !issuedActions.empty(); }
    std::optional<IssuedAction> popIssuedAction();

    // LinkLayer / SLC-SF 向指定事务投递一个有类型的状态机事件。
    // 不立即处理: 记录触发, 排 processEvent 到下一拍 (classic-cache 风格,
    // 避免本拍级联 + 多个触发合并到一次 process)。
    void trigger(uint32_t srcid, uint32_t txnid, const PocqEvent &event);

    // 事件处理: processEvent 触发时取出所有 pending, 逐个推进状态机。
    void process();

  private:
    struct Rawflit
    {
        RawReq req{};
        RawRsp rsp{};
        RawSnp snp{};
        RawDat dat{};
    };

    struct Writeback
    {
        uint64_t addr = 0;
        bool ns = true;
        SlcLineData data{};
        uint32_t wireId = 0;
        uint32_t dbid = 0;
        bool sent = false;
        bool haveDbid = false;
        bool complete = false;
        unsigned beats = 0;
    };

    struct SnoopReply
    {
        bool sent = false;
        bool done = false;
        bool dirty = false;
        uint8_t beats = 0;
        SlcLineData data{};
    };

    struct Entry : public PocqMachine
    {
        bool valid = false;
        bool is_sleep = false;
        ChannelType channel = NUM_CHANNELS;
        std::vector<Entry *> sleep_queue;
        Rawflit rawflit{};
        PoolPriority qosPriority = Low;
        bool qosPoolReserved = false;
        std::deque<PocqAction> pendingActions;
        uint32_t wireId = 0;
        uint64_t slcToken = 0;
        std::optional<PocqAction> slcAction;
        bool slcCommitted = false;
        bool lookupSeen = false;
        bool lookupDelivered = false;
        SlcSfLookup lookup{};
        SlcLineData line{};
        uint8_t lineBeats = 0;
        bool dirty = false;
        SlcLineData victimData{};
        uint8_t victimBeats = 0;
        bool victimQueued = false;
        std::map<uint32_t, SnoopReply> snoops;
        bool snoopStarted = false;
        bool snoopDelivered = false;
        unsigned txDataBeat = 0;
        uint32_t memoryDbid = 0;
        bool memorySent = false;
        bool memoryDone = false;
        bool memoryReceipt = false;
        bool memoryEventSent = false;
        bool dirtyQueued = false;
        bool completionSent = false;
        bool writeDbidSent = false;
        bool writeDataValid = false;
        std::optional<uint8_t> writeDataResp;
        std::deque<Writeback> writebacks;
        bool internalSeq = false;
        uint64_t seqId = 0;
        bool seqReleased = false;

        DecodedReq reqOpcode{
            ReqOpcode{0}, ReqMajor::ReservedOrUnsupported,
            ReqMinor::ReservedOrUnsupported};
        DecodedRsp rspOpcode{
            RspOpcode{0}, RspMajor::ReservedOrUnsupported,
            RspMinor::ReservedOrUnsupported};
        DecodedSnp snpOpcode{
            SnpOpcode{0}, SnpMajor::ReservedOrUnsupported,
            SnpMinor::ReservedOrUnsupported};
        DecodedDat datOpcode{
            DatOpcode{0}, DatMajor::ReservedOrUnsupported,
            DatMinor::ReservedOrUnsupported};

        Entry() = default;

        Entry(const FlitVariant &flit, const PocqGraphPanel &panel)
        {
            update(flit);

            const auto graph = PocqGraphPanel::graphForReq(reqOpcode.minor);
            if (graph)
                panel.initialize(*this, *graph);
        }

        std::optional<PocqStepResult>
        step(const PocqEvent &event)
        {
            if (!state)
                return std::nullopt;
            return state->step(*this, event);
        }

        void setSleepQueue(Entry *q)
        {
            sleep_queue.push_back(q);
        }

        void
        deallocate()
        {
            if (!sleep_queue.empty()) {
                Entry *next = sleep_queue.front();
                sleep_queue.erase(sleep_queue.begin());
                next->sleep_queue = std::move(sleep_queue);
                next->is_sleep = false;
            }
        }

        uint64_t
        ReqAddr() const
        {
            return rawflit.req.addr;
        }

        void update(const FlitVariant &flit)
        {
            std::visit([this](const auto &raw) {
                using Flit = std::decay_t<decltype(raw)>;

                qosPriority = raw.qosPriority();

                if constexpr (std::is_same_v<Flit, RawReq>) {
                    channel = REQ;
                    rawflit.req = raw;
                    reqOpcode = decodeReq(raw.opcode);
                } else if constexpr (std::is_same_v<Flit, RawRsp>) {
                    channel = RSP;
                    rawflit.rsp = raw;
                    rspOpcode = decodeRsp(raw.opcode);
                } else if constexpr (std::is_same_v<Flit, RawSnp>) {
                    channel = SNP;
                    rawflit.snp = raw;
                    snpOpcode = decodeSnp(raw.opcode);
                } else {
                    static_assert(std::is_same_v<Flit, RawDat>);
                    channel = DAT;
                    rawflit.dat = raw;
                    datOpcode = decodeDat(raw.opcode);
                }
            }, flit);

            valid = true;
        }

        bool
        isAt(PocqNodeRole role) const
        {
            return state && state->key().role == role;
        }

        const BaseFlit &
        baseFlit() const
        {
            switch (channel) {
              case REQ: return rawflit.req;
              case RSP: return rawflit.rsp;
              case SNP: return rawflit.snp;
              case DAT: return rawflit.dat;
              default: return rawflit.req;
            }
        }

        void
        appendActions(const PocqStepResult &result)
        {
            for (std::size_t i = 0; i < result.action_count(); ++i) {
                const auto &action = result.get_action(i);
                // A SEQ operates on an evicted snapshot. Its SF replacement
                // has already committed, so there is no tag operation here.
                // Do not queue a no-op behind older SeqFull lookup replays:
                // they depend on this SEQ completing and releasing capacity.
                if (internalSeq && action.kind == PocqActionKind::FlushSf)
                    continue;
                pendingActions.push_back(action);
            }
        }
    };

    struct POCQueue
    {
        POCQueue(std::size_t entry_num, const PocqGraphPanel &panel)
            : m_entry_num(entry_num), graphPanel(panel)
        {}

        // Age order plus stable addresses for sleep_queue pointers. Immediate
        // removal bounds storage even when the oldest transaction stalls.
        std::list<Entry> queue;

        enum Arb_win
        {
            SlcLookup,
            MCRead,
            TxDat,
            TxSnp,
            TxRsp,
            McRetry,
            Num_ArbWin
        };
        using win_array = std::array<Entry *, Num_ArbWin>;

        bool allocate(const FlitVariant &flit, PoolPriority priority,
                      bool qos_pool_reserved)
        {
            if (is_full())
                return false;

            queue.emplace_back(flit, graphPanel);
            Entry &entry = queue.back();

            // 尚未实现的 opcode 没有状态图，不能占住 POCQ
            // 变成永远无法被仲裁的 Entry。
            const BaseFlit &base = entry.baseFlit();
            if (!entry.state || find(base.srcId(), base.txnId(), &entry)) {
                queue.pop_back();
                return false;
            }

            entry.qosPriority = priority;
            entry.qosPoolReserved = qos_pool_reserved;
            Sleep_if_Hazard(entry);
            ++activeEntries;
            assert(queue.size() == activeEntries);
            return true;
        }

        Entry *
        find(uint32_t srcid, uint32_t txnid, const Entry *ignore = nullptr)
        {
            for (Entry &entry : queue) {
                if (&entry == ignore || !entry.valid)
                    continue;

                const BaseFlit &base = entry.baseFlit();
                if (base.srcId() == srcid && base.txnId() == txnid)
                    return &entry;
            }
            return nullptr;
        }

        bool
        retire(Entry &entry)
        {
            if (!entry.valid)
                return false;

            assert(!entry.is_sleep);
            const bool woke_entry = !entry.sleep_queue.empty();
            entry.deallocate();
            entry.valid = false;
            --activeEntries;

            // A sleeping entry cannot retire. The retiring runnable head has
            // already transferred its dependent chain, and erasing this list
            // node preserves every other entry's address and age order.
            const auto position = std::find_if(queue.begin(), queue.end(),
                [&entry](const Entry &other) { return &entry == &other; });
            assert(position != queue.end());
            queue.erase(position);
            assert(queue.size() == activeEntries);
            return woke_entry;
        }

        bool Sleep_if_Hazard(Entry &entry)
        {
            // Address ordering applies to request entries only. RSP/SNP/DAT
            // do not carry the RawReq address stored in Entry::rawflit.req.
            if (entry.channel != REQ)
                return false;

            for (Entry &x : queue) {
                if (&x != &entry && x.valid && x.channel == REQ &&
                    (x.ReqAddr() >> 6) == (entry.ReqAddr() >> 6) &&
                    x.rawflit.req.ns == entry.rawflit.req.ns) {
                    entry.is_sleep = true;
                    x.setSleepQueue(&entry);
                    return true;
                }
            }
            return false;
        }

        win_array Arb_Oldest()
        {
            // queue is traversed from oldest to newest. Only fill an empty
            // slot, so each result remains the oldest eligible entry.
            win_array ans{};
            using PriorityCandidates =
                std::array<std::array<Entry *, PoolPriorityNum>, Num_ArbWin>;
            PriorityCandidates candidates{};

            auto rememberOldest = [&candidates](Arb_win arb, Entry &entry) {
                const auto priority =
                    static_cast<std::size_t>(entry.qosPriority);
                if (!candidates[arb][priority])
                    candidates[arb][priority] = &entry;
            };

            for (Entry &entry : queue) {
                if (!entry.valid || entry.is_sleep || !entry.state)
                    continue;

                // Action 在边上：step() 后 state 已经到了下一个
                // waiting node，所以仲裁资源必须看 pending action，
                // 不能只看当前 node 名字。
                for (const PocqAction &action : entry.pendingActions) {
                    const auto arb = arbForAction(entry, action.kind);
                    if (!arb)
                        continue;

                    if (*arb == SlcLookup) {
                        if (!ans[SlcLookup])
                            ans[SlcLookup] = &entry;
                    } else {
                        rememberOldest(*arb, entry);
                    }
                }
            }

            // PoolPriority is ordered HighHigh, High, Medium, Low. For each
            // resource choose the highest non-empty priority, preserving
            // oldest order within that priority.
            for (int arb = MCRead; arb <= McRetry; ++arb) {
                for (int priority = HighHigh;
                     priority < PoolPriorityNum; ++priority) {
                    Entry *candidate = candidates[arb][priority];
                    if (candidate) {
                        ans[arb] = candidate;
                        break;
                    }
                }
            }

            return ans;
        }

        std::optional<PocqAction>
        takeAction(Entry &entry, Arb_win arb)
        {
            for (auto it = entry.pendingActions.begin();
                 it != entry.pendingActions.end(); ++it) {
                if (arbForAction(entry, it->kind) != arb)
                    continue;

                PocqAction action = *it;
                entry.pendingActions.erase(it);
                return action;
            }
            return std::nullopt;
        }

        bool
        hasPendingActions() const
        {
            for (const Entry &entry : queue) {
                if (entry.valid && !entry.is_sleep &&
                    !entry.pendingActions.empty()) {
                    return true;
                }
            }
            return false;
        }

        bool hasFreeEntry() const { return !is_full(); }
        std::size_t size() const { return activeEntries; }

        bool is_full() const
        {
            return activeEntries >= m_entry_num;
        }

      private:
        static std::optional<Arb_win>
        arbForAction(const Entry &entry, PocqActionKind action)
        {
            switch (action) {
              case PocqActionKind::StartSlcLookup:
              case PocqActionKind::FlushSf:
              case PocqActionKind::FlushL3:
              case PocqActionKind::WriteL3:
              case PocqActionKind::WriteL3FlushSf:
              case PocqActionKind::UpdateSf:
                return SlcLookup;
              case PocqActionKind::SendReadNoSnp:
                return entry.isAt(PocqNodeRole::McRetry)
                    ? McRetry : MCRead;
              case PocqActionKind::SendCompData:
                return TxDat;
              case PocqActionKind::SendSnpOnce:
              case PocqActionKind::SendSnpOnceFwd:
              case PocqActionKind::SendSnpShared:
              case PocqActionKind::SendSnpUnique:
              case PocqActionKind::SendSnpMakeInvalid:
              case PocqActionKind::SendSnpCleanInvalid:
                return TxSnp;
              case PocqActionKind::SendCompAck:
              case PocqActionKind::SendComp:
              case PocqActionKind::SendCompDbidResp:
              case PocqActionKind::SendDbidResp:
                return TxRsp;
            }
            return std::nullopt;
        }

        std::size_t m_entry_num;
        std::size_t activeEntries = 0;
        const PocqGraphPanel &graphPanel;
    };

    HomeNodeFull *m_homenode;
    PocqGraphPanel graphPanel;
    std::size_t m_entry_num;
    POCQueue m_pocq;
    PocqVictimBuffer victimBuffers;

    // classic-cache 风格事件成员: 绑定 process()
    EventFunctionWrapper processEvent;

    // 待处理的触发 (积累, process() 时一并处理; 同一拍多个触发合并)
    struct Trigger
    {
        uint32_t srcid;
        uint32_t txnid;
        PocqEvent event;
    };
    std::vector<Trigger> m_pending;

    std::deque<IssuedAction> issuedActions;

    static const BaseFlit &baseFlit(const FlitVariant &flit);
    void startReadyEntries();
    void arbitrateActions();
    bool retireCompletedEntries();
    bool hasPendingPocqWork() const;
    bool allocateImpl(const FlitVariant &flit, PoolPriority priority,
                      bool qos_pool_reserved, bool internal_seq = false);
    bool issueAction(Entry &entry, const PocqAction &action);
    Entry *findWire(uint32_t wire);
    uint64_t nextToken = 1;
    uint32_t nextWireId = 1;
    uint32_t allocateWireId();
    struct PendingLink
    {
        Tick ready;
        std::variant<RawRsp, RawDat> flit;
    };
    std::deque<PendingLink> incoming;
    void consumeLink();
    void processLink(const RawRsp &rsp);
    void processLink(const RawDat &dat);
    void collectSlcResults();
    void acceptSeq();
    void progressEntry(Entry &entry);
    void progressWritebacks(Entry &entry);
    void queueWriteback(Entry &entry, uint64_t addr, bool ns,
                        const SlcLineData &data);
    void dispatch(Entry &entry, PocqEvent event);
    bool sendSnoops(Entry &entry, uint8_t opcode);
    bool sendCompletion(Entry &entry, bool data);
    bool operationsComplete(const Entry &entry) const;

    // 单个触发的状态机推进 (选 Entry → state->step → 收集 action)。
    void step(const Trigger &t);
};

}  // namespace gem5::Chi

#endif  // __HOMEPOCQ__HH__

#include "mem/cache/CHI/HomePocq.hh"

#include "base/trace.hh"
#include "debug/HomePocq.hh"
#include "mem/cache/CHI/HomeNodeFull.hh"

namespace gem5::Chi
{

HomePocq::HomePocq(HomeNodeFull *hnf, std::size_t entry_num)
    : Consumer(hnf),
      m_homenode(hnf),
      graphPanel(),
      m_entry_num(entry_num),
      m_pocq(m_entry_num, graphPanel),
      processEvent([this] { process(); },
                   getObject()->name() + ".processEvent")
{}

const BaseFlit &
HomePocq::baseFlit(const FlitVariant &flit)
{
    return std::visit(
        [](const auto &raw) -> const BaseFlit & { return raw; }, flit);
}

bool
HomePocq::allocate(const FlitVariant &flit)
{
    return allocateImpl(flit, baseFlit(flit).qosPriority(), false);
}

bool
HomePocq::allocate(const FlitVariant &flit, PoolPriority reserved_pool)
{
    return allocateImpl(flit, reserved_pool, true);
}

bool
HomePocq::allocateImpl(const FlitVariant &flit, PoolPriority priority,
                       bool qos_pool_reserved)
{
    const BaseFlit &raw = baseFlit(flit);
    if (!m_pocq.allocate(flit, priority, qos_pool_reserved)) {
        DPRINTF(HomePocq,
                "allocate: reject srcid=%u txnid=%u opcode=%#x "
                "(full, duplicate, or graph not implemented)\n",
                raw.srcid, raw.txnid, raw.opcode);
        return false;
    }

    DPRINTF(HomePocq,
            "allocate: srcid=%u txnid=%u opcode=%#x entries=%zu/%zu\n",
            raw.srcid, raw.txnid, raw.opcode,
            m_pocq.size(), m_entry_num);

    // LinkLayer 只负责准入。POCQ 用自己的 Consumer 事件在
    // 下一拍先同时推进所有可运行 Entry，再仲裁 action。
    scheduleEvent(Cycles(1));
    return true;
}

bool
HomePocq::hasFreeEntry() const
{
    return m_pocq.hasFreeEntry();
}

std::optional<HomePocq::IssuedAction>
HomePocq::popIssuedAction()
{
    if (issuedActions.empty())
        return std::nullopt;

    IssuedAction action = issuedActions.front();
    issuedActions.pop_front();
    return action;
}

// ────────────── 触发入口 (link/SLC 调) ──────────────
void
HomePocq::trigger(uint32_t srcid, uint32_t txnid, const PocqEvent &event)
{
    DPRINTF(HomePocq, "trigger: srcid=%u txnid=%u event=%u source=%u\n",
            srcid, txnid, static_cast<unsigned>(event.kind),
            static_cast<unsigned>(event.source));

    // 记录触发
    m_pending.push_back({srcid, txnid, event});

    // 排到下一拍 (不是本拍): classic-cache 风格。
    // 已有 pending 事件就不重复排 (同一拍多个触发合并到一次 process)。
    if (!processEvent.scheduled())
        getObject()->schedule(processEvent, getObject()->nextCycle());
}

// ────────────── 事件处理: 取出 pending, 逐个推进 ──────────────
void
HomePocq::process()
{
    DPRINTF(HomePocq, "process: %zu pending trigger(s)\n", m_pending.size());

    // 取出本拍要处理的所有触发 (swap 后清空, 避免处理中再 push 干扰)
    std::vector<Trigger> ready;
    ready.swap(m_pending);

    for (const Trigger &t : ready)
        step(t);
}

void
HomePocq::issueAction(const Entry &entry, const PocqAction &action)
{
    const BaseFlit &base = entry.baseFlit();
    issuedActions.push_back({base.srcId(), base.txnId(), action});
    DPRINTF(HomePocq,
            "action: srcid=%u txnid=%u action=%s target=%u\n",
            base.srcId(), base.txnId(), pocqActionName(action.kind),
            static_cast<unsigned>(action.target));
}

void
HomePocq::startReadyEntries()
{
    for (Entry &entry : m_pocq.queue) {
        if (!entry.valid || entry.is_sleep || entry.completed ||
            !entry.isAt(PocqNodeRole::Entry) ||
            !entry.pendingActions.empty()) {
            continue;
        }

        const auto result = entry.step(PocqEvent::enter());
        const BaseFlit &base = entry.baseFlit();
        if (!result) {
            DPRINTF(HomePocq,
                    "start: srcid=%u txnid=%u rejected Enter\n",
                    base.srcId(), base.txnId());
            continue;
        }

        entry.appendActions(*result);
        DPRINTF(HomePocq,
                "start: srcid=%u txnid=%u state=%s actions=%zu\n",
                base.srcId(), base.txnId(),
                entry.state ? pocqNodeRoleName(entry.state->key().role)
                            : "null",
                result->action_count());
    }
}

void
HomePocq::arbitrateActions()
{
    const POCQueue::win_array winners = m_pocq.Arb_Oldest();

    for (int resource = POCQueue::SlcLookup;
         resource < POCQueue::Num_ArbWin; ++resource) {
        Entry *winner = winners[resource];
        if (!winner)
            continue;

        const auto action = m_pocq.takeAction(
            *winner, static_cast<POCQueue::Arb_win>(resource));
        if (action)
            issueAction(*winner, *action);
    }
}

bool
HomePocq::retireCompletedEntries()
{
    std::vector<Entry *> completed;
    for (Entry &entry : m_pocq.queue) {
        if (entry.valid && entry.completed && entry.pendingActions.empty())
            completed.push_back(&entry);
    }

    bool woke_entry = false;
    for (Entry *entry : completed) {
        const BaseFlit &base = entry->baseFlit();
        DPRINTF(HomePocq,
                "retire: srcid=%u txnid=%u\n",
                base.srcId(), base.txnId());
        if (entry->qosPoolReserved)
            m_homenode->releasePocqQos(entry->qosPriority);
        woke_entry |= m_pocq.retire(*entry);
    }
    return woke_entry;
}

bool
HomePocq::hasPendingPocqWork() const
{
    if (m_pocq.hasPendingActions())
        return true;

    for (const Entry &entry : m_pocq.queue) {
        if (entry.valid && !entry.is_sleep && !entry.completed &&
            entry.isAt(PocqNodeRole::Entry)) {
            return true;
        }
    }
    return false;
}

// ────────────── 单个触发的状态机推进 ──────────────
void
HomePocq::step(const Trigger &t)
{
    Entry *entry = m_pocq.find(t.srcid, t.txnid);
    if (!entry) {
        DPRINTF(HomePocq,
                "step: srcid=%u txnid=%u has no active Entry\n",
                t.srcid, t.txnid);
        return;
    }

    if (entry->is_sleep) {
        DPRINTF(HomePocq,
                "step: srcid=%u txnid=%u is sleeping on address hazard\n",
                t.srcid, t.txnid);
        return;
    }

    if (!entry->state) {
        DPRINTF(HomePocq,
                "step: srcid=%u txnid=%u Entry has no state\n",
                t.srcid, t.txnid);
        return;
    }

    auto step_result = entry->step(t.event);
    if (!step_result) {
        const PocqNodeKey state = entry->state->key();
        DPRINTF(HomePocq,
                "step: graph=%s state=%s rejected event=%u\n",
                pocqGraphName(state.graph), pocqNodeRoleName(state.role),
                static_cast<unsigned>(t.event.kind));
        return;
    }

    entry->appendActions(*step_result);

    if (step_result->is_complete()) {
        DPRINTF(HomePocq,
                "step: srcid=%u txnid=%u graph completed\n",
                t.srcid, t.txnid);
    }

    // step() 只产生 pending action；真正发出要到下一拍经过
    // Arb_Oldest()，保证多个 event 不在本拍级联。
    scheduleEvent(Cycles(1));
}

// ────────────── Consumer 接口: 每拍仲裁并推进赢家 ──────────────
void
HomePocq::wakeup()
{
    DPRINTF(HomePocq, "wakeup: %zu POCQ slot(s)\n", m_pocq.size());

    // 所有可运行的新 Entry 同拍从 Entry/Idle 推进到下一个
    // node。随后每类资源只发射一个赢家；输家保留
    // 当前 node 和 pending action，下拍继续参与仲裁。
    startReadyEntries();
    arbitrateActions();
    const bool woke_entry = retireCompletedEntries();

    if (hasPendingPocqWork() || woke_entry) {
        scheduleEvent(Cycles(1));
    }
}

}  // namespace gem5::Chi

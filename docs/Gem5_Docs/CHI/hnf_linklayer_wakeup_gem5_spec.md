# HNF HomeLinkLayer gem5 C++ Wakeup 方式编程 Spec

版本：v0.2-wakeup-skeleton
目标仓库：`makenma/XS-DSU-GEM5`
目标骨架路径：`src/mem/cache/CHI/HomeLinkLayer.{hh,cc}`、`HomeNodeFull.{hh,cc}`、`base/ChiChannel.hh`、`base/ChiCommonPort.hh`

---

## 0. 这版 spec 的核心变化

这版不再要求把 LinkLayer 写成单独的 `tick()` 函数，而是**沿用当前骨架的 `HomeNodeFull::wakeup() -> HomeLinkLayer::wakeup()` 方式**。

但是必须修正当前骨架的三个时序问题：

1. `ChiCommonPort::enqueueFlit()` 当前直接调用 `dst.m_consumer->wakeup()`，这会形成同调用栈、同周期的 0-delay 接收路径。需要改成 schedule 下一拍事件。
2. `HomeLinkLayer::wakeup()` 当前从 `rxport->getRxFlit()` 取出 flit 后只在局部变量上 `advancePipeline(rxflit)`，stage 加一后没有写回持久 pipeline，因此 H1/H2/H3 不会在后续周期继续推进。
3. `ChiCommonPort::getRxFlit()` 当前 dequeue 时立即 `credit++`，这对 RXREQ 不成立。RXREQ credit 只能在 dynamic/static allocation 成功或 RetryAck 真正发送成功后返还。

最终模型仍然是 gem5 wakeup/event 驱动模型，但内部要像 RTL pipeline 一样，每个 wakeup 只表示一个 link cycle，所有新产生的工作只能在下一次 wakeup 被后级看到。

---

## 1. 必须保留的骨架风格

### 1.1 保留 HomeNodeFull 调用入口

当前骨架中 `HomeNodeFull` 持有 `HomeLinkLayer linklayer` 和 `ChiCommonPort rxport`，构造时把 `rxport` 传给 LinkLayer；`HomeNodeFull::wakeup()` 调用 `linklayer.wakeup()`。保留这个结构。

```cpp
class HomeNodeFull : public BasicChiComponent, public ruby::Consumer
{
  public:
    HomeNodeFull(const HomeNodeFullParams& p);
    void wakeup() override;
    Port& getPort(const std::string& if_name, PortID idx) override;

  private:
    HomeLinkLayer linklayer;
    ChiCommonPort rxport;
};
```

`HomeNodeFull::wakeup()` 只做调度入口，不在 HomeNodeFull 里实现 Retry/QoS/TXRSP 逻辑：

```cpp
void
HomeNodeFull::wakeup()
{
    linklayer.wakeup();
}
```

### 1.2 保留每个 flit 类型的 stage function 表

当前 `HomeLinkLayer` 已经有：

```cpp
reqFuncs = {
    &HomeLinkLayer::doStageH0_Req,
    &HomeLinkLayer::doStageH1_Req,
    &HomeLinkLayer::doStageH2_Req,
    &HomeLinkLayer::doStageH3_Req
};
```

这版继续保留这种写法，但函数签名要从 `void(RawReq*)` 改成可以返回 stage 行为的形式。

推荐签名：

```cpp
struct PipeEntry;

struct StageResult
{
    enum class Action : uint8_t
    {
        Stay,       // 本 stage 保持，等待资源或 backpressure
        Advance,    // 下一拍进入下一级 stage
        Drop,       // 当前 flit 已被消费，不再进入后级
        Error       // 协议错误或模型错误
    } action = Action::Stay;
};

template<typename FlitType>
using MemFn = StageResult (HomeLinkLayer::*)(PipeEntry&, FlitType&);
```

如果 Codex 想尽量少改，也可以保留 `void` 函数签名，但 stage 函数必须通过 `PipeEntry` 标记结果，不能在函数内部直接把 flit 同拍送进后级。

---

## 2. wakeup 调度规则

### 2.1 绝对禁止直接 wakeup

禁止跨模块在 enqueue / credit / port 操作里同步调用对方的 wakeup()，禁止把 wakeup() 当成组合逻辑回调；允许 HomeNodeFull 在 gem5 event 触发后，作为本周期入口调用 LinkLayer 的 wakeup()

禁止：

```cpp
dst.m_consumer->wakeup();
```

必须改成：

```cpp
dst.m_consumer->scheduleEvent(Cycles(1));
```

或者在 `HomeNodeFull` 增加一个 helper：

```cpp
void
HomeNodeFull::scheduleNextCycle()
{
    scheduleEvent(Cycles(1));
}
```

LinkLayer 内部统一调用：

```cpp
void
HomeLinkLayer::scheduleNextCycle()
{
    m_homenode->scheduleEvent(Cycles(1));
}
```

硬约束：

```cpp
// delay 必须 >= 1 cycle
assert(delayCycles >= 1);
```

### 2.2 wakeup 只表示一个 link cycle

每次 `HomeLinkLayer::wakeup()` 执行以下阶段。顺序不能随意换，因为它用来避免 0-delay producer -> consumer 链。

```cpp
void
HomeLinkLayer::wakeup()
{
    DPRINTF(HomeLinkLayer, "cycle=%llu wakeup\n", llCycle);

    // 1. 先消费上一周期已经存在的 TX 队列。
    //    这样本周期新生成的 RetryAck / PCrdGrant 不会同拍发送。
    doTxRspArb();

    // 2. 处理上一周期已经到期的 credit event。
    //    本周期 TX 成功新产生的 credit event dueCycle = llCycle + 1。
    doCreditEvents();

    // 3. 处理 CC 返回结果，包含 admit result 和 retire event。
    //    retire event 只产生 held token / pcrdGrant candidate，不允许同拍 TX。
    doCcResultAndRetire();

    // 4. 处理上一周期 retry decision，生成 RetryAck FIFO 和 pending retry counter。
    //    因为 TXRSP 仲裁已经在本周期第 1 步完成，所以不会同拍发 RetryAck。
    doRetryWakeup();

    // 5. 处理 held retired token，尝试转换成 static reservation 并入 PCrdGrant FIFO。
    //    因为 TXRSP 仲裁已经完成，所以不会同拍发 PCrdGrant。
    doPcrdGrantWakeup();

    // 6. 推进 RX pipeline。必须按 stage 从高到低处理，防止 H0->H1 同拍连跑。
    doRxPipelineWakeup();

    // 7. 从 rxport 取新 flit 放入 H0 input latch。
    //    本周期取到的新 flit 不能同拍执行 H0 stage。
    sampleRxPortsToH0();

    // 8. 统计、assert、决定是否继续 schedule 下一拍。
    doAssertionsAndStats();

    llCycle++;

    if (hasPendingWork()) {
        scheduleNextCycle();
    }
}
```

这套顺序保证以下不可能发生：

```text
RXREQ arrival -> H1 retry -> RetryAck FIFO -> TXRSP arb -> RetryAck TX -> RXREQ credit return
```

同一个 wakeup 内最多只能完成其中一个局部动作，后续动作要等下一次 wakeup。

---

## 3. Pipeline queue 设计

### 3.1 PipeEntry

不要再把 flit 作为 `advancePipeline()` 的局部变量处理。需要持久化 pipeline entry。

```cpp
struct PipeEntry
{
    FlitVariant flit;
    ChannelType channel = ChannelType::REQ;

    uint32_t stage = 0;
    uint64_t seq = 0;
    uint64_t enterCycle = 0;

    // Link admission metadata
    bool retry = false;
    bool dynamicAllocated = false;
    bool staticAllocated = false;
    bool sentToCc = false;

    int tokenId = -1;
    uint8_t priority = 0;
    uint8_t resourceClass = 0;
};
```

`BaseFlit::stage` 可以保留，但推荐 `PipeEntry::stage` 作为 pipeline 真正依据。`BaseFlit::stage` 仅用于 debug。

### 3.2 Stage queues

每个 channel 独立 pipeline：

```cpp
static constexpr size_t NumCh = static_cast<size_t>(ChannelType::NUM_CHANNELS);
static constexpr size_t NumLlStages = 4; // H0/H1/H2/H3 for MVP

using StageQueue = std::deque<PipeEntry>;
using ChannelPipe = std::array<StageQueue, NumLlStages>;

std::array<ChannelPipe, NumCh> rxPipe;
```

每个 wakeup 处理时必须从高 stage 到低 stage：

```cpp
void
HomeLinkLayer::doRxPipelineWakeup()
{
    for (ChannelType ch : allChannels()) {
        auto& pipe = rxPipe[toIdx(ch)];
        for (int st = NumLlStages - 1; st >= 0; --st) {
            if (pipe[st].empty()) {
                continue;
            }
            processOneStageEntry(ch, st, pipe[st].front());
        }
    }
}
```

处理完一个 stage 后：

```cpp
switch (result.action) {
  case StageResult::Action::Advance:
      entry.stage++;
      pipe[st + 1].push_back(std::move(entry)); // 因为 st+1 已经处理过，所以不会同拍再跑
      pipe[st].pop_front();
      break;

  case StageResult::Action::Stay:
      // 不 pop
      break;

  case StageResult::Action::Drop:
      pipe[st].pop_front();
      break;

  case StageResult::Action::Error:
      panic("HomeLinkLayer stage error");
}
```

### 3.3 H0 sample 不同拍处理

`sampleRxPortsToH0()` 放在 wakeup 末尾：

```cpp
void
HomeLinkLayer::sampleRxPortsToH0()
{
    for (ChannelType ch : allChannels()) {
        auto flit = rxport->getRxFlitNoCredit(ch);
        if (!flit.has_value()) {
            continue;
        }

        PipeEntry e;
        e.flit = flit.value();
        e.channel = ch;
        e.stage = 0;
        e.seq = nextSeq++;
        e.enterCycle = llCycle;

        rxPipe[toIdx(ch)][0].push_back(std::move(e));
    }
}
```

这意味着外部 port 收到 flit 后，最早下一次 LinkLayer wakeup 进入 H0，再下一次 wakeup 执行 H0 stage。这样能避免 port enqueue 和 Link decode 之间的 0-delay。

如果以后确认 RTL 存在 H0 bypass，可以单独加 `enableH0SameCycleSample` 参数，但 MVP 必须关闭。

---

## 4. ChiCommonPort 必须修改

当前 `ChiCommonPort::enqueueFlit()` 有两个问题：

1. enqueue 后直接 `wakeup()`，导致 0-delay。
2. dequeue 时立即归还 credit，RXREQ credit 返还点错误。

### 4.1 增加 no-credit dequeue

保留原函数供非严格模型使用，但 LinkLayer 必须使用 no-credit 版本：

```cpp
std::optional<FlitVariant>
getRxFlitNoCredit(ChannelType ch)
{
    auto* queues = queueArray(QueueKind::Rx);
    size_t idx = static_cast<size_t>(ch);
    if (queues[idx].empty()) {
        return std::nullopt;
    }

    FlitVariant f = std::move(queues[idx].front());
    queues[idx].pop_front();
    return f;
}
```

### 4.2 显式返还 RX credit

```cpp
void
returnRxCredit(ChannelType ch, uint8_t val = 1)
{
    auto idx = static_cast<size_t>(ch);
    rxCredit[idx] += val;
    checkRxCredit(ch);
}
```

RXREQ 只能在以下事件触发：

```text
dynamic allocation 成功并被 CC 接收
static allocation 成功并被 CC 接收
RetryAck TXRSP 真实发送成功
```

其他 RX channel 可以先简化为 “成功送入 CC input queue 后返还 credit”，但也不要在 port dequeue 当拍无条件返还。

### 4.3 enqueue 只能 schedule 下一拍

```cpp
bool
enqueueFlit(QueueKind kind, ChannelType ch, const FlitVariant& f)
{
    ChiCommonPort& dst = targetPort();
    ...
    queues[idx].push_back(f);

    assert(dst.m_consumer && "ChiCommonPort m_consumer is null");
    dst.m_consumer->scheduleEvent(Cycles(1));
    return true;
}
```

如果 `ruby::Consumer` 不能从这里直接调用 `scheduleEvent(Cycles(1))`，则新增接口：

```cpp
class ChiPortWakeupTarget
{
  public:
    virtual void schedulePortWakeup(Cycles delay) = 0;
};
```

由 `HomeNodeFull` 实现，`ChiCommonPort` 保存 `ChiPortWakeupTarget*`。

---

## 5. Flit 字段补全

当前 `RawReq` 字段不足以做 Retry/QoS/PCrdGrant。需要在 `ChiChannel.hh` 中补字段。

```cpp
struct RawReq : BaseFlit
{
    uint8_t  AllowRetry = 0;
    uint64_t addr = 0;
    uint8_t  size = 0;
    uint32_t ReturnNid = 0;

    // Required by Retry/QoS
    uint8_t  order = 0;
    uint8_t  pcrdtype = 0;
    uint8_t  memattr = 0;
    uint8_t  snpattr = 0;
    bool     expCompAck = false;
    bool     traceTag = false;
    uint8_t  srcType = 0;
    uint8_t  ldid = 0;
};
```

`RawRsp` 需要能表达 RetryAck / PCrdGrant / ReadReceipt / DBIDResp：

```cpp
enum class RspKind : uint8_t
{
    MainPath,
    ShortPath,
    RetryAck,
    PCrdGrant
};

struct RawRsp : BaseFlit
{
    uint16_t dbid = 0;
    uint8_t  resp = 0;
    uint8_t  respErr = 0;
    uint8_t  pcrdtype = 0;
    RspKind  rspKind = RspKind::MainPath;

    uint64_t originSeq = 0;
    uint64_t originCycle = 0;
};
```

---

## 6. Link 与 CC 的接口先留结构体

LinkLayer 不创建完整 `TxnContext`。MVP 只保留结构体。

### 6.1 Link -> CC

```cpp
struct LinkToCcReq
{
    bool valid = false;
    uint64_t seq = 0;
    ChannelType channel = ChannelType::REQ;

    FlitVariant flit;

    int tokenId = -1;
    uint8_t priority = 0;
    uint8_t resourceClass = 0;

    bool isDynamic = false;
    bool isStatic = false;
    bool isFvb = false;

    bool shortPathCandidate = false;
    enum class ShortPathKind : uint8_t {
        None,
        ReadReceipt,
        DBIDResp,
        CompDBIDResp
    } shortPathKind = ShortPathKind::None;
};
```

### 6.2 CC -> Link

```cpp
struct CcAdmitResult
{
    bool valid = false;
    uint64_t seq = 0;
    int tokenId = -1;
    bool accepted = false;

    bool hazard = false;
    bool shortPathAllowed = false;
};

struct CcRetireEvent
{
    bool valid = false;
    int tokenId = -1;
    uint8_t resourceClass = 0;
    uint8_t reqPriority = 0;
    uint32_t srcid = 0;
    uint8_t pcrdtype = 0;
};

struct CcToLinkBundle
{
    bool rxreqReady = true;
    bool rxrspReady = true;
    bool rxdatReady = true;
    bool fvbReady = true;

    CcAdmitResult admit;
    std::vector<CcRetireEvent> retires;

    std::optional<RawRsp> txrspMain;
    // txreqMain / txdatMain / txsnpMain can be added later.
};
```

### 6.3 CC 接口时序

禁止：

```text
Link H1 送 CC -> CC 同拍 accepted -> Link 同拍生成 short path/credit
```

必须：

```text
cycle N:   Link 写 linkToCcQ
cycle N+1: CC 最早看到该请求
cycle N+2: Link 最早处理 CcAdmitResult
cycle N+3: ShortPath 最早进入 TXRSP arb 可见队列
```

MVP 中 CC 可以是 stub，但必须通过 `CcToLinkBundle` 回写，不能在 LinkLayer stage 函数中直接调用 CC 方法拿组合返回值。

---

## 7. Resource token ledger

LinkLayer 不建完整 entry，但必须保存资源账本。

```cpp
enum class TokenState : uint8_t
{
    Free,
    AllocPendingCcAck,
    WorkingDynamic,
    WorkingStatic,
    WorkingFvb,
    RetireHeldForPCrdGrant,
    StaticReserved
};

struct ResourceToken
{
    int id = -1;
    TokenState state = TokenState::Free;

    uint8_t resourceClass = 0;   // Low/Medium/High/HHigh/FVB
    uint8_t reqPriority = 0;

    uint32_t ownerSrcid = 0;
    uint8_t pcrdtype = 0;

    uint64_t allocatedSeq = 0;
    uint64_t allocatedCycle = 0;

    uint32_t staticOwnerSrcid = 0;
    uint8_t staticPcrdtype = 0;
    uint8_t staticPriority = 0;
};
```

PCrdGrant 必须绑定一个具体 token：

```text
retire dynamic token
-> token.state = RetireHeldForPCrdGrant
-> select pending retry winner
-> token.state = StaticReserved
-> RawRsp(rspKind=PCrdGrant) 进入 pcrdGrantFifo
```

禁止：

```text
发了 PCrdGrant 但 token 已经 Free
pending retry-- 但 pcrdGrantFifo 满
PCrdGrant 没有 static reservation
```

---

## 8. QoS priority 与动态分配

### 8.1 Priority 定义

```cpp
enum class LlPriority : uint8_t
{
    Low = 0,
    Medium = 1,
    High = 2,
    HHigh = 3,
    Num = 4
};

enum class ResourceClass : uint8_t
{
    Low = 0,
    Medium = 1,
    High = 2,
    HHigh = 3,
    Fvb = 4
};
```

默认 QoS range：

```text
QoS 15    -> HHigh
QoS 12-14 -> High
QoS 8-11  -> Medium
QoS 0-7   -> Low
```

FVB 固定使用 FVB resource，不参与普通 QoS pool。

### 8.2 动态分配规则

`AllowRetry=1`：

```cpp
std::optional<int>
HomeLinkLayer::allocDynamicToken(LlPriority prio)
{
    for (int cls = static_cast<int>(prio); cls >= static_cast<int>(LlPriority::Low); --cls) {
        if (auto token = findFreeToken(static_cast<ResourceClass>(cls)); token.has_value()) {
            return token;
        }
    }
    return std::nullopt;
}
```

高优先级可以占低优先级资源，低优先级不能占高优先级资源。

`AllowRetry=0`：

```cpp
std::optional<int>
HomeLinkLayer::findStaticReservation(uint32_t srcid, uint8_t pcrdtype)
{
    for (auto& t : tokens) {
        if (t.state == TokenState::StaticReserved &&
            t.staticOwnerSrcid == srcid &&
            t.staticPcrdtype == pcrdtype) {
            return t.id;
        }
    }
    return std::nullopt;
}
```

没有 static reservation 的 `AllowRetry=0` 请求是协议错误，不能重新 Retry，不能偷偷使用 dynamic resource。

---

## 9. RXREQ stage 行为

### 9.1 H0

H0 只做 latch/debug，不分配资源。

```cpp
StageResult
HomeLinkLayer::doStageH0_Req(PipeEntry& e, RawReq& req)
{
    DPRINTF(HomeLinkLayer, "H0 REQ seq=%llu src=%u txn=%u\n",
            e.seq, req.srcid, req.txnid);
    return {StageResult::Action::Advance};
}
```

### 9.2 H1：decode/admission

H1 是 Retry/QoS 的主判断点。

```cpp
StageResult
HomeLinkLayer::doStageH1_Req(PipeEntry& e, RawReq& req)
{
    if (!ccIn.rxreqReady) {
        return {StageResult::Action::Stay};
    }

    LlPriority prio = mapPriority(req);
    e.priority = static_cast<uint8_t>(prio);

    if (req.AllowRetry) {
        auto tokenId = allocDynamicToken(prio);
        if (!tokenId.has_value()) {
            RetryRecord rec = makeRetryRecord(e, req, prio);

            // 只写 retryDecisionQ，不能同拍写 retryAckFifo。
            retryDecisionQ.push_back(rec);
            return {StageResult::Action::Drop};
        }

        reserveTokenPendingCcAck(*tokenId, e, req, prio, /*isStatic*/false);
        linkToCcQ.push_back(makeLinkToCcReq(e, req, *tokenId, prio, /*dynamic*/true));
        return {StageResult::Action::Drop};
    }

    auto tokenId = findStaticReservation(req.srcid, req.pcrdtype);
    if (!tokenId.has_value()) {
        panic("AllowRetry=0 request without static reservation: srcid=%u pcrdtype=%u",
              req.srcid, req.pcrdtype);
    }

    consumeStaticReservationPendingCcAck(*tokenId, e, req, prio);
    linkToCcQ.push_back(makeLinkToCcReq(e, req, *tokenId, prio, /*dynamic*/false));
    return {StageResult::Action::Drop};
}
```

### 9.3 H2/H3

MVP 可以保留为空 stage，用于以后 ShortPath / CRC / decode expansion。

```cpp
StageResult doStageH2_Req(PipeEntry& e, RawReq& req) { return {StageResult::Action::Advance}; }
StageResult doStageH3_Req(PipeEntry& e, RawReq& req) { return {StageResult::Action::Drop}; }
```

---

## 10. RetryAck path

### 10.1 RetryRecord

```cpp
struct RetryRecord
{
    uint64_t originSeq = 0;
    uint64_t retryCycle = 0;

    uint32_t srcid = 0;
    uint32_t tgtid = 0;
    uint32_t txnid = 0;
    uint8_t qos = 0;
    uint8_t priority = 0;
    uint8_t pcrdtype = 0;
};
```

### 10.2 Pending retry counter

不能用 bit。

```cpp
struct PendingRetryTable
{
    // [priority][srcid][pcrdtype] -> count
    std::map<uint8_t, std::map<uint32_t, std::map<uint8_t, uint16_t>>> cnt;
    std::map<uint8_t, std::map<uint32_t, std::map<uint8_t, uint16_t>>> age;

    uint16_t totalForSrc(uint32_t srcid) const;
    void increment(uint8_t prio, uint32_t srcid, uint8_t pcrdtype);
    void decrement(uint8_t prio, uint32_t srcid, uint8_t pcrdtype);
};
```

每个 RN outstanding retry 上限：

```cpp
assert(pendingRetry.totalForSrc(srcid) <= 256);
```

### 10.3 doRetryWakeup

```cpp
void
HomeLinkLayer::doRetryWakeup()
{
    if (retryDecisionQ.empty()) {
        return;
    }

    const RetryRecord& rec = retryDecisionQ.front();

    if (retryAckFifo.size() >= retryAckFifoDepth) {
        stats.retryAckFifoFull++;
        return;
    }

    RawRsp rsp;
    rsp.rspKind = RspKind::RetryAck;
    rsp.qos = rec.qos;
    rsp.srcid = rec.tgtid;  // response source is HNF
    rsp.tgtid = rec.srcid;  // response target is requester
    rsp.txnid = rec.txnid;
    rsp.pcrdtype = rec.pcrdtype;
    rsp.originSeq = rec.originSeq;
    rsp.originCycle = rec.retryCycle;

    retryAckFifo.push_back(rsp);

    pendingRetry.increment(rec.priority, rec.srcid, rec.pcrdtype);
    assert(pendingRetry.totalForSrc(rec.srcid) <= 256);

    retryDecisionQ.pop_front();
}
```

因为 `doTxRspArb()` 在 wakeup 最开始执行，所以本周期 `doRetryWakeup()` 新 push 的 `retryAckFifo` 最早下一周期发送。

---

## 11. PCrdGrant path

### 11.1 retire event 处理

```cpp
void
HomeLinkLayer::doCcResultAndRetire()
{
    processCcAdmitResult();

    for (const auto& ev : ccIn.retires) {
        if (!ev.valid) {
            continue;
        }

        auto& token = tokens[ev.tokenId];

        if (token.resourceClass == static_cast<uint8_t>(ResourceClass::Fvb)) {
            releaseToken(token);
            continue;
        }

        if (!hasEligiblePendingRetry(token.resourceClass)) {
            releaseToken(token);
            continue;
        }

        token.state = TokenState::RetireHeldForPCrdGrant;
        token.reqPriority = ev.reqPriority;
    }
}
```

### 11.2 eligible rule

```cpp
bool
HomeLinkLayer::pendingPrioCanUseResource(uint8_t pendingPrio, uint8_t resourceClass) const
{
    if (resourceClass == static_cast<uint8_t>(ResourceClass::Fvb)) {
        return false;
    }
    return pendingPrio >= resourceClass;
}
```

### 11.3 二维仲裁

```cpp
struct PendingWinner
{
    bool valid = false;
    uint8_t priority = 0;
    uint32_t srcid = 0;
    uint8_t pcrdtype = 0;
};
```

优先级维度用 weighted RR，SrcID 维度用 RR/find-first。只更新真正 commit 的 row：

```cpp
PendingWinner
HomeLinkLayer::selectPendingRetryForResource(uint8_t resourceClass)
{
    std::array<bool, 4> prioValid = {false, false, false, false};
    std::array<PendingWinner, 4> rowWinner;

    for (uint8_t p = 0; p < 4; ++p) {
        if (!pendingPrioCanUseResource(p, resourceClass)) {
            continue;
        }
        rowWinner[p] = selectSrcInPriorityRow(p);
        prioValid[p] = rowWinner[p].valid;
    }

    auto p = retryPrioWrr.select(prioValid);
    if (!p.has_value()) {
        return {};
    }
    return rowWinner[*p];
}
```

### 11.4 doPcrdGrantWakeup

```cpp
void
HomeLinkLayer::doPcrdGrantWakeup()
{
    for (auto& token : tokens) {
        if (token.state != TokenState::RetireHeldForPCrdGrant) {
            continue;
        }

        if (pcrdGrantFifo.size() >= pcrdGrantFifoDepth) {
            stats.pcrdGrantFifoFull++;
            continue;
        }

        PendingWinner w = selectPendingRetryForResource(token.resourceClass);
        if (!w.valid) {
            releaseToken(token);
            continue;
        }

        token.state = TokenState::StaticReserved;
        token.staticOwnerSrcid = w.srcid;
        token.staticPcrdtype = w.pcrdtype;
        token.staticPriority = w.priority;

        RawRsp rsp;
        rsp.rspKind = RspKind::PCrdGrant;
        rsp.tgtid = w.srcid;
        rsp.pcrdtype = w.pcrdtype;
        rsp.originCycle = llCycle;

        pcrdGrantFifo.push_back(rsp);

        pendingRetry.decrement(w.priority, w.srcid, w.pcrdtype);
        retryPrioWrr.updateOnCommit(w.priority);
        updateSrcRrOnCommit(w.priority, w.srcid);
    }
}
```

因为 `doTxRspArb()` 已经执行过，本周期新生成的 PCrdGrant 最早下一周期发送。

---

## 12. TXRSP 固定优先级仲裁

TXRSP 输入源：

```cpp
std::deque<RawRsp> shortPathFifo;
std::deque<RawRsp> retryAckFifo;
std::deque<RawRsp> pcrdGrantFifo;
std::deque<RawRsp> mainPathFifo;
```

优先级：

```text
ShortPath > RetryAck > PCrdGrant > MainPath
```

实现：

```cpp
void
HomeLinkLayer::doTxRspArb()
{
    if (!linkTxRun || !slcInitDone) {
        return;
    }

    if (!rxport->hasTxCredit(ChannelType::RSP)) {
        stats.txrspCreditStall++;
        return;
    }

    RawRsp* winner = nullptr;
    std::deque<RawRsp>* winnerQ = nullptr;

    if (!shortPathFifo.empty()) {
        winner = &shortPathFifo.front();
        winnerQ = &shortPathFifo;
    } else if (!retryAckFifo.empty()) {
        winner = &retryAckFifo.front();
        winnerQ = &retryAckFifo;
    } else if (!pcrdGrantFifo.empty()) {
        winner = &pcrdGrantFifo.front();
        winnerQ = &pcrdGrantFifo;
    } else if (!mainPathFifo.empty()) {
        winner = &mainPathFifo.front();
        winnerQ = &mainPathFifo;
    }

    if (!winner) {
        return;
    }

    FlitVariant fv = *winner;
    bool ok = rxport->enqueueTxNoWakeupSameCycle(ChannelType::RSP, fv);
    if (!ok) {
        stats.txrspPortBlocked++;
        return;
    }

    if (winner->rspKind == RspKind::RetryAck) {
        scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);
    }

    winnerQ->pop_front();
}
```

`enqueueTxNoWakeupSameCycle()` 发送到对端 port 后，也只能 schedule 对端下一拍 wakeup，不能直接调用对端 `wakeup()`。

---

## 13. RXREQ credit return

### 13.1 Credit event

```cpp
struct CreditEvent
{
    ChannelType ch;
    uint8_t amount = 1;
    uint64_t dueCycle = 0;
};

std::deque<CreditEvent> creditEvents;
```

### 13.2 生成事件

Dynamic/static 成功：

```cpp
void
HomeLinkLayer::processCcAdmitResult()
{
    const auto& res = ccIn.admit;
    if (!res.valid) {
        return;
    }

    auto& token = tokens[res.tokenId];

    if (!res.accepted) {
        releaseToken(token);
        stats.ccReject++;
        return;
    }

    if (token.state == TokenState::AllocPendingCcAck) {
        if (tokenWasDynamic(token)) {
            token.state = TokenState::WorkingDynamic;
            scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);
        } else if (tokenWasStatic(token)) {
            token.state = TokenState::WorkingStatic;
            scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);
        } else if (tokenWasFvb(token)) {
            token.state = TokenState::WorkingFvb;
        }
    }
}
```

Retry 成功：

```cpp
// In doTxRspArb(), only after real TX success:
if (winner->rspKind == RspKind::RetryAck) {
    scheduleRxCreditReturn(ChannelType::REQ, 1, llCycle + 1);
}
```

### 13.3 执行事件

```cpp
void
HomeLinkLayer::doCreditEvents()
{
    while (!creditEvents.empty() && creditEvents.front().dueCycle <= llCycle) {
        auto ev = creditEvents.front();
        creditEvents.pop_front();
        rxport->returnRxCredit(ev.ch, ev.amount);
    }
}
```

禁止在以下时机返还 RXREQ credit：

```text
H1 判定 retry 时
retryDecisionQ push 时
retryAckFifo push 时
RetryAck 被选中但 TX credit 不足时
rxport dequeue RXREQ 时
```

---

## 14. Short Path MVP

MVP 默认关闭真实 ShortPath：

```cpp
bool enableTxRspShortPath = false;
```

如果以后打开：

```text
RXREQ H1 admission 成功 -> LinkToCcReq.shortPathCandidate = true
CC admit result 返回 accepted/hazard/shortPathAllowed
下一拍 push shortPathFifo
再下一拍才可能 TXRSP arb 发送
```

禁止 H1 同拍生成 ShortPath 并进入 TXRSP arb。

---

## 15. FVB MVP

FVB 使用独立 resource pool，不参与普通 QoS pending retry。

```cpp
struct FvbState
{
    bool pending = false;
    FlitVariant flit;
    uint64_t lastEnqueueCycle = 0;
};
```

规则：

```text
1. FVB resource 有空
2. 三个周期内没有 FVB 入队
3. 等待 RXREQ 间隙
```

wakeup 中处理顺序：RXREQ 优先，FVB 在 `sampleRxPortsToH0()` 或单独 `doFvbWakeup()` 中只在本周期没有 RXREQ dispatch 时尝试入队。

发生 RN RXREQ 与 FVB 同拍冲突时：

```text
RN RXREQ 优先入队。
RN RXREQ credit return 需要等冲突的 FVB 成功入队后再释放。
```

---

## 16. hasPendingWork

`HomeLinkLayer::wakeup()` 末尾需要判断是否继续 schedule。

```cpp
bool
HomeLinkLayer::hasPendingWork() const
{
    if (portHasRxFlit()) return true;
    if (pipelineHasWork()) return true;
    if (!retryDecisionQ.empty()) return true;
    if (!retryAckFifo.empty()) return true;
    if (!pcrdGrantFifo.empty()) return true;
    if (!mainPathFifo.empty()) return true;
    if (!shortPathFifo.empty()) return true;
    if (!creditEvents.empty()) return true;
    if (hasHeldRetireToken()) return true;
    if (ccIn.admit.valid || !ccIn.retires.empty()) return true;
    return false;
}
```

注意：stage 函数内部不要到处调用 `scheduleEvent(Cycles(1))`。统一在 wakeup 末尾根据 `hasPendingWork()` 调度下一拍。这样避免重复事件，也让时序更清楚。

---

## 17. 当前骨架文件的具体修改建议

### 17.1 `HomeLinkLayer.hh`

需要做的改动：

```text
1. 删除或重命名 pipline_queue，改为 rxPipe[NUM_CH][NumLlStages]。
2. 修改 MemFn 签名，传入 PipeEntry& 和具体 flit type，返回 StageResult。
3. 增加 Retry/QoS/TXRSP/credit/resource token 成员。
4. 增加 scheduleNextCycle()、hasPendingWork()。
5. 增加 setCcInput()/getLinkToCcOutput() 这类结构体接口。
```

示意：

```cpp
class HomeLinkLayer : public ruby::Consumer
{
  public:
    HomeLinkLayer(HomeNodeFull* hnf);
    void wakeup() override;
    void setRxPort(ChiCommonPort* port) { rxport = port; }

    void setCcInput(const CcToLinkBundle& in) { ccIn = in; }
    const std::deque<LinkToCcReq>& getLinkToCcQueue() const { return linkToCcQ; }

  private:
    HomeNodeFull* m_homenode = nullptr;
    ChiCommonPort* rxport = nullptr;

    uint64_t llCycle = 0;
    uint64_t nextSeq = 1;

    std::array<ChannelPipe, NumCh> rxPipe;

    std::deque<LinkToCcReq> linkToCcQ;
    CcToLinkBundle ccIn;

    std::vector<ResourceToken> tokens;
    PendingRetryTable pendingRetry;

    std::deque<RetryRecord> retryDecisionQ;
    std::deque<RawRsp> retryAckFifo;
    std::deque<RawRsp> pcrdGrantFifo;
    std::deque<RawRsp> shortPathFifo;
    std::deque<RawRsp> mainPathFifo;
    std::deque<CreditEvent> creditEvents;

    // wakeup subfunctions
    void doTxRspArb();
    void doCreditEvents();
    void doCcResultAndRetire();
    void doRetryWakeup();
    void doPcrdGrantWakeup();
    void doRxPipelineWakeup();
    void sampleRxPortsToH0();

    bool hasPendingWork() const;
    void scheduleNextCycle();
};
```

### 17.2 `HomeLinkLayer.cc`

`wakeup()` 不再直接遍历 port 并 `advancePipeline(rxflit)`。

删除当前模式：

```cpp
for (ChannelType ch : {...}) {
    auto flit = rxport->getRxFlit(ch);
    if (flit.has_value()) {
        FlitVariant rxflit = flit.value();
        advancePipeline(rxflit);
    }
}
```

替换成第 2.2 节的固定 wakeup 顺序。

### 17.3 `ChiCommonPort.hh`

需要做的改动：

```text
1. enqueueFlit 不直接 wakeup，改为 scheduleEvent(Cycles(1))。
2. 增加 getRxFlitNoCredit。
3. 增加 returnRxCredit。
4. 增加 hasTxCredit / consumeTxCredit 或 enqueueTxNoWakeupSameCycle。
5. 不要在 RXREQ dequeue 时自动 credit++。
```

### 17.4 `ChiChannel.hh`

需要做的改动：

```text
1. BaseFlit.stage 初始化为 0。
2. RawReq 增加 order/pcrdtype/traceTag 等字段。
3. RawRsp 增加 rspKind/pcrdtype/originSeq/originCycle。
4. data_payload 建议改用 std::vector<uint8_t> 或 std::array<uint8_t, 64>，不要裸指针长期跨周期保存。
```

当前 `RawDat::data_payload` 使用裸 `uint8_t*`，如果 flit 要进入 pipeline 并跨周期保存，裸指针生命周期很危险。MVP 可先不处理 DAT，但后续必须改。

---

## 18. Assertions

### 18.1 No zero delay

```cpp
assert(retryAckTxCycle >= retryDetectedCycle + 2);
assert(pcrdGrantTxCycle >= retireCycle + 2);
assert(rxReqCreditReturnCycle >= retryAckTxCycle + 1);
```

### 18.2 Pending retry

```cpp
assert(pendingRetry.count(prio, srcid, pcrdtype) >= 0);
assert(pendingRetry.totalForSrc(srcid) <= 256);
```

### 18.3 PCrdGrant reservation

```cpp
assert(pcrdGrant implies exists token.state == TokenState::StaticReserved);
assert(token.staticOwnerSrcid == pcrdGrant.tgtid);
assert(token.staticPcrdtype == pcrdGrant.pcrdtype);
```

### 18.4 Credit

```cpp
assert(!creditReturnedOnRxPortDequeueForReq);
assert(!creditReturnedOnRetryDecision);
assert(!creditReturnedOnRetryAckFifoPush);
assert(creditReturnedOnlyAfterRetryAckTxSuccess);
```

### 18.5 Stage movement

```cpp
assert(newlySampledFlitNotProcessedInSameWakeup);
assert(stageAdvancedEntryNotProcessedAgainInSameWakeup);
```

---

## 19. Unit tests for Codex

### Test 1: port enqueue no direct wakeup

输入：对端调用 `enqueueRx(REQ, flit)`。
期望：当前调用栈内不执行 `HomeLinkLayer::wakeup()`；只 schedule 下一 cycle。

### Test 2: sampled flit not processed same wakeup

输入：`rxport` 已有一个 RXREQ。
期望：本次 wakeup 末尾进入 `rxPipe[REQ][H0]`，本次 wakeup 不执行 `doStageH0_Req`。

### Test 3: H0 to H1 not same wakeup

输入：`rxPipe[REQ][H0]` 有一个 flit。
期望：本次 wakeup 执行 H0 并移动到 H1；本次 wakeup 不执行 H1。

### Test 4: RetryAck earliest cycle

输入：H1 判定 retry。
期望：

```text
cycle N:   H1 生成 retryDecisionQ
cycle N+1: retryAckFifo push + pendingRetry++
cycle N+2: TXRSP 最早发送 RetryAck
cycle N+3: RXREQ credit 最早 return
```

### Test 5: Pending retry counter

输入：同一个 `srcid=7, pcrdtype=1, qos=8` retry 三次。
期望：counter 为 3，三次 retire 后生成三个 PCrdGrant。

### Test 6: PCrdGrant FIFO full

输入：retire token，pending retry 存在，但 pcrdGrantFifo 满。
期望：token 维持 `RetireHeldForPCrdGrant`，pending counter 不变。

### Test 7: Static request consumes reservation

输入：PCrdGrant 已为 `srcid=7,pcrdtype=1` 建立 `StaticReserved` token，然后收到 `AllowRetry=0` 请求。
期望：该 token 进入 `AllocPendingCcAck/WorkingStatic`，不能再被其他请求使用。

### Test 8: TXRSP fixed priority

同时存在 ShortPath、RetryAck、PCrdGrant、MainPath，且 TXRSP credit=1。
期望：发送 ShortPath。关闭 ShortPath 时发送 RetryAck。

### Test 9: RXREQ credit not returned by getRxFlit

输入：`rxport->getRxFlitNoCredit(REQ)`。
期望：REQ credit 不变。直到 allocation success 或 RetryAck TX success 才增加。

### Test 10: TX credit no same-cycle use

输入：cycle N 收到下游 TXRSP CreditV，同时 retryAckFifo 非空。
期望：cycle N 不发送；cycle N+1 才能用该 credit。

---

## 20. Codex 实现提示词

可直接把下面这段给 Codex：

```text
Modify the existing HomeLinkLayer skeleton instead of replacing it with a standalone tick class.
Keep HomeNodeFull::wakeup() calling HomeLinkLayer::wakeup(). Keep the per-flit-type stage function tables, but store flits in persistent per-channel per-stage queues. A flit sampled from ChiCommonPort must be pushed into H0 at the end of wakeup and must not execute H0 in the same wakeup. Process stages from high stage to low stage so an entry advanced from H0 to H1 is not processed again in the same wakeup. Do not call wakeup() directly from ChiCommonPort::enqueueFlit; schedule the consumer for Cycles(1). Do not return RXREQ credit when dequeuing from the RX port. Return RXREQ credit only after CC admission success for dynamic/static requests or after real TXRSP send success for RetryAck. Retry decision pushes RetryRecord first; RetryAck FIFO and pending retry counter update in the next wakeup; TXRSP can send it no earlier than one further wakeup. Pending retry must be a counter keyed by priority, srcid, pcrdtype, not a bit. PCrdGrant must reserve a concrete ResourceToken in StaticReserved state before the PCrdGrant flit is queued. TXRSP fixed priority is ShortPath > RetryAck > PCrdGrant > MainPath. CC interface is only struct-based; LinkLayer must not create TxnContext.
```

---

## 21. MVP 边界

MVP 必须做：

```text
1. wakeup-based persistent pipeline
2. no direct wakeup from port enqueue
3. no RXREQ credit return on port dequeue
4. RetryDecision -> RetryAck FIFO -> TXRSP -> credit 的多拍链路
5. PendingRetry counter[priority][srcid][pcrdtype]
6. ResourceToken + StaticReserved
7. PCrdGrant FIFO 与 static reservation 绑定
8. TXRSP fixed priority
9. CC struct interface
10. basic assertions and unit tests
```

MVP 可以暂时关闭：

```text
1. TXRSP ShortPath 真实发送
2. TXDAT Short/Skip Path
3. TXSNP Skip Path
4. TXREQ Skip Path
5. 完整 DAT beat ordering
6. 完整 Comp/CompAck/RespSepData ordering
7. DCT/DMT/SLCREQ skip path
```

---

## 22. 最后检查清单

提交代码前检查：

```text
[ ] HomeLinkLayer::wakeup() 是唯一 LinkLayer 周期入口。
[ ] ChiCommonPort 不再直接调用 consumer->wakeup()。
[ ] rxport sample 的新 flit 不会同 wakeup 跑 H0。
[ ] H0->H1 的 flit 不会同 wakeup 跑 H1。
[ ] RetryDecision push 与 RetryAck FIFO push 不在同一个 wakeup。
[ ] RetryAck FIFO push 与 TXRSP send 不在同一个 wakeup。
[ ] RetryAck TX success 与 RXREQ credit return 至少隔一拍。
[ ] PendingRetry 是 counter，不是 bit。
[ ] PCrdGrant 发出前存在 StaticReserved token。
[ ] AllowRetry=0 必须匹配 StaticReserved token。
[ ] TXRSP priority 是 ShortPath > RetryAck > PCrdGrant > MainPath。
[ ] CC 接口只有 struct，不创建 TxnContext。
```

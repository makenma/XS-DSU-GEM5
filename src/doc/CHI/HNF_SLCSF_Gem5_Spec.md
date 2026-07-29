# 1. 总体设计目标

## 1.1 推荐结论

**`SlcSnoopFilter` 在逻辑上是 `HomeNodeFull` 的内部子模块，在 gem5 实现上建议做成独立的 child `ClockedObject`。**

内部的 `SlcArray`、`SfDirectory`、`VictimBuffer`、`SeqBuffer`、`SlcSfService` 都做成普通 C++ object，由 `SlcSnoopFilter` 统一持有、调度、统计和序列化。

```text
HomeNodeFull
├── LinkLayer
├── CoherenceController
│   ├── PoCQueue
│   ├── TransactionGraph
│   ├── DataBuffer
│   └── AddressBuffer
└── SlcSnoopFilter                 // child ClockedObject
    ├── SlcArray                   // ordinary C++ object
    ├── SfDirectory                // ordinary C++ object
    ├── VictimBuffer               // ordinary C++ object
    ├── SeqBuffer                  // ordinary C++ object
    ├── SlcSfService               // ordinary C++ object
    ├── SlcSfReqQueue
    └── SlcSfRespQueue
```

这与当前 HNF 的职责划分是一致的：事务流程、SLC/SF 查询结果、snoop 信息和 FVB 信息由 CC entry/事务流消费；SLC hit/victim 数据进入 CC Data Buffer，后续 Fill 数据再由 CC 送回 SLCSF。也就是说，真正的事务边界应是 **PoCQ/CC ↔ SLCSF**，而不是 LinkLayer ↔ SLCSF。

`ClockedObject` 本身就是带时钟域的 `SimObject`，提供 `clockEdge()`、`nextCycle()` 等周期换算接口；而 `SimObject` 天然属于 gem5 的配置、事件、统计、drain 和 checkpoint 体系。因此一个拥有独立队列、服务延迟、初始化状态和内部资源的 SLCSF，作为 child `ClockedObject` 比嵌入 CC 的普通成员更合适。([GitHub][1])

## 1.2 为什么不建议整个 SLCSF 只是 CC 内部普通对象

两种方案的区别如下。

| 设计                                       | 优点                                      | 问题                                                  | 结论        |
| ---------------------------------------- | --------------------------------------- | --------------------------------------------------- | --------- |
| `SlcSnoopFilter` 是 CC 内部普通对象             | 代码少，直接调用方便                              | 时钟、事件、统计、初始化、drain、checkpoint 都依赖 CC；后续很难独立调参或改变并行度 | 不推荐作为最终结构 |
| `SlcSnoopFilter : ClockedObject`         | 独立参数、队列、事件、统计、checkpoint；边界清晰；易于单测和替换实现 | 多一层 Python/SConscript 配置                            | 推荐        |
| 每个 `SlcArray`、`SfDirectory` 都做 SimObject | 每个组件可独立配置                               | SimObject 数量过多，内部调用和序列化复杂，没有实际收益                    | 不推荐       |

推荐原则是：

> **只有拥有独立时间推进和外部配置边界的 `SlcSnoopFilter` 是 SimObject；内部算法和存储组件都是普通 C++ 类。**

默认让 `CoherenceController` 和 `SlcSnoopFilter` 使用同一个 `clk_domain`，但各自有独立 wakeup event。以后需要单独调节 SLC/SF 时钟时，不需要重构接口。

## 1.3 与 CoherenceController / PoCQueue 的关系

### PoCQ/CC 负责

* 持有 transaction graph。
* 持有 CHI request 原始信息。
* 发起 `Lookup`、`Fill`、`Update`、`Evict`。
* 消费 lookup 结果并选择图分支。
* 根据 `SlcSfSnoopInfo` 生成 TXSNP。
* 根据 `McAction` 生成 MC TXREQ。
* 汇总 snoop、MC 和 requester data。
* 把最终完整 cache line 通过 Fill/Update 送回 SLCSF。
* 接管 dirty victim，并在后续 graph state 写回 SN。
* 接管 SF victim/FVB，并完成反向失效事务。
* replay 后清除旧 token，重新发起 Lookup。

### SLCSF 负责

* SLC/SF storage 的唯一所有权。
* hit/miss、state、directory target 查询。
* replacement、dirty victim、SF victim。
* same-set/way、VictimBuffer、SEQ 冲突。
* lookup snapshot 和 commit token。
* 参数化服务延迟。
* replay、busy 和 backpressure。
* init 状态。
* 对 PoCQ 可见的完成事件。

### SLCSF 不负责

* 不直接生成 CHI flit。
* 不直接发 TXSNP、TXREQ、TXDAT、TXRSP。
* 不直接接收 RXRSP/RXDAT。
* 不维护完整 CHI transaction lifetime。
* 不负责 CompAck、DBID、TxnID 等协议流程。
* 不直接调用 Memory Controller。

原有设计中，snoop target、MC 请求指示、no-fill、replay 等信息最终都是交给 PoC/CC 消费；replay 要求取消当前操作并由 PoC 重新提起请求。粗粒度实现应保留这一职责关系，而不是把 PoCQ transaction graph 搬进 SLCSF。

## 1.4 与 LinkLayer 的关系

**正常事务数据路径上，SLCSF 不直接与 LinkLayer 交互。**

推荐路径：

```text
SLCSF LookupComplete
    ↓
PoCQ 更新 entry
    ↓
TransactionGraph 选择 Snoop / MC / Response 动作
    ↓
CC TX arbiter
    ↓
LinkLayer
```

例如：

```text
SLCSF directed snoop decision
    → PoCQ records target
    → CC builds TXSNP
    → LinkLayer sends TXSNP
```

```text
SLCSF dirty victim
    → PoCQ writes victim data into CC DataBuffer
    → PoCQ creates SN writeback graph
    → CC builds TXREQ/TXDAT
    → LinkLayer sends writeback
```

唯一可以向 HNF 顶层直接暴露的是：

```cpp
bool initialized() const;
```

`HomeNodeFull` 可以用该状态控制 LinkLayer 是否开放 RX credit/TX traffic，但这只是**顶层控制状态**，不是 SLCSF 与 LinkLayer 的事务接口。HNF 文档也把最终 TX 报文组织和 credit 反压放在 CC/LinkLayer，而不是 SLC storage 中。

## 1.5 同步调用与延迟接口

### 允许同步调用的接口

同步调用只能用于“传递消息或读取已经寄存的状态”，不能同步返回 cache 语义结果。

```cpp
SlcSfEnqueueResult tryEnqueue(const SlcSfReq &req);
unsigned registeredReqCredits() const;

bool responseAvailable() const;
const SlcSfResp &frontResponse() const;
void popResponse();

bool initialized() const;
```

其中：

* `tryEnqueue()` 只返回 `Accepted` 或 `NoCredit`。
* 它不能返回 hit/miss。
* 它不能在内部立即执行 lookup。
* `registeredReqCredits()` 返回上一周期寄存的 credit snapshot。
* `frontResponse()` 读取的是此前已经延迟完成并进入 visible response queue 的结果。

内部普通对象可以同步调用：

```cpp
slc.probe(line);
sf.probe(line);
policy.planLookup(...);
slc.prepareFill(...);
sf.prepareUpdate(...);
```

这些调用发生在 SLCSF 自己的 wakeup 中，不跨 SimObject 时间边界。

### 必须延迟返回的接口

以下所有事件必须经 response queue：

```text
LookupComplete
FillComplete
UpdateComplete
EvictComplete
VictimReady
SfEvict/FvbReady
Replay
InitDone
Error
```

即：

```cpp
tryEnqueue(req) == Accepted
```

只表示 SLCSF 已获得 request 的所有权，不表示操作完成。

## 1.6 避免 0-cycle combinational loop

推荐实现四条硬规则。

### 规则一：request 使用 current/next 双缓冲

```cpp
BoundedQueue<SlcSfReq> reqIngress; // 本周期外部写入
BoundedQueue<SlcSfReq> reqReady;   // 本周期 service 可读取
```

本周期 `tryEnqueue()` 只能写 `reqIngress`。在下一个 SLCSF wakeup 开始时才执行：

```cpp
reqReady.splice(reqIngress);
```

因此：

```text
PoCQ enqueue at cycle N
SLCSF earliest issue at cycle N+1
```

### 规则二：response 也使用 pending/visible 双缓冲

```cpp
BoundedQueue<SlcSfResp> respPending;
BoundedQueue<SlcSfResp> respVisible;
```

本周期完成的操作只写 `respPending`。下一个 SLCSF cycle 才变成 `respVisible`。

### 规则三：所有 latency 至少为 1

```cpp
Cycles
effectiveLatency(Cycles configured)
{
    return std::max(configured, Cycles(1));
}
```

不要使用：

```cpp
clockEdge(Cycles(0))
```

来调度任何对 PoCQ 可见的事件。应使用：

```cpp
clockEdge(effectiveLatency(latency))
```

或者：

```cpp
nextCycle()
```

`nextCycle()` 明确返回至少一个本地周期之后的时刻。([GitHub][1])

### 规则四：SLCSF 不在 callback 中直接推进 PoCQ

禁止：

```cpp
void SlcSnoopFilter::complete()
{
    cc->processSlcSfResp(resp);     // 禁止
    // processSlcSfResp 又立即 enqueue Fill
}
```

允许：

```cpp
void SlcSnoopFilter::complete()
{
    respPending.push(resp);
}
```

PoCQ 在自己的后续 wakeup 中读取 response。

如果 CC 可能完全休眠，可以提供单向通知接口，但通知只能调度未来事件：

```cpp
class SlcSfWakeupSink
{
  public:
    virtual void scheduleSlcSfWakeup(Tick when) = 0;
};
```

调用时只能：

```cpp
sink->scheduleSlcSfWakeup(ccNextCycle);
```

不能在通知函数中消费 response。

### 推荐可观察时序

```text
Cycle N:
    PoCQ graph state = IssueLookup
    tryEnqueue(Lookup) -> Accepted

Cycle N+1:
    SLCSF promotes request
    service issues Lookup

Cycle N+1+lookupLatency:
    lookup completes internally
    result enters respPending

Next SLCSF cycle:
    response becomes respVisible

Next PoCQ cycle:
    PoCQ consumes response
    graph transitions to IssueSnoop / IssueMc / Complete
```

这样即使 CC 和 SLCSF 共用一个 event queue，也不会依赖同 tick 的事件执行顺序。

## 1.7 当前实现状态（2026-07）

目标架构已落地为 `HomeNodeFull.slcsf` child
`SlcSnoopFilter : ClockedObject`。child 按值拥有普通、不可复制的
`HnfSLCSF` service，而 HomeNode 和 CC 只保留配置所有权或非拥有指针。
child 的独立 `EventFunctionWrapper` 是 service 的唯一推进源；默认继承
HomeNode 时钟域，也可显式配置不同 child `clk_domain`。

当前默认值如下：

| 类别 | 默认值 |
| --- | --- |
| geometry | block size 继承 `cache_line_size`；SLC `1024 x 16`；SF `1024 x 16`；SEQ `8` |
| latency | init `16`；lookup `4`；fill `4`；update `3`；dirty victim `3`；SF evict `2`；replay `2` child cycles |
| capacity | request queue `8`；response queue `8`；VictimBuffer `2`；max in-flight `1` |
| width | lookup/fill/update issue width 均为 `1`；CC response-consume width `1` |
| concurrency | set lock 默认关闭；`max_inflight > 1` 时必须显式开启 |

`HomeNodeFull.py` 保留了原有 geometry 和 `slcsf_*` 参数路径作为
兼容表面。`SlcSnoopFilter.py` 上的同名 canonical child 参数通过
`Parent` proxy 把这些 parent 值作为默认值；因此旧的
`system.home_node[...].sf_num_sets/sf_num_ways/seq_entries` 等 override 继续有效。
如果用户显式构造 `SlcSnoopFilter(...)` 并设置 child 参数，正常
SimObject 解析规则使 child override 优先于 parent 默认值；C++ 只读取
child 中解析后的最终值，不实现第二套优先级。

当前 checkpoint 支持边界仍是 **drained checkpoint only**。HomeNode 必须先
停止新 RXREQ，等所有已分配事务不再产生 child intent，再 seal child
admission；只有 parent protocol/deferred-retire 和 child queue/response/in-flight/
lock/VictimBuffer/SEQ 均空闲时才能序列化。恢复会保持 SLC/SF/
SEQ 持久状态、replacement/generation 和所有 monotonic next IDs，不会再执行
cold `initState()`。活动队列、半完成响应、deferred retire 或未释放 owner
的 checkpoint 会 fail fast，目前不支持。

---

# 2. 建议的文件组织

推荐不要继续沿用 RTL 子模块命名拆分。粗粒度模型按“类型、存储、功能规则、时间服务、gem5 外壳”拆分。

```text
src/mem/chi/hnf/
├── SConscript
├── SlcSnoopFilter.py
│
├── slcsf_types.hh
│
├── slc_snoop_filter.hh
├── slc_snoop_filter.cc
│
├── slcsf_service.hh
├── slcsf_service.cc
│
├── slc_array.hh
├── slc_array.cc
│
├── sf_directory.hh
├── sf_directory.cc
│
├── slcsf_policy.hh
├── slcsf_policy.cc
│
├── slcsf_buffers.hh
├── slcsf_buffers.cc
│
├── slcsf_queue.hh
│
├── slcsf_stats.hh
├── slcsf_stats.cc
│
└── tests/
    ├── slc_array.test.cc
    ├── sf_directory.test.cc
    ├── slcsf_policy.test.cc
    ├── slcsf_service.test.cc
    └── slc_snoop_filter.test.cc
```

## 2.1 文件职责

| 文件                   | 职责                                                               |
| -------------------- | ---------------------------------------------------------------- |
| `slcsf_types.hh`     | 所有 enum、request/response、token、lookup snapshot、victim、snoop info |
| `slc_array.*`        | SLC tag/data storage、probe、fill、update、invalidate、victim policy  |
| `sf_directory.*`     | SF storage、rnfid/rnfvec、directory update、snoop target、SF victim  |
| `slcsf_policy.*`     | 纯函数形式的 transaction/state transition 规则                           |
| `slcsf_buffers.*`    | `VictimBuffer` 和 `SeqBuffer`                                     |
| `slcsf_queue.hh`     | bounded queue、current/next queue、response slot reservation       |
| `slcsf_service.*`    | issue、inflight、latency、reservation、complete、replay               |
| `slc_snoop_filter.*` | `ClockedObject` 外壳、wakeup、external API、drain、checkpoint          |
| `SlcSnoopFilter.py`  | gem5 参数                                                          |
| `slcsf_stats.*`      | 统计组；MVP 也可以先放进顶层类                                                |
| `tests/`             | 无需启动完整 CHI 系统的组件级单测                                              |

不建议使用过于通用的：

```text
snoop_filter.hh
```

gem5 其他内存子系统也可能存在同名概念。推荐使用：

```text
sf_directory.hh
class SfDirectory;
```

或者：

```text
hnf_snoop_filter.hh
class HnfSnoopFilter;
```

顶层仍叫：

```cpp
class SlcSnoopFilter;
```

## 2.2 MVP 可先合并的文件

第一版可以收敛为：

```text
slcsf_types.hh
slc_array.hh/.cc
sf_directory.hh/.cc
slcsf_service.hh/.cc
slc_snoop_filter.hh/.cc
SlcSnoopFilter.py
SConscript
```

此时：

* `VictimBuffer` 和 `SeqBuffer` 暂时放在 `slcsf_service.hh/.cc`。
* `SlcSfPolicy` 暂时放在 `sf_directory.cc` 或独立 `slcsf_policy.cc`。
* queue 使用 header-only 模板。
* stats 暂时作为 `SlcSnoopFilter::Stats` 内部类。

代码稳定以后再拆文件。

---

# 3. C++ 类依赖与所有权

```text
SlcSnoopFilter
│
├── SlcSfReqQueue
├── SlcSfRespQueue
├── SlcArray
├── SfDirectory
├── VictimBuffer
├── SeqBuffer
├── SlcSfPolicy
├── SlcSfService
└── Stats
```

推荐全部按值持有：

```cpp
class SlcSnoopFilter : public ClockedObject
{
  private:
    SlcSfReqQueue reqIngress;
    SlcSfReqQueue reqReady;

    SlcSfRespQueue respPending;
    SlcSfRespQueue respVisible;

    SlcArray slc;
    SfDirectory sf;

    VictimBuffer victimBuffer;
    SeqBuffer seqBuffer;

    SlcSfPolicy policy;
    SlcSfService service;

    SlcSfStats stats;
};
```

`SlcSfService` 保存对其他组件的引用：

```cpp
class SlcSfService
{
  private:
    SlcArray &slc;
    SfDirectory &sf;
    VictimBuffer &victimBuffer;
    SeqBuffer &seqBuffer;
    const SlcSfPolicy &policy;
};
```

因此构造顺序应为：

```cpp
SlcSnoopFilter::SlcSnoopFilter(const Params &p)
  : ClockedObject(p),
    reqIngress(p.reqQueueEntries),
    reqReady(p.reqQueueEntries),
    respPending(p.respQueueEntries),
    respVisible(p.respQueueEntries),
    slc(...),
    sf(...),
    victimBuffer(...),
    seqBuffer(...),
    policy(...),
    service(slc, sf, victimBuffer, seqBuffer, policy, ...),
    stats(this),
    wakeupEvent(*this)
{
}
```

`service` 必须在它引用的对象之后声明和构造。

---

# 4. `slcsf_types.hh` 设计

## 4.1 基础类型

```cpp
namespace gem5::chi::hnf
{

using SlcSfReqId = uint64_t;
using PocEntryId = uint16_t;
using RnfId = uint16_t;
using SetIndex = uint32_t;
using WayIndex = uint16_t;
using Generation = uint64_t;
using ReservationId = uint64_t;
using VictimId = uint64_t;
using SeqId = uint32_t;

using CacheLine = std::array<uint8_t, 64>;
using ByteMask = uint64_t;

// MVP 假定 RNF 数量不超过 64。
using RnfMask = uint64_t;

} // namespace gem5::chi::hnf
```

以后 RNF 超过 64 个时，可以把 `RnfMask` 换成自定义动态 bitset，而不改变 request/response 的语义。

## 4.2 Request 使用 common header + typed payload

推荐避免一个包含几十个仅部分有效字段的大结构体。

```cpp
struct SlcSfReqHeader
{
    SlcSfReqId reqId;
    PocEntryId pocEntryId;

    LineKey line;
    RnfId requester;

    ChiReqOpcode opcode;
    uint8_t qos;
    bool trace;
};
```

```cpp
struct LookupReq
{
    bool needData;
    bool allowSlcAllocation;
    bool noFill;
    bool likelyShared;
    bool fullLineOverwrite;
    bool allowSelfSnoop;
};
```

```cpp
struct FillReq
{
    CacheLine data;
    ByteMask validBytes;

    CommitFacts facts;
    CommitToken token;
};
```

```cpp
struct UpdateReq
{
    UpdateKind kind;
    CommitFacts facts;
    CommitToken token;

    std::optional<VictimId> victimId;
    std::optional<SeqId> seqId;
};
```

`UpdateKind` 建议包含：

```cpp
enum class UpdateKind : uint8_t
{
    UpdateLineState,
    InvalidateLine,
    AddSharer,
    RemoveSharer,
    MakeRequesterUnique,

    ReleaseDirtyVictim,
    CompleteSfEvict
};
```

这样 dirty victim 和 FVB 都有明确结束握手：

```text
dirty victim SN writeback done
    → Update{ReleaseDirtyVictim, victimId}

FVB snoop/data handling done
    → Update{CompleteSfEvict, seqId}
```

```cpp
struct EvictReq
{
    CommitToken token;
    EvictKind kind;
};
```

```cpp
struct InitReq
{
    bool clearSlc;
    bool clearSf;
};
```

最终：

```cpp
using SlcSfReqPayload =
    std::variant<LookupReq, FillReq, UpdateReq, EvictReq, InitReq>;

struct SlcSfReq
{
    SlcSfReqHeader header;
    SlcSfReqPayload payload;
};
```

### Replay 不建议作为公开 request operation

公开接口中建议只有：

```text
Lookup
Fill
Update
Evict
Init
```

Replay 是 response status：

```cpp
SlcSfStatus::Replay
```

PoCQ 收到 replay 后重新发送原始 `Lookup`。把 Replay 同时设计成 request 和 response，容易把“重试原因”和“重发动作”混在一起。

## 4.3 CommitToken

```cpp
struct EntryVersion
{
    bool hit;
    SetIndex set;
    WayIndex way;
    Generation generation;
};
```

```cpp
struct CommitToken
{
    SlcSfReqId lookupReqId;
    LineKey line;

    EntryVersion slcVersion;
    EntryVersion sfVersion;

    uint64_t lookupEpoch;
};
```

必须遵守：

* token 中不保存 `SlcTagEntry*`。
* 不保存 `SfTagEntry*`。
* 不保存 `std::vector` element 的引用。
* 不依赖数组地址长期不变。
* Fill/Update 时重新检查 tag、way、generation。
* generation 不匹配返回 `StaleLookupToken` replay。

Lookup 不建议在等待 snoop/MC 的几十个周期内长期锁住 set/way。长期 reservation 会把无关事务也阻塞。更合适的是：

```text
Lookup 返回 generation snapshot
外部动作执行期间不持有锁
Fill/Update 提交时校验 snapshot
失效则 replay
```

短期 reservation 只覆盖 SLCSF 内部已经 issue、尚未 complete 的操作。

---

# 5. `SlcSnoopFilter` 顶层接口

```cpp
class SlcSnoopFilter : public ClockedObject
{
  public:
    PARAMS(SlcSnoopFilter);

    explicit SlcSnoopFilter(const Params &p);

    enum class EnqueueResult : uint8_t
    {
        Accepted,
        NoCredit,
        Initializing,
        Draining
    };

    EnqueueResult tryEnqueue(const SlcSfReq &req);

    unsigned registeredReqCredits() const;

    bool responseAvailable() const;
    const SlcSfResp &frontResponse() const;
    void popResponse();

    bool initialized() const;

    void init() override;
    void initState() override;
    void startup() override;

    DrainState drain() override;
    void drainResume() override;

    void serialize(CheckpointOut &cp) const override;
    void unserialize(CheckpointIn &cp) override;

  private:
    void wakeup();
    void ensureWakeup();
    void scheduleNextWakeup();

    void promoteIngressRequests();
    void promotePendingResponses();
    void updateRegisteredCredits();

    bool hasWork() const;
    Tick calculateNextWakeup() const;

    MemberEventWrapper<&SlcSnoopFilter::wakeup> wakeupEvent;

    bool initDone = false;
    bool draining = false;
    bool restored = false;

    unsigned visibleReqCredits = 0;
    unsigned nextReqCredits = 0;

    // queues, arrays, buffers, service, stats...
};
```

当前 gem5 stable 中，`MemberEventWrapper` 被明确建议优先于 `EventFunctionWrapper`，主要原因是更好的类型安全和更低的包装开销。老分支没有该类型时，再退回：

```cpp
EventFunctionWrapper wakeupEvent;
```

构造方式：

```cpp
wakeupEvent([this] { wakeup(); }, name())
```

即可。([GitHub][2])

## 5.1 `tryEnqueue()` 的实现边界

```cpp
SlcSnoopFilter::EnqueueResult
SlcSnoopFilter::tryEnqueue(const SlcSfReq &req)
{
    if (draining)
        return EnqueueResult::Draining;

    if (!initDone)
        return EnqueueResult::Initializing;

    if (visibleReqCredits == 0 || reqIngress.full())
        return EnqueueResult::NoCredit;

    reqIngress.push(req);
    --visibleReqCredits;

    ensureWakeup();
    return EnqueueResult::Accepted;
}
```

这里不能调用：

```cpp
service.issue(req);
service.complete(req);
```

也不能产生 response。

## 5.2 response queue 消费

```cpp
bool
SlcSnoopFilter::responseAvailable() const
{
    return !respVisible.empty();
}
```

```cpp
const SlcSfResp &
SlcSnoopFilter::frontResponse() const
{
    panic_if(respVisible.empty(), "No SLCSF response");
    return respVisible.front();
}
```

```cpp
void
SlcSnoopFilter::popResponse()
{
    panic_if(respVisible.empty(), "No SLCSF response");
    respVisible.pop();
    ensureWakeup();
}
```

PoCQ 在一个 graph transition 中最多消费一个或参数化数量的 response。消费 response 后产生的新 Fill/Update 仍进入 `reqIngress`，不能在本次 SLCSF wakeup 中被处理。

---

# 6. `SlcSfService`

`SlcSfService` 不继承 `SimObject`，也不持有 Event。它只由顶层每个 wakeup 驱动。

```cpp
class SlcSfService
{
  public:
    struct Params
    {
        Cycles lookupLatency;
        Cycles fillLatency;
        Cycles updateLatency;
        Cycles victimLatency;
        Cycles sfEvictLatency;
        Cycles replayPenalty;

        unsigned lookupIssueWidth;
        unsigned fillIssueWidth;
        unsigned updateIssueWidth;
        unsigned maxInflight;
    };

    SlcSfService(
        SlcArray &slc,
        SfDirectory &sf,
        VictimBuffer &victimBuffer,
        SeqBuffer &seqBuffer,
        const SlcSfPolicy &policy,
        const Params &params);

    void completeReady(
        Tick now,
        SlcSfRespQueue &respPending);

    void issueReady(
        Tick now,
        SlcSfReqQueue &reqReady,
        SlcSfRespQueue &respPending);

    bool idle() const;
    Tick nextCompletionTick() const;

  private:
    bool canIssue(const SlcSfReq &req) const;
    Cycles latencyFor(const SlcSfReq &req) const;

    void issueOne(Tick now, SlcSfReq &&req);
    SlcSfResp completeOne(InFlightOp &op);

    SlcSfResp completeLookup(InFlightOp &op);
    SlcSfResp completeFill(InFlightOp &op);
    SlcSfResp completeUpdate(InFlightOp &op);
    SlcSfResp completeEvict(InFlightOp &op);
    SlcSfResp completeInit(InFlightOp &op);

    std::vector<InFlightOp> inflight;
};
```

## 6.1 `InFlightOp`

```cpp
struct InFlightOp
{
    SlcSfReq req;

    Tick acceptedTick;
    Tick issueTick;
    Tick readyTick;

    ServicePhase phase;

    std::optional<ReservationId> slcReservation;
    std::optional<ReservationId> sfReservation;
    std::optional<ReservationId> victimReservation;
    std::optional<ReservationId> seqReservation;

    bool responseSlotReserved;
};
```

不要在 `InFlightOp` 中存：

```cpp
SlcTagEntry *tagEntry;
SfTagEntry *sfEntry;
uint8_t *dataPointer;
PoCEntry *pocEntry;
```

全部使用 ID、set、way、generation 和值拷贝。

## 6.2 response slot 必须提前预留

一个 accepted request 必须最终生成一个 terminal response，因此 issue 前要保证 response queue 不会把完成操作永久堵住。

```cpp
if (!respPending.canReserveSlot())
    return false;

respPending.reserveSlot(req.header.reqId);
```

完成时：

```cpp
respPending.commitReservedSlot(reqId, std::move(resp));
```

replay 也消耗这个预留 slot。

## 6.3 complete 先于 issue

每次 wakeup 的 service 顺序固定为：

```cpp
service.completeReady(curTick(), respPending);
service.issueReady(curTick(), reqReady, respPending);
```

这样即使错误地把 latency 配成 0，新 issue 的 operation 也不会在同一个 `wakeup()` 中 complete。再配合 latency clamp，可以形成双保险。

---

# 7. Storage 和 policy 类

## 7.1 `SlcArray`

```cpp
class SlcArray
{
  public:
    SlcProbeResult probe(
        const LineKey &line,
        bool readData) const;

    SlcFillPlan prepareFill(
        const LineKey &line,
        SlcState newState,
        RnfId associatedRnf,
        const ResourceView &resources) const;

    CommitCheck validate(
        const CommitToken &token) const;

    CommitResult commitFill(
        const SlcFillPlan &plan,
        const CacheLine &data);

    CommitResult commitStateUpdate(
        const SlcUpdatePlan &plan);

    CommitResult invalidate(
        const SlcUpdatePlan &plan);

    SlcVictimSnapshot snapshotVictim(
        SetIndex set,
        WayIndex way) const;

    void reset();

    void serialize(CheckpointOut &cp) const;
    void unserialize(CheckpointIn &cp);

  private:
    std::vector<SlcSet> sets;
    std::unique_ptr<ReplacementPolicy> replacement;
};
```

`probe()` 必须是 const，不得悄悄改 coherence state。

replacement metadata 可以在 commit 或明确的 `recordAccess()` 中更新：

```cpp
void recordAccess(SetIndex set, WayIndex way, AccessKind kind);
```

## 7.2 `SfDirectory`

```cpp
class SfDirectory
{
  public:
    SfProbeResult probe(const LineKey &line) const;

    SlcSfSnoopInfo decideSnoop(
        const SlcSfReq &req,
        const SlcProbeResult &slc,
        const SfProbeResult &sf,
        const std::optional<SeqSnapshot> &seq) const;

    SfAllocatePlan prepareAllocate(
        const LineKey &line,
        const SfEntryValue &newEntry,
        const ResourceView &resources) const;

    CommitCheck validate(
        const CommitToken &token) const;

    CommitResult commitAllocate(
        const SfAllocatePlan &plan);

    CommitResult commitUpdate(
        const SfUpdatePlan &plan);

    CommitResult removeSharer(
        const SfUpdatePlan &plan,
        RnfId requester);

    CommitResult invalidate(
        const SfUpdatePlan &plan);

    void reset();

    void serialize(CheckpointOut &cp) const;
    void unserialize(CheckpointIn &cp);
};
```

## 7.3 `SlcSfPolicy`

transaction/state transition 规则不要散落在：

* `SlcSnoopFilter::wakeup()`
* queue 代码
* array 写函数
* PoCQ callback

推荐集中成纯函数：

```cpp
class SlcSfPolicy
{
  public:
    LookupPlan planLookup(
        const SlcSfReq &req,
        const SlcProbeResult &slc,
        const SfProbeResult &sf,
        const ResourceView &resources) const;

    CommitPlan planCommit(
        const SlcSfReq &req,
        const SlcProbeResult &currentSlc,
        const SfProbeResult &currentSf) const;
};
```

`LookupPlan` 只描述动作：

```cpp
struct LookupPlan
{
    McAction mcAction;

    bool fillRecommended;
    bool updateRequired;

    std::optional<SlcSfSnoopInfo> snoop;
    std::optional<SlcVictimCandidate> slcVictim;
    std::optional<SfVictimCandidate> sfVictim;

    bool selfCancel;
};
```

它不直接写 array。

这样 transaction 规则可以用普通 gtest 做矩阵测试，而不需要启动 event queue。

---

# 8. VictimBuffer 与 SeqBuffer

## 8.1 `VictimBuffer`

```cpp
class VictimBuffer
{
  public:
    bool full() const;
    bool contains(const LineKey &line) const;

    std::optional<ReservationId> reserve(
        SlcSfReqId owner);

    VictimId install(
        ReservationId reservation,
        const SlcVictimSnapshot &victim);

    const VictimEntry &get(VictimId id) const;

    void markHandedToPoc(VictimId id);
    void markWritebackIssued(VictimId id);
    void release(VictimId id);

    void serialize(CheckpointOut &cp) const;
    void unserialize(CheckpointIn &cp);
};
```

dirty victim fill 的原子顺序：

```text
1. validate commit token
2. reserve VictimBuffer
3. snapshot old tag/data
4. install snapshot into VictimBuffer
5. install new SLC line
6. generate FillComplete + SlcSfVictim
```

不能：

```text
先覆盖 old line
再尝试 reserve VictimBuffer
```

## 8.2 `SeqBuffer`

```cpp
class SeqBuffer
{
  public:
    bool full() const;

    std::optional<SeqId> find(
        const LineKey &line) const;

    std::optional<ReservationId> reserve(
        SlcSfReqId owner);

    SeqId install(
        ReservationId reservation,
        const SfVictimSnapshot &victim);

    const SeqEntry &get(SeqId id) const;

    void markFvbIssued(SeqId id);
    void markSnoopComplete(
        SeqId id,
        bool dirtyDataReceived);

    void release(SeqId id);
};
```

SF set 满时：

```text
reserve SEQ
→ copy old directory state
→ replace SF way
→ response carries FVB/SeqId
→ PoCQ performs reverse invalidation
→ PoCQ sends CompleteSfEvict(seqId)
→ release SEQ
```

SEQ full 或 slot race 产生 replay；这正是需要由 service 层建模的 correctness-visible 资源冲突。

---

# 9. `wakeup()` 主流程

推荐固定为：

```cpp
void
SlcSnoopFilter::wakeup()
{
    // 1. 暴露上一周期生成的响应。
    promotePendingResponses();

    // 2. 暴露上一周期接收的请求。
    promoteIngressRequests();

    // 3. 完成已经到时的操作。
    service.completeReady(curTick(), respPending);

    // 4. 从 ready queue 发起新操作。
    service.issueReady(curTick(), reqReady, respPending);

    // 5. 计算下一周期可见 credit。
    updateRegisteredCredits();

    // 6. drain 检查。
    if (draining && completelyIdle()) {
        signalDrainDone();
        return;
    }

    // 7. 事件驱动调度。
    scheduleNextWakeup();
}
```

## 9.1 不建议永久每周期 tick

SLCSF 空闲时不需要：

```cpp
schedule(wakeupEvent, nextCycle());
```

无限自唤醒。

`calculateNextWakeup()` 取以下最小值：

```text
nextCycle，若 reqIngress 非空
nextCycle，若 respPending 非空
最早 inflight readyTick
init complete tick
drain 需要继续推进的 tick
```

这样仍是 cycle-based service，但保持 gem5 的事件驱动特性。gem5 事件通常在 `startup()` 中首次调度，之后由 callback 根据需要继续调度。([gem5][3])

## 9.2 调度辅助函数

```cpp
void
SlcSnoopFilter::ensureWakeup()
{
    const Tick when = nextCycle();

    if (!wakeupEvent.scheduled()) {
        schedule(wakeupEvent, when);
    } else if (when < wakeupEvent.when()) {
        reschedule(wakeupEvent, when);
    }
}
```

```cpp
void
SlcSnoopFilter::scheduleNextWakeup()
{
    if (!hasWork())
        return;

    const Tick when = calculateNextWakeup();

    if (!wakeupEvent.scheduled())
        schedule(wakeupEvent, when);
    else
        reschedule(wakeupEvent, when);
}
```

---

# 10. Busy、stall 与 replay 的框架划分

## 10.1 Admission busy

以下情况在 `tryEnqueue()` 阶段拒绝：

```text
init 未完成
正在 drain
reqIngress 满
registered credit 为 0
```

返回：

```cpp
EnqueueResult::NoCredit
```

PoCQ 不改变 transaction graph ownership，只在后续周期重试发送。

## 10.2 Service stall

request 已经进入 `reqReady`，但暂时不能 issue：

```text
operation issue width 已用完
maxInflight 已满
response slot 不可预留
所需 service port 本周期不可用
```

这类情况留在 `reqReady`，不产生 replay。

## 10.3 Replay

request 已经 accepted，但继续处理会违反 snapshot/resource correctness：

```text
same set/way mutation conflict
VictimBuffer line conflict
VictimBuffer full race
SEQ full race
stale commit token
generation changed
reservation lost
recoverable error
```

产生：

```cpp
SlcSfResp {
    status = SlcSfStatus::Replay,
    replay = SlcSfReplay {
        .reason = ...,
        .retryAfter = replayPenalty,
        .redoLookup = true
    }
};
```

并保证：

```text
没有写 SLC tag
没有写 SLC data
没有写 SF tag
没有删除 sharer
没有覆盖 victim
没有递增 generation
```

---

# 11. PoCQ transaction graph 对接

建议 PoCQ 的 SLCSF 相关 graph node 至少有：

```text
IssueSlcSfLookup
WaitSlcSfLookup

IssueSnoop
WaitSnoop

IssueMcRead
WaitMcRead

IssueSlcSfFill
WaitSlcSfFill

IssueSlcSfUpdate
WaitSlcSfUpdate

WaitReplayPenalty
```

## 11.1 Issue node

```cpp
case GraphState::IssueSlcSfLookup:
{
    const auto req = buildLookupReq(entry);

    switch (slcsf->tryEnqueue(req)) {
      case Accepted:
        entry.slcsfReqId = req.header.reqId;
        transitionNextCycle(entry, WaitSlcSfLookup);
        break;

      case NoCredit:
      case Initializing:
        stay(entry);
        break;

      case Draining:
        stay(entry);
        break;
    }
    break;
}
```

## 11.2 Response 分发

```cpp
void
PoCQueue::consumeSlcSfResponses()
{
    unsigned count = 0;

    while (slcsf->responseAvailable() &&
           count < maxSlcSfResponsesPerCycle) {

        const auto resp = slcsf->frontResponse();
        auto &entry = entries.at(resp.pocEntryId);

        panic_if(entry.slcsfReqId != resp.reqId,
                 "Unexpected SLCSF response");

        latchResponse(entry, resp);
        slcsf->popResponse();

        scheduleGraphTransition(entry, nextCycle());
        ++count;
    }
}
```

不能在 `latchResponse()` 内立即发送下一个 SLCSF request。它只能设置 entry 中的 event bit，后续 graph transition 再发请求。

## 11.3 Replay

```text
收到 Replay
→ 清除 CommitToken
→ 清除本轮 lookup result
→ entry.replayReadyCycle = now + replayPenalty
→ WaitReplayPenalty
→ IssueSlcSfLookup
```

不能只重发原 Fill，因为 victim、way、SF vector 都可能已经变化。

---

# 12. Python SimObject 与 SConscript

## 12.1 `SlcSnoopFilter.py`

```python
from m5.params import *
from m5.objects.ClockedObject import ClockedObject


class SlcSnoopFilter(ClockedObject):
    type = "SlcSnoopFilter"
    cxx_class = "gem5::chi::hnf::SlcSnoopFilter"
    cxx_header = "mem/chi/hnf/slc_snoop_filter.hh"

    slc_num_sets = Param.Unsigned(1024, "Number of SLC sets")
    slc_assoc = Param.Unsigned(16, "SLC associativity")

    sf_num_sets = Param.Unsigned(2048, "Number of SF sets")
    sf_assoc = Param.Unsigned(16, "SF associativity")

    num_rnfs = Param.Unsigned(8, "Number of RN-F nodes")

    req_queue_entries = Param.Unsigned(8, "SLCSF request queue entries")
    resp_queue_entries = Param.Unsigned(8, "SLCSF response queue entries")

    max_inflight = Param.Unsigned(4, "Maximum in-flight operations")
    lookup_issue_width = Param.Unsigned(1, "Lookup issue width")
    fill_issue_width = Param.Unsigned(1, "Fill issue width")
    update_issue_width = Param.Unsigned(1, "Update issue width")

    victim_buffer_entries = Param.Unsigned(
        2, "Dirty SLC victim entries"
    )
    seq_entries = Param.Unsigned(
        8, "SF eviction sequence entries"
    )

    lookup_latency = Param.Cycles(4, "Lookup service latency")
    fill_latency = Param.Cycles(4, "Fill service latency")
    update_latency = Param.Cycles(2, "Update service latency")
    victim_latency = Param.Cycles(3, "Dirty victim extraction latency")
    sf_evict_latency = Param.Cycles(2, "SF eviction preparation latency")
    replay_penalty = Param.Cycles(2, "Replay retry penalty")

    init_latency = Param.Cycles(16, "Abstract SLC/SF initialization latency")

    slc_victim_policy = Param.String("rr", "rr/random/plru")
    sf_victim_policy = Param.String("rr", "rr/random/plru")
```

SimObject 的 Python 类负责参数和 C++ 类映射；SimObject 之间也可以通过参数形成层次和引用。([gem5][4])

HNF 配置：

```python
hnf.slcsf = SlcSnoopFilter(
    clk_domain=hnf.clk_domain
)

hnf.cc.slcsf = hnf.slcsf
```

`CoherenceController.py` 中：

```python
slcsf = Param.SlcSnoopFilter("The HNF SLC/SF service")
```

LinkLayer 不需要 `slcsf` 参数。

## 12.2 `SConscript`

```python
Import("*")

SimObject(
    "SlcSnoopFilter.py",
    sim_objects=["SlcSnoopFilter"],
)

Source("slc_snoop_filter.cc")
Source("slcsf_service.cc")
Source("slc_array.cc")
Source("sf_directory.cc")
Source("slcsf_policy.cc")
Source("slcsf_buffers.cc")
Source("slcsf_stats.cc")

DebugFlag("SlcSf")
DebugFlag("SlcSfQueue")
DebugFlag("SlcSfState")
DebugFlag("SlcSfVictim")
DebugFlag("SlcSfReplay")
```

gem5 需要在 SConscript 中注册 Python SimObject 和 C++ source。([gem5][4])

---

# 13. 初始化、drain 和 checkpoint

## 13.1 初始化

粗粒度 MVP 不必逐 set 扫描。

`initState()`：

```cpp
void
SlcSnoopFilter::initState()
{
    slc.reset();
    sf.reset();
    victimBuffer.reset();
    seqBuffer.reset();

    initDone = false;
}
```

`startup()`：

```cpp
void
SlcSnoopFilter::startup()
{
    if (restored) {
        scheduleNextWakeup();
        return;
    }

    service.startInit(
        curTick(),
        clockEdge(std::max(params().init_latency, Cycles(1)))
    );

    ensureWakeup();
}
```

初始化完成前：

```text
registeredReqCredits = 0
tryEnqueue() = Initializing
```

完成后生成一次：

```cpp
SlcSfResp::InitDone
```

或者仅设置 `initialized()`，由 HNF 顶层读取。现有设计也明确把 init completion 作为 PoC/HNF 可观察状态。

## 13.2 Drain

MVP 推荐只支持 drained checkpoint。

```cpp
DrainState
SlcSnoopFilter::drain()
{
    draining = true;

    if (completelyIdle())
        return DrainState::Drained;

    ensureWakeup();
    return DrainState::Draining;
}
```

`completelyIdle()`：

```text
reqIngress empty
reqReady empty
respPending empty
respVisible empty
service inflight empty
VictimBuffer 不存在未完成 owner
SeqBuffer 不存在未完成 FVB
```

`drainResume()`：

```cpp
void
SlcSnoopFilter::drainResume()
{
    draining = false;
    updateRegisteredCredits();
    ensureWakeup();
}
```

## 13.3 Serialize

至少保存：

```text
initDone
visibleReqCredits

SLC tags/data/generation
SLC replacement state

SF tags/generation
SF replacement state

VictimBuffer entries
SeqBuffer entries

next ReqId / VictimId / ReservationId
```

MVP 在 drain 完成后 checkpoint，因此不必保存：

```text
active inflight readyTick
半完成的 response slot reservation
正在调度的 transient operation
```

以后需要 active checkpoint，再序列化 queue、inflight 和 event。`SimObject` 已处于 gem5 的 `Serializable`、`Drainable` 和 statistics 体系中。([GitHub][5])

---

# 14. 建议统计项

```cpp
struct SlcSfStats : public statistics::Group
{
    statistics::Scalar lookups;

    statistics::Scalar slcHits;
    statistics::Scalar sfHits;

    statistics::Scalar slcHitSfHit;
    statistics::Scalar slcHitSfMiss;
    statistics::Scalar slcMissSfHit;
    statistics::Scalar slcMissSfMiss;

    statistics::Scalar fills;
    statistics::Scalar updates;
    statistics::Scalar invalidates;

    statistics::Scalar cleanVictims;
    statistics::Scalar dirtyVictims;
    statistics::Scalar sfVictims;

    statistics::Scalar directedSnoops;
    statistics::Scalar broadcastSnoops;

    statistics::Scalar replays;
    statistics::Scalar setWayReplays;
    statistics::Scalar victimBufferReplays;
    statistics::Scalar seqFullReplays;
    statistics::Scalar staleTokenReplays;

    statistics::Scalar reqQueueFullCycles;
    statistics::Scalar respQueueFullCycles;

    statistics::Histogram reqQueueOccupancy;
    statistics::Histogram respQueueOccupancy;
    statistics::Histogram lookupLatency;
    statistics::Histogram fillLatency;
};
```

性能统计和 correctness 状态不要混在 storage entry 中。

---

# 15. 推荐实现顺序

| 阶段 | 实现内容                                      | 验证目标                                |
| -- | ----------------------------------------- | ----------------------------------- |
| 1  | `slcsf_types.hh`、`SlcArray`、`SfDirectory` | storage hit/miss 和状态不变量             |
| 2  | `SlcSfPolicy`                             | 每类 transaction 的 lookup/commit plan |
| 3  | 单 inflight `SlcSfService`                 | lookup/fill/update 参数化延迟            |
| 4  | `SlcSnoopFilter : ClockedObject`          | request/response 双缓冲、无 0-cycle      |
| 5  | PoCQ Lookup 对接                            | 四种 SLC/SF hit/miss                  |
| 6  | Fill/Update + generation token            | stale token replay                  |
| 7  | dirty VictimBuffer                        | victim data 与 SN writeback 生命周期     |
| 8  | SF victim + SeqBuffer/FVB                 | SEQ full/race replay                |
| 9  | 多 inflight、set/way reservation            | 不同 set 并行、同 set 冲突                  |
| 10 | drain/checkpoint/stats                    | 长测试和恢复测试                            |

MVP 最好先配置：

```text
lookupIssueWidth = 1
fillIssueWidth   = 1
updateIssueWidth = 1
maxInflight      = 1
victimPolicy     = RoundRobin
```

功能跑通后，再打开不同 set 并行。这样不会在第一版同时调试 transaction correctness 和复杂资源调度。

---

# 16. 必须固化为断言的框架不变量

```cpp
// 每个 accepted request 恰好一个 terminal response。
acceptedRequests ==
    completedResponses + replayResponses + errorResponses + inflightRequests;
```

```cpp
// response 不得在 request 接收周期可见。
resp.visibleCycle > req.acceptedCycle;
```

```cpp
// Replay 不修改 persistent storage。
generationAfterReplay == generationBeforeRequest;
```

```cpp
// Dirty line 必须有完整数据。
isDirty(entry.state) -> entry.dataValid;
```

```cpp
// Dirty victim 覆盖前必须进入 VictimBuffer。
dirtyVictim -> victimBufferReservation.valid();
```

```cpp
// SF victim 覆盖前必须进入 SEQ。
sfVictim -> seqReservation.valid();
```

```cpp
// Commit token 必须匹配当前 line/way/generation。
commit -> tokenValidated;
```

```cpp
// 同 line 不能同时存在两个已提交有效 entry。
uniqueValidTagPerSetAndLine;
```

```cpp
// 外部消息不得保存 storage 裸指针。
static_assert(std::is_trivially_copyable_v<LineKey>);
```

最终推荐的核心边界可以概括为：

```text
PoCQ owns transaction
SLCSF owns cache/directory state

PoCQ issues intent
SLCSF returns delayed facts and decisions

PoCQ performs external CHI actions
SLCSF commits state only through explicit Fill/Update

No request produces a semantic result synchronously
No response can trigger a same-cycle request execution
```

[1]: https://github.com/gem5/gem5/blob/stable/src/sim/clocked_object.hh "gem5/src/sim/clocked_object.hh at stable · gem5/gem5 · GitHub"
[2]: https://raw.githubusercontent.com/gem5/gem5/stable/src/sim/eventq.hh "raw.githubusercontent.com"
[3]: https://www.gem5.org/documentation/learning_gem5/part2/events/ "
		gem5: Event-driven programming
	"
[4]: https://www.gem5.org/documentation/learning_gem5/part2/helloobject/ "
		gem5: Creating a very simple SimObject
	"
[5]: https://github.com/gem5/gem5/blob/stable/src/sim/sim_object.hh "gem5/src/sim/sim_object.hh at stable · gem5/gem5 · GitHub"

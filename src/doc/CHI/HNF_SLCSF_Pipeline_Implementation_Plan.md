# HnfSLCSF 流水线化实现方案

> 适用范围：`src/mem/cache/CHI/HnfSLCSF.{hh,cc}`、`HnfCoherencyController.{hh,cc}`、`HomeNodeFull.{hh,cc}`、`HnfCcTypes.hh`、`HnfPOCQStateGraph.{hh,cc}`、`HomeNodeFull.py`、`SConscript`
>
> 关联文档：`HNF_SLCSF_Gem5_Spec.md`（目标架构）、`HNF_Gem5_Architecture_Design_Document.md`、`HNF_POCQ_SEQ_Litmus_Implementation_and_Debug_Case_Studies.md`

---

## 0. 已交付实现状态（2026-07）

本文后续章节保留了开发前的分阶段设计和伪代码，作为设计决策记录。
当其与本节冲突时，以本节和当前代码为准：阶段 1–7 已经交付，
SLCSF 不再是 CC 按值持有且由 `HomeNodeFull::wakeup()` 直接推进的
内嵌对象。

### 0.1 当前所有权和调度

- `HomeNodeFull` 通过 SimObject 参数持有 child
  `SlcSnoopFilter : ClockedObject`；`HomeNodeFull` 和 CC 仅保留非拥有指针。
- `SlcSnoopFilter` 按值拥有不可复制的普通 C++ service `HnfSLCSF`；
  `HnfSLCSFBackend` 是 SLC/SF/SEQ/replacement 持久语义的唯一所有者。
- child 的 `EventFunctionWrapper` 是 service 的唯一推进源。它根据最早有用
  logical cycle 按需调度，不在空闲时每拍自唤醒。可见响应或从 0
  转为非 0 的寄存 credit 只会请求严格未来的 owner wakeup，不在
  callback 内推进 POCQ。

### 0.2 POCQ graph 和退休边界

- Lookup 使用 CC entry 中的 `IssuePending -> Waiting -> ResponseLatched`
  阶段。`DoSlcLookup` 只构造并入队一个拥有型请求；只有后续 CC
  service boundary 才消费寄存响应并向 graph 注入
  `SlcLookupDone`。
- 持久变更使用 `SlcUpdateIssue -> SlcUpdateWait`，包括 read
  commit、maintenance、write、evict 和 sharer removal。变更请求按值携带
  lookup `CommitToken`，在唯一写入级之前校验；Replay 回到 sleep/
  retry 路径重新 lookup，不会带着旧 token 直接重放 mutation。
- SEQ/FVB completion 和 dirty-victim release 也是有 reqId 的异步变更，
  terminal response 保持 producer-bound lease，直到 CC 精确 ACK 才释放容量所有权。
- RXDAT 触发的 write 可在原调用栈返回后完成。CC 将其 retire info
  放入拥有型 `deferredRetireQ`；HomeNode/LinkLayer 将该队列计入 work，
  并在后续 owner scheduling boundary 恢复且仅恢复一次。

`FlushSf`/`FlushL3`/`WriteL3FlushSf` 在当前 protocol graph 中仍不可达。
它们已有拥有型异步 service request、latency/token/resource 路径和定向
单元测试；本次实现没有为尚未定义的 CHI opcode 凭空新增 protocol
entry path。

### 0.3 service latency 的口径

`lookup/fill/update` latency 是从请求在 child edge 上成功 issue 到最早
terminal production 的 child cycles，不包含请求和响应双缓冲的额外
寄存边界。请求在 acceptance 时进 `reqIngress`，最早下一个
child edge 进 `reqReady` 并 issue；在 `issueCycle + serviceLatency`
无资源 stall 时最早成为 `respPending`，stall 只会延后它；再经一个
child edge 进 `respVisible`。因此
`issueCycle >= acceptedCycle + 1`、`completeCycle >= issueCycle + L`、
`visibleCycle >= completeCycle + 1`，且跨时钟域比较使用绝对 gem5 `Tick`。
dirty SLC victim 和 SF victim 分别在该请求的基础 service latency 上叠加
`victim_latency` 和 `sf_evict_latency`；L0 早期 Replay 的 service 部分为
1 child cycle，重试绝对截止时间在 terminal cleanup 时加上
`replay_penalty`。

### 0.4 语义基线和阶段 7 文件清单

流水线化前的 `HnfSLCSF.test.cc` 基线是下列 **10** 个测试，不是
11 个：

1. `ReadUniqueBroadcastsToOtherVectorSharers`
2. `ReadUniqueExcludesRequesterFromBroadcast`
3. `ReadUniqueIsDirectedToDifferentUniqueOwner`
4. `ReadSharedObtainsDirtyDataFromUniqueOwner`
5. `EvictRemovesTheRequestingSharerOnly`
6. `SfVictimEntersSeqAndBlocksConflictingLookups`
7. `DirtySeqSnoopDataIsPreservedInSlc`
8. `ReservationsPreventSeqSlotOvercommit`
9. `CurrentOwnerWritebackPreservesLatestData`
10. `StaleWritebackCannotOverwriteNewOwner`

阶段 7 生产文件是 `SlcSnoopFilter.{py,hh,cc}`、`HomeNodeFull.{py,hh,cc}`、
`HnfSLCSF.{hh,cc}`、`HnfSLCSFBackend.{hh,cc}`、`HnfSLCSFRequest.hh`、
`HnfSLCSFResponse.hh`、`HnfCoherencyController.{hh,cc}`、
`HnfPOCQStateGraph.{hh,cc}`、`HnfSeqPOCQStateGraph.{hh,cc}` 和 `SConscript`。
直接覆盖位于 `SlcSnoopFilter.test.cc`、`HnfSLCSFBackend.test.cc`、
`HnfSLCSF.test.cc`、`HnfCoherencyController.test.cc` 和
`tests/gem5/chi/slcsf_child_params.py`。

只允许在 HomeNode 完成 D1 upstream quiesce 和 D2 child admission seal，且
parent/CC/deferred-retire 与 child request/response/in-flight/set-lock/VictimBuffer/SEQ
全部空闲时创建 checkpoint。checkpoint 分属两个序列化边界：

- child 保存 initialized/logical-cycle，SLC tag/data/state/generation/replacement，
  SF tag/owner/sharers/state/generation/replacement，SEQ 持久元数据，storage epoch/
  access/generation 计数器，以及 victim/reservation/set-lock 的 next IDs；
- parent CC 保存下一个 SLCSF request ID。

不序列化 active queue、in-flight stage/ready tick、response reservation、
deferred retire 或已分配的 protocol/transient owner；active checkpoint 明确不支持。

这里的 D1/D2 保证只覆盖 HomeNode、CC 和 SLCSF 的本地所有权边界。创建
drained checkpoint 前，外部 CHI fabric 和 Cache2ChiBridge 必须已经停止注入并
排空所有发往该 HomeNode 的请求。当前 `kmhv2_chi_2x2_router` 拓扑尚未实现
Router/Bridge 间的 ordered drain fence/epoch，因而不支持直接依赖全局
DrainManager 自动完成整条 2x2 fabric 的 checkpoint；该能力需要单独的系统级
实现和测试，不能由 HomeNode 的本地 admission gate 推断。

### 0.5 最终回归命令

以下命令均从 `GEM5` workspace 根目录执行。

```bash
scons build/RISCV/gem5.opt -j16

scons \
  build/RISCV/mem/cache/CHI/hnf_pocq_state_graph.test.opt \
  build/RISCV/mem/cache/CHI/hnf_seq_pocq_state_graph.test.opt \
  build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt \
  build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt \
  -j16

build/RISCV/mem/cache/CHI/hnf_pocq_state_graph.test.opt
build/RISCV/mem/cache/CHI/hnf_seq_pocq_state_graph.test.opt
build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt
build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt
```

默认四核 litmus：

```bash
build/RISCV/gem5.opt \
  --outdir=m5out/kmhv2_chi_2x2_router_4cpu_litmus_xbar6 \
  --listener-mode=off \
  --redirect-stdout --stdout-file=simout \
  --redirect-stderr --stderr-file=simerr \
  configs/example/kmhv2_chi_2x2_router.py \
  --num-cpus=4 \
  --generic-rv-cpt=/home/makenma/project/xs-gem5/nexus-am/apps/chi-litmus/build/chi-litmus-riscv64-xs.bin \
  --raw-cpt --no-pf \
  --l1d_size=16kB --l2_size=32kB \
  -m 2000000000
```

强制 `SF=64 sets x 4 ways, SEQ=4` 四核 litmus（保留旧 parent 参数路径）：

```bash
build/RISCV/gem5.opt \
  --outdir=m5out/kmhv2_chi_2x2_router_4cpu_litmus_xbar_seq \
  --listener-mode=off \
  --debug-flags=HnfCC,HnfSLCSF \
  --debug-start=80000000 --debug-end=150000000 \
  --debug-file=seq.log.gz \
  --redirect-stdout --stdout-file=simout \
  --redirect-stderr --stderr-file=simerr \
  configs/example/kmhv2_chi_2x2_router.py \
  --num-cpus=4 \
  --generic-rv-cpt=/home/makenma/project/xs-gem5/nexus-am/apps/chi-litmus/build/chi-litmus-riscv64-xs.bin \
  --raw-cpt --no-pf \
  --l1d_size=16kB --l2_size=32kB \
  -P 'system.home_node[0].sf_num_sets=64' \
  -P 'system.home_node[0].sf_num_ways=4' \
  -P 'system.home_node[0].seq_entries=4' \
  -m 2000000000
```

## 1. 背景与目标

### 1.1 现状

下表记录流水线化开始前的基线：当时 `HnfSLCSF` 是
`HnfCoherencyController` 按值持有的**普通 C++ 对象**，所有接口都是
**组合逻辑（0 cycle）同步返回**：

| 接口 | 调用点（CC） | 语义 |
| --- | --- | --- |
| `tryReserveSfResources()` | `startReadFlow()` | 同步返回 bool（资源记账） |
| `lookup()` | `executePocqAction(DoSlcLookup)` | 同步返回 `HnfSlcLookupResult` |
| `commitRead()` | `commitRead()` / `UpdateSlcSf` | 同步写 SLC+SF |
| `completeMaintenance()` | `completeMaintenance()` | 同步写 |
| `removeSharer()` | `RemoveSharer` | 同步写 |
| `writeLine()` | write 路径 | 同步写 |
| `flushSf()` / `flushL3()` / `writeL3FlushSf()` | `FlushSf`/`FlushL3`/`WriteL3FlushSf` | 同步写 |
| `frontPendingSeq()` / `markSeqIssued()` / `completeSfEvict()` | seq pocq 路径 | 同步 |

最关键的组合回路在 `executePocqAction` 的 `DoSlcLookup` 分支（`HnfCoherencyController.cc:162`）：

```cpp
case PocqActionKind::DoSlcLookup: {
    entry.slcLookupResult = slcsfUnit->lookup(lookup);   // 同步
    ...
    return stepPocq(entryId, lookupDone);                 // 同一周期再次推进 POCQ
}
```

`lookup()` 返回后**同一 tick 内**就用 `SlcLookupDone` 事件二次推进 POCQ 状态机。也就是说一次 POCQ step 在 0 cycle 内完成了"发起 lookup → 拿到结果 → 决定下一分支"。这无法体现 SLC tag read / SF directory read 的真实延迟，也阻塞了同周期其它 entry 的并行推进。

### 1.2 目标

1. 给 `HnfSLCSF` 增加**多级流水线**，使 lookup / fill / update 具有可配置的非零延迟。
2. **打破 0-cycle 组合回路**：`DoSlcLookup` 不再同步返回结果，而是入队请求、转入等待态；结果在若干周期后通过 response queue 回送，由 CC 的周期性 `serviceInternalWork()` 注入 `SlcLookupDone` 事件。
3. 保持**功能等价**：流水线化前后，单线程顺序场景的 cache 行为（hit/miss、snoop target、victim、SEQ、replay）完全一致，只是时序被拉伸。
4. 保持**最小侵入**：不要求一次性重构为独立 `ClockedObject`（那是 `HNF_SLCSF_Gem5_Spec.md` 的远期目标）。本方案在现有 `HomeNodeFull` 单事件源下完成流水线化，作为通向 Spec 目标架构的中间里程碑。

### 1.3 两条实现路径对比

| 路径 | 描述 | 优点 | 代价 | 结论 |
| --- | --- | --- | --- | --- |
| **A. 内嵌流水线** | `HnfSLCSF` 仍是普通对象，增加 `tick()` 由 `HomeNodeFull::wakeup()` 驱动 | 改动面小；复用现有单事件源；POCQ/LinkLayer 调度不变 | 时钟/参数/统计仍寄生在 CC；无法独立 `clk_domain` | **本方案推荐（阶段 1–6）** |
| **B. 独立 ClockedObject** | 拆出 `SlcSnoopFilter : ClockedObject`，独立 `.py`/params/event | 符合 Spec 目标；可独立调时序、drain、checkpoint | 需改 `HomeNodeFull.py` 层次、SConscript、CC 持有方式 | **阶段 7（可选，后续迁移）** |

本方案先走 A，把流水线时序正确性与 POCQ 解耦跑通；代码稳定后再按 `HNF_SLCSF_Gem5_Spec.md` 第 2、5、12 节迁移到 B，接口不变。

---

## 2. 流水线架构设计

### 2.1 总体数据通路

```
        ┌──────────────── HnfSLCSF (内嵌流水线) ────────────────┐
        │                                                         │
 CC ──► reqIngress ──► reqReady ──► [Lookup Pipe] ──► respPending ──► respVisible ──► CC
        │                              [Update Pipe]               │
        │                                                         │
        │  tick() 由 HomeNodeFull::wakeup() 每周期驱动            │
        └─────────────────────────────────────────────────────────┘
```

两条独立流水线共享同一份 SLC/SF 存储，但**分级锁 set**：

- **Lookup Pipe**（读通路）：`L0 Decode → L1 SLC tag read → L2 SF tag read → L3 Policy/Snoop decide → L4 Latch resp`
- **Update Pipe**（写通路）：`U0 Decode/Validate → U1 Victim extract → U2 Array write → L4 Latch resp`（末级与 Lookup 复用 latch 段，统一进 `respPending`）

### 2.2 流水段定义

#### 2.2.1 Lookup Pipe

| 段 | 工作 | 读 | 写 | 备注 |
| --- | --- | --- | --- | --- |
| L0 Decode | 解析 txn/addr，算 set/tag；查 SEQ 命中、replay 判定 | — | 入流水寄存器 | replay 在此段即可短路输出（仍走 1 cycle） |
| L1 SLC read | `findSlc()` + 读 data | SLC set | — | hit/miss、state、dataDirty |
| L2 SF read | `findSf()` + 算 snoop target | SF set | — | rnfid/rnfvec、snoopOpcode、broadcast/directed |
| L3 Policy | 按 txn 合并 L1/L2 结果，定 `mcreqNonspec`、victim 候选 | — | — | 纯组合，复用现有 `lookup()` 末段逻辑 |
| L4 Latch | 组装 `HnfSlcLookupResult`，写 `respPending` | — | respPending | 末级寄存存器 |

> 现有 `lookup()` 方法体正好可按 L0–L4 切分：L0 = 开头的 SEQ/replay 判定；L1 = `findSlc` 块；L2 = `findSf` 块；L3 = `mcreqNonspec` 与 DPRINTF；L4 = return。切分时把局部变量提升为流水寄存器结构体 `LookupPipeReg`。

#### 2.2.2 Update Pipe

| 段 | 工作 | 读 | 写 |
| --- | --- | --- | --- |
| U0 Decode | 解析 commit kind（commitRead/completeMaintenance/writeLine/flush*/removeSharer），校验 commit token（generation） | — | 入流水寄存器 |
| U1 Victim | 若需替换：`selectSfVictim` → `installSeqVictim`（SF victim）或 dirty SLC victim 快照 | SLC/SF set | SEQ、VictimBuffer |
| U2 Write | `installSlc` / `allocateSf` / `invalidateSlc` / `invalidateSf` / `removeSharer` 主体 | SLC/SF set | SLC、SF |
| U3 Check+Latch | `checkLineInvariant()`，写 `respPending` | — | respPending |

> 现有 `commitRead/writeLine/...` 各方法体拆成"算 victim（U1）"+"写 array（U2）"+"check（U3）"三段。`checkLineInvariant` 必须在写入完成后执行，因此放 U3。

### 2.3 结构冒险：set 锁

为避免同 set 并发读写破坏 tag/directory 一致性，引入**逐 set 流水锁**：

```cpp
struct SetLock {
    bool lookupHeld = false;   // L1/L2 占用
    bool updateHeld = false;   // U1/U2 占用
    int  holderEntry = -1;
};
std::vector<SetLock> slcSetLocks;   // size = slcSets
std::vector<SetLock> sfSetLocks;    // size = sfSets
```

- L0→L1 前必须拿到对应 SLC set 与 SF set 的 lookup 锁；拿不到则**停在 L0**（不产生 replay，纯 stall）。
- U0→U1 前必须拿到 set 的 update 锁；lookup 与 update 互斥同 set。
- 段退出时释放。

> MVP 阶段可先简化为**全流水线单条 in-flight**（`maxInflight=1`），不实现 set 锁并行；功能验证后再放开（见第 11 节阶段 6）。

### 2.4 数据冒险：commit token / generation

`HNF_SLCSF_Gem5_Spec.md` §4.3 要求 lookup 返回 generation snapshot，commit 时校验。当前代码没有 generation 校验（依赖 CC 的地址 hazard 串行化）。流水线化后，lookup 与 commit 之间会间隔多个周期，**必须引入 generation 校验**以防 race：

- `SlcLine` / `SfLine` 已有 `generation` 字段（每次写递增 `accessCounter`）。复用之。
- `HnfSlcLookupResult` 增加 `slcGeneration` / `sfGeneration`。
- CC `Entry` 增加 `slcsfToken`（保存 lookup 时的 generation）。
- Update Pipe 的 U0 校验 token：若 generation 不匹配 → 输出 `SlcSfStatus::StaleToken` replay，**不写 array**。

> 这是流水线化引入的新 correctness 要求。当前同步实现因"lookup 即返回、同周期 commit"而天然无 race；流水线化后必须补上。

### 2.5 Replay 语义

沿用现有 `entry.slcsfReplay` + `SleepForReplay` + `retrySlcsfReplayEntries()` 机制，replay 原因细化：

| 来源 | 触发 | CC 行为 |
| --- | --- | --- |
| SEQ 命中 / SEQ full | L0 判定 | `SlcLookupDone{replay=true}` → POCQ `slcLookup → sleep` |
| Stale commit token | U0 判定 | `SlcUpdateDone{replay=true}` → CC 重发 lookup |
| Resource NoCredit | `tryEnqueue` 阶段 | CC 留在 Issue 态，下周期重试（不 sleep） |

Replay 必须保证**不修改持久存储**（不递增 generation、不写 tag/data、不删 sharer）——这与 Spec §16 不变量一致。现有 `lookup()` replay 分支已满足（直接 return），Update Pipe 的 U0 校验失败也必须在 U1 之前短路。

---

## 3. 请求 / 响应双缓冲

严格遵循 `HNF_SLCSF_Gem5_Spec.md` §1.6 的四条规则，避免 0-cycle 组合回路。

### 3.1 队列结构

```cpp
class HnfSLCSF
{
    // ---- 请求双缓冲 ----
    std::deque<SlcSfReq> reqIngress;   // 本周期 CC 写入
    std::deque<SlcSfReq> reqReady;     // 本周期流水线可读

    // ---- 响应双缓冲 ----
    std::deque<SlcSfResp> respPending; // 本周期流水线完成写入
    std::deque<SlcSfResp> respVisible; // 下一周期对 CC 可见

    // ---- 流水线寄存器 ----
    std::array<LookupPipeReg, LookupDepth> lookupPipe;
    std::array<UpdatePipeReg, UpdateDepth> updatePipe;
    std::vector<InFlightOp> inflight;   // 带完成时刻的已发射操作

    // ---- 反压 ----
    uint32_t reqCredit;        // 对 CC 暴露的可用请求槽
    uint32_t reqCreditNext;    // 下一周期生效
};
```

### 3.2 `tick()` 主流程（每周期调用一次）

```cpp
void HnfSLCSF::tick()
{
    // 1. 暴露上一周期完成的响应
    for (auto& r : respPending) respVisible.push_back(std::move(r));
    respPending.clear();

    // 2. 暴露上一周期接收的请求
    for (auto& r : reqIngress) reqReady.push_back(std::move(r));
    reqIngress.clear();

    // 3. 逆序推进流水线：末段先走，腾出空间
    advanceLookupPipe();   // L4→respPending, L3→L4, ..., L0→L1
    advanceUpdatePipe();   // U3→respPending, U2→U3, ..., U0→U1

    // 4. 从 reqReady 取新操作注入 L0/U0（受 issue width / set lock / inflight 限制）
    issueReady();

    // 5. 更新对 CC 可见的 credit
    reqCredit = reqCreditNext;
    reqCreditNext = capacity - (reqReady.size() + inflightCount());

    // 6. 通知 HomeNodeFull 是否还有 work（用于事件自调度）
}
```

要点：
- **完成先于发射**（Spec §6.3）：先 `advance*` 再 `issueReady`，即使 latency 配 0，新发射的操作也不会本周期 complete。
- **双缓冲**：CC 在本周期 `tryEnqueue` 只写 `reqIngress`，最早下周期进流水；流水线本周期完成只写 `respPending`，最早下周期被 CC 读到。天然满足 `resp.visibleCycle > req.acceptedCycle`。

### 3.3 对外接口（替换原同步接口）

```cpp
// 替换 lookup() / commitRead() / writeLine() / flush*() / completeMaintenance() / removeSharer()
enum class SlcSfEnqResult : uint8_t { Accepted, NoCredit };
SlcSfEnqResult tryEnqueue(const SlcSfReq& req);

// CC 轮询
bool responseAvailable() const { return !respVisible.empty(); }
SlcSfResp     frontResponse() const;
void          popResponse();

// 同步保留（纯资源记账 / 状态读取，0-cycle 合法，见 Spec §1.5）
bool tryReserveSfResources(uint32_t entry, uint64_t addr, PocqTxnKind txn);
void releaseSfResources(uint32_t entry);
bool hasSfReservation(uint32_t entry) const;
bool hasPendingSeq() const;
SeqVictim frontPendingSeq() const;     // seq 路径仍由 CC 驱动，可保留同步
void markSeqIssued(SeqId id);
void completeSfEvict(SeqId id, const std::vector<uint8_t>& data, bool dirty);

// 驱动
void tick();
bool hasWork() const;
```

### 3.4 `SlcSfReq` / `SlcSfResp` 定义（新增到 `HnfCcTypes.hh`）

```cpp
enum class SlcSfOp : uint8_t {
    Lookup,
    CommitRead,
    CompleteMaintenance,
    RemoveSharer,
    WriteLine,
    FlushSf,
    FlushL3,
    WriteL3FlushSf,
};

struct SlcSfReq {
    SlcSfOp op;
    uint32_t entry;          // POCQ entry id（= tokenId）
    uint64_t seq;            // 调试用
    RawReq   req;            // CHI 原始请求
    PocqTxnKind txn;
    uint64_t blockAddr;
    // commit 类操作携带的数据
    std::vector<uint8_t> data;
    bool dataDirty = false;
    uint32_t homeNodeId = 0;
    // commit token（lookup 时由 CC 填，update 时校验）
    uint64_t slcGeneration = 0;
    uint64_t sfGeneration = 0;
};

enum class SlcSfStatus : uint8_t { Done, Replay, StaleToken };

struct SlcSfResp {
    SlcSfOp op;
    uint32_t entry;
    uint64_t seq;
    SlcSfStatus status = SlcSfStatus::Done;
    HnfSlcLookupResult lookupResult;  // 仅 Lookup 有效
};
```

---

## 4. CC / POCQ 改造

### 4.1 `Entry` 新增字段

```cpp
struct Entry {
    ...
    // ---- SLCSF 流水线对接 ----
    std::optional<uint64_t> slcsfReqSeq;   // 匹配响应
    bool slcsfLookupPending = false;       // 已发射 lookup，等响应
    bool slcsfUpdatePending  = false;      // 已发射 update，等响应
    uint64_t slcGeneration = 0;            // commit token
    uint64_t sfGeneration  = 0;
};
```

### 4.2 打破 `DoSlcLookup` 组合回路

原实现：`DoSlcLookup` 同步 lookup + 同周期 `stepPocq(SlcLookupDone)`。

改为：`DoSlcLookup` 只**入队请求**，POCQ 停在 `slcLookup` 态等待；`SlcLookupDone` 事件由 `serviceInternalWork()` 在收到响应后注入。

```cpp
case PocqActionKind::DoSlcLookup: {
    entry.state = HnfCcEntryState::WaitSlc;

    SlcSfReq req = buildLookupReq(entryId);   // 见下
    auto r = slcsfUnit->tryEnqueue(req);
    if (r == SlcSfEnqResult::Accepted) {
        entry.slcsfLookupPending = true;
        entry.slcsfReqSeq = req.seq;
        // 不再同周期 stepPocq！POCQ 留在 slcLookup 态。
    } else {
        // NoCredit：留在 slcLookup 态，下周期由 serviceInternalWork 重试
        entry.slcsfLookupPending = false;
    }
    return std::nullopt;   // 不产生 retire
}
```

> 关键：`DoSlcLookup` 不再调用 `stepPocq(lookupDone)`。`slcLookup` 状态在 POCQ 图里本身就是"等待 `SlcLookupDone`"的态（见 `HnfPOCQStateGraph.cc:230` 起的 5 条出边），原本就是为异步设计的，只是被同步实现"借用"了。流水线化后正好回归图的本意。

### 4.3 `UpdateSlcSf` / `CommitRead` / `CommitMaintenance` 同理

这些 action 现在同步调用 `slcsfUnit->commitRead()` 等。改为入队 `SlcSfReq{op=CommitRead/...}`，POCQ 停在 `slcUpdate` 态，等 `SlcUpdateDone` 响应。

```cpp
case PocqActionKind::CommitRead: {
    SlcSfReq req = buildCommitReadReq(entryId);
    if (slcsfUnit->tryEnqueue(req) == SlcSfEnqResult::Accepted) {
        entry.slcsfUpdatePending = true;
        entry.slcsfReqSeq = req.seq;
    }
    return std::nullopt;
}
```

`FlushSf`/`FlushL3`/`WriteL3FlushSf`/`RemoveSharer`/`StoreWriteData` 同样改造为入队。

> 注意：`RemoveSharer` 和 `releaseSfResources` 当前在同一个 action 里连调。改造后 `RemoveSharer` 入队 update 请求，`releaseSfResources` **不能在响应回来前调用**（资源仍被 in-flight 操作占用）。需把 `releaseSfResources(entryId)` 移到响应处理处（见 §4.5）。

### 4.4 响应处理：`serviceInternalWork()` 注入事件

```cpp
void HnfCoherencyController::serviceInternalWork()
{
    // 1. 消费 SLCSF 可见响应
    while (slcsfUnit->responseAvailable()) {
        const SlcSfResp& r = slcsfUnit->frontResponse();
        Entry& entry = entries[r.entry];
        panic_if(!entry.slcsfLookupPending && !entry.slcsfUpdatePending,
                 "CC got unexpected SLCSF resp entry=%u op=%u\n", r.entry, (unsigned)r.op);

        if (r.op == SlcSfOp::Lookup) {
            entry.slcsfLookupPending = false;
            entry.slcLookupResult = r.lookupResult;
            entry.slcGeneration = r.lookupResult.slcGeneration;
            entry.sfGeneration  = r.lookupResult.sfGeneration;
            if (r.lookupResult.slcHit) {
                entry.data = r.lookupResult.data;
                entry.data.resize(blockSize, 0);
            }
            entry.responseDataDirty = r.lookupResult.dataDirty;

            PocqEvent ev{};
            ev.kind = PocqEventKind::SlcLookupDone;
            ev.txn = entry.txnKind;
            ev.slcHit = r.lookupResult.slcHit;
            ev.sfHit  = r.lookupResult.sfHit;
            ev.replay = r.lookupResult.replay || r.status == SlcSfStatus::StaleToken;
            ev.needsSnoop = r.lookupResult.snoopTargets != 0;
            ev.dataAvailable = r.lookupResult.slcHit;
            ev.needsCompAck = entry.needsCompAck;
            stepPocq(r.entry, ev);

        } else {
            // Update 类响应
            entry.slcsfUpdatePending = false;
            slcsfUnit->releaseSfResources(r.entry);   // 资源在此释放
            if (r.status == SlcSfStatus::StaleToken) {
                // 重发 lookup
                entry.slcsfReplay = true;
                entry.state = HnfCcEntryState::Sleep;
                // 下周期 retrySlcsfReplayEntries() 会重新 startReadFlow
            } else {
                PocqEvent ev{};
                ev.kind = PocqEventKind::SlcUpdateDone;
                ev.txn = entry.txnKind;
                ev.needsCompAck = entry.needsCompAck;
                stepPocq(r.entry, ev);
            }
        }
        slcsfUnit->popResponse();
    }

    // 2. seq pocq 驱动（不变）
    if (!seqPocqEntry.valid && slcsfUnit->hasPendingSeq()) startSeqPocq();
    else if (seqPocqEntry.valid && seqPocqEntry.state == SeqPocqState::Sleep &&
             !hasMainAddressHazard(seqPocqEntry.blockAddr))
        stepSeqPocq({SeqPocqEventKind::HazardClear});

    // 3. 重试 NoCredit 的 issue（lookup/update 入队失败的 entry）
    retrySlcsfIssuePending();

    // 4. replay 重试（不变）
    retrySlcsfReplayEntries();
}
```

### 4.5 `retrySlcsfIssuePending()`（新增）

处理 `tryEnqueue` 返回 NoCredit 的情况——这些 entry 仍处在 POCQ 的 `slcLookup` / `slcUpdate` 态，但还没成功入队。每周期重试：

```cpp
void HnfCoherencyController::retrySlcsfIssuePending()
{
    for (uint32_t i = 0; i < entries.size(); ++i) {
        Entry& e = entries[i];
        if (e.slcsfLookupPending || e.slcsfUpdatePending) continue;
        if (e.pocqState == PocqState::SlcLookup && !e.slcsfLookupPending) {
            // 重新执行 DoSlcLookup 的入队逻辑
            ...
        } else if (e.pocqState == PocqState::SlcUpdate && !e.slcsfUpdatePending) {
            ...
        }
    }
}
```

> 更干净的做法：把"DoSlcLookup 入队"抽成 `issueLookup(entryId)`，`executePocqAction` 和 `retrySlcsfIssuePending` 都调它，入队失败时不改 POCQ 态。

### 4.6 POCQ 图是否需要改？

**基本不需要改图结构**。现有图已经把 `slcLookup` / `slcUpdate` 当作"等待 `SlcLookupDone`/`SlcUpdateDone`"的态。改动只在：
- `DoSlcLookup` / `CommitRead` / `CommitMaintenance` / `UpdateSlcSf` / `FlushSf` / `FlushL3` / `WriteL3FlushSf` / `RemoveSharer` / `StoreWriteData` 这些 **action 的执行体**（从同步调用改为入队）。
- 新增 `SlcSfStatus::StaleToken` 触发的 `SlcUpdateDone{replay}` 分支：`slcUpdate → sleep`（复用现有 `SleepForReplay` 态/动作，可加一条 `slcUpdate → sleep` 边）。

如果想让"update 完成"也走显式等待态，可把 `slcUpdate` 的入边从 `CommitRead` action 直接进入，出边仍是 `SlcUpdateDone`。图拓扑不变。

---

## 5. HomeNodeFull 调度时序

### 5.1 当前调度

```cpp
void HomeNodeFull::wakeup() { linklayer.wakeup(); }
```

LinkLayer 在 `wakeup()` 中驱动 RX 流水、调 CC `acceptLinkReq/acceptRxRsp/acceptRxDat/serviceInternalWork`。CC 内部对 SLCSF 的调用都嵌在 LinkLayer 的 wakeup 里。

### 5.2 改造后调度

SLCSF 流水线必须在 CC 消费响应**之前**推进一拍：

```cpp
void HomeNodeFull::wakeup()
{
    slcsf.tick();          // 1. 推进 SLCSF 流水线，暴露本周期 respVisible
    linklayer.wakeup();    // 2. LinkLayer 驱动 CC；CC 读 respVisible + 入队 reqIngress
}
```

时序保证：
- CC 本周期入队的请求进 `reqIngress`，最早**下周期** `tick()` 才进 `reqReady` → 满足"PoCQ enqueue at N，SLCSF 最早 issue at N+1"。
- SLCSF 本周期完成的响应进 `respPending`，**下周期** `tick()` 才进 `respVisible` 被 CC 读 → 满足"response.visibleCycle > req.acceptedCycle"。

### 5.3 事件自调度

`HomeNodeFull` 是 `ruby::Consumer`，`wakeup()` 由事件驱动。需保证 SLCSF 有未完成工作时 `HomeNodeFull` 持续被调度。`HomeNodeFull::hasLinkWork()` 已存在，扩展：

```cpp
bool HomeNodeFull::hasLinkWork() const {
    return linklayer.hasWork() || slcsf.hasWork();
}
```

LinkLayer 的 `wakeup()` 末尾原本就会根据 `hasWork` 重调度；确保它也考虑 `slcsf.hasWork()`（或在 `HomeNodeFull::wakeup()` 末尾补一次 `scheduleEvent(Cycles(1))` 当 `slcsf.hasWork()`）。

---

## 6. 参数化

在 `HomeNodeFull.py` 增加 SLCSF 流水线参数（阶段 A 不拆 SimObject，参数挂在 `HomeNodeFull` 上）：

```python
# HomeNodeFull.py 新增
slcsf_lookup_latency   = Param.Cycles(4, "SLCSF lookup pipe latency (L0..L4)")
slcsf_update_latency   = Param.Cycles(3, "SLCSF update pipe latency (U0..U3)")
slcsf_req_queue_entries  = Param.UInt32(8,  "SLCSF request queue entries")
slcsf_resp_queue_entries = Param.UInt32(8,  "SLCSF response queue entries")
slcsf_max_inflight     = Param.UInt32(1,  "SLCSF max in-flight ops (MVP=1)")
slcsf_lookup_issue_width = Param.UInt32(1, "SLCSF lookup issue width/cycle")
slcsf_update_issue_width = Param.UInt32(1, "SLCSF update issue width/cycle")
slcsf_enable_set_lock  = Param.Bool(False, "Enable per-set pipeline lock (并行)")
slcsf_enable_gen_check = Param.Bool(True,  "Enable commit-token generation check")
```

构造时透传给 `HnfSLCSF` 构造函数（扩展其参数列表）。latency 用 `Cycles`，`tick()` 内以"每周期推进一级"实现，`lookupLatency=4` 即 L0→L4 走 4 个 tick。

> Spec §1.6 规则三：所有 latency 至少为 1。构造时 `fatal_if(lookup_latency < Cycles(1))`。

---

## 7. 文件与 SConscript 改动

### 7.1 改动清单

| 文件 | 改动 |
| --- | --- |
| `HnfCcTypes.hh` | 新增 `SlcSfOp`/`SlcSfReq`/`SlcSfResp`/`SlcSfStatus`；`HnfSlcLookupResult` 加 `slcGeneration`/`sfGeneration` |
| `HnfSLCSF.hh` | 新增队列、流水寄存器、`tick()`、`tryEnqueue()`、response 轮询接口；保留同步的 reserve/release/seq 接口 |
| `HnfSLCSF.cc` | 把 `lookup()` 拆成 L0–L4 段函数；把 `commitRead/writeLine/...` 拆成 U0–U3 段；实现 `tick()` |
| `HnfCoherencyController.{hh,cc}` | `Entry` 加 slcsf 流水字段；`DoSlcLookup` 等 action 改入队；`serviceInternalWork` 加响应消费 + `retrySlcsfIssuePending` |
| `HomeNodeFull.{hh,cc}` | `wakeup()` 先 `slcsf.tick()`；`hasLinkWork()` 加 `slcsf.hasWork()` |
| `HomeNodeFull.py` | 新增 §6 参数 |
| `SConscript` | 无新源文件（阶段 A）；阶段 7 拆 SimObject 时再加 |
| `HnfSLCSF.test.cc` | 见 §8 |

### 7.2 不变量断言（加到 `HnfSLCSF.cc`）

```cpp
// 每个 accepted request 恰好一个 terminal response
panic_if(acceptedCount != completedCount + replayCount + inflightCount(),
         "SLCSF response accounting broken");

// respVisible 里的响应 seq 必 > 任何 reqIngress 里的 seq（双缓冲）
panic_if(!respVisible.empty() && !reqIngress.empty() &&
         respVisible.front().seq >= reqIngress.front().seq,
         "SLCSF double-buffer ordering violated");

// replay 不递增 generation（在 replay 路径前后采样校验）
```

---

## 8. 单测与验证

### 8.1 现有 `HnfSLCSF.test.cc` 必须保持通过

现有 10 个用例都是**同步语义**测试（直接调 `lookup()`/`commitRead()`/...）。流水线化后这些方法不再存在。两种处理方式：

**方式 1（推荐）**：保留一个"同步后端" `HnfSLCSFBackend`，把现有 `lookup()/commitRead()/...` 实现整体搬过去；`HnfSLCSF` 流水线内部调 backend。现有测试改为测 backend，保证存储语义不回归。

**方式 2**：把测试改为"构造请求 → tick N 次 → 断言 respVisible"。更贴近最终行为，但工作量大。

阶段 1 先用方式 1 保底，阶段 5 再补方式 2 的流水线时序测试。

### 8.2 新增流水线时序测试（gtest，无需 event queue）

```cpp
TEST(HnfSlcSfPipelineTest, LookupHasConfiguredLatency) {
    HnfSLCSF m(64, 4, 2, 4, 2, /*seq=*/1,
               /*lookupLatency=*/Cycles(4), /*updateLatency=*/Cycles(3), ...);
    m.commitReadSync(...);            // 用 backend 预置
    auto r = m.tryEnqueue({.op=Lookup, ...});
    ASSERT_EQ(r, SlcSfEnqResult::Accepted);
    for (int i = 0; i < 3; ++i) { m.tick(); EXPECT_FALSE(m.responseAvailable()); }
    m.tick();                          // 第 4 拍
    ASSERT_TRUE(m.responseAvailable());
    EXPECT_TRUE(m.frontResponse().lookupResult.slcHit);
}

TEST(HnfSlcSfPipelineTest, NoZeroCycleLoop) {
    // 入队后同一 tick 不应产生响应
    m.tryEnqueue({.op=Lookup, ...});
    m.tick();
    EXPECT_FALSE(m.responseAvailable());
}

TEST(HnfSlcSfPipelineTest, StaleTokenReplaysWithoutWrite) {
    // lookup 拿 token → 中途 writeLine 改 generation → commit 校验失败 → replay，array 不变
}

TEST(HnfSlcSfPipelineTest, ReqQueueFullReturnsNoCredit) {
    // 灌满 reqQueue，再入队应返回 NoCredit
}
```

### 8.3 端到端验证

复用 `HNF_POCQ_SEQ_Litmus_Implementation_and_Debug_Case_Studies.md` 中的 litmus 用例，在 `HnfCoherencyController.test.cc` 里跑完整 POCQ 流程，断言：
- 功能不变（最终 SLC/SF 状态、snoop 序列、retire 顺序一致）。
- 时序被拉伸：lookup 路径多耗 `lookupLatency` 周期，commit 路径多耗 `updateLatency` 周期。

---

## 9. 分阶段实施计划

| 阶段 | 内容 | 验证 | 风险 |
| --- | --- | --- | --- |
| **1** | 抽 `HnfSLCSFBackend`，把现有 `lookup/commitRead/writeLine/...` 整体搬入；`HnfSLCSF` 改为持有 backend + 队列骨架，`tick()` 为空 | 现有 10 个测试改测 backend，全绿 | 低；纯重构 |
| **2** | 定义 `SlcSfReq/Resp`，实现 `tryEnqueue`/`responseAvailable`/`frontResponse`/`popResponse` + 双缓冲；`tick()` 只做 promote，无流水线 | 队列入队/出队单测 | 低 |
| **3** | 实现 Lookup Pipe（L0–L4），`lookupLatency` 参数化；`tick()` 推进 lookup 流水 | `LookupHasConfiguredLatency`、`NoZeroCycleLoop` 单测 | 中；切分 `lookup()` 要小心局部变量提升 |
| **4** | 改 CC：`DoSlcLookup` 入队、`serviceInternalWork` 消费响应、`retrySlcsfIssuePending`；`HomeNodeFull::wakeup` 加 `slcsf.tick()` | `HnfCoherencyController.test.cc` 全套 litmus 功能等价 | 中；POCQ 等待态时序要对 |
| **5** | 实现 Update Pipe（U0–U3）+ generation 校验；改 `CommitRead/UpdateSlcSf/Flush*/RemoveSharer/StoreWriteData` 入队 | `StaleTokenReplaysWithoutWrite` 单测；litmus | 高；commit token race 是新引入逻辑 |
| **6** | 放开 `maxInflight>1` + set lock 并行；`slcsf_enable_set_lock=True` | 并发不同 set 的 litmus | 中；set 锁正确性 |
| **7（可选）** | 按 Spec 迁移为独立 `SlcSnoopFilter : ClockedObject`，拆 `.py`/SConscript，CC 改持指针 | drain/checkpoint 测试 | 中；接口已稳定，主要是外壳搬迁 |

> MVP 建议固定 `maxInflight=1`、`lookupLatency=4`、`updateLatency=3`、`enable_set_lock=False`、`enable_gen_check=True`。功能跑通后再调时序与并行度。

---

## 10. 关键风险与对策

1. **POCQ 等待态死锁**：`DoSlcLookup` 入队失败（NoCredit）时 POCQ 停在 `slcLookup`，若 `retrySlcsfIssuePending` 漏调会永久卡死。对策：`serviceInternalWork` 每周期必调；`hasWork()` 把"有 entry 处于 issue-pending"算作 work。

2. **commit token 误判**：generation 校验是新增逻辑，若 `accessCounter` 在非破坏性操作（如 `findSlc` 更新 `lastUse`）时也递增，会误触发 StaleToken。对策：检查现有代码——`lastUse = ++accessCounter` 确实会在每次 probe 时递增 generation？**需审计**：当前 `generation` 与 `lastUse` 都用 `accessCounter`，probe hit 时 `lastUse++` 但 `generation` 不变（只有 `allocateSlc/allocateSf/installSlc` 时 `generation = ++accessCounter`）。校验应只比对 `generation`，不比对 `lastUse`。

3. **SEQ 路径时序**：`frontPendingSeq/markSeqIssued/completeSfEvict` 当前由 CC 的 seq pocq 同步驱动。流水线化后，`completeSfEvict`（写 SLC）若也走 update pipe，则 seq pocq 的 `SnoopDone` 事件要等响应。对策：阶段 5 一并改造 seq pocq 的 `completeSfEvict` 调用为入队；或先保留同步（seq 路径频率低，可接受 0-cycle）。

4. **`releaseSfResources` 时机**：原同步实现里 reserve/release 紧邻 commit 调用。流水线化后 reserve 仍在 `startReadFlow` 同步做，release 必须推迟到 update 响应回来。若 entry 在等响应期间被重试逻辑误回收会泄漏 reservation。对策：`entryAllocated` 已排除 `Sleep` 态，但 `WaitSlc`/`WaitSnoop` 态的 entry 在等 SLCSF 响应期间不能被 admit 复用——确认 `acceptLinkReq` 的 `entryAllocated` 检查覆盖这些态（已覆盖）。

5. **双缓冲队列容量**：`reqQueueEntries` 必须够大，否则 CC 频繁 NoCredit。建议 `>= num_poc_entries`，保证每个 entry 至少能挂一个 in-flight 请求。

---

## 11. 与 Spec 目标架构的对齐

本方案是 `HNF_SLCSF_Gem5_Spec.md` 的**阶段 1–4 子集**：

| Spec 章节 | 本方案对应 | 阶段 7 后 |
| --- | --- | --- |
| §1.5 同步/延迟接口划分 | §3.3 reserve/release 同步、lookup/update 异步 | 一致 |
| §1.6 双缓冲四规则 | §3.1–3.2 | 一致 |
| §4.3 CommitToken | §2.4 generation 校验 | 一致 |
| §6 SlcSfService | §2.2 流水段 + §3.2 `tick()` | 拆为独立 `SlcSfService` 类 |
| §5 SlcSnoopFilter 顶层 | 阶段 A 内嵌于 `HnfSLCSF` | 阶段 7 拆 `ClockedObject` |
| §8 VictimBuffer/SeqBuffer | 复用现有 SEQ；VictimBuffer 当前内嵌于 SLC 替换 | 阶段 7 拆独立类 |
| §11 PoCQ graph 对接 | §4 Issue/Wait 节点已存在于现有图 | 一致 |
| §16 不变量 | §7.2 断言 | 一致 |

阶段 7 迁移时，`HnfSLCSF` 的 `tick()/tryEnqueue/response*` 接口直接平移到 `SlcSnoopFilter`，CC 持有方式从按值改为指针（`cc.setSlcsf(&slcsf)` 已是指针，只需 `HomeNodeFull` 改持 `SlcSnoopFilter*` 子对象）。POCQ 侧零改动。

---

## 12. 速查：改动 Checklist

- [ ] `HnfCcTypes.hh`：`SlcSfOp/SlcSfReq/SlcSfResp/SlcSfStatus`；`HnfSlcLookupResult` +generation 字段
- [ ] `HnfSLCSF.hh/.cc`：抽 `HnfSLCSFBackend`；加队列/流水寄存器/`tick()`/`tryEnqueue`/response 接口
- [ ] `HnfSLCSF.test.cc`：现有用例改测 backend；新增 4 个流水线时序用例
- [ ] `HnfCoherencyController.hh`：`Entry` +slcsf 流水字段
- [ ] `HnfCoherencyController.cc`：`DoSlcLookup`/`CommitRead`/`CommitMaintenance`/`UpdateSlcSf`/`FlushSf`/`FlushL3`/`WriteL3FlushSf`/`RemoveSharer`/`StoreWriteData` 改入队；`serviceInternalWork` 加响应消费；新增 `retrySlcsfIssuePending`；`releaseSfResources` 移到响应处理
- [ ] `HnfPOCQStateGraph.cc`：必要时加 `slcUpdate → sleep`（StaleToken replay）边
- [ ] `HomeNodeFull.hh/.cc`：`wakeup()` 先 `slcsf.tick()`；`hasLinkWork()` 加 `slcsf.hasWork()`
- [ ] `HomeNodeFull.py`：§6 参数
- [ ] `SConscript`：阶段 7 前无改动
- [ ] 文档：本文件 + 更新 `HNF_SLCSF_Gem5_Spec.md` 的"当前实现状态"段落

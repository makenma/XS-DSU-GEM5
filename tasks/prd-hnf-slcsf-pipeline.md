# PRD: HNF SLCSF 流水线化与独立 ClockedObject 迁移

## 1. Introduction / Overview

当前 `HnfSLCSF` 是由 `HomeNodeFull` 按值持有、由 `HnfCoherencyController` 通过指针访问的普通 C++ 对象。它的 `lookup()`、`commitRead()`、`writeLine()`、`completeMaintenance()`、`removeSharer()`、`flush*()` 和 `completeSfEvict()` 等接口都会同步返回或同步修改 SLC/SF 状态。

最明显的问题位于 `HnfCoherencyController::executePocqAction(DoSlcLookup)`：控制器调用同步 `lookup()` 后，会在同一个 tick 内递归注入 `SlcLookupDone` 并再次推进 POCQ。该 0-cycle 组合路径无法表达 SLC tag、SF directory、策略决策和 update 的真实延迟，也无法安全支持多请求并行。

本功能要分阶段完成以下迁移：

1. 先将现有存储语义抽成可独立验证的同步后端。
2. 在当前 `HomeNodeFull` 事件源下加入请求/响应双缓冲和可配置的 lookup/update 流水线。
3. 将所有会产生 cache/directory 语义结果或修改持久状态的 CC/POCQ 操作改成异步请求/响应。
4. 加入 generation-based commit token、replay、SLC victim 直交 PoCQ、SEQ/FVB 生命周期和逐 set 并行控制。
5. 最终将顶层服务迁移成 `SlcSnoopFilter : ClockedObject` child SimObject，拥有独立时钟事件、初始化、drain、checkpoint 和统计能力。

最终职责边界为：POCQ/CC 拥有 CHI transaction lifetime、SLC dirty-victim writeback 和外部消息流程；SLCSF 独占 SLC/SF、SEQ、服务时序和资源冲突状态。SLCSF 不直接生成 CHI flit，也不在完成回调中直接推进 POCQ。

### 1.1 输入文档

- [流水线实现方案](../src/doc/CHI/HNF_SLCSF_Pipeline_Implementation_Plan.md)
- [SLCSF 目标架构 Spec](../src/doc/CHI/HNF_SLCSF_Gem5_Spec.md)
- [HNF 架构设计文档](../src/doc/CHI/HNF_Gem5_Architecture_Design_Document.md)
- [POCQ / SEQ Litmus 与调试案例](../src/doc/CHI/HNF_POCQ_SEQ_Litmus_Implementation_and_Debug_Case_Studies.md)

### 1.2 已确认的产品决策

- 范围覆盖实现方案阶段 1–7，包括最终独立 `ClockedObject` 迁移。
- 本文的“阶段 1–7”始终指流水线实现方案 §9 的阶段编号；它不等同于目标架构 Spec §15 的同名阶段编号。dirty SLC victim 已按 RTL 设计修订为 U1 exact capture 后直接交给 PoCQ；只有 SF victim 使用 SEQ 并 snoop。
- 保持默认配置、所有既有单元测试和两套四核 POCQ/SEQ litmus 的功能兼容；周期数允许因流水线化而变化。
- 对实现方案未定案处采用目标 Spec 的推荐做法；仍依赖具体代码或 gem5 分支能力的事项保留在 Open Questions。
- 最终接口采用 common header + typed payload 的 request/response 模型，避免先引入大而稀疏的请求结构、阶段 7 再二次重构。
- generation/token 校验是 correctness 要求，最终产品中不可关闭。
- 阶段 6 的 set lock 只覆盖 SLCSF 内部短期 service 操作，不允许跨 snoop、MC 或 requester 等外部等待长期持锁。
- 现有默认 geometry、LRU/victim 选择行为和功能结果保持不变；目标 Spec 中的 `sf_num_sets=2048`、RR policy 等示例值不替换当前默认值。
- 为控制改动面，新增组件继续位于 `GEM5/src/mem/cache/CHI/` 并沿用 `gem5::Chi` namespace；本功能不做整个 CHI 目录或 namespace 迁移。

### 1.3 当前回归基线

当前 checkout 的四个 CHI 单元测试目标合计 36 个用例并全部通过：

| 测试目标 | 当前用例数 |
| --- | ---: |
| `hnf_pocq_state_graph.test.opt` | 15 |
| `hnf_seq_pocq_state_graph.test.opt` | 2 |
| `hnf_slcsf.test.opt` | 10 |
| `hnf_coherency_controller.test.opt` | 9 |

流水线实现方案写有“现有 11 个 `HnfSLCSF` 用例”，但当前 `HnfSLCSF.test.cc` 实际只有 10 个。验收门槛定义为“当前全部 36 个用例继续通过，并且新增用例全部通过”，而不是把最终测试总数固定成 36 或假定存在第 11 个旧用例。

## 2. Goals

- 消除所有 PoCQ/CC ↔ SLCSF 的同步语义返回和同 tick 递归状态机推进。
- 保证任一 response 的 `visibleCycle` 严格晚于对应 request 的 `acceptedCycle`。
- 为 lookup、fill、update、victim、SF eviction 和 replay 提供可配置且至少为 1 个 SLCSF 周期的延迟。
- 保持现有 hit/miss、SLC data、SF owner/sharer、snoop target/opcode、SEQ、replay 和 retire 语义不变。
- 对每个 accepted request 产生且只产生一个 terminal response，不因队列反压而丢失、覆盖或重复。
- 使用完整 CommitToken 检测 lookup 与 commit 之间的 stale snapshot；stale replay 不得修改任何持久状态。
- 在覆盖 victim 前原子保存 dirty SLC victim 或 SF victim，并建立明确的 CC handoff/release 生命周期。
- 支持参数化多 in-flight：不同 set 可并行，同 set 的冲突访问必须串行。
- 将最终 `SlcSnoopFilter` 纳入 gem5 的 Python 配置、独立时钟事件、统计、drain 和 checkpoint 体系。
- 保持现有 `HomeNodeFull()` 默认实例化以及已有 `sf_num_sets`、`sf_num_ways`、`seq_entries` 等参数覆盖方式有效。

## 3. Delivery Sequence

每个阶段都必须保持可构建和可测试，不能依赖尚未提交的后续阶段才能恢复基线。

本文把实现方案阶段 1–6 合称“阶段 A（内嵌流水线）”，阶段 7 称为“阶段 B（独立 ClockedObject）”。

| 实施阶段 | 对应用户故事 | 阶段出口 |
| --- | --- | --- |
| 阶段 1：后端抽取 | US-001 | 同步存储语义独立，现有测试全绿 |
| 阶段 2：协议与队列 | US-002～US-003 | 双缓冲、credit 和 terminal-response contract 可测 |
| 阶段 3：Lookup Pipe | US-004 | lookup 延迟与无 0-cycle 行为可测 |
| 阶段 4：内嵌集成 | US-005～US-006 | HomeNode 驱动流水，CC 异步消费 lookup |
| 阶段 5：Update/Replay/Victim/SEQ | US-007～US-014 | 所有持久修改异步且 token-safe |
| 阶段 6：并行 | US-015 | 多 in-flight 与逐 set 互斥可测 |
| 阶段 7：独立 SimObject | US-016～US-020 | 独立 event、生命周期和统计完成 |
| 最终门禁 | US-021 | 构建、单测、两套 litmus 和文档全部通过 |

所有阶段出口都必须构建并运行四个 CHI gtest binary，保持当前 36 个既有用例全绿，并通过该阶段新增用例。阶段 4 额外运行默认四核 litmus；阶段 5、6、7 运行默认与 forced-SF/SEQ 两套 litmus。阶段 6 另以 `max_inflight > 1` 运行定向并行测试；兼容 litmus 仍使用默认 `max_inflight=1`，以便把功能回归与性能模式验证分开。

## 4. User Stories

### US-001: 抽取同步存储后端并冻结行为基线

**Description:** As an HNF maintainer, I want the current SLC/SF storage semantics isolated in an ordinary C++ backend so that timing changes can be developed without changing coherence behavior at the same time.

**Acceptance Criteria:**

- [ ] 将当前 SLC/SF line、replacement metadata、SEQ storage 和同步语义操作抽入普通 C++ backend；backend 不继承 `SimObject`，不持有 gem5 Event。
- [ ] 在 CC 尚未迁移前，保留过渡期同步 adapter/forwarder，使每个阶段的提交都可独立构建和运行。
- [ ] `lookup`、`commitRead`、`completeMaintenance`、`removeSharer`、`writeLine`、`flushSf`、`flushL3`、`writeL3FlushSf` 和 `completeSfEvict` 的既有结果不变。
- [ ] `HnfSLCSF.test.cc` 的全部 10 个既有用例改为直接验证 backend 或等价 test fixture，并全部通过。
- [ ] `HnfCoherencyController.test.cc` 的全部 9 个既有用例继续通过。
- [ ] 若 backend 拆出新的 `.cc`，同步更新 `SConscript` 的 production `Source` 和 `hnf_slcsf`/`hnf_coherency_controller` GTest 链接依赖。
- [ ] 本故事不引入可观察 latency、队列、credit 或 POCQ graph 行为变化。
- [ ] 受影响目标编译通过，`git diff --check` 通过。

### US-002: 定义稳定的异步请求、响应和 CommitToken 类型

**Description:** As an SLCSF integrator, I want every delayed operation represented by an owned, typed message so that requests can safely outlive the calling stack and survive internal refactoring.

**Acceptance Criteria:**

- [ ] 定义 SLCSF 自增且每个 operation 唯一的 `SlcSfReqId`；不得复用仅用于 LinkLayer 调试的 `Entry.seq` 作为异步操作 ID。
- [ ] request common header 至少携带 `reqId`、`pocEntryId`、line/address key、requester、CHI opcode、QoS 和 trace 信息。
- [ ] public typed payload 至少覆盖 `Lookup`、`Fill`、`Update` 和 `Evict`；`UpdateKind` 能表达现有 runtime mutation 和 `CompleteSfEvict`。`ReleaseDirtyVictim` 只保留为被拒绝的兼容编码。初始化由 child lifecycle 驱动，不伪装成无 POCQ owner 的 public request。
- [ ] response 至少携带 `reqId`、`pocEntryId`、operation kind、terminal status 和 operation-specific payload。
- [ ] terminal status 能区分 `Done`、`Replay` 和不可恢复 `Error`；Replay payload 包含 reason、绝对 `retryNotBeforeTick` 和 `redoLookup`。
- [ ] `CommitToken` 至少包含 lookup request ID、line、lookup epoch，以及 SLC/SF 各自的 hit、set、way、generation snapshot。
- [ ] request、response、token 和 in-flight state 不保存 SLC/SF entry、POCQ entry 或 data storage 的裸指针/引用。
- [ ] message 内的 cache line、mask、victim 和 snoop 信息具有明确所有权，不依赖调用者局部对象生命周期。
- [ ] 所有既有操作都有明确的 request/response 映射测试，受影响目标编译通过。

### US-003: 实现有界请求/响应双缓冲与注册 credit

**Description:** As a POCQ client, I want bounded current/next request and response queues so that admission and completion never form a same-cycle combinational loop.

**Acceptance Criteria:**

- [ ] 实现 `reqIngress`/`reqReady` 和 `respPending`/`respVisible` 四个逻辑队列；同一 wakeup 内只能按 pending→visible、ingress→ready 的方向 promote。
- [ ] `tryEnqueue()` 只取得 request 所有权并返回 `Accepted`、`NoCredit`、`Initializing` 或 `Draining`；不得执行 probe、policy 或 storage mutation。
- [ ] registered request credit 是前一 SLCSF 周期锁存的 snapshot，并正确计入 `reqIngress`、`reqReady`、in-flight 及 admission capacity，不能只统计 ready/in-flight。
- [ ] 每次 `tryEnqueue(Accepted)` 立即递减本周期的 visible credit；同一 CC wakeup 内连续调用不能重复使用同一个 registered snapshot 而超额接收。
- [ ] `req_queue_entries` 表示 ingress+ready+尚未产生 terminal response 的 in-flight request 的总逻辑上限；不得把每个物理双缓冲各自配置成 N 而把总容量意外放大为 2N。
- [ ] `resp_queue_entries` 表示 reserved+pending+visible response slot 的总逻辑上限；每个物理双缓冲共享同一容量 accounting。
- [ ] `req_queue_entries`、`resp_queue_entries`、`max_inflight` 和各 issue width 均必须大于 0，且 `max_inflight <= req_queue_entries`；非法组合在构造时 fail-fast。
- [ ] issue 前为 terminal response 预留 response slot；response queue 满时末级或 issue 端保持状态并 stall，不得 drop、overwrite 或产生重复 response。
- [ ] 每个 accepted request 恰好产生一个 terminal response；NoCredit/Initializing/Draining 拒绝不产生 response，也不修改持久状态。
- [ ] 双 pipe 可以乱序完成；response 必须用 `(pocEntryId, reqId)` 匹配，不依赖全局 FIFO 顺序。
- [ ] 新增并通过 `RequestNotIssuedInAcceptanceCycle`、`CompletedResponseNotVisibleUntilNextCycle`、`ReqQueueFullReturnsNoCreditWithoutMutation`、`AcceptedRequestProducesExactlyOneTerminalResponse` 和 `ResponseBackpressureDoesNotDropOrDuplicate` 测试。
- [ ] 新增同一 wakeup burst-enqueue 测试，证明第 N+1 次调用在 N 个可见 credit 已消费后返回 NoCredit。
- [ ] accounting 测试覆盖 ingress、ready、in-flight、pending、visible 和 consumed response 的边界状态。

### US-004: 实现单 in-flight Lookup Pipeline

**Description:** As a simulation user, I want SLC/SF lookup to have a configurable non-zero service latency so that hit/miss and snoop decisions are timing-visible.

**Acceptance Criteria:**

- [ ] 将现有 lookup 语义拆成或等价建模为 L0 Decode、L1 SLC probe、L2 SF probe、L3 policy 和 L4 response latch。
- [ ] L0 的 SEQ hit/SEQ full replay 可以短路后续语义计算，但仍必须经历至少一个 service cycle 和 response 双缓冲。
- [ ] lookup response 完整保留现有 hit/miss、state、data/dataDirty、MC decision、snoop mode/opcode/targets、RNF 信息，并新增 CommitToken。
- [ ] probe/replay 路径不修改 coherence state、tag、data、sharer、generation、SEQ 或 victim storage。
- [ ] successful lookup 通过显式 `recordAccess()` 或等价 commit point 更新现有 LRU `lastUse` 语义；Replay 不更新 replacement metadata，且 LRU 更新不改变 generation。
- [ ] `lookup_latency` 是 issue→internal completion 的 SLCSF 本地周期数，默认 4；配置值小于 1 时构造必须 fail-fast，service 内仍做最小 1 cycle 的防御性 clamp。
- [ ] 对 accepted cycle N、配置 latency L，固定 `issueCycle >= N+1`、`completeCycle >= issueCycle+L`、`visibleCycle >= completeCycle+1`；测试不得把 request sequence number 当作 cycle。
- [ ] 流水结构支持运行时配置的 latency；不得用与 Python runtime 参数冲突的固定编译期深度假设。
- [ ] 新增并通过 `LookupHasConfiguredLatency`、`NoZeroCycleLoop`、`SeqHitReplayHasNoSideEffects`、`ZeroLatencyConfigurationIsRejected` 及四种 SLC/SF hit/miss 组合测试。
- [ ] 增加 deterministic replacement-order 回归，证明每个成功、非 Replay lookup hit 恰好记录一次 access，victim 选择与同步基线一致。

### US-005: 在内嵌阶段接入参数与 HomeNode 调度

**Description:** As an HNF configuration author, I want the stage-A pipeline driven by the existing HomeNode clock so that asynchronous behavior can be integrated before the standalone SimObject migration.

**Acceptance Criteria:**

- [ ] `HomeNodeFull.py` 增加 lookup/fill/update/SF-evict/replay latency、request/response queue entries、issue width、`max_inflight` 和 set-lock 控制参数；旧 victim latency/capacity 参数为兼容 no-op。
- [ ] 阶段 A 的安全默认值为 lookup=4、fill=4、update=3、SF-evict=2、replay=2 cycles，request/response queue=8，所有 issue width=1，`max_inflight=1`；旧 victim=3/VictimBuffer=2 不参与时序或容量。
- [ ] `HomeNodeFull::wakeup()` 在 `linklayer.wakeup()` 之前推进一次内嵌 SLCSF；同一 HomeNode wakeup 不得推进两次。
- [ ] `hasLinkWork()`/等价调度条件覆盖 ingress、ready、in-flight、pending/visible response 和 CC issue-pending entry，确保没有外部 flit 时仍能完成已接收操作。
- [ ] 内部工作耗尽后不继续无意义自调度。
- [ ] 现有 `HomeNodeFull()` 默认配置无需调用方修改即可实例化。
- [ ] 新增 stage-A 调度 smoke test，证明一个无额外 RX flit 的 accepted lookup 最终产生 response。
- [ ] 文档明确此直接 `tick()` 驱动只属于阶段 A，并将在 US-016 中移除。

### US-006: 将 CC/POCQ Lookup 改为异步 Issue/Wait/Consume

**Description:** As a POCQ transaction, I want lookup issuance and completion separated by explicit wait state so that the transaction graph cannot consume a semantic result in the request cycle.

**Acceptance Criteria:**

- [ ] CC entry 保存当前 SLCSF `reqId`、lookup pending/issue-pending 状态、锁存的 lookup response 和 CommitToken；每个 main POCQ entry 同时最多一个 SLCSF operation outstanding。
- [ ] `DoSlcLookup` 只构造 request 并调用 `tryEnqueue()`；不得同步调用 backend lookup，也不得递归注入 `SlcLookupDone`。
- [ ] Accepted 后 graph 留在明确的 lookup wait 状态；NoCredit/Initializing 时 ownership 和 graph issue 状态不变，并由周期性 retry 最终重新尝试。
- [ ] CC 以参数化 budget 消费 visible response，并严格核对 `(pocEntryId, reqId)`；unexpected、duplicate 或 stale response 触发断言。
- [ ] response 消费只锁存结果和 future graph event；`SlcLookupDone` 的 graph transition 最早在后续 CC 周期发生。
- [ ] lookup hit data 和 dirty 信息在 transition 前完整复制到 CC entry。
- [ ] POCQ lookup 的 hit、miss、snoop、maintenance 和 replay 分支保持现有功能语义。
- [ ] controller tests 使用显式 pump/tick helper，不依赖同步调用。
- [ ] 新增并通过 `LookupNoCreditEventuallyProgresses`、`LookupResponseIsConsumedInLaterCcCycle` 和 `MismatchedLookupResponseIsRejected`。

### US-007: 实现 generation-based CommitToken 校验与 stale replay

**Description:** As a coherence transaction, I want a lookup snapshot validated at commit so that intervening mutations cannot commit against a stale way or directory entry.

**Acceptance Criteria:**

- [ ] SLC/SF 的 generation 只在 committed tag/state/data/directory mutation 时变化；普通 probe 或 replacement `lastUse` 更新不得改变 generation。
- [ ] Fill/Update/Evict 在任何持久修改前重新 probe line，并校验 token 的 line、hit/miss、set、way、generation 和 epoch。
- [ ] line 被替换后又分配回同一 way 的 ABA 场景必须使旧 token 失效。
- [ ] token validation 分别覆盖 tag/line 改变、way 被复用、hit↔miss 改变、generation 改变和 lookup epoch 失效。
- [ ] token 不匹配返回明确的 stale-token Replay，且 tag、data、state、owner、sharer、generation、SEQ、dirty-victim capture/seal 和 replacement state 均保持请求前值。
- [ ] CC 收到要求 `redoLookup` 的 replay 后清除旧 token、旧 lookup result 和本轮派生决策，等待 `replay_penalty` 后从 Lookup 重新开始。
- [ ] fresh re-lookup 取得的新 token 可以成功 commit，证明 replay 具备 liveness。
- [ ] generation 校验在最终配置中始终开启，不提供会破坏 correctness 的关闭模式。
- [ ] 新增并通过 `StaleTokenReplaysWithoutWrite`、`LookupTouchDoesNotInvalidateToken`、`EvictReallocateAbaInvalidatesOldToken` 和 `FreshLookupAfterStaleReplayCommits`。

### US-008: 实现 Fill/Update/Evict Service Pipeline

**Description:** As an SLCSF service, I want all persistent mutations executed through a staged pipeline so that writes have modeled latency and a single terminal completion point.

**Acceptance Criteria:**

- [ ] 实现 U0 Decode/Validate、U1 resource/victim preparation、U2 array write 和 U3 invariant check/response latch，或实现具有相同可观察阶段边界的 ready-tick service。
- [ ] `CommitRead`、memory fill、maintenance、remove sharer、write line、flush SF、flush L3、write-L3-and-flush-SF、SF-evict completion 和 dirty-victim release 都映射为 typed Fill/Update/Evict request。
- [ ] U0 token/replay 失败在任何 reservation 消耗或持久写入前终止。
- [ ] U1 在可能覆盖 victim 前完成所需 buffer/SEQ reservation；无法原子取得全部资源时返回 Replay 或保持 service stall，不能部分占有后继续。
- [ ] 持久状态只在 U2 commit point 修改；`checkLineInvariant()` 只在写入完成后运行。
- [ ] update/fill response 只在 U3 写入 `respPending`，并遵守与 lookup 相同的 egress 可见周期规则。
- [ ] 默认 fill latency=4、update latency=3，所有配置 latency 至少为 1。
- [ ] operation 只有在持有 terminal response reservation 后才能进入 U0/U1/U2；U3 将 reservation 原子转换成 `respPending` entry，因此 U2 mutation 后不得再因 response capacity stall。缺少 reservation 属于框架断言失败。
- [ ] visible response 长时间不消费时，新的 operation 在 U0 前 stall；已完成 response 不丢失，且任何 U2 mutation 不会重复执行。
- [ ] 为每种 operation kind 增加 parameterized service test，覆盖成功、replay、latency 和 response backpressure。

### US-009: 将 Read 与 Maintenance 的 POCQ Update 路径异步化

**Description:** As a requester transaction, I want read and maintenance responses emitted only after their SLCSF commit finishes so that external completion never races ahead of persistent state.

**Acceptance Criteria:**

- [ ] 修改实际 `POCQ_StateGraph` 拓扑，而不只替换 action body：read hit、snoop-with-data、memory fill 和 maintenance 都必须进入明确的 SLCSF update wait 状态。
- [ ] read 的 `CompData` 只能在对应 Fill/Update terminal `Done` response 后排队。
- [ ] maintenance 的 `Comp` 只能在对应 update terminal `Done` response 后排队。
- [ ] update NoCredit 留在 issue 状态重试；accepted update 在 response 前不得重复发送。
- [ ] stale/recoverable Replay 转到 replay wait 并重新 Lookup，不能只重发原 Fill/Update。
- [ ] SF/resource reservation 在 response 前保持有效，并在成功、Replay、Error 或取消的唯一清理点恰好释放一次。
- [ ] `serviceInternalWork()` 对 `stepPocq()` 的返回值进行处理，不得丢弃 retire 或其他 terminal side effect。
- [ ] 既有 `ReadUniqueSnoopsAllOtherSharers`、`ReadNoSnpReturnsWithoutAllocatingSlcSf`、`RetireWakesOneSameAddressSleeper` 和所有 read/maintenance state-graph tests 继续通过。
- [ ] 新增并通过 `ReadResponseWaitsForSlcSfCommit`、`MaintenanceResponseWaitsForSlcSfCommit` 和 `ReservationHeldUntilUpdateResponse`。

### US-010: 将 Write、Evict、Flush 与 retire 通道异步化

**Description:** As a write or eviction transaction, I want storage completion and POCQ retirement decoupled from the RX callback so that delayed updates return LinkLayer resources exactly once.

**Acceptance Criteria:**

- [ ] `RemoveSharer`、`StoreWriteData`/`writeLine`、`FlushSf`、`FlushL3` 和 `WriteL3FlushSf` 不再直接修改 backend，全部通过 service request/response。
- [ ] Evict 和所有 write graph 路径在 storage response 前保持 allocated，不发送过早 completion，也不 retire。
- [ ] 增加 CC→LinkLayer deferred retire queue/API 或等价机制，承接在 `serviceInternalWork()` 中异步产生的 retire；不得依赖原 `acceptRxDat()` 调用栈返回。
- [ ] LinkLayer token 对每个 transaction 恰好 retire 一次；deferred-retire work 纳入 `hasWork()` 和 wakeup 调度。
- [ ] update NoCredit 后 transaction 最终进展，且不会重复写入或重复 retire。
- [ ] flush service API 即使当前 graph 没有生产 transition，也必须具备异步单元测试；本故事不为不可达 action 擅自增加新的 CHI opcode 语义。
- [ ] `ReadNoSnp` 继续绕过 SLC/SF allocation/update。
- [ ] 既有 `EverySupportedTransactionCompletes` 继续验证 12/12 已支持 transaction 均完整退休且最终 `!cc.hasWork()`。
- [ ] 新增并通过 `WriteDoesNotRetireBeforeUpdateResponse`、`DeferredWriteRetiresTokenExactlyOnce`、`EvictWaitsForRemoveSharerResponse` 和 `UpdateNoCreditEventuallyProgresses`。

### US-011: 将 SF Victim / SEQ / FVB completion 纳入异步协议

**Description:** As the internal SEQ POCQ, I want SF victim handoff and completion represented by delayed messages so that reverse invalidation cannot synchronously mutate SLCSF state.

**Acceptance Criteria:**

- [ ] SF way 覆盖前先原子 reserve SEQ、复制完整 directory snapshot，再安装 replacement。
- [ ] SF victim 通过 response payload 暴露 `SeqId`/FVB snapshot；阶段 A 可暂留 registered getter adapter，但最终 production mutation 不依赖同步 `completeSfEvict()`。
- [ ] SEQ POCQ snoop 完成后发送 typed `CompleteSfEvict` update，并等待其 terminal response 后才 retire/release SEQ。
- [ ] 等待 update response 期间 dirty snoop data、owner、sharer、address 和 `SeqId` 均保持有效。
- [ ] SEQ full、reservation race 或 stale completion 产生无副作用 Replay/Error，不得覆盖原 SF victim。
- [ ] `SEQ occupancy + reserved slots <= seq_entries` 始终成立。
- [ ] 同地址 main POCQ 与 SEQ 不同时 commit；Sleep main waiter 不阻塞负责释放资源的 SEQ。
- [ ] 既有 SEQ back-invalidate、dirty-data preservation、reservation replay、main-address hazard 和 younger-Sleep-waiter 回归全部通过。
- [ ] 新增并通过 `SeqDoesNotRetireBeforeCompleteSfEvictResponse` 和 `SeqCompletionNoCreditEventuallyProgresses`。

### US-012: dirty SLC victim 直接交给 PoCQ

**Description:** As an HNF transaction, I want an M-state SLC victim transferred directly to PoCQ so that its latest data is preserved without an SLC-side VictimBuffer.

**Acceptance Criteria:**

- [ ] clean SLC victim 直接丢弃；`MU/MN` victim 不走 snoop，在 U1 排他窗口内抓取完整 tag/state/data/mask 并 seal 所选物理 slot。
- [ ] U2 用 exact capture/seal 校验后才覆盖旧 line；terminal response 携带稳定 `VictimId`、address、完整 data 和 dirty/state facts，不暴露 storage 指针。
- [ ] response 被 CC 消费后由 PoCQ 唯一持有 line，并直接生成 `WriteNoSnpFull`；SLCSF 不保留 victim entry。
- [ ] 不存在 VictimBuffer 容量、同 line owner 或 victim latency 引起的 stall/Replay；旧 `victim_buffer_entries` 和 `victim_latency` 参数允许为 0 且不参与建模。
- [ ] 尚未完成的前一个 PoCQ writeback 和相同 victim address 的再次替换均不阻塞后续 SLC 流水。
- [ ] SF victim 仍原子 reserve SEQ、保留 directory snapshot，并通过 snoop/`CompleteSfEvict` 完成。
- [ ] 新增并通过 `DirtyVictimCapturedBeforeOverwriteAndHandedDirectlyToPocq`、`PriorDirtyVictimHandoffDoesNotBlockNextReplacement` 和 `DirtyVictimHandoffPreservesData`。

### US-013: 完成 PoCQ dirty victim 的 SN writeback 与退休

**Description:** As the HNF controller, I want each directly handed-off dirty victim written to the system node by an independent PoCQ transaction so that requester completion and victim lifetime remain decoupled and lossless.

**Acceptance Criteria:**

- [ ] Fill/Update terminal response 携带 victim 后，CC 创建独立 PoCQ dirty-victim transaction，使用 `VictimId` 和独立 downstream TxnID 关联；它不复用或阻塞原 requester 的 POCQ token。
- [ ] dirty-victim transaction 向 `snNodeId` 发出 `WriteNoSnpFull` TXREQ 和完整 cache-line TXDAT，并在 TXDAT 被接受且 downstream terminal completion 到达前保持 allocated；TX backpressure 不丢失 request/data。
- [ ] 两个完成条件同时满足后直接从 PoCQ 退休，不向 SLCSF 发送 release；旧 `ReleaseDirtyVictim` request 返回 `InvalidRequest`。
- [ ] downstream retry 保留 PoCQ owner 和完整 line；不可恢复 error fail-stop，不能静默丢失唯一 dirty copy。
- [ ] 新增并通过 `DirtyVictimDataSurvivesWriteback`、`DirtyVictimTxBackpressureMakesProgress`、`DirtyVictimCompletionMatchesVictimId`、`DirtyVictimCompletionDoesNotConsumeSlcsfRequestCredit` 和 `ObsoleteDirtyVictimReleaseRequestIsRejected`。
- [ ] 新增强制小 SLC 的端到端测试，实际触发 dirty eviction，并验证 `WriteNoSnpFull` address/data、唯一 downstream completion、SLC victim occupancy 始终为 0 和原 requester 正常完成。

### US-014: 统一 admission、stall、reservation 与 replay 生命周期

**Description:** As a concurrency maintainer, I want one unambiguous resource model so that old long-lived SF reservations do not deadlock with new pipeline locks.

**Acceptance Criteria:**

- [ ] 明确区分 admission rejection、accepted service stall 和 correctness Replay；三者分别使用 retry issue、留在 ready queue、terminal Replay response。
- [ ] 现有跨 lookup→snoop/MC→commit 的 `sfReservationOwners` 长期 set 独占被移除或收敛为仅针对有界资源的 reservation；不得与阶段 6 set lock 叠加。
- [ ] lookup 返回后不持有 SLC/SF set/way lock；外部等待期间的正确性由 CommitToken 保证。
- [ ] 短期 reservation 只覆盖已 issue、尚未 complete 的内部操作、显式 response slot、SEQ capacity，或 dirty SLC victim 在 U1→U2 之间的瞬时 slot seal。
- [ ] 获取多个资源采用 all-or-nothing；失败路径不泄漏部分 lock/reservation。
- [ ] 成功、Replay、Error、drain 和断言前可恢复路径都有唯一、可测试的资源释放点。
- [ ] NoCredit 不改变 graph ownership、不生成 response；service stall 不生成 Replay；Replay 不修改持久状态。
- [ ] 新增并通过资源泄漏审计测试，以及 `ReservationsPreventSeqSlotOvercommit`、`SeqReservationReplayMakesProgress` 回归。

### US-015: 支持多 in-flight 和逐 set 并行

**Description:** As a performance-modeling user, I want independent sets processed concurrently while conflicting accesses serialize so that throughput can be modeled without sacrificing coherence correctness.

**Acceptance Criteria:**

- [ ] `max_inflight`、lookup/fill/update issue width 和各 service port 限制在运行时生效。
- [ ] SLC/SF set lock 或等价短期 reservation 覆盖冲突的 lookup/update commit window；同 set read/write 互斥，不同 set 可同时 in-flight。
- [ ] 同一 request 需要 SLC 与 SF 两类 lock 时采用 all-or-nothing acquisition，stall 重试后不遗留 lock。
- [ ] lock 在成功、Replay、Error 和 backpressure 完成路径均恰好释放一次。
- [ ] 不同 pipe 的 response 允许按 ready time 乱序，但 `(pocEntryId, reqId)` 匹配和每 entry 单 outstanding 约束保持正确。
- [ ] `max_inflight > 1` 时 set conflict protection 必须启用；配置不得允许不安全的多 in-flight 模式。
- [ ] 兼容默认仍为 `max_inflight=1`、各 issue width=1；并行通过显式配置开启。
- [ ] 新增并通过 `DifferentSetsCompleteConcurrently`、`SameSetOperationsSerialize`、`LookupAndUpdateSameSetMutuallyExclude`、`StalledLockAcquisitionLeaksNoLock` 和 `MaxInflightAndIssueWidthAreEnforced`。
- [ ] 新增交叉 SLC/SF set 获取与乱序完成测试，证明无 lock-order deadlock，且 response 投递到正确 reqId/entry。
- [ ] 并发测试结束后所有 queue、lock、reservation、dirty-victim capture/seal 和 SEQ occupancy 均回到预期值。

### US-016: 迁移为独立 SlcSnoopFilter ClockedObject

**Description:** As a gem5 configuration author, I want SLCSF represented by a child ClockedObject so that it owns its clock, event, parameters and lifecycle independently from HomeNode LinkLayer.

**Acceptance Criteria:**

- [ ] 新增 `SlcSnoopFilter : ClockedObject` C++ 类及 `SlcSnoopFilter.py`，并在 `GEM5/src/mem/cache/CHI/SConscript` 注册 SimObject、source、tests 和 debug flags。
- [ ] Python 配置创建 `HomeNodeFull` 的 child SLCSF；C++ `HomeNodeFull`/CC 保存配置生成的 child 指针，不再按值拥有计时顶层对象。
- [ ] child 按值持有普通 C++ storage、policy、SeqBuffer、queue、service 和 stats 组件；SLC victim 只存在于当前 request/response 的瞬时 capture；内部组件不各自变成 SimObject。
- [ ] child 默认继承 `HomeNodeFull` 的 `clk_domain`，但接口允许以后配置独立 clock domain。
- [ ] child 使用自身 event 推进；移除阶段 A 的 `HomeNodeFull::wakeup()` 直接 `tick()`，防止双推进。
- [ ] response 变为 visible 或 registered credit 从 0 变为非 0 后，只通过未来周期 wakeup notification 唤醒 CC/HomeNode；不得从 child callback 直接消费 response 或推进 POCQ。
- [ ] 正常 transaction 数据路径保持 POCQ/CC ↔ SLCSF；LinkLayer 不获得 SLCSF transaction API。
- [ ] 原 `HomeNodeFull()` 配置无需修改可启动。
- [ ] child `slcsf.*` 是唯一 runtime 参数真源；旧 `HomeNodeFull` geometry、SEQ、latency、queue、issue-width 和 max-inflight 参数仅作为 child 默认值的 Parent proxy/兼容入口；旧 victim latency/capacity 参数为 no-op，不在 C++ 中形成第二份有效配置状态。
- [ ] 只设置旧 parent 路径时，值必须传到 child；同时显式设置 parent 与 child 同一参数时，显式 child 值优先。该优先级写入参数说明和配置测试。
- [ ] `clk_domain` 的 canonical source 是 child；默认代理 `Parent.clk_domain`，显式 child clock domain 可覆盖默认值。
- [ ] standalone child 构建、instantiate 和独立调度 smoke tests 通过；`config.ini` 验证 geometry、SEQ、latency、queue、legacy victim no-op、issue width、max-inflight 和 clock-domain 的最终值。
- [ ] 至少一项测试使用非 1:1 的 CC/SLCSF clock period，证明 request/response 双缓冲和 future wakeup 不依赖同 tick event 执行顺序。

### US-017: 实现初始化与事件驱动调度

**Description:** As a simulator user, I want SLCSF initialization and wakeups modeled explicitly so that credits and progress are deterministic without permanent per-cycle polling.

**Acceptance Criteria:**

- [ ] cold `initState()` 清空 SLC、SF、SeqBuffer 和所有瞬时 dirty-victim capture/seal，并将 initialized 置为 false；checkpoint restore 不执行破坏性重置。
- [ ] `startup()` 启动至少 1 cycle、默认 16 cycles 的 abstract initialization；初始化完成前 registered credits=0，`tryEnqueue()` 返回 `Initializing`。
- [ ] init 完成通过已寄存的 `initialized()` 状态和 future wakeup notification 暴露；不向普通 response queue 注入没有 POCQ owner 的 `InitDone`，也不产生 same-cycle transaction result。
- [ ] child wakeup 固定顺序为 promote responses → promote requests → complete ready operations → issue ready operations → update registered credits → lifecycle check → schedule next wakeup。
- [ ] complete 必须先于 issue；配置错误的 0-cycle latency 也不能让新 issue 在同一次 wakeup complete。
- [ ] `calculateNextWakeup()` 选择 ingress/pending promotion、最早 ready tick、init 或 drain 所需的最早时间；完全空闲时不永久每周期 self-schedule。
- [ ] `ensureWakeup()` 不重复排同一 event，并能在出现更早工作时安全 reschedule。
- [ ] 新增并通过 `NoCreditsBeforeInitialization`、`InitializationCompletesAfterConfiguredLatency`、`IdleFilterDoesNotSelfWakeForever` 和 `NewEarlierWorkReschedulesWakeup`。

### US-018: 实现两阶段协调 Drain

**Description:** As a gem5 user, I want HomeNode, CC, and SLCSF to quiesce in a defined order so that drain completes without dropping work or deadlocking protocol completions.

**Acceptance Criteria:**

- [ ] drain 使用由 `HomeNodeFull` 协调的两阶段状态机，而不依赖 gem5 drain manager 遍历 SimObject 的先后顺序。
- [ ] 阶段 D1 `QuiesceUpstream` 只停止新的 RXREQ admission；RXDAT、RXRSP、credit/retry、TX arbitration、CC internal work、deferred retire 和既有 allocated entry 的 SLCSF enqueue 全部继续。child 收到 drain request 后只记录 `drainRequested`，此阶段仍接受这些既有 intent。
- [ ] 当 HomeNode/CC 证明没有新的 transaction admission，且所有已 allocated main/SEQ/victim entry 均不再可能产生新的 SLCSF request 时，显式通知 child 进入阶段 D2 `SealAndDrainChild`；此后 `tryEnqueue()` 才返回 `Draining`。
- [ ] D2 继续推进 child 已 accepted work，并允许 CC 消费 response、发送完成所需 TX、处理 RX completion 和 retire，直到 parent 与 child 同时 completely idle。
- [ ] 存在 ingress、ready、in-flight、pending/visible response、deferred retire、dirty-victim capture/seal、PoCQ writeback 或未完成 SEQ/FVB 时不得报告 `Drained`。
- [ ] drain 期间每个 accepted request 仍产生恰好一个 terminal response；不得因 `Draining` 返回造成已拥有 transaction 永久卡在 issue 状态。
- [ ] drain tests 分别从 ingress、ready、in-flight、respPending 和 respVisible 非空状态启动，并证明 CC 会继续消费 terminal response 直到完成。
- [ ] 完全 idle 后调用 `signalDrainDone()`；`drainResume()` 恢复 admission、重算 registered credits 并按需唤醒。
- [ ] 新增并通过 `DrainWaitsForAllAcceptedWork`、`DrainDoesNotLoseIssuePendingIntent`、`DrainKeepsProtocolCompletionsEnabled` 和 `DrainResumeRestoresAdmission`。

### US-019: 实现 drained checkpoint/restore

**Description:** As a gem5 user, I want persistent SLCSF state serialized only at a drained boundary so that restore does not need to recreate half-completed operations.

**Acceptance Criteria:**

- [ ] drained checkpoint 至少保存 init 状态、SLC tag/data/state/generation/replacement state、SF tag/owner/sharer/state/generation/replacement state、`accessCounter`/generation allocator、request/SEQ/victim-identity/reservation next-ID、epoch counters 和 SEQ metadata；历史 VictimBuffer 数组仅作为 canonical-empty 格式兼容字段。
- [ ] drained-only 模式不序列化 active queue、in-flight ready tick 或半完成 response reservation；serialize 前用断言验证这些 transient state 为空。
- [ ] restore 后不得重新 cold initialize、重复发送 init completion 或使 ID/generation 回退；对 checkpoint 前已有 line 的 lookup 结果、data、dirty、owner/sharer、replacement 次序和 generation 与 checkpoint 前一致。
- [ ] restore 后可以接收和完成新 transaction，且 ID 不与已序列化历史状态碰撞。
- [ ] 新增并通过 `CheckpointRequiresDrainedState`、`DrainedCheckpointRestoresSlcSfState`、`RestorePreservesReplacementAndNextIds` 和 `RestoredFilterAcceptsNewWork`。

### US-020: 增加统计、调试与框架不变量

**Description:** As a model developer, I want observable statistics and fail-fast invariants so that timing, replay and resource bugs can be localized without unbounded debug logs.

**Acceptance Criteria:**

- [ ] 统计至少覆盖 lookup/fill/update/evict 数量，SLC/SF 四种 hit/miss 组合，directed/broadcast snoop，clean/dirty/SF victim 和各 replay reason。
- [ ] 统计 request/response queue full cycles、queue occupancy、configured service latency、accepted-to-visible latency、NoCredit、service stall、in-flight occupancy 和 set-lock conflict。
- [ ] debug trace 可关联 `reqId`、POCQ entry、line、accepted/issue/complete/visible cycle、operation、status 和 replay reason。
- [ ] assertion 验证 `accepted = terminalProduced + outstandingWithoutTerminal`，且 terminal response 的 Done/Replay/Error 分类之和一致。
- [ ] assertion 验证 `visibleCycle > acceptedCycle`；不得使用 request `seq` 大小比较推断周期顺序。
- [ ] assertion 验证 replay 无持久 mutation、dirty line 有完整 data、dirty victim 覆盖前有 buffer reservation、SF victim 覆盖前有 SEQ reservation、commit 前 token 已验证。
- [ ] assertion 验证同一 set/line 无两个已提交 valid tag，response/request ID 不重复，queue/lock/reservation 不超容量。
- [ ] statistics 不写入 storage entry 的 correctness state，关闭 debug flag 不改变模型行为。
- [ ] 每个统计计数点定义在 accepted、issued、terminal-produced 或 consumed 中的唯一阶段，NoCredit/retry/replay 不得造成同一事件重复计数。
- [ ] 定向测试验证关键 counter 在 hit、NoCredit、stale replay、SEQ full 和不同-set 并行场景中精确增加。

### US-021: 完成全量回归与实现状态文档

**Description:** As an HNF maintainer, I want a reproducible final validation gate so that the timing refactor can be accepted without hiding functional regressions.

**Acceptance Criteria:**

- [ ] `scons build/RISCV/gem5.opt -j16` 成功。
- [ ] 四个 CHI gtest binary 均成功构建；当前 36 个既有用例全部通过，且本 PRD 新增的全部测试通过。
- [ ] 默认 SF 四核 litmus 的 7 项 workload 全部通过并输出 `CHI_LITMUS_PASS total_errors=0`。
- [ ] 强制 `SF=64 sets x 4 ways, SEQ=4` 四核 litmus 的 7 项 workload 全部通过并输出 `CHI_LITMUS_PASS total_errors=0`。
- [ ] 两套完整运行的 alias checksum 均为 `0x50000001f0`，pressure checksum 均为 `0x2800000000ffc000`，每个 `CHI_LITMUS_BAD count = 0`。
- [ ] forced run trace 出现 SEQ install、admit、internal snoop、complete 和 retire；无 HNF panic、CPU commit watchdog、死锁或 `abs-max-tick` 退出。
- [ ] 不把历史 exit tick 相等作为门禁，因为本功能有意改变时序。
- [ ] 现有默认和 forced regression 命令无需修改参数路径即可运行。
- [ ] 更新流水线实现方案中测试数、当前所有权、graph 改造范围、latency 口径、deferred retire 和阶段 7 文件/checkpoint 清单。
- [ ] 更新目标架构文档的“当前实现状态”，记录最终默认值、兼容参数和仅支持 drained checkpoint 的限制。
- [ ] `git diff --check` 通过；不提交 build log、m5out 或生成二进制。

## 5. Functional Requirements

### 5.1 接口与时序

- FR-1: 最终只有 `tryEnqueue()`、registered credit、visible response queue 访问和 `initialized()` 可以作为同步跨模块接口；同步接口不得返回 hit/miss 或执行持久 mutation。
- FR-2: Lookup、Fill、Update、Evict、victim/FVB handoff、Replay、Init completion 和 Error 必须通过延迟 response 或寄存状态暴露。
- FR-3: `tryEnqueue(Accepted)` 表示 SLCSF 已取得 request 所有权，不表示 operation 完成。
- FR-4: request 在 accepted cycle 只进入 ingress，最早下一 SLCSF cycle 才能 issue。
- FR-5: internal completion 在本 cycle 只进入 pending，最早下一 SLCSF cycle 才能 visible。
- FR-6: `configuredLatency` 定义为 issue→internal completion 的本地周期数；`visibleCycle >= acceptedCycle + 1 + configuredLatency + 1`。
- FR-7: 所有配置 latency 必须至少为 1；构造对零值 fail-fast，service 内使用至少 1 cycle 的防御性 effective latency，并由测试固定行为。
- FR-8: 每次 service wakeup 先 complete 已到时 operation，再 issue 新 operation。
- FR-9: response slot 必须在 issue 前预留；terminal stage backpressure 不得重复执行 mutation。
- FR-10: child 不得在 callback 中直接调用 CC response handler 或 `stepPocq()`；通知只能调度未来事件。

### 5.2 Storage 与提交正确性

- FR-11: SLCSF 是 SLC/SF、SeqBuffer 和相关 replacement/generation state 的唯一所有者；PoCQ 是已移交 dirty SLC victim 的唯一持有者。
- FR-12: storage probe 为只读语义；replacement access metadata 必须通过显式 commit/record-access 操作更新。
- FR-13: 每次 accepted request 使用独立 reqId；response 通过 reqId 与 POCQ entry 双重关联。
- FR-14: CommitToken 必须描述 lookup 时的 line、SLC/SF hit、set、way、generation、request ID 和 epoch，不得保存 entry 指针。
- FR-15: 每个持久 Fill/Update/Evict 在 commit 前验证 token；不匹配返回 redo-lookup Replay。
- FR-16: Replay 不得写 tag/data/state/sharer，不得覆盖 victim，不得递增 generation。
- FR-17: dirty SLC victim 覆盖前必须在 U1 完成 exact capture/seal，并随 terminal response 直接交给独立 PoCQ transaction 以 `WriteNoSnpFull` 写回 SN；不得 snoop 或回 SLCSF release。SF victim 覆盖前必须完整进入已 reserve 的 SEQ 并 snoop。
- FR-18: 同一 set/line 最多存在一个 committed valid SLC entry 和一个 committed valid SF entry。
- FR-19: dirty state 必须伴随完整 cache-line data。
- FR-20: `ReadNoSnp` 继续不分配或更新 SLC/SF。

### 5.3 Backpressure、资源与并行

- FR-21: admission rejection、service stall 和 correctness Replay 必须使用不同状态与计数。
- FR-22: registered credit 必须避免 ingress、ready 和未产生 terminal response 的 in-flight 总量超出 request admission capacity；response capacity 则统一计入 reservation、pending 和 visible slot。
- FR-23: 每个 accepted request 恰好一个 terminal response；outstanding accounting 包含所有队列和 service 阶段。
- FR-24: response 可以跨 pipe 乱序，但同一 POCQ entry 不允许同时存在两个未完成 SLCSF request。
- FR-25: set lock 只覆盖内部 issue→complete 的必要窗口，不跨外部 CHI 等待长期持有。
- FR-26: 多资源获取必须 all-or-nothing；任何 stall/replay/error 都不得泄漏 lock 或 reservation。
- FR-27: `max_inflight` 和各类 issue width 必须受参数约束；不同 set 可并行，同 set 冲突操作必须串行。
- FR-28: `max_inflight > 1` 时不得关闭 set conflict protection。
- FR-29: SEQ 和 response reservation 的 occupancy 均不得超过配置容量；dirty-victim capture/seal 数必须与当前 in-flight U1/U2 owner 精确对应。

### 5.4 CC / POCQ / LinkLayer 集成

- FR-30: 每个 delayed operation 在 graph 中必须有明确的 Issue、Wait 和 response-driven continuation；不能把 enqueue 与后续外部响应/retire action 放在同一 transition。
- FR-31: lookup response 先锁存，最早后续 CC cycle 才注入 `SlcLookupDone`；follow-up update 仍需通过 ingress。
- FR-32: NoCredit/Initializing 保持 issue ownership 并周期重试；accepted request 在 terminal response 前不得重发。
- FR-33: stale replay 清除本轮 token、lookup result 和派生 victim/snoop/MC decision，从 lookup 重新开始。
- FR-34: SF/resource reservation 直到相关 terminal response 才释放，并且恰好释放一次。
- FR-35: 异步完成产生的 retire 通过 deferred CC→LinkLayer 通道传递，LinkLayer token 恰好回收一次。
- FR-36: SEQ POCQ 在 `CompleteSfEvict` response 前不 retire，且同址 main POCQ 不越过未完成 SEQ commit。
- FR-37: SLCSF 不直接生成 TXSNP、TXREQ、TXDAT 或 TXRSP；所有外部 CHI action 仍由 CC/LinkLayer 执行。

### 5.5 配置、事件与生命周期

- FR-38: 阶段 A 使用 HomeNode 单事件源且每周期恰好推进一次 SLCSF；阶段 7 完成后只能由 child event 推进。
- FR-39: 最终 `SlcSnoopFilter` 是 `HomeNodeFull` child `ClockedObject`，默认共享 clock domain，内部算法/存储组件保持普通 C++ 对象。
- FR-40: 初始化完成前不暴露 request credit；初始化、ingress/pending promotion、completion 和 drain work 都能触发准确的下一次 wakeup。
- FR-41: 完全空闲的 child 不永久每周期自唤醒。
- FR-42: child 参数是唯一 runtime 真源；旧 `HomeNodeFull` geometry/SEQ/timing/queue/width 参数和 forced-litmus `-P` 路径作为兼容默认映射到 child，显式 child override 优先。
- FR-43: drain 必须执行 `QuiesceUpstream`→`SealAndDrainChild` 两阶段协议；D1 仅阻止新 RXREQ 并继续所有 completion 流量，D2 才拒绝新 child request，parent 与 child 未同时 completely idle 不得 checkpoint。
- FR-44: drained checkpoint 保存全部持久 SLC/SF 和 ID/version state；restore 后不重新 cold initialize。
- FR-45: 统计与断言覆盖 timing、queue、resource、replay、victim、SEQ、token 和 response accounting。

## 6. Non-Goals (Out of Scope)

- 不实现 Atomic、LockedRMW、DVM、Stash、DCT 或完整 CMO/Persist 支持。
- 不修复低 `num_txns` 下既有的 TxnID 跨 channel 快速复用限制。
- 不改变 Cache2ChiBridge transient snoop/copyback 协议或建立新的 L1/L2 ownership-transfer handshake。
- 不将 POCQ transaction graph、CHI flit 生成或 Memory Controller 访问职责移入 SLCSF。
- 不把 `SlcArray`、`SfDirectory`、SeqBuffer 或 service 分别实现成 SimObject。
- 不提供 active/non-drained pipeline checkpoint；本 PRD 只要求 drained checkpoint。
- 不要求与 RTL 微架构逐拍一致；只保证本文定义的粗粒度 service latency 和队列时序。
- 不要求历史整机 exit tick 不变，也不以固定 wall-clock speedup 作为 correctness 门禁。
- 不改变现有 SLC/SF 默认 geometry、LRU/replacement policy 或 victim 选择结果；新增 policy 参数不得暗中改变默认行为。
- 不在本功能中整体搬迁 `mem/cache/CHI` 目录、重命名 `gem5::Chi` namespace 或清理无关 CHI 代码。
- 不把当前单 active SEQ POCQ 扩展为多个并行 SEQ transaction；SEQ storage 可保存多个 victim，但控制器并行度保持现状。
- 不仅为了让现有不可达 `Flush*` action 可达而新增未经 spec 定义的 CHI transaction 路径。
- 不将 RNF mask 从当前最多 64 个节点扩展为动态 bitset。

## 7. Design and Technical Considerations

### 7.1 最终模块边界

```text
HomeNodeFull
├── HomeLinkLayer
├── HnfCoherencyController / POCQ
└── SlcSnoopFilter : ClockedObject
    ├── bounded req/resp queues
    ├── SlcSfService
    ├── SLC storage
    ├── SF directory
    ├── SeqBuffer
    ├── policy
    └── stats
```

生产路径上，CC 只能提交 intent 并消费 delayed facts/decisions；测试可以直接调用 ordinary backend，以验证 storage/policy 语义。

### 7.2 时序口径

以下时间均按 SLCSF 本地 clock cycle 记录：

```text
acceptedCycle = N
issueCycle    >= N + 1
completeCycle >= issueCycle + configuredLatency
visibleCycle  >= completeCycle + 1
PoCQ transition cycle > visibleCycle
```

因此配置 `lookup_latency=4` 不表示“enqueue 后第 4 tick response 已 visible”。它表示 operation issue 后 4 个 SLCSF 周期内部完成，外加 ingress 和 response-visible 边界。测试必须记录 cycle stamp 验证该定义。

当 CC 与 SLCSF 不是 1:1 clock period 时，以上 N 表示 SLCSF local-edge index；request 在接受时刻之后的第一个 SLCSF edge 才能 promote/issue。跨时钟断言使用绝对 gem5 Tick，并始终满足 `visibleTick > acceptedTick`。

队列与 credit 使用以下统一 accounting：

```text
reqOutstanding = reqIngress + reqReady + inflightWithoutTerminal
respOccupied   = reservedResponseSlots + respPending + respVisible

reqOutstanding <= req_queue_entries
respOccupied   <= resp_queue_entries
acceptedTotal  = terminalProducedTotal + reqOutstanding
```

每个 SLCSF wakeup 锁存 `visibleReqCredits = req_queue_entries - reqOutstanding`；本周期每次 Accepted 立即递减该值，credit 只能在后续 SLCSF wakeup 重新增加。operation complete 时，将其预留的 response slot 原子转换为 pending slot，`respOccupied` 不变。

### 7.3 推荐默认参数

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `lookup_latency` | 4 cycles | issue 到 lookup internal completion |
| `fill_latency` | 4 cycles | issue 到 fill internal completion |
| `update_latency` | 3 cycles | issue 到 update internal completion |
| `victim_latency` | 3 cycles | dirty victim extraction |
| `sf_evict_latency` | 2 cycles | SF victim/FVB preparation |
| `replay_penalty` | 2 cycles | CC full re-lookup 前等待 |
| `init_latency` | 16 cycles | abstract cold initialization |
| `req_queue_entries` | 8 | 必须大于 0 |
| `resp_queue_entries` | 8 | 必须大于 0，并支持 response reservation |
| `victim_buffer_entries` | 2 | Dirty SLC victim entries |
| `max_inflight` | 1 | 兼容、安全默认；阶段 6 后可显式调大 |
| 各 issue width | 1 | lookup/fill/update 独立限制 |
| response consume width | 1 | CC 每周期默认消费数，可参数化 |

`generation check` 永远开启。若保留 `enable_set_lock` 过渡参数，则 `max_inflight > 1 && !enable_set_lock` 必须配置失败；最终实现可直接在并行模式强制启用冲突保护。

现有 `slc_num_sets=1024`、`slc_num_ways=16`、`sf_num_sets=1024`、`sf_num_ways=16`、`seq_entries=8` 和现有 LRU/replacement 结果保持默认。child 参数的默认值通过 Parent proxy 继承旧 parent 参数；显式 `slcsf.*` override 优先，C++ 只读取 child 中解析后的最终值。

`latencyFor(operation)` 使用以下固定映射，所有相加项均以 child SLCSF clock cycles 计：

| Operation / condition | Internal service latency |
| --- | --- |
| `Lookup` 正常完成 | `lookup_latency` |
| `Fill`，无 victim | `fill_latency` |
| `Fill`，产生 dirty SLC victim | `fill_latency + victim_latency` |
| `Fill`/`Update`，产生 SF victim | 对应 base latency `+ sf_evict_latency` |
| 同时产生 dirty SLC 与 SF victim | base latency `+ victim_latency + sf_evict_latency` |
| 普通 `Update`、`Evict`、`CompleteSfEvict` | `update_latency` |
| L0/U0 提前判定的 Replay | 1 child cycle 后 internal completion |
| cold initialization | `init_latency` |

`replay_penalty` 的配置单位是 child SLCSF cycles，但它不是 service pipeline latency。child 在 Replay payload 中给出绝对 `retryNotBeforeTick`；CC 在不早于该 Tick 的首个 CC clock edge 才允许重新发 Lookup。这样异频配置不需要由 CC 猜测 child cycle 单位。

### 7.4 迁移约束

- 阶段 1 的同步 adapter 是过渡兼容层，最终 production CC 不得通过它取得语义结果。
- 阶段 A 的 `HomeNodeFull::wakeup() -> slcsf.tick()` 是临时驱动；child ClockedObject 落地后必须删除，不能同时保留两种推进源。
- 现有 `sfReservationOwners` 混合了长生命周期 set ownership 与 SEQ capacity reservation。最终实现必须拆开：set/way correctness 使用短期 lock + CommitToken，SEQ/Victim/response 容量使用显式 reservation。
- 当前 graph 的 read hit、snoop read、maintenance、Evict 和 Write 都把同步 mutation 与后续 action 放在同一 transition；这些 topology 必须真实改造。仅把 action body 改成 enqueue 会导致过早 response/retire。
- 异步 write 的 completion 已脱离 `acceptRxDat()` 调用栈，必须使用 deferred retire queue 或等价机制把 retire info 送回 LinkLayer。
- 当前 checkout 未提供目标 Spec 示例中的 `MemberEventWrapper`，因此本实现使用现有 `EventFunctionWrapper`；若未来分支切换 wrapper，cycle-stamped 可观察时序不得改变。

### 7.5 验证命令

以下命令从 workspace 根目录执行：

```bash
cd GEM5
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

两套完整四核命令沿用输入文档《POCQ / SEQ Litmus 与调试案例》§9.2 和 §9.3；尤其保留原 `system.home_node[0].sf_num_sets/sf_num_ways/seq_entries` forced override 路径。

## 8. Success Metrics

- 所有 semantic request 的 `visibleCycle > acceptedCycle`，cycle-stamped 单测 100% 通过。
- 所有 accepted request 均有且仅有一个 terminal response；压力测试结束后 outstanding、lock 和 reservation 数为 0。
- 默认 latency、最小 latency、NoCredit、response backpressure 和 stale-token replay 的定向测试全部通过。
- 多 in-flight 配置下，不同 set 测试观察到重叠 in-flight window；同 set lookup/update 从不重叠 commit critical section。
- 当前 36 个 CHI 单元测试保持通过，所有新增测试通过。
- 默认与 forced-SF/SEQ 四核 litmus 均 7/7，绝对 checksum 与基线一致，且无 panic、watchdog、deadlock 或 max-tick 退出。
- dirty SLC victim 和 SF victim 均能完成 reserve→handoff→external action→release 生命周期，数据无丢失。
- drained checkpoint/restore 后，对已有 line 的 lookup/data/directory/version 结果一致，并可继续完成新事务。
- 默认配置和现有 forced regression 参数路径继续工作；配置文件能证明值传递到 child。
- 默认 geometry、replacement/victim 选择结果与流水线化前一致。
- SLCSF 完全空闲时不持续产生无工作 wakeup。

## 9. Open Questions

1. 当前不可达的 `FlushSf`、`FlushL3`、`WriteL3FlushSf` actions 是否将在后续 CHI opcode 工作中接入 production graph？本 PRD 只要求其 service API 异步且有单测，不新增未定义的协议入口。

# Dummy AI Core Gate 6 Coding Spec

实施目标：在既有 Agent、Mesh Core、Dynamic MoE 和 weight cache 上完成真实 Serving、continuous batching、prefill/decode/PUBLISH、persistent KV 与 shared-member completion；通过 Gate 6 累计验收。本文是交给实现 agent 的开发合同，不代表所列能力已经存在或已允许提交。

本文规定实施边界、组件责任、依赖顺序和可执行证据。wire 布局、枚举值、公式、排序和状态转移以主合同与生成器为唯一来源；引用处的全部限定条件仍适用。实施时使用本文件导航到源定义，不另建简化版协议。

## 1. 范围、权威与交付

### 1.1 权威索引

| 内容 | 唯一来源 |
|---|---|
| Gate 边界与 mandatory 分句 | [主合同](DUMMY_AI_CORE_AGENT_CODEX_SPEC.md) §17.3–17.9、§18 Gate 6 |
| Serving/profile/Host I/O/PUBLISH | 主合同 §10.1–10.3.1 |
| KV admission、claim、pin、append、snapshot、eviction、release | 主合同 §10.4 |
| session/protocol/cancel/CQ/metadata | 主合同 §8、§9.1、§9.2.1、§9.6 |
| frozen batch、MemberSlice、weight/cache/overlay/error reducer | 主合同 §7.2–7.10、§8.7、§10.6；[Gate 5 Coding Spec](DUMMY_AI_CORE_GATE5_CODING_SPEC.md) |
| concrete Mesh IR、DMA、SRAM、fence | 主合同 §4–6；[Mesh IR 合同](TORCH_EXPORT_MESH_IR_CODEX_SPEC.md)；[AXI/Garnet 合同](AXI_GARNET_CODEX_SPEC.md) |
| Mesh ABI | [mesh_ir_abi.yaml](../../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml) 及 generate_abi.py |
| Agent ABI/detail/disposition | [agent_protocol_abi.yaml](../../../util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml) 及 generate_agent_abi.py |
| exact 配置、capacity、事实、守恒、退出 | 主合同 §12–15、§17.9；现有 [schemas](../../../schemas/ai_mesh/) |
| case 归属与执行登记 | [mandatory manifest](../../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml)、[acceptance.py](../../../util/mesh_ir/mesh_ir/acceptance.py) |
| 现有运行命令 | [测试索引](../../../docs/ai_mesh/mesh_ir_test_commands.md) |

遇到正文不同位置的宽泛描述与专门条款时，先按适用范围核实。例如同 layer exact alias 去重不能覆盖跨 layer occurrence 独立 ownership 条款；instance-owned drain 与独立后台 fill terminal 不是同一边界。确实冲突时在实施计划记录双方锚点并请求决策，不靠修改 oracle 或增加 fallback 消除冲突。

### 1.2 Gate 选择集合

当前 manifest 为 Gate 6 新增 30 个 logical ID：`MOE-24`、`PROTO-12/23/25`、`HOST-15/25`、`SERV-1..21`、`E2E-D/E/H`。Gate 5 累计 118，Gate 6 累计 **148**；执行集合始终由 `earliest_gate<=6` 派生。subcase 数不冻结，按 mandatory 每个分句实际展开。

必须交付：

- Serving required feature、六份 conditional section、exact request/instance profile compiler、C++/Python reader/verifier。
- SQ 接纳到真实 Mesh 多 instance 执行，再到单次 GENERATE terminal/CQ 的异步链。
- canonical phase scheduler、冻结 shared batch、immutable member/weight/KV bindings 与 bounded backpressure。
- fixed-slot SessionKvManager、真实 KV DMA、append/instance join、repair reuse/re-prefill、RELEASE 与 generation tombstone。
- 真实 PUBLISH fill/store、可证明的 committed output prefix、shared cancel/error fanout。
- 独立 serving/KV/protocol/traffic oracle、有效篡改负例和 E2E-D/E/H。

FULL_TIMING 为本 Gate 验收模式，FULL 基线使用主合同规定的 `decode_chunk_tokens=1`；其他 chunk profile 的验证须显式记录粒度和对应执行范围。Gate 7 的完整 12-user 压力与 E2E-F/G、新的长任务性能结论、Gate 8 的 FAST_EVENT/WINDOWED_TIMING 切换不在交付范围；不补四个历史双 lane E1 点。不新增 CPU Mesh、第二套 Garnet、真实 UCIe、tokenizer、sampler 或 reference numerical compute。已有不支持的配置继续明确拒绝。

### 1.3 工作树与依赖边界

允许从当前 Gate 5 工作树推进，不要求先开始另一个 Gate。第一阶段记录实际 HEAD、dirty/index 状态和构建对象对应源码；保留他人修改及 `.tmp/` 证据。Gate 5 依赖的修复沿用原逻辑项，不移动 earliest_gate，也不记成 Gate 6 新功能。

接入 shared serving 前必须具备这些可执行前提：

- cache shadow 按所有 core/layer 的实际 occurrence 预留 subscriber/token/pin，任一不足无部分提交；物理 fill 去重，跨 layer ownership 保持独立。
- reservation 首次建立的 identity 在 RESOURCE_WAIT/retry 中不变；有限等待确实阻止不具备资源的 batch start。
- cached 权重真实参与 SRAM read 和计算内容；COPY_THROUGH 保留唯一输入；compute 时间不缩窄回绕。
- 健康后台 fill 按真实 terminal 完成，独立 fill ownership 与 batch subscriber/error drain 各自可对账。

这些修复可与 profile ABI、纯 scheduler/KV 模型并行推进；依赖项未闭合前只报告局部进度，不宣布 shared serving 或累计 Gate 6 完成。

## 2. 当前接缝与统一架构

### 2.1 先读的代码

| 现有入口 | 实施动作 |
|---|---|
| [NpuRequestExecutor](../../dev/ai_mesh/npu_request_executor.hh)、[Frontend](../../dev/ai_mesh/npu_serving_frontend.hh) | 现有 accept 返回 serviceNs，probe/full-context surrogate 不是 Mesh completion。改成可表达异步接纳、运行、取消和终态的共同生命周期，复用同一 SQ/CQ 前端 |
| [Frontend control](../../dev/ai_mesh/npu_serving_frontend_control.cc)、[plan output](../../dev/ai_mesh/npu_serving_frontend_plan.cc) | 将 prompt/output payload 的读取写入责任交给所选 Mesh profile；保留 Frontend control/metadata/CQ 顺序，不对同一 payload 再执行 surrogate DMA |
| [SessionRecordTable](../../dev/ai_mesh/session_record_table.hh) | 当前只有 tuple/generation，不是 KV manager。整合为唯一 session 生命周期所有者，删除重复 session lookup/create/release 路径 |
| [MeshDispatcher](../../dev/ai_mesh/mesh_dispatcher.hh)、[MeshProgramLoader](../../dev/ai_mesh/mesh_program_loader.hh) | 由自启动固定序列扩展为按 concrete profile/bindings 提交并异步通知 terminal；原测试入口成为共同接口调用方 |
| [MeshDummyCore](../../dev/ai_mesh/mesh_dummy_core.hh)、[scoreboard](../../dev/ai_mesh/program_scoreboard.hh)、[runtime key](../../dev/ai_mesh/runtime_key.hh) | phase/instance 共用 decode、admit、engine、commit、drain；完整 generation/key 防旧事件污染 |
| [MoE runtime](../../dev/ai_mesh/mesh_moe_runtime.hh)、[cache](../../dev/ai_mesh/mesh_weight_cache.hh) | 使用 serving 冻结的成员与 batch tables；不新增第二套 materializer、cache 或 SRAM 服务模型 |
| [DMA](../../dev/ai_mesh/dma_types.hh)、[AXI engine](../../dev/ai_mesh/axi_tensor_dma_engine.hh)、[peer aperture](../../dev/ai_mesh/peer_sram_aperture.hh) | 统一 logical descriptor owner，扩展 KV accounting 与 PUBLISH segment issue 约束；继续共用 AXI/Garnet/credit/response 路径 |
| [Agent workload](../../dev/ai_mesh/agent_workload_manager.hh)、[plan codec](../../dev/ai_mesh/agent_plan_codec.hh)、[object table](../../dev/ai_mesh/agent_object_table.hh) | real serving 的 wire/profile/metadata 和既有 repair FSM、对象 ref、auto RELEASE 接线，不更改冻结业务 outcome |
| [agent_config.py](../../../util/mesh_ir/mesh_ir/agent_config.py)、[agent_planning.py](../../../util/mesh_ir/mesh_ir/agent_planning.py)、[EffectiveArchitecture](../../../util/mesh_ir/mesh_ir/effective.py) | 扩展既有 exact 配置/CapacityPlan/identity 派生，所有硬件覆盖参与有效配置 digest |
| [registry](../../../configs/example/ai_mesh/dummy_core_case_registry.py)、[selector](../../../tests/gem5/ai_mesh/run_manifest_selector.py) | 显式接入 GATE6 backend、真实 case 与统一 artifacts；不复制整套 runner/selector |

### 2.2 单一写入口与有限所有权

下表是职责，不要求为每行新增类或文件。优先重构已有所有者，形成少量深封装接口；不要创建只有一次转发、没有生命周期价值的 helper。

| 所有者 | 唯一可变状态 / 输出 |
|---|---|
| Program/profile registry | immutable decoded program、exact selector 索引、binding/descriptor oracle、静态容量证明 |
| RequestContext table | 一个 GENERATE 的 phase 进度、immutable request identity、embedded rollback/append/terminal snapshot、唯一 CQ obligation 关联 |
| SessionKvManager | record/slot bitmap/claim/pin/release waiter/tombstone、KV epoch、append/accounting commit；其他模块不得直接改这些字段 |
| ServingBatchScheduler | READY snapshot、phase_ready_tick、wait deadline、finite candidate queue、唯一 freeze commit |
| BatchContext | frozen member order/profile/rank vector/QoS、member/weight/interval tables、cache reservation ownership、failure record 关联 |
| Dispatcher | instance generation、all-core arm/start、instance-owned work drain 与 InstanceKvCompletionJoin |
| DMA/PUBLISH accounting | descriptor accept/segment terminal、per-member prefix、KV descriptor outstanding、错误和 suppress 分类 |
| InstanceFailureReducer | 全局候选集合的唯一 first error、BatchFailureRecord、对 live member 的 status/detail/source 派生 |
| Frontend completion path | 消费 immutable request terminal 结果，写 metadata/fence/CQ/MSI；不再执行 Mesh output payload store |

所有表在 load 时由 CapacityPlan 定长；runtime queue full 返回背压，不扩容、不覆盖、不重新分配 identity。一个 core 同时最多一个 active instance不限制系统只存在一个 BatchContext：已冻结待 core 的 batch、cache subscriber 和 KV waiter 必须可共存。

普通 callback 只递交带完整 owner 的事实/结果，由对应合法 edge 的唯一提交点改变状态。不要把 `now`、容器顺序或 host wall time 变成语义 ID、调度 tie-break 或 first-error authority。

### 2.3 异步执行接口

真实 serving accept 只表示已接纳或背压/拒绝，不携带虚构完成 timer。执行器必须能通知 phase/instance terminal、request terminal 和 drain；cancel/release 使用同一 RequestContext/obligation。

Frontend 可以保留 probe 与 full-context surrogate 作为明确的执行器实现，用于 Gate 3/4 行为保护；transport、CQ ledger、control 状态机只有一份。删除真实 serving 路径中的 `serviceNs→terminal`、Frontend prompt pull、Frontend outputPayload 写入旁路。semantic digest codec 可复用，但它是 PUBLISH 数据源，不是执行完成证明。

所有 serving 产物显式报告真实执行 backend、FULL_TIMING、KV 开启状态及实际 phase/instance 数；Gate 4 Agent-only subset 仍如实标为 surrogate，不能计入 E2E-E。

## 3. ABI、profile compiler 与加载期证明

### 3.1 生成器先行

按主合同 §10.3.1 在 Mesh ABI YAML 注册 AGENT_SERVING required feature 和六个 conditional sections；record 大小、ID 位宽、flags、reserved、zero-role、runtime-bound fill sentinel 与 byte offsets 全部走生成器。保留未知 bit/section/enum、缺失/重复 section、feature-section 不一致的拒绝。

同时支持 serving-only program 与 serving+Dynamic MoE program；不得以启用 serving 为由把 MoE feature 默认为开启。Agent detail/disposition 复用 Agent ABI 已定义项，新增项先核对主合同和已有值，不自行找空号。

compiler、Python reader、C++ reader/verifier 使用同一 wire schema，语义 verifier 分别独立执行。新增 schema 先明确唯一源和对应 artifact，再接 exact parser；禁止 loose dict、缺字段默认成功或一套 JSON 旁路 binary verifier。

### 3.2 Exact profile 编译与选择

实现主合同 §10.3.1 全部 request/instance/source-map/member-binding/requirement/PUBLISH-binding 记录及 reachable closure。loader 必须证明：

- request program/profile 与 instance profile 各自 namespace/位宽正确；u32 instance ID 不经 u16 中间变量。
- requested_profile_key 的 zero-key base projection 与最终含真实 key 的 semantic projection按原公式分离，不形成自引用。
- 所有 reachable path 的 singleton PREFILL、每个 DECODE chunk（含尾 chunk）、PUBLISH 都存在；没有“最近 bucket”或运行时 descriptor 修补。
- concrete selector 包含完整 logical_source_rank_vector；member ordinal、rank 与 source-core map exact。重复 rank 只允许显式 profile 且 member symbols 独立。
- REQUEST_BINDABLE / INSTANCE_MEMBER_SLOT 互斥、完整覆盖，phase zero/nonzero 矩阵及跨 profile symbol role 一致。
- compiler 与 loader 从普通 descriptor 独立重算 Host input/output、KV read/write 的 interval union；hole、overlap、±1、错误 role/owner、padding 越过 Host valid range均拒绝。
- PUBLISH 专用 fill allocation/producer/event/store DAG 唯一且完整，KV/MoE/Host input 为合同要求的空集合。

不能只验证命令含一个 WEIGHT 或 OUTPUT view：还须验证所选 profile 的 identity、几何、访问方向、完整 operand 集合、exact ranges 与引用内容。

### 3.3 Preflight 的时机

program/config load：验证 ABI、拓扑/partition/address map、profile closure、所有静态 capacity bound、KV layout/bytes-per-token 一致性和 arithmetic overflow。

request preflight：以 WorkloadPlan、SQ、parameter、binding、TLV 和 selected request profile 做 exact 校验，完成全部可能 path 的合法性证明，然后才进入 session admission。普通 profile/binding 错误此时零 cache reservation、零 core start、零 payload DMA、SESSION_ADMITTED=0。

session admission 之后：KV policy 错误依主合同保留 admitted claim 并 terminalize；此前已由 preflight 证明的不变量再被破坏属于 infrastructure fatal。不得通过 CQ status 猜测 SESSION_ADMITTED，必须来自唯一 admission commit。

### 3.4 必须先落地的 golden

六个 section 的 exact bytes、feature 开关组合、reserved/enum±1、u32 profile ID 边界、profile key 双 projection、rank `[0,1]`/`[1,0]`/显式 `[0,0]`、跨 member symbol、全路径 Host/KV bytes oracle、末尾 partial chunk、PUBLISH chunk/4 KiB 切分。

任何输入/输出/token/byte/flag 的负例均记录失败阶段、固定 detail/disposition、是否 admitted 和零副作用账本；不能只断言“抛了一个异常”。

## 4. Request、phase 与 completion 生命周期

一个 GENERATE 对应一个 CQ obligation；内部 PREFILL/DECODE/PUBLISH instance 不是新的 SQ request，也不创建额外 GENERATE CQ。

按主合同 §10.2 实现恰一次 PREFILL、精确 DECODE 序列和恰一次 PUBLISH。每次 phase start验证连续 token cursor、exact profile、KV-before 与新 append；phase SUCCESS 只从 Dispatcher/KV join 的真实完成产生。

同一 request 在完整生成期间持有 KV owner；不能在 instance 间隙放掉 pin，也不能在 compile/test 期间继续持 pin。最后 PUBLISH 完成后仍需 KV terminal snapshot与 owner release，之后 Frontend 才能锁存 request terminal、提交 metadata/CQ。

正常终态、cancel、prestart policy error、shared fault、completion-path fatal分别使用已有 disposition，不把所有失败变成 PROGRAM_ERROR。CANCEL 与 target 双 CQ 的 Host join、early completion、合法 Host edge 推进保留 Gate 4 实现及反例。

## 5. Continuous batching 与 shared bindings

### 5.1 Scheduler

实现主合同 §10.3 的唯一 policy 与比较器，Python/C++ 从同一输入事件序列独立产生决定：

- 区分 pre-pin `kv_admission_ready_tick` 与 READY phase 的 `phase_ready_tick`，等待/重试不能刷新年龄。
- quantum 只看前一 edge 已 ready 的 snapshot；按合同 phase 优先级、aged prefill、head 顺序和连续 homogeneous prefix 选择。
- 先确定最高优先 phase再应用 batch wait；不得绕过 incompatible head 或正在等待的高优先 phase吸收低优先请求。
- exact profile member-count 集合与 token/sequence cap 共同约束 freeze；PUBLISH 不等 batch_wait。缺合法 selector 的错误范围按合同，不能整队报错。
- 只有 freeze commit 才分配 batch/instance/route/reservation identity；未选择到的 request留在原队列且保持年龄。
- effective batch QoS 从全部 frozen members计算；deadline head 的 QoS不能替代 batch QoS。

有限队列容量、满 active sequence、长未来 timer与真实 deadlock分别处理。仅反复检查 pending 不算进度；watchdog 知道合法未来事件，不允许 `progressed=true` 永久掩盖资源互锁。

### 5.2 冻结与 all-or-none arm

冻结成员顺序后，依次构造并冻结以下表，所有存储先 all-or-none 预留：

| 表 | 验证重点 |
|---|---|
| PerInstanceMemberBindingTable | ordinal→request→phase role，input/output/KV 的 immutable resolved view，singleton 也走同一路径 |
| BatchWeightBindingTable | 所有 member 的 immutable weight binding一致；source 在 cold obligation 创建时冻结，后续 attach 不改写 |
| FrozenBatchIntervalTable | Host input/output/metadata、每个 session owned slot span、weight ranges 的 checked overlap/alias 审计 |
| RuntimeFillBinding | PUBLISH producer 与本 member full semantic output digest，实例内有效且不能串 generation |

KV slot span 用于 ownership 审计，不代表 PUBLISH 可以创建 KV operand。任何 admitted-state 表不一致走正确 fatal/cleanup，不能装半张表或回退到第一个 member 的 binding。

Dispatcher 在选中 concrete profile、全部成员 binding/append/cache 资源可用后原子 arm/start。任何 core 未准备好时不得部分 start；成功 start 反馈必须包含真实 participating cores与 instance generation。

### 5.3 Dynamic MoE 接入

Serving 提供唯一 frozen population、MemberSlice、rank vector、batch QoS 和 immutable weight bindings；继续使用 Gate 5 provider/capacity/materializer/cache/executor。每个 reachable layer 每次 invocation只 materialize一次。

真实 Serving 在 freeze 后调用 production provider/capacity/materializer，生成本 batch 的有界 overlay。启动时加载的 immutable provider/profile 数据可复用；Gate 5 的预生成 overlay 镜像只作为固定测试入口与 golden，不能按 case 名或 batch ID查表充当运行期 materialization。若当前 C++ 只有图加载/执行、尚缺实际 materializer，则在既有组件边界补齐并用 Python 独立 oracle对照，不把 fixture builder 的结果当成实现已经存在的证据。

普通 Scheduled command 不改 shape/descriptor；只有 Dynamic MoE ABI 指定的有界区域允许绑定 expert M。profile padding、expert PAD 和真实 member token 身份分别审计，不能把 padding 当 SemanticTokenUid或污染输出。

同 core 可串行执行 instance，但有限候选 batch 可并存并共享 cold fill subscription。实现 crossing-batch RESOURCE_WAIT、同 tag attach、prestart abort、shared fill fault 的真实资源场景，禁止把所有 batch强制全局串行来绕开 E2E-H。

strict replay 与 normal scheduling 共用 production 状态机。strict 使用一次初始 cache/KV 状态连续演化；normal 下 batching/cache decision 受 timing影响时按合同分栏归因，不能强迫所有 materialization digest一致。

## 6. KV 生命周期

### 6.1 Record、slot 与 admission

按主合同 §10.4 实现 fixed-slot 几何、lowest-free slot、typed tuple/generation、kv_semantic_contract_digest、tagged states及各个有限表。record/tombstone 容量与物理 slot 数独立；ERROR/EVICTED record 不因没有 slot而消失。

唯一 `session_admission_commit` 建立有 owner 的 claim 和 SESSION_ADMITTED；claim→pin原子转移，不允许两者并存或只置 bit。post-admission policy error也必须有 claim terminal snapshot，不能形成 admitted-no-owner。

KV edge 按合同固定相位批量提交：前一 edge DMA/fault/append、terminal snapshot/owner release、release/eviction、width-1 canonical head admission。same-edge callback排列不能改变 slot/victim、first error或 claim/pin赢家；队首不可跳过。

### 6.2 Rollback authority 与 snapshots

| 对象 | 必须证明的边界 |
|---|---|
| KvAdmissionClaim | admission 到 claim→pin 或 claim terminal，保存完整 prior snapshot；INITIAL prior_absent 精确 tagged defaults |
| KvAdmissionRollbackState | claim→pin 时移动到 RequestContext；首 PREFILL 的真实 core-start commit 才清除；后续 DECODE/PUBLISH prestart fault 不恢复旧状态 |
| KvRequestPin | 覆盖完整 GENERATE 与 shared cancel drain；计数等于完整 owner set，错误/重复 acquire/release 为 invariant fatal |
| RuntimeKvViewSnapshot | 每次 PREFILL/DECODE arm 从前一 KV edge状态创建，冻结 slot、contract、epoch、valid prefix；append 不扩张当前 instance 的 read window |
| KvTerminalSnapshot | 在最后 pin/claim 释放的原子提交内生成，后续 eviction/RELEASE 不改变结果；Frontend不得再读 mutable record推导 terminal |

REPREFILL prestart abort恢复原 EVICTED snapshot与 epoch；INITIAL prestart abort留下合同要求的无 slot ERROR record；KV_REUSE恢复旧 RESIDENT。首 PREFILL 已 start 后的 later fault保留已提交 prefix并转 ERROR，不执行初始 rollback。

### 6.3 真实 DMA 与 append

KV 读写只走选中 concrete profile 的 Scheduled DMA_LOAD/DMA_STORE 与现有 NPU AXI/Garnet。Frontend不能直接复制 KV bytes、按 token count直接增长 cached_tokens，或用一次 timer代替读写。

每个 RequestContext 内嵌唯一有界 append obligation；bitmap 上限由 reachable profile证明。所有成员 arm前检查可用并冻结 base/new token、interval union和 descriptor owner。

outstanding_kv_dma 统计被接受而未在 KV edge完成 terminal accounting的 logical descriptor；不按 burst计、不把重试重复计入、不在最后 B callback直接提前减掉。

乱序 B/commit必须按完整 token bitmap计算连续有效 prefix；partial token及其后物理 bytes不可读。success、post-freeze cancel、data/program fault按合同分别更新 prefix/ERROR，不能把普通 cancel当 KV fault。

InstanceKvCompletionJoin 同时等待 core work drain和所有 append 的 KV-edge terminal commit；core HALT/INSTANCE_DONE不能单独推进下一 DECODE或 CQ。KV local error仅决定 prefix/诊断，业务 CQ来源仍是全局 BatchFailureRecord。

### 6.4 Eviction 与 RELEASE

eviction只选无 claim/pin/DMA/append/release_pending 的合法记录，按合同 ERROR/RESIDENT、admission epoch与tuple tie-break。EVICTING跨 edge提交后 slot才能复用；V1 discard 不产生隐式 writeback。

RELEASE 的 release-pending阻止新 claim，但不得阻塞已有 owner继续 claim→pin或执行。waiter等完整 predicate 后清 slot/record、递增 generation、写独立 tombstone，再完成自己的 CQ；duplicate/full为BUSY且不覆盖旧 owner。

compile/test 期间无 KV owner但 session可保留；Host repair命中时只读取 delta suffix，被驱逐时按 policy走完整 REPREFILL。Host始终提供 full context，不需要探测或获知物理 slot。

### 6.5 Persistence 与恢复边界

实现合同要求的 persistent session/tombstone/epoch/contract manifest与全局 quiescent snapshot校验；恢复时重算所有 geometry/digest/owner关系，旧 generation仍必须 STALE。

对 claim→pin rollback state等 live状态提供完整可验证的保存/恢复表示和组件 round-trip，不能只恢复计数。实际 gem5 in-flight checkpoint若上游不支持，明确拒绝并登记能力限制；不能用 quiescent replay冒充 live checkpoint。Gate 8 的混合保真切换和长任务恢复另行交付。

## 7. PUBLISH、Host 可见性与输出 ownership

最后 DECODE 后冻结完整 semantic output digest；PUBLISH 是独立 concrete Mesh instance，使用本 instance 的唯一 surrogate-source allocation与显式 DMA_FILL producer。输出块公式、absolute offset/tail规则引用主合同 §10.3.1；不得复用前一 DECODE 的临时 allocation，或按 descriptor重启数据流。

语义 digest 继续遵循主合同 §9.3，以完整业务上下文为输入。KV_REUSE/REPREFILL、decode chunk 与硬件 timing只改变允许变化的执行/流量投影，不能把 delta buffer digest误当完整上下文摘要；同一语义请求的最终 digest和输出字节必须保持合同要求的一致性。

每个 member 的输出 descriptor保持独立 owner。普通 DMA引擎接入由 loader重建的 burst-segment offset chain：同 member前一 matching OKAY B后才发下一 segment，其他 member仍可并行。不能以 descriptor整体成功代替每个 burst的 committed-prefix accounting。

取消与错误按不同边界处理：

- CANCEL 以 descriptor首 segment是否已 issue为边界；已开始 descriptor继续按序 drain到末尾，尚未开始的后续 descriptor suppress。
- started descriptor自身 error按合同停止未 issue余段；无 success event，不把已接受错误 bytes记为 committed。
- instance-global fault传播到其他 member时执行其规定的 error-no-issue/drain，而不是继续整个输出；前一 edge已 tombstoned member保留 cancel处置。

committed prefix由唯一 segment账本派生，metadata/CQ/AgentObject/traffic使用同一值。不同 chunk size、单 descriptor跨 4 KiB、cancel中间 segment都不能产生 hole、重复 payload或悬挂等待。

PUBLISH output payload仅 Mesh DMA_STORE写；Frontend只写metadata并复用原 fence/CQ/MSI顺序。Host通过既有 target/local backing visibility读取，禁止 callback偷读 RequestContext。compile取得 generated-code valid-range引用并持有 allocation；allocation尾部和消费者未释放对象不可复用，Host local-I/O只计一次。

## 8. Shared CANCEL、fault 与终态

按主合同 §7.8、§8.7 实现三层结果：member preflight拒绝、shared instance data/program fault、protocol/completion infrastructure fatal。边界不可通过统一 catch-all混在一起。

freeze前 CANCEL移除或终结正确的等待 owner；freeze后只给成员建立 tombstone，不改成员、route/capacity、shared activation/weight/KV执行计划。所有 member cancel也必须完成 frozen shared work的合法 drain；成员输出各自 suppress/drain。

InstanceFailureReducer收齐同 edge原始候选，再按 typed full source key选择全局赢家；KV local first不能覆盖它。每 batch至多一个 BatchFailureRecord，fanout保持每个 live member恰一个 CQ；此前 terminal/tombstone不回退。

尤其覆盖一个 cold fill被两个 batch订阅：单 member cancel不取消物理 fill；真实 fill失败扇出到全部相关 batch，健康 fill不能被合成故障。所有发生在最后 success、new cancel、KV B、command fault和 reservation release同 edge的排列必须稳定。

completion path保持既有 exactly-once和 fatal-retained ledger。CQ/MSI/ACK故障不能倒退已经成功完成的 KV session，也不能假定损坏 completion path还能发普通 error CQ。

## 9. Oracle、facts 与可执行覆盖

### 9.1 独立期望

新增建议入口 `mesh_ir.gate6_contract`、`mesh_ir.serving_oracle`、`mesh_ir.kv_oracle`、`gate6_acceptance.py`、`tests/gem5/ai_mesh/gate6/runtime_contract.py`；可按职责合理合并，不能复制 Gate 4/5 全套框架。

oracle 从 immutable plan/program/architecture/bindings与显式输入事件重算：

- phase序列、exact selector/rank vector、freeze成员、phase priority、batch wait与 effective QoS；
- KV claim/pin/path、slot/victim/epoch、append interval/bitmap、terminal/release/tombstone；
- profile descriptor的 Host/KV ranges、cached/streamed weight、local SRAM service与 output block/prefix；
- member/shared-instance/physical-fill 三种 ownership下的 command、DMA、CQ和流量守恒。

能静态推导的期望不能读取 runtime outcome反推；受时间影响的决定使用明确输入事实和合同相位，不能把 actual route/cache/bytes复制成 expected。Python模型与C++ golden必须比较同一小例的完整输出，防止各自写不同期望却都通过。

### 9.2 生命周期事实

事实记录在唯一状态提交入口，带完整 request/batch/instance/tuple/generation/typed source身份及有定义的 edge phase。必要边界包括 preflight、session admission、claim→pin、first-PREFILL start、freeze/arm、KV descriptor accept/terminal、append commit、owner release/terminal snapshot、PUBLISH segment、record teardown/tombstone和CQ提交。

按实际观测阶段校验前置事实存在性：已有 commit/terminal必须有合法前因；未接受的 safe rollback不要求 NPU acceptance；合法 cutoff/fatal前缀允许未发生的后续事件。禁止 missing=未来 sentinel或通过最终 winner/status修补事实链。

必须从真实产物删除/移动/改写上述边界、member/rank/slot/epoch/source、KV bitmap、PUBLISH prefix、traffic分类，证明 schema/oracle明确失败；正确前缀同时必须通过。

### 9.3 流量与退出

分别报告 Host local-I/O、prompt full/delta、generated output、metadata/control、KV read/write/invalid prefix、weight logical demand/physical fill、activation、MoE local/remote、AXI payload/overhead与per-link数据。所有数字从已接受/已提交/已丢弃的实际账本与独立 descriptor oracle对账，不把 cache fill等同weight read或把KV折进activation。

使用既有 summary/JUnit/RunManifest/invariants/traffic与terminal-class框架。扩展 schema必须 exact且来源唯一；记录选中 program/profile、有效配置、backend、输入/manifest digest、实际执行身份和 persistent资源。

全局 quiescence遵循主合同 §15.6：control、batch、cache、KV、Host、Mesh、AXI/Garnet全部owner与future event清空；允许persistent session/cache必须无live owner且在manifest声明。连续合法网络edge确认，不以所有用户business done或所有core HALT替代。

## 10. Mandatory 证据矩阵

下表指定证据组织；完整语义仍以主合同对应编号为准。一个 logical ID含多个分句时拆为真实 subcase，不以一条 happy path代替。

| Logical ID | 必须具备的证据 |
|---|---|
| SERV-1/2 | INITIAL→精确 DECODE尾块→PUBLISH 的真实实例链；多 instance仅一个 GENERATE CQ |
| SERV-3/4 | scheduler独立golden与真实有限队列；优先phase、aged prefill、head连续prefix、deadline±1、same-edge新ready、全满背压 |
| SERV-5/6/7 | repair delta hit、Host compile/test期间驻留无pin、压力驱逐后full REPREFILL；真实Host/KV字节对账 |
| SERV-8/9 | pinned/claimed/DMA session不可逐出；lowest slot、ERROR优先、LRU/tuple ties与callback排列 |
| SERV-10 | prompt/output/activation/weight/KV/local-I/O/control分类，logical/physical守恒 |
| SERV-11 | request key、完整instance selector/rank vector、u32 profile ID、交换rank/显式重复rank、零/多匹配 |
| SERV-12/20 | concrete profile padding与MoE PAD隔离；Host valid range、member output和semantic UID负例 |
| SERV-13 | stable split；所有 byte/token/chunk/TLV±1、缺profile、range hole/overlap、zero payload preflight |
| SERV-14/15 | 两个以上真实请求共享instance，三类member binding隔离；冻结相同replay population时arrival shuffle对照 |
| SERV-16 | repair与连续多instance；KV/weight持久、generation/event/relocation无泄漏 |
| SERV-17 | output只有Mesh store、metadata只有Frontend；延迟output/metadata B及Host local visibility |
| SERV-18 | KV状态与owner全矩阵、容量bound-1、claim→pin rollback、immutable view、乱序append、terminal snapshot、eviction/release/tombstone、contract mismatch |
| SERV-19 | requirement/member-slot互斥、phase role矩阵、symbol跨profile role、member overlap±1、admitted前后错误边界 |
| SERV-21 | PUBLISH producer ABI/DAG、绝对offset block/tail、chunk/4KiB重切、segment cancel/error、Host有效范围引用与local-I/O |
| MOE-24 | 来自真实Serving的shared population、capacity/route按member切分、完整batch tables与真实Mesh execution |
| PROTO-12 | frozen shared member cancel、partial output、target唯一CQ，其他member route/output不变 |
| PROTO-23 | RELEASE全部status、active owner下waiter、duplicate/full BUSY、generation tombstone和无隐式retry |
| PROTO-25 | 三opcode exact字段/identity/QoS/binding/TLV非法矩阵，拒绝发生在规定边界 |
| HOST-15 | 有限4/6/4与足够大有限pool对照，冻结业务identity；token-local selection不变，histogram/replay与batch/cache差异归因 |
| HOST-25 | success/error/cancel/standalone/join/repair-limit/auto RELEASE唯一FSM终点；下一task等待release SUCCESS |
| E2E-D | 单Agent经真实PREFILL/DECODE/PUBLISH、output/metadata/CQ/MSI再compile success，单NPU Garnet |
| E2E-E | 三用户 first-pass/compile-repair/test-repair，至少resident reuse与eviction/re-prefill两臂；FULL真实KV，不计surrogate subset |
| E2E-H | 多member共享instance，另一个batch attach同cold weight；shared command/weight-fill fault、all-member-cancel、一次failure record/每member一次CQ及完整资源drain |

## 11. 不可省略的反例族

| 反例族 | 最小可复现设置 / 判据 |
|---|---|
| Profile binding | 两member不同input/output/KV；交换rank、跨role symbol、1 B overlap、缺primary或PUBLISH producer；精确失败阶段与零payload |
| Scheduler | 三类phase同quantum、边沿新ready、持续decode+aged prefill、incompatible head夹同key后项、等待deadline边界、非head最高QoS |
| Admission/rollback | 两waiter抢一个slot；release/eviction/claim/pin同edge；cancel在claim前/后/转pin后；REPREFILL首start前与later-DECODE fault对照 |
| Terminal snapshot | pin release后同edge eviction/RELEASE，CQ仍使用冻结prefix/contract/status；删除snapshot事实必须拒绝 |
| KV append | 两member、多logical descriptor、多burst乱序；末token部分成功、后段先成功；旧arm snapshot晚到read不得读新prefix |
| Global fault winner | command PROGRAM_ERROR与KV AXI_ERROR同tick，两组反向source-key与callback shuffle；全局CQ赢家及KV本地diagnostic各自稳定 |
| PUBLISH cancel | 三segment descriptor首B后cancel，剩余segment继续drain、下一descriptor suppress；exact committed prefix与无悬挂event |
| PUBLISH shared error | A故障、B处于多segment中段；prior tombstone/new cancel分别验证，不生成错误success event |
| Cache ownership | 同core多layer alias、subscriber/token/pin bound-1、两个crossing batch浅资源、稳定reservation ID、健康fill与故障subscriber分离 |
| Host cleanup | compile持valid-range引用、output/metadata延迟；error/cancel不compile；auto RELEASE成功/失败/rollback/fatal与下一task因果 |
| Canonical artifacts | 三次同配置、strict replay timing A/B、正常batch变化对照、事实/traffic/bitmap/owner真实产物篡改 |

每族需组件 exact golden；涉及 protocol/DMA/资源竞争的必须补生产 GEM5臂。模型级通过不能关闭生产缺口，源码关键字存在不能算 capability。测试失败先保留原反例再修根因，不能通过改名、放宽expected或增加异常兜底清绿。

## 12. 实施顺序与阶段出口

先在 `.tmp/docs/gate6-implementation-plan.md` 写文件/责任图、依赖、每项mandatory断言及定向命令。遵循实际会话已获授权；缺少实施授权时按仓库规则确认具体方案，已有授权不重复询问。本文不授权 commit/push。

| 阶段 | 可审查交付 | 阶段出口 |
|---|---|---|
| R0：接缝与依赖 | 工作树/基线清点；Gate 5 occurrence容量/ownership/等待identity修复；异步executor、dispatcher、唯一session ownership设计 | 原反例实测；共享路径定向回归；不引入第二套协议/执行器状态机 |
| R1：ABI/profile | 六section、compiler/双reader/verifier、keys、exact selectors、Host/KV oracle、capacity | 跨语言bytes/semantic golden与完整负例；正常Serving尚未执行时也能fail closed |
| R2：KV admission核心 | typed record/slot/claim/pin/rollback/terminal snapshot/eviction/release、状态校验 | edge permutation与容量边界；Python/C++同输入同结果；claim/release无互锁 |
| R3：单request真实执行 | 异步Mesh接纳、PREFILL/DECODE/PUBLISH、真实KV descriptor与append join、真实output fill/store | E2E-D；一次CQ、准确phase/token/byte链、surrogate旁路关闭 |
| R4：continuous shared batch | canonical scheduler、immutable三表、all-core arm、serving驱动MoE、多batchcache候选 | SERV-3/4/11/14/15、MOE-24；有限资源生产背压与stable identity |
| R5：repair/persistence | delta reuse、full REPREFILL、compile/test无pin、generation tombstone、持久恢复验证 | E2E-E两臂、HOST-15/25、完整SESSION_ADMITTED/RELEASE cleanup |
| R6：取消/错误收尾 | shared member tombstone、PUBLISH segment链、全局failure reducer、KV prefix与terminal快照 | PROTO-12/23/25、E2E-H、多故障/同edge排列、全部live资源归零 |
| R7：oracle与累计验收 | 注册全部分句、真实篡改负例、determinism、diagnostic与RunManifest、测试索引 | 本节所有退出条件与最终人工Code Review |

每阶段先写能复现缺失行为的测试，再实现最小完整模块并使用真实依赖验证。开发中只跑受影响定向测试，避免每次修改重跑累计 Gate。不要按固定轮数、预算或现有绿色数宣称完成。

## 13. 最终退出条件与交付格式

1. 主合同 Gate 6 的全部分句均映射到可执行 subcase，所有 `future_*` Gate 6占位替换，新增backend实际使用真实Serving/KV。
2. mandatory selector在新的独立workdir单次运行 `earliest_gate<=6`：**148/148 logical**、全部注册subcase PASS、0 skip/xfail/timeout。构建/缺资源/watchdog不允许冒充expected fatal。
3. validate_manifest、Gate 6 acceptance/runtime capability检查通过；Gate 5依赖与生产覆盖遗留归零。coverage来源是逐条可执行断言与生产证据，不是手写空列表。
4. ABI两个生成器 --check、Python/C++ golden、受影响unit/negative/integration与GTest通过；Gate 1–5共享路径有定向保护，probe行为保持原合同。成功类与expected-fatal类分别按账本核验。
5. determinism三次重复与要求的A/B成立；生产篡改负例可复现；Host/KV/weight/activation/output/control/AXI/Garnet独立对账。
6. 正常quiescence、error drain、partial-output/KV prefix、persistent manifest和控制面fatal snapshot符合相应terminal class，所有owner exactly-once结束。
7. 代码结构复审：单一状态写入口、完整typed identity、无重复session/cache/compute/transport实现、无绕过端口的字节操作；新增代码不写注释，删除旧旁路。单文件超过2000行必须按职责结构审查，禁止靠挪行掩盖耦合。
8. 更新 [测试索引](../../../docs/ai_mesh/mesh_ir_test_commands.md) 中唯一命令入口、新增组件/schema/API索引；文档引用源定义，不复制第二套公式/状态表。
9. 报告实际执行命令/退出码、独立产物目录、manifest和输入digest、每项反例结果、未执行范围及真实限制。源码改了但旧binary未重建的运行不得作为证据。
10. 停在人工Code Review与提交决定；不自行 commit/push、进入Gate 7/8或补双lane实验。

建议验证入口沿用现有 selector、validate_manifest、ABI生成器及本Gate登记的定向测试；具体命令由测试索引维护。累计验收在实现完成后执行一次，修复失败后在新目录重跑，保留失败产物，不合并多次局部结果宣称单次全量PASS。

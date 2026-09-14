# Dummy AI Core Gate 5 Coding Spec

实施目标：在 Gate 4 基线上实现 Dynamic MoE V1 的真实 Mesh 执行、有限 weight cache 和独立流量对账，通过 Gate 5 累计验收。交付给实现 agent；本文不表示列出的新组件、schema 或测试已经存在。

本文冻结 Gate 5 的实施边界、接入方式、工作顺序与证据要求。协议字段、数值、排序、公式和状态机以主合同及生成器为唯一来源，不在此复制。先读代码、提出具体实施方案并按仓库规则取得确认，再写实现；不得把本文件当作已经通过 Code Review 或允许提交的证明。

## 1. 权威来源与退出范围

| 内容 | 唯一来源 |
|---|---|
| Gate 边界、30 个新增逻辑项 | [主合同](DUMMY_AI_CORE_AGENT_CODEX_SPEC.md) §17.3、§17.7、§17.9、§18 Gate 5 |
| Dynamic MoE ABI、对象身份、provider、capacity、cache、materializer、traffic | 主合同 §7 |
| Core/SRAM/DMA、instance start/error/drain | 主合同 §4、§5、§6、§10.6 |
| 配置容量、trace、错误、守恒 | 主合同 §12–15 |
| AXI/Garnet 完成与网络路径 | [AXI 合同](AXI_GARNET_CODEX_SPEC.md) |
| Mesh compiler、canonical JSON、binary 结构 | [Mesh IR 合同](TORCH_EXPORT_MESH_IR_CODEX_SPEC.md) |
| Mesh wire 数值与布局 | [mesh_ir_abi.yaml](../../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml) 及其生成器 |
| Agent detail code、disposition、SQ/CQ | [agent_protocol_abi.yaml](../../../util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml) 及其生成器 |
| 逻辑 case 的 Gate 归属、全部 subcase | [mandatory manifest](../../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml) |
| Gate 4 业务路径及保护边界 | [Gate 4 Coding Spec](DUMMY_AI_CORE_GATE4_CODING_SPEC.md)、当前实现、[测试索引](../../../docs/ai_mesh/mesh_ir_test_commands.md) |

基线为 Gate 4 提交 `245e94b666`。实施开始时记录实际 HEAD、分支、dirty/index 状态；检查根目录 `AGENTS.md` 和存在时的 `Agents.custome.md`，保留无关修改，不清空用户暂存区。

Gate 5 新增 `MOE-1..23,25..30` 和 `E2E-C`，共 30 个 logical ID；基线累计 88 个，本 Gate 累计 **118 个**。实际选择集合从 manifest 的 `earliest_gate<=5` 派生，禁止另维护第二份运行名单。subcase 数量随覆盖增长，不冻结成 270 或其他预估数。

必须交付：

- ABI feature/section、严格 reader/compiler/verifier、跨语言 golden。
- 四类 route provider、语义 token 身份、版本化 RNG、capacity 和 placement。
- separate-domain overlay、真实 Core/SRAM/DMA/AXI/Garnet 执行、insertion gate。
- streamed weight，以及 cached 的 reservation/fill/subscriber/failure/replay 完整生命周期。
- 独立 selection/materialization/traffic oracle、真实 4×4 `E2E-C`。
- 所有 Gate 5 mandatory 分句的正例、负例、时序与资源边界覆盖。

不在本 Gate 交付 continuous serving 调度、完整 prefill/decode/PUBLISH 编排、KV reuse/eviction、shared-serving 的 `MOE-24`、`PROTO-12/23/25`、`HOST-15/25`、全部 `SERV-*`、完整 `E2E-D/E/H`、FAST_EVENT/WINDOWED_TIMING 或 checkpoint 恢复。四个历史双 lane E1 点不补。不得因此跳过 Gate 5 的多 member 数据结构、cache 跨 batch 竞争或 `MOE-28` 的取消/错误 fanout。

## 2. 当前接入点与必须先做的重构

| 当前实现 | Gate 5 接入要求 |
|---|---|
| [Mesh binary reader](../../dev/ai_mesh/mesh_binary.cc)、[Python ABI](../../../util/mesh_ir/mesh_ir/abi/) | 当前 minor 已为 1.2，reader 仍拒绝所有非零 required feature；扩展能力 mask 和 conditional sections，不降级版本、不取消未知 bit 拒绝 |
| [builder](../../../util/mesh_ir/mesh_ir/builder.py)、[model](../../../util/mesh_ir/mesh_ir/model.py)、[EffectiveArchitecture](../../../util/mesh_ir/mesh_ir/effective.py) | 从同一有效配置生成 program/容量/SimObject 参数；新增 override 先验证后进入对应 digest，禁止 runner 私下改硬件 |
| [MeshProgramLoader](../../dev/ai_mesh/mesh_program_loader.hh) | 安装 immutable static program、已验证 MoE specs、binding/tag registry；不在 load 时把动态 route 写回 CommandROM |
| [MeshDispatcher](../../dev/ai_mesh/mesh_dispatcher.hh) | 从固定次数自启动扩展为可 arm/start/observe terminal 的实例接口；保留既有测试入口作为调用方，不增加第二个 dispatcher |
| [MeshDummyCore](../../dev/ai_mesh/mesh_dummy_core.hh)、[ProgramScoreboard](../../dev/ai_mesh/program_scoreboard.hh) | 现有 command/event/transfer 多处为裸 u32；先统一 typed key 和实例 generation，再接 overlay，与 static 命令共用 scheduler/engine |
| [DMA 接口](../../dev/ai_mesh/dma_types.hh)、[AXI DMA](../../dev/ai_mesh/axi_tensor_dma_engine.hh)、[peer aperture](../../dev/ai_mesh/peer_sram_aperture.hh) | 扩展 owner/descriptor/transfer 身份及缓存 load 仲裁；所有 descriptor 复用现有 burst、4 KiB、response、peer commit 与 credit 路径 |
| [TensorSram](../../dev/ai_mesh/tensor_sram.hh) | 在现有 bank/port ledger 与 allocation validity/pin 之上实现 partition、bounded metadata、row/gather view 和 refcount；不建立一个绕过 SRAM 端口的 MoE scratch 数组 |
| [NpuRequestExecutor](../../dev/ai_mesh/npu_request_executor.hh)、[Frontend](../../dev/ai_mesh/npu_serving_frontend.hh) | 当前 accept 主要返回 surrogate service 时间，不能据此宣称真实 Mesh 完成；新增异步执行生命周期时改造共同接口，保留 probe/surrogate 实现 |
| [case registry](../../../configs/example/ai_mesh/dummy_core_case_registry.py)、[selector](../../../tests/gem5/ai_mesh/run_manifest_selector.py) | 注册 Gate 5 backend 与真实 subcase，继续统一 artifact/exit/环境身份检查 |

优先完成 runtime identity、instance lifecycle、DMA ownership、SRAM view 的结构重构，再写 provider/materializer。不得复制 `MeshDummyCore`、另造 MoE opcode 执行引擎、在 AXI/Garnet Router 加 expert 路由语义，或把 overlay key 压成 hash/u32 塞回旧 lookup。

现有静态路径的 wire ID 保持原样，只在 runtime 边界提升为完整 typed key。所有查找、callback、wait、actual traffic 和错误 source 同步迁移；迁移后删除旧状态表和旁路。feature 关闭时 Gate 1–4 行为、事件顺序和已有生成产物保持合同兼容。

## 3. Gate 5 的执行入口与 Gate 6 接缝

### 3.1 预冻结批次入口

Gate 5 使用明确标记的 **预冻结批次测试入口** 驱动 production MoE 组件，不要求先做 continuous batch scheduler。入口从已验证 WorkloadPlan、concrete program/profile 与 replay 数据构造 immutable population/MemberSlice；不按 tick、callback 或网络结果重排 member/source rank。接口只提交 frozen batch 与 bindings，不能替 provider 选择 expert、替 cache 指定 hit/miss、替 materializer 生成执行结果。

该入口必须支持多个 member、多个 layer、多个待接纳 batch 和有限 backpressure；串行 core 执行不等于全系统只有一个 batch。`max_active_instances_per_core=1` 时，待启动 batch 仍可持有合同允许的 reservation/subscription，用于 `MOE-28` 的交叉资源、共享 fill、abort 和故障验证。

`MOE-3/25` 的 population A/B 可直接使用这个入口；`MOE-28` 必须使用真实 coordinator/cache/DMA/instance owner。涉及成员取消、错误 CQ fanout 的子项接到现有 RequestContext/completion ledger/Frontend，通过真实协议提交与消费验证，不能用手工记录若干“完成事件”替代。仅不涉及协议的 provider/golden 测试可以使用纯函数 fixture。

Gate 5 不把该入口称作 continuous serving，也不占 `MOE-24` 的 PASS。Gate 6 替换的是 frozen batch 的来源，而不是 provider/cache/materializer/Core 的实现。所有相关结构从第一版就按真实多 member 表设计，不提供单 member 特化数据模型。

### 3.2 执行与完成边界

执行链使用主合同 §7.1；selection 在 capacity/cache 前冻结，最终 materialization 仅由唯一 finalizer 产生。全部 participating core/binding/overlay 预检成功后才原子 arm/start。cached fill 可以在 start 前产生真实流量，但失败不得造成部分 core 启动；“零副作用”必须按失败发生的具体阶段定义，不可要求已 commit 的后台 fill 消失。

Frontend 以 Dispatcher 的真实 instance terminal/drain 通知推进完成，不能用 surrogate serviceNs、`CORE_START` probe、预估 timer 或 `REQUEST_END` marker 代替。若场景包含 Host output，继续遵循实际 output B、metadata B、fence、CQ/MSI/ACK 顺序；MoE 输出不应再经过 surrogate 重写而掩盖真实执行。纯 NPU `E2E-C` 独立报告其范围，不冒充完整 Agent serving E2E。

static lifecycle 保持唯一指定 BEGIN/END 和每核 local HALT。region 进入/退出相对该 lifecycle 的关系必须通过跨 core 依赖支配关系证明，不在每核额外复制全局 BEGIN/END。所有 region 的 anchor 邻接、stream 约束及 layer 顺序仍逐项按 §7.2.2 校验；若具体 compiler 表达与主合同冲突，在实施方案阶段解决，不运行时猜测。

## 4. 所有权与有限资源

下表是职责边界，名称为建议的新组件，不要求机械地一类一文件。

| 组件 | 唯一拥有的状态 |
|---|---|
| `MoERouteProvider` / selection validator | immutable provider/profile、token-local draw 或 population matching；无 cache/Core side effect |
| `MoeRouteMaterializer` | selection + capacity + binding + frozen cache token 到 canonical overlay 的一次性构造；不拥有物理 fill |
| `InstanceCommandBuffer` / overlay verifier | 预分配对象槽、typed ordinal、DAG、view/ref、scratch interval、region gate 引用 |
| `CacheReservationCoordinator` | 有限候选队列、canonical arbitration、跨 core shadow transaction；唯一 reservation COMMIT 写入口 |
| `DynamicWeightCache` | 每 core slot/tag/validity/LRU/generation、failure table、active fill map |
| `WeightFillObligation` | fill descriptor/outstanding/SRAM commit、subscriber 和 first-error；不由首个 batch 代持 |
| `BatchCacheReservationSet` | 该 batch/layer 全部 token、pin 和 subscriber 引用，唯一 abort/release 入口 |
| `InstanceFailureReducer` | 同 edge 多源候选的唯一业务 first error 与 fanout 决定 |
| `MeshDispatcher` / region coordinator | 实例与 gate 生命周期、参与 core 集合、正常/错误 drain 汇聚；不重算 route/cache outcome |

`WeightCacheBaseKey`、full `WeightFillKey`、RuntimeObjectKey、SemanticTokenUid 及比较器各只有一个定义源；跨 wire 的布局/enum 走 ABI generator。正常 RESOURCE_WAIT 不分配新 identity、不改 LRU、不遗留部分 pin。固定槽上限来自 binary 和 CapacityPlan，禁止借用无限 map/vector、隐藏 retry queue 或 overflow 后自动扩容。

## 5. ABI、schema、binding 与 preflight

### 5.1 ABI 扩展

按主合同 §7.2 扩展唯一 Mesh ABI registry：feature bit、四个 conditional-required sections、record 布局、role/key/validity/error-source enum 及 helper record。保留当前 ABI 1.2/FENCE 能力；Dynamic MoE V1 的 1.1 最低 feature 语义不要求把整个 writer 降到 1.1。1.0 的 reserved bytes、不同 minor 的 attr 支持和 unknown required bit 按各自规则 fail closed。

Python encoder/decoder/verifier 与 C++ loader 都检查 feature、section presence、record bytes/version、reserved、zero-based reference 和 semantic dense ID。必须测试修改 payload 后重算 checksum 的恶意输入，不能只证明 SHA 能检出未重签字节。未置 feature 却携带 MoE sections、旧 reader 遇新 required bit 的行为必须明确并有 golden。

### 5.2 Schema 与 digest

扩展现有 schema/RunManifest，而非另建一个宽松输出通道。主合同列出的 route/replay/correlated/cache/fill/weight-binding/image schema 均落地；递归 exact object、严格 JSON key/整数/NFC、canonical sorting、digest omission/projection 复用既有工具。新增 nested envelope 在 schema 中冻结，文档只索引。

必须提供 selection、逐 member selection、materialization、tag manifest、cache snapshot 和 fill traffic 的 Python/C++ projection golden。比较 typed numeric 字段，不对 little-endian UID/key 用 memcmp。不能 hash 整个带 runtime identity、timing、diagnostics 的 artifact 充当 semantic digest。

program/architecture/weight registry/image 的引用必须在方案阶段画出无环 projection 依赖图，并给出 cycle-breaking golden。主合同已规定的字段不能改；缺少 exact envelope 的部分先在唯一 schema/生成源中定义并审查，不让运行时和 oracle 分别发明。已落地的对象在 RunManifest 不得继续使用 Gate 0–4 的 null 占位。

### 5.3 权重、profile 与地址

实现 Gate 5 所需的 external weight symbol、read-only persistent binding、program weight registry/model image 和 NPU memory endpoint backing。source address/home/content 只能从已验证 registry/image 派生；fixture 生成器可以产生明确标记的 synthetic weight bytes，但不能把地址按 expert ID 随手拼出。

request-bindable profile、symbol requirement 和 frozen MemberSlice 所需的共同基础设施属于本 Gate 前置依赖；不因此提前实现 KV/完整 serving 调度，也不能省略这些绑定验证。exact alias 共用物理 tag，冲突 alias 必须装载失败。每核 canonical tag 表覆盖全部 reachable program，不按当次访问懒分配。

先安装有效 architecture、互斥 SRAM partitions、memory map 和 immutable program/provider，再派生 identity/bindings/tag/CapacityPlan；验证与安装使用同一文件快照。编译配置无法满足的静态容量在 tick 0 前拒绝；合法运行时竞争采用 bounded backpressure。

## 6. Provider 与 capacity 实施要求

四种 provider 遵循 §7.3–7.7，顺序采用 replay → histogram → correlated → uniform；不能拿 uniform 完成整个 Gate 后将其他 provider 留 TODO。

- replay 按全场景 reachable union 检查，再投影当前 population；全局未来 entry 不能被误报 extra。所有 provider 共用 without-replacement/slot completeness validator。
- histogram 必须 exact source×expert count 与 canonical feasible assignment；优先复用可提供确定性 integer flow 的项目组件。若无可复用实现，按合同冻结的算法实现并单测，不换成随机近似或依赖库的未规定 tie-break。
- correlated 的 Q32/Q16、hotset、source bias、sticky predecessor、window 与虚拟 prefix 按合同；没有全局可变 RNG state、浮点热路径或 rejection redraw。
- token-local 与 population-coupled 的 A/B 声明分开。改变硬件、batch membership、decode chunk 或 KV-path 投影的纯 identity 测试，不等于已实现 Gate 6 调度/KV。
- selection 先冻结再 capacity；capacity 在全 frozen population 一次处理，不按 source arrival。FAIL 不启动任何 core，DROP/PAD 不改 selected expert。

必须有大小端敏感 UID、exact RNG preimage、每 slot 固定 draw、不可实现 histogram、全部 top-k DROP、zero real/positive padding、zero tokens、多 layer E/K 不同、missing/fallback 和 digest 篡改测试。禁止以统计近似通过替代 exact golden。

## 7. Overlay 与真实执行

按 §7.2.1–7.2.3、§7.9 分离 static/overlay namespaces；所有 command 仍是既有 base opcode，走相同 decode/admit/ready/engine/commit/drain 路径。Static ROM 不预留 ID hole、不插入模板、不改写；overlay 容量空槽不算逻辑对象。

materializer 构造完整 logical overlay 后 canonical 排序、赋 ordinal、checked bump scratch，独立 verifier 再检查密度、owner、单 producer、DAG、各级 bound 和 traffic。group/per-region transfer 计数语义不同，view records 与 refs 必须各自计容量；bound-1 测试不能只覆盖 command 数。

必须落地的执行细节：

- local gather row 直接引用原有效 SRAM，remote row 通过真实 P2P 到目标 slot；无隐式 scatter/copy 或无端口开销的 view commit。
- greedy chunk 同时要求 src/dst allocation 与 offset 连续；不能跨离散 member 地址合并。row 不拆分，AXI burst 分段仍由唯一 DMA engine 完成。
- route metadata、PAD、全 DROP 使用合同的真实 fill/digest/validity；FUNCTIONAL_BYTES/DIGEST_ONLY/VALIDITY_ONLY 的服务字节和 cycles 相同。
- fan-in 0/1/>1 分别走规定 fill、COPY_THROUGH、reduce；显式 SRAM read/write、output-ready event 和每 region terminal signal。
- insertion gate 阻止 early resume；cached subscription ready、entry visibility 与全 region release 条件均满足才放行。overlay 全部 command/event/DMA/SRAM/transfer drain 后才发布 group exit。
- successive instances 重用 static program 和合法 persistent cache，清空旧 generation/owner/wait/临时 allocation；fault-before-HALT 按错误 drain，不能等一个不再执行的 HALT。

scratch hole、INVALID padded result、不同 region 同 ordinal、typed-key collision、early resume、receiver commit 与 sender B 重排必须有负例。不得只把 Python 生成的 overlay JSON 当作 C++ 已执行证据。

## 8. Weight cache、严格 replay 与错误收尾

### 8.1 Reservation 和 physical fill

cached/streamed 按 §5.5、§7.8 完整实施，resident 明确拒绝。streamed 的目标是 scratch allocation；cached 的目标是 cache slot。`M_e=0` 不产生 demand，exact alias 按 base key 去重，layer occurrence 的 token 所有权仍独立。

coordinator 使用两阶段 shadow：先保护全部 hit，再处理 attach/miss/victim；所有 core 一次 COMMIT。slot reservation、cache edge handoff、下一 DMA edge eligibility 必须按合同分离，不在一个 callback 内同时留下旧 tag hit 和新 fill 写入。

物理 fill 永远 cache-owned；HIT/ATTACH/NEW_FILL、fill incarnation、success/failed drain、subscriber release、tombstone 发布遵循 §7.8。ordinary DMA 与 cache DMA 使用同一有限 typed-union arbiter；填充不能绕过 descriptor queue、AXI outstanding、SRAM bank 服务或 peer credit。

### 8.2 Replay 的范围

支持 cache-state replay 的严格装载、冷/暖状态、generation/incarnation/LRU 与 failure tombstone。支持用于 Gate 5 固定 population 的 batch replay 投影及 `strict_replay_serial_batches`；完整 serving eligibility/phase 覆盖留 Gate 6，未实现的模式明确拒绝，不能宽松忽略字段。

strict 模式从一次初始 snapshot 连续演化，逐 batch 等待合同 terminal barrier，且在 start 前对全部 reachable layer 做单 snapshot batch-wide reservation。不得每 batch 重装 cold/warm 状态。普通 concurrent 模式允许 timing 改变 cache decision；oracle 只在合同要求的决策条件相同情况下强求 materialization/physical traffic 相同。

### 8.3 必须固化的资源/错误反例

| 反例族 | 要检出的错误 |
|---|---|
| 两 batch 交叉请求两核、浅 MSHR/slot | partial pin、ABBA、RESOURCE_WAIT 重分 identity/LRU |
| 满 cache 同次 HIT A + miss C | miss 遍历先逐出应保护的 A |
| 同 tag 并发、同 batch exact alias、多 layer alias | 重复 fill、重复 physical bytes、错误 token 去重 |
| A→B→A eviction、warm replay | incarnation 重用、旧 fill ref、tail validity 泄漏 |
| 第一/全部 subscriber member cancel | 误取消共享 fill、重发、漏唤醒原 batch drain |
| prestart abort、已启动 instance fault、多 fill 部分完成 | 未枚举全部 token、pin 泄漏、只释放当前失败 weight |
| fill error 与最后 success/command error/new cancel 同 edge | first error 随 callback 排列改变、提前 VALID、重复 CQ |
| failure retire 与新 reservation 同 edge | tombstone 空窗、同 generation 重试、ERROR_HELD 被当 free |
| background fill 仍 pending 的实例错误收尾 | 等不到 HALT、错误等待独立 cache-owned 工作、提前复用 scratch |

每项使用 production 状态机和真实资源；C++ 定向 edge permutation golden 与 GEM5 浅资源/fault 场景互补。错误码/disposition 走生成器，禁止记录一个预期错误码就把场景判为通过。

## 9. Oracle、facts 与报告

实现独立的 Python MoE oracle（主合同 §7.10）：从 immutable workload/provider/program/architecture/binding/replay 重算 selection、capacity、overlay、cache physical obligation 和逐 peer/link expected；C++ runtime 只记录 actual。schema、enum、canonical wire codec 可共用 SSOT，预期与实际不能共用同一累计变量或同一 materializer 输出当唯一依据。

若 Python 同时承担 fixture compiler，oracle 仍须独立重算关键约束与守恒，并用手算小例、跨语言 golden、真实产物篡改证明能检出 emitter/runtime 错误。

报告扩展现有 acceptance schema/RunManifest，至少关联 route plan、fill traffic report、typed trace、instance/cache drain 和 summary/JUnit。logical demand、physical fill、local SRAM、remote dispatch/combine、AXI payload、wire overhead 和 per-link packet/flit 分栏；shared fill 只按完整 fill ID 汇总一次。路由策略、拓扑、packetization 由当前实际配置解析，不硬编码 XY 或把 src/dst 总量当 link bytes。

### 9.1 生命周期事实不能替代结果验证

新增 facts 仅在现有事件不足以表达必要状态边界时引入，记录点必须位于唯一状态写入口。先查因果事实再判预期，不从最终 winner/status/actual bytes 反推“预期”。至少区分 freeze、reservation commit、slot handoff、fill accept/SRAM commit/terminal、gate release、instance-owned drain、token release、failure-table commit 与最终 fanout。

同 tick 若存在多个合同阶段，比较器使用有定义的 phase/edge 顺序；不能默认同 tick 全都先于当前事件，也不能拿日志输出次序代替硬件因果。动态插入、缓存和跨时钟通知特别要覆盖 phase permutation。

按已观测阶段验证存在性：没有下一阶段的合法 cut 可以缺后续事实；已有 assignment/commit/terminal 的对象必须具有其前置证据。不得用“缺失=未来”的 sentinel 让已经完成的链条通过，也不能要求尚未接受的 rollback 命令有 acceptance。full run 和 assigned/committed-but-not-consumed 前缀都要测。

### 9.2 真实产物篡改

至少逐类删除/修改 route、slot、fill ID、subscriber、latch/commit、typed owner、DAG edge、view validity、peer/link actual 和 terminal record，验证 oracle 或 schema 明确拒绝。正例应同时覆盖 cold/warm/attach/streamed、正常/FAIL/drop/pad、全 local/remote、prestart/poststart fault 和合法未终结前缀。不得用捕获所有异常、复制错误后的 oracle 期望或给反例改名的方式让测试通过。

## 10. 验收覆盖组织

下表只指定实施证据位置与组合方式，具体断言仍引用主合同 §17.3 对应编号；一个编号包含多个分句时必须拆成真实 subcase。

| Logical ID | 最少证据组织 |
|---|---|
| MOE-1,2,8,21,22 | 独立 UID/RNG/without-replacement golden + C++/Python parity + 非法输入 |
| MOE-3,25 | token-local 与 histogram 分开的 population/timing A/B；共同 token/rank projection |
| MOE-4 | 多 layer 共享 tag、严格串行 replay 的冷/暖及快/慢 fill；normal 模式对照 |
| MOE-5,6,7,17 | 四 provider 正负例、exact histogram/matching、schedule/window 与 fallback |
| MOE-9,10,11,12,15 | capacity 全局性、全 DROP、padding byte/cycle/validity、unsupported transport、zero work |
| MOE-13,14,18,20,30 | local/remote 执行、dispatch/combine、hotspot、逐 peer/link 与独立 traffic oracle |
| MOE-16,26,27 | ABI/section/bounds、typed identity、DAG/gate、view/ref/scratch 容量边界 |
| MOE-19 | streamed/cached cold/warm/eviction/replay，resident 拒绝 |
| MOE-23,29 | canonical artifact、projection 和 exact schema 跨语言；重算 checksum/digest 的负例 |
| MOE-28 | §8.3 全部资源/错误反例；production cache+真实 DMA+成员完成证据 |
| E2E-C | 真实 4×4 NPU Garnet，balanced/replay/hotspot，DIGEST_ONLY，完整 dispatch→expert→combine→drain |

新增建议入口 `mesh_ir.gate5_contract`、`mesh_ir.moe_oracle`、`gate5_acceptance.py`、`run_gate5_moe.py` 和 `tests/gem5/ai_mesh/gate5/runtime_contract.py`，职责分别为登记、独立预期、artifact 转换、GEM5 配置入口、能力缺口检查。通过现有 registry/selector 接入，不复制 selector：新增显式 `Backend.GATE5` 与对应 scenario 分支，不能落入既有 Gate 3 `else`；Gate 5 的 program weight registry、model weight image 与 endpoint map digest 必须来自这些入口，不得继续填写 null 占位。`coverage_gaps=0` 必须来自已注册且可执行的能力检查，不是手写空列表。

所有本 Gate `future_moe_*` 和 `future_e2e_c` 替换为真实注册项；Gate 6/7 future 项保留。不得调整 earliest_gate、删 mandatory 分句、借已有逻辑 PASS 掩盖未执行 subcase。

## 11. 实施顺序与每轮交付

先在 `.tmp/docs/` 写实施计划，列出代码接缝、schema/ABI 依赖图、状态所有者、每项 mandatory 到 subcase 的映射及容量 closure。确认方案后按以下依赖顺序推进；每轮独立可构建、有针对性测试和真实路径证据，不要求每轮重复全量。

| 轮次 | 可审查产物与过关要求 |
|---|---|
| R0：现状与合同核对 | 核实基线、ABI 1.2 与 feature 扩展、profile/registry/image projection、冻结 batch 入口；消除不一致再开发 |
| R1：共同基础设施 | typed runtime identity、实例 arm/terminal/error API、DMA owner 与 SRAM view/partition；Gate 1–4 定向行为不变 |
| R2：ABI 与离线计划 | 编解码/loader/verifier、exact schema、binding/tag manifest/CapacityPlan；跨语言 golden、capacity-1、零副作用负例 |
| R3：provider 与 capacity | 四 provider、UID/RNG、selection freeze、capacity；exact golden、跨 population/硬件边界验证 |
| R4：streamed overlay | materializer/verifier、gather/row/chunk/DAG/gate；先用真实 streamed 路径跑通 local/remote/fill/reduce/E2E-C 基础场景 |
| R5：cached 与失败收尾 | coordinator、cache obligation/subscriber、failure table、strict replay、共同 DMA arbiter；§8.3 全部反例与 normal/strict 对照 |
| R6：验收闭合 | 全部 Gate 5 subcase、独立 oracle/tamper、4×4 E2E-C、determinism；最终累计 selector 与代码复审 |

生命周期/typed key 不应在最后一轮补；shared fill 不能先用 batch-owned load 暂代再留下兼容分支。每轮删掉被新结构取代的旧路径。按职责控制文件规模，任何文件超过 2000 行必须做结构审查；新增实现遵守仓库的无注释、无过程补丁说明和无冗余 helper 要求。

## 12. 最终退出条件与交接

完成所有模块后执行一次新的、独立目录的 Gate 5 累计验收；这是 Gate 合同退出检查，不是全仓性能/全量测试。开发中优先用 `--id` 定向执行。

```bash
scons build/AXI_MESH/gem5.opt -j8
python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check
python3 util/mesh_ir/mesh_ir/abi/generate_agent_abi.py --check
python3 tests/gem5/ai_mesh/validate_manifest.py
python3 tests/gem5/ai_mesh/gate5/runtime_contract.py
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 5 --workdir .tmp/gate5-final
```

其中 Gate 5 runtime_contract 是本 Gate 需新增的入口；实际命令与新增测试索引同步落在 [测试文档](../../../docs/ai_mesh/mesh_ir_test_commands.md)。执行时使用新的 workdir，不混合多次运行结果。

退出条件逐项核验：

1. 累计 118/118 logical PASS，全部实际注册 subcase 通过，0 skip/xfail/timeout；expected-fatal 按 exact first error/retained ledger 通过，不能伪装成 quiescent success。
2. Gate 5 future 占位归零，manifest、results、JUnit、每份 summary、输入 digest 和执行身份一致；能力缺口为零。
3. ABI 生成器干净；所有新增 schema、projection、canonical key 和记录大小有跨语言 golden；无手改 generated 文件。
4. Gate 1–4 共享路径定向回归通过；累计 selector 包含此前全部 mandatory；Gate 3 protocol prefix 和 Gate 4 Oracle 生命周期/tamper 回归另保留。修改共享路径时核对既有 facts/仲裁顺序，而非只看最后 PASS 数。
5. `E2E-C` 使用真实 Garnet/AXI/SRAM/Core；dispatch/combine/weight 与 per-peer/per-link 逐项对账。pure Python、mock transport 或 surrogate 结果不能占它的 PASS。
6. 固定输入至少三次 canonical artifact/trace 一致；严格 replay 与 normal cache A/B 的不同保证分别验证，不能把 timing 变化强行解释为 route 变化。
7. 正常 drain 的 instance/overlay/command/event/descriptor/AXI/SRAM/token/pin/subscriber/MSHR/queue live 状态归零；合法持久 VALID cache line 和 failure tombstone 单独核对，不要求把持久状态清空来获得“零”。
8. prestart abort、started error、fill failure 和 cut 前缀的所有权及阶段证据闭合，真实产物篡改被拒绝；没有 callback 顺序依赖、假 completion 或缺失事实默认通过。
9. 构建、必要测试、`git diff --check`、文件规模和架构收尾检查通过；记录实际 SHA、命令、退出码、报告路径、未执行项与准确的实现限制。

实现 agent 完成交接后停止，等待独立 Code Review 与人类提交授权；不自行 commit/push，不进入 Gate 6，不补双 lane E1 点。

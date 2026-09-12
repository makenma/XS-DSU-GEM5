# Dummy AI Core Gate 4 Coding Spec

交付对象：Sol。实施目标：在已完成的 Gate 3 上实现可复现的多用户 coding-agent 闭环、有限 Host 资源池和对象生命周期，通过 Gate 4 累计 88 个 logical case，以及单独报告的 E2E-E Agent-only subset。

本文件规定 Gate 4 的实施选择、代码边界与验证要求。已有协议、字段和公式沿用主合同。§5 的 surrogate 阶段投影、§7.2 的仲裁步骤、§8 的 Host 本地故障类型与 §9 的停止口径是本文件补充冻结的设计选择，不能表述为 Gate 3 已有能力。本文提到的新文件、类型及命令是待实现要求，不表示已有实现或测试通过。

## 1. 权威来源与阶段范围

| 内容 | 唯一来源 |
|---|---|
| 总体边界与 Gate 划分 | [主合同](DUMMY_AI_CORE_AGENT_CODEX_SPEC.md) §0、§1、§18 Gate 4 |
| command identity、control plan | 主合同 §6.1 |
| SQ/CQ、submission、CancelJoin、completion/fatal | 主合同 §8、§9.1 |
| WorkloadPlan、HostStagePlan、对象与资源池 | 主合同 §9 |
| 配置、容量与地址规划 | 主合同 §12 |
| 守恒、watchdog、drain | 主合同 §15 |
| mandatory case 与报告合同 | 主合同 §17.4、§17.5、§17.9 |
| AXI channel、ordering、buffer、credit | [AXI 合同](AXI_GARNET_CODEX_SPEC.md) |
| Mesh IR、canonical JSON、binary ABI | [Mesh IR 合同](TORCH_EXPORT_MESH_IR_CODEX_SPEC.md) |
| 协议数值和生成产物 | [agent_protocol_abi.yaml](../../../util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml) |
| case 注册与执行身份 | [mandatory manifest](../../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml)、[case registry](../../../configs/example/ai_mesh/dummy_core_case_registry.py) |
| 已有测试命令 | [测试索引](../../../docs/ai_mesh/mesh_ir_test_commands.md) |

本 Gate 必须实现：

- `replay_plan` 装载、严格校验、稳定 identity、地址/容量/Host task 计划。
- 每用户一条 business pipeline；first-pass、compile-fail repair、test-fail repair、repair-limit。
- compile/test/log-parse 三种服务、有限排队、共享 token、确定性仲裁与防饥饿。
- Host local-I/O 计时、generated-code/raw-log/excerpt 对象及引用守恒。
- 多用户共享一套 SQ/CQ 的 submission 与 completion；单请求 CANCEL 双腿 join。
- 1/3/12 用户真实 AXI-over-Garnet 集成、停止接收、错误收尾与全局 drain。

本 Gate 的 NPU 执行使用主合同允许的 **full-context re-prefill surrogate**。它每轮实际读取完整 Host input、经真实 AXI 写入输出，再走既有 metadata/CQ/MSI/ACK 路径。MoE、KV 数据复用/eviction、continuous batching、真实多次 prefill/decode dispatch、shared-batch cancel、FAST_EVENT/WINDOWED_TIMING 和 checkpoint 恢复属于后续 Gate。

`PROTO-12/23/25`、`HOST-15/25`、`SERV-*` 和完整 E2E-E 不因实现了相关公共组件而提前计为通过。Gate 4 的主运行模式为 `FULL_TIMING`，仅 Host 服务及明确标注的 NPU surrogate 使用解析计时。

## 2. 从当前代码开始

先阅读根目录 `AGENTS.md`、存在时的 `Agents.custome.md`，记录本次实际 SHA、分支和 dirty worktree。当前仓库未找到 `Agents.custome.md`；实施时重新检查，不创建替代规则。现有未提交的 DMA、Garnet、实验及文档修改须保留。

| 现有入口 | Gate 4 的使用方式 |
|---|---|
| [gate3_protocol_runtime.hh](../../dev/ai_mesh/gate3_protocol_runtime.hh)、[Driver](../../dev/ai_mesh/gate3_protocol_runtime.cc) | 将 protocol pump 与按 profile 生成请求的测试脚本职责分开；business FSM 不叠入现有 `Stage` |
| [Frontend](../../dev/ai_mesh/gate3_protocol_frontend.cc) | 分离 SQ intake、request execution 和 completion publication，使等待执行的 GENERATE 不阻止后续 CANCEL 被读取 |
| [SubmissionLedger](../../dev/ai_mesh/agent_submission_ledger.hh) | 复用 pending publication、ID 不复用、early evidence、commit/rollback；不增加第二份 producer/head 状态 |
| [completion ledger](../../dev/ai_mesh/gate3_completion_ledger.hh)、[IRQ queue](../../dev/ai_mesh/gate3_irq_commit_queue.hh)、[fatal reducer](../../dev/ai_mesh/gate3_fatal_reducer.hh) | 保留 Gate 3 的 ownership、MSI/ACK 乘积状态与 fatal-cut 语义 |
| [protocol validation](../../dev/ai_mesh/agent_protocol_validation.hh)、[AXI transfer planner](../../dev/ai_mesh/gate3_axi_transfer.hh) | 扩展参数/metadata 的已验证期望值来源，复用 wire 校验与分段；不按 case 名跳过校验 |
| [AiMesh.py](../../dev/ai_mesh/AiMesh.py)、[SConscript](../../dev/ai_mesh/SConscript) | 维持同一个 Driver/Frontend SimObject，新增组件由它们拥有 |
| [run_gate3_protocol.py](../../../configs/example/ai_mesh/run_gate3_protocol.py)、[AXI_MESH.py](../../../configs/ruby/AXI_MESH.py) | 复用已有 fabric 装配与 adapter；Gate 3 profile 继续是协议测试 fixture |
| [MeshDispatcher](../../dev/ai_mesh/mesh_dispatcher.hh)、[MeshProgramLoader](../../dev/ai_mesh/mesh_program_loader.hh) | 后续真实 program execution 的接入对象；本 Gate 不把 protocol executor 的 `CORE_START` 当作它们的执行证据 |
| [acceptance.py](../../../util/mesh_ir/mesh_ir/acceptance.py)、[selector](../../../tests/gem5/ai_mesh/run_manifest_selector.py) | 扩展现有验收入口和 artifact schema，不另写宽松 runner |

当前 `NpuServingFrontend::handlePromptRecord()` 的 `CORE_START` 是协议探针，随后调用 `startOutput()`；当前 Driver 也由 `profile/request_count` 生成请求。这两点决定了 Gate 4 需要抽出 request source/executor 边界，不能只在收到 IRQ 时加一个 compile timer。

优先将被实际改动的 Driver/Frontend 分到 `agent_axi_driver.*`、`npu_serving_frontend.*`；移动后更新 SimObject header、构建和测试引用，删除旧定义。独立且已有测试的 `gate3_*` ledger/transfer 类不为改名而改名。业务路径不得继续增长 `mode("CASE_NAME")` 分支；fault/profile 只在测试配置或明确的 fault-injection 接口中选择。

## 3. 组件与唯一状态所有者

| 组件 | 所有状态与必要接口 |
|---|---|
| `AgentWorkloadManager` / `CodingAgentFsm` | immutable plan 引用、每用户 TaskContext、round、business outcome、lifecycle；接收已验证 completion 和 Host stage completion，产出 submission/stage intent |
| `DriverSubmissionTable` | planned identity 到 live submission 的关联、pending publication、early completion；复用 `SubmissionLedger` 的 ring 算术 |
| `HostResourceManager` | admission registry、三类队列、slot/token、SWRR、aging reservation、stage timers；`tryEnqueue`、edge arbitration、完成通知、drain snapshot |
| `AgentObjectTable` / `RemoteHostMemoryBacking` | object identity、allocation/valid range、版本、producer、consumer ref、poison/drain；所有本地和 AXI 访问使用同一份 backing |
| `ControlTriggerCoordinator` | test-only control-plan record、anchor、delivery edge；只产出 action intent，不代替 AXI 完成 |
| `NpuServingFrontend` | accepted request table、执行等待队列、CANCEL target 查找与 tombstone、terminal queue、completion publication |
| `NpuRequestExecutor` | NPU 内部的有限执行接口：接纳已验证 request、执行进度、cancel/drain、terminal result；本 Gate 实现 full-context surrogate |

这些是职责边界，不要求一类一个文件。`CodingAgentFsm` 可以是 `agent_workload_manager.*` 内的普通 C++ 类；三种 pool 使用同一个实现和 `HostStageKindV1`，不复制三套 scheduler。Host 组件没有独立 AXI port，不注册为 CPU/Host fabric SimObject。

新增 ID 在 [agent_runtime_types.hh](../../dev/ai_mesh/agent_runtime_types.hh) 复用 `SequenceValue<Tag>`，避免把 request、Host task、Agent object、SQ/CQ sequence 混用。跨 event 保存 ID 或 owned value；只读 plan 可共享，不能保存可失效的容器元素指针。结果使用有类型的 accepted/backpressured/rejected 状态，普通 queue-full 不是异常或 fatal。

Trace/ABI 可见 enum 在现有 ABI YAML 中定义并生成。Host runtime 的详细 state 与主合同用于诊断的 `HostStageStateV1` 通过唯一 projection 关联，不能让两份 enum 独立推进。不得手改 generated 文件。

## 4. Plan、identity 和 preflight

### 4.1 装载链

实现 `mesh_ir.agent_workload` 和 `mesh_ir.agent_planning`，职责分别为 immutable 业务计划与从计划派生的 identity/address/capacity。使用已有 Python `json`、`jsonschema` 和 canonical 工具，先检查可复用实现；不手写 JSON parser 或另造 schema 引擎。

装载顺序固定为：严格 JSON 解析 → Workload/Control schema → 跨字段语义校验 → profile 解析 → canonical digest → CommandIdentityPlan/HostTaskIdentityPlan → HostArena/Object plan → CapacityPlan → runtime 安装。

所有步骤在 `simulate()` 前完成。C++ 只安装通过验证的不可变计划，保留无损整数类型，并检查运行时实际 SQ/parameter 与计划的一致性；不在 C++ hot path 再调用 Python 推进业务。计划文件内容读取一次，校验与安装必须使用同一快照。

### 4.2 必须补齐的 schema 与生成产物

在 `schemas/ai_mesh/` 落地：

- `agent_workload_plan_v1.schema.json`、`agent_control_plan_v1.schema.json`。
- `command_identity_plan_v1.schema.json`、`host_task_identity_plan_v1.schema.json`。
- `host_arena_object_plan_v1.schema.json`、`capacity_plan_v1.schema.json`。
- 本 Gate 实际创建的 endpoint map、runtime config、surrogate profile 和 observation schema。

主合同已定义的字段、顺序、wire 和 digest 规则直接实现；所有 object 递归 `additionalProperties=false`。本 Gate 不使用的合法 plan 字段仍须完整校验，不能从 WorkloadPlan 删掉 session/KV policy/full-context 信息来省略校验。

`HostTaskIdentityPlan` 的 record 必须覆盖 static-reachable `{user_id,task_seq,repair_round,stage_kind}`，按主合同次序赋 `host_task_id`。对主合同只给出内容、未完全列出 JSON envelope 的派生产物，先将 exact envelope 冻结在 schema 和 golden 中；RunManifest 引用同一 `$defs`，不得写第二份结构说明或逐组件自建格式。

必须覆盖以下拒绝条件：

- duplicate JSON key、NaN/Inf、非法 UTF-8/NFC、unknown/missing field、bool 冒充 integer、不规范 u64-json。
- user/task/round 不连续、item ID 重复/越界、session 或 handle 重复、错误 stage kind、outcome 路径不闭合、repair cap 多/少一轮。
- full/cached/delta 的 token 与 byte 等式、相邻 round closure、KV token terminal 等式、I/O 最小字节不成立。
- YAML 用户数/repair cap 与 plan 不同；profile/key、output capacity、metadata capacity、deadline presence/value、TLV digest 或 parameter 任一字段不匹配。
- unsupported execution capability、任何容量不足、地址溢出/重叠、固定 AXI ID 或 MSI ID range 冲突。

JSON schema 处理结构、类型和字段排他；跨 record 算术/引用由 semantic validator 处理。错误报告包含字段路径和已有稳定 DetailCode。遵循主合同的 authority-first 校验顺序，不能先用不存在的 profile 推导 capacity 错误。

### 4.3 可复现性

runtime 只接受 `agent.mode=replay_plan`。主合同 Gate 4 条目中的 keyed draws 按 §9.2 的严格规定执行：业务 draw 在离线 fixture 中冻结；runtime 不抽样 think time、token、Host 时长、日志大小或 outcome。无需为本 Gate 新增通用随机 workload 生成器。

复用 [canonical_json_bytes](../../../util/mesh_ir/mesh_ir/model.py) 和 [canonical_u64](../../../util/mesh_ir/mesh_ir/acceptance.py) 的唯一实现；严格 parser/NFC 验证应补在共享入口，不另加一套 serializer。回归必须证明已有 ASCII golden 的 digest 不变，并增加 Unicode、duplicate-key、u64 边界 golden。

CommandIdentityPlan 按主合同 §6.1 的静态三段次序分配；HostTaskIdentityPlan 按 §9.4 分配。runtime cutoff、故障、callback 次序和实际 issue 次序不能压缩后续 ID。Control plan 为空时仍计算主合同规定的非零 null-plan digest。

think time 和所有 ns 时间使用 checked ceil 转 Tick，并按接收方合法 edge 投影。deadline 保持绝对 Tick。不同硬件配置可改变实际到达 tick 和跨用户完成顺序，但不能改变计划、每用户 task/round 次序、冻结 outcome 或大小。

### 4.4 容量与对象规划

按主合同 §12 的 static outcome closure 计算完整计划；不根据一次运行的 peak 占用倒推容量。`required/configured/headroom` 逐字段对账。对象、Host task、request ID 等 run-lifetime history 与 live queue 分开，history 留存不等于 live work。

Host input/parameter/output/metadata 按唯一 semantic key、alignment 和最低可用地址分配。SQ/CQ 使用既有 compact ring layout，不继续按 request sequence 的固定 stride 隐式分配各对象。control、ring、payload 范围用 checked half-open interval 验证。

RAW_LOG/EXCERPT 是 Host-local 对象，不能取得 NPU 可读的 raw-log aperture。100 MiB raw log 可只保存有限 metadata、大小和 digest，不必分配等大的 C++ byte vector；其 local-I/O bytes 必须真实计入解析计时。OUTPUT 则必须与 AXI target backing 共享有效区间和版本。

## 5. Gate 4 NPU surrogate 接口

此处是阶段实施选择，不是新增一套模型执行器：将 Gate 3 的协议执行探针隔离为 `NpuRequestExecutor` 的测试实现，完整 serving 后续接入同一个 Frontend 的执行接口。协议层只有一套 SQ/CQ、metadata、MSI、ACK 和 fatal owner。

配置显式选择 `FULL_CONTEXT_SURROGATE`，并进入 configuration digest 和 observation。Gate 3 的 protocol-probe fixture 与 Gate 4 的 workload fixture 使用同一协议实现。不得把 surrogate 配置隐藏在 case 名里，或让报告声称执行了真实 Mesh program。

默认一个执行 slot，多个 accepted request 进入 CapacityPlan 约束的有限队列；SQ intake 和 completion publication 独立推进，不能等执行 slot 空闲才接收 CANCEL。选取待执行 GENERATE 使用 `{deadline_or_UINT64_MAX, qos 降序, ready_tick, request_id}`，每次仅启动一个 singleton；不等待组成 batch。

`agent_surrogate_profiles_v1.schema.json` 作为测试 fixture 的唯一结构源：顶层 exact `{schema:"agent_surrogate_profiles_v1",version:1,profiles}`；profiles 非空，按 `{program_id,profile_id}` 唯一升序。record exact 字段为 `program_id:u16>0,profile_id:u16>0,profile_key:u64-json>0,input_tokens:u32>0,input_bytes:u64-json>0,output_tokens:u32>0,output_bytes:u64-json>0,service_ns:u64-json>0,publish_chunk_bytes:u32>0`。这些是 surrogate 请求匹配与计时参数，不是预期结果；profile fixture digest 进入 execution configuration，不能替代 WorkloadPlan digest。参数与 fixture 的 exact 比较在正常 Frontend admission 实现，不由测试脚本直接断言后绕开 runtime。

本阶段严格边界：

1. 每轮真实读取 `full_context_bytes`，地址、tokens、digest、program/profile/key 都来自已验证 request。repair 使用 `ALLOW_REPREFILL`；不把 `cached_tokens` 偷改为 0，不只读取 delta。`REQUIRE_REUSE` 对此 backend 在 preflight 以 `E_KV_REUSE_REQUIRED` 拒绝；WorkloadPlan 通用 schema 仍认识该 policy。
2. concrete surrogate profile 固定输入/输出大小与非零解析执行时长，不来自当次 observed timing。profile registry 是显式测试 fixture，逐字段验证 plan；它不是 `.mshb` 的 `AGENT_REQUEST_PROFILES`，不宣称开启 `MESH_FEATURE_AGENT_SERVING_V1`。正式 serving 必须读取主合同 §10.3.1 的 binary authority。
3. prompt 通过真实 AR/R 拉取，output 通过真实 AW/W/B 写入；可按有界 chunk 流式处理，不一次性缓存任意大的 payload。执行计时最早在输入 R 完成后的合法 NPU edge 开始。
4. output 内容是执行端派生的确定性 surrogate。测试 backend 的 seed 固定为 `SHA256(UTF8("AI_MESH_AGENT_SURROGATE_V1\0") || raw32(input_content_digest) || LE16(program_id) || LE16(profile_id) || LE64(profile_key))`；第 i 个 token digest 为 `SHA256(seed || LE32(i))`，i 从 0 连续编号。执行 timer 成功完成时 commit 完整 token prefix；此前取消/失败为 0 token，此后 output 错误不改已 commit token prefix。最终 prefix/filler 复用主合同公式。计时时长、地址、AXI ID、buffer、tick 和 runtime seed 不参与，expected-output oracle 永远不输入执行端。该公式只定义本测试 executor，不替代真实 Mesh compute digest。
5. `completed_program_instance_count` 只能来自实际 MeshDispatcher completion。纯 protocol surrogate 为 0，单独报告 `surrogate_executions`；不得沿用探针写死的 1。TTFT、decode/KV 指标只可报告为未建模，不用 0 延迟冒充实测。
6. metadata 的 `SESSION_ADMITTED` 只能来自实际 tuple-admission record commit。Gate 4 只需要 session identity/admission 的公共记录基础，不需要 KV 数据有效性或 reuse；该基础属于后续 `SessionKvManager` 的唯一 record owner，由 Frontend 持有，不能另建独立的临时 session manager。首次 INITIAL 验证并创建 tuple，后续 ALLOW_REPREFILL 验证相同 owner/program/generation 后命中 identity record，但没有 KV data hit。记录无 KV allocation、无 pin，并在 RunManifest 声明。成功 CQ 仍遵守主合同要求的 admission bit，禁止仅凭 SUCCESS 填 1。
7. Gate 4 fixture 显式 `release_session_on_task_terminal=false`；已接纳的无资源 session record 按 persistent manifest 保留。Host 仍维护 task `cleanup_required` 和唯一 lifecycle final 节点。auto-release 和 standalone RELEASE 的完整行为留给 `HOST-25/PROTO-23`；未支持的 runtime 配置/trigger 在 preflight 明确拒绝，不伪造 RELEASE SUCCESS。
8. 支持单 member 的 queued/running/output-pending CANCEL。已有 output descriptor 全部 drain；尚未 issue 的后续 output 不再发出；terminal partial bytes 按已完成传输和有效前缀生成。`PROTO-12` 的 shared-batch 行为不在此 backend 实现。

surrogate 的 output result 只进入 NPU 本地的 completion publication queue；Driver 仍须经真实 CQ/metadata backing-read 得到结果。未来接真实 Dispatcher 时，该 NPU 内部接口可复用；本 Gate 不修改 MeshDummyCore 的普通 opcode 或 tensor 数值语义。

Wire layout、CRC、completion class 和 metadata flags 不产生 Gate 4 私有变体。Gate 4 的阶段投影只有明确的 surrogate token-completion 来源、零真实 instance count、无 KV 数据的 session identity record，以及 fixture profile authority；观察 schema 必须区分 surrogate 与真实 program execution。尚无完整 serving capability 时，不能仅凭此模式通过 §10.3.1 或 SERV 的验收。

## 6. Agent FSM、submission 与取消

### 6.1 正常业务

实现主合同 §9.1 中本 Gate 涉及的业务和 CANCEL 状态边；auto-release 依 §5 的阶段配置延后。每用户最多一个 active task、一个 business GENERATE；CANCEL control obligation 独立计数。业务状态与 protocol transfer state 正交。

`GENERATE/SUCCESS`、合法 CRC/metadata、对应 submission 已 doorbell-B commit 三者全部满足，才能进入 CompileQueue。成功 compile 才能 test；失败且可 repair 才创建 parse；parse 完成才能提交下一轮 GENERATE，随后重新 compile。到达 repair cap 时不创建最后一次 parse 或额外 NPU 请求。

`TaskLifecycleFinal` 是唯一 completed/failed task 计数和 next-task think-time 起点。普通 control completion 不能推进 task。保留业务成功、业务失败、`INFRA_FAILED` 的明确 outcome 分类，不能把本地提交失败当成 compile FAIL 消耗 repair budget。

### 6.2 Driver edge 顺序

复用 Gate 3 的 physical facts 收集、fatal reduction 与 normal-commit 屏障。无 fatal 时，Host edge 统一执行：

1. 接收 committed backing/local-event/AXI response facts，按稳定 key 归并 acceptance evidence，再解析 doorbell B。
2. 按 `cq_seq` 验证并处理到期 CQ/metadata local reads，推进 HOST_VISIBLE/business FSM。
3. 处理到期 Host 服务完成、对象 commit/ref release、lifecycle final 与 run cutoff。
4. materialize control trigger、到达的 task 和已具备对象的 submission/stage intent。
5. Host admission registry、pool 仲裁和 SQ submit 仲裁各使用完整候选集；新产生的跨组件工作最早下一接收方 edge 开始。

同 edge 的 target completion 先于新 cancel intent，因此之后的 CANCEL 使用 standalone waiter。SQ 全局只有一个 pending publication，仲裁 key 使用主合同 §8.0；不能改成每用户一套 ring 或同 tick callback 抢先写 SQ。

CQ/metadata local read 延迟可独立配置，至少下一 Driver edge 可见。MSI callback 只入有限 IRQ 队列；禁止读取 NPU RequestContext、metadata 指针或直接调用业务 completion。ACK B 继续属于独立 response ledger，不让它回滚已在 NPU semantic retire 的 CQ。

### 6.3 CancelSubmitting / CancelJoin

共用主合同 §9.1 的双腿 record。record 在 CANCEL 本地提交前建立，target CQ 与 command CQ 都可以早于 doorbell B；缓存后不得提前推进业务。B 成功后才确认 command CQ obligation，已有早到 leg 不得清空。

必须区分：

- cancel-wins：command SUCCESS，target CANCELLED。
- target-success-wins / target-error-wins：command ALREADY_TERMINAL，业务只按 target outcome。
- safe local rollback：无 command acceptance evidence，销毁未发布 command leg；target CQ 已到则立即按其结果继续，否则回 WaitNpu；command ID 永不复用。
- command CQ/head 已到后 B error、wrong/corrupt B 或显式 ambiguous commit：沿用 Gate 3 infrastructure fatal，不能 rollback/retry。
- standalone CANCEL 的 NOT_FOUND/ALREADY_TERMINAL：只终结其 waiter，不开始 compile/next task。

合法 control plan 不能生成 self-target/0/control-target；这些协议负例通过明确 wire mutation 测试注入，不能放宽 plan validator。已有 live target 返回 NOT_FOUND、cookie 错配或两腿 status 不合法应命中稳定 protocol fatal。

Gate 4 control fixture 使用已有可观察的 `SCENARIO_START`、`AFTER_SQ_ACCEPT`、`AFTER_FIRST_OUTPUT_CHUNK`、`SAME_EDGE_AS_TERMINAL`、`AFTER_GENERATE_TERMINAL` anchor；尚未实现的 batch/KV/release anchor 明确拒绝。Coordinator 仅是验收触发器，不参与业务网络完成判定。

## 7. HostResourceManager

### 7.1 原子 reservation 与 timer

token/slot 数量、Q16 可用比例、Host task 状态及 `actual_done` 公式严格按主合同 §9.4/9.5。一个 stage 在同一仲裁事务取得 type slot 和全部 token，不得先占一个再等另一个。释放也必须原子且恰好一次。

以配置的 `fixed_latency_ns`、`bytes_per_ns` 和全局 tick frequency 用 checked 整数/有理数换算计算 local-I/O；不把小于 1 的 bytes-per-tick 截断为 0，不使用 double。所有 read/write bytes 相加和 Tick 加法都检查溢出。

nominal 与 local-I/O 各有可观察完成条件；stage 完成取两者共同满足的首个合法 Host edge。资源持有到该 edge 的 object commit/ref release 完成。计时关闭时 local-I/O 条件初始满足，但 local byte 账本仍记录计划值。零 callback 递归、零 wall-clock sleep、零 CPU-local AXI。

### 7.2 确定性 SWRR 与 aging

以下补足主合同未展开的调度步骤，必须固化为算法单测 golden：

1. `HostAdmissionRegistry` 的等待记录按主合同 canonical arrival key 排序；每类 service queue 也是此顺序。queue-full 保留原 arrival tick 和 record，在 slot 释放等事件唤醒时重试，不每周期忙轮询。
2. 同 edge 已到期 release 先提交，再将可接纳记录入队，随后开始一个稳定的 arbitration pass；整个 pass 的 arrival 集合固定，不能插入新 callback。
3. 每类仅队首参与 SWRR。无 aging reservation 时，eligible 为队首所需 slot/token 均可满足的 kind；对 eligible kind 的有符号 score 加各自 weight，取最大 score，平手按 `COMPILE < TEST < LOG_PARSE`；winner score 减本轮 eligible weights 之和，然后原子分配。继续挑选直到无 eligible。无成功选择不更新 score，空/暂不可执行 kind 的 score 保留，所有算术 checked。
4. 先于每轮普通 SWRR 查找已达到 aging threshold 的队首，按完整 arrival key 选唯一最老 target。target 可运行则优先分配；不可运行则设全局 reservation，暂停新的 slot/token 分配，等待已有 running stage 归还资源。
5. reservation 不是部分资源持有，不抢占 running stage，不清空其他 queue，不冻结已有 timer。target 启动时清除 reservation；本次强制选择不修改 SWRR score，随后按原 score 继续普通仲裁。
6. reservation 等待期间不得反复累加 score。触发 aging 的时刻必须有一次离散 wakeup，不靠无关网络 traffic 偶然唤醒。

只考虑已到达的任务。preflight 已保证每个需求可被全局资源满足且 stage 时长有限，因此 reservation 建立后，在当时所有 running stage 完成并归还资源后的下一个可启动 Host edge 内，target 必须开始。测试用这个有限 completion bound 验证，不能只运行很久后断言“最终启动”。

服务容量合法性与底层 queue backpressure 测试分开：主合同 required queue depth 可能已覆盖整个单业务 pipeline 峰值。`HOST-9/23` 可在 HostResourceManager 单元夹具中使用 depth=1 和多个真实 stage record 证明 backpressure；不能为了在 E2E 制造 queue-full 而绕过 CapacityPlan 的 required 下限。

## 8. 对象、日志与错误收尾

对象状态、valid/allocation bytes、producer/consumer ref 按主合同 §9.6。只有唯一 `AgentObjectTable` 改变 object state；compile/parse/FSM 持有 typed handle，不各存一份 committed flag。

- GENERATED_CODE：真实 output 写入 backing；NPU 的 output B/drain/fence 约束和 Driver 最终 metadata 验证共同保证其可消费。Driver 不能通过跨边界 C++ 查询 output B；它只使用协议给出的完成证据及本地已提交范围。
- RAW_LOG：失败 COMPILE/TEST 的 Host service 在 actual-done edge 原子 commit；必须满足该 stage 的 local write bytes 约束。未 commit 时 parse 不可入 running。
- EXCERPT：parse 消费同一 RAW_LOG，完成时 commit。同一 EXCERPT object 的引用进入下一 repair INPUT 的来源账本；其余 raw log 字节不映射到该 INPUT。
- 所有消费均受 valid prefix 约束，allocation padding 不可读。持有 ref 或 producer/DMA 未 drain 时不得释放/复用。RAW_LOG/EXCERPT 的逻辑来源关系和 prompt filler bytes 分开，后者仍使用主合同规定的 deterministic input surrogate。

100 MiB/16 KiB 用二进制单位的 exact 整数 fixture。分别记录主合同 §9.7 的六个 byte/token 指标；`repair_input_excerpt_bytes` 不等于整个 full-context repair input。必须同时证明 excerpt 份额为 16 KiB、完整 input 实际拉取等于 plan full-context bytes、fabric raw-log bytes 为 0。

错误分三个域：

| 域 | 行为与证据 |
|---|---|
| NPU program/output data 错误 | 停止未 issue 的输出，已接受 AXI drain，按实际 partial prefix 产生一次 request error CQ；未输出 RESERVED object 也必须释放 |
| Driver generated-code/object local read/produce fault | `HostStageErrorDraining`；poison 对象、终止后续消费者、drain 已创建本地事件、归还 ref/slot/token，task 以 INFRA_FAILED final；不制造 AXI B/R error |
| CQ/metadata/control/MSI/ACK 或协议不变量错误 | 使用现有 fatal reducer/cut/retained ledger；停止业务推进，保留冻结 ownership，不能伪造成功 drain 或第二条 CQ |

本地 object/read fault 是显式可注入的 Host 领域 fault，不是 `DRIVER_CQ_READ/DRIVER_METADATA_READ` 的 completion-path fatal。本 Gate 在 ABI YAML 冻结 `HostLocalFaultSiteV1{OBJECT_PRODUCE=0,OBJECT_READ=1}` 和 `E_HOST_LOCAL_OBJECT=0x00060006`，使用 `BUSINESS_TERMINAL` disposition、`cq_status=null,run_exit_reason=null`；Host final outcome 仍为 `INFRA_FAILED`。生成器输出跨语言常量和 golden，不占用 `FaultSiteV1.RESERVED_16`，不制造额外 CQ，也不把对象错误改写为正常 compile FAIL。若实施基线已占用该值，按主合同冲突规则处理，不自行换号。正常失败路径不会因为缺 output object 而自动创建默认对象。

## 9. 停止、watchdog 与实际证据

stop-accepting 只阻止新的 business task；已 accepted task 的 repair、Host stage、已有 CANCEL obligation 和 completion/ACK 必须收尾。static plan/identity/capacity 不重算。尚未启动的 think event 与依主合同可抑制的 control trigger 转为明确 terminal/suppressed 状态。

task final 计数提交后、下一轮 task admission 之前检查 cutoff。`stop_after_completed_tasks` 在本 Gate 按已进入 lifecycle final 的 task 总数判定，包含成功与失败；同 edge 多个 final 全部提交后再关 admission，已在途 task 可使最终数量超过阈值。这个停止口径必须有 golden 并进入 runtime config schema 的字段说明。

watchdog 要识别合法的 nominal/local-I/O/think/aging future event；800 ms compile 等待不应变成 deadlock。未收到 AXI 响应且无可靠 side-effect 证据不能靠超时猜测 rollback，继续遵循 Gate 3 watchdog/fatal 区分。

global drain 使用主合同 §15.6 的合取 predicate，并连续两个 network edge 成立。必须直接检查业务 live state、Host queue/reservation/slot/token、对象 ref/poison-drain、pending submission、IRQ/local-read/local-store、三本 protocol ledger、adapter/shaper、Garnet buffer/credit 和 scenario future event。不能把 `frontend_drained` 的一个统计值当整个系统的状态来源。

输出 raw observation 至少包括 task/round/state edge、Host admission/reservation/start/两个 timer/actual-done、object/ref transition、submission resolution、CQ local reads/HOST_VISIBLE、cancel 双腿、actual AXI transaction 及 owner、drain snapshot。canonical key 和 enum 注册在 schema/生成源中。

Python oracle 从 immutable plan 与独立 raw facts 重算合法状态边、资源积分、字节守恒和完成因果。actual 由 runtime 事件产生，不能从 WorkloadPlan 或 expected traffic 表拷贝。错误地将 Host local-I/O 注入网络的实现应由真实 transaction owner/range 检查抓住，不能只因为程序写死一个 `raw_log=0` counter 就通过。

同配置至少重复三次，canonical timing/trace/stats 一致。改变 Host slots、clock、AXI latency/buffer 后，只在无 fault、无 runtime cutoff 的 paired run 中要求实际完成路径全部相同；有 cutoff 时仅要求 static plan/identity 不变，并显式记录执行子集差异。改变生成输出 digest 的测试仍必须保持冻结 compile/test outcome。

## 10. 验收映射

Gate 4 新增 27 个 logical ID：`PROTO-21/22/27` 和 `HOST-1..14,16..24,26`。manifest 的 earliest_gate 不变；原来的 `future_* / default` 占位必须替换成下表要求的真实 subcase。表内是必须覆盖的行为，不要求固定 subcase 总数；最终数量由 manifest 展开计算。

| ID | 最低验证内容 |
|---|---|
| PROTO-21 | cancel/target-success/target-error 三种赢家；两 CQ 两种可见顺序；same-edge CQ-before-intent；command CQ 早于 B；safe local rollback；不合法双腿组合拒绝 |
| PROTO-22 | standalone NOT_FOUND/ALREADY_TERMINAL；self/invalid target 负例；已发 output drain、未发 output 抑制、partial prefix 和单次 terminal |
| PROTO-27 | 分别延迟 CQ、metadata header、metadata tail local read；HOST_VISIBLE/compile 不前移；禁止 ISR 跨对象直接 completion 的负例 |
| HOST-1 | 单用户 first-pass；真实 output/CQ/MSI 后 compile/test；唯一 task final |
| HOST-2 | compile FAIL → RAW_LOG → parse → repair GENERATE → compile/test SUCCESS |
| HOST-3 | test FAIL 的 repair 重新经过 NPU 和 compile |
| HOST-4 | 直接 repair→test 非法边被拒绝，不能仅测试正常路径未出现此边 |
| HOST-5 | cap=0、cap 最后一轮失败；没有多余 parse/repair/ID issue |
| HOST-6 | 各 kind 缺 slot/缺 token 两类阻塞；无 partial reservation |
| HOST-7 | acquisition/release 和 token 总量逐 edge 守恒，terminal 后无泄漏 |
| HOST-8 | nominal-first、I/O-first、相等、I/O-disabled 的 exact actual-done |
| HOST-9 | 有限 queue、SWRR golden、无饥饿；下层真实 ResourceManager 夹具 |
| HOST-10 | 100 MiB raw log + 16 KiB excerpt，repair full-context 实际 AXI 字节 |
| HOST-11 | raw-log fabric 0、excerpt 16 KiB；篡改 owner/byte fact 的 oracle 负例 |
| HOST-12 | 输入/输出 semantic digest 改变时 compile/test outcome 不变 |
| HOST-13 | 三用户分别走 first-pass/compile-repair/test-repair；三次重跑 exact trace |
| HOST-14 | 12 用户、4/6/4 pool、有限 token；实际有 queue wait 且非人为同 tick 完成；所有任务收尾 |
| HOST-16 | 多 MSI、有限 IRQ/read queue 仍可靠消费；polling 配置拒绝 |
| HOST-17 | cutoff 时分别处于 think、WAIT_NPU、排队、running、repair；活跃任务 drain，未启动 task 无 ghost submission |
| HOST-18 | schema/跨字段/canonical/arrival/parameter/profile 全部正反例及边界；负例按字段族拆分 |
| HOST-19 | 持续小任务到达下的大 token aged task，满足明确 completion bound |
| HOST-20 | reservation 不抢占、不部分占用；target 后 SWRR score 序列继续确定 |
| HOST-21 | 非整除带宽、非整周期时间、两 timer 完成条件与零 Host-local AXI |
| HOST-22 | RAW_LOG producer/read 同 object；EXCERPT commit/ref 同 object；错误范围/状态访问拒绝 |
| HOST-23 | depth=1 队列的多个 WAIT_HOST_ENQUEUE 稳定接纳；arrival/release callback 排列不改变 trace |
| HOST-24 | Python/C++ allocator golden；valid/allocation/tail/ref；local-store/target request/response/shaper 各 capacity-minus-one；无隐藏 queue/HostPhysicalBurstJoin |
| HOST-26 | pre-output error；output B 与 ID fault 的真实 request drain；Host object/read fault 的本地 error-drain；ref/slot/token 恰好释放一次 |

GTEST/PYTEST 适合精确状态、算术、allocator 和拒绝测试；声称 AXI、IRQ、backing visibility 或 fabric 字节正确的 subcase 必须运行真实 gem5/Garnet。单元测试覆盖下一层真实 ledger/object/resource 依赖，不把这些关键依赖全部 mock。

normal business failure 和 request recoverable error 可以是 `QUIESCENT_SUCCESS` 的测试结果；assertion 必须证明预期业务 outcome。配置负例由外层测试证明 `CONFIG_ERROR` 再以 0 退出。协议 fatal subcase 使用 `EXPECTED_INFRA_FATAL`、exact first fatal/retained ledger；WATCHDOG、skip、xfail、未匹配 selector 一律不作为通过。

额外 E2E-E Agent-only subset 单独包含 first-pass、compile-repair、test-repair 三路径及 12-user 组合，报告 execution backend 和没有 KV reuse 的事实；它不占用完整 `E2E-E` 的 PASS。

## 11. Harness 与文档交付

新增 `mesh_ir.gate4_contract`、`mesh_ir.gate4_oracle`、`tests/gem5/ai_mesh/gate4/runtime_contract.py` 和 `run_gate4_agent.py` 作为 Gate 4 配置入口；总入口仍由现有 case registry/`run_dummy_core_agent.py` 路由。需要新增 Backend 时使用显式分支，不能落入现有的 Gate 3 `else`。

检查 `run_dummy_core_agent.py` 当前 legacy child-report 路径：Gate 4 必须提交真实 runtime child report，不能被 wrapper 在成功退出后覆盖成全零 ledger/强制 quiescence。将报告所有权显式交给 backend，外层 selector 仍独占 summary/JUnit。

扩展 [acceptance_contract_v1.schema.json](../../../schemas/ai_mesh/acceptance_contract_v1.schema.json) 的已落地 plan/object 定义。Gate 4 的 workload/control/command identity/Host task/Host arena/capacity digest 和对象不得继续使用 Gate 1/2 的 null 占位。只有本场景确实不创建的后续模块可使用主合同允许的阶段空表示；不得通过全局放宽 object schema 让缺字段通过。

新 GTEST 注册到 SConscript 和 selector 的二进制解析表；schema/oracle 测试放在 `util/mesh_ir/tests/{unit,negative,golden,integration}`，不向已有封闭的 `tests/pyunit/ai_mesh` 集合随意加测试。每个 logical ID 必须映射到全部 subcase 的唯一 execution identity。

报告至少交付：每 subcase summary/JUnit/RunManifest/invariants/traffic，实际 observation、失败诊断、Gate 4 aggregate results，以及独立 Agent-only subset 结果。Host stats 从事件区间计算 queue wait/utilization，不能只导出最终 occupancy。未建模的 NPU 性能指标明确注明，不能拿 surrogate 数字替代真实 serving 研究结果。

完成实现后，测试命令仅维护到 [测试索引](../../../docs/ai_mesh/mesh_ir_test_commands.md)，docs 只增加入口/独有结论，字段和算法说明指向其 schema、代码和测试。不要在产品文档、代码或注释中保留实施过程说明。

## 12. 实施顺序与退出条件

按以下可独立 review 的步骤做 TDD，每步先用针对性测试证明缺口，再实现并复核：

1. schema、strict parser、immutable plan、identity/capacity/allocator golden；冻结 supplementary wire/envelope 和 surrogate profile。
2. 抽出 Driver request source 与 NPU executor 接口；Gate 3 仍使用同一套 protocol ledger，定向复跑其 early-CQ/fatal/MSI/ACK 回归。
3. AgentObjectTable、HostResourceManager、local-I/O、SWRR/aging、Host local error-drain 的单元与真实 event 测试。
4. single-user first-pass → compile repair → test repair → repair-limit；全过程真实 AXI。
5. ControlPlan/CANCEL 双腿、多用户 submission、3/12-user、cutoff、capacity/backpressure、fault 与 determinism。
6. manifest/报告/schema/oracle 完整接通，运行本 Gate 范围回归并做收尾 Code Review。

核心基础改造集中在共享 plan/identity、协议与业务分离、object/resource 生命周期和验收设施。不要为满足改动比例重构无关 router/实验代码，也不引入只使用一次且没有封装意义的 helper。禁止新增代码注释；用类型、命名和测试表达约束。改动后的单文件超过 2000 行须按职责重新审查，不能继续堆积。

开发前期不运行全量测试。定向测试完成后，Gate 4 的有限累计验收是必需项，不是仓库全量回归：

```bash
python3 util/mesh_ir/mesh_ir/abi/generate_agent_abi.py --check
python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check
python3 tests/gem5/ai_mesh/validate_manifest.py
python3 tests/gem5/ai_mesh/gate4/runtime_contract.py
scons build/AXI_MESH/gem5.opt -j8
```

构建并运行本次新增的具体 GTEST target；执行已登记的 Gate 4 unit/negative/golden/integration 节点。使用新的空 workdir 执行累计 selector：

```bash
GATE4_RUN_DIR=$(mktemp -d /tmp/ai-mesh-gate4-XXXXXX)
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 4 --workdir "$GATE4_RUN_DIR"
git diff --check
```

`runtime_contract.py` 等新增入口必须先落实，不能把这些目标命令写为已执行记录。selector 必须得到 `88/88 logical PASS`、全部已注册 subcase PASS、0 skip/xfail/timeout；另外完成 E2E-E Agent-only subset 和所触及共享 AXI/协议路径的定向回归。新测试失败后不得修改 expected 为 observed 或删 subcase。

最终 review 检查：有无两套 ring/业务状态、case-specific 正常逻辑、运行时补抽样、隐式扩容、绕过 backing read、虚构 instance/session/KV 证据、oracle/actual 共用状态、无 owner 的 future event，以及错误路径遗留的 object/ref/token。

交付说明列出实际改动、运行命令、退出码/数量、报告路径、已知限制和未执行项。未经人类允许与 Code Review 不 commit；不 push。Gate 4 完成后停止，不自行进入 Gate 5/6。

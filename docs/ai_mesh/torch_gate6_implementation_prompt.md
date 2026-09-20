# Torch Gate 6 实施任务：Collective 与 workload

按 A→F 顺序执行，完成后交主审。本文在用户交给 coding agent 并要求实施时授权本范围内的开发、定向重建、真实运行和证据归档；正常实现选择无需反复确认。不提前宣布 Gate 6 PASS，不进入 Gate 7，不自动 commit/merge/push。

## 0. 权威、基线与隔离

- 先读仓库 `AGENTS.md`、存在时的 `Agents.custome.md`，以及 [Torch 文档索引](torch_frontend.md)。
- Gate 5 接受状态唯一在 [torch_gate5_acceptance.json](torch_gate5_acceptance.json)；范围与证据入口见 [验收说明](torch_gate5_acceptance.md)。以包含该验收文件的已推送 Torch 提交为基线，启动时记录 `git rev-parse HEAD`，不沿用历史 `7a72d51db2` 作为新结果身份。
- 上位规范：[Torch Export / Mesh IR Spec](../../src/doc/ai_mesh/TORCH_EXPORT_MESH_IR_CODEX_SPEC.md) §8、§12.4、§12.5、§13、§14 Gate 6。本文规定可执行切片，不改上位编译/ABI/运行语义。
- 工作区：`.tmp/worktrees/torch-frontend`；分支 `feature/ai-mesh-torch-frontend`。若在另一台机器，使用该分支独立 checkout，先核对接受基线。不得触碰外层 `feature/ai-mesh-mesh-ir` 的 host binding、serving、R3/R4 等工作。
- **编号陷阱**：`acceptance.py --gate 6` 与外层 Mesh IR 的 Gate 6 不是本任务；使用明确 Torch case ID 和测试路径。
- 保留既有用户修改；不得 reset/clean/stash 他人内容。只保留一个产品代码 writer；发现同一路径其他 writer 时停止重叠写入，保存身份与现场，继续不冲突的只读工作。
- 工作计划和证据建于 `.tmp/docs/torch-gate-6-implementation/`。一个 `status.json` 管理状态、义务、矩阵、node IDs、源码/程序/结果身份；正文只链接唯一证据，不复制代码逻辑。已提交的 Gate 5 接受记录保持冻结。

## 1. 交付目标与不在范围内的事项

| 义务 | 必须证明 |
| --- | --- |
| E2E-3 | 真实 2×2 Garnet 上的四核 ring all-reduce，在有限浅 W/R buffer 下存在实测回压，能结束且每条 link/VC 的 credit 恢复 |
| E2E-4 | 既有 Tiny Transformer prefill 一层，由真实 `torch.export` 产物经当前 compiler、ABI、loader 执行；baseline/constrained 均完成，weight/activation traffic 分离，expected/actual 精确相等 |
| 运行闭环 | instance/core/command/generation/descriptor/burst/UID 归属一致，跨核 event/P2P commit 合法，局部 SRAM 驻留正确，HALT 与网络 drain 完整 |
| 观察面 | 规范 §13 要求的 trace、分类统计和实测 credit stall；过滤 trace 不改变功能结果 |
| 回归 | E2E-1/2 接受范围保持，最终四个 mandatory E2E 全通过 |

E2E-3/4允许 CONTENT_DIGEST，但地址、logical bytes、transaction/beat、packet/flit、event、credit 仍必须逐项核验。FUNCTIONAL_BYTES 可继续使用；禁止用 digest 代替字节计数、用 Python 数值模型反推期望。当前 compute 内容模型不承诺数值 GEMM/softmax/all-reduce 正确，不宣称真实 NPU ISA、coherence、MoE、decode 或 UCIe 已完成。

Gate 7 的 clean build、全仓 mandatory suite 和最终发布文档不在本门。不能为通过而增加 watchdog、无限队列、减掉计划、屏蔽 mismatch、skip/xfail 或重录旧 golden。

## A. 勘察、拓扑职责与最小 RED

1. 核对 HEAD、工作树、锁定依赖、binary 与 Gate 5 源码身份。使用现有 `.tmp/torch-package-compiler/bin/python`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=util/mesh_ir`；新环境依 [requirements-lock.txt](../../util/mesh_ir/requirements-lock.txt)及[安装文档](torch_frontend.md)建立，不随意升级。
2. 先读并复用：
   - `architecture.py`、`compile_config.py`、`compile_service.py`、`public_cli.py`；
   - `passes/collectives.py`、`collective_geometry.py`、`placement.py`、`schedule.py`，`scheduled/verify.py`；
   - `examples/tiny_transformer.py`、`tests/integration/backend_compiler_fixtures.py` 及 `test_gate2_backend_compiler.py` 已有 ring-tp4 编译证据；
   - `run_mesh_dma_garnet.py`、`dummy_core_case_registry.py`、`support/garnet_harness.py`；
   - `mesh_dispatcher`、`axi_tensor_dma_engine`、`peer_transfer_coverage`、`fault_plan.py`、`runtime_reconciliation.py` 和 producer-content owner。
3. 当前 [mesh_2x2.yaml](../../configs/example/ai_mesh/arch/mesh_2x2.yaml) core IDs 为 `[7,2,9,13]`，不是节点序号。runner 内仍有 Gate5 专用 `{0:0,1:1}`、单 transfer、首帧路径。逐项识别其适用范围，建立来自 admitted architecture 的 core↔node↔endpoint 映射；不得把稀疏 core ID 改成连续数字来掩盖问题，也不得复制一个“4核专用runner”。
4. 以最小合法四核 program 固化实际失败/缺失入口，保留双核健康对照。对于已正确的模块只补验收，不为产生 diff 改实现。
5. 在短计划中明确唯一 owner、输入/输出、先测后改的顺序。本文既定范围直接实施；若需改变上位规范、删除已接受能力或扩大模型，保存最小冲突并交主审，继续其余独立任务。

## B. 拓扑与逐执行关系的共享化

- 将拓扑/地址/事务身份中影响新载体的固定假设收敛到既有架构及 invocation binding owner；所有调用方派生使用。不得另写 UID 或 splitter 公式。
- 使用完整 admitted dispatch plan。逐实例/核/命令/generation/descriptor 执行分别归档；建 dict/set 前验证原始身份唯一性。一个地址被多个合法执行复用不能用“第一个地址匹配”代替执行身份。
- peer expectation 的源/目标、allocation、片段和完成义务来自 admitted plan；target fresh commit、receiver通知、source B退休保持各自事实。
- 保持 Gate5 `verified_landing_events` 的完整事件集合、stage/区间覆盖、同事务 AW≤landing≤已收到的B 校验；合法 pending-B 与重复但无 fresh coverage 的路径不能伪造事件。
- 先用稀疏core、错peer、跨实例借用、重复掩盖缺失等负例证明共享映射，再跑 Gate5 的双核控制。遇到2000行以上文件按职责审查，移除被取代逻辑，不做无关大拆分。

## C. E2E-3：2×2 ring all-reduce

1. 从既有 compiler collective lowering 生成真实四核环，不在 runtime手工补传输。环顺序、chunk/tail、各phase的P2P/REDUCE/event来自 admitted program；验证每个必需贡献恰好一次，不能只检查最终地址union。
2. 建 baseline 与合法 constrained W/R buffer 配置。容量由 architecture/FIFO/packetizer 约束证明，不能用装不下一条合法事务的配置冒充压力。记录实际stall、有限occupancy和恢复，不能只标记“shallow”。
3. 记录每个transfer/rank/chunk/generation的producer→push→target commit→wait→reduce路径。编译器已接受的重叠、tail和多descriptor行为保持；如果最小载体不覆盖tail，增加一个非整chunk控制。
4. 守恒逐层分开：logical bytes、transactions、beats、packets、flits、各link/VC credit。正常结束时pending、队列、in-flight和credit deficit均归零；逐link/VC核验，不能只检查总credit。
5. 至少固化：删中间rank贡献；重复首chunk掩盖末chunk；目标core/UID/generation替换；event提前；整组packet/flit谎言；单link credit未归还。原始result变异调用共享检查入口；实际fault注入另有真实路径证据，不能把两者混为一谈。
6. 复用既有可控drop/error边界至少验证一条真实中途错误drain和一条真实未排空watchdog。不能删除依赖或抬高watchdog把失败改成完成。

## D. E2E-4：Tiny Transformer prefill

1. 复用 `examples/tiny_transformer.py` 的一层模型与真实 export/import/compiler链。输出应包含manifest、effective config、schedule、mshb、expected traffic及诊断；不能用手写golden program冒充workload。
2. 选编译器已接受的小型concrete profile，记录batch/sequence/hidden/TP和参数身份。至少一个四核TP配置运行baseline/constrained；新增较小TP控制可用于定位，不替代四核collective工作。
3. weight/activation/partial-result/KV等分类来自 admitted tensor role/object/operation；不存在KV时明确为0。不能靠地址段猜tensor class；PRE_RESIDENT权重必须按真实编译契约记录，不把无LOAD偷偷算成HBM流量。
4. expected由 compiler输出、独立splitter/packetizer合同及 admitted executions 推导；actual由实际提交和退休事实汇总，按 program/profile/core/peer/memory_space/tensor_class/direction 精确对账。
5. LOAD→compute→P2P/reduce→STORE 的内容/摘要来自现有producer物理证据owner。缺证据fail closed，不从最终transport反推期望；仅在需要时扩展真实writer只读观察。
6. 负例至少覆盖：weight/activation标签交换但总量不变；漏掉中间层/末实例执行；row/chunk重复掩盖缺失；错误generation/producer；目的偏移平移；错误B后伪报成功。正例必须证明trace开/关、baseline/constrained保持相同功能结果和逻辑流量。

## E. Trace、统计、确定性与矩阵

- 按规范§13补齐本门缺失字段，扩展现有owner/types，不建等价第二套schema。`--mesh-trace`稳定JSONL，支持core/command/tensor/time过滤；默认不启用逐beat/flit海量日志。trace不是运行状态或正确性的唯一来源。
- 统计至少解释makespan、每核不平衡、collective延迟、engine/queue/dependency/SRAM stalls、weight/activation字节、link/vnet/VC utilization及credit stall。不能用观测值反填模型预测。
- 新增矩阵最低要求：E2E-3 baseline/constrained × instances1/2；E2E-4 baseline/constrained × instances1/2；E2E-3非整chunk控制；一条真实错误drain和一条预期watchdog。每行明确期望退出原因，expected-failure单列，不把非零退出计为健康通过。
- E2E-1/2沿用Gate5正常/浅队列及多实例覆盖；新四核共享改动后重跑受影响Gate5套件和46-case矩阵。mock单测不能替代真实Garnet验收。
- 编译确定性沿用workers1/2/8及至少三个PYTHONHASHSEED的既有合同；关键ring/workload载体各两次独立runtime运行，比较语义artifact、trace和结果。排除仅由wall-clock/path造成的元数据需列明，不能删功能字段获得相同结果。
- 顺序：最小RED→owner单元/下一层依赖测试→真实单case→focused→受影响Gate4/5→package/golden/codegen检查→最终matrix与确定性。最后产品源码固定后再重钉身份；无需反复运行已通过且未受影响的全套。
- 使用新建 `/tmp` 路径进行原子program publication。真实命令、返回码、时长、环境、Junit/JSON、输入程序及hash及时归档，不依赖临时路径在下次shell仍存在。

## F. 交付与停止条件

所有成功case必须loader零数据面流量、完整计划退休/合法错误终态、精确账目、内容合同、网络drain。长期负例由同一产品入口拒绝，健康控制通过。新观察字段必须有消费检查，不能只增加日志。

交付包括：

1. `status.json`：IMPLEMENTED_PENDING_REVIEW；义务→case→共享检查→nodeID→证据映射，明确缺口与设计范围。
2. `delivery.md`与`handoff.md`：主审最短验证入口、精确运行数字、未解决项。正文不复制代码或重复建立状态。
3. 自包含复跑器：先保存summary再断言健康通过/负例拒绝；无匹配测试、缺程序、缺字段、hash mismatch均非零退出。
4. 内容寻址program库、矩阵每行的command/effective architecture/result/oracle/identity、当前source与binary/package身份、逐文件manifest检查结果。
5. Gate5回归、旧golden不变、package包含新模块、结构审查和确定性证据；新增golden单独增加，不覆盖已接受值。

不提交build/m5out/trace/binary输出到Git。未获进一步指令不commit/push、不标Gate6 PASS、不执行Gate7 full regression。完成本文后交主审；真实范围冲突给出最小证据，不以降低验收标准自行裁决。

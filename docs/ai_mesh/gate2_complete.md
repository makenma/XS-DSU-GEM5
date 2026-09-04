# Gate 2 完成报告：Dummy Core DMA 与真实 NPU AXI-over-Garnet 集成

依据 `src/doc/ai_mesh/DUMMY_AI_CORE_AGENT_CODEX_SPEC.md` §18 Gate 2。测试命令索引见
`docs/ai_mesh/mesh_ir_test_commands.md`（唯一命令入口）。所有数字以机器产物为准：
JUnit `junit_gate<N>.xml`、`summary.json`/`run_manifest.json`（selector 输出目录），
本文不复述明细。

## 1. Baseline 与交付

| 项 | 值 |
|---|---|
| Branch | `feature/ai-mesh-axi-garnet` |
| Base SHA | `b580482a75`（Gate 0 记录；Gate 1+2 产物在工作树待 review 提交） |
| 前置 | Gate 1 selector 复跑确认 21/21 PASS 后开工 |

## 2. 实际使用的 AXI/Garnet 接口

既有基础设施（语义零修改，仅加只读 `AxiWriteCommitObserver` 钩子与
`driver_mode=mesh_program` 场景模式）：`AxiInitiatorAdapter::tryAcceptAw/W/Ar` +
`tryConsumeB/R`（真实 SLICC/Garnet 注入）、per-ID B/R ROB（B 乱序可观测）、
`AxiTargetAdapter`+`AxiSimpleMemory`（HBM endpoint 与 SRAM aperture 功能后端）、
per-UID extra-latency/fault plan（UID 由 `dma_uid_predict.py` 从 schedule 确定性预测）、
decoder miss→default error target（DECERR）、`GarnetNetwork::quiescenceSnapshot()`。

## 3. 组件（src/dev/ai_mesh/）

- `DmaEngineBase`：mock `TensorDmaEngine` 与真实 `AxiTensorDmaEngine` 的公共
  submit 契约（false=有限队列背压，不计账）。
- `AxiGarnetBridge`：每 core master 桥；有限 AW/AR 队列、per-ID B 有序等待、
  R beat 头部归属。
- `AxiTensorDmaEngine`：descriptor 状态机。每方向 FIFO（UID 可预测）；
  每方向有限 AXI ID free pool（response 完成前不复用）；LOAD 的功能提交与
  burst 终结在 SRAM 写服务完成事件上（RLAST 不等于完成）；STORE 的 W beats
  在本地 SRAM 读服务完成后才构建/提交；FILL 逐行经 SRAM 写服务；任一 R beat
  错误锁存整 burst 丢弃；完成记录在出队前拷贝（无悬空引用）。
- `PeerSramAperture`：SRAM tile 真实 AXI target；tile 功能字节 SSOT；按
  transfer 期望 range 精确匹配 committed bytes 释放 RECV_WAIT；每 instance
  `beginInstance()` 重置观察状态。
- `NpuMemoryEndpoint`：HBM seed/digest 验证。
- `ProgramScoreboard`：instance 级共享事件记分板（跨 core 可见；barrier 计数；
  dispatcher 每 instance 重置）。HALT 等待 publication drain（pending visibility
  为空才算 quiescent）。
- 核心错误路径：instance error latch → 未 issue 命令 CANCELLED、在途 AXI 安全
  drain → INSTANCE_ERROR_DRAINED（不要求 REQUEST_END/HALT）。
- AXI_FENCE：捕获 fence 前已接受 DMA 命令的精确 tag 集合，全部退休才释放。
- DMA allocation pin：在途 descriptor pin 其 SRAM allocation，第二个 admit 背压。
- 验证器：C++/Python 双语逐字段一致（producer 声明值、allocation memory_space
  与 checked 边界、physical/stride、固定 record size、traffic 全字段、opcode↔attr、
  UTF-8、per-side span、local endpoint↔operand shard 绑定）；一致性由
  `util/mesh_ir/tests/golden/test_mutation_corpus.py`（含独立 arch facts 的
  mutation driver）逐 mutant 钉死。

## 4. 验证

机器判定以 §17.9 strict selector 产出的 results、summary 与 JUnit 为准；
命令只在 `docs/ai_mesh/mesh_ir_test_commands.md` 维护：

- `run_manifest_selector.py --gate 1` → `21/21 logical`、`45/45 subcases`、`0 skip`
- `run_manifest_selector.py --gate 2` → `GATE 2: 37/37 logical PASS, 0 FAIL, 0 skip`
  （71/71 subcases，含 dma_edge/dma_shapes/delayed_b/p2p_delayed/read_error/
  write_error/fence/fence_scopes/dma_zero/cross_error/repeat_error/
  pin/constrained/sram_persist/p2p_persist 全部真实 AXI-over-Garnet case）
- manifest、contract tests、Python/C++ 单元测试和 AXI 回归命令见唯一命令索引
- AXI 回归：`run_axi_unit_tests.py`（AXI_UNIT_SUITE_PASS 10 binaries/66 tests）、
  `run_axi_garnet_tests.py --suite quick|full`（0 FAIL）

selector 的结构与语义合同由 `schemas/ai_mesh/acceptance_contract_v1.schema.json`
和 `util/mesh_ir/mesh_ir/acceptance.py` 共同执行；checked-in manifest 是唯一写入口，
case/backend/invariant registry 位于
`configs/example/ai_mesh/dummy_core_case_registry.py`。

守恒/完成点断言（每个 garnet case 由 `CHECKS` 机器执行）：command
issued==completed、live=0；AW==B==write bursts、AR==read bursts、W beats 与
R beats 等于 oracle、valid bytes==useful（错误行计入 discarded/drained-uncommitted）；
`last_r <= local_commit <= done`（LOAD）与本地读先于 W（STORE）；aperture
committed==P2P bytes；RECV_WAIT 在 commit 后完成；Garnet quiescence 全 0 且
creditDeficit==0；bridge/engine 队列 outstanding 全空；每 subcase 唯一
RunManifest 执行身份。

## 5. 未解决

- P0：无。
- P1：E2E-A 的 store 尾部以 after16 digest 验证（compute digest 前缀不在
  Python 侧复刻；逐字节级验证由 dma_edge/dma_shapes 覆盖）。

# Dummy Core Gate 0 仓库探针报告

依据 `DUMMY_AI_CORE_AGENT_CODEX_SPEC.md` §18 Gate 0 与 `TORCH_EXPORT_MESH_IR_CODEX_SPEC.md` §14 Gate 0 的只读探针合同产出。本文只记录从代码验证的事实与索引，不复述 spec。

## 1. Baseline

| 项 | 值 |
|---|---|
| Branch | `feature/ai-mesh-axi-garnet` |
| HEAD SHA | `b580482a75` |
| Dirty files | `.pi-glla/{active,owner,session-owner}.json`（会话状态，非本任务产物） |
| Untracked | `src/doc/ai_mesh/`（三份 spec，随首次 commit 入库） |
| Build variant | `build_opts/AXI_MESH`：`TARGET_ISA='null'`、`PROTOCOL='AXI_MESH'` |
| 已有二进制 | `build/AXI_MESH/gem5.opt`（599 MB，2026-09-01 构建） |
| 工具链 | Python 3.12.3、SCons 4.5.2 |

## 2. Mesh IR 现状：零实现

`grep -rn "mshb\|MeshDummyCore\|mesh_ir" src/ util/ configs/` 无任何命中。`util/mesh_ir/`、`src/dev/ai_mesh/`、`configs/example/ai_mesh/`、`tests/gem5/ai_mesh/` 均不存在。三份 spec 位于 `src/doc/ai_mesh/`（AXI_GARNET / TORCH_EXPORT_MESH_IR / DUMMY_AI_CORE_AGENT）。

## 3. 上游依赖完成度

### 3.1 AXI Garnet phase：已完成（dependency gate 满足）

执行记录：`docs/ai_mesh/axi_garnet_implementation_report.md`。代码索引：

| 组件 | 路径 |
|---|---|
| AXI endpoint/adapters | `src/mem/axi/axi_garnet_endpoint.{hh,cc}`、`axi_initiator_adapter.{hh,cc}`、`axi_target_adapter.{hh,cc}` |
| SimObject 声明 | `src/mem/axi/AxiGarnetEndpoint.py`（AxiInitiatorAdapter/AxiTargetAdapter）、`AxiTraceTester.py` |
| 协议/拓扑 | `src/mem/ruby/protocol/axi_mesh/*.sm`、`configs/ruby/AXI_MESH.py`、`configs/topologies/AxiMeshDie.py` |
| E2E 入口 | `configs/example/axi_garnet_test.py` + `configs/example/axi_garnet_scenarios/*.json` |
| GTest | `src/mem/axi/*.test.cc`（7 个，SConscript 内 GTest 声明） |
| 集成测试设施 | `tests/gem5/axi_garnet/{run_axi_garnet_tests.py,run_axi_unit_tests.py,axi_test_lib.py,axi_result_verifier.py,generate_manifest.py,golden/,manifest.json,schema_constants.json}` |
| Python 单测 | `tests/pyunit/ai_mesh/`（4 文件，conftest 强制封闭 node-id 集合，禁止 skip） |

### 3.2 Mesh IR phase：未开始（本阶段的实施对象）

### 3.3 UCIe / CPU fabric：无依赖

`grep -rli "ucie" src/ configs/` 零命中。本阶段（mock AXI）不触碰 `AxiGarnetBridge`/Ruby/Garnet。

## 4. 真实 build/test 命令

| 目的 | 命令 |
|---|---|
| 构建 | `scons build/AXI_MESH/gem5.opt -j4` |
| GTest 构建+运行 | `scons build/AXI_MESH/<src.rel.path>.test.opt -j4` → 直接执行该二进制（参照 `run_axi_unit_tests.py`） |
| AXI E2E | `build/AXI_MESH/gem5.opt configs/example/axi_garnet_test.py --help`（无 ISA、`Root(full_system=False)`、退出原因断言） |
| 既有 pyunit | `python3 -m pytest tests/pyunit/ai_mesh -q`（封闭集合，新测试禁止加入此目录） |

Mesh IR Python 测试将放 `util/mesh_ir/tests/`（spec §10 目录合同），与封闭集合隔离。新命令登记于 `docs/ai_mesh/mesh_ir_test_commands.md`（Gate 1 交付）。

## 5. Canonical opcode mapping（Gate 0 对齐）

- 仓库无既有 Mesh IR opcode 实现 → 无历史命名冲突，无需 mapping 表。
- 规范命名直接采用：`DMA_P2P_PUSH`、`RECV_WAIT`、`REPEAT`（Mesh IR spec §5.9 闭集 20 opcode、Dummy Core spec §4.1 同一闭集，两 spec 一致）。
- `DIGEST_ONLY` 不是 opcode：它是 MoE 数据安装 mode（`FUNCTIONAL_BYTES/DIGEST_ONLY/VALIDITY_ONLY`，Dummy Core spec §12/§15 上下文），属于 MoE 阶段，本阶段不实现。
- Engine 映射按 Dummy Core spec §4.3（CONTROL/DMA_READ/DMA_WRITE/TENSOR/VECTOR/REDUCE），opcode-engine 不匹配即 loader 拒绝。

## 6. 本阶段 mock AXI 边界（已获用户确认）

`MockAxiTransport` 为独立 SimObject：HBM/peer-SRAM 功能字节拷贝 + 解析时延 `f(bytes,bw,burst)` + 逐命令 read/write/P2P 字节记账（traffic oracle 对账）。`DmaDescriptor` 接口按 Mesh IR spec §7.1/§7.2 设计，Gate 2 替换为真实 `AxiGarnetBridge` 时接口不变。交付名为 "Dummy Core runtime prototype"（Dummy Core spec §19 第 22 条）。

## 7. Gate 1 精确文件清单

```
util/mesh_ir/
  pyproject.toml
  mesh_ir/__init__.py
  mesh_ir/abi/{mesh_ir_abi.yaml,generate_abi.py,encoder.py,decoder.py,verifier.py}
  mesh_ir/builder.py            # 手写 golden program 构造 API（含 traffic oracle）
  tests/{unit,negative,golden,integration}/
src/dev/ai_mesh/
  SConscript                    # PROTOCOL=='AXI_MESH' guard（参照 src/mem/axi/SConscript）
  AiMesh.py                     # SimObject 声明
  generated/mesh_ir_abi.hh      # generate_abi.py 产物（schema SHA-256 内嵌）
  mesh_ir_verifier.{hh,cc}      # C++ 独立 verifier（与 Python 共享 golden vectors）
  mesh_program_loader.{hh,cc}
  command_rom.hh
  tensor_sram.{hh,cc}
  mesh_dummy_core.{hh,cc}       # scheduler/scoreboard/engine timing/digest
  tensor_dma_engine.{hh,cc}     # DmaDescriptor + burst splitter（spec §7.2 公式）
  mock_axi_transport.{hh,cc}
  mesh_dispatcher.{hh,cc}
  *.test.cc                     # GTest
configs/example/ai_mesh/
  run_mesh_program.py           # Root(full_system=False) + 退出原因断言（参照 axi_garnet_test.py）
  golden_single_core.py、golden_dual_core.py
tests/gem5/ai_mesh/
  mandatory_case_manifest.yaml  # 全量 150-ID 注册表 + earliest_gate
  run_manifest_selector.py
```

## 8. Gate 1 最小测试清单（§17.2 非 AXI 子集）

1. 全部 20 opcode decode/engine 映射；unknown opcode/opcode-engine mismatch fail closed（§17.2.1-2）
2. Command 状态机非法跳转稳定错误码（§17.2.3）
3. event SSA：单 producer/多 consumer、barrier arrival 计数、跨 instance event ID 不串扰（§17.2.5-6）
4. admit/engine/SRAM queue 满产生 backpressure 不 drop（§17.2.7）
5. ready 仲裁在固定输入下确定（§17.2.8）
6. GEMM/vector/reduce/softmax/norm exact cycle golden、zero-size op ≥1 控制周期、乘法 overflow 拒绝（§17.2.9-11）
7. compute digest 对 tick/地址/queue 深度不敏感、对 opcode/shape/attrs 敏感（§17.2.12-13）
8. `reference_compute=true` 启动失败（§17.2.14）
9. SRAM bank/port/仲裁、allocation bounds/lifetime（§17.2.15-16）
10. lifecycle stream 合同：恰一 BEGIN/END、每 core 恰一 reachable HALT（§17.2.27）
11. `RepeatAttrV1` wire golden、count=1/3 generation 映射、非法 subrange 拒绝（§17.2.35 非 P2P 部分）
12. manifest selector：earliest_gate<=1 21 项 PASS 0 skip

DMA completion 语义（§17.2.17-25）属 Gate 2（真实 AXI）；本阶段仅验证 mock transport 字节记账与时延推进。

## 9. Unresolved 问题

- P0：无。
- P1：`tests/pyunit/ai_mesh/conftest.py` 封闭集合使 Mesh IR 测试必须独立于该目录（已按 spec 目录合同规避）；`src/doc/ai_mesh/` 未入库，需随首个 commit 提交。

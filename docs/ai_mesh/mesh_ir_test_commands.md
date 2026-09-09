# Mesh IR / Dummy Core Test Commands

All commands run from the repository root on the `AXI_MESH` build. Every
entry below was executed during its phase and must stay green.

## Build

```bash
scons build/AXI_MESH/gem5.opt -j8
scons build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt -j8
scons build/AXI_MESH/dev/ai_mesh/mesh_splitter.test.opt -j8
```

## ABI schema SSOT

```bash
# regenerate Python/C++/docs artifacts from mesh_ir_abi.yaml; --check is
# idempotent and must exit 0 (committed artifacts are up to date)
python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check
```

## Agent protocol ABI SSOT (Gate 3)

```bash
python3 util/mesh_ir/mesh_ir/abi/generate_agent_abi.py --check
# cross-language agent protocol golden images
cd util/mesh_ir && python3 -m mesh_ir.cli emit-agent-golden \
    --out ../../src/dev/ai_mesh/generated/agent_golden.inc
./build/AXI_MESH/dev/ai_mesh/agent_protocol.test.opt --gtest_color=no
```

## Gate 3 protocol runtime

```bash
python3 tests/gem5/ai_mesh/gate3/run_preimplementation.py
python3 tests/gem5/ai_mesh/gate3/runtime_contract.py
python3 tests/gem5/ai_mesh/gate3/validate_observation.py <canonical-observation.json>
python3 -m pytest \
    util/mesh_ir/tests/unit/test_agent_protocol.py \
    util/mesh_ir/tests/unit/test_gate3_oracle.py \
    util/mesh_ir/tests/integration/test_gate3_e2e_prefix.py \
    util/mesh_ir/tests/integration/test_gate3_protocol_regressions.py -q
./build/AXI_MESH/dev/ai_mesh/agent_ring.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/agent_submission.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_axi_transfer.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_completion_ledger.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_fatal_reducer.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/agent_protocol_validation.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_msi_id_pool.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_irq_commit_queue.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/gate3_sq_intake_ledger.test.opt --gtest_color=no
```

`runtime_contract.py` is the implementation exit criterion. Protocol semantics
are indexed by `src/doc/ai_mesh/DUMMY_AI_CORE_AGENT_CODEX_SPEC.md`; executable
cases come from `mesh_ir.gate3_contract.GATE3_CASES` and
`mandatory_case_manifest.yaml`. The observation schema is
`schemas/ai_mesh/gate3_observation_v1.schema.json`.

Fatal cut timing and ACK watermark regressions are indexed by
`test_msi_response_preserves_fatal_cut_state`,
`test_fatal_ack_snapshot_uses_npu_target_watermark`, and
`test_retirement_at_fatal_edge_preserves_tick_start_ownership` in
`util/mesh_ir/tests/integration/test_gate3_protocol_regressions.py`;
ledger lifecycle coverage is in
`src/dev/ai_mesh/gate3_completion_ledger.test.cc`.

## Python unit / negative / golden / integration

```bash
python3 -m pytest util/mesh_ir/tests -q
```

## C++ unit tests

```bash
./build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/mesh_splitter.test.opt --gtest_color=no
```

## AXI read-return arbitration

```bash
scons build/AXI_MESH/mem/axi/axi_ordering.test.opt -j4
build/AXI_MESH/mem/axi/axi_ordering.test.opt --gtest_filter='AxiReadArbitrationTest.*:AxiOrderingTest.*'
```

The arbitration contract is defined in
`src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md` section 5.4. Its regression cases
are `AxiReadArbitrationTest` in `src/mem/axi/axi_ordering.test.cc`; test
registration is indexed by `tests/gem5/axi_garnet/axi_test_lib.py`.

## Golden program artifacts

```bash
cd util/mesh_ir
for p in single dual fill repeat poison dma_edge dma_error dma_write_error \
         dma_fence dma_pin dma_shapes zero_dma fence_scopes cross_error \
         repeat_error p2p_reuse; do
    python3 -m mesh_ir.cli build --program $p \
        --arch ../../configs/example/ai_mesh/arch/mesh_1x2.yaml --out /tmp/ai-mesh-golden/$p
done
# cross-language pins embedded into the C++ test tree
python3 -m mesh_ir.cli emit-cpp-golden \
    --arch ../../configs/example/ai_mesh/arch/mesh_1x2.yaml \
    --out ../../src/dev/ai_mesh/generated/golden_mshb.inc
```

Golden semantic SHAs for all nine programs are pinned by
`util/mesh_ir/tests/golden/golden_program_shas.json`.

## Golden E2E (gem5 event loop, mock AXI)

```bash
G=configs/example/ai_mesh/run_mesh_program.py
./build/AXI_MESH/gem5.opt -d /tmp/mesh-single $G --program single --mesh-program-dir /tmp/ai-mesh-golden/single
./build/AXI_MESH/gem5.opt -d /tmp/mesh-dual   $G --program dual   --mesh-program-dir /tmp/ai-mesh-golden/dual
./build/AXI_MESH/gem5.opt -d /tmp/mesh-fill   $G --program fill   --mesh-program-dir /tmp/ai-mesh-golden/fill
./build/AXI_MESH/gem5.opt -d /tmp/mesh-repeat $G --program repeat --mesh-program-dir /tmp/ai-mesh-golden/repeat \
    --traffic-multiplier 3 --digest-repeat-count 3
```

The spec-driven selector owns per-subcase `summary.json` and `junit.xml`,
the aggregate JUnit, and mandatory results. Child reporting and artifact
requirements are indexed by the mandatory manifest section below.

Per-command conservation (`commands_issued == commands_completed`,
`live_commands == 0`) is asserted unconditionally on every run;
`--assert-all-done` additionally checks that every command of every stream
reached DONE.  Content
flow is verified against an independent Python digest oracle: LOAD rows
must match the zero-initialized HBM digest, FILL rows the pattern digest,
and STORE/P2P digests must differ from the zero digest.  Additional
assertion args: `--instances N` (same CommandROM, fresh per-instance
state), `--dma-queue-depth N` (finite-queue backpressure),
`--watchdog-ticks N` (progress watchdog; must exceed the longest single
command latency), `--expect-ticks`, `--expect-digest`,
`--expect-digests-differ`, `--expect-fatal <marker>`, `--admit-window N`,
`--expect-gemm-cycles N`, `--expect-sram-service-min N`,
`--assert-all-done`.  The `poison` program faults
`E_TENSOR_NOT_RESIDENT` at issue (compute reads an allocation with no
committed producer).

Each run must print `MESH_E2E_PASS`; the config reconciles the mock
transport per-descriptor byte accounting against `expected_traffic.json`
and exits non-zero on any mismatch.

## Real AXI-over-Garnet E2E (Gate 2)

```bash
RG=configs/example/ai_mesh/run_mesh_dma_garnet.py
./build/AXI_MESH/gem5.opt $RG --case dma_basic    --mesh-program-dir /tmp/ai-mesh-golden/single
./build/AXI_MESH/gem5.opt $RG --case p2p_basic    --mesh-program-dir /tmp/ai-mesh-golden/dual
./build/AXI_MESH/gem5.opt $RG --case dma_edge     --mesh-program-dir /tmp/ai-mesh-golden/dma_edge
./build/AXI_MESH/gem5.opt $RG --case delayed_b    --mesh-program-dir /tmp/ai-mesh-golden/single
./build/AXI_MESH/gem5.opt $RG --case p2p_delayed  --mesh-program-dir /tmp/ai-mesh-golden/dual
./build/AXI_MESH/gem5.opt $RG --case read_error   --mesh-program-dir /tmp/ai-mesh-golden/dma_error
./build/AXI_MESH/gem5.opt $RG --case write_error  --mesh-program-dir /tmp/ai-mesh-golden/dma_write_error
./build/AXI_MESH/gem5.opt $RG --case fence        --mesh-program-dir /tmp/ai-mesh-golden/dma_fence
./build/AXI_MESH/gem5.opt $RG --case pin          --mesh-program-dir /tmp/ai-mesh-golden/dma_pin
./build/AXI_MESH/gem5.opt $RG --case constrained  --mesh-program-dir /tmp/ai-mesh-golden/dual
./build/AXI_MESH/gem5.opt $RG --case sram_persist --mesh-program-dir /tmp/ai-mesh-golden/single
./build/AXI_MESH/gem5.opt $RG --case p2p_persist --mesh-program-dir /tmp/ai-mesh-golden/dual
./build/AXI_MESH/gem5.opt $RG --case dma_shapes  --mesh-program-dir /tmp/ai-mesh-golden/dma_shapes
```

Each run must print `MESH_DMA_GARNET_PASS`.  Architecture, components and
the conservation/quiescence contract live in
`src/doc/ai_mesh/DUMMY_AI_CORE_AGENT_CODEX_SPEC.md` (Gate 2) and the
result-JSON writer `src/dev/ai_mesh/mesh_dispatcher.cc`.  Component map:
`src/dev/ai_mesh/AiMesh.py`.

Case-to-assertion map (checks are named in
`configs/example/ai_mesh/run_mesh_dma_garnet.py::CHECKS`):

| case | proves |
|---|---|
| dma_basic | E2E-A LOAD→GEMM→STORE; content flow; drain |
| p2p_basic | E2E-B P2P commit + RECV_WAIT/REDUCE/STORE ordering |
| dma_edge | DC-17 byte-level round trip for all edge sizes |
| delayed_b | DC-21 delayed middle B reordering + full retire |
| p2p_delayed | DC-22 RECV_WAIT after last committed P2P byte |
| read_error | DC-23/30 DECERR drain, downstream cancel |
| write_error | DC-25/30 SLVERR drain, committed prefix kept |
| fence | DC-28 fence waits only pre-fence transactions |
| pin | DC-32 in-flight DMA pins its allocation |
| constrained | DC-26/31 shallow-buffer backpressure + full drain |
| sram_persist | DC-33 cross-instance SRAM/state isolation |
| p2p_persist | DC-33 every instance re-observes its P2P transfers |
| dma_shapes | DC-17 PREFETCH+FILL+multi-row strided P2P/STORE+cross-core event |
| read_outstanding_window | strict AXI read window slides on RLAST (spec 5.6) |
| load_saturation_* | perf benchmark: dual-core HBM LOAD saturation (contiguous/multi_tensor/strided) |

## Dual-lane router

规格与资源合同见 [Router Spec](dual_lane_router_spec.md)，验证范围与证据见
[复核记录](dual_lane_router_review.md)。仲裁状态入口为
[LaneArbiter](../../src/mem/ruby/network/garnet/LaneArbiter.hh)。

```bash
scons build/AXI_MESH/gem5.opt \
    build/AXI_MESH/mem/ruby/network/garnet/lane_arbitration.test.opt \
    build/AXI_MESH/mem/ruby/network/garnet/dual_lane_selector.test.opt -j8
build/AXI_MESH/mem/ruby/network/garnet/lane_arbitration.test.opt
build/AXI_MESH/mem/ruby/network/garnet/dual_lane_selector.test.opt
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_dual_lane_checks.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_verify.py
python3 tests/gem5/ai_mesh/run_dual_lane_checks.py \
    --program-dir <built load_saturation_contiguous program> \
    --workdir <new-or-empty-directory>
```

真实 gem5 验收的配置、事件字段及断言由
[run_dual_lane_checks.py](../../tests/gem5/ai_mesh/run_dual_lane_checks.py)
定义；产物包含独立保存的单/双 lane 命令、配置、日志、数据检查结果和
`checks.json`。`GarnetDualLane` 开关输出选择、发送、合并 grant 和资源快照。
构建完成后再启动仿真。

5×5 tensor 实验通过 profile 的 `network.dual_lane` 布尔字段启用双 lane；
[实验入口](../../configs/example/ai_mesh/run_mesh_experiment.py) 和
[校验器](../../util/mesh_ir/mesh_ir/experiment/verify.py) 读取同一 profile。
物理链路统计保留 lane 身份，按逻辑路由汇总后与 workload oracle 核对。
LOAD 图及运行证据见 [实验报告](../../src/doc/ai_mesh/goal_mesh_outstanding_buffer_experiment.md)。

## Load saturation sweep (perf, not a gate)

```bash
python3 tests/gem5/ai_mesh/run_load_sweep.py --workdir "$(mktemp -d /tmp/load-sweep.XXXXXX)"
```

The command and measurement fields are defined in
`tests/gem5/ai_mesh/run_load_sweep.py`. Selection is per workload and latency;
throughput uses core cycles, with the clock period retained in the result.
Pareto compares throughput and router credit stall VC-cycles. Adapter blocked
cycles, target blocked cycles, NI stalls and MessageBuffer queue-time ticks
remain separate. Completion-time skew measures imbalance; it is not a
fixed-window service fairness measurement.

Runtime evidence comes from `MeshDispatcher::writeConservationJson` and
`AxiGarnetBridge::burstTimings`. Regressions:
`util/mesh_ir/tests/unit/test_load_sweep.py`,
`util/mesh_ir/tests/unit/test_read_outstanding_config.py`, and
`util/mesh_ir/tests/integration/test_axi_outstanding_runtime.py`.

## Mandatory manifest selector (Dummy Core Gates)

```bash
python3 tests/gem5/ai_mesh/validate_manifest.py
python3 -m pytest \
    util/mesh_ir/tests/integration/test_gate12_harness_contract.py \
    util/mesh_ir/tests/integration/test_selector_spec_contract.py \
    util/mesh_ir/tests/integration/test_runtime_rejections.py -q
gate1_workdir="$(mktemp -d /tmp/ai-mesh-gate1.XXXXXX)"
gate2_workdir="$(mktemp -d /tmp/ai-mesh-gate2.XXXXXX)"
python3 tests/gem5/ai_mesh/run_manifest_selector.py \
    --gate 1 --workdir "$gate1_workdir"
python3 tests/gem5/ai_mesh/run_manifest_selector.py \
    --gate 2 --workdir "$gate2_workdir"
```

`mandatory_case_manifest.yaml` is the only writable acceptance registry;
`validate_manifest.py` never generates or repairs it. The selector requires
a new or empty workdir and validates §17.9 typed execution, exact child
environment, child report, RunManifest, invariant/traffic artifacts,
post-write summary availability, JUnit bijection, and result-to-summary
replay. Gate 1 selects 21 logical IDs and 45 subcases; Gate 2 selects 37
logical IDs and 71 subcases.

## AXI Garnet regression (must not regress)

```bash
python3 tests/gem5/axi_garnet/run_axi_unit_tests.py \
    --build-dir build/AXI_MESH --variant opt \
    --manifest tests/gem5/axi_garnet/manifest.json
python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
    --gem5 build/AXI_MESH/gem5.opt --suite quick
python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
    --gem5 build/AXI_MESH/gem5.opt --suite full
```

## Gate scope authority

`mesh_ir.acceptance.GATE_EXPRESSIONS` is the only Gate membership authority.
Gate 3 runtime coverage derives from `mesh_ir.gate3_contract` and the
canonical manifest; identifiers from future Gates must not be judged by a
repository-wide text search because the manifest and specifications
intentionally retain their placeholders.

Acceptance tests for `reference_compute` and poison paths are indexed by
`mandatory_case_manifest.yaml` and implemented in
`util/mesh_ir/tests/integration/test_runtime_rejections.py`.

## 5×5 outstanding / router input buffer experiment

The experiment contract and implementation boundaries are indexed by
[outstanding_router_fifo_experiment_design.md](outstanding_router_fifo_experiment_design.md).
The measured configurations, frozen execution budget, raw artifacts and
reproduction commands are indexed by
[outstanding_router_fifo_experiment_report.md](outstanding_router_fifo_experiment_report.md).
This experiment is separate from the two-core load saturation sweep.

```bash
python3 -m pytest util/mesh_ir/tests/unit/test_mesh_experiment_*.py -q
python3 -m pytest \
    util/mesh_ir/tests/integration/test_mesh_experiment_topology.py \
    util/mesh_ir/tests/integration/test_mesh_experiment_observer.py \
    util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py -q
build/AXI_MESH/mem/axi/synthetic_hbm_backend.test.opt
build/AXI_MESH/mem/axi/axi_simple_memory.test.opt
build/AXI_MESH/mem/ruby/network/garnet/garnet_input_capacity.test.opt
```

The runner is `tests/gem5/ai_mesh/run_mesh_experiment.py`; its `smoke`, `run`,
`sweep`, `resume`, `verify` and `analyze` subcommands share
`util/mesh_ir/mesh_ir/experiment/`. The real gem5 configuration is
`configs/example/ai_mesh/run_mesh_experiment.py`, and non-search resources
are defined in `configs/example/ai_mesh/experiments/fixed_profile.json`.

按 vnet 选择 XY/YX 的配置入口为
[GarnetNetwork](../../src/mem/ruby/network/garnet/GarnetNetwork.py) 的 `yx_vnets`，
实验 profile 通过 `network.yx_vnets` 传入。
路径实现见 [RoutingUnit](../../src/mem/ruby/network/garnet/RoutingUnit.cc)，
独立逐链路 oracle 见 [workload.py](../../util/mesh_ir/mesh_ir/experiment/workload.py)。
写 tensor 对照的结果和复现输入见
[实验报告](../../src/doc/ai_mesh/goal_mesh_outstanding_buffer_experiment.md#写-tensor-yx-路由对照)。
读 XY、写 YX 的混合负载对照见
[MIXED 实验](../../src/doc/ai_mesh/goal_mesh_outstanding_buffer_experiment.md#mixed-读-xy-写-yx-路由对照)。
固定读 XY、写 YX 的单/双 lane 全套实验按
[执行 Spec](dual_lane_xy_yx_experiment_spec.md) 组织矩阵、容量搜索验收与结果对照。

```bash
python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_mesh_experiment_workload.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_runner.py \
    util/mesh_ir/tests/unit/test_mesh_experiment_verify.py
python3 -m pytest -q \
    util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py
```

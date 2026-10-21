# Mesh IR / Dummy Core Test Commands

Torch frontend usage and source indexes are in [`torch_frontend.md`](torch_frontend.md).

Torch gates and Agent Serving gates are separate acceptance tracks. The ABI 2.0 integration status and owners are indexed in [`abi2_integration.md`](abi2_integration.md).

## Torch package contract

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate1_package_contract.py
```

## Torch frontend Gate 1

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate1_canonical_diagnostics.py \
  util/mesh_ir/tests/unit/test_gate1_arch_config.py \
  util/mesh_ir/tests/unit/test_gate1_graph_ir.py \
  util/mesh_ir/tests/unit/test_export_archive_streaming.py \
  util/mesh_ir/tests/integration/test_gate1_real_frontend.py
```

Qwen3 frontend primitives (the eight operator classes the full model
export needs; requires the torch export environment python):

```bash
PYTHONPATH=util/mesh_ir .tmp/worktrees/torch-frontend/.tmp/qwen-export-env/bin/python -m pytest -q \
  util/mesh_ir/tests/integration/test_gate1_qwen_primitives.py
```

## Torch address and traffic foundation

Contracts are indexed by [`architecture.py`](../../util/mesh_ir/mesh_ir/architecture.py) and [`traffic.py`](../../util/mesh_ir/mesh_ir/traffic.py).

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate2_shared_attrs.py \
  util/mesh_ir/tests/unit/test_gate2_address_bindings.py \
  util/mesh_ir/tests/unit/test_control_initiators.py \
  util/mesh_ir/tests/unit/test_gate2_fabric_traffic.py
```

## Torch Kernel and SRAM foundation

The dependency and allocation contracts are indexed by the [`analysis`](../../util/mesh_ir/mesh_ir/analysis) package and [`kernel_verify.py`](../../util/mesh_ir/mesh_ir/ir/kernel_verify.py).

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate2_kernel_ir.py \
  util/mesh_ir/tests/unit/test_gate2_kernel_work.py \
  util/mesh_ir/tests/unit/test_gate2_dependency_sram.py
```

## Torch Scheduled variant and completion checks

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate2_scheduled_variant_dependencies.py \
  util/mesh_ir/tests/unit/test_gate2_scheduled_dma.py \
  util/mesh_ir/tests/unit/test_gate2_scheduled_barriers.py
```

## Torch selected placement geometry

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate2_placement_geometry.py
```

## Torch backend composition

Focused composition contract:

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/unit/test_gate2_compiler.py
```

Complete real-model backend integration:

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/integration/test_gate2_backend_compiler.py
```

## Torch public compilation

Use new output paths on a filesystem that supports atomic no-replace directory rename. These commands use `/tmp`:

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m mesh_ir.export_and_compile \
  --module examples.tiny_mlp:create_model \
  --inputs util/mesh_ir/examples/tiny_mlp_inputs.json \
  --arch configs/example/ai_mesh/arch/mesh_2x2.yaml \
  --config util/mesh_ir/examples/tiny_mlp_compile.yaml \
  --output /tmp/ai-mesh-tiny-mlp-export

PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m mesh_ir.compile \
  --exported-program /tmp/ai-mesh-tiny-mlp-export/exported_program.pt2 \
  --arch configs/example/ai_mesh/arch/mesh_2x2.yaml \
  --config util/mesh_ir/examples/tiny_mlp_compile.yaml \
  --output /tmp/ai-mesh-tiny-mlp-load
```

All commands run from the repository root. The runtime commands below use
the `AXI_MESH` build; Python foundation tests do not require a gem5 build.

## Build

The `AXI_MESH` build requires libisl headers, which the system does not carry;
export the environment recorded in
`.tmp/docs/torch-stage3-isl-environment/` (extracted `.deb` tree under
`.tmp/torch-isl-dev-package/`) before running scons: `CCFLAGS_EXTRA`,
`LIBRARY_PATH` and `PKG_CONFIG_PATH` are the whitelisted scons variables that
carry the extracted tree into the probes and the link.

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

## Gate 4 replay-plan business loop

```bash
scons build/AXI_MESH/dev/ai_mesh/agent_workload_manager.test.opt -j8
./build/AXI_MESH/dev/ai_mesh/agent_workload_manager.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/agent_request_source.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/agent_plan_image.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/agent_plan_codec.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/control_trigger_coordinator.test.opt --gtest_color=no
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_gate4_single_user_fixtures.py
python3 -m pytest -q \
    util/mesh_ir/tests/integration/test_gate4_plan_mode.py \
    util/mesh_ir/tests/integration/test_gate4_single_user.py \
    util/mesh_ir/tests/integration/test_gate4_control.py \
    util/mesh_ir/tests/integration/test_gate4_matrix.py \
    util/mesh_ir/tests/integration/test_gate4_determinism.py \
    util/mesh_ir/tests/integration/test_gate4_oracle_tamper.py \
    util/mesh_ir/tests/integration/test_gate4_config_effect.py
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_gate4_oracle.py
python3 tests/gem5/ai_mesh/gate4/runtime_contract.py
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 4 \
    --workdir "$(mktemp -d /tmp/ai-mesh-gate4-XXXXXX)"
```

Single-user first-pass/compile-repair/test-repair/repair-limit fixtures live in
`tests/gem5/ai_mesh/fixtures/gate4/`; rebuild the checked-in `.bin` images with
`PYTHONPATH=util/mesh_ir python3
tests/gem5/ai_mesh/fixtures/gate4/build_gate4_single_user_images.py`. The
business FSM, host resource scheduling and object lifecycle are owned by
[src/dev/ai_mesh/agent_workload_manager.hh](../../src/dev/ai_mesh/agent_workload_manager.hh);
plan-mode wiring into the real AXI protocol pump is in
[src/dev/ai_mesh/agent_axi_driver.cc](../../src/dev/ai_mesh/agent_axi_driver.cc)
and [src/dev/ai_mesh/agent_axi_driver_plan.cc](../../src/dev/ai_mesh/agent_axi_driver_plan.cc).

The step-5 matrix suite `test_gate4_matrix.py` covers the 12-user scale run
(`agent_plan_image_twelve_user.bin`, 6 first-pass + 3 compile-repair +
3 test-repair users, token/slot/queue pressure, aging reservation, determinism
x3), the agent-runner regression for the HOST-9/HOST-19 argv
(`run_gate4_agent.py` + twelve-user runtime config with
`--host-compute-tokens 6 --host-aging-threshold-ns 5000000`: completes
QUIESCENT with 12 terminals under a 120 s wall bound), token pools below the
plan requirement failing fast at the capacity plan (agent runner) and at
`AgentWorkloadManager` construction (protocol runner), task-count and tick
cutoffs (`--stop-after-completed-tasks`,
`--stop-accepting-at-tick`), the recoverable NPU output fault
(`--inject-output-b-error`, single `AXI_ERROR` CQ with
`DETAIL_IN_CQ|E_AXI_RESPONSE` and the committed prefix in the
`output_b_error_prefix_bytes` metric), and the injectable host local fault
(`--host-fault-site object_produce|object_read` with task/round selection,
`INFRA_FAILED` terminal, `infra_failed_tasks` metric, HOST_FAULT facts).
Host-local fault sites and dispositions are frozen in
`util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml`
(`host_local_fault_site_v1`, `E_HOST_LOCAL_OBJECT`,
`metadata_flags.SESSION_ADMITTED`).

The config-integrity suite `test_gate4_config_effect.py` proves the validated
`agent_runtime_config_*.yaml` is the single effective configuration: SimObject
params (ring depths/bases, MSI/NPU/proxy bases, host window, clock domain,
service slots/tokens/fraction, local-I/O, kv/MSI capacities, stop knobs) are
derived from it in `gate4_runtime.config_hardware`/`assemble`, config edits
change facts, the surrogate registry is plan-validated at load
(`validate_surrogate_profiles`) and per wire request at frontend admission
(`FullContextSurrogateExecutor::accept`, `E_WORKLOAD_PLAN_MISMATCH`/
`E_OUTPUT_CAPACITY`), cutoff `stop_accepting_new_tasks_at_tick` keeps null vs
0 distinct via `stop_accepting_enabled`, and arena allocation accounts for
alignment padding (`ArenaAllocator.allocate`). CLI synthetic-host-service
knobs (`--host-compute-tokens`, `--host-service-queue-depth`,
`--host-aging-threshold-ns`) override the effective document before any
derivation (`run_gate4_agent._apply_service_overrides`); a token knob names
the effective pool (availability fraction reset to 1.0), so the agent and
protocol runners agree on knob semantics and the capacity preflight sees the
pool that will actually run.

The acceptance harness is the gate-4 analogue of Gate 3's: subcases are
declared in `mesh_ir.gate4_contract.GATE4_CASES` (with
`gate4_coverage_gaps()` listing the not-yet-implementable sub-verifications),
the independent Python oracle over facts TSV plus immutable plan fixtures is
`mesh_ir.gate4_oracle.Gate4RunOracle`, the total config entry writing real
child artifacts (gate4_observation/traffic/invariants/child_report) is
[run_gate4_agent.py](../../configs/example/ai_mesh/run_gate4_agent.py) over a
generated `agent_runtime_config_*.yaml`, and `runtime_contract.py` is the
implementation exit criterion. The facts TSV parser is
`mesh_ir.gate3_oracle.parse_facts_tsv`.

Gate 4 control-plane coverage: the `agent_plan_image_ctrl_*.bin`
scenarios pair the three-user workload with a control plan (live CANCEL via
CancelJoin, late CANCEL resolving standalone ALREADY_TERMINAL, a late-anchor
CANCEL whose join resolves TARGET_SUCCESS_WINS/TARGET_ERROR_WINS, and a
SCENARIO_START RELEASE_SESSION resolving NOT_FOUND). The single-user
`agent_plan_image_su_output_cancel.bin` scenario pairs the 80 KiB output
workload with a 4 KiB publish chunk and an `AFTER_FIRST_OUTPUT_CHUNK` CANCEL
(registered subcase `PROTO-22/cancel_output_chunk`): output publication is
chunk-bounded, the in-flight request parks for control intake between output
segment completions, issued chunks drain while un-issued chunks are
suppressed, and the CANCELLED CQ carries `PARTIAL_OUTPUT` with the committed
prefix in `output_cancel_prefix_bytes`; CancelJoin advances business only at
join resolution. Trigger scheduling is owned by
[src/dev/ai_mesh/control_trigger_coordinator.hh](../../src/dev/ai_mesh/control_trigger_coordinator.hh);
the control command wire codec is `buildControlParameter` in
[src/dev/ai_mesh/agent_plan_codec.hh](../../src/dev/ai_mesh/agent_plan_codec.hh);
CancelJoin leg caching/resolution lives in `src/dev/ai_mesh/cancel_join.hh`
plus `AgentAxiDriver::resolveCancelJoin`.

The CANCEL lifecycle oracle (`Gate4RunOracle`) recomputes both legs from plan
identity plus independent facts instead of trigger anchors: the join decision
is `PUBLICATION_COMMIT(target) ≤ LOCAL_VISIBLE(command) < CQ_CONSUME(target)`
(the driver's `acceptedGenerateRequests`/`consumedGenerateRequests` window),
and the command leg is `SQ_CONSUME` order plus the NPU terminal latch
(`GENERATE_TERMINAL_LATCHED`, emitted by
`NpuServingFrontend::latchGenerateTerminal` in
[src/dev/ai_mesh/npu_serving_frontend.cc](../../src/dev/ai_mesh/npu_serving_frontend.cc))
relative to the CANCEL's own `SQ_CONSUME`. `TERMINAL_READY`/`CQ_ASSIGN` are
publication-lagged and must not be used as that boundary. Every planned
GENERATE is checked stage by stage (`PlannedWalk._require_generate_lifetimes`):
a CQ assignment requires the terminal latch at or before it, a CQ consume
requires the assignment at or before it, and a request with neither fact is a
legal cutoff prefix.
`test_gate4_control.py` drives the real-join scenarios
(`PROTO-21/target_success_wins_late_anchor`, `.../target_error_wins_late_anchor`)
and `test_gate4_oracle_tamper.py` rejects tampered leg status, winner,
join/standalone classification and latch facts.

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
[实验架构解析器](../../util/mesh_ir/mesh_ir/experiment/workload.py) 生成唯一
`ArchManifest`，[实验入口](../../tests/gem5/ai_mesh/run_mesh_experiment.py) 通过
`architecture_document` 持久化该架构，[校验器](../../util/mesh_ir/mesh_ir/experiment/verify.py)
读取同一持久化架构。
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

## Gate 6 Agent serving V1 (R1)

feature bit、六个 conditional-required section 与六个 record 布局由
[mesh_ir_abi.yaml](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml) 单点定义，
见上文 "ABI schema SSOT"；profile key 双 projection、语义 fixture 与
PUBLISH surrogate DAG 在 `mesh_ir.serving_profiles` / `mesh_ir.serving_programs`：

```bash
python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check
python3 -m pytest util/mesh_ir/tests/unit/test_serving_abi.py \
    util/mesh_ir/tests/unit/test_serving_profile.py -q
python3 tests/gem5/ai_mesh/fixtures/gate6/build_gate6_serving_images.py
scons build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt -j8
./build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt \
    --gtest_filter='MeshBinaryServingTest.*:MeshBinaryFullViewTest.*' \
    --gtest_color=no
```

仓内跨语言 golden 为 `tests/gem5/ai_mesh/fixtures/gate6/` 下的
`serving_min.mshb`（三 phase 单成员）与 `serving_fullview.mshb`（同核 `[0,0]`
独立 view 双成员，含独立 fill allocation 与 wait 链）及各自
`*_expected.json`；Python reader 与 C++ `MeshBinaryServingTest` /
`MeshBinaryFullViewTest` 解码同一映像并逐项比对，篡改负例各自断言固定 detail code。exact selector、
path closure、Host/KV interval oracle 与 serving capacity 派生
（`mesh_ir.serving_profiles`，C++ 镜像 `verifyPathClosure`/`verifyMemberIo`/
`servingCapacityRequired`）在两侧同时执行；canonical semantic projection 与
profile key 由 `mesh_serving_projection.{hh,cc}` 在 C++ 重算（与 Python
`canonical_json_bytes` 逐 bit 相同）；
`verify_program(keyed_serving_program(arch), arch)` 是 R1 的 fail-closed 出口；
负例经 `mesh_ir.serving_profiles.capture_preflight` 记录失败阶段、固定
detail/disposition、`admitted=False` 与零副作用账本。

## Gate 6 KV admission core (R2)

唯一 session/KV 状态所有者是
[mesh_kv_manager.hh](../../src/dev/ai_mesh/mesh_kv_manager.hh)（§10.4：record/slot
bitmap/tuple+generation/contract digest/claim/pin/rollback authority/terminal
snapshot/append obligation/eviction/release waiter/tombstone/epoch），唯一 edge
写入口为 `MeshKvManager::commitEdge`。独立 Python oracle 为
`mesh_ir.kv_oracle`（同输入事件序列、不读 C++ 或 runtime 结果）：

```bash
python3 -m pytest util/mesh_ir/tests/unit/test_kv_oracle.py -q
python3 tests/gem5/ai_mesh/fixtures/gate6/build_gate6_kv_traces.py
scons build/AXI_MESH/dev/ai_mesh/mesh_kv_manager.test.opt \
      build/AXI_MESH/dev/ai_mesh/mesh_kv_manager_restore.test.opt \
      build/AXI_MESH/dev/ai_mesh/mesh_kv_manager_trace.test.opt -j8
./build/AXI_MESH/dev/ai_mesh/mesh_kv_manager.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/mesh_kv_manager_restore.test.opt --gtest_color=no
./build/AXI_MESH/dev/ai_mesh/mesh_kv_manager_trace.test.opt --gtest_color=no
```

恢复入口的校验分层（原始字段/容器形状→counter 类型范围→元素标量闭集与宽度→
集合关系→候选态一次安装）在 `kv_oracle._validate_structure` 与 C++
`MeshKvManager::validateStructure` 成对实现；WAITING 的路径语义由冻结 flags 派生
（`kv_types.derived_initial` / `derivedInitial`），序列化字段只是受校验投影。
flags 合法性（未知 bit 与两个 bit 同时置位）由 `kv_types.legal_flags` /
`legalFlags` 单一规则同时服务 admission 与恢复；admission 对非法 flags 保留独立
outcome `FlagCombination`（Python 同名 `FLAG_COMBINATION`），frontend 经单一生产映射
`mesh_kv_manager` 之外的 `npu_serving_frontend_kv_map.{hh,cc}`
（`mapKvAdmissionOutcome`）派生 detail/CQ status，双 bit 组合为
`PARAM_ERROR/E_KV_FLAG_COMBINATION`，ERROR record 仍为 `PROGRAM_ERROR/E_KV_STATE`；已保存 path 必须与纯策略
`_decide_path`/`decidePath`（输入为冻结 flags、冻结 required_cached_tokens 与 prior）
在 CLAIMED/PRESTART 两阶段一致。record 身份宽度与嵌套 `first_error`（tick/code 宽度、
`ErrorSourceKey` 宽度与闭集枚举）在 `KvRecord.gaps` 校验，`ErrorSourceKey.gaps()`
同时服务构造与恢复。

纯值类型与标量规则（枚举、常量、值对象及其 `gaps()`）单一定义在
`util/mesh_ir/mesh_ir/kv_types.py`，状态机 `mesh_ir.kv_oracle` 与测试/夹具从该模块取类型；
C++ 侧测试按 admission/lifecycle、restore 校验、跨语言 golden 重放拆分，
共享 helper 在 `src/dev/ai_mesh/mesh_kv_manager_test_support.hh`。

仓内真实 GEM5 臂：`agent_control_plan_release_live.json` + `agent_plan_image_ctrl_release_live.bin`
（`build_gate4_single_user_images.py` 生成）经
`util/mesh_ir/tests/integration/test_gate4_control.py::test_gate4_release_live_defers_until_owner_drain`
断言 active owner 下 RELEASE waiter 不发提前终态、owner drain 后消费唯一 release result
（`CONTROL_DEFERRED` → `CONTROL_RESOLVE SUCCESS`、tombstone 恰一次、determinism 重复逐字节一致）。

跨语言 golden 为 `tests/gem5/ai_mesh/fixtures/gate6/kv_trace.bin`（13 场景，含
record 容量等待、WAITING→drain、pin PRESTART/STARTED、重复身份 fatal）：每个场景内含
geometry/capacity、初始 persistent 状态、逐 edge 事件序列与 Python oracle
算出的逐 edge 标量投影（决定 + 状态，含字段名）及预期 fatal 边数；
`build_gate6_kv_traces.py` 写盘后重新读回并再跑一次 oracle，保证 wire 布局与模型不分叉。C++
`MeshKvManager.MatchesTheCrossLanguageTrace` 重放同一事件序列并逐标量比对。原型状态
投影 schema 版本为 2；版本不符即拒绝，无旧格式回退。
Gate 3/4 的 surrogate session 状态与 KV record 同表（`MeshKvManager` 是唯一
可变 session 表，`session_record_table.{hh,cc}` 已删除）；真实
PREFILL/DECODE/PUBLISH 的 admission/slot/pin/append 生产接线属 R3。

## Gate 6 serving E2E-D (R3)

R3 当前接受范围、独立证据与后续边界见 [r86 独立复审](../../.tmp/docs/gate6-r3-r86-independent-review.md)；实施状态唯一入口为 [Gate 6 实施计划](../../.tmp/docs/gate6-implementation-plan.md)。

profile 执行绑定与 feature 契约见 [ABI SSOT](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml)。Python ownership
入口为 [execution_view.py](../../util/mesh_ir/mesh_ir/execution_view.py)，C++ 侧为
[mesh_execution_view.hh](../../src/dev/ai_mesh/mesh_execution_view.hh)（scoped
program 无 selector 即 fatal，不退回全 stream 执行）。per-phase fixture 为
`tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb`（PREFILL → 2×DECODE
→ PUBLISH 四个 concrete profile），运行期由
[serving_mesh_executor.hh](../../src/dev/ai_mesh/serving_mesh_executor.hh) 按真实
DMA/instance 回调推进 phase 链；`mesh_result.json` 的 descriptor surface 由
[mesh_dispatcher.cc](../../src/dev/ai_mesh/mesh_dispatcher.cc) 写出。

真实 run 与验收产物由 suite selector 驱动（单一入口；`run_gate6_serving.py` 只在
子进程内运行）：

```bash
python3 -m pytest util/mesh_ir/tests/unit/test_serving_e2e_oracle.py \
    util/mesh_ir/tests/unit/test_gate6_acceptance.py -q
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 6 --id E2E-D \
    --workdir .tmp/gate6-suite
```

`configs/example/ai_mesh/gate6_acceptance.py` 从 `mesh_result.json` 与
`gate6_facts.tsv` 派生 `invariants.json`/`traffic.json`/`child_report.json`：
descriptor 闭包、per-phase KV token/descriptor 账本与终态账本由
`mesh_ir.serving_e2e_oracle` 独立重算（输入为 facts 的 `SERVING_PHASE` 行，
单点定义在 `mesh_ir.gate3_oracle.SERVING_PHASE_FIELDS`），phase 串行性由
逐实例 command observations、join 双条件 tick 次序由 `SERVING_PHASE` 行重算，traffic
由 `program.expected_traffic` 重建；`SERVING_PHASE` 行由运行期
`ServingMeshExecutor::setPhaseFactSink`（frontend 接
`Gate3ObservationRecorder::recordServingPhase`）在每次 phase commit 时落盘。
invariant 名与顺序即 `dummy_core_case_registry.GATE6_CASES` 的 requirements。run manifest 的
GEM5 scenario 由 `run_manifest_selector._gate6_scenario` 从同一 fixture 与
plan 文档派生。真实运行的证据快照保留在
`tests/gem5/ai_mesh/fixtures/gate6/run_evidence/`（`mesh_result.json`、
`gate6_facts.tsv`、`gate6_scenario.json`），验收单测在该快照上通过。

Host backing 读取入口为
[AgentAxiDriver::readGeneratedCodeBacking](../../src/dev/ai_mesh/agent_axi_driver.cc)，
冻结输入期望由
[serving_contract_digest.py](../../util/mesh_ir/mesh_ir/serving_contract_digest.py)
派生。生产 artifact 入口与复审篡改回归见
[gate6_acceptance.py](../../configs/example/ai_mesh/gate6_acceptance.py) 和
[test_gate6_acceptance.py](../../util/mesh_ir/tests/unit/test_gate6_acceptance.py)。

Host-wire 冻结证明反例由
[serving_fault_entry.py](../../tests/gem5/ai_mesh/gate6/serving_fault_entry.py) 的
`--host-wire-mode {control,input_digest,workload_digest,item,session_id,program_id,chunk,deadline}`
驱动（Frontend 证明保持 canonical，仅 Host 镜像变异；`deadline` 臂同步扩
parameter arena 容量以容纳新增 TLV）：digest/workload/item/session/program/deadline
错配断言 `--expect-workload-mismatch`，chunk 值错配断言
`--expect-chunk-mismatch`；两者均要求接纳前拒绝、零 phase/core start/payload、
错误 CQ 被真实 Host 消费且无 infrastructure fatal。参数 TLV 的 wire 语义（type
严格递增、known type flags/size exact、unknown required 拒绝、unknown optional
跳过并记 `skipped_optional_tlvs`、8 B 对齐零 padding、chunk u32+reserved=0）
由 `parameterTlvStructureError` 单一走查入口定义，单测见
[agent_protocol_validation.test.cc](../../src/dev/ai_mesh/agent_protocol_validation.test.cc)；
first-error 顺序依主合同 `RequestValidationSiteV1` 全序（WORKLOAD_PLAN 身份 →
REQUEST_PROFILE_KEY → 绑定 → TLV 结构 → digest → chunk → deadline），交叉
反例（item+chunk）经 `.tmp` gdb 注入探针复跑。

fill 的 instance 生命周期组件入口为
[serving_fill_inputs.test.cc](../../src/dev/ai_mesh/serving_fill_inputs.test.cc)。
KV 物理地址平移与内容不变回归入口为
[test_gate6_timing_ab.py](../../util/mesh_ir/tests/integration/test_gate6_timing_ab.py)
的 `test_kv_physical_binding_moves_axi_addresses_and_preserves_content`；物理 base
覆盖参数见 [run_gate6_serving.py](../../configs/example/ai_mesh/run_gate6_serving.py)。
KV FILL/STORE/LOAD 的冻结内容期望同样由 `serving_contract_digest.py` 派生，并经
`write_gate6_artifacts` 的生产验收入口核对。

§4 的 timing A/B 类由 `util/mesh_ir/tests/integration/test_gate6_timing_ab.py`
覆盖（真实 gem5 两跑）：`run_gate6_serving.py` 的
`--sram-write-bytes-per-cycle-per-bank`（经 `EFFECTIVE_ARCH` 覆盖，模型装配统一取
`EFFECTIVE_ARCH`）放慢 SRAM 写服务后，per-descriptor 字节与 `payload_digest`
逐字节不变、commit tick 与 run terminal tick 后移。determinism 类由
`run_manifest_selector.py --gate 6 --id E2E-D` 重复运行并对
`mesh_result.json`/`gate6_facts.tsv`/`traffic.json` 做逐字节比较取证。

## Gate 6 serving scheduler (R4)

调度模型与量子生命周期用例分别见
[scheduler tests](../../src/dev/ai_mesh/mesh_serving_scheduler.test.cc)、
[executor tests](../../src/dev/ai_mesh/serving_mesh_executor.test.cc) 和
[Python scheduler tests](../../util/mesh_ir/tests/unit/test_serving_scheduler.py)。
资源等待/取消见 [admission tests](../../src/dev/ai_mesh/serving_mesh_executor_admission.test.cc)，
READY/WAIT/实例选择见 [quantum tests](../../src/dev/ai_mesh/serving_mesh_executor_quantum.test.cc)；
两者复用 [Harness](../../src/dev/ai_mesh/serving_mesh_executor_test.hh)。
共享实例与 canonical arrival permutation 的组件入口见
[shared executor tests](../../src/dev/ai_mesh/serving_mesh_executor_shared.test.cc)。
多请求调度、CQ ACK 回收与整轮 drain 见
[multiple-request integration](../../util/mesh_ir/tests/integration/test_gate6_multiple_requests.py)。
运行期 MoE 的独立完整图对照见
[materializer golden](../../util/mesh_ir/tests/unit/test_moe_materializer_golden.py) 与
[serving oracle](../../util/mesh_ir/tests/integration/serving_moe_oracle.py)。
跨 batch attach、RESOURCE_WAIT、失败与冻结后取消复用上述 multiple-request integration；
实例身份通过 [phase facts](../../src/dev/ai_mesh/serving_phase_fact.hh) 对账。
R4 出口与 cache 生产覆盖使用同一 manifest selector：

```bash
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 6 \
    --id SERV-3 --id SERV-4 --id SERV-11 --id SERV-14 --id SERV-15 \
    --id MOE-24 --id MOE-28 --workdir .tmp/r4-acceptance
scons build/AXI_MESH/dev/ai_mesh/mesh_moe_materializer.test.opt -j4
build/AXI_MESH/dev/ai_mesh/mesh_moe_materializer.test.opt
```

Host 控制与真实 owner terminal 的联合场景见
[serving control integration](../../util/mesh_ir/tests/integration/test_gate6_serving_controls.py)。
静态 member slot 与 primary relocation 独立性见
[member-slot integration](../../util/mesh_ir/tests/integration/test_gate6_member_slots.py)，
与 [multirow prefix](../../util/mesh_ir/tests/integration/test_gate6_multirow_prefix.py)
共用 [fixture/runner](../../util/mesh_ir/tests/integration/serving_runtime.py)。
三表配置容量的 Python/Loader 双层拒绝与恰够执行见
[binding-capacity integration](../../util/mesh_ir/tests/integration/test_gate6_binding_capacity.py)，
C++ 静态证明见 [capacity tests](../../src/dev/ai_mesh/mesh_serving_capacity.test.cc)。
运行期三表冻结、失败不发布及存储复用见
[batch tests](../../src/dev/ai_mesh/mesh_serving_batch.test.cc)；
singleton 接入与冻结失败清理见
[admission tests](../../src/dev/ai_mesh/serving_mesh_executor_admission.test.cc)。
地址与成员身份共用解析入口见
[binding tests](../../src/dev/ai_mesh/serving_instance_binding.test.cc)；
DMA accepted/terminal 的调用入口为
[MeshDummyCore](../../src/dev/ai_mesh/mesh_dummy_core.cc) 的 `dmaOwner`。
真实事件所有者见
[Frontend execution adapter](../../src/dev/ai_mesh/npu_serving_frontend_execution.cc)。
单请求回归复用上节 E2E-D；R4 范围与开放项以
[实施计划](../../.tmp/docs/gate6-implementation-plan.md) 为准。

```bash
scons build/AXI_MESH/dev/ai_mesh/mesh_serving_scheduler.test.opt \
    build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt -j4
build/AXI_MESH/dev/ai_mesh/mesh_serving_scheduler.test.opt
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingMeshExecutor.*Quantum*:ServingMeshExecutor.SelectedPhaseRemainsBusyUntilTheRealStart'
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingMeshExecutor.*Identity*:ServingMeshExecutor.UnknownCompletionBeforeAdmissionHasNoState:ServingMeshExecutor.*Facts*'
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingMeshExecutor.SelectorsAreInstalledOnlyByTheWinningQuantum:ServingMeshExecutor.InstanceIdentityComesFromDispatcherSelection:ServingMeshExecutor.FailedReadyPhaseDoesNotWaitForANeverStartedInstance'
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingMeshExecutor.Admission*:ServingMeshExecutor.CancellingAdmission*'
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingBatchFixture.*:ServingMeshExecutor.InvalidFrozenTable*'
build/AXI_MESH/dev/ai_mesh/serving_mesh_executor.test.opt \
    --gtest_filter='ServingMeshExecutor.Cancel*:ServingMeshExecutor.SameEdgeCancel*:ServingMeshExecutor.ReleaseOnTheOwnerTerminalEdgePreservesTheSnapshot'
PYTHONPATH=util/mesh_ir python3 -m pytest \
    util/mesh_ir/tests/unit/test_serving_scheduler.py -q
PYTHONPATH=util/mesh_ir python3 -m pytest \
    util/mesh_ir/tests/integration/test_gate6_serving_controls.py \
    util/mesh_ir/tests/integration/test_gate6_multiple_requests.py -q
PYTHONPATH=util/mesh_ir python3 -m pytest \
    util/mesh_ir/tests/integration/test_gate6_member_slots.py \
    util/mesh_ir/tests/integration/test_gate6_multirow_prefix.py -q
PYTHONPATH=util/mesh_ir python3 -m pytest \
    util/mesh_ir/tests/integration/test_gate6_binding_capacity.py -q
```

## Gate 5 Dynamic MoE V1

ABI surface（feature bit、四个 conditional-required section、record 布局）由
[mesh_ir_abi.yaml](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml) 单点定义，
见上文 "ABI schema SSOT"；跨语言 golden 与 fail-closed 用例：

```bash
python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check
python3 -m pytest util/mesh_ir/tests/unit/test_abi.py \
    util/mesh_ir/tests/unit/test_gate5_moe_abi.py -q
scons build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt -j8
./build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt --gtest_color=no
python3 tests/gem5/ai_mesh/fixtures/gate5/build_gate5_moe_images.py
cd util/mesh_ir && python3 -m mesh_ir.cli emit-gate5-weight-golden \
    --arch ../../configs/example/ai_mesh/arch/mesh_1x2_moe.yaml \
    --program ../../tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb \
    --image ../../tests/gem5/ai_mesh/fixtures/model_weight_image_v1.json \
    --registry ../../tests/gem5/ai_mesh/fixtures/program_weight_bindings_v1.json \
    --out ../../src/dev/ai_mesh/generated/gate5_weight_golden.inc
python3 -m mesh_ir.cli emit-gate5-rng-golden \
    --golden ../../tests/gem5/ai_mesh/golden/rng_golden.json \
    --out ../../src/dev/ai_mesh/generated/gate5_rng_golden.inc
python3 -m mesh_ir.cli emit-gate5-overlay-golden \
    --fixtures ../../tests/gem5/ai_mesh/fixtures/gate5 \
    --json-out ../../tests/gem5/ai_mesh/fixtures/gate5/moe_overlay_objects.json \
    --out ../../src/dev/ai_mesh/generated/gate5_overlay_golden.inc
python3 -m pytest util/mesh_ir/tests/unit/test_gate5_weight_registry.py \
    util/mesh_ir/tests/unit/test_gate5_arch_partitions.py \
    util/mesh_ir/tests/unit/test_gate5_moe_capacity.py \
    util/mesh_ir/tests/unit/test_gate5_moe_identity.py \
    util/mesh_ir/tests/unit/test_gate5_moe_providers.py \
    util/mesh_ir/tests/unit/test_gate5_moe_overlay.py \
    util/mesh_ir/tests/unit/test_gate5_moe_materializer.py \
    util/mesh_ir/tests/unit/test_gate5_moe_overlay_runtime.py -q
```

跨语言 golden 为 `tests/gem5/ai_mesh/fixtures/gate5/moe_min.mshb` 与其
`moe_min_expected.json`；多 layer/不同 E/K 的 fixture 为 `moe_multi.mshb`
（layer1 E=2/K=2、layer2 E=4/K=2，两个不同 insertion site）；route buffer wire record（64 B `MOE_RUNTIME_ROUTE_ENTRY`）与确定性
pad/drop fill 派生由 [moe_fill.py](../../util/mesh_ir/mesh_ir/moe_fill.py)
唯一实现；数据面（row move/chunk 合并/expert 行/fan-in）由
[moe_materializer.py](../../util/mesh_ir/mesh_ir/moe_materializer.py) 展开，
运行期接线：`MeshProgramLoader` 通过 `overlay_image` 参数装载镜像并按 core 调用
`MeshDummyCore::installOverlay()`；core 在 static 解码到达 `insert_after` 时发布
overlay entry event，并在 `tick()` 中推进 executor；executor 发布 group exit 后
`MeshDispatcher::notifyOverlayExit()` 释放 insertion gate。Gate 1–4 场景不提供
overlay image，因此该路径完全惰性。

运行期 overlay 镜像（固定宽度记录，免 JSON 解析）由
[MoeOverlayImage](../../src/dev/ai_mesh/mesh_moe_overlay.hh) 编解码：每条记录为
canonical 122 B + 描述符 src/dst view（16 B）+ 三份引用表（wait、signal、
对象自身 view，各 `kRefsPerObject` 槽；view 引用让运行期按视图而非 opcode
估计来服务 SRAM），fixture 产出
`tests/gem5/ai_mesh/fixtures/gate5/moe_overlay_objects.bin`，C++ GTest 解码后必须
得到与 Python golden 相同的 canonical graph、view 引用与 digest（`encode()`
再编码必须逐字节相同）。

overlay 执行器（typed wait/signal、按视图的 SRAM 端口/字节服务、跨核事件总线、
region/group drain）由
[mesh_moe_runtime.cc](../../src/dev/ai_mesh/mesh_moe_runtime.cc) 实现：DMA 命令的
端点由描述符的 view 解析（端口服务仍由共享引擎拥有，descriptor queue 满时命令
保持未 armed 并在后续 tick 重试提交，不会提前完成）；core 在每个 edge 传入绝对
tick（`publishEntry(event, core_tick)`/`tick(core_tick)`，执行器不再自累加时钟），
compute 命令按 `READS → ENGINE → WRITES → RETIRE` 推进，engine 窗口从 operand
read 服务完成时刻起算、结果只在 output write 服务完成后提交（
[mesh_compute_timing.hh](../../src/dev/ai_mesh/mesh_compute_timing.hh) 的公式由
静态与 overlay 两条路径共用；[mesh_compute_commit.hh](../../src/dev/ai_mesh/mesh_compute_commit.hh)
是两侧唯一的 digest/结果字节/validity 提交入口）。operand 方向与计费跨度按
command 角色解析（combine 每个 contributor 只计一行），SRAM 服务字节按 region
与 view kind 记账并写入 `moe.regions[].sram_read_bytes/sram_write_bytes`、
`sram_read_by_kind/sram_write_by_kind`，另暴露
`sram_service_cycles`/`sram_bank_conflicts`/`compute_cycles`/`compute_commits`/
`uncommitted_views`；fixture 冻结的 oracle lane 与 `compute_geometry` 由
`moe_oracle_recompute` 逐项核对（见下）。已发布但未 drain 的 overlay 属于
未完成工作：[mesh_dummy_core.hh](../../src/dev/ai_mesh/mesh_dummy_core.hh) 的
`overlayPending()` 阻止 `REQUEST_DRAINING → INSTANCE_DONE`、维持 core tick 并让
`quiescent()` 为假，watchdog 的进度计数改为 `workProgress()`（static 与 overlay
完成数之和）。GTest 直接消费真实 golden overlay 直至 drain，并覆盖背压重试、
view 服务与 image 往返。

insertion gate（`insert_after` 之后停住、`OVERLAY_EXIT` 之后才在 `resume_before`
恢复解码）由 [mesh_moe_gate.cc](../../src/dev/ai_mesh/mesh_moe_gate.cc) 实现，
`MeshDispatcher::armOverlayGates()` 在 dispatch 时按 region record 逐核 arm，
overlay 组退出后以 `overlayGroupExited(layer_id)` 释放。

overlay 对象图与 DAG 由
[moe_overlay_runtime.py](../../util/mesh_ir/mesh_ir/moe_overlay_runtime.py)
两阶段安装（handle → ordinal）；C++ 侧
[mesh_moe_overlay.cc](../../src/dev/ai_mesh/mesh_moe_overlay.cc) 从同一 golden
重建对象表、重跑 canonical ordinal 与结构校验，并对逐字段 little-endian 编码
求 SHA-256，必须与 Python digest 相同。
overlay 对象身份/ordinal/scratch/结构校验的唯一实现是
[moe_overlay.py](../../util/mesh_ir/mesh_ir/moe_overlay.py)（枚举来自同一 ABI
registry）；provider
artifact 的 exact schema 为 `schemas/ai_mesh/moe_{route_replay,histogram_replay,
correlated_profile}.schema.json`，四个 provider 与 freeze/capacity/digest 的
唯一实现是 [moe_provider.py](../../util/mesh_ir/mesh_ir/moe_provider.py)；两侧语义校验实现分别在
[moe_verifier.py](../../util/mesh_ir/mesh_ir/abi/moe_verifier.py) 与
[mesh_moe_verifier.cc](../../src/dev/ai_mesh/mesh_moe_verifier.cc)，规则同源。

`SemanticTokenUidV1` 与 keyed RNG（80 B key、SHA-256 seed、单次 SplitMix64、
Q32 threshold sampler）分别由 [moe_uid.py](../../util/mesh_ir/mesh_ir/moe_uid.py)
与 [moe_rng.py](../../util/mesh_ir/mesh_ir/moe_rng.py) 唯一实现，跨语言 golden 为
`tests/gem5/ai_mesh/golden/rng_golden.json` 与 C++ 侧
[mesh_moe_rng.cc](../../src/dev/ai_mesh/mesh_moe_rng.cc)；
`src/dev/ai_mesh/generated/gate5_rng_golden.inc` 由同一 JSON 生成，禁止两侧各写
一套常量。

Gate 5 使用带 SRAM partition 的 architecture
`configs/example/ai_mesh/arch/mesh_1x2_moe.yaml`（四分区互斥、`WEIGHT_CACHE`
等大 slot、`metadata_entries == slot 数`），legacy arch 不声明 partition 时其
digest 逐字节不变；派生容量（`C_e`、view record/ref bound、cache slot 与
failure table 需求）由 [moe_capacity.py](../../util/mesh_ir/mesh_ir/moe_capacity.py)
唯一计算，capacity−1 在构造阶段即以 `E_CAPACITY_PLAN` 拒绝且无副作用。

权重域的唯一所有者是 [weight_registry.py](../../util/mesh_ir/mesh_ir/weight_registry.py)：
`tests/gem5/ai_mesh/fixtures/model_weight_image_v1.json`（immutable 内容身份）
与 `program_weight_bindings_v1.json`（program 符号 → image 投影）按其 schema
校验，`program_weight_registry_image_cycle_golden.json` 固定无环投影顺序与各
阶段 digest；84 B `WeightFillTagTupleV1` manifest 由 Python 与
[mesh_weight_tags.cc](../../src/dev/ai_mesh/mesh_weight_tags.cc) 独立生成并逐字节对照
（`src/dev/ai_mesh/generated/gate5_weight_golden.inc`）。`weight_tag_index`
索引空间是 program-wide 的 region 顺序（`weight_region_order`）：每个 core
的 `weightTagSitesOf()` 只滤出本核 expert，因此同一 region 在不同核共享同一
tag index 而各自持有 slot/pin，`cacheable_tags` 与 `cache_fills` 均使用该索引。

E2E-C 的四个 4×4 子例共用 GEM5 case `moe_quad`（hotspot 用 `moe_quad_hotspot`
以追加 `moe_hotspot_load`）：`balanced_4x4`（uniform）、`replay_4x4`
（checked-in `moe_quad_route_replay.json`，热点 expert 0..3）、`hotspot_4x4`
（checked-in `moe_quad_hotspot_profile.json`，hotset {0,1}）与
`determinism_three_repeat`（[test_gate5_e2e_determinism.py](../../util/mesh_ir/tests/integration/test_gate5_e2e_determinism.py)
连续跑 3 次 gem5 并逐字段比较 canonical result，仅剔除 host 字段）。
`moe_hotspot_load` 以 hotspot 与 balanced 两个投影对比：峰值 region 的 P2P
必须超过 balanced 均值的 2 倍，且每个热点 region 的实际 P2P 字节与其投影逐项
相等。三者共用 4×4 arch 与真实
4×4 Garnet（16 router，`axi_shared_router_endpoints`）与 arch-aware scenario：
`base_scenario(options, core_count)` 为每 core 生成一个 initiator 与一个
SRAM aperture target（node id 从 `NODE_SRAM_FIRST` 稠密分配，HBM/ERR 紧随），
router 数取 `max(6, rows*cols)`，target 的 context/beat 容量随 core 数放大以
容纳全部 source 配额；2-core 时逐字段等价于既有 endpoint map（Gate 1 归档
494/494 逐字节一致）。overlay 镜像为 `moe_quad_overlay_objects.bin`
（16 region、841 objects、DIGEST_ONLY）。

4×4 E2E-C 的静态 program 由
[moe_programs.py](../../util/mesh_ir/mesh_ir/moe_programs.py) 的
`moe_quad_program()` 生成：16 个 core 各持一个 region 与 expert（`top_k=2`），
每对 core 互为 reducer/sender（`quad_reducer/quad_sender`），唯一
`REQUEST_BEGIN/REQUEST_END` 在 core 0，16 个 local HALT；core 0 的
`EVENT_SIGNAL` join 等待全部 16 个 store/push completion event，使 lifecycle
END 支配每个 region 的 `resume_before`；architecture 为
[mesh_4x4_moe.yaml](../../configs/example/ai_mesh/arch/mesh_4x4_moe.yaml)
（4×4 die、16 core、四 SRAM 分区）。runner 的 AXI mesh router 数仍由 scenario
endpoint map（2 initiator + SRAM/HBM/error target）决定，并显式校验 arch 的
core 数不超过 router 数；GATE5 case 允许 `--arch` 改变 layout（program+镜像+arch
成组给出），其余 case 仍只允许 tuning 字段差异。

drain 状态（主合同 §10.6、coding spec 退出条件 7）由 `moe_drain_state` 检查：
所有 core 的 `live_commands==0`、`commands_issued==completed+errored+cancelled`、
`dma_idle==1`、`instance_error==0`，所有 region `drained` 且 `issued==completed`，
bridge/aperture 无 pending AXI 与 error-drained 字节，cache 的
`live_tokens/live_obligations/pending_fills/pending_subscribers` 全为 0 且
MSHR/eviction/obligation/subscriber 的 free 计数等于 slot 数；同时**持久状态
单独核对**：无 tombstone 的 core 必须仍有 `valid_lines`（`valid_bytes>0`），
即 drain 归零不能把合法 resident line 清掉。模型侧对应
`test_gate5_cache_drain_state_keeps_the_persistent_line` 与
`..._keeps_the_failure_tombstone`（后者验证 failure tombstone 在 slot 释放后
仍保留且同 generation 不重试）。

canonical 对账：`moe_canonical_projection` 直接以 materialized overlay 投影
（`--overlay-image` 同目录的 `.json`）为期望，逐 descriptor 核对 dma kind、
payload bytes 与 burst 数（`LOCAL_FILL` 无 AXI burst，`LOAD`/`P2P_PUSH` 按
`axi_data_bytes × axi_max_burst_beats` 切分），并按 `owner_core` 核对每核执行的
materialized command 数；`moe_dual_basic`（E2E-C）与 `moe_dual_timing`
（MOE-3 的 timing/buffer 深度 A/B，`dma_read_outstanding=2`、
`dma_write_outstanding=2`、`dma_descriptor_queue_depth=2`）都跑同一检查，
因此两条 timing 不同的臂必须给出同一 canonical projection。

fan-in 0（整 token 全 DROP）的运行期证据由 `moe_dual_drop`（MOE-12
`dual_dropped_token_fill`）给出：fixture 把 dual layer 的 `capacity_factor_q16`
降到 0.5，使 m1 占满两个 expert 容量、m2 的两条 route 全部 DROP，materializer
因此产出 `DROPPED_TOKEN_FILL` 命令/描述符（目标为该 member 的 STATIC-backed
`MEMBER_OUTPUT` 视图，core1 的 program allocation 7）。runner 的 `moe_drop_fill`
检查对齐 fixture 与运行结果：投影里 fill 命令与描述符一一对应、目标视图是
`MEMBER_OUTPUT` + `STATIC_ALLOCATION` 且字节等于 fill 的 `valid_bytes`，执行该
fill 的 core 真正 drain（`issued==completed`）并服务了非零 SRAM 写字节；
`moe_canonical_projection` 同时逐 descriptor 核对这条 `DMA_FILL` 的实际 payload
与 burst，`moe_drain_state` 覆盖其收尾。

weight residency 两种策略由 `--weight-policy` 选择，并由 overlay 镜像内容交叉
校验（[mesh_program_loader.cc](../../src/dev/ai_mesh/mesh_program_loader.cc)
装载镜像时按 WEIGHT view 的 backing kind 与 STREAMED_WEIGHT descriptor
fail-closed）：`streamed` 由 batch-owned `DMA_LOAD` 写入 `RUNTIME_SCRATCH` 的
`STREAMED_WEIGHT` allocation；`cached` 不产生任何 batch-owned weight load 或
allocation，WEIGHT view 以 `WEIGHT_CACHE_SLOT` backing 指向 program-wide tag，
真实流量由 cache fill 拥有并落在 `WEIGHT_CACHE` 分区，因此
`moe.cache_fills[].address == partition_base + slot_id * slot_bytes` 且
overlay domain 不再出现 weight LOAD。镜像 fixture 由
[build_gate5_overlay.py](../../tests/gem5/ai_mesh/fixtures/gate5/build_gate5_overlay.py)
为两种策略分别产出：`moe_dual_overlay_objects.bin`（streamed，E2E-C 使用，
经 `emit-gate5-overlay-golden` 冻结为 `gate5_overlay_golden.inc`）与
`moe_dual_overlay_cached.bin`（cached，MOE-19 使用，由 C++ GTest 解码并逐
WEIGHT view 核对 runtime tag index 与 backing kind）。

member slice 几何不手写：`moe_materializer.member_slice()` 由
`Program.shard_of(tensor_role, owner_core)`（[model.py](../../util/mesh_ir/mesh_ir/model.py)，
同一 (role, core) 多 shard 时 fail-closed）派生 INPUT/OUTPUT allocation 与行宽
（`layer.token_bytes`/`output_token_bytes`），OUTPUT shard 容不下整行时留 0，
由运行期 instance binding 解析。materialize 阶段另有两条 fail-closed 不变式：
member 行宽必须等于 layer 行宽（[moe_materializer.py](../../util/mesh_ir/mesh_ir/moe_materializer.py)
的 `member rows must match the layer token rows`，否则 pad 行偏移会与 real 行
重叠），STATIC backing view 必须落在**同 owner core** 的 program allocation 内
（[moe_overlay.py](../../util/mesh_ir/mesh_ir/moe_overlay.py) 的
`view crosses the program allocation owner` / `view escapes its program
allocation`）。全 DROP 的 token（`fan_in==0`）必须绑定 member output，否则
`a dropped-token fill needs a bound member output`。

reservation 走两阶段：[mesh_weight_cache.cc](../../src/dev/ai_mesh/mesh_weight_cache.cc)
的 `prepare()` 只读 live 状态并返回影子计划、`commit()` 是唯一原子入口；
[mesh_dispatcher.cc](../../src/dev/ai_mesh/mesh_dispatcher.cc)
`resolveCacheReservations()` 覆盖全部 core/layer demand——全 COMMITTED 才逐核
commit，任一 RESOURCE_WAIT 整批入有界稳定队列（request_id 冻结、容量=demand
item 数）并在资源释放后重试，任一 FAILED 整批走 `abortBeforeStart` + 一次
fanout。token 记录该 batch 引用该 weight view 的 consumer 数，
`noteConsumerDrained()` 递减到 0 才释放 pin（HIT/ATTACH 同样持有）；instance
fault 由统一 join 释放——`noteInstanceFault` 把 pending subscriber 转
`TERMINAL_TOMBSTONED`，`noteFailureFanoutDone`/`noteOwnedWorkDrained` 作为 join
输入，started fault 需 `fanout && fill terminal && owned drain`（两种先后顺序
等价），prestart abort 只需 fanout + fill terminal；core 端先到
`INSTANCE_OWNED_WORK_DRAINED`，在随后的 cache edge 释放且
`liveTokensOfBatch()==0` 时才锁存 `INSTANCE_ERROR_DRAINED`。每次释放/异动计入
`moe.cache_state[]` 的 `tokens_created/token_releases/tombstoned_subscribers/
woken_subscribers/faulted_fill_terminals`，`moe_drain_state` 断言
`token_releases == tokens_created`。

资源/错误反例（主合同 §17.3-28）由
[test_gate5_moe_failure_fanout.py](../../util/mesh_ir/tests/unit/test_gate5_moe_failure_fanout.py)
与 [test_gate5_weight_cache.py](../../util/mesh_ir/tests/unit/test_gate5_weight_cache.py)
覆盖：跨核 batch all-or-none（升序 probe 后一次 commit，失败零 partial token）、
满 cache 同 batch HIT+miss 保护、exact alias 单 subscriber、A→B→A 新
incarnation、member cancel 不改变 batch subscriber、instance fault 只 tombstone
自身、started fault 的两种 join 顺序与 fanout gate、故障 batch 不被 WOKEN、
最后 consumer drain、`prepare` 零可见副作用、fill error 单次 fanout + 单条
tombstone、failure retire 原子释放、同 generation 不重试、cancel 与 error
同 edge 时 error 胜、background fill 收尾 tombstone subscriber。生产路径证据为
`moe_dual_cached_reuse`（`--weight-policy cached`、`instances=2`）：两个 instance
各核 ledger 完全一致且 0 errored/cancelled，而 `moe.cache_fills` 只有每核一次
`fill_incarnation=1`、WEIGHT_FILL domain 只搬 2×4096 B，即第二个 instance 命中
resident line，检查为 `moe_cache_reuse`。

独立 MoE oracle（主合同 §7.10、coding spec §9/§9.2）由
[moe_oracle.py](../../util/mesh_ir/mesh_ir/moe_oracle.py) 实现：`moe_traffic_lanes()`
从 frozen route plan/capacity/member slice/placement/weight binding 重算
§7.10 的全部 lane（dispatch/combine 的 all/local/remote、padding slot 与 fill、
expert in/out SRAM、全 DROP fill、gather/output SRAM、COPY_THROUGH/LOCAL_REDUCE
计数与 reduce ops、weight read、route metadata fill），并按 `Packetization`
（AXI data/burst/header/flit）与 `MeshTopology`（XY hop）展开 `per-peer`
logical 与 `per-link` wire bytes；`descriptor_expectations()` 从 materialized
投影推导逐 descriptor 的 dma kind/payload/burst，`verify_result()` 对实际
result JSON 逐项对账（含 cache fill ledger 的 unique 身份、`fill_traffic_id`
自哈希、key/扁平字段一致性与 `address == partition_base + slot*slot_bytes`）；
`verify_overlay_document()` 独立校验投影（dense ordinal、引用可解析、event 单
生产者、region terminal drain join、group exit 覆盖全部 region terminal、view
validity/越界、以及从 region entry 出发的 DAG 可达性）。`tamper()` 覆盖 12 类
真实产物篡改（route record / descriptor bytes / dma kind / typed owner / DAG
edge / view validity / peer actual / terminal record / fill id / slot id /
subscriber / latch-commit），逐一被上述校验拒绝。

strict serial replay（主合同 §7.5、§17.3-4）由
[moe_strict_replay.py](../../util/mesh_ir/mesh_ir/moe_strict_replay.py) 实现：
`run_strict_replay()` 先在 tick 0 用 `prove_strict_capacity()` 证明该 batch 的
最坏 cold union（line/MSHR/obligation 按 dedup 后 key 数、subscriber/token/pin
按全部 layer occurrence 数），再按 `batch_ordinal` 串行 `reserve_batch_wide()`
（`strict_batch_barrier()` 拒绝对仍有 live token/obligation 的 cache 开新
batch），每个 `{batch,layer,base_key}` occurrence 各得 subscriber/token/pin 而
物理 fill 只发一次；`decision_digest` 不含 tick，因此快/慢 fill 两臂必须给出
同一 decision/fill 投影与同一 `materialization_digest`。cache 初态可用
`cache_state_replay_document()` 序列化并由
`apply_cache_state_replay()` 装回（只接受 INVALID/VALID、dense LRU rank、
同 core/同 generation、无重复 VALID、VALID 与 tombstone 互斥）。
materialization `weight_bindings`（`hit|attach|new_fill` + slot + fill ref）
进入 [moe_overlay_runtime.py](../../util/mesh_ir/mesh_ir/moe_overlay_runtime.py)
的 materialization projection/digest。

实施顺序、接缝与每轮证据见
[Gate 5 Coding Spec](../../src/doc/ai_mesh/DUMMY_AI_CORE_GATE5_CODING_SPEC.md)；
已登记集合为 `mesh_ir.gate5_contract.GATE5_CASES` 加 `E2E-C`，占位 ID 补齐前
按 ID 定向选择（每个 `--id` 一个 logical ID）：

```bash
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 5 \
    --id MOE-1 --id MOE-2 --id MOE-3 --id MOE-5 --id MOE-6 --id MOE-7 \
    --id MOE-8 --id MOE-9 --id MOE-10 --id MOE-11 --id MOE-12 \
    --id MOE-13 --id MOE-14 --id MOE-15 --id MOE-16 --id MOE-17 \
    --id MOE-18 --id MOE-19 --id MOE-20 --id MOE-21 --id MOE-22 \
    --id MOE-23 --id MOE-25 --id MOE-26 --id MOE-27 --id MOE-29 \
    --id MOE-30 --id E2E-C \
    --workdir "$(mktemp -d /tmp/ai-mesh-gate5.XXXXXX)"
python3 configs/example/ai_mesh/gate5_acceptance.py
python3 tests/gem5/ai_mesh/gate5/runtime_contract.py
```

## Torch Gate 4 mock runtime acceptance

Compiler publication and simulator setup share the
[`runtime_harness`](../../util/mesh_ir/tests/integration/support/runtime_harness.py).
Focused contracts are in the [view-offset tests](../../util/mesh_ir/tests/integration/test_gate4_view_offset_runtime.py),
[fill tests](../../util/mesh_ir/tests/integration/test_gate4_fill_runtime.py),
[transfer snapshots](../../util/mesh_ir/tests/integration/test_gate4_transfer_snapshots.py),
[reconciliation tests](../../util/mesh_ir/tests/integration/test_gate4_reconciliation.py),
[diagnostic lifecycle tests](../../util/mesh_ir/tests/integration/test_gate4_diagnostic_lifecycle.py)
and [behaviour matrix](../../util/mesh_ir/tests/integration/test_gate4_behaviour_matrix.py).
Use the locked interpreter and a fresh local `/tmp` publication directory as above.

## Torch Gate 5 real AXI/Garnet runtime

The real-backend run entry is
[`run_mesh_dma_garnet.py`](../../configs/example/ai_mesh/run_mesh_dma_garnet.py);
the harness that drives it is
[`garnet_harness.py`](../../util/mesh_ir/tests/integration/support/garnet_harness.py),
which shares program publication, oracle readers and the gem5 runtime
environment with [`runtime_harness.py`](../../util/mesh_ir/tests/integration/support/runtime_harness.py).
The runner reconciles through the same
[`runtime_reconciliation.py`](../../util/mesh_ir/mesh_ir/runtime_reconciliation.py)
entry as the mock runtime and writes its admitted oracle to
`gate5_oracle.json` before simulating. Raw byte archives are opt-in per run
(`--dump-verify-bytes`, `--dump-source-bytes`, `--dump-compute-bytes`).

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/integration/test_gate5_garnet_e2e.py \
  util/mesh_ir/tests/integration/test_gate5_transport_contract.py \
  util/mesh_ir/tests/integration/test_gate5_axi_completion.py \
  util/mesh_ir/tests/integration/test_gate5_peer_commit_runtime.py \
  util/mesh_ir/tests/integration/test_gate5_wstrb_runtime.py \
  util/mesh_ir/tests/integration/test_gate5_determinism.py \
  util/mesh_ir/tests/integration/test_gate5_burst_attribution.py \
  util/mesh_ir/tests/integration/test_gate5_e2e_bytes.py \
  util/mesh_ir/tests/integration/test_gate5_packet_traffic.py \
  util/mesh_ir/tests/integration/test_gate5_archive_manifest.py \
  util/mesh_ir/tests/integration/test_axi_outstanding_runtime.py \
  --basetemp=/tmp/torch-gate5-focused-UNIQUE
```

## Torch Gate 6 workload acceptance

Entry points live in
[`workload_producer.py`](../../util/mesh_ir/mesh_ir/workload_producer.py)
(every consumed local source row has a byte-exact admitted producer,
sub-range reads tile producer runs, consumer rows are anchored to their
admitted endpoint geometry, P2P relays inherit producer identity) and
[`workload_statistics.py`](../../util/mesh_ir/mesh_ir/workload_statistics.py)
(spec §13 metrics derived from real event owners, including the compiler
cycle prediction of [`compute_timing.py`](../../util/mesh_ir/mesh_ir/compute_timing.py)
against archived engine executions; `unavailable` lists what still has no
owner). Both consume the archived `actual_result.json`; the statistics
summary is also written per run as `workload_statistics.json`. Composite
counterexamples (consistent moves, replayed instances, masked deletions,
error-B disguises) are exercised by
[`test_gate6_composite_lies.py`](../../util/mesh_ir/tests/integration/test_gate6_composite_lies.py).

```bash
PYTHONPATH=util/mesh_ir .tmp/torch-package-compiler/bin/python -m pytest -q \
  util/mesh_ir/tests/integration/test_gate6_ring_workload.py \
  util/mesh_ir/tests/integration/test_gate6_tensor_classes.py \
  util/mesh_ir/tests/integration/test_gate6_producer_chain.py \
  util/mesh_ir/tests/integration/test_gate6_statistics.py \
  util/mesh_ir/tests/integration/test_gate6_composite_lies.py \
  --basetemp=/tmp/torch-gate6-focused-UNIQUE
```

Target fault plans are resolved to admitted execution identities by
[`fault_plan.py`](../../util/mesh_ir/mesh_ir/fault_plan.py), so an error-drained
run is judged by the shared reconciliation entry instead of a UID list; the
same module derives the accepted transaction order, clipped by the smaller of
the architecture and descriptor burst caps. The `read_reorder` case delays one
middle read burst and asserts the resulting R completion order, while
`read_outstanding_window` is its no-delay control.
Every real commit that extends a transfer's coverage is archived as a stage,
and the receiver notification crosses `MeshDispatcher::routePeerCommit`, so
`--receiver-fault receive_duplicate|receive_redirect|receive_drop` injects at
the same boundary for both backends. Peer coverage is observed per byte by
[`peer_sram_aperture.cc`](../../src/dev/ai_mesh/peer_sram_aperture.cc) and
verified by `verified_transfer_publishes` in
[`runtime_peer_publish.py`](../../util/mesh_ir/mesh_ir/runtime_peer_publish.py);
`--replay-write-commit TARGET:UID:COUNT` injects a real duplicate observer
notification at the target delivery boundary. The `p2p_partial_abandon` carrier
fails one burst of an admitted P2P plan: the aperture retires the expectation as
abandoned (`PeerSramAperture::abandonAllExpectations`) instead of discarding its
coverage, and `verified_transfer_publishes` requires the receiver's covered bytes
and transactions to equal the source's own partial contribution while neither
side reports a release. The DMA direction class of each descriptor kind is one
table (`_AXI_DIRECTION_FIELDS`) shared by the execution oracle and the fault
model. The `p2p_prefilled` and `p2p_prefilled_incomplete` carriers push a
40-descriptor transfer onto a destination a core-1 `LOCAL_FILL` already made
resident: every committed descriptor archives its own destination observation
(`MeshDummyCore::recordTransferCommit`, shared by both engines) and
`verified_transfer_snapshots` in
[`runtime_reconciliation.py`](../../util/mesh_ir/mesh_ir/runtime_reconciliation.py)
requires a resident destination never to be this transfer's completion, the
pending set to be exactly the admitted descriptors that did not commit, and each
untouched pending span to still hold its admitted initial content. Peer coverage
is a per-frame fact: the dispatcher snapshots every aperture's transfer coverage
into the instance frame it archives (`MeshDispatcher::writeInstanceApertures`),
and the `p2p_frames` carrier runs the whole E2E-2 chain at 1/2/3 instances with
each frame's own receiver ordering, peer publish, staged stages and chain content
(the shared `verify_transfer_producer_content` pairs the frame's producer with
each local-source descriptor). `dma_basic_shallow` and `p2p_frames_shallow` run
the same two chains under one shallow configuration
(`run_mesh_dma_garnet.shallow_queues`) with the full assertion set, and the shared
`verified_queue_bounds` proves from the archive that the in-flight descriptor
peak stays within the admitted depth and reaches it, that the read window is
filled but never exceeded, that every accepted burst retires exactly once and
that the source recorded measurable blocking. The admitted compute model is one
shared definition in
[`compute_timing.py`](../../util/mesh_ir/mesh_ir/compute_timing.py): the core
archives each engine plan's analytic cycle count, and every real compute command
must run exactly the cycles its own admitted attribute costs, on the admitted
engine, ending at the tick the model predicts. `p2p_peer_edge` pushes onto a
peer destination that itself starts unaligned, pads every row by four bytes and
crosses a 4 KiB page: its admitted burst split is verified against the observed
AW bursts, and `verified_sentinels` treats a declared span a published compute
run really wrote as explained while every other span must stay identical.
`drain_stalled` arms a test-only credit-drop fault at the real credit-return
boundary (`--drain-fault drop_credit`) once the drain is open, so every core has
halted and the data path is empty while the credit ledger alone keeps the gate
waiting; the drain-phase watchdog must report that state with
`unrestored_links`/`deficit` instead of DONE. The E2E-1 destination landing is
owned by `verified_store_destinations`: the verified destination digest must equal
the payload the last instance's own STORE execution wrote over the whole admitted
span, so a wrong store payload fails instead of merely being non-zero. WSTRB-disabled lanes, row
padding and the unaligned head and tail around a payload are proven untouched
by `TargetSentinels` in
[`target_sentinels.cc`](../../src/dev/ai_mesh/target_sentinels.cc) and
`verified_sentinels` in
[`runtime_reconciliation.py`](../../util/mesh_ir/mesh_ir/runtime_reconciliation.py).
The unified drain boundary is owned by `MeshDispatcher::drainSatisfied` in
[`mesh_dispatcher.cc`](../../src/dev/ai_mesh/mesh_dispatcher.cc): after HALT it
waits for an empty Garnet snapshot, every restored credit-ledger link, idle
bridges, no live peer expectation and an idle target endpoint, publishes the
drain window and the ledger, and is verified by `verified_drain` in
[`runtime_reconciliation.py`](../../util/mesh_ir/mesh_ir/runtime_reconciliation.py).
The drain is re-evaluated on real clock edges (and on real progress), and a
drain that makes no progress fires the watchdog with the pending owner
identities. The `drain_deferred` case raises the per-link latency so credits
are still returning when the last core halts, which is the carrier for the
postponed exit. A rejected admission is also covered: a corrupt image and a program admitted
against a different architecture are refused before any tick runs, and the
structured diagnostic names the lifecycle stage, the phase (`decode`,
`admission`, `variant_selection`, `binding_resolution`, `command_rom`),
`install_state` and `installed_cores`. A post-commit B error is injected with `--post-commit-fault TARGET:UID`: the
target commits the burst and then reports a failing B, so the landing and the
error are both recorded. The fault model in
[`fault_plan.py`](../../util/mesh_ir/mesh_ir/fault_plan.py) names the failed
burst and the admitted per-burst bytes, and `reconcile` verifies that every
other burst still accounts as committed. A `lost_response` carrier delays one read transaction far beyond the watchdog
budget: it must fail closed with `E_RUNTIME_DEADLOCK`, a watchdog diagnostic and
the pending descriptor and waiter identities, never as DONE or ERROR_DRAINED.
`test_gate5_determinism.py` runs five carriers twice each and requires the
canonical result JSON to be identical, with no excluded field.
A drained run's issued beats are classified from the archived executions and
must equal the bridge counters exactly, so an errored run is not exempted from
beat conservation. Per-case evidence for the whole real-backend matrix is
regenerated by
[`run_case_matrix.py`](../../.tmp/docs/torch-gate-5-implementation/run_case_matrix.py)
into `.tmp/docs/torch-gate-5-implementation/matrix`; the implementation state is
`status.json` under `.tmp/docs/torch-gate-5-implementation/`.

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
logical IDs and 72 subcases.

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

Physical memory ownership and manifest-based deployment checks:

```bash
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
    util/mesh_ir/tests/unit/test_runtime_memory.py \
    util/mesh_ir/tests/unit/test_runtime_deployment.py \
    util/mesh_ir/tests/unit/test_runtime_fabric.py \
    util/mesh_ir/tests/unit/test_gate6_credit.py \
    util/mesh_ir/tests/integration/test_gather_runtime.py
build/AXI_MESH/dev/ai_mesh/memory_address_map.test.opt
```

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

## Gate 6 repair、持久状态与 Host 生命周期（R5）

阶段合同：[Gate 6 coding spec](../../src/doc/ai_mesh/DUMMY_AI_CORE_GATE6_CODING_SPEC.md)。
注册入口：[mandatory manifest](../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml) 的 E2E-E、HOST-15、HOST-25。

- [真实 repair、策略错误、控制交错和持久 manifest](../../util/mesh_ir/tests/integration/test_gate6_repair.py)。
- [有限 Host pool 与运行期 provider 对照](../../util/mesh_ir/tests/integration/test_gate6_host_pools.py)；
  [provider 接缝](../../util/mesh_ir/mesh_ir/serving_moe_provider.py) 复用 [provider 组件](../../util/mesh_ir/mesh_ir/moe_provider.py)，
  [C++ population](../../src/dev/ai_mesh/mesh_moe_population.hh) 统一验证选择与容量。
- [持久恢复](../../src/dev/ai_mesh/mesh_kv_persistence.hh)、[独立 manifest reader](../../util/mesh_ir/mesh_ir/kv_persistence.py)、
  [exact schema](../../schemas/ai_mesh/kv_persistent_v1.schema.json)。恢复入口要求调用方提供捕获时冻结的 manifest digest；
  整体 gem5 serving checkpoint 见 [运行合同与入口](serving_checkpoint.md)。
- [Host 生命周期](../../src/dev/ai_mesh/agent_workload_manager.hh)、
  [metadata 诊断校验](../../src/dev/ai_mesh/agent_protocol_validation.hh)、
  [ABI 投影真源](../../util/mesh_ir/mesh_ir/abi/agent_protocol_abi.yaml)。
- [Host plan image 编解码](../../util/mesh_ir/mesh_ir/agent_plan_image.py)、
  [C++ reader](../../src/dev/ai_mesh/agent_plan_image.hh)；版本与可达 instance counts 格式以编解码实现为准。
- [真实输入文件与 native admission](../../util/mesh_ir/tests/unit/test_agent_input_payload.py)、
  [driver 字节落地与 checkpoint 身份](../../util/mesh_ir/tests/integration/test_agent_input_payload_runtime.py)。
  输入声明见 [workload schema](../../schemas/ai_mesh/agent_workload_plan_v1.schema.json)。

```sh
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 6 \
  --id E2E-E --id HOST-15 --id HOST-25 --workdir .tmp/r5-acceptance
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_serving_repair.py \
  util/mesh_ir/tests/unit/test_serving_moe_provider.py \
  util/mesh_ir/tests/unit/test_kv_persistence.py
```

## Gate 6 取消与错误收尾（R6）

合同和出口：[Gate 6 coding spec](../../src/doc/ai_mesh/DUMMY_AI_CORE_GATE6_CODING_SPEC.md)。

- [分段 PUBLISH、4 KiB、错误前缀和取消 drain/suppress](../../util/mesh_ir/tests/integration/test_gate6_failure.py)。
- [独立 segment oracle](../../util/mesh_ir/mesh_ir/serving_publish_oracle.py)、[篡改拒绝](../../util/mesh_ir/tests/unit/test_serving_publish_oracle.py)。
- [全局 reducer](../../src/dev/ai_mesh/instance_failure_reducer.test.cc)、[segment 账本](../../src/dev/ai_mesh/publish_segment_ledger.test.cc)。
- [共享 fill fanout](../../util/mesh_ir/tests/integration/test_gate6_multiple_requests.py)、[cache first candidate](../../src/dev/ai_mesh/mesh_weight_cache_candidates.test.cc)。
- [错误 source wire 真源](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml)、[diagnostic golden](../../util/mesh_ir/tests/unit/test_agent_diagnostic.py)。
- [共享取消与部分输出](../../util/mesh_ir/tests/integration/test_gate6_shared_cancel.py)、[RELEASE 状态矩阵](../../util/mesh_ir/tests/integration/test_gate6_release.py)。
- [SQ/parameter/identity/binding wire 矩阵](../../util/mesh_ir/tests/integration/test_gate6_wire_admission.py)、[SQ 身份与 CQ 合同组件](../../src/dev/ai_mesh/agent_sq_admission.test.cc)。
- [共享与跨 batch 故障](../../util/mesh_ir/tests/integration/test_gate6_shared_failure.py)、[compute poison-read](../../util/mesh_ir/tests/integration/test_gate6_compute_failure.py)、[materializer prestart](../../util/mesh_ir/tests/integration/test_gate6_materializer_failure.py)。
- [completion-path fatal KV 保留](../../util/mesh_ir/tests/integration/test_gate6_completion_fatal.py)。
- Mandatory 节点唯一索引：[manifest](../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml) 的 `PROTO-12/23/25`、`E2E-H`。

```sh
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 6 \
  --id PROTO-12 --id PROTO-23 --id PROTO-25 --id E2E-H \
  --workdir .tmp/r6-acceptance
```

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate6_failure.py
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate6_multiple_requests.py \
  -k 'crossing or cancel_frozen_attached'
```

R6 PUBLISH suppression 事件闭包与冻结 KV 错误来源：
[Loader negative](../../util/mesh_ir/tests/integration/test_gate6_publish_closure.py)、
[事件闭包 unit](../../util/mesh_ir/tests/unit/test_publish_consumer_closure.py)、
[真实 source/prefix](../../util/mesh_ir/tests/integration/test_gate6_failure.py)。

## Gate 6 oracle 与累计验收（R7）

阶段出口：[Coding Spec §13](../../src/doc/ai_mesh/DUMMY_AI_CORE_GATE6_CODING_SPEC.md)。
[真实三次重复与产物篡改](../../util/mesh_ir/tests/integration/test_gate6_artifact_integrity.py)复用
[serving oracle](../../util/mesh_ir/mesh_ir/serving_e2e_oracle.py)；
[组件反例](../../util/mesh_ir/tests/unit/test_serving_e2e_oracle.py)与
[acceptance 验证](../../util/mesh_ir/tests/unit/test_gate6_acceptance.py)使用相同正式入口。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_serving_e2e_oracle.py \
  util/mesh_ir/tests/unit/test_gate6_acceptance.py
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate6_artifact_integrity.py
```

## Gate 7 定向压力与故障验收

[12 用户压力与生效配置校验](../../util/mesh_ir/tests/integration/test_gate7_pressure.py)、
[有限服务池与 provider 对账](../../util/mesh_ir/tests/integration/test_gate6_host_pools.py)、
[AXI 故障](../../util/mesh_ir/tests/integration/test_gate6_failure.py)和
[program 故障](../../util/mesh_ir/tests/integration/test_gate6_compute_failure.py)
由 mandatory manifest 的 E2E-F/G 调用。
Proxy 请求/响应队列的配置映射由
[gate4_runtime.py](../../configs/example/ai_mesh/gate4_runtime.py)供两个 runner 共用。

```sh
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 7 \
  --id E2E-F --id E2E-G --id HOST-1 --workdir .tmp/gate7-fg
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 7 \
  --id E2E-H --id E2E-C --id DC-26 --id DC-31 --workdir .tmp/gate7-stress
```

这些命令只运行显式 ID；累计出口仍要求新目录单次 Gate 7 全部 150 项通过。

### Serving/KV 分句定向验收

[固定 profile padding 与 MoE PAD](../../util/mesh_ir/tests/integration/test_gate6_padding.py)、
[wire 长度边界](../../util/mesh_ir/tests/integration/test_gate6_wire_admission.py)、
[Host 阶段与 repair](../../util/mesh_ir/tests/integration/test_gate6_repair.py)
与既有 KV、binding、PUBLISH 组件及生产用例在 mandatory manifest 中逐子例登记。

```sh
python3 tests/gem5/ai_mesh/run_manifest_selector.py --gate 6 \
  --id SERV-6 --id SERV-8 --id SERV-9 --id SERV-10 --id SERV-12 \
  --id SERV-13 --id SERV-17 --id SERV-18 --id SERV-19 --id SERV-20 \
  --id SERV-21 --workdir .tmp/r7-serving-clauses
```

Batch replay 的真实共享成员与 strict timing 检查：

```bash
PYTHONPATH=util/mesh_ir python3 -m pytest -q util/mesh_ir/tests/integration/test_gate6_batch_replay.py
```


## Gate 8 有限聚合网络

[FAST_EVENT 真实校准与压力测试](../../util/mesh_ir/tests/integration/test_gate8_fast_event.py)
复用 serving runner 与 Gate 7 Host 服务池场景。模式与容量合同见
[runtime schema](../../schemas/ai_mesh/agent_runtime_config_v1.schema.json)。
校准产物为 `calibration.json`，12 用户产物为 `pressure.json`；
`simulation_mode.json` 记录本次运行的聚合粒度与能力边界。
状态安装入口见 [serving runner](../../configs/example/ai_mesh/run_gate6_serving.py)。

```sh
scons build/AXI_MESH/gem5.opt -j4
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate8_fast_event.py
```

缓存状态导出与原子安装的组件验证见
[C++ owner tests](../../src/dev/ai_mesh/mesh_weight_cache_replay.test.cc)、
[Python 原子安装测试](../../util/mesh_ir/tests/unit/test_cache_replay_atomic.py)；
[FULL/FAST 真实导出](../../util/mesh_ir/tests/integration/test_gate8_cache_state.py)
验证运行期缓存元数据的跨语言读取。该验证不包含权重字节恢复或整机 checkpoint。

```sh
scons build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt -j4
build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt --gtest_filter='WeightCache*:*WeightCache*'
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_cache_replay_atomic.py \
  util/mesh_ir/tests/unit/test_gate5_strict_replay.py \
  util/mesh_ir/tests/unit/test_gate5_weight_cache.py
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate8_cache_state.py
```

启动时外部 warm-state 的输入与验证入口见
[runtime compiler](../../util/mesh_ir/mesh_ir/weight_runtime.py) 和
[C++ 安装准备](../../src/dev/ai_mesh/mesh_cache_replay.cc)。
真实测试覆盖 FULL/FAST/WINDOWED cold→warm 数据与输出对账、Python/Loader 分别拒绝非法输入，
以及失败 tombstone 恢复后禁止重试 DMA。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate8_warm_cache.py
```

同进程暂停、排空和恢复入口见
[program boundary](../../src/dev/ai_mesh/serving_program_boundary.cc)，
replay 依赖由 [replay owner](../../src/dev/ai_mesh/serving_mesh_replay.cc) 推导。
[真实用例](../../util/mesh_ir/tests/integration/test_gate8_program_boundary.py)
对账恢复后的 payload、cache replay state 与实际 SRAM 内容摘要，并覆盖非法转换拒绝。
网络切换验证见下方 WINDOWED_TIMING 入口；完整保存/恢复见 [checkpoint](serving_checkpoint.md)。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate8_program_boundary.py
```

WINDOWED_TIMING 的 program boundary 交接见
[GarnetNetwork](../../src/mem/ruby/network/garnet/GarnetNetwork.cc) 与
[聚合路径](../../src/mem/ruby/network/simple/AggregateNetwork.cc)。
窗口计划与 `timing_windows.json` 产物由
[ServingTimeline](../../util/mesh_ir/mesh_ir/serving_timeline.py)管理。
[真实测试](../../util/mesh_ir/tests/integration/test_gate8_windowed.py)
覆盖双向切换、业务状态和流量对账、既有路径时序一致及非法转换拒绝。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/integration/test_gate8_windowed.py
```

## Gate 8 serving checkpoint

使用合同与所有者索引见 [serving checkpoint](serving_checkpoint.md)。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_serving_checkpoint.py \
  util/mesh_ir/tests/integration/test_gate8_checkpoint.py \
  util/mesh_ir/tests/integration/test_gate6_repair.py::test_checkpoint_without_program_boundary_is_rejected
build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt --gtest_filter='MeshCheckpoint.*'
build/AXI_MESH/dev/ai_mesh/agent_workload_manager.test.opt --gtest_filter='AgentWorkloadManager.Checkpoint*'
build/AXI_MESH/mem/axi/axi_checkpoint.test.opt
```

## Gate 8 长任务校准

[十二用户、二十四任务实验](../../util/mesh_ir/tests/integration/test_gate8_long_task.py)
复用 serving runner，运行 FULL/FAST/WINDOWED 三臂。
[校准组件](../../util/mesh_ir/mesh_ir/serving_calibration.py)统一请求、输出与端点流量口径；
`calibration.json` 包含每请求误差、延迟分布、运行成本、选中窗口 DMA 对账和输入/产物摘要。
运行状态与能力边界见 [验收索引](../../.tmp/docs/gate8-long-task.md)。

```sh
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_serving_calibration.py \
  util/mesh_ir/tests/integration/test_gate8_long_task.py \
  --basetemp=.tmp/gate8-long-task
```

## Serving physical deployment

[Deployment and fault-drain checks](../../util/mesh_ir/tests/integration/test_serving_deployment_runtime.py) generate current ABI Programs and exercise shared or separate host owners. [Composition contracts](../../util/mesh_ir/tests/unit/test_gate6_scenario.py) consume the architecture and runtime configuration.

```bash
PYTHONPATH=util/mesh_ir python3 -m pytest -q \
  util/mesh_ir/tests/unit/test_gate6_scenario.py \
  util/mesh_ir/tests/integration/test_serving_deployment_runtime.py
```

Binary startup image contracts: [native tests](../../src/dev/ai_mesh/mesh_memory_image.test.cc) and [Serving integration](../../util/mesh_ir/tests/integration/test_memory_file_image_runtime.py). Partitioned view and unary native execution: [targeted integration](../../util/mesh_ir/tests/integration/test_partitioned_slice.py).

- [Tensor payload and memory file contracts](tensor_payload.md).

- Gather runtime coverage: [AXI read and error paths](../../util/mesh_ir/tests/integration/test_gather_runtime.py), [compiled driver cancellation and checkpoint](../../util/mesh_ir/tests/integration/test_gather_driver_runtime.py), [seeded mock transport](../../util/mesh_ir/tests/integration/test_gather_mock_runtime.py).
- Compiled prefill execution: [parameterized entrypoint](prefill_smoke.md), [actual driver and negative artifact checks](../../util/mesh_ir/tests/integration/test_prefill_smoke.py).
- Preparation execution: [analysis and rejection tests](../../util/mesh_ir/tests/unit/test_prepare_analysis.py), [serial/parallel artifact equivalence and native smoke](../../util/mesh_ir/tests/integration/test_prepare_execution.py).

ROM allocation and immutable derivation tests: [capacity planning](../../util/mesh_ir/tests/unit/test_command_capacity_planning.py), [early admission](../../util/mesh_ir/tests/unit/test_command_rom_capacity.py), [proof reuse](../../util/mesh_ir/tests/unit/test_scheduled_derivation_reuse.py), [descriptor indexing](../../util/mesh_ir/tests/unit/test_descriptor_address_index.py) and [native 32-worker carrier](../../util/mesh_ir/tests/integration/test_streamed_weight_reuse.py).

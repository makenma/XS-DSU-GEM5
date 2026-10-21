# Compiled prefill smoke

[CLI and artifact checks](../../util/mesh_ir/mesh_ir/prefill_smoke.py) execute a single compiled prefill through the CPU driver, SQ/CQ, computed HBM workspace, bounded publication and Host ACK. The source must contain one compiled variant and one contiguous output. DummyCore bytes establish functional data movement and timing, not neural numerical accuracy.

With `util/mesh_ir` on `PYTHONPATH`, use independent stages:

```bash
python -m mesh_ir.compile --exported-program "$EXPORT" --arch "$ARCH" --config "$COMPILE_CONFIG" --output "$COMPILED" --workers 32
python -m mesh_ir.prefill_smoke prepare --compiled "$COMPILED" --arch "$ARCH" --payload "$PAYLOAD" --runtime-config "$RUNTIME_CONFIG" --workload "$WORKLOAD" --output "$BUNDLE" --input-tokens 16 --publish-chunk-bytes 524288 --workers 32
python -m mesh_ir.prefill_smoke run --bundle "$BUNDLE" --gem5 "$GEM5" --sim-tick-limit "$TICK_LIMIT"
python -m mesh_ir.prefill_smoke check --bundle "$BUNDLE"
```

`PAYLOAD` is the [normalized tensor manifest](tensor_payload.md). Supply the actual runtime resource configuration and a single-task, single-round workload. Host arenas must fit the complete packed inputs and output. Preparation validates the resulting configuration through the shared Host planner. Compilation and preparation require new output directories; execution requires an unused `runtime` directory.

`COMPILED` is the complete compiler publication directory. [CompiledPublication](../../util/mesh_ir/mesh_ir/publication.py) checks its artifact set, byte digests, manifest, Graph/Kernel/Program identities and registered pass chain. Preparation requires the payload's archive and semantic export identities to match the compiler source. The bundle retains `compilation-manifest.json` and `compile_report.json` beside the admitted source Program. [Source rejection tests](../../util/mesh_ir/tests/integration/test_compilation_source.py) cover broken artifacts and pass identities.

Preparation uses the shared [balanced binding policy](../../util/mesh_ir/mesh_ir/binding_layout.py) and writes the admitted `model-invocation.json`. The tensor image and [Host model requirements](../../util/mesh_ir/mesh_ir/host_bindings.py) derive their addresses from this invocation. The Serving entrypoint accepts repeated `--model-invocation` files for streamed model variants; variants sharing a request weight must resolve to identical content and physical ranges. Native batch freezing admits the resulting request addresses through the existing invocation path. Source Program bindings and composition provenance remain immutable.

[Full-model preparation performance evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/prepare-performance-diagnosis.json) indexes live samples, frozen-source prefix traces and attribute-projection measurements.

[Preparation analysis execution](../../util/mesh_ir/mesh_ir/analysis/execution.py) owns worker scope and substage progress. `bundle.json.analysis` records stage outcomes and process-pool task diagnostics. [Preparation equivalence and native execution](../../util/mesh_ir/tests/integration/test_prepare_execution.py) and [analysis admission tests](../../util/mesh_ir/tests/unit/test_prepare_analysis.py) cover the shared [record comparison](../../util/mesh_ir/mesh_ir/analysis/derivation.py), [memory projection](../../util/mesh_ir/mesh_ir/scheduled/records.py) and [burst counts](../../util/mesh_ir/mesh_ir/burst_splitter.py). [Validation and full-model preparation](../../.tmp/docs/qwen3-8b-smoke/stage-b/prepare-performance-result.json) index measured results.

`bundle.json`, `run.json` and `check.json` each own their corresponding stage result. An absent stage result means `NOT_RUN`; a failed child or check exits nonzero. Input, Program, architecture, executable and runtime artifact identities are recorded at their respective boundaries. A successful component bundle does not establish the [official Qwen smoke](qwen3_8b_smoke.md).

The [Serving runtime entrypoint](../../configs/example/ai_mesh/run_gate6_serving.py) accepts repeated `--verify-memory-range ADDRESS BYTES`. `--memory-verify-bytes-limit` controls endpoint byte observations independently of `--compute-bytes-limit`. Large final outputs can be inspected with intermediate compute byte dumping disabled.

[Real compiled driver and negative checks](../../util/mesh_ir/tests/integration/test_prefill_smoke.py), [gather driver and endpoint observations](../../util/mesh_ir/tests/integration/test_gather_driver_runtime.py), and [Serving phase oracle](../../util/mesh_ir/mesh_ir/serving_e2e_oracle.py) own the executable contracts.

[Workload statistics](../../util/mesh_ir/mesh_ir/workload_statistics.py) consume the admitted execution view for each phase. `workload-report.json` records per-core work and SRAM high water, including phase-idle cores. `check.json` separates cores with nonzero compute work from cores that execute only empty compute partitions or control/DMA commands.

Command ROM occupancy is the admitted command count per core and selected variant, across that core's streams. [Python admission](../../util/mesh_ir/mesh_ir/scheduled/verify.py) and [native admission](../../src/dev/ai_mesh/mesh_ir_verifier.cc) enforce the architecture's `command_rom_entries`; the runtime loader receives this capacity explicitly. `workload-report.json` records phase occupancy and capacity, including zero occupancy for idle cores. [Boundary and rejection tests](../../util/mesh_ir/tests/unit/test_command_rom_capacity.py) exercise the same composed Program in both readers.

[Unified drain checks](../../util/mesh_ir/mesh_ir/runtime_drain.py) validate physical core coverage, command/tag/pin retirement, engine occupancy, credit conservation, network queues and memory endpoints. [Request latency measurement](../../util/mesh_ir/mesh_ir/serving_e2e_oracle.py) owns event boundaries and tick conversion. The report records request, retirement, business and mesh windows separately from process wall time.

[Deployment audit](../../util/mesh_ir/mesh_ir/serving_deployment_audit.py) checks the instantiated simulator configuration against the shared architecture and Serving deployment. Core capabilities use the same [parameter projection](../../util/mesh_ir/mesh_ir/runtime_fabric.py) as the runner. Router links, lane count, physical memory backing and CPU/frontend/core connections are checked by identity.

`run` enables `--fabric-snapshots` in the Serving entrypoint. The existing [native observer](../../src/dev/ai_mesh/mesh_experiment_observer.cc) records initial and final target/backend and network counters. [Physical traffic audit](../../util/mesh_ir/mesh_ir/serving_memory_audit.py) joins actual DMA burst addresses and CPU/frontend AXI facts with those independent counters. `fabric-report.json` records core HBM useful bytes separately from AXI beat bytes, all physical owner counters, backend service and queue high water, and network packet/flit/wire totals. Owners with no traffic remain present. Backend bandwidth uses the explicitly recorded whole-simulation window, including Host think time. The run artifact manifest covers both snapshots and the actual simulator configuration; `check.json` owns the report digest.

[Placement reservations](../../util/mesh_ir/mesh_ir/passes/command_reservations.py) and [early capacity admission](../../util/mesh_ir/mesh_ir/analysis/command_capacity.py) enforce ROM resources before descriptor and traffic construction. [Capacity-aware compilation](../../util/mesh_ir/tests/unit/test_command_capacity_planning.py) and [native constrained-ROM execution](../../util/mesh_ir/tests/integration/test_streamed_weight_reuse.py) own regression coverage.

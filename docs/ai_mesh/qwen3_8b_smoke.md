# Qwen3-8B smoke

- [Retained affine views and full-model placement storage](retained_views.md).

- [Current full-model pipeline and observation commands](../../.tmp/docs/qwen3-8b-smoke/stage-b/smoke-monitoring.md).
- [Native startup workspace admission CPU samples](../../.tmp/docs/qwen3-8b-smoke/stage-b/runtime-startup-performance-diagnosis.json).
- [Dependency-closed operator smoke plan](../../.tmp/docs/qwen3-8b-smoke/stage-b/operator-smoke-plan.md) and [execution evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/operator-smoke-current.json).
- [First decoder block network experiment](../../.tmp/docs/qwen3-8b-smoke/stage-b/decoder-smoke-plan.md) and [report index](../../.tmp/docs/qwen3-8b-smoke/stage-b/decoder-smoke-current.json).
- [Current compiler CPU sampling and real-prefix verification profile](../../.tmp/docs/qwen3-8b-smoke/stage-b/movement-performance-diagnosis.json).

- [Parameterized compiled prefill entrypoint](prefill_smoke.md).
- [Entrypoint component and target-topology evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-smoke-entrypoint-result.json).
- [Phase work, resource drain and latency audit](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-observation-audit-result.json).
- [Instantiated topology and physical HBM/network audit](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-fabric-audit-result.json).
- [Per-core command ROM capacity admission](../../.tmp/docs/qwen3-8b-smoke/stage-b/command-rom-capacity-result.json).
- [Serving model layout and shared invocation evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/serving-model-layout-review.json).
- [Compiler publication and payload source identity](../../.tmp/docs/qwen3-8b-smoke/stage-b/compilation-source-identity-result.json).

- [Smoke report](../../.tmp/docs/qwen3-8b-smoke/report.md).
- [Execution result](../../.tmp/docs/qwen3-8b-smoke/result.json).
- [Model, hardware and workload inputs](../../.tmp/docs/qwen3-8b-smoke/plan.json).
- [Full-model frontend and CPU reference evidence](../../.tmp/docs/qwen3-8b-smoke/frontend-result.json).
- [Architecture admission](../../.tmp/docs/qwen3-8b-smoke/architecture-admission.json).
- [Serving topology probe](../../.tmp/docs/qwen3-8b-smoke/serving-topology-probe.json).
- [Implementation plan](../../.tmp/docs/qwen3-8b-smoke/integration-plan.md).
- [Gather compiler review](../../.tmp/docs/qwen3-8b-smoke/stage-b/compiler-review.md).
- [Indexed HBM gather contract](gather_streaming.md).
- [Real AXI gather evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/runtime-gather-result.json).
- [Backend diagnostic failure analysis](../../.tmp/docs/qwen3-8b-smoke/stage-b/timed-stack-diagnosis.json).
- [Control initiator contracts](../../util/mesh_ir/tests/unit/test_control_initiators.py).
- [Runtime memory range coverage](../../src/mem/axi/axi_simple_memory.test.cc).
- [Physical memory registry evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/memory-registry-result.json).
- [Target compute topology probe](../../.tmp/docs/qwen3-8b-smoke/stage-b/gather-deployment-result.json).
- [Immutable deployment](../../util/mesh_ir/mesh_ir/runtime_deployment.py) and [fabric parameter projection](../../util/mesh_ir/mesh_ir/runtime_fabric.py).
- [Serving deployment and driver evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/serving-deployment-review.json).
- [Placement geometry review](../../.tmp/docs/qwen3-8b-smoke/stage-b/placement-source-review.md).
- [Placement live-storage equivalence](../../.tmp/docs/qwen3-8b-smoke/stage-b/placement-live-storage-result.json).
- [Binary startup memory image evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/memory-file-image-result.json).
- [Partitioned RoPE slice](../../.tmp/docs/qwen3-8b-smoke/stage-b/blocker-rope-slice.md).
- [Attention output reshape](../../.tmp/docs/qwen3-8b-smoke/stage-b/blocker-attention-reshape.md).
- [Actual driver input payload](../../.tmp/docs/qwen3-8b-smoke/stage-b/driver-input-result.json).
- [Full-shape attention runtime evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/attention-runtime-result.json).

- [Tensor payload and memory file contracts](tensor_payload.md).
- [Prefill lifecycle component evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-lifecycle-result.json).
- [Mapped phase I/O and multi-input driver evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/serving-mapped-io-result.json).
- [No-KV computed workspace admission](../../.tmp/docs/qwen3-8b-smoke/stage-b/request-workspace-result.json).
- [No-KV executor and Host plan tests](../../src/dev/ai_mesh/serving_prefill.test.cc), [driver publication test](../../util/mesh_ir/tests/integration/test_prefill_driver_runtime.py).
- [No-KV executor evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-executor-result.json).
- [No-KV computed driver and bounded publication evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/prefill-driver-result.json).
- [Persistent parameter and buffer binding evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/persistent-model-result.json).
- [Compiled Serving composition design](../../.tmp/docs/qwen3-8b-smoke/stage-b/program-composition-design.md).
- [Compiled Program composition evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/program-composition-result.json).
- [Target-topology compiled TP4 driver and fault-drain evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/compiled-target-result.json).
- [Verification scaling and window allocation evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/verification-allocation-result.json).
- [Scheduled verification performance diagnosis](../../.tmp/docs/qwen3-8b-smoke/stage-b/scheduled-verification-performance-diagnosis.json).
- [Full-model command ROM capacity failure](../../.tmp/docs/qwen3-8b-smoke/stage-b/full-model-command-rom-failure.json).
- [Command ROM allocation and compiler performance evidence](../../.tmp/docs/qwen3-8b-smoke/stage-b/command-rom-performance-result.json).

# ABI 2.0 integration

- Wire contract: [ABI schema](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml).
- Python scoped symbols: [ProgramBindingCatalog](../../util/mesh_ir/mesh_ir/scheduled/binding_catalog.py).
- C++ scoped symbols: [ProgramSemanticContext](../../src/dev/ai_mesh/mesh_ir_semantic_context.hh).
- Prepared invocation: [MeshProgramInvocation](../../src/dev/ai_mesh/mesh_program_invocation.hh).
- Serving member geometry: [serving_profiles.py](../../util/mesh_ir/mesh_ir/serving_profiles.py).
- Serving tensor subranges: [Python catalog](../../util/mesh_ir/mesh_ir/serving_tensor_bindings.py), [native admission](../../src/dev/ai_mesh/mesh_serving_tensor_bindings.cc), [frozen batch](../../src/dev/ai_mesh/mesh_serving_batch.cc), [contract tests](../../util/mesh_ir/tests/unit/test_serving_tensor_bindings.py).
- Host weight plan: [host_bindings.py](../../util/mesh_ir/mesh_ir/host_bindings.py).
- KV identity: [Python](../../util/mesh_ir/mesh_ir/kv_layout.py), [C++](../../src/dev/ai_mesh/mesh_kv_layout.cc).
- Authored memory and Serving variants: [authoring.py](../../util/mesh_ir/mesh_ir/authoring.py), [serving_programs.py](../../util/mesh_ir/mesh_ir/serving_programs.py).

- Runtime checkpoint records: [DMA](../../src/dev/ai_mesh/mesh_dma_checkpoint.cc), [dispatcher](../../src/dev/ai_mesh/mesh_dispatcher_checkpoint.cc).
- Shared compute commit: [mesh_compute_commit](../../src/dev/ai_mesh/mesh_compute_commit.hh).
- DMA command lifecycle: [dma_command_lifecycle](../../src/dev/ai_mesh/dma_command_lifecycle.hh).

- Static and dynamic DMA execution identity: [runtime observations](../../src/dev/ai_mesh/mesh_runtime_observations.hh), [burst attribution](../../src/dev/ai_mesh/burst_attribution.hh), [timing reconciliation](../../util/mesh_ir/mesh_ir/runtime_dma_timing.py).
- MoE layer operand selection: [materializer](../../util/mesh_ir/mesh_ir/moe_materializer.py).
- Cache startup binding: [mesh_cache_replay](../../src/dev/ai_mesh/mesh_cache_replay.cc).

- Serving settlement evidence: [serving_error_oracle](../../util/mesh_ir/mesh_ir/serving_error_oracle.py).
- Validated schema cache: [schema](../../util/mesh_ir/mesh_ir/schema.py).
- Archive source and environment validation: [archive_manifest](../../util/mesh_ir/mesh_ir/archive_manifest.py).

- Serving acceptance: [gate6_acceptance](../../configs/example/ai_mesh/gate6_acceptance.py).
- Generated enum and section interfaces: [ABI generator](../../util/mesh_ir/mesh_ir/abi/generate_abi.py).

- Local padding and external input views: [authored storage](../../util/mesh_ir/mesh_ir/authoring.py), [Python/C++ admission](../../util/mesh_ir/tests/unit/test_abi2_serving_padding.py), [runtime](../../util/mesh_ir/tests/integration/test_gate6_padding.py).
- Serving rejection and partial commit cases: [PUBLISH closure](../../util/mesh_ir/tests/integration/test_gate6_publish_closure.py), [multirow KV](../../util/mesh_ir/tests/integration/test_gate6_multirow_prefix.py), [shared errors](../../util/mesh_ir/tests/integration/test_gate6_shared_failure.py).
- Issued DMA traffic evidence: [acceptance](../../util/mesh_ir/mesh_ir/acceptance.py), [drain cases](../../util/mesh_ir/tests/unit/test_acceptance_dma_packets.py).
- SRAM placement timing: [runtime comparison](../../util/mesh_ir/tests/integration/test_sram_placement_runtime.py).
- Instance retirement and prestart rollback: [core lifecycle](../../src/dev/ai_mesh/mesh_dummy_core.cc), [mandatory acceptance](../../tests/gem5/ai_mesh/mandatory_case_manifest.yaml).

- Qwen3-8B business smoke: [execution evidence](qwen3_8b_smoke.md).

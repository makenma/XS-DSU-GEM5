# Program contract

The binary and JSON contract is defined by [mesh_ir_abi.yaml](../../util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml); its generated reference is [mesh_ir_abi.md](../generated/mesh_ir_abi.md).

## Admission and execution

- Python admission: [scheduled/verify.py](../../util/mesh_ir/mesh_ir/scheduled/verify.py).
- C++ admission and retained facts: [mesh_ir_verifier.cc](../../src/dev/ai_mesh/mesh_ir_verifier.cc), [mesh_ir_admission.hh](../../src/dev/ai_mesh/mesh_ir_admission.hh).
- Invocation preparation: [mesh_program_invocation.cc](../../src/dev/ai_mesh/mesh_program_invocation.cc).
- Profile execution projection: [execution_view.py](../../util/mesh_ir/mesh_ir/execution_view.py), [mesh_execution_view.cc](../../src/dev/ai_mesh/mesh_execution_view.cc).
- SRAM partition contract: [memory_partitions.py](../../util/mesh_ir/mesh_ir/memory_partitions.py), `validateRuntimeArch` in the C++ admission owner.

## Identity

- Canonical representation: [canonical.py](../../util/mesh_ir/mesh_ir/canonical.py), [mesh_binary_canonical.cc](../../src/dev/ai_mesh/mesh_binary_canonical.cc).
- Serving profile identity: [serving_profiles.py](../../util/mesh_ir/mesh_ir/serving_profiles.py), [mesh_serving_projection.cc](../../src/dev/ai_mesh/mesh_serving_projection.cc).
- KV layout identity: [kv_layout.py](../../util/mesh_ir/mesh_ir/kv_layout.py), [mesh_kv_layout.cc](../../src/dev/ai_mesh/mesh_kv_layout.cc).

## Focused contracts

The `test_abi2_*` files in [unit tests](../../util/mesh_ir/tests/unit/) cover format rejection, cross-language bytes and identities, variant projection, domain admission and partition validation. Whole-system runtime acceptance is tracked separately from these component contracts.

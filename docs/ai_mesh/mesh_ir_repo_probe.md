# Torch Mesh IR repository probe

This document is the Gate 0 source index for the Torch frontend work. The machine-readable inventories are [probe.json](../../.tmp/docs/torch-gate-0/probe.json) and [requirements.json](../../.tmp/docs/torch-gate-0/requirements.json); they own exact commands, statuses, evidence, gaps, proposed verification, and gate assignments. The normative contract remains [TORCH_EXPORT_MESH_IR_CODEX_SPEC.md](../../src/doc/ai_mesh/TORCH_EXPORT_MESH_IR_CODEX_SPEC.md).

## Provenance

- Worktree branch: `feature/ai-mesh-torch-frontend`.
- Baseline and current probe HEAD: `7a72d51db2dee4f667fab41cd9ec9c6eb31192a7`.
- AXI/Garnet implementation ancestor: `cef83f0f7eb2b4111b3797d5e65e3edd729c4dc1`. `git merge-base --is-ancestor ... HEAD` returned 0.
- Results in [axi_garnet_implementation_report.md](axi_garnet_implementation_report.md) are historical evidence from that ancestor. Gate 0 did not rerun them.
- `AI_MESH_MODELING_SPEC.md` and `Agents.custome.md` were not present in a repository search. Decisions that would depend on those missing authorities require main-agent review.

## Current entrypoints and ownership

| Concern | Current source | Gate 0 conclusion |
|---|---|---|
| Scheduled model and canonical JSON | `util/mesh_ir/mesh_ir/model.py` | Reusable Scheduled records; no Graph IR, Kernel IR, schema identity, required feature set, or cross-layer lineage. |
| Hand-written programs | `util/mesh_ir/mesh_ir/builder.py` | `ProgramBuilder` deliberately does not lower `torch.export`. |
| CLI | `util/mesh_ir/mesh_ir/cli.py` | Builds named hand-written programs and verifies binaries; neither required Torch compile entrypoint exists. |
| Wire ABI authority | `util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml` | Existing SSOT and generator are reusable. Wire changes remain in the designated ABI gate. |
| Python ABI | `util/mesh_ir/mesh_ir/abi/{encoder,decoder,verifier}.py` | Substantial Scheduled validation and round-trip support; optional-section retention is incomplete. |
| DMA oracle | `util/mesh_ir/mesh_ir/burst_splitter.py` | Independent reference implementation exists; exact mandatory boundary coverage is incomplete. |
| Loader and dispatch | `src/dev/ai_mesh/mesh_program_loader.*`, `mesh_dispatcher.*` | Installation/execution exists; exact entrypoint/profile selection and relocation binding do not. |
| Compute and SRAM | `src/dev/ai_mesh/mesh_dummy_core.*`, `tensor_sram.hh` | Analytic compute and deterministic surrogate bytes exist; this is not numeric GEMM. Bank interleave is implicit. |
| DMA and fabric | `src/dev/ai_mesh/{tensor_dma_engine,axi_tensor_dma_engine,axi_garnet_bridge,peer_sram_aperture}.*` | Mock and real AXI/Garnet paths are reusable. |
| Build/config | `src/dev/ai_mesh/{AiMesh.py,SConscript}`, `configs/ruby/AXI_MESH.py`, `configs/topologies/AxiMeshDie.py` | Existing integration is the runtime substrate, not Torch compiler evidence. |
| Real run entrypoint | `configs/example/ai_mesh/run_mesh_dma_garnet.py` | Real fabric configuration exists; Gate 0 did not run gem5. |
| Command index | [mesh_ir_test_commands.md](mesh_ir_test_commands.md) | Existing commands target hand-written Scheduled programs and Agent/Dummy-Core acceptance. |

## Gate 0 findings

The exact source references and proposed verification are in `probe.json`. The interface-blocking findings are:

- Effective architecture mismatch: `effective.py` accepts behavior-changing overrides, but both run configs pass `base_digest()` to the loader. The Torch contract requires the resolved effective architecture digest to be the compile/load authority.
- Frontend and lineage are absent: no genuine `torch.export` compatibility boundary, Graph IR, Kernel IR, pass pipeline, or Graph → Kernel → Scheduled semantic hash chain exists.
- Selection and binding are absent: dispatcher inputs do not name entrypoint/profile/symbol bindings, and the loader decodes relocation rows without applying dispatch bindings.
- SRAM bank geometry is not architecture data: `TensorSram::bankOf` uses `offset / line_bytes`, while configs feed read throughput into `sram_line_bytes`. Interleave and bandwidth must be independent canonical facts.
- Sparse core-ID aperture coverage is inconsistent: the loader uses `owner_core * tile_stride`, while the builder defaults aperture bytes to `core_count * tile_stride`. The frozen direction retains ID-based aperture addressing with checked extent through the maximum legal core ID; row-major list order is used separately for coordinates.
- Fabric facts are split: flit bytes, wire-header bytes, and endpoint-to-router geometry live in run/config/experiment profiles rather than `ArchManifest`. Gate 2 needs one resolved canonical view for traffic prediction; Gate 5 must prove runtime equality.
- Canonical op semantics do not fit the current attrs: several operations collapse into unconstrained integers, normalization algorithms are indistinguishable, and view/movement/embedding attrs are absent.
- Diagnostic names drift among prose, Python, and C++. The frozen SSOT is `util/mesh_ir/mesh_ir/diagnostics.yaml`, consumed by Python in Gate 1 and generated for C++ in Gate 3, with no executable legacy aliases.

Secondary gaps are known optional-section retention, tensor-state SSA, complete expected/actual traffic identities, explicit orthogonal runtime modes, and Torch-specific acceptance registration. Existing deterministic byte checks are compute-surrogate checks and must not be described as numeric GEMM.

## ABI authority

[DUMMY_AI_CORE_AGENT_CODEX_SPEC.md](../../src/doc/ai_mesh/DUMMY_AI_CORE_AGENT_CODEX_SPEC.md) section 7.2 explicitly amends Torch section 9.2. The existing 128-byte header assigns `required_features:u64` to bytes 104..111 and retains bytes 112..127 as reserved. `mesh_ir_abi.yaml` already records those offsets. Later ABI work must preserve this amendment and use the ABI YAML/generator path; it must not claim an unamended Torch header match.

## Test evidence

Gate 0 ran only the permitted small checks:

| Check | Result | Evidence |
|---|---|---|
| `python3 util/mesh_ir/mesh_ir/abi/generate_abi.py --check` | exit 0; no output | `../../.tmp/docs/torch-gate-0/abi-check.log` |
| Fixed ABI/arch/splitter pytest baseline | 31 passed, 0 failed/errors/skipped, exit 0 | `../../.tmp/docs/torch-gate-0/python-baseline.log`, `../../.tmp/docs/torch-gate-0/python-baseline.junit.xml` |

The pytest result covers only existing Scheduled ABI, architecture validation, and reference splitter tests. In particular, its ten splitter tests do not establish the full `TORCH-PY-08` vector: the current max-burst test uses `(L±1)*W`, not the required `L*W±1` bytes. No Torch compiler suite, C++ build, gem5 run, or AXI replay was executed here.

Python 3.12.3, pytest 9.1.1, PyYAML 6.0.1, and jsonschema 4.10.3 are available. PyTorch and SymPy are absent, and `util/mesh_ir/requirements-lock.txt` does not exist. Gate 1 therefore requires a pinned isolated compiler environment and must not alter the gem5 build environment.

## Gate boundaries

Gate 1 is limited to the real export/load frontend, compatibility DTOs, diagnostics authority, canonical Graph IR, concrete Graph-profile specialization, and passes 1–5. User-visible symbol identity is explicit project metadata; external `.pt2` display names are never interpreted as semantic names such as `B` or `S`.

Independent Scheduled variants, Kernel IR, scheduling, SRAM planning, and the traffic oracle complete in Gate 2. `TORCH-PY-04` therefore completes in Gate 2 even though Gate 1 proves its concrete Graph variants and unbound-symbol failure.

The final compile/export-and-compile CLI, binary metadata, atomic success directory, failed-directory diagnostics, manifest closure, and checksums complete together in Gate 3. Earlier library artifacts are not a successful compile bundle.

Production runtime, shared ABI, global acceptance manifest, and gem5 build changes are outside Gate 1. The exact proposed file boundary and shared-file coordination list are maintained in `probe.json`; Gate 1 may not begin until the Gate 0 review is approved.

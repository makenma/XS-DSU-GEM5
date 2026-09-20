import copy
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch, load_arch_text, validate_arch
from mesh_ir.compile_config import CompileOverrides, load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"


def test_architecture_is_typed_sparse_safe_and_row_major():
    arch = load_arch(ARCH_PATH)
    assert arch.core_id_at(0, 0) == 7
    assert arch.core_id_at(1, 1) == 13
    assert arch.sram_bank_interleave_bytes == 64
    aperture = next(region for region in arch.regions if region.kind == "CORE_SRAM_APERTURE")
    assert aperture.bytes >= 14 * aperture.tile_stride


def test_five_by_five_example_uses_the_same_schema_and_row_major_coordinates():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    assert (arch.mesh_rows, arch.mesh_cols, len(arch.core_ids)) == (5, 5, 25)
    assert arch.core_id_at(4, 4) == 24


def test_arch_digest_covers_timing_and_resource_fields():
    arch = load_arch(ARCH_PATH)
    changed = copy.deepcopy(arch)
    changed.sram_bank_interleave_bytes *= 2
    assert changed.digest() != arch.digest()


@pytest.mark.parametrize(
    "old,new",
    [
        ("bank_interleave_bytes: 64", "bank_interleave_bytes: true"),
        ("bank_interleave_bytes: 64", "bank_interleave_bytes: 64.0"),
        ("bank_interleave_bytes: 64", "bank_interleave_bytes: 3"),
        ("decode_width: 1", "decode_width: 1.0"),
        ("data_bytes: 32", "data_bytes: 32.0"),
        ("command_rom_entries: 65536", "command_rom_entries: 1208925819614629174706176"),
        ("descriptor_queue_depth: 16", "descriptor_queue_depth: -1"),
        ("data_bytes: 32", "data_bytes: 24"),
        ("enforce_4k_boundary: true", "enforce_4k_boundary: false"),
        ("core_ids: [7, 2, 9, 13]", "core_ids: [7, 2, 9, 65535]"),
    ],
)
def test_architecture_rejects_nonsensical_values(old, new):
    with pytest.raises(MeshIrError) as error:
        load_arch_text(ARCH_PATH.read_text().replace(old, new))
    assert error.value.code == "E_CONFIG"


def test_architecture_rejects_duplicate_yaml_and_unknown_keys():
    text = ARCH_PATH.read_text()
    for bad in (text + "\nclock_hz: 1\n", text.replace("clock_hz:", "unknown: 1\nclock_hz:")):
        with pytest.raises(MeshIrError) as error:
            load_arch_text(bad)
        assert error.value.code == "E_CONFIG"


def test_public_validation_rejects_mutated_invalid_architecture():
    arch = load_arch(ARCH_PATH)
    arch.dma_read_outstanding = 0
    with pytest.raises(MeshIrError) as error:
        validate_arch(arch)
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "field,value",
    [
        ("decode_width", True),
        ("mesh_rows", 2.0),
        ("axi_qos_default", 16),
        ("schema_version", "mesh-arch-v9"),
    ],
)
def test_public_validation_uses_the_same_schema_as_loading(field, value):
    arch = load_arch(ARCH_PATH)
    setattr(arch, field, value)
    with pytest.raises(MeshIrError) as error:
        validate_arch(arch)
    assert error.value.code == "E_CONFIG"


def test_public_validation_rejects_overflowed_throughput():
    arch = load_arch(ARCH_PATH)
    arch.tensor_macs_per_cycle["fp32"] = 2**80
    with pytest.raises(MeshIrError) as error:
        validate_arch(arch)
    assert error.value.code == "E_CONFIG"


def test_negative_region_id_is_rejected():
    with pytest.raises(MeshIrError) as error:
        load_arch(ARCH_PATH).region_by_id(-1)
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize("y,x", [(0.0, 0), (0, True), (-1, 0), (0, 2)])
def test_mesh_coordinates_are_strict_bounded_integers(y, x):
    with pytest.raises(MeshIrError) as error:
        load_arch(ARCH_PATH).core_id_at(y, x)
    assert error.value.code == "E_CONFIG"


COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles:
  forward:
    - {profile_id: small, logical_batch: 2, s2: 4}
symbol_bindings:
  forward:
    logical_batch: {input: x, axis: 0}
parallelism: {tensor_parallel: 2, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 8, gemm_n: 8, gemm_k: 8, double_buffer: true}
collectives: {all_reduce_algorithm: ring, chunk_bytes: 256}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


def test_compile_config_is_typed_and_records_explicit_override_provenance():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(COMPILE, arch)
    resolved = resolve_compile_config(config, arch, CompileOverrides(gemm_m=16))
    assert resolved.config.tiling.gemm_m == 16
    assert [(item.path, item.source, item.value) for item in resolved.provenance if item.source == "cli"] == [
        ("tiling.gemm_m", "cli", 16)
    ]


def test_schema_default_provenance_is_distinct_from_explicit_workload_value():
    arch = load_arch(ARCH_PATH)
    omitted = load_compile_config_text(COMPILE.replace(", reserve_cores: []", ""), arch)
    explicit = load_compile_config_text(COMPILE, arch)
    assert omitted.placement == explicit.placement
    assert "placement.reserve_cores" in omitted.defaulted_paths
    assert "placement.reserve_cores" not in explicit.defaulted_paths
    default_entries = [item for item in resolve_compile_config(omitted, arch).provenance if item.source == "schema-default"]
    assert [(item.path, item.value) for item in default_entries] == [("placement.reserve_cores", [])]


@pytest.mark.parametrize("old,new", [("gemm_m: 8", "gemm_m: 8.0"), ("gemm_m: 8", "gemm_m: 1208925819614629174706176"), ("tensor_parallel: 2", "tensor_parallel: 2.0")])
def test_compile_schema_uses_strict_representable_integers(old, new):
    with pytest.raises(MeshIrError) as error:
        load_compile_config_text(COMPILE.replace(old, new), load_arch(ARCH_PATH))
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize("overrides", [CompileOverrides(gemm_m=1.5), CompileOverrides(gemm_m=2**80), CompileOverrides(tensor_parallel=True)])
def test_typed_overrides_reject_non_integer_or_unrepresentable_values(overrides):
    arch = load_arch(ARCH_PATH)
    with pytest.raises(MeshIrError):
        resolve_compile_config(load_compile_config_text(COMPILE, arch), arch, overrides)


@pytest.mark.parametrize(
    "old,new",
    [
        ("tensor_parallel: 2", "tensor_parallel: true"),
        ("pipeline_parallel: 1", "pipeline_parallel: 2"),
        ("allowed_cores: [7, 2]", "allowed_cores: [7, 99]"),
        ("all_reduce_algorithm: ring", "all_reduce_algorithm: auto"),
        ("tensor_data: DIGEST_ONLY", "tensor_data: NUMERIC"),
    ],
)
def test_compile_config_rejects_invalid_policy(old, new):
    with pytest.raises(MeshIrError) as error:
        load_compile_config_text(COMPILE.replace(old, new), load_arch(ARCH_PATH))
    assert error.value.code in {"E_CONFIG", "E_PLACEMENT_INFEASIBLE"}

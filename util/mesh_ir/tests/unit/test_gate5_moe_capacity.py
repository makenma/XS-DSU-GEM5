import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_capacity import (
    expert_capacity,
    instance_view_requirements,
    layer_view_bounds,
    moe_capacity_requirements,
    validate_moe_capacity,
)
from mesh_ir.weight_registry import (
    load_model_weight_image,
    load_program_weight_registry,
)

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures"
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
LEGACY_ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
MOE_IMAGE = FIXTURES / "gate5/moe_min.mshb"
TOTAL_TOKENS = 8


@pytest.fixture(scope="module")
def context():
    arch = load_arch(ARCH_PATH)
    program = decode_program(MOE_IMAGE.read_bytes())
    image = load_model_weight_image(FIXTURES / "model_weight_image_v1.json")
    registry = load_program_weight_registry(
        FIXTURES / "program_weight_bindings_v1.json", image, program)
    return arch, program, registry


def test_gate5_expert_capacity_uses_checked_q16_ceiling():
    assert expert_capacity(0, 2, 65536, 2) == 0
    assert expert_capacity(8, 2, 65536, 2) == 8
    assert expert_capacity(7, 2, 65536, 2) == 7
    assert expert_capacity(1, 1, 65536, 4) == 1
    assert expert_capacity(3, 2, 49152, 4) == 2
    with pytest.raises(MeshIrError) as err:
        expert_capacity((1 << 63), 2, 65536, 2)
    assert err.value.code == "E_CAPACITY_PLAN"
    for bad in ((8, 0, 65536, 2), (8, 3, 65536, 2), (8, 2, 0, 2),
                (8, 2, 65536, 0)):
        with pytest.raises(MeshIrError):
            expert_capacity(*bad)


def test_gate5_layer_view_bounds_follow_the_frozen_formula(context):
    _, program, _ = context
    layer = program.moe_layer_specs[0]
    drop = layer_view_bounds(layer, TOTAL_TOKENS)
    assert drop.max_view_records == 272
    assert drop.max_view_refs == 496
    padded_layer = copy.copy(layer)
    padded_layer.overflow_policy = A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY
    padded = layer_view_bounds(padded_layer, TOTAL_TOKENS)
    assert padded.max_view_records == 272 + 2 * 16
    assert padded.max_view_refs == 496 + 4 * 16
    assert padded.max_view_records - drop.max_view_records == 32
    assert padded.max_view_refs - drop.max_view_refs == 64


def test_gate5_instance_bounds_sum_every_active_region(context):
    _, program, _ = context
    entries, refs = instance_view_requirements(program, TOTAL_TOKENS)
    layer = program.moe_layer_specs[0]
    bounds = layer_view_bounds(layer, TOTAL_TOKENS)
    assert entries == bounds.max_view_records * layer.dynamic_region_count
    assert refs == bounds.max_view_refs * layer.dynamic_region_count


def test_gate5_capacity_requirements_match_arch_and_registry(context):
    arch, program, registry = context
    requirements = moe_capacity_requirements(program, arch, registry,
                                             TOTAL_TOKENS)
    cache = arch.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
    assert requirements.weight_cache_slots == \
        cache.bytes // arch.sram_weight_cache_slot_bytes
    assert requirements.weight_cache_slots == cache.metadata_entries
    assert requirements.weight_cache_slot_bytes == 4096
    assert requirements.weight_fill_failure_table_entries_per_core == \
        len(program.moe_expert_specs)
    assert requirements.view_entries_per_core == 272
    assert requirements.view_ref_entries_per_core == 496


def _configured(requirements) -> dict:
    return {
        "view_entries_per_core": requirements.view_entries_per_core,
        "view_ref_entries_per_core": requirements.view_ref_entries_per_core,
        "weight_cache_slots": requirements.weight_cache_slots,
        "weight_cache_slot_bytes": requirements.weight_cache_slot_bytes,
        "weight_fill_failure_table_entries_per_core":
            requirements.weight_fill_failure_table_entries_per_core,
    }


def test_gate5_capacity_exact_configuration_is_accepted(context):
    arch, program, registry = context
    requirements = moe_capacity_requirements(program, arch, registry,
                                             TOTAL_TOKENS)
    configured = _configured(requirements)
    validate_moe_capacity(requirements, configured)
    assert configured == _configured(requirements)


def test_gate5_capacity_minus_one_has_no_side_effect(context):
    arch, program, registry = context
    requirements = moe_capacity_requirements(program, arch, registry,
                                             TOTAL_TOKENS)
    for field in _configured(requirements):
        configured = _configured(requirements)
        configured[field] -= 1
        snapshot = copy.deepcopy(configured)
        with pytest.raises(MeshIrError) as err:
            validate_moe_capacity(requirements, configured)
        assert err.value.code == "E_CAPACITY_PLAN", field
        assert configured == snapshot, field
        assert err.value.context["configured"] == snapshot[field]
        assert err.value.context["required"] == getattr(requirements, field)


def test_gate5_capacity_missing_field_is_rejected(context):
    arch, program, registry = context
    requirements = moe_capacity_requirements(program, arch, registry,
                                             TOTAL_TOKENS)
    for field in _configured(requirements):
        configured = _configured(requirements)
        del configured[field]
        with pytest.raises(MeshIrError) as err:
            validate_moe_capacity(requirements, configured)
        assert err.value.code == "E_CAPACITY_PLAN"


def test_gate5_capacity_needs_a_partitioned_architecture(context):
    _, program, registry = context
    legacy = load_arch(LEGACY_ARCH)
    with pytest.raises(MeshIrError) as err:
        moe_capacity_requirements(program, legacy, registry, TOTAL_TOKENS)
    assert err.value.code == "E_CAPACITY_PLAN"

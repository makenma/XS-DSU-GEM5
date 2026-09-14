"""Gate 5 MoE capacity derivation (contract 7.2/7.7/7.8, spec 5.3).

Every Gate 5 bound is derived here from the loaded architecture, program
and weight registry; runtime tables and the capacity plan must consume
these numbers instead of sizing themselves.  All arithmetic is checked:
overflow is a plan error, never a saturated value.
"""

from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.weight_registry import weight_tag_manifest

Q16 = 65536


def _checked_mul(left: int, right: int, what: str) -> int:
    if left < 0 or right < 0:
        raise MeshIrError("E_CAPACITY_PLAN", f"{what} must be non-negative")
    product = left * right
    if product > (1 << 64) - 1:
        raise MeshIrError("E_CAPACITY_PLAN", f"{what} overflows u64")
    return product


def expert_capacity(total_valid_tokens: int, top_k: int,
                    capacity_factor_q16: int, expert_count: int) -> int:
    if expert_count <= 0 or not 1 <= top_k <= expert_count or \
            capacity_factor_q16 <= 0 or total_valid_tokens < 0:
        raise MeshIrError("E_CAPACITY_PLAN", "invalid MoE capacity inputs")
    if total_valid_tokens == 0:
        return 0
    numerator = _checked_mul(total_valid_tokens, top_k, "capacity numerator")
    numerator = _checked_mul(numerator, capacity_factor_q16,
                             "capacity numerator")
    denominator = _checked_mul(expert_count, Q16, "capacity denominator")
    return -(-numerator // denominator)


@dataclass(frozen=True)
class ViewBounds:
    max_view_records: int
    max_view_refs: int


def layer_view_bounds(layer, total_valid_tokens: int) -> ViewBounds:
    routes = layer.max_routes
    tokens = layer.max_tokens_per_frozen_batch
    commands = layer.max_materialized_commands
    descriptors = layer.max_materialized_descriptors
    allocations = layer.max_dynamic_allocations
    transfers = layer.max_materialized_transfers
    padding = 0
    if layer.overflow_policy == A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY:
        capacity = expert_capacity(total_valid_tokens, layer.top_k,
                                   layer.capacity_factor_q16,
                                   layer.expert_count)
        padding = _checked_mul(layer.expert_count, capacity, "padding slots")
    records = (4 * routes + 2 * padding + 3 * tokens + 3 * commands +
               2 * descriptors + allocations + 2 * transfers)
    refs = (8 * routes + 4 * padding + 4 * tokens + 6 * commands +
            4 * descriptors + 2 * transfers)
    return ViewBounds(max_view_records=records, max_view_refs=refs)


@dataclass(frozen=True)
class MoeCapacityRequirements:
    view_entries_per_core: int
    view_ref_entries_per_core: int
    weight_cache_slots: int
    weight_cache_slot_bytes: int
    weight_fill_failure_table_entries_per_core: int
    weight_tag_count_per_core: int


def instance_view_requirements(program, total_valid_tokens: int) -> tuple:
    entries = 0
    refs = 0
    for layer in program.moe_layer_specs:
        bounds = layer_view_bounds(layer, total_valid_tokens)
        regions = layer.dynamic_region_count
        entries += bounds.max_view_records * regions
        refs += bounds.max_view_refs * regions
    return entries, refs


def moe_capacity_requirements(program, arch, registry,
                              total_valid_tokens: int) -> \
        MoeCapacityRequirements:
    cache = arch.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
    if cache is None or arch.sram_weight_cache_slot_bytes == 0:
        raise MeshIrError("E_CAPACITY_PLAN",
                          "Gate 5 needs a WEIGHT_CACHE partition")
    slots = cache.bytes // arch.sram_weight_cache_slot_bytes
    tags = weight_tag_manifest(program, registry)
    entries, refs = instance_view_requirements(program, total_valid_tokens)
    return MoeCapacityRequirements(
        view_entries_per_core=entries,
        view_ref_entries_per_core=refs,
        weight_cache_slots=slots,
        weight_cache_slot_bytes=arch.sram_weight_cache_slot_bytes,
        weight_fill_failure_table_entries_per_core=len(tags),
        weight_tag_count_per_core=len(tags),
    )


def validate_moe_capacity(requirements: MoeCapacityRequirements,
                          configured: dict) -> None:
    fields = (
        "view_entries_per_core",
        "view_ref_entries_per_core",
        "weight_cache_slots",
        "weight_cache_slot_bytes",
        "weight_fill_failure_table_entries_per_core",
    )
    for field in fields:
        if field not in configured:
            raise MeshIrError("E_CAPACITY_PLAN",
                              f"missing configured capacity {field}")
        value = configured[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise MeshIrError("E_CAPACITY_PLAN",
                              f"configured {field} must be a non-negative int")
        required = getattr(requirements, field)
        if value < required:
            raise MeshIrError(
                "E_CAPACITY_PLAN",
                f"configured {field} below required",
                configured=value, required=required)

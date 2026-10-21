from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from mesh_ir.architecture import architecture_document, load_arch_text
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def document():
    base = yaml.safe_load((ROOT / 'configs/example/ai_mesh/arch/mesh_1x2.yaml').read_text())
    serving = yaml.safe_load((ROOT / 'configs/example/ai_mesh/arch/mesh_1x2_moe.yaml').read_text())
    for key in ('partitions', 'weight_cache_slot_bytes'):
        base['core']['sram'][key] = deepcopy(serving['core']['sram'][key])
    return base


def test_partitioned_fabric_roundtrip_retains_cache_capacity_and_router_identity(document):
    arch = load_arch_text(yaml.safe_dump(document))
    cache = arch.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
    assert cache.bytes // arch.sram_weight_cache_slot_bytes == cache.metadata_entries == 128
    assert cache.max_pinned_entries == 64
    restored = load_arch_text(yaml.safe_dump(architecture_document(arch)))
    assert restored == arch
    assert restored.digest() == arch.digest()
    assert tuple(item.core_id for item in arch.fabric.initiators) == arch.core_ids


@pytest.mark.parametrize('damage', ['overlap', 'overflow', 'misalignment', 'duplicate', 'unknown', 'zero_metadata', 'slot_not_dividing', 'slot_metadata', 'slot_unaligned', 'slot_without_partition', 'pinned_capacity'])
def test_partitioned_fabric_rejects_invalid_memory_contract(document, damage):
    sram = document['core']['sram']
    parts = sram['partitions']
    if damage == 'overlap': parts[2]['base'] = parts[1]['base']
    elif damage == 'overflow': parts[3]['bytes'] = sram['bytes']
    elif damage == 'misalignment': parts[0]['base'] = 1
    elif damage == 'duplicate': parts[3]['kind'] = 'WEIGHT_CACHE'
    elif damage == 'unknown': parts[3]['kind'] = 'ABSENT'
    elif damage == 'zero_metadata': parts[0]['metadata_entries'] = 0
    elif damage == 'slot_not_dividing': sram['weight_cache_slot_bytes'] = 0x3000
    elif damage == 'slot_metadata': parts[2]['metadata_entries'] -= 1
    elif damage == 'slot_unaligned': sram['weight_cache_slot_bytes'] = 48
    elif damage == 'slot_without_partition': parts.pop(2)
    else: parts[2]['max_pinned_entries'] = 129
    with pytest.raises(MeshIrError, match='E_ARCH_PARTITION'):
        load_arch_text(yaml.safe_dump(document))


@pytest.mark.parametrize('field,value', [('kind', True), ('alignment', 1 << 32), ('metadata_entries', 1 << 32), ('max_pinned_entries', 1 << 32)])
def test_partition_records_obey_runtime_integer_widths(document, field, value):
    from dataclasses import replace
    from mesh_ir.memory_partitions import validate_partitions

    arch = load_arch_text(yaml.safe_dump(document))
    first, *rest = arch.sram_partitions
    partitions = (replace(first, **{field: value}), *rest)
    with pytest.raises(MeshIrError, match='E_ARCH_PARTITION'):
        validate_partitions(partitions, arch.sram_bytes, arch.sram_weight_cache_slot_bytes)

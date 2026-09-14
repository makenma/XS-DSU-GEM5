import copy
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError

REPO = Path(__file__).resolve().parents[4]
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
LEGACY_ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch_document():
    return yaml.safe_load(ARCH_PATH.read_text())


def load_mutated(arch_document, tmp_path, mutate) -> None:
    document = copy.deepcopy(arch_document)
    mutate(document)
    path = tmp_path / "arch.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    load_arch(path)


def test_gate5_moe_arch_partitions_are_disjoint_and_inside_sram(arch_document):
    arch = load_arch(ARCH_PATH)
    partitions = list(arch.sram_partitions)
    assert [partition.kind for partition in partitions] == [
        A.SRAM_PARTITION_KIND.STATIC_PROGRAM,
        A.SRAM_PARTITION_KIND.RUNTIME_SCRATCH,
        A.SRAM_PARTITION_KIND.WEIGHT_CACHE,
        A.SRAM_PARTITION_KIND.KV_STAGING_CACHE,
    ]
    for partition in partitions:
        assert partition.base % partition.alignment == 0
        assert partition.base + partition.bytes <= arch.sram_bytes
        assert partition.metadata_entries > 0
    ordered = sorted(partitions, key=lambda partition: partition.base)
    for previous, current in zip(ordered, ordered[1:]):
        assert previous.base + previous.bytes <= current.base


def test_gate5_weight_cache_slots_are_equal_and_complete(arch_document):
    arch = load_arch(ARCH_PATH)
    cache = arch.partition(A.SRAM_PARTITION_KIND.WEIGHT_CACHE)
    slot_bytes = arch.sram_weight_cache_slot_bytes
    assert slot_bytes > 0
    assert slot_bytes % cache.alignment == 0
    assert cache.bytes % slot_bytes == 0
    assert cache.metadata_entries == cache.bytes // slot_bytes
    assert cache.max_pinned_entries <= cache.metadata_entries


def test_gate5_legacy_arch_without_partitions_keeps_its_digest():
    legacy = load_arch(LEGACY_ARCH)
    assert legacy.sram_partitions == ()
    assert legacy.sram_weight_cache_slot_bytes == 0
    assert "sram_partitions" not in legacy.canonical_dict()
    assert legacy.digest().hex() == (
        "76bb2d472057bd3907a9722ff397a164fae2328825c41e48bf12bc92d9bfe25c")


ARCH_VIOLATIONS = (
    ("overlapping_partitions", lambda doc: doc["core"]["sram"]["partitions"][2]
     .update({"base": 0x040000}), "E_ARCH_PARTITION"),
    ("partition_escapes_sram", lambda doc: doc["core"]["sram"]["partitions"][3]
     .update({"bytes": 0x100000}), "E_ARCH_PARTITION"),
    ("misaligned_base", lambda doc: doc["core"]["sram"]["partitions"][0]
     .update({"base": 0x40}), "E_ARCH_PARTITION"),
    ("duplicate_kind", lambda doc: doc["core"]["sram"]["partitions"][3]
     .update({"kind": "WEIGHT_CACHE"}), "E_ARCH_PARTITION"),
    ("unknown_kind", lambda doc: doc["core"]["sram"]["partitions"][3]
     .update({"kind": "TENSOR_SCRATCH"}), "E_ARCH_PARTITION"),
    ("zero_metadata", lambda doc: doc["core"]["sram"]["partitions"][0]
     .update({"metadata_entries": 0}), "E_ARCH_PARTITION"),
    ("slot_not_dividing", lambda doc: doc["core"]["sram"]
     .update({"weight_cache_slot_bytes": 0x3000}), "E_ARCH_PARTITION"),
    ("slot_metadata_mismatch", lambda doc: doc["core"]["sram"]["partitions"][2]
     .update({"metadata_entries": 127}), "E_ARCH_PARTITION"),
    ("slot_unaligned", lambda doc: doc["core"]["sram"]
     .update({"weight_cache_slot_bytes": 48}), "E_ARCH_PARTITION"),
    ("slot_without_partition", lambda doc: doc["core"]["sram"].update(
        {"partitions": [entry for entry in doc["core"]["sram"]["partitions"]
                        if entry["kind"] != "WEIGHT_CACHE"]}),
     "E_ARCH_PARTITION"),
)


@pytest.mark.parametrize(
    "mutate,code", [case[1:] for case in ARCH_VIOLATIONS],
    ids=[case[0] for case in ARCH_VIOLATIONS])
def test_gate5_arch_partition_violations_are_rejected(arch_document, tmp_path,
                                                      mutate, code):
    with pytest.raises(MeshIrError) as err:
        load_mutated(arch_document, tmp_path, mutate)
    assert err.value.code == code

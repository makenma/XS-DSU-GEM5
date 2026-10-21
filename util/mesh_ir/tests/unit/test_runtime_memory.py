import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.runtime_memory import MemoryAddressMap, MemoryRange


def test_seed_and_verify_spans_are_partitioned_without_losing_payload_metadata():
    mapping = MemoryAddressMap((MemoryRange(7, 0x2000, 0x3000), MemoryRange(5, 0x1000, 0x2000)))
    assert mapping.partition([(0x1ff0, 0x30, 0xa5)]) == {5: [(0x1ff0, 0x10, 0xa5)], 7: [(0x2000, 0x20, 0xa5)]}
    assert mapping.partition([(0x1ff0, 0x30)]) == {5: [(0x1ff0, 0x10)], 7: [(0x2000, 0x20)]}


@pytest.mark.parametrize('address,size', [(0x10ff, 0x102), (0xfff, 2), (0x12ff, 2), (0x1000, 0), ((1 << 64)-1, 2)])
def test_partition_rejects_unmapped_or_unrepresentable_bytes(address, size):
    mapping = MemoryAddressMap((MemoryRange(5, 0x1000, 0x1100), MemoryRange(7, 0x1200, 0x1300)))
    with pytest.raises(MeshIrError):
        mapping.partition([(address, size)])


def test_overlapping_physical_owners_are_rejected():
    with pytest.raises(MeshIrError):
        MemoryAddressMap((MemoryRange(5, 0x1000, 0x1200), MemoryRange(7, 0x1100, 0x1300)))


def test_file_image_oracle_reads_adjacent_sources_and_rejects_gaps(tmp_path):
    import hashlib
    import json
    from mesh_ir.runtime_memory import MemoryFileImage

    (tmp_path / 'payload.bin').write_bytes(b'prefixabcdefghtrailer')
    document = dict(schema='mesh-memory-file-image-v1', program_semantic_sha256='1' * 64,
        architecture_sha256='2' * 64, spans=[
            dict(address=0x1000 + index * 4, path='payload.bin', offset=6 + index * 4,
                 size=4, sha256=hashlib.sha256(data).hexdigest())
            for index, data in enumerate((b'abcd', b'efgh'))])
    path = tmp_path / 'image.json'
    path.write_text(json.dumps(document))
    image = MemoryFileImage.load(path, '1' * 64, '2' * 64)
    assert image.read(0x1002, 5) == b'cdefg'
    assert image.read(0x1000, 8) == b'abcdefgh'
    assert image.read(0xfff, 2) is None
    assert image.read(0x1007, 2) is None
    with pytest.raises(MeshIrError):
        MemoryFileImage.load(path, '3' * 64, '2' * 64)
    for changed in (dict(document['spans'][1], address=0x1003),
                    dict(document['spans'][1], sha256='0' * 64),
                    dict(document['spans'][1], offset=19)):
        path.write_text(json.dumps(dict(document, spans=[document['spans'][0], changed])))
        with pytest.raises(MeshIrError):
            MemoryFileImage.load(path, '1' * 64, '2' * 64)

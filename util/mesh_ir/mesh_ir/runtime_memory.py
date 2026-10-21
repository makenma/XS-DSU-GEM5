from dataclasses import dataclass
from bisect import bisect_right
import hashlib
from pathlib import Path

from mesh_ir.canonical import U64_MAX, checked_add_u64, strict_json_loads
from mesh_ir.schema import validate_schema
from mesh_ir.diagnostics import MeshIrError


@dataclass(frozen=True)
class MemoryRange:
    node: int
    begin: int
    end: int


@dataclass(frozen=True)
class MemoryAddressMap:
    ranges: tuple[MemoryRange, ...]

    def __post_init__(self):
        if type(self.ranges) is not tuple or any(type(row) is not MemoryRange for row in self.ranges):
            raise MeshIrError('E_CONFIG', 'physical memory map requires typed ranges')
        for row in self.ranges:
            if any(type(value) is not int for value in (row.node, row.begin, row.end)) or not (
                    0 <= row.node <= 0xffffffff and 0 <= row.begin < row.end <= U64_MAX):
                raise MeshIrError('E_CONFIG', 'physical memory range is not representable')
        ordered = tuple(sorted(self.ranges, key=lambda row: (row.begin, row.end, row.node)))
        if any(right.begin < left.end for left, right in zip(ordered, ordered[1:])):
            raise MeshIrError('E_CONFIG', 'physical memory owners overlap')
        object.__setattr__(self, 'ranges', ordered)

    def partition(self, rows):
        result = {row.node: [] for row in self.ranges}
        for address, size, *payload in rows:
            if type(address) is not int or type(size) is not int or not (
                    0 <= address <= U64_MAX and 0 < size <= U64_MAX - address):
                raise MeshIrError('E_CONFIG', 'physical memory span is not representable')
            end = address + size
            for region in self.ranges:
                if region.end <= address:
                    continue
                if region.begin > address:
                    break
                next_address = min(end, region.end)
                result[region.node].append((address, next_address - address, *payload))
                address = next_address
                if address == end:
                    break
            if address != end:
                raise MeshIrError('E_CONFIG', 'physical memory span crosses unmapped bytes')
        return result


@dataclass(frozen=True)
class MemoryFileRange:
    address: int
    path: Path
    offset: int
    size: int


@dataclass(frozen=True)
class MemoryFileImage:
    spans: tuple[MemoryFileRange, ...]
    addresses: tuple[int, ...]

    @classmethod
    def load(cls, path, program_sha256, architecture_sha256):
        path = Path(path)
        document = strict_json_loads(path.read_bytes())
        validate_schema('mesh_memory_file_image_v1.schema.json', document, 'memory file image')
        if document['program_semantic_sha256'] != program_sha256 or document['architecture_sha256'] != architecture_sha256:
            raise MeshIrError('E_MEMORY_IMAGE', 'memory file image identity differs')
        spans = []
        for row in document['spans']:
            checked_add_u64(row['address'], row['size'], 'memory file image range')
            if row['offset'] + row['size'] > (1 << 63) - 1 or '\0' in row['path']:
                raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source range is invalid')
            source = (path.parent / row['path']).resolve()
            digest = hashlib.sha256()
            remaining = row['size']
            try:
                with source.open('rb') as stream:
                    stream.seek(row['offset'])
                    while remaining:
                        block = stream.read(min(65536, remaining))
                        if not block:
                            raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source is truncated')
                        digest.update(block)
                        remaining -= len(block)
            except OSError as error:
                raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source cannot be read', path=str(source)) from error
            if digest.hexdigest() != row['sha256']:
                raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source digest differs', path=str(source))
            spans.append(MemoryFileRange(row['address'], source, row['offset'], row['size']))
        spans.sort(key=lambda row: row.address)
        if any(left.address + left.size > right.address for left, right in zip(spans, spans[1:])):
            raise MeshIrError('E_MEMORY_IMAGE', 'memory file image destinations overlap')
        return cls(tuple(spans), tuple(row.address for row in spans))

    def read(self, address: int, size: int) -> bytes | None:
        end = checked_add_u64(address, size, 'memory file image read')
        index = bisect_right(self.addresses, address) - 1
        if index < 0:
            return None
        result = bytearray()
        while address < end:
            if index >= len(self.spans):
                return None
            row = self.spans[index]
            if not row.address <= address < row.address + row.size:
                return None
            length = min(end - address, row.address + row.size - address)
            try:
                with row.path.open('rb') as stream:
                    stream.seek(row.offset + address - row.address)
                    data = stream.read(length)
            except OSError as error:
                raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source cannot be read', path=str(row.path)) from error
            if len(data) != length:
                raise MeshIrError('E_MEMORY_IMAGE', 'memory file image source is truncated')
            result.extend(data)
            address += length
            index += 1
        return bytes(result)

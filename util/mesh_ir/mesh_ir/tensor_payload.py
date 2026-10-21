from dataclasses import dataclass
from enum import Enum
import hashlib
import math
from pathlib import Path

from mesh_ir.canonical import checked_add_u64, strict_json_loads
from mesh_ir.compat import INPUT_ROLES
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType
from mesh_ir.runtime_memory import MemoryAddressMap, MemoryRange
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.addressing import bind_invocation
from mesh_ir.scheduled.model import BoundInvocation
from mesh_ir.traffic import Binding
from mesh_ir.schema import validate_schema


class PayloadKind(str, Enum):
    PARAMETER = 'PARAMETER'
    BUFFER = 'BUFFER'
    CONSTANT_TENSOR = 'CONSTANT_TENSOR'
    USER_INPUT = 'USER_INPUT'


@dataclass(frozen=True)
class TensorFileRecord:
    name: str
    kind: PayloadKind
    dtype: DType
    shape: tuple[int, ...]
    offset: int
    size: int
    sha256: str
    input_data: bytes | None


@dataclass(frozen=True)
class PackedTensorInput:
    slot_id: int
    symbol: str
    name: str
    dtype: DType
    shape: tuple[int, ...]
    offset: int
    size: int
    sha256: str


@dataclass(frozen=True)
class BoundTensorPayload:
    memory_image: dict
    inputs: tuple[PackedTensorInput, ...]
    input_bytes: bytes
    owner_bytes: tuple[tuple[int, int], ...]
    source_manifest_sha256: str
    invocation: BoundInvocation


@dataclass(frozen=True)
class TensorPayload:
    path: Path
    manifest_sha256: str
    export_sha256: str
    export_semantic_sha256: str
    records: tuple[TensorFileRecord, ...]

    @classmethod
    def load(cls, path: Path):
        path = Path(path)
        raw = path.read_bytes()
        document = strict_json_loads(raw)
        validate_schema('mesh_tensor_payload_v1.schema.json', document, 'tensor payload')
        source = (path.parent / document['payload_file']).resolve()
        records = []
        names = set()
        cursor = 0
        stream_hash = hashlib.sha256()
        try:
            with source.open('rb') as stream:
                for row in document['tensors']:
                    dtype = DType[row['dtype']]
                    shape = tuple(row['shape'])
                    kind = PayloadKind(row['kind'])
                    if row['name'] in names or row['offset'] != cursor or row['size'] != math.prod(shape) * dtype.byte_width:
                        raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload identity or dense storage extent is invalid', name=row['name'])
                    names.add(row['name'])
                    digest = hashlib.sha256()
                    remaining = row['size']
                    data = bytearray() if kind is PayloadKind.USER_INPUT else None
                    while remaining:
                        block = stream.read(min(65536, remaining))
                        if not block:
                            raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload is truncated', name=row['name'])
                        digest.update(block)
                        stream_hash.update(block)
                        if data is not None:
                            data.extend(block)
                        remaining -= len(block)
                    if digest.hexdigest() != row['sha256']:
                        raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload content digest differs', name=row['name'])
                    cursor = checked_add_u64(cursor, row['size'], 'tensor payload extent')
                    records.append(TensorFileRecord(row['name'], kind, dtype, shape, row['offset'], row['size'],
                        row['sha256'], None if data is None else bytes(data)))
                if stream.read(1) or stream_hash.hexdigest() != document['payload_sha256']:
                    raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload stream identity differs')
        except OSError as error:
            raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload file cannot be read', path=str(source)) from error
        return cls(source, hashlib.sha256(raw).hexdigest(), document['export_sha256'],
                   document['export_semantic_sha256'], tuple(records))

    def bind(self, program, arch, *, entrypoint_id: int, profile_id: int,
             bindings: tuple[Binding, ...] | None = None) -> BoundTensorPayload:
        if program.arch_digest != arch.digest():
            raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload binding architecture differs from Program')
        catalog = ProgramBindingCatalog(program).for_variant(entrypoint_id, profile_id)
        invocation = bind_invocation(program, arch, entrypoint_id, profile_id,
            tuple(row.slot.reference_binding for row in catalog.values()) if bindings is None else bindings)
        by_slot = {row.slot_id: row for row in invocation.bindings}
        required = {}
        for binding in catalog.values():
            if binding.tensor.role not in INPUT_ROLES.values():
                continue
            if binding.tensor.name in required:
                raise MeshIrError('E_MEMORY_IMAGE', 'source tensor binding is ambiguous', name=binding.tensor.name)
            required[binding.tensor.name] = binding
        if set(required) != {row.name for row in self.records}:
            raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload does not exactly cover Program sources',
                missing=sorted(set(required) - {row.name for row in self.records}),
                extra=sorted({row.name for row in self.records} - set(required)))
        memory = MemoryAddressMap(tuple(MemoryRange(target.dst_node,
            arch.regions[region.region_id].base + region.offset_bytes,
            arch.regions[region.region_id].base + region.offset_bytes + region.size_bytes)
            for target in arch.fabric.targets for region in target.ranges
            if arch.regions[region.region_id].kind == 'HBM'))
        spans = []
        inputs = []
        packed = bytearray()
        owners = {}
        for row in self.records:
            binding = required[row.name]
            tensor = binding.tensor
            expected_role = INPUT_ROLES[row.kind]
            if (tensor.role is not expected_role or tensor.dtype is not row.dtype or tensor.shape != row.shape or
                    tensor.storage_offset_elements != 0 or tensor.storage_extent_bytes != row.size or
                    tensor.strides != tuple(math.prod(row.shape[index + 1:]) for index in range(len(row.shape))) or
                    binding.object.footprint_bytes != row.size):
                raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload metadata differs from Program storage', name=row.name)
            if row.kind is PayloadKind.USER_INPUT:
                alignment = max(arch.axi_data_bytes, binding.slot.required_allocation_alignment_bytes)
                offset = (len(packed) + alignment - 1) // alignment * alignment
                packed.extend(bytes(offset - len(packed)))
                packed.extend(row.input_data)
                packed.extend(bytes(binding.slot.required_allocation_bytes - row.size))
                inputs.append(PackedTensorInput(binding.slot.slot_id, binding.slot.symbol, row.name,
                    row.dtype, row.shape, offset, row.size, row.sha256))
                continue
            if not binding.is_immutable_model_content or tensor.content_sha256 != row.sha256:
                raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload is not the admitted immutable HBM content', name=row.name)
            reference = by_slot[binding.slot.slot_id]
            address = checked_add_u64(arch.regions[reference.region_id].base,
                reference.allocation_offset_bytes, 'tensor payload binding address')
            if row.size > reference.allocation_size_bytes:
                raise MeshIrError('E_MEMORY_IMAGE', 'tensor payload exceeds its binding allocation', name=row.name)
            for node, pieces in memory.partition(((address, row.size),)).items():
                owners[node] = owners.get(node, 0) + sum(piece[1] for piece in pieces)
            spans.append(dict(address=address, path=str(self.path), offset=row.offset, size=row.size, sha256=row.sha256))
        spans.sort(key=lambda row: row['address'])
        if any(left['address'] + left['size'] > right['address'] for left, right in zip(spans, spans[1:])):
            raise MeshIrError('E_MEMORY_IMAGE', 'immutable tensor bindings overlap')
        return BoundTensorPayload(dict(schema='mesh-memory-file-image-v1', program_semantic_sha256=program.semantic_sha256,
            architecture_sha256=arch.digest().hex(), spans=spans), tuple(inputs), bytes(packed),
            tuple(sorted(owners.items())), self.manifest_sha256, invocation)

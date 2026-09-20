"""Scheduled Mesh IR data model, canonical JSON and stable error codes.

Record dataclasses mirror util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml exactly;
the correspondence is asserted at import time against the generated ABI so
the schema cannot drift without a loud failure.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mesh_ir.architecture import ArchManifest, ArchRegion
from mesh_ir.canonical import canonical_json_bytes, to_canonical
from mesh_ir.diagnostics import ERROR_CODES, MeshIrError
from mesh_ir.generated import abi as A

if TYPE_CHECKING:
    from mesh_ir.scheduled.model import ProgramSemantics


@dataclass(frozen=True)
class StringEntry:
    value: str


@dataclass(frozen=True)
class Entrypoint:
    entrypoint_id: int
    name_sid: int
    profile_begin: int
    profile_count: int
    lifecycle_core_id: int
    lifecycle_stream_id: int
    flags: int = 0
    reserved: int = 0


@dataclass(frozen=True)
class Profile:
    profile_id: int
    entrypoint_id: int
    name_sid: int
    rank: int
    reserved: int = 0
    dims: tuple = (0,) * 8


@dataclass(frozen=True)
class Tensor:
    tensor_id: int
    name_sid: int
    role: int
    dtype: int
    storage_class: int
    access: int
    rank: int
    layout: int
    layout_attr: int = 0
    placement_id: int = 0
    sharding_id: int = 0
    flags: int = 0
    reserved: int = 0
    dims: tuple = (0,) * 8
    content_sha256: bytes = bytes(32)


@dataclass(frozen=True)
class Shard:
    shard_id: int
    tensor_id: int
    sharding_id: int
    owner_core: int
    rank: int
    reserved: int = 0
    flags: int = 0
    reserved0: int = 0
    global_origin: tuple = (0,) * 8
    local_shape: tuple = (0,) * 8
    valid_shape: tuple = (0,) * 8
    allocation_id: int = 0
    reserved2: int = 0
    allocation_offset: int = 0
    span_bytes: int = 0


@dataclass(frozen=True)
class Allocation:
    allocation_id: int
    owner_core: int
    memory_space: int
    offset_bytes: int
    size_bytes: int
    alignment_bytes: int
    flags: int = 0


@dataclass(frozen=True)
class Stream:
    core_id: int
    stream_id: int
    command_begin: int
    command_count: int
    flags: int = 0
    reserved: int = 0


@dataclass(frozen=True)
class Command:
    command_id: int
    source_op_id: int
    core_id: int
    stream_id: int
    engine: int
    opcode: int
    wait_begin: int = 0
    wait_count: int = 0
    operand_count: int = 0
    operand_begin: int = 0
    signal_event: int = 0
    attr_index: int = 0
    debug_loc_id: int = 0


@dataclass(frozen=True)
class CommandWait:
    event_id: int


@dataclass(frozen=True)
class CommandOperand:
    tensor_id: int
    shard_id: int
    allocation_id: int
    access: int
    reserved: int = 0


@dataclass(frozen=True)
class Event:
    event_id: int
    kind: int
    reserved: int = 0
    producer_command_id: int = 0
    expected_arrivals: int = 0
    reserved2: int = 0


@dataclass(frozen=True)
class DmaEndpoint:
    memory_space: int
    region_id: int
    owner_core: int
    tensor_id: int
    shard_id: int
    reserved: int = 0
    offset_bytes: int = 0


@dataclass(frozen=True)
class DmaDescriptor:
    descriptor_id: int
    command_id: int
    transfer_id: int
    owner_core: int
    kind: int
    src: DmaEndpoint
    dst: DmaEndpoint
    rows: int
    row_bytes: int
    src_stride_bytes: int
    dst_stride_bytes: int
    useful_bytes: int
    physical_storage_bytes: int
    axi_id: int
    qos: int
    reserved: int
    max_burst_beats: int
    reserved2: int
    completion_event: int


@dataclass(frozen=True)
class OpAttr:
    kind: int
    reserved: int = 0
    payload: tuple = ()
    payload_fields: tuple = ()

    def payload_dict(self) -> dict:
        return dict(zip(self.payload_fields, self.payload))


@dataclass(frozen=True)
class Relocation:
    relocation_id: int
    symbol_sid: int
    kind: int
    region_id: int
    tensor_id: int
    reserved: int
    offset_bytes: int
    reserved2: int


@dataclass(frozen=True)
class ExpectedTrafficRow:
    entrypoint_id: int
    profile_id: int
    command_id: int
    descriptor_id: int
    kind: int
    reserved: int
    useful_bytes: int
    physical_beat_bytes: int
    segments: int
    bursts: int
    ar_count: int
    r_beats: int
    aw_count: int
    w_beats: int
    b_count: int
    min_flits: int = 0
    reserved2: int = 0


@dataclass(frozen=True)
class SourceMap:
    loc_id: int
    file: str
    line: int
    column: int


@dataclass(frozen=True)
class ProfileHint:
    entrypoint_id: int
    profile_id: int
    name: str
    value: str


@dataclass(frozen=True)
class ContentDigest:
    object_kind: int
    reserved: int
    object_id: int
    digest: bytes


@dataclass(frozen=True)
class Program:
    abi_major: int
    abi_minor: int
    arch_digest: bytes
    strings: tuple[StringEntry, ...]
    entrypoints: tuple[Entrypoint, ...]
    profiles: tuple[Profile, ...]
    tensors: tuple[Tensor, ...]
    shards: tuple[Shard, ...]
    allocations: tuple[Allocation, ...]
    streams: tuple[Stream, ...]
    commands: tuple[Command, ...]
    command_waits: tuple[CommandWait, ...]
    command_operands: tuple[CommandOperand, ...]
    events: tuple[Event, ...]
    dma_descriptors: tuple[DmaDescriptor, ...]
    op_attrs: tuple[OpAttr, ...]
    relocations: tuple[Relocation, ...]
    expected_traffic: tuple[ExpectedTrafficRow, ...]
    semantics: "ProgramSemantics"
    semantic_sha256: str
    min_reader_minor: int = A.MIN_READER_MINOR
    required_features: int = A.REQUIRED_FEATURES
    source_map: tuple[SourceMap, ...] = ()
    profile_hints: tuple[ProfileHint, ...] = ()
    content_digests: tuple[ContentDigest, ...] = ()

    def semantic_dict(self) -> dict:
        return self._projection(True)

    def canonical_dict(self) -> dict:
        return self._projection(False)

    def _projection(self, semantic: bool) -> dict:
        from mesh_ir.abi.semantic import semantic_to_canonical

        values = {
            "abi": {
                name: to_canonical(getattr(self, binding["program_field"]))
                for name, binding in A.CANONICAL_ABI_FIELDS.items()
            },
            "arch_digest": self.arch_digest.hex(),
            "sections": self._sections(semantic),
            "semantics": semantic_to_canonical(self.semantics),
            "semantic_sha256": self.semantic_sha256,
        }
        return {
            field["name"]: values[field["name"]]
            for field in A.PROGRAM_CANONICAL_FIELDS
            if not semantic or field["semantic"]
        }

    def _sections(self, semantic: bool) -> dict:
        sections = {}
        for name, binding in A.TRANSPORT_CANONICAL_SECTIONS.items():
            if semantic and not binding["semantic"]:
                continue
            records = getattr(self, binding["program_field"])
            if binding["optional"] and not records:
                continue
            if binding["projection"] == "strings":
                sections[name] = [item.value for item in records]
            elif binding["projection"] == "attr_payloads":
                sections[name] = [self._attr_dict(item) for item in records]
            else:
                fields = A.TRANSPORT_CANONICAL_FIELDS[name]
                sections[name] = [{
                    field["name"]: _plain(getattr(item, field["name"]))
                    for field in fields
                    if not semantic or field["semantic"]
                } for item in records]
        return sections

    @staticmethod
    def _attr_dict(attr: OpAttr) -> dict:
        return {"kind": attr.kind, **{k: _plain(v) for k, v in attr.payload_dict().items()}}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())


def _plain(value):
    if hasattr(value, "payload_dict"):
        return {key: to_canonical(item) for key, item in value.payload_dict().items()}
    return to_canonical(value)


RECORD_CLASSES = {
    "ENTRYPOINTS": Entrypoint,
    "PROFILES": Profile,
    "TENSORS": Tensor,
    "SHARDS": Shard,
    "ALLOCATIONS": Allocation,
    "STREAMS": Stream,
    "COMMANDS": Command,
    "COMMAND_WAITS": CommandWait,
    "COMMAND_OPERANDS": CommandOperand,
    "EVENTS": Event,
    "DMA_DESCRIPTORS": DmaDescriptor,
    "RELOCATIONS": Relocation,
    "EXPECTED_TRAFFIC": ExpectedTrafficRow,
    "SOURCE_MAP": SourceMap,
    "PROFILE_HINTS": ProfileHint,
    "CONTENT_DIGESTS": ContentDigest,
}


def _assert_model_matches_schema() -> None:
    for name, cls in RECORD_CLASSES.items():
        schema_names = tuple(f.get("python_field", f["name"]) for f in getattr(A, f"{name}_FIELDS"))
        model_names = tuple(f.name for f in dataclasses.fields(cls))
        assert schema_names == model_names, (
            f"{name} model fields {model_names} != schema fields {schema_names}; "
            "regenerate abi.py and update model.py"
        )


_assert_model_matches_schema()

from __future__ import annotations

from pathlib import PurePosixPath

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import ContentDigest, ProfileHint, Program, SourceMap


def _ordered_unique(values: tuple, key, section: str) -> None:
    keys = tuple(key(value) for value in values)
    if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
        raise MeshIrError("E_ABI_ORDER", "optional section keys must be sorted and unique", section=section)


def validate_optional_sections(program: Program) -> None:
    for binding in A.TRANSPORT_CANONICAL_SECTIONS.values():
        if not binding["optional"]:
            continue
        name = binding["program_field"]
        if type(getattr(program, name)) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "optional Program table must be an immutable tuple", section=name)
    if any(type(item) is not SourceMap for item in program.source_map):
        raise MeshIrError("E_ABI_BOUNDS", "SOURCE_MAP contains a wrong record type")
    if any(type(item) is not ProfileHint for item in program.profile_hints):
        raise MeshIrError("E_ABI_BOUNDS", "PROFILE_HINTS contains a wrong record type")
    if any(type(item) is not ContentDigest for item in program.content_digests):
        raise MeshIrError("E_ABI_BOUNDS", "CONTENT_DIGESTS contains a wrong record type")
    _ordered_unique(program.source_map, lambda item: item.loc_id, "SOURCE_MAP")
    _ordered_unique(program.profile_hints, lambda item: (item.entrypoint_id, item.profile_id, item.name), "PROFILE_HINTS")
    _ordered_unique(program.content_digests, lambda item: (item.object_kind, item.object_id), "CONTENT_DIGESTS")

    source_ids = {item.loc_id for item in program.source_map}
    for item in program.source_map:
        if type(item.file) is not str:
            raise MeshIrError("E_ABI_BOUNDS", "source filename must be a string")
        path = PurePosixPath(item.file)
        if not item.file or path.is_absolute() or ".." in path.parts or path.as_posix() != item.file:
            raise MeshIrError("E_ABI_BOUNDS", "source filename must be a contained relative path", file=item.file)
    for command in program.commands:
        if command.debug_loc_id and command.debug_loc_id not in source_ids:
            raise MeshIrError("E_ABI_BOUNDS", "command debug location is unknown", loc_id=command.debug_loc_id)

    profiles = {(item.entrypoint_id, item.profile_id) for item in program.profiles}
    for item in program.profile_hints:
        if (item.entrypoint_id, item.profile_id) not in profiles:
            raise MeshIrError("E_ABI_BOUNDS", "profile hint references an unknown profile")

    targets = {
        A.CONTENT_DIGEST_OBJECT_KIND["TENSOR"]: {item.tensor_id for item in program.tensors},
        A.CONTENT_DIGEST_OBJECT_KIND["SHARD"]: {item.shard_id for item in program.shards},
        A.CONTENT_DIGEST_OBJECT_KIND["ALLOCATION"]: {item.allocation_id for item in program.allocations},
        A.CONTENT_DIGEST_OBJECT_KIND["KERNEL_OBJECT"]: {item.object_id for item in program.semantics.objects},
    }
    tensors = {item.tensor_id: item for item in program.tensors}
    for item in program.content_digests:
        if item.object_kind not in targets:
            raise MeshIrError("E_ABI_ENUM", "content digest object kind is unknown", kind=item.object_kind)
        if item.object_id not in targets[item.object_kind]:
            raise MeshIrError("E_ABI_BOUNDS", "content digest references an unknown object", object_id=item.object_id)
        if type(item.digest) is not bytes or len(item.digest) != 32:
            raise MeshIrError("E_ABI_BOUNDS", "content digest must contain 32 bytes")
        if item.object_kind == A.CONTENT_DIGEST_OBJECT_KIND["TENSOR"]:
            tensor = tensors[item.object_id]
            if tensor.flags & A.TENSOR_FLAGS.HAS_CONTENT_SHA256 and tensor.content_sha256 != item.digest:
                raise MeshIrError("E_ABI_CHECKSUM", "content digest contradicts tensor content hash", tensor_id=item.object_id)


__all__ = ["validate_optional_sections"]

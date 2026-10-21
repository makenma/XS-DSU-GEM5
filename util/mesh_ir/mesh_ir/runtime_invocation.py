from pathlib import Path

from mesh_ir.canonical import strict_json_loads, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access
from mesh_ir.scheduled.addressing import bind_invocation
from mesh_ir.scheduled.model import BoundInvocation
from mesh_ir.schema import validate_schema
from mesh_ir.traffic import Binding


def invocation_document(invocation: BoundInvocation) -> dict:
    return dict(schema='mesh-invocation-v1',
        program_semantic_sha256=invocation.program_semantic_sha256,
        architecture_sha256=invocation.arch_digest.hex(),
        entrypoint_id=invocation.entrypoint_id, profile_id=invocation.profile_id,
        bindings=to_canonical(invocation.bindings),
        bound_identity_sha256=invocation.bound_identity_sha256)


def load_invocation(path: Path, program, arch) -> BoundInvocation:
    document = strict_json_loads(Path(path).read_bytes())
    validate_schema('mesh_invocation_v1.schema.json', document, 'runtime invocation')
    if (document['program_semantic_sha256'] != program.semantic_sha256 or
            document['architecture_sha256'] != arch.digest().hex()):
        raise MeshIrError('E_RELOCATION', 'runtime invocation input identity differs')
    try:
        bindings = tuple(Binding(**dict(row, access=Access(row['access']))) for row in document['bindings'])
    except ValueError as error:
        raise MeshIrError('E_RELOCATION', 'runtime invocation access kind is invalid') from error
    invocation = bind_invocation(program, arch, document['entrypoint_id'], document['profile_id'], bindings)
    if invocation.bound_identity_sha256 != document['bound_identity_sha256']:
        raise MeshIrError('E_RELOCATION', 'runtime invocation binding identity differs')
    return invocation


def invocation_parameters(invocation: BoundInvocation) -> dict:
    fields = dict(binding_slot_ids='slot_id', binding_region_ids='region_id',
        binding_owner_cores='owner_core', binding_offsets='allocation_offset_bytes',
        binding_sizes='allocation_size_bytes', binding_alignments='allocation_alignment_bytes')
    return dict(entrypoint_id=invocation.entrypoint_id, profile_id=invocation.profile_id,
        **{key: [getattr(row, field) for row in invocation.bindings] for key, field in fields.items()},
        binding_accesses=[int(row.access) for row in invocation.bindings])

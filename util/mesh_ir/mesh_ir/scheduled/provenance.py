import re

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, MemorySpace, StorageClass, TensorRole
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin, AuthoredVariantLineage, CompiledProgramOrigin, CompiledVariantLineage,
    ComposedProgramOrigin, ComposedVariantLineage, CompositionSource, ExternalStorageAdaptation,
)


_DIGEST = re.compile(r'[0-9a-f]{64}')


def validate_program_origin(origin):
    if type(origin) is CompiledProgramOrigin:
        if type(origin.kernel_bundle_semantic_sha256) is not str or not _DIGEST.fullmatch(origin.kernel_bundle_semantic_sha256):
            raise MeshIrError('E_ABI_CHECKSUM', 'compiled Program origin digest is malformed')
    elif type(origin) is AuthoredProgramOrigin:
        if (type(origin.namespace) is not str or not origin.namespace or type(origin.name) is not str or
                not origin.name or type(origin.version) is not int or origin.version < 1):
            raise MeshIrError('E_ABI_BOUNDS', 'authored Program origin is malformed')
    elif type(origin) is ComposedProgramOrigin:
        if type(origin.sources) is not tuple or not origin.sources:
            raise MeshIrError('E_ABI_BOUNDS', 'composed Program requires source identities')
        identities = set()
        for source in origin.sources:
            if (type(source) is not CompositionSource or type(source.origin) not in (CompiledProgramOrigin, AuthoredProgramOrigin) or
                    type(source.variant_count) is not int or source.variant_count < 1):
                raise MeshIrError('E_ABI_BOUNDS', 'composition source is malformed')
            if type(source.program_semantic_sha256) is not str or not _DIGEST.fullmatch(source.program_semantic_sha256):
                raise MeshIrError('E_ABI_CHECKSUM', 'composition source digest is malformed')
            validate_program_origin(source.origin)
            if source.program_semantic_sha256 in identities:
                raise MeshIrError('E_ABI_DUPLICATE', 'composition source identity is repeated')
            identities.add(source.program_semantic_sha256)
    else:
        raise MeshIrError('E_ABI_BOUNDS', 'Program origin type is invalid')


def resolve_variant_lineage(origin, lineage):
    source_program_id = 0
    if type(origin) is ComposedProgramOrigin:
        if (type(lineage) is not ComposedVariantLineage or type(lineage.source_program_id) is not int or
                not 1 <= lineage.source_program_id <= len(origin.sources)):
            raise MeshIrError('E_ABI_BOUNDS', 'composed variant source Program is invalid')
        source = origin.sources[lineage.source_program_id - 1]
        if type(lineage.source_variant_id) is not int or not 1 <= lineage.source_variant_id <= source.variant_count:
            raise MeshIrError('E_ABI_BOUNDS', 'composed variant source identity is invalid')
        if type(lineage.storage_adaptations) is not tuple:
            raise MeshIrError('E_ABI_BOUNDS', 'storage adaptations require typed records')
        previous = 0
        for adaptation in lineage.storage_adaptations:
            if (type(adaptation) is not ExternalStorageAdaptation or type(adaptation.object_id) is not int or
                    adaptation.object_id <= previous or adaptation.source_space is not MemorySpace.HBM or
                    adaptation.target_space is not MemorySpace.HOST_SHARED or type(source.origin) is not CompiledProgramOrigin):
                raise MeshIrError('E_ABI_BOUNDS', 'external storage adaptation is invalid')
            previous = adaptation.object_id
        source_program_id = lineage.source_program_id
        origin, lineage = source.origin, lineage.source_lineage
    if type(origin) is CompiledProgramOrigin:
        if (type(lineage) is not CompiledVariantLineage or type(lineage.kernel_module_ordinal) is not int or
                lineage.kernel_module_ordinal < 0 or type(lineage.kernel_module_semantic_sha256) is not str or
                not _DIGEST.fullmatch(lineage.kernel_module_semantic_sha256)):
            raise MeshIrError('E_ABI_CHECKSUM', 'compiled variant lineage is malformed')
    elif type(origin) is AuthoredProgramOrigin:
        if type(lineage) is not AuthoredVariantLineage or type(lineage.authoring_variant_id) is not str or not lineage.authoring_variant_id:
            raise MeshIrError('E_ABI_BOUNDS', 'authored variant lineage is malformed')
    else:
        raise MeshIrError('E_ABI_BOUNDS', 'variant origin is invalid')
    return origin, lineage, source_program_id


def validate_storage_adaptations(lineage, records):
    if type(lineage) is not ComposedVariantLineage:
        return
    for adaptation in lineage.storage_adaptations:
        if not 1 <= adaptation.object_id <= len(records.objects):
            raise MeshIrError('E_ABI_BOUNDS', 'storage adaptation object escapes its source variant')
        obj = records.objects[adaptation.object_id - 1]
        tensor = records.tensors[obj.storage_tensor_id - 1]
        if (obj.memory_space is not adaptation.target_space or tensor.role is not TensorRole.INPUT or
                tensor.storage_class is not StorageClass.EXTERNAL or tensor.access is not Access.READ_ONLY):
            raise MeshIrError('E_BINDING_ROLE', 'storage adaptation must preserve an external read-only user input')

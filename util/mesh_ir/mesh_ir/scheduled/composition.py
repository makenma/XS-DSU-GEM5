from dataclasses import dataclass, replace

from mesh_ir.analysis.derivation import same_records
from mesh_ir.analysis.sram import SramAllocation, plan_memory_sram
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.kernel_ir import KernelMemoryRecords
from mesh_ir.model import Program
from mesh_ir.passes.scheduled import lower_static_to_program
from mesh_ir.passes.static_sram import _AuthoredStreamPlan, _AuthoredVariantSource, _CompiledVariantSource, _StaticSramState, _VariantSramPlan
from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin, CompiledProgramOrigin, ComposedProgramOrigin, ComposedVariantLineage,
    CompositionSource, ExternalSlotBacking, ExternalStorageAdaptation, KernelCommandSource,
    LocalAllocationBacking, ObjectBacking,
)
from mesh_ir.scheduled.records import variant_memory_records, verify_variant_partitions
from mesh_ir.scheduled.provenance import resolve_variant_lineage, validate_program_origin
from mesh_ir.scheduled.verify import verify_program


@dataclass(frozen=True)
class VariantComposition:
    program: Program
    variant_id: int
    entrypoint: str
    profile_id: str
    storage_adaptations: tuple[ExternalStorageAdaptation, ...] = ()


def adapt_external_storage(records: KernelMemoryRecords, adaptations: tuple[ExternalStorageAdaptation, ...]) -> KernelMemoryRecords:
    objects = list(records.objects)
    previous = 0
    if type(adaptations) is not tuple:
        raise MeshIrError('E_ABI_BOUNDS', 'storage adaptations require typed records')
    for adaptation in adaptations:
        if (type(adaptation) is not ExternalStorageAdaptation or type(adaptation.object_id) is not int or
                not previous < adaptation.object_id <= len(objects) or
                adaptation.source_space is not MemorySpace.HBM or adaptation.target_space is not MemorySpace.HOST_SHARED):
            raise MeshIrError('E_ABI_BOUNDS', 'external storage adaptation is invalid')
        obj = objects[adaptation.object_id - 1]
        tensor = records.tensors[obj.storage_tensor_id - 1]
        if (obj.memory_space is not adaptation.source_space or tensor.role is not TensorRole.INPUT or
                tensor.storage_class is not StorageClass.EXTERNAL or tensor.access is not Access.READ_ONLY):
            raise MeshIrError('E_BINDING_ROLE', 'storage adaptation must preserve an external read-only user input')
        objects[adaptation.object_id - 1] = replace(obj, memory_space=adaptation.target_space)
        previous = adaptation.object_id
    return replace(records, objects=tuple(objects))


def _variant_allocations(program, variant):
    membership = variant.membership
    span = membership.object_backings
    backings = program.semantics.object_backings[span.first_id - 1:span.first_id - 1 + span.count]
    by_allocation = {row.backing.allocation_id: row.object_id for row in backings if type(row.backing) is LocalAllocationBacking}
    span = membership.allocations
    return tuple(SramAllocation(row.allocation_id - span.first_id + 1,
        by_allocation[row.allocation_id] - membership.objects.first_id + 1,
        row.owner_core, row.offset_bytes, row.size_bytes, row.alignment_bytes)
        for row in program.allocations[span.first_id - 1:span.first_id - 1 + span.count])


def _authored_streams(program, variant):
    commands = {row.command_id: row.source for row in program.semantics.command_semantics}
    streams = []
    span = variant.membership.streams
    for stream in program.semantics.streams[span.first_id - 1:span.first_id - 1 + span.count]:
        ids = program.semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
        ordered = tuple(replace(commands[identity], kernel_op_id=commands[identity].kernel_op_id - variant.membership.kernel_ops.first_id + 1)
            if type(commands[identity]) is KernelCommandSource else commands[identity] for identity in ids)
        streams.append(_AuthoredStreamPlan(stream.core_id, stream.physical_stream_id, stream.flags, ordered))
    return tuple(streams)


def compose_program(selections: tuple[VariantComposition, ...], arch) -> Program:
    if type(selections) is not tuple or not selections or any(type(row) is not VariantComposition for row in selections):
        raise MeshIrError('E_ABI_BOUNDS', 'composition requires typed source selections')
    sources, source_ids, variants = [], {}, []
    for selection in selections:
        program = selection.program
        verify_program(program, arch)
        if (type(program.semantics.origin) not in (CompiledProgramOrigin, AuthoredProgramOrigin) or
                program.required_features != A.REQUIRED_FEATURES):
            raise MeshIrError('E_ABI_BOUNDS', 'composition requires base execution Programs')
        if type(selection.variant_id) is not int or not 1 <= selection.variant_id <= len(program.semantics.variants):
            raise MeshIrError('E_ABI_BOUNDS', 'composition source variant is missing')
        if program.semantic_sha256 not in source_ids:
            sources.append(program)
            source_ids[program.semantic_sha256] = len(sources)
        variant = program.semantics.variants[selection.variant_id - 1]
        lineage = ComposedVariantLineage(source_ids[program.semantic_sha256], variant.variant_id,
            variant.lineage, selection.storage_adaptations)
        records = adapt_external_storage(variant_memory_records(program.semantics, variant), selection.storage_adaptations)
        plan = plan_memory_sram(records, arch, allocations=_variant_allocations(program, variant))
        if type(program.semantics.origin) is CompiledProgramOrigin:
            source = _CompiledVariantSource(selection.entrypoint, selection.profile_id, lineage, records)
        else:
            membership = variant.membership
            span = membership.binding_slots
            slots = tuple(replace(row, slot_id=row.slot_id - span.first_id + 1,
                reference_binding=replace(row.reference_binding, slot_id=row.slot_id - span.first_id + 1))
                for row in program.semantics.binding_slots[span.first_id - 1:span.first_id - 1 + span.count])
            span = membership.object_backings
            backings = tuple(ObjectBacking(row.object_id - membership.objects.first_id + 1,
                ExternalSlotBacking(row.backing.slot_id - membership.binding_slots.first_id + 1))
                for row in program.semantics.object_backings[span.first_id - 1:span.first_id - 1 + span.count]
                if type(row.backing) is ExternalSlotBacking)
            source = _AuthoredVariantSource(selection.entrypoint, selection.profile_id, lineage, records, _authored_streams(program, variant), backings, slots)
        variants.append(_VariantSramPlan(source, plan))
    origin = ComposedProgramOrigin(tuple(CompositionSource(source.semantic_sha256, source.semantics.origin,
        len(source.semantics.variants)) for source in sources))
    result = lower_static_to_program(_StaticSramState(origin, tuple(variants)), arch)
    verify_composition_sources(result, tuple(sources))
    return result


def verify_composition_sources(program: Program, sources: tuple[Program, ...]) -> None:
    if type(program) is not Program or program.semantic_sha256 != program.computed_semantic_sha256:
        raise MeshIrError('E_ABI_CHECKSUM', 'composed Program identity differs')
    origin = program.semantics.origin
    if type(origin) is not ComposedProgramOrigin or type(sources) is not tuple or len(sources) != len(origin.sources):
        raise MeshIrError('E_ABI_CHECKSUM', 'composition source set differs')
    validate_program_origin(origin)
    verify_variant_partitions(program.semantics, program)
    for source, identity in zip(sources, origin.sources):
        if (type(source) is not Program or source.semantic_sha256 != source.computed_semantic_sha256 or
                source.semantic_sha256 != identity.program_semantic_sha256 or source.semantics.origin != identity.origin or
                len(source.semantics.variants) != identity.variant_count):
            raise MeshIrError('E_ABI_CHECKSUM', 'composition source identity differs')
    for variant in program.semantics.variants:
        lineage = variant.lineage
        resolve_variant_lineage(origin, lineage)
        source = sources[lineage.source_program_id - 1]
        original = source.semantics.variants[lineage.source_variant_id - 1]
        if lineage.source_lineage != original.lineage:
            raise MeshIrError('E_ABI_CHECKSUM', 'composition source lineage differs')
        expected = adapt_external_storage(variant_memory_records(source.semantics, original), lineage.storage_adaptations)
        actual = variant_memory_records(program.semantics, variant)
        if not same_records(actual, expected):
            raise MeshIrError('E_ABI_CHECKSUM', 'composition changed source Kernel records')
        if type(source.semantics.origin) is AuthoredProgramOrigin and _authored_streams(program, variant) != _authored_streams(source, original):
            raise MeshIrError('E_ABI_CHECKSUM', 'composition changed source authored streams')
        if _variant_allocations(program, variant) != _variant_allocations(source, original):
            raise MeshIrError('E_ABI_CHECKSUM', 'composition changed source SRAM allocations')

from dataclasses import replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from tests.integration.support.workspace_program import build_workspace_program


def test_variant_record_extraction_preserves_each_kernel_domain():
    from mesh_ir.scheduled.records import variant_memory_records
    program, arch = build_workspace_program(output_rows=6, publish_rows=4)
    records = [variant_memory_records(program.semantics, variant) for variant in program.semantics.variants]
    for record in records:
        verify_kernel_memory(record, arch)
        assert [row.object_id for row in record.objects] == list(range(1, len(record.objects) + 1))
    assert sum(len(record.ops) for record in records) == len(program.semantics.kernel_ops)
    assert sum(len(record.computations) for record in records) == 1
    assert records[0].computations[0].opcode.name == 'MATMUL'


def test_variant_record_extraction_rejects_cross_variant_tensor():
    from mesh_ir.scheduled.records import variant_memory_records
    program, _ = build_workspace_program()
    first, second = program.semantics.variants
    bad = replace(program.semantics.computations[0], result_tensor_id=second.membership.kernel_tensors.first_id)
    semantics = replace(program.semantics, computations=(bad,))
    with pytest.raises(MeshIrError, match='E_ABI_BOUNDS'):
        variant_memory_records(semantics, first)


@pytest.mark.parametrize('damage', [None, 'coercible_identity', 'overlap', 'gap', 'uncovered_tail'])
def test_variant_partitions_cover_admitted_collections(damage):
    from mesh_ir.scheduled.records import verify_variant_partitions
    program, _ = build_workspace_program()
    semantics = program.semantics
    if damage:
        first, second = semantics.variants
        span = second.membership.objects
        if damage == 'uncovered_tail':
            span = replace(span, count=span.count-1)
        else:
            value = {'coercible_identity': False, 'overlap': span.first_id-1,
                     'gap': span.first_id+1}[damage]
            span = replace(span, first_id=value)
        second = replace(second, membership=replace(second.membership, objects=span))
        semantics = replace(semantics, variants=(first, second))
        with pytest.raises(MeshIrError, match='E_ABI_ORDER|E_ABI_BOUNDS'):
            verify_variant_partitions(semantics, program)
    else:
        verify_variant_partitions(semantics, program)


def composed_workspace_program():
    from mesh_ir.scheduled.model import ComposedProgramOrigin, ComposedVariantLineage, CompositionSource
    from mesh_ir.serving_profiles import apply_request_profile_keys
    program, arch = build_workspace_program()
    origin = ComposedProgramOrigin((CompositionSource(program.semantic_sha256, program.semantics.origin, len(program.semantics.variants)),))
    variants = tuple(replace(row, lineage=ComposedVariantLineage(1, row.variant_id, row.lineage, ()))
                     for row in program.semantics.variants)
    return apply_request_profile_keys(replace(program, semantics=replace(program.semantics, origin=origin, variants=variants))), arch


def test_composed_origin_roundtrip_retains_source_lineage():
    from mesh_ir.abi import decode_program, encode_program
    from mesh_ir.scheduled.verify import verify_program
    program, arch = composed_workspace_program()
    verify_program(program, arch)
    decoded = decode_program(encode_program(program))
    verify_program(decoded, arch)
    assert decoded.semantics.origin == program.semantics.origin
    assert [row.lineage for row in decoded.semantics.variants] == [row.lineage for row in program.semantics.variants]


def test_native_composed_origin_admission(driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch = composed_workspace_program()
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'ACCEPTED'


@pytest.mark.parametrize('mutation', ['source_digest', 'source_index', 'source_variant', 'lineage_kind', 'repeated_source'])
def test_composed_origin_rejects_false_source_identity(mutation, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from mesh_ir.scheduled.model import CompiledVariantLineage
    from mesh_ir.scheduled.verify import verify_program
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch = composed_workspace_program()
    origin = program.semantics.origin
    variants = list(program.semantics.variants)
    if mutation == 'source_digest':
        origin = replace(origin, sources=(replace(origin.sources[0], program_semantic_sha256='invalid'),))
    elif mutation == 'repeated_source':
        origin = replace(origin, sources=origin.sources * 2)
    else:
        fields = {'source_index': {'source_program_id': 2}, 'source_variant': {'source_variant_id': 3},
                  'lineage_kind': {'source_lineage': CompiledVariantLineage(0, 'a' * 64)}}[mutation]
        variants[0] = replace(variants[0], lineage=replace(variants[0].lineage, **fields))
    program = apply_request_profile_keys(replace(program, semantics=replace(program.semantics, origin=origin, variants=tuple(variants))))
    with pytest.raises(MeshIrError, match='E_ABI_CHECKSUM|E_ABI_BOUNDS|E_ABI_DUPLICATE'):
        verify_program(program, arch)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) != 'ACCEPTED'


@pytest.fixture(scope='module')
def compiled_composition():
    from mesh_ir.builder import ProgramBuilder
    from mesh_ir.canonical import semantic_sha256
    from mesh_ir.ir.common import Access, MemorySpace
    from mesh_ir.ir.kernel_ir import KernelBundle
    from mesh_ir.passes.execution import PassExecutor
    from mesh_ir.passes.scheduled import lower_to_program
    from mesh_ir.scheduled.composition import VariantComposition, compose_program
    from mesh_ir.scheduled.model import AuthoredProgramOrigin, ExternalStorageAdaptation
    from tests.unit.test_gate2_scheduled_pipeline import _inputs, _lowering_for_bundle
    from tests.unit.test_gate2_authored_facade import _declare_variant
    arch, lowering, effective = _inputs()
    module, = lowering.bundle.modules
    module = replace(module, tensors=(replace(module.tensors[0], access=Access.READ_ONLY), *module.tensors[1:]))
    module = replace(module, semantic_sha256=semantic_sha256(module.semantic_dict()))
    lowering = _lowering_for_bundle(KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (module,)))
    with PassExecutor(1) as executor:
        compiled = lower_to_program(lowering, arch, effective, executor).program
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('test', 'additional-phase', 1))
    _declare_variant(builder, arch)
    authored = builder.build()
    selections = (VariantComposition(compiled, 1, 'prefill', 'main',
        (ExternalStorageAdaptation(1, MemorySpace.HBM, MemorySpace.HOST_SHARED),)),
        VariantComposition(authored, 1, 'publish', 'main'))
    result = compose_program(selections, arch)
    return result, compiled, authored, arch, lowering.bundle.modules[0].memory_records()


def test_composition_keeps_compiled_records_and_declares_host_input_adaptation(compiled_composition):
    from mesh_ir.ir.common import MemorySpace
    from mesh_ir.scheduled.composition import verify_composition_sources
    from mesh_ir.scheduled.records import variant_memory_records
    from mesh_ir.scheduled.verify import verify_program
    result, compiled, authored, arch, original = compiled_composition
    verify_program(result, arch)
    verify_composition_sources(result, (compiled, authored))
    records = variant_memory_records(result.semantics, result.semantics.variants[0])
    assert records.objects[0].memory_space is MemorySpace.HOST_SHARED
    assert replace(records, objects=original.objects) == original
    assert result.semantics.variants[0].lineage.source_lineage == compiled.semantics.variants[0].lineage
    assert [row.program_semantic_sha256 for row in result.semantics.origin.sources] == [compiled.semantic_sha256, authored.semantic_sha256]
    altered = replace(original, computations=(replace(original.computations[0], result_tensor_id=1),))
    bad_source = replace(compiled, semantics=replace(compiled.semantics, computations=altered.computations))
    with pytest.raises(MeshIrError, match='E_ABI_CHECKSUM'):
        verify_composition_sources(result, (bad_source, authored))


@pytest.mark.parametrize('mutation', ['source_index', 'source_variant', 'lineage_kind', 'changed_compute', 'missing_compute', 'duplicate_compute', 'trailing_compute', 'missing_effect', 'undeclared_storage'])
def test_composition_source_correspondence_rejects_mutations(compiled_composition, mutation):
    from mesh_ir.canonical import semantic_sha256
    from mesh_ir.scheduled.composition import verify_composition_sources
    from mesh_ir.scheduled.model import AuthoredVariantLineage
    result, compiled, authored, _, _ = compiled_composition
    semantics = result.semantics
    first, *others = semantics.variants
    if mutation in ('source_index', 'source_variant', 'lineage_kind', 'undeclared_storage'):
        fields = {'source_index': {'source_program_id': 99}, 'source_variant': {'source_variant_id': 99},
                  'lineage_kind': {'source_lineage': AuthoredVariantLineage('false')},
                  'undeclared_storage': {'storage_adaptations': ()}}[mutation]
        semantics = replace(semantics, variants=(replace(first, lineage=replace(first.lineage, **fields)), *others))
    elif mutation == 'missing_effect':
        semantics = replace(semantics, kernel_ops=semantics.kernel_ops[:-1])
    else:
        rows = semantics.computations
        if mutation == 'changed_compute':
            rows = (replace(rows[0], result_tensor_id=1), *rows[1:])
        elif mutation == 'missing_compute':
            rows = rows[1:]
        elif mutation == 'trailing_compute':
            rows = (*rows, rows[0])
        else:
            rows = (rows[0], *rows)
        semantics = replace(semantics, computations=rows)
    result = replace(result, semantics=semantics)
    result = replace(result, semantic_sha256=semantic_sha256(result.semantic_dict()))
    with pytest.raises(MeshIrError, match='E_ABI_CHECKSUM|E_ABI_BOUNDS|E_ABI_DUPLICATE'):
        verify_composition_sources(result, (compiled, authored))


def test_native_compiled_composition_admission(compiled_composition, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    result, _, _, arch, _ = compiled_composition
    assert _cpp_verdict(encode_program(result), driver, tmp_path, arch) == 'ACCEPTED'


@pytest.mark.parametrize('mutation', ['object_bounds', 'direction', 'physical_space'])
def test_storage_adaptation_native_rejection(compiled_composition, driver, tmp_path, mutation):
    from mesh_ir.abi import encode_program
    from mesh_ir.canonical import semantic_sha256
    from mesh_ir.ir.common import MemorySpace
    from mesh_ir.scheduled.verify import verify_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    result, _, _, arch, _ = compiled_composition
    first, *others = result.semantics.variants
    adaptation, = first.lineage.storage_adaptations
    if mutation == 'physical_space':
        objects = tuple(replace(row, memory_space=MemorySpace.HBM) if row.object_id == 1 else row
            for row in result.semantics.objects)
        semantics = replace(result.semantics, objects=objects)
    else:
        adaptation = replace(adaptation, **({'object_id': 999} if mutation == 'object_bounds' else {'target_space': MemorySpace.HBM}))
        first = replace(first, lineage=replace(first.lineage, storage_adaptations=(adaptation,)))
        semantics = replace(result.semantics, variants=(first, *others))
    result = replace(result, semantics=semantics)
    result = replace(result, semantic_sha256=semantic_sha256(result.semantic_dict()))
    with pytest.raises(MeshIrError):
        verify_program(result, arch)
    assert _cpp_verdict(encode_program(result), driver, tmp_path, arch) != 'ACCEPTED'

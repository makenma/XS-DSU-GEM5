from dataclasses import replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import MemorySpace, TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.composition import VariantComposition, compose_program, verify_composition_sources
from mesh_ir.scheduled.model import ExternalStorageAdaptation
from mesh_ir.scheduled.records import variant_memory_records
from tests.integration.test_tensor_payload import payload_carrier


@pytest.fixture(scope='module')
def compiled_publication(payload_carrier):
    from mesh_ir.serving_composition import build_publication_program
    _, compiled, arch, *_ = payload_carrier
    source, = compiled.semantics.variants
    bindings = ProgramBindingCatalog(compiled).for_variant(source.entrypoint_id, source.profile_id)
    output, = (row for row in bindings.values() if row.tensor.role is TensorRole.OUTPUT)
    publication = build_publication_program(arch, output, staging_bytes=32)
    adaptations = tuple(ExternalStorageAdaptation(row.object.object_id - source.membership.objects.first_id + 1,
        MemorySpace.HBM, MemorySpace.HOST_SHARED) for row in sorted(bindings.values(), key=lambda row: row.object.object_id)
        if row.tensor.role is TensorRole.INPUT)
    program = compose_program((VariantComposition(compiled, 1, 'prefill', 'static', adaptations),
        VariantComposition(publication, 1, 'publish', 'static')), arch)
    return program, compiled, publication, arch, output


def test_actual_export_composition_preserves_all_computations_and_effects(compiled_publication):
    from mesh_ir.scheduled.verify import verify_program
    program, compiled, publication, arch, output = compiled_publication
    verify_program(program, arch)
    verify_composition_sources(program, (compiled, publication))
    original = variant_memory_records(compiled.semantics, compiled.semantics.variants[0])
    actual = variant_memory_records(program.semantics, program.semantics.variants[0])
    assert actual.computations == original.computations
    assert actual.ops == original.ops
    assert len(original.computations) >= 4
    assert len(program.semantics.variants[0].lineage.storage_adaptations) == 3
    assert {row.tensor.role for row in ProgramBindingCatalog(program).for_variant(1, 1).values()
            if row.is_immutable_model_content} == {TensorRole.WEIGHT, TensorRole.STATE}
    publish, = publication.semantics.variants
    workspace = ProgramBindingCatalog(publication).binding(publish.entrypoint_id, publish.profile_id, 'workspace')
    assert (workspace.tensor.dtype, workspace.tensor.shape, workspace.tensor.strides) == (output.tensor.dtype, output.tensor.shape, output.tensor.strides)
    assert max(row.offset_bytes + row.size_bytes for row in publication.allocations) <= 32
    assert sum(row.rows * row.row_bytes for row in publication.dma_descriptors) == output.object.footprint_bytes * 2


def test_actual_export_composition_native_admission(compiled_publication, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, _, _, arch, _ = compiled_publication
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'ACCEPTED'


@pytest.fixture(scope='module')
def compiled_serving(compiled_publication, payload_carrier):
    from mesh_ir.serving_composition import attach_prefill_request
    from mesh_ir.tensor_payload import TensorPayload
    program, compiled, publication, arch, _ = compiled_publication
    packed = TensorPayload.load(payload_carrier[0]).bind(compiled, arch, entrypoint_id=1, profile_id=1)
    serving = attach_prefill_request(program, arch, packed.inputs, input_tokens=2, publish_chunk_bytes=32)
    return serving, packed, compiled, publication, arch


def test_compiled_serving_maps_actual_packed_inputs_and_computed_workspace(compiled_serving):
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.serving_tensor_bindings import ServingTensorBindingCatalog
    from mesh_ir.scheduled.verify import verify_program
    serving, packed, compiled, publication, arch = compiled_serving
    verify_program(serving, arch)
    verify_composition_sources(serving, (compiled, publication))
    catalog = ServingTensorBindingCatalog(serving)
    mappings = [row for row in catalog.for_instance(1) if row.mapping.binding_kind == AG.BINDING_KIND.HOST_INPUT]
    assert {row.binding.slot.symbol: row.mapping.offset_bytes for row in mappings} == {row.symbol: row.offset for row in packed.inputs}
    assert catalog.workspace_sizes[(1, 1)] == 64
    assert serving.agent_request_profiles[0].input_binding_bytes == len(packed.input_bytes)
    assert not serving.agent_request_profiles[0].flags


def test_compiled_serving_native_admission(compiled_serving, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    serving, _, _, _, arch = compiled_serving
    assert _cpp_verdict(encode_program(serving), driver, tmp_path, arch) == 'ACCEPTED'


@pytest.fixture
def compiled_driver_fixture(compiled_serving, payload_carrier, tmp_path):
    import hashlib
    import json
    import yaml
    from mesh_ir.abi import encode_program
    from mesh_ir.serving_profiles import serving_capacity_required
    from mesh_ir.tensor_payload import TensorPayload
    from tests.integration.serving_runtime import write_serving_fixture, write_serving_observation_probe
    serving, _, compiled, publication, arch = compiled_serving
    write_serving_fixture(tmp_path, serving, payload_carrier[-1])
    for name, program in (('compiled-source', compiled), ('publication-source', publication)):
        (tmp_path / f'{name}.mshb').write_bytes(encode_program(program))
        (tmp_path / f'{name}.json').write_text(json.dumps(program.canonical_dict()))
    payload = TensorPayload.load(payload_carrier[0]).bind(serving, arch, entrypoint_id=1, profile_id=1)
    (tmp_path / 'input.bin').write_bytes(payload.input_bytes)
    workload_path = tmp_path / 'workload.json'
    workload = json.loads(workload_path.read_text())
    workload['users'][0]['tasks'][0]['rounds'][0].update(input_payload_file='input.bin',
        input_content_digest=hashlib.sha256(payload.input_bytes).hexdigest())
    workload_path.write_text(json.dumps(workload))
    (tmp_path / 'memory-image.json').write_text(json.dumps(payload.memory_image))
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    config['serving'].update(serving_capacity_required(serving).fields())
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config))
    address_map = config['serving']['address_map']
    addresses = {name: address_map[name + '_arena']['base'] for name in ('input', 'output', 'workspace')}
    ranges = [(addresses['input'], len(payload.input_bytes)), (addresses['output'], 64), (addresses['workspace'], 64)]
    ranges.extend((row['address'], row['size']) for row in payload.memory_image['spans'])
    probe = write_serving_observation_probe(tmp_path, arch, ranges)
    return tmp_path, probe, payload, addresses, serving


def test_compiled_prefill_driver_publishes_actual_computed_bytes(compiled_driver_fixture):
    import json
    from mesh_ir.scheduled.model import ComputeExecution, KernelCommandSource
    from tests.integration.serving_runtime import run_serving_fixture
    work, probe, payload, addresses, program = compiled_driver_fixture
    _, result, metrics = run_serving_fixture(work, 'runtime', entry=probe,
        extra_args=('--memory-file-image', str(work / 'memory-image.json')))
    assert result['terminal'] == 'DONE'
    assert metrics['completed_tasks'] == metrics['frontend_drained'] == 1
    assert metrics['metadata_reads_validated'] == 1
    assert metrics['kv_session_records'] == metrics['kv_session_tombstones'] == 0
    assert all(metrics[key] == 0 for key in ('live_contexts', 'live_cq_obligations', 'live_sq_intakes', 'live_submissions'))
    checks = {row['address']: bytes.fromhex(row['bytes_hex']) for endpoint in result['memory_endpoints']
        for row in endpoint['verifies'] if row['bytes_hex']}
    assert checks[addresses['input']] == payload.input_bytes
    output = checks[addresses['output']]
    assert output == checks[addresses['workspace']] and output != bytes(64)
    output_tensor, = (row.tensor for row in ProgramBindingCatalog(program).for_variant(1, 1).values()
        if row.tensor.role is TensorRole.OUTPUT)
    operations = {row.op_id for row in program.semantics.kernel_ops if row.computation_id == output_tensor.producer_computation_id}
    command_ids = {row.command_id for row in program.semantics.command_semantics
        if type(row.source) is KernelCommandSource and row.source.kernel_op_id in operations and type(row.execution) is ComputeExecution}
    computed = [row for instance in result['instances'] for core in instance['cores'].values()
        for row in core['observations']['compute_outputs'] if row['command_id'] in command_ids]
    assert b''.join(bytes.fromhex(row['bytes_hex']) for item in computed for row in item['rows']) == output
    for span in payload.memory_image['spans']:
        with open(span['path'], 'rb') as stream:
            stream.seek(span['offset'])
            assert checks[span['address']] == stream.read(span['size'])
    assert len(result['instances']) == 2
    assert all(bridge['idle'] == 1 for bridge in result['bridges'])
    (work / 'component-result.json').write_text(json.dumps({'business_smoke': 'NOT_RUN',
        'terminal': result['terminal'], 'program_semantic_sha256': program.semantic_sha256,
        'computed_output_hex': output.hex(), 'input_bytes': len(payload.input_bytes),
        'immutable_owner_bytes': payload.owner_bytes, 'frontend_drained': metrics['frontend_drained']}, indent=2))


def test_compiled_prefill_driver_drains_storage_error(compiled_driver_fixture):
    from mesh_ir.gate3_oracle import read_facts_document
    from tests.integration.serving_runtime import assert_serving_error_quiescent, run_serving_fixture
    work, probe, _, addresses, _ = compiled_driver_fixture
    _, result, metrics = run_serving_fixture(work, 'fault', entry=probe, fault_index=0,
        fault_target_address=addresses['workspace'],
        extra_args=('--memory-file-image', str(work / 'memory-image.json')))
    assert_serving_error_quiescent(result, metrics, read_facts_document(work / 'fault/gate6_facts.tsv'), [1])


@pytest.mark.parametrize('mutation', ['zero_staging', 'over_sram', 'strided', 'wrong_role'])
def test_publication_rejects_unrepresentable_storage(compiled_publication, mutation):
    from mesh_ir.serving_composition import build_publication_program
    _, _, _, arch, output = compiled_publication
    size = 32
    if mutation == 'zero_staging':
        size = 0
    elif mutation == 'over_sram':
        size = arch.sram_bytes + 1
    elif mutation == 'strided':
        output = replace(output, tensor=replace(output.tensor, strides=(9, 1)))
    else:
        output = replace(output, tensor=replace(output.tensor, role=TensorRole.INPUT))
    with pytest.raises(MeshIrError):
        build_publication_program(arch, output, staging_bytes=size)

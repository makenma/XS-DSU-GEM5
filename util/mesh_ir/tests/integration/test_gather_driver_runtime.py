import hashlib
import json
from dataclasses import replace

import pytest
import torch
import yaml

from examples.gather_carrier import create_model
from mesh_ir.abi import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.compile_service import compile_exported_program
from mesh_ir.ir.common import MemorySpace, TensorRole
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.composition import VariantComposition, compose_program
from mesh_ir.scheduled.model import ExternalStorageAdaptation
from mesh_ir.serving_composition import attach_prefill_request, build_publication_program
from mesh_ir.serving_profiles import serving_capacity_required
from mesh_ir.tensor_payload import TensorPayload
from mesh_ir.torch_compat import load_exported_program, write_tensor_payload
from tests.integration.serving_runtime import run_serving_fixture, write_serving_fixture, write_serving_observation_probe
from tests.integration.test_gate2_gather_streaming import ARCH, CONFIG


@pytest.fixture(scope='module')
def gather_source(tmp_path_factory):
    work = tmp_path_factory.mktemp('gather-serving')
    model = create_model()
    with torch.no_grad():
        model.weight.copy_(torch.arange(model.weight.shape[0], dtype=torch.float32)[:, None])
    indices = torch.tensor([7, 2, 7], dtype=torch.int32)
    exported = torch.export.export(model, (indices,))
    torch.export.save(exported, work / 'model.pt2')
    manifest = write_tensor_payload(load_exported_program(work / 'model.pt2'), work / 'payload')
    compile_exported_program(work / 'model.pt2', ARCH, CONFIG, work / 'compiled', workers=1)
    return work / 'compiled', load_arch(ARCH), TensorPayload.load(manifest), model(indices).detach().numpy().tobytes()


@pytest.fixture(scope='module')
def gathered_serving(gather_source):
    directory, arch, payload, expected = gather_source
    compiled = decode_program((directory / 'program.mshb').read_bytes())
    source, = compiled.semantics.variants
    bindings = ProgramBindingCatalog(compiled).for_variant(source.entrypoint_id, source.profile_id)
    output, = [row for row in bindings.values() if row.tensor.role is TensorRole.OUTPUT]
    publication = build_publication_program(arch, output, staging_bytes=1024)
    adaptations = tuple(ExternalStorageAdaptation(row.object.object_id - source.membership.objects.first_id + 1,
        MemorySpace.HBM, MemorySpace.HOST_SHARED) for row in bindings.values() if row.tensor.role is TensorRole.INPUT)
    composed = compose_program((VariantComposition(compiled, 1, 'prefill', 'static', adaptations),
        VariantComposition(publication, 1, 'publish', 'static')), arch)
    inputs = payload.bind(compiled, arch, entrypoint_id=1, profile_id=1).inputs
    program = attach_prefill_request(composed, arch, inputs, input_tokens=3, publish_chunk_bytes=1024)
    return program, arch, payload, expected


def test_short_gather_input_preserves_allocation_capacity(gathered_serving, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch, payload, _ = gathered_serving
    bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1)
    request, = program.agent_request_profiles
    assert request.full_input_dma_bytes == program.agent_instance_profiles[0].host_input_dma_bytes_per_member == 12
    assert request.input_binding_bytes == len(bound.input_bytes) > 12
    assert bound.input_bytes[:12] == b''.join(value.to_bytes(4, 'little') for value in [7, 2, 7])
    assert bound.input_bytes[12:] == bytes(len(bound.input_bytes)-12)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'ACCEPTED'


def test_short_gather_input_rejects_undersized_mapping(gathered_serving, driver, tmp_path):
    from mesh_ir.abi import encode_program
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from mesh_ir.scheduled.verify import verify_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    program, arch, _, _ = gathered_serving
    mappings = tuple(replace(row, span_bytes=12) if row.binding_kind == AG.BINDING_KIND.HOST_INPUT
                     else row for row in program.agent_instance_tensor_bindings)
    damaged = apply_request_profile_keys(replace(program, agent_instance_tensor_bindings=mappings))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        verify_program(damaged, arch)
    assert _cpp_verdict(encode_program(damaged), driver, tmp_path, arch) == 'VERIFY:E_BINDING_ROLE'


@pytest.fixture
def gather_driver(gathered_serving, tmp_path):
    program, arch, payload, expected = gathered_serving
    write_serving_fixture(tmp_path, program, ARCH)
    bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1)
    (tmp_path / 'input.bin').write_bytes(bound.input_bytes)
    (tmp_path / 'memory-image.json').write_text(json.dumps(bound.memory_image))
    workload_path = tmp_path / 'workload.json'
    workload = json.loads(workload_path.read_text())
    workload['users'][0]['tasks'][0]['rounds'][0].update(input_payload_file='input.bin',
        input_content_digest=hashlib.sha256(bound.input_bytes).hexdigest())
    workload_path.write_text(json.dumps(workload))
    config_path = tmp_path / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['serving'].update(serving_capacity_required(program).fields())
    arenas = config['serving']['address_map']
    end = max(row['address'] + row['size'] for row in bound.memory_image['spans'])
    arenas['workspace_arena'] = dict(base=(end+4095)//4096*4096, bytes=8192)
    config_path.write_text(yaml.safe_dump(config))
    ranges = [(arenas[name+'_arena']['base'], len(expected)) for name in ('workspace', 'output')]
    probe = write_serving_observation_probe(tmp_path, arch, ranges, bytes_limit=8192)
    return tmp_path, probe, expected


def test_gather_checkpoint_preserves_occurrences_output_and_drain(gather_driver):
    work, probe, expected = gather_driver
    image_args = ['--memory-file-image', str(work / 'memory-image.json')]
    _, baseline, _ = run_serving_fixture(work, 'baseline', entry=probe, extra_args=image_args)
    gather, = [row for frame in baseline['instances'] for core in frame['cores'].values()
               for row in core['observations']['descriptor_executions'] if row['index_reads']]
    assert [row['raw_index'] for row in gather['index_reads']] == [7, 2, 7]
    checkpoint = work / 'checkpoint'
    _, continued, continued_metrics = run_serving_fixture(work, 'continued', entry=probe,
        extra_args=image_args + ['--checkpoint-dir', str(checkpoint), '--checkpoint-at-tick',
            str(gather['index_reads'][0]['ready_tick'])])
    _, restored, restored_metrics = run_serving_fixture(work, 'restored', entry=probe,
        extra_args=image_args + ['--restore-checkpoint', str(checkpoint)])
    assert restored['terminal'] == continued['terminal'] == 'DONE'
    assert restored_metrics == continued_metrics
    assert restored_metrics['frontend_drained'] == 1
    assert restored['terminal_tick'] == continued['terminal_tick']
    assert restored['instances'] == continued['instances']
    assert restored['burst_timings'] == continued['burst_timings']
    assert restored['bridges'] == continued['bridges']
    manifest = json.loads((checkpoint / 'checkpoint.json').read_text())
    assert manifest['state']['tick'] >= gather['completion_tick']
    for result in (baseline, continued, restored):
        observed = [bytes.fromhex(row['bytes_hex']) for endpoint in result['memory_endpoints']
                    for row in endpoint['verifies']]
        assert observed == [expected, expected]
        assert all(row['idle'] == 1 for row in result['bridges'])
    assert (work / 'continued/gate6_facts.tsv').read_bytes() == (work / 'restored/gate6_facts.tsv').read_bytes()


def test_request_cancel_during_gather_drains_without_publication(gather_driver):
    from mesh_ir.agent_workload import load_workload_plan
    from mesh_ir.gate3_oracle import read_facts_document
    work, probe, _ = gather_driver
    workload = load_workload_plan(work / 'workload.json')
    action = dict(control_ordinal=1, opcode='CANCEL', issuer_user_id=0, issuer_task_seq=0,
        target_user_id=0, target_task_seq=0, target_repair_round=0,
        trigger=dict(kind='AFTER_BATCH_FREEZE', event_user_id=0, event_task_seq=0, event_repair_round=0))
    (work / 'control.json').write_text(json.dumps(dict(schema='agent_control_plan_v1', version=1,
        workload_plan_digest=workload.digest, actions=[action])))
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['agent']['control_plan'] = 'control.json'
    path.write_text(yaml.safe_dump(config))
    _, result, metrics = run_serving_fixture(work, 'cancel', entry=probe,
        extra_args=['--memory-file-image', str(work / 'memory-image.json')])
    facts = read_facts_document(work / 'cancel/gate6_facts.tsv')
    assert result['terminal'] == 'DONE'
    cq = {row['request_id']: row for row in facts.events if row['kind'] == 'CQ_CONSUME'}
    assert {request: row['status'] for request, row in cq.items()} == {1: 'CANCELLED', 2: 'SUCCESS'}
    gather, = [row for frame in result['instances'] for core in frame['cores'].values()
               for row in core['observations']['descriptor_executions'] if row['index_reads']]
    cancel, = [row for row in facts.events if row['kind'] == 'SQ_CONSUME' and row['request_id'] == 2]
    assert gather['index_reads'][0]['read_tick'] <= cancel['tick'] < gather['completion_tick']
    assert cq[1]['tick'] >= gather['completion_tick']
    assert len(result['instances']) == 1
    assert metrics['frontend_drained'] == 1
    assert all(metrics[field] == 0 for field in ('live_contexts', 'live_cq_obligations',
        'live_sq_intakes', 'live_submissions', 'kv_session_records'))
    assert all(row['idle'] == 1 for row in result['bridges'])
    assert not facts.publish_segments


def test_gather_driver_oracle_reconciles_real_read_volume(gather_driver):
    from copy import deepcopy
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.gate3_oracle import read_facts_document
    from mesh_ir.generated import abi as A
    from mesh_ir.serving_e2e_oracle import verify_serving_run
    work, probe, _ = gather_driver
    _, result, metrics = run_serving_fixture(work, 'oracle', entry=probe,
        extra_args=['--memory-file-image', str(work / 'memory-image.json')])
    program = decode_program((work / 'program.mshb').read_bytes())
    facts = read_facts_document(work / 'oracle/gate6_facts.tsv')
    summary = verify_serving_run(program, result['transport'], metrics, facts.serving_phases)
    assert summary['descriptors'] == len(program.dma_descriptors)
    gather, = [row for row in result['transport'] if row['dma_kind'] == A.DMA_KIND.GATHER_LOAD]
    assert gather['read_bytes'] == 3 * 512 * 4
    for field, value in (('read_bytes', 0), ('read_bytes', 2 * 512 * 4), ('write_bytes', 1)):
        rows = deepcopy(result['transport'])
        next(row for row in rows if row['descriptor_id'] == gather['descriptor_id'])[field] = value
        with pytest.raises(MeshIrError, match='E_TRAFFIC_MISMATCH'):
            verify_serving_run(program, rows, metrics, facts.serving_phases)


def test_serving_entrypoint_observes_complete_output_without_compute_byte_dump(gather_driver):
    import os
    import subprocess
    from mesh_ir.runtime_environment import embedded_python_path
    from tests.integration.serving_runtime import GEM5, REPO
    work, _, expected = gather_driver
    out = work / 'direct'
    out.mkdir()
    config = yaml.safe_load((work / 'config.yaml').read_text())
    addresses = config['serving']['address_map']
    argv = [str(GEM5), '--outdir=' + str(out), str(REPO / 'configs/example/ai_mesh/run_gate6_serving.py'),
        '--arch', str(work / 'arch.yaml'), '--serving-program', str(work / 'program.mshb'),
        '--runtime-config', str(work / 'config.yaml'), '--memory-file-image', str(work / 'memory-image.json'),
        '--weight-image-digest', hashlib.sha256((work / 'memory-image.json').read_bytes()).hexdigest(),
        '--sim-tick-limit', '10000000000', '--memory-verify-bytes-limit', str(len(expected)),
        '--compute-bytes-limit', '0']
    for name in ('workspace', 'output'):
        argv.extend(['--verify-memory-range', str(addresses[name + '_arena']['base']), str(len(expected))])
    env = {name: value for name, value in os.environ.items() if not name.startswith(('AI_MESH_', 'GATE6_'))}
    env['PYTHONPATH'] = embedded_python_path()
    with (out / 'console.log').open('w') as stream:
        result = subprocess.run(argv, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
    assert result.returncode == 0, (out / 'console.log').read_text()
    result = json.loads((out / 'mesh_result.json').read_text())
    assert result['terminal'] == 'DONE'
    assert [bytes.fromhex(row['bytes_hex']) for endpoint in result['memory_endpoints']
            for row in endpoint['verifies']] == [expected, expected]
    computed = [row for frame in result['instances'] for core in frame['cores'].values()
                for output in core['observations']['compute_outputs'] for row in output['rows']]
    assert computed and all(not row['bytes_hex'] for row in computed)

import hashlib
import json

import pytest

from mesh_ir.abi import decode_program, encode_program
from mesh_ir.scheduled.composition import verify_composition_sources
from tests.integration.serving_runtime import FIXTURES, GEM5
from tests.integration.test_tensor_payload import payload_carrier
from tests.integration.test_compilation_source import compiled_publication


@pytest.fixture
def smoke_bundle(payload_carrier, compiled_publication, tmp_path):
    from mesh_ir.prefill_smoke import prepare
    manifest, compiled, _, *_, arch_path = payload_carrier
    output = tmp_path / 'bundle'
    prepare(compiled_publication, arch_path, manifest, FIXTURES / 'serving_runtime_config.yaml',
        FIXTURES / 'serving_workload_plan.json', output, input_tokens=2, publish_chunk_bytes=32)
    return output, compiled


@pytest.mark.parametrize('payload_carrier', ['balanced'], indirect=True)
def test_prefill_smoke_prepares_closed_program_and_real_payload(smoke_bundle):
    from mesh_ir.architecture import load_arch
    from mesh_ir.binding_layout import balanced_bindings
    from mesh_ir.runtime_invocation import load_invocation
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
    output, compiled = smoke_bundle
    document = json.loads((output / 'bundle.json').read_text())
    program = decode_program((output / 'program.mshb').read_bytes())
    publication = decode_program((output / 'publication.mshb').read_bytes())
    verify_composition_sources(program, (compiled, publication))
    assert document['prepare'] == 'PASS'
    assert document['program_semantic_sha256'] == program.semantic_sha256
    assert document['source_program_semantic_sha256'] == compiled.semantic_sha256
    source_manifest = (output / 'compilation-manifest.json').read_bytes()
    assert document['inputs']['compilation_manifest']['sha256'] == hashlib.sha256(source_manifest).hexdigest()
    report = json.loads((output / 'compile_report.json').read_text())
    assert report['passes'][-1]['input_hash'] == compiled.semantic_sha256
    assert report['sources'][0]['provenance']['archive_sha256'] == document['payload_export_sha256']
    assert len((output / 'input.bin').read_bytes()) == 192
    arch = load_arch(output / 'arch.yaml')
    invocation = load_invocation(output / 'model-invocation.json', program, arch)
    slots = tuple(row.slot for row in ProgramBindingCatalog(program).for_variant(1, 1).values())
    assert invocation.bindings == balanced_bindings(slots, arch)
    assert invocation.bindings != tuple(row.reference_binding for row in slots)
    for name, digest in document['artifacts'].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_host_model_plan_uses_admitted_relocated_invocation(smoke_bundle):
    from dataclasses import replace
    from mesh_ir.architecture import load_arch
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.host_bindings import build_host_binding_plans
    from mesh_ir.scheduled.addressing import bind_invocation
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    output, _ = smoke_bundle
    program = decode_program((output / 'program.mshb').read_bytes())
    arch = load_arch(output / 'arch.yaml')
    catalog = ProgramBindingCatalog(program).for_variant(1, 1)
    bindings = tuple(replace(row.slot.reference_binding,
        allocation_offset_bytes=row.slot.reference_binding.allocation_offset_bytes +
            (4096 if arch.regions[row.slot.region_id].kind == 'HBM' else 0)) for row in catalog.values())
    invocation = bind_invocation(program, arch, 1, 1, bindings)
    plan, = build_host_binding_plans(program, [row.base for row in arch.regions], invocations=(invocation,))
    by_slot = {row.slot_id: row for row in bindings}
    for requirement in plan.requirements:
        if requirement.kind != AG.BINDING_KIND.WEIGHT_EXTERNAL:
            continue
        symbol = program.strings[requirement.symbol_id - 1].value
        actual = by_slot[catalog[symbol].slot.slot_id]
        assert requirement.platform_address == arch.regions[actual.region_id].base + actual.allocation_offset_bytes
        assert requirement.required_bytes == actual.allocation_size_bytes
    selected, = [row for row in program.semantics.variants if row.variant_id != invocation.variant_id]
    publication = ProgramBindingCatalog(program).for_variant(selected.entrypoint_id, selected.profile_id)
    other = bind_invocation(program, arch, selected.entrypoint_id, selected.profile_id,
        tuple(row.slot.reference_binding for row in publication.values()))
    for invalid in ((invocation, invocation), (replace(invocation, program_semantic_sha256='0' * 64),),
            (replace(invocation, arch_digest=bytes(32)),), (other,)):
        with pytest.raises(MeshIrError, match='E_RELOCATION'):
            build_host_binding_plans(program, [row.base for row in arch.regions], invocations=invalid)


def test_compiled_rom_capacity_counts_all_engine_streams(payload_carrier, driver, tmp_path):
    from dataclasses import replace
    from mesh_ir.canonical import semantic_sha256
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.execution_view import build_execution_views
    from mesh_ir.scheduled.verify import verify_program
    from tests.golden.test_mutation_corpus import _cpp_verdict
    _, program, arch, *_ = payload_carrier
    view, = build_execution_views(program).views
    largest_stream = max(count for _, _, _, count in view.ranges)
    assert max(view.command_counts.values()) > largest_stream
    arch = replace(arch, command_rom_entries=largest_stream)
    program = replace(program, arch_digest=arch.digest())
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    with pytest.raises(MeshIrError, match='command ROM'):
        verify_program(program, arch)
    assert _cpp_verdict(encode_program(program), driver, tmp_path, arch) == 'VERIFY:E_ABI_BOUNDS'


@pytest.mark.parametrize('payload_carrier', ['balanced'], indirect=True)
def test_prefill_smoke_executes_and_checks_actual_driver(smoke_bundle):
    from copy import deepcopy
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.runtime_errors import ReconciliationError
    from mesh_ir.prefill_smoke import check, run
    output, _ = smoke_bundle
    run(output, GEM5, sim_tick_limit=10000000000)
    report = check(output)
    assert report['check'] == 'PASS'
    assert report['output_bytes'] == 64
    assert report['input_bytes'] == 192
    assert report['compute_active_cores']
    assert report['frontend_drained'] == 1
    latency = report['latency']
    assert latency['ticks_per_second'] == 10**12
    assert latency['process_wall_seconds'] > 0
    assert latency['host_request']['begin_tick'] <= latency['mesh']['begin_tick']
    assert latency['mesh']['end_tick'] <= latency['host_request']['end_tick']
    assert latency['request_retirement']['end_tick'] >= latency['host_request']['end_tick']
    for name in ('host_request', 'request_retirement', 'business', 'mesh'):
        row = latency[name]
        assert row['seconds'] == (row['end_tick'] - row['begin_tick']) / latency['ticks_per_second']
    from mesh_ir.gate3_oracle import read_facts_document
    from mesh_ir.serving_e2e_oracle import measure_request_latency
    facts = read_facts_document(output / 'runtime/gate6_facts.tsv')
    for mutation in ('missing', 'duplicate', 'reversed'):
        changed = deepcopy(facts)
        event, = [row for row in changed.events if row['kind'] == 'PUBLICATION_COMMIT']
        if mutation == 'missing':
            changed.events.remove(event)
        elif mutation == 'duplicate':
            changed.events.append(deepcopy(event))
        else:
            event['tick'] = latency['host_request']['end_tick'] + 1
        with pytest.raises(ReconciliationError):
            measure_request_latency(changed, latency['mesh'], ticks_per_second=10**12, process_wall_seconds=1)
    result_path = output / 'runtime/mesh_result.json'
    original = json.loads(result_path.read_text())
    from mesh_ir.generated import agent_abi as AG
    from mesh_ir.architecture import load_arch
    from mesh_ir.host_bindings import build_host_binding_plans
    from mesh_ir.runtime_invocation import load_invocation
    program = decode_program((output / 'program.mshb').read_bytes())
    arch = load_arch(output / 'arch.yaml')
    invocation = load_invocation(output / 'model-invocation.json', program, arch)
    plan, = build_host_binding_plans(program, [row.base for row in arch.regions], invocations=(invocation,))
    weights = [row for row in plan.requirements if row.kind == AG.BINDING_KIND.WEIGHT_EXTERNAL]
    image = json.loads((output / 'memory-image.json').read_text())
    assert {(row.platform_address, row.required_bytes) for row in weights} == {
        (row['address'], row['size']) for row in image['spans']}
    reads = [burst for burst in original['instances'][0]['bursts'] if burst['channel'] == 'AR']
    for weight in weights:
        assert any(weight.platform_address <= row['address'] < weight.platform_address + weight.required_bytes
            for row in reads)
    from mesh_ir.runtime_reconciliation import payload_digest
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
    from mesh_ir.scheduled.model import EndpointSide
    from mesh_ir.tensor_payload import TensorPayload
    payload = TensorPayload.load(json.loads((output / 'bundle.json').read_text())['inputs']['payload']['path'])
    sources = {row.name: row for row in payload.records}
    catalog = ProgramBindingCatalog(program)
    transport = {row['descriptor_id']: row for row in original['transport']}
    blob = payload.path.read_bytes()
    covered = set()
    for descriptor in program.dma_descriptors:
        binding = catalog.descriptor_binding(descriptor.descriptor_id, EndpointSide.SRC)
        if binding is None or not binding.is_immutable_model_content:
            continue
        source = sources[binding.tensor.name]
        expected = b''.join(blob[source.offset + descriptor.src.offset_bytes + index * descriptor.src_stride_bytes:
            source.offset + descriptor.src.offset_bytes + index * descriptor.src_stride_bytes + descriptor.row_bytes]
            for index in range(descriptor.rows))
        assert transport[descriptor.descriptor_id]['payload_digest'] == payload_digest(expected)
        covered.add(source.name)
    assert covered == {row.name for row in payload.records if row.input_data is None}
    for mutation in ('traffic', 'commands', 'output', 'credit', 'core_resource', 'phase_resource', 'endpoint', 'network_pending', 'missing_core'):
        result = deepcopy(original)
        if mutation == 'traffic':
            result['transport'][0]['read_bytes'] += 1
        elif mutation == 'commands':
            for core in result['instances'][0]['cores'].values():
                if core['observations']['commands']:
                    core['observations']['commands'].pop()
                    break
        elif mutation == 'output':
            address = json.loads((output / 'bundle.json').read_text())['observed_ranges']['output'][0]
            row, = [row for endpoint in result['memory_endpoints'] for row in endpoint['verifies'] if row['address'] == address]
            row['bytes_hex'] = bytes(64).hex()
        elif mutation == 'credit':
            next(row for row in result['credit_ledger'] if row['sent'])['returned'] -= 1
        elif mutation == 'core_resource':
            result['cores'][0]['live_commands'] = 1
        elif mutation == 'phase_resource':
            next(iter(result['instances'][0]['cores'].values()))['resources']['live_dma_tags'] = 1
        elif mutation == 'endpoint':
            result['memory_endpoints'][0]['idle'] = False
        elif mutation == 'network_pending':
            result['garnet']['ni_queued_flits'] = 1
        else:
            result['cores'].pop()
        result_path.write_text(json.dumps(result))
        executed = json.loads((output / 'run.json').read_text())
        executed['artifacts']['mesh_result.json'] = hashlib.sha256(result_path.read_bytes()).hexdigest()
        (output / 'run.json').write_text(json.dumps(executed))
        with pytest.raises((MeshIrError, ReconciliationError)):
            check(output)
        assert json.loads((output / 'check.json').read_text())['check'] == 'FAIL'


def test_prefill_smoke_rejects_changed_inputs_before_launch(smoke_bundle):
    from mesh_ir.prefill_smoke import run
    output, _ = smoke_bundle
    (output / 'input.bin').write_bytes(bytes(192))
    with pytest.raises(ValueError, match='artifact digest'):
        run(output, GEM5, sim_tick_limit=10000000000)
    assert not (output / 'runtime').exists()


@pytest.mark.parametrize('name', ['compilation-manifest.json', 'compile_report.json'])
def test_prefill_bundle_requires_compiler_provenance(smoke_bundle, name):
    from mesh_ir.prefill_smoke import load_bundle
    output, _ = smoke_bundle
    document = json.loads((output / 'bundle.json').read_text())
    document['artifacts'].pop(name)
    (output / name).unlink()
    (output / 'bundle.json').write_text(json.dumps(document))
    with pytest.raises(ValueError, match='compilation provenance'):
        load_bundle(output)


def test_prefill_smoke_check_rejects_unexecuted_bundle(smoke_bundle):
    from mesh_ir.prefill_smoke import check
    output, _ = smoke_bundle
    with pytest.raises(FileNotFoundError):
        check(output)
    assert json.loads((output / 'check.json').read_text())['check'] == 'FAIL'


def test_prefill_smoke_failed_child_is_not_a_success(smoke_bundle):
    import subprocess
    from pathlib import Path
    from mesh_ir.prefill_smoke import run
    output, _ = smoke_bundle
    with pytest.raises(subprocess.CalledProcessError):
        run(output, Path('/bin/false'), sim_tick_limit=10000000000)
    assert json.loads((output / 'run.json').read_text())['run'] == 'FAIL'


@pytest.mark.parametrize('mutation', ['workspace_overlap', 'output_capacity'])
def test_prefill_smoke_invalid_resource_plan_is_not_prepared(payload_carrier, compiled_publication, tmp_path, mutation):
    import yaml
    from mesh_ir.agent_workload import PlanError
    from mesh_ir.prefill_smoke import prepare
    manifest, program, arch, *_, arch_path = payload_carrier
    config = yaml.safe_load((FIXTURES / 'serving_runtime_config.yaml').read_text())
    addresses = config['serving']['address_map']
    if mutation == 'workspace_overlap':
        addresses['workspace_arena'] = dict(base=arch.regions[0].base, bytes=4096)
    else:
        addresses['output_arena']['bytes'] = 32
    config_path = tmp_path / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config))
    output = tmp_path / 'rejected'
    with pytest.raises((ValueError, PlanError)):
        prepare(compiled_publication, arch_path, manifest, config_path, FIXTURES / 'serving_workload_plan.json',
            output, input_tokens=2, publish_chunk_bytes=32)
    assert json.loads((output / 'bundle.json').read_text())['prepare'] == 'FAIL'
    assert not (output / 'program.mshb').exists()


@pytest.mark.parametrize('field', ['export_sha256', 'export_semantic_sha256'])
def test_prefill_rejects_payload_from_a_different_export(payload_carrier, compiled_publication, tmp_path, field):
    from mesh_ir.diagnostics import MeshIrError
    from mesh_ir.prefill_smoke import prepare
    manifest, _, _, *_, arch_path = payload_carrier
    document = json.loads(manifest.read_text())
    document[field] = '0' * 64
    (tmp_path / document['payload_file']).write_bytes((manifest.parent / document['payload_file']).read_bytes())
    wrong = tmp_path / 'payload.json'
    wrong.write_text(json.dumps(document))
    output = tmp_path / 'rejected'
    with pytest.raises(MeshIrError, match='payload export identity'):
        prepare(compiled_publication, arch_path, wrong, FIXTURES / 'serving_runtime_config.yaml',
            FIXTURES / 'serving_workload_plan.json', output, input_tokens=2, publish_chunk_bytes=32)
    assert json.loads((output / 'bundle.json').read_text())['prepare'] == 'FAIL'
    assert not (output / 'program.mshb').exists()


def test_prefill_statistics_use_each_admitted_phase(smoke_bundle):
    from copy import deepcopy
    from mesh_ir.architecture import load_arch
    from mesh_ir.execution_view import build_execution_views
    from mesh_ir.prefill_smoke import run
    from mesh_ir.runtime_errors import ReconciliationError
    from mesh_ir.workload_statistics import summarize_workload
    output, _ = smoke_bundle
    run(output, GEM5, sim_tick_limit=10000000000)
    result = json.loads((output / 'runtime/mesh_result.json').read_text())
    program = decode_program((output / 'program.mshb').read_bytes())
    sections = program.canonical_dict()['sections']
    views = build_execution_views(program).views
    report = summarize_workload(sections, result, arch=load_arch(output / 'arch.yaml'),
        program_dir=output, execution_views=views)
    assert all(row['matches'] for row in report['compute_prediction'].values())
    for frame, view in zip(report['instances'], views):
        assert {row['core_id'] for row in frame['cores'] if row['issued']} == view.core_ids
        assert sum(row['issued'] for row in frame['cores']) == len(view.command_ids)
        assert {row['core_id']: row['command_rom_entries'] for row in frame['cores']} == {
            core: view.command_counts.get(core, 0) for core in load_arch(output / 'arch.yaml').core_ids}
        assert all(row['command_rom_entries'] <= row['command_rom_capacity_entries'] for row in frame['cores'])
    publish = report['instances'][1]
    inactive, = [row for row in publish['cores'] if not row['issued']]
    assert inactive['begin_tick'] is inactive['end_tick'] is None
    assert inactive['elapsed_ticks'] == 0
    damaged = deepcopy(result)
    source = result['instances'][0]['cores'][str(inactive['core_id'])]['observations']['commands'][0]
    damaged['instances'][1]['cores'][str(inactive['core_id'])]['observations']['commands'].append(source)
    with pytest.raises(ReconciliationError, match='phase'):
        summarize_workload(sections, damaged, execution_views=views)


def test_prefill_deployment_audits_instantiated_hardware(smoke_bundle):
    from copy import deepcopy
    from mesh_ir.architecture import load_arch
    from mesh_ir.prefill_smoke import run
    from mesh_ir.runtime_errors import ReconciliationError
    from mesh_ir.serving_deployment_audit import verify_serving_deployment
    import yaml
    output, _ = smoke_bundle
    run(output, GEM5, sim_tick_limit=10000000000)
    arch = load_arch(output / 'arch.yaml')
    config = yaml.safe_load((output / 'config.yaml').read_text())
    actual = json.loads((output / 'runtime/config.json').read_text())
    scenario = json.loads((output / 'runtime/gate6_scenario.json').read_text())
    report = verify_serving_deployment(arch, config, actual, scenario, ticks_per_second=10**12)
    assert report['core_ids'] == list(arch.core_ids)
    assert report['routers'] == arch.fabric.network.router_rows * arch.fabric.network.router_cols
    assert report['axi_data_bytes'] == arch.axi_data_bytes
    for mutation in ('core', 'sram', 'rom', 'dtype_rate', 'lane', 'router', 'owner_range', 'driver', 'frontend', 'control', 'endpoint'):
        changed = deepcopy(actual)
        system = changed['system']
        if mutation == 'core':
            del system['mesh_core_0']
        elif mutation == 'sram':
            system['mesh_core_0']['sram_bytes'] *= 2
        elif mutation == 'rom':
            system['mesh_loader']['command_rom_entries'] += 1
        elif mutation == 'dtype_rate':
            system['mesh_core_0']['tensor_macs_by_dtype'][1] += 1
        elif mutation == 'lane':
            system['ruby']['network']['routers'][0]['dual_lane'] = True
        elif mutation == 'router':
            system['ruby']['network']['routers'].pop()
        elif mutation == 'owner_range':
            system['ruby']['axi_target_adapter0']['memory_range_ends'][0] -= arch.axi_data_bytes
        elif mutation == 'driver':
            system['gate6_driver']['master'] = system['mesh_bridge_0']['adapter']
        elif mutation == 'frontend':
            system['gate6_frontend']['master'] = system['gate6_driver']['master']
        elif mutation == 'control':
            system['gate6_frontend']['control_target'] = system['gate6_driver']['target']
        else:
            system['mesh_endpoint_100']['adapter'] = system['mesh_endpoint_101']['adapter']
        with pytest.raises(ReconciliationError):
            verify_serving_deployment(arch, config, changed, scenario, ticks_per_second=10**12)
    changed = deepcopy(scenario)
    changed['endpoint_to_router']['targets'][0]['router_id'] += 1
    with pytest.raises(ReconciliationError):
        verify_serving_deployment(arch, config, actual, changed, ticks_per_second=10**12)


@pytest.mark.parametrize('payload_carrier', [False, True], indirect=True, ids=['adapter', 'hbm-backend'])
def test_prefill_memory_traffic_reconciles_physical_owners(smoke_bundle):
    from copy import deepcopy
    from mesh_ir.architecture import load_arch
    from mesh_ir.gate3_oracle import read_facts_document
    from mesh_ir.prefill_smoke import run
    from mesh_ir.runtime_errors import ReconciliationError
    from mesh_ir.serving_memory_audit import verify_serving_memory_traffic
    output, _ = smoke_bundle
    run(output, GEM5, sim_tick_limit=10000000000)
    arch = load_arch(output / 'arch.yaml')
    result = json.loads((output / 'runtime/mesh_result.json').read_text())
    before = json.loads((output / 'runtime/fabric_initial.json').read_text())
    after = json.loads((output / 'runtime/fabric_final.json').read_text())
    config = json.loads((output / 'runtime/config.json').read_text())
    facts = read_facts_document(output / 'runtime/gate6_facts.tsv')
    report = verify_serving_memory_traffic(arch, config, result, facts, before, after, ticks_per_second=10**12)
    assert {row['node'] for row in report['hbm']} == {100, 101}
    assert all(row['read_useful_bytes'] > 0 for row in report['hbm'])
    assert sum(row['write_useful_bytes'] for row in report['hbm']) == 64
    for mutation in ('read_count', 'owner', 'queue', 'network'):
        changed = deepcopy(after)
        if mutation == 'network':
            changed['network_counters']['wire_bytes_received'][0] += 1
        else:
            row = next(row for row in changed['targets'] if row['read_committed_bytes'])
            if mutation == 'read_count':
                row['read_committed_bytes'] += arch.axi_data_bytes
            elif mutation == 'owner':
                row['name'] = 'missing.target'
            else:
                row['read_services'] = 1
        with pytest.raises(ReconciliationError):
            verify_serving_memory_traffic(arch, config, result, facts, before, changed, ticks_per_second=10**12)
    for mutation in ('control_read', 'core_burst'):
        changed_facts, changed_result = deepcopy(facts), deepcopy(result)
        if mutation == 'control_read':
            event = next(row for row in changed_facts.events if row['kind'] == 'AXI_ACCEPT' and row['channel'] == 'R')
            changed_facts.events.remove(event)
        else:
            changed_result['instances'][0]['bursts'].append(deepcopy(result['instances'][0]['bursts'][0]))
        with pytest.raises(ReconciliationError):
            verify_serving_memory_traffic(arch, config, changed_result, changed_facts, before, after, ticks_per_second=10**12)
    for target in after['targets']:
        if target['synthetic_backend'] is None:
            continue
        for field in ('read_bytes_serviced', 'write_bytes_serviced', 'retired', 'queued', 'ready', 'high_water'):
            changed = deepcopy(after)
            row = next(row for row in changed['targets'] if row['name'] == target['name'])
            row['synthetic_backend'][field] += 1000
            with pytest.raises(ReconciliationError):
                verify_serving_memory_traffic(arch, config, result, facts, before, changed, ticks_per_second=10**12)

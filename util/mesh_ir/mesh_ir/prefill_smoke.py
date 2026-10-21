import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time

import yaml

from mesh_ir.analysis.execution import AnalysisExecution
from mesh_ir.abi import decode_program, encode_program
from mesh_ir.agent_config import build_capacity_plan, load_agent_runtime_config, release_policy_of, validate_runtime_config
from mesh_ir.agent_plan_image import build_agent_plan_image
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.architecture import load_arch
from mesh_ir.binding_layout import balanced_bindings
from mesh_ir.canonical import JSON_SAFE_INTEGER_MAX
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.execution_view import build_execution_views
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.gate4_oracle import arena_regions
from mesh_ir.host_bindings import build_host_binding_plans
from mesh_ir.ir.common import MemorySpace, TensorRole
from mesh_ir.publication import CompiledPublication
from mesh_ir.runtime_deployment import FabricDeployment
from mesh_ir.runtime_drain import verified_core_drain, verified_drain
from mesh_ir.runtime_environment import embedded_python_path
from mesh_ir.runtime_errors import require
from mesh_ir.runtime_invocation import invocation_document
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.composition import VariantComposition, compose_program, verify_composition_sources
from mesh_ir.scheduled.model import ExternalStorageAdaptation
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_composition import attach_prefill_request, build_publication_program
from mesh_ir.serving_deployment_audit import verify_serving_deployment
from mesh_ir.serving_e2e_oracle import measure_request_latency, verify_serving_run
from mesh_ir.serving_memory_audit import verify_serving_memory_traffic
from mesh_ir.serving_profiles import serving_capacity_required
from mesh_ir.tensor_payload import TensorPayload
from mesh_ir.workload_statistics import summarize_workload


REPO = Path(__file__).resolve().parents[3]


def file_sha256(path: str | Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_document(path: str | Path, document: dict) -> None:
    Path(path).write_text(json.dumps(document, sort_keys=True, indent=2) + '\n')


def load_bundle(directory: str | Path) -> tuple[Path, dict]:
    directory = Path(directory).resolve()
    document = json.loads((directory / 'bundle.json').read_text())
    if document['prepare'] != 'PASS':
        raise ValueError('bundle preparation has not passed')
    if not {'compilation-manifest.json', 'compile_report.json'} <= document['artifacts'].keys():
        raise ValueError('bundle compilation provenance is incomplete')
    for name, digest in document['artifacts'].items():
        if Path(name).name != name or file_sha256(directory / name) != digest:
            raise ValueError(f'bundle artifact digest differs: {name}')
    return directory, document


def prepare(compiled_path: str | Path, arch_path: str | Path, payload_path: str | Path,
            config_path: str | Path, workload_path: str | Path, output: str | Path, *,
            input_tokens: int, publish_chunk_bytes: int, workers: int = 1) -> dict:
    execution = AnalysisExecution(workers)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    state = dict(prepare='RUNNING')
    write_document(output / 'bundle.json', state)
    try:
        with execution:
            with execution.stage('LoadSource'):
                arch = load_arch(arch_path)
                compilation = CompiledPublication.load(compiled_path, arch)
                compiled = compilation.program
                source, = compiled.semantics.variants
                identity, = compilation.sources
            with execution.stage('LoadPayload'):
                payload = TensorPayload.load(payload_path)
                if (payload.export_sha256 != identity.archive_sha256 or
                        payload.export_semantic_sha256 != identity.archive_semantic_sha256):
                    raise MeshIrError('E_MEMORY_IMAGE', 'payload export identity differs from the compiled source')
            with execution.stage('BuildPublication'):
                bindings = ProgramBindingCatalog(compiled).for_variant(source.entrypoint_id, source.profile_id)
                result, = [row for row in bindings.values() if row.tensor.role is TensorRole.OUTPUT]
                publication = build_publication_program(arch, result, staging_bytes=publish_chunk_bytes)
                adaptations = tuple(ExternalStorageAdaptation(row.object.object_id - source.membership.objects.first_id + 1,
                    MemorySpace.HBM, MemorySpace.HOST_SHARED) for row in bindings.values() if row.tensor.role is TensorRole.INPUT)
            with execution.stage('ComposeProgram'):
                composed = compose_program((VariantComposition(compiled, source.variant_id, 'prefill', 'static', adaptations),
                    VariantComposition(publication, 1, 'publish', 'static')), arch)
            with execution.stage('BindModelPayload'):
                slots = tuple(row.slot for row in ProgramBindingCatalog(composed).for_variant(1, 1).values())
                layout = balanced_bindings(slots, arch)
                bound = payload.bind(composed, arch, entrypoint_id=1, profile_id=1, bindings=layout)
            with execution.stage('AttachRequest'):
                program = attach_prefill_request(composed, arch, bound.inputs,
                    input_tokens=input_tokens, publish_chunk_bytes=publish_chunk_bytes)
                verify_composition_sources(program, (compiled, publication))
            with execution.stage('BindRequestPayload'):
                bound = payload.bind(program, arch, entrypoint_id=1, profile_id=1, bindings=layout)
            with execution.stage('ConfigureRuntime'):
                request, = program.agent_request_profiles
                config = yaml.safe_load(Path(config_path).read_text())
                workload = json.loads(Path(workload_path).read_text())
                user, = workload['users']
                task, = user['tasks']
                round_, = task['rounds']
                if (config['runtime']['mode'] != 'FULL_TIMING' or not config['runtime']['require_global_drain'] or
                        config['agent']['mode'] != 'replay_plan' or config['agent']['control_plan'] is not None or
                        config['serving']['batch_replay_artifact'] is not None or workload['max_repair_rounds']):
                    raise ValueError('prefill smoke requires one FULL_TIMING request with global drain and no control or replay plan')
                task.update(kv_handle=0, initial_kv_generation=0)
                round_.update(prompt_tokens=input_tokens, prompt_bytes=request.input_binding_bytes,
                    full_context_tokens=input_tokens, full_context_bytes=request.input_binding_bytes,
                    expected_cached_tokens=0, expected_cached_context_bytes=0, output_tokens=0,
                    generated_code_bytes=request.host_output_bytes, output_capacity_bytes=request.host_output_bytes,
                    kv_required_tokens_after_round=0, requested_profile_key=(request.requested_profile_key
                        if request.requested_profile_key <= JSON_SAFE_INTEGER_MAX else f'0x{request.requested_profile_key:016x}'),
                    input_payload_file='input.bin', input_content_digest=hashlib.sha256(bound.input_bytes).hexdigest())
                round_['compile']['local_io']['read_bytes'] = max(round_['compile']['local_io']['read_bytes'], request.host_output_bytes)
                config['agent']['workload_plan'] = 'workload.json'
                config['serving'].update(serving_capacity_required(program).fields(), output_chunk_bytes=publish_chunk_bytes)
                addresses = config['serving']['address_map']
                alignment = max(addresses['arena_alignment_bytes'], arch.axi_data_bytes)
                admitted = ProgramBindingCatalog(program).for_variant(1, 1)
                references = {row.slot_id: row for row in bound.invocation.bindings}
                immutable = tuple((arch.regions[reference.region_id].base + reference.allocation_offset_bytes,
                    reference.allocation_size_bytes) for row in admitted.values() if row.is_immutable_model_content
                    for reference in (references[row.slot.slot_id],))
                immutable_end = max((address + size for address, size in immutable),
                    default=next(row.base for row in arch.regions if row.kind == 'HBM'))
                if 'workspace_arena' not in addresses:
                    addresses['workspace_arena'] = dict(base=(immutable_end + alignment - 1) // alignment * alignment,
                        bytes=(request.host_output_bytes + alignment - 1) // alignment * alignment)
                workspace = addresses['workspace_arena']
                memory = FabricDeployment.from_arch(arch).memory_map
                memory.partition(((workspace['base'], workspace['bytes']),))
                if not any(row.kind == 'HBM' and row.base <= workspace['base'] and
                        workspace['base'] + workspace['bytes'] <= row.base + row.bytes for row in arch.regions):
                    raise ValueError('workspace must be covered by HBM')
                for address, size in immutable:
                    if max(workspace['base'], address) < min(workspace['base'] + workspace['bytes'], address + size):
                        raise ValueError('workspace overlaps immutable model content')
                (output / 'input.bin').write_bytes(bound.input_bytes)
                (output / 'arch.yaml').write_bytes(Path(arch_path).read_bytes())
                (output / 'config.yaml').write_text(yaml.safe_dump(config))
                write_document(output / 'workload.json', workload)
            with execution.stage('HostPlan'):
                effective = load_agent_runtime_config(output / 'config.yaml')
                planned = load_workload_plan(output / 'workload.json')
                validate_runtime_config(effective, planned)
                build_capacity_plan(planned, None, effective)
                binding_plans = build_host_binding_plans(program, [row.base for row in arch.regions],
                    invocations=(bound.invocation,))
                build_agent_plan_image(planned, None, release_policy_of(effective), arena_regions(addresses), None, binding_plans)
            with execution.stage('EncodeArtifacts'):
                for name, value in (('source', compiled), ('publication', publication), ('program', program)):
                    (output / f'{name}.mshb').write_bytes(encode_program(value))
            with execution.stage('WriteMetadata'):
                write_document(output / 'schedule.mesh.json', program.canonical_dict())
                write_document(output / 'memory-image.json', bound.memory_image)
                write_document(output / 'model-invocation.json', invocation_document(bound.invocation))
                (output / 'compilation-manifest.json').write_bytes(compilation.manifest_bytes)
                (output / 'compile_report.json').write_bytes(compilation.report_bytes)
                observed = {name: [addresses[name + '_arena']['base'], size] for name, size in
                    (('input', len(bound.input_bytes)), ('workspace', request.host_output_bytes), ('output', request.host_output_bytes))}
                memory.partition(observed.values())
            with execution.stage('PublishBundle'):
                state.update(prepare='PASS', program_semantic_sha256=program.semantic_sha256,
                    source_program_semantic_sha256=compiled.semantic_sha256, architecture_sha256=arch.digest().hex(),
                    payload_manifest_sha256=payload.manifest_sha256, payload_export_sha256=payload.export_sha256,
                    payload_owner_bytes=bound.owner_bytes, observed_ranges=observed,
                    inputs=dict(compilation_manifest=dict(path=str((Path(compiled_path) / 'manifest.json').resolve()),
                        sha256=hashlib.sha256(compilation.manifest_bytes).hexdigest()),
                        **{name: dict(path=str(Path(path).resolve()), sha256=file_sha256(path)) for name, path in
                            (('architecture', arch_path), ('payload', payload_path),
                             ('runtime_config', config_path), ('workload', workload_path))}),
                    artifacts={path.name: file_sha256(path) for path in sorted(output.iterdir()) if path.name != 'bundle.json'})
    except Exception as error:
        state.update(prepare='FAIL', error=str(error))
        raise
    finally:
        state['analysis'] = execution.canonical_dict()
        write_document(output / 'bundle.json', state)
    return state


def run(directory: str | Path, gem5: str | Path, *, sim_tick_limit: int) -> dict:
    if type(sim_tick_limit) is not int or sim_tick_limit <= 0:
        raise ValueError('simulation tick limit must be positive')
    directory, bundle = load_bundle(directory)
    gem5 = Path(gem5).resolve()
    runtime = directory / 'runtime'
    runtime.mkdir(exist_ok=False)
    entrypoint = REPO / 'configs/example/ai_mesh/run_gate6_serving.py'
    argv = [str(gem5), '--outdir=' + str(runtime), str(entrypoint),
        '--arch', str(directory / 'arch.yaml'), '--serving-program', str(directory / 'program.mshb'),
        '--runtime-config', str(directory / 'config.yaml'), '--memory-file-image', str(directory / 'memory-image.json'),
        '--model-invocation', str(directory / 'model-invocation.json'),
        '--fabric-snapshots',
        '--weight-image-digest', bundle['payload_manifest_sha256'], '--sim-tick-limit', str(sim_tick_limit),
        '--memory-verify-bytes-limit', str(max(size for _, size in bundle['observed_ranges'].values()))]
    for address, size in bundle['observed_ranges'].values():
        argv.extend(['--verify-memory-range', str(address), str(size)])
    env = {name: value for name, value in os.environ.items() if not name.startswith(('AI_MESH_', 'GATE6_'))}
    env['PYTHONPATH'] = embedded_python_path()
    state = dict(run='RUNNING', argv=argv, gem5_sha256=file_sha256(gem5),
        entrypoint_sha256=file_sha256(entrypoint), bundle_sha256=file_sha256(directory / 'bundle.json'))
    write_document(directory / 'run.json', state)
    started = time.monotonic()
    try:
        with (runtime / 'console.log').open('w') as stream:
            result = subprocess.run(argv, cwd=REPO, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        state.update(run='PASS', returncode=result.returncode,
            artifacts={name: file_sha256(runtime / name) for name in
                ('mesh_result.json', 'gate6_facts.tsv', 'config.ini', 'config.json', 'gate6_scenario.json', 'stats.txt',
                 'fabric_initial.json', 'fabric_final.json')})
    except Exception as error:
        state.update(run='FAIL', error=str(error))
        raise
    finally:
        state['wall_seconds'] = time.monotonic() - started
        write_document(directory / 'run.json', state)
    return state


def observed_bytes(result: dict, address: int, size: int) -> bytes:
    pieces = sorted((row for endpoint in result['memory_endpoints'] for row in endpoint['verifies']
        if address <= row['address'] < address + size), key=lambda row: row['address'])
    actual = bytearray()
    for row in pieces:
        require(row['address'] == address + len(actual), 'observed memory range has a gap or duplicate')
        data = bytes.fromhex(row['bytes_hex'])
        require(len(data) == row['size'], 'observed memory bytes were truncated')
        actual.extend(data)
    require(len(actual) == size, 'observed memory extent differs')
    return bytes(actual)


def check(directory: str | Path) -> dict:
    directory = Path(directory).resolve()
    report = dict(check='RUNNING')
    try:
        directory, bundle = load_bundle(directory)
        runtime = directory / 'runtime'
        execution = json.loads((directory / 'run.json').read_text())
        require(execution['run'] == 'PASS', 'simulation did not finish successfully')
        require(execution['bundle_sha256'] == file_sha256(directory / 'bundle.json'), 'executed bundle identity differs')
        require(set(execution['artifacts']) == {'mesh_result.json', 'gate6_facts.tsv', 'config.ini',
            'config.json', 'gate6_scenario.json', 'stats.txt', 'fabric_initial.json', 'fabric_final.json'},
            'runtime artifact set is incomplete')
        for name, digest in execution['artifacts'].items():
            require(Path(name).name == name and file_sha256(runtime / name) == digest, 'runtime artifact digest differs')
        program = decode_program((directory / 'program.mshb').read_bytes())
        arch = load_arch(directory / 'arch.yaml')
        verify_program(program, arch)
        facts = read_facts_document(runtime / 'gate6_facts.tsv')
        result = json.loads((runtime / 'mesh_result.json').read_text())
        require(facts.fatal is None and result['terminal'] == 'DONE', 'runtime did not reach healthy terminal')
        verify_serving_run(program, result['transport'], facts.metrics, facts.serving_phases)
        required = dict(completed_tasks=1, failed_tasks=0, infra_failed_tasks=0, frontend_drained=1,
            metadata_reads_validated=1, generated_code_reads_validated=1, kv_session_records=0,
            kv_session_tombstones=0, live_contexts=0, live_cq_obligations=0, live_sq_intakes=0,
            live_submissions=0, agent_live_objects=0, ack_wait_b=0, msi_rob_entries=0,
            driver_cq_consumer_seq=1, driver_cq_ack_seq=1, npu_cq_ack_seq=1)
        require(all(facts.metrics.get(name) == value for name, value in required.items()), 'Host or Serving did not drain')
        cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
        require(len(cq) == 1 and cq[0]['status'] == 'SUCCESS', 'request completion is not exactly one success')
        require(result['bridges'] and all(row['idle'] == 1 for row in result['bridges']), 'AXI bridges did not drain')
        views = build_execution_views(program).views
        require(len(result['instances']) == len(views), 'physical instance count differs')
        deployment = FabricDeployment.from_arch(arch)
        require(len(result['memory_endpoints']) == len(deployment.memory_nodes) and
            {row['node'] for row in result['memory_endpoints']} == set(deployment.memory_nodes),
            'physical memory drain coverage differs from the architecture')
        require(len(result['bridges']) == len(arch.core_ids) and
            {row['core_id'] for row in result['bridges']} == set(arch.core_ids),
            'physical bridge drain coverage differs from the architecture')
        verified_core_drain(result['cores'], result['instances'], arch.core_ids)
        verified_drain(result['garnet'], result['credit_ledger'], result['drain'], result['bridges'],
            [row for frame in result['instances'] for row in frame['apertures']], result['memory_endpoints'], len(views))
        for frame, view in zip(result['instances'], views):
            commands = [row for core in frame['cores'].values() for row in core['observations']['commands'] if row['issued']]
            require(len(commands) == len(view.command_ids) and {row['command_id'] for row in commands} == view.command_ids,
                'executed command closure differs')
            require(sum(core['completed'] for core in frame['cores'].values()) == len(view.command_ids) and
                all(core['errored'] == 0 for core in frame['cores'].values()), 'commands did not complete cleanly')
        observed = {name: observed_bytes(result, *span) for name, span in bundle['observed_ranges'].items()}
        require(observed['input'] == (directory / 'input.bin').read_bytes(), 'Host input differs from supplied payload')
        require(observed['workspace'] == observed['output'], 'Host output differs from computed workspace')
        statistics = summarize_workload(program.canonical_dict()['sections'], result, arch=arch,
            program_dir=directory, execution_views=views)
        require(all(row['matches'] for row in statistics['compute_prediction'].values()),
            'phase compute work differs from admitted cycles')
        active = sorted({row['core_id'] for frame in statistics['instances'] for row in frame['cores']
            if any(engine['work_cycles'] > 0 for engine in row['engines'].values())})
        command_active = sorted({row['core_id'] for frame in statistics['instances'] for row in frame['cores'] if row['issued']})
        zero_work = sorted({row['core_id'] for frame in statistics['instances'] for row in frame['cores']
            if row['core_id'] not in active and any(engine['commands'] for engine in row['engines'].values())})
        require(active, 'compiled prefill executed no nonzero compute work')
        write_document(directory / 'workload-report.json', statistics)
        frequencies = [int(fields[1]) for line in (runtime / 'stats.txt').read_text().splitlines()
            if (fields := line.split()) and fields[0] == 'simFreq']
        require(len(frequencies) == 1 and frequencies[0] == arch.clock_hz * result['core_clock_period_ticks'],
            'simulator tick frequency differs from the admitted core clock')
        topology = verify_serving_deployment(arch, yaml.safe_load((directory / 'config.yaml').read_text()),
            json.loads((runtime / 'config.json').read_text()), json.loads((runtime / 'gate6_scenario.json').read_text()),
            ticks_per_second=frequencies[0])
        memory = verify_serving_memory_traffic(arch, json.loads((runtime / 'config.json').read_text()), result, facts,
            json.loads((runtime / 'fabric_initial.json').read_text()), json.loads((runtime / 'fabric_final.json').read_text()),
            ticks_per_second=frequencies[0])
        write_document(directory / 'fabric-report.json', memory)
        latency = measure_request_latency(facts, statistics['run_window'], ticks_per_second=frequencies[0],
            process_wall_seconds=execution['wall_seconds'])
        report = dict(check='PASS', scope='compiled_prefill_driver', bundle_sha256=execution['bundle_sha256'],
            gem5_sha256=execution['gem5_sha256'], program_semantic_sha256=program.semantic_sha256,
            compute_active_cores=active, compute_idle_cores=[core for core in arch.core_ids if core not in active],
            command_active_cores=command_active, zero_work_compute_cores=zero_work,
            physical_cores=list(arch.core_ids), frontend_drained=facts.metrics['frontend_drained'],
            terminal_tick=result['terminal_tick'], input_bytes=len(observed['input']), output_bytes=len(observed['output']),
            output_sha256=hashlib.sha256(observed['output']).hexdigest(), payload_owner_bytes=bundle['payload_owner_bytes'],
            workload_report_sha256=file_sha256(directory / 'workload-report.json'),
            fabric_report_sha256=file_sha256(directory / 'fabric-report.json'), latency=latency, topology=topology)
        return report
    except Exception as error:
        report = dict(check='FAIL', error=str(error))
        raise
    finally:
        write_document(directory / 'check.json', report)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='stage', required=True)
    build = commands.add_parser('prepare')
    for name in ('compiled', 'arch', 'payload', 'runtime-config', 'workload', 'output'):
        build.add_argument('--' + name, type=Path, required=True)
    build.add_argument('--workers', type=int, default=1)
    build.add_argument('--input-tokens', type=int, required=True)
    build.add_argument('--publish-chunk-bytes', type=int, required=True)
    execute = commands.add_parser('run')
    execute.add_argument('--bundle', type=Path, required=True)
    execute.add_argument('--gem5', type=Path, required=True)
    execute.add_argument('--sim-tick-limit', type=int, required=True)
    inspect = commands.add_parser('check')
    inspect.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    if args.stage == 'prepare':
        result = prepare(args.compiled, args.arch, args.payload, args.runtime_config, args.workload, args.output,
            input_tokens=args.input_tokens, publish_chunk_bytes=args.publish_chunk_bytes, workers=args.workers)
    elif args.stage == 'run':
        result = run(args.bundle, args.gem5, sim_tick_limit=args.sim_tick_limit)
    else:
        result = check(args.bundle)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()

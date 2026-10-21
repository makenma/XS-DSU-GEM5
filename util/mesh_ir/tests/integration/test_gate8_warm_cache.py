import hashlib
import json

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import agent_abi as AG
from mesh_ir.host_bindings import build_host_binding_plans
from mesh_ir.model import canonical_json_bytes
from mesh_ir.weight_registry import (
    weight_region_order, load_model_weight_image, load_program_weight_registry, weight_tag_manifest,
)

from .serving_runtime import run_serving_fixture, write_shared_fixture
from .test_gate6_batch_replay import install_replay


def write_weight_artifacts(work):
    program = decode_program((work / "program.mshb").read_bytes())
    from mesh_ir.architecture import load_arch
    manifest = load_arch(work / 'arch.yaml')
    plans = build_host_binding_plans(program, [region.base for region in manifest.regions])
    requirements = {row.symbol_id: row for plan in plans for row in plan.requirements
                    if row.kind == AG.BINDING_KIND.WEIGHT_EXTERNAL}
    base = min(row.platform_address for row in requirements.values())
    homes, symbols = [], []
    for index, (symbol, row) in enumerate(sorted(requirements.items()), 1):
        seed = f'weight-{symbol}-nonzero'
        content = (seed.encode() * (row.required_bytes // len(seed) + 1))[:row.required_bytes]
        digest = hashlib.sha256(content).hexdigest()
        homes.append(dict(weight_home_id=index, arena_offset=row.platform_address-base,
            bytes=row.required_bytes, content_sha256=digest, read_only_alias_group=index,
            synthetic_fill={'seed_utf8': seed}))
        regions = [dict(region_offset=offset, bytes=size,
                       resolved_content_digest=hashlib.sha256(content[offset:offset+size]).hexdigest())
                   for sid, offset, size in weight_region_order(program) if sid == symbol]
        symbols.append(dict(symbol_id=symbol, weight_home_id=index, home_offset=0,
            bytes=row.required_bytes, flags=0, resolved_content_digest=digest,
            read_only_alias_group=index, regions=regions))
    image = dict(schema='model_weight_image_v1', version=1, arena_base=base,
        arena_bytes=max(row.platform_address+row.required_bytes-base for row in requirements.values()), homes=homes)
    image['model_weight_image_digest'] = hashlib.sha256(canonical_json_bytes(image)).hexdigest()
    registry = dict(schema='program_weight_bindings_v1', version=1,
        model_weight_image_digest=image['model_weight_image_digest'], programs=[dict(
            program_id=plans[0].program_id, program_semantic_digest=program.semantic_sha256, symbols=symbols)])
    registry['program_weight_registry_digest'] = hashlib.sha256(canonical_json_bytes(registry)).hexdigest()
    (work / 'weights.json').write_text(json.dumps(image))
    (work / 'registry.json').write_text(json.dumps(registry))
    return ('--model-weight-image', str(work / 'weights.json'), '--program-weight-registry',
            str(work / 'registry.json'), '--weight-image-digest', image['model_weight_image_digest'])


@pytest.mark.parametrize('mode', ['FULL_TIMING', 'FAST_EVENT', 'WINDOWED_TIMING'])
def test_external_warm_state_restores_real_weight_bytes_and_skips_fill(tmp_path, mode):
    program, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    install_replay(work, shared=False)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if mode in ('FAST_EVENT', 'WINDOWED_TIMING'):
        config['runtime']['fast_event'] = {'queue_entries': 1}
    if mode == 'WINDOWED_TIMING':
        config['runtime']['windows'] = dict(initial_mode='FAST_EVENT', switches=[
            dict(at_tick=1000010000, mode='FULL_TIMING')])
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    args = write_weight_artifacts(work)
    _, cold, _ = run_serving_fixture(work, 'cold', weight_policy='cached', extra_args=args)
    replay = work / 'cold' / 'cache_state_replay.json'
    assert replay.is_file()
    _, warm, metrics = run_serving_fixture(work, 'warm', weight_policy='cached',
        extra_args=args + ('--cache-state-replay', str(replay)))
    assert cold['terminal'] == warm['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 2
    assert len(cold['moe']['cache_fills']) == 1
    assert warm['moe']['cache_fills'] == []
    stores = []
    for result in (cold, warm):
        stores.append([(row['descriptor_id'], row['write_bytes'], row['payload_digest'])
                       for row in result['transport'] if row['write_bytes']])
    assert stores[0] and stores[0] == stores[1]
    loaded_program = decode_program((work / 'program.mshb').read_bytes())
    weight_image = load_model_weight_image(work / 'weights.json')
    registry = load_program_weight_registry(work / 'registry.json', weight_image, loaded_program)
    expected = {index: wire[-32:].hex() for index, wire in weight_tag_manifest(loaded_program, registry)}
    assert [row['valid_lines'] for row in cold['moe']['cache_state']] == [
        row['valid_lines'] for row in warm['moe']['cache_state']]
    for row in warm['moe']['cache_state']:
        for slot in row['valid_lines']:
            assert slot['content_sha256'] == expected[slot['weight_tag_index']]
            assert slot['content_sha256'] != hashlib.sha256(bytes(slot['valid_bytes'])).hexdigest()


@pytest.mark.parametrize('boundary', ['python', 'loader'])
@pytest.mark.parametrize('fault', ['digest', 'core', 'slots', 'tag', 'bytes', 'dirty', 'unknown', 'source', 'overflow'])
def test_warm_state_is_rejected_before_execution(tmp_path, boundary, fault):
    from mesh_ir.acceptance import ContractError
    from mesh_ir.agent_workload import load_workload_plan
    from mesh_ir.architecture import load_arch
    from mesh_ir.effective import EffectiveArchitecture
    from mesh_ir.model import MeshIrError
    from mesh_ir.moe_weight_cache import CacheReservationCoordinator
    from mesh_ir.weight_runtime import RuntimeWeightImage
    from .serving_runtime import PROBE

    program, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    args = write_weight_artifacts(work)
    image = RuntimeWeightImage.load(decode_program((work / "program.mshb").read_bytes()), EffectiveArchitecture(load_arch(work / 'arch.yaml')),
        work / 'weights.json', work / 'registry.json', args[-1], load_workload_plan(work / 'workload.json'))
    states = [CacheReservationCoordinator(core, image.slot_count, image.slot_bytes, tags,
        image.slot_count, image.slot_count, image.slot_count, image.slot_count * 2).snapshot()
        for core, tags in image.core_tags.items()]
    document = image.replay_document(states)
    slot = document['cores'][0]['slots'][0]
    slot.update(state=1, weight_tag_index=0, valid_bytes=128, lru_rank=0)
    if fault == 'overflow': document['cores'][0]['next_fill_incarnation'] = (1 << 32) - 1
    if fault == 'digest': document['model_weight_image_digest'] = '00' * 32
    if fault == 'core': document['cores'].pop()
    if fault == 'slots': document['cores'][0]['slots'].pop()
    if fault == 'tag': document['cores'][1]['slots'][0].update(slot)
    if fault == 'bytes': slot['valid_bytes'] = 64
    if fault == 'dirty': slot['state'] = 0
    if fault == 'unknown': slot['extra'] = 1
    if fault == 'source':
        slot.update(state=0, weight_tag_index=None, valid_bytes=0, lru_rank=None)
        document['cores'][0]['failure_tombstones'] = [dict(weight_tag_index=0,
            first_error=dict(tick=1, error_code=131073, source_key_wire='00' * 40))]
    if boundary == 'python':
        with pytest.raises((MeshIrError, ContractError)):
            image.validate_replay(document)
    else:
        entry = work / 'cache_loader_probe.py'
        entry.write_text('import runpy\nfrom m5.objects import MeshProgramLoader\n'
            'original = MeshProgramLoader.__init__\n'
            'def override(self, *args, **kwargs):\n'
            f'    kwargs["cache_state_replay"] = {json.dumps(document)!r}\n'
            '    original(self, *args, **kwargs)\n'
            'MeshProgramLoader.__init__ = override\n'
            f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
        run_serving_fixture(work, 'rejected', entry=entry, weight_policy='cached',
                            extra_args=args, rejection_code='E_MOE_CACHE')


@pytest.mark.parametrize('large_tick', [False, True])
def test_external_failure_tombstone_rejects_without_retrying_weight_dma(tmp_path, large_tick):
    program, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    args = write_weight_artifacts(work)
    _, failed, _ = run_serving_fixture(work, 'failed', weight_policy='cached',
        fault_kind='read', fault_index=0, delay_read_cycles=2000, extra_args=args)
    path = work / 'failed' / 'cache_state_replay.json'
    before = json.loads(path.read_text())
    assert failed['terminal'] == 'ERROR_DRAINED'
    assert sum(len(row['failure_tombstones']) for row in before['cores']) == 1
    if large_tick:
        before['cores'][0]['failure_tombstones'][0]['first_error']['tick'] = '0x0020000000000014'
        path.write_text(json.dumps(before))
    _, replayed, _ = run_serving_fixture(work, 'replayed_failure', weight_policy='cached',
        extra_args=args + ('--cache-state-replay', str(path)))
    assert replayed['terminal'] == 'ERROR_DRAINED'
    assert replayed['moe']['cache_fills'] == []
    after = json.loads((work / 'replayed_failure' / 'cache_state_replay.json').read_text())
    assert after == before
    first = before['cores'][0]['failure_tombstones'][0]['first_error']
    assert replayed['instances']
    assert all(row['failure']['detail'] == first['error_code'] and
               row['failure']['source_wire'] == first['source_key_wire']
               for row in replayed['instances'])


@pytest.mark.parametrize('fault', ['address', 'payload', 'program'])
def test_compiled_weight_projection_is_verified_by_loader(tmp_path, fault):
    from .serving_runtime import PROBE

    _, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    args = write_weight_artifacts(work)
    mutations = {
        'address': 'projection["symbols"][0]["address"] += 1',
        'payload': 'projection["symbols"][0]["data_hex"] = "00" + projection["symbols"][0]["data_hex"][2:]',
        'program': 'projection["program_semantic_digest"] = "00" * 32',
    }
    entry = work / 'weight_loader_probe.py'
    entry.write_text('import json\nimport runpy\nfrom m5.objects import MeshProgramLoader\n'
        'original = MeshProgramLoader.__init__\n'
        'def override(self, *args, **kwargs):\n'
        '    projection = json.loads(kwargs["weight_runtime_image"])\n'
        f'    {mutations[fault]}\n'
        '    kwargs["weight_runtime_image"] = json.dumps(projection)\n'
        '    original(self, *args, **kwargs)\n'
        'MeshProgramLoader.__init__ = override\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry, weight_policy='cached',
                        extra_args=args, rejection_code='E_MOE_CACHE')

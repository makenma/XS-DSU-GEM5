import json

import pytest
import yaml

from .serving_runtime import run_serving_fixture, write_shared_fixture
from .test_gate6_batch_replay import install_replay
from .test_gate6_timing_ab import payloads


@pytest.mark.parametrize('external_weights', [False, True])
@pytest.mark.parametrize('mode', ['FULL_TIMING', 'FAST_EVENT', 'WINDOWED_TIMING'])
def test_native_checkpoint_restores_future_host_work_and_physical_state(tmp_path, mode, external_weights):
    program, work = write_shared_fixture(tmp_path, moe=True)
    path = work / 'workload.json'
    workload = json.loads(path.read_text())
    workload['users'][1]['tasks'][0]['think_time_ns'] += 1000000
    path.write_text(json.dumps(workload))
    install_replay(work, shared=False)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if mode != 'FULL_TIMING':
        config['runtime']['fast_event'] = {'queue_entries': 1}
    if mode == 'WINDOWED_TIMING':
        config['runtime']['windows'] = dict(initial_mode='FULL_TIMING', switches=[
            dict(at_tick=0, mode='FAST_EVENT'), dict(at_tick=1500000000, mode='FULL_TIMING')])
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    from .test_gate8_warm_cache import write_weight_artifacts
    weight_args = list(write_weight_artifacts(work)) if external_weights else []
    if external_weights:
        from .test_gate6_host_pools import provider_artifact
        from mesh_ir.abi.decoder import decode_program
        program = decode_program((work / 'program.mshb').read_bytes())
        kind = 'route_replay'
        artifact = provider_artifact(work, program, workload, kind)
        weight_args += ['--moe-provider', kind, '--moe-provider-artifact', str(artifact)]
    checkpoint = work / 'checkpoint'
    _, continued, continued_metrics = run_serving_fixture(
        work, 'continued', weight_policy='cached', extra_args=weight_args + [
            '--checkpoint-dir', str(checkpoint), '--checkpoint-at-tick', '1000100000'])
    _, restored, metrics = run_serving_fixture(
        work, 'restored', weight_policy='cached', extra_args=weight_args + [
            '--restore-checkpoint', str(checkpoint)])
    assert restored['terminal'] == continued['terminal'] == 'DONE'
    assert restored['terminal_tick'] == continued['terminal_tick']
    assert metrics == continued_metrics
    assert payloads(restored) == payloads(continued)
    assert restored['burst_timings'] == continued['burst_timings']
    assert restored['moe']['cache_state'] == continued['moe']['cache_state']
    assert restored['bridges'] == continued['bridges']
    assert (work / 'restored' / 'gate6_facts.tsv').read_bytes() == (
        work / 'continued' / 'gate6_facts.tsv').read_bytes()
    for pattern in ('provider_selection_*.json', 'moe_instance_*_layer_*.bin'):
        expected_files = {path.name: path.read_bytes() for path in (work / 'continued').glob(pattern)}
        actual_files = {path.name: path.read_bytes() for path in (work / 'restored').glob(pattern)}
        assert actual_files == expected_files
    if mode == 'WINDOWED_TIMING':
        assert (work / 'restored' / 'timing_windows.json').read_bytes() == (
            work / 'continued' / 'timing_windows.json').read_bytes()
    manifest = json.loads((checkpoint / 'checkpoint.json').read_text())
    assert manifest['state']['tick'] >= 1000100000
    assert manifest['files']['m5.cpt']
    assert len(manifest['files']) > 10
    assert (work / 'restored' / 'gate6_facts.tsv.kv.json').read_bytes() == (
        work / 'continued' / 'gate6_facts.tsv.kv.json').read_bytes()
    if mode == 'FULL_TIMING' and not external_weights:
        run_serving_fixture(work, 'wrong_configuration', weight_policy='cached',
            extra_args=weight_args + ['--restore-checkpoint', str(checkpoint), '--watchdog-ticks', '12345'],
            rejection_code='E_CHECKPOINT_IDENTITY')
        state_path = checkpoint / 'system.gate6_driver.state'
        content = state_path.read_bytes()
        state_path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
        run_serving_fixture(work, 'corrupt_checkpoint', weight_policy='cached',
            extra_args=weight_args + ['--restore-checkpoint', str(checkpoint)],
            rejection_code='E_CHECKPOINT_DIGEST')
        from .serving_runtime import PROBE
        entry = work / 'native_checksum_probe.py'
        entry.write_text('import json, runpy\n'
            'from mesh_ir.serving_checkpoint import CheckpointBundle\n'
            'def unchecked(self):\n'
            '    state = json.loads((self.path / "checkpoint.json").read_text())["state"]\n'
            '    return state["tick"], state["runtime"]\n'
            'CheckpointBundle.read = unchecked\n'
            f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
        run_serving_fixture(work, 'native_corrupt_checkpoint', entry=entry, weight_policy='cached',
            extra_args=weight_args + ['--restore-checkpoint', str(checkpoint)],
            rejection_code='checkpoint state checksum mismatch')


@pytest.mark.parametrize('save_tick', [0, 1000100000])
def test_native_checkpoint_preserves_repair_and_release_lifecycle(tmp_path, save_tick):
    from .test_gate6_repair import repair_fixture
    repair_fixture(tmp_path)
    checkpoint = tmp_path / 'checkpoint'
    _, continued, expected = run_serving_fixture(tmp_path, 'continued', extra_args=[
        '--checkpoint-dir', str(checkpoint), '--checkpoint-at-tick', str(save_tick)])
    _, restored, actual = run_serving_fixture(tmp_path, 'restored', extra_args=[
        '--restore-checkpoint', str(checkpoint)])
    assert continued['terminal'] == restored['terminal'] == 'DONE'
    assert continued['terminal_tick'] == restored['terminal_tick']
    assert actual == expected
    assert actual['metadata_reads_validated'] == 5
    assert actual['kv_session_records'] == actual['host_release_waiters'] == 0
    assert payloads(continued) == payloads(restored)
    assert continued['burst_timings'] == restored['burst_timings']
    for name in ('gate6_facts.tsv', 'gate6_facts.tsv.kv.json'):
        assert (tmp_path / 'continued' / name).read_bytes() == (
            tmp_path / 'restored' / name).read_bytes()


def test_restored_process_can_save_a_new_checkpoint(tmp_path):
    from .test_gate6_repair import repair_fixture
    repair_fixture(tmp_path)
    first, second = tmp_path / 'first_checkpoint', tmp_path / 'second_checkpoint'
    run_serving_fixture(tmp_path, 'first', extra_args=[
        '--checkpoint-dir', str(first), '--checkpoint-at-tick', '0'])
    _, continued, expected = run_serving_fixture(tmp_path, 'second', extra_args=[
        '--restore-checkpoint', str(first), '--checkpoint-dir', str(second),
        '--checkpoint-at-tick', '1000100000'])
    _, restored, actual = run_serving_fixture(tmp_path, 'third', extra_args=[
        '--restore-checkpoint', str(second)])
    assert restored['terminal'] == continued['terminal'] == 'DONE'
    assert restored['terminal_tick'] == continued['terminal_tick']
    assert actual == expected
    assert restored['burst_timings'] == continued['burst_timings']
    for name in ('gate6_facts.tsv', 'gate6_facts.tsv.kv.json'):
        assert (tmp_path / 'second' / name).read_bytes() == (tmp_path / 'third' / name).read_bytes()

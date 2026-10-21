import json
from pathlib import Path

import pytest

from mesh_ir.serving_checkpoint import CheckpointBundle


def test_bundle_binds_identity_timeline_and_every_native_file(tmp_path):
    checkpoint = tmp_path / 'checkpoint'
    identity = {'binary': 'same-build', 'program': 'same-program'}
    state = {'switch_cursor': 1, 'windows': [{'mode': 'FAST_EVENT'}]}

    def save_native(path):
        directory = Path(path)
        directory.mkdir()
        (directory / 'm5.cpt').write_text('[Globals]\ncurTick=100\n')
        (directory / 'memory.state').write_bytes(bytes(range(256)))

    bundle = CheckpointBundle(checkpoint, identity)
    bundle.save(save_native, 100, state)
    assert bundle.read() == (100, state)
    with pytest.raises(ValueError, match='E_CHECKPOINT_IDENTITY'):
        CheckpointBundle(checkpoint, {'binary': 'different'}).read()
    (checkpoint / 'memory.state').write_bytes(b'changed')
    with pytest.raises(ValueError, match='E_CHECKPOINT_DIGEST'):
        bundle.read()


def test_failed_native_save_cannot_publish_a_checkpoint(tmp_path):
    bundle = CheckpointBundle(tmp_path / 'checkpoint', {})
    def fail(path):
        raise RuntimeError('native save failed')
    with pytest.raises(RuntimeError, match='native save failed'):
        bundle.save(fail, 10, {})
    assert not bundle.path.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('damage', ['manifest', 'missing', 'extra'])
def test_bundle_rejects_missing_added_and_modified_manifest_data(tmp_path, damage):
    bundle = CheckpointBundle(tmp_path / 'checkpoint', {})
    def save(path):
        Path(path).mkdir()
        (Path(path) / 'm5.cpt').write_text('native state')
    bundle.save(save, 10, {})
    manifest = bundle.path / 'checkpoint.json'
    if damage == 'manifest':
        document = json.loads(manifest.read_text())
        document['state']['tick'] = 11
        manifest.write_text(json.dumps(document))
    elif damage == 'missing':
        (bundle.path / 'm5.cpt').unlink()
    else:
        (bundle.path / 'unexpected.state').write_bytes(b'untracked')
    with pytest.raises(ValueError, match='E_CHECKPOINT_DIGEST'):
        bundle.read()


def test_timeline_checkpoint_keeps_future_switches_without_repeating_past_ones():
    from mesh_ir.serving_timeline import ServingTimeline
    class Runtime:
        tick = 0
        switches = []
        def simulate(self, duration):
            self.tick += duration
            return self
        def getCause(self):
            return 'simulate() limit reached'
        def requestProgramBoundary(self):
            pass
        def programBoundaryReady(self):
            return True
        def resumeProgramBoundary(self):
            pass
        def switchProgramTiming(self, aggregate):
            self.switches.append((self.tick, aggregate))
    plan = dict(initial_mode='FULL_TIMING', switches=[
        dict(at_tick=10, mode='FAST_EVENT'), dict(at_tick=30, mode='FULL_TIMING')])
    timeline = ServingTimeline('WINDOWED_TIMING', plan)
    runtime = Runtime()
    saved = []
    timeline.run(runtime.simulate, lambda: runtime.tick, runtime, 40, 1,
                 save_at=20, save=lambda tick, state: saved.append((tick, json.loads(json.dumps(state)))))
    assert runtime.switches == [(10, True), (30, False)]
    restored = ServingTimeline('WINDOWED_TIMING', plan)
    restored.restore(saved[0][1])
    runtime.tick = saved[0][0]
    runtime.switches = []
    restored.run(runtime.simulate, lambda: runtime.tick, runtime, 40, 1)
    assert runtime.switches == [(30, False)]
    assert restored.snapshot() == timeline.snapshot()


def test_identity_follows_input_contents_and_ignores_output_locations(tmp_path):
    from mesh_ir.serving_checkpoint import checkpoint_identity
    binary = tmp_path / 'gem5.opt'
    binary.write_bytes(b'executable')
    identities = []
    for directory in [tmp_path / 'save', tmp_path / 'restore']:
        directory.mkdir()
        plan = directory / 'plan.bin'
        plan.write_bytes(b'same plan')
        config = dict(type='Gate3ObservationRecorder', output_path=str(directory / 'facts.tsv'),
                      plan_image=str(plan), fs=str(directory / 'fs'))
        identities.append(checkpoint_identity(config, directory, binary, {}))
    assert identities[0] == identities[1]
    plan.write_bytes(b'changed plan')
    assert checkpoint_identity(config, directory, binary, {}) != identities[0]


def test_checkpoint_restores_owned_diagnostic_artifacts(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    evidence = original / 'moe_instance_7_layer_2.bin'
    evidence.write_bytes(bytes(range(256)))
    bundle = CheckpointBundle(tmp_path / 'checkpoint', {})
    def save(path):
        Path(path).mkdir()
        (Path(path) / 'm5.cpt').write_text('native')
    bundle.save(save, 10, {'provider_invocations': 3}, artifacts=[evidence])
    restored = tmp_path / 'restored'
    restored.mkdir()
    bundle.read()
    bundle.restore_artifacts(restored)
    assert (restored / evidence.name).read_bytes() == evidence.read_bytes()

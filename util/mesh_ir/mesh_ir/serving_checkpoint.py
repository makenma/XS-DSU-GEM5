import hashlib
import json
from pathlib import Path
import tempfile
import shutil


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class CheckpointBundle:
    def __init__(self, path, identity):
        self.path = Path(path).resolve()
        self.identity = identity

    def save(self, native_save, tick, runtime, artifacts=()):
        if self.path.exists():
            raise ValueError('E_CHECKPOINT_EXISTS: destination already exists')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.checkpoint-', dir=self.path.parent) as temporary:
            staging = Path(temporary) / 'state'
            native_save(str(staging))
            for artifact in artifacts:
                target = staging / "artifacts" / artifact.name
                target.parent.mkdir(exist_ok=True)
                if target.exists():
                    raise ValueError("E_CHECKPOINT_STATE: duplicate artifact name")
                shutil.copyfile(artifact, target)
            files = {str(path.relative_to(staging)): digest_file(path)
                     for path in sorted(staging.rglob('*')) if path.is_file()}
            if 'm5.cpt' not in files:
                raise ValueError('E_CHECKPOINT_STATE: native checkpoint is missing')
            document = dict(schema='serving_checkpoint_v1', identity=self.identity,
                            state=dict(tick=tick, runtime=runtime), files=files)
            (staging / 'checkpoint.json').write_text(json.dumps(
                dict(document, sha256=canonical_digest(document)), sort_keys=True, indent=2) + '\n')
            staging.rename(self.path)

    def read(self):
        document = json.loads((self.path / 'checkpoint.json').read_text())
        checksum = document.pop('sha256', None)
        if checksum != canonical_digest(document):
            raise ValueError('E_CHECKPOINT_DIGEST: manifest content differs')
        if document['schema'] != 'serving_checkpoint_v1':
            raise ValueError('E_CHECKPOINT_SCHEMA: unsupported checkpoint schema')
        if document['identity'] != self.identity:
            raise ValueError('E_CHECKPOINT_IDENTITY: binary, configuration or inputs differ')
        files = {str(path.relative_to(self.path)): digest_file(path)
                 for path in sorted(self.path.rglob('*'))
                 if path.is_file() and path != self.path / 'checkpoint.json'}
        if files != document['files'] or 'm5.cpt' not in files:
            raise ValueError('E_CHECKPOINT_DIGEST: native state files differ')
        return document['state']['tick'], document['state']['runtime']

    def restore_artifacts(self, output_directory):
        for artifact in (self.path / 'artifacts').glob('*'):
            shutil.copyfile(artifact, Path(output_directory) / artifact.name)


def checkpoint_identity(root_config, artifact_dir, binary, runtime, extra_inputs=()):
    outputs = {'Gate3ObservationRecorder': {'output_path'}, 'MeshDispatcher': {'result_json'}}
    def normalize(value):
        if isinstance(value, dict):
            excluded = outputs.get(value.get('type'), set())
            return {key: normalize(item) for key, item in value.items() if key not in excluded}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            path = Path(value)
            if path.is_absolute() and path.is_file():
                return {'sha256': digest_file(path)}
            return value.replace(str(Path(artifact_dir).resolve()), '<artifacts>')
        return value
    return dict(binary=digest_file(binary), configuration=normalize(root_config),
                runtime=runtime, inputs=[digest_file(path) for path in extra_inputs if path])

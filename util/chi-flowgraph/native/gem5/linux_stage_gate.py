"""Host-side gates for the fixed Linux guest suite; no guest commands execute here."""
import json
import hashlib
from pathlib import Path
import subprocess

STAGES = ['boot', 'litmus-smoke', 'litmus-large', 'torture', 'pthreads', 'cbo', 'uncached', 'complete']


class LinuxStageGate:
    def __init__(self, project, output, inputs=None):
        self.project, self.output = Path(project), Path(output)
        self.offset = 0
        self.results = []
        self.inputs = None
        if inputs is not None:
            index = Path(inputs).resolve()
            self.input_root = index.parent
            self.inputs = json.loads(index.read_text())
            if self.inputs.get('schemaVersion') != 1:
                raise RuntimeError('Unsupported suite input index')
            for entry in self.inputs['files']:
                path = self.input_root / entry['path']
                if not path.resolve().is_relative_to(self.input_root):
                    raise RuntimeError('Suite input escapes its snapshot')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                if digest.hexdigest() != entry['sha256']:
                    raise RuntimeError(f'Suite input changed: {path}')
            (self.output / 'suite-inputs-index.json').write_text(index.read_text())

    def accept(self, serial):
        index = len(self.results)
        if index >= len(STAGES):
            raise RuntimeError('Unexpected exit after completed Linux pipeline')
        stage = STAGES[index]
        segment = serial[self.offset:]
        lines = [line.strip() for line in segment.replace('\r', '').splitlines()]
        markers = [line for line in lines if line.startswith('CHI_LINUX_STAGE_DONE=')]
        if markers != ['CHI_LINUX_STAGE_DONE=' + stage]:
            raise RuntimeError(f'Expected exactly one stage marker for {stage}, got {markers}')
        log = self.output / (stage + '.serial.log')
        report = self.output / (stage + '.result.json')
        log.write_text(segment)
        p = self.project
        checks = {
            'litmus-smoke': ['check-linux-litmus.py', '--input', log, '--manifest',
                             p / 'build/linux-litmus-smoke/manifest.json', '--output', report],
            'litmus-large': ['check-linux-litmus.py', '--input', log, '--manifest',
                             p / 'build/linux-litmus-large/manifest.json', '--output', report],
            'torture': ['check-kernel-torture.py', log, '--output', report],
            'pthreads': ['check-linux-suite.py', '--input', log, '--manifest',
                         p / 'build/linux-tests/manifest.json', '--iterations', '128', '--output', report],
            'cbo': ['check-linux-cbo.py', '--log', log, '--manifest',
                    p / 'build/linux-cbo/manifest.json', '--phase', 'all', '--output', report],
            'uncached': ['check-linux-uncached.py', '--log', log, '--manifest',
                        p / 'build/linux-uncached/manifest.json', '--fs-config',
                        self.output / 'flowgraph-config.json', '--output', report],
        }
        if stage == 'boot':
            required = ['CHI_LINUX_ONLINE_CPUS=0-3', 'CHI_LINUX_ONLINE_NODES=0-1', 'CHI_LINUX_BOOT_PASS']
            if any(lines.count(marker) != 1 for marker in required):
                raise RuntimeError('Linux pipeline requires actual four-CPU, two-NUMA boot markers')
            report.write_text(json.dumps({'passed': True, 'stage': stage}) + '\n')
        elif stage == 'complete':
            if lines.count('CHI_LINUX_PIPELINE_COMPLETE') != 1:
                raise RuntimeError('Missing complete pipeline marker')
            report.write_text(json.dumps({'passed': True, 'stage': stage}) + '\n')
        else:
            command = checks[stage]
            checker = p / 'scripts' / command[0]
            if self.inputs is not None:
                frozen = self.inputs['stages'][stage]
                checker = self.input_root / frozen['checker']
                if '--manifest' in command:
                    command[command.index('--manifest') + 1] = self.input_root / frozen['manifest']
            result = subprocess.run(['python3', str(checker), *map(str, command[1:])],
                                    capture_output=True, text=True)
            (self.output / (stage + '.checker.log')).write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError(f'Linux stage {stage} checker failed; see {report}')
        self.offset = len(serial)
        self.results.append({'stage': stage, 'passed': True, 'serialFile': str(log), 'reportFile': str(report)})
        (self.output / 'stage-results.json').write_text(json.dumps(self.results, indent=2) + '\n')
        return stage == 'complete'

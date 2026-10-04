"""Run model10 launch prerequisites only; never launch full Linux acceptance.

Requires the completed 23-step focused run for the explicit candidate02 SHA.
Rechecks its actual SE/BPU artifacts against model05 and the independent oracle,
then executes exactly two 5e9-tick full-system comparisons. New output and
temporary files stay under the D-backed WSL run root; cwd remains the original
D-drive project so a changed working directory cannot alter model configuration.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
WORK = Path('/root/chi-flowgraph-work/linux-validation')
SOURCE = Path('/root/chi-flowgraph-work/gem5')
RUN_ROOT = WORK / 'runs'
RESULTS = PROJECT / 'build/linux-validation'
SCRIPTS = PROJECT / 'scripts'
BINARY = WORK / 'artifacts/fs-model-10-fetch-demand-fault-candidate02/gem5.opt'
MODEL05 = WORK / 'artifacts/fs-model-05-nonspec/gem5.opt'
MODEL07 = WORK / 'artifacts/fs-model-07-phase-oracle/gem5.opt'
MODEL05_SHA = '4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d'
MODEL07_SHA = '3c76d5e2688e604e296640e826783db2a685ba1cf08708795af489c0a70bcc07'
PROGRAMS = {
    'rtl': ('881aa515e8d919447f73086903eb89d0b219a26ead464b5eed024547bd9b9ed3',
            PROJECT / 'build/native-rtl-program'),
    'bpu-phase': ('227efd913f4a91942dc3e117edd4e79efb34f96b4ad952c0f64f2120607070e7',
                  PROJECT / 'build/native-bpu-phase-program'),
}
FIRMWARE = WORK / 'opensbi-suite/platform/generic/firmware/fw_payload.elf'
CONFIG = PROJECT / 'native/gem5/native_l2_fs.py'
SUITE_INPUTS = WORK / 'artifacts/linux-suite-inputs/76aa90c405b1a84843542040de750d9bcb9380205267bd87ffb1e0eb20a1baac/index.json'
FS_WRAPPER = RESULTS / 'run-bounded-fs-plan.py'
HELPER = PROJECT / 'build/qa/bpu-phase-storage-helper-report.json'
FETCH_CASES = (
    'same-layout', 'changed-layout', 'control-interior', 'cross-stale',
    'cross-clean', 'prefix-interior', 'amo-cross-stale', 'legal-taken',
    'page-tail-return', 'last-halfword-return', 'straddling-instruction-fault',
)
FOCUSED_LABELS = ({'fetch-' + case for case in FETCH_CASES}
                  | {'cbo-' + case for case in ('clean', 'flush', 'inval', 'flush-fault')}
                  | {'se-' + work + '-' + mode + suffix
                     for work in PROGRAMS for mode in ('on', 'off')
                     for suffix in ('', '-equivalence')})
SCOPE = ('Finite model10 launch prerequisites only: completed focused CPU regressions, '
         'model05 SE and independent BPU oracle equivalence, and bounded 5e9-tick '
         'real-FS equivalence. No Linux boot, suite, or full acceptance claim.')


def digest(path):
    before = path.stat()
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('input changed while hashing: ' + str(path))
    return value.hexdigest()


def read_json(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('duplicate JSON key in ' + str(path))
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=pairs)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def validate_focused(folder, expected_sha):
    state = read_json(folder / 'state.json')
    require(state.get('kind') == 'fetch-boundary-candidate-regressions'
            and state.get('complete') is True and state.get('includePageFaults') is True
            and state.get('linuxAcceptance') is False,
            'focused run must be complete and include all three page-fault cases')
    require(state.get('gem5Sha256') == expected_sha and state.get('gem5') == str(BINARY),
            'focused run belongs to a different candidate binary')
    steps = state.get('steps', [])
    require(len(steps) == 23 and {s.get('label') for s in steps} == FOCUSED_LABELS,
            'focused state must contain exactly the 23 distinct required steps')
    for step in steps:
        label = step['label']
        require(type(step.get('hostExitCode')) is int and step['hostExitCode'] == 0,
                'focused child did not exit successfully: ' + label)
        require(type(step.get('driverPid')) is int and step['driverPid'] > 0
                and step.get('startedAtUtc') and step.get('finishedAtUtc'),
                'focused step lacks actual process completion evidence: ' + label)
        command = step.get('command')
        require(isinstance(command, list) and len(command) > 2
                and all(isinstance(s, str) for s in command), 'bad focused command: ' + label)
        require((folder / (label + '.log')).stat().st_size > 0, 'missing focused log: ' + label)
        if not label.endswith('-equivalence'):
            require(command.count('--gem5') == 1
                    and command[command.index('--gem5') + 1] == str(BINARY),
                    'focused command executed a different model: ' + label)
    # Bind all four actual candidate SE runs independently of the outer state.
    for workload, (program_sha, _) in PROGRAMS.items():
        for mode in ('on', 'off'):
            run = folder / ('se-' + workload + '-' + mode)
            plan = read_json(run / 'execution-plan.json')
            require(plan.get('gem5Sha256') == expected_sha
                    and plan.get('programManifestSha256') == program_sha
                    and plan.get('trace') == mode and plan.get('command', [None])[0] == str(BINARY),
                    'candidate SE identity mismatch: ' + str(run))
            require((run / 'host-exit-code.txt').read_text().strip() == '0',
                    'candidate SE process did not actually finish with zero: ' + str(run))
    return state


def preflight(expected_sha, focused, output):
    require(os.name == 'posix' and PROJECT == Path('/mnt/d/proj/chi_flowgraph'),
            'run this driver in WSL against the D-drive project')
    require(re.fullmatch('[0-9a-f]{64}', expected_sha) is not None,
            '--gem5-sha256 must be an explicit lowercase SHA-256')
    require(output.is_absolute() and output.resolve() == output
            and output.is_relative_to(RUN_ROOT) and output != RUN_ROOT,
            '--output must be a nonsymlink directory beneath ' + str(RUN_ROOT))
    require(not output.exists(), 'refusing an existing output directory')
    require(focused.is_absolute() and focused.resolve(strict=True) == focused
            and focused.is_relative_to(RUN_ROOT), 'focused output must remain in the D-backed WSL run root')
    require(not output.is_relative_to(focused) and not focused.is_relative_to(output),
            'focused and new output directories must be separate')
    require(digest(BINARY) == expected_sha, 'candidate binary differs from explicit SHA')
    require(digest(MODEL05) == MODEL05_SHA, 'immutable model05 binary changed')
    require(digest(MODEL07) == MODEL07_SHA, 'immutable independent oracle binary changed')
    validate_focused(focused, expected_sha)
    files = [BINARY, MODEL05, MODEL07, focused / 'state.json', FIRMWARE, CONFIG, SUITE_INPUTS,
             CONFIG.parent / 'linux_stage_gate.py', FS_WRAPPER, HELPER, Path(__file__).resolve(),
             PROJECT / 'native/gem5/BpuPhaseSpool.hh']
    for script in ('check-native-model-regression.py', 'check-bpu-phase-storage-equivalence.py',
                   'check-bpu-phase-trace-invariance.py', 'check-native-fs-equivalence.py',
                   'check-chi-transfer-census.py', 'check-native-linux-validation.py'):
        files.append(SCRIPTS / script)
    for _, (expected, folder) in PROGRAMS.items():
        manifest = folder / 'manifest.json'
        require(digest(manifest) == expected, 'frozen CPU workload manifest changed: ' + str(manifest))
        files.append(manifest)
        for row in read_json(manifest)['binaries']:
            binary = folder / row['file']
            require(digest(binary) == row['sha256'], 'CPU workload ELF changed: ' + str(binary))
            files.append(binary)
    for mode in ('on', 'off'):
        legacy = RESULTS / ('se-nonspec-retry-' + mode) / 'executable.sha256'
        require(legacy.read_text().split()[0] == MODEL05_SHA, 'wrong model05 SE baseline: ' + str(legacy))
        files.append(legacy)
        for prefix, sha in (('se-bpu-phase-original-', MODEL05_SHA), ('se-bpu-phase-oracle-', MODEL07_SHA)):
            plan = RESULTS / (prefix + mode) / 'execution-plan.json'
            require(read_json(plan).get('gem5Sha256') == sha, 'wrong BPU baseline: ' + str(plan))
            files.append(plan)
    files.extend(sorted((SOURCE / 'configs').rglob('*.py')))
    return {str(path): digest(path) for path in files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5-sha256', required=True)
    parser.add_argument('--focused', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output, focused = args.output, args.focused
    state = None
    try:
        pins = preflight(args.gem5_sha256, focused, output)
        output.mkdir(parents=True, exist_ok=False)
        temporary = output / 'tmp'
        temporary.mkdir()
        environment = os.environ.copy()
        environment.update({'TMPDIR': str(temporary), 'TMP': str(temporary), 'TEMP': str(temporary),
                            'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
        for name in ('CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'):
            environment.pop(name, None)
        state = {'schemaVersion': 1, 'kind': 'model10-launch-regressions', 'gem5': str(BINARY),
                 'gem5Sha256': args.gem5_sha256, 'focused': str(focused), 'createdAtUtc': now(),
                 'inputSha256': pins, 'steps': [], 'complete': False, 'linuxAcceptance': False,
                 'scope': SCOPE, 'temporaryDirectory': str(temporary), 'cwd': str(PROJECT)}

        def save():
            atomic_json(output / 'state.json', state)

        def python(script, *arguments):
            return [sys.executable, str(SCRIPTS / script), *map(str, arguments)]

        def check_pins():
            for name, expected in pins.items():
                require(digest(Path(name)) == expected, 'pinned prerequisite changed: ' + name)
            current = {str(p) for p in (SOURCE / 'configs').rglob('*.py')}
            expected = {p for p in pins if p.startswith(str(SOURCE / 'configs') + '/')}
            require(current == expected, 'gem5 configuration Python inventory changed')

        def run(label, command, report=None, success_key=None):
            check_pins()
            entry = {'label': label, 'command': list(map(str, command)), 'startedAtUtc': now()}
            state['steps'].append(entry)
            save()
            print(json.dumps({'starting': label, 'linuxAcceptance': False}), flush=True)
            with (output / (label + '.log')).open('xb') as log:
                process = subprocess.Popen(entry['command'], stdout=log, stderr=subprocess.STDOUT,
                                           cwd=PROJECT, env=environment)
                entry['pid'] = process.pid
                save()
                entry['hostExitCode'] = process.wait()
            entry['finishedAtUtc'] = now()
            save()
            require(entry['hostExitCode'] == 0, label + ' actual child exit was ' + str(entry['hostExitCode']))
            if report:
                parsed = read_json(report)
                require(parsed.get(success_key) is True, label + ' did not produce a passing actual report')
                entry['report'] = str(report)
                entry['reportSha256'] = digest(report)
                save()
            print(json.dumps({'finished': label, 'hostExitCode': entry['hostExitCode']}), flush=True)

        save()
        regressions = {'se': {}, 'bpu': {}}
        program_manifest = PROGRAMS['bpu-phase'][1] / 'manifest.json'
        for mode in ('on', 'off'):
            before, after = RESULTS / ('se-nonspec-retry-' + mode), focused / ('se-rtl-' + mode)
            report = output / ('rtl-model05-' + mode + '.json')
            command = python('check-native-model-regression.py', '--before', before,
                             '--after', after, '--output', report)
            if mode == 'off':
                command.append('--without-trace')
            run('rtl-model05-' + mode, command, report, 'passed')
            regressions['se'][mode] = {'report': str(report), 'before': str(before), 'after': str(after)}
        for mode in ('on', 'off'):
            legacy = RESULTS / ('se-bpu-phase-original-' + mode)
            oracle = RESULTS / ('se-bpu-phase-oracle-' + mode)
            candidate = focused / ('se-bpu-phase-' + mode)
            report = output / ('bpu-equivalence-' + mode + '.json')
            run('bpu-equivalence-' + mode, python('check-bpu-phase-storage-equivalence.py',
                '--legacy', legacy, '--oracle', oracle, '--candidate', candidate,
                '--program-manifest', program_manifest, '--helper-report', HELPER,
                '--output', report), report, 'pass')
            regressions['bpu'][mode] = {'report': str(report), 'legacy': str(legacy),
                'oracle': str(oracle), 'candidate': str(candidate),
                'programManifest': str(program_manifest), 'helperReport': str(HELPER)}
        report = output / 'bpu-trace-invariance.json'
        on, off = [focused / ('se-bpu-phase-' + mode) for mode in ('on', 'off')]
        run('bpu-trace-invariance', python('check-bpu-phase-trace-invariance.py',
            '--on', on, '--off', off, '--program-manifest', program_manifest,
            '--output', report), report, 'pass')
        regressions['bpuTrace'] = {'report': str(report), 'on': str(on), 'off': str(off),
                                 'programManifest': str(program_manifest)}
        plan_path = output / 'fs-equivalence-inputs.json'
        run('fs-prepare', python('check-native-fs-equivalence.py', '--prepare', '--plan', plan_path,
            '--before', output / 'fs-before', '--after', output / 'fs-after',
            '--before-binary', MODEL05, '--after-binary', BINARY, '--firmware', FIRMWARE,
            '--config', CONFIG, '--gem5-source', SOURCE, '--suite-inputs', SUITE_INPUTS,
            '--ticks', '5000000000', '--progress-ticks', '1000000000'))
        plan = read_json(plan_path)
        require(plan.get('kind') == 'bounded-real-fs-equivalence-inputs'
                and plan.get('ticks') == '5000000000' and plan.get('progressTicks') == '1000000000'
                and plan.get('suitePipeline') is True
                and type(plan.get('expectedHostExitCode')) is int and plan['expectedHostExitCode'] == 2,
                'unexpected bounded FS plan or exit semantics')
        for index, label in enumerate(('before', 'after')):
            planned = plan['runs'][index]
            require(planned['label'] == label and planned['directory'] == str(output / ('fs-' + label))
                    and planned['binary'] == str(MODEL05 if label == 'before' else BINARY),
                    'bounded FS plan identity changed')
            run('fs-' + label, [sys.executable, str(FS_WRAPPER), '--plan', str(plan_path), '--label', label])
            actual_exit = (output / ('fs-' + label) / 'host-exit-code.txt').read_text().strip()
            require(actual_exit == '2', 'FS wrapper masked an unexpected actual simulator exit')
            state['steps'][-1]['simulatorHostExitCode'] = 2
            state['steps'][-1]['simulatorPid'] = int((output / ('fs-' + label) / 'host-pid.txt').read_text())
            state['steps'][-1]['boundedIncompleteExitExpected'] = True
            save()
        report = output / 'fs-equivalence.json'
        run('fs-equivalence', python('check-native-fs-equivalence.py', '--plan', plan_path,
                                    '--output', report), report, 'equivalent')
        regressions['fs'] = {'report': str(report), 'plan': str(plan_path)}
        check_pins()
        require(len(state['steps']) == 9, 'internal error: exactly nine prerequisite steps required')
        atomic_json(output / 'regressions.json', regressions)
        state['regressions'] = str(output / 'regressions.json')
        state['complete'] = True
        state['finishedAtUtc'] = now()
        save()
        print(json.dumps({'regressionsComplete': True, 'linuxAcceptance': False,
                          'output': str(output), 'scope': SCOPE}), flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError) as error:
        if state is not None:
            state['complete'] = False
            state['error'] = str(error)
            state['finishedAtUtc'] = now()
            atomic_json(output / 'state.json', state)
        print(json.dumps({'regressionsComplete': False, 'linuxAcceptance': False, 'error': str(error)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

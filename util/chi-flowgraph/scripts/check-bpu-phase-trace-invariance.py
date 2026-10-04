"""Read-only trace on/off comparison for an oracle or storage-candidate SE run.

The two runs must use the same model, program and configuration. Full per-core
phase exports and all archives are compared in their original record order.
This finite SE regression is not Linux workload acceptance.
"""
import argparse
import importlib.util
import json
from pathlib import Path

CHECKER_PATH = Path(__file__).with_name('check-bpu-phase-storage-equivalence.py')
SPEC = importlib.util.spec_from_file_location('phase_equivalence_reader', CHECKER_PATH)
CORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CORE)


def check(on, off, program_manifest):
    report = {'schemaVersion': 1, 'status': 'INCOMPLETE', 'pass': False,
              'scope': 'Finite four-core native CHI SE trace invariance; not Linux acceptance.',
              'issues': [], 'missing': [], 'excludedStats': sorted(CORE.HOST_STATS),
              'rawPolicy': 'trace-on file required; trace-off file absent; no other trace-dependent exclusions',
              'recordOrderCompared': True, 'checkerSha256': CORE.sha(CHECKER_PATH)}
    try:
        on, off = Path(on).resolve(), Path(off).resolve()
        CORE.require(on != off, 'trace-on and trace-off must be distinct actual runs')
        program_manifest = Path(program_manifest)
        program = CORE.read_json(program_manifest)
        CORE.require(program.get('protocolOpcodeInjection') is False
                     and program.get('rtlRequestWorkload') is True, 'not an actual CPU program')
        workload = program['bpuPhaseWorkload']
        CORE.require(type(workload['minimumCompletedMainPhasesPerHart']) is int
                     and workload['minimumCompletedMainPhasesPerHart'] >= 2
                     and workload['partialFinalPhaseMustBeObserved'] is True, 'weak phase workload')
        CORE.require([r['hart'] for r in program['binaries']] == list(range(4)), 'four hart binaries required')
        for row in program['binaries']:
            CORE.require(CORE.sha(program_manifest.parent / row['file']) == row['sha256'], 'ELF hash mismatch')
        program_sha = CORE.sha(program_manifest)
        left = CORE.validate_run(on, program, program_sha)
        right = CORE.validate_run(off, program, program_sha)
        CORE.require(left['plan']['trace'] == 'on' and right['plan']['trace'] == 'off',
                     'wrong trace identities: expected on then off')
        for key in ('gem5Sha256', 'programManifestSha256', 'configSha256', 'binaries'):
            CORE.require(left['plan'][key] == right['plan'][key], 'different execution plan ' + key)
        for key in ('result', 'stats', 'counts', 'configSha256'):
            CORE.require(left[key] == right[key], 'actual behavior differs: ' + key)
        report['modelSha256'] = left['plan']['gem5Sha256']
        report['programManifestSha256'] = program_sha
        report['configSourceSha256'] = left['plan']['configSha256']
        report['cpuExitTick'] = left['result']['cpuExitTick']
        report['drainEndTick'] = left['result']['drainEndTick']
        report['nonHostStatsCompared'] = len(left['stats'])
        report['traceOnRawSha256'] = left['rawSha256']
        report['traceOffRawAbsent'] = right['rawSha256'] is None
        report['files'] = {}
        for file in CORE.CSV + CORE.TXT + ('config.ini', 'flowgraph-config.json', 'counts.json'):
            hashes = [CORE.sha(directory / file) for directory in (on, off)]
            CORE.require(hashes[0] == hashes[1], 'file bytes differ: ' + file)
            report['files'][file] = hashes[0]
        banners = []
        for directory in (on, off):
            banners.append([line for line in (directory / 'run.log').read_text().splitlines()
                            if line.startswith(('BPU_PHASE_HART_', 'NATIVE_RTL_HART_'))])
        CORE.require(banners[0] == banners[1], 'actual per-hart completion order differs')
        report['completionOrder'] = banners[0]
        report['cores'] = []
        for hart in range(4):
            a = CORE.validate_exports(on, hart)
            b = CORE.validate_exports(off, hart)
            CORE.require(a == b, f'core {hart}: full records, iteration order, files or archives differ')
            report['cores'].append({'hart': hart, **a['manifest'],
                                    'perInstanceArtifactCount': len(a['artifacts'])})
    except FileNotFoundError as error:
        report['missing'].append(str(error))
    except (ValueError, KeyError, IndexError, TypeError, StopIteration, CORE.configparser.Error) as error:
        report['issues'].append(str(error))
    report['status'] = 'FAIL' if report['issues'] else ('INCOMPLETE' if report['missing'] else 'PASS')
    report['pass'] = report['status'] == 'PASS'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('on', 'off', 'program-manifest', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = check(args.on, args.off, args.program_manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'issues': result['issues'], 'missing': result['missing'],
                      'output': str(args.output)}))
    return 0 if result['pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

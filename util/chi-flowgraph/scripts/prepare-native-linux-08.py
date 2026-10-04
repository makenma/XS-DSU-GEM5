"""Prepare a reviewable model08 plan; launch only with explicit --launch.

Prepare: --spec INPUT.json --plan NEW_PLAN.json
Execute one authorized group: --plan READY_PLAN.json --launch core|random
Core starts its separate kernel continuation. Random has its own driver exit.
Missing/failed prerequisites produce INCOMPLETE and never start a guest.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from native_linux_run08 import PROJECT, environment, prepare, read, require, verify_plan, write_host_exit


def launch(plan_path, selection):
    plan_path = Path(plan_path).resolve()
    plan = read(plan_path)
    spec = verify_plan(plan)  # All checks happen before mkdir/Popen.
    outputs = {key: Path(value) for key, value in spec['outputs'].items()}
    env = environment(spec['gem5Source'])
    if selection == 'random':
        require(not outputs['randomMemory'].exists(), 'random run output already exists')
        command = plan['commands']['randomRunner']
        code = subprocess.call(command, env=env)
        verify_plan(plan)
        return code
    require(selection == 'core', 'invalid launch selection')
    for name in ('core', 'kernelBuild', 'state', 'acceptance', 'supervisorLog'):
        require(not outputs[name].exists(), 'refusing existing launch output: ' + str(outputs[name]))
    core = outputs['core']
    core.mkdir(parents=True)
    command = plan['commands']['core']
    (core / 'execution-plan.json').write_text(json.dumps({
        'kind': 'native-linux-core-model08', 'launchPlan': str(plan_path),
        'launchIdentitySha256': plan['identitySha256'], 'gem5Sha256': spec['gem5Sha256'],
        'command': command}, indent=2) + '\n')
    with (core / 'run.log').open('wb') as log, outputs['supervisorLog'].open('xb') as supervisor_log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        (core / 'host-pid.txt').write_text(str(process.pid) + '\n')
        continuation = [sys.executable, str(PROJECT / 'scripts/continue-native-linux-08.py'),
            '--launch-plan', str(plan_path), '--project', str(PROJECT), '--gem5-source', spec['gem5Source'],
            '--gem5', spec['gem5'], '--gem5-sha256', spec['gem5Sha256'],
            '--firmware', spec['kernelFirmware'], '--firmware-sha256', spec['kernelFirmwareSha256'],
            '--core', str(core), '--core-pid', str(process.pid), '--kernel-build', str(outputs['kernelBuild']),
            '--state', str(outputs['state']), '--output', str(outputs['acceptance'])]
        supervisor = None
        try:
            supervisor = subprocess.Popen(continuation, env=env, stdout=supervisor_log, stderr=subprocess.STDOUT)
        except OSError as error:
            # Do not abandon the actual core's exit receipt if supervisor creation fails.
            (core / 'supervisor-start-error.txt').write_text(str(error) + '\n')
        print(json.dumps({'corePid': process.pid, 'supervisorPid': supervisor.pid if supervisor else None,
                          'gem5Sha256': spec['gem5Sha256'], 'command': command}), flush=True)
        code = process.wait()
        write_host_exit(core / 'host-exit-code.txt', code)
        supervisor_code = supervisor.wait() if supervisor else 1
    verify_plan(plan)
    (core / 'driver-exit.json').write_text(json.dumps({'coreHostExitCode': code,
        'supervisorExitCode': supervisor_code, 'passed': code == 0 and supervisor_code == 0}, indent=2)+'\n')
    print(json.dumps({'coreHostExitCode': code, 'supervisorExitCode': supervisor_code}), flush=True)
    return 0 if code == 0 and supervisor_code == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--launch', choices=('core', 'random'))
    args = parser.parse_args()
    if args.launch:
        if args.spec: parser.error('--launch uses an already reviewed plan, not --spec')
        try: return launch(args.plan, args.launch)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print(json.dumps({'status': 'REFUSED', 'error': str(error)})); return 2
    if args.spec is None: parser.error('--spec required to prepare')
    if args.plan.exists(): parser.error('refusing to replace an existing plan')
    try: result = prepare(read(args.spec))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'schemaVersion': 1, 'status': 'INCOMPLETE', 'ready': False,
                  'guestExecuted': False, 'problems': [str(error)]}
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    args.plan.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'ready': result['ready'], 'problems': result['problems']}))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Independent final entry point with explicit externally observed driver ID.

The original checker is imported into an isolated module and its exact checks
are run with the explicitly supplied expected session. No source is modified.
"""
import argparse
import json
from pathlib import Path

from native_linux_run08 import module, read, verify_plan


def check(core, kernel_build, random_memory, driver_completion, expected_driver_session, launch_plan):
    if type(expected_driver_session) is not int or expected_driver_session <= 0:
        raise ValueError('positive explicitly observed expected driver session required')
    plan = read(launch_plan)
    spec = verify_plan(plan)
    original = module('check-native-linux-complete.py')
    original.DRIVER_SESSION = expected_driver_session
    report = original.check(core, kernel_build, random_memory, driver_completion)
    report['expectedDriverSession'] = expected_driver_session
    report['launchPlan'] = str(Path(launch_plan).resolve())
    try:
        for key, path in [('core', core), ('kernelBuild', kernel_build), ('randomMemory', random_memory)]:
            if Path(spec['outputs'][key]).resolve() != Path(path).resolve():
                report['failures'].append('launch plan run directory mismatch: ' + key)
        if report.get('gem5Sha256') and report['gem5Sha256'] != spec['gem5Sha256']:
            report['failures'].append('Linux runs differ from the authorized candidate SHA')
    except (OSError, ValueError, KeyError, TypeError) as error:
        report['failures'].append('launch plan verification: ' + str(error))
    # Final callback failures can occur after normal Python result files.
    for label, directory in [('core', core), ('kernel-build', kernel_build), ('random-memory', random_memory)]:
        path = Path(directory) / 'host-exit-code.txt'
        try:
            if path.read_text().strip() != '0':
                report['failures'].append(label + ': actual host exit is not zero')
        except OSError:
            report['incomplete'].append(label + ': missing actual host exit receipt')
    report['status'] = 'INCOMPLETE' if report['incomplete'] else 'FAIL' if report['failures'] else 'PASS'
    report['passed'] = report['status'] == 'PASS'
    report['knownFailure'] = bool(report['failures'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('core', 'kernel-build', 'random-memory', 'driver-completion', 'launch-plan', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--expected-driver-session', type=int, required=True)
    args = parser.parse_args()
    try:
        result = check(args.core, args.kernel_build, args.random_memory, args.driver_completion,
                       args.expected_driver_session, args.launch_plan)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'status': 'FAIL', 'passed': False, 'knownFailure': True,
                  'failures': [str(error)], 'incomplete': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'failures': result['failures'], 'incomplete': result['incomplete']}))
    return 0 if result['passed'] else 2 if result['status'] == 'INCOMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())

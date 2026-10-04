"""Recheck all model10 actual Linux evidence and its external terminal driver receipt."""
import argparse
import json
from pathlib import Path

from native_linux_run_v2 import ERRORS, _write, final_check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core', 'kernel-build', 'random-memory', 'driver-completion', 'launch-plan', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-driver-session', type=int, required=True)
    args = parser.parse_args()
    try:
        result = final_check(args.core, args.kernel_build, args.random_memory, args.driver_completion,
                             args.expected_driver_session, args.launch_plan)
    except ERRORS as error:
        result = {'status': 'FAIL', 'passed': False, 'knownFailure': True,
                  'failures': [str(error)], 'incomplete': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write(args.output, result, exclusive=True)
    print(json.dumps({'status': result['status'], 'failures': result['failures'], 'incomplete': result['incomplete']}))
    return 0 if result['passed'] else 2 if result['status'] == 'INCOMPLETE' else 1


if __name__ == '__main__':
    raise SystemExit(main())

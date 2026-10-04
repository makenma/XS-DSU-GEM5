"""Prepare model10; --launch explicitly runs the full core -> random -> kernel chain."""
import argparse
import json
from pathlib import Path

from native_linux_run_v2 import ERRORS, _write, launch_serial, prepare, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--launch', action='store_true')
    args = parser.parse_args()
    if args.launch:
        if args.spec is not None:
            parser.error('--launch accepts an already prepared --plan only')
        try:
            return launch_serial(args.plan)
        except ERRORS as error:
            print(json.dumps({'status': 'REFUSED', 'error': str(error)})); return 2
    if args.spec is None:
        parser.error('--spec is required to prepare')
    result = prepare(read(args.spec))
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    _write(args.plan, result, exclusive=True)
    print(json.dumps({'ready': result['ready'], 'status': result['status'], 'problems': result['problems']}))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

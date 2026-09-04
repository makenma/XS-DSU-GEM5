import argparse
import runpy
import sys

from dummy_core_case_registry import CASES, invocation


parser = argparse.ArgumentParser()
parser.add_argument("--case", choices=tuple(CASES), required=True)
parser.add_argument("--master-seed", type=int, required=True)
parser.add_argument("--sim-tick-limit", required=True)
arguments, remainder = parser.parse_known_args()

script, child_argv = invocation(arguments.case, remainder, arguments.sim_tick_limit)
sys.argv = child_argv
try:
    runpy.run_path(str(script), run_name="__main__")
except SystemExit as error:
    code = error.code if isinstance(error.code, int) else 1
    if code != 0:
        raise

import argparse
import os
import sys
import types
from pathlib import Path

from m5.objects.PrestartCancelDriver import PrestartCancelDriver

root = Path.cwd()
sys.path.insert(0, str(root / "configs"))
sys.path.insert(0, str(root / "configs/example/ai_mesh"))
from dummy_core_case_registry import invocation

source_path = root / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
runner = types.ModuleType("gate6_cancel_runner")
runner.__file__ = str(source_path)
sys.modules[runner.__name__] = runner
source = source_path.read_text()
assert source.rstrip().endswith("sys.exit(main())")
exec(compile(source.rsplit("sys.exit(main())", 1)[0], str(source_path),
             "exec"), runner.__dict__)

parser = argparse.ArgumentParser()
parser.add_argument("--case", required=True)
parser.add_argument("--master-seed", required=True)
parser.add_argument("--sim-tick-limit", required=True)
arguments, remainder = parser.parse_known_args()
script, child_argv = invocation(arguments.case, remainder,
                                arguments.sim_tick_limit)

EXIT_CAUSE = os.environ.get("GATE6_CANCEL_EXIT_CAUSE",
                           "PRESTART_CANCEL_OBSERVED")
evidence_path = os.environ["GATE6_CANCEL_EVIDENCE"]

original_scenario = runner.SCENARIOS[arguments.case]


def cancel_scenario(ctx):
    original_scenario(ctx)
    ctx.expected_terminal = EXIT_CAUSE


runner.SCENARIOS[arguments.case] = cancel_scenario
runner.invariant_registry = lambda case, argv: []


def dispatched_with_cancel_driver(**kwargs):
    dispatcher = real_dispatcher(**kwargs)
    driver = PrestartCancelDriver(
        dispatcher=dispatcher,
        cores=list(kwargs["cores"]),
        evidence_path=evidence_path,
        poll_interval=int(os.environ.get("GATE6_CANCEL_POLL", "1000")),
        wait_timeout=int(os.environ["GATE6_CANCEL_WAIT_TIMEOUT"]),
        observe_ticks=int(os.environ["GATE6_CANCEL_OBSERVE"]),
        cancel_on_wait=os.environ.get("GATE6_CANCEL_ON_WAIT", "1") == "1",
    )
    kwargs["loader"].cancel_driver = driver
    return dispatcher


real_dispatcher = runner.MeshDispatcher
runner.MeshDispatcher = dispatched_with_cancel_driver
sys.argv = child_argv
runner.main()

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mesh_ir.acceptance import write_success_artifacts


_ACCEPTANCE_CALLS = []


def pytest_sessionstart(session):
    _ACCEPTANCE_CALLS.clear()


def pytest_runtest_logreport(report):
    if report.when == "call":
        outcome = "xfail" if hasattr(report, "wasxfail") else report.outcome
        _ACCEPTANCE_CALLS.append(outcome)


def pytest_sessionfinish(session, exitstatus):
    if "AI_MESH_CHILD_REPORT" not in os.environ:
        return
    if exitstatus == 0 and _ACCEPTANCE_CALLS == ["passed"]:
        write_success_artifacts(["unit_test_passed"])

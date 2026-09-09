import ast
import importlib.util
import json
import sys

import pytest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("load_sweep_review", REPO / "tests/gem5/ai_mesh/run_load_sweep.py")
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def check_window(result):
    path = REPO / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == "check_read_window_slides")
    def fatal_if(condition, message, *args):
        if condition:
            raise AssertionError(message % args if args else message)
    namespace = {"EFFECTIVE_ARCH": SimpleNamespace(dma_read_outstanding=4),
                 "fatal_if": fatal_if}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    out = []
    namespace["check_read_window_slides"](None, result, None, out)
    return out


def test_cycle_units():
    result = {
        "core_clock_period_ticks": 500,
        "dma_timings": [{"first_ar_tick": 1000, "local_commit_tick": 2000}],
        "bridges": [{"valid_read_bytes": 64, "peak_read_outstanding": 4}],
        "watchdog_fired": False, "transport": [{"error_code": 0}],
    }
    axes = {"workload": "contiguous", "outstanding": 4, "burst": 8, "vnet": "V1"}
    result["network_stalls"] = {"R": {"router_credit_stall_vc_cycles": 0}}
    result["target_message_buffer_blocked_cycles"] = {"hbm": {"R": 0}}
    measured = sweep.measure(result, axes, 0, True)
    assert measured["valid"]
    expected_cycles = (2000 - 1000) / 500
    assert measured["bytes_per_cycle"] == 64 / expected_cycles


def window_fixture():
    return {
        "bridges": [{"peak_read_outstanding": 4}],
        "burst_timings": [
            {"core_id": 0, "ordinal": i, "channel": "AR", "axi_id": i % 4,
             "address": 4096 + 256 * i, "beats": 8, "beat_bytes": 32,
             "ar_aw_tick": tick, "response_tick": 40 + 10 * i,
             "commit_tick": 100 + 10 * i}
            for i, tick in enumerate((1, 2, 3, 4, 60))],
    }


def test_window_validates_real_bursts():
    assert check_window(window_fixture()) == ["read window slides: PASS"]


def test_fifth_ar_after_first_commit_is_rejected():
    result = window_fixture()
    result["burst_timings"][0]["commit_tick"] = 50
    with pytest.raises(AssertionError, match="first burst SRAM commit"):
        check_window(result)


def test_four_ars_must_precede_first_rlast():
    result = window_fixture()
    result["burst_timings"][2]["ar_aw_tick"] = 45
    result["burst_timings"][3]["ar_aw_tick"] = 46
    with pytest.raises(AssertionError, match="exactly the configured number"):
        check_window(result)


@pytest.mark.parametrize("missing", ("burst_timings", "bridges"))
def test_window_requires_evidence(missing):
    result = window_fixture()
    result[missing] = []
    with pytest.raises((AssertionError, ValueError)):
        check_window(result)


def test_zero_duration_run_is_invalid_not_exception():
    result = {
        "core_clock_period_ticks": 500,
        "dma_timings": [{"first_ar_tick": 1000, "local_commit_tick": 1000}],
        "bridges": [{"valid_read_bytes": 0, "peak_read_outstanding": 0}],
        "watchdog_fired": True, "transport": [{"error_code": 1}],
    }
    axes = {"workload": "contiguous", "outstanding": 4, "burst": 8, "vnet": "V1"}
    assert sweep.measure(result, axes, 0, False)["valid"] is False


def run_selection(monkeypatch, tmp_path, workloads, latency, throughput):
    recorded = []
    monkeypatch.setattr(sweep, "materialize", lambda workspace, *args: (workspace / "arch", workspace / "program"))
    def execute(gem5, workspace, arch, program, axes, delay):
        row = {**axes, "latency_cycles": delay, "valid": True,
               "bytes_per_cycle": throughput({**axes, "latency_cycles": delay}),
               "peak_read_outstanding": axes["outstanding"],
               "router_credit_stall_vc_cycles": 0}
        recorded.append(row)
        return True, row
    monkeypatch.setattr(sweep, "execute_run", execute)
    destination = tmp_path / "sweep"
    monkeypatch.setattr(sys, "argv", [
        "run_load_sweep.py", "--workdir", str(destination),
        "--workloads", *workloads, "--latency-cycles",
        *map(str, latency if isinstance(latency, list) else [latency])])
    sweep.main()
    return json.loads((destination / "results.json").read_text())


def test_phase2_preserves_incumbent_burst(monkeypatch, tmp_path):
    rows = run_selection(monkeypatch, tmp_path, ["contiguous"], 0,
                         lambda axes: {8: 100, 2: 20, 4: 40, 16: 80}[axes["burst"]])
    phase3 = [row for row in rows if row["config"] in ("C8", "C9", "C10")]
    assert all(row["burst"] == 8 for row in phase3)


def test_phase_carry_is_per_workload(monkeypatch, tmp_path):
    def throughput(axes):
        if axes["workload"] == "contiguous":
            return 100 if axes["outstanding"] == 4 else 10
        return 90 if axes["outstanding"] == 8 else 9
    rows = run_selection(monkeypatch, tmp_path, ["contiguous", "multi_tensor"], 0, throughput)
    phase2 = [row for row in rows if row["workload"] == "multi_tensor"
              and row["config"] in ("C5", "C6", "C7")]
    assert all(row["outstanding"] == 8 for row in phase2)


def test_nonzero_only_latency_axis(monkeypatch, tmp_path):
    rows = run_selection(monkeypatch, tmp_path, ["contiguous"], 500,
                         lambda axes: 100)
    assert len(rows) == 11


@pytest.mark.parametrize("fault", ("empty", "zero", "bytes", "watchdog", "error"))
def test_invalid_measurements_do_not_crash(fault):
    result = {
        "core_clock_period_ticks": 500,
        "dma_timings": [{"core_id": 0, "first_ar_tick": 1000, "local_commit_tick": 2000}],
        "bridges": [{"valid_read_bytes": 64, "peak_read_outstanding": 4}],
        "transport": [{"error_code": 0}],
    }
    if fault == "empty":
        result["dma_timings"] = []
    elif fault == "zero":
        result["dma_timings"][0]["local_commit_tick"] = 1000
    elif fault == "bytes":
        result["bridges"][0]["valid_read_bytes"] = 0
    elif fault == "watchdog":
        result["watchdog_fired"] = True
    else:
        result["transport"][0]["error_code"] = 1
    row = sweep.measure(result, {}, 0, True)
    assert not row["valid"]
    assert row["invalid_reasons"]
    assert sweep.pareto_frontier([row]) == []


def test_completion_skew_distinguishes_equal_work():
    result = {
        "core_clock_period_ticks": 500,
        "dma_timings": [
            {"core_id": 0, "first_ar_tick": 1000, "local_commit_tick": 2000},
            {"core_id": 1, "first_ar_tick": 1000, "local_commit_tick": 10000}],
        "bridges": [{"valid_read_bytes": 64, "peak_read_outstanding": 4}] * 2,
        "transport": [{"error_code": 0}],
    }
    assert sweep.measure(result, {}, 0, True)["completion_skew_cycles"] == 16


def test_phase_carry_is_per_latency(monkeypatch, tmp_path):
    def throughput(axes):
        best = 4 if axes["latency_cycles"] == 0 else 8
        return 100 if axes["outstanding"] == best else 10
    rows = run_selection(monkeypatch, tmp_path, ["contiguous"], [0, 500], throughput)
    for latency, best in ((0, 4), (500, 8)):
        phase2 = [row for row in rows if row["latency_cycles"] == latency
                  and row["config"] in ("C5", "C6", "C7")]
        assert len(phase2) == 3
        assert all(row["outstanding"] == best for row in phase2)

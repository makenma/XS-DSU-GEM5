import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
CONFIG_SCRIPT = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_agent.py"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate4"
sys.path.insert(0, str(REPO / "configs" / "example" / "ai_mesh"))
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts

SIM_TICK_LIMIT = 2000000000000


def run_agent(scenario, outdir, extra=()):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={outdir}",
            str(CONFIG_SCRIPT),
            "--runtime-config",
            str(FIXTURES / f"agent_runtime_config_{scenario}.yaml"),
            "--surrogate-profiles",
            str(FIXTURES / f"agent_surrogate_profiles_{scenario}.json"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
            *extra,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_single_user_three_repeat_is_byte_identical(tmp_path):
    facts = []
    for index in range(3):
        run = run_agent("su_first_pass", tmp_path / f"run{index}")
        assert run.returncode == 0, run.stdout + run.stderr
        assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
        facts.append(
            (tmp_path / f"run{index}" / "gate4_facts.tsv").read_bytes()
        )
    assert facts[0] == facts[1] == facts[2]


def test_three_user_three_repeat_is_byte_identical(tmp_path):
    facts = []
    for index in range(3):
        run = run_agent("three_user", tmp_path / f"run{index}")
        assert run.returncode == 0, run.stdout + run.stderr
        assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
        facts.append(
            (tmp_path / f"run{index}" / "gate4_facts.tsv").read_bytes()
        )
    assert facts[0] == facts[1] == facts[2]


def test_cq_metadata_read_delay_moves_host_visible_later_only(tmp_path):
    runs = {}
    for delay in (0, 250, 1000):
        run = run_agent(
            "su_compile_repair",
            tmp_path / f"delay{delay}",
            (
                "--cq-read-delay-ns",
                str(delay),
                "--metadata-read-delay-ns",
                str(delay),
            ),
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
        events, final, metrics, fatal = parse_gate3_facts(
            tmp_path / f"delay{delay}" / "gate4_facts.tsv"
        )
        assert fatal is None
        runs[delay] = (events, final, metrics)

    def skeleton(events):
        return sorted(
            (event["kind"], event["object"], event["request_id"])
            for event in events
        )

    def consume_ticks(events):
        return [
            event["tick"]
            for event in events
            if event["kind"] == "CQ_CONSUME"
        ]

    base_events, base_final, base_metrics = runs[0]
    for delay in (250, 1000):
        events, final, metrics = runs[delay]
        assert skeleton(events) == skeleton(base_events)
        assert final == base_final
        assert metrics == base_metrics
    ticks_zero = consume_ticks(runs[0][0])
    ticks_250 = consume_ticks(runs[250][0])
    ticks_1000 = consume_ticks(runs[1000][0])
    assert all(later >= sooner for sooner, later in zip(ticks_zero, ticks_250))
    assert all(later >= sooner for sooner, later in zip(ticks_250, ticks_1000))
    assert any(
        later > sooner for sooner, later in zip(ticks_zero, ticks_1000)
    )

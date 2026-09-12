import copy
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

from mesh_ir.agent_config import load_agent_runtime_config, release_policy_of
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.gate3_oracle import parse_facts_tsv
from mesh_ir.gate4_oracle import (
    Gate4Execution,
    Gate4Faults,
    Gate4OracleError,
    Gate4RunOracle,
    ORACLE_CHECKS,
    arena_regions,
)

SIM_TICK_LIMIT = 40000000000
LATE_ANCHOR_TICK_LIMIT = 400000000000


def run_first_pass(outdir):
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
            str(FIXTURES / "agent_runtime_config_su_compile_repair.yaml"),
            "--surrogate-profiles",
            str(FIXTURES / "agent_surrogate_profiles_su_compile_repair.json"),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def run_control_fixture(config_name, outdir):
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
            str(FIXTURES / f"agent_runtime_config_{config_name}.yaml"),
            "--surrogate-profiles",
            str(FIXTURES / "agent_surrogate_profiles_three_user.json"),
            "--sim-tick-limit",
            str(LATE_ANCHOR_TICK_LIMIT),
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def build_execution(config_name="agent_runtime_config_su_compile_repair.yaml",
                    surrogate_name=None):
    config = load_agent_runtime_config(FIXTURES / config_name)
    agent = config.document["agent"]
    workload = load_workload_plan(FIXTURES / agent["workload_plan"])
    control = (
        load_control_plan(FIXTURES / agent["control_plan"], workload)
        if agent["control_plan"] is not None
        else None
    )
    surrogate = load_surrogate_profiles(
        FIXTURES / (surrogate_name or "agent_surrogate_profiles_su_compile_repair.json")
    )
    regions = arena_regions(config.document["serving"]["address_map"])
    return Gate4Execution(
        workload,
        control,
        surrogate,
        regions,
        Gate4Faults(),
    )


def write_events(path, events, metrics, original_lines):
    lines = []
    for event in events:
        def field(value):
            if value is None:
                return "-"
            return str(value)

        lines.append(
            "|".join(
                [
                    "EVENT",
                    str(event["tick"]),
                    str(event["phase"]),
                    event["kind"],
                    field(event["object"]),
                    field(event["absolute_seq"]),
                    field(event["request_id"]),
                    field(event["cookie"]),
                    field(event.get("slot")),
                    field(event.get("generation")),
                    field(event.get("txn")),
                    field(event["channel"]),
                    field(event.get("direction")),
                    field(event.get("control")),
                    field(event.get("axi_id")),
                    field(event.get("address")),
                    field(event.get("size")),
                    field(event.get("response")),
                    str(event["bytes"]),
                    field(event.get("wstrb")),
                    field(event.get("status")),
                ]
            )
        )
    for name, value in sorted(metrics.items()):
        lines.append(f"METRIC|{name}|{value}")
    lines.extend(
        line
        for line in original_lines
        if line and not line.startswith(("EVENT", "METRIC"))
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_oracle_rejects_tampered_owner_and_byte_facts(tmp_path):
    run = run_first_pass(tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    facts_path = tmp_path / "run" / "gate4_facts.tsv"
    execution = build_execution()
    oracle = Gate4RunOracle(execution, facts_path)
    results = oracle.run()
    assert all(results.values()), results

    events, final, metrics, fatal = parse_facts_tsv(facts_path)
    original_lines = facts_path.read_text(encoding="utf-8").splitlines()

    byte_tampered = copy.deepcopy(metrics)
    byte_tampered["raw_log_bytes"] = metrics["raw_log_bytes"] + 1
    byte_path = tmp_path / "byte_tampered.tsv"
    write_events(byte_path, events, byte_tampered, original_lines)
    results = Gate4RunOracle(execution, byte_path).run()
    assert results["byte_conservation"] is False

    owner_events = copy.deepcopy(events)
    stolen = [
        event
        for event in owner_events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] == "W"
        and event["control"] == "OUTPUT"
    ]
    assert stolen
    stolen[0]["request_id"] = 999999
    owner_path = tmp_path / "owner_tampered.tsv"
    write_events(owner_path, owner_events, metrics, original_lines)
    results = Gate4RunOracle(execution, owner_path).run()
    assert results["traffic_conservation"] is False

    irq_events = copy.deepcopy(events)
    delivered = [
        event for event in irq_events if event["kind"] == "IRQ_DELIVER"
    ]
    assert delivered
    irq_events.remove(delivered[0])
    irq_path = tmp_path / "irq_tampered.tsv"
    write_events(irq_path, irq_events, metrics, original_lines)
    results = Gate4RunOracle(execution, irq_path).run()
    assert results["irq_before_cq_consume"] is False

    outcome_events = copy.deepcopy(events)
    for event in outcome_events:
        if event["kind"] == "TASK_TERMINAL":
            event["status"] = "BUSINESS_FAILED"
            break
    outcome_path = tmp_path / "outcome_tampered.tsv"
    write_events(outcome_path, outcome_events, metrics, original_lines)
    results = Gate4RunOracle(execution, outcome_path).run()
    assert results["task_outcome_closure"] is False


CANCEL_TARGET = (1 << 32) | 1
CANCEL_COMMAND = (1 << 32) | 2


def _control_oracle(tmp_path, config_name, surrogate_name):
    run = run_control_fixture(config_name, tmp_path / "run")
    assert run.returncode == 0, run.stdout + run.stderr
    facts_path = tmp_path / "run" / "gate4_facts.tsv"
    execution = build_execution(
        f"agent_runtime_config_{config_name}.yaml", surrogate_name
    )
    return facts_path, execution


def _without_generate_latch(lines, request_id):
    removed = [
        line
        for line in lines
        if line.startswith("EVENT|")
        and line.split("|")[3] == "GENERATE_TERMINAL_LATCHED"
        and int(line.split("|")[6]) == request_id
    ]
    assert len(removed) == 1
    return [line for line in lines if line not in removed]


def _prefix_lines(facts_path, tick):
    return [
        line
        for line in facts_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("EVENT|") or int(line.split("|")[1]) <= tick
    ]


def _rewrite(tmp_path, name, events, metrics, original_lines):
    path = tmp_path / f"{name}.tsv"
    write_events(path, events, metrics, original_lines)
    return path


CANCEL_LIFECYCLE_CHECKS = ("cq_conservation", "control_outcome_legality")


def _rejection(execution, path):
    try:
        results = Gate4RunOracle(execution, path).run()
    except Gate4OracleError:
        return "facts_inconsistency"
    return [
        name for name in CANCEL_LIFECYCLE_CHECKS if not results[name]
    ] or None


def test_oracle_rejects_missing_latch_on_cancel_wins(tmp_path):
    facts_path, execution = _control_oracle(
        tmp_path, "cancel_live", "agent_surrogate_profiles_three_user.json"
    )
    oracle = Gate4RunOracle(execution, facts_path)
    results = oracle.run()
    assert all(results.values()), results
    target = oracle.walk.controls[0]["target_request_id"]
    assert oracle.walk.controls[0]["winner"] == "CANCEL_WINS"

    events, final, metrics, fatal = parse_facts_tsv(facts_path)
    fact_lines = facts_path.read_text(encoding="utf-8").splitlines()
    path = tmp_path / "missing_cancel_wins_latch.tsv"
    path.write_text(
        "\n".join(_without_generate_latch(fact_lines, target)) + "\n",
        encoding="utf-8",
    )
    assert _rejection(execution, path) == "facts_inconsistency"

    assigned = min(
        event["tick"]
        for event in events
        if event["kind"] == "CQ_ASSIGN" and event["request_id"] == target
    )
    prefix = _prefix_lines(facts_path, assigned)
    prefix_path = tmp_path / "assigned_prefix.tsv"
    prefix_path.write_text("\n".join(prefix) + "\n", encoding="utf-8")
    assert Gate4RunOracle(
        execution, prefix_path
    ).walk.controls[0]["winner"] == "CANCEL_WINS"

    prefix_path.write_text(
        "\n".join(_without_generate_latch(prefix, target)) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Gate4OracleError):
        Gate4RunOracle(execution, prefix_path)


def test_oracle_rejects_tampered_cancel_lifecycle_facts(tmp_path):
    facts_path, execution = _control_oracle(
        tmp_path, "cancel_late_anchor", "agent_surrogate_profiles_three_user.json"
    )
    oracle = Gate4RunOracle(execution, facts_path)
    results = oracle.run()
    assert all(results.values()), results

    events, final, metrics, fatal = parse_facts_tsv(facts_path)
    original_lines = facts_path.read_text(encoding="utf-8").splitlines()

    wrong_winner = copy.deepcopy(events)
    for event in wrong_winner:
        if event["kind"] == "CANCEL_JOIN_RESOLVED":
            event["status"] = "CANCEL_WINS"
    assert _rejection(
        execution,
        _rewrite(tmp_path, "wrong_winner", wrong_winner, metrics, original_lines),
    ) == ["control_outcome_legality"]

    standalone = copy.deepcopy(events)
    for event in standalone:
        if event["kind"] == "CANCEL_JOIN_RESOLVED":
            event["kind"] = "CONTROL_TERMINAL"
            event["status"] = "ALREADY_TERMINAL"
    assert _rejection(
        execution,
        _rewrite(tmp_path, "standalone", standalone, metrics, original_lines),
    ) == ["control_outcome_legality"]

    missing_latch = [
        event
        for event in copy.deepcopy(events)
        if not (
            event["kind"] == "GENERATE_TERMINAL_LATCHED"
            and event["request_id"] == CANCEL_TARGET
        )
    ]
    assert (
        _rejection(
            execution,
            _rewrite(tmp_path, "missing_latch", missing_latch, metrics, original_lines),
        )
        == "facts_inconsistency"
    )

    late_latch = copy.deepcopy(events)
    command_seen = min(
        event["tick"]
        for event in late_latch
        if event["kind"] == "SQ_CONSUME"
        and event["request_id"] == CANCEL_COMMAND
    )
    for event in late_latch:
        if (
            event["kind"] == "GENERATE_TERMINAL_LATCHED"
            and event["request_id"] == CANCEL_TARGET
        ):
            event["tick"] = command_seen + 1
    assert (
        _rejection(
            execution,
            _rewrite(tmp_path, "late_latch", late_latch, metrics, original_lines),
        )
        == "facts_inconsistency"
    )

    wrong_status = copy.deepcopy(events)
    for event in wrong_status:
        if (
            event["kind"] == "CQ_CONSUME"
            and event["request_id"] == CANCEL_COMMAND
        ):
            event["status"] = "SUCCESS"
    assert _rejection(
        execution,
        _rewrite(tmp_path, "wrong_status", wrong_status, metrics, original_lines),
    ) == ["cq_conservation"]

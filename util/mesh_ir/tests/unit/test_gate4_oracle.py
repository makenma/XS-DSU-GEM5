import os
import subprocess
from pathlib import Path

import pytest

from mesh_ir.agent_planning import ArenaRegion
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.gate4_oracle import (
    CancelLegs,
    FactTimeline,
    Gate4Execution,
    Gate4Faults,
    Gate4OracleError,
    Gate4RunOracle,
    _cancel_legs,
    arena_regions,
    plan_bursts,
)

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate4"
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/ai_mesh/run_gate4_protocol.py"

REGIONS = (
    ArenaRegion("INPUT", 0x0000000101000000, 67108864, 32),
    ArenaRegion("PARAMETER", 0x0000000100100000, 8388608, 8),
    ArenaRegion("OUTPUT", 0x0000000105000000, 67108864, 32),
    ArenaRegion("METADATA", 0x0000000109000000, 8388608, 8),
)


def execution(workload_name, control_name=None, faults=None, partial=False):
    workload = load_workload_plan(
        FIXTURES / f"agent_workload_plan_{workload_name}.json"
    )
    control = (
        load_control_plan(
            FIXTURES / f"agent_control_plan_{control_name}.json", workload
        )
        if control_name
        else None
    )
    surrogate = load_surrogate_profiles(
        FIXTURES / f"agent_surrogate_profiles_{workload_name}.json"
    )
    return Gate4Execution(
        workload, control, surrogate, REGIONS,
        faults or Gate4Faults(), partial,
    )


@pytest.mark.parametrize(
    "address,length,expected",
    [
        (0x0, 8, 1),
        (0x0, 64, 1),
        (0x0, 256, 1),
        (0x0, 4096, 1),
        (0x0, 4097, 2),
        (0x0, 8192, 2),
        (0x0, 16384, 4),
        (0x20, 256, 3),
        (0x8, 160, 6),
        (0x100, 200, 2),
        (0x7F0, 64, 3),
    ],
)
def test_plan_bursts_matches_axi_transfer_planner(address, length, expected):
    assert plan_bursts(address, length) == expected


def test_arena_regions_covers_four_arenas():
    address_map = {
        "input_arena": {"base": 1, "bytes": 2},
        "parameter_arena": {"base": 3, "bytes": 4},
        "output_arena": {"base": 5, "bytes": 6},
        "metadata_arena": {"base": 7, "bytes": 8},
        "arena_alignment_bytes": 32,
    }
    regions = arena_regions(address_map)
    assert [region.kind for region in regions] == [
        "INPUT", "PARAMETER", "OUTPUT", "METADATA",
    ]
    assert [region.base for region in regions] == [1, 3, 5, 7]
    assert all(region.alignment == 32 for region in regions)


def _event(tick, phase, kind, fields):
    tail = ["-"] * 17
    tail[BYTES] = "0"
    for index, value in fields.items():
        tail[index] = str(value)
    return "|".join(["EVENT", str(tick), str(phase), kind, *tail])


OBJECT = 0
ABS_SEQ = 1
REQUEST = 2
CHANNEL = 7
CONTROL = 9
BYTES = 14
STATUS = 16


def _facts_text(terminals, consumes, doorbells, metrics, stages=()):
    lines = []
    ordinal = 0
    for kind, request_id in stages:
        lines.append(
            _event(1000 + ordinal, 0, kind, {OBJECT: "COMPILE", REQUEST: request_id})
        )
        ordinal += 1
    for request_id in doorbells:
        lines.append(
            _event(1500 + ordinal, 0, "AXI_ACCEPT", {OBJECT: "DOORBELL", REQUEST: request_id})
        )
        ordinal += 1
    for request_id, status in consumes:
        lines.append(
            _event(
                1900 + ordinal,
                3,
                "GENERATE_TERMINAL_LATCHED",
                {OBJECT: "CONTEXT", REQUEST: request_id},
            )
        )
        lines.append(
            _event(
                1950 + ordinal,
                3,
                "CQ_ASSIGN",
                {OBJECT: "CQ_ENTRY", ABS_SEQ: ordinal, REQUEST: request_id},
            )
        )
        lines.append(
            _event(
                2000 + ordinal,
                3,
                "CQ_CONSUME",
                {OBJECT: "CQ_ENTRY", ABS_SEQ: ordinal, REQUEST: request_id, STATUS: status},
            )
        )
        ordinal += 1
    for request_id, status in terminals:
        lines.append(
            _event(
                9000 + ordinal,
                3,
                "TASK_TERMINAL",
                {OBJECT: "TASK", REQUEST: request_id, STATUS: status},
            )
        )
        ordinal += 1
    for name, value in sorted(metrics.items()):
        lines.append(f"METRIC|{name}|{value}")
    lines.append("FINAL|" + "|".join(["0"] * 19))
    return "\n".join(lines) + "\n"


def _oracle_on_text(runner, text, tmp_path):
    facts = tmp_path / "facts.tsv"
    facts.write_text(text)
    return Gate4RunOracle(runner, facts)


def test_cq_conservation_detects_missing_doorbell(tmp_path):
    runner = execution("su_first_pass")
    text = _facts_text(
        terminals=[(1, "BUSINESS_DONE")],
        consumes=[(1, "SUCCESS")],
        doorbells=[],
        metrics={
            "completed_tasks": 1,
            "failed_tasks": 0,
            "infra_failed_tasks": 0,
            "npu_fabric_raw_log_bytes": 0,
            "agent_live_objects": 0,
            "raw_log_bytes": 0,
            "scanned_log_bytes": 0,
            "excerpt_bytes": 0,
            "excerpt_tokens": 0,
            "repair_input_excerpt_bytes": 0,
        },
        stages=[
            ("HOST_STAGE_ENQUEUE", 1),
            ("HOST_STAGE_START", 1),
            ("HOST_STAGE_DONE", 1),
            ("HOST_STAGE_ENQUEUE", 1),
            ("HOST_STAGE_START", 1),
            ("HOST_STAGE_DONE", 1),
        ],
    )
    oracle = _oracle_on_text(runner, text, tmp_path)
    assert not oracle.check_cq_conservation()
    assert oracle.check_task_outcome_closure()


def test_task_outcome_closure_detects_wrong_status(tmp_path):
    runner = execution("su_first_pass")
    text = _facts_text(
        terminals=[(1, "BUSINESS_FAILED")],
        consumes=[(1, "SUCCESS")],
        doorbells=[1],
        metrics={
            "completed_tasks": 0,
            "failed_tasks": 1,
            "infra_failed_tasks": 0,
            "npu_fabric_raw_log_bytes": 0,
            "agent_live_objects": 0,
            "raw_log_bytes": 0,
            "scanned_log_bytes": 0,
            "excerpt_bytes": 0,
            "excerpt_tokens": 0,
            "repair_input_excerpt_bytes": 0,
        },
        stages=[],
    )
    oracle = _oracle_on_text(runner, text, tmp_path)
    assert not oracle.check_task_outcome_closure()


def test_cancel_legality_rejects_illegal_winner_pair(tmp_path):
    oracle = _oracle_on_text(
        execution("three_user", "cancel_live"), _cancel_join_facts(), tmp_path
    )
    assert oracle.check_cq_conservation()
    assert oracle.check_control_outcome_legality()

    tampered = _cancel_join_facts(cancel_status="SUCCESS")
    assert not _oracle_on_text(
        execution("three_user", "cancel_live"), tampered, tmp_path
    ).check_cq_conservation()

    standalone = _cancel_join_facts(join_status="CONTROL_TERMINAL")
    assert not _oracle_on_text(
        execution("three_user", "cancel_live"), standalone, tmp_path
    ).check_control_outcome_legality()

    wrong_winner = _cancel_join_facts(join_status="CANCEL_WINS")
    assert not _oracle_on_text(
        execution("three_user", "cancel_live"), wrong_winner, tmp_path
    ).check_control_outcome_legality()


def test_cancel_rejects_generate_cq_without_terminal_latch(tmp_path):
    runner = execution("three_user", "cancel_live")
    for stage in (
        {"target_assign": True, "target_consume": True},
        {"target_assign": True, "target_consume": False},
    ):
        with pytest.raises(Gate4OracleError):
            _oracle_on_text(
                runner, _cancel_join_facts(latch=False, **stage), tmp_path
            )


def test_cancel_rejects_generate_consume_without_assignment(tmp_path):
    with pytest.raises(Gate4OracleError):
        _oracle_on_text(
            execution("three_user", "cancel_live"),
            _cancel_join_facts(target_assign=False),
            tmp_path,
        )


def test_cancel_allows_generate_prefix_without_cq(tmp_path):
    for stage, expected in (
        (
            {"latch": False, "target_assign": False, "target_consume": False},
            (True, "SUCCESS", "CANCEL_WINS"),
        ),
        (
            {"latch": True, "target_assign": True, "target_consume": False},
            (True, "ALREADY_TERMINAL", "TARGET_SUCCESS_WINS"),
        ),
    ):
        oracle = _oracle_on_text(
            execution("three_user", "cancel_live"),
            _cancel_join_facts(**stage),
            tmp_path,
        )
        entry = oracle.walk.controls[0]
        assert (entry["join"], entry["status"], entry["winner"]) == expected


CANCEL_TARGET = (1 << 32) | 1
CANCEL_COMMAND = (1 << 32) | 2
USER0_ROUND0 = 1
USER0_REPAIR = 2
USER2_ROUND0 = 8589934593
USER2_REPAIR = 8589934594
CONSUMED_COMMANDS = (
    USER0_ROUND0,
    CANCEL_COMMAND,
    CANCEL_TARGET,
    USER0_REPAIR,
    USER2_ROUND0,
    USER2_REPAIR,
)


def _generate_facts(lines, tick, request_id, absolute_seq):
    lines.append(
        _event(tick, 3, "GENERATE_TERMINAL_LATCHED", {
            OBJECT: "CONTEXT", REQUEST: request_id,
        })
    )
    lines.append(
        _event(tick + 1, 3, "CQ_ASSIGN", {
            OBJECT: "CQ_ENTRY", ABS_SEQ: absolute_seq, REQUEST: request_id,
            STATUS: "SUCCESS",
        })
    )
    lines.append(
        _event(tick + 2, 3, "CQ_CONSUME", {
            OBJECT: "CQ_ENTRY", ABS_SEQ: absolute_seq, REQUEST: request_id,
            STATUS: "SUCCESS",
        })
    )


def _cancel_join_facts(cancel_status="ALREADY_TERMINAL",
                       join_status="TARGET_SUCCESS_WINS",
                       latch=True, target_assign=True, target_consume=True,
                       latch_tick=15):
    lines = [
        _event(10, 3, "PUBLICATION_COMMIT", {REQUEST: CANCEL_TARGET}),
        _event(20, 3, "PUBLICATION_COMMIT", {REQUEST: CANCEL_COMMAND}),
        _event(30, 0, "LOCAL_VISIBLE", {OBJECT: "PARAMETER", REQUEST: CANCEL_COMMAND}),
        _event(40, 3, "SQ_CONSUME", {OBJECT: "SQ_ENTRY", ABS_SEQ: 0, REQUEST: CANCEL_TARGET}),
        _event(50, 3, "SQ_CONSUME", {OBJECT: "SQ_ENTRY", ABS_SEQ: 1, REQUEST: CANCEL_COMMAND}),
        _event(70, 3, "CQ_CONSUME", {OBJECT: "CQ_ENTRY", ABS_SEQ: 0, REQUEST: CANCEL_COMMAND, STATUS: cancel_status}),
    ]
    if target_assign:
        if latch:
            lines.append(
                _event(latch_tick, 3, "GENERATE_TERMINAL_LATCHED", {
                    OBJECT: "CONTEXT", REQUEST: CANCEL_TARGET,
                })
            )
        lines.append(
            _event(16, 3, "CQ_ASSIGN", {
                OBJECT: "CQ_ENTRY", ABS_SEQ: 0, REQUEST: CANCEL_TARGET,
                STATUS: "SUCCESS",
            })
        )
    if target_consume:
        lines.append(
            _event(60, 3, "CQ_CONSUME", {
                OBJECT: "CQ_ENTRY", ABS_SEQ: 0, REQUEST: CANCEL_TARGET,
                STATUS: "SUCCESS",
            })
        )
    for index, request in enumerate(
        (USER0_ROUND0, USER0_REPAIR, USER2_ROUND0, USER2_REPAIR)
    ):
        _generate_facts(lines, 80 + 10 * index, request, 2 + index)
    if join_status == "CONTROL_TERMINAL":
        lines.append(
            _event(120, 3, "CONTROL_TERMINAL", {
                OBJECT: "CONTROL_WAITER", REQUEST: CANCEL_COMMAND,
                STATUS: cancel_status,
            })
        )
    else:
        lines.append(
            _event(120, 3, "CANCEL_JOIN_RESOLVED", {
                OBJECT: "CONTROL_WAITER", REQUEST: CANCEL_COMMAND,
                STATUS: join_status,
            })
        )
    for index, request in enumerate(CONSUMED_COMMANDS):
        lines.append(
            _event(200 + index, 0, "AXI_ACCEPT", {
                OBJECT: "DOORBELL", CHANNEL: "AW", CONTROL: "SQ_DOORBELL",
                REQUEST: request, BYTES: 8,
            })
        )
    lines.append("FINAL|" + "|".join(["0"] * 19))
    return "\n".join(lines) + "\n"


def _timeline(entries):
    return FactTimeline(
        [
            {"tick": tick, "kind": kind, "request_id": request_id}
            for tick, kind, request_id in entries
        ]
    )


def _legs(entries, local_submit_failed=False, cut=False,
          target_status="SUCCESS"):
    return _cancel_legs(
        _timeline(entries), 200, 100, local_submit_failed, cut, target_status
    )


def test_cancel_join_wins_while_target_is_live():
    legs = _legs(
        [
            (10, "PUBLICATION_COMMIT", 200),
            (20, "LOCAL_VISIBLE", 100),
            (30, "SQ_CONSUME", 200),
            (40, "SQ_CONSUME", 100),
        ],
        target_status="CANCELLED",
    )
    assert legs == CancelLegs(True, "SUCCESS", "CANCEL_WINS")


def test_cancel_join_reports_already_terminal_when_target_latched():
    latched = [
        (10, "PUBLICATION_COMMIT", 200),
        (20, "LOCAL_VISIBLE", 100),
        (25, "GENERATE_TERMINAL_LATCHED", 200),
        (30, "SQ_CONSUME", 200),
        (40, "SQ_CONSUME", 100),
    ]
    assert _legs(latched) == CancelLegs(
        True, "ALREADY_TERMINAL", "TARGET_SUCCESS_WINS"
    )
    assert _legs(latched, target_status="ERROR") == CancelLegs(
        True, "ALREADY_TERMINAL", "TARGET_ERROR_WINS"
    )


def test_cancel_standalone_after_target_left_the_cancel_window():
    legs = _legs(
        [
            (10, "PUBLICATION_COMMIT", 200),
            (12, "GENERATE_TERMINAL_LATCHED", 200),
            (15, "CQ_CONSUME", 200),
            (20, "LOCAL_VISIBLE", 100),
            (30, "SQ_CONSUME", 200),
            (40, "SQ_CONSUME", 100),
        ]
    )
    assert legs == CancelLegs(False, "ALREADY_TERMINAL", None)


def test_cancel_standalone_when_target_cq_lands_on_the_intent_edge():
    legs = _legs(
        [
            (10, "PUBLICATION_COMMIT", 200),
            (12, "GENERATE_TERMINAL_LATCHED", 200),
            (20, "LOCAL_VISIBLE", 100),
            (20, "CQ_CONSUME", 200),
            (30, "SQ_CONSUME", 200),
            (40, "SQ_CONSUME", 100),
        ]
    )
    assert legs == CancelLegs(False, "ALREADY_TERMINAL", None)


def test_cancel_standalone_when_target_unpublished_at_local_commit():
    legs = _legs(
        [
            (20, "LOCAL_VISIBLE", 100),
            (40, "SQ_CONSUME", 100),
            (50, "SQ_CONSUME", 200),
            (60, "PUBLICATION_COMMIT", 200),
        ]
    )
    assert legs == CancelLegs(False, "NOT_FOUND", None)


def test_cancel_local_rollback_needs_no_acceptance_evidence():
    legs = _legs([(20, "LOCAL_VISIBLE", 100)], local_submit_failed=True)
    assert legs == CancelLegs(False, "LOCAL_SUBMIT_FAILED", None)


def test_cancel_missing_acceptance_is_a_facts_gap():
    entries = [(20, "LOCAL_VISIBLE", 100)]
    with pytest.raises(Gate4OracleError):
        _legs(entries)
    assert _legs(entries, cut=True) is None


def test_cancel_rollback_with_npu_acceptance_is_rejected():
    with pytest.raises(Gate4OracleError):
        _legs(
            [(20, "LOCAL_VISIBLE", 100), (40, "SQ_CONSUME", 100)],
            local_submit_failed=True,
        )


def test_cancel_target_consumed_without_latch_is_rejected():
    with pytest.raises(Gate4OracleError):
        _legs(
            [
                (10, "PUBLICATION_COMMIT", 200),
                (15, "CQ_CONSUME", 200),
                (20, "LOCAL_VISIBLE", 100),
                (30, "SQ_CONSUME", 200),
                (40, "SQ_CONSUME", 100),
            ]
        )


def test_cancel_join_with_unknown_target_is_rejected():
    with pytest.raises(Gate4OracleError):
        _legs(
            [
                (10, "PUBLICATION_COMMIT", 200),
                (20, "LOCAL_VISIBLE", 100),
                (40, "SQ_CONSUME", 100),
                (50, "SQ_CONSUME", 200),
            ]
        )


def test_cancel_rejects_generate_latch_after_cq_assignment(tmp_path):
    with pytest.raises(Gate4OracleError):
        _oracle_on_text(
            execution("three_user", "cancel_live"),
            _cancel_join_facts(latch_tick=17),
            tmp_path,
        )


def _first_pass_environment(outdir):
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AI_MESH_")
    }


def test_first_pass_e2e_oracle_and_tamper_negatives(tmp_path):
    if not GEM5.is_file():
        pytest.skip("gem5.opt is not built")
    run = subprocess.run(
        [
            str(GEM5),
            f"--outdir={tmp_path / 'run'}",
            str(CONFIG),
            "--plan-image",
            str(FIXTURES / "agent_plan_image_su_first_pass.bin"),
            "--sim-tick-limit",
            "40000000000",
        ],
        cwd=REPO,
        env=_first_pass_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=570,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    facts_path = tmp_path / "run" / "gate4_facts.tsv"
    oracle = Gate4RunOracle(execution("su_first_pass"), facts_path)
    results = oracle.run()
    assert all(results.values()), results
    rows, ownership, unattributed, matched = oracle.traffic_artifact_rows()
    assert matched and unattributed == 0

    text = facts_path.read_text()
    byte_line = next(
        line for line in text.splitlines()
        if line.split("|")[3] == "AXI_ACCEPT"
        and line.split("|")[13] == "OUTPUT"
        and line.split("|")[11] == "W"
    )
    fields = byte_line.split("|")
    fields[18] = str(int(fields[18]) + 64)
    tampered = tmp_path / "tampered.tsv"
    tampered.write_text(text.replace(byte_line, "|".join(fields)))
    tampered_oracle = Gate4RunOracle(execution("su_first_pass"), tampered)
    assert not tampered_oracle.check_traffic_conservation()

    outcome = tmp_path / "outcome.tsv"
    outcome.write_text(text.replace("|BUSINESS_DONE", "|INFRA_FAILED"))
    outcome_oracle = Gate4RunOracle(execution("su_first_pass"), outcome)
    assert not outcome_oracle.check_task_outcome_closure()

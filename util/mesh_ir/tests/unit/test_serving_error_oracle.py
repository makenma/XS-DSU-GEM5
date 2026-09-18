import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "configs" /
                   "example" / "ai_mesh"))

from mesh_ir.abi.decoder import decode_program  # noqa: E402
from mesh_ir.agent_workload import load_workload_plan  # noqa: E402
from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.gate3_oracle import read_facts_document  # noqa: E402
from mesh_ir.serving_error_oracle import (  # noqa: E402
    frozen_request_identity,
    planned_instance_cores,
    typed_flag,
    typed_tick,
    verify_error_drain,
)

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "tests/gem5/ai_mesh/fixtures/gate6/error_evidence"
PROGRAM = REPO / "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"
WORKLOAD = REPO / "tests/gem5/ai_mesh/fixtures/gate6/serving_workload_plan.json"
ARCH_YAML = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
FAULT_CORE = 0
COMMITTED_PHASES = 2
TERMINAL_KINDS = ("GENERATE_TERMINAL_LATCHED", "CQ_ASSIGN", "TASK_TERMINAL")
IDENTITY_KINDS = ("CORE_START", "CQ_OBLIGATION_RESERVE") + TERMINAL_KINDS + \
    ("TERMINAL_READY",)


def stored_result():
    return json.loads((FIXTURE / "mesh_result.json").read_text("utf-8"))


def stored_events():
    return read_facts_document(FIXTURE / "gate6_facts.tsv").events


def frozen_identity():
    return frozen_request_identity(load_workload_plan(WORKLOAD),
                                   "EXPLICIT_ONLY")


def verify(result, events=None):
    request_id, cookie = frozen_identity()
    return verify_error_drain(
        result,
        decode_program(PROGRAM.read_bytes()),
        list(load_arch(ARCH_YAML).core_ids),
        FAULT_CORE,
        COMMITTED_PHASES,
        stored_events() if events is None else events,
        request_id,
        cookie,
    )


def fault_core_row(result):
    return next(row for row in result["cores"]
                if row["core_id"] == FAULT_CORE)


def fault_bridge(result):
    return next(row for row in result["bridges"]
                if row["core_id"] == FAULT_CORE)


def test_real_error_evidence_passes_the_post_drain_oracle():
    assert verify(stored_result()) == []


def test_typed_evidence_helpers_reject_booleans_and_ranges():
    assert typed_tick(5, minimum=1) == 5
    assert typed_tick(True, minimum=1) is None
    assert typed_tick(0, minimum=1) is None
    assert typed_tick("5", minimum=1) is None
    assert typed_flag(1) == 1
    assert typed_flag(True) is None
    assert typed_flag(1.0) is None
    assert typed_flag(0.0) is None
    assert typed_flag("1") is None
    assert typed_flag(2) is None


def test_independent_terminal_evidence_counterexamples_are_rejected():
    result = copy.deepcopy(stored_result())
    result.pop("bridges")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["instances"].pop()
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_core_row(result)["work_drained_tick"] = 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    for event in events:
        if event["kind"] in TERMINAL_KINDS:
            event["request_id"] = 999
            if event["cookie"] is not None:
                event["cookie"] = 999
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    task = next(event for event in events
                if event["kind"] == "TASK_TERMINAL")
    for event in events:
        if event["kind"] == "GENERATE_TERMINAL_LATCHED":
            event["tick"] = task["tick"] + 1
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    for instance in result["instances"][:-1]:
        instance["cores"] = {}
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["error_drained"] = 1.0
    for core in result["cores"]:
        core["dma_idle"] = 1.0
    for bridge in result["bridges"]:
        bridge["idle"] = 1.0
    assert verify(result)


def test_joint_anchor_and_terminal_identity_tamper_is_rejected():
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    for event in events:
        if event["kind"] in IDENTITY_KINDS:
            event["request_id"] = 999
            if event["cookie"] is not None:
                event["cookie"] = 999
    assert verify(result, events)


def test_frozen_identity_comes_from_the_command_plan():
    request_id, cookie = frozen_identity()
    assert request_id == 1
    assert cookie == request_id
    program = decode_program(PROGRAM.read_bytes())
    cores = planned_instance_cores(program)
    assert cores == [[FAULT_CORE]] * 4


def test_missing_core_or_bridge_set_is_rejected():
    result = copy.deepcopy(stored_result())
    result.pop("cores")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["bridges"] = []
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["bridges"] = result["bridges"][:-1]
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["bridges"].append(copy.deepcopy(result["bridges"][0]))
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["bridges"][0]["core_id"] = 99
    assert verify(result)


def test_missing_final_instance_is_rejected():
    result = copy.deepcopy(stored_result())
    result["instances"][-1]["instance"] = \
        result["instances"][-2]["instance"]
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_instance"] = result["instances"][-2]["instance"]
    assert verify(result)


def test_terminal_identity_is_required():
    result = copy.deepcopy(stored_result())
    result.pop("terminal_instance")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_error_core"] = 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_error_core"] = True
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["error_drained"] = True
    assert verify(result)


def test_required_ticks_are_typed_and_ordered():
    result = copy.deepcopy(stored_result())
    result.pop("terminal_tick")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_tick"] = True
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_tick"] = 0
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_core_row(result).pop("error_latch_tick")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_core_row(result)["work_drained_tick"] = \
        fault_core_row(result)["error_latch_tick"] - 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal_tick"] = fault_core_row(result)["work_drained_tick"] - 1
    assert verify(result)


def test_only_the_terminal_enum_without_drain_proof_is_rejected():
    result = copy.deepcopy(stored_result())
    fault_core_row(result).pop("work_drained_tick")
    result.pop("bridges")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["cores"] = [row for row in result["cores"]
                       if row["core_id"] != FAULT_CORE]
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["terminal"] = "DONE"
    assert verify(result)


def test_live_traffic_at_the_error_terminal_is_rejected():
    result = copy.deepcopy(stored_result())
    fault_bridge(result)["outstanding_reads"] = 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_bridge(result)["pending_aw"] = 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_bridge(result)["idle"] = 0
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_bridge(result)["outstanding_writes"] = True
    assert verify(result)
    result = copy.deepcopy(stored_result())
    fault_core_row(result)["dma_idle"] = 0
    assert verify(result)


def test_instance_ledgers_must_cover_the_frozen_participants():
    result = copy.deepcopy(stored_result())
    result["instances"][0]["cores"].pop(str(FAULT_CORE))
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["instances"][0]["cores"]["1"] = {"completed": 0, "errored": 0,
                                            "cancelled": 0}
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["instances"][-1]["cores"][str(FAULT_CORE)].pop("errored")
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["instances"][0]["cores"][str(FAULT_CORE)]["completed"] = True
    assert verify(result)


def test_terminal_events_require_the_admitted_identity():
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    events = [event for event in events
              if event["kind"] != "CQ_ASSIGN"]
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    events.append(copy.deepcopy(next(event for event in events
                                     if event["kind"] == "CQ_ASSIGN")))
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    for event in events:
        if event["kind"] == "CQ_ASSIGN":
            event["cookie"] = 999
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    starts = [event for event in events if event["kind"] == "CORE_START"]
    starts[0]["request_id"] = 999
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    reserves = [event for event in events
                if event["kind"] == "CQ_OBLIGATION_RESERVE"]
    reserves[0]["cookie"] = 999
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = [event for event in copy.deepcopy(stored_events())
              if event["kind"] != "CORE_START"]
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    for event in events:
        if event["kind"] == "GENERATE_TERMINAL_LATCHED":
            event["cookie"] = 1
    assert verify(result, events)


def test_terminal_causality_chain_is_enforced():
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    events = [event for event in events
              if event["kind"] != "TERMINAL_READY"]
    assert verify(result, events)
    result = copy.deepcopy(stored_result())
    events = copy.deepcopy(stored_events())
    cq = next(event for event in events if event["kind"] == "CQ_ASSIGN")
    for event in events:
        if event["kind"] == "TERMINAL_READY":
            event["tick"] = cq["tick"] + 1
    assert verify(result, events)


def test_error_identity_is_attributed_to_the_fault_core():
    result = copy.deepcopy(stored_result())
    other = next(row for row in result["cores"]
                 if row["core_id"] != FAULT_CORE)
    other["error_latch_tick"] = fault_core_row(result)["error_latch_tick"]
    other["instance_error"] = 1
    assert verify(result)
    result = copy.deepcopy(stored_result())
    result["instances"][0]["cores"][str(FAULT_CORE)]["errored"] = 1
    assert verify(result)

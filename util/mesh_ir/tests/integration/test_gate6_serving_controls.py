import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from mesh_ir.runtime_environment import embedded_python_path
from mesh_ir.agent_config import load_agent_runtime_config, release_policy_of
from mesh_ir.agent_planning import build_command_identity_plan
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.gate3_oracle import read_facts_document


REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate6"
WEIGHT_DIGEST = "d3c1ec48f8238e96c579ad087d218b8510ca81c9c15f9b05d97b99111fd789b2"


def _run_control(tmp_path, trigger, opcode, auto_release=False):
    workload = load_workload_plan(FIXTURES / "serving_workload_plan.json")
    control_path = tmp_path / "control.json"
    target = ({"target_user_id": 0, "target_task_seq": 0, "target_repair_round": 0}
              if opcode == "CANCEL" else
              {"target_session_id": 1, "target_kv_handle": 1, "target_generation": 1})
    control_path.write_text(json.dumps({
        "schema": "agent_control_plan_v1", "version": 1,
        "workload_plan_digest": workload.digest,
        "actions": [{
            "control_ordinal": 1, "opcode": opcode,
            "issuer_user_id": 0, "issuer_task_seq": 0,
            **target,
            "trigger": {
                "kind": trigger, "event_user_id": 0,
                "event_task_seq": 0, "event_repair_round": 0,
            },
        }],
    }))
    config = yaml.safe_load((FIXTURES / "serving_runtime_config.yaml").read_text())
    config["agent"]["workload_plan"] = str(FIXTURES / "serving_workload_plan.json")
    config["agent"]["control_plan"] = str(control_path)
    config["agent"]["release_session_on_task_terminal"] = auto_release
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    control = load_control_plan(control_path, workload)
    identity = build_command_identity_plan(
        workload, control, release_policy_of(load_agent_runtime_config(config_path)))
    generate_id = next(item["request_id"] for item in identity["records"]
                       if item["command_kind"] == "GENERATE")
    control_id = next(item["request_id"] for item in identity["records"]
                      if item["command_kind"] == opcode and item["control_ordinal_or_zero"] == 1)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AI_MESH_", "GATE6_"))}
    env["AI_MESH_ARTIFACT_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = embedded_python_path()
    argv = [
        str(REPO / "build/AXI_MESH/gem5.opt"), "--outdir=" + str(tmp_path),
        "configs/example/ai_mesh/run_dummy_core_agent.py",
        "--case=gate6_serving_e2e_d", "--master-seed=20260901",
        "--sim-tick-limit=10000000000",
        "--serving-program", str(FIXTURES / "serving_two_tokens.mshb"),
        "--runtime-config", str(config_path),
        "--surrogate-profiles", str(FIXTURES / "serving_surrogate_profiles.json"),
        "--weight-image-digest", WEIGHT_DIGEST,
    ]
    with (tmp_path / "console.log").open("w") as output:
        process = subprocess.run(argv, cwd=REPO, env=env, stdout=output,
                                 stderr=subprocess.STDOUT, timeout=180)
    assert process.returncode == 0, (tmp_path / "console.log").read_text()
    facts = read_facts_document(tmp_path / "gate6_facts.tsv")
    assert facts.fatal is None
    mesh = json.loads((tmp_path / "mesh_result.json").read_text())
    assert mesh["terminal"] == "DONE"
    return facts, mesh, generate_id, control_id


@pytest.mark.parametrize("trigger", ["AFTER_SQ_ACCEPT", "AFTER_GENERATE_TERMINAL"])
def test_release_control_consumes_the_real_owner_terminal(tmp_path, trigger):
    facts, mesh, generate_id, release_id = _run_control(tmp_path, trigger, "RELEASE_SESSION")
    assert len(facts.serving_phases) == 4
    assert facts.metrics["metadata_reads_validated"] == 1
    for request_id in (generate_id, release_id):
        cq = [event for event in facts.events
              if event["kind"] == "CQ_ASSIGN" and event["request_id"] == request_id]
        assert len(cq) == 1
        assert cq[0]["status"] == "SUCCESS"
    resolved = [event for event in facts.events
                if event["kind"] == "CONTROL_RESOLVE" and
                event["request_id"] == release_id]
    assert len(resolved) == 1
    assert resolved[0]["status"] == "SUCCESS"
    assert resolved[0]["tick"] >= facts.serving_phases[-1]["commit_tick"]
    assert mesh["terminal_tick"] >= resolved[0]["tick"]
    if trigger == "AFTER_SQ_ACCEPT":
        deferred = [event for event in facts.events
                    if event["kind"] == "CONTROL_DEFERRED" and
                    event["request_id"] == release_id]
        assert len(deferred) == 1
        assert deferred[0]["tick"] < facts.serving_phases[-1]["commit_tick"]
        assert facts.metrics["kv_release_pending"] == 0
    assert facts.metrics["kv_session_records"] == 0


@pytest.mark.parametrize("trigger", ["AFTER_SQ_ACCEPT", "AFTER_GENERATE_TERMINAL"])
def test_cancel_control_preserves_the_frozen_instance_until_drain(tmp_path, trigger):
    facts, mesh, generate_id, cancel_id = _run_control(tmp_path, trigger, "CANCEL")
    live = trigger == "AFTER_SQ_ACCEPT"
    for request_id, status in (
        (generate_id, "CANCELLED" if live else "SUCCESS"),
        (cancel_id, "SUCCESS" if live else "ALREADY_TERMINAL"),
    ):
        cq = [event for event in facts.events
              if event["kind"] == "CQ_ASSIGN" and event["request_id"] == request_id]
        assert len(cq) == 1
        assert cq[0]["status"] == ("SUCCESS" if status == "SUCCESS" else "ERROR")
        consumed = [event for event in facts.events
                    if event["kind"] == "CQ_CONSUME" and event["request_id"] == request_id]
        assert len(consumed) == 1
        assert consumed[0]["status"] == status
        assert consumed[0]["tick"] >= cq[0]["tick"]
        if request_id == generate_id:
            assert cq[0]["tick"] >= facts.serving_phases[-1]["core_drain_tick"]
            assert mesh["terminal_tick"] >= consumed[0]["tick"]
    assert mesh["instances"]
    assert mesh["watchdog_fired"] == 0
    if live:
        target_ready = [event for event in facts.events
                        if event["kind"] == "TERMINAL_READY" and
                        event["request_id"] == generate_id]
        resolved = [event for event in facts.events
                    if event["kind"] == "CONTROL_RESOLVE" and
                    event["request_id"] == cancel_id]
        assert len(target_ready) == len(resolved) == 1
        assert resolved[0]["tick"] >= target_ready[0]["tick"]
        assert len(facts.serving_phases) == 1
        assert len(mesh["instances"]) == 1
        assert facts.metrics["kv_cached_tokens"] == 8
        assert facts.metrics["metadata_reads_validated"] == 1
        assert facts.metrics.get("generated_code_reads_validated", 0) == 0
    else:
        assert len(facts.serving_phases) == 4
        assert facts.metrics["metadata_reads_validated"] == 1

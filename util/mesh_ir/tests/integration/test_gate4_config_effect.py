import configparser
import dataclasses
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build" / "AXI_MESH" / "gem5.opt"
FIXTURES = REPO / "tests" / "gem5" / "ai_mesh" / "fixtures" / "gate4"
AGENT_SCRIPT = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_agent.py"
PROTOCOL_SCRIPT = REPO / "configs" / "example" / "ai_mesh" / "run_gate4_protocol.py"
sys.path.insert(0, str(REPO / "configs" / "example" / "ai_mesh"))
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from gate3_acceptance import parse_facts_tsv as parse_gate3_facts
from mesh_ir.agent_planning import ArenaRegion
from mesh_ir.agent_plan_image import build_agent_plan_image
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import load_workload_plan

SIM_TICK_LIMIT = 40000000000
SIM_TICK_LIMIT_THREE_USER = 400000000000
TICKS_PER_NS = 1000

ARENA_REGIONS = (
    ArenaRegion("INPUT", 0x0000000101000000, 67108864, 32),
    ArenaRegion("PARAMETER", 0x0000000100100000, 8388608, 8),
    ArenaRegion("OUTPUT", 0x0000000105000000, 67108864, 32),
    ArenaRegion("METADATA", 0x0000000109000000, 8388608, 8),
)


def run_gem5(script, outdir, arguments):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AI_MESH_")
    }
    return subprocess.run(
        [str(GEM5), f"--outdir={outdir}", str(script), *arguments],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def run_agent_config(config_path, profiles_path, outdir, tick_limit=None):
    return run_gem5(
        AGENT_SCRIPT,
        outdir,
        [
            "--runtime-config",
            str(config_path),
            "--surrogate-profiles",
            str(profiles_path),
            "--sim-tick-limit",
            str(tick_limit or SIM_TICK_LIMIT),
        ],
    )


def copy_config(source_name, workdir, replacements):
    text = (FIXTURES / source_name).read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in text, f"{source_name} does not declare {old!r}"
        text = text.replace(old, new)
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / source_name
    target.write_text(text, encoding="utf-8")
    for reference in re.findall(r"workload_plan: (.+)", text):
        fixture = FIXTURES / Path(reference.strip()).name
        (workdir / fixture.name).write_bytes(fixture.read_bytes())
    return target


def parse_ini(outdir):
    parser = configparser.ConfigParser()
    parser.read(outdir / "config.ini")
    return parser


def events_of(facts_path):
    events, final, metrics, fatal = parse_gate3_facts(facts_path)
    assert fatal is None
    return events, final, metrics


def semantic_skeleton(events):
    return [
        (event["kind"], event["object"], event["request_id"], event["status"],
         event["tick"])
        for event in events
        if event["kind"] != "AXI_ACCEPT"
    ]


def test_runtime_config_drives_simobjects_and_changes_facts(tmp_path):
    profiles = FIXTURES / "agent_surrogate_profiles_three_user.json"
    baseline_config = copy_config(
        "agent_runtime_config_three_user.yaml",
        tmp_path / "base",
        [],
    )
    changed_config = copy_config(
        "agent_runtime_config_three_user.yaml",
        tmp_path / "changed",
        [
            ("clock: 1GHz", "clock: 500MHz"),
            ("compile_slots: 4", "compile_slots: 1"),
            ("fixed_latency_ns: 1000", "fixed_latency_ns: 10000000"),
        ],
    )
    tight_config = copy_config(
        "agent_runtime_config_three_user.yaml",
        tmp_path / "tight",
        [
            ("sq_entries: 8", "sq_entries: 4"),
            ("cq_entries: 8", "cq_entries: 4"),
        ],
    )

    facts = {}
    tick_limits = {
        "base": SIM_TICK_LIMIT_THREE_USER,
        "changed": SIM_TICK_LIMIT_THREE_USER,
        "tight": SIM_TICK_LIMIT_THREE_USER,
    }
    for name, config in (
        ("base", baseline_config),
        ("changed", changed_config),
        ("tight", tight_config),
    ):
        run = run_agent_config(
            config, profiles, tmp_path / name / "run", tick_limits[name]
        )
        assert run.returncode == 0, run.stdout + run.stderr
        assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
        facts[name] = (tmp_path / name / "run" / "gate4_facts.tsv").read_bytes()

    assert facts["base"] != facts["changed"]
    assert facts["base"] != facts["tight"]
    assert facts["changed"] != facts["tight"]

    changed_ini = parse_ini(tmp_path / "changed" / "run")
    driver = changed_ini["system.gate4_driver"]
    frontend = changed_ini["system.gate4_frontend"]
    clock = changed_ini["system.gate4_driver.clk_domain"]
    address_map = {
        "sq_ring_base": 4295032832,
        "cq_ring_base": 4295098368,
        "msi_address": 4294967552,
        "npu_control_page_base": 206158430208,
        "host_control_page_base": 4294967296,
    }
    assert int(driver["sq_depth"]) == 8
    assert int(driver["cq_depth"]) == 8
    assert int(frontend["sq_depth"]) == 8
    assert int(driver["host_compile_slots"]) == 1
    assert int(clock["clock"]) == 2000
    assert int(changed_ini["system.clk_domain"]["clock"]) == 1000
    assert int(driver["sq_ring_base"]) == address_map["sq_ring_base"]
    assert int(driver["cq_ring_base"]) == address_map["cq_ring_base"]
    assert int(driver["msi_base"]) == address_map["msi_address"]
    assert int(driver["npu_control_base"]) == address_map["npu_control_page_base"]
    assert int(driver["host_base"]) == 4294967296
    assert int(driver["host_local_io_fixed_ns"]) == 10000000
    assert int(driver["host_available_fraction_q16"]) == 52428
    assert int(frontend["msi_axi_id_count"]) == 16
    assert int(frontend["kv_session_record_entries"]) == 8

    events, final, metrics = events_of(
        tmp_path / "changed" / "run" / "gate4_facts.tsv"
    )
    compile_spans = []
    starts = {}
    for event in events:
        if event["kind"] == "HOST_STAGE_START" and event["object"] == "COMPILE":
            starts[event["request_id"]] = event["tick"]
        if event["kind"] == "HOST_STAGE_DONE" and event["object"] == "COMPILE":
            compile_spans.append(event["tick"] - starts[event["request_id"]])
    assert compile_spans
    for span in compile_spans:
        assert span >= 10 * 1000 * 1000 * TICKS_PER_NS - 1
    assert metrics["completed_tasks"] + metrics["failed_tasks"] == 3

    tight_ini = parse_ini(tmp_path / "tight" / "run")
    assert int(tight_ini["system.gate4_driver"]["host_compile_slots"]) == 4
    assert int(tight_ini["system.gate4_driver"]["sq_depth"]) == 4
    assert int(tight_ini["system.gate4_frontend"]["cq_depth"]) == 4
    assert int(tight_ini["system.gate4_driver.clk_domain"]["clock"]) == 1000


def test_baseline_config_run_matches_probe_path_semantics(tmp_path):
    plan_image = FIXTURES / "agent_plan_image_su_first_pass.bin"
    probe_run = run_gem5(
        PROTOCOL_SCRIPT,
        tmp_path / "probe",
        ["--plan-image", str(plan_image), "--sim-tick-limit", str(SIM_TICK_LIMIT)],
    )
    assert probe_run.returncode == 0, probe_run.stdout + probe_run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in probe_run.stdout

    agent_run = run_agent_config(
        FIXTURES / "agent_runtime_config_su_first_pass.yaml",
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json",
        tmp_path / "agent",
    )
    assert agent_run.returncode == 0, agent_run.stdout + agent_run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in agent_run.stdout

    probe_events, probe_final, _ = events_of(tmp_path / "probe" / "gate4_facts.tsv")
    agent_events, agent_final, _ = events_of(tmp_path / "agent" / "gate4_facts.tsv")
    assert semantic_skeleton(probe_events) == semantic_skeleton(agent_events)
    assert probe_final == agent_final

    repeat_run = run_agent_config(
        FIXTURES / "agent_runtime_config_su_first_pass.yaml",
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json",
        tmp_path / "repeat",
    )
    assert repeat_run.returncode == 0, repeat_run.stdout + repeat_run.stderr
    assert (
        tmp_path / "repeat" / "gate4_facts.tsv").read_bytes() == (
        tmp_path / "agent" / "gate4_facts.tsv").read_bytes()

    agent_ini = parse_ini(tmp_path / "agent")
    driver = agent_ini["system.gate4_driver"]
    assert int(driver["sq_depth"]) == 8
    assert int(driver["cq_depth"]) == 8
    assert int(driver["host_base"]) == 4294967296
    assert driver["stop_accepting_enabled"] in ("false", "0")
    doorbells = [
        event
        for event in agent_events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "SQ_DOORBELL"
        and event["channel"] == "AW"
    ]
    assert doorbells


def test_profile_registry_mismatch_fails_fast_at_load(tmp_path):
    document = json.loads(
        (FIXTURES / "agent_surrogate_profiles_su_first_pass.json").read_text()
    )
    document["profiles"][0]["input_bytes"] = 8192
    document["profiles"][0]["output_bytes"] = 2048
    mutated = tmp_path / "profiles.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")

    run = run_agent_config(
        FIXTURES / "agent_runtime_config_su_first_pass.yaml",
        mutated,
        tmp_path / "run",
    )
    assert run.returncode != 0
    assert "E_WORKLOAD_PLAN_MISMATCH" in run.stderr + run.stdout
    assert not (tmp_path / "run" / "gate4_facts.tsv").exists()


def build_registry_image(registry, target: Path) -> None:
    workload = load_workload_plan(
        FIXTURES / "agent_workload_plan_su_first_pass.json"
    )
    image, _ = build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", ARENA_REGIONS, registry
    )
    target.write_bytes(image)


def test_frontend_admission_rejects_profile_mismatch(tmp_path):
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json"
    )
    mutated = dataclasses.replace(
        registry,
        profiles=(
            dataclasses.replace(
                registry.profiles[0], input_bytes=8192, output_bytes=2048
            ),
        ),
    )
    mutated_image = tmp_path / "mutated_image.bin"
    build_registry_image(mutated, mutated_image)

    run = run_gem5(
        PROTOCOL_SCRIPT,
        tmp_path / "run",
        [
            "--plan-image",
            str(mutated_image),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
        ],
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
    events, final, metrics = events_of(tmp_path / "run" / "gate4_facts.tsv")

    rejects = [
        event
        for event in events
        if event["kind"] == "PARAMETER_REJECT"
    ]
    assert rejects
    assert all(
        event["status"] == "E_WORKLOAD_PLAN_MISMATCH" for event in rejects
    )
    details = [
        event for event in events if event["kind"] == "CQ_DETAIL"
    ]
    assert [event["status"] for event in details] == [
        "E_WORKLOAD_PLAN_MISMATCH"
    ]
    consumes = [event for event in events if event["kind"] == "CQ_CONSUME"]
    assert [event["status"] for event in consumes] == ["ERROR"]
    terminals = [
        event for event in events if event["kind"] == "TASK_TERMINAL"
    ]
    assert [event["status"] for event in terminals] == ["BUSINESS_FAILED"]
    assert metrics["failed_tasks"] == 1
    assert metrics["completed_tasks"] == 0
    output_writes = [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["channel"] == "AW"
        and event["control"] == "OUTPUT"
    ]
    assert output_writes == []
    success_cq = [
        event
        for event in events
        if event["kind"] == "CQ_CONSUME" and event["status"] == "SUCCESS"
    ]
    assert success_cq == []
    stage_starts = [
        event for event in events if event["kind"] == "HOST_STAGE_START"
    ]
    assert stage_starts == []

    positive_image = tmp_path / "positive_image.bin"
    build_registry_image(registry, positive_image)
    positive = run_gem5(
        PROTOCOL_SCRIPT,
        tmp_path / "positive",
        [
            "--plan-image",
            str(positive_image),
            "--sim-tick-limit",
            str(SIM_TICK_LIMIT),
        ],
    )
    assert positive.returncode == 0, positive.stdout + positive.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in positive.stdout
    positive_events, _, positive_metrics = events_of(
        tmp_path / "positive" / "gate4_facts.tsv"
    )
    terminals = [
        event for event in positive_events
        if event["kind"] == "TASK_TERMINAL"
    ]
    assert [event["status"] for event in terminals] == ["BUSINESS_DONE"]
    assert positive_metrics["completed_tasks"] == 1


def test_cutoff_zero_suppresses_all_submissions_and_terminates(tmp_path):
    zero_config = copy_config(
        "agent_runtime_config_su_first_pass.yaml",
        tmp_path / "zero",
        [("stop_accepting_new_tasks_at_tick: null",
          "stop_accepting_new_tasks_at_tick: 0")],
    )
    run = run_agent_config(
        zero_config,
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json",
        tmp_path / "zero" / "run",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "AI_MESH_GATE3_QUIESCENT_SUCCESS" in run.stdout
    exit_tick = re.search(r"Exiting @ tick (\d+)", run.stdout)
    assert exit_tick
    assert int(exit_tick.group(1)) < SIM_TICK_LIMIT
    events, final, metrics = events_of(
        tmp_path / "zero" / "run" / "gate4_facts.tsv"
    )
    assert not [
        event for event in events if event["kind"] == "TASK_THINK_READY"
    ]
    suppressed = [
        event for event in events if event["kind"] == "TASK_SUPPRESSED"
    ]
    assert [event["request_id"] for event in suppressed] == [0]
    assert not [
        event
        for event in events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "SQ_DOORBELL"
    ]
    assert not [
        event for event in events if event["kind"] == "CQ_CONSUME"
    ]
    assert not final["fatal"]

    null_ini = parse_ini(tmp_path / "zero" / "run")
    driver = null_ini["system.gate4_driver"]
    assert driver["stop_accepting_enabled"] in ("true", "1")
    assert int(driver["stop_accepting_at_tick"]) == 0

    null_run = run_agent_config(
        FIXTURES / "agent_runtime_config_su_first_pass.yaml",
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json",
        tmp_path / "null",
    )
    assert null_run.returncode == 0, null_run.stdout + null_run.stderr
    null_events, _, _ = events_of(tmp_path / "null" / "gate4_facts.tsv")
    null_ini = parse_ini(tmp_path / "null")
    driver = null_ini["system.gate4_driver"]
    assert driver["stop_accepting_enabled"] in ("false", "0")
    assert [
        event for event in null_events if event["kind"] == "TASK_THINK_READY"
    ]
    assert [
        event
        for event in null_events
        if event["kind"] == "AXI_ACCEPT"
        and event["control"] == "SQ_DOORBELL"
        and event["channel"] == "AW"
    ]


def test_arena_padding_overflow_fails_at_plan_build(tmp_path):
    workload = json.loads(
        (FIXTURES / "agent_workload_plan_su_first_pass.json").read_text()
    )
    workload["users"][0]["tasks"][0]["rounds"][0]["output_capacity_bytes"] = 1025
    arena_config = copy_config(
        "agent_runtime_config_su_first_pass.yaml",
        tmp_path / "arena",
        [("bytes: 67108864\n    metadata_arena:", "bytes: 1025\n    metadata_arena:")],
    )
    (tmp_path / "arena" / "agent_workload_plan_su_first_pass.json").write_text(
        json.dumps(workload), encoding="utf-8"
    )
    run = run_agent_config(
        arena_config,
        FIXTURES / "agent_surrogate_profiles_su_first_pass.json",
        tmp_path / "arena" / "run",
    )
    assert run.returncode != 0
    combined = run.stderr + run.stdout
    assert "E_ADDRESS_PLAN" in combined
    assert "OUTPUT" in combined
    assert not (tmp_path / "arena" / "run" / "gate4_facts.tsv").exists()

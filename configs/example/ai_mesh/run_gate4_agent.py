import argparse
import os
import sys
from pathlib import Path

import m5
from m5.util import addToPath, fatal

addToPath("../../")

REPO = Path(__file__).resolve().parents[3]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
if str(MESH_IR_ROOT) not in sys.path:
    sys.path.insert(0, str(MESH_IR_ROOT))

from mesh_ir.agent_config import (
    build_capacity_plan,
    load_agent_runtime_config,
    release_policy_of,
    validate_runtime_config,
)
from mesh_ir.agent_planning import (
    build_command_identity_plan,
    build_host_arena_object_plan,
    build_host_task_identity_plan,
)
from mesh_ir.agent_plan_image import build_agent_plan_image
from mesh_ir.agent_surrogate import (
    load_surrogate_profiles,
    validate_surrogate_profiles,
)
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.gate4_oracle import arena_regions

from gate4_runtime import (
    add_gate4_arguments,
    artifact_directory,
    assemble,
    define_gate4_options,
    normalize_gate4_arguments,
    run_simulation,
    validate_gate4_arguments,
)


def _plan_documents(args):
    config_path = Path(args.runtime_config).resolve()
    config = load_agent_runtime_config(config_path)
    agent = config.document["agent"]
    if agent["mode"] != "replay_plan":
        fatal("Gate4 agent runtime only accepts agent.mode=replay_plan")
    workload_path = config_path.parent / agent["workload_plan"]
    workload = load_workload_plan(workload_path)
    control = None
    if agent["control_plan"] is not None:
        control = load_control_plan(
            config_path.parent / agent["control_plan"], workload
        )
    surrogate = load_surrogate_profiles(Path(args.surrogate_profiles))
    validate_runtime_config(config, workload, surrogate)
    validate_surrogate_profiles(surrogate, workload)
    return config, workload, control, surrogate


def _knob(args, name, document_value):
    value = getattr(args, name)
    return document_value if value is None else value


def _apply_service_overrides(args, services):
    if args.host_compute_tokens is not None:
        services["compute_tokens"] = args.host_compute_tokens
        services["host_available_fraction_q16"] = 65536
    if args.host_service_queue_depth is not None:
        services["service_queue_depth"] = args.host_service_queue_depth
    if args.host_aging_threshold_ns is not None:
        services["aging_threshold_ns"] = args.host_aging_threshold_ns


def main():
    parser = argparse.ArgumentParser()
    define_gate4_options(parser)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--surrogate-profiles", required=True)
    parser.add_argument("--sim-tick-limit", type=int, required=True)
    add_gate4_arguments(parser)
    args = parser.parse_known_args()[0]
    if args.sim_tick_limit <= 0:
        fatal("Gate4 simulation tick limit must be positive")
    try:
        normalize_gate4_arguments(args)
        validate_gate4_arguments(args)
    except ValueError as error:
        fatal(str(error))

    config, workload, control, surrogate = _plan_documents(args)
    services = config.document["agent_axi_driver"]["synthetic_host_services"]
    _apply_service_overrides(args, services)
    runtime = config.document["runtime"]
    args.host_compute_tokens = services["compute_tokens"]
    args.host_service_queue_depth = services["service_queue_depth"]
    args.host_aging_threshold_ns = services["aging_threshold_ns"]
    args.stop_after_completed_tasks = _knob(
        args, "stop_after_completed_tasks", runtime["stop_after_completed_tasks"] or 0
    )
    stop_accepting = _knob(
        args,
        "stop_accepting_at_tick",
        runtime["stop_accepting_new_tasks_at_tick"],
    )
    args.stop_accepting_enabled = stop_accepting is not None
    args.stop_accepting_at_tick = stop_accepting or 0

    policy = release_policy_of(config)
    regions = arena_regions(config.document["serving"]["address_map"])
    identity = build_command_identity_plan(workload, control, policy)
    host_tasks = build_host_task_identity_plan(workload)
    arena = build_host_arena_object_plan(workload, control, policy, regions)
    capacity = build_capacity_plan(workload, control, config)
    args.request_id_capacity = max(64, len(identity["records"]))
    image, image_digest = build_agent_plan_image(
        workload, control, policy, regions, surrogate
    )

    from gate4_runtime import artifact_directory

    artifact_dir = artifact_directory()
    image_path = artifact_dir / "plan_image.bin"
    image_path.write_bytes(image)
    plans = {
        "runtime_config": config.digest,
        "workload_plan": workload.digest,
        "control_plan": (
            control.digest
            if control is not None
            else arena["control_plan_digest"]
        ),
        "command_identity": identity["command_identity_digest"],
        "host_task_identity": host_tasks["host_task_identity_digest"],
        "host_arena_object_plan": arena["host_arena_object_digest"],
        "capacity_plan": capacity["capacity_plan_digest"],
        "surrogate_profiles": surrogate.digest,
        "plan_image": image_digest,
    }

    assemble(
        args,
        str(image_path),
        config_document=config.document,
        accepted_queue_entries=capacity["counts"]["live_context_bound"],
    )
    event = run_simulation(args.sim_tick_limit)
    if os.environ.get("AI_MESH_CHILD_REPORT"):
        from gate4_acceptance import write_gate4_artifacts
        from gate4_runtime import config_hardware
        from mesh_ir.gate4_oracle import Gate4Execution, Gate4Faults

        hardware = config_hardware(config.document)

        faults = Gate4Faults(
            output_b_error_request=args.inject_output_b_error,
            output_b_error_segment=args.output_b_error_segment,
            control_doorbell_error_ordinal=args.inject_control_doorbell_b_error,
            mutate_cancel_command_ordinal=args.mutate_cancel_command_ordinal,
            mutate_cancel_command_status=args.mutate_cancel_command_status,
            host_fault_site=args.host_fault_site,
            host_fault_task=args.host_fault_task,
            host_fault_round=args.host_fault_round,
        )
        partial = (
            args.stop_after_completed_tasks > 0
            or args.stop_accepting_enabled
        )
        execution = Gate4Execution(
            workload,
            control,
            surrogate,
            regions,
            faults,
            partial,
            args.cancel_output_prefix_bytes,
        )
        write_gate4_artifacts(
            plans,
            execution,
            event.getCode(),
            artifact_dir / "gate4_facts.tsv",
            artifact_dir,
            os.environ["AI_MESH_CASE_ID"],
            os.environ["AI_MESH_SUBCASE"],
            hardware,
        )
    raise SystemExit(event.getCode())


main()

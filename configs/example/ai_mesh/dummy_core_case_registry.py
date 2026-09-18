from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "util" / "mesh_ir"))
sys.path.insert(0, str(ROOT / "configs" / "example" / "ai_mesh"))

from mesh_ir.gate3_contract import GATE3_CASES
from mesh_ir.gate4_contract import GATE4_CASES
from gate3_profiles import profile_for


class Backend(Enum):
    MOCK = "MOCK"
    GARNET = "GARNET"
    GATE3 = "GATE3"
    GATE4 = "GATE4"
    GATE5 = "GATE5"
    GATE6 = "GATE6"


@dataclass(frozen=True)
class CaseDefinition:
    backend: Backend
    backend_case: str
    invariants: tuple[str, ...] = ()


BASE_GARNET_INVARIANTS = (
    "conservation",
    "quiescence",
    "completion_timing",
)


def _garnet(backend_case, *invariants):
    return CaseDefinition(
        Backend.GARNET,
        backend_case,
        (*BASE_GARNET_INVARIANTS, *invariants),
    )


CASES = {
    **{
        f"mock_{program}": CaseDefinition(Backend.MOCK, program)
        for program in (
            "single",
            "dual",
            "fill",
            "repeat",
            "poison",
            "sram_parallel",
            "sram_conflict",
            "poison_ew",
            "poison_store",
            "poison_reduce",
            "zero_dma",
            "dma_shapes",
            "compute_timing",
            "cross_error",
            "barrier_e2e",
        )
    },
    "dma_basic": _garnet("dma_basic", "instance_ledgers", "e2e_a_content"),
    "p2p_basic": _garnet(
        "p2p_basic", "instance_ledgers", "p2p_commit", "e2e_b_order"
    ),
    "dma_edge": _garnet("dma_edge", "edge_bytes"),
    "region_edge": _garnet("region_edge", "instance_ledgers", "e2e_a_content"),
    "delayed_b": _garnet("delayed_b", "b_reorder"),
    "p2p_delayed": _garnet("p2p_delayed", "p2p_commit", "recv_wait_order"),
    "read_error": _garnet("read_error", "read_error_drain"),
    "write_error": _garnet("write_error", "write_error_drain"),
    "fence": _garnet("fence", "fence_window"),
    "pin": _garnet("pin", "pin_serialize"),
    "constrained": _garnet("constrained", "p2p_commit"),
    "fence_scopes": _garnet("fence_scopes", "fence_scopes"),
    "cross_error": _garnet("cross_error", "cross_error_drain"),
    "cross_error_first_instance": CaseDefinition(
        Backend.GARNET,
        "cross_error_first_instance",
        ("quiescence", "completion_timing", "first_instance_error_only"),
    ),
    "repeat_error": _garnet("repeat_error", "repeat_error_drain"),
    "p2p_reuse": _garnet("p2p_reuse", "p2p_reuse_commits"),
    "dma_zero": _garnet("dma_zero"),
    "read_outstanding_window": _garnet(
        "read_outstanding_window", "read_window_slides"
    ),
    "load_saturation_contiguous": _garnet("load_saturation_contiguous"),
    "load_saturation_multi_tensor": _garnet("load_saturation_multi_tensor"),
    "load_saturation_strided": _garnet("load_saturation_strided"),
    "dma_shapes": _garnet(
        "dma_shapes", "p2p_commit", "shapes_content", "cross_core_order"
    ),
    "p2p_persist": _garnet(
        "p2p_persist", "instance_ledgers", "p2p_commit", "e2e_b_order"
    ),
    "sram_persist": _garnet(
        "sram_persist", "instance_ledgers", "e2e_a_content"
    ),
}


GATE5_CASES = {
    "moe_dual_cached": CaseDefinition(
        Backend.GATE5,
        "moe_dual_cached",
        (
            "quiescence",
            "completion_timing",
            "moe_cache_fill_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_basic": CaseDefinition(
        Backend.GATE5,
        "moe_dual_basic",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_drop": CaseDefinition(
        Backend.GATE5,
        "moe_dual_drop",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drop_fill",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_copy": CaseDefinition(
        Backend.GATE5,
        "moe_dual_copy",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_copy_through",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_cached_reuse_fault": CaseDefinition(
        Backend.GATE5,
        "moe_dual_cached_reuse_fault",
        (
            "quiescence",
            "moe_prestart_terminal",
        ),
    ),
    "moe_multi_cached": CaseDefinition(
        Backend.GATE5,
        "moe_multi_cached",
        (
            "quiescence",
            "completion_timing",
            "moe_cache_fill_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_quad": CaseDefinition(
        Backend.GATE5,
        "moe_quad",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_quad_hotspot": CaseDefinition(
        Backend.GATE5,
        "moe_quad_hotspot",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_hotspot_load",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_cached_reuse": CaseDefinition(
        Backend.GATE5,
        "moe_dual_cached_reuse",
        (
            "quiescence",
            "completion_timing",
            "moe_cache_reuse",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
    "moe_dual_timing": CaseDefinition(
        Backend.GATE5,
        "moe_dual_timing",
        (
            "quiescence",
            "completion_timing",
            "moe_overlay_traffic",
            "moe_canonical_projection",
            "moe_oracle_recompute",
            "moe_drain_state",
            "moe_gate_release",
            "moe_overlay_execution",
        ),
    ),
}
CASES.update(GATE5_CASES)


GATE6_CASES = {
    "gate6_serving_e2e_d": CaseDefinition(
        Backend.GATE6,
        "gate6_serving_e2e_d",
        (
            "quiescence",
            "completion_timing",
            "serving_phase_chain",
            "serving_unique_cq",
            "serving_host_compile",
        ),
    ),
}
CASES.update(GATE6_CASES)


GATE6_ARGS = {
    "gate6_serving_e2e_d": (
        "--serving-program",
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb",
        "--runtime-config",
        "tests/gem5/ai_mesh/fixtures/gate6/serving_runtime_config.yaml",
        "--surrogate-profiles",
        "tests/gem5/ai_mesh/fixtures/gate6/serving_surrogate_profiles.json",
        "--weight-image-digest",
        "d3c1ec48f8238e96c579ad087d218b8510ca81c9c15f9b05d97b99111fd789b2",
    ),
}


GATE3_PROFILES = {}
for _requirements in GATE3_CASES.values():
    for _requirement in _requirements:
        if _requirement.runner != "GEM5":
            continue
        CASES[_requirement.backend_case] = CaseDefinition(
            Backend.GATE3,
            _requirement.backend_case,
            _requirement.invariants,
        )
        GATE3_PROFILES[_requirement.backend_case] = profile_for(_requirement.name).name


GATE4_ARGS = {}
for _requirements in GATE4_CASES.values():
    for _requirement in _requirements:
        if _requirement.runner != "GEM5":
            continue
        CASES[_requirement.backend_case] = CaseDefinition(
            Backend.GATE4,
            _requirement.backend_case,
            _requirement.invariants,
        )
        GATE4_ARGS[_requirement.backend_case] = _requirement.manifest_args


MOCK_BASE_INVARIANTS = (
    "exit_reason",
    "traffic_conservation",
    "payload_flow",
    "command_conservation",
)

MOCK_VALUE_INVARIANTS = (
    ("--expect-ticks", "cycle_golden", lambda value: int(value) != 0),
    ("--expect-digest", "digest_golden", lambda value: value != ""),
    ("--expect-sram-service-min", "sram_service_minimum", lambda value: int(value) != 0),
    ("--expect-gemm-cycles", "gemm_cycle_formula", lambda value: int(value) >= 0),
)

MOCK_FLAG_INVARIANTS = (
    ("--expect-digests-differ", "digest_semantic_sensitivity"),
    ("--digest-stable-across-instances", "instance_digest_stability"),
    ("--assert-all-done", "all_commands_done"),
    ("--assert-event-visibility", "event_visibility"),
)


def _option_values(arguments, name):
    values = []
    for index, argument in enumerate(arguments):
        if argument == name and index + 1 < len(arguments):
            values.append(arguments[index + 1])
        elif argument.startswith(name + "="):
            values.append(argument.split("=", 1)[1])
    return values


def invariant_registry(case_name, arguments):
    definition = CASES[case_name]
    if definition.backend in (Backend.GARNET, Backend.GATE3, Backend.GATE4,
                              Backend.GATE5, Backend.GATE6):
        return definition.invariants
    names = list(MOCK_BASE_INVARIANTS)
    for option, name, enabled in MOCK_VALUE_INVARIANTS:
        values = _option_values(arguments, option)
        if values and any(enabled(value) for value in values):
            names.append(name)
    for option, name in MOCK_FLAG_INVARIANTS:
        if option in arguments:
            names.append(name)
    if _option_values(arguments, "--expect-command-latency"):
        names.append("command_latency")
    return tuple(names)


def garnet_scenario_cases():
    return (
        *backend_cases(Backend.GARNET),
        *backend_cases(Backend.GATE5),
    )


def backend_cases(backend):
    return tuple(
        name for name, definition in CASES.items() if definition.backend is backend
    )


def backend_programs(backend):
    return tuple(
        definition.backend_case
        for definition in CASES.values()
        if definition.backend is backend
    )


def invocation(case_name: str, arguments: list[str], sim_ticks: str) -> tuple[Path, list[str]]:
    definition = CASES[case_name]
    if definition.backend is Backend.MOCK:
        script = ROOT / "configs/example/ai_mesh/run_mesh_program.py"
        argv = [
            str(script),
            "--program",
            definition.backend_case,
            "--sim-tick-limit",
            sim_ticks,
        ]
    elif definition.backend in (Backend.GARNET, Backend.GATE5):
        script = ROOT / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
        argv = [
            str(script),
            "--case",
            definition.backend_case,
            "--axi-max-sim-ticks",
            sim_ticks,
        ]
    elif definition.backend is Backend.GATE6:
        script = ROOT / "configs/example/ai_mesh/run_gate6_serving.py"
        argv = [
            str(script),
            *GATE6_ARGS[case_name],
            "--sim-tick-limit",
            sim_ticks,
        ]
    elif definition.backend is Backend.GATE4:
        script = ROOT / "configs/example/ai_mesh/run_gate4_agent.py"
        argv = [
            str(script),
            *GATE4_ARGS[case_name],
            "--sim-tick-limit",
            sim_ticks,
        ]
    else:
        script = ROOT / "configs/example/ai_mesh/run_gate3_protocol.py"
        argv = [
            str(script),
            "--profile",
            GATE3_PROFILES[case_name],
            "--sim-tick-limit",
            sim_ticks,
        ]
    return script, argv + list(arguments)

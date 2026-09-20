from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "util" / "mesh_ir"))
sys.path.insert(0, str(ROOT / "configs" / "example" / "ai_mesh"))

from mesh_ir.gate3_contract import GATE3_CASES
from gate3_profiles import profile_for


class Backend(Enum):
    MOCK = "MOCK"
    GARNET = "GARNET"
    GATE3 = "GATE3"


@dataclass(frozen=True)
class CaseDefinition:
    backend: Backend
    backend_case: str
    invariants: tuple[str, ...] = ()


BASE_GARNET_INVARIANTS = (
    "conservation",
    "quiescence",
    "completion_timing",
    "burst_attribution",
    "packet_traffic",
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
            "fill_offset",
            "fill_view_offset",
            "fill_view_offset_out",
            "fill_view_offset_out_pad",
            "fill_view_offset_padding_zero",
            "fill_view_offset_padding_pattern",
            "fill_view_offset_row_gap_zero",
            "fill_view_offset_row_gap_pattern",
            "fill_view_offset_split",
            "fill_view_offset_split_zero",
            "fill_view_offset_split_pattern",
            "fill_view_offset_in0",
            "fill_view_offset_in0_out",
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
            "multi_descriptor",
            "pre_resident_weight",
            "dma_pin",
            "dma_fence",
            "engine_tensor_pressure",
            "engine_vector_pressure",
            "engine_reduce_pressure",
            "double_buffer_overlap",
            "double_buffer_serialized",
            "barrier_asymmetric",
            "p2p_multi_descriptor",
            "p2p_prefilled_destination",
            "p2p_cancel",
        )
    },
    "drain_stalled": _garnet("drain_stalled"),
    "dma_basic": _garnet("dma_basic", "reconciliation", "loader_zero_traffic",
                         "global_drain", "instance_ledgers", "e2e_a_content",
                         "destination_bytes", "compute_timing"),
    "p2p_basic": _garnet(
        "p2p_basic", "global_drain", "instance_ledgers", "p2p_commit",
        "e2e_b_order", "e2e_b_segments", "e2e_a_content", "destination_bytes", "p2p_unique_publish", "staged_commit_stages"
    ),
    "dma_edge": _garnet("dma_edge", "edge_bytes", "wstrb_sentinels"),
    "region_edge": _garnet("region_edge", "reconciliation", "wstrb_sentinels",
                           "instance_ledgers", "e2e_a_content",
                           "destination_bytes"),
    "delayed_b": _garnet("delayed_b", "b_reorder"),
    "delayed_b_ejection": _garnet("delayed_b_ejection", "b_reorder"),
    "p2p_delayed": _garnet("p2p_delayed", "p2p_commit", "p2p_unique_publish",
                           "staged_commit_stages", "recv_wait_order"),
    "read_error": _garnet("read_error", "reconciliation",
                          "cancelled_commands_issue_nothing", "read_error_drain"),
    "read_error_middle": _garnet(
        "read_error_middle", "reconciliation",
        "cancelled_commands_issue_nothing", "global_drain"
    ),
    "write_error": _garnet("write_error", "reconciliation",
                           "cancelled_commands_issue_nothing", "write_error_drain"),
    "fence": _garnet("fence", "fence_window"),
    "pin": _garnet("pin", "reconciliation", "pin_serialize"),
    "constrained": _garnet("constrained", "p2p_commit", "p2p_unique_publish"),
    "fence_scopes": _garnet("fence_scopes", "fence_scopes"),
    "cross_error": _garnet("cross_error", "reconciliation",
                           "cancelled_commands_issue_nothing", "cross_error_drain"),
    "cross_error_first_instance": CaseDefinition(
        Backend.GARNET,
        "cross_error_first_instance",
        ("reconciliation", "quiescence", "completion_timing",
         "burst_attribution", "first_instance_error_only"),
    ),
    "repeat_error": _garnet("repeat_error", "reconciliation",
                            "cancelled_commands_issue_nothing",
                            "repeat_error_drain"),
    "p2p_reuse": _garnet("p2p_reuse", "p2p_reuse_commits",
                         "p2p_unique_publish", "staged_commit_stages"),
    "p2p_partial_abandon": _garnet(
        "p2p_partial_abandon", "reconciliation",
        "cancelled_commands_issue_nothing", "p2p_partial_abandon"
    ),
    "p2p_prefilled": _garnet(
        "p2p_prefilled", "reconciliation", "transfer_snapshots",
        "p2p_unique_publish", "wstrb_sentinels"
    ),
    "p2p_prefilled_incomplete": _garnet(
        "p2p_prefilled_incomplete", "reconciliation",
        "cancelled_commands_issue_nothing", "transfer_snapshots",
        "p2p_partial_abandon"
    ),
    "dma_zero": _garnet("dma_zero"),
    "read_outstanding_window": _garnet(
        "read_outstanding_window", "read_window_slides"
    ),
    "lost_response": _garnet("lost_response"),
    "write_error_post_commit": _garnet(
        "write_error_post_commit", "reconciliation",
        "cancelled_commands_issue_nothing", "post_commit_landing"
    ),
    "drain_deferred": _garnet(
        "drain_deferred", "reconciliation", "global_drain", "instance_ledgers",
        "e2e_a_content", "destination_bytes"
    ),
    "read_reorder": _garnet(
        "read_reorder", "reconciliation", "r_completion_order",
        "global_drain", "read_window_slides"
    ),
    "load_saturation_contiguous": _garnet("load_saturation_contiguous",
                                          "global_drain"),
    "load_saturation_multi_tensor": _garnet("load_saturation_multi_tensor"),
    "load_saturation_strided": _garnet("load_saturation_strided"),
    "dma_shapes": _garnet(
        "dma_shapes", "reconciliation", "p2p_commit", "p2p_unique_publish",
        "staged_commit_stages", "wstrb_sentinels", "shapes_content",
        "e2e_b_order", "destination_bytes"
    ),
    "p2p_persist": _garnet(
        "p2p_persist", "global_drain", "instance_ledgers", "p2p_commit",
        "e2e_b_segments", "e2e_a_content", "destination_bytes",
        "p2p_unique_publish", "staged_commit_stages", "e2e_b_order"
    ),
    "dma_basic_shallow": _garnet(
        "dma_basic_shallow", "reconciliation", "loader_zero_traffic",
        "global_drain", "instance_ledgers", "e2e_a_content",
        "destination_bytes", "queue_bounds", "compute_timing"
    ),
    "p2p_frames_shallow": _garnet(
        "p2p_frames_shallow", "reconciliation", "global_drain",
        "instance_ledgers", "p2p_commit", "e2e_b_order", "e2e_b_segments",
        "p2p_unique_publish", "staged_commit_stages", "e2e_a_content",
        "destination_bytes", "queue_bounds", "compute_timing"
    ),
    "p2p_peer_edge": _garnet(
        "p2p_peer_edge", "reconciliation", "global_drain", "instance_ledgers",
        "p2p_commit", "p2p_unique_publish", "staged_commit_stages",
        "wstrb_sentinels", "peer_write_bursts", "destination_bytes"
    ),
    "p2p_frames": _garnet(
        "p2p_frames", "reconciliation", "global_drain", "instance_ledgers",
        "p2p_commit", "e2e_b_order", "e2e_b_segments", "p2p_unique_publish",
        "staged_commit_stages", "e2e_a_content", "destination_bytes", "compute_timing"
    ),
    "sram_persist": _garnet(
        "sram_persist", "reconciliation", "instance_ledgers", "e2e_a_content",
        "destination_bytes"
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
    if definition.backend in (Backend.GARNET, Backend.GATE3):
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
    elif definition.backend is Backend.GARNET:
        script = ROOT / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
        argv = [
            str(script),
            "--case",
            definition.backend_case,
            "--axi-max-sim-ticks",
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

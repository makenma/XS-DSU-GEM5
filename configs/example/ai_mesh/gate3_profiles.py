from dataclasses import dataclass


@dataclass(frozen=True)
class Gate3Profile:
    name: str
    sq_depth: int = 2
    cq_depth: int = 2
    request_count: int = 1
    local_visibility_delay: int = 0
    request_id_capacity: int = 8
    drain_cycles: int = 8
    sq_read_issue_delay: int = 0


_PROFILE_NAMES = {
    "depth1_wrap": "DEPTH1_WRAP",
    "depth2_wrap": "DEPTH2_WRAP",
    "sq_full": "SQ_FULL",
    "cq_full": "CQ_FULL",
    "stale_doorbell": "STALE_DOORBELL",
    "duplicate_doorbell": "DUPLICATE_DOORBELL",
    "empty_doorbell": "EMPTY_DOORBELL",
    "sq_read_error": "SQ_READ_ERROR",
    "sq_sequence_mismatch": "SQ_SEQUENCE_MISMATCH",
    "sq_crc": "SQ_CRC",
    "parameter_read_error": "PARAMETER_READ_ERROR",
    "parameter_crc": "PARAMETER_CRC",
    "parameter_bounds": "PARAMETER_BOUNDS",
    "parameter_envelope_long": "PARAMETER_ENVELOPE_LONG",
    "parameter_envelope_short": "PARAMETER_ENVELOPE_SHORT",
    "parameter_cross_4k": "PARAMETER_CROSS_4K",
    "parameter_narrow_beats": "PARAMETER_NARROW_BEATS",
    "parameter_binding_end": "PARAMETER_BINDING_END",
    "parameter_binding_oob": "PARAMETER_BINDING_OOB",
    "parameter_binding_header_overlap": "PARAMETER_BINDING_HEADER_OVERLAP",
    "parameter_binding_extension_overlap": "PARAMETER_BINDING_EXTENSION_OVERLAP",
    "parameter_binding_record_size": "PARAMETER_BINDING_RECORD_SIZE",
    "metadata_tlv_tail": "METADATA_TLV_TAIL",
    "metadata_cq_mismatch": "METADATA_CQ_MISMATCH",
    "metadata_session_mismatch": "METADATA_SESSION_MISMATCH",
    "metadata_user_mismatch": "METADATA_USER_MISMATCH",
    "metadata_task_mismatch": "METADATA_TASK_MISMATCH",
    "metadata_round_mismatch": "METADATA_ROUND_MISMATCH",
    "metadata_flags_unknown": "METADATA_FLAGS_UNKNOWN",
    "metadata_tlv_size_mismatch": "METADATA_TLV_SIZE_MISMATCH",
    "prompt_not_visible": "PROMPT_NOT_VISIBLE",
    "parameter_not_visible": "PARAMETER_NOT_VISIBLE",
    "sq_not_visible": "SQ_NOT_VISIBLE",
    "release_fence_pending": "RELEASE_FENCE_PENDING",
    "producer_commit": "PRODUCER_COMMIT",
    "early_normal_cq": "EARLY_NORMAL_CQ",
    "early_seq_only_cq": "EARLY_SEQ_ONLY_CQ",
    "early_cumulative_head": "EARLY_CUMULATIVE_HEAD",
    "proven_no_effect_b_error": "PROVEN_NO_EFFECT_B_ERROR",
    "same_edge_acceptance_conflict": "SAME_EDGE_ACCEPTANCE_CONFLICT",
    "ambiguous_b": "AMBIGUOUS_B",
    "pre_ar_intake_fatal": "PRE_AR_INTAKE_FATAL",
    "normal_sq_at_fatal_cut": "NORMAL_SQ_AT_FATAL_CUT",
    "sq_error_at_fatal_cut": "SQ_ERROR_AT_FATAL_CUT",
    "driver_commit_at_fatal_cut": "DRIVER_COMMIT_AT_FATAL_CUT",
    "delayed_prompt_r": "DELAY_PROMPT",
    "delayed_output_b": "DELAY_OUTPUT",
    "delayed_cq_b": "DELAY_CQ",
    "stale_cq_seq": "STALE_CQ_SEQ",
    "request_mismatch": "CQ_REQUEST_MISMATCH",
    "cookie_mismatch": "CQ_COOKIE_MISMATCH",
    "ack_before_target_commit": "ACK_BEFORE_TARGET_COMMIT",
    "bulk_saturation": "BULK_SATURATION",
    "directional_traffic": "DIRECTIONAL_TRAFFIC",
    "doorbell_sequence": "DOORBELL_SEQUENCE",
    "sq_head_sequence": "SQ_HEAD_SEQUENCE",
    "cq_tail_sequence": "CQ_TAIL_SEQUENCE",
    "cq_ack_sequence": "CQ_ACK_SEQUENCE",
    "msi_sequence": "MSI_SEQUENCE",
    "bad_response_order": "BAD_RESPONSE_ORDER",
    "sq_crc_identity": "SQ_CRC_IDENTITY",
    "parameter_error_identity": "PARAMETER_ERROR_IDENTITY",
    "delayed_metadata_b": "DELAY_METADATA",
    "output_capacity": "CAPACITY_OUTPUT",
    "metadata_capacity": "CAPACITY_METADATA",
    "cq_not_acked": "CQ_NOT_ACKED",
    "qos_out_of_order": "QOS_OUT_OF_ORDER",
    "same_tick_seq_only": "SAME_TICK_SEQ_ONLY",
    "cq_entry_b_error": "CQ_ENTRY_B_ERROR",
    "sq_head_b_error": "SQ_HEAD_B_ERROR",
    "cq_tail_b_error": "CQ_TAIL_B_ERROR",
    "msi_b_error": "MSI_B_ERROR",
    "msi_postcommit_ack_ok": "MSI_POSTCOMMIT_ACK_OK",
    "same_tick_completion_faults": "MSI_POSTCOMMIT_ACK_ERROR",
    "msi_postcommit_ack_delayed": "MSI_POSTCOMMIT_ACK_DELAYED",
    "second_sq_read_error": "SECOND_SQ_READ_ERROR",
    "ack_no_target_commit": "ACK_NO_TARGET_COMMIT",
    "ack_error_msi_before": "ACK_ERROR_MSI_BEFORE",
    "ack_error_msi_same": "ACK_ERROR_MSI_SAME",
    "ack_error_msi_after": "ACK_ERROR_MSI_AFTER",
    "ack_error_msi_late": "ACK_ERROR_MSI_LATE",

    "ack_after_target_commit": "ACK_POSTCOMMIT_ERROR",
    "local_store_visibility": "LOCAL_STORE_VISIBILITY",
    "release_fence": "RELEASE_FENCE",
    "local_store_no_fabric": "LOCAL_STORE_NO_FABRIC",
    "ack_commit_before_b": "ACK_COMMIT_BEFORE_B",
    "ack_no_target_commit_error": "ACK_NO_TARGET_COMMIT",
    "ack_postcommit_error": "ACK_POSTCOMMIT_ERROR",
    "control_windows": "CONTROL_WINDOWS",
    "early_ack": "EARLY_ACK",
    "msi_reverse_b": "MSI_REVERSE_B",
    "msi_error_hole": "MSI_ERROR_HOLE",
    "same_edge_callbacks": "SAME_EDGE_CALLBACKS",
    "msi_callback_hole": "MSI_CALLBACK_HOLE",
    "msi_callback_duplicate": "MSI_CALLBACK_DUPLICATE",
    "msi_callback_perm_123": "MSI_CALLBACK_PERM_123",
    "msi_callback_perm_132": "MSI_CALLBACK_PERM_132",
    "msi_callback_perm_213": "MSI_CALLBACK_PERM_213",
    "msi_callback_perm_231": "MSI_CALLBACK_PERM_231",
    "msi_callback_perm_312": "MSI_CALLBACK_PERM_312",
    "msi_callback_perm_321": "MSI_CALLBACK_PERM_321",
    "ack_beyond_issued": "ACK_BEYOND_ISSUED",
    "topology": "TOPOLOGY",
}


_FATAL_PROFILES = frozenset({
    "SQ_READ_ERROR",
    "SQ_SEQUENCE_MISMATCH",
    "SAME_EDGE_ACCEPTANCE_CONFLICT",
    "AMBIGUOUS_B",
    "PRE_AR_INTAKE_FATAL",
    "NORMAL_SQ_AT_FATAL_CUT",
    "SQ_ERROR_AT_FATAL_CUT",
    "DRIVER_COMMIT_AT_FATAL_CUT",
    "CQ_ENTRY_B_ERROR",
    "SQ_HEAD_B_ERROR",
    "CQ_TAIL_B_ERROR",
    "MSI_B_ERROR",
    "ACK_NO_TARGET_COMMIT",
    "ACK_ERROR_MSI_BEFORE",
    "ACK_ERROR_MSI_SAME",
    "ACK_ERROR_MSI_AFTER",
    "ACK_ERROR_MSI_LATE",
    "ACK_POSTCOMMIT_ERROR",
    "MSI_ERROR_HOLE",
    "METADATA_CQ_MISMATCH",
    "METADATA_SESSION_MISMATCH",
    "METADATA_USER_MISMATCH",
    "METADATA_TASK_MISMATCH",
    "METADATA_ROUND_MISMATCH",
    "METADATA_FLAGS_UNKNOWN",
    "METADATA_TLV_SIZE_MISMATCH",
    "MSI_POSTCOMMIT_ACK_OK",
    "MSI_POSTCOMMIT_ACK_ERROR",
    "MSI_POSTCOMMIT_ACK_DELAYED",
    "SECOND_SQ_READ_ERROR",
})


def profile_for(subcase: str) -> Gate3Profile:
    try:
        name = _PROFILE_NAMES[subcase]
    except KeyError as error:
        raise ValueError(f"unknown Gate3 subcase {subcase}") from error
    if name == "DEPTH1_WRAP":
        return Gate3Profile(name, sq_depth=1, cq_depth=1, request_count=3)
    if name == "DEPTH2_WRAP":
        return Gate3Profile(name, sq_depth=2, cq_depth=2, request_count=3)
    if name == "SQ_FULL":
        return Gate3Profile(name, sq_depth=1, request_count=3,
                            request_id_capacity=4)
    if name == "CQ_FULL":
        return Gate3Profile(
            name, sq_depth=4, cq_depth=2, request_count=3,
            request_id_capacity=4,
        )
    if name == "BULK_SATURATION":
        return Gate3Profile(name, request_count=4, request_id_capacity=8)
    if name in {
        "QOS_OUT_OF_ORDER", "SAME_TICK_SEQ_ONLY", "SECOND_SQ_READ_ERROR"
    }:
        return Gate3Profile(name, request_count=2)
    if name in {
        "MSI_REVERSE_B", "MSI_ERROR_HOLE", "SAME_EDGE_CALLBACKS",
        "MSI_CALLBACK_HOLE", "MSI_CALLBACK_DUPLICATE",
    } or name.startswith("MSI_CALLBACK_PERM_"):
        return Gate3Profile(
            name, sq_depth=4, cq_depth=4, request_count=3,
            request_id_capacity=4,
        )
    if name in {
        "PROMPT_NOT_VISIBLE", "PARAMETER_NOT_VISIBLE", "SQ_NOT_VISIBLE",
        "RELEASE_FENCE_PENDING", "LOCAL_STORE_VISIBILITY", "RELEASE_FENCE",
    }:
        return Gate3Profile(name, local_visibility_delay=2)
    if name == "PRE_AR_INTAKE_FATAL":
        return Gate3Profile(name, sq_read_issue_delay=20)
    return Gate3Profile(name)


def profile_named(name: str) -> Gate3Profile:
    if name in {"NORMAL", "E2E_D_PREFIX"}:
        return Gate3Profile(name)
    for subcase in _PROFILE_NAMES:
        profile = profile_for(subcase)
        if profile.name == name:
            return profile
    raise ValueError(f"unknown Gate3 profile {name}")


def e2e_d_prefix_steps():
    return ("CQ_READ", "METADATA_READ", "CQ_HEAD_ACK", "DRAIN")


def is_expected_fatal(profile: Gate3Profile) -> bool:
    return profile.name in _FATAL_PROFILES


def _src1_read(local: int) -> int:
    return (1 << 48) | (1 << 39) | local


def _src1_write(local: int) -> int:
    return (1 << 48) | local


def _fault(target: int, uid: int, response: str = "slverr") -> dict:
    return {"target": target, "uid": uid, "resp": response}


def _latency(target: int, uid: int, cycles: int) -> dict:
    return {"target": target, "uid": uid, "cycles": cycles}


def _b_delay(target: int, uid: int, cycles: int) -> dict:
    return {"target": target, "uid": uid, "cycles": cycles}


def target_plans(profile: Gate3Profile) -> dict:
    faults = []
    extra_latency = []
    b_delays = []
    post_commit = []
    write_commit_tiebreaks = []
    if profile.name == "SQ_READ_ERROR":
        faults.append(_fault(3, _src1_read(0)))
    elif profile.name == "SECOND_SQ_READ_ERROR":
        faults.append(_fault(3, _src1_read(3)))
    elif profile.name == "PARAMETER_READ_ERROR":
        faults.append(_fault(3, _src1_read(1)))
    elif profile.name == "SQ_HEAD_B_ERROR":
        faults.append(_fault(3, _src1_write(0)))
    elif profile.name == "CQ_ENTRY_B_ERROR":
        faults.append(_fault(3, _src1_write(3)))
    elif profile.name == "CQ_TAIL_B_ERROR":
        faults.append(_fault(3, _src1_write(4)))
    elif profile.name == "MSI_B_ERROR":
        faults.append(_fault(3, _src1_write(5)))
    elif profile.name in {
        "MSI_POSTCOMMIT_ACK_OK",
        "MSI_POSTCOMMIT_ACK_ERROR",
        "MSI_POSTCOMMIT_ACK_DELAYED",
    }:
        post_commit.append(_fault(3, _src1_write(5)))
        if profile.name == "MSI_POSTCOMMIT_ACK_ERROR":
            post_commit.append(_fault(2, 1))
            b_delays.append(_b_delay(3, _src1_write(5), 21))
        elif profile.name == "MSI_POSTCOMMIT_ACK_DELAYED":
            b_delays.append(_b_delay(2, 1, 3000))
    elif profile.name == "MSI_ERROR_HOLE":
        faults.append(_fault(3, _src1_write(11)))
        b_delays.append(_b_delay(3, _src1_write(11), 6000))
    elif profile.name == "PROVEN_NO_EFFECT_B_ERROR":
        faults.append(_fault(2, 0))
    elif profile.name in {
        "AMBIGUOUS_B", "PRE_AR_INTAKE_FATAL", "NORMAL_SQ_AT_FATAL_CUT",
        "SQ_ERROR_AT_FATAL_CUT", "DRIVER_COMMIT_AT_FATAL_CUT",
        "SAME_EDGE_ACCEPTANCE_CONFLICT",
    }:
        if profile.name != "DRIVER_COMMIT_AT_FATAL_CUT":
            post_commit.append(_fault(2, 0))
        if profile.name == "NORMAL_SQ_AT_FATAL_CUT":
            b_delays.append(_b_delay(2, 0, 20))
            extra_latency.append(_latency(3, _src1_read(0), 1))
        elif profile.name == "SQ_ERROR_AT_FATAL_CUT":
            b_delays.append(_b_delay(2, 0, 19))
            faults.append(_fault(3, _src1_read(0)))
        elif profile.name == "DRIVER_COMMIT_AT_FATAL_CUT":
            b_delays.append(_b_delay(2, 0, 19))
            faults.append(_fault(3, _src1_read(0)))
    elif profile.name == "ACK_NO_TARGET_COMMIT" or profile.name.startswith("ACK_ERROR_MSI_"):
        faults.append(_fault(2, 1))
        if profile.name.startswith("ACK_ERROR_MSI_"):
            delay = {"BEFORE": 0, "SAME": 21, "AFTER": 22, "LATE": 3000}[
                profile.name.removeprefix("ACK_ERROR_MSI_")
            ]
            if delay:
                b_delays.append(_b_delay(3, _src1_write(5), delay))
    elif profile.name == "ACK_POSTCOMMIT_ERROR":
        post_commit.append(_fault(2, 1))
    elif profile.name in {
        "EARLY_NORMAL_CQ", "EARLY_SEQ_ONLY_CQ", "EARLY_CUMULATIVE_HEAD"
    }:
        b_delays.append(_b_delay(2, 0, 1000))
    elif profile.name == "EARLY_ACK":
        b_delays.append(_b_delay(3, _src1_write(5), 3000))
    elif profile.name == "CQ_FULL":
        b_delays.append(_b_delay(3, _src1_write(5), 1000))
    elif profile.name == "MSI_REVERSE_B":
        b_delays.extend((
            _b_delay(3, _src1_write(11), 6000),
            _b_delay(3, _src1_write(14), 3000),
        ))
    elif profile.name == "SAME_EDGE_CALLBACKS":
        extra_latency.extend((
            _latency(3, _src1_write(11), 124),
            _latency(3, _src1_write(14), 59),
        ))
    elif profile.name == "MSI_CALLBACK_HOLE":
        extra_latency.append(_latency(3, _src1_write(11), 300))
    elif profile.name.startswith("MSI_CALLBACK_PERM_"):
        permutation = tuple(
            int(value) for value in profile.name.rsplit("_", 1)[1]
        )
        alignment = {1: 124, 2: 59, 3: 0}
        uid = {1: 11, 2: 14, 3: 17}
        for tail in (1, 2, 3):
            extra_latency.append(
                _latency(3, _src1_write(uid[tail]),
                         alignment[tail])
            )
        write_commit_tiebreaks.extend(
            {"target": 3, "uid": _src1_write(uid[tail]), "rank": rank}
            for rank, tail in enumerate(permutation)
        )

    if profile.name == "DELAY_PROMPT":
        extra_latency.append(_latency(3, _src1_read(2), 4))
    elif profile.name == "DELAY_OUTPUT":
        extra_latency.append(_latency(3, _src1_write(1), 4))
    elif profile.name == "DELAY_METADATA":
        extra_latency.append(_latency(3, _src1_write(2), 4))
    elif profile.name == "DELAY_CQ":
        extra_latency.append(_latency(3, _src1_write(3), 4))
    return {
        "planned_faults": faults,
        "planned_extra_latency": extra_latency,
        "planned_post_commit_faults": post_commit,
        "planned_b_ejection": b_delays,
        "planned_write_commit_replays": (
            [{"target": 3, "uid": _src1_write(11), "count": 1}]
            if profile.name == "MSI_CALLBACK_DUPLICATE" else []
        ),
        "planned_write_commit_tiebreaks": write_commit_tiebreaks,
    }


EXPECTED_FATAL_PROFILES = {
    "SQ_READ_ERROR": "E_AGENT_PROTOCOL_FATAL",
    "SQ_SEQUENCE_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "SQ_HEAD_B_ERROR": "E_COMPLETION_PATH_AXI",
    "CQ_ENTRY_B_ERROR": "E_COMPLETION_PATH_AXI",
    "CQ_TAIL_B_ERROR": "E_COMPLETION_PATH_AXI",
    "MSI_B_ERROR": "E_INTERRUPT",
    "MSI_ERROR_HOLE": "E_INTERRUPT",
    "ACK_NO_TARGET_COMMIT": "E_COMPLETION_PATH_AXI",
    "ACK_ERROR_MSI_BEFORE": "E_COMPLETION_PATH_AXI",
    "ACK_ERROR_MSI_SAME": "E_COMPLETION_PATH_AXI",
    "ACK_ERROR_MSI_AFTER": "E_COMPLETION_PATH_AXI",
    "ACK_ERROR_MSI_LATE": "E_COMPLETION_PATH_AXI",

    "ACK_NO_TARGET_COMMIT_ERROR": "E_COMPLETION_PATH_AXI",
    "ACK_AFTER_TARGET_COMMIT": "E_COMPLETION_PATH_AXI",
    "ACK_POSTCOMMIT_ERROR": "E_COMPLETION_PATH_AXI",
    "AMBIGUOUS_B": "E_AGENT_PROTOCOL_FATAL",
    "PRE_AR_INTAKE_FATAL": "E_AGENT_PROTOCOL_FATAL",
    "NORMAL_SQ_AT_FATAL_CUT": "E_AGENT_PROTOCOL_FATAL",
    "SQ_ERROR_AT_FATAL_CUT": "E_AGENT_PROTOCOL_FATAL",
    "DRIVER_COMMIT_AT_FATAL_CUT": "E_AGENT_PROTOCOL_FATAL",
    "SAME_EDGE_ACCEPTANCE_CONFLICT": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_CQ_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_SESSION_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_USER_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_TASK_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_ROUND_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_FLAGS_UNKNOWN": "E_AGENT_PROTOCOL_FATAL",
    "METADATA_TLV_SIZE_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "MSI_POSTCOMMIT_ACK_OK": "E_INTERRUPT",
    "MSI_POSTCOMMIT_ACK_ERROR": "E_INTERRUPT",
    "MSI_POSTCOMMIT_ACK_DELAYED": "E_INTERRUPT",
    "SECOND_SQ_READ_ERROR": "E_AGENT_PROTOCOL_FATAL",
    "STALE_CQ_SEQ": "E_AGENT_PROTOCOL_FATAL",
    "CQ_REQUEST_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
    "CQ_COOKIE_MISMATCH": "E_AGENT_PROTOCOL_FATAL",
}


def expected_outcome(profile_name: str):
    if profile_name in EXPECTED_FATAL_PROFILES:
        symbol = EXPECTED_FATAL_PROFILES[profile_name]
        return ("EXPECTED_FATAL", "INFRA_FATAL", symbol)
    return ("QUIESCENT_SUCCESS", "QUIESCENT_SUCCESS", None)

import copy
import hashlib
from pathlib import Path

import pytest
import yaml

from mesh_ir.agent_config import (
    AgentRuntimeConfig,
    build_capacity_plan,
    load_agent_runtime_config,
    release_policy_of,
    validate_runtime_config,
)
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import (
    PlanError,
    load_control_plan,
    load_workload_plan,
)
from mesh_ir.model import canonical_json_bytes

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
CONFIG_DIGEST = "0ff7cdf1bb695635ee95f43e624f5ecb3ddf3e21d4a1bad05210ed4d3c182077"
NULL_CONTROL_DIGEST = "49fcb22b9f99dbce8e30a87871b3d738b99e49e4dde346a381004c218ad1676b"


def load_all():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    control = load_control_plan(FIXTURES / "agent_control_plan_two_user.json", workload)
    config = load_agent_runtime_config(
        FIXTURES / "agent_runtime_config_two_user.yaml"
    )
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_two_user.json"
    )
    return workload, control, config, registry


def mutated(document):
    return AgentRuntimeConfig(
        document=copy.deepcopy(document),
        digest=hashlib.sha256(canonical_json_bytes(document)).hexdigest(),
    )


def test_fixture_loads_with_frozen_digest():
    workload, control, config, registry = load_all()
    assert config.digest == CONFIG_DIGEST
    assert release_policy_of(config) == "EXPLICIT_ONLY"
    validate_runtime_config(config, workload, registry)


def test_capacity_plan_required_matches_contract_formulas():
    workload, control, config, _ = load_all()
    document = build_capacity_plan(workload, control, config)
    assert document["counts"]["Q"] == 7
    assert document["counts"]["live_context_bound"] == 4
    required = document["required"]
    assert required["global"]["sq_submit_ready_queue_entries"] == 7
    assert required["global"]["control_trigger_queue_entries"] == 2
    assert required["global"]["seen_request_id_entries"] == 7
    assert required["global"]["kv_session_record_entries"] == 3
    assert required["global"]["kv_session_tombstone_entries"] == 1
    assert required["global"]["cancel_join_entries"] == 1
    assert required["global"]["host_admission_registry_entries"] == 2
    assert required["global"]["agent_object_table_entries"] == 9
    assert required["global"]["max_request_contexts"] == 4
    assert required["global"]["early_cq_ack_entries"] == 4
    assert required["global"]["completion_read_queue_entries"] == 8
    assert required["global"]["global_host_tokens"] == 2
    assert required["global"]["npu_session_release_waiter_entries"] == 1
    assert required["global"]["agent_proxy_to_npu_queue_entries"] == 0
    assert required["service_pools"] == {
        "COMPILE": {"slots": 1, "queue_depth": 2},
        "TEST": {"slots": 1, "queue_depth": 2},
        "LOG_PARSE": {"slots": 1, "queue_depth": 2},
    }
    assert document["configured"]["global"]["global_host_tokens"] == 19
    assert document["control_plan_digest"] != NULL_CONTROL_DIGEST


def test_capacity_plan_headroom_is_configured_minus_required():
    workload, control, config, _ = load_all()
    document = build_capacity_plan(workload, control, config)
    for section in ("global",):
        for key, value in document["headroom"][section].items():
            assert value == (
                document["configured"][section][key] - document["required"][section][key]
            )
    body = {
        key: value
        for key, value in document.items()
        if key != "capacity_plan_digest"
    }
    assert document["capacity_plan_digest"] == hashlib.sha256(
        canonical_json_bytes(body)
    ).hexdigest()


def test_capacity_minus_one_fails_closed():
    workload, control, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent_axi_driver"]["synthetic_host_services"][
        "agent_object_table_entries"
    ] = 8
    with pytest.raises(PlanError, match="E_CAPACITY_PLAN"):
        build_capacity_plan(workload, control, mutated(document))


def test_zero_token_pool_fails_closed():
    workload, control, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent_axi_driver"]["synthetic_host_services"][
        "host_available_fraction_q16"
    ] = 1
    with pytest.raises(PlanError, match="E_CAPACITY_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_user_count_mismatch_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent"]["users"] = 3
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_polling_completion_mode_rejected(tmp_path):
    _, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent"]["completion_mode"] = "polling"
    target = tmp_path / "polling_runtime_config.yaml"
    target.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        load_agent_runtime_config(target)


def test_repair_cap_mismatch_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent"]["max_repair_rounds"] = 2
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_chunk_mismatch_rejected():
    workload, _, config, registry = load_all()
    document = copy.deepcopy(config.document)
    document["serving"]["output_chunk_bytes"] = 4097
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload, registry)


def test_arena_overlap_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["serving"]["address_map"]["input_arena"]["base"] = 0x0000000105000000
    with pytest.raises(PlanError, match="E_ADDRESS_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_msi_outside_control_page_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["serving"]["address_map"]["msi_address"] = 0x0000000100030000
    with pytest.raises(PlanError, match="E_ADDRESS_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_msi_id_count_below_inflight_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["serving"]["msi_axi_id_count"] = 2
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_control_id_in_msi_range_rejected():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["serving"]["control_axi_ids"]["sq_doorbell"] = 33
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload)

def test_shaper_enabled_rejected_at_load():
    workload, _, config, _ = load_all()
    document = copy.deepcopy(config.document)
    document["agent_axi_driver"]["remote_link_shaper"]["enabled"] = True
    with pytest.raises(PlanError, match="E_AGENT_PLAN"):
        validate_runtime_config(mutated(document), workload)


def test_shaper_disabled_baseline_still_valid():
    workload, _, config, registry = load_all()
    validate_runtime_config(mutated(config.document), workload, registry)

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_workload import load_workload_plan

FIXTURE = Path(__file__).resolve().parents[4] / \
    "tests/gem5/ai_mesh/fixtures/gate6"


def serving_program(name="serving_two_tokens"):
    return decode_program((FIXTURE / f"{name}.mshb").read_bytes())


def serving_round():
    plan = load_workload_plan(FIXTURE / "serving_workload_plan.json")
    return plan.users[0].tasks[0].rounds[0], plan


def test_workload_plan_matches_the_serving_request_profile():
    program = serving_program()
    request = program.agent_request_profiles[0]
    item, plan = serving_round()
    assert plan.max_repair_rounds == 0
    assert item.prompt_tokens == request.full_input_tokens == 8
    assert item.prompt_bytes == request.full_input_dma_bytes == 128
    assert item.full_context_tokens == request.full_input_tokens
    assert item.expected_cached_tokens == request.expected_cached_tokens == 0
    assert item.output_tokens == request.output_tokens == 2
    assert item.generated_code_bytes == request.host_output_bytes == 256
    assert item.output_capacity_bytes >= request.host_output_bytes
    assert item.kv_required_tokens_after_round == 10
    assert item.program_id == request.program_id
    assert item.profile_id == request.profile_id
    assert item.requested_profile_key == request.requested_profile_key
    assert item.kv_policy == "INITIAL"


def test_workload_plan_agrees_with_the_phase_plan_tokens():
    from mesh_ir.serving_oracle import generate_phase_plan

    program = serving_program()
    request = program.agent_request_profiles[0]
    item, _ = serving_round()
    plan = generate_phase_plan(program, request, 0)
    assert plan[0].valid_tokens_per_member == item.prompt_tokens
    assert sum(step.decode_chunk_tokens for step in plan) == item.output_tokens
    assert plan[-1].cached_tokens_after == item.kv_required_tokens_after_round
    assert plan[-1].host_output_dma_bytes_per_member == \
        item.generated_code_bytes
    assert request.publish_chunk_bytes == 64
    assert item.generated_code_bytes % request.publish_chunk_bytes == 0


def test_workload_plan_round_is_self_consistent():
    item, _ = serving_round()
    assert item.kv_required_tokens_after_round == \
        item.full_context_tokens + item.output_tokens
    assert item.output_capacity_bytes >= item.generated_code_bytes
    digest = json.loads(
        (FIXTURE / "serving_workload_plan.json").read_text())
    encoded = digest["users"][0]["tasks"][0]["rounds"][0][
        "input_content_digest"]
    assert len(encoded) == 64
    assert item.compile.outcome == "SUCCESS"

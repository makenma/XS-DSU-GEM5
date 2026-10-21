import json

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.generated import abi as A
from mesh_ir.serving_contract_digest import frozen_expectation
from mesh_ir.serving_e2e_oracle import verify_serving_run
from mesh_ir.serving_programs import serving_program

from .serving_runtime import ARCH_YAML, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize("output_tokens", [1, 2, 3])
def test_initial_decode_publish_chain_has_one_generate_completion(tmp_path, output_tokens):
    write_serving_fixture(tmp_path, serving_program(load_arch(ARCH_YAML), output_tokens))
    program = decode_program((tmp_path / "program.mshb").read_bytes())
    profile = program.agent_request_profiles[0]
    path = tmp_path / "workload.json"
    workload = json.loads(path.read_text())
    row = workload["users"][0]["tasks"][0]["rounds"][0]
    row.update(output_tokens=output_tokens, generated_code_bytes=profile.host_output_bytes,
               output_capacity_bytes=profile.host_output_bytes,
               kv_required_tokens_after_round=profile.full_input_tokens + output_tokens)
    row["compile"]["local_io"]["read_bytes"] = max(
        row["compile"]["local_io"]["read_bytes"], profile.host_output_bytes)
    path.write_text(json.dumps(workload))
    path = tmp_path / "surrogate.json"
    surrogate = json.loads(path.read_text())
    surrogate["profiles"][0].update(output_tokens=output_tokens,
                                     output_bytes=profile.host_output_bytes)
    path.write_text(json.dumps(surrogate))
    _, result, metrics = run_serving_fixture(tmp_path, "phase_chain")
    facts = read_facts_document(tmp_path / "phase_chain/gate6_facts.tsv")
    verify_serving_run(program, result["transport"], metrics, facts.serving_phases)
    assert [row["phase"] for row in facts.serving_phases] == (
        [A.PHASE.PREFILL] + [A.PHASE.DECODE] * output_tokens + [A.PHASE.PUBLISH])
    assert [row["append_tokens"] for row in facts.serving_phases] == (
        [profile.full_input_tokens] + [1] * output_tokens + [0])
    assert len(result["instances"]) == output_tokens + 2
    cq = [event for event in facts.events if event["kind"] == "CQ_ASSIGN"]
    assert len(cq) == 1 and cq[0]["status"] == "SUCCESS"
    assert metrics["metadata_reads_validated"] == 1
    assert metrics["generated_code_reads_validated"] == 1
    assert metrics["generated_code_read_bytes"] == profile.host_output_bytes
    expected = frozen_expectation(tmp_path / "workload.json", program,
                                  profile.profile_id, profile.host_output_bytes)
    reads = [event for event in facts.events if event["kind"] == "LOCAL_READ"
             and event["object"] == "GENERATED_CODE_READ"]
    assert len(reads) == 1 and reads[0]["status"] == expected["host_digest"]
    assert reads[0]["tick"] > facts.serving_phases[-1]["commit_tick"]
    assert metrics["kv_cached_tokens"] == profile.full_input_tokens + output_tokens

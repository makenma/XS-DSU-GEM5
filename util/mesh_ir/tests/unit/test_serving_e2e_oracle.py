import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_e2e_oracle import verify_serving_run

FIXTURE = Path(__file__).resolve().parents[4] / \
    "tests/gem5/ai_mesh/fixtures/gate6"
READ_KINDS = (A.DMA_KIND.LOAD, A.DMA_KIND.PREFETCH)
WRITE_KINDS = (A.DMA_KIND.STORE, A.DMA_KIND.P2P_PUSH)


def program():
    return decode_program((FIXTURE / "serving_two_tokens.mshb").read_bytes())


def rows_of(program):
    rows = []
    for descriptor in program.dma_descriptors:
        row = {"descriptor_id": descriptor.descriptor_id,
               "dma_kind": descriptor.kind, "read_bytes": 0,
               "write_bytes": 0, "fill_bytes": 0, "error_code": 0}
        if descriptor.kind in READ_KINDS:
            row["read_bytes"] = descriptor.useful_bytes
        elif descriptor.kind in WRITE_KINDS:
            row["write_bytes"] = descriptor.useful_bytes
        else:
            row["fill_bytes"] = descriptor.useful_bytes
        rows.append(row)
    return rows


def phases_of(program_):
    from mesh_ir.generated import abi as A
    from mesh_ir.serving_oracle import generate_phase_plan

    steps = generate_phase_plan(program_, program_.agent_request_profiles[0],
                                A.PATH_KIND.INITIAL_PREFILL)
    kv_symbol = program_.agent_request_profiles[0].primary_kv_symbol_id
    kv_tensor = next(relocation.tensor_id
                     for relocation in program_.relocations
                     if relocation.symbol_sid == kv_symbol)
    rows = []
    for instance in program_.agent_instance_profiles:
        step = next((value for value in steps
                     if value.instance_profile_id ==
                     instance.instance_profile_id), None)
        if step is None:
            continue
        command_ids = {
            program_.commands[index].command_id
            for range_ in program_.profile_stream_ranges
            if range_.profile_id == instance.mesh_profile_id
            for index in range(range_.command_begin,
                               range_.command_begin + range_.command_count)
        }
        appended = step.cached_tokens_after - step.kv_tokens_before
        appends = sum(
            1 for descriptor in program_.dma_descriptors
            if descriptor.command_id in command_ids and
            descriptor.kind == A.DMA_KIND.STORE and
            descriptor.dst.tensor_id == kv_tensor)
        rows.append({
            "phase": step.phase,
            "instance_profile_id": step.instance_profile_id,
            "mesh_profile_id": instance.mesh_profile_id,
            "kv_tokens_before": step.kv_tokens_before,
            "append_tokens": appended,
            "accepted_descriptors": appends,
            "terminal_descriptors": appends,
            "core_drain_tick": 100 + step.ordinal,
            "append_terminal_tick": 110 + step.ordinal,
            "commit_tick": 120 + step.ordinal,
        })
    return rows

def metrics():
    return {"cq_assignments": 1, "core_starts": 1, "live_contexts": 0,
            "fatal_records": 0}


def test_a_clean_run_is_accepted():
    program_ = program()
    summary = verify_serving_run(program_, rows_of(program_), metrics(), phases_of(program_))
    assert summary["descriptors"] == len(program_.dma_descriptors)
    assert summary["profiles"] == [1, 2, 3, 4]
    assert summary["kv_read_bytes"] == {11: 0, 12: 128, 13: 144, 14: 0}


def test_a_missing_descriptor_is_rejected():
    program_ = program()
    rows = rows_of(program_)
    with pytest.raises(MeshIrError) as err:
        verify_serving_run(program_, rows[1:], metrics(), phases_of(program_))
    assert err.value.code == "E_TRAFFIC_MISMATCH"


def test_an_extra_descriptor_is_rejected():
    program_ = program()
    rows = rows_of(program_)
    rows.append(dict(rows[0], descriptor_id=99))
    with pytest.raises(MeshIrError):
        verify_serving_run(program_, rows, metrics(), phases_of(program_))


def test_a_mutated_byte_count_is_rejected():
    program_ = program()
    rows = rows_of(program_)
    for row in rows:
        if row["dma_kind"] in WRITE_KINDS and row["write_bytes"] > 0:
            row["write_bytes"] -= 16
            break
    with pytest.raises(MeshIrError):
        verify_serving_run(program_, rows, metrics(), phases_of(program_))


def test_a_mutated_kind_is_rejected():
    program_ = program()
    rows = rows_of(program_)
    rows[0]["dma_kind"] = A.DMA_KIND.STORE
    with pytest.raises(MeshIrError):
        verify_serving_run(program_, rows, metrics(), phases_of(program_))


def test_a_failed_descriptor_is_rejected():
    program_ = program()
    rows = rows_of(program_)
    rows[0]["error_code"] = 2
    with pytest.raises(MeshIrError):
        verify_serving_run(program_, rows, metrics(), phases_of(program_))


def test_a_shifted_kv_window_is_rejected():
    import dataclasses

    program_ = program()
    instances = [
        dataclasses.replace(record, kv_read_bytes_per_member=
                            record.kv_read_bytes_per_member + 16)
        if record.phase == A.PHASE.DECODE else record
        for record in program_.agent_instance_profiles]
    mutated = dataclasses.replace(program_, agent_instance_profiles=instances)
    with pytest.raises(MeshIrError):
        verify_serving_run(mutated, rows_of(program_), metrics(), phases_of(program_))


@pytest.mark.parametrize("metric,value", [
    ("cq_assignments", 2), ("core_starts", 0), ("live_contexts", 1),
    ("fatal_records", 1)])
def test_terminal_ledger_must_match(metric, value):
    program_ = program()
    observed = metrics()
    observed[metric] = value
    with pytest.raises(MeshIrError):
        verify_serving_run(program_, rows_of(program_), observed, phases_of(program_))


def test_the_real_run_artifact_is_accepted():
    import json

    from mesh_ir.gate3_oracle import read_facts_document

    evidence = FIXTURE / "run_evidence"
    rows = json.loads(
        (evidence / "mesh_result.json").read_text("utf-8"))["transport"]
    facts = read_facts_document(evidence / "gate6_facts.tsv")
    observed = {"cq_assignments": 1, "core_starts": 1, "live_contexts": 0,
                "fatal_records": 0}
    program_ = program()
    summary = verify_serving_run(program_, rows, observed,
                                 facts.serving_phases)
    assert summary["descriptors"] == len(program_.dma_descriptors)
    assert [phase[0] for phase in summary["phases"]] == [1, 2, 2, 3]

import dataclasses

import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.gate3_oracle import read_facts_document

from .serving_runtime import FIXTURES, run_serving_fixture, write_serving_fixture


def test_static_member_slots_are_independent_of_primary_relocation_origins(tmp_path):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    profile = program.agent_request_profiles[0]
    primary = {profile.primary_input_symbol_id, profile.primary_output_symbol_id}
    config = yaml.safe_load((FIXTURES / "serving_runtime_config.yaml").read_text())
    address_map = config["serving"]["address_map"]
    input_base = address_map["input_arena"]["base"]
    output_base = address_map["output_arena"]["base"]
    output_end = output_base + profile.host_output_bytes
    shifted = dataclasses.replace(program, relocations=tuple(
        dataclasses.replace(record, offset_bytes=record.offset_bytes + 4096)
        if record.symbol_sid in primary else record
        for record in program.relocations))
    outputs = []
    for name, image in (("control", program), ("primary_shift", shifted)):
        work = write_serving_fixture(tmp_path / name, image)
        _, result, metrics = run_serving_fixture(work, "run")
        facts = read_facts_document(work / "run/gate6_facts.tsv")
        assert facts.fatal is None
        assert result["terminal"] == "DONE"
        assert result["watchdog_fired"] == 0
        assert len(facts.serving_phases) == 4
        assert metrics["metadata_reads_validated"] == 1
        assert metrics["kv_cached_tokens"] == 10
        assert any(row["channel"] == "AR" and row["address"] == input_base
                   for row in result["burst_timings"])
        output_addresses = [row["address"] for row in result["burst_timings"]
                            if row["channel"] == "AW" and
                            output_base <= row["address"] < output_end]
        assert output_addresses == list(range(
            output_base, output_end, config["serving"]["output_chunk_bytes"]))
        outputs.append(result)
    assert outputs[0]["burst_timings"] == outputs[1]["burst_timings"]

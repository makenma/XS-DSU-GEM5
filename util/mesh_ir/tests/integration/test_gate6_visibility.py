import json

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.gate3_oracle import read_facts_document

from .serving_runtime import FIXTURES, PROBE, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize("boundary", ["output", "metadata"])
def test_host_visibility_waits_for_the_real_producer_response(tmp_path, boundary):
    program = decode_program((FIXTURES / "serving_two_tokens.mshb").read_bytes())
    work = write_serving_fixture(tmp_path, program)
    _, baseline, _ = run_serving_fixture(work, "baseline")
    baseline_facts = read_facts_document(work / "baseline/gate6_facts.tsv")
    arena = yaml.safe_load((work / "config.yaml").read_text())["serving"]["address_map"]["output_arena"]
    base, end = arena["base"], arena["base"] + arena["bytes"]
    writes = [row for row in baseline["burst_timings"] if row["channel"] == "AW"]
    if boundary == "output":
        index, transaction = next((index, row) for index, row in enumerate(writes)
                                  if base <= row["address"] < end)
        entry = PROBE
        delays = [dict(core=transaction["core_id"], kind="write", index=index,
                       address=transaction["address"], cycles=10000)]
    else:
        transaction = next(row for row in baseline_facts.events
                           if row["kind"] == "AXI_ACCEPT" and row["control"] == "METADATA"
                           and row["channel"] == "AW")
        entry = work / "metadata_delay.py"
        entry.write_text("import os, runpy\n"
            f"os.environ['GATE6_DELAY_UID'] = {str(transaction['txn'])!r}\n"
            "os.environ['GATE6_DELAY_CYCLES'] = '10000'\n"
            f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
        delays = []
    _, delayed, metrics = run_serving_fixture(work, "delayed", entry=entry,
        extra_latencies=delays, fault_target_address=transaction["address"])
    facts = read_facts_document(work / "delayed/gate6_facts.tsv")
    assert facts.fatal is None and delayed["terminal"] == "DONE"
    assert delayed["terminal_tick"] > baseline["terminal_tick"]
    assert delayed["transport"] == baseline["transport"]
    output_writes = [row for row in delayed["burst_timings"]
                     if row["channel"] == "AW" and base <= row["address"] < end]
    assert sum(row["beats"] * row["beat_bytes"] for row in output_writes) == 256
    assert not any(row["kind"] == "AXI_ACCEPT" and row["control"] == "OUTPUT"
                   for row in facts.events)
    metadata = [row for row in facts.events if row["kind"] == "AXI_ACCEPT"
                and row["control"] == "METADATA"]
    assert min(row["tick"] for row in metadata if row["channel"] == "AW") > max(
        row["response_tick"] for row in output_writes)
    reads = [row for row in facts.events if row["kind"] == "LOCAL_READ"
             and row["object"] in ("METADATA_READ", "GENERATED_CODE_READ")]
    assert reads
    assert min(row["tick"] for row in reads) > max(
        row["tick"] for row in metadata if row["channel"] == "B")
    original_reads = [row for row in baseline_facts.events if row["kind"] == "LOCAL_READ"
                      and row["object"] == "GENERATED_CODE_READ"]
    code_reads = [row for row in reads if row["object"] == "GENERATED_CODE_READ"]
    assert len(code_reads) == len(original_reads) == 1
    assert code_reads[0]["status"] == original_reads[0]["status"]
    assert metrics["metadata_reads_validated"] == metrics["generated_code_reads_validated"] == 1

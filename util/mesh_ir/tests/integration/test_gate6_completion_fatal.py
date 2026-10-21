import json

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.gate3_oracle import read_facts_document
from .serving_runtime import FIXTURES, PROBE, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize('control', ['METADATA', 'CQ_ENTRY', 'CQ_TAIL_UPDATE', 'MSI'])
def test_completion_transport_fatal_preserves_committed_kv(tmp_path, control):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    run_serving_fixture(work, 'baseline')
    baseline = read_facts_document(work / 'baseline/gate6_facts.tsv')
    transaction = next(row for row in baseline.events if row['kind'] == 'AXI_ACCEPT'
                       and row['control'] == control and row['channel'] == 'AW')
    entry = work / 'completion_fault.py'
    entry.write_text('import os, runpy\n'
        f'os.environ["GATE6_FAULT_UID"] = {str(transaction["txn"])!r}\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    _, result, metrics = run_serving_fixture(work, 'fatal', entry=entry,
        fault_target_address=transaction['address'], expect_execution=False,
        expected_fatal='E_INTERRUPT' if control == 'MSI' else 'E_COMPLETION_PATH_AXI')
    facts = read_facts_document(work / 'fatal/gate6_facts.tsv')
    state = json.loads((work / 'baseline/gate6_facts.tsv.kv.json').read_text())['manifest']
    assert len(facts.serving_phases) == len(baseline.serving_phases) == 4
    assert metrics['fatal_kv_cached_tokens'] == 10
    assert metrics['fatal_kv_valid_bytes'] == 160
    assert metrics['fatal_kv_records'] == 1
    assert metrics['fatal_kv_outstanding_dma'] == metrics['fatal_kv_pins'] == 0
    assert state['geometry']['bytes_per_token'] == 16
    assigned = [row for row in facts.events if row['kind'] == 'CQ_ASSIGN']
    assert len(assigned) <= 1
    assert metrics['fatal_records'] == 1
    assert result is None

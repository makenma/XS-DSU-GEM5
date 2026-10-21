from dataclasses import replace
import json
import struct

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_publish_oracle import validate_publish_segments
from .serving_runtime import FIXTURES, PROBE, run_serving_fixture, write_serving_fixture, assert_serving_error_quiescent


def test_admitted_axi_fault_has_validated_error_metadata(tmp_path):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    _, result, metrics = run_serving_fixture(work, 'axi_fault', fault_index=0)
    assert_serving_error_quiescent(result, metrics,
        read_facts_document(work / 'axi_fault/gate6_facts.tsv'), [1])
    assert result['terminal'] == 'ERROR_DRAINED'
    assert metrics['metadata_reads_validated'] == 1
    assert sum(row['read_bytes'] for row in result['transport']) == 256
    assert sum(row['write_bytes'] for row in result['transport']) == 0
    assert sum(row['write_drained_uncommitted_bytes'] for row in result['transport']) == 128
    failures = [row['failure'] for row in result['instances'] if row.get('failure')]
    assert len(failures) == 1
    assert failures[0]['detail'] == 0x00020001
    assert failures[0]['source_wire'] == struct.pack(
        '<HHBBHIIIIQQ', 2, 0, 4, 2, 0, 0, 0, 1, 1, 1, 1).hex()


@pytest.fixture
def segmented_publish(tmp_path):
    from mesh_ir.serving_programs import serving_program
    from mesh_ir.architecture import load_arch
    from .serving_runtime import ARCH_YAML

    program = serving_program(load_arch(ARCH_YAML), output_tokens=2, publish_max_burst_beats=1)
    work = write_serving_fixture(tmp_path, program)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    arena = config['serving']['address_map']['output_arena']
    arena['base'] += 4096 - 32
    arena['bytes'] -= 4096
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return work


@pytest.mark.parametrize('fault_index,prefix', [(-1, 256), (3, 0), (4, 32), (6, 96)])
def test_publish_segments_commit_only_a_contiguous_prefix(segmented_publish, fault_index, prefix):
    work = segmented_publish
    output_base = yaml.safe_load((work / 'config.yaml').read_text())['serving']['address_map']['output_arena']['base']
    _, result, metrics = run_serving_fixture(work, 'publish', fault_index=fault_index,
        fault_target_address=output_base)
    facts = read_facts_document(work / 'publish' / 'gate6_facts.tsv')
    assert result['terminal'] == ('DONE' if fault_index < 0 else 'ERROR_DRAINED')
    assert metrics['metadata_reads_validated'] == 1
    if fault_index >= 0:
        failure = next(row['failure'] for row in result['instances'] if row.get('failure'))
        assert failure['tick'] == min(row['tick'] for row in facts.publish_segments if row['kind'] == 2)
    segments = facts.publish_segments
    committed = [row for row in segments if row['kind'] == 1]
    assert sum(row['bytes'] for row in committed) == prefix
    assert [row['offset'] for row in committed] == list(range(0, prefix, 32))
    program = decode_program((work / 'program.mshb').read_bytes())
    assert validate_publish_segments(program, 14, 4, {0: (1, output_base)}, 32, segments) == {1: prefix}


def test_publish_cancel_drains_started_descriptor_and_suppresses_tail(segmented_publish):
    work = segmented_publish
    workload = load_workload_plan(work / 'workload.json')
    control = work / 'control.json'
    control.write_text(json.dumps({
        'schema': 'agent_control_plan_v1', 'version': 1,
        'workload_plan_digest': workload.digest,
        'actions': [{
            'control_ordinal': 1, 'opcode': 'CANCEL',
            'issuer_user_id': 0, 'issuer_task_seq': 0,
            'target_user_id': 0, 'target_task_seq': 0, 'target_repair_round': 0,
            'trigger': {'kind': 'AFTER_FIRST_OUTPUT_CHUNK', 'event_user_id': 0,
                        'event_task_seq': 0, 'event_repair_round': 0},
        }],
    }))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['control_plan'] = str(control)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    entry = work / 'delayed_publish.py'
    entry.write_text(
        'import os, runpy\n'
        "os.environ.update(GATE6_DELAY_ENABLE='1', GATE6_DELAY_CORE='0', "
        "GATE6_DELAY_KIND='write', GATE6_DELAY_INDEX='5', GATE6_DELAY_CYCLES='10000')\n"
        f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
    _, result, metrics = run_serving_fixture(work, 'cancel', entry=entry,
        fault_target_address=config['serving']['address_map']['output_arena']['base'])
    facts = read_facts_document(work / 'cancel' / 'gate6_facts.tsv')
    assert result['terminal'] == 'DONE'
    assert facts.cancel_sources[1] == dict(instance=4, phase=3)
    assert metrics['metadata_reads_validated'] == 1
    segments = facts.publish_segments
    assert sum(row['bytes'] for row in segments if row['kind'] == 1) == 128
    assert {row['descriptor'] for row in segments if row['kind'] == 3} == {14, 15}
    program = decode_program((work / 'program.mshb').read_bytes())
    output_base = config['serving']['address_map']['output_arena']['base']
    assert validate_publish_segments(program, 14, 4, {0: (1, output_base)}, 32, segments) == {1: 128}
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_CONSUME'].count('CANCELLED') == 1


def test_axi_read_error_uses_first_response_before_descriptor_drain(tmp_path):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    _, result, metrics = run_serving_fixture(work, 'read_fault', fault_index=1, fault_kind='read')
    assert_serving_error_quiescent(result, metrics,
        read_facts_document(work / 'read_fault/gate6_facts.tsv'), [1])
    assert result['terminal'] == 'ERROR_DRAINED'
    assert metrics['metadata_reads_validated'] == 1
    assert sum(row['read_bytes'] for row in result['transport']) == 128
    assert sum(row['read_discarded_bytes'] for row in result['transport']) == 128
    failure = next(row['failure'] for row in result['instances'] if row.get('failure'))
    failed = [row for row in result['dma_timings'] if row.get('first_error_tick')]
    assert failed
    assert failure['tick'] == min(row['first_error_tick'] for row in failed)
    assert any(row['first_error_tick'] < row['done_tick'] for row in failed)

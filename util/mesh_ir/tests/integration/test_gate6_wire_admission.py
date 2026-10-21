import copy
import json

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.generated import agent_abi as A
from mesh_ir.gate3_oracle import read_facts_document
from .serving_runtime import FIXTURES, run_serving_fixture, write_serving_fixture


CONTROL_ZERO_FIELDS = (
    'flags', 'reserved', 'reserved2', 'reserved3', 'input_addr', 'input_bytes',
    'input_tokens', 'cached_tokens', 'output_addr', 'output_capacity_bytes',
    'output_metadata_addr', 'output_metadata_capacity_bytes', 'max_output_tokens',
    'user_id', 'task_seq', 'repair_round', 'qos', 'workload_plan_item_id',
    'requested_profile_key', 'moe_route_profile_id', 'binding_table_offset',
    'binding_count', 'extension_offset', 'extension_bytes',
)
FIELD_CASES = [
    (opcode, record, field)
    for opcode in ('GENERATE', 'RELEASE_SESSION', 'CANCEL')
    for record, fields in (
        (0, ('flags2', 'reserved') + (() if opcode == 'GENERATE' else
            ('flags', 'qos', 'program_id', 'profile_id')) + (('session_id',) if opcode == 'CANCEL' else ())),
        (1, ('flags', 'reserved', 'reserved2', 'reserved3', 'target_request_id', 'moe_route_profile_id')
            if opcode == 'GENERATE' else CONTROL_ZERO_FIELDS +
            (('target_request_id',) if opcode == 'RELEASE_SESSION' else ('kv_handle', 'kv_generation'))),
    )
    for field in fields
]


def wire_patch(sequence, record, field, value):
    fields = A.SQ_DESCRIPTOR_FIELDS if record == 0 else A.PARAMETER_HEADER_FIELDS
    row = next(row for row in fields if row['name'] == field)
    width = int(row['type'][1:]) // 8
    return [part for index, byte in enumerate(value.to_bytes(width, 'little'))
            for part in (sequence, record, row['offset'] + index, byte)]


def wire_fixture(tmp_path, opcode='GENERATE', trigger='SCENARIO_START'):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    if opcode != 'GENERATE':
        workload = load_workload_plan(work / 'workload.json')
        target = ({'target_user_id': 0, 'target_task_seq': 0, 'target_repair_round': 0}
                  if opcode == 'CANCEL' else
                  {'target_session_id': 1, 'target_kv_handle': 1, 'target_generation': 1})
        event = {'kind': trigger}
        if trigger != 'SCENARIO_START':
            event.update(event_user_id=0, event_task_seq=0, event_repair_round=0)
        (work / 'control.json').write_text(json.dumps({
            'schema': 'agent_control_plan_v1', 'version': 1,
            'workload_plan_digest': workload.digest,
            'actions': [{'control_ordinal': 1, 'opcode': opcode,
                         'issuer_user_id': 0, 'issuer_task_seq': 0,
                         **target, 'trigger': event}]}))
        config_path = work / 'config.yaml'
        config = yaml.safe_load(config_path.read_text())
        config['agent']['control_plan'] = 'control.json'
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return work


@pytest.mark.parametrize('opcode,record,field', FIELD_CASES,
                         ids=['-'.join(map(str, case)) for case in FIELD_CASES])
def test_each_nonapplicable_wire_field_rejects_before_work(tmp_path, opcode, record, field):
    work = wire_fixture(tmp_path, opcode)
    _, result, metrics = run_serving_fixture(work, 'rejected',
        wire_patches=wire_patch(0, record, field, 1), expect_execution=opcode != 'GENERATE')
    facts = read_facts_document(work / 'rejected/gate6_facts.tsv')
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == (1 if opcode == 'GENERATE' else 2)
    rejected = next(row for row in cq if row['request_id'] == (1 if opcode == 'GENERATE' else 2))
    assert rejected['status'] == 'ERROR'
    detail = next(row for row in facts.events if row['kind'] == 'CQ_DETAIL' and
                  row['request_id'] == rejected['request_id'])
    assert detail['status'] in ('E_RESERVED_FIELD', 'E_PARAMETER_LENGTH_MISMATCH', 'E_REQUEST_BINDING')
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') and row['tick'] <= rejected['tick']
                   for row in facts.events)
    assert metrics.get('metadata_reads_validated', 0) == (0 if opcode == 'GENERATE' else 1)
    if result:
        assert len(result['instances']) == 4
    else:
        assert not facts.serving_phases


@pytest.mark.parametrize('field,value,detail', [
    ('request_id', 0, 'E_REQUEST_IDENTITY'),
    ('completion_cookie', 0, 'E_REQUEST_IDENTITY'),
    ('completion_cookie', 2, 'E_REQUEST_IDENTITY'),
    ('abi_major', 2, 'E_REQUEST_BINDING'),
    ('abi_minor', 1, 'E_REQUEST_BINDING'),
])
def test_sequence_keyed_rejection_does_not_publish_an_untrusted_identity(tmp_path, field, value, detail):
    work = wire_fixture(tmp_path)
    _, _, metrics = run_serving_fixture(work, 'identity', expect_execution=False,
                                       wire_patches=wire_patch(0, 0, field, value))
    facts = read_facts_document(work / 'identity/gate6_facts.tsv')
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == 1 and cq[0]['request_id'] == 0 and cq[0]['cookie'] == 0
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == [detail]
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') or
                   (row['kind'] == 'AXI_ACCEPT' and row['object'] == 'PARAMETER') for row in facts.events)
    assert metrics.get('metadata_reads_validated', 0) == 0
    assert metrics['failed_tasks'] == 1


@pytest.mark.parametrize('retired', [False, True])
def test_duplicate_generate_identity_remains_reserved_after_retirement(tmp_path, retired):
    work = wire_fixture(tmp_path)
    workload = json.loads((work / 'workload.json').read_text())
    second = copy.deepcopy(workload['users'][0])
    second['user_id'] = 1
    task = second['tasks'][0]
    task['session_id'] = task['kv_handle'] = 2
    task['rounds'][0]['workload_plan_item_id'] = 2
    if retired:
        task['think_time_ns'] = 6000000
    workload['users'].append(second)
    (work / 'workload.json').write_text(json.dumps(workload))
    config = yaml.safe_load((work / 'config.yaml').read_text())
    config['agent']['users'] = 2
    (work / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'duplicate',
        wire_patches=wire_patch(1, 0, 'request_id', 1) + wire_patch(1, 0, 'completion_cookie', 1))
    facts = read_facts_document(work / 'duplicate/gate6_facts.tsv')
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == 2
    assert {(row['request_id'], row['status']) for row in cq} == {(0, 'ERROR'), (1, 'SUCCESS')}
    assert next(row for row in cq if row['request_id'] == 0)['cookie'] == 1
    assert len(result['instances']) == 4 and metrics['metadata_reads_validated'] == 1
    if retired:
        assert cq[0]['request_id'] == 1


@pytest.mark.parametrize('opcode', ['CANCEL', 'RELEASE_SESSION'])
def test_control_cannot_reuse_a_retired_generate_identity(tmp_path, opcode):
    work = wire_fixture(tmp_path, opcode, 'AFTER_GENERATE_TERMINAL')
    _, result, metrics = run_serving_fixture(work, 'collision',
        wire_patches=wire_patch(1, 0, 'request_id', 1) + wire_patch(1, 0, 'completion_cookie', 1))
    facts = read_facts_document(work / 'collision/gate6_facts.tsv')
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert [(row['request_id'], row['status']) for row in cq] == [(1, 'SUCCESS'), (0, 'ERROR')]
    assert metrics['metadata_reads_validated'] == 1 and len(result['instances']) == 4


def test_cancel_rejects_a_control_request_id(tmp_path):
    work = wire_fixture(tmp_path, 'CANCEL')
    _, result, _ = run_serving_fixture(work, 'target', wire_patches=wire_patch(0, 1, 'target_request_id', 2))
    facts = read_facts_document(work / 'target/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == ['E_REQUEST_BINDING']
    assert len(result['instances']) == 4


@pytest.mark.parametrize('field,value,detail', [
    ('qos', 1, 'E_RESERVED_FIELD'),
    ('request_kind', 2, 'E_RESERVED_FIELD'),
    ('binding_table_offset', 152, 'E_REQUEST_BINDING'),
    ('binding_table_offset', 0xfffffff8, 'E_REQUEST_BINDING'),
    ('binding_count', 65535, 'E_REQUEST_BINDING'),
    ('extension_offset', 160, 'E_REQUEST_BINDING'),
    ('extension_offset', 0xfffffff8, 'E_REQUEST_BINDING'),
    ('extension_bytes', 0xfffffff8, 'E_REQUEST_BINDING'),
    ('total_bytes', 0xfffffff8, 'E_PARAMETER_LENGTH_MISMATCH'),
])
def test_parameter_layout_and_qos_fail_before_any_serving_work(tmp_path, field, value, detail):
    work = wire_fixture(tmp_path)
    run_serving_fixture(work, 'layout', expect_execution=False,
                        wire_patches=wire_patch(0, 1, field, value))
    facts = read_facts_document(work / 'layout/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == [detail]
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') for row in facts.events)
    assert len([row for row in facts.events if row['kind'] == 'CQ_CONSUME']) == 1


@pytest.mark.parametrize('chunk', ['missing', 63, 65])
def test_output_chunk_tlv_is_mandatory_and_exact(tmp_path, chunk):
    work = wire_fixture(tmp_path)
    program = decode_program((work / 'program.mshb').read_bytes())
    offset = A.PARAMETER_HEADER_BYTES + len(program.agent_request_binding_requirements) * A.BINDING_RECORD_BYTES
    chunk_header = offset + 8 + 32
    patches = ([0, 1, chunk_header, A.TLV_TYPE.DEADLINE] if chunk == 'missing' else
               [0, 1, chunk_header + 8, chunk])
    run_serving_fixture(work, 'chunk', expect_execution=False, wire_patches=patches)
    facts = read_facts_document(work / 'chunk/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == ['E_OUTPUT_CHUNK_MISMATCH']
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') for row in facts.events)
    assert len([row for row in facts.events if row['kind'] == 'CQ_CONSUME']) == 1


@pytest.mark.parametrize('delta', [-1, 1])
@pytest.mark.parametrize('field,value', [
    ('input_bytes', 128),
    ('input_tokens', 8),
    ('max_output_tokens', 2),
    ('output_capacity_bytes', 256),
])
def test_serving_header_exact_sizes_reject_both_neighbors(tmp_path, field, value, delta):
    work = wire_fixture(tmp_path)
    run_serving_fixture(work, 'neighbor', expect_execution=False,
                        wire_patches=wire_patch(0, 1, field, value + delta))
    facts = read_facts_document(work / 'neighbor/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == ['E_WORKLOAD_PLAN_MISMATCH']
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') for row in facts.events)
    assert len([row for row in facts.events if row['kind'] == 'CQ_CONSUME']) == 1


@pytest.mark.parametrize('field,value,detail', [
    ('parameter_block_addr', 0, 'E_REQUEST_BINDING'),
    ('parameter_block_addr', 7, 'E_REQUEST_BINDING'),
    ('parameter_block_addr', (1 << 64) - 8, 'E_REQUEST_BINDING'),
    ('parameter_block_bytes', 0, 'E_PARAMETER_LENGTH_MISMATCH'),
    ('parameter_block_bytes', 152, 'E_PARAMETER_LENGTH_MISMATCH'),
    ('parameter_block_bytes', 161, 'E_PARAMETER_LENGTH_MISMATCH'),
    ('parameter_block_bytes', (1 << 32) - 8, 'E_PARAMETER_LENGTH_MISMATCH'),
])
def test_sq_fetch_envelope_rejects_without_an_ar(tmp_path, field, value, detail):
    work = wire_fixture(tmp_path)
    run_serving_fixture(work, 'envelope', expect_execution=False,
                        wire_patches=wire_patch(0, 0, field, value))
    facts = read_facts_document(work / 'envelope/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == [detail]
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') or
                   (row['kind'] == 'AXI_ACCEPT' and row['object'] == 'PARAMETER') for row in facts.events)
    assert len([row for row in facts.events if row['kind'] == 'CQ_CONSUME']) == 1


@pytest.mark.parametrize('role,field,value', [
    ('HOST_INPUT', 'flags', 0x8000),
    ('HOST_OUTPUT', 'flags', 0x8000),
    ('WEIGHT_EXTERNAL', 'flags', 0x8000),
    ('KV_EXTERNAL', 'flags', 0x8000),
    ('HOST_INPUT', 'address', 0),
    ('HOST_OUTPUT', 'bytes', 0),
    ('WEIGHT_EXTERNAL', 'address', (1 << 64) - 8),
    ('KV_EXTERNAL', 'address', 64),
    ('HOST_INPUT', 'symbol_id', 0),
    ('HOST_INPUT', 'kind', 65535),
])
def test_binding_record_permissions_and_ranges_reject_before_work(tmp_path, role, field, value):
    work = wire_fixture(tmp_path)
    program = decode_program((work / 'program.mshb').read_bytes())
    requirements = sorted(program.agent_request_binding_requirements, key=lambda row: row.symbol_id)
    index = next(i for i, row in enumerate(requirements) if row.binding_kind == getattr(A.BINDING_KIND, role))
    spec = next(row for row in A.BINDING_RECORD_FIELDS if row['name'] == field)
    offset = A.PARAMETER_HEADER_BYTES + index * A.BINDING_RECORD_BYTES + spec['offset']
    width = int(spec['type'][1:]) // 8
    patches = [part for i, byte in enumerate(value.to_bytes(width, 'little')) for part in (0, 1, offset + i, byte)]
    run_serving_fixture(work, 'binding', expect_execution=False, wire_patches=patches)
    facts = read_facts_document(work / 'binding/gate6_facts.tsv')
    details = [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL']
    assert len(details) == 1 and details[0] in ('E_REQUEST_BINDING', 'E_BINDING_ROLE', 'E_BINDING_ALIAS_MISMATCH')
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') for row in facts.events)
    assert len([row for row in facts.events if row['kind'] == 'CQ_CONSUME']) == 1


def test_cancel_targeting_a_known_release_uses_target_kind_rejection(tmp_path):
    work = wire_fixture(tmp_path, 'RELEASE_SESSION')
    control_path = work / 'control.json'
    control = json.loads(control_path.read_text())
    control['actions'].append({'control_ordinal': 2, 'opcode': 'CANCEL',
        'issuer_user_id': 0, 'issuer_task_seq': 0, 'target_user_id': 0,
        'target_task_seq': 0, 'target_repair_round': 0,
        'trigger': {'kind': 'AFTER_RELEASE_TERMINAL', 'after_control_ordinal': 1}})
    control_path.write_text(json.dumps(control))
    _, result, _ = run_serving_fixture(work, 'target_kind',
                                       wire_patches=wire_patch(1, 1, 'target_request_id', 2))
    facts = read_facts_document(work / 'target_kind/gate6_facts.tsv')
    assert [row['status'] for row in facts.events if row['kind'] == 'CQ_DETAIL'] == ['E_REQUEST_BINDING']
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert {row['request_id']: row['status'] for row in cq} == {1: 'SUCCESS', 2: 'NOT_FOUND', 3: 'ERROR'}
    assert len(cq) == 3 and len(result['instances']) == 4


@pytest.mark.parametrize('record,field,value', [
    (0, 'session_id', 0), (0, 'program_id', 0), (0, 'profile_id', 0),
    (1, 'input_addr', 0), (1, 'input_bytes', 0), (1, 'input_tokens', 0),
    (1, 'output_addr', 0), (1, 'output_capacity_bytes', 0),
    (1, 'output_metadata_addr', 0), (1, 'output_metadata_capacity_bytes', 0),
    (1, 'max_output_tokens', 0), (1, 'kv_handle', 0), (1, 'kv_generation', 0),
    (1, 'workload_plan_item_id', 0), (1, 'requested_profile_key', 0),
    (1, 'user_id', 1), (1, 'task_seq', 1), (1, 'repair_round', 1),
])
def test_generate_mandatory_fields_match_the_frozen_plan_before_work(tmp_path, record, field, value):
    work = wire_fixture(tmp_path)
    _, _, metrics = run_serving_fixture(work, 'mandatory', expect_execution=False,
                                       wire_patches=wire_patch(0, record, field, value))
    facts = read_facts_document(work / 'mandatory/gate6_facts.tsv')
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == 1 and cq[0]['status'] == 'ERROR'
    assert len([row for row in facts.events if row['kind'] == 'CQ_DETAIL']) == 1
    assert not any(row['kind'] in ('CORE_START', 'BATCH_FREEZE') for row in facts.events)
    assert metrics.get('metadata_reads_validated', 0) == 0

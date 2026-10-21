from dataclasses import replace
import struct

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as M, agent_abi as A
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_programs import ranked_full_view_serving_program
from .serving_runtime import ARCH_YAML, FIXTURES, run_serving_fixture, write_serving_fixture, write_shared_fixture, assert_serving_error_quiescent


@pytest.mark.parametrize('shared', [False, True])
def test_compute_poison_read_drains_the_frozen_instance_and_its_members(tmp_path, shared):
    program = (ranked_full_view_serving_program(load_arch(ARCH_YAML)) if shared else
               decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes()))
    if shared:
        _, work = write_shared_fixture(tmp_path, program=program)
    else:
        work = write_serving_fixture(tmp_path, program)
    _, result, metrics = run_serving_fixture(work, 'poison',
        extra_args=('--residency-fault', 'invalidate_compute_input'))
    facts = read_facts_document(work / 'poison/gate6_facts.tsv')
    assert_serving_error_quiescent(result, metrics, facts, [1, 4294967297] if shared else [1])
    assert result['terminal'] == 'ERROR_DRAINED'
    failures = [row['failure'] for row in result['instances'] if row.get('failure')]
    assert len(failures) == 1
    assert failures[0]['detail'] == A.DETAIL_CODE['E_DCORE_POISON_READ']
    source = struct.unpack('<HHBBHIIII16s', bytes.fromhex(failures[0]['source_wire']))
    assert source[:5] == (M.MOE_ERROR_CLASS.SRAM, 0, M.MESH_OBJECT_DOMAIN.STATIC,
                          M.ERROR_SOURCE_OBJECT_KIND.COMMAND, 0)
    assert source[5:7] == (0, 0)
    assert source[7] in {row.command_id for row in program.commands if row.opcode == M.OPCODE.GEMM}
    assert source[8:] == (0, bytes(16))
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == metrics['metadata_reads_validated'] == 1 + int(shared)
    assert len({row['request_id'] for row in cq}) == len(cq)
    assert all(row['status'] == 'ERROR' for row in cq)
    assert metrics['live_cq_obligations'] == 0

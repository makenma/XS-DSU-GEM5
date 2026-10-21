from copy import deepcopy
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.acceptance import ContractError
from mesh_ir.serving_publish_oracle import validate_publish_segments


@pytest.fixture
def published():
    program = decode_program(Path('tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb').read_bytes())
    facts = []
    tick = 10
    for descriptor, offset, size in ((12, 0, 32), (12, 32, 32), (13, 64, 64),
                                      (14, 128, 64), (15, 192, 64)):
        for kind in (0, 1):
            facts.append(dict(request=1, instance=4, descriptor=descriptor,
                              offset=offset, bytes=size, tick=tick, kind=kind))
            tick += 1
    return program, facts


def test_static_slots_and_splitter_independently_prove_the_prefix(published):
    program, facts = published
    assert validate_publish_segments(program, 14, 4, {0: (1, 4096 - 32)}, 32, facts) == {1: 256}


@pytest.mark.parametrize('mutation', ['early_issue', 'duplicate_b', 'wrong_size', 'wrong_owner', 'wrong_instance', 'hole'])
def test_tampered_segment_evidence_is_rejected(published, mutation):
    program, original = published
    facts = deepcopy(original)
    if mutation == 'early_issue':
        facts[2]['tick'] = facts[1]['tick']
    elif mutation == 'duplicate_b':
        facts.insert(2, deepcopy(facts[1]))
    elif mutation == 'wrong_size':
        facts[0]['bytes'] = 64
    elif mutation == 'wrong_owner':
        facts[0]['request'] = 2
    elif mutation == 'wrong_instance':
        facts[0]['instance'] = 5
    else:
        del facts[:2]
    with pytest.raises(ContractError):
        validate_publish_segments(program, 14, 4, {0: (1, 4096 - 32)}, 32, facts)

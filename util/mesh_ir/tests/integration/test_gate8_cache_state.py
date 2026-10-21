import json
from pathlib import Path

import pytest
import yaml

from jsonschema import Draft202012Validator

from .serving_runtime import run_serving_fixture, write_shared_fixture
from .test_gate6_batch_replay import install_replay


@pytest.mark.parametrize('mode', ['FULL_TIMING', 'FAST_EVENT'])
def test_runtime_exports_quiescent_cache_through_the_replay_owner(tmp_path, mode):
    _, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    install_replay(work, shared=False)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if mode == 'FAST_EVENT':
        config['runtime']['fast_event'] = {'queue_entries': 1}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'export', weight_policy='cached')
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 2
    states = result['moe']['cache_state']
    assert states
    for row in states:
        state = row['replay_state']
        assert state['core_id'] == row['core_id']
        assert len(state['slots']) == row['mshr_slots']
        assert sum(slot['state'] for slot in state['slots']) == len(row['valid_lines'])
        assert state['failure_tombstones'] == []
        valid = [slot for slot in state['slots'] if slot['state']]
        assert sorted(slot['lru_rank'] for slot in valid) == list(range(len(valid)))
        assert [slot['slot_id'] for slot in state['slots']] == list(range(len(state['slots'])))
        assert [{key: slot[key] for key in ('slot_id', 'weight_tag_index', 'valid_bytes')}
                for slot in valid] == [{key: slot[key] for key in ('slot_id', 'weight_tag_index', 'valid_bytes')}
                                       for slot in row['valid_lines']]
        contract = json.loads((Path(__file__).resolve().parents[4] /
            'schemas/ai_mesh/acceptance_contract_v1.schema.json').read_text())
        schema = contract['$defs']['cacheStateReplay']['properties']['cores']['items']
        Draft202012Validator(schema).validate(state)

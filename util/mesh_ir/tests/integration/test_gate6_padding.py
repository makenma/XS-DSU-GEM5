import pytest
import yaml
from dataclasses import replace

from mesh_ir.architecture import load_arch
from mesh_ir.serving_programs import full_view_serving_program, moe_full_view_serving_program
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.generated import abi as A
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.serving_contract_digest import surrogate_seed, token_digests, semantic_output_digest
from mesh_ir.serving_output_bytes import surrogate_output_bytes, dual_fnv_digest
from .serving_runtime import ARCH_YAML, write_shared_fixture, run_serving_fixture
from .serving_moe_oracle import assert_shared_moe_image


@pytest.mark.parametrize('moe', [False, True])
def test_profile_padding_is_local_and_preserves_member_outputs(tmp_path, moe):
    arch = ARCH_YAML.with_name('mesh_1x2_moe.yaml') if moe else ARCH_YAML
    outcomes = []
    for padding in (0, 8):
        program = (moe_full_view_serving_program if moe else full_view_serving_program)(
            load_arch(arch), padding_tokens=padding)
        if moe:
            program = replace(program,
                moe_layer_specs=tuple(replace(layer, overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY,
                    capacity_factor_q16=98304) for layer in program.moe_layer_specs),
                moe_kernel_specs=tuple(replace(kernel, max_m=32) for kernel in program.moe_kernel_specs))
        _, work = write_shared_fixture(tmp_path / str(padding), moe=moe, program=program)
        _, result, metrics = run_serving_fixture(work, 'shared')
        assert result['terminal'] == 'DONE'
        assert metrics['metadata_reads_validated'] == metrics['generated_code_reads_validated'] == 2
        facts = read_facts_document(work / 'shared/gate6_facts.tsv')
        assert facts.fatal is None
        ranges = yaml.safe_load((work / 'config.yaml').read_text())['serving']['address_map']
        for role, channel in (('input_arena', 'AR'), ('output_arena', 'AW')):
            arena = ranges[role]
            assert sum(row['beats'] * row['beat_bytes'] for row in result['burst_timings']
                       if row['channel'] == channel and arena['base'] <= row['address'] <
                       arena['base'] + arena['bytes']) == 256
        if moe:
            assert_shared_moe_image(work, 'streamed')
        outputs = [(e['request_id'], e['status']) for e in facts.events
                   if e['kind'] == 'LOCAL_READ' and e['object'] == 'GENERATED_CODE_READ']
        assert len(outputs) == 2
        workload = load_workload_plan(work / 'workload.json')
        expected = []
        for user in workload.document['users']:
            row = user['tasks'][0]['rounds'][0]
            seed = surrogate_seed(bytes.fromhex(row['input_content_digest']), row['program_id'],
                row['profile_id'], int(row['requested_profile_key'], 16))
            digest = semantic_output_digest(bytes.fromhex(workload.digest),
                row['workload_plan_item_id'], row['output_tokens'], token_digests(seed, row['output_tokens']))
            expected.append(((user['user_id'] << 32) | 1,
                dual_fnv_digest(surrogate_output_bytes(digest, row['generated_code_bytes']))))
        assert sorted(outputs) == expected
        outcomes.append((result, outputs))
    baseline, padded = outcomes
    assert sum(row['fill_bytes'] for row in padded[0]['transport'] if row['domain'] == 0) - sum(
        row['fill_bytes'] for row in baseline[0]['transport'] if row['domain'] == 0) == 256
    assert sum(row['gemm_cycles'] for row in padded[0]['cores']) > sum(
        row['gemm_cycles'] for row in baseline[0]['cores'])
    assert [request for request, _ in padded[1]] == [request for request, _ in baseline[1]]

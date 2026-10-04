"""Mutate recorded small RTL-SE evidence; these tests do not execute a CPU."""
import copy
import importlib.util
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fetch_fault_gate', ROOT / 'scripts/check-native-fetch-fault-regression.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/fetch-fault-rtl-se.json').read_text())


class FetchFaultGateTests(unittest.TestCase):
    def setUp(self):
        self.data = copy.deepcopy(FIXTURE)

    def reject_stats(self):
        with self.assertRaises(ValueError):
            gate.verify_statistics(*self.data['statistics'])

    def test_actual_recorded_statistics_and_episodes(self):
        self.assertEqual(len(gate.verify_statistics(*self.data['statistics'])), 22)
        for i, text in enumerate(self.data['episodes']):
            evidence = gate.verify_episode(text, bool(i))
            self.assertEqual(len(evidence['carriers']), 1 if i else 2)
            self.assertEqual(len(evidence['decodedCarriers']), 0 if i else 2)

    def test_each_primitive_delta_is_required(self):
        for key in gate.PRIMITIVES:
            with self.subTest(key=key):
                self.data = copy.deepcopy(FIXTURE)
                self.data['statistics'][1][gate.P + key][0] = str(int(self.data['statistics'][1][gate.P + key][0]) + 1)
                self.reject_stats()

    def test_primitive_equal_shift_both_sides_is_not_accepted(self):
        for stats in self.data['statistics']:
            stats[gate.P + 'fetch.cycles'][0] = str(int(stats[gate.P + 'fetch.cycles'][0]) + 5)
        self.reject_stats()

    def test_unknown_nonhost_change_fails(self):
        self.data['statistics'][1]['system.cpu1.committedInsts'][0] = '1'
        self.reject_stats()

    def test_missing_statistic_fails(self):
        del self.data['statistics'][1]['system.cpu1.committedInsts']
        self.reject_stats()

    def test_new_statistic_fails(self):
        self.data['statistics'][1]['unexpected'] = ['0']
        self.reject_stats()

    def test_each_formula_is_recomputed(self):
        for key in gate.FORMULAS:
            with self.subTest(key=key):
                self.data = copy.deepcopy(FIXTURE)
                self.data['statistics'][1][gate.P + key] = ['0.123456']
                self.reject_stats()

    def test_formula_dependency_both_sides_changed_fails(self):
        for stats in self.data['statistics']:
            stats[gate.P + 'scheduler.memstall_any_load'][0] = '2225'
        self.reject_stats()

    def test_fetch_width_cannot_replace_decode_width(self):
        with self.assertRaises(ValueError):
            gate.verify_statistics(*self.data['statistics'], width=16)

    def test_every_distribution_percentage_checked(self):
        for prefix in ('fetch.fetchStatusDist', 'iew.fetchStallReason', 'iew.decodeStallReason'):
            for token in (1, 2):
                with self.subTest(prefix=prefix, token=token):
                    self.data = copy.deepcopy(FIXTURE)
                    name = 'Running' if prefix.startswith('fetch.') else 'NoStall'
                    self.data['statistics'][1][gate.P + prefix + '::' + name][token] = '99.99%'
                    self.reject_stats()

    def test_changed_cdf_with_same_count_is_not_ignored(self):
        self.data['statistics'][1][gate.P + 'fetch.fetchStatusDist::Blocked'][2] = '89.70%'
        self.reject_stats()

    def test_distribution_total_not_only_changed_rows(self):
        for stats in self.data['statistics']:
            stats[gate.P + 'iew.decodeStallReason::total'][0] = '129336'
        self.reject_stats()

    def test_distribution_order_matters(self):
        stats = self.data['statistics'][1]
        key = gate.P + 'fetch.fetchStatusDist::Running'
        value = stats.pop(key)
        stats[key] = value
        self.reject_stats()

    def test_additional_full_span_fault_is_rejected(self):
        text = self.data['episodes'][1] + '500000: system.cpu1.fetch: fault, mem_req.addr=0x1234\n'
        with self.assertRaises(ValueError):
            gate.verify_fault_records(text, True)

    def test_demand_wrong_pc_missing_request_or_cause_is_rejected(self):
        original = self.data['episodes'][1]
        for field, old, new in (('pc', gate.PC, gate.SECOND), ('missing', gate.PC, gate.SECOND),
                                ('request', gate.PC, gate.SECOND), ('cause', '0', '12')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.verify_episode(original.replace(field + '=' + old, field + '=' + new), True)

    def test_duplicate_carrier_not_accepted(self):
        text = self.data['episodes'][1]
        line = next(l for l in text.splitlines() if 'Instruction PC' in l and 'created [sn:81808]' in l)
        with self.assertRaises(ValueError):
            gate.verify_episode(text + line + '\n', True)

    def test_missing_old_second_carrier(self):
        text = '\n'.join(l for l in self.data['episodes'][0].splitlines() if '81809' not in l)
        with self.assertRaises(ValueError):
            gate.verify_episode(text, False)

    def test_new_carrier_reaching_decode_is_not_rtl_profile(self):
        text = self.data['episodes'][1] + f'21348000: system.cpu0.decode: [tid:0] Processing instruction [sn:81808] with PC ({gate.PC}=>{gate.NEXT}).(0=>1)\n'
        with self.assertRaises(ValueError):
            gate.verify_episode(text, True)

    def test_younger_branch_squash_not_sufficient(self):
        text = self.data['episodes'][1].replace('81764', '81810')
        with self.assertRaises(ValueError):
            gate.verify_episode(text, True)

    def test_wrong_ftq_not_same_causal_group(self):
        text = self.data['episodes'][1].replace('with fetch id 6609', 'with fetch id 6610')
        with self.assertRaises(ValueError):
            gate.verify_episode(text, True)

    def test_squash_wrong_tick_rejected(self):
        text = self.data['episodes'][1].replace('21348000: system.cpu0.decode:', '21349000: system.cpu0.decode:')
        with self.assertRaises(ValueError):
            gate.verify_episode(text, True)

    def test_carrier_committing_rejected(self):
        text = self.data['episodes'][1] + f'21346000: system.cpu0.commit: Committing [sn:81808] PC {gate.PC}\n'
        with self.assertRaises(ValueError):
            gate.verify_episode(text, True)

    def test_full_span_plan_cannot_be_windowed(self):
        plan = self.data['plans'][1]
        folder = PurePosixPath(self.data['sourcePaths'][1])
        gate.verify_plan(plan, folder, 1, gate.DEBUG_ARGS)
        with self.assertRaises(ValueError):
            gate.verify_plan(plan, folder, 1, gate.SPARSE_ARGS)

    def test_wrong_binary_program_limits_trace_and_elf(self):
        base = self.data['plans'][1]
        mutations = []
        for key in ('gem5Sha256', 'programManifestSha256', 'configSha256'):
            p = copy.deepcopy(base); p[key] = '0' * 64; mutations.append(p)
        p = copy.deepcopy(base); p['trace'] = 'off'; mutations.append(p)
        p = copy.deepcopy(base); p['binaries'][0]['sha256'] = '0' * 64; mutations.append(p)
        p = copy.deepcopy(base); p['command'][-1] = '1'; mutations.append(p)
        p = copy.deepcopy(base); p['command'][0] = gate.MODELS[0][0]; mutations.append(p)
        for index, plan in enumerate(mutations):
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                gate.verify_plan(plan, PurePosixPath(self.data['sourcePaths'][1]), 1, gate.DEBUG_ARGS)

    def log(self, result, harts='0123'):
        return ''.join(f'NATIVE_RTL_HART_{hart}_PASS\n' for hart in harts) + 'NATIVE_L2_RESULT ' + json.dumps(result) + '\n'

    def test_recorded_cpu_result_positive(self):
        result = self.data['results'][1]
        gate.verify_cpu(result, self.log(result))

    def test_failed_exit_no_hart_duplicate_hart_and_wrong_footer(self):
        result = self.data['results'][1]
        for harts in ('012', '00123', ''):
            with self.subTest(harts=harts), self.assertRaises(ValueError):
                gate.verify_cpu(result, self.log(result, harts))
        for key, value in (('pass', False), ('exitCode', 1), ('exitCode', False),
                           ('protocolOpcodeInjection', True), ('cpuExitTick', '21699001')):
            wrong = {**result, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                gate.verify_cpu(wrong, self.log(wrong))
        with self.assertRaises(ValueError):
            gate.verify_cpu(result, self.log({**result, 'pass': False}))

    def test_diagnostic_cannot_differ_from_normal_in_any_statistics_or_bytes(self):
        base = {'result': self.data['results'][1], 'hashes': {'raw.jsonl': 'a'}, 'stats': self.data['statistics'][1]}
        gate.compare_run(base, copy.deepcopy(base))
        for kind in ('stats', 'hashes', 'result'):
            wrong = copy.deepcopy(base); wrong[kind]['extra'] = 'changed'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                gate.compare_run(base, wrong)

    def test_truncated_missing_repeated_statistics_and_duplicate_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'stats.txt'
            for text in ('', 'Begin Simulation Statistics\nx 1\n',
                         'Begin Simulation Statistics\nx 1\nx 2\nEnd Simulation Statistics\n',
                         'Begin Simulation Statistics\nx 1\nEnd Simulation Statistics\n'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    gate.statistics(path)
            path.write_text('{"passed":true,"passed":false}')
            with self.assertRaises(ValueError):
                gate.read_json(path)


if __name__ == '__main__':
    unittest.main()

"""Mutation tests for the checker only; these fixtures are not simulator results."""
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cbo_check', Path(__file__).with_name('check-linux-cbo.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest = {'suite': 'native-linux-cbo', 'buildId': 'a' * 64,
            'assemblyChecks': {key: True for key in ('rv64Elf', 'fixedExecutable', 'inval', 'clean', 'flush')},
            'instructionAddresses': {'inval': '0x10004', 'clean': '0x10014', 'flush': '0x10024'},
            'module': {'built': True}}


def fixture():
    records = []
    for phase in ['disabled', 'enabled']:
        enabled = phase == 'enabled'
        records.append(('BEGIN', {'schemaVersion': 1, 'buildId': 'a' * 64, 'phase': phase,
            'cpus': [0, 1, 2, 3], 'lineBytes': 64, 'pageBytes': 4096, 'pteHelper': enabled}))
        if enabled:
            records.extend(('ENV', {'stage': stage, 'cpu': cpu, 'senvcfg': hex(0x70 if stage == 'enabled' else 0)})
                           for stage, cpu in itertools.product(['before', 'enabled'], range(4)))
        for cpu, op in itertools.product(range(4), ['inval', 'clean', 'flush']):
            for i, (name, offset) in enumerate([('aligned-rw', 0), ('unaligned-rw', 13), ('readonly', 17),
                                               ('noaccess', 37), ('unmapped', 53)]):
                address = 0x100000 + i * 4096 + offset
                signal = 4 if not enabled else 11 if i >= 3 else 0
                code = 1 if not enabled else 2 if i == 3 else 1 if i == 4 else 0
                si = manifest['instructionAddresses'][op] if signal == 4 else hex(address if signal else 0)
                records.append(('RESULT', {'phase': phase, 'cpu': cpu, 'case': name, 'operation': op,
                    'address': hex(address), 'signal': signal, 'siCode': code, 'siAddress': si,
                    'expectedSignal': signal, 'expectedCode': code, 'byteMismatches': 0, 'pass': True}))
            if enabled:
                for name in ['pte-rw', 'pte-ro']:
                    writable = name == 'pte-rw'
                    before = 0xabc000d3 | (4 if writable else 0)
                    records.append(('PTE', {'cpu': cpu, 'case': name, 'operation': op,
                        'address': '0x1800000d' if writable else '0x19000011',
                        'ioctlError': 0, 'instructionError': 0, 'writableVma': int(writable),
                        'before': hex(before), 'cleared': hex(before & ~128), 'after': hex(before & ~128),
                        'restored': hex(before), 'pass': True}))
        if enabled:
            records.extend(('ENV', {'stage': 'restored', 'cpu': cpu, 'senvcfg': '0x0'}) for cpu in range(4))
        records.append(('END', {'phase': phase, 'rows': 84 if enabled else 60, 'errors': 0, 'pass': True}))
    return records


def evaluate(records, build=manifest):
    text = '\n'.join('LINUX_CBO_' + kind + ' ' + json.dumps(row) for kind, row in records)
    return module.check(text, build, ['disabled', 'enabled'])


class CheckerTests(unittest.TestCase):
    def test_complete_synthetic_fixture(self):
        self.assertTrue(evaluate(fixture())['fullCoverage'])

    def test_mutations_cannot_pass(self):
        mutations = [
            ('RESULT', 'signal', 0, lambda r: r['phase'] == 'disabled'),
            ('RESULT', 'siAddress', '0x100000', lambda r: r['phase'] == 'enabled' and r['case'] == 'noaccess'),
            ('RESULT', 'siAddress', '0x10000', lambda r: r['phase'] == 'disabled'),
            ('RESULT', 'siCode', 1, lambda r: r['phase'] == 'enabled' and r['case'] == 'noaccess'),
            ('RESULT', 'signal', 11, lambda r: r['phase'] == 'enabled' and r['case'] == 'readonly'),
            ('RESULT', 'expectedSignal', 0, lambda r: r['case'] == 'unmapped'),
            ('RESULT', 'byteMismatches', 1, lambda r: True),
            ('PTE', 'cleared', '0xabc000d7', lambda r: True),
            ('PTE', 'after', '0xabc000d7', lambda r: True),
            ('PTE', 'restored', '0xabc00057', lambda r: True),
            ('PTE', 'instructionError', -14, lambda r: True),
            ('PTE', 'ioctlError', 14, lambda r: True),
            ('PTE', 'writableVma', 0, lambda r: r['case'] == 'pte-rw'),
            ('ENV', 'senvcfg', '0x0', lambda r: r['stage'] == 'enabled'),
            ('ENV', 'senvcfg', '0x70', lambda r: r['stage'] == 'restored'),
            ('BEGIN', 'buildId', 'b' * 64, lambda r: True),
            ('BEGIN', 'cpus', [0, 1, 2, 2], lambda r: True),
            ('END', 'rows', 1, lambda r: True),
        ]
        for kind, field, value, predicate in mutations:
            with self.subTest(kind=kind, field=field, value=value):
                rows = fixture()
                next(row for name, row in rows if name == kind and predicate(row))[field] = value
                self.assertFalse(evaluate(rows)['passed'])

    def test_missing_duplicate_truncated_or_unbuilt(self):
        rows = fixture()
        self.assertFalse(evaluate(rows[:-1])['passed'])
        self.assertFalse(evaluate([r for r in rows if r[0] != 'PTE'])['passed'])
        self.assertFalse(evaluate([*rows[:2], rows[1], *rows[2:]])['passed'])
        build = copy.deepcopy(manifest)
        build['module']['built'] = False
        self.assertFalse(evaluate(rows, build)['passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Adversarial checker fixtures only; these tests are not Linux execution evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('uncached_check', Path(__file__).with_name('check-linux-uncached.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture():
    manifest = {'suite': 'native-linux-uncached', 'buildId': 'fixture-only', 'module': {'built': True},
                'assemblyChecks': {x: True for x in ['rv64Elf', 'fixedExecutable',
                    *[f'nc_{op}{bits}' for op in ['load', 'store'] for bits in [8, 16, 32, 64]]]}}
    region = {'start': '0xbfdff000', 'bytes': 4096, 'kind': 'normal-noncacheable-ram',
              'cacheableAlias': False, 'reservedNoMap': True, 'Device': False,
              'MemAttr': 0, 'Order': 2, 'ExpCompAck': False}
    fs = {'manifest': {'source': {'fullSystem': True, 'nativeChi': True}, 'linux': {'uncachedTestRam': region}}}
    rows = [('INFO', {'buildId': 'fixture-only', 'abi': 1, 'physicalStart': region['start'], 'bytes': 4096,
                      'dtNoMap': True, 'mappingProtection': '0xd7', 'cpus': [0, 1, 2, 3]})]
    for index in range(4):
        for line in range(4):
            data = bytearray(0xa5 ^ (index * 17) ^ (line * 7) ^ b for b in range(64))
            for width, offset in [(1, 5), (2, 10), (4, 20), (8, 40)]:
                value = (0x8877665544332200 ^ (index << 5) ^ (line << 3) ^ width) & ((1 << (width * 8)) - 1)
                data[offset:offset + width] = value.to_bytes(width, 'little')
                rows.append(('SAMPLE', {'index': index, 'cpu': index, 'line': line, 'lineOffset': index * 256 + line * 64,
                                       'width': width, 'offset': offset,
                                       'physicalAddress': hex(0xbfdff000 + index * 256 + line * 64 + offset),
                                       'written': hex(value), 'read': hex(value), 'lineData': data.hex(), 'matches': True}))
    rows.append(('RESULT', {'buildId': 'fixture-only', 'completed': 64, 'errors': 0, 'status': 'PASS'}))
    return rows, manifest, fs


def run(rows, manifest, fs):
    return checker.check('\n'.join(f'LINUX_NC_{kind} {json.dumps(row)}' for kind, row in rows), manifest, fs)


class CheckTest(unittest.TestCase):
    def test_complete_fixture(self):
        self.assertTrue(run(*fixture())['passed'])

    def test_false_pass_mutations(self):
        mutations = {
            'missing end': lambda r, m, f: r.pop(),
            'missing sample': lambda r, m, f: r.pop(5),
            'duplicate sample': lambda r, m, f: r.insert(2, copy.deepcopy(r[1])),
            'wrong affinity': lambda r, m, f: r[1][1].update(cpu=3),
            'missing neighbor data': lambda r, m, f: r[1][1].pop('lineData'),
            'neighbor corrupt': lambda r, m, f: r[1][1].update(lineData='00' + r[1][1]['lineData'][2:]),
            'false guest matches': lambda r, m, f: r[1][1].update(read='0x0', matches=True),
            'wrong width': lambda r, m, f: r[1][1].update(width=3),
            'wrong PA': lambda r, m, f: r[1][1].update(physicalAddress='0x1'),
            'fake complete': lambda r, m, f: r[-1][1].update(completed=63),
            'guest error': lambda r, m, f: r[-1][1].update(errors=1),
            'wrong ELF': lambda r, m, f: m.update(buildId='other'),
            'missing instruction': lambda r, m, f: m['assemblyChecks'].update(nc_load64=False),
            'uncompiled module': lambda r, m, f: m['module'].update(built=False),
            'PMA mismatch': lambda r, m, f: f['manifest']['linux']['uncachedTestRam'].update(start='0xa0000000'),
            'cacheable alias': lambda r, m, f: f['manifest']['linux']['uncachedTestRam'].update(cacheableAlias=True),
            'Device memory': lambda r, m, f: f['manifest']['linux']['uncachedTestRam'].update(Device=True),
            'SE substitute': lambda r, m, f: f['manifest']['source'].update(fullSystem=False),
            'PBMT override': lambda r, m, f: r[0][1].update(mappingProtection=hex(0xd7 | (2 << 61))),
            'missing core': lambda r, m, f: r[0][1].update(cpus=[0, 1, 2]),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                data = fixture()
                mutation(*data)
                self.assertFalse(run(*data)['passed'])


if __name__ == '__main__':
    unittest.main()

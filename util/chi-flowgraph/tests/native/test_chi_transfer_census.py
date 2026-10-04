"""Synthetic negative cases; no simulation or protocol-validation claim."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('census_checker', ROOT / 'scripts/check-chi-transfer-census.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def fixture():
    config = {'manifest': {'profile': 'h-native-256', 'topology': [
        {'id': 'RN0', 'nodeId': 0, 'role': 'RN-F'}, {'id': 'HN0', 'nodeId': 4, 'role': 'HN-F'},
        {'id': 'SN0', 'nodeId': 8, 'role': 'SN-F'}], 'endpointPorts': {'rn': 0, 'hn': 4, 'sn': 8}}}
    base = 9007199254740993
    transfers = [('rn', 'TX', 0, 4, 7), ('hn', 'RX', 0, 4, 7), ('hn', 'TX', 4, 8, 4), ('sn', 'RX', 4, 8, 4)]
    rows, raw = [], [json.dumps({'type': 'manifest', **config['manifest']})]
    for i, (port, direction, src, tgt, opcode) in enumerate(transfers):
        row = {'boundary': port, 'observation': direction, 'channel': 'REQ', 'srcid': src,
               'tgtid': tgt, 'opcode': opcode, 'count': '1', 'firstTick': str(base + i), 'lastTick': str(base + i)}
        rows.append(row)
        raw.append(json.dumps({'type': 'observed', 'kind': 'flit', 'wireEncoding': 'native-chi',
                               'tick': str(base + i), 'boundary': port, 'observation': direction,
                               'channel': 'REQ', 'fields': {'srcid': src, 'tgtid': tgt, 'opcode': opcode}}))
    census = {'schemaVersion': '1.0', 'source': 'accepted native endpoint transfers', 'snapshotKind': 'process-exit',
              'observations': '4', 'lastTick': str(base + 3), 'counts': rows}
    return census, config, raw


class TransferCensusTests(unittest.TestCase):
    def bad(self, edit, raw=False):
        census, config, lines = fixture()
        edit(census, config, lines)
        self.assertFalse(MOD.check(census, config, lines if raw else None)['valid'])

    def test_real_shape_and_big_ticks(self):
        census, config, raw = fixture()
        report = MOD.check(census, config, raw)
        self.assertTrue(report['valid'])
        self.assertTrue(report['rawComparison']['identical'])
        self.assertEqual(report['rtlRequestCounts']['ReadUnique'], '1')
        self.assertEqual(report['rtlRequestCounts']['ReadNoSnp'], '0')
        self.assertIn('ReadNoSnp', report['missingRtlRequestOpcodes'])
        self.assertFalse(report['completeRtlRequestCoverage'])
        self.assertNotIn('pass', report)

    def test_missing_coverage_is_not_faked(self):
        census, config, _ = fixture()
        census.update(counts=[], observations='0', lastTick='0', snapshotKind='initialized')
        report = MOD.check(census, config)
        self.assertTrue(report['valid'])
        self.assertEqual(len(report['missingRtlRequestOpcodes']), 12)

    def test_numeric_tick_rejected(self):
        self.bad(lambda c, _, r: c['counts'][0].update(firstTick=9007199254740993))

    def test_numeric_count_rejected(self):
        self.bad(lambda c, _, r: c['counts'][0].update(count=1))

    def test_leading_zero_rejected(self):
        self.bad(lambda c, _, r: c['counts'][0].update(count='01'))

    def test_uint64_overflow(self):
        self.bad(lambda c, _, r: c.update(observations=str(1 << 64)))

    def test_negative(self):
        self.bad(lambda c, _, r: c['counts'][0].update(count='-1'))

    def test_zero_row(self):
        self.bad(lambda c, _, r: c['counts'][0].update(count='0'))

    def test_wrong_sum(self):
        self.bad(lambda c, _, r: c.update(observations='5'))

    def test_wrong_last_tick(self):
        self.bad(lambda c, _, r: c.update(lastTick='99'))

    def test_reversed_ticks(self):
        self.bad(lambda c, _, r: c['counts'][0].update(firstTick='9007199254740999'))

    def test_duplicate_key(self):
        self.bad(lambda c, _, r: c['counts'].append(copy.deepcopy(c['counts'][0])))

    def test_router_boundary(self):
        self.bad(lambda c, _, r: c['counts'][0].update(boundary='router.link'))

    def test_wrong_endpoint_identity(self):
        self.bad(lambda c, _, r: c['counts'][0].update(boundary='hn'))

    def test_boolean_nodeid(self):
        self.bad(lambda c, _, r: c['counts'][0].update(srcid=False))

    def test_unknown_nodeid(self):
        self.bad(lambda c, _, r: c['counts'][0].update(tgtid=99))

    def test_opcode_width(self):
        self.bad(lambda c, _, r: c['counts'][0].update(opcode=128))

    def test_wrong_raw_count(self):
        self.bad(lambda c, _, r: r.append(r[-1]), raw=True)

    def test_wrong_raw_tick(self):
        self.bad(lambda c, _, r: r.__setitem__(1, r[1].replace('9007199254740993', '9007199254740992')), raw=True)

    def test_truncated_raw(self):
        self.bad(lambda c, _, r: r.append('{"type":'), raw=True)

    def test_missing_raw_manifest(self):
        self.bad(lambda c, _, r: r.pop(0), raw=True)

    def test_wrong_raw_opcode(self):
        self.bad(lambda c, _, r: r.__setitem__(1, r[1].replace('"opcode": 7', '"opcode": 12')), raw=True)


if __name__ == '__main__':
    unittest.main()

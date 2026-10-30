#!/usr/bin/env python3
"""Check observed CMN RTL events against the reviewed, measured baseline."""
import argparse
import json
from pathlib import Path

SCENARIOS = (
    'read_unique_both_miss', 'read_unique_slc_hit', 'read_unique_sf_hit',
    'read_unique_both_hit', 'writeback_fill', 'poc_replay',
    'read_clean_sf_eu_to_en', 'read_clean_sf_su_to_sn',
    'read_clean_slc_en_allocates_sf_en', 'read_clean_slc_mn_allocates_sf_en',
    'clean_invalid_both_hit', 'continuous_same_way',
    'read_once_both_miss_no_update', 'read_once_slc_hit_no_update',
)
EVENTS = ('accept', 'busy', 'l3wr', 'l3ramwr', 'lruwr', 'sfwr', 'sframwr',
          'datawr0', 'datawr2')


def summarize(rows):
    summary = {}
    for index, name in enumerate(SCENARIOS):
        # A case has a reset/settle interval before its new cycle-0 request.
        # Discard it by starting at that case's actual accept event.
        case = [row for row in rows if int(row['test']) == index]
        start = next(i for i, row in enumerate(case)
                     if row['cycle'] == '0' and row['accept'] == '1')
        case = [row for row in case[start:] if int(row['cycle']) < 30]
        if len(case) != 30 or [int(r['cycle']) for r in case] != list(range(30)):
            raise AssertionError('%s: incomplete or duplicated cycles' % name)
        if any(int(row['eccpipe']) for row in case):
            raise AssertionError('%s: ECC pipeline is enabled' % name)
        expected = {event: [int(row['cycle']) for row in case if int(row[event])]
                    for event in EVENTS}
        expected['data_beats'] = [[int(row['cycle']), int(row['owval'])]
                                  for row in case if int(row['owval'])]
        # RTL payload buses can retain stale values when valid is low.
        expected['control'] = [dict(cycle=int(row['cycle']), replay=int(row['replay']),
                                    slc_hit=int(row['l3hit']), sf_hit=int(row['sfhit']),
                                    slc_state=int(row['slcstate']), sf_state=int(row['sfstate']))
                               for row in case if int(row['result'])]
        summary[name] = expected
    return summary


def check(record, baseline):
    if record['parameters'] != baseline['parameters']:
        raise AssertionError('Reference parameter configuration changed')
    if record['runtime_config'] != baseline['runtime_config']:
        raise AssertionError('Reference runtime configuration changed')
    for path, digest in baseline['source_sha256'].items():
        if record['source_sha256'].get(path) != digest:
            raise AssertionError('Reference RTL changed: ' + path)
    actual = summarize(record['traces'])
    for name in SCENARIOS:
        if actual[name] != baseline['scenarios'][name]:
            raise AssertionError('%s differs:\nactual %s\nexpected %s' %
                                 (name, actual[name], baseline['scenarios'][name]))
    return len(SCENARIOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace_json', type=Path)
    parser.add_argument('--expected', type=Path,
                        default=Path(__file__).with_name('expected.json'))
    args = parser.parse_args()
    count = check(json.loads(args.trace_json.read_text()), json.loads(args.expected.read_text()))
    print('PASS: %d actual RTL scenarios match the reviewed baseline' % count)


if __name__ == '__main__':
    main()

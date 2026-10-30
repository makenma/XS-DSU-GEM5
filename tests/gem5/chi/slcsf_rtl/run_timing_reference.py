#!/usr/bin/env python3
"""Check history/replay boundary cases against installed, unmodified CMN RTL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from run_reference import DEFAULT_ROOT, MODULES, PARAMETERS, generate

NAMES = ('read_once_slc_gap8', 'read_once_slc_gap10',
         'read_once_full_sf_miss_gap2', 'read_once_full_sf_miss_gap8',
         'read_once_full_sf_miss_gap10', 'fill_gap12', 'replayed_fill_then_fill',
         'clean_invalid_slc_gap8', 'clean_invalid_sf_gap8')
GAPS = (8, 10, 2, 8, 10, 12, 2, 8, 8)
REPLAYS = ((15,), (), (9,), (15,), (), (), (7,), (15,), (15,))


def check(rows):
    summary = {}
    for index, name in enumerate(NAMES):
        case = [row for row in rows if row['test'] == index]
        start = next(i for i, row in enumerate(case)
                     if row['cycle'] == 0 and row['accept'] == 1)
        case = [r for r in case[start:] if r['cycle'] < 32]
        assert [r['cycle'] for r in case] == list(range(32)), (name, 'incomplete trace')
        accepts = [r['cycle'] for r in case if r['accept']]
        controls = [r['cycle'] for r in case if r['valid']]
        replays = [r['cycle'] for r in case if r['valid'] and r['replay']]
        assert accepts == [0, GAPS[index]], (name, 'admission', accepts)
        assert controls == [7, GAPS[index] + 7], (name, 'control', controls)
        assert replays == list(REPLAYS[index]), (name, 'replay', replays)
        writes = [(r['cycle'], r['l3write']) for r in case if r['l3write']]
        sf_writes = [(r['cycle'], r['sfwrite']) for r in case if r['sfwrite']]
        if index < 5:
            assert not writes and not sf_writes, (name, 'unexpected update')
        if index == 5:
            assert writes == [(11, 1), (23, 1)], (name, 'fill writes', writes)
        if index == 6:
            assert writes == [(13, 2)], (name, 'raw reservation lost', writes)
        if index == 7:
            assert writes == [(11, 1)], (name, 'replay wrote tag', writes)
        if index == 8:
            assert sf_writes == [(12, 1)], (name, 'replay wrote SF', sf_writes)
        # A non-updating read still carries its history through cycle 14.
        if index in (0, 2, 3):
            field = 'l3hist' if index == 0 else 'sfhist'
            assert case[14][field] and not case[15][field], (name, 'history lifetime')
        summary[name] = dict(accept=accepts, control=controls, replay=replays,
                             slc_writes=writes, sf_writes=sf_writes)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl-root', type=Path, default=Path(os.environ.get('CMN700_RTL_ROOT', DEFAULT_ROOT)))
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--verilator', default='verilator')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    logical = args.rtl_root / 'logical'
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='slcsf-timing-rtl-'))
    work.mkdir(parents=True, exist_ok=True)
    source = generate(logical)
    stimulus = Path(__file__).with_name('timing_stimulus.svh').read_text()
    source = source.replace(Path(__file__).with_name('stimulus.svh').read_text(), stimulus)
    tb = work / 'slcsf_rtl_tb.sv'
    tb.write_text(source)
    command = [args.verilator, '--binary', '--timing', '--top-module', 'slcsf_rtl_tb',
               '-Wno-fatal', '-Wno-PINMISSING', '-Wno-WIDTH', '-Wno-TIMESCALEMOD',
               '-DARM_ASSERT_OFF', '-j', str(args.jobs)]
    for directory in sorted(logical.rglob('verilog')):
        command += ['-I' + str(directory), '-y', str(directory)]
    command += ['-G%s=%s' % item for item in PARAMETERS.items()]
    sources = [logical / 'cmn_hns/verilog' / (name + '.sv') for name in MODULES]
    command += ['--Mdir', str(work / 'obj'), str(tb)] + [str(p) for p in sources]
    with (work / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, CCACHE_DISABLE='1'), check=True)
    result = subprocess.run([str(work / 'obj/Vslcsf_rtl_tb')], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    (work / 'trace.log').write_text(result.stdout)
    rows = [{k: int(v) for k, v in re.findall(r'(\w+)=(-?\d+)', line)}
            for line in result.stdout.splitlines() if line.startswith('TIMING ')]
    record = dict(release='r3p0-00eac0', parameters=PARAMETERS,
                  source_sha256={str(p.relative_to(args.rtl_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sources}, traces=rows)
    record['scenarios'] = check(rows)
    (work / 'reference.json').write_text(json.dumps(record, indent=2) + '\n')
    print('PASS: %d CMN RTL history/replay scenarios' % len(record['scenarios']))
    print('RTL trace:', work / 'reference.json')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Measure the installed SF SEQ registers via real tagpipe interface stimulus.

ACK and deallocation are driven at the SF/POC boundary; this deliberately does
not claim to simulate the whole POC. No vendor implementation is copied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import run_reference as base


def check(record):
    rows = {(int(r['test']), int(r['cycle'])): r for r in record['traces']}
    def expect(test, cycle, **values):
        row = rows[(test, cycle)]
        for name, value in values.items():
            radix = 16 if name in ('valid', 'sent', 'broadcast') else 10
            assert int(row[name], radix) == value, (test, cycle, name, row[name], value)
    # H6 write enable, H7 SEQ valid, following edge registered H0 request.
    for test in range(7):
        expect(test, 6, victim=1, valid=0, req=0)
        expect(test, 7, valid=1, sent=0, req=0)
        expect(test, 8, valid=1, req=1, reqslot=0)
    # H1 ACK -> sent +1, H2 deallocation -> valid clears +2 (ACK +3).
    expect(0, 20, ack=1, sent=0, valid=1)
    expect(0, 21, dealloc0=1, sent=1, valid=1)
    expect(0, 22, ptr=1, valid=1)
    expect(0, 23, valid=0)
    # Hazard holds valid after sent until the deallocation input is sampled.
    expect(1, 29, sent=1, valid=1)
    expect(1, 30, dealloc1=1, valid=1)
    expect(1, 31, valid=0)
    # H5 hit conservatively broadens the SEQ despite the subsequent replay.
    expect(2, 25, hit=1, broadcast=0)
    expect(2, 26, broadcast=1)
    expect(2, 27, replay=1, broadcast=1)
    for cycle in range(24, 30):
        expect(3, cycle, hit=0, broadcast=0)
    # Reused slot zero waits for the physical pointer to walk all 12 slots.
    expect(4, 47, valid=1, ptr=1, req=0)
    for cycle in range(48, 58):
        expect(4, cycle, ptr=cycle-46, req=0)
    expect(4, 58, ptr=0, req=0)
    expect(4, 59, ptr=0, req=1, reqslot=0)
    expect(5, 7, broadcast=1)
    expect(6, 25, hit=1)
    expect(6, 26, broadcast=1, snpbrd=0, snpdir=0, nofill=1)
    # LAT2 aligns only the external H4 replay pulse at accepted +3.
    for test in range(7, 13):
        expect(test, 7, replay=int(test == 10), valid=int(test != 10))
    return 13


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rtl-root', type=Path, default=Path(base.DEFAULT_ROOT))
    p.add_argument('--work-dir', type=Path, default=Path('/tmp/slcsf-seq-reference'))
    p.add_argument('--verilator', default='/usr/local/bin/verilator')
    p.add_argument('--jobs', type=int, default=4)
    args = p.parse_args()
    work = args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    logical = args.rtl_root / 'logical'
    stimulus = Path(__file__).with_name('seq_stimulus.svh').read_text()
    tb = work / 'slcsf_rtl_tb.sv'
    tb.write_text(base.generate(logical).replace(
        Path(__file__).with_name('stimulus.svh').read_text(), stimulus))
    cmd = [args.verilator, '--binary', '--timing', '--top-module', 'slcsf_rtl_tb',
           '-Wno-fatal', '-Wno-PINMISSING', '-Wno-WIDTH', '-Wno-TIMESCALEMOD',
           '-DARM_ASSERT_OFF', '-j', str(args.jobs)]
    for directory in sorted(logical.rglob('verilog')):
        cmd += ['-I' + str(directory), '-y', str(directory)]
    cmd += ['-G%s=%s' % item for item in base.PARAMETERS.items()]
    cmd += ['--Mdir', str(work / 'obj'), str(tb)]
    sources = [logical / 'cmn_hns/verilog' / (n + '.sv') for n in base.MODULES]
    cmd += [str(path) for path in sources]
    with (work / 'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, CCACHE_DISABLE='1'), check=True)
    output = subprocess.check_output([str(work / 'obj/Vslcsf_rtl_tb')], text=True)
    (work / 'trace.log').write_text(output)
    traces = [dict(re.findall(r'(\w+)=([^ ]+)', line))
              for line in output.splitlines() if line.startswith('SEQ ')]
    sources += [logical / "cmn_hns/verilog/cmn_hns_poc_addr.sv",
                logical / "cmn_hns/verilog/cmn_hns_poc_ctl.sv",
                logical / "shared/verilog/cmn_hns_localparams.v"]
    record = dict(parameters=base.PARAMETERS,
                  source_sha256={str(s.relative_to(args.rtl_root)):
                                 hashlib.sha256(s.read_bytes()).hexdigest() for s in sources},
                  traces=traces)
    (work / 'reference.json').write_text(json.dumps(record, indent=2) + '\n')
    count = check(record)
    print('PASS: %d actual RTL SEQ and victim-replay scenarios' % count)
    print(work / 'reference.json')


if __name__ == '__main__':
    main()

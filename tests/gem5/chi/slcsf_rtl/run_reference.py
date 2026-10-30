#!/usr/bin/env python3
"""Run installed CMN700 RTL; never copies vendor source into this repository.

The generated fixture instantiates the actual SLC/SF tag pipelines and data
datapath. SRAM response ports contain bounded, known tag/data fixtures;
this is an interface timing test, not a full SRAM/NoC simulation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

DEFAULT_ROOT = '/nfs/home/lilongfei/tl_test/cmn/CMN700_3x2_4C_SBSX'
MODULES = ('cmn_hns_l3_tagpipe', 'cmn_hns_sf_tagpipe', 'cmn_hns_l3_datadp')
PARAMETERS = dict(HNS_SLC_SIZE_PARAM=1, HNS_SF_SIZE_PARAM=3,
    HNS_SLC_NUM_WAYS_PARAM=16, HNS_SF_NUM_WAYS_PARAM=16,
    HNS_SLC_DSLICE_PARAM=0, HNS_SLC_TAG_RAM_LATENCY_PARAM=2,
    HNS_SLC_DATA_RAM_LATENCY_PARAM=2, HNS_NUM_ENTRIES_POCQ_PARAM=16,
    HNS_MIN_SF_RNFVEC_WIDTH_PARAM=4, HNS_LID_WIDTH_PARAM=2,
    POR_CHI_MPAM_ENABLE_PARAM=0, POR_CHI_MTE_ENABLE_PARAM=0)
RUNTIME_CONFIG = dict(ecc_disable=1, slc_ecc_pipe_mode_dis=1,
                      other_configuration_inputs=0, bounded_sram_response_ports=True)


def ports(source):
    header = source[:re.search(r'^\s*\);', source, re.M).start()]
    result = []
    for direction, declaration in re.findall(r'^\s*(input|output)\s+([^\n]+)', header, re.M):
        declaration = declaration.split('//')[0].strip().rstrip(',').strip()
        name = re.search(r'(\w+)\s*$', declaration).group(1)
        if not re.match(r'(logic|wire|\w+_TYPE)\b', declaration):
            declaration = 'logic ' + declaration
        result.append((direction, name, declaration))
    return result


def generate(logical):
    sources = [(logical / 'cmn_hns/verilog' / (name + '.sv')).read_text() for name in MODULES]
    wrapper = (logical / 'cmn_hns/verilog/cmn_hns_wrap.sv').read_text()
    # Resolve the vendor's actual parameterized structs, without reproducing
    # their declarations or implementation in this repository.
    bindings = wrapper[wrapper.index(' `include "cmn_hns_localparams.v"'):
                       wrapper.index('  wire                                                                        reset_por;')]
    includes = '\n'.join(re.findall(r'^`include.*$', sources[0][:sources[0].index('module ')], re.M))
    lines = [includes, 'module slcsf_rtl_tb import cmn_hns_unit_pkgs::*; import cmn_chix_pkg::*; `CMN_HNS_WRAP_PARAM_DECL ();',
             '`include "por_functions.v"', bindings]
    declarations = {}
    driven = set()
    per_module = []
    for source in sources:
        ps = ports(source)
        per_module.append(ps)
        for direction, name, declaration in ps:
            declarations.setdefault(name, declaration)
            if direction == 'output':
                driven.add(name)
    for declaration in declarations.values():
        lines.append(declaration + ';')
    for name, ps in zip(MODULES, per_module):
        lines.append(name + ' `CMN_HNS_UNIT_PARAM_INST ' + name + ' (\n' +
                     ',\n'.join('.%s(%s)' % (p[1], p[1]) for p in ps) + '\n);')
    lines.append('initial begin\n' + '\n'.join(name + "='0;" for name in declarations if name not in driven))
    lines.append(Path(__file__).with_name('stimulus.svh').read_text())
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl-root', type=Path, default=Path(os.environ.get('CMN700_RTL_ROOT', DEFAULT_ROOT)))
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--verilator', default='verilator')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    logical = args.rtl_root / 'logical'
    work = args.work_dir or Path(tempfile.mkdtemp(prefix='slcsf-rtl-'))
    work.mkdir(parents=True, exist_ok=True)
    tb = work / 'slcsf_rtl_tb.sv'
    tb.write_text(generate(logical))
    command = [args.verilator, '--binary', '--timing', '--top-module', 'slcsf_rtl_tb',
               '-Wno-fatal', '-Wno-PINMISSING', '-Wno-WIDTH', '-Wno-TIMESCALEMOD',
               '-DARM_ASSERT_OFF', '-j', str(args.jobs)]
    for directory in sorted(logical.rglob('verilog')):
        command += ['-I' + str(directory), '-y', str(directory)]
    command += ['-G%s=%s' % item for item in PARAMETERS.items()]
    command += ['--Mdir', str(work / 'obj'), str(tb)]
    sources = [logical / 'cmn_hns/verilog' / (name + '.sv') for name in MODULES]
    command += [str(path) for path in sources]
    env = dict(os.environ, CCACHE_DISABLE='1')
    with (work / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    result = subprocess.run([str(work / 'obj/Vslcsf_rtl_tb')], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    (work / 'trace.log').write_text(result.stdout)
    rows = []
    for line in result.stdout.splitlines():
        if line.startswith('TRACE '):
            rows.append(dict(re.findall(r'(\w+)=([^ ]+)', line)))
    reference_files = sources + [logical / 'cmn_hns/verilog/cmn_hns_wrap.sv',
                       logical / 'cmn_hns/verilog/cmn_hns_l3_data_llib.sv',
                       logical / 'cmn_hns/verilog/cmn_hns_poc_addr.sv',
                       logical / 'cmn_hns/verilog/cmn_hns_poc_ctl.sv',
                       logical / 'cmn_hns/verilog/cmn_hns_cfg_params.v',
                       logical / 'shared/verilog/cmn_hns_defines.v',
                       logical / 'shared/verilog/cmn_hns_localparams.v',
                       logical / 'shared/verilog/cmn_hns_structs.sv',
                       logical / 'cmn700/verilog/cmn700_3x2_4c_sbsx.v']
    record = dict(release='r3p0-00eac0', rtl_root=str(args.rtl_root), parameters=PARAMETERS,
                  runtime_config=RUNTIME_CONFIG,
                  simulator=subprocess.check_output([args.verilator, '--version'], text=True).strip(),
                  source_sha256={str(p.relative_to(args.rtl_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in reference_files}, traces=rows)
    (work / 'reference.json').write_text(json.dumps(record, indent=2) + '\n')
    from check_reference import check
    count = check(record, json.loads(Path(__file__).with_name('expected.json').read_text()))
    print('PASS: %d actual RTL scenarios match the reviewed baseline' % count)
    print('RTL trace:', work / 'reference.json')
    print('Build log:', work / 'build.log')


if __name__ == '__main__':
    main()

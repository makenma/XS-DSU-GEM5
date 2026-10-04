"""Check CPU completion and raw RN request coverage, without protocol refinement."""
import argparse
from collections import Counter
import configparser
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
parser.add_argument('--allow-missing-coverage', action='store_true',
                    help='Diagnostic only: still report missing opcodes; never mark complete coverage')
args = parser.parse_args()
out = args.directory
result = json.loads((out / 'native-l2-result.json').read_text())
log = (out / 'run.log').read_text()
required = ['ReadNotSharedDirty', 'ReadUnique', 'MakeUnique', 'CleanShared', 'CleanInvalid',
            'MakeInvalid', 'Evict', 'WriteBackFull', 'WriteCleanFull', 'WriteEvictOrEvict',
            'ReadNoSnp', 'WriteNoSnpPtl']
wire_opcode = dict(zip(required, [0x26, 7, 12, 8, 9, 10, 13, 0x1b, 0x17, 0x42, 4, 0x1c]))
counts = Counter()
first = {}
issues = []
configuration = configparser.ConfigParser(interpolation=None)
configuration.read(out / 'config.ini')
actual_types = Counter(configuration.get(section, 'type', fallback='')
                       for section in configuration.sections())
if actual_types['ChiNativeL2'] != 4 or actual_types['Cache2ChiBridge'] != 0:
    issues.append({'reason': 'config.ini must have four native L2 instances and no classic bridge'})
weoe_pending = {}
weoe_branches = Counter()
total = 0
nc = result.get('uncachedMapping') or {}
nc_base = int(nc.get('physical', '0'), 0)
nc_end = nc_base + nc.get('bytes', 0)
with (out / 'raw.jsonl').open() as source:
    manifest = json.loads(next(source))
    program = manifest.get('program', {})
    native = manifest.get('nativeL2', {})
    if not (manifest.get('type') == 'manifest' and manifest.get('profile') == 'h-native-256'
            and manifest.get('source', {}).get('nativeChi') is True
            and manifest.get('source', {}).get('generator') == 'native/gem5/native_l2_run.py'
            and program.get('rtlRequestWorkload') is True
            and native.get('classicL2') is False
            and native.get('protocolOpcodeInjection') is False
            and program.get('protocolOpcodeInjection') is False):
        issues.append({'reason': 'missing native RTL CPU capture provenance'})
    if not (nc.get('kind') == 'normal-noncacheable-ram' and nc.get('cacheableAlias') is False
            and nc.get('bytes', 0) > 0):
        issues.append({'reason': 'missing separate normal noncacheable RAM mapping'})
    nodes = {n['id']: n for n in manifest.get('topology', [])}
    for line in source:
        event = json.loads(line)
        if event.get('kind') != 'flit' or event.get('observation') != 'TX':
            continue
        total += 1
        if event.get('wireEncoding') != 'native-chi':
            issues.append({'event': event.get('id'), 'reason': 'non-native wire event'})
        fields = event.get('fields', {})
        if event.get('channel') == 'RSP':
            key = (fields.get('tgtid'), fields.get('txnid'))
            if key in weoe_pending and fields.get('srcid') == weoe_pending[key]:
                if fields.get('opcode') in (4, 5):
                    weoe_branches['Comp' if fields['opcode'] == 4 else 'CompDBIDResp'] += 1
                    del weoe_pending[key]
                elif fields.get('opcode') == 3:
                    del weoe_pending[key]
        if event.get('channel') != 'REQ' or not event.get('source', '').startswith('RN'):
            continue
        op = event['opcode']
        node = nodes.get(event['source'], {})
        if node.get('role') != 'RN-F' or node.get('nodeId') != fields.get('srcid'):
            issues.append({'event': event.get('id'), 'reason': 'RN label and wire NodeID mismatch'})
        if op not in wire_opcode or fields.get('opcode') != wire_opcode[op]:
            issues.append({'event': event.get('id'), 'reason': 'REQ opcode label and raw value mismatch'})
        if op == 'WriteEvictOrEvict':
            weoe_pending[(fields.get('srcid'), fields.get('txnid'))] = fields.get('tgtid')
        counts[op] += 1
        first.setdefault(op, {k: event.get(k) for k in
                              ('id', 'tick', 'source', 'target', 'address', 'fields', 'causeInstructionIds')})
        addr = int(event.get('address', '0'), 0)
        if op in ('ReadNoSnp', 'WriteNoSnpPtl') and not nc_base <= addr < nc_end:
            issues.append({'event': event['id'], 'reason': 'NoSnp outside configured normal NC RAM'})
        if nc_base <= addr < nc_end and op not in ('ReadNoSnp', 'WriteNoSnpPtl'):
            issues.append({'event': event['id'], 'reason': 'cacheable request in normal NC RAM'})
        if nc_base <= addr < nc_end and not (fields.get('memattr') == 0
                and fields.get('order') == 2 and fields.get('expCompAck') == 0):
            issues.append({'event': event['id'], 'reason': 'normal NC request attributes mismatch'})
missing = [op for op in required if not counts[op]]
unexpected = sorted(set(counts) - set(required))
hart_pass = [log.splitlines().count(f'NATIVE_RTL_HART_{hart}_PASS') == 1 for hart in range(4)]
cpu_pass = (result.get('pass') is True and result.get('exitCode') == 0
            and result.get('cause') == 'exiting with last active thread context'
            and result.get('protocolOpcodeInjection') is False
            and result.get('timedCpuPath') ==
                'KMH CPU -> classic L1 -> ChiNativeL2 -> CHI NoC -> HN -> ChiNativeMemory'
            and all(hart_pass) and 'NATIVE_RTL_FAIL' not in log)
report = {'pass': cpu_pass and total > 0 and not issues and not unexpected and
          (not missing or args.allow_missing_coverage),
          'cpuPass': cpu_pass, 'hartPass': hart_pass,
          'cpuExitTick': result.get('cpuExitTick'), 'drainEndTick': result.get('drainEndTick'),
          'protocolOpcodeInjection': False, 'rawTxFlits': total,
          'actualNativeL2Instances': actual_types['ChiNativeL2'],
          'requestCounts': dict(counts), 'firstRequest': first,
          'writeEvictOrEvictResponses': dict(weoe_branches),
          'requiredRequestOpcodes': required, 'missingRequestOpcodes': missing,
          'unexpectedRequestOpcodes': unexpected, 'completeRequestCoverage': not missing,
          'uncachedMapping': nc, 'issues': issues,
          'scope': 'CPU data checks and raw RN opcode coverage; no RTL lockstep equivalence proof'}
(out / 'native-rtl-result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'firstRequest'}, indent=2))
if not report['pass']:
    raise SystemExit('Native RTL workload or raw request coverage failed')

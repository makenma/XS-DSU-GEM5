"""Validate every real Linux CBO row; a truncated or disabled-only run is not full coverage."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


def check(log, manifest, phases):
    errors = []
    def require(ok, message):
        if not ok: errors.append(message)
    def value(item, key):
        try:
            text = item[key]
            if not isinstance(text, str) or not text.startswith('0x'): raise ValueError()
            return int(text, 16)
        except (KeyError, ValueError, TypeError):
            errors.append(f'invalid hexadecimal {key}')
            return -1
    require(manifest.get('suite') == 'native-linux-cbo', 'incorrect build manifest suite')
    require(all(manifest.get('assemblyChecks', {}).get(name) is True for name in
                ('rv64Elf', 'fixedExecutable', 'inval', 'clean', 'flush')), 'missing compiled-instruction audit')
    sections = []
    active = None
    prefixes = ['BEGIN', 'RESULT', 'PTE', 'ENV', 'END', 'ABORT']
    for line in log.splitlines():
        for kind in prefixes:
            prefix = f'LINUX_CBO_{kind} '
            if not line.startswith(prefix): continue
            try:
                record = json.loads(line[len(prefix):])
                if not isinstance(record, dict): raise ValueError()
            except (ValueError, TypeError):
                errors.append(f'malformed {kind} JSON')
                break
            if kind == 'BEGIN':
                require(active is None, 'nested BEGIN or missing prior END')
                active = {'BEGIN': record, 'RESULT': [], 'PTE': [], 'ENV': [], 'END': None}
                sections.append(active)
            elif kind == 'ABORT':
                errors.append(f'runtime abort: {record}')
            elif active is None:
                errors.append(f'{kind} outside a complete phase')
            elif kind == 'END':
                active['END'] = record
                active = None
            else:
                active[kind].append(record)
            break
    require(active is None, 'truncated phase: no END')
    require([s['BEGIN'].get('phase') for s in sections] == phases, 'missing, duplicated, or reordered phases')
    ops = ['inval', 'clean', 'flush']
    cases = ['aligned-rw', 'unaligned-rw', 'readonly', 'noaccess', 'unmapped']
    for section in sections:
        begin, end = section['BEGIN'], section['END'] or {}
        phase = begin.get('phase')
        enabled = phase == 'enabled'
        cpus = begin.get('cpus', [])
        require(phase in ('enabled', 'disabled'), 'unknown phase')
        require(len(cpus) == 4 and all(type(c) is int and c >= 0 for c in cpus) and len(set(cpus)) == 4,
                'four distinct real CPU affinities required')
        require(begin.get('buildId') == manifest.get('buildId') and len(str(begin.get('buildId'))) == 64,
                'runtime/build identity mismatch')
        require(begin.get('schemaVersion') == 1 and begin.get('lineBytes') == 64, 'bad format or cache line size')
        require(begin.get('pteHelper') is enabled, 'D-bit helper scope mismatch')
        expected = list(itertools.product(cpus, ops, cases))
        actual = [(r.get('cpu'), r.get('operation'), r.get('case')) for r in section['RESULT']]
        require(actual == expected, f'{phase}: incomplete/reordered CPU/operation/case inventory')
        for row in section['RESULT']:
            name, op = row.get('case'), row.get('operation')
            label = f'{phase}/cpu{row.get("cpu")}/{op}/{name}: '
            sig = 4 if not enabled else 11 if name in ('noaccess', 'unmapped') else 0
            code = 1 if not enabled else 2 if name == 'noaccess' else 1 if name == 'unmapped' else 0
            require(row.get('phase') == phase, label + 'phase mismatch')
            require(row.get('signal') == sig and row.get('siCode') == code,
                    label + 'unexpected signal or si_code')
            require(row.get('expectedSignal') == sig and row.get('expectedCode') == code,
                    label + 'runtime incorrectly weakened expectation')
            address = value(row, 'address')
            si_address = value(row, 'siAddress')
            offset = {'aligned-rw': 0, 'unaligned-rw': 13, 'readonly': 17, 'noaccess': 37, 'unmapped': 53}.get(name)
            require(address > 0 and address % 64 == offset, label + 'wrong original rs1 alignment')
            if sig == 11:
                require(si_address == address, label + 'fault lost original rs1 in tval/si_addr')
            elif sig == 4:
                require(si_address == value(manifest.get('instructionAddresses', {}), op),
                        label + 'SIGILL was not at the audited CBO instruction')
            else:
                require(si_address == 0, label + 'unexpected fault address')
            require(row.get('byteMismatches') == 0 and row.get('pass') is True, label + 'data or self-check failure')
        ptes = section['PTE']
        expected_ptes = list(itertools.product(cpus, ops, ['pte-rw', 'pte-ro'])) if enabled else []
        require([(r.get('cpu'), r.get('operation'), r.get('case')) for r in ptes] == expected_ptes,
                f'{phase}: missing or unexpected D-bit tests')
        for row in ptes:
            label = f'pte/cpu{row.get("cpu")}/{row.get("operation")}/{row.get("case")}: '
            writable = row.get('case') == 'pte-rw'
            before, cleared, after, restored = [value(row, key) for key in ('before', 'cleared', 'after', 'restored')]
            require(before > 0 and before & 0x53 == 0x53, label + 'PTE must be present, user, readable, accessed')
            require(bool(before & 4) == writable and row.get('writableVma') == int(writable), label + 'wrong PTE/VMA protection')
            require(cleared == before & ~0x80 and not (cleared & 0x80), label + 'D was not actually cleared')
            require(after == cleared and not (after & 0x80), label + 'CBO modified D or another PTE field')
            require(restored == before, label + 'PTE was not restored')
            require(row.get('ioctlError') == 0 and row.get('instructionError') == 0 and row.get('pass') is True,
                    label + 'kernel helper or instruction faulted')
        envs = section['ENV']
        require([(r.get('stage'), r.get('cpu')) for r in envs] ==
                list(itertools.product(['before', 'enabled', 'restored'], cpus)) if enabled else not envs,
                f'{phase}: missing per-hart permission/restore evidence')
        if enabled:
            require(manifest.get('module', {}).get('built') is True, 'enabled phase lacks matching built kernel module')
            table = {(r.get('stage'), r.get('cpu')): value(r, 'senvcfg') for r in envs}
            for cpu in cpus:
                before, active_env, restored = [table.get((s, cpu), -1) for s in ('before', 'enabled', 'restored')]
                require(before >= 0 and before & 0x70 == 0, 'stock Linux must start with U CBO disabled')
                require(active_env == before | 0x70, 'temporary helper did not enable exact CBO bits')
                require(restored == before, 'temporary helper did not restore original senvcfg')
        require(end.get('phase') == phase and end.get('rows') == (84 if enabled else 60) and
                end.get('errors') == 0 and end.get('pass') is True, f'{phase}: missing or failing final accounting')
    return {'suite': 'native-linux-cbo', 'phases': phases, 'passed': not errors,
            'fullCoverage': not errors and phases == ['disabled', 'enabled'], 'errors': errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=Path('build/linux-cbo/manifest.json'))
    parser.add_argument('--phase', choices=['all', 'disabled', 'enabled'], default='all')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    result = check(args.log.read_text(errors='replace'), manifest,
                   ['disabled', 'enabled'] if args.phase == 'all' else [args.phase])
    binary = args.manifest.parent / manifest['binary']
    if not binary.exists() or hashlib.sha256(binary.read_bytes()).hexdigest() != manifest['binarySha256']:
        result['errors'].append('binary hash does not match manifest')
        result['passed'] = result['fullCoverage'] = False
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end='')
    raise SystemExit(0 if result['passed'] else 1)

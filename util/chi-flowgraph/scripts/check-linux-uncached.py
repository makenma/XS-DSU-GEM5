"""Check all CPU/width/line samples independently; no guest-only CHI coverage claim."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path


def check(log, manifest, fs_manifest):
    errors = []
    def require(ok, message):
        if not ok: errors.append(message)
    def number(record, field):
        try:
            val = record[field]
            if not isinstance(val, str) or not val.startswith('0x'): raise ValueError()
            return int(val, 16)
        except (KeyError, ValueError, TypeError):
            errors.append(f'invalid hexadecimal {field}')
            return -1
    require(manifest.get('suite') == 'native-linux-uncached', 'wrong build suite')
    require(all(manifest.get('assemblyChecks', {}).get(name) is True for name in
                ['rv64Elf', 'fixedExecutable', *[f'nc_{op}{bits}' for op in ['load', 'store'] for bits in [8, 16, 32, 64]]]),
            'missing actual ELF instruction audit')
    require(manifest.get('module', {}).get('built') is True, 'missing compiled module evidence')
    records = {'INFO': [], 'SAMPLE': [], 'RESULT': []}
    order = []
    for line in log.splitlines():
        for kind in records:
            prefix = f'LINUX_NC_{kind} '
            if not line.startswith(prefix): continue
            try:
                item = json.loads(line[len(prefix):])
                if not isinstance(item, dict): raise ValueError()
                records[kind].append(item)
                order.append(kind)
            except (ValueError, TypeError):
                errors.append(f'invalid {kind} JSON')
    require(order == ['INFO'] + ['SAMPLE'] * 64 + ['RESULT'], 'missing, duplicate, reordered or truncated records')
    if len(records['INFO']) != 1 or len(records['RESULT']) != 1:
        return {'passed': False, 'errors': errors}
    info, result = records['INFO'][0], records['RESULT'][0]
    cpus = info.get('cpus')
    require(isinstance(cpus, list) and len(cpus) == 4 and len(set(str(x) for x in cpus)) == 4
            and all(type(x) is int and x >= 0 for x in cpus), 'exactly four distinct CPUs required')
    require(info.get('abi') == 1 and info.get('bytes') == 4096 and info.get('dtNoMap') is True, 'invalid reserved RAM ABI')
    require(info.get('buildId') == manifest.get('buildId') == result.get('buildId'), 'build identity mismatch')
    base = number(info, 'physicalStart')
    require(base >= 0 and base % 4096 == 0, 'reserved page alignment')
    protection = number(info, 'mappingProtection')
    # Ordinary user RW PTE: R/W/U set, X clear, PBMT bits 61..62 clear. PMA
    # supplies the Normal-NC type; this model does not advertise Svpbmt.
    require(protection >= 0 and (protection & 0x16) == 0x16 and not (protection & (0x8 | (3 << 61))),
            'mapping must be user RW non-executable Normal PTE without PBMT override')
    fs = fs_manifest.get('manifest', fs_manifest)
    region = fs.get('linux', {}).get('uncachedTestRam') or {}
    require(number(region, 'start') == base and region.get('bytes') == 4096, 'guest physical page differs from FS PMA reservation')
    require(region.get('kind') == 'normal-noncacheable-ram' and region.get('cacheableAlias') is False
            and region.get('reservedNoMap') is True and region.get('Device') is False
            and region.get('MemAttr') == 0 and region.get('Order') == 2
            and region.get('ExpCompAck') is False, 'FS Normal-NC policy does not match test')
    require(fs.get('source', {}).get('fullSystem') is True and fs.get('source', {}).get('nativeChi') is True,
            'test must use full-system native CHI')
    seen = set()
    expected_lines = {}
    expected_order = list(itertools.product(range(4), range(4), [1, 2, 4, 8]))
    actual_order = []
    for sample in records['SAMPLE']:
        index, line, width = sample.get('index'), sample.get('line'), sample.get('width')
        if type(index) is not int or type(line) is not int or type(width) is not int or (index, line, width) not in expected_order:
            errors.append('invalid CPU/line/width sample'); continue
        key = (index, line, width)
        actual_order.append(key)
        require(key not in seen, f'duplicate sample {key}'); seen.add(key)
        offset = {1: 5, 2: 10, 4: 20, 8: 40}[width]
        line_offset = index * 256 + line * 64
        value = (0x8877665544332200 ^ (index << 5) ^ (line << 3) ^ width) & ((1 << (width * 8)) - 1)
        require(isinstance(cpus, list) and len(cpus) == 4 and sample.get('cpu') == cpus[index], f'CPU affinity {key}')
        require(sample.get('lineOffset') == line_offset and sample.get('offset') == offset, f'line/lane selection {key}')
        require(number(sample, 'physicalAddress') == base + line_offset + offset, f'physical address {key}')
        require(number(sample, 'written') == value and number(sample, 'read') == value, f'actual value mismatch {key}')
        expected = expected_lines.setdefault((index, line), bytearray(0xa5 ^ (index * 17) ^ (line * 7) ^ b for b in range(64)))
        expected[offset:offset + width] = value.to_bytes(width, 'little')
        require(sample.get('lineData') == expected.hex(), f'64-byte payload or neighboring-byte corruption {key}')
        require(sample.get('matches') is True, f'guest sample failed {key}')
    require(actual_order == expected_order, 'samples do not cover every hart/line/width in execution order')
    require(result.get('completed') == 64 and result.get('errors') == 0 and result.get('status') == 'PASS', 'guest did not finish cleanly')
    return {'passed': not errors, 'errors': errors, 'samples': len(records['SAMPLE']),
            'physicalStart': hex(base), 'cpus': cpus,
            'claim': 'Full-system CPU Normal-NC data integrity; accepted CHI REQ coverage requires independent simulator evidence.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--log', required=True, type=Path)
    p.add_argument('--manifest', type=Path, default=Path('build/linux-uncached/manifest.json'))
    p.add_argument('--fs-config', required=True, type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text())
    binary = a.manifest.parent / manifest['binary']
    if not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != manifest.get('binarySha256'):
        raise RuntimeError('ELF does not match the supplied manifest')
    report = check(a.log.read_text(errors='replace'), manifest, json.loads(a.fs_config.read_text()))
    text = json.dumps(report, indent=2) + '\n'
    if a.output: a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(text)
    print(text, end='')
    raise SystemExit(0 if report['passed'] else 1)

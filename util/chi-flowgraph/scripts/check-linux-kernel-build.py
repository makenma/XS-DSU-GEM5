"""Check serial evidence of a fresh native tinyconfig+SMP Linux Image build."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re


def check(text):
    lines = text.replace('\r', '').splitlines()
    errors = []

    def one(prefix):
        matches = [(index, line[len(prefix):]) for index, line in enumerate(lines)
                   if line.startswith(prefix)]
        if len(matches) != 1:
            raise ValueError(f'expected exactly one {prefix.strip()}')
        return matches[0]

    try:
        begin_at, begin_json = one('CHI_KERNEL_BUILD_BEGIN ')
        end_at, end_json = one('CHI_KERNEL_BUILD_END ')
        begin, end = json.loads(begin_json), json.loads(end_json)
        if begin_at >= end_at:
            errors.append('end precedes begin')
        if any(line.startswith('CHI_KERNEL_BUILD_FAIL') for line in lines):
            errors.append('guest failure marker')
        for key, value in {'profile': 'tinyconfig-smp4', 'sourceVersion': '6.6.52',
                           'jobs': 4, 'onlineCpus': '0-3', 'architecture': 'riscv64',
                           'freshOutput': True}.items():
            if type(begin.get(key)) is not type(value) or begin.get(key) != value:
                errors.append(f'wrong begin {key}')
        if not re.fullmatch(r'riscv64[^\s]*-linux-gnu', begin.get('compilerTarget', '')):
            errors.append('compiler target is not native RISC-V GNU/Linux')
        if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', begin.get('compilerVersion', '')):
            errors.append('missing compiler version')
        for key, value in {'profile': 'tinyconfig-smp4', 'exitCode': 0, 'target': 'Image',
                           'jobs': 4, 'elfClass': 'ELF64', 'elfMachine': 'RISC-V'}.items():
            if type(end.get(key)) is not type(value) or end.get(key) != value:
                errors.append(f'wrong end {key}')
        for key in ('imageBytes', 'objectFiles'):
            if type(end.get(key)) is not int or end[key] <= 0:
                errors.append(f'no positive {key}')
        for key in ('imageSha256', 'vmlinuxSha256', 'configSha256', 'makeLogSha256'):
            if not re.fullmatch('[0-9a-f]{64}', end.get(key, '')):
                errors.append(f'missing {key}')
        if (type(begin.get('startSeconds')) not in (int, float)
                or type(end.get('endSeconds')) not in (int, float)
                or not math.isfinite(begin['startSeconds'])
                or not math.isfinite(end['endSeconds'])
                or not end['endSeconds'] > begin['startSeconds'] >= 0):
            errors.append('build did not advance guest time')

        def cpu_stats(marker):
            matches = [i for i, line in enumerate(lines) if line == marker]
            if len(matches) != 1:
                raise ValueError(f'missing/duplicate {marker}')
            position = matches[0]
            stats = {}
            for line in lines[position + 1:position + 5]:
                fields = line.split()
                if len(fields) < 5 or not re.fullmatch(r'cpu[0-3]', fields[0]):
                    raise ValueError(f'incomplete four-CPU {marker}')
                if fields[0] in stats or not all(value.isdigit() for value in fields[1:]):
                    raise ValueError(f'malformed/duplicate CPU counters in {marker}')
                stats[fields[0]] = [int(value) for value in fields[1:]]
            if set(stats) != {'cpu0', 'cpu1', 'cpu2', 'cpu3'}:
                raise ValueError(f'wrong CPUs in {marker}')
            return position, stats

        before_at, before = cpu_stats('CHI_KERNEL_BUILD_CPUSTAT_BEFORE')
        after_at, after = cpu_stats('CHI_KERNEL_BUILD_CPUSTAT_AFTER')
        ready = [i for i, line in enumerate(lines) if line == 'CHI_KERNEL_BUILD_CONFIG_READY']
        if len(ready) != 1 or not begin_at < before_at < ready[0] < after_at < end_at:
            errors.append('invalid build evidence order')
        deltas = {}
        for cpu in before:
            # /proc/stat: user, nice, system. Require actual accounted CPU
            # work on every hart, beyond merely passing -j4 to make.
            if len(before[cpu]) != len(after[cpu]) or any(
                    b > a for b, a in zip(before[cpu], after[cpu])):
                errors.append(f'{cpu} counters regressed')
            deltas[cpu] = sum(after[cpu][:3]) - sum(before[cpu][:3])
            if deltas[cpu] <= 0:
                errors.append(f'{cpu} did no accounted work')
        body = '\n'.join(lines[before_at:after_at])
        for label, pattern in (
                ('C compilation', r'^\s*CC\s+\S+\.o\s*$'),
                ('vmlinux link', r'^\s*LD\s+vmlinux\s*$'),
                ('Image generation', r'^\s*OBJCOPY\s+arch/riscv/boot/Image\s*$')):
            if not re.search(pattern, body, re.MULTILINE):
                errors.append(f'make log lacks {label}')
        return {'passed': not errors, 'errors': errors, 'begin': begin, 'end': end,
                'cpuWorkTicksDelta': deltas,
                'hostArtifactHashesRecomputed': False,
                'artifactEvidence': 'SHA256 values were calculated inside the guest and preserved in serial output. No exported Image/vmlinux bytes were supplied for an independent host recomputation.',
                'scope': 'One fresh native tinyconfig+SMP make -j4 Image completed; all four harts accrued work. This is not a defconfig build, a boot of the resulting Image, or a proof of all CHI/RVWMO behaviors.'}
    except (ValueError, TypeError, KeyError) as error:
        return {'passed': False, 'errors': errors + [str(error)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('serial', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = args.serial.read_bytes()
    result = check(data.decode(errors='replace'))
    result['serialSha256'] = hashlib.sha256(data).hexdigest()
    result['serialPath'] = str(args.serial)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()

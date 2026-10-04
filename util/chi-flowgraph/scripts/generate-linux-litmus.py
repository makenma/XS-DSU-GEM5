"""Run pinned official diy7 -> herd7 -> litmus7, retaining all generated tests and expectations."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess


FAMILIES = [
    ('sb32', 6, 'Fre', 'PodWR', None),
    ('lb32', 6, 'Rfe', 'PodRW', None),
    ('mp32', 6, 'Rfe,Fre', 'PodWW,PodRR', None),
    ('iriw32', 6, 'Rfe,Fre', 'PodRR', None),
    ('orders32', 6, 'Rfe,Fre,Coe,PodRR,PodRW,PodWR,PodWW', None, None),
    ('coherence32', 6, 'Rfe,Fre,Coe,PosRR,PosRW,PosWR,PosWW', None, None),
    ('fenced32', 6, 'Rfe,Fre,Coe,Fence.rw.rwdRR,Fence.rw.rwdRW,Fence.rw.rwdWR,Fence.rw.rwdWW', None, None),
    ('sb64', 6, 'Fre', 'PodWR', 'uint64_t'),
    ('lb64', 6, 'Rfe', 'PodRW', 'uint64_t'),
    ('mp64', 6, 'Rfe,Fre', 'PodWW,PodRR', 'uint64_t'),
    ('iriw64', 6, 'Rfe,Fre', 'PodRR', 'uint64_t'),
    ('fenced64', 6, 'Rfe,Fre,Coe,Fence.rw.rwdRR,Fence.rw.rwdRW,Fence.rw.rwdWR,Fence.rw.rwdWW', None, 'uint64_t'),
]

# Exhaustive deterministic cycle enumeration within each declared bound. The
# dependency/fence family alone yields 1629 tests at size 8 with this pinned tool.
LARGE_FAMILIES = [
    ('dependency_fences32', 8, 'Rfe,Fre,Coe,PodWW,PodWR,DpAddrdR,DpAddrdW,DpDatadW,DpCtrldR,DpCtrldW,Fence.r.rwdRR,Fence.rw.wdRW,Fence.w.wdWW,Fence.w.rdWR', None, None),
    ('amo_ar32', 6, 'Rfe,Fre,Coe,PodRR,PodRW,PodWR,PodWW', None, None),
    ('amo_ar64', 6, 'Rfe,Fre,Coe,PodRR,PodRW,PodWR,PodWW', None, 'uint64_t'),
    ('lrsc_aqrl32', 4, 'Rfe,Fre,Coe,PodRR,PodRW,PodWR,PodWW', None, None),
    ('lrsc_aqrl64', 4, 'Rfe,Fre,Coe,PodRR,PodRW,PodWR,PodWW', None, 'uint64_t'),
]


def package_runtime(tools, out, manifest):
    version = subprocess.check_output([tools / 'herd7', '-version'], text=True).strip()
    commit = '37caee17e651ac2a46257973494e9037f219aa4f'
    if version != '7.56, Rev: ' + commit:
        raise RuntimeError('wrong official herdtools version/commit: ' + version)
    manifest.pop('herdtoolsBuild', None)
    manifest['herdtools'] = {'version': '7.56', 'commit': commit,
                            'source': 'https://github.com/herd/herdtools7/tree/7.56',
                            'tools': {tool: hashlib.sha256((tools / tool).read_bytes()).hexdigest()
                                      for tool in ('diy7', 'herd7', 'litmus7')}}
    feature_counts, opcode_counts = Counter(), Counter()
    for item in manifest['tests']:
        source = Path(item['source']).read_text()
        features = []
        for feature, pattern in (
            ('addressDependency', r'\bDpAddr'), ('dataDependency', r'\bDpData'),
            ('controlDependency', r'\bDpCtrl'), ('fence', r'\bfence '),
            ('amo', r'\bamo(?:or|swap)\.'), ('lrsc', r'\blr\.[wd]'),
            ('access64', r'\b(?:ld|sd|amo\w+\.d|lr\.d|sc\.d)\b')):
            if re.search(pattern, source):
                features.append(feature)
                feature_counts[feature] += 1
        item['sourceFeatures'] = features
        instructions = source.split('\n P0', 1)[1].split('\nexists', 1)[0].splitlines()[1:]
        for line in instructions:
            for cell in line.rstrip(' ;').split('|'):
                parts = cell.strip().split()
                if parts and not parts[0].endswith(':'):
                    opcode_counts[parts[0]] += 1
    manifest['sourceFeatureTestCounts'] = dict(feature_counts)
    manifest['generatedSourceInstructionCounts'] = dict(sorted(opcode_counts.items()))
    manifest['expectedTotalOutcomes'] = sum(max(1, 4 // item['threads']) for item in manifest['tests']) * manifest['runtime']['samplesPerInstance'] * manifest['runtime']['runs']
    manifest['unsupportedOrUncovered'] = ['All longer/unbounded cycles and all possible interleavings',
                                        'Mixed-size atomicity and architecture extensions outside the fixed model',
                                        'AMO opcodes beyond the generated amoor/amoswap idioms (pthread suite separately tests amoadd)']
    if manifest['profile'] == 'large':
        required = {'addressDependency', 'dataDependency', 'controlDependency', 'fence', 'amo', 'lrsc'}
        if not required <= feature_counts.keys():
            raise RuntimeError('large profile missing required generated instruction/dependency coverage')
    atomic_objects = []
    for item in manifest['tests']:
        if not item['family'].startswith(('amo_', 'lrsc_')):
            continue
        path = out / 'generated' / (item['name'] + '.o')
        assembly = subprocess.check_output(['riscv64-linux-gnu-objdump', '-d', path], text=True)
        source = Path(item['source']).read_text()
        mnemonics = sorted(set(re.findall(r'\b(?:amo(?:or|swap)\.[wd]\.aq\.rl|lr\.[wd]\.aq|sc\.[wd]\.rl)\b', source)))
        if not mnemonics or any(mnemonic.replace('.aq.rl', '.aqrl') not in assembly for mnemonic in mnemonics):
            raise RuntimeError(item['name'] + ': actual object assembly lost the requested atomic ordering bits')
        atomic_objects.append({'test': item['name'], 'objectSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                               'verifiedAssemblyMnemonics': [mnemonic.replace('.aq.rl', '.aqrl') for mnemonic in mnemonics]})
    (out / 'atomic-assembly-audit.json').write_text(json.dumps({'objectCount': len(atomic_objects), 'objects': atomic_objects,
        'scope': 'Actual generated test objects contain their required AMO aqrl or LR.aq/SC.rl mnemonics; no guest execution claim.'}, indent=2) + '\n')
    manifest['atomicAssemblyAudit'] = 'atomic-assembly-audit.json'
    digest = manifest['binarySha256']
    samples, runs = manifest['runtime']['samplesPerInstance'], manifest['runtime']['runs']
    profile = manifest['profile']
    wrapper = f'''#!/bin/sh
# The binary is official litmus7 output. This wrapper only establishes evidence.
set -u
binary="${{1:-$(dirname "$0")/generated/run.exe}}"
actual=$(sha256sum "$binary") || exit 2
actual=${{actual%% *}}
if [ "$actual" != "{digest}" ]; then
    echo 'LINUX_LITMUS_ABORT binary_hash_mismatch'
    exit 2
fi
online=$(grep -c '^cpu[0-9]' /proc/stat)
printf 'LINUX_LITMUS_BEGIN {{"profile":"{profile}","binarySha256":"%s","onlineCpus":%s}}\\n' "$actual" "$online"
timeout "${{CHI_LITMUS_TIMEOUT:-1800}}" "$binary" -v -a 4 -s {samples} -r {runs}
code=$?
printf 'LINUX_LITMUS_END {{"exitCode":%s}}\\n' "$code"
exit "$code"
'''
    (out / 'run-guest.sh').write_text(wrapper)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def run(command, logfile, cwd=None):
    with logfile.open('w', encoding='utf-8') as output:
        subprocess.run([str(item) for item in command], cwd=cwd, stdout=output, stderr=subprocess.STDOUT, check=True)


def normalize_gnu_amo(out, generated):
    original = out / 'original-generated-c'
    original.mkdir(exist_ok=True)
    records = []
    for path in generated.glob('chi_*.c'):
        content = path.read_text()
        fixed, count = re.subn(r'\b(amo(?:or|swap)\.[wd])\.aq\.rl\b', r'\1.aqrl', content)
        if count:
            (original / path.name).write_text(content)
            path.write_text(fixed)
            records.append({'file': path.name, 'occurrences': count,
                            'originalSha256': hashlib.sha256(content.encode()).hexdigest(),
                            'compiledSha256': hashlib.sha256(fixed.encode()).hexdigest()})
    report = {'reason': 'Pinned litmus7 prints .aq.rl; GNU assembler spells the identical aq=1/rl=1 AMO as .aqrl.',
              'replacement': 'Only amoor/amoswap.[wd].aq.rl -> amoor/amoswap.[wd].aqrl', 'files': records}
    target = out / 'gnu-assembly-adaptation.json'
    if records or not target.exists():
        target.write_text(json.dumps(report, indent=2) + '\n')


def generate(tools, out, profile='full', cycle_size=8, samples=128, runs=1, resume=False):
    out.mkdir(parents=True, exist_ok=True)
    inventory, generations = [], []
    diy_dir = out / 'diy'
    model_dir = out / 'model-results'
    model_dir.mkdir(exist_ok=True)
    prepared = out / 'compile-inputs'
    prepared.mkdir(exist_ok=True)
    families = FAMILIES[:4] if profile == 'smoke' else FAMILIES + (LARGE_FAMILIES if profile == 'large' else [])
    for family, family_size, safe, relaxed, datatype in families:
        size = 6 if profile == 'smoke' else family_size if family in {item[0] for item in LARGE_FAMILIES} else cycle_size
        target = diy_dir / family
        target.mkdir(parents=True, exist_ok=True)
        command = [tools / 'diy7', '-arch', 'RISCV', '-nprocs', '4', '-size', str(size),
                   '-name', 'chi_' + family, '-safe', safe]
        if relaxed:
            command += ['-relax', relaxed]
        if datatype:
            command += ['-type', datatype]
        if family.startswith('coherence'):
            # sc mode discards the single-location cycles. The official uni
            # mode retains coherence cycles; -oneloc permits their emission.
            command += ['-mode', 'uni', '-oneloc']
        if family.startswith('amo_'):
            command += ['-variant', 'AsAmo', '-varatom', 'AR']
        if family.startswith('lrsc_'):
            command += ['-varatom', 'XAqRl', '-ua', '0']
        if resume:
            if json.loads((target / 'generator-command.json').read_text()) != [str(arg) for arg in command]:
                raise RuntimeError(f'{family}: resume requires exactly the saved generator command')
        else:
            run(command, target / 'generator.log', target)
            (target / 'generator-command.json').write_text(json.dumps([str(arg) for arg in command], indent=2) + '\n')
        index = target / '@all'
        paths = [target / line.strip() for line in index.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        if not paths:
            raise RuntimeError(f'official diy7 produced no tests for {family}; do not claim coverage')
        generations.append({'family': family, 'generator': 'diy7', 'testCount': len(paths),
                            'command': [str(arg) for arg in command], 'index': str(index)})
        for path in paths:
            source = path.read_text()
            header = re.search(r'^RISCV\s+(\S+)', source)
            if not header:
                raise RuntimeError(f'non-RISCV generator output: {path}')
            name = header[1]
            processor_line = next((line for line in source.splitlines() if re.match(r'\s*P0\s*(?:\||;)', line)), '')
            threads = len(re.findall(r'\bP\d+\b', processor_line))
            if not 1 <= threads <= 4:
                raise RuntimeError(f'{name}: unsupported/missing generated thread count {threads}')
            log = model_dir / (name + '.log')
            if not resume:
                run([tools / 'herd7', '-model', 'riscv.cat', path], log)
            output = log.read_text()
            match = re.search(rf'^Observation {re.escape(name)} (Never|Sometimes|Always) (\d+) (\d+)$', output, re.M)
            if not match:
                raise RuntimeError(f'{name}: no model observation; inspect {log}')
            model_hash = re.search(r'^Hash=([0-9a-f]+)$', output, re.M)
            if not model_hash:
                raise RuntimeError(f'{name}: no official test Hash')
            # diy7 7.56 emits Co but litmus7 7.56's optional topology parser
            # expects Ws. Remove that hint in a separate copy; keep every
            # instruction and condition byte-identical. Forced incr1 affinity
            # remains configured independently. Verify the semantic hash again.
            compile_source = prepared / path.name
            prepared_text = re.sub(r'^Com=.*\n', '', source, flags=re.M)
            if resume:
                if compile_source.read_text() != prepared_text:
                    raise RuntimeError(f'{name}: raw/prepared input changed since the saved oracle run')
            else:
                compile_source.write_text(prepared_text)
            prepared_log = model_dir / (name + '.compile-input.log')
            if not resume:
                run([tools / 'herd7', '-model', 'riscv.cat', compile_source], prepared_log)
            if f'Hash={model_hash[1]}' not in prepared_log.read_text():
                raise RuntimeError(f'{name}: metadata compatibility copy changed semantic Hash')
            state_block = re.search(r'^States (\d+)\n(.*?)^(?:Ok|No)\n', output, re.M | re.S)
            if not state_block:
                raise RuntimeError(f'{name}: missing full model state set')
            model_states = [line for line in state_block[2].splitlines() if line.strip()]
            if len(model_states) != int(state_block[1]):
                raise RuntimeError(f'{name}: model state inventory truncated')
            inventory.append({'name': name, 'family': family, 'threads': threads,
                              'source': str(path), 'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                              'compileSource': str(compile_source), 'testHash': model_hash[1],
                              'compileSourceSha256': hashlib.sha256(compile_source.read_bytes()).hexdigest(),
                              'modelAllowedStates': model_states,
                              'model': 'herdtools7 7.56 / riscv.cat', 'modelLog': str(log),
                              'modelObservation': match[1], 'modelPositiveStates': int(match[2]),
                              'modelNegativeStates': int(match[3]), 'conditionAllowed': int(match[2]) > 0})
    if len({item['name'] for item in inventory}) != len(inventory):
        raise RuntimeError('generated test names are not unique')
    unique_count = len({item['testHash'] for item in inventory})
    if profile == 'large' and unique_count < 1000:
        raise RuntimeError(f'large profile requires at least 1000 unique semantic Hash values, got {unique_count}')
    all_tests = out / '@all'
    all_tests.write_text(''.join(item['compileSource'] + '\n' for item in inventory))
    config = out / 'riscv-linux.cfg'
    config.write_text('carch = RISCV\navail = 4\nprocs = 0,1,2,3\nlimit = true\naffinity = incr1\n'
                      'force_affinity = true\nmemory = direct\nbarrier = userfence\nstride = 1\n'
                      f'size_of_test = {samples}\nnumber_of_run = {runs}\nos = linux\ndriver = C\n'
                      'gcc = riscv64-linux-gnu-gcc\nccopts = -O2 -march=rv64gc -mabi=lp64d\n'
                      'linkopt = -static\nsmt_mode = seq\nsmt = 1\n')
    generated = out / 'generated'
    generated.mkdir(exist_ok=True)
    if not resume:
        run([tools / 'litmus7', '-mach', config, '-o', generated, all_tests], out / 'litmus-generation.log')
    generation_log = (out / 'litmus-generation.log').read_text()
    if re.search(r'exception|Assertion failed|Fatal error|Warning', generation_log, re.I):
        raise RuntimeError('litmus7 reported a generation diagnostic; inspect litmus-generation.log')
    compiled_sources = (generated / 'src').read_text().splitlines()
    if set(compiled_sources) != {item['name'] + '.c' for item in inventory} or len(compiled_sources) != len(inventory):
        raise RuntimeError('litmus7 silently omitted or duplicated generated tests')
    normalize_gnu_amo(out, generated)
    compile_log = out / ('cross-compile-resumed.log' if resume else 'cross-compile.log')
    run(['make', *([] if resume else ['-B']), '-j2'], compile_log, generated)
    binary = generated / 'run.exe'
    if not binary.is_file():
        raise RuntimeError('litmus7 generated executable missing')
    assembly = subprocess.check_output(['riscv64-linux-gnu-objdump', '-d', binary], text=True)
    (out / 'run.asm').write_text(assembly)
    if 'elf64-littleriscv' not in assembly:
        raise RuntimeError('generated binary is not RV64')
    model = tools.parent / 'share/herdtools7/herd/riscv.cat'
    manifest = {'schemaVersion': 1, 'suite': 'official-diy7-riscv-linux', 'profile': profile,
                'herdtoolsBuild': 'herdtools-build.json', 'modelSha256': hashlib.sha256(model.read_bytes()).hexdigest(),
                'generations': generations, 'testCount': len(inventory), 'tests': inventory,
                'uniqueSemanticTestCount': unique_count,
                'generatorRandomness': {'mode': 'deterministic exhaustive enumeration', 'seed': None,
                                        'note': 'diy7 cycle enumeration uses no random seed; full commands and fixed commit are retained.'},
                'excludedTests': [], 'guestBinary': str(binary),
                'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'runtime': {'availableCpus': 4, 'affinityCpus': [0, 1, 2, 3], 'forcedAffinity': True,
                            'samplesPerInstance': samples, 'runs': runs,
                            'instances': 'max(1, floor(4 / test.threads))'},
                'guestCommand': f'./run.exe -a 4 -s {samples} -r {runs}', 'guestExecution': 'NOT_RUN',
                'compileInputAdaptation': 'Remove optional Com= topology hint only; retain raw source and verify herd7 Hash unchanged.',
                'gnuAssemblyAdaptation': 'gnu-assembly-adaptation.json',
                'scope': 'Complete generated bounded matrix; not all possible RISC-V litmus tests. '
                         'No guest result or memory-model correctness claimed by building.'}
    package_runtime(tools, out, manifest)
    print(json.dumps({'testCount': len(inventory), 'families': generations,
                      'binary': str(binary), 'guestExecution': 'NOT_RUN'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=['smoke', 'full', 'large'], default='full')
    parser.add_argument('--cycle-size', type=int, choices=range(4, 11), default=8)
    parser.add_argument('--samples', type=int, default=128)
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--package-existing', action='store_true', help='Only add wrapper/provenance to an already built manifest')
    parser.add_argument('--resume-compile', action='store_true', help='Reuse saved, matching generator inputs and model logs after compiler failure')
    args = parser.parse_args()
    if args.samples < 1 or args.runs < 1:
        parser.error('samples and runs must be positive')
    if args.package_existing:
        package_runtime(args.tools.resolve(), args.output.resolve(), json.loads((args.output / 'manifest.json').read_text()))
    else:
        generate(args.tools.resolve(), args.output.resolve(), args.profile, args.cycle_size, args.samples, args.runs, args.resume_compile)

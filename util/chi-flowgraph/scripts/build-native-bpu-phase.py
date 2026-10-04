"""Build four actual KMH workloads spanning multiple BPU statistics phases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
MASK = (1 << 64) - 1


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reference(hart, iterations):
    state, checksum = 0x9e3779b97f4a7c15 ^ hart, 0
    for n in range(iterations):
        state = (state ^ (state << 13)) & MASK
        state ^= state >> 7
        state = (state ^ (state << 17)) & MASK
        if state & 1:
            checksum = (checksum + (state ^ n)) & MASK
        else:
            checksum ^= (state + n) & MASK
    return state, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'build/native-bpu-phase-program')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise ValueError('refusing an existing program output directory')
    wrapper = PROJECT / 'native/gem5/native-bpu-phase-test.c'
    rtl = PROJECT / 'native/gem5/native-l2-rtl-test.c'
    base_path = PROJECT / 'build/native-rtl-program/manifest.json'
    base = json.loads(base_path.read_text())
    if sha(rtl) != base['sourceSha256']:
        raise ValueError('native RTL source no longer matches its prepared manifest')
    out.mkdir(parents=True)
    sources = out / 'sources'
    sources.mkdir()
    for source in (wrapper, rtl):
        (sources / source.name).write_bytes(source.read_bytes())
    cc = os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc')
    objdump = os.environ.get('CHI_RISCV_OBJDUMP', 'riscv64-linux-gnu-objdump')
    toolchain = subprocess.check_output([cc, '--version'], text=True).splitlines()[0]
    flags = base['flags']
    binaries, checks = [], []
    for hart in range(4):
        iterations = 24013 + hart * 113
        state, checksum = reference(hart, iterations)
        defines = [f'-DHART={hart}', f'-DPHASE_ITERATIONS={iterations}',
                   f'-DPHASE_EXPECT_STATE=0x{state:016x}ULL',
                   f'-DPHASE_EXPECT_CHECKSUM=0x{checksum:016x}ULL']
        binary = out / f'native-l2-{hart}.elf'
        command = [cc, *flags, *defines, str(sources / wrapper.name), '-o', str(binary)]
        subprocess.run(command, check=True)
        assembly = subprocess.check_output([objdump, '-d', str(binary)], text=True)
        (out / f'native-l2-{hart}.asm').write_text(assembly)
        if re.search(r'^\s*[0-9a-f]+:\s+[0-9a-f]+\s+[^\n]*\bgp\b', assembly, re.M):
            raise ValueError('uninitialized gp dependence')
        block = re.search(r'<phase_exercise[^>]*>:\n(.*?)(?=\n\n)', assembly, re.S)
        nop_count = len(re.findall(r'\bnop\s*$', block[1], re.M)) if block else 0
        if nop_count < 12:
            raise ValueError('missing actual twelve-instruction loop padding')
        binaries.append({'hart': hart, 'file': binary.name, 'sha256': sha(binary)})
        checks.append({'hart': hart, 'iterations': iterations, 'loopPaddingInstructions': 12,
                       'minimumPaddingInstructionsExecutedOnPass': iterations * 12,
                       'expectedState': str(state), 'expectedChecksum': str(checksum),
                       'gpIndependent': True, 'command': command})
    manifest = dict(base)
    manifest.update(title='KMH 四核 BPU 阶段存储等价回归',
                    description='四核多次跨越阶段边界，然后执行原生 CHI CPU 请求场景；仅用于宿主统计存储回归。',
                    source='native/gem5/native-bpu-phase-test.c', sourceSha256=sha(wrapper),
                    includedRtlSourceSha256=sha(rtl), toolchain=toolchain, binaries=binaries,
                    cboSupport='Same actual SE CBO workload; Linux permission/translation tests remain separate.',
                    bpuPhaseWorkload={'minimumCompletedMainPhasesPerHart': 2, 'harts': checks,
                                      'partialFinalPhaseMustBeObserved': True,
                                      'scope': 'Host statistics storage regression, not Linux workload acceptance.'})
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (out / 'disassembly-check.json').write_text(json.dumps({'passed': True, 'guestExecuted': False,
                                                          'harts': checks}, indent=2) + '\n')
    print(json.dumps({'prepared': True, 'guestExecuted': False, 'output': str(out),
                      'manifestSha256': sha(out / 'manifest.json')}))


if __name__ == '__main__':
    main()

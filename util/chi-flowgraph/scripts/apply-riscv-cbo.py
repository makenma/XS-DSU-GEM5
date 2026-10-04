"""Install Zicbom CPU, xenvcfg and translation support on the pinned KMH gem5.

Each output must equal the pinned source, our previous installed output, or
the requested output. Independent edits are never silently overwritten.
"""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5', type=Path, required=True)
parser.add_argument('--check', action='store_true', help='Validate all inputs without changing the checkout')
args = parser.parse_args()
project = Path(__file__).resolve().parent.parent
gem5 = args.gem5.resolve()
commit = '65f3241a6cd486dffddaca251536f5c691af9a1f'
assert subprocess.check_output(['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip() == commit

def baseline(path):
    return subprocess.check_output(['git', '-C', str(gem5), 'show', f'{commit}:{path}'], text=True)

decoder_path = 'src/arch/riscv/isa/decoder.isa'
format_path = 'src/arch/riscv/isa/formats/mem.isa'
decoder = baseline(decoder_path)
old = '''        0x03: decode FUNCT3 {
            format FenceOp {'''
new = '''        0x03: decode FUNCT3 {
            // Zicbom 1.0 encodings: MISC-MEM, funct3=2, rd=0,
            // imm12=0/1/2 for inval/clean/flush. xenvcfg gates FS execution.
            0x2: decode RD {
                0x0: decode IMM12 {
                    0x0: CboOp::cbo_inval({{Request::INVALIDATE}});
                    0x1: CboOp::cbo_clean({{Request::CLEAN}});
                    0x2: CboOp::cbo_flush({{Request::CLEAN | Request::INVALIDATE}});
                }
            }
            format FenceOp {'''
assert decoder.count(old) == 1
pending = {decoder_path: decoder.replace(old, new),
           format_path: baseline(format_path) + '\n' +
               (project / 'native/gem5/riscv-cbo-format.isa').read_text(encoding='utf-8')}
sys.path.insert(0, str(project / 'native/gem5'))
from riscv_cbo_patch import patches
pending.update(patches(baseline))
composed_inputs = {}
def compose_observer(path, old, new):
    # apply-extensions owns the instruction observer changes in these files.
    # Compose with its checked installed snapshot, never arbitrary source edits.
    installed = project / 'build/extended-installed' / path
    if not installed.exists():
        raise RuntimeError(f'Install the existing observer before CBO support: {path}')
    source = installed.read_text()
    if new in source:
        source = source.replace(new, old)  # permit repeated standalone installs
    assert source.count(old) == 1, (path, old[:80])
    composed_inputs[path] = source
    pending[path] = source.replace(old, new)

compose_observer('src/cpu/o3/lsq.cc',
'''    if (!isLoad && !isAtomic) {
        // store inst temporally saves its data in memData''',
'''    // Cache maintenance and block zero have no input store data. In
    // particular, CBO writeMem(nullptr, ...) must not be copied before the
    // existing STORE_NO_DATA handling in LSQUnit::write.
    if (!isLoad && !isAtomic && !(flags & Request::STORE_NO_DATA)) {
        // store inst temporally saves its data in memData''')
compose_observer('src/cpu/o3/lsq_unit.cc',
'''        cpu->goldenMemManager() &&
        cpu->goldenMemManager()->inPmem(request->mainReq()->getPaddr())) {''',
'''        cpu->goldenMemManager() &&
        !request->mainReq()->isCacheMaintenance() &&
        cpu->goldenMemManager()->inPmem(request->mainReq()->getPaddr())) {''')
for path, content in pending.items():
    previous = project / 'build/riscv-cbo-installed' / path
    accepted = {content}
    if path != 'src/arch/riscv/cbo.hh':
        accepted.add(baseline(path))
    if path in composed_inputs:
        accepted.add(composed_inputs[path])
    if previous.exists():
        accepted.add(previous.read_text())
    if (gem5 / path).exists() and (gem5 / path).read_text() not in accepted:
        raise RuntimeError(f'Refusing to overwrite independent edits: {gem5 / path}')
if args.check:
    print(f'Validated Zicbom install inputs ({len(pending)} files); checkout unchanged.')
    raise SystemExit(0)
for path, content in pending.items():
    target = gem5 / path
    if not target.exists() or target.read_text() != content:
        target.write_text(content)
    previous = project / 'build/riscv-cbo-installed' / path
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text(content)
    if path in composed_inputs:
        # The observer installer must recognize the composed version before
        # reapplying its own layer on the next complete installation.
        (project / 'build/extended-installed' / path).write_text(content)
print('Installed RISC-V Zicbom CPU instructions, xenvcfg and CBO translation rules (no CHI opcode injection).')

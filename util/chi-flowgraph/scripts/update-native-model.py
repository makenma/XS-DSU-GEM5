"""Update owned native endpoints without reinstalling unchanged observer layers.

Only the previously installed project version or the requested version is
accepted. This preserves source mtimes and refuses unrelated local edits.
"""
import argparse
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--gem5', type=Path, required=True)
p.add_argument('--check', action='store_true')
args = p.parse_args()
expected = '65f3241a6cd486dffddaca251536f5c691af9a1f'
assert subprocess.check_output(['git', '-C', str(args.gem5), 'rev-parse', 'HEAD'], text=True).strip() == expected
project = Path(__file__).resolve().parent.parent
prefix = Path('src/mem/cache/CHI')
pending = {}
for source in (project / 'native/gem5').glob('Chi*'):
    if source.suffix in {'.cc', '.hh', '.py'}:
        pending[prefix / source.name] = source.read_text()
pending[prefix / 'FlowGraphTrace.hh'] = (project / 'native/gem5/FlowGraphTrace.hh').read_text()
sconscript = prefix / 'SConscript'
registered = (args.gem5 / sconscript).read_text()
test = "GTest('chi_native_request.test', 'ChiNativeRequest.test.cc', '../../packet.cc', '../../../sim/bufval.cc', with_tag('gem5 trace'))"
if test not in registered:
    registered += '\n' + test + '\n'
pending[sconscript] = registered
for path, content in pending.items():
    target = args.gem5 / path
    accepted = {content}
    if path == prefix / 'FlowGraphTrace.hh':
        # Earlier observer installs copied this new file without a snapshot.
        # Accept precisely that known version, with only census additions removed.
        accepted.add(content.replace('#include "mem/cache/CHI/ChiTransferCensus.hh"\n', '')
            .replace('        auto& census=ChiTransferCensus::get();\n'
                     '        if(census.enabled())census.observe(ch,f,src,dst,accepted,tick());\n', ''))
    for previous in (project / 'build/extended-installed' / path,
                     project / 'build/gem5-installed' / path.relative_to(prefix)):
        if previous.exists():
            accepted.add(previous.read_text())
    if target.exists() and target.read_text() not in accepted:
        raise RuntimeError(f'Refusing to overwrite independent edits: {target}')
if args.check:
    print(f'Validated {len(pending)} native model files; checkout unchanged.')
else:
    changed = []
    for path, content in pending.items():
        target = args.gem5 / path
        if not target.exists() or target.read_text() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            changed.append(str(path))
        for previous in (project / 'build/extended-installed' / path,
                         project / 'build/gem5-installed' / path.relative_to(prefix)):
            if previous.exists() or 'extended-installed' in str(previous):
                previous.parent.mkdir(parents=True, exist_ok=True)
                previous.write_text(content)
    print(f'Updated {len(changed)} native model files: ' + ', '.join(changed))

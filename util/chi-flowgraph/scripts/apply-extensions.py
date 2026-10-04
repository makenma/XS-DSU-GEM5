"""Install the extended profile after the baseline observer patch.

Each existing file must equal the pinned baseline or the last installed
version. New files must equal this project's last installed version.
"""
import argparse
import pathlib
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--gem5', required=True, type=pathlib.Path)
args = parser.parse_args()
project = pathlib.Path(__file__).resolve().parent.parent
gem5 = args.gem5.resolve()
commit = '65f3241a6cd486dffddaca251536f5c691af9a1f'
assert subprocess.check_output(['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip() == commit
sys.path.insert(0, str(project / 'native/gem5'))
from extended_patch import patches

def baseline(path):
    return subprocess.check_output(['git', '-C', str(gem5), 'show', f'{commit}:{path}'], text=True, stderr=subprocess.DEVNULL)

pending = patches(gem5, baseline)
for src in (project / 'native/gem5').glob('Chi*'):
    if src.suffix in {'.cc', '.hh', '.py'}:
        pending['src/mem/cache/CHI/' + src.name] = src.read_text(encoding='utf-8')
for path, content in pending.items():
    destination = gem5 / path
    previous = project / 'build/extended-installed' / path
    if destination.exists():
        current = destination.read_text()
        accepted = {content}
        if previous.exists(): accepted.add(previous.read_text())
        if path.startswith('src/mem/cache/CHI/'):
            observed = project / 'build/gem5-installed' / path.removeprefix('src/mem/cache/CHI/')
            if observed.exists(): accepted.add(observed.read_text())
        try: accepted.add(baseline(path))
        except subprocess.CalledProcessError: pass
        if current not in accepted:
            raise RuntimeError(f'Refusing to overwrite independent edits: {destination}')
for path, content in pending.items():
    destination = gem5 / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text() != content:
        destination.write_text(content)
    previous = project / 'build/extended-installed' / path
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text(content)
    # Let the baseline installer recognize the complete installed version.
    if path.startswith('src/mem/cache/CHI/'):
        observed = project / 'build/gem5-installed' / path.removeprefix('src/mem/cache/CHI/')
        if observed.exists(): observed.write_text(content)
print(f'Installed extended CHI profile ({len(pending)} files).')

"""Compile the production HN recency index and compare with its previous scan.

Run in WSL: python3 scripts/check-home-recency.py. This standalone C++17 check
does not build or run gem5, and does not claim full simulation equivalence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = args.output or root / 'build/qa/home-recency-equivalence.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    binary = output.parent / 'home-recency-equivalence'
    source = root / 'tests/native/home_recency_equivalence.cc'
    header = root / 'native/gem5/ChiHomeRecency.hh'
    command = [*shlex.split(os.environ.get('CXX', 'g++')), '-std=c++17', '-O2',
               '-Wall', '-Wextra', '-Werror', '-pedantic', '-I', str(header.parent),
               str(source), '-o', str(binary)]
    subprocess.run(command, check=True)
    result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
    report = json.loads(result.stdout)
    report['sourceSha256'] = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (source, header, root / 'native/gem5/ChiExtendedHome.cc',
                     root / 'native/gem5/ChiExtendedHome.hh')}
    report['scope'] = ('Production recency index versus the previous address-ordered '
                       'scan under insertion, both lookup/response touches, erasure, '
                       'busy and all-busy candidates. End-to-end gem5 ticks/data '
                       'still require an independently rebuilt simulation comparison.')
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

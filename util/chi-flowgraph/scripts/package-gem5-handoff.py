"""Copy the source workspace into an existing gem5 checkout for server handoff.

Builds, local machine configuration, the supplied protocol PDF, vendor checkouts
and large generated stress traces are intentionally outside the source package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5', type=Path, required=True)
parser.add_argument('--copy', action='store_true')
parser.add_argument('--verify', action='store_true')
args = parser.parse_args()
project = Path(__file__).resolve().parents[1]
gem5 = args.gem5.resolve(strict=True)
assert (gem5 / 'SConstruct').is_file() and (gem5 / '.git').exists()
destination = gem5 / 'util/chi-flowgraph'
assert destination.resolve().is_relative_to(gem5) and not destination.is_symlink()
if args.verify:
    assert not args.copy, '--verify and --copy are mutually exclusive'
    manifest = json.loads((destination / 'SOURCE_PACKAGE.json').read_text())
    expected = {row['path']: row for row in manifest['files']}
    actual = {path.relative_to(destination).as_posix() for path in destination.rglob('*')
              if path.is_file() and '__pycache__' not in path.parts
              and path.name != 'SOURCE_PACKAGE.json'}
    assert actual == set(expected), {'extra': sorted(actual - set(expected)),
                                     'missing': sorted(set(expected) - actual)}
    for name, row in expected.items():
        path = destination / name
        assert not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == row['packagedSha256'], name
    print(json.dumps({'verified': True, 'files': len(actual), 'destination': str(destination)}))
    raise SystemExit(0)
roots = ['native', 'scripts', 'config', 'docs', 'src', 'tests', 'public', 'examples', 'validation']
top_files = ['.gitignore', 'README.md', 'plan.md', 'package.json', 'pnpm-lock.yaml',
             'pnpm-workspace.yaml', 'index.html', 'tsconfig.json', 'vite.config.ts', 'vitest.config.ts']
files = []
excluded = []
for root in roots:
    for path in sorted((project / root).rglob('*')):
        relative = path.relative_to(project)
        if not path.is_file():
            continue
        if path.is_symlink():
            raise RuntimeError('Refusing source symlink: ' + str(relative))
        if ('__pycache__' in relative.parts or path.suffix in ('.pyc', '.pdf', '.log')
                or relative.parts[:2] in (('public', 'benchmarks'), ('examples', 'native-l2'))):
            excluded.append(relative.as_posix())
            continue
        assert path.stat().st_size < 50 * 1024**2, 'Unexpected large source file: ' + str(relative)
        files.append(path)
files += [project / name for name in top_files if (project / name).is_file()]
records = []
for path in files:
    relative = path.relative_to(project)
    data = path.read_bytes()
    records.append({'path': relative.as_posix(), 'bytes': len(data),
                    'sourceSha256': hashlib.sha256(data).hexdigest()})
    if args.copy:
        target = destination / relative
        assert not target.is_symlink() and target.resolve().is_relative_to(destination.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755 if path.suffix == '.sh' else 0o644)
if args.copy:
    ignore = destination / '.gitignore'
    with ignore.open('a') as stream:
        stream.write('\n# The enclosing gem5 repository ignores all package.json names.\n!package.json\n')
        stream.write('examples/native-l2/\n')
    for row in records:
        row['packagedSha256'] = hashlib.sha256((destination / row['path']).read_bytes()).hexdigest()
    (destination / 'SOURCE_PACKAGE.json').write_text(json.dumps({
        'kind': 'source-package', 'baseGem5Commit': '65f3241a6cd486dffddaca251536f5c691af9a1f',
        'note': 'Source input hashes before Git line-ending normalization. Build outputs and host-specific QA remain local.',
        'files': records, 'excludedGeneratedFiles': excluded,
    }, indent=2) + '\n')
    entry = gem5 / 'CHI_NATIVE_L2_HANDOFF.md'
    title = '# CHI native L2 server handoff\n'
    assert not entry.exists() or entry.read_text().startswith(title), 'Refusing an unrelated existing handoff file'
    entry.write_text(title + '''
本分支基于 `65f3241a6cd486dffddaca251536f5c691af9a1f`，已直接合入原生 CHI L2、
端点轨迹、CBO、取指修复及 Linux 验证工具。gem5 保持 C++17。

**从这里开始：[服务器构建与运行说明](util/chi-flowgraph/docs/native-linux-server.md)。**

- gem5 实际实现位于 `src/`；工具、前端和测试位于 `util/chi-flowgraph/`。
- 直接编译本分支，不要再次执行历史 `apply-*` 补丁安装器。
- 默认验证拓扑为 4 KMH CPU、3×2 CHI mesh、4 HN-F、2 SN-F。
- [交付验证状态](util/chi-flowgraph/validation/README.md)保留实际通过与失败记录。
- 完整 Linux 长测尚未完成，服务器新编译/运行需要生成自己的证据。
- 镜像、二进制、完整运行日志、大型生成 trace 和协议手册 PDF 不在 Git 中。
''')
print(json.dumps({'destination': str(destination), 'copied': args.copy,
                  'files': len(records), 'bytes': sum(row['bytes'] for row in records),
                  'excludedFiles': len(excluded),
                  'largest': sorted(records, key=lambda row: row['bytes'], reverse=True)[:5]}, indent=2))

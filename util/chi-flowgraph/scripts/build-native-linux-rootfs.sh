#!/usr/bin/env bash
# Reuse the completed core-suite kernel objects; preserve its Image/config first.
# This builds host cross-compiled boot firmware, not the later guest make test.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
kernel="$work_dir/sources/linux-6.6.52"
kernel_out="$work_dir/kernel-build"
rootfs="$work_dir/native-rootfs-kernel-only-final"
opensbi_out="$work_dir/opensbi-rootfs"
log_dir="$project_dir/build/linux-validation/rootfs-build"
jobs="${CHI_BUILD_JOBS:-2}"
case "$jobs" in ''|*[!0-9]*) echo 'Invalid CHI_BUILD_JOBS' >&2; exit 2;; esac
[ "$jobs" -ge 1 ] && [ "$jobs" -le 2 ]
[ ! -e "$log_dir/build-started.json" ] || {
    echo 'Existing rootfs build evidence found; preserve it before a new build.' >&2; exit 2;
}
mkdir -p "$log_dir"
python3 - "$project_dir" "$work_dir" "$log_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import shutil
import sys

p, w, out = map(Path, sys.argv[1:])
def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()

prepared = json.loads((p / 'build/linux-validation/rootfs-kernel-only-preparation.json').read_text())
assert prepared['preparedIntegrityVerified'] is True
assert Path(prepared['finalInitramfsList']) == w / 'native-rootfs-kernel-only-final/initramfs.list'
for item in prepared['files'] + prepared['initramfsSources']:
    source = Path(item.get('path', item.get('source')))
    assert source.is_file() and sha(source) == item['sha256'], f'prepared input changed: {source}'

suite = p / 'build/linux-validation/suite-build'
known = {Path(source): digest for digest, source in
         (line.split(None, 1) for line in (suite / 'sha256.txt').read_text().splitlines())}
image = w / 'kernel-build/arch/riscv/boot/Image'
firmware = w / 'opensbi-suite/platform/generic/firmware/fw_payload.elf'
for source in (image, firmware):
    assert source in known and sha(source) == known[source], f'core suite artifact changed: {source}'
assert (w / 'kernel-build/.config').read_bytes() == (suite / '.config').read_bytes()
snapshot = w / 'artifacts/core-suite-before-rootfs' / known[image]
snapshot.mkdir(parents=True, exist_ok=False)
files = []
for source, name in ((image, 'Image'), (w / 'kernel-build/.config', 'kernel.config'),
                     (w / 'suite-initramfs.list', 'initramfs.list')):
    target = snapshot / name
    shutil.copyfile(source, target)
    assert sha(source) == sha(target)
    target.chmod(0o444)
    files.append({'path': str(target), 'sha256': sha(target), 'bytes': target.stat().st_size})
record = {'schemaVersion': 1, 'coreSuiteSnapshot': str(snapshot), 'files': files,
          'coreSuiteFirmware': {'path': str(firmware), 'sha256': known[firmware]},
          'preparedRootfs': prepared['finalInitramfsList'],
          'guestExecuted': False, 'buildJobs': int(__import__('os').environ.get('CHI_BUILD_JOBS', '2'))}
(snapshot / 'snapshot.json').write_text(json.dumps(record, indent=2) + '\n')
(out / 'build-started.json').write_text(json.dumps(record, indent=2) + '\n')
print('Preserved core-suite Image/config: ' + str(snapshot), flush=True)
PY

for format in GZIP BZIP2 LZMA XZ LZO LZ4 ZSTD; do
    "$kernel/scripts/config" --file "$kernel_out/.config" --disable "INITRAMFS_COMPRESSION_$format"
done
"$kernel/scripts/config" --file "$kernel_out/.config" \
    --enable INITRAMFS_COMPRESSION_NONE \
    --set-str INITRAMFS_SOURCE "$rootfs/initramfs.list"
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- olddefconfig \
    > "$log_dir/config.log" 2>&1
python3 - "$kernel_out/.config" "$rootfs" <<'PY'
from pathlib import Path
import sys
config, rootfs = map(Path, sys.argv[1:])
lines = set(config.read_text().splitlines())
required = (rootfs / 'kernel-rootfs.config').read_text().splitlines()
required += ['CONFIG_SMP=y', 'CONFIG_NR_CPUS=4', 'CONFIG_NUMA=y',
             'CONFIG_RISCV_ISA_ZICBOM=y', f'CONFIG_INITRAMFS_SOURCE="{rootfs}/initramfs.list"']
for line in required:
    assert line in lines, f'required option missing: {line}'
for kind in ('GZIP', 'BZIP2', 'LZMA', 'XZ', 'LZO', 'LZ4', 'ZSTD'):
    assert f'CONFIG_INITRAMFS_COMPRESSION_{kind}=y' not in lines
print('Final rootfs kernel config verified; outer initramfs compression NONE.', flush=True)
PY
cp "$kernel_out/.config" "$log_dir/kernel.config"
printf 'Building Linux Image with %s jobs; log: %s\n' "$jobs" "$log_dir/kernel-build.log"
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- \
    -j"$jobs" Image > "$log_dir/kernel-build.log" 2>&1
printf 'Building independent OpenSBI rootfs payload; log: %s\n' "$log_dir/opensbi-build.log"
make -C "$work_dir/sources/opensbi-1.3.1" O="$opensbi_out" PLATFORM=generic \
    CROSS_COMPILE=riscv64-linux-gnu- FW_PAYLOAD=y \
    FW_PAYLOAD_PATH="$kernel_out/arch/riscv/boot/Image" FW_PAYLOAD_OFFSET=0x200000 \
    -j"$jobs" > "$log_dir/opensbi-build.log" 2>&1
python3 "$project_dir/scripts/check-native-linux-rootfs-build.py" \
    --work "$work_dir" --log-dir "$log_dir"
printf 'Rootfs boot payload ready: %s\n' "$opensbi_out/platform/generic/firmware/fw_payload.elf"

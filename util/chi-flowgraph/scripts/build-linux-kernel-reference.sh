#!/usr/bin/env bash
# Independent QEMU user-mode reference only. This never runs m5ops or gem5,
# and cannot satisfy the real gem5 guest's kernel-build acceptance gate.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
reference="$work_dir/qemu-kernel-reference"
prepared="$work_dir/native-rootfs-kernel-only-final"
log_dir="$project_dir/build/linux-validation/kernel-reference"
root="$reference/root"
resume=0
case "${1:-}" in '' ) ;; --resume-prepared) resume=1;; *) echo 'Unknown argument' >&2; exit 2;; esac
if [ "$resume" -eq 0 ]; then
    [ ! -e "$reference" ] && [ ! -e "$log_dir/reference-start.json" ] || {
        echo 'Reference output exists; preserve this first reference instead of rebuilding.' >&2; exit 2;
    }
else
    test -s "$log_dir/reference-start.json"
    test -d "$root"
    [ ! -e "$root/build/kernel" ] || {
        echo 'A build already began; resume-prepared cannot rebuild it.' >&2; exit 2;
    }
fi
test -s "$prepared/rootfs.squashfs"
grep -qx enabled /proc/sys/fs/binfmt_misc/qemu-riscv64
for tool in python3 unsquashfs qemu-riscv64-static chroot sha256sum; do command -v "$tool" >/dev/null; done
mkdir -p "$reference" "$log_dir"
python3 - "$project_dir" "$prepared" "$reference" "$log_dir" "$$" "$resume" <<'PY'
import hashlib, json, os
from pathlib import Path
import sys, time
p, prepared, reference, out = map(Path, sys.argv[1:5])
report = json.loads((p / 'build/linux-validation/rootfs-kernel-only-preparation.json').read_text())
image = prepared / 'rootfs.squashfs'
expected = next(item['sha256'] for item in report['files'] if item['path'] == str(image))
h = hashlib.sha256()
with image.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
assert h.hexdigest() == expected, 'prepared SquashFS hash changed'
metadata = {'schemaVersion': 1, 'referenceOnly': True, 'execution': 'QEMU user-mode chroot',
            'gem5Executed': False, 'jobs': 2, 'guestJobs': 4, 'recipeDifference': 'make parallelism only: -j4 becomes -j2',
            'rootfs': str(image), 'rootfsSha256': expected, 'referenceRoot': str(reference / 'root'),
            'startedUnixSeconds': time.time(), 'launcherPid': int(sys.argv[5])}
record = out / 'reference-start.json'
if sys.argv[6] == '1':
    previous = json.loads(record.read_text())
    assert previous['rootfsSha256'] == expected and previous['referenceRoot'] == str(reference / 'root')
    previous.setdefault('preBuildResumes', []).append({'launcherPid': int(sys.argv[5]), 'unixSeconds': time.time()})
    metadata = previous
record.write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata), flush=True)
PY
qemu-riscv64-static --version > "$log_dir/qemu-version.txt"
cp /proc/sys/fs/binfmt_misc/qemu-riscv64 "$log_dir/binfmt.txt"
if [ "$resume" -eq 0 ]; then
    unsquashfs -processors 2 -no-progress -d "$root" "$prepared/rootfs.squashfs" > "$log_dir/extract.log" 2>&1
fi
python3 "$project_dir/scripts/linux-kernel-reference.py" prepare --root "$root" --log-dir "$log_dir"
# No host filesystem is mounted into the reference root. These character
# devices are sufficient for the native compiler and shell redirections.
for pair in null:3 zero:5 random:8 urandom:9; do
    name=${pair%:*}; minor=${pair#*:}
    test -e "$root/dev/$name" || mknod -m 666 "$root/dev/$name" c 1 "$minor"
done
printf 'Running QEMU reference only, -j2; launcher PID=%s, root=%s\n' "$$" "$root"
status=0
chroot "$root" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /bin/sh /opt/chi/kernel-reference.sh > "$log_dir/reference-run.log" 2>&1 || status=$?
printf '%s\n' "$status" > "$log_dir/exit-code.txt"
if [ "$status" -ne 0 ]; then
    echo "QEMU reference failed with status $status; preserved logs: $log_dir" >&2
    exit "$status"
fi
python3 "$project_dir/scripts/linux-kernel-reference.py" finish --root "$root" --log-dir "$log_dir"

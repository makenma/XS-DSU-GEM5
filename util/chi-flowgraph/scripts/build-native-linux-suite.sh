#!/usr/bin/env bash
# Build a single-boot, host-checked Linux test pipeline. Guest kernel build uses
# the larger SquashFS stage separately; this initramfs contains no host toolchain.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
kernel="$work_dir/sources/linux-6.6.52"
kernel_out="$work_dir/kernel-build"
jobs=${CHI_BUILD_JOBS:-3}
log_dir="$project_dir/build/linux-validation/suite-build"
mkdir -p "$log_dir"
for artifact in linux-litmus-smoke/generated/run.exe linux-litmus-large/generated/run.exe linux-tests/linux-suite; do
    test -s "$project_dir/build/$artifact"
done
test -s "$kernel_out/.config"
"$kernel/scripts/config" --file "$kernel_out/.config" --enable RISCV_ISA_ZICBOM \
    --enable BLK_DEV_LOOP --set-val BLK_DEV_LOOP_MIN_COUNT 8 \
    --enable SQUASHFS --enable SQUASHFS_LZ4 --enable SQUASHFS_FILE_DIRECT \
    --enable SQUASHFS_COMPILE_DECOMP_MULTI_PERCPU --enable OVERLAY_FS --enable TMPFS_XATTR
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- olddefconfig > "$log_dir/config.log" 2>&1
# Establish the matching kernel/module ABI before compiling the CBO test module.
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j"$jobs" Image modules > "$log_dir/abi-build.log" 2>&1
python3 "$project_dir/scripts/build-linux-cbo.py" --kernel-source "$kernel" --kernel-build "$kernel_out" > "$log_dir/cbo-build.log" 2>&1
python3 "$project_dir/scripts/build-linux-uncached.py" --kernel-source "$kernel" --kernel-build "$kernel_out" > "$log_dir/uncached-build.log" 2>&1
python3 "$project_dir/scripts/freeze-linux-suite.py" --output-root "$work_dir/artifacts/linux-suite-inputs" > "$log_dir/frozen-inputs.json"
riscv64-linux-gnu-gcc -O2 -static -march=rv64gc -mabi=lp64d \
    -I"$gem5_dir/include" "$project_dir/native/linux-platform/guest-control.c" \
    "$gem5_dir/util/m5/src/abi/riscv/m5op.S" -o "$work_dir/boot-rootfs/bin/guest-control"
python3 - "$project_dir" "$work_dir" "$log_dir/frozen-inputs.json" <<'PY'
from pathlib import Path
import json
import sys
p, w, frozen = map(Path, sys.argv[1:])
index = Path(json.loads(frozen.read_text())['index'])
stages = json.loads(index.read_text())['stages']
def artifact(stage, name):
    return index.parent / stages[stage][name]
k = w / 'kernel-build'
release = (k / 'include/config/kernel.release').read_text().strip()
lines = (w / 'boot-initramfs.list').read_text().splitlines()
lines = [f'file /init {p}/native/linux-platform/suite-init.sh 755 0 0' if line.startswith('file /init ') else line for line in lines]
existing = {line.split()[1] for line in lines}
for applet in 'cut sha256sum mkdir insmod rmmod dmesg'.split():
    lines.append(f'slink /bin/{applet} busybox 777 0 0')
files = {
    '/bin/litmus-smoke': artifact('litmus-smoke', 'binary'),
    '/bin/litmus-large': artifact('litmus-large', 'binary'),
    '/bin/linux-suite': artifact('pthreads', 'binary'),
    '/bin/linux-cbo': artifact('cbo', 'binary'),
    '/bin/linux-uncached': artifact('uncached', 'binary'),
    '/bin/run-kernel-torture.sh': p / 'native/linux-platform/run-kernel-torture.sh',
    f'/lib/modules/{release}/extra/chi_cbo_test.ko': artifact('cbo', 'module'),
    f'/lib/modules/{release}/extra/chi_uncached_test.ko': artifact('uncached', 'module'),
}
for mod in ('kernel/torture.ko', 'kernel/rcu/rcutorture.ko', 'kernel/locking/locktorture.ko'):
    files[f'/lib/modules/{release}/kernel/{mod}'] = k / mod
for dest, source in files.items():
    assert source.is_file(), source
    for parent in reversed(Path(dest).parents):
        if str(parent) != '/' and str(parent) not in existing:
            lines.append(f'dir {parent} 755 0 0')
            existing.add(str(parent))
    lines.append(f'file {dest} {source} 755 0 0')
targets = [line.split()[1] for line in lines]
assert len(targets) == len(set(targets)), 'duplicate initramfs target'
(w / 'suite-initramfs.list').write_text('\n'.join(lines) + '\n')
PY
"$kernel/scripts/config" --file "$kernel_out/.config" --set-str INITRAMFS_SOURCE "$work_dir/suite-initramfs.list"
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- olddefconfig >> "$log_dir/config.log" 2>&1
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j"$jobs" Image modules > "$log_dir/kernel-build.log" 2>&1
make -C "$work_dir/sources/opensbi-1.3.1" O="$work_dir/opensbi-suite" PLATFORM=generic \
    CROSS_COMPILE=riscv64-linux-gnu- FW_PAYLOAD=y FW_PAYLOAD_PATH="$kernel_out/arch/riscv/boot/Image" \
    FW_PAYLOAD_OFFSET=0x200000 -j"$jobs" > "$log_dir/opensbi-build.log" 2>&1
cp "$kernel_out/.config" "$work_dir/suite-initramfs.list" "$log_dir/"
sha256sum "$kernel_out/arch/riscv/boot/Image" \
    "$work_dir/opensbi-suite/platform/generic/firmware/fw_payload.elf" \
    "$work_dir/boot-rootfs/bin/guest-control" > "$log_dir/sha256.txt"
printf 'Linux suite payload ready: %s\n' "$work_dir/opensbi-suite/platform/generic/firmware/fw_payload.elf"

#!/usr/bin/env bash
# Reuse the configured native Linux kernel, replacing only the guest harness.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
kernel="$work_dir/sources/linux-6.6.52"
kernel_out="$work_dir/kernel-build"
stage=${1:?usage: build-native-linux-stage.sh litmus-smoke}
case "$stage" in
  litmus-smoke)
    init="$project_dir/native/linux-platform/litmus-init.sh"
    executable="$project_dir/build/linux-litmus-smoke/generated/run.exe"
    ;;
  *) printf 'Unknown stage: %s\n' "$stage" >&2; exit 2;;
esac
test -s "$executable"
test -s "$kernel_out/.config"
log_dir="$project_dir/build/linux-validation/$stage-build"
mkdir -p "$log_dir" "$work_dir/artifacts/boot-01"
# Preserve the existing image/config before changing the initramfs selection.
if [[ ! -e "$work_dir/artifacts/boot-01/fw_payload.elf" ]]; then
    cp "$work_dir/opensbi-build/platform/generic/firmware/fw_payload.elf" "$work_dir/artifacts/boot-01/"
    cp "$kernel_out/arch/riscv/boot/Image" "$kernel_out/.config" "$work_dir/artifacts/boot-01/"
fi
python3 - "$work_dir/boot-initramfs.list" "$work_dir/$stage-initramfs.list" "$init" "$executable" <<'PY'
import sys
from pathlib import Path
source, target, init, binary = sys.argv[1:]
lines=Path(source).read_text().splitlines()
assert sum(line.startswith('file /init ') for line in lines)==1
lines=[f'file /init {init} 755 0 0' if line.startswith('file /init ') else line for line in lines]
lines.append(f'file /bin/linux-litmus {binary} 755 0 0')
Path(target).write_text('\n'.join(lines)+'\n')
PY
"$kernel/scripts/config" --file "$kernel_out/.config" --set-str INITRAMFS_SOURCE "$work_dir/$stage-initramfs.list"
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- olddefconfig \
    > "$log_dir/kernel-config.log" 2>&1
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j"${CHI_BUILD_JOBS:-4}" Image modules \
    > "$log_dir/kernel-build.log" 2>&1
make -C "$work_dir/sources/opensbi-1.3.1" O="$work_dir/opensbi-$stage" PLATFORM=generic \
    CROSS_COMPILE=riscv64-linux-gnu- FW_PAYLOAD=y FW_PAYLOAD_PATH="$kernel_out/arch/riscv/boot/Image" \
    FW_PAYLOAD_OFFSET=0x200000 -j"${CHI_BUILD_JOBS:-4}" > "$log_dir/opensbi-build.log" 2>&1
cp "$kernel_out/.config" "$log_dir/kernel.config"
sha256sum "$kernel_out/arch/riscv/boot/Image" \
    "$work_dir/opensbi-$stage/platform/generic/firmware/fw_payload.elf" "$init" "$executable" \
    > "$log_dir/sha256.txt"
printf 'Guest stage payload ready: %s\n' "$work_dir/opensbi-$stage/platform/generic/firmware/fw_payload.elf"

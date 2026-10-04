#!/usr/bin/env bash
# Build the boot gate, plus real kernel torture modules for subsequent stages.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
jobs="${CHI_BUILD_JOBS:-6}"
kernel="$work_dir/sources/linux-6.6.52"
busybox="$work_dir/sources/busybox-1.36.1"
opensbi="$work_dir/sources/opensbi-1.3.1"
kernel_out="$work_dir/kernel-build"
busybox_out="$work_dir/busybox-build"
rootfs="$work_dir/boot-rootfs"
log_dir="$project_dir/build/linux-validation"
mkdir -p "$kernel_out" "$busybox_out" "$rootfs/bin" "$log_dir"
test -f "$kernel/Makefile"
test -f "$opensbi/Makefile"
make -C "$busybox" O="$busybox_out" defconfig > "$log_dir/busybox-config.log" 2>&1
sed -i 's/# CONFIG_STATIC is not set/CONFIG_STATIC=y/' "$busybox_out/.config"
# BusyBox 1.36's optional tc applet uses CBQ UAPI removed from the host's
# cross headers. Network traffic shaping is not part of the guest harness.
sed -i 's/^CONFIG_TC=y/# CONFIG_TC is not set/' "$busybox_out/.config"
make -C "$busybox" O="$busybox_out" CROSS_COMPILE=riscv64-linux-gnu- -j"$jobs" \
    > "$log_dir/busybox-build.log" 2>&1
cp "$busybox_out/busybox" "$rootfs/bin/busybox"
riscv64-linux-gnu-gcc -O2 -static -march=rv64gc -mabi=lp64d \
    -I"$gem5_dir/include" "$project_dir/native/linux-platform/guest-control.c" \
    "$gem5_dir/util/m5/src/abi/riscv/m5op.S" -o "$rootfs/bin/guest-control"
cat > "$work_dir/boot-initramfs.list" <<EOF
dir /dev 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/null 666 0 0 c 1 3
dir /proc 755 0 0
dir /sys 755 0 0
dir /tmp 1777 0 0
dir /run 755 0 0
dir /bin 755 0 0
dir /sbin 755 0 0
dir /usr 755 0 0
dir /usr/bin 755 0 0
dir /usr/sbin 755 0 0
file /bin/busybox $rootfs/bin/busybox 755 0 0
file /bin/guest-control $rootfs/bin/guest-control 755 0 0
file /init $project_dir/native/linux-platform/boot-init.sh 755 0 0
EOF
for applet in sh mount echo cat uname sleep sync; do
    printf 'slink /bin/%s busybox 777 0 0\n' "$applet" >> "$work_dir/boot-initramfs.list"
done
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- defconfig \
    > "$log_dir/kernel-config.log" 2>&1
cfg="$kernel/scripts/config --file $kernel_out/.config"
$cfg --enable SMP --set-val NR_CPUS 4 --enable NUMA --enable NUMA_BALANCING --enable MIGRATION \
    --enable MODULES --enable MODULE_UNLOAD --module RCU_TORTURE_TEST --module LOCK_TORTURE_TEST \
    --enable BLK_DEV_INITRD --enable DEVTMPFS --enable DEVTMPFS_MOUNT --enable TMPFS \
    --enable FUTEX --enable PROC_FS --enable SYSFS --enable MEMFD_CREATE \
    --enable IKCONFIG --enable IKCONFIG_PROC --disable DEBUG_INFO \
    --enable DEBUG_INFO_NONE --disable WERROR --disable RANDOMIZE_BASE \
    --disable RISCV_ISA_V --disable RISCV_ISA_ZBB --disable RISCV_ISA_ZICBOM --disable RISCV_ISA_ZICBOZ \
    --disable DRM --disable SND --disable USB_SUPPORT --disable WLAN --disable WIRELESS \
    --disable BT --disable MEDIA_SUPPORT --disable KVM --disable VIRTUALIZATION \
    --set-str INITRAMFS_SOURCE "$work_dir/boot-initramfs.list" --set-str LOCALVERSION "-chi-native"
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- olddefconfig \
    >> "$log_dir/kernel-config.log" 2>&1
python3 - "$kernel_out/.config" <<'PY'
from pathlib import Path
import sys
config = Path(sys.argv[1]).read_text().splitlines()
for key in ['CONFIG_SMP=y', 'CONFIG_NR_CPUS=4', 'CONFIG_NUMA=y', 'CONFIG_MIGRATION=y',
            'CONFIG_NUMA_BALANCING=y', 'CONFIG_MODULE_UNLOAD=y', 'CONFIG_TORTURE_TEST=m',
            'CONFIG_RCU_TORTURE_TEST=m', 'CONFIG_LOCK_TORTURE_TEST=m', 'CONFIG_FUTEX=y']:
    assert key in config, f'required kernel option missing: {key}'
PY
make -C "$kernel" O="$kernel_out" ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j"$jobs" Image modules \
    > "$log_dir/kernel-build.log" 2>&1
make -C "$opensbi" O="$work_dir/opensbi-build" PLATFORM=generic CROSS_COMPILE=riscv64-linux-gnu- \
    FW_PAYLOAD=y FW_PAYLOAD_PATH="$kernel_out/arch/riscv/boot/Image" FW_PAYLOAD_OFFSET=0x200000 \
    -j"$jobs" > "$log_dir/opensbi-build.log" 2>&1
cp "$kernel_out/.config" "$log_dir/kernel.config"
sha256sum "$kernel_out/arch/riscv/boot/Image" \
    "$work_dir/opensbi-build/platform/generic/firmware/fw_payload.elf" \
    "$rootfs/bin/busybox" "$rootfs/bin/guest-control" > "$log_dir/boot-sha256.txt"
printf 'Boot payload ready: %s\n' "$work_dir/opensbi-build/platform/generic/firmware/fw_payload.elf"

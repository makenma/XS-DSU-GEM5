#!/usr/bin/env bash
# Package existing inputs only: no QEMU/chroot execution, apt, kernel build,
# SCons, or gem5 launch. Run under WSL; output stays in its Linux filesystem.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
base_root="${CHI_LINUX_BASE_ROOTFS:-$work_dir/rootfs-build/noble-riscv64}"
kernel_source="$work_dir/sources/linux-6.6.52"
kernel_build="${CHI_LINUX_KERNEL_BUILD:-$work_dir/kernel-build}"
litmus_dir="${CHI_LINUX_LITMUS_DIR:-$project_dir/build/linux-litmus-full}"
output="${CHI_LINUX_ROOTFS_OUT:-$work_dir/native-rootfs}"
epoch="${SOURCE_DATE_EPOCH:-1704067200}"
jobs="${CHI_ROOTFS_JOBS:-2}"
mode="${CHI_ROOTFS_MODE:-kernel-build}"
case "$mode" in kernel-build|all) ;; *) echo 'Invalid CHI_ROOTFS_MODE' >&2; exit 2;; esac
for tool in rsync mksquashfs python3 sha256sum; do command -v "$tool" >/dev/null; done
case "$jobs" in ''|*[!0-9]*) echo 'Invalid CHI_ROOTFS_JOBS' >&2; exit 2;; esac
[ "$jobs" -ge 1 ] && [ "$jobs" -le 16 ]
for input in "$base_root/usr/bin/gcc" "$base_root/usr/bin/make" \
    "$base_root/usr/bin/bc" "$base_root/usr/bin/flex" "$base_root/usr/bin/bison" \
    "$base_root/usr/include/libelf.h" "$base_root/usr/include/openssl/ssl.h" \
    "$kernel_source/Makefile" "$kernel_build/.config" \
    "$work_dir/busybox-build/busybox" "$work_dir/boot-rootfs/bin/guest-control"; do
    [ -f "$input" ] || { echo "Required prepared input missing: $input" >&2; exit 2; }
done
if [ "$mode" = all ]; then
    for input in "$kernel_build/include/config/kernel.release" \
        "$kernel_build/kernel/torture.ko" "$kernel_build/kernel/rcu/rcutorture.ko" \
        "$kernel_build/kernel/locking/locktorture.ko" \
        "$litmus_dir/generated/run.exe" "$litmus_dir/run-guest.sh" "$litmus_dir/manifest.json" \
        "$project_dir/build/linux-tests/linux-suite" "$project_dir/build/linux-tests/manifest.json"; do
        [ -f "$input" ] || { echo "Required all-mode input missing: $input" >&2; exit 2; }
    done
fi
mkdir -p "$output"
[ ! -e "$output/rootfs.squashfs" ] || {
    echo 'Output image already exists; select a fresh CHI_LINUX_ROOTFS_OUT.' >&2; exit 2;
}
stage=$(mktemp -d "$output/stage.XXXXXX")
printf 'Staging only prepared guest files in %s\n' "$stage"
# Never import host homes, keys, histories, machine identity, network secrets,
# package caches or runtime mounts from the distribution staging directory.
rsync -aH --no-owner --no-group \
    --exclude='/root/***' --exclude='/home/***' --exclude='/dev/***' \
    --exclude='/proc/***' --exclude='/sys/***' --exclude='/run/***' \
    --exclude='/tmp/***' --exclude='/mnt/***' --exclude='/media/***' \
    --exclude='/var/log/***' --exclude='/var/cache/***' --exclude='/var/tmp/***' \
    --exclude='/var/lib/systemd/random-seed' --exclude='/var/lib/dbus/machine-id' \
    --exclude='/etc/machine-id' --exclude='/etc/hostname' --exclude='/etc/hosts' \
    --exclude='/etc/resolv.conf' --exclude='/etc/fstab' \
    --exclude='/etc/ssh/ssh_host_*' --exclude='/etc/ssl/private/***' \
    --exclude='/etc/apt/auth.conf*' --exclude='/etc/netplan/***' \
    --exclude='/etc/NetworkManager/system-connections/***' \
    "$base_root/" "$stage/"
mkdir -p "$stage"/{root,home,dev,proc,sys,run,tmp,build,mnt,media,var/log,var/cache,var/tmp} \
    "$stage/etc/ssl/private" "$stage/opt/chi" "$stage/usr/src"
chmod 1777 "$stage/tmp" "$stage/var/tmp"
printf 'chi-native\n' > "$stage/etc/hostname"
printf '127.0.0.1 localhost chi-native\n::1 localhost\n' > "$stage/etc/hosts"
: > "$stage/etc/machine-id"
: > "$stage/etc/resolv.conf"
: > "$stage/etc/fstab"
# This init never starts a login service; also remove any inherited password
# hash rather than trusting how the base distribution was assembled.
if [ -f "$stage/etc/shadow" ]; then
    awk -F: 'BEGIN {OFS=":"} {$2="!"; print}' "$stage/etc/shadow" > "$stage/etc/shadow.clean"
    chmod 600 "$stage/etc/shadow.clean"
    mv "$stage/etc/shadow.clean" "$stage/etc/shadow"
fi
if [ -f "$stage/etc/gshadow" ]; then
    awk -F: 'BEGIN {OFS=":"} {$2="!"; print}' "$stage/etc/gshadow" > "$stage/etc/gshadow.clean"
    chmod 600 "$stage/etc/gshadow.clean"
    mv "$stage/etc/gshadow.clean" "$stage/etc/gshadow"
fi
rsync -a --no-owner --no-group --exclude='.git' "$kernel_source/" "$stage/usr/src/linux-6.6.52/"
for script in rootfs-entry.sh run-kernel-build.sh; do
    install -m755 "$project_dir/native/linux-platform/$script" "$stage/opt/chi/$script"
done
install -m755 "$work_dir/boot-rootfs/bin/guest-control" "$stage/opt/chi/guest-control"
if [ "$mode" = all ]; then
    install -m755 "$project_dir/native/linux-platform/run-kernel-torture.sh" "$stage/opt/chi/run-kernel-torture.sh"
    install -m755 "$project_dir/build/linux-tests/linux-suite" "$stage/opt/chi/linux-suite"
    install -m644 "$project_dir/build/linux-tests/manifest.json" "$stage/opt/chi/linux-suite-manifest.json"
    install -m755 "$litmus_dir/generated/run.exe" "$stage/opt/chi/litmus-run.exe"
    install -m755 "$litmus_dir/run-guest.sh" "$stage/opt/chi/run-litmus.sh"
    install -m644 "$litmus_dir/manifest.json" "$stage/opt/chi/litmus-manifest.json"
    release=$(cat "$kernel_build/include/config/kernel.release")
    case "$release" in 6.6.52-chi-native) ;; *) echo "Unexpected boot kernel release: $release" >&2; exit 2;; esac
    for module in kernel/torture.ko kernel/rcu/rcutorture.ko kernel/locking/locktorture.ko; do
        install -D -m644 "$kernel_build/$module" "$stage/lib/modules/$release/kernel/$module"
    done
fi
python3 - "$stage" "$base_root" "$kernel_build" "$epoch" "$mode" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

stage, base, kernel_build = map(Path, sys.argv[1:4])
def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()

def guest_file(name):
    # Resolve absolute guest symlinks inside the staged root, never the host.
    pieces, resolved, count = list(Path(name).parts[1:]), [], 0
    while pieces:
        piece = pieces.pop(0)
        if piece in ('', '.'):
            continue
        if piece == '..':
            if resolved:
                resolved.pop()
            continue
        candidate = stage.joinpath(*resolved, piece)
        if candidate.is_symlink():
            count += 1
            if count > 64:
                raise RuntimeError(f'guest symlink loop: {name}')
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                resolved = []
                pieces = list(target.parts[1:]) + pieces
            else:
                pieces = list(target.parts) + pieces
        else:
            resolved.append(piece)
    return stage.joinpath(*resolved)

tools = {}
for name in ('gcc', 'make', 'as', 'ld', 'sh'):
    path = guest_file('/usr/bin/' + name)
    with path.open('rb') as binary:
        header = binary.read(20)
    assert header[:6] == b'\x7fELF\x02\x01' and int.from_bytes(header[18:20], 'little') == 243, name
    tools[name] = {'path': '/' + str(path.relative_to(stage)), 'elfMachine': 243, 'sha256': digest(path)}
for directory in ('root', 'home', 'etc/ssl/private'):
    assert not any((stage / directory).iterdir()), directory
tree = hashlib.sha256()
source = stage / 'usr/src/linux-6.6.52'
for path in sorted(source.rglob('*')):
    relative = str(path.relative_to(source)).encode()
    if path.is_symlink():
        tree.update(b'L\0' + relative + b'\0' + os.readlink(path).encode() + b'\0')
    elif path.is_file():
        tree.update(b'F\0' + relative + b'\0' + digest(path).encode() + b'\0')
manifest = {
    'format': 'chi-linux-rootfs-1', 'base': 'Ubuntu noble riscv64 prepared distribution',
    'preparedMode': sys.argv[5], 'defaultGuestMode': 'kernel-build',
    'sourceDateEpoch': int(sys.argv[4]), 'architecture': 'riscv64',
    'bootMemoryBytes': 4 * 1024**3, 'numaNodes': 2,
    'ramStart': '0x80000000', 'ramEndExclusive': '0x180000000',
    'dtbAddress': '0x17fe00000', 'dtbReservedBytes': 2 * 1024**2,
    'filesystem': 'LZ4 SquashFS image in built-in initramfs; CPU loop + tmpfs overlay',
    'dmaDevices': [], 'buildOutput': 'fresh tmpfs /build/kernel',
    'guestKernelBuild': {'sourceVersion': '6.6.52', 'profile': 'tinyconfig-smp4', 'jobs': 4,
                         'target': 'Image', 'sourceTreeSha256': tree.hexdigest(),
                         'isDefconfigBuild': False, 'executed': False},
    'nativeTools': tools,
    'sanitization': 'No host filesystem copied. Distribution homes/runtime/cache/logs, machine/network identity, SSH/private keys and apt auth excluded; login password hashes locked.',
    'baseDpkgStatusSha256': digest(base / 'var/lib/dpkg/status'),
    'bootKernelConfigSha256': digest(kernel_build / '.config'),
    'moduleSha256': {str(path.relative_to(stage)): digest(path)
                     for path in (stage / 'lib/modules').rglob('*.ko')},
    'testArtifactsSha256': {str(path.relative_to(stage)): digest(path)
                           for path in sorted((stage / 'opt/chi').iterdir()) if path.is_file()},
    'validation': 'Prepared, not yet booted or executed in gem5.'}
(stage / 'opt/chi/rootfs-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
cp "$stage/opt/chi/rootfs-manifest.json" "$output/rootfs-manifest.json"
# LZ4 reduces simulated decompression work. The outer built-in initramfs must
# use CONFIG_INITRAMFS_COMPRESSION_NONE, avoiding a second compression layer.
mksquashfs "$stage" "$output/rootfs.squashfs" -noappend -all-root -no-xattrs \
    -comp lz4 -b 131072 -processors "$jobs" -mkfs-time "$epoch" -all-time "$epoch" \
    > "$output/mksquashfs.log" 2>&1
cat > "$output/kernel-rootfs.config" <<'EOF'
CONFIG_BLK_DEV_INITRD=y
CONFIG_BLK_DEV_LOOP=y
CONFIG_BLK_DEV_LOOP_MIN_COUNT=8
CONFIG_SQUASHFS=y
CONFIG_SQUASHFS_LZ4=y
CONFIG_SQUASHFS_FILE_DIRECT=y
CONFIG_SQUASHFS_COMPILE_DECOMP_MULTI_PERCPU=y
CONFIG_OVERLAY_FS=y
CONFIG_TMPFS=y
CONFIG_TMPFS_XATTR=y
CONFIG_DEVTMPFS=y
CONFIG_PROC_FS=y
CONFIG_SYSFS=y
CONFIG_INITRAMFS_COMPRESSION_NONE=y
EOF
cat > "$output/initramfs.list" <<EOF
dir /dev 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/null 666 0 0 c 1 3
dir /proc 755 0 0
dir /sys 755 0 0
dir /run 755 0 0
dir /tmp 1777 0 0
dir /newroot 755 0 0
dir /bin 755 0 0
dir /sbin 755 0 0
dir /usr 755 0 0
dir /usr/bin 755 0 0
dir /usr/sbin 755 0 0
file /bin/busybox $work_dir/busybox-build/busybox 755 0 0
file /bin/guest-control $work_dir/boot-rootfs/bin/guest-control 755 0 0
file /init $project_dir/native/linux-platform/rootfs-init.sh 755 0 0
file /rootfs.squashfs $output/rootfs.squashfs 444 0 0
EOF
for applet in sh mount mkdir mknod losetup switch_root sleep touch; do
    printf 'slink /bin/%s busybox 777 0 0\n' "$applet" >> "$output/initramfs.list"
done
cat > "$output/README.txt" <<EOF
Prepared only. No guest commands, kernel build or gem5 run were performed.

Merge kernel-rootfs.config into the actual Linux 6.6.52 boot configuration,
set CONFIG_INITRAMFS_SOURCE="$output/initramfs.list", run olddefconfig and
assert every listed required option remains y/value. Build Image/modules and
OpenSBI fw_payload.elf using the same fixed versions/toolchain as boot-01.
Prepared mode: $mode. Default kernel-build mode has no litmus, pthread or
torture-module dependency. For optional all mode, prepare with CHI_ROOTFS_MODE=all
and add chi.rootfs_mode=all to the guest command line. Its modules must match
the final boot kernel release/configuration/ABI and build artifacts.

BusyBox required applets: sh mount mkdir mknod losetup switch_root sleep touch.
Required BusyBox mount options: FEATURE_MOUNT_LOOP, FEATURE_MOUNT_FLAGS.
All actual suite tools and the compiler are RISC-V programs in Ubuntu userspace.

Run existing native_l2_fs.py with:
  --kernel FINAL_FW_PAYLOAD.elf --mem-size 4GiB --numa-nodes 2
  --dtb-addr 0x17fe00000 --max-ticks 1000000000000000
The existing FS config verifies every ELF PT_LOAD lies in RAM and does not
overlap the reserved DTB. Built-in initramfs is part of that Image/PT_LOAD;
there is no separately loaded external initrd or disk DMA path.

Default guest work: clean native tinyconfig+SMP make -j4 Image only. Core tests
have their separate staged-boot evidence and are not repeated by default.
Optional all order: litmus -> torture -> pthread suite -> kernel build.
This is not a defconfig build.
Guest END means commands completed; independent host result checkers are
still mandatory. Files in /build disappear when this simulation ends; Image,
vmlinux, config and full make-log SHA256 plus build log are emitted to UART.
EOF
sha256sum "$output/rootfs.squashfs" "$output/initramfs.list" "$output/kernel-rootfs.config" \
    "$output/rootfs-manifest.json" > "$output/artifacts.sha256"
printf 'Prepared rootfs: %s\n' "$output/rootfs.squashfs"
du -h "$output/rootfs.squashfs"

#!/bin/sh
# Supplement the frozen core suite with actual concurrent randomized migration.
set -eu
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
fail() { echo "CHI_RANDOM_MEMORY_RUN_FAIL=$1"; /bin/guest-control fail 46; exit 1; }
trap 'fail shell_error' EXIT
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
uname -a
echo CHI_LINUX_ONLINE_CPUS="$(cat /sys/devices/system/cpu/online)"
echo CHI_LINUX_ONLINE_NODES="$(cat /sys/devices/system/node/online)"
[ "$(cat /sys/devices/system/cpu/online)" = 0-3 ] || fail cpu_topology
[ "$(cat /sys/devices/system/node/online)" = 0-1 ] || fail numa_topology
echo CHI_LINUX_BOOT_PASS
hash=$(/bin/busybox sha256sum /bin/litmus-smoke | /bin/busybox cut -d ' ' -f 1)
printf 'LINUX_LITMUS_BEGIN {"profile":"smoke","binarySha256":"%s","onlineCpus":4}\n' "$hash"
set +e
/bin/litmus-smoke -a 4 -s 8 -r 1
status=$?
set -e
printf 'LINUX_LITMUS_END {"exitCode":%s}\n' "$status"
[ "$status" -eq 0 ] || fail litmus_smoke
hash=$(/bin/busybox sha256sum /bin/random-memory | /bin/busybox cut -d ' ' -f 1)
printf 'CHI_RANDOM_MEMORY_RUN_BEGIN {"binarySha256":"%s"}\n' "$hash"
set +e
/bin/random-memory --iterations 128 --seed 1 --pages-per-writer 64 \
    --pressure-bytes 1048576 --pressure-samples 64 --timeout 120
status=$?
set -e
printf 'CHI_RANDOM_MEMORY_RUN_END {"exitCode":%s}\n' "$status"
[ "$status" -eq 0 ] || fail random_memory
sync
trap - EXIT
# This standalone run has one exit; tcdrain in guest-control preserves the log.
/bin/guest-control stage random-memory-complete
while :; do sleep 60; done

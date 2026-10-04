#!/bin/sh
# PID 1 of the prepared Ubuntu userspace; every program executes on gem5 KMH.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
export HOME=/root
mode=${CHI_ROOTFS_MODE:-kernel-build}
for argument in $(cat /proc/cmdline); do
    case "$argument" in chi.rootfs_mode=*) mode=${argument#chi.rootfs_mode=};; esac
done
fail() {
    echo "CHI_LINUX_ROOTFS_FAIL reason=$1"
    /opt/chi/guest-control fail 52
    while :; do sleep 60; done
}
trap 'fail unexpected_entry_error' EXIT
case "$mode" in kernel-build|all) ;; *) fail invalid_rootfs_mode;; esac
[ "$(uname -m)" = riscv64 ] || fail architecture
cpus=$(cat /sys/devices/system/cpu/online)
nodes=$(cat /sys/devices/system/node/online)
echo "CHI_LINUX_ONLINE_CPUS=$cpus"
echo "CHI_LINUX_ONLINE_NODES=$nodes"
[ "$cpus" = 0-3 ] && [ "$nodes" = 0-1 ] || fail topology
# 4 GiB physical RAM leaves substantially more than 3 GiB available after
# kernel/firmware reservations. This is an explicit rootfs memory contract.
mem_kib=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
[ "$mem_kib" -ge 3145728 ] || fail insufficient_ram
echo CHI_LINUX_BOOT_PASS
mount -t tmpfs -o mode=1777,size=512m tmpfs /tmp
mount -t tmpfs -o mode=755,size=1536m tmpfs /build
uname -a
cat /opt/chi/rootfs-manifest.json
echo "CHI_LINUX_ROOTFS_BEGIN mode=$mode cpus=$cpus nodes=$nodes mem_kib=$mem_kib"
run_stage() {
    name=$1
    shift
    echo "CHI_LINUX_STAGE_BEGIN name=$name"
    status=0
    "$@" || status=$?
    echo "CHI_LINUX_STAGE_END name=$name exit_code=$status"
    [ "$status" -eq 0 ] || fail "stage_$name"
}
if [ "$mode" = all ]; then
    [ -x /opt/chi/litmus-run.exe ] && [ -x /opt/chi/linux-suite ] || fail missing_all_mode_artifacts
    run_stage litmus sh /opt/chi/run-litmus.sh /opt/chi/litmus-run.exe
    run_stage kernel_torture sh /opt/chi/run-kernel-torture.sh
    run_stage pthread_suite /opt/chi/linux-suite --iterations 128 --timeout 120
fi
run_stage kernel_build sh /opt/chi/run-kernel-build.sh
# This marker means every command returned successfully. The host must still
# validate each test's evidence/outcomes with its independent checker.
echo CHI_LINUX_ROOTFS_END status=completed
sync
trap - EXIT
# stage flushes stdio and drains the serial tty before m5_exit. This rootfs is
# checked by the kernel-build checker, without the core suite-pipeline gate.
/opt/chi/guest-control stage kernel-build
while :; do sleep 60; done

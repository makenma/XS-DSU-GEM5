#!/bin/sh
# One boot, checked host gates between actual guest tests.
set -eu
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
fail() { echo "CHI_LINUX_PIPELINE_FAIL=$1"; /bin/guest-control fail 45; exit 1; }
trap 'fail shell_error' EXIT
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
uname -a
cat /proc/cpuinfo
echo CHI_LINUX_ONLINE_CPUS="$(cat /sys/devices/system/cpu/online)"
echo CHI_LINUX_ONLINE_NODES="$(cat /sys/devices/system/node/online)"
[ "$(cat /sys/devices/system/cpu/online)" = 0-3 ] || fail cpu_topology
[ "$(cat /sys/devices/system/node/online)" = 0-1 ] || fail numa_topology
echo CHI_LINUX_BOOT_PASS
/bin/guest-control stage boot

run_litmus() {
    profile=$1
    samples=$2
    binary=/bin/litmus-$profile
    hash=$(sha256sum "$binary" | cut -d ' ' -f 1)
    printf 'LINUX_LITMUS_BEGIN {"profile":"%s","binarySha256":"%s","onlineCpus":4}\n' "$profile" "$hash"
    set +e
    "$binary" -a 4 -s "$samples" -r 1
    status=$?
    set -e
    printf 'LINUX_LITMUS_END {"exitCode":%s}\n' "$status"
    [ "$status" -eq 0 ] || fail "litmus_$profile"
    /bin/guest-control stage "litmus-$profile"
}
run_litmus smoke 8
run_litmus large 128

/bin/run-kernel-torture.sh
/bin/guest-control stage torture
/bin/linux-suite --iterations 128 --timeout 120 --test all
/bin/guest-control stage pthreads

/bin/linux-cbo --phase disabled
insmod /lib/modules/$(uname -r)/extra/chi_cbo_test.ko
set +e
/bin/linux-cbo --phase enabled
status=$?
set -e
rmmod chi_cbo_test
[ "$status" -eq 0 ] || fail cbo
/bin/guest-control stage cbo
insmod /lib/modules/$(uname -r)/extra/chi_uncached_test.ko
set +e
/bin/linux-uncached
status=$?
set -e
rmmod chi_uncached_test
[ "$status" -eq 0 ] || fail uncached
/bin/guest-control stage uncached
echo CHI_LINUX_PIPELINE_COMPLETE
sync
trap - EXIT
/bin/guest-control stage complete
while :; do sleep 60; done

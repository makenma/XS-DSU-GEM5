#!/bin/sh
# Bootstrap evidence only. Test-suite success is a separate gate.
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
echo CHI_LINUX_BOOT_BEGIN
uname -a
cat /proc/cmdline
echo CHI_LINUX_ONLINE_CPUS="$(cat /sys/devices/system/cpu/online)"
echo CHI_LINUX_ONLINE_NODES="$(cat /sys/devices/system/node/online)"
cat /proc/cpuinfo
if [ "$(cat /sys/devices/system/cpu/online)" != "0-3" ]; then
    echo CHI_LINUX_BOOT_FAIL_CPU_COUNT
    /bin/guest-control fail 41
fi
if [ "$(cat /sys/devices/system/node/online)" != "0-1" ]; then
    echo CHI_LINUX_BOOT_FAIL_NUMA_COUNT
    /bin/guest-control fail 42
fi
echo CHI_LINUX_BOOT_PASS
sync
/bin/guest-control exit
while :; do sleep 60; done

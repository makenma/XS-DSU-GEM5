#!/bin/sh
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
uname -a
echo CHI_LINUX_ONLINE_CPUS="$(cat /sys/devices/system/cpu/online)"
echo CHI_LINUX_ONLINE_NODES="$(cat /sys/devices/system/node/online)"
if [ "$(cat /sys/devices/system/cpu/online)" != "0-3" ] || \
   [ "$(cat /sys/devices/system/node/online)" != "0-1" ]; then
    echo CHI_LINUX_BOOT_FAIL_TOPOLOGY
    /bin/guest-control fail 43
fi
echo CHI_LINUX_BOOT_PASS
hash=$(/bin/busybox sha256sum /bin/linux-litmus | /bin/busybox cut -d ' ' -f 1)
printf 'LINUX_LITMUS_BEGIN {"profile":"smoke","binarySha256":"%s","onlineCpus":4}\n' "$hash"
/bin/linux-litmus -a 4 -s 8 -r 1
status=$?
printf 'LINUX_LITMUS_END {"exitCode":%s}\n' "$status"
sync
if [ "$status" -ne 0 ]; then /bin/guest-control fail 44; fi
# Host checker still has to validate outcomes against herd7 and full test list.
/bin/guest-control exit
while :; do sleep 60; done

#!/bin/sh
# Small built-in initramfs: all persistent guest files are in a RAM-backed
# SquashFS. Loop I/O is performed by Linux CPUs, with no emulated DMA device.
set -eu
export PATH=/bin:/sbin:/usr/bin:/usr/sbin
fail() {
    echo "CHI_ROOTFS_INIT_FAIL reason=$1"
    /bin/guest-control fail 51
    while :; do sleep 60; done
}
trap 'fail unexpected_init_error' EXIT
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs -o mode=755,size=16m tmpfs /run
mkdir -p /run/chi-root/lower /run/chi-root/writable /newroot
# Preserve the image's original RAM pages across switch_root without copying
# them to another filesystem or depending on the deleted initramfs pathname.
touch /run/chi-root/rootfs.squashfs
mount -o bind /rootfs.squashfs /run/chi-root/rootfs.squashfs
[ -b /dev/loop0 ] || mknod /dev/loop0 b 7 0
losetup -r /dev/loop0 /run/chi-root/rootfs.squashfs
mount -t squashfs -o ro /dev/loop0 /run/chi-root/lower
mount -t tmpfs -o mode=755,size=512m tmpfs /run/chi-root/writable
mkdir /run/chi-root/writable/upper /run/chi-root/writable/work
mount -t overlay overlay -o lowerdir=/run/chi-root/lower,upperdir=/run/chi-root/writable/upper,workdir=/run/chi-root/writable/work /newroot
for directory in dev proc sys run; do
    mount --move "/$directory" "/newroot/$directory"
done
echo CHI_ROOTFS_READY filesystem=squashfs-overlay backing=ram-loop dma=none
trap - EXIT
exec switch_root /newroot /opt/chi/rootfs-entry.sh

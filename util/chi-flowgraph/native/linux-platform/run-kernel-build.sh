#!/bin/sh
# A real, clean native Linux Image build on the four simulated RISC-V CPUs.
# The deliberately small tinyconfig+SMP profile is not a defconfig build.
set -eu
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
export KBUILD_BUILD_USER=chi KBUILD_BUILD_HOST=gem5
export KBUILD_BUILD_TIMESTAMP='Mon Jan 1 00:00:00 UTC 2024'
export SOURCE_DATE_EPOCH=1704067200
unset CROSS_COMPILE CC HOSTCC CFLAGS CXXFLAGS LDFLAGS MAKEFLAGS MFLAGS
source_dir=/usr/src/linux-6.6.52
build_dir=/build/kernel
fail() { echo "CHI_KERNEL_BUILD_FAIL reason=$1"; exit 1; }
[ "$(uname -m)" = riscv64 ] || fail architecture
[ "$(cat /sys/devices/system/cpu/online)" = 0-3 ] || fail cpu_count
[ ! -e "$build_dir" ] || fail output_not_fresh
[ -f "$source_dir/Makefile" ] || fail missing_source
mkdir "$build_dir"
read start_seconds idle < /proc/uptime
compiler=$(gcc -dumpmachine)
case "$compiler" in riscv64*-linux-gnu) ;; *) fail compiler_target;; esac
compiler_version=$(gcc -dumpfullversion)
printf 'CHI_KERNEL_BUILD_BEGIN {"profile":"tinyconfig-smp4","sourceVersion":"6.6.52","jobs":4,"onlineCpus":"0-3","architecture":"riscv64","compilerTarget":"%s","compilerVersion":"%s","freshOutput":true,"startSeconds":%s}\n' "$compiler" "$compiler_version" "$start_seconds"
echo CHI_KERNEL_BUILD_CPUSTAT_BEFORE
grep '^cpu[0-9]' /proc/stat
make -C "$source_dir" O="$build_dir" ARCH=riscv tinyconfig > /build/kernel.config.log 2>&1 || {
    cat /build/kernel.config.log
    fail tinyconfig
}
"$source_dir/scripts/config" --file "$build_dir/.config" --enable 64BIT --enable MMU \
    --enable SMP --set-val NR_CPUS 4 --disable DEBUG_INFO --enable DEBUG_INFO_NONE \
    --disable RISCV_ISA_V --disable RISCV_ISA_ZBB --disable RISCV_ISA_ZICBOM \
    --disable RISCV_ISA_ZICBOZ --disable WERROR --disable LOCALVERSION_AUTO \
    --set-str LOCALVERSION '-chi-guest-small'
make -C "$source_dir" O="$build_dir" ARCH=riscv olddefconfig >> /build/kernel.config.log 2>&1 || {
    cat /build/kernel.config.log
    fail olddefconfig
}
grep -qx CONFIG_SMP=y "$build_dir/.config" || fail missing_smp
grep -qx CONFIG_NR_CPUS=4 "$build_dir/.config" || fail wrong_nr_cpus
[ ! -e "$build_dir/arch/riscv/boot/Image" ] && [ ! -e "$build_dir/vmlinux" ] || fail preexisting_image
echo CHI_KERNEL_BUILD_CONFIG_READY
status=0
make -C "$source_dir" O="$build_dir" ARCH=riscv -j4 --output-sync=target Image \
    > /build/kernel.make.log 2>&1 || status=$?
cat /build/kernel.make.log
[ "$status" -eq 0 ] || fail "make_exit_$status"
image="$build_dir/arch/riscv/boot/Image"
[ -s "$image" ] && [ -s "$build_dir/vmlinux" ] || fail missing_output
readelf -h "$build_dir/vmlinux" > /build/kernel.elf-header.txt
grep -Eq 'Class:[[:space:]]+ELF64' /build/kernel.elf-header.txt || fail elf_class
grep -Eq 'Machine:[[:space:]]+RISC-V' /build/kernel.elf-header.txt || fail elf_machine
objects=$(find "$build_dir" -type f -name '*.o' | wc -l)
[ "$objects" -gt 0 ] || fail no_compiled_objects
image_sha=$(sha256sum "$image"); image_sha=${image_sha%% *}
elf_sha=$(sha256sum "$build_dir/vmlinux"); elf_sha=${elf_sha%% *}
config_sha=$(sha256sum "$build_dir/.config"); config_sha=${config_sha%% *}
log_sha=$(sha256sum /build/kernel.make.log); log_sha=${log_sha%% *}
image_bytes=$(stat -c %s "$image")
read end_seconds idle < /proc/uptime
echo CHI_KERNEL_BUILD_CPUSTAT_AFTER
grep '^cpu[0-9]' /proc/stat
printf 'CHI_KERNEL_BUILD_END {"exitCode":0,"profile":"tinyconfig-smp4","target":"Image","jobs":4,"elfClass":"ELF64","elfMachine":"RISC-V","imageBytes":%s,"objectFiles":%s,"imageSha256":"%s","vmlinuxSha256":"%s","configSha256":"%s","makeLogSha256":"%s","endSeconds":%s}\n' "$image_bytes" "$objects" "$image_sha" "$elf_sha" "$config_sha" "$log_sha" "$end_seconds"

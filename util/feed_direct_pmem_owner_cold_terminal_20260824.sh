#!/usr/bin/env bash

set -u

repo=/nfs/home/majunhong/project/DSU-GEM5-TEST/XS-DSU-GEM5
outdir=${repo}/m5out/linux-16core-128mb-direct-pmem-owner-cold-v7-detached-20260824
audit_dir=${outdir}/audit
pid_file=${audit_dir}/gem5.pid
helper=/tmp/chi_guest_checkpoint_repo
log=${audit_dir}/terminal_feeder.log
commands=${audit_dir}/terminal_commands.txt
terminal_log=${audit_dir}/terminal.log
expected_sha=53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38

mkdir -p "${audit_dir}"

note()
{
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >> "${log}"
}

fail()
{
    note "FAIL: $*"
    exit 1
}

[[ -s "${helper}" ]] || fail "missing helper ${helper}"
actual_sha=$(sha256sum "${helper}" | awk '{print $1}')
[[ "${actual_sha}" == "${expected_sha}" ]] ||
    fail "host helper SHA256 mismatch: ${actual_sha}"

for _attempt in $(seq 1 120); do
    [[ -s "${pid_file}" ]] && break
    sleep 1
done
[[ -s "${pid_file}" ]] || fail "gem5 pid file did not appear"
gem5_pid=$(<"${pid_file}")

terminal_port=
for _attempt in $(seq 1 720); do
    terminal_port=$(
        sed -n \
            's/.*system\.terminal: Listening for connections on port \([0-9][0-9]*\).*/\1/p' \
            "${outdir}/simerr" 2>/dev/null | tail -n 1
    )
    [[ -n "${terminal_port}" ]] && break
    kill -0 "${gem5_pid}" 2>/dev/null ||
        fail "gem5 exited before terminal port publication"
    sleep 5
done
[[ -n "${terminal_port}" ]] || fail "terminal port timeout"
printf '%s\n' "${terminal_port}" > "${audit_dir}/terminal.port"

helper_b64=$(base64 -w0 "${helper}")
printf '%s\n' \
    "printf '%s' '${helper_b64}' | /bin/busybox base64 -d > /tmp/chi_guest_checkpoint_repo" \
    '/bin/busybox chmod 755 /tmp/chi_guest_checkpoint_repo' \
    'echo DIRECT_PMEM_OWNER_COLD_SHELL_20260824' \
    '/bin/busybox mkdir -p /proc /sys' \
    '/bin/busybox mount -t proc proc /proc || true' \
    '/bin/busybox mount -t sysfs sysfs /sys || true' \
    'echo DIRECT_PMEM_OWNER_PROC_SYS_READY_20260824' \
    '/bin/busybox uname -a' \
    "printf 'DIRECT_PMEM_OWNER_CPU_COUNT='; /bin/busybox grep -c '^processor' /proc/cpuinfo" \
    "printf 'DIRECT_PMEM_OWNER_CPU_ONLINE='; /bin/busybox cat /sys/devices/system/cpu/online" \
    "printf 'DIRECT_PMEM_OWNER_UPTIME='; /bin/busybox cat /proc/uptime" \
    "printf 'DIRECT_PMEM_OWNER_SHELL_PID='; /bin/busybox sh -c 'echo \$\$'" \
    '/bin/busybox sha256sum /tmp/chi_guest_checkpoint_repo' \
    '/bin/busybox sync' \
    'echo DIRECT_PMEM_OWNER_TRIGGER_CHECKPOINT_20260824' \
    '/tmp/chi_guest_checkpoint_repo' \
    > "${commands}"

note "connecting terminal port=${terminal_port} gem5_pid=${gem5_pid}"
{
    while IFS= read -r command; do
        printf '%s\n' "${command}"
    done < "${commands}"
    while kill -0 "${gem5_pid}" 2>/dev/null; do
        sleep 60
    done
} | nc 127.0.0.1 "${terminal_port}" > "${terminal_log}" 2>&1
terminal_rc=$?

if grep -q 'm5 terminal: Terminal 0' "${terminal_log}"; then
    note "terminal handshake received; nc exit=${terminal_rc}"
else
    fail "terminal handshake missing; nc exit=${terminal_rc}"
fi

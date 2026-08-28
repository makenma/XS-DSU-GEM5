#!/usr/bin/env bash

# Restore only after the fifth cold checkpoint passes every semantic gate,
# execute real guest terminal commands, then re-checkpoint and audit again.

set -u

repo=/nfs/home/majunhong/project/DSU-GEM5-TEST/XS-DSU-GEM5
cold_out=${repo}/m5out/linux-16core-128mb-lower-to-upper-cold-detached-20260824
cold_cpt_parent=${cold_out}/checkpoints
cold_audit=${cold_out}/audit
gate_log=${cold_audit}/restore_gate.log
gate_lock=${cold_audit}/restore_gate.v5.lock
restore_out=${repo}/m5out/linux-16core-128mb-lower-to-upper-restore-detached-20260824
restore_audit=${restore_out}/audit
restore_cpt_parent=${restore_out}/checkpoints
gem5=${repo}/build/RISCV/gem5.opt
payload=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin
expected_helper_sha=53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
pmp_fault_storm_threshold=100

mkdir -p "${cold_audit}"

note()
{
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" \
        >> "${gate_log}"
}

fail()
{
    note "FAIL: $*"
    exit 1
}

if ! mkdir "${gate_lock}" 2>/dev/null; then
    fail "another restore gate owns ${gate_lock}"
fi
printf '%s\n' "$$" > "${cold_audit}/restore_gate.pid"
note "restore gate v5 started; waiting for exact 15/15 lower-to-upper audit; PMP storm threshold=${pmp_fault_storm_threshold}; restored checkpoint output=${restore_cpt_parent}"

while [[ ! -f "${cold_audit}/stack_audit.status" ]]; do
    if grep -q ' FAIL:' "${cold_audit}/watcher.log" 2>/dev/null; then
        fail "cold-checkpoint watcher reported failure"
    fi
    sleep 60
done

for status_name in gzip chi_audit stack_audit; do
    grep -Fxq 'PASS' "${cold_audit}/${status_name}.status" 2>/dev/null ||
        fail "cold-checkpoint ${status_name}.status is not PASS"
done
grep -Fxq 'idle_summary passed=15 total=15' \
    "${cold_audit}/stack_audit.txt" ||
    fail "cold checkpoint did not pass exact 15/15 gate"
grep -q 'Exiting @ tick .* because checkpoint' "${cold_out}/simout" ||
    fail "cold run lacks normal checkpoint exit marker"

mapfile -t cold_cpts < <(
    find "${cold_cpt_parent}" -mindepth 1 -maxdepth 1 \
        -type d -name 'cpt.*' -print | sort -V
)
[[ ${#cold_cpts[@]} -eq 1 ]] ||
    fail "expected one cold checkpoint, found ${#cold_cpts[@]}"
cold_cpt=${cold_cpts[0]}
[[ -x "${gem5}" ]] || fail "gem5 binary missing: ${gem5}"
[[ -s "${payload}" ]] || fail "firmware payload missing: ${payload}"
recorded_gem5_sha=$(awk 'NR == 1 {print $1}' \
    "${cold_audit}/binary_and_source.sha256" 2>/dev/null)
current_gem5_sha=$(sha256sum "${gem5}" | awk '{print $1}')
[[ -n "${recorded_gem5_sha}" &&
        "${current_gem5_sha}" == "${recorded_gem5_sha}" ]] ||
    fail "restore gem5 SHA256 differs from cold binary: recorded=${recorded_gem5_sha:-missing} current=${current_gem5_sha}"

if [[ -e "${restore_out}" ]] &&
        find "${restore_out}" -mindepth 1 -print -quit | grep -q .; then
    fail "restore outdir is already non-empty: ${restore_out}"
fi
mkdir -p "${restore_out}" "${restore_audit}"
note "PASS: cold audit gate; restoring ${cold_cpt}"

"${gem5}" \
    --listener-mode=on \
    --redirect-stdout \
    --redirect-stderr \
    --outdir="${restore_out}" \
    "${repo}/configs/example/kmhv2_chi_6x4_hnf.py" \
    --raw-cpt \
    --generic-rv-cpt="${payload}" \
    --num-cpus=16 \
    --mem-size=128MB \
    --mem-type=SimpleMemory \
    --maxinsts=0 \
    -m 200000000000 \
    --no-pf \
    -r 1 \
    --max-checkpoints=1 \
    --checkpoint-dir="${cold_cpt_parent}" \
    --restore-terminal-wait=120 &
restore_pid=$!
printf '%s\n' "${restore_pid}" > "${cold_audit}/restore.pid"
note "restore launched pid=${restore_pid} outdir=${restore_out}"

terminal_port=
for _attempt in $(seq 1 720); do
    terminal_port=$(
        sed -n \
            's/.*system\.terminal: Listening for connections on port \([0-9][0-9]*\).*/\1/p' \
            "${restore_out}/simerr" 2>/dev/null | tail -n 1
    )
    [[ -n "${terminal_port}" ]] && break
    kill -0 "${restore_pid}" 2>/dev/null ||
        fail "restore exited before terminal port publication"
    sleep 5
done
[[ -n "${terminal_port}" ]] || fail "restore terminal port timeout"
printf '%s\n' "${terminal_port}" > "${restore_audit}/terminal.port"

commands=${restore_audit}/terminal_commands.txt
printf '%s\n' \
    'echo FINAL_LOWER_TO_UPPER_RESTORE_TERMINAL_ACCEPTED_20260824' \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_UNAME='; /bin/busybox uname -a" \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_CPU_COUNT='; /bin/busybox grep -c '^processor' /proc/cpuinfo" \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_CPU_ONLINE='; /bin/busybox cat /sys/devices/system/cpu/online" \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_UPTIME='; /bin/busybox cat /proc/uptime" \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_SHELL_PID='; echo \$\$" \
    "printf 'FINAL_LOWER_TO_UPPER_RESTORE_HELPER_SHA256='; /bin/busybox sha256sum /tmp/chi_guest_checkpoint_repo | /bin/busybox cut -d' ' -f1" \
    'echo FINAL_LOWER_TO_UPPER_RESTORE_COMMANDS_DONE_20260824' \
    '/bin/busybox sync' \
    'echo FINAL_LOWER_TO_UPPER_RESTORE_RECHECKPOINT_REQUEST_20260824' \
    '/tmp/chi_guest_checkpoint_repo' \
    > "${commands}"

note "connecting terminal port=${terminal_port}; acceptance input queued"
{
    while IFS= read -r command; do
        printf '%s\n' "${command}"
    done < "${commands}"
    while kill -0 "${restore_pid}" 2>/dev/null; do
        sleep 60
    done
} | nc 127.0.0.1 "${terminal_port}" \
    > "${restore_audit}/terminal.log" 2>&1 &
terminal_pid=$!
printf '%s\n' "${terminal_pid}" > "${cold_audit}/restore_terminal.pid"

terminal_attached=0
for _attempt in $(seq 1 30); do
    if grep -q 'm5 terminal: Terminal 0' \
            "${restore_audit}/terminal.log" 2>/dev/null; then
        terminal_attached=1
        break
    fi
    kill -0 "${terminal_pid}" 2>/dev/null || break
    sleep 1
done
[[ ${terminal_attached} -eq 1 ]] || fail "restore terminal handshake missing"
note "restore terminal handshake received"

crash_seen=0
while kill -0 "${restore_pid}" 2>/dev/null; do
    if [[ ${crash_seen} -eq 0 ]] &&
            grep -Eiq \
                'Unable to handle kernel NULL pointer|Oops \[#|Kernel panic|panic:|fatal:|Program aborted' \
                "${restore_out}/simout" "${restore_out}/simerr" \
                2>/dev/null; then
        crash_seen=1
        note "FAIL: crash signature detected while restore still runs"
    fi
    if [[ ${crash_seen} -eq 0 ]]; then
        pmp_faults=$(
            grep -Eih 'pmp access fault' \
                "${restore_out}/simout" "${restore_out}/simerr" \
                2>/dev/null | awk 'END {print NR}'
        )
        if (( pmp_faults >= pmp_fault_storm_threshold )); then
            crash_seen=1
            note "FAIL: PMP fault storm detected count=${pmp_faults} threshold=${pmp_fault_storm_threshold}"
        fi
    fi
    sleep 60
done

wait "${restore_pid}"
restore_rc=$?
wait "${terminal_pid}" 2>/dev/null || true
printf '%s\n' "${restore_rc}" > "${restore_audit}/gem5.exit_status"
tr -d '\r' < "${restore_out}/simout" \
    > "${restore_audit}/simout.normalized"

[[ ${restore_rc} -eq 0 ]] || fail "restore gem5 exited rc=${restore_rc}"
[[ ${crash_seen} -eq 0 ]] || fail "restore log contains crash signature"
grep -q 'Exiting @ tick .* because checkpoint' "${restore_out}/simout" ||
    fail "restore did not exit through guest re-checkpoint"

normalized=${restore_audit}/simout.normalized
grep -Fxq 'FINAL_LOWER_TO_UPPER_RESTORE_TERMINAL_ACCEPTED_20260824' \
    "${normalized}" || fail "missing executed restore marker"
grep -Eq '^FINAL_LOWER_TO_UPPER_RESTORE_UNAME=Linux .* riscv64 GNU/Linux$' \
    "${normalized}" || fail "missing real restored uname"
grep -Fxq 'FINAL_LOWER_TO_UPPER_RESTORE_CPU_COUNT=16' "${normalized}" ||
    fail "restored guest did not report 16 CPUs"
grep -Fxq 'FINAL_LOWER_TO_UPPER_RESTORE_CPU_ONLINE=0-15' "${normalized}" ||
    fail "restored guest online mask mismatch"
grep -Eq '^FINAL_LOWER_TO_UPPER_RESTORE_UPTIME=[0-9]+\.[0-9]+ [0-9]+\.[0-9]+$' \
    "${normalized}" || fail "missing real restored uptime"
grep -Fxq "FINAL_LOWER_TO_UPPER_RESTORE_HELPER_SHA256=${expected_helper_sha}" \
    "${normalized}" || fail "restored helper SHA256 mismatch"
grep -Fxq 'FINAL_LOWER_TO_UPPER_RESTORE_COMMANDS_DONE_20260824' \
    "${normalized}" || fail "restore command sequence incomplete"

# run_vanilla() keeps --checkpoint-dir as the immutable restore input parent,
# but directs every checkpoint produced by the restored guest to
# <restore_out>/checkpoints.  This prevents a second artifact from changing
# the meaning of a future "-r 1" selection and keeps the acceptance output
# self-contained under the restore outdir.
mapfile -t restore_cpts < <(
    find "${restore_cpt_parent}" -mindepth 1 -maxdepth 1 \
        -type d -name 'cpt.*' -print | sort -V
)
[[ ${#restore_cpts[@]} -eq 1 ]] ||
    fail "expected exactly one post-restore checkpoint in ${restore_cpt_parent}, found ${#restore_cpts[@]}"
restore_cpt=${restore_cpts[0]}
note "post-restore checkpoint detected: ${restore_cpt}"
restore_m5=${restore_cpt}/m5.cpt
restore_pmem=${restore_cpt}/system.physmem.store0.pmem
[[ -s "${restore_m5}" && -s "${restore_pmem}" ]] ||
    fail "post-restore checkpoint files missing"

if gzip -t "${restore_pmem}" 2> "${restore_audit}/gzip.err"; then
    printf 'PASS\n' > "${restore_audit}/gzip.status"
else
    printf 'FAIL\n' > "${restore_audit}/gzip.status"
    fail "post-restore gzip integrity failed"
fi
sha256sum "${restore_m5}" "${restore_pmem}" \
    > "${restore_audit}/compressed.sha256"

raw_dir=/tmp/lower-to-upper-restore-audit-20260824
raw_pmem=${raw_dir}/pmem.raw
mkdir -p "${raw_dir}"
gzip -dc "${restore_pmem}" > "${raw_pmem}" ||
    fail "post-restore pmem decompression failed"
stat -c '%s %n' "${restore_m5}" "${restore_pmem}" "${raw_pmem}" \
    > "${restore_audit}/sizes.txt"
sha256sum "${raw_pmem}" > "${restore_audit}/raw.sha256"

if python3 "${repo}/util/debug_chi_checkpoint.py" \
        "${restore_m5}" "${raw_pmem}" \
        > "${restore_audit}/chi_audit.txt" 2>&1; then
    printf 'PASS\n' > "${restore_audit}/chi_audit.status"
else
    printf 'FAIL\n' > "${restore_audit}/chi_audit.status"
    fail "post-restore CHI audit failed"
fi

python3 "${repo}/util/audit_riscv_checkpoint_stacks.py" \
    "${restore_m5}" "${raw_pmem}" \
    > "${restore_audit}/stack_audit.txt" 2>&1
stack_rc=$?
if [[ ${stack_rc} -eq 0 ]] &&
        grep -Fxq 'idle_summary passed=15 total=15' \
            "${restore_audit}/stack_audit.txt"; then
    printf 'PASS\n' > "${restore_audit}/stack_audit.status"
else
    printf 'FAIL rc=%d\n' "${stack_rc}" \
        > "${restore_audit}/stack_audit.status"
    fail "post-restore checkpoint failed exact 15/15 audit"
fi

note "PASS: restore, terminal commands, re-checkpoint, CHI, and 15/15"
printf 'PASS\n' > "${restore_audit}/final_acceptance.status"

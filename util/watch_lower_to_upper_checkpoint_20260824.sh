#!/usr/bin/env bash

set -u

repo=/nfs/home/majunhong/project/DSU-GEM5-TEST/XS-DSU-GEM5
outdir=${repo}/m5out/linux-16core-128mb-lower-to-upper-cold-detached-20260824
audit_dir=${outdir}/audit
pid_file=${audit_dir}/gem5.pid
watch_log=${audit_dir}/watcher.log

mkdir -p "${audit_dir}"

note()
{
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" \
        >> "${watch_log}"
}

fail()
{
    note "FAIL: $*"
    exit 1
}

for _attempt in $(seq 1 120); do
    [[ -s "${pid_file}" ]] && break
    sleep 1
done
[[ -s "${pid_file}" ]] || fail "gem5 pid file did not appear"
gem5_pid=$(<"${pid_file}")
note "watcher started for gem5 pid=${gem5_pid}"

while true; do
    if grep -q 'Exiting @ tick .* because checkpoint' "${outdir}/simout" \
            2>/dev/null; then
        break
    fi
    if grep -Eiq \
            'Unable to handle kernel NULL pointer|Oops \[#|Kernel panic|panic:|fatal:|Program aborted' \
            "${outdir}/simout" "${outdir}/simerr" 2>/dev/null; then
        fail "crash signature appeared before checkpoint"
    fi
    kill -0 "${gem5_pid}" 2>/dev/null ||
        fail "gem5 exited before a checkpoint marker appeared"
    sleep 60
done

mapfile -t cpt_dirs < <(
    find "${outdir}/checkpoints" -mindepth 1 -maxdepth 1 \
        -type d -name 'cpt.*' -print 2>/dev/null | sort -V
)
[[ ${#cpt_dirs[@]} -eq 1 ]] ||
    fail "expected exactly one checkpoint, found ${#cpt_dirs[@]}"
cpt_dir=${cpt_dirs[0]}
m5_cpt=${cpt_dir}/m5.cpt
pmem_gz=${cpt_dir}/system.physmem.store0.pmem
[[ -s "${m5_cpt}" && -s "${pmem_gz}" ]] ||
    fail "checkpoint files missing or empty: ${cpt_dir}"
note "checkpoint detected: ${cpt_dir}"

for marker in \
        LOWER_TO_UPPER_COLD_SHELL_20260824 \
        LOWER_TO_UPPER_CPU_COUNT=16 \
        LOWER_TO_UPPER_CPU_ONLINE=0-15 \
        LOWER_TO_UPPER_TRIGGER_CHECKPOINT_20260824; do
    grep -Fq "${marker}" "${outdir}/simout" ||
        fail "missing executed guest marker: ${marker}"
done

if gzip -t "${pmem_gz}" 2> "${audit_dir}/gzip.err"; then
    printf 'PASS\n' > "${audit_dir}/gzip.status"
else
    printf 'FAIL\n' > "${audit_dir}/gzip.status"
    fail "gzip integrity check failed"
fi

sha256sum "${m5_cpt}" "${pmem_gz}" > "${audit_dir}/compressed.sha256"
raw_dir=/tmp/lower-to-upper-cpt-audit-20260824
raw_pmem=${raw_dir}/pmem.raw
mkdir -p "${raw_dir}"
gzip -dc "${pmem_gz}" > "${raw_pmem}" ||
    fail "pmem decompression failed"
stat -c '%s %n' "${m5_cpt}" "${pmem_gz}" "${raw_pmem}" \
    > "${audit_dir}/sizes.txt"
sha256sum "${raw_pmem}" > "${audit_dir}/raw.sha256"

if python3 "${repo}/util/debug_chi_checkpoint.py" \
        "${m5_cpt}" "${raw_pmem}" \
        > "${audit_dir}/chi_audit.txt" 2>&1; then
    printf 'PASS\n' > "${audit_dir}/chi_audit.status"
else
    printf 'FAIL\n' > "${audit_dir}/chi_audit.status"
    fail "CHI checkpoint audit failed"
fi

python3 "${repo}/util/audit_riscv_checkpoint_stacks.py" \
    "${m5_cpt}" "${raw_pmem}" \
    > "${audit_dir}/stack_audit.txt" 2>&1
stack_rc=$?
if [[ ${stack_rc} -eq 0 ]] &&
        grep -Fxq 'idle_summary passed=15 total=15' \
            "${audit_dir}/stack_audit.txt"; then
    printf 'PASS\n' > "${audit_dir}/stack_audit.status"
else
    printf 'FAIL rc=%d\n' "${stack_rc}" \
        > "${audit_dir}/stack_audit.status"
    fail "idle-hart stack audit failed rc=${stack_rc}"
fi

note "PASS: terminal markers, gzip, CHI, and exact 15/15 stack audit"

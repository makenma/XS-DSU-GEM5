#!/usr/bin/env bash

set -u

repo=/nfs/home/majunhong/project/DSU-GEM5-TEST/XS-DSU-GEM5
outdir=${repo}/m5out/linux-16core-128mb-all-valid-cold-detached-20260824
audit_dir=${outdir}/audit
watch_log=${audit_dir}/watcher.log
gem5_pid=2606826

mkdir -p "${audit_dir}"

log()
{
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" \
        >> "${watch_log}"
}

log "watcher started for gem5 pid=${gem5_pid}"

while true; do
    if grep -q 'Exiting @ tick .* because checkpoint' "${outdir}/simout" \
            2>/dev/null; then
        break
    fi

    if ! kill -0 "${gem5_pid}" 2>/dev/null; then
        log "FAIL: gem5 exited before a checkpoint exit marker appeared"
        exit 1
    fi

    sleep 60
done

cpt_dir=$(find "${outdir}/checkpoints" -mindepth 1 -maxdepth 1 \
    -type d -name 'cpt.*' -print 2>/dev/null | sort -V | tail -n 1)

if [[ -z "${cpt_dir}" ]]; then
    log "FAIL: checkpoint exit marker exists but no cpt.* directory was found"
    exit 1
fi

m5_cpt=${cpt_dir}/m5.cpt
pmem_gz=${cpt_dir}/system.physmem.store0.pmem

if [[ ! -s "${m5_cpt}" || ! -s "${pmem_gz}" ]]; then
    log "FAIL: checkpoint files are missing or empty: ${cpt_dir}"
    exit 1
fi

log "checkpoint detected: ${cpt_dir}"

if gzip -t "${pmem_gz}" 2> "${audit_dir}/gzip.err"; then
    printf 'PASS\n' > "${audit_dir}/gzip.status"
else
    printf 'FAIL\n' > "${audit_dir}/gzip.status"
    log "FAIL: gzip integrity check failed"
    exit 1
fi

sha256sum "${m5_cpt}" "${pmem_gz}" \
    > "${audit_dir}/compressed.sha256"

raw_dir=/tmp/all-valid-cpt-audit-20260824
raw_pmem=${raw_dir}/pmem.raw
mkdir -p "${raw_dir}"

if ! gzip -dc "${pmem_gz}" > "${raw_pmem}"; then
    log "FAIL: pmem decompression failed"
    exit 1
fi

stat -c '%s %n' "${m5_cpt}" "${pmem_gz}" "${raw_pmem}" \
    > "${audit_dir}/sizes.txt"
sha256sum "${raw_pmem}" > "${audit_dir}/raw.sha256"

if python3 "${repo}/util/debug_chi_checkpoint.py" \
        "${m5_cpt}" "${raw_pmem}" \
        > "${audit_dir}/chi_audit.txt" 2>&1; then
    printf 'PASS\n' > "${audit_dir}/chi_audit.status"
else
    printf 'FAIL\n' > "${audit_dir}/chi_audit.status"
    log "FAIL: CHI checkpoint audit failed"
    exit 1
fi

python3 "${repo}/util/audit_riscv_checkpoint_stacks.py" \
    "${m5_cpt}" "${raw_pmem}" \
    > "${audit_dir}/stack_audit.txt" 2>&1
stack_status=$?

if [[ ${stack_status} -eq 0 ]]; then
    printf 'PASS\n' > "${audit_dir}/stack_audit.status"
    log "PASS: gzip, CHI, and idle-hart stack audits all passed"
else
    printf 'FAIL rc=%d\n' "${stack_status}" \
        > "${audit_dir}/stack_audit.status"
    log "FAIL: idle-hart stack audit failed rc=${stack_status}"
    exit "${stack_status}"
fi

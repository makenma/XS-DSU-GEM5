#!/usr/bin/env bash

set -eu

repo=/nfs/home/majunhong/project/DSU-GEM5-TEST/XS-DSU-GEM5
outdir=${repo}/m5out/linux-16core-128mb-lower-to-upper-cold-detached-20260824
checkpoint_dir=${outdir}/checkpoints
audit_dir=${outdir}/audit
gem5=${repo}/build/RISCV/gem5.opt
payload=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin

if [[ -e "${outdir}" ]] &&
        find "${outdir}" -mindepth 1 -print -quit | grep -q .; then
    printf 'refusing non-empty outdir: %s\n' "${outdir}" >&2
    exit 1
fi

[[ -x "${gem5}" ]] || {
    printf 'missing gem5 binary: %s\n' "${gem5}" >&2
    exit 1
}
[[ -s "${payload}" ]] || {
    printf 'missing firmware payload: %s\n' "${payload}" >&2
    exit 1
}

mkdir -p "${checkpoint_dir}" "${audit_dir}"
printf '%s\n' "$$" > "${audit_dir}/gem5.pid"
date '+%Y-%m-%d %H:%M:%S %Z' > "${audit_dir}/start_time.txt"
sha256sum "${gem5}" "${repo}/src/python/m5/simulate.py" \
    > "${audit_dir}/binary_and_source.sha256"
stat -c '%d:%i %s %y %n' "${gem5}" \
    > "${audit_dir}/gem5_binary.stat"

exec "${gem5}" \
    --listener-mode=on \
    --redirect-stdout \
    --redirect-stderr \
    --outdir="${outdir}" \
    "${repo}/configs/example/kmhv2_chi_6x4_hnf.py" \
    --raw-cpt \
    --generic-rv-cpt="${payload}" \
    --num-cpus=16 \
    --mem-size=128MB \
    --mem-type=SimpleMemory \
    --maxinsts=0 \
    -m 120000000000 \
    --no-pf \
    --max-checkpoints=1 \
    --checkpoint-dir="${checkpoint_dir}"

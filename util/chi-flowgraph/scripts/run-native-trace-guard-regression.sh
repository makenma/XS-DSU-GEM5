#!/usr/bin/env bash
set -euo pipefail
project=/mnt/d/proj/chi_flowgraph
gem5=/root/chi-flowgraph-work/gem5
results="$project/build/linux-validation"
export CHI_GEM5_DIR="$gem5"
for mode in on off; do
    output="$results/se-trace-guard-$mode"
    if [ -e "$output" ]; then
        printf 'Refusing to overwrite existing run: %s\n' "$output" >&2
        exit 2
    fi
    mkdir -p "$output"
    export CHI_FLOWGRAPH_COUNTS="$output/counts.json"
    if [ "$mode" = on ]; then
        export CHI_FLOWGRAPH_OUT="$output/raw.jsonl"
        baseline="$results/se-after"
        extra=()
    else
        unset CHI_FLOWGRAPH_OUT
        baseline="$results/se-before-no-trace"
        extra=(--without-trace)
    fi
    sha256sum "$gem5/build/RISCV/gem5.opt" > "$output/executable.sha256"
    "$gem5/build/RISCV/gem5.opt" --outdir="$output" \
        "$project/native/gem5/native_l2_run.py" \
        --elf-dir "$project/build/native-rtl-program" > "$output/run.log" 2>&1
    python3 "$project/scripts/check-native-model-regression.py" \
        --before "$baseline" --after "$output" \
        --output "$results/trace-guard-regression-$mode.json" "${extra[@]}"
done

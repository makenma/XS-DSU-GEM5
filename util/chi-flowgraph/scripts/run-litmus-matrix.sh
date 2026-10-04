#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
for variant in iriw protocol stress; do
    output_dir="$project_dir/build/litmus-$variant-off"
    extra=();if [[ "$variant" == stress ]];then extra=(--hn-entries 1 --slc-lines 4);fi
    mkdir -p "$output_dir"
    env -u CHI_FLOWGRAPH_OUT CHI_GEM5_DIR="$gem5_dir" "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
        "$project_dir/native/gem5/litmus.py" --elf-dir "$project_dir/build/litmus" --variant "$variant" \
        --max-ticks 30000000 "${extra[@]}" > "$output_dir/run.log" 2>&1
done
for rotation in 1 2 3;do
    output_dir="$project_dir/build/litmus-iriw-rotation-$rotation";mkdir -p "$output_dir"
    CHI_GEM5_DIR="$gem5_dir" CHI_FLOWGRAPH_OUT="$output_dir/raw.jsonl" "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
        "$project_dir/native/gem5/litmus.py" --elf-dir "$project_dir/build/litmus" --variant iriw --rotation "$rotation" \
        --max-ticks 3000000 > "$output_dir/run.log" 2>&1
done
python3 "$project_dir/scripts/check-litmus-invariance.py"

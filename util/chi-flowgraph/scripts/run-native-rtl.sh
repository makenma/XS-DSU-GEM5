#!/usr/bin/env bash
# Use the already-built gem5; actual CPU instructions select all requests.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
output_dir="${CHI_NATIVE_RTL_OUT:-$project_dir/build/native-rtl-run}"
test -x "$gem5_dir/build/RISCV/gem5.opt"
python3 "$project_dir/scripts/build-native-rtl.py"
mkdir -p "$output_dir"
CHI_GEM5_DIR="$gem5_dir" CHI_FLOWGRAPH_OUT="$output_dir/raw.jsonl" \
    "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
    "$project_dir/native/gem5/native_l2_run.py" \
    --elf-dir "$project_dir/build/native-rtl-program" "$@" > "$output_dir/run.log" 2>&1
python3 "$project_dir/scripts/check-native-rtl.py" "$output_dir"
python3 "$project_dir/scripts/check-native-wire.py" --input "$output_dir/raw.jsonl" \
    --output "$output_dir/native-wire-audit.json"
python3 "$project_dir/scripts/check-native-rtl-behavior.py" "$output_dir"

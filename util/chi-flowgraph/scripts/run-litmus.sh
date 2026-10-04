#!/usr/bin/env bash
# Run after bootstrap-wsl.sh and apply-gem5.py; this script never downloads a toolchain.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
variant="${1:-iriw}"
shift || true
test -x "$gem5_dir/build/RISCV/gem5.opt"
python3 "$project_dir/scripts/build-litmus.py"
output_dir="${CHI_LITMUS_OUT:-$project_dir/build/litmus-$variant}"
mkdir -p "$output_dir"
CHI_GEM5_DIR="$gem5_dir" CHI_FLOWGRAPH_OUT="$output_dir/raw.jsonl" \
  "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
  "$project_dir/native/gem5/litmus.py" --elf-dir "$project_dir/build/litmus" \
  --variant "$variant" "$@" > "$output_dir/run.log" 2>&1
cat "$output_dir/flowgraph-result.json"

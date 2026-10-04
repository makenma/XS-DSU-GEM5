#!/usr/bin/env bash
# Reuse the built native CHI L2; this does not rebuild gem5 or inject CHI opcodes.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
gem5_dir="${CHI_GEM5_DIR:-$HOME/chi-flowgraph-work/gem5}"
variant="${1:-iriw-fenced}"
shift || true
iterations="${CHI_LITMUS_ITERATIONS:-128}"
seed="${CHI_LITMUS_SEED:-1}"
case "$variant" in
    iriw|iriw-fenced) ;;
    *) echo 'Variant must be iriw or iriw-fenced' >&2; exit 2 ;;
esac
test -x "$gem5_dir/build/RISCV/gem5.opt"
python3 "$project_dir/scripts/build-native-litmus.py" --variant "$variant" \
    --iterations "$iterations" --seed "$seed"
output_dir="${CHI_NATIVE_LITMUS_OUT:-$project_dir/build/native-litmus-$variant}"
mkdir -p "$output_dir"
CHI_GEM5_DIR="$gem5_dir" CHI_FLOWGRAPH_OUT="$output_dir/raw.jsonl" \
    "$gem5_dir/build/RISCV/gem5.opt" --outdir="$output_dir" \
    "$project_dir/native/gem5/native_l2_run.py" \
    --elf-dir "$project_dir/build/native-litmus-program/$variant" --seed "$seed" \
    "$@" > "$output_dir/run.log" 2>&1
python3 "$project_dir/scripts/check-native-litmus.py" "$output_dir" \
    --variant "$variant" --iterations "$iterations" --seed "$seed"
python3 "$project_dir/scripts/check-native-wire.py" --input "$output_dir/raw.jsonl" \
    --output "$output_dir/native-wire-audit.json" \
    --require-opcode ReadNotSharedDirty --require-opcode ReadUnique \
    --require-opcode SnpRespData --require-opcode CompAck

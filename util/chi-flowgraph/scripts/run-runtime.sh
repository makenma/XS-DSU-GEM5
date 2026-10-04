#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_WORK_DIR:-$HOME/chi-flowgraph-work}"
cd "$work_dir/gem5"
test -x build/RISCV/gem5.opt
mkdir -p "$project_dir/build/runtime" "$project_dir/build/qa" "$project_dir/examples/coarse"
env -u CHI_FLOWGRAPH_OUT build/RISCV/gem5.opt --outdir="$project_dir/build/runtime/off" "$project_dir/native/gem5/smoke.py" > "$project_dir/build/runtime/off.log" 2>&1
CHI_FLOWGRAPH_OUT="$project_dir/examples/coarse/runtime-miss-hit.coarse.jsonl" build/RISCV/gem5.opt --outdir="$project_dir/build/runtime/on" "$project_dir/native/gem5/smoke.py" > "$project_dir/build/runtime/on.log" 2>&1
python3 "$project_dir/scripts/check-runtime.py"
mkdir -p "$project_dir/build/runtime-retry"
env -u CHI_FLOWGRAPH_OUT CHI_FLOWGRAPH_RUNTIME_SCENARIO=retry build/RISCV/gem5.opt --outdir="$project_dir/build/runtime-retry/off" "$project_dir/native/gem5/smoke.py" > "$project_dir/build/runtime-retry/off.log" 2>&1
CHI_FLOWGRAPH_RUNTIME_SCENARIO=retry CHI_FLOWGRAPH_OUT="$project_dir/examples/coarse/runtime-retry.coarse.jsonl" build/RISCV/gem5.opt --outdir="$project_dir/build/runtime-retry/on" "$project_dir/native/gem5/smoke.py" > "$project_dir/build/runtime-retry/on.log" 2>&1
python3 "$project_dir/scripts/check-runtime.py" retry

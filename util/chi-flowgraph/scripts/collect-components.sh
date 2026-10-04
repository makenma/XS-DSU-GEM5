#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_WORK_DIR:-$HOME/chi-flowgraph-work}"
python3 "$project_dir/scripts/apply-gem5.py" --gem5 "$work_dir/gem5"
mkdir -p "$project_dir/examples/coarse" "$project_dir/build/qa"
cd "$work_dir/gem5"
scons build/RISCV/mem/cache/CHI/flowgraph.test.opt build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt build/RISCV/mem/cache/CHI/cache2chi_bridge.test.opt -j"${CHI_JOBS:-4}"
build/RISCV/mem/cache/CHI/cache2chi_bridge.test.opt > "$project_dir/build/qa/rnf-regression.log"
build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt > "$project_dir/build/qa/hnf-regression.log"
CHI_DEMO_DIR="$project_dir/examples/coarse" build/RISCV/mem/cache/CHI/flowgraph.test.opt | tee "$project_dir/build/qa/components.log"

#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_WORK_DIR:-$HOME/chi-flowgraph-work}"
# The sparse WSL disk can advertise much more free space than its Windows host.
if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$project_dir/scripts/check-wsl-space.ps1")" -Distro "$WSL_DISTRO_NAME" -MinimumFreeGiB "${CHI_MIN_BUILD_FREE_GIB:-20}"
fi
mkdir -p "$work_dir/tmp"
export TMPDIR="$work_dir/tmp"
python3 "$project_dir/scripts/apply-gem5.py" --gem5 "$work_dir/gem5"
cd "$work_dir/gem5"
scons build/RISCV/gem5.opt -j"${CHI_JOBS:-4}"
bash "$project_dir/scripts/run-runtime.sh"

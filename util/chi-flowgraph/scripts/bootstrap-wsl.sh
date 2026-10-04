#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_WORK_DIR:-$HOME/chi-flowgraph-work}"
gem5_commit=65f3241a6cd486dffddaca251536f5c691af9a1f
chiron_commit=55cd1e1d92142960af2b57c686fc9f35ce7286be
mkdir -p "$work_dir" "$project_dir/vendor"
if [[ ! -d "$work_dir/gem5/.git" ]]; then
    git clone --no-checkout https://github.com/makenma/XS-DSU-GEM5.git "$work_dir/gem5"
    git -C "$work_dir/gem5" checkout --detach "$gem5_commit"
fi
test "$(git -C "$work_dir/gem5" rev-parse HEAD)" = "$gem5_commit"
if [[ ! -d "$project_dir/vendor/chiron/.git" ]]; then
    git clone --no-checkout https://github.com/RISMicroDevices/CHIron.git "$project_dir/vendor/chiron"
    git -C "$project_dir/vendor/chiron" checkout --detach "$chiron_commit"
fi
test "$(git -c safe.directory="$project_dir/vendor/chiron" -C "$project_dir/vendor/chiron" rev-parse HEAD)" = "$chiron_commit"
if [[ ! -f "$project_dir/vendor/json/nlohmann/json.hpp" ]]; then
    if [[ ! -d "$work_dir/json/.git" ]]; then
        git clone --depth 1 --branch v3.11.3 https://github.com/nlohmann/json.git "$work_dir/json"
    fi
    test "$(git -C "$work_dir/json" describe --tags --exact-match)" = v3.11.3
    mkdir -p "$project_dir/vendor/json"
    cp -R "$work_dir/json/include/nlohmann" "$project_dir/vendor/json/"
fi
python3 "$project_dir/scripts/apply-gem5.py" --gem5 "$work_dir/gem5"
printf 'Sources ready. gem5 directory: %s\n' "$work_dir/gem5"

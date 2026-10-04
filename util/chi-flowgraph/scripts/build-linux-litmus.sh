#!/usr/bin/env bash
# Official, pinned generators and model checker; no hand-written replacement tests.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
task_root="${CHI_LINUX_VALIDATION_ROOT:-$HOME/chi-flowgraph-work/linux-validation}"
herd_commit=37caee17e651ac2a46257973494e9037f219aa4f
archive_sha=4185eadbbb9da441548e76768785b9326dccd4524f645c6ea4a838ecc995b54c
source_dir="$task_root/sources/herdtools7-7.56"
prefix_dir="$task_root/herdtools7-7.56"
output_dir="${CHI_LINUX_LITMUS_OUT:-$project_dir/build/linux-litmus-large}"
matrix_profile="${CHI_LINUX_LITMUS_PROFILE:-large}"
jobs="${CHI_HERD_JOBS:-2}"
case "$jobs" in 1|2) ;; *) echo 'Use CHI_HERD_JOBS=1 or 2 alongside the kernel build' >&2; exit 2;; esac
for tool in git ocamlc dune menhir ocamlfind python3 riscv64-linux-gnu-gcc; do
    command -v "$tool" >/dev/null || { echo "Missing build prerequisite: $tool" >&2; exit 2; }
done
mkdir -p "$task_root/sources" "$prefix_dir/bin" "$output_dir"
if [[ ! -d "$source_dir/.git" ]]; then
    git clone --depth 1 --branch 7.56 https://github.com/herd/herdtools7.git "$source_dir"
fi
test "$(git -C "$source_dir" rev-parse HEAD)" = "$herd_commit"
git -C "$source_dir" diff --quiet
git -C "$source_dir" archive --format=tar HEAD > "$task_root/sources/herdtools7-7.56-source.tar"
printf '%s  %s\n' "$archive_sha" "$task_root/sources/herdtools7-7.56-source.tar" | sha256sum --check
(
    cd "$source_dir"
    printf 'let version = "7.56"\nlet rev = "%s"\nlet libdir = "%s/share/herdtools7/"\n' \
        "$herd_commit" "$prefix_dir" > Version.ml
    dune build --profile release -j "$jobs" herd/herd.exe litmus/litmus.exe \
        gen/diy.exe gen/diyone.exe tools/msum.exe tools/mcompare.exe
    for spec in herd/herd litmus/litmus gen/diy gen/diyone tools/msum tools/mcompare; do
        cp "_build/default/$spec.exe" "$prefix_dir/bin/$(basename "$spec")7"
    done
    for library in herd litmus; do
        mkdir -p "$prefix_dir/share/herdtools7/$library"
        cp -a "$library/libdir/." "$prefix_dir/share/herdtools7/$library/"
    done
)
python3 - "$source_dir" "$prefix_dir" "$output_dir" "$herd_commit" "$archive_sha" <<'PY'
import hashlib, json, pathlib, subprocess, sys
source, prefix, out = map(pathlib.Path, sys.argv[1:4])
versions = {}
for tool in ('herd7', 'litmus7', 'diy7', 'diyone7', 'msum7', 'mcompare7'):
    if tool in ('msum7', 'mcompare7'):
        version, method = '7.56', 'pinned source package; this utility has no -version option'
    else:
        process = subprocess.run([str(prefix / 'bin' / tool), '-version'], text=True, capture_output=True, check=True)
        version, method = (process.stdout + process.stderr).strip(), 'actual -version output'
    versions[tool] = {'version': version, 'versionEvidence': method,
                      'sha256': hashlib.sha256((prefix / 'bin' / tool).read_bytes()).hexdigest()}
manifest = {'source': 'https://github.com/herd/herdtools7', 'tag': '7.56', 'commit': sys.argv[4],
            'sourceGitArchiveSha256': sys.argv[5], 'prefix': str(prefix), 'sourceDirectory': str(source),
            'ocaml': subprocess.check_output(['ocamlc', '-version'], text=True).strip(),
            'dune': subprocess.check_output(['dune', '--version'], text=True).strip(), 'tools': versions}
(out / 'herdtools-build.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
PY
if [[ "${1:-}" == '--tools-only' ]]; then exit 0; fi
python3 "$project_dir/scripts/generate-linux-litmus.py" --tools "$prefix_dir/bin" --output "$output_dir" --profile "$matrix_profile"

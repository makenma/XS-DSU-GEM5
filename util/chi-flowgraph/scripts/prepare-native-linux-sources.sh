#!/usr/bin/env bash
# Pinned sources for the native-L2 full-system validation. Build on Linux FS.
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
work_dir="${CHI_LINUX_WORK:-$HOME/chi-flowgraph-work/linux-validation}"
mkdir -p "$work_dir/downloads" "$work_dir/sources" "$project_dir/build/linux-validation"
fetch() {
    local url="$1" target="$2"
    if [[ ! -s "$target" ]]; then
        curl --fail --location --retry 3 --connect-timeout 30 "$url" -o "$target.partial"
        mv -- "$target.partial" "$target"
    fi
}
fetch https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.6.52.tar.xz "$work_dir/downloads/linux-6.6.52.tar.xz"
fetch https://cdn.kernel.org/pub/linux/kernel/v6.x/sha256sums.asc "$work_dir/downloads/linux-sha256sums.asc"
(cd "$work_dir/downloads"; grep -E '^[0-9a-f]{64}  linux-6\.6\.52\.tar\.xz$' linux-sha256sums.asc | sha256sum -c -)
fetch https://busybox.net/downloads/busybox-1.36.1.tar.bz2 "$work_dir/downloads/busybox-1.36.1.tar.bz2"
fetch https://busybox.net/downloads/busybox-1.36.1.tar.bz2.sha256 "$work_dir/downloads/busybox-1.36.1.tar.bz2.sha256"
(cd "$work_dir/downloads"; sha256sum -c busybox-1.36.1.tar.bz2.sha256)
fetch https://github.com/riscv-software-src/opensbi/archive/refs/tags/v1.3.1.tar.gz "$work_dir/downloads/opensbi-1.3.1.tar.gz"
for spec in 'linux-6.6.52.tar.xz linux-6.6.52' 'busybox-1.36.1.tar.bz2 busybox-1.36.1' 'opensbi-1.3.1.tar.gz opensbi-1.3.1'; do
    read -r archive directory <<< "$spec"
    if [[ ! -d "$work_dir/sources/$directory" ]]; then
        python3 - "$work_dir/downloads/$archive" "$work_dir/sources" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1]) as source:
    source.extractall(sys.argv[2], filter='data')
PY
    fi
done
sha256sum "$work_dir/downloads/linux-6.6.52.tar.xz" \
  "$work_dir/downloads/busybox-1.36.1.tar.bz2" "$work_dir/downloads/opensbi-1.3.1.tar.gz" \
  > "$project_dir/build/linux-validation/source-sha256.txt"
printf 'Pinned Linux, BusyBox and OpenSBI sources ready in %s\n' "$work_dir/sources"

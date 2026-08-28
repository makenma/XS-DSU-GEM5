#!/usr/bin/env bash
#
# run_chi_16core_linux.sh - parameterized launcher for the figure-23.2 D0
# 16-core CHI full-system configuration.
#
# Three strictly separated phases:
#   checkpoint     Boot 16-core Linux and wait for a guest m5 checkpoint op.
#   restore-shell  Restore the checkpoint and expose the interactive terminal.
#   restore-redis  Restore the checkpoint and run a guest workload script.
#
# Every path is parameterized via flags or environment variables; no absolute
# host path is hard-coded into the gem5 Python config.
#
# Usage:
#   ./util/run_chi_16core_linux.sh checkpoint    [options]
#   ./util/run_chi_16core_linux.sh restore-shell [options]
#   ./util/run_chi_16core_linux.sh restore-redis [options]
#   ./util/run_chi_16core_linux.sh --help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${CONFIG:-${REPO_DIR}/configs/example/kmhv2_chi_6x4_hnf.py}"

usage() {
  cat <<'EOF'
run_chi_16core_linux.sh - launch the 16-core CHI D0 full-system config.

Usage:
  run_chi_16core_linux.sh <checkpoint|restore-shell|restore-redis> [options]
  run_chi_16core_linux.sh --help

Phases:
  checkpoint      Boot 16-core Linux and take a boot checkpoint via a guest
                  `m5 checkpoint` pseudo-op after the shell is usable.
  restore-shell   Restore from the boot checkpoint and keep the serial shell
                  attached for interactive validation.
  restore-redis   Restore from the boot checkpoint (no re-boot) and run the
                  Redis benchmark workload, then `m5 exit`.

Options (all may also be set via the shown environment variable):
  --gem5 PATH            gem5 binary            [GEM5]
  --payload PATH         OpenSBI FW_PAYLOAD image [PAYLOAD]
  --kernel PATH          Linux kernel image      [KERNEL]
  --firmware PATH        firmware/boot binary    [FIRMWARE] (RISC-V XiangShan
                         uses an internal reset vector; optional/ignored here)
  --dtb PATH             device tree blob        [DTB] (RISC-V XiangShan
                         auto-generates the DTB; optional/ignored here)
  --disk PATH            Linux root disk image   [DISK_IMAGE] (repeatable)
  --outdir PATH          gem5 output directory   [OUTDIR]
  --checkpoint-dir PATH  checkpoint directory    [CHECKPOINT_DIR]
  --mem-size SIZE        total DRAM size         [MEM_SIZE] (default 16GB)
  --mem-type TYPE        gem5 memory type        [MEM_TYPE] (default SimpleMemory)
  --num-cpus N           number of CPUs          [NUM_CPUS] (default 16)
  --max-tick TICK        simulation tick limit   [MAX_TICK] (default 100000000000)
  --restore-terminal-wait SECONDS
                         on restore, keep guest time frozen while attaching
                         the terminal and queueing initial input
                         [RESTORE_TERMINAL_WAIT] (default 0)
  --no-pf                disable cache prefetchers [NO_PF=1] (default)
  --with-pf              enable configured cache prefetchers [NO_PF=0]
  --redis-script PATH    guest Redis workload    [REDIS_SCRIPT]
  --dry-run              print the gem5 command instead of running it
  --help, -h             show this help and exit

Example:
  run_chi_16core_linux.sh checkpoint \
      --gem5 build/RISCV/gem5.opt \
      --payload /path/to/fw_payload.bin \
      --outdir m5out/chi-16core \
      --checkpoint-dir m5out/chi-16core \
      --mem-size 16GB --num-cpus 16
EOF
}

# --- defaults from environment ---
GEM5="${GEM5:-}"
PAYLOAD="${PAYLOAD:-}"
KERNEL="${KERNEL:-}"
FIRMWARE="${FIRMWARE:-}"
DTB="${DTB:-}"
DISK_IMAGE="${DISK_IMAGE:-}"
OUTDIR="${OUTDIR:-m5out/chi-16core}"
CHECKPOINT_DIR="${CHECKPOINT_DIR:-m5out/chi-16core/boot-checkpoint}"
MEM_SIZE="${MEM_SIZE:-16GB}"
MEM_TYPE="${MEM_TYPE:-SimpleMemory}"
NUM_CPUS="${NUM_CPUS:-16}"
MAX_TICK="${MAX_TICK:-100000000000}"
RESTORE_TERMINAL_WAIT="${RESTORE_TERMINAL_WAIT:-0}"
NO_PF="${NO_PF:-1}"
REDIS_SCRIPT="${REDIS_SCRIPT:-}"
DRY_RUN=0
MODE=""

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gem5)            GEM5="$2"; shift 2 ;;
    --gem5=*)          GEM5="${1#*=}"; shift ;;
    --payload)         PAYLOAD="$2"; shift 2 ;;
    --payload=*)       PAYLOAD="${1#*=}"; shift ;;
    --kernel)          KERNEL="$2"; shift 2 ;;
    --kernel=*)        KERNEL="${1#*=}"; shift ;;
    --firmware)        FIRMWARE="$2"; shift 2 ;;
    --firmware=*)      FIRMWARE="${1#*=}"; shift ;;
    --dtb)             DTB="$2"; shift 2 ;;
    --dtb=*)           DTB="${1#*=}"; shift ;;
    --disk)            DISK_IMAGE="${DISK_IMAGE:+$DISK_IMAGE,}$2"; shift 2 ;;
    --disk=*)          DISK_IMAGE="${DISK_IMAGE:+$DISK_IMAGE,}${1#*=}"; shift ;;
    --outdir)          OUTDIR="$2"; shift 2 ;;
    --outdir=*)        OUTDIR="${1#*=}"; shift ;;
    --checkpoint-dir)  CHECKPOINT_DIR="$2"; shift 2 ;;
    --checkpoint-dir=*)CHECKPOINT_DIR="${1#*=}"; shift ;;
    --mem-size)        MEM_SIZE="$2"; shift 2 ;;
    --mem-size=*)      MEM_SIZE="${1#*=}"; shift ;;
    --mem-type)        MEM_TYPE="$2"; shift 2 ;;
    --mem-type=*)      MEM_TYPE="${1#*=}"; shift ;;
    --num-cpus)        NUM_CPUS="$2"; shift 2 ;;
    --num-cpus=*)      NUM_CPUS="${1#*=}"; shift ;;
    --max-tick)        MAX_TICK="$2"; shift 2 ;;
    --max-tick=*)      MAX_TICK="${1#*=}"; shift ;;
    --restore-terminal-wait)
                       RESTORE_TERMINAL_WAIT="$2"; shift 2 ;;
    --restore-terminal-wait=*)
                       RESTORE_TERMINAL_WAIT="${1#*=}"; shift ;;
    --no-pf)           NO_PF=1; shift ;;
    --with-pf)         NO_PF=0; shift ;;
    --redis-script)    REDIS_SCRIPT="$2"; shift 2 ;;
    --redis-script=*)  REDIS_SCRIPT="${1#*=}"; shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage; exit 0 ;;
    --)                shift; break ;;
    -*)                echo "Error: unknown option '$1'." >&2; usage >&2; exit 2 ;;
    *)                 if [[ -z "$MODE" ]]; then MODE="$1"; else
                         echo "Error: unexpected argument '$1'." >&2; exit 2
                       fi; shift ;;
  esac
done

if [[ -z "$MODE" ]]; then
  echo "Error: phase (checkpoint|restore-shell|restore-redis) is required." >&2
  usage >&2
  exit 2
fi
case "$MODE" in
  checkpoint|restore-shell|restore-redis) ;;
  *) echo "Error: invalid phase '$MODE'." >&2
     usage >&2; exit 2 ;;
esac

# --- build the gem5 command ---
GEM5_BIN="${GEM5:-${GEM5_BIN:-${REPO_DIR}/build/RISCV/gem5.opt}}"

cmd=( "$GEM5_BIN" --listener-mode=on --redirect-stdout --redirect-stderr
      --outdir="$OUTDIR" "$CONFIG"
      --raw-cpt --generic-rv-cpt="$PAYLOAD"
      --num-cpus="$NUM_CPUS" --mem-size="$MEM_SIZE"
      --mem-type="$MEM_TYPE" --maxinsts=0 -m "$MAX_TICK" )
if [[ "$NO_PF" == 1 ]]; then
  cmd+=( --no-pf )
fi
case "$MODE" in
  checkpoint)
    # Boot Linux and take a boot checkpoint from a guest `m5 checkpoint`
    # pseudo-op after the shell is usable.  --max-checkpoints 1 stops after
    # the first guest request.
    cmd+=( --max-checkpoints=1 --checkpoint-dir="$CHECKPOINT_DIR" )
    [[ -n "$KERNEL" ]] && cmd+=( --kernel="$KERNEL" )
    # --disk-image is repeatable in gem5; split the comma-separated list.
    if [[ -n "$DISK_IMAGE" ]]; then
      IFS=',' read -ra _disks <<<"$DISK_IMAGE"
      for d in "${_disks[@]}"; do cmd+=( --disk-image="$d" ); done
    fi
    [[ -n "$REDIS_SCRIPT" ]] && cmd+=( --script="$REDIS_SCRIPT" )
    ;;
  restore-shell|restore-redis)
    # Restore from the boot checkpoint (no re-boot) and run the Redis workload.
    cmd+=( -r 1 --checkpoint-dir="$CHECKPOINT_DIR" )
    if [[ "$RESTORE_TERMINAL_WAIT" != 0 ]]; then
      cmd+=( --restore-terminal-wait="$RESTORE_TERMINAL_WAIT" )
    fi
    [[ -n "$KERNEL" ]] && cmd+=( --kernel="$KERNEL" )
    if [[ -n "$DISK_IMAGE" ]]; then
      IFS=',' read -ra _disks <<<"$DISK_IMAGE"
      for d in "${_disks[@]}"; do cmd+=( --disk-image="$d" ); done
    fi
    if [[ "$MODE" == "restore-redis" ]]; then
      [[ -n "$REDIS_SCRIPT" ]] || {
        echo "Error: --redis-script is required for restore-redis." >&2
        exit 2
      }
      cmd+=( --script="$REDIS_SCRIPT" )
    fi
    ;;
esac

# --- dry-run / artifact check / execute ---
printf 'gem5 command: '
printf '%q ' "${cmd[@]}"
printf '\n'

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[dry-run] not executing."
  exit 0
fi

if [[ -z "$GEM5" && ! -x "$GEM5_BIN" ]]; then
  echo "Error: gem5 binary not found/executable: $GEM5_BIN" >&2
  echo "       Build it first: scons build/RISCV/gem5.opt -j\$(nproc)" >&2
  echo "       or pass --gem5 PATH, or set GEM5=PATH." >&2
  exit 1
fi
if [[ -z "$PAYLOAD" || ! -f "$PAYLOAD" ]]; then
  echo "Error: --payload must name an OpenSBI FW_PAYLOAD image: $PAYLOAD" >&2
  exit 1
fi

if [[ "$MODE" == "checkpoint" ]]; then
  mkdir -p "$CHECKPOINT_DIR"
fi

echo "[launch] executing phase: $MODE"
exec "${cmd[@]}"

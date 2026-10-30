# Warm native L2 checkpoints

`chi_mesh_native_l2_linux.py` supports native gem5 save/restore of the merged
two-core, 2x2 CHI mesh. A snapshot retains the warm cache hierarchy, including
dirty data that still belongs to a classic L1. It is not a GCPT snapshot.

Before serialization, the native L2 drains requests, responses, MSHRs, upper
cache probes, snoops, adapter transaction mappings and CHI flit/credit queues.
Transient packets and callbacks are completed rather than serialized. The
saved state includes:

| Object | Retained state |
| --- | --- |
| Native L2 | Tags, 64-byte data, validity, permissions, dirty/upper ownership, replacement ages, transaction counters, protocol credits and LR/SC guard deadlines |
| Classic L1 and walker caches | Tags, data, coherence bits, physical set/way, LRU order, readiness, access metadata, LR/SC locks and timing bookkeeping |
| Existing mesh and system | CPU/ISA/TLB/PMP state, memory, HNF/SLC/SF state, router arbitration and devices through their existing serializers |

The classic cache parameter `checkpoint_preserve=True` enables this mode for
the eight L1/walker caches in this configuration. Checkpoint creation skips
their normal functional writeback. Explicit writebacks and other configurations
retain their existing behavior. The warm classic path currently accepts plain
set-associative tags with LRU, `cache_level=0`, and no prefetcher, compressor,
write allocator or way-prediction engine. Unsupported combinations fail at
initialization.

Restore requires the same cache geometry, CPU/fabric clock and home mapping.
Native L2 and warm classic cache state are versioned. Old snapshots without
these fields, including the former cold-only native L2 profile, cannot be
restored as warm snapshots. The existing RNF snapshot format is unchanged.

## Save and restore Linux

Prepare firmware as described in [Linux_Mesh.md](Linux_Mesh.md), then build:

```sh
scons build/RISCV/gem5.opt -j32
```

Cold boot to the actual getty prompt and save a warm checkpoint:

```sh
build/RISCV/gem5.opt --listener-mode=off --remote-gdb-port=0 \
  -d m5out/native-l2-save configs/example/chi_mesh_native_l2_linux.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf --clock 100MHz
```

The configuration exits with `status=login_checkpoint`. The default snapshot
directory is `m5out/native-l2-save/cpt.login`; `--checkpoint-dir PATH` overrides
it. Existing snapshots are never overwritten. A guest `m5_checkpoint` also
creates a snapshot and continues; `--stop-at-checkpoint` stops after saving.

Restore that snapshot, log in as root over localhost serial and check both CPUs:

```sh
python3 tests/gem5/chi/login_native_l2_linux.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf --clock 100MHz \
  --restore m5out/native-l2-save/cpt.login --out m5out/native-l2-restored
```

The checkpoint already contains the printed login prompt, so the driver sends
`root` when serial connects. It requires a real root shell, UID 0, CPUs `0-1`
online, the expected kernel version and a zero simulator exit status. The
configuration itself defaults to 1 GHz; 100 MHz reduces host time spent in
guest timer waits for functional validation. Use the same clock on both runs.

## Regression checks

```sh
python3 tests/gem5/chi/run_native_l2_checkpoint.py \
  --out m5out/native-l2-checkpoint-tests
python3 tests/gem5/chi/restore_native_l2_linux.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf \
  --out m5out/native-l2-linux-checkpoint
```

The directed runner saves and continues real guest programs, then independently
restores the same files. It checks two-hart dirty/coherent memory, LR/SC with
both L1 hits and L2 misses, and live Sv39/PMP/`instret` state. Each restored run
must finish at the same absolute tick as its original continuation. It audits
nonempty warm/dirty cache state, live CPU/L1 reservations and, in the miss case,
the L2 guard. Geometry mismatch and missing L2 state must fail explicitly.

The Linux runner saves at getty, audits the hierarchy, restores and logs in.
Both runners verify source snapshot hashes remain unchanged and record binary
hashes. Pass `--checkpoint PATH` to the Linux runner to check an existing
login snapshot without repeating the cold boot. Large firmware and snapshot
files stay in ignored `m5out` directories.

## 2026-10-30 acceptance

All four directed cases passed at 1 GHz, with identical continuation/restore
finish ticks. The original RNF native/legacy LR/SC regression also passed.
The 100 MHz Linux run saved at tick `3256000430000`; restore started at that
same tick and completed actual root shell commands at `3551000430000`:

```text
CHI_MERGE_ONLINE=0-1
CHI_MERGE_KERNEL=6.10.7-g0dc336bf6c07
CHI_MERGE_UID=0
CHI_MERGE_LOGIN_DONE
```

The snapshot retained 6,467 valid native L2 lines, including 2,628 dirty
lines, plus 1,770 valid L1/walker lines including 777 dirty lines. Every
source snapshot file hash remained unchanged after both restores.
The final build, style hook and whitespace check passed.

[Validation summary](validation/native-l2-checkpoint-20261030/validation.json),
[directed results](validation/native-l2-checkpoint-20261030/directed.json),
[Linux save/restore](validation/native-l2-checkpoint-20261030/linux-save-and-restore.json)
and [final Linux restore](validation/native-l2-checkpoint-20261030/linux-final-restore.json)
retain the evidence. See the validation directory's
[README](validation/native-l2-checkpoint-20261030/README.md) for binary hashes
and console normalization details.

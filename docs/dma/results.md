# Executed DMA baseline results

Validated on 2026-09-30 in
`/nfs/home/majunhong/project/chiron/XS-DSU-GEM5`.
The CPU executes the guest instructions; both MMIO and DMA payload traverse
the repository's CHI adapters/network. All successful CPU cases check every
source, destination and guard byte and require DMA/HNI/RNI/RNF-uncached/HNF
quiescence. This is a software timing baseline, not RTL cycle accuracy.

## Commands and evidence

```sh
cd /nfs/home/majunhong/project/chiron/XS-DSU-GEM5
python3 tests/gem5/dma/run.py --smoke
python3 tests/gem5/dma/run.py
```

The runner builds `build/RISCV/gem5.opt` with
`scons --ignore-style --linker=gold -j32`, builds the guest using
`riscv64-linux-gnu-gcc`, and executes the HNF unit binary for a full matrix.

The actual full run was followed by this focused repair/validation run:

```sh
python3 tests/gem5/dma/run.py \
  --cases '^(repeat-stalls|smoke64|directed-rni|dma-error)$'
```

The pressure case now uses 8 HNF entries and 2 RNI entries. The existing CPU
cache path stalled during startup with 2/4 HNF entries. The factory rejects
such CPU configurations explicitly; CPU-free directed integration still uses
2 HNF entries. The focused run also checked the final RNI response invariants.
The runner preserves other cases' actual evidence when a subset is rerun.

Canonical artifacts:

* [37-case summary](../../results/dma-baseline/regression/summary.json)
* [Benchmark CSV](../../results/dma-baseline/regression/benchmark.csv)
* [Saved-run verification](../../results/dma-baseline/regression/verification.json)
* [Full execution log](../../results/dma-baseline/full-regression-fixed.log)
* [Successful repair execution log](../../results/dma-baseline/repair-regression.log)
* [77-test HNF unit log](../../results/dma-baseline/regression/hnf-unit.log)

Each case has its actual `command.json`, `run.log`, configuration, stats and
result JSON; selected small cases also have `path.trace`. Earlier experimental
and failed-run directories are retained separately from the canonical matrix.

## Coverage

**77 HNF unit tests passed. All 37 matrix cases are verified: 34 positive/
directed cases passed, and 3 negative cases failed as required.**

| Coverage | Executed cases |
|---|---|
| First CPU closure | 64 B TimingSimpleCPU; separate Xiangshan O3 64 B case |
| Length × concurrency | 64 B, 4096 B, 1048576 B, each with p=1 and p=4 |
| Boundaries | 1, 3, 31, 33, 63, 65, 127, 129 B, each with p=1/4; source offset 3 and destination offset 29 |
| Reuse / busy / errors | repeated tasks, snapshots, busy START, ignored CLEAR while busy, zero length, overlap, overflow, address rejection, halves, DMA error completion |
| Directed memory | 256/130/64 B original Packets, masked writes, entry exhaustion, rejected responses, out-of-order original responses and DAT beats |
| Directed PIO | 10 exact PIO operations, rejected requests, width/hole errors, masked START; exactly one actual zero-length job |
| HNI link credits | 256 SCRATCH reads, held receiver, exhausted response credits, recovery and global drain |
| CPU pressure | three 129 B tasks, four private chunks, two RNI entries, PIO/DMA response stalls, reversed DMA DAT beats |
| Benchmarks | dma_copy, separate SCRATCH read and write/readback loops, 16-task continuous mixed loops |
| Negative paths | HNI cut, RNI cut with bounded guest timeout, unimplemented RTL backend |

The guest additionally forces compressed instructions in both halves of a
fetch word and a straddling 32-bit instruction on both CPU models.

## Simulated performance

TimingSimpleCPU and all nodes run at 1 GHz. `chunk_bytes=256` in this table;
one task per row. Effective bytes are LENGTH, not read+write traffic.
Initialization and final byte checking are excluded from the START-to-DONE
interval. The interval includes CPU command/poll/fence overhead and the
RNF/mesh/HNI/PIO/DMA/RNI/HNF/SLC/memory path.

| Length (B) | parallel_chunks | Measured cycles / ns | Effective MB/s (10^6 B/s) |
|---:|---:|---:|---:|
| 64 | 1 | 338 | 189.35 |
| 64 | 4 | 338 | 189.35 |
| 4096 | 1 | 4375 | 936.23 |
| 4096 | 4 | 3194 | 1282.40 |
| 1048576 | 1 | 1842480 | 569.11 |
| 1048576 | 4 | 1147404 | 913.87 |

64 B fits one chunk, so the p=4 case also has only one active chunk.
The 1 MiB p=4 run actually reaches four active chunks.

* SCRATCH read: 43.00 ns per access, 23.26 million accesses/s.
* SCRATCH write/fence/readback: 108.94 ns per sequence, 18.36 million register
  accesses/s counting both accesses.
* Mixed 64 B × 16 tasks: 331.06 ns mean loop time per task,
  approximately 3.02 million tasks/s and 193.32 MB/s (both p settings).
  Warmup and final verification are outside the measured loop.

These are system/software-adapter measurements. They are not independent
HNI/RNI RTL performance scores and do not use host wall time.

## Path and resource evidence

64 B smoke: RNF MMIO injection = HNI reception = accepted PIO requests =
actual device accesses = **7**. The device writes **64 B**; RNI accepts
**4 original Packets** and injects **6 CHI REQs**. Original read/write payload
is 64 B each; full-line read/RMW traffic is 256 B and full-line write traffic
128 B. Both adapters' terminal entries/outstanding are zero.

| 1 MiB counter | p=1 | p=4 |
|---|---:|---:|
| Original DMA Packets | 40960 | 40960 |
| Injected CHI REQs, including reissues | 57347 | 71242 |
| Original read bytes / write bytes | 1048576 / 1048576 | 1048576 / 1048576 |
| CHI read bytes / write bytes | 1835008 / 1310720 | 1835008 / 1310720 |
| Parent entry high water | 5 | 8 |
| Actual CHI outstanding high water | 4 | 4 |
| Active chunk high water | 1 | 4 |
| Packet request rejection | 0 | 40952 |
| CHI RetryAck | 8195 | 22090 |
| STATUS polls | 42756 | 26940 |

Directed RNI completes all 48 original Packets, recovers from 42 request
rejections and 48 response rejections, records 8 original-response order
inversions and 88 reversed terminal DAT beats. CPU pressure records 33 RNI
request rejections, 42 response rejections, 5 CHI retries and 42 reversed DAT
beats while completing all three copies and draining.

The direct credit unit records **49611 HNI credit stalls**, then completes
exactly 256 reads. Its parent high water is 256 but actual injected CHI high
water is 13; these are deliberately separate measurements. This unit isolates
the HNI link. All CPU cases and directed memory/PIO integration use the mesh.

Cut-HNI: RNF injects one MMIO REQ, HNI/device see zero, and simulation reaches
the bounded limit without PASS. Cut-RNI: CPU performs nine actual MMIO actions,
RNI injects two DMA REQs into the sink, and the guest reports its bounded
timeout without PASS. RTL selection raises NotImplementedError.

## Implemented files

| Area | Added or updated files for this delivery |
|---|---|
| Device and shared ABI | `src/dev/SimpleDma.py`, `simple_dma.hh`, `simple_dma.cc`, `simple_dma_regs.h` |
| Adapters | `src/mem/cache/CHI/HniIoAdapter.{py,hh,cc}`, `RniDmaAdapter.{py,hh,cc}`, `DmaPathStats.hh` |
| Timing stimuli | `src/mem/cache/CHI/DmaPathTester.{py,hh,cc}`, `DmaTimingStall.{py,hh,cc}` |
| Wiring / guest / runner | `configs/example/chi_dma.py`, `tests/test-progs/simple_dma/{simple_dma.c,Makefile}`, `tests/gem5/dma/{run.py,directed.py}` |
| DmaPort error notification | `src/dev/dma_device.{hh,cc}`; optional callback preserves existing callers |
| Existing bridge fixes | `Cache2ChiBridge.{hh,cc}` request retry discipline; `Chi2ClassicMemBridge.cc` saves fields before popping their storage |
| HNF completion / tests | `HnfCoherencyController.{hh,cc,test.cc}`, `HomeNodeFull.{py,cc}`; default-off committed-write completion and test hooks |
| RV64 SE execution | `src/arch/riscv/{decoder.hh,decoder.cc,tlb.cc,process.cc}`; aligned fetch, mapping flags, simulated counter delegation |
| Build / documentation | `src/dev/SConscript`, `src/mem/cache/CHI/SConscript`, narrow `.gitignore` exceptions, `docs/dma/{gate0.md,README.md,results.md}` |

Existing user changes, including co-simulation changes, remain uncommitted.
No commit or push was performed.

## Limits and next RTL work

The supported workload is RV64GC SE with exclusively owned uncacheable DMA
buffers and physical addresses from the explicit mapping. Ordinary CPU caches
remain enabled. Cacheable DMA aliases/destinations are unsupported; an actual
cacheable experiment failed visibility checks. Memory-only partial RMW is
serialized within an adapter and relies on exclusive buffer ownership.

DONE means the modeled SLC/coherent-memory completion point, not that every
write has reached DRAM. CPU runs verify quiescence after a bounded network
tail; CPU-free tests also run global drain. Full checkpoint/restore is explicitly
unimplemented, and this fork's O3 drain after SE thread exit does not converge.
O3 is verified for the separate 64 B case; RV32, full-system boot, malformed-flit
fuzzing and long-duration soak were not run.

RTL execution was not run and is not implemented. The remaining replacements
need Packet/AXI BFMs, AXI byte-strobe/ID/channel handling, CHI wire geometry and
ID translation, clock/reset/time advancement, credit/error/poison behavior,
completion ordering and drain. The same SimpleDma, register header and guest
remain the interface boundary. See [README.md](README.md) for both replacement
directions and the existing SN-side co-simulation reference.

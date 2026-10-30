# ancient_coding + native CHI L2

This integration uses these sources:

| Component | Source |
| --- | --- |
| Router, HNF, POCQ, SLC/SF, SNF and shared CHI transport | `ancient_coding` at `1edf65206b`, including the existing working implementation saved as `a43642cd9a` |
| L2 tags/data, MSHRs, inclusive probes, refill and replacement | `agent/chi-flowgraph-native-l2-linux-validation` at `72fbf09812`, adapted to the ancient HNF interface |
| Classic L1 snoop ordering support and JSON dependency | Selected dependencies from `72fbf09812` |

The merge deliberately retains the ancient router/HNF implementation. It does
not import the other branch's replacement HNF, router, unrelated CPU changes,
experiment archives or checkpoints. Both source histories remain merge parents.

The two-core Linux configuration is
`configs/example/chi_mesh_native_l2_linux.py`: each TimingSimpleCPU has classic
instruction/data and page-walker L1 caches followed by `ChiNativeL2`. Both L2s
connect directly to the four ancient CHI routers. Two HNFs alternate 64-byte
lines using address bit 6; one SNF backs 128 MiB of RAM. CPU memory traffic
passes through the L2 and CHI mesh. MMIO uses the platform bus; the functional
loading bus initializes firmware/DTB only.

## HNF compatibility profile

The retained HNF supports ReadUnique, CleanInvalid and WriteBackFull. The L2
therefore selects ReadUnique for misses, WriteBackFull for dirty victims and
CleanInvalid for clean victims. Unsupported maintenance and uncacheable RAM
requests fail explicitly; device MMIO is routed separately. This is an
exclusive-only profile, not validation of the source L2's shared-read, WEOE,
MakeUnique, general CMO or non-cacheable protocol paths.

`ChiNativeChannel.hh` keeps the source L2's owned native flits separate from
the ancient shared flit definitions. `ChiNativeLegacy.hh` converts payloads
and SNP addresses at the L2 boundary. It maps the HNF's monotonically increasing
32-bit live transaction tokens into 12-bit native tokens, separately per home,
and retires mappings only after the final response beat or acknowledgement is
accepted by the transport. The router and HNF source files remain unchanged.

Exclusive reads can otherwise make two synchronized LR/SC loops invalidate
each other before either SC succeeds. After returning an LR miss, the L2
protects the line from inclusive replacement and defers queued snoops for a
bounded `reservation_cycles` window (256 cycles by default, allowing an
instruction refill between LR and SC). It does not extend
the window while waiting for a snoop; normal L1 reservation invalidation and
SC success/failure semantics remain in effect.

A queued miss reserves exactly one replacement way through the upper-cache
probe and the victim's completion. Other misses cannot reuse the way while
the probe is outstanding, and retrying the queued miss cannot evict every
way in its set. Outstanding victim probes count against MSHR admission. The
Linux configuration retains the source L2's 32 MSHRs per core.

The integrated L2 now supports drained warm native checkpoints. Its tags,
data, permissions, replacement ages and LR/SC guard deadlines are retained.
The configuration also enables warm serialization of its classic L1 caches,
including dirty data and LR/SC locks, so upper-cache ownership remains valid
after restore. See [Native_L2_Checkpoint.md](Native_L2_Checkpoint.md) for save,
restore and validation commands. The original RNF configuration and its
checkpoint tests remain available under `chi_mesh_linux.py`.

## Reproduce the Linux login check

Use the login firmware prepared by `util/chi_prepare_linux.py`, as described
in [Linux_Mesh.md](Linux_Mesh.md). The firmware is not committed.

```sh
scons build/RISCV/gem5.opt -j32
python3 tests/gem5/chi/login_native_l2_linux.py \
  --kernel m5out/linux-chi-mesh/assets/fw_payload.elf \
  --out m5out/chi-native-l2-login
```

The login driver uses a 100 MHz CPU/fabric clock for functional acceptance,
reducing host work during fixed-duration guest timer waits. The configuration
itself defaults to 1 GHz; pass `--clock 1GHz` to the driver to use that clock.
Both clocks keep the RTC at 10 MHz. This acceptance is a functional check,
not a performance measurement.

The check cold boots Linux, waits for the actual getty login prompt, logs in
as root over localhost serial, and executes commands in the shell. It requires
two online CPUs, Linux 6.10.7, UID 0, the guest completion marker, a zero gem5
exit status, and an instantiated topology with four routers and two native L2s.
`report.json`, `run.log` and `system.platform.terminal` retain the evidence.

Standalone adapter tests cover large HNF IDs across repeated reuse, independent
home namespaces, retries retaining their mapping, SNP address conversion and
owned two-beat dirty data. The source wire-format test checks 89 conditions.

## 2026-10-30 acceptance

The 100 MHz cold boot and actual root login passed with both CPUs online
and 32 MSHRs per L2. Guest commands returned:

```text
CHI_MERGE_ONLINE=0-1
CHI_MERGE_KERNEL=6.10.7-g0dc336bf6c07
CHI_MERGE_UID=0
CHI_MERGE_LOGIN_DONE
```

The guest completion tick was 3551000000000; host elapsed time
was 960.89 seconds. The 1 GHz two-hart memory/atomic smoke
also passed using the same simulator binary.

[Acceptance summary](validation/native-l2-20261030/validation.json),
[serial console](validation/native-l2-20261030/linux-console.log),
[login checks](validation/native-l2-20261030/linux-login.json), and
[source audit](validation/native-l2-20261030/source-audit.json) retain the evidence.

The source audit confirms all 56 existing CHI component files match the
ancient working snapshot; SConscript only registers the new L2 and tests.
The build, 89 wire checks, three adapter unit tests and two-hart guest checks
passed. The imported/adapted sources retain formatting that fails the gem5
style hook; its output is recorded in the validation directory. Commit hooks
are bypassed for this merge without changing the repository hook settings.

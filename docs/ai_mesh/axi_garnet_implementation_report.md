# AXI Garnet MVP Implementation Report

This file records implementation gates for
`src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md`.  It is an execution record, not a
replacement for the specification.

## Provenance

- Specification base: `7478835ac25d406941490578874d5022e3a46ec5`
- User-approved working baseline: `65f3241a6cd486dffddaca251536f5c691af9a1f`
- Branch: `feature/ai-mesh-axi-garnet`
- Protocol-under-development: `AXI_MESH`
- AXI_MESH target ISA: `null`

## Gate 0: baseline and technical probes

### Baseline deviation authorized by Decision 1

The first clean `Garnet_standalone` build did not compile at the approved
baseline.  Existing Xiangshan CPU/cache dependencies leaked into a null-ISA
build: the O3 `SMTQueuePolicy` generated type, O3-only `TagReadFail` debug
registration, `BaseCPU`/LSQ definitions, the generic architecture database,
and CPU difftest golden memory were referenced by network-only code.  The
stock Garnet traffic script also selected optional DRAMsim3 by the repository
default although that model is not built into `Garnet_standalone`.

The user selected Decision 1: repair only these prerequisites because the AI
Mesh configuration has no CPU.  The repairs therefore guard CPU-only runtime
hooks when `IS_NULL_ISA`, move shared registrations/objects out of CPU-only
SCons paths, use forward declarations where possible, and give only the stock
Garnet synthetic-traffic script a built-in DDR3 default.  No Garnet datapath,
router, NI, link, VC, credit, or packetization source was changed while the
legacy golden was captured.

### Builds and command-line probes

| Command | Result |
|---|---|
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| `build/Garnet_standalone/gem5.opt --help` | PASS, exit 0 |
| `build/Garnet_standalone/gem5.opt configs/example/garnet_synth_traffic.py --help` | PASS, exit 0 |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |

### Legacy Garnet golden

The three commands from specification section 15.4 completed with exit 0 and
`Network Tester completed simCycles`.  The versioned machine-readable record
is `tests/gem5/axi_garnet/golden/garnet_xs_dev_7478835a.json`.  Integer and
count fields are exact-match; printed floating fields use absolute tolerance
`1e-12`.

| Case | Tick | Injected/received packets by vnet | Injected/received flits by vnet | Avg packet latency | Avg flit latency | Avg hops |
|---|---:|---|---|---:|---:|---:|
| `legacy_vnet0` | 5000328 | `[100,0,0]` | `[100,0,0]` | 2997.0 | 2997.0 | 2.0 |
| `legacy_vnet1` | 5000328 | `[0,100,0]` | `[0,100,0]` | 2997.0 | 2997.0 | 2.0 |
| `legacy_vnet2` | 5000328 | `[0,0,100]` | `[0,0,500]` | 6040.62 | 4681.314 | 2.0 |

The captured configuration is four routers in a 2x2 `Mesh_XY`, three vnets,
XY routing (`routing_algorithm=1`), 16-byte NI/link width, four VCs per vnet,
legacy control/data VC depths 1/4, one-cycle links, and
`DDR3_1600_8x8`.

### Existing GTest path probe

The existing target was built and run as:

```text
scons build/Garnet_standalone/mem/translation_gen.test.opt -j4
build/Garnet_standalone/mem/translation_gen.test.opt --gtest_color=no
```

Result: PASS, exit 0, 9 tests run and 9 passed.  This confirms the path rules:

- independent binary:
  `build/<variant>/mem/translation_gen.test.opt`;
- aggregate XML target:
  `build/<variant>/unittests.opt/mem/translation_gen.test.xml`;
- generated protocol header:
  `build/<variant>/mem/ruby/protocol/RequestMsg.hh`.

`unittests.opt` was not executed as a binary.

### Queue ownership and wakeup probe

Read-only inspection established the Commit 1 implementation contract:

- `MessageBuffer::setConsumer()` rejects a second consumer;
- each incoming network buffer remains owned by its generated SLICC
  controller;
- each finite local-delivery buffer is owned by its C++ adapter;
- SLICC checks
  `MessageBuffer.areNSlotsAvailable(1, clockEdge())`, enqueues the local copy
  with a one-controller-cycle delay, and only then dequeues the network input;
- when the adapter dequeues a local message, that local buffer's dequeue
  callback schedules the existing shim controller for the following cycle.
  This wakes a shim stalled on a full local queue without polling and without
  modifying the SLICC generator or adding a second consumer.

Commit 1 must prove this with independent AW-like request-direction and B-like
response-direction raw messages before any legal AXI transaction is claimed.

### CHI runtime smoke

`conditional-not-applicable`.  The following existing, image-free candidate
was tested after the mandatory build:

```text
timeout 120s build/RISCV_CHI/gem5.opt \
  -d m5out/chi-baseline-smoke \
  configs/example/ruby_random_test.py \
  --maxloads=100 --mem-type=DDR3_1600_8x8 \
  --num-cpus=1 --num-dirs=1 --num-l3caches=1
```

It exited 1 during Python configuration, before instantiate or simulation:
the repository's default `XSCompositePrefetcher` accesses `cpu.mmu`, while
this existing script supplies a `RubyTester` rather than a `BaseCPU` to the
CHI factory.  No other existing no-image CHI runtime entry point was found.
Per section 15.1, the runtime command is therefore not made mandatory; the
successful `RISCV_CHI` compile remains mandatory.  CHI sources and behavior
were not changed to manufacture a runtime smoke.

## Commit 1 gate: isolated AXI_MESH scaffold

### Build and construction boundary

`build_opts/AXI_MESH` selects `TARGET_ISA='null'` and
`PROTOCOL='AXI_MESH'`.  The protocol has separate initiator and target SLICC
machines, the five directional network queues, finite non-network
local-delivery queues, C++ endpoint shells, a CPU-less configuration script,
and an `AxiMeshDie` topology with explicit controller-to-router mapping.

This fork defines `MachineType` as a closed enum in the common SLICC exports,
so the two machine types (`AxiInitiator` and `AxiTarget`) must be declared
there.  All message/controller generation remains protocol-selected: the
generated `AxiMeshMsg.hh` exists under `build/AXI_MESH` and is absent from the
`Garnet_standalone` and `RISCV_CHI` generated protocol directories.  The C++
endpoint SConscript also returns before declaring any AXI source unless the
selected protocol is exactly `AXI_MESH`.

The protocol factory returns no CPU sequencer and no directory controller.
The common Ruby construction path was therefore made to call
`setup_memory_controllers()` only when its protocol factory returned at least
one real directory.  This prevents AXI_MESH from creating a fake directory or
DRAM controller, while existing protocols retain their previous path.

### Independent raw queue-ownership probe

The Commit 1 probe deliberately sends exactly two independent messages, not
an AXI write transaction:

- the initiator adapter constructs one AW-like message for the target;
- the target adapter independently constructs one B-like message for the
  initiator; it does not derive that message from, or use it to acknowledge,
  the AW-like message.

Both corresponding local-delivery buffers were configured with depth one.
With `AxiGarnetProbe` tracing enabled, both C++ enqueue operations occurred at
tick 333, both remote adapters first observed their local-delivery message at
tick 3996, both held the full local queue until tick 6660, and both then
drained exactly once.  The simulation exited at tick 6993 with:

```text
AXI_MESH raw shim ownership probe passed: independent messages=2
(AW-like=1, B-like=1), single-consumer local delivery preserved
AXI_MESH_RAW_SHIM_PROBE_PASS
```

The target's AW drain is reported to the initiator through a Commit 1-only
observer pointer solely to join the two probe completion conditions.  It does
not construct, mutate, or order the independent B-like network message.  No
AXI checker or legal AW/W/B state machine is exercised at this gate.

The final debug probe command was:

```text
timeout 120s build/AXI_MESH/gem5.debug --debug-flags=AxiGarnetProbe \
  -d m5out/axi-commit1-raw-probe-trace \
  configs/example/axi_garnet_test.py \
  --axi-raw-shim-probe \
  --axi-scenario=configs/example/axi_garnet_scenarios/smoke.json \
  --axi-local-delivery-depths=1,4,1,4,4 \
  --axi-message-buffer-depths=4,4,4,4,4 \
  --axi-max-sim-ticks=100000
```

The same probe without the debug flag also passed with
`build/AXI_MESH/gem5.opt`.

### Commit 1 mandatory results

| Command | Result |
|---|---|
| `scons build/AXI_MESH/gem5.debug -j4` | PASS, exit 0 |
| `scons build/AXI_MESH/gem5.opt -j4` | PASS, exit 0 |
| debug independent AW-like/B-like probe | PASS, exit 0, tick 6993 |
| opt independent AW-like/B-like probe | PASS, exit 0, tick 6993 |
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |
| specification section 15.5 Garnet AXI-leak scan | PASS, no output |

The only build warnings were the existing optional PNG/HDF5/backtrace
availability warnings and existing generated-SLICC ignored-return warnings.

## Commit 2 gate: per-vnet class and VC depth

### Normalization and compatibility

`GarnetNetwork` now accepts optional `vnet_classes` and
`buffers_per_vnet` vectors and normalizes both to arrays with one entry per
virtual network.  Empty vectors retain the stock classification rule
(`response` is data; every other type is control) and the stock 4/1
data/control depths.  Non-empty vectors override those legacy inputs.  The
AXI_MESH configuration defaults to classes
`ctrl,data,ctrl,ctrl,data` and depths `4,8,4,4,8`.

Input VC storage and all NI/router output-credit state now obtain their depth
from the same `getBuffersPerVnet()` result.  Input consumption checks capacity
before removing a link flit, and credit overflow/underflow and buffer overflow
use contextual panic diagnostics.  Router total-VC validation occurs in the
member initializer so `SwitchAllocator` observes the validated value during
its own construction.  The stock FaultModel compatibility getters are kept;
non-uniform depths within one class are rejected only when that FaultModel is
enabled.

The production normalization helper also rejects zero vnets, zero VCs, zero
flit width, vector length mismatches, zero/oversized depth, invalid class,
VC-count overflow, and FaultModel-inexpressible class depths.  NI setup keeps
its existing uniform-`consumerVcs` rule but now diagnoses zero or inconsistent
values with `fatal` rather than relying on an assertion.

### Unit and negative tests

The two protocol-neutral binaries ran with the exact required suites and test
names:

| Binary | Result |
|---|---|
| `build/AXI_MESH/mem/ruby/network/garnet/garnet_vnet_config.test.opt` | PASS, 6/6 (U9) |
| `build/AXI_MESH/mem/ruby/network/garnet/garnet_vc_isolation.test.opt` | PASS, 2/2 (U11) |

Runtime negative probes also exited 1 during construction and contained the
required stable fragments for a four-entry depth vector, class `payload`, and
zero depth at index 2, respectively:

```text
buffers_per_vnet length 4 must equal number_of_virtual_networks 5
vnet_classes[2]='payload' is invalid; expected 'ctrl' or 'data'
buffers_per_vnet[2] must be >= 1
```

### Raw-message depth/credit probe

The Commit 1 independent-message probe was repeated with one VC per vnet,
classes `ctrl,data,ctrl,ctrl,data`, and non-uniform depths `1,2,1,3,2`.
`config.ini` records those vectors exactly.  RubyNetwork tracing showed every
selected vnet-0/vnet-2 output credit decrement from 1 to 0 and subsequent
return to 1 along the two-router paths.  Both depth-one local queues remained
full until tick 6660, each message drained exactly once, and the probe passed
at tick 6993.  This remains a protocol-neutral ownership/capacity probe, not a
claim of legal AXI behavior.

### Legacy and build regression

The three section 15.4 commands were rerun character-for-character.  All
exited 0 at tick 5000328, and every versioned count and floating statistic
matched `garnet_xs_dev_7478835a.json` exactly: vnet 0/1 each transported 100
one-flit packets at latency 2997; vnet 2 transported 100 five-flit packets at
packet latency 6040.62 and flit latency 4681.314; average hops remained 2.
Their generated configs show empty new vectors and the stock 1/4 depths,
demonstrating the runtime legacy fallback.

| Command | Result |
|---|---|
| `scons build/AXI_MESH/gem5.debug -j4` | PASS, exit 0 |
| `scons build/AXI_MESH/gem5.opt -j4` | PASS, exit 0 |
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |
| specification section 15.5 Garnet AXI-leak scan | PASS, no output |
| `git diff --check` | PASS, no output |

## Commit 3 gate: protocol-neutral dynamic wire bytes

### Packetization contract

The common Ruby `Message` interface now exposes a protocol-neutral
`getWireSizeBytes()` hook.  Its default value is zero, which means that
Garnet must retain the legacy `MessageSizeType_to_int()` sizing path.  AXI
messages override the hook through the generated `AxiMeshMsg` field and
provide an explicit, nonzero on-wire byte count.

`NetworkInterface` resolves the message size once and uses that same result
for both the ceiling flit-count calculation and each flit's `msgSize`.  This
keeps packet injection, NI serialization/deserialization, and link
utilization accounting on one byte count.  The shared packetization helper
rejects negative or zero resolved sizes, zero flit width, and results that do
not fit the simulator's integer representation.  It performs integer ceiling
division without a floating-point conversion.

AXI_MESH exposes five configurable header sizes, ordered AW/W/B/AR/R, and a
data-bus width.  Its defaults are `24,16,8,24,16` header bytes and a 64-byte
data beat.  W and R wire sizes include one complete data beat; AW, B, and AR
use only their configured header size.  Semantic payload size is deliberately
not part of this calculation.

### Unit tests and runtime proof

The exact U8 binary and required test names all passed:

```text
build/AXI_MESH/mem/axi/axi_packetization.test.opt
  AxiDynamicWireBytesTest.CountsDefaultFiveChannels
  AxiDynamicWireBytesTest.FallsBackForLegacyMessage
  AxiDynamicWireBytesTest.IgnoresSemanticBytesForFlits
  AxiDynamicWireBytesTest.RejectsInvalidWireSize
[  PASSED  ] 4 tests
```

With 16-byte flits, the default AW/W/B/AR/R wire sizes are respectively
24/80/8/24/80 bytes and therefore consume 2/5/1/2/5 flits.  The legacy unit
case exercises a message whose dynamic hook returns zero, while the three
real Garnet standalone smoke tests below prove the same fallback in the
unchanged protocol path.

The independent raw-message debug probe exited 0 at tick 7326.  Its trace
shows the AW-like message carrying `WireSizeBytes=24`, Garnet reporting
`Message Size:24`, and packet flits 0 and 1 as head and tail.  The independent
B-like message carries `WireSizeBytes=8`, reports `Message Size:8`, and forms
one head-tail flit.  The B local queue was visible from tick 3996 through
6660; the two-flit AW local queue was visible from tick 4329 through 6993.
Each was drained exactly once.  `config.ini` records the five default header
sizes and the 64-byte data-bus width.

### Legacy and build regression

The three section 15.4 legacy commands were again run character-for-character
and exited 0 at tick 5000328.  Their results still match the checked-in golden
exactly: vnet 0 and vnet 1 each carried 100 one-flit packets with latency
2997; vnet 2 carried 100 five-flit packets with packet latency 6040.62 and
flit latency 4681.314; average hops remained 2.  The exact per-vnet
distributions also matched.

| Command | Result |
|---|---|
| `scons build/AXI_MESH/mem/axi/axi_packetization.test.opt -j4` | PASS, 4/4 U8 tests |
| `scons build/AXI_MESH/gem5.debug -j4` | PASS, exit 0 |
| `scons build/AXI_MESH/gem5.opt -j4` | PASS, exit 0 |
| debug dynamic-size raw probe | PASS, exit 0, tick 7326 |
| opt dynamic-size raw probe | PASS, exit 0, tick 7326 |
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |
| U8/test-helper absence from both non-AXI builds | PASS |
| specification section 15.5 Garnet AXI-leak scan | PASS; only the protocol-neutral hook is present |
| `git diff --check` | PASS, no output |

## Commit 4 gate: AXI beat messages and endpoint adapters

### CPU-less endpoint and transaction boundary

The AXI_MESH target remains a null-ISA, CPU-less system.  A dedicated
`AxiTraceTester` drives architected AW/W/AR handshakes directly into source
adapters and consumes B/R handshakes; no CPU, cache, sequencer, directory, or
DRAM controller is introduced.  The initiator adapter allocates the specified
UID, target sequence, and response sequence only when AW/AR admission succeeds.
It implements independent bounded AW/W/B/AR/R queues, ordinal-based streaming
AW/W pairing, bounded pre-AW storage, per-target injection quotas, and indexed
R reassembly.

The target adapter uses one shared bounded write-context pool for AW_ONLY,
W_ONLY, and BOUND states.  W_ONLY entries additionally consume the configured
orphan transaction/beat subquota and convert in place when AW arrives.  Static
per-source quotas are checked against each target pool before instantiate.
Address packets are revalidated at the target, and every non-DECERR request
must fit completely in one of that target's configured memory ranges.

The internal byte-addressable simple memory applies only selected WSTRB lanes
after a complete successful burst.  Reads return a full data-bus word with
zeroes outside legal narrow-transfer lanes.  Decode errors use the configured
default error target, drain a complete write or produce a complete read, do
not modify memory, and never generate EXOKAY.  Commit 5 will add the full
same-ID architectural-commit/response-retire machinery and response
obligations; this gate does not claim U5 or later behavior.

### Exact U1-U4 unit suites

Both explicitly registered binaries were rebuilt after the final target-range
defense and completed without disabled or skipped tests:

| Binary | Result |
|---|---|
| `build/AXI_MESH/mem/axi/axi_validation.test.opt` | PASS, 8/8 U1 tests |
| `build/AXI_MESH/mem/axi/axi_write_pairing.test.opt` | PASS, 19/19 U2-U4 tests |

The validation vectors cover 1/2/256-beat INCR traffic, all four supported bus
widths, narrow lane mapping, unsupported-feature DECERR routing, strict 4 KiB
and arithmetic checks, and illegal WSTRB rejection.  The pairing/context tests
cover AW-first, W-first, two pre-AW bursts, Nth-to-Nth binding, partial
streaming release, independent AW admission under W pressure, orphan merge and
subquota backpressure, shared-slot conversion, duplicate rejection, LAST/beat
validation, and indexed R reassembly.  Expected panic-path diagnostics printed
by gem5's GTest handler are assertions exercised by those passing tests.

### I0-I4 functional Garnet runs

All five runs used `build/AXI_MESH/gem5.debug`, a 200,000-tick ceiling, and the
real five-vnet Ruby/Garnet path.  The tester independently maintained a byte
shadow and checked B/R results and target memory before declaring success.

| Case | Result | Evidence |
|---|---|---|
| `endpoint_probe` | PASS, tick 27639 | write + read covered all five channels |
| `single_write` | PASS, tick 17649 | 1 AW, 1 W, 1 B; memory matched shadow |
| `burst_read` | PASS, tick 43290 | 1 AR, 16 R, one final RLAST |
| `partial_wstrb` | PASS, tick 38295 | two writes + read; 512-bit narrow/WSTRB bytes matched |
| `w_before_aw_at_target` | PASS, tick 22311 | orphan occupancy rose to 1 and drained to 0 |

The I0 trace reports message sizes 24/80/8/24/80 bytes on vnets 0/1/2/3/4.
With 16-byte flits, `stats.txt` records packet counts `[1,1,1,1,1]` and flit
counts `[2,5,1,2,5]` for both injection and reception.  Every result summary
reported `outstanding_at_exit=0`.  The current `axi_result.json` is a scoped
Commit 4 summary; the complete section 12.2 schema, trace verifier, and suite
manifest are Commit 6 deliverables.

The protocol-neutral raw ownership/depth regression was also repeated with
depth-one AW/B local delivery and passed at tick 7326.  It remains the explicit
proof that a full local-delivery queue is not dequeued by its SLICC owner;
functional I0 supplies the legal five-channel transaction proof.

### Build and legacy regression

The section 15.4 commands were rerun character-for-character.  All three
exited 0 at tick 5000328 and retained the golden totals and latencies: vnet 0
and vnet 1 each transported 100 one-flit packets at latency 2997; vnet 2
transported 100 five-flit packets at packet latency 6040.62 and flit latency
4681.314; average hops remained 2.

| Command | Result |
|---|---|
| `scons build/AXI_MESH/gem5.debug -j4` | PASS, exit 0 |
| `scons build/AXI_MESH/gem5.opt -j4` | PASS, exit 0 |
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |
| Commit 1 raw ownership regression | PASS, exit 0, tick 7326 |
| specification section 15.5 Garnet AXI-leak scan | PASS, no output |
| `git diff --check` | PASS, no output |

## Commit 5 gate: ordering, response ROB, and backpressure

### Ordering and bounded response service

AW and AR acceptance now freeze a globally unique transaction UID plus the
target-local and source-response sequence domains.  Target write and read
service may become ready out of order, but the target same-ID gate performs
architectural commit in `targetSeq` order.  The source keeps independently
bounded B-transaction and R-beat reorder storage and retires responses in the
global per-source, per-direction `responseSeq` order, including same-ID
transactions sent to different targets.  Read and write ordering domains
remain independent, and different IDs are not globally serialized.

Target service queues, ready-response queues, write/orphan assembly, source
outstanding tables, response ROBs, endpoint ingress queues, SLICC network
queues, and local-delivery queues all have explicit capacities.  Deterministic
per-UID latency/fault plans let integration tests make a younger transaction
service-ready first without changing its acceptance-time identity.  Quota
pressure and B/R ejection pressure are exposed through stable progress and
high-water counters.

During I9, simultaneous B/R local dequeues exposed a protocol-neutral Garnet
NI wakeup loss: multiple dequeue callbacks could coalesce into one scheduled
NI event, while `checkStallQueue()` intentionally released only one stalled
tail per input port per cycle.  The NI now reschedules itself one cycle later
only when it made real unstall progress and another stalled tail remains.
This preserves one-per-cycle behavior without spinning and fixes forward
progress for every protocol using the same MessageBuffer/NI path.

### Protocol-neutral drain state and stats

`GarnetNetwork::quiescenceSnapshot()` now accounts for queued NI flits and
messages (both injection and ejection MessageBuffers), router flits, non-IDLE
input/output VCs, pending data/credit link items, bridge items, and every
output-credit deficit.  Accessors are read-only and do not schedule events.
The functional tester requires all AXI transactions complete, every adapter
idle, and this snapshot empty for two consecutive network cycles.

The same generic instrumentation records per-vnet input-VC maximum occupancy,
router credit stalls, VC-allocation stalls, and NI credit stalls.  Garnet
continues to see only vnet/VC/flit/link/credit state; the section 15.5 AXI
type/include/channel scan remains empty.

### Exact U5-U7, U10, and U12 suites

All explicitly registered binaries were rebuilt and run with no skipped or
disabled tests:

| Binary | Result |
|---|---|
| `build/AXI_MESH/mem/axi/axi_ordering.test.opt` | PASS, 8/8 U5 tests |
| `build/AXI_MESH/mem/axi/axi_flow_control.test.opt` | PASS, 4/4 U6 tests |
| `build/AXI_MESH/mem/axi/axi_error_response.test.opt` | PASS, 7/7 U7 tests |
| `build/AXI_MESH/mem/axi/axi_protocol_checker.test.opt` | PASS, 5/5 U10 tests |
| `build/AXI_MESH/mem/ruby/network/garnet/garnet_quiescence.test.opt` | PASS, 3/3 U12 tests |

The five earlier binaries were also rerun: U1 8/8, U2-U4 19/19, U8 4/4,
U9 6/6, and U11 2/2.  The combined required unit set is therefore 66/66.

### I5-I10 functional Garnet runs

All runs used the real five-vnet Garnet path and parsed `axi_result.json`.
Each result has issued=completed, zero protocol errors, zero outstanding,
orphan, and ROB entries at exit, an all-zero protocol-neutral quiescence
snapshot, and at least two consecutive quiet cycles.

| Case | Result | Required evidence |
|---|---|---|
| `same_id_order` | PASS, tick 25641 | completion `[0,1,2,3]`; source buffered 2 and target blocked 2 younger same-ID responses |
| `cross_id_reorder` | PASS, tick 24642 | completion `[3,1,2,0]`, proving real different-ID inversion |
| `buffer_depth_credit_d1` | PASS, tick 514152 | 64-bit link, one VC/vnet; W VC max 1, credit stalls 2131 |
| `buffer_depth_credit_d2` | PASS, tick 258408 | W VC max 2, credit stalls 1005 |
| `buffer_depth_credit_d8` | PASS, tick 147852 | ten-flit W packet, W VC max 8, credit stalls 280 |
| `target_quota_no_hol` | PASS, tick 27306 | orphan high-water 1, quota stall 1; source 1 completed first |
| `ejection_backpressure` | PASS, tick 71262 | B/R local queues reached depth 2; 274 ejection-stall cycles; all 16 transactions drained |
| `response_progress` | PASS, tick 3008988 | 2001 transactions created every cycle through cycle 2000; max eligible/no-progress 4 cycles |

I10 records and validates its resolved liveness proof before simulation:
target latency 4 + forced stall 32 + packet/path slack 64 = 100 cycles,
strictly below the fixed 256-cycle watchdog.  It completed 1001 writes and
1000 reads and drained around network cycle 9036, before cycle 20000.

I0-I4 were repeated after the final NI wakeup change and passed at ticks
21978, 11322, 37296, 32634, and 16650 respectively.  An AXI_MESH opt
`single_write` smoke also passed at tick 11322.

### Build and legacy regression

| Command | Result |
|---|---|
| `scons build/AXI_MESH/gem5.debug -j4` | PASS, exit 0 |
| `scons build/AXI_MESH/gem5.opt -j4` | PASS, exit 0 |
| `scons build/Garnet_standalone/gem5.opt -j4` | PASS, exit 0 |
| section 15.4 legacy vnet 0/1/2 commands | PASS, exit 0; exact golden match |
| `scons build/RISCV_CHI/gem5.opt -j4` | PASS, exit 0 |
| specification section 15.5 Garnet AXI-leak scan | PASS, no output |
| `git diff --check` | PASS, no output |

The three legacy cases again exited at tick 5000328.  Packet/flit totals,
per-vnet distributions, average packet/flit latency, and average hops exactly
match `tests/gem5/axi_garnet/golden/garnet_xs_dev_7478835a.json`.

## Commit 6 gate: deterministic stress, replay, and regressions

### Deterministic workload and artifact contract

The final gate adds one shared, versioned deterministic contract for C++ and
Python.  SplitMix64 keyed draws use the stable scenario ID, transaction index,
stage, beat index, and draw kind, so adding an unrelated random draw cannot
perturb existing traffic.  Golden vectors test both implementations.  The
workload JSONL freezes expected UID, target/source ordering sequences, write
ordinal, target, response, arrival, payload seed, and strobe before gem5
starts; canonical encoding and SHA256 fields make replay inputs auditable.

Every successful AXI process emits and independently verifies:

- the resolved semantic configuration and its SHA256;
- canonical workload and event traces with their SHA256 values;
- packet, flit, and wire-byte conservation by vnet;
- bounded queue high-water and categorized stall counters;
- a rectangular link-by-VC credit ledger with stable link ownership IDs;
- endpoint, MessageBuffer, NI, router, link, bridge, and credit drain state;
- exact transaction completion order and architected error count.

Strict failures run in separate processes and emit a negative-result schema
with the expected marker, exit code, provenance, hashes, available artifacts,
and explicit unavailable fields.  N10 is distinguished as a final-consistency
failure with exit code 2: its intentionally residual source W burst is
reported, while every credit-ledger entry proves `sent == 0`.

### Measurement window and buffer matrix

The 72-case matrix is the Cartesian product of four per-vnet depth profiles,
three VC counts, and six traffic selectors.  Traffic is generated throughout
the fixed network-cycle interval `[500,2500)`.  Counter snapshots at both
boundaries produce window deltas; max occupancy is reset at the start
boundary, and the tester holds the simulation open until the end boundary
even if functional traffic has already drained.  The verifier rejects traffic
on an unselected vnet, an incorrect boundary, a non-window-scoped occupancy or
stall value, or any occupancy above the configured per-vnet depth.

I6 now names and checks the intended pair rather than accepting an arbitrary
different-ID inversion: source-0 fast write ID 1 must finish before slow write
ID 0.  I7 uses a 64-bit link, so each 80-byte W packet contains ten flits and
is larger than even the depth-8 VC.  Each depth-1/2/8 case must reach exactly
the configured occupancy, observe credit backpressure, finish, and restore all
credits.

### Exact manifest and unit gates

The generated manifest contains exactly 10 required C++ binaries and 66 named
tests.  Its integration sets are exactly 44 quick processes and 127 full
processes; the full set contains all 22 negative processes, 72 buffer-matrix
processes, three quick random seeds, ten full-only random seeds, and one
10,000-transaction nightly process.  Both runners reject missing, extra,
disabled, skipped, xfailed, deselected, timed-out, or stale results.

The aggregate null-ISA build required three build-only compatibility repairs:

- every `GTest` clone defines its test-only `UNIT_TEST` macro, including when
  reached through the aggregate target;
- CPU/O3 trace and BTB tests return early for the deliberately CPU-less null
  ISA, while real-ISA behavior is unchanged;
- the existing dueling-cache test now uses the sequential integer monitor IDs
  introduced by the user-approved working baseline.

No CHI source or protocol file was changed.  These repairs only affect test
construction and do not add a CPU to AXI_MESH or Garnet_standalone.

## Final delivery summary

### Repository state

- Specification base: `7478835ac25d406941490578874d5022e3a46ec5`
- User-approved working baseline: `65f3241a6cd486dffddaca251536f5c691af9a1f`
- Branch: `feature/ai-mesh-axi-garnet`
- Verified code SHA: `cef83f0f7eb2b4111b3797d5e65e3edd729c4dc1`
- Final delivery diff from the specification base, including this report:
  209 files, 34,250 insertions, 445 deletions.  This includes the pre-existing
  working baseline commit described under Deviations below.
- AXI/Decision-1 delivery alone, relative to the approved working baseline
  and including this report: 112 files, 18,662 insertions, 158 deletions.

The user-approved Decision-1 prerequisite is `5abfa985fd` (`build: restore
null-ISA Garnet standalone baseline`).  The specification's implementation
series is:

1. `86360e441c` `axi-mesh: add isolated AXI_MESH build scaffold`
2. `dd38e5bad6` `garnet: add per-vnet class and VC buffer depth`
3. `7a7c2ceb58` `garnet: support protocol-neutral dynamic packet sizing`
4. `2868eebc83` `axi-mesh: add AXI beat messages and endpoint adapters`
5. `51774551c9` `axi-mesh: add ordering, response ROB, and backpressure`
6. `cef83f0f7e` `tests: add AXI Garnet stress, replay, and regressions`
7. `docs: document AXI Garnet configuration and limitations` (this report;
   its SHA is recorded in the final handoff because embedding a commit's own
   SHA would change that SHA)

### Implemented scope

- A separately selected, null-ISA `AXI_MESH` Ruby protocol and CPU-less
  four-router mesh with explicit initiator/target placement.
- Five independent AXI channel vnets (`AW/W/B/AR/R`), generated beat-level
  messages, finite SLICC/local queues, and C++ initiator/target adapters.
- Configurable data width, link width, VC count, per-vnet VC depth, endpoint
  queue depths, target capacities, source quotas, ordering ROBs, and service
  plans, all validated before simulation.
- Full-bus data packet accounting, protocol-neutral dynamic Garnet
  packetization, per-vnet class/depth configuration, and legacy fallback.
- AXI4 INCR validation, Nth-AW/Nth-W-burst pairing, streaming W-first merge,
  WSTRB/narrow accesses, simple byte memory, OKAY/DECERR/SLVERR responses, and
  default error-target routing through the real NoC.
- Per-source/per-direction same-ID ordering with different-ID reordering,
  bounded response ROB/reassembly, response obligations, and lossless
  backpressure from target service through network ejection.
- Protocol-neutral credit ledger, queue/VC high-water and stall statistics,
  two-cycle quiescence proof, deterministic workloads, replay, strict result
  schemas, negative fault injection, and matrix-window measurement.

### Configuration reference

All comma-separated channel vectors use `AW,W,B,AR,R` order unless a row says
otherwise.  A queue entry is one channel message: W and R therefore count
beats, while AW, B, and AR count their address/response messages.  All cycle
parameters use the owning Ruby/adapter clock; the supplied test configuration
sets both Ruby and system clocks to 1 GHz.

| Option | Default | Unit and contract |
|---|---|---|
| `--axi-mesh-routers` | `4` | router count; copied to legacy `num_cpus` only because the common topology interface uses that field as router count |
| `--mesh-rows` | `2` | router rows; positive and must divide router count |
| `--link-width-bits` | `128` | Garnet link/flit width in bits; positive multiple of 8 |
| `--vcs-per-vnet` | `4` | VCs in each virtual network |
| `--garnet-vnet-classes` | `ctrl,data,ctrl,ctrl,data` | five `ctrl`/`data` class names |
| `--garnet-buffers-per-vnet` | `4,8,4,4,8` | flits per input VC for each vnet; also the corresponding initial/max upstream credit |
| `--axi-data-width-bits` | `512` | AXI data bus bits; one of 64, 128, 256, or 512 |
| `--axi-id-width-bits` | `8` | implemented AXI ID bits, range 1 through 16 |
| `--axi-user-width-bits` | `0` | USER bits; zero is the only supported value |
| `--axi-wire-header-bytes` | `24,16,8,24,16` | serialized header bytes per channel; W/R add one complete data-bus word |
| `--axi-source-fifo-depths` | `16,64,16,32,128` | source adapter entries by channel |
| `--axi-message-buffer-depths` | `16,64,16,32,128` | SLICC/network MessageBuffer entries by channel |
| `--axi-local-delivery-depths` | `16,64,16,32,128` | finite post-network delivery entries by channel |
| `--axi-max-outstanding-reads` | `64` | accepted read transactions per source adapter |
| `--axi-max-outstanding-writes` | `32` | accepted write transactions per source adapter |
| `--axi-source-pre-aw-bursts` | `16` | unbound W-burst slots per source adapter |
| `--axi-source-pre-aw-beats` | `256` | total unbound W-beat slots per source adapter |
| `--axi-b-rob-transactions` | `64` | B response-transaction ROB slots; must cover the write outstanding limit |
| `--axi-r-rob-beats` | `1024` | R response-reassembly beat slots |
| `--axi-target-write-contexts` | `64` | shared AW_ONLY/W_ONLY/BOUND transaction contexts per target |
| `--axi-target-write-assembly-beats` | `4096` | total W assembly-beat reservations per target |
| `--axi-target-read-contexts` | `64` | live read transaction contexts per target |
| `--axi-target-read-response-beats` | `4096` | reserved/generated R beats per target |
| `--axi-target-service-depths` | `32,32` | bounded write/read service transaction slots |
| `--axi-target-base-latencies` | `1,1` | write/read base service cycles |
| `--axi-target-response-ready-depths` | `16,128` | ready B transactions and ready R beats |
| `--axi-orphan-w-transactions` | `16` | W_ONLY transaction subquota inside the shared write pool |
| `--axi-orphan-w-beats` | `256` | W_ONLY beat subquota inside assembly storage |
| `--axi-strict-protocol` | `true` | must remain true; false is rejected before instantiate |
| `--axi-seed` | `42` | unsigned 64-bit deterministic master seed |
| `--axi-scenario` | empty | JSON scenario path; empty selects the built-in smoke |
| `--axi-max-sim-ticks` | `100000` | maximum simulation duration in gem5 ticks; supplied tests start at tick zero |
| `--axi-result-json` | output-directory default | result JSON path override |
| `--axi-raw-shim-probe` | false | Commit-1-only independent queue ownership probe, not an AXI transaction |
| `--axi-raw-probe-hold-cycles` | `8` | local-delivery hold duration for the raw probe |

Scenario `target_ranges` are half-open byte-address intervals.  Each quota row
uses transaction units for `write_contexts/read_contexts` and beat units for
`write_beats/read_beats`; the sum of statically configured source quotas may
not exceed the target capacity.  A transaction's `size` is `log2(bytes per
beat)`, `beat_count` is 1 through 256, `arrival_cycle` and
`target_extra_latency_cycles` are non-negative cycles, and addresses are
unsigned 64-bit byte addresses.  `measurement_window_cycles` is an inclusive
start/exclusive end pair in network cycles; `measurement_vnet` is 0 through 4
or -1 for all vnets.  The -2 sentinel is internal and means measurement is
disabled.

### AXI support matrix

| Feature | Support and failure policy |
|---|---|
| Five independent channels | supported as five ordered vnets: AW=0, W=1, B=2, AR=3, R=4 |
| Data width | 64/128/256/512 bits |
| Burst length | 1 through 256 beats |
| Naturally aligned INCR | supported, including narrow transfers and full-bus W/R wire accounting |
| Unaligned INCR | architected DECERR after complete drain |
| FIXED/WRAP | architected DECERR after complete drain |
| 4 KiB crossing or arithmetic overflow | strict fatal before unsafe address arithmetic or injection |
| WSTRB | byte-accurate full or arbitrary legal lane masks; integration generator covers full/alternating masks |
| W before AW | supported with bounded ordinal-based orphan storage and in-place merge |
| Multiple outstanding IDs | supported; same-ID commit/retire ordered, different IDs may reorder |
| W interleaving | no WID exists in AXI4; a single W stream is segmented only by WLAST and paired Nth-to-Nth with AW |
| Backpressure | lossless at source FIFO, MessageBuffer/NI, router credit, target quota/service, response-ready, local delivery, and source ROB boundaries |
| Responses | OKAY, DECERR, and SLVERR; erroneous reads return all requested zero-data beats with only the final RLAST |
| Decode miss | routed through a real default error target and real Garnet packets, never source-local shortcut |
| LOCK/exclusive or nonzero REGION | architected DECERR |
| CACHE/PROT | carried in trace metadata; no cache/permission semantics |
| QOS | carried and counted; no Router/SwitchAllocator QoS policy |
| USER | width must be zero |
| ACE/CHI snoop, AXI5 ATOP/atomic | not represented and never silently downgraded |

### Statistics and artifact semantics

| Field | Meaning |
|---|---|
| `packets_{injected,ejected}[v]` | complete channel messages entering/leaving vnet `v` |
| `flits_{injected,ejected}[v]` | dynamic `ceil(wireBytes/linkBytes)` flits entering/leaving vnet `v` |
| `wire_bytes_{injected,ejected}[v]` | serialized header plus full data-bus word for W/R, summed by vnet |
| `input_vc_occupancy_flit_cycles[v]` | sum over all router input VCs of occupancy multiplied by elapsed router cycles |
| `input_vc_full_vc_cycles[v]` | sum of cycles in which each input VC is full; may exceed total simulation cycles because VCs are aggregated |
| `input_vc_full_events[v]` | transitions into full state for input VCs in vnet `v` |
| `input_vc_max_occupancy[v]` | maximum flits observed in any selected input VC; never greater than configured depth |
| `credit_stall_vc_cycles[v]` | output-VC cycles blocked by unavailable downstream credit |
| `vc_alloc_stall_vc_cycles[v]` | eligible head-flit VC-allocation stall cycles, separate from credit stalls |
| `ni_credit_stall_vc_cycles[v]` | NI output-VC cycles blocked by credit |
| `ni_vc_busy_cycles[v]` | NI cycles in which injection could not acquire a free output VC |
| `queue_high_water` | maximum occupancy of local FIFO, MessageBuffer, router VC, and adapter ingress groups |
| `stall_events` | separate local-FIFO, MessageBuffer/NI, router-credit, and VC-allocation observations |
| `qos_transactions[0..15]` | target-side count of accepted AW/AR transactions by carried AxQOS value; the sum equals accepted transactions |
| credit ledger entry | stable directed `link_id`, owner/port/vnet/VC, depth, initial/current credit, sent and returned counts; invariant `initial + returned == sent + current` |
| `quiescence_snapshot_at_exit` | NI messages/flits, router flits/VC states, data/credit links, bridges, and credit deficit; every field must be zero twice consecutively |
| `measurement_window` | boundary ticks plus window-delta counters; max occupancy is reset at its start boundary |

`axi_result.json`, `workload.jsonl`, `event_trace.jsonl`, and
`credit_ledger.json` are the normal mandatory artifacts.  Controlled failures
instead use `negative_result.json` and declare which normal fields are
unavailable.  Result status alone is never trusted: the runner recalculates
semantic configuration, workload/trace hashes, transaction/beat/packet/flit
counts, queue bounds, credit conservation, completion ordering, and drain.

### Reproduction commands

From the repository root, the mandatory commands are:

```bash
scons build/AXI_MESH/gem5.debug -j4
scons build/AXI_MESH/gem5.opt -j4
scons build/Garnet_standalone/gem5.opt -j4
scons build/RISCV_CHI/gem5.opt -j4
scons build/AXI_MESH/unittests.opt -j4

python3 tests/gem5/axi_garnet/run_axi_unit_tests.py \
  --build-dir build/AXI_MESH --variant opt \
  --manifest tests/gem5/axi_garnet/manifest.json

timeout 60s python3 -m pytest -q --runxfail \
  --junitxml=m5out/axi-pyunit.xml tests/pyunit/ai_mesh

python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.debug --suite quick
python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.opt --suite full

if rg -n '#include .*(axi|Axi|AXI)|dynamic_cast<.*Axi|AxiChannel|AXIChannel' \
  src/mem/ruby/network/garnet; then
  exit 1
fi
git diff --check
```

The exact three-command legacy loop is recorded under Gate 0 and was rerun
unchanged for the final gate.  The runners require an empty unique output root
when one is supplied, so a previous result cannot satisfy a new run.

### Known unsupported features

- Only naturally aligned AXI4 INCR transfers take the OKAY fast path.
  FIXED/WRAP and unaligned transfers are fully drained through the default
  error target and return DECERR; strict arithmetic and 4 KiB violations are
  rejected as specified.
- Exclusive/LOCK and nonzero REGION return DECERR.  USER width must be zero.
- ACE/CHI snoops and AXI5 ATOP/atomic operations are not represented.
- CACHE and PROT are trace metadata only.  QOS is carried and counted but does
  not change Garnet switch arbitration.
- The deterministic target memory/service model is not a DRAM timing model;
  a modeled error has all-or-nothing side effects and is not claimed as AXI
  architectural atomicity.
- Queue/depth parameters are selectable before instantiate and are not
  dynamically resized during simulation.

### Verification

| Command | Exit | Cases | Pass | Fail | Skip | Timeout | Result directory |
|---|---:|---:|---:|---:|---:|---:|---|
| `scons build/AXI_MESH/gem5.debug -j4` | 0 | - | - | 0 | 0 | 0 | `build/AXI_MESH/gem5.debug` |
| `scons build/AXI_MESH/gem5.opt -j4` | 0 | - | - | 0 | 0 | 0 | `build/AXI_MESH/gem5.opt` |
| `scons build/Garnet_standalone/gem5.opt -j4` | 0 | - | - | 0 | 0 | 0 | `build/Garnet_standalone/gem5.opt` |
| `scons build/RISCV_CHI/gem5.opt -j4` | 0 | - | - | 0 | 0 | 0 | `build/RISCV_CHI/gem5.opt` |
| `scons build/AXI_MESH/unittests.opt -j4` | 0 | all registered | target passed | 0 | 1 generic | 0 | `build/AXI_MESH/unittests.opt` |
| AXI strict C++ unit runner | 0 | 66 | 66 | 0 | 0 | 0 | `m5out/axi-unit-suite` |
| Python unit command from section 15.2 | 0 | 21 | 21 | 0 | 0 | 0 | `m5out/axi-pyunit.xml` |
| debug quick integration suite | 0 | 44 | 44 | 0 | 0 | 0 | `m5out/axi-garnet-quick-20260901T050617281358Z-2` |
| opt full integration suite | 0 | 127 | 127 | 0 | 0 | 0 | `m5out/axi-garnet-full-20260901T050847114952Z-2` |
| section 15.4 legacy vnet 0/1/2 loop | 0 | 3 | 3 | 0 | 0 | 0 | `m5out/garnet-legacy-vnet{0,1,2}` |
| section 15.5 Garnet AXI-leak scan | 0 | 1 | 1 | 0 | 0 | 0 | no output |
| `git diff --check` | 0 | 1 | 1 | 0 | 0 | 0 | no output |

The one aggregate skip is the repository's existing
`SerializableFixtureDeathTest.NoSectionParamIn`, which explicitly skips in a
fast build because assertions are compiled out.  It is outside the mandatory
AXI set.  The exact AXI runner separately discovered and executed all 66 named
tests with zero skipped or disabled tests.  The host `/usr/bin/python3` does
not provide pytest, so the exact Python command was run with the already
available `/tmp/axi-pytest-venv/bin` first in `PATH`; no dependency was
installed or changed.

The only build warnings were pre-existing optional-host-feature notices for
missing PNG, HDF5, and backtrace support.  They did not disable an AXI,
Garnet, Ruby, or CHI component.

### Conservation and drain summary

The full suite contains 105 normal AXI cases, three architected-error AXI
cases, 16 strict/final-consistency failure processes, and three legacy cases.
Across the 108 successful/architected-error AXI result artifacts:

- issued = accepted = completed = 36,417 transactions; 253 expected
  architected errors completed normally;
- the target-observed AxQOS histogram was
  `[5363,2021,2035,2048,2082,2064,2152,2029,2143,2091,2126,2043,2152,2086,2014,1968]`;
  every value 0 through 15 was observed and its sum was exactly 36,417;
- injected = ejected = 265,324 packets, distributed by vnet as
  `[18657,107071,18657,17760,103179]`;
- injected = ejected = 1,143,227 flits, distributed by vnet as
  `[37320,535835,18657,35520,515895]`;
- every credit ledger was rectangular and restored, across observed
  `(directed links, VCs/link)` shapes `(12,5)`, `(12,10)`, `(12,20)`,
  `(14,5)`, `(14,20)`, and `(64,20)`; total mismatches were zero;
- the maximum at exit was zero for outstanding transactions, orphan W,
  response ROB, MessageBuffers, local delivery, adapter ingress, response
  obligations, business events, router VC flits, and credit mismatches;
- every quiescence snapshot was empty for at least two consecutive network
  cycles;
- all workload and event trace SHA256 values were independently recomputed and
  matched.  The determinism triplet also exact-matched config, workload, and
  event-trace hashes;
- all 16 strict/final-consistency failures matched their exit code and marker
  with zero packets and flits injected before failure.  N10 additionally had
  zero sent flits in every credit-ledger entry.

All three explicit legacy runs exited at tick 5,000,328.  Packet/flit totals,
per-vnet distributions, average packet/flit latency, and average hops matched
the versioned baseline golden exactly (floating tolerance `1e-12`).

### Deviations from this specification

- The requested specification base is not the repository's checked-out
  parent.  The user-approved working baseline includes pre-existing commit
  `65f3241a6c` (`Fix 16-core CHI Linux checkpoint restore`).  Final diff
  counts therefore report both the specification-base range and the isolated
  AXI/Decision-1 range.  Those pre-existing changes were preserved.
- Per user-selected Decision 1, commit `5abfa985fd` repairs only the null-ISA
  build prerequisites needed by CPU-less AI Mesh/Garnet.  It does not add or
  enable a CPU.  Later aggregate-test guards likewise omit CPU-only O3/BTB
  tests only when `TARGET_ISA == 'null'`; real-ISA builds are unchanged.
- Gate 0 found no runnable, image-free CHI smoke in this fork: the available
  `ruby_random_test.py` path fails in pre-instantiate configuration because
  the repository's default prefetcher expects `cpu.mmu` on a `RubyTester`.
  As allowed by section 15.1, CHI runtime smoke is conditional-not-applicable;
  the mandatory `RISCV_CHI` build passed.  No CHI file was changed to
  manufacture a smoke.
- There are no AXI protocol, packetization, ordering, flow-control, artifact,
  or mandatory-test deviations from the specification.

### Remaining blockers

None.  All mandatory builds, exact unit sets, integration processes, legacy
golden comparisons, conservation/drain checks, and static hygiene checks have
completed successfully.

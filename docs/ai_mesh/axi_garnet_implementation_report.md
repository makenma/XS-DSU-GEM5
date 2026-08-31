# AXI Garnet implementation report

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

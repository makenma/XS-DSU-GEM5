# Timing CPU RNF adapter

`ChiRnfBridge` connects the instruction, data and page-table-walker ports of a
timing CPU to the custom CHI mesh. It has a finite unified cache and one active
CPU request. Local cache hits use `hit_latency`; every coherent RAM miss,
ownership transfer and replacement travels through the mesh. MMIO uses the
platform bus. The adapter does not provide an atomic or timing bypass to backing
memory. Initial ELF/DTB loading uses the SNF's functional port.

This adapter supplies an executable CPU endpoint for the HNF implementation. It
is not a cycle-accurate model of a particular processor's RNF. Its private-cache
timing and LR/SC policy are distinct from the CMN-aligned SLCSF pipeline and the
router's preserved pipeline stages.

## Cache and transactions

The cache stores 64-byte lines, NS, valid/dirty bits and an LRU timestamp. Capacity
and associativity are fixed parameters (`cache_lines`, `cache_ways`, defaults
256 and 4). Invalid ways are selected first; otherwise the least recently used
way in the set is replaced. Instruction fetch and LR data need distinct ways,
so at least two ways are required.

The first implementation keeps exclusive clean or dirty lines. It does not
retain shared copies: even instruction misses use `ReadUnique`. Consequently,
shared instruction streams can generate ownership traffic between CPUs. This
is a performance limitation of the endpoint, not a reason to bypass CHI or keep
an invalidated copy.

| Operation | Wire exchange |
| --- | --- |
| Miss | `ReadUnique` (0x07), two 32-byte `CompData` beats, `CompAck` |
| Clean or dirty eviction | `WriteBackFull` (0x1b), `DBIDResp`, two `CopyBackWrData` beats, `Comp` |
| Clean/invalidate CPU maintenance | `CleanInvalid` (0x09) with requester snoop enabled, then `Comp` |
| Incoming ownership snoop | `SnpUnique` (0x07) or `SnpCleanInvalid` (0x09); invalidate the private copy and reply |

Requests have 64-byte size encoding 6 and `AllowRetry=0`. The HNF is selected by
cache-line index modulo the configured `hnf_ids`. Snoop data reports the final
invalid state: response 0 for clean or 4 (`I_PD`) for dirty. Writeback data instead
reports UC (2), UD (6), or Invalid (0). Both data paths use beat IDs 0 and 2, with
`last` only on the second beat.

A replacement remains snoopable while its writeback waits for a DBID. If an
older transaction at the HNF wins the line hazard and invalidates it, the
writeback returns two Invalid beats. The HNF discards these and completes the
writeback without overwriting the new owner's state or data. Once the HNF has
issued the DBID, its same-line hazard must prevent later conflicting snoops
until the writeback completes.

All outgoing channels share a bounded response budget. A received snoop or data
response is consumed only when enough response capacity remains. CPU response
backpressure holds the packet, and request retries are delivered when the single
active packet retires. A response callback may immediately submit the next CPU
request without losing ownership of either packet.

## Atomic operations and LR/SC

AMOs and swaps execute against the owned cache line, preserving the old value
for the CPU response. Ordinary stores honor byte enables. A line transferred to
another RNF therefore includes all earlier writes and atomic operations.

The adapter records the LR's 16-byte reservation granule, NS and CPU context.
SC succeeds only with the matching live reservation; a failed SC does not
reacquire ownership. Replacement and coherence invalidations clear both the
adapter reservation and the CPU's ISA reservation through an upstream express
invalidation snoop.

With exclusive-only reads, two CPUs can otherwise alternate ownership on every
LR and both fail SC indefinitely. `reservation_cycles` provides a bounded RNF
forward-progress policy, defaulting to 1024 cycles. An ownership snoop of the
reserved line may occupy one deferred-snoop slot until SC clears the reservation
or the protection deadline expires. The slot retains the incoming wire credit
until the snoop is actually processed; unrelated snoops can still proceed. Its
deadline is fixed when the snoop is deferred, so subsequent LRs cannot extend an
already pending snoop indefinitely. A zero window disables protection.

This policy delays the RNF's snoop response; it does not modify router, HNF or
SLCSF stage delays. The `deferredSnoops` statistic exposes its use. Drain waits
for any deferred snoop to become eligible, including natural deadline expiry
when the CPU has stopped issuing instructions.

## Native checkpoint contract

Drain completes all CPU requests, outgoing responses, CHI transactions,
deferred snoops and link credit returns. Valid private cache lines remain warm;
dirty data is not silently written through to SNF during checkpoint creation.
Serialization saves the line tags, NS, valid/dirty state, all 64 data bytes,
replacement timestamps, next transaction ID, and adapter reservation fields.
The checkpoint checks cache capacity and associativity on restore. HNF/SLC/SF,
SNF backing memory and the routers serialize their corresponding stable state.

The RISC-V ISA also maintains an LR reservation, which the native checkpoint
saves separately. Legacy ISA checkpoints without that field restore an invalid
reservation, so their first SC may fail and software must retry. CPU functional
debug accesses are supported only for a complete resident line, so a debugger
cannot read stale SNF backing memory behind a dirty private copy.

## Validation scope

The two-hart mesh smoke test in `tests/gem5/chi/mesh_smoke.S` fills and evicts
both RNFs, checks another hart's data including both halves of each line,
exercises AMO and contended LR/SC, and checkpoints with warm coherent state.
Its resumed path rechecks data and atomic visibility. The final binary passed
both the original and restored execution with identical ending ticks in
`m5out/linux-chi-mesh/mesh-final/`. Separate live-reservation/legacy-reservation
checkpoint tests passed in `lrsc-final/`, and translated accesses, PMP and
instruction-retirement state passed in `mmu-final/`. These paths are all below
`m5out/linux-chi-mesh/`. Linux login/checkpoint evidence is recorded separately;
these directed tests are not a claim about Linux application performance.

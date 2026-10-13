# Gate 0 — repository audit

Inspected 2026-09-30. No applicable AGENTS.md exists in this workspace.
Existing uncommitted CHI/co-simulation changes are retained.

Publication note (2026-10-13): the `hni` branch includes the shared CHI
metadata and HNF ReadReceipt support used by this baseline. Independent
ChiCosimBridge ABI v2 and common co-simulation configuration edits remain
local; the ABI v2 observations below describe that inspected workspace.

* Version: src/base/version.cc reports DEVELOP-FOR-22.1; fork of XS-GEM5.
  Build: scons build/RISCV/gem5.opt --ignore-style --linker=gold -j32.
  configs/example/kmhv2_chi_2x2_hnf_se.py demonstrates actual RV64 SE
  execution despite the older README saying SE is unsupported. Use
  RiscvTimingSimpleCPU with L1 caches for the reproducible workload;
  XiangshanCore/RiscvO3CPU remains selectable and has a 64 B acceptance case.
  The initial TimingSimpleCPU run exposed the decoder's assumption that fetch
  words start at the instruction PC. Repair aligned-word/straddling decode
  and check it with explicit guest instructions on both CPU models.
* Types/ports: src/mem/cache/CHI/base/ChiChannel.hh, ChiCommonPort.hh.
  RawReq.size is a byte count (zero means block size), not log2(CHI Size).
  DAT is 32 B, lines are 64 B. NodeID = x<<7 | y<<4 | p<<2 | d.
  ChiCommonPort has bounded per-channel queues/credits and Consumer wakeups.
* RNF: Cache2ChiBridge.{cc,hh,py}; uncacheable requests currently go through
  mem_side to the classic bus. In the new topology that port terminates at
  the RNF uncacheable Packet/flit engine, which injects into the same mesh.
  Functional image loading uses system_port; it is outside the measured path.
* Network/address selection: ChiRouterRefModel; configs/common/CacheConfig.py
  documents mesh wiring. RNF chooses HNF or a hash; routers route by NodeID.
  The new factory routes the exact MMIO window to HNI and RAM to HNF.
* HNF: HomeNodeFull, HomeLinkLayer, HnfCoherencyController, SlcSnoopFilter.
  Existing full-line coherent read/write and CompAck paths are reused.
  ReadOnce retains no SF ownership. WriteUniqueFull installs SLC data but
  does not snoop cached destinations. Therefore the dedicated buffer aperture
  MUST be mapped uncacheable; cached aliases/concurrent CPU buffer accesses
  while BUSY are excluded. Ordinary instruction/data caches remain enabled.
  Partial coherent write is explicitly prohibited: adapter memory-only RMW
  preserves that restriction. RMW is serialized per line within each adapter;
  CPU/device ownership excludes cross-adapter races.
  CPU startup stalls with 2/4 HNF PoC entries; use at least 8 for this CPU
  baseline. The independent CPU-free RNI/HNI tests run with 2 HNF entries.
* Existing write completion is early CompDBIDResp. A default-off HNF option
  will provide DBIDResp followed by Comp after successful SLC update; the DMA
  baseline requires this option and never completes on DBID or credit.
* DmaDevice/DmaPort: src/dev/dma_device.{hh,cc}, src/dev/Device.py.
  API actions split at cache-line boundaries; DmaPort preserves SenderState,
  buffers and completion events until all Packet responses. Its stock API
  has no error callback: add a callback without changing existing devices.
* SN: Chi2ClassicMemBridge forwards real timing memory accesses and handles
  separated write DBID/DAT/Comp; reuse it behind HNF.
* Co-sim: ChiCosimBridge.{cc,hh,py}, kmhv2_chi_2x2_router_cosim.py;
  UNIX socket ABI v2, quantum-based time advancement, bypass Packet proxy.
  Existing transaction spec is ../C2XM_COSIM_TRANSACTION_SPEC.md. RTL isn't
  invoked in this baseline; future HNI/RNI need CHI/BFM proxies, not SN reuse.

Address map: RAM [0,0x20000000); reserved buffer aperture
[0x10000000,0x10400000), guest VA 0x50000000 -> PA 0x10000000.
MMIO [0x40000000,0x40001000), identity mapped uncacheable. No IOMMU.
The config factory is the only topology/identity selection layer.

Gate 1: implement bounded DmaPort/Packet-flit engine and directed timing
stress. Gate 2: serial exact MMIO HNI plus 64 B CPU program. Gate 3:
lengths/boundaries/parallelism/errors/retries and path break tests. Gate 4:
three CPU benchmarks, stats, CSV and documented RTL replacement boundaries.

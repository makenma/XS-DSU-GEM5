"""Four KMH CPUs boot Linux through the native CHI L2 / 3x2 NoC.

RAM and page-table traffic use classic private caches -> ChiNativeL2 -> NoC
-> HN -> SN -> RAM. CLINT/PLIC/UART use a separate uncached DeviceIO crossbar.
OpenSBI's fw_payload.elf contains the Linux Image and its built-in initramfs.
This configuration has no disk/DMA device and no CPU bypass to backend RAM.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time

import m5
from _m5.core import seedRandom
from m5.objects import *
from m5.util import addToPath
from m5.util.convert import toMemorySize
from m5.util.fdthelper import Fdt, FdtNode, FdtProperty, FdtPropertyStrings, FdtPropertyWords, FdtState


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--kernel', '--firmware', dest='firmware', required=True,
                    help='OpenSBI fw_payload.elf containing the Linux Image')
parser.add_argument('--mem-size', default='1GiB')
parser.add_argument('--numa-nodes', type=int, choices=(1, 2), default=2)
parser.add_argument('--dtb-addr', type=lambda value: int(value, 0), default=None,
                    help='Default: start of last 2MiB of RAM (0xbfe00000 for 1GiB)')
parser.add_argument('--command-line', default=
                    'console=ttyS0,115200 earlycon=uart8250,mmio,0x10000000 '
                    'rdinit=/init loglevel=7 panic=-1')
parser.add_argument('--sets', type=int, default=512)
parser.add_argument('--assoc', type=int, default=8)
parser.add_argument('--mshrs', type=int, default=32)
parser.add_argument('--hn-entries', type=int, default=64)
parser.add_argument('--slc-lines', type=int, default=8192)
parser.add_argument('--seed', type=int, default=1)
parser.add_argument('--max-ticks', type=int, default=None)
parser.add_argument('--progress-ticks', type=int, default=1_000_000_000)
parser.add_argument('--no-census', action='store_true',
                    help='Disable compact endpoint transfer counts (detailed trace is separate)')
parser.add_argument('--zicbom', action='store_true',
                    help='Advertise implemented Zicbom (requires the FS CBO model patch)')
parser.add_argument('--suite-pipeline', action='store_true',
                    help='Validate each fixed suite stage at m5_exit before resuming Linux')
parser.add_argument('--suite-inputs', type=Path,
                    help='Frozen input index produced while building the suite payload')
parser.add_argument('--uncached-test-ram', action='store_true',
                    help='Reserve a no-map 4KiB normal noncacheable RAM page, still routed through CHI')
args = parser.parse_args()
if args.max_ticks is None:
    args.max_ticks = 100_000_000_000_000 if args.suite_pipeline else 100_000_000_000
if args.suite_pipeline and (not args.zicbom or args.numa_nodes != 2 or not args.uncached_test_ram):
    parser.error('suite pipeline requires --zicbom --numa-nodes 2 --uncached-test-ram')
if args.suite_pipeline and (args.suite_inputs is None or not args.suite_inputs.is_file()):
    parser.error('suite pipeline requires a prepared --suite-inputs index')
if args.max_ticks <= 0 or args.progress_ticks <= 0:
    parser.error('max/progress ticks must be positive')
if args.sets * args.assoc * 64 < 2 * 32 * 1024 + 2 * 4 * 1024:
    parser.error('native L2 capacity must cover the 32KiB I/D and 4KiB walker caches')
ram_start = 0x80000000
ram_bytes = int(toMemorySize(args.mem_size))
ram_end = ram_start + ram_bytes
dtb_window = 2 * 1024 * 1024
dtb_addr = args.dtb_addr if args.dtb_addr is not None else ram_end - dtb_window
if ram_bytes < 512 * 1024 * 1024 or ram_bytes % (args.numa_nodes * dtb_window):
    parser.error('RAM must be >=512MiB and divisible into 2MiB-aligned NUMA nodes')
if dtb_addr % 4096 or not ram_start <= dtb_addr < dtb_addr + dtb_window <= ram_end:
    parser.error('DTB needs a page-aligned, reserved 2MiB window inside RAM')
uncached_addr = dtb_addr - 4096 if args.uncached_test_ram else None
if uncached_addr is not None and uncached_addr < ram_start:
    parser.error('uncached test page must fit below the DTB reservation')
firmware = Path(args.firmware).resolve()
if not firmware.is_file():
    parser.error(f'firmware ELF does not exist: {firmware}')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inspect_payload(path):
    """Reject a relocated/wrong-ISA ELF or a payload overwriting the DTB."""
    segments = []
    with path.open('rb') as source:
        header = source.read(64)
        if len(header) != 64 or header[:6] != b'\x7fELF\x02\x01':
            parser.error('firmware must be a little-endian ELF64, not a raw Image')
        elf = struct.unpack('<16sHHIQQQIHHHHHH', header)
        if elf[2] != 243 or not ram_start <= elf[4] < ram_end:
            parser.error('firmware must be a RISC-V ELF with its entry inside RAM')
        for index in range(elf[10]):
            source.seek(elf[5] + index * elf[9])
            ph = struct.unpack('<IIQQQQQQ', source.read(56))
            if ph[0] != 1 or ph[6] == 0:
                continue
            address, end = ph[4], ph[4] + ph[6]
            if not ram_start <= address < end <= ram_end:
                parser.error(f'ELF PT_LOAD lies outside RAM: {address:#x}..{end:#x}')
            if address < dtb_addr + dtb_window and dtb_addr < end:
                parser.error('ELF payload overlaps reserved DTB window; move --dtb-addr or enlarge RAM')
            if uncached_addr is not None and address < uncached_addr + 4096 and uncached_addr < end:
                parser.error('ELF payload overlaps reserved uncached test RAM')
            segments.append({'start': hex(address), 'endExclusive': hex(end),
                             'fileBytes': ph[5], 'memoryBytes': ph[6]})
    if not segments:
        parser.error('firmware ELF has no nonempty PT_LOAD segments')
    return {'entry': hex(elf[4]), 'segments': segments, 'sha256': sha256(path)}


payload = inspect_payload(firmware)
seedRandom(args.seed)
gem5 = Path(os.environ['CHI_GEM5_DIR']).resolve()
addToPath(str(gem5 / 'configs'))
from common.xiangshan import XiangshanCore
from common.Caches import L1_ICache, L1_DCache, L1ToL2Bus, PageTableWalkerCache

system = System(mem_mode='timing', cache_line_size=64,
                mem_ranges=[AddrRange(ram_start, size=ram_bytes)])
system.voltage_domain = VoltageDomain()
system.clk_domain = SrcClockDomain(clock='1GHz', voltage_domain=system.voltage_domain)
system.num_cpus = 4
system.xiangshan_system = True
system.enable_difftest = False
system.cpu = [XiangshanCore(cpu_id=hart) for hart in range(4)]
system.platform = HiFive()
system.platform.terminal = Terminal(port=0, outfile='file')
system.platform.rtc = RiscvRTC(frequency='10MHz')
system.platform.clint.int_pin = system.platform.rtc.int_pin
system.platform.setNumCores(4)
system.platform.attachPlic()

# DeviceIO is independently decoded below each CPU's L1 crossbar. Its range
# contains no RAM, and it has no port to the SN/backend memory crossbar.
system.iobus = IOXBar()
system.platform.attachOnChipIO(system.iobus)
system.platform.attachOffChipIO(system.iobus)
# HiFive instantiates a mandatory PCI host PIO object. Connect its otherwise
# unused config aperture to DeviceIO, but advertise no PCI or DMA device in DT.
system.platform.pci_host.pio = system.iobus.mem_side_ports
io_ranges = [*system.platform._on_chip_ranges(), *system.platform._off_chip_ranges(),
             AddrRange(system.platform.pci_host.conf_base,
                       size=system.platform.pci_host.conf_size)]

corners = [(0, 0), (2, 0), (0, 1), (2, 1)]
def nid(x, y, port=0):
    return (x << 7) | (y << 4) | (port << 2)
rn_ids = [nid(x, y) for x, y in corners]
hn_ids = [nid(x, y, 1) for x, y in corners]
sn_ids = [nid(1, y, 2) for y in range(2)]
system.l2 = [ChiNativeL2(node_id=node, home_node_ids=hn_ids, home_hash_masks=[64, 128],
                       sets=args.sets, assoc=args.assoc, mshrs=args.mshrs,
                       request_queue=64, ranges=system.mem_ranges) for node in rn_ids]
system.hn = [ChiExtendedHome(node_id=node, sn_node_ids=sn_ids, sn_hash_masks=[4160],
                            num_entries=args.hn_entries, slc_lines=args.slc_lines) for node in hn_ids]
system.sn = [ChiNativeMemory(node_id=node, max_outstanding=64) for node in sn_ids]
system.routers = [ChiRouterRefModel(local_x=x, local_y=y) for y in range(2) for x in range(3)]
def router(x, y):
    return system.routers[y * 3 + x]
links = []
for y in range(2):
    for x in range(3):
        if x < 2:
            router(x, y).internal_ports[0] = router(x + 1, y).internal_peer_ports[2]
            router(x + 1, y).internal_ports[2] = router(x, y).internal_peer_ports[0]
            links.append({'from': f'R{x}{y}', 'to': f'R{x+1}{y}'})
        if y < 1:
            router(x, y).internal_ports[3] = router(x, y + 1).internal_peer_ports[1]
            router(x, y + 1).internal_ports[1] = router(x, y).internal_peer_ports[3]
            links.append({'from': f'R{x}{y}', 'to': f'R{x}{y+1}'})

system.l1bus = [L1ToL2Bus(point_of_coherency=False) for _ in range(4)]
for hart, (x, y) in enumerate(corners):
    cpu = system.cpu[hart]
    cpu.branchPred = DecoupledBPUWithBTB(bpDBSwitches=[])
    cpu.store_prefetch_train = False
    cpu.enable_storeSet_train = False
    cpu.mmu.pma_checker = PMAChecker(uncacheable=io_ranges +
        ([AddrRange(uncached_addr, size=4096)] if uncached_addr is not None else []))
    # Writable walker caches handle PTE A/D updates; their misses and writes
    # use the same native L2/NoC as all other RAM traffic.
    cpu.addPrivateSplitL1Caches(
        L1_ICache(size='32KiB', assoc=4, prefetcher=NULL),
        L1_DCache(size='32KiB', assoc=4, prefetcher=NULL),
        PageTableWalkerCache(size='4KiB', assoc=4, writeback_clean=False),
        PageTableWalkerCache(size='4KiB', assoc=4, writeback_clean=False))
    cpu.createInterruptController()
    cpu.connectCachedPorts(system.l1bus[hart].cpu_side_ports)
    cpu.connectUncachedPorts(system.iobus.cpu_side_ports, system.iobus.mem_side_ports)
    system.l1bus[hart].mem_side_ports = system.l2[hart].cpu_side
    system.l1bus[hart].mem_side_ports = system.iobus.cpu_side_ports
    router(x, y).device_ports[0] = system.l2[hart].chi_side
    router(x, y).local_ports[4] = system.hn[hart].rxport
    cpu.createThreads()
for sn in range(2):
    router(1, sn).local_ports[8] = system.sn[sn].chi_side

# Only the two SNs and functional initialization/debug ports reach RAM here.
# No CPU timing port or page-table walker connects to this crossbar.
system.membus = NoncoherentXBar(width=32, frontend_latency=1, forward_latency=1,
                               response_latency=1, header_latency=1)
system.system_port = system.membus.cpu_side_ports
system.memory = [SimpleMemory(range=AddrRange(ram_start, size=ram_bytes,
                         masks=[4160], intlvMatch=sn), latency='20ns') for sn in range(2)]
for sn in range(2):
    system.sn[sn].mem_side = system.membus.cpu_side_ports
    system.membus.mem_side_ports = system.memory[sn].port
for l2 in system.l2:
    l2.functional = system.membus.cpu_side_ports

out = Path(m5.options.outdir).resolve()
out.mkdir(parents=True, exist_ok=True)
numa_bytes = ram_bytes // args.numa_nodes
numa = [{'nodeId': node, 'start': hex(ram_start + node * numa_bytes),
         'bytes': numa_bytes,
         'harts': [hart for hart in range(4) if hart * args.numa_nodes // 4 == node]}
        for node in range(args.numa_nodes)]


def generate_dtb():
    state = FdtState(addr_cells=2, size_cells=2, cpu_cells=1)
    state.resetPhandles()
    dt = FdtNode('/')
    dt.append(state.addrCellsProperty())
    dt.append(state.sizeCellsProperty())
    dt.appendCompatible(['riscv-virtio'])
    dt.append(FdtPropertyStrings('model', 'gem5 four-hart KMH native CHI'))
    chosen = FdtNode('chosen')
    chosen.append(FdtPropertyStrings('bootargs', args.command_line))
    chosen.append(FdtPropertyStrings('stdout-path', '/soc/uart@10000000:115200n8'))
    dt.append(chosen)
    for placement in numa:
        address = int(placement['start'], 0)
        memory = FdtNode(f'memory@{address:x}')
        memory.append(FdtPropertyStrings('device_type', 'memory'))
        memory.append(FdtPropertyWords('reg', state.addrCells(address) + state.sizeCells(numa_bytes)))
        memory.append(FdtPropertyWords('numa-node-id', placement['nodeId']))
        dt.append(memory)
    reserved = FdtNode('reserved-memory')
    reserved.append(state.addrCellsProperty())
    reserved.append(state.sizeCellsProperty())
    reserved.append(FdtProperty('ranges'))
    dtb_reserved = FdtNode(f'dtb@{dtb_addr:x}')
    dtb_reserved.append(FdtPropertyWords('reg', state.addrCells(dtb_addr) + state.sizeCells(dtb_window)))
    dtb_reserved.append(FdtProperty('no-map'))
    reserved.append(dtb_reserved)
    if uncached_addr is not None:
        uncached = FdtNode(f'chi-uncached-test@{uncached_addr:x}')
        uncached.appendCompatible('chi-flowgraph,uncached-test-ram')
        uncached.append(FdtPropertyWords('reg', state.addrCells(uncached_addr) + state.sizeCells(4096)))
        uncached.append(FdtProperty('no-map'))
        reserved.append(uncached)
    dt.append(reserved)
    cpus = FdtNode('cpus')
    cpus.append(FdtPropertyWords('#address-cells', 1))
    cpus.append(FdtPropertyWords('#size-cells', 0))
    cpus.append(FdtPropertyWords('timebase-frequency', 10_000_000))
    for hart, cpu in enumerate(system.cpu):
        node = FdtNode(f'cpu@{hart:x}')
        node.append(FdtPropertyStrings('device_type', 'cpu'))
        node.append(FdtPropertyWords('reg', hart))
        node.appendCompatible('riscv')
        node.append(FdtPropertyStrings('status', 'okay'))
        node.append(FdtPropertyStrings('riscv,isa', 'rv64imafdc_zicsr_zifencei' + ('_zicbom' if args.zicbom else '')))
        if args.zicbom:
            node.append(FdtPropertyWords('riscv,cbom-block-size', 64))
        node.append(FdtPropertyStrings('mmu-type', 'riscv,sv39'))
        node.append(FdtPropertyWords('clock-frequency', 1_000_000_000))
        node.append(FdtPropertyWords('numa-node-id', hart * args.numa_nodes // 4))
        node.appendPhandle(f'linux-cpu-{hart}')
        intc = FdtNode('interrupt-controller')
        intc.append(FdtPropertyWords('#interrupt-cells', 1))
        intc.append(FdtProperty('interrupt-controller'))
        intc.appendCompatible('riscv,cpu-intc')
        intc.appendPhandle(cpu)  # Matches CLINT/PLIC generateDeviceTree keys.
        node.append(intc)
        cpus.append(node)
    dt.append(cpus)
    distance = FdtNode('distance-map')
    distance.appendCompatible('numa-distance-map-v1')
    matrix = []
    for source in range(args.numa_nodes):
        for target in range(args.numa_nodes):
            matrix.extend((source, target, 10 if source == target else 20))
    distance.append(FdtPropertyWords('distance-matrix', matrix))
    dt.append(distance)
    soc = FdtNode('soc')
    soc.append(state.addrCellsProperty())
    soc.append(state.sizeCellsProperty())
    soc.append(FdtProperty('ranges'))
    soc.appendCompatible('simple-bus')
    for device in (system.platform.clint, system.platform.plic, system.platform.uart):
        for node in device.generateDeviceTree(state):
            soc.append(node)
    dt.append(soc)
    fdt = Fdt()
    fdt.add_rootnode(dt)
    fdt.writeDtsFile(str(out / 'device.dts'))
    fdt.writeDtbFile(str(out / 'device.dtb'))
    if (out / 'device.dtb').stat().st_size > dtb_window // 2:
        parser.error('generated DTB leaves insufficient reserved space for OpenSBI fixups')


system.workload = RiscvLinux(object_file=str(firmware), command_line=args.command_line,
                             dtb_filename=str(out / 'device.dtb'), dtb_addr=dtb_addr,
                             load_addr_mask=0xffffffffffffffff, load_addr_offset=0)
root = Root(full_system=True, system=system)
generate_dtb()
topology = [{'id': f'{prefix}{i}', 'label': f'{prefix}{i}', 'nodeId': node, 'role': role}
            for prefix, ids, role in [('RN', rn_ids, 'RN-F'), ('HN', hn_ids, 'HN-F'), ('SN', sn_ids, 'SN-F')]
            for i, node in enumerate(ids)]
manifest = {
    'type': 'manifest', 'schemaVersion': '1.1', 'title': '四核 Linux · 原生 CHI L2',
    'description': 'KMH 实际执行 OpenSBI/Linux；全部 RAM 与页表请求经 4RN/4HN/2SN、3×2 NoC。',
    'source': {'kind': 'gem5_run', 'gem5Commit': '65f3241a6cd486dffddaca251536f5c691af9a1f',
               'generator': 'native/gem5/native_l2_fs.py', 'nativeChi': True, 'fullSystem': True},
    'protocol': 'ARM IHI 0050 Issue H', 'profile': 'h-native-256', 'lineBytes': 64,
    'dataWidth': 256, 'topology': topology, 'complete': False, 'initialState': {},
    'clock': {'tickHz': '1000000000000'}, 'simulationSeed': args.seed,
    'nativeL2': {'sets': args.sets, 'assoc': args.assoc, 'mshrs': args.mshrs,
                 'classicL2': False, 'protocolOpcodeInjection': False},
    'privateCaches': {'l1InstructionBytes': 32768, 'l1DataBytes': 32768,
                       'instructionWalkerBytes': 4096, 'dataWalkerBytes': 4096},
    'linux': {'firmware': str(firmware), 'payload': payload, 'commandLine': args.command_line,
              'ramStart': hex(ram_start), 'ramBytes': ram_bytes, 'dtbAddress': hex(dtb_addr),
              'dtbReservedBytes': dtb_window, 'dtbSha256': sha256(out / 'device.dtb'),
              'configuredHarts': 4, 'numa': numa, 'dmaDevices': [],
              'numaMeaning': 'Linux logical placement; CHI SAM and physical NoC are unchanged',
              'pageTablePath': 'walker -> private writable walker cache -> L1 bus -> ChiNativeL2 -> NoC -> HN -> SN -> RAM',
              'deviceIOPath': 'L1 bus -> independent IOXBar -> CLINT/PLIC/UART; no CHI Device conversion',
              'deviceIORanges': [{'start': hex(int(r.start)), 'bytes': int(r.size())} for r in io_ranges],
              'pci': 'HiFive config aperture connected; no advertised PCI or DMA devices'},
    'sam': {'description': 'RN→HN PA[6],PA[7]; HN→SN PA[6] XOR PA[12]',
            'homeNodeIds': [f'HN{i}' for i in range(4)], 'hashMasks': ['64', '128']},
    'fabric': {'routers': [{'id': f'R{x}{y}', 'x': x, 'y': y, 'object': f'system.routers{y*3+x}'}
                            for y in range(2) for x in range(3)], 'links': links,
               'attachments': [{'node': f'{prefix}{i}', 'router': f'R{x}{y}'}
                               for prefix in ('RN', 'HN') for i, (x, y) in enumerate(corners)]
                              + [{'node': f'SN{i}', 'router': f'R1{i}'} for i in range(2)]},
    'endpointPorts': {**{f'system.l2{i}.chi_side': node for i, node in enumerate(rn_ids)},
                      **{f'system.hn{i}.rxport': node for i, node in enumerate(hn_ids)},
                      **{f'system.sn{i}.chi_side': node for i, node in enumerate(sn_ids)}},
}
manifest['linux']['zicbom'] = {'enabled': args.zicbom, 'cbomBlockBytes': 64 if args.zicbom else None}
manifest['linux']['uncachedTestRam'] = None if uncached_addr is None else {
    'start': hex(uncached_addr), 'bytes': 4096, 'kind': 'normal-noncacheable-ram',
    'cacheableAlias': False, 'reservedNoMap': True, 'Device': False, 'MemAttr': 0,
    'Order': 2, 'ExpCompAck': False,
    'path': 'CPU ld/sd -> L1 -> ChiNativeL2 -> NoC -> HN -> SN -> RAM'}
settings = out / 'flowgraph-config.json'
settings.write_text(json.dumps({'manifest': manifest}, ensure_ascii=False, indent=2) + '\n')
os.environ['CHI_FLOWGRAPH_CONFIG'] = str(settings)
# CHI_FLOWGRAPH_OUT is opt-in; a full boot otherwise creates an enormous trace.
if args.no_census:
    os.environ.pop('CHI_FLOWGRAPH_COUNTS', None)
else:
    os.environ['CHI_FLOWGRAPH_COUNTS'] = str(out / 'native-chi-counts.json')
binary_sha256 = sha256(Path('/proc/self/exe'))
stage_gate = None
if args.suite_pipeline:
    addToPath(str(Path(__file__).resolve().parent))
    from linux_stage_gate import LinuxStageGate
    stage_gate = LinuxStageGate(Path(__file__).resolve().parents[2], out, args.suite_inputs)
m5.instantiate()
wall_start = time.monotonic()
progress_file = out / 'native-linux-progress.jsonl'
terminal_file = out / 'system.platform.terminal'
end_tick = m5.curTick() + args.max_ticks
event = None
pipeline_complete = False
pipeline_error = None
while m5.curTick() < end_tick:
    event = m5.simulate(min(args.progress_ticks, end_tick - m5.curTick()))
    progress = {'tick': str(m5.curTick()), 'cause': event.getCause(), 'exitCode': event.getCode(),
                'hostElapsedSeconds': round(time.monotonic() - wall_start, 3),
                'terminalBytes': terminal_file.stat().st_size if terminal_file.exists() else 0}
    with progress_file.open('a') as output:
        output.write(json.dumps(progress) + '\n')
    print('NATIVE_LINUX_PROGRESS', json.dumps(progress), flush=True)
    if stage_gate and event.getCode() == 0 and event.getCause().startswith('m5_exit instruction encountered'):
        try:
            pipeline_complete = stage_gate.accept(terminal_file.read_text(errors='replace'))
        except RuntimeError as error:
            pipeline_error = str(error)
            print('NATIVE_LINUX_STAGE_FAILURE', pipeline_error, flush=True)
            break
        print('NATIVE_LINUX_STAGE_PASSED', stage_gate.results[-1]['stage'], flush=True)
        m5.stats.dump()
        if not pipeline_complete:
            continue
    if event.getCause() != 'simulate() limit reached':
        break
cause = event.getCause() if event else 'no simulation interval'
code = event.getCode() if event else -1
serial = terminal_file.read_text(errors='replace') if terminal_file.exists() else ''
serial_lines = [line.strip() for line in serial.replace('\r', '').splitlines()]
evidence = {'onlineCpus': 'CHI_LINUX_ONLINE_CPUS=0-3' in serial_lines,
            'onlineNodes': ('CHI_LINUX_ONLINE_NODES=' + ('0-1' if args.numa_nodes == 2 else '0')) in serial_lines,
            'bootMarker': 'CHI_LINUX_BOOT_PASS' in serial_lines}
exit_ok = code == 0 and cause.startswith('m5_exit instruction encountered')
boot_pass = exit_ok and all(evidence.values())
result = {'tick': str(m5.curTick()), 'cause': cause, 'exitCode': code,
          'status': 'boot-passed' if boot_pass else ('incomplete' if cause == 'simulate() limit reached' else 'not-verified'),
          'bootPass': boot_pass, 'fullSystem': True, 'configuredHarts': 4,
          'numaNodes': args.numa_nodes, 'bootEvidence': evidence,
          'guestSuitePass': None, 'guestSuiteNote': 'Suite results require their independent per-test checker',
          'protocolOpcodeInjection': False, 'timingRamPath': 'KMH -> private caches -> ChiNativeL2 -> NoC -> HN -> SN -> RAM',
          'walkerPath': manifest['linux']['pageTablePath'], 'deviceIOPath': manifest['linux']['deviceIOPath'],
          'terminalFile': str(terminal_file), 'simulationSeed': args.seed,
          'firmwareSha256': payload['sha256'], 'dtbSha256': sha256(out / 'device.dtb')}
result['gem5Sha256'] = binary_sha256
result['transferCensusFile'] = None if args.no_census else str(out / 'native-chi-counts.json')
result['suitePipeline'] = {'requested': bool(stage_gate), 'completed': pipeline_complete,
                          'error': pipeline_error, 'stages': stage_gate.results if stage_gate else [],
                          'scope': 'Core Linux suites; guest kernel build has a separate rootfs stage'}
if stage_gate:
    result['guestSuitePass'] = True if pipeline_complete else (False if pipeline_error else None)
    result['status'] = 'suite-passed' if pipeline_complete else ('failed' if pipeline_error else 'incomplete')
(out / 'native-linux-result.json').write_text(json.dumps(result, indent=2) + '\n')
m5.stats.dump()
print('NATIVE_LINUX_RESULT', json.dumps(result), flush=True)
if code != 0 or cause.startswith('m5_fail'):
    raise RuntimeError(f'Linux guest reported failure: {cause}, code={code}')
if pipeline_error:
    raise RuntimeError(pipeline_error)
if stage_gate and not pipeline_complete:
    sys.exit(2)  # A time limit or interrupted pipeline never exits successfully.

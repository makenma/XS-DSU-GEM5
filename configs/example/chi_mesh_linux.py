"""Two timing CPUs, two CMN-configured HNFs and one SNF on a 2x2 CHI mesh.

Only firmware/DTB loading uses the SNF functional port. CPU instructions,
data and page-table walks all use their RNF and the credit-controlled mesh.
MMIO uses the platform bus. Native checkpoints drain the complete fabric.
"""
import argparse
import json
from pathlib import Path
import time

import m5
from m5.objects import (
    AddrRange, Bridge, ChiRnfBridge, ChiRouterRefModel, ChiSnfBridge,
    HiFive, HomeNodeFull, IOXBar, PMAChecker, RiscvBareMetal, RiscvRTC,
    RiscvTimingSimpleCPU, Root, SlcSf, SrcClockDomain, System, SystemXBar,
    VoltageDomain,
)
from m5.params import NULL
from m5.util.fdthelper import (
    Fdt, FdtNode, FdtPropertyStrings, FdtPropertyWords, FdtState,
)


def device_tree(system, destination, command_line):
    state = FdtState(addr_cells=2, size_cells=2, cpu_cells=1)
    tree = FdtNode("/")
    tree.append(state.addrCellsProperty())
    tree.append(state.sizeCellsProperty())
    tree.appendCompatible(["riscv-virtio"])
    tree.append(FdtPropertyStrings("model", "gem5 CHI 2x2 mesh"))
    for memory in system.mem_ranges:
        node = FdtNode("memory@%x" % int(memory.start))
        node.append(FdtPropertyStrings("device_type", "memory"))
        node.append(FdtPropertyWords("reg", state.addrCells(memory.start) +
                                     state.sizeCells(memory.size())))
        tree.append(node)
    for component in [*system.cpu, system.platform]:
        for node in component.generateDeviceTree(state):
            if node.get_name() == tree.get_name():
                tree.merge(node)
            else:
                tree.append(node)
    soc = tree[tree.index("soc")]
    uart = soc[soc.index("uart@10000000")]
    # The supplied OpenSBI matches ns16550a; gem5's generated ns8250-only
    # compatible string is not recognized by that firmware console driver.
    uart.appendCompatible(["ns16550a", "ns8250"])
    chosen = FdtNode("chosen")
    chosen.append(FdtPropertyStrings("stdout-path", "/soc/uart@10000000"))
    chosen.append(FdtPropertyStrings("bootargs", command_line))
    tree.append(chosen)
    fdt = Fdt()
    fdt.add_rootnode(tree)
    fdt.writeDtsFile(str(destination.with_suffix(".dts")))
    fdt.writeDtbFile(str(destination))
    # This firmware unconditionally selects its embedded DTB. Load the new
    # tree over that reserved image after loading the ELF, without patching
    # firmware instructions. OpenSBI relocates the tree for Linux itself.
    if destination.stat().st_size > 0x21D8:
        raise RuntimeError("Generated DTB exceeds the firmware's reserved DTB")


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--kernel", type=Path, required=True)
parser.add_argument("--restore", type=Path)
parser.add_argument("--bare-test", action="store_true")
parser.add_argument("--checkpoint-dir", type=Path)
parser.add_argument("--stop-at-checkpoint", action="store_true")
parser.add_argument("--exit-marker", help="Stop after this text appears on the guest serial console")
parser.add_argument("--max-ticks", type=int, default=30_000_000_000_000)
parser.add_argument("--poll-ticks", type=int, default=1_000_000_000)
parser.add_argument("--rnf-lines", type=int, default=512)
parser.add_argument("--sf-sets", type=int, default=4096)
parser.add_argument("--slc-sets", type=int, default=1024)
parser.add_argument("--sn-latency", type=int, default=20)
args = parser.parse_args()
if args.max_ticks <= 0 or args.poll_ticks <= 0:
    parser.error("max-ticks and poll-ticks must be positive")
out = Path(m5.options.outdir).resolve()
out.mkdir(parents=True, exist_ok=True)

system = System(mem_mode="timing", cache_line_size=64)
system.voltage_domain = VoltageDomain()
system.clk_domain = SrcClockDomain(clock="1GHz", voltage_domain=system.voltage_domain)
system.mem_ranges = [AddrRange(0x80000000, size="128MiB")]
system.enable_difftest = False
system.map_to_raw_cpt = False
system.restore_from_gcpt = False
system.platform = HiFive()
system.platform.rtc = RiscvRTC(frequency="10MHz")
system.platform.clint.int_pin = system.platform.rtc.int_pin
system.platform.setNumCores(2)
system.platform.attachPlic()
system.iobus = IOXBar()
system.platform.attachOnChipIO(system.iobus)
system.platform.attachOffChipIO(system.iobus)
system.platform.pci_host.pio = system.iobus.mem_side_ports

system.routers = [ChiRouterRefModel(
    clk_domain=system.clk_domain, node_type="router", local_x=x, local_y=y,
) for y in range(2) for x in range(2)]
# A link has two independent physical directions. Only the primary lane is
# enabled; unused EXT endpoints remain unconnected.
for left, right in ((0, 1), (2, 3)):
    system.routers[left].internal_ports[0] = system.routers[right].internal_peer_ports[2]
    system.routers[right].internal_ports[2] = system.routers[left].internal_peer_ports[0]
for lower, upper in ((0, 2), (1, 3)):
    system.routers[lower].internal_ports[3] = system.routers[upper].internal_peer_ports[1]
    system.routers[upper].internal_ports[1] = system.routers[lower].internal_peer_ports[3]

system.hnfs = [HomeNodeFull(
    clk_domain=system.clk_domain, node_type="hnf", x=x, y=1,
    port=0, device=0, rnf_num=2, rnf_node_ids=[0, 128],
    sn_node_id=148, pocq_entry_num=16,
    slcsf=SlcSf(clk_domain=system.clk_domain, rnf_num=2,
                home_count=2, home_index=x,
                l3_tag_sets=args.slc_sets, l3_tag_ways=16,
                sf_tag_sets=args.sf_sets, sf_tag_ways=16, cache_line_size=64),
) for x in range(2)]
for i, hnf in enumerate(system.hnfs):
    system.routers[i + 2].local_ports[0] = hnf.rxport
system.snf = ChiSnfBridge(
    clk_domain=system.clk_domain, range=system.mem_ranges[0],
    node_id=148, response_latency=args.sn_latency,
)
system.snf.chi = system.routers[3].device_ports[4]
system.system_port = system.snf.functional

system.rnfs = [ChiRnfBridge(
    clk_domain=system.clk_domain, node_id=node,
    hnf_ids=[16, 144], range=system.mem_ranges[0], cache_lines=args.rnf_lines,
) for node in (0, 128)]
system.cpu = [RiscvTimingSimpleCPU(
    cpu_id=i, clk_domain=system.clk_domain, arch_db=NULL, enable_difftest=False,
) for i in range(2)]
system.cpu_buses = [SystemXBar(snoop_filter=NULL) for _ in range(2)]
io_ranges = system.platform._on_chip_ranges() + system.platform._off_chip_ranges()
io_ranges.append(AddrRange(system.platform.pci_host.conf_base,
                          size=system.platform.pci_host.conf_size))
system.io_bridges = [Bridge(delay="1ns", ranges=io_ranges) for _ in range(2)]
for i, cpu in enumerate(system.cpu):
    cpu.createThreads()
    cpu.createInterruptController()
    cpu.mmu.pma_checker = PMAChecker(uncacheable=io_ranges)
    cpu.connectBus(system.cpu_buses[i])
    system.rnfs[i].cpu_side = system.cpu_buses[i].mem_side_ports
    system.rnfs[i].chi = system.routers[i].device_ports[0]
    system.io_bridges[i].cpu_side_port = system.cpu_buses[i].mem_side_ports
    system.io_bridges[i].mem_side_port = system.iobus.cpu_side_ports

system.workload = RiscvBareMetal(bootloader=str(args.kernel.resolve()),
                               bare_metal=args.bare_test)
if not args.bare_test:
    dtb = out / "device.dtb"
    device_tree(system, dtb,
        "console=ttyS0,115200 earlycon=sbi loglevel=7 rdinit=/init "
        "initcall_blacklist=check_unaligned_access_all_cpus")
    system.workload.dtb_filename = str(dtb)
    system.workload.dtb_addr = 0x8001B000
root = Root(full_system=True, system=system)
m5.instantiate(str(args.restore.resolve()) if args.restore else None)

start_tick = m5.curTick()
start_wall = time.monotonic()
terminal = out / "system.platform.terminal"
checkpoint = args.checkpoint_dir or (out / "cpt.login")
result = {"topology": "2x2 mesh, 2 CPU/RNF, 2 HNF, 1 SNF",
          "kernel": str(args.kernel.resolve()), "restore": str(args.restore) if args.restore else None,
          "start_tick": start_tick, "status": "running"}
(out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
next_progress = start_wall + 30
while m5.curTick() - start_tick < args.max_ticks:
    duration = min(args.poll_ticks, args.max_ticks - (m5.curTick() - start_tick))
    event = m5.simulate(duration)
    cause = event.getCause()
    console = terminal.read_text(errors="replace") if terminal.exists() else ""
    if "Kernel panic" in console:
        raise RuntimeError("Linux kernel panic; inspect " + str(terminal))
    if args.exit_marker and args.exit_marker in console:
        result.update(status="console_marker", marker=args.exit_marker)
        break
    login = not args.bare_test and not args.restore and "gem5-chi login:" in console
    if login or cause == "checkpoint":
        checkpoint = checkpoint.resolve()
        if checkpoint.exists():
            raise RuntimeError("Refusing to overwrite checkpoint: " + str(checkpoint))
        m5.checkpoint(str(checkpoint))
        result.update(checkpoint=str(checkpoint), checkpoint_tick=m5.curTick(),
                      status="login_checkpoint" if login else "checkpoint")
        print("CHI_MESH_CHECKPOINT", json.dumps(result), flush=True)
        if login or args.stop_at_checkpoint:
            break
    elif cause != "simulate() limit reached":
        result.update(status="guest_exit", cause=cause, code=event.getCode())
        if event.getCode() != 0:
            raise RuntimeError("Guest failure: " + cause)
        break
    now = time.monotonic()
    if now >= next_progress:
        print("CHI_MESH_PROGRESS tick=%d wall=%.1fs" % (m5.curTick(), now - start_wall), flush=True)
        next_progress = now + 30
else:
    result["status"] = "restore_ran" if args.restore else "timeout"
result.update(end_tick=m5.curTick(), wall_seconds=time.monotonic() - start_wall)
m5.stats.dump()
(out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print("CHI_MESH_RESULT", json.dumps(result), flush=True)
if result["status"] == "timeout":
    raise RuntimeError("Linux/test did not finish before max-ticks")

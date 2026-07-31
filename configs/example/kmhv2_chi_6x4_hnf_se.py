"""Run a static RISC-V Linux ELF through the figure-23.2 6x4 CHI mesh.

This syscall-emulation entry point is intended for reproducible open-source
proxy workloads.  It uses the same 24 routers, 16 HN-Fs, CMN-style address
hash, post-L2 RN bridge, and shared classic-memory SN adapter as
``kmhv2_chi_6x4_hnf.py`` without requiring a full-system GCPT.
"""

import argparse
import os
import shlex

from m5.objects import (
    AddrRange,
    Cache2ChiBridge,
    Chi2ClassicMemBridge,
    ChiRouterRefModel,
    DecoupledBPUWithBTB,
    HomeNodeFull,
    Process,
    Root,
    SEWorkload,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util import addToPath, fatal

addToPath("../")

from common import CacheConfig, MemConfig, Options, Simulation
from common.FSConfig import MemBus
from common.xiangshan import XiangshanCore
from example.noc_config.chi_6x4_hnf import (
    HNF_NODE_IDS,
    MESH_COLUMNS,
    MESH_ROWS,
    chi_node_id,
)


def _process(args):
    binary = os.path.abspath(args.cmd)
    if not os.path.isfile(binary):
        fatal("--cmd must name an existing static RISC-V ELF: %s", binary)

    process = Process(pid=100)
    process.executable = binary
    workload_cwd = os.path.abspath(args.workload_cwd)
    if not os.path.isdir(workload_cwd):
        fatal("--workload-cwd must name an existing directory: %s", workload_cwd)
    process.cwd = workload_cwd
    process.gid = os.getgid()
    process.cmd = [binary] + (shlex.split(args.options) if args.options else [])
    if args.input:
        process.input = args.input
    if args.output:
        process.output = args.output
    if args.errout:
        process.errout = args.errout
    if args.env:
        with open(args.env, encoding="utf-8") as source:
            process.env = [line.rstrip() for line in source]
    return binary, process


def _parse_args():
    parser = argparse.ArgumentParser()
    Options.addCommonOptions(parser, configure_xiangshan=True)
    Options.addXiangshanCommonOptions(parser)
    Options.addSEOptions(parser)
    parser.add_argument(
        "--workload-cwd",
        default=os.getcwd(),
        help="guest process working directory (use a per-run directory)",
    )
    parser.add_argument(
        "--slc-replacement-policy",
        choices=("lru", "lsu", "pseudo_random"),
        default="lru",
        help="HN-F SLC replacement policy; lsu is the compatibility LRU alias",
    )
    parser.add_argument(
        "--slc-replacement-seed",
        type=lambda value: int(value, 0),
        default=1,
        help="deterministic pseudo-random replacement seed",
    )
    return parser.parse_args()


def _build_system(args, binary, process):
    if args.num_cpus != 1:
        fatal("the public proxy workload entry point currently requires -n 1")
    if args.external_memory_system or args.ruby:
        fatal("the 6x4 HNF SE entry point requires classic in-process memory")
    if args.classic_l2:
        fatal("the 6x4 HNF SE entry point requires the aligned L2 wrapper")

    # Match figure 23.2 and the full-system entry point.
    args.sys_clock = "1.8GHz"
    args.cpu_clock = "2.3GHz"
    args.kmh_align = True
    args.caches = True
    args.l2cache = True
    args.l1_to_l2_pf_hint = True
    args.l3cache = False
    args.no_l3cache = True
    args.chi_test_mode = True
    args.chi_6x4_hnf_router_test_mode = True
    args.chi_2x2_router_test_mode = False
    args.enable_difftest = False
    args.xiangshan_system = True
    args.cpu_type = "RiscvO3CPU"
    if args.bp_type not in (None, "DecoupledBPUWithBTB"):
        fatal(
            "XiangShan SE mode supports only --bp-type=DecoupledBPUWithBTB"
        )
    args.bp_type = "DecoupledBPUWithBTB"

    system = System(
        cpu=[XiangshanCore(cpu_id=0)],
        mem_mode="timing",
        mem_ranges=[AddrRange(args.mem_size)],
        cache_line_size=args.cacheline_size,
    )
    system.num_cpus = 1
    system.xiangshan_system = True
    system.enable_difftest = False
    system.enable_riscv_vector = True

    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=args.sys_clock, voltage_domain=system.voltage_domain
    )
    system.cpu_voltage_domain = VoltageDomain()
    system.cpu_clk_domain = SrcClockDomain(
        clock=args.cpu_clock, voltage_domain=system.cpu_voltage_domain
    )
    system.cpu[0].clk_domain = system.cpu_clk_domain
    system.cpu[0].branchPred = DecoupledBPUWithBTB(bpDBSwitches=[])
    system.cpu[0].branchPred.isDumpMisspredPC = True
    system.cpu[0].store_prefetch_train = False
    system.cpu[0].enable_storeSet_train = False
    system.cpu[0].enable_riscv_vector = True

    system.chi_bridges = [Cache2ChiBridge()]
    system.home_node = [HomeNodeFull() for _ in HNF_NODE_IDS]
    system.chi_routers = [
        ChiRouterRefModel(local_x=x, local_y=y, node_type="router")
        for y in range(MESH_ROWS)
        for x in range(MESH_COLUMNS)
    ]
    system.snf_bridge = Chi2ClassicMemBridge()
    system._chi_router_6x4_snf_node_id = chi_node_id(5, 0, 0, 0)

    system.workload = SEWorkload.init_compatible(binary)
    system.cpu[0].workload = process
    system.cpu[0].createThreads()

    system.membus = MemBus()
    system.system_port = system.membus.cpu_side_ports
    CacheConfig.config_cache(args, system)
    MemConfig.config_mem(args, system)
    return system


if __name__ == "__m5_main__":
    args = _parse_args()
    binary, process = _process(args)
    test_sys = _build_system(args, binary, process)

    print("CHI figure-23.2 6x4 SE topology:")
    print("  Routers:    %d (%dx%d mesh)" %
          (MESH_COLUMNS * MESH_ROWS, MESH_COLUMNS, MESH_ROWS))
    print("  HN-Fs:      %d" % len(HNF_NODE_IDS))
    print("  SLC policy: %s (seed=%d)" %
          (args.slc_replacement_policy, args.slc_replacement_seed))
    print("  Workload:   %s %s" % (binary, args.options))
    print("  Clocks:     CPU %s, Router/HN-F %s" %
          (args.cpu_clock, args.sys_clock))

    root = Root(full_system=False, system=test_sys)
    exit_event = Simulation.run_vanilla(args, root, test_sys, None)
    if exit_event.getCode() != 0:
        fatal(
            "guest workload exited with code %d (%s)",
            exit_event.getCode(),
            exit_event.getCause(),
        )

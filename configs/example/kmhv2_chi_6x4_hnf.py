"""Runnable XiangShan CHI configuration for figure 23.2's 6x4 mesh.

The modeled NoC contains exactly 24 routers and 16 HN-Fs.  A post-L2 RN-F
bridge and a classic-memory SN adapter are boundary plumbing needed to run
real workloads; they are not treated as additional figure-23.2 model nodes.
"""

from m5.objects import Root
from m5.util import addToPath

addToPath("../")

from common import Simulation
from common.xiangshan import build_xiangshan_system, xiangshan_system_init
from example.noc_config.chi_6x4_hnf import (
    CMN_16_HNF_XOR_MASKS,
    HNF_ATTACHMENTS,
    HNF_NODE_IDS,
    MESH_COLUMNS,
    MESH_ROWS,
)


if __name__ == "__m5_main__":
    args = xiangshan_system_init()
    args.enable_difftest = False

    # Figure 23.2 fixes the CPU and CMN/HN-F clock domains at 2.3 GHz and
    # 1.8 GHz respectively. Routers and HN-Fs inherit the system domain;
    # XiangShan cores use the CPU domain.
    args.sys_clock = "1.8GHz"
    args.cpu_clock = "2.3GHz"

    args.l2_wrapper_hwp_type = "L2CompositeWithWorkerPrefetcher"
    args.kmh_align = True
    args.chi_test_mode = True
    args.chi_6x4_hnf_router_test_mode = True
    args.l3cache = False
    args.no_l3cache = True

    if args.external_memory_system:
        raise RuntimeError("CHI 6x4 HNF mode requires the classic memory bus")
    if getattr(args, "ruby", False):
        raise RuntimeError("CHI 6x4 HNF mode is a classic-cache configuration")
    if args.classic_l2:
        raise RuntimeError("CHI 6x4 HNF mode requires the aligned L2 wrapper")
    if not 1 <= args.num_cpus <= 4:
        raise RuntimeError("CHI 6x4 HNF mode supports one to four CPUs")

    FutureClass = None

    test_sys = build_xiangshan_system(args)

    print("CHI figure-23.2 6x4 topology:")
    print("  Routers:    %d (%dx%d mesh)" %
          (MESH_COLUMNS * MESH_ROWS, MESH_COLUMNS, MESH_ROWS))
    print("  HN-Fs:      %d, each on its router P1/D0" % len(HNF_ATTACHMENTS))
    print("  HNF IDs:    " + ", ".join("%#x" % node for node in HNF_NODE_IDS))
    print("  CMN masks:  " +
          ", ".join("%#014x" % mask for mask in CMN_16_HNF_XOR_MASKS))
    print("  SLC policy: %s (seed=%d)" %
          (args.slc_replacement_policy, args.slc_replacement_seed))
    print("  Clocks: CPU %s, Router/HN-F %s" %
          (args.cpu_clock, args.sys_clock))
    print("  RN boundary: CPU n at west-edge router(0,n) P0/D0")
    print("  Memory boundary: shared SN adapter at router(5,0) P0/D0 -> DDR")

    root = Root(full_system=True, system=test_sys)
    Simulation.run_vanilla(args, root, test_sys, FutureClass)

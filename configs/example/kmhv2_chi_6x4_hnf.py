"""Runnable XiangShan CHI configuration for figure 23.2's 6x4 mesh.

The modeled NoC contains exactly 24 routers and 16 HN-Fs.  A post-L2 RN-F
bridge and a classic-memory SN adapter are boundary plumbing needed to run
real workloads; they are not treated as additional figure-23.2 model nodes.
"""

import os

from m5.objects import Root
from m5.util import addToPath

addToPath("../")

from common import Simulation
from common.xiangshan import (
    build_xiangshan_system,
    configure_xiangshan_linux_workload,
    xiangshan_system_init,
)
from example.noc_config.chi_6x4_hnf import (
    CMN_16_HNF_XOR_MASKS,
    HNF_ATTACHMENTS,
    HNF_NODE_IDS,
    MESH_COLUMNS,
    MESH_ROWS,
    format_topology_summary,
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
    if not 1 <= args.num_cpus <= 16:
        raise RuntimeError("CHI 6x4 HNF mode supports 1 to 16 CPUs")

    FutureClass = None

    test_sys = build_xiangshan_system(args)
    # This model uses classic caches behind the CHI adapters.  Do not issue
    # load consumers before the cache response is known to be available:
    # the O3 selective load-cancel path cannot recover an already-issued
    # consumer on a miss, which can expose stale operands to firmware/Linux.
    for cpu in test_sys.cpu:
        cpu.EnableLoadSpecWakeup = False
    # Match kmhv3.py for raw linux.bin images which accept an external DTB.
    # Firmware payloads with an embedded FW_FDT keep using their own DTB.
    if (args.raw_cpt and args.generic_rv_cpt and
            os.path.basename(args.generic_rv_cpt) == "linux.bin"):
        configure_xiangshan_linux_workload(test_sys, args)

    print(format_topology_summary())
    print("  SLC policy: %s (seed=%d)" %
          (args.slc_replacement_policy, args.slc_replacement_seed))
    print("  Runtime clocks: CPU %s, Router/HN-F %s" %
          (args.cpu_clock, args.sys_clock))

    root = Root(full_system=True, system=test_sys)
    Simulation.run_vanilla(args, root, test_sys, FutureClass)

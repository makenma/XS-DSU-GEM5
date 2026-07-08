import sys

import m5
from m5.objects import *
from m5.util import addToPath

addToPath("../")

from common import Simulation
from common.xiangshan import *


def chi_node_id(x, y, p=0, d=0):
    return ((x & 0xF) << 7) | ((y & 0x7) << 4) | ((p & 0x3) << 2) | (d & 0x3)


if __name__ == "__m5_main__":
    args = xiangshan_system_init()
    args.enable_difftest = False

    args.l2_wrapper_hwp_type = "L2CompositeWithWorkerPrefetcher"
    args.kmh_align = True
    args.chi_test_mode = True
    args.chi_2x2_router_test_mode = True

    assert not args.external_memory_system

    if args.xiangshan_ecore:
        FutureClass = None
        args.cpu_clock = "2.4GHz"
    else:
        FutureClass = None

    test_sys = build_xiangshan_system(args)

    print("CHI 2x2 router smoke topology:")
    print("  RNF bridges: router(0,0) P[core] D[slice], "
          "same-core slices share SrcID")
    print("  SN bridge:   router(1,0) P0 D0 node_id=%#x -> classic cache -> DDR" %
          chi_node_id(1, 0, 0, 0))
    print("  HNF:         router(1,1) P0 D0 node_id=%#x" %
          chi_node_id(1, 1, 0, 0))

    root = Root(full_system=True, system=test_sys)

    Simulation.run_vanilla(args, root, test_sys, FutureClass)

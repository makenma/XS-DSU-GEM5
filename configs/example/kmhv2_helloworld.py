import argparse
from platform import system
import sys

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath, fatal, warn
from m5.util.fdthelper import *

addToPath('../')

from ruby import Ruby

from common.FSConfig import *
from common.SysPaths import *
from common.Benchmarks import *
from common import Simulation
from common.Caches import *
from common.xiangshan import *



if __name__ == '__m5_main__':

    args = xiangshan_system_init()
    args.enable_difftest = False

    # l1cache prefetcher use stream, stride
    # l2cache prefetcher use pht, bop, cmc
    # disable l1prefetcher store pf train
    # disable l1 berti, l2 cdp
    args.l2_wrapper_hwp_type = "L2CompositeWithWorkerPrefetcher"
    args.kmh_align = True

    assert not args.external_memory_system

    test_mem_mode = 'timing'

    # override cpu class and clock
    if args.xiangshan_ecore:
        FutureClass = None
        args.cpu_clock = '2.4GHz'
    else:
        FutureClass = None
    print(args.xiangshan_ecore )
    print(args.no_l3cache)
    print("L2 cache is " + str(args.l2cache))
    args.chi_test_mode = True
    test_sys = build_xiangshan_system(args)

    # test_sys.l2_caches[0].mem_side = test_sys.chi_bridge.cache_side    # L2's output -> bridge input
    # test_sys.chi_bridge.chi_side = test_sys.membus.slave   # bridge output -> main memory bus

    root = Root(full_system=True, system=test_sys)

    Simulation.run_vanilla(args, root, test_sys, FutureClass)

    # #########################################################################
    # hello = args.binary

    # root = Root(full_system=False, system=test_sys)

    # test_sys.workload = SEWorkload.init_compatible(hello)

    # p = Process()
    # p.cmd = [hello]

    # cpus = []
    # if hasattr(test_sys, "cpu"):
    #     cpus = test_sys.cpu if isinstance(test_sys.cpu, list) else [test_sys.cpu]
    # elif hasattr(test_sys, "cpus"):
    #     cpus = test_sys.cpus

    # for c in cpus:
    #     c.workload = p
    #     c.createThreads()

    # Simulation.run_vanilla(args, root, test_sys, FutureClass)








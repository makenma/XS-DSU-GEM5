"""kmhv2 2x2 CHI mesh with the SNF replaced by the C2XM co-simulation.

Identical topology to kmhv2_chi_2x2_router.py, except the SN-F position
(router (1,0)) is held by ChiCosimBridge: CHI flits are shipped to a
pyuvm/VCS testbench over a UNIX socket, the testbench drives the C2XM
CHI-to-AXI RTL, and the RTL's AXI traffic is served from *this* system's
DDR through the bridge's mem_side.  RNF bypass traffic is funnelled
through the bridge too (route configurable).

The two simulators advance in lockstep: every chi_cosim_quantum gem5
cycles the bridge sends a "sync" and blocks until the testbench answers
"ack" (see c2xm_pyuvm_env/cosim_runtime.py).

Usage (start the TB side first, see c2xm_exp/c2xm_pyuvm_env/run_cosim.sh):
    build/RISCV/gem5.opt configs/example/kmhv2_chi_2x2_router_cosim.py \
        [--chi-cosim-socket /tmp/c2xm_cosim.sock] \
        [--chi-cosim-quantum 100] [--chi-cosim-bypass-route all]
"""

import os
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
    args.l3cache = False
    args.no_l3cache = True

    assert not args.external_memory_system

    # ---- co-simulation options (consumed by configs/common/CacheConfig.py).
    # gem5's option parser rejects unknown --flags, so these come through
    # the environment instead.
    args.chi_cosim_socket = os.environ.get("CHI_COSIM_SOCKET",
                                           "/tmp/c2xm_cosim.sock")
    args.chi_cosim_quantum = int(os.environ.get("CHI_COSIM_QUANTUM", "100"))
    args.chi_cosim_bypass_route = os.environ.get("CHI_COSIM_BYPASS_ROUTE",
                                                 "all")

    if args.xiangshan_ecore:
        FutureClass = None
        args.cpu_clock = "2.4GHz"
    else:
        FutureClass = None

    test_sys = build_xiangshan_system(args)

    print("CHI 2x2 router CO-SIM topology:")
    print("  RNF bridges: one per L2 wrapper after its internal xbar, "
          "router(0,0) P[core] D0")
    print("  SN co-sim:   router(1,0) P0 D0 node_id=%#x socket=%s" % (
        chi_node_id(1, 0, 0, 0), args.chi_cosim_socket))
    print("  HNF:         router(1,1) P0 D0 node_id=%#x" %
          chi_node_id(1, 1, 0, 0))
    print("  bypass route: %s, quantum: %d cycles" % (
        args.chi_cosim_bypass_route, args.chi_cosim_quantum))

    root = Root(full_system=True, system=test_sys)

    Simulation.run_vanilla(args, root, test_sys, FutureClass)

import argparse

import m5
from m5.objects import *
from m5.util import addToPath, fatal

addToPath("../")

from common import Options
from ruby import Ruby


parser = argparse.ArgumentParser()
Options.addNoISAOptions(parser)
Ruby.define_options(parser)
parser.set_defaults(
    network="garnet",
    topology="AxiMeshDie",
    routing_algorithm=1,
    mesh_rows=2,
    mem_type="DDR3_1600_8x8",
    garnet_vnet_classes="ctrl,data,ctrl,ctrl,data",
    garnet_buffers_per_vnet="4,8,4,4,8",
)
args = parser.parse_args()

# Ruby's legacy topology interface calls this field num_cpus.  AXI_MESH has
# no CPU; here it is only the number of routers.
args.num_cpus = args.axi_mesh_routers

system = System(mem_ranges=[AddrRange(args.mem_size)])
system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
system.clk_domain = SrcClockDomain(
    clock=args.sys_clock, voltage_domain=system.voltage_domain
)

Ruby.create_system(args, False, system, cpus=[])
system.ruby.clk_domain = SrcClockDomain(
    clock=args.ruby_clock, voltage_domain=system.voltage_domain
)

root = Root(full_system=False, system=system)
root.system.mem_mode = "timing"
m5.ticks.setGlobalFrequency("1ps")
m5.instantiate()

exit_event = m5.simulate(args.axi_max_sim_ticks)
cause = exit_event.getCause()
print("Exiting @ tick", m5.curTick(), "because", cause)

if args.axi_raw_shim_probe:
    expected = "AXI_MESH raw shim ownership probe passed"
    if cause != expected:
        fatal("raw shim probe did not complete: %s", cause)
    print("AXI_MESH_RAW_SHIM_PROBE_PASS")
else:
    expected = "AXI_MESH functional scenario passed"
    if cause != expected:
        fatal("AXI functional scenario did not complete: %s", cause)
    print("AXI_MESH_FUNCTIONAL_SCENARIO_PASS")

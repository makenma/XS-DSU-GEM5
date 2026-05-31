import optparse
import sys
import os

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import addToPath
addToPath('../configs')


from common.FSConfig import *
from common.SysPaths import *
from common.Benchmarks import *
from common import Simulation
from common.Caches import *
from common.xiangshan import *
# KMHv2 core model (Xiangshan E-core 2-read config)
# Make sure xiangshan.py is reachable in PYTHONPATH (we add config_root below).
from common.xiangshan import XiangshanECore2Read
#Define DDR imports
from m5.objects import MemCtrl, DDR4_2400_16x4

config_path = os.path.dirname(os.path.abspath(__file__))
config_root = os.path.join(config_path, '../')
print("config_root:", config_root)
sys.path.append(config_root)
sys.path.append(os.path.join(config_root, 'configs'))
NocConfigPath = os.path.join(config_root, 'configs','example','noc_config','noc_2x4.py')
sys.path.append(os.path.join(config_root, 'configs','example','noc_config'))



from common import Options
from common import Simulation
from ruby import Ruby
import ruby
import argparse
import noc_2x4 as NocConfig
from ruby import CHI_config

from gem5.resources.resource import BinaryResource

def get_parser():
    # 基于原始选项，但针对 2x4 配置进行定制
    parser = argparse.ArgumentParser()
    Options.addCommonOptions(parser)

    # 添加二进制文件选项
    parser.add_argument(
        "--binary",
        type=str,
        default="tests/test-progs/hello/bin/riscv/linux/hello",
        help="要运行的二进制文件路径"
    )

    # 选择 CPU core 模型
    parser.add_argument(
        "--core-model",
        type=str,
        choices=["simple", "kmhv2"],
        default="kmhv2",
        help="simple=TimingSimpleCPU; kmhv2=XiangshanECore2Read (RiscvO3CPU)"
    )

    # 添加 Ruby 选项
    Ruby.define_options(parser)

    # 移除合成流量相关的参数
    # 设置 2x4 特定的默认值
    parser.set_defaults(
        num_cpus=4,                    # 2x4 = 8个节点，这里用4个测试
        num_dirs=2,
        network='garnet',              # 使用 garnet 而不是 garnet2.0
        topology='CustomMesh',         # 使用 Mesh 拓扑
        mesh_rows=2,                   # 2行
        mesh_cols=4,                   # 4列
        protocol='CHI',                # 使用 CHI 协议
        num_l3caches=4,
        vcs_per_vnet=4,
        num_memory=2,
        link_width_bits=512,
        routing_algorithm=1,           # XY 路由
        garnet_deadlock_threshold=500000,
        network_fault_model=None,
        cpu_type='TimingSimpleCPU',    # 使用 TimingSimpleCPU
        mem_size='2GiB',
        num_hnf =4,
        l1i_size='32kB',
        l1d_size='32kB',
        l2_size='256kB',
        l1i_assoc=8,
        l1d_assoc=8,
        l2_assoc=8,
        sys_clock='2GHz',
        ruby_clock='2GHz',
        chi_config=NocConfigPath       # 使用 CHI 协议配置
    )
    return parser

def build_system(options):

    # 创建系统
    system = System()

    # ctrl = MemCtrl()
    # ctrl1 = MemCtrl()
    # # 选一个：
    # ctrl.dram = DDR4_2400_16x4()      # DDR4
    # ctrl1.dram = DDR4_2400_16x4()      # DDR4
    # system.mem_ctrls = [ctrl,ctrl1]  # Classic 模式
    # 创建顶层电压域和时钟域
    system.voltage_domain = VoltageDomain(voltage=options.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock=options.sys_clock,
        voltage_domain=system.voltage_domain
    )


    # 创建内存范围
    system.mem_ranges = [AddrRange(0,size='1GiB'), AddrRange(0x40000000, size='1GiB')]
    system.mem_mode = 'timing'
    # 创建 CPU
    # - simple: TimingSimpleCPU (默认原逻辑)
    # - kmhv2 : XiangshanECore2Read (RiscvO3CPU, 更接近香山/KMH 的配置)
    if options.core_model == "kmhv2":
        system.cpu = [XiangshanECore2Read(clk_domain=system.clk_domain, cpu_id=i)
                      for i in range(options.num_cpus)]
        # Xiangshan/KMH 通常用 RISC-V，确保你编译的 gem5 TARGET_ISA=riscv
        system.mem_mode = 'timing'
    else:
        if options.cpu_type == "TimingSimpleCPU":
            system.cpu = [TimingSimpleCPU() for i in range(options.num_cpus)]
        elif options.cpu_type == "AtomicSimpleCPU":
            system.cpu = [AtomicSimpleCPU() for i in range(options.num_cpus)]
        else:
            system.cpu = [TimingSimpleCPU() for i in range(options.num_cpus)]

    # 为每个 CPU 创建中断控制器
    for i in range(options.num_cpus):
        system.cpu[i].createInterruptController()

    # 创建 Ruby 内存系统（这会处理 CHI 协议）
    print("创建 Ruby 内存系统...")
    Ruby.create_system(options, False, system)


    # for i in range(len(system.ruby.snf)):
    #     system.ruby.snf[i].mem_ctrl = MemCtrl()
    #     system.ruby.snf[i].mem_ctrl.dram = DDR4_2400_16x4()
    #     if i == 0:
    #         system.ruby.snf[i]._cntrl.addr_ranges = AddrRange(0,size='1GiB')
    #     else:
    #         system.ruby.snf[i]._cntrl.addr_ranges = AddrRange(0x40000000, size='1GiB')
    #     for r in system.ruby.snf[i]._cntrl.addr_ranges:
    #         print(f"为 SNF {i} 设置内存控制器地址范围: 0x{r.start} - 0x{r.end}")
    # for i in range(len(system.ruby.rnf)):
    #     system.ruby.rnf[i].generate(options=options, ruby_system=system.ruby,cpus = system.cpu)
    print("连接 CPU 端口到 Ruby 控制器...")
    for i, cpu in enumerate(system.cpu):
        port = system.ruby._cpu_ports[i]
        port.connectCpuPorts(cpu)


        print(f"  ✓ CPU {i} 端口连接完成")

    # 设置 Ruby 时钟域
    system.ruby.clk_domain = SrcClockDomain(
        clock=options.ruby_clock,
        voltage_domain=system.voltage_domain
    )



    print(f"Created Ruby system with {options.num_cpus} CPUs")
    print(f"Using protocol: {options.protocol}")
    print(f"Topology: {options.topology} with {options.mesh_rows}x{options.mesh_cols} mesh")

    return system

def setup_workload(system, binary_path):
    """为所有 CPU 设置工作负载"""
    # 检查二进制文件是否存在
    if not os.path.exists(binary_path):
        print(f"警告: 二进制文件不存在: {binary_path}")
        # 尝试使用默认的 hello 程序
        binary_path = "../tests/test-progs/hello/bin/arm/linux/hello"
        if not os.path.exists(binary_path):
            print(f"默认二进制文件也不存在: {binary_path}")
            return False

    print(f"为所有 CPU 设置工作负载: {binary_path}")



    # 创建进程
    process = Process()
    process.cmd = [binary_path]
    process.executable = binary_path
    process.cwd = os.getcwd()
    process.uid = 100
    process.gid = 100
    process.euid = 100
    process.egid = 100

    # 为每个 CPU 设置相同的工作负载
    for i, cpu in enumerate(system.cpu):
        cpu.workload = process
        cpu.createThreads()
        system.workload = SEWorkload.init_compatible(binary_path)
        print(f"  - CPU {i}: 工作负载设置完成")

    return True



def main():
    parser = get_parser()
    args = parser.parse_args()

    # 构建系统
    system = build_system(args)

    # 设置工作负载
    if not setup_workload(system, args.binary):
        print("错误: 无法设置工作负载，退出")
        return

    # 设置根系统
    root = Root(full_system=False, system=system)

    # 实例化并运行
    print("实例化系统...")
    m5.instantiate()
    for key, value in vars(system.ruby).items():
        print(f"{key}: {value}")

    print("内存控制器地址范围:")
    print(system.ruby.snf[0]._cntrl.addr_ranges)
    for i in range(len(system.ruby.snf)):
        print(f"SNF {i} 地址范围:")
        for r in system.ruby.snf[i]._cntrl.addr_ranges:
            print(f"Range: 0x{r.start} - 0x{r.end} (size: 0x{r.size()} bytes)")



    print("Starting 2x4 NoC simulation with CHI protocol...")
    max_ticks = 100000000000000  # 最大 100M ticks
    exit_event = m5.simulate(max_ticks)



    print(f"Exiting @ tick {m5.curTick()} because {exit_event.getCause()}")

if __name__ == '__m5_main__':
    main()
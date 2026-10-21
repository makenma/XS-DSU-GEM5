# 单次多核与 Mesh 仿真加速资料索引

资料核对日期：2026-10-19。关注单次仿真的宿主执行时间；外部项目的性能数字仅对应其论文配置。

## gem5 并行研究与公开实现

| 项目 | 一手资料 | 适用边界 |
| --- | --- | --- |
| FlowerZhou/gem5-multithread | [源码](https://github.com/FlowerZhou/gem5-multithread)、[CPU 队列分配](https://github.com/FlowerZhou/gem5-multithread/blob/master/configs/example/se.py)、[AtomicCPU](https://github.com/FlowerZhou/gem5-multithread/blob/master/src/cpu/simple/atomic.cc) | 有实际宿主多线程改造的旧原型；README 标记仍在开发。未验证 O3、Garnet 或当前 AXI Mesh 的支持与收益。 |
| parti-gem5 | [2026 期刊论文](https://link.springer.com/article/10.1007/s10766-026-00819-5)、[2023 预印本](https://arxiv.org/abs/2308.09445) | 基于 gem5 21.1.0.2，支持 O3/Minor 与 Ruby。CPU 私有域并行，共享互连仍集中在一个域；评估为 ARM CHI 分层星形拓扑。时间片解耦引入时序偏差。未确认公开仿真器源码入口。 |
| par-gem5 | [作者说明](https://www.chciken.com/simulation/2023/11/11/par-gem5.html) | Atomic 模式；作者明确说明源码不公开。 |
| SplitSim / SimBricks | [GitHub 实验入口](https://github.com/simbricks/conext25-artifact/tree/main/parallel_gem5)、[论文](https://www.comp.nus.edu.sg/~lijl/papers/splitsim-conext25.pdf)、[组件拆分说明](https://www.simbricks.io/blog/parallelizing-with-simbricks.html) | 将同一次多核 gem5 仿真拆为多个进程。论文算术密集实验中，8 核约 5 倍；尚未确认 Ruby/Garnet Mesh 支持。实验入口指向 GitLab 源码，本次未验证源站可下载性。 |

## 网络仿真替代框架

| 项目 | 一手资料 | 适用边界 |
| --- | --- | --- |
| CNSim | [源码](https://github.com/Yinxiao-Feng/chiplet-network-sim)、[USENIX ATC 2024](https://www.usenix.org/conference/atc24/presentation/feng-yinxiao)、[论文](https://www.usenix.org/system/files/atc24-feng-yinxiao.pdf) | 独立的 packet-parallel NoC/Chiplet 仿真器。16K 节点 Dragonfly 实验中，8 线程比自身单线程约快 3 倍；整体相对其他工具的加速不能全归于多线程。并行仲裁具有随机性，不等同于当前 Garnet 的逐事件行为；接入 AXI 语义需要适配。论文复现使用 atc24_artifact 分支。 |
| SST | [Core](https://github.com/sstsimulator/sst-core)、[Kingsley Mesh](https://github.com/sstsimulator/sst-elements/tree/master/src/sst/elements/kingsley)、[Merlin](https://github.com/sstsimulator/sst-elements/tree/master/src/sst/elements/merlin)、[并行运行文档](https://sst-simulator.org/sst-docs/docs/guides/runningSST) | 支持单次仿真的多线程与 MPI 分区。需要迁移或适配模型；[gem5 集成](https://github.com/gem5/gem5/blob/stable/ext/sst/README.md)本身不会自动并行化 gem5 内部 Mesh。 |

## 当前工程的评估入口

- [保留原时序与仲裁的 NoC 多线程可行性](../ai_mesh/noc_multithreading_feasibility.md)。
- [AXI/HBM 端点串行优化方案](../ai_mesh/axi_endpoint_optimization_design.md)。
- 原生并行调度：[simulation loop](../../src/sim/simulate.cc)、[跨队列约束](../../src/sim/eventq.hh)。
- Garnet 分区边界审查：[NetworkLink](../../src/mem/ruby/network/garnet/NetworkLink.cc)、[Consumer](../../src/mem/ruby/common/Consumer.cc)。
- 编译优化：[构建选项](../../SConstruct)、[编译器与构建类型](../../src/SConscript)；外部参考：[ISPASS 2026 PGO 实验](https://github.com/ece-fast-lab/ISPASS-2026-Gem5-PGO)。PGO 应采集本工程代表性 Mesh 负载，不能复用其他模型的收益承诺。
- FULL_TIMING、FAST_EVENT、WINDOWED_TIMING 与校准：[统一测试索引](../ai_mesh/mesh_ir_test_commands.md#gate-8-有限聚合网络)、[模式 schema](../../schemas/ai_mesh/agent_runtime_config_v1.schema.json)、[runner](../../configs/example/ai_mesh/run_gate6_serving.py)。
- 窗口模式拓扑与链路适用约束：[GarnetNetwork](../../src/mem/ruby/network/garnet/GarnetNetwork.cc)。
- 状态复用：[serving checkpoint](../ai_mesh/serving_checkpoint.md)。

对短延迟、高交互密度的 Mesh，将每个 router 单独分区可能受同步开销限制；这是基于跨分区消息与最小链路延迟约束的推断，需以代表性负载测量。

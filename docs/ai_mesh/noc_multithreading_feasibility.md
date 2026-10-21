# NoC 多线程可行性

## 范围与结论

约束为保留当前串行模型的传输时刻、仲裁、背压和 credit 行为，不允许通过增加链路延迟或推迟消息取得加速。分析对象为当前 AXI_MESH/Garnet；规模参考已有 5×5、2 GHz、单周期链路、双 lane 与写 YX 实验。本文是静态分析及历史工件核对，未实现或实测多线程版本。

冻结历史负载的宿主采样与双线程优先级结论见[性能采样报告](../../results/noc-5x5-dual-profile-20261019/report.md)。

技术上可行，工程范围涉及事件执行、通信所有权和共享观测。现有流控的本地 credit 状态适合按完整 router 划分所有权；直接分配多个 eventq、逐 router 加锁或按 lane 拆线程均不能满足上述约束。是否值得实施，取决于运行阶段热点占比、可独立执行的事件数量及同步成本。

## 候选架构

| 路线 | 保持原结果所需条件 | 评估意见 |
| --- | --- | --- |
| 原全局事件队列，批次并行完整 router 回调 | 仿真调度主线程保留原 tick/priority/LIFO 次序；只并行已证明可交换的回调；调度与共享效果按原事件顺序提交 | 优先评估的受限候选。保留事件跳跃和既有端点执行；收益受同 tick 独立批次宽度限制，不是已验证方案。 |
| 连续空间块分区，每分区独立事件队列 | 跨区 flit、反向 credit 和控制交互有明确所有权、原 arrival tick 及严格安全推进规则；复现会影响模型结果的原同 tick 顺序 | 长期候选，适合活跃 router 较多的密集流量；同步及端点边界改造范围更大。 |
| 全网大锁或每个 router 一个宿主线程 | 锁顺序仍须复现模型事件顺序，且共享队列边界仍需处理 | 全网大锁缺乏计算并行收益；逐 router 线程增加同步及边界数量，不作为优先路线。 |

两种候选均复用同一份 router、VC、lane 和 credit 实现，不另写 timing model；在证据充分前不同时建设两套生产执行引擎。

### 保留全局调度的批次候选

仿真调度主线程独占事件出队、时间推进及提交。初始候选仅包含原队列中连续、相同 tick/priority 且状态独立的 router 回调，遇到未证明可交换的 link、NI 或端点事件就结束批次。worker 执行原 router 逻辑，产生的高层调度意图由主线程按原顺序提交。不得仅拦截 EventQueue 插入：Consumer 会在调用队列前读取并修改自己的唤醒状态。

候选资格必须显式表达对象所有权及执行能力，不能根据事件名称特判。需要证明回调之间没有同 tick 反馈，并处理 RNG、网络统计及 fault 等共享效果。当前无序 vnet 的本地路由也可能调用全局 RNG；不可直接换成各 router 独立 RNG。即使新执行顺序可重复，也不能视为已经复现原串行结果。

### 空间分区候选

完整 router 的两个 lane、selector、Local 合并、仲裁器、VC 和 credit 状态归同一 owner。边界传输使用源端 outbox、目标端 inbox 和明确的所有权交接；接收端独占其队列及唤醒状态。mailbox 仅承载原模型的在途数据，不能额外提供无约束的接收容量或提前返还 credit。

NI 留在主线程时，NI↔router 的外部链路是分区边界；NI 随 router 分区时，NI↔协议控制器的 MessageBuffer 及容量反馈成为边界。两种放置都需要验证，不能将控制器调用当作普通容器访问。若把 core/DMA/adapter 随 tile 一起分区，还会涉及共享 scoreboard、dispatcher 和 peer publication 的同步控制语义，不能仅凭网络链路延迟证明可并行推进。

## 正确性审查入口

| 约束 | 唯一源入口 |
| --- | --- |
| 事件同时间平局次序、异步插入与所属线程限制 | [event queue](../../src/sim/eventq.cc)、[event API](../../src/sim/eventq.hh) |
| 原生多队列与同步屏障所在阶段 | [simulate](../../src/sim/simulate.cc)、[global event](../../src/sim/global_event.cc) |
| 跨对象队列和唤醒所有权 | [NetworkLink](../../src/mem/ruby/network/garnet/NetworkLink.cc)、[flitBuffer](../../src/mem/ruby/network/garnet/flitBuffer.hh)、[Consumer](../../src/mem/ruby/common/Consumer.cc) |
| router 内原顺序、双 lane 快照和共享出口 | [Router](../../src/mem/ruby/network/garnet/Router.cc)、[selector](../../src/mem/ruby/network/garnet/DualLaneSelector.cc)、[SwitchAllocator](../../src/mem/ruby/network/garnet/SwitchAllocator.cc)、[双 lane 合同](dual_lane_router_spec.md) |
| NI 顺序、协议队列时序、同时间入队次序及回调 | [NetworkInterface](../../src/mem/ruby/network/garnet/NetworkInterface.cc)、[MessageBuffer](../../src/mem/ruby/network/MessageBuffer.cc)、[Message](../../src/mem/ruby/slicc_interface/Message.hh) |
| 路由 RNG、全网 packet ID 与统计 | [RoutingUnit](../../src/mem/ruby/network/garnet/RoutingUnit.cc)、[GarnetNetwork](../../src/mem/ruby/network/garnet/GarnetNetwork.hh) |
| 在途消息及功能访问 | [AXI envelope](../../src/mem/ruby/protocol/axi_mesh/AXI_MESH-msg.sm)、[flit](../../src/mem/ruby/network/garnet/flit.cc) |
| AXI 同步反馈、跨核 publication 与共享控制 | [bridge](../../src/dev/ai_mesh/axi_garnet_bridge.cc)、[peer aperture](../../src/dev/ai_mesh/peer_sram_aperture.cc)、[dispatcher](../../src/dev/ai_mesh/mesh_dispatcher.cc)、[scoreboard](../../src/dev/ai_mesh/program_scoreboard.hh) |
| stats/退出边界、全网观测与已有 checkpoint 限制 | [stat control](../../src/sim/stat_control.cc)、[exit](../../src/sim/sim_events.cc)、[network](../../src/mem/ruby/network/garnet/GarnetNetwork.cc)、[checkpoint](../../src/mem/ruby/network/garnet/GarnetCheckpoint.cc) |

### 时间推进约束

对空间分区，设所有真实跨区因果路径的最小可见延迟为 L。严格半开窗口必须在处理边界 tick 的模型回调之前交接消息，才能讨论 Q≤L；数据和反向 credit 都纳入 L。不能用往返延迟、平均 hop 延迟或 packet 总延迟替代 L，也不能为扩大 Q 修改被模拟链路。

当前原生同步位于普通 Consumer 回调之后，边界末尾才合并 async queue。因此直接设置 Q=L 可能使消息在本周期仲裁结束后才可见；当前调度下采用 Q<L 只能处理这一边界问题，不能自动恢复原串行平局顺序。当前 stats 和部分退出 API 也会添加 simQuantum，不能默认保持原 ROI/退出 tick。

同 tick 的任意稳定排序不等于原事件次序；只允许重排已证明对时序和仲裁可交换的操作。统计可按 owner 本地累加并在一致停点确定顺序归并，但不能让全局原子计数器或锁竞争决定模型的可见事件顺序。

drain、snapshot、stats reset 和已有支持范围内的 checkpoint 必须在一致停点读取全网状态；新增边界在途数据及未提交调度意图必须纳入 quiescence。当前 dual-lane checkpoint 不在支持范围内，多线程分析不隐含扩展这一能力。

## 性能证据与边界

| 已有工件 | 观察值 | 推论限制 |
| --- | --- | --- |
| [5×5 双 lane MIXED](../../.tmp/mesh-dual-xy-yx.6xNUz7/dual/cases/A_H10_EAST2_MIXED_1_1_N128/raw/stats.txt)、[配置](../../.tmp/mesh-dual-xy-yx.6xNUz7/dual/cases/A_H10_EAST2_MIXED_1_1_N128/raw/config.json) | 14,336,000 flits；669,449,500 ticks；hostSeconds=1649.84；网络 clock=500 ticks | 历史执行有外层并发，不是隔离性能基线；单实例宿主采样见[报告](../../results/noc-5x5-dual-profile-20261019/report.md)。 |
| [稀疏 serving FULL](../../.tmp/gate8-long-regression/test_finite_fast_fabric_preser0/full/stats.txt)、[配置](../../.tmp/gate8-long-regression/test_finite_fast_fabric_preser0/full/config.json) | 309 flits；5,000,857,001 ticks；hostSeconds=0.63；网络 clock=500 ticks | 若整个跨度都固定每周期同步，会新增约一千万次同步事件；空闲区间必须保持事件跳跃能力。 |
| [Qwen 短采样诊断](../../.tmp/docs/qwen3-8b-smoke/stage-b/runtime-startup-performance-diagnosis.json) | 57 个带 DWARF 栈的样本位于启动 admission/验证 | 不支持将 Qwen 当前等待归因于 NoC，也不能推算全程各阶段占比。 |

四 worker 的理想上限为 S=1/((1-p)+p/4)，其中 p 是可并行部分占串行总耗时的比例。p 为 30%、60%、80% 时，上限分别约 1.29、1.82、2.50 倍；均未计算同步、通信和不均衡，不能作为实际加速预测。仅有 router 数量或 flit 数量不足以估计 p。

## 决策与验证门槛

1. 对密集 Mesh 的实际运行阶段隔离采样，区分准备、admission、router、link、NI、端点、统计及分配成本。测量可并行 router 批次宽度与覆盖率；空间分区另测边界流量和负载不均衡。
2. 只有确认目标热路径具备足够并行工作量，才选择一种执行架构。受限 AXI_MESH 原型优先比较原串行、一 worker、两 worker；保留同一 router 实现，不重写仲裁和流控。
3. 差分验证每个 flit 的 arrival tick、lane、outport、VC、grant，以及 credit 返回时刻、每 VC 守恒和 endpoint 完成顺序。总耗时或平均延迟相同不足以验收。
4. 在相同输入下重复验证一、二、四 worker；同时覆盖反压、双 lane 合并、写 YX、饱和流量和长空闲。宿主性能使用隔离运行，并分别记录模型执行和端到端耗时。

已有真实 oracle 复用入口：[实验测量](../../util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py)、[网络观测](../../util/mesh_ir/tests/integration/test_mesh_experiment_observer.py)、[transport/drain](../../util/mesh_ir/tests/integration/test_gate5_transport_contract.py)、[AXI 确定性与协议回归](../../tests/gem5/axi_garnet/run_axi_garnet_tests.py)。跨 worker 的逐事件等价检查仍需建设，本文不宣称现有测试已经提供该保证。

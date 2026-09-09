# 双 lane Router 定向复核

依据：[Router Spec](dual_lane_router_spec.md)。复核日期：2026-09-08。

两个 lane 使用独立仲裁实例；实现及定向验收通过。此结论限于下列验证范围，未运行全量回归。

## 实现入口

| 复核对象 | 唯一实现入口 |
| --- | --- |
| 两份输入 VC、输出输入轮询和请求状态 | [LaneArbiter](../../src/mem/ruby/network/garnet/LaneArbiter.hh) |
| lane 实例创建、方向出口选择和 Local 合并 | [SwitchAllocator](../../src/mem/ruby/network/garnet/SwitchAllocator.cc) |
| FIFO 深度来源和实际端口身份 | [Router](../../src/mem/ruby/network/garnet/Router.cc)、[GarnetNetwork](../../src/mem/ruby/network/garnet/GarnetNetwork.cc) |
| 输入 bank 的 lane / ingress 清单 | [InputUnit](../../src/mem/ruby/network/garnet/InputUnit.cc)、[GarnetQuiescence](../../src/mem/ruby/network/garnet/GarnetQuiescence.hh) |
| 实际发送 lane 标签 | [CrossbarSwitch](../../src/mem/ruby/network/garnet/CrossbarSwitch.cc) |
| bank 容量汇总及共享 ingress credit 核对 | [实验校验器](../../util/mesh_ir/mesh_ir/experiment/verify.py) |

`SwitchAllocator` 承担统一调度，仲裁请求和轮询状态由各 lane 的 `LaneArbiter` 实例持有。共享 P 出口的 `LaneMergeArbiter` 只选择获胜 lane，提交仅推进获胜 lane 的状态。

## 验证入口与证据

运行命令见 [测试入口](mesh_ir_test_commands.md#dual-lane-router)。

- [生产仲裁组件测试](../../src/mem/ruby/network/garnet/lane_arbitration.test.cc)：覆盖持续竞争下的 lane 内公平性、未获 grant 不推进、输入 VC 与输出间状态隔离、单 lane 轮询。包含输入分布为 lane 0 `{0,2}`、lane 1 `{1}` 的持续竞争反例。
- [检查器负向测试](../../util/mesh_ir/tests/unit/test_dual_lane_checks.py)：覆盖缺失接收/发送/合并事件、错误 lane 和 count、重复接收、错误重试顺序、容量身份及配置差异。
- [容量统计测试](../../util/mesh_ir/tests/unit/test_mesh_experiment_verify.py)：覆盖共享 P 两份 bank 的容量和 credit 汇总。
- [真实 gem5 验收](../../tests/gem5/ai_mesh/run_dual_lane_checks.py)：使用统一 32 B sideband 配置分别运行单/双 lane，核对物理发送、Local 合并和逐链路 VC 终态台账。
- [真实 observer 测试](../../util/mesh_ir/tests/integration/test_mesh_experiment_observer.py)：运行 `test_real_mixed_observer_preserves_capacity_credit_and_bytes[False]`，核对现有单 lane MIXED 场景的容量、credit、数据及统计。

本次实际命令、构建与文件摘要、测试结果的本机索引：
[validation.json](/tmp/dual-lane-arbiters-final-20260908/validation.json)。
单/双 lane 的配置、trace 和数据验证产物分别保存在该目录的 `single/`、`dual/`；
统计数和结束 tick 以 [checks.json](/tmp/dual-lane-arbiters-final-20260908/checks.json) 为准。

## 覆盖边界

真实运行观测到同方向双发及两个 lane 同时竞争 Local 出口。持续多输入竞争的 lane 内公平性由生产仲裁组件测试验证，不能从 lane 总 grant 比例推导。

当前 workload 未产生 selector reject。检查器会核对出现拒绝时的后续重试顺序，但不能将零次拒绝视为生产背压分支已覆盖；既有 FIFO 组件测试也不等同于生产 selector 的完整拒绝路径测试。

按当前单 flit packet 和 [NI VC 生命周期](../../src/mem/ruby/network/garnet/NetworkInterface.cc) 推导，NI 收到 free credit 后才复用该 VC，而接收 InputUnit 在返还 free credit 前已释放。合法注入下，同一 VC 的前一 packet 已离开输入 FIFO；仅延长 burst 或降低 credit 并不能保证触发 selector 拒绝。

本次 credit 验收覆盖逐 VC 累计发送/返还、两份 P bank 汇总及排空终态，不宣称逐周期在途守恒全覆盖。同配置结束时间相同，不据此宣称端到端性能提升。

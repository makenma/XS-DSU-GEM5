# AXI/HBM 端点串行优化索引

仿真时序、仲裁顺序、背压、内存副作用和模型统计必须保持一致。协议合同见 [AXI Garnet Spec](../../src/doc/ai_mesh/AXI_GARNET_CODEX_SPEC.md)；具体选择规则、平局处理和推进边界以源码及冻结行为轨迹为准。

验收数据保存在本地 `results/axi-endpoint-optimization-20261019/`，不随源码仓库分发；下文的验收数据链接指向该本地归档。

## 当前选择与实现

[最佳版本指针](../../results/axi-endpoint-optimization-20261019/best.json)是唯一选择入口，引用冻结 variant 和晋升证据。候选测试通过不代表已晋升，也不代表具有性能收益。

| 职责 | 实现入口 |
| --- | --- |
| 源端 AW/W 配对、响应退休与派生索引 | [initiator](../../src/mem/axi/axi_initiator_adapter.cc)、[状态及空闲 checkpoint 边界](../../src/mem/axi/axi_initiator_adapter.hh) |
| 服务准入、读写提交、R/B 输出与内存访问 | [target](../../src/mem/axi/axi_target_adapter.cc)、[状态定义](../../src/mem/axi/axi_target_adapter.hh) |
| HBM 服务与完成交接 | [backend](../../src/mem/axi/synthetic_hbm_backend.cc)、[容量及 checkpoint 边界](../../src/mem/axi/synthetic_hbm_backend.hh) |

事务和 payload 由原存储唯一拥有；索引只保存派生引用，不另存一套事务状态。索引维护必须随既有生命周期完成，不能增加隐式推进或改变可观察计数的更新点。

## 验证入口

| 证据 | 单一入口 |
| --- | --- |
| 单测注册与派生清单 | [注册源](../../tests/gem5/axi_garnet/axi_test_lib.py)、[清单校验及执行](../../tests/gem5/axi_garnet/run_axi_unit_tests.py) |
| 行为 corpus 的组件与源码身份 | [组件清单](../../src/mem/axi/axi_behavior_sources.json)、[逐操作比较器](../../tests/gem5/axi_garnet/compare_axi_behavior.py) |
| 本批冻结黄金合同 | [黄金执行](../../results/axi-endpoint-optimization-20261019/serial-20261021/contract-v3/verification.json) |
| 本批各候选的测试、行为和网络差分 | [正确性证据索引](../../results/axi-endpoint-optimization-20261019/serial-20261021/correctness.json) |
| 所选版本的完整模型与窗口轨迹 | [所选 variant 的验证证据](../../results/axi-endpoint-optimization-20261019/best.json)、[窗口与观测范围](../../results/axi-endpoint-optimization-20261019/dense-network-diagnostic-plan.json) |
| 实际工作树的构建、单测与网络 | [构建与测试](../../results/axi-endpoint-optimization-20261019/serial-20261021/workspace/staged-w-final/verification.json)、[主程序网络差分](../../results/axi-endpoint-optimization-20261019/serial-20261021/workspace/staged-w-final/network-comparison.json) |
| 真实网络回归 | [AXI trace runner](../../tests/gem5/axi_garnet/run_axi_garnet_tests.py)、[Mesh observer](../../util/mesh_ir/tests/integration/test_mesh_experiment_observer.py)、[双 lane 测量](../../util/mesh_ir/tests/integration/test_mesh_experiment_measurement.py) |

完整输出和模型统计的比较与网络窗口轨迹的比较具有不同范围。宿主字段排除、输出路径规范化及模型哈希规则统一由[评估工具](../../results/axi-endpoint-optimization-20261019/benchmark.py)定义，不另维护忽略列表。

## 实验身份与性能

| 项目 | 入口 |
| --- | --- |
| 原始源码身份 | [冻结清单](../../results/axi-endpoint-optimization-20261019/baseline-source-manifest.json) |
| 正式负载 | [输入清单](../../results/axi-endpoint-optimization-20261019/input-manifest-current-measured.json)、[输入迁移审查](../../results/axi-endpoint-optimization-20261019/input-migration-review.json)、[测量窗口审查](../../results/axi-endpoint-optimization-20261019/measurement-window-review.json) |
| 运行依赖 | [冻结环境恢复记录](../../results/axi-endpoint-optimization-20261019/recovery-20261021.json) |
| 所选源码与实际构建的关联 | [所选 variant 的工作树构建及依赖审计](../../results/axi-endpoint-optimization-20261019/best.json) |
| 所选版本的调用栈采样 | [所选 variant 的采集证据](../../results/axi-endpoint-optimization-20261019/best.json) |
| 独立交错计时 | [运行索引](../../results/axi-endpoint-optimization-20261019/serial-20261021/timing/timing-plan.json) |
| 性能验收规则 | [评估工具](../../results/axi-endpoint-optimization-20261019/benchmark.py)的独立运行校验与 summary |

诊断与采样运行不计入正式性能。比较必须使用冻结输入、运行依赖和二进制；CPU 与墙钟收益均须超过观测噪声。阶段热点比例用于定位，不能累加为预计收益；当前负载的结果也不能外推到其他 gem5 模型。

## 已验收阶段

当前选择仍只以上方最佳版本指针为准。历史性能、源码身份与正确性证据通过各阶段晋升记录关联。

| 阶段 | 性能报告 | 晋升记录 |
| --- | --- | --- |
| HBM 完成交接 | [报告](../../results/axi-endpoint-optimization-20261019/phase1-performance.json) | [归档](../../results/axi-endpoint-optimization-20261019/promotions/) |
| 源端 R 响应 | [报告](../../results/axi-endpoint-optimization-20261019/phase2-performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase2-r-response-index-fresh.json) |
| target 读提交 | [报告](../../results/axi-endpoint-optimization-20261019/phase3-read-commit-index/formal/performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase3-read-commit-index-fresh.json) |
| target R 生成 | [报告](../../results/axi-endpoint-optimization-20261019/phase4-read-generation-index/formal-attempt02/performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase4-read-generation-index-fresh.json) |
| target 写提交 | [报告](../../results/axi-endpoint-optimization-20261019/phase5-write-commit-index/formal/performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase5-write-commit-index-fresh.json) |
| target 服务准入 | [报告](../../results/axi-endpoint-optimization-20261019/phase6-service-admission-index/formal/performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase6-service-admission-index-fresh.json) |
| 源端 B 响应 | [报告](../../results/axi-endpoint-optimization-20261019/phase7-b-response-index/followup-20261021/performance.json) | [记录](../../results/axi-endpoint-optimization-20261019/promotions/phase7-b-response-index-fresh-20261021.json) |
| 源端 staged W | [报告](../../results/axi-endpoint-optimization-20261019/serial-20261021/timing/checked/staged-w-fifo-performance.json) | [当前选择](../../results/axi-endpoint-optimization-20261019/best.json) |

## 候选评估记录

候选通过功能验证后仍须独立通过性能门槛；组合发生变化时，以该组合的测量为准。各报告保留全部有效独立样本。

| 候选 | 性能证据 |
| --- | --- |
| HBM 服务选择堆 | [增量对照](../../results/axi-endpoint-optimization-20261019/serial-20261021/timing/checked/hbm-service-index-performance.json) |
| 内存页访问（含服务选择堆） | [增量对照](../../results/axi-endpoint-optimization-20261019/serial-20261021/timing/checked/memory-page-access-performance.json) |
| W 与内存页访问组合 | [独立交错对照](../../results/axi-endpoint-optimization-20261019/serial-20261021/memory-without-service-heap-timing/performance.json) |

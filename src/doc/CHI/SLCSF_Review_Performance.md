# SLCSF RTL review、router 重构与运行性能

本文保留第一轮结果；第二轮针对 router 的算法优化、严格等价对照和性能数据见
[Router_Performance_Round2.md](Router_Performance_Round2.md)。第一轮基线差分仅适用于
该轮归档源码，不应直接套用到后续扩展过的当前源码。

本轮针对现有实现修正逐拍时序、SEQ/replay 和压力下的前进性，将用户指定的
chiron router 引入本仓库，并在功能修正后的共同基线上比较算法优化。
CMN 的完整源码、寄存器依据和配置边界见
[SLCSF_CMN700_Alignment.md](SLCSF_CMN700_Alignment.md)，router 结构和阶段见
[Router_Garnet_Refactor.md](Router_Garnet_Refactor.md)。

## Review 发现及修复

| 问题 | 最终行为及证据 |
| --- | --- |
| set-way 历史过早释放，非分配 miss 漏保护 | H6 检查 replay，H7 插入历史，保护至接受后第 14 拍，15 拍清除；默认 SF 精确模式关闭时，非分配 miss 也保护候选 way。定向实际 RTL 与组件测试覆盖边界。 |
| 同拍先写后读改变年轻请求看到的 RAM 内容 | 所有请求先完成当拍 RAM 快照，再执行写提交；两拍数据分别采样，不用整行提前读取代替。 |
| POC victim 冲突只在接收时检查 | 保存有限时间窗，在 H3 采样、H4 寄存 replay；包括地址、NS 和本事务排除，释放后的历史只保留必要窗口。 |
| SEQ valid/sent/POCQ 事务混用 | valid 在 H7 写入，H0 请求寄存输出后才可接收；ACK 后一拍 sent，无 hazard ACK 后第 3 拍释放 valid，有 hazard 则到 wake 后一拍；内部 POCQ 仍等协议和写回完成才退休。 |
| SEQ 命中资格、NoSnp 和 replay 元数据处理不准确，物理槽扫描简化 | 按 opcode 处理；NoSnp 返回 noFill；replay 时仍保留 RTL 的保守 broadcast 更新。普通有效槽为 8，但读指针保留 12 个物理位置的扫描。 |
| 满目录持续流量使 SEQ 无法进入 POCQ | 有待发 SEQ 且无内部事务时，最后一个空位优先给 SEQ；无 SEQ 时仍可占满全部 16 项。该策略保持前进性，未复制 CMN 全部 HBT/QoS 池分配。 |
| 内部 SEQ 的空 FlushSf 被较老 replay 永久阻塞 | 在生成内部动作时跳过该操作，不参加 SLCSF 仲裁；普通 CMO 的 FlushSf 保留，dirty 数据仍完成 SN 写回后才退休。 |
| flit/credit 的同 tick 执行顺序改变可见性 | LinkLayer 和 router 使用前拍 flit、前拍归还的 credit；事件通知仅安排下一边沿。检查器在 DUT 当拍事件完成后采样。 |

长流量先复现了两个 SEQ 循环等待，随后加入永久回归。4×4 SF 下 1000 请求
完成于 9952 周期，产生 984 次 snoop；CMN 容量下 100000 请求完成于 609495
周期，产生 34464 次 snoop。SN read 数严格等于前台请求数。正常/提前 endpoint
事件顺序的周期、数据和完整端口事件摘要相同，不能仅凭最终完成数判定等价。

## Router

已从 `/nfs/home/majunhong/project/chiron/XS-DSU-GEM5/src/mem/cache/CHI`
移植 `ChiRouterRefModel`，来源仓库没有改动。参考仓库中 Garnet 的模块划分，
拆出输入缓冲、路由、仲裁和输出阶段，保留原 least-recently-granted 仲裁赢家、
credit 流控、单双 lane 和 FIFO/按输出分组队列。

原两套同时构造的队列改为按配置分配，默认 deque 数从 2848 降到 384；
age matrix 的 O(N²) 选择改为 O(N) 全序选择，按输入一次收集出口请求。
普通静态 node 路由无需保存每事务 reverse-map。另修复虚增 credit、提前返还
wire credit、无界 merge 队列、primary/EXT 控制数据错配和反压时改投错误端口。
这些是原模型的功能错误；等价性能比较不使用错误拥塞行为作为时序基线。

## 验证结果

最终构建使用 `scons --linker=mold`，完整日志在
`m5out/chi-review/validation/build-final-pair.log`。
统一结果索引及交付源码 SHA-256 在 `m5out/chi-review/validation/report.json`；
主回归目录为 `hnf-order-final/`，router 矩阵为 `router/`，均位于
`m5out/chi-review/` 下。

| 验证 | 结果 |
| --- | --- |
| SLCSF 组件 | 56/56 |
| POCQ 状态图 | 7/7 |
| DAT 所有权 | 3/3 |
| Router 单元 | 8/8，含 8 路全优先序 × 全请求掩码穷举 |
| ASan、UBSan 和容器断言 | 67/67，覆盖 SLCSF、DAT、router；不包括 POCQ 状态图 |
| 实际 CMN RTL 轨迹 | 36/36：原 14 + timing/history 9 + SEQ/victim 13 |
| HNF 主矩阵 | 46/46，23 对正常/提前事件顺序摘要一致 |
| HNF 额外响应时序 | 16/16，0/11/17 拍配置；包括 ACK 输入流水中的 hazard 保持检查 |
| Router 端口矩阵 | 56/56，399552 个 flit，28 对事件顺序一致 |
| SEQ 压力扩展 | 100000 请求正常/提前一致；SF1×1、响应延迟0/17、两种事件顺序均通过 |

RTL 测试实例化供应商 tagpipe/datadp 模块，以固定 RAM 返回接口驱动；SEQ
ACK/dealloc 从 POC 边界驱动，未实例化完整 CMN 的动态 SRAM/POCQ。它证明
对应寄存边沿和场景的规则，不代表完整芯片逐拍等价。ASan 设置
`detect_leaks=0`，当前沙箱不支持 LeakSanitizer，未声称完成泄漏检测。

## 保持模拟时序的算法优化

SLCSF 接收时间单调，原先每拍扫描槽位、分配临时 vector 并排序没有必要。
现在用固定数组保存活跃槽的接收顺序，退休时稳定压缩；各读/写阶段的遍历
顺序不变，`hasPipelineWork()` 为 O(1)。历史按过期顺序从队首删除。
只有流水和 SEQ 都不再产生时钟变化时才跳过空闲区间，仍处理目标边沿的
history、busy 和上一拍 SEQ 快照。

性能基线包含本轮全部功能修复，只撤销五个函数中的算法优化；相同结构体、
状态规则、事件边界和 tester。基线差分保存在
`tests/gem5/chi/slcsf_performance_baseline.patch`，可在副本中应用，勿当作功能回退。
两种实现绑定同一 CPU、交替顺序、各运行 5 次并取中位数。

| 测量范围 | 基线中位耗时 | 优化后 | 加速比 |
| --- | ---: | ---: | ---: |
| SLCSF 密集 100 万周期 | 0.794594 s | 0.609894 s | 1.303× |
| SLCSF 稀疏 1000 请求、间隔4096周期 | 0.295738 s | 0.00191144 s | 154.72× |
| Router 8 路仲裁 kernel、1000 万步 | 0.637771 s | 0.204572 s | 3.118× |
| 完整 gem5 HNF、30 万请求，hostSeconds | 6.50 s | 6.03 s | 1.078× |
| 同一 HNF 测试，完整进程 wall time | 7.02084 s | 6.48535 s | 1.083× |

稀疏负载的高加速来自删除空闲循环，不能外推为普通完整仿真收益。SLCSF
两组测量均逐项比较准入、结果类型和周期、SEQ、最终 tag/data 与替换状态。
密集摘要为 `417abc27ac2178d5`，稀疏摘要为 `41c910fcc5302578`。
Router kernel 检查每次仲裁结果。实际测量和所有样本见
`m5out/chi-review/performance-final/report.json`、
`m5out/chi-review/validation/router-arbiter-benchmark.json`。

完整 HNF 测量通过实际 LinkLayer/POCQ/SLCSF 和可控 RNF/SN responder，使用
固定 CMN 容量，包含超过 SF 容量后的持续 eviction。5 对运行的模拟周期、
端口事件/数据摘要、read/snoop 数完全一致。仿真 host 耗时下降 7.23%，
完整进程耗时下降 7.63%；后者还包括 Python 配置、对象初始化与退出。
这是该 HNF 流量负载的结果，不代表 CPU/SPEC workload 的加速。原始样本、
逐拍摘要和二进制 SHA-256 见 `m5out/chi-review/hnf-performance/report.json`。

## 复现入口与保留边界

```sh
scons --linker=mold build/RISCV/gem5.opt -j32
python3 tests/gem5/chi/run_slcsf.py --check-event-order --out m5out/chi-review/hnf-order-final
python3 tests/gem5/chi/run_router.py --check-event-order --out m5out/chi-review/router
python3 tests/gem5/chi/benchmark_slcsf.py --baseline-source /tmp/slcsf-corrected-baseline
python3 tests/gem5/chi/benchmark_hnf.py --baseline /tmp/chi-review-baseline.opt --requests 300000
```

纯模型基线可复制 `SlcSfModel.cc`、`SlcSfModel.hh`、`SlcSfTypes.hh` 到临时目录，
在该目录用 `patch -p1 -i <上述差分的绝对路径>` 生成；本轮原件也归档于
`m5out/chi-review/performance-final/baseline-source/mem/cache/CHI/`。
完整 gem5 对照二进制由相同源码树分别构建，只有该差分不同；基线构建后已
恢复优化源码并重新构建。性能脚本比较完整端口逐事件摘要，而非只比较耗时。

本轮没有扩展到完整 CPU workload、所有 CHI opcode 的端到端协议或 CMN
router RTL 对齐。Router 的兼容事务别名路径仍缺少事务退休通知，持续使用
新别名可能增加存储；普通静态 node 路由不再积累该状态。ECC、Stash、跨 die、
MTE/MPAM/ABF 等原先排除项保持在范围之外。

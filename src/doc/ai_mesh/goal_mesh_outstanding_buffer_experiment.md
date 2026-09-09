# 5×5 AI Mesh：东侧双 HBM、32 B 单 flit 实验

正式终态报告：所有已提交物理配置均已完成验证与结果关联。

本报告采用新拓扑和单 flit 数据传输的实际物理结果。带宽为十进制 GB/s；容量使用 KiB/MiB。

原始数据：[mesh-east-dual-hbm-32B.9luwm2qi](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/README.md)。身份审计：[identity_audit.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/identity_audit.json)。
进程终态与旧数据保护核验：[completion_audit.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/completion_audit.json)。
旧配置结果保存在 [旧实验报告](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/previous_report.md) 与旧实验目录中。

## 配置与统计口径

| 项目 | 配置 |
| --- | --- |
| core / router | 25 / 25，2 GHz |
| HBM 映射 | router 4、9、14、19、24 各接 core＋两个独立 HBM endpoint，共 10 个 HBM 口 |
| 错误端点 | router 12 的独立 error endpoint，不承载正常负载 |
| 数据通路 | AXI 32 B；Mesh 每有向链路每周期 1 个 32 B 数据 flit |
| 包格式 | W/R 每 beat 一个 flit，16 B 元数据预算采用边带；AW/AR/B 各占一个 flit；五通道共享物理输出 |
| 逐跳时序 | 单 flit 消除包内串行拍数；每跳 router/link 延迟仍按冻结 profile 执行 |
| 控制位口径 | 32 B 为数据通路宽度；完整 RTL 控制位编码、线数与元数据存储成本未建模 |
| VC | 每输入、每 vnet 4 个 VC；每 VC 同时占用一个 packet |
| 搜索边界 | 只改变 Router 输入 VC 深度；NI、端点队列、DMA ROB 与 SRAM 资源按 profile 独立冻结 |
| HBM 后端 | 每口 100 ns 基础等待、32 B/cycle 读写共享服务，共 640 GB/s；无 bank/row/refresh 模型 |
| 正式 workload | 25 核各 16 MiB，64 KiB tile，两个 SRAM 槽/方向；同方向 DMA 按 descriptor 队头推进，每 burst 16×32 B |
| 地址分布 | 每核独立地址；tile 按 (core_id + tile_index) mod 10 轮转 HBM；MIXED 每核读写各覆盖五口、合计十口；XY 路由 |
| 读/写/混合 | LOAD：HBM→SRAM；STORE：预填 SRAM→HBM；MIXED 每核 8 MiB 读＋8 MiB 写，无计算算子 |
| ROI | 各核活跃发起区间交集，舍弃前 20%，冻结三个连续子窗口；≤2% 稳定性阈值 |
| 有效带宽 | ROI 内 LOAD burst SRAM commit 字节＋STORE 成功 B 字节，除以共同 ROI 时长 |
| 全程带宽 | 400 MiB 除以首个被测 DMA_LOAD/STORE command issue 到全部 tile 完成的 makespan；STORE 预填不计入 |
| 长验证 | 20 MiB/core，结果独立列示，不改写 16 MiB 排名 |

配置唯一来源：[east_dual_hbm_32B_profile.json](../../../configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json)；执行参数与身份：[launch.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/launch.json)。

## 推荐与峰值

16 MiB 排名采用该工作量下有效 ROI 峰值带宽的 95% 门槛，再优先节省 Router 数据存储和 outstanding；结论限于已测配置。该门槛仅作用于 ROI 带宽，整个作业的完成吞吐需另看全程带宽列。跨长度核验后的配置选择单独列在下表之后。

| 负载 | 角色 | N/核/方向 | AW/W/B/AR/R flits/VC | 数据存储 KiB | ROI GB/s | 全程 GB/s | AXI P99 μs | Jain | 20 MiB 状态/带宽变化 | 物理结果 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 推荐（16 MiB） | 16 | [1, 1, 1, 1, 1] | 72.500 | 365.3298 | 363.3201 | 1.1795 | 0.999994 | [valid (+0.090%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_LOAD_ONLY_0/measurement.json) | [B_H10_EAST2_LOAD_ONLY_N16_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N16_V0/measurement.json) |
| LOAD_ONLY | 峰值（16 MiB） | 16 | [1, 1, 1, 1, 1] | 72.500 | 365.3298 | 363.3201 | 1.1795 | 0.999994 | [valid (+0.090%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_LOAD_ONLY_0/measurement.json) | [B_H10_EAST2_LOAD_ONLY_N16_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N16_V0/measurement.json) |
| STORE_ONLY | 推荐（16 MiB） | 4 | [1, 1, 1, 1, 1] | 72.500 | 185.1216 | 162.6766 | 1.4450 | 0.806163 | [unconverged (-1.074%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_0/measurement.json) | [B_H10_EAST2_STORE_ONLY_N4_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N4_V0/measurement.json) |
| STORE_ONLY | 峰值（16 MiB） | 32 | [4, 8, 4, 4, 8] | 406.000 | 185.4769 | 175.7520 | 7.3915 | 0.624613 | [valid (-1.760%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_1/measurement.json) | [A_H10_EAST2_STORE_ONLY_N32](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N32/measurement.json) |
| MIXED_1_1 | 推荐（16 MiB） | 2 | [1, 1, 1, 1, 1] | 72.500 | 286.8242 | 250.5283 | 0.3495 | 0.983476 | [valid (+0.689%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_MIXED_1_1_0/measurement.json) | [B_H10_EAST2_MIXED_1_1_N2_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N2_V0/measurement.json) |
| MIXED_1_1 | 峰值（16 MiB） | 2 | [1, 1, 1, 1, 1] | 72.500 | 286.8242 | 250.5283 | 0.3495 | 0.983476 | [valid (+0.689%)](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_MIXED_1_1_0/measurement.json) | [B_H10_EAST2_MIXED_1_1_N2_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N2_V0/measurement.json) |

N 分别按 AR→RLAST、AW→B 统计；读 RLAST 后 SRAM commit 等待不继续占用 AXI outstanding。未激活通道的深度不具备本负载下的性能识别性。

跨长度核验后的配置选择要求 16 MiB 和 20 MiB 均为 valid，且 16 MiB 带宽达到该负载正式峰值的 95%，再按同一资源优先规则选择。未收敛长验证不满足这一条件。

| 负载 | N/核/方向 | AW/W/B/AR/R | KiB | 16 MiB ROI GB/s | 20 MiB ROI GB/s | 20 MiB P99 μs | 验证 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 16 | [1, 1, 1, 1, 1] | 72.500 | 365.3298 | 365.6596 | 1.1815 | [20 MiB 结果](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_LOAD_ONLY_0/measurement.json) |
| STORE_ONLY | 32 | [4, 8, 4, 4, 8] | 406.000 | 185.4769 | 182.2124 | 7.8060 | [20 MiB 结果](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_1/measurement.json) |
| MIXED_1_1 | 2 | [1, 1, 1, 1, 1] | 72.500 | 286.8242 | 288.8016 | 0.3440 | [20 MiB 结果](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_MIXED_1_1_0/measurement.json) |

跨长度选择与物理结果关联：[length_validation.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/length_validation.json)。两种长度均收敛仍不等于带宽完全相同，相对变化与全部长验证见后表。

LOAD 峰值的计算示例：25 核在共同 ROI 内完成 334,573,568 B，ROI 时长为 915.8124 μs，相除得到 365.3298 GB/s。这是合计带宽；十个口各自的完成带宽见后表。

三种负载采用相同硬件与 N 时，已测配置中没有全部达到各自有效峰值 95% 的点；下表为最小相对性能最高的折中点。

| 负载 | N | map | ROI GB/s | 相对该负载峰值 | case_id |
| --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 128 | [571041385f97](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_LOAD_ONLY_N128/measurement.json) | 354.7828 | 97.11% | A_H10_EAST2_LOAD_ONLY_N128 |
| MIXED_1_1 | 128 | [571041385f97](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_MIXED_1_1_N128/measurement.json) | 265.4911 | 92.56% | A_H10_EAST2_MIXED_1_1_N128 |
| STORE_ONLY | 128 | [571041385f97](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N128/measurement.json) | 185.4769 | 100.00% | A_H10_EAST2_STORE_ONLY_N128 |

共同硬件的完整候选与判据结果：[common_hardware.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/common_hardware.json)。

## 完成吞吐与时延

| 负载 | 完成读 Mburst/s | 完成写 Mburst/s | 完成 Mtile/s | AXI 平均 μs | tile P99 μs |
| --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 713.5348 | 0.0000 | 5.5732 | 0.5271 | 14.9315 |
| STORE_ONLY | 0.0000 | 362.2596 | 2.8279 | 1.2275 | 96.6140 |
| MIXED_1_1 | 280.1079 | 280.0956 | 4.3756 | 0.1608 | 35.8815 |

burst 完成按 ROI 内事件计数；tile 完成另行计数，ROI 边界处不要求二者整除。AXI 时延为 ROI 内发起事务的完整响应时延，tile 时延另含 DMA/SRAM 调度。

## Outstanding 曲线

### LOAD_ONLY

| N | 状态 | 基线 ROI GB/s | 相邻 N 增益 | AXI P99 μs |
| --- | --- | --- | --- | --- |
| 1 | valid | 92.5776 | — | 0.1455 |
| 2 | valid | 170.5917 | +84.27% | 0.2275 |
| 4 | valid | 287.8872 | +68.76% | 0.3260 |
| 8 | valid | 333.4909 | +15.84% | 0.6765 |
| 16 | valid | 365.3298 | +9.55% | 1.1795 |
| 32 | unconverged | 349.8771 | -4.23% | 3.6675 |
| 64 | valid | 343.7757 | -1.74% | 8.3835 |
| 128 | valid | 354.7828 | +3.20% | 9.5020 |

固定基线 FIFO 的有效峰值在 N=16；达到该峰值 95% 的最小已测 N=16。这是本表的饱和点口径，联合 FIFO 搜索的推荐见前表。相邻增益保留原始状态，不将未收敛点用于有效峰值。

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_bandwidth_Bps.svg)

延迟曲线：[LOAD_ONLY_p99_ticks.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_p99_ticks.svg)。

### STORE_ONLY

| N | 状态 | 基线 ROI GB/s | 相邻 N 增益 | AXI P99 μs |
| --- | --- | --- | --- | --- |
| 1 | valid | 91.1222 | — | 0.1610 |
| 2 | valid | 163.9003 | +79.87% | 0.2500 |
| 4 | valid | 185.1216 | +12.95% | 1.4450 |
| 8 | unconverged | 180.1483 | -2.69% | 3.4510 |
| 16 | unconverged | 180.7925 | +0.36% | 6.8590 |
| 32 | valid | 185.4769 | +2.59% | 7.3915 |
| 64 | valid | 185.4769 | +0.00% | 7.3915 |
| 128 | valid | 185.4769 | +0.00% | 7.3915 |

固定基线 FIFO 的有效峰值在 N=32；达到该峰值 95% 的最小已测 N=4。这是本表的饱和点口径，联合 FIFO 搜索的推荐见前表。相邻增益保留原始状态，不将未收敛点用于有效峰值。

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_bandwidth_Bps.svg)

延迟曲线：[STORE_ONLY_p99_ticks.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_p99_ticks.svg)。

### MIXED_1_1

| N | 状态 | 基线 ROI GB/s | 相邻 N 增益 | AXI P99 μs |
| --- | --- | --- | --- | --- |
| 1 | valid | 180.6675 | — | 0.1630 |
| 2 | valid | 286.8242 | +58.76% | 0.3495 |
| 4 | valid | 278.6551 | -2.85% | 1.6325 |
| 8 | valid | 270.4869 | -2.93% | 3.7560 |
| 16 | valid | 267.2771 | -1.19% | 7.3910 |
| 32 | valid | 265.1177 | -0.81% | 8.4025 |
| 64 | unconverged | 268.1303 | +1.14% | 8.1750 |
| 128 | valid | 265.4911 | -0.98% | 8.1885 |

固定基线 FIFO 的有效峰值在 N=2；达到该峰值 95% 的最小已测 N=2。这是本表的饱和点口径，联合 FIFO 搜索的推荐见前表。相邻增益保留原始状态，不将未收敛点用于有效峰值。

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_bandwidth_Bps.svg)

延迟曲线：[MIXED_1_1_p99_ticks.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_p99_ticks.svg)。

## Router FIFO 对照

下表保留相同 N 下实际执行的四档全网 Router 深度；完整独立通道和异构分配点见原始 summary。容量只计 Router 输入 VC 的数据 flit，不包含 NI、端点队列、DMA ROB、控制元数据或 SRAM 宏开销。

| 负载 | N | AW/W/B/AR/R | KiB | ROI GB/s | P99 μs | 状态 | case_id |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 16 | [1, 1, 1, 1, 1] | 72.500 | 365.3298 | 1.1795 | valid | [B_H10_EAST2_LOAD_ONLY_N16_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N16_V0/measurement.json) |
| LOAD_ONLY | 16 | [2, 4, 2, 2, 4] | 203.000 | 365.3298 | 1.1795 | valid | [B_H10_EAST2_LOAD_ONLY_N16_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N16_V1/measurement.json) |
| LOAD_ONLY | 16 | [4, 8, 4, 4, 8] | 406.000 | 365.3298 | 1.1795 | valid | [A_H10_EAST2_LOAD_ONLY_N16](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_LOAD_ONLY_N16/measurement.json) |
| LOAD_ONLY | 16 | [8, 16, 8, 8, 16] | 812.000 | 365.3298 | 1.1795 | valid | [B_H10_EAST2_LOAD_ONLY_N16_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N16_V3/measurement.json) |
| LOAD_ONLY | 32 | [1, 1, 1, 1, 1] | 72.500 | 349.8771 | 3.6675 | unconverged | [B_H10_EAST2_LOAD_ONLY_N32_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V0/measurement.json) |
| LOAD_ONLY | 32 | [2, 4, 2, 2, 4] | 203.000 | 349.8771 | 3.6675 | unconverged | [B_H10_EAST2_LOAD_ONLY_N32_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V1/measurement.json) |
| LOAD_ONLY | 32 | [4, 8, 4, 4, 8] | 406.000 | 349.8771 | 3.6675 | unconverged | [A_H10_EAST2_LOAD_ONLY_N32](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_LOAD_ONLY_N32/measurement.json) |
| LOAD_ONLY | 32 | [8, 16, 8, 8, 16] | 812.000 | 349.8771 | 3.6675 | unconverged | [B_H10_EAST2_LOAD_ONLY_N32_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V3/measurement.json) |
| STORE_ONLY | 4 | [1, 1, 1, 1, 1] | 72.500 | 185.1216 | 1.4450 | valid | [B_H10_EAST2_STORE_ONLY_N4_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N4_V0/measurement.json) |
| STORE_ONLY | 4 | [2, 4, 2, 2, 4] | 203.000 | 185.1216 | 1.4450 | valid | [B_H10_EAST2_STORE_ONLY_N4_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N4_V1/measurement.json) |
| STORE_ONLY | 4 | [4, 8, 4, 4, 8] | 406.000 | 185.1216 | 1.4450 | valid | [A_H10_EAST2_STORE_ONLY_N4](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N4/measurement.json) |
| STORE_ONLY | 4 | [8, 16, 8, 8, 16] | 812.000 | 185.1216 | 1.4450 | valid | [B_H10_EAST2_STORE_ONLY_N4_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N4_V3/measurement.json) |
| STORE_ONLY | 8 | [1, 1, 1, 1, 1] | 72.500 | 180.1483 | 3.4510 | unconverged | [B_H10_EAST2_STORE_ONLY_N8_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V0/measurement.json) |
| STORE_ONLY | 8 | [2, 4, 2, 2, 4] | 203.000 | 180.1483 | 3.4510 | unconverged | [B_H10_EAST2_STORE_ONLY_N8_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V1/measurement.json) |
| STORE_ONLY | 8 | [4, 8, 4, 4, 8] | 406.000 | 180.1483 | 3.4510 | unconverged | [A_H10_EAST2_STORE_ONLY_N8](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N8/measurement.json) |
| STORE_ONLY | 8 | [8, 16, 8, 8, 16] | 812.000 | 180.1483 | 3.4510 | unconverged | [B_H10_EAST2_STORE_ONLY_N8_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V3/measurement.json) |
| MIXED_1_1 | 2 | [1, 1, 1, 1, 1] | 72.500 | 286.8242 | 0.3495 | valid | [B_H10_EAST2_MIXED_1_1_N2_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N2_V0/measurement.json) |
| MIXED_1_1 | 2 | [2, 4, 2, 2, 4] | 203.000 | 286.8242 | 0.3495 | valid | [B_H10_EAST2_MIXED_1_1_N2_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N2_V1/measurement.json) |
| MIXED_1_1 | 2 | [4, 8, 4, 4, 8] | 406.000 | 286.8242 | 0.3495 | valid | [A_H10_EAST2_MIXED_1_1_N2](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_MIXED_1_1_N2/measurement.json) |
| MIXED_1_1 | 2 | [8, 16, 8, 8, 16] | 812.000 | 286.8242 | 0.3495 | valid | [B_H10_EAST2_MIXED_1_1_N2_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N2_V3/measurement.json) |
| MIXED_1_1 | 4 | [1, 1, 1, 1, 1] | 72.500 | 278.6551 | 1.6325 | valid | [B_H10_EAST2_MIXED_1_1_N4_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N4_V0/measurement.json) |
| MIXED_1_1 | 4 | [2, 4, 2, 2, 4] | 203.000 | 278.6551 | 1.6325 | valid | [B_H10_EAST2_MIXED_1_1_N4_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N4_V1/measurement.json) |
| MIXED_1_1 | 4 | [4, 8, 4, 4, 8] | 406.000 | 278.6551 | 1.6325 | valid | [A_H10_EAST2_MIXED_1_1_N4](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_MIXED_1_1_N4/measurement.json) |
| MIXED_1_1 | 4 | [8, 16, 8, 8, 16] | 812.000 | 278.6551 | 1.6325 | valid | [B_H10_EAST2_MIXED_1_1_N4_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_MIXED_1_1_N4_V3/measurement.json) |

容量对照核验接收端实际 FIFO、发送端初始 credit 与冻结 map 一致；不仅检查命令行参数。

每个 AXI burst 仍是 16 beats；W/R 逐 beat 形成独立 packet，经 VC 依次传输，所以 flits/VC 深度不等于 burst 长度上限。本模型一个 VC 同时占用一个 packet，直到 free credit 返回才可重新分配；当前每个 packet 只有一个 flit。增加深度不会增加四个 VC 所能同时承载的 packet 数，因此不能用更大的 FIFO 容量替代 VC 周转或链路带宽。深度为 1 时出现输入满占比，也需要结合 credit 失败和吞吐对照判断。

区域搜索使用的实际 router 划分及 pilot 点：[LOAD_ONLY](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/hotspots_H10_EAST2_LOAD_ONLY.json)；[STORE_ONLY](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/hotspots_H10_EAST2_STORE_ONLY.json)；[MIXED_1_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/hotspots_H10_EAST2_MIXED_1_1.json)。HOT_NON_HBM 是从非 HBM router 中按实际观察值选出的搜索区域，不能与 HBM 附着热点混为一谈。推荐表若为全网统一深度，该配置同时覆盖热点和边缘；异构点以完整 input-port map 为准。

热点与边缘分配的实测对照：

| case_id | 参照 | 分配操作 | N | KiB | 状态 | ROI GB/s | 相对参照 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [C0_H10_EAST2_STORE_ONLY_S0_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_0/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HBM_ATTACH:OTHER_INTERIOR:W:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C0_H10_EAST2_STORE_ONLY_S0_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_1/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HOT_NON_HBM:OTHER_INTERIOR:AW:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C0_H10_EAST2_STORE_ONLY_S0_2](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_2/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HOT_NON_HBM:OTHER_INTERIOR:AW:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C0_H10_EAST2_STORE_ONLY_S0_3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_3/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HOT_NON_HBM:OTHER_INTERIOR:W:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C0_H10_EAST2_STORE_ONLY_S0_4](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_4/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HOT_NON_HBM:OTHER_INTERIOR:W:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C0_H10_EAST2_STORE_ONLY_S0_5](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C0_H10_EAST2_STORE_ONLY_S0_5/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:HOT_NON_HBM:OTHER_INTERIOR:B:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_0/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:AW:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_1/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:AW:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_2](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_2/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:W:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_3/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:W:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_4](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_4/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:B:-1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [D_H10_EAST2_STORE_ONLY_5](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/D_H10_EAST2_STORE_ONLY_5/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | equal_capacity:port_14_0:port_14_4:B:+1 | 32 | 406.000 | valid | 185.4769 | +0.0000% |
| [C1_H10_EAST2_LOAD_ONLY_S0_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_LOAD_ONLY_S0_0/measurement.json) | B_H10_EAST2_LOAD_ONLY_N16_V0 | coordinate:HBM_ATTACH:AR:+1 | 16 | 76.000 | valid | 365.3298 | +0.0000% |
| [C1_H10_EAST2_LOAD_ONLY_S1_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_LOAD_ONLY_S1_0/measurement.json) | B_H10_EAST2_LOAD_ONLY_N16_V1 | coordinate:HBM_ATTACH:AR:+1 | 16 | 206.500 | valid | 365.3298 | +0.0000% |
| [C1_H10_EAST2_MIXED_1_1_S0_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_MIXED_1_1_S0_0/measurement.json) | B_H10_EAST2_MIXED_1_1_N2_V0 | coordinate:HBM_ATTACH:AW:+1 | 2 | 76.000 | valid | 286.8242 | +0.0000% |
| [C1_H10_EAST2_MIXED_1_1_S1_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_MIXED_1_1_S1_0/measurement.json) | B_H10_EAST2_MIXED_1_1_N2_V1 | coordinate:HBM_ATTACH:AW:+1 | 2 | 206.500 | valid | 286.8242 | +0.0000% |
| [C1_H10_EAST2_STORE_ONLY_S0_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_STORE_ONLY_S0_0/measurement.json) | A_H10_EAST2_STORE_ONLY_N32 | coordinate:HBM_ATTACH:AW:+1 | 32 | 409.500 | valid | 185.4769 | +0.0000% |
| [C1_H10_EAST2_STORE_ONLY_S1_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/C1_H10_EAST2_STORE_ONLY_S1_0/measurement.json) | C0_H10_EAST2_STORE_ONLY_S0_0 | coordinate:HBM_ATTACH:AW:+1 | 32 | 409.500 | valid | 185.4769 | +0.0000% |

局部搜索中未能生成候选的项如下；合法整数深度、实际输入端口数和容量约束会限制交换。该搜索不构成所有异构分配的穷举。预算未执行项保留在 candidate history 中。

| 阶段 | 负载 | 搜索项 | 原因 |
| --- | --- | --- | --- |
| C | LOAD_ONLY | same_capacity_seed0 | no_legal_variant |
| C | LOAD_ONLY | same_capacity_seed1 | no_legal_variant |
| C | MIXED_1_1 | same_capacity_seed0 | no_legal_variant |
| C | MIXED_1_1 | same_capacity_seed1 | no_legal_variant |
| C | STORE_ONLY | same_capacity_seed1 | no_legal_variant |
| D | LOAD_ONLY | equal_capacity_uniform_control | no_legal_variant |
| D | MIXED_1_1 | equal_capacity_uniform_control | no_legal_variant |

## 理论带宽与实际限制

640 GB/s 是十个 HBM 后端的共享读写服务预算。单 flit 消除了数据包头的额外串行周期；内部路由仍受共享有向链路限制。

LOAD 约 80% 的数据要从东列跨到其余四列，五条向西链路的原始容量为 320 GB/s，因此该流量模式的理想 LOAD 上界约 400 GB/s。STORE 的 XY 路由将跨行写流量集中到东列纵向链路；AW、W、B 共用输出。

XY 对每条消息按其源和目的地址独立选路：STORE 的 W 在东列转向目标 HBM，LOAD 的 R 在接收 core 所在列转向；返回路径通常不等于请求路径的反向。因此即使读写均为单 flit beat，纵向链路压力仍不对称。

下表用完整实际计划 V 与每条链路全部通道 flit 数 F 计算 V×2 GHz/F；与全程带宽比较。该上界不保证可达，也不能直接作为 ROI 的同窗口利用率分母。

| 负载 | 最紧有向链路 | 链路计划上界 GB/s | 源 NI 时序上界 GB/s | HBM 计划上界 GB/s | 全程 GB/s | 全程/综合上界 | ROI/640 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | router:9->router:8 | 398.8315 | 510.4050 | 638.0062 | 363.3201 | 91.10% | 57.08% |
| STORE_ONLY | router:19->router:14 | 248.0451 | 1280.0000 | 638.0062 | 175.7520 | 70.85% | 28.98% |
| MIXED_1_1 | router:19->router:14 | 398.2015 | 981.0778 | 638.0062 | 250.5283 | 62.91% | 44.82% |

若保持相同路由与流量比例并要求全程 640 GB/s，最忙有向链路的必要发送速率分别为 LOAD_ONLY 1.605 flits/cycle、STORE_ONLY 2.580 flits/cycle、MIXED_1_1 1.607 flits/cycle，均超过当前 1 flit/cycle。调整 outstanding 或 FIFO 深度不改变这一容量条件。

NI 时序上界由冻结实现推导：[NetworkInterface.cc](../../../src/mem/ruby/network/garnet/NetworkInterface.cc) 在 flit 分配后处理返回 credit，结合 [InputUnit.cc](../../../src/mem/ruby/network/garnet/InputUnit.cc) 与 [NetworkLink.cc](../../../src/mem/ruby/network/garnet/NetworkLink.cc) 的当前 1-cycle 时序，一个 NI 输出 VC 最早在分配后的第 5 个周期重新用于下一 packet。四个 VC 对单 vnet 的持续注入上限为 4/5 flit/cycle，数据为 51.2 GB/s；不同 vnet 仍共享同一物理链路。下文单流诊断的 burst 时间序列支持这一推导。

单个 HBM 的计划上界为 64 GB/s 除以该口承担的有效字节比例，读写相加计入同一服务预算；有限 tile 轮转存在少量不均衡。综合上界取全部链路、源 NI 单 vnet 时序和 HBM 服务约束的最小值。

在 x=1/2 和 y=1/2 的中心划分上，每个方向都有五条链路、原始容量 320 GB/s。按实际流量比例计算，四个中心 cut 方向中最紧的有效带宽上界为 LOAD_ONLY 800.0000 GB/s、STORE_ONLY 752.9412 GB/s、MIXED_1_1 1219.1383 GB/s。这些合计约束较松；东侧附着区域及纵向单链路上的流量集中给出了更紧的限制。

逐链路与全部方向 cut 求和：[final_statistics.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/final_statistics.json)。

## 热点与仲裁阻塞区域

credit_stalls、no_vc_stalls 是实际 allocator 检查失败次数；sa_lost 是 SA-II 申请未获 grant 次数。它们不是全局停顿周期，不能相加为堵塞率；输入端记录的位置也不等于被争用的下游输出位置。下列图使用各负载的有效峰值配置。

HEAD_TAIL 单 flit 先申请空闲下游 VC；空闲 VC 已包含至少一个 credit，因此该路径的反压主要记入 no_vc_stalls。credit_stalls 为零不能解读为无反压。

### LOAD_ONLY

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_links.svg)

| 有向链路 | ROI 利用率 | AW/W/B/AR/R flits |
| --- | --- | --- |
| router:4->router:3 | 91.61% | 0/0/0/0/1678026 |
| router:9->router:8 | 91.58% | 0/0/0/0/1677408 |
| router:19->router:18 | 91.21% | 0/0/0/0/1670619 |
| router:14->router:13 | 91.15% | 0/0/0/0/1669459 |
| router:24->router:23 | 91.09% | 0/0/0/0/1668409 |

| Router | 输入 | 方向 | 通道 | SA-II 失选 | 无 VC | 缺 credit | 输入满占比 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 | 1 | Local | R | 630367 | 35410 | 0 | 25.88% |
| 4 | 2 | Local | R | 626654 | 43681 | 0 | 25.91% |
| 14 | 2 | Local | R | 623451 | 37491 | 0 | 25.69% |
| 9 | 2 | Local | R | 614666 | 16762 | 0 | 24.82% |
| 19 | 2 | Local | R | 612140 | 11184 | 0 | 24.61% |

sa_lost：[LOAD_ONLY_sa_lost.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_sa_lost.svg)。

no_vc_stalls：[LOAD_ONLY_no_vc_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_no_vc_stalls.svg)。

credit_stalls：[LOAD_ONLY_credit_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_credit_stalls.svg)。

max_full_fraction：[LOAD_ONLY_max_full_fraction.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_max_full_fraction.svg)。

每核有效带宽分布：[LOAD_ONLY_core_GBps.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/LOAD_ONLY_core_GBps.svg)。最小/平均/最大为 14.5581/14.6132/14.6833 GB/s；Jain=0.999994。

### STORE_ONLY

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_links.svg)

| 有向链路 | ROI 利用率 | AW/W/B/AR/R flits |
| --- | --- | --- |
| router:14->router:19 | 81.15% | 55871/892359/20016/0/0 |
| router:14->router:9 | 81.14% | 55821/892853/19426/0/0 |
| router:19->router:14 | 67.07% | 45674/731041/23500/0/0 |
| router:9->router:14 | 65.35% | 44513/711572/23600/0/0 |
| router:13->router:14 | 55.97% | 39289/628481/0/0/0 |

| Router | 输入 | 方向 | 通道 | SA-II 失选 | 无 VC | 缺 credit | 输入满占比 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 14 | 0 | Local | W | 543456 | 349865 | 0 | 0.00% |
| 19 | 0 | Local | W | 457487 | 1082157 | 0 | 0.00% |
| 9 | 0 | Local | W | 443557 | 1150620 | 0 | 0.00% |
| 14 | 4 | West | W | 423217 | 282915 | 0 | 0.00% |
| 19 | 4 | West | W | 420147 | 1102265 | 0 | 0.00% |

sa_lost：[STORE_ONLY_sa_lost.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_sa_lost.svg)。

no_vc_stalls：[STORE_ONLY_no_vc_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_no_vc_stalls.svg)。

credit_stalls：[STORE_ONLY_credit_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_credit_stalls.svg)。

max_full_fraction：[STORE_ONLY_max_full_fraction.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_max_full_fraction.svg)。

每核有效带宽分布：[STORE_ONLY_core_GBps.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/STORE_ONLY_core_GBps.svg)。最小/平均/最大为 1.6530/7.4191/22.6110 GB/s；Jain=0.624613。

### MIXED_1_1

![](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_links.svg)

| 有向链路 | ROI 利用率 | AW/W/B/AR/R flits |
| --- | --- | --- |
| router:14->router:19 | 75.52% | 68112/1089819/14491/68104/233472 |
| router:14->router:9 | 73.92% | 66269/1060277/14721/66481/234912 |
| router:9->router:14 | 72.58% | 64609/1033778/14848/64515/238720 |
| router:19->router:14 | 69.99% | 61662/986537/15231/62024/240544 |
| router:19->router:24 | 49.67% | 45252/724047/9054/45157/145920 |

| Router | 输入 | 方向 | 通道 | SA-II 失选 | 无 VC | 缺 credit | 输入满占比 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9 | 4 | West | W | 482811 | 1336440 | 0 | 48.34% |
| 19 | 4 | West | W | 465942 | 1431058 | 0 | 48.24% |
| 14 | 4 | West | W | 463921 | 405418 | 0 | 33.32% |
| 19 | 5 | North | W | 419634 | 1500514 | 0 | 53.92% |
| 9 | 3 | South | W | 410050 | 1261062 | 0 | 50.95% |

sa_lost：[MIXED_1_1_sa_lost.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_sa_lost.svg)。

no_vc_stalls：[MIXED_1_1_no_vc_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_no_vc_stalls.svg)。

credit_stalls：[MIXED_1_1_credit_stalls.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_credit_stalls.svg)。

max_full_fraction：[MIXED_1_1_max_full_fraction.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_max_full_fraction.svg)。

每核有效带宽分布：[MIXED_1_1_core_GBps.svg](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/MIXED_1_1_core_GBps.svg)。最小/平均/最大为 8.6444/11.4730/13.7740 GB/s；Jain=0.983476。

## DMA 与 HBM 服务状态

实际在途均值取 25 核各自 ROI 时间均值的算术平均，峰值取最大核峰值。后端 busy 为服务周期占该测量窗口周期比例；基础等待可以重叠。

| 负载 | 实际读 N 均值/峰值 | 实际写 N 均值/峰值 | HBM busy 最小–最大 | 后端 queue-full | SRAM bank conflict | SRAM reservation 拒绝 |
| --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 15.044/16 | 0.000/0 | 56.93%–57.26% | 0 | 0 | 0 |
| STORE_ONLY | 0.000/0 | 17.783/30 | 27.96%–29.82% | 0 | 0 | 0 |
| MIXED_1_1 | 1.618/2 | 1.984/2 | 44.70%–45.00% | 0 | 0 | 0 |

端点反压的 ROI 累计计数如下。AR/AW 为实际尝试被拒绝的次数，MessageBuffer 满计数取冻结 observer 的对应通道字段；各行 ROI 时长不同，不直接用跨行总数比较严重程度，也不相加为全局停顿率。

| 负载 | AR outstanding 拒绝 | AW outstanding 拒绝 | 响应预留拒绝 | AR/AW FIFO 拒绝 | quota 等待 | 源 W MessageBuffer 满 | 目标 R MessageBuffer 满 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 39,352,342 | 0 | 0 | 0 | 0 | 0 | 5,085,382 |
| STORE_ONLY | 0 | 0 | 0 | 0 | 0 | 24,179,641 | 0 |
| MIXED_1_1 | 38,836,260 | 43,659,046 | 0 | 0 | 0 | 0 | 0 |

## 瓶颈结论及证据边界

对于本次流量分布，640 GB/s 在进入仲裁效率讨论之前就被内部路由容量限制：LOAD 的东列向西出口、STORE/MIXED 的东列纵向共享输出，均给出更低的必要上界。十个独立 HBM 后端不能消除这些中间链路上的汇聚。

- LOAD_ONLY：有效峰值的最忙链路 `router:4->router:3` 利用率为 91.61%；十个 HBM 后端平均服务 busy 为 57.08%。最大 no-VC 检查失败发生在 router 4、Local 输入、R 通道，计 43,681 次。该位置说明反压传播到哪里，不能直接当作源头位置。

- STORE_ONLY：有效峰值的最忙链路 `router:14->router:19` 利用率为 81.15%；十个 HBM 后端平均服务 busy 为 28.98%。最大 no-VC 检查失败发生在 router 0、Local 输入、W 通道，计 4,478,713 次。该位置说明反压传播到哪里，不能直接当作源头位置。

- MIXED_1_1：有效峰值的最忙链路 `router:14->router:19` 利用率为 75.52%；十个 HBM 后端平均服务 busy 为 44.82%。最大 no-VC 检查失败发生在 router 22、West 输入、W 通道，计 3,575,702 次。该位置说明反压传播到哪里，不能直接当作源头位置。

FIFO 的因果证据来自相同 N 的实际容量对照；N 的因果证据来自相同 FIFO 的曲线。单 flit 模式下，VC 的 packet 占用与 free-credit 周转、每个输入每拍只向一个输出申请的两级仲裁，以及按 tile 聚集的目的地址都会影响剩余链路空闲。当前统计能定位争用与反压区域，但没有逐项反事实实验，不能将理论上界与实测之间的差额精确分摊给每一个机制，也不能仅凭 no-VC 次数断言增加 VC 必然有效。

输出仲裁按输入端口轮转，见 [SwitchAllocator.cc](../../../src/mem/ruby/network/garnet/SwitchAllocator.cc)；West 输入可能承载多个远端 core，而 Local 输入只承载一个本地 core。多个上游 core 因而共享一个输入的仲裁机会，继续汇聚时再次共享份额，端口轮转不能保证逐 core 公平。每核带宽图中的东快西慢与该机制一致；增加 FIFO 深度不改变仲裁规则。

## 每个 HBM 口的完成带宽

以下按 DMA 完成数据的目标归属统计，和后端内部服务速率分开。

| 负载 | HBM 口 | Router | 读 GB/s | 写 GB/s | 合计 GB/s |
| --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | 0 | 4 | 36.5870 | 0.0000 | 36.5870 |
| LOAD_ONLY | 1 | 4 | 36.6451 | 0.0000 | 36.6451 |
| LOAD_ONLY | 2 | 9 | 36.6367 | 0.0000 | 36.6367 |
| LOAD_ONLY | 3 | 9 | 36.4796 | 0.0000 | 36.4796 |
| LOAD_ONLY | 4 | 14 | 36.4506 | 0.0000 | 36.4506 |
| LOAD_ONLY | 5 | 14 | 36.4791 | 0.0000 | 36.4791 |
| LOAD_ONLY | 6 | 19 | 36.5546 | 0.0000 | 36.5546 |
| LOAD_ONLY | 7 | 19 | 36.5613 | 0.0000 | 36.5613 |
| LOAD_ONLY | 8 | 24 | 36.4947 | 0.0000 | 36.4947 |
| LOAD_ONLY | 9 | 24 | 36.4411 | 0.0000 | 36.4411 |
| STORE_ONLY | 0 | 4 | 0.0000 | 18.1042 | 18.1042 |
| STORE_ONLY | 1 | 4 | 0.0000 | 17.8922 | 17.8922 |
| STORE_ONLY | 2 | 9 | 0.0000 | 18.1034 | 18.1034 |
| STORE_ONLY | 3 | 9 | 0.0000 | 18.3643 | 18.3643 |
| STORE_ONLY | 4 | 14 | 0.0000 | 18.8741 | 18.8741 |
| STORE_ONLY | 5 | 14 | 0.0000 | 19.0826 | 19.0826 |
| STORE_ONLY | 6 | 19 | 0.0000 | 18.9033 | 18.9033 |
| STORE_ONLY | 7 | 19 | 0.0000 | 18.8930 | 18.8930 |
| STORE_ONLY | 8 | 24 | 0.0000 | 18.8466 | 18.8466 |
| STORE_ONLY | 9 | 24 | 0.0000 | 18.4132 | 18.4132 |
| MIXED_1_1 | 0 | 4 | 14.8911 | 13.8527 | 28.7438 |
| MIXED_1_1 | 1 | 4 | 13.8501 | 14.8769 | 28.7270 |
| MIXED_1_1 | 2 | 9 | 14.9372 | 13.8118 | 28.7491 |
| MIXED_1_1 | 3 | 9 | 13.8087 | 14.7977 | 28.6063 |
| MIXED_1_1 | 4 | 14 | 14.8659 | 13.8213 | 28.6871 |
| MIXED_1_1 | 5 | 14 | 13.8234 | 14.8008 | 28.6242 |
| MIXED_1_1 | 6 | 19 | 14.8234 | 13.7971 | 28.6205 |
| MIXED_1_1 | 7 | 19 | 13.7882 | 14.8690 | 28.6572 |
| MIXED_1_1 | 8 | 24 | 14.7914 | 13.8165 | 28.6079 |
| MIXED_1_1 | 9 | 24 | 13.8360 | 14.9650 | 28.8010 |

## 与原 H10 实验比较

两次实验都采用 25 核、16 MiB/core、64 KiB tile 的均匀流量。原 H10 为西侧五口＋东侧五口、16 B flit、W/R 每 beat 三个 flit；本次为东侧十口和单 flit beat。拓扑与包格式同时变化，下表是配置整体的比较，不能将全部增益归因于其中一个改动。

| 负载 | 原 H10 有效峰值 | 原 N | 原 GB/s | 新 N | 新 GB/s | 倍率 |
| --- | --- | --- | --- | --- | --- | --- |
| LOAD_ONLY | [E1_H10_LOAD_ONLY_S2_N32](../../../.tmp/mesh-outstanding-experiment.JMEEG1/cases/E1_H10_LOAD_ONLY_S2_N32/measurement.json) | 32 | 159.5825 | 16 | 365.3298 | 2.289× |
| STORE_ONLY | [D_H10_STORE_ONLY_0](../../../.tmp/mesh-outstanding-experiment.JMEEG1/cases/D_H10_STORE_ONLY_0/measurement.json) | 32 | 102.9871 | 32 | 185.4769 | 1.801× |
| MIXED_1_1 | [C0_H10_MIXED_1_1_S0_1](../../../.tmp/mesh-outstanding-experiment.JMEEG1/cases/C0_H10_MIXED_1_1_S0_1/measurement.json) | 8 | 165.3431 | 2 | 286.8242 | 1.735× |

## 长验证与工况长度

以下 20 MiB 点独立验证正式推荐、峰值及邻近已测配置。邻近点可能采用相同 N、不同 FIFO，具体参照以 validation_of 为准。未收敛结果保留并限制对应配置的稳定性结论。

| 20 MiB case | 角色 | 16 MiB 参照 | N | 状态 | GB/s | 相对变化 | 三个子窗口 GB/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [F_long_H10_EAST2_LOAD_ONLY_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_LOAD_ONLY_0/measurement.json) | recommendation/peak | B_H10_EAST2_LOAD_ONLY_N16_V0 | 16 | valid | 365.6596 | +0.090% | [366.9311, 364.4807, 365.5671] |
| [F_long_H10_EAST2_LOAD_ONLY_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_LOAD_ONLY_1/measurement.json) | neighbor | E0_H10_EAST2_LOAD_ONLY_S0_N8 | 8 | valid | 333.7412 | +0.075% | [334.0986, 330.8243, 336.3008] |
| [F_long_H10_EAST2_MIXED_1_1_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_MIXED_1_1_0/measurement.json) | recommendation/peak | B_H10_EAST2_MIXED_1_1_N2_V0 | 2 | valid | 288.8016 | +0.689% | [284.7651, 289.9171, 291.7226] |
| [F_long_H10_EAST2_MIXED_1_1_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_MIXED_1_1_1/measurement.json) | neighbor | E0_H10_EAST2_MIXED_1_1_S0_N1 | 1 | valid | 180.3496 | -0.176% | [180.828, 180.426, 179.7948] |
| [F_long_H10_EAST2_STORE_ONLY_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_0/measurement.json) | recommendation | B_H10_EAST2_STORE_ONLY_N4_V0 | 4 | unconverged | 183.1333 | -1.074% | [184.6098, 185.8532, 178.9369] |
| [F_long_H10_EAST2_STORE_ONLY_1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_1/measurement.json) | peak | A_H10_EAST2_STORE_ONLY_N32 | 32 | valid | 182.2124 | -1.760% | [183.4744, 182.9641, 180.1987] |
| [F_long_H10_EAST2_STORE_ONLY_2](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_2/measurement.json) | neighbor | B_H10_EAST2_STORE_ONLY_N4_V1 | 4 | unconverged | 183.1333 | -1.074% | [184.6098, 185.8532, 178.9369] |

## 固定硬件的分布与距离诊断

hotspot 将一半 tile 流量指定到单一 HBM，其余流量分配到其他口；near/far 分别只启用距离 target 0 最近/最远的一个 core，N 沿用固定硬件点。距离诊断并非 N=1 的空载 RTT，不能把带负载的 AXI 延迟直接当作链路往返基准。

| case_id | 活跃 core | 分布/目标 | N | 状态 | ROI GB/s | 全程 GB/s | 综合计划上界 GB/s | AXI 平均/P99 μs | Jain |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [F_H10_EAST2_LOAD_ONLY_H0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_LOAD_ONLY_H0/measurement.json) | 0–24 | hotspot/0 | 128 | valid | 99.3972 | 99.3679 | 102.4000 | 8.4115/30.8925 | 1.000000 |
| [F_H10_EAST2_LOAD_ONLY_H9](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_LOAD_ONLY_H9/measurement.json) | 0–24 | hotspot/9 | 128 | valid | 99.3814 | 99.3508 | 102.4000 | 8.4142/30.9090 | 0.999998 |
| [F_H10_EAST2_MIXED_1_1_H0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H0/roi_probe/measurement.json) | 0–24 | hotspot/0 | 128 | failed | — | — | 128.0000 | — | — |
| [F_H10_EAST2_MIXED_1_1_H9](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H9/roi_probe/measurement.json) | 0–24 | hotspot/9 | 128 | failed | — | — | 128.0000 | — | — |
| [F_H10_EAST2_STORE_ONLY_H0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_STORE_ONLY_H0/measurement.json) | 0–24 | hotspot/0 | 128 | valid | 117.2489 | 96.8407 | 120.4706 | 1.8847/19.7060 | 0.387808 |
| [F_H10_EAST2_STORE_ONLY_H9](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_STORE_ONLY_H9/measurement.json) | 0–24 | hotspot/9 | 128 | valid | 117.3773 | 97.1064 | 120.4706 | 1.8803/19.3130 | 0.393849 |
| [F_diagnostic_H10_EAST2_far](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_diagnostic_H10_EAST2_far/measurement.json) | [20] | single_target/0 | 128 | valid | 46.3562 | 46.3642 | 51.2000 | 0.7457/1.3390 | 1.000000 |
| [F_diagnostic_H10_EAST2_near](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_diagnostic_H10_EAST2_near/measurement.json) | [4] | single_target/0 | 128 | valid | 46.8868 | 46.8950 | 51.2000 | 0.7297/1.3230 | 1.000000 |

每个诊断点的逐链路、cut、源 NI 和 HBM 服务上界：[diagnostics.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/diagnostics.json)。综合计划上界使用该点的实际流量分配，应与全程带宽比较；单口热点的服务上界不能直接沿用十口均衡的 640 GB/s。

对于 50% 单口热点，STORE 目标 HBM 前的本地链路需要共同承载一个 AW 和十六个 W flit，因此该口有效数据容量为 64×16/17=60.2353 GB/s，对应合计计划上界 120.4706 GB/s。即使该链路占满，数据服务占比也只有 16/17≈94.12%；后端 busy 低于 100% 并不能排除端口前链路已经饱和。LOAD 的热点口 R 注入则受四个 NI VC 的周转约束，51.2 GB/s 对应合计计划上界 102.4 GB/s。下表给出各点实际窗口内的利用率。

| 诊断点 | 状态 | 最高 HBM busy | 最忙有向链路 / ROI 利用率 | 分布图 |
| --- | --- | --- | --- | --- |
| F_H10_EAST2_LOAD_ONLY_H0 | valid | 口 0 / 77.65% | endpoint:25->router:4 / 77.65% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H0_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H0_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H0_no_vc_stalls.svg) |
| F_H10_EAST2_LOAD_ONLY_H9 | valid | 口 9 / 77.63% | endpoint:34->router:24 / 77.64% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H9_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H9_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_LOAD_ONLY_H9_no_vc_stalls.svg) |
| F_H10_EAST2_STORE_ONLY_H0 | valid | 口 0 / 91.53% | router:4->endpoint:25 / 97.23% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H0_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H0_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H0_no_vc_stalls.svg) |
| F_H10_EAST2_STORE_ONLY_H9 | valid | 口 9 / 91.86% | router:24->endpoint:34 / 97.59% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H9_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H9_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_H10_EAST2_STORE_ONLY_H9_no_vc_stalls.svg) |
| F_diagnostic_H10_EAST2_far | valid | 口 0 / 72.43% | endpoint:25->router:4 / 72.43% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_far_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_far_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_far_no_vc_stalls.svg) |
| F_diagnostic_H10_EAST2_near | valid | 口 0 / 73.26% | endpoint:25->router:4 / 73.26% | [链路](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_near_links.svg) / [SA-II](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_near_sa_lost.svg) / [无 VC](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_near_no_vc_stalls.svg) |

链路图绘制 Router 间的流量，并在 HBM 方框内列出本地链路两个方向的实际利用率；全部链路见 measurement 的 per_link。near 的 core 和 HBM 同挂一个 Router。诊断图使用该点实际 ROI 的计数；unconverged 图仍可展示流量与反压位置，但不构成稳定吞吐的证据。

以下工况没有通过功能与测量验证，其带宽、时延及热点分布不作为结论依据。主机超时只表示未在预设时限内完成，不能据此判断仿真死锁。失败阶段、退出状态和原始文件均保留，并核对执行时记录的文件哈希。

| 失败工况 | 阶段 | 主机秒 | 超时 | 退出码 | 执行记录 |
| --- | --- | --- | --- | --- | --- |
| F_H10_EAST2_MIXED_1_1_H0 | probe | 3601.57 | True | -15 | [execution.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H0/roi_probe/execution.json) |
| F_H10_EAST2_MIXED_1_1_H9 | probe | 3601.30 | True | -15 | [execution.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H9/roi_probe/execution.json) |

### 单流的 NI 周转与 DMA 发起节奏

下表直接从完整 burst_timings 与 dma_timings 取间隔，所有出现的不同值均列出；各值的出现次数保存在诊断 JSON 中。

| 诊断点 | tile 内 burst 完成间隔 ns | 首 burst 应答时延 ns | 末 RLAST 至下一 tile AR ns | 相邻 tile 首 AR 间隔 ns | 由 tile 周期计算 GB/s | Router SA/无 VC/缺 credit | 在途曲线 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F_diagnostic_H10_EAST2_far | 10 | 142 | 1.5 | 1413.5 | 46.3643 | 0/0/0 | [前两个 tile](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_far_outstanding.svg) |
| F_diagnostic_H10_EAST2_near | 10 | 126 | 1.5 | 1397.5 | 46.8952 | 0/0/0 | [前两个 tile](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/analysis/F_diagnostic_H10_EAST2_near_outstanding.svg) |

512 B / 10 ns = 51.2 GB/s 是这两个单流点在 tile 内的实测响应节奏，与四个 NI VC、五周期复用间隔的推导一致。Router 争用计数为零并不排除源 NI 的周转限制；ni_vc_busy_cycles 则是非空闲 VC 持续周期之和，不是 VC 分配失败次数。

[axi_tensor_dma_engine.cc](../../../src/dev/ai_mesh/axi_tensor_dma_engine.cc) 的 driveReadDescriptor/driveWriteDescriptor 只推进各自队头，完成后才移出队列；两个 SRAM 槽并不使同方向两个 descriptor 同时发起。单流每个 tile 的首响应等待和切换间隔因而重复出现，解释了 tile 周期计算带宽低于 51.2 GB/s。

far 比 near 多八个单程 Router 间 hop；本轮每 hop 的 Router＋link 共两周期，在 2 GHz 下增加 16 ns 往返时间。实际首 burst 时延和 tile 发起周期都多 16 ns，平均 AXI 时延与 P99 的差也为 16 ns。这是相同负载设置下的距离差分。

## 可信度与全部异常结果

已纳入物理结果 107 个，状态计数 `{"valid": 90, "unconverged": 15, "failed": 2}`；唯一物理身份 107，duplicates=0、conflicts=0、尚未绑定结果=0。missing=0。

unconverged 的功能校验可通过，但稳定性未通过，因此不参与有效峰值和推荐。failed 不作为性能证据。所有逻辑别名及预算未执行项保留在 candidate history 中。

本批次 interrupted/recovered=0，重跑归档数=0；probe 与 fixed ROI 是预定的两阶段，均保留进程身份与终态记录。两个 probe 超时按 failed 保留，没有重新提交。

| 阶段 | 提交物理配置 | valid | unconverged | failed | 逻辑复用 | 预算未提交候选 | 不支持 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | 24 | 20 | 4 | 0 | 0 | 0 | 0 |
| B | 41 | 35 | 6 | 0 | 9 | 0 | 0 |
| C | 12 | 12 | 0 | 0 | 0 | 75 | 0 |
| D | 6 | 6 | 0 | 0 | 0 | 30 | 0 |
| E | 9 | 6 | 3 | 0 | 47 | 2 | 0 |
| F | 15 | 11 | 2 | 2 | 3 | 0 | 0 |

逻辑复用项关联已有物理结果，不产生新执行；未提交候选不计入物理 missing。物理身份审计的 duplicates=0 表示已提交配置没有重复，不能据此推断搜索空间已被穷举。

本实验采用确定性流量与固定时序。三个 ROI 子窗口用于检查时间稳定性，20 MiB 点用于检查工作量长度敏感性；它们不构成多个独立随机重复样本，本报告不据此给出统计置信区间。

| case_id | 状态 | 原因 |
| --- | --- | --- |
| [A_H10_EAST2_LOAD_ONLY_N32](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_LOAD_ONLY_N32/measurement.json) | unconverged | unstable_subwindows |
| [A_H10_EAST2_MIXED_1_1_N64](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_MIXED_1_1_N64/measurement.json) | unconverged | unstable_subwindows |
| [A_H10_EAST2_STORE_ONLY_N16](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N16/measurement.json) | unconverged | unstable_subwindows |
| [A_H10_EAST2_STORE_ONLY_N8](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/A_H10_EAST2_STORE_ONLY_N8/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_LOAD_ONLY_N32_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V0/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_LOAD_ONLY_N32_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V1/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_LOAD_ONLY_N32_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_LOAD_ONLY_N32_V3/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_STORE_ONLY_N8_V0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V0/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_STORE_ONLY_N8_V1](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V1/measurement.json) | unconverged | unstable_subwindows |
| [B_H10_EAST2_STORE_ONLY_N8_V3](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/B_H10_EAST2_STORE_ONLY_N8_V3/measurement.json) | unconverged | unstable_subwindows |
| [E0_H10_EAST2_STORE_ONLY_S1_N16](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/E0_H10_EAST2_STORE_ONLY_S1_N16/measurement.json) | unconverged | unstable_subwindows |
| [E0_H10_EAST2_STORE_ONLY_S2_N16](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/E0_H10_EAST2_STORE_ONLY_S2_N16/measurement.json) | unconverged | unstable_subwindows |
| [E1_H10_EAST2_STORE_ONLY_S3_N16](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/E1_H10_EAST2_STORE_ONLY_S3_N16/measurement.json) | unconverged | unstable_subwindows |
| [F_H10_EAST2_MIXED_1_1_H0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H0/roi_probe/measurement.json) | failed | host_timeout |
| [F_H10_EAST2_MIXED_1_1_H9](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_H10_EAST2_MIXED_1_1_H9/roi_probe/measurement.json) | failed | host_timeout |
| [F_long_H10_EAST2_STORE_ONLY_0](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_0/measurement.json) | unconverged | unstable_subwindows |
| [F_long_H10_EAST2_STORE_ONLY_2](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/cases/F_long_H10_EAST2_STORE_ONLY_2/measurement.json) | unconverged | unstable_subwindows |

完整统计：[sweep_summary.csv](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/sweep_summary.csv)；候选覆盖：[family_coverage.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/family_coverage.json)；执行历史：[candidate_history.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/candidate_history.json)。

## 可复现输入

构建、packetization/runner 测试与真实 gem5 小规模校验：[validation](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/validation)；记录说明：[README.md](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/README.md)。

源摘要：`c0d5b5f8def35a3c8eb613031f36cc9825c8a9fb08f9dcac6d3133e7b5b2ebaa`；二进制摘要：`554520f480dc494420777949dce3cc42ef12bb382e409449ac6a30f06668c33e`。

源文件清单：[frozen_source.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/frozen_source.json)；冻结配置：[profile.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/profile.json)；命令：[launch.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/launch.json)。

源文件归档：[source.tar.gz](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/source.tar.gz)；归档校验：[archive_verification.json](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/archive_verification.json)；冻结二进制：[gem5.opt](../../../.tmp/mesh-east-dual-hbm-32B.9luwm2qi/provenance/gem5.opt)。

报告在实验结束后发布；实验身份采用执行时冻结源文件，报告发布不回写原始 measurement。


## 双 lane LOAD tensor 热点图

本节采用 5×5 Mesh、东侧 10 个 HBM 口的 LOAD_ONLY：25 核各读取 16 MiB tensor，64 KiB tile，N=16；32 B flit、W/R sideband、每输入每 vnet 4 个 VC、每 VC 深度 1。两个 lane 使用独立仲裁，外部 P 接入保持共享。冻结配置见 [profile.json](../../../.tmp/dual-lane-load-09s4v2wv/profile.json)，实际端口与参数见 [config.json](../../../.tmp/dual-lane-load-09s4v2wv/load/raw/config.json)。

实际仿真及数据、credit、ROI 稳定性校验均通过（`valid`）。固定 ROI 为 185726401–928616001 tick，共 742.8896 μs；有效 LOAD 带宽 **451.0481 GB/s**，全程带宽 **448.5659 GB/s**，两份 Router 输入 FIFO 数据容量合计 145.000 KiB。结果以 [measurement.json](../../../.tmp/dual-lane-load-09s4v2wv/load/measurement.json) 为准。

![双 lane LOAD tensor 链路利用率与 R 通道仲裁压力](../../../.tmp/dual-lane-load-09s4v2wv/analysis/dual_lane_load_hotspots.png)

[矢量 SVG](../../../.tmp/dual-lane-load-09s4v2wv/analysis/dual_lane_load_hotspots.svg)。左右分别为 lane 0 普通链路与 lane 1 `_ext` 链路，使用相同色标。上图是固定 ROI 内每条有向物理链路的利用率，分母为该 lane 的 1 flit/cycle；HBM 框标注共享接入口利用率，在两图中重复展示。下图按 router、lane 汇总 R 输入 VC 的 SA-II 失选请求数，再除以 ROI 周期数，单位是请求/周期，不是堵塞周期百分比。

| lane | 最忙内部有向链路 | ROI 利用率 | R 仲裁压力最高 router / 请求每周期 |
| --- | --- | --- | --- |
| lane 0 | R4 → R3（West） | 89.40% | R14 / 0.6788 |
| lane 1 | R14 → R13（West_ext） | 24.70% | R14 / 0.0144 |

本负载的热点集中在 lane 0 的东侧 HBM 接入 router 及向西链路；lane 1 承载了实际 R 流量，链路利用率与仲裁压力均较低。

运行与复现索引：[README](../../../.tmp/dual-lane-load-09s4v2wv/README.md)、[命令](../../../.tmp/dual-lane-load-09s4v2wv/launch.json)、[执行及文件摘要](../../../.tmp/dual-lane-load-09s4v2wv/load/execution.json)。图表数据：[物理链路 CSV](../../../.tmp/dual-lane-load-09s4v2wv/analysis/physical_links.csv)、[仲裁压力 CSV](../../../.tmp/dual-lane-load-09s4v2wv/analysis/router_pressure.csv)、[热点摘要](../../../.tmp/dual-lane-load-09s4v2wv/analysis/hotspot_summary.json)。


## 写 tensor YX 路由对照

2026-09-08。同一构建的单 lane STORE_ONLY 对照显示，AW/W 改用 YX 后，**全程写带宽从 175.7520 提升到 275.5765 GB/s（+56.80%），400 MiB 写作业完成时间缩短 36.22%**。YX 的数据、逐链路流量、outstanding 和排空校验通过；ROI 子窗口未达到原定 2% 稳定性门槛，状态为 `unconverged`。

### 对照配置与结果

沿用原 STORE 峰值点：5×5 Mesh、东侧双 HBM、单 lane、N=32/核/方向、25 核各 16 MiB、64 KiB tile、32 B 单 flit、每输入每 vnet 4 个 VC；AW/W/B/AR/R 深度为 `[4, 8, 4, 4, 8]`，Router FIFO 数据容量为 406 KiB。

唯一变化为 profile 的 `network.yx_vnets`：XY 为 `[]`，YX 为 `[0, 1]`，即 AW/W 先走 Y 再走 X；B、AR、R 保持 XY。两组 `program.mshb` 字节及 workload digest 相同，源码、二进制、地址分布和全部资源参数一致。路由收益以本节的同构建单 lane XY 对照计算。

输入：[XY profile](../../../.tmp/write-yx-5wys1_tg/xy_profile.json)、[YX profile](../../../.tmp/write-yx-5wys1_tg/yx_profile.json)、[执行命令](../../../.tmp/write-yx-5wys1_tg/launch.json)；正式结果：[XY measurement](../../../.tmp/write-yx-5wys1_tg/xy/measurement.json)、[YX measurement](../../../.tmp/write-yx-5wys1_tg/yx/measurement.json)。

| 指标 | XY | AW/W YX | 相对变化 |
| --- | --- | --- | --- |
| 全程 GB/s | 175.7520 | 275.5765 | +56.80% |
| 写作业 makespan μs | 2386.4895 | 1522.0110 | −36.22% |
| ROI GB/s | 185.4769 | 342.4717 | +84.64%，YX 未收敛 |
| ROI 发起事务 AXI 平均 μs | 1.2275 | 0.7100 | −42.16% |
| ROI 发起事务 AXI P99 μs | 7.3915 | 3.3790 | −54.29% |
| 每核 ROI 带宽 Jain | 0.624613 | 0.831562 | — |
| HBM 平均服务 busy | 28.98% | 53.51% | — |
| 正确性 / 排空 | pass / true | pass / true | — |
| 稳定性状态 | valid | unconverged | `unstable_subwindows` |

XY 重跑与前文原 STORE 峰值点的 ROI、全程带宽一致。两组各先运行一次 ROI 探测，再按原规则冻结三个连续子窗口并重跑采样；探测结果分别保存在 [XY roi_probe](../../../.tmp/write-yx-5wys1_tg/xy/roi_probe/measurement.json) 和 [YX roi_probe](../../../.tmp/write-yx-5wys1_tg/yx/roi_probe/measurement.json)。

| 路由 | 固定 ROI tick | ROI 完成 MiB | 三个子窗口 GB/s |
| --- | --- | --- | --- |
| XY | 149274701–745827501 | 105.5210 | 188.7761, 184.5225, 183.1322 |
| AW/W YX | 140179701–700352501 | 182.9561 | 330.9086, 344.9340, 351.5724 |

YX 的 ROI 带宽沿三个窗口上升；342.4717 GB/s 是本次窗口的观测均值。稳态峰值与配置推荐仍需收敛验证，本节不改写前文的排名与长验证结论。

### W 通道阻塞与链路分布

计数定义沿用[配置与测量设计](../../../docs/ai_mesh/outstanding_router_fifo_experiment_design.md#router-统计口径)。下表按全网 W 输入 VC 汇总；单位数据项以各自 ROI 内成功 B 对应的完成 MiB 归一化。

| W 通道指标 | XY | AW/W YX | 相对变化 |
| --- | --- | --- | --- |
| no-VC 检查失败 / 完成 MiB | 1,375,758.75 | 667,951.02 | −51.45% |
| SA-II 失选请求 / 完成 MiB | 80,283.34 | 58,328.38 | −27.35% |
| 源 MessageBuffer stall cycles / 完成 MiB | 229,145.31 | 107,265.23 | -53.19% |
| SA-II 失选请求 / router cycle | 7.1004 | 9.5252 | +34.15% |
| credit 检查失败次数 | 0 | 0 | — |

YX 每单位完成数据的等待压力下降，同时承载了更多流量；全网 SA-II 累计失选从 8,471,578 增至 10,671,530，每周期竞争也上升。上述计数不能解释成阻塞周期百分比。

| 链路指标 | XY | AW/W YX |
| --- | --- | --- |
| ROI 最忙内部有向链路 / 利用率 | `router:14->router:19` / 81.15% | `router:23->router:24` / 75.36% |
| ROI 东列纵向链路最高利用率 | 81.15% | 51.11% |
| 全程流量比例对应的链路必要上界 GB/s | 248.0451 | 375.3709 |

全程 W 的纵向 flit-hop 总数均为 20,975,616：XY 全部集中在 x=4；YX 在 x=0…4 分别为 4,194,304 / 4,196,352 / 4,194,304 / 4,196,352 / 4,194,304，约各占 20%。AW/W/B 总跳数均不变，逐链路计数已与独立 oracle 核对。因此，此次收益来自路径分布改变；东列纵向汇聚被分散，最忙区域转到各行进入东列的向东链路。

必要带宽上界按完整 400 MiB 的流量比例计算，见 [XY 上界](../../../.tmp/write-yx-5wys1_tg/analysis/xy_bounds.json)、[YX 上界](../../../.tmp/write-yx-5wys1_tg/analysis/yx_bounds.json)；它与有限 ROI 的流量比例分开使用。YX 的最忙 ROI 链路仍有空闲，640 GB/s HBM 总服务预算也仍高于该负载的链路必要上界。

![YX 写 tensor 的 ROI 有向链路利用率](../../../.tmp/write-yx-5wys1_tg/analysis/yx_links.svg)

同色标 [XY 热点图](../../../.tmp/write-yx-5wys1_tg/analysis/xy_links.svg)；原始图表数据：[XY 链路 CSV](../../../.tmp/write-yx-5wys1_tg/analysis/xy_links.csv)、[YX 链路 CSV](../../../.tmp/write-yx-5wys1_tg/analysis/yx_links.csv)、[XY W 压力](../../../.tmp/write-yx-5wys1_tg/analysis/xy_W_pressure.csv)、[YX W 压力](../../../.tmp/write-yx-5wys1_tg/analysis/yx_W_pressure.csv)。

汇总与复现索引：[comparison.json](../../../.tmp/write-yx-5wys1_tg/analysis/comparison.json)、[README](../../../.tmp/write-yx-5wys1_tg/README.md)。构建及 57 项 Python 定向测试、6 项真实 gem5 定向测试通过；两组各两次物理运行均正常退出。源码、二进制、执行身份及原始文件摘要的审计见 [identity.json](../../../.tmp/write-yx-5wys1_tg/provenance/identity.json)、[XY execution](../../../.tmp/write-yx-5wys1_tg/xy/execution.json)、[YX execution](../../../.tmp/write-yx-5wys1_tg/yx/execution.json)、[进程终态](../../../.tmp/write-yx-5wys1_tg/pair_process.json)。


## MIXED 读 XY 写 YX 路由对照

2026-09-08。固定单 lane、N=32 的 MIXED_1_1 中，**读 XY、写 AW/W YX 的全程合计带宽为 491.1622 GB/s，比全 XY 提升 78.25%；ROI 合计为 532.2658 GB/s，提升 100.77%**。两组均为 `valid`，数据、逐链路流量、outstanding、排空、三窗口稳定性及 ROI 读写比例校验全部通过。读、写吞吐均提高，但读单事务延迟增加。

### 配置与吞吐

硬件沿用[上一节写 tensor 对照](#写-tensor-yx-路由对照)：单 lane、N=32/核/方向，FIFO 深度 `[4, 8, 4, 4, 8]`、406 KiB。MIXED 每核读 8 MiB、写 8 MiB，25 核合计读写各 200 MiB；64 KiB tile、地址分布与资源参数保持一致。本节衡量固定配置的路由收益。

唯一自变量为 `network.yx_vnets`：对照组 `[]`，实验组 `[0, 1]`。实验组 AR/R 使用 XY，AW/W 使用 YX，B 返回仍为 XY。两组 workload 二进制逐字节相同；AR、R、B 的全程逐链路流量 oracle 也相同。使用上一节已验证的同一二进制，执行期间源码冻结。

输入：[XY profile](../../../.tmp/mixed-xy-yx-gzah3ty3/xy_profile.json)、[读 XY／写 YX profile](../../../.tmp/mixed-xy-yx-gzah3ty3/yx_profile.json)、[命令](../../../.tmp/mixed-xy-yx-gzah3ty3/launch.json)；正式测量：[全 XY](../../../.tmp/mixed-xy-yx-gzah3ty3/xy/measurement.json)、[读 XY／写 YX](../../../.tmp/mixed-xy-yx-gzah3ty3/yx/measurement.json)。

| 指标 | 全 XY | 读 XY／写 YX | 相对变化 |
| --- | --- | --- | --- |
| 全程合计 GB/s | 275.5500 | 491.1622 | +78.25% |
| 400 MiB 作业 makespan μs | 1522.1570 | 853.9550 | −43.90% |
| ROI 合计 GB/s | 265.1177 | 532.2658 | +100.77% |
| ROI 读 GB/s | 132.8361 | 266.1380 | +100.35% |
| ROI 写 GB/s | 132.2816 | 266.1278 | +101.18% |
| ROI 完成字节中读占比 | 50.1046% | 50.0010% | — |
| 每核 ROI 合计带宽 Jain | 0.611859 | 0.985305 | — |
| HBM 平均服务 busy | 41.40% | 83.16% | — |
| 状态 | valid | valid | — |

全 XY 重现了原实验 N=32 MIXED 点的 ROI 与全程带宽。每组先探测 ROI，再冻结边界重跑；正式吞吐与各自探测值一致。探测证据：[XY](../../../.tmp/mixed-xy-yx-gzah3ty3/xy/roi_probe/measurement.json)、[读 XY／写 YX](../../../.tmp/mixed-xy-yx-gzah3ty3/yx/roi_probe/measurement.json)。

| 路由 | 固定 ROI tick | ROI 完成读 / 写 MiB | 三个子窗口合计 GB/s |
| --- | --- | --- | --- |
| 全 XY | 97555801–487233001 | 49.3652 / 49.1592 | 263.8866, 263.4215, 268.0451 |
| 读 XY／写 YX | 137870501–688806501 | 139.8325 / 139.8271 | 529.5355, 537.5955, 529.6665 |

两组均满足三窗口相对均值偏差 ≤2%、ROI 读占比 50%±2 个百分点。全程带宽使用各自相同 400 MiB 作业的完整 makespan。

### 读写时延与仲裁压力

AXI 时延取各自 ROI 内发起事务的完整响应时间，分别统计 AR→RLAST 与 AW→B；量化函数复用 [latency_summary](../../../util/mesh_ir/mesh_ir/experiment/metrics.py)。

| 方向 | 全 XY 平均 / P99 μs | 读 XY／写 YX 平均 / P99 μs | P99 变化 |
| --- | --- | --- | --- |
| 读 AR→RLAST | 0.6760 / 2.0840 | 1.2283 / 3.2580 | +56.33% |
| 写 AW→B | 1.7250 / 10.7375 | 1.0534 / 3.7090 | -65.46% |

读吞吐提高的同时，读 P99 上升 56.33%；写 P99 下降 65.46%。每核平均实际读 outstanding 从 7.021 升至 25.533，写从 17.832 升至 21.908，两方向的峰值均仍为 32。HBM 后端 `queue_full_cycles` 全口合计由 0 增至 114,618。更高的读并发、仲裁竞争和后端压力与读时延增加同时出现，吞吐收益伴随这一时延代价。

计数口径沿用[实验设计](../../../docs/ai_mesh/outstanding_router_fifo_experiment_design.md#router-统计口径)；R 项按 ROI 内 SRAM commit 的读 MiB，W 项按成功 B 对应的写 MiB 归一化。

| 数据通道指标 | 全 XY | 读 XY／写 YX | 相对变化 |
| --- | --- | --- | --- |
| R：no-VC 检查失败 / 完成 MiB | 56,448.05 | 59,184.73 | +4.85% |
| R：SA-II 失选请求 / 完成 MiB | 13,016.27 | 20,508.29 | +57.56% |
| W：no-VC 检查失败 / 完成 MiB | 2,147,063.53 | 559,775.56 | -73.93% |
| W：SA-II 失选请求 / 完成 MiB | 81,663.65 | 50,561.69 | -38.09% |

两组 R/W 的 credit 检查失败次数和最大 FIFO full fraction 均为 0。W 每单位数据的仲裁失败下降，R 的 SA-II 竞争上升；这些是检查失败或失选请求次数，不是阻塞周期百分比。完整计数及每周期值见 [XY 压力 CSV](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/xy_pressure.csv)、[读 XY／写 YX 压力 CSV](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/yx_pressure.csv)。

### 链路与服务预算

| 指标 | 全 XY | 读 XY／写 YX |
| --- | --- | --- |
| ROI 最忙内部有向链路 / 利用率 | `router:14->router:19` / 81.17% | `router:13->router:14` / 73.62% |
| ROI 东列纵向链路最高利用率 | 81.17% | 53.61% |
| 按全程流量比例的链路必要上界 GB/s | 398.2015 | 709.8018 |
| 合并链路、NI 与 HBM 约束的必要上界 GB/s | 398.2015 | 638.0062 |

写 YX 分散东列纵向汇聚，最忙链路转到进入东列的向东链路。对于读 XY／写 YX 的完整流量计划，HBM 共享读写服务预算成为比内部链路更紧的必要约束；有限 tile 分配下该上界为 638.0062 GB/s。必要上界与 ROI 实际流量比例分开使用，详细数据见 [XY 上界](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/xy_bounds.json)、[读 XY／写 YX 上界](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/yx_bounds.json)。

![MIXED 读 XY 写 YX 的 ROI 有向链路利用率](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/yx_links.svg)

同色标 [全 XY 热点图](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/xy_links.svg)；[XY 链路 CSV](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/xy_links.csv)、[读 XY／写 YX 链路 CSV](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/yx_links.csv)。

汇总、分方向时延与复现索引：[comparison.json](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/comparison.json)、[README](../../../.tmp/mixed-xy-yx-gzah3ty3/README.md)。每组的 ROI 探测及正式运行均正常退出并通过独立校验，证据见 [XY execution](../../../.tmp/mixed-xy-yx-gzah3ty3/xy/execution.json)、[读 XY／写 YX execution](../../../.tmp/mixed-xy-yx-gzah3ty3/yx/execution.json)、[冻结输入身份](../../../.tmp/mixed-xy-yx-gzah3ty3/provenance/identity.json)、[完成审计](../../../.tmp/mixed-xy-yx-gzah3ty3/analysis/completion_audit.json)。结论限于本节单 lane、N=32、16 MiB/core 的已测配置。

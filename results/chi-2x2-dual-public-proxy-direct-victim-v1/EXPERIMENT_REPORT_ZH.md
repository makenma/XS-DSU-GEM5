# 双核 2×2 CHI 共享 SLC 替换策略实验报告

> **Direct-PoCQ victim 模型；public-source proxy，not SPEC CPU2006。** 本实验使用 libquantum 0.2.4 与 OMNeT++ 3.3.2 的公开源码代理程序。报告中的 IPC、miss、victim、Replay 等结果都不能称为 SPEC CPU2006 分数。

## 1. 结论摘要

在同一个严格满载 checkpoint 后，各运行固定 **2,000,000,000 ticks**，新模型的 aggregate IPC 排名为：

| 排名 | 策略 | Aggregate IPC | 相对 Random |
|---:|---|---:|---:|
| 1 | Random | **2.041935** | 基准 |
| 2 | SRRIP | 2.034898 | -0.3446% |
| 3 | LRU | 2.030094 | -0.5799% |

三个策略的 simulated ticks 和每核 cycles 完全相同；Random 没有少跑拍数。Random 的宿主 wall-time 反而最长：562.2 秒，而 LRU/SRRIP 分别为 537.8/535.9 秒。因此，“Random 的 guest IPC 最高”不等于“Random 在宿主机上建模最快”。

本轮最重要的模型验证是：三策略的 `victimBufferFullReplays` 全部为 **0**。与旧的持久 SLC victim-buffer 模型相比，aggregate IPC 提升 26.95%–28.39%，Replay/KI 降低 94.82%–95.13%，service-stall/KI 降低 93.08%–93.82%。这说明旧结果中的大部分 HNF 排队确实来自不存在于 RTL 设计中的 SLC victim-buffer 阻塞。

新模型下 Random 仍获得最高 aggregate IPC，但领先幅度从旧模型相对 LRU/SRRIP 的 1.6927%/1.3396%，缩小到 0.5799%/0.3446%。当前窗口内，Random 的优势主要来自 CPU1 进展、较高 SLC hit rate、较低 Replay/KI 及较少 DRAM/NoC traffic 的组合；不能解释为“随机选 victim 本身只需要更少拍数”。

## 2. 本次修正的建模边界

本轮使用的实现与 RTL 语义一致：

- **SLC clean victim**：直接丢弃，不发 snoop。
- **SLC dirty/M-state victim**：在 U1 排他边界精确抓取地址、状态和 64 B 数据；U2 可以覆盖 SLC array；terminal response 将唯一持久所有权交给 PoCQ；PoCQ 向 SN/DDR 发 `WriteNoSnpFull`。SLC 侧没有持久 victim buffer，也没有 `ReleaseDirtyVictim` 往返。
- **SF victim**：仍进入独立 SEQ/PoCQ 状态机，并根据 sharer/owner 发 `SnpCleanInvalid`；这条路径没有被本次 SLC victim 修正旁路。

`victimBufferFullReplays` 仅作为旧统计字段保留，描述已经明确为 legacy 且 direct-PoCQ handoff 下恒为 0。SF victim 的正确性由 SEQ/PoCQ 单元测试覆盖；当前 `directedSnoops`/`broadcastSnoops` 是主 lookup 的统计，不是 SF-eviction SEQ snoop 计数，因此本报告不拿它们代替 SF victim snoop 数。

## 3. 负载与固定映射

| CPU | 代理负载 | 参数 | 地址空间 | RNF |
|---|---|---|---|---|
| CPU0 | libquantum 0.2.4 Shor | `1397 8` | 独立 SE 进程 | RNF0 / 0x00 |
| CPU1 | OMNeT++ 3.3.2 Token Ring | `-f .../omnetpp.ini` | 独立 SE 进程 | RNF1 / 0x04 |

两个进程在同一个 gem5 实例内并发运行，各自使用私有 L1/L2，并经 2×2 CHI mesh 竞争一个共享 HNF/SLC。CPU/workload/RNF 映射在 LRU、Random、SRRIP 间没有交换。

拓扑和主要参数保持与上次正式实验一致：

- 2 CPU、2 RNF、4-router 2×2 mesh、1 HNF、1 SN。
- 共享 SLC：1 MiB，1024 set × 16 way × 64 B，共 16,384 lines。
- CPU 2.3 GHz；系统/CHI/HNF/Router 1.8 GHz；1 GiB memory。
- 所有策略统一禁用预取器，避免已知 tick 0 初始化问题。
- Random seed 固定为 `20260730`；SRRIP 为确定性 2-bit SRRIP。

## 4. 公平起点和时间口径

三策略复用上次正式实验生成的同一个已 drain、严格满载 checkpoint：

- checkpoint tick：1,378,457,840。
- 恢复时每核累计指令：`[4,394,492, 3,175,617]`。
- SLC：16,384 / 16,384 lines；每 set 最大 16 valid ways。
- `m5.cpt` SHA-256：`65004b6a911bd5b10ced72c82c96c809d6a52997a403c8189e7ee3fba85bfc0d`。
- memory image SHA-256：`4ab74a09c280b240f1aa649c99b4c2d2725cbf42eb2971278df4e55de9111738`。

每个策略恢复后 reset stats，再运行 2B ticks。每核均执行 4,597,700 cycles，ROI 结束时两个 CPU 都保持 active。因此：

- **模拟时间**：三策略相同。
- **guest 性能**：用固定 cycles 内提交的指令和 IPC 比较。
- **host 建模耗时**：受事件数量、宿主调度与实现开销影响，只用于复现成本参考，不代表 guest 性能。

## 5. 每核与系统性能

| 策略 | CPU0 IPC | CPU1 IPC | Aggregate IPC | 总提交指令 | Host elapsed/s |
|---|---:|---:|---:|---:|---:|
| LRU | 1.606015 | 0.424078 | 2.030094 | 9,333,762 | 537.8 |
| Random | 1.594560 | **0.447375** | **2.041935** | **9,388,204** | 562.2 |
| SRRIP | **1.609660** | 0.425238 | 2.034898 | 9,355,850 | **535.9** |

Aggregate IPC 定义为两个 CPU committed instructions 之和除以共同 cycles。Random 并不是每个核都最快：CPU0 上 SRRIP/LRU 分别比 Random 高 0.9470%/0.7184%；Random 依靠 CPU1 比 LRU/SRRIP 高 5.2074%/4.9482%，最终取得最高系统吞吐。

## 6. 私有缓存与 CPU stall

| 策略/核 | L1 demand MPKI | L2 demand MPKI | Memory-stall cycles |
|---|---:|---:|---:|
| LRU / CPU0 | 10.4316 | 10.4182 | 4,424 |
| LRU / CPU1 | 37.7031 | 6.4479 | 3,817,314 |
| Random / CPU0 | 10.4325 | 10.4190 | 5,297 |
| Random / CPU1 | **36.5765** | **6.2536** | **3,791,349** |
| SRRIP / CPU0 | 10.4315 | 10.4180 | 3,612 |
| SRRIP / CPU1 | 37.6321 | 6.4559 | 3,819,705 |

CPU0 三策略的 L1/L2 MPKI 几乎相同；系统排名主要受 CPU1 影响。Random 在 CPU1 上同时具有最低 L1/L2 MPKI 和最低 memory-stall cycles，与其 CPU1 IPC 领先同向。

## 7. SLC 与 SF victim

| 策略 | Clean SLC | Dirty SLC | Total SLC | SF victim | Victims/KI | Victim/lookup |
|---|---:|---:|---:|---:|---:|---:|
| LRU | 8,767 | 65,324 | 74,091 | 88,228 | 7.9380 | 80.5022% |
| Random | 8,368 | 64,673 | **73,041** | **88,084** | **7.7801** | **80.0590%** |
| SRRIP | 8,869 | 65,493 | 74,362 | 88,482 | 7.9482 | 80.6451% |

三策略都满足：

```text
cleanSlcVictims + dirtySlcVictims
  = totalSlcVictims
  = replacementAttempts
```

三策略恢复时和峰值都达到 16,384/16,384 lines，并持续发生 replacement。新模型中 Random 的 raw victim 和 Victims/KI 都是三者最低，但这仍不能单独证明因果；victim 只是替换事件，是否阻塞取决于 dirty handoff、PoCQ/DDR 资源和后续请求关系。

## 8. Replay 与 backpressure

| 策略 | Stale | SEQ conflict | Victim-buffer full | Total Replay | Replay/KI | Req-full cycles | Resp-full cycles | No-credit | Service stalls | Stall/KI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LRU | 171 | 21,954 | **0** | 22,125 | 2.3704 | 33 | 0 | 0 | 247,898 | 26.5593 |
| Random | **101** | **19,545** | **0** | **19,646** | **2.0926** | 19 | 0 | 2 | 247,732 | 26.3876 |
| SRRIP | 151 | 20,574 | **0** | 20,725 | 2.2152 | **3** | 0 | 0 | **246,441** | **26.3408** |

`resourceConflictReplays`、`cancelledReplays` 和 `setLockConflicts` 在三轮中也均为 0。

这里需要区分三类量：

1. `Total Replay` 是已经形成终态 Replay response 的请求数。
2. `No-credit`/request-full/response-full 描述队列容量背压。
3. `Service stalls` 是已发工作在语义 mutation 前停顿的次数，范围比队列满更广。

Random 的 Replay/KI 最低，但 SRRIP 的 service-stall/KI 略低；所以不能把 IPC 排名只归结为一个计数器。三策略的 service-stall/KI 已经非常接近，Random 最终领先更像是 cache 命中、CPU1 相位、Replay 和 traffic 的综合结果。

## 9. SLC/SF 命中、可见延迟、DDR 与 NoC

| 策略 | SLC hit rate | SF hit rate | Accepted→visible/cycle | DRAM reads | DRAM writes | NoC traffic score |
|---|---:|---:|---:|---:|---:|---:|
| LRU | 1.6211% | 1.4494% | **6.9535** | 88,176 | 64,825 | 7,078,185 |
| Random | **2.5659%** | 1.3241% | 6.9852 | **87,090** | **64,159** | **7,028,869** |
| SRRIP | 1.6419% | 1.4066% | 6.9662 | 88,378 | 64,993 | 7,096,985 |

Random 的 accepted-to-visible 平均延迟不是最低，但它有明显更高的 SLC hit rate，以及最低的 DRAM request 和 NoC traffic。这再次说明“单个 victim 处理得快”不是充分解释；策略改变的是后续访问组合和两个 workload 在固定时间窗口内的进展。

## 10. 旧 victim-buffer 模型与 direct-PoCQ 模型 A/B

实验复用了完全相同的 checkpoint、workload、拓扑、seed 和 2B-tick ROI，因此可以直接观察模型修正的影响。

### 10.1 性能与 victim 归一化

| 策略 | 旧 IPC | 新 IPC | IPC Δ | 旧 Victims/KI | 新 Victims/KI | Victims/KI Δ |
|---|---:|---:|---:|---:|---:|---:|
| LRU | 1.581214 | 2.030094 | **+28.3883%** | 7.9677 | 7.9380 | -0.3737% |
| Random | 1.608441 | 2.041935 | **+26.9512%** | 7.9965 | 7.7801 | -2.7062% |
| SRRIP | 1.586895 | 2.034898 | **+28.2314%** | 7.9758 | 7.9482 | -0.3463% |

新模型的 raw victim 数提高 23.5%–27.9%，但固定时间内提交的指令也提高 27.0%–28.4%；按 KI 归一化后 victim rate 反而小幅下降。raw victim 增多是吞吐增加后的更多替换机会，不是新模型产生了更多 stall。

### 10.2 Replay、排队与延迟

| 策略 | Victim-full Replay 旧→新 | Replay/KI 旧→新 | Stall/KI 旧→新 | Latency/cycle 旧→新 |
|---|---:|---:|---:|---:|
| LRU | 52,409 → **0** | 45.7818 → 2.3704 (-94.82%) | 427.699 → 26.559 (-93.79%) | 10.2128 → 6.9535 (-31.91%) |
| Random | 46,053 → **0** | 41.6862 → 2.0926 (-94.98%) | 381.086 → 26.388 (-93.08%) | 9.9678 → 6.9852 (-29.92%) |
| SRRIP | 51,793 → **0** | 45.4561 → 2.2152 (-95.13%) | 426.243 → 26.341 (-93.82%) | 10.2189 → 6.9662 (-31.83%) |

request-full cycles 也从 LRU/Random/SRRIP 的 26,331/22,882/27,147，降到 33/19/3；no-credit 从 25,713/22,271/25,143，降到 0/2/0。旧模型不仅产生了虚假的 victim-buffer-full Replay，还通过长期占用和重试放大了 SEQ conflict 与服务排队。

## 11. 对“Random 为什么 IPC 更高”的回答

本次结果可以把此前的疑问拆成四句：

1. **三种策略没有跑不同的模拟时间。** 都是 2B ticks、每核 4,597,700 cycles。
2. **Random 不是宿主建模最快。** 它的 host elapsed 最长，但 guest aggregate IPC 最高。
3. **victim 总数不是 stall 总数。** 新 LRU 相比旧 LRU raw victim 更多，但 Replay/KI 和 stall/KI 分别减少约 95%/94%，IPC 提升 28.4%。
4. **Random 的当前优势不是“随机踢出的 cache 更方便处理”。** 所有 dirty SLC victim 都走相同 direct-PoCQ 写回路径。Random 只是在这个 workload/checkpoint/ROI 中形成了更有利的访问序列：CPU1 miss/stall 更少、SLC hit rate 更高、Replay/KI 与 DDR/NoC traffic 更低。

LRU/SRRIP 利用局部性的理论优势并不保证在所有共享缓存混合负载中取胜；两个独立程序对 set 的竞争、插入/命中序列以及固定时间窗口内的相位都会改变结果。当前差距只有 0.35%–0.58%，一次运行不足以宣称 Random 普遍优于 LRU/SRRIP。

## 12. 验证矩阵

- 三个正式运行返回码均为 0；LRU/Random/SRRIP host elapsed 为 537.8/562.2/535.9 秒。
- 每轮 2B ticks；每核 4,597,700 cycles；ROI 结束时两个 CPU active。
- 三策略恢复自同一 checkpoint，且峰值 SLC 为 16,384/16,384。
- replacement/victim 守恒、victim-by-RNF、victim-by-set、victim-by-policy 守恒全部通过。
- 三策略 `victimBufferFullReplays == 0`。
- 10 个 CHI/HNF/SLC 测试二进制，共 284 tests，0 failed。
- 当前 `gem5.opt` SHA-256：`fc44f8301caeba55cc56012892941e6331dc153aa3d074df2da840af93b08e2a`。
- config SHA-256：`187b71b560cf08fcdb65dc700d912e883976d9cedfb8df740e78b9dd1a0f1b4c`。
- runner SHA-256：`61719ea1dbe164cbdc06dfbe318380b73c21b7448d0700dcee30e7be9612f48c`。

正式运行发生在 dirty worktree：基准 commit 为 `e247a8dffd0c8e2693afe5985c40bb1d83712513`，tracked diff SHA-256 为 `76552d65060e48fdbe97caecb15fb5cb7f48807094f23da77cc0e918aff97be6`。因此在这些改动提交前，**不能只凭 commit ID 复现本轮二进制**；应同时使用 manifest 中的 binary/source hashes。

## 13. 结果文件

- `analysis/summary.json`：三策略完整结构化指标。
- `analysis/per_core_metrics.csv`：每核 IPC、MPKI、stall。
- `analysis/policy_comparison.csv`：相对 Random 的策略差异。
- `analysis/hnf_victims.csv`：SLC/SF victim 与 snoop lookup 统计。
- `analysis/hnf_pressure_replay.csv`：Replay、backpressure 与归一化指标。
- `analysis/model_change_comparison.csv`：旧模型与 direct-PoCQ 模型逐指标 A/B。
- `analysis/model_change_summary.json`：A/B 比较的机器可读版本。
- `runs/{lru,random,srrip}/stats.txt`：三轮原始统计。
- `validation/cpp_unit_tests.json`：10 个测试程序、284 tests 的结果。
- `manifest.json`：命令、输入、checkpoint、二进制和源码状态哈希。

## 14. 复现命令

复用相同满载 checkpoint，执行三策略正式运行：

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py run \
  results/chi-2x2-dual-public-proxy-direct-victim-v1-reproduced \
  --memory 1GB \
  --seed 20260730 \
  --roi-ticks 2000000000 \
  --min-roi-insts-per-core 100000 \
  --skip-warmup \
  --checkpoint-source \
  results/chi-2x2-dual-public-proxy-formal-v2/warmup/common_full_cpt
```

只重新提取统计：

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py analyze \
  results/chi-2x2-dual-public-proxy-direct-victim-v1
```

## 15. 解释限制

- 两个 workload 是公开源码代理，不是 SPEC CPU2006；所有结论仅适用于当前代理组合。
- 每种策略只有一次正式 2B-tick 性能运行，没有置信区间。
- 固定 cycle 窗口保证时间公平，但策略改变 CPU1 的程序进展，结果可能对 workload phase 敏感。
- 旧→新 A/B 显示的是完整 direct-victim 实现状态与旧模型的差异；它强烈支持“旧 victim buffer 是主要人为瓶颈”，但不是把每个源代码改动逐项隔离的消融实验。
- host wall-time 受宿主调度影响，只能用于估算仿真成本，不能替代 simulated ticks、cycles 或 IPC。

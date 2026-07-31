# 双核 2×2 CHI 共享 SLC 替换策略实验报告

> **边界声明：public-source proxy; not SPEC CPU2006。** 本实验使用 libquantum 0.2.4 与 OMNeT++ 3.3.2 的公开源码代理程序，不使用 SPEC CPU2006 二进制、reference input、checkpoint 或 restorer。报告中的 IPC、miss、victim 等结果不能称为 SPEC CPU2006 分数。

## 1. 实验问题与结论

实验在一个真实的双核并发 multiprogram 场景中比较共享 HNF/SLC 的 true LRU、固定种子 Random 与确定性 2-bit SRRIP。两个进程在同一 gem5 实例中同时运行、具有独立地址空间和私有 L1/L2，并通过各自 RNF 竞争同一共享 SLC。

本次固定 ROI 中，Random 获得最高 aggregate IPC（1.608441）。LRU 和 SRRIP 分别降低 1.6927% 和 1.3396%；两者虽然减少了 victim 和 NoC traffic，却同时增加约 10.3% 的 HNF service stall。最稳妥的解释是：在当前窗口中，victim 数不是性能的单一决定因素，服务排队、命中组合和每核进展共同影响最终 IPC。该排序不是普适结论。

## 2. 负载、进程与固定映射

| CPU | 地址空间/进程 | 代理程序 | 参数 | RNF 与接入点 |
|---|---|---|---|---|
| CPU0 | PID 100，单线程 | libquantum 0.2.4 Shor 代理 | `1397 8` | RNF 0x00 → router(0,0)/P0D0 |
| CPU1 | PID 101，单线程 | OMNeT++ 3.3.2 Token Ring 代理 | `-f .../omnetpp.ini` | RNF 0x04 → router(0,0)/P1D0 |

CPU0 只运行 libquantum 代理，CPU1 只运行 OMNeT++ 代理；三策略之间从未交换映射。它们是两个独立进程，不是一个程序跨两核并行，也不是分别运行后拼接统计。

输入 ELF 均为 RV64 Linux 静态链接文件。路径、参数与 SHA-256 见 `dual-core-se-dependencies.json`；正式清单保留了依赖清单的 SHA-256。

## 3. 2×2 CHI 拓扑

```text
CPU0/private L1/L2 ─ RNF0 0x00 ┐
                                ├─ router(0,0) ─ router(1,0) ─ SN 0x80 ─ Memory
CPU1/private L1/L2 ─ RNF1 0x04 ┘       │              │
                                      │              │
                                router(0,1) ─ router(1,1) ─ HNF 0x90 / shared SLC
```

准确映射如下：

- router 0 = (0,0)：RNF0/P0D0、RNF1/P1D0。
- router 1 = (1,0)：SN 0x80/P0D0，连接内存。
- router 2 = (0,1)：纯中转。
- router 3 = (1,1)：共享 HNF 0x90/P0D0。
- HNF/SLC：1 MiB = 1024 set × 16 way × 64 B = 16,384 lines。
- CPU 时钟 2.3 GHz；系统/CHI/HNF/Router 时钟 1.8 GHz；单通道 DDR4-2400_8x8；1 GiB 地址空间。
- 为规避该 SE 拓扑已确认的 tick 0 组合预取器初始化问题，所有策略统一禁用预取器；这不会在策略之间引入不公平差异。

## 4. 替换策略语义

### 4.1 True LRU

命中更新 recency；满组分配时选择最久未使用的有效 line。无效 way 总是优先于替换有效 line。

### 4.2 固定种子 Random

仅当目标 set 已满时，在有效 ways 中均匀随机选择 victim。随机状态以 seed `20260730` 初始化。三次额外的 50M-tick 固定 checkpoint 短运行表明：排除 host wall-time/rate 四个字段后，10,035 个有限数值仿真统计全部逐值一致。

### 4.3 确定性 2-bit SRRIP

本实现明确是 **SRRIP**，不是 BRRIP 或 DRRIP：RRPV 最大值为 3；命中置 0；新 line 插入为 2；优先选择 RRPV=3；若没有候选，则对该 set 内所有 line 的 RRPV 饱和递增，直到出现候选。无效 way 优先。

## 5. 公平边界：Fill/Warm-up 与 Replacement ROI

### 5.1 Phase A：Fill/Warm-up

两个代理进程从冷状态同时运行。SLC 第一次达到精确满载的 tick 为 1,264,513,580，此时每核累计指令为 `[4,132,939, 3,146,279]`。随后执行 settle 与全局 drain；第一次 drain 使占用暂降至 16,383 lines，配置恢复运行直至再次精确满载，并在第二次 drain 后得到严格 checkpoint：

- checkpoint tick：1,378,457,840。
- 每核累计指令：`[4,394,492, 3,175,617]`。
- `slcValidLines = slcCapacityLines = 16,384`。
- `maxValidWaysPerSet = 16`。
- `m5.cpt` SHA-256：`65004b6a911bd5b10ced72c82c96c809d6a52997a403c8189e7ee3fba85bfc0d`。
- `system.physmem.store0.pmem` SHA-256：`4ab74a09c280b240f1aa649c99b4c2d2725cbf42eb2971278df4e55de9111738`。

这一定义直接读取 SLC 有效行状态，不用 Fill 次数推算满载。

### 5.2 Phase B：Replacement ROI

三策略均恢复同一个 checkpoint，仅允许替换策略状态按目标策略重新初始化；workload、内存、私有缓存、SLC/SF 内容、拓扑和进程进展均相同。恢复后验证 SLC 为 16,384/16,384 且两个 CPU active，再 reset stats。reset 只清统计，不清缓存状态。

三次 ROI 都运行 2,000,000,000 ticks，对应每核 4,597,700 cycles；没有因某策略快慢而改变窗口。结束时两个 CPU active，且每核测量指令均大于 100,000。

## 6. 每核与系统性能

| 策略 | CPU0/libquantum proxy IPC | CPU1/OMNeT++ proxy IPC | Aggregate IPC | CPU0 指令 | CPU1 指令 |
|---|---:|---:|---:|---:|---:|
| LRU | 1.376740 | 0.204474 | 1.581214 | 6,329,837 | 940,112 |
| Random | **1.384636** | **0.223805** | **1.608441** | 6,366,143 | 1,028,986 |
| SRRIP | 1.381455 | 0.205440 | 1.586895 | 6,351,515 | 944,552 |

Aggregate IPC 定义为两核 committed instructions 之和除以共同测量 cycles；由于两核周期相同，它也等于 CPU0 IPC + CPU1 IPC。

相对 Random 的绝对值与百分比：

| 策略 | CPU0 IPC Δ | CPU0 Δ% | CPU1 IPC Δ | CPU1 Δ% | Aggregate IPC Δ | Aggregate Δ% |
|---|---:|---:|---:|---:|---:|---:|
| LRU | -0.007897 | -0.5703% | -0.019330 | -8.6370% | -0.027227 | -1.6927% |
| SRRIP | -0.003182 | -0.2298% | -0.018364 | -8.2056% | -0.021546 | -1.3396% |

CPU0 的策略间测量指令最大相对 spread 为 0.5703%，CPU1 为 8.6370%。这来自等长 cycle 窗口内的实际性能差异；若 workload 非平稳，CPU1 的进展差异也会放大相位敏感性，因此不作强普适因果结论。

## 7. 私有缓存 miss 与 stall

| 策略/核 | L1 demand MPKI | L2 demand MPKI | Memory requests/ki | Stalled cycles | Memory-stall cycles |
|---|---:|---:|---:|---:|---:|
| LRU / CPU0 | 10.4322 | 10.4194 | 10.4194 | 4,480,821 | 5,701 |
| LRU / CPU1 | 45.6254 | 8.5383 | 8.5383 | 4,586,082 | 4,057,445 |
| Random / CPU0 | 10.4291 | 10.4182 | 10.4182 | 4,477,956 | 6,290 |
| Random / CPU1 | 46.9278 | 8.6221 | 8.6221 | 4,585,283 | 4,039,678 |
| SRRIP / CPU0 | 10.4289 | 10.4185 | 10.4185 | 4,479,906 | 5,812 |
| SRRIP / CPU1 | 45.6852 | 8.5448 | 8.5448 | 4,586,054 | 4,061,465 |

这里的 memory request 定义为每核私有 L2 demand miss 之和；定义已写入 CSV，避免和 HNF/DRAM transaction 混用。

## 8. SLC 占用与 victim 守恒

| 策略 | ROI 末有效行 | 峰值/容量 | 峰值占用 | Full SLC cycles | Clean | Dirty | Total/Attempts |
|---|---:|---:|---:|---:|---:|---:|---:|
| LRU | 16,382 | 16,384/16,384 | 100% | 2,859,722 | 6,086 | 51,839 | 57,925 |
| Random | 16,384 | 16,384/16,384 | 100% | 2,813,872 | 6,451 | 52,684 | 59,135 |
| SRRIP | 16,384 | 16,384/16,384 | 100% | 2,866,217 | 5,960 | 52,232 | 58,192 |

所有策略满足：`cleanSlcVictims + dirtySlcVictims = totalSlcVictims = replacementAttempts`。LRU 末态少 2 line 是 ROI 内一致性失效造成的瞬时占用，不否定起点满载、峰值 100%、持续 full cycles 与真实 replacement。正式有效性判定使用峰值和 victim 活性，而不是强制末态仍恰好满载。

相对 Random：

- LRU：total victim -1,210（-2.0462%）；clean -365（-5.6580%）；dirty -845（-1.6039%）。
- SRRIP：total victim -943（-1.5947%）；clean -491（-7.6112%）；dirty -452（-0.8580%）。

## 9. Victim 归属

| 策略 | RNF0/CPU0 | RNF1/CPU1 | 合计 |
|---|---:|---:|---:|
| LRU | 51,007 | 6,918 | 57,925 |
| Random | 51,669 | 7,466 | 59,135 |
| SRRIP | 51,286 | 6,906 | 58,192 |

归属和总数严格守恒。RNF0 占大多数 victim，符合 CPU0 较高的 L2 miss 绝对数量，但不能把 requester attribution 解释为 victim line 的原始所有者。

## 10. SLC/SF、HNF 与内存

| 策略 | SLC hit rate | SF hit rate | Accepted→visible latency/cycle | HNF service stalls | DRAM reads | DRAM writes |
|---|---:|---:|---:|---:|---:|---:|
| LRU | 1.3760% | 2.3246% | 10.2128 | 3,109,349 | 73,082 | 51,331 |
| Random | 1.3552% | 1.9878% | **9.9678** | **2,818,183** | 74,044 | 52,163 |
| SRRIP | 1.2900% | 2.4325% | 10.2189 | 3,109,896 | 73,296 | 51,684 |

相对 Random，LRU/SRRIP 的 accepted-to-visible latency 分别增加 0.2450 cycle（+2.4579%）和 0.2511 cycle（+2.5195%）；service stall 分别增加 291,166（+10.3317%）和 291,713（+10.3511%）。这与两者较低 IPC 同向，是比单看 victim 数更有解释力的信号，但仍不等价于孤立的因果证明。

## 11. NoC traffic 与热点

| 策略 | NoC traffic score | 相对 Random | Router stall cycles | 最热 Router |
|---|---:|---:|---:|---|
| LRU | 6,354,046 | -30,848（-0.4831%） | 956 | router 3 / (1,1) / HNF |
| Random | 6,384,894 | 基准 | 1,018 | router 3 / (1,1) / HNF |
| SRRIP | 6,372,952 | -11,942（-0.1870%） | 973 | router 3 / (1,1) / HNF |

三策略的热点排名一致：router 3（HNF）最热，其次 router 1（SN），再到 router 0（双 RNF），router 2 最低。router 3 的 traffic score 为 2,488,417 / 2,496,222 / 2,496,588（LRU/Random/SRRIP）。热点位置由共享 HNF 汇聚流量的拓扑决定，策略主要改变幅度。

## 12. 实现与 checkpoint 正确性修复

为使 SE 双进程与满载 SLC checkpoint 真正可恢复，本实验补齐了两类边界：

1. O3 LSQ drain 会等待已提交、仍停留在共享 store buffer 的写入完成；否则 checkpoint 可能遗漏 guest memory 状态。
2. SE `MemState` checkpoint 序列化历史 heap 映射上界 `_endBrkPoint`；否则 restore 后 `brk` 扩展可能与已恢复 VMA 冲突，并在 OMNeT++ 代理的 glibc `sysmalloc` 路径出现 guest page fault。

修复后的 gem5 还能向后兼容旧 checkpoint：旧格式缺少字段时，从当前 brk 与 heap VMA 恢复安全上界。正式 checkpoint 由修复后的二进制生成并由三策略共同使用。

## 13. 验证矩阵

- RISCV `gem5.opt` 构建：通过；SHA-256 为 `8ed7c46276d0b08affb375a52edf9f14fa050af203c38cec5f9c994e42bc066c`。
- CHI/HNF/SLC/SF C++：9 个测试二进制、268 tests、0 failed。
- 替换策略：true LRU、固定 Random、SRRIP 的无效 way、命中、插入、老化、victim 选择测试均在上述测试集中通过。
- SLC occupancy/peak 与 victim attribution：单元测试和正式统计守恒均通过。
- 2×2/2 RNF/1 HNF、双核双进程：三次正式日志与 `phase_metadata.json` 验证通过。
- 三策略 smoke/formal：通过；正式每项 2B ticks。
- Random 三重复：通过；详见 `validation/random_reproducibility.json`。
- Manifest SHA-256：gem5、config、runner、依赖清单与 checkpoint 两文件均复核通过。
- JSON/CSV schema、`git diff --check`、PPT reopen/render：通过；机器可读状态见 `validation/validation_summary.json`。

单元测试中的 panic/fatal 文本来自 `EXPECT_DEATH` 负向测试，测试进程返回码均为 0，不是失败。

## 14. 复现命令

依赖路径必须与 `dual-core-se-dependencies.json` 一致。在 `/home/makenma/project/xs-gem5/GEM5` 执行：

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py run \
  results/chi-2x2-dual-public-proxy-formal-v2-reproduced \
  --memory 1GB \
  --seed 20260730 \
  --fill-max-ticks 5000000000 \
  --settle-ticks 100000 \
  --checkpoint-full-retry-limit 8 \
  --checkpoint-refill-max-ticks 1000000000 \
  --roi-ticks 2000000000 \
  --min-roi-insts-per-core 100000
```

runner 先生成公共满载 checkpoint，再依次运行 LRU、Random、SRRIP，最后执行结构化分析。若只需重算分析：

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py analyze \
  results/chi-2x2-dual-public-proxy-formal-v2
```

## 15. 解释限制

- 这是公开源码代理负载，不是 SPEC CPU2006，不能报告 SPEC 分数或与正式 suite 加权结果对比。
- 每种策略只有一次 2B-tick 正式性能运行；三次 Random 短重复只证明确定性，不提供正式性能置信区间。
- 固定 cycle 窗口保证时间公平，但不同策略提交指令数不同，CPU1 进展 spread 达 8.637%；结论限定于该 checkpoint 后的当前 ROI。
- 当前配置统一禁用预取器；若修复 tick 0 初始化后重新启用，需要从头生成 checkpoint 和三策略结果。
- “Random 最好”只描述本拓扑、本代理组合和本窗口；不能据此声称 Random 普遍优于 LRU/SRRIP。


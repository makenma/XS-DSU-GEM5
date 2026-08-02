# 2×2 CHI 双核 SLC 替换策略实验报告

## 结论摘要

本实验使用同一个严格满载 checkpoint，在 direct-PoCQ dirty-victim 模型下分别运行 LRU、Random、SRRIP；三组正式 ROI 均为 **2,000,000,000 simulated ticks**，seed 均为 **20260730**，结束时两个 CPU 均 active。全部验证项通过，包括 SLC victim 守恒、`victimBufferFullReplays == 0` 与 284/284 项 CHI/HNF/SLC 单测。

这里的 workload 是公开源码代理 workload，不是 SPEC CPU2006 合规跑分。所有策略比较都只对本固定 checkpoint 与固定 ROI 成立。

## 固定配置与边界

- CPU0：libquantum 0.2.4 Shor，参数 `1397 8`。
- CPU1：OMNeT++ 3.3.2 Token Ring，参数 `-f omnetpp.ini`。
- 2×2 CHI：2 RNF、1 HNF、1 SN；shared SLC 为 1 MiB、1024 sets、16 ways、64 B line。
- 只改变 shared SLC 替换策略；三组 L1/L2 配置抽取结果 SHA-256 完全一致。
- transaction 与 profile recorder 都在精确的 `roi_start` 启动，在精确的 `roi_end` 停止；不统计 restore、warm-up 或 drain。

## 性能、容量与压力指标

| 策略 | CPU0 IPC | CPU1 IPC | CPU0 L2 MPKI | CPU1 L2 MPKI | SLC victims | Replay | HNF service stall | No-credit | host 秒 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LRU | 1.60602 | 0.42408 | 10.418 | 6.448 | 74091 | 22125 | 247898 | 0 | 558.48 |
| Random | 1.59456 | 0.44737 | 10.419 | 6.254 | 73041 | 19646 | 247732 | 2 | 553.93 |
| SRRIP | 1.60966 | 0.42524 | 10.418 | 6.456 | 74362 | 20725 | 246441 | 0 | 521.97 |

`cycles` 是 guest CPU 的 simulated cycles；IPC 是 guest committed instructions / guest cycles。`host 秒` 只表示运行 gem5 的宿主机 wall-clock 时间，不能与 guest simulated time 混用。DRAM 与 NoC 的完整计数、每核 L1/L2 MPKI、memory-stall、victim、Replay 与 HNF 压力指标见 `analysis/summary.json` 和 `analysis/policy_comparison.csv`。

| 策略 | CPU0 memory-stall cyc | CPU1 memory-stall cyc | DRAM reads | DRAM writes | DRAM read bursts | DRAM write bursts | NoC traffic | NoC stall cyc |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LRU | 4424 | 3817314 | 88176 | 64825 | 88176 | 64825 | 7078185 | 1740 |
| Random | 5297 | 3791349 | 87090 | 64159 | 87090 | 64159 | 7028869 | 1789 |
| SRRIP | 3612 | 3819705 | 88378 | 64993 | 88378 | 64993 | 7096985 | 1687 |

## RNF transaction latency

定义如下：REQ latency 从 RNF 成功注入 REQ 开始，到相同 `{SrcID, TxnID}` 对应的 terminal RSP/DAT 完成并 retire；SNP latency 从 RNF 接受 TXSNP 开始，到相同 wire transaction ID 的最终 SnpResp/SnpRespData 注入。所有匹配都在 RNF bridge transaction table 内完成，不使用时间邻近或 opcode 猜测。ROI 末仍 outstanding 的 transaction 记为 right-censored，不进入 mean/P50/P95。

每个 type 的 mean 是该 type 全部 completed instances 的算术平均；每个 RNF 的 weighted mean 定义为 `sum(count_type × mean_type) / sum(count_type)`。P50/P95 采用 nearest-rank。CHI cycle 用 raw trace 内的 clock period 换算；ns 用 `simFreq` 换算。

若 `SNP:SnpCleanInvalid → RSP:SnpResp` 显示 0 cycle，含义是当前 bridge 在接受 snoop 的同一 simulated tick 就成功注入了无数据响应；这是模型允许的同步快路径，不是漏采样。带数据或受 backpressure 的 snoop 会按实际结束 tick 计时。

| 策略 | RNF | accepted | completed | censored | weighted cyc | ticks | ns | overall P50 cyc | overall P95 cyc | weighted vs Random |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LRU | RNF0/CPU0 | 153672 | 153665 | 7 | 195.485 | 108689.9 | 108.690 | 224.000 | 555.000 | -0.647% |
| LRU | RNF1/CPU1 | 24606 | 24605 | 1 | 189.281 | 105240.2 | 105.240 | 84.000 | 556.000 | +2.415% |
| Random | RNF0/CPU0 | 152592 | 152576 | 16 | 196.758 | 109397.3 | 109.397 | 224.000 | 563.000 | +0.000% |
| Random | RNF1/CPU1 | 25143 | 25141 | 2 | 184.817 | 102758.3 | 102.758 | 49.000 | 558.000 | +0.000% |
| SRRIP | RNF0/CPU0 | 154091 | 154082 | 9 | 194.933 | 108382.8 | 108.383 | 224.000 | 552.000 | -0.927% |
| SRRIP | RNF1/CPU1 | 24641 | 24641 | 0 | 188.495 | 104803.2 | 104.803 | 85.000 | 550.000 | +1.990% |

### 按正式 CHI transaction type

| 策略 | RNF | CHI type | terminal response type | accepted | completed | censored | mean cyc | P50 cyc | P95 cyc | mean vs Random |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LRU | RNF0 | REQ:ReadShared | DAT:CompData | 20 | 20 | 0 | 249.300 | 258.000 | 489.000 | -4.884% |
| LRU | RNF0 | REQ:ReadUnique | DAT:CompData | 76908 | 76901 | 7 | 388.362 | 351.000 | 635.000 | -0.728% |
| LRU | RNF0 | REQ:WriteBackFull | RSP:CompDBIDResp | 467 | 467 | 0 | 164.248 | 140.000 | 353.000 | -4.135% |
| LRU | RNF0 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 76277 | 76277 | 0 | 1.208 | 1.261 | 2.268 | -0.049% |
| LRU | RNF1 | REQ:ReadShared | DAT:CompData | 11193 | 11192 | 1 | 360.868 | 330.000 | 629.000 | +2.964% |
| LRU | RNF1 | REQ:ReadUnique | DAT:CompData | 1469 | 1469 | 0 | 419.809 | 400.000 | 713.000 | -0.253% |
| LRU | RNF1 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 11944 | 11944 | 0 | 0.144 | 0.000 | 1.344 | -4.036% |
| Random | RNF0 | REQ:ReadShared | DAT:CompData | 20 | 20 | 0 | 262.100 | 146.000 | 853.000 | +0.000% |
| Random | RNF0 | REQ:ReadUnique | DAT:CompData | 76365 | 76350 | 15 | 391.210 | 351.000 | 637.000 | +0.000% |
| Random | RNF0 | REQ:WriteBackFull | RSP:CompDBIDResp | 319 | 319 | 0 | 171.332 | 135.000 | 406.000 | +0.000% |
| Random | RNF0 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 75888 | 75887 | 1 | 1.209 | 1.264 | 2.268 | +0.000% |
| Random | RNF1 | REQ:ReadShared | DAT:CompData | 11441 | 11439 | 2 | 350.479 | 323.000 | 633.000 | +0.000% |
| Random | RNF1 | REQ:ReadUnique | DAT:CompData | 1510 | 1510 | 0 | 420.874 | 400.000 | 748.000 | +0.000% |
| Random | RNF1 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 12192 | 12192 | 0 | 0.151 | 0.000 | 1.372 | +0.000% |
| SRRIP | RNF0 | REQ:ReadShared | DAT:CompData | 20 | 20 | 0 | 112.850 | 77.000 | 256.000 | -56.944% |
| SRRIP | RNF0 | REQ:ReadUnique | DAT:CompData | 77081 | 77072 | 9 | 387.515 | 350.000 | 632.000 | -0.944% |
| SRRIP | RNF0 | REQ:WriteBackFull | RSP:CompDBIDResp | 440 | 440 | 0 | 167.761 | 140.000 | 379.000 | -2.084% |
| SRRIP | RNF0 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 76550 | 76550 | 0 | 1.215 | 1.268 | 2.268 | +0.528% |
| SRRIP | RNF1 | REQ:ReadShared | DAT:CompData | 11235 | 11235 | 0 | 358.356 | 329.000 | 620.000 | +2.247% |
| SRRIP | RNF1 | REQ:ReadUnique | DAT:CompData | 1476 | 1476 | 0 | 417.880 | 397.000 | 727.000 | -0.711% |
| SRRIP | RNF1 | SNP:SnpCleanInvalid | DAT:SnpRespData;RSP:SnpResp | 11930 | 11930 | 0 | 0.150 | 0.000 | 1.369 | -0.665% |

图：`analysis/rnf0_transaction_latency.png/.svg` 与 `analysis/rnf1_transaction_latency.png/.svg`。完整逐 type 机器可读结果在 `analysis/rnf_transaction_latency.csv` 和 `.json`；原始逐 transaction 记录在各 `runs/<policy>/rnf*_transaction_latency_raw.csv`。

## Guest workload flame graph

这些图来自 guest O3 Commit probe 的 retired macro-instruction 采样，并按 CPU 对应的 guest ELF 离线符号化；它们不是宿主机 gem5 的 perf/call stack。采样周期为每 1000 条被 probe 观察到的 retired guest macro-instructions 一次。共享 checkpoint 在 profiler attachment 之前生成，因此 ROI 起点以前的祖先栈无法恢复：图中显式加入 `[ROI root: pre-ROI ancestry unavailable]`，只把 ROI 内观察到的 call/return 维护为 shadow call stack。CPU1 在 ROI 内观察到大量 call/return，因此标为 **guest ROI-local shadow-call-stack flame graph**；CPU0 在三策略 ROI 内都没有观察到 call、shadow depth 也未超过 1，所以其同名 SVG 明确标为 **guest function/PC profile in flame layout（不是 call-stack flame graph）**。两者都不是完整 DWARF unwind。

两个现有 static ELF 都带 `.symtab` 与 `.eh_frame`，但不带 `.debug_info/.debug_line`；因此本次没有另编译 `-g -fno-omit-frame-pointer` 版本，也没有改变正式 workload。`addr2line -f -C` 能可靠给出函数名，源码行可能显示 `??:0`。未解析比例在下表和每份 profile metadata 中按“函数名无法解析”统计。

| 策略 | workload | profile 分类 | 样本数 | Commit 覆盖率 | 未解析 leaf | Top leaf functions | Top inclusive functions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LRU | CPU0/libquantum | PC/function (flame layout) | 7384 | 100.0000% | 0.000% | `quantum_gate1` (100.0%) | `quantum_gate1` (100.0%) |
| Random | CPU0/libquantum | PC/function (flame layout) | 7332 | 100.0000% | 0.000% | `quantum_gate1` (100.0%) | `quantum_gate1` (100.0%) |
| SRRIP | CPU0/libquantum | PC/function (flame layout) | 7401 | 100.0000% | 0.000% | `quantum_gate1` (100.0%) | `quantum_gate1` (100.0%) |
| LRU | CPU1/omnetpp | call-stack | 1924 | 98.6501% | 0.000% | `read_encoded_value_with_base` (44.6%), `fde_single_encoding_compare` (15.1%), `add_fdes` (11.0%), `classify_object_over_fdes` (9.9%), `search_object` (4.7%) | `____strtoll_l_internal` (100.0%), `cModule::buildInside()` (96.4%), `Computer::doBuildInside()` (96.0%), `cGate::connectTo(cGate*, cChannel*)` (91.0%), `__cxa_throw` (90.3%) |
| Random | CPU1/omnetpp | call-stack | 2030 | 98.6597% | 0.000% | `read_encoded_value_with_base` (45.3%), `fde_single_encoding_compare` (16.4%), `add_fdes` (10.5%), `classify_object_over_fdes` (9.3%), `search_object` (4.7%) | `____strtoll_l_internal` (100.0%), `cModule::buildInside()` (96.6%), `Computer::doBuildInside()` (96.3%), `cGate::connectTo(cGate*, cChannel*)` (91.5%), `__cxa_throw` (90.8%) |
| SRRIP | CPU1/omnetpp | call-stack | 1929 | 98.6526% | 0.000% | `read_encoded_value_with_base` (45.8%), `fde_single_encoding_compare` (13.9%), `add_fdes` (10.4%), `classify_object_over_fdes` (10.0%), `search_object` (5.3%) | `____strtoll_l_internal` (100.0%), `cModule::buildInside()` (96.4%), `Computer::doBuildInside()` (96.1%), `cGate::connectTo(cGate*, cChannel*)` (91.0%), `__cxa_throw` (90.4%) |

每个 profile 的 raw CSV、metadata、folded stack、symbol map、SVG 与 PNG 都在 `analysis/flamegraphs/`。`commit_probe_coverage_fraction` 用 profiler 观察计数除以 gem5 `committedInsts`；两者因 Commit probe 对部分 SE 路径的可见性可能不完全一致，因此同时保留原始分母和分子。

CPU0 三策略的 sampled leaf 都是 `quantum_gate1`（100%），说明该固定 ROI 位于 Shor 量子门核心长循环；由于 ROI 内无 call，不能补造其 checkpoint 之前的 caller。

CPU1 inclusive 栈包含 `Computer::doBuildInside()`、`_Unwind_RaiseException`、`__cxa_throw`、`cGate::connectTo(cGate*, cChannel*)`、`cModule::buildInside()`；因此该固定 ROI 的 OMNeT++ 热点主要仍在模块构建/连接及 C++ exception unwind 路径，而不是稳态 token-passing event loop。相应 IPC、MPKI 与 RNF latency 必须解释为这个 guest phase。

## 与 IPC、MPKI、memory-stall 的对应

函数热点给出“ROI 中退休 guest 指令落在哪些调用路径”，IPC/MPKI/memory-stall 给出同一模拟窗口的性能和存储系统压力。比较时应先看各策略的 sampled function distribution 是否稳定，再把 RNF weighted/type latency、L2 MPKI 与 `memstall_any_load` 的同向变化作为对应关系；只有三种策略、单一 checkpoint，不能从相关性推出因果。逐策略数据已并列放入 `analysis/summary.json`，PPT 中也采用这一限制。

- CPU0/libquantum：三策略的第一热点函数相同。LRU: IPC 1.60602, L2 MPKI 10.418, memory-stall 4424, RNF weighted 195.49 cyc, top `quantum_gate1` 100.0%；Random: IPC 1.59456, L2 MPKI 10.419, memory-stall 5297, RNF weighted 196.76 cyc, top `quantum_gate1` 100.0%；SRRIP: IPC 1.60966, L2 MPKI 10.418, memory-stall 3612, RNF weighted 194.93 cyc, top `quantum_gate1` 100.0%。最高 IPC 为 SRRIP，最低 L2 MPKI 为 SRRIP，最低 memory-stall 为 SRRIP，最低 RNF weighted latency 为 SRRIP。
- CPU1/omnetpp：三策略的第一热点函数相同。LRU: IPC 0.42408, L2 MPKI 6.448, memory-stall 3817314, RNF weighted 189.28 cyc, top `read_encoded_value_with_base` 44.6%；Random: IPC 0.44737, L2 MPKI 6.254, memory-stall 3791349, RNF weighted 184.82 cyc, top `read_encoded_value_with_base` 45.3%；SRRIP: IPC 0.42524, L2 MPKI 6.456, memory-stall 3819705, RNF weighted 188.49 cyc, top `read_encoded_value_with_base` 45.8%。最高 IPC 为 Random，最低 L2 MPKI 为 Random，最低 memory-stall 为 Random，最低 RNF weighted latency 为 Random。

## Instrumentation 开销与语义

20M-tick 短跑 A/B 的所有非 host 数值 stats 差异数为 0；latency-only、profile-only、两者同时的 hostSeconds 开销依次为 +2.27%、+4.36%、-1.75%。正式 2B-tick 相对未采样的上次 direct-PoCQ 同统计运行为：LRU +5.12%, Random -0.42%, SRRIP -1.46%；其非 host stats 差异数为 0。 其中负值不表示 instrumentation 加速了 gem5，而表示单次 host wall-clock 抖动已大于待测开销；因此这里只报告原始观测范围，不给出虚假的稳定正开销。两个 recorder 只做宿主侧 vector/CSV 记录，不调度新的 simulated event，不改变 CHI message、credit、queue 或 terminal 条件；A/B 对照的 guest/CHI/SLC/NoC/DRAM stats 完全一致时，才把它标记为 timing-neutral。host overhead 不是 guest latency。

源码位置：

- `src/mem/cache/CHI/Cache2ChiBridge.cc/.hh/.py`：RNF exact-ID latency。
- `src/cpu/o3/probe/guest_call_stack_profiler.cc/.hh` 与 `GuestCallStackProfiler.py`：guest ROI sampler。
- `configs/example/kmhv2_chi_2x2_hnf_se.py`：精确 ROI start/stop。

## 验证与可复现性

`validation/validation_summary.json` 状态为 `passed`。SHA-256 清单在 `validation/sha256_manifest.json`，包含输入、两个 ELF、OMNeT++ ini、checkpoint、gem5、config、runner、instrumentation 和图表/PPT 脚本。

完整复现命令：

```bash
cd /home/makenma/project/xs-gem5/GEM5
python3 -m venv /tmp/chi-analysis-venv-20260801
/tmp/chi-analysis-venv-20260801/bin/python -m pip install -r util/xs_scripts/chi_2x2_latency_flame_requirements.txt
scons build/RISCV/gem5.opt -j8
scons --unit-test build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt build/RISCV/mem/cache/CHI/slc_snoop_filter.test.opt build/RISCV/mem/cache/CHI/hnf_slcsf_backend.test.opt build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt build/RISCV/mem/cache/CHI/cache2chi_bridge.test.opt build/RISCV/mem/cache/CHI/chi2classic_mem_bridge.test.opt build/RISCV/mem/cache/CHI/hnf_seq_pocq_state_graph.test.opt build/RISCV/mem/cache/CHI/hnf_pocq_state_graph.test.opt build/RISCV/mem/cache/CHI/home_link_layer.test.opt build/RISCV/mem/cache/CHI/hnf_cc_types.test.opt -j8
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py run results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1 --skip-warmup --checkpoint-source results/chi-2x2-dual-public-proxy-direct-victim-v1/warmup/common_full_cpt --roi-ticks 2000000000 --seed 20260730 --guest-stack-sample-period-insts 1000
python3 util/xs_scripts/chi_2x2_instrumentation_ab.py results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1 --ticks 20000000
/tmp/chi-analysis-venv-20260801/bin/python util/xs_scripts/chi_2x2_latency_flame_analysis.py results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1 --run-unit-tests --ab-summary results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1/validation/instrumentation_ab_summary.json
/tmp/chi-analysis-venv-20260801/bin/python util/xs_scripts/chi_2x2_latency_flame_presentation.py results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1
```

结果目录：`/home/makenma/project/xs-gem5/GEM5/results/chi-2x2-dual-public-proxy-rnf-latency-flame-v1`。

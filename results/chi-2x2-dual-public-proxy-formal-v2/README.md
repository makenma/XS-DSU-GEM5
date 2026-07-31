# 双核 2×2 CHI 共享 SLC 替换策略实验

> **数据分类：public-source proxy; not SPEC CPU2006。** 本目录中的 libquantum 与 OMNeT++ 均为公开源码代理负载，任何数值都不是 SPEC CPU2006 分数。

本目录是从同一个、SLC 恰好满载的公共 checkpoint 启动的三策略正式结果。两个独立单线程进程在同一 gem5 实例内并发执行，CPU/workload 映射始终固定：

- CPU0 → RNF0：libquantum 0.2.4 Shor 公开源码代理，参数 `1397 8`。
- CPU1 → RNF1：OMNeT++ 3.3.2 Token Ring 公开源码代理，参数 `-f omnetpp.ini`。
- RNF0 与 RNF1 经 2×2 CHI Mesh 访问同一个 HNF/SLC。

## 结论摘要

固定 2B tick（每核 4,597,700 cycle）的本次 ROI 中，Random 的 aggregate IPC 最高：1.608441。LRU 为 1.581214（相对 Random -1.6927%），确定性 2-bit SRRIP 为 1.586895（-1.3396%）。LRU 和 SRRIP 的 victim 数分别比 Random 少 1,210（-2.0462%）和 943（-1.5947%），但 HNF service stall 均约高 10.3%。因此，本窗口中“减少 victim”没有转化为更高 IPC；该观察仅适用于当前代理负载、拓扑和 ROI，不能外推为通用策略排序。

## 关键证据

- 公共 checkpoint：`warmup/common_full_cpt`，tick 1,378,457,840。
- checkpoint SLC：16,384 / 16,384 lines；1 MiB、1024 set、16 way、64 B line。
- 三策略恢复点相同，Random seed 固定为 `20260730`。
- 三次正式 ROI 均为 2,000,000,000 ticks；结束时两个 CPU 均保持活跃。
- 三策略峰值占用均为 100%，且 replacement/victim 均大于 0。
- 每个策略均满足 `clean + dirty = total = replacementAttempts`。
- 三次 50M-tick Random 短复现除 4 个 host wall-time/rate 字段外，10,035 个有限数值仿真统计全部逐值相同。

## 目录导航

- [完整中文实验报告](EXPERIMENT_REPORT_ZH.md)
- [机器可读实验清单](manifest.json)
- [运行状态摘要](run_summary.json)
- [结构化分析](analysis/summary.json)
- [每核指标](analysis/per_core_metrics.csv)
- [相对 Random 的策略对比](analysis/policy_comparison.csv)
- [SLC 占用](analysis/slc_occupancy.csv)
- [HNF victim](analysis/hnf_victims.csv)
- [victim-by-RNF](analysis/victim_by_rnf.csv)
- [victim-by-set](analysis/victim_by_set.csv)
- [Router 热点](analysis/router_hotspots.csv)
- [验证摘要](validation/validation_summary.json)
- [Random 可复现性](validation/random_reproducibility.json)
- 原始正式运行：`runs/lru`、`runs/random`、`runs/srrip`
- 中文汇报：`双核2x2_CHI_公开源码代理_SLC替换策略分析.pptx`

## 一键复现

在 GEM5 仓库根目录执行：

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

只重新分析已有原始结果：

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py analyze \
  results/chi-2x2-dual-public-proxy-formal-v2
```

生成 PPT 和渲染预览：

```bash
/home/makenma/project/xs-gem5/.venv-pptx/bin/python \
  util/xs_scripts/chi_2x2_dual_proxy_presentation.py \
  results/chi-2x2-dual-public-proxy-formal-v2
```


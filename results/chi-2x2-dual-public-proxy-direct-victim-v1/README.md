# 双核 2×2 CHI SLC 替换策略：Direct-PoCQ Victim 模型

> **public-source proxy; not SPEC CPU2006。** 本目录使用 libquantum 0.2.4 与 OMNeT++ 3.3.2 公开源码代理负载，结果不是 SPEC CPU2006 分数。

本目录保存 SLC victim 路径按 RTL 修正后的三策略正式结果。SLC dirty/M-state victim 不进入持久 SLC victim buffer，而是在 U1 抓取后交给 PoCQ，通过 `WriteNoSnpFull` 写回 DDR；SF victim 仍走 SEQ+snoop。

## 关键结论

三策略均从同一满载 checkpoint 启动并运行 2B ticks：

| 策略 | Aggregate IPC | Victims/KI | Replay/KI | Service-stall/KI | Victim-buffer Replay | Host elapsed/s |
|---|---:|---:|---:|---:|---:|---:|
| LRU | 2.030094 | 7.9380 | 2.3704 | 26.5593 | **0** | 537.8 |
| Random | **2.041935** | **7.7801** | **2.0926** | 26.3876 | **0** | 562.2 |
| SRRIP | 2.034898 | 7.9482 | 2.2152 | **26.3408** | **0** | **535.9** |

Random 的 guest IPC 最高，但宿主运行时间最长；三个策略的 simulated ticks 和 cycles 完全相同。与旧 victim-buffer 模型相比，三策略 IPC 提升 26.95%–28.39%，Replay/KI 下降约 95%，而 Victims/KI 基本不变或下降。这说明旧模型的主要问题是人为 backpressure/Replay，不是 victim 数本身。

## 目录导航

- [完整中文报告](EXPERIMENT_REPORT_ZH.md)
- [实验清单与哈希](manifest.json)
- [结构化总表](analysis/summary.json)
- [三策略对比 CSV](analysis/policy_comparison.csv)
- [Replay/背压 CSV](analysis/hnf_pressure_replay.csv)
- [Victim CSV](analysis/hnf_victims.csv)
- [旧模型→direct-PoCQ A/B CSV](analysis/model_change_comparison.csv)
- [旧模型→direct-PoCQ A/B JSON](analysis/model_change_summary.json)
- [单元测试结果](validation/cpp_unit_tests.json)
- [中文汇报 PPT](双核2x2_CHI_公开源码代理_SLC替换策略分析.pptx)
- [PPT 渲染检查](validation/ppt_render_report.json)
- 原始结果：`runs/lru`、`runs/random`、`runs/srrip`

## 复现

```bash
python3 util/xs_scripts/chi_2x2_dual_proxy_experiment.py run \
  results/chi-2x2-dual-public-proxy-direct-victim-v1-reproduced \
  --memory 1GB --seed 20260730 \
  --roi-ticks 2000000000 --min-roi-insts-per-core 100000 \
  --skip-warmup \
  --checkpoint-source \
  results/chi-2x2-dual-public-proxy-formal-v2/warmup/common_full_cpt
```

当前正式 binary SHA-256 为 `fc44f8301caeba55cc56012892941e6331dc153aa3d074df2da840af93b08e2a`。运行时源码尚未提交，完整 source-state 和输入哈希见 `manifest.json`。

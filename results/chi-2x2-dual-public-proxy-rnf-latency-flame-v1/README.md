# CHI 2×2 dual-public-proxy RNF latency + guest flame v1

Validated result directory for the fixed 2,000,000,000-tick LRU/Random/SRRIP experiment. Workloads are public-source proxies, not SPEC CPU2006 scores.

## Key artifacts

- Chinese report: [EXPERIMENT_REPORT_ZH.md](EXPERIMENT_REPORT_ZH.md)
- Main machine summary: [analysis/summary.json](analysis/summary.json)
- Policy comparison: [analysis/policy_comparison.csv](analysis/policy_comparison.csv)
- RNF latency: [analysis/rnf_transaction_latency.csv](analysis/rnf_transaction_latency.csv), [JSON](analysis/rnf_transaction_latency.json)
- RNF plots: [RNF0 PNG](analysis/rnf0_transaction_latency.png), [RNF1 PNG](analysis/rnf1_transaction_latency.png)
- Guest profiles: [analysis/flamegraphs](analysis/flamegraphs)
- Validation: [validation/validation_summary.json](validation/validation_summary.json)
- SHA-256 manifest: [validation/sha256_manifest.json](validation/sha256_manifest.json)
- Chinese presentation: [中文汇报_RNF延迟与Guest火焰图.pptx](中文汇报_RNF延迟与Guest火焰图.pptx)

## Terminology guardrail

- simulated ticks/cycles: modeled gem5 time;
- guest workload IPC: committed guest instructions per guest CPU cycle;
- host wall-clock: time spent running gem5 on the host;
- RNF transaction latency: exact-ID matched CHI transaction duration inside the ROI;
- guest flame graph: CPU-specific guest ELF symbols from ROI retired-instruction samples;
- host gem5 profiling: not used for these workload flame graphs.

See the Chinese report for methodology, limitations, results, and full reproduction commands.

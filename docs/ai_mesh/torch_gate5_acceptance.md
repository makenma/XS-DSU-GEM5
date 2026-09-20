# Torch Gate 5 验收入口

当前裁决、逐项义务、设计范围、已接受修复、测试数字和证据哈希的唯一状态源是 [torch_gate5_acceptance.json](torch_gate5_acceptance.json)。Gate 4 revision 34 保留历史冻结身份；其五项真实传输跨门义务由本 Gate 5 权威记录关闭，不回写历史证据。

## 范围

真实 AXI/Garnet 的有限队列、读乱序归位、全部 B 后退休、WSTRB、peer 实际提交、错误排空、loader 零数据面流量与 E2E-1/2。FUNCTIONAL_BYTES 证明实际搬运内容及账目，不承诺数值 GEMM/REDUCE 或真实 NPU ISA。

CPP-06 的同 ID 行为遵循已接受的单 in-flight/ID 设计：ID 在 RLAST 后才可复用；同 ID 并发并非当前模型的可达义务。不同 ID 乱序、复用时刻、逐执行内容和退休仍受完整验证。

## 可审阅证据

- [历史实现快照](torch_gate5_evidence/implementation-reviewed.json)：冻结的输入身份、19行覆盖映射、测试索引及原始证据引用；其中旧 pending/partial 文本属于历史输入，当前裁决只读上述权威。
- [矩阵索引](torch_gate5_evidence/matrix.json)：每个case的命令/结果/oracle/程序身份引用。正常或合法错误drain与预期fail-closed分开统计。
- [主审历史链](torch_gate5_evidence/review-history.json)：保留修复要求和最终接受记录的原文机器状态与哈希，不改写旧结论。
- [最终清单核对](torch_gate5_evidence/manifest-final.json)：实际逐文件读回；唯一已知偏移是单独列出的主审文档索引变动。
- [最新独立反例重放](torch_gate5_evidence/m20-independent-probes.json)、[定向回归](torch_gate5_evidence/m20-independent-focused.txt)、[最终实现测试](torch_gate5_evidence/focused-233.txt)与[矩阵运行](torch_gate5_evidence/matrix-46.txt)。
- [发布前构建](torch_gate5_evidence/publication-build.txt)与[package/golden复核](torch_gate5_evidence/publication-package-golden-9.txt)。

Git只保存精简验收记录及源码。原始内容寻址程序、runtime JSON、trace和build产物仍在本地 `.tmp/docs/torch-gate-5-implementation/` 等归档中；矩阵索引保留其精确哈希，不宣称它们随Git分发。独立机器上的新运行应使用当前源码与锁定环境重新生成证据，不能伪造本机归档。

构建环境要求见 [README](../../README.md) 和 [quick start](../quick_start.md)。本机独立提取的ISL开发包属于环境产物，不进入源码；发布复核保留了配置缓存修复日志，重新链接后的验收二进制身份未变。

## 下一阶段

[Gate 6 实施 prompt](torch_gate6_implementation_prompt.md)定义 collective/workload 的交付要求。Gate 5 接受不代表 Gate 6 或 Gate 7 已实现；本次提交与推送的授权不延伸为后续 agent 自动提交授权。

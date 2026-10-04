# 实现状态与验证边界

更新：2026-10-02。原始目标见项目根目录 `plan.md`。

## 本轮扩展

四核 SE 共享页、私有 classic L2、CPU 测试页、有限 HNF/SLC/SF 行表、双内存通道、实际 3×2 mesh、JSONL 1.1 和源码联动均已实现。默认 IRIW；协议测试中 19/19 类均由 CPU 在 RN→HN 发起并完成。压力测试通过 128 次跨核数据检查，实际产生 300 次 RetryAck 和 315 次 victim。

当前回归为 C++ 展开器 47 项、前端 7 项、gem5 FlowGraph/HNF/RNF 共 90 项，均通过。原有九个示例通过 748 次独立协议核查。IRIW、协议测试、压力测试各自比较采集开关，实际读回、结束 tick 和分别 8671 / 9456 / 9968 个系统统计一致。Chrome headless、1600×1000 的 10 万事件 / 200 可见 flit 平均约 127 FPS；精确值及采集时间见性能报告。

接口、语义修正、固定版本、重现命令及验收范围见 [四核 litmus 与 19 类事务](extended-litmus.md)。报告包括 `build/litmus-*/acceptance.json`、`build/qa/litmus-invariance.json`、`litmus-attribution.json`、`litmus-browser-report.json` 和 `browser-report.json`。协议测试另有 11 项部分/无有效字节数据比较记为 insufficient，保留未知证据；实际 CPU 数据检查已通过。

以下记录保留首版 `h-core-256` 的实现与历史验证边界；新 `h-extended-256` 的范围以上述扩展说明为准。

## 首版实现

- 版本化 JSONL、C++17 可关闭观察器、独立 C++20 展开器与 CHIron 固定版本字段及 RN-F/SN-F 事务关联检查。
- 六个真实 HNF/SLCSF 组件场景、显式建模的有序读变体，以及实际 gem5 事件循环的 miss→hit、RN 重试示例。组件测试对每个场景比较开关导出器前后的 tick、计数、数据及缓存终态。
- root / leg / parent / attempt 身份传递，协议 ID 分配和释放、write DBID 映射、completion DBID → CompAck、两拍 DataID 0/2、重试信用先到及错误类型处理。
- React/TypeScript/Three.js 本地回放、Worker 解析/索引/状态计算、随机定位与回退、字段来源跳转、过滤、时序图窗口、最多 200 flit 的实例化渲染和路径聚合。
- 浏览器测试覆盖加载、播放暂停、节点过滤、写 DAT 的 DBID 跳转、ReadReceipt DBID=0、实际运行示例、读 BE 不适用标记、100k 事件 / 200 flit 性能、页面异常。
- 展开器包含 41 项回归；前端状态/导入 6 项、FlowGraph 组件 3 项、原 HNF 62 项及 RN 25 项回归。RN 测试包含新增的 3 项信用匹配与消耗测试；各次运行结果见下方日志。

## gem5 接点

`scripts/apply-gem5.py` 从固定 commit 重建有限目标文件，携带的观察器代码位于 `native/gem5`。

| 接点 | 记录 |
|---|---|
| BaseFlit metadata | 显式事务树、重试代次、消息身份、数据来源 |
| Cache2ChiBridge | RN 请求身份、实际 SAM 算法参数及索引、写/读/CompAck/snoop 身份继承、RN 完成 |
| HnfCoherencyController | 分配、lookup、状态提交、同地址等待、snoop/MC/dirty victim 子事务及完成 |
| HomeLinkLayer | RetryAck 与 PCrdGrant 的创建来源 |
| Chi2ClassicMemBridge | SN 分配/完成、真实内存数据或模拟器补零来源 |
| ChiCommonPort | 已配置端点边界的成功 TX/RX、实际 enqueue 失败的队列信用/接收门控等待 |

`native/gem5/smoke.py` 提供真实事件循环配置，`scripts/collect-runtime.sh` 构建、采集并比较开关结果。运行验收以 `build/qa/runtime-report.json` 为准；组件级验收以 `demo-tests.log` / `build/qa/components.log` 为准。

**实际运行验收已通过**：Ubuntu 的注册位置及虚拟磁盘已迁至 `D:\wsl\Ubuntu`，完整 RISCV 可执行文件编译成功。同一地址完成两笔 RN 读取，HNF lookup 为 miss→hit，SN/HN 共返回六拍数据，结束于 tick `800000`。开关导出器前后的 tick、RN latency CSV 和系统统计一致；开启采集的 SN→HN→RN 数据一致。实际运行使用 SimpleMemory 的初始零值；非零数据、dirty owner 以及缓存终态的开关对比由组件测试覆盖。`scripts/run-runtime.sh` 可直接复跑已有二进制。

实际 RN 重试场景同样通过：HN 只有一个 PoCQ 在途名额，两次读取以 1ns 间隔发出，观测到三条 RN 请求（其中一次重发）、一次 RetryAck 和一次 PCrdGrant。两笔逻辑请求均完成，结束于 tick `800000`，导出开关结果一致；报告为 `build/qa/runtime-retry-report.json`。

## Issue H 手册对照

依据项目内的 716 页 `IHI0050H_amba_chi_architecture_spec.pdf`；页码为该 PDF 的页码。`scripts/audit-sessions.py` 对九个已保存示例执行 748 次独立核查，结果为零问题；手册 SHA-256 和逐规则计数保存在 `build/qa/protocol-audit.json`。它独立检查 opcode 编码、字段来源、ID 生命周期、写数据/CompAck ID 来源、返回字段、有序读、内存属性、DataID/BE 和信用依赖；错误轨迹由展开器的单元反例补充覆盖。

| 手册位置 | 发现的问题及处理 |
|---|---|
| B2.4.2–B2.4.4 / B2.4.10，102–107 页 | RN 请求错误使用自身 ReturnNID，且 HN 下游读缺少 ReturnTxnID；已补齐 gem5 字段及展开映射。分开的 Comp / DBIDResp 检查原始 DBID 一致性；协议 ID 在节点/代次内独立管理，每节点最多保留 1024 个请求 ID。 |
| B2.5.1.4 / B2.5.3，116–127 页 | 修正等待 DBIDResp 或 ReadReceipt 时过早释放请求 ID；写 DAT 使用响应 DBID，CopyBack / Immediate 写 DAT opcode 分开检查。 |
| B2.7.3 / B2.7.5，137–141 页 | 保留实测 ExpCompAck，检查 ReadShared/ReadUnique 的必需 CompAck、HN→SN 禁止 CompAck；Order=10/11 在 HN→SN 上被拒绝。普通读不补 ReadReceipt，有序读不重复补出已有确认。 |
| B2.8.3.5，151–152 页 | 展开器曾把下游 MemAttr 清成默认值；现保留观测属性，检查下游读继承与 SLC eviction 的 MemAttr=13 / SnpAttr=0。 |
| B2.9.3 / B2.9.4，163–165 页 | CompData 的 BE 不适用，不能用来掩盖错误数据。写/snoop 检查 BE 和零字节要求；256-bit DAT 使用 DataID 0、2，检出乱序之外的重复/缺失拍。跨拍 Resp 等保持一致；RespErr 可变化，按实测保留。 |
| B2.10.2 / B2.10.3，181–182 页 | 首发 AllowRetry=1 要求 PCrdType=0；预先收到 P-Credit 不能替代 RetryAck，二者齐备才能重发。修正 gem5 RN 原来按 TxnID 匹配 PCrdGrant 的问题，信用按节点对与类型管理并只消耗一次。RN、HNF 下游读和 dirty victim 路径已有信用处理回归。 |
| B13.10.18，482–487 页 | 已将首版 REQ/RSP/DAT/SNP 的 opcode 编码与四张表逐项核对，独立脚本检查所有示例。 |

OWO 有序写，以及 CopyBack 不传数据、以单独 Comp 完成的变体，需要额外的完成确认流程，当前明确记为 unsupported 并完整保留原始事务，不套用普通写流程。上述通过结果只覆盖有限场景，不能作为完整 Issue H 一致性认证。

缺失写/snoop BE 时保留原始事务并标记证据不足，不把未知值当成全零，也不因停止展开而误报缺失数据拍。已知 BE、未知 payload 的数据仍可回放，字节保持未知。默认重发优先复用空闲的原协议 ID；原 ID 被其他在途请求使用时仍不能强行复用，换 ID 的 CHIron 覆盖限制单独说明。

验证记录：`build/qa/native-windows.log`、`native-linux.log`、`components.log`、`hnf-regression.log`、`rnf-regression.log`、`runtime-report.json`、`runtime-retry-report.json`、`protocol-audit.json`、`browser-report.json`。

## 覆盖边界

- CHIron 接入 E.b flit 字段映射和 RNFJoint / SNFJoint 事务检查；有限 Issue H 状态机和显式关联另行检查。没有声称完整 Issue H 验证通过。固定 CHIron 的重试匹配仍依赖相同 TxnID；重发换 ID 时其 transaction-joint 结果记为 unsupported，独立 Issue H 检查仍执行。
- 已按用户要求在 gem5 中补齐下游读取的首发 AllowRetry=1，新增实际 MC ReadNoSnp 的 RetryAck/P-Credit/换 TxnID 重发路径。信用按 requester/completer/PCrdType 管理，允许提前到达；导出开关不参与这些协议决策。RN 组件驱动同样显式设置首发字段。展开器不再把原始 AllowRetry=0 改为 1；缺少信用证据时保留原始视图并报告。
- RN 的节点对/信用类型记账与导出器独立，已用实际事件循环触发并完成重试。任意 PCrdGrant.TxnID、信用先于 RetryAck、错误节点/类型及两笔请求竞争一份信用由 RN 组件回归覆盖。未用完信用支持保存与恢复，但新增信用状态的 checkpoint 往返尚未单独验收。
- `h-core-256` 只展开对齐的 64B 整行事务。部分有效字节保留并检查；小于整行的请求、未支持 opcode、超出 52-bit PA 的请求保留原始视图。
- Dirty owner 数据正确性有实际组件场景验证；普通运行示例目前覆盖 miss→hit 和 RN 重试。组件测试的 RN/SN 是测试端点，网页清楚标注来源。
- 端点队列观察只覆盖实际发生的 enqueue 尝试；提前查询 credit 后未尝试发送的等待、router 内部阻塞不会被猜测或归因。已记录 SLCSF 接收信用、初始化/排空、replay 原因；内部所有 set lock 排队阶段尚未全部导出。
- 一般 checkpoint 恢复场景没有自动导出完整初始缓存内容；缺失初态保留未知。SEQ 引发的复杂 SF victim 扩展和任意生产负载仍需扩大验证。
- 性能报告来自当前机器的 Chrome headless；公开在 `build/qa/browser-report.json` 中，单次解析时间由 Vitest 输出。不是跨设备保证。
- 当前 WSL 为 Ubuntu 24.04.2；Ubuntu 22.04 + GCC 13 的安装路径已说明，但尚未在第二套发行版复验。

固定 gem5 的 SN 发送函数在 `pop_front()` 后继续读取已销毁的 DAT 引用；安装脚本将所需标量提前保存，避免观察元数据拥有字符串后扩大该悬空引用问题。修改不改变队列调度或事务状态机。

首版不覆盖 RNI/HNI、DMT/DCT/DWT、Atomic、DVM，也不实时控制 gem5。

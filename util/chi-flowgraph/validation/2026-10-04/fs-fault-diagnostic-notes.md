# 有限 FS FetchFault 诊断交接（2026-10-04）

本目录只记录已完成的 5,000,000,000 tick Linux 启动前缀诊断。它不是 Linux 全套验收，也不解除原严格比较的 FAIL。

原始计划位于 `fs-inputs.json`，其 SHA256 为 `3ae09d15ec0d661f0c687eada2b88cd5a1036acc3ca2ef322a7b8a785efbc3b8`。`state.json` 保存两次实际命令、PID、二进制 SHA 和退出状态；`exec-session-completion.json` 保存工具 session59269 真正返回的终态。两次 gem5 都在原定 5e9 tick 上限以预期 host exit2 结束，外层诊断工具实际 exit0。该 exit0 只表示有限诊断完成。

两次诊断仅在原命令上增加全程 `FetchFault` 日志及独立 outdir，原 firmware、seed、4 CPU、2 NUMA、suite inputs、progress、预算和 cwd 均保持。`before/after-same-binary-debug-invariance.json` 分别将诊断与同一二进制的原运行重比：9,626 项非宿主统计、实际配置、结果、进度、串口、DTB、198,138 次观测的 census 全部一致。这里只证明打开稀疏日志没有改变本次有限运行结果，不能表述为旧新模型等价。

| 已读实际日志 | MODEL05 | candidate02 |
|---|---:|---:|
| fault handler 入口日志条数 | 45 | 48 |
| 新增 demand fault 日志条数 | 旧版没有该观测点 | 23 |
| demand cause=12（instruction page fault） | 不据旧日志推断 | 4 |
| demand cause=1（instruction access fault） | 不据旧日志推断 | 19 |

入口日志条数不能直接当作不同 MMU callback 或不同架构异常数量：旧版延后事件可以重新进入同一 handler。所有观测均来自 CPU0，完整原文、逐行解析与 SHA 位于两个子目录，不能用地址相同推定同一事务/FTQ。

四个低地址时刻 `3492776000 / 3500103000 / 3500180000 / 3500265000`，旧版每时刻仅出现一条 fault handler 入口；新版每时刻保留两个请求的故障，并在相同时刻产生一个 cause12 demand carrier。其后高地址区段，候选出现十九个 cause1 demand carrier，大多比对应 handler 入口晚 1000 ticks。`3595596000` 的 `ffffffff80a04920/ffffffff80a04940` 两请求有故障入口，后续没有该 PC 的 demand 日志；是否被更老控制恢复及时取消，需要详细顺序日志确认。旧版另有 `3592529000` 对 `ffffffff80a04880` 的额外 handler 入口，新版没有对应入口。稀疏日志未提供 seq/FTQ，不能从这些事实独自解释全部统计差异。

源码提供两个可证伪方向。冻结修复前 `fetch.cc` 的 `handleTranslationFault` 会 reset 整组请求并立即创建 fault carrier（`build/qa/fetch-demand-fault/before/fetch.cc:957`），旧 `validateTranslationRequest` 用整组 pending 状态决定是否计入 `tlbSquashes`（同文件第878行）。候选按 RequestPtr 和该请求 TlbWait 状态验证（`src/cpu/o3/fetch.cc:841`），handler 只保存故障（第929行），实际所需字节触发唯一 carrier（第1808行）。这与上述日志差异相容，但尚未把 FS 的每个差异绑定到完整动态事件；修复前快照为 model09，MODEL05 的具体实际调用链仍应以其冻结源码/运行日志核对。

服务器接续必须保留以下未解决状态：

- `strict-report.json` 仍为 **FAIL**：13 项 CPU0 前端统计不同，包括 `tlbSquashes 8→4`、fetch cycles `+23`、Running `+24`、TrapPending `-24`、decodedInsts `-7`。
- 不得直接套用已解释的 RTL-SE 22 项或 BPU 23 项统计差异许可；FS 有多次独立故障与页表/特权状态转换。
- 下一步可在保持同一完整 5e9 运行输入的前提下，只对上述四个低地址时刻、`3592528000–3592560000` 的重复入口及 `3595596000` 的未供给 carrier 附近启用 Fetch/Decode/Commit/DecoupleBP 窄窗，核对 RequestPtr 生命周期、seq、FTQ、fault carrier 数量、较老 squash 与提交结果，再解释全部13项。此建议尚未执行，不能算测试结果。
- 用户改为服务器交接后，本代理没有再启动仿真或修改模型。完整 Linux litmus/random-memory/kernel-build 验收仍待后续真实完成。

本次二进制仍绑定原基线 HEAD `65f3241a6cd486dffddaca251536f5c691af9a1f` 及冻结补丁/source SHA。以后创建的新分支提交不应重写该历史身份。

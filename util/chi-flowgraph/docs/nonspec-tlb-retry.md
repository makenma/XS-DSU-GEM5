# KMH O3：已获提交授权的 CBO 延迟翻译重放

适用 gem5 固定提交：`65f3241a6cd486dffddaca251536f5c691af9a1f`。这是 CPU 内存调度修复，不改变 CBO 指令、CHI opcode、L2 状态机或 NoC。

## 已复现的问题

真实 Linux `linux-suite-03` 在 tick `103161925000` 命中 `IssueQue::retryMem` 的 `!inst->isNonSpeculative()` 断言。独立的四核 KMH / ChiNativeL2 / NoC 裸 FS 测试又在旧 `fs-model-04-bpu` 上复现：冷 DTLB 的 `cbo.flush`，PC `0x80000094`、序号 `621`、tick `3672000`。记录见 [旧版本重现结果](../build/native-cbo-tlb-before/flush/cbo-tlb-result.json)。

本固定版本中，SE 地址翻译直接查询 Process 页表并同步完成，不能用 SE 的首次访问证明硬件 walker 延迟重放正确。实际回归使用 FS、Sv39 页表和冷 DTLB。

## 控制流与修改边界

以下行号来自修补前的固定源码（LSQ 文件包含本项目原有 CBO/observer 补丁）：

- `commit.cc:1639–1701` 在 ROB 头、满足旧 store 排空条件后发出非推测执行授权；`inst_queue.cc:739–753` 的 `scheduleNonSpec` 设置 `AtCommit`，首次发射后删除 `nonSpecInsts` 项。重试不能重新插入或重新申请这份首次授权。
- CBO 带静态 `IsNonSpeculative` 和 `IsStore`。`lsq_unit.cc:1435–1451` 启动翻译并标记 TLB miss；`1584–1588` 将指令放入 deferred list 后立即 `endPipelining`。翻译回调 `lsq.cc:1732` 附近保存 fault 和 `translationCompleted`，没有把指令标成已执行。
- 原 `inst_queue.cc:608` 将该指令送入只接受普通内存指令的 `retryMem`。修改后，仅 deferred list 的静态 nonSpec 分支调用新 `retryCommitAuthorizedMem`；该分支中的 squashed 指令直接丢弃。普通指令（包括其原有 squashed 重放行为）、cache miss、STLF、cache blocked 三条路径保持原样。
- 新入口要求同一 IssueQue、`IsNonSpeculative`、`MemRef`、已有 `AtCommit`、翻译已完成，并要求未 squash、未在流水线中、未执行。错误状态保留运行时诊断（seq、PC 和状态位），普通 `retryMem` 的原断言保留。
- 新入口进入原 `replayQ`。`issue_queue.cc:369–401` 继续限制 outports、load/store pipe 数；不进入普通 ready queue，不触碰该队列的占用计数。默认关闭的 `Schedule` 日志可显示专用重试的 seq、PC、fault。
- `lsq_unit.cc:1034–1043` 重新进入 store pipe，`dyn_inst.hh:1032–1041` 的 `beginPipelining` 清除 NeedReplay、SkipFollowingPipe 和 replayType。补丁不提前清这些状态，也不清 `AtCommit`、fault 或 savedRequest。
- `lsq.cc:1627` 附近的 `pushRequest` 在 translationStarted 时复用 savedRequest；只有新建 Request 才清 fault。`lsq_unit.cc:1468` 附近的 `storeDoWriteSQ` 只在 NoFault 时写入。因此完成的 translation fault 也必须重放，以进入原有精确异常提交路径，不能被 NoFault 检查拦下。

本版本原有 LR/SC/AMO 没有静态 `IsNonSpeculative` 标志；它们仍走原重放路径。动态 STRICT_ORDER 属性也不会因此被当成静态 nonSpec。新 API 仅用于已有提交授权的 deferred 翻译完成，不能用于重复发射已经接受的 cache-port 请求。

## 安装与验证

补丁源是 [nonspec_tlb_retry_patch.py](../native/gem5/nonspec_tlb_retry_patch.py)，安装器是 [apply-nonspec-tlb-retry.py](../scripts/apply-nonspec-tlb-retry.py)，已经接入 `apply-gem5.py`。安装器核验固定 HEAD、三文件原始 SHA256、唯一替换上下文；只接受原文件或精确目标文件，先检查全部目的文件，再写入。重复安装保持源码 mtime。

```sh
python3 scripts/apply-nonspec-tlb-retry.py --gem5 /root/chi-flowgraph-work/gem5 --check
python3 scripts/apply-nonspec-tlb-retry.py --gem5 /root/chi-flowgraph-work/gem5 --snapshot-only
python3 -m unittest discover -s tests/native -p test_nonspec_tlb_retry_patch.py -v
```

7 个 focused tests 全部通过。其 C++17 测试编译执行生成补丁中的真实方法和 deferred 分流代码，使用小型 DynInst/queue 替身，覆盖八种非法入口状态、正常和带 fault 的保留状态、nonSpec squash、普通重试和安装器拒绝/幂等行为。它们是控制流检查，实际 CPU 回归另见下表。

审阅快照保存在 `build/nonspec-tlb-preview/{original,patched}` 及 `manifest.json`。初审后统一安装并构建了 `fs-model-05-nonspec`；回归使用其不可变二进制 `/root/chi-flowgraph-work/linux-validation/artifacts/fs-model-05-nonspec/gem5.opt`，SHA256 为 `4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d`。

## 真实 FS 回归结果

2026-10-04，四例均完整运行通过。每例配置四个 KMH / ChiNativeL2，经原 NoC 数据通路；仅 hart0 执行测试，其他三个 hart 停在 WFI。这是裸 FS 指令回归，不是 Linux 启动或四核并发测试。

| 用例 | 冷 CBO 实际 deferred tick | 最终 m5_exit tick | 原始 L2→NoC 维护 REQ | 已检查的 guest 结果 | 报告 |
|---|---:|---:|---|---|---|
| `inval` | 3672000 | 13934000 | 恰 1 条 `MakeInvalid` | CBO 后预期 S-mode ecall，cause=9、PC 匹配 | [结果](../build/native-cbo-tlb-after/inval/cbo-tlb-result.json) |
| `clean` | 3672000 | 13934000 | 恰 1 条 `CleanShared` | cause=9、PC 匹配 | [结果](../build/native-cbo-tlb-after/clean/cbo-tlb-result.json) |
| `flush` | 3672000 | 13934000 | 恰 1 条 `CleanInvalid` | cause=9、PC 匹配 | [结果](../build/native-cbo-tlb-after/flush/cbo-tlb-result.json) |
| `flush-fault` | 3680000 | 17204000 | 0 条 | 精确 StorePageFault，cause=15、CBO PC 匹配、tval=`0x40020000` | [结果](../build/native-cbo-tlb-after/flush-fault/cbo-tlb-result.json) |

四例的 host return code 和实际 m5_exit code 均为 0，运行输出记录的二进制 SHA 与实测 SHA 一致。有效映射三例在实际 tick `3682000` 发出对应 REQ，地址均为 `0x80020000`；请求数量和数值 opcode 来自 `system.l20.chi_side` 的原始 TX 记录，未通过协议细化补出。故障例在专用重放日志中明确记录 `fault=1`，固件比较 mcause、mepc 和 mtval 后才报告成功；其原始维护 REQ 数量为零。

复现任一新例（输出目录必须尚不存在）：

```sh
python3 scripts/run-native-cbo-tlb.py \
  --gem5 /root/chi-flowgraph-work/linux-validation/artifacts/fs-model-05-nonspec/gem5.opt \
  --case flush-fault \
  --output /mnt/d/proj/chi_flowgraph/build/native-cbo-tlb-rerun/flush-fault
```

旧版本的同路径断言证据保留在 `build/native-cbo-tlb-before/flush`。这些实测覆盖了三条 CBO 的冷翻译完成以及一个未映射页故障；Linux 长运行、多核交错和其他 CPU 路径的结果仍由实际运行单独记录，不能由本表推定全部正确。

# XS-DSU-GEM5 16 核 CHI Linux / Checkpoint 调试状态报告

报告时间：2026-08-25 03:20 CST（最终端到端验收）
项目目录：`XS-DSU-GEM5`
分支：`xs-dev`
基线提交：`7478835ac25d406941490578874d5022e3a46ec5`
目标配置：16 核 XiangShan O3 + 6×4 CHI mesh + 16 HN-F/SLC/SF + 4 DDR SN + 128 MiB Full-System Linux
总体状态：**端到端目标已经通过。direct-pmem-owner v7 冷启动完整进入 Linux `/bin/sh`，16/16 CPU online，真实 terminal 命令执行成功，并在 tick `67414051464` 生成第一份 checkpoint；该 artifact 的 direct-pmem 路由、gzip、CHI canonical 状态和修正后的精确 RISC-V 栈审计全部 PASS（15/15）。v9 从这份 checkpoint 成功恢复，没有 NULL/Oops/panic/PMP fault storm；恢复后的 terminal 返回 Linux uname、CPU count=16、online=0-15、uptime、PID 和 helper SHA。guest 随后在 tick `97174117152` 生成第二份 checkpoint，第二份 artifact 再次通过 direct-pmem、gzip、CHI 与精确 15/15，最终 `final_acceptance.status=PASS`。当前没有剩余技术卡点，gem5 已因第二次 checkpoint 正常退出。**

> 最新权威证据：cold checkpoint 为 `cpt.67414051464`；restore/re-checkpoint 为 `cpt.97174117152`；冻结 gem5 SHA256=`2dc5ae354b53d1c324e044f4e0a4b2fe74483aec5f79e7c0111261ca9d4bcad6`。v7 初次 0/15 是 auditor 错把调用者 `saved_s0` 与当前 `live_s0` 比较，精确 payload 反汇编证明正确关系是 `saved_s0=sp+32=live_s0+16`；修正后四类历史坏 artifact 仍为 0/15。v8 的 tick-0 abort 是沙箱拒绝创建 localhost socket，不是 restore/guest 错误；v9 在获批的本地 socket 环境完成了全部验收。详见 18.73–18.80。
>
> 本报告是持续追加的取证文档。第 4、5 节保留历史运行快照，第 15 节记录 timer/坏栈定位，第 16、17 节记录早期 checkpoint 与被证伪的修复假设；第 18 节记录最终 direct-pmem 修复、v7 cold、v9 restore 与二次 checkpoint 闭环。发生冲突时，以编号更大的更新节和本页顶部最终状态为准。

## 1. 一句话结论

最初的 Linux `NULL pointer dereference @ 0x4` 已解决；16 核 Linux cold boot、`/bin/sh`、第一次 checkpoint、checkpoint restore、恢复后的真实 terminal 输入，以及恢复后第二次 checkpoint 的完整语义审计均已通过。

最终闭环为：

```text
v7 cold boot
  -> Linux 6.10.7 / 16 CPUs / online 0-15 / real terminal
  -> cpt.67414051464
  -> direct-pmem PASS + gzip PASS + CHI PASS + stack 15/15 PASS
  -> v9 restore
  -> real terminal uname/CPU/online/uptime/PID/helper SHA PASS
  -> cpt.97174117152
  -> direct-pmem PASS + gzip PASS + CHI PASS + stack 15/15 PASS
  -> final_acceptance.status=PASS
```

最终根因是 checkpoint consolidation 的数据所有权与写入路径，而不是 Linux 本身：旧实现让 classic cache 的 functional write 经过 CoherentXBar/peer cache，stale 副本可以在落盘前污染真正 owner 数据；同时 cache 层级/peer 的写回优先级不足以保证同一 physical line 的最终值。修复后，普通内存 cache snapshot 直接调用 `PhysicalMemory::functionalAccess()`，避开 peer snoop mutation，并在整个 classic hierarchy 上全局执行：

```text
phase 0: shared / former owner
phase 1: clean Writable owner
phase 2: Dirty / Owned owner
```

正常物理内存 line 走 `direct-pmem`，仅非内存/MMIO 地址保留 routed fallback。最终两个 checkpoint 的路由日志都满足 `direct-pmem > 0` 且 `routed-nonmem = 0`。

v7 初次栈审计显示 0/15，但这是验收脚本自身的 frame-pointer 语义错误：在精确 payload 中，`arch_cpu_idle` 与调用者 `default_idle_call` 都有 16 字节 frame，因此停在 `arch_cpu_idle+0x10` 时，正确关系是：

```text
live_s0  = sp+16
saved_s0 = sp+32 = live_s0+16
saved_ra = live_ra = default_idle_call+0x18
```

旧检查错误要求 `saved_s0 == live_s0`。修正为精确 `saved_s0_chain` 后，v7 与二次 checkpoint 均为 15/15；all-valid、phased-owner、ordered、SLC-first 四类历史坏 artifact 仍全部是 0/15，说明 gate 没有被放宽成伪通过。

当前没有正在运行或卡住的 gem5：v9 已在 tick `97174117152` 因第二次 guest checkpoint 正常退出，最终 gate 返回码为 0。需要注意但不阻断验收的只有两类已知 warning：MicroTAGE block-index clamp，以及成功 baseline 中也存在的 UARTLite `-ENXIO` probe warning；两者均未导致 guest crash。

## 2. 验收矩阵

| 验收项 | 状态 | 已有证据 / 尚缺证据 |
|---|---:|---|
| 原始 Linux `NULL pointer dereference` | 已解决 | 多次越过原崩溃点，Linux 完整进入 `/bin/sh` |
| 16 核 Linux 冷启动 | 已通过 | `smp: Brought up 1 node, 16 CPUs` |
| Linux init / shell | 已通过 | `Run /bin/sh as init process`，shell 提示符可用 |
| cold-run terminal 输入 | 已通过 | guest 返回 `uname`、CPU 数量和 uptime |
| guest 内触发 checkpoint | 已多轮通过 | helper 输出 `Writing checkpoint`，退出原因是 `checkpoint` |
| CHI transient drain / canonical 检查 | 已实现 | 不可重建 transient state 会在 checkpoint/restore 时被显式拒绝 |
| 旧 checkpoint 文件格式 | 通过 | `m5.cpt` 存在，pmem gzip 可完整解压 |
| 旧 checkpoint 数据语义 | 失败，根因已定位 | 191,681 条 SLC line 与 pmem 不一致 |
| 旧 checkpoint 非破坏性修复 | 已完成 | SLC overlay 后 mismatch 为 `{}`，gzip 检查通过 |
| SLC-overlay checkpoint 恢复 | 已结束；证明仍不完整 | 越过 3.626G tick，但首次 MTIP 的指令跟踪暴露恢复栈内容错误 |
| restore 后 terminal 输入 | **最终已通过** | v9 返回 terminal accepted、uname、CPU=16、online=0-15、uptime、PID 与 helper SHA；旧 artifact 的失败仅作历史取证 |
| 15 条 idle 栈 line 诊断修复 | 部分成功 | 13 个 hart 可清 MTIP；CPU10/12/13 仍异常，证明旧 checkpoint 不止缺这 15 条 line |
| 第一轮 ordered-writeback checkpoint 生成 | 已完成 | guest helper 在 tick `65437211256` 正常触发并退出 |
| 第一轮新 checkpoint 文件/CHI 审计 | 文件通过、语义失败 | gzip/hash/空 SLC/SF 均通过；15 个 idle hart 的关键栈为 **0/15** 通过 |
| checkpoint writeback 顺序问题 | 已定位并修复 | 旧顺序 `L1→L2→SLC` 会被 stale SLC 反向覆盖；第二轮日志证明已实际变为 `SLC→L1→L2` |
| 第二轮 SLC-first cold run | guest 路径正常、宿主会话截断 | 到 `[0.026816] clk: Disabling unused clocks` 无 crash；3 小时边界时 PID 被回收，未生成 checkpoint |
| SLC-first 持久 cold 重跑 | 已完成 | Linux 到 `/bin/sh`；terminal 命令通过；tick `94883960936` 正常退出并生成 checkpoint |
| 第二个 SLC-first checkpoint 文件/CHI 审计 | 通过 | gzip/hash/CHI mismatch/空 SLC/SF 均通过；实际 writeback 顺序正确 |
| 第二个 SLC-first checkpoint 栈语义 | **失败** | 15 个 idle hart 仍为 **0/15**；CPU1/6 页表 walk 甚至 unmapped，禁止直接 restore |
| dirty-only 根因 | 已定位并修复代码 | checkpoint 已改为遍历所有 valid SLC/classic-cache line，而不是只依赖协议 dirty/sticky 位 |
| all-valid 构建与聚焦测试 | 已通过但端到端假设被证伪 | 旧 `174/174` 与排序 probe 通过；第三轮证明 all-valid 任意副本最后覆盖仍不安全 |
| 第三轮 all-valid cold run | **cold/shell/checkpoint 通过，栈语义失败** | Linux、terminal、helper 均通过；生成 `cpt.67032562072`；gzip/CHI PASS，但 idle 栈 `0/15` |
| 第三轮 DTLB/pmem 交叉审计 | **已定位根因** | 15 个 idle CPU 的 DTLB 映射与 pmem 页表系统性冲突；pmem 出现 3/4-hart 成组栈页别名 |
| phased-owner 修复 | 代码、构建、聚焦测试通过 | 每层按 shared/former-owner→clean-Writable→Dirty 全局展开；`gem5.opt` 成功；`176/176`；真实 phase probe PASS |
| 第四轮 phased-owner cold run | **cold/shell/terminal/checkpoint 通过，栈语义失败** | 生成 `cpt.67004810444`；gzip/CHI PASS；idle 栈 `0/15`；restore gate 正确拒绝 |
| 跨层 lower-to-upper 修复 | **已被更深入的源码/日志证据证伪** | 顺序本身已生效，但 functional write 会先通过 CoherentXBar 改写 dirty peer；第五轮于 20:12 主动中止，无 checkpoint |
| direct-pmem + hierarchy-wide owner 修复 | **代码、构建、探针和测试通过** | phase probe PASS；真实 Dirty L1D checkpoint 显示 `direct-pmem>0`、`routed-nonmem=0`；`176/176` PASS |
| v7 direct-pmem-owner cold run | **通过** | 16 核 Linux、`/bin/sh`、terminal 与 helper 均通过；生成 `cpt.67414051464` |
| v7 cold checkpoint 最终语义 | **通过** | direct-pmem/gzip/CHI PASS；精确 idle 栈 `15/15`；checkpoint 与验收输入 SHA 均冻结并复核 |
| 新 checkpoint 最终恢复 | **通过** | v9 从 `cpt.67414051464` 恢复，无 NULL/Oops/panic/PMP storm，gem5 exit code 0 |
| 最终 restore 后 terminal 命令 | **通过** | guest 真实返回 uname、CPU count=16、online=0-15、uptime、PID=1、helper SHA |
| restore 后第二 checkpoint | **通过** | guest 生成 `cpt.97174117152`，正常 checkpoint exit |
| 第二 checkpoint 最终语义 | **通过** | direct-pmem/gzip/CHI/15-of-15 全 PASS，`final_acceptance.status=PASS` |

## 3. Linux 到底已经跑到哪里

### 3.1 已经确认跑通的 Linux 路径

已完成的 cold run artifact：

```text
m5out/linux-16core-128mb-drainfix-native-cold-20260815/
```

关键启动进度：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
[    0.020703] printk: legacy console [hvc0] enabled
[    0.038617] Run /bin/sh as init process
```

随后通过 gem5 terminal 端口执行了真实 guest 命令：

```text
DRAINFIX_COLD_GUEST_SHELL_20260815
DRAINFIX_HELPER_SHA256=6a5d0cc57f0c791e4cb98413a0ffc23494ba6914db84caf3eb09d276ce67ff13
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP ... riscv64 GNU/Linux
DRAINFIX_COLD_CPU_COUNT=16
DRAINFIX_COLD_UPTIME=0.05 0.72
DRAINFIX_COLD_TRIGGER_CHECKPOINT_20260815
Writing checkpoint
Exiting @ tick 63968479988 because checkpoint
```

这组输出证明以下能力都至少在 cold boot 场景真实工作过：

- Linux 内核入口和 MMU 切换；
- 16 hart SBI/HSM bring-up；
- CLINT timer 和 IPI；
- O3 WFI suspend/wakeup；
- CHI RN-F、router、HN-F、SLC 和 memory 数据通路；
- hvc0/SBI console 输出；
- host terminal socket 到 guest shell 的输入；
- guest 执行 m5 checkpoint pseudo-op。

### 3.2 最终 Linux 位置与当前运行状态

最终 cold 路径已经完整越过所有早期和 late-init printk，位置为：

```text
[    0.004307] smp: Brought up 1 node, 16 CPUs
[    0.038540] Freeing unused kernel image (initmem) memory: 4112K
[    0.038616] Run /bin/sh as init process
```

随后 cold shell 执行命令并在 tick `67414051464` 生成 checkpoint。v9 从该 tick 恢复后，Linux 继续运行到同一个 BusyBox shell，实际返回 uname、16 CPU、online `0-15`、uptime `0.08 1.22`、shell PID 1 和 helper SHA，最后在 tick `97174117152` 生成第二 checkpoint。

第二 checkpoint 中，CPU8 位于 guest checkpoint helper continuation `PC=0x100b8`，其余 15 个 hart 停在：

```text
PC       = 0xffffffff8046f112  # arch_cpu_idle+0x10
live RA  = 0xffffffff80470480  # default_idle_call+0x18
live s0  = sp+16
saved s0 = sp+32
saved RA = live RA
```

15 个 idle hart 的 serialized DTLB 与 raw pmem Sv48 page walk 逐核一致，审计为 15/15。当前宿主上没有仍在“卡住”的验收 gem5：v9 已以 `because checkpoint` 正常退出，最后状态是完整且已审计的第二 checkpoint artifact，而不是某条 printk、WFI 或 terminal wait。

## 4. 截至 18:05 的历史运行状态

> 本节用于保留当时的运行取证。路径 B 后续因一次 remote GDB attach 的宿主侧调试器故障而以 SIGSEGV 结束；不要再把本节中的 PID 或“正在运行”理解为当前状态。最新状态见第 15 节。

当前保留两条运行路径。

### 4.1 路径 A：全新 ordered cold run（后备路径，主动暂停）

输出目录：

```text
m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/
```

宿主进程快照：

| 字段 | 值 |
|---|---|
| PID | `3028672` |
| 状态 | `Ts` |
| 含义 | sleeping/stopped；这是主动 `SIGSTOP`，不是 guest 死锁 |
| 启动时间 | 2026-08-19 17:17:54 CST |
| 已消耗 CPU time | `00:14:34` |
| 当前 checkpoint 目录 | 空 |

暂停前最新统计：

```text
simTicks     = 1,688,382,465
simInsts     = 20,583,597
hostSeconds  = 253.60
hostTickRate = 6,657,739 tick/s
```

该路径还没有出现可见的 `Linux version` printk。它被主动暂停，是为了把宿主 CPU 全部让给路径 B，尽快验证已经在 shell 的旧 checkpoint 修复方案。若路径 B 失败，可用 `SIGCONT` 恢复这条从零 cold boot 的权威后备路径。

### 4.2 路径 B：修复后旧 checkpoint 的加速恢复（历史主路径，现已结束）

输出目录：

```text
m5out/linux-16core-128mb-repaired-accelerated-restore-checkpoint-20260819/
```

输入 checkpoint：

```text
m5out/linux-16core-128mb-drainfix-native-consolidated-repair-20260819/
  cpt.63968479988/
```

宿主进程快照：

| 字段 | 值 |
|---|---|
| PID | `3077461` |
| 状态 | `Rs`，正在运行 |
| 宿主 CPU | `94.7%` |
| 启动时间 | 2026-08-19 17:27:31 CST |
| 已消耗 CPU time | `00:35:33` |
| terminal | 端口 `3457` 已连接 |
| checkpoint 输出目录 | 仍为空 |

最新主动 stats dump：

```text
simTicks     = 3,626,144,782
simInsts     = 29,047,066
hostSeconds  = 2,249.11
hostTickRate = 1,612,258 tick/s
```

日志已确认：

```text
Restoring from checkpoint: .../cpt.63968479988
Waiting 120.0 host seconds for restored terminal input at guest tick 63968479988
63968479988: system.terminal: attach terminal 0
Restored terminal wait complete at guest tick 63968479988
**** REAL SIMULATION ****
```

截至快照时间，以下错误模式均未出现：

- `panic`；
- `Oops`；
- `Unable to handle`；
- `PMP access fault`；
- `sbi_trap_error`；
- `fatal`；
- `Program aborted`。

当前只看到普通 `MicroTAGE ... clamping index` warning，没有证据表明它影响 guest 前进性。

## 5. 18:05 时的工作假设：terminal continuation

> 本节记录当时为何把范围收敛到 terminal/CLINT continuation。18:46 的指令级证据已经证明，CLINT 确实送达且 OpenSBI handler 可以运行；真正更早的失败是 `mret` 后读取了未落盘的内核栈。最新判断见第 15 节。

### 5.1 眼前卡点：已经越过首个 timer compare，但 terminal 输入仍未到达 shell

checkpoint 中最早一组 hart 的 `mtimecmp=672629`，CLINT/RTC 频率为 10 MHz，即一个 `mtime` 增量对应 100,000 gem5 tick。下一次自然 timer 的绝对 tick 约为：

```text
672629 × 100000 = 67,262,900,000
```

从恢复点到该 timer 的相对距离：

```text
67,262,900,000 - 63,968,479,988
= 3,294,420,012 tick
```

18:05 快照时已运行：

```text
3,626,144,782 tick
```

因此不是“距离第一个 compare 还有多久”，而是已经越过：

```text
3,626,144,782 - 3,294,420,012
= 331,724,770 tick
```

当前绝对 guest tick 为：

```text
63,968,479,988 + 3,626,144,782
= 67,594,624,770
```

checkpoint 还保存了：

```text
[system.rtc]
rtcTimerInterruptTickOffset=20012
```

`MC146818::unserialize()` 会恢复这个 offset，`startup()` 在 periodic interrupt enable 时从 `curTick()+offset` 重排 event，之后按 100,000 tick 周期调用 `RiscvRTC::handleEvent()`；CLINT 的 `raiseInterruptPin()` 每次把 `mtime` 加一，并在 `mtime >= mtimecmp` 时 post MTIP。因此从静态状态和已运行 tick 看，第一个 compare 理应已经具备触发条件。

terminal 已预先排队以下类别命令：

- shell alive marker；
- mount `/proc`；
- `uname -a`；
- `/proc/cpuinfo` 中的 processor 数量；
- `/sys/devices/system/cpu/online`；
- `/proc/uptime`；
- checkpoint helper SHA-256；
- `sync`；
- 执行 `/tmp/chi_guest_checkpoint_repo`。

原工作假设是：输入准备好后，第一个 restored guest timer interrupt 会唤醒 scheduler/hvc polling，shell 随即消费 Terminal RX。现在第一个预计 compare 已经跨过而没有输出，因此这项假设未被运行结果支持。

### 5.2 已经排除到哪一层

#### 5.2.1 host TCP socket 没有积压

宿主只读 `ss` 快照：

```text
State Recv-Q Send-Q Local Address:Port  Peer Address:Port
ESTAB 0      0      127.0.0.1:3457    127.0.0.1:37854  gem5.opt
ESTAB 0      0      127.0.0.1:37854   127.0.0.1:3457   nc
```

两端 `Recv-Q=0`、`Send-Q=0`。结合命令已经写入 nc 会话，可以确认数据没有卡在 host TCP 队列；gem5 已从 socket 读取这些字节。字节此后可能仍在 `Terminal::rxbuf`，也可能已被 UARTLite/SBI 读取，但目前没有 guest 输出证明具体停在哪一步。

#### 5.2.2 不是 16 个 CPU 全部停在 WFI

最新 per-CPU committed instructions：

| CPU | committedInsts | CPU | committedInsts |
|---:|---:|---:|---:|
| 0 | 1,315,877 | 8 | 1,309,470 |
| 1 | 1,313,267 | 9 | 1,313,620 |
| 2 | 1,313,682 | 10 | 571,877 |
| 3 | 1,013,027 | 11 | 1,311,401 |
| 4 | 1,311,870 | 12 | 11,262,498 |
| 5 | 1,309,822 | 13 | 757,688 |
| 6 | 1,313,809 | 14 | 1,005,763 |
| 7 | 1,312,432 | 15 | 1,310,963 |

16 个 CPU 都提交了指令，CPU12 尤其活跃。guest 不是整体静态睡眠，gem5 也不是 event queue 静止。

#### 5.2.3 旧数据损坏型失败暂未复现

截至相对 3.626G tick，仍没有：

- hart3 `misaligned load`；
- Linux panic/Oops；
- PMP access fault storm；
- gem5 fatal/abort。

因此 SLC-to-pmem overlay 对旧恢复数据损坏是有效的；当前 terminal 无输出是一个更靠后的、独立的 continuation/I/O 问题。

### 5.3 当前待确认的具体环节

按现有证据，优先级如下：

1. **Terminal internal RX → UARTLite polling**：socket 已被读空，但当前 UartLite 是 polling model；需要确认 guest MMIO status/read 是否实际发生，以及 `Terminal::rxbuf` 是否仍有数据。
2. **SBI/hvc input polling**：确认 timer interrupt 后 SBI DBCN/legacy getchar 和 Linux hvc poll work 是否运行。
3. **helper/shell continuation**：checkpoint 在 helper pseudo-op 上退出；恢复后应继续执行 helper 的 `exit(0)`，由 shell `wait` 回收并读取下一条命令。需要确认这条 continuation 是否真实完成。
4. **CLINT MTIP delivery**：RTC event 静态 restore 逻辑看起来完整，但当前未打开 Clint debug，尚无动态日志直接证明 `mtime==mtimecmp` 时的 MTIP post 和 supervisor handler。
5. **剩余 checkpoint 语义**：不排除 CPU/device continuation 还有不会立刻 trap 的语义异常，例如某 hart 在活跃循环中运行，但负责 shell/hvc 的 task 没有重新获得运行机会。

当前已经不应把卡点描述为泛泛的“Linux 卡死”或“还没到 timer”。最精确描述是：

> repaired restore 中 guest 和 16 个 CPU 持续执行，host TCP 输入已被 gem5 收取，首个预计 CLINT compare 已跨过，但 terminal 数据尚未通过 UARTLite/SBI-hvc/shell continuation 形成 guest 输出；因此 helper 没有执行，新 checkpoint 目录仍为空。

### 5.4 当前不能算完成的原因

虽然修复后恢复已经明显好于旧路径，但目前仍然没有：

- 恢复后 guest 对 `echo`/`uname` 的真实回显；
- 当前代码实际生成的 ordered-writeback checkpoint；
- 新 artifact 的一致性审计；
- 从新 artifact 的第二次恢复；
- 第二次恢复后的 terminal 命令证据。

所以“没有报错地跑了 3.626G tick”是很强的正向证据，但还不是最终功能验收。

## 6. 今天新增定位：旧 checkpoint 的 SLC 与 pmem 明确不一致

### 6.1 源 checkpoint

源 artifact：

```text
m5out/linux-16core-128mb-drainfix-native-cold-20260815/
  checkpoints/cpt.63968479988/
```

文件：

| 文件 | 大小 |
|---|---:|
| `m5.cpt` | 97,490,876 bytes |
| `system.physmem.store0.pmem` | 5,550,944 bytes（gzip） |

这个 checkpoint 是从可用 `/bin/sh` 中由 guest helper 主动生成的，因此 CPU/guest continuation 比任意 host tick checkpoint 更可信。但它仍采用旧的 native SLC serialization 语义：SLC data 保存在 `m5.cpt`，pmem 不一定包含同一时刻的最新 line。

### 6.2 直接恢复的失败表现

旧 artifact 直接恢复后出现：

```text
sbi_trap_error: hart3: misaligned load handler failed (error -2)
sbi_trap_error: hart3: mcause=0x0000000000000004 mtval=0xffffffffffffffff
sbi_trap_error: hart3: mepc=0x0000000080007378 ...
```

对应运行最终在：

```text
Exiting @ tick 64483603950 because user interrupt received
```

结束。相对恢复点的窗口上界为：

```text
64,483,603,950 - 63,968,479,988
= 515,123,962 tick
```

也就是说旧直接恢复在不超过约 0.515G 相对 tick 的窗口内已经明确损坏。

### 6.3 数据级审计结果

新工具 `util/consolidate_chi_checkpoint.py` 解析 16 个 HNF backend 后得到：

```text
serialized SLC lines = 299,178
serialized SF lines  = 0
changed SLC lines    = 191,681
changed bytes        = 8,163,370
pmem size            = 134,217,728
```

这意味着：

- 不是只有一两个偶发 dirty line；
- 约 64.1% 的有效 SLC line 与源 pmem 至少有一个字节不同；
- 若恢复端丢弃 SLC 并只保留源 pmem，会大规模丢失 kernel/user/页表/栈等最新状态；
- OpenSBI trap 是数据语义损坏的下游症状，不是一个孤立的 misaligned-load 模型 bug。

### 6.4 非破坏性修复方法

修复工具的策略是：

1. 读取源 `m5.cpt` 中所有有效 SLC line；
2. 解压源 pmem 到内存；
3. 按物理地址将每条有效 SLC line 覆盖到 pmem；
4. 用固定 gzip metadata（空 filename、`mtime=0`）生成确定性压缩文件；
5. 原样复制 `m5.cpt`，不修改源 checkpoint；
6. 写出 `consolidation.json`，记录计数和所有关键 hash；
7. current `SlcSnoopFilter::unserialize()` 验证旧数组后丢弃 saved SLC/SF，从修复后的 pmem 作为唯一真值继续运行。

目的目录：

```text
m5out/linux-16core-128mb-drainfix-native-consolidated-repair-20260819/
  cpt.63968479988/
```

### 6.5 修复 artifact 的完整性

manifest 中的 hash：

```text
source m5.cpt SHA-256:
357beec08d002f095db37ffb78999c549c4ed00167934f806d004940f83fa2b2

output m5.cpt SHA-256:
357beec08d002f095db37ffb78999c549c4ed00167934f806d004940f83fa2b2

source pmem gzip SHA-256:
bd35d140b66b3b52369e06114d33485f0f2fb702ea5687495d693defbe6029f5

output pmem gzip SHA-256:
003a8c3d4a651d6c3671e39b61020421af0367413ab88ed0c0a67ea3d6d5a5d5

output uncompressed pmem SHA-256:
7955d3efdc0268422f77ec6c83399ee7e6c36736c4135b6674171b691a303289
```

检查结果：

```text
gzip -t: PASS
serialized SLC lines: 299178
serialized SF lines: 0
SLC/pmem mismatches by state: {}
git diff --check -- src/python/m5/simulate.py util/consolidate_chi_checkpoint.py: PASS
```

源和目标 `m5.cpt` hash 完全相同，证明修复只作用于目标 pmem 副本，没有篡改 CPU、设备、CLINT 或 SimObject checkpoint 状态。

### 6.6 修复后恢复已经取得的增量证据

修复后主路径已无错运行到相对：

```text
3,626,144,782 tick
```

相对于旧直接恢复的 0.515G 失败窗口上界，已经运行到约 7.04 倍距离，且没有再出现 hart3 misaligned-load trap。这不能单独替代最终 shell 验收，但对根因判断是非常强的 A/B 证据：

```text
同一 CPU/device checkpoint state
同一 gem5 binary
同一 firmware
唯一核心差异：pmem 是否包含 serialized SLC 最新数据
```

## 7. 此前 debug 遇到的 bug、根因和解决方式

下面按依赖关系记录已经遇到的主要问题。很多问题的表面现象相似，都是“Linux 不再输出”，但根因分别位于 O3、classic cache、CHI、设备、中断和 checkpoint 层，不能混为同一个卡死。

### 7.1 Linux `NULL pointer dereference @ 0x4`

**现象**

```text
riscv: base ISA extensions
riscv: ELF capabilities
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000004
Oops [#1]
```

同时 gem5 曾输出：

```text
Tt's should not happen on classic cache,
it may be wrong delay of load wake or missed loadcancel in lsq
```

**根因**

XiangShan O3 load pipeline 在 cache hit/miss 最终确定前 speculative wakeup 消费者。如果 load 后来 miss，已经唤醒或已经离开 issue queue 的消费者必须被完整 cancel/replay。当前 classic-cache + CHI bridge 路径没有完整实现这套 miss cancel 保证，消费者可能使用旧值或尚未返回的数据，最终形成错误指针并访问 `NULL + 4`。

**修复**

- 增加 `EnableLoadSpecWakeup` 参数；
- LSQ 只有开关启用时才走 speculative wake；
- 当前 classic-cache CHI 配置显式关闭该开关；
- load 完成后使用正常 writeback wakeup。

**状态**

已解决。后续多次 cold run 越过原始位置并进入 `/bin/sh`。

### 7.2 `--no-pf` 没有真正关闭 L2 wrapper 默认预取器

**现象/风险**

用户传入 `--no-pf`，但 `L2CacheWrapper` SimObject 类仍带有默认 prefetcher。原代码只是跳过显式 `create_prefetcher()`，没有覆盖类默认值，导致预取仍可能发出请求，而且 virtual-address prefetcher 没有绑定正确 CPU TLB。

**修复**

在 `--no-pf` 时明确执行：

```python
l2_wrapper.prefetcher = NULL
```

**状态**

已解决，当前命令使用 `--no-pf`。

### 7.3 wrong-path / 越界 cacheable load 让 L2 xbar 直接 fatal

**现象**

```text
fatal: Unable to find destination for
[0x880043240:0x880043280]
on system.l2_wrappers03.xbar
```

其他 smoke 还见过 `0xf8` 一类地址。O3 wrong-path 指令即使最后会 squash，也可能已经把物理请求送入 cache/interconnect。

**根因**

L2 xbar 没有 default responder，错误地址在 architectural access fault 形成前就被 BaseXBar 当成模型 fatal。

**修复**

1. 每个 L2 wrapper xbar 增加独立 `BadAddr` default responder；
2. PMP 在 FullSystem 下拒绝不属于 physical memory 的 cacheable 请求；
3. 合法 MMIO uncacheable 请求仍保留；
4. xbar fatal 日志补充 source port、requestor、vaddr、PC、packet 和 tick。

**状态**

已解决为 architectural error 路径，不再因为错误路由直接杀死 gem5。

### 7.4 16 核 D0 拓扑不完整

**问题**

原配置只允许最多 4 CPU，不能表示目标 figure-23.2 D0 结构。

**修复**

当前拓扑实现为：

- 24 router，6×4 mesh；
- 16 RN-F/CPU；
- 16 HN-F，每个含 SLC/SF；
- 4 DDR SN；
- DDR 以 64-byte cache line 交织：`(pa >> 6) & 3`；
- HN-F 使用 CMN 16-HNF XOR hash。

**状态**

已实际启动 16 CPUs，拓扑至少通过 Linux cold boot 验证。

### 7.5 RN-F NodeID 不能直接作为 64-bit sharer mask 位号

**问题**

D0 RN-F NodeID 可高达 `0x230`，直接计算 `1ULL << node_id` 会产生越界位移，破坏 directory sharer 状态。

**修复**

建立双向映射：

```text
真实 RN-F SrcID <-> 紧凑 sharer index [0, 63]
```

SF 内只保存紧凑 index，发 snoop 时映射回真实 SrcID；映射本身也进入 checkpoint 序列化，避免恢复后编号漂移。

**状态**

已解决。

### 7.6 DuelingMonitor 只能表示 64 个实例

**问题**

旧实现以 64-bit one-hot mask 分配 DuelingMonitor ID：

```cpp
id = 1 << numInstances
```

16 核、多 slice、多 replacement/prefetch 结构会超过 64 个实例，出现 shift overflow 或实例归属冲突。

**修复**

- one-hot bit 改为普通实例编号；
- sample/team 归属改用 `std::set<uint32_t>`。

**状态**

已解决，并有相应测试覆盖。

### 7.7 CHI 64-byte write 在 DAT backpressure 下丢第二 beat

**现象**

64-byte line 被拆成两个 32-byte DAT beat。第一拍成功、第二拍 backpressure 后，下游一直等待，transaction 永久不完成。

**根因**

发送续拍只依赖 DBIDResp handler；DBIDResp 只到一次，第二拍失败后不会再有新事件触发重试。

**修复**

每次 pump 主动扫描：

```cpp
txn.hasDbid && txn.nextDataBeat < txn.dataBeats.size()
```

从保存的 `nextDataBeat` 继续发送，不再等待第二个 DBIDResp。

**测试**

`WriteDataBackpressureRetriesWithoutDroppingSecondBeat`。

### 7.8 uncacheable bypass 重发违反 classic retry 协议

**现象**

```text
BaseXBar::Layer::tryTiming:
Assertion waitingForLayer does not already contain src_port failed
```

**根因**

`sendTimingReq()` 返回 false 后，bridge 在每次 pump 都重发同一请求，没有等待 `recvReqRetry()`。这违反 classic port retry contract。

**修复**

- 增加 `memReqBlocked`；
- send false 后设置 blocked；
- blocked 时停止 pump 重发；
- 仅 `memSidePortRecvReqRetry()` 清除 blocked 并重新调度。

**状态**

已解决。

### 7.9 `SCUpgradeFailReq` 被错误当成不可能路径

**现象**

```text
SCUpgradeFailReq is a classic internal failure path and
must not be translated into CHI
```

**根因**

store-conditional upgrade 与 invalidation 竞争时，classic cache 会产生 `SCUpgradeFailReq`。SC 虽已失败，cache 仍可能需要 unique data refill，因此不能 panic，也不能错误返回 SC 成功。

**修复**

映射为“失败 SC + unique refill”：获取数据，保留 SC 失败语义，不提升为成功。

**状态**

已解决。

### 7.10 `cacheResponding` ownership 协调包产生重复 `UpgradeResp`

**现象**

```text
response ... has no route
packet=UpgradeResp [81e21500:81e2153f]
```

**根因**

上层 cache 已承诺返回 writable data 时，classic coherent xbar 仍向下发送 express snoop 以协调 ownership。该 packet 设置 `cacheResponding=true`，xbar 不会为它建立 response route。旧 bridge 却把它翻译成 data-bearing CHI `ReadUnique`，最终产生第二个 `UpgradeResp`，而 xbar 没有对应 route。

**修复**

- `cacheResponding` 协调请求映射为 ownership-only `MakeUnique`；
- 不重复请求 data；
- 不生成第二个 classic response；
- 普通 upgrade 才走 data-bearing promotion。

**状态**

已解决。

### 7.11 WFI 最初接近 no-op，或者会把仍有 store 的 CPU 永久睡死

这一组是 Linux/SBI SMP bring-up 中最复杂的 forward-progress 问题。

#### 7.11.1 WFI 语义不足

修复后 WFI：

- 本地 enabled interrupt 已 pending 时只 quiesce 到下一周期；
- 否则真正 suspend context；
- 标记 `IsQuiesce`、`IsNonSpeculative`、`IsSerializeAfter`、`IsSquashAfter`。

#### 7.11.2 WFI 前 older store 留在 store buffer

典型 OpenSBI 路径：

```text
spin_unlock store
WFI
```

若最后一个线程先 suspend，O3 tick 停止，unlock store 永远不向其他 hart 可见。

修复：WFI 与 fence 一样，必须等 older stores 全部 writeback 后才能 commit。

#### 7.11.3 最后一个 store response 没有重新唤醒 CPU

释放 store-buffer entry 后增加：

```cpp
cpu->wakeCPU();
cpu->activityThisCycle();
```

#### 7.11.4 interrupt pending 时 Fetch 继续消费旧 fetch buffer

cache-resident 短循环可能一直取指，pipeline 不 drain，Commit 无法处理 interrupt。

修复：消费已有 fetch buffer 前先检查 interrupt。

#### 7.11.5 ROB 为空时 commit loop 被跳过

原逻辑在 `commit_width == 0` 时可能跳过 `handleInterrupt()`。

修复：pending interrupt 且 commit width 为 0 时仍强制进入一次 commit loop。

#### 7.11.6 watchdog 把合法 WFI 当成卡死

历史输出：

```text
can't commit inst ... wfi
Commit stage is stucked for more than 40,000 cycles
```

修复：参数化 `CommitStuckCheckCycles`，并排除 suspended context、ROB-head quiesce 和全 pipeline drained 等合法状态，其他真实 forward-progress 问题仍保留检查。

**状态**

已解决到足以让 16 个 hart online、持续 timer/IPI 并进入 shell。

### 7.12 CLINT 没有 RTC，`mtime` 不推进

**根因**

DT 声明 10 MHz timebase，但平台没有将 RTC 接到 CLINT，导致 `mtime` 不变，Linux timer、secondary bring-up 和调度都不可靠。

**修复**

```python
self.rtc = RiscvRTC(frequency="10MHz")
self.lint.int_pin = self.rtc.int_pin
```

加入 RTC 后，初始 `mtimecmp=0` 会立即产生 MTIP，因此默认值改为 `UINT64_MAX`，等待 SBI 显式编程。

**状态**

已解决。当前 checkpoint 中 `mtime/mtimecmp` 和 RTC event offset 也已序列化。主路径已经越过首个预计 compare；当前仍缺 MTIP 动态日志，以及 hvc poll 是否真正消费输入的证据。

### 7.13 UARTLite 没有 terminal RX 能力

**原模型问题**

- TX 直接 `putc(stdout)`；
- 没有连接 `SerialDevice`；
- 没有 RX FIFO；
- status 永远为 0；
- 只接受 1-byte MMIO；
- PIO 范围只有 `0xd`，未覆盖完整 `0x10`。

**修复**

- UartLite 接入 `Terminal`；
- 实现 RX FIFO；
- status 返回 RX valid / TX empty；
- 支持 8-bit 和 32-bit MMIO；
- TX 统一走 terminal；
- PIO size 改为 `0x10`。

Linux 的：

```text
uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
```

目前是已知非致命提示；模型采用 polling，未实现 UART IRQ，实际主 console 是 hvc0/SBI。

### 7.14 Terminal accepted socket 阻塞整个 gem5 event queue

**根因**

accepted socket 实际为 blocking descriptor。`Terminal::data()` 读完当前命令后再次 `read()`，可能等待未来数据，导致 gem5 在 event queue 上下文永久阻塞。

**修复**

- `accept()` 后显式设置 `O_NONBLOCK`；
- `EAGAIN/EWOULDBLOCK` 作为正常 drain 结束；
- 每次通知读取完当前所有 socket chunk；
- drain 后一次性通知 serial interface；
- 新增 `--restore-terminal-wait`，在恢复后冻结 guest tick，先给 host terminal 留出连接和预灌命令时间。

**状态**

cold-run terminal 已真实验证；最终 restore 后 terminal 仍是当前最后几个验收点之一。

### 7.15 XiangShan FS 入口缺少完整 checkpoint 参数和目录管理

**问题**

原 parser/Simulation 路径没有完整支持：

- `--max-checkpoints`；
- `--checkpoint-dir`；
- `-r`；
- `--restore-terminal-wait`。

guest pseudo-op 到达后父目录不存在还会很晚才报 `ENOENT`。

**修复**

- 补齐参数；
- cold run 启动时预创建输出目录；
- restore checkpoint 按 `cpt.<tick>` 数值排序；
- restore 期间生成的新 checkpoint 放到 restore outdir，不污染输入目录。

**状态**

已解决。

### 7.16 任意 host tick checkpoint 不是可靠 Linux continuation

**问题**

host 强制打点可能落在 runnable task、pending IPI、interrupt entry 或 pipeline transient 的任意中间状态，缺少明确 guest continuation。

**解决方案**

从 `/bin/sh` 运行极小静态 RISC-V ELF，主动调用 m5 checkpoint pseudo-op。这样 checkpoint 的软件语义是可解释的：shell 执行 helper，helper 返回点就是恢复 continuation。

本轮新 helper：

```text
/tmp/chi_guest_checkpoint_20260819_stripped
size    = 560 bytes
SHA-256 = 78ae261dd36f943d1644fc143e6efc7e11e388f863bee95e313b8cf1e659d862
```

旧 shell checkpoint 中现有 helper：

```text
/tmp/chi_guest_checkpoint_repo
SHA-256 = 6a5d0cc57f0c791e4cb98413a0ffc23494ba6914db84caf3eb09d276ce67ff13
```

### 7.17 raw-cpt 恢复时 physical memory backing store 尚未建立

**根因**

raw-cpt/gcpt 原本把 backing-store allocation 推迟到 `System::initState()`，但 gem5 checkpoint `loadState()` 更早执行。解压 pmem 时可能拿到空 `pmem` 指针，或者 PhysicalMemory 和 memory object 指向不同 backing。

**修复**

- unserialize gzip 时若 `pmem == nullptr`，立即 mmap backing；
- 所有 memory object 重新指向恢复后的 backing；
- 非 dedup 模式也显式 `setBackingStore()`。

**状态**

已解决，当前修复 artifact 已成功完成 unserialize 并进入 real simulation。

### 7.18 CHI bridge/router/HNF 缺少完整 drain 与 checkpoint invariant

**历史现象**

```text
system.chi_bridges13 cannot restore non-drained CHI RN state:
txns=1 snoops=0 ...
```

**根因**

`PacketPtr`、flit、retry state 和 transient queue 不能安全跨 checkpoint 重建。静默清空会让恢复后的 coherent state 随机损坏。

**修复**

为以下组件增加 drain/serialize invariant：

- Cache2ChiBridge / RN；
- Chi2ClassicMemBridge / SN；
- ChiRouterRefModel；
- HNF coherency controller；
- SLC/SF backend。

只有 request/response/retry/compack/snoop queue、txn map、snoop map、promoted-upgrade set 和所有 CHI TX flit queue 全空时才允许 checkpoint。恢复遇到不可重建 transient state 时显式 fatal，拒绝带病继续。

**状态**

机制已实现；新 checkpoint 生成后仍需再次审计 artifact 中所有 size/count。

### 7.19 私有 cache 不序列化，但旧 SF 仍可能指向私有副本

**根因**

classic private cache checkpoint 不保存 tag/data。若恢复时保留旧 SF：

- SF 认为某 RN-F 仍持有 line；
- 实际 L1/L2 已为空；
- HNF 按旧目录发 snoop；
- data/ownership 与目录假设不一致。

**修复方向**

采用 consolidated checkpoint：

1. 所有未序列化 cache 的修改数据按拓扑相关的覆盖优先级合并；
2. SLC lower copy 先写、private owner newer copy 后写，最终都固化到 pmem；
3. SLC/SF/SEQ 以 canonical empty 语义保存/恢复；
4. restore 后 pmem 是唯一真值。

对于历史 native checkpoint，先用本轮 overlay 工具把 saved SLC data 合并进 pmem，再让当前 restore policy 丢弃 saved SLC/SF。

### 7.20 只看 protocol `DirtyBit` 会漏掉曾经修改过的数据

**根因**

MOESI ownership 可以迁移。一个 cache block 曾经持有最新修改值，之后 protocol `DirtyBit` 被清除，并不保证该值已经到达 physical memory。classic checkpoint 又不保存 cache data，因此仅写回当前 `DirtyBit` 会丢数据。

**修复**

给 `CacheBlk` 增加 sticky `checkpointDirty`：

- protocol DirtyBit 被设置时同时设置；
- ownership 状态变化不清除；
- checkpoint functional writeback 成功或 block invalidation 后才清除。

**证据**

旧 cold run 中每个 L2 slice 都实际写回了数十到上百条“protocol DirtyBit 已清、但 checkpointDirty 仍为真”的 line，证明该问题是实际发生的，不只是理论风险。

### 7.21 consolidated cache writeback 不能只按 architectural level 排序

这一项经历了两轮认识，必须把历史假设和当前结论分开。

**第一轮假设（对普通 classic 层级成立）**

最初顺序近似：

```text
SLC -> L2 -> L1
```

在纯 classic hierarchy 中，上层 functional write 可能只更新 resident downstream block，因此通常需要：

```text
L1 -> L2 -> LLC -> pmem
```

基于这个假设，第一轮修复按 `cache_level` 升序执行：

```text
L1(1) -> L2(2) -> SLC(3)
```

**第一轮新 checkpoint 暴露的拓扑差异**

当前 SLC 不是 classic cache，而是 CHI HN-F 内的独立模型。`Cache2ChiBridge::cacheRecvFunctional()` 不把 functional packet 注入 CHI，而是经 `memPort` 直接送到 classic membus；因此：

```text
L2 functional write -> pmem（绕过 modeled SLC）
SLC memWriteback     -> pmem（直接 physicalAccess）
```

在这个具体拓扑中，简单的 `L1 -> L2 -> SLC` 会让 SLC 的 stale lower copy 最后覆盖 L2 已写入的 newer owner copy。第一轮新 checkpoint 的 15 个 idle 栈正是 0/15 通过。

**第二轮修复（当前）**

新增独立的 `checkpoint_writeback_priority`，不再用 architectural cache level 表达数据覆盖优先级：

```text
SLC priority -1 -> L1 level 1 -> L2 level 2
```

这样 SLC 先提交可能陈旧的 lower copy，private owner 的新数据随后覆盖并最终由 L2 直达 pmem。

**状态**

第一轮错误顺序的 artifact 已生成并完成逐核失败审计；第二轮代码已重编译，SLC-first cold run 正在执行。最终判断以第二轮相同工具的 15/15 栈审计和 restore 结果为准。完整证据见第 17 节。

### 7.22 旧 native checkpoint 不能直接套用 canonical-empty restore policy

**问题**

旧 native checkpoint 的最新数据大量仍在 serialized SLC 中；当前 restore 为避免 stale SF/private-cache 问题，会验证后丢弃 SLC/SF。二者直接组合会把 191,681 条与 pmem 不同的 line 丢掉。

**修复**

新增 `util/consolidate_chi_checkpoint.py`，先把所有 saved SLC line overlay 到 pmem 的非破坏性副本，再恢复。

**状态**

数据审计通过，恢复已越过旧失败窗口和首个预计 timer compare；当前运行时验证卡在 gem5 已读取 host 输入、但 guest 尚无 terminal 输出。

### 7.23 Remote GDB CSR mask 和 attach 稳定性

**问题**

RemoteGDB 对 `CSRMasks` 使用 `map::at()`，但 SIE、MSTATUS、MIP 等并非都存在显式 mask，读取通用寄存器包时可能抛异常。

**修复**

未列出的 CSR 默认使用全位 mask。

**剩余风险**

曾有一次 GDB attach 扰动后宿主 gem5 segfault。最终验收不再依赖 GDB，只使用 terminal 和日志；GDB 稳定性不是当前闭环的必要条件。

## 8. 关键调试时间线

| 阶段 | 表面现象 | 定位结果 | 处理后推进 |
|---|---|---|---|
| Linux very early boot | `NULL + 4` Oops | load speculative wakeup 与 classic-cache miss cancel 不闭合 | 越过原 Oops |
| L2 request routing | xbar `Unable to find destination` | wrong-path/坏地址无 default responder | 转为 architecture access error |
| 16 核扩展 | NodeID/sharer、dueling overflow | 4 核假设和 64-bit one-hot 假设失效 | 16 核拓扑可运行 |
| CHI write | 第二 DAT beat 丢失 | backpressure 后没有主动续发 | multi-beat write 前进 |
| classic bypass | xbar retry assertion | send false 后违规重复发送 | retry contract 闭合 |
| SC/upgrade | panic 或重复 response | CHI 映射不符合 classic 内部语义 | unique/ownership 路径闭合 |
| SMP/WFI | hart 不前进或 watchdog | store drain、CPU wake、fetch/commit interrupt 顺序 | 16 CPUs online |
| timer | `mtime` 不动 | CLINT 未接 RTC | Linux timer/IPI 工作 |
| terminal | 输入后 gem5 静止 | accepted socket 阻塞 event queue | cold shell 命令可执行 |
| raw restore | pmem 指针/映射错误 | loadState 早于 initState | pmem 可恢复 |
| CHI checkpoint | restore 有 transient txn | PacketPtr/flit 不可重建 | drain invariant 显式化 |
| consolidated checkpoint | restore PMP/trap storm | dirty tracking不足 + 写回顺序反 | sticky dirty + upper-to-lower order |
| old native checkpoint | hart3 SBI misaligned trap | saved SLC 与 pmem 大规模不一致、restore 又丢 SLC | overlay 191,681 条 line 后已越过旧失败窗口 |
| 当前 | terminal 尚无 guest 回显 | TCP 已读空、16 CPU 活跃、已跨首个 compare；问题位于 Terminal RX 到 shell continuation | 需加动态计数/trace 确认 UARTLite poll、SBI/hvc、MTIP 和 helper return |

## 9. 构建、测试和工作区状态

### 9.1 当前关键二进制和输入

```text
build/RISCV/gem5.opt
size    = 931,168,672 bytes
SHA-256 = 61939ecea1727f8d57494f44be2bb662e0307d5e47b4be6b4accda6349124a24

/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin
size    = 19,264,520 bytes
SHA-256 = 0642b2626cd26f281ac99ba0d547a5f3cc4ca6fc45c27a40f446218eb88f904f
```

firmware command line 包含：

```text
console=hvc0 earlycon=sbi loglevel=8 rdinit=/bin/sh \
initcall_blacklist=check_unaligned_access_all_cpus
```

`initcall_blacklist` 用于绕过 Linux 6.10 跨 CPU unaligned-access capability probe；当前完成条件针对这份明确 firmware。移除 blacklist 的纯净内核验证属于额外工作，不在本轮最终闭环的最低验收条件内。

### 9.2 最近一次完整聚焦单元测试

8 月 17 日最后一次完整聚焦测试记录：

| 测试组 | 结果 |
|---|---:|
| HnfSLCSFBackend | 24/24 |
| HnfSLCSF | 131/131 |
| SlcSnoopFilter | 17/17 |
| HnfCoherencyController | 62/62 |
| Cache2ChiBridge | 22/22 |
| CacheBlk checkpoint dirty | 2/2 |
| 合计 | **258/258** |

death test 中出现的 `panic/fatal` 是故意验证非法状态会被拒绝，不是测试失败。

### 9.3 本轮新增检查

| 检查 | 结果 |
|---|---:|
| repaired pmem `gzip -t` | PASS |
| repaired SLC/pmem mismatch | `{}` |
| serialized SLC count | 299,178 |
| serialized SF count | 0 |
| `git diff --check`（ordered writeback + consolidation utility） | PASS |

### 9.4 工作区说明

当前工作区是有意保留的调试工作树：

- 56 个 tracked 文件有修改；
- tracked diff 约 `+2087/-281`；
- `util/consolidate_chi_checkpoint.py`、checkpoint helper、调试脚本和部分测试文件尚未跟踪；
- 尚未创建最终 commit；
- 报告没有清理、reset 或覆盖任何既有修改。

单元测试和静态检查不能替代 full-system checkpoint/restore/terminal 闭环。

## 10. 当前仍存在但不是 blocker 的 warning

以下 warning 目前尚未证明会导致当前失败：

- `MicroTAGE ... block [0,0) ... clamping index`；
- `write to misc_reg ... but now write 0`；
- `enable SV48`；
- `No config for opClass: IprAccess/InstPrefetch`；
- `Difftest is disabled`；
- stats bucket rounding；
- UARTLite `IRQ index 0 not found`。

审计时必须把这些 warning 与以下真正失败信号分开：

```text
panic
fatal
Oops
Unable to handle
PMP access fault
sbi_trap_error
Program aborted
```

## 11. 后续必须执行的步骤

### 11.1 完成当前修复后恢复

1. 给 Terminal/UartLite 增加只读诊断计数或定向 debug：socket 收到字节数、`rxbuf` depth、UARTLite status/read 次数和取出的字符数；
2. 给 RTC/CLINT 开启或增加定向日志：恢复后的 `mtime`、`mtimecmp`、MTIP post/clear 和目标 hart；
3. 取得活跃 CPU（尤其 CPU12）以及原 helper/shell hart 的 PC/privilege/interrupt 状态，确认是正常 idle/scheduler 路径还是异常循环；
4. 在不改变 guest tick 语义的前提下重新验证一条短 terminal marker；
5. 一旦 shell 回应，立即取得 `uname`、CPU_COUNT、online、uptime 和 helper SHA-256；
6. 让 `/tmp/chi_guest_checkpoint_repo` 触发 checkpoint。

当前应针对 `Terminal RX → UARTLite → SBI/hvc → shell` 分层取证，而不是继续把它笼统描述为“Linux 卡住”或无限等待同一个已跨过的 timer compare。

### 11.2 审计新 checkpoint

新 artifact 必须满足：

- `m5.cpt` 和 pmem 文件存在且大小合理；
- pmem `gzip -t` 通过；
- 记录压缩和解压后 SHA-256；
- 日志显示 private/upper cache writeback 在 SLC writeback 之前；
- 16 个 Cache2ChiBridge 的 request/response/retry/txn/snoop/flit state 全为空；
- 16 个 HNF 的 SLC/SF/SEQ 为 canonical empty；
- checkpoint 退出原因是 `checkpoint`，不是 signal/fatal；
- checkpoint 前 guest `CPU_COUNT=16`、online 为 `0-15`。

### 11.3 从新 checkpoint 做最终恢复

用相同 16 核配置和新的输出目录执行 `-r 1`，terminal 连接后至少执行：

```sh
echo FINAL_RESTORE_TERMINAL_OK_20260819
uname -a
printf 'FINAL_RESTORE_CPU_COUNT='; grep -c '^processor' /proc/cpuinfo
printf 'FINAL_RESTORE_CPU_ONLINE='; cat /sys/devices/system/cpu/online
cat /proc/uptime
```

最终成功标准：

- 无 Linux panic/Oops；
- 无 SBI trap；
- 无 PMP fault storm；
- 无 gem5 fatal/abort；
- CPU count 为 16；
- online 为 `0-15`；
- 至少两条命令有真实 guest 输出；
- 输出不是 nc 的本地 tty echo。

## 12. 关键运行命令

### 12.1 当前后备 cold run

```bash
build/RISCV/gem5.opt \
  --listener-mode=on \
  --redirect-stdout \
  --redirect-stderr \
  --outdir=m5out/linux-16core-128mb-consolidated-ordered-cold-20260819 \
  configs/example/kmhv2_chi_6x4_hnf.py \
  --raw-cpt \
  --generic-rv-cpt=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin \
  --num-cpus=16 \
  --mem-size=128MB \
  --mem-type=SimpleMemory \
  --maxinsts=0 \
  -m 120000000000 \
  --no-pf \
  --max-checkpoints=1 \
  --checkpoint-dir=m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/checkpoints
```

### 12.2 当前 repaired accelerated restore

```bash
build/RISCV/gem5.opt \
  --listener-mode=on \
  --redirect-stdout \
  --redirect-stderr \
  --outdir=m5out/linux-16core-128mb-repaired-accelerated-restore-checkpoint-20260819 \
  configs/example/kmhv2_chi_6x4_hnf.py \
  --raw-cpt \
  --generic-rv-cpt=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin \
  --num-cpus=16 \
  --mem-size=128MB \
  --mem-type=SimpleMemory \
  --maxinsts=0 \
  -m 120000000000 \
  --no-pf \
  -r 1 \
  --max-checkpoints=1 \
  --checkpoint-dir=m5out/linux-16core-128mb-drainfix-native-consolidated-repair-20260819 \
  --restore-terminal-wait=120
```

## 13. 关键 artifact 索引

| 用途 | 路径 |
|---|---|
| 上一版完整报告 | `linux_checkpoint_debug_report_20260817.md` |
| 已跑通 shell 的 cold log | `m5out/linux-16core-128mb-drainfix-native-cold-20260815/simout` |
| 源 native checkpoint | `m5out/linux-16core-128mb-drainfix-native-cold-20260815/checkpoints/cpt.63968479988` |
| 旧直接 restore 失败 log | `m5out/linux-16core-128mb-drainfix-native-restore-final-20260815/simout` |
| repaired checkpoint | `m5out/linux-16core-128mb-drainfix-native-consolidated-repair-20260819/cpt.63968479988` |
| repair manifest | `m5out/linux-16core-128mb-drainfix-native-consolidated-repair-20260819/cpt.63968479988/consolidation.json` |
| 当前 accelerated restore log | `m5out/linux-16core-128mb-repaired-accelerated-restore-checkpoint-20260819/simout` |
| 当前 accelerated restore error log | `m5out/linux-16core-128mb-repaired-accelerated-restore-checkpoint-20260819/simerr` |
| 当前 accelerated restore stats | `m5out/linux-16core-128mb-repaired-accelerated-restore-checkpoint-20260819/stats.txt` |
| 后备 ordered cold run | `m5out/linux-16core-128mb-consolidated-ordered-cold-20260819` |
| SLC/pmem 审计工具 | `util/debug_chi_checkpoint.py` |
| checkpoint consolidation 工具 | `util/consolidate_chi_checkpoint.py` |
| ordered writeback 实现 | `src/python/m5/simulate.py` |

## 14. 最终状态判断

### 已确认解决

- 原始 Linux NULL dereference；
- 16 核拓扑和 SMP bring-up；
- 主要 CHI request/write/retry/SC/upgrade 前进性问题；
- WFI/store/interrupt 前进性；
- CLINT RTC；
- cold-run terminal 输入；
- raw pmem backing restore；
- CHI transient drain invariant；
- checkpoint sticky dirty tracking；
- consolidated writeback 顺序代码；
- 历史 native checkpoint 的 SLC-to-pmem repair 方法。

### 当前正在验证

- restored RTC/CLINT 的 MTIP 是否真实投递并进入 guest handler；
- gem5 已读取的 terminal 字节是否仍在 RX buffer，以及 UARTLite/SBI-hvc 是否读取；
- helper pseudo-op 后的 `exit(0)`、shell `wait/read` continuation 是否恢复；
- guest helper 是否能在当前代码下生成 ordered-writeback checkpoint。

### 尚未完成

- 新 checkpoint artifact 审计；
- 从新 checkpoint 再恢复；
- 最终 restore 后真实 terminal 命令输出。

18:05 时最准确的阶段性结论是：

> Linux 本身已经跑通到 16 核 `/bin/sh`。旧 checkpoint 的直接恢复失败最初收敛为 serialized SLC 未合并进 pmem，并完成了非破坏性 overlay；但第 15 节的新证据表明 overlay 仍会漏掉从未到达 SLC 的 private/store 状态。因此这里的“terminal 路径是唯一剩余卡点”已经被后续证据推翻。最终 checkpoint→restore→terminal 闭环仍未完成。

## 15. 18:46 最新增量：CLINT 已证实正常，真正卡在未落盘的内核栈和其他 private/store 状态

本节是本报告的最新结论，优先级高于第 4、5、14 节的历史快照。

### 15.1 最新结论摘要

当前最精确的判断是：

> 修复后的旧 checkpoint 不是卡在 terminal socket，也不是卡在 CLINT 没有发中断。CLINT 已向全部 16 个 hart 投递 MTIP，OpenSBI machine-timer handler 也能运行。第一个确定性 guest 失败发生在 handler `mret` 返回 Linux 后：idle hart 从未正确落盘的内核栈中恢复出错误返回地址，跳到非法地址并进入异常/Oops 路径。

这解释了之前看似矛盾的现象：

- 16 个 CPU 都有 committed instructions；
- 仿真能运行数十亿 tick；
- host TCP 输入已被 gem5 收取；
- 没有立即出现早期 Linux printk；
- 但 shell 永远不消费命令。

CPU 并不是健康地执行 shell/scheduler，而是在错误的 trap/exception continuation 中持续执行或反复 fault。

### 15.2 动态证据一：RTC event 从恢复后立即按周期运行

自然 `mtime` 的诊断输出目录：

```text
m5out/linux-16core-128mb-repaired-terminal-clint-diag-20260819/
```

启用：

```text
TerminalVerbose,Clint,MC146818
```

恢复点：

```text
63,968,479,988
```

动态日志证明：

- 第一个 RTC event 在恢复后 `20,012` tick 到达；
- 此后每 `100,000` tick 到达一次；
- 与 checkpoint 中的 `rtcTimerInterruptTickOffset=20012` 和 10 MHz `mtime` 完全一致。

因此以下假设已排除：

- RTC event 没有反序列化；
- event 被排到过去；
- event queue 恢复后静止；
- `mtime` 完全不再增长。

### 15.3 动态证据二：timerprobe 只改一行 `mtime`，MTIP 确实送达

为了避免等待自然 compare，又制作了一份严格隔离的诊断副本：

```text
m5out/linux-16core-128mb-drainfix-native-consolidated-repair-timerprobe-20260819/
  cpt.63968479988/
```

该副本只做一处改动：

```diff
-mtime=639684
+mtime=672628
```

所有 `mtimecmp` 保持：

```text
CPU 2/3/4/7/8/9/10/11/12/13/14/15 = 672629
CPU 0/1/5/6                         = 672630
```

完整性信息：

| 项目 | SHA-256 |
|---|---|
| 原 repaired `m5.cpt` | `357beec08d002f095db37ffb78999c549c4ed00167934f806d004940f83fa2b2` |
| timerprobe `m5.cpt` | `81f900100c62be88fc8de2c27b354102b64dbfceb048b4dbe5826852aac8b601` |
| pmem gzip | 仍为 `003a8c3d4a651d6c3671e39b61020421af0367413ab88ed0c0a67ea3d6d5a5d5` |

`diff -U 1` 已确认除 `mtime` 外没有第二处 `m5.cpt` 改动。该 artifact 只用于定位，不能用于最终验收。

对应运行：

```text
m5out/linux-16core-128mb-repaired-timerprobe-restore-diag-20260819/
```

结果：

```text
63968500000: CPU 2/3/4/7/8/9/10/11/12/13/14/15 MTIP posted
63968600000: CPU 0/1/5/6 MTIP posted
```

所以“terminal 没输出是因为第一个 CLINT interrupt 没到”已经被直接动态证据否定。

### 15.4 高强度 O3 trace 的 SIGSEGV 不是 guest Linux 崩溃

首次尝试启用：

```text
Clint,Interrupt,O3CPU,Commit,Fetch,Quiesce,TerminalVerbose,Faults
```

输出目录：

```text
m5out/linux-16core-128mb-repaired-timerprobe-interrupt-diag2-20260819/
```

trace 最后显示：

```text
63968500005: system.cpu02.commit: Interrupt detected.
63968500005: system.cpu02: Interrupt interrupt being handled
63968500005: system.cpu02: Fault (interrupt) at PC:
               (0xffffffff8046f112=>0xffffffff8046f116).(0=>1)
63968500005: system.cpu02.commit: Generating trap event for [tid:0]
```

随后宿主 gem5 SIGSEGV。为了判断这是不是 trap 处理本身的真实缺陷，使用最小 flags 重跑：

```text
Clint,Interrupt,Faults,Quiesce,TerminalVerbose
```

输出目录：

```text
m5out/linux-16core-128mb-repaired-timerprobe-minimal-interrupt-diag-20260819/
```

最小版本没有 SIGSEGV，并继续运行：

```text
simTicks = 65,882,932
simInsts = 402,165
```

16 个 CPU 都成功进入第一次 interrupt fault，并继续提交指令。因此高强度 trace 的 SIGSEGV 属于调试组合/宿主路径现象，不是“CPU2 一进入 trap，正常 gem5 就必然崩溃”。

另外一次 remote GDB attach 也曾令 gem5 SIGSEGV，且 `gdb-multiarch` 自身报 `Recursive internal problem`。后续不再使用 remote GDB 作为取证手段。

### 15.5 指令级证据：CPU2 能完整运行 OpenSBI timer handler

CPU2 单核指令跟踪：

```text
m5out/linux-16core-128mb-repaired-timerprobe-cpu02-exec-long-diag-20260819/
  cpu02-exec.trace
```

起始 guest 状态：

```text
PC = 0xffffffff8046f112  arch_cpu_idle
SP = 0xffff8f800011be60
RA = 0xffffffff80470480  default_idle_call+0x18
S0 = 0xffff8f800011be70
```

MTIP 后，CPU2 进入 OpenSBI trap vector：

```text
0x80000400  csrrw tp, mscratch, tp
...
0x800034f6  csrrs s2, mcause, zero
             mcause = 0x8000000000000007
```

即 machine timer interrupt，cause 7。

随后 handler：

```text
0x80002ad4  csrrc zero, mie, a5   # 清 MTIE
0x80002ae8  csrrs zero, mip, a5   # 置 supervisor pending
...
0x800004f8  csrrw zero, mepc, t0  # 恢复 mepc=arch_cpu_idle
0x80000500  csrrw zero, mstatus, t0
0x8000050a  mret
```

这说明以下路径都正常执行：

1. gem5 CLINT 比较 `mtime/mtimecmp`；
2. CPU interrupt controller 接收 MTIP；
3. O3 commit 检测 interrupt；
4. RISC-V fault entry 更新 CSR/PC；
5. OpenSBI 保存 trap frame；
6. OpenSBI 识别 machine timer cause；
7. 清 MTIE、置 STIP；
8. 恢复寄存器并执行 `mret`。

因此当前根因不在 CLINT，也不在 OpenSBI timer handler 的入口。

### 15.6 第一个确定性错误：`mret` 后从坏栈恢复 `ra=0x3`

`mret` 后 CPU2 返回：

```text
0xffffffff8046f112  c_ldsp ra, 8(sp)
                     D=0x0000000000000003
                     A=0xffff8f800011be68
0xffffffff8046f114  c_ldsp s0, 0(sp)
                     D=0x0000000000000000
0xffffffff8046f116  c_addi sp, 16
0xffffffff8046f118  c_jr ra
```

下一条 fault：

```text
Fault (Address) at PC: (0x2=>0x6).(0=>1)
```

RISC-V compressed `c_jr` 会清除地址 bit 0，所以 `ra=0x3` 实际跳到 `0x2`。这不是预测器误预测，也不是 UART 没轮询，而是 architectural control flow 已经被坏栈直接破坏。

符号解析：

| 地址 | 符号 |
|---|---|
| `0xffffffff8046f102` | `arch_cpu_idle` |
| `0xffffffff80470468` | `default_idle_call` |
| `0xffffffff80470480` | `default_idle_call+0x18`，即 `arch_cpu_idle` 的正确返回点 |
| `0xffffffff804773e6` | `_raw_spin_lock_irqsave` |

错误跳转后 Linux 进入 exception/Oops 路径，最终大量 CPU 在 `_raw_spin_lock_irqsave` 附近循环，解释了“CPU 持续提交很多指令但没有 shell 输出”。

### 15.7 页表 walk：错误值确实来自 checkpoint pmem，且该 line 不在 SLC

CPU2 `satp`：

```text
0x900000000008126a  # Sv48
```

对虚拟地址：

```text
0xffff8f800011be68
```

walk 结果：

```text
L3 pte @ 0x8126a8f8 = 0x0000000020500401
L2 pte @ 0x81401000 = 0x0000000020525401
L1 pte @ 0x81495000 = 0x0000000020525801
L0 pte @ 0x814968d8 = 0x00000000205638e7
physical                = 0x8158ee68
physical cache line     = 0x8158ee40
```

读取结果：

```text
pmem value = 0x0000000000000003
SLC line   = absent
```

这条 line 不在 299,178 条 serialized SLC line 中，所以 SLC overlay 工具无法修复它。overlay 前后读到的都是 `0x3`。

### 15.8 不是 CPU2 单点：15 个 idle hart 的栈 line 系统性缺失

checkpoint 中 15 个 hart 停在同一条 `arch_cpu_idle` 指令；CPU10 例外，它停在 guest helper pseudo-op `PC=0x100b8`。

15 个 idle hart 的关键寄存器模式：

```text
PC = 0xffffffff8046f112
RA = 0xffffffff80470480
S0 = SP + 0x10
```

物理栈 line：

| CPU | SP | physical line | SLC |
|---:|---|---|---|
| 0 | `0xffff8f800010be60` | `0x81584e40` | absent |
| 1 | `0xffff8f8000113e60` | `0x8158ae40` | absent |
| 2 | `0xffff8f800011be60` | `0x8158ee40` | absent |
| 3 | `0xffffffff81003e10` | `0x81203e00` | absent |
| 4 | `0xffff8f8000123e60` | `0x81592e40` | absent |
| 5 | `0xffff8f800012be60` | `0x81596e40` | absent |
| 6 | `0xffff8f8000133e60` | `0x8159ae40` | absent |
| 7 | `0xffff8f800013be60` | `0x8159ee40` | absent |
| 8 | `0xffff8f8000143e60` | `0x815c2e40` | absent |
| 9 | `0xffff8f800014be60` | `0x815c6e40` | absent |
| 11 | `0xffff8f800015be60` | `0x815cee40` | absent |
| 12 | `0xffff8f8000163e60` | `0x815d2e40` | absent |
| 13 | `0xffff8f800016be60` | `0x815d6e40` | absent |
| 14 | `0xffff8f8000173e60` | `0x815dae40` | absent |
| 15 | `0xffff8f800017be60` | `0x815dee40` | absent |

以 CPU2 为例，旧 pmem line：

```text
offset +0x20: 0x0000000000000000   # 应为保存的 S0
offset +0x28: 0x0000000000000003   # 应为保存的 RA
offset +0x30: 0xffff8f800011bee0   # 旧 frame pointer
offset +0x38: 0xffffffff8000820a   # 旧 return PC
```

同一 deterministic boot 的更早 checkpoint 中：

```text
offset +0x20: 0xffff8f800011be80
offset +0x28: 0xffffffff80470480
offset +0x30: 0xffff8f800011bea0
offset +0x38: 0xffffffff800495e4
```

后者同时满足：

- `arch_cpu_idle` prologue 的保存规则；
- checkpoint 中的 live `RA`；
- `default_idle_call` 的实际反汇编；
- 15 个 hart 的同构栈布局。

所有 15 个 hart 都呈现相同模式：当前 pmem 中仍是 early-boot/旧 idle frame，正确 frame 没有落盘。这是系统性 checkpoint 一致性缺陷，不是一个偶发 bit flip。

### 15.9 与 WFI/store buffer 的关系

`arch_cpu_idle` 和 `default_idle_call` 的栈帧会在执行 `WFI` 前写入：

```text
default_idle_call:
  addi sp, sp, -16
  sd   ra, 8(sp)
  sd   s0, 0(sp)
  ...
  jal  arch_cpu_idle

arch_cpu_idle:
  addi sp, sp, -16
  sd   s0, 0(sp)
  sd   ra, 8(sp)
  ...
  wfi
```

当前 working tree 已增加规则：quiesce/WFI 和 fence 一样，commit 前必须确认 older stores 已离开 LSQ/store buffer；最后一个 store response 也要重新唤醒 CPU。

本次栈证据与“WFI 前 store 没有全部 durable”高度一致。更严格地说，旧 artifact 能直接证明的是：

```text
正确栈数据不在最终 pmem
且不在 serialized SLC
```

它无法仅靠事后文件区分数据最后停在：

- O3 store buffer；
- private L1 D-cache；
- private L2；
- 或 checkpoint writeback 的某个中间时序窗口。

但无论具体停在哪一级，结论相同：旧 checkpoint 在清空/不序列化 private state 前，没有保证所有 architectural store 已变成最终可恢复的 pmem/SLC 数据。

### 15.10 15-line 诊断修复：强烈支持根因，但也证明不止这 15 条

为验证因果关系，制作了不覆盖原件的诊断 artifact：

```text
m5out/linux-16core-128mb-repaired-stackline-timerprobe-diag-20260819/
  cpt.63968479988/
```

只从更早的 deterministic boot 复制上述 15 条、每条 64 bytes 的栈 line；实际变化字节数为：

```text
485 bytes
```

hash：

| 项目 | SHA-256 |
|---|---|
| `m5.cpt` | `81f900100c62be88fc8de2c27b354102b64dbfceb048b4dbe5826852aac8b601` |
| patched pmem gzip | `0c0751d80c174d87d4e25ab3ef72c14deb631b096718db10e50c011ecd7f39bc` |
| patched raw pmem | `b3778ebcfae7c6d6d254f3f2829b60dee1e954c858cc5568cba60ff136f0349c` |

manifest：

```text
m5out/linux-16core-128mb-repaired-stackline-timerprobe-diag-20260819/
  cpt.63968479988/consolidation.json
```

恢复运行：

```text
m5out/linux-16core-128mb-repaired-stackline-timerprobe-restore-diag-20260819/
```

停止时：

```text
simTicks   = 58,891,612
simInsts   = 353,096
exit tick  = 64,027,371,600
```

对照未补栈版本：

- 未补栈：16 个 hart 的 MTIP 长时间全部保持 posted；CPU2 直接从 `arch_cpu_idle` 跳到 `0x2`。
- 补栈后：CPU0–9、CPU11、CPU14、CPU15 共 **13 个 hart** 的 MTIP 已变为 cleared。
- 补栈后：这些 idle hart 能从 `arch_cpu_idle` 返回到 `default_idle_call`/`do_idle`，并继续接收 supervisor timer interrupt。

这是很强的因果证据：补回 idle 栈 line 确实修复了大部分 hart 的 timer continuation。

但该实验仍未通过：

- CPU10 MTIP 仍 posted，并在 Linux exception entry `0xffffffff8047763e/0xffffffff80477648` 附近反复 Address fault；
- CPU13 也进入 exception-entry fault storm；
- CPU12 MTIP 在观测窗口末尾仍 posted；
- 预灌 `echo/uname/CPU_COUNT/online` 仍没有 guest 输出。

因此 15 条 idle 栈 line 是已证实的重要缺失数据，但不是旧 checkpoint 的全部缺失数据。CPU10 当时正在 helper pseudo-op，而不是 idle；它的 user/kernel continuation 很可能还依赖其他没有写回的栈、task 或页表相关 line。

### 15.11 当前卡点应如何表述

不再准确的说法：

- “还没等到第一个 timer”；
- “CLINT 可能没有 post MTIP”；
- “当前只卡在 UARTLite/SBI-hvc polling”；
- “SLC overlay 已经完整修好旧 checkpoint”；
- “跑了数十亿 tick 没有显式 panic，所以 guest 健康”。

当前最准确的说法：

> Linux cold boot 已经完全跑通；旧 checkpoint 恢复时，SLC overlay 修复了 191,681 条 SLC/pmem mismatch，但仍漏掉至少 15 条、实际还不止 15 条从未进入 SLC/pmem 的 active private/store 数据。首次 MTIP 会暴露这些坏状态：OpenSBI handler 正常返回后，Linux 从坏栈恢复错误 PC，进入异常入口 fault storm，因此无法回到 shell 消费 terminal 输入。

### 15.12 后续正确路径

最终不能继续依赖手工修补旧 artifact，因为：

- 不可能从 `m5.cpt + pmem` 唯一重建所有已经被丢弃的 private/store 数据；
- 15-line 修复已经证明还有其他缺失 line；
- 从更早 checkpoint 借数据只能用于因果诊断，不能作为验收依据；
- timerprobe 人工修改了 `mtime`，也不能用于验收。

正确路径是继续保留并恢复从零运行：

```text
PID 3028672
m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/
```

然后严格执行：

1. `SIGCONT` 恢复该 cold run；
2. 等待 Linux 6.10.7 再次进入 `/bin/sh`；
3. terminal 执行健康检查并触发 guest checkpoint helper；
4. checkpoint 前确认 O3 pipeline、LSQ/store buffer、private caches 和 CHI transient 全部 drain；
5. 按 `L1/L2 → SLC → pmem` 顺序 writeback；
6. 审计关键 idle 栈 line 在最终 pmem 中与 live registers/调用约定一致；
7. 审计 CHI queue、SLC/SF/SEQ、gzip 和 hash；
8. 从新 checkpoint 恢复；
9. 等待一次真实 timer interrupt；
10. terminal 获取 `echo`、`uname`、CPU count、online mask、uptime 的真实 guest 输出。

只有第 10 步完成，才能满足最终验收。

### 15.13 最新关键 artifact 索引

| 用途 | 路径 |
|---|---|
| 自然 RTC/CLINT trace | `m5out/linux-16core-128mb-repaired-terminal-clint-diag-20260819/` |
| 只改 `mtime` 的 timerprobe checkpoint | `m5out/linux-16core-128mb-drainfix-native-consolidated-repair-timerprobe-20260819/cpt.63968479988/` |
| timerprobe 基础恢复 trace | `m5out/linux-16core-128mb-repaired-timerprobe-restore-diag-20260819/` |
| 高强度 O3 trace（宿主 SIGSEGV） | `m5out/linux-16core-128mb-repaired-timerprobe-interrupt-diag2-20260819/interrupt-window.trace` |
| 最小 interrupt trace | `m5out/linux-16core-128mb-repaired-timerprobe-minimal-interrupt-diag-20260819/minimal-interrupt.trace` |
| CPU2 指令级 trace | `m5out/linux-16core-128mb-repaired-timerprobe-cpu02-exec-long-diag-20260819/cpu02-exec.trace` |
| 15-line 诊断修复 checkpoint | `m5out/linux-16core-128mb-repaired-stackline-timerprobe-diag-20260819/cpt.63968479988/` |
| 15-line 修复 manifest | `m5out/linux-16core-128mb-repaired-stackline-timerprobe-diag-20260819/cpt.63968479988/consolidation.json` |
| 15-line 修复恢复 trace | `m5out/linux-16core-128mb-repaired-stackline-timerprobe-restore-diag-20260819/stackline-timerprobe.trace` |
| 当前暂停的权威 cold run | `m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/` |

### 15.14 18:46 最终阶段判断

| 项目 | 结论 |
|---|---|
| Linux cold boot | 已通过 |
| 16 CPUs online | 已通过 |
| cold terminal 输入 | 已通过 |
| 原始 `NULL pointer dereference @ 0x4` | 已解决 |
| RTC periodic event restore | 已证明正常 |
| CLINT MTIP post | 已证明正常 |
| OpenSBI machine timer handler | 已证明可完整执行 |
| 旧 checkpoint SLC overlay | 只修复一部分，不完整 |
| 旧 checkpoint idle 栈 | 15 条 line 系统性错误 |
| 15-line 诊断修复 | 13/16 hart 明显恢复，但整体仍失败 |
| restore 后 terminal | 未通过；是 guest 状态损坏的结果 |
| 新 ordered checkpoint | 尚未生成 |
| 新 checkpoint restore | 尚未执行 |
| 最终验收 | **未完成** |

最终结论：

> 目前最深卡点已经从“terminal 无输出”推进到“checkpoint 前仍有 architectural store/private state 未被固化”。旧 artifact 无法可靠补全，必须用当前 drain、WFI store flush、sticky dirty 和有序 writeback 代码重新从零生成 checkpoint，再完成 restore + terminal 验收。

## 16. 18:46–20:22：权威 ordered cold run 恢复执行后的最新进展

本节记录报告初版完成后继续运行的现场结果。它不使用旧 checkpoint，不使用 timerprobe，也不注入诊断栈数据；因此是当前代码从零冷启动的权威基线。

### 16.1 运行身份与连续性

仍然使用同一个先前暂停的进程：

```text
PID     = 3028672
outdir  = m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/
```

完整命令：

```text
build/RISCV/gem5.opt --listener-mode=on --redirect-stdout --redirect-stderr \
  --outdir=m5out/linux-16core-128mb-consolidated-ordered-cold-20260819 \
  configs/example/kmhv2_chi_6x4_hnf.py --raw-cpt \
  --generic-rv-cpt=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin \
  --num-cpus=16 --mem-size=128MB --mem-type=SimpleMemory --maxinsts=0 \
  -m 120000000000 --no-pf --max-checkpoints=1 \
  --checkpoint-dir=m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/checkpoints
```

恢复前该进程停在：

```text
finalTick = 1,688,382,465
simInsts  = 20,583,597
```

执行 `SIGCONT` 后，gem5 持续运行。20:22 的宿主状态检查显示：

```text
PID 3028672
STAT Rs
CPU time 01:24:57
```

两次宿主采样相隔 556 秒，CPU time 增加约 555 秒，说明当前阶段几乎持续占满一个 host core；墙钟慢来自详细模型本身，不是进程处于 stop 状态或长期抢不到调度。

### 16.2 已再次越过原始 Linux 崩溃窗口

本次串口重新得到：

```text
[0] Linux version 6.10.7-g0dc336bf6c07 ...
Machine model: freechips,rocketchip-unknown
SBI specification v2.0 detected
SBI TIME/IPI/RFENCE/DBCN extension detected
riscv: base ISA extensions acdfhimv
riscv: ELF capabilities acdfimv
```

原始问题最容易在 `riscv: ELF capabilities` 附近暴露；本次再次越过该位置，未出现：

```text
Unable to handle kernel NULL pointer dereference
Oops
Address fault
sbi_trap_error
panic
fatal
```

这进一步确认：关闭 classic-cache CHI 路径中不安全的 O3 load speculative wakeup 后，原始 `NULL pointer dereference @ 0x4` 不再复现。

### 16.3 16 个 CPU 已全部完成 Linux SMP bring-up

串口关键证据：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

在 bring-up 期间，`simerr` 中逐步出现 30 次 `enable SV48`，量级上对应 15 个 secondary hart 各自的地址空间切换序列。随后 Linux 明确汇总 `16 CPUs`，因此不是只启动了 boot hart，也不是某个 secondary hart 卡在 OpenSBI HSM 路径。

### 16.4 已完成的后续内核阶段

截至 20:22，串口已依次打印：

```text
[    0.005231] devtmpfs: initialized
[    0.006291] clocksource: jiffies: ...
[    0.006358] futex hash table entries: 4096 ...
[    0.006605] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.006770] DMA: preallocated 128 KiB GFP_KERNEL pool ...
[    0.006844] DMA: preallocated 128 KiB GFP_KERNEL|GFP_DMA32 pool ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008349] vgaarb: loaded
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
[    0.010783] NET: Registered PF_INET protocol family
[    0.011227] TCP: Hash tables configured (established 1024 bind 1024)
[    0.011308] UDP hash table entries: 256 ...
[    0.011354] UDP-Lite hash table entries: 256 ...
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011490] PCI: CLS 0 bytes, default 64
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

其中 `check_unaligned_access_all_cpus` 是由明确的 kernel command line blacklist，目的是避开详细 16 核模型上代价很高的探测 initcall；这不是运行时自动跳过错误。

`riscv_clocksource` 已接管早期 `jiffies` clocksource，且当前 cold run 没有在 timer/timekeeping 路径产生异常。这一点与旧 checkpoint restore 的“首次 MTIP 暴露坏栈”形成清晰对照：冷启动 timer 路径正常，旧恢复失败来自保存状态不完整。

### 16.5 定量进度

恢复运行后的部分采样如下：

| `finalTick` | `simInsts` | 可见阶段 |
|---:|---:|---|
| 9,648,003,344 | 86,507,484 | 已越过原始 crash window |
| 12,636,674,745 | 90,783,340 | secondary CPU bring-up |
| 15,033,779,220 | 96,662,929 | 16 CPU 汇合前后 |
| 15,904,989,825 | 99,031,069 | 16 CPUs online 后 |
| 17,019,184,035 | 101,519,578 | devtmpfs 后 |
| 20,204,290,575 | 109,068,242 | riscv_clocksource 后 |
| 23,290,232,292 | 116,323,358 | block I/O 初始化 |
| 25,064,027,490 | 122,242,926 | 已知静默 initcall 区间 |
| 27,732,578,055 | 130,833,239 | 已知静默 initcall 区间 |
| 28,830,669,870 | 134,237,319 | 20:22 最新采样 |

`finalTick` 和 `simInsts` 在每次采样中均单调增长，因此当前不是协议 deadlock 或 CPU 停止提交指令。

### 16.6 当前具体运行位置与“卡点”

当前最后一条 guest 日志是：

```text
[    0.012178] io scheduler bfq registered
```

与 2026-08-17 同配置、已经成功到 `/ #` 的 ordered cold baseline 对照，下一批预期日志为：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
...
[    0.038614] Run /bin/sh as init process
/ #
```

当前日志从启动到 `0.012178s` 与该成功 baseline 逐行一致。旧 baseline 在 `0.012178s → 0.020703s` 本来就没有串口输出，所以当前状态应表述为：

> 正在运行 block-I/O scheduler 注册之后、hvc0 console 接管之前的已知长 initcall 计算区间；模拟 tick 和提交指令持续增长，当前没有新 bug 或新死锁。

不能把“几分钟无串口输出”误写成 Linux 卡死。详细模型中几毫秒 guest time 会消耗数百亿 gem5 tick 和较长墙钟时间。

### 16.7 对可复用旧 run 的排查

扫描发现：

```text
m5out/linux-16core-128mb-consolidated-ordered-cold-20260817/
```

该 run 已经成功打印：

```text
[    0.038614] Run /bin/sh as init process
/ #
```

但当时没有 terminal 输入，也没有触发 guest checkpoint，最终：

```text
Exiting @ tick 120000000000 because simulate() limit reached
```

其 `checkpoints/` 为空。进程已经退出，无法事后从内存状态补生成 checkpoint。因此它只能作为成功启动的对照日志，不能替代当前 live run。

### 16.8 checkpoint helper 已核验

initramfs 自带：

```text
/bin/m5_checkpoint
SHA-256 = 53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
```

反汇编：

```text
0x100b0: li a0, 0
0x100b2: li a1, 0
0x100b4: .insn 4, 0x8600007b   # m5 checkpoint pseudo-op
0x100b8: li a0, 0
0x100ba: li a7, 93
0x100be: ecall                  # exit(0)
```

这与之前上传到 `/tmp/chi_guest_checkpoint_repo` 的 helper 执行语义等价。本次将直接使用 initramfs 自带 helper，避免 base64 注入带来的额外 guest 脏页和变量。

### 16.9 当前尚未完成的验收项

截至 20:22：

| 项目 | 状态 |
|---|---|
| 当前代码从零启动 Linux 6.10.7 | 进行中，已到 guest 0.012178s |
| 当前 run 16 CPUs online | **已通过** |
| 原始 NULL/Oops 不复现 | **已通过至当前阶段** |
| `/bin/sh` 提示符 | 尚未到达 |
| cold terminal 健康输出 | 尚未执行 |
| 新 ordered checkpoint | 尚未生成 |
| 新 checkpoint 内容审计 | 尚未执行 |
| 新 checkpoint restore | 尚未执行 |
| restore 后真实 terminal 命令 | 尚未执行 |

因此当前总体卡点仍是等待权威 cold run 完成剩余的正常内核初始化；它是性能/墙钟耗时卡点，不是已发现的新功能 bug。最终验收状态仍为 **未完成**。

### 16.10 21:44 更新：hvc0 与剩余驱动阶段已通过

20:22 之后，前一小节记录的长静默区间正常结束。串口依次得到：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
[    0.026816] clk: Disabling unused clocks
```

结论：

- hvc0 已成功接管 console；
- SBI early bootconsole 已正常退出；
- UARTLite `-ENXIO` 的时间戳和文本与两次成功 baseline 完全相同，它是设备树缺少该 UART IRQ 的已知 warning，不影响 hvc0；
- SDHCI、IPv6、Segment Routing、IOAM 和 tunnel driver 均正常注册；
- 第二段 `0.022703s → 0.026816s` 长静默区间也已被正常越过。

21:44 的量化采样：

```text
finalTick = 37,722,034,635
simTicks  = 36,033,652,170
simInsts  = 177,422,760
```

错误扫描仍然没有匹配：

```text
panic
Oops
Unable to handle
sbi_trap_error
fatal
Address fault
deadlock
watchdog
```

当前精确位置更新为：

> Linux 已完成 unused clocks 处理，正在 `clk: Disabling unused clocks` 与 `Freeing unused kernel image (initmem)` 之间的最后一段正常 initcall/内存释放计算。成功 baseline 的下一组关键日志是 guest `0.038539s` 释放 initmem、`0.038614s` 启动 `/bin/sh`。

因此功能状态仍正常；当前唯一卡点仍是详细模型完成最后约 11.7ms guest time 所需的墙钟时间。

## 17. 21:44–次日 00:20：第一轮新 checkpoint 已生成，但语义审计失败；writeback 顺序再次修正

本节是当前最新状态。第 16 节末尾仍处于 Linux 最后一个静默 initcall；随后同一个 cold run 已正常进入 shell、执行命令并生成 checkpoint。新 artifact 在文件级和 CHI canonical-state 检查上全部通过，但关键内核栈为 **0/15 通过**，所以不能直接进入最终 restore 验收。

### 17.1 第一轮权威 cold run 已完整进入 `/bin/sh`

运行目录：

```text
m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/
```

Linux 后续关键输出：

```text
[    0.038540] Freeing unused kernel image (initmem) memory: ...
[    0.038617] Run /bin/sh as init process
/bin/sh: can't access tty; job control turned off
/ #
```

整个 cold boot 再次确认：

```text
smp: Brought up 1 node, 16 CPUs
```

且日志中没有出现：

```text
panic
Oops
Unable to handle
sbi_trap_error
Address fault
deadlock
watchdog
Program aborted
```

因此此前原始 Linux `NULL pointer dereference @ 0x4`、16 核 bring-up、CLINT/RTC、WFI 唤醒和 hvc0 console 路径在 cold boot 中仍然保持通过。

### 17.2 cold terminal 命令和 checkpoint helper 均由 guest 真实执行

预排队输入到达 shell 后，guest 返回：

```text
FINAL_ORDERED_COLD_SHELL_20260819
FINAL_ORDERED_HELPER_SHA256=78ae261dd36f943d1644fc143e6efc7e11e388f863bee95e313b8cf1e659d862  /tmp/chi_guest_checkpoint
FINAL_ORDERED_PROC_READY_20260819
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
FINAL_ORDERED_CPU_COUNT=16
FINAL_ORDERED_CPU_ONLINE=cat: can't open '/sys/devices/system/cpu/online': No such file or directory
FINAL_ORDERED_UPTIME=0.05 0.77
FINAL_ORDERED_TRIGGER_CHECKPOINT_20260819
Writing checkpoint
Exiting @ tick 65437211256 because checkpoint
```

`CPU_ONLINE` 这一项失败只是该轮脚本没有挂载 sysfs；同一输出中的 `/proc/cpuinfo` 已明确返回 16，kernel 早期日志也明确返回 16 CPUs online。第二轮脚本已经增加：

```sh
mount -t sysfs sysfs /sys
cat /sys/devices/system/cpu/online
```

以补齐预期 `0-15` 的直接证据。

### 17.3 第一轮新 checkpoint 的文件完整性

生成目录：

```text
m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/
  checkpoints/cpt.65437211256/
```

文件大小和 SHA-256：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `m5.cpt` | 39,087,944 bytes | `6447fd1e103ddfd175ee4dfb505bad58d1890d7e3675c8297db9c92f59acd5ed` |
| `system.physmem.store0.pmem` | 6,389,933 bytes（gzip） | `a42a2b8fe563380aa8f861517596693c1567f35ca447a1fe6d8cb5154a9c79e8` |
| 解压后的 pmem | 134,217,728 bytes | `420be3c803d8387a8f26968f28e1d5fa4b62d48df87c2914c76db35a1a047f6c` |

检查结果：

```text
gzip -t: PASS
uncompressed size: 134217728
```

因此这不是 checkpoint 文件截断、gzip 损坏或 128 MiB backing store 尺寸错误。

### 17.4 第一轮实际 writeback 数量和顺序

checkpoint 前的日志统计：

| 层级 | 实例数 | 写回 line 总数 | 日志顺序 |
|---|---:|---:|---|
| private L2 inner cache | 64 slices | 6,188 条 formerly-modified line | `simerr:675–738` |
| CHI SLC | 16 HN-F | 275,697 条 dirty line | `simerr:739–754` |

当时的实际顺序为：

```text
L1（没有逐 cache 计数日志）
  -> L2 64 slices
  -> SLC 16 HN-F
  -> serialize pmem
```

所有 L2 slice 和所有 HN-F 都进入了 writeback，没有“漏调用某一个 slice”的迹象；但调用齐全不代表最终值的覆盖优先级正确。

### 17.5 CHI transient/canonical 状态检查通过

对新 `m5.cpt` 和解压 pmem 执行：

```bash
python3 util/debug_chi_checkpoint.py \
  m5out/linux-16core-128mb-consolidated-ordered-cold-20260819/checkpoints/cpt.65437211256/m5.cpt \
  /tmp/ordered-cpt-audit.R2d0Zc/pmem.raw
```

结果：

```text
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

这证明当前 canonical checkpoint policy 已按设计执行：

- dirty SLC 数据在 serialize 前被要求写入 backing memory；
- 最终 `m5.cpt` 不再保存会指向未序列化 private cache 的 SLC/SF；
- restore 时以 pmem 为唯一权威数据源；
- 当前失败不是“又忘记 overlay 一个 serialized SLC”。

### 17.6 决定性失败：15 个 idle hart 的栈为 0/15 通过

新增可重复审计工具：

```text
util/audit_riscv_checkpoint_stacks.py
```

它对每个 CPU 执行以下检查：

1. 从 `m5.cpt` 读取 architectural `x1/ra`、`x2/sp`、`x8/s0`、PC；
2. 从对应 ISA section 的 `miscRegFile[143]` 读取 SATP；
3. 只使用最终 raw pmem 做 Sv48 page-table walk；
4. 将虚拟 SP 映射到 physical address；
5. 读取 `[sp]` 和 `[sp+8]`；
6. 对停在 `arch_cpu_idle+0x10` 的 CPU 检查：
   - `s0 == sp + 0x10`；
   - `[sp+8] == live ra`。

完整逐核结果：

| CPU | PC | SP physical | pmem `[sp]` | pmem `[sp+8]` | live RA | 结果 |
|---:|---|---|---|---|---|---:|
| 0 | `ffffffff8046f112` | `81584e60` | `0` | `1` | `ffffffff80470480` | FAIL |
| 1 | `ffffffff8046f112` | `8158ae60` | `0` | `2` | `ffffffff80470480` | FAIL |
| 2 | `ffffffff8046f112` | `8158ee60` | `0` | `3` | `ffffffff80470480` | FAIL |
| 3 | `ffffffff8046f112` | `81203e10` | `ffffffff81003e70` | `1` | `ffffffff80470480` | FAIL |
| 4 | `ffffffff8046f112` | `81592e60` | `0` | `4` | `ffffffff80470480` | FAIL |
| 5 | `ffffffff8046f112` | `81596e60` | `0` | `5` | `ffffffff80470480` | FAIL |
| 6 | `ffffffff8046f112` | `8159ae60` | `0` | `6` | `ffffffff80470480` | FAIL |
| 7 | `ffffffff8046f112` | `8159ee60` | `0` | `7` | `ffffffff80470480` | FAIL |
| 8 | `ffffffff8046f112` | `815c2e60` | `0` | `8` | `ffffffff80470480` | FAIL |
| 9 | `ffffffff8046f112` | `815c6e60` | `0` | `9` | `ffffffff80470480` | FAIL |
| 10 | `ffffffff8046f112` | `815cae60` | `0` | `a` | `ffffffff80470480` | FAIL |
| 11 | `ffffffff8046f112` | `815cee60` | `0` | `0` | `ffffffff80470480` | FAIL |
| 12 | `ffffffff8046f112` | `815d2e60` | `0` | `c` | `ffffffff80470480` | FAIL |
| 13 | `ffffffff8046f112` | `815d6e60` | `0` | `d` | `ffffffff80470480` | FAIL |
| 14 | `00000000000100b8` | user SP 未映射 | — | — | `0000000000064c0c` | helper continuation，不计入 idle |
| 15 | `ffffffff8046f112` | `815dee60` | `0` | `f` | `ffffffff80470480` | FAIL |

汇总：

```text
idle_summary passed=0 total=15
```

重要细节：

- 15 个 idle hart 的 live `s0 == sp+0x10` 全部通过，说明寄存器和栈映射不是随意猜测；
- 15 个 hart 的 live RA 全部是符号正确的 `default_idle_call+0x18 = 0xffffffff80470480`；
- pmem 中却仍是 `1,2,3,...,a,0,c,d,f` 这组明显的早期/旧 frame 数据；
- CPU2 的 `0x3` 与旧 checkpoint 首次 MTIP 后实际 `c_ldsp ra,8(sp)` 读到并跳向 `0x2` 的值完全一致；
- 本轮 helper 运行在 CPU14，旧 artifact 的 helper 曾运行在 CPU10，说明审计不是把旧 CPU 状态表机械套到新 checkpoint 上。

因此 `cpt.65437211256` 虽然是正常退出生成的完整文件，仍然是**语义不可恢复 checkpoint**。在这一步直接跑 restore，只会重复已知坏栈故障，不能算有效验收。

### 17.7 为什么“L2/SLC 都写回了”仍会得到旧 pmem

代码路径给出了一个与最终结果完全一致的覆盖顺序问题。

第一，classic cache 的 checkpoint writeback 使用：

```cpp
memSidePort.sendFunctional(&packet);
```

第二，RN-F bridge 对 functional request 的实现是：

```cpp
void Cache2ChiBridge::cacheRecvFunctional(PacketPtr pkt)
{
    memPort.sendFunctional(pkt);
}
```

在当前 6×4 配置中：

```text
bridge.mem_side = system.membus.cpu_side_ports
```

因此 L2 的 functional write 直接进入 classic membus/physical-memory 路径，不会作为正常 CHI transaction 去更新 modeled SLC。

第三，SLC checkpoint writeback 又直接调用：

```cpp
system->getPhysMem().functionalAccess(&packet);
```

第一轮 Python walker 使用升序：

```python
descendants.sort(key=cache_level)
```

配置值是：

```text
L1  = 1
L2  = 2
SLC = 3
```

于是同一地址可能发生：

```text
1. L1 把最新 owner data 推给 L2；
2. L2 把最新 data functional-write 到 pmem；
3. SLC 最后把自己仍持有的旧 lower copy 写到同一 pmem 地址；
4. 最终 checkpoint 保存步骤 3 的旧值。
```

这也解释了为何：

- writeback 数量很大且所有实例都被调用；
- SLC/SF 最终可以序列化为空；
- pmem 的关键 line 却仍然是旧值。

严格地说，第一轮 artifact 已经丢弃 SLC/SF，无法事后证明那 15 个具体地址当时都在 SLC 中；因此最终确认仍以第二轮同配置 A/B 审计为准。但上述 bypass 路径和覆盖次序是确定的代码事实，也是目前唯一同时解释“完整调用 + 空 SLC/SF + stale pmem”的可执行根因。

### 17.8 最新代码修复：显式采用 `SLC → L1 → L2`

不再把“architectural cache level”和“checkpoint 覆盖优先级”混为一个字段。`SlcSnoopFilter` 新增：

```python
checkpoint_writeback_priority = Param.Int(-1, ...)
cache_level = Param.Unsigned(3, ...)
```

全局 walker 使用：

```python
checkpoint_writeback_priority（若存在）
否则 cache_level
否则 0
```

升序后的关键阶段为：

```text
-1: 16 个 SLC 先把 lower copy 写到 pmem
 0: 无 cache-level 的普通 SimObject（memWriteback 通常为空）
 1: L1 把最新 private copy 推向 L2/下游
 2: L2 最后把最新合并结果写到 pmem
```

同时 `BaseCache::memWriteback()` 新增每个 cache 的总 modified-line 和 sticky-only-line 计数。第二轮 checkpoint 时将能直接确认：

- 哪些 L1 DCache 实际持有 modified data；
- SLC 日志是否严格早于 L1/L2；
- 64 个 L2 slice 是否全部在最后阶段执行。

### 17.9 构建验证

执行：

```bash
scons build/RISCV/gem5.opt -j64
```

结果：

```text
scons: done building targets.
```

新 binary：

```text
build/RISCV/gem5.opt
mtime = 2026-08-20 00:15:21 CST
size  = 931177768 bytes
```

生成参数已确认：

```text
SlcSnoopFilterParams::cache_level                    unsigned
SlcSnoopFilterParams::checkpoint_writeback_priority int
```

静态检查：

```text
git diff --check: PASS
python3 -m py_compile src/python/m5/simulate.py \
                          src/mem/cache/CHI/SlcSnoopFilter.py: PASS
```

使用新 binary 对 walker 运行最小 mock-order 自检，输入对象故意按 `L2, ordinary, SLC, L1` 的乱序提供；实际输出：

```text
checkpoint writeback order PASS: slc -> ordinary -> l1 -> l2
```

构建 warning 仍只有缺少 PNG/HDF5/backtrace 支持；与本次 checkpoint 修改无关。

### 17.10 第二轮权威 cold run 已启动

输出目录：

```text
m5out/linux-16core-128mb-slc-first-cold-20260820/
```

启动时间与进程：

```text
gem5 started Aug 20 2026 00:20:12
host PID 1273441
terminal port 3456
```

完整命令：

```bash
build/RISCV/gem5.opt --listener-mode=on --redirect-stdout --redirect-stderr \
  --outdir=m5out/linux-16core-128mb-slc-first-cold-20260820 \
  configs/example/kmhv2_chi_6x4_hnf.py --raw-cpt \
  --generic-rv-cpt=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin \
  --num-cpus=16 --mem-size=128MB --mem-type=SimpleMemory --maxinsts=0 \
  -m 120000000000 --no-pf --max-checkpoints=1 \
  --checkpoint-dir=m5out/linux-16core-128mb-slc-first-cold-20260820/checkpoints
```

新 config 已确认 16 个 SLC 全部是：

```text
cache_level=3
checkpoint_writeback_priority=-1
```

terminal 已连接，以下 guest 验收输入已经预排队：

```text
SLC_FIRST_COLD_SHELL_20260820
mount -t proc proc /proc
mount -t sysfs sysfs /sys
SLC_FIRST_PROC_SYS_READY_20260820
uname -a
SLC_FIRST_CPU_COUNT=<期望 16>
SLC_FIRST_CPU_ONLINE=<期望 0-15>
SLC_FIRST_UPTIME=<guest 实际值>
SLC_FIRST_SHELL_PID=<guest 实际 PID>
sync
SLC_FIRST_TRIGGER_CHECKPOINT_20260820
/bin/m5_checkpoint
```

00:20 的精确位置是：

```text
**** REAL SIMULATION ****
0: system.terminal: attach terminal 0
Entering event queue @ 1688382900. Starting simulation...
```

其中 terminal 在连接时记录的 `0` 是串口 attach 事件标签；真正进入 event queue 的初始 tick 是 `1,688,382,900`。新 binary 已完成 16 核 CHI 6×4 topology 初始化并开始执行 raw payload，当前没有新 panic/fatal。它还没到 Linux printk，因此当前卡点是详细模型从启动状态重跑到 shell 的墙钟时间，不是已经观察到新的 Linux 卡死。

### 17.11 当前最终验收状态

| 验收项 | 状态 |
|---|---:|
| 第一轮修复代码 cold boot 到 Linux shell | **通过** |
| 第一轮 cold terminal 真实命令 | **通过** |
| 第一轮 guest checkpoint 正常生成 | **通过** |
| 第一轮 checkpoint 文件/gzip/hash | **通过** |
| 第一轮 CHI transient、SLC/SF canonical empty | **通过** |
| 第一轮 idle 栈语义 | **失败：0/15** |
| writeback 顺序缺陷 | **已定位并完成第二次修复** |
| 修复后 binary 构建 | **通过** |
| 第二轮 SLC-first cold run | **进行中** |
| 第二轮 checkpoint 栈审计 | 待生成后执行 |
| 第二轮 checkpoint restore | 待审计通过后执行 |
| restore 后 `uname`/CPU/online/uptime 命令 | 待执行 |
| 最终闭环 | **未完成** |

截至 00:20，最准确的卡点表述是：

> Linux 本身已经多次在 16 核 CHI 配置中稳定启动到 shell。第一轮新 checkpoint 的失败不是 drain 没结束、文件损坏或 SLC/SF 没清空，而是最终 pmem 覆盖优先级仍可能让 stale SLC 覆盖 private owner 的新值。该顺序已修为 `SLC → L1 → L2`，当前正在等待第二轮 cold run 生成可供同一逐核工具复审的新 checkpoint。只有 15/15 idle 栈通过、随后 restore 无 Linux crash 且 terminal 返回真实命令输出，任务才算完成。

### 17.12 00:35 量化进度：第二轮不是静默卡死

宿主进程：

```text
PID 1273441
STAT Rs+
CPU 99.9%
CPU time 00:14:47
```

主动 stats dump：

```text
simTicks     = 5,275,261,530
finalTick    = 6,963,643,995
simInsts     = 80,584,184
hostSeconds  = 619.98
hostTickRate = 8,508,747 tick/s
```

相对共同起点已经推进约 5.275B tick，`finalTick` 和 `simInsts` 均明显增长；日志仍无 panic/Oops/fatal/address-fault/deadlock/watchdog。此时还没有新的 console printk，因此状态仍是启动早期的计算区间，而不是发现了新故障。

### 17.13 00:36–01:22：第二轮已越过原始崩溃点并完成 16 CPU bring-up

00:36 出现第二轮 Linux 入口：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI specification v2.0 detected
[    0.000000] SBI TIME extension detected
[    0.000000] SBI IPI extension detected
[    0.000000] SBI RFENCE extension detected
[    0.000000] SBI DBCN extension detected
[    0.000000] printk: legacy bootconsole [sbi0] enabled
```

随后正常完成：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
[    0.000000] pcpu-alloc: [0] 00 ... [0] 15
[    0.000000] Memory: 108580K/131072K available ...
[    0.000000] SLUB: ... CPUs=16, Nodes=1
[    0.000000] rcu: ... nr_cpu_ids=16
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

原始 `NULL pointer dereference @ 0x4` 发生在 ISA extension 附近；本轮已经明确越过，且错误扫描仍为空。

后续时间线：

```text
[    0.005231] devtmpfs: initialized
[    0.006291] clocksource: jiffies ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008349] vgaarb: loaded
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
[    0.010783] NET: Registered PF_INET protocol family
[    0.011227] TCP: Hash tables configured ...
[    0.011308] UDP hash table entries: 256 ...
[    0.011354] UDP-Lite hash table entries: 256 ...
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011490] PCI: CLS 0 bytes, default 64
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

01:22 的精确位置是 block I/O scheduler 后、hvc0 接管前的已知长 initcall 区间；这与第一轮第 16.6 节的路径一致，不是新卡点。第二轮 cold execution 至此已验证 writeback 排序参数不会影响正常 CHI/Linux 执行。

### 17.14 01:22–03:20：第二轮继续通过 hvc0、驱动和时钟收尾

后续 console 输出继续沿第一轮已通过路径前进，完整经过控制台切换、串口/SDHCI、IPv6 和时钟清理：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
[    0.026816] clk: Disabling unused clocks
```

其中 `uartlite ... -ENXIO` 与第一轮相同，是设备树未提供 IRQ 时的已知非阻塞 warning；其后仍有多项 initcall 正常完成，因此不能把它归类为当前 crash 或卡点。

本轮最后一次手工 stats dump 位于 console 时钟清理之前：

```text
simTicks    = 35,916,312,225
finalTick   = 37,604,694,690
simInsts    = 176,923,884
hostSeconds = 10,663.46
```

`simout` 最后修改时间为 `2026-08-20 03:20:00 +0800`，最后一条 guest 输出仍是正常的 `clk: Disabling unused clocks`；`simerr` 中没有 checkpoint、panic、Oops、fatal、SIGSEGV 或异常地址故障。该执行尚未到第一轮已经观察到的：

```text
[    0.038540] Freeing unused kernel image ...
[    0.038617] Run /bin/sh as init process
```

### 17.15 新增宿主侧卡点：交互执行会话满 3 小时被回收

第二轮原进程在 `00:20:12` 启动，并在约 `03:20:00` 消失，持续时间与执行基础设施的 3 小时会话上限精确重合。退出后检查结果：

```text
host PID 1273441: 不存在
checkpoints/: 目录存在，但没有 cpt.*
simout 最后一行: [0.026816] clk: Disabling unused clocks
simerr crash 关键字扫描: 空
```

因此这次没有生成第二轮 checkpoint；它不是 SLC-first 代码回归，也不是 Linux 在 `clk` 处崩溃。结合精确的 3 小时边界，最符合证据的解释是承载 gem5 的交互 PTY/exec 会话被基础设施回收。这个问题不改变 guest/CHI 根因，但会阻止一个需要约 4–5 小时墙钟的 16 核 O3 cold boot 完成。

处理方式是把同一命令改为独立 session 的持久进程，而不是继续依赖会被回收的交互父会话。03:28 启动的新 run：

```text
outdir: m5out/linux-16core-128mb-slc-first-cold-detached-20260820
host PID: 2407347
start: 2026-08-20 03:28:21 +0800
STAT: Rs
CPU: 100%
terminal port: 3457
```

其 workload、16 核 CHI 6×4 拓扑、128 MiB 内存、`-m 120000000000`、checkpoint 目录和新 binary 全部与被截断的第二轮相同；唯一变化是宿主生命周期管理。旧 terminal 会话仍占用 3456，因此新 gem5 明确记录 `Listening for connections on port 3457`。初次尝试连接 3456 得到 `terminal already attached!` 后，已根据 `simerr` 纠正到 3457，并确认收到 `==== m5 terminal: Terminal 0 ====`。以下验收命令已经在正确端口重新预排队：

```text
SLC_FIRST_DETACHED_COLD_SHELL_20260820
mount /proc and /sys
uname -a
CPU_COUNT / CPU_ONLINE / uptime / shell PID
sync
SLC_FIRST_DETACHED_TRIGGER_CHECKPOINT_20260820
/bin/m5_checkpoint
```

截至 03:29，新 PID 持续占用 100% CPU，配置文件与 `simout/simerr` 已创建，尚无 guest crash。当前真实卡点已从“等待原 run 到 shell”进一步细化为：

> 原第二轮因宿主交互会话 3 小时寿命不足而被截断；现已绕过该寿命限制，用持久进程从相同输入重跑。仍需等待它到 `/bin/sh` 并生成 SLC-first checkpoint，才能执行决定性的 15-hart 栈审计。

### 17.16 03:28–04:29：持久重跑再次越过原崩溃点并完成 16 核启动

新 run 的 terminal 实际监听端口为 3457，连接记录和 event queue 起点为：

```text
1663253820: system.terminal: attach terminal 0
Entering event queue @ 1688382900. Starting simulation...
```

约 15 分钟墙钟后进入 Linux：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI specification v2.0 detected
[    0.000000] SBI TIME extension detected
[    0.000000] SBI IPI extension detected
[    0.000000] SBI RFENCE extension detected
[    0.000000] SBI DBCN extension detected
[    0.000000] SBI HSM extension detected
```

原始用户问题中的崩溃窗口再次明确通过：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
[    0.000000] percpu: Embedded 14 pages/cpu ...
[    0.000000] pcpu-alloc: [0] 00 ... [0] 15
```

没有再次出现历史错误：

```text
Unable to handle kernel NULL pointer dereference at ... 0x4
Oops [#1]
```

随后完整完成 16 核 SMP bring-up：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

并继续通过设备、时钟与网络初始化：

```text
[    0.005231] devtmpfs: initialized
[    0.006291] clocksource: jiffies ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
[    0.010783] NET: Registered PF_INET protocol family
[    0.011227] TCP: Hash tables configured ...
[    0.011308] UDP hash table entries: 256 ...
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

04:29 的宿主快照：

```text
PID      = 2407347
elapsed  = 01:01:22
CPU time = 01:01:22
CPU      = 99.9%
STAT     = Rs
```

elapsed 与 CPU time 同步增长，证明进程在持续计算；日志没有 panic/Oops/fatal/watchdog/stall。当前 guest 精确位置是 I/O scheduler 完成后、`hvc0` 接管前的长 initcall 区间，与上一轮路径一致。该位置不是新的 Linux 卡死点。

### 17.17 06:29：持久进程已跨过原 3 小时回收边界

持久重跑随后继续完成：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
[    0.026816] clk: Disabling unused clocks
```

这与被截断 run 的 Linux 路径和 guest 时间戳完全一致。决定性区别出现在宿主 elapsed 超过三小时后：

```text
wall clock = 2026-08-20 06:29:31 +0800
PID        = 2407347
start      = 2026-08-20 03:28:21 +0800
elapsed    = 03:01:08
CPU time   = 03:01:08
CPU        = 99.9%
STAT       = Rs
```

原交互 run 在约 `03:00:00` 恰好消失；新 PID 在 `03:01:08` 后仍持续执行，证明独立 session 的宿主生命周期 workaround 生效。此时 checkpoint 目录仍只有 parent、没有 `cpt.*`，因为 guest 尚未到 `/bin/sh` 和 helper；这是符合阶段预期的空目录，不是 checkpoint 创建失败。

当前精确 Linux 位置是 clocks 清理完成后的已知长段，下一组预期输出为：

```text
[    0.038540] Freeing unused kernel image ...
[    0.038617] Run /bin/sh as init process
```

因此截至 06:29，宿主 3 小时 blocker 已解决；当前剩余等待项恢复为 guest 从 `0.026816s` 运行到 shell，并触发第二个 SLC-first checkpoint。

### 17.18 08:52：持久重跑完整进入 shell，并由 guest 生成第二个 checkpoint

持久 run 最终越过最后一个静默 initcall，完整进入 init shell：

```text
[    0.038541] Freeing unused kernel image (initmem) memory: 4112K
[    0.038620] Run /bin/sh as init process
[    0.038649]   with arguments:
[    0.038673]     /bin/sh
[    0.038693]   with environment:
[    0.038717]     HOME=/
[    0.038736]     TERM=linux
/bin/sh: can't access tty; job control turned off
/ #
```

预排队命令随后由 guest shell 真实执行，返回值如下：

```text
SLC_FIRST_DETACHED_COLD_SHELL_20260820
SLC_FIRST_DETACHED_PROC_SYS_READY_20260820
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
SLC_FIRST_DETACHED_CPU_COUNT=16
SLC_FIRST_DETACHED_CPU_ONLINE=0-15
SLC_FIRST_DETACHED_UPTIME=0.04 0.66
SLC_FIRST_DETACHED_SHELL_PID=133
```

这组输出同时证明：

1. Linux 6.10.7 已完成 init；
2. `/proc` 和 `/sys` 已挂载；
3. 16 个逻辑 CPU 均被 Linux 枚举且 online mask 为 `0-15`；
4. terminal RX → UARTLite → guest shell 的输入链路有效；
5. 先前 `[0.026816] clk: Disabling unused clocks` 后的长静默只是详细 O3 模型耗时，不是 Linux 卡死。

本轮同时暴露了一个新的、可恢复的 guest 打包问题：firmware payload 中没有预期的 `/bin/m5_checkpoint`：

```text
/ # /bin/m5_checkpoint
/bin/sh: /bin/m5_checkpoint: not found
```

这不是 gem5 checkpoint 逻辑或 Linux crash。为避免重新构建并再跑一遍 5 小时 cold boot，采用了可审计的 guest 内注入：把宿主已有的 552-byte static RISC-V ELF 经过 base64 写入 guest `/tmp/chi_guest_checkpoint_repo`，再 `chmod 755`。宿主与 guest 的 SHA256 一致：

```text
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
```

宿主文件属性：

```text
ELF 64-bit LSB executable, UCB RISC-V, RVC, double-float ABI,
statically linked, stripped
size = 552 bytes
```

该 helper 在 `0x100b4` 执行 gem5 checkpoint pseudo instruction，随后正常退出。guest 侧证据：

```text
SLC_FIRST_DETACHED_RETRY_CHECKPOINT_20260820
/ # /tmp/chi_guest_checkpoint_repo
Writing checkpoint
Exiting @ tick 94883960936 because checkpoint
```

因此第二个 checkpoint 确实由已经进入 Linux shell 的 guest 主动触发，不是 host 在任意 tick 强切。

### 17.19 第二个 checkpoint 的文件、hash、gzip 与实际 writeback 顺序

新 artifact：

```text
m5out/linux-16core-128mb-slc-first-cold-detached-20260820/
└── checkpoints/
    └── cpt.94883960936/
        ├── m5.cpt
        └── system.physmem.store0.pmem
```

文件元数据：

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `m5.cpt` | 40,022,901 | `e2d8d6cfe08aafe361d3ffc0fadc92741836123868fb54c0371857549a23f250` |
| `system.physmem.store0.pmem` | 6,377,291 | `454445a96c68c5256a9bf92dc21818c39890d912f338d1dd72c7108d152203e8` |
| 解压后的 raw pmem | 134,217,728 | `998a07113af605c1a7d300e8b20bb7c3e9e4579f75710b1ce29175e42fbf3d43` |

`gzip -t` 返回 0，解压大小与配置的 128 MiB 完全一致。

更重要的是，`simerr` 行号直接证明第二轮的 SLC-first 排序不是“代码看起来应该生效”，而是实际执行顺序已经生效：

```text
689–704 : system.home_node00..15.slcsf
705–719 : dtb/itb walker caches
720–735 : system.cpu00..15.dcache
736–799 : system.l2_wrappers00..15.slices0..3.inner_cache
```

数量汇总：

| 层级 | 对象数 | 本轮 dirty/sticky-only writer 实际写入 line 数 |
|---|---:|---:|
| HN-F/SLC | 16 | 280,467 dirty SLC lines |
| DTB/ITB walker cache | 15 个有写入 | 35 modified lines |
| L1 D-cache | 16 | 8,252 modified lines |
| L2 slices | 64 | 63,995 modified/sticky lines |
| classic cache 合计 | 95 个有写入 | 72,282 lines |

本轮日志中没有 I-cache writeback，是因为 I-cache 中的 line 都是 clean，而当时 `BaseCache::memWriteback()` 仍只选择 `DirtyBit || checkpointDirty`。这条“缺失日志”后来成为继续下沉根因的重要证据。

CHI checkpoint 离线检查通过：

```text
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

这里的 `{}` 表示序列化的 canonical-empty SLC 与最终 pmem 没有矛盾；它不能证明所有未序列化 cache 中的最新数据都已被挑选出来，因此仍必须做栈语义审计。

### 17.20 决定性结果：第二个 checkpoint 的 idle-hart 栈仍然是 0/15

对解压后的 128 MiB pmem 运行：

```bash
python3 util/audit_riscv_checkpoint_stacks.py \
  m5out/linux-16core-128mb-slc-first-cold-detached-20260820/checkpoints/cpt.94883960936/m5.cpt \
  /tmp/slc-first-cpt-audit.7KyB5W/pmem.raw
```

完整结果：

| CPU | PC | SP / physical | pmem saved RA | live RA | 结果 |
|---:|---|---|---|---|---:|
| 00 | `0xffffffff8046f112` | `0xffff8f800010be60` / `0x81584e60` | `0x1` | `0xffffffff80470480` | FAIL |
| 01 | `0xffffffff8046f112` | `0xffff8f8000113e60` / unmapped | unmapped | `0xffffffff80470480` | FAIL |
| 02 | `0xffffffff8046f112` | `0xffff8f800011be60` / `0x8158ee60` | `0x0` | `0xffffffff80470480` | FAIL |
| 03 | `0xffffffff8046f112` | `0xffffffff81003e10` / `0x81203e10` | `0x1` | `0xffffffff80470480` | FAIL |
| 04 | `0xffffffff8046f112` | `0xffff8f8000123e60` / `0x81592e60` | `0x4` | `0xffffffff80470480` | FAIL |
| 05 | `0xffffffff8046f112` | `0xffff8f800012be60` / `0x81596e60` | `0x5` | `0xffffffff80470480` | FAIL |
| 06 | `0xffffffff8046f112` | `0xffff8f8000133e60` / unmapped | unmapped | `0xffffffff80470480` | FAIL |
| 07 | `0xffffffff8046f112` | `0xffff8f800013be60` / `0x8159ee60` | `0x7` | `0xffffffff80470480` | FAIL |
| 08 | `0xffffffff8046f112` | `0xffff8f8000143e60` / `0x815c2e60` | `0x8` | `0xffffffff80470480` | FAIL |
| 09 | `0xffffffff8046f112` | `0xffff8f800014be60` / `0x815c6e60` | `0x9` | `0xffffffff80470480` | FAIL |
| 10 | `0xffffffff8046f112` | `0xffff8f8000153e60` / `0x815cae60` | `0xa` | `0xffffffff80470480` | FAIL |
| 11 | `0xffffffff8046f112` | `0xffff8f800015be60` / `0x815cee60` | `0x0` | `0xffffffff80470480` | FAIL |
| 12 | `0xffffffff8046f112` | `0xffff8f8000163e60` / `0x815d2e60` | `0xc` | `0xffffffff80470480` | FAIL |
| 13 | helper PC `0x100b8` | `0x7fffebc47d70` / `0x80b7fd70` | n/a | helper context | 不计入 idle 集合 |
| 14 | `0xffffffff8046f112` | `0xffff8f8000173e60` / `0x815dae60` | `0xe` | `0xffffffff80470480` | FAIL |
| 15 | `0xffffffff8046f112` | `0xffff8f800017be60` / `0x815dee60` | `0xf` | `0xffffffff80470480` | FAIL |

最终摘要：

```text
idle_summary passed=0 total=15
```

几个特征非常关键：

1. hart 0/2/3/4/5/7–12/14/15 的 pmem saved RA 仍是 `0、1、4、5、7...f` 这类旧初始化值，而 CPU architectural state 中的 live `ra` 已是 Linux idle 返回地址 `0xffffffff80470480`；
2. CPU1 和 CPU6 的栈虚拟地址甚至无法用 checkpoint 内页表映射，说明丢失的不只是 15 条栈数据，也包括部分页表更新；
3. CPU13 正在执行 checkpoint helper，所以不应拿用户态 helper 栈与 idle frame 比较；审计正确排除了它；
4. 15 个 idle hart 全部失败，模式与第一轮一致，不可能解释为单核偶发或 audit 工具误差。

因此 `cpt.94883960936` 是文件完整但语义不完整的 checkpoint。直接 restore 它只会重现坏栈/坏页表 continuation，故本轮明确没有浪费数小时去执行一个已知无效的恢复。

### 17.21 被证伪的假设与更深根因：顺序正确，但 dirty-only 集合不完整

第一轮的根因假设是：

```text
L1/L2 新值 → pmem
随后 stale SLC 旧值 → pmem
最终旧值覆盖新值
```

第二轮日志已经证明 SLC 被提前到所有 private/L2 writer 之前，因此上述“stale SLC 最后覆盖”的确切路径已被消除。然而 0/15 结果不变，所以结论必须更新为：

> writeback 顺序是必要条件，但不是充分条件；当前 checkpoint writer 选择的 line 集合本身不完整。

旧实现有两个过滤条件：

```text
SLC        : line.valid && isDirty(line.state)
             isDirty 只接受 MU/MN

BaseCache  : DirtyBit || checkpointDirty
```

它们共同假定“只有 dirty line 才可能比下层内存更新”。这个假定在标准、完全正确维护状态的 inclusive hierarchy 中通常成立，但当前自研 CHI/SLC 模型的历史数据已经直接反证：

```text
有效 SLC line                    = 299,178
与 pmem 内容不同的有效 SLC line = 191,681
变化字节                         = 8,163,370
```

这些 mismatch 不可能全部由当前仅 `MU/MN` 的 dirty 集合覆盖。类似地，第二轮 checkpoint 日志完全没有 I-cache，说明 classic cache writer 也会静默跳过所有 valid-clean copy。

更准确的数据优先级模型应是：

```text
较低/较旧副本先写
        ↓
所有有效上层副本按层级覆盖
        ↓
最终 pmem 成为 non-serialized hierarchy 的完整合并结果
```

这不是永久改变正常 cache eviction/writeback 行为；只在已经 drain、即将创建不序列化 private cache tag/data 的 full-system checkpoint 时执行。

### 17.22 最新 all-valid 修复

#### 17.22.1 SLC backend：新增全有效 line visitor

`HnfSLCSFBackend` 新增：

```cpp
void forEachValidSlcLine(
    const std::function<void(
        uint64_t, const std::vector<uint8_t>&)>& visitor) const;
```

行为：

- 按确定的 set/way 顺序遍历；
- 只跳过 invalid line，不再只限 `MU/MN`；
- 每条 valid line 必须有完整 `blockSize` 数据，否则 checkpoint 前 panic；
- 保留 `forEachDirtySlcLine()`，便于协议级测试和对比，不破坏原 API。

`SlcSnoopFilter::memWriteback()` 改用 `forEachValidSlcLine()`，日志也改为 `valid SLC lines`。SLC 仍以 `checkpoint_writeback_priority = -1` 最先写入 physical memory；checkpoint 中 SLC/SF 仍序列化为 canonical empty，restore 只信最终 pmem。

#### 17.22.2 classic cache：checkpoint 时遍历所有 valid block

`BaseCache::memWriteback()` 从：

```cpp
DirtyBit || checkpointDirty
```

改为：

```cpp
blk.isValid()
```

同时保留统计：总 valid line、其中 modified line、其中只剩 sticky checkpointDirty 的 line。每条有效 block 仍通过 functional `WriteReq` 发送到下一级，写完清除 protocol DirtyBit 和 checkpoint sticky bit。

该变化只影响显式 `memWriteback()`/checkpoint consolidation，不改变日常 timing request、替换或正常 writeback 流程。

#### 17.22.3 同级覆盖优先级：只读 cache 先于可写 cache

全 valid 写回后，同层副本的先后顺序也必须确定。`m5.simulate.memWriteback()` 的 key 从单一整数改为：

```python
(checkpoint_writeback_priority 或 cache_level,
 0 if is_read_only else 1)
```

实际目标顺序：

```text
SLC
  → walker/level-0 private caches
  → L1 I-cache
  → L1 D-cache
  → L2 slices
```

I-cache 先于 D-cache的原因是 self-modifying code 或写后取指场景：可能较旧的只读指令副本不能成为同层最后覆盖者。跨 CPU 的同类 cache 仍使用 Python stable sort 保持原 descendants 顺序；正常 coherence 应保证共享 clean copy 内容一致、dirty owner 唯一。

### 17.23 构建和聚焦测试结果

主构建：

```bash
scons build/RISCV/gem5.opt -j64
```

结果：成功生成 `build/RISCV/gem5.opt`，无 C++/Python/link 错误。构建中的 PNG、HDF5 和 backtrace 提示均为历史可选依赖 warning，与本次代码无关。

测试构建必须使用仓库的 `--unit-test` 开关；第一次遗漏该开关时，测试 TU 看见 `UNIT_TEST` 声明、被测源文件没有对应实现，产生 `corruptInstalledDirtyVictimIdForTest()` undefined reference。用正确命令重建后无需修改生产代码：

```bash
scons --unit-test \
  build/RISCV/mem/cache/CHI/hnf_slcsf_backend.test.opt \
  build/RISCV/mem/cache/CHI/slc_snoop_filter.test.opt \
  build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt \
  build/RISCV/mem/cache/cache_blk.test.opt \
  -j64
```

聚焦测试汇总：

| 测试二进制 | 通过数 | 失败数 |
|---|---:|---:|
| `hnf_slcsf_backend.test.opt` | 24 | 0 |
| `slc_snoop_filter.test.opt` | 17 | 0 |
| `hnf_slcsf.test.opt` | 131 | 0 |
| `cache_blk.test.opt` | 2 | 0 |
| **合计** | **174** | **0** |

新增 backend 用例同时构造一条 dirty SLC line 和一条 clean-valid SLC line，验证：

- dirty visitor 仍只返回 1 条 dirty line；
- valid visitor 返回两条并保持完整数据；
- full-system checkpoint 仍把两路 SLC/SF array 序列化为 canonical zero；
- restore 后不会复活被 consolidation 丢弃的 SLC/SF ownership。

嵌入实际 `gem5.opt` Python 环境的排序 probe 结果：

```text
checkpoint_writeback_order=PASS slc,walker,icache,dcache,l2
```

此外：

```text
python3 -m py_compile src/python/m5/simulate.py \
  util/audit_riscv_checkpoint_stacks.py \
  util/debug_chi_checkpoint.py
```

已通过，相关源码 `git diff --check` 也通过。

### 17.24 09:20 当前精确卡点与下一步门槛

当前不是卡在 Linux 的某个 printk，也不是卡在编译或单元测试。精确状态是：

```text
Linux cold boot             已通过多次
16 CPU online               已通过
cold terminal 命令          已通过
guest checkpoint helper     已通过
第二个 checkpoint 文件       已通过
第二个 checkpoint CHI 审计   已通过
第二个 checkpoint 栈语义     失败（0/15）
all-valid 修复编译/单测       已通过（174/174）
第三个 all-valid checkpoint  尚未生成
最终 restore                尚未执行
restore 后 terminal          尚未验收
```

下一轮计划使用全新目录，避免覆盖前两轮取证：

```text
m5out/linux-16core-128mb-all-valid-cold-detached-20260820/
```

核心命令参数保持不变：16 核、128 MiB、6×4 CHI、SimpleMemory、`--raw-cpt`、同一 19,264,520-byte firmware、`--no-pf`、`-m 120000000000`、单 checkpoint；宿主侧必须继续使用独立 session，否则约 5 小时的 detailed cold boot 会再次被 3 小时交互会话上限截断。

报告生成时，执行平台对新的宿主进程空间检查/持久启动请求返回临时 usage-limit 拒绝，并提示 11:33 后恢复。因此尚未创建第三轮 PID/outdir；报告不会把“准备启动”误写成“已经运行”。这属于即时宿主执行资源阻塞，不是新的 Linux/CHI 代码故障。

第三轮严格 gate：

1. 先确认新日志使用 `valid SLC lines` 和 `valid cache lines`，且实际顺序仍是 SLC → private → L2；
2. helper 退出后立刻执行 gzip、大小、SHA256、canonical SLC/SF 和 CHI mismatch 审计；
3. 运行 15-hart 栈审计；只有 `idle_summary passed=15 total=15` 才允许进入 restore；
4. restore 后扫描 panic/Oops/fault storm，并在正确 terminal 端口输入 `uname -a`、CPU count、online mask、uptime 和 marker；
5. 只有 guest 返回真实命令输出，才满足用户定义的最终验收。

截至本节，最准确的结论是：**原始 Linux 崩溃和 cold terminal 已解决；checkpoint 文件生成也已解决；当前唯一未闭环的核心技术问题是 non-serialized cache 数据能否通过 all-valid ordered consolidation 完整进入 pmem。代码与单测已就绪，但还缺第三轮约 5 小时 cold run 和随后 restore 的端到端证据。**

## 18. 2026-08-24：第三轮 all-valid detached cold run

### 18.1 运行身份与命令

8 月 20 日的临时宿主 usage-limit 已消失。启动前在 host process namespace 中确认没有本工作区遗留的 `gem5.opt`，随后于 08:16 启动全新 detached run：

```text
outdir  = m5out/linux-16core-128mb-all-valid-cold-detached-20260824
PID     = 2606826
start   = 2026-08-24 08:16:45 CST
terminal= 3456
gdb     = 7000
```

完整命令：

```bash
setsid -f build/RISCV/gem5.opt \
  --listener-mode=on \
  --redirect-stdout \
  --redirect-stderr \
  --outdir=m5out/linux-16core-128mb-all-valid-cold-detached-20260824 \
  configs/example/kmhv2_chi_6x4_hnf.py \
  --raw-cpt \
  --generic-rv-cpt=/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.bin \
  --num-cpus=16 \
  --mem-size=128MB \
  --mem-type=SimpleMemory \
  --maxinsts=0 \
  -m 120000000000 \
  --no-pf \
  --max-checkpoints=1 \
  --checkpoint-dir=m5out/linux-16core-128mb-all-valid-cold-detached-20260824/checkpoints
```

与第二轮相比，workload、firmware、16 核、128 MiB、CHI 6×4、SimpleMemory 和仿真上限均未改变；决定性变量是 8 月 20 日已经完成构建和测试的 all-valid checkpoint consolidation。

启动后 host 定量快照：

```text
PID      = 2606826
elapsed  = 00:09:31
CPU time = 00:09:31
CPU      = 99.8%
STAT     = Rs
```

elapsed 和 CPU time 同步增长，表明进程持续执行，不是 sleep、I/O wait 或 zombie。

### 18.2 terminal 和 guest checkpoint 命令已预置

`simerr` 明确记录：

```text
system.terminal: Listening for connections on port 3456
0: system.remote_gdb: listening for remote gdb on port 7000
```

连接 3456 后得到：

```text
==== m5 terminal: Terminal 0 ====
```

terminal attach 被模拟器记录在：

```text
70297305: system.terminal: attach terminal 0
```

已预排队的 guest 动作：

1. 将 552-byte static RISC-V checkpoint helper 通过 base64 写入 `/tmp/chi_guest_checkpoint_repo`；
2. `chmod 755`；
3. 输出 `ALL_VALID_COLD_SHELL_20260824`；
4. 挂载 `/proc` 和 `/sys`；
5. 输出 `uname -a`；
6. 输出 CPU count、online mask、uptime、shell PID；
7. 在 guest 内计算 helper SHA256；
8. `sync`；
9. 输出 `ALL_VALID_TRIGGER_CHECKPOINT_20260824`；
10. 执行 helper，由 guest 触发 checkpoint。

预期 helper hash 仍为：

```text
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
```

这消除了上轮 `/bin/m5_checkpoint: not found` 的路径不确定性，也避免在 cold run 到 shell 后再依赖人工及时连接。

### 18.3 独立 checkpoint 审计 watcher

新增并启动：

```text
util/watch_all_valid_checkpoint_20260824.sh
```

watcher 日志起点：

```text
2026-08-24 08:23:26 CST watcher started for gem5 pid=2606826
```

它只做以下工作，不修改 checkpoint，也不会擅自启动 restore：

- 每 60 秒确认 guest 是否以 `because checkpoint` 正常退出；
- 若 PID 提前消失，写入失败原因并退出；
- 检查 `m5.cpt` 和 compressed pmem 非空；
- `gzip -t`；
- 记录 compressed/raw SHA256 与文件大小；
- 解压 128 MiB raw pmem 到 `/tmp/all-valid-cpt-audit-20260824/pmem.raw`；
- 运行 `debug_chi_checkpoint.py`；
- 运行 `audit_riscv_checkpoint_stacks.py`；
- 把每一项状态写到 run 下的 `audit/` 目录。

最终关键文件将是：

```text
audit/gzip.status
audit/compressed.sha256
audit/raw.sha256
audit/sizes.txt
audit/chi_audit.status
audit/chi_audit.txt
audit/stack_audit.status
audit/stack_audit.txt
audit/watcher.log
```

### 18.4 08:34–08:38：第三轮再次越过原始 Linux 崩溃窗口

raw firmware 已加载到 pmem，随后进入 real simulation：

```text
Restored from Xiangshan RISC-V Checkpoint
Entering event queue @ 0. Starting simulation...
```

Linux banner：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ... #69 SMP ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI specification v2.0 detected
[    0.000000] SBI TIME extension detected
[    0.000000] SBI IPI extension detected
[    0.000000] SBI RFENCE extension detected
[    0.000000] SBI DBCN extension detected
[    0.000000] earlycon: sbi0 at I/O port 0x0
```

固件阶段结束后的 event queue 起点与前轮一致：

```text
Entering event queue @ 1688382900. Starting simulation...
```

决定性的原始 crash 窗口：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
[    0.000000] percpu: Embedded 14 pages/cpu s27304 r0 d30040 u57344
```

原用户日志在 `ELF capabilities` 后立刻出现：

```text
Unable to handle kernel NULL pointer dereference at virtual address 0x4
Oops [#1]
```

第三轮已经打印下一条 `percpu`，且全量 `simout/simerr` 未匹配 `Unable to handle kernel NULL pointer`、`Oops [#`、panic 或 fatal。因此 all-valid 代码没有引入 early-boot 回归，原始 Linux 崩溃在第三轮仍保持解决。

### 18.5 当前剩余 gate

截至 08:56，本轮已完成 16 CPU SMP bring-up，但还没有到 shell 或 checkpoint，`checkpoints/` 为空属于阶段预期。剩余顺序更新为：

```text
16 CPU SMP bring-up（已通过）
  → hvc0 / drivers / clocks（当前）
  → /bin/sh
  → terminal 命令真实返回
  → guest helper checkpoint
  → all-valid writeback 日志
  → gzip/hash/CHI audit
  → idle_summary passed=15 total=15
  → restore
  → restore 后 terminal 命令闭环
```

在 `15/15` 栈 gate 之前仍禁止启动 restore；只有第三轮 artifact 同时通过语法完整性和语义完整性，才是合格的最终恢复输入。

### 18.6 08:55：第三轮 16 CPU SMP bring-up 通过

长期监视器在 08:55:10 CST 捕获到：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

这个 guest 时间戳与既有成功冷启动完全一致，说明 15 个 secondary hart 并非只在设备树中声明，而是已经由 Linux 逐个拉起并完成 SMP 汇合。此前 `simerr` 也为每个 hart 打印了 CSR 初始化和 `enable SV48` 过程；这段耗时较长是 detailed O3 + CHI 的正常模拟开销，不是内核卡死。

08:52 的宿主进程空间复核为：

```text
PID      = 2606826
elapsed  = 00:32:50
CPU time = 00:32:50
CPU      = 99.8%
STAT     = Rs
```

一度在沙箱内执行 `ps -p 2606826` 没有输出，原因是沙箱 PID namespace 隔离；切换到宿主进程空间后，同一 PID 明确存在并持续满核运行。这不是 gem5 退出，也不是新的 Linux/CHI 故障。

截至 08:56，全量日志仍未命中：

```text
Unable to handle kernel NULL pointer
Oops [#
Kernel panic
panic:
fatal:
```

因此第三轮当前精确位置是：**Linux 已越过原始崩溃点并完成 16 CPU bring-up，正在 SMP 之后、`hvc0`/驱动初始化和 `/bin/sh` 之前继续执行。** 下一项可见 gate 是 `Run /bin/sh as init process`，随后才会消费已经缓存在 terminal 3456 的 guest 验收命令并触发 checkpoint。

### 18.7 严格审计后自动 restore gate

为避免约五小时 cold run 完成后再依赖人工及时接管，同时保证坏 checkpoint 绝不会被误恢复，新增：

```text
util/watch_all_valid_restore_20260824.sh
```

该监督器已于 09:03 在独立宿主 session 中启动：

```text
PID = 2729060
log = m5out/linux-16core-128mb-all-valid-cold-detached-20260824/audit/restore_gate.log
```

它与只读 checkpoint watcher 分工明确：checkpoint watcher 负责生成审计文件；restore gate 只读取结果并做以下硬性判断：

```text
gzip.status       必须严格等于 PASS
chi_audit.status  必须严格等于 PASS
stack_audit.status 必须严格等于 PASS
stack_audit.txt   必须包含精确的 idle_summary passed=15 total=15
cold simout       必须包含 Exiting @ tick ... because checkpoint
cold checkpoints  必须恰好只有一个 cpt.* 目录
```

任一条件失败，监督器只记录 `FAIL` 并退出，不会启动 restore。全部通过后才会用相同的 16 核、128 MiB、6×4 CHI、SimpleMemory、`--no-pf` 配置执行：

```text
-r 1
--checkpoint-dir=<第三轮 cold checkpoints 父目录>
--restore-terminal-wait=120
```

恢复输出目录固定为新的、不可与输入混用的目录：

```text
m5out/linux-16core-128mb-all-valid-restore-detached-20260824
```

监督器会从 `simerr` 动态解析实际 terminal 端口，验证 `m5 terminal: Terminal 0` 握手后预置以下 guest 验收输出：

```text
FINAL_RESTORE_TERMINAL_ACCEPTED_20260824
FINAL_RESTORE_UNAME=<真实 uname -a 输出>
FINAL_RESTORE_CPU_COUNT=16
FINAL_RESTORE_CPU_ONLINE=0-15
FINAL_RESTORE_UPTIME=<真实 /proc/uptime 输出>
FINAL_RESTORE_SHELL_PID=<真实 shell PID>
FINAL_RESTORE_HELPER_SHA256=53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
FINAL_RESTORE_COMMANDS_DONE_20260824
```

只有这些干净输出行实际出现在去除 CR 后的 guest `simout` 中，才算端口输入执行成功；仅有 nc 本地输入回显不能通过。命令完成后，guest 会再次执行同一 checkpoint helper，要求恢复运行以 `because checkpoint` 正常退出。监督器还会对这个 post-restore checkpoint 再做 gzip、SHA256、CHI canonical/mismatch 和精确 15/15 栈审计，最终只在整个闭环通过时写入：

```text
m5out/linux-16core-128mb-all-valid-restore-detached-20260824/audit/final_acceptance.status = PASS
```

截至 09:05，cold gem5 PID `2606826`、原 terminal client 和 restore gate PID `2729060` 均仍存活；第三轮 guest 已继续打印：

```text
[    0.005231] devtmpfs: initialized
[    0.006358] futex hash table entries: 4096 ...
[    0.006605] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.006770] DMA: preallocated 128 KiB GFP_KERNEL pool ...
[    0.006844] DMA: preallocated 128 KiB GFP_KERNEL|GFP_DMA32 pool ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
```

这说明当前不是停在 SMP bring-up，而是在其后的正常内核初始化路径持续前进。

### 18.8 restore 绝对 tick 上限边界修正

启动 gate 后再次核对历史 checkpoint tick，发现第二轮输入位置为：

```text
cpt.94883960936
```

若 restore 仍使用原 cold run 的绝对上限 `-m 120000000000`，恢复后只剩：

```text
120000000000 - 94883960936 = 25116039064 ticks
```

虽然最终命令不再需要重新注入 552-byte helper，但仍要等待 HVC 轮询、执行多条 shell 命令、输出 terminal 证据并再次触发 checkpoint，25.1G tick 的余量存在不必要的边界失败风险。一旦命中，该失败原因会是 `simulate() limit reached`，不能用于判断 checkpoint 修复是否正确。

因此在 cold checkpoint 尚未生成、旧 gate 尚未启动任何 restore 的安全窗口执行了以下调整：

1. 停止仅处于 `sleep` 等待状态的旧 gate PID `2729060`；
2. 确认 cold gem5 PID `2606826` 未受影响，仍为 `Rs`、CPU `99.8%`；
3. 将 restore 命令改为 `-m 200000000000`；
4. `bash -n` 和 `git diff --check` 均通过；
5. 将旧锁目录保留为 `restore_gate.lock.pre_200g`，不删除取证；
6. 09:10 重新启动新 gate PID `2749092`。

新的余量按同一历史 tick 估算约为：

```text
200000000000 - 94883960936 = 105116039064 ticks
```

这只扩大恢复后的观测窗口，不改变 checkpoint 输入、16 核微结构、CHI 拓扑、内存大小、cache 策略或 guest 行为，因而不会削弱最终验收的等价性。

### 18.9 当前启动前缀与两次成功 cold run 逐行一致

`clocksource` 之后一段时间没有新 printk。为避免只根据“PID 还活着”把真实行为偏离误判为正常慢速，进一步对比了三份 artifact：

```text
第三轮 all-valid：
m5out/linux-16core-128mb-all-valid-cold-detached-20260824/simout

第二轮成功到 shell/checkpoint：
m5out/linux-16core-128mb-slc-first-cold-detached-20260820/simout

早期成功到 shell/checkpoint：
m5out/linux-16core-128mb-drainfix-native-cold-20260815/simout
```

从以下共同起点开始：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ...
```

到当前终点：

```text
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
```

三份日志各有 **73 行**。去除串口 CR 后，将第三轮这 73 行分别与两份已知成功日志执行 `diff -u`，两次 diff 均为零输出、退出码 0，即逐字节一致。

同时宿主状态为：

```text
PID      = 2606826
elapsed  = 00:53:23
CPU time = 00:53:23
CPU      = 99.8%
STAT     = Rs
```

因此当前“暂时没有新 printk”的最强证据解释是：第三轮正处于与历史成功路径相同的长 initcall 计算区间，而不是出现了新的 Linux 控制流偏离、gem5 sleep 或进程退出。

两份成功日志在该点之后的下一条输出也完全相同，因而下一项对照 gate 已明确为：

```text
[    0.010783] NET: Registered PF_INET protocol family
```

其后预期依次为 TCP/UDP/PF_UNIX、I/O scheduler、`hvc0`、串口/SDHCI/IPv6、unused clocks、释放 initmem，最终到 `/bin/sh`。在第三轮实际打印这些行之前，报告不会把预期日志误写成已经通过的证据。

### 18.10 PF_INET / TCP / UDP / PF_UNIX gate 已通过

09:16–09:19，第三轮实际打印：

```text
[    0.010783] NET: Registered PF_INET protocol family
[    0.010855] IP idents hash table entries: 2048 ...
[    0.011009] tcp_listen_portaddr_hash hash table entries: 256 ...
[    0.011066] Table-perturb hash table entries: 65536 ...
[    0.011119] TCP established hash table entries: 1024 ...
[    0.011173] TCP bind hash table entries: 1024 ...
[    0.011227] TCP: Hash tables configured (established 1024 bind 1024)
[    0.011308] UDP hash table entries: 256 ...
[    0.011354] UDP-Lite hash table entries: 256 ...
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011490] PCI: CLS 0 bytes, default 64
```

这不是预期值，而是 terminal 3456 和 `simout` 同时观察到的真实 guest 输出。此时从 Linux banner 起共有 **84 行**；再次去除 CR 后，分别与 SLC-first 成功 run 和 drainfix 成功 run 的前 84 行执行 `diff -u`，两次仍为零输出、退出码 0。

因此第三轮已经实际越过第 18.9 节定义的 PF_INET gate，并继续保持与两份成功路径逐行一致。下一项尚未通过的 gate 更新为：

```text
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
```

### 18.11 workingset 与 I/O scheduler gate 已通过

在第 18.10 节更新后，terminal 立即收到下一组真实 guest 输出：

```text
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

因此第 18.10 节定义的下一 gate 已通过。根据两份成功日志，当前到下一个可见大节点之间是较长的 console-driver 初始化区间；新的权威 gate 为：

```text
[    0.020703] printk: legacy console [hvc0] enabled
```

在该行实际出现之前，当前准确状态是 **I/O scheduler 已完成，hvc0 尚未启用**。

### 18.12 09:20–10:08：hvc0 前长 initcall 区间持续执行

截至 10:08，第三轮尚未打印下一条 `hvc0`，但也没有任何 crash、退出或 checkpoint 事件。为判断是“正常慢”还是“进程假活”，在宿主进程空间持续采样：

| 宿主时间 | elapsed | CPU time | CPU | STAT | 最新 guest 日志 |
|---|---:|---:|---:|---:|---|
| 09:21 | 约 01:01:05 | 01:01:01 | 99.8% | `Rs` | `[0.012178] io scheduler bfq registered` |
| 09:40 | 约 01:20:03 | 01:19:58 | 99.8% | `Rs` | 同上 |
| 09:57 | 约 01:36:35 | 01:36:28 | 99.8% | `Rs` | 同上 |
| 10:08 | 约 01:47:45 | 01:47:37 | 99.8% | `Rs` | 同上 |

四次采样中 elapsed 与 CPU time 的增量持续一一对应，CPU 始终接近单核满载，进程状态始终为 runnable。只读长监视器同时持续检查：

```text
legacy console [hvc0]
Unable to handle kernel NULL pointer
Oops [#
Kernel panic
panic:
fatal:
Exiting @ tick
```

截至本节均未命中。因此当前不能把 `hvc0` 写成已通过，但也没有证据支持 deadlock、sleep、zombie、Linux crash 或 gem5 提前退出。准确结论仍是：**第三轮正在 I/O scheduler 之后、hvc0 之前的详细 O3/CHI 计算区间持续推进。**

### 18.13 hvc0 前静默时长与历史成功 run 对照

进一步回查报告中两次成功 cold run 的宿主时间记录，可以直接量化当前静默区间是否异常。

第一轮权威 ordered cold run 在 20:22 的最新 guest 行同样是：

```text
[    0.012178] io scheduler bfq registered
```

当时宿主 CPU time 已为 `01:24:57`。直到 21:44 更新，才真实出现：

```text
[    0.020703] printk: legacy console [hvc0] enabled
```

也就是说，仅报告可见的保守墙钟区间就约为 **82 分钟**，并且该 run 随后继续成功到 shell/checkpoint。

第二轮持久重跑也在 04:29、elapsed/CPU time `01:01:22` 时停在同一条 bfq 日志；随后正常通过 hvc0、驱动和 clocks，并最终生成 `cpt.94883960936`。报告对该阶段的历史结论也是“已知长 initcall 区间”，不是死锁。

第三轮本次 bfq 行的宿主 mtime 为 09:16:43；截至 10:14，静默约 58 分钟，仍短于第一轮已明确观测到的约 82 分钟对照。同时当前宿主状态为：

```text
elapsed  = 约 01:54:13
CPU time = 01:54:05
CPU      = 99.8%
STAT     = Rs
```

因此当前静默时长本身也处于已成功历史 run 的正常范围。仍然坚持证据边界：只有第三轮实际打印 hvc0 才会标记该 gate 通过，但在此之前没有理由中止或重跑。

### 18.14 10:19：hvc0 gate 已真实通过

只读长监视器在 10:19:57 CST 返回：

```text
[    0.020703] printk: legacy console [hvc0] enabled
```

随后 terminal 3456 和 `simout` 均实际得到：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
```

`hvc0 enabled` 和 `bootconsole disabled` 各出现两次，是 Linux 从 early SBI console 向正式 hvc console 切换时的正常 replay，与成功 baseline 完全一致，不是重复启动。

第三轮 bfq 行的宿主 mtime 为 09:16:43，hvc0 监视器在 10:19:57 触发，实际静默约 **63 分 14 秒**；这与第 18.13 节第一轮约 82 分钟的成功历史范围吻合。

10:24 的宿主状态：

```text
PID      = 2606826
elapsed  = 02:03:34
CPU time = 02:03:26
CPU      = 99.8%
STAT     = Rs
```

同一时刻全量 `simout/simerr` 未匹配 NULL dereference、Oops、panic 或 fatal。因此 hvc0 gate 现已从“预期”升级为 **第三轮实测通过**。

当前下一组尚待实际确认的节点为：

```text
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.026816] clk: Disabling unused clocks
[    0.038617] Run /bin/sh as init process
```

其中 UARTLite `-ENXIO` 是两份成功 baseline 都存在的已知非致命 warning；只有后续文本/时间戳偏离或伴随 panic 时才视为新故障。

### 18.15 10:22：UARTLite 节点通过，进程与双 gate 仍健康

第三轮 `simout` 在 10:22:53 CST 继续写入：

```text
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
```

该文本、地址、错误码和 guest 时间戳均与两份最终成功到 shell 的 baseline 一致，因此它不是本轮新 crash，也不是 all-valid 修复引入的回归。当前下一项尚未实测通过的节点收窄为：

```text
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
```

10:27 对已知 PID 做精确宿主快照：

```text
gem5 PID = 2606826
elapsed  = 02:06:52（约值）
CPU time = 02:06:43
CPU      = 99.8%
STAT     = Rs
watcher  = PID 2613928，仍在等待冷 checkpoint
gate     = PID 2749092，STAT Ss / WCHAN do_wait，仍在等待精确 15/15
```

`pgrep` 同时返回了完整且未变的第三轮 gem5 命令行。checkpoint 目录和任何 audit PASS 文件此时仍未生成，restore outdir 也尚不存在，这与“guest 尚未到 shell/helper、两个 gate 只等待不抢跑”的设计一致。全量日志仍未命中 NULL dereference、Oops、panic、fatal 或 `Exiting @ tick`。

### 18.16 10:31：SDHCI 驱动组继续通过

下一轮只读监视器实际命中：

```text
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
```

terminal 3456 随后返回完整的连续输出：

```text
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
```

监视命令末尾的 `tail: write error: Broken pipe` 是 `rg -m 1` 命中第一条 SDHCI 行后主动退出、上游 `tail` 才失去管道读端；它不在 gem5 的 `simout/simerr` 中，也不代表 guest 或仿真错误。

10:31 对 `simout/simerr` 再次执行全量 crash scan，NULL dereference、Oops、panic、fatal 和 `Exiting @ tick` 均为零命中。当前准确位置由“UARTLite 后、SDHCI 前”推进为 **SDHCI 驱动组内/之后**；下一关键 gate 是 PF_INET6，然后是 unused clocks 和 `/bin/sh`。

### 18.17 10:33：PF_INET6 gate 通过

第三轮 terminal 3456 和文件监视器均实际得到：

```text
[    0.022300] NET: Registered PF_INET6 protocol family
```

通过时的精确宿主快照为：

```text
gem5 PID 2606826: elapsed=02:12:59, CPU time=02:12:50, CPU=99.8%, STAT=Rs
watcher PID 2613928: STAT=Ss, WCHAN=do_wait
restore gate PID 2749092: STAT=Ss, WCHAN=do_wait
```

这说明冷启动持续推进，watcher 与 restore gate 仍严格等待，且没有在 checkpoint 生成前误启动 restore。根据两份成功 baseline，下一批关键行是 late driver/initcall、`clk: Disabling unused clocks` 和 `Run /bin/sh as init process`。

### 18.18 10:39：IPv6 late-driver 连续序列通过

terminal 3456 在 PF_INET6 之后继续返回：

```text
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
```

三行文本及 guest 时间戳均与两份成功 baseline 一致。随后一个 5 分钟只读监视窗口没有出现 `clk: Disabling unused clocks`，也没有出现 crash/exit；窗口以 `timeout` 返回码 1 结束，只表示本轮正则没有命中。

10:39 宿主快照：

```text
gem5 PID 2606826: elapsed=02:18:48, CPU time=02:18:39, CPU=99.8%, STAT=Rs
watcher PID 2613928: STAT=Ss, WCHAN=do_wait
restore gate PID 2749092: STAT=Ss, WCHAN=do_wait
```

同期全量 crash scan 仍为零命中。当前精确位置是 **tunnel driver 之后、unused clocks 之前**。成功持久 run 已证明后续 `clk` 到 `/bin/sh` 还能消耗约 2 小时 23 分墙钟，所以 late-init 静默本身不构成死锁证据。

### 18.19 10:45：tunnel→clocks 静默窗口复核

在第 18.18 节之后又完成一个连续 5 分钟的只读监视窗口，检查目标包括：

```text
clk: Disabling unused clocks
Run /bin/sh
ALL_VALID_COLD_SHELL
ALL_VALID_TRIGGER_CHECKPOINT
Unable to handle kernel NULL pointer
Oops [#
Kernel panic / panic: / fatal:
Exiting @ tick
```

本窗口无命中；重新读取文件末尾，最后一行仍是：

```text
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
```

10:42 的中间宿主采样已经确认 gem5 CPU time 增长到 `02:21:48`、CPU `99.8%`、状态 `Rs`，checkpoint 目录仍为空，两个 gate 仍在 `do_wait`。结合成功持久 run 大约在宿主运行 3 小时时进入 clocks、约 5 小时 24 分进入 shell，当前粗略预计还需约半小时到 clocks，之后还可能需要约 2 小时多到 shell。该数值只用于解释详细模型成本，不能替代第三轮实际日志，也不作为超时失败判据。

### 18.20 10:45–11:03：连续监视仍证明进程前进

在 10:45 后连续执行多轮 5 分钟只读监视，均检查 clocks、shell、guest marker、checkpoint、Linux crash 和 gem5 exit。各窗口都没有命中目标，重新锚定 `simout` 后最后一行仍为 tunnel driver；checkpoint 目录也仍为空。

宿主采样序列：

| 时间 | gem5 CPU time | CPU | STAT | watcher/gate |
|---|---:|---:|---:|---|
| 10:49 | `02:28:50` | 99.8% | `Rs` | 均为 `do_wait` |
| 10:52 | `02:31:49` | 99.8% | `Rs` | 均为 `do_wait` |
| 10:55 | `02:34:20` | 99.8% | `Rs` | 均为 `do_wait` |
| 10:58 | `02:37:18` | 99.9% | `Rs` | 均为 `do_wait` |
| 11:00 | `02:39:49` | 99.9% | `Rs` | 均为 `do_wait` |
| 11:03 | `02:42:36` | 99.9% | `Rs` | 均为 `do_wait` |

CPU time 在 14 分钟采样跨度内增加约 13 分 46 秒，且进程始终 runnable。这是强于单次 `ps` 的连续证据：当前没有 sleep、zombie、退出或明显宿主假活。准确卡点仍是 **详细 O3+CHI 模型执行 tunnel driver 到 unused clocks 之间的 late initcall 所需墙钟时间**，尚未出现新的功能 bug。

### 18.21 11:20：unused-clocks gate 已真实通过

事件监视器和 terminal 3456 均实际得到：

```text
[    0.026816] clk: Disabling unused clocks
```

该文本和 guest 时间戳与两份最终成功到 shell 的 baseline 完全一致。文件 `simout` 对应修改时间为 `11:16:22.613340 +0800`；由于文件系统时间戳与监视命令显示的宿主时间存在约 4 分钟固定偏差，本节同时保留两者，不用其中一个覆盖另一个。

11:20 的精确宿主状态：

```text
gem5 PID 2606826: elapsed=02:59:59, CPU time=02:59:49, CPU=99.9%, STAT=Rs
watcher PID 2613928: STAT=Ss, WCHAN=do_wait
restore gate PID 2749092: STAT=Ss, WCHAN=do_wait
```

同期全量 crash scan 对 NULL dereference、Oops、panic、fatal 和 `Exiting @ tick` 均为零命中。当前精确位置由“tunnel driver 后、clocks 前”推进为：

> **unused clocks 已完成，正在等待 `Freeing unused kernel image (initmem)` 和 `Run /bin/sh as init process`。**

成功持久 run 在 clocks 后约 2 小时 23 分才进入 shell，因此下一段仍预期是长静默；这次不会因静默误杀或重启。guest 一旦出现 shell，terminal 中已排队的验收命令会立即运行并触发第三个 all-valid checkpoint。

### 18.22 11:21–12:02：clocks 后连续健康采样

unused-clocks gate 通过后，对 initmem、shell、guest marker、checkpoint、Linux crash 和 gem5 exit 持续执行 5 分钟滚动监视。到 12:02 为止所有事件窗口均没有命中，`simout` 末尾仍是正常的 clocks 行，checkpoint 目录为空，watcher 与 restore gate 均保持 `do_wait`。

代表性宿主采样：

| 时间 | gem5 CPU time | CPU | STAT | crash/exit | checkpoint |
|---|---:|---:|---:|---|---|
| 11:21 | `03:00:41` | 99.9% | `Rs` | 零命中 | 未生成 |
| 11:29 | `03:08:35` | 99.9% | `Rs` | 零命中 | 未生成 |
| 11:38 | `03:17:00` | 99.9% | `Rs` | 零命中 | 未生成 |
| 11:46 | `03:25:01` | 99.9% | `Rs` | 零命中 | 未生成 |
| 11:54 | `03:33:22` | 99.9% | `Rs` | 零命中 | 未生成 |
| 12:02 | `03:41:21` | 99.9% | `Rs` | 零命中 | 未生成 |

41 分钟墙钟跨度内 CPU time 增加约 40 分 40 秒，进程始终 runnable。它证明 clocks 后静默不是 sleep、zombie、宿主进程假活或 Linux 已崩溃；当前唯一卡点仍是详细 O3+CHI 模型跑完最后约 `0.0118s` guest kernel time 所需的墙钟时间。成功 baseline 的 clocks→shell 约 2 小时 23 分对照仍适用，当前尚远未超过该范围。

### 18.23 13:45：第三轮完整进入 shell，并由 guest 正常生成 checkpoint

clocks 后的长静默最终正常结束。第三轮 `simout` 实际打印：

```text
[    0.038540] Freeing unused kernel image (initmem) memory: 4112K
[    0.038615] Run /bin/sh as init process
```

此前在 terminal 3456 排队的 helper 和验收命令随即由真实 guest shell 执行。去除串口 CR 后的权威输出为：

```text
ALL_VALID_COLD_SHELL_20260824
ALL_VALID_PROC_SYS_READY_20260824
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
ALL_VALID_CPU_COUNT=16
ALL_VALID_CPU_ONLINE=0-15
ALL_VALID_UPTIME=0.05 0.78
ALL_VALID_SHELL_PID=137
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38  /tmp/chi_guest_checkpoint_repo
ALL_VALID_TRIGGER_CHECKPOINT_20260824
Writing checkpoint
Exiting @ tick 67032562072 because checkpoint
```

这些行全部位于 gem5 的 `simout`，不是 nc 的宿主本地回显。它们证明第三轮同时满足：

1. Linux 6.10.7 完整进入 PID 1 `/bin/sh`；
2. `/proc` 和 `/sys` 可挂载；
3. 16 个 processor 均可见，online mask 为 `0-15`；
4. terminal 输入确实被 guest shell 执行；
5. 552-byte checkpoint helper 的 SHA256 与预期完全一致；
6. checkpoint 由 guest 主动触发，并通过正常 `because checkpoint` 路径退出。

因此第三轮后续失败不能归因于 Linux 没启动、terminal 没连上、helper 被截断或宿主强制结束。

### 18.24 `cpt.67032562072` 文件、writeback 和 CHI 审计

第三轮 artifact：

```text
m5out/linux-16core-128mb-all-valid-cold-detached-20260824/
└── checkpoints/
    └── cpt.67032562072/
        ├── m5.cpt
        └── system.physmem.store0.pmem
```

文件与 hash：

| 对象 | 字节数 | SHA256 |
|---|---:|---|
| `m5.cpt` | 39,532,598 | `baac158fce97a4e9c6214ce5baac9b30338d30375ef75e5c8f966113942bab53` |
| gzip pmem | 5,986,113 | `ca52f108a8b3f270b9416eb97816f461a5081380067ee7678594025eff9cc689` |
| raw pmem | 134,217,728 | `e10dfb444539b1bcbda65be5c9df027159ab4a7036106a3e6769fdd34d5317d0` |

`gzip -t` 返回 0，raw 大小精确等于配置的 128 MiB。第三轮日志还证明 all-valid 代码真实执行，而不是只完成了构建：

| 层级 | 实际写回 valid line 数 |
|---|---:|
| 16 个 HN-F/SLC | 303,593 |
| 32 个 ITB/DTB walker cache | 281 |
| 16 个 I-cache | 16,376 |
| 16 个 D-cache | 12,808 |
| 64 个 L2 slice | 180,749 |
| classic cache 合计 | 210,214 |

离线 CHI 审计结果：

```text
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

这证明 checkpoint 文件结构完整、SLC/SF canonical empty 策略生效、所有有效 cache line visitor 也确实运行；但它仍不能单独证明同一地址多个副本的最后覆盖者正确。

### 18.25 决定性失败：第三轮 all-valid 栈审计仍为 0/15

独立 watcher 在 13:49 检测到 checkpoint，gzip 与 CHI audit 均写入 `PASS`，但 stack audit 返回：

```text
idle_summary passed=0 total=15
```

严格 restore gate 随即在 13:50 记录：

```text
FAIL: cold-checkpoint stack_audit.status is not PASS
```

因此没有创建 restore outdir，也没有用一个已知语义失败的 checkpoint 抢跑最终验收。这是门禁的预期行为，不是 restore launcher 自身崩溃。

第三轮与第二轮不同的异常特征是多个虚拟栈被 pmem 页表映射到相同 physical 地址：

| CPU 组 | 各 CPU 的虚拟 SP | pmem walk 最终地址 | pmem saved RA |
|---|---|---|---:|
| 0–2 | 分别为 `...10be60`、`...113e60`、`...11be60` | 全部 `0x8158ee60` | `0x3` |
| 4–7 | `...123e60` 至 `...13be60` | 全部 `0x8159ee60` | `0x7` |
| 8–11 | `...143e60` 至 `...15be60` | 全部 `0x815cee60` | `0x0` |
| 12、13、15 | `...163e60`、`...16be60`、`...17be60` | 全部 `0x815dee60` | `0xf` |

CPU14 正在用户态 helper `PC=0x100b8`，所以正确地不计入 15 个 idle hart。CPU3 的 kernel direct-map 栈也失败，pmem walk 落到旧代码页 `0x80203e10`，而不是栈页。

### 18.26 SATP 与 DTLB 交叉验证：排除审计器误报

首先重新从 `src/arch/riscv/regs/misc.hh` 机械计数 `MiscRegIndex`，确认：

```text
MISCREG_SATP index=143
```

CPU0 checkpoint 中该值为：

```text
SATP = 0x900000000008126a
mode = 9 (Sv48)
```

因此审计器默认 `--satp-index=143` 正确。更强的证据来自 `m5.cpt` 自带 DTLB entry。对每个 CPU 的 SP 查找覆盖它的 serialized DTLB entry，并与同一 checkpoint raw pmem 的 Sv48 walk 比较：

| CPU | DTLB physical | pmem page-walk physical | 一致性 |
|---:|---:|---:|---:|
| 00 | `0x81584e60` | `0x8158ee60` | FAIL |
| 01 | `0x8158ae60` | `0x8158ee60` | FAIL |
| 02 | `0x8158ee60` | `0x8158ee60` | PASS（恰为组内最后页） |
| 03 | `0x81203e10` | `0x80203e10` | FAIL |
| 04 | `0x81592e60` | `0x8159ee60` | FAIL |
| 05 | `0x81596e60` | `0x8159ee60` | FAIL |
| 06 | `0x8159ae60` | `0x8159ee60` | FAIL |
| 07 | `0x8159ee60` | `0x8159ee60` | PASS（组内最后页） |
| 08 | `0x815c2e60` | `0x815cee60` | FAIL |
| 09 | `0x815c6e60` | `0x815cee60` | FAIL |
| 10 | `0x815cae60` | `0x815cee60` | FAIL |
| 11 | `0x815cee60` | `0x815cee60` | PASS（组内最后页） |
| 12 | `0x815d2e60` | `0x815dee60` | FAIL |
| 13 | `0x815d6e60` | `0x815dee60` | FAIL |
| 14 | `0x80a65d70` | unmapped | helper 用户态上下文 |
| 15 | `0x815dee60` | `0x815dee60` | PASS（组内最后页） |

CPU0 的具体页表路径为：

```text
L3 PTE @ 0x8126a8f8 = 0x0000000020500401
L2 PTE @ 0x81401000 = 0x0000000020525401
L1 PTE @ 0x81495000 = 0x0000000020525801
L0 PTE @ 0x81496858 = 0x00000000205638e7
pmem walk             = 0x8158ee60
serialized CPU0 DTLB  = 0x81584e60
```

这组对照说明：

1. SATP、Sv48 level/index 解析没有偏一；
2. DTLB 在 checkpoint 时仍保存运行中实际使用的每 hart 独立映射；
3. consolidated pmem 的 PTE 已回退为更旧、成组别名的值；
4. 恢复后即使初始 DTLB 暂时掩盖问题，后续 `sfence.vma`、TLB miss 或 timer continuation 仍会读坏页表并崩溃。

所以 `0/15` 是真实 checkpoint 语义损坏，不能通过放宽审计器或绕过 restore gate 解决。

### 18.27 all-valid 假设被证伪：集合完整，但覆盖优先级仍错误

第三轮修复的原假设是：

```text
所有 valid 下层副本先写
所有 valid 上层副本后写
=> 最终 pmem 一定是最新值
```

这只在“同层所有 shared clean copy 字节完全一致”时成立。当前 classic/CHI 集成中同一 line 可能经历 ownership handoff；`checkpointDirty` 是 sticky 历史事实，不等于当前 owner。旧 owner 的 `DirtyBit` 已清，但它仍可能保留旧数据。all-valid 又让所有完全 clean 的历史副本参与覆盖。跨 16 个 RN-F/L2 的 Python stable traversal 只保证确定性，不保证最后一个对象就是当前 owner。

第三轮成组 PTE 别名恰好证明了这个机制：每个正确 DTLB 映射仍在，但某个后遍历 cache 的旧页表 block 成为最终 pmem 值。故修复方向不能再是“写更多 line”，而必须是：

```text
同一 cache level 内：
  shared / former-owner copy
      → clean Writable owner
      → Dirty owner
```

更高优先级阶段必须在该 level 的所有 cache 对象之间全局展开，而不能让每个 cache 先独立完成全部阶段，否则后一个对象的低优先级 copy 仍会覆盖前一个对象的 owner。

### 18.28 phased-owner 修复、构建与测试

#### 18.28.1 block 优先级

`CacheBlk::checkpointWritebackPhase()` 新增三类：

```text
phase 0: !DirtyBit && !WritableBit
         shared clean、只读副本、former-owner sticky copy

phase 1: !DirtyBit && WritableBit
         当前 clean exclusive/writable owner

phase 2: DirtyBit
         当前 dirty/owned/modified 数据
```

`checkpointDirty` 不参与 owner 排序。它仍用于诊断和 durable-history 统计，但 former owner 必须在当前 owner 前落盘。

#### 18.28.2 每层全局 phase

`SimObject` 新增可选接口：

```cpp
memWritebackPhaseCount()
memWritebackPhase(unsigned phase)
```

默认返回 0，Ruby、CPU 和其他现有 SimObject 继续使用单次 `memWriteback()`，不改变行为。`BaseCache` opt-in 三阶段。`m5.simulate.memWriteback()` 现在先按既有 hierarchy priority 分组，再对同一 level 执行：

```text
所有 cache 的 phase 0
→ 所有 cache 的 phase 1
→ 所有 cache 的 phase 2
```

完整跨层顺序为：

```text
SLC all-valid
→ walker level 0 的 p0/p1/p2
→ L1 I-cache/D-cache 的 p0/p1/p2（每个 phase 内 I-cache 先）
→ 64 个 L2 slice 的 p0/p1/p2
```

这保留了 SLC-first、upper-to-lower 和 I-before-D 三个已证明必要的条件，同时补上“当前 owner 最后”这一缺失条件。

#### 18.28.3 验证结果

主构建：

```text
scons build/RISCV/gem5.opt -j64
exit = 0
build/RISCV/gem5.opt = 931,304,896 bytes
```

可选 PNG、HDF5 和 backtrace warning 与此前相同，不是本次编译错误。

新增 `CacheBlkCheckpointDirtyTest.WritebackPhaseTracksCurrentOwner` 验证 sticky former owner 为 p0、clean Writable 为 p1、Dirty 为 p2；后续又增加 `WritebackPhaseMatchesMoesiPrecedence`，显式验证 Shared→p0、Exclusive→p1、Modified/Owned→p2。四个聚焦二进制结果：

| 测试 | 通过 | 失败 |
|---|---:|---:|
| HnfSLCSFBackend | 24 | 0 |
| SlcSnoopFilter | 17 | 0 |
| HnfSLCSF | 131 | 0 |
| CacheBlk | 4 | 0 |
| **合计** | **176** | **0** |

嵌入新 `gem5.opt` 的实际 Python probe 输出：

```text
checkpoint_writeback_phase_order=PASS
slc:legacy,
walker1:p0,walker0:p0,walker1:p1,walker0:p1,walker1:p2,walker0:p2,
icache:p0,dcache:p0,icache:p1,dcache:p1,icache:p2,dcache:p2,
l2b:p0,l2a:p0,l2b:p1,l2a:p1,l2b:p2,l2a:p2
```

`python3 -m py_compile`、`git diff --check` 同样通过。

### 18.29 14:16 当前精确卡点

当前状态不再是“Linux 跑到哪里”：第三轮已经完整跑完 Linux、进入 shell、执行 terminal 命令并生成 checkpoint。精确卡点是：

```text
第三轮 all-valid checkpoint 文件       PASS
第三轮 gzip/hash/CHI canonical audit   PASS
第三轮 idle-hart 栈与页表语义          FAIL（0/15）
第三轮 restore gate                    正确拒绝
DTLB/SATP/页表交叉定位                  已完成
phased-owner 修复代码                   已完成
phased-owner 主构建                     PASS
phased-owner 聚焦测试                   176/176 PASS
phased-owner 实际调用顺序 probe         PASS
第四轮全新 cold checkpoint             尚未生成
最终 restore + terminal                尚未执行
```

旧 `cpt.67032562072` 的 pmem 已经在生成时被旧覆盖顺序写坏；新二进制不会追溯修改该 artifact，所以不能拿它冒充 phased-owner 的端到端结果。下一步必须使用全新 outdir 运行同一 16 核/128 MiB/6×4 CHI cold workload，取得第四个 checkpoint 并重复 gzip、CHI、DTLB/page-table 和精确 15/15 gate。只有 gate 通过后才允许恢复，恢复后还必须由真实 terminal 返回命令并再次 re-checkpoint。

### 18.30 第四轮 phased-owner 权威 cold run 已启动

第四轮不复用第三轮输出目录，也不复用第三轮 checkpoint。冷启动与后续恢复使用两个全新目录：

```text
cold:
m5out/linux-16core-128mb-phased-owner-cold-detached-20260824/

restore:
m5out/linux-16core-128mb-phased-owner-restore-detached-20260824/
```

冷启动命令沿用相同的可比配置：16 个 XiangShan O3 CPU、128 MiB `SimpleMemory`、6×4 CHI mesh、16 HN-F/SLC、4 DDR SN、`--raw-cpt`、`--no-pf`、最大绝对 tick `120000000000`、最多生成一个 checkpoint。输入仍是同一个 19,264,520-byte Linux/OpenSBI raw payload，变化项仅为重新构建的 phased-owner `gem5.opt` 和全新 outdir。

本轮可执行文件：

```text
build/RISCV/gem5.opt
SHA256 = 72d2d59c0999319c9f18cb60d0d6aedb135287ce7f8348904d978dcbce131ea8
compiled = 2026-08-24 14:12:21
```

启动与宿主进程快照：

| 字段 | 值 |
|---|---|
| 启动时间 | `2026-08-24 14:24:44 CST` |
| gem5 PID | `3754949` |
| 14:28 elapsed | `226 s` |
| 14:28 CPU time | `00:03:46` |
| 14:28 CPU | `99.9%` |
| 14:28 state | `Rs` |
| terminal port | `3456` |
| terminal feeder | PID `3757013` / child `3757025`，存活 |
| checkpoint watcher | PID `3758449`，存活 |
| restore gate | PID `3758828`，存活 |

串口监听和连接已经由 gem5 日志确认：

```text
system.terminal: Listening for connections on port 3456
27879585: system.terminal: attach terminal 0
==== m5 terminal: Terminal 0 ====
```

截至 14:28，gem5 已完成 16 核对象、64 个 L2 slice、CHI 24-router/16-RN-F/16-HN-F/4-DDR 拓扑初始化，载入 raw payload，并从 tick 0 进入事件队列。日志尚未出现 Linux printk，也未出现 `panic`、`fatal`、assert、SIGSEGV 或 checkpoint 退出。因此这一时刻的卡点只是详细 O3+CHI 模型从 reset 推进到 Linux shell 所需的宿主墙钟时间，不能解释为已观察到新的 Linux 死锁。

#### 18.30.1 自动验收链

terminal feeder 已准备在 `/bin/sh` 出现后注入 helper，并要求 guest 返回以下不可混淆 marker：

```text
PHASED_OWNER_COLD_SHELL_20260824
PHASED_OWNER_PROC_SYS_READY_20260824
PHASED_OWNER_CPU_COUNT=16
PHASED_OWNER_CPU_ONLINE=0-15
PHASED_OWNER_UPTIME=...
PHASED_OWNER_SHELL_PID=...
PHASED_OWNER_TRIGGER_CHECKPOINT_20260824
```

host 侧 helper 固定为 552 bytes，期望 SHA256 为：

```text
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
```

checkpoint watcher 只接受“精确一个 checkpoint”，然后依次执行：

1. `gzip -t`；
2. gzip/raw SHA256 与 raw 128 MiB 大小记录；
3. canonical-empty CHI SLC/SF 与 pmem mismatch 审计；
4. 15 个 idle hart 的 SATP/Sv48/栈 RA 审计，必须精确 `15/15`。

restore gate 已在 14:25:36 启动。它只有在上述 cold checkpoint 的 gzip、CHI 和 stack 三项状态全部为 `PASS`，且 stack summary 精确为 `15/15` 时才会创建 restore outdir；任何一项失败都会拒绝恢复，避免把已知坏 artifact 误当成新代码结果。

#### 18.30.2 一次“进程消失”假象及纠正

14:27 曾用 sandbox 内普通 `ps -p ...` 查询 detached 宿主 PID，返回空结果。该结果一度看似 gem5 与三个监控脚本同时退出，但与无退出原因的 simout/simerr、仍在监听的 terminal 连接不一致。随后使用宿主进程视图的完整 `ps -eo ...` 精确过滤，确认五个目标进程全部存在，gem5 仍为 `Rs` 且 `99.9% CPU`。

因此该现象是执行环境进程命名空间可见性差异，不是 gem5 crash。后续进程存活判定必须使用宿主进程视图，并同时交叉检查 CPU time、日志增量和 checkpoint artifact，不能再把 sandbox 内空 `ps` 当成退出证据。

### 18.31 14:43：第四轮已再次越过原始 Linux 崩溃点

第四轮约在宿主运行 12 分钟时出现统计 reset 边界：

```text
Will trigger stat dump and reset
Entering event queue @ 1688382900. Starting simulation...
```

随后发生正常的 Linux MMU 切换：

```text
enable SV48
enable SATP BARE
enable SV48
```

串口先后真实打印：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI specification v2.0 detected
[    0.000000] earlycon: sbi0 at I/O port 0x0 (options '')
[    0.000000] printk: legacy bootconsole [sbi0] enabled
[    0.000000]   DMA32 [mem 0x0000000080000000-0x0000000087ffffff]
[    0.000000] Initmem setup node 0 [mem 0x0000000080000000-0x0000000087ffffff]
```

原始问题的精确失败序列是：

```text
riscv: base ISA extensions
riscv: ELF capabilities
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000004
Oops [#1]
```

而第四轮的实测序列为：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
[    0.000000] percpu: Embedded 14 pages/cpu s27304 r0 d30040 u57344
[    0.000000] pcpu-alloc: [0] 00 ... [0] 15
[    0.000000] Kernel command line: console=hvc0 earlycon=sbi loglevel=8 rdinit=/bin/sh initcall_blacklist=check_unaligned_access_all_cpus
[    0.000000] printk: log_buf_len min size: 32768 bytes
```

全量日志未匹配 `Unable to handle kernel NULL pointer`、`Oops [#`、panic 或 fatal。因此 phased-owner checkpoint 写回改动没有回归 cold boot 的原始修复；第四轮已从“尚未进入 Linux”推进为“已越过原始崩溃窗口，继续执行 early init”。

14:43:23 的宿主样本：

```text
PID      = 3754949
elapsed  = 00:18:37
CPU time = 00:18:36
CPU      = 99.8%
STAT     = Rs
```

elapsed 与 CPU time 几乎完全同步，仍无 sleep/zombie/假活迹象。当前下一项尚未通过的功能 gate 是 `smp: Brought up 1 node, 16 CPUs`；更后的最终 gate 仍是 shell/helper checkpoint 的精确 `15/15`、restore、restore terminal 与二次 checkpoint。

### 18.32 14:56：PMP warning 对照排除，SMP bring-up 推进到 12/16 组

第四轮 early boot 中出现：

```text
pmp access fault.mode 0  vaddr 880043278
[    0.000881] riscv: ELF compat mode unsupported
```

这两行容易被误认为新的 PMP 故障。对所有历史 outdir 做精确字符串检索后，第一行在 all-valid、SLC-first、drainfix、ordered-cold 等已成功进入 shell 的 run 中均位于同一启动位置；第二行同样以完全相同 guest 时间 `0.000881` 出现在这些成功 run 中。第四轮紧接着继续打印 ASID allocator、RCU 和 EFI unavailable，也没有 Oops。因此它是内核兼容性/访问探测产生的确定性 warning，不是新的 crash，不能作为中止理由。

之后第四轮进入：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
```

每个 secondary hart 会产生一组 CSR 初始化 warning，特征包括：

```text
write to misc_reg d val ffffffffffffffff but now write 0
...
enable SV48
enable SV48
```

截至 14:56:40 的机械计数：

```text
完整 CSR 初始化组 = 12
enable SV48 次数  = 28
成功 all-valid run 最终完整 CSR 组 = 16
```

因此此时不是重复卡在同一个 hart，而是至少已经依次推进了 12 组 CPU 初始化。宿主同步证据：

```text
PID      = 3754949
elapsed  = 00:31:55
CPU time = 00:31:53
CPU      = 99.9%
STAT     = Rs
```

checkpoint 目录仍为空，watcher 与 restore gate 仍在等待且没有写入 `FAIL`。下一项判定点保持为 Linux 自己输出 `smp: Brought up 1 node, 16 CPUs`，不能用中间 CSR 计数替代最终 online 证明。

### 18.33 15:04：第四轮 16 核 SMP gate 权威通过

14:56 之后完整 CSR 初始化组从 12 依次增加到 14、15、最终 16。16/16 后经历约两分钟 Linux barrier/同步静默，随后 `simout` 正式落盘：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
[    0.005231] devtmpfs: initialized
```

对第三轮 all-valid 与第二轮 SLC-first 成功 cold run 做精确检索，三条 run 的上述三行具有相同行号 `479–481`、相同 guest 时间戳与相同文本。这是比 CSR warning 计数更强的权威证据：Linux 已确认一个 NUMA node 上 16 个 CPU 全部完成 bring-up，第四轮的 phased-owner 代码没有破坏 SBI HSM、secondary MMU、IPI 或 SMP barrier 路径。

15:04:22 的宿主状态：

```text
PID      = 3754949
elapsed  = 00:39:36
CPU time = 00:39:34
CPU      = 99.9%
STAT     = Rs
```

当前 Linux 精确位置更新为 `devtmpfs: initialized` 之后、网络协议/驱动与 hvc0 初始化之前。checkpoint 尚未生成属于预期：terminal 中缓存的 helper 命令只有 `/bin/sh` 接管 console 后才会执行。下一阶段将依次观察 PF_NETLINK/PF_INET/PF_UNIX、I/O scheduler、hvc0、unused clocks、释放 initmem 和 `Run /bin/sh as init process`。

### 18.34 15:24：网络协议与 I/O scheduler gate 通过

第四轮在 SMP/devtmpfs 之后继续打印：

```text
[    0.006605] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.006770] DMA: preallocated 128 KiB GFP_KERNEL pool for atomic allocations
[    0.006844] DMA: preallocated 128 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008349] vgaarb: loaded
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
[    0.010783] NET: Registered PF_INET protocol family
[    0.010855] IP idents hash table entries: 2048
```

此前 blacklist 后出现约 11 分钟宿主静默；同一期间 gem5 CPU time 等速增长，最终 PF_INET 正常出现，证明该静默是详细模型计算而非卡死。随后 TCP/UDP 与本地 socket 路径完整通过：

```text
[    0.011227] TCP: Hash tables configured (established 1024 bind 1024)
[    0.011308] UDP hash table entries: 256
[    0.011354] UDP-Lite hash table entries: 256
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011490] PCI: CLS 0 bytes, default 64
[    0.011955] workingset: timestamp_bits=62 max_order=15 bucket_order=0
```

最后三个 block scheduler 均已实测注册：

```text
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

15:24:53 宿主证据：

```text
PID      = 3754949
elapsed  = 01:00:08
CPU time = 01:00:05
CPU      = 99.9%
STAT     = Rs
```

第三轮 all-valid 的 bfq→hvc0 实测静默约 63 分 14 秒，第一轮成功路径曾观察到约 82 分钟。因此第四轮下一阶段预期长时间没有 printk；只要 CPU time 持续增长、进程保持 runnable 且无 crash marker，就不能把这段历史已知长区间写成新卡点。下一项权威 marker 是 `printk: legacy console [hvc0] enabled`。

### 18.35 15:30：phased-owner 协议假设与 functional 路由复核

第四轮进入不可加速的 bfq→hvc0 区间后，继续静态复核 phased-owner 是否仍有“低优先级 functional write 先污染真正 owner”的隐患。

`CacheBlk::print()` 对 classic-cache MOESI 位的源码约束明确写明：

```text
S: Writable=0, Dirty=0
E: Writable=1, Dirty=0
M: Writable=1, Dirty=1
O: Writable=0, Dirty=1

同一 block 只有一个 cache 处于 Modified 或 Owned，
即只有一个 cache 持有 DirtyBit。
同一路径不同 level 可以同时为 Writable，但 peer branch 不可以。
```

这与 checkpoint phase 映射一致：

```text
Shared / former owner -> phase 0
Exclusive             -> phase 1
Modified / Owned      -> phase 2
```

`BaseCache::functionalAccess()` 确实会更新沿途 valid block，但只有 dirty owner 才会终止 functional request。层级与 peer 路由进一步证明当前顺序成立：

1. L1 的低 phase 先写，真正的 L1 owner 在 p1/p2 后写，可把本路径 L2 数据纠正为最新版；
2. owner 路径的下层 inclusive copy 保留 Writable(E)，因此在 L2 p1 晚于远端 Shared p0 落盘；
3. `Cache2ChiBridge::cacheRecvFunctional()` 的实现只有 `memPort.sendFunctional(pkt)`，L2 writeback 直接进入 classic memory path，不经过其他 L2 peer，也不重新写入 modeled SLC；
4. SLC 已在所有 private cache 之前写回，所以它不能在最后反向覆盖 private owner。

为避免只靠注释推导，新增单测：

```text
CacheBlkCheckpointDirtyTest.WritebackPhaseMatchesMoesiPrecedence
```

实测结果：

```text
Shared   -> phase 0 PASS
Exclusive-> phase 1 PASS
Modified -> phase 2 PASS
Owned    -> phase 2 PASS
CacheBlk suite: 4/4 PASS
```

增量构建 `scons build/RISCV/mem/cache/cache_blk.test.opt -j2` 返回 0，更新后的四个聚焦 suite 合计为 `176/176 PASS`。这次只修改并重建了 test source/二进制，没有改动当前长跑所使用的 `build/RISCV/gem5.opt`，因此不会改变第四轮 executable hash 或污染实验可比性。

15:29 左右第四轮宿主状态仍为：

```text
elapsed  = 01:05:08
CPU time = 01:05:04
CPU      = 99.9%
STAT     = Rs
```

没有 hvc0、crash、exit 或 gate failure；精确运行位置仍为 bfq 之后的历史长 initcall 区间。

### 18.36 15:36：严格 checkpoint 审计再补三道硬门槛

在等待第四轮越过 bfq→hvc0 长区间时，对自动验收链做了一次反向审计。原来的 `util/audit_riscv_checkpoint_stacks.py` 已经独立执行 pmem 中的 Sv48 page walk，并检查 idle hart 的 live `s0 == sp + 16` 和 `saved_ra == live_ra`；但还存在三个可以进一步收紧的点：

1. 输出了 `saved_s0`，却没有要求 `saved_s0 == live_s0`；
2. 没有把 `m5.cpt` 中 serialized DTLB 对同一 `sp` 的翻译与 pmem page-table walk 结果纳入 PASS；
3. 单独运行审计器时，`idle_total == 0` 会形成 `0 == 0` 的空集通过，尽管当前 watcher 另有精确 `15/15` grep，不会让本轮 gate 被绕过。

现已把单 hart PASS 条件收紧为以下全部成立：

```text
PC == arch_cpu_idle
pmem Sv48 walk 能映射 SP
serialized DTLB 能映射 SP
DTLB physical == pmem page-walk physical
live_s0 == SP + 16
saved_s0 == live_s0
saved_ra == live_ra
```

审计器总返回码也改成只有 `idle_total > 0 && idle_passed == idle_total` 才为 0；本轮 cold/restore watcher 在此基础上仍额外要求日志中精确存在：

```text
idle_summary passed=15 total=15
```

新解析器直接读取形如 `[system.cpu00.mmu.dtb.Entry1]` 的 section，并只接受与当前 SATP ASID 相同的 entry，避免误用 TLB 中其他地址空间留下的同虚址记录。以第三轮 CPU0 为例，checkpoint 中保留的是：

```text
vaddr   = 0xffff8f800010b000
paddr   = 0x81584 (PPN)
logBytes= 12
SP      = 0xffff8f800010be60
```

因此 serialized DTLB 给出 `0x81584e60`；而从 pmem 自身页表 walk 得到的是 `0x8158ee60`。这正是此前逐核分析发现的页面别名，不依赖 cache dump 或人工地址推断。

将加强后的审计器重放到第三轮 `cpt.67032562072` 及其 128 MiB raw pmem，结果为：

```text
idle hart 数                    = 15
最终 PASS                       = 0/15
saved_s0 == live_s0             = 0/15
serialized DTLB == pmem walk    = 4/15
两种 physical 映射不一致        = 11/15
```

DTLB/pagewalk 恰好一致的 CPU2、CPU7、CPU11、CPU15 仍然因 pmem 中保存的 `s0/ra` 与 architectural state 不一致而失败；这说明新条件既能抓页表被旧副本覆盖，也能抓“页表碰巧指到某个现存页、但该页属于另一个 hart”的情况。CPU3 仍显示 DTLB `0x81203e10`、pmem walk `0x80203e10` 的 16 MiB 物理基址差异。

验证记录：

```text
python3 -m py_compile util/audit_riscv_checkpoint_stacks.py  -> PASS
第三轮坏 artifact 重放                                            -> exit 1
idle_summary passed=0 total=15                                    -> 符合预期
使用不存在的 idle PC 做空集测试：passed=0 total=0                  -> exit 1
git diff --check                                                   -> PASS
```

这次修改仅涉及验收脚本，不改动正在执行的 `gem5.opt`，也不需要重启第四轮；watcher 在 checkpoint 生成后启动新的 Python 进程，因此会自动加载加强后的规则。15:36:08 的宿主状态仍为：

```text
gem5 PID = 3754949
elapsed  = 01:11:02
CPU time = 01:11:02
CPU      = 99.9%
STAT     = Rs
feeder / cold watcher / restore gate = 全部存活
```

guest 最后 marker 仍是 `[0.012178] io scheduler bfq registered`，没有 Oops、panic、exit 或 gate failure；因此当前卡点仍是历史可复现的 bfq→hvc0 长 initcall，而不是审计器变更或新的 Linux 崩溃。

### 18.37 15:40：自动化等待语义复核与本次报告交付点

为排除“guest 其实正常，只是 feeder/watcher 自己超时退出”的假卡点，逐行复核了当前正在执行的四个自动化脚本：

- cold feeder 对 terminal 端口的等待上限是 `720 × 5 s = 3600 s`，但端口 `3456` 已在启动初期发布并完成 `m5 terminal: Terminal 0` 握手，所以这个 deadline 已经过去；连接后它把 16 条 guest 命令排队，并持续保持 `nc` 到 gem5 退出；
- cold watcher 没有 Linux boot deadline，只以 60 秒周期检查 checkpoint marker、明确 crash signature 和 gem5 PID；
- restore gate 同样没有 cold boot deadline，它只等待 `stack_audit.status`，且只有 gzip、CHI、stack 三个 status 全为 PASS、日志精确 `15/15`、cold run 明确以 checkpoint 退出后才启动 restore；
- restore 侧会真实执行 `uname`、CPU count、online mask、uptime、shell PID、helper SHA256，再次触发 checkpoint，并对第二份 checkpoint 重做 gzip、CHI 与精确 15/15 审计。

因此当前长静默不会被自动化误杀，也不存在“时间到了就伪造 PASS”的路径。15:39:46 的最终取证快照为：

```text
gem5 PID       = 3754949
elapsed        = 01:15:01
CPU time       = 01:15:01
CPU            = 99.9%
PMEM           = 0.2%
STAT           = Rs
terminal feeder= alive (3757013 / 3757025)
cold watcher   = alive (3758449)
restore gate   = alive (3758828)
checkpoint dirs= 0
hvc0 marker    = 0
shell marker   = 0
exit marker    = 0
crash marker   = 0
```

`elapsed == CPU time` 且 `STAT=Rs` 表明 gem5 仍持续消耗 CPU 推进 detailed simulation，不是 sleep、zombie 或 PID 假活。追加本节后的报告为 4553 行、约 177 KB，`git diff --check` 通过。交付时的精确卡点仍是 Linux guest `[0.012178] io scheduler bfq registered` 之后、`legacy console [hvc0] enabled` 之前；新的 checkpoint/15-of-15/restore/terminal 闭环尚未发生，任务状态保持进行中。

### 18.38 15:46：修正 restore gate 对单条 PMP warning 的误杀风险

继续反查历史 restore 日志时，发现 `watch_phased_owner_restore_20260824.sh` 的运行中 crash 正则包含裸字符串：

```text
PMP access fault
```

这会把一条 PMP 探测 warning 与真正的 PMP fault storm 等价处理。历史证据表明两者规模完全不同：

```text
当前正常 phased-owner cold simout+simerr              = 1 条
历史坏 consolidated restore preorder simout+simerr    = 19,189 条
```

同时，历史坏 restore 的 `Oops [#1]`、`Kernel panic`、gem5 `fatal:` 和 `Program aborted` 都能被原有明确 crash pattern 独立捕获。因此把任意一条 PMP warning 立即判死既不提高已知 Oops/fatal 的检出率，反而可能让一个语义正确的 restore 因单次合法探测被误报。

修正后的判据是：

```text
NULL dereference / Oops / Kernel panic / panic: / fatal: / Program aborted
    -> 任意一次立即记为 crash

pmp access fault
    -> 累计达到 100 条才记为 PMP fault storm
```

阈值回放验证：当前正常样本 `1 < 100`，历史坏样本 `19189 >= 100`；另用 `guestcpt-restore-v15-accept` 验证明确 Oops/panic 正则仍返回命中。脚本通过 `bash -n` 与 `git diff --check`。

修正时 cold checkpoint 尚未生成、restore outdir 尚不存在，旧 gate PID `3758828` 只在每 60 秒等待 `stack_audit.status`，没有启动任何 restore 子进程。故只对该等待 gate 执行 TERM，未触碰 gem5、terminal feeder 或 cold watcher。为保留旧锁的取证痕迹，新 gate 使用独立的 `restore_gate.v2.lock`，随后 detached 重启成功：

```text
新 restore gate PID = 4183432
PPID                 = 1
SID                  = 4183432
STAT                 = Ss
gate log             = waiting for exact 15/15; PMP storm threshold=100
```

同一时刻原 cold-run 进程完全连续：

```text
gem5 PID       = 3754949, elapsed 01:20:47, CPU time 01:20:47, 99.9%, Rs
terminal feeder= 3757013 / 3757025, alive
cold watcher   = 3758449, alive
```

这项修正不改变 guest、gem5 二进制、checkpoint 内容或 cache 算法，只消除最终 restore 验收中的一条宿主自动化假阴性路径。

### 18.39 15:52：walker-cache 层级风险复核与排除范围

继续检查 phased-owner 的跨层顺序时，config 暴露出一个需要特别确认的点：普通 I/D cache 的 `cache_level=1`，而 32 个 ITB/DTB walker cache 继承默认 `cache_level=0`。实际端口拓扑显示每个 CPU 的四个 cache 都是同一 coherent `tol2bus_listNN` 的 peer：

```text
port[0] = icache.mem_side
port[1] = dcache.mem_side
port[2] = itb_walker_cache.mem_side
port[3] = dtb_walker_cache.mem_side
```

如果 page-table walker 能直接更新 PTE 的 Accessed/Dirty 位，它就可能成为 Dirty owner；这时把 walker 的三个 phase 全部放在 L1 之前，理论上可能让后写的旧 L1 copy 反向覆盖 walker owner。因此这里不能只凭类名认定安全。

对当前 RISC-V walker 实现做源码级核查后，确认这一风险在本次 binary 中不可达：

```cpp
bool doWrite = false;

if (!pte.a)
    fault = pageFault(...);
if (!pte.d && mode == BaseMMU::Write)
    fault = pageFault(...);

if (!functional && doWrite) {
    ...
    write->cmd = MemCmd::WriteReq;
    panic("wrong in ptw , now don't need do write");
}
```

`doWrite` 在该函数内没有任何置 true 路径；A/D 位缺失会直接触发 page fault，真正的 PTE WriteReq 分支不仅不可达，还带显式 panic。因此本轮 RISC-V walker cache 只会通过 read 获得 clean Shared/Exclusive line，不会成为 Modified/Owned owner。当前顺序的语义是：

1. clean walker copy 先落盘；
2. 若同块后来由 L1 owner 修改，L1 的 p1/p2 再覆盖；
3. 若 owner 已下沉或位于另一 private hierarchy，最终 L2 全局 p1/p2 再覆盖；
4. 若 walker 保有 clean Exclusive，按一致性约束不存在另一个同时有效的脏 peer，数据与取得时的 backing value 相同。

所以这不是停止第四轮或启动第五轮的充分理由。需要保留的边界条件是：若未来启用 RISC-V hardware A/D update，或复用这套 checkpoint 顺序到真正会写 PTE 的 ISA walker，应把 walker 与 L1 放进同一全局 owner-phase group，并新增可写 walker 的端到端测试；当前目标不依赖该未来行为。

15:52:13 宿主复核仍为：

```text
gem5 elapsed  = 01:27:28
gem5 CPU time = 01:27:28
CPU / STAT    = 99.9% / Rs
feeder, cold watcher, corrected restore gate = all alive
```

guest 与 gate 日志仍无新行，精确位置不变：bfq 之后、hvc0 之前。

### 18.40 15:56：当前二进制的 phase probe 与 176 个测试重新取证

为避免最终报告只引用修复时的历史测试记录，在第四轮运行期间重新执行当前磁盘上的主 `gem5.opt`、probe 与四个聚焦测试二进制：

```text
build/RISCV/gem5.opt /tmp/checkpoint_phase_probe.py                -> PASS
build/RISCV/mem/cache/cache_blk.test.opt                           -> 4/4 PASS
build/RISCV/mem/cache/CHI/hnf_slcsf_backend.test.opt               -> 24/24 PASS
build/RISCV/mem/cache/CHI/slc_snoop_filter.test.opt                -> 17/17 PASS
build/RISCV/mem/cache/CHI/hnf_slcsf.test.opt                       -> 131/131 PASS
合计                                                               -> 176/176 PASS
```

实际 probe 事件序列为：

```text
slc:legacy,
walker1:p0,walker0:p0,walker1:p1,walker0:p1,walker1:p2,walker0:p2,
icache:p0,dcache:p0,icache:p1,dcache:p1,icache:p2,dcache:p2,
l2b:p0,l2a:p0,l2b:p1,l2a:p1,l2b:p2,l2a:p2
```

这不是手写算法的独立复制，而是由当前 `build/RISCV/gem5.opt` 启动实际 `m5.simulate.memWriteback()` 得到。HnfSLCSFBackend/HnfSLCSF 输出中的若干 panic/fatal 是 death test 对非法 checkpoint、未 drain、重复请求和 counter overflow 的预期断言；suite 总退出码均为 0，不能误记为测试失败。

测试是独立短进程，没有修改第四轮 outdir，也没有向 terminal 端口写入额外数据；实时 monitor 在测试前后均未观察到 guest、cold watcher 或 restore gate 新事件。

### 18.41 16:06：修正恢复后二次 checkpoint 的查找目录

对 `--checkpoint-dir` 的恢复/输出双重语义做源码复核后，发现旧 restore gate 还有一个确定性的末端假失败：

```text
restore 启动参数：
    -r 1
    --checkpoint-dir=<cold_out>/checkpoints
    --max-checkpoints=1

旧脚本验收位置：
    <restore_out>/checkpoints/cpt.*
```

`Simulation.py` 只有在同时使用 `--checkpoint-restore` 与命令行 `--take-checkpoints`/SimPoint checkpoint 时，才会在 restore 完成实例化后把新 checkpoint 输出重定向到 `--outdir`。本轮故意依赖 guest helper 的 checkpoint pseudo-op，不能增加 `--take-checkpoints`，否则 Python 定时 checkpoint 路径会忽略 guest checkpoint instruction。

实际调用链为：

```text
run(): cptdir = options.checkpoint_dir
findCptDir(): 用 cptdir/cpt.<N> 恢复
benchCheckpoints(..., cptdir): 收到 guest "checkpoint" exit event
m5.checkpoint(joinpath(cptdir, "cpt.%d"))
```

所以第二份 checkpoint 必然写回 `<cold_out>/checkpoints`，与原始 checkpoint 同一 parent；旧脚本即使已经做到“restore 无 crash、terminal 命令全部返回、guest re-checkpoint 成功”，最后也会因去 `<restore_out>/checkpoints` 查找而报 0 个目录。

修正后的严格选择逻辑：

1. restore 前 cold gate 已保证 parent 中恰好一个目录，保存为 `cold_cpt`；
2. restore 正常以 guest checkpoint 退出后，同一 parent 必须恰好有两个 `cpt.*`；
3. 排除路径严格等于 `cold_cpt` 的原目录；
4. 剩余项必须恰好一个，记为 `restore_cpt`；
5. gzip、SHA256、CHI canonical 与精确 15/15 全部针对 `restore_cpt` 执行。

数组选择逻辑用 `cpt.100 + cpt.200` 合成样本验证能唯一选出 `cpt.200`；脚本通过 `bash -n` 与 `git diff --check`。修正发生时 checkpoint 数仍为 0，v2 gate PID `4183432` 仍只在等待 cold audit，故安全终止它并用新锁 `restore_gate.v3.lock` 重启：

```text
v3 gate PID = 98388
PPID / SID  = 1 / 98388
STAT        = Ss
启动日志    = waiting for exact 15/15; PMP storm threshold=100
```

原 cold gem5 继续保持同一 PID `3754949`，v3 启动时 `elapsed=01:41:02`、`CPU time=01:41:02`、`99.9%/Rs`；terminal feeder 和 cold watcher 未中断。该修复只影响恢复成功后定位第二份 artifact，不改变模拟状态或 checkpoint 内容。

### 18.42 16:52：第四轮 hvc0 与 late-driver gates 连续通过

第四轮最终越过 bfq→hvc0 长静默，terminal 与 simout 实测得到：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
```

两次 hvc0/bootconsole 行是 early SBI console 切换到正式 HVC console 时的正常 replay，与 all-valid 成功 run 完全一致。随后 late-driver 序列也逐行匹配：

```text
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
```

UARTLite 的 `-ENXIO` 又一次用全仓历史日志做精确字符串对照：它存在于 all-valid、SLC-first、ordered、drainfix 等成功 checkpoint cold run 的同一 guest 时间 `0.021485`，其后本轮也继续进入 SDHCI/IPv6，因此仍是设备树不提供该 UART IRQ 的非致命 probe 结果。

当前 terminal 最后更新时间为 `16:39:49`，最后 marker 是 sit；16:51:37 的宿主快照：

```text
gem5 PID       = 3754949
elapsed        = 02:26:58
CPU time       = 02:26:58
CPU / STAT     = 99.9% / Rs
terminal feeder= alive
cold watcher   = alive
v3 restore gate= alive
checkpoint dirs= 0
crash markers  = 0
```

第三轮 all-valid 的历史墙钟表明 sit→`clk: Disabling unused clocks` 约需 37 分钟，clocks→`Run /bin/sh` 还约需 2 小时 23 分；该估算只解释详细 O3+CHI 模型成本，不作为超时判据。当前精确位置更新为 **sit tunnel 之后、unused clocks 之前**，功能状态正常且最终验收仍在运行。

### 18.43 17:18：越过历史耗时估算不等于 hang，CPU 时间仍连续推进

从第四轮最后一条 `sit` marker 的宿主时间 `16:39:49` 到 17:18 已约 38 分钟，略微越过第三轮同区间约 37 分钟的历史耗时。由于历史耗时不是协议或软件规定的 deadline，不能据此杀掉当前唯一权威 run。17:18 重新读取宿主进程表得到：

```text
gem5 PID       = 3754949
elapsed        = 02:53:12
CPU time       = 02:53:06
CPU / STAT     = 99.9% / Rs
terminal feeder= PID 3757013/3757025/3757026，alive
cold watcher   = PID 3758449，alive
v3 restore gate= PID 98388，alive
checkpoint dirs= 0
crash markers  = 0
```

`CPU time` 与 `elapsed` 仅相差约 6 秒，并且相对 16:51:37 快照同步增长约 26 分钟，证明进程不是 sleep、zombie 或被宿主暂停，而是在持续执行 gem5 event queue。terminal、simout、watcher 和 restore-gate 日志没有新行的含义仅是 guest 尚未完成当前 initcall；它既没有提供 hang 的证据，也没有改变最终严格 gate。当前仍保持 **sit tunnel 之后、unused clocks 之前**，不重启、不放宽审计条件，继续等待真实 guest marker。

### 18.44 17:25：第四轮通过 unused-clocks gate

第四轮随后输出了与第三轮成功 cold boot 完全相同 guest tick 的下一条里程碑：

```text
[    0.026816] clk: Disabling unused clocks
```

该行写入 terminal 的宿主时间是 `17:20:35.566093 CST`，simout 在 `17:20:43.185366 CST` 刷盘。从上一条 sit 的 `16:39:49.382793` 算起，本轮 sit→unused-clocks 实耗约 **40 分 46 秒**；第三轮约 37 分钟只能作为量级参考，不能作为超时。更重要的是，guest 时间 `0.026816` 与 all-valid、SLC-first、ordered 和 drainfix 的成功 cold 路径一致，说明 phased-owner cache writeback 修改没有扰动当前 cold-boot 控制流。

17:25 的宿主快照为：

```text
gem5 PID       = 3754949
elapsed        = 03:00:13
CPU time       = 03:00:07
CPU / STAT     = 99.9% / Rs
terminal feeder= alive
cold watcher   = alive
v3 restore gate= alive
checkpoint dirs= 0
crash markers  = 0
```

因此当前 Linux 精确位置已经更新为 **`clk: Disabling unused clocks` 之后、`Run /bin/sh as init process` 之前**。第三轮同配置从 unused-clocks 到 shell 约耗 2 小时 23 分，故下一阶段仍可能长时间没有 printk；自动 terminal feeder 会在 shell 可读时依次执行 uname、CPU count、online mask、uptime、helper SHA256 和 guest checkpoint，cold watcher 随后执行 gzip、CHI 与精确 15/15 审计。17:25 快照中的 v3 gate 当时仍在等这些证据；其末端 artifact 路径问题随后在 18.45 节纠正并替换为 v4。

### 18.45 17:31：纠正 v3 对恢复后二次 checkpoint 输出目录的错误判断

在 unused-clocks→shell 的等待窗口中，对四个自动验收脚本重新做逐行审计时，发现 18.41 节的结论与**当前工作树中的实际 `Simulation.py`**相反。当前 `run_vanilla()` 明确实现为：

```python
cptdir = options.checkpoint_dir or m5.options.outdir
checkpoint_output_dir = cptdir

if options.checkpoint_restore is not None:
    checkpoint_dir = joinpath(cptdir, selected_cpt)
    checkpoint_output_dir = joinpath(m5.options.outdir, "checkpoints")

m5.instantiate(checkpoint_dir)
exit_event = benchCheckpoints(
    testsys, options, maxtick, cptdir=checkpoint_output_dir)
```

因此 restore 命令中的 `--checkpoint-dir=<cold_out>/checkpoints` 只负责选择 immutable 输入 checkpoint；恢复后的 guest helper 再次触发 pseudo-op 时，新 checkpoint 会写入：

```text
<restore_out>/checkpoints/cpt.<tick>
```

而不会回写 cold parent。18.41 节把旧/通用 Simulation.py 行为当成当前 `run_vanilla()` 行为，进而把 v2 的正确查找方向错误地改成了 v3 的 cold-parent 双目录选择。若不修正，真正成功的 restore、terminal 和 re-checkpoint 会在最后被 v3 误报为“cold parent 只有一个 checkpoint”。这属于验收自动化假失败，不改变当前 cold guest 或 checkpoint 内容，但会浪费完整 restore 的数小时结果。

继续下钻 `benchCheckpoints()` 可确认这不是只靠变量名推断：函数从 `m5.simulate()` 收到精确的 `checkpoint` exit cause 后，直接调用 `m5.checkpoint(joinpath(cptdir, "cpt.%d"))`；随后 checkpoint counter 从 0 增为 1，与本轮 `--max-checkpoints=1` 相等时立即停止循环。`run_vanilla()` 最终仍打印原始 exit event 的 `checkpoint` cause。因此 v4 同时要求“restore simout 有正常 checkpoint exit marker”和“`<restore_out>/checkpoints` 恰好一个目录”，与实际写盘和停止控制流逐项对应，不会把命令行定时 checkpoint 与 guest pseudo-op 混为一谈。

修正后的 v4 逻辑为：

1. cold parent 必须恰好一个 checkpoint，作为 `-r 1` 的 immutable 输入；
2. restore outdir 启动前必须为空；
3. `Simulation.py` 在 instantiate 后创建 `<restore_out>/checkpoints`；
4. restore 正常因 guest checkpoint 退出后，该目录必须恰好一个 `cpt.*`；
5. gzip、SHA256、CHI canonical 与精确 15/15 全部审计这一个 restore artifact。

脚本锁从 `restore_gate.v3.lock` 升级为 `restore_gate.v4.lock`，通过 `bash -n` 与 `git diff --check`；另用仅含 `checkpoints/cpt.700` 的临时 restore-out 合成样本运行与脚本相同的 `find + sort -V + mapfile` 选择逻辑，得到 `v4_selection_probe=PASS`。替换时 cold `stack_audit.status` 尚不存在、cold checkpoint 数仍为 0、restore outdir 尚不存在；所以 PID `98388` 仍只在 60 秒等待循环中，未启动 gem5、未创建或修改任何 restore artifact。确认精确命令行后对其发送 TERM，再以 detached session 启动 v4：

```text
v4 gate PID = 518853
启动时间    = 2026-08-24 17:29:38 CST
启动日志    = restored checkpoint output=<restore_out>/checkpoints
```

替换后 cold gem5 保持原 PID `3754949`，17:30 快照 `elapsed=03:05:31`、`CPU time=03:05:24`、`99.9%/Rs`；terminal feeder 与 cold watcher 均未中断。旧 v2/v3 lock 目录只作为历史运行证据保留，v4 仅检查自己的唯一锁，不会发生竞争。

### 18.46 17:36：当前主 gem5 实际执行 run_vanilla restore-output probe

为避免 18.45 仍停留在源码阅读或 shell 目录选择层，新增 `/tmp/phased_owner_run_vanilla_probe_20260824.py`，由当前主二进制直接执行：

```text
build/RISCV/gem5.opt /tmp/phased_owner_run_vanilla_probe_20260824.py
```

probe 不复制 `run_vanilla()` 算法，而是导入工作树中的 `configs/common/Simulation.py`，只把耗时的 `m5.instantiate/simulate/checkpoint` 后端替换成记录参数的 fake-m5。输入 parent 故意同时放置 `cpt.100` 和 `cpt.20`，`checkpoint_restore=1`；restore outdir 与 v4 一样是独立目录。实际输出：

```text
gem5 compiled Aug 24 2026 14:12:21
No switch cpu_class provided
Restoring from checkpoint: /tmp/phased-owner-run-vanilla-probe/input/cpt.20
**** REAL SIMULATION ****
Exiting @ tick 123456789 because checkpoint
run_vanilla_restore_input=/tmp/phased-owner-run-vanilla-probe/input/cpt.20
run_vanilla_checkpoint_output=/tmp/phased-owner-run-vanilla-probe/restore/checkpoints/cpt.%d
run_vanilla_restore_output_probe=PASS
```

这同时实测证明：

1. `cpt.*` 按数字而非字典序排序，`-r 1` 在 `cpt.20/cpt.100` 中选择 `cpt.20`；
2. instantiate 输入保持在 cold-style input parent；
3. guest checkpoint 输出被传给 `<restore_out>/checkpoints/cpt.%d`；
4. `max_checkpoints=1` 后原始 exit cause 仍是 `checkpoint`，与 v4 simout gate 匹配；
5. restore 输出目录由 `run_vanilla()` 自行创建。

probe 退出码为 0，未启动真实 CPU/CHI 模型，也没有连接或写入 terminal 3456。结束后宿主权威 cold gem5 仍保持 PID `3754949`，`elapsed=03:11:04`、`CPU time=03:10:58`、`99.9%/Rs`；terminal feeder、cold watcher 与 v4 gate 未受影响。

### 18.47 17:38：实际进入 restore-terminal-wait zero-tick 路径

最终验收不只需要找到 terminal socket，还要求恢复 guest 在 host 输入到达前不要抢先推进到首次昂贵 timer poll。为此把同一 actual-gem5 probe 的 `restore_terminal_wait` 从 0 改为 `0.1` 秒，并让 fake-m5 记录每次 `simulate(delta)`；断言第一项必须为 0，最后一项才必须等于正常 `MaxTick-curTick`。当前主 `gem5.opt` 实际执行结果：

```text
Restoring from checkpoint: /tmp/phased-owner-run-vanilla-probe/input/cpt.20
Waiting 0.1 host seconds for restored terminal input at guest tick 123456789
Restored terminal wait complete at guest tick 123456789
**** REAL SIMULATION ****
Exiting @ tick 123456789 because checkpoint
run_vanilla_restore_input=/tmp/phased-owner-run-vanilla-probe/input/cpt.20
run_vanilla_checkpoint_output=/tmp/phased-owner-run-vanilla-probe/restore/checkpoints/cpt.%d
run_vanilla_terminal_zero_tick_calls=2
run_vanilla_restore_output_probe=PASS
```

等待前后 guest tick 都是 `123456789`，且记录到两次 delta=0；随后才出现正常 bench simulation delta 和 checkpoint。这证明当前 `run_vanilla()` 的 terminal-wait 顺序确实是：

```text
instantiate restored state
  → zero-tick simulate，服务异步 terminal PollQueue
  → host wait window 完成，guest architectural time 未推进
  → REAL SIMULATION
  → guest timer/指令继续执行
```

v4 实际使用 120 秒而非 probe 的 0.1 秒；gate 会在解析到 listener port 后立即启动 nc 并排入验收命令，因此真实 run 将获得充足 host 时间把输入送入 PollQueue。该 probe 验证机制和调用顺序，但不替代最终真实 restore socket 输出；最终仍必须在 restore simout 中取得 uname、16 CPU、online `0-15`、uptime、helper SHA256 和命令完成 marker。

### 18.48 17:40：运行中 binary 与 phased-owner 源码构建一致性

最终 checkpoint 结果必须能精确归属于报告所述代码，而不能只凭进程命令行假设。主 binary 文件证据为：

```text
build/RISCV/gem5.opt
size   = 931304896
mtime  = 2026-08-24 14:10:51.318728 +0800
banner = gem5 compiled Aug 24 2026 14:12:21
sha256 = 72d2d59c0999319c9f18cb60d0d6aedb135287ce7f8348904d978dcbce131ea8
```

用 `find src configs -type f -newer build/RISCV/gem5.opt` 检查整个运行时源码树，只得到：

```text
src/python/m5/__pycache__/simulate.cpython-312.pyc
src/mem/cache/cache_blk.test.cc
```

前者是 binary 启动后生成的 Python bytecode cache，源文件 `src/python/m5/simulate.py` 的 mtime 为 `14:02:24`；后者是 15:25 新增的 standalone 聚焦测试源码，不链接进已经运行的主 gem5。所有 phased-owner 运行时代码都早于主 binary：

```text
src/mem/cache/base.cc                       14:02:05
src/mem/cache/base.hh                       14:02:05
src/mem/cache/cache_blk.hh                  14:01:43
src/python/m5/SimObject.py                  14:01:31
src/python/m5/simulate.py                   14:02:24
src/sim/sim_object.hh                       14:01:30
src/mem/cache/CHI/HnfSLCSFBackend.cc        2026-08-20 09:04:37
src/mem/cache/CHI/SlcSnoopFilter.cc         2026-08-20 09:04:37
```

最后从宿主进程命名空间直接比较正在运行的 executable 与磁盘文件：

```text
/proc/3754949/exe:
    device:inode = 116:1113876681
    size         = 931304896
    mtime        = 2026-08-24 14:10:51.318728 +0800

build/RISCV/gem5.opt:
    device:inode = 116:1113876681
    size         = 931304896
    mtime        = 2026-08-24 14:10:51.318728 +0800
```

两者完全相同且没有 `(deleted)`/被替换状态。因此第四轮 PID `3754949` 确实运行 SHA256 `72d2...31ea8` 的当前 phased-owner binary；后续 cold checkpoint 与 restore 输入可以归因于这一精确构建。`configs/common/Simulation.py` 属于启动时动态解释的配置层，其当前 restore path 又已由 18.46/18.47 的同一 binary probe 单独验证。

### 18.49 18:00：unused-clocks 后持续监控，进程仍为计算态

17:49–17:59 对 terminal、cold watcher 和 v4 restore-gate 三个日志连续执行约 10 分钟 follow；期间没有 shell/checkpoint marker，也没有 crash/FAIL。监控窗口结束时重新读取宿主进程表：

```text
gem5 PID       = 3754949
elapsed        = 03:34:55
CPU time       = 03:34:49
CPU / STAT     = 99.9% / Rs
terminal feeder= PID 3757013/3757025/3757026，alive
cold watcher   = PID 3758449，alive
v4 restore gate= PID 518853，alive
checkpoint dirs= 0
watcher FAIL   = 0
gate FAIL      = 0
```

`elapsed-CPU time` 仍只有约 6 秒，并与 17:30、17:38 快照连续增长；所以“暂无 printk”仍表示 guest 正在 detailed event queue 中执行，而不是进程 sleep、zombie、被宿主暂停或 watcher 丢失。最后 guest marker 仍为 `[0.026816] clk: Disabling unused clocks`，当前精确位置不变。第三轮同配置 unused-clocks→shell 的历史量级约 2 小时 23 分，因此这段 39 分钟静默尚不异常；不更换 artifact、不缩短 gate，继续等待真实 `/bin/sh`。

### 18.50 18:32：三段十分钟窗口均无异常，CPU 时间继续线性增长

18:00 之后又完成两段各约十分钟的 terminal/watcher/gate follow，三类日志均没有 shell/checkpoint/crash/FAIL 新事件。18:32 宿主快照：

```text
gem5 PID       = 3754949
elapsed        = 04:07:25
CPU time       = 04:07:19
CPU / STAT     = 99.9% / Rs
terminal feeder= PID 3757013/3757025/3757026，alive
cold watcher   = PID 3758449，alive
v4 restore gate= PID 518853，alive
checkpoint dirs= 0
watcher FAIL   = 0
gate FAIL      = 0
```

相对 18:00，elapsed 与 CPU time 同步增加约 32 分钟，二者差仍约 6 秒，证明 detailed simulation 连续占用 CPU。unused-clocks marker 的 NFS mtime 为 17:20:35；截至本快照约过去 1 小时 12 分，仍低于第三轮同配置 unused-clocks→shell 约 2 小时 23 分的实测量级。当前无证据支持 hang 或回归；保持权威 PID、terminal 连接和所有严格 gate 不变。

### 18.51 19:44：第四轮完整进入 shell、真实 terminal 与 checkpoint 生成均通过

第四轮没有卡在 Linux init。权威 cold gem5 PID `3754949` 最终输出：

```text
[    0.004307] smp: Brought up 1 node, 16 CPUs
[    0.038540] Freeing unused kernel image (initmem) memory: 4112K
[    0.038616] Run /bin/sh as init process
```

terminal feeder 从 14:25 开始保持连接，直到 shell 可用后才完成命令序列；其最终状态是：

```text
2026-08-24 14:25:15 CST connecting terminal port=3456 gem5_pid=3754949
2026-08-24 19:44:16 CST terminal handshake received; nc exit=0
```

guest 的真实返回值如下，不是 nc 本地回显或宿主侧推断：

```text
PHASED_OWNER_COLD_SHELL_20260824
PHASED_OWNER_PROC_SYS_READY_20260824
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
PHASED_OWNER_CPU_COUNT=16
PHASED_OWNER_CPU_ONLINE=0-15
PHASED_OWNER_UPTIME=0.05 0.78
PHASED_OWNER_SHELL_PID=137
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38  /tmp/chi_guest_checkpoint_repo
PHASED_OWNER_TRIGGER_CHECKPOINT_20260824
```

开头可见一段命令文本与 late-init printk 交错，这是 feeder 在终端可写后排入的 TTY echo；其后每个 marker 和命令返回值都由 guest shell 独立输出，CPU 数、online mask、uptime、PID 和 SHA256 均有明确值。最后执行 RISC-V guest checkpoint helper，gem5 正常打印：

```text
Writing checkpoint
Exiting @ tick 67004810444 because checkpoint
```

因此第四轮 cold run 的结论是：

1. 原始 Linux `NULL pointer @ 0x4` 没有复现；
2. 16 核 SMP、完整 init、`/bin/sh` 和真实 terminal 输入全部通过；
3. checkpoint 由 guest helper 主动触发，不是宿主强杀、超时或 crash 后残留；
4. gem5 以标准 `checkpoint` exit cause 在 tick `67004810444` 正常退出。

第四轮 artifact 固定为：

```text
m5out/linux-16core-128mb-phased-owner-cold-detached-20260824/
└── checkpoints/
    └── cpt.67004810444/
        ├── m5.cpt
        └── system.physmem.store0.pmem
```

### 18.52 第四轮文件完整性与 CHI canonical 审计通过

cold watcher 于 19:44:28 检测到新 checkpoint。文件大小为：

```text
m5.cpt                                  39,530,419 bytes
system.physmem.store0.pmem               5,984,319 bytes（gzip）
解压后的 pmem                          134,217,728 bytes（128 MiB）
```

SHA256 为：

```text
m5.cpt
160144e34127d1da8becf2a9b54cf03c44e2e8d1dd008c3e8c7adf19d51c59aa

压缩 pmem
c393e14dc5a6d4bb1628fc8898f6e8d44b4708ccb69c69b5497be1ad98eef98d

解压 pmem
d9e3402da5568c7ed3f89def3b0d127aca5265fd6da7a06b4dca37933f82efa4
```

自动 gate 的结果：

```text
gzip.status       = PASS
chi_audit.status  = PASS
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

这说明第四轮失败不是压缩流损坏、截断、物理内存尺寸错误，也不是仍有未 canonicalize 的 CHI SLC/SF line。checkpoint 的容器和 CHI 边界状态是可解析且自洽的；失败集中在 classic private-cache 写回后 pmem 所表达的最终 Linux 地址空间语义。

### 18.53 精确栈审计仍为 0/15，恢复门禁正确拒绝坏 artifact

独立审计器从 `m5.cpt` 读取每个 CPU 的 PC、SP、live `s0`、live `ra`、SATP 与 serialized DTLB，再对解压后的 pmem 自行执行 Sv39 page walk，并读取 Linux idle continuation 栈帧。CPU10 当时处于用户态，按规则排除；其余 15 个 idle CPU 的结果全部失败：

```text
idle_summary passed=0 total=15
stack_audit.status = FAIL rc=1
```

失败不是单一宽松条件造成。15 个 idle CPU 的 frame-range 检查全部成立，但 pmem 中 saved `s0` 全部不等于 live `s0`，saved `ra` 也全部不等于 live `ra=0xffffffff80470480`。同时 serialized DTLB 与 pmem page walk 只有 CPU2、CPU7、CPU11、CPU15 四个组尾 hart 一致；其余映射已被覆盖。

关键结果按组归纳如下：

| CPU 组 | 各 CPU serialized DTLB 物理栈 | pmem page walk 最终结果 | pmem saved `ra` | 结果 |
|---|---|---|---:|---:|
| 0–2 | `0x81584e60` / `0x8158ae60` / `0x8158ee60` | 全部变成 `0x8158ee60`（CPU2） | `0x3` | 0/3 |
| 3 | `0x81203e10` | 退化为 `0x80203e10` 的 2 MiB 映射 | `0x64a2644260e228de` | 0/1 |
| 4–7 | `0x81592e60` / `0x81596e60` / `0x8159ae60` / `0x8159ee60` | 全部变成 `0x8159ee60`（CPU7） | `0x7` | 0/4 |
| 8、9、11 | `0x815c2e60` / `0x815c6e60` / `0x815cee60` | 全部变成 `0x815cee60`（CPU11） | `0x0` | 0/3 |
| 12–15 | `0x815d2e60` / `0x815d6e60` / `0x815dae60` / `0x815dee60` | 全部变成 `0x815dee60`（CPU15） | `0xf` | 0/4 |

这种结果与第三轮 all-valid checkpoint 的成组别名模式基本一致。它具有两个非常强的特征：

1. 错误不是随机 bit flip，而是每个共享层级/cluster 中“最后一个 peer”的完整旧页表或栈内容系统性覆盖其他 peer；
2. phased-owner 已解决同一 cache level 内 shared→Writable→Dirty 的最后写入者优先级，但没有解决 L1 与 L2 两个 classic cache level 之间的覆盖关系。

v4 restore gate 明确要求 cold checkpoint 的 `stack_audit.status` 必须是 `PASS`，所以它于 19:44:39 输出：

```text
FAIL: cold-checkpoint stack_audit.status is not PASS
```

restore outdir 没有被创建，任何 CPU/CHI restore 都没有启动。该行为是预期的安全保护：若直接恢复这个 0/15 artifact，历史证据表明 Linux 会在首次 timer continuation 从错误 idle 栈返回，随后跳入低地址或 fault storm。第四轮 checkpoint 被完整保留用于取证，但绝不作为最终恢复输入。

### 18.54 最新根因：同层 phased-owner 正确，但跨层仍是 L1→L2，stale L2 可最后覆盖

当前 `memWriteback(root)` 的实际顺序是：

```text
CHI SLC（显式 priority=-1）
→ PageTableWalker cache（cache_level=0）
→ 所有 L1（cache_level=1，内部 phase 0→1→2）
→ 所有 L2（cache_level=2，内部 phase 0→1→2）
```

每个 level 内的全局三阶段确实保证 dirty/current-owner peer 在 shared/former-owner peer 之后写。然而 `BaseCache::writebackVisitor()` 不是直接对最终 pmem 做离线 overlay，而是把 functional `WriteReq` 从该 cache 的 `memSidePort` 向下发送。由此产生跨层漏洞：

```text
正确 L1 owner 写一条 line
  → functional request 到达它自己的 L2
  → 若该 L2 仍是 Dirty/current owner，functionalAccess 可在 L2 满足请求
  → 正确值暂时停在/更新该 L2

随后进入全局 L2 writeback
  → 其他 peer L2 的 clean/shared 旧副本也依次写出
  → 某个排序靠后的 stale peer L2 再次覆盖最终 pmem
```

所以第四轮不是 phased-owner 分类本身无效，而是它只在一个 level 的 peer 集合中定义“谁最后写”，没有把真正靠近 CPU 的上层 owner 放到所有下层副本之后。审计中 CPU0–2→CPU2、CPU4–7→CPU7、CPU8/9/11→CPU11、CPU12–15→CPU15 的规律，正是“每组最后一个下层副本赢得最终 pmem”的数据指纹。

下一版修复将保持 SLC 和只读 walker 优先清空，同时把正 cache level 改成 lower/far→upper/near：

```text
SLC → walker/read-only → L3（如有）→ L2 → L1
```

同一 level 内仍保持：

```text
phase 0: shared/former-owner
phase 1: clean Writable owner
phase 2: Dirty owner
```

其工作原理是先让 L2 把自身旧/脏副本写完并清除 checkpoint dirty 状态，再让正确 L1 owner 最后向下发送 functional write。此时自己的 L2 已完成 writeback，L1 的正确值可继续传播到最终 backing memory；也不会再有后续 peer L2 覆盖它。若最新值只存在于 L2 而 L1 没有 valid copy，L2 先前的写回仍然保留，因此不会丢失 L2-only owner。

该判断目前是由源码控制流与第四轮成组数据共同支持的根因结论，尚需第五轮新 artifact 的精确 15/15 审计来完成证伪式验证。只有第五轮依次通过 gzip、CHI、15/15、真实 restore、restore terminal 和恢复后二次 checkpoint 审计，任务才可关闭。

### 18.55 截至 19:50 的精确卡点与下一步

当前没有运行中的 cold/restore gem5；第四轮及其 feeder、watcher、v4 gate 都已正常退出。卡点已经从此前各阶段逐步收敛：

```text
Linux NULL crash                    已解决
16 核 SMP / init / shell            已通过
cold terminal 输入                  已通过
guest helper 触发 checkpoint        已通过
checkpoint gzip/尺寸/hash           已通过
CHI SLC/SF canonical                已通过
classic-cache→pmem 最终语义         失败（idle 栈 0/15）
restore                             被安全门禁阻止，尚未执行
restore 后 terminal                 尚未执行
恢复后二次 checkpoint              尚未执行
```

接下来的严格顺序是：

1. 把正 `cache_level` 的 checkpoint 写回顺序改成 L3→L2→L1，并保留同层三阶段 owner 优先级；
2. 更新真实嵌入式 gem5 Python phase-order probe，运行 C++ cache/CHI 聚焦测试并重建主 `gem5.opt`；
3. 使用全新 outdir 启动第五轮 16 核 Linux cold run，原第四轮目录保持 immutable；
4. guest shell 再次返回 uname、CPU count、online mask、uptime 与 helper SHA256 后生成新 checkpoint；
5. 必须取得 gzip PASS、CHI PASS 和 idle stack **15/15**；
6. 只有 15/15 gate 通过后才启动 restore，验证无 Oops/panic/fault storm，并取得真实 terminal 返回；
7. 在恢复后的 guest 内生成第二个 checkpoint，再对该 artifact 重复 gzip、CHI 和 15/15 审计。

### 18.56 19:58：lower-to-upper 修复已构建，实际顺序 probe 与 176/176 测试通过

`src/python/m5/simulate.py` 的默认 priority 现分为三段：

```text
显式 checkpoint_writeback_priority：保持配置值，SLC=-1
无 cache_level 或 cache_level=0：priority=0
正 cache_level：priority=1,000,000-cache_level
```

因此普通 classic 层级按 L3、L2、L1 递增执行；同一 level 内仍按 `is_read_only` 让 I-cache 先于 D-cache，并对所有 peer 全局展开 p0、p1、p2。修改后先用旧 binary 运行更新后的 probe，旧 binary 如预期退出 1，捕获到：

```text
slc → walker → icache/dcache → l2 → l3
RuntimeError: phase order mismatch
```

这一步证明 probe 不是无条件 PASS，也能实际区分旧嵌入 Python 与新源码。随后执行：

```text
scons build/RISCV/gem5.opt -j64
```

构建退出码为 0；PNG、HDF5 和 backtrace 是环境中已有的可选功能 warning，没有编译或链接错误。新 binary 信息：

```text
compiled = Aug 24 2026 19:55:26
size     = 931,304,856 bytes
mtime    = 2026-08-24 19:53:59.389988 +0800
sha256   = d12cd3829ba65e4f0e6903424922d47267b17e8ff313656fe619f99fc5c9c2d7
```

新 binary 执行同一 probe 得到退出码 0 和精确事件序列：

```text
checkpoint_writeback_phase_order=PASS
slc:legacy,
walker1:p0,walker0:p0,walker1:p1,walker0:p1,walker1:p2,walker0:p2,
l3:p0,l3:p1,l3:p2,
l2b:p0,l2a:p0,l2b:p1,l2a:p1,l2b:p2,l2a:p2,
icache:p0,dcache:p0,icache:p1,dcache:p1,icache:p2,dcache:p2
```

聚焦回归结果：

| 测试二进制 | 通过 | 失败 |
|---|---:|---:|
| `cache_blk.test.opt` | 4 | 0 |
| `hnf_slcsf_backend.test.opt` | 24 | 0 |
| `slc_snoop_filter.test.opt` | 17 | 0 |
| `hnf_slcsf.test.opt` | 131 | 0 |
| **合计** | **176** | **0** |

death test 中故意触发的 panic/fatal 仍由 gtest 捕获，各 suite 最终退出码全部为 0。Python 静态编译检查与 `git diff --check` 也通过。第五轮启动脚本会把 binary 与 `simulate.py` SHA256 固化到 cold audit 目录；restore gate 还会在恢复前重新计算 binary SHA256，若与 cold 时不一致则拒绝恢复，从而确保 cold 与 restore 使用同一个精确实现。

### 18.57 20:02：第五轮 lower-to-upper 权威 cold run 已持久启动

第五轮使用全新且启动前确认不存在的目录：

```text
cold:
m5out/linux-16core-128mb-lower-to-upper-cold-detached-20260824/

restore:
m5out/linux-16core-128mb-lower-to-upper-restore-detached-20260824/
```

启动时间、进程和固定输入为：

```text
start time      = 2026-08-24 20:00:55 CST
cold gem5 PID   = 1154493
terminal port   = 3456
gem5 SHA256     = d12cd3829ba65e4f0e6903424922d47267b17e8ff313656fe619f99fc5c9c2d7
simulate.py SHA = 379e78e390c810c82ec0632f2ef02d97f77fd1751134418dfd7ceecf574da6a0
guest helper    = 53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
firmware image  = 0642b2626cd26f281ac99ba0d547a5f3cc4ca6fc45c27a40f446218eb88f904f
```

运行中 `/proc/1154493/exe` 与磁盘 binary 的证据完全一致：

```text
/proc/1154493/exe  116:1031114321  931304856 bytes  2026-08-24 19:53:59.389988 +0800
build/RISCV/gem5.opt
                     116:1031114321  931304856 bytes  2026-08-24 19:53:59.389988 +0800
```

20:02 宿主状态：

```text
PID 1154493  elapsed=81s  CPU time=80s  CPU=98.9%  STAT=Rs
```

命令行固定为 16 个 XiangShan O3 CPU、128 MiB SimpleMemory、6×4 CHI、关闭 prefetcher、max tick `120000000000`、guest checkpoint 上限 1。terminal 已实际输出：

```text
system.terminal: Listening for connections on port 3456
51535755: system.terminal: attach terminal 0
```

三个外围自动化均已持久启动：

```text
20:01:34 terminal feeder connecting port=3456, gem5_pid=1154493
20:01:36 checkpoint watcher started for gem5_pid=1154493
20:01:33 restore gate v5 waiting for exact 15/15 lower-to-upper audit
```

v5 gate 除 gzip、CHI、15/15、正常 checkpoint exit 外，还会比较 restore 前 binary SHA256 与 cold 启动时记录值；只要任一条件失败，restore outdir 就不会创建。当前 simout 已进入 `**** REAL SIMULATION ****`，尚未出现第一条 Linux printk，属于刚开始执行 firmware/boot 的正常位置，不能记为 Linux hang。后续位置仍以 guest 自身 marker、宿主 CPU time 连续增长和 watcher 结果共同判定。

### 18.58 第五轮实际实例的 cache-level 配置审计

为排除“合成 probe 顺序正确，但真实 16 核实例没有设置层级”的可能，直接审计第五轮启动后生成的 `config.ini`。计数为：

```text
32 × cache_level=0
    = 16 个 DTB walker cache + 16 个 ITB walker cache

32 × cache_level=1
    = 16 个 D-cache + 16 个 I-cache
    其中 16 个 I-cache 的 is_read_only=true

64 × cache_level=2
    = 16 个 L2 wrapper × 每个 4 个 slice

16 × cache_level=3 且 checkpoint_writeback_priority=-1
    = system.home_node00..15.slcsf
```

本配置没有额外 classic L3；16 个 HN-F SLC 通过显式 `-1` 脱离默认 level-3 映射。因此第五轮实际会执行：

```text
16 HN-F SLC（priority -1）
→ 32 walker cache（priority 0）
→ 64 L2 slice（priority 999998）
→ 32 L1 I/D cache（priority 999999）
```

每个 priority group 内仍执行所有对象的 p0，再执行所有 p1，最后执行所有 p2；L1 的同一 phase 内 16 个只读 I-cache 排在 D-cache 前。这与修复假设和实际二进制 probe 完全一致，排除了配置未生效、L1/L2 level 颠倒或 SLC 意外回到默认 level-3 排序。

### 18.59 20:12：第五轮被主动中止，不是 Linux crash，也没有生成 checkpoint

第五轮不是因为出现新的 guest Oops、gem5 panic 或超时而结束，而是在运行期间继续审阅第四轮真实日志与 checkpoint 写回源码后，发现 lower-to-upper 假设缺少一个关键前提。继续等待数小时只会生成一个算法上已可证明不安全的 artifact，因此于约 20:12 主动终止 PID `1154493`。

停止时的客观状态如下：

```text
start time                     2026-08-24 20:00:55 CST
stop / watcher failure time    2026-08-24 20:12:36 CST
terminal                       port 3456，handshake 已收到
REAL SIMULATION                已进入
Linux version marker           无
Run /bin/sh marker             无
guest checkpoint marker        无
checkpoints/cpt.*              0 个
restore run                    未启动
```

feeder 因 gem5 被主动停止而在 20:12:34 正常结束，日志为 `terminal handshake received; nc exit=0`。checkpoint watcher 随后记录：

```text
2026-08-24 20:12:36 CST FAIL: gem5 exited before a checkpoint marker appeared
```

v5 restore gate 在 20:13:34 观察到 watcher FAIL 后退出：

```text
FAIL: cold-checkpoint watcher reported failure
```

第五轮 outdir 被保留为失败实验取证，但其中没有可恢复 checkpoint。`audit/aborted_reason.txt` 已明确写入 `ABORTED_BY_DEBUGGER`，并说明该目录的任何中间输出都不得作为验收证据。宿主复查确认 PID `1154493`、restore gate PID `1157071` 以及所有包含 `lower_to_upper` 的 feeder/watcher 进程均已退出，没有残留进程与第六轮竞争 terminal 端口或 CPU。

这次主动中止非常重要：它不是“第五轮跑失败”，而是通过静态控制流和上一轮真实动态日志在 checkpoint 产生前证伪了实验假设，避免把约 4–5 小时继续花在已知不充分的写回顺序上。

### 18.60 最终根因修正：functional checkpoint 写会先污染尚未执行 owner phase 的 peer cache

此前 18.39/18.54 中把 RISC-V PageTableWalker cache 当作“实际只读或不可能持有写 owner”的前提是错误的，以下新证据优先级更高并明确覆盖该判断。

第一，真实配置并未把 RISC-V walker cache 设为只读。`configs/common/Caches.py` 的逻辑是：

```python
if buildEnv['TARGET_ISA'] in ['x86', 'riscv']:
    is_read_only = False
```

第二，第四轮 `simerr` 不是只有理论可能，而是实际出现了 **19 个** `walker_cache checkpoint phase 2 (dirty-owner)` 对象，覆盖多个 CPU 的 ITB/DTB walker cache。例如 CPU1 的 DTB/ITB、CPU5 的 DTB/ITB、CPU13 的 DTB/ITB 都持有 Dirty owner line；CPU5 ITB walker 一次写出了 10 条 dirty line。因此 walker 不仅可能参与页表数据竞争，而且已经在权威 Linux checkpoint 中参与。

第三，也是最关键的一点：旧 `BaseCache::writebackVisitor()` 使用：

```cpp
memSidePort.sendFunctional(&packet);
```

这不是“把当前 cache snapshot 直接复制到最终 pmem”。当 packet 进入 `CoherentXBar::recvFunctional()` 时，只要系统没有 bypass cache，就会先执行：

```cpp
forwardFunctional(pkt, cpu_side_port_id);
```

也就是向其他 snooping CPU-side cache 广播 functional 请求。匹配的 peer `BaseCache::functionalAccess()` 又会先调用：

```cpp
pkt->trySatisfyFunctional(..., blk->data)
```

再计算 `have_dirty` 并决定是否停止继续向下。对 checkpoint 的 functional `WriteReq` 而言，`trySatisfyFunctional` 会把 packet 中的数据写进匹配 cache block。因此完整破坏链是：

```text
phase 0 的 stale/shared copy 开始写回
  → WriteReq 从 memSidePort 进入 CoherentXBar
  → xbar 在到达 memory 之前先 snoop peer cache
  → peer 中恰好存在同地址 Dirty/current-owner block
  → trySatisfyFunctional 先用 stale packet data 改写 dirty block
  → have_dirty=true，请求被 dirty peer 满足并停止
  → 轮到该 peer 的 phase 2 时，它写出的已经是被污染后的 stale 数据
```

这解释了为什么以下方案都会失败：

- 只写 dirty line：漏掉 restore 必须使用的 clean-valid 数据；
- 写所有 valid line：任意最后副本可能覆盖 owner；
- 每层内部 phased-owner：跨层 peer 仍会覆盖；
- L3→L2→L1 lower-to-upper：早期 stale functional write 仍能在真正 owner phase 前直接修改 owner 本体。

因此问题不只是“最后谁写 pmem”的排序问题，而是旧写回操作在排序过程中还会**改变尚未写出的输入快照**。只要这一副作用存在，就无法单靠任何线性 object order 保证最终 owner 胜出。这也是第五轮 lower-to-upper 必须在 checkpoint 前被中止的决定性原因。

### 18.61 direct-pmem + hierarchy-wide owner phase 修复、构建与验证

修复分成两个互相依赖的部分。

#### 18.61.1 classic cache snapshot 直接写 PhysicalMemory

`src/mem/cache/base.cc` 的 `writebackVisitor()` 现在先重建 block address，再判断它是否属于系统普通内存：

```cpp
const bool direct_to_memory = system->isMemAddr(block_addr);
if (direct_to_memory) {
    system->getPhysMem().functionalAccess(&packet);
} else {
    memSidePort.sendFunctional(&packet);
}
```

普通 physical-memory line 不再经过 cache 的 `memSidePort`、CoherentXBar 或任何 snooper，而是把当前 cache block 的不可变快照直接 overlay 到 backing PhysicalMemory。仅为理论上可能存在的 cached MMIO/non-memory line 保留原 routed functional fallback。

每个非空 phase 的日志同时输出：

```text
selected / sticky / writable / dirty / direct-pmem / routed-nonmem
```

这样端到端 run 可以审计新路径是否真的执行，而不是仅凭源码推断。第六轮 cold 与 restore gate 都要求至少一条 `direct-pmem>0`，且任何 `routed-nonmem>0` 都立即 FAIL。

#### 18.61.2 owner phase 在整个 classic hierarchy 上全局展开

`src/python/m5/simulate.py::memWriteback()` 仍按 SLC、walker、L3、L2、L1 排出稳定顺序，但不再对每个对象或每个 level 一次跑完 p0/p1/p2。现在先收集所有支持 phase 的 classic cache，然后执行：

```text
所有 classic cache 的 phase 0：shared / former owner
→ 所有 classic cache 的 phase 1：clean Writable owner
→ 所有 classic cache 的 phase 2：Dirty owner
```

每个 phase 内才按 lower/far→upper/near 和 read-only-first 稳定排序。CHI SLC 是 non-phased、显式 priority `-1`，仍在所有 classic phase 之前 overlay。由于 classic snapshot 已直接写 pmem，phase 0 stale copy 不会再修改 phase 2 owner；最后写入的 Dirty/current owner 才能安全决定最终 memory image。

#### 18.61.3 主构建与精确顺序 probe

主构建：

```text
scons build/RISCV/gem5.opt -j64       PASS
compiled                             Aug 24 2026 20:16:05
size                                 931,282,688 bytes
sha256                               2dc5ae354b53d1c324e044f4e0a4b2fe74483aec5f79e7c0111261ca9d4bcad6
git diff --check                     PASS
simulate.py py_compile               PASS
```

PNG、HDF5 和 backtrace 仍只是本机缺少可选依赖的已有 warning，没有 C++ 编译或链接错误。嵌入新 binary 的 phase-order probe 返回：

```text
checkpoint_writeback_phase_order=PASS
slc:legacy,
walker1:p0,walker0:p0,l3:p0,l2b:p0,l2a:p0,icache:p0,dcache:p0,
walker1:p1,walker0:p1,l3:p1,l2b:p1,l2a:p1,icache:p1,dcache:p1,
walker1:p2,walker0:p2,l3:p2,l2b:p2,l2a:p2,icache:p2,dcache:p2
```

这与第五轮 binary 的“walker 自己跑完 p0/p1/p2，再 L3 自己跑完三阶段”不同，证明 hierarchy-wide phase 已真正嵌入 `gem5.opt`。

#### 18.61.4 真实 Dirty cache checkpoint probe

只检查 Python 调用顺序还不足以证明 C++ direct-pmem 数据路径，因此另外构造了会执行真实 store/load/exit 的最小 RV64 程序，并连接 AtomicSimpleCPU、L1I、L1D、L2 和 SimpleMemory。程序正常退出后生成 checkpoint，持久日志为：

```text
system.cpu.icache phase 0: 2 lines，2 direct-pmem，0 routed-nonmem
system.l2         phase 1: 4 lines，4 direct-pmem，0 routed-nonmem
system.cpu.dcache phase 1: 1 line， 1 direct-pmem，0 routed-nonmem
system.cpu.dcache phase 2: 1 Dirty line，1 direct-pmem，0 routed-nonmem
probe_exit_cause=exiting with last active thread context
direct_pmem_checkpoint_probe=PASS
```

`/tmp/direct-pmem-owner-probe-cpt-v7-20260824/m5.cpt` 实际存在；正则检查确认至少一个非零 direct-pmem 行，并且全文没有非零 routed-nonmem。这条 phase-2 L1D Dirty line 是本轮修复最直接的动态证据。

探针搭建过程中还发现并隔离了三个与主 Linux bug 无关、但容易误导后续调试的问题：

1. `configs/example/memtest.py` 的 `-m` 是 maxtick，不是 max-load 数；首次传入 `-m 1000000` 后仿真很快返回，但随后 `m5.checkpoint()` drain 时 MemTest 继续发请求，表现为反复 `Entering event queue`，不能完成 checkpoint。
2. 改用 `-l 1000` 后，首个 tester 触发 maximum-load exit，但其他 MemTest/事件在 drain 中仍继续产生流量，同样无法形成静止 checkpoint。两个临时 MemTest 进程都被精确 Ctrl-C 终止，未把半成品目录计为 PASS。
3. 仓库自带 RISC-V `hello` 在该定制 build 的简单 CPU SE probe 中于 PC `0x102ae` 取到 zero instruction，而 objdump 对应位置实际是 `addi a0,a0,232`；同时 `configs/example/se.py::setDefaultArgs()` 会无条件把用户指定的 `--mem-type=SimpleMemory` 覆盖回未编入当前 binary 的 `DRAMsim3`。这里没有把未定位的 SE 兼容问题混入 Linux 根因，而是改用无 RVC、无 libc 的最小 RV64 汇编和独立配置完成所需数据路径验证。

#### 18.61.5 聚焦回归

| 测试二进制 | 通过 | 失败 |
|---|---:|---:|
| `cache_blk.test.opt` | 4 | 0 |
| `hnf_slcsf_backend.test.opt` | 24 | 0 |
| `slc_snoop_filter.test.opt` | 17 | 0 |
| `hnf_slcsf.test.opt` | 131 | 0 |
| **合计** | **176** | **0** |

四个 suite 退出码均为 0。输出中的 panic/fatal 属于 death test 故意触发并由 gtest 捕获的负向用例，不是测试失败。

### 18.62 20:37：第六轮 direct-pmem-owner 权威 cold run 已持久启动

第六轮使用启动前确认不存在的全新目录：

```text
cold:
m5out/linux-16core-128mb-direct-pmem-owner-cold-detached-20260824/

restore:
m5out/linux-16core-128mb-direct-pmem-owner-restore-detached-20260824/
```

启动快照：

```text
start time        = 2026-08-24 20:31:14 CST
cold gem5 PID     = 1315430
terminal port     = 3456
20:32:50 host     = elapsed 96s, CPU time 95s, 99.2% CPU, STAT=Rs
gem5 SHA256       = 2dc5ae354b53d1c324e044f4e0a4b2fe74483aec5f79e7c0111261ca9d4bcad6
simulate.py SHA   = 182b570caf07998fde0ca70d72c976fcff9d430377c1ec93002167d5a16026f0
base.cc SHA       = 66c90c52eb021078a7c87646184e1b58708c2de0f96bb9dd8ee04fca06254e8c
base.hh SHA       = aecd45ea2beb7fd68ff37a9132054602af48ec06ff2b93978c472e1467af1f88
```

terminal 与三项外围自动化均已实际启动：

```text
67151820: system.terminal: attach terminal 0
2026-08-24 20:32:02 CST feeder connecting terminal port=3456
2026-08-24 20:32:07 CST checkpoint watcher started for PID 1315430
2026-08-24 20:32:07 CST restore gate v6 waiting for direct-route + gzip + CHI + 15/15
```

宿主 `pgrep` 同时确认 feeder 的 shell/pipe、checkpoint watcher 和 restore gate 都仍存活。冷启动命令仍固定为 16 个 XiangShan O3 CPU、6×4 CHI、128 MiB SimpleMemory、关闭 prefetcher、max tick `120000000000`、只允许生成一个 checkpoint。cold 脚本在启动时冻结 binary、`simulate.py`、`base.cc`、`base.hh` 四个 SHA；restore 前 gate 会重新计算 gem5 SHA，若 binary 被重建或替换就拒绝恢复。

截至本节快照，运行已经进入：

```text
**** REAL SIMULATION ****
```

但尚未出现：

```text
Linux version
Run /bin/sh as init process
DIRECT_PMEM_OWNER_COLD_SHELL_20260824
DIRECT_PMEM_OWNER_TRIGGER_CHECKPOINT_20260824
Exiting @ tick ... because checkpoint
```

所以“目前 Linux 跑到哪里”的准确答案是：第六轮已经完成配置实例化、载入 128 MiB firmware payload、进入真实 event queue，并完成 host terminal attach；尚处于 OpenSBI/早期启动指令仿真，未到第一条 Linux printk。进程 CPU time 持续增长、无 Oops/panic/fatal/Program aborted，当前没有证据表明它卡死在某条 Linux 路径。

当前唯一未闭环的卡点仍是端到端 artifact 语义，而不是 Linux cold boot 能力：

```text
新 direct-pmem 写回是否让 cold checkpoint idle stack 达到 15/15      等待第六轮 artifact
第六轮 checkpoint gzip / CHI / direct-route                         尚未执行
从通过 gate 的第六轮 checkpoint 恢复                              尚未执行
restore 后无 Oops/fault storm 且真实 terminal 返回                  尚未执行
restore guest 生成第二 checkpoint 并再次通过全部审计                尚未执行
```

v6 gate 的严格顺序是：cold guest marker → 正常 checkpoint exit → exactly one artifact → `classic_route=PASS` → gzip PASS → CHI PASS → idle stack 15/15 → binary SHA equality → restore → real terminal output → guest re-checkpoint → 对第二 artifact 再跑 route/gzip/CHI/15/15。任何一步失败都会留下 FAIL 日志并停止后续恢复，不会用已知错误 checkpoint 冒险启动 Linux restore。

### 18.63 20:47：第六轮已进入 Linux，尚未到原始 NULL 崩溃邻近点

经过首个连续观察窗口，第六轮从只有 `REAL SIMULATION` 推进到真实 Linux early boot。新增串口输出包括：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ... #69 SMP ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI specification v2.0 detected
[    0.000000] SBI TIME extension detected
[    0.000000] SBI IPI extension detected
[    0.000000] SBI RFENCE extension detected
[    0.000000] SBI DBCN extension detected
[    0.000000] earlycon: sbi0 at I/O port 0x0
[    0.000000] printk: legacy bootconsole [sbi0] enabled
[    0.000000] Initmem setup node 0 [mem 0x0000000080000000-0x0000000087ffffff]
```

20:47 宿主复查：

```text
PID      = 1315430
elapsed  = 950 s
CPU time = 949 s
CPU      = 99.9%
STAT     = Rs
```

这证明进程不是卡在配置、firmware 或第一条 Linux printk，CPU time 在 5 分钟窗口内保持近 1:1 线性增加。日志中没有 `Unable to handle kernel NULL pointer`、`Oops [#`、`Kernel panic`、gem5 `panic:`/`fatal:` 或 `Program aborted`。

但此时还不能把原始 NULL crash 单独以本轮证据判定为再次通过：用户最初给出的崩溃紧跟在 `riscv: base ISA extensions` 与 `riscv: ELF capabilities` 之后，而本轮最新输出仍停留在更早的 `Initmem setup`。因此本节状态严格记为“已进入 Linux、正向接近旧崩溃点”，下一判据必须实际观察到 `riscv: ELF capabilities` 之后继续出现后续 printk；随后仍需 16 CPU online、shell、checkpoint 和完整 restore 验收。

### 18.64 20:53：第六轮已正面越过用户最初的 NULL 崩溃点

第二个观察窗口取得了本轮针对原始问题的直接证据。串口先打印：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
```

旧失败日志会紧接着出现：

```text
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000004
Oops [#1]
```

而第六轮没有任何上述 signature，继续打印：

```text
[    0.000000] percpu: Embedded 14 pages/cpu ...
[    0.000000] pcpu-alloc: [0] 00 ... 15
[    0.000000] Kernel command line: ...
[    0.000000] Memory: 108580K/131072K available ...
[    0.000000] SLUB: ... CPUs=16, Nodes=1
[    0.000000] rcu: Hierarchical RCU implementation.
[    0.000000] NR_IRQS: 64, nr_irqs: 64, preallocated irqs: 0
```

20:53 宿主进程同时为：

```text
PID      = 1315430
elapsed  = 1322 s
CPU time = 1321 s
CPU      = 99.9%
STAT     = Rs
```

因此“冷启动在 ELF capability 后立刻 NULL @ 0x4”已经由当前 direct-pmem-owner binary 的实际第六轮运行再次证明消失，而不是只引用旧成功 run。这里仍不能完成整个目标：还要看到 `smp: Brought up ... 16 CPUs`、`Run /bin/sh`、terminal 命令、checkpoint route/gzip/CHI/15/15、无崩溃 restore、restore terminal 和恢复后二次 checkpoint。

### 18.65 20:59：当前精确卡点为 secondary CPU bring-up；一次沙箱 PID 假阴性已排除

第六轮最新 guest 串口已经继续推进至：

```text
[    0.001149] EFI services will not be available.
[    0.001403] smp: Bringing up secondary CPUs ...
```

截至 20:59，尚未出现 `CPU1: ...`、`smp: Brought up ... 16 CPUs` 或后续 init/shell 文本，因此当前 Linux 内部位置可以精确写成：**boot CPU 已完成早期内存、RCU、IRQ、clocksource、ASID 等初始化，正在等待/执行 secondary hart online 流程**。这比“Linux early boot”更窄，也是本轮此刻唯一可见的 guest 级卡点。

中途在受限 shell 内执行 `ps -p 1315430,1319528` 返回空表，表面上像 gem5 和 restore gate 同时退出；但这是工具沙箱使用独立 PID namespace 导致的假阴性。随后在宿主进程命名空间重新查询得到：

```text
PID      PPID  ELAPSED  CPU TIME  %CPU  STAT  含义
1315430     1     1692   00:28:11  99.9  Rs    cold gem5 持续运行
1319528     1     1639   00:00:00   0.0  Ss    restore gate 在 do_wait
```

这一步避免了把“监控视角错误”误报为新的 gem5 退出 bug。连续两次宿主采样中，gem5 CPU time 从 `00:25:58` 增长到 `00:28:11`，与 133 秒 wall time 完全同速，证明仿真线程仍在执行；`simerr` 的修改时间也推进到 20:55:35。串口暂时没有新增行只说明 secondary CPU bring-up 的模拟成本高，单凭这一点还不能判定死锁。

同时完成了错误特征计数：

```text
Unable to handle kernel NULL pointer = 0
Oops [#                              = 0
panic:/fatal:/Program aborted        = 0
pmp access fault                     = 1
```

唯一一条 PMP warning 为 `vaddr 0x880043278`，远低于 restore gate 设定的 100 条 storm 阈值，不构成 PMP fault storm。checkpoint 目录仍未生成，watcher 日志仍只有启动行，所以 cold artifact 的 route/gzip/CHI/15-of-15 审计以及 restore 都还没有开始。

### 18.66 21:07：secondary CPU bring-up 已完成，16/16 CPU 全部 online

针对上一节“慢还是死锁”的问题，本轮取得了两层连续证据。第一层来自每个 hart 启动时写 CSR/开启 SV48 的 `simerr` 序列：完整序列计数先从 12 增长到 14，表明 gem5 确实在逐个推进 secondary hart，而不是重复处理同一条 fault。第二层是最终的权威 Linux printk：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

这正式关闭了“Linux 是否卡在多核 bring-up”的疑点：配置声明的 16 个 CPU 已全部由 Linux online。21:07 宿主快照仍为：

```text
cold gem5 PID = 1315430
elapsed       = 2174 s
CPU time      = 00:36:12
CPU           = 99.9%
STAT          = Rs
restore gate  = PID 1319528, Ss/do_wait
```

因此此前从 `smp: Bringing up` 到 `smp: Brought up` 的约十余分钟宿主耗时属于 16 核 XiangShan O3 + 6×4 CHI 在该配置下的逐核启动模拟成本，不是 Linux hang。当前卡点已经下移到 **16 核 online 之后的剩余 initcalls/initramfs → `/bin/sh`**；cold checkpoint 仍未创建，后续 artifact 审计和 restore 仍待实际执行。

### 18.67 21:28：第六轮被 remote GDB 诊断动作破坏，作为无效 run 封存

16 核 online 后，第六轮还继续通过了多项 Linux 初始化：

```text
[    0.005231] devtmpfs: initialized
[    0.006291] clocksource: jiffies: ...
[    0.006358] futex hash table entries: 4096 ...
[    0.006605] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.006770] DMA: preallocated 128 KiB GFP_KERNEL pool ...
[    0.006844] DMA: preallocated 128 KiB GFP_KERNEL|GFP_DMA32 pool ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008349] vgaarb: loaded
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
```

最后一条 printk 后约十分钟，gem5 仍保持 99.9% CPU、CPU time 与 wall time 同速增长、PMP warning 仍只有 1 条。为了进一步区分“慢速 initcall”与“同 PC 自旋”，执行了一次本应只读并立即 detach 的：

```text
gdb-multiarch -q -batch ... target remote 127.0.0.1:7000 ...
```

客户端立即输出：

```text
warning: No executable has been specified ...
Recursive internal problem.
```

随后宿主 gem5 PID 1315430 消失，`simerr` 末尾新增：

```text
gem5 has encountered a segmentation fault!
```

时间关系、客户端 signature 和本报告 15.4 节已有结论完全一致：此前已经验证过 remote GDB attach 会同时触发 `gdb-multiarch` 的 `Recursive internal problem` 和 gem5 SIGSEGV，并明确写过“不再使用 remote GDB 作为取证手段”。本次在执行前没有先检索该禁用项，是诊断流程回归；它不是 guest Linux Oops、不是 direct-pmem 修复失效，也不能用来判断 Linux 当时的 initcall 是否有问题。

自动化随后按设计 fail-closed：

```text
2026-08-24 21:27:07 CST FAIL: gem5 exited before a checkpoint marker appeared
2026-08-24 21:28:07 CST FAIL: cold-checkpoint watcher reported failure
```

terminal feeder 随端口断开退出；cold checkpoint 数量为 0；restore 没有启动。宿主再次确认 cold gem5 PID 1315430 与 gate PID 1319528 均已退出，无残留进程。第六轮目录保持原样作为失败证据，不复用、不覆盖：

```text
m5out/linux-16core-128mb-direct-pmem-owner-cold-detached-20260824/
```

纠正措施是启动全新 v7 权威 run，并把以下诊断列为运行期间硬禁用项：remote GDB attach、会改变 gem5 执行路径的高强度 debug trace、重建/替换冻结 binary。运行时进度只使用宿主 `ps`、串口/`simout`、`simerr` 计数、watcher 和 artifact 文件这些非侵入式证据。

### 18.68 21:33：v7 权威 cold/restore 流程已用全新目录重启

第六轮失败目录完全保留后，四个自动化脚本被机械切换到两个此前不存在的新目录：

```text
cold:
m5out/linux-16core-128mb-direct-pmem-owner-cold-v7-detached-20260824/

restore:
m5out/linux-16core-128mb-direct-pmem-owner-restore-v7-detached-20260824/
```

启动前检查结果：四个脚本 `bash -n` 全部退出 0、执行位存在、两个 outdir 均为 `ABSENT`、`git diff --check` 退出 0。第六轮 audit 中冻结的四个 SHA256 重新执行 `sha256sum -c` 后全部为 `OK`，因此没有因为失败 run 重建或替换验证对象：

```text
gem5.opt    2dc5ae354b53d1c324e044f4e0a4b2fe74483aec5f79e7c0111261ca9d4bcad6
simulate.py 182b570caf07998fde0ca70d72c976fcff9d430377c1ec93002167d5a16026f0
base.cc     66c90c52eb021078a7c87646184e1b58708c2de0f96bb9dd8ee04fca06254e8c
base.hh     aecd45ea2beb7fd68ff37a9132054602af48ec06ff2b93978c472e1467af1f88
```

v7 启动状态：

```text
start time       = 2026-08-24 21:31:04 CST
cold gem5 PID    = 1575749
terminal port    = 3456
terminal         = ==== m5 terminal: Terminal 0 ====
watcher PID      = 1579878
restore gate PID = 1579757
feeder shell     = 1578416（另有 pipeline 子 shell 1578428）
```

启动 162 秒时，宿主 gem5 CPU time 为 161 秒、CPU 99.5%、状态 `Rs`。watcher 与 restore gate 均只有启动行、没有 FAIL。cold audit 目录新增了不可变运行约束文件：

```text
non_intrusive_monitoring_policy.txt
  remote GDB attach is forbidden: known to SIGSEGV this gem5 target
  high-intensity debug traces are forbidden during the acceptance run
  binary and checkpoint source files must retain the frozen SHA256 values
```

v7 后续仍执行与第六轮相同的严格验收链：真实 cold terminal marker与 16 CPU/online mask → guest checkpoint → direct-pmem route → gzip → CHI → exact 15/15 idle stack → binary SHA equality → restore → restore terminal真实输出 → 第二 checkpoint → 第二次 route/gzip/CHI/15-of-15。任何 gate 失败都停止后续流程。

### 18.69 21:50：v7 再次进入 Linux并越过旧 NULL@0x4 崩溃点

v7 在 `REAL SIMULATION` 与 terminal attach 后继续输出：

```text
[    0.000000] Linux version 6.10.7-g0dc336bf6c07 ... #69 SMP ...
[    0.000000] Machine model: freechips,rocketchip-unknown
[    0.000000] SBI TIME extension detected
[    0.000000] SBI IPI extension detected
[    0.000000] SBI RFENCE extension detected
[    0.000000] SBI DBCN extension detected
[    0.000000] Initmem setup node 0 [mem 0x0000000080000000-0x0000000087ffffff]
[    0.000000] SBI HSM extension detected
```

随后再次抵达并越过旧 failure window：

```text
[    0.000000] riscv: base ISA extensions acdfhimv
[    0.000000] riscv: ELF capabilities acdfimv
[    0.000000] percpu: Embedded 14 pages/cpu ...
[    0.000000] pcpu-alloc: [0] 08 ... 15
[    0.000000] Kernel command line: ... rdinit=/bin/sh ...
```

从 `simout` 全文检索不到 `Unable to handle kernel NULL pointer`、`Oops [#`、`Kernel panic` 或 `Program aborted`。因此旧的“ELF capabilities 后立刻 NULL dereference at 0x4”已经在不使用 remote GDB、不使用高强度 trace的 v7 独立 run 中再次被排除。

21:50 宿主快照：

```text
cold gem5 PID = 1575749
elapsed       = 1189 s
CPU time      = 00:19:48
CPU           = 99.9%
STAT          = Rs
watcher/gate  = Ss/do_wait
```

当前 Linux 位置是早期内存/缓存哈希表初始化，尚未到 secondary CPU bring-up；因此本轮下一里程碑仍是 `smp: Brought up 1 node, 16 CPUs`，再之后才是 initramfs shell 和第一个 checkpoint。

### 18.70 22:43：v7 完成 16 核 online并进入全新后半段 initcall 区间

v7 在 RCU、IRQ、IPI、clocksource、ASID、SRCU 初始化后进入：

```text
[    0.001403] smp: Bringing up secondary CPUs ...
```

非侵入式 CSR 序列计数在等待期间依次从 4 → 8 → 13 单调增长，PMP warning 始终只有 1 条，最终串口给出：

```text
[    0.004307] smp: Brought up 1 node, 16 CPUs
```

这说明 v7 与第六轮一样真实完成 16/16 CPU online，且没有 bring-up deadlock。之后继续通过：

```text
[    0.005231] devtmpfs: initialized
[    0.006291] clocksource: jiffies: ...
[    0.006358] futex hash table entries: 4096 ...
[    0.006605] NET: Registered PF_NETLINK/PF_ROUTE protocol family
[    0.006770] DMA: preallocated 128 KiB GFP_KERNEL pool ...
[    0.006844] DMA: preallocated 128 KiB GFP_KERNEL|GFP_DMA32 pool ...
[    0.007147] initcall check_unaligned_access_all_cpus blacklisted
[    0.008349] vgaarb: loaded
[    0.008446] clocksource: Switched to clocksource riscv_clocksource
```

`riscv_clocksource` 是第六轮被 remote GDB SIGSEGV 打断前的最后可靠 guest 位置。v7 不附加调试器，随后自然继续到新的区域：

```text
[    0.011009] tcp_listen_portaddr_hash ...
[    0.011119] TCP established hash table entries ...
[    0.011227] TCP: Hash tables configured ...
[    0.011308] UDP hash table entries ...
[    0.011443] NET: Registered PF_UNIX/PF_LOCAL protocol family
[    0.011490] PCI: CLS 0 bytes, default 64
[    0.011955] workingset: ...
[    0.012110] io scheduler mq-deadline registered
[    0.012143] io scheduler kyber registered
[    0.012178] io scheduler bfq registered
```

这直接证明第六轮在 clocksource 后并不存在已知 Linux hang；当时的停止完全来自 GDB attach。当前 v7 串口停留在 I/O scheduler 注册后的下一批 initcalls。连续两个五分钟静默窗口中宿主 CPU time 仍与 wall time 几乎完全一致：

```text
elapsed = 4317 s
CPU time = 4314 s
CPU = 99.9%
STAT = Rs
watcher/gate = Ss/do_wait
```

因此截至 22:43，这一位置应标记为“printk 稀疏、正在执行的慢段”，而不是确认的死锁。仍未出现 `/bin/sh`、guest marker、checkpoint 或 artifact 审计结果。

### 18.71 8 月 25 日 00:12：hvc0 与 late-driver gates 通过，当前在 SIT→unused-clocks

v7 的 bfq→hvc0 静默段最终自然结束，实际串口输出：

```text
[    0.020703] printk: legacy console [hvc0] enabled
[    0.020772] printk: legacy bootconsole [sbi0] disabled
```

hvc0 和 bootconsole 行各出现两次，是 console 接管时的 replay/CR 输出，与多份成功 baseline 一致。这个结果再次证明约一小时没有 printk 的 bfq→hvc0 区间不是 hang。随后 v7 继续逐项通过：

```text
[    0.021042] Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled
[    0.021485] uartlite 40600000.serial: error -ENXIO: IRQ index 0 not found
[    0.022020] sdhci: Secure Digital Host Controller Interface driver
[    0.022064] sdhci: Copyright(c) Pierre Ossman
[    0.022104] sdhci-pltfm: SDHCI platform and OF driver helper
[    0.022300] NET: Registered PF_INET6 protocol family
[    0.022618] Segment Routing with IPv6
[    0.022656] In-situ OAM (IOAM) with IPv6
[    0.022703] sit: IPv6, IPv4 and MPLS over IPv4 tunneling driver
```

UARTLite 的 `-ENXIO` 是所有成功 baseline 在相同 guest timestamp 都存在的已知设备树 probe warning；本轮其后实际继续完成 SDHCI 与 IPv6，因此不是阻断性 bug。

00:12 宿主进程快照：

```text
cold gem5 PID = 1575749
elapsed       = 9703 s / 02:41:43
CPU time      = 02:41:36
CPU           = 99.9%
STAT          = Rs
watcher/gate  = Ss/do_wait
```

elapsed 与 CPU time 只差约 7 秒。历史成功 v4 从 SIT 到 `[0.026816] clk: Disabling unused clocks` 实耗约 40 分 46 秒；v7 截至本节约进入该区间 15 分钟。当前精确 Linux 位置因此是 **SIT tunnel 已完成、unused clocks 尚未禁用**，仍在成功基线范围；checkpoint 数仍为 0，restore 未启动。

### 18.72 00:28：unused-clocks gate 通过，进入 shell 前最后长段

v7 随后实际输出：

```text
[    0.026816] clk: Disabling unused clocks
```

guest timestamp 与所有成功 cold baseline 相同，说明 late driver/initcall 控制流保持一致。00:28 宿主快照：

```text
cold gem5 PID = 1575749
elapsed       = 10669 s / 02:57:49
CPU time      = 02:57:41
CPU           = 99.9%
STAT          = Rs
watcher/gate  = Ss/do_wait
```

当前 Linux 精确位置更新为 **`clk: Disabling unused clocks` 之后、`Freeing unused kernel image (initmem)` 与 `Run /bin/sh as init process` 之前**。历史同配置从 unused-clocks 到 shell 约耗 2 小时 23 分，所以此后可能再次长时间没有 printk；这只是量级参考，不是超时阈值。terminal feeder 已连接并缓存命令，但 shell 尚未读取，checkpoint 数仍为 0，cold artifact 审计和 restore 尚未开始。

### 18.73 02:48：v7 完整进入 `/bin/sh`，真实终端命令与 cold checkpoint 成功

v7 没有卡在 unused-clocks 后。最终 Linux 串口继续输出：

```text
[    0.038540] Freeing unused kernel image (initmem) memory: 4112K
[    0.038616] Run /bin/sh as init process
```

此前从 terminal 端口 3456 排入的命令随后由 guest BusyBox shell 实际执行；判据不是宿主本地 echo，而是每条命令都有 guest 返回值：

```text
DIRECT_PMEM_OWNER_COLD_SHELL_20260824
DIRECT_PMEM_OWNER_PROC_SYS_READY_20260824
Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
DIRECT_PMEM_OWNER_CPU_COUNT=16
DIRECT_PMEM_OWNER_CPU_ONLINE=0-15
DIRECT_PMEM_OWNER_UPTIME=0.05 0.78
DIRECT_PMEM_OWNER_SHELL_PID=137
53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38  /tmp/chi_guest_checkpoint_repo
DIRECT_PMEM_OWNER_TRIGGER_CHECKPOINT_20260824
```

这组返回值同时证明：

1. Linux 完成 early boot、16 核 secondary bring-up、late initcall、initmem 释放和 `/bin/sh`；
2. `/proc` 与 `/sys` 已可读，`/proc/cpuinfo` 返回 16 个 processor，online mask 为 `0-15`；
3. terminal 不是“端口存在但 guest 不收字符”，shell PID、uptime、uname 都是恢复不了的动态 guest 值；
4. checkpoint helper 由 terminal 注入，guest 内 SHA256 与冻结的宿主 helper 完全一致；
5. checkpoint 是 guest helper 主动触发，不是 max tick、宿主 kill 或异常退出形成的残留目录。

gem5 随后正常打印：

```text
Writing checkpoint
Exiting @ tick 67414051464 because checkpoint
```

cold checkpoint 唯一路径是：

```text
m5out/linux-16core-128mb-direct-pmem-owner-cold-v7-detached-20260824/
└── checkpoints/
    └── cpt.67414051464/
        ├── m5.cpt
        └── system.physmem.store0.pmem
```

文件尺寸与 SHA256：

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `m5.cpt` | 39,675,033 | `8bacb06a13282002292554fbba295e22704092c8473d7853bf527af36fd15893` |
| gzip pmem | 6,761,262 | `79bb58ea7cf9d2fd6cc7278c77a652674d341569ea40b365eb647e781c666ae2` |
| 解压 raw pmem | 134,217,728 | `a21afa692826a2cce0fa42632e4c410bf48b98961dc3f38682a73501e4f3deda` |

因此原始用户问题中的 Linux NULL 崩溃、16 核启动、shell 和第一次 checkpoint 四项，在 v7 cold 侧都已经以真实运行证据通过。

### 18.74 direct-pmem 路由、gzip 与 CHI canonical 审计全部通过

v7 checkpoint 的前三类自动审计结果：

```text
classic_route.status = PASS
gzip.status          = PASS
chi_audit.status     = PASS
```

classic cache 的 checkpoint 日志中，正常物理内存 line 全部走新实现的 `direct-pmem` 路径；包括 page-table walker、64 个 private L2 slice 与 16 个 L1 D-cache 的 dirty-owner 写回。每一条统计均满足：

```text
direct-pmem > 0
routed-nonmem = 0
```

例如末端 16 个 L1 D-cache 均有数百条 dirty line 直接写入 PhysicalMemory；CPU14 D-cache 为 `856 direct-pmem, 0 routed-nonmem`。这证明完整 Linux run 实际经过了 `BaseCache::writebackVisitor()` 的新路线，而不是仅在单测中覆盖。

pmem gzip 流可完整解压为精确 128 MiB；CHI 独立审计输出：

```text
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

含义是：checkpoint 结束时 CHI SLC/SF 已 canonicalize，没有依赖未恢复的私有 cache/SLC 副本；pmem 是唯一且可解析的权威数据源。

### 18.75 初次 stack audit 的 0/15 是审计器 frame-pointer 判据 bug，不是 artifact 损坏

第一次 v7 审计输出了：

```text
stack_audit.status = FAIL rc=1
idle_summary passed=0 total=15
```

restore v7 gate 因而在 02:49 正确 fail-closed：

```text
FAIL: cold-checkpoint stack_audit.status is not PASS
```

没有创建 restore v7 outdir，也没有用未解释的 artifact 启动恢复。随后逐列检查发现，15 个 idle hart 并不是随机失败，而是具有完全一致的模式：

| 条件 | v7 结果 |
|---|---:|
| PC 为 `arch_cpu_idle+0x10 = 0xffffffff8046f112` | 15/15 |
| `live_s0 = sp+16` | 15/15 |
| pmem Sv48 walk 成功 | 15/15 |
| serialized DTLB physical = pmem page walk physical | 15/15 |
| pmem `saved_ra = live_ra = 0xffffffff80470480` | 15/15 |
| pmem `saved_s0 = sp+32 = live_s0+16` | 15/15 |
| 旧判据 `saved_s0 = live_s0` | 0/15 |

例如 CPU2：

```text
sp        = 0xffff8f800011be60
walk_pa   = 0x000000008158ee60
dtlb_pa   = 0x000000008158ee60
live_s0   = 0xffff8f800011be70  # sp+0x10
saved_s0  = 0xffff8f800011be80  # sp+0x20
live_ra   = 0xffffffff80470480
saved_ra  = 0xffffffff80470480
```

对本轮实际使用的冻结文件：

```text
/tmp/opensbi-build-linux-128mb-blacklist/platform/generic/firmware/fw_payload.elf
SHA256 = bf6c8f01809ea8e3a685e80d2c78ad1169113d066460343f16a718a9a6b1aa8d
```

执行精确反汇编。Linux 虚拟 `0xffffffff8046f102` 对应 payload 地址 `0x8066f102`：

```asm
arch_cpu_idle:
  0x8066f102  addi sp,sp,-16
  0x8066f104  sd   s0,0(sp)
  0x8066f106  sd   ra,8(sp)
  0x8066f108  addi s0,sp,16
  0x8066f10a  fence
  0x8066f10e  wfi
  0x8066f112  ld   ra,8(sp)
  0x8066f114  ld   s0,0(sp)
  0x8066f116  addi sp,sp,16
  0x8066f118  ret
```

精确调用者 `default_idle_call` 同样分配 16 字节 frame：

```asm
  0x80670470  addi sp,sp,-16
  0x80670472  sd   ra,8(sp)
  0x80670474  sd   s0,0(sp)
  0x80670476  addi s0,sp,16
  0x8067047c  jal  0x8066f102
  0x80670480  ...                 # arch_cpu_idle 的返回点
```

若 `S` 是 `default_idle_call` 建 frame 前的 SP，则调用 `arch_cpu_idle` 后：

```text
current sp        = S-32
arch live_s0      = S-16 = current sp+16
saved caller s0   = S    = current sp+32 = live_s0+16
saved return addr = 0xffffffff80470480
```

所以旧审计器要求 `saved_s0 == live_s0`，实际是在比较调用者 frame pointer 与当前 frame pointer；任何正确的这条调用链都会被误报。修正后的条件不是删除 `s0` 检查，而是更精确地要求：

```text
saved_s0_chain := saved_s0 == sp+32 == live_s0+16
```

初次失败的逐核输出、status、watcher log 和 v7 gate log 均另名保留；checkpoint 两个文件没有改写。完整证明保存在：

```text
m5out/linux-16core-128mb-direct-pmem-owner-cold-v7-detached-20260824/
  audit/stack_frame_semantics_correction.txt
  audit/stack_audit.pre_frame_semantics_fix.txt
  audit/stack_audit.pre_frame_semantics_fix.status
  audit/watcher.pre_frame_semantics_fix.log
  audit/restore_gate.v7.failed.log
```

### 18.76 修正后的冷 artifact 为严格 15/15，且四份历史坏类型仍被拒绝

修正审计器 SHA256：

```text
426e0d7e835495c6872703efd9b24fe1d373cf721bf0e7a1a3de81aa24197ea9
```

语法检查通过后，对当前 v7 与四类历史坏 artifact 使用同一程序回放：

| artifact | 返回码 | idle summary | 结论 |
|---|---:|---:|---|
| direct-pmem v7 | 0 | 15/15 | PASS |
| all-valid | 1 | 0/15 | FAIL |
| phased-owner | 1 | 0/15 | FAIL |
| consolidated-ordered | 1 | 0/15 | FAIL |
| SLC-first | 1 | 0/15 | FAIL |

历史坏 artifact 仍被 stale saved S0/RA 和/或 DTLB-pagewalk mismatch 捕获，所以这不是为了放行 v7 而把 gate 改成恒真。重新执行完整 cold watcher 后：

```text
classic_route.status = PASS
gzip.status          = PASS
chi_audit.status     = PASS
stack_audit.status   = PASS
idle_summary passed=15 total=15
```

15 个 idle hart 的 `live_s0_frame`、`saved_s0_chain`、`dtlb_match` 和最终 `pass` 均为 `yes/PASS`；CPU14 位于 guest helper continuation，按原规则为 `n/a`，因此总数严格是 15 而非宽松的 16 或空集。

此外 watcher 冻结 auditor、CHI checker、payload bin 与 payload ELF 的 SHA；restore gate 在启动前对 checkpoint SHA 和这四个验收输入执行 `sha256sum -c`，全部为 OK。

### 18.77 v8 恢复尝试仅暴露沙箱 socket 权限，不属于 gem5/Linux 失败

第一次恢复脚本在普通 filesystem sandbox 中启动，cold gate 与 checkpoint hash 检查均已通过，但 gem5 在 tick 0 输出：

```text
build/RISCV/base/socket.cc:131: panic: Can't create socket:Operation not permitted !
Program aborted at tick 0
```

当时还没有 terminal port、没有 `REAL SIMULATION`、没有执行 unserialize 后的 guest event，也没有 Linux 指令。因此不能把这个 `SIGABRT` 归因于 checkpoint、CHI 或 Linux；它是执行环境拒绝创建 localhost listener 的 EPERM。v8 outdir 被完整保留，没有删除或覆盖：

```text
m5out/linux-16core-128mb-direct-pmem-owner-restore-v8-detached-20260824
```

随后将 gate lock、restore outdir 与 raw-audit 目录升级为独立 v9，并仅为 gem5 localhost terminal socket 使用获批的沙箱外运行权限。

### 18.78 03:02：v9 已恢复至真实 `/ #`，当前在消费 terminal 命令

v9 先重新通过 cold gate、checkpoint SHA、auditor/CHI checker/payload SHA 检查，然后选择唯一输入：

```text
cpt.67414051464
```

关键恢复日志：

```text
Restoring from checkpoint: .../cpt.67414051464
system.terminal: Listening for connections on port 3456
Waiting 120.0 host seconds for restored terminal input at guest tick 67414051464
67414051464: system.terminal: attach terminal 0
Restored terminal wait complete at guest tick 67414051464
**** REAL SIMULATION ****
Entering event queue @ 67414051464. Starting simulation...
```

terminal 真实握手：

```text
==== m5 terminal: Terminal 0 ====
/ #
```

随后 shell 已开始逐字符回显第一条验收命令：

```text
/ # echo FINAL_DIRECT_PMEM_OWNER_RESTO...
```

这一步已经证明：

1. checkpoint 被 gem5 成功选中并实例化；
2. restore 后 event queue 从原 tick 开始运行；
3. guest checkpoint helper continuation 能返回；
4. Linux 没有在首次恢复 continuation 立即跳入低地址或 Oops；
5. HVC terminal 能连接，恢复后的 BusyBox shell 能重新输出 prompt 并接收字符。

截至本节，尚未完成的最终验收只有：等待整组命令返回 uname/16 CPUs/online mask/uptime/helper SHA，guest 执行第二次 checkpoint，随后对第二份 artifact 重做 direct-pmem、gzip、CHI 与精确 15/15 栈审计。v9 正在运行，当前没有 `Unable to handle kernel NULL pointer`、`Oops`、`Kernel panic`、`Program aborted` 或 PMP fault storm；任务不能在第二 checkpoint 审计前标为完成。

### 18.79 03:20：恢复后 terminal 全部返回，并由 guest 正常生成第二 checkpoint

18.78 的逐字符输入最终全部执行完成。去掉终端 CR 后的权威 guest 输出为：

```text
FINAL_DIRECT_PMEM_OWNER_RESTORE_TERMINAL_ACCEPTED_20260824
FINAL_DIRECT_PMEM_OWNER_RESTORE_UNAME=Linux (lvna) 6.10.7-g0dc336bf6c07 #69 SMP Wed Mar 12 15:06:07 CST 2025 riscv64 GNU/Linux
FINAL_DIRECT_PMEM_OWNER_RESTORE_CPU_COUNT=16
FINAL_DIRECT_PMEM_OWNER_RESTORE_CPU_ONLINE=0-15
FINAL_DIRECT_PMEM_OWNER_RESTORE_UPTIME=0.08 1.22
FINAL_DIRECT_PMEM_OWNER_RESTORE_SHELL_PID=1
FINAL_DIRECT_PMEM_OWNER_RESTORE_HELPER_SHA256=53f7be9474e6bbcf61bde0169fa5720671ec9d94c0e593d3e77cc88a58237c38
FINAL_DIRECT_PMEM_OWNER_RESTORE_COMMANDS_DONE_20260824
FINAL_DIRECT_PMEM_OWNER_RESTORE_RECHECKPOINT_REQUEST_20260824
```

这些值证明输入不是 nc 本地 echo：uname、`/proc/cpuinfo`、sysfs online mask、`/proc/uptime`、shell PID 和 guest 文件 SHA 均必须由恢复后的 Linux 执行才能产生。`SHELL_PID=1` 与 initramfs 以 `/bin/sh` 作为 init 的运行方式一致。

在执行 `busybox sync` 并打印 re-checkpoint marker 后，guest helper 实际运行：

```text
/ # /tmp/chi_guest_checkpoint_repo
Writing checkpoint
Exiting @ tick 97174117152 because checkpoint
```

gem5 返回码为 0；整个 v9 `simout/simerr` 中没有：

```text
Unable to handle kernel NULL pointer
Oops [#
Kernel panic
Program aborted
PMP access fault storm
```

这直接完成了用户要求的“再次跑 checkpoint 时 Linux 不崩溃，而且端口能输入命令”。第二份 checkpoint 唯一路径为：

```text
m5out/linux-16core-128mb-direct-pmem-owner-restore-v9-detached-20260824/
└── checkpoints/
    └── cpt.97174117152/
        ├── m5.cpt
        └── system.physmem.store0.pmem
```

### 18.80 第二 checkpoint 四类审计全部 PASS，最终验收闭环

v9 gate 在 gem5 正常退出后没有只看 exit marker，而是对第二份 artifact 重复执行与 cold 相同的离线检查。最终 status：

```text
gem5.exit_status       = 0
classic_route.status   = PASS
gzip.status            = PASS
chi_audit.status       = PASS
stack_audit.status     = PASS
final_acceptance.status= PASS
```

第二份文件尺寸与 SHA256：

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `m5.cpt` | 42,836,310 | `8133f348316cd1c508bacf981b3592c8b8605c10e17151bf3703b57159b311f9` |
| gzip pmem | 6,805,510 | `c483845eb3fe74b9897e134a586de4045ce970b2f0c061ab2254e08419962ba3` |
| 解压 raw pmem | 134,217,728 | `d27a9bb3b8a1c031e2629177d85f77eb3810537a40d20127888426bed783e726` |

`sha256sum -c` 对第二 checkpoint、原 cold checkpoint、auditor、CHI checker、payload bin 与 payload ELF 均返回 `OK`。

第二次 classic route audit 共记录 269 条 cache/phase 写回统计：

| phase | 统计行数 | direct-pmem line 数 | routed-nonmem |
|---:|---:|---:|---:|
| 0 shared/former-owner | 128 | 101,434 | 0 |
| 1 clean-Writable owner | 64 | 5,151 | 0 |
| 2 Dirty/Owned owner | 77 | 32,664 | 0 |
| 合计 | 269 | 139,249 | 0 |

所以第二次 checkpoint 同样实际经过 direct-pmem 修复，不是因为 restore 后 cache 恰好为空而绕过代码。

CHI canonical audit：

```text
serialized SLC lines: 0
serialized SF lines: 0
SLC/pmem mismatches by state: {}
```

第二次栈审计中 CPU8 正在 guest checkpoint helper `PC=0x100b8`，按规则不计入 idle；其余 CPU0–7、9–15 共 15 个 hart 全部满足：

```text
PC == 0xffffffff8046f112
pmem Sv48 walk succeeds
serialized DTLB PA == pmem page-walk PA
live_s0 == sp+16
saved_s0 == sp+32 == live_s0+16
saved_ra == live_ra == 0xffffffff80470480
```

最终汇总：

```text
idle_summary passed=15 total=15
```

这比“restore 能跑一小段”更强：恢复后的 Linux 已经历 timer/idle continuation、shell syscalls、proc/sysfs 访问、文件哈希、sync 和再次 checkpoint；二次 artifact 的页表、idle 栈、CHI 和 pmem 又独立自洽。

### 18.81 最终卡点清单：技术卡点为零，保留事项均不阻断验收

截至 2026-08-25 03:20 CST，原目标没有剩余技术卡点：

| 问题 | 最终状态 |
|---|---|
| Linux 在 `riscv: ELF capabilities` 后 NULL@0x4 | 已解决，多次越过且最终完整入 shell |
| 16 核 secondary bring-up | 已通过，cold 与 restore 都报告 16 / `0-15` |
| shell/terminal 无响应 | 已通过，恢复后多类真实命令返回 |
| cold checkpoint 数据语义 | 已通过，direct/gzip/CHI/15-of-15 |
| checkpoint restore 首次 timer continuation | 已通过，无低地址跳转/Oops/fault storm |
| restore 后再次 checkpoint | 已通过，tick `97174117152` 正常退出 |
| 第二 checkpoint 数据语义 | 已通过，direct/gzip/CHI/15-of-15 |

仍需保留在工程使用说明中的非阻断事项：

1. 不要对权威长跑附加 remote GDB；历史上它会让该 gem5 target 与 gdb 自身进入 SIGSEGV/recursive internal problem，属于调试器副作用；
2. 不要在验收长跑启用高强度 O3 trace 组合；历史上会触发宿主 trace 路径 SIGSEGV并严重扰动运行；
3. restore 使用 terminal listener 时必须允许 localhost socket；普通 filesystem sandbox 会在 tick 0 以 EPERM 失败，这不代表 checkpoint 坏；
4. MicroTAGE block-index clamp、UARTLite `-ENXIO` 和部分 legacy-stat/issue-queue warning 仍可见，但最终 guest 控制流与两次 checkpoint 审计证明它们不阻断本次目标；
5. 工作树中存在本轮和更早调试积累的多文件改动；是否拆分 commit/PR 属于后续代码整理，不影响本报告记录的已运行 binary 与 artifact 结论。

最终可作为验收入口的文件：

```text
linux_checkpoint_debug_report_20260819.md
m5out/linux-16core-128mb-direct-pmem-owner-cold-v7-detached-20260824/audit/
m5out/linux-16core-128mb-direct-pmem-owner-restore-v9-detached-20260824/audit/final_acceptance.status
m5out/linux-16core-128mb-direct-pmem-owner-restore-v9-detached-20260824/audit/stack_audit.txt
m5out/linux-16core-128mb-direct-pmem-owner-restore-v9-detached-20260824/checkpoints/cpt.97174117152/
```

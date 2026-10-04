# 原生 CHI L2：IRIW litmus

更新：2026-10-03。**两个变体各 128 轮正式运行通过**：修复 L1 排队请求与 L2 probe 的等待环后，使用新 gem5 二进制分别完成 `iriw`、`iriw-fenced`，逐轮 CPU 结果核验与原始 CHI gate 均通过。fenced 变体未观察到禁止的 `1010`；这是本批运行结果，不是全部 RVWMO 的证明。失败前缀与正式结果分别保留，见下方。

测试程序是 [`native-l2-litmus.c`](../native/gem5/native-l2-litmus.c)。四个 KMH CPU 在 SE 模式执行普通 RV64 `ld`、`sd`、`fence`，共享一段 64KiB、可缓存的物理内存；不使用协议 opcode 注入、测试 MMIO 或 host 代发请求。实际路径由 [`native_l2_run.py`](../native/gem5/native_l2_run.py) 配置：

```text
KMH CPU → classic L1 → ChiNativeL2 → CHI NoC → ChiExtendedHome
                                              → ChiNativeMemory → SimpleMemory
```

拓扑为 4 RN / 4 HN / 2 SN、3×2 mesh；每核原生 L2 默认 16 sets × 4 ways × 64B、16 个 MSHR。新 L2 的实现边界见 [`native-l2-design.md`](native-l2-design.md)，既有普通读写回归见 [`native-l2-validation.md`](native-l2-validation.md)。

## 测试与判定

每轮开始时 `x = y = 0`。下表中的 P0–P3 是逻辑角色，会轮换到不同物理 hart。`iriw-fenced` 仅在两个 reader 的两条 load 之间各插入 `fence r,r`；`iriw` 没有这两道 fence。

| P0 | P1 | P2 | P3 |
|---|---|---|---|
| `sd 1, x` | `sd 1, y` | `ld a, x` | `ld c, y` |
| | | `[fence r,r]` | `[fence r,r]` |
| | | `ld b, y` | `ld d, x` |

结果 tuple 固定为 `(a,b,c,d) = (P2.x,P2.y,P3.y,P3.x)`。`1010` 表示两个 reader 对两个写入的观察次序相反：P2 读到 `x=1,y=0`，P3 读到 `y=1,x=0`。程序记录全部 16 种二值结果，不只记录目标 tuple。

| 变体 | `1010` 的 RVWMO 判定 | 本测试处理 |
|---|---|---|
| `iriw` | 允许 | 计入 `weakOutcomeCount`，出现或不出现均不据此判失败 |
| `iriw-fenced` | 禁止 | 任意一轮出现即失败 |

依据是 RISC-V 20260120 版本的 [RVWMO 规范 §17.1.1.3（Preserved Program Order）及 §17.1.1.4.1（Load Value Axiom）](https://docs.riscv.org/reference/isa/v20260120/unpriv/rvwmo.html)。独立、不同地址的两次 load 在无 fence 时不必保持全局顺序；Rule 4 则要求 fence 指定的前驱和后继访问保持顺序。以下 IRIW 判定是对这些规则的推导，并非引用手册中的一张 IRIW 结果表。

令 `Wx`、`Wy` 为本轮写入 1，`Rx`、`Ry` 为 P2 的读取，`Ry'`、`Rx'` 为 P3 的读取。加入两道 reader fence 后，`1010` 要求全局内存顺序同时满足：

```text
Wx < Rx=1 < Ry=0 < Wy < Ry'=1 < Rx'=0 < Wx
```

该环不可能嵌入一个全序，故禁止。没有 reader fence 时，可以有 `Rx'=0 < Wx < Rx=1 < Ry=0 < Wy < Ry'=1`，因此允许。RVWMO 的多副本原子性不替代对 reader 内部读取顺序的约束；参见[官方解释 §37.1.3.6（Fences）与 §37.1.6（Implementation Guidelines）](https://docs.riscv.org/reference/isa/v20260120/unpriv/mm-eplan.html)。实现可以比 RVWMO 更强，允许的结果不保证会被观察到。

## 轮换与隔离

默认每个变体运行 **128 轮，seed=1**，逻辑角色为 `(hart + iteration + seed) & 3`。`x`、`y` 使用不同的 64B 对齐 cache line，位于共享区不同页面。RN SAM 使用 `PA[6]`、`PA[7]` 选择 HN：

```text
x_home = (iteration / 4 + seed) & 3
y_home = (x_home + 1 + (iteration / 16) % 3) & 3
x = shared + 0x2000 + 64 * x_home
y = shared + 0x3000 + 64 * y_home
```

当前公式让 `x`、`y` 总在**不同 HN**；它覆盖跨 HN 的地址组合，不包含同 HN 对照。每个角色在实验前使用由轮数和 seed 决定的短 NOP 延迟，两条 reader load 之间没有延迟、结果写回、条件分支或 syscall。

每 hart 的 barrier 标志和结果槽各占独立 cache line。barrier 使用递增 epoch，并在发布与观察边界执行 `fence rw,rw`。CPU0 在每轮 start barrier 前重置 `x/y`；该 barrier 不等待本轮 writer 已写入 1。

各 hart **通过 start barrier 后**才把自己的两个结果槽写为 `~0ULL` sentinel；只有 reader 随后用实际 load 返回值覆盖。读取对之后才写结果并进入 end barrier，CPU0 通过 end barrier 后采集四个值、保存逐轮 sample 并统计。下一轮 sentinel 必须等 CPU0 完成上一轮统计并发布新 start arrival，因此不会覆盖尚未读取的旧结果。不得把每 hart 的 sentinel 初始化移回 start barrier 之前。未写入、未知或非 0/1 的结果会失败，不会继承上一轮有效值而假通过。

最终由 CPU0 发布汇总状态，经最后一道 barrier 后四个 hart 分别报告完成轮数并退出。CPU 成功退出后，仿真额外运行有上限的 1,000,000 tick，接收已经发出的 CompAck 等消息；不强制刷出缓存中的所有 dirty line。

## 核验与证据

[`build-native-litmus.py`](../scripts/build-native-litmus.py) 保存源文件/ELF 的 SHA-256、编译参数、每 hart 反汇编及 `disassembly-check.json`。反汇编必须在标记区间恰好包含 `ld; ld` 或 `ld; fence r,r; ld`。ELF 使用直接 `_start`、无 CRT；构建禁止 small-data / linker relaxation，并拒绝依赖未初始化 `gp` 的指令。正式实验必须使用通过这些检查的新二进制。

[`check-native-litmus.py`](../scripts/check-native-litmus.py) 独立读取真实 CPU 日志和配置，检查：

- 四个 `ChiNativeL2` 实例、无 `Cache2ChiBridge`，正确的变体、轮数和 seed；四个 hart 均成功退出且各完成 128 轮，达到仿真时间上限不算通过。
- 恰好 128 个连续、不重复的 sample，每个包含四个实际 CPU 返回值且严格属于 0/1；reader 与对齐地址元数据有效。
- 从逐轮 sample 重算全部 16 桶 histogram，与 CPU 汇总逐项一致，总和为 128；fenced 变体的 `1010` 必须为零。

随后 [`check-native-wire.py`](../scripts/check-native-wire.py) 检查原始字段、数据拍、发送侧握手及末尾生命周期闭合，并对照实际 TX/RX 的字段和字节一致性；运行中必须出现 ReadNotSharedDirty、ReadUnique、SnpRespData 和 CompAck。正常完成的采集不使用 `--allow-open`。CPU 内存模型结果依据 CPU 返回值，不能由 NoC 到达顺序代替；原始 CHI gate 是另一个独立检查维度，不构成逐端点 RX 顺序的事务检查。

## 正式实测结果

以下两组均使用修复后的 gem5、128 轮、seed=1；每组四个 hart 均完成 128 轮并报告 PASS，覆盖 12 个不同的跨 HN 地址组合。独立核验的 sample 数和 histogram 总数均为 128，CPU 结果错误数为零。

| 项目 | `iriw` | `iriw-fenced` |
|---|---|---|
| CPU 退出 tick | `368530000` | `375807000` |
| 尾部结束 tick | `369530000` | `376807000` |
| 观察到的 `1010` | 0，允许但本批未出现 | 0，禁止结果未出现 |
| 实际 TX / RX flit | 57,690 / 57,690 | 57,420 / 57,420 |
| 独立原始 CHI 检查 | 2,218,928 次，零错误 | 2,208,536 次，零错误 |
| 开放请求 / snoop / 写缓冲 / CompAck / retry / 未匹配 TX/RX | 全零 | 全零 |
| CPU 结果报告 | [`litmus-result.json`](../build/native-litmus-iriw-fixed/litmus-result.json) | [`litmus-result.json`](../build/native-litmus-iriw-fenced-fixed/litmus-result.json) |
| 原始 CHI gate 报告 | [`native-wire-audit.json`](../build/native-litmus-iriw-fixed/native-wire-audit.json) | [`native-wire-audit.json`](../build/native-litmus-iriw-fenced-fixed/native-wire-audit.json) |
| 原始 CPU 日志与 sample | [`run.log`](../build/native-litmus-iriw-fixed/run.log) | [`run.log`](../build/native-litmus-iriw-fenced-fixed/run.log) |
| 网页可导入回放 | [`session.chi.jsonl`](../build/native-litmus-iriw-fixed/session.chi.jsonl) | [`session.chi.jsonl`](../build/native-litmus-iriw-fenced-fixed/session.chi.jsonl) |
| 无修改转换核验 | [`native-refiner-audit.json`](../build/native-litmus-iriw-fixed/native-refiner-audit.json) | [`native-refiner-audit.json`](../build/native-litmus-iriw-fenced-fixed/native-refiner-audit.json) |

两份原始 gate 均采用 `closed-capture`，`completeCaptureValidated=true`。检查次数是本有限 CHI profile 的规则执行次数，不是覆盖率，也不表示 CHIron 在线 RX/TX transaction joint 已通过；该 joint 当前仍为 unsupported。

回放转换分别逐条保留 306,997 / 301,761 个原始 ObservedEvent，将 57,690 / 57,420 条实际 TX 映射为 ProtocolEvent。Manifest 原有字段、协议 ID/opcode/Size/DataID、payload/mask、TX 顺序和 tick 原值比较均通过。CHIron E.b 字段映射分别 57,690 / 57,420 条通过，零 fail、零 insufficient；每份都保留一条 transaction-joint unsupported，未补造消息或把字段映射通过解释为事务通过。

实际回放 engine 检查也通过：每份会话的原始、协议双视图各完成 7 个倒序随机 seek，节点状态与逐步播放一致，末尾 inflight / waits 全零。报告见 [`iriw replay-check.json`](../build/native-litmus-iriw-fixed/replay-check.json)、[`iriw-fenced replay-check.json`](../build/native-litmus-iriw-fenced-fixed/replay-check.json)。

全部 histogram 如下，tuple 顺序始终为 `(P2.x,P2.y,P3.y,P3.x)`：

| tuple | `iriw` 次数 | `iriw-fenced` 次数 |
|---|---:|---:|
| `0000` | 8 | 1 |
| `0001` | 18 | 29 |
| `0010` | 0 | 0 |
| `0011` | 6 | 3 |
| `0100` | 12 | 11 |
| `0101` | 0 | 0 |
| `0110` | 28 | 15 |
| `0111` | 0 | 14 |
| `1000` | 3 | 0 |
| `1001` | 21 | 12 |
| `1010` | 0 | 0 |
| `1011` | 11 | 10 |
| `1100` | 15 | 22 |
| `1101` | 1 | 2 |
| `1110` | 3 | 2 |
| `1111` | 2 | 7 |
| 总计 | 128 | 128 |

### 本轮发现并修复的停滞

先前 fenced 运行在 tick `280060000` 触发 CPU commit watchdog，停在 barrier 地址 `0x11100` 的 load；原始失败日志保存在不带 `-fixed` 的 [`build/native-litmus-iriw-fenced/run.log`](../build/native-litmus-iriw-fenced/run.log)。该运行未完成，不计为通过，也不能用它判断是否出现 fenced 禁止结果。

原因是 L1 已把一个被 L2 接受的请求标为 in-service，但请求仍在 L2 `requests` 队列，尚未分配原生 CHI MSHR。此时 inclusive victim probe 或外部 snoop 若只检查 active MSHR，L1 会把探测延迟到该请求 fill 后；L2 又因地址正在 probing 而阻止排队请求发出，形成等待环。

[`ChiNativeL2::preparePendingUpperDemand`](../native/gem5/ChiNativeL2.cc) 现在在公共 `probe()` 路径检查同地址、`needsResponse` 的排队请求，并设置 `SnoopPrecedesMshr`，使探测先处理旧缓存副本。失效探测同时把 queued `UpgradeReq` 提升为 `ReadExReq` 并分配数据缓冲，避免 L1 的旧数据失效后仍收到无数据的升级响应。active 请求原有的数据响应处理保留；已完成、正在等待送回 L1 的响应不纳入这个排队判断。上面的两个 `-fixed` 目录是修复后重新执行的正式证据。

修复后的既有数据工作负载也重跑了 baseline、pressure、traceoff，四核自检全部通过。baseline CPU 退出 tick 为 `17630000`，原始 gate 707,677 次检查；pressure 为 `26597000`，gate 922,767 次检查，两组均零错误、末尾生命周期闭合。baseline 与 traceoff 的全部 CPU 结果字段及 8,765 项非 host 模拟统计相同，明确排除 5 项 host 性能统计；没有导出并比较完整缓存终态。汇总及本次 `ChiNativeL2.cc/.hh` 源码 SHA 见 [`native-litmus-regression.json`](../build/qa/native-litmus-regression.json)。

## WSL 复现

在已构建最新原生 L2 gem5 的 Ubuntu WSL 中执行；脚本只编译四个小型 RV64 程序，不重新构建 gem5。默认 gem5 目录为 `$HOME/chi-flowgraph-work/gem5`，可用 `CHI_GEM5_DIR` 指定。需要 `riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-objdump` 和 Python 3。

```bash
cd /mnt/d/proj/chi_flowgraph
CHI_NATIVE_LITMUS_OUT=/mnt/d/proj/chi_flowgraph/build/native-litmus-iriw-repro \
  bash scripts/run-native-litmus.sh iriw
CHI_NATIVE_LITMUS_OUT=/mnt/d/proj/chi_flowgraph/build/native-litmus-iriw-fenced-repro \
  bash scripts/run-native-litmus.sh iriw-fenced
```

脚本默认输出分别是 `build/native-litmus-iriw`、`build/native-litmus-iriw-fenced`；上面的命令显式使用新的 `-repro` 目录，保留历史日志和正式结果。脚本顺序运行 gem5、CPU 结果核验和原始 wire gate；任一步失败即返回非零。可通过 `CHI_LITMUS_ITERATIONS`、`CHI_LITMUS_SEED` 修改采样，并用绝对路径 `CHI_NATIVE_LITMUS_OUT` 保留不同运行：

```bash
CHI_LITMUS_ITERATIONS=128 CHI_LITMUS_SEED=2 \
CHI_NATIVE_LITMUS_OUT=/mnt/d/proj/chi_flowgraph/build/native-litmus-fenced-seed2 \
  bash scripts/run-native-litmus.sh iriw-fenced
```

这是一组真实 CPU 执行的**有限采样**。两个变体通过只能说明本批运行没有发现对应违例，不证明所有并发交错、全部 RVWMO、完整 Issue H 或与 RTL 逐周期等价，也不要求无 fence 变体必须观察到 `1010`。

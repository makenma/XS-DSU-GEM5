# 四核 Native L2 Linux 验证工作负载

本目录的 pthread 工作负载与官方 herdtools 生成的 litmus 程序分开计数。交叉编译、验证器突变测试和目标 Linux 的执行结果是三种证据；前两者不能代替 gem5 的真实 guest 运行。

## 官方 diy7 → herd7 → litmus7

固定 [herdtools7 7.56](https://github.com/herd/herdtools7/tree/7.56)，commit `37caee17e651ac2a46257973494e9037f219aa4f`。固定源码 `git archive` 的 SHA-256 是 `4185eadbbb9da441548e76768785b9326dccd4524f645c6ea4a838ecc995b54c`。构建脚本检查该值，保存各工具的版本、二进制校验和与实际生成命令。此版本附带的 `riscv.cat` 是固定的历史模型；与它一致不等于证明全部最新 RISC-V 扩展。

WSL 构建依赖：OCaml、Dune、Menhir、ocamlfind、Zarith、Git、Python 3、Make、`riscv64-linux-gnu-gcc` 与对应 binutils。构建仅使用 `-j2`，gem5 与 kernel 的构建由上层流程管理。

```sh
bash scripts/build-linux-litmus.sh --tools-only
TOOLS=/root/chi-flowgraph-work/linux-validation/herdtools7-7.56/bin
python3 scripts/generate-linux-litmus.py --tools "$TOOLS" --output build/linux-litmus-smoke --profile smoke --samples 8
python3 scripts/generate-linux-litmus.py --tools "$TOOLS" --output build/linux-litmus-full --profile full --cycle-size 8 --samples 128
python3 scripts/generate-linux-litmus.py --tools "$TOOLS" --output build/linux-litmus-large --profile large --cycle-size 8 --samples 128
```

| Profile | 用途 | 生成边界 |
|---|---|---|
| `smoke` | Linux bring-up | 6 个 SB/LB/MP/IRIW 测试，8 samples / instance；不能当作大规模完成 |
| `full` | 中间有界矩阵 | size 8，12 家族，243 个命名测试、228 个唯一 semantic Hash |
| `large` | 大规模矩阵 | 1936 个命名测试、1908 个唯一 semantic Hash，17 家族；中间矩阵加三类依赖、方向 fence、32/64-bit AMO 与 LR/SC |

diy7 按固定候选边和周期上界确定性枚举，不使用随机 seed。Manifest 保存完整参数、每项原始源文件、每项源码 SHA、herd7 Hash、允许的可观察状态、条件是否允许以及实际生成的全部入口。`large` 的实际最终数量以该次 `manifest.json` 为准，不能用目标数量替代。未覆盖的任意更长周期、混合宽度、其它架构扩展及所有 possible interleavings 均不在本矩阵范围。

当前 large 的模型 oracle 为 Never 809 / Sometimes 1124 / Always 3，允许可观察状态合计 29302 个；`-a 4 -s 128 -r 1` 的计划总 outcome 数为 259840。源文件特性计数（可重叠）：fence 1411、地址依赖 694、数据依赖 509、控制依赖 694、AMO 52、LR/SC 12。以上是已生成和已建模的清单，尚不表示任何 guest 测试运行通过。

原子家族使用官方 `-variant AsAmo -varatom AR` 生成 `amoor` / `amoswap`，以及 `-varatom XAqRl -ua 0` 生成 `lr.aq` / `sc.rl`，包含 SC 失败结果。这里不声称覆盖每一种 AMO opcode；pthread suite 另有真实 `amoadd.d.aqrl` 竞争。

7.56 的 diy7 输出 `Com=... Co ...` 元数据，而同版本 litmus7 的可选 topology parser 仍接受旧名 `Ws`。生成脚本保留原始文件，另写仅删除 `Com=` 提示的编译副本，逐项重新检查 herd7 Hash 相同。测试指令、初值和判定条件保持不变。CPU affinity 独立配置为强制使用 0、1、2、3。litmus7 曾在返回码 0 时仅生成部分测试，因此脚本还检查诊断日志、`src` 全入口与完整清单一一相符，任何漏项都会失败。

另一个 7.56 的兼容问题是 AMO 的 acquire/release 汇编后缀输出为 `.aq.rl`；当前 GNU 汇编器要求 `.aqrl`。脚本只在生成 C 的 `amoor` / `amoswap` 32/64-bit 助记符中做此等价拼写规范化，aq/rl 两位保持开启。`original-generated-c/` 保留原始 C，`gnu-assembly-adaptation.json` 记录每文件替换次数及前后 SHA；原始 `.litmus` 和模型不变。编译错误不会触发删测或跳过原子家族。

将对应 profile 的 `generated/run.exe` 与 `run-guest.sh` 打包到 initramfs，在 guest 中运行：

```sh
sh /tests/run-litmus.sh /tests/litmus-run.exe
```

包装脚本先验证 ELF 的 SHA、读取 `/proc/stat` 的在线 CPU 数，打印 `LINUX_LITMUS_BEGIN`，执行带超时的官方程序，再打印含实际进程退出码的 `LINUX_LITMUS_END`。测试的线程数为 1–4；并行 instance 数为 `max(1, floor(4 / threads))`。每项预期 outcome 数为 `instances × samples × runs`，不是笼统地认为所有测试都有四个线程。单线程 coherence 测试保留在清单中。

在 host 验证串口日志：

```sh
python3 scripts/check-linux-litmus.py --input SERIAL.log --manifest build/linux-litmus-smoke/manifest.json --output build/qa/linux-litmus-smoke.json
```

验证器检查精确完整清单、每项 Hash、histogram / Witnesses / Observation 三者计数、实际 outcome 总数及每一行状态是否属于 herd7 允许集合。只运行部分测试、超时、非零退出、四核未上线、重复或丢失结果、截断日志都会失败。允许的弱序结果未出现不算失败；禁止结果出现则失败。输出 `Test ... Allowed` 是 litmus 测试语法中的条件类型，不能单独用作内存模型判断。

## 独立 pthread suite

```sh
python3 scripts/build-linux-tests.py
# Guest：
/tests/linux-suite --iterations 128 --timeout 120
# Host：
python3 scripts/check-linux-suite.py --input SERIAL.log --manifest build/linux-tests/manifest.json --iterations 128 --output build/qa/linux-suite.json
```

静态 RV64 ELF 位于 `build/linux-tests/linux-suite`。构建要求 `-Wall -Wextra -Werror`，并反汇编检查 AMO aqrl、LR acquire、SC release、Linux ecall。源文件 SHA 作为 buildId 编进 ELF，运行时不能用参数覆盖。

| 测试 | 实际操作与断言 |
|---|---|
| `spinlock`, `mutex` | 四个绑定 CPU 的线程保护计数器，检测临界区并发进入与最终计数 |
| `futex` | 四线程 turn ring，实际 `FUTEX_WAIT_PRIVATE` 睡眠与 wake；零实际等待不会 PASS |
| `amo`, `lrsc`, `atomic_counter` | 四线程争用，返回 ticket 必须恰为完整不重复区间；LR/SC 另记重试 |
| `release_acquire` | 两组 producer/consumer，经 C11 release/acquire 发布，验证全部普通 payload 字节 |
| `false_sharing` | 同一 64B 区域中四个原子对象，核对每个线程的更新 |
| `memcpy_checksum` | 复制邻线程生成的实际源数据，逐字节验证并校验 checksum |
| `alias_cache_pressure` | 同一个 memfd 的两个 MAP_SHARED 别名，跨线程读取并叠加真实缓存压力 |
| `page_migration` | 真实 `move_pages` 前后查询物理 NUMA 节点，要求迁往不同 node 且 payload 不变 |

每项打印独立 JSON：实际线程数、绑定结果、实际 CPU ID、每线程操作/错误、总操作与指标；watchdog 超时打印 ABORT 并退出 124。`--test NAME` 只用于独立诊断，其通过不能表示完整 suite 通过。默认 full suite 包含全部 11 项。

页面迁移需要 kernel `CONFIG_NUMA` / `CONFIG_MIGRATION`、真实的两个以上有内存节点、`move_pages` 权限以及可迁移页。单 NUMA、系统调用缺失或权限不足明确输出 `UNSUPPORTED`，整体为 `INCOMPLETE` / exit 2。不会用 `memcpy` 替代迁移，也不会因为“kernel 支持 NUMA”就把未发生迁移判为通过。其它必要 kernel 功能包括 SMP（至少 4 CPU）、FUTEX、PROC_FS、SYSFS、TMPFS / memfd。

## 已有 host 侧证据

- pthread suite 已用 RV64 Linux GCC 静态交叉编译并通过反汇编检查；没有由构建本身产生的 guest PASS。
- `tests/native/test_linux_suite_checker.py`：28 项合成突变测试。
- `tests/native/test_linux_litmus_checker.py`：34 项合成突变测试。
- 运行日志、完整 profile 清单和 Linux FS 运行证据由上层采集保存；本目录不把 host fixture 当作实际 CPU 执行。

官方工作流依据：[diy7 generator 文档](https://diy.inria.fr/doc/gen.html)、[litmus7 运行与编译文档](https://diy.inria.fr/doc/litmus.html)、[Linux page migration 文档](https://www.kernel.org/doc/html/latest/mm/page_migration.html)。

## 长时间运行的 CHI 流量统计

```sh
python3 scripts/check-chi-transfer-census.py --census census.json --config flowgraph-config.json --raw raw.jsonl --output census-audit.json
```

`--raw` 可省略。检查 schema、canonical 十进制字符串计数/tick、uint64 范围、总和、重复 key、首末 tick、端点 NodeID。仅 RN-F 端点成功 TX 的 REQ 计入 RTL 12 类请求覆盖；HN 下游同 opcode 不会填补 RN 缺口。可选 raw 比对所有通道、所有 TX/RX key 的 count / firstTick / lastTick。

输出状态为 `VALID` / `INVALID`，没有 `pass` 字段；缺少的 opcode 留在 `missingRtlRequestOpcodes`。包括重试在内的流量次数既不是独立事务数，也不能证明协议握手、payload、Linux 工作负载或最终缓存状态通过。periodic snapshot 允许仍在途的 TX/RX 数量差；与 raw 对比时必须提供同一完整统计区间。

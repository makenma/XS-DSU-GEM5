# ChiNativeL2：昆明湖 v2 请求选择对齐

验证日期：2026-10-03。此次将固定昆明湖 v2 / CoupledL2 的 **12 种实际 REQ 发送路径**接入在线 `ChiNativeL2`，用四个真实 KMH CPU 执行普通访存、fence 和 CBO 指令验证。请求由 L1 输入、L2 数据/权限、维护和替换状态选择；测试程序不注入 CHI opcode。RTL 固定版本、源码位置和抽象选择见 [设计说明](native-l2-design.md)。

参考版本为 XiangShan `e7bab53e66dfb3c4a1d11cf9519b0396f8576cae` 及其 CoupledL2 gitlink `55d8e69c690690dbc786e9ca2f3839a95555df95`。这是一组功能行为对齐与实测证据，不是 RTL lockstep 或周期等价证明。

## 请求选择及实测次数

下表统计 RN 的实际发送，包括压力运行中的重试尝试。基准输出为 `build/native-rtl-run2`，压力输出为 `build/native-rtl-pressure`。

| 请求 | 触发条件 | 基准 TX | 压力 TX |
|---|---|---:|---:|
| ReadNotSharedDirty | 取指、load 或读预取需要向下获取数据 | 1748 | 2005 |
| ReadUnique | 写分配/权限升级需要数据及独占；没有整行覆盖承诺 | 51 | 61 |
| MakeUnique | 上游提供完整行覆盖写承诺，只请求权限 | 322 | 528 |
| ReadNoSnp | Normal non-cacheable RAM 的实际读 | 20 | 20 |
| WriteNoSnpPtl | Normal non-cacheable RAM 的实际写，保留 Size 和字节掩码 | 8 | 8 |
| CleanShared | CPU cbo.clean，经必要的脏数据提交后发出 | 6 | 9 |
| CleanInvalid | CPU cbo.flush，经必要的写回/驱逐后发出 | 1 | 1 |
| MakeInvalid | CPU cbo.inval，经必要的本地失效/驱逐后发出 | 1 | 1 |
| Evict | 维护过程的干净副本驱逐；也供 B/C 兼容配置使用 | 1 | 1 |
| WriteBackFull | 脏 victim 或 flush 前的脏数据释放 | 323 | 121 |
| WriteCleanFull | clean 时提交脏数据并保留干净副本 | 5 | 5 |
| WriteEvictOrEvict | 默认 E.b 配置的干净 victim 替换 | 1489 | 1995 |

“已有共享副本的写升级”不能单独推出 MakeUnique。固定 CPU DCache 用 `isStore && store_mask.andR` 选择 full_overwrite，只有这条路径发 AcquirePerm。当前 classic L1 的非维护 `InvalidateReq` 提供对应的完整行写承诺；带 `Request::INVALIDATE` 的维护请求则走 MakeInvalid。未拿到 L1 的真实写入数据前，MakeUnique 路径不制造已知 payload。

命中且权限充足时不发下游 CHI。相同 fetch/load 地址是否发包及发哪个 opcode，仍取决于当时的缓存状态。classic L1 和 RTL 的请求合并时机不同，同一程序的逐笔请求数量不保证相同。

## 实现

- [`ChiNativeL2.cc`](../native/gem5/ChiNativeL2.cc) 执行选择、独立维护子事务、MakeUnique 无数据完成、WEOE 双分支、非缓存读写和 ReadReceipt 等待。分配事件记录 `inputPacket`、`cacheMaintenance`、`fullOverwritePromise`，可以回查选择依据。
- [`ChiExtendedHome.cc`](../native/gem5/ChiExtendedHome.cc) 补齐维护与 WEOE 的对应行为。WEOE 的 Comp-only 分支收到 CompAck 后结束；带数据分支接收 CopyBackWriteData，不再要求额外 CompAck。WriteCleanFull 保留 RN 的 UC/SC 状态，HN 接收脏数据责任。
- [`ChiNativeMemory.cc`](../native/gem5/ChiNativeMemory.cc) 记录实际成功的 memory_request 和返回的 memory_response。维护验收比较数据直到真实内存端口，而非只比较 RN/HN 协议名称。
- [`riscv-cbo-format.isa`](../native/gem5/riscv-cbo-format.isa) 与 [`apply-riscv-cbo.py`](../scripts/apply-riscv-cbo.py) 补齐固定 gem5 缺少的三条 Zicbom 指令，经真实地址翻译、LSQ、L1 和维护 Packet 到达 L2。修复 LSQ 对 CBO 空输入数据的复制，并避免维护指令误更新 golden memory。
- 独立 raw gate 和原生转换器识别新增 opcode、ExpCompAck、DBID、CAH=0、LikelyShared、子行 BE 和 WCF 脏数据响应。原生转换保留原始发送，不补协议消息。

## 真实 CPU 运行

拓扑为 4 RN / 4 HN / 2 SN 的 3×2 mesh；实际配置确认有四个 ChiNativeL2，零 Cache2ChiBridge。每个 L2 为 16 sets × 4 ways × 64B、16 MSHR。CPU 退出后再模拟 1,000,000 tick，检查捕获内生命周期闭合。

[`native-l2-rtl-test.c`](../native/gem5/native-l2-rtl-test.c) 包括：部分写和多核 dirty owner 交接；320 行完整写入后由全部 CPU 逐字节读回；dirty clean/flush 和 clean invalidate；私有行 clean 后保留 unique，再以容量压力驱逐；独立非缓存 RAM 上的 64-bit 写和单字节覆盖。反汇编另行确认真实三条 CBO 编码和整行写的八条普通 sd。

| 运行 | HN entries / SLC lines | CPU 退出 tick | 尾部结束 tick | TX / RX flit | 原始协议规则检查 |
|---|---|---|---|---|---|
| 基准 | 16 / 256 | `21699000` | `22699000` | 20,000 / 20,000 | 804,643，零错误 |
| 压力 | 4 / 32 | `30707000` | `31707000` | 27,627 / 27,627 | 1,098,671，零错误 |

两次四核均 PASS，12 种请求均实际出现，所有 request/snoop/write buffer/completion ack/retry 及缺失 TX/RX 的开放计数为零。报告：[基准覆盖](../build/native-rtl-run2/native-rtl-result.json)、[基准 raw gate](../build/native-rtl-run2/native-wire-audit.json)、[压力覆盖](../build/native-rtl-pressure/native-rtl-result.json)、[压力 raw gate](../build/native-rtl-pressure/native-wire-audit.json)。检查次数不是完整协议覆盖率。

压力下另按 RN 实际 RX 顺序回放信用池，1,225 次重发均已收到相应 RetryAck 及按 requester/completer/PCrdType 匹配的信用，结束时无未消费 RetryAck 或剩余信用。此次实际运行全为同 TxnID 重发、PCrdType=0；提前信用和重试换 ID 有独立夹具检查，但它们以及跨生命周期 ID 复用均不能称为本次 CPU 运行已覆盖。见 [独立证据复核](../build/native-rtl-pressure/native-rtl-evidence-review.json)。

额外的 [`check-native-rtl-behavior.py`](../scripts/check-native-rtl-behavior.py) 使用显式 root/parent leg 及 TX/RX message identity 检查：

- 基准 322 笔完成的 MakeUnique 均来自整行覆盖承诺，收到 Comp_UC，以 completion DBID 发 CompAck；无 CompData。压力有 318 笔完成事务；上表 528 次发送包含重试。
- 基准 WEOE 的 Comp→CompAck 分支 1,485 笔、CompDBIDResp→CopyBackWriteData 分支 4 笔；压力分别 1,297 和 4 笔。
- 两条指定 dirty clean/flush 行各比较 64B：RN release DAT → HN 下游 DAT → 实际接受的内存写。已知掩码、BE 和数据逐字节一致，实际内存响应先于维护 Comp 返回。clean 的第一字为 `0xaabbccdd01020304`，flush 为 `0xddccbbaa04030201`。

[基准行为报告](../build/native-rtl-run2/native-rtl-behavior.json) 151,845 项、[压力行为报告](../build/native-rtl-pressure/native-rtl-behavior.json) 135,901 项通过，零问题。

## 回归与回放

此次最新版二进制重新运行 reader 带 `fence r,r` 的 IRIW，四核轮换角色并轮换 12 种地址映射，128/128 轮完成，禁止结果 `1010` 为 0；原始 gate 2,261,898 项通过。见 [litmus 报告](../build/native-rtl-litmus-fenced/litmus-result.json) 和 [raw gate](../build/native-rtl-litmus-fenced/native-wire-audit.json)。有限轮次未出现禁止结果，不构成所有并发交错的证明。

独立回归通过：C++17 wire 工具 89 项；raw gate 42 项；原生 refiner 24 项；旧 refiner 47 项；CPU 覆盖检查器 35 项；行为检查器 29 项。后两组包含对错误数据、缺失响应、错误 ID/来源和伪覆盖的突变检查。

同一工作负载关闭导出后，四核 PASS、CPU 退出 tick `21699000`、尾部结束 tick `22699000`、全部结果字段、config.ini 和采集配置均一致；8,936 项非 host 模拟统计精确相等。仅排除报告明确列出的五项 host 性能/资源统计。开启时存在 raw trace，关闭时不存在。见 [开关不变性报告](../build/qa/native-rtl-invariance.json)；未直接比较完整缓存终态。

可导入文件：[session.chi.jsonl](../build/native-rtl-run2/session.chi.jsonl)。原有 Manifest 字段和全部 117,164 条 ObservedEvent 逐项保持一致。20,000 条实际发送全部通过 CHIron 字段映射，零 fail、零 insufficient；一项 transaction-joint 明确标记 unsupported。见 [保真检查](../build/native-rtl-run2/preservation-check.json)。

使用与 Worker 相同的 parser/replay engine 完成 117,164 个 raw 步骤和 97,164 个 protocol 视图步骤；两种视图各 7 次倒序随机跳转与逐步播放一致，结束时 active/inflight/waits 全零。见 [回放检查](../build/native-rtl-run2/replay-check.json)。Manifest 的未知初态和 `complete=false` 保持原值，生命周期闭合不等于系统初态已知。

## 复现

WSL 中从 `/mnt/d/proj/chi_flowgraph` 执行；gem5 源码和构建位于 D 盘 VHD 内的 Linux 文件系统，保持 C++17。首次安装/重建：

```bash
python3 scripts/apply-gem5.py --gem5 /root/chi-flowgraph-work/gem5
cd /root/chi-flowgraph-work/gem5
scons build/RISCV/gem5.opt -j8 --linker=gold
```

已有最新版二进制后，回到项目目录运行。这会编译真实 CPU 程序并自动运行覆盖、raw gate 和行为检查：

```bash
CHI_NATIVE_RTL_OUT="$PWD/build/native-rtl-repro" bash scripts/run-native-rtl.sh
CHI_NATIVE_RTL_OUT="$PWD/build/native-rtl-pressure-repro" \
  bash scripts/run-native-rtl.sh --hn-entries 4 --slc-lines 32 --max-ticks 200000000
CHI_NATIVE_LITMUS_OUT="$PWD/build/native-rtl-litmus-repro" \
  bash scripts/run-native-litmus.sh iriw-fenced
```

复核已保存的开关运行结果：

```bash
python3 scripts/check-native-rtl-invariance.py --on build/native-rtl-run2 --off build/native-rtl-traceoff --output build/qa/native-rtl-invariance.json
```

Windows 转换和回放状态验证：

```powershell
.\build\chi-refine.exe --input build/native-rtl-repro/raw.jsonl --output build/native-rtl-repro/session.chi.jsonl --profile h-native-256
node scripts/check-native-replay.mjs build/native-rtl-repro/session.chi.jsonl build/native-rtl-repro/replay-check.json
```

独立检查器测试可用 Python 3 运行 `tests/native/test_native_rtl_checker.py` 和 `tests/native/test_native_rtl_behavior.py`。不要把测试夹具算作真实 CPU 流量。

## 验证边界

- CBO 当前支持 **SE timing**；FS 的 envcfg 权限控制尚未实现，执行会明确陷入非法指令。
- 非缓存用例是独立 Normal non-cacheable RAM（无 cacheable alias），不是 Device 外设。CPU STRICT_ORDER 不等于 Device MemAttr；当前 RN-F/HN-F/SN-F 拓扑不实现 HN-I/Device 路径。
- classic L1 对共享 snoop 采用保守失效，L2 保留允许的干净状态；上游合并、仲裁及延迟不复制 RTL 逐周期行为。
- CAH 固定 0、CompAck 使用固定 RTL 的 Resp=0。没有实现完整 Issue H、DMT/DCT/DWT、Atomic、DVM 或全部错误恢复。
- CHIron transaction joint 未运行；不能把全局 TX 顺序当成各端点 RX/TX 顺序。实际握手与生命周期由独立 raw gate 检查，字段检查通过不等于完整 CHI 验证。
- 未逐行导出比较全部缓存终态，未做 RTL lockstep，未穷举全部并发交错。当前通过的 CPU 数据、协议和回放证据只支持本文明确列出的范围。

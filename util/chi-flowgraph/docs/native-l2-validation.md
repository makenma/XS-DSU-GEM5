# 原生 CHI L2 验证记录

本页保留此前普通访存版本的验收记录。新增的昆明湖 v2 **12 类请求**、真实 CPU CBO、WEOE 双分支、Normal non-cacheable RAM 和最新版回归结果见 [RTL 请求选择对齐验证](native-l2-rtl-alignment.md)。

更新：2026-10-03。本页区分独立协议工具验证和实际 CPU/L2 运行证据。原有 classic L2 + Cache2ChiBridge 的示例不能证明新 L2 的实现正确。

同日后续 IRIW 长跑发现并修复了排队上游请求与 snoop/probe 的等待环，详见 [litmus 验证](native-l2-litmus.md)。修复后二进制的 baseline、pressure、traceoff 再验均通过；最新压力检查为 922,767 项，导出开关前后 8,765 项非 host 统计一致，证据见 [`native-litmus-regression.json`](../build/qa/native-litmus-regression.json)。下表保留首次验收的原始产物和计数，未覆盖历史轨迹。

## 当前已验证

| 项目 | 实测结果 | 证据 |
|---|---|---|
| C++17 原生字段与数据工具 | 89 项检查通过，GCC 13，`-Wall -Wextra -Werror` | [`native-wire-unit.txt`](../build/qa/native-wire-unit.txt) |
| 原始轨迹 gate 自身的突变测试 | 24 项通过，Windows Python | [`native-wire-gate-tests.txt`](../build/qa/native-wire-gate-tests.txt) |
| 原生轨迹到回放格式的转换 | Windows / WSL Linux 各 19 项通过，C++20 展开器 | [`Windows`](../build/qa/native-refiner-tests.txt)、[`Linux`](../build/qa/native-refiner-tests-linux.txt) |
| 既有展开路径回归 | Windows / WSL Linux 各 47 项通过，C++20 展开器 | [`Windows`](../build/qa/native-legacy-refiner-tests.txt)、[`Linux`](../build/qa/native-legacy-refiner-tests-linux.txt) |
| 既有 gem5 协议组件回归 | 90 / 90 通过：RNF bridge 25、HNF 62、FlowGraph 3 | [`RNF`](../build/qa/native-l2-rnf-regression.log)、[`HNF`](../build/qa/native-l2-hnf-regression.log)、[`FlowGraph`](../build/qa/native-l2-components-regression.log) |
| 既有 classic L2 + bridge 四核 IRIW | tick `1000000`；`done=[1,1,1,1]`、`errors=[0,0,0,0]` | [`flowgraph-result.json`](../build/native-l2-legacy-iriw/flowgraph-result.json) |
| 四核 KMH CPU → 新 L2 → NoC → HN/SN | baseline 与压力配置均为四核 PASS，普通 load/store/fence，无协议 opcode 注入 | [`baseline`](../build/native-l2-run/native-l2-result.json)、[`pressure`](../build/native-l2-pressure/native-l2-result.json) |
| 实际原生端点 TX/RX 的独立协议核查 | baseline 707,677 次、pressure 918,915 次检查，均零错误、零开放生命周期 | [`baseline`](../build/native-l2-run/native-wire-audit.json)、[`pressure`](../build/native-l2-pressure/native-wire-audit.json) |
| 实际轨迹的 CHIron 字段映射及无修改转换 | baseline 18,048 条、pressure 23,622 条 field mapping pass，零 fail / insufficient；每份仅一条 joint unsupported | [`baseline`](../build/native-l2-run/native-refiner-audit.json)、[`pressure`](../build/native-l2-pressure/native-refiner-audit.json) |
| 导出器开关一致性 | CPU 自检、退出 tick、尾部结束 tick 和 8,763 项模拟统计一致 | [`native-l2-invariance.json`](../build/qa/native-l2-invariance.json)；未导出全部 L1/L2 缓存终态 |
| baseline / pressure 回放最终状态与回退一致性 | raw / protocol 双视图逐步播放与各 7 个倒序随机跳转一致，末尾 active、各节点 inflight / waits 全零 | 实际回放 engine：[`baseline`](../build/qa/native-replay.json)、[`pressure`](../build/qa/native-replay-pressure.json) |
| 最新原生转换路径的 Linux 构建 | GCC 13、C++20 编译通过 | `/root/chi-flowgraph-work/refiner/chi-refine`；测试日志见上方 |
| 新原生会话的浏览器回放 | 完整文件导入、实测 Size=6、原始字段对照、前后单步、tick 定位和视图切换通过；无页面异常 | [`browser report`](../build/qa/native-l2-browser.json)、[`截图`](../build/qa/native-l2-browser.png) |

浏览器界面在缺少 CPU instruction 关联记录时不显示旧的 `0/19` 事务覆盖面板，避免将未采集的来源关系解释为零覆盖。此显示修正已通过前端生产构建及 65 项既有教学/特殊场景测试。

前四项均使用明确标注的验证夹具或纯函数输入，未冒充 gem5 运行轨迹。检查数量不能解释为完整 Issue H 覆盖率。

### 四核实际运行

工作负载为 [`native-l2-test.c`](../native/gem5/native-l2-test.c)，由实际 KMH CPU 执行普通 RV64 load/store/fence，不使用测试 MMIO 或协议 opcode 注入。四核依次交接 dirty owner 并仅更新一条 cache line 中的部分 64-bit 字；随后每核写入 80 条 cache line，所有四核都检查全部 320 条、20,480 字节数据。每行的八个 64-bit 字包含非零高位的确定性模式。四个 hart 均报告 PASS。

实际路径为 `KMH CPU → classic L1 → ChiNativeL2 → CHI NoC → HN → ChiNativeMemory → SimpleMemory`，拓扑为 4 RN / 4 HN / 2 SN 的 3×2 mesh。每个新 L2 使用 16 sets × 4 ways × 64B、16 个 MSHR；上下游 CHI 消息均由在线端点产生。CPU 退出后额外运行 1,000,000 tick，让剩余消息到达并完成协议生命周期，再对原始 TX/RX 做 closed-capture 检查。

| 运行 | HN 事务名额 / 每 HN SLC lines | CPU 退出 tick | 尾部结束 tick | 实际 TX / RX | 原始规则检查 |
|---|---|---|---|---|---|
| baseline | 16 / 256 | `17630000` | `18630000` | 18,048 / 18,048 | 707,677，零错误 |
| pressure | 4 / 32 | `26526000` | `27526000` | 23,622 / 23,622 | 918,915，零错误 |

baseline 包含 ReadNotSharedDirty、ReadUnique、SnpNotSharedDirty、SnpUnique、SnpRespData、Evict、WriteBackFull / CompDBIDResp / CopyBackWriteData 及 CompAck。pressure 进一步实测到 430 次 RetryAck、430 次 PCrdGrant，以及 338 笔 HN→SN WriteNoSnpFull / DBIDResp、676 拍 NonCopyBackWriteData；还覆盖 SnpCleanInvalid 与无数据 SnpResp。两份 gate 报告的请求、snoop、写缓冲、completion ack、重试和缺失 TX/RX 计数全部为零，`completeCaptureValidated=true`。

baseline 回放保存在 [`examples/native-l2/run.chi.jsonl`](../examples/native-l2/run.chi.jsonl)，pressure 回放保存在 `build/qa/native-pressure.chi.jsonl`。补齐在线 snoop 完成观测后已重新运行两种配置，转换逐条保留了 107,310 / 132,050 个原始 ObservedEvent；独立比较确认原始 Manifest 字段、实测协议字段、payload、掩码及锚点 tick 未改变。两份会话的 CHIron 字段映射分别 18,048 / 23,622 条通过，零 fail、零 insufficient；各有一条明确的 transaction joint unsupported，原因见下文。

baseline 在实际回放 engine 中完成 107,310 个 raw 步骤和 89,262 个 protocol 视图步骤（其中 18,048 条 flit）；pressure 完成 132,050 个 raw 步骤和 108,428 个 protocol 视图步骤（其中 23,622 条 flit）。每种配置、每种视图的 7 个倒序随机跳转状态均与逐步播放一致，结束时 active 事务及所有节点的 inflight / waits 全零。protocol 视图仍包含实际状态/分配/完成等 ObservedEvent，因此视图步骤数大于协议 flit 数。

原始 Manifest 的 `complete=false` 和未知初态仍保留。closed gate 的通过只说明本次捕获内消息的握手与生命周期已闭合，不补造起始缓存状态，也不将它改写为全系统初态已知。

baseline 导出器开启 / 关闭时，四核自检结果、CPU 退出 tick `17630000`、尾部结束 tick `18630000` 和 8,763 项模拟统计完全相同。没有导出并逐项比较全部 L1/L2 缓存终态，故不将上述证据扩展为完整缓存终态等价声明。

### 历史崩溃前缀核查

`build/native-l2-invalidate-demand/raw.jsonl` 是 CPU 第三轮运行因尚未接入的 classic InvalidateReq 停止后留下的**截断前缀**。独立 gate 以显式 `--allow-open` 检查了 1,148 条发送、1,144 条接收，共 45,124 次规则检查，未发现前缀内字段或握手错误。原生转换直接检查了 1,148 条实测发送的 CHIron 字段映射，零 fail。

末尾仍有 4 个开放请求及对应的 4 条未记录 RX 的发送消息，因此不能称为完整通过；原生转换分别将它们标为 insufficient，joint 仍为 unsupported。机器报告明确保存 `completeCaptureValidated=false` 及开放证据数量。报告见 [`native-prefix-summary.json`](../build/qa/native-prefix-summary.json) 和 [`native-prefix-wire-audit.json`](../build/qa/native-prefix-wire-audit.json)；可导入回放文件为 `build/qa/native-prefix.chi.jsonl`。

## 已覆盖的协议工具行为

`ChiNativeWire.hh` 使用 CHI 字段语义：REQ `Size=log2(bytes)`、SNP `Addr=PA>>3`、64B line 在 256-bit DAT 上使用 `DataID=0/2`。部分访问也占一个完整的 32B 物理 DAT；例如 8B、地址偏移 40 的数据位于 `DataID=2` 的 byte lane 8～15。

组包与重组分别检查以下内容：

- 12-bit TxnID/DBID 和配置的 NodeID/Addr 位宽；拒绝把粗粒度的字节数 Size 或旧写 opcode 当作原生编码。
- DAT 乱序、重复拍、错误 DataID、跨拍 SrcID/TgtID/TxnID/HomeNID/Resp/适用 DBID 不一致。
- 接收完成依据请求 Size 和 DataID 覆盖计算；`last`、`beatOffset` 是仿真元数据，不参与原生数据重组。
- 写入和 snoop 的 `BE=0` 已知字节必须为零；读响应的 BE 不影响有效数据。未知 chunk 保留未知，组包完成不等于数据全部已知。
- Write/Snoop DAT 的 HomeNID 为零；无 DataPull 的 SnpRespData 的 DBID 为零；CopyBackWriteData_I 的 BE 全零。
- SnpUnique、维护及 Stash snoop 的 DoNotGoToSD 必需值，以及维护/Stash snoop 的 RetToSrc 必须为零。

`check-native-wire.py` 独立读取实际端点 TX/RX，不调用展开器、不借助 flow/root/leg ID 决定协议匹配，检查：

- TxnID、write DBID、completion DBID 和 CompAck 的实际关联，包含不同节点相同 TxnID；DAT.TxnID 必须使用 DBIDResp.DBID。
- 同一 completer 发出的分离 Comp/DBIDResp 使用相同 DBID；Comp-only CopyBack 使用 CompAck，无虚构 DAT。
- PCrdGrant 按 requester/completer/PCrdType 匹配，不使用 PCrdGrant.TxnID；允许 credit 先到，重试前仍须同时具有 RetryAck 和匹配的 credit。
- 无 DMT 的 CompData.HomeNID 等于 completer，即原请求 TgtID；HN→SN 的 ReadNoSnp 不请求 CompAck。
- Raw Size、SNP 编码地址、DataID、physical DAT 宽度、natural byte lanes 和 BE；消息 TX/RX 的真实字段与数据不能被 router 改写。
- 完整采集结束时未完成请求、snoop、DBID、CompAck、重试和缺失的接收证据。只有显式 `--allow-open` 才允许截断采集的开放生命周期。

## 原生转换与 CHIron 边界

`chi-refine --profile h-native-256`，或输入 Manifest 的 `profile=h-native-256`，启用原生路径。

每条实际 TX 映射为一条回放 ProtocolEvent；RX 仍完整保留，并通过 `sourceEventIds` 关联。原始 ObservedEvent、Manifest 原有字段和 tick 均保留。转换器不分配或修改 ID，不重新编码 opcode/Size/DataID，不拆拍或补齐 payload，也不添加协议消息。`--ordered-read` 和 `--rotate-retry-id` 在原生模式下被拒绝。

原生 numeric 字段直接提交给固定版本 CHIron E.b 的字段映射检查器。缺失的必需实测字段显示为 unknown；本有限 profile 未采集的固定零字段明确标为 modeled，不能当作实测值。错误字段保留原值并报告；超出固定 profile 的非零可选字段记录 unsupported，不以另外构造的零字段掩盖它们。

**原生 CHIron transaction joint 当前明确记录 unsupported。** 展示采用 TX 顺序，而正确的 endpoint joint 必须分别使用各端点实际 RX/TX 顺序；当前没有把全局发送顺序冒充节点接收顺序。字段映射通过不等于事务通过。原始握手和生命周期由独立 `check-native-wire.py` 检查；转换命令自身不会自动执行该 gate。

本 profile 不包含完整 Issue H、DMT/DCT/DWT、Atomic、DVM、Tag、错误恢复或所有 snoop 扩展。新 L2 对 classic L1 使用保守探测策略：共享/clean snoop 可以额外使上层 L1 副本失效，而 RN 内的新 L2 保留协议允许的干净共享副本。上述普通 CPU 工作负载已通过，仍不构成逐周期 RTL 等价声明或所有并发交错的证明。

## 复现命令

以下命令从项目根目录执行。Windows 项目目录为 `D:\proj\chi_flowgraph`，WSL 路径为 `/mnt/d/proj/chi_flowgraph`；gem5 源码和构建位于 D 盘上的 WSL 文件系统 `/root/chi-flowgraph-work/gem5`。

独立 C++17 工具检查，使用安装后的真实 Raw 类型，无需链接 gem5：

```bash
g++ -std=c++17 -Wall -Wextra -Werror \
  -I/root/chi-flowgraph-work/gem5/src -Inative/gem5 \
  native/gem5/ChiNativeWire.test.cc \
  -o /root/chi-flowgraph-work/native-wire-test
/root/chi-flowgraph-work/native-wire-test
```

Windows 或 Linux 下构建独立转换器并验证；这里的 `python` 应指向可用的 Python 3：

```text
pnpm native:build
python tests/native/test_refiner_native.py
python tests/native/test_native_wire_gate.py
python tests/native/test_refiner.py
```

已有最新版 gem5 二进制时，运行普通 CPU load/store/fence 工作负载并采集：

```bash
bash scripts/run-native-l2.sh
python3 scripts/check-native-wire.py \
  --input build/native-l2-run/raw.jsonl \
  --output build/native-l2-run/native-wire-audit.json \
  --require-opcode ReadNotSharedDirty \
  --require-opcode ReadUnique \
  --require-opcode WriteBackFull \
  --require-opcode CompAck
```

只有 CPU 结果与独立 raw gate 均通过后，才能将真实运行标为通过。随后生成可导入回放文件；此命令不会修正任何原生协议错误：

```bash
mkdir -p examples/native-l2
/root/chi-flowgraph-work/refiner/chi-refine --input build/native-l2-run/raw.jsonl --output examples/native-l2/run.chi.jsonl --profile h-native-256
```

Windows 可执行文件为 `build/chi-refine.exe`。输出父目录需预先创建。网页本地导入该文件；旧 coarse 示例继续使用旧 profile。

压力运行与原始协议核查：

```bash
CHI_NATIVE_L2_OUT="$PWD/build/native-l2-pressure" \
  bash scripts/run-native-l2.sh --hn-entries 4 --slc-lines 32
python3 scripts/check-native-wire.py \
  --input build/native-l2-pressure/raw.jsonl \
  --output build/native-l2-pressure/native-wire-audit.json \
  --require-opcode RetryAck --require-opcode PCrdGrant \
  --require-opcode WriteNoSnpFull --require-opcode NonCopyBackWriteData
```

导出关闭运行使用 `CHI_NATIVE_L2_TRACE=0`，并给定独立输出目录，例如 `CHI_NATIVE_L2_OUT="$PWD/build/native-l2-traceoff"`；保持其他参数与 baseline 相同后，比较两份 CPU 结果和模拟统计。不要以关闭导出时没有 flit 记录解释为未发生协议事务。

使用与 Worker 相同的 parser / replay engine 验证双视图逐步播放、回退和最终状态：

```text
node scripts/check-native-replay.mjs examples/native-l2/run.chi.jsonl build/qa/native-replay.json
node scripts/check-native-replay.mjs build/qa/native-pressure.chi.jsonl build/qa/native-replay-pressure.json
```

## 手册依据

依据项目内 [`IHI0050H_amba_chi_architecture_spec.pdf`](../IHI0050H_amba_chi_architecture_spec.pdf)：B2.4/B2.5 的 ID 与返回字段，B2.5.1.4 第 118 页的无 DMT CompData.HomeNID，B2.7.3 的 CompAck，B2.9 第 162～165 页的 Size/BE/packetization，B2.10 的 retry credit，B4.8 第 265～269 页的 snoop 状态，B4.11 第 283～284 页的同地址 hazard，以及 B13.9/B13.10 的字段位宽和 RetToSrc/DoNotGoToSD。

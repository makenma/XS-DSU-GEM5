# 四核 litmus 与 19 类 CHI 事务

本轮在固定 gem5 上增加行为模型、真实 CPU 程序和 JSONL 1.1 会话。网页默认加载 IRIW；下拉框中的协议测试覆盖全部 19 类，压力测试展示真实重试、同址竞争和 SLC/SF victim。原有九个会话继续保留。

## 模型与协议选择

四个 `XiangshanCore` / RiscvO3CPU 使用私有 L1、`L2CacheWrapper` 和真实 classic L2 tags/data。四个独立 SE 进程共享同一组物理页，代码和栈各自分配。SE 页表原来没有应用 `Uncacheable` 映射标志，补丁修正了 Request 属性，测试页因此实际走非缓存端口。

扩展配置使用 `ChiExtendedHome`：收到的 REQ/RSP/DAT 驱动有限 HNF 事务表、目录、缓存行、snoop、dirty victim 和内存访问。它是用于本项目场景的行为 HNF，并非香山 RTL 的移植。旧 `HomeNodeFull` 和组件场景继续使用原控制器。SLC/SF 行容量在这个模型中由同一行表约束；不声称复现 RTL 的 bank、way 或仲裁时序。

参考源码固定为 [XSCache MSHR](https://github.com/OpenXiangShan/XSCache/blob/300515bc7b2f65ca988041a984204489d80ec2fb/src/main/scala/coupledL2/MSHR.scala)，协议依据是本地 `IHI0050H_amba_chi_architecture_spec.pdf`。普通读取映射为 ReadNotSharedDirty；部分写升级使用 ReadUnique，已有本核响应者的权限协调使用 CleanUnique。**MakeUnique 要求随后覆盖整行**（B4.1 Dataless transactions），协议程序在 MakeUnique 完成后确实写入全部 64 字节。不能把普通 8 字节 store 的升级映射成会丢弃其他 dirty 字节的 MakeUnique。

Issue H 数据响应保留真实状态：SnpRespData_SD=3、I_PD=4、CompData_UD_PD=6；旧 coarse profile 的编码保持兼容。Copyback 使用 CompDBIDResp → CopyBackWriteData；MakeUnique / CleanUnique 和分配读取都有 CompAck。

## 测试页 ABI

每核 VA `0x200000000` 映射到 PA `0x40000000 + hart*4096`，位于 DRAM 外。共享数据 VA 为 `0x100000000`，实际 PA 由 SE 页分配器返回并写入控制寄存器。

| 字节偏移 | 内容 |
|---|---|
| 0 / 8 / 16 | Issue H opcode / 物理地址 / sizeBytes |
| 24 / 32 | 每字节 BE 位图 / stashTarget NodeID |
| 40 | 仿真器设置的共享物理页基址 |
| 48 | CPU 测试程序提交的实际调用 JAL PC |
| 64–127 | little-endian payload，copyback 忽略此字段并使用真实缓存数据 |
| 256 | fence 后写 doorbell |
| 264 / 272 | 状态（1 busy、2 done、3 error）/ 错误码 |
| 512–575 | 读取返回数据 |
| 1024–1279 / 2048 | CPU 实际结果 / 完成标志 |

自然对齐的 1/2/4/8/16/32/64 字节行内访问合法。分配读、copyback、full write 及 MakeUnique 使用完整 64 字节。准备阶段等待本地 MSHR/write buffer 排空并固定真实 L2 行；需要 copyback 数据时通过本核 RN 的正常 snoop 端口取得最新 L1 数据。提交前检查缓存前提，错误操作以状态 3 结束。准备期间暂存本核普通请求，必要 snoop 继续处理。

原请求者的 stash Comp 和目标的 DataPull / CompAck / L2 安装分别采集。RN1 接受提示并由 CPU 命中读回；RN3 拒绝提示，原事务正常完成。拒绝不是伪造的数据传输。

## 拓扑、SAM 与归因

实际拓扑是六个 router 的 3×2 mesh，四角各连接 RNF/HNF，中间上下连接两个 SNF，共 10 个协议端点。RN→HN 为现有 CMN SCG XOR，PA 宽度 48；HN→SN 为同一个选择函数应用 mask `0x1040`，即 `PA[6] XOR PA[12]`。SimpleMemory 两个通道使用相同 XOR mask，victim 写回也共用此函数。

指令 ID 为 `cpu:hart:iteration:dynamicSeq`，PC 和序号以字符串保存。普通访存来自实际 Request；提交的 store buffer 保留来源集合，MSHR 合并以 cause_add 增补来源。子事务、重试继承来源；dirty snoop / copyback 保留历史写入者。CPU 撤销 load 时记录 instruction_cancel，已经发送的消息仍保留其实际来源。

测试接口的动态来源始终是实际 doorbell 指令。instruction_context 额外记录程序测量的调用 JAL PC，网页同时高亮具体 `chi(opcode,...)` 调用行；不会把调用 PC 冒充为 doorbell PC 或创造不存在的动态执行。

router 转发作为 route 观测引用已有 messageId，展开器不再生成一份重复消息。原始/展开切换保留当前锚点，点击源码行直接定位本核 RN→HN 请求发出时刻，也可选择动态实例；cache hit 定位到执行观测。默认时刻步进按真实 TX / route / RX tick 分组，所有在途 flit 共用同一时间，逐段慢放后停住。等比例模式保留固定仿真时间倍率。事务时序与事件列表左右并排，选中项同步居中。缺少来源时明确显示未知。

## 重现

gem5 固定 commit `65f3241a6cd486dffddaca251536f5c691af9a1f`；CHIron 固定 `55cd1e1d92142960af2b57c686fc9f35ce7286be`。交叉工具链固定 GCC 13.3.0 / binutils 2.42、RV64IMAFD / LP64D，无压缩指令；构建脚本拒绝其他版本。预取器关闭，随机种子为 1。

在 WSL Ubuntu 执行：

```bash
cd /mnt/d/proj/chi_flowgraph
python3 scripts/apply-gem5.py --gem5 /root/chi-flowgraph-work/gem5
cd /root/chi-flowgraph-work/gem5
scons build/RISCV/gem5.opt -j4
cd /mnt/d/proj/chi_flowgraph
bash scripts/run-litmus.sh iriw
bash scripts/run-litmus.sh protocol
bash scripts/run-litmus.sh stress --hn-entries 1 --slc-lines 4
bash scripts/run-litmus-matrix.sh
```

Windows 构建 C++ 展开器后，使用 `python scripts/check-litmus.py build/litmus-protocol --variant protocol --publish`；iriw / stress 同理。只有 CPU 完成标志、实际读回值和协议检查通过的会话才能发布。`scripts/refine-demos.mjs` 会保留这些会话，不会在重建旧示例时清掉它们。

三份地址轮换使用 `python scripts/check-litmus.py build/litmus-iriw-rotation-1 --variant iriw --session-id litmus-iriw-rotation-1 --publish` 发布，编号 2/3 同理；共享物理地址来自运行结果，不由网页选择。`python scripts/check-litmus-attribution.py` 独立检查动态身份、PC 映射、合并/子事务来源、撤销、历史写入者、route 身份及两个实际 SAM 的运算结果。

验收报告分别保存在 `build/litmus-*/acceptance.json`、`build/qa/litmus-invariance.json`、`build/qa/litmus-browser-report.json` 和 `build/qa/browser-report.json`。`python scripts/summarize-extended.py` 检查这些结果并生成汇总 `build/qa/extended-acceptance.json`。十万事件性能文件是明确标注的性能 fixture，不计入 CPU 协议覆盖。

本次验收：19/19、128 次跨核读回检查、300 次 RetryAck、315 次 victim；47 项原生、14 项前端、90 项 gem5 组件回归通过。协议测试的 11 个部分/无有效字节 payload 对比保留 insufficient，未伪造未知字节；其 CPU 读回值另行检查。实际 IRIW 本次 P2/P3 都读回 `[0,0]`，这是固定运行的观测结果，不代表枚举所有可能的 litmus 结果。

CHIron E.b 检查可支持的字段和事务关联；Issue H 新事务及 implicit DataPull 使用本项目的显式流程检查，覆盖范围写入 refinement metadata。h-extended-256 保留重试的建模 TxnID，避免旧 CHIron 把同时存在的重试与其他请求误认作同一事务。它不等于完整 Issue H 一致性认证；Atomic、DVM、RTL 联仿、在线仿真服务及其他可选变体仍在本轮范围外。

教学目录及可重现的新增 CPU 用例见 [teaching.md](teaching.md)。移动 flit、暂停、C/ASM 跳转的浏览器检查为 `scripts/browser-replay.cjs`，教学交互检查为 `scripts/browser-teaching.cjs`。

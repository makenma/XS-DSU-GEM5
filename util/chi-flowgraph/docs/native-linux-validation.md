# 四核原生 CHI L2 的 Linux 验证

任务建立：2026-10-03。状态：进行中；现有 SE 结果不计作 Linux 验收。

最近的完整验收使用不可变model08（SHA `e71ec1143c2840394c4c63348edba86f3ff46eaab14ef6ad9f42e0ff2f521550`）。随机迁移补充运行已在 litmus 期间异常退出；core 随后因 WSL 环境重启而中断，没有真实宿主完成记录。两组都不能计作通过，当前正在修复和回归取指错误。输出保留在D盘WSL ext4中的`/root/chi-flowgraph-work/linux-validation/runs/model08/`；kernel-build 尚未启动。冻结的负载、固件、拓扑和规模未缩减，完整Linux验收未完成。启动计划为`build/linux-validation/model08-launch-plan.ready-wsl.json`，原工具会话身份记录为`model08-execution-identity.json`：core驱动71505，random驱动30493。model08独立验收使用`check-native-linux-complete-08.py`及`--expected-driver-session 71505`，不能伪造消失会话的退出码或使用旧05完成记录。

2026-10-04 02:32 UTC 重新检查时，WSL uptime 已重置，core PID 192656、其驱动和监督器都不存在，`write_stdin(71505)` 返回未知进程。最后完整进度 tick 为 `364000000000`，没有 host-exit-code 或最终结果。[中断证据](../build/qa/model08-core-interruption-20261004T023410Z/evidence.json)保存完整日志和 SHA；此前出现 PowerShell 初始化内存异常，但 WSL 重启的原因尚未证实。串口中已出现完整 `CHI_LINUX_BOOT_PASS`，随后 `guest-control[68]` 在 CPU2 报 SIG11：`epc=0x10c02`、`badaddr=0xffffffff80007c06`、cause `0xc`，必须另行定位，不能因为环境中断而忽略。后续候选需重新执行完整工作负载。

随机补充运行在 tick `241337018000` 触发 `Fetch::lookupAndUpdateNextPC` 的 `stream.startPC <= curr_pc && curr_pc < stream.predEndPC` 断言（`fetch.cc:783`）。实际 gem5 宿主退出码为 **-6**，外层会话 30493 实际退出码为 **1**，并未产生 `LINUX_LITMUS_END`。[完整失败证据](../build/qa/model08-random-failure-evidence-20261004T002825Z/evidence.json)保存原始日志、串口、退出码、执行计划、进度和 SHA；[真实外层完成记录](../build/linux-validation/model08-random-driver-completion.json)保留工具终态。[总验收报告](../build/linux-validation/native-linux-complete-08.json)已重新生成，仍为 INCOMPLETE，并明确记录 random 的 known failure 和非零宿主退出。正在定位取指与预测 stream 范围不一致的原因；现有日志不足以确定出错 PC 或根因，不能仅凭该断言归因于 L2。

已用独立裸 FS 固件复现该断言的一条具体路径：先在同一虚拟地址执行 512 次 `c.nop; c.j`，切换 Sv39 页表后，该地址变为覆盖旧 `+2` 分支位置的 32 位 `addi`。不可变 model08 的同布局对照正常 `m5_exit(0)`（tick `23707000`），改变布局组在 tick `8025000` 触发同处断言、实际退出 **-6**。[复现结果与原始日志摘录](../build/qa/fetch-stream-model08-baseline/summary.json)显示实际 PC 顺序走过旧分支位置，而下一 FTQ 仍指向旧目标；Decode 已发出的 return 纠错尚未到达 Fetch。这是四 CPU / 原生 CHI NoC 上仅 hart0 活动的裸 FS 诊断，不计 Linux 验收；原 Linux 失败缺少现场 PC，尚未证实同根因。主运行曾打印 `[0.324622] Run /init as init process`，之后发生的段错误和环境中断已在上文记录。

取指边界候选 model09 已实际编译成功，SHA 为 `0e45dfb345200506be4940e0a940a0ada4bc82905f1f1ae22059f3a96f044597`。聚焦回归的真实驱动会话 34173 已退出 0：[20 步结果](../build/qa/model09-focused-regression/summary.json)包括 8 个取指固件、4 个 CBO 固件、四核 RTL/BPU 各自的 trace on/off 运行及与 model08 的等价检查。[独立覆盖审计](../build/qa/model09-fetch-boundary-coverage-audit.json)确认 5 个错误槽位场景确实执行恢复，3 个合法场景未触发；AMO 三个微操作和合法前缀已送达 Decode，固件数据检查通过。旧 model08 的 cross-stale 场景也能通过，故该项只证明新恢复路径覆盖，不能伪称旧模型必崩。

随后新增的页边界回归证明另一独立缺陷：model08 和 model09 的页内 return 都在 tick `28939000` 错误触发 instruction page fault，完整 debug SHA 相同；model09 页尾 2B 压缩 return 也失败。真正 32 位跨页指令的精确 cause/EPC/TVAL 对照通过，但旧实现未依据实际指令长度决定是否需要后页，不能由这一项通过推出实现正确。[页边界原始证据](../build/qa/fetch-page-fault-baseline/README.md)保留三项失败及对照。正在实施按实际所需字节延迟异常的后续候选，model09 尚未用于完整 Linux 重跑。

model10 将预取页的故障保留到真实指令需要该字节时再投递，分别检查 16 位与 32 位指令；正常成功取指仍等待原有两次访问完成。candidate01 实际编译退出 2，原因是通过派生类调用了 protected 的 `AddressFault::exception()`，未产生可运行候选。[失败完成记录](../build/qa/model10-candidate01-build-completion.json)已保留。candidate02 仅将该表达式改为公开基类虚接口 `precise->exception()`，[独立源码复核](../build/qa/model10-fetch-demand-fault-candidate02-review.json)确认其余差分未变。candidate02 已实际编译退出 0，二进制 SHA 为 `7aba95946686c49c763e54d1876670e4e737c90ed843fb0a4905f400c326309a`，见[构建完成记录](../build/qa/model10-candidate02-build-completion.json)。

candidate02 的 focused 回归实际完成了 16 次 CPU 运行，均为宿主退出 0：11 个取指固件（原 8 项边界用例及 3 项页故障用例）、4 个 CBO 固件，以及 1 次四核 SE RTL trace-on 自检。两个合法 return 用例检查返回值、返回地址和最终 supervisor ecall；真实跨页指令检查 instruction page fault 的精确 EPC/TVAL，三项均通过。它们是裸 FS/SE 定向证据，不计 Linux 套件通过。[本轮小证据归档](../build/qa/model10-focused-regression/summary.json)包含原始驱动 state、各项结果、实际退出证据及[归档 SHA 清单](../build/qa/model10-focused-regression/archive-sha256.json)；完整 raw/debug 留在 D 盘 WSL 的原运行目录。

随后严格 SE 等价比较失败，真实驱动会话 **29321 退出 1**。新旧 CPU 退出 tick 均为 `21699000`、drain tick 均为 `22699000`，四核自检、配置和完整 `raw.jsonl` 字节相同；但各 8936 项非 host 统计中有 **22 项不同**，集中在 CPU0 Fetch/Decode 状态、停顿及派生统计，例如 `fetch.cycles` 增加 1、`decode.decodedInsts` 减少 2、`TrapPending` 计数减少 1。[严格失败报告](../build/qa/model10-focused-regression/se-rtl-on-equivalence.json)保持 `passed=false`，不能用数据和 tick 相同覆盖统计差异。驱动据此停止，余下 SE RTL trace-off、BPU trace-on/off 三次运行及各自比较均未执行；差异原因仍在定位。[pending 启动状态](../build/linux-validation/model10-launch-spec.pending-status.json)保持 **INCOMPLETE / knownFailure=true**，没有生成 READY，也没有启动 model10 的正式 core、random 或 kernel-build Linux 运行。

存储位置已再次核验并补齐：[D盘存储记录](../build/qa/wsl-d-drive-storage/verified.json)确认 Ubuntu 根盘为 `D:\wsl\Ubuntu\ext4.vhdx`，WSL 交换文件已改为 `D:\wsl\swap.vhdx` 并生效，旧 C 盘交换文件已自动清除；C 盘仅保留必需的 59B `.wslconfig` 设置文件。本项目构建、日志和临时产物继续保存在 D 盘及其中的 WSL 文件系统内。

model08 的两组实际串口均已报告 `smp: Brought up 2 nodes, 4 CPUs`；guest 日志时间分别为 0.076279 和 0.076280。完整串口捕获、SHA 和进程身份保存在[四核上线记录](../build/qa/model08-four-cpu-online-20261003T214509Z.json)。该记录只确认四核及两个 NUMA 节点上线；正式 boot gate 还要求进入 guest init、验证 sysfs 拓扑和取得成功标记。

两组随后分别在 guest 日志时间 0.151451 和 0.151607 输出 `Serial: 8250/16550 driver, 4 ports, IRQ sharing disabled`，已越过 BFQ 后的无串口输出窗口。[串口驱动初始化记录](../build/qa/model08-serial8250-milestone-20261003T231325Z.json)保留完整串口、SHA 和原进程身份；其中最近完整 progress tick `176000000000` 是独立采样的进度边界，不是该串口消息的精确 tick。该快照尚无 guest init、阶段结果或宿主退出记录，仍不能判为 boot gate 通过。

随机迁移补充运行随后实际打印 `[0.179010] Run /init as init process`，记录了内核开始尝试执行 `/init`。[init 启动记录](../build/qa/model08-init-milestone-20261003T235532Z.json)保留两组完整串口、SHA 和原进程身份；此时 core 尚未出现该行，两组均无 `CHI_LINUX_ONLINE_CPUS`、`CHI_LINUX_ONLINE_NODES`、`CHI_LINUX_BOOT_PASS` 或阶段结果。该消息不能单独证明用户态初始化成功，正式启动和全部测试仍待验收。

补充运行的后续[guest 启动检查记录](../build/qa/model08-userspace-boot-markers-20261004T000705Z.json)已捕获完整 `uname`、`CHI_LINUX_ONLINE_CPUS=0-3`、`CHI_LINUX_ONLINE_NODES=0-1` 和 `CHI_LINUX_BOOT_PASS`。冻结 init 的 SHA 与输入清单一致，脚本在重新读取并断言两项 sysfs 拓扑后才输出成功标记，因此该补充运行的 guest 启动检查已通过。此快照尚无 `LINUX_LITMUS_BEGIN`、测试结果或宿主退出记录，core 仍未进入 guest init；完整 Linux 验收保持未完成。

补充运行随后已实际执行 litmus 冒烟程序，[启动证据](../build/qa/model08-userspace-litmus-start-20261004T002411Z.json)记录完整 `LINUX_LITMUS_BEGIN` 及首项 `chi_sb32000` 的测试定义和汇编输出。guest 报告的程序 SHA `30724f4ccfbdaa015657243bd3c306e04cf33a29f59aeeb05afc1b1c44364f32` 与冻结 index、manifest、实际 RISC-V ELF 一致，冻结调用为 `-a 4 -s 8 -r 1`。该快照尚无完整单项结果、`LINUX_LITMUS_END` 或退出码；它证明冒烟程序已经运行，不能计作 litmus 通过，也不能替代主运行的大规模矩阵。

四核上线后的[启动期 census](../build/qa/model08-post-online-census-20261003T215248Z.json)在实际 census tick `104273938000` / `104226269000` 均观察到 6/12 种 RN-F TX REQ：ReadNotSharedDirty、ReadUnique、MakeUnique、CleanInvalid、WriteBackFull、WriteEvictOrEvict，其中 CleanInvalid 各 4096 次。CleanShared、MakeInvalid、Evict、WriteCleanFull、ReadNoSnp、WriteNoSnpPtl 在该快照中仍为 0；HN-F 下游、RX 和其他通道已排除。periodic 计数只证明流量出现，完整测试、握手及最终覆盖验收仍未完成。最新运行观察见[实时快照](../build/linux-validation/model08-live-status.json)，该快照本身不执行验收判定。

目标路径保持 `4 × KMH CPU / classic L1 / ChiNativeL2 → 3×2 CHI NoC → 4 HN-F → 2 SN-F → RAM`。Linux RAM 和页表访问经过这条路径；UART/中断控制器等平台 MMIO 的路由另行明确记录。Linux 内核、固件和构建目录保存在 D 盘 WSL Linux 文件系统中。

Linux 基准配置将容量设置为每核 I/D L1 各32KiB、I/D walker cache各4KiB、原生L2 256KiB/32MSHR、每HN 64 entries/8192 SLC lines、RAM 1GiB。NoC和SAM保持原拓扑；这是Linux基准容量，区别于之前用于快速制造替换/重试的SE小缓存配置。两NUMA节点各512MiB，用于真实跨node迁移，物理CHI路由仍按SAM。

## 实施与历史运行记录

以下按实施顺序保留当时的状态和失败证据；当前运行身份及验收状态以上文 model08 记录和下方验收清单为准。

当前已完成官方源码校验、BusyBox/OpenSBI/Linux编译（包含rcutorture/locktorture模块）、RISC-V guest原生GCC/make文件系统安装及herdtools7构建。首次运行 `build/linux-validation/boot-01` 已进入Linux并识别NUMA；新版 `linux-smoke-02` 已实际打印`[0.076273] smp: Brought up 2 nodes, 4 CPUs`。仍须取得guest init中的sysfs拓扑和启动成功标记及正常退出，当前不能标为完整启动通过。SC升级失败语义的3项实际gem5组件测试通过；FS CBO权限及保留的Normal-NC RAM测试已编译，仍待真实guest验收。

`linux-smoke-02`进入SMP启动后曾较长时间没有新增串口。只读PC快照及与实际固件的局部代码字节比对确认：一个次核在执行Linux自带的非对齐复制速度测量，其余hart处于Linux/OpenSBI的WFI。该内核为HZ=250，word/byte两段各测2个jiffy，合计至少16ms客体时间，另有对齐等待和warmup。随后串口实际记录首个次核完成（0.002747→0.028084，25.337ms）。这解释了该段停顿，不能把无串口直接判为死锁；也不代表四核online。详情见`build/qa/smoke02-smp-readonly-diagnosis.md`。

官方diy7/litmus7的large矩阵为1936个命名测试、1908个唯一语义Hash、17家族，运行参数为 `-a 4 -s 128 -r 1`，预计259840个outcomes。herd7为每项生成允许状态；64个AMO/LRSC对象已核对真实指令。生成、编译及参考模型计算不等于guest执行通过。上游 `.aq.rl` 输出与GNU汇编器兼容问题按同义 `.aqrl` 修正，原输入和适配前后摘要保留。

新版与保留旧版运行相同四核SE CPU程序，结束tick均为21699000，drain均为22699000，配置及完整raw轨迹SHA逐字节相同。8936项非host统计中8项不同：CBO访问尺寸64B→1B修正使4个L1的bytesRecv减少315/63/63/63，及相应bytesRecvPerCycle变化；严格统计全相等检查因此保留为失败，不能写成“全部统计一致”。该比较仅是实现回归，不能替代Linux验收。

随后对保持相同CBO语义的纯主机trace优化单独做了开关两组回归：`trace-guard-regression-on.json` 与 `trace-guard-regression-off.json` 均通过。两组各8936项非host统计、配置、CPU结果和tick完全一致；开启时完整raw字节一致，关闭时端点计数字节一致。优化只使opcode查表静态化，并在trace关闭时跳过无用JSON构造。本轮Linux core及其后续kernel-build保持使用同一个已保存的`fs-model-02/gem5.opt`，避免合并不同执行文件的验收证据；新优化文件留作后续独立运行。

2026-10-04凌晨，`linux-suite-03`在tick `103161925000`触发`IssueQue::retryMem`的`!inst->isNonSpeculative()`断言，尚未到guest init。core进程已异常退出，后续kernel-build监督器据此拒绝启动；这是已知失败，不能算作单纯运行中。原始`run.log`与串口保留，汇总报告的`knownFailure=true`。该问题后续修复及回归见下文。独立`linux-smoke-02`当时继续运行，之后因宿主输出写入异常退出，详见后续记录。

该问题随后由真实FS/Sv39冷DTLB的`cbo.flush`独立复现。修复增加仅供已获ROB提交授权指令使用的翻译完成重试入口，普通重试断言保留。新`fs-model-05-nonspec`（SHA256 `4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d`）已通过三种合法CBO及缺页CBO的实际CPU测试：合法地址分别发出唯一的对应维护REQ，缺页保留正确PC/tval、精确StorePageFault且没有维护REQ；详见[nonspec-tlb-retry.md](nonspec-tlb-retry.md)。既有四核SE trace开/关回归各8936项非host统计、结果、配置、tick与协议记录仍精确相同。这些通过仅覆盖修复的定向路径，不能算完整Linux套件通过。

已启动`linux-suite-05`重跑完整Linux套件，固件、拓扑、缓存容量和1936项litmus等负载参数保持原样。监督器`kernel-stage-supervisor-05.json`会在core各阶段独立检查全部通过后，以同一model05启动`linux-kernel-build-06`；它不会把旧`linux-suite-03`的失败或其他模型结果合并为本次成功。此次总验收输出为`native-linux-validation-05.json`。

suite05随后实际打印`[0.076279] smp: Brought up 2 nodes, 4 CPUs`，并越过旧版`103161925000 tick`断言失败点，继续完成DMA pool、audit及cpuidle初始化。读取时同一model05进程仍活跃，最新进度已到`106000000000 tick`。旧版串口终止于`NET: Registered PF_NETLINK/PF_ROUTE protocol family`，原失败日志保留。这说明该次完整Linux重跑已越过此前失败的初始化阶段；尚未进入guest init并通过正式boot gate，不能据此标为Linux suite PASS。

已独立验证分支预测器的主机临时数据清理：`FullBTBPrediction::tageInfoForMgscs`仅供当次TAGE→MGSC通信，在`clearPreds()`中一并清空；不清除预测表或训练/恢复元数据。真实运行曾观察到当前1条分支结果对应5181条历史scratch项，详见`build/qa/native-btb-scratch-audit.md`。`bpu-scratch-regression-on.json`及`-off.json`均通过同一四核SE程序的精确回归（各8936项非host统计，tick/结果/配置，以及raw或counts全部字节相等）。容器微基准不能推算完整Linux加速或代替Linux验收；真实FS固定tick比较另行进行。

该性能修复的真实FS对照也已完成：两组使用同一Linux固件和配置，均在`5,000,000,000 tick`截止，9626项非host统计、198138个端点观察的完整census、1160字节串口、DTB、manifest及配置相同（`fs-bpu-equivalence.json`）。这只证明该窗口等价。初版检查器不能解码gem5二进制装载诊断中的非UTF8字节，修正后仅对`run.log`使用保留字节的`surrogateescape`；原冻结计划、检查器、失败报告均保留，重检报告显式记录检查器前后SHA，其他输入锁定不变。

对运行中的model05还做过一次30秒、99Hz的主机CPU采样，2951个样本无丢失，采样期间仿真继续推进；没有暂停CPU或修改模拟配置。该早期Linux初始化窗口中，router自身符号占38.665%，BPU占4.981%，只能用于定位该窗口的主机开销（`build/qa/fs-model05-host-profile.json`）。随后制作了可选model06：禁用多输出模式时跳过始终为空的队列扫描，读取方向时避免复制整个flit；信用、仲裁和事件调度保持原逻辑。默认模式trace开/关回归各8936项非host统计相同，多输出模式8939项统计及完整raw相同；同一5e9 tick真实FS窗口的9626项统计、census、串口、DTB和配置也全部相同（`fs-router-equivalence.json`）。

该单次FS配对耗时为326.438→317.280秒，约缩短2.8%，不足以推断完整套件收益。因此主验收继续运行已固定的model05，默认源码和执行文件均恢复model05；model06执行文件、可选安装器及回归证据保留，但不由`apply-gem5.py`默认启用（`router-optimization-decision.json`）。没有为此重启`linux-suite-05`，也没有更改冻结的负载或验收条件。

长运行使用可关闭的`CHI_FLOWGRAPH_COUNTS`轻量计数，记录成功接受的native端点TX/RX，包含重试尝试，不含router内部跳数或被拒绝发送。它只证明流量出现，不能代替握手、数据或测试结果检查。独立计数测试确认了拒绝不计数、TX/RX分开、重发累加和大于2^53的tick保真；新版SE的40000个端点观察、596个聚合key及各自first/last tick与完整raw轨迹全量一致。Linux覆盖只使用Linux本次记录，尚未取得完整的Linux详细握手轨迹。

suite05的实际启动期快照`build/qa/linux-suite05-census-52196331000.json`已通过端点计数结构检查，包含32100000次TX/RX观察。仅筛选RN-F TX REQ后，已出现ReadNotSharedDirty、ReadUnique、MakeUnique、WriteBackFull和WriteEvictOrEvict五类；另外七类计数仍为0，完整覆盖为false。CBO程序与Normal-NC程序提供后续触发路径，但WriteCleanFull、Evict还取决于执行时驻留/脏状态，必须等待最终实测。此处的`VALID`仅表示计数结构正确，不表示Linux测试或事务握手通过。

后续真实快照`build/qa/linux-suite05-census-144775351000.json`及同名`-result.json`记录38500000次TX/RX观察，通过结构检查。RN-F TX新增4096次CleanInvalid，当前为6/12类；CleanShared、MakeInvalid、Evict、WriteCleanFull、ReadNoSnp和WriteNoSnpPtl尚未出现。仍是启动期的periodic快照，不代替最终process-exit覆盖及guest测试验收。

为确认启动过程的实际执行进度，核对gem5 `init_signals.cc`后，对身份固定的suite05进程发送了一次仅导出统计的SIGUSR1，没有发送reset或exit信号。`build/qa/linux-suite05-stats-145354585000.txt`保存tick145354585000时的完整统计：simInsts=259374391，四核committedInsts分别为121047173、41435894、49839541、47051783；宿主耗时5744.40秒。信号记录为`build/qa/linux-suite05-stats-signal.json`。这些数据证明已有实际多核执行，不证明随后测试通过，也不能单靠累计统计排除之后的等待或故障。

约tick150e9的另一段30秒、99Hz主机采样记录在`build/qa/model05-boot-window-perf/fs-model05-host-profile.json`：2963个样本、无丢失，direct O3符号28.755%、direct分支预测21.431%、分配器库17.989%、direct router仅3.172%。平面采样不能把分配器开销归到特定调用者，也不能定位guest PC；它说明早期NUMA窗口的router占比不能直接外推到此启动阶段。静态核对发现router真正无工作时已经停止调度，而阻塞时仍需逐拍处理credit及唤醒顺序；未采用进一步跳过周期的改动。当前模型及工作负载继续运行。

长测资源核对另发现BPU统计历史随运行长度持续留存：两分钟RSS增加29008KiB，其中28808KiB来自宿主heap，guest RAM映射仅增加204KiB。源码确认当前四核每10000/100000提交指令追加的七个历史容器仅用于退出报告；这不是对全部堆增长原因的量化归因。记录见`build/qa/model05-host-memory-audit.md`及同名JSON。正在准备保留完整快照、原报告和统计顺序的有界内存落盘候选，尚未应用到运行中的模型；还需单独检查统计计数的整数范围。

为该资源修复增加的四核SE基准使用`native/gem5/native-bpu-phase-test.c`，每核跨多个阶段后执行原有RTL请求场景。原model05的trace-on运行`se-bpu-phase-original-on`真实宿主exit0、CPU exit tick385027000、含tail结束tick386027000；四核committedInsts为741635/745009/745574/728002，四核阶段校验及原CHI场景均PASS。它只提供候选修复的独立比较基准，完整Linux验收仍未完成。

原model05的trace-off基准也已正常退出，与trace-on的8957项非主机统计、端点计数、配置及旧14份报告一致。独立model07 oracle保留旧历史容器与原格式器，仅增加逐核完整导出；不可变二进制SHA为`3c76d5e2688e604e296640e826783db2a685ba1cf08708795af489c0a70bcc07`。其实际trace-on运行与model05的CPU结果、8957项统计、完整CHI原始文件及旧14份报告逐项相同，见`build/qa/bpu-phase-original-oracle-on.json`；原始轨迹SHA为`178c9ba4bca56f76dff3796590f96aed1cfda9eaf2e8f1aad837e34f5ba990d3`。model07自身trace on/off的四核完整记录、顺序和归档也通过比较，见`build/qa/bpu-phase-oracle-trace-invariance.json`。每核完成7个主阶段及72或74个子阶段，并保留实际未闭合尾段标记；这些结果仍仅是有限SE回归。

独立model08统计落盘候选已构建，不可变二进制SHA为`e71ec1143c2840394c4c63348edba86f3ff46eaab14ef6ad9f42e0ff2f521550`。安装先核对当前oracle三源及公共镜像SHA，仅恢复oracle统计源，再执行候选`--check`、预览、安装和再次检查；公共ITTAGE/commit报告镜像保持不变，原Linux主套件与随机补充进程继续使用不可变model05。首次链接被OOM终止，失败日志保留为`gem5-bpu-phase-storage-build-failed-link.log`；在WSL工作目录临时增加4GiB交换空间，并用`LINKFLAGS_EXTRA=-Wl,--no-keep-files-mapped,--no-map-whole-files`重试后真实exit0。重试后的进程检查当时仍可见三个原Linux PID；随后确认旧冒烟已发生写入异常并最终退出，不能将PID存活当作运行健康证据。临时交换空间已安全停用并删除，见`build/qa/bpu-phase-storage-link-swap-cleanup.json`。

实际回归驱动`build/linux-validation/run-phase-storage-regressions.py`已启动，要求显式候选SHA，并记录每个实际子进程退出码，顺序执行三方BPU on/off比较、候选trace开关对照、四项CBO回归、原短RTL场景on/off及5e9 tick真实FS对照；任一失败即停止，进度记录为`build/linux-validation/phase-storage-regression-driver/state.json`。该驱动不会启动完整Linux验收，也不把固定窗口正常结束判作Linux启动通过。

该候选回归串已全部实际完成，外层session92063真实exit0。BPU的trace-on/off三方比较和候选自身开关比较均PASS，8,957项非主机统计、旧14份报告及四核完整历史保持一致；实际CPU回归仅覆盖原有符号整数的定义范围，宽计数边界另由组件测试覆盖，不声称与原有有符号溢出等价。Clean/Flush/Inval/Flush-fault四项真实FS CBO回归及原短RTL场景on/off也均通过。真实FS对照固定5e9 tick，两边9,626项非主机统计、198,138次端点观察、1,160字节串口、DTB、配置和未完成结果全等，见`fs-phase-storage-equivalence.json`。单次宿主时间289.941→299.230秒不能证明提速；采用该修改的依据是移除随阶段数增长的内存历史并保留报告。累计每PC映射仍保留，其内存可能随不同PC数量增长。

完整准备与启动门禁重新读取上述实际证据及全部冻结输入，READY identity为`72d38377ee866d38f902651c34b0fcbce794e4099470bb90635db921b0a75c2d`。新运行正常推进后，于UTC 2026-10-03 20:45:33向旧suite05和random01的核对PID发送gem5支持的SIGINT，原因是采用已经回归的统计存储修复；不是观察超时，也不计作测试通过。旧结果、日志、计数和真实退出将完整保留，审计为`build/linux-validation/model05-supersession.json`。不把旧05与新08结果混合验收。

旧`linux-smoke-02`的真实外层session84923已返回1，最后进度tick160000000000，日志随后报告census临时文件写入失败。退出快照最终写成，但不能抵消非零退出；工具完成记录为`build/linux-validation/smoke02-exec-completion.json`，完整审计为`build/qa/linux-smoke02-census-write-failure.md`及同名JSON。宿主日志另有gem5经9p写入时的内核内存分配失败栈；两种PID namespace尚未关联，未把该栈直接归因于本次census或某次链接。新model08长测计划将六项输出统一置于`/root/chi-flowgraph-work/linux-validation/runs/model08/`，所在ext4.vhdx位于D盘，避免这部分高频输出经过`/mnt/d`挂载；仍需全部实际回归通过后重新生成READY计划才允许启动。旧05输入和结果保持原样。

完整core套件采用单次启动分阶段m5_exit：boot→6项smoke→large→torture→11项pthread/迁移测试→CBO→Normal-NC RAM。每次退出先用宿主独立检查器验证，再恢复同一guest；不清空cache或人为排空NoC。输入manifest、程序、CBO/NC模块和检查器按内容SHA冻结，guest超时或阶段未完成返回非零。内核原生make-j4是独立的4GiB SquashFS/overlay阶段，默认不重复core套件。

后续源码复核发现范围缺口：冻结core的`page_migration`真实迁移固定四页，并在迁移时让writer停在barrier；`alias_cache_pressure`使用每hart64KiB的顺序压力。它们有实际数据检查，但没有guest随机种子或随机选页，不能完成用户要求的随机页迁移。现有运行及冻结输入保留；新增独立`random-memory`补充程序使用64页/worker、3个并发版本化writer和1个随机迁移线程，每轮各选一页，共128轮/384次query→move→query，使用固定seed，并在停止writer后从CPU0逐字节复核全部页。每writer另有1MiB双别名压力区，初始化全扫描和迁移期实际随机工作集分别报告。该补充程序目前仅交叉编译完成，仍须同一model05上的真实Linux执行和独立检查。

补充固件使用独立构建/输出目录，保留原内核配置并更换initramfs，先在Linux内执行相同6项litmus冒烟，再运行随机迁移。**原`native-linux-validation-05.json`即使通过，也不能单独宣布全目标完成；随机迁移补充运行是新增的必需验收项。** 原core及kernel-build监督链不因此重启或修改。

独立固件已构建，`linux-random-memory-01`补充仿真已启动；当前仍待实际guest结果。固定输入、命令及证据边界见[native-linux-random-memory.md](native-linux-random-memory.md)。

`scripts/continue-native-linux-validation.py`可在本地串行衔接已经运行的core与后续kernel-build：先核对当前PID和启动身份，等待core真实退出并重验全部阶段，然后以固定SHA的模型和固件启动`linux-kernel-build-04`。它不覆盖已有运行、不自动重试失败、不把超时当成功；等待期间若配置、固件或检查器发生变化就拒绝启动。`--check-only`只校验准备条件，不代表已经执行来宾。最终总报告由`scripts/check-native-linux-validation.py`重新读取实际日志与配置产生，未完成仍为INCOMPLETE。

## 验收清单

| 项目 | 必须取得的证据 | 当前状态 |
|---|---|---|
| Linux 启动 | 真实 FS 启动日志、4 CPU online、实际配置含4个原生L2和NoC，进入guest init | model08 随机补充运行已取得 guest 四核/双 NUMA 标记和 BOOT_PASS；core 仍在内核初始化，待该运行的正式 boot gate |
| Linux litmus | guest内真实执行、多线程、禁止结果判定、退出码 | 补充运行冒烟期间触发 fetch.cc:783 断言，实际宿主 -6、外层 1，无 END，未通过；主运行仍待启动 |
| litmus7 / diy7 大规模生成 | 固定工具版本、生成参数、测试清单及模型允许结果、实际guest结果逐项匹配；不以手写测试代替 | 1936项已编译，待guest运行 |
| rcutorture | 本次内核模块、参数、实际运行量、终止后的成功报告，无错误/停滞 | 待运行 |
| locktorture | 本次内核模块、各锁类型参数、实际运行量、结束报告 | 待运行 |
| spinlock / mutex / futex | 4线程实际竞争、操作数和数据检查，无死锁/超时 | 已编译并冻结，待guest运行 |
| RMW / LL-SC | AMO及LR/SC反汇编、并发成功/失败尝试计数、最终数据一致 | SC组件测试通过；guest程序已编译，待运行 |
| producer-consumer | release/acquire下数据及序号一致，实际传递量 | 已编译并冻结，待guest运行 |
| false sharing | 同cache line不同字多核写入、实际转移及最终数值 | 已编译并冻结，待guest运行 |
| shared counter | 原子更新总次数与最终值精确一致 | 已编译并冻结，待guest运行 |
| random page migration / cache pressure | 真实页迁移前后node查询、成功迁移计数、随机种子及压力数据完整；单node不可冒充迁移成功 | 冻结 core 只含固定四页迁移；独立补充运行在前置 litmus 冒烟期间断言退出，随机迁移程序尚未运行，需修复后完整重跑 |
| 多核 memcpy + checksum | 各核并发复制、字节数、期望与实测校验及逐字节错误统计 | 已编译并冻结，待guest运行 |
| kernel build / make -j | guest原生编译器、完整构建命令及日志、并行度、最终内核产物及退出码 | 原生工具链/rootfs已准备，待guest运行 |
| CHI事务覆盖 | 12种RTL实际REQ及其必要握手分支逐项统计，保留观察窗口与缺口，不能以SE覆盖代替 | model08 两组启动期 RN-F TX REQ 均观察到 6/12 类；其余六类、握手和最终 process-exit 覆盖仍待验收 |

测试规模、参数、工具版本和每次失败都随结果记录。只有相应guest运行和独立检查器证据完整，才将项目改为通过。所有项目通过也只证明给定配置和测试范围，不能据此证明全部L2并发交错或RTL逐周期等价。

旧 suite05 已因采用回归后的 model08 而正常接收 SIGINT，实际 core 宿主退出码为 2、监督器退出码为 2、外层驱动退出码为 1，后续内核构建未启动；记录见 `model05-supersession.json` 和 `model05-driver-completion.json`。它不能计入 model08 成功结果。当前 model08 总验收还要求 core、random 和 kernel-build 的实际宿主退出码均为 0，以及真实外层会话 71505 的完成凭据；由[总检查器](../scripts/check-native-linux-complete-08.py)结合冻结启动计划重新核对，缺少任何必需阶段均保持未完成。

## 来源与准备

- Linux 固定 6.6.52，来源 kernel.org；OpenSBI 固定 v1.3.1，来源其官方仓库；BusyBox 固定1.36.1。下载和校验脚本：[prepare-native-linux-sources.sh](../scripts/prepare-native-linux-sources.sh)。
- [gem5官方OpenSBI说明](https://github.com/gem5/gem5-resources/blob/stable/src/riscv-fs/riscv-opensbi/README.md)提供固件与kernel装载参考；本项目以固定gem5源码实际接口为准。
- 内核测试采用上游 [RCU torture](https://docs.kernel.org/RCU/torture.html) 和 [lock torture](https://docs.kernel.org/locking/locktorture.html) 的模块及结束报告。
- 自动生成litmus采用官方 [herdtools7](https://github.com/herd/herdtools7)；RISC-V测试格式参考 [litmus-tests-riscv](https://github.com/litmus-tests/litmus-tests-riscv)。

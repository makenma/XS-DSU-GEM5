# H10 MIXED N4：NI W 长时间未分配 VC 的证据

## 结论与适用范围

本次未发现明确的 credit/free-state 错误或接收端漏唤醒。直接证据是现有两级 SA 与隐式 output-VC 分配下的长时间单 VC 饥饿，最终触发 NI 的连续分配失败阈值；这不等于已证明存在静态依赖环或永久死锁。

主实验保留原仲裁、50000 阈值、固定资源和原始 failed 记录。该点不进入有效吞吐排名；不引入新 VA policy，不根据失败结果改变模型假设。所有诊断已结束，未修改源码、测试、配置 SSOT 或二进制。

原始点：`.tmp/mesh-outstanding-experiment.TwGHeP/cases/A_H10_MIXED_1_1_N4/roi_probe`。精确身份见其 `measurement.json`、`execution.json`、`case.json`、`program/workload.json`；实际配置见 `raw/config.json`。这是 16 MiB/core、64 KiB tile 的 MIXED workload，不是先前的 8 MiB pilot。

原始执行在 tick `1043071000` 由 `NetworkInterface::calculateVC` 报出 `netifs24/vnet1` panic，return code `-6`，host time `360.2373665 s`。该失败发生于人为停止其他任务之前，不能归类为取消或超时。二进制 SHA256 为 `1e2a7072e72aa7ca0d5940b5801307c8b33e87e3bed2688bf543932621e2e58e`。

## 实现索引

- [NI VC 分配与计数](../../src/mem/ruby/network/garnet/NetworkInterface.cc:549)：counter 在成功分配时清零，失败调用累加；它不是依赖环检测器。
- [SA-I](../../src/mem/ruby/network/garnet/SwitchAllocator.cc:124)、[SA-II / RR 更新](../../src/mem/ruby/network/garnet/SwitchAllocator.cc:175)、[send_allowed](../../src/mem/ruby/network/garnet/SwitchAllocator.cc:291)。
- [配置 MessageBuffer ordering](../../configs/ruby/AXI_MESH.py:99)：本 case 的五路 ordered 均为 false，不能用 ordered-vnet 的年龄条件解释此次阻塞。
- [精确 Local endpoint 选路](../../src/mem/ruby/network/garnet/RoutingUnit.cc:167)：目标 router 相同时使用 routing table；重复 Local 方向不等于重复目标。
- [UID 编码](../../src/mem/axi/axi_initiator_adapter.cc:66)、[bridge AW/W/B 生命周期](../../src/dev/ai_mesh/axi_garnet_bridge.cc:103)。

## 两次实际复现

两次均复用原二进制、原 program、原 architecture 和所有资源/阈值，均到达同一 panic tick。

### 四点 snapshot

目录 `/tmp/mesh-n4-deadlock-diagnostic.xUUpwN`，命令：

```sh
build/AXI_MESH/gem5.opt --outdir=/tmp/mesh-n4-deadlock-diagnostic.xUUpwN/raw configs/example/ai_mesh/run_mesh_experiment.py --experiment-config /tmp/mesh-n4-deadlock-diagnostic.xUUpwN/case.json
```

诊断 case 仅设置独立 `output_dir` 和 `snapshot_ticks=[1000000000,1018000000,1020000000,1040000000]`。索引：`run.log`、`case.json`、`raw/config.json`、四份 `network_roi_<tick>.json`。shell 报退出134；原始 runner 的 signal return code 是 -6，两者对应同一 SIGABRT。

### GDB 与有限 trace

目录 `/tmp/mesh-n4-gdb.Hwozh1`。GDB 脚本 `inspect.gdb` 只读取 NI24、路径 router `24/23/22/21/20/15/10/5` 的 W head/outvc/credit 与 NI26 接收状态，不调用被调试模型的函数，不导出整个进程内存。脚本在 SIGABRT 后完成，证据见 `gdb.log` 中 `DIAG` 行。

实际启动 argv：

```sh
gdb -q --batch -x /tmp/mesh-n4-gdb.Hwozh1/inspect.gdb --args build/AXI_MESH/gem5.opt --outdir=/tmp/mesh-n4-gdb.Hwozh1/raw --debug-flags=RubyNetwork --debug-start=1040000000 --debug-end=1040500000 --debug-file=/tmp/mesh-n4-gdb.Hwozh1/router20_21.trace.gz --debug-ignore=system.ruby.network.routers0:system.ruby.network.routers1:system.ruby.network.routers2:system.ruby.network.routers3:system.ruby.network.routers4:system.ruby.network.routers5:system.ruby.network.routers6:system.ruby.network.routers7:system.ruby.network.routers8:system.ruby.network.routers9:system.ruby.network.routers10:system.ruby.network.routers11:system.ruby.network.routers12:system.ruby.network.routers13:system.ruby.network.routers14:system.ruby.network.routers15:system.ruby.network.routers16:system.ruby.network.routers17:system.ruby.network.routers18:system.ruby.network.routers19:system.ruby.network.routers22:system.ruby.network.routers23:system.ruby.network.routers24:system.ruby.network.netifs*:system.ruby.network.ext_links*:system.ruby.network.int_links* configs/example/ai_mesh/run_mesh_experiment.py --experiment-config /tmp/mesh-n4-gdb.Hwozh1/case.json
```

最初的沙箱启动在 ptrace 前退出，没有运行 gem5；随后同一命令经工具授权启动自己的本地子进程，未访问网络。GDB 返回0只表示诊断脚本完成，不能解释为 gem5 验收通过。

过滤的实际覆盖必须按产物描述：RubyNetwork 的部分 allocator 输出名是 `global`；[ObjectMatch](../../src/base/match.cc:76) 只支持整个路径分量为 `*`，不支持 `netifs*` 等分量内 glob；部分对象名还有前导0。因此文件不只含 router20/21，而是有限时间窗内的多 router/NI 输出。本分析从行内 router ID 选择 router22 及其下游，不把 CLI 的过滤意图当作实际覆盖。

trace 因 abort 没有完整 gzip 尾标记；只使用已完整解码的前缀记录。通过 `zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(...)` 可读取现存完整 deflate 块。已解码约83.45 MB，SA 完整记录35697条，记录 tick 范围 `1040000000..1040498500`，不宣称最后丢失字节范围的完整性，不补造缺失行。

## AXI 与 packet 等待证据

core24 从 tick1018000000 到1040000000：`AW=4233、W=67728、B=4229` 均不变，且 `4233×16=67728`；已接受 AW 所需 W 全部已被 master adapter 接受。同期 AR 从4302增至4480，R beats从68768增至71680。不是 DMA 完全停止调度。

四笔未完成写均到 HBM index1 / NI26 / router5，路径 `24→23→22→21→20→15→10→5`。下表 UID 根据 snapshot 中 AW 接受顺序与实现 UID 编码推导，不冒充 GDB 直接读取；bridge ordinal 与 AXI write ordinal 是不同编号空间。

| Bridge ordinal | 推导 TxnUid | AXI ID | 地址 | AW tick | 至 panic 的等待 μs |
| --- | --- | --- | --- | --- | --- |
| 8580 | 0x18000000001084 | 128 | 0x871210800 | 1010235501 | 32.835499 |
| 8582 | 0x18000000001086 | 136 | 0x871210c00 | 1010308001 | 32.762999 |
| 8583 | 0x18000000001087 | 134 | 0x871210e00 | 1010319501 | 32.751499 |
| 8584 | 0x18000000001088 | 137 | 0x871211000 | 1012172501 | 30.898499 |

GDB 直接读取的 packet：

| PacketId | 位置 | Enqueue tick | 至 panic 的等待 μs | 状态 |
| --- | --- | --- | --- | --- |
| 5486829 | router23 East/inport3 VC7 | 1010241000 | 32.830 | HEAD，outport2/West，outvc=-1，3 flits |
| 5515814 | router22 East/inport3 VC7 | 1017385000 | 25.686 | HEAD，outport2/West，outvc=-1，3 flits |

两包均 src NI24、dst NI26。未采到 stalled message 的 UID 字段，因此不将两包与上表某一 UID 强行一一绑定。

NI24 的四个 W output VC 均 ACTIVE、credit=5/depth8、NI flit queue为空；对应 router24 Local input VC 各有3 flits且 HEAD 未分配 outvc。这是受背压的已占用 VC，不是 credit=0，也未出现空 receiver 却遗失3个 credit 的证据。

NI26 在 panic 时 stall queue为空、W MessageBuffer为空，incoming link 仍有 src NI8 的 W BODY（packet5666037）。所有 snapshot 中 target W admission refusal为0，W MessageBuffer 历史高水位仅2。先前静态阅读提出的“最后一个 tail 的 NI 唤醒”候选不支持本故障。

## 实际仲裁记录

已解码短窗内，router22 West 共分配8个 W HEAD：7个来自 Local、1个来自 East VC4。East 同期获得649次 R flit grant、11次 B grant、3次 W grant；W 的3次全为 VC4 的同一 packet，VC5/6/7没有 grant。

以下为同 tick 的真实 grant 对，不是构造的调度示意：

| Tick | East 输入获 grant | Local 输入获 grant |
| --- | --- | --- |
| 1040104500 | R VC19，packet5649101/BODY，输出South | W VC5，packet5646479/HEAD，输出West/outvc4 |
| 1040359500 | R VC19，packet5650460/TAIL，输出South | W VC4，packet5648089/HEAD，输出West/outvc4 |
| 1040479000 | R VC19，packet5651180/HEAD，输出South | W VC4，packet5650614/HEAD，输出West/outvc5 |

这些 tick 上 West 确实能分配 W output VC，但 East 的 SA-I 选择服务了 R，而不是其持续等待的 W HEAD。单个 input 的 RR 指针由不同 vnet 的 SA-II grant共同更新，不能把该 RR 等同于每个 output/vnet 上持续 HEAD 的强公平分配。

下游仍推进：1020000000→1040000000，router21→20 的四个 W output VC 分别发送 `729/369/195/123` flits并收到相应返回 credit。router21 East VC7 在此区间曾无 dequeue，但 panic 时已换为 tick1041665500进入的 src NI23 packet5656499。固定 VC 编号不是 packet 身份，不能将这段无 dequeue 描述成该 VC 永久停止。

## 已确认与未证明

- 已确认：原失败和两次复现同 tick触发；源四笔写等待、沿途旧 HEAD 长期未分配、其他流量持续推进、部分可用 W 分配时机由同输入的 R 服务取代。
- 未发现：本故障中的明确 credit/free-state 泄漏、错误 Local 选路、ordered-vnet 限制、目标 W 容量拒绝或 NI26 漏唤醒。
- 未证明：永久饥饿、静态闭合依赖环、若不终止则永远无法完成、所有时段的唯一瓶颈归因。有限 trace 不能升级为无限运行的证明。
- 实验处理：这是当前模型和既定进展阈值下的真实 failed 点，不改为 valid/unconverged，也不隐去。继续原模型的有界 tested-set 搜索；更强 VC 公平策略只能属于另行声明的模型实验。

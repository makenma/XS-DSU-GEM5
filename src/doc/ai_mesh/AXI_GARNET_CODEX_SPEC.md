# AXI4 Transaction/Beat Transport over Garnet

## Codex 实现规格与验收合同（第一阶段）

> - 文档状态：Implementation Contract
> - 目标仓库：`makenma/XS-DSU-GEM5`
> - 基线分支：`xs-dev`
> - 基线提交：`7478835ac25d406941490578874d5022e3a46ec5`
> - 建议工作分支：`feature/ai-mesh-axi-garnet`
> - 阶段目标：先完成可验证、可配置、可复用的 AXI-over-Garnet model
> - 后续阶段：CPU Mesh / UCIe / NPU Mesh / Mesh IR / Dummy Core / Agent workload

---

## 0. 给 Codex 的硬性执行指令

本文件是实现合同，不是方向性建议。Codex 不得自行扩大功能范围。遇到设计歧义时，以本文件的支持范围、配置优先级、状态机、不变量和测试 oracle 为准；仍不能唯一决定时停止并询问用户，不得选择最容易让测试通过的实现。

### 0.1 开工前检查

首先阅读仓库内所有适用的 `AGENTS.md`。然后执行：

```bash
git status --short
git branch --show-current
git rev-parse HEAD
git log -1 --oneline
```

只有同时满足以下条件才能开始修改：

1. 仓库是 `makenma/XS-DSU-GEM5`；
2. `HEAD` 是 `7478835ac25d406941490578874d5022e3a46ec5`，或用户明确提供了新的基线；
3. 即将修改的文件没有用户未提交修改；
4. 基线构建和基线 Garnet smoke 能执行。

若 SHA 不一致，不得自行 `pull`、merge 或 rebase。若存在与本任务重叠的用户修改，停止并报告具体文件。无关脏文件必须原样保留。

检查通过且当前仍在 `xs-dev` 时，未来执行本规格的 coding Codex 必须创建本地工作分支：

```bash
git switch -c feature/ai-mesh-axi-garnet
```

若该分支已存在，不得删除、覆盖或强行重建；先报告其 SHA、相对基线的 commits 和工作树状态，再由用户决定继续使用还是改名。本规格生成阶段本身不创建分支。

### 0.2 Git 与文件安全

- 可以创建本地分支和本地 commit；
- 未经用户明确授权，不得 push、force-push、创建 PR 或修改远端分支；
- 禁止 `git reset --hard`、`git clean -fd`、`git checkout -- <file>`；
- 禁止批量格式化整个仓库；
- 不提交 `build/`、`m5out/`、测试日志、trace、临时 JSON 或生成的二进制；
- 不修改现有 `src/mem/cache/CHI/` 和 `src/mem/ruby/protocol/chi/`；若发现必须修改 CHI 才能实现，应视为架构假设失败并停止。

### 0.3 “通过”的声明规则

- 本次实际执行、退出码为 0、无 skip/xfail/timeout、结果 JSON 完整的测试，才可以写“通过”；
- 只编译成功只能写“构建通过”；
- 未执行的测试必须写“未运行”；
- 环境阻塞必须写“未验证”；
- quick suite 不能代表 full suite；一个 seed 不能代表 stress；
- 任一 mandatory case 未通过，最终状态只能是“部分实现/未完成”；
- 不允许只 grep 一行 `PASS`；必须解析机器可读结果并检查守恒及排空条件。

---

## 1. 本阶段范围

### 1.1 必须完成

1. 独立 `AXI_MESH` Ruby/Garnet build target；
2. AW/W/B/AR/R 五通道到五个 Garnet vnet 的固定映射；
3. Garnet 逐 vnet 类型和逐 vnet VC buffer 深度；
4. protocol-neutral 动态 `wireBytes` packetization；
5. 有界 AXI initiator/target adapter；
6. AW/W pairing、network-side orphan W、burst/beat/LAST 检查；
7. 同 ID ordering、不同 ID 合法乱序；
8. WSTRB、OKAY/SLVERR/DECERR、简单 byte-addressable target memory；
9. 有限 channel FIFO、MessageBuffer、outstanding table、ROB、assembly/orphan table；
10. 可重放的确定性 trace tester；
11. 单元、集成、压力、负向和 legacy regression；
12. 机器可读测试结果、关键 stats、显式 quiescence 判定。

### 1.2 明确不做

- CPU Mesh 与 NPU Mesh 双 fabric；
- UCIe tunnel；
- DMA engine、HBM/DRAM timing model；
- Mesh IR compiler、Dummy Core；
- Agent/HostServicePool、KV cache、continuous batching、MoE workload；
- pin-level AXI `VALID/READY` 波形；
- ACE/CHI/coherence；
- AXI5 atomic/ATOP；
- Router QoS 仲裁改造；本阶段只携带/统计 `AxQOS`，不修改 SwitchAllocator；
- adaptive routing、escape VC；规则 mesh 使用 XY；
- checkpoint/restore 可在下一阶段实现，但本阶段必须支持正常 drain。

### 1.3 不得声称的能力

交付物不得写“完整支持 AXI4”或“AXI4 compliant”。只能写：

> 实现并通过本规格列出的 transaction/beat-level AXI4 memory-mapped 子集。

---

## 2. 建模边界和总体架构

### 2.1 抽象层级

模型不是 AXI crossbar RTL，也不逐周期传递五组 `VALID/READY` 引脚。一次本地 adapter 调用返回 `true` 表示该 adapter clock edge 完成一次抽象 handshake；返回 `false` 表示本地容量不足，需要 producer 保持字段不变并稍后重试。

```text
AXI transaction
  -> bounded local channel FIFO
  -> AXI initiator adapter
  -> AXI_MESH Ruby MessageBuffer
  -> Garnet NI / packet / flit / VC / credit / Router
  -> AXI_MESH Ruby MessageBuffer
  -> AXI target adapter / ordering gate / memory service
  -> B or R response through Garnet
  -> initiator ROB / beat reassembly
  -> completion callback
```

每个跨 SimObject 的动作至少延迟一个 clock edge。禁止同 tick 完成“接收请求 -> 目标处理 -> 返回 response”的零延迟组合链。

### 2.2 Router 必须保持协议无关

Garnet 仅处理：

```text
route, vnet, VC, flit, credit, packet length, generic stats
```

以下字段只能由 endpoint/adapter 处理：

```text
AWID/ARID, AxLEN, AxSIZE, AxBURST, WSTRB, WLAST, RLAST,
BRESP/RRESP, txnUid, targetSeq, responseSeq, writeOrdinal
```

禁止在 `Router`、`InputUnit`、`OutputUnit`、`SwitchAllocator` 中 include AXI message header 或按 AW/W/B/AR/R 分支。`InputUnit` 可以读取通用的 vnet buffer capacity 来做容量断言，但不能知道该 vnet 对应哪一个 AXI channel。

### 2.3 SLICC 与 C++ 职责

采用以下固定拆分，避免修改 SLICC generator，也禁止 SLICC 与 C++ 同时拥有同一动作：

- `AXI_MESH` SLICC controller 是薄网络 shim：注册 Ruby network `MessageBuffer`，并把 incoming network message 转发到有界、非 network 的 local-delivery `MessageBuffer`；它不维护 AXI 状态表，也不构造 outgoing message；
- `AxiInitiatorAdapter` 和 `AxiTargetAdapter` 是 C++ `ClockedObject`，是 outgoing `AxiMeshMsg` 的唯一构造者/enqueue 方，并实现 pairing、ordering、ROB、assembly、memory service、backpressure 与 stats；
- initiator controller 只有 AW/W/AR 三个 to-network buffer 和 B/R 两个 from-network buffer；target controller 方向相反，只有 AW/W/AR 三个 from-network buffer 和 B/R 两个 to-network buffer；不得给每个 controller 重复注册十个无用 queue；
- outgoing network buffer 由 SLICC controller 向 Ruby Network 注册，同时由 Python 作为 adapter 的 `MessageBuffer*` 参数传入；该 buffer 的 producer 只有对应 C++ adapter，consumer 只有 Ruby/Garnet；
- C++ adapter 只有在 outgoing network buffer `areNSlotsAvailable(1, enqueueTick)` 为真时才 enqueue；否则 message 保留在对应有界 endpoint/channel FIFO，不能调用会越过容量的 enqueue；
- 每个 incoming channel 独占一个有限 local-delivery buffer。incoming network buffer 的唯一 Consumer 是 SLICC controller，local-delivery buffer 的唯一 Consumer 是对应 C++ adapter；adapter 不得轮询或注册为 network buffer 的第二 Consumer；
- SLICC 只有在对应 local-delivery buffer 至少有 1 个 slot 时，才把队头 message enqueue 到 local delivery 并 dequeue network buffer。local delivery enqueue 至少延迟 1 个 controller cycle；满时 message 保留在 network buffer，逐级形成 NI/Garnet backpressure；
- adapter 从 local-delivery buffer dequeue 后，只进入自身对应 channel 的有界 ingress FIFO并在下一 adapter clock edge处理。五个 channel 不共享 handoff/ingress FIFO，不得先 dequeue 后 drop，也不得自动扩容；
- 此方案不向 SLICC machine 注入自定义 `Axi*Adapter*` 参数，不修改 `src/mem/slicc/symbols/StateMachine.py`。若实现者选择自定义 C++ `AbstractController` 替代，必须在 Commit 1 gate 停止并先提交设计变更给用户，不能边实现边混用两种方案；
- C++ adapter 可以 include 生成的 `AxiMeshMsg.hh`；Garnet 通用目录不可以；
- Commit 1 queue-ownership probe 使用两个彼此独立、只到 local-delivery 为止的 raw shim message：一个 request-direction AW-like message和一个 response-direction B-like message。它们不进入 AXI state machine/checker，不宣称构成合法 write transaction；只验证两方向的 adapter enqueue、NI、远端 SLICC 唯一消费、local-delivery 满时不 dequeue、至少 1-cycle 投递和 queue drain。合法 AW/W/B 语义从 I0 开始验证。若 ownership/wakeup 合同无法成立，立即按第 17 节停止。

### 2.4 五个 vnet

| vnet | AXI channel | Garnet class | packet 粒度 | 默认每 VC 深度 |
|---:|---|---|---|---:|
| 0 | AW | ctrl | 1 AW = 1 packet | 4 flits |
| 1 | W | data | 1 W beat = 1 packet | 8 flits |
| 2 | B | ctrl | 1 B = 1 packet | 4 flits |
| 3 | AR | ctrl | 1 AR = 1 packet | 4 flits |
| 4 | R | data | 1 R beat = 1 packet | 8 flits |

五个 vnet 在 Garnet 中全部 `ordered=false`。AXI ordering 必须由 adapter 恢复，不得使用 ordered vnet 掩盖排序错误。

### 2.5 Ruby 构造与 Mesh endpoint 映射

本阶段复用 `configs/ruby/Ruby.py::create_system()`，但新增专用 protocol factory 和 topology；不得把 AXI controller 冒充 CPU cache、directory 或 DMA controller：

1. `configs/example/axi_garnet_test.py` 创建无真实 CPU 的 `System`，显式以 `cpus=[]` 调用 `Ruby.create_system()`；
2. `configs/ruby/AXI_MESH.py::create_system(...)` 创建 initiator/target SLICC controllers、对应 adapters 和 tester，返回 `([], [], topology)`；第一项没有 CPU sequencer，第二项没有真实 directory/memory controller；target 使用第 8.1 节的内部 simple memory；
   该 factory 在 topology/network init 前把 `ruby.network.number_of_virtual_networks` 设为 5，并核对五个 queue 的 vnet/class/ordered 属性；
3. 新增 `configs/topologies/AxiMeshDie.py`，复用 `Mesh_XY` 的 router/internal-link 生成方式，但 external link 完全按 scenario 中的显式 `controller -> router_id` 映射创建，不使用 stock `Mesh_XY` 的“controller 数必须整除 router 数，remainder 必须为 DMA”规则；
4. `--axi-mesh-routers` 和 `--mesh-rows` 决定 router 数与行数。由于现有公共配置把 `options.num_cpus` 当 router 数，test config 在调用 Ruby 前令 `options.num_cpus = options.axi_mesh_routers`，并在文档中说明这里不是 CPU 数；
5. 2×2 smoke 固定 initiator controller 接 router 0、target controller 接 router 3；4 initiator + 4 target 的 4×4 stress 使用 manifest 给出的八个 router ID，不能依赖 controller list 顺序隐式分配；
6. 两类 SLICC machine 各有独立、连续 `version`。逻辑 `dstNode` 通过只读 endpoint map 转为 target `MachineID/NetDest`；每个 `(srcNode,srcPort)` 也必须唯一映射到一个 initiator `MachineID`。AW/W/AR 的 `Source` 是该 initiator MachineID，`Destination` 是目标 NetDest；target context 保存 request 的 `Source`，B/R 的 `Source` 改为 target MachineID、`Destination` 必须精确复制为原 request `Source`，不能重新按 controller list 顺序猜返程 endpoint。`srcNode/srcPort/dstNode` 只保留作 protocol/checker metadata；
7. topology 初始化前检查 router ID 唯一性/范围、rows×columns、每个 controller 恰好一条 external link、每个 target logical node 恰好映射一个 MachineID、每个 `(srcNode,srcPort)` 恰好映射一个 initiator MachineID，并验证所有 request/response endpoint map 可逆且无歧义。

不得让 `Ruby.setup_memory_controllers()` 创建本阶段未使用的 DRAM controller；返回空 `dir_cntrls` 后必须用 instantiate smoke 证明公共 Ruby 流程能接受该配置。若 fork 的公共流程强制至少一个 directory，按第 17 节停止并报告，不得悄悄增加假 directory 改变测试拓扑。

---

## 3. AXI4 MVP 功能合同

### 3.1 支持矩阵

| 特性 | 本阶段行为 |
|---|---|
| AXI4 memory-mapped | 支持 |
| Address width | 64-bit |
| AXI ID width | 启动时配置，1–16 bit |
| Data width | 启动时配置，64/128/256/512 bit |
| AW/W/B/AR/R | 支持 |
| Burst | 仅 `INCR` |
| Burst length | 1–256 beats，`AxLEN = beatCount - 1` |
| Narrow transfer | 支持自然对齐，`beatBytes <= dataBusBytes` |
| Unaligned transfer | AXI 合法但本阶段不实现，完整 drain 后返回 `DECERR` |
| 4 KiB crossing | 不支持，strict 模式 fatal |
| WSTRB | 支持，每一位对应一个 byte lane |
| WLAST/RLAST | 支持并严格检查 |
| Multiple outstanding | 支持，容量可配置 |
| Same-ID ordering | 支持 target architectural-commit 与 source-retire ordering |
| Different-ID reorder | 支持且必须由测试证明发生过 |
| BRESP/RRESP | `OKAY/SLVERR/DECERR` |
| `AxQOS` | 携带和统计，不影响 Router 仲裁 |
| CACHE/PROT | 仅 trace metadata，不实现 cache/permission 语义 |
| FIXED/WRAP | 合法但未实现，返回 `DECERR` |
| Exclusive/LOCK | 返回 `DECERR` |
| REGION | 只允许 0，否则 `DECERR` |
| USER | 本阶段宽度必须为 0 |
| EXOKAY | 不生成 |
| ACE/CHI snoop | 不支持 |
| AXI5 ATOP/atomic | 不支持 |

### 3.2 错误分类

协议错误在本阶段唯一支持的 `strict_protocol=true` 模式下必须 fatal：

- WLAST 与 AWLEN 不匹配；
- 重复 AW、B、R beat 或 W beat；
- beat index 缺失、重复或越界；
- 相同 `txnUid` 的关键字段冲突；
- 不合法 SIZE 或跨 4 KiB；
- 未知 response `txnUid`；
- counter 即将回绕。

workload 已停止且 `finishAndCheckQuiescence()` 仍发现未配对 AW/W 或 partial burst 时，不走即时 `fatal_runtime`：它必须进入可诊断的 `final_consistency_failure`，保留 residual state 后让 config 以退出码 2 结束。该分类仍表示 strict contract 失败，不能返回 AXI response，也不能被当成 watchdog/deadlock；其 marker 与结果 schema 见第 12.1、12.2 和 N10。

合法但未实现的 AXI feature（包括 unaligned）返回 `DECERR`；地址 decode miss 返回 `DECERR`；目标 memory/service fault 返回 `SLVERR`；queue full 只产生 backpressure，绝不能返回错误 response。`tryAccept*()` API 无法观察 producer 在返回 false 之后是否持续驱动 VALID，因此“字段保持稳定”是 tester/producer 合同，不是 DUT 可执行的 fatal checker。

本阶段不定义 permissive recovery。`strict_protocol=false` 或 CLI/config 请求关闭 strict 时必须在 instantiate 前以稳定错误 `AXI_MESH supports strict_protocol=true only` 失败，不能静默切换错误策略。

architected response 优先级：

```text
DECERR > SLVERR > OKAY
```

strict fatal 不属于 response 优先级；一旦命中已支持语义中的 protocol/内部不变量错误，立即终止且不生成 response。

Read 即使出错也必须返回完整 `ARLEN+1` 个 R beat，并只在最后 beat 设置 RLAST。Write 即使目标出错，也必须 drain 完该 burst 的全部 W beat，再返回恰好一个 B。
DECERR/SLVERR read 的整个 `functionalData` 固定填 0，`payloadDigest` 按同一 zero bus word 计算；不得返回未初始化 DataBlock。

### 3.3 burst 合法性

```text
beatCount = AxLEN + 1
beatBytes = 1 << AxSIZE
```

检查必须按以下短路顺序执行：

```text
1. beatCount/size/shift 可表示且 1 <= beatCount <= 256、1 <= beatBytes <= dataBusBytes；否则 fatal。
2. burst != INCR：直接选择 DECERR responder，只保留 beatCount 用于 drain/response；
   不得套用 INCR span、4 KiB 或 last-address 公式，也不声称检查 FIXED/WRAP 子规则。
3. burst == INCR：checked 计算 span/last address；溢出或跨 4 KiB 为 fatal。
4. INCR 但 address % beatBytes != 0：选择 DECERR responder。
5. LOCK、非零 REGION 或其他可表达但未实现 feature：选择 DECERR responder。
6. 只有仍为 OKAY candidate 时才做 normal target range decode。
```

对 INCR 的所有 shift/multiply/add 使用 checked arithmetic；`1 << size`、`beatCount*beatBytes` 或最后一个 beat address 溢出 64-bit 时 strict fatal，不能依赖 C++ wrap。FIXED/WRAP 只执行步骤 1 后短路到 DECERR，禁止因错误使用 INCR 公式而误 fatal。

支持的 INCR path 第 `i` 个 beat 地址：

```text
beatAddress(i) = baseAddress + i * beatBytes
```

自然对齐是本阶段的 OKAY fast path；若 `address % beatBytes != 0`，走 DECERR responder，不能当作 AXI protocol fatal。

字节序固定为 little-endian。对于每个 beat：

```text
busBase  = floor(beatAddress / dataBusBytes) * dataBusBytes
laneBase = beatAddress - busBase
legal lanes = [laneBase, laneBase + beatBytes)
WSTRB bit j addresses byte (busBase + j)
functionalData[j] is the byte carried on bus lane j
```

自然对齐与 `beatBytes <= dataBusBytes` 保证合法 lane 不跨当前 bus word。OKAY write 中合法 lane 外 `WSTRB=1` 是 protocol error，在 AW 与该 W beat 信息都已知的最早 adapter edge fatal（W-first 时可能是后续 AW binding edge）；合法 lane 内 `WSTRB=0` 表示该 byte 不更新。Read 在整个 `dataBusBytes` 的 `functionalData` 中只保证合法 lane 有定义，其他 lane 固定填 0，以便 hash 可复现。FULL_TIMING 下 W/R packet 按整个 data bus 宽度计入 wire traffic，`semanticBytes` 与 `wireBytes` 必须分开。

---

## 4. 标识、消息和不可变字段

### 4.1 三类 ID 不能混用

```cpp
struct AxiInternalId
{
    uint64_t txnUid;       // fabric 内唯一 transaction
    uint64_t writeOrdinal; // 第 N 个 AW 与第 N 个 W burst 配对
    uint64_t targetSeq;    // 同 target、同 ID、同 direction 的 commit 顺序
    uint64_t responseSeq;  // 同 source、同 ID、同 direction 的交付顺序
};
```

- `axiId` 是外部 AXI ID；
- `txnUid` 是全 fabric 唯一 64-bit 值，固定编码为 `srcNode[63:48] | srcPort[47:40] | direction[39] | localCounter[38:0]`；`direction=0` 表示 write、`direction=1` 表示 read，每个 `(srcNode,srcPort,direction)` counter 在 reset 后从 0 开始；对应 AW/AR acceptance 把当前值写入 UID 后再加 1，因此第一个 write/read UID 的 `localCounter` 都是 0，独立 AW/AR backpressure 不改变另一 direction 的 UID；
- AW 或 AR 的本地 `tryAccept*=true` edge 是唯一 sequence allocation point：先完成 validator/address decode，再在该 edge 分配 `txnUid`，按 `(srcNode,srcPort,axiId,direction,dstNode)` 分配 `targetSeq`，并按 `(srcNode,srcPort,axiId,direction)` 分配 `responseSeq`；
- 每个 `targetSeq/responseSeq` key 的 counter 同样 reset 为 0，并采用“分配当前值、随后加 1”；workload golden 因而可在运行 DUT 前独立预计算三种 sequence；
- sequence 在 target quota 不足、network injection 延迟、ejection 或 service reorder 前已经冻结。target 和 Garnet 不得重新分配/改写；issue order在本文中就是本地 AW/AR acceptance order；
- W-first 只有 `writeOrdinal`，直到匹配 AW acceptance 才取得该 AW 的 UID/destination/sequences；
- read 和 write 使用独立 ordering domain；
- W 没有 AXI WID，`writeOrdinal/txnUid` 是 adapter/fabric 内部元数据，不能声称为 AXI wire field；
- 任一计数器即将回绕时 fatal，不允许复用仍可能 outstanding 的编号。

instantiate 时要求 `srcNode < 2^16`、`srcPort < 2^8`；任一 direction counter 到达 `2^39` 前 fatal。workload plan 可按每个 source/direction 的地址请求顺序预计算 expected UID，actual acceptance 必须核对。

### 4.2 建议 C++ 类型

```cpp
enum class AxiChannel : uint8_t { AW, W, B, AR, R };
enum class AxiBurst : uint8_t { Fixed = 0, Incr = 1, Wrap = 2 };
enum class AxiResp : uint8_t { Okay = 0, ExOkay = 1, SlvErr = 2, DecErr = 3 };

struct AxiCommonMeta
{
    uint64_t txnUid;
    uint64_t targetSeq;
    uint64_t responseSeq;
    uint32_t srcNode;
    uint16_t srcPort;
    uint32_t dstNode;
    uint32_t axiId;
    uint32_t semanticBytes;
    uint32_t wireBytes;
    uint8_t qos;
    Tick acceptedTick;
};

// Local AW/AR boundary payload: only architected AXI fields.
struct AxiAddressRequest
{
    uint32_t axiId;
    uint64_t address;
    uint16_t beatCount;
    uint8_t size;
    AxiBurst burst;
    uint8_t lock;
    uint8_t cache;
    uint8_t prot;
    uint8_t region;
    uint8_t qos;
};

// Local W boundary payload: AXI4 has neither WID nor beatIndex.
// payloadDigest/functionalData are model representations of WDATA, not IDs.
struct AxiWBeat
{
    bool last;
    uint64_t byteStrobe;
    uint64_t payloadDigest;
    std::vector<uint8_t> functionalData;
};

// Local B/R boundary payloads. Internal UID/index stay in trace/state;
// payloadDigest/functionalData are model representations of RDATA.
struct AxiBBeat
{
    uint32_t axiId;
    AxiResp resp;
};

struct AxiRBeat
{
    uint32_t axiId;
    bool last;
    AxiResp resp;
    uint64_t payloadDigest;
    std::vector<uint8_t> functionalData;
};

// Internal transport envelopes, constructed only by adapters.
struct AxiAddressPacket
{
    AxiCommonMeta meta;
    AxiAddressRequest request;
    uint64_t writeOrdinal; // meaningful only for AW
};

struct AxiDataPacket
{
    AxiCommonMeta meta;
    uint64_t writeOrdinal;
    uint16_t beatIndex;
    uint16_t beatCount;
    bool last;
    uint64_t byteStrobe;
    AxiResp resp;
    uint64_t payloadDigest;
    std::vector<uint8_t> functionalData;
};

struct AxiBPacket
{
    AxiCommonMeta meta;
    AxiResp resp;
};
```

本地 AW/AR/W/B/R API 只出现 AXI channel 语义：`beatCount` 是 `AxLEN+1` 的规范化表示，`functionalData/payloadDigest` 是 WDATA/RDATA 的两种模型表示，不是额外 wire field。内部 `txnUid/writeOrdinal/targetSeq/responseSeq/beatIndex/beatCount transport copy` 只能由 adapter 生成，并只出现在内部 packet、状态表和 trace。`functionalData` 长度固定为 `dataBusBytes`，使用第 3.3 节的 bus-lane 映射。

`functionalData` 只在 functional correctness 测试或小规模 FULL_TIMING 中保存。traffic-only 模式可只保存稳定的 `payloadDigest` 和长度，但 target 不得通过共享指针直接读取 source 的完整 payload；数据必须随每个 W/R beat message 传输或由独立 checker 的 shadow plan 重建。所有 mandatory byte-level memory/WSTRB 测试必须启用 `functionalData`，traffic-only 模式不得声称验证了 byte memory correctness。

### 4.3 SLICC `AxiMeshMsg`

`AxiMeshMsg` 至少包含：

```text
Channel, Source, Destination, SrcNode, SrcPort, DstNode,
TxnUid, AxiId, TargetSeq, ResponseSeq, WriteOrdinal,
Address, BeatIndex, BeatCount, Size, Burst,
Lock, Cache, Prot, Region, ByteStrobe, Last, Resp, Qos,
SemanticBytes, WireSizeBytes, PayloadDigest,
DataBlk, MessageSize
```

message 注入后以上字段不可修改。SLICC 字段命名为 `WireSizeBytes`，生成通用 getter；C++ adapter 内部可以继续使用 `wireBytes`。它是模型中的 wire size，不使用 `sizeof(AxiMeshMsg)`。

`Source/Destination` 是 Ruby/Garnet 路由使用的真实 `MachineID/NetDest`；`SrcNode/SrcPort/DstNode` 是稳定的逻辑 endpoint metadata。target 必须从 AW/AR 保存两套信息，返回 B/R 时按第 2.5 节交换真实路由端点，但保持逻辑 origin/target metadata 不变。checker 必须同时验证二者与只读 endpoint map 一致。

---

## 5. AW/W、read 与 ordering 状态机

### 5.1 Source-side AW/W pairing

每个 source port：

1. `nextAwOrdinal` 和 `nextWBurstOrdinal` reset 为 0；AW acceptance 分配当前 `nextAwOrdinal` 后递增，所以第一个 AW 的 `writeOrdinal=0`；
2. reset 后或前一个 WLAST 后接受的第一个 W beat 立即开启新 W burst，分配当前 `nextWBurstOrdinal` 后递增，所以第一个 W burst 的 `writeOrdinal=0`；WLAST 只关闭当前 burst，不负责首次分配 ordinal；
3. adapter 自行从 0 递增当前 W burst 的 `beatIndex`。第 N 个 AW 只可与第 N 个 W burst 配对；
4. AXI4 W 没有 WID 或 burst-start 标记，因此 DUT 只按 WLAST 把单一 W stream 分段，不声称能识别 producer“本想交织”两个 burst；
5. W 可以先于 AW 被本地 adapter 接受并进入 bounded pre-AW beat buffer；AW 和 W 使用独立 admission resource，W buffer 满不能阻止匹配 AW 被接受；
6. 未配对 W 没有 destination，禁止注入 Garnet；
7. AW 到达后，即使当前 W burst 尚未收到 WLAST，也立即绑定 ordinal，并把 `txnUid/dstNode/axiId/beatCount/ordering seq` 复制到已缓冲及后续内部 W packet；已缓冲 beat 可释放，后续 W 可流式注入；
8. 绑定时若已观察 beat 数大于 AW beatCount、已观察 WLAST 位置不符，或以后 WLAST 与 AWLEN 不符，在对应 handshake edge fatal；
9. 配对后 AW 与 W 独立注入各自 vnet，所以 W packet 仍可能先于 AW packet 到达 target。

这条流式绑定规则是有界性的硬要求。例如 `source_pre_aw_beats=4`、16-beat W-first burst：接受前 4 个 W 后 WREADY 拉低；匹配 AW 到达后必须绑定并释放已缓冲 beat，随后其余 12 个 W 和 WLAST 可以继续，最终正常完成。禁止要求先缓存到 WLAST 才能匹配。

Source write 生命周期：

```text
EMPTY -> AW_ONLY or W_ONLY -> BOUND -> AW/W INJECTING
      -> WAIT_B -> B_ROB_WAIT -> B HANDSHAKE -> COMPLETE
```

AW admission 必须预留 outstanding metadata 和 B ROB entry。Write completion 定义为 B 被本地 consumer 接受，不是 WLAST 接受、W 注入或 target commit。

### 5.2 Target-side orphan W

Target 维护有界：

```cpp
std::map<TxnUid, TargetWriteContext> writeContexts;
```

规则：

- `AW_ONLY`、`W_ONLY`、`BOUND/ASSEMBLING` 共用同一个 `target_write_contexts` transaction-slot pool；`orphanWrites` 只是 `writeContexts` 中 `W_ONLY` entry 的索引/计数，不是第二套容量；
- W 先到：占一个 write context slot 并建立 `W_ONLY`；AW 先到：占一个 slot 并建立 `AW_ONLY`；
- 后到的匹配 packet 合并进既有 context；
- `W_ONLY -> BOUND` 或 `AW_ONLY -> BOUND` 原地转换，不得再次申请 transaction slot、assembly beat slot 或 response obligation；
- 第一个 AW/W packet 已携带 `beatCount`；创建 context 时同时消费该 transaction 预留的 `beatCount` 个 `target_write_assembly_beats` 和一个 B obligation。资源由第 6.3 节的 source/target quota 在注入前保证，因此 target 不得出现“已合法注入但无 context/beat/response slot”；
- `target_orphan_w_transactions` 与 `target_orphan_w_beats` 是上述共同 pool 的 W_ONLY 子配额。子配额满时只阻塞新的 unmatched W ejection，不 drop、不扩容、不返回 SLVERR；AW 使用独立 incoming channel，仍可到达并合并已有 W_ONLY；
- 重复 AW、重复 beat、同 UID 字段不一致必须 fatal；
- target 接受并复制 packet 后立即释放 Garnet VC，不得持有 input VC 等待 B。

B 只能在以下条件全部成立后产生：

```text
AW received
exactly AWLEN+1 W beats received
beat index contiguous and WLAST valid
same-ID targetSeq is architectural-commit eligible
outcome finalized; OKAY has committed all selected bytes, error has drained all beats
the context-owned B obligation can move to the independent bounded B-ready path
```

### 5.3 Read

AR admission 时必须预留：

- read outstanding entry；
- response sequence entry；
- 至少 `ARLEN+1` 个 R reassembly/ROB beat slots，或使用可证明不会丢 response 的等价 reservation。

Target 按 target ordering gate 执行 read，每个 R beat 单独形成 packet。Source 按 `txnUid + beatIndex` 重组，一个 burst 内只按 beatIndex 连续交付；同 ID 的 younger burst 即使全部到齐，也必须等待 older burst 的 RLAST handshake。

Read completion 定义为最后一个 R beat 的本地 R handshake。

### 5.4 三级 ordering

Target architectural-commit ordering key：

```text
(srcNode, srcPort, axiId, direction, dstNode)
```

Source response ordering key：

```text
(srcNode, srcPort, axiId, direction)
```

必须区分三个时刻，字段和 trace event 名不得混用：

1. `serviceReady`：target latency/model 已算完；不同或相同 ID 都允许乱序 ready；
2. `architecturalCommit/responseEligible`：按 target key 的 `targetSeq` 递增；same-ID write 只有在此 gate 才能更新 byte memory，read 在此 gate 冻结将返回的数据/错误并进入 R-ready path；
3. `sourceRetire`：按 source key 的 `responseSeq` 递增；B handshake 或一个 read burst 的 RLAST handshake 才完成事务。

必须同时保证：

- same-ID write 对 target memory 的副作用按 issue 顺序发生，不能只把倒序 B 放进 ROB；
- same-ID read 可以乱序 `serviceReady`，但按 targetSeq 依次成为 responseEligible；这是本模型的保守 response-order 实现，不声称 target memory service 必须串行；
- same-ID B/R 对 source 的可见完成顺序正确；
- different-ID 可以越过，测试中必须用确定性 target latency 制造一次真实 reorder；
- read 与 write 不因 ID 相同而隐式互相排序；
- 不得依赖 `unordered_map` 迭代顺序仲裁。

target key 含 `dstNode`，因此每个 target 独立 commit；source key 故意不含 `dstNode`，因此同一 source port、同一 ID、同一 direction 跨多个 target 仍全局 retire 有序。必须有 same-ID/different-destination 测试证明这个有意选择。

所有有多个 source 的本地 arbiter使用两级确定性选择：先用持久化 round-robin pointer 在非空 eligible source queue 之间选 source；再在该 source 内按

```text
(readyTick, txnUid, beatIndex)
```

取最小项。source ID 遍历顺序固定为 `(srcNode, srcPort)` 升序，pointer 只在实际 grant 后推进。禁止一边全局 tuple 排序、一边声称 round-robin 决定同一层级。

---

## 6. READY、有限容量与 backpressure

### 6.1 本地边界 API

```cpp
bool tryAcceptAw(const AxiAddressRequest &aw);
bool tryAcceptW(const AxiWBeat &w);
bool tryAcceptAr(const AxiAddressRequest &ar);

bool tryConsumeB(AxiBBeat &b);
bool tryConsumeR(AxiRBeat &r);
```

- `true` 表示当前 adapter clock edge 完成一次抽象 handshake；
- `false` 后 producer 必须保持完全相同的对象并在后续 edge 重试；
- 已被 adapter 接受的 item 即使暂时不能注入 Garnet，也必须安全保存在有限本地资源中；
- Garnet credit 不足只阻塞 injection，不撤销已经接受的 AXI item；
- READY 只由已注册的本地容量决定，不形成跨 Router 的组合 READY 链。

建议 admission 条件：

```text
AWREADY = AW FIFO space
       && write outstanding slot
       && pairing metadata slot
       && B ROB reservation

WREADY  = W FIFO space
       && burst parser state legal
       && pre-AW burst/beat capacity

ARREADY = AR FIFO space
       && read outstanding slot
       && R transaction/beat reservation
```

### 6.2 默认配置与单位

```yaml
garnet:
  number_of_virtual_networks: 5
  vcs_per_vnet: 4
  vnet_classes: [ctrl, data, ctrl, ctrl, data]
  buffers_per_vnet: [4, 8, 4, 4, 8]
  ni_flit_size_bytes: 16
  routing_algorithm: 1

axi:
  data_width_bits: 512
  id_width_bits: 8
  strict_protocol: true
  max_outstanding_reads: 64
  max_outstanding_writes: 32

  source_fifo_depth:
    aw_transactions: 16
    w_beats: 64
    b_responses: 16
    ar_transactions: 32
    r_beats: 128

  ruby_message_buffer_depth:
    aw_messages: 16
    w_messages: 64
    b_messages: 16
    ar_messages: 32
    r_messages: 128

  local_delivery_depth:
    aw_messages: 16
    w_messages: 64
    b_messages: 16
    ar_messages: 32
    r_messages: 128

  source_pre_aw_bursts: 16
  source_pre_aw_beats: 256
  target_write_contexts: 64
  target_write_assembly_beats: 4096
  target_read_contexts: 64
  target_read_response_beats: 4096
  target_service_depth:
    writes: 32
    reads: 32
  target_response_ready_depth:
    b_responses: 16
    r_beats: 128
  target_orphan_w_transactions: 16
  target_orphan_w_beats: 256
  b_rob_transactions: 64
  r_rob_beats: 1024

axi_wire:
  header_bytes: [24, 16, 8, 24, 16]
  data_encoding: full_bus
```

单位必须保持独立：

| 配置 | 单位 |
|---|---|
| Router input VC | flits / VC |
| `OutVcState` | credits，即下游 VC 空槽镜像 |
| NI flitized output queue | flits |
| Ruby `MessageBuffer` | messages |
| SLICC local-delivery buffer | messages，每 channel 独立 |
| AXI AW/AR/B FIFO | transactions/responses |
| AXI W/R FIFO | beats |
| outstanding/ROB | transactions 或明确的 beat slots |
| target service/response-ready | 分别为 transactions 与 B responses/R beats |

任何 `MessageBuffer.buffer_size=0` 都表示无限，本阶段 AXI network buffers 禁止设为 0。`randomization=disabled`，跨对象 buffer 的 `allow_zero_latency=false`。

### 6.3 Forward progress 与资源预留

- AW、W、B、AR、R 必须使用独立 endpoint FIFO 和独立 Ruby MessageBuffer；禁止合并成一个 gateway FIFO；
- source 接受 AW 前预留 B completion/ROB entry，接受 AR 前预留 R transaction/beat state；
- 每个 target 在 instantiate 时把 write/read context、assembly beat 和 response obligation 分成静态 per-source quota。对每个 target 必须满足：

```text
sum(write_context_quota[src]) <= target_write_contexts
sum(write_beat_quota[src])    <= target_write_assembly_beats
sum(read_context_quota[src])  <= target_read_contexts
sum(read_beat_quota[src])     <= target_read_response_beats
```

- source 在某 transaction 的第一个 AW/W 或 AR packet 注入前，必须从该 transaction 的 destination quota 取得一个 context token、完整 `beatCount` 的 beat tokens 和一个由 context 持有的 response obligation；W-first 可先被本地 pre-AW buffer 接受，但 AW 未给出 destination/beatCount 前不得取得 token或注入；
- quota 是启动时静态分配给 source 的本地 credit，不需要跨 mesh 请求 reservation。source 只使用自己的额度，token 在最终 B/RLAST local handshake 后归还，因此 target 不可能因另一个 source 抢占全部 context 而拒绝已合法注入的首 packet；
- target 仍按 source 统计 active context/beat reservations，并对每个首 packet 验证未超过配置 quota；超过说明 source bookkeeping 损坏并 fatal，不能用 backpressure 掩盖。source/target 两侧的 quota acquire/release counters 在 quiescence 时必须逐项相等；
- target 的 B/R obligation 可以保存在 context 内直到 response-ready FIFO 有空间，不要求一开始占用 injection FIFO；但 B 与 R 各自有独立 obligation/ready/injection path。memory service ready 后不得占用会阻塞另一 response channel的共享 service slot；
- 若 quota 不够，source 只阻塞该 destination 的首次注入；不能占用 network resource 等待 target context。其他 destination、channel 和已取得 token 的 transaction 仍可前进；
- B/R ready queue 与 injection FIFO 必须相互独立，也不能被 AW/W/AR request 占用；
- 长 R burst 可以随 bounded R FIFO 空间逐 beat 生成，不能一次把全部 R beat 放进无限容器；
- target 复制已 ejected message 后立即允许返回 Garnet credit，不得占用 input VC 等待同一 transaction 的后续 channel 或 response；
- AW/W/AR request flood 下，基线 round-robin 仲裁必须让 B/R 获得持续进展；本阶段不得增加严格优先级；
- master 不得等待 B 才发送同一 transaction 尚未发送的 W beat；
- incoming AW、W、AR、B、R 使用独立 network/local-delivery FIFO。orphan W 子配额满时只停 W dequeue；用于匹配 W_ONLY 的 AW 在独立 AW FIFO 中仍可到达并原地 merge；
- 所有容量耗尽都是 backpressure，不允许自动扩大 `deque`，也不允许通过增大 deadlock threshold 掩盖无进展。

资源依赖必须按下表实现和写入 debug dump：

| 状态 | 已持有 | 允许等待 | 释放点 |
|---|---|---|---|
| source W-first, unbound | pre-AW beat slots | 匹配 AW / 本地容量 | AW 绑定后逐 beat 释放；最终错误检查时清空 |
| source bound write | outstanding+B ROB+target quota tokens | AW/W injection、B | B local handshake 后全部释放 |
| target AW_ONLY/W_ONLY | 1 write context+全部 assembly beat reservation+B obligation | 匹配 channel | 原地转 BOUND，不重新申请 |
| target write serviceReady | 同一 context+B obligation | targetSeq commit gate、B-ready space | B descriptor 安全入独立 path 后释放 target context；source token仍到 B handshake 才归还 |
| source read | outstanding+R ROB beats+target quota tokens | R packets/RLAST | RLAST local handshake |
| target read | read context+R beat reservation+R obligation | service/targetSeq/R-ready space | 全部 R descriptors 安全入独立 path 后释放 target context |

任何一行都不得“持有 request FIFO/Router VC，等待 response FIFO 空间”。Commit 1 probe 或后续压力测试若发现新的 hold-and-wait edge，必须更新此表并给出无环说明后才可继续。

---

## 7. Garnet 通用改造

### 7.1 兼容参数

保留 stock 参数：

```python
buffers_per_data_vc = Param.UInt32(4, ...)
buffers_per_ctrl_vc = Param.UInt32(1, ...)
```

新增：

```python
vnet_classes = VectorParam.String(
    [], "Optional per-vnet class: ctrl or data"
)
buffers_per_vnet = VectorParam.UInt32(
    [], "Optional input depth in flits for every VC of each vnet"
)
```

固定优先级：

1. `vnet_classes=[]`：保持 legacy，通过 `vnet_type_name == "response"` 推导 data，其余 ctrl；
2. `vnet_classes` 非空：完全覆盖 legacy 推导；
3. `buffers_per_vnet=[]`：按规范化 class 使用旧 data/ctrl depth；
4. `buffers_per_vnet` 非空：完全覆盖旧 depth；
5. 内部始终保存长度等于 `number_of_virtual_networks` 的规范化数组。

新增统一 getter：

```cpp
uint32_t getBuffersPerVnet(unsigned vnet) const;
VNET_type getVnetType(unsigned vnet) const;
```

现有 `getBuffersPerDataVC()` / `getBuffersPerCtrlVC()` 不删除。vector mode 下：同一 class 的所有 vnet depth 相等时，legacy getter 返回该统一值；同一 class depth 不等且 FaultModel 已启用时 init fatal；FaultModel 关闭时新 credit/capacity 路径绝不能调用 legacy getter。若配置中没有某个 class，legacy getter 保持原参数值但只允许兼容代码查询，不能据此创建不存在的 VC。

### 7.2 初始化 fatal 条件

以下必须在 instantiate/init 阶段以稳定诊断失败：

1. `number_of_virtual_networks == 0`；
2. `vcs_per_vnet == 0`；
3. `ni_flit_size == 0`；
4. `buffers_per_vnet` 非空但长度不等于 vnet 数；
5. 任一 depth 为 0；
6. `vnet_classes` 非空但长度不等于 vnet 数；
7. class 不是严格的 `ctrl` 或 `data`；
8. `virtual_networks * vcs_per_vnet` 整数溢出；
9. NI 对同一 physical out link 使用不一致的 `consumerVcs`；
10. 启用 stock FaultModel 时，同一 class 内出现不同 depth。

稳定错误片段示例：

```text
buffers_per_vnet length 4 must equal number_of_virtual_networks 5
vnet_classes[1]='payload' is invalid; expected 'ctrl' or 'data'
buffers_per_vnet[2] must be >= 1
```

### 7.3 credit 与真实容量

Stock Garnet 的 `VirtualChannel/flitBuffer` 实际可增长，容量主要由 upstream credit 间接约束。本阶段必须增加双重保护：

- `OutVcState` 通过 `getBuffersPerVnet(vnet)` 设置 `m_max_credit_count` 和初始 credit；
- 每个 Router input `VirtualChannel` 保存同一个 getter 返回的 capacity；
- 接收 flit 前检查 `occupancy < capacity`，若已满仍收到 flit，panic 并打印 router/inport/vc/vnet/occupancy/capacity，说明 credit bookkeeping 已损坏；
- credit underflow/overflow 使用带上下文的 `panic_if`，不能只靠会在 opt build 消失的 `assert`。

始终满足：

```text
0 <= availableCredit <= configuredDepth
0 <= inputOccupancy <= configuredDepth
availableCredit = initialCredit - sentFlits + returnedCredits
sentFlits - returnedCredits <= configuredDepth
```

对于默认配置，每个 Router input port 总容量：

```text
4 VCs/vnet * (4 + 8 + 4 + 4 + 8) = 112 flits
```

### 7.4 动态 packet size

在 `Message` 增加与 SLICC 自动生成 getter 匹配的 protocol-neutral hook：

```cpp
virtual const int&
getWireSizeBytes() const
{
    static const int unspecified = 0;
    return unspecified;
}
```

`AXI_MESH-msg.sm` 中声明：

```text
int WireSizeBytes, default="0";
```

由生成的 `AxiMeshMsg::getWireSizeBytes()` override 上述虚函数。`0` 表示 legacy fallback。`NetworkInterface::flitisizeMessage()` 使用：

```cpp
wireBytes = msg->getWireSizeBytes();
if (wireBytes == 0)
    wireBytes = network->MessageSizeType_to_int(msg->getMessageSize());

numFlits = divCeil(wireBytes, outPortFlitBytes);
```

必须检查 `wireBytes > 0`、转换不溢出，并把同一个 `wireBytes` 传给 flit/统计。严禁在 Garnet 中 `dynamic_cast<AxiMeshMsg*>`。

FULL_TIMING 默认：

```text
AW wireBytes = awHeaderBytes
AR wireBytes = arHeaderBytes
B  wireBytes = bHeaderBytes
W  wireBytes = wHeaderBytes + dataBusBytes
R  wireBytes = rHeaderBytes + dataBusBytes
```

`semanticBytes`：W 为 `popcount(WSTRB)`，R 为 `1 << ARSIZE`。Garnet `wireBytes` 不包含未来 UCIe framing/FEC/replay 开销。
本阶段只接受 `data_encoding=full_bus`；任何 compact/sparse encoding 在 instantiate 前拒绝，避免同一通过标准对应两种链路负载。

### 7.5 统计

Garnet 最低新增/确认统计只包含它能从协议无关信息得到的量：

```text
per-vnet packets/flits injected/ejected
per-vnet wire bytes
input_vc_occupancy_flit_cycles
input_vc_full_vc_cycles
input_vc_full_events
input_vc_max_occupancy
credit_stall_vc_cycles
vc_alloc_stall_vc_cycles
ni_credit_stall_vc_cycles
ni_vc_busy_cycles
```

统计定义必须写进代码注释或文档：`input_vc_occupancy_flit_cycles[v]` 是该 vnet 所有 inport/VC 的 `occupancy × router cycles` 总和；`input_vc_full_vc_cycles[v]` 是 full 状态的 VC-cycle 总和，因此可以大于仿真周期；max occupancy 同时保留 per-vnet aggregate 和 per-router/inport/VC debug high-water。占用积分在 insert/pop 前按 elapsed cycles 更新，并在 stats dump 时 flush 到当前 cycle，不能只在 Router wakeup 时采样。不能把 no-credit、no-free-VC 和 SA lost 合成同一个 stall。

Garnet 不得解析 AXI 字段来统计 semantic/payload bytes；这两项只由 AXI adapter 输出。AXI adapter 还必须输出 per-channel/per-ID 可聚合统计：transaction issued/accepted/completed/error、beat count、payload/semantic/wire bytes、FIFO full、outstanding full、ROB full、orphan full、same-ID buffered/retired、target service latency 和端到端 latency。动态 ID 不得创建无上限的 gem5 Stats 对象；高基数字段写有预算的 trace，固定维度写 Stats。

### 7.6 Protocol-neutral quiescence introspection

基线没有一个公共 API 能同时证明 NI、Router、link 和 credit 已清空，因此本阶段必须增加只读的通用 introspection；不能让 AXI tester 读取私有成员或只从“已完成事务数”推断网络为空。

`GarnetNetwork` 至少暴露：

```cpp
struct GarnetQuiescenceSnapshot
{
    uint64_t niQueuedFlits;
    uint64_t niQueuedMessages;
    uint64_t routerBufferedFlits;
    uint64_t nonIdleInputVcs;
    uint64_t nonIdleOutputVcs;
    uint64_t dataLinkPendingFlits;
    uint64_t creditLinkPendingCredits;
    uint64_t bridgePendingItems;
    uint64_t creditDeficit;

    bool empty() const;
};

GarnetQuiescenceSnapshot quiescenceSnapshot() const;
bool isQuiescent() const;
```

实现由 `NetworkInterface`、`Router/InputUnit/OutputUnit`、`NetworkLink/CreditLink` 逐层汇总；若配置中实际创建了 `NetworkBridge`，还必须计入 bridge 的 serialization/CDC pending state，否则该字段为 0。`creditDeficit` 是所有 output VC 的 `initialCredit-currentCredit` 之和，成功 drain 时必须为 0。

要求：

- API 只读、协议无关，不改变 wakeup、仲裁或 timing；
- 每个计数项的单位和包含的 queue 在注释中列全；
- `empty()` 只在所有字段为 0 且所有 VC 状态恢复初始状态时返回 true；
- timeout 时把 snapshot 连同第一个非空 NI/router/link 标识写进结果和日志；
- 至少一个通用 Garnet 单测/集成测试人工制造每类非零状态，证明不会出现 false quiescent；
- legacy regression 必须证明关闭 introspection consumer 时 timing 和 grant trace 不变。

---

## 8. 目标内存、checker 与确定性

### 8.1 Simple target memory

提供一个仅用于协议验证的 byte-addressable memory：

- address range 可配置；
- read/write base latency 可配置；
- 可按 `txnUid` 指定确定性额外 latency 和错误；
- WSTRB 只更新选中的 byte；
- OKAY write 在 targetSeq architectural-commit gate 一次性应用全部 WSTRB-selected bytes；
- fault plan 以 transaction 为粒度并在 commit 前确定。SLVERR/DECERR write drain 全部 W 但提交 0 byte；本阶段不建模 partial write side effect。SLVERR/DECERR read 按第 3.2 节返回全零数据；
- same-ID write side effect 按 targetSeq 顺序；
- 不模拟 cache、DDR bank、row buffer 或 coherence；
- target service queue 有界，满时 backpressure；
- response descriptor queue 有界。

上述 all-or-nothing fault side-effect 是本验证 memory 的确定性建模策略，不得对外宣称为 AXI architectural atomicity。

Normal target ranges 是启动时配置的非空、半开区间 `[start,end)`；必须排序且两两不重叠，重叠、空区间或 64-bit end overflow 在 instantiate 前 fatal。default error target 不用 catch-all range参与重叠检查。

对通过第 3.3 节 INCR/对齐检查的 OKAY candidate，source 用 checked arithmetic 计算完整 byte span：

```text
firstByte = address
lastByteExclusive = address + beatCount * beatBytes
```

只有该半开 span 完整落在恰好一个 normal target range 时才选择该 target；未命中或尾部跨 normal target range 均整体路由 default error target并返回 DECERR，不允许把一个 AXI burst拆到两个 target。FIXED/WRAP/unaligned/LOCK/REGION 等已经选中 DECERR 的请求跳过 normal range decode，直接去 error target。

Address decoder 必须包含一个显式 default error target，并把它像普通 target 一样连接到一个配置的 mesh router。所有未命中/unsupported 地址在 source AW/AR acceptance 时解析到该 target 的真实 `MachineID/NetDest`，并取得该 target 的 quota token；AW/W/AR 仍通过 Garnet。error target 不访问 memory：read 产生完整 `beatCount` 个 DECERR R，write 接收 AW 并 drain 全部 W 后产生一个 DECERR B。这样 decode miss 参与正常 packet/flit/credit conservation，不允许 source-local shortcut。

### 8.2 独立 oracle

测试 checker 必须从 workload plan 和实际 ejection/completion event 构造 shadow state，不能调用 DUT 的 burst validation、beat address、pairing 或 ordering helper，否则相同 bug 可能同时存在于 DUT 和 oracle。

checker 必须逐 UID 检查：

- AW/AR 各恰好一次；
- W beat 数等于 AWLEN+1；
- R beat 数等于 ARLEN+1；
- beat index 无缺失、重复或越界；
- WLAST/RLAST 仅最后 beat；
- 每个 write 恰好一个 B；
- WSTRB 之后的 byte-level memory 与 golden array 完全相等；
- read payload/digest 与 shadow memory 相等；
- response 和 completion 顺序满足 ordering key。

禁止只用 XOR checksum，因为成对重复可能抵消。

Randomized functional workload 中，不同 `(src, axiId, direction, dst)` ordering domain 必须使用不重叠的 byte ranges，避免允许的 different-ID 或 read/write reorder 造成多个合法 golden。随机地址碰撞应在 workload 生成阶段拒绝。相同地址 alias 只出现在专门的 same-ID ordering case，其 reference linearization 由 workload plan 预先固定；checker 不得采用 DUT 实际 commit 次序作为正确性 oracle。

### 8.3 keyed RNG 与 replay

RNG namespace 至少分开：

```text
workload, memory_latency, channel_skew, response_stall, fault_injection
```

禁止 Python/C++ 默认 `hash()`、全局 RNG 或依赖调用顺序的 PRNG。两端实现同一固定 SplitMix64 keyed function：

```text
state = masterSeed
keyId = planTxnIndex for workload namespace, otherwise txnUid
for word in [keyId, stageEnum, beatIndex, drawKindEnum]:
    state = splitmix64(state XOR word)
random64 = state

splitmix64(x):
    z = x + 0x9e3779b97f4a7c15                         (mod 2^64)
    z = (z XOR (z >> 30)) * 0xbf58476d1ce4e5b9      (mod 2^64)
    z = (z XOR (z >> 27)) * 0x94d049bb133111eb      (mod 2^64)
    return z XOR (z >> 31)
```

所有 word 为 unsigned 64-bit，常量 `stageEnum/drawKindEnum` 写入一个共享表，并由 Python/C++ 单测对至少 32 个 golden vector 核对。运行前冻结完整 workload plan，并保存：

```text
git SHA, semantic config hash, master seed, stableScenarioId,
arrival tick, planTxnIndex, src/dst, expected txnUid, axiId,
address, burst, size, strobe, payload digest,
target latency/error, channel skew
```

`planTxnIndex` 是 scenario 内稳定逻辑编号；每个 source/direction 的 driver 不重排 AW 内部或 AR 内部顺序，但两个 direction 可独立前进。按第 4.1 节 UID layout 可预先写出 expected UID；实际 acceptance 后 checker 必须核对 expected/actual UID，而不是用 DUT UID回填 workload oracle。

`determinism_a/b/replay` 的 manifest `invocationId`、outdir 和 result path 可以不同，但三者共享同一个 `stableScenarioId`，且这些 invocation-only 字段不进入 semantic config hash、workload bytes 或 event-trace hash。replay workload hash 必须与 a/b 相同。

normalized event trace 固定为 UTF-8 JSONL。每个 event 固定包含：

```text
schemaVersion, eventSeq, tick, phaseEnum, eventEnum, channel,
srcNode, srcPort, dstNode, txnUid, axiId, writeOrdinal,
targetSeq, responseSeq, beatIndex, beatCount, resp,
wireBytes, semanticBytes, payloadDigest, occupancy
```

不适用字段写 JSON `null`，不允许省略。key 按字典序，整数为十进制，布尔为小写 JSON，禁止浮点、对象地址、wall time 和 outdir；每行使用 compact separators 并以单个 `\n` 结束。事件按 `(tick, phaseEnum, eventSeq)` 写出，同 tick `eventSeq` 使用第 5.4 节的确定性 grant 次序。SHA256 对文件的精确 bytes 计算。相同配置、trace、seed 两次运行的 workload SHA256 和 event-trace SHA256 必须完全一致。失败必须保存完整 workload trace；自动 delta-minimize 是后续功能，不是本阶段通过条件。
`phaseEnum/eventEnum/channel/stageEnum/drawKindEnum` 的数值表必须 versioned，C++ 与 Python mirror 由同一组 golden-vector tests 核对；不得依赖 C++ enum 的隐式重编号。

---

## 9. 文件落点

### 9.1 新增文件

```text
build_opts/AXI_MESH

src/mem/ruby/protocol/axi_mesh/
  Kconfig
  SConsopts
  AXI_MESH.slicc
  AXI_MESH-msg.sm
  AXI_MESH-initiator.sm
  AXI_MESH-target.sm

configs/ruby/AXI_MESH.py
configs/topologies/AxiMeshDie.py

src/mem/axi/
  SConscript
  AxiGarnetEndpoint.py
  axi_types.hh
  axi_validation.hh
  axi_validation.cc
  axi_initiator_adapter.hh
  axi_initiator_adapter.cc
  axi_target_adapter.hh
  axi_target_adapter.cc
  axi_protocol_checker.hh
  axi_protocol_checker.cc
  axi_determinism.hh
  axi_determinism.cc
  axi_trace_tester.hh
  axi_trace_tester.cc
  axi_validation.test.cc
  axi_packetization.test.cc
  axi_write_pairing.test.cc
  axi_ordering.test.cc
  axi_flow_control.test.cc
  axi_error_response.test.cc
  axi_protocol_checker.test.cc

src/mem/ruby/network/garnet/
  garnet_vnet_config.test.cc
  garnet_vc_isolation.test.cc
  garnet_quiescence.test.cc

configs/example/axi_garnet_test.py
configs/example/axi_garnet_scenarios/
  smoke.json
  ordering.json
  backpressure.json
  stress.json

tests/gem5/axi_garnet/
  manifest.json
  run_axi_unit_tests.py
  run_axi_garnet_tests.py
  verify_axi_garnet_result.py
  golden/
    garnet_xs_dev_7478835a.json

tests/pyunit/ai_mesh/
  conftest.py
  test_axi_config.py
  test_axi_scenario.py
  test_axi_trace_checker.py
  test_axi_determinism.py

docs/ai_mesh/axi_garnet.md
```

`build_opts/AXI_MESH` 固定为：

```python
TARGET_ISA = 'null'
PROTOCOL = 'AXI_MESH'
```

根 `src/mem/ruby/protocol/Kconfig` 必须新增 `rsource "axi_mesh/Kconfig"`。子 Kconfig 按现有 `chi/Kconfig` 模式，在 `config PROTOCOL` 中为 `RUBY_PROTOCOL_AXI_MESH` 设置 default `"AXI_MESH"`，并在同一个 `cont_choice "Ruby protocol"` 中声明 `RUBY_PROTOCOL_AXI_MESH`；不得直接修改其他 protocol 的 choice 行为。`SConsopts` 必须注册 `AXI_MESH.slicc`，生成 protocol header 的 include 路径以实际 build 输出为准并在 Gate 0 记录。

若仓库的 SLICC/Kconfig 约定要求协议文件位于不同层级，可以按现有 `chi/` 的实际模式调整，但最终报告必须列出偏差原因；不可将文件混进现有 CHI protocol。

### 9.2 修改既有文件

| 文件 | 修改 |
|---|---|
| `src/mem/ruby/protocol/Kconfig` | 注册 `axi_mesh/Kconfig` |
| `src/mem/ruby/slicc_interface/Message.hh` | 增加默认返回 0 的动态 wire size hook |
| `src/mem/ruby/network/garnet/GarnetNetwork.py` | 增加两个兼容 vector 参数 |
| `GarnetNetwork.hh/.cc` | 参数规范化、getter、fatal、stats、quiescence 汇总 |
| `flit.hh/.cc`（仅在实现确需保存 packet wire size 时） | protocol-neutral wire-byte metadata；不得保存 AXI channel |
| `OutVcState.hh/.cc` | 逐 vnet 初始/max credit、max getter 与硬 invariant |
| `VirtualChannel.hh/.cc` | 通用 capacity/occupancy/isFull |
| `InputUnit.hh/.cc` | 通用 input VC 容量检查、统计和 idle accessor |
| `NetworkInterface.hh/.cc` | dynamic wireBytes packetization、queue idle accessor |
| `Router.hh/.cc`、`OutputUnit.hh/.cc` | protocol-neutral VC/credit quiescence accessor |
| `NetworkLink.hh/.cc`（含 data/credit link） | pending flit/credit accessor |
| `NetworkBridge.hh/.cc`（只有实际启用时） | pending serialization/CDC accessor |
| `configs/network/Network.py` | CLI 解析和启动前参数传递 |
| 对应 `SConscript` | 注册 SimObject/source/gtest/debug flag |

尽量不修改 `Router.cc`；只有通用的 `vcs_per_vnet > 0`、乘法溢出检查确实无法放在 Network 初始化时，才允许加入无 AXI 依赖的检查。

`src/mem/axi/SConscript` 必须隔离生成协议头依赖。最简单、首选的 guard 是：

```python
Import("*")

if env["CONF"]["PROTOCOL"] != "AXI_MESH":
    Return()
```

若把 protocol-neutral AXI helper 留给其他 build，则只允许把不 include 生成头的 source 放在 guard 外；所有 include `AxiMeshMsg.hh` 的 adapter/source/GTest 必须在 guard 内。每个 `*.test.cc` 都必须由显式 `GTest(...)` 注册，不能依赖通配符或自动发现。通用 Garnet 参数 helper 的轻量 GTest 放在 Garnet 自身 `SConscript`，不要为测试 vector normalization 强行构造完整 RubySystem/Topology。`Garnet_standalone` 和至少一个 CHI build 必须证明没有 AXI 生成头泄漏。

---

## 10. 配置接口

至少提供以下 CLI：

```text
--garnet-vnet-classes=ctrl,data,ctrl,ctrl,data
--garnet-buffers-per-vnet=4,8,4,4,8
--vcs-per-vnet=4
--link-width-bits=128

--axi-mesh-routers=4
--mesh-rows=2
--axi-data-width-bits=512
--axi-id-width-bits=8
--axi-user-width-bits=0
--axi-max-outstanding-reads=64
--axi-max-outstanding-writes=32
--axi-source-fifo-depths=16,64,16,32,128
--axi-message-buffer-depths=16,64,16,32,128
--axi-local-delivery-depths=16,64,16,32,128
--axi-source-pre-aw-bursts=16
--axi-source-pre-aw-beats=256
--axi-target-write-contexts=64
--axi-target-write-assembly-beats=4096
--axi-target-read-contexts=64
--axi-target-read-response-beats=4096
--axi-target-service-depths=32,32
--axi-target-response-ready-depths=16,128
--axi-orphan-w-transactions=16
--axi-orphan-w-beats=256
--axi-b-rob-transactions=64
--axi-r-rob-beats=1024
--axi-wire-header-bytes=24,16,8,24,16
--axi-strict-protocol=true
--axi-seed=42
--axi-scenario=<json>
--axi-max-sim-ticks=<N>
--axi-result-json=<path>
```

CSV 参数必须：

- 拒绝空元素、负数、尾随垃圾；
- 检查元素数；
- 不允许 Python 静默截断；
- 用稳定错误信息退出。

scenario JSON 还必须显式给出 endpoint-to-router map、default error target，以及每个 `(source,target)` 的 write/read context 和 beat quota。parser 必须验证第 6.3 节的 quota sum，不得根据运行时竞争动态超配。

instantiate 前还必须检查：

```text
link_width_bits % 8 == 0
ni_flit_size_bytes == link_width_bits / 8 and > 0
data_width_bits in {64,128,256,512}
id_width_bits in [1,16]
user_width_bits == 0
every srcNode < 65536 and srcPort < 256
WSTRB width == dataBusBytes
RubySystem.block_size_bytes >= dataBusBytes
DataBlock capacity >= dataBusBytes
every headerBytes + optional dataBusBytes fits positive int
every headerBytes >= 1
all FIFO/MessageBuffer/context/beat/quota depths > 0
strict_protocol == true
data_encoding == full_bus
```

`--axi-strict-protocol=false` 必须按第 3.2 节失败。local-delivery 和 network `MessageBuffer` 都必须 `buffer_size>0`、`allow_zero_latency=false`。

“运行时可配”在本文中仅表示 gem5 instantiate 前由 Python/CLI/JSON 配置且无需重新编译；仿真过程中不支持动态 resize。

---

## 11. 不变量与结束条件

### 11.1 Runtime 不变量

```text
acceptedAW = completedB + outstandingWrites
acceptedAR = completedReadBursts + outstandingReads

completed write txn -> exactly one AW, expected W beats, one WLAST, one B
completed read txn  -> exactly one AR, expected R beats, one RLAST
for every in-flight bound write: 0 <= observed W beats <= beatCount
for every in-flight read:        0 <= observed R beats <= beatCount
wBurstsOpenedTotal == wBurstsClosedTotal + (current W burst open ? 1 : 0)
wBurstsPairedTotal <= acceptedAW and wBurstsPairedTotal <= wBurstsOpenedTotal
liveUnboundBursts == completeUnboundBufferedBursts + (currentOpenWIsUnbound ? 1 : 0)
liveUnboundBeats == all currently buffered beats belonging to live unbound W bursts
0 <= liveUnboundBursts <= source_pre_aw_bursts
0 <= liveUnboundBeats <= source_pre_aw_beats
RLAST count <= acceptedAR

B is eligible only after all W beats are received/drained and outcome finalized
OKAY B requires all WSTRB-selected bytes architecturally committed
error B does not require bytes to commit, but still requires every W beat drained
write completion is not before B handshake
read completion is not before RLAST handshake

same source Nth AW pairs only with Nth W burst
txnUid is unique among outstanding transactions
same-ID targetSeq architectural commit is monotonic
same-ID responseSeq retires monotonically

queue overflow -> backpressure, never drop or auto-resize
wireBytes > 0
numFlits = ceil(wireBytes / niFlitBytes)
for every directed link and VC:
    currentCredit = initialCredit - sentFlits + returnedCredits
    0 <= currentCredit <= configuredDepth
```

`issued` 只表示 workload plan 中唯一逻辑 transaction 数，不包含 `tryAccept*=false` 的重试调用；另行统计 `admission_attempts` 和 `retry_cycles`。

### 11.2 Quiescence

成功结束不能由“到达 max tick”或“请求已经发完”定义。必须显式确认：

```text
tester pending plan == 0
tester held retry items == 0
source AW/W/AR FIFOs == 0
source unmatched AW/W == 0
source outstanding == 0
source B/R ROB and reassembly == 0
all SLICC local-delivery buffers == 0
all adapter ingress handoff/FIFOs == 0
all producer pending local handshake/callback == 0
all adapter-owned delivery/retry business events == 0
target context/orphan/order/service queues == 0
target response obligations == 0
B/R generation queues == 0
all AXI Ruby MessageBuffers == 0
all target completion events == 0
GarnetNetwork.quiescenceSnapshot().empty() == true

W beats == sum(write beatCount)
R beats == sum(read beatCount)
WLAST count == accepted write transaction count
RLAST count == accepted read transaction count
one accepted write -> exactly one B
one accepted read  -> exactly one RLAST
```

上述条件必须连续两个 network clock edge 为真才允许成功退出，避免同 tick 的 credit/link event 尚未落地。正常 drain 阶段先停止创建新 transaction并等待已有 partial burst、response 和 credit 完成；只有 workload 已明确提交完且最终一致性检查仍发现 orphan/partial burst，才报告 protocol failure。组件必须自己登记业务 event 数；不要求 gem5 全局 EventQueue 为空，周期性 stats/watchdog event 不计入业务非静默事件。

本阶段实现 tester 自有的 `finishAndCheckQuiescence()` 状态机：`RUNNING -> STOP_ISSUE -> DRAINING -> CHECK_STABLE_1 -> CHECK_STABLE_2 -> exitSimLoop`。它不宣称实现 gem5 checkpoint `Drainable::drain()`/serialize；checkpoint/restore 仍在第 1.2 节排除范围。

达到 `max_sim_ticks`、满足第 14 节 liveness 前提后的 progress watchdog 超时，或最终仍有 outstanding 时必须非零退出。

---

## 12. 测试基础设施

### 12.1 统一入口

必须实现两个 runner。先运行显式登记的 unit binary：

```bash
python3 tests/gem5/axi_garnet/run_axi_unit_tests.py \
  --build-dir build/AXI_MESH --variant opt \
  --manifest tests/gem5/axi_garnet/manifest.json
```

`manifest.json` 必须显式列出每个 `GTest(...)` 生成的 binary 相对路径和预期 suite，例如 `mem/axi/axi_validation.test.opt`。unit runner 不得把 `build/AXI_MESH/unittests.opt` 当作可执行文件，也不得用 `find` 猜测 binary。它必须拒绝 missing binary、zero binary、zero test、disabled/skip test，并为每个 binary 设置 60 秒 wall timeout，通过 `--gtest_output=xml:<case-outdir>/gtest.xml` 产出机器可读结果，再汇总为 suite report。

集成测试入口：

```bash
python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.debug \
  --suite quick

python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.opt \
  --suite full
```

runner 必须：

- 读取 versioned `manifest.json`；
- 每个 case 声明 `binary_role`：默认 `axi` 使用命令行 `--gem5`；三个 `legacy_vnet*` 固定使用已构建的 `build/Garnet_standalone/gem5.opt`。runner 启动前检查对应 binary 和 build SHA，禁止静默用 AXI build 代跑 legacy case；
- 每个 case 使用独立 outdir；
- `binary_role=axi` 且 `expect=pass/architected_error` 的 case 必须生成完整 `axi_result.json`、`credit_ledger.json`、`stats.txt`、resolved `config.json` 和 normalized event/workload trace（成功后可按 manifest 只保留 hash）；
- `expect=fatal_init/fatal_runtime` 的 case 必须由 runner 生成第 12.2 节的 `negative_result.json`；instantiate/handshake 即时终止来不及 dump 的字段可以标为 unavailable，但 marker、退出码和已观察 counters 仍为必需；
- `expect=final_consistency_failure` 只用于受控最终检查，固定退出码 2。它必须生成 `negative_result.json`、完整 `credit_ledger.json`、`stats.txt`、resolved `config.json`、normalized event/workload trace 和 residual-state snapshot；N10 的 network ledger 必须已恢复，但 endpoint residual 故意非零；
- `legacy_vnet*` 不实现 AXI schema。它们保留 stock `stats.txt/config.ini/config.json`，runner 按 versioned golden 的精确 stat keys 生成 `legacy_result.json`；不要求 AXI transaction、credit ledger 或 workload/event trace 字段；
- 使用 wall-clock timeout；timeout 退出码 124；
- 检查 gem5 exit code；
- 检查实际 case 数等于 manifest；zero-test success 视为失败；
- 每个 case 声明 `expect ∈ {pass, architected_error, fatal_init, fatal_runtime, final_consistency_failure}`、精确 exit-code policy 和稳定 marker：前两类固定退出 0，受控 fatal 固定退出 1，final-consistency 固定退出 2；只有 manifest 指定的后三类才允许非零 gem5 exit；wall timeout 124 永远失败；
- AXI `pass/architected_error` 缺少完整 result/ledger 必须失败；final-consistency 缺少完整 negative result/ledger/residual snapshot 必须失败；legacy pass 缺少完整 stock stats 或 `legacy_result.json` 也失败。不能把 signal、crash、segfault、裸 assert 或未匹配 marker 的退出当作预期 fatal；
- skip、xfail 或 disabled 均使 suite 非零退出；
- 不通过解析人类日志判断功能正确性；
- 失败时保留 seed、scenario、命令、stderr tail 和 replay trace；
- 成功时可以清理大 trace，但保留结果 JSON。

### 12.2 AXI 集成 case 的结果 JSON

以下 `axi_result.json` 适用于正常完成的 `pass/architected_error` case，至少包含：

```json
{
  "schema_version": 1,
  "case": "same_id_order",
  "status": "pass",
  "git_sha": "...",
  "config_hash": "...",
  "seed": 42,
  "sim_ticks": 12345,
  "sim_network_cycles": 123,
  "transactions_issued": 8,
  "admission_attempts": 12,
  "retry_cycles": 4,
  "transactions_accepted": 8,
  "transactions_completed": 8,
  "transactions_error": 0,
  "packets_injected": 30,
  "packets_ejected": 30,
  "flits_injected": 80,
  "flits_ejected": 80,
  "per_vnet": {
    "packets_injected": [8, 8, 8, 3, 3],
    "packets_ejected": [8, 8, 8, 3, 3],
    "flits_injected": [16, 40, 8, 6, 10],
    "flits_ejected": [16, 40, 8, 6, 10]
  },
  "queue_high_water": {
    "local_fifo": [1, 4, 1, 1, 4],
    "message_buffer": [1, 4, 1, 1, 4],
    "router_vc": [2, 5, 1, 2, 5]
  },
  "stall_events": {
    "local_fifo_full": 0,
    "message_buffer_or_ni": 0,
    "router_credit": 0,
    "vc_allocation": 0
  },
  "credit_ledger": {
    "path": "credit_ledger.json",
    "sha256": "...",
    "directed_links": 16,
    "vcs_per_link": 20,
    "all_restored": true
  },
  "credit_mismatches_by_link_vc": 0,
  "outstanding_at_exit": 0,
  "orphan_w_at_exit": 0,
  "rob_entries_at_exit": 0,
  "message_buffers_at_exit": 0,
  "local_delivery_at_exit": 0,
  "adapter_ingress_at_exit": 0,
  "response_obligations_at_exit": 0,
  "business_events_at_exit": 0,
  "router_vc_flits_at_exit": 0,
  "quiescence_snapshot_at_exit": {
    "ni_queued_flits": 0,
    "ni_queued_messages": 0,
    "router_buffered_flits": 0,
    "non_idle_input_vcs": 0,
    "non_idle_output_vcs": 0,
    "data_link_pending_flits": 0,
    "credit_link_pending_credits": 0,
    "bridge_pending_items": 0,
    "credit_deficit": 0
  },
  "quiescent_consecutive_cycles": 2,
  "protocol_errors": 0,
  "workload_sha256": "...",
  "trace_sha256": "..."
}
```

上述数字只展示 schema，不是 golden。`credit_ledger.json` 必须逐 directed link × VC 保存 `initial/sent/returned/current/depth`，主结果记录 shape 与 SHA256；credit 事件不能与端到端 NI injected flit 数直接比较。verifier 必须读取 ledger 并拒绝缺字段、维度与实际 network/VC 数不符、hash 不符、未知 schema、NaN、负计数和 `status=pass` 但守恒条件不成立的结果。

`fatal_init/fatal_runtime/final_consistency_failure` 使用独立 `negative_result.json`，公共必需字段为：

```json
{
  "schema_version": 1,
  "case": "n10_source_w_without_aw",
  "status": "expected_failure",
  "failure_class": "final_consistency_failure",
  "expected_marker": "AXI_FINAL_CONSISTENCY: source W burst without AW",
  "observed_marker": "AXI_FINAL_CONSISTENCY: source W burst without AW",
  "exit_code": 2,
  "timed_out": false,
  "terminated_by_signal": false,
  "git_sha": "...",
  "command_sha256": "...",
  "seed": 1,
  "packets_injected_before_failure": 0,
  "flits_injected_before_failure": 0,
  "available_artifacts": ["stats.txt", "config.json", "credit_ledger.json", "residual_state.json", "workload.jsonl", "event_trace.jsonl"],
  "unavailable_fields": [],
  "workload_sha256": "...",
  "trace_sha256": "...",
  "residual_state": {
    "write_ordinal": 0,
    "buffered_w_beats": 3,
    "saw_wlast": false,
    "txn_uid_present": false
  }
}
```

runner 必须 exact-match `failure_class/exit_code/marker`，验证 timeout/signal 均为 false，并拒绝未声明的 extra failure。`available_artifacts` 是 mandatory non-result artifact 的 exhaustive list，必须与 outdir 中该 schema 管理的集合完全相等；它不列 `negative_result.json` 自身或 runner 的 stdout/stderr 日志。每个 trace path 必须存在且 SHA256 与结果字段一致。即时 fatal 若无法生成完整 network dump，必须在 `unavailable_fields` 明列缺项而不能伪造 0；manifest 还要给出该错误前允许的 injected packet/flit 精确上限。N10 属于受控 final-consistency：`unavailable_fields` 必须为空，`residual_state` 必须精确列出 `writeOrdinal/buffered beats/saw WLAST/txn_uid_present=false`，credit ledger 必须全恢复，且不得等待 watchdog。

`legacy_result.json` 使用独立 schema，至少含 `schema_version/case/status/git_sha/base_sha/command_sha256/golden_sha256`、逐 stat 的 `expected/actual/delta/tolerance`、exit code 和 stock outdir；`status=pass` 只有第 13.2/I14 的全部比较通过。禁止把 legacy case 伪装成零 AXI transaction 的 `axi_result.json`。

GTest 不产出本 schema；它由 unit runner 收集 GTest XML 并生成独立 `unit_suite_result.json`，至少列 binary、expected/discovered fully-qualified test name set 及 diff、run/passed/failed/skipped/disabled/timed_out 数、duration、exit code 和 XML path。聚合 gate 必须满足 10 个 binary 全部存在、`expected=discovered=run=passed=66` 且 `failed=skipped=disabled=timed_out=0`。

### 12.3 Manifest 完整性合同

manifest schema/version 必须由 runner 校验，且 suite 不能通过删除 case 来变绿。`quick` 必须恰好包含下列 44 个 gem5 process case：

```text
endpoint_probe
single_write
burst_read
partial_wstrb
w_before_aw_at_target
same_id_order
cross_id_reorder
buffer_depth_credit_d1
buffer_depth_credit_d2
buffer_depth_credit_d8
target_quota_no_hol
ejection_backpressure
response_progress
rand_quick_s1
rand_quick_s7
rand_quick_s42
determinism_a
determinism_b
determinism_replay
legacy_vnet0
legacy_vnet1
legacy_vnet2
n1_static_zero_beat_count
n1_static_257_beat_count
n1_runtime_beat_count
n2_size
n3a_unaligned_decerr
n3b_4k_crossing
n4_early_wlast
n4_late_wlast
n4_missing_wlast
n5_duplicate_rbeat
n5_out_of_range_rbeat
n6_duplicate_uid
n6_unknown_response
n7_strict_false
n8_unmapped_decerr
n9_lock_decerr
n10_source_w_without_aw
n11_fifo_full
n11_rob_full
n11_orphan_full
n12_vector_length
n12_zero_depth
```

`full` 是 quick 的严格超集，恰好 127 个 process case：

- 上述 44 个；
- `buffer_matrix_<profile>_vc<vcs>_<traffic>` 的 72 个笛卡尔积：`profile ∈ {11111,24224,48448,8168816}`、`vcs ∈ {1,2,4}`、`traffic ∈ {aw,w,b,ar,r,all}`；
- `rand_full_s<seed>` 10 个，seed 固定为 `{1,7,42,20260831,314159,271828,65537,99,1234,9001}`，每个 2,000 transactions；
- `rand_nightly_s42_10000` 1 个，10,000 transactions。

profile 名不是按数字逐字符猜测，runner、manifest generator 和 verifier 必须共用同一个 versioned 映射表：

```text
11111   -> [1, 1, 1, 1, 1]
24224   -> [2, 4, 2, 2, 4]
48448   -> [4, 8, 4, 4, 8]
8168816 -> [8, 16, 8, 8, 16]
```

buffer matrix 的 `traffic=<channel>` 指“测量窗口内只让该 vnet 注入”：AW/W/AR 通过延迟其配对/target service来收尾，B/R 在 setup 阶段预先建立 response obligations 后于测量窗口释放；setup 与 drain 仍计入总守恒，但 high-water/stall delta 只取带明确 start/end tick 的测量窗口。`traffic=all` 同时释放五类。禁止直接伪造没有前置事务的 B/R。

runner 在启动前自行展开上述集合，与 manifest case 名做 exact set equality，并检查 quick random case 数为 3、full-only random case 数为 11（full 包含 quick 后合计 14 个 random process）、negative 子进程数为 22。missing、duplicate、extra case 都在运行前失败。U1–U12 的 suite 名也必须在 unit manifest 中 exact-match；`RISCV_CHI` build 是 build gate，不计入上述 process 数。

---

## 13. 必须实现的测试矩阵

### 13.1 C++ 与 Python 单元测试

C++ 通过仓库现有 `GTest(...)` 机制注册，不自建第三方测试框架。配置、scenario builder 和独立 trace verifier 使用 Python 单测。

unit manifest 固定登记 10 个 binary：

| Binary 相对路径（`.opt`） | 必含 suites |
|---|---|
| `mem/axi/axi_validation.test.opt` | U1 |
| `mem/axi/axi_packetization.test.opt` | U8 |
| `mem/axi/axi_write_pairing.test.opt` | U2、U3、U4 |
| `mem/axi/axi_ordering.test.opt` | U5 |
| `mem/axi/axi_flow_control.test.opt` | U6 |
| `mem/axi/axi_error_response.test.opt` | U7 |
| `mem/axi/axi_protocol_checker.test.opt` | U10 |
| `mem/ruby/network/garnet/garnet_vnet_config.test.opt` | U9 |
| `mem/ruby/network/garnet/garnet_vc_isolation.test.opt` | U11 |
| `mem/ruby/network/garnet/garnet_quiescence.test.opt` | U12 |

runner 必须 exact-match 10 个 binary 和 U1–U12 suite 集合。路径规则由 Gate 0 实测确认；若 SCons 将某 binary 放到不同 variant 子目录，只更新 manifest 和 implementation report，不改变 suite 数。

unit manifest 还必须 exact-match 下列 66 个 fully-qualified GTest 名；一个 test 可以内部 parameterize 多个 vector，但不得通过删 test 或把多个必需 failure path 变成未执行分支来减数：

| Suite | 必需 test names |
|---|---|
| `AxiBurstValidationTest` | `AcceptsIncrLengths`, `AcceptsDataWidths`, `MapsNarrowNonZeroLanes`, `RejectsOutOfLaneStrobe`, `RoutesUnalignedToDecerr`, `Rejects4KiBCrossing`, `RoutesUnsupportedFeaturesToDecerr`, `RejectsArithmeticOverflow` |
| `AxiWritePairingTest` | `PairsAwFirst`, `PairsWFirst`, `BuffersMultiplePreAwBursts`, `PairsNthWithNth`, `BlocksUnboundWInjection`, `StreamsPartialWAfterAw`, `KeepsAwReadyWhenWFull` |
| `AxiTargetOrphanWTest` | `MergesWBeforeAw`, `BackpressuresNewOrphanAtLimit`, `AllowsAwPastBlockedW`, `ConvertsSharedSlotInPlace`, `RejectsQuotaOversubscription`, `RejectsDuplicatePacket` |
| `AxiLastAndBeatTest` | `HandlesSingleAnd256BeatLast`, `RejectsEarlyWlast`, `RejectsMissingWlast`, `RejectsExtraWBeat`, `RejectsDuplicateBeat`, `ReassemblesRByIndex` |
| `AxiOrderingTest` | `AllocatesSequencesAtAcceptance`, `SameIdReadWaitsOlder`, `DifferentIdReadCanInvert`, `SameIdWriteCommitsInOrder`, `SameIdBRetiresInOrder`, `DifferentIdWriteCanInvert`, `ReadWriteDomainsIndependent`, `SameIdCrossTargetRetiresGlobally` |
| `AxiBackpressureTest` | `DepthOneChannelsBackpressure`, `PausesAndResumesBAndR`, `QueueFullReturnsNoError`, `RetriesOnlyOnLaterEdge` |
| `AxiErrorResponseTest` | `DecodeMissReadFullDecerr`, `DecodeMissWriteDrainsThenDecerr`, `RangeTailRoutesWholeBurstToError`, `TargetFaultReturnsSlverr`, `ErrorWriteCommitsNoBytes`, `ErrorReadReturnsZeroData`, `NeverProducesExOkay` |
| `AxiDynamicWireBytesTest` | `CountsDefaultFiveChannels`, `FallsBackForLegacyMessage`, `IgnoresSemanticBytesForFlits`, `RejectsInvalidWireSize` |
| `GarnetVnetConfigTest` | `PreservesLegacyFallback`, `MapsPerVnetDepth`, `MapsVnetClass`, `RejectsInvalidVectors`, `AcceptsFaultModelCompatibleDepths`, `RejectsFaultModelIncompatibleDepths` |
| `AxiProtocolCheckerTest` | `AcceptsValidTrace`, `RejectsDuplicateOrMissingBeat`, `RejectsBadLast`, `RejectsBadResponseSequence`, `RejectsBadData` |
| `GarnetVcIsolationTest` | `ExhaustsOnlySelectedVc`, `PreservesOtherVcs` |
| `GarnetQuiescenceSnapshotTest` | `DetectsEveryPendingClass`, `BecomesEmptyOnlyAfterRestore`, `AccessorsDoNotScheduleEvents` |

pytest collection 也必须从仓库根 exact-match 下列 21 个完整 node ID，`conftest.py` 在 collection/session finish 对比：

```text
tests/pyunit/ai_mesh/test_axi_config.py::test_accepts_default_configuration
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_invalid_csv
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_invalid_widths_and_datablock
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_overlapping_or_empty_ranges
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_quota_oversubscription
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_zero_or_infinite_buffers
tests/pyunit/ai_mesh/test_axi_config.py::test_rejects_non_strict_or_compact_mode
tests/pyunit/ai_mesh/test_axi_scenario.py::test_quick_manifest_exact_44
tests/pyunit/ai_mesh/test_axi_scenario.py::test_full_manifest_exact_127
tests/pyunit/ai_mesh/test_axi_scenario.py::test_rejects_duplicate_missing_extra_cases
tests/pyunit/ai_mesh/test_axi_scenario.py::test_validates_endpoint_router_map
tests/pyunit/ai_mesh/test_axi_scenario.py::test_random_domains_use_disjoint_ranges
tests/pyunit/ai_mesh/test_axi_scenario.py::test_stable_scenario_id_excludes_invocation_fields
tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_accepts_canonical_valid_trace
tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_duplicate_or_missing_beats
tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_last_or_response_order
tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_memory_data
tests/pyunit/ai_mesh/test_axi_trace_checker.py::test_rejects_bad_credit_ledger
tests/pyunit/ai_mesh/test_axi_determinism.py::test_splitmix64_golden_vectors
tests/pyunit/ai_mesh/test_axi_determinism.py::test_canonical_jsonl_bytes_and_sha
tests/pyunit/ai_mesh/test_axi_determinism.py::test_replay_hash_independent_of_invocation
```

#### U1 `AxiBurstValidationTest`

- 1、2、256 beat INCR 成功；
- 64/128/256/512-bit data width；
- 合法自然对齐 narrow transfer；
- 非零 lane offset 的 narrow transfer，逐 lane 检查第 3.3 节地址映射；
- OKAY narrow write 的合法 lane 外 WSTRB=1 为 death test；
- unaligned 走 DECERR policy；4 KiB crossing、非法 SIZE 为预期 fatal/death test；
- FIXED/WRAP、LOCK、非零 REGION 进入 DECERR policy。
- address 接近 `UINT64_MAX`、SIZE shift 和 burst span overflow 均被稳定拒绝。

#### U2 `AxiWritePairingTest`

- AW first、W first；
- 连续两个 pre-AW W burst；
- 第 N 个 AW 只配第 N 个 W burst；
- W 未取得 destination 前不得进入 network message buffer；
- `pre_aw_beats=4` 的 16-beat W-first burst：先接受 4 W、再接受 AW、释放并继续余下 12 W，最终完成；
- pre-AW burst/beat capacity 满后 `tryAcceptW=false`，独立 AW 仍能被接受并解除阻塞。

#### U3 `AxiTargetOrphanWTest`

- W packet 先到建立 `W_ONLY`；
- AW 后到正确 merge；
- orphan table 满只阻塞新的 unmatched W；
- local-delivery W 满时 SLICC 不 dequeue network W；独立 AW local-delivery 仍能投递并匹配既有 entry；
- AW_ONLY/W_ONLY/BOUND 共享 slot，merge 原地完成且不重复申请 assembly/B obligation；
- source quota 总和不超过 target pool；超配配置必须在 instantiate 前拒绝；
- duplicate beat/AW death test；
- 最终只生成一个 B。

#### U4 `AxiLastAndBeatTest`

- single-beat 与 256-beat WLAST/RLAST；
- early/missing/duplicate LAST；
- extra/missing/duplicate beat；
- R beat out-of-order 到达后按 index 重组。

#### U5 `AxiOrderingTest`

- AW/AR acceptance 时立即分配并冻结 txnUid/targetSeq/responseSeq，后续 quota/injection delay 不改变；
- 同 ID read：younger 内部 ready 更早，但 older RLAST 先对外完成；
- 不同 ID read：fast younger 允许先完成；
- 同 ID write 同地址：memory side effect 不能颠倒；
- 同 ID B 按 AW 顺序；
- 同 ID、不同 destination 在各 target 独立 commit，但 source 仍按全局 responseSeq retire；
- 不同 ID write 允许由 target latency 造成乱序；
- read/write 之间不隐式排序。

#### U6 `AxiBackpressureTest`

- 所有 FIFO 深度设为 1；
- 分别拉低 AW/W/AR acceptance；
- B/R consumer 暂停 50 cycles 后恢复；
- accepted item 最终精确完成一次；
- queue full 不产生 error response；
- tester 在 `tryAccept*=false` 后保存相同对象并只在后续 edge 重试；DUT 不承担不可观察的 VALID 稳定性检查。

#### U7 `AxiErrorResponseTest`

- decode miss read 返回完整 DECERR R beats + RLAST；
- decode miss write drain 全部 W 后返回一个 DECERR B；
- burst 首地址命中但尾部跨 normal target range 时，整笔去 error target、memory 不变且只返回一个 DECERR；
- target read/write fault 返回 SLVERR；
- SLVERR/DECERR write 完整 drain 后 memory image 保持不变，OKAY 才原子应用本模型的 selected bytes；
- DECERR/SLVERR R 的 DataBlock 全零且 digest 确定；
- model 从不生成 EXOKAY。

#### U8 `AxiDynamicWireBytesTest`

配置 `flitBytes=16`、header `[24,16,8,24,16]`、data width 512-bit：

```text
AW = ceil(24/16)      = 2 flits
W  = ceil((16+64)/16) = 5 flits
B  = ceil(8/16)       = 1 flit
AR = ceil(24/16)      = 2 flits
R  = ceil((16+64)/16) = 5 flits
```

同时检查 `semanticBytes` 不参与 FULL_TIMING flit 计费。

#### U9 `GarnetVnetConfigTest`

- empty vectors 保持 stock fallback；
- `[2,3,4,5,6]` 逐 vnet depth；
- vnet class mapping；
- 所有非法长度、0 depth、非法 class、0 VC、0 flit size；
- FaultModel 可表达与不可表达两种配置。

AXI target range/quota/DataBlock 配置属于 Python config tests，不得塞进 protocol-neutral Garnet binary。

#### U10 `AxiProtocolCheckerTest`

人工构造合法 trace，以及 duplicate/missing beat、错误 LAST、错误 responseSeq、错误 data 的 trace；独立 checker 必须逐一接受或拒绝，不能复用 DUT helper。

#### U11 `GarnetVcIsolationTest`

直接对 protocol-neutral `OutVcState`/InputUnit helper 构造同 vnet 4 VC，只耗尽其中一个 VC 的 credit；要求其他三个 credit/occupancy 不变且仍可前进。该测试不依赖 NI 的动态 VC 选择，也不得给生产态 AXI adapter 增加 forced-VC 语义。

#### U12 `GarnetQuiescenceSnapshotTest`

逐一人工制造 NI queued message/flit、Router buffered flit、非 IDLE input/output VC、data/credit link pending item、bridge pending item（若 build 支持）和 credit deficit；每种状态下 `empty()==false`，全部释放且 credit 恢复后才为 true。证明不会 false-quiescent，且 accessor 不调度 event、不改变 timing。

### 13.2 Garnet 集成测试

#### I0 `endpoint_probe`

1 initiator + 1 target + 至少 2 routers。initiator 的 AW/W/AR to-network、B/R from-network，以及 target 的相反方向全部正确注册；每个 incoming channel 还有一个独立有限 local-delivery buffer。无 null queue、双 Consumer 或同 tick 回调。用一笔 write 和一笔 read 覆盖五类 packet，五个 vnet 计数准确；local-delivery 设为 1 并填满时 network message 保持不 dequeue。

#### I1 `single_write`

- 单 beat write；
- 1 AW、1 W、1 B；
- target byte data 与 golden 一致；
- B 晚于 W commit；
- 完全 drain。

#### I2 `burst_read`

- 16-beat read；
- 1 AR、16 R、1 RLAST；
- 每 beat address/index/resp/data 正确；
- completion 只发生在 RLAST handshake 后。

#### I3 `partial_wstrb`

- 预置 memory pattern；
- 使用多种 WSTRB；
- 覆盖 512-bit bus 上 address `0x24` 的 4-byte narrow transfer，只有 lanes 36..39 可更新；
- 最终逐 byte 与独立 golden array 完全一致；
- FULL_TIMING W packet 仍按完整 data bus 计费。

#### I4 `w_before_aw_at_target`

- 用独立 channel injection delay 强制 W 比 AW 早抵达；
- target orphan occupancy 必须先上升再归零；
- 不丢 beat，只返回一个 B。

#### I5 `same_id_order`

- 两笔相同 ID read/write，故意让第二笔 service latency 更短；
- younger 可以更早 `serviceReady`，但 same-ID `architecturalCommit/responseEligible` 与 source completion 仍按第一、第二顺序；
- checker 必须观察到 younger 曾经 ready/blocked，证明不是测试没有制造倒序。

#### I6 `cross_id_reorder`

- slow ID=0 与 fast ID=1；
- ID=1 必须先完成；
- 若仍全局串行化则测试失败。

#### I7 `buffer_depth_and_credit`

配置 `vcs_per_vnet=1`，depth 依次扫描 1、2、8：

- 必须使目标 VC 达到 full；
- `maxOccupancy == configuredDepth`；
- 第 depth+1 个无 credit flit 不能进入；
- 让一个 W/R packet 的 flit 数大于单 VC depth，验证 wormhole packet 可以边收边退并最终完成，不能错误要求“整包必须装进一个 VC”；
- small depth 下 `credit_stall_vc_cycles > 0`；
- 恢复后全部完成且 credit 回到 initial；
- occupancy 永不越界。

#### I8 `target_quota_no_hol`

2 sources + 1 target，target write context=2，每 source quota=1；source 0 制造 W-before-AW 并填满其 orphan 子配额，source 1 同时提交 AW-first。要求：

- 两个 source 都不能超过各自 quota；
- W incoming/local-delivery 堵塞不阻塞独立 AW channel；
- 匹配 AW 原地 merge，不申请第二 slot；
- source 1 仍取得 forward progress；
- 所有 context、assembly beat、B obligation token 最终精确归还。

#### I9 `ejection_backpressure`

暂停 target 或 initiator response consumer，流量超过 MessageBuffer 与 local FIFO 深度：

- NI stall queue/credit backpressure 确实出现；
- 所有有限队列 high-water mark 不超过配置；
- network/local-delivery/adapter ingress 的 occupancy 与 pending delivery business event 都被结果记录；
- 恢复后无 drop/duplicate 并 drain；
- 至少一个预期 queue 达到 full，避免“测试没打到回压”。

#### I10 `response_progress`

持续 AW/W/AR request flood，同时产生 B/R：

- request flood 固定持续 2,000 network cycles；在 consumer stall 已释放、target latency 有限且存在 `responseEligible` 的任意连续 256 cycles 内，至少完成一个 B 或 R beat；
- master 不得等待 B 才发送同一 transaction 剩余 W；
- cycle 2,000 停止新请求，cycle 20,000 前完全 drain。

该 liveness oracle 仅在 XY routing、无永久 fault、consumer stall 最终释放、target latency 有界、Router/NI round-robin 弱公平时启用。I10 scenario 必须把 `max_target_latency + max_forced_stall + worst_packet_serialization_and_path_slack` 的保守上界证明为小于 256 cycles，并写入 resolved config；否则该 case 在启动前失败而不是放宽 oracle。当前没有 ready/eligible work 时不要求 B/R 计数增长。

#### I11 `randomized_scoreboard`

quick：固定 seeds `{1, 7, 42}`，每 seed 至少 500 transactions。
full：固定 seeds `{1, 7, 42, 20260831, 314159, 271828, 65537, 99, 1234, 9001}`，每 seed 2,000 transactions；另提供 10,000 transaction nightly case。

负载包含：

- 至少 4 initiators + 4 targets；
- mixed read/write；
- multiple IDs、bursts、narrow/full width；
- random deterministic channel skew、target latency、B/R consumer stall；
- 小 buffer 制造 credit backpressure；
- 不同 ordering domain 使用不重叠地址；same-address alias 仅由专门 case 预先定义 linearization；
- mandatory randomized case 启用 `functionalData` 并做 byte-level shadow memory；traffic-only digest 另列性能 smoke，不计作 functional pass。

每个 seed 都必须满足所有守恒、ordering 和 drain 条件。失败保存完整 replay trace。

#### I12 `determinism`

相同 config + seed 运行两次，normalized event trace SHA256 完全相同；再用保存的 workload trace replay，transaction/beat/stat digest 一致。

#### I13 `invalid_configuration`

每个非法配置单独启动，要求非零退出和固定诊断；segfault、越界、模糊 assert 失败都不算通过。
I13 是 N1/N2/N7/N12 等 invalid-config process 的 umbrella requirement，不额外占一个 manifest case；第 12.3 节列出的 `n*` case 是唯一计数来源。

#### I14 `legacy_garnet_regression`

在修改前记录真实可运行的 Garnet standalone build/smoke 命令；修改后执行完全相同的命令。必须显式传 `--network=garnet`；仓库现有 `tests/gem5/memory/test.py` 中名为 `garnet_synth_traffic` 的 case 没有显式启用 Garnet，不能单独作为回归证据。
I14 由 manifest 中 `legacy_vnet0/1/2` 三个 process 实现，不另增同名 case。

未使用新 vector/dynamic hook 的 legacy message 必须保持旧分类、4/1 depth 与固定 MessageSizeType 行为。

Gate 0 把三条 single-path command、base SHA、relevant config 和以下既有 stats 写入 versioned golden：injected/received packets/flits、average packet/flit latency、average hops、每 vnet counts。修改后 integer/count 项必须精确相等；浮点项只允许文本打印舍入造成的 `abs <= 1e-12`，不能设置宽松百分比。若 Gate 0 另有可用的 grant trace，则 hash 也必须相等；不得在修改后才发明一个基线无法生成的 oracle。

#### I15 `chi_build_smoke`

mandatory compile regression 固定为 `scons build/RISCV_CHI/gem5.opt -j4`。若 Gate 0 在基线成功运行并记录了不依赖外部镜像的 CHI runtime smoke，修改后必须运行同一命令；否则 runtime smoke 记录为 conditional-not-applicable，而 build 仍 mandatory。不得为了 AXI 修改 CHI 源码。
I15 是 build/conditional-runtime gate，不计入第 12.3 节的 gem5 process case 数。

### 13.3 负向测试

| Case | 刺激 | 预期 |
|---|---|---|
| N1 | scenario 静态 beatCount=0、静态 beatCount=257、runtime beatCount=257 | 三个独立进程；静态输入 instantiate 前拒绝，runtime 在该 handshake edge fatal，network injected 均不增加 |
| N2 | AxSIZE 大于 data bus | strict fatal |
| N3a | unaligned INCR | 通过 default error target 完整 drain 后 DECERR |
| N3b | 跨 4 KiB | strict fatal |
| N4 | early/late/missing WLAST | 带 txnUid/beatIndex 的 fatal |
| N5 | duplicate R beat、out-of-range R beat | 两个独立进程，reassembler fatal |
| N6 | duplicate txnUid / unknown response | fatal |
| N7 | `strict_protocol=false` | instantiate 前稳定 fatal |
| N8 | unmapped address | 一笔 architected DECERR response |
| N9 | 通过现有 LOCK 字段表达 exclusive | architected DECERR；ACE/ATOP 接口不可表达且不设伪 runtime case |
| N10 | source 接受一段 W/部分 W burst 后 workload 结束，永远无 AW | `finishAndCheckQuiescence()` 立即给出 final-consistency failure，列出 `writeOrdinal`、buffered beats、是否已见 WLAST；它尚无 destination/txnUid，不得伪造 UID，也不得等到 host/watchdog timeout |
| N11 | FIFO/ROB/orphan full | backpressure；protocol_errors 仍为 0 |
| N12 | vector length 错或含 0 | initialize fatal |

每个预期 fatal case 必须独立进程执行，非零退出且匹配稳定 error marker；crash、segfault 或无上下文 assert 不算通过。

### 13.4 仿真与 wall-clock 预算

AXI_MESH 测试 scenario 中的 duration/watchdog 一律用 network cycles 表达；runner 根据实际 network clock period 转为 `--axi-max-sim-ticks`，并在 result 同时记录 cycles/ticks，禁止把 gem5 tick 当 cycle。三个 legacy case 是唯一例外：runner 必须逐字符复用 stock 命令的 `--sim-cycles=5000000`，把它当不透明的基线参数，不改写为 AXI tick/network-cycle limit，也不参与 AXI result schema。

| Case class | max network cycles | no-progress rule | host timeout |
|---|---:|---|---:|
| C++ unit binaries（10 个，覆盖 U1–U12） | N/A | N/A | 60 s/binary |
| Python unit collection | N/A | N/A | 60 s/collection |
| I0–I4 smoke/functional | 200,000 | 仅全局 max；forced stall 不算 deadlock | 120 s/case |
| I5–I6 ordering | 300,000 | eligible work 20,000 cycles 无进展失败 | 120 s/case |
| I7–I9 buffer/backpressure | 500,000 | `max_forced_stall + 50,000` | 180 s/case |
| I10 response progress | 20,000 | 第 13.2 节 256-cycle oracle | 120 s |
| determinism_a/b/replay | 300,000 | 20,000 eligible cycles | 120 s/process |
| legacy_vnet0/1/2 | N/A；原样使用 stock `--sim-cycles=5000000` | stock exit contract | 120 s/process |
| quick random | 2,000,000 | 100,000 eligible cycles | 180 s/seed |
| full 2,000-txn random/matrix | 5,000,000 | 250,000 eligible cycles | 300 s/case |
| 10,000-txn nightly | 20,000,000 | 500,000 eligible cycles | 1,200 s |
| expected fatal | 10,000 | N/A | 30 s |
| architected error/final-consistency negative | 100,000 | final consistency 不等待 progress timeout | 60 s |
| N11 bounded-full pass cases | 500,000 | `max_forced_stall + 50,000` | 180 s/case |

高拥塞 case 不设置单 transaction 固定延迟上限，只使用有 liveness 前提的全局/eligible-work watchdog。任何 host timeout 124 都是失败，不能当预期 deadlock 通过。

---

## 14. 每个测试的统一通过条件

纯 C++/Python unit test 以机器可读 GTest XML/pytest 结果验收，不要求构造 Router、result JSON 或 network quiescence；zero test、disabled、skip、xfail 都失败。

正常 AXI 集成测试必须满足；legacy case 只按 I14 独立 schema 验收：

```text
exit code == 0
completed before max_sim_ticks
issued == accepted == completed
protocol_errors == 0
duplicate/missing beat == 0
packet injected == packet ejected, per vnet and total
flit injected == flit ejected, per vnet and total
for every directed link × VC:
    initial - sent + returned == current
    0 <= current <= configured depth
    at quiescence current == initial
credit_mismatches_by_link_vc == 0
outstanding_at_exit == 0
orphan_w_at_exit == 0
ROB/reassembly/context/service/obligation queues at exit == 0
all network and local-delivery MessageBuffers at exit == 0
all adapter ingress/held-retry/business events at exit == 0
Garnet quiescence snapshot empty for two consecutive network cycles
```

启用 `functionalData` 的 functional case 还必须满足 checker shadow memory/data 与 target memory 完全一致；traffic-only case 不得报告该断言。architected DECERR/SLVERR case 仍计入 `completed`，要求 `transactions_error` 等于 manifest 的精确期望值、`protocol_errors==0` 并完全 drain；strict fatal case 则必须独立进程以退出码 1 结束且 injected counter 不越过 manifest 允许值。N10 final-consistency case 以退出码 2 和精确 marker 结束，network/credit 已排空但 endpoint residual 故意非零，只按第 12.2 节的 negative schema 验收，不能套用本节正常完成条件。

Backpressure 采用每 case manifest 的 `expected_stall_mask`，不统一强求所有 stall：

```text
U6: local_fifo_full > 0
I7: router_credit_stall > 0 and targeted_router_vc_max == configured depth
I8: orphan_or_quota_stall > 0
I9: message_buffer_or_ni_stall > 0 and targeted_queue_max == configured depth
all: no capacity violation and untargeted queue/VC max <= configured depth
```

只有明确以填满某个 queue/VC 为目标的 case 才要求 `max == depth`。Different-ID reorder 测试还必须观察到实际完成次序反转；否则即使数据正确也失败。Progress watchdog 只在 I10 所列 liveness 前提成立时作为 pass/fail oracle。

---

## 15. 构建、测试与静态检查命令

### 15.1 构建

```bash
scons build/AXI_MESH/gem5.debug -j4
scons build/AXI_MESH/gem5.opt -j4
scons build/Garnet_standalone/gem5.opt -j4
scons build/RISCV_CHI/gem5.opt -j4
```

### 15.2 单元测试

仓库使用 `GTest(...)` 注册独立 test binary；`build/AXI_MESH/unittests.opt` 是 SCons aggregate/XML target，不是可执行文件。命令为：

```bash
scons build/AXI_MESH/unittests.opt -j4

python3 tests/gem5/axi_garnet/run_axi_unit_tests.py \
  --build-dir build/AXI_MESH --variant opt \
  --manifest tests/gem5/axi_garnet/manifest.json

timeout 60s python3 -m pytest -q --runxfail \
  --junitxml=m5out/axi-pyunit.xml tests/pyunit/ai_mesh
```

Gate 0 必须先用一个现有 GTest 实测独立 binary 的确切路径规则并写入 implementation report；后续 manifest 只列实测路径。不得尝试执行 `unittests.opt`，也不可假造测试已执行。
`conftest.py` 必须在 session finish 时把任意 skipped test 转为非零退出；`--runxfail` 让 xfail 不被当作成功。Python unit 的 JUnit XML 与 collected node-id set 由 runner/最终报告检查：`collected=run=passed=21`，`failed=skipped=xfail=xpass=deselected=timed_out=0`，且 discovered set 与第 13.1 节完整 node ID exact-match。GTest 聚合则必须满足第 12.2 节的 10 binary / 66 test 精确等式。

### 15.3 集成 suite

```bash
python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.debug --suite quick

python3 tests/gem5/axi_garnet/run_axi_garnet_tests.py \
  --gem5 build/AXI_MESH/gem5.opt --suite full
```

### 15.4 Legacy Garnet 显式 smoke

以下命令需在基线先执行并记录，再在修改后原样执行：

```bash
set -euo pipefail
for vnet in 0 1 2; do
  timeout 120s build/Garnet_standalone/gem5.opt \
    -d "m5out/garnet-legacy-vnet${vnet}" \
    configs/example/garnet_synth_traffic.py \
    --network=garnet \
    --topology=Mesh_XY \
    --routing-algorithm=1 \
    --num-cpus=4 \
    --num-dirs=4 \
    --mesh-rows=2 \
    --single-sender-id=0 \
    --single-dest-id=3 \
    --inj-vnet="${vnet}" \
    --num-packets-max=100 \
    --sim-cycles=5000000 \
    --injectionrate=0.1
done
```

该 fork 没有 `--random-seed` 参数，不得添加。先在基线执行 `--help` 和以上三条 deterministic single-path case，记录标准化 stats/trace；修改后逐字符使用同一参数。不能删除 `--network=garnet`。

### 15.5 禁止 AXI 泄漏进 Router

```bash
if rg -n '#include .*(axi|Axi|AXI)|dynamic_cast<.*Axi|AxiChannel|AXIChannel' \
  src/mem/ruby/network/garnet; then
  exit 1
fi
```

验收要求：无 protocol type/include/channel switch 输出。通用 `getWireSizeBytes` 名称允许出现，但不能伴随 AXI include 或 cast。

### 15.6 仓库卫生

```bash
git diff --check
git status --short
git diff --stat 7478835ac25d406941490578874d5022e3a46ec5...HEAD
```

---

## 16. 分阶段实现与 commit gates

上一 gate 未通过，不得继续下一 gate。

### Gate 0：只读基线与技术探针

- 在 `xs-dev@7478835a...` 记录 15.1 中 Garnet_standalone/RISCV_CHI build 结果和 15.4 三条 legacy golden；
- 运行现有 GTest target，并从现有 protocol build 确认本 fork 独立 test binary、aggregate XML 和 generated protocol header 的真实路径规则；
- 记录是否存在不依赖外部镜像且可运行的 CHI runtime smoke；没有则只把 CHI build 设为 mandatory；
- 只读检查现有 MessageBuffer/Consumer/SLICC wakeup pattern，记录用于 Commit 1 probe 的具体 API；实际 AXI queue ownership probe 随 Commit 1 scaffold 实现；
- Gate：基线本身失败、queue ownership 无法实现或需要修改 SLICC generator 时，按第 17 节停止。

### Commit 1：`axi-mesh: add isolated AXI_MESH build scaffold`

- build_opts、Kconfig/SConsopts、最小 directional SLICC queues/local-delivery、Python factory 和 `AxiMeshDie` topology；
- 只要求 queue registration、single-Consumer ownership、1-cycle local delivery 和 1 initiator/1 target instantiate probe；尚不要求完整五通道 adapter traffic；
- Gate：AXI_MESH debug、Garnet_standalone、RISCV_CHI build 均成功，protocol header guard 静态检查通过。

### Commit 2：`garnet: add per-vnet class and VC buffer depth`

- vector 参数、fallback、统一 getter、capacity/credit invariant、非法配置；
- Gate：U9、U11、protocol-neutral raw-message depth/credit probe 和 legacy fallback 通过；不得提前要求尚未实现的 AXI I7/I8。

### Commit 3：`garnet: support protocol-neutral dynamic packet sizing`

- Message hook、NI packetization、legacy fallback；
- Gate：U8、legacy dynamic-size fallback、原 Garnet standalone 三个 deterministic vnet smoke 通过。

### Commit 4：`axi-mesh: add AXI beat messages and endpoint adapters`

- 类型、validation、bounded FIFO、流式 AW/W pairing、quota/context pool、default error target、simple target memory；
- Gate：U1–U4、I0–I4 通过。

### Commit 5：`axi-mesh: add ordering, response ROB, and backpressure`

- serviceReady/targetSeq commit/responseSeq retire、same-ID side effect、ROB、response obligation、quiescence introspection；
- Gate：U5–U7、U10、U12、I5–I10 通过。

### Commit 6：`tests: add AXI Garnet stress, replay, and regressions`

- runner、manifest、JSON verifier、randomized scoreboard、determinism；
- Gate：44-case quick、127-case full、I11–I14 和 RISCV_CHI mandatory build 通过；CHI runtime smoke 仅在 Gate 0 记录了可运行基线时 mandatory。

### Commit 7：`docs: document AXI Garnet configuration and limitations`

- 参数单位、支持矩阵、命令、stats、已知限制；
- Gate：文档中的 mandatory 命令逐条实测，最终报告记录 exit code。

测试与实现应在同一行为 commit 中同步加入，不允许先提交无法验证的大段代码。

---

## 17. 停止条件

遇到下列情况立即停止、保留现场并报告，不得扩大修改范围：

- current SHA 与基线不一致；
- 待修改文件有用户未提交修改；
- 原始基线 build 或 Garnet smoke 已失败；
- AXI_MESH 必须修改 CHI 才能构建；
- SLICC shim 与 C++ adapter 无法建立单 Consumer 的可靠 queue ownership；
- local-delivery wiring 需要修改 `src/mem/slicc/symbols/StateMachine.py` 或引入无限 handoff；
- per-vnet depth 无法同时作用于 input capacity 与对应 credit；
- dynamic wire bytes 只能通过 Garnet include AXI header 实现；
- 测试出现 packet/flit/credit/beat 不守恒；
- stress 超过 max ticks 或 wall timeout；
- 必须改成无限 queue、禁用断言、删除测试或降低 transaction 数才能继续；
- 无法执行 mandatory test（环境、依赖或资源阻塞）。

停点报告必须包含：

```text
current branch and SHA
last successful gate and commit
failing command and exit code
minimal relevant log
replay seed/scenario if applicable
causes already ruled out
2–3 concrete options requiring user decision
```

不得在 blocker 后写“基本完成”。

---

## 18. 最终 Definition of Done

只有全部满足才算第一阶段完成：

1. `AXI_MESH` debug/opt 独立构建成功；
2. 五 vnet 和 `[4,8,4,4,8]` 逐 vnet depth 真实生效；
3. input VC capacity 与 upstream initial/max credit 使用同一个 getter；
4. 所有 AXI/network/local-delivery/ROB/context/assembly/obligation queue 有限，static target quota 不超配且可产生真实 backpressure；
5. W/R 以一个 beat 一个 packet 传输并按动态 wireBytes 计 flit；
6. payload/beat/packet/flit 守恒，credit 按每个 directed link × VC 守恒；
7. WSTRB byte-level functional result 正确；
8. same-ID architectural commit/target side effect 与 source retire 均有序，serviceReady 可乱序；
9. different-ID 实际发生合法乱序；
10. W-before-AW、orphan full、ROB full、response stall 均测试通过；
11. finite load 停止注入后完全 quiescent，所有 credit 恢复；
12. 相同 seed 的 normalized trace hash 一致，可 replay；
13. 所有 U1–U12、I0–I15 与 N1–N12 mandatory tests 无 skip/xfail/timeout；
14. quick debug suite 与 full opt suite 返回 0；
15. 原 Garnet standalone regression 通过；
16. `RISCV_CHI` build 通过；只有 Gate 0 记录了可运行基线 smoke 时，同一 CHI runtime smoke 也必须通过；
17. 整个 Garnet 目录无 AXI include、cast 或 channel 分支；
18. unsupported feature 明确 DECERR/fatal，不静默降级；
19. `git diff --check` 通过，无 build/m5out/trace 被提交；
20. 最终报告给出 commit SHA、diff stat、逐命令 exit code 和结果目录。

---

## 19. 最终交付报告模板

```markdown
# AXI Garnet MVP Implementation Report

## Repository state
- Base: xs-dev@7478835a...
- Branch: ...
- Commits: ...
- Diff stat: ...

## Implemented scope
- ...

## Known unsupported features
- ...

## Verification
| Command | Exit | Cases | Pass | Fail | Skip | Timeout | Result dir |
|---|---:|---:|---:|---:|---:|---:|---|
| ... | ... | ... | ... | ... | ... | ... | ... |

## Conservation and drain summary
- issued/accepted/completed: ...
- packets/flits per vnet: ...
- credit ledger link×VC mismatch/restored: ...
- outstanding/ROB/orphan/obligation/local-delivery/VC at exit: ...
- quiescent stable cycles: ...
- workload/event trace SHA256: ...

## Deviations from this Spec
- None / exact item, reason, impact, user decision

## Remaining blockers
- None / ...
```

任何 mandatory test 没有实际执行时，报告结论必须是“未完成”或“部分实现”。
